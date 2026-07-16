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
        "saOutputDataFiles": [sName + "/data.csv"],
        "saPlotFiles": [sName + "/plot.pdf"],
        "bNoInputData": True,
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
    dictStep["saOutputDataFiles"] = []
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


# ------------------------------------------------------------------------
# script-stale promotion to L1
# ------------------------------------------------------------------------


def _fnWriteFile(sRoot, sRelPath, sContent):
    """Write a file under ``sRoot`` at the repo-relative path."""
    import os
    sAbs = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbs) or sAbs, exist_ok=True)
    with open(sAbs, "w") as fileHandle:
        fileHandle.write(sContent)


def _fnWriteManifest(sRoot, listEntries):
    """Write a MANIFEST.sha256 with ``[(sHash, sRelPath), ...]`` entries."""
    import os
    sPath = os.path.join(sRoot, "MANIFEST.sha256")
    with open(sPath, "w", encoding="utf-8", newline="\n") as fileHandle:
        fileHandle.write("# SHA-256 manifest of workflow artefacts\n")
        for sHash, sRelPath in listEntries:
            fileHandle.write(f"{sHash}  {sRelPath}\n")


def testScriptStaleCriterionFiresWhenScriptMtimeNewerThanOutput(tmp_path):
    """A step whose script status is ``modified`` and whose outputs do
    not match MANIFEST.sha256 emits the ``script-stale`` criterion."""
    dictStep = _fdictAllGreenStep()
    dictScriptStatus = {0: {"sStatus": "modified", "listStaleArtifacts": []}}
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, str(tmp_path),
        dictScriptStatus,
    )
    assert len(listBlockers) == 1
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "script-stale"
    assert dictEntry["listOffendingFiles"] == [
        "A/data.csv", "A/plot.pdf",
    ]
    assert dictEntry["listOffendingUpstreamSteps"] == []


def _fdictManifestFriendlyStep():
    """Return a step whose declared outputs resolve cleanly under
    ``sDirectory``. The shared ``_fdictAllGreenStep`` doubles the
    directory prefix at resolution time; this fixture matches the
    production convention where ``saOutputDataFiles`` entries are step-
    directory-relative names (e.g. ``data.csv``), not repo-relative."""
    return {
        "sName": "stepOne", "sDirectory": "stepOne",
        "saOutputDataFiles": ["data.csv"],
        "saPlotFiles": ["plot.pdf"],
        "bNoInputData": True,
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }


def testScriptStaleSuppressedWhenManifestHashMatches(tmp_path):
    """When every declared output's content matches MANIFEST.sha256 the
    script-stale criterion is suppressed even with ``sStatus='modified''."""
    from vaibify.reproducibility.provenanceTracker import fsComputeFileHash
    dictStep = _fdictManifestFriendlyStep()
    _fnWriteFile(str(tmp_path), "stepOne/data.csv", "data-payload")
    _fnWriteFile(str(tmp_path), "stepOne/plot.pdf", "plot-payload")
    import os
    sHashData = fsComputeFileHash(
        os.path.join(str(tmp_path), "stepOne/data.csv"),
    )
    sHashPlot = fsComputeFileHash(
        os.path.join(str(tmp_path), "stepOne/plot.pdf"),
    )
    _fnWriteManifest(str(tmp_path), [
        (sHashData, "stepOne/data.csv"),
        (sHashPlot, "stepOne/plot.pdf"),
    ])
    dictScriptStatus = {0: {"sStatus": "modified", "listStaleArtifacts": []}}
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, str(tmp_path),
        dictScriptStatus,
    )
    assert listBlockers == []


def testUpstreamModifiedBeatsScriptStale(tmp_path):
    """Priority rule: a step that is both upstream-modified and
    script-stale emits ``upstream-modified`` (root cause)."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["bUpstreamModified"] = True
    dictScriptStatus = {0: {"sStatus": "modified", "listStaleArtifacts": []}}
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, str(tmp_path),
        dictScriptStatus,
    )
    assert len(listBlockers) == 1
    assert listBlockers[0]["sCriterion"] == "upstream-modified"


def testScriptStaleBeatsAxisNotGreen(tmp_path):
    """Priority rule: a step that is both axis-failing and script-stale
    emits ``script-stale`` because the edited script invalidates the
    test result that produced the axis verdict."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    dictScriptStatus = {0: {"sStatus": "modified", "listStaleArtifacts": []}}
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, str(tmp_path),
        dictScriptStatus,
    )
    assert len(listBlockers) == 1
    assert listBlockers[0]["sCriterion"] == "script-stale"


def testScriptStaleBlocksStepLevelGate():
    """``fbStepIsAtLeastLevel1`` must return False for a step whose
    script status reports ``modified`` (boolean gate preservation)."""
    from vaibify.reproducibility.levelGates import fbStepIsAtLeastLevel1
    dictStep = _fdictAllGreenStep()
    dictScriptStatus = {0: {"sStatus": "modified", "listStaleArtifacts": []}}
    assert fbStepIsAtLeastLevel1(
        dictStep, dictScriptStatus, iStepIndex=0,
    ) is False
    assert fbStepIsAtLeastLevel1(dictStep) is True


# ------------------------------------------------------------------------
# state-aware axis-not-green hints + per-file hints
# ------------------------------------------------------------------------


def testAxisNotGreenNeverRunHintNamesUntestedCategory():
    """Quantitative never run (others passed) yields the never-run
    language naming the category, not the failing-tests language."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sQuantitative"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sCriterion"] == "axis-not-green"
    assert listBlockers[0]["sRemediationHint"] == (
        "Test category never run (quantitative) — "
        "run tests, then verify"
    )


def testAxisNotGreenFailedHintNamesFailedCategory():
    """A true test failure keeps the failing-tests language and names
    the failed category."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sRemediationHint"] == (
        "Re-run failing tests (integrity), then verify"
    )


def testAxisNotGreenFailedHintDropsRedundantAggregate():
    """When a category failed, the aggregate ``unit`` name is dropped
    from the hint so the researcher sees the root-cause category."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    dictStep["dictVerification"]["sIntegrity"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sRemediationHint"] == (
        "Re-run failing tests (integrity), then verify"
    )


def testAxisNotGreenOutputsMissingHint():
    """``outputs-missing`` dominates ``untested`` in the hint."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-missing"
    dictStep["dictVerification"]["sQualitative"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sRemediationHint"] == (
        "Declared output missing — re-run step, then verify"
    )


def testAxisNotGreenOutputsChangedHintAndPerFileHints():
    """Marker drift produces the drift hint plus per-file hints for
    every offending file (no narrowing without ``listModifiedFiles``)."""
    sExpectedHint = (
        "Output changed since the last test run (file newer than its "
        "verification marker) — re-run tests, then verify"
    )
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-changed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    dictEntry = listBlockers[0]
    assert dictEntry["sRemediationHint"] == sExpectedHint
    assert dictEntry["listOffendingFiles"] == [
        "A/data.csv", "A/plot.pdf",
    ]
    assert dictEntry["dictOffendingFileHints"] == {
        "A/data.csv": sExpectedHint,
        "A/plot.pdf": sExpectedHint,
    }


def testAxisNotGreenMarkerDriftNarrowsOffendingFiles():
    """With ``listModifiedFiles`` populated, the offending files narrow
    to the drifted outputs via the repo-relative path mapping."""
    from vaibify.reproducibility.levelGates import (
        _fdictAxisNotGreenBlocker,
    )
    sExpectedHint = (
        "Output changed since the last test run (file newer than its "
        "verification marker) — re-run tests, then verify"
    )
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-changed"
    dictStep["dictVerification"]["listModifiedFiles"] = ["A/A/data.csv"]
    dictEntry = _fdictAxisNotGreenBlocker(
        _fdictWorkflowWithSteps([dictStep]), 0, dictStep, "/repo",
    )
    assert dictEntry["listOffendingFiles"] == ["A/data.csv"]
    assert dictEntry["dictOffendingFileHints"] == {
        "A/data.csv": sExpectedHint,
    }


def testAxisNotGreenFailedKeepsAllDeclaredOutputs():
    """A true failure never narrows the offending files and emits no
    per-file drift hints."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    dictEntry = listBlockers[0]
    assert dictEntry["listOffendingFiles"] == [
        "A/data.csv", "A/plot.pdf",
    ]
    assert "dictOffendingFileHints" not in dictEntry


def testUpstreamModifiedPerFileHintNamesUpstreamLabel():
    """Each offending file of an upstream-modified step carries a hint
    naming the modified upstream step's label."""
    dictA = _fdictAllGreenStep(sName="A")
    dictB = _fdictAllGreenStep(sName="B")
    dictB["saDataCommands"] = ["python s.py {Step01.data}"]
    dictB["dictVerification"]["bUpstreamModified"] = True
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictA, dictB]),
        {"0": "20", "1": "10"},
        "/repo",
    )
    dictEntry = listBlockers[0]
    sExpectedHint = (
        "Upstream step A01 modified after this output was produced — "
        "re-run this step"
    )
    assert dictEntry["dictOffendingFileHints"] == {
        "B/data.csv": sExpectedHint,
        "B/plot.pdf": sExpectedHint,
    }


def testAttestationStalePerFileHints():
    """Each offending file of an attestation-stale step carries the
    re-verify-or-re-run hint."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"]["sLastUserUpdate"] = (
        "2026-01-01T00:00:00Z"
    )
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    dictEntry = listBlockers[0]
    assert dictEntry["sCriterion"] == "attestation-stale"
    sExpectedHint = (
        "This output changed after you verified — re-verify or re-run"
    )
    assert dictEntry["dictOffendingFileHints"] == {
        "A/data.csv": sExpectedHint,
        "A/plot.pdf": sExpectedHint,
    }


# ------------------------------------------------------------------------
# sSubState on axis-not-green
# ------------------------------------------------------------------------


def testAxisSubStateFailedDominates():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    dictStep["dictVerification"]["sIntegrity"] = "outputs-missing"
    dictStep["dictVerification"]["sQualitative"] = "outputs-changed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sSubState"] == "failed"


def testAxisSubStateOutputsMissingBeatsChangedAndUntested():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-missing"
    dictStep["dictVerification"]["sQualitative"] = "outputs-changed"
    dictStep["dictVerification"]["sQuantitative"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sSubState"] == "outputs-missing"


def testAxisSubStateOutputsChangedBeatsUntested():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-changed"
    dictStep["dictVerification"]["sQuantitative"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sSubState"] == "outputs-changed"


def testAxisSubStateUntestedWhenNothingWorse():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sQuantitative"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sSubState"] == "untested"


def testAxisSubStateMatchesHintForEveryState():
    """The hint must remain a projection of the sub-state — they can
    never disagree about the cause."""
    dictExpectedHintBySubState = {
        "failed": "Re-run failing tests (integrity), then verify",
        "outputs-missing":
            "Declared output missing — re-run step, then verify",
        "outputs-changed": (
            "Output changed since the last test run (file newer than "
            "its verification marker) — re-run tests, then verify"
        ),
        "untested": (
            "Test category never run (integrity) — "
            "run tests, then verify"
        ),
    }
    dictAxisValueBySubState = {
        "failed": "failed",
        "outputs-missing": "outputs-missing",
        "outputs-changed": "outputs-changed",
        "untested": "untested",
    }
    for sSubState, sAxisValue in dictAxisValueBySubState.items():
        dictStep = _fdictAllGreenStep()
        dictStep["dictVerification"]["sIntegrity"] = sAxisValue
        listBlockers = flistLevel1Blockers(
            _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
        )
        assert listBlockers[0]["sSubState"] == sSubState
        assert listBlockers[0]["sRemediationHint"] == (
            dictExpectedHintBySubState[sSubState]
        )


# ------------------------------------------------------------------------
# dictOffendingFileMarks
# ------------------------------------------------------------------------


def testAxisFailedMarksEveryOffendingFileFailed():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "failed"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    dictEntry = listBlockers[0]
    assert dictEntry["dictOffendingFileMarks"] == {
        "A/data.csv": "failed", "A/plot.pdf": "failed",
    }


def testAxisOutputsMissingMarksFilesMissing():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-missing"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["dictOffendingFileMarks"] == {
        "A/data.csv": "missing", "A/plot.pdf": "missing",
    }


def testAxisDriftMarksNarrowedFilesStale():
    """Marks attach after drift narrowing so the keys exactly mirror
    the narrowed ``listOffendingFiles``."""
    from vaibify.reproducibility.levelGates import (
        _fdictAxisNotGreenBlocker,
    )
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sIntegrity"] = "outputs-changed"
    dictStep["dictVerification"]["listModifiedFiles"] = ["A/A/data.csv"]
    dictEntry = _fdictAxisNotGreenBlocker(
        _fdictWorkflowWithSteps([dictStep]), 0, dictStep, "/repo",
    )
    assert dictEntry["listOffendingFiles"] == ["A/data.csv"]
    assert dictEntry["dictOffendingFileMarks"] == {
        "A/data.csv": "stale",
    }


def testAxisUntestedAttachesNoFileMarks():
    """Untested files are not wrong, merely unexercised — marking them
    would misrepresent the dashboard's ground truth."""
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sQuantitative"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert "dictOffendingFileMarks" not in listBlockers[0]


def testUpstreamModifiedMarksAllFilesStale():
    dictA = _fdictAllGreenStep(sName="A")
    dictB = _fdictAllGreenStep(sName="B")
    dictB["saDataCommands"] = ["python s.py {Step01.data}"]
    dictB["dictVerification"]["bUpstreamModified"] = True
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictA, dictB]),
        {"0": "20", "1": "10"},
        "/repo",
    )
    assert listBlockers[0]["dictOffendingFileMarks"] == {
        "B/data.csv": "stale", "B/plot.pdf": "stale",
    }


def testScriptStaleMarksAllFilesStale(tmp_path):
    dictStep = _fdictAllGreenStep()
    dictScriptStatus = {0: {"sStatus": "modified", "listStaleArtifacts": []}}
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, str(tmp_path),
        dictScriptStatus,
    )
    assert listBlockers[0]["sCriterion"] == "script-stale"
    assert listBlockers[0]["dictOffendingFileMarks"] == {
        "A/data.csv": "stale", "A/plot.pdf": "stale",
    }


def testAttestationStaleMarksAllFilesStale():
    dictStep = _fdictAllGreenStep()
    dictStep["dictVerification"]["sUser"] = "stale"
    dictStep["dictVerification"]["sLastUserUpdate"] = (
        "2026-05-01 00:00:00 UTC"
    )
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sCriterion"] == "attestation-stale"
    assert listBlockers[0]["dictOffendingFileMarks"] == {
        "A/data.csv": "stale", "A/plot.pdf": "stale",
    }


def testFileMarkKeysMirrorOffendingFilesForEveryEmitter():
    """Structural contract: whenever ``dictOffendingFileMarks`` is
    present its keys are exactly ``listOffendingFiles``."""
    listScenarios = []
    dictFailed = _fdictAllGreenStep()
    dictFailed["dictVerification"]["sIntegrity"] = "failed"
    listScenarios.append(dictFailed)
    dictStale = _fdictAllGreenStep()
    dictStale["dictVerification"]["sUser"] = "stale"
    dictStale["dictVerification"]["sLastUserUpdate"] = (
        "2026-05-01 00:00:00 UTC"
    )
    listScenarios.append(dictStale)
    for dictStep in listScenarios:
        listBlockers = flistLevel1Blockers(
            _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
        )
        dictEntry = listBlockers[0]
        assert sorted(dictEntry["dictOffendingFileMarks"].keys()) == (
            sorted(dictEntry["listOffendingFiles"])
        )
        for sMark in dictEntry["dictOffendingFileMarks"].values():
            assert sMark in ("stale", "failed", "missing")


# ------------------------------------------------------------------------
# input-data-undeclared
# ------------------------------------------------------------------------


def test_undeclared_input_data_blocks_level1():
    """An otherwise all-green step without an input declaration is
    not self-consistent: nothing distinguishes 'no raw inputs' from
    'nobody looked'."""
    dictStep = _fdictAllGreenStep()
    del dictStep["bNoInputData"]
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert len(listBlockers) == 1
    assert listBlockers[0]["sCriterion"] == "input-data-undeclared"
    assert fbAtLeastLevel1(
        _fdictWorkflowWithSteps([dictStep]), "/repo",
    ) is False


def test_listed_input_files_satisfy_the_declaration():
    dictStep = _fdictAllGreenStep()
    del dictStep["bNoInputData"]
    dictStep["saInputDataFiles"] = ["data/observations.csv"]
    assert flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    ) == []


def test_no_input_data_flag_satisfies_the_declaration():
    dictStep = _fdictAllGreenStep()
    assert dictStep["bNoInputData"] is True
    assert flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    ) == []


def test_undeclared_beats_every_other_criterion():
    """Priority: the declaration gap outranks freshness signals."""
    dictStep = _fdictAllGreenStep()
    del dictStep["bNoInputData"]
    dictStep["dictVerification"]["bUpstreamModified"] = True
    dictStep["dictVerification"]["sUnitTest"] = "failed"
    dictStep["dictVerification"]["sUser"] = "untested"
    listBlockers = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    )
    assert listBlockers[0]["sCriterion"] == "input-data-undeclared"


def test_ai_declaration_step_exempt_from_input_declaration():
    dictStep = _fdictAllGreenStep()
    del dictStep["bNoInputData"]
    dictStep["sStepKind"] = "ai-declaration"
    assert flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictStep]), {}, "/repo",
    ) == []


def test_declaration_toggle_busts_the_blocker_cache():
    """Two workflows differing only in bNoInputData must not share a
    cached blocker list — checking the box has to clear the blocker
    on the very next evaluation."""
    dictUndeclared = _fdictAllGreenStep()
    del dictUndeclared["bNoInputData"]
    listBefore = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictUndeclared]), {}, "/repo",
    )
    assert len(listBefore) == 1
    dictDeclared = _fdictAllGreenStep()
    listAfter = flistLevel1Blockers(
        _fdictWorkflowWithSteps([dictDeclared]), {}, "/repo",
    )
    assert listAfter == []
