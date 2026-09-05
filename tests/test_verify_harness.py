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
            lowered = lower(sva.read_text())
        except LowerError as exc:
            pytest.fail(f"{block.name}: {exc}")
        assert lowered.assertions, block.name
