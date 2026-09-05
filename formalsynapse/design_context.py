"""Phase A: extract the names a generator is allowed to use from DUT RTL.

Regex over ANSI ports / declarations — not Yosys. The prompt gets this table so the model
does not invent signals. Formal regions are stripped first.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from formalsynapse.sva_inject import strip_formal_blocks
from formalsynapse.sva_lower import strip_comments

_MODULE = re.compile(r"(?<![\w$])module\s+([A-Za-z_]\w*)")
_ENDMODULE = re.compile(r"(?<![\w$])endmodule\b")
_PORT = re.compile(
    r"\b(?P<dir>input|output|inout)\s+"
    r"(?:(?P<kind>wire|reg|logic|bit)\s+)?"
    r"(?:signed\s+)?"
    r"(?:\[(?P<width>[^\]]+)\]\s+)?"
    r"(?P<name>[A-Za-z_]\w*)",
    re.I,
)
_DECL = re.compile(
    r"\b(?P<kind>logic|reg|wire|bit)\s+"
    r"(?:signed\s+)?"
    r"(?:\[(?P<width>[^\]]+)\]\s+)?"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?:\s*\[[^\]]+\])?",
    re.I,
)
_LOCALPARAM = re.compile(
    r"\blocalparam\s+"
    r"(?:[A-Za-z_][\w:]*\s+(?:\[[^\]]+\]\s+)?)?"
    r"(?P<name>[A-Za-z_]\w*)",
    re.I,
)
_CLOCK_NAMES = frozenset({"clk", "clock"})
_RESET_NAMES = frozenset({"rst_n", "rst", "reset_n", "reset", "resetn"})


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

    def render(self) -> str:
        lines = [f"module {self.top}"]
        if self.clock or self.reset:
            clk = self.clock or "?"
            rst = self.reset or "?"
            lines.append(f"clock {clk}  reset {rst}")
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


def extract_context(rtl: str, top: str) -> DesignContext:
    """Parse ANSI ports and body declarations for ``top``."""
    clean = strip_comments(strip_formal_blocks(rtl))
    header, body = _module_parts(clean, top)
    ports = tuple(_unique_signals(_PORT.finditer(header)))
    port_names = {p.name for p in ports}
    internals: list[Signal] = []
    for m in _DECL.finditer(body):
        name = m.group("name")
        if name in port_names or name.startswith("f_fsyn_"):
            continue
        internals.append(Signal(name, "internal", _width(m.group("width"))))
    internals_u = tuple(_dedupe(internals))
    constants = tuple(dict.fromkeys(m.group("name") for m in _LOCALPARAM.finditer(clean)))
    inputs = [p.name for p in ports if p.direction == "input"]
    clock = next((n for n in inputs if n.lower() in _CLOCK_NAMES), None)
    reset = next((n for n in inputs if n.lower() in _RESET_NAMES), None)
    return DesignContext(
        top=top,
        ports=ports,
        internals=internals_u,
        constants=constants,
        clock=clock,
        reset=reset,
    )


def _module_parts(text: str, top: str) -> tuple[str, str]:
    for m in _MODULE.finditer(text):
        if m.group(1) != top:
            continue
        end = _ENDMODULE.search(text, m.end())
        if end is None:
            break
        chunk = text[m.end() : end.start()]
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
        name = m.group("name")
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
