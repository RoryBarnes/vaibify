"""A command missing from the SHADOW is a finding, not an error message.

The shadow container is built from the image digest the envelope pins,
never from the researcher's running project container. That difference
is the entire reason the shadow exists: a tool pip-installed into a
live container after its image was built is absent from the image, so
nobody reproducing from the published envelope would have it.

Reported as "command not found: pytest" that finding is thrown away —
the researcher reads it as vaibify failing to find a tool they can
plainly see on their own PATH. Verified in exactly that form: a
project whose live container carried
``/home/researcher/.local/bin/pytest`` pinned an image with no
``pytest`` at all (2026-09-01).

The recovery of the command name is pinned against the REAL validator
here, not against a hand-typed copy of its message. A parser carrying
its own copy of the format drifts silently the first time the wording
is edited, and the wording has already been edited once this week.
"""

import asyncio

import pytest

from vaibify.gui.pipelineValidator import _fnValidateSingleCommand
from vaibify.reproducibility.shadowRerun import (
    _fnExplainAMissingCommandAsAnImageGap,
    flistNameCommandsMissingFromTheImage,
)


class _ConnectionFindingNothing:
    """Answers every command probe with "not found"."""

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        del sContainerId, sCommand
        return 1, ""


def _flistRealValidatorErrors(sCommand):
    """Return the errors the REAL validator emits for a missing command."""
    listErrors = []
    _fnValidateSingleCommand(
        _ConnectionFindingNothing(), "cid", sCommand,
        "/shadow/repo/Step", 1, "Step", listErrors,
    )
    return listErrors


def test_the_command_is_recovered_from_the_real_validators_message():
    """Pinned to the validator's output, never to a copy of its format."""
    listErrors = _flistRealValidatorErrors("pytest")
    assert listErrors, "the validator reported no error to parse"
    assert flistNameCommandsMissingFromTheImage(listErrors) == ["pytest"]


def test_a_command_with_arguments_recovers_only_the_command():
    """The message names the script, so the parse must not take the tail."""
    listErrors = _flistRealValidatorErrors(
        "python makePlot.py --out figure.png",
    )
    assert flistNameCommandsMissingFromTheImage(listErrors) == [
        "makePlot.py",
    ]


def test_the_same_command_in_several_steps_is_named_once():
    """A researcher installs pytest once, not once per step."""
    listErrors = (
        _flistRealValidatorErrors("pytest")
        + _flistRealValidatorErrors("pytest")
    )
    assert flistNameCommandsMissingFromTheImage(listErrors) == ["pytest"]


def test_an_unrelated_preflight_error_names_no_command():
    """A missing DIRECTORY is a different fault and must not be explained
    as an image gap: the copy was incomplete, not the image."""
    assert flistNameCommandsMissingFromTheImage([
        "Step 1 (Alpha): directory does not exist: /shadow/repo/Alpha",
    ]) == []


@pytest.mark.falsification
def test_the_outcome_carries_the_explanation_not_just_the_error():
    """The finding must reach the record, or it reaches nobody.

    Kills: in _fnExplainAMissingCommandAsAnImageGap, return before
    setting the keys — the outcome carries the raw "command not found"
    and the researcher is left reading it as vaibify failing to find a
    tool that is plainly on their own PATH.
    """
    dictOutcome = {
        "bPassed": False,
        "dictRerunFailure": {
            "sKind": "preflight",
            "listErrors": _flistRealValidatorErrors("pytest"),
        },
    }
    _fnExplainAMissingCommandAsAnImageGap(dictOutcome)
    dictFailure = dictOutcome["dictRerunFailure"]
    assert dictFailure["listCommandsMissingFromImage"] == ["pytest"]
    assert "envelope pins" in dictFailure["sImageGapNote"]
    # The raw errors survive: a researcher who doubts the explanation
    # must still be able to read what was actually checked.
    assert dictFailure["listErrors"], dictFailure


def test_a_failure_naming_no_command_gains_no_note():
    """An explanation attached to the wrong fault is worse than none."""
    dictOutcome = {
        "dictRerunFailure": {
            "sKind": "preflight",
            "listErrors": ["Step 1 (Alpha): directory does not exist: /x"],
        },
    }
    _fnExplainAMissingCommandAsAnImageGap(dictOutcome)
    assert "sImageGapNote" not in dictOutcome["dictRerunFailure"]
