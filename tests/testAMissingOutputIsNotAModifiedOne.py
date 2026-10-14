"""A cleaned step's missing outputs are expected, not "changed".

Researcher-reported (QuickStart section 5, 2026-10-13): after Clean
Outputs, every step's warning said its outputs had "changed after you
verified". Nobody had verified, and nothing had changed: the files were
gone because the researcher removed them. The committed test markers
still recorded their digests, and the poll judged each missing file as
drift on every tick.

A missing file now invalidates only a step that claims results about
it. The guard that matters is the other direction: a step that DOES
claim results must still be invalidated when an output vanishes, and
must not keep Level 1 -- a deleted plot of a signed-off step is caught
only by its modified-files list.

These drive the real poll (``tests/markerPollHarness.py``).
"""

import pytest

from tests.markerPollHarness import MarkerPollProject, fsOutputRelativePath


DICT_CLEANED_VERIFICATION = {
    "sUnitTest": "untested", "sIntegrity": "untested",
    "sQualitative": "untested", "sQuantitative": "untested",
    "sUser": "untested",
}
DICT_MISSING_ENTRY = {
    "sSha256": None, "sBlobSha": None, "bMissing": True,
    "sSymlinkSegment": None, "bEscapesRoot": False,
}


def _fprojectWithStepCMissing(dictVerificationOfC):
    """Return a project whose step C output is gone, C verified as given."""
    project = MarkerPollProject(dictHashOverrides={
        fsOutputRelativePath("C"): dict(DICT_MISSING_ENTRY),
    })
    project.dictWorkflow["listSteps"][2]["dictVerification"] = dict(
        dictVerificationOfC)
    return project


@pytest.mark.falsification
def testACleanedStepsMissingOutputIsNotListedAsModified():
    """Kills: judging a missing output as a modification of a step that
    claims nothing about it."""
    project = _fprojectWithStepCMissing(DICT_CLEANED_VERIFICATION)
    dictAnswer = project.fdictRunPoll()
    assert not project.fdictVerificationOf("C").get("listModifiedFiles")
    assert 2 not in dictAnswer["dictInvalidatedSteps"]


def testACleanedProjectIsAFixedPointOfThePoll():
    """No invalidation, no save, on the polls after a clean."""
    project = _fprojectWithStepCMissing(DICT_CLEANED_VERIFICATION)
    project.fdictRunPoll()
    project.dictCtx["save"].reset_mock()
    dictSecond = project.fdictRunPoll()
    assert dictSecond["dictInvalidatedSteps"] == {}
    assert project.dictCtx["save"].call_count == 0


@pytest.mark.parametrize("dictVerificationOfC", [
    dict(DICT_CLEANED_VERIFICATION, sUser="passed"),
    dict(DICT_CLEANED_VERIFICATION, sUser="stale"),
    dict(DICT_CLEANED_VERIFICATION, sUnitTest="passed"),
    dict(DICT_CLEANED_VERIFICATION, sQuantitative="passed-from-marker"),
], ids=["signed-off", "sign-off-stale", "unit-tests-passed",
        "category-passed-from-marker"])
def testAClaimingStepsMissingOutputIsStillModified(dictVerificationOfC):
    """Each kind of claim keeps a vanished output listed."""
    project = _fprojectWithStepCMissing(dictVerificationOfC)
    project.fdictRunPoll()
    assert fsOutputRelativePath("C") in project.fdictVerificationOf(
        "C")["listModifiedFiles"]


@pytest.mark.falsification
def testASignedOffStepWhoseOutputVanishesLosesLevelOne():
    """Kills: dropping a missing output for a step that claims results,
    which would let a signed-off step keep Level 1 over a deleted file."""
    project = _fprojectWithStepCMissing({
        "sUnitTest": "passed", "sIntegrity": "passed",
        "sQualitative": "passed", "sQuantitative": "passed",
        "sUser": "passed",
    })
    dictAnswer = project.fdictRunPoll()
    assert fsOutputRelativePath("C") in project.fdictVerificationOf(
        "C")["listModifiedFiles"]
    assert dictAnswer["dictStepLevels"]["2"]["s1"]["sState"] != "attained"
    assert dictAnswer["iProofLevel"] == 0
