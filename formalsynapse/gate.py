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
class GateReport:
    """Four-check gate result for one DUT + SVA pair."""

    block: str
    prove: VerifyResult
    cover: VerifyResult | None
    coi: CoiReport
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
    def vacuity_ok(self) -> bool | None:
        if self.cover is None:
            return None
        return self.cover.status == "PASS"

    @property
    def passed(self) -> bool:
        prove_ok = self.prove.status == "PASS"
        cover_ok = self.vacuity_ok is not False
        return prove_ok and cover_ok


def _identifiers(text: str) -> set[str]:
    names: set[str] = set()
    for match in _IDENT.finditer(text):
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
    return bool(re.search(r"\bcover\b", sva))


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
) -> GateReport:
    """Run prove + optional cover + COI + mutation kill on one pair.

    ``mutants`` overrides the cheap local mutator (used for AssertLLM2
    shipped mutants). Extra compile units are passed through to ``sby``.
    """
    rtl = dut.read_text(encoding="utf-8")
    sva_text = sva.read_text(encoding="utf-8") if isinstance(sva, Path) else sva
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
    )
    cover_result: VerifyResult | None = None
    if run_cover and _sva_has_cover(sva_text):
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
        )
    coi = coi_report(rtl, top, sva_text)
    chosen = tuple(mutants) if mutants is not None else generate_mutants(rtl, max_mutants=max_mutants)
    chosen = chosen[: max(0, max_mutants)]
    outcomes: list[MutantOutcome] = []
    for mutant in chosen:
        mutant_dir = workdir / "mutants" / mutant.name
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
        )
        outcomes.append(MutantOutcome(mutant=mutant, result=result))
    return GateReport(
        block=label,
        prove=prove,
        cover=cover_result,
        coi=coi,
        mutants=tuple(outcomes),
    )


def report_json(report: GateReport) -> dict[str, object]:
    """JSON-serialisable view of a :class:`GateReport`."""
    return {
        "block": report.block,
        "passed": report.passed,
        "prove": report.prove.status,
        "cover": None if report.cover is None else report.cover.status,
        "vacuity_ok": report.vacuity_ok,
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
