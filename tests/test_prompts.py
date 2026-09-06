from __future__ import annotations

from formalsynapse.mutate import MutantHunk
from formalsynapse.prompts import (
    SPEC_CHAR_BUDGET,
    SYSTEM_PROMPT,
    clip_prompt_text,
    cover_only_user,
    kill_miss_user,
    kill_unscored_user,
    refinement_user,
    slot_repair_user,
    system_prompt,
    zero_shot_user,
)


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


def test_clip_prompt_text_keeps_requirements() -> None:
    long_intro = "Intro filler. " * 200
    spec = (
        f"# vcnt\n\n{long_intro}\n\n"
        "## Interface\n\n| Signal | Dir |\n| clk | in |\n\n"
        "## Requirements\n\n1. On reset, qi is 0.\n2. When cke is low, qi holds.\n"
    )
    clipped = clip_prompt_text(spec, 400, label="specification")
    assert "truncated" in clipped
    assert "Requirements" in clipped
    assert len(clipped) < len(spec)


def test_zero_shot_clips_long_spec() -> None:
    spec = "## Requirements\n\n" + "\n".join(f"{i}. hold the previous value when idle" for i in range(400))
    user = zero_shot_user(module="vcnt", spec=spec, rtl="module vcnt;\nendmodule\n")
    assert "truncated" in user
    assert len(user) < len(spec) + 2000
    assert len(user) < SPEC_CHAR_BUDGET + 2500


def test_system_prompt_follows_reset_polarity() -> None:
    assert SYSTEM_PROMPT == system_prompt()
    high = system_prompt(clock="clk", reset="rst")
    assert "@(posedge clk) disable iff (rst)" in high
    assert "active-high" in high
    assert "disable iff (!rst_n)" not in high


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


def test_kill_miss_lists_survivors() -> None:
    msg = kill_miss_user(
        kept_sva="`ifdef FORMAL\na_clear: assert property (p_clear);\n`endif",
        killed=2,
        valid=8,
        survivors=(
            MutantHunk(
                "M_0003",
                "clear 0 -> 1",
                "@@\n- assign q_next_fw = clear ? 4'd0 : qi - 4'd1;\n"
                "+ assign q_next_fw = clear ? 4'd1 : qi - 4'd1;",
            ),
            MutantHunk("M_0001", "shift 1 -> 7", ""),
        ),
        min_kill=0.25,
    )
    assert "2/8" in msg
    assert "25%" in msg
    assert "clear 0 -> 1" in msg
    assert "4'd0" in msg
    assert "assert the golden" in msg.lower()
    assert "not `== 1`" in msg or "not == 1" in msg
    assert "do not copy" in msg.lower()


def test_kill_unscored_asks_for_literals() -> None:
    msg = kill_unscored_user(
        kept_sva="`ifdef FORMAL\na_dec: assert property (p_dec);\n`endif",
        attempted=8,
    )
    assert "8 mutants ERROR" in msg
    assert "4'd1" in msg
    assert "CNT_LENGTH" in msg


def test_cover_only_asks_for_asserts() -> None:
    msg = cover_only_user(kept_sva="`ifdef FORMAL\nc_en: cover property (en);\n`endif")
    assert "c_en" in msg
    assert "no labeled assert" in msg
    assert "assert" in msg.lower()
