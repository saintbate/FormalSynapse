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


def log_trajectory(path: Path, traj: Trajectory) -> int:
    return append_jsonl(path, traj.dataset_rows())
