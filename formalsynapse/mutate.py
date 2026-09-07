"""Cheap RTL mutation operators for the formal sign-off gate.

Mutants are applied to the DUT *without* the ``FORMAL`` block so we do not
edit ``benchmarks/golden/**``. A mutant is *killed* when the candidate SVA
fails on it (``sby`` FAIL) and *survives* when the SVA still passes.

Operators follow the conservative AssertLLM2 subset that maps onto Yosys
combinational/sequential RTL: ``&&``/``||``, ``==``/``!=``, relational
pairs and their off-by-one neighbours (``<`` vs ``<=``), binary ``+``/``-``,
simple ``if (ident)`` polarity flips, constant flips (``4'd0`` -> ``4'd1``,
``<= 0;`` -> ``<= 1;``) and adjacent ``case`` arm swaps.
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
    ("le_lt", "<=", "<"),  # off-by-one at the boundary
    ("ge_gt", ">=", ">"),
    ("eq_ne", "==", "!="),
    ("ne_eq", "!=", "=="),
    ("and_or", "&&", "||"),
    ("or_and", "||", "&&"),
    ("lt_gt", "<", ">"),
    ("gt_lt", ">", "<"),
    ("lt_le", "<", "<="),
    ("gt_ge", ">", ">="),
    ("add_sub", "+", "-"),
    ("sub_add", "-", "+"),
)

# Sized literals ``4'd0``, ``1'b1``, ``8'hFF`` (x/z digits are left alone), and a bare decimal
# on the right of an assignment ``<= 0;`` / ``= 3;``.
_SIZED_LIT = re.compile(r"\b(\d+)'([sS]?)([bBdDhH])([0-9a-fA-F_]+)\b")
_BARE_ASSIGN_LIT = re.compile(r"(<=|(?<![=!<>])=(?!=))\s*(\d+)\s*;")
# A case item label at the start of a line: an identifier, a sized/bare literal, not ``default``.
_CASE_LABEL = re.compile(r"^[ \t]*((?:\d+'[sS]?[bBdDhH][0-9a-fA-F_xXzZ?]+)|(?:\d+)|(?:[A-Za-z_]\w*))[ \t]*:(?!:)", re.M)
_CASE_OPEN = re.compile(r"\b(case[xz]?)\b")
_CASE_CLOSE = re.compile(r"\bendcase\b")

# ``if (en)``, ``if (!en)``, ``if (req[0])``, ``if (!next_grant[1])``
_IF_IDENT = re.compile(r"\bif\s*\(\s*(!?)\s*([A-Za-z_]\w*(?:\s*\[[^\]\n]+\])?)\s*\)")
# Loop headers are excluded too: ``for (i = N-2; i >= 0; i = i + 1)`` never terminates, and
# yosys unrolls it until the machine runs out of memory (that took a CI runner down).
# ``initial`` values are excluded because the grader holds reset at step 0, so a changed init
# value is overwritten before any check fires and would only pad the survivor count.
_SKIP_LINE = re.compile(
    r"\b(localparam|parameter|posedge|negedge|always|initial|for|while|repeat|genvar|generate)\b",
    re.IGNORECASE,
)
_DEFAULT_PROTECTED = frozenset({"rst_n", "rst", "reset", "reset_n", "resetn", "clk", "clock"})
# Text between the last statement boundary and ``<=`` that makes it a non-blocking assignment:
# an optional ``begin``/case-label, then a bare lvalue (``q``, ``mem[i]``, ``s.f``).
_NBA_LVALUE = re.compile(r"^\s*(?:begin\s+)?(?:[\w\.']+\s*:\s*)?[\w\.\[\]]+\s*$")
_STMT_BOUNDARY = re.compile(r"[;)]|\b(?:begin|end|else)\b")
_RELATIONAL = frozenset({"le_ge", "ge_le", "le_lt", "ge_gt"})


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


def _flip_literal(width: str, signed: str, base: str, digits: str) -> str | None:
    """``4'd0`` -> ``4'd1``, ``1'b1`` -> ``1'b0``, ``8'hFF`` -> ``8'hFE``: flip the low bit."""
    clean = digits.replace("_", "")
    if not clean:
        return None
    try:
        w = int(width)
    except ValueError:  # pragma: no cover - regex guarantees digits
        return None
    radix = {"b": 2, "d": 10, "h": 16}[base.lower()]
    try:
        value = int(clean, radix)
    except ValueError:
        return None
    flipped = value ^ 1
    if w > 0 and flipped >= (1 << w):
        return None
    if radix == 2:
        text = format(flipped, f"0{len(clean)}b")
    elif radix == 16:
        text = format(flipped, f"0{len(clean)}x")
        if clean.isupper():
            text = text.upper()
    else:
        text = str(flipped)
    return f"{width}'{signed}{base}{text}"


def _case_regions(scan: str) -> list[tuple[int, int]]:
    """``(start, end)`` of the text between each ``case`` and its ``endcase`` (outermost only)."""
    regions: list[tuple[int, int]] = []
    depth = 0
    start = 0
    events = sorted(
        [(m.start(), 1) for m in _CASE_OPEN.finditer(scan)] + [(m.start(), -1) for m in _CASE_CLOSE.finditer(scan)]
    )
    for pos, kind in events:
        if kind == 1:
            if depth == 0:
                start = pos
            depth += 1
        else:
            depth = max(0, depth - 1)
            if depth == 0:
                regions.append((start, pos))
    return regions


def _case_arm_swaps(scan: str, body: str) -> list[tuple[str, str]]:
    """Swap the labels of adjacent case arms; each swap is one mutant ``(description, rtl)``."""
    out: list[tuple[str, str]] = []
    for start, end in _case_regions(scan):
        labels = [
            m
            for m in _CASE_LABEL.finditer(scan, start, end)
            if m.group(1).lower() not in {"default", "begin", "end", "else"}
            and not _SKIP_LINE.search(_line_span(scan, m.start())[0])
        ]
        for a, b in zip(labels, labels[1:], strict=False):
            la, lb = a.group(1), b.group(1)
            if la == lb:
                continue
            # replace the later label first so the earlier offsets stay valid
            rtl = _replace_once(body, b.start(1), b.end(1), la)
            rtl = _replace_once(rtl, a.start(1), a.end(1), lb)
            out.append((f"case arms swapped: `{la}` <-> `{lb}`", rtl))
    return out


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

    def _add(operator: str, description: str, mutant_rtl: str) -> None:
        if mutant_rtl in seen_rtl or mutant_rtl == body:
            return
        seen_rtl.add(mutant_rtl)
        found.append(
            Mutant(name=f"{operator}_{len(found)}", operator=operator, description=description, rtl=mutant_rtl)
        )

    for match in _SIZED_LIT.finditer(scan):
        line, _col = _line_span(scan, match.start())
        if _SKIP_LINE.search(line) or guarded.search(line) or _bracket_depth(scan, match.start()) > 0:
            continue
        flipped_lit = _flip_literal(*match.groups())
        if flipped_lit is None:
            continue
        _add(
            "const_flip",
            f"{match.group(0)} -> {flipped_lit} in `{line.strip()}`",
            _replace_once(body, match.start(), match.end(), flipped_lit),
        )

    for match in _BARE_ASSIGN_LIT.finditer(scan):
        line, _col = _line_span(scan, match.start())
        if _SKIP_LINE.search(line) or guarded.search(line):
            continue
        value = int(match.group(2))
        new_value = value ^ 1
        _add(
            "const_flip",
            f"{value} -> {new_value} in `{line.strip()}`",
            _replace_once(body, match.start(2), match.end(2), str(new_value)),
        )

    for description, mutant_rtl in _case_arm_swaps(scan, body):
        _add("case_swap", description, mutant_rtl)

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
