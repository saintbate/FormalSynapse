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

First kill-aware generate on `versatile_counter` (`--min-kill 0.5`, 8×4,
`--max-tokens 2048`, 63 min): turn 1 BMC PASS with two covers only, kill 0/8;
the loop stayed open. Turn 2 extract ERROR (`<think>`). Turn 3 lowering
ERROR. Turn 4 FAIL (`a_clear_priority_m0003` encoded the mutant, not the
golden). `--out` now writes the winning attempt (highest prove+kill), not the last
turn. A cover-only BMC PASS is incomplete: CEGAR does not score kill on it
and asks for labeled asserts before treating the block as proven. Did not
clear 50% kill. The gate as a filter is working; the model is not yet
writing killing properties from mutant descriptions. `kill_miss_user`
now shows a golden-minus / mutant-plus hunk and tells the model to
assert the golden side — the previous wording ("fail on these mutants")
made CodeV encode the bug (`q_next == 1`).

Third generate (`--min-kill 0.5`, golden-vs-mutant prompt, 46 min): turn 1
FAIL `a_non_clear_priority` (`qi - 1` width); turn 2 extract ERROR; turn 3
PASS with `clear |-> q_next == 0` and `!clear |-> q_next == (qi - \`CNT_LENGTH'd1)`.
Gate prove PASS, but all 8 shipped mutants ERROR — the SVA still names
`CNT_LENGTH`, which the mutant copies do not define. Generate exited 0
because zero valid mutants currently counted as meeting `--min-kill`.
The lowerer now expands `` `define `` from the DUT/includes (so
`` `CNT_LENGTH'd1 `` becomes ``4'd1``) and rejects leftover macros.
`--min-kill > 0` treats “mutants exist but none scored” as incomplete.
Re-gating that same SVA after the expand: prove PASS, kill 3/8 (38%) —
clear-swap, clear-to-1, and rewind decrement. Below the 50% bar, but
honest and better than the 25% one-property block.

Four more AssertLLM2 designs at `--min-kill 0.25`, 8×4, BMC 20 (sby only,
not JasperGold). `versatile_counter` is the prior row for comparison:

| design | prove | kill | note |
|---|---|---|---|
| versatile_counter | PASS | 3/8 (38%) | meets 25% |
| uart | FAIL | n/a | `a_idle_ser_out` @ step 1 on every repair |
| present_cipher_encryption_core | PASS | 1/7 (14%) | below bar |
| programmable_interval_timer | FAIL | n/a | `a_counter_reset` @ step 2 |
| pwm | ERROR | n/a | Yosys: `i_clk` both polarities in `down_clocking_odd` |

CEGAR prove 2/5, `--min-kill 0.25` met 1/5. First-turn extract (`<think>`
only) is still common. `pwm` is a DUT/frontend skip, not a model miss —
open Yosys BMC cannot clock that block. Generate now skips dual-edge
clocks before sampling. Stray-tick labels (`` `a_counter_reset: ``) are
rewritten to ordinary labels so leftover-macro does not fire.

### Weeks 5–6

`fsyn` unit CI (ruff, mypy, pytest minus `toolchain`/`llm`) is in
`.github/workflows/ci.yml`. A Nix flake (`flake.nix`) provides the Python
package and a `nix develop` shell with nixpkgs yosys/sby/z3; the full OSS
CAD Suite (yosys-slang) remains `scripts/install_toolchain.sh`.
`.github/workflows/gate.yml` runs `fsyn gate` on the golden `counter` and
on `ben-marshall/uart` `uart_tx` (RTL checked out in CI, SVA in
`examples/uart_tx/`). External repos checkout this tree into `.fsyn` and
call `.github/actions/fsyn-gate` with their DUT/SVA/top.

### Grader audit (after weeks 5–6)

A code review of the lowerer and harness found four ways an `sby PASS` could be
reported without a proof. All numbers above this section were produced by the
old grader and are **not comparable** with anything produced after it; treat
them as history, not baselines.

1. **No reset assumption.** BMC started from an arbitrary register state, so
   properties without `disable iff` (or with `|->` on the first sample) failed
   spuriously, and covers were "reached" from pre-reset garbage. The lowerer now
   emits `initial assume(<reset active>)` (name and polarity from
   `design_context`, override with `--no-reset-assume` / `--reset-cycles`) and
   every check is gated to cycle >= 1.
2. **Guard depth summed instead of maxed.** `req |=> ##[0:10] gnt` was gated to
   cycle >= 21 and never checked at BMC depth 20 (silent PASS). Fixed; test
   `test_range_consequent_is_checked_within_bound` is the regression.
3. **Skipped asserts became a PASS.** Unsupported constructs were turned into
   comments; a block whose every assert was skipped still reported PASS. Now
   `verify` returns ERROR ("nothing to prove") and skipped items are listed in
   every report. CEGAR's `proven` uses the lowered assert list, not a regex.
4. **`assume` / verbatim could make anything pass.** `strict` lowering (default
   for CEGAR and `fsyn gate`; `--trusted` opts out for hand-written blocks)
   rejects `assume property` and `fsyn:verbatim` in candidates.

Vacuity is no longer opt-in: every assert with an implication gets an
auto-cover of its antecedent (`<label>__cov`), the gate always runs cover
when there is one, and CEGAR runs cover after each BMC PASS, strips vacuous
labels in Python, and asks for replacements. `Trajectory.ok` means proven and
non-vacuous, not "sby exited 0".

Also fixed: cover-label parsing (was reading the module name), `__c1`
labels mapping back to their source label for strip, mutants on the DUT's
actual clock/reset lines, NBA vs relational `<=` after `if (...)`, string
literals in comment stripping / `endmodule` search / mutation, `` `else ``
branches of `ifdef FORMAL` kept when stripping, `$fatal` / `begin...end`
action blocks in `strip_labels`, `#(...)` headers and `a, b, c` declarations
in `design_context`, missing `--extra` files as ERROR, sby's own timeout +
process-group kill, and CEGAR history trimmed to the last exchange.

Re-gating the saved outputs with the fixed grader found two more holes:

5. **Undeclared signals were free wires.** Yosys turns an identifier the DUT
   does not declare into an implicitly declared, undriven wire. The AssertLLM2
   `present_cipher_encryption_core` and `uart` candidates wrote
   `disable iff (!rst_n)` against DUTs that have no `rst_n` (the cipher has no
   reset at all; the uart's is `reset`, active-high). `verify` now reads Yosys's
   "implicitly declared" warnings and reports ERROR for any such name that the
   SVA introduced. The prompt no longer invents `rst_n` either: a DUT without a
   reset port gets a system prompt that forbids `disable iff`, and the lowerer's
   fallback disable is the DUT's real reset or nothing.
6. **Kills were scored on a failed prove.** An SVA that FAILs on the golden RTL
   fails on every mutant; `fsyn gate` was printing that as `kill=8/8`. Mutation
   is now scored only after prove PASS and cover PASS.

Also: `design_context` reads non-ANSI port lists (`module m(a, b); input a;`),
which is how the uart's reset had gone undetected; `fsyn gate --sva <dir>` grades
the blocks it has and lists the missing ones instead of aborting.

Re-gate with the fixed grader (BMC 20, 8 mutants, `--min-kill 0.25`):

| set | result |
|---|---|
| golden hand-written SVA, 10 blocks | 10/10 prove+cover PASS; kill 100% on 7, `rr_arbiter` 8/8, `onehot_fsm` 1/2, `spi_master` 2/6 |
| `examples/uart_tx` (ben-marshall/uart) | prove+cover PASS, kill 0/7 (frame-timing mutants are ~5k cycles out at 9600 baud; CI gates prove+cover only) |
| saved CodeV-14B golden candidates, 8 of 10 | 2/8 pass the bar (`counter` 8/8, `edge_detector` 1/1); `onehot_fsm` proves but kills 0/2; 5 ERROR on prose/undefined-macro output |
| saved AssertLLM2 candidates, 4 | `versatile_counter` PASS 3/8 (38%) unchanged; `uart` and `present_cipher` now ERROR (invented `rst_n`, item 5); `programmable_interval_timer` FAIL (`a_counter_reset` @ step 2, genuine) |

The old `present_cipher` "PASS 1/7" row above is therefore withdrawn. The 14B
candidate files were saved before extraction was fixed and contain the model's
reasoning; the gate's extractor recovers the block when there is one.

### Weeks 7–9

Re-run the golden CEGAR / AssertLLM2 generate rows with the fixed grader before
any distillation. RWOPD-style distillation with kill-weighted filtering on one
H200. Release the adapter and the dataset.

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
