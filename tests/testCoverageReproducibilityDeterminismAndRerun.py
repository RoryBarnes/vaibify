"""The determinism scan and the post-rerun hash verdict, on real files.

Both modules read a project repository, so the fixtures are real
repositories under ``tmp_path``: real scripts, a real
``requirements.lock``, a real ``MANIFEST.sha256`` whose hashes are
computed here from the bytes on disk. The one boundary replaced is the
container run itself (``pipelineRunner.fiRunAllSteps``, or the
``fbRunWorkflowInContainer`` seam above it), and its stand-in writes
real bytes into the tree the comparison re-hashes.

Every verdict that PASSES has a sibling in which one byte or one input
is changed and the same verdict FAILS, so no test here can pass against
a comparison that always agrees.
"""

import asyncio
import hashlib

import pytest

from vaibify.reproducibility import determinismGate, rerunVerification
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_STEP_DIRECTORY = "stepAlpha"
S_SCRIPT_NAME = "generate.py"
S_OUTPUT_NAME = "dataFile.csv"
S_OUTPUT_PATH = S_STEP_DIRECTORY + "/" + S_OUTPUT_NAME
BA_OUTPUT = b"x,y\n1,2\n"


def fdictWorkflowWithOneStep():
    """Return a workflow whose one automated step runs one script."""
    return {
        "sWorkflowName": "workflowAlpha",
        "listSteps": [{
            "sName": "Step Alpha",
            "sDirectory": S_STEP_DIRECTORY,
            "saDataCommands": ["python " + S_SCRIPT_NAME],
            "saOutputDataFiles": [S_OUTPUT_NAME],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }],
    }


def fnWriteFile(pathRepo, sRelativePath, baContent):
    """Write ``baContent`` at a repo-relative path, creating parents."""
    pathFile = pathRepo / sRelativePath
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_bytes(baContent)


def fnWriteManifestPinning(pathRepo, dictPathToBytes):
    """Write MANIFEST.sha256 pinning each path to its bytes' sha256."""
    sManifest = "".join(
        hashlib.sha256(baContent).hexdigest() + "  " + sPath + "\n"
        for sPath, baContent in dictPathToBytes.items()
    )
    (pathRepo / "MANIFEST.sha256").write_text(sManifest, encoding="utf-8")


# ── determinism: the workflow script scan ────────────────────────


def testScanFindsAClockSeedInAStepScript(tmp_path):
    """A clock-derived seed in a step's script is reported by line."""
    fnWriteFile(
        tmp_path, S_STEP_DIRECTORY + "/" + S_SCRIPT_NAME,
        b"import random, time\nrandom.seed(time.time())\n",
    )
    dictScan = determinismGate.fdictScanWorkflowScripts(
        fdictWorkflowWithOneStep(), str(tmp_path),
    )
    sScript = S_STEP_DIRECTORY + "/" + S_SCRIPT_NAME
    assert dictScan["listScanned"] == [sScript]
    assert dictScan["listUnreadable"] == []
    assert len(dictScan["listIssues"]) == 1
    assert dictScan["listIssues"][0].startswith(sScript + ":2:")


def testScanOfASeededScriptReportsNothing(tmp_path):
    """The same script with a fixed seed is scanned and clean."""
    fnWriteFile(
        tmp_path, S_STEP_DIRECTORY + "/" + S_SCRIPT_NAME,
        b"import random\nrandom.seed(42)\n",
    )
    dictScan = determinismGate.fdictScanWorkflowScripts(
        fdictWorkflowWithOneStep(), str(tmp_path),
    )
    assert dictScan["listIssues"] == []
    assert dictScan["listScanned"] == [S_STEP_DIRECTORY + "/" + S_SCRIPT_NAME]


def testAScriptThatCannotBeReadIsNotCountedClean(tmp_path):
    """'Could not look' is reported apart from 'looked and found nothing'."""
    dictWorkflow = fdictWorkflowWithOneStep()
    dictWorkflow["listSteps"].insert(0, "not-a-step")
    dictScan = determinismGate.fdictScanWorkflowScripts(
        dictWorkflow, str(tmp_path),
    )
    assert dictScan["listScanned"] == []
    assert dictScan["listUnreadable"] == [
        S_STEP_DIRECTORY + "/" + S_SCRIPT_NAME,
    ]
    assert dictScan["listIssues"] == []


# ── determinism: the maths-library evidence ──────────────────────


def testALockPinningMklSaysTheQuestionApplies(tmp_path):
    """MKL distributions in the lock are named, sorted, and deduplicated."""
    (tmp_path / "requirements.lock").write_text(
        "numpy==1.26.4\nmkl==2024.0.0\nintel-openmp==2024.0.2 ; "
        "sys_platform == 'linux'\nmkl==2024.0.0\n",
        encoding="utf-8",
    )
    dictEvidence = determinismGate.fdictDetectMathsLibrary(str(tmp_path))
    assert dictEvidence["bMklFound"] is True
    assert dictEvidence["listMklPackages"] == ["intel-openmp", "mkl"]
    assert "pins intel-openmp, mkl" in dictEvidence["sNote"]
    assert "pins Intel MKL" in dictEvidence["sHeadline"]


def testALockWithoutMklSaysTheQuestionLikelyDoesNotApply(tmp_path):
    """A lock that names no MKL package is evidence, stated as such."""
    (tmp_path / "requirements.lock").write_text(
        "numpy==1.26.4\nscipy==1.13.0\nmklike==1.0\n", encoding="utf-8",
    )
    dictEvidence = determinismGate.fdictDetectMathsLibrary(str(tmp_path))
    assert dictEvidence["bMklFound"] is False
    assert dictEvidence["listMklPackages"] == []
    assert "no Intel MKL package" in dictEvidence["sHeadline"]
    assert "DECLARES" in dictEvidence["sNote"]


class _UnreadableLockRepo:
    """A repository whose lock exists but cannot be decoded."""

    def fbIsFile(self, sRelativePath):
        return sRelativePath == "requirements.lock"

    def fsReadText(self, sRelativePath):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start")


def testAnUnreadableLockCannotTell():
    """A lock that cannot be read answers None, never 'no MKL'."""
    dictEvidence = determinismGate.fdictDetectMathsLibrary(
        _UnreadableLockRepo(),
    )
    assert dictEvidence["bMklFound"] is None
    assert "could not be read" in dictEvidence["sNote"]
    assert dictEvidence["sHeadline"] == "Maths library: could not tell."


# ── rerun: the container run seam ────────────────────────────────


def fnInstallPipelineRunner(monkeypatch, iExitCode, listCalls):
    """Replace the container run with one that reports ``iExitCode``."""
    from vaibify.gui import pipelineRunner

    async def fiFakeRunAllSteps(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        sWorkdir, fnStatusCallback, iSourceDateEpochOverride=None,
    ):
        await fnStatusCallback({"sType": "stepStarted"})
        listCalls.append({
            "sContainerId": sContainerId, "sWorkdir": sWorkdir,
            "iSourceDateEpochOverride": iSourceDateEpochOverride,
        })
        return iExitCode

    monkeypatch.setattr(pipelineRunner, "fiRunAllSteps", fiFakeRunAllSteps)


def testAZeroExitRunIsARunAndCarriesTheRecordedEpoch(monkeypatch):
    """Exit 0 is True, and the envelope's epoch reaches the runner."""
    listCalls = []
    fnInstallPipelineRunner(monkeypatch, 0, listCalls)
    assert rerunVerification.fbRunWorkflowInContainer(
        None, "containerIdAlpha", {}, "/workspace/repo/.vaibify/w.json",
        "/workspace/repo/.vaibify", None, iSourceDateEpochOverride=1700000000,
    ) is True
    assert listCalls == [{
        "sContainerId": "containerIdAlpha",
        "sWorkdir": "/workspace/repo/.vaibify",
        "iSourceDateEpochOverride": 1700000000,
    }]


def testANonZeroExitRunIsNotARun(monkeypatch):
    """Any non-zero pipeline exit answers False."""
    fnInstallPipelineRunner(monkeypatch, 3, [])
    assert rerunVerification.fbRunWorkflowInContainer(
        None, "containerIdAlpha", {}, "/w/.vaibify/w.json", "/w/.vaibify",
    ) is False


def testTheDefaultStatusSinkAcceptsAnyEvent():
    """The no-callback sink is awaitable and swallows events."""
    assert asyncio.run(
        rerunVerification._fnDiscardStatusEvent({"sType": "anything"}),
    ) is None


# ── rerun: end to end through the verify seam ────────────────────


def fnInstallRerunWriting(monkeypatch, pathRepo, baWritten, bSucceeded=True):
    """Stand in for the container run: write ``baWritten`` as the output."""
    def fbFakeRun(connectionDocker, sContainerId, dictWorkflow,
                  sWorkflowPath, sWorkdir, fnStatusCallback,
                  iSourceDateEpochOverride=None):
        fnWriteFile(pathRepo, S_OUTPUT_PATH, baWritten)
        return bSucceeded

    monkeypatch.setattr(
        rerunVerification, "fbRunWorkflowInContainer", fbFakeRun,
    )


def fdictRerunOnce(pathRepo):
    """Drive the tier-5 entry point once; return outcome and events."""
    listEvents = []
    dictOutcome = rerunVerification.fdictRerunAndVerifyWorkflow(
        None, "containerIdAlpha", fdictWorkflowWithOneStep(),
        str(pathRepo / ".vaibify" / "workflows" / "workflowAlpha.json"),
        str(pathRepo), fnStatusCallback=listEvents.append,
    )
    return dictOutcome, listEvents


def testARerunThatReproducesEveryByteIsAttestedAsPassed(
    monkeypatch, tmp_path,
):
    """Identical rerun bytes match the frozen manifest and pass."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifestPinning(tmp_path, {S_OUTPUT_PATH: BA_OUTPUT})
    fnInstallRerunWriting(monkeypatch, tmp_path, BA_OUTPUT)
    dictOutcome, listEvents = fdictRerunOnce(tmp_path)
    assert dictOutcome["bPassed"] is True
    assert dictOutcome["listMatchedPaths"] == [S_OUTPUT_PATH]
    assert dictOutcome["listDivergedHashes"] == []
    assert {"sType": "comparingOutputs"} in listEvents
    assert dictOutcome["sManifestDigest"]


def testARerunThatChangesOneByteFails(monkeypatch, tmp_path):
    """One different output byte is a divergence naming that file."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifestPinning(tmp_path, {S_OUTPUT_PATH: BA_OUTPUT})
    fnInstallRerunWriting(monkeypatch, tmp_path, BA_OUTPUT[:-1] + b"3")
    dictOutcome, _listEvents = fdictRerunOnce(tmp_path)
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["iOutputHashesMatched"] == 0
    assert dictOutcome["listDivergedHashes"] == [S_OUTPUT_PATH]


# ── rerun: the verdict helpers ───────────────────────────────────


def testVerifyWithoutASnapshotFreezesTheManifestAtCallTime(tmp_path):
    """A quiescent re-check snapshots itself and matches real bytes."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    fnWriteManifestPinning(tmp_path, {S_OUTPUT_PATH: BA_OUTPUT})
    dictOutcome = rerunVerification.fdictVerifyRerunOutputs(
        str(tmp_path), True,
    )
    assert dictOutcome["bPassed"] is True
    assert dictOutcome["iOutputHashesTotal"] == 1


def testVerifyWithoutASnapshotStillFailsAMissingOutput(tmp_path):
    """The same re-check with the output deleted is a miss, not a pass."""
    fnWriteManifestPinning(tmp_path, {S_OUTPUT_PATH: BA_OUTPUT})
    dictOutcome = rerunVerification.fdictVerifyRerunOutputs(
        str(tmp_path), True,
    )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["listFileOutcomes"][0]["sStatus"] == (
        rerunVerification.S_FILE_MISSING
    )


def testVerifyWithNoManifestAtAllFailsClosed(tmp_path):
    """No manifest is an unreadable expectation, never zero-of-zero passing."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    dictSnapshot = rerunVerification.fdictSnapshotExpectedManifest(
        str(tmp_path),
    )
    assert dictSnapshot == {
        "bReadable": False, "listEntries": [], "sDigest": "",
    }
    dictOutcome = rerunVerification.fdictVerifyRerunOutputs(
        str(tmp_path), True,
    )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["listDivergedHashes"] == [
        rerunVerification.S_DIVERGENCE_MANIFEST_UNREADABLE,
    ]


class _RepoThatCannotHash(HostRepoFiles):
    """A host repository whose hashing fails with an I/O error."""

    def fdictHashFiles(self, listRelPaths):
        raise OSError("input/output error")


def testAnUnhashableTreeIsNoComparisonNotAMatch(tmp_path):
    """Hashing that fails outright is 'manifest unreadable', never a pass."""
    dictExpected = {
        "bReadable": True, "sDigest": "",
        "listEntries": [{"sPath": S_OUTPUT_PATH, "sExpected": "0" * 64}],
    }
    dictOutcome = rerunVerification.fdictVerifyRerunOutputs(
        _RepoThatCannotHash(str(tmp_path)), True, dictExpected,
    )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["iOutputHashesTotal"] == 0
    assert dictOutcome["listDivergedHashes"] == [
        rerunVerification.S_DIVERGENCE_MANIFEST_UNREADABLE,
    ]


def testAFailedStepIsNamedAfterTheGenericLine(tmp_path):
    """A failed run's second line names the step, its label and code."""
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    dictExpected = {
        "bReadable": True, "sDigest": "",
        "listEntries": [{
            "sPath": S_OUTPUT_PATH,
            "sExpected": hashlib.sha256(BA_OUTPUT).hexdigest(),
        }],
    }
    dictOutcome = rerunVerification.fdictVerifyRerunOutputs(
        str(tmp_path), False, dictExpected, (),
        {"sKind": "step", "sStepLabel": "A01", "sStepName": "Step Alpha",
         "iExitCode": 2},
    )
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["listDivergedHashes"] == [
        rerunVerification.S_DIVERGENCE_PIPELINE_FAILED,
        "step A01 'Step Alpha' stopped with error code 2",
    ]


def testAPreflightRefusalListsEachDistinctProblemOnce(tmp_path):
    """Preflight says 'never started', then each error once, in order."""
    from vaibify.reproducibility.rerunDiagnostics import (
        S_FAILURE_KIND_PREFLIGHT,
    )
    fnWriteFile(tmp_path, S_OUTPUT_PATH, BA_OUTPUT)
    dictExpected = {
        "bReadable": True, "sDigest": "",
        "listEntries": [{
            "sPath": S_OUTPUT_PATH,
            "sExpected": hashlib.sha256(BA_OUTPUT).hexdigest(),
        }],
    }
    dictOutcome = rerunVerification.fdictVerifyRerunOutputs(
        str(tmp_path), False, dictExpected, (),
        {"sKind": S_FAILURE_KIND_PREFLIGHT,
         "listErrors": ["directory missing", "script missing",
                        "directory missing"]},
    )
    assert dictOutcome["listDivergedHashes"] == [
        rerunVerification.S_DIVERGENCE_PIPELINE_FAILED,
        "the run was stopped before any step could start",
        "directory missing",
        "script missing",
    ]


@pytest.mark.parametrize("sManifestText, iExpected", [
    (None, 0),
    ("this line has no separator\n", 0),
    ("0" * 64 + "  a.txt\n# comment\n\n" + "1" * 64 + "  b.txt\n", 2),
])
def testManifestEntryCountIsZeroWhenUnusable(
    tmp_path, sManifestText, iExpected,
):
    """Absent or malformed manifests count zero; a valid one counts entries."""
    if sManifestText is not None:
        (tmp_path / "MANIFEST.sha256").write_text(sManifestText, "utf-8")
    assert rerunVerification.fiCountManifestEntriesOrZero(str(tmp_path)) == (
        iExpected
    )
