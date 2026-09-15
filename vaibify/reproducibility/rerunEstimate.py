"""What a Level 3 rerun is likely to cost, from what the steps took.

The confirmation modal used to warn that a rerun "can take hours",
which for a project whose two steps take five seconds each is not
caution but noise -- and noise in a safety notice is how researchers
learn to click through safety notices. Vaibify already records
``fWallClock`` for every step it has run, so it can say what this
project actually took instead of guessing across four orders of
magnitude (researcher-reported, 2026-09-15).

WHAT THIS IS AND IS NOT

It is the sum of the LAST RECORDED wall-clock of the steps that will
run. It is not a prediction: a step whose input grew, a machine under
different load, or a cold image pull will all diverge from it, and the
renderer says "last time" rather than "will take".

Two things make it a FLOOR rather than an estimate, and both are
reported rather than folded in:

* A step vaibify has never run contributes nothing, so a project with
  untimed steps will take longer than this number says.
* The rerun is more than the steps. It exports the project, acquires
  the pinned image -- which may mean loading a multi-gigabyte archive
  -- starts a container, and hashes every pinned file. None of that is
  in ``fWallClock``.

Reporting the count of untimed steps separately is what lets the
renderer refuse to show a total at all when nothing has been timed.
Showing "0 seconds" there would be the same lie as "hours", pointing
the other way.
"""

__all__ = [
    "fdictEstimateRerunCost",
]


def _fbStepWillRun(dictStep):
    """Return True unless the step is explicitly disabled.

    Absent means enabled: every step predating the flag ran, and
    treating a missing key as "off" would silently shrink the estimate
    on exactly the oldest projects.
    """
    return dictStep.get("bRunEnabled", True) is not False


def _ffRecordedSecondsOf(dictStep):
    """Return the step's last recorded wall-clock, or None.

    ``None`` rather than 0.0, because "never run" and "ran instantly"
    are different facts and only one of them makes the total a floor.
    """
    dictRunStats = dictStep.get("dictRunStats")
    if not isinstance(dictRunStats, dict):
        return None
    fSeconds = dictRunStats.get("fWallClock")
    if not isinstance(fSeconds, (int, float)) or fSeconds < 0:
        return None
    return float(fSeconds)


def fdictEstimateRerunCost(dictWorkflow):
    """Return what the steps that will run last took, and what is unknown."""
    listSteps = [
        dictStep for dictStep in (dictWorkflow or {}).get("listSteps") or []
        if isinstance(dictStep, dict) and _fbStepWillRun(dictStep)
    ]
    listSeconds = [
        fSeconds for fSeconds in (
            _ffRecordedSecondsOf(dictStep) for dictStep in listSteps
        ) if fSeconds is not None
    ]
    return {
        "fRecordedSeconds": round(sum(listSeconds), 1),
        "iStepsTimed": len(listSeconds),
        "iStepsUntimed": len(listSteps) - len(listSeconds),
        # The one flag the renderer needs to decide between a figure
        # and an honest silence.
        "bAnyStepTimed": bool(listSeconds),
    }
