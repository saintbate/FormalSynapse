from __future__ import annotations

import pytest

from formalsynapse.sva_inject import (
    InjectError,
    has_formal_block,
    inject,
    inject_clean,
    module_names,
    strip_formal_blocks,
)

DUT = """\
module demo (
    input logic clk
);
    logic x;
endmodule
"""


def test_inject_wraps_ifdef() -> None:
    out = inject(DUT, "  logic f;\n", "demo")
    assert "`ifdef FORMAL" in out
    assert "logic f;" in out
    assert out.index("`ifdef FORMAL") < out.index("endmodule")
    assert module_names(out) == ["demo"]


def test_inject_missing_module() -> None:
    with pytest.raises(InjectError, match="not found"):
        inject(DUT, "logic f;", "other")


def test_inject_empty_block() -> None:
    with pytest.raises(InjectError, match="empty"):
        inject(DUT, "  // only comment\n", "demo")


def test_strip_keeps_else_branch_of_formal_region() -> None:
    dut = """\
module m (input clk);
`ifdef FORMAL
    logic f_only;
`else
    logic synth_only;
`endif
`ifdef FORMAL
    logic f_two;
`elsif SIM
    logic sim_only;
`else
    logic synth_two;
`endif
endmodule
"""
    out = strip_formal_blocks(dut)
    assert "f_only" not in out and "f_two" not in out
    assert "logic synth_only;" in out
    assert "`ifdef SIM" in out and "sim_only" in out and "synth_two" in out
    assert out.count("`ifdef") == 1 and out.count("`endif") == 1
    assert out.count("`else") == 1


def test_endmodule_inside_string_is_ignored() -> None:
    dut = 'module m (input clk);\n    initial $display("endmodule");\n    logic x;\nendmodule\n'
    out = inject(dut, "  logic f;\n", "m")
    assert out.index("logic f;") > out.index('$display("endmodule")')
    assert out.rstrip().endswith("endmodule")


def test_strip_and_reinject_idempotent() -> None:
    once = inject(DUT, "  logic f_a;\n", "demo")
    twice = inject_clean(once, "  logic f_b;\n", "demo")
    assert "f_a" not in twice
    assert "f_b" in twice
    assert twice.count("`ifdef FORMAL") == 1
    assert has_formal_block(once)
    assert "logic f_a;" not in strip_formal_blocks(once)
