"""Locate and characterize the formal toolchain (OSS CAD Suite).

Resolution order for every tool:

1. ``$FSYN_TOOLCHAIN_DIR/bin/<tool>`` (default ``~/.local/opt/oss-cad-suite``)
2. ``<tool>`` on ``$PATH``

The suite is deliberately kept outside the repository because the workspace path may contain
``:`` which cannot appear inside ``$PATH`` entries.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TOOLCHAIN_DIR = Path.home() / ".local" / "opt" / "oss-cad-suite"

REQUIRED_TOOLS: tuple[str, ...] = ("yosys", "sby", "yosys-smtbmc", "z3")
OPTIONAL_TOOLS: tuple[str, ...] = ("bitwuzla", "boolector", "yices", "verilator", "gtkwave")


def toolchain_dir() -> Path:
    """Return the configured toolchain directory (may not exist)."""
    raw = os.environ.get("FSYN_TOOLCHAIN_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_TOOLCHAIN_DIR


def find_tool(name: str) -> Path | None:
    """Locate ``name`` in the toolchain dir first, then on PATH."""
    candidate = toolchain_dir() / "bin" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def tool_env() -> dict[str, str]:
    """Environment for subprocesses with the toolchain ``bin`` prepended to PATH."""
    env = dict(os.environ)
    bin_dir = toolchain_dir() / "bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _run(cmd: list[str], timeout_s: float = 60.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=tool_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr)


def tool_version(name: str) -> str | None:
    """Best-effort single-line version string for a tool, or None if unavailable."""
    path = find_tool(name)
    if path is None:
        return None
    flags: dict[str, list[str]] = {
        "yosys": ["-V"],
        "sby": ["--help"],
        "yosys-smtbmc": ["--help"],
        "z3": ["--version"],
        "bitwuzla": ["--version"],
        "boolector": ["--version"],
        "yices": ["--version"],
        "verilator": ["--version"],
        "gtkwave": ["--version"],
    }
    rc, out = _run([str(path), *flags.get(name, ["--version"])], timeout_s=30.0)
    first = next((line.strip() for line in out.splitlines() if line.strip()), "")
    if name in ("sby", "yosys-smtbmc"):
        return "present"
    return first or ("present" if rc == 0 else None)


def slang_plugin_status() -> tuple[bool, bool, str]:
    """Return ``(plugin_loads, concurrent_sva_supported, detail)``.

    The OSS CAD Suite ships the ``yosys-slang`` frontend, but as of the 2026 releases its SVA
    support (povik/yosys-slang PR #237) is not included: ``assert property`` with sequences is
    rejected with ``SVA unsupported``. The harness therefore lowers SVA to immediate assertions
    (see :mod:`formalsynapse.sva_lower`). This probe records the current state so ``fsyn doctor``
    can report when native support arrives.
    """
    yosys = find_tool("yosys")
    if yosys is None:
        return False, False, "yosys not found"
    rc, out = _run([str(yosys), "-q", "-p", "plugin -i slang"], timeout_s=60.0)
    if rc != 0:
        return (
            False,
            False,
            out.strip().splitlines()[-1] if out.strip() else "plugin -i slang failed",
        )

    probe = (
        "module fsyn_probe(input logic clk, input logic rst_n, input logic a, input logic b);\n"
        "  property p; @(posedge clk) disable iff (!rst_n) a |=> b; endproperty\n"
        "  a_p: assert property (p);\n"
        "endmodule\n"
    )
    with tempfile.TemporaryDirectory(prefix="fsyn_probe_") as tmp:
        src = Path(tmp) / "probe.sv"
        src.write_text(probe)
        rc, out = _run(
            [
                str(yosys),
                "-q",
                "-p",
                f"plugin -i slang; read_slang --top fsyn_probe {src}; prep -top fsyn_probe",
            ],
            timeout_s=60.0,
        )
    if rc == 0:
        return True, True, "yosys-slang accepts concurrent SVA"
    err = next((line for line in out.splitlines() if "error" in line.lower()), out.strip())
    return True, False, err.strip()


@dataclass(frozen=True)
class ToolReport:
    """Snapshot of the toolchain as seen by the harness."""

    toolchain_dir: Path
    tools: dict[str, Path | None]
    versions: dict[str, str | None]
    slang_loads: bool
    slang_sva: bool
    slang_detail: str
    missing_required: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.missing_required

    def render(self) -> str:
        lines = [f"toolchain dir: {self.toolchain_dir} ({'found' if self.toolchain_dir.is_dir() else 'missing'})"]
        for name in (*REQUIRED_TOOLS, *OPTIONAL_TOOLS):
            path = self.tools.get(name)
            tag = "required" if name in REQUIRED_TOOLS else "optional"
            if path is None:
                lines.append(f"  {name:<14} MISSING ({tag})")
            else:
                lines.append(f"  {name:<14} {self.versions.get(name) or 'present':<48} {path}")
        lines.append(
            "  yosys-slang    "
            + ("loads" if self.slang_loads else "does not load")
            + ", concurrent SVA "
            + ("SUPPORTED" if self.slang_sva else "NOT supported")
            + f" ({self.slang_detail})"
        )
        lines.append(
            "SVA frontend: " + ("native yosys-slang" if self.slang_sva else "sva_lower -> read_verilog -sv -formal")
        )
        lines.append("status: " + ("OK" if self.ok else f"MISSING {', '.join(self.missing_required)}"))
        return "\n".join(lines)


def inspect_toolchain(probe_slang: bool = True) -> ToolReport:
    """Collect tool paths, versions and slang capability."""
    tools = {name: find_tool(name) for name in (*REQUIRED_TOOLS, *OPTIONAL_TOOLS)}
    versions = {name: (tool_version(name) if path else None) for name, path in tools.items()}
    missing = tuple(name for name in REQUIRED_TOOLS if tools[name] is None)
    if probe_slang and tools["yosys"] is not None:
        loads, sva, detail = slang_plugin_status()
    else:
        loads, sva, detail = False, False, "not probed"
    return ToolReport(
        toolchain_dir=toolchain_dir(),
        tools=tools,
        versions=versions,
        slang_loads=loads,
        slang_sva=sva,
        slang_detail=detail,
        missing_required=missing,
    )


def have_sby() -> bool:
    """True when sby, yosys and z3 are all available (used to skip toolchain tests)."""
    return all(find_tool(n) is not None for n in ("sby", "yosys", "z3"))
