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


def test_parameter_header_and_multi_name_ports() -> None:
    rtl = """
module fifo #(parameter W = 8, parameter D = 4) (
    input  logic clk_i, rst_ni,
    input  logic [W-1:0] wdata, rdata_in,
    output logic full, empty
);
    logic [W-1:0] mem [0:D-1];
    logic [1:0] wptr, rptr;
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) wptr <= 0;
    end
endmodule
"""
    ctx = extract_context(rtl, "fifo")
    assert {p.name for p in ctx.ports} == {"clk_i", "rst_ni", "wdata", "rdata_in", "full", "empty"}
    assert {i.name for i in ctx.internals} >= {"mem", "wptr", "rptr"}
    assert ctx.clock == "clk_i"
    assert ctx.reset == "rst_ni"
    assert ctx.reset_active_low
    assert ctx.reset_active == "!rst_ni"


def test_clock_and_reset_from_edges_when_names_are_unusual() -> None:
    rtl = """
module m (input wire ck, input wire clr, input wire d, output reg q);
    always @(posedge ck or posedge clr) begin
        if (clr) q <= 1'b0;
        else q <= d;
    end
endmodule
"""
    ctx = extract_context(rtl, "m")
    assert ctx.clock == "ck"
    assert ctx.reset == "clr"
    assert not ctx.reset_active_low
    assert ctx.reset_active == "clr"


def test_sync_reset_polarity_from_usage() -> None:
    rtl = """
module m (input clk, input reset, input d, output reg q);
    always @(posedge clk) begin
        if (!reset) q <= 1'b0;
        else q <= d;
    end
endmodule
"""
    ctx = extract_context(rtl, "m")
    assert ctx.reset == "reset"
    assert ctx.reset_active_low  # name says active-high, usage says active-low; usage wins
    assert ctx.disable_iff_clause() == "disable iff (!reset)"


def test_no_reset_port_is_reported_not_invented() -> None:
    from formalsynapse.prompts import system_prompt

    rtl = """
module cipher (input clk_i, input [7:0] data_i, input load, output reg [7:0] data_o);
    always @(posedge clk_i) if (load) data_o <= data_i;
endmodule
"""
    ctx = extract_context(rtl, "cipher")
    assert ctx.clock == "clk_i"
    assert ctx.reset is None
    assert ctx.reset_active is None
    assert ctx.disable_iff_clause() == ""
    assert "rst_n" not in ctx.render()
    assert "no reset port" in ctx.render()
    prompt = system_prompt(clock=ctx.clock or "clk", reset=ctx.reset)
    assert "rst_n" not in prompt.replace("do not name rst_n", "")
    assert "NO reset port" in prompt
    assert "disable iff" not in prompt.split("Example shape")[1]


def test_non_ansi_ports() -> None:
    rtl = """
module uart_top (clock, reset, ser_in, ser_out, tx_busy);
    input clock, reset;
    input ser_in;
    output ser_out, tx_busy;
    wire ser_out;
    reg tx_busy;
    always @(posedge clock or posedge reset)
        if (reset) tx_busy <= 1'b0;
endmodule
"""
    ctx = extract_context(rtl, "uart_top")
    assert {p.name for p in ctx.ports} == {"clock", "reset", "ser_in", "ser_out", "tx_busy"}
    assert {p.name for p in ctx.ports if p.direction == "input"} == {"clock", "reset", "ser_in"}
    assert ctx.clock == "clock"
    assert ctx.reset == "reset"
    assert not ctx.reset_active_low
    assert "tx_busy" not in {i.name for i in ctx.internals}


def test_reset_name_variants() -> None:
    for name, low in (("aresetn", True), ("nrst", True), ("sys_rst", False), ("rst_n_i", True), ("i_rst_n", True)):
        rtl = f"module m (input clk, input {name}, output q);\nendmodule\n"
        ctx = extract_context(rtl, "m")
        assert ctx.reset == name, name
        assert ctx.reset_active_low is low, name


def test_both_edges_of_one_clock_are_detected() -> None:
    rtl = """
module m(input i_clk, input i_rst);
    always @(posedge i_clk or negedge i_rst) ;
    always @(negedge i_clk or negedge i_rst) ;
endmodule
"""
    assert dual_edge_clocks(rtl) == ("i_clk",)
