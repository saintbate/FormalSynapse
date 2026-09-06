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
    assert gen.seen[0][0].content == SYSTEM_PROMPT
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


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    n = append_jsonl(path, [{"block": "x", "turns": 2}])
    assert n == 1
    assert "x" in path.read_text()
    report = SuiteReport(trajectories=[])
    log_suite(path, report)
    assert "suite_summary" in path.read_text()
