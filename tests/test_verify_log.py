"""sby/smtbmc log parsing: no toolchain needed."""

from __future__ import annotations

from pathlib import Path

from formalsynapse.sva_lower import lower
from formalsynapse.verify_harness import VerifyResult, _nothing_to_check, _parse_log

SMTBMC_COVER_LOG = """\
SBY [run] engine_0: ##   0:00:00  Reached cover statement in step 1 at counter: c_reach
SBY [run] engine_0: ##   0:00:00  Unreached cover statement at counter: a_vac__cov
SBY [run] engine_0: ##   0:00:00  Unreached cover statement at counter: c_never
SBY [run] summary: unreached cover statements:
SBY [run] summary:   counter.c_never at counter.sv:35.7-35.55
"""

SMTBMC_FAIL_LOG = """\
SBY [run] engine_0: ##   0:00:00  Assert failed in counter: a_hold__c1
SBY [run] engine_0: ##   0:00:00  Assert failed in counter: a_hold__c1
SBY [run] summary:   failed assertion counter.a_hold__c1 at counter.sv:40.9-40.40 step 5
"""


def test_parse_log_extracts_cover_labels_and_base_labels() -> None:
    failed, step, _errors, unreached, reached = _parse_log(SMTBMC_COVER_LOG + SMTBMC_FAIL_LOG)
    assert unreached == ("a_vac__cov", "c_never")
    assert reached == ("c_reach",)
    assert failed == ("a_hold",)  # __c1 maps back to the source label
    assert step == 5


def test_vacuous_assertions_come_from_auto_covers() -> None:
    res = VerifyResult(
        status="FAIL",
        exit_code=2,
        run_dir=Path("."),
        sby_log_path=None,
        trace_vcd_path=None,
        failing_step=None,
        failed_assertions=(),
        errors=(),
        report="",
        depth=20,
        mode="cover",
        elapsed_s=0.0,
        unreached_covers=("a_vac__cov", "c_never"),
    )
    assert res.vacuous_assertions == ("a_vac",)


def test_nothing_to_check_rules() -> None:
    covers_only = lower("c_en: cover property (@(posedge clk) disable iff (!rst_n) en);")
    msg = _nothing_to_check(covers_only, "bmc", 20)
    assert msg is not None and "nothing to prove" in msg
    assert _nothing_to_check(covers_only, "cover", 20) is None
    invariant = lower("a_x: assert property (@(posedge clk) disable iff (!rst_n) count <= 4'hF);")
    assert _nothing_to_check(invariant, "bmc", 20) is None
    assert _nothing_to_check(invariant, "cover", 20) == "no cover statements to check"
    implication = lower("a_x: assert property (@(posedge clk) disable iff (!rst_n) en |=> count != 0);")
    assert _nothing_to_check(implication, "cover", 20) is None  # auto-cover of the antecedent


def test_nothing_to_check_flags_a_bound_too_shallow_for_the_guard() -> None:
    """Guard depth 12 (|=> ##10 plus the reset sample) never fires in a 12-step BMC."""
    late = lower("a_late: assert property (@(posedge clk) disable iff (!rst_n) en |=> ##10 done);")
    depth = next(a.history_depth for a in late.assertions if a.name == "a_late")
    msg = _nothing_to_check(late, "bmc", depth)
    assert msg is not None and "too shallow" in msg and f"a_late (needs depth > {depth})" in msg
    assert _nothing_to_check(late, "bmc", depth + 1) is None
    # the antecedent auto-cover only needs the antecedent's own history, so cover mode is fine
    assert _nothing_to_check(late, "cover", 3) is None
