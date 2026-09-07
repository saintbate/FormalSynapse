from __future__ import annotations

from formalsynapse.mutate import generate_mutants, rtl_hunk

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


def test_loop_headers_are_never_mutated() -> None:
    """`i = i + 1` on a down-counting for loop never terminates; yosys unrolls it until OOM."""
    rtl = """
module shifter (input clk, input load, input [7:0] d, output reg [7:0] q);
    integer i;
    always @(posedge clk) begin
        if (load) q <= d;
        else begin
            for (i = 8 - 2; i >= 0; i = i - 1) begin
                q[i] <= q[i + 1];
            end
        end
    end
endmodule
"""
    mutants = generate_mutants(rtl, max_mutants=32)
    assert mutants, "the loop body and the if are still mutable"
    for m in mutants:
        assert "for (i = 8 - 2; i >= 0; i = i - 1)" in m.rtl, m.description


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


def test_protected_names_from_design_context_are_not_mutated() -> None:
    rtl = """\
module m (input clk, input clr, input en, output reg [3:0] q);
    always @(posedge clk or posedge clr) begin
        if (clr) q <= 4'd0;
        else if (en) q <= q + 4'd1;
    end
endmodule
"""
    unguarded = generate_mutants(rtl, max_mutants=16)
    assert any("if (!clr)" in m.rtl for m in unguarded)  # 'clr' is not a default reset name
    guarded = generate_mutants(rtl, max_mutants=16, protected=frozenset({"clk", "clr"}))
    assert guarded
    assert not any("if (!clr)" in m.rtl for m in guarded)


def test_nba_after_if_is_not_relational() -> None:
    rtl = """\
module m (input clk, input rst_n, input en, input [3:0] a, input [3:0] b, output reg [3:0] q, output y);
    always @(posedge clk) begin
        if (en) q <= a;
        else q <= b;
    end
    assign y = a <= b;
endmodule
"""
    mutants = generate_mutants(rtl, max_mutants=16)
    assert not any("q >= a" in m.rtl or "q >= b" in m.rtl for m in mutants)
    assert any("a >= b" in m.rtl for m in mutants)


def test_strings_are_not_mutated() -> None:
    rtl = """\
module m (input clk, input rst_n, input a, input b, output y);
    assign y = a && b;
    initial $display("a && b == c + d");
endmodule
"""
    mutants = generate_mutants(rtl, max_mutants=16)
    assert all('$display("a && b == c + d")' in m.rtl for m in mutants)
    assert any("a || b" in m.rtl for m in mutants)


def test_site_selection_is_deterministic_and_spread() -> None:
    a = generate_mutants(ARITH, max_mutants=8)
    b = generate_mutants(ARITH, max_mutants=8)
    assert [m.rtl for m in a] == [m.rtl for m in b]
    assert [m.name for m in a] == [f"{m.operator}_{i}" for i, m in enumerate(a)]


def test_const_flip_sized_and_bare_literals() -> None:
    mutants = generate_mutants(COUNTER, max_mutants=32)
    flips = [m for m in mutants if m.operator == "const_flip"]
    # reset value and increment are both flipped; each is a distinct single-site mutant
    assert any("count <= 4'd1;" in m.rtl and "count + 4'd1" in m.rtl for m in flips)
    assert any("count + 4'd0" in m.rtl and "count <= 4'd0;" in m.rtl for m in flips)
    rtl = """\
module m (input clk, input rst_n, input a, output logic [7:0] y, output logic z);
    always_ff @(posedge clk) begin
        y <= 8'hFF;
        z <= 0;
    end
    assign w = 1'bx;
endmodule
"""
    flips = [m for m in generate_mutants(rtl, max_mutants=32) if m.operator == "const_flip"]
    assert any("y <= 8'hFE;" in m.rtl for m in flips)
    assert any("z <= 1;" in m.rtl for m in flips)
    assert all("1'bx" in m.rtl for m in flips)  # x digits are left alone


def test_const_flip_skips_parameters_initials_and_selects() -> None:
    rtl = """\
module m #(parameter W = 4'd4) (input clk, input rst_n, input [3:0] v, output logic [3:0] q);
    localparam logic [3:0] ZERO = 4'd0;
    initial q = 4'd0;
    always_ff @(posedge clk) q <= v[4'd2] ? ZERO : q;
endmodule
"""
    assert not any(m.operator == "const_flip" for m in generate_mutants(rtl, max_mutants=32))


def test_case_swap_adjacent_arms_only() -> None:
    rtl = """\
module fsm (input clk, input rst_n, input go, output logic [1:0] st);
    localparam logic [1:0] A = 2'd0;
    localparam logic [1:0] B = 2'd1;
    localparam logic [1:0] C = 2'd2;
    always_ff @(posedge clk) begin
        case (st)
            A: st <= go ? B : A;
            B: st <= C;
            C: st <= A;
            default: st <= A;
        endcase
    end
endmodule
"""
    swaps = [m for m in generate_mutants(rtl, max_mutants=32) if m.operator == "case_swap"]
    assert len(swaps) == 2
    assert any("B: st <= go ? B : A;\n            A: st <= C;" in m.rtl for m in swaps)
    assert any("C: st <= C;\n            B: st <= A;" in m.rtl for m in swaps)
    assert all("default:" in m.rtl for m in swaps)


def test_relational_off_by_one() -> None:
    rtl = """\
module m (input clk, input rst_n, input [3:0] a, input [3:0] b, output y, output z);
    assign y = a < b;
    assign z = a >= b;
endmodule
"""
    mutants = generate_mutants(rtl, max_mutants=32)
    ops = {m.operator for m in mutants}
    assert {"lt_le", "lt_gt", "ge_gt", "ge_le"} <= ops
    assert any("a <= b" in m.rtl for m in mutants)
    assert any("a > b" in m.rtl for m in mutants)


def test_rtl_hunk_marks_golden_minus_and_mutant_plus() -> None:
    golden = "assign q_next = clear ? 4'd0 : qi - 4'd1;\n"
    mutant = "assign q_next = clear ? 4'd1 : qi - 4'd1;\n"
    hunk = rtl_hunk(golden, mutant)
    assert hunk.startswith("@@")
    assert "-assign q_next = clear ? 4'd0 : qi - 4'd1;" in hunk
    assert "+assign q_next = clear ? 4'd1 : qi - 4'd1;" in hunk
    assert rtl_hunk(golden, golden) == ""
