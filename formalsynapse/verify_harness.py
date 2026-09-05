"""Run a candidate SVA block against a DUT with SymbiYosys and classify the outcome.

Pipeline::

    SVA text --lower--> immediate assertions --inject--> <top>.sv copy --sby-> PASS/FAIL/ERROR/TIMEOUT

Status semantics (these are the RLVR rewards of Phase 4, kept explicit and deterministic):

- ``PASS``    all assertions hold to the requested depth (``sby`` rc 0)                -> +1.0
- ``FAIL``    a counterexample was found (``sby`` rc 2, ``trace.vcd`` written)          ->  0.0
- ``ERROR``   the SVA could not be lowered or the design failed to elaborate (rc 16)   -> -1.0
- ``TIMEOUT`` solver did not finish (rc 8 or harness wall-clock timeout)               ->  0.0
- ``UNKNOWN`` solver returned unknown (rc 4)                                            ->  0.0
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from formalsynapse import toolchain
from formalsynapse.sby_config import Frontend, Mode, SbyConfig, SbyTask
from formalsynapse.sva_inject import InjectError, inject_clean
from formalsynapse.sva_lower import LoweredSVA, LowerError, lower
from formalsynapse.vcd_parser import CounterexampleReport, build_report, load_vcd

Status = Literal["PASS", "FAIL", "ERROR", "TIMEOUT", "UNKNOWN"]

REWARD: dict[Status, float] = {
    "PASS": 1.0,
    "FAIL": 0.0,
    "ERROR": -1.0,
    "TIMEOUT": 0.0,
    "UNKNOWN": 0.0,
}

# sby exit codes (sby --help / sby_core): 0 pass, 2 fail, 4 unknown, 8 timeout, 16 error
_RC_STATUS: dict[int, Status] = {0: "PASS", 2: "FAIL", 4: "UNKNOWN", 8: "TIMEOUT", 16: "ERROR"}

_FAILED_ASSERTION = re.compile(r"failed assertion\s+(\S+)\s+at\s+(\S+)\s+step\s+(\d+)", re.I)
_ASSERT_FAILED = re.compile(r"Assert failed in\s+(\S+):\s+(\S+)")
_UNREACHED = re.compile(r"Unreached cover statement at\s+(\S+)")
_REACHED = re.compile(r"Reached cover statement at\s+(\S+)\s+in step\s+(\d+)")
_ERROR_LINE = re.compile(r"(?:ERROR|error):\s*(.+)")


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of one ``verify`` call."""

    status: Status
    exit_code: int
    run_dir: Path
    sby_log_path: Path | None
    trace_vcd_path: Path | None
    failing_step: int | None
    failed_assertions: tuple[str, ...]
    errors: tuple[str, ...]
    report: str
    depth: int
    mode: Mode
    elapsed_s: float
    lowered: LoweredSVA | None = None
    unreached_covers: tuple[str, ...] = field(default_factory=tuple)
    reached_covers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reward(self) -> float:
        return REWARD[self.status]

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def summary(self) -> str:
        head = f"{self.status} (rc={self.exit_code}, mode={self.mode}, depth={self.depth}, {self.elapsed_s:.1f}s)"
        if self.failed_assertions:
            head += f" failed: {', '.join(self.failed_assertions)}"
        if self.failing_step is not None:
            head += f" @ step {self.failing_step}"
        if self.unreached_covers:
            head += f" unreached: {', '.join(self.unreached_covers)}"
        return head


def _error_result(
    run_dir: Path, message: str, *, depth: int, mode: Mode, started: float, log: Path | None = None
) -> VerifyResult:
    return VerifyResult(
        status="ERROR",
        exit_code=-1,
        run_dir=run_dir,
        sby_log_path=log,
        trace_vcd_path=None,
        failing_step=None,
        failed_assertions=(),
        errors=(message,),
        report=message,
        depth=depth,
        mode=mode,
        elapsed_s=time.monotonic() - started,
    )


def prepare_run_dir(workdir: Path, run_name: str | None) -> Path:
    name = run_name or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    run_dir = workdir / name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    return run_dir


def prepare_sources(
    dut_path: Path,
    sva_text: str,
    top: str,
    run_dir: Path,
    *,
    frontend: Frontend = "verilog",
    extra_files: tuple[Path, ...] = (),
) -> tuple[Path, LoweredSVA | None]:
    """Lower (unless slang) and inject the SVA; write ``<top>.sv`` plus extras into ``run_dir``.

    Raises :class:`LowerError` or :class:`InjectError`.
    """
    dut_text = dut_path.read_text()
    lowered: LoweredSVA | None = None
    if frontend == "verilog":
        lowered = lower(sva_text)
        block = lowered.verilog
    else:
        block = sva_text
    merged = inject_clean(dut_text, block, top)
    out = run_dir / f"{top}.sv"
    out.write_text(merged)
    for extra in extra_files:
        shutil.copy(extra, run_dir / extra.name)
    return out, lowered


def _parse_log(
    log_text: str,
) -> tuple[tuple[str, ...], int | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    failed: list[str] = []
    step: int | None = None
    for m in _FAILED_ASSERTION.finditer(log_text):
        name = m.group(1).split(".")[-1]
        if name not in failed:
            failed.append(name)
        step = int(m.group(3)) if step is None else min(step, int(m.group(3)))
    if not failed:
        for m in _ASSERT_FAILED.finditer(log_text):
            name = m.group(2).split(".")[-1]
            if name not in failed:
                failed.append(name)
    errors: list[str] = []
    for line in log_text.splitlines():
        em = _ERROR_LINE.search(line)
        if em and "returned ERROR" not in line and "DONE (ERROR" not in line:
            msg = em.group(1).strip()
            if msg not in errors:
                errors.append(msg)
    unreached = tuple(dict.fromkeys(m.group(1) for m in _UNREACHED.finditer(log_text)))
    reached = tuple(dict.fromkeys(m.group(1) for m in _REACHED.finditer(log_text)))
    return tuple(failed), step, tuple(errors), unreached, reached


def run_sby(sby_path: Path, *, timeout_s: float, extra_args: tuple[str, ...] = ()) -> tuple[int, str, bool]:
    """Run ``sby -f <file>`` from the file's directory. Returns ``(rc, output, timed_out)``."""
    sby = toolchain.find_tool("sby")
    if sby is None:
        raise FileNotFoundError("sby not found; run scripts/install_toolchain.sh")
    cmd = [str(sby), "-f", *extra_args, sby_path.name]
    try:
        proc = subprocess.run(
            cmd,
            cwd=sby_path.parent,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=toolchain.tool_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 8, str(out), True
    return proc.returncode, proc.stdout + proc.stderr, False


def verify(
    dut_path: Path,
    sva_text: str,
    top: str,
    *,
    depth: int = 20,
    engine: str = "smtbmc z3",
    mode: Mode = "bmc",
    timeout_s: float = 300.0,
    workdir: Path = Path("work"),
    run_name: str | None = None,
    frontend: Frontend = "verilog",
    extra_files: tuple[Path, ...] = (),
    clock: str = "clk",
) -> VerifyResult:
    """Verify ``sva_text`` against ``dut_path``'s module ``top`` and classify the result."""
    started = time.monotonic()
    run_dir = prepare_run_dir(workdir, run_name)
    (run_dir / "attempt.sva").write_text(sva_text)
    try:
        _, lowered = prepare_sources(dut_path, sva_text, top, run_dir, frontend=frontend, extra_files=extra_files)
    except LowerError as exc:
        (run_dir / "error.txt").write_text(f"SVA lowering error: {exc}\n")
        return _error_result(run_dir, f"SVA lowering error: {exc}", depth=depth, mode=mode, started=started)
    except InjectError as exc:
        (run_dir / "error.txt").write_text(f"injection error: {exc}\n")
        return _error_result(run_dir, f"injection error: {exc}", depth=depth, mode=mode, started=started)

    files = (f"{top}.sv", *(p.name for p in extra_files))
    task = SbyTask("run", mode=mode, depth=depth, engine=engine)
    cfg = SbyConfig(top=top, files=files, tasks=(task,), frontend=frontend)
    sby_file = run_dir / "run.sby"
    sby_file.write_text(cfg.render())

    try:
        rc, out, timed_out = run_sby(sby_file, timeout_s=timeout_s)
    except FileNotFoundError as exc:
        return _error_result(run_dir, str(exc), depth=depth, mode=mode, started=started)

    task_dir = run_dir / "run"
    log_path = task_dir / "logfile.txt"
    log_text = (log_path.read_text() if log_path.exists() else "") + "\n" + out
    failed, step, errors, unreached, reached = _parse_log(log_text)

    status: Status = "TIMEOUT" if timed_out else _RC_STATUS.get(rc, "ERROR")
    if status == "ERROR" and not errors:
        tail = [line for line in out.splitlines() if line.strip()][-5:]
        errors = tuple(tail) or (f"sby exited with {rc}",)

    trace = task_dir / "engine_0" / "trace.vcd"
    trace_path = trace if trace.exists() else None
    report_lines = [f"Result: {status}"]
    if status == "FAIL":
        if mode == "cover" and unreached:
            report_lines.append("Unreached cover statements: " + ", ".join(unreached))
        elif trace_path is not None:
            try:
                rep: CounterexampleReport = build_report(
                    load_vcd(trace_path),
                    top=top,
                    failing_step=step,
                    failed_assertions=failed,
                    clock=clock,
                )
                report_lines.append(rep.render())
            except (OSError, ValueError) as exc:  # pragma: no cover - defensive
                report_lines.append(f"(could not parse trace: {exc})")
        else:
            report_lines.append("Counterexample found but no trace.vcd was written.")
    elif status == "ERROR":
        report_lines.append("Elaboration/solver errors:")
        report_lines.extend(f"  {e}" for e in errors)
    elif status == "PASS" and mode == "cover":
        report_lines.append("Reached cover statements: " + (", ".join(reached) or "(none listed)"))

    return VerifyResult(
        status=status,
        exit_code=rc,
        run_dir=run_dir,
        sby_log_path=log_path if log_path.exists() else None,
        trace_vcd_path=trace_path,
        failing_step=step,
        failed_assertions=failed,
        errors=errors,
        report="\n".join(report_lines),
        depth=depth,
        mode=mode,
        elapsed_s=time.monotonic() - started,
        lowered=lowered,
        unreached_covers=unreached,
        reached_covers=reached,
    )
