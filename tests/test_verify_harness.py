from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.paths import golden_dir, smoke_dir
from formalsynapse.sva_lower import LowerError, lower
from formalsynapse.toolchain import have_sby
from formalsynapse.verify_harness import verify

pytestmark = pytest.mark.toolchain

skip_no_sby = pytest.mark.skipif(not have_sby(), reason="sby/yosys/z3 not installed")


@skip_no_sby
def test_smoke_pass(tmp_path: Path) -> None:
    root = smoke_dir() / "counter"
    result = verify(
        root / "counter.sv",
        (root / "counter.sva.sv").read_text(),
        "counter",
        depth=12,
        timeout_s=120.0,
        workdir=tmp_path,
        run_name="pass",
    )
    assert result.status == "PASS", result.report
    assert result.reward == 1.0
    assert result.lowered is not None


@skip_no_sby
def test_smoke_fail_has_trace(tmp_path: Path) -> None:
    root = smoke_dir() / "counter"
    result = verify(
        root / "counter.sv",
        (root / "counter_fail.sva.sv").read_text(),
        "counter",
        depth=12,
        timeout_s=120.0,
        workdir=tmp_path,
        run_name="fail",
    )
    assert result.status == "FAIL", result.report
    assert result.reward == 0.0
    assert result.trace_vcd_path is not None
    assert result.trace_vcd_path.is_file()
    assert result.failed_assertions
    assert "Failed assertion" in result.report or "cyc" in result.report


@skip_no_sby
def test_syntax_error_is_error(tmp_path: Path) -> None:
    root = smoke_dir() / "counter"
    result = verify(
        root / "counter.sv",
        "this is not sva",
        "counter",
        depth=8,
        timeout_s=60.0,
        workdir=tmp_path,
        run_name="err",
    )
    assert result.status == "ERROR"
    assert result.reward == -1.0


@skip_no_sby
def test_golden_counter(tmp_path: Path) -> None:
    block = golden_dir() / "counter"
    result = verify(
        block / "counter.sv",
        (block / "counter.sva.sv").read_text(),
        "counter",
        depth=16,
        timeout_s=180.0,
        workdir=tmp_path,
        run_name="golden-counter",
    )
    assert result.status == "PASS", result.report


def test_lower_golden_suite_parses() -> None:
    for block in sorted(p for p in golden_dir().iterdir() if p.is_dir()):
        sva = block / f"{block.name}.sva.sv"
        try:
            lowered = lower(sva.read_text(), strict=True)
        except LowerError as exc:
            pytest.fail(f"{block.name}: {exc}")
        assert lowered.proves_something, block.name
        assert not lowered.skipped, f"{block.name}: {lowered.skipped}"


# ---- grader integrity, end to end -------------------------------------------------------------

COUNTER = """\
module counter (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       up,
    input  logic       down,
    output logic [3:0] count
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) count <= 4'd0;
        else if (up && !down && count != 4'hF) count <= count + 4'd1;
        else if (down && !up && count != 4'd0) count <= count - 4'd1;
    end
endmodule
"""

HOLD_ZERO = """\
module hold (
    input  logic clk,
    input  logic rst_n,
    output logic q
);
    always_ff @(posedge clk) begin
        if (!rst_n) q <= 1'b0;
        else q <= q;
    end
endmodule
"""


@skip_no_sby
def test_reset_assumption_removes_pre_reset_garbage(tmp_path: Path) -> None:
    """``q`` is 0 after reset forever, but arbitrary before it. Only the reset-assumed run PASSes."""
    dut = tmp_path / "hold.sv"
    dut.write_text(HOLD_ZERO)
    sva = "a_zero: assert property (@(posedge clk) !q);\n"
    with_reset = verify(dut, sva, "hold", depth=10, timeout_s=60.0, workdir=tmp_path, run_name="rst")
    assert with_reset.status == "PASS", with_reset.report
    assert with_reset.lowered is not None and with_reset.lowered.reset == "rst_n"
    without = verify(
        dut, sva, "hold", depth=10, timeout_s=60.0, workdir=tmp_path, run_name="norst", reset_assume=False
    )
    assert without.status == "FAIL", without.report


@skip_no_sby
def test_range_consequent_is_checked_within_bound(tmp_path: Path) -> None:
    """Regression for the guard-depth bug: this used to PASS at depth 20 because the check
    was gated to cycle >= 21."""
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    sva = (
        "a_imp: assert property (@(posedge clk) disable iff (!rst_n) "
        "up |=> ##[0:10] (count == 4'd3 && count == 4'd5));\n"
    )
    res = verify(dut, sva, "counter", depth=20, timeout_s=60.0, workdir=tmp_path)
    assert res.status == "FAIL", res.report
    assert res.failed_assertions == ("a_imp",)


@skip_no_sby
def test_bound_too_shallow_is_error_not_pass(tmp_path: Path) -> None:
    """A check gated to cycle >= 12 never fires in a 10-step BMC; sby would say PASS."""
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    sva = "a_late: assert property (@(posedge clk) disable iff (!rst_n) up |=> ##10 (count == 4'd9));\n"
    shallow = verify(dut, sva, "counter", depth=10, timeout_s=60.0, workdir=tmp_path, run_name="s")
    assert shallow.status == "ERROR"
    assert "too shallow" in shallow.report and "a_late" in shallow.report
    deep = verify(dut, sva, "counter", depth=16, timeout_s=60.0, workdir=tmp_path, run_name="d")
    assert deep.status == "FAIL", deep.report  # actually checked now, and it is false


PARAM_COUNTER = """\
module pcounter #(
    parameter int LIMIT = 200
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    output logic       done
);
    logic [7:0] n;
    always_ff @(posedge clk) begin
        if (!rst_n) n <= 8'd0;
        else if (en && n != LIMIT[7:0]) n <= n + 8'd1;
    end
    assign done = (n == LIMIT[7:0]);
endmodule
"""


@skip_no_sby
def test_param_override_is_applied_and_reported(tmp_path: Path) -> None:
    """At LIMIT=200 ``done`` is unreachable in 12 steps; at LIMIT=3 it is reached, and the result
    says which elaboration it holds for."""
    dut = tmp_path / "pcounter.sv"
    dut.write_text(PARAM_COUNTER)
    sva = "c_done: cover property (@(posedge clk) disable iff (!rst_n) done);\n"
    shipped = verify(dut, sva, "pcounter", depth=12, timeout_s=60.0, workdir=tmp_path, mode="cover", run_name="a")
    assert shipped.status == "FAIL", shipped.report
    assert shipped.params == ()
    small = verify(
        dut,
        sva,
        "pcounter",
        depth=12,
        timeout_s=60.0,
        workdir=tmp_path,
        mode="cover",
        run_name="b",
        params=(("LIMIT", "3"),),
    )
    assert small.status == "PASS", small.report
    assert small.params == (("LIMIT", "3"),)
    assert "LIMIT=3" in small.summary() and "LIMIT=3" in small.report
    assert (small.run_dir / "params.txt").read_text() == "LIMIT=3\n"
    assert "chparam -set LIMIT 3 pcounter" in (small.run_dir / "run.sby").read_text()
    bogus = verify(
        dut, sva, "pcounter", depth=12, timeout_s=60.0, workdir=tmp_path, mode="cover", run_name="c",
        params=(("NOPE", "1"),),
    )
    assert bogus.status == "ERROR" and "NOPE" in bogus.report


@skip_no_sby
def test_nothing_lowered_is_error_not_pass(tmp_path: Path) -> None:
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    sva = "c_up: cover property (@(posedge clk) disable iff (!rst_n) up);\n"
    res = verify(dut, sva, "counter", depth=8, timeout_s=60.0, workdir=tmp_path)
    assert res.status == "ERROR"
    assert "nothing to prove" in res.report
    cov = verify(dut, sva, "counter", depth=8, timeout_s=60.0, workdir=tmp_path, mode="cover", run_name="c")
    assert cov.status == "PASS", cov.report


@skip_no_sby
def test_vacuous_antecedent_fails_cover_and_names_the_assert(tmp_path: Path) -> None:
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    sva = (
        "a_vac: assert property (@(posedge clk) disable iff (!rst_n) (up && !up) |=> count == 0);\n"
        "a_ok: assert property (@(posedge clk) disable iff (!rst_n) "
        "(up && !down && count != 4'hF) |=> count == $past(count) + 1);\n"
    )
    bmc = verify(dut, sva, "counter", depth=12, timeout_s=60.0, workdir=tmp_path, run_name="bmc")
    assert bmc.status == "PASS", bmc.report
    cov = verify(dut, sva, "counter", depth=12, timeout_s=60.0, workdir=tmp_path, run_name="cov", mode="cover")
    assert cov.status == "FAIL"
    assert cov.vacuous_assertions == ("a_vac",)
    assert "a_ok__cov" in cov.reached_covers


@skip_no_sby
def test_strict_rejects_environment_assume(tmp_path: Path) -> None:
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    sva = (
        "m_never_up: assume property (@(posedge clk) disable iff (!rst_n) !up);\n"
        "a_const: assert property (@(posedge clk) disable iff (!rst_n) count == 0);\n"
    )
    trusted = verify(dut, sva, "counter", depth=8, timeout_s=60.0, workdir=tmp_path, run_name="t")
    assert trusted.status == "PASS"  # the assume makes it trivially true
    strict = verify(dut, sva, "counter", depth=8, timeout_s=60.0, workdir=tmp_path, run_name="s", strict=True)
    assert strict.status == "ERROR"
    assert "assume property is not allowed" in strict.report


@skip_no_sby
def test_undeclared_signal_in_sva_is_error(tmp_path: Path) -> None:
    """Yosys turns an unknown identifier into a free wire; the harness must not call that a proof."""
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    sva = "a_ghost: assert property (@(posedge clk) disable iff (!rst_n) ghost_en |-> count <= 4'hF);\n"
    res = verify(dut, sva, "counter", depth=6, timeout_s=60.0, workdir=tmp_path)
    assert res.status == "ERROR"
    assert "ghost_en" in res.report


@skip_no_sby
def test_missing_extra_file_is_error(tmp_path: Path) -> None:
    dut = tmp_path / "counter.sv"
    dut.write_text(COUNTER)
    res = verify(
        dut,
        "a_x: assert property (@(posedge clk) disable iff (!rst_n) count <= 4'hF);\n",
        "counter",
        depth=4,
        timeout_s=30.0,
        workdir=tmp_path,
        extra_files=(tmp_path / "does_not_exist.vh",),
    )
    assert res.status == "ERROR"
    assert "not found" in res.report
