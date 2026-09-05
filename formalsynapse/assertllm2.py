"""Ingest an AssertLLM2 checkout without vendoring the 83 designs.

Layout (hkust-zhiyao/AssertLLM2)::

    designs/<CATEGORY>/<design_name>/{<top>.v|.sv|.vhd, spec.md, include/, mutations/}

Shipped mutants live in ``mutations/mutants/M_XXXX/`` with a
``mutations/mutation_summary.json`` sidecar. VHDL is discovered and skipped
for the open Yosys grader. Point ``--root`` or ``FSYN_ASSERTLLM2_ROOT`` at
the clone; do not copy designs into ``benchmarks/golden``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from formalsynapse.mutate import Mutant
from formalsynapse.sva_inject import module_names

HDL_EXTS = (".v", ".sv", ".vhd", ".vhdl")
VHDL_EXTS = (".vhd", ".vhdl")
_OP_IN_LOG = re.compile(r"applied\.\s+([A-Za-z0-9_()><-]+)")
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ShippedMutant:
    """One AssertLLM2 cached mutant (top RTL only; includes stay golden)."""

    mutant_id: str
    operator: str
    description: str
    top_rtl: Path


@dataclass(frozen=True)
class Design:
    """One AssertLLM2 design as seen by the open grader."""

    key: str
    category: str
    name: str
    top: str
    dut: Path
    spec: Path | None
    extras: tuple[Path, ...]
    language: str
    mutants: tuple[ShippedMutant, ...]
    skip_reason: str | None

    @property
    def open_ok(self) -> bool:
        return self.skip_reason is None


def default_root() -> Path | None:
    raw = os.environ.get("FSYN_ASSERTLLM2_ROOT", "").strip()
    return Path(raw).expanduser() if raw else None


def designs_root(root: Path) -> Path:
    """Accept either the repo root or the ``designs/`` directory."""
    if (root / "designs").is_dir():
        return root / "designs"
    return root


def _slug(name: str) -> str:
    return _SLUG.sub("_", name.strip().lower()).strip("_")


def _top_rtl_files(design_dir: Path) -> list[Path]:
    return sorted(
        p for p in design_dir.iterdir() if p.is_file() and p.suffix.lower() in HDL_EXTS
    )


def _include_files(design_dir: Path) -> tuple[Path, ...]:
    include = design_dir / "include"
    if not include.is_dir():
        return ()
    found = [p for p in include.rglob("*") if p.is_file() and p.suffix.lower() in HDL_EXTS]
    return tuple(sorted(found))


def _detect_top(dut: Path) -> str:
    if dut.suffix.lower() in VHDL_EXTS:
        return dut.stem
    names = module_names(dut.read_text(encoding="utf-8", errors="replace"))
    if dut.stem in names:
        return dut.stem
    return names[0] if names else dut.stem


def _parse_summary(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("mutants") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("mutant_id", "")).strip()
        if not mid:
            continue
        log = str(row.get("log", "")).strip()
        match = _OP_IN_LOG.search(log)
        out[mid] = {"log": log, "operator": match.group(1) if match else "shipped"}
    return out


def _shipped_mutants(design_dir: Path, top_name: str) -> tuple[ShippedMutant, ...]:
    root = design_dir / "mutations" / "mutants"
    if not root.is_dir():
        return ()
    summary = _parse_summary(design_dir / "mutations" / "mutation_summary.json")
    found: list[ShippedMutant] = []
    for mid_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        candidate = mid_dir / top_name
        if not candidate.is_file():
            rtl = _top_rtl_files(mid_dir)
            if not rtl:
                continue
            candidate = rtl[0]
        meta = summary.get(mid_dir.name, {})
        found.append(
            ShippedMutant(
                mutant_id=mid_dir.name,
                operator=meta.get("operator", "shipped"),
                description=meta.get("log") or f"shipped mutant {mid_dir.name}",
                top_rtl=candidate,
            )
        )
    return tuple(found)


def load_mutants(design: Design, *, max_mutants: int = 20) -> tuple[Mutant, ...]:
    """Read shipped mutant RTL into :class:`Mutant` objects for ``evaluate``."""
    out: list[Mutant] = []
    for shipped in design.mutants[: max(0, max_mutants)]:
        out.append(
            Mutant(
                name=shipped.mutant_id,
                operator=shipped.operator,
                description=shipped.description,
                rtl=shipped.top_rtl.read_text(encoding="utf-8", errors="replace"),
            )
        )
    return tuple(out)


def discover(root: Path) -> tuple[Design, ...]:
    """Walk an AssertLLM2 tree and return every design dir that looks like one."""
    designs = designs_root(root)
    if not designs.is_dir():
        raise FileNotFoundError(f"AssertLLM2 designs directory not found: {designs}")
    found: list[Design] = []
    for category_dir in sorted(p for p in designs.iterdir() if p.is_dir()):
        for design_dir in sorted(p for p in category_dir.iterdir() if p.is_dir()):
            rtl_files = _top_rtl_files(design_dir)
            spec = design_dir / "spec.md"
            spec_path = spec if spec.is_file() else None
            key = f"assertllm2/{_slug(category_dir.name)}/{_slug(design_dir.name)}"
            skip: str | None = None
            dut: Path | None = rtl_files[0] if rtl_files else None
            language = "unknown"
            top = design_dir.name
            extras: tuple[Path, ...] = ()
            mutants: tuple[ShippedMutant, ...] = ()
            if dut is None:
                skip = "no top-level HDL"
            elif len(rtl_files) > 1:
                skip = f"multiple top-level HDL files: {', '.join(p.name for p in rtl_files)}"
            else:
                language = "vhdl" if dut.suffix.lower() in VHDL_EXTS else "verilog"
                top = _detect_top(dut)
                extras = _include_files(design_dir)
                mutants = _shipped_mutants(design_dir, dut.name)
                if language == "vhdl":
                    skip = "VHDL; Yosys open grader is Verilog/SV only"
                elif spec_path is None:
                    skip = "missing spec.md"
            found.append(
                Design(
                    key=key,
                    category=category_dir.name,
                    name=design_dir.name,
                    top=top,
                    dut=dut if dut is not None else design_dir,
                    spec=spec_path,
                    extras=extras,
                    language=language,
                    mutants=mutants,
                    skip_reason=skip,
                )
            )
    return tuple(found)


def _matches(design: Design, wanted: str) -> bool:
    needle = wanted.strip()
    return (
        design.key == needle
        or design.name == needle
        or design.key.endswith("/" + _slug(needle))
    )


def select(
    designs: tuple[Design, ...],
    *,
    only: Sequence[str] | None = None,
    open_only: bool = True,
) -> tuple[Design, ...]:
    """Filter by key or design name. ``open_only`` drops VHDL and broken dirs."""
    chosen = designs
    if only:
        chosen = tuple(d for d in chosen if any(_matches(d, w) for w in only))
    if open_only:
        chosen = tuple(d for d in chosen if d.open_ok)
    return chosen


def index_json(designs: tuple[Design, ...]) -> dict[str, object]:
    return {
        "source": "https://github.com/hkust-zhiyao/AssertLLM2",
        "grader": "sby (open, not JasperGold)",
        "designs": len(designs),
        "open_ok": sum(1 for d in designs if d.open_ok),
        "with_mutants": sum(1 for d in designs if d.mutants),
        "entries": [
            {
                "key": d.key,
                "category": d.category,
                "name": d.name,
                "top": d.top,
                "language": d.language,
                "dut": str(d.dut),
                "spec": None if d.spec is None else str(d.spec),
                "extras": [str(p) for p in d.extras],
                "mutants": len(d.mutants),
                "skip_reason": d.skip_reason,
            }
            for d in designs
        ],
    }


def write_index(designs: tuple[Design, ...], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index_json(designs), indent=2) + "\n", encoding="utf-8")
