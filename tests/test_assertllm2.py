from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.assertllm2 import discover, load_mutants, select, write_index
from formalsynapse.cli import _generate_jobs, build_parser, main

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "assertllm2"


def test_discovers_verilog_and_skips_vhdl() -> None:
    designs = discover(FIXTURE)
    keys = {d.key for d in designs}
    assert "assertllm2/library/tiny_box" in keys
    assert "assertllm2/other/vhdl_box" in keys
    tiny = next(d for d in designs if d.name == "tiny_box")
    assert tiny.open_ok
    assert tiny.top == "tiny_box"
    assert tiny.language == "verilog"
    assert len(tiny.mutants) == 1
    assert tiny.mutants[0].operator == "LOR(Plus->Minus)"
    vhdl = next(d for d in designs if d.name == "vhdl_box")
    assert not vhdl.open_ok
    assert vhdl.skip_reason is not None
    assert "VHDL" in vhdl.skip_reason
    dual = next(d for d in designs if d.name == "dual_clk")
    assert not dual.open_ok
    assert dual.skip_reason is not None
    assert "posedge and negedge" in dual.skip_reason


def test_select_open_only_and_name() -> None:
    designs = discover(FIXTURE)
    assert len(select(designs, open_only=True)) == 1
    chosen = select(designs, only=["tiny_box"], open_only=True)
    assert [d.name for d in chosen] == ["tiny_box"]
    chosen = select(designs, only=["assertllm2/library/tiny_box"], open_only=True)
    assert [d.key for d in chosen] == ["assertllm2/library/tiny_box"]


def test_load_shipped_mutants() -> None:
    tiny = next(d for d in discover(FIXTURE) if d.name == "tiny_box")
    mutants = load_mutants(tiny, max_mutants=8)
    assert len(mutants) == 1
    assert mutants[0].name == "M_0000"
    assert "count - 4'd1" in mutants[0].rtl


def test_write_index(tmp_path: Path) -> None:
    designs = discover(FIXTURE)
    path = tmp_path / "index.json"
    write_index(designs, path)
    text = path.read_text(encoding="utf-8")
    assert "tiny_box" in text
    assert "sby (open, not JasperGold)" in text


def test_gate_list_cli(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        ["gate", "--suite", "assertllm2", "--root", str(FIXTURE), "--list", "--workdir", str(tmp_path)]
    )
    assert args.suite == "assertllm2"
    assert args.list
    rc = main(
        [
            "gate",
            "--suite",
            "assertllm2",
            "--root",
            str(FIXTURE),
            "--list",
            "--workdir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert (tmp_path / "assertllm2_index.json").is_file()


def test_generate_jobs_assertllm2_resolves_spec() -> None:
    args = build_parser().parse_args(
        ["generate", "--suite", "assertllm2", "--root", str(FIXTURE), "--only", "tiny_box"]
    )
    jobs = _generate_jobs(args)
    assert jobs is not None
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == "tiny_box"
    assert job.dut.name == "tiny_box.sv"
    assert job.spec.name == "spec.md"
    assert job.top == "tiny_box"
    assert job.extras == ()
    assert job.mutants is not None


def test_generate_jobs_skips_dual_edge_clock(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_parser().parse_args(
        ["generate", "--suite", "assertllm2", "--root", str(FIXTURE), "--only", "dual_clk"]
    )
    jobs = _generate_jobs(args)
    assert jobs is None
    assert "posedge and negedge" in capsys.readouterr().out
