from __future__ import annotations

from formalsynapse.sva_edit import has_assert, merge_sva, strip_labels, unwrap_formal, wrap_formal

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


def test_strip_ignores_the_word_property_in_comments() -> None:
    """'// Property for requirement 1' used to parse as a declaration named 'for' and swallow
    the real property below it as an orphan (edge_detector regen: statements left without
    their properties, then flagged as undeclared signals)."""
    commented = TWO.replace("property p_keep;", "// Property for Requirement 1: keep counting\nproperty p_keep;")
    body = unwrap_formal(strip_labels(commented, ("a_bad",)))
    assert "property p_keep;" in body and "endproperty" in body and "a_keep" in body
    assert "p_bad" not in body
    # A string mentioning 'property x' is not a declaration either.
    stringy = TWO.replace('$error("keep")', '$error("property nope; is fine")')
    assert "a_keep" in unwrap_formal(strip_labels(stringy, ("a_bad",)))


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


def test_has_assert_ignores_covers() -> None:
    assert has_assert(TWO)
    assert not has_assert("`ifdef FORMAL\nc_en: cover property (en);\n`endif")


def test_has_assert_ignores_comments() -> None:
    assert not has_assert("`ifdef FORMAL\n// a_x: assert property (p);\nc_en: cover property (en);\n`endif")


def test_strip_handles_fatal_and_begin_end_action_blocks() -> None:
    sva = """\
`ifdef FORMAL
a_one: assert property (@(posedge clk) a |-> b)
    else $fatal;
a_two: assert property (@(posedge clk) c |-> d)
    else begin
        $error("two; failed");
        $fatal(1, "stop");
    end
a_keep: assert property (@(posedge clk) e |-> f);
`endif
"""
    kept = unwrap_formal(strip_labels(sva, ("a_one", "a_two")))
    assert "a_one" not in kept
    assert "a_two" not in kept
    assert "$fatal" not in kept
    assert kept.strip() == "a_keep: assert property (@(posedge clk) e |-> f);"
