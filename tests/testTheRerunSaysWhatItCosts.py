"""A safety notice that spans four orders of magnitude is noise.

The Level 3 confirmation warned that a rerun "can take hours". The
researcher's project reruns in ten seconds: two steps, 4.7s and 5.2s,
both already recorded by vaibify itself (reported 2026-09-15). A notice
that is wrong by four orders of magnitude teaches people to click
through notices, which is the opposite of what it is for.

The number is evidence, not a prediction, and every test here is about
keeping that distinction: it is a FLOOR, it abstains when nothing has
been measured, and "never run" is never counted as "took no time".
"""

import pytest

from vaibify.reproducibility.rerunEstimate import fdictEstimateRerunCost


def _fdictWorkflow(*listSteps):
    return {"listSteps": list(listSteps)}


def _fdictStep(sName, fWallClock=None, **dictExtra):
    dictStep = {"sName": sName}
    if fWallClock is not None:
        dictStep["dictRunStats"] = {"fWallClock": fWallClock}
    dictStep.update(dictExtra)
    return dictStep


def test_the_recorded_steps_are_summed():
    """The live case: two steps, ten seconds, not "hours"."""
    dictCost = fdictEstimateRerunCost(_fdictWorkflow(
        _fdictStep("a", 4.7), _fdictStep("b", 5.2),
    ))
    assert dictCost["fRecordedSeconds"] == 9.9
    assert dictCost["iStepsTimed"] == 2
    assert dictCost["iStepsUntimed"] == 0
    assert dictCost["bAnyStepTimed"] is True


@pytest.mark.falsification
def test_an_untimed_step_is_counted_as_unknown_not_as_zero():
    """"Never run" and "ran instantly" are different facts.

    Folding the first into the second produces a total that is
    confidently too small — the same failure as "hours", pointing the
    other way, and harder to notice because it looks precise.

    Kills: defaulting a missing fWallClock to 0.0 instead of None,
    which silently drops the untimed count to zero and lets the
    renderer present a floor as a complete figure.
    """
    dictCost = fdictEstimateRerunCost(_fdictWorkflow(
        _fdictStep("timed", 3.0), _fdictStep("never-run"),
    ))
    assert dictCost["fRecordedSeconds"] == 3.0
    assert dictCost["iStepsUntimed"] == 1, (
        "an untimed step must be reported, not absorbed"
    )


@pytest.mark.falsification
def test_a_project_with_no_timings_reports_no_figure():
    """Abstaining beats a confident zero.

    A workflow nobody has run has no evidence about its duration.
    Rendering "0 seconds" would be a claim, and a false one.

    Kills: reporting bAnyStepTimed from the step count rather than
    from the recorded timings — a project with steps and no runs
    would then present a total of zero as measured.
    """
    dictCost = fdictEstimateRerunCost(_fdictWorkflow(
        _fdictStep("a"), _fdictStep("b"),
    ))
    assert dictCost["bAnyStepTimed"] is False
    assert dictCost["iStepsUntimed"] == 2


def test_a_disabled_step_is_not_part_of_the_rerun():
    dictCost = fdictEstimateRerunCost(_fdictWorkflow(
        _fdictStep("skipped", 900.0, bRunEnabled=False),
        _fdictStep("runs", 1.5),
    ))
    assert dictCost["fRecordedSeconds"] == 1.5
    assert dictCost["iStepsUntimed"] == 0


def test_a_step_predating_the_enabled_flag_still_counts():
    """Absent means enabled; the oldest projects must not shrink."""
    dictCost = fdictEstimateRerunCost(_fdictWorkflow(
        _fdictStep("legacy", 2.0),
    ))
    assert dictCost["iStepsTimed"] == 1


def test_a_malformed_run_stat_is_unknown_rather_than_believed():
    """A negative or non-numeric duration is not evidence."""
    for anyValue in (-1.0, "12", None, {}):
        dictCost = fdictEstimateRerunCost(_fdictWorkflow(
            {"sName": "s", "dictRunStats": {"fWallClock": anyValue}},
        ))
        assert dictCost["bAnyStepTimed"] is False, anyValue
        assert dictCost["iStepsUntimed"] == 1, anyValue


def test_the_readiness_payload_carries_the_cost():
    """The modal renders a verdict it never re-derives."""
    import inspect
    from vaibify.reproducibility import levelGates
    sSource = inspect.getsource(levelGates.fdictL3ReadinessGaps)
    assert "dictRerunCost" in sSource
