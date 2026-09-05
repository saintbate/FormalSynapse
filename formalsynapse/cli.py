"""Command-line interface: ``fsyn doctor | smoke | verify | golden | gate | trace``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from formalsynapse import __version__, toolchain
from formalsynapse.cegar import MAX_FEEDBACK, Attempt, run_block, run_suite
from formalsynapse.dataset import log_suite, log_trajectory
from formalsynapse.gate import GateReport, evaluate, write_report
from formalsynapse.generator import VLLMGenerator, ping
from formalsynapse.paths import default_output_dir, default_workdir, golden_dir, smoke_dir
from formalsynapse.sby_config import Frontend, Mode
from formalsynapse.vcd_parser import VcdError, build_report, load_vcd
from formalsynapse.verify_harness import VerifyResult, verify


def _print(msg: str) -> None:
    sys.stdout.write(msg if msg.endswith("\n") else msg + "\n")


def _print_suite_attempt(name: str, att: Attempt) -> None:
    _print(f"[{name}] turn {att.turn}: {att.result.summary()}")
    if att.result.status == "ERROR" and att.result.errors:
        _print(f"  {att.result.errors[0][:400]}")


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
    )
    _print(result.summary())
    _print(result.report)
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


def cmd_generate(args: argparse.Namespace) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    block = Path(args.block)
    if block.is_dir():
        top = args.top or block.name
        dut = block / f"{top}.sv"
        spec = Path(args.spec) if args.spec else block / f"{top}.spec.md"
    else:
        dut = block
        top = args.top or dut.stem
        spec = Path(args.spec) if args.spec else dut.with_name(f"{top}.spec.md")
    traj = run_block(
        dut_path=dut,
        spec_path=spec,
        top=top,
        generator=_llm_from_args(args),
        workdir=Path(args.workdir),
        depth=args.depth,
        timeout_s=args.timeout,
        max_feedback=args.max_feedback,
        candidates=args.candidates,
        on_attempt=lambda att: _print_suite_attempt(top, att),
    )
    _print(f"{traj.block}: {traj.status} in {traj.turns} turn(s), {traj.elapsed_s:.1f}s")
    if args.out:
        Path(args.out).write_text(traj.attempts[-1].sva if traj.attempts else "")
    n = log_trajectory(Path(args.dataset), traj)
    if n:
        _print(f"logged {n} heal row(s) -> {args.dataset}")
    return 0 if traj.ok else 1


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


def cmd_gate(args: argparse.Namespace) -> int:
    if not toolchain.have_sby():
        _print("sby/yosys/z3 not found; run scripts/install_toolchain.sh && source scripts/env.sh")
        return 1
    workdir = Path(args.workdir)
    pairs: list[tuple[str, Path, Path, str]] = []
    if args.dut is not None:
        dut = Path(args.dut)
        sva = Path(args.sva) if args.sva else dut.with_name(f"{dut.stem}.sva.sv")
        top = args.top or dut.stem
        if not sva.is_file():
            _print(f"missing SVA file {sva}")
            return 1
        pairs.append((top, dut, sva, top))
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
            sva = block / f"{name}.sva.sv"
            if not sva.is_file():
                _print(f"[{name}] missing {sva.name}")
                return 1
            pairs.append((name, dut, sva, name))

    rows: list[GateReport] = []
    failed = 0
    for name, dut, sva, top in pairs:
        block_dir = workdir / f"gate-{name}"
        _print(f"[{name}] prove + cover + COI + mutation ({args.max_mutants} mutants)")
        report = evaluate(
            dut,
            sva,
            top,
            block_dir,
            block=name,
            depth=args.depth,
            timeout_s=args.timeout,
            max_mutants=args.max_mutants,
            run_cover=not args.no_cover,
        )
        write_report(report, block_dir / "gate.json")
        _print(
            f"[{name}] prove={report.prove.status} cover={_cover_cell(report)} "
            f"coi={report.coi.coverage:.2f} kill={report.killed}/{len(report.valid_mutants)} "
            f"({100.0 * report.kill_rate:.0f}%)"
        )
        if report.prove.status != "PASS" and report.prove.errors:
            _print(f"  {report.prove.errors[0][:400]}")
        for outcome in report.mutants:
            mark = "KILL" if outcome.killed else outcome.result.status
            _print(f"  {outcome.mutant.name}: {mark}  {outcome.mutant.description}")
        rows.append(report)
        if not report.passed or report.kill_rate < args.min_kill:
            failed += 1

    _print("")
    _print_gate_table(rows)
    _print(
        f"\ngate: {len(rows) - failed}/{len(rows)} PASS "
        f"(prove+cover, min-kill {args.min_kill:.0%})"
    )
    return 0 if failed == 0 else 1


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
    gate.add_argument("--dut", type=Path, default=None, help="DUT .sv (omit to run benchmarks/golden)")
    gate.add_argument("--sva", type=Path, default=None, help="candidate SVA (default: <dut-stem>.sva.sv)")
    gate.add_argument("--top", default=None)
    gate.add_argument("--only", nargs="*", default=None, help="restrict golden blocks to these names")
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

    trace = sub.add_parser("trace", help="pretty-print a yosys-smtbmc counterexample VCD")
    trace.add_argument("vcd", type=Path)
    trace.add_argument("--top", required=True)
    trace.add_argument("--clock", default="clk")
    trace.add_argument("--step", type=int, default=None)
    trace.add_argument("--window", type=int, default=4)
    trace.add_argument("--assertion", action="append", default=[])
    trace.add_argument("--internal", action="store_true")

    gen = sub.add_parser("generate", help="zero-shot + CEGAR on one block")
    gen.add_argument("block", type=Path, help="golden block directory or DUT .sv")
    gen.add_argument("--top", default=None)
    gen.add_argument("--spec", type=Path, default=None)
    gen.add_argument("--max-feedback", type=int, default=MAX_FEEDBACK)
    gen.add_argument("--out", type=Path, default=None, help="write the last SVA attempt here")
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
