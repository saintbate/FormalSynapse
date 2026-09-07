"""Append-only JSONL log of CEGAR trajectories (Phase 4 RLVR seed)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from formalsynapse.cegar import SuiteReport, Trajectory


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    """Append ``rows`` as JSON lines. Returns the number written."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def log_suite(path: Path, report: SuiteReport) -> int:
    """Write heal rows plus a one-line suite summary."""
    rows: list[dict[str, Any]] = []
    for traj in report.trajectories:
        rows.extend(traj.dataset_rows())
    rows.append(
        {
            "type": "suite_summary",
            "blocks": len(report.trajectories),
            "syntactic_rate": report.syntactic_rate,
            "first_pass_rate": report.first_pass_rate,
            "cegar_rate": report.cegar_rate,
            "heal_rate": report.heal_rate,
        }
    )
    return append_jsonl(path, rows)


def sft_row(traj: Trajectory) -> dict[str, Any] | None:
    """One distillation example per trajectory: the prompt pair and the winning SVA.

    Only emitted when the winner is a real proof (asserts lowered, non-vacuous). ``kill_rate``
    is ``None`` when mutation was not scored, so a filter can distinguish "unscored" from 0%.
    The verifier's evidence travels with the row; weighting is the trainer's decision
    (see ``scripts/distill/build_sft.py``).
    """
    chosen = traj.winner
    if chosen is None or not chosen.proven:
        return None
    lowered = chosen.result.lowered
    return {
        "type": "sft",
        "block": traj.block,
        "top": traj.top,
        "system": traj.system,
        "prompt": traj.prompt,
        "sva": chosen.sva,
        "turn": chosen.turn,
        "turns": traj.turns,
        "first_pass": traj.first_pass,
        "asserts": sum(1 for a in lowered.assertions if a.kind == "assert") if lowered is not None else None,
        "skipped": len(chosen.result.skipped),
        "cover_ok": chosen.cover.ok if chosen.cover is not None else None,
        "killed": chosen.killed,
        "valid_mutants": chosen.valid_mutants,
        "kill_rate": chosen.kill_rate if chosen.valid_mutants else None,
    }


def log_trajectory(path: Path, traj: Trajectory) -> int:
    rows = list(traj.dataset_rows())
    chosen = traj.winner
    sft = sft_row(traj)
    if sft is not None:
        rows.append(sft)
    rows.append(
        {
            "type": "trajectory",
            "block": traj.block,
            "status": traj.status,
            "turns": traj.turns,
            "ok": traj.ok,
            "winner_turn": chosen.turn if chosen is not None else 0,
            "killed": chosen.killed if chosen is not None else 0,
            "valid_mutants": chosen.valid_mutants if chosen is not None else 0,
            "kill_rate": chosen.kill_rate if chosen is not None else None,
        }
    )
    return append_jsonl(path, rows)
