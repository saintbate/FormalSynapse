from __future__ import annotations

from formalsynapse.mutate import generate_mutants

COUNTER = """\
module counter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    output logic [3:0] count
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count <= 4'd0;
        end else if (en) begin
            count <= count + 4'd1;
        end
    end
`ifdef FORMAL
    always @(posedge clk) begin
        a_dummy: assert (count == count);
    end
`endif
endmodule
"""

ARITH = """\
module alu (
    input  logic clk,
    input  logic rst_n,
    input  logic a,
    input  logic b,
    output logic y
);
    always_comb begin
        if (a && b)
            y = a == b;
        else
            y = a || b;
    end
endmodule
"""


def test_counter_mutates_add_and_if_not_formal() -> None:
    mutants = generate_mutants(COUNTER, max_mutants=8)
    ops = {m.operator for m in mutants}
    assert "add_sub" in ops
    assert "if_not" in ops
    add = next(m for m in mutants if m.operator == "add_sub")
    assert "count - 4'd1" in add.rtl
    assert "`ifdef FORMAL" not in add.rtl
    assert not any("count >= " in m.rtl for m in mutants)


def test_skips_reset_line() -> None:
    mutants = generate_mutants(COUNTER, max_mutants=8)
    for mutant in mutants:
        assert "if (rst_n)" not in mutant.rtl
        assert "if (!!rst_n)" not in mutant.rtl


def test_logic_and_eq_ops() -> None:
    mutants = generate_mutants(ARITH, max_mutants=8)
    ops = {m.operator for m in mutants}
    assert "and_or" in ops
    assert "eq_ne" in ops
    assert "or_and" in ops
    assert any("a || b" in m.rtl and m.operator == "and_or" for m in mutants)


def test_max_mutants_cap() -> None:
    mutants = generate_mutants(ARITH, max_mutants=2)
    assert len(mutants) == 2
    assert generate_mutants(ARITH, max_mutants=0) == ()


def test_skips_shifts_case_nba_and_array_bounds() -> None:
    rtl = """\
module gray (
    input  logic clk,
    input  logic rst_n,
    input  logic start,
    output logic [3:0] gray
);
    localparam DEPTH = 8;
    logic [3:0] bin;
    logic [7:0] mem [0:DEPTH-1];
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) bin <= 4'd0;
        else unique case (1'b1)
            start: bin <= bin + 4'd1;
            default: bin <= bin;
        endcase
    end
    assign gray = bin ^ (bin >> 1);
endmodule
"""
    mutants = generate_mutants(rtl, max_mutants=16)
    texts = [m.rtl for m in mutants]
    assert not any("bin > < 1" in t or "bin < < 1" in t for t in texts)
    assert not any("bin >= " in t for t in texts)
    assert not any("DEPTH+1" in t for t in texts)
    assert any(m.operator == "add_sub" and "bin - 4'd1" in m.rtl for m in mutants)


def test_does_not_mutate_comments() -> None:
    rtl = COUNTER + "\n// count <= count + 4'd1 && en\n"
    mutants = generate_mutants(rtl, max_mutants=16)
    assert not any("&&" in m.description and "comment" in m.description.lower() for m in mutants)
    # The extra comment line is not a new add site (body already has one +).
    adds = [m for m in mutants if m.operator == "add_sub"]
    assert len(adds) == 1
