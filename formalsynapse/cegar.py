"""3-turn CEGAR loop: generate SVA, grade with sby, refine from the counterexample.

Zero-shot is turn 1. Up to three solver-feedback iterations follow (spec: max 3). The LLM never
grades itself — :func:`formalsynapse.verify_harness.verify` is the only referee.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from formalsynapse.design_context import extract_context
from formalsynapse.generator import GenerateError, Generator, Message, generate_sva_n
from formalsynapse.prompts import (
    extract_fail_user,
    refinement_user,
    slot_repair_user,
    system_prompt,
    zero_shot_user,
)
from formalsynapse.sva_edit import merge_sva, strip_labels, unwrap_formal
from formalsynapse.sva_inject import strip_formal_blocks
from formalsynapse.verify_harness import VerifyResult, verify

MAX_FEEDBACK = 3


@dataclass(frozen=True)
class Attempt:
    """One generate → verify cycle."""

    turn: int
    sva: str
    result: VerifyResult


@dataclass(frozen=True)
class Trajectory:
    """Full CEGAR record for one DUT (the RLVR / dataset unit)."""

    block: str
    top: str
    prompt: str
    attempts: tuple[Attempt, ...]
    elapsed_s: float

    @property
    def status(self) -> str:
        return self.attempts[-1].result.status if self.attempts else "ERROR"

    @property
    def ok(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].result.ok

    @property
    def first_pass(self) -> bool:
        return bool(self.attempts) and self.attempts[0].result.ok

    @property
    def healed(self) -> bool:
        return self.ok and not self.first_pass

    @property
    def turns(self) -> int:
        return len(self.attempts)

    def dataset_rows(self) -> list[dict[str, object]]:
        """``(Prompt, Faulty_Attempt, Counterexample, Fixed_Attempt)`` rows for successful heals."""
        if not self.healed:
            return []
        rows: list[dict[str, object]] = []
        final = self.attempts[-1].sva
        for att in self.attempts[:-1]:
            if att.result.ok:
                continue
            rows.append(
                {
                    "block": self.block,
                    "prompt": self.prompt,
                    "faulty_attempt": att.sva,
                    "counterexample": att.result.report,
                    "fixed_attempt": final,
                    "fault_status": att.result.status,
                    "turns": self.turns,
                }
            )
        return rows


@dataclass
class SuiteReport:
    """Aggregate metrics matching PROJECT_CONTEXT.md §5."""

    trajectories: list[Trajectory] = field(default_factory=list)

    def _n(self) -> int:
        return len(self.trajectories)

    def _syntax_ok(self) -> int:
        return _count(
            self.trajectories,
            lambda t: bool(t.attempts) and t.attempts[0].result.status != "ERROR",
        )

    @property
    def syntactic_rate(self) -> float:
        if not self.trajectories:
            return 0.0
        ok = sum(1 for t in self.trajectories if t.attempts and t.attempts[0].result.status != "ERROR")
        return ok / self._n()

    @property
    def first_pass_rate(self) -> float:
        if not self.trajectories:
            return 0.0
        return sum(1 for t in self.trajectories if t.first_pass) / self._n()

    @property
    def cegar_rate(self) -> float:
        if not self.trajectories:
            return 0.0
        return sum(1 for t in self.trajectories if t.ok) / self._n()

    @property
    def heal_rate(self) -> float:
        failed_first = [t for t in self.trajectories if t.attempts and not t.first_pass]
        if not failed_first:
            return 0.0
        return sum(1 for t in failed_first if t.healed) / len(failed_first)

    def render(self) -> str:
        n = self._n()
        lines = [
            f"blocks: {n}",
            "syntactic compilation (first attempt not ERROR): "
            f"{self.syntactic_rate:.0%}  ({self._syntax_ok()}/{n})",
            f"first-pass formal: {self.first_pass_rate:.0%}  (target 40%)",
            f"CEGAR multi-turn pass: {self.cegar_rate:.0%}  (target 50–60%)",
            f"heal rate among first-pass failures: {self.heal_rate:.0%}",
            "",
            f"{'block':<20} {'turns':>5}  {'first':<7}  final",
        ]
        for t in self.trajectories:
            first = t.attempts[0].result.status if t.attempts else "-"
            lines.append(f"{t.block:<20} {t.turns:>5}  {first:<7}  {t.status}")
        return "\n".join(lines)


def _count(items: Sequence[Trajectory], pred: Callable[[Trajectory], bool]) -> int:
    return sum(1 for t in items if pred(t))


def _rank(result: VerifyResult) -> tuple[int, int, int]:
    """Lower is better. PASS wins; among FAILs, fewer failed labels win."""
    if result.ok:
        return (0, 0, 0)
    if result.status == "ERROR":
        return (3, 99, 0)
    n_fail = len(result.failed_assertions)
    step = result.failing_step if result.failing_step is not None else 99
    return (1, n_fail, step)


@contextmanager
def _with_temperature(generator: Generator, value: float | None) -> Iterator[None]:
    old = getattr(generator, "temperature", None)
    if value is not None and isinstance(old, float):
        generator.temperature = value  # type: ignore[attr-defined]
    try:
        yield
    finally:
        if value is not None and isinstance(old, float):
            generator.temperature = old  # type: ignore[attr-defined]


def run_block(
    *,
    dut_path: Path,
    spec_path: Path,
    top: str,
    generator: Generator,
    workdir: Path,
    depth: int = 20,
    timeout_s: float = 300.0,
    max_feedback: int = MAX_FEEDBACK,
    candidates: int = 1,
    extra_files: tuple[Path, ...] = (),
    on_attempt: Callable[[Attempt], None] | None = None,
) -> Trajectory:
    """Zero-shot + up to ``max_feedback`` repairs. ``candidates`` is sby-graded best-of-N."""
    started = time.monotonic()
    rtl = dut_path.read_text()
    spec = spec_path.read_text() if spec_path.is_file() else ""
    clean_rtl = strip_formal_blocks(rtl)
    ctx = extract_context(clean_rtl, top)
    context = ctx.render()
    user0 = zero_shot_user(module=top, spec=spec, rtl=clean_rtl, context=context)
    messages: list[Message] = [
        Message("system", system_prompt(clock=ctx.clock or "clk", reset=ctx.reset or "rst_n")),
        Message("user", user0),
    ]
    attempts: list[Attempt] = []
    kept = ""
    max_turns = 1 + max(0, max_feedback)
    n_cand = max(1, candidates)
    for turn in range(1, max_turns + 1):
        sample_temp = 0.5 if n_cand > 1 else None
        try:
            with _with_temperature(generator, sample_temp):
                raw_blocks = generate_sva_n(generator, messages, n_cand)
        except GenerateError as exc:
            result = VerifyResult(
                status="ERROR",
                exit_code=-1,
                run_dir=workdir,
                sby_log_path=None,
                trace_vcd_path=None,
                failing_step=None,
                failed_assertions=(),
                errors=(str(exc),),
                report=str(exc),
                depth=depth,
                mode="bmc",
                elapsed_s=0.0,
            )
            att = Attempt(turn, "", result)
            attempts.append(att)
            if on_attempt is not None:
                on_attempt(att)
            if turn == max_turns:
                break
            messages.append(Message("user", extract_fail_user(report=str(exc))))
            continue
        best: Attempt | None = None
        for i, raw in enumerate(raw_blocks, start=1):
            sva = merge_sva(kept, raw) if unwrap_formal(kept) else raw
            result = verify(
                dut_path,
                sva,
                top,
                depth=depth,
                timeout_s=timeout_s,
                workdir=workdir,
                run_name=f"cegar-{top}-{turn}-{i}",
                extra_files=extra_files,
            )
            att = Attempt(turn, sva, result)
            if best is None or _rank(result) < _rank(best.result):
                best = att
            if result.ok:
                break
        assert best is not None
        attempts.append(best)
        if on_attempt is not None:
            on_attempt(best)
        if best.result.ok or turn == max_turns:
            break
        failed = best.result.failed_assertions
        kept = strip_labels(best.sva, failed) if failed else ""
        repeated = len(attempts) >= 2 and set(failed) == set(attempts[-2].result.failed_assertions)
        messages.append(Message("assistant", best.sva))
        if failed and unwrap_formal(kept):
            repair = slot_repair_user(
                kept_sva=kept,
                previous_sva=best.sva,
                status=best.result.status,
                report=best.result.report,
                failed_assertions=failed,
            )
        else:
            repair = refinement_user(
                previous_sva=best.sva,
                status=best.result.status,
                report=best.result.report,
                failed_assertions=failed,
                repeated=repeated,
            )
        messages.append(Message("user", repair))
    return Trajectory(
        block=dut_path.parent.name,
        top=top,
        prompt=user0,
        attempts=tuple(attempts),
        elapsed_s=time.monotonic() - started,
    )


def run_suite(
    blocks: Sequence[Path],
    *,
    generator: Generator,
    workdir: Path,
    depth: int = 20,
    timeout_s: float = 300.0,
    max_feedback: int = MAX_FEEDBACK,
    candidates: int = 1,
    on_attempt: Callable[[str, Attempt], None] | None = None,
) -> SuiteReport:
    """Run :func:`run_block` over every golden directory in ``blocks``."""
    report = SuiteReport()
    for block in blocks:
        top = block.name
        dut = block / f"{top}.sv"
        spec = block / f"{top}.spec.md"
        if not dut.is_file():
            continue

        def _hook(att: Attempt, name: str = top) -> None:
            if on_attempt is not None:
                on_attempt(name, att)

        report.trajectories.append(
            run_block(
                dut_path=dut,
                spec_path=spec,
                top=top,
                generator=generator,
                workdir=workdir,
                depth=depth,
                timeout_s=timeout_s,
                max_feedback=max_feedback,
                candidates=candidates,
                on_attempt=_hook,
            )
        )
    return report
