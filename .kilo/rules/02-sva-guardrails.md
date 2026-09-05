# SystemVerilog Assertion (SVA) Quality Standards

## 1. Forbidden Patterns (Hallucination & Bug Traps)
- NO Vacuous Assertions: Never write implications where the antecedent is contradictory or unreachable (e.g., `(a && !a) |-> b`). Add a `cover property` for every non-trivial antecedent.
- NO Interface Fabrication: You may ONLY use signals explicitly defined in the target module's port list or declared internal registers.
- NO Missing Reset Blocks: Every clocked property MUST include explicit reset gating:
  `disable iff (!rst_n)`

## 2. Temporal Logic Rules
- Differentiate Implications:
  - Use `|->` (overlapping) ONLY when the consequence must hold on the EXACT SAME clock cycle as the antecedent.
  - Use `|=>` (non-overlapping) when the consequence must hold on the NEXT clock cycle.
- Bounded Sequences:
  - Avoid unbounded temporal ranges like `##[0:$]` unless verifying liveness properties.
  - Default to finite bounded windows (e.g., `##[1:10]`) for Bounded Model Checking (BMC).
- `$past(x)` in the first cycle after reset is undefined unless guarded; prefer `$past(x, 1, rst_n)` or gate with `$past(rst_n)` when the reset value matters.

## 3. Required SVA Template
Every assertion must follow this structure:

```systemverilog
property p_<module>_<property_name>;
    @(posedge clk) disable iff (!rst_n)
    <antecedent_expression> |=> <consequent_expression>;
endproperty

a_<module>_<property_name>: assert property (p_<module>_<property_name>)
    else $error("Assertion Failed: <property_name> violated at cycle %0t", $time);
```

## 4. Placement
Assertion blocks are placed inside the DUT module, immediately before `endmodule`, wrapped as:

```systemverilog
`ifdef FORMAL
// properties here
`endif
```

The harness (`formalsynapse.sva_inject`) performs this injection automatically when given a candidate SVA file; do not hand-edit protected golden DUTs.
