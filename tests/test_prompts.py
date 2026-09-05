from __future__ import annotations

from formalsynapse.prompts import SYSTEM_PROMPT, refinement_user, zero_shot_user


def test_zero_shot_strips_formal_and_includes_spec() -> None:
    rtl = "module t;\n`ifdef FORMAL\n  logic secret;\n`endif\nendmodule\n"
    user = zero_shot_user(module="t", spec="1. Do not overflow.", rtl=rtl)
    assert "secret" not in user
    assert "Do not overflow" in user
    assert "module t" in user
    assert "`ifdef FORMAL" in SYSTEM_PROMPT


def test_refinement_includes_status_and_trace() -> None:
    msg = refinement_user(previous_sva="property p; endproperty", status="FAIL", report="step 4")
    assert "FAIL" in msg
    assert "step 4" in msg
    assert "property p" in msg
