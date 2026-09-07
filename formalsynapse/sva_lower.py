"""Lower a bounded subset of concurrent SVA into Yosys-native immediate assertions.

Why this exists
---------------
Open-source Yosys (``read_verilog -sv -formal``) only understands *immediate* assertions
(``assert(expr)`` inside an ``always`` block) plus the sampled-value functions ``$past``,
``$rose``, ``$fell``, ``$stable`` and ``$changed``. Concurrent SVA (``property``/``endproperty``,
``|->``, ``|=>``, ``##n``) requires the commercial Verific frontend, and the ``yosys-slang`` build
shipped with the OSS CAD Suite rejects it with ``SVA unsupported``. This module keeps the project's
SVA template as the *interface* (what humans and LLMs write) while producing something the open
toolchain can prove.

Supported input (one block, typically the contents placed under ```ifdef FORMAL``)::

    property p_name;
        @(posedge clk) disable iff (!rst_n)
        <antecedent> |=> <consequent>;
    endproperty
    a_name: assert property (p_name) else $error("...");
    c_name: cover property (@(posedge clk) disable iff (!rst_n) <sequence>);
    m_name: assume property (p_name);

Property body grammar (bounded, BMC-friendly)::

    prop     := seq [ ( '|->' | '|=>' ) conseq ]
    seq      := expr { '##' INT expr }                 # fixed delays only
    conseq   := [ '##' delay ] seq                     # a range is allowed only as the
    delay    := INT | '[' INT ':' INT ']'              #   leading consequent delay, single expr
    expr     := any boolean SystemVerilog expression (balanced parentheses), may use
                $past(x[, n]), $rose(x), $fell(x), $stable(x), $changed(x), $onehot, $countones

Anything else (unbounded ``##[n:$]``, repetition ``[*n]``, ``sequence`` declarations,
``throughout``,
``s_eventually``, local variables) raises :class:`LowerError`; the harness maps that to the
``ERROR`` status, i.e. the same reward as a syntax error.

Semantics of the lowering
-------------------------
All properties are evaluated at ``posedge clk`` with sampled values, exactly like SVA. For an
implication whose antecedent spans ``A`` cycles and whose consequent element sits ``S`` cycles after
the antecedent's end, we check at the current cycle ``t``::

    if (ante shifted back by S)  assert(consequent element);

where shifting an expression back by ``k`` cycles is ``$past(expr, k)``. A bounded range
``|-> ##[m:n] c`` becomes ``$past(ante, n) -> (c || $past(c,1) || ... || $past(c, n-m))``.
``disable iff (D)`` is honored by requiring ``!D`` at every sampled cycle of the evaluation window,
and every check is additionally gated by a saturating cycle counter so that ``$past`` never reads
before the initial state (Yosys leaves such values unconstrained).

Verbatim regions
----------------
Golden properties sometimes need auxiliary modelling state (for example FIFO ordering checks).
Text between ``// fsyn:verbatim`` and ``// fsyn:endverbatim`` is copied unchanged into the output
and is not parsed as SVA.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["assert", "assume", "cover"]

_DIRECTIVE = frozenset(
    {
        "begin_keywords",
        "celldefine",
        "default_nettype",
        "define",
        "else",
        "elsif",
        "end_keywords",
        "endcelldefine",
        "endif",
        "ifdef",
        "ifndef",
        "include",
        "line",
        "nounconnected_drive",
        "pragma",
        "resetall",
        "timescale",
        "unconnected_drive",
        "undef",
    }
)
_DEFINE = re.compile(r"(?m)^\s*`define\s+([A-Za-z_]\w*)(?!\()(?:[ \t]+(\S.*?))?\s*$")
_MACRO = re.compile(r"`([A-Za-z_]\w*)")

_COUNTER = "f_fsyn_cycles"
_COUNTER_WIDTH = 8
_COUNTER_MAX = (1 << _COUNTER_WIDTH) - 1


class LowerError(ValueError):
    """Raised when the SVA text is outside the supported subset or malformed."""


def collect_defines(*texts: str) -> dict[str, str]:
    """Parse `` `define NAME value `` from DUT / include files. Flag-only defines are skipped."""
    defs: dict[str, str] = {}
    for text in texts:
        for match in _DEFINE.finditer(text):
            name = match.group(1)
            raw = (match.group(2) or "").strip()
            value = re.split(r"//", raw, maxsplit=1)[0].strip()
            if value:
                defs[name] = value
    return defs


def expand_macros(text: str, defines: Mapping[str, str]) -> str:
    """Replace `` `NAME `` with its define. Directives (`` `ifdef ``) are left alone."""
    for _ in range(8):
        def _repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in _DIRECTIVE or name not in defines:
                return match.group(0)
            return defines[name]

        nxt = _MACRO.sub(_repl, text)
        if nxt == text:
            break
        text = nxt
    return text


_TICK_LABEL = re.compile(r"`([A-Za-z_]\w*)(\s*:)")


def strip_tick_labels(text: str) -> str:
    """Turn stray-tick labels (`` `a_foo: ``) into ordinary labels (``a_foo:``)."""

    def _repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _DIRECTIVE:
            return match.group(0)
        return f"{name}{match.group(2)}"

    return _TICK_LABEL.sub(_repl, text)


def leftover_macros(text: str) -> tuple[str, ...]:
    """`` `NAME `` tokens that are not preprocessor directives or labels."""
    found: list[str] = []
    for match in _MACRO.finditer(text):
        name = match.group(1)
        if name in _DIRECTIVE:
            continue
        if re.match(r"\s*:", text[match.end() :]):
            continue
        if name not in found:
            found.append(name)
    return tuple(found)


@dataclass(frozen=True)
class LoweredAssertion:
    """One immediate assertion/cover produced from an SVA statement."""

    name: str
    kind: Kind
    property_name: str | None
    source: str
    history_depth: int


@dataclass(frozen=True)
class LoweredSVA:
    """Result of lowering an SVA block."""

    verilog: str
    assertions: tuple[LoweredAssertion, ...]
    clock: str
    max_history: int
    verbatim_blocks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.assertions)


# --------------------------------------------------------------------------------------------
# Small lexical helpers
# --------------------------------------------------------------------------------------------


def strip_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments (string literals are not special-cased)."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _split_top_level(text: str, seps: tuple[str, ...]) -> list[tuple[str | None, str]]:
    """Split ``text`` on any separator in ``seps`` that occurs at bracket depth zero.

    Returns ``[(None, first_chunk), (sep, chunk), ...]``. Longest separators are matched first.
    """
    ordered = sorted(seps, key=len, reverse=True)
    out: list[tuple[str | None, str]] = []
    depth = 0
    buf: list[str] = []
    last_sep: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                raise LowerError(f"unbalanced brackets in: {text.strip()!r}")
        if depth == 0:
            matched = next((s for s in ordered if text.startswith(s, i)), None)
            if matched is not None:
                out.append((last_sep, "".join(buf)))
                buf = []
                last_sep = matched
                i += len(matched)
                continue
        buf.append(ch)
        i += 1
    if depth != 0:
        raise LowerError(f"unbalanced brackets in: {text.strip()!r}")
    out.append((last_sep, "".join(buf)))
    return out


def _balanced_call_args(text: str, start: int) -> tuple[list[str], int]:
    """Given ``text[start] == '('``, return the top-level comma-split args and index after ')'."""
    if text[start] != "(":
        raise LowerError(f"expected '(' at {start} in {text!r}")
    depth = 0
    buf: list[str] = []
    args: list[str] = []
    i = start
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(buf))
                return [a.strip() for a in args], i + 1
        if ch == "," and depth == 1:
            args.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    raise LowerError(f"unterminated call in {text!r}")


_SAMPLED_FN = re.compile(r"\$(rose|fell|stable|changed)\s*\(")
_PAST_FN = re.compile(r"\$past\s*\(")


def rewrite_sampled_functions(expr: str) -> str:
    """Rewrite ``$rose/$fell/$stable/$changed`` into ``$past`` form.

    Yosys cannot nest these inside ``$past`` ("Don't know how to detect sign and width for
    AST_FCALL node"), but it happily nests ``$past`` inside ``$past``. Only applied when an
    expression must be shifted.
    """
    while True:
        m = _SAMPLED_FN.search(expr)
        if m is None:
            return expr
        args, end = _balanced_call_args(expr, m.end() - 1)
        if len(args) != 1:
            raise LowerError(f"${m.group(1)} takes exactly one argument: {expr!r}")
        x = rewrite_sampled_functions(args[0])
        fn = m.group(1)
        if fn == "rose":
            rep = f"(({x}) && !$past({x}))"
        elif fn == "fell":
            rep = f"(!({x}) && $past({x}))"
        elif fn == "stable":
            rep = f"(({x}) == $past({x}))"
        else:
            rep = f"(({x}) != $past({x}))"
        expr = expr[: m.start()] + rep + expr[end:]


def history_depth(expr: str) -> int:
    """Over-approximate how many past cycles ``expr`` reads (sum over all sampled functions)."""
    depth = 0
    for _m in _SAMPLED_FN.finditer(expr):
        depth += 1
    pos = 0
    while True:
        past_m = _PAST_FN.search(expr, pos)
        if past_m is None:
            break
        args, end = _balanced_call_args(expr, past_m.end() - 1)
        n = 1
        if len(args) >= 2 and args[1]:
            if not re.fullmatch(r"\d+", args[1]):
                raise LowerError(f"$past depth must be an integer literal: {expr.strip()!r}")
            n = int(args[1])
        depth += n + history_depth(args[0])
        pos = past_m.end()
    return depth


def shift(expr: str, k: int) -> str:
    """Return ``expr`` evaluated ``k`` cycles ago."""
    expr = expr.strip()
    if k == 0:
        return f"({expr})"
    return f"$past({rewrite_sampled_functions(expr)}, {k})"


# --------------------------------------------------------------------------------------------
# Property AST
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Seq:
    """``e0 ##d1 e1 ##d2 e2 ...`` with fixed delays; ``offsets[i]`` is e_i's cycle offset."""

    exprs: tuple[str, ...]
    offsets: tuple[int, ...]

    @property
    def span(self) -> int:
        return self.offsets[-1]

    def at_end_shifted(self, k: int) -> str:
        """Conjunction of all elements, evaluated so that the sequence ends ``k`` cycles ago."""
        parts = [shift(e, k + self.span - off) for e, off in zip(self.exprs, self.offsets, strict=True)]
        return " && ".join(parts)

    def history(self) -> int:
        return max(history_depth(e) for e in self.exprs)


@dataclass(frozen=True)
class Range:
    lo: int
    hi: int


@dataclass(frozen=True)
class Property:
    clock: str
    disable: str | None
    antecedent: Seq
    operator: Literal["|->", "|=>"] | None
    consequent: Seq | None
    consequent_range: Range | None  # only with a single-element consequent


def _parse_delay(tok: str) -> int | Range:
    tok = tok.strip()
    if re.fullmatch(r"\d+", tok):
        return int(tok)
    m = re.fullmatch(r"\[\s*(\d+)\s*:\s*(\d+|\$)\s*\]", tok)
    if m is None:
        raise LowerError(f"unsupported delay '##{tok}' (only ##N and ##[m:n] with finite n)")
    if m.group(2) == "$":
        raise LowerError("unbounded delay ##[m:$] is not supported in bounded lowering")
    lo, hi = int(m.group(1)), int(m.group(2))
    if hi < lo:
        raise LowerError(f"invalid delay range ##[{lo}:{hi}]")
    return Range(lo, hi)


_DELAY_TOKEN = re.compile(r"^\s*(\[\s*\d+\s*:\s*(?:\d+|\$)\s*\]|\d+)")


def _parse_seq(text: str, *, allow_leading_delay: bool) -> tuple[Seq, int | Range | None]:
    """Parse a sequence; returns the Seq and an optional leading delay."""
    chunks = _split_top_level(text, ("##",))
    leading: int | Range | None = None
    exprs: list[str] = []
    offsets: list[int] = []
    cursor = 0
    first_sep, first_chunk = chunks[0]
    rest = chunks[1:]
    if first_chunk.strip():
        exprs.append(first_chunk.strip())
        offsets.append(0)
    elif not rest:
        raise LowerError("empty sequence")
    for sep, chunk in rest:
        del sep
        m = _DELAY_TOKEN.match(chunk)
        if m is None:
            raise LowerError(f"expected delay after '##' in {text.strip()!r}")
        delay = _parse_delay(m.group(1))
        body = chunk[m.end() :].strip()
        if not body:
            raise LowerError(f"expected expression after '##{m.group(1)}' in {text.strip()!r}")
        if not exprs:
            if not allow_leading_delay:
                raise LowerError("a leading '##' delay is only allowed in the consequent")
            leading = delay
            exprs.append(body)
            offsets.append(0)
            continue
        if isinstance(delay, Range):
            raise LowerError(
                "a '##[m:n]' range is only supported as the leading delay of a single-element "
                f"consequent: {text.strip()!r}"
            )
        cursor += delay
        exprs.append(body)
        offsets.append(cursor)
    if isinstance(leading, Range) and len(exprs) != 1:
        raise LowerError("'##[m:n]' consequents must consist of a single expression")
    for e in exprs:
        _check_expr(e)
    return Seq(tuple(exprs), tuple(offsets)), leading


_FORBIDDEN = (
    ("[*", "consecutive repetition [*n]"),
    ("[=", "non-consecutive repetition [=n]"),
    ("[->", "goto repetition [->n]"),
    ("throughout", "'throughout'"),
    ("within", "'within'"),
    ("intersect", "'intersect'"),
    ("s_eventually", "'s_eventually'"),
    ("eventually", "'eventually'"),
    ("first_match", "'first_match'"),
    ("s_until", "'s_until'"),
    ("until", "'until'"),
    ("nexttime", "'nexttime'"),
    ("always", "'always'"),
)


def _check_expr(expr: str) -> None:
    if not expr.strip():
        raise LowerError("empty expression")
    for needle, label in _FORBIDDEN:
        if needle[0].isalpha():
            pattern = r"(?<![\w$])" + re.escape(needle) + r"\b"
        else:
            pattern = re.escape(needle)
        if re.search(pattern, expr):
            raise LowerError(f"{label} is not supported in bounded lowering: {expr.strip()!r}")
    if "|->" in expr or "|=>" in expr:
        raise LowerError(f"nested implication is not supported: {expr.strip()!r}")
    if re.search(r"\[\s*\]", expr):
        raise LowerError(f"empty bit-select [] is not valid: {expr.strip()!r}")


_RESET_HINT = re.compile(r"(?i)^(rst|rst_n|reset|reset_n|resetn)$")


def _norm_sv_expr(text: str) -> str:
    s = re.sub(r"\s+", "", text)
    if s.startswith("(") and s.endswith(")") and s.count("(") == 1:
        s = s[1:-1]
    return s


def _redundant_reset_antecedent(ante: str, disable: str | None) -> bool:
    """True when the antecedent is a reset literal (vacuous or always-on under disable iff)."""
    a = _norm_sv_expr(ante)
    name = a[1:] if a.startswith("!") else a
    if _RESET_HINT.fullmatch(name):
        return True
    if disable is None:
        return False
    d = _norm_sv_expr(disable)
    if a == d:
        return True
    return a == f"!{d}" or d == f"!{a}"


_CLOCKING = re.compile(r"^\s*@\s*\(\s*posedge\s+([A-Za-z_]\w*)\s*\)\s*", re.S)
_DISABLE = re.compile(r"^\s*disable\s+iff\s*\(", re.S)


def parse_property_body(body: str) -> Property:
    """Parse ``@(posedge clk) [disable iff (D)] <prop>``.

    Clocking is optional: models often write a bare implication. The FormalSynapse
    subset is single-clock ``clk`` with ``disable iff (!rst_n)``.
    """
    body = body.strip().rstrip(";").strip()
    m = _CLOCKING.match(body)
    if m is None:
        if _DISABLE.match(body) is not None:
            body = f"@(posedge clk) {body}"
        else:
            body = f"@(posedge clk) disable iff (!rst_n) {body}"
        m = _CLOCKING.match(body)
        if m is None:  # pragma: no cover - prefix is constant
            raise LowerError(f"property must start with '@(posedge <clk>)': {body!r}")
    clock = m.group(1)
    rest = body[m.end() :]
    disable: str | None = None
    dm = _DISABLE.match(rest)
    if dm is not None:
        args, end = _balanced_call_args(rest, dm.end() - 1)
        if len(args) != 1:
            raise LowerError("disable iff takes a single expression")
        disable = args[0]
        rest = rest[end:]
    rest = rest.strip()
    if not rest:
        raise LowerError("property has no body")
    parts = _split_top_level(rest, ("|->", "|=>"))
    if len(parts) > 2:
        raise LowerError(f"chained implications are not supported: {rest!r}")
    ante, _ = _parse_seq(parts[0][1], allow_leading_delay=False)
    if _redundant_reset_antecedent(" && ".join(ante.exprs), disable):
        raise LowerError(
            "antecedent is a reset signal or the disable-iff condition; that is either "
            "vacuous or an always-on invariant (e.g. rst_n |-> count==0 means count is "
            "always 0). Delete this property and encode post-reset behavior"
        )
    if len(parts) == 1:
        return Property(clock, disable, ante, None, None, None)
    raw_op = parts[1][0]
    if raw_op == "|->":
        op: Literal["|->", "|=>"] = "|->"
    elif raw_op == "|=>":
        op = "|=>"
    else:  # pragma: no cover - split only yields these
        raise LowerError(f"unexpected operator {raw_op!r}")
    cons, leading = _parse_seq(parts[1][1], allow_leading_delay=True)
    if isinstance(leading, Range):
        return Property(clock, disable, ante, op, cons, leading)
    if isinstance(leading, int):
        # consequent offsets are relative to the antecedent end (before the |->/|=> base shift)
        cons = Seq(cons.exprs, tuple(o + leading for o in cons.offsets))
    return Property(clock, disable, ante, op, cons, None)


# --------------------------------------------------------------------------------------------
# Block parsing
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Statement:
    kind: Kind
    label: str
    property_name: str | None
    inline_body: str | None
    source: str


_PROPERTY_DECL = re.compile(
    r"property\s+([A-Za-z_]\w*)\s*(?:\([^;]*\))?\s*;(.*?)endproperty",
    re.S,
)
_SEQUENCE_DECL = re.compile(r"\bsequence\s+[A-Za-z_]\w*", re.S)
_STATEMENT = re.compile(r"(?:([A-Za-z_]\w*)\s*:\s*)?(assert|assume|cover)\s+property\s*\(", re.S)
_VERBATIM = re.compile(r"//\s*fsyn:verbatim\b(.*?)//\s*fsyn:endverbatim\b", re.S)
_IFDEF = re.compile(
    r"^\s*`ifdef\s+FORMAL\s*\n(.*?)(?:\n\s*`endif\s*)?$",
    re.S | re.I,
)
_DEFAULT_DIR = re.compile(
    r"\bdefault\s+(?:disable\s+iff\s*\([^;]*\)|clocking\b[^;]*)\s*;",
    re.S | re.I,
)
_SIMPLE_DECL = re.compile(
    r"^\s*(?:(?:const\s+)?(?:logic|bit|reg|wire|integer|localparam|parameter)\b[^;]*;)",
    re.M,
)


def _unwrap_ifdef(text: str) -> str:
    m = _IFDEF.match(text.strip())
    return m.group(1) if m else text


def _extract_verbatim(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def take(m: re.Match[str]) -> str:
        blocks.append(m.group(1).strip("\n"))
        return "\n"

    return _VERBATIM.sub(take, text), blocks


def _extract_simple_decls(text: str) -> tuple[str, list[str]]:
    """Promote leftover ``logic``/``parameter`` declarations into verbatim modelling state."""
    decls = [m.group(0).strip() for m in _SIMPLE_DECL.finditer(text)]
    if not decls:
        return text, []
    return _SIMPLE_DECL.sub("\n", text), decls


def _parse_block(text: str) -> tuple[dict[str, str], list[Statement]]:
    if _SEQUENCE_DECL.search(text):
        raise LowerError("'sequence' declarations are not supported; inline the sequence")
    properties: dict[str, str] = {}
    for m in _PROPERTY_DECL.finditer(text):
        name, body = m.group(1), m.group(2)
        if name in properties:
            raise LowerError(f"duplicate property '{name}'")
        properties[name] = body
    remaining = _PROPERTY_DECL.sub("\n", text)

    statements: list[Statement] = []
    pos = 0
    auto = 0
    while True:
        stmt_m = _STATEMENT.search(remaining, pos)
        if stmt_m is None:
            break
        label, kind = stmt_m.group(1), stmt_m.group(2)
        try:
            args, end = _balanced_call_args(remaining, stmt_m.end() - 1)
        except LowerError:
            break
        if len(args) != 1:
            raise LowerError(f"{kind} property takes one argument")
        arg = args[0].strip()
        # consume optional action block up to ';'
        semi = remaining.find(";", end)
        if semi == -1:
            # Truncated last statement (hit max_tokens mid-$error). Keep prior ones.
            break
        tail = remaining[end:semi].strip()
        if tail and not tail.startswith("else"):
            raise LowerError(f"unexpected text after {kind} property: {tail!r}")
        source = remaining[stmt_m.start() : semi + 1].strip()
        if kind not in ("assert", "assume", "cover"):  # pragma: no cover - regex restricts this
            raise LowerError(kind)
        kind_t: Kind = kind  # type: ignore[assignment]
        if re.fullmatch(r"[A-Za-z_]\w*", arg):
            if arg not in properties:
                raise LowerError(f"{kind} property references undefined property '{arg}'")
            stmt = Statement(kind_t, label or "", arg, None, source)
        else:
            stmt = Statement(kind_t, label or "", None, arg, source)
        if not stmt.label:
            auto += 1
            base = stmt.property_name or f"{kind}_{auto}"
            prefix = {"assert": "a", "assume": "m", "cover": "c"}[kind]
            stmt = Statement(kind_t, f"{prefix}_{base}", stmt.property_name, stmt.inline_body, source)
        statements.append(stmt)
        pos = semi + 1
    leftover = _DEFAULT_DIR.sub("", _strip_statements(remaining, statements))
    leftover = re.sub(r"`(?:ifdef|ifndef|endif)\b[^\n]*", " ", leftover)
    leftover = re.sub(r"\b(?:module|endmodule|bind)\b[^;]*;?", " ", leftover)
    leftover = re.sub(r"\b(?:property|generate|genvar)\b[\s\S]*$", " ", leftover)
    leftover_txt = leftover.strip()
    # Models often hit max_tokens mid-statement; a tail with no ';' is truncation, not extra syntax.
    if leftover_txt and ";" in leftover_txt:
        raise LowerError(
            "unsupported text outside property/assert/assume/cover statements: "
            f"{leftover_txt[:120]!r}"
        )
    return properties, statements


def _strip_statements(text: str, statements: list[Statement]) -> str:
    for s in statements:
        text = text.replace(s.source, "", 1)
    return text


# --------------------------------------------------------------------------------------------
# Code generation
# --------------------------------------------------------------------------------------------


def _not_disable(disable: str | None, k: int) -> str | None:
    """``!D`` evaluated ``k`` cycles ago, simplifying ``!(!x)`` to ``x``."""
    if disable is None:
        return None
    d = disable.strip()
    m = re.fullmatch(r"!\s*\(?\s*([A-Za-z_]\w*)\s*\)?", d)
    pos = m.group(1) if m else f"!({d})"
    return shift(pos, k) if k else f"({pos})"


def _reset_gate(disable: str | None, window: int) -> list[str]:
    terms: list[str] = []
    for k in range(window + 1):
        t = _not_disable(disable, k)
        if t is not None:
            terms.append(t)
    return terms


def _emit_check(
    out: list[str],
    *,
    label: str,
    kind: Kind,
    clock: str,
    guard_terms: list[str],
    condition: str | None,
    body: str,
    depth: int,
) -> None:
    gate = [f"{_COUNTER} >= {_COUNTER_WIDTH}'d{depth}", *guard_terms]
    out.append(f"  always @(posedge {clock}) begin")
    out.append(f"    if ({' && '.join(gate)}) begin")
    if condition is not None:
        out.append(f"      if ({condition})")
        out.append(f"        {label}: {kind}({body});")
    else:
        out.append(f"      {label}: {kind}({body});")
    out.append("    end")
    out.append("  end")


def _lower_statement(stmt: Statement, prop: Property, out: list[str]) -> list[LoweredAssertion]:
    produced: list[LoweredAssertion] = []
    ante = prop.antecedent

    if prop.operator is None:
        # Plain sequence: for assert/assume this is an invariant over the sequence end; for cover
        # it is "the sequence completed".
        window = ante.span
        depth = window + ante.history()
        body = ante.at_end_shifted(0)
        _emit_check(
            out,
            label=stmt.label,
            kind=stmt.kind,
            clock=prop.clock,
            guard_terms=_reset_gate(prop.disable, window),
            condition=None,
            body=body,
            depth=depth,
        )
        produced.append(LoweredAssertion(stmt.label, stmt.kind, stmt.property_name, stmt.source, depth))
        return produced

    if stmt.kind == "cover":
        # Named `cover property (p_foo)` where p_foo is an implication: cover the antecedent
        # (non-vacuity). Same as writing `cover property (@(posedge clk) ante)`.
        window = ante.span
        depth = window + ante.history()
        _emit_check(
            out,
            label=stmt.label,
            kind="cover",
            clock=prop.clock,
            guard_terms=_reset_gate(prop.disable, window),
            condition=None,
            body=ante.at_end_shifted(0),
            depth=depth,
        )
        produced.append(LoweredAssertion(stmt.label, stmt.kind, stmt.property_name, stmt.source, depth))
        return produced

    cons = prop.consequent
    if cons is None:  # pragma: no cover - parser guarantees consequent with operator
        raise LowerError("implication without consequent")
    base = 0 if prop.operator == "|->" else 1

    if prop.consequent_range is not None:
        rng = prop.consequent_range
        s_hi = base + rng.hi
        window = s_hi + ante.span
        c = cons.exprs[0]
        alts = [shift(c, j) for j in range(0, rng.hi - rng.lo + 1)]
        depth = window + max(ante.history(), (rng.hi - rng.lo) + history_depth(c))
        _emit_check(
            out,
            label=stmt.label,
            kind=stmt.kind,
            clock=prop.clock,
            guard_terms=_reset_gate(prop.disable, window),
            condition=ante.at_end_shifted(s_hi),
            body=" || ".join(alts),
            depth=depth,
        )
        produced.append(LoweredAssertion(stmt.label, stmt.kind, stmt.property_name, stmt.source, depth))
        return produced

    for j, (c, off) in enumerate(zip(cons.exprs, cons.offsets, strict=True)):
        s = base + off
        window = s + ante.span
        depth = window + max(ante.history(), history_depth(c))
        label = stmt.label if j == 0 else f"{stmt.label}__c{j}"
        _emit_check(
            out,
            label=label,
            kind=stmt.kind,
            clock=prop.clock,
            guard_terms=_reset_gate(prop.disable, window),
            condition=ante.at_end_shifted(s),
            body=f"({c.strip()})",
            depth=depth,
        )
        produced.append(LoweredAssertion(label, stmt.kind, stmt.property_name, stmt.source, depth))
    return produced


def lower(sva_text: str, *, defines: Mapping[str, str] | None = None) -> LoweredSVA:
    """Lower an SVA block to Yosys-compatible immediate assertions.

    ``defines`` expands `` `CNT_LENGTH'd1 `` to ``4'd1`` so mutant copies
    do not need the include file. Leftover macros raise :class:`LowerError`.
    """
    text = _unwrap_ifdef(sva_text)
    if defines:
        text = expand_macros(text, defines)
    text = strip_tick_labels(text)
    leftover = leftover_macros(strip_comments(text))
    if leftover:
        names = ", ".join(f"`{n}" for n in leftover)
        raise LowerError(
            f"SVA uses undefined macros {names}; write sized literals (4'd1) "
            "instead of `CNT_LENGTH'd1"
        )
    text, verbatim = _extract_verbatim(text)
    text = strip_comments(text)
    text, decls = _extract_simple_decls(text)
    verbatim.extend(decls)
    properties, statements = _parse_block(text)
    if not statements and not verbatim:
        raise LowerError("no assert/assume/cover property statements found")

    parsed: dict[str, Property] = {}
    clocks: set[str] = set()
    skipped: list[str] = []
    for name, body in properties.items():
        try:
            parsed[name] = parse_property_body(body)
        except LowerError as exc:
            skipped.append(f"property '{name}': {exc}")
            continue
        clocks.add(parsed[name].clock)

    checks: list[str] = []
    produced: list[LoweredAssertion] = []
    seen: set[str] = set()
    for stmt in statements:
        try:
            if stmt.property_name is not None:
                if stmt.property_name not in parsed:
                    skipped.append(f"{stmt.kind} '{stmt.label}' references dropped property")
                    continue
                prop = parsed[stmt.property_name]
            else:
                assert stmt.inline_body is not None
                prop = parse_property_body(stmt.inline_body)
                clocks.add(prop.clock)
            if stmt.label in seen:
                raise LowerError(f"duplicate label '{stmt.label}'")
            seen.add(stmt.label)
            checks.append(f"  // {stmt.source.splitlines()[0]}")
            produced.extend(_lower_statement(stmt, prop, checks))
        except LowerError as exc:
            skipped.append(f"{stmt.kind} '{stmt.label}': {exc}")
            continue
    if skipped:
        checks.append("  // skipped unsupported/truncated items:")
        checks.extend(f"  //   {item}" for item in skipped)
    if not produced and not verbatim:
        detail = "; ".join(skipped) if skipped else "no assert/assume/cover property statements found"
        raise LowerError(detail)

    if len(clocks) > 1:
        raise LowerError(f"all properties must share one clock; found {sorted(clocks)}")
    clock = next(iter(clocks)) if clocks else "clk"
    max_hist = max((a.history_depth for a in produced), default=0)
    if max_hist > _COUNTER_MAX - 1:
        raise LowerError(f"history depth {max_hist} exceeds lowering limit {_COUNTER_MAX - 1}")

    out: list[str] = [
        "  // ---- generated by formalsynapse.sva_lower; do not edit ----",
        f"  logic [{_COUNTER_WIDTH - 1}:0] {_COUNTER};",
        f"  initial {_COUNTER} = '0;",
        f"  always @(posedge {clock}) begin",
        f"    if ({_COUNTER} != {_COUNTER_WIDTH}'d{_COUNTER_MAX}) {_COUNTER} <= {_COUNTER} + 1'b1;",
        "  end",
    ]
    for vb in verbatim:
        out.append("  // fsyn:verbatim")
        out.append(vb)
        out.append("  // fsyn:endverbatim")
    out.extend(checks)
    return LoweredSVA(
        verilog="\n".join(out) + "\n",
        assertions=tuple(produced),
        clock=clock,
        max_history=max_hist,
        verbatim_blocks=tuple(verbatim),
    )
