"""Merge a (lowered) assertion block into a copy of the DUT.

Assertions are placed *inside* the target module, immediately before its ``endmodule``, wrapped in
```ifdef FORMAL ... `endif``. Yosys defines ``FORMAL`` automatically when reading with
``read_verilog -formal``, so the same source stays synthesizable for non-formal flows and the
assertions can observe internal registers without ``bind`` (unsupported in open-source Yosys).
"""

from __future__ import annotations

import re

from formalsynapse.sva_lower import strip_comments


class InjectError(ValueError):
    """Raised when the target module cannot be located in the DUT source."""


_MODULE_HDR = re.compile(r"(?<![\w$])module\s+([A-Za-z_]\w*)")
_ENDMODULE = re.compile(r"(?<![\w$])endmodule\b")
_IFDEF_FORMAL = re.compile(r"^\s*`ifdef\s+FORMAL\b", re.M)


def _module_span(dut_text: str, top: str) -> tuple[int, int]:
    """Return ``(header_start, endmodule_start)`` for module ``top`` in the original text.

    Comments are blanked (not removed) for searching so offsets stay aligned with ``dut_text``.
    """
    blanked = _blank_comments(dut_text)
    for m in _MODULE_HDR.finditer(blanked):
        if m.group(1) != top:
            continue
        end = _ENDMODULE.search(blanked, m.end())
        if end is None:
            raise InjectError(f"module '{top}' has no matching 'endmodule'")
        return m.start(), end.start()
    names = sorted({m.group(1) for m in _MODULE_HDR.finditer(blanked)})
    raise InjectError(f"module '{top}' not found in DUT (modules present: {names or 'none'})")


def _blank_comments(text: str) -> str:
    """Replace comment characters with spaces, preserving length and newlines."""

    def blank(m: re.Match[str]) -> str:
        return "".join("\n" if c == "\n" else " " for c in m.group(0))

    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    return re.sub(r"//[^\n]*", blank, text)


def module_names(dut_text: str) -> list[str]:
    """All module names declared in ``dut_text``."""
    return [m.group(1) for m in _MODULE_HDR.finditer(_blank_comments(dut_text))]


def inject(dut_text: str, block_text: str, top: str, *, wrap_ifdef: bool = True) -> str:
    """Insert ``block_text`` before the ``endmodule`` of ``top``.

    ``block_text`` is wrapped in ```ifdef FORMAL`` unless it already starts with one or
    ``wrap_ifdef`` is False. The DUT text is otherwise returned byte-for-byte.
    """
    if not strip_comments(block_text).strip():
        raise InjectError("assertion block is empty")
    _, end_start = _module_span(dut_text, top)
    body = block_text.rstrip("\n")
    if wrap_ifdef and not _IFDEF_FORMAL.match(body):
        body = "`ifdef FORMAL\n" + body + "\n`endif"
    head = dut_text[:end_start].rstrip("\n")
    tail = dut_text[end_start:]
    return f"{head}\n\n{body}\n{tail}"


def has_formal_block(dut_text: str) -> bool:
    """True if the DUT already carries an ```ifdef FORMAL`` region."""
    return _IFDEF_FORMAL.search(dut_text) is not None


_IFDEF_ANY = re.compile(r"^\s*`(ifdef|ifndef|elsif|else|endif)\b", re.M)


def strip_formal_blocks(dut_text: str) -> str:
    """Remove every ```ifdef FORMAL ... `endif`` region (preprocessor-depth aware)."""
    lines = dut_text.splitlines(keepends=True)
    out: list[str] = []
    depth = 0
    skipping = 0
    for line in lines:
        m = _IFDEF_ANY.match(line)
        if m is None:
            if skipping == 0:
                out.append(line)
            continue
        kind = m.group(1)
        if kind in ("ifdef", "ifndef"):
            entering = kind == "ifdef" and re.search(r"`ifdef\s+FORMAL\b", line) is not None
            if skipping:
                skipping += 1
            elif entering:
                skipping = 1
            else:
                depth += 1
                out.append(line)
        elif kind == "endif":
            if skipping:
                skipping -= 1
            else:
                depth = max(0, depth - 1)
                out.append(line)
        elif skipping == 0:
            out.append(line)
    return "".join(out)


def inject_clean(dut_text: str, block_text: str, top: str, *, wrap_ifdef: bool = True) -> str:
    """Strip any existing FORMAL region, then inject ``block_text``."""
    return inject(strip_formal_blocks(dut_text), block_text, top, wrap_ifdef=wrap_ifdef)
