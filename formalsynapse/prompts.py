"""Prompt templates for zero-shot SVA generation and CEGAR refinement."""

from __future__ import annotations

import re

from formalsynapse.sva_inject import strip_formal_blocks

SPEC_CHAR_BUDGET = 4000
RTL_CHAR_BUDGET = 3500
SVA_CHAR_BUDGET = 2000
REPORT_CHAR_BUDGET = 1500

_KEEP_HEAD = re.compile(
    r"(?im)^#{1,3}\s+(interface|requirements?|function|clock|reset|constraints?)\b"
)
_NUMBERED = re.compile(r"(?m)^\s*\d+\.\s+\S")

_ACTIVE_LOW_RESET = frozenset({"rst_n", "reset_n", "resetn"})


def system_prompt(*, clock: str = "clk", reset: str = "rst_n") -> str:
    """System prompt with clock/reset names taken from the DUT, not hardcoded."""
    active_low = reset.lower() in _ACTIVE_LOW_RESET
    disable = f"!{reset}" if active_low else reset
    clause = f"disable iff ({disable})"
    polarity = "active-low" if active_low else "active-high"
    ante = f"{reset} or {disable}" if disable != reset else reset
    return f"""\
You are a formal hardware verification engineer. Emit ONLY a SystemVerilog assertion block.
No prose, no markdown fences, no module/endmodule, no bind.

Rules:
- Wrap the block in `ifdef FORMAL ... `endif
- Every clocked property: @(posedge {clock}) {clause}
- |-> only when the consequent holds in the SAME cycle; |=> for the NEXT cycle
- A |=> consequent that mentions a DUT register MUST use $past for the old value
  (write `count == $past(count) + 1`, never `count == count + 1`)
- Bounded delays only (##N or ##[m:n]); no ##[0:$], no [*n], no sequence/generate/genvar
- No eventually / s_eventually / until / throughout / bind / module
- Only name signals that appear in the DUT ports or declared internals
- No vacuous antecedents (never (a && !a) |-> ...)
- Reset `{reset}` is {polarity}. Never use {ante} as the antecedent.
  {clause} already excludes reset. `{reset} |-> count == 0` means "count is always 0"
  and will FAIL. Encode the numbered post-reset behaviors instead (increment, hold,
  grant-follows-request, …).
- At most 6 properties. Finish every property (endproperty) and close `endif`. Never start a property you cannot finish.
- Add a cover for each non-trivial antecedent as a sequence, not an implication.
- Do not emit angle-bracket placeholders. Fill in real signal names from the DUT.
- Example shape only (rewrite for the module you are given):

property p_example_inc;
    @(posedge {clock}) {clause}
    en |=> count == $past(count) + 4'd1;
endproperty
a_example_inc: assert property (p_example_inc)
    else $error("Assertion Failed: inc violated at cycle %0t", $time);
c_example_en: cover property (@(posedge {clock}) {clause} en);
"""


SYSTEM_PROMPT: str = system_prompt()


def clip_prompt_text(text: str, max_chars: int, *, label: str = "text") -> str:
    """Keep a prompt payload under ``max_chars`` so CEGAR fits an 8k context."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    parts = re.split(r"(?=^#{1,3}\s+)", text, flags=re.M)
    preferred = [p for p in parts if _KEEP_HEAD.search(p) or _NUMBERED.search(p)]
    rest = [p for p in parts if p not in preferred]
    packed = ""
    for part in preferred + rest:
        if len(packed) + len(part) <= max_chars:
            packed += part
            continue
        remain = max_chars - len(packed) - 48
        if remain > 80:
            packed += part[:remain].rsplit("\n", 1)[0] + "\n"
        break
    packed = packed.strip() or text[:max_chars].rsplit("\n", 1)[0]
    return packed + f"\n\n[{label} truncated for the 8k context window]\n"


def zero_shot_user(*, module: str, spec: str, rtl: str, context: str = "") -> str:
    """User message for the first (zero-shot) attempt."""
    spec = clip_prompt_text(spec, SPEC_CHAR_BUDGET, label="specification")
    clean = clip_prompt_text(strip_formal_blocks(rtl).rstrip() + "\n", RTL_CHAR_BUDGET, label="DUT")
    ctx = f"## Design context (only these names exist)\n{context.strip()}\n\n" if context.strip() else ""
    return (
        f"Write SystemVerilog Assertions for module `{module}` that capture every numbered "
        f"requirement in the specification. Cover the interesting antecedents.\n\n"
        f"## Specification\n{spec}\n\n"
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
    prev = clip_prompt_text(previous_sva, SVA_CHAR_BUDGET, label="previous SVA")
    diag = clip_prompt_text(report, REPORT_CHAR_BUDGET, label="diagnostic")
    return (
        f"{repeat}"
        f"The previous assertion block did not formally verify.\n"
        f"Harness status: {status}\n"
        f"FAILED LABELS (delete or fully rewrite; do not keep them unchanged): {labels}\n\n"
        f"## Previous attempt\n```systemverilog\n{prev}\n```\n\n"
        f"## Solver / lowering diagnostic (ground truth; do not ignore)\n{diag}\n\n"
        f"RTL is golden — fix the SVA. Emit a complete replacement block, not a diff.\n"
        f"If a next-cycle (|=>) equality failed, the consequent needs $past on the old value.\n"
        f"If a reset-named assert failed, you used the reset signal as the antecedent — delete it.\n"
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
    kept = clip_prompt_text(kept_sva, SVA_CHAR_BUDGET, label="kept SVA") or (
        "(none — emit a full replacement block)"
    )
    prev = clip_prompt_text(previous_sva, SVA_CHAR_BUDGET, label="failed SVA")
    diag = clip_prompt_text(report, REPORT_CHAR_BUDGET, label="diagnostic")
    return (
        f"The harness already deleted the failed labels. Do not repeat the kept properties.\n"
        f"FAILED LABELS to replace with *different* properties: {labels}\n"
        f"Harness status: {status}\n\n"
        f"## Kept (already compiled; do not copy these back)\n```systemverilog\n{kept}\n```\n\n"
        f"## Failed attempt (do not copy)\n```systemverilog\n{prev}\n```\n\n"
        f"## Solver / lowering diagnostic\n{diag}\n\n"
        f"Emit ONLY the replacement property + assert (and cover) blocks for the failed "
        f"labels. New antecedents and consequents. If a |=> equality failed, use $past. "
        f"Never use the reset signal as an antecedent. cover property, not coverproperty.\n"
    )
