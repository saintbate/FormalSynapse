from __future__ import annotations

from pathlib import Path

from formalsynapse.design_context import dual_edge_clocks, extract_context
from formalsynapse.sva_inject import strip_formal_blocks


def test_extracts_ansi_ports_and_skips_formal() -> None:
    rtl = Path(__file__).resolve().parents[1] / "benchmarks" / "smoke" / "counter" / "counter.sv"
    ctx = extract_context(rtl.read_text(), "counter")
    names = {p.name for p in ctx.ports}
    assert names == {"clk", "rst_n", "en", "count"}
    assert ctx.clock == "clk"
    assert ctx.reset == "rst_n"
    assert all(p.name != "f_fsyn_cycles" for p in ctx.internals)
    text = ctx.render()
    assert "input clk" in text
    assert "output count" in text
    assert "Do not invent" in text


def test_extracts_localparam_and_internals() -> None:
    rtl = Path(__file__).resolve().parents[1] / "benchmarks" / "golden" / "onehot_fsm" / "onehot_fsm.sv"
    ctx = extract_context(strip_formal_blocks(rtl.read_text()), "onehot_fsm")
    assert "S_IDLE" in ctx.constants
    assert "S_DONE" in ctx.constants
    rendered = ctx.render()
    assert "start" in rendered
    assert "state" in rendered


def test_unknown_module_is_empty() -> None:
    ctx = extract_context("module a;\nendmodule\n", "missing")
    assert ctx.ports == ()
    assert ctx.top == "missing"


def test_active_high_reset_disable_iff() -> None:
    rtl = """
module vcnt (
    input clk,
    input rst,
    output q
);
endmodule
"""
    ctx = extract_context(rtl, "vcnt")
    assert ctx.clock == "clk"
    assert ctx.reset == "rst"
    assert not ctx.reset_active_low
    assert ctx.disable_iff_clause() == "disable iff (rst)"
    assert "active-high" in ctx.render()


def test_async_reset_is_not_dual_edge() -> None:
    rtl = """
module m(input clk, input rst_n, output q);
    always @(posedge clk or negedge rst_n) q <= 0;
endmodule
"""
    assert dual_edge_clocks(rtl) == ()


def test_both_edges_of_one_clock_are_detected() -> None:
    rtl = """
module m(input i_clk, input i_rst);
    always @(posedge i_clk or negedge i_rst) ;
    always @(negedge i_clk or negedge i_rst) ;
endmodule
"""
    assert dual_edge_clocks(rtl) == ("i_clk",)
