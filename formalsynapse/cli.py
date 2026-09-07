"""Command-line interface: ``fsyn doctor | smoke | verify | golden | gate | trace``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from formalsynapse import __version__, toolchain
from formalsynapse.assertllm2 import Design as AssertLLM2Design
from formalsynapse.assertllm2 import default_root as assertllm2_default_root
from formalsynapse.assertllm2 import discover as discover_assertllm2
from formalsynapse.assertllm2 import load_mutants as load_assertllm2_mutants
from formalsynapse.assertllm2 import select as select_assertllm2
from formalsynapse.assertllm2 import write_index as write_assertllm2_index
from formalsynapse.cegar import MAX_FEEDBACK, Attempt, run_block, run_suite
from formalsynapse.dataset import log_suite, log_trajectory
from formalsynapse.design_context import dual_edge_clock_reason_from_files
from formalsynapse.gate import GateReport, evaluate, write_report
from formalsynapse.generator import VLLMGenerator, ping
from formalsynapse.mutate import Mutant
from formalsynapse.paths import default_output_dir, default_workdir, golden_dir, smoke_dir
from formalsynapse.sby_config import Frontend, Mode
from formalsynapse.vcd_parser import VcdError, build_report, load_vcd
from formalsynapse.verify_harness import VerifyResult, verify


def _print(msg: str) -> None:
    sys.stdout.write(msg if msg.endswith("\n") else msg + "\n")
    sys.stdout.flush()


def _print_suite_attempt(name: str, att: Attempt) -> None:
    _print(f"[{name}] turn {att.turn}: {att.result.summary()}")
    if att.vacuous:
        _print(f"  vacuous (antecedent unreachable): {', '.join(att.vacuous)}")
    if att.valid_mutants:
        _print(f"  kill={att.killed}/{att.valid_mutants} ({100.0 * att.kill_rate:.0f}%)")
    if att.result.status == "ERROR" and att.result.errors:
        _print(f"  {att.result.errors[0][:400]}")


@dataclass(frozen=True)
class GenerateJob:
    """One generate target: golden block or AssertLLM2 design."""

    name: str
    dut: Path
    spec: Path
    top: str
    extras: tuple[Path, ...] = ()
    mutants: tuple[Mutant, ...] | None = None


def cmd_doctor(args: argparse.Namespace) -> int:
    report = toolchain.inspect_toolchain(probe_slang=True)
    _print(report.render())
    gen = VLLMGenerator()
    base = getattr(args, "llm_url", None) or gen.base_url
    ok, detail = ping(base, api_key=gen.api_key if gen.api_key != "EMPTY" else None)
    _print(f"LLM endpoint: {base} ({'up: ' + detail if ok else 'DOWN: ' + detail})")
    return 0 if report.ok else 1


def _run_named(
    name: str,
    dut: Path,
    sva: Path,
    top: str,
    *,
    mode: Mode = "bmc",
    depth: int = 20,
    engine: str = "smtbmc z3",
    timeout_s: float = 300.0,
    workdir: Path,
    frontend: Frontend = "verilog",
    expect: str | None = None,
) -> VerifyResult:
    result = verify(
        dut,
        sva.read_text(),
        top,
        depth=depth,
        engine=engine,
        mode=mode,
        timeout_s=timeout_s,
        workdir=workdir,
        run_name=name,
        frontend=frontend,
    )
    _print(f"[{name}] {result.summary()}")
    if result.report and result.status != "PASS":
        _print(result.report)
    if expect is not None and result.status != expect:
        _print(f"[{name}] expected {expect}, got {result.status}")
    return result


def cmd_smoke(args: argparse.Namespace) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    root = smoke_dir() / "counter"
    dut = root / "counter.sv"
    workdir = Path(args.workdir)
    pass_r = _run_named(
        "smoke-pass",
        dut,
        root / "counter.sva.sv",
        "counter",
        depth=args.depth,
        timeout_s=args.timeout,
        workdir=workdir,
        expect="PASS",
    )
    fail_r = _run_named(
        "smoke-fail",
        dut,
        root / "counter_fail.sva.sv",
        "counter",
        depth=args.depth,
        timeout_s=args.timeout,
        workdir=workdir,
        expect="FAIL",
    )
    ok = pass_r.status == "PASS" and fail_r.status == "FAIL" and fail_r.trace_vcd_path is not None
    if fail_r.trace_vcd_path is None:
        _print("[smoke-fail] missing trace.vcd")
    _print("smoke: " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    result = verify(
        Path(args.dut),
        Path(args.sva).read_text(),
        args.top,
        depth=args.depth,
        engine=args.engine,
        mode=args.mode,
        timeout_s=args.timeout,
        workdir=Path(args.workdir),
        frontend=args.frontend,
        extra_files=_unique_extras(tuple(Path(p) for p in (args.extra or []))),
        strict=bool(args.strict),
        reset_assume=not args.no_reset_assume,
        reset_cycles=max(1, int(args.reset_cycles)),
    )
    _print(result.summary())
    _print(result.report)
    if result.lowered is not None and result.lowered.reset is None and not args.no_reset_assume:
        _print("note: no reset port recognised; BMC starts from an arbitrary register state")
    return 0 if result.ok else 1


def _golden_blocks() -> list[Path]:
    root = golden_dir()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / f"{p.name}.sv").is_file())


def cmd_golden(args: argparse.Namespace) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    blocks = _golden_blocks()
    if not blocks:
        _print(f"no golden blocks under {golden_dir()}")
        return 1
    workdir = Path(args.workdir)
    rows: list[tuple[str, VerifyResult]] = []
    failed = 0
    for block in blocks:
        name = block.name
        dut = block / f"{name}.sv"
        sva = block / f"{name}.sva.sv"
        if not sva.is_file():
            _print(f"[{name}] missing {sva.name}")
            failed += 1
            continue
        result = _run_named(
            f"golden-{name}",
            dut,
            sva,
            name,
            depth=args.depth,
            engine=args.engine,
            timeout_s=args.timeout,
            workdir=workdir,
            expect="PASS",
        )
        rows.append((name, result))
        if result.status != "PASS":
            failed += 1
        if args.cover:
            cover = _run_named(
                f"golden-{name}-cover",
                dut,
                sva,
                name,
                mode="cover",
                depth=args.depth,
                engine=args.engine,
                timeout_s=args.timeout,
                workdir=workdir,
            )
            if cover.status != "PASS":
                failed += 1
    name_w = max((len(n) for n, _ in rows), default=6)
    _print("")
    _print(f"{'block':<{name_w}}  status   rc  step  time")
    for name, result in rows:
        step = "-" if result.failing_step is None else str(result.failing_step)
        _print(f"{name:<{name_w}}  {result.status:<7} {result.exit_code:>2}  {step:>4}  {result.elapsed_s:5.1f}s")
    _print(f"\ngolden: {len(rows) - failed}/{len(rows)} PASS" if rows else "golden: no results")
    return 0 if failed == 0 else 1


def cmd_trace(args: argparse.Namespace) -> int:
    path = Path(args.vcd)
    try:
        vcd = load_vcd(path)
    except (OSError, VcdError) as exc:
        _print(f"failed to read {path}: {exc}")
        return 1
    report = build_report(
        vcd,
        top=args.top,
        failing_step=args.step,
        failed_assertions=tuple(args.assertion) if args.assertion else (),
        clock=args.clock,
        include_internal=args.internal,
    )
    _print(report.render(window=args.window))
    return 0


def _llm_from_args(args: argparse.Namespace) -> VLLMGenerator:
    return VLLMGenerator(
        base_url=args.llm_url,
        model=args.llm_model,
        api_key=args.llm_key,
        max_tokens=args.max_tokens,
        guided=bool(getattr(args, "grammar", False)),
        guided_backend="grammar" if getattr(args, "grammar", False) else "off",
    )


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    gen = VLLMGenerator()
    p.add_argument("--llm-url", default=gen.base_url, help="OpenAI-compatible base URL (vLLM default)")
    p.add_argument("--llm-model", default=gen.model)
    p.add_argument("--llm-key", default=gen.api_key)
    p.add_argument(
        "--grammar",
        action="store_true",
        help="request guided_grammar (off by default; the lowerer is the syntax gate)",
    )
    p.add_argument(
        "--no-grammar",
        action="store_true",
        help="ignored; grammar is off unless --grammar (kept for old scripts)",
    )
    p.add_argument(
        "--candidates",
        type=int,
        default=8,
        help="sby-graded best-of-N samples per turn (default 8)",
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=gen.max_tokens,
        help="completion cap (keep well under the model context; 7B vLLM is 8192)",
    )
    p.add_argument("--depth", type=int, default=20)
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--workdir", type=Path, default=default_workdir())
    p.add_argument("--dataset", type=Path, default=default_output_dir() / "trajectories.jsonl")


def _generate_jobs(args: argparse.Namespace) -> list[GenerateJob] | None:
    extras = tuple(Path(p) for p in (args.extra or []))
    max_mutants = int(getattr(args, "max_mutants", 8))
    if getattr(args, "suite", None) == "assertllm2":
        root = Path(args.root) if getattr(args, "root", None) else assertllm2_default_root()
        if root is None:
            _print("AssertLLM2 root required: --root or FSYN_ASSERTLLM2_ROOT")
            return None
        try:
            designs = discover_assertllm2(root)
        except FileNotFoundError as exc:
            _print(str(exc))
            return None
        wanted = select_assertllm2(designs, only=args.only, open_only=args.only is None)
        jobs: list[GenerateJob] = []
        for design in wanted:
            if not design.open_ok:
                _print(f"[{design.key}] skip: {design.skip_reason}")
                continue
            if design.spec is None:
                _print(f"[{design.key}] missing spec.md")
                continue
            jobs.append(
                GenerateJob(
                    name=design.name,
                    dut=design.dut,
                    spec=design.spec,
                    top=design.top,
                    extras=_unique_extras(design.extras + extras),
                    mutants=load_assertllm2_mutants(design, max_mutants=max_mutants),
                )
            )
        if not jobs:
            _print("no open AssertLLM2 designs selected")
            return None
        return jobs
    if args.block is None:
        _print("pass a block path or --suite assertllm2 --only <name>")
        return None
    block = Path(args.block)
    if block.is_dir():
        top = args.top or block.name
        dut = block / f"{top}.sv"
        spec = Path(args.spec) if args.spec else block / f"{top}.spec.md"
    else:
        dut = block
        top = args.top or dut.stem
        spec = Path(args.spec) if args.spec else dut.with_name(f"{top}.spec.md")
    return [GenerateJob(name=top, dut=dut, spec=spec, top=top, extras=extras)]


def cmd_generate(args: argparse.Namespace) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    jobs = _generate_jobs(args)
    if not jobs:
        return 1
    failed = 0
    min_kill = float(getattr(args, "min_kill", 0.0))
    max_mutants = int(getattr(args, "max_mutants", 8))
    for job in jobs:
        clock_skip = dual_edge_clock_reason_from_files(job.dut, *job.extras)
        if clock_skip:
            _print(f"{job.name}: skip: {clock_skip}")
            failed += 1
            continue

        def _on_attempt(att: Attempt, block: str = job.name) -> None:
            _print_suite_attempt(block, att)

        traj = run_block(
            dut_path=job.dut,
            spec_path=job.spec,
            top=job.top,
            generator=_llm_from_args(args),
            workdir=Path(args.workdir),
            depth=args.depth,
            timeout_s=args.timeout,
            max_feedback=args.max_feedback,
            candidates=args.candidates,
            extra_files=job.extras,
            min_kill=min_kill,
            max_mutants=max_mutants,
            mutants=job.mutants,
            on_attempt=_on_attempt,
        )
        chosen = traj.winner
        kill_note = ""
        if chosen is not None and chosen.valid_mutants:
            kill_note = f", kill {chosen.killed}/{chosen.valid_mutants} ({100.0 * chosen.kill_rate:.0f}%)"
        if chosen is not None and chosen.turn != traj.turns:
            kill_note += f" (kept turn {chosen.turn})"
        _print(f"{traj.block}: {traj.status} in {traj.turns} turn(s), {traj.elapsed_s:.1f}s{kill_note}")
        n = log_trajectory(Path(args.dataset), traj)
        if n:
            _print(f"logged {n} row(s) -> {args.dataset}")
        last_sva = chosen.sva if chosen is not None else ""
        shallow = chosen is not None and not chosen.meets_kill(min_kill)
        if not traj.ok or shallow or (min_kill > 0.0 and chosen is not None and not chosen.proven):
            failed += 1
        if args.out:
            dest = Path(args.out)
            if dest.is_dir() or (len(jobs) > 1 and dest.suffix == ""):
                dest.mkdir(parents=True, exist_ok=True)
                dest = dest / f"{job.name}.sva.sv"
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(last_sva)
            verdict = "proven" if traj.ok else f"NOT proven ({traj.status})"
            _print(f"wrote {dest} [{verdict}]")
    return 0 if failed == 0 else 1


def cmd_baseline(args: argparse.Namespace) -> int:
    return _run_suite_cmd(args, max_feedback=0, label="baseline (zero-shot)")


def cmd_cegar(args: argparse.Namespace) -> int:
    return _run_suite_cmd(args, max_feedback=args.max_feedback, label="CEGAR")


def _run_suite_cmd(args: argparse.Namespace, *, max_feedback: int, label: str) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    blocks = _golden_blocks()
    if args.only:
        wanted = set(args.only)
        blocks = [b for b in blocks if b.name in wanted]
    if not blocks:
        _print("no golden blocks selected")
        return 1
    _print(f"{label}: {len(blocks)} block(s), max_feedback={max_feedback}")
    report = run_suite(
        blocks,
        generator=_llm_from_args(args),
        workdir=Path(args.workdir),
        depth=args.depth,
        timeout_s=args.timeout,
        max_feedback=max_feedback,
        candidates=args.candidates,
        on_attempt=_print_suite_attempt,
    )
    _print("")
    _print(report.render())
    n = log_suite(Path(args.dataset), report)
    _print(f"\nlogged {n} row(s) -> {args.dataset}")
    return 0 if report.cegar_rate == 1.0 else 1


def _cover_cell(report: GateReport) -> str:
    if report.cover is None:
        return "n/a"
    return report.cover.status


def _print_gate_table(rows: list[GateReport]) -> None:
    name_w = max((len(r.block) for r in rows), default=6)
    _print(
        f"{'block':<{name_w}}  prove    cover    coi   mutants  killed  kill%"
    )
    for report in rows:
        _print(
            f"{report.block:<{name_w}}  "
            f"{report.prove.status:<7}  "
            f"{_cover_cell(report):<7}  "
            f"{report.coi.coverage:4.2f}  "
            f"{len(report.valid_mutants):>7}  "
            f"{report.killed:>6}  "
            f"{100.0 * report.kill_rate:5.1f}%"
        )


def _unique_extras(files: tuple[Path, ...]) -> tuple[Path, ...]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in files:
        if path.name in seen:
            continue
        seen.add(path.name)
        out.append(path)
    return tuple(out)


def _resolve_sva(args: argparse.Namespace, name: str, default: Path | None) -> Path | None:
    if args.sva is None:
        return default
    path = Path(args.sva)
    if path.is_dir():
        return path / f"{name}.sva.sv"
    return path


def _print_assertllm2_index(rows: list[AssertLLM2Design]) -> None:
    if not rows:
        _print("no AssertLLM2 designs found")
        return
    key_w = max(len(d.key) for d in rows)
    _print(f"{'key':<{key_w}}  lang      top            mutants  skip")
    for design in rows:
        skip = design.skip_reason or "-"
        _print(
            f"{design.key:<{key_w}}  {design.language:<8}  {design.top:<14}  "
            f"{len(design.mutants):>7}  {skip}"
        )
    _print(
        f"\n{sum(1 for d in rows if d.open_ok)}/{len(rows)} open-ok, "
        f"{sum(1 for d in rows if d.mutants)} with shipped mutants "
        "(sby numbers, not JasperGold)"
    )


def cmd_gate(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    if args.suite == "assertllm2" and args.list:
        root = Path(args.root) if args.root else assertllm2_default_root()
        if root is None:
            _print("AssertLLM2 root required: --root or FSYN_ASSERTLLM2_ROOT")
            return 1
        try:
            designs = discover_assertllm2(root)
        except FileNotFoundError as exc:
            _print(str(exc))
            return 1
        chosen = select_assertllm2(designs, only=args.only, open_only=False)
        write_assertllm2_index(chosen, workdir / "assertllm2_index.json")
        _print_assertllm2_index(list(chosen))
        _print(f"index -> {workdir / 'assertllm2_index.json'}")
        return 0

    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1

    rows: list[GateReport] = []
    failed = 0
    not_evaluated: list[str] = []

    if args.suite == "assertllm2":
        root = Path(args.root) if args.root else assertllm2_default_root()
        if root is None:
            _print("AssertLLM2 root required: --root or FSYN_ASSERTLLM2_ROOT")
            return 1
        try:
            designs = discover_assertllm2(root)
        except FileNotFoundError as exc:
            _print(str(exc))
            return 1
        chosen = select_assertllm2(designs, only=args.only, open_only=not args.include_skipped)
        if args.only:
            for design in select_assertllm2(designs, only=args.only, open_only=False):
                if not design.open_ok and not args.include_skipped:
                    _print(f"[{design.key}] skip: {design.skip_reason}")
        if not chosen:
            _print("no open AssertLLM2 designs selected")
            return 1
        for design in chosen:
            sva = _resolve_sva(args, design.name, None)
            if sva is None or not sva.is_file():
                _print(f"[{design.key}] missing candidate SVA (pass --sva file or directory)")
                not_evaluated.append(design.name)
                continue
            shipped = load_assertllm2_mutants(design, max_mutants=args.max_mutants)
            block_dir = workdir / f"gate-{design.name}"
            _print(
                f"[{design.key}] prove + cover + COI + {len(shipped)} shipped mutants"
            )
            report = evaluate(
                design.dut,
                sva,
                design.top,
                block_dir,
                block=design.key,
                depth=args.depth,
                timeout_s=args.timeout,
                max_mutants=args.max_mutants,
                run_cover=not args.no_cover,
                mutants=shipped,
                extra_files=_unique_extras(design.extras),
                strict=not args.trusted,
            )
            write_report(report, block_dir / "gate.json")
            _print_gate_report(design.key, report)
            rows.append(report)
            if not report.passed or report.kill_rate < args.min_kill:
                failed += 1
    else:
        pairs: list[tuple[str, Path, Path, str]] = []
        if args.dut is not None:
            dut = Path(args.dut)
            sva_path = _resolve_sva(args, dut.stem, dut.with_name(f"{dut.stem}.sva.sv"))
            top = args.top or dut.stem
            if sva_path is None or not sva_path.is_file():
                _print(f"missing SVA file {sva_path}")
                return 1
            pairs.append((top, dut, sva_path, top))
        else:
            blocks = _golden_blocks()
            if args.only:
                wanted = set(args.only)
                blocks = [b for b in blocks if b.name in wanted]
            if not blocks:
                _print("no golden blocks selected")
                return 1
            for block in blocks:
                name = block.name
                dut = block / f"{name}.sv"
                sva_path = _resolve_sva(args, name, block / f"{name}.sva.sv")
                if sva_path is None or not sva_path.is_file():
                    # A candidate directory may lack a block; report it and grade the rest.
                    _print(f"[{name}] missing {sva_path}")
                    not_evaluated.append(name)
                    continue
                pairs.append((name, dut, sva_path, name))

        extras = _unique_extras(tuple(Path(p) for p in (args.extra or [])))
        for name, dut, sva_path, top in pairs:
            clock_skip = dual_edge_clock_reason_from_files(dut, *extras)
            if clock_skip:
                _print(f"[{name}] skip: {clock_skip}")
                not_evaluated.append(name)
                continue
            block_dir = workdir / f"gate-{name}"
            _print(f"[{name}] prove + cover + COI + mutation ({args.max_mutants} mutants)")
            report = evaluate(
                dut,
                sva_path,
                top,
                block_dir,
                block=name,
                depth=args.depth,
                timeout_s=args.timeout,
                max_mutants=args.max_mutants,
                run_cover=not args.no_cover,
                extra_files=extras,
                strict=not args.trusted,
            )
            write_report(report, block_dir / "gate.json")
            _print_gate_report(name, report)
            rows.append(report)
            if not report.passed or report.kill_rate < args.min_kill:
                failed += 1

    if rows:
        _print("")
        _print_gate_table(rows)
        summary = f"\ngate: {len(rows) - failed}/{len(rows)} PASS (prove+cover, min-kill {args.min_kill:.0%})"
        if not_evaluated:
            summary += f"; {len(not_evaluated)} not evaluated: {', '.join(not_evaluated)}"
        _print(summary)
    if args.markdown is not None and rows:
        md = Path(args.markdown)
        md.parent.mkdir(parents=True, exist_ok=True)
        with md.open("a", encoding="utf-8") as fh:
            fh.write(gate_markdown(rows, min_kill=args.min_kill, failed=failed))
        _print(f"markdown summary -> {md}")
    return 0 if failed == 0 and not not_evaluated else 1


def _print_gate_report(name: str, report: GateReport) -> None:
    _print(
        f"[{name}] prove={report.prove.status} cover={_cover_cell(report)} "
        f"coi={report.coi.coverage:.2f} kill={report.killed}/{len(report.valid_mutants)} "
        f"({100.0 * report.kill_rate:.0f}%)"
    )
    if report.prove.status != "PASS" and report.prove.errors:
        _print(f"  {report.prove.errors[0][:400]}")
    if report.vacuous:
        _print(f"  vacuous (antecedent unreachable): {', '.join(report.vacuous)}")
    for item in report.skipped:
        _print(f"  not proven (skipped by lowerer): {item[:200]}")
    for outcome in report.mutants:
        mark = "KILL" if outcome.killed else outcome.result.status
        _print(f"  {outcome.mutant.name}: {mark}  {outcome.mutant.description}")


def gate_markdown(rows: list[GateReport], *, min_kill: float, failed: int) -> str:
    """GitHub step-summary table for a gate run."""
    lines = [
        f"### fsyn gate: {len(rows) - failed}/{len(rows)} PASS (prove+cover, min-kill {min_kill:.0%})",
        "",
        "| block | prove | cover | coi | mutants | killed | kill% | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        notes: list[str] = []
        if r.vacuous:
            notes.append("vacuous: " + ", ".join(r.vacuous))
        if r.skipped:
            notes.append(f"{len(r.skipped)} skipped")
        if r.prove.lowered is not None and r.prove.lowered.reset is None:
            notes.append("no reset assumed")
        lines.append(
            f"| {r.block} | {r.prove.status} | {_cover_cell(r)} | {r.coi.coverage:.2f} | "
            f"{len(r.valid_mutants)} | {r.killed} | {100.0 * r.kill_rate:.0f}% | {'; '.join(notes)} |"
        )
    return "\n".join(lines) + "\n\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fsyn",
        description="FormalSynapse verification harness (Yosys + SymbiYosys + SMT).",
    )
    parser.add_argument("--version", action="version", version=f"fsyn {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    doctor = sub.add_parser("doctor", help="report toolchain paths, versions, slang, and LLM endpoint")
    doctor.add_argument("--llm-url", default=None)

    smoke = sub.add_parser("smoke", help="run the passing and failing smoke-counter checks")
    smoke.add_argument("--depth", type=int, default=12)
    smoke.add_argument("--timeout", type=float, default=120.0)
    smoke.add_argument("--workdir", type=Path, default=default_workdir())

    verify_p = sub.add_parser("verify", help="lower a candidate SVA, inject it, and run sby")
    verify_p.add_argument("--dut", required=True, type=Path)
    verify_p.add_argument("--top", required=True)
    verify_p.add_argument("--sva", required=True, type=Path)
    verify_p.add_argument("--depth", type=int, default=20)
    verify_p.add_argument("--engine", default="smtbmc z3")
    verify_p.add_argument("--mode", choices=("bmc", "prove", "cover"), default="bmc")
    verify_p.add_argument("--timeout", type=float, default=300.0)
    verify_p.add_argument("--workdir", type=Path, default=default_workdir())
    verify_p.add_argument("--frontend", choices=("verilog", "slang"), default="verilog")
    verify_p.add_argument("--extra", action="append", type=Path, default=None, help="extra compile units (repeatable)")
    verify_p.add_argument(
        "--strict",
        action="store_true",
        help="reject assume property / fsyn:verbatim (what the grader applies to model output)",
    )
    verify_p.add_argument(
        "--no-reset-assume",
        action="store_true",
        help="do not hold the DUT reset active at step 0 (default: assume it, like a testbench)",
    )
    verify_p.add_argument("--reset-cycles", type=int, default=1, help="cycles the reset is held (default 1)")

    golden = sub.add_parser("golden", help="prove every block in benchmarks/golden")
    golden.add_argument("--depth", type=int, default=20)
    golden.add_argument("--engine", default="smtbmc z3")
    golden.add_argument("--timeout", type=float, default=300.0)
    golden.add_argument("--workdir", type=Path, default=default_workdir())
    golden.add_argument("--cover", action="store_true", help="also run cover (antecedent reachability)")

    gate = sub.add_parser(
        "gate",
        help="prove + cover/vacuity + COI + mutation kill (golden suite or one pair)",
    )
    gate.add_argument(
        "--suite",
        choices=("golden", "assertllm2"),
        default="golden",
        help="golden (default) or an AssertLLM2 checkout",
    )
    gate.add_argument(
        "--root",
        type=Path,
        default=None,
        help="AssertLLM2 repo root (or set FSYN_ASSERTLLM2_ROOT)",
    )
    gate.add_argument("--list", action="store_true", help="index a suite and exit (no sby)")
    gate.add_argument(
        "--include-skipped",
        action="store_true",
        help="do not drop VHDL / broken AssertLLM2 dirs from selection",
    )
    gate.add_argument("--dut", type=Path, default=None, help="DUT .sv (omit to run benchmarks/golden)")
    gate.add_argument("--sva", type=Path, default=None, help="candidate SVA file, or a directory of <name>.sva.sv")
    gate.add_argument("--top", default=None)
    gate.add_argument("--extra", action="append", type=Path, default=None, help="extra compile units (repeatable)")
    gate.add_argument("--only", nargs="*", default=None, help="restrict to these block / design names")
    gate.add_argument("--depth", type=int, default=20)
    gate.add_argument("--timeout", type=float, default=60.0)
    gate.add_argument("--workdir", type=Path, default=default_workdir())
    gate.add_argument("--max-mutants", type=int, default=8)
    gate.add_argument(
        "--min-kill",
        type=float,
        default=0.0,
        help="fail the gate if mutation kill rate is below this (default 0.0)",
    )
    gate.add_argument("--no-cover", action="store_true", help="skip the cover/vacuity sby run")
    gate.add_argument(
        "--trusted",
        action="store_true",
        help="allow assume property / fsyn:verbatim in the SVA (hand-written blocks only; "
        "never for model output)",
    )
    gate.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="append a markdown summary table here (e.g. $GITHUB_STEP_SUMMARY)",
    )

    trace = sub.add_parser("trace", help="pretty-print a yosys-smtbmc counterexample VCD")
    trace.add_argument("vcd", type=Path)
    trace.add_argument("--top", required=True)
    trace.add_argument("--clock", default="clk")
    trace.add_argument("--step", type=int, default=None)
    trace.add_argument("--window", type=int, default=4)
    trace.add_argument("--assertion", action="append", default=[])
    trace.add_argument("--internal", action="store_true")

    gen = sub.add_parser("generate", help="zero-shot + CEGAR on one block or an AssertLLM2 design")
    gen.add_argument("block", type=Path, nargs="?", default=None, help="golden block directory or DUT .sv")
    gen.add_argument("--top", default=None)
    gen.add_argument("--spec", type=Path, default=None)
    gen.add_argument("--extra", action="append", type=Path, default=None, help="extra compile units (repeatable)")
    gen.add_argument(
        "--suite",
        choices=("assertllm2",),
        default=None,
        help="generate against an AssertLLM2 checkout instead of a local block",
    )
    gen.add_argument("--root", type=Path, default=None, help="AssertLLM2 repo root (or FSYN_ASSERTLLM2_ROOT)")
    gen.add_argument("--only", nargs="*", default=None, help="restrict AssertLLM2 generation to these names")
    gen.add_argument("--max-feedback", type=int, default=MAX_FEEDBACK)
    gen.add_argument("--out", type=Path, default=None, help="write the last SVA attempt here")
    gen.add_argument(
        "--min-kill",
        type=float,
        default=0.25,
        help="keep sampling after a BMC PASS until mutation kill reaches this (default 0.25)",
    )
    gen.add_argument(
        "--max-mutants",
        type=int,
        default=8,
        help="mutants scored after each BMC PASS (default 8)",
    )
    _add_llm_args(gen)

    base = sub.add_parser("baseline", help="zero-shot pass rate on benchmarks/golden (no CEGAR)")
    base.add_argument("--only", nargs="*", default=None, help="restrict to these block names")
    _add_llm_args(base)

    cegar = sub.add_parser("cegar", help="3-turn CEGAR loop on benchmarks/golden")
    cegar.add_argument("--only", nargs="*", default=None)
    cegar.add_argument("--max-feedback", type=int, default=MAX_FEEDBACK)
    _add_llm_args(cegar)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "doctor": cmd_doctor,
        "smoke": cmd_smoke,
        "verify": cmd_verify,
        "golden": cmd_golden,
        "gate": cmd_gate,
        "trace": cmd_trace,
        "generate": cmd_generate,
        "baseline": cmd_baseline,
        "cegar": cmd_cegar,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
