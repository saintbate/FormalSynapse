"""Shared ``.sby`` text for golden / smoke blocks that already contain ``ifdef FORMAL``."""

from __future__ import annotations

from formalsynapse.sby_config import SbyConfig, SbyTask


def golden_sby_text(top: str, *, include_prove: bool = True, depth: int = 20) -> str:
    tasks = [
        SbyTask("bmc", mode="bmc", depth=depth),
        SbyTask("cover", mode="cover", depth=depth),
    ]
    if include_prove:
        tasks.append(SbyTask("prove", mode="prove", depth=depth))
    return SbyConfig(
        top=top,
        files=(f"{top}.sv",),
        tasks=tuple(tasks),
        frontend="verilog",
    ).render()
