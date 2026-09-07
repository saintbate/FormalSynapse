from __future__ import annotations

import pytest

from formalsynapse.sva_lower import (
    LoweredSVA,
    LowerError,
    base_label,
    blank_comments,
    history_depth,
    lower,
    parse_property_body,
    shift,
    strip_comments,
)

SVA = """\
property p_demo_inc;
    @(posedge clk) disable iff (!rst_n)
    en && count != 4'hF |=> count == $past(count) + 4'd1;
endproperty
a_demo_inc: assert property (p_demo_inc)
    else $error("inc");
c_demo_en: cover property (@(posedge clk) disable iff (!rst_n) en);
"""


def test_lower_implication_and_cover() -> None:
    result = lower(SVA)
    assert result.clock == "clk"
    assert "a_demo_inc" in result.names
    assert "c_demo_en" in result.names
    assert "f_fsyn_cycles" in result.verilog
    assert "|=>" not in result.verilog
    assert "endproperty" not in result.verilog
    assert "a_demo_inc: assert(" in result.verilog
    assert "c_demo_en: cover(" in result.verilog


def test_overlapping_vs_nonoverlapping() -> None:
    ov = parse_property_body("@(posedge clk) disable iff (!rst_n) a |-> b")
    nv = parse_property_body("@(posedge clk) disable iff (!rst_n) a |=> b")
    assert ov.operator == "|->"
    assert nv.operator == "|=>"
    assert lower("a_ov: assert property (@(posedge clk) disable iff (!rst_n) a |-> b);").verilog
    ov_v = lower("a_ov: assert property (@(posedge clk) disable iff (!rst_n) a |-> b);").verilog
    nv_v = lower("a_nv: assert property (@(posedge clk) disable iff (!rst_n) a |=> b);").verilog
    assert "$past((a), 1)" in nv_v or "$past(a, 1)" in nv_v or "a), 1)" in nv_v
    assert ov_v != nv_v


def test_range_consequent() -> None:
    body = parse_property_body("@(posedge clk) disable iff (!rst_n) req |=> ##[0:3] gnt")
    assert body.consequent_range is not None
    assert body.consequent_range.lo == 0
    assert body.consequent_range.hi == 3
    verilog = lower("a_f: assert property (@(posedge clk) disable iff (!rst_n) req |=> ##[0:3] gnt);").verilog
    assert "||" in verilog


def test_unbounded_rejected() -> None:
    with pytest.raises(LowerError, match="unbounded"):
        lower("a_x: assert property (@(posedge clk) disable iff (!rst_n) a |-> ##[0:$] b);")


def test_repetition_rejected() -> None:
    with pytest.raises(LowerError, match="repetition"):
        lower("a_x: assert property (@(posedge clk) disable iff (!rst_n) a[*3] |=> b);")


def test_missing_clock_gets_default() -> None:
    result = lower("a_x: assert property (a |=> b);")
    assert result.clock == "clk"
    assert "a_x: assert(" in result.verilog


def test_shift_and_history() -> None:
    assert shift("en", 0) == "(en)"
    assert shift("en", 2) == "$past(en, 2)"
    assert history_depth("$past(count, 2)") == 2
    rewritten = shift("$rose(en)", 1)
    assert "$rose" not in rewritten
    assert "$past" in rewritten


def test_verbatim_copied() -> None:
    text = """
// fsyn:verbatim
logic f_flag;
// fsyn:endverbatim
a_x: assert property (@(posedge clk) disable iff (!rst_n) f_flag |-> 1'b1);
"""
    result = lower(text)
    assert "logic f_flag;" in result.verilog
    assert result.verbatim_blocks


def test_ifdef_unwrapped() -> None:
    result = lower("`ifdef FORMAL\n" + SVA + "\n`endif\n")
    assert result.names


def test_empty_rejected() -> None:
    with pytest.raises(LowerError, match="no assert"):
        lower("// nothing\n")


def test_cover_named_implication_covers_antecedent() -> None:
    text = """
property p_inc;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 1;
endproperty
a_inc: assert property (p_inc);
c_inc: cover property (p_inc);
"""
    result = lower(text)
    assert "c_inc: cover(" in result.verilog
    assert "en" in result.verilog


def test_truncated_tail_ignored() -> None:
    text = SVA + "\nc_partial: cover prope"
    result = lower(text)
    assert "a_demo_inc" in result.names


def test_implicit_clocking() -> None:
    result = lower("a_inv: assert property (en |=> count == $past(count) + 1);")
    assert "a_inv: assert(" in result.verilog


def test_property_formals_and_decls() -> None:
    text = """
default disable iff (!rst_n);
logic [3:0] f_shadow;
property p_hold(clk);
    en |=> count == $past(count);
endproperty
a_hold: assert property (p_hold);
"""
    result = lower(text)
    assert "logic [3:0] f_shadow;" in result.verilog
    assert "a_hold: assert(" in result.verilog


def test_truncated_property_and_generate_dropped() -> None:
    text = """
`ifdef FORMAL
property p_ok;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count);
endproperty
a_ok: assert property (p_ok);
property p_cut;
    @(posedge clk) disable iff (!rst_n)
    en |=> bin =
generate
    genvar i;
    for (i = 0; i < 4; i++)
`endif
"""
    result = lower(text)
    assert "a_ok" in result.names


def test_unterminated_last_assert_dropped() -> None:
    text = """
property p_ok;
    @(posedge clk) disable iff (!rst_n)
    en |=> 1'b1;
endproperty
a_ok: assert property (p_ok);
a_cut: assert property (p_ok
"""
    result = lower(text)
    assert "a_ok" in result.names


def test_active_high_rst_antecedent_skipped() -> None:
    text = """
property p_ok;
    @(posedge clk) disable iff (rst)
    cke |=> qi == $past(q_next);
endproperty
a_ok: assert property (p_ok);
property p_reset;
    @(posedge clk) disable iff (rst)
    rst |-> qi == 0;
endproperty
a_reset: assert property (p_reset);
"""
    result = lower(text)
    assert "a_ok" in result.names
    assert "a_reset" not in result.names
    assert "reset" in result.verilog.lower() or "disable-iff" in result.verilog


def test_rst_n_antecedent_skipped() -> None:
    text = """
property p_ok;
    @(posedge clk) disable iff (!rst_n)
    up && !down |=> count == $past(count) + 1;
endproperty
a_ok: assert property (p_ok);
property p_reset;
    @(posedge clk) disable iff (!rst_n)
    rst_n |-> count == 4'd0;
endproperty
a_reset: assert property (p_reset);
"""
    result = lower(text)
    assert "a_ok" in result.names
    assert "a_reset" not in result.names
    assert "rst_n" in result.verilog  # skip comment


def test_empty_bit_select_skipped() -> None:
    text = """
property p_ok;
    @(posedge clk) disable iff (!rst_n)
    en |=> 1'b1;
endproperty
a_ok: assert property (p_ok);
property p_bad;
    @(posedge clk) disable iff (!rst_n)
    req[] |=> grant[0];
endproperty
a_bad: assert property (p_bad);
"""
    result = lower(text)
    assert "a_ok" in result.names
    assert "a_bad" not in result.names


def test_unlowerable_property_skipped() -> None:
    text = """
property p_ok;
    @(posedge clk) disable iff (!rst_n)
    en |=> 1'b1;
endproperty
a_ok: assert property (p_ok);
property p_bad;
    @(posedge clk) disable iff (!rst_n)
    en |=> eventually done;
endproperty
a_bad: assert property (p_bad);
"""
    result = lower(text)
    assert "a_ok" in result.names
    assert "a_bad" not in result.names
    assert "eventually" in result.verilog  # mentioned in skip comment


def test_lower_expands_width_macro() -> None:
    text = """
property p_dec;
    @(posedge clk) disable iff (rst)
    !clear |-> q_next == (qi - `CNT_LENGTH'd1);
endproperty
a_dec: assert property (p_dec);
"""
    result = lower(text, defines={"CNT_LENGTH": "4"})
    assert "`CNT_LENGTH" not in result.verilog
    assert "4'd1" in result.verilog


def test_lower_rejects_unknown_macro() -> None:
    text = """
a_dec: assert property (@(posedge clk) disable iff (rst) clear |-> q_next == `CNT_LENGTH'd0);
"""
    with pytest.raises(LowerError, match="undefined macros"):
        lower(text)


def test_lower_accepts_tick_prefixed_label() -> None:
    text = """
`a_counter_reset: assert property (@(posedge clk) disable iff (rst) q == 0);
"""
    result = lower(text)
    assert "a_counter_reset" in result.names
    assert "`a_counter_reset" not in result.verilog


def test_lower_ignores_backticks_in_comments() -> None:
    text = """
// DUT is ben-marshall/uart `uart_tx`
a_idle: assert property (@(posedge clk) disable iff (!resetn) !busy |-> txd);
"""
    result = lower(text)
    assert "a_idle" in result.names


# ---- grader-integrity fixes -----------------------------------------------------------------


def _depth(result: LoweredSVA, name: str) -> int:
    return next(a.history_depth for a in result.assertions if a.name == name)


def test_guard_depth_is_max_not_sum() -> None:
    """``req |=> ##[0:10] gnt`` must be checkable within a depth-20 BMC (guard 11, not 21)."""
    rng = lower("a_r: assert property (@(posedge clk) disable iff (!rst_n) req |=> ##[0:10] gnt);", auto_cover=False)
    assert _depth(rng, "a_r") == 11
    assert "f_fsyn_cycles >= 8'd11" in rng.verilog
    assert "f_fsyn_cycles >= 8'd21" not in rng.verilog
    fixed = lower(
        "a_f: assert property (@(posedge clk) disable iff (!rst_n) en |=> count == $past(count) + 1);",
        auto_cover=False,
    )
    assert _depth(fixed, "a_f") == 1
    ov = lower("a_o: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##[1:3] gnt);", auto_cover=False)
    assert _depth(ov, "a_o") == 3


def test_reset_assumption_emitted_and_checks_start_after_reset() -> None:
    low = lower("a_x: assert property (@(posedge clk) a |-> b);", reset="rst_n", reset_active_low=True)
    assert "initial assume(!rst_n);" in low.verilog
    assert low.reset == "rst_n"
    assert _depth(low, "a_x") == 1  # pre-reset garbage at step 0 is never checked
    high = lower("a_x: assert property (@(posedge clk) a |-> b);", reset="rst", reset_active_low=False)
    assert "initial assume(rst);" in high.verilog
    multi = lower("a_x: assert property (@(posedge clk) a |-> b);", reset="rst_n", reset_cycles=3)
    assert "if (f_fsyn_cycles < 8'd3) assume(!rst_n);" in multi.verilog
    assert _depth(multi, "a_x") == 3
    none = lower("a_x: assert property (@(posedge clk) a |-> b);")
    assert "assume(" not in none.verilog
    assert _depth(none, "a_x") == 0


def test_reset_is_disable_fallback_for_bare_properties() -> None:
    low = lower("a_x: assert property (a |=> b);", reset="rst", reset_active_low=False, auto_cover=False)
    assert "$past(!(rst), 1)" in low.verilog or "$past(!rst, 1)" in low.verilog


def test_strict_rejects_assume_and_verbatim() -> None:
    with pytest.raises(LowerError, match="assume property is not allowed"):
        lower("m_env: assume property (@(posedge clk) disable iff (!rst_n) !en);\n" + SVA, strict=True)
    verbatim = "// fsyn:verbatim\ninitial assume(0);\n// fsyn:endverbatim\n" + SVA
    with pytest.raises(LowerError, match="verbatim"):
        lower(verbatim, strict=True)
    assert lower(verbatim).verbatim_blocks  # trusted mode still accepts it


def test_auto_cover_per_assert_antecedent() -> None:
    result = lower(SVA)
    assert "a_demo_inc__cov" in result.names
    cov = next(a for a in result.assertions if a.name == "a_demo_inc__cov")
    assert cov.kind == "cover"
    assert "a_demo_inc__cov: cover(" in result.verilog
    assert "a_demo_inc__cov" not in lower(SVA, auto_cover=False).names
    # invariants have no antecedent -> no auto cover
    inv = lower("a_inv: assert property (@(posedge clk) disable iff (!rst_n) count <= 4'hF);")
    assert inv.names == ("a_inv",)


def test_skipped_items_are_reported_not_hidden() -> None:
    text = SVA + "\na_ev: assert property (@(posedge clk) disable iff (!rst_n) en |=> eventually done);\n"
    result = lower(text)
    assert result.proves_something
    assert len(result.skipped) == 1
    assert "a_ev" in result.skipped[0]
    covers_only = lower("c_en: cover property (@(posedge clk) disable iff (!rst_n) en);")
    assert not covers_only.proves_something


def test_base_label_strips_lowering_suffixes() -> None:
    assert base_label("a_x__c1") == "a_x"
    assert base_label("a_x__cov") == "a_x"
    assert base_label("counter.a_x__c2") == "a_x"
    assert base_label("a_x") == "a_x"


def test_default_disable_iff_applies_to_clocked_properties() -> None:
    text = """
default disable iff (!rst_n);
a_x: assert property (@(posedge clk) a |=> b);
"""
    result = lower(text, auto_cover=False)
    assert "$past(rst_n, 1)" in result.verilog


def test_default_clocking_sets_clock() -> None:
    text = """
default clocking cb @(posedge sys_clk); endclocking
a_x: assert property (a |=> b);
"""
    result = lower(text, auto_cover=False)
    assert result.clock == "sys_clk"


def test_semicolon_inside_error_string() -> None:
    text = SVA.replace('$error("inc")', '$error("inc; count=%0d", count)')
    result = lower(text)
    assert "a_demo_inc" in result.names
    assert "c_demo_en" in result.names


def test_bare_identifier_is_an_invariant() -> None:
    result = lower("a_busy: assert property (@(posedge clk) disable iff (!rst_n) idle);", auto_cover=False)
    assert "a_busy: assert((idle))" in result.verilog.replace("  ", " ")


def test_sva_boolean_keywords_become_verilog_operators() -> None:
    text = (
        "a_hold: assert property (@(posedge clk) disable iff (!rst_n) "
        "(up and down) or (not up and not down) |=> q == $past(q));"
    )
    result = lower(text, auto_cover=False)
    checks = "\n".join(ln for ln in result.verilog.splitlines() if not ln.strip().startswith("//"))
    assert " or " not in checks and " and " not in checks
    assert "(up && down) || (! up && ! down)" in checks
    with pytest.raises(LowerError, match="## delays"):
        lower("a_x: assert property (@(posedge clk) (a ##1 b) or c |-> d);", auto_cover=False)


def test_conjunction_of_implications_lowers_to_one_check_per_conjunct() -> None:
    from formalsynapse.sva_lower import base_label

    text = """
property p_grant_follows_req;
    @(posedge clk) disable iff (!rst_n)
    (grant[1] |-> $past(req[1])) &&
    (grant[0] |-> $past(req[0]));
endproperty
a_grant_follows_req: assert property (p_grant_follows_req);
property p_legal;
    @(posedge clk) disable iff (!rst_n)
    (state == 2'd0) |=> (state == 2'd1) and
    (state == 2'd1) |=> (state == 2'd0);
endproperty
a_legal: assert property (p_legal);
"""
    result = lower(text)
    asserts = [a.name for a in result.assertions if a.kind == "assert"]
    assert asserts == ["a_grant_follows_req__k1", "a_grant_follows_req__k2", "a_legal__k1", "a_legal__k2"]
    covers = [a.name for a in result.assertions if a.kind == "cover"]
    assert "a_grant_follows_req__k2__cov" in covers
    assert {base_label(n) for n in result.names} == {"a_grant_follows_req", "a_legal"}
    assert not result.skipped
    checks = "\n".join(ln for ln in result.verilog.splitlines() if not ln.strip().startswith("//"))
    assert "a_grant_follows_req__k2: assert" in checks and "$past(req[0])" in checks
    with pytest.raises(LowerError, match="mix implications"):
        lower("a_x: assert property (@(posedge clk) (a |-> b) && c);", auto_cover=False)


def test_reset_state_via_rose_is_accepted_and_bare_reset_is_not() -> None:
    ok = lower("a_r: assert property (@(posedge clk) disable iff (!rst_n) $rose(rst_n) |-> q == 0);", reset="rst_n")
    assert "a_r" in ok.names
    with pytest.raises(LowerError, match=r"\$rose\(rst_n\)"):
        lower("a_r: assert property (@(posedge clk) disable iff (!rst_n) !rst_n |-> q == 0);", reset="rst_n")


def test_sequence_keyword_inside_string_is_fine() -> None:
    text = SVA.replace('$error("inc")', '$error("sequence broke")')
    assert "a_demo_inc" in lower(text).names


def test_strip_comments_respects_strings() -> None:
    assert strip_comments('x = "a // b"; // c') == 'x = "a // b"; '
    assert strip_comments("a /* b */ c") == "a   c"
    blank = blank_comments('q = "abc"; // zz', strings=True)
    assert len(blank) == len('q = "abc"; // zz')
    assert blank.startswith('q = "   ";')
