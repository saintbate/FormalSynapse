"""Prompt templates for zero-shot SVA generation and CEGAR refinement."""

from __future__ import annotations

import re
from collections.abc import Sequence

from formalsynapse.mutate import MutantHunk
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


def extract_fail_user(*, report: str) -> str:
    """User message when every sample failed extract (think/prose, no labeled assert)."""
    diag = clip_prompt_text(report, REPORT_CHAR_BUDGET, label="diagnostic")
    return (
        "The previous completion had no labeled assert/assume/cover property.\n"
        "Emit ONLY a `ifdef FORMAL ... `endif block. No <think>, no prose, no markdown fences.\n"
        "Every property needs a matching labeled assert or cover on the next line.\n\n"
        f"## Extract diagnostic\n{diag}\n"
    )


def cover_only_user(*, kept_sva: str) -> str:
    """User message after a BMC PASS that contained covers and no assert/assume."""
    kept = clip_prompt_text(kept_sva, SVA_CHAR_BUDGET, label="kept SVA")
    return (
        "BMC passed because the block had covers and no labeled assert/assume.\n"
        "Covers do not prove a requirement and cannot kill mutants.\n"
        "Keep the covers. Emit ONLY new labeled assert properties for the numbered "
        "requirements. Every property needs a matching assert, not just a cover.\n\n"
        f"## Kept (covers only; do not copy these back)\n```systemverilog\n{kept}\n```\n"
    )


def kill_unscored_user(*, kept_sva: str, attempted: int) -> str:
    """User message when every mutant ERROR'd, so kill cannot be scored."""
    kept = clip_prompt_text(kept_sva, SVA_CHAR_BUDGET, label="kept SVA")
    return (
        f"BMC proved the block, but all {attempted} mutants ERROR'd — none scored.\n"
        "That usually means the SVA still contains `macros (`CNT_LENGTH'd1). "
        "Mutant copies do not define those macros.\n"
        "Keep the properties. Rewrite every `MACRO as a sized literal "
        "(4'd1, not `CNT_LENGTH'd1). No compiler directives in the block.\n\n"
        f"## Kept (already proven; do not copy these back)\n```systemverilog\n{kept}\n```\n"
    )


def kill_miss_user(
    *,
    kept_sva: str,
    killed: int,
    valid: int,
    survivors: Sequence[MutantHunk],
    min_kill: float,
) -> str:
    """User message after a BMC PASS that missed too many mutants.

    Hunks are golden-minus / mutant-plus. The model must assert the golden
    side; encoding a ``+`` line as the consequent will FAIL on golden RTL.
    """
    kept = clip_prompt_text(kept_sva, SVA_CHAR_BUDGET, label="kept SVA")
    rate = (killed / valid) if valid else 0.0
    parts: list[str] = []
    for hunk in survivors[:6]:
        block = f"### {hunk.name}\n{hunk.description}\n"
        if hunk.diff:
            block += f"```diff\n{hunk.diff}\n```\n"
        parts.append(block)
    listed = clip_prompt_text("\n".join(parts), REPORT_CHAR_BUDGET, label="unkilled mutants")
    return (
        f"BMC proved the block, but mutation kill is {killed}/{valid} ({rate:.0%}); "
        f"the gate needs at least {min_kill:.0%}.\n"
        "Each hunk is a bug. Lines starting with `-` are GOLDEN RTL; `+` is the mutant.\n"
        "Assert the golden behavior. Never write a consequent that matches a `+` line.\n"
        "Example: golden `clear ? 0 : qi - 1` → `clear |-> q_next == 0`, not `== 1`.\n"
        "Keep the existing properties. Emit ONLY new assert properties (and covers) "
        "for the golden behaviors these hunks broke. You may go past 6 properties; "
        "finish every one.\n\n"
        f"## Kept (already proven; do not copy these back)\n```systemverilog\n{kept}\n```\n\n"
        f"## Unkilled mutants (assert golden, not the + lines)\n{listed}\n"
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
