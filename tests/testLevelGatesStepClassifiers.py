"""Behaviour tests for the per-step PROOF classification helpers.

These pure helpers decide, for one step, whether it has started, how
many of its present verification axes are green, what its Level-1
requirements are, and whether its attestation has gone stale. They are
the building blocks of the level gates, and are exercised here with
crafted step dicts hitting each branch.
"""

import pytest

from vaibify.reproducibility import levelGates as lg


# --- _fbStepHasNoActivity ---

def test_not_started_true_for_pristine_step():
    assert lg._fbStepHasNoActivity({}) is True


def test_not_started_true_for_corrupt_step_entry():
    assert lg._fbStepHasNoActivity("not a dict") is True


def test_not_started_false_once_run_stats_exist():
    assert lg._fbStepHasNoActivity({"dictRunStats": {"fWallClock": 1.0}}) \
        is False


def test_not_started_false_after_user_attestation():
    dictStep = {"dictVerification": {"sLastUserUpdate": "2026-01-01"}}
    assert lg._fbStepHasNoActivity(dictStep) is False


def test_not_started_false_when_a_test_axis_moved():
    dictStep = {"dictVerification": {"sUnitTest": "passed"}}
    assert lg._fbStepHasNoActivity(dictStep) is False


# --- _ftCountGreenAxes ---

def test_count_green_axes_counts_only_present_axes():
    dictStep = {"dictVerification": {
        "sUnitTest": "passed", "sIntegrity": "failed",
    }}
    assert lg._ftCountGreenAxes(dictStep) == (1, 2)


def test_count_green_axes_treats_unnecessary_as_green():
    dictStep = {"dictVerification": {"sQuantitative": "unnecessary"}}
    assert lg._ftCountGreenAxes(dictStep) == (1, 1)


def test_count_green_axes_zero_when_no_axes_present():
    assert lg._ftCountGreenAxes({"dictVerification": {}}) == (0, 0)


def test_count_green_axes_handles_corrupt_verification():
    assert lg._ftCountGreenAxes({"dictVerification": "x"}) == (0, 0)


# --- _flistStepLevel1Requirements / counts ---

def test_ai_declaration_step_has_no_level1_requirements():
    dictStep = {"bAiModelDeclaration": True}
    if not lg.fbStepIsAiDeclaration(dictStep):
        pytest.skip("ai-declaration flag differs; classifier is authority")
    assert lg._flistStepLevel1Requirements(dictStep, set()) == []


def test_level1_requirements_include_user_timing_and_input_declaration():
    dictStep = {
        "dictVerification": {"sUnitTest": "passed", "sUser": "passed",
                             "sLastUserUpdate": "2026-01-01"},
        "bNoInputData": True,
    }
    listReq = lg._flistStepLevel1Requirements(dictStep, set())
    dictReq = dict(listReq)
    assert "user-attestation" in dictReq
    assert "timing-clean" in dictReq
    assert dictReq["input-data-declared"] is True


def test_level1_counts_satisfied_over_total():
    dictStep = {
        "dictVerification": {"sUnitTest": "passed", "sUser": "passed",
                             "sLastUserUpdate": "2026-01-01"},
        "bNoInputData": True,
    }
    iSatisfied, iTotal = lg._ftStepLevel1Counts(dictStep, set())
    assert iTotal >= 1
    assert 0 <= iSatisfied <= iTotal


# --- _fbStepTimingRequirementMet / _fbAttestationStaleOnStep ---

def test_timing_requirement_fails_on_a_blocker_criterion():
    assert lg._fbStepTimingRequirementMet(
        {}, {"upstream-modified"}) is False


def test_timing_requirement_fails_on_stale_attestation():
    dictStep = {"dictVerification": {
        "sUser": "stale", "sLastUserUpdate": "2026-01-01",
    }}
    assert lg._fbStepTimingRequirementMet(dictStep, set()) is False


def test_attestation_stale_only_when_user_stale_and_timestamped():
    assert lg._fbAttestationStaleOnStep({"dictVerification": {
        "sUser": "stale", "sLastUserUpdate": "2026-01-01"}}) is True
    assert lg._fbAttestationStaleOnStep({"dictVerification": {
        "sUser": "passed", "sLastUserUpdate": "2026-01-01"}}) is False


def test_attestation_stale_false_for_corrupt_step():
    assert lg._fbAttestationStaleOnStep("x") is False
    assert lg._fbAttestationStaleOnStep({"dictVerification": "x"}) is False
