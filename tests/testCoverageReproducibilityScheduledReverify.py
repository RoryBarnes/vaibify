"""The scheduled re-verify: configuration refusals, persistence, and the loop.

Real repositories and a real state file under a private ``$HOME``
carry every file-level assertion. The network verify of one remote
(``fdictVerifyRemoteService``) and Zenodo's HTTP transport are the
boundaries replaced; the loop is driven on a real asyncio event loop
with a cadence of milliseconds rather than hours.
"""

import asyncio
import logging
import types

import pytest
import requests

from vaibify.reproducibility import scheduledReverify, zenodoClient
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_CONTAINER_ID = "c0ffee12ab34"
S_WORKFLOW_ID = "workflowAlpha"


@pytest.fixture
def pathHome(monkeypatch, tmp_path):
    """A private $HOME, so the state file never touches ~/.vaibify."""
    pathPrivateHome = tmp_path / "home"
    pathPrivateHome.mkdir()
    monkeypatch.setenv("HOME", str(pathPrivateHome))
    return pathPrivateHome


# ── the origin remote, read from .git/config ─────────────────────


def fnWriteGitConfig(pathRepo, baConfig):
    """Write ``.git/config`` bytes under a repository."""
    (pathRepo / ".git").mkdir(parents=True, exist_ok=True)
    (pathRepo / ".git" / "config").write_bytes(baConfig)


def testTheOriginUrlIsReadFromItsOwnSection(tmp_path):
    """Only the ``origin`` section's url is the origin remote."""
    fnWriteGitConfig(tmp_path, (
        b'[core]\n\tbare = false\n[remote "upstream"]\n'
        b"\turl = https://example.invalid/upstream.git\n"
        b'[remote "origin"]\n\turl = https://example.invalid/origin.git\n'
    ))
    assert scheduledReverify._fsReadOriginRemoteUrl(
        HostRepoFiles(str(tmp_path)),
    ) == (
        "https://example.invalid/origin.git"
    )


def testAConfigWithNoOriginUrlAnswersEmpty(tmp_path):
    """An origin section without a url is no remote, not a guess."""
    fnWriteGitConfig(tmp_path, (
        b'[remote "origin"]\n\tfetch = +refs/heads/*:refs/remotes/o/*\n'
        b'[remote "upstream"]\n\turl = https://example.invalid/up.git\n'
    ))
    assert scheduledReverify._fsReadOriginRemoteUrl(
        HostRepoFiles(str(tmp_path)),
    ) == ""


def testAnUndecodableConfigAnswersEmpty(tmp_path):
    """Bytes that are not text read as 'no remote', never a crash."""
    fnWriteGitConfig(tmp_path, b'[remote "origin"]\n\turl = \xff\xfe\n')
    assert scheduledReverify._fsReadOriginRemoteUrl(
        HostRepoFiles(str(tmp_path)),
    ) == ""


# ── per-service configuration refusals ───────────────────────────


def testGithubWithoutOwnerIsRefusedByThePanelItNames():
    """No owner/repo is a configuration refusal naming the Repos panel."""
    with pytest.raises(
        scheduledReverify.ReverifyConfigError, match="Open the Repos panel",
    ):
        scheduledReverify._fdictFetchGithubHashes(
            {"sOwner": "", "sRepo": "projectAlpha"}, ["a.csv"],
        )


def testOverleafWithoutAProjectIdIsRefused(tmp_path):
    """No Overleaf project id refuses before any clone is attempted."""
    with pytest.raises(
        scheduledReverify.ReverifyConfigError, match="Overleaf card",
    ):
        scheduledReverify._fdictFetchOverleafHashes(
            {"sProjectId": ""}, ["figures/a.pdf"], str(tmp_path),
        )


# ── the archived attestation ─────────────────────────────────────


class _FakeResponse:
    def __init__(self, iStatus, jsonBody=None, baContent=b""):
        self.status_code = iStatus
        self.headers = {}
        self._jsonBody = jsonBody
        self._baContent = baContent
        self.text = ""

    def json(self):
        return self._jsonBody

    def iter_content(self, iChunkBytes):
        yield self._baContent


def fnServeRecords(monkeypatch, dictRecordFiles):
    """Serve each sandbox record id with the given file table."""
    def fresponseGet(sUrl, **kwargs):
        for sRecordId, dictFiles in dictRecordFiles.items():
            sRecordUrl = "https://sandbox.zenodo.org/api/records/" + sRecordId
            if sUrl == sRecordUrl:
                return _FakeResponse(200, {"files": [
                    {"key": sKey, "links": {"self": sRecordUrl + "/f/" + sKey}}
                    for sKey in dictFiles
                ]})
            if sUrl.startswith(sRecordUrl + "/f/"):
                return _FakeResponse(
                    200, baContent=dictFiles[sUrl.rsplit("/", 1)[-1]],
                )
        return _FakeResponse(404, {})

    monkeypatch.setattr(zenodoClient, "requests", types.SimpleNamespace(
        get=fresponseGet, RequestException=requests.RequestException,
    ))


def testNoDeclaredRecordServingAnAttestationAnswersNone(monkeypatch):
    """Records without the attestation file yield None, never a guess."""
    fnServeRecords(monkeypatch, {"101": {"data.csv": b"x"}, "102": {}})
    assert scheduledReverify._fjsonFetchArchivedAttestation({
        "sService": "sandbox", "sRecordId": "101",
        "listRecords": [{"sRecordId": "102"}],
    }) is None


def testTheFirstRecordServingAnAttestationWins(monkeypatch):
    """The second declared record's attestation is found and decoded."""
    fnServeRecords(monkeypatch, {
        "101": {"data.csv": b"x"},
        "102": {"l3_attestation.json": b'{"sStatus": "passed"}'},
    })
    assert scheduledReverify._fjsonFetchArchivedAttestation({
        "sService": "sandbox", "sRecordId": "101",
        "listRecords": [{"sRecordId": "102"}],
    }) == {"sStatus": "passed"}


# ── one verify attempt ───────────────────────────────────────────


def testAStatusThatCannotBeWrittenIsAnErrorResultWithRedaction(
    monkeypatch, tmp_path,
):
    """A failed sidecar write is reported as the service's error, redacted."""
    monkeypatch.setattr(
        scheduledReverify, "fdictVerifyRemoteService",
        lambda filesRepo, dictWorkflow, sService, sNowIso=None: {"a": 1},
    )

    def fnRefuseWrite(filesRepo, dictStatus):
        raise OSError(
            "cannot write https://user:hunterPassword@example.invalid/x",
        )

    monkeypatch.setattr(scheduledReverify, "fnWriteSyncStatus", fnRefuseWrite)
    dictResult = scheduledReverify.fdictAttemptOneVerify(
        str(tmp_path), {}, "zenodo", "2026-01-01T00:00:00Z",
    )
    assert dictResult["sService"] == "zenodo"
    assert dictResult["sStatus"] == "error"
    assert "hunterPassword" not in dictResult["sError"]


def testAStatusThatIsWrittenIsAnOkResult(monkeypatch, tmp_path):
    """The same attempt whose write lands reports ok."""
    listWritten = []
    monkeypatch.setattr(
        scheduledReverify, "fdictVerifyRemoteService",
        lambda filesRepo, dictWorkflow, sService, sNowIso=None: {"a": 1},
    )
    monkeypatch.setattr(
        scheduledReverify, "fnWriteSyncStatus",
        lambda filesRepo, dictStatus: listWritten.append(dictStatus),
    )
    assert scheduledReverify.fdictAttemptOneVerify(
        str(tmp_path), {}, "zenodo", "2026-01-01T00:00:00Z",
    ) == {"sService": "zenodo", "sStatus": "ok"}
    assert listWritten == [{"a": 1}]


# ── one pass over the context's workflows ────────────────────────


def testContainerEntriesAreVerifiedThroughTheirOwnAdapter(monkeypatch):
    """A (container id, workflow) pair asks the context for that id's files."""
    listFilesAsked = []
    listVerified = []
    objectAdapter = object()

    def ffilesForContainer(sContainerId):
        listFilesAsked.append(sContainerId)
        return objectAdapter

    def fdictRunOne(dictWorkflow, sNowIso=None, filesRepo=None):
        listVerified.append((dictWorkflow["sWorkflowId"], filesRepo))
        return {"sWorkflowId": dictWorkflow["sWorkflowId"], "listResults": [
            {"sService": "zenodo", "sStatus": "ok"},
        ]}

    monkeypatch.setattr(
        scheduledReverify, "fdictRunReverifyForWorkflow", fdictRunOne,
    )
    dictCtx = {"files": ffilesForContainer, "workflows": {
        S_CONTAINER_ID: {"sWorkflowId": S_WORKFLOW_ID},
    }}
    dictReport = scheduledReverify.fdictRunReverifyOnce(
        dictCtx, scheduledReverify._flistEnumerateWorkflows(dictCtx)
        + [{"sWorkflowId": "workflowLegacy"}],
        sNowIso="2026-01-01T00:00:00Z",
    )
    assert listFilesAsked == [S_CONTAINER_ID]
    assert listVerified == [
        (S_WORKFLOW_ID, objectAdapter), ("workflowLegacy", None),
    ]
    assert dictReport["sNowIso"] == "2026-01-01T00:00:00Z"
    assert len(dictReport["listResults"]) == 2


def testAContextWithoutAFilesFactoryFallsBackToTheHost(monkeypatch):
    """No ``files`` callable means the host adapter (None) is used."""
    listVerified = []
    monkeypatch.setattr(
        scheduledReverify, "fdictRunReverifyForWorkflow",
        lambda dictWorkflow, sNowIso=None, filesRepo=None: (
            listVerified.append(filesRepo)
            or {"sWorkflowId": "", "listResults": []}
        ),
    )
    scheduledReverify.fdictRunReverifyOnce(
        {}, [(S_CONTAINER_ID, {"sWorkflowId": S_WORKFLOW_ID})],
    )
    assert listVerified == [None]


def testOnlyContainerEntriesBumpTheSyncEpoch(monkeypatch):
    """A bare workflow dict has no container to invalidate."""
    from vaibify.gui import pipelineServer
    listBumped = []
    monkeypatch.setattr(
        pipelineServer, "fnBumpSyncEpoch",
        lambda dictCtx, sContainerId: listBumped.append(sContainerId),
    )
    scheduledReverify._fnBumpSyncEpochForVerifiedContainers(
        {}, [{"sWorkflowId": "legacy"}, (S_CONTAINER_ID, {})],
    )
    assert listBumped == [S_CONTAINER_ID]


# ── the persisted stamp and the first delay ──────────────────────


def testAStateFileThatIsNotAnObjectReadsAsNeverRan(pathHome):
    """A JSON list in the state file is not a stamp."""
    (pathHome / ".vaibify").mkdir()
    (pathHome / ".vaibify" / "reverifyState.json").write_text("[1, 2]")
    assert scheduledReverify.fsReadLastReverifyIso() == ""
    assert scheduledReverify.fdictDescribeReverifySchedule()["bEverRan"] is (
        False
    )


def testAStampThatCannotBePersistedIsLoggedNotRaised(pathHome, caplog):
    """An unwritable state directory warns and leaves 'never ran'."""
    (pathHome / ".vaibify").write_text("a file where a directory belongs")
    with caplog.at_level(logging.WARNING, logger=scheduledReverify.__name__):
        scheduledReverify.fnRecordLastReverifyIso("2026-01-01T00:00:00Z")
    assert "Could not persist the scheduled-reverify stamp" in caplog.text
    assert scheduledReverify.fsReadLastReverifyIso() == ""


def testAStampThatIsPersistedIsReadBack(pathHome):
    """The same write with a directory available round-trips."""
    scheduledReverify.fnRecordLastReverifyIso("2026-01-01T00:00:00Z")
    assert scheduledReverify.fsReadLastReverifyIso() == "2026-01-01T00:00:00Z"


def testAMalformedStampGivesTheStartupDelayOnly(monkeypatch):
    """An unparseable stamp is no evidence of a pass: the short delay."""
    monkeypatch.setattr(scheduledReverify.random, "uniform", lambda a, b: 0.0)
    assert scheduledReverify.ffComputeFirstReverifyDelay(
        6.0, "yesterday-ish", fNowEpoch=1767225600.0,
    ) == scheduledReverify._F_STARTUP_DELAY_SECONDS


def testARecentStampDefersToTheRemainingCadence(monkeypatch):
    """A pass one hour ago on a six-hour cadence waits five more hours."""
    monkeypatch.setattr(scheduledReverify.random, "uniform", lambda a, b: 0.0)
    assert scheduledReverify.ffComputeFirstReverifyDelay(
        6.0, "2026-01-01T00:00:00Z", fNowEpoch=1767225600.0 + 3600.0,
    ) == pytest.approx(5 * 3600.0)


# ── the loop and its lifespan hooks ──────────────────────────────


def testAFailedPassRecordsNoStampAndTheNextPassDoes(monkeypatch, pathHome):
    """Only a completed pass is stamped and bumps the epoch."""
    listStampsAtEachPass = []
    listBumped = []

    def fdictRunOnce(dictCtx, listWorkflows, sNowIso=None):
        listStampsAtEachPass.append(scheduledReverify.fsReadLastReverifyIso())
        if len(listStampsAtEachPass) == 1:
            raise RuntimeError("network down for the first pass")
        return {"listResults": []}

    from vaibify.gui import pipelineServer
    monkeypatch.setattr(
        scheduledReverify, "fdictRunReverifyOnce", fdictRunOnce,
    )
    monkeypatch.setattr(
        scheduledReverify, "ffComputeFirstReverifyDelay",
        lambda fHours, sLastIso: 0.0,
    )
    monkeypatch.setattr(
        pipelineServer, "fnBumpSyncEpoch",
        lambda dictCtx, sContainerId: listBumped.append(sContainerId),
    )

    async def fnDriveTwoPasses():
        taskLoop = asyncio.ensure_future(scheduledReverify._fnReverifyLoop(
            {"workflows": {S_CONTAINER_ID: {}}}, 1e-6,
        ))
        for _ in range(400):
            await asyncio.sleep(0.005)
            if listBumped:
                break
        taskLoop.cancel()
        try:
            await taskLoop
        except asyncio.CancelledError:
            pass

    asyncio.run(fnDriveTwoPasses())
    assert listStampsAtEachPass[:2] == ["", ""]
    assert scheduledReverify.fsReadLastReverifyIso() != ""
    assert listBumped[0] == S_CONTAINER_ID


def testTheLifespanHooksStartAndCancelTheLoop(monkeypatch):
    """Startup creates the task; shutdown cancels it and awaits its end."""
    monkeypatch.setattr(
        scheduledReverify, "ffComputeFirstReverifyDelay",
        lambda fHours, sLastIso: 3600.0,
    )
    appFake = types.SimpleNamespace(state=types.SimpleNamespace(
        listLifespanStartup=[], listLifespanShutdown=[],
    ))
    scheduledReverify.fnScheduleReverify(appFake, {})
    assert len(appFake.state.listLifespanStartup) == 1
    assert len(appFake.state.listLifespanShutdown) == 1

    async def fnStartThenStop():
        await appFake.state.listLifespanStartup[0](appFake)
        taskLoop = appFake.state.taskScheduledReverify
        await asyncio.sleep(0)
        assert not taskLoop.done()
        await appFake.state.listLifespanShutdown[0](appFake)
        return taskLoop

    taskLoop = asyncio.run(fnStartThenStop())
    assert taskLoop.done()


def testShutdownWithoutAStartedLoopIsQuiet():
    """A shutdown hook that finds no task returns without error."""
    appFake = types.SimpleNamespace(state=types.SimpleNamespace(
        listLifespanStartup=[], listLifespanShutdown=[],
    ))
    scheduledReverify.fnScheduleReverify(appFake, {})
    assert asyncio.run(
        appFake.state.listLifespanShutdown[0](appFake),
    ) is None
    assert not hasattr(appFake.state, "taskScheduledReverify")


def testShutdownSurvivesALoopThatAlreadyDied(monkeypatch):
    """A loop that crashed at startup does not break the hub's shutdown."""
    def ffRaiseOnDelay(fHours, sLastIso):
        raise RuntimeError("state unreadable")

    monkeypatch.setattr(
        scheduledReverify, "ffComputeFirstReverifyDelay", ffRaiseOnDelay,
    )
    appFake = types.SimpleNamespace(state=types.SimpleNamespace(
        listLifespanStartup=[], listLifespanShutdown=[],
    ))
    scheduledReverify.fnScheduleReverify(appFake, {})

    async def fnStartThenStop():
        await appFake.state.listLifespanStartup[0](appFake)
        await asyncio.sleep(0)
        await appFake.state.listLifespanShutdown[0](appFake)
        return appFake.state.taskScheduledReverify

    taskLoop = asyncio.run(fnStartThenStop())
    assert isinstance(taskLoop.exception(), RuntimeError)
