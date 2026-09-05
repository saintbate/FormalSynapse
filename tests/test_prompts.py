from __future__ import annotations

from formalsynapse.prompts import SYSTEM_PROMPT, refinement_user, slot_repair_user, zero_shot_user


def test_zero_shot_strips_formal_and_includes_spec() -> None:
    rtl = "module t;\n`ifdef FORMAL\n  logic secret;\n`endif\nendmodule\n"
    user = zero_shot_user(module="t", spec="1. Do not overflow.", rtl=rtl, context="ports:\n  input clk 1")
    assert "secret" not in user
    assert "Do not overflow" in user
    assert "module t" in user
    assert "Design context" in user
    assert "input clk" in user
    assert "`ifdef FORMAL" in SYSTEM_PROMPT


def test_refinement_includes_status_and_trace() -> None:
    msg = refinement_user(previous_sva="property p; endproperty", status="FAIL", report="step 4")
    assert "FAIL" in msg
    assert "step 4" in msg
    assert "property p" in msg


def test_refinement_names_failed_labels_and_repeats() -> None:
    msg = refinement_user(
        previous_sva="a_counter_reset: assert property (p);",
        status="FAIL",
        report="step 1",
        failed_assertions=("a_counter_reset",),
        repeated=True,
    )
    assert "a_counter_reset" in msg
    assert "SAME failing labels" in msg
    assert "$past" in SYSTEM_PROMPT or "rst_n" in SYSTEM_PROMPT


def test_slot_repair_lists_kept_and_failed() -> None:
    msg = slot_repair_user(
        kept_sva="`ifdef FORMAL\na_ok: assert property (p_ok);\n`endif",
        previous_sva="a_bad: assert property (p_bad);",
        status="FAIL",
        report="step 2",
        failed_assertions=("a_bad",),
    )
    assert "a_ok" in msg
    assert "a_bad" in msg
    assert "FAILED LABELS to replace" in msg
