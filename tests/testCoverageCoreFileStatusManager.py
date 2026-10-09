"""Verification-state transitions and path helpers of fileStatusManager.

fileStatusManager is the verification state machine, so nothing in it
is mocked here: every test builds a small workflow dict and asserts the
transition the documented invariants require (vaibify/gui/AGENTS.md,
"Verification state machine"). Step ids are distinct from step names
and directories so an id/name mix-up cannot pass by coincidence.
"""

import logging

import pytest

from vaibify.gui import fileStatusManager


S_REPO = "/repository"


def fdictBuildTwoStepWorkflow():
    """Return a workflow where stepBeta consumes stepAlpha's data file."""
    return {
        "sProjectRepoPath": S_REPO,
        "listSteps": [
            {
                "sStepId": "step-alpha", "sName": "stepAlpha",
                "sDirectory": "stepAlpha",
                "saDataCommands": ["python produce.py"],
                "saOutputDataFiles": ["dataFile.csv"],
                "saPlotFiles": ["figure.pdf"],
                "dictVerification": {
                    "sUnitTest": "passed", "sUser": "passed",
                    "sLastUserUpdate": "2020-01-01 00:00 UTC",
                },
            },
            {
                "sStepId": "step-beta", "sName": "stepBeta",
                "sDirectory": "stepBeta",
                "saDataCommands": ["python use.py {step:step-alpha.dataFile}"],
                "saOutputDataFiles": ["derived.csv"],
                "dictVerification": {
                    "sUnitTest": "passed", "sIntegrity": "passed",
                },
            },
        ],
    }


def testDataChangeResetsUnitTestAndFlagsTheDownstreamStep():
    """A changed data file demotes its step and marks every consumer."""
    dictWorkflow = fdictBuildTwoStepWorkflow()
    sChanged = f"{S_REPO}/stepAlpha/dataFile.csv"
    dictInvalidated = fileStatusManager._fdictInvalidateAffectedSteps(
        dictWorkflow, {0: [sChanged], 9: ["/outOfRange"]},
        {sChanged: "100"}, S_REPO,
    )
    dictAlpha = dictWorkflow["listSteps"][0]["dictVerification"]
    dictBeta = dictWorkflow["listSteps"][1]["dictVerification"]
    assert dictAlpha["sUnitTest"] == "untested"
    assert dictAlpha["sUser"] == "passed"
    assert dictAlpha["listModifiedFiles"] == ["stepAlpha/dataFile.csv"]
    assert dictBeta["bUpstreamModified"] is True
    assert dictBeta["sUnitTest"] == "untested"
    assert dictBeta["sIntegrity"] == "untested"
    assert sorted(dictInvalidated) == [0, 1]


def testPlotNewerThanAttestationMarksUserStaleAndKeepsTimestamp():
    """Stale by-eye evidence flips sUser to stale, never erasing its date."""
    dictWorkflow = fdictBuildTwoStepWorkflow()
    dictVars = {"sPlotDirectory": "Plot", "sFigureType": "pdf",
                "sRepoRoot": S_REPO}
    bChanged = fileStatusManager._fbCheckStaleUserVerification(
        dictWorkflow, {f"{S_REPO}/stepAlpha/figure.pdf": "1700000000"},
        dictVars,
    )
    dictAlpha = dictWorkflow["listSteps"][0]["dictVerification"]
    assert bChanged is True
    assert dictAlpha["sUser"] == "stale"
    assert dictAlpha["sLastUserUpdate"] == "2020-01-01 00:00 UTC"


def testFreshPlotClearsLeftoverModificationFlags():
    """A plot older than the attestation clears stale modification flags."""
    dictWorkflow = fdictBuildTwoStepWorkflow()
    dictAlpha = dictWorkflow["listSteps"][0]["dictVerification"]
    dictAlpha["listModifiedFiles"] = ["stepAlpha/figure.pdf"]
    dictAlpha["bOutputModified"] = True
    dictVars = {"sPlotDirectory": "Plot", "sFigureType": "pdf",
                "sRepoRoot": S_REPO}
    bChanged = fileStatusManager._fbCheckStaleUserVerification(
        dictWorkflow, {f"{S_REPO}/stepAlpha/figure.pdf": "100"}, dictVars,
    )
    assert bChanged is True
    assert dictAlpha["sUser"] == "passed"
    assert "listModifiedFiles" not in dictAlpha
    assert "bOutputModified" not in dictAlpha
    assert fileStatusManager._fbCheckStaleUserVerification(
        dictWorkflow, {f"{S_REPO}/stepAlpha/figure.pdf": "100"}, dictVars,
    ) is False


def testUnparseableAttestationTimestampIsNotJudged():
    """A garbled sLastUserUpdate skips the check instead of guessing."""
    dictWorkflow = fdictBuildTwoStepWorkflow()
    dictAlpha = dictWorkflow["listSteps"][0]["dictVerification"]
    dictAlpha["sLastUserUpdate"] = "yesterday-ish"
    assert fileStatusManager._fbCheckStaleUserVerification(
        dictWorkflow, {f"{S_REPO}/stepAlpha/figure.pdf": "1700000000"},
    ) is False
    assert dictAlpha["sUser"] == "passed"


@pytest.mark.parametrize(
    "sTimestamp,iExpected",
    [
        ("2020-01-01 00:00 UTC", 1577836800),
        ("2020-01-01 00:00:30 UTC", 1577836830),
        ("not a time", None),
        (None, None),
    ],
)
def testUtcTimestampParsingAcceptsBothPrecisionsOnly(sTimestamp, iExpected):
    """Minute and second precision parse; anything else is None."""
    assert fileStatusManager._fiParseUtcTimestamp(sTimestamp) == iExpected


def testClearingModificationStateIgnoresOutOfRangeIndex():
    """An index past the step list changes nothing; a valid one clears."""
    dictWorkflow = fdictBuildTwoStepWorkflow()
    dictAlpha = dictWorkflow["listSteps"][0]["dictVerification"]
    dictAlpha["listModifiedFiles"] = ["a"]
    dictAlpha["bOutputModified"] = True
    fileStatusManager._fnClearStepModificationState(dictWorkflow, 5)
    fileStatusManager._fnClearStepModificationState(dictWorkflow, -1)
    assert dictAlpha["listModifiedFiles"] == ["a"]
    fileStatusManager._fnClearStepModificationState(dictWorkflow, 0)
    assert "listModifiedFiles" not in dictAlpha
    assert "bOutputModified" not in dictAlpha


def testMaximumMtimesAreComputedPerStepAndKind():
    """Plot, data and test-category mtimes resolve against the repo root."""
    dictWorkflow = fdictBuildTwoStepWorkflow()
    dictModTimes = {
        f"{S_REPO}/stepAlpha/figure.pdf": "50",
        f"{S_REPO}/stepAlpha/dataFile.csv": "70",
        f"{S_REPO}/stepBeta/derived.csv": "80",
        f"{S_REPO}/stepAlpha/tests/test_integrity_stepAlpha.py": "5",
    }
    assert fileStatusManager._fdictComputeMaxPlotMtimeByStep(
        dictWorkflow, dictModTimes,
    ) == {"0": "50"}
    assert fileStatusManager._fdictComputeMaxDataMtimeByStep(
        dictWorkflow, dictModTimes,
    ) == {"0": "70", "1": "80"}
    assert fileStatusManager._fdictComputeTestCategoryMtimes(
        dictWorkflow, dictModTimes,
    ) == {"0": {"integrity": "5"}}
    assert fileStatusManager._fdictComputeMaxTestSourceMtimeByStep(
        dictWorkflow, dictModTimes,
    ) == {"0": "5"}
    assert fileStatusManager._fdictComputeMaxInputMtimeByStep(
        dictWorkflow, dictModTimes,
    ) == {}


def testRecordedTestPathWinsOverTheDerivedName():
    """An agent-chosen test file name is the one whose mtime is tracked."""
    dictStep = {
        "sDirectory": "stepAlpha",
        "dictTests": {"dictQuantitative": {
            "sFilePath": "stepAlpha/tests/test_numbers_custom.py",
        }},
    }
    dictPaths = fileStatusManager._fdictResolveCategoryTestPaths(
        dictStep, {"sRepoRoot": S_REPO},
    )
    assert dictPaths["quantitative"] == (
        f"{S_REPO}/stepAlpha/tests/test_numbers_custom.py"
    )
    assert fileStatusManager._fdictResolveCategoryTestPaths(
        {"sDirectory": ""}, {"sRepoRoot": S_REPO},
    ) == {}


def testTestPathAbsolutizationNeedsARepoRoot():
    """Absolute stays absolute; relative without a root stays relative."""
    assert fileStatusManager._fsAbsolutizeTestPath("/abs/t.py", S_REPO) == (
        "/abs/t.py"
    )
    assert fileStatusManager._fsAbsolutizeTestPath("rel/t.py", "") == "rel/t.py"
    assert fileStatusManager._fsAbsFromRepoRelative("/abs/x", S_REPO) == "/abs/x"


def testMarkerIdentityMustMatchLabelAndDirectory():
    """A marker from another step (label or directory) is not this step's."""
    dictStep = {"sLabel": "A01", "sDirectory": "/stepAlpha"}
    assert fileStatusManager._fbMarkerIdentityMatchesStep(
        dictStep, {"sLabel": "A01", "sDirectory": "stepAlpha"},
    ) is True
    assert fileStatusManager._fbMarkerIdentityMatchesStep(
        dictStep, {"sLabel": "A02", "sDirectory": "stepAlpha"},
    ) is False
    assert fileStatusManager._fbMarkerIdentityMatchesStep(
        dictStep, {"sLabel": "A01", "sDirectory": "stepBeta"},
    ) is False
    assert fileStatusManager._fsRepoRelDirectory("") == ""


def testMarkerVerdictsSkipNonDictMarkersAndUnknownSteps():
    """Only in-range dict markers are judged.

    The lane once built mtime hints per marker and skipped the same
    shapes; it now judges digests against the poll snapshot and must
    skip them for the same reason: a non-dict marker and a marker for a
    step that does not exist describe nothing the workflow has.
    """
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    dictWorkflow = fdictBuildTwoStepWorkflow()
    dictMarkers = {
        0: {"dictOutputHashes": {"stepAlpha/dataFile.csv": "x",
                                 "stepAlpha/absent.csv": "y"},
            "dictInputHashes": {"stepAlpha/input.csv": "z"}},
        1: "not a marker",
        7: {"dictOutputHashes": {"a": "b"}},
    }
    dictVerdicts = fileStatusManager.fdictMarkerVerdictsByStep(
        dictWorkflow, dictMarkers, SnapshotRepoFiles(S_REPO, {}, {}),
    )
    assert list(dictVerdicts) == [0]
    assert dictVerdicts[0]["listUnknown"] == [
        "stepAlpha/absent.csv", "stepAlpha/dataFile.csv",
        "stepAlpha/input.csv"]


def testMarkerVerdictsNeedMarkersWithHashes():
    """Without markers, or with markers that hash nothing, nothing is judged."""
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    dictWorkflow = fdictBuildTwoStepWorkflow()
    filesPoll = SnapshotRepoFiles(S_REPO, {}, {})
    assert fileStatusManager.fdictMarkerVerdictsByStep(
        dictWorkflow, {}, filesPoll) == {}
    assert fileStatusManager.fdictMarkerVerdictsByStep(
        dictWorkflow, None, filesPoll) == {}
    assert fileStatusManager.fdictMarkerVerdictsByStep(
        dictWorkflow, {0: {"dictOutputHashes": {}}}, filesPoll) == {}


def testManifestShortCircuitIsConservativeWithoutEvidence(tmp_path):
    """No cache, no repo, or no manifest never proves a step fresh."""
    dictStep = fdictBuildTwoStepWorkflow()["listSteps"][0]
    fbFresh = fileStatusManager._fbStepHashesMatchManifest
    assert fbFresh(dictStep, {"sRepoRoot": str(tmp_path)}, None) is False
    assert fbFresh(dictStep, {"sRepoRoot": ""}, {}) is False
    assert fbFresh(dictStep, {"sRepoRoot": str(tmp_path)}, {}) is False


def testManifestShortCircuitRefusesUntrackedOutputs(tmp_path):
    """A manifest that omits a declared output cannot prove freshness."""
    (tmp_path / "MANIFEST.sha256").write_text(
        "0" * 64 + "  stepAlpha/other.csv\n",
    )
    dictStep = fdictBuildTwoStepWorkflow()["listSteps"][0]
    assert fileStatusManager._fbStepHashesMatchManifest(
        dictStep, {"sRepoRoot": str(tmp_path)}, {},
    ) is False
    assert fileStatusManager._fbStepHashesMatchManifest(
        {"sDirectory": "stepAlpha"}, {"sRepoRoot": str(tmp_path)}, {},
    ) is False


def testCacheSweepEvictsEveryCacheKindForAbsentContainers(monkeypatch):
    """Plain caches, docker handles and incident buckets all follow Docker."""
    from vaibify.gui import hostIncidents, pipelineServer
    listEvictedByDocker = []

    class DockerEvictionRecorder:
        def fnEvictAbsentContainers(self, setRunning):
            listEvictedByDocker.append(set(setRunning))
            raise RuntimeError("pool already closed")

    monkeypatch.setattr(
        pipelineServer, "DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER",
        {"idGone": {}, "idRunning": {}},
    )
    dictCtx = {
        "docker": DockerEvictionRecorder(),
        "workflows": {"idGone": {}, "idRunning": {}},
        "paths": "not a dict",
    }
    setEvicted = fileStatusManager.fsetSweepAllContainerCaches(
        dictCtx, ["idRunning"],
    )
    assert "idGone" in setEvicted
    assert list(dictCtx["workflows"]) == ["idRunning"]
    assert list(pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER) == [
        "idRunning",
    ]
    assert listEvictedByDocker and "idRunning" in listEvictedByDocker[0]
    assert fileStatusManager.fsetSweepAllContainerCaches(None, []) == set()


def testAutoArchiveDigestsNeedAProjectRepo():
    """Without a repo there is no scope to hash post-archive files in."""
    assert fileStatusManager._fdictAutoArchiveZenodoDigests(
        None, "idRunning", {"sProjectRepoPath": ""}, ["a.csv"],
    ) == {}


def testAutoArchiveDigestFailureIsLoggedNotRaised(caplog):
    """A failed digest stamp leaves the archive's success intact."""

    class DockerRefusingExec:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            raise RuntimeError("exec refused")

    dictWorkflow = {"sProjectRepoPath": S_REPO}
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        fileStatusManager._fnPersistAutoArchiveZenodoDigests(
            DockerRefusingExec(), "idRunning", dictWorkflow,
            [f"{S_REPO}/stepAlpha/figure.pdf"], "sandbox",
        )
    assert any("Zenodo digest stamp failed" in r.getMessage()
               for r in caplog.records)
