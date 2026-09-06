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
      +-- PASS --> quality gate (vacuity / COI / mutation)   [fsyn gate]
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

### Weeks 3–4 (in progress)

`fsyn gate` is in the tree: BMC prove, cover/vacuity, cheap identifier COI, and
mutation kill. Mutants are written under the workdir; `benchmarks/golden/**` is
not edited.

Golden-suite open-grader baseline (BMC depth 20, 8 cheap mutants, `sby` only —
these are not JasperGold numbers):

| block | prove | cover | coi | kill |
|---|---|---|---|---|
| counter | PASS | PASS | 1.00 | 8/8 |
| edge_detector | PASS | PASS | 1.00 | 1/1 |
| gray_counter | PASS | PASS | 1.00 | 2/2 |
| onehot_fsm | PASS | PASS | 0.44 | 1/2 |
| priority_arbiter | PASS | PASS | 1.00 | 6/6 |
| rr_arbiter | PASS | PASS | 0.50 | 0/0 |
| shift_register | PASS | PASS | 1.00 | 2/2 |
| skid_buffer | PASS | PASS | 0.88 | 5/5 |
| spi_master | PASS | PASS | 0.70 | 2/6 |
| sync_fifo | PASS | PASS | 0.58 | 7/7 |

CodeV-SVA-14B on the 10 golden blocks (`sby` only, not JasperGold): first-pass
50%, CEGAR 80% (8/10 prove). `fsyn gate --sva` on those 8 survivors:

| block | prove | cover | coi | kill |
|---|---|---|---|---|
| counter | PASS | n/a | 1.00 | 8/8 |
| edge_detector | PASS | PASS | 1.00 | 1/1 |
| gray_counter | PASS | PASS | 1.00 | 0/2 |
| onehot_fsm | PASS | n/a | 0.56 | 0/2 |
| priority_arbiter | PASS | n/a | 0.50 | 0/6 |
| shift_register | PASS | PASS | 0.40 | 0/2 |
| skid_buffer | PASS | n/a | 0.50 | 1/5 |
| spi_master | PASS | n/a | 0.70 | 0/6 |

Most CEGAR PASSes are shallow: they hold on golden RTL and miss the cheap mutants.
That is why the gate exists. `rr_arbiter` and `sync_fifo` stayed ERROR.

AssertLLM2 ingest: `fsyn gate --suite assertllm2 --root <clone> --list` walks the
83-design tree, skips VHDL, and indexes shipped `mutations/mutants/M_*`. Generate
with `fsyn generate --suite assertllm2 --only <name>` (clock/reset polarity and
`include/` extras come from the DUT). Do not vendor the designs.

First open-grader generate on `versatile_counter` (CodeV-SVA-14B, BMC 20, not
JasperGold) aborted: turn 1–2 lowering ERROR; turn 3 FAILED `a_reset_deassert`;
turn 4 blew the 8192-token context.

After generate-path hardening (clip + labeled-assert extract + reset-polarity
skip) a first rerun still aborted: CodeV-SVA-14B spent turn 1 in `<think>` with
no extractable SVA, and CEGAR treated `GenerateError` as terminal. CEGAR now
continues with an extract-fail user message.

Second rerun (8 candidates, 3 feedback turns, 23 min): turn 1–2 extract ERROR
(`<think>` only); turn 3 PASS. Winning SVA is one property,
`clear |-> q_next == 0`. Gate: prove PASS, cover n/a, COI 0.17, kill 2/8
(25%) — both kills are the two shipped mutants that break clear-to-zero.
Shallow but honest: the open grader no longer reports a FAIL as a kill score.

Kill-aware CEGAR: `fsyn generate --min-kill` (default 0.25) scores mutation
kill after a BMC PASS and keeps sampling, merging new properties onto the
proven block, until the rate clears the bar or feedback turns run out.
`fsyn cegar` / `baseline` stay prove-only (`min_kill=0`) so the golden
first-pass / heal numbers stay comparable. A 0-mutant design does not spin.

### Weeks 5–6

`fsyn` unit CI (ruff, mypy, pytest minus `toolchain`/`llm`) is in
`.github/workflows/ci.yml`. Next: Nix flake for the OSS CAD Suite, then one
external repo (Ibex, Tiny Tapeout, or a riscv-formal user) running the gate on PRs.

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
