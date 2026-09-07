from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.cli import build_parser, main


def test_doctor_exits_cleanly() -> None:
    rc = main(["doctor"])
    assert rc in (0, 1)


def test_gate_parser() -> None:
    args = build_parser().parse_args(["gate", "--only", "counter", "--min-kill", "0.25"])
    assert args.cmd == "gate"
    assert args.only == ["counter"]
    assert args.min_kill == 0.25
    assert args.max_mutants == 8


def test_gate_extra_parser() -> None:
    args = build_parser().parse_args(
        ["gate", "--dut", "rtl/foo.sv", "--sva", "foo.sva.sv", "--top", "foo", "--extra", "rtl/bar.v"]
    )
    assert args.extra == [Path("rtl/bar.v")]


def test_param_flag_parses_and_validates() -> None:
    from formalsynapse.cli import _parse_params

    args = build_parser().parse_args(
        ["gate", "--dut", "u.v", "--sva", "u.sva.sv", "--top", "u", "--param", "BIT_RATE=25000000", "--param", "N=2"]
    )
    assert _parse_params(args) == (("BIT_RATE", "25000000"), ("N", "2"))
    base = ["verify", "--dut", "u.v", "--sva", "s", "--top", "u"]
    args = build_parser().parse_args([*base, "--param", "N=1", "--param", "N=2"])
    with pytest.raises(ValueError, match="twice"):
        _parse_params(args)
    args = build_parser().parse_args([*base, "--param", "N=rm -rf"])
    with pytest.raises(ValueError):
        _parse_params(args)


def test_gate_param_requires_single_dut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Suites are graded at shipped parameters; --param without --dut is refused before any sby."""
    from formalsynapse import toolchain

    monkeypatch.setattr(toolchain, "have_sby", lambda: True)
    rc = main(["gate", "--only", "counter", "--param", "WIDTH=4", "--workdir", str(tmp_path)])
    assert rc == 2


def test_generate_assertllm2_parser() -> None:
    args = build_parser().parse_args(
        ["generate", "--suite", "assertllm2", "--only", "versatile_counter", "--out", "cand.sva.sv"]
    )
    assert args.cmd == "generate"
    assert args.block is None
    assert args.suite == "assertllm2"
    assert args.only == ["versatile_counter"]
    assert args.min_kill == 0.25
    assert args.max_mutants == 8


def test_trace_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    vcd = Path(__file__).resolve().parent / "fixtures" / "counter_trace.vcd"
    rc = main(["trace", str(vcd), "--top", "counter", "--step", "2"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "count" in captured.out
