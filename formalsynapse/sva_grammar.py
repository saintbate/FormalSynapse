"""SVA BNF for grammar-constrained decoding (vLLM outlines/xgrammar, or local Outlines).

The grammar is deliberately *structural*: it forces a property/assert/cover block and forbids
prose, but it does not enumerate DUT signal names (those are open identifiers). Anything that
parses here is in the subset :mod:`formalsynapse.sva_lower` can compile; the solver still grades
semantics.
"""

from __future__ import annotations

import re

# Lark / xgrammar-style EBNF. vLLM accepts this via ``guided_grammar``.
SVA_EBNF: str = r"""
root ::= ws ifdef_block ws | ws items ws

ifdef_block ::= "`ifdef" ws "FORMAL" ws items ws "`endif"

items ::= item (ws item)*

item ::= property_decl | labeled_stmt

property_decl ::= "property" ws ident ws opt_empty_parens ws ";" ws clocked_body ws ";" ws "endproperty"

opt_empty_parens ::= ("(" ws ")")?

clocked_body ::= clocking ws disable ws prop

clocking ::= "@" ws "(" ws "posedge" ws ident ws ")"

disable ::= "disable" ws "iff" ws "(" ws expr ws ")"

prop ::= seq (ws impl ws opt_lead_delay seq)?

impl ::= "|->" | "|=>"

opt_lead_delay ::= ("##" delay ws)?

delay ::= number | "[" number ":" number "]"

seq ::= expr (ws "##" delay ws expr)*

labeled_stmt ::= ident ws ":" ws kind ws "property" ws "(" ws prop_arg ws ")" ws opt_else ws ";"

kind ::= "assert" | "assume" | "cover"

prop_arg ::= ident | clocked_body

opt_else ::= ("else" ws "$error" ws "(" ws string ws ")")?

expr ::= unary (ws binop ws unary)*

unary ::= "!" ws unary | sysfn | primary

sysfn ::= "$" ident ws "(" ws expr (ws "," ws expr)* ws ")"

primary ::= ident slice? | number | sized | "(" ws expr ws ")"

slice ::= "[" number (ws ":" ws number)? "]"

binop ::= "&&" | "||" | "==" | "!=" | "<=" | ">=" | "<" | ">" | "&" | "|" | "^" | "+" | "-" | "*"

ident ::= [a-zA-Z_] [a-zA-Z0-9_]*

number ::= [0-9]+

sized ::= [0-9]+ "'" [bodhBODH] [0-9a-fA-FxXzZ_]+

string ::= "\"" [^"]* "\""

ws ::= [ \t\n\r]*
"""

# Last-resort regex for backends that only accept guided_regex (still blocks English).
SVA_REGEX: str = (
    r"(`ifdef[ \t]+FORMAL[ \t\n]+)?"
    r"(property[ \t]+[A-Za-z_][A-Za-z0-9_]*[\s\S]*?endproperty[\s\S]*?"
    r"[A-Za-z_][A-Za-z0-9_]*[ \t]*:[ \t]*(assert|assume|cover)[ \t]+property[\s\S]*?;)+"
    r"([ \t\n]+`endif)?"
)

_FENCE = re.compile(r"```(?:systemverilog|sv|verilog)?\s*([\s\S]*?)```", re.I)
_IFDEF = re.compile(r"`ifdef\s+FORMAL\b([\s\S]*?)`endif", re.I)
_PROPERTY = re.compile(r"property\s+[A-Za-z_]\w*", re.I)
_LABELED = re.compile(r"[A-Za-z_]\w*\s*:\s*(assert|assume|cover)\s+property", re.I)
_THINK = re.compile(r"<think>([\s\S]*?)</think>", re.I)
_PLACEHOLDER = re.compile(r"<(?:antecedent|consequent|module|name|filename)>|p_<module>")


_SVA_START = re.compile(
    r"(`ifdef\s+FORMAL\b|```|property\s+[A-Za-z_]\w*|[A-Za-z_]\w*\s*:\s*(?:assert|assume|cover)\s+property)",
    re.I,
)


class ExtractError(ValueError):
    """Model output contained no recognizable SVA block."""


_PROP_BEGIN = re.compile(r"^\s*property\s+[A-Za-z_]\w*", re.I)
_PROP_END = re.compile(r"\bendproperty\b", re.I)
# A statement line must *start* with the statement (optionally labelled). "We can write: cover
# property (...)" is prose, not a cover named `write`.
_STMT_LINE = re.compile(r"^\s*(?:`?[A-Za-z_]\w*\s*:\s*)?(?:assert|assume|cover)\s+property\b", re.I)
_DIRECTIVE_LINE = re.compile(r"^\s*`(?:ifdef|ifndef|elsif|else|endif|define|undef)\b", re.I)
_DEFAULT_LINE = re.compile(r"^\s*default\s+(?:clocking|disable)\b", re.I)
_LEADING_FENCE = re.compile(r"^\s*```[A-Za-z]*[ \t]*\n")
_TRAILING_FENCE = re.compile(r"\n[ \t]*```[ \t]*$")


def _keep_sva_line(line: str, *, in_prop: bool, in_stmt: bool) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("//") or _DIRECTIVE_LINE.match(stripped):
        return True
    if in_prop or in_stmt:
        return True
    return bool(_PROP_BEGIN.match(stripped) or _STMT_LINE.match(stripped) or _DEFAULT_LINE.match(stripped))


def compact_sva(block: str) -> str | None:
    """Keep property/assert/cover lines; drop English left inside ``ifdef FORMAL``."""
    if _PLACEHOLDER.search(block):
        return None
    out: list[str] = []
    in_prop = False
    in_stmt = False
    for line in block.splitlines():
        stripped = line.strip()
        if _PROP_BEGIN.match(stripped) and not _PROP_END.search(stripped):
            in_prop = True
        if _STMT_LINE.match(stripped):
            in_stmt = True
        if _keep_sva_line(line, in_prop=in_prop, in_stmt=in_stmt):
            out.append(line.rstrip())
        if _PROP_END.search(stripped):
            in_prop = False
        if in_stmt and ";" in stripped:
            in_stmt = False
    body = "\n".join(out).strip()
    if not body or not _LABELED.search(body):
        return None
    if "`ifdef" not in body:
        return "`ifdef FORMAL\n" + body + "\n`endif\n"
    if "`endif" not in body:
        body += "\n`endif"
    return body if body.endswith("\n") else body + "\n"


def _strip_reasoning(text: str) -> str:
    """Drop chain-of-thought wrappers some 14B checkpoints emit before the SVA."""
    inners = [m.group(1) for m in _THINK.finditer(text)]
    without = _THINK.sub("", text)
    if _SVA_START.search(without):
        body = without
    else:
        body = next((inner for inner in inners if _SVA_START.search(inner)), without)
    start = _SVA_START.search(body)
    if start is not None:
        body = body[start.start() :]
    return body


def extract_sva(text: str) -> str:
    """Pull an SVA block out of model output (fences, ``ifdef``, or raw properties)."""
    stripped = _strip_reasoning(text).strip()
    fences = _FENCE.findall(stripped)
    candidates = list(fences) if fences else []
    # An unmatched fence (max_tokens hit before the closing ```) would otherwise survive as
    # a stray `systemverilog macro.
    stripped = _TRAILING_FENCE.sub("", _LEADING_FENCE.sub("", stripped))
    candidates.append(stripped)
    for cand in candidates:
        block = str(cand).strip()
        ifdef = _IFDEF.search(block)
        source = str(ifdef.group(1)).strip() if ifdef is not None else block
        compact = compact_sva(source)
        if compact is not None:
            return compact
    raise ExtractError("no assert/assume/cover property statements in model output")
