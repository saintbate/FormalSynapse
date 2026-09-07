"""Cheap RTL mutation operators for the formal sign-off gate.

Mutants are applied to the DUT *without* the ``FORMAL`` block so we do not
edit ``benchmarks/golden/**``. A mutant is *killed* when the candidate SVA
fails on it (``sby`` FAIL) and *survives* when the SVA still passes.

Operators follow the conservative AssertLLM2 subset that maps onto Yosys
combinational/sequential RTL: ``&&``/``||``, ``==``/``!=``, relational
pairs, binary ``+``/``-``, and simple ``if (ident)`` polarity flips.
"""

from __future__ import annotations

import difflib
import random
import re
import zlib
from dataclasses import dataclass

from formalsynapse.sva_inject import strip_formal_blocks
from formalsynapse.sva_lower import blank_comments

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

# ``if (en)``, ``if (!en)``, ``if (req[0])``, ``if (!next_grant[1])``
_IF_IDENT = re.compile(r"\bif\s*\(\s*(!?)\s*([A-Za-z_]\w*(?:\s*\[[^\]\n]+\])?)\s*\)")
_SKIP_LINE = re.compile(r"\b(localparam|parameter|posedge|negedge|always)\b", re.IGNORECASE)
_DEFAULT_PROTECTED = frozenset({"rst_n", "rst", "reset", "reset_n", "resetn", "clk", "clock"})
# Text between the last statement boundary and ``<=`` that makes it a non-blocking assignment:
# an optional ``begin``/case-label, then a bare lvalue (``q``, ``mem[i]``, ``s.f``).
_NBA_LVALUE = re.compile(r"^\s*(?:begin\s+)?(?:[\w\.']+\s*:\s*)?[\w\.\[\]]+\s*$")
_STMT_BOUNDARY = re.compile(r"[;)]|\b(?:begin|end|else)\b")
_RELATIONAL = frozenset({"le_ge", "ge_le"})


@dataclass(frozen=True)
class Mutant:
    """One single-site RTL mutant."""

    name: str
    operator: str
    description: str
    rtl: str


@dataclass(frozen=True)
class MutantHunk:
    """Compact golden-vs-mutant snippet for a kill-miss prompt."""

    name: str
    description: str
    diff: str


def rtl_hunk(
    golden: str,
    mutant: str,
    *,
    context: int = 1,
    max_lines: int = 12,
) -> str:
    """Unified diff of the first changed site. ``-`` is golden, ``+`` is the bug."""
    if golden == mutant:
        return ""
    lines = list(
        difflib.unified_diff(
            golden.splitlines(),
            mutant.splitlines(),
            fromfile="golden",
            tofile="mutant",
            lineterm="",
            n=context,
        )
    )
    body = [ln for ln in lines if not ln.startswith(("---", "+++"))]
    if len(body) > 40:
        return ""
    return "\n".join(body[: max(0, max_lines)])


def _blank_comments(text: str) -> str:
    """Replace comment and string-literal characters with spaces, preserving offsets."""
    return blank_comments(text, strings=True)


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
    """``<=`` is a non-blocking assignment when only a bare lvalue precedes it in its statement.

    Handles ``if (en) q <= q + 1;`` and ``else q <= 0;`` (statement starts after ``)``/``else``),
    while ``if (a <= b)`` and ``assign x = a <= b;`` stay relational.
    """
    before = line[:col]
    last = None
    for m in _STMT_BOUNDARY.finditer(before):
        last = m
    stmt = before[last.end() :] if last is not None else before
    return bool(_NBA_LVALUE.match(stmt))


def _protected_re(names: frozenset[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})\b", re.IGNORECASE)


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


def generate_mutants(
    dut_text: str,
    *,
    max_mutants: int = 8,
    protected: frozenset[str] = frozenset(),
) -> tuple[Mutant, ...]:
    """Return up to ``max_mutants`` single-site mutants of ``dut_text``.

    The formal block is stripped first. Sites on lines that mention a ``protected`` name (the
    DUT's clock and reset from :mod:`design_context`, plus the common default names), a
    parameter, or a sensitivity list are skipped so we do not produce modules that never leave
    reset. Non-blocking assignments (``count <= ...``) are not treated as ``<=``.

    Site selection is deterministic per DUT text (seeded shuffle): one mutant per operator
    first, then the remaining sites spread across the file rather than clustered at the top.
    """
    body = strip_formal_blocks(dut_text)
    scan = _blank_comments(body)
    found: list[Mutant] = []
    seen_rtl: set[str] = set()
    guarded = _protected_re(protected | _DEFAULT_PROTECTED)

    for op_name, src, dst in _OPS:
        start = 0
        while True:
            idx = scan.find(src, start)
            if idx < 0:
                break
            nxt = idx + len(src)
            line, col = _line_span(scan, idx)
            start = nxt
            if _SKIP_LINE.search(line) or guarded.search(line):
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
        if guarded.fullmatch(ident):
            continue
        line, _col = _line_span(scan, match.start())
        if _SKIP_LINE.search(line) or guarded.search(line):
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

    rng = random.Random(zlib.crc32(body.encode("utf-8")))
    shuffled = list(found)
    rng.shuffle(shuffled)
    ordered: list[Mutant] = []
    used_ops: set[str] = set()
    for mutant in shuffled:
        if mutant.operator not in used_ops:
            ordered.append(mutant)
            used_ops.add(mutant.operator)
    for mutant in shuffled:
        if mutant not in ordered:
            ordered.append(mutant)
    chosen = ordered[: max(0, max_mutants)]
    # Stable names: numbered by final position, not by discovery order.
    return tuple(
        Mutant(name=f"{m.operator}_{i}", operator=m.operator, description=m.description, rtl=m.rtl)
        for i, m in enumerate(chosen)
    )
