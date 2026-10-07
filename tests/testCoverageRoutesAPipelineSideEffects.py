"""The poll's side effects that must never lie and never take the poll down.

The file-status poll does more than report: under Supervised mode it
flags repository changes nobody recorded, it keeps the AI-provenance
stamp machine-written, and it summarizes the Prompt Record for the
envelope row. Each of these is a side effect of a READ path, so each is
pinned here for both halves of its contract: it states what the files
show (a flag is written, a stamp is rewritten, a broken chain reads
broken), and a failure inside it is logged rather than raised into the
poll.

The helpers are driven against a real repository on disk under
``tmp_path``. Only the container facts the stamp captures (the network
probe, the workspace prompt, the agent CLI versions) come from a stand
in, because they are answers a Docker daemon would give.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.testCarrierMigratedRoutes import (
    DockerDoubleServingALevelThreeWorkflow,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import S_CONTAINER_ID
from vaibify.docker import containerManager
from vaibify.gui import attributionLog, promptRecordManager
from vaibify.gui.routes import pipelineRoutes
from vaibify.reproducibility import aiProvenanceStamp, replayGate
from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)
from vaibify.reproducibility.l3Attestation import fsCurrentManifestDigest
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


S_CONTAINER = "containerPollJuliet"
DICT_DECLARED_MODEL = {
    "sVendor": "vendorAlpha", "sModelId": "modelBravo-1",
    "sUseStartDate": "2026-01-01",
}


def _fdictSupervisedWorkflow(sRepo):
    return {
        "sProjectRepoPath": sRepo,
        "listSteps": [],
        "dictAiProvenance": {"dictSupervision": {"bEnabled": True}},
    }


def _fdictContext(listSaved):
    """A route context whose only reachable effect is the workflow save."""
    return {
        "save": lambda sContainerId, dictWorkflow: listSaved.append(
            sContainerId,
        ),
    }


def _fnRunWatchdog(dictCtx, dictWorkflow, dictModTimes, sRepo,
                   bPipelineRunning=False):
    asyncio.run(pipelineRoutes._fnRunSupervisionWatchdog(
        dictCtx, S_CONTAINER, dictWorkflow, dictModTimes, sRepo,
        bPipelineRunning,
    ))


# ── The Supervised-mode watchdog ──


def testAnUnsupervisedWorkflowIsNeverJudged(tmp_path):
    listSaved = []
    dictWorkflow = {"sProjectRepoPath": str(tmp_path), "listSteps": []}
    _fnRunWatchdog(
        _fdictContext(listSaved), dictWorkflow,
        {str(tmp_path / "output.csv"): time.time()}, str(tmp_path),
    )
    assert listSaved == []
    assert "dictAiProvenance" not in dictWorkflow


def testALiveRunIsItsOwnRecordedCauseAndIsSkipped(tmp_path):
    listSaved = []
    dictWorkflow = _fdictSupervisedWorkflow(str(tmp_path))
    _fnRunWatchdog(
        _fdictContext(listSaved), dictWorkflow,
        {str(tmp_path / "output.csv"): time.time()}, str(tmp_path),
        bPipelineRunning=True,
    )
    assert listSaved == []
    assert attributionLog.flistLoadFlags(
        ffilesEnsureRepoFiles(str(tmp_path)),
    ) == []


def testAQuietTickOnlyRatchetsTheWatchedManifestDigest(tmp_path):
    """A legitimate manifest change under watch advances the digest once."""
    listSaved = []
    sRepo = str(tmp_path)
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileOut:
        fileOut.write("a" * 64 + "  stepA/output.csv\n")
    dictWorkflow = _fdictSupervisedWorkflow(sRepo)
    _fnRunWatchdog(_fdictContext(listSaved), dictWorkflow, {}, sRepo)
    dictSupervision = dictWorkflow["dictAiProvenance"]["dictSupervision"]
    assert dictSupervision["sLastManifestDigest"] == (
        fsCurrentManifestDigest(sRepo)
    )
    assert listSaved == [S_CONTAINER]
    _fnRunWatchdog(_fdictContext(listSaved), dictWorkflow, {}, sRepo)
    assert listSaved == [S_CONTAINER], "an unchanged digest saved again"


def testAnUnrecordedChangeIsFlaggedPermanentlyByRepoPath(tmp_path):
    listSaved = []
    sRepo = str(tmp_path)
    dictWorkflow = _fdictSupervisedWorkflow(sRepo)
    _fnRunWatchdog(
        _fdictContext(listSaved), dictWorkflow,
        {os.path.join(sRepo, "stepA", "output.csv"): time.time()}, sRepo,
    )
    listFlags = attributionLog.flistLoadFlags(ffilesEnsureRepoFiles(sRepo))
    assert [
        (dictFlag["sFlagKind"], dictFlag["sDetail"]) for dictFlag in listFlags
    ] == [("unattributed-modification", "stepA/output.csv")]
    assert dictWorkflow["dictAiProvenance"]["dictSupervision"][
        "iUnattributedFlagCount"
    ] == 1
    assert listSaved == [S_CONTAINER]
    dictEvidence = attributionLog.fdictSummarizeSupervisionEvidence(
        ffilesEnsureRepoFiles(sRepo), dictWorkflow,
    )
    assert replayGate.fbSupervisionClean(dictWorkflow, dictEvidence) is False


def testATamperedEventChainIsFlaggedAsTampering(tmp_path):
    sRepo = str(tmp_path)
    dictWorkflow = _fdictSupervisedWorkflow(sRepo)
    filesRepo = ffilesEnsureRepoFiles(sRepo)
    _fnTamperWithFirstEvent(sRepo, dictWorkflow)
    _fnRunWatchdog(_fdictContext([]), dictWorkflow, {}, sRepo)
    listKinds = [
        dictFlag["sFlagKind"]
        for dictFlag in attributionLog.flistLoadFlags(filesRepo)
    ]
    assert listKinds == ["attribution-log-tampered"]


def _fnTamperWithFirstEvent(sRepo, dictWorkflow):
    """Break the event chain and age every event past the window."""
    filesRepo = ffilesEnsureRepoFiles(sRepo)
    for sDetail in ("first", "second"):
        attributionLog.fnAppendAttributionEvent(
            filesRepo, dictWorkflow, "write-file", "hub", sDetail,
        )
    sEventsPath = os.path.join(sRepo, attributionLog.S_ATTRIBUTION_EVENTS_PATH)
    with open(sEventsPath) as fileIn:
        listRecords = [json.loads(sLine) for sLine in fileIn.read().splitlines()]
    sLongAgo = datetime.fromtimestamp(
        time.time() - 3600, timezone.utc,
    ).isoformat()
    for dictRecord in listRecords:
        dictRecord["sTimestampUtc"] = sLongAgo
    listRecords[0]["sDetail"] = "rewritten"
    with open(sEventsPath, "w") as fileOut:
        fileOut.write("\n".join(
            json.dumps(dictRecord, sort_keys=True) for dictRecord in listRecords
        ) + "\n")


def _fnFailAppendAfter(monkeypatch, iWritesAllowed):
    """Make ``fdictAppendFlag`` raise once ``iWritesAllowed`` writes landed."""
    fnRealAppend = attributionLog.fdictAppendFlag
    listCalls = []

    def fnAppendThatFails(filesRepo, sFlagKind, sDetail):
        listCalls.append(sFlagKind)
        if len(listCalls) > iWritesAllowed:
            raise OSError("the container filesystem refused the write")
        return fnRealAppend(filesRepo, sFlagKind, sDetail)

    monkeypatch.setattr(attributionLog, "fdictAppendFlag", fnAppendThatFails)
    return listCalls


def testAFailedFlagWriteLeavesTheChangeToBeJudgedAgain(tmp_path, monkeypatch):
    sRepo = str(tmp_path)
    dictWorkflow = _fdictSupervisedWorkflow(sRepo)
    dictModTimes = {os.path.join(sRepo, "stepA", "output.csv"): time.time()}
    _fnFailAppendAfter(monkeypatch, 0)
    _fnRunWatchdog(_fdictContext([]), dictWorkflow, dictModTimes, sRepo)
    dictSupervision = dictWorkflow["dictAiProvenance"]["dictSupervision"]
    assert "fLastJudgedMtime" not in dictSupervision
    monkeypatch.undo()
    _fnRunWatchdog(_fdictContext([]), dictWorkflow, dictModTimes, sRepo)
    listFlags = attributionLog.flistLoadFlags(ffilesEnsureRepoFiles(sRepo))
    assert [dictFlag["sFlagKind"] for dictFlag in listFlags] == [
        "unattributed-modification",
    ]


def testARetryAfterAPartialWriteDuplicatesNoFlagAndKeepsTheChain(
    tmp_path, monkeypatch,
):
    sRepo = str(tmp_path)
    dictWorkflow = _fdictSupervisedWorkflow(sRepo)
    _fnTamperWithFirstEvent(sRepo, dictWorkflow)
    dictModTimes = {os.path.join(sRepo, "stepA", "output.csv"): time.time()}
    _fnFailAppendAfter(monkeypatch, 1)
    _fnRunWatchdog(_fdictContext([]), dictWorkflow, dictModTimes, sRepo)
    dictSupervision = dictWorkflow["dictAiProvenance"]["dictSupervision"]
    assert "fLastJudgedMtime" not in dictSupervision
    assert not dictSupervision.get("bEventChainBroken")
    monkeypatch.undo()
    _fnRunWatchdog(_fdictContext([]), dictWorkflow, dictModTimes, sRepo)
    filesRepo = ffilesEnsureRepoFiles(sRepo)
    listFlags = attributionLog.flistLoadFlags(filesRepo)
    assert sorted(dictFlag["sFlagKind"] for dictFlag in listFlags) == [
        "attribution-log-tampered", "unattributed-modification",
    ]
    assert attributionLog.fbVerifyFlagChain(listFlags)
    assert dictSupervision["fLastJudgedMtime"] > 0
    assert "listFlagsWrittenForInterval" not in dictSupervision


def testAFailingSaveIsLoggedAndThePollSurvives(tmp_path, caplog):
    def fnSaveThatFails(sContainerId, dictWorkflow):
        raise OSError("workflow file is read-only")

    sRepo = str(tmp_path)
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileOut:
        fileOut.write("a" * 64 + "  stepA/output.csv\n")
    dictWorkflow = _fdictSupervisedWorkflow(sRepo)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        _fnRunWatchdog({"save": fnSaveThatFails}, dictWorkflow, {}, sRepo)
    assert "Supervision watchdog failed" in caplog.text
    assert "workflow file is read-only" in caplog.text


def testAnUnreadableMtimeIsSkippedRatherThanJudged():
    dictSupervision = {}
    listRecent = pipelineRoutes._flistRecentWatchedChanges(
        {"/repo/stepA/good.csv": time.time(), "/repo/stepA/bad.csv": "n/a"},
        dictSupervision,
    )
    assert [sPath for _fMtime, sPath in listRecent] == ["/repo/stepA/good.csv"]


def testFlaggedPathsAreRepoRelativeAndCappedAtTwenty():
    listAbsolute = ["/repo/step/file%02d.csv" % iIndex for iIndex in range(25)]
    listRelative = pipelineRoutes._flistRepoRelativePaths(
        {"sProjectRepoPath": "/repo"}, listAbsolute + ["/elsewhere/x.csv"],
    )
    assert len(listRelative) == 20
    assert listRelative[0] == "step/file00.csv"
    assert pipelineRoutes._flistRepoRelativePaths(
        {"sProjectRepoPath": "/repo"}, ["/elsewhere/x.csv"],
    ) == ["/elsewhere/x.csv"]


# ── The AI-provenance stamp ──


class _ProvenanceDocker:
    """Answer the two container reads the stamp capture makes."""

    def __init__(self):
        self.iCaptures = 0

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        raise FileNotFoundError(sPath)

    def ftRunInContainerStreamed(self, sContainerId, sCommand, **dictKeywords):
        self.iCaptures += 1
        return SimpleNamespace(
            iExitCode=0, sStdout="claude\t2.1.0\nunknownAgent\t9\n",
            sStderr="",
        )


def _fdictAttestedWorkflow(sRepo):
    return {
        "sProjectRepoPath": sRepo,
        "listSteps": [{
            "sName": "AI Declaration", "sDirectory": "aiDeclaration",
            "sStepKind": S_AI_DECLARATION_STEP_KIND,
            "dictVerification": {"sUser": "passed"},
        }],
        "dictAiProvenance": {"listDeclaredModels": [DICT_DECLARED_MODEL]},
    }


def _fdictReadStamp(sRepo):
    with open(os.path.join(
        sRepo, aiProvenanceStamp.fsStampRelativePath(),
    )) as fileIn:
        return json.load(fileIn)


def _fnMaintainStamp(connectionDocker, dictWorkflow, sRepo):
    asyncio.run(pipelineRoutes._fnMaintainAiProvenanceStamp(
        {"docker": connectionDocker}, S_CONTAINER, dictWorkflow, sRepo,
    ))


def testAMissingStampIsCapturedFromTheLiveContainer(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (True, True),
    )
    sRepo = str(tmp_path)
    connectionDocker = _ProvenanceDocker()
    _fnMaintainStamp(connectionDocker, _fdictAttestedWorkflow(sRepo), sRepo)
    dictStamp = _fdictReadStamp(sRepo)
    assert dictStamp["listDeclaredModels"] == [DICT_DECLARED_MODEL]
    assert dictStamp["dictAgentCliVersions"] == {"claude": "2.1.0"}
    assert dictStamp["bNetworkIsolatedAtCapture"] is True
    assert dictStamp["sWorkspacePromptSha256"] == ""


def testAHandEditedStampDoesNotSurviveTheNextPoll(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (False, False),
    )
    sRepo = str(tmp_path)
    dictWorkflow = _fdictAttestedWorkflow(sRepo)
    connectionDocker = _ProvenanceDocker()
    _fnMaintainStamp(connectionDocker, dictWorkflow, sRepo)
    assert _fdictReadStamp(sRepo)["bNetworkIsolatedAtCapture"] is None
    dictEdited = _fdictReadStamp(sRepo)
    dictEdited["listDeclaredModels"] = []
    with open(os.path.join(
        sRepo, aiProvenanceStamp.fsStampRelativePath(),
    ), "w") as fileOut:
        json.dump(dictEdited, fileOut)
    _fnMaintainStamp(connectionDocker, dictWorkflow, sRepo)
    assert _fdictReadStamp(sRepo)["listDeclaredModels"] == [
        DICT_DECLARED_MODEL,
    ]
    assert connectionDocker.iCaptures == 2


def testAStampThatMatchesItsDeclarationIsLeftAlone(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (True, False),
    )
    sRepo = str(tmp_path)
    dictWorkflow = _fdictAttestedWorkflow(sRepo)
    connectionDocker = _ProvenanceDocker()
    _fnMaintainStamp(connectionDocker, dictWorkflow, sRepo)
    _fnMaintainStamp(connectionDocker, dictWorkflow, sRepo)
    assert connectionDocker.iCaptures == 1


@pytest.mark.parametrize("fdictBuildWorkflow", [
    lambda sRepo: {"sProjectRepoPath": "", "listSteps": []},
    lambda sRepo: {"sProjectRepoPath": sRepo, "listSteps": []},
])
def testNoStampIsWrittenWithoutAnAttestedDeclaration(
    tmp_path, fdictBuildWorkflow,
):
    sRepo = str(tmp_path)
    connectionDocker = _ProvenanceDocker()
    _fnMaintainStamp(connectionDocker, fdictBuildWorkflow(sRepo), sRepo)
    assert connectionDocker.iCaptures == 0
    assert not os.path.exists(os.path.join(
        sRepo, aiProvenanceStamp.fsStampRelativePath(),
    ))


def testAStampRewriteThatFailsIsLoggedNotRaised(
    tmp_path, monkeypatch, caplog,
):
    def ftProbeThatFails(sContainer):
        raise RuntimeError("daemon unreachable")

    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation", ftProbeThatFails,
    )
    sRepo = str(tmp_path)
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        _fnMaintainStamp(
            _ProvenanceDocker(), _fdictAttestedWorkflow(sRepo), sRepo,
        )
    assert "AI-provenance stamp rewrite failed" in caplog.text


@pytest.mark.parametrize("sContent", ["{not json", "[1, 2]"])
def testAnUnparseableStampReadsAsAbsent(tmp_path, sContent):
    sStampPath = os.path.join(
        str(tmp_path), aiProvenanceStamp.fsStampRelativePath(),
    )
    os.makedirs(os.path.dirname(sStampPath))
    with open(sStampPath, "w") as fileOut:
        fileOut.write(sContent)
    assert pipelineRoutes._fdictReadStampFromSnapshot(str(tmp_path)) is None


# ── The Prompt Record summary on the envelope row ──


def _fdictCaptureRecord(sFileName, iRedactions, sKind, sPrevious):
    return {
        "sSessionFileName": sFileName, "iRedactionCount": iRedactions,
        "sCaptureKind": sKind, "sPreviousRecordSha256": sPrevious,
        "sSha256": "c" * 64, "iBytesCaptured": 10,
        "sCapturedAtUtc": "2026-01-01T00:00:00+00:00",
    }


def _fnWritePromptIndex(sRepo, dictIndex):
    sIndexPath = os.path.join(sRepo, ".vaibify", "promptRecord", "index.json")
    os.makedirs(os.path.dirname(sIndexPath), exist_ok=True)
    with open(sIndexPath, "w") as fileOut:
        json.dump(dictIndex, fileOut)


def testThePromptRecordSummaryCountsSessionsGapsAndTheChain(
    tmp_path,
):
    dictFirst = _fdictCaptureRecord("sessionOne.jsonl", 2, "whole", "")
    dictSecond = _fdictCaptureRecord(
        "sessionOne.jsonl", 3, "appended",
        promptRecordManager._fsHashRecord(dictFirst),
    )
    _fnWritePromptIndex(str(tmp_path), {
        "listCaptures": [dictFirst, dictSecond],
        "listCoverageIntervals": [["a", "b"], ["c", "d"]],
        "iSessionsOutsideProject": 1,
    })
    dictSummary = pipelineRoutes._fdictEnvelopePromptRecord(
        {"dictAiProvenance": {"dictPromptRecord": {"bEnabled": True}}},
        ffilesEnsureRepoFiles(str(tmp_path)),
    )
    assert dictSummary == {
        "bEnabled": True, "bFirstCaptureReviewed": False,
        "iSessionCount": 1, "iRedactionTotal": 5, "bGapPresent": True,
        "bChainIntact": True, "iSessionsOutsideProject": 1,
    }


def testAPromptRecordWithABrokenChainSaysSo(tmp_path):
    _fnWritePromptIndex(str(tmp_path), {"listCaptures": [
        _fdictCaptureRecord("sessionOne.jsonl", 0, "whole", "f" * 64),
    ]})
    dictSummary = pipelineRoutes._fdictEnvelopePromptRecord(
        {}, ffilesEnsureRepoFiles(str(tmp_path)),
    )
    assert dictSummary["bChainIntact"] is False
    assert dictSummary["bEnabled"] is False


def testAnUnreadablePromptIndexReportsNothingRatherThanGuessing(
    tmp_path,
):
    sIndexPath = os.path.join(
        str(tmp_path), ".vaibify", "promptRecord", "index.json",
    )
    os.makedirs(os.path.dirname(sIndexPath))
    with open(sIndexPath, "w") as fileOut:
        fileOut.write("{truncated")
    dictSummary = pipelineRoutes._fdictEnvelopePromptRecord(
        {}, ffilesEnsureRepoFiles(str(tmp_path)),
    )
    assert dictSummary["iSessionCount"] == 0
    assert dictSummary["bChainIntact"] is True


# ── Smaller poll helpers with a stated contract ──


def testAStopWithNoProjectOpenEndsTheOnlyLiveRun():
    """With several live runs, guessing would stop somebody else's work."""
    taskLive = SimpleNamespace(done=lambda: False)
    taskOther = SimpleNamespace(done=lambda: False)
    dictCtx = {"workflows": {}, "pipelineTasks": {
        S_CONTAINER: {"/workspace/projectA": taskLive},
    }}
    assert pipelineRoutes._ftaskRunToStop(dictCtx, S_CONTAINER) is taskLive
    dictCtx["pipelineTasks"][S_CONTAINER]["/workspace/projectB"] = taskOther
    assert pipelineRoutes._ftaskRunToStop(dictCtx, S_CONTAINER) is None


def testAKilledCountThatIsNotANumberReadsAsZero():
    sTag = pipelineRoutes.S_KILLED_COUNT_TAG
    assert pipelineRoutes._fiParseKilledCount(sTag + " 3\n") == 3
    assert pipelineRoutes._fiParseKilledCount(sTag + " many\n") == 0
    assert pipelineRoutes._fiParseKilledCount(
        "kill: no such process\n" + sTag + " 2",
    ) == 2


def testMarkersMapOntoLiveStepIndicesByDirectory():
    dictResult = pipelineRoutes._fdictMarkersByStepIndex(
        [({"sDirectory": "stepB"}, {"iPassed": 1}),
         ({"sDirectory": "stepGone"}, {"iPassed": 9})],
        [{"sDirectory": "stepA"}, {"sDirectory": "stepB"},
         {"sDirectory": ""}],
    )
    assert dictResult == {1: {"iPassed": 1}}


def testACacheThatCannotBePersistedIsLoggedNotRaised(caplog):
    """The poll's one persisted cache is the container's; it never raises.

    The host-side mtime cache this test once covered is gone with the
    host read it served. What remains is the sha cache the poll writes
    back into the container, whose contract is the same: a failed write
    costs a rehash next poll and is logged, never raised into the poll.
    """
    dictCtx = {"docker": _DockerThatCannotAnswer()}
    with caplog.at_level(logging.INFO, logger="vaibify"):
        pipelineRoutes._fnPersistShaCacheToContainer(
            dictCtx, S_CONTAINER, "/workspace/project",
            {"stepA/out.csv": {
                "listStatKey": [1, 2, 3, 4], "sSha256": "a" * 64,
                "sBlobSha": "b" * 40}},
        )
    assert "sha cache save failed" in caplog.text


class _DockerThatCannotAnswer:
    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        raise RuntimeError("exec socket closed")

    def ftRunInContainerStreamed(self, *tArguments, **dictKeywords):
        raise RuntimeError("exec socket closed")

    def fnWriteFile(self, *tArguments, **dictKeywords):
        raise RuntimeError("exec socket closed")


def testTheShaCacheStartsEmptyWhenTheContainerCannotAnswer():
    """A failed hydrate costs a rehash; it never fabricates hashes."""
    dictCtx = {"docker": _DockerThatCannotAnswer()}
    assert pipelineRoutes._fdictHydrateShaCacheFromContainer(
        dictCtx, S_CONTAINER, "/workspace/project",
    ) == {}
    assert pipelineRoutes._fdictHydrateShaCacheFromContainer(
        dictCtx, S_CONTAINER, "",
    ) == {}
    pipelineRoutes._fnPersistShaCacheToContainer(
        dictCtx, S_CONTAINER, "/workspace/project", {"a.csv": "b"},
    )


def testEachProjectInAContainerKeepsItsOwnShaCache():
    dictCtx = {"docker": _DockerThatCannotAnswer()}
    dictFirst = pipelineRoutes._fdictManifestShaCache(
        dictCtx, S_CONTAINER, "/workspace/projectA",
    )
    dictFirst["out.csv"] = "hashOfProjectA"
    dictSecond = pipelineRoutes._fdictManifestShaCache(
        dictCtx, S_CONTAINER, "/workspace/projectB",
    )
    assert dictSecond == {}
    assert pipelineRoutes._fdictManifestShaCache(
        dictCtx, S_CONTAINER, "/workspace/projectA",
    ) == {"out.csv": "hashOfProjectA"}


@pytest.mark.parametrize("jsonValue, sExpected", [
    (4.0, "4"), (0.5, "0.5"), (None, ""), ("OPENBLAS", "OPENBLAS"),
])
def testAPinnedDeterminismValueIsDisplayedAsWritten(
    jsonValue, sExpected,
):
    assert pipelineRoutes._fsStringifyDeterminismValue(
        {"sValue": jsonValue}, "sValue",
    ) == sExpected
    assert pipelineRoutes._fsStringifyDeterminismValue(
        {"sValue": jsonValue}, "",
    ) == ""


def testAMalformedModelDeclarationIsStillNamed():
    assert pipelineRoutes._fsNameDeclaredModel("not a dict") == (
        "A declaration"
    )
    assert pipelineRoutes._fsNameDeclaredModel({"sVendor": "vendorAlpha"}) == (
        "vendorAlpha / ?"
    )


def testARootContextFileIsOfferedOnlyUntilItIsAdopted(tmp_path):
    sRepo = str(tmp_path)
    filesRepo = ffilesEnsureRepoFiles(sRepo)
    assert pipelineRoutes._fbRootContextCandidateDetected(filesRepo) is False
    with open(os.path.join(sRepo, "AGENTS.md"), "w") as fileOut:
        fileOut.write("context\n")
    assert pipelineRoutes._fbRootContextCandidateDetected(filesRepo) is True
    os.makedirs(os.path.join(sRepo, ".vaibify"))
    with open(os.path.join(sRepo, ".vaibify", "AGENTS.md"), "w") as fileOut:
        fileOut.write("context\n")
    assert pipelineRoutes._fbRootContextCandidateDetected(filesRepo) is False


def testAStepSuffixedGeneratedTestIsStillComparedToItsTemplate():
    """Generated tests gained step suffixes; edits must still be seen."""
    dictExpected = {"test_quantitative.py": "templateHash"}
    assert pipelineRoutes._flistFindCustomTestFiles(
        {"test_quantitative_stepA.py": "editedHash",
         "test_quantitative_stepB.py": "templateHash",
         "test_unrelated.py": "anything"},
        dictExpected,
    ) == ["test_quantitative_stepA.py"]


def testTestFileChangesNameNewMissingAndEditedFiles():
    dictStep = {"sDirectory": "stepA", "dictTests": {"quantitative": {
        "saCommands": ["pytest tests/test_quantitative_stepA.py",
                       "pytest test_registered_gone.py"],
    }}}
    dictEntry = pipelineRoutes._fdictBuildStepTestChangeEntry(
        dictStep,
        {"listFiles": ["test_quantitative_stepA.py", "test_extra.py"],
         "dictHashes": {"test_quantitative_stepA.py": "editedHash"}},
        {"test_quantitative.py": "templateHash"},
    )
    assert dictEntry == {
        "listNew": ["test_extra.py"],
        "listMissing": ["test_registered_gone.py"],
        "listCustom": ["test_quantitative_stepA.py"],
    }


def testAManifestThatDoesNotExistReportsZeroEntries(tmp_path):
    dictResult = pipelineRoutes._fdictBuildManifestVerifyResult(
        ffilesEnsureRepoFiles(str(tmp_path)), [], ["stepA/out.csv"],
    )
    assert dictResult["iTotal"] == 0
    assert dictResult["iMatching"] == 0
    assert dictResult["saIncomplete"] == ["stepA/out.csv"]


@pytest.mark.parametrize("iRequested, iExpected", [
    (-5, 5 * 1024 * 1024), ("many", 5 * 1024 * 1024),
    (10 ** 12, 50 * 1024 * 1024), (100, 100),
])
def testTheManifestViewerCapIsClamped(iRequested, iExpected):
    assert pipelineRoutes._fiClampManifestMaxBytes(iRequested) == iExpected


def testTheManifestTextOfAProjectWithoutARepositoryIsRefused():
    client, _connectionDocker = _tConnectGatedClient(
        DockerDoubleServingALevelThreeWorkflow(),
    )
    client.app.state.dictRouteContext["workflows"][S_CONTAINER_ID][
        "sProjectRepoPath"
    ] = ""
    responseHttp = client.get(
        f"/api/workflow/{S_CONTAINER_ID}/manifest/text",
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == (
        "No repository configured for this project."
    )
