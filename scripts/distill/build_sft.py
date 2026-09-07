#!/usr/bin/env python3
"""Build a kill-weighted SFT dataset from ``fsyn generate`` trajectory logs.

Reads the ``type == "sft"`` rows that ``fsyn generate`` appends to its ``--dataset`` JSONL
(one per DUT whose winner is a real proof) and writes chat-format JSONL that TRL's
``SFTTrainer`` consumes directly::

    {"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}],
     "weight": 0.875, "block": "counter", "kill_rate": 0.875, ...}

The verifier is the filter and the weight (RWOPD-style), never a judge:

* drop rows whose cover run failed or that carry lowered-but-skipped statements
  (``skipped > 0``): those blocks contain text the grader did not prove;
* drop rows below ``--min-kill`` when mutation was scored;
* unscored rows (``kill_rate`` is null: the mutator found no site) get ``--unscored-weight``;
* ``weight`` = kill rate, floored at ``--floor`` so a proven-but-weak block still teaches
  syntax; ``--repeat K`` additionally duplicates each row ``round(weight * K)`` times for
  trainers without per-sample weights.

Stdlib only. Example::

    python scripts/distill/build_sft.py \
        ~/.cache/formalsynapse/output/regen-*/golden.jsonl \
        ~/.cache/formalsynapse/output/regen-*/assertllm2.jsonl \
        --min-kill 0.25 --out work/distill/train.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _rows(paths: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("type") == "sft":
                    row["_source"] = str(path)
                    out.append(row)
    return out


def _keep(row: dict[str, Any], *, min_kill: float, require_cover: bool) -> str | None:
    """Return a drop reason, or None to keep."""
    if not row.get("sva", "").strip():
        return "empty"
    if row.get("skipped", 0):
        return "skipped-statements"
    if require_cover and row.get("cover_ok") is False:
        return "cover-fail"
    if row.get("asserts") == 0:  # None = lowering info not recorded; the proven flag already gated it
        return "no-asserts"
    rate = row.get("kill_rate")
    if rate is not None and rate + 1e-12 < min_kill:
        return "below-min-kill"
    return None


def _weight(row: dict[str, Any], *, floor: float, unscored: float) -> float:
    rate = row.get("kill_rate")
    if rate is None:
        return unscored
    return max(floor, float(rate))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", type=Path, help="fsyn generate --dataset JSONL file(s)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--min-kill", type=float, default=0.25, help="drop scored rows below this (default 0.25)")
    ap.add_argument("--floor", type=float, default=0.25, help="minimum weight for kept rows (default 0.25)")
    ap.add_argument("--unscored-weight", type=float, default=0.5, help="weight when mutation was not scored")
    ap.add_argument("--no-require-cover", action="store_true", help="keep rows whose cover run failed")
    ap.add_argument("--repeat", type=int, default=0, help="duplicate rows round(weight*K) times (0 = off)")
    ap.add_argument("--dedupe", action="store_true", help="keep the best-weighted row per block")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    rows = _rows(args.logs)
    drops: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    for row in rows:
        why = _keep(row, min_kill=args.min_kill, require_cover=not args.no_require_cover)
        if why:
            drops[why] += 1
            continue
        kept.append(row)
    if args.dedupe:
        best: dict[str, dict[str, Any]] = {}
        for row in kept:
            w = _weight(row, floor=args.floor, unscored=args.unscored_weight)
            cur = best.get(row["block"])
            if cur is None or w > _weight(cur, floor=args.floor, unscored=args.unscored_weight):
                best[row["block"]] = row
        drops["dedupe"] += len(kept) - len(best)
        kept = list(best.values())

    out_rows: list[dict[str, Any]] = []
    for row in kept:
        w = _weight(row, floor=args.floor, unscored=args.unscored_weight)
        messages = []
        if row.get("system"):
            messages.append({"role": "system", "content": row["system"]})
        messages.append({"role": "user", "content": row["prompt"]})
        messages.append({"role": "assistant", "content": row["sva"].strip() + "\n"})
        example = {
            "messages": messages,
            "weight": round(w, 4),
            "block": row["block"],
            "top": row.get("top"),
            "kill_rate": row.get("kill_rate"),
            "killed": row.get("killed"),
            "valid_mutants": row.get("valid_mutants"),
            "turn": row.get("turn"),
            "source": row["_source"],
        }
        copies = max(1, round(w * args.repeat)) if args.repeat > 0 else 1
        out_rows.extend([example] * copies)
    random.Random(args.seed).shuffle(out_rows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for ex in out_rows:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    total_w = sum(ex["weight"] for ex in out_rows)
    print(f"read {len(rows)} sft rows from {len(args.logs)} log(s)")
    for why, n in sorted(drops.items()):
        print(f"  dropped {n:4d}  {why}")
    print(f"kept {len(kept)} rows -> {len(out_rows)} examples (mean weight {total_w / max(1, len(out_rows)):.2f})")
    print(f"wrote {args.out}")
    return 0 if out_rows else 1


if __name__ == "__main__":
    sys.exit(main())
