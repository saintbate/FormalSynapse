# FormalSynapse / OpenSVA-RL

Neuro-symbolic SystemVerilog Assertion (SVA) synthesis and verification. An open-weight LLM proposes
assertions; the open-source formal toolchain (Yosys + yosys-slang + SymbiYosys + Z3/Bitwuzla/Boolector)
is the referee. Full context and roadmap: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md).

This repository currently contains Phases 1-2 of the roadmap:

- a 10-block golden benchmark suite with hand-written, formally proven SVAs (`benchmarks/golden/`)
- the Python verification bridge (`formalsynapse/`): SVA injection, `.sby` generation, `sby` execution,
  result classification, and VCD counterexample parsing
- the `fsyn` CLI
- dual-agent configuration for Cursor (`.cursorrules`) and Kilo Code (`kilo.jsonc`, `.kilo/rules/`)

## Quickstart

```bash
# 1. Install the OSS CAD Suite (yosys, sby, z3, bitwuzla, boolector, verilator, yosys-slang) into tools/
scripts/install_toolchain.sh          # macOS arm64 by default; PLATFORM=linux-x64 for Ubuntu/WSL2
source scripts/env.sh                 # puts tools/oss-cad-suite/bin on PATH

# 2. Python environment (uv)
uv sync
source .venv/bin/activate             # or prefix commands with `uv run`

# 3. Check the toolchain and run the end-to-end smoke test
fsyn doctor
fsyn smoke

# 4. Prove the whole golden suite (BMC depth 20 with Z3)
fsyn golden

# 5. Verify a candidate SVA against a DUT
fsyn verify --dut benchmarks/golden/sync_fifo/sync_fifo.sv --top sync_fifo \
            --sva benchmarks/golden/sync_fifo/sync_fifo.sva.sv

# 6. Inspect a counterexample
fsyn trace work/<run>/<task>/engine_0/trace.vcd --top sync_fifo
```

## Toolchain notes

- Open-source Yosys (`read_verilog -sv -formal`) supports only immediate assertions. Concurrent SVA
  (`property`/`endproperty`, `|->`, `|=>`, `##[m:n]`, `disable iff`, `$past`, `$rose`, ...) is compiled
  through the `yosys-slang` frontend, which ships with the OSS CAD Suite. `fsyn doctor` confirms that the
  plugin loads. `fsyn verify --frontend verilog` falls back to the built-in frontend for
  immediate-assertion checkers.
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

## Dual-agent workflow (Cursor + Kilo Code)

Cursor is the interactive pilot (architecture, review, scaffolding). Kilo Code is the autonomous terminal
worker that runs `sby`/`verilator`/`ruff`/`mypy` loops until they pass, governed by `.kilo/rules/`.

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
for f in benchmarks/**/*.sv; do verilator --lint-only -Wall "$f"; done
```
