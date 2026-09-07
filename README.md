# FormalSynapse / OpenSVA-RL

Neuro-symbolic SystemVerilog Assertion (SVA) synthesis and verification. An open-weight LLM proposes
assertions; the open-source formal toolchain (Yosys + yosys-slang + SymbiYosys + Z3/Bitwuzla/Boolector)
is the referee. Full context and roadmap: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

Position: the formal sign-off gate for open silicon (Yosys/`sby` only). See
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for the revised roadmap.

This repository currently contains the harness, golden suite, CEGAR loop, Week 1–2 generator changes, and the `fsyn gate` quality checks:

- a 10-block golden benchmark suite with hand-written, formally proven SVAs (`benchmarks/golden/`)
- the Python verification bridge (`formalsynapse/`): SVA injection, `.sby` generation, `sby` execution,
  result classification, and VCD counterexample parsing
- the `fsyn` CLI
- dual-agent configuration for Cursor (`.cursorrules`) and Kilo Code (`kilo.jsonc`, `.kilo/rules/`)

## Quickstart

```bash
# 1. Install the OSS CAD Suite (yosys, sby, z3, bitwuzla, boolector, verilator, yosys-slang)
#    into $HOME/.local/opt/oss-cad-suite (outside the repo — this workspace path contains ':' )
scripts/install_toolchain.sh          # macOS arm64 by default; PLATFORM=linux-x64 for Ubuntu/WSL2
source scripts/env.sh                 # puts the suite on PATH and defines the `fsyn` wrapper

# 2. Python environment (uv)
uv sync
source .venv/bin/activate             # or prefix commands with `uv run`

# 3. Check the toolchain and run the end-to-end smoke test
fsyn doctor
fsyn smoke

# 4. Prove the whole golden suite (BMC depth 20 with Z3)
fsyn golden

# 4b. Sign-off gate: prove + cover/vacuity + COI + mutation kill
fsyn gate --only counter
# fsyn gate                         # all golden blocks
# fsyn gate --dut path/to/dut.sv --sva path/to/cand.sva.sv --top dut
# AssertLLM2 (clone separately; do not copy into benchmarks/golden):
# fsyn gate --suite assertllm2 --root ~/src/AssertLLM2 --list
# fsyn generate --suite assertllm2 --only versatile_counter --out cand.sva.sv
# fsyn gate --suite assertllm2 --root ~/src/AssertLLM2 --only versatile_counter --sva cand.sva.sv
# fsyn gate --sva ~/.cache/formalsynapse/output/cegar-14b-sva   # generated golden candidates

# 5. Verify a candidate SVA against a DUT
fsyn verify --dut benchmarks/golden/sync_fifo/sync_fifo.sv --top sync_fifo \
            --sva benchmarks/golden/sync_fifo/sync_fifo.sva.sv

# 6. Inspect a counterexample
fsyn trace work/<run>/<task>/engine_0/trace.vcd --top sync_fifo
```

## Toolchain notes

- Open-source Yosys (`read_verilog -sv -formal`) supports only immediate assertions. Concurrent SVA
  (`property`/`endproperty`, `|->`, `|=>`, `##[m:n]`, `disable iff`) is the *authoring* format
  (`.sva.sv` files). `formalsynapse.sva_lower` compiles a bounded subset of that format into
  immediate assertions before `sby` runs. The OSS CAD Suite ships `yosys-slang`, but the 2026
  builds still reject `assert property` (`SVA unsupported`). `fsyn doctor` probes both plugin load
  and concurrent-SVA support so we can switch the frontend when slang catches up.
  `fsyn verify --frontend slang` is available for that day; the default is `--frontend verilog`.
- Assertions live inside the DUT module under `` `ifdef FORMAL ... `endif `` so they can observe internal
  registers. `formalsynapse.sva_inject` does this automatically for candidate SVA blocks.
- Each golden block ships a `.sby` with `bmc`, `cover` (antecedent reachability / non-vacuity) and, where
  it converges, `prove` tasks.

## What a PASS means

`sby` is the only grader, and the harness is built so that a PASS cannot be cheaper than a proof:

- **Reset.** The DUT's reset (name and polarity from `formalsynapse.design_context`) is held active at
  step 0 (`initial assume(!rst_n)`), and every check starts at cycle 1, so pre-reset register garbage
  cannot produce a spurious FAIL or a spurious cover hit. `fsyn verify --no-reset-assume` /
  `--reset-cycles N` override this.
- **Every assert is checked within the bound.** `##[m:n]` and `$past` history only push a check to the
  deepest read it needs (`max`, not `sum`), so `req |=> ##[0:10] gnt` is live from cycle 11 at depth 20.
- **Nothing skipped counts.** If the lowerer cannot translate a statement it is listed under
  "Not proven" in the report; a block whose asserts were all skipped is an ERROR, not a PASS.
- **Candidates cannot constrain the environment.** Strict lowering (the default for CEGAR and `fsyn
  gate`) rejects `assume property` and `fsyn:verbatim`. `fsyn gate --trusted` re-enables them for
  hand-written blocks.
- **Vacuity is checked, not optional.** Every assert with an implication gets an automatic cover of its
  antecedent (`<label>__cov`). `fsyn gate` runs cover whenever there is one, and CEGAR treats an
  unreachable antecedent as a failure: the vacuous label is stripped and the model is asked again.
- **Only real signals.** Yosys turns an identifier the DUT does not declare into a free, undriven wire;
  the harness reads that warning and reports ERROR for any name the SVA introduced (a candidate's
  `disable iff (!rst_n)` on a DUT whose reset is `reset`, or that has none, is not a proof). A DUT
  without a reset port gets a prompt that forbids `disable iff` instead of one that invents `rst_n`.
- **Kills need a proof first.** `fsyn gate` scores mutation only after prove and cover PASS; an SVA
  that fails on the golden RTL fails on every mutant, and that is not a kill rate.
- **The bound is deep enough to fire every check.** A check gated to cycle >= N never runs in an
  N-step BMC, and sby would still say PASS. The harness compares every lowered check's guard depth
  with `--depth` and reports ERROR naming the labels that could never have been checked.
- **Parameter overrides are part of the result.** `--param` changes what was proven; the summary,
  report and JSON say which values the result holds for.

## Repository layout

```
PROJECT_CONTEXT.md            master project context (Part 1 of the founding prompt)
.cursorrules                  Cursor agent rules
kilo.jsonc, .kilo/rules/      Kilo Code permissions and agent protocol
flake.nix, flake.lock         nix develop + `fsyn` package (nixpkgs yosys; full suite still install_toolchain.sh)
examples/uart_tx/             hand-written SVA for the external `ben-marshall/uart` CI consumer
scripts/                      install_toolchain.sh, env.sh
.github/actions/fsyn-gate     reusable composite action for `fsyn gate` on a DUT/SVA pair
benchmarks/smoke/counter/     end-to-end toolchain sanity check (one passing, one failing task)
benchmarks/golden/<block>/    <block>.sv, <block>.spec.md, <block>.sva.sv, <block>.sby (protected)
formalsynapse/                toolchain, sva_inject, sby_config, verify_harness, vcd_parser, cli
tests/                        pytest (unit tests always run; `toolchain` tests skip without sby)
work/, output/                gitignored scratch for agents and harness runs
```

## Phase 3: zero-shot baseline and CEGAR

The generator is an OpenAI-compatible client aimed at **local vLLM** serving
`wyt2000/CodeV-SVA-14B`. Grammar-constrained decode is **off** by default (`--grammar` to
enable). Each turn samples `--candidates` (default 8) completions; `sby` picks the winner.
Failed labels are stripped in Python before the next prompt. `sby` is still the only grader.

```bash
# On a CUDA box (or any host that can run vLLM):
MODEL=wyt2000/CodeV-SVA-14B scripts/run_vllm.sh

# From this repo (point at that server if it is not localhost):
export FSYN_LLM_BASE_URL=http://localhost:8000/v1
export FSYN_LLM_MODEL=wyt2000/CodeV-SVA-14B

fsyn doctor                  # pings the LLM endpoint
fsyn baseline                # zero-shot (target 40% first-pass after the 14B is up)
fsyn cegar                   # slot repair + best-of-N (target 50–60%)
fsyn generate benchmarks/golden/counter --max-feedback 3 --out /tmp/counter.sva.sv
# fsyn generate --suite assertllm2 --only versatile_counter --out cand.sva.sv
```

Healed `(Prompt, Faulty_Attempt, Counterexample, Fixed_Attempt)` rows are appended to
`trajectories.jsonl` under `output/` (or `~/.cache/formalsynapse/output` when the repo path
contains `:`). That file is the Phase 4 RLVR seed.

This Mac does not run vLLM; keep the formal tools local and point `FSYN_LLM_BASE_URL` at a
remote GPU. Any OpenAI-compatible host works, including OpenRouter, but the spec default is
air-gapped vLLM.

## Dual-agent workflow (Cursor + Kilo Code)

Cursor is the interactive pilot (architecture, review, scaffolding). Kilo Code is the autonomous terminal
worker that runs `sby`/`verilator`/`ruff`/`mypy` loops until they pass, governed by `.kilo/rules/`.

Kilo's *coding* model is separate from Phase 3's generator. As originally specified, route Kilo
via OpenRouter (see `.kilo/rules/03-model-routing.md`): DeepSeek-V3 for scaffolding, DeepSeek-R1
for temporal/CEGAR repair, local vLLM as the zero-cost fallback. Put the OpenRouter key in the
Kilo UI — never in the repo.

Anti-collision rules:

1. Do not edit a file in Cursor while Kilo is iterating on it; wait for its diff to land.
2. In Cursor settings disable `Terminal > Integrated: AI Suggestions` so Kilo owns the shell.
3. Kilo works on `agent/<task>` branches (for example `agent/fifo-sva`), never directly on `main`.
4. `benchmarks/golden/**` is protected; agents write to `work/` or `output/`.

## Nix and CI gate

```bash
nix develop          # python + nixpkgs yosys/sby/z3; still source scripts/env.sh for yosys-slang
```

This repo runs `fsyn gate` on the golden `counter` and on
[ben-marshall/uart](https://github.com/ben-marshall/uart) `uart_tx` in
`.github/workflows/gate.yml`. An external repo can do the same on PRs:

```yaml
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4
        with:
          repository: saintbate/FormalSynapse
          path: .fsyn
      - uses: ./.fsyn/.github/actions/fsyn-gate
        with:
          source: .fsyn
          dut: rtl/foo.sv
          sva: formal/foo.sva.sv
          top: foo
          # optional: elaborate with parameters that make the behaviour fit the bound
          # params: "BAUD_DIV=4"
          # min-kill: "0.5"
```

Most real RTL ships with parameters (baud dividers, refresh counters, timeouts) that put the
interesting behaviour thousands of cycles out, where no affordable BMC bound can see it and every
mutant survives. `params` (CLI: `--param NAME=VALUE`, yosys `chparam`) elaborates the DUT with
different values for prove, cover and every mutant. The report, the JSON and the step summary all
carry the values, because a proof at `BIT_RATE=25000000` says nothing about 9600 baud. Suites
(`--suite golden|assertllm2`) refuse `--param` so their numbers stay comparable across runs.
`examples/uart_tx/uart_tx.sva.sv` shows the pattern: 0/8 kills at shipped parameters, 7/8 with
frame-timing properties under `BIT_RATE=25000000 PAYLOAD_BITS=2`.

## Quality gates

```bash
uv run ruff check .
uv run mypy
uv run pytest                     # add -m toolchain to run only sby-backed tests
# DUT modules only (.sva.sv files are assertion fragments, not elaboratable tops)
for f in benchmarks/golden/*/*.sv benchmarks/smoke/counter/counter.sv; do
  case "$f" in *.sva.sv) continue ;; esac
  verilator --lint-only -Wall --Wno-DECLFILENAME "$f"
done
```
