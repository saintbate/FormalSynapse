from __future__ import annotations

import pytest

from formalsynapse.sva_grammar import SVA_EBNF, ExtractError, extract_sva

BLOCK = """\
`ifdef FORMAL
property p_counter_inc;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 4'd1;
endproperty
a_counter_inc: assert property (p_counter_inc)
    else $error("Assertion Failed: inc violated at cycle %0t", $time);
`endif
"""


def test_extract_raw() -> None:
    assert "p_counter_inc" in extract_sva(BLOCK)


def test_extract_fenced() -> None:
    text = "Here you go:\n```systemverilog\n" + BLOCK + "```\n"
    out = extract_sva(text)
    assert "endproperty" in out
    assert "`ifdef FORMAL" in out


def test_extract_without_ifdef_wraps() -> None:
    inner = "property p_x; @(posedge clk) disable iff (!rst_n) a |=> b; endproperty\n"
    inner += "a_x: assert property (p_x);"
    out = extract_sva(inner)
    assert out.startswith("`ifdef FORMAL")


def test_extract_rejects_prose() -> None:
    with pytest.raises(ExtractError):
        extract_sva("The assertions should check that the counter increments.")


def test_extract_falls_back_when_fence_is_empty() -> None:
    text = "```systemverilog\n```\n" + BLOCK
    assert "p_counter_inc" in extract_sva(text)


def test_ebnf_mentions_implication() -> None:
    assert "|=>" in SVA_EBNF
    assert "endproperty" in SVA_EBNF
