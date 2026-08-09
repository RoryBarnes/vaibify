"""The host busy oracle and the three vetoes it feeds.

A host pipeline is running iff its durable-task record is live
in-process OR its journaled host-exec process group still has members
(host-mode plan §4). These tests drive REAL journal files and REAL
process identities (our own PID; a child that provably exited), then
prove each veto consults the oracle for host names — in both
directions, per the standing symmetric-pair rule.
"""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal, registryManager
from vaibify.gui import registryRoutes, serverLifespan
from vaibify.gui.commitCarrier import DurableTaskRecord
from vaibify.gui.containerOwnership import OwnerRecord
from vaibify.gui.hostBusyOracle import fbHostProjectHasLiveRun

S_HOST_PROJECT_NAME = "busy-host-proj"
S_CONTAINER_PROJECT_NAME = "busy-container-proj"
S_DOCKER_CONTAINER_ID = "cid-busy-distinct-from-name"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistryLocksAndJournal(tmp_path, monkeypatch):
    """Registry, flocks, and journals all live under tmp_path."""
    sRegistryDir = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDir, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDir, "registry.lock"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    registryManager.fnSaveRegistry({"listProjects": [
        {
            "sName": S_HOST_PROJECT_NAME,
            "sDirectory": str(tmp_path / "hostproj"),
            "sMode": "host",
        },
        {
            "sName": S_CONTAINER_PROJECT_NAME,
            "sDirectory": str(tmp_path / "containerproj"),
        },
    ]})


def _fnJournalLiveHostExec(sName):
    """Write a real host-exec record carrying THIS process's identity."""
    sOperationId = operationJournal.fsPrepareOperation(
        sName, "host-exec", "pipeline-step:A01",
    )
    operationJournal.fnPromoteOperationToInFlight(
        sName, sOperationId, {
            "iHolderPid": os.getpid(),
            "iHolderProcessGroup": os.getpgid(0),
        },
    )


def _fnJournalDeadHostExec(sName):
    """Write a host-exec record for a child that provably exited.

    The child ran in its own session, so its process group died with
    it — the probe sees a dead holder and an empty group, which is the
    settled (idle) verdict.
    """
    processChild = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processChild.wait()
    sOperationId = operationJournal.fsPrepareOperation(
        sName, "host-exec", "pipeline-step:A01",
    )
    operationJournal.fnPromoteOperationToInFlight(
        sName, sOperationId, {
            "iHolderPid": processChild.pid,
            "iHolderProcessGroup": processChild.pid,
        },
    )


def _appStateEmpty():
    """Return a bare app-state carrier with no live work registered."""
    return SimpleNamespace()


def _recordOwnerFor(sName, sResourceId):
    """Return a minimal owner record binding a name to a resource id."""
    return OwnerRecord(
        sLeaseId="busy-oracle-lease", fileHandleLock=None,
        sAgentToken="", sContainerId=sResourceId, sBrowserSessionId="",
    )


class _PoisonLeg:
    """Raises on any use — proves a veto never consulted Docker."""

    def __getattr__(self, sAttributeName):
        raise AssertionError(
            f"Docker was consulted (.{sAttributeName}) for a host name"
        )


class _ListingLeg:
    """A Docker leg listing one running container with name != id."""

    def flistGetRunningContainers(self):
        return [{
            "sName": S_CONTAINER_PROJECT_NAME,
            "sContainerId": S_DOCKER_CONTAINER_ID,
        }]


class TestJournalHalf:

    def test_a_live_recorded_group_reads_as_running(self):
        """Kills: the oracle skipping every record (blind kind filter)."""
        _fnJournalLiveHostExec(S_HOST_PROJECT_NAME)
        assert operationJournal.fbAnyHostExecHolderLive(
            S_HOST_PROJECT_NAME,
        ) is True

    def test_an_absent_journal_reads_as_idle(self):
        assert operationJournal.fbAnyHostExecHolderLive(
            S_HOST_PROJECT_NAME,
        ) is False

    def test_a_dead_holder_with_an_empty_group_reads_as_idle(self):
        """Kills: the probe verdict inverted to always-busy, which
        would deadlock every veto behind a run that already ended."""
        _fnJournalDeadHostExec(S_HOST_PROJECT_NAME)
        assert operationJournal.fbAnyHostExecHolderLive(
            S_HOST_PROJECT_NAME,
        ) is False

    def test_an_unreadable_journal_fails_safe_to_running(self):
        operationJournal.fnEnsureDirectory(
            operationJournal._S_JOURNAL_DIRECTORY,
        )
        with open(operationJournal.fsJournalPathFor(
            S_HOST_PROJECT_NAME,
        ), "w") as fileHandle:
            fileHandle.write("not json at all")
        assert operationJournal.fbAnyHostExecHolderLive(
            S_HOST_PROJECT_NAME,
        ) is True


class TestComposedOracle:

    def test_a_live_durable_task_reads_as_running(self):
        """The in-process half: a registered durable task is live work
        even with no journal record on disk."""
        appState = _appStateEmpty()
        appState.dictDurableTaskRecords = {
            S_HOST_PROJECT_NAME: DurableTaskRecord(
                sTaskId="task-1", sName=S_HOST_PROJECT_NAME,
                sContainerId=S_HOST_PROJECT_NAME, iOwnerGeneration=1,
                taskAsync=None, admission=None,
            ),
        }
        assert fbHostProjectHasLiveRun(
            appState, S_HOST_PROJECT_NAME,
        ) is True

    def test_idle_without_task_or_journal(self):
        assert fbHostProjectHasLiveRun(
            _appStateEmpty(), S_HOST_PROJECT_NAME,
        ) is False


class TestClaimTakeOverVeto:

    def test_host_run_vetoes_a_take_over_without_docker(self):
        """Kills: dropping the host branch — the Docker walk finds no
        such container and reports a live host run as idle, so a
        foreign claim would evict the owner mid-run."""
        _fnJournalLiveHostExec(S_HOST_PROJECT_NAME)
        assert registryRoutes._fbNameHasRunningPipeline(
            {"docker": _PoisonLeg()}, _appStateEmpty(),
            S_HOST_PROJECT_NAME,
        ) is True

    def test_idle_host_project_permits_the_take_over(self):
        assert registryRoutes._fbNameHasRunningPipeline(
            {"docker": _PoisonLeg()}, _appStateEmpty(),
            S_HOST_PROJECT_NAME,
        ) is False

    def test_container_run_still_vetoes_through_docker(self, monkeypatch):
        """Kills: the veto stuck at host — a container name would be
        asked for a journal it never writes and read as idle."""
        monkeypatch.setattr(
            "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
            lambda dictCtx, sContainerId: (
                sContainerId == S_DOCKER_CONTAINER_ID
            ),
        )
        assert registryRoutes._fbNameHasRunningPipeline(
            {"docker": _ListingLeg()}, _appStateEmpty(),
            S_CONTAINER_PROJECT_NAME,
        ) is True


class TestReaperVeto:

    def test_host_run_vetoes_the_reap_without_docker(self):
        """Kills: dropping the host branch in the ownership reaper's
        busy probe."""
        _fnJournalLiveHostExec(S_HOST_PROJECT_NAME)
        app = SimpleNamespace(state=_appStateEmpty())
        assert serverLifespan._fbOwnedNamePipelineRunning(
            app, {"docker": _PoisonLeg()}, S_HOST_PROJECT_NAME,
        ) is True

    def test_idle_host_project_permits_the_reap(self):
        app = SimpleNamespace(state=_appStateEmpty())
        assert serverLifespan._fbOwnedNamePipelineRunning(
            app, {"docker": _PoisonLeg()}, S_HOST_PROJECT_NAME,
        ) is False

    def test_container_run_still_vetoes_through_docker(self, monkeypatch):
        monkeypatch.setattr(
            "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
            lambda dictCtx, sContainerId: (
                sContainerId == S_DOCKER_CONTAINER_ID
            ),
        )
        app = SimpleNamespace(state=_appStateEmpty())
        assert serverLifespan._fbOwnedNamePipelineRunning(
            app, {"docker": _ListingLeg()}, S_CONTAINER_PROJECT_NAME,
        ) is True


class TestIdleWatchdogVeto:

    def test_host_run_vetoes_self_exit_without_docker(self):
        """Kills: dropping the host check from the watchdog — a hub
        whose only live work is a host run would self-SIGTERM."""
        _fnJournalLiveHostExec(S_HOST_PROJECT_NAME)
        appState = _appStateEmpty()
        appState.iHubPort = 8642
        appState.dictContainerOwners = {
            S_HOST_PROJECT_NAME: _recordOwnerFor(
                S_HOST_PROJECT_NAME, S_HOST_PROJECT_NAME,
            ),
        }
        app = SimpleNamespace(state=appState)
        assert serverLifespan._fbAnyHeldContainerBusy(
            app, {"docker": _PoisonLeg()},
        ) is True

    def test_idle_host_only_hub_may_exit_with_no_daemon_at_all(self):
        """A hub holding ONLY idle host projects never asks Docker, so
        a daemon-less machine is not read as perpetually busy.

        Kills: leaving host names in the Docker id-resolution walk,
        whose fail-safe would turn every daemon error into busy."""
        appState = _appStateEmpty()
        appState.iHubPort = 8642
        appState.dictContainerOwners = {
            S_HOST_PROJECT_NAME: _recordOwnerFor(
                S_HOST_PROJECT_NAME, S_HOST_PROJECT_NAME,
            ),
        }
        app = SimpleNamespace(state=appState)
        assert serverLifespan._fbAnyHeldContainerBusy(
            app, {"docker": _PoisonLeg()},
        ) is False

    def test_container_run_still_vetoes_self_exit(self, monkeypatch):
        monkeypatch.setattr(
            "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
            lambda dictCtx, sContainerId: (
                sContainerId == S_DOCKER_CONTAINER_ID
            ),
        )
        appState = _appStateEmpty()
        appState.iHubPort = 8642
        appState.dictContainerOwners = {
            S_CONTAINER_PROJECT_NAME: _recordOwnerFor(
                S_CONTAINER_PROJECT_NAME, S_DOCKER_CONTAINER_ID,
            ),
        }
        app = SimpleNamespace(state=appState)
        assert serverLifespan._fbAnyHeldContainerBusy(
            app, {"docker": _ListingLeg()},
        ) is True

    def test_idle_container_hub_may_exit(self, monkeypatch):
        monkeypatch.setattr(
            "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
            lambda dictCtx, sContainerId: False,
        )
        appState = _appStateEmpty()
        appState.iHubPort = 8642
        appState.dictContainerOwners = {
            S_CONTAINER_PROJECT_NAME: _recordOwnerFor(
                S_CONTAINER_PROJECT_NAME, S_DOCKER_CONTAINER_ID,
            ),
        }
        app = SimpleNamespace(state=appState)
        assert serverLifespan._fbAnyHeldContainerBusy(
            app, {"docker": _ListingLeg()},
        ) is False
