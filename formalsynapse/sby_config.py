"""Render SymbiYosys ``.sby`` configuration files.

Only relative file names are written into ``[files]``/``[script]``: sby's config parser splits on
whitespace and the harness must work from directories whose paths contain spaces or ``:``. The
caller is responsible for running ``sby`` with the config's directory as the working directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["bmc", "prove", "cover", "live"]
Frontend = Literal["verilog", "slang"]


@dataclass(frozen=True)
class SbyTask:
    """One named task inside a multi-task ``.sby``."""

    name: str
    mode: Mode = "bmc"
    depth: int = 20
    engine: str = "smtbmc z3"
    timeout_s: int | None = None
    extra_options: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(f"task name must be an identifier: {self.name!r}")
        if self.depth < 1:
            raise ValueError("depth must be >= 1")
        if not self.engine.strip():
            raise ValueError("engine must be non-empty")


@dataclass(frozen=True)
class SbyConfig:
    """A complete ``.sby`` description."""

    top: str
    files: tuple[str, ...]
    tasks: tuple[SbyTask, ...] = (SbyTask("bmc"),)
    frontend: Frontend = "verilog"
    defines: tuple[str, ...] = ("FORMAL",)
    multiclock: bool = False
    extra_script: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.top.isidentifier():
            raise ValueError(f"top must be an identifier: {self.top!r}")
        if not self.files:
            raise ValueError("at least one source file is required")
        for f in self.files:
            if "/" in f or " " in f or ":" in f:
                raise ValueError(f"[files] entries must be bare relative names: {f!r}")
        if not self.tasks:
            raise ValueError("at least one task is required")
        names = [t.name for t in self.tasks]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate task names: {names}")

    def read_command(self) -> str:
        if self.frontend == "slang":
            defs = " ".join(f"-D {d}" for d in self.defines)
            return f"plugin -i slang\nread_slang --top {self.top} {defs} {' '.join(self.files)}".strip()
        defs = " ".join(f"-D{d}" for d in self.defines if d != "FORMAL")
        parts = ["read_verilog", "-sv", "-formal"]
        if defs:
            parts.append(defs)
        parts.extend(self.files)
        return " ".join(parts)

    def render(self) -> str:
        single = len(self.tasks) == 1
        lines: list[str] = []

        def opt(task: SbyTask, text: str) -> str:
            return text if single else f"{task.name}: {text}"

        if not single:
            lines.append("[tasks]")
            lines.extend(t.name for t in self.tasks)
            lines.append("")

        lines.append("[options]")
        for t in self.tasks:
            lines.append(opt(t, f"mode {t.mode}"))
            if t.mode != "live":
                lines.append(opt(t, f"depth {t.depth}"))
            if t.timeout_s is not None:
                lines.append(opt(t, f"timeout {t.timeout_s}"))
            for extra in t.extra_options:
                lines.append(opt(t, extra))
        if self.multiclock:
            lines.append("multiclock on")
        lines.append("")

        lines.append("[engines]")
        for t in self.tasks:
            lines.append(opt(t, t.engine))
        lines.append("")

        lines.append("[script]")
        lines.extend(self.read_command().splitlines())
        lines.append(f"prep -top {self.top}")
        lines.extend(self.extra_script)
        lines.append("")

        lines.append("[files]")
        lines.extend(self.files)
        lines.append("")
        return "\n".join(lines)


def task_dir_name(sby_stem: str, task: SbyTask, single: bool) -> str:
    """Directory sby creates for a task: ``<stem>`` or ``<stem>_<task>``."""
    return sby_stem if single else f"{sby_stem}_{task.name}"
