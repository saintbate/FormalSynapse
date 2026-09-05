"""Cheap RTL mutation operators for the formal sign-off gate.

Mutants are applied to the DUT *without* the ``FORMAL`` block so we do not
edit ``benchmarks/golden/**``. A mutant is *killed* when the candidate SVA
fails on it (``sby`` FAIL) and *survives* when the SVA still passes.

Operators follow the conservative AssertLLM2 subset that maps onto Yosys
combinational/sequential RTL: ``&&``/``||``, ``==``/``!=``, relational
pairs, binary ``+``/``-``, and simple ``if (ident)`` polarity flips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from formalsynapse.sva_inject import strip_formal_blocks

# Longest token first so ``<=`` is not split into ``<`` + ``=``.
_OPS: tuple[tuple[str, str, str], ...] = (
    ("le_ge", "<=", ">="),
    ("ge_le", ">=", "<="),
    ("eq_ne", "==", "!="),
    ("ne_eq", "!=", "=="),
    ("and_or", "&&", "||"),
    ("or_and", "||", "&&"),
    ("lt_gt", "<", ">"),
    ("gt_lt", ">", "<"),
    ("add_sub", "+", "-"),
    ("sub_add", "-", "+"),
)

_IF_IDENT = re.compile(r"\bif\s*\(\s*(!?)\s*([A-Za-z_]\w*)\s*\)")
_SKIP_LINE = re.compile(r"\b(rst_n|reset|localparam|parameter)\b", re.IGNORECASE)
_PROTECTED_UNARY = frozenset({"rst_n", "reset", "clk", "clock"})
_NBA_LVALUE = re.compile(r"^\s*(?:begin\s+)?(?:[\w\.]+\s*:\s*)?[\w\.\[\]]+\s*$")
_RELATIONAL = frozenset({"le_ge", "ge_le"})


@dataclass(frozen=True)
class Mutant:
    """One single-site RTL mutant."""

    name: str
    operator: str
    description: str
    rtl: str


def _blank_comments(text: str) -> str:
    """Replace comment characters with spaces, preserving offsets."""

    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    return re.sub(r"//[^\n]*", blank, text)


def _line_span(text: str, index: int) -> tuple[str, int]:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end < 0:
        end = len(text)
    return text[start:end], index - start


def _prev_nons(text: str, index: int) -> str:
    i = index - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    return text[i] if i >= 0 else ""


def _looks_like_nba(line: str, col: int) -> bool:
    return bool(_NBA_LVALUE.match(line[:col]))


def _bracket_depth(text: str, index: int) -> int:
    depth = 0
    for ch in text[:index]:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
    return depth


def _replace_once(text: str, start: int, end: int, repl: str) -> str:
    return text[:start] + repl + text[end:]


def generate_mutants(dut_text: str, *, max_mutants: int = 8) -> tuple[Mutant, ...]:
    """Return up to ``max_mutants`` single-site mutants of ``dut_text``.

    The formal block is stripped first. Sites on reset / localparam lines
    are skipped so we do not produce modules that never leave reset.
    Non-blocking assignments (``count <= ...``) are not treated as ``<=``.
    """
    body = strip_formal_blocks(dut_text)
    scan = _blank_comments(body)
    found: list[Mutant] = []
    seen_rtl: set[str] = set()

    for op_name, src, dst in _OPS:
        start = 0
        while True:
            idx = scan.find(src, start)
            if idx < 0:
                break
            nxt = idx + len(src)
            line, col = _line_span(scan, idx)
            start = nxt
            if _SKIP_LINE.search(line):
                continue
            if op_name in _RELATIONAL and _looks_like_nba(line, col):
                continue
            if src in {"<", ">"}:
                nxt_ch = scan[nxt] if nxt < len(scan) else ""
                prev = _prev_nons(scan, idx)
                if nxt_ch in {"=", src} or prev == src:
                    continue
            if src in {"+", "-"}:
                if _bracket_depth(scan, idx) > 0:
                    continue
                prev = _prev_nons(scan, idx)
                if not (prev.isalnum() or prev in {"_", "]"}):
                    continue
            mutant_rtl = _replace_once(body, idx, nxt, dst)
            if mutant_rtl in seen_rtl or mutant_rtl == body:
                continue
            seen_rtl.add(mutant_rtl)
            found.append(
                Mutant(
                    name=f"{op_name}_{len(found)}",
                    operator=op_name,
                    description=f"{src!r} -> {dst!r} in `{line.strip()}`",
                    rtl=mutant_rtl,
                )
            )

    for match in _IF_IDENT.finditer(scan):
        bang, ident = match.group(1), match.group(2)
        if ident.lower() in _PROTECTED_UNARY:
            continue
        line, _col = _line_span(scan, match.start())
        if _SKIP_LINE.search(line):
            continue
        flipped = "" if bang else "!"
        repl = f"if ({flipped}{ident})"
        mutant_rtl = _replace_once(body, match.start(), match.end(), repl)
        if mutant_rtl in seen_rtl or mutant_rtl == body:
            continue
        seen_rtl.add(mutant_rtl)
        found.append(
            Mutant(
                name=f"if_not_{len(found)}",
                operator="if_not",
                description=f"if-polarity flip of {ident} in `{line.strip()}`",
                rtl=mutant_rtl,
            )
        )

    ordered: list[Mutant] = []
    used_ops: set[str] = set()
    for mutant in found:
        if mutant.operator not in used_ops:
            ordered.append(mutant)
            used_ops.add(mutant.operator)
    for mutant in found:
        if mutant not in ordered:
            ordered.append(mutant)
    return tuple(ordered[: max(0, max_mutants)])
