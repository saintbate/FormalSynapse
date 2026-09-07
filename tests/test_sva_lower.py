from __future__ import annotations

import pytest

from formalsynapse.sva_lower import LowerError, history_depth, lower, parse_property_body, shift

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
