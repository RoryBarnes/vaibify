"""A PROOF receipt records ONE observation: one instant, one set of inputs.

Source: ``vaibify/reproducibility/levelGates.py`` (the evaluation-time
pin, the blocker-cache bypass, the arXiv memo) and
``vaibify/reproducibility/proofReceipt.py`` (the capture, the recording
wrapper and its replay).

The gate, the gaps and the blockers each read the clock and each may
ask arXiv. Evaluated one after another they can disagree -- a verdict
taken at 23h59m beside a blocker list taken at 24h01m -- and a receipt
built from them would contradict itself. These tests drive real host
repositories; the clock and arXiv are the only things faked.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tests.syncStatusFixtures import fdictBuildCachedVerify
from vaibify.reproducibility import arxivClient, levelGates, proofReceipt
from vaibify.reproducibility.repoFiles import HostRepoFiles, SnapshotRepoFiles


S_CONTAINER_ID = "observedProject"
_LIST_COMPARED = [".vaibify/projects/project.json", "Step/out.csv"]
S_PUSH_COMMIT = "c0ffee" + "0" * 34


def _fsIsoHoursAgo(fHoursAgo):
    return (datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _fnWriteText(sPath, sText):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileText:
        fileText.write(sText)


def _fnWriteRecords(sRepo, fHoursAgo=1.0):
    dictRecords = {
        "github": fdictBuildCachedVerify(
            sService="github", listComparedPaths=_LIST_COMPARED,
            sLastVerified=_fsIsoHoursAgo(fHoursAgo)),
        "zenodo": fdictBuildCachedVerify(
            sService="zenodo", listComparedPaths=_LIST_COMPARED,
            sLastVerified=_fsIsoHoursAgo(fHoursAgo),
            sZenodoDoi="10.5281/zenodo.1", sEndpointVerified="sandbox"),
    }
    _fnWriteText(sRepo + "/.vaibify/syncStatus.json", json.dumps(dictRecords))


def _fsBuildProject(pathTmp, fHoursAgo=1.0):
    sRepo = os.path.realpath(str(pathTmp)) + "/project"
    _fnWriteText(sRepo + "/Step/out.csv", "a,b\n1,2\n")
    _fnWriteText(sRepo + "/.vaibify/projects/project.json", "{}\n")
    _fnWriteRecords(sRepo, fHoursAgo)
    return sRepo


def _fdictWorkflow(sRepo):
    return {
        "sWorkflowName": "Example Project",
        "sProjectRepoPath": sRepo,
        "dictRemotes": {
            "github": {"sOwner": "someone", "sRepo": "something"},
            "zenodo": {"sDoi": "10.5281/zenodo.1"},
        },
        "listSteps": [{
            "sName": "Step", "sDirectory": "Step",
            "saOutputDataFiles": ["out.csv"], "saPlotFiles": [],
        }],
    }


def _fdictContext(sRepo):
    return {"files": None, "docker": None, "paths": {
        S_CONTAINER_ID: sRepo + "/.vaibify/projects/project.json"}}


@pytest.fixture
def fnHostProject(monkeypatch):
    """Treat the observed project as a host project: live reads from disk."""
    from vaibify.config import registryManager
    monkeypatch.setattr(registryManager, "fbIsHostProject", lambda _s: True)
    levelGates.fnClearLevelBlockerCache()
    yield
    levelGates.fnClearLevelBlockerCache()


def _ClockAt(dtFixed):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return dtFixed if tz else dtFixed.replace(tzinfo=None)
    return _Clock


def _fdictObservedQuestions(dictObservation):
    return {sKey: dictObservation[sKey] for sKey in (
        "dictVerdict", "dictGaps", "listLevel2Blockers", "dictHeaderCell",
        "dictRecordFresh")}


def _fdictCapture(sRepo):
    return proofReceipt.fdictCaptureObservation(
        _fdictContext(sRepo), S_CONTAINER_ID, _fdictWorkflow(sRepo))


# ── The pin ──────────────────────────────────────────────────────────


@pytest.mark.falsification
def testACaptureIsUnchangedWhenTheClockMovesDuringIt(
    tmp_path, monkeypatch, fnHostProject,
):
    """Two days pass on the gates' clock mid-capture; nothing it records moves.

    Kills: the freshness check ignores the pin.
    """
    sRepo = _fsBuildProject(tmp_path, fHoursAgo=1.0)
    dictBefore = _fdictCapture(sRepo)
    assert dictBefore["dictRecordFresh"]["github"] is True
    monkeypatch.setattr(levelGates, "datetime", _ClockAt(
        datetime.now(timezone.utc) + timedelta(days=2)))
    dictDuring = _fdictCapture(sRepo)
    assert _fdictObservedQuestions(dictDuring) == _fdictObservedQuestions(
        dictBefore)


@pytest.mark.falsification
def testAnUnpinnedFreshnessCheckStillReadsTheClock(monkeypatch):
    """Outside a capture the gates keep reading the clock, nested pins included.

    Kills: the pin made permanent (left set when its context exits).
    """
    dtNow = datetime.now(timezone.utc)
    dictRecord = {"sLastVerified": _fsIsoHoursAgo(1.0)}
    with levelGates.fcontextPinnedEvaluation(dtNow):
        with levelGates.fcontextPinnedEvaluation(dtNow + timedelta(hours=1)):
            pass
        assert levelGates._fdatetimeEvaluationInstant() == dtNow
    monkeypatch.setattr(levelGates, "datetime", _ClockAt(
        dtNow + timedelta(days=2)))
    assert levelGates._fbCachedSyncStatusFresh(dictRecord, 24.0) is False
    with levelGates.fcontextPinnedEvaluation(dtNow):
        assert levelGates._fbCachedSyncStatusFresh(dictRecord, 24.0) is True


@pytest.mark.falsification
def testAPinnedEvaluationNeverReadsTheBlockerCache(tmp_path, fnHostProject):
    """The cross-poll cache's key has no instant, so a capture must bypass it.

    Kills: the bypass removed.
    """
    sRepo = _fsBuildProject(tmp_path)
    dictWorkflow = _fdictWorkflow(sRepo)
    listCached = levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
    listCached.append({"sCriterion": "planted-by-the-test", "iStepIndex": -1})
    assert "planted-by-the-test" in [d["sCriterion"] for d in (
        levelGates.flistLevel2Blockers(dictWorkflow, sRepo))]
    with levelGates.fcontextPinnedEvaluation(datetime.now(timezone.utc)):
        listPinned = levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
    assert "planted-by-the-test" not in [d["sCriterion"] for d in listPinned]


# ── arXiv: asked once per question per capture ──────────────────────


def _fsBuildManuscriptProject(pathTmp):
    sRepo = _fsBuildProject(pathTmp)
    _fnWriteText(sRepo + "/Step/fig.pdf", "%PDF-1.4 figure\n")
    _fnWriteText(sRepo + "/.vaibify/overleafPushManifest.json", json.dumps(
        {S_PUSH_COMMIT: {"Step/fig.pdf": "figures/fig.pdf"}}))
    return sRepo


def _fdictManuscriptWorkflow(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo)
    dictWorkflow["listSteps"][0]["saPlotFiles"] = ["Step/fig.pdf"]
    dictWorkflow["dictRemotes"]["overleaf"] = {
        "sProjectId": "0123456789abcdef01234567",
        "sLastPushCommit": S_PUSH_COMMIT,
    }
    dictWorkflow["dictRemotes"]["arxiv"] = {
        "sArxivId": "2401.00001", "sArxivVersion": "v1"}
    return dictWorkflow


class _CountingArxiv:
    """Counts each arXiv question; answers from scripted sequences."""

    def __init__(self, sRepo, listVersionAnswers):
        self.dictCalls = {"hashes": 0, "version": 0}
        self._sRepo = sRepo
        self._listVersionAnswers = list(listVersionAnswers)

    def fdictFetchRemoteHashes(self, sArxivId, listPushed, **_dictKeywords):
        """Serve the e-print as holding exactly the local figures."""
        import hashlib
        self.dictCalls["hashes"] += 1
        return {sPath: hashlib.sha256(open(
            os.path.join(self._sRepo, sPath), "rb").read()).hexdigest()
            for sPath in listPushed}

    def fsResolveLatestVersion(self, sArxivId):
        self.dictCalls["version"] += 1
        genericAnswer = self._listVersionAnswers.pop(0) if (
            len(self._listVersionAnswers) > 1) else self._listVersionAnswers[0]
        if isinstance(genericAnswer, Exception):
            raise genericAnswer
        return genericAnswer


def _fCountingArxiv(monkeypatch, sRepo, listVersionAnswers):
    countingArxiv = _CountingArxiv(sRepo, listVersionAnswers)
    monkeypatch.setattr(arxivClient, "fdictFetchRemoteHashes",
                        countingArxiv.fdictFetchRemoteHashes)
    monkeypatch.setattr(arxivClient, "fsResolveLatestVersion",
                        countingArxiv.fsResolveLatestVersion)
    return countingArxiv


@pytest.mark.falsification
def testArxivIsAskedOncePerQuestionInACapture(
    tmp_path, monkeypatch, fnHostProject,
):
    """The gaps, the gate and the blockers would each ask; one answer serves all.

    Kills: the arXiv memo removed.
    """
    sRepo = _fsBuildManuscriptProject(tmp_path)
    countingArxiv = _fCountingArxiv(monkeypatch, sRepo, ["v1"])
    proofReceipt.fdictCaptureObservation(
        _fdictContext(sRepo), S_CONTAINER_ID, _fdictManuscriptWorkflow(sRepo))
    assert countingArxiv.dictCalls == {"hashes": 1, "version": 1}


@pytest.mark.falsification
def testAnArxivFailureStaysAFailureWhenAskedTwiceInOneCapture(
    tmp_path, monkeypatch, fnHostProject,
):
    """A failed ask is replayed: the second asker cannot see a success.

    Kills: errors left out of the arXiv memo.
    """
    sRepo = _fsBuildManuscriptProject(tmp_path)
    countingArxiv = _fCountingArxiv(
        monkeypatch, sRepo,
        [arxivClient.ArxivError("arXiv unreachable"), "v1"])
    dictObservation = proofReceipt.fdictCaptureObservation(
        _fdictContext(sRepo), S_CONTAINER_ID, _fdictManuscriptWorkflow(sRepo))
    assert dictObservation["dictGaps"]["bArxivFullySynced"] is False
    assert "arxiv-version-stale" in [
        d["sCriterion"] for d in dictObservation["listLevel2Blockers"]]
    assert countingArxiv.dictCalls["version"] == 1
    listVersion = [d for d in dictObservation["dictManuscript"][
        "listArxivAnswers"] if d["sQuestion"] == "latest-version"]
    assert listVersion[0]["sError"] == "arXiv unreachable"


def testOutsideACaptureArxivIsAskedEveryTime(tmp_path, monkeypatch):
    """The memo is the capture's alone; an ordinary poll asks afresh."""
    sRepo = _fsBuildManuscriptProject(tmp_path)
    countingArxiv = _fCountingArxiv(monkeypatch, sRepo, ["v1"])
    dictWorkflow = _fdictManuscriptWorkflow(sRepo)
    levelGates.fnClearLevelBlockerCache()
    levelGates.fdictLevel2Gaps(dictWorkflow, sRepo)
    levelGates.flistLevel2Blockers(dictWorkflow, sRepo)
    assert countingArxiv.dictCalls == {"hashes": 2, "version": 2}


# ── Inputs: one snapshot, or live reads proven unchanged ────────────


def _ffilesSnapshotOf(sRepo, listSampled):
    dictFiles = {}
    for sRelPath in listSampled:
        sPath = os.path.join(sRepo, sRelPath)
        bIsFile = os.path.isfile(sPath)
        dictFiles[sRelPath] = {"bIsFile": bIsFile, "sText": open(
            sPath, encoding="utf-8").read() if bIsFile else None}
    return SnapshotRepoFiles(sRepo, dictFiles, {})


def _fnServeSnapshot(monkeypatch, sRepo, listSampled):
    from vaibify.config import registryManager
    from vaibify.gui import routeContext
    monkeypatch.setattr(registryManager, "fbIsHostProject", lambda _s: False)
    monkeypatch.setattr(
        routeContext, "ffilesSnapshotForWorkflow",
        lambda _c, _i, _w: _ffilesSnapshotOf(sRepo, listSampled))


def _fdictSnapshotContext(sRepo):
    dictCtx = _fdictContext(sRepo)
    dictCtx["files"] = lambda _sId: HostRepoFiles(sRepo)
    return dictCtx


_LIST_FULL_SAMPLE = [
    ".vaibify/syncStatus.json", ".vaibify/environment.json",
    ".vaibify/overleafPushManifest.json", ".vaibify/AGENTS.md",
    ".vaibify/promptRecord/attribution/events.jsonl",
    ".vaibify/promptRecord/attribution/flags.jsonl",
    ".vaibify/promptRecord/index.json",
]


def testAFullySampledSnapshotIsOneSnapshotAndCoherent(
    tmp_path, monkeypatch,
):
    sRepo = _fsBuildProject(tmp_path)
    _fnServeSnapshot(monkeypatch, sRepo, _LIST_FULL_SAMPLE)
    dictObservation = proofReceipt.fdictCaptureObservation(
        _fdictSnapshotContext(sRepo), S_CONTAINER_ID, _fdictWorkflow(sRepo))
    assert dictObservation["sInputs"] == proofReceipt.S_INPUTS_ONE_SNAPSHOT
    assert dictObservation["sInputsReason"] == ""
    assert dictObservation["bCoherent"] is True


def testASnapshotMissThatPropagatesRestartsTheCaptureLive(
    tmp_path, monkeypatch,
):
    sRepo = _fsBuildProject(tmp_path)
    _fnServeSnapshot(monkeypatch, sRepo, [])
    dictObservation = proofReceipt.fdictCaptureObservation(
        _fdictSnapshotContext(sRepo), S_CONTAINER_ID, _fdictWorkflow(sRepo))
    assert dictObservation["sInputs"] == proofReceipt.S_INPUTS_LIVE_READS
    assert "did not sample" in dictObservation["sInputsReason"]
    assert dictObservation["dictRecords"]["github"]["sLastVerified"]


@pytest.mark.falsification
def testASnapshotMissAGateSwallowedStillRestartsTheCaptureLive(
    tmp_path, monkeypatch,
):
    """A swallowed miss answered some question with no data; redo it live.

    Kills: a snapshot capture that missed a read is kept (a live read
    mixed into a snapshot capture), not redone over live reads.
    """
    sRepo = _fsBuildProject(tmp_path)
    _fnServeSnapshot(monkeypatch, sRepo, _LIST_FULL_SAMPLE)
    fdictRealGaps = levelGates.fdictLevel2Gaps

    def fdictGapsThatSwallowAMiss(dictWorkflow, filesRepo):
        try:
            filesRepo.fsReadText("Step/never-sampled.csv")
        except (KeyError, OSError):
            pass
        return fdictRealGaps(dictWorkflow, filesRepo)

    monkeypatch.setattr(levelGates, "fdictLevel2Gaps", fdictGapsThatSwallowAMiss)
    dictObservation = proofReceipt.fdictCaptureObservation(
        _fdictSnapshotContext(sRepo), S_CONTAINER_ID, _fdictWorkflow(sRepo))
    assert dictObservation["sInputs"] == proofReceipt.S_INPUTS_LIVE_READS


def testAHostProjectIsReadLiveAndSaysWhy(tmp_path, fnHostProject):
    sRepo = _fsBuildProject(tmp_path)
    dictObservation = _fdictCapture(sRepo)
    assert dictObservation["sInputs"] == proofReceipt.S_INPUTS_LIVE_READS
    assert "host project" in dictObservation["sInputsReason"]
    assert dictObservation["bCoherent"] is True
    assert dictObservation["listChangedReads"] == []


@pytest.mark.falsification
def testAFileChangedBetweenTwoLiveReadsMakesTheObservationIncoherent(
    tmp_path, monkeypatch, fnHostProject,
):
    """A record rewritten after the gates read it is named, and the
    observation is labeled incoherent.

    Kills: the replay at the end removed.
    """
    sRepo = _fsBuildProject(tmp_path)
    fdictRealEvaluate = proofReceipt._fdictEvaluateUnderPin

    def fdictEvaluateThenRewrite(dictWorkflow, filesRepo, datetimeEvaluatedAt):
        dictEvaluation = fdictRealEvaluate(dictWorkflow, filesRepo, datetimeEvaluatedAt)
        _fnWriteRecords(sRepo, fHoursAgo=3.0)
        return dictEvaluation

    monkeypatch.setattr(
        proofReceipt, "_fdictEvaluateUnderPin", fdictEvaluateThenRewrite)
    dictObservation = _fdictCapture(sRepo)
    assert dictObservation["bCoherent"] is False
    assert ".vaibify/syncStatus.json" in dictObservation["listChangedReads"]


def testTwoReadsOfOneFileThatDisagreeAreAChange(tmp_path):
    sRepo = _fsBuildProject(tmp_path)
    filesRecorder = proofReceipt.RecordingRepoFiles(HostRepoFiles(sRepo))
    filesRecorder.fdictHashFiles(["Step/out.csv", ".vaibify/projects/project.json"])
    _fnWriteText(sRepo + "/Step/out.csv", "a,b\n9,9\n")
    filesRecorder.fdictHashFiles(["Step/out.csv", ".vaibify/projects/project.json"])
    assert filesRecorder.setChangedReads == {"Step/out.csv"}
    assert filesRecorder.sRootPath == sRepo


def testTheCaptureRecordsTheWorkflowIdentity(tmp_path, fnHostProject):
    sRepo = _fsBuildProject(tmp_path)
    _fnWriteText(sRepo + "/.git/HEAD", "ref: refs/heads/main\n")
    _fnWriteText(sRepo + "/.git/refs/heads/main", "ab" * 20 + "\n")
    dictIdentity = _fdictCapture(sRepo)["dictWorkflowIdentity"]
    assert dictIdentity["sName"] == "Example Project"
    assert dictIdentity["sDefinitionPath"] == ".vaibify/projects/project.json"
    import hashlib
    assert dictIdentity["sDefinitionSha256"] == hashlib.sha256(
        b"{}\n").hexdigest()
    assert dictIdentity["sRepositoryHead"] == "ab" * 20
