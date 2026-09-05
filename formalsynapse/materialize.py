"""Write lowered ``ifdef FORMAL`` blocks into DUTs and emit companion ``.sby`` files."""

from __future__ import annotations

from pathlib import Path

from formalsynapse.golden_sby import golden_sby_text
from formalsynapse.paths import golden_dir, smoke_dir
from formalsynapse.sva_inject import inject_clean
from formalsynapse.sva_lower import lower


def materialize_block(block_dir: Path, top: str, sva_name: str | None = None) -> Path:
    """Inject lowered SVA into ``<top>.sv`` and write ``<top>.sby``. Returns the DUT path."""
    dut = block_dir / f"{top}.sv"
    sva = block_dir / (sva_name or f"{top}.sva.sv")
    lowered = lower(sva.read_text())
    dut.write_text(inject_clean(dut.read_text(), lowered.verilog, top))
    (block_dir / f"{top}.sby").write_text(golden_sby_text(top))
    return dut


def materialize_smoke() -> None:
    root = smoke_dir() / "counter"
    dut = root / "counter.sv"
    rtl = dut.read_text()
    pass_l = lower((root / "counter.sva.sv").read_text())
    fail_l = lower((root / "counter_fail.sva.sv").read_text())
    dut.write_text(inject_clean(rtl, pass_l.verilog, "counter"))
    (root / "counter_fail.sv").write_text(inject_clean(rtl, fail_l.verilog, "counter"))
    (root / "counter.sby").write_text(
        """\
[tasks]
pass
fail

[options]
pass: mode bmc
pass: depth 12
fail: mode bmc
fail: depth 12

[engines]
smtbmc z3

[script]
pass: read_verilog -sv -formal counter.sv
fail: read_verilog -sv -formal counter_fail.sv
prep -top counter

[files]
counter.sv
counter_fail.sv
"""
    )


def materialize_all() -> list[Path]:
    written: list[Path] = []
    materialize_smoke()
    written.append(smoke_dir() / "counter" / "counter.sv")
    for block in sorted(p for p in golden_dir().iterdir() if p.is_dir()):
        written.append(materialize_block(block, block.name))
    return written
