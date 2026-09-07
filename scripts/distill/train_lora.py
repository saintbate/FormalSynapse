#!/usr/bin/env python3
"""LoRA SFT of a student on verifier-filtered CodeV-SVA-14B trajectories (one GPU).

Run on the GPU box, not in the repo's stdlib-only package::

    pip install -r scripts/distill/requirements.txt
    python scripts/distill/train_lora.py --data work/distill/train.jsonl \
        --model Qwen/Qwen2.5-Coder-7B-Instruct --out work/distill/adapter

Input is the chat-format JSONL from ``build_sft.py``. Per-example ``weight`` is honoured by
a weighted sampler (rows are drawn proportionally to weight, with replacement, for
``--epochs`` passes of the dataset size) so kill-weighted filtering shapes the gradient
without needing a custom loss. Loss is on the completion (the SVA block) only.

Nothing here judges SVA. The only supervision is text the sby grader proved and scored.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="train.jsonl from build_sft.py")
    ap.add_argument("--eval", type=Path, default=None, help="optional held-out JSONL (same format)")
    ap.add_argument("--model", default="Qwen/Qwen2.5-Coder-7B-Instruct")
    ap.add_argument("--out", type=Path, default=Path("work/distill/adapter"))
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--alpha", type=int, default=64)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--max-length", type=int, default=6144)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-weighted-sampling", action="store_true", help="ignore the weight column")
    args = ap.parse_args()

    # Heavy imports after arg parsing so --help works without a GPU environment.
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(ln) for ln in args.data.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not rows:
        raise SystemExit(f"no rows in {args.data}")
    rng = random.Random(args.seed)
    if not args.no_weighted_sampling:
        # Draw len(rows) * epochs examples proportionally to weight, then train one epoch over
        # the draw. Equivalent in expectation to per-sample loss weights, and trainer-agnostic.
        weights = [max(1e-3, float(r.get("weight", 1.0))) for r in rows]
        n_draw = int(round(len(rows) * args.epochs))
        rows = rng.choices(rows, weights=weights, k=n_draw)
        epochs = 1.0
    else:
        epochs = args.epochs
    def _split(r: dict[str, object]) -> dict[str, object]:
        # Conversational prompt-completion: TRL masks the prompt (completion_only_loss) without
        # needing a chat template with generation markers, which Qwen2.5 does not ship.
        msgs = r["messages"]
        assert isinstance(msgs, list) and msgs[-1]["role"] == "assistant"
        return {"prompt": msgs[:-1], "completion": [msgs[-1]]}

    train_ds = Dataset.from_list([_split(r) for r in rows])
    eval_ds = None
    if args.eval is not None:
        eval_rows = [json.loads(ln) for ln in args.eval.read_text(encoding="utf-8").splitlines() if ln.strip()]
        eval_ds = Dataset.from_list([_split(r) for r in eval_rows])

    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map="cuda")

    lora = LoraConfig(
        r=args.rank,
        lora_alpha=args.alpha,
        lora_dropout=args.dropout,
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    cfg = SFTConfig(
        output_dir=str(args.out),
        num_train_epochs=epochs,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        bf16=True,
        max_length=args.max_length,
        completion_only_loss=True,
        logging_steps=5,
        save_strategy="epoch",
        eval_strategy="epoch" if eval_ds is not None else "no",
        report_to="none",
        seed=args.seed,
    )
    trainer = SFTTrainer(
        model=model,
        args=cfg,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        peft_config=lora,
        processing_class=tok,
    )
    trainer.train()
    trainer.save_model(str(args.out))
    tok.save_pretrained(str(args.out))
    (args.out / "distill_args.json").write_text(json.dumps(vars(args), default=str, indent=2))
    print(f"adapter saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
