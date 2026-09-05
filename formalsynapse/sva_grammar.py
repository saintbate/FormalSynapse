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

opt_empty_parens ::= "(" ws ")" |

clocked_body ::= clocking ws disable ws prop

clocking ::= "@" ws "(" ws "posedge" ws ident ws ")"

disable ::= "disable" ws "iff" ws "(" ws expr ws ")"

prop ::= seq (ws impl ws opt_lead_delay seq)?

impl ::= "|->" | "|=>"

opt_lead_delay ::= "##" delay ws |

delay ::= number | "[" number ":" number "]"

seq ::= expr (ws "##" delay ws expr)*

labeled_stmt ::= ident ws ":" ws kind ws "property" ws "(" ws prop_arg ws ")" ws opt_else ws ";"

kind ::= "assert" | "assume" | "cover"

prop_arg ::= ident | clocked_body

opt_else ::= "else" ws "$error" ws "(" ws string ws ")" |

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


class ExtractError(ValueError):
    """Model output contained no recognizable SVA block."""


def extract_sva(text: str) -> str:
    """Pull an SVA block out of model output (fences, ``ifdef``, or raw properties)."""
    stripped = text.strip()
    fences = _FENCE.findall(stripped)
    candidates = list(fences) if fences else []
    candidates.append(stripped)
    for cand in candidates:
        block = cand.strip()
        ifdef = _IFDEF.search(block)
        if ifdef is not None:
            inner = ifdef.group(1).strip()
            if _PROPERTY.search(inner) or _LABELED.search(inner):
                return "`ifdef FORMAL\n" + inner + "\n`endif\n"
        if _PROPERTY.search(block) or _LABELED.search(block):
            if "`ifdef" not in block:
                return "`ifdef FORMAL\n" + block.strip() + "\n`endif\n"
            return block if block.endswith("\n") else block + "\n"
    raise ExtractError("no property/assert/cover block in model output")
