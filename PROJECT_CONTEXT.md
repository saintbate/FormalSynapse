# System Prompt & Project Context: Open Neuro-Symbolic Hardware Verification Engine

## 1. Executive Summary & Core Thesis

**Project Name:** Neuro-Symbolic Hardware Verification Engine (Working Title: FormalSynapse / OpenSVA-RL)

**Core Mission:** Automate the synthesis, verification, and hardening of SystemVerilog Assertions (SVA) for digital hardware designs by pairing open-weight Large Language Models with open-source formal Satisfiability Modulo Theories (SMT) verification engines.

**The Core Thesis:** Hardware verification is a $15B+ bottleneck where pure machine learning inevitably fails. Modern LLMs are probabilistic interpolators that hallucinate syntax, miss temporal invariants, and emit vacuous logic. Conversely, formal solvers (SMT/SAT) are mathematically sound and exhaustive, but cannot parse human specifications or write architectural properties.

**The Solution:** A closed-loop Neuro-Symbolic system utilizing Counterexample-Guided Abstraction Refinement (CEGAR) and Reinforcement Learning with Verifiable Rewards (RLVR / GRPO). The LLM acts as an unconstrained hypothesis generator; the formal toolchain (Yosys + SymbiYosys + Z3) acts as an uncompromising mathematical referee.

## 2. Market Dynamics & Strategic Asymmetric Wedge

### The Industry Problem

- In digital ASIC and SoC design, writing RTL takes ~30% of the engineering budget; verification consumes ~70%.
- A single missed corner-case bug can cause an ASIC re-spin costing $10M to $50M and 6 to 12 months of market delay.
- The talent pool capable of writing complex temporal SystemVerilog Assertions (SVA) using Linear Temporal Logic (LTL) is tiny and prohibitively expensive.

### Competitive Landscape

- **The EDA Oligopoly (Cadence JasperGold, Synopsys VSO.ai):** High-end, enterprise tools priced at $50,000-$100,000+ per seat license, locking their technology behind proprietary cloud/enterprise walls.
- **Frontier Research & Startups:** NVIDIA Research (AssertionForge), ProofLoop (ReAct + JasperGold), and Architect Labs ($24M seed) are proving that solver-in-the-loop assertion generation works.
- **The Structural Barrier:** Proprietary chip makers (Apple, Nvidia, AMD) will never send RTL code across public cloud APIs (OpenAI/Anthropic) due to extreme IP paranoia. Any viable tool must run local-first, on-premise, or air-gapped.

### Our Asymmetric Wedge: The Open-Silicon & RISC-V Economy

Rather than attempting to displace Cadence on a TSMC 3nm tape-out on Day 1, this project targets the booming Open-Silicon and RISC-V Ecosystem (OpenLane, Tiny Tapeout, SkyWater 130nm, European Processor Initiative):

- These teams have zero access to six-figure proprietary EDA licenses and rely on unverified testbenches.
- They run entirely on open-source toolchains (Yosys, SymbiYosys, Verilator).
- We become the default, automated formal sign-off pipeline for open hardware, building verifiable trust where none currently exists.

## 3. Technical Architecture & System Pipeline

The engine operates as a four-stage closed control loop:

```
[ Natural Language Spec + RTL Interface ]
                   |
                   v
+--------------------------------------------------------+
| Stage 1: Grammar-Constrained Open-Weight Generator      |
| Model: Qwen-2.5-Coder-7B / DeepSeek-Coder              |
| Engine: vLLM + Outlines / XGrammar (Enforcing SVA BNF) |
+--------------------------+-----------------------------+
                           | Emits Candidate SVA:
                           | property p_safety; @(posedge clk) ...
                           v
+--------------------------------------------------------+
| Stage 2: The Formal Verification Harness (Local Engine)|
| Tools: Yosys (Synthesis) + SymbiYosys (SBY Driver)     |
| SMT Solvers: Z3, Boolector, Bitwuzla                   |
+--------------------------+-----------------------------+
                           |
             +-------------+-------------+
             |                           |
      [ PROVEN: Exit 0 ]          [ FAILED: Exit 1 ]
             |                           |
             v                           v
+-------------------------+  +---------------------------+
| Stage 3: Vacuity &      |  | Stage 4: Diagnostic       |
| Mutation Engine         |  | Extraction & CEGAR Loop   |
| (Rejects trivial/       |  | (Parses trace.vcd into    |
| unreachable proofs)     |  | signal timeline prompt)   |
+------------+------------+  +-------------+-------------+
             | Verified                    | Refinement Prompt
             v                             v
   [ Reward = +1.0 ] -----------> [ Feed back to Generator ]
   (Store for RLVR Training)     (Max 3 iterations)
```

### Detailed Pipeline Mechanics

1. **Constrained Generation:** The input prompt (Verilog DUT code + functional specification) is passed to an open-weight coding model. Using context-free grammar constraints (CFG/BNF), the model's logits are masked so it is physically incapable of emitting conversational text or invalid syntax; it only outputs valid SystemVerilog assertion blocks.
2. **Deterministic Bounded Model Checking (BMC):** The harness automatically injects the candidate SVA into a verification wrapper, generates an `.sby` configuration file, and invokes `sby -f verify.sby`. The SMT solver unrolls the digital logic for k cycles to formally prove correctness or falsify the claim.
3. **Trace Parsing (CEGAR):** If falsified, sby dumps a Value Change Dump (`.vcd`) file. The Python harness parses the exact clock cycle, signal states, and failing conditions, converting raw binary simulation traces into a structured diagnostic prompt for automated self-repair.
4. **Vacuity & Mutation Testing:** To prevent the model from outputting trivial proofs (e.g., `(req && !req) |-> ack`), the harness verifies that the antecedent is reachable and tests the assertion against mutated RTL containing injected bugs to verify it catches real errors.

## 4. Full Technology Stack

### Operating & Development Environment

- **Platform:** Native Linux (Ubuntu 22.04/24.04 LTS) or Windows Subsystem for Linux (WSL2). macOS arm64 is supported via the OSS CAD Suite `darwin-arm64` build (this is the current development host).
- **IDE & Coding Agent:** Cursor (with full codebase context indexing).
- **Editor Extensions:**
  - `mshr-h.verilogHDL`: SystemVerilog/Verilog syntax highlighting and linting.
  - `surfer` or `wavetrace`: Embedded `.vcd` waveform viewer inside the IDE.
  - `charliermarsh.ruff`: High-performance Python linting.

### Formal EDA Engine (Deterministic Ground Truth)

- **Logic Synthesis:** `yosys` (Open SYnthesis Suite).
- **SystemVerilog Frontend:** `yosys-slang` (open-source; required for concurrent SVA). Open-source `read_verilog -sv -formal` supports only immediate assertions.
- **Formal Front-End:** SymbiYosys (`sby`) by YosysHQ.
- **SMT/SAT Backends:** `z3` (Microsoft Research), `boolector`, `bitwuzla`.
- **Waveform / Trace Analysis:** `gtkwave`, plus a stdlib Python VCD reader (`formalsynapse/vcd_parser.py`) for programmatic counterexample extraction.

### Neural & Inference Layer

- **Base Models:** `Qwen/Qwen2.5-Coder-7B-Instruct` (or 14B), `deepseek-ai/deepseek-coder-6.7b-instruct`.
- **Inference Engine:** vLLM or SGLang (for high-throughput local execution).
- **Structured Generation:** `outlines` or `xgrammar` (grammar-guided token masking).

### Training & Reinforcement Learning (RLVR)

- **Framework:** `verl` (Volcano Engine Reinforcement Learning for LLMs) or OpenRLHF.
- **RL Algorithm:** GRPO (Group Relative Policy Optimization). Eliminates the need for an external reward model by using deterministic verification scores as the direct mathematical reward.
- **Hardware Requirements:** Local laptop/workstation for Stages 1-3; on-demand 8x A100/H100 instances (RunPod, Lambda Labs, Vast.ai) for Stage 4 training runs (~$200-$400 total budget).

## 5. Standard Benchmarks & Evaluation Standards

Do not invent arbitrary test cases. Evaluate the pipeline against established peer-reviewed academic hardware verification benchmarks:

- **FVEval (Formal Verification Evaluation):**
  - *NL2SVA-Human:* Human-authored natural language requirements mapped to complex temporal assertions.
  - *Design2SVA:* Pure RTL-to-assertion synthesis where the model must autonomously deduce safety and liveness invariants without explanatory text.
- **FIXME (AAAI 2026 Benchmark):** Full-lifecycle functional verification benchmark on silicon-proven designs measuring testbench generation, assertion coverage, and automated debugging.
- **AssertLLM / AssertLLM2:** Benchmark measuring mutation kill rates, evaluating whether synthesized SVAs actually catch injected RTL hardware bugs (off-by-one errors, state-machine deadlocks, missed handshakes).

### Core Metrics to Track

- **Syntactic Compilation Rate:** Percentage of generated assertions that compile without syntax or scoping errors.
- **First-Pass Formal Pass Rate:** Percentage of assertions mathematically proven on the golden DUT without refinement.
- **CEGAR Multi-Turn Resolution Rate:** Percentage of failing assertions successfully self-healed within 3 solver-feedback loops.
- **Non-Vacuity Score:** Percentage of proven assertions with fully reachable antecedents.

## 6. Phased Implementation Roadmap

### Phase 1: Local Toolchain & Golden Testbed (Weeks 1-2)

- Install native `yosys`, `sby`, and `z3`.
- Assemble a benchmark suite of 10 classic digital blocks: FIFO buffer, 4-way priority arbiter, shift register, SPI controller, round-robin scheduler, and others.
- Hand-write golden SVAs for each module and verify them locally using Bounded Model Checking (BMC).

### Phase 2: The Python Verification Bridge (Weeks 3-4)

- Write `verify_harness.py`: takes a candidate SVA string, merges it with the DUT, dynamically creates an `.sby` configuration, and executes `sby` via subprocess.
- Write `vcd_parser.py`: intercepts solver failures, parses `trace.vcd`, and outputs a human/LLM-readable diagnostic report detailing signal values at the failing clock cycle.

### Phase 3: Zero-Shot Baseline & CEGAR Feedback Loop (Weeks 5-6)

- Connect the harness to local vLLM running Qwen-2.5-Coder-7B.
- Enforce SVA grammar constraints using `outlines`.
- Run the benchmark suite zero-shot to establish a baseline pass rate (target: 15-25%).
- Activate the 3-turn CEGAR loop using parsed counterexamples. Measure the increase in formal pass rate (target: 50-60%).
- Automatically log all successful `(Prompt, Faulty_Attempt, Counterexample, Fixed_Attempt)` trajectories into a dataset.

### Phase 4: Forking & RLVR Training via GRPO (Weeks 7-9)

- Deploy the open-source `verl` framework on a rented multi-GPU cloud instance.
- Wire `verify_harness.py` directly into the RL environment as the reward function (R=+1.0 for formal proof, R=-1.0 for syntax error, R=0.0 for counterexample).
- Execute a GRPO training run to fine-tune the open-weight model into a specialized formal reasoning policy.

## 7. The Ultimate Long-Term Vision

- **Years 1-3:** Establish the project as the indispensable, trusted formal verification sign-off engine for the open-source hardware, RISC-V, and defense-research communities.
- **Years 3-6 (Autonomous Silicon):** Use this verified assertion engine as the missing "closed-loop governor" to unlock autonomous Prompt-to-GDSII. Because the engine can independently verify and isolate bugs, it enables autonomous neural synthesis of entire digital cores without risking hardware bricking.
- **Years 7-10 (The AI-Native EDA Platform):** Challenge the legacy $150B+ duopoly of Synopsys and Cadence by delivering an open-core, provably correct hardware compilation and verification platform that slashes custom ASIC design cycles from 2 years to 2 weeks.

## 8. Instructions for AI / Coding Agent

When generating code, architecture, or scripts for this project:

- **Prioritize Determinism:** Never rely on probabilistic LLM evaluation to grade an assertion. Always route execution through the local `sby` / `yosys` subprocess.
- **Enforce Temporal Precision:** In SVA, pay extreme attention to clocking blocks (`@(posedge clk)`), reset gating (`disable iff (!rst_n)`), and temporal implication operators (distinguishing overlapping `|->` from non-overlapping `|=>`).
- **Guard Against Vacuity:** Ensure that any generated assertion does not contain contradictory or unreachable antecedents.
- **Follow Clean Systems Design:** Keep the verification engine modular, CLI-driven, and completely decoupled from proprietary cloud APIs. All models and tools must run in an air-gapped, local-first paradigm.
