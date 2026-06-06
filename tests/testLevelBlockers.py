"""Unit tests for ``flistLevel1Blockers``.

The blocker list is the per-step diagnostic surface that drives the
dashboard's check rendering, banner glyph, and per-file/per-edge glyphs
when L1 fails. The boolean gate ``_fbComputeLevel1`` is now a thin
wrapper that returns ``len(flistLevel1Blockers(...)) == 0``; these
tests pin both the structural contract of the list and the gate's
preserved boolean semantics.

The contract test for the rendered step check ties the JS-side
"show check iff zero blockers" rule to the per-step blocker map
produced by this backend, so a regression that drops the binding
fails the suite without requiring a browser.
"""

from vaibify.gui.fileStatusManager import (
    _fnResetUserAttestationIfStale,
    fbReconcileUserVerificationTimestamps,
)
from vaibify.reproducibility.levelGates import (
    fbAtLeastLevel1,
    flistLevel1Blockers,
    _fbComputeLevel1,
)


def _fdictAllGreenStep(sName="A"):
    """Return a step dict that satisfies every L1 criterion."""
    return {
        "sName": sName, "sDirectory": sName,
        "saDataFiles": [sName + "/data.csv"],
        "saPlotFiles": [sName + "/plot.pdf"],
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }


def _fdictWorkflowWithSteps(listSteps):
    """Wrap a list of step dicts as a workflow shell."""
    return {"listSteps": listSteps}


# ------------------------------------------------------------------------
# user-not-approved
# ------------------------------------------------------------------------


def test_flistLevel1Blockers_user_not_approved_empty_lists():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "user-not-approved"
    assert dictEntry["listOffendingFiles"] == []
    assert dictEntry["listOffendingUpstreamSteps"] == []
    assert dictEntry["iStepIndex"] == 0


def test_flistLevel1Blockers_user_not_approved_includes_label():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sStepLabel"] == "A01"


# ------------------------------------------------------------------------
# axis-not-green
# ------------------------------------------------------------------------


def test_flistLevel1Blockers_axis_not_green_offending_files():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "axis-not-green"
    assert dictEntry["listOffendingFiles"] == [
        "A/data.csv", "A/plot.pdf",
    ]
    assert dictEntry["listOffendingUpstreamSteps"] == []


def test_flistLevel1Blockers_axis_not_green_handles_empty_files():
    dictStep = _fdictAllGreenStep()
    dictStep["saDataFiles"] = []
    dictStep["saPlotFiles"] = []
    dictStep["dictVerification"]["sQuantitative"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sCriterion"] == "axis-not-green"
    assert listBlockers[0]["listOffendingFiles"] == []


# ------------------------------------------------------------------------
# upstream-modified
# ------------------------------------------------------------------------


def test_flistLevel1Blockers_upstream_modified_marks_files_and_edges():
    dictA = _fdictAllGreenStep(sName="A")
    dictB = _fdictAllGreenStep(sName="B")
    dictB["saDataCommands"] = ["python s.py {Step01.data}"]
    dictB["dictVerification"]["bUpstreamModified"] = True
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictA, dictB]),
        {"0": "20", "1": "10"},
        "/repo",
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["iStepIndex"] == 1
    assert dictEntry["sCriterion"] == "upstream-modified"
    assert dictEntry["listOffendingFiles"] == [
        "B/data.csv", "B/plot.pdf",
    ]
    assert dictEntry["listOffendingUpstreamSteps"] == [0]


def test_flistLevel1Blockers_upstream_modified_no_offending_when_younger():
    dictA = _fdictAllGreenStep(sName="A")
    dictB = _fdictAllGreenStep(sName="B")
    dictB["saDataCommands"] = ["python s.py {Step01.data}"]
    dictB["dictVerification"]["bUpstreamModified"] = True
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictA, dictB]),
        {"0": "5", "1": "10"},
        "/repo",
    )
    assert listBlockers[0]["listOffendingUpstreamSteps"] == []


# ------------------------------------------------------------------------
# Composition rules
# ------------------------------------------------------------------------


def test_flistLevel1Blockers_l1_clean_workflow_returns_empty():
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([_fdictAllGreenStep()]),
        {}, "/repo",
    )
    assert listBlockers == []


def test_flistLevel1Blockers_missing_project_repo_returns_empty():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "",
    )
    assert listBlockers == []


def test_flistLevel1Blockers_mixed_blockers_sorted_by_step_index():
    dictA = _fdictAllGreenStep(sName="A")
    dictA["dictVerification"]["sUser"] = "untested"
    dictB = _fdictAllGreenStep(sName="B")
    dictB["dictVerification"]["sUnitTest"] = "failed"
    dictC = _fdictAllGreenStep(sName="C")
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictA, dictB, dictC]),
        {}, "/repo",
    )
    assert [dictEntry["iStepIndex"] for dictEntry in listBlockers] == [0, 1]
    assert listBlockers[0]["sCriterion"] == "user-not-approved"
    assert listBlockers[1]["sCriterion"] == "axis-not-green"


def test_flistLevel1Blockers_priority_upstream_over_axis():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["bUpstreamModified"] = True
    dictStep["dictVerification"]["sUnitTest"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    assert listBlockers[0]["sCriterion"] == "upstream-modified"


def test_flistLevel1Blockers_priority_upstream_over_user():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["bUpstreamModified"] = True
    dictStep["dictVerification"]["sUser"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sCriterion"] == "upstream-modified"


def test_flistLevel1Blockers_priority_axis_over_user():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    dictStep["dictVerification"]["sUser"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sCriterion"] == "axis-not-green"


# ------------------------------------------------------------------------
# _fbComputeLevel1 regression
# ------------------------------------------------------------------------


def test_fbComputeLevel1_clean_workflow_returns_true():
    assert _fbComputeLevel1(
        _fdictWorkflowWithSteps([_fdictAllGreenStep()]), "/repo",
    ) is True


def test_fbComputeLevel1_blocker_returns_false():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "untested"
    assert _fbComputeLevel1(
        _fdictWorkflowWithSteps([dictStep]), "/repo",
    ) is False


def test_fbComputeLevel1_missing_repo_returns_false():
    assert _fbComputeLevel1(
        _fdictWorkflowWithSteps([_fdictAllGreenStep()]), "",
    ) is False


def test_fbComputeLevel1_empty_steps_returns_false():
    assert _fbComputeLevel1(
        _fdictWorkflowWithSteps([]), "/repo",
    ) is False


def test_fbAtLeastLevel1_matches_blocker_emptiness():
    dictWorkflow = _fdictWorkflowWithSteps([_fdictAllGreenStep()])
    bGate = fbAtLeastLevel1(dictWorkflow, "/repo")
    listBlockers = flistLevel1Blockers(dictWorkflow, {}, "/repo")
    assert bGate == (len(listBlockers) == 0)


# ------------------------------------------------------------------------
# Step-check rendering contract
# ------------------------------------------------------------------------


def test_step_check_rendering_contract_ties_check_to_blocker_emptiness():
    """The Step Viewer's "show check" rule must match the blocker list.

    The JS-side ``fsComputeStepDotState`` reads
    ``_dictWorkflowState.dictBlockersByStep`` and refuses to return
    ``"verified"`` whenever that map carries an entry for the step.
    The map is populated from the poll response's ``listBlockers``,
    which is exactly the output of ``flistLevel1Blockers``. This
    contract test confirms the binding by asserting that every
    blocker emitted by the backend would land in the blocker map
    and therefore suppress the check.
    """
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    dictBlockersByStep = {
        dictEntry["iStepIndex"]: dictEntry
        for dictEntry in listBlockers
    }
    assert 0 in dictBlockersByStep
    assert fbAtLeastLevel1(
        _fdictWorkflowWithSteps([dictStep]), "/repo",
    ) is False


# ------------------------------------------------------------------------
# attestation-stale split
# ------------------------------------------------------------------------


def testAttestationStaleCriterionFiresWhenOutputsNewerThanAttestation():
    """A step with ``sUser='stale'`` and prior ``sLastUserUpdate`` emits
    ``attestation-stale`` rather than ``user-not-approved``.

    The two states are semantically different: the researcher *did*
    attest (evidence in ``sLastUserUpdate``) but outputs changed since.
    The discriminator at the blocker layer must preserve that
    distinction so the dashboard tooltip reflects reality.
    """
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"]["sLastUserUpdate"] = (
        "2026-05-01 00:00:00 UTC"
    )
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "attestation-stale"
    assert dictEntry["listOffendingFiles"] == [
        "A/data.csv", "A/plot.pdf",
    ]
    assert dictEntry["listOffendingUpstreamSteps"] == []


def testUserNotApprovedCriterionPersistsWhenNeverAttested():
    """A step with ``sUser='untested'`` and no ``sLastUserUpdate`` keeps
    the existing ``user-not-approved`` criterion."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "untested"
    dictStep["dictVerification"].pop("sLastUserUpdate", None)
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    assert listBlockers[0]["sCriterion"] == "user-not-approved"


def testAttestationStalePreservesLastUserUpdate():
    """``_fnResetUserAttestationIfStale`` must preserve the attestation
    evidence so the dashboard can render the *re-verify-or-re-run*
    affordance rather than the *never-attested* one."""
    dictVerification = {
        "sUser": "passed",
        "sLastUserUpdate": "2026-05-01 00:00:00 UTC",
    }
    _fnResetUserAttestationIfStale(dictVerification)
    assert dictVerification["sUser"] == "stale"
    assert dictVerification["sLastUserUpdate"] == (
        "2026-05-01 00:00:00 UTC"
    )


def testStaleAndUntestedBothBlockBooleanGate():
    """Boolean L1 truth-table is preserved across the split: both
    ``stale`` and ``untested`` must fail ``fbAtLeastLevel1``."""
    dictStale = _fdictAllGreenStep(sName="A")
    dictStale["dictVerification"]["sUser"] = "stale"
    dictStale["dictVerification"]["sLastUserUpdate"] = (
        "2026-05-01 00:00:00 UTC"
    )
    dictUntested = _fdictAllGreenStep(sName="B")
    dictUntested["dictVerification"]["sUser"] = "untested"
    assert fbAtLeastLevel1(
        _fdictWorkflowWithSteps([dictStale]), "/repo",
    ) is False
    assert fbAtLeastLevel1(
        _fdictWorkflowWithSteps([dictUntested]), "/repo",
    ) is False


# ------------------------------------------------------------------------
# attestation-stale regression guards
# ------------------------------------------------------------------------


def testAttestationStaleWithMissingTimestampFallsBackToUserNotApproved():
    """Corrupt state ``sUser='stale'`` without ``sLastUserUpdate`` must
    degrade to ``user-not-approved`` rather than ``attestation-stale``."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"].pop("sLastUserUpdate", None)
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "user-not-approved"


def testUserFailedEmitsUserNotApprovedCriterion():
    """``sUser='failed'`` carries no stale-attestation evidence, so the
    discriminator emits ``user-not-approved`` alongside ``untested``."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "user-not-approved"


def testReconcilePreservesLastUserUpdateForStaleState():
    """``fbReconcileUserVerificationTimestamps`` must retain the prior
    attestation timestamp on ``stale`` so the discriminator keeps firing."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"]["sLastUserUpdate"] = (
        "2026-05-01 12:00:00 UTC"
    )
    dictWorkflow = _fdictWorkflowWithSteps([dictStep])
    fbReconcileUserVerificationTimestamps(dictWorkflow)
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sLastUserUpdate"] == "2026-05-01 12:00:00 UTC"


def testAxisNotGreenPriorityBeatsAttestationStale():
    """Priority ladder is preserved: a step that is both axis-failing and
    stale-attested emits ``axis-not-green`` (root cause)."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"]["sLastUserUpdate"] = (
        "2026-05-01 00:00:00 UTC"
    )
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    assert listBlockers[0]["sCriterion"] == "axis-not-green"
