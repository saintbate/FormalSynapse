from __future__ import annotations

from formalsynapse.toolchain import inspect_toolchain, toolchain_dir


def test_inspect_without_slang_probe() -> None:
    report = inspect_toolchain(probe_slang=False)
    assert report.toolchain_dir == toolchain_dir()
    assert "toolchain dir" in report.render()
