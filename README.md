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
# fsyn gate                         # all golden blocks (many sby runs)
# fsyn gate --dut path/to/dut.sv --sva path/to/cand.sva.sv --top dut

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

## Repository layout

```
PROJECT_CONTEXT.md            master project context (Part 1 of the founding prompt)
.cursorrules                  Cursor agent rules
kilo.jsonc, .kilo/rules/      Kilo Code permissions and agent protocol
scripts/                      install_toolchain.sh, env.sh
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
