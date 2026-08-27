"""Tests for vaibify.reproducibility.scheduledReverify.

The L2 verifies hash the workflow's declared canonical files as they
exist on disk at verify time (never MANIFEST.sha256 — the L3 envelope
artifact), so every verify fixture creates REAL files and mocks the
remote with the files' real hashes.
"""

import asyncio
import hashlib
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import overleafSync, scheduledReverify


_BA_DATA_CONTENT = b"canonical data bytes\n"
S_DATA_SHA = hashlib.sha256(_BA_DATA_CONTENT).hexdigest()
_BA_TEST_CONTENT = b"def test_quantitative(): pass\n"
S_TEST_SHA = hashlib.sha256(_BA_TEST_CONTENT).hexdigest()
_BA_PLOT_CONTENT = b"%PDF-1.4 canonical figure\n"
S_PLOT_SHA = hashlib.sha256(_BA_PLOT_CONTENT).hexdigest()


def _fdictBuildWorkflow(sProjectRepo, sId="wf01"):
    """Return a minimal workflow dict with four remotes configured.

    The step declares ``data.csv`` relative to its directory, which
    the canonical-path collector resolves to ``step01/data.csv`` —
    the file :func:`_fnWriteDataFile` creates.
    """
    return {
        "sWorkflowId": sId,
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {
            "github": {
                "sOwner": "owner",
                "sRepo": "repo",
                "sBranch": "main",
            },
            "overleaf": {
                "sProjectId": "project1234",
                "sLastPushCommit": "commit1",
            },
            "zenodo": {"sRecordId": "98765", "sService": "sandbox"},
            "arxiv": {"sArxivId": "2401.12345"},
        },
        "listSteps": [
            {
                "sDirectory": "step01",
                "saOutputDataFiles": ["data.csv"],
                "saPlotFiles": [],
            },
        ],
    }


def _fnWriteDataFile(sProjectRepo):
    """Create the declared step01/data.csv with known content."""
    os.makedirs(os.path.join(sProjectRepo, "step01"), exist_ok=True)
    with open(
        os.path.join(sProjectRepo, "step01", "data.csv"), "wb",
    ) as fileHandle:
        fileHandle.write(_BA_DATA_CONTENT)


def _fnWritePlotFile(sProjectRepo):
    """Create step01/plot.pdf with known content."""
    os.makedirs(os.path.join(sProjectRepo, "step01"), exist_ok=True)
    with open(
        os.path.join(sProjectRepo, "step01", "plot.pdf"), "wb",
    ) as fileHandle:
        fileHandle.write(_BA_PLOT_CONTENT)


def _fnWriteManifestForOneFile(sProjectRepo, sExpectedHash):
    """Write a single-entry MANIFEST.sha256 for the test repo."""
    sManifest = os.path.join(sProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(
            "# SHA-256 manifest of workflow outputs\n"
            f"{sExpectedHash}  step01/data.csv\n"
        )


def _fnRecordPushedFigures(sProjectRepo, listPaths=None):
    """Record an Overleaf push manifest so figure verifies have a scope."""
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commit1", listPaths or ["step01/data.csv"],
    )


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Return a temp project repo with a real declared file on disk."""
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    _fnWriteManifestForOneFile(sRepo, "a" * 64)
    _fnRecordPushedFigures(sRepo)
    return sRepo


# --------- fdictRunReverifyOnce: happy path ---------


def testRunReverifyOnceCoversAllConfiguredServices(tmp_path):
    """Two workflows × four remotes → eight results, all sStatus=ok."""
    sRepoOne = str(tmp_path / "one")
    sRepoTwo = str(tmp_path / "two")
    for sRepo in (sRepoOne, sRepoTwo):
        _fnWriteDataFile(sRepo)
        _fnRecordPushedFigures(sRepo)
    listWorkflows = [
        _fdictBuildWorkflow(sRepoOne, "wfA"),
        _fdictBuildWorkflow(sRepoTwo, "wfB"),
    ]
    dictMatch = {"step01/data.csv": S_DATA_SHA}
    # The Overleaf clone is hashed at the pushed REMOTE path (the push
    # flattens to <target>/<basename>; the fixture records target "").
    dictOverleafMatch = {"data.csv": S_DATA_SHA}
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value=dictOverleafMatch,
    ), patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ):
        dictReport = scheduledReverify.fdictRunReverifyOnce(
            {"workflows": {}}, listWorkflows,
        )
    assert len(dictReport["listResults"]) == 8
    listStatuses = [d["sStatus"] for d in dictReport["listResults"]]
    assert listStatuses == ["ok"] * 8
    assert dictReport["sNowIso"]


# --------- fdictRunReverifyOnce: one bad remote isolated ---------


def testRunReverifyOnceCapturesPerServiceFailures(tmp_path):
    """One service raising → only that service is sStatus=error."""
    sRepoOne = str(tmp_path / "one")
    sRepoTwo = str(tmp_path / "two")
    for sRepo in (sRepoOne, sRepoTwo):
        _fnWriteDataFile(sRepo)
        _fnRecordPushedFigures(sRepo)
    listWorkflows = [
        _fdictBuildWorkflow(sRepoOne, "wfA"),
        _fdictBuildWorkflow(sRepoTwo, "wfB"),
    ]
    dictMatch = {"step01/data.csv": S_DATA_SHA}
    from vaibify.reproducibility.githubMirror import GithubMirrorError
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        side_effect=GithubMirrorError(
            "GitHub authentication failed: token=abc123",
        ),
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value={"data.csv": S_DATA_SHA},
    ), patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ):
        dictReport = scheduledReverify.fdictRunReverifyOnce(
            {"workflows": {}}, listWorkflows,
        )
    listResults = dictReport["listResults"]
    assert len(listResults) == 8
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


# --------- arxiv-specific dispatcher wiring ---------


def testVerifyArxivPassesCacheDirToClient(fixtureProjectRepo):
    """The arxiv branch points the client at <repo>/.vaibify/arxivCache/."""
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflow["dictRemotes"]["arxiv"] = {
        "sArxivId": "2401.12345",
        "dictPathMap": {"step01/data.csv": "paper/data.csv"},
    }
    mockFetch = MagicMock(return_value={"step01/data.csv": S_DATA_SHA})
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        mockFetch,
    ):
        scheduledReverify.fdictVerifyRemoteService(
            fixtureProjectRepo, dictWorkflow, "arxiv",
        )
    _argsCall, dictKwargs = mockFetch.call_args
    sExpectedCache = os.path.join(
        fixtureProjectRepo, ".vaibify", "arxivCache",
    )
    assert dictKwargs["sCacheDir"] == sExpectedCache
    assert dictKwargs["dictPathMap"] == {
        "step01/data.csv": "paper/data.csv",
    }


def testVerifyArxivRaisesConfigErrorWhenIdMissing(fixtureProjectRepo):
    """Empty/missing sArxivId surfaces as a config error."""
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflow["dictRemotes"]["arxiv"] = {"sArxivId": ""}
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify.fdictVerifyRemoteService(
            fixtureProjectRepo, dictWorkflow, "arxiv",
        )


def testVerifyArxivScopesToPushedFiguresOnly(tmp_path):
    """The arXiv comparison set is the Overleaf push list only.

    An e-print carries only the manuscript figures. The workflow's
    other declared outputs (data files) must not inflate
    ``iTotalFiles`` or paint every non-figure as diverged (the
    "0 of 84" bug).
    """
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    _fnWritePlotFile(sRepo)
    _fnRecordPushedFigures(sRepo, ["step01/plot.pdf"])
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    dictWorkflow["listSteps"][0]["saPlotFiles"] = ["plot.pdf"]
    mockFetch = MagicMock(
        return_value={"step01/plot.pdf": S_PLOT_SHA},
    )
    with patch(
        "vaibify.reproducibility.arxivClient.fdictFetchRemoteHashes",
        mockFetch,
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "arxiv",
        )
    assert dictStatus["iTotalFiles"] == 1
    assert dictStatus["iMatching"] == 1
    assert dictStatus["listDiverged"] == []
    listRequestedPaths = mockFetch.call_args[0][1]
    assert listRequestedPaths == ["step01/plot.pdf"]


def testVerifyArxivWithoutPushManifestRaisesConfigError(tmp_path):
    """No recorded Overleaf push means no honest comparison set.

    A vacuous "0 of 0 matching" would render as synced; the verify
    must refuse instead so the dashboard says why.
    """
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "arxiv",
        )


# --------- overleaf-specific dispatcher wiring ---------


def testVerifyOverleafScopesToPushedFiguresAtRemotePaths(tmp_path):
    """The Overleaf comparison hashes the clone at pushed remote paths.

    The push flattens ``step01/plot.pdf`` into ``figures/plot.pdf``
    inside the Overleaf project, so the verify must request the remote
    path and re-key the result to the local path — and must not count
    the manifest's non-figure entries (the "0 of 84" bug).
    """
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    _fnWritePlotFile(sRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sRepo, "commit1", ["step01/plot.pdf"], "figures",
    )
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    dictWorkflow["listSteps"][0]["saPlotFiles"] = ["plot.pdf"]
    mockFetch = MagicMock(
        return_value={"figures/plot.pdf": S_PLOT_SHA},
    )
    with patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        mockFetch,
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "overleaf",
        )
    assert dictStatus["iTotalFiles"] == 1
    assert dictStatus["iMatching"] == 1
    assert dictStatus["listDiverged"] == []
    listRequestedPaths = mockFetch.call_args[0][1]
    assert listRequestedPaths == ["figures/plot.pdf"]


def testVerifyOverleafWithoutPushManifestRaisesConfigError(tmp_path):
    """No recorded push means no honest Overleaf comparison set."""
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "overleaf",
        )


def testVerifyOverleafContentDriftReadsDiverged(tmp_path):
    """A pushed figure whose Overleaf copy differs must read diverged.

    The expected side is the figure's live local hash, so an Overleaf
    copy from an older push honestly reads as drifted.
    """
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    _fnWritePlotFile(sRepo)
    overleafSync.fnRecordOverleafPushManifest(
        sRepo, "commit1", ["step01/plot.pdf"], "figures",
    )
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    dictWorkflow["listSteps"][0]["saPlotFiles"] = ["plot.pdf"]
    with patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value={"figures/plot.pdf": "c" * 64},
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "overleaf",
        )
    assert dictStatus["iTotalFiles"] == 1
    assert dictStatus["iMatching"] == 0
    assert [d["sPath"] for d in dictStatus["listDiverged"]] == [
        "step01/plot.pdf",
    ]


# --------- fnDeleteSyncStatus ---------


def testDeleteSyncStatusRemovesOnlyTargetService(fixtureProjectRepo):
    """Deleting one service's cache entry leaves the others intact."""
    scheduledReverify.fnWriteSyncStatus(fixtureProjectRepo, {
        "sService": "arxiv", "sLastVerified": "2026-07-09T00:00:00Z",
        "iTotalFiles": 2, "iMatching": 0, "listDiverged": [],
    })
    scheduledReverify.fnWriteSyncStatus(fixtureProjectRepo, {
        "sService": "github", "sLastVerified": "2026-07-09T00:00:00Z",
        "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
    })
    scheduledReverify.fnDeleteSyncStatus(fixtureProjectRepo, "arxiv")
    dictArxiv = scheduledReverify.fdictReadCachedSyncStatus(
        fixtureProjectRepo, "arxiv",
    )
    dictGithub = scheduledReverify.fdictReadCachedSyncStatus(
        fixtureProjectRepo, "github",
    )
    assert dictArxiv["sLastVerified"] is None
    assert dictGithub["sLastVerified"] == "2026-07-09T00:00:00Z"


def testDeleteSyncStatusIsNoOpWithoutCacheFile(tmp_path):
    """Deleting from a repo with no syncStatus.json must not raise."""
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    scheduledReverify.fnDeleteSyncStatus(sRepo, "arxiv")


# --------- Fix C3: scheduled loop dispatches via to_thread ---------


def test_reverify_loop_uses_to_thread():
    """The scheduled loop dispatches the synchronous verify pass off-loop.

    Uses fHoursCadence=0.0 so the real asyncio.sleep(0) yields once
    per iteration — the loop body must yield at least once each round
    or the test's wait_for never gets scheduled. Patching
    asyncio.sleep with a no-op coroutine breaks this and causes the
    test to spin without yielding (Py3.9-observed hang).

    The first-pass delay is patched to 0.0 because the production
    delay is deliberately never zero (see
    ``ffComputeFirstReverifyDelay``); the completion stamp is patched
    out so the test cannot write into the developer's home directory.
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
            "vaibify.reproducibility.scheduledReverify.fdictRunReverifyOnce",
            mockReverify,
        ), patch(
            "vaibify.reproducibility.scheduledReverify."
            "ffComputeFirstReverifyDelay",
            return_value=0.0,
        ), patch(
            "vaibify.reproducibility.scheduledReverify."
            "fnRecordLastReverifyIso",
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
    from vaibify.reproducibility import repoFiles
    fBudget = (
        repoFiles._I_LOCK_RETRY_MAX
        * repoFiles._F_LOCK_RETRY_SLEEP
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
        "vaibify.reproducibility.repoFiles.HostRepoFiles.fnWriteJsonAtomic",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError):
            scheduledReverify.fnWriteSyncStatus(sRepo, dictStatus)
    # Second call should succeed: lock was released despite first
    # failure.
    scheduledReverify.fnWriteSyncStatus(sRepo, dictStatus)
    dictRead = scheduledReverify.fdictReadCachedSyncStatus(sRepo, "github")
    assert dictRead["sService"] == "github"

# --------- Test files in the verify comparison set ---------


def _fdictWorkflowWithTestFile(sProjectRepo):
    """Return a workflow declaring a data file and a quantitative test.

    Creates both files on disk so the live expected-hash builder has
    real content to hash.
    """
    _fnWriteDataFile(sProjectRepo)
    sTestsDir = os.path.join(sProjectRepo, "step01", "tests")
    os.makedirs(sTestsDir, exist_ok=True)
    with open(
        os.path.join(sTestsDir, "test_quantitative.py"), "wb",
    ) as fileHandle:
        fileHandle.write(_BA_TEST_CONTENT)
    dictWorkflow = _fdictBuildWorkflow(sProjectRepo)
    dictWorkflow["listSteps"][0]["dictTests"] = {
        "dictQuantitative": {
            "sFilePath": "step01/tests/test_quantitative.py",
        },
    }
    return dictWorkflow


def testVerifyCountsDeclaredTestFilesInComparisonSet(tmp_path):
    """Declared test files are part of the remote comparison set."""
    sRepo = str(tmp_path / "project")
    dictWorkflow = _fdictWorkflowWithTestFile(sRepo)
    # Keyed by DEPOSIT KEY (the basename): a Zenodo deposit is flat,
    # so the verify requests basenames and maps them back.
    mockFetch = MagicMock(return_value={
        "data.csv": S_DATA_SHA,
        "test_quantitative.py": S_TEST_SHA,
    })
    with patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        mockFetch,
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "zenodo",
        )
    assert dictStatus["iTotalFiles"] == 2
    assert dictStatus["iMatching"] == 2
    _argsCall, dictKwargs = mockFetch.call_args
    assert "test_quantitative.py" in dictKwargs["listRelPaths"]


def testTestFileMissingFromRemoteIsDivergenceNotError(tmp_path):
    """A test file absent on the remote is reported as a divergence.

    The honest failure mode: the verify completes (sStatus stays ok),
    the gap shows up in listDiverged with sActual None.
    """
    sRepo = str(tmp_path / "project")
    dictWorkflow = _fdictWorkflowWithTestFile(sRepo)
    with patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value={"data.csv": S_DATA_SHA},
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "zenodo",
        )
        dictAttempt = scheduledReverify._fdictAttemptOneVerify(
            sRepo, dictWorkflow, "zenodo", "2026-06-11T00:00:00Z",
        )
    assert dictStatus["iTotalFiles"] == 2
    assert dictStatus["iMatching"] == 1
    assert dictStatus["listDiverged"] == [{
        "sPath": "step01/tests/test_quantitative.py",
        "sExpected": S_TEST_SHA,
        "sActual": None,
    }]
    assert dictAttempt["sStatus"] == "ok"


def testVerifyRefusesVacuousComparisonWhenNoFigureDeclared(tmp_path):
    """Pushed figures outside the declared set must refuse, not 0/0.

    Observed live (in the manifest era): an empty comparison set was
    recorded as "0 of 0 matching" — which the dashboard would render
    as attained. An empty expected set demonstrates nothing and must
    raise instead, leaving the previous cache entry untouched.
    """
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    _fnRecordPushedFigures(sRepo, ["Plot/corner.pdf"])  # undeclared
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    with pytest.raises(
        scheduledReverify.ReverifyConfigError, match="declared",
    ):
        scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "overleaf",
        )


def testVerifyExpectedHashesTrackCurrentFileContent(tmp_path):
    """The expected side is the file's CURRENT bytes, not a frozen pin.

    The defining behavior of live hashing: editing a local file flips
    an agreeing remote to diverged on the very next verify, with no
    intermediate artifact to regenerate.
    """
    sRepo = str(tmp_path / "project")
    _fnWriteDataFile(sRepo)
    dictWorkflow = _fdictBuildWorkflow(sRepo)
    dictRemote = {"data.csv": S_DATA_SHA}
    with patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictRemote,
    ):
        dictBefore = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "zenodo",
        )
        with open(
            os.path.join(sRepo, "step01", "data.csv"), "wb",
        ) as fileHandle:
            fileHandle.write(b"the science moved on\n")
        dictAfter = scheduledReverify.fdictVerifyRemoteService(
            sRepo, dictWorkflow, "zenodo",
        )
    assert dictBefore["iMatching"] == 1
    assert dictBefore["listDiverged"] == []
    assert dictAfter["iMatching"] == 0
    assert [d["sPath"] for d in dictAfter["listDiverged"]] == [
        "step01/data.csv",
    ]


# ------- Scheduling: the loop must actually reach its first pass -------
#
# The hub self-terminates after 30 minutes idle, so a loop that slept a
# full 6-hour cadence before its first pass only ever ran if a dashboard
# tab stayed open for six continuous hours. These tests pin the two
# properties that make the first pass reachable: a short first delay,
# and a stamp that lets a restart resume the cadence instead of
# restarting it. No test sleeps for real — the delay is computed, not
# awaited.


_F_SIX_HOUR_CADENCE = 6.0
_F_ONE_HOUR_SECONDS = 3600.0


@pytest.fixture
def fixtureIsolatedHome(tmp_path, monkeypatch):
    """Point the reverify stamp at a temporary home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        os.path, "expanduser", lambda sPath: sPath.replace(
            "~", str(tmp_path), 1,
        ),
    )
    return tmp_path


@pytest.mark.falsification
def test_first_reverify_pass_does_not_wait_a_full_cadence():
    """A hub that has never re-verified must not wait 6 hours to start.

    The hub's own idle shutdown is 30 minutes
    (``serverLifespan.F_HUB_IDLE_TIMEOUT_SECONDS``), so any first delay
    at or near the cadence is a first pass that never happens. The
    delay must still be non-zero so a restart storm does not become a
    remote-request storm.

    Kills: returning the full cadence instead of the startup delay in
    ``ffComputeFirstReverifyDelay`` (scheduledReverify) restores the
    unreachable-first-pass bug.
    """
    fDelay = scheduledReverify.ffComputeFirstReverifyDelay(
        _F_SIX_HOUR_CADENCE, "",
    )
    assert fDelay > 0.0
    assert fDelay < 1800.0


@pytest.mark.falsification
def test_restart_resumes_the_cadence_instead_of_restarting_it():
    """A recorded pass one hour ago leaves five hours of the cadence.

    Without this the hub would re-verify every remote on every restart
    once the startup delay elapsed, which is exactly the stampede the
    original sleep-first loop was written to avoid.

    Kills: ignoring the remaining cadence in
    ``ffComputeFirstReverifyDelay`` (returning the startup delay
    unconditionally) makes every restart re-verify within minutes.
    """
    fNowEpoch = 1785000000.0
    sOneHourAgo = scheduledReverify.datetime.fromtimestamp(
        fNowEpoch - _F_ONE_HOUR_SECONDS, scheduledReverify.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fDelay = scheduledReverify.ffComputeFirstReverifyDelay(
        _F_SIX_HOUR_CADENCE, sOneHourAgo, fNowEpoch,
    )
    assert abs(fDelay - 5.0 * _F_ONE_HOUR_SECONDS) < 1.0


def test_an_overdue_stamp_falls_back_to_the_startup_delay():
    """A pass older than the cadence is due now, modulo the startup wait."""
    fNowEpoch = 1785000000.0
    sLongAgo = scheduledReverify.datetime.fromtimestamp(
        fNowEpoch - 30.0 * _F_ONE_HOUR_SECONDS,
        scheduledReverify.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fDelay = scheduledReverify.ffComputeFirstReverifyDelay(
        _F_SIX_HOUR_CADENCE, sLongAgo, fNowEpoch,
    )
    assert 0.0 < fDelay < 1800.0


def test_a_future_stamp_is_not_treated_as_a_completed_pass():
    """An impossible timestamp is not evidence that a pass ran."""
    fNowEpoch = 1785000000.0
    sFuture = scheduledReverify.datetime.fromtimestamp(
        fNowEpoch + 10.0 * _F_ONE_HOUR_SECONDS,
        scheduledReverify.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    fDelay = scheduledReverify.ffComputeFirstReverifyDelay(
        _F_SIX_HOUR_CADENCE, sFuture, fNowEpoch,
    )
    assert 0.0 < fDelay < 1800.0


def test_reverify_stamp_round_trips_through_the_state_file(
    fixtureIsolatedHome,
):
    """The stamp is real file state, readable by the next hub process."""
    assert scheduledReverify.fsReadLastReverifyIso() == ""
    dictBefore = scheduledReverify.fdictDescribeReverifySchedule()
    assert dictBefore["bEverRan"] is False
    scheduledReverify.fnRecordLastReverifyIso("2026-07-26T09:30:00Z")
    assert (
        scheduledReverify.fsReadLastReverifyIso()
        == "2026-07-26T09:30:00Z"
    )
    dictAfter = scheduledReverify.fdictDescribeReverifySchedule()
    assert dictAfter["bEverRan"] is True
    assert dictAfter["sLastReverifyIso"] == "2026-07-26T09:30:00Z"
    assert dictAfter["fHoursCadence"] == 6.0


def test_a_corrupt_state_file_reads_as_never_ran(fixtureIsolatedHome):
    """A damaged stamp must not suppress the next pass for a cadence."""
    os.makedirs(fixtureIsolatedHome / ".vaibify", exist_ok=True)
    with open(
        scheduledReverify.fsReverifyStatePath(), "w", encoding="utf-8",
    ) as fileState:
        fileState.write("{not json at all")
    assert scheduledReverify.fsReadLastReverifyIso() == ""


def _fnDriveOneScheduledPass(dictCtx, mockRunOnce):
    """Run exactly one iteration of the loop with the delay collapsed.

    The first delay is patched to zero and the cadence set to an hour,
    so the loop performs one pass and then parks on a sleep the test
    cancels — one pass, deterministically, without sleeping for real.
    """

    async def _fnRunLoopOnce():
        eventDispatched = asyncio.Event()

        async def _fnFakeToThread(fnCallback, *args, **kwargs):
            eventDispatched.set()
            return fnCallback(*args, **kwargs)

        with patch(
            "vaibify.reproducibility.scheduledReverify.asyncio.to_thread",
            _fnFakeToThread,
        ), patch(
            "vaibify.reproducibility.scheduledReverify.fdictRunReverifyOnce",
            mockRunOnce,
        ), patch(
            "vaibify.reproducibility.scheduledReverify."
            "ffComputeFirstReverifyDelay",
            return_value=0.0,
        ):
            taskLoop = asyncio.create_task(
                scheduledReverify._fnReverifyLoop(dictCtx, 1.0),
            )
            await asyncio.wait_for(eventDispatched.wait(), timeout=2.0)
            await asyncio.sleep(0)
            taskLoop.cancel()
            try:
                await taskLoop
            except asyncio.CancelledError:
                pass

    asyncio.run(_fnRunLoopOnce())


@pytest.mark.falsification
def test_completed_pass_bumps_every_touched_container_sync_epoch(
    fixtureIsolatedHome,
):
    """A scheduled pass rewrites the cache the L2 cells read.

    The sync epoch is the dashboard's only poll-free invalidation
    signal, so a pass that does not bump it leaves every open tab
    rendering the previous verify result indefinitely.

    Kills: inverting the tuple/dict guard in
    ``_fnBumpSyncEpochForVerifiedContainers`` (scheduledReverify)
    skips every real workflow entry, leaving the epoch at zero.
    """
    dictCtx = {
        "workflows": {"container-alpha": {"sWorkflowId": "wf01"}},
        "dictSyncEpochs": {},
    }
    _fnDriveOneScheduledPass(
        dictCtx,
        MagicMock(return_value={"sNowIso": "x", "listResults": []}),
    )
    assert dictCtx["dictSyncEpochs"]["container-alpha"] == 1


@pytest.mark.falsification
def test_a_completed_pass_persists_its_stamp(fixtureIsolatedHome):
    """Only a persisted stamp lets the next hub resume the cadence.

    Without it every restart re-verifies every remote shortly after
    startup, and the dashboard can never distinguish "the background
    loop has never run" from "the loop is keeping this current".

    Kills: dropping the ``fnRecordLastReverifyIso`` call from
    ``_fnReverifyLoop`` (scheduledReverify) leaves the stamp empty
    forever, so the schedule restarts on every hub start.
    """
    assert scheduledReverify.fsReadLastReverifyIso() == ""
    _fnDriveOneScheduledPass(
        {"workflows": {}, "dictSyncEpochs": {}},
        MagicMock(return_value={"sNowIso": "x", "listResults": []}),
    )
    assert scheduledReverify.fsReadLastReverifyIso() != ""
    assert scheduledReverify.fdictDescribeReverifySchedule()[
        "bEverRan"
    ] is True


# --------- Scope version: what the verify was ANSWERING ---------


def testVerifyStampsTheScopeItRanUnder(fixtureProjectRepo):
    """A verify records which definition of "published" it used.

    The gate refuses a cache whose scope predates the current one,
    because a file the verify never looked at is missing from
    ``listDiverged`` in exactly the way a file that matched is
    missing. That refusal is only survivable if the writer stamps the
    field — a scope check whose writer never sets it is not a check,
    it is a permanent outage in which no project can reach Level 2.

    So this drives the real verify rather than asserting the constant
    against itself.
    """
    from vaibify.reproducibility import publicationScope

    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        return_value={"step01/data.csv": S_DATA_SHA},
    ):
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            fixtureProjectRepo, dictWorkflow, "github",
        )
    assert publicationScope.fbCachedScopeIsCurrent(dictStatus), (
        "the verify wrote no scope version, so the gate will refuse "
        f"its own fresh output forever: {dictStatus}"
    )


def testNeverVerifiedServiceIsNotScopeCurrent():
    """The empty default must not present as evidence of anything."""
    from vaibify.reproducibility import publicationScope

    dictEmpty = scheduledReverify._fdictEmptyServiceStatus("github")
    assert not publicationScope.fbCachedScopeIsCurrent(dictEmpty)
