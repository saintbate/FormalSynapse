# FormalSynapse / OpenSVA-RL

Open-source formal sign-off for open silicon. An open-weight model proposes SystemVerilog
Assertions; Yosys + SymbiYosys + Z3 grade them. An LLM never judges correctness.

## 1. Position

Hardware verification is the expensive half of chip design. Formal is the only method that
can prove a property over all inputs, but writing SVA is a scarce skill and commercial
formal (JasperGold, VC Formal) is a six-figure seat. Open-silicon and RISC-V teams already
live on Yosys, Verilator, and GitHub Actions. They do not have Jasper, and the funded AI-EDA
wave (Cadence ChipStack, Synopsys Formal Advisor, ChipAgents, Normal Computing) is built on
top of those proprietary engines.

FormalSynapse is the formal sign-off gate for that open stack: generate assertions, prove
them with `sby`, reject vacuous or shallow proofs, and kill mutants. It is not “take over
open-source verification” (that is still mostly Verilator + cocotb) and it is not a Day-1
Jasper replacement on a TSMC 3 nm tape-out.

Solver-in-the-loop is table stakes (ProofLoop, Formal Advisor, CodeV-SVA, RWOPD). The
asymmetric pieces are:

1. **An open quality gate** — vacuity, cone-of-influence, mutation kill — runnable in CI
   with no license. AssertLLM2 needs Jasper for this; we will not.
2. **Design context before generation** — ports, internals, constants. ProofLoop doubled
   functional correctness with the same model by retrieving structure first.
3. **A CI-native form factor** — GitHub Action / Nix, not a laptop CLI.

## 2. What we will not do

- Train GRPO on a sparse `sby` PASS/FAIL reward as the product plan. RWOPD (May 2026) ran
  seven GRPO pilots plus two IPO pilots on `Qwen2.5-Coder-7B` with an open SymbiYosys+Z3
  property-equivalence checker and none beat the SFT baseline. Dense on-policy distillation
  from CodeV-SVA-14B, with the verifier as a *filter and weight*, did work (one H200,
  minutes). Phase 4 follows that recipe and adds mutation-kill to the filter.
- Default to grammar-constrained decoding. On current vLLM it produced
  `coverproperty` / `endif` junk and crashed llguidance. The lowerer plus a compile gate is
  the syntax filter; `--grammar` is opt-in.
- Treat “7B from scratch” as a requirement. Local-first is the requirement. The generator
  default is `wyt2000/CodeV-SVA-14B` (open weights, FVEval-competitive). A 7B adapter is a
  later distillation target.

## 3. Pipeline

```
[ spec + RTL ]
      |
      v
 Phase A: design context (ports, internals, constants, clk/rst)
      |
      v
 Generator (CodeV-SVA-14B / vLLM or any OpenAI-compatible host)
      |  best-of-N samples
      v
 sva_lower -> inject -> sby     <-- only grader
      |
      +-- PASS --> quality gate (vacuity / COI / mutation)   [Stage 3, next]
      |
      +-- FAIL --> strip failed labels, keep survivors,
                   regenerate only those slots, sby picks the winner
```

Repair is programmatic. Asking a 7B to “delete the failed property” does not work; we
strip the failed labels in Python and sample N replacements.

## 4. Stack

- **Formal:** OSS CAD Suite (`yosys`, `sby`, `z3`, `bitwuzla`, `boolector`). Concurrent SVA
  is authored then lowered by `formalsynapse.sva_lower` to immediate asserts. `yosys-slang`
  still rejects `assert property`; Property IR (YosysHQ, 2026) is the long-term frontend.
- **Generator:** `wyt2000/CodeV-SVA-14B` on vLLM, or OpenRouter as a stand-in. Grammar off
  by default.
- **Later training:** RWOPD-style filtered distillation from CodeV-SVA-14B, weights include
  mutation-kill from the open gate. Not 8×A100 GRPO.

## 5. Metrics

Evaluate on AssertLLM2 (83 designs + shipped mutants) and FVEval Design2SVA. Report
open-grader numbers and say they are not Jasper numbers.

| Metric | Why |
|---|---|
| Syntax (first attempt not ERROR) | compile gate |
| First-pass formal | proven on golden RTL, no repair |
| CEGAR / best-of-N pass | survivors + slot regen |
| Heal rate | first-pass fail that later proves |
| Vacuity / cover | antecedent reachable |
| Mutation kill | assertion catches a real bug |

Ten golden blocks remain the smoke suite, not the published score.

## 6. Roadmap

### Weeks 1–2 (this change)

- Grammar optional, default off.
- Default model `wyt2000/CodeV-SVA-14B`.
- Phase A design-context in the zero-shot prompt.
- Strip failed labels; best-of-N (`--candidates`, default 8); `sby` chooses.

Gate: first-pass on the 10 golden blocks should move off 0% once the 14B is on the other
end of `FSYN_LLM_BASE_URL`. Do not rent 8×A100 until that is true.

### Weeks 3–4

`fsyn gate`: reachability cover, COI, mutation kill (AssertLLM2 operator set). Ingest
AssertLLM2 designs/mutants. Publish open-grader baselines.

### Weeks 5–6

GitHub Action + Nix flake. One external repo (Ibex, Tiny Tapeout, or a riscv-formal user)
running the gate on PRs.

### Weeks 7–9

RWOPD-style distillation with kill-weighted filtering on one H200. Release the adapter and
the dataset.

## 7. Long term

Years 1–3: trusted formal gate for open hardware, RISC-V, and air-gapped research.
Years 3+: the closed-loop governor for prompt-to-RTL flows — only after the gate is
honest on mutation kill, not just `sby PASS`.

## 8. Agent rules

- Grade only through `formalsynapse.verify_harness` / `sby`.
- Local-first verification path. No proprietary API on the critical path.
- Do not edit `benchmarks/golden/**` unless asked. Scratch in `work/` or
  `~/.cache/formalsynapse`.
- `ruff check` and `mypy --strict`. Stdlib only in `formalsynapse/` unless a dependency is
  approved.
- Every `.sv` DUT: `verilator --lint-only -Wall`. Every `benchmarks/**/*.sby`: exit 0.
