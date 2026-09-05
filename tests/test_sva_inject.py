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


def test_strip_and_reinject_idempotent() -> None:
    once = inject(DUT, "  logic f_a;\n", "demo")
    twice = inject_clean(once, "  logic f_b;\n", "demo")
    assert "f_a" not in twice
    assert "f_b" in twice
    assert twice.count("`ifdef FORMAL") == 1
    assert has_formal_block(once)
    assert "logic f_a;" not in strip_formal_blocks(once)
