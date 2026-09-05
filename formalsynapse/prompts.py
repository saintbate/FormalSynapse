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
- No vacuous antecedents (never (a && !a) |-> ...)
- Never use rst_n or !rst_n as the antecedent. disable iff (!rst_n) already excludes reset.
  `rst_n |-> count == 0` means "count is always 0" and will FAIL. Encode the numbered
  post-reset behaviors instead (increment, hold, grant-follows-request, …).
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


def zero_shot_user(*, module: str, spec: str, rtl: str, context: str = "") -> str:
    """User message for the first (zero-shot) attempt."""
    clean = strip_formal_blocks(rtl).rstrip() + "\n"
    ctx = f"## Design context (only these names exist)\n{context.strip()}\n\n" if context.strip() else ""
    return (
        f"Write SystemVerilog Assertions for module `{module}` that capture every numbered "
        f"requirement in the specification. Cover the interesting antecedents.\n\n"
        f"## Specification\n{spec.strip()}\n\n"
        f"{ctx}"
        f"## DUT (RTL only; do not copy it back)\n```systemverilog\n{clean}```\n"
    )


def refinement_user(
    *,
    previous_sva: str,
    status: str,
    report: str,
    failed_assertions: tuple[str, ...] = (),
    repeated: bool = False,
) -> str:
    """User message after sby FAIL/ERROR. Ground the rewrite in the solver diagnostic."""
    labels = ", ".join(failed_assertions) if failed_assertions else "(see diagnostic)"
    repeat = (
        "You submitted the SAME failing labels as the last turn. Delete those properties "
        "entirely and write different ones. Do not copy the previous block.\n\n"
        if repeated
        else ""
    )
    return (
        f"{repeat}"
        f"The previous assertion block did not formally verify.\n"
        f"Harness status: {status}\n"
        f"FAILED LABELS (delete or fully rewrite; do not keep them unchanged): {labels}\n\n"
        f"## Previous attempt\n```systemverilog\n{previous_sva.strip()}\n```\n\n"
        f"## Solver / lowering diagnostic (ground truth; do not ignore)\n{report.strip()}\n\n"
        f"RTL is golden — fix the SVA. Emit a complete replacement block, not a diff.\n"
        f"If a next-cycle (|=>) equality failed, the consequent needs $past on the old value.\n"
        f"If a reset-named assert failed, you used rst_n or !rst_n as the antecedent — delete it.\n"
        f"Do not use empty bit-selects, generate/genvar, or coverproperty (it is cover property).\n"
    )


def slot_repair_user(
    *,
    kept_sva: str,
    previous_sva: str,
    status: str,
    report: str,
    failed_assertions: tuple[str, ...],
) -> str:
    """Ask only for replacements of the failed labels; survivors stay in Python."""
    labels = ", ".join(failed_assertions) if failed_assertions else "(see diagnostic)"
    kept = kept_sva.strip() or "(none — emit a full replacement block)"
    return (
        f"The harness already deleted the failed labels. Do not repeat the kept properties.\n"
        f"FAILED LABELS to replace with *different* properties: {labels}\n"
        f"Harness status: {status}\n\n"
        f"## Kept (already compiled; do not copy these back)\n```systemverilog\n{kept}\n```\n\n"
        f"## Failed attempt (do not copy)\n```systemverilog\n{previous_sva.strip()}\n```\n\n"
        f"## Solver / lowering diagnostic\n{report.strip()}\n\n"
        f"Emit ONLY the replacement property + assert (and cover) blocks for the failed "
        f"labels. New antecedents and consequents. If a |=> equality failed, use $past. "
        f"Never use rst_n / !rst_n as an antecedent. cover property, not coverproperty.\n"
    )
