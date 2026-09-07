"""Formal sign-off gate: prove, cover/vacuity, cheap COI, mutation kill.

The LLM never grades. Every score comes from ``sby`` or from a static
intersection of identifiers already present in the DUT.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from formalsynapse.design_context import DesignContext, extract_context
from formalsynapse.mutate import Mutant, generate_mutants
from formalsynapse.sva_grammar import ExtractError, extract_sva
from formalsynapse.sva_lower import LowerError, blank_comments, lower
from formalsynapse.verify_harness import VerifyResult, verify

_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*)\b")
_SV_KW = frozenset(
    {
        "always",
        "always_comb",
        "always_ff",
        "and",
        "assert",
        "assign",
        "assume",
        "automatic",
        "begin",
        "bit",
        "case",
        "casex",
        "casez",
        "cover",
        "default",
        "disable",
        "else",
        "end",
        "endcase",
        "endfunction",
        "endmodule",
        "endproperty",
        "endtask",
        "enum",
        "for",
        "forever",
        "function",
        "generate",
        "genvar",
        "if",
        "iff",
        "ifdef",
        "ifndef",
        "initial",
        "input",
        "integer",
        "localparam",
        "logic",
        "module",
        "nand",
        "negedge",
        "nor",
        "not",
        "or",
        "output",
        "parameter",
        "posedge",
        "property",
        "reg",
        "repeat",
        "return",
        "signed",
        "task",
        "typedef",
        "unique",
        "unsigned",
        "wire",
        "xor",
    }
)


@dataclass(frozen=True)
class CoiReport:
    """Identifier overlap between the SVA and the DUT signal set."""

    design_signals: tuple[str, ...]
    sva_signals: tuple[str, ...]
    overlap: tuple[str, ...]
    coverage: float


@dataclass(frozen=True)
class MutantOutcome:
    """One mutant after ``sby``."""

    mutant: Mutant
    result: VerifyResult

    @property
    def killed(self) -> bool:
        return self.result.status == "FAIL"


@dataclass(frozen=True)
class KillReport:
    """Mutation-kill scores for one SVA. Used by the gate and by kill-aware CEGAR."""

    mutants: tuple[MutantOutcome, ...]

    @property
    def valid_mutants(self) -> tuple[MutantOutcome, ...]:
        return tuple(m for m in self.mutants if m.result.status in {"PASS", "FAIL"})

    @property
    def killed(self) -> int:
        return sum(1 for m in self.valid_mutants if m.killed)

    @property
    def kill_rate(self) -> float:
        valid = self.valid_mutants
        if not valid:
            return 0.0
        return self.killed / len(valid)

    @property
    def survivors(self) -> tuple[MutantOutcome, ...]:
        return tuple(m for m in self.valid_mutants if not m.killed)


@dataclass(frozen=True)
class GateReport:
    """Four-check gate result for one DUT + SVA pair."""

    block: str
    prove: VerifyResult
    cover: VerifyResult | None
    coi: CoiReport
    mutants: tuple[MutantOutcome, ...]

    @property
    def _kill(self) -> KillReport:
        return KillReport(self.mutants)

    @property
    def valid_mutants(self) -> tuple[MutantOutcome, ...]:
        return self._kill.valid_mutants

    @property
    def killed(self) -> int:
        return self._kill.killed

    @property
    def kill_rate(self) -> float:
        return self._kill.kill_rate

    @property
    def vacuity_ok(self) -> bool | None:
        if self.cover is None:
            return None
        return self.cover.status == "PASS"

    @property
    def vacuous(self) -> tuple[str, ...]:
        """Assert labels whose antecedent was never reachable within the bound."""
        return self.cover.vacuous_assertions if self.cover is not None else ()

    @property
    def skipped(self) -> tuple[str, ...]:
        """Statements the lowerer dropped; they are not part of the proof."""
        return self.prove.skipped

    @property
    def params(self) -> tuple[tuple[str, str], ...]:
        """Parameter overrides the DUT was elaborated with; the whole gate holds only for them."""
        return self.prove.params

    @property
    def passed(self) -> bool:
        prove_ok = self.prove.status == "PASS"
        cover_ok = self.vacuity_ok is not False
        return prove_ok and cover_ok


def _identifiers(text: str) -> set[str]:
    names: set[str] = set()
    for match in _IDENT.finditer(blank_comments(text, strings=True)):
        name = match.group(1)
        if name in _SV_KW or name.startswith("$"):
            continue
        names.add(name)
    return names


def _interesting(ctx: DesignContext) -> set[str]:
    names = {p.name for p in ctx.ports} | {i.name for i in ctx.internals}
    names |= set(ctx.constants)
    for skip in (ctx.clock, ctx.reset):
        if skip:
            names.discard(skip)
    return names


def coi_report(rtl: str, top: str, sva: str) -> CoiReport:
    """Cheap cone-of-influence: SVA identifiers ∩ DUT ports/regs."""
    ctx = extract_context(rtl, top)
    design = _interesting(ctx)
    used = _identifiers(sva) & (
        {p.name for p in ctx.ports}
        | {i.name for i in ctx.internals}
        | set(ctx.constants)
        | {n for n in (ctx.clock, ctx.reset) if n}
    )
    overlap = sorted(used & design)
    coverage = (len(overlap) / len(design)) if design else 1.0
    return CoiReport(
        design_signals=tuple(sorted(design)),
        sva_signals=tuple(sorted(used)),
        overlap=tuple(overlap),
        coverage=coverage,
    )


def _sva_has_cover(sva: str) -> bool:
    return bool(re.search(r"\bcover\b", blank_comments(sva, strings=True)))


def _unwrap_model_output(sva_text: str) -> str:
    """Saved candidates may carry fences, ``<think>`` wrappers or prose; extract the block.

    A block that already lowers to at least one assert is returned unchanged, so hand-written
    files are never rewritten. Only when lowering fails (or proves nothing) is the model-output
    extractor tried, and only if the extracted block lowers to an assert is it used; otherwise
    the original text, and its error, is what the gate reports.
    """
    try:
        if lower(sva_text).proves_something:
            return sva_text
    except LowerError:
        pass
    try:
        extracted = extract_sva(sva_text)
        if not lower(extracted).proves_something:
            return sva_text
    except (ExtractError, LowerError):
        return sva_text
    return extracted


def _has_cover_checks(prove: VerifyResult, sva_text: str) -> bool:
    """True when a cover run has something to reach (auto-covers included)."""
    if prove.lowered is not None:
        return any(a.kind == "cover" for a in prove.lowered.assertions)
    return _sva_has_cover(sva_text)


def score_kill(
    dut: Path,
    sva_text: str,
    top: str,
    workdir: Path,
    *,
    max_mutants: int = 8,
    mutants: Sequence[Mutant] | None = None,
    extra_files: tuple[Path, ...] = (),
    depth: int = 20,
    timeout_s: float = 60.0,
    clock: str = "clk",
    mutant_root: Path | None = None,
    strict: bool = False,
    params: tuple[tuple[str, str], ...] = (),
) -> KillReport:
    """Run the SVA against each mutant. A FAIL is a kill; a PASS is a miss."""
    rtl = dut.read_text(encoding="utf-8")
    if mutants is not None:
        chosen = tuple(mutants)
    else:
        ctx = extract_context(rtl, top)
        protected = frozenset(n for n in (ctx.clock, ctx.reset) if n)
        chosen = generate_mutants(rtl, max_mutants=max_mutants, protected=protected)
    chosen = chosen[: max(0, max_mutants)]
    root = mutant_root if mutant_root is not None else workdir / "mutants"
    outcomes: list[MutantOutcome] = []
    for mutant in chosen:
        mutant_dir = root / mutant.name
        mutant_dir.mkdir(parents=True, exist_ok=True)
        mutant_dut = mutant_dir / f"{top}.sv"
        mutant_dut.write_text(mutant.rtl, encoding="utf-8")
        result = verify(
            mutant_dut,
            sva_text,
            top,
            mode="bmc",
            depth=depth,
            timeout_s=timeout_s,
            workdir=workdir,
            run_name=f"mutant-{mutant.name}",
            extra_files=extra_files,
            clock=clock,
            strict=strict,
            params=params,
        )
        outcomes.append(MutantOutcome(mutant=mutant, result=result))
    return KillReport(mutants=tuple(outcomes))


def evaluate(
    dut: Path,
    sva: Path | str,
    top: str,
    workdir: Path,
    *,
    block: str = "",
    depth: int = 20,
    timeout_s: float = 60.0,
    max_mutants: int = 8,
    run_cover: bool = True,
    mutants: Sequence[Mutant] | None = None,
    extra_files: tuple[Path, ...] = (),
    clock: str = "clk",
    strict: bool = False,
    params: tuple[tuple[str, str], ...] = (),
) -> GateReport:
    """Run prove + cover (vacuity) + COI + mutation kill on one pair.

    Every assert's antecedent gets an automatic cover, so the cover run is always meaningful
    when the block has an implication: an unreachable antecedent fails the gate. ``mutants``
    overrides the cheap local mutator (used for AssertLLM2 shipped mutants). ``strict``
    rejects ``assume``/verbatim (use it for anything a model wrote). ``params`` overrides
    top-level parameters for prove, cover and every mutant alike; the report carries them.
    """
    rtl = dut.read_text(encoding="utf-8")
    sva_text = sva.read_text(encoding="utf-8") if isinstance(sva, Path) else sva
    sva_text = _unwrap_model_output(sva_text)
    label = block or top
    prove = verify(
        dut,
        sva_text,
        top,
        mode="bmc",
        depth=depth,
        timeout_s=timeout_s,
        workdir=workdir,
        run_name="prove",
        extra_files=extra_files,
        clock=clock,
        strict=strict,
        params=params,
    )
    cover_result: VerifyResult | None = None
    if run_cover and prove.status == "PASS" and _has_cover_checks(prove, sva_text):
        cover_result = verify(
            dut,
            sva_text,
            top,
            mode="cover",
            depth=depth,
            timeout_s=timeout_s,
            workdir=workdir,
            run_name="cover",
            extra_files=extra_files,
            clock=clock,
            strict=strict,
            params=params,
        )
    coi = coi_report(rtl, top, sva_text)
    # An SVA that fails on the golden RTL fails on every mutant too; those are not kills.
    # Vacuous asserts cannot kill anything either, so kill is scored on real proofs only.
    scorable = prove.status == "PASS" and (cover_result is None or cover_result.status == "PASS")
    outcomes: tuple[MutantOutcome, ...] = ()
    if scorable:
        kill = score_kill(
            dut,
            sva_text,
            top,
            workdir,
            max_mutants=max_mutants,
            mutants=mutants,
            extra_files=extra_files,
            depth=depth,
            timeout_s=timeout_s,
            clock=clock,
            strict=strict,
            params=params,
        )
        outcomes = kill.mutants
    return GateReport(
        block=label,
        prove=prove,
        cover=cover_result,
        coi=coi,
        mutants=outcomes,
    )


def report_json(report: GateReport) -> dict[str, object]:
    """JSON-serialisable view of a :class:`GateReport`."""
    return {
        "block": report.block,
        "passed": report.passed,
        "prove": report.prove.status,
        "cover": None if report.cover is None else report.cover.status,
        "vacuity_ok": report.vacuity_ok,
        "vacuous": list(report.vacuous),
        "unreached_covers": list(report.cover.unreached_covers) if report.cover is not None else [],
        "skipped": list(report.skipped),
        "reset_assumed": report.prove.lowered.reset if report.prove.lowered is not None else None,
        "params": dict(report.params),
        "coi": asdict(report.coi),
        "mutants": len(report.mutants),
        "valid_mutants": len(report.valid_mutants),
        "killed": report.killed,
        "kill_rate": report.kill_rate,
        "mutant_detail": [
            {
                "name": o.mutant.name,
                "operator": o.mutant.operator,
                "description": o.mutant.description,
                "status": o.result.status,
                "killed": o.killed,
            }
            for o in report.mutants
        ],
    }


def write_report(report: GateReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report_json(report), indent=2) + "\n", encoding="utf-8")
