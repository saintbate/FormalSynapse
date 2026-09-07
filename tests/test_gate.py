from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.gate import GateReport, MutantOutcome, coi_report, evaluate, report_json
from formalsynapse.mutate import Mutant
from formalsynapse.paths import smoke_dir
from formalsynapse.toolchain import have_sby
from formalsynapse.verify_harness import VerifyResult

RTL = """\
module counter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    output logic [3:0] count
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 4'd0;
        else if (en)
            count <= count + 4'd1;
    end
endmodule
"""

SVA = """\
`ifdef FORMAL
property p_counter_inc;
    @(posedge clk) disable iff (!rst_n)
    en |=> count == $past(count) + 4'd1;
endproperty
a_counter_inc: assert property (p_counter_inc);
c_counter_en: cover property (@(posedge clk) disable iff (!rst_n) en);
`endif
"""


def _result(status: str) -> VerifyResult:
    return VerifyResult(
        status=status,  # type: ignore[arg-type]
        exit_code=0 if status == "PASS" else 2,
        run_dir=Path("."),
        sby_log_path=None,
        trace_vcd_path=None,
        failing_step=None,
        failed_assertions=(),
        errors=(),
        report="",
        depth=8,
        mode="bmc",
        elapsed_s=0.0,
    )


def test_coi_overlaps_en_and_count() -> None:
    report = coi_report(RTL, "counter", SVA)
    assert "en" in report.design_signals
    assert "count" in report.design_signals
    assert "clk" not in report.design_signals
    assert "rst_n" not in report.design_signals
    assert report.overlap == ("count", "en")
    assert report.coverage == 1.0


def test_coi_ignores_invented_names() -> None:
    report = coi_report(RTL, "counter", "assert property (ghost |-> ready);")
    assert report.overlap == ()
    assert report.coverage == 0.0


def test_kill_rate_discards_errors() -> None:
    dummy = Mutant(name="m", operator="add_sub", description="x", rtl="module x; endmodule")
    report = GateReport(
        block="t",
        prove=_result("PASS"),
        cover=_result("PASS"),
        coi=coi_report(RTL, "counter", SVA),
        mutants=(
            MutantOutcome(dummy, _result("FAIL")),
            MutantOutcome(dummy, _result("PASS")),
            MutantOutcome(dummy, _result("ERROR")),
        ),
    )
    assert report.killed == 1
    assert report.kill_rate == 0.5
    assert report.passed
    payload = report_json(report)
    assert payload["killed"] == 1
    assert payload["valid_mutants"] == 2


def test_failed_cover_fails_gate() -> None:
    dummy = Mutant(name="m", operator="add_sub", description="x", rtl="")
    report = GateReport(
        block="t",
        prove=_result("PASS"),
        cover=_result("FAIL"),
        coi=coi_report(RTL, "counter", SVA),
        mutants=(MutantOutcome(dummy, _result("FAIL")),),
    )
    assert report.vacuity_ok is False
    assert not report.passed


def test_evaluate_monkeypatched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import gate as gate_mod

    dut = tmp_path / "counter.sv"
    dut.write_text(RTL)
    sva = tmp_path / "counter.sva.sv"
    sva.write_text(SVA)

    def fake_verify(
        dut_path: Path,
        sva_text: str,
        top: str,
        **kwargs: object,
    ) -> VerifyResult:
        run_name = str(kwargs.get("run_name", ""))
        if run_name in {"prove", "cover"}:
            return _result("PASS")
        return _result("FAIL")

    monkeypatch.setattr(gate_mod, "verify", fake_verify)
    report = evaluate(dut, sva, "counter", tmp_path / "work", max_mutants=4)
    assert report.passed
    assert report.mutants
    assert report.kill_rate == 1.0
    assert (tmp_path / "work" / "mutants").is_dir()
    js = report_json(report)
    assert js["vacuous"] == [] and js["skipped"] == []


def test_params_reach_every_run_and_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    from formalsynapse import gate as gate_mod

    dut = tmp_path / "counter.sv"
    dut.write_text(RTL)
    seen: list[tuple[str, object]] = []

    def fake_verify(dut_path: Path, sva_text: str, top: str, **kwargs: object) -> VerifyResult:
        seen.append((str(kwargs.get("run_name", "")), kwargs.get("params")))
        status = "FAIL" if str(kwargs.get("run_name", "")).startswith("mutant") else "PASS"
        return replace(_result(status), params=(("WIDTH", "4"),))

    monkeypatch.setattr(gate_mod, "verify", fake_verify)
    report = evaluate(dut, SVA, "counter", tmp_path / "work", max_mutants=2, params=(("WIDTH", "4"),))
    assert seen and all(p == (("WIDTH", "4"),) for _, p in seen)
    assert {name for name, _ in seen} >= {"prove", "cover"}
    assert report.params == (("WIDTH", "4"),)
    assert report_json(report)["params"] == {"WIDTH": "4"}


def test_failed_prove_scores_no_kills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An SVA that FAILs on golden RTL fails on every mutant; those must not count as kills."""
    from formalsynapse import gate as gate_mod

    dut = tmp_path / "counter.sv"
    dut.write_text(RTL)
    calls: list[str] = []

    def fake_verify(dut_path: Path, sva_text: str, top: str, **kwargs: object) -> VerifyResult:
        calls.append(str(kwargs.get("run_name", "")))
        return _result("FAIL")

    monkeypatch.setattr(gate_mod, "verify", fake_verify)
    report = evaluate(dut, SVA, "counter", tmp_path / "work", max_mutants=4)
    assert not report.passed
    assert report.mutants == ()
    assert report.kill_rate == 0.0
    assert calls == ["prove"]  # no cover on a failed prove, no mutants either


def test_evaluate_unwraps_model_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from formalsynapse import gate as gate_mod

    dut = tmp_path / "counter.sv"
    dut.write_text(RTL)
    seen: list[str] = []

    def fake_verify(dut_path: Path, sva_text: str, top: str, **kwargs: object) -> VerifyResult:
        seen.append(sva_text)
        return _result("PASS")

    monkeypatch.setattr(gate_mod, "verify", fake_verify)
    raw = "Here is the block:\n```systemverilog\n" + SVA + "```\nHope that helps."
    evaluate(dut, raw, "counter", tmp_path / "work", max_mutants=0)
    assert seen and "```" not in seen[0] and "a_counter_inc" in seen[0]
    # a clean hand-written block is passed through byte-for-byte
    seen.clear()
    evaluate(dut, SVA, "counter", tmp_path / "work2", max_mutants=0)
    assert seen[0] == SVA


skip_no_sby = pytest.mark.skipif(not have_sby(), reason="sby/yosys/z3 not installed")


@skip_no_sby
@pytest.mark.toolchain
def test_gate_smoke_counter(tmp_path: Path) -> None:
    root = smoke_dir() / "counter"
    report = evaluate(
        root / "counter.sv",
        root / "counter.sva.sv",
        "counter",
        tmp_path,
        # With the reset assumption the 4-bit counter starts at 0, so the wrap cover needs
        # 17 cycles (it used to be "reached" from an arbitrary pre-reset state).
        depth=20,
        timeout_s=120.0,
        max_mutants=4,
    )
    assert report.prove.status == "PASS", report.prove.report
    assert report.cover is not None
    assert report.cover.status == "PASS", report.cover.report
    assert report.killed >= 1
    assert report.kill_rate > 0.0
