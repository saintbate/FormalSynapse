"""Stdlib-only Value Change Dump reader and counterexample report generator.

``yosys-smtbmc`` writes traces with one ``#<time>`` block per solver step, a 32-bit ``smt_step``
integer that increments at every step and the DUT signals under ``$scope module <top>``. Internal
solver state lives under ``_witness_`` and assertion enables appear as ``<label>_EN``; both are
hidden from reports unless requested.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path


class VcdError(ValueError):
    """Malformed VCD input."""


@dataclass(frozen=True)
class Signal:
    """A declared VCD variable."""

    code: str
    name: str
    scope: tuple[str, ...]
    width: int
    kind: str

    @property
    def full_name(self) -> str:
        return ".".join((*self.scope, self.name))


@dataclass
class Vcd:
    """Parsed VCD: signal table plus the value of every signal at every timestamp."""

    signals: dict[str, Signal] = field(default_factory=dict)
    times: list[int] = field(default_factory=list)
    values: list[dict[str, str]] = field(default_factory=list)  # per time: code -> raw value

    def by_name(self, full_name: str) -> Signal | None:
        for s in self.signals.values():
            if s.full_name == full_name or s.name == full_name:
                return s
        return None

    def value_at(self, code: str, index: int) -> str:
        return self.values[index].get(code, "x")


_VAR = re.compile(r"\$var\s+(\w+)\s+(\d+)\s+(\S+)\s+(.+?)\s+\$end", re.S)
_SCOPE = re.compile(r"\$scope\s+\w+\s+(\S+)\s+\$end", re.S)


def parse_vcd(text: str) -> Vcd:
    """Parse VCD text. Values are stored as bit strings (``0/1/x/z``) or ``r<float>``."""
    vcd = Vcd()
    header, sep, body = text.partition("$enddefinitions")
    if not sep:
        raise VcdError("missing $enddefinitions")
    scope: list[str] = []
    for m in re.finditer(r"\$(scope|upscope|var)\b", header):
        kw = m.group(1)
        if kw == "scope":
            sm = _SCOPE.match(header, m.start())
            if sm is None:
                raise VcdError("malformed $scope")
            scope.append(sm.group(1))
        elif kw == "upscope":
            if scope:
                scope.pop()
        else:
            vm = _VAR.match(header, m.start())
            if vm is None:
                raise VcdError("malformed $var")
            kind, width, code, name = vm.group(1), int(vm.group(2)), vm.group(3), vm.group(4)
            name = re.sub(r"\s*\[\d+(?::\d+)?\]$", "", name.strip())
            vcd.signals[code] = Signal(code, name, tuple(scope), width, kind)

    current: dict[str, str] = {}
    have_time = False
    body = body.split("$end", 1)[1] if body.lstrip().startswith("$end") else body
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("$"):
            continue
        if line.startswith("#"):
            if have_time:
                vcd.values.append(dict(current))
            vcd.times.append(int(line[1:]))
            have_time = True
            continue
        c0 = line[0]
        if c0 in "01xXzZ":
            current[line[1:].strip()] = c0.lower()
        elif c0 in "bB":
            val, _, code = line[1:].partition(" ")
            current[code.strip()] = val.lower()
        elif c0 in "rR":
            val, _, code = line[1:].partition(" ")
            current[code.strip()] = "r" + val
        # events and unknown records are ignored
    if have_time:
        vcd.values.append(dict(current))
    return vcd


def load_vcd(path: Path) -> Vcd:
    return parse_vcd(path.read_text())


# --------------------------------------------------------------------------------------------
# Sampling into solver steps / clock cycles
# --------------------------------------------------------------------------------------------


def step_indices(vcd: Vcd, clock: str | None = None) -> list[int]:
    """Indices into ``vcd.times`` that correspond to successive steps.

    Preference: ``smt_step`` changes (yosys-smtbmc), then rising edges of ``clock``, then every
    timestamp.
    """
    step = vcd.by_name("smt_step")
    if step is not None:
        out: list[int] = []
        last: str | None = None
        for i in range(len(vcd.times)):
            v = vcd.value_at(step.code, i)
            if v != last:
                out.append(i)
                last = v
        if out:
            return out
    if clock is not None:
        clk = vcd.by_name(clock)
        if clk is not None:
            out = []
            prev = "x"
            for i in range(len(vcd.times)):
                v = vcd.value_at(clk.code, i)
                if v == "1" and prev != "1":
                    out.append(i)
                prev = v
            if out:
                return out
    return list(range(len(vcd.times)))


def format_value(raw: str, width: int) -> str:
    """Human-friendly rendering: ``0``/``1`` for bits, hex for clean vectors, else binary."""
    if raw.startswith("r"):
        return raw[1:]
    if width == 1:
        return raw
    bits = raw.rjust(width, "0") if set(raw) <= {"0", "1"} else raw
    if set(bits) <= {"0", "1"}:
        return f"0x{int(bits, 2):0{(width + 3) // 4}x}"
    return "b" + bits


@dataclass(frozen=True)
class SignalTrace:
    name: str
    width: int
    values: tuple[str, ...]  # one formatted value per step


def sample(
    vcd: Vcd,
    *,
    top: str | None = None,
    clock: str | None = None,
    include_internal: bool = False,
    signals: Iterable[str] | None = None,
) -> tuple[list[int], list[SignalTrace]]:
    """Return ``(step_numbers, traces)`` for the DUT-level signals."""
    idx = step_indices(vcd, clock)
    wanted = set(signals) if signals is not None else None
    traces: list[SignalTrace] = []
    for sig in vcd.signals.values():
        if sig.name == "smt_step" or sig.kind == "event":
            continue
        if "_witness_" in sig.scope and not include_internal:
            continue
        if sig.name.endswith("_EN") and not include_internal and wanted is None:
            continue
        if top is not None and sig.scope and sig.scope[0] != top:
            continue
        if wanted is not None and sig.name not in wanted and sig.full_name not in wanted:
            continue
        label = ".".join((*sig.scope[1:], sig.name)) if top is not None else sig.full_name
        vals = tuple(format_value(vcd.value_at(sig.code, i), sig.width) for i in idx)
        traces.append(SignalTrace(label, sig.width, vals))
    traces.sort(key=lambda t: (t.name.count("."), t.name))
    return list(range(len(idx))), traces


# --------------------------------------------------------------------------------------------
# Counterexample report
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CounterexampleReport:
    """LLM/human-readable diagnostic for a failing BMC run."""

    top: str
    failing_step: int | None
    failed_assertions: tuple[str, ...]
    steps: tuple[int, ...]
    traces: tuple[SignalTrace, ...]
    changed_at_failure: tuple[str, ...]

    def render(self, *, window: int = 4) -> str:
        lines: list[str] = []
        if self.failed_assertions:
            lines.append(f"Failed assertion(s): {', '.join(self.failed_assertions)}")
        if self.failing_step is not None:
            lines.append(f"Failure at clock cycle (BMC step) {self.failing_step}")
        else:
            lines.append("Failure step unknown; showing full trace")
        if not self.steps:
            lines.append("(empty trace)")
            return "\n".join(lines)
        last = self.failing_step if self.failing_step is not None else self.steps[-1]
        last = min(last, self.steps[-1])
        first = max(0, last - window)
        cols = list(range(first, last + 1))
        name_w = max(len("signal"), *(len(t.name) for t in self.traces)) if self.traces else 6
        val_w = max(
            [len(f"cyc{c}") for c in cols] + [len(t.values[c]) for t in self.traces for c in cols if c < len(t.values)]
        )
        header = f"{'signal':<{name_w}} | " + " ".join(f"{('cyc' + str(c)):>{val_w}}" for c in cols)
        lines.append(header)
        lines.append("-" * len(header))
        for t in self.traces:
            row = " ".join(f"{(t.values[c] if c < len(t.values) else '-'):>{val_w}}" for c in cols)
            marker = " *" if t.name in self.changed_at_failure else ""
            lines.append(f"{t.name:<{name_w}} | {row}{marker}")
        if self.changed_at_failure:
            lines.append(
                f"Signals that changed entering cycle {last} (marked *): " + ", ".join(self.changed_at_failure)
            )
        lines.append(
            "Interpretation: values in each column are the sampled values at that posedge; "
            f"the assertion was evaluated with the cyc{last} column (consequent) and earlier "
            "columns (antecedent / $past)."
        )
        return "\n".join(lines)


def build_report(
    vcd: Vcd,
    *,
    top: str,
    failing_step: int | None,
    failed_assertions: Sequence[str] = (),
    clock: str = "clk",
    include_internal: bool = False,
) -> CounterexampleReport:
    steps, traces = sample(vcd, top=top, clock=clock, include_internal=include_internal)
    changed: list[str] = []
    if steps:
        last = failing_step if failing_step is not None else steps[-1]
        last = min(last, steps[-1])
        if last >= 1:
            for t in traces:
                if t.values[last] != t.values[last - 1]:
                    changed.append(t.name)
    return CounterexampleReport(
        top=top,
        failing_step=failing_step,
        failed_assertions=tuple(failed_assertions),
        steps=tuple(steps),
        traces=tuple(traces),
        changed_at_failure=tuple(changed),
    )
