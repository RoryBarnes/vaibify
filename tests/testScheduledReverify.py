"""Tests for vaibify.reproducibility.scheduledReverify."""

import asyncio
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import scheduledReverify


def _fdictBuildWorkflow(sProjectRepo, sId="wf01"):
    """Return a minimal workflow dict with three remotes configured."""
    return {
        "sWorkflowId": sId,
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {
            "github": {
                "sOwner": "owner",
                "sRepo": "repo",
                "sBranch": "main",
            },
            "overleaf": {"sProjectId": "project1234"},
            "zenodo": {"sRecordId": "98765", "sService": "sandbox"},
        },
        "listSteps": [
            {
                "sDirectory": "step01",
                "saDataFiles": ["step01/data.csv"],
                "saPlotFiles": [],
                "saOutputFiles": [],
            },
        ],
    }


def _fnWriteManifestForOneFile(sProjectRepo, sExpectedHash):
    """Write a single-entry MANIFEST.sha256 for the test repo."""
    sManifest = os.path.join(sProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(
            "# SHA-256 manifest of workflow outputs\n"
            f"{sExpectedHash}  step01/data.csv\n"
        )


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Return a temp project repo with a one-line manifest."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
    _fnWriteManifestForOneFile(sRepo, "a" * 64)
    return sRepo


# --------- fnRunReverifyOnce: happy path ---------


def testRunReverifyOnceCoversAllConfiguredServices(tmp_path):
    """Two workflows × three remotes → six results, all sStatus=ok."""
    sRepoOne = str(tmp_path / "one")
    sRepoTwo = str(tmp_path / "two")
    for sRepo in (sRepoOne, sRepoTwo):
        os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
        _fnWriteManifestForOneFile(sRepo, "a" * 64)
    listWorkflows = [
        _fdictBuildWorkflow(sRepoOne, "wfA"),
        _fdictBuildWorkflow(sRepoTwo, "wfB"),
    ]
    dictMatch = {"step01/data.csv": "a" * 64}
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ):
        dictReport = scheduledReverify.fnRunReverifyOnce(
            {"workflows": {}}, listWorkflows,
        )
    assert len(dictReport["listResults"]) == 6
    listStatuses = [d["sStatus"] for d in dictReport["listResults"]]
    assert listStatuses == ["ok"] * 6
    assert dictReport["sNowIso"]


# --------- fnRunReverifyOnce: one bad remote isolated ---------


def testRunReverifyOnceCapturesPerServiceFailures(tmp_path):
    """One service raising → only that service is sStatus=error."""
    sRepoOne = str(tmp_path / "one")
    sRepoTwo = str(tmp_path / "two")
    for sRepo in (sRepoOne, sRepoTwo):
        os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
        _fnWriteManifestForOneFile(sRepo, "a" * 64)
    listWorkflows = [
        _fdictBuildWorkflow(sRepoOne, "wfA"),
        _fdictBuildWorkflow(sRepoTwo, "wfB"),
    ]
    dictMatch = {"step01/data.csv": "a" * 64}
    from vaibify.reproducibility.githubMirror import GithubMirrorError
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        side_effect=GithubMirrorError(
            "GitHub authentication failed: token=abc123",
        ),
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ):
        dictReport = scheduledReverify.fnRunReverifyOnce(
            {"workflows": {}}, listWorkflows,
        )
    listResults = dictReport["listResults"]
    assert len(listResults) == 6
    listGithubResults = [
        d for d in listResults if d["sService"] == "github"
    ]
    listOtherResults = [
        d for d in listResults if d["sService"] != "github"
    ]
    assert all(d["sStatus"] == "error" for d in listGithubResults)
    assert all(d["sStatus"] == "ok" for d in listOtherResults)
    for dictGithub in listGithubResults:
        assert "abc123" not in dictGithub["sError"]


# --------- fnScheduleReverify: registration ---------


def testScheduleReverifyRegistersStartupAndShutdown():
    """fnScheduleReverify appends startup + shutdown hooks to the app's
    lifespan lists (the modern FastAPI pattern; @app.on_event is
    deprecated and unsupported when lifespan= is set)."""
    appMock = MagicMock()
    appMock.state.listLifespanStartup = []
    appMock.state.listLifespanShutdown = []
    scheduledReverify.fnScheduleReverify(
        appMock, {"workflows": {}}, fHoursCadence=0.1,
    )
    assert len(appMock.state.listLifespanStartup) == 1
    assert len(appMock.state.listLifespanShutdown) == 1


# --------- Manifest loading helper ---------


def testLoadManifestExpectedHashesParsesEntries(fixtureProjectRepo):
    """Manifest entries are parsed into a {relpath: hash} dict."""
    dictExpected = scheduledReverify.fdictLoadManifestExpectedHashes(
        fixtureProjectRepo,
    )
    assert dictExpected == {"step01/data.csv": "a" * 64}


def testLoadManifestRaisesWhenAbsent(tmp_path):
    """Missing manifest raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        scheduledReverify.fdictLoadManifestExpectedHashes(str(tmp_path))


# --------- Sync status read/write round trip ---------


def testWriteAndReadSyncStatusRoundTrip(fixtureProjectRepo):
    """A status written for one service is recovered by the read helper."""
    dictPersisted = {
        "sService": "overleaf",
        "sLastVerified": "2026-05-03T12:00:00Z",
        "iTotalFiles": 5,
        "iMatching": 4,
        "listDiverged": [
            {"sPath": "x.txt", "sExpected": "h1", "sActual": "h2"},
        ],
    }
    scheduledReverify.fnWriteSyncStatus(
        fixtureProjectRepo, dictPersisted,
    )
    dictReadBack = scheduledReverify.fdictReadCachedSyncStatus(
        fixtureProjectRepo, "overleaf",
    )
    assert dictReadBack == dictPersisted


def testReadCachedReturnsEmptyDefaultWhenAbsent(tmp_path):
    """Missing syncStatus.json yields a clean empty default per service."""
    sRepo = str(tmp_path)
    dictResult = scheduledReverify.fdictReadCachedSyncStatus(
        sRepo, "github",
    )
    assert dictResult["sService"] == "github"
    assert dictResult["sLastVerified"] is None
    assert dictResult["listDiverged"] == []


# --------- Config error path ---------


def testVerifyRemoteRaisesConfigErrorOnUnconfiguredService(
    fixtureProjectRepo,
):
    """A workflow without a remote configured for sService raises."""
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflow["dictRemotes"].pop("github")
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify.fdictVerifyRemoteService(
            fixtureProjectRepo, dictWorkflow, "github",
        )


# --------- Fix C3: scheduled loop dispatches via to_thread ---------


def test_reverify_loop_uses_to_thread():
    """The scheduled loop dispatches the synchronous verify pass off-loop.

    Uses fHoursCadence=0.0 so the real asyncio.sleep(0) yields once
    per iteration — the loop body must yield at least once each round
    or the test's wait_for never gets scheduled. Patching
    asyncio.sleep with a no-op coroutine breaks this and causes the
    test to spin without yielding (Py3.9-observed hang).
    """
    dictCtx = {"workflows": {"wf01": {"sWorkflowId": "wf01"}}}

    mockReverify = MagicMock(return_value={"sNowIso": "x", "listResults": []})
    mockToThread = MagicMock()

    async def _fnDriveOneIteration():
        eventDispatched = asyncio.Event()

        async def _fnFakeToThread(fnCallback, *args, **kwargs):
            eventDispatched.set()
            return fnCallback(*args, **kwargs)

        mockToThread.side_effect = _fnFakeToThread
        with patch(
            "vaibify.reproducibility.scheduledReverify.asyncio.to_thread",
            mockToThread,
        ), patch(
            "vaibify.reproducibility.scheduledReverify.fnRunReverifyOnce",
            mockReverify,
        ):
            taskLoop = asyncio.create_task(
                scheduledReverify._fnReverifyLoop(dictCtx, 0.0),
            )
            await asyncio.wait_for(eventDispatched.wait(), timeout=1.0)
            taskLoop.cancel()
            try:
                await taskLoop
            except asyncio.CancelledError:
                pass

    asyncio.run(_fnDriveOneIteration())
    assert mockToThread.called
    args, _kwargs = mockToThread.call_args
    assert args[0] is mockReverify
    assert args[1] is dictCtx
    listWorkflows = args[2]
    assert isinstance(listWorkflows, list)


# --------- Fix M2: parser unification with manifestWriter helpers ---------


def testLoadManifestExpectedHashesReturnsConsistentWithParser(tmp_path):
    """Loaded mapping must include escaped-path entries."""
    sRepo = str(tmp_path)
    sEscapedHash = "b" * 64
    sNormalHash = "a" * 64
    sManifest = os.path.join(sRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write("# header\n")
        fileHandle.write(f"{sNormalHash}  step01/data.csv\n")
        fileHandle.write(f"\\{sEscapedHash}  step01/has\\\\back.csv\n")
    dictExpected = scheduledReverify.fdictLoadManifestExpectedHashes(sRepo)
    assert dictExpected["step01/data.csv"] == sNormalHash
    assert dictExpected["step01/has\\back.csv"] == sEscapedHash


# --------- Fix M4: lost-update protection across threads ---------


def test_concurrent_sync_status_writes_do_not_lose_updates(tmp_path):
    """Two threads writing different services keep both entries."""
    sRepo = str(tmp_path)
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)

    def _fnBuildStatus(sService, iMatching):
        return {
            "sService": sService,
            "sLastVerified": "2026-05-03T00:00:00Z",
            "iTotalFiles": iMatching,
            "iMatching": iMatching,
            "listDiverged": [],
        }

    def _fnWorkerWrite(sService, iMatching):
        scheduledReverify.fnWriteSyncStatus(
            sRepo, _fnBuildStatus(sService, iMatching),
        )

    threadA = threading.Thread(
        target=_fnWorkerWrite, args=("github", 7),
    )
    threadB = threading.Thread(
        target=_fnWorkerWrite, args=("zenodo", 11),
    )
    threadA.start()
    threadB.start()
    threadA.join()
    threadB.join()
    dictA = scheduledReverify.fdictReadCachedSyncStatus(sRepo, "github")
    dictB = scheduledReverify.fdictReadCachedSyncStatus(sRepo, "zenodo")
    assert dictA["iMatching"] == 7
    assert dictB["iMatching"] == 11


# --------- Hardening: lock retry budget + missing-manifest redaction ---------


def test_acquire_sync_status_lock_retry_budget_is_at_least_one_second():
    """Three concurrent writers must not blow the retry budget.

    Regression for the original 5×50ms = 250ms ceiling: under
    contention the JSON read-modify-write critical section can
    credibly take longer than 250ms on a busy host, raising spurious
    RuntimeError from the second writer. The configured
    ``_I_LOCK_RETRY_MAX`` and ``_F_LOCK_RETRY_SLEEP`` constants must
    product to at least 1 second of patience before declaring failure.
    """
    fBudget = (
        scheduledReverify._I_LOCK_RETRY_MAX
        * scheduledReverify._F_LOCK_RETRY_SLEEP
    )
    assert fBudget >= 1.0, (
        f"lock retry budget {fBudget:.2f}s is too short for "
        "three concurrent writers under realistic disk pressure"
    )


def test_missing_manifest_error_is_captured_and_redacted(tmp_path):
    """A missing-manifest error must surface as sStatus=error, redacted."""
    sRepo = str(tmp_path / "no-manifest")
    os.makedirs(sRepo, exist_ok=True)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    dictResult = scheduledReverify._fdictAttemptOneVerify(
        sRepo, dictWorkflow, "github", "2026-05-03T00:00:00Z",
    )
    assert dictResult["sStatus"] == "error"
    assert "sError" in dictResult
    assert isinstance(dictResult["sError"], str)
    assert dictResult["sError"]


def test_sync_status_lock_releases_on_persist_exception(tmp_path):
    """An exception inside the critical section must release the flock.

    Otherwise a transient write failure permanently wedges every
    subsequent verify call until the host process exits.
    """
    sRepo = str(tmp_path)
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    dictStatus = {
        "sService": "github",
        "sLastVerified": "2026-05-03T00:00:00Z",
        "iTotalFiles": 0,
        "iMatching": 0,
        "listDiverged": [],
    }
    with patch(
        "vaibify.reproducibility.scheduledReverify._fnPersistSyncStatusEntry",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError):
            scheduledReverify.fnWriteSyncStatus(sRepo, dictStatus)
    # Second call should succeed: lock was released despite first
    # failure.
    scheduledReverify.fnWriteSyncStatus(sRepo, dictStatus)
    dictRead = scheduledReverify.fdictReadCachedSyncStatus(sRepo, "github")
    assert dictRead["sService"] == "github"
