from __future__ import annotations

from pathlib import Path

from formalsynapse.vcd_parser import build_report, format_value, load_vcd, parse_vcd, sample

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "counter_trace.vcd"


def test_parse_fixture() -> None:
    vcd = load_vcd(FIXTURE)
    assert vcd.by_name("count") is not None
    assert vcd.by_name("smt_step") is not None
    steps, traces = sample(vcd, top="counter")
    names = {t.name for t in traces}
    assert "count" in names
    assert "en" in names
    assert "anyinit" not in names
    assert "a_wrong_EN" not in names
    assert steps == [0, 1, 2, 3]
    count = next(t for t in traces if t.name == "count")
    assert count.values[0] == "0x0"
    assert count.values[2] == "0x1"


def test_report_marks_changes() -> None:
    vcd = load_vcd(FIXTURE)
    report = build_report(vcd, top="counter", failing_step=2, failed_assertions=("a_wrong",), clock="clk")
    text = report.render()
    assert "a_wrong" in text
    assert "cyc2" in text
    assert "en" in report.changed_at_failure or "count" in report.changed_at_failure


def test_format_value() -> None:
    assert format_value("1", 1) == "1"
    assert format_value("1010", 4) == "0xa"


def test_parse_inline() -> None:
    vcd = parse_vcd(
        "$scope module t $end\n$var wire 1 n0 clk $end\n$upscope $end\n$enddefinitions $end\n#0\n1n0\n#5\n0n0\n"
    )
    assert vcd.times == [0, 5]
    assert vcd.value_at("n0", 1) == "0"
