from __future__ import annotations

from pathlib import Path

import pytest

from formalsynapse.cli import main


def test_doctor_exits_cleanly() -> None:
    rc = main(["doctor"])
    assert rc in (0, 1)


def test_trace_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    vcd = Path(__file__).resolve().parent / "fixtures" / "counter_trace.vcd"
    rc = main(["trace", str(vcd), "--top", "counter", "--step", "2"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "count" in captured.out
