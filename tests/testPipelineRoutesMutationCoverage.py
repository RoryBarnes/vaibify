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
* single-field change detection in ``_fnUpdateShaCache``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from vaibify.gui.routes import pipelineRoutes

pytestmark = pytest.mark.falsification


# ── Hole 1: /kill route auth gate runs before any container exec ──


class TestKillRouteAuthGate:
    """An unauthorized caller is rejected before the kill exec runs."""

    def test_unauthorized_kill_rejected_before_count_exec(self):
        """Kills: Delete the dictCtx['require']() auth gate at the top of fnKillRunningTasks."""
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
            "vaibify.gui.routes.pipelineRoutes._fnMarkPipelineStopped",
            new=AsyncMock(),
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

    def _fnPostKill(self, sCountOutput):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

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
            "vaibify.gui.routes.pipelineRoutes._fnMarkPipelineStopped",
            new=AsyncMock(),
        ):
            pipelineRoutes._fnRegisterPipelineKill(app, dictCtx)
            client = TestClient(app)
            response = client.post("/api/pipeline/cid1/kill")
        return response, recordingDocker.listCommands

    def test_kill_exec_issued_when_count_positive(self):
        """Kills: Neutralize the iCountBefore>0 guard body so _fnKillMatchingProcesses is never awaited (if iCountBefore > 0 -> if False)."""
        response, listCommands = self._fnPostKill("3\n")
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

    def test_no_kill_exec_when_count_zero(self):
        """Kills: Force the iCountBefore>0 guard always-true so a kill exec runs even when no processes match (if iCountBefore > 0 -> if True)."""
        response, listCommands = self._fnPostKill("0\n")
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
            "iAICSLevel": 1,
            "iL1BlockerCount": 0,
            "iL2BlockerCount": 0,
            "iL3BlockerCount": 0,
        }

    def _fsTag(self, dictPayload):
        return pipelineRoutes._fsBuildFileStatusEtag(
            dictPayload, iSyncEpoch=1,
        )

    def test_max_mtime_by_step_change_advances_tag(self):
        """Kills: Drop the ('maxByStep', ...) entry from listSignals in _fsBuildFileStatusEtag."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, dictMaxMtimeByStep={"0": 2})
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)

    def test_aics_level_change_advances_tag(self):
        """Kills: Drop the ('aicsLevel', ...) entry from listSignals in _fsBuildFileStatusEtag."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, iAICSLevel=2)
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)

    def test_l2_blocker_count_change_advances_tag(self):
        """Kills: Drop the ('l2', ...) entry from listSignals in _fsBuildFileStatusEtag."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, iL2BlockerCount=1)
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)

    def test_l3_blocker_count_change_advances_tag(self):
        """Kills: Drop the ('l3', ...) entry from listSignals in _fsBuildFileStatusEtag."""
        dictBase = self._fdictBase()
        dictChanged = dict(dictBase, iL3BlockerCount=1)
        assert self._fsTag(dictBase) != self._fsTag(dictChanged)


# ── Hole 5: _ftSplitCachedAndChanged revalidates against mtime ───


class TestSplitCachedAndChanged:
    """A cache entry is reused only when its mtime still matches."""

    def test_stale_mtime_forces_rehash(self):
        """Kills: Remove the dictEntry.get('iMtime') == iMtime conjunct in _ftSplitCachedAndChanged."""
        dictShaCache = {"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}
        dictSeed, listNeedHash = pipelineRoutes._ftSplitCachedAndChanged(
            ["out/a.dat"], {"out/a.dat": "1800"}, dictShaCache,
        )
        assert "out/a.dat" in listNeedHash
        assert "out/a.dat" not in dictSeed

    def test_matching_mtime_reuses_cache(self):
        """Kills: Force _ftSplitCachedAndChanged to always rehash by treating a matching-mtime entry as changed (drop the cache-hit seed branch)."""
        dictShaCache = {"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}
        dictSeed, listNeedHash = pipelineRoutes._ftSplitCachedAndChanged(
            ["out/a.dat"], {"out/a.dat": "1700"}, dictShaCache,
        )
        assert "out/a.dat" in dictSeed
        assert dictSeed["out/a.dat"]["sSha256"] == "aa"
        assert "out/a.dat" not in listNeedHash


# ── Hole 6: _fnUpdateShaCache detects a single-field change ──────


class _FakeFilesFixedSha:
    """Return a fixed sha256 for every requested path."""

    def __init__(self, sSha256):
        self._sSha256 = sSha256

    def fdictHashFiles(self, listPaths):
        return {
            sPath: {
                "sSha256": self._sSha256,
                "sSymlinkSegment": None,
                "bEscapesRoot": False,
            }
            for sPath in listPaths
        }


class TestUpdateShaCacheSingleFieldChange:
    """A change in either sha or mtime alone signals persistence."""

    def test_mtime_only_change_signals_persistence(self):
        """Kills: Change the change-detection disjunction in _fnUpdateShaCache from OR to AND."""
        dictCache = {"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}
        bChanged = pipelineRoutes._fnUpdateShaCache(
            dictCache, _FakeFilesFixedSha("aa"),
            ["out/a.dat"], {"out/a.dat": "1800"},
        )
        assert bChanged is True

    def test_sha_only_change_signals_persistence(self):
        """Kills: Change the change-detection disjunction in _fnUpdateShaCache from OR to AND."""
        dictCache = {"out/a.dat": {"iMtime": 1700, "sSha256": "aa"}}
        bChanged = pipelineRoutes._fnUpdateShaCache(
            dictCache, _FakeFilesFixedSha("bb"),
            ["out/a.dat"], {"out/a.dat": "1700"},
        )
        assert bChanged is True
