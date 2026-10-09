"""Mutation-coverage tests for vaibify.gui.routes.pipelineRoutes.

Each test closes a specific coverage hole surfaced by mutation
testing: it asserts the guarantee that the surviving mutant violated,
so it passes on the unmutated code and fails when the mutation is
applied. The holes covered here are:

* the ``/kill`` route auth gate (require fires before any exec),
* ``/kill`` actually issuing the kill exec when processes match,
* the pipeline-WS reject-before-serve gate (rejected => closed,
  authorized => served), distinguished the way the terminal-route
  tests are, not merely ``pytest.raises``,
* every non-L1 signal in the file-status ETag,
* mtime revalidation in ``_ftSplitCachedAndChanged``,
* single-field change detection in ``_fbUpdateShaCache``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui.routes import pipelineRoutes

pytestmark = pytest.mark.falsification


# ── Hole 1: /kill route auth gate runs before any container exec ──


class TestKillRouteAuthGate:
    """An unauthorized caller is rejected before the kill exec runs."""

    def test_unauthorized_kill_rejected_before_count_exec(self):
        """Kills: Delete the dictCtx['require']() auth gate at the top of fdictKillRunningTasks."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "3\n")
        dictWorkflow = {
            "listSteps": [{
                "saDataCommands": ["python myScript.py"],
                "saPlotCommands": [],
            }],
        }
        dictCtx = {
            "docker": mockDocker,
            "require": MagicMock(
                side_effect=HTTPException(status_code=401),
            ),
            "workflows": {"cid1": dictWorkflow},
            "pipelineTasks": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes._fiMarkPipelineStopped",
            new=AsyncMock(return_value=0),
        ):
            pipelineRoutes._fnRegisterPipelineKill(app, dictCtx)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/api/pipeline/cid1/kill")
        # The auth gate must reject the request ...
        assert response.status_code == 401
        # ... and no process-count / kill exec may have run.
        mockDocker.ftResultExecuteCommand.assert_not_called()
        dictCtx["require"].assert_called_once()


# ── Hole 2: /kill actually issues the kill exec when count > 0 ────


class _RecordingKillDocker:
    """Record every command and return a fixed ps/wc count string."""

    def __init__(self, sCountOutput):
        self.listCommands = []
        self._sCountOutput = sCountOutput

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return (0, self._sCountOutput)


class TestKillRouteActuallyKills:
    """When processes match, a real ``xargs kill -9`` is issued."""

    def _fnPostKill(self, sCountOutput, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # The sweep is carried now, and this app has no owner record,
        # so the real carrier would answer 403 before any exec ran.
        # These tests are about WHICH commands the sweep issues, not
        # about the admission -- that lives in
        # tests/testCarrierMigratedRoutes.py.
        fnStandCarrierDown(monkeypatch, pipelineRoutes)
        app = FastAPI()
        recordingDocker = _RecordingKillDocker(sCountOutput)
        dictWorkflow = {
            "listSteps": [{
                "saDataCommands": ["python myScript.py"],
                "saPlotCommands": [],
            }],
        }
        dictCtx = {
            "docker": recordingDocker,
            "require": MagicMock(),
            "workflows": {"cid1": dictWorkflow},
            "pipelineTasks": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes._fiMarkPipelineStopped",
            new=AsyncMock(return_value=0),
        ):
            pipelineRoutes._fnRegisterPipelineKill(app, dictCtx)
            client = TestClient(app)
            response = client.post("/api/pipeline/cid1/kill")
        return response, recordingDocker.listCommands

    def test_kill_exec_issued_when_count_positive(self, monkeypatch):
        """Kills: Neutralize the iCountBefore>0 guard body so _fnKillMatchingProcesses is never awaited (if iCountBefore > 0 -> if False)."""
        response, listCommands = self._fnPostKill("3\n", monkeypatch)
        assert response.status_code == 200
        assert response.json()["iProcessesKilled"] == 3
        listKillCommands = [
            sCommand for _, sCommand in listCommands
            if "xargs kill -9" in sCommand
        ]
        assert listKillCommands, (
            "a kill exec must run when matching processes exist"
        )
        # The kill targets the bracketed grep pattern for the script.
        assert any(
            "[m]yScript.py" in sCommand
            for sCommand in listKillCommands
        )

    def test_no_kill_exec_when_count_zero(self, monkeypatch):
        """Kills: Force the iCountBefore>0 guard always-true so a kill exec runs even when no processes match (if iCountBefore > 0 -> if True)."""
        response, listCommands = self._fnPostKill("0\n", monkeypatch)
        assert response.status_code == 200
        assert response.json()["iProcessesKilled"] == 0
        assert not any(
            "xargs kill -9" in sCommand for _, sCommand in listCommands
        )


# ── Hole 3: pipeline-WS reject-before-serve gate ─────────────────


def _fnCaptureWsHandler(dictCtx):
    """Register the pipeline-WS route and return its handler."""
    listRegistered = []

    def fnCaptureRoute(sPath):
        def fnDecorator(fnHandler):
            listRegistered.append(fnHandler)
            return fnHandler
        return fnDecorator

    app = MagicMock()
    app.websocket = fnCaptureRoute
    pipelineRoutes._fnRegisterPipelineWs(app, dictCtx)
    return listRegistered[0]


class TestPipelineWsRejectBeforeServe:
    """Rejected sessions are closed; the serve path never runs."""

    @pytest.mark.parametrize("iRejectCode", [4003, 4401, 4403])
    @pytest.mark.asyncio
    async def test_rejected_session_closed_not_served(self, iRejectCode):
        """Kills: Invert the rejection branch in _fnRegisterPipelineWs: if iRejectCode -> if not iRejectCode."""
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
            "dictContainerOwners": {},
        }
        fnHandler = _fnCaptureWsHandler(dictCtx)
        mockWs = AsyncMock()
        with patch.object(
            pipelineRoutes, "fsContainerNameForId", return_value="name",
        ), patch.object(
            pipelineRoutes, "fiContainerSessionRejectionCode",
            return_value=iRejectCode,
        ), patch.object(
            pipelineRoutes, "fnServeUnderLiveConnectionCounters",
            new_callable=AsyncMock,
        ) as mockServe:
            await fnHandler(mockWs, "cid1")
        mockWs.close.assert_awaited_once_with(code=iRejectCode)
        mockServe.assert_not_awaited()
        dictCtx["require"].assert_not_called()

    @pytest.mark.asyncio
    async def test_authorized_session_served_not_closed(self):
        """Kills: Invert the rejection branch in _fnRegisterPipelineWs: if iRejectCode -> if not iRejectCode."""
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
            "dictContainerOwners": {},
        }
        fnHandler = _fnCaptureWsHandler(dictCtx)
        mockWs = AsyncMock()
        with patch.object(
            pipelineRoutes, "fsContainerNameForId", return_value="name",
        ), patch.object(
            pipelineRoutes, "fiContainerSessionRejectionCode",
            return_value=0,
        ), patch.object(
            pipelineRoutes, "fnServeUnderLiveConnectionCounters",
            new_callable=AsyncMock,
        ) as mockServe:
            await fnHandler(mockWs, "cid1")
        mockServe.assert_awaited_once()
        mockWs.close.assert_not_called()
        dictCtx["require"].assert_called_once()


# ── Hole 4: every non-L1 signal participates in the ETag ─────────


class TestFileStatusEtagSignals:
    """Each verification-state signal advances the ETag stamp."""

    def _fdictBase(self):
        return {
            "dictModTimes": {"a/b": "1"},
            "dictMaxMtimeByStep": {"0": 1},
            "iProofLevel": 1,
            "iL1BlockerCount": 0,
            "iL2BlockerCount": 0,
            "iL3BlockerCount": 0,
        }

    def _fsTag(self, dictPayload):
        return pipelineRoutes._fsBuildFileStatusEtag(
            dictPayload, iSyncEpoch=1,
        )

    def test_max_mtime_by_step_change_advances_tag(self):
        """Kills: Add "dictMaxMtimeByStep" to _SET_ETAG_VOLATILE_KEYS in pipelineRoutes.py."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, dictMaxMtimeByStep={"0": 2})
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)

    def test_proof_level_change_advances_tag(self):
        """Kills: Add "iProofLevel" to _SET_ETAG_VOLATILE_KEYS in pipelineRoutes.py."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, iProofLevel=2)
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)

    def test_l2_blocker_count_change_advances_tag(self):
        """Kills: Add "iL2BlockerCount" to _SET_ETAG_VOLATILE_KEYS in pipelineRoutes.py."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, iL2BlockerCount=1)
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)

    def test_l3_blocker_count_change_advances_tag(self):
        """Kills: Add "iL3BlockerCount" to _SET_ETAG_VOLATILE_KEYS in pipelineRoutes.py."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, iL3BlockerCount=1)
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)


# ── Hole 5: the cache offers an entry only under its full stat key ───


class TestCachedEntriesForSnapshot:
    """An entry is offered only with the four-integer key it was hashed under."""

    def test_an_entry_without_a_stat_key_is_never_offered(self):
        """Kills: Drop the listStatKey conjunct in _fdictCachedEntriesForSnapshot."""
        # Both digests are present so that only the missing key can
        # decline the entry; with one absent, the digest check declines
        # it first and this test kills nothing.
        dictShaCache = {"out/a.dat": {
            "iMtime": 1700, "sSha256": "aa", "sBlobSha": "bb"}}
        assert pipelineRoutes._fdictCachedEntriesForSnapshot(
            dictShaCache) == {}

    def test_an_entry_under_its_full_key_is_offered_with_its_hash(self):
        """Kills: Offer no entry at all from _fdictCachedEntriesForSnapshot (always rehash)."""
        dictEntry = {
            "listStatKey": [1, 2, 3, 4], "sSha256": "aa", "sBlobSha": "bb"}
        dictOffered = pipelineRoutes._fdictCachedEntriesForSnapshot(
            {"out/a.dat": dictEntry})
        assert dictOffered == {"out/a.dat": dictEntry}

    def test_an_entry_missing_either_digest_is_never_offered(self):
        """Kills: Offer an entry that cannot supply both digests.

        A cache written before the blob digest existed holds a SHA-256
        only; offering it would answer a hit with a digest of None,
        which the marker lane reads as unknown on every poll.
        """
        dictShaOnly = {"listStatKey": [1, 2, 3, 4], "sSha256": "aa"}
        dictBlobOnly = {"listStatKey": [1, 2, 3, 4], "sBlobSha": "bb"}
        assert pipelineRoutes._fdictCachedEntriesForSnapshot(
            {"out/a.dat": dictShaOnly, "out/b.dat": dictBlobOnly}) == {}


# ── Hole 6: _fbUpdateShaCache detects a single-field change ──────


class _FakeFilesAnswering:
    """Answer one hash entry per path, as the snapshot's own program does."""

    def __init__(self, dictEntries):
        self._dictEntries = dictEntries

    def fdictAllHashEntries(self):
        return dict(self._dictEntries)


def _fdictSteadyEntry(sSha256, listKey, sBlobSha="bb"):
    return {
        "sSha256": sSha256, "sBlobSha": sBlobSha, "listStatKey": listKey,
        "iHashedAtNs": max(listKey[0], listKey[1]) + 5 * 10 ** 9,
        "sSymlinkSegment": None, "bEscapesRoot": False,
    }


class TestUpdateShaCacheSingleFieldChange:
    """A change in either sha or stat key alone signals persistence."""

    def test_key_only_change_signals_persistence(self):
        """Kills: Change the change-detection disjunction in _fbUpdateShaCache from OR to AND."""
        dictCache = {"out/a.dat": {
            "listStatKey": [1, 2, 3, 4], "sSha256": "aa",
            "sBlobSha": "bb"}}
        bChanged = pipelineRoutes._fbUpdateShaCache(
            dictCache, _FakeFilesAnswering({
                "out/a.dat": _fdictSteadyEntry("aa", [9, 2, 3, 4])}),
        )
        assert bChanged is True

    def test_sha_only_change_signals_persistence(self):
        """Kills: Change the change-detection disjunction in _fbUpdateShaCache from OR to AND."""
        dictCache = {"out/a.dat": {
            "listStatKey": [1, 2, 3, 4], "sSha256": "aa",
            "sBlobSha": "bb"}}
        bChanged = pipelineRoutes._fbUpdateShaCache(
            dictCache, _FakeFilesAnswering({
                "out/a.dat": _fdictSteadyEntry("bb", [1, 2, 3, 4])}),
        )
        assert bChanged is True

    def test_blob_digest_only_change_signals_persistence(self):
        """Kills: Compare only the SHA-256 and key in _fbUpdateShaCache."""
        dictCache = {"out/a.dat": {
            "listStatKey": [1, 2, 3, 4], "sSha256": "aa",
            "sBlobSha": "bb"}}
        bChanged = pipelineRoutes._fbUpdateShaCache(
            dictCache, _FakeFilesAnswering({
                "out/a.dat": _fdictSteadyEntry(
                    "aa", [1, 2, 3, 4], sBlobSha="cc")}),
        )
        assert bChanged is True
        assert dictCache["out/a.dat"]["sBlobSha"] == "cc"

    def test_an_entry_missing_the_blob_digest_is_not_cached(self):
        """Kills: Cache a half-entry that could never be offered again."""
        dictCache = {}
        dictHalf = _fdictSteadyEntry("aa", [1, 2, 3, 4])
        dictHalf["sBlobSha"] = None
        bChanged = pipelineRoutes._fbUpdateShaCache(
            dictCache, _FakeFilesAnswering({"out/a.dat": dictHalf}),
        )
        assert bChanged is False
        assert dictCache == {}
