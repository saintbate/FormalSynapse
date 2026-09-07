from __future__ import annotations

import pytest

from formalsynapse.golden_sby import golden_sby_text
from formalsynapse.sby_config import SbyConfig, SbyTask, parse_param, task_dir_name


def test_single_task_render() -> None:
    text = SbyConfig(top="counter", files=("counter.sv",)).render()
    assert "[tasks]" not in text
    assert "mode bmc" in text
    assert "read_verilog -sv -formal counter.sv" in text
    assert "prep -top counter" in text
    assert text.strip().endswith("counter.sv")


def test_multi_task_prefixes() -> None:
    cfg = SbyConfig(
        top="counter",
        files=("counter.sv",),
        tasks=(SbyTask("bmc"), SbyTask("cover", mode="cover"), SbyTask("prove", mode="prove")),
    )
    text = cfg.render()
    assert "bmc: mode bmc" in text
    assert "cover: mode cover" in text
    assert "prove: mode prove" in text
    assert task_dir_name("counter", SbyTask("bmc"), single=False) == "counter_bmc"


def test_rejects_path_separators() -> None:
    with pytest.raises(ValueError, match="bare relative"):
        SbyConfig(top="t", files=("dir/t.sv",))


def test_golden_helper() -> None:
    text = golden_sby_text("sync_fifo")
    assert "read_verilog -sv -formal sync_fifo.sv" in text
    assert "bmc:" in text
    assert "cover:" in text
    assert "prove:" in text


def test_slang_frontend() -> None:
    text = SbyConfig(top="t", files=("t.sv",), frontend="slang").render()
    assert "plugin -i slang" in text
    assert "read_slang" in text


def test_params_render_as_chparam_before_prep() -> None:
    cfg = SbyConfig(top="uart_tx", files=("uart_tx.sv",), params=(("BIT_RATE", "25000000"), ("PAYLOAD_BITS", "2")))
    lines = cfg.render().splitlines()
    read = lines.index("read_verilog -sv -formal uart_tx.sv")
    prep = lines.index("prep -top uart_tx")
    assert lines[read + 1 : prep] == [
        "chparam -set BIT_RATE 25000000 uart_tx",
        "chparam -set PAYLOAD_BITS 2 uart_tx",
    ]
    assert "chparam" not in SbyConfig(top="t", files=("t.sv",)).render()


@pytest.mark.parametrize("text", ["BIT_RATE=9600", " W = 8'd3 ", "R=-1", "F=1.5", 'S="abc"', "X=32'hDEAD_BEEF"])
def test_parse_param_accepts_literals(text: str) -> None:
    name, value = parse_param(text)
    assert name.isidentifier() and value == text.split("=", 1)[1].strip()


@pytest.mark.parametrize("text", ["BIT_RATE", "=5", "1X=5", "N=", "N=5; rm -rf", "N=a b", "N=$(x)", "N=foo"])
def test_parse_param_rejects_junk(text: str) -> None:
    with pytest.raises(ValueError):
        parse_param(text)


def test_duplicate_params_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        SbyConfig(top="t", files=("t.sv",), params=(("N", "1"), ("N", "2")))
