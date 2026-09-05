"""Prompt templates for zero-shot SVA generation and CEGAR refinement."""

from __future__ import annotations

from formalsynapse.sva_inject import strip_formal_blocks

SYSTEM_PROMPT: str = """\
You are a formal hardware verification engineer. Emit ONLY a SystemVerilog assertion block.
No prose, no markdown fences, no module/endmodule, no bind.

Rules:
- Wrap the block in `ifdef FORMAL ... `endif
- Every clocked property: @(posedge clk) disable iff (!rst_n)
- |-> only when the consequent holds in the SAME cycle; |=> for the NEXT cycle
- A |=> consequent that mentions a DUT register MUST use $past for the old value
  (write `count == $past(count) + 1`, never `count == count + 1`)
- Bounded delays only (##N or ##[m:n]); no ##[0:$], no [*n], no sequence/generate/genvar
- No eventually / s_eventually / until / throughout / bind / module
- Only name signals that appear in the DUT ports or declared internals
- No vacuous antecedents (never (a && !a) |-> ...); do not assert !rst_n under disable iff (!rst_n)
- At most 6 properties. Finish every property (endproperty) and close `endif`. Never start a property you cannot finish.
- Add a cover for each non-trivial antecedent as a sequence, not an implication:
  `c_<name>: cover property (@(posedge clk) disable iff (!rst_n) <antecedent>);`
- Use this template:

property p_<module>_<name>;
    @(posedge clk) disable iff (!rst_n)
    <antecedent> |=> <consequent>;
endproperty
a_<module>_<name>: assert property (p_<module>_<name>)
    else $error("Assertion Failed: <name> violated at cycle %0t", $time);
"""


def zero_shot_user(*, module: str, spec: str, rtl: str) -> str:
    """User message for the first (zero-shot) attempt."""
    clean = strip_formal_blocks(rtl).rstrip() + "\n"
    return (
        f"Write SystemVerilog Assertions for module `{module}` that capture every numbered "
        f"requirement in the specification. Cover the interesting antecedents.\n\n"
        f"## Specification\n{spec.strip()}\n\n"
        f"## DUT (RTL only; do not copy it back)\n```systemverilog\n{clean}```\n"
    )


def refinement_user(*, previous_sva: str, status: str, report: str) -> str:
    """User message after sby FAIL/ERROR. Ground the rewrite in the solver diagnostic."""
    return (
        f"The previous assertion block did not formally verify.\n"
        f"Harness status: {status}\n\n"
        f"## Previous attempt\n```systemverilog\n{previous_sva.strip()}\n```\n\n"
        f"## Solver / lowering diagnostic (ground truth; do not ignore)\n{report.strip()}\n\n"
        f"Diagnose whether the bug is in the assertion or would require RTL change. "
        f"RTL is golden — fix the SVA. Emit a complete replacement block, not a diff.\n"
        f"If a next-cycle (|=>) equality failed, the consequent almost certainly needs $past "
        f"on the previous value. If lowering said there were no statements, the last attempt "
        f"was truncated: emit fewer, fully-closed properties (endproperty + `endif).\n"
        f"Do not use empty bit-selects like req[] or generate/genvar loops.\n"
    )
