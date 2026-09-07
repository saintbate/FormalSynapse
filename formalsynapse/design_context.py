"""Phase A: extract the names a generator is allowed to use from DUT RTL.

Regex over ANSI ports / declarations — not Yosys. The prompt gets this table so the model
does not invent signals. Formal regions are stripped first.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from formalsynapse.sva_inject import strip_formal_blocks
from formalsynapse.sva_lower import strip_comments

_MODULE = re.compile(r"(?<![\w$])module\s+([A-Za-z_]\w*)")
_ENDMODULE = re.compile(r"(?<![\w$])endmodule\b")
_PORT = re.compile(
    r"\b(?P<dir>input|output|inout)\s+"
    r"(?:(?P<kind>wire|reg|logic|bit)\s+)?"
    r"(?:(?:un)?signed\s+)?"
    r"(?:\[(?P<width>[^\]]+)\]\s*)?"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?P<more>(?:\s*,\s*[A-Za-z_]\w*(?!\s*[\[\w]))*)",
    re.I,
)
_DECL = re.compile(
    r"\b(?P<kind>logic|reg|wire|bit)\s+"
    r"(?:(?:un)?signed\s+)?"
    r"(?:\[(?P<width>[^\]]+)\]\s*)?"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*\[[^\]]+\])?"
    r"(?P<more>(?:\s*,\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?)*)",
    re.I,
)
_LOCALPARAM = re.compile(
    r"\blocalparam\s+"
    r"(?:[A-Za-z_][\w:]*\s+(?:\[[^\]]+\]\s+)?)?"
    r"(?P<name>[A-Za-z_]\w*)",
    re.I,
)
_KEYWORDS = frozenset(
    {"input", "output", "inout", "wire", "reg", "logic", "bit", "signed", "unsigned", "parameter"}
)
# clk, clock, i_clk, clk_i, sys_clk, pclk, hclk, aclk, clk_in ...
_CLOCK_NAME = re.compile(r"(?i)^(?:\w*_)?[a-z]{0,2}cl(?:k|ock)(?:_?i|_?in)?$")
# rst, rst_n, reset, reset_n, resetn, rstn, arst_n, aresetn, nrst, sys_rst, rst_ni, i_rst_n ...
_RESET_NAME = re.compile(r"(?i)^(?:\w*_)?n?[as]?(?:rst|reset)n?(?:_n|_ni|_n_i|_i|_b)?$")
_RESET_ACTIVE_LOW_NAME = re.compile(
    r"(?i)^(?:\w*_)?(?:n[as]?(?:rst|reset)|[as]?(?:rst|reset)(?:n|_n|_ni|_n_i|_b))$"
)
_EDGE = re.compile(r"\b(posedge|negedge)\s+([A-Za-z_]\w*)", re.I)


@dataclass(frozen=True)
class Signal:
    name: str
    direction: str
    width: str


@dataclass(frozen=True)
class DesignContext:
    top: str
    ports: tuple[Signal, ...]
    internals: tuple[Signal, ...]
    constants: tuple[str, ...]
    clock: str | None
    reset: str | None
    reset_active_low_hint: bool | None = None

    @property
    def reset_active_low(self) -> bool:
        if self.reset is None:
            return False
        if self.reset_active_low_hint is not None:
            return self.reset_active_low_hint
        return _RESET_ACTIVE_LOW_NAME.match(self.reset) is not None

    @property
    def reset_active(self) -> str | None:
        """Expression that is true while the DUT is in reset (``!rst_n`` or ``rst``)."""
        if self.reset is None:
            return None
        return f"!{self.reset}" if self.reset_active_low else self.reset

    def disable_iff_clause(self) -> str:
        """SVA ``disable iff (...)`` matching this DUT's reset polarity ("" when it has none)."""
        active = self.reset_active
        return f"disable iff ({active})" if active else ""

    def render(self) -> str:
        lines = [f"module {self.top}"]
        if self.clock or self.reset:
            clk = self.clock or "?"
            if self.reset:
                polarity = "active-low" if self.reset_active_low else "active-high"
                lines.append(f"clock {clk}  reset {self.reset} ({polarity})")
            else:
                lines.append(f"clock {clk}  reset: none (no reset port; do not write disable iff)")
        if self.ports:
            lines.append("ports:")
            for sig in self.ports:
                lines.append(f"  {sig.direction} {sig.name} {sig.width}")
        if self.internals:
            lines.append("internals (declared in the module; you may name these):")
            for sig in self.internals:
                lines.append(f"  {sig.name} {sig.width}")
        if self.constants:
            lines.append("constants: " + ", ".join(self.constants))
        lines.append("Do not invent signal names that are not listed above.")
        return "\n".join(lines)


def dual_edge_clocks(*texts: str) -> tuple[str, ...]:
    """Nets clocked on both ``posedge`` and ``negedge``. Yosys BMC cannot handle those."""
    pos: set[str] = set()
    neg: set[str] = set()
    for text in texts:
        for kind, name in _EDGE.findall(strip_comments(text)):
            if kind.lower() == "posedge":
                pos.add(name)
            else:
                neg.add(name)
    return tuple(sorted(pos & neg))


def dual_edge_clock_reason(*texts: str) -> str | None:
    names = dual_edge_clocks(*texts)
    if not names:
        return None
    listed = ", ".join(names)
    return f"Yosys BMC cannot clock {listed} (posedge and negedge)"


def dual_edge_clock_reason_from_files(*paths: Path) -> str | None:
    texts: list[str] = []
    for path in paths:
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    return dual_edge_clock_reason(*texts)


def extract_context(rtl: str, top: str) -> DesignContext:
    """Parse ANSI ports and body declarations for ``top``."""
    clean = strip_comments(strip_formal_blocks(rtl))
    header, body = _module_parts(clean, top)
    ports = tuple(_unique_signals(_PORT.finditer(header)))
    if not ports and header.strip("() \n\t"):
        # Non-ANSI style: ``module m (clk, rst, q); input clk, rst; output q; ...``
        ports = tuple(_unique_signals(_PORT.finditer(body)))
    port_names = {p.name for p in ports}
    internals: list[Signal] = []
    for m in _DECL.finditer(body):
        for name in _names(m):
            if name in port_names or name.startswith("f_fsyn_"):
                continue
            internals.append(Signal(name, "internal", _width(m.group("width"))))
    internals_u = tuple(_dedupe(internals))
    constants = tuple(dict.fromkeys(m.group("name") for m in _LOCALPARAM.finditer(clean)))
    inputs = [p.name for p in ports if p.direction == "input"]
    edges = _edges(body)
    clock = _pick_clock(inputs, edges)
    reset = _pick_reset(inputs, edges, clock)
    hint = _reset_polarity(reset, edges, body) if reset else None
    return DesignContext(
        top=top,
        ports=ports,
        internals=internals_u,
        constants=constants,
        clock=clock,
        reset=reset,
        reset_active_low_hint=hint,
    )


def _edges(body: str) -> list[tuple[str, str]]:
    return [(kind.lower(), name) for kind, name in _EDGE.findall(body)]


def _pick_clock(inputs: list[str], edges: list[tuple[str, str]]) -> str | None:
    named = next((n for n in inputs if _CLOCK_NAME.match(n)), None)
    if named is not None:
        return named
    # Fall back to the input most often used as a posedge event.
    counts: dict[str, int] = {}
    for kind, name in edges:
        if kind == "posedge" and name in inputs:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda n: (counts[n], -inputs.index(n)))


def _pick_reset(inputs: list[str], edges: list[tuple[str, str]], clock: str | None) -> str | None:
    named = next((n for n in inputs if n != clock and _RESET_NAME.match(n)), None)
    if named is not None:
        return named
    # Async reset: the non-clock input that appears in a sensitivity list edge.
    for _kind, name in edges:
        if name != clock and name in inputs:
            return name
    return None


def _reset_polarity(reset: str, edges: list[tuple[str, str]], body: str) -> bool | None:
    """Active-low? From the async edge, else from ``if (!rst)`` vs ``if (rst)`` usage, else None."""
    kinds = {kind for kind, name in edges if name == reset}
    if kinds == {"negedge"}:
        return True
    if kinds == {"posedge"}:
        return False
    low = len(re.findall(rf"if\s*\(\s*!\s*\(?\s*{re.escape(reset)}\s*\)?\s*\)", body))
    low += len(re.findall(rf"if\s*\(\s*{re.escape(reset)}\s*==\s*1'b0\s*\)", body))
    high = len(re.findall(rf"if\s*\(\s*{re.escape(reset)}\s*\)", body))
    high += len(re.findall(rf"if\s*\(\s*{re.escape(reset)}\s*==\s*1'b1\s*\)", body))
    if low > high:
        return True
    if high > low:
        return False
    return None


def _names(m: re.Match[str]) -> list[str]:
    """All identifiers declared by one ``a, b, c`` match, minus keywords."""
    names = [m.group("name")]
    more = m.group("more") or ""
    for part in more.split(","):
        ident = re.match(r"\s*([A-Za-z_]\w*)", part)
        if ident and ident.group(1).lower() not in _KEYWORDS:
            names.append(ident.group(1))
    return names


def _module_parts(text: str, top: str) -> tuple[str, str]:
    for m in _MODULE.finditer(text):
        if m.group(1) != top:
            continue
        end = _ENDMODULE.search(text, m.end())
        if end is None:
            break
        chunk = text[m.end() : end.start()]
        # Skip a parameter port list: module top #(parameter W = 8) (ports...)
        pm = re.match(r"\s*#\s*\(", chunk)
        if pm is not None:
            pclose = _match_paren(chunk, pm.end() - 1)
            if pclose < 0:
                return chunk, ""
            chunk = chunk[pclose + 1 :]
        paren = chunk.find("(")
        if paren < 0:
            return "", chunk
        close = _match_paren(chunk, paren)
        if close < 0:
            return chunk, ""
        return chunk[paren : close + 1], chunk[close + 1 :]
    return "", ""


def _match_paren(text: str, start: int) -> int:
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _width(raw: str | None) -> str:
    if not raw:
        return "1"
    return f"[{raw.strip()}]"


def _unique_signals(matches: Iterator[re.Match[str]]) -> list[Signal]:
    seen: dict[str, Signal] = {}
    for m in matches:
        for name in _names(m):
            if name in seen:
                continue
            seen[name] = Signal(name, m.group("dir").lower(), _width(m.group("width")))
    return list(seen.values())


def _dedupe(signals: list[Signal]) -> list[Signal]:
    seen: dict[str, Signal] = {}
    for sig in signals:
        if sig.name not in seen:
            seen[sig.name] = sig
    return list(seen.values())
