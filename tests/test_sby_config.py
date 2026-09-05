from __future__ import annotations

import pytest

from formalsynapse.golden_sby import golden_sby_text
from formalsynapse.sby_config import SbyConfig, SbyTask, task_dir_name


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
