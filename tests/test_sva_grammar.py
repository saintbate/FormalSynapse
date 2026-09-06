from __future__ import annotations

import pytest

from formalsynapse.sva_grammar import SVA_EBNF, ExtractError, compact_sva, extract_sva

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


def test_extract_strips_think_wrapper() -> None:
    text = "<think>plan the increment property then emit SVA</think>\n" + BLOCK
    assert extract_sva(text).startswith("`ifdef FORMAL")
    assert "<think>" not in extract_sva(text)
    assert "p_counter_inc" in extract_sva(text)


def test_extract_sva_inside_unclosed_think() -> None:
    text = "<think>\nWe should write:\n" + BLOCK
    out = extract_sva(text)
    assert "p_counter_inc" in out
    assert "<think>" not in out


def test_extract_rejects_angle_placeholders() -> None:
    with pytest.raises(ExtractError):
        extract_sva(
            "property p_<module>_inc;\n"
            "    @(posedge clk) disable iff (!rst_n)\n"
            "    <antecedent> |=> <consequent>;\n"
            "endproperty\n"
        )


def test_extract_requires_labeled_assert() -> None:
    with pytest.raises(ExtractError):
        extract_sva(
            "`ifdef FORMAL\n"
            "property p_reset_qi;\n"
            "    @(posedge clk) disable iff (rst) 1'b1 |-> (qi == 0);\n"
            "endproperty\n"
            "`endif\n"
        )


def test_extract_drops_english_inside_ifdef() -> None:
    text = """`ifdef FORMAL
property p_cke_hold;
    @(posedge clk) disable iff (rst)
    !cke |=> q_bin == $past(q_bin);
endproperty
   But wait: the reset is asynchronous so we cannot check during reset.
a_cke_hold: assert property (p_cke_hold);
`endif
"""
    out = extract_sva(text)
    assert "p_cke_hold" in out
    assert "a_cke_hold" in out
    assert "But wait" not in out


def test_compact_sva_rejects_placeholders() -> None:
    assert compact_sva("property p_<module>; endproperty\na_x: assert property (p_x);") is None


def test_extract_rejects_prose() -> None:
    with pytest.raises(ExtractError):
        extract_sva("The assertions should check that the counter increments.")


def test_extract_falls_back_when_fence_is_empty() -> None:
    text = "```systemverilog\n```\n" + BLOCK
    assert "p_counter_inc" in extract_sva(text)


def test_ebnf_mentions_implication() -> None:
    assert "|=>" in SVA_EBNF
    assert "endproperty" in SVA_EBNF
