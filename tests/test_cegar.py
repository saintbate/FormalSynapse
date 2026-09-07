from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.cegar import Attempt, SuiteReport, Trajectory, run_block
from formalsynapse.dataset import append_jsonl, log_suite
from formalsynapse.generator import Message
from formalsynapse.prompts import SYSTEM_PROMPT
from formalsynapse.verify_harness import VerifyResult

BAD = """\
`ifdef FORMAL
property p_t_bad;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count);
endproperty
a_t_bad: assert property (p_t_bad);
`endif
"""

GOOD = """\
`ifdef FORMAL
property p_t_good;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 4'd1;
endproperty
a_t_good: assert property (p_t_good);
`endif
"""


class Scripted:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.seen: list[list[Message]] = []

    def generate(self, messages: list[Message]) -> str:
        self.seen.append(list(messages))
        return self.texts.pop(0)


def _result(status: str, report: str = "") -> VerifyResult:
    return VerifyResult(
        status=status,  # type: ignore[arg-type]
        exit_code=0 if status == "PASS" else 2,
        run_dir=Path("."),
        sby_log_path=None,
        trace_vcd_path=None,
        failing_step=4 if status == "FAIL" else None,
        failed_assertions=("a_t_bad",) if status == "FAIL" else (),
        errors=(),
        report=report or status,
        depth=8,
        mode="bmc",
        elapsed_s=0.1,
    )


def test_healed_trajectory_logs_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    statuses = ["FAIL", "PASS"]

    def fake_verify(*_a: object, **_k: object) -> VerifyResult:
        return _result(statuses.pop(0), "Failed assertion a_t_bad at step 4")

    monkeypatch.setattr(cegar_mod, "verify", fake_verify)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    gen = Scripted([BAD, GOOD])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        depth=8,
        timeout_s=10.0,
        max_feedback=3,
    )
    assert traj.healed
    assert traj.turns == 2
    assert not traj.first_pass
    rows = traj.dataset_rows()
    assert len(rows) == 1
    assert "a_t_bad" in str(rows[0]["counterexample"])
    assert "p_t_good" in str(rows[0]["fixed_attempt"])
    # `module t;` has no reset port, so the prompt must not invent rst_n
    assert gen.seen[0][0].content != SYSTEM_PROMPT
    assert "NO reset port" in gen.seen[0][0].content
    assert "increment" in gen.seen[0][1].content
    assert "Failed assertion" in gen.seen[1][3].content
    assert "a_t_bad" in gen.seen[1][3].content
    # single-property fail strips everything, so we fall back to a full rewrite prompt
    assert "FAILED LABELS" in gen.seen[1][3].content or "FAILED LABELS to replace" in gen.seen[1][3].content


def test_first_pass_has_no_dataset_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("ok\n")
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=Scripted([GOOD]),
        workdir=tmp_path,
        max_feedback=3,
    )
    assert traj.first_pass
    assert traj.dataset_rows() == []


def test_best_of_n_picks_passing_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    statuses = ["FAIL", "PASS"]

    def fake_verify(*_a: object, **_k: object) -> VerifyResult:
        return _result(statuses.pop(0))

    monkeypatch.setattr(cegar_mod, "verify", fake_verify)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text(
        "module t (input logic clk, input logic rst_n, input logic en, "
        "output logic [3:0] count);\nendmodule\n"
    )
    spec.write_text("1. increment\n")
    gen = Scripted([BAD, GOOD])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=0,
        candidates=2,
    )
    assert traj.first_pass
    assert traj.turns == 1
    assert len(gen.seen) == 2


def test_slot_repair_keeps_survivors_out_of_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    mixed = """\
`ifdef FORMAL
property p_keep;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 1;
endproperty
a_keep: assert property (p_keep);
property p_bad;
    @(posedge clk) disable iff (!rst_n)
    rst_n |-> count == 0;
endproperty
a_bad: assert property (p_bad);
`endif
"""

    def fake_verify_pos(*_a: object, **_k: object) -> VerifyResult:
        sva = str(_a[1]) if len(_a) > 1 else ""
        if "a_t_bad" in sva or ("a_bad" in sva and "GOOD_SLOT" not in sva):
            return VerifyResult(
                status="FAIL",
                exit_code=2,
                run_dir=tmp_path,
                sby_log_path=None,
                trace_vcd_path=None,
                failing_step=2,
                failed_assertions=("a_bad",),
                errors=(),
                report="Failed a_bad",
                depth=8,
                mode="bmc",
                elapsed_s=0.1,
            )
        return _result("PASS")

    monkeypatch.setattr(cegar_mod, "verify", fake_verify_pos)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    slot = """\
`ifdef FORMAL
property p_fixed;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 1;
endproperty
a_fixed: assert property (p_fixed);
GOOD_SLOT
`endif
"""
    gen = Scripted([mixed, slot])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=1,
        candidates=1,
    )
    assert traj.healed
    repair = gen.seen[1][3].content
    assert "do not copy" in repair.lower() or "do not repeat" in repair.lower()
    assert "a_keep" in repair
    assert "FAILED LABELS to replace" in repair


def test_extract_error_continues_cegar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    gen = Scripted(["this is not SVA", GOOD])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=1,
        candidates=1,
    )
    assert traj.turns == 2
    assert traj.attempts[0].result.status == "ERROR"
    assert traj.ok
    assert "no labeled assert" in gen.seen[1][2].content


def test_min_kill_zero_skips_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("prove-only CEGAR must not score kill")

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    monkeypatch.setattr(cegar_mod, "score_kill", boom)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=Scripted([GOOD]),
        workdir=tmp_path,
        max_feedback=1,
        min_kill=0.0,
    )
    assert traj.turns == 1
    assert traj.first_pass
    assert traj.attempts[0].valid_mutants == 0


def test_shallow_pass_continues_for_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod
    from formalsynapse.gate import KillReport, MutantOutcome
    from formalsynapse.mutate import Mutant

    dummy = Mutant("m0", "eq_ne", "swap clear-to-zero", "module x; endmodule")
    scores = [
        KillReport((MutantOutcome(dummy, _result("PASS")), MutantOutcome(dummy, _result("PASS")))),
        KillReport((MutantOutcome(dummy, _result("FAIL")), MutantOutcome(dummy, _result("FAIL")))),
    ]

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    monkeypatch.setattr(cegar_mod, "score_kill", lambda *_a, **_k: scores.pop(0))
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    more = GOOD.replace("p_t_good", "p_t_more").replace("a_t_good", "a_t_more")
    gen = Scripted([GOOD, more])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=1,
        min_kill=0.5,
        candidates=1,
    )
    assert traj.turns == 2
    assert traj.attempts[0].killed == 0
    assert traj.attempts[1].killed == 2
    assert traj.attempts[1].meets_kill(0.5)
    repair = gen.seen[1][-1].content
    assert "assert the golden" in repair.lower()
    assert "swap clear-to-zero" in repair
    assert "```diff" in repair


SKIPPY = GOOD.replace(
    "`endif",
    "property p_t_chain;\n    @(posedge clk) disable iff (!rst_n)\n"
    "    a |=> b[*2];\nendproperty\na_t_chain: assert property (p_t_chain);\n`endif",
)


def test_skipped_statements_are_fed_back_and_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PASS whose block has unproven (skipped) statements is not a finished turn."""
    from dataclasses import replace as dc_replace

    from formalsynapse import cegar as cegar_mod
    from formalsynapse.sva_lower import lower

    def fake_verify(_dut: Path, sva: str, *_a: object, **_k: object) -> VerifyResult:
        low = lower(sva, fallback_disable="!rst_n")
        return dc_replace(_result("PASS"), lowered=low)

    monkeypatch.setattr(cegar_mod, "verify", fake_verify)
    monkeypatch.setattr(cegar_mod, "score_kill", lambda *_a, **_k: pytest.fail("min_kill=0: no scoring"))
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    fixed = GOOD.replace("p_t_good", "p_t_fix").replace("a_t_good", "a_t_fix")
    gen = Scripted([SKIPPY, fixed])
    traj = run_block(dut_path=dut, spec_path=spec, top="t", generator=gen, workdir=tmp_path, max_feedback=2)
    assert traj.turns == 2, [a.result.status for a in traj.attempts]
    first, second = traj.attempts
    assert first.proven and first.skipped and not first.complete
    assert second.complete
    assert traj.winner is second
    repair = gen.seen[1][-1].content
    assert "could NOT check" in repair and "p_t_chain" in repair
    assert "p_t_chain" not in repair.split("## Kept")[1], "skipped property must be stripped from kept"
    # The second block is kept survivors + the fix, without the skipped chain property.
    assert "p_t_good" in second.sva and "p_t_fix" in second.sva and "p_t_chain" not in second.sva


TRUNCATED = GOOD.replace(
    "`endif",
    "// After a rising edge, pulse must drop.\nproperty p_t_tail;\n    @(posedge clk) disable iff (!rst_n)\n"
    "    pulse |=> (din && !pulse); // Actually we don't need\n`endif",
)


def test_truncated_property_is_skipped_not_silently_dropped() -> None:
    from formalsynapse.sva_edit import strip_truncated
    from formalsynapse.sva_lower import lower

    low = lower(TRUNCATED, auto_cover=False)
    assert low.names == ("a_t_good",)
    assert low.skipped == ("property 'p_t_tail': truncated (no endproperty)",)
    cut = strip_truncated(TRUNCATED)
    assert "p_t_tail" not in cut and "a_t_good" in cut
    assert lower(cut, auto_cover=False).skipped == ()
    half_stmt = GOOD.replace("`endif", 'a_t_half: assert property (p_t_good) else $error("x\n`endif')
    low2 = lower(half_stmt, auto_cover=False)
    assert low2.names == ("a_t_good",) and any("a_t_half" in s and "truncated" in s for s in low2.skipped)
    assert "a_t_half" not in strip_truncated(half_stmt)


def test_canonical_sva_drops_reasoning_comments_but_keeps_checks() -> None:
    import importlib.util

    from formalsynapse.sva_lower import lower

    script = Path(__file__).resolve().parents[1] / "scripts" / "distill" / "build_sft.py"
    spec = importlib.util.spec_from_file_location("build_sft", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    noisy = GOOD.replace(
        "property p_t_good;",
        "// Property 1: count increments\n// We reason at length here about why this\n"
        "// is the right formulation and never stop.\nproperty p_t_good;",
    ).replace(
        "en |=> count == $past(count) + 4'd1;",
        'en |=> count == $past(count) + 4\'d1; // trailing musing "with // inside"',
    ).replace("`endif", "// dangling thought that is cut off mid-sen\n`endif")
    clean = mod.canonical_sva(noisy)
    assert "reason at length" not in clean and "trailing musing" not in clean and "dangling" not in clean
    assert "// Property 1: count increments" in clean
    assert lower(clean, auto_cover=False).names == lower(noisy, auto_cover=False).names == ("a_t_good",)
    assert mod._canonical_or_raw(noisy) == (clean, True)


def test_dut_smoke_failure_makes_no_llm_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    calls: list[str] = []

    def fake_verify(_dut: Path, sva: str, *_a: object, **kw: object) -> VerifyResult:
        calls.append(str(kw.get("run_name")))
        assert "a_fsyn_smoke" in sva
        return _result("ERROR", report="Can not open file `` for $readmemh.")

    monkeypatch.setattr(cegar_mod, "verify", fake_verify)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t(input clk);\nendmodule\n")
    spec.write_text("1. x\n")
    gen = Scripted([])  # any generate() call would IndexError
    traj = run_block(dut_path=dut, spec_path=spec, top="t", generator=gen, workdir=tmp_path, max_feedback=3)
    assert calls == ["cegar-t-smoke"] and gen.seen == []
    assert traj.status == "ERROR" and traj.turns == 1 and traj.attempts[0].turn == 0
    assert traj.attempts[0].result.report.startswith("DUT smoke failed")
    assert traj.winner is not None and not traj.ok


def test_fit_context_compacts_history_then_shrinks_rtl(monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse.cegar import _estimate_tokens, _fit_context
    from formalsynapse.prompts import zero_shot_user

    monkeypatch.setenv("FSYN_LLM_CONTEXT", "8192")
    rtl = "module big(input clk);\n" + "".join(f"  reg [7:0] r{i}; // reg {i}\n" for i in range(600)) + "endmodule\n"
    spec = "1. something\n" * 50
    user0 = zero_shot_user(module="big", spec=spec, rtl=rtl, context="")
    rambling = GOOD.replace("`endif", "// " + "reasoning " * 2500 + "\n`endif")
    repair = "The previous assertion block did not formally verify.\n" + "x" * 1500
    msgs = [Message("system", "sys"), Message("user", user0), Message("assistant", rambling), Message("user", repair)]
    assert _estimate_tokens(msgs) > 8192 - 2048
    fitted = _fit_context(msgs, max_tokens=2048, module="big", spec=spec, rtl=rtl, context="")
    assert _estimate_tokens(fitted) <= 8192 - 2048 - 384
    assert fitted[-1].content == repair, "the repair message is never cut"
    assistant = [m for m in fitted if m.role == "assistant"]
    assert all("reasoning" not in m.content and "p_t_good" in m.content for m in assistant)
    # With a small context the history pair goes and the RTL shrinks, but the repair survives.
    monkeypatch.setenv("FSYN_LLM_CONTEXT", "3000")
    tiny = _fit_context(msgs, max_tokens=1024, module="big", spec=spec, rtl=rtl, context="")
    assert [m.role for m in tiny] == ["system", "user", "user"]
    assert tiny[-1].content == repair and "truncated for the 8k context" in tiny[1].content


def test_drop_duplicates_keeps_proven_copy() -> None:
    from formalsynapse.sva_edit import drop_duplicates

    addition = GOOD.replace("`endif", "property p_new;\n  @(posedge clk) a |=> b;\nendproperty\n"
                            "a_new: assert property (p_new);\n`endif")
    edited, dropped = drop_duplicates(GOOD, addition)
    assert set(dropped) == {"a_t_good", "p_t_good"}
    assert "p_t_good" not in edited and "a_t_good" not in edited
    assert "p_new" in edited and "a_new: assert property (p_new)" in edited
    untouched, none = drop_duplicates("", addition)
    assert untouched == addition and none == ()


def test_zero_valid_mutants_does_not_spin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod
    from formalsynapse.gate import KillReport

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    monkeypatch.setattr(cegar_mod, "score_kill", lambda *_a, **_k: KillReport(()))
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=Scripted([GOOD]),
        workdir=tmp_path,
        max_feedback=2,
        min_kill=0.5,
    )
    assert traj.turns == 1
    assert traj.ok
    assert traj.attempts[0].meets_kill(0.5)


def test_all_error_mutants_do_not_meet_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod
    from formalsynapse.gate import KillReport, MutantOutcome
    from formalsynapse.mutate import Mutant

    dummy = Mutant("m0", "eq_ne", "swap", "module x; endmodule")
    errors = KillReport((MutantOutcome(dummy, _result("ERROR")), MutantOutcome(dummy, _result("ERROR"))))
    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    monkeypatch.setattr(cegar_mod, "score_kill", lambda *_a, **_k: errors)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    gen = Scripted([GOOD, GOOD])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=1,
        min_kill=0.5,
        candidates=1,
    )
    assert traj.turns == 2
    assert traj.attempts[0].attempted_mutants == 2
    assert traj.attempts[0].valid_mutants == 0
    assert not traj.attempts[0].meets_kill(0.5)
    assert "mutants ERROR" in gen.seen[1][-1].content


COVERS = """\
`ifdef FORMAL
c_t_en: cover property (@(posedge clk) disable iff (!rst_n) en);
`endif
"""


def test_cover_only_pass_continues_without_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from formalsynapse import cegar as cegar_mod
    from formalsynapse.gate import KillReport, MutantOutcome
    from formalsynapse.mutate import Mutant

    scored = {"n": 0}
    dummy = Mutant("m0", "eq_ne", "swap", "module x; endmodule")

    def fake_kill(*_a: object, **_k: object) -> KillReport:
        scored["n"] += 1
        return KillReport((MutantOutcome(dummy, _result("FAIL")), MutantOutcome(dummy, _result("FAIL"))))

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("PASS"))
    monkeypatch.setattr(cegar_mod, "score_kill", fake_kill)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    gen = Scripted([COVERS, GOOD])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=1,
        min_kill=0.5,
        candidates=1,
    )
    assert traj.turns == 2
    assert not traj.attempts[0].proven
    assert scored["n"] == 1
    assert traj.winner is not None
    assert traj.winner.proven
    assert "a_t_good" in traj.winner.sva
    assert "covers and no labeled assert" in gen.seen[1][-1].content


def test_later_fail_keeps_earlier_prove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod
    from formalsynapse.gate import KillReport, MutantOutcome
    from formalsynapse.mutate import Mutant

    dummy = Mutant("m0", "eq_ne", "swap clear-to-zero", "module x; endmodule")
    kill = KillReport((MutantOutcome(dummy, _result("FAIL")), MutantOutcome(dummy, _result("PASS"))))
    statuses = ["PASS", "FAIL"]

    def fake_verify(*_a: object, **_k: object) -> VerifyResult:
        return _result(statuses.pop(0) if statuses else "FAIL")

    monkeypatch.setattr(cegar_mod, "verify", fake_verify)
    monkeypatch.setattr(cegar_mod, "score_kill", lambda *_a, **_k: kill)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=Scripted([GOOD, BAD]),
        workdir=tmp_path,
        max_feedback=1,
        min_kill=0.75,
        candidates=1,
    )
    assert traj.turns == 2
    assert traj.ok
    assert traj.winner is not None
    assert traj.winner.turn == 1
    assert traj.winner.sva == GOOD
    assert traj.status == "PASS"


VACUOUS = """\
`ifdef FORMAL
property p_t_vac;
    @(posedge clk) disable iff (!rst_n)
    (en && !en) |=> count == 0;
endproperty
a_t_vac: assert property (p_t_vac);
property p_t_good;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 4'd1;
endproperty
a_t_good: assert property (p_t_good);
`endif
"""

REPLACEMENT = """\
`ifdef FORMAL
property p_t_hold;
    @(posedge clk) disable iff (!rst_n)
    !en |=> count == $past(count);
endproperty
a_t_hold: assert property (p_t_hold);
`endif
"""


def test_vacuous_pass_is_stripped_and_not_a_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BMC PASS + unreachable antecedent: the vacuous label is deleted in Python, the model is
    asked for a replacement, and a vacuous attempt never counts as proven."""
    from formalsynapse import cegar as cegar_mod
    from formalsynapse.sva_lower import lower

    calls: list[str] = []

    def fake_verify(_dut: Path, sva: str, _top: str, **kw: object) -> VerifyResult:
        mode = str(kw.get("mode", "bmc"))
        calls.append(mode)
        assert kw.get("strict") is True
        lowered = lower(sva, reset="rst_n")
        base = _result("PASS")
        if mode == "cover":
            unreached = ("a_t_vac__cov",) if "a_t_vac" in sva else ()
            return VerifyResult(
                **{
                    **base.__dict__,
                    "status": "FAIL" if unreached else "PASS",
                    "mode": "cover",
                    "lowered": lowered,
                    "unreached_covers": unreached,
                }
            )
        return VerifyResult(**{**base.__dict__, "lowered": lowered})

    monkeypatch.setattr(cegar_mod, "verify", fake_verify)
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    gen = Scripted([VACUOUS, REPLACEMENT])
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top="t",
        generator=gen,
        workdir=tmp_path,
        max_feedback=1,
        candidates=1,
    )
    assert calls == ["bmc", "cover", "bmc", "cover"]
    assert "a_t_good" in traj.attempts[1].sva and "a_t_hold" in traj.attempts[1].sva  # kept + new
    first = traj.attempts[0]
    assert first.result.ok and not first.proven
    assert first.vacuous == ("a_t_vac",)
    assert not traj.first_pass
    repair = gen.seen[1][-1].content
    assert "vacuous" in repair and "a_t_vac" in repair
    assert "p_t_vac" not in repair.split("## Kept")[1]  # stripped from the kept block
    assert traj.winner is not None and traj.winner.turn == 2
    assert traj.ok and traj.healed


def test_history_is_trimmed_to_last_exchange(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import cegar as cegar_mod

    monkeypatch.setattr(cegar_mod, "verify", lambda *_a, **_k: _result("FAIL", "Failed assertion a_t_bad"))
    dut = tmp_path / "t.sv"
    spec = tmp_path / "t.spec.md"
    dut.write_text("module t;\nendmodule\n")
    spec.write_text("1. increment\n")
    gen = Scripted([BAD, BAD, BAD, BAD])
    run_block(dut_path=dut, spec_path=spec, top="t", generator=gen, workdir=tmp_path, max_feedback=3)
    assert len(gen.seen) == 4
    for i, msgs in enumerate(gen.seen):
        assert msgs[0].role == "system" and msgs[1].role == "user"
        assert len(msgs) == (2 if i == 0 else 4)  # system, user0, last assistant, last repair


def test_suite_metrics() -> None:
    def traj(block: str, first: str, final: str) -> Trajectory:
        a0 = Attempt(1, BAD, _result(first))
        atts = [a0]
        if first != final:
            atts.append(Attempt(2, GOOD, _result(final)))
        return Trajectory(block, block, "p", tuple(atts), 1.0)

    report = SuiteReport(
        trajectories=[
            traj("a", "PASS", "PASS"),
            traj("b", "FAIL", "PASS"),
            traj("c", "FAIL", "FAIL"),
            traj("d", "ERROR", "ERROR"),
        ]
    )
    assert report.first_pass_rate == 0.25
    assert report.cegar_rate == 0.5
    assert report.heal_rate == 1 / 3
    text = report.render()
    assert "first-pass" in text


def test_sft_row_only_for_proven_winner_and_builder_filters(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from formalsynapse.dataset import log_trajectory, sft_row

    lost = Trajectory("c", "c", "p", (Attempt(1, BAD, _result("FAIL")),), 1.0, system="sys")
    assert sft_row(lost) is None
    won = Trajectory(
        "b",
        "b",
        "user prompt",
        (Attempt(1, BAD, _result("FAIL")), Attempt(2, GOOD, _result("PASS"), killed=3, valid_mutants=4)),
        1.0,
        system="sys prompt",
    )
    row = sft_row(won)
    assert row is not None
    assert row["system"] == "sys prompt" and row["prompt"] == "user prompt" and row["sva"] == GOOD
    assert row["kill_rate"] == 0.75 and row["turn"] == 2 and row["first_pass"] is False
    weak = Trajectory("w", "w", "p", (Attempt(1, GOOD, _result("PASS"), killed=0, valid_mutants=5),), 1.0)
    log = tmp_path / "gen.jsonl"
    for t in (lost, won, weak):
        log_trajectory(log, t)
    kinds = [json.loads(ln)["type"] for ln in log.read_text().splitlines()]
    assert kinds.count("sft") == 2 and kinds.count("trajectory") == 3

    out = tmp_path / "train.jsonl"
    script = Path(__file__).resolve().parents[1] / "scripts" / "distill" / "build_sft.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(log), "--out", str(out), "--min-kill", "0.5", "--repeat", "4"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "below-min-kill" in proc.stdout
    examples = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert {e["block"] for e in examples} == {"b"}
    assert len(examples) == 3  # weight 0.75 * repeat 4
    assert examples[0]["messages"][0] == {"role": "system", "content": "sys prompt"}
    assert examples[0]["messages"][-1]["role"] == "assistant"
    assert examples[0]["messages"][-1]["content"].startswith("`ifdef FORMAL")


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    n = append_jsonl(path, [{"block": "x", "turns": 2}])
    assert n == 1
    assert "x" in path.read_text()
    report = SuiteReport(trajectories=[])
    log_suite(path, report)
    assert "suite_summary" in path.read_text()
