"""Deterministic edits on authored (concurrent) SVA blocks.

CEGAR repair strips failed labels in Python instead of asking the model to delete them.
"""

from __future__ import annotations

import re

from formalsynapse.sva_lower import blank_comments

_IFDEF = re.compile(r"^\s*`ifdef\s+FORMAL\b", re.I | re.M)
_SYSTASK = re.compile(r"\$[A-Za-z_]\w*")
_ENDIF = re.compile(r"^\s*`endif\b", re.I | re.M)
# Declaration form only (``property p_x;`` / ``property p_x(...)``): not ``assert property (p_x)``.
_PROP = re.compile(r"\bproperty\s+(?P<name>[A-Za-z_]\w*)\s*(?=[;(])", re.I)
_ENDPROP = re.compile(r"\bendproperty\b", re.I)
_LABELED = re.compile(
    r"(?P<label>[A-Za-z_]\w*)\s*:\s*(?P<kind>assert|assume|cover)\s+property\b",
    re.I,
)
_PROP_REF = re.compile(
    r"\b(?:assert|assume|cover)\s+property\s*\(\s*([A-Za-z_]\w*)\s*\)",
    re.I,
)


def has_assert(sva: str) -> bool:
    """True when the block has a labeled assert or assume. Covers alone do not count.

    Comments and string literals are ignored, so a commented-out assert is not an assert.
    """
    for match in _LABELED.finditer(blank_comments(sva, strings=True)):
        if match.group("kind").lower() in {"assert", "assume"}:
            return True
    return False


def unwrap_formal(block: str) -> str:
    """Return the inside of ```ifdef FORMAL ... `endif`` if present."""
    text = block.strip()
    start = _IFDEF.search(text)
    if start is None:
        return text
    end = None
    for m in _ENDIF.finditer(text):
        end = m
    if end is None or end.start() <= start.end():
        return text
    return text[start.end() : end.start()].strip()


def wrap_formal(body: str) -> str:
    """Wrap ``body`` in ```ifdef FORMAL`` unless it already is."""
    text = body.strip()
    if not text:
        return ""
    if _IFDEF.match(text):
        return text if text.endswith("\n") else text + "\n"
    return f"`ifdef FORMAL\n{text}\n`endif\n"


def merge_sva(kept: str, addition: str) -> str:
    """Concatenate two formal blocks (kept survivors + new slots)."""
    a = unwrap_formal(kept)
    b = unwrap_formal(addition)
    if not a:
        return wrap_formal(b)
    if not b:
        return wrap_formal(a)
    return wrap_formal(a.rstrip() + "\n\n" + b)


def declared_names(sva: str) -> tuple[set[str], set[str]]:
    """(property names, statement labels) declared in a block, lower-cased. Comments ignored."""
    text = blank_comments(unwrap_formal(sva), strings=True)
    props = {m.group("name").lower() for m in _PROP.finditer(text)}
    labels = {m.group("label").lower() for m in _LABELED.finditer(text)}
    return props, labels


def drop_duplicates(kept: str, addition: str) -> tuple[str, tuple[str, ...]]:
    """Remove from ``addition`` any label or property already declared in ``kept``.

    CEGAR tells the model "do not copy the kept properties back"; when it does anyway, the
    kept copy is the proven one, so the re-emitted statement is dropped rather than letting
    the lowerer reject the whole turn as a duplicate. Returns (edited addition, dropped names).
    """
    props, labels = declared_names(kept)
    if not props and not labels:
        return addition, ()
    text = unwrap_formal(addition)
    if not text:
        return addition, ()
    clean = blank_comments(text, strings=True)
    dropped: list[str] = []
    ranges = _labeled_ranges(clean, labels)
    dropped.extend(m.group("label") for m in _LABELED.finditer(clean) if m.group("label").lower() in labels)
    # Statements that reference a re-emitted property go too, or they would dangle.
    dup_props = {name.lower() for name, _, _ in _property_ranges(clean) if name.lower() in props}
    for m in _LABELED.finditer(clean):
        if m.group("label").lower() in labels:
            continue
        end = _statement_end(clean, m.end())
        if end < 0:
            continue
        ref = _PROP_REF.search(clean, m.start(), end)
        if ref is not None and ref.group(1).lower() in dup_props:
            ranges.append((_line_start(clean, m.start()), end))
            dropped.append(m.group("label"))
    ranges.extend((a, b) for name, a, b in _property_ranges(clean) if name.lower() in props)
    dropped.extend(name for name, _, _ in _property_ranges(clean) if name.lower() in props)
    if not ranges:
        return addition, ()
    edited = _cut(text, sorted(set(ranges))).strip()
    return wrap_formal(edited), tuple(dict.fromkeys(dropped))


def strip_truncated(sva: str) -> str:
    """Cut an unterminated trailing ``property`` / statement (the model hit its token cap).

    Everything from the last ``property NAME`` without an ``endproperty``, or the last labeled
    statement without a closing ``;``, to the end of the block is removed. Complete items are
    untouched, so the result lowers to exactly the checks the truncated block did.
    """
    text = unwrap_formal(sva)
    if not text:
        return ""
    clean = blank_comments(text, strings=True)
    cut = len(text)
    for m in _PROP.finditer(clean):
        if _ENDPROP.search(clean, m.end()) is None:
            cut = min(cut, _line_start(clean, m.start()))
            break
    for m in _LABELED.finditer(clean):
        if m.start() >= cut:
            break
        if _statement_end(clean, m.end()) < 0:  # no closing ';' (nor a complete action block)
            cut = min(cut, _line_start(clean, m.start()))
            break
    if cut >= len(text):
        return wrap_formal(text)
    return wrap_formal(text[:cut].rstrip())


def strip_labels(sva: str, labels: tuple[str, ...]) -> str:
    """Drop labeled assert/assume/cover statements and orphaned property decls."""
    if not labels:
        return sva if sva.endswith("\n") or not sva else sva + "\n"
    wanted = {lab.lower() for lab in labels}
    text = unwrap_formal(sva)
    if not text:
        return ""
    # Scan comment-blanked text (same offsets) and cut the original: a comment such as
    # "// Property for requirement 1" must not be mistaken for a declaration named "for".
    drop_ranges = _labeled_ranges(blank_comments(text, strings=True), wanted)
    kept = _cut(text, drop_ranges)
    clean = blank_comments(kept, strings=True)
    refs = {m.group(1).lower() for m in _PROP_REF.finditer(clean)}
    prop_ranges = _property_ranges(clean)
    orphan = [(a, b) for name, a, b in prop_ranges if name.lower() not in refs]
    kept = _cut(kept, orphan).strip()
    return wrap_formal(kept)


def _labeled_ranges(text: str, wanted: set[str]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for m in _LABELED.finditer(text):
        if m.group("label").lower() not in wanted:
            continue
        end = _statement_end(text, m.end())
        if end < 0:
            continue
        start = _line_start(text, m.start())
        ranges.append((start, end))
    return ranges


def _property_ranges(text: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for m in _PROP.finditer(text):
        end_m = _ENDPROP.search(text, m.end())
        if end_m is None:
            continue
        start = _line_start(text, m.start())
        end = end_m.end()
        while end < len(text) and text[end] in " \t":
            end += 1
        if end < len(text) and text[end] == ";":
            end += 1
        if end < len(text) and text[end] == "\n":
            end += 1
        out.append((m.group("name"), start, end))
    return out


def _statement_end(text: str, from_idx: int) -> int:
    i = _skip_ws(text, from_idx)
    if i >= len(text) or text[i] != "(":
        return -1
    i = _skip_balanced(text, i)
    if i < 0:
        return -1
    i = _skip_ws(text, i)
    if text.startswith("else", i):
        i = _skip_ws(text, i + 4)
        if text.startswith("begin", i):
            # else begin $error(...); $fatal; end
            end = _skip_begin_end(text, i)
            if end < 0:
                return -1
            i = _skip_ws(text, end)
            if i < len(text) and text[i] == ";":
                i += 1
            if i < len(text) and text[i] == "\n":
                i += 1
            return i
        task = _SYSTASK.match(text, i)  # $error / $fatal / $warning / $info / $display
        if task is not None:
            i = _skip_ws(text, task.end())
            if i < len(text) and text[i] == "(":
                i = _skip_balanced(text, i)
                if i < 0:
                    return -1
                i = _skip_ws(text, i)
    if i < len(text) and text[i] == ";":
        i += 1
        if i < len(text) and text[i] == "\n":
            i += 1
        return i
    return -1


def _skip_begin_end(text: str, start: int) -> int:
    """Index just past the ``end`` matching the ``begin`` at ``start`` (string/comment aware)."""
    code = blank_comments(text, strings=True)
    depth = 0
    for m in re.finditer(r"\b(begin|end)\b", code[start:]):
        if m.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return start + m.end()
    return -1


def _skip_balanced(text: str, start: int) -> int:
    depth = 0
    i = start
    in_str = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < len(text):
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\n\r":
        i += 1
    return i


def _line_start(text: str, i: int) -> int:
    while i > 0 and text[i - 1] != "\n":
        i -= 1
    return i


def _cut(text: str, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return text
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = []
    for a, b in ordered:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    out: list[str] = []
    cursor = 0
    for a, b in merged:
        out.append(text[cursor:a])
        cursor = b
    out.append(text[cursor:])
    return "".join(out)
