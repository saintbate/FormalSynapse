from __future__ import annotations

from formalsynapse.sva_edit import merge_sva, strip_labels, unwrap_formal, wrap_formal

TWO = """\
`ifdef FORMAL
property p_keep;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 1;
endproperty
a_keep: assert property (p_keep)
    else $error("keep");

property p_bad;
    @(posedge clk) disable iff (!rst_n)
    rst_n |-> count == 0;
endproperty
a_bad: assert property (p_bad);
`endif
"""


def test_strip_failed_keeps_survivors() -> None:
    kept = strip_labels(TWO, ("a_bad",))
    body = unwrap_formal(kept)
    assert "p_keep" in body
    assert "a_keep" in body
    assert "p_bad" not in body
    assert "a_bad" not in body


def test_strip_all_failed_is_empty() -> None:
    kept = strip_labels(TWO, ("a_keep", "a_bad"))
    assert unwrap_formal(kept) == ""


def test_merge_and_wrap() -> None:
    kept = strip_labels(TWO, ("a_bad",))
    extra = wrap_formal("a_new: assert property (p_new);")
    merged = merge_sva(kept, extra)
    assert "a_keep" in merged
    assert "a_new" in merged
    assert merged.count("`ifdef FORMAL") == 1
