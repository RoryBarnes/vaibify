"""Cancelling a host run signals only what vaibify journaled.

WHAT IS ACTUALLY AT RISK HERE
-----------------------------

Cancel is the one host-mode action that reaches out of vaibify and
changes the researcher's machine. Inside a container the sweep can
pattern-match the process table, because that table belongs to vaibify
entirely; on the host the same sweep matches the researcher's editor,
their other shell, and anything else running a file with the same name.
So the host lane may signal exactly one thing — a process group it
recorded when it started one — and only while that record's identity is
still provable.

The tests below are therefore about REFUSING as much as about killing.
The dangerous mutant is not "cancel does nothing"; it is "cancel signals
a group it cannot prove is still ours", and the only test that can catch
it is one where a live process with a recycled identity SURVIVES.

REAL PROCESSES, REAL SIGNALS, REAL JOURNAL
------------------------------------------

Nothing here mocks ``os.killpg`` except the one test whose subject is
the guard that must stop a call from being made at all. The processes
are launched, the groups are signalled, and the journal records are
written through the same API the host connection writes them with. This
repository has shipped a fatal bug under a green suite whose fixtures
never drove the real boundary; a cancel path proven only against stubs
would be exactly that shape of evidence.

BOTH DIRECTIONS, per the standing rule for every mode-aware behavior:
a host project must never reach the container sweep, and a container
project must never reach the journal terminator.
"""

import contextlib
import datetime
import os
import signal
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.config import operationJournal, registryManager
from vaibify.gui.routes import pipelineRoutes
from vaibify.host import hostCancellation


S_HOST_PROJECT = "host-cancel-project"
S_CONTAINER_PROJECT = "container-cancel-project"

DICT_WORKFLOW_WITH_A_KILLABLE_STEP = {
    "listSteps": [{
        "saDataCommands": ["python analysis.py"],
        "saPlotCommands": [],
    }],
}


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the registry so mode lookups answer from tmp_path."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )


def _fnRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory and register it in the given mode."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)


def _fprocessStartSleeperInItsOwnGroup():
    """Launch a sleeper that leads its own process group, like a run does.

    ``start_new_session=True`` is what the host launch primitive does,
    so the sleeper's pid IS its process group id — the same identity
    shape a real ``host-exec`` record carries.
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _fsJournalHostExecRecord(
    sProjectName, iPid, iProcessGroup,
    sOperationLabel="pipeline-step:A01", sInFlightIso=None,
):
    """Journal an in-flight host-exec record with the given identity.

    Written through the real journal API rather than by planting JSON,
    so a schema change that broke the host lane would break this too.
    ``sInFlightIso`` backdates the record — the only way to build a
    RECYCLED identity without waiting for the kernel to hand a pid back.

    The clock is moved with ``patch.object`` rather than the
    ``monkeypatch`` fixture on purpose: that fixture is ONE instance
    shared with every other fixture in the test, so ``undo()`` would
    also revert the registry and journal redirections — which it did,
    and the symptom was a test reading the researcher's real journal
    directory and finding it empty.
    """
    contextClock = (
        contextlib.nullcontext() if sInFlightIso is None
        else patch.object(
            operationJournal, "_fsNowIso", lambda: sInFlightIso,
        )
    )
    with contextClock:
        sOperationId = operationJournal.fsPrepareOperation(
            sProjectName, "host-exec", sOperationLabel,
        )
        operationJournal.fnPromoteOperationToInFlight(
            sProjectName, sOperationId,
            {"iHolderPid": iPid, "iHolderProcessGroup": iProcessGroup},
        )
    return sOperationId


def _fsAnHourAgoIso():
    """Return an ISO stamp an hour old — a recycled record's in-flight."""
    return (
        datetime.datetime.now() - datetime.timedelta(hours=1)
    ).isoformat()


# ── The listing the terminator acts on ───────────────────────────────


@pytest.mark.falsification
def testTheListingNamesOnlyHostExecRecords():
    """A file-write record is not a run, and must never be cancellable.

    Both kinds carry a holder pid, so a listing that filtered on
    nothing would hand the terminator the hub's OWN carrier worker and
    Cancel would signal the process serving the request.

    Kills: dropping the ``sKind`` filter.
    """
    sOperationIdWrite = operationJournal.fsPrepareOperation(
        S_HOST_PROJECT, "file-write", "/tmp/somewhere/project.json",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_HOST_PROJECT, sOperationIdWrite,
        {"iHolderPid": os.getpid(), "iHolderProcessGroup": os.getpgrp()},
    )
    sOperationIdExec = _fsJournalHostExecRecord(
        S_HOST_PROJECT, os.getpid(), os.getpgrp(),
    )
    listHolders = operationJournal.flistDescribeHostExecHolders(
        S_HOST_PROJECT,
    )
    assert [dictHolder["sOperationId"] for dictHolder in listHolders] == [
        sOperationIdExec
    ], listHolders


@pytest.mark.falsification
def testTheListingMarksARecycledIdentityUnproven():
    """The listing's proof is recycle-proof, not a bare existence check.

    ``os.kill(pid, 0)`` says a pid EXISTS; it cannot say the process
    wearing it is the one the record named. The record here is
    backdated an hour, which is what a pid handed to something new
    looks like from the journal's side.

    Kills: proving liveness with the bare existence check — the
    shortcut a developer reaches for when a start-clock read looks
    like overhead. (It also fails
    ``testARecycledIdentityIsRefusedAndItsProcessSurvives``, because
    the property has one implementation and two observers; that test
    is the one that proves the CONSEQUENCE, a live process left
    alone.)
    """
    _fsJournalHostExecRecord(
        S_HOST_PROJECT, os.getpid(), os.getpgrp(),
        sInFlightIso=_fsAnHourAgoIso(),
    )
    listHolders = operationJournal.flistDescribeHostExecHolders(
        S_HOST_PROJECT,
    )
    assert len(listHolders) == 1, listHolders
    assert listHolders[0]["bHolderProven"] is False, (
        "a pid that exists was taken as proof that the recorded "
        "process is the one still wearing it"
    )


# ── The terminator itself, against live processes ────────────────────


@pytest.mark.falsification
def testAProvenGroupIsTerminated():
    """A run whose identity still holds is stopped.

    Kills: refusing every record (never signalling), which would make
    Cancel a button that reports success and stops nothing.
    """
    processSleeper = _fprocessStartSleeperInItsOwnGroup()
    try:
        _fsJournalHostExecRecord(
            S_HOST_PROJECT, processSleeper.pid, processSleeper.pid,
        )
        dictOutcome = hostCancellation.fdictCancelJournaledHostRun(
            S_HOST_PROJECT,
        )
        assert dictOutcome["iGroupsTerminated"] == 1, dictOutcome
        assert dictOutcome["listRefused"] == []
        assert dictOutcome["listTerminated"][0]["sOperationLabel"] == (
            "pipeline-step:A01"
        )
        iReturnCode = processSleeper.wait(timeout=10)
        assert iReturnCode != 0, (
            "the sleeper exited normally, so the signal that was "
            "supposed to stop it never arrived"
        )
    finally:
        _fnKillLeftoverSleeper(processSleeper)


@pytest.mark.falsification
def testARecycledIdentityIsRefusedAndItsProcessSurvives():
    """The safety property: an unprovable record is never signalled.

    The sleeper is alive and its process group is exactly the one the
    journal names — but the record claims to have gone in flight an
    hour BEFORE that process started, which is what a recycled pid
    looks like from the journal's side. Signalling it would kill
    whatever inherited the number: on a researcher's machine, an
    unrelated program running under their own authority.

    Kills: dropping the ``bHolderProven`` check and signalling any
    record that names a group — the mutation a developer would write
    to "make cancel more reliable".
    """
    processSleeper = _fprocessStartSleeperInItsOwnGroup()
    try:
        _fsJournalHostExecRecord(
            S_HOST_PROJECT, processSleeper.pid, processSleeper.pid,
            sInFlightIso=_fsAnHourAgoIso(),
        )
        dictOutcome = hostCancellation.fdictCancelJournaledHostRun(
            S_HOST_PROJECT,
        )
        assert dictOutcome["iGroupsTerminated"] == 0, dictOutcome
        assert len(dictOutcome["listRefused"]) == 1, dictOutcome
        assert "reconcile" in dictOutcome["listRefused"][0]["sReason"]
        time.sleep(0.2)
        assert processSleeper.poll() is None, (
            "a process whose journaled identity could not be proven "
            "was signalled anyway; on a real machine that is somebody "
            "else's program"
        )
    finally:
        _fnKillLeftoverSleeper(processSleeper)


@pytest.mark.falsification
def testARunThatAlreadyFinishedIsReportedAsExitedNotRefused():
    """Nothing to signal is not the same answer as refusing to signal.

    Kills: collapsing the exited case into the refusal list, which
    would tell a researcher that vaibify declined to stop a run that
    had in fact finished — and, in the quarantine view, offer them a
    termination that can never succeed.
    """
    processSleeper = subprocess.Popen(
        [sys.executable, "-c", ""], start_new_session=True,
    )
    processSleeper.wait(timeout=10)
    _fsJournalHostExecRecord(
        S_HOST_PROJECT, processSleeper.pid, processSleeper.pid,
    )
    dictOutcome = hostCancellation.fdictCancelJournaledHostRun(
        S_HOST_PROJECT,
    )
    assert dictOutcome["listRefused"] == [], dictOutcome
    assert dictOutcome["iGroupsTerminated"] == 0
    assert len(dictOutcome["listAlreadyExited"]) == 1, dictOutcome


@pytest.mark.falsification
def testAnUnreadableJournalRaisesInsteadOfReportingNothingToCancel():
    """A journal that cannot be read is not a project with no runs.

    Kills: making :func:`flistDescribeHostExecHolders` fall back to an
    empty list the way the busy-oracle predicate falls back to True.
    Both are "fail safe" for their own caller and only one of them is
    safe here: an empty list makes Cancel answer "0 processes" about a
    machine whose running work could not be enumerated at all.
    """
    sPath = operationJournal.fsJournalPathFor(S_HOST_PROJECT)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileJournal:
        fileJournal.write("{ this is not json")
    with pytest.raises(operationJournal.OperationJournalUnreadableError):
        hostCancellation.fdictCancelJournaledHostRun(S_HOST_PROJECT)


@pytest.mark.falsification
@pytest.mark.parametrize("objProcessGroup", [0, None, -1])
def testAnUnusableProcessGroupIsNeverSignalled(
    monkeypatch, objProcessGroup,
):
    """``killpg(0, ...)`` signals the CALLER's group — the hub itself.

    A group id read back out of a journal file can be absent or zero,
    which the number never was when this code lived beside the launch
    that produced it. ``os.killpg`` is recorded rather than allowed,
    because the failure mode being guarded is the test process taking
    its own SIGKILL.

    Kills: dropping the usable-pid guard from either primitive.
    """
    listCalls = []
    monkeypatch.setattr(
        "os.killpg",
        lambda iGroup, iSignal: listCalls.append((iGroup, iSignal)),
    )
    hostCancellation.fnTerminateProcessGroup(objProcessGroup)
    assert listCalls == [], (
        f"an unusable process group {objProcessGroup!r} was signalled"
    )
    assert hostCancellation.fbProcessGroupProvedEmpty(
        objProcessGroup,
    ) is False, "no proof is not the same as proved empty"


def _fnKillLeftoverSleeper(processSleeper):
    """Make sure a test's sleeper cannot outlive the test."""
    if processSleeper.poll() is None:
        try:
            os.killpg(processSleeper.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        processSleeper.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


# ── The route, in both modes ─────────────────────────────────────────


class _RecordingKillDocker:
    """Record every container command and answer the ps/wc count."""

    def __init__(self, sCountOutput="2\n"):
        self.listCommands = []
        self._sCountOutput = sCountOutput

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        return (0, self._sCountOutput)


def _ftPostKillFor(sProjectName, monkeypatch):
    """POST the kill route for a project; return (response, docker)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    fnStandCarrierDown(monkeypatch, pipelineRoutes)
    app = FastAPI()
    recordingDocker = _RecordingKillDocker()
    dictCtx = {
        "docker": recordingDocker,
        "require": MagicMock(),
        "workflows": {sProjectName: DICT_WORKFLOW_WITH_A_KILLABLE_STEP},
        "pipelineTasks": {},
    }
    with patch(
        "vaibify.gui.routes.pipelineRoutes.fdictRequireWorkflow",
        return_value=DICT_WORKFLOW_WITH_A_KILLABLE_STEP,
    ), patch(
        "vaibify.gui.routes.pipelineRoutes._fnMarkPipelineStopped",
        new=AsyncMock(),
    ):
        pipelineRoutes._fnRegisterPipelineKill(app, dictCtx)
        client = TestClient(app)
        response = client.post(f"/api/pipeline/{sProjectName}/kill")
    return response, recordingDocker


@pytest.mark.falsification
def testKillingAHostProjectSignalsTheJournalAndNeverTheProcessTable(
    tmp_path, monkeypatch,
):
    """The host branch, end to end through the real route.

    Kills: deleting the host branch, which sends the container sweep's
    ``ps aux | grep`` at a host project — matching, and killing, every
    process on the researcher's machine whose command line contains a
    step's script name.
    """
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    processSleeper = _fprocessStartSleeperInItsOwnGroup()
    try:
        _fsJournalHostExecRecord(
            S_HOST_PROJECT, processSleeper.pid, processSleeper.pid,
        )
        response, recordingDocker = _ftPostKillFor(
            S_HOST_PROJECT, monkeypatch,
        )
        assert response.status_code == 200, response.text
        assert response.json()["iProcessesKilled"] == 1, response.text
        assert recordingDocker.listCommands == [], (
            "a host project reached the container process-table sweep: "
            f"{recordingDocker.listCommands}"
        )
        assert processSleeper.wait(timeout=10) != 0
    finally:
        _fnKillLeftoverSleeper(processSleeper)


@pytest.mark.falsification
def testKillingAContainerProjectStillSweepsItsProcessTable(
    tmp_path, monkeypatch,
):
    """The other direction: containers keep the sweep they have always had.

    Kills: making the host branch unconditional, which leaves every
    containerized project's Stop All Tasks button issuing no kill at
    all while still reporting success.
    """
    _fnRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    response, recordingDocker = _ftPostKillFor(
        S_CONTAINER_PROJECT, monkeypatch,
    )
    assert response.status_code == 200, response.text
    assert any(
        "xargs kill -9" in sCommand
        for sCommand in recordingDocker.listCommands
    ), (
        "a containerized project issued no kill: "
        f"{recordingDocker.listCommands}"
    )
    assert response.json()["listCancellationRefusals"] == []


@pytest.mark.falsification
def testARefusedCancellationReachesTheResponse(tmp_path, monkeypatch):
    """A refusal the dashboard never sees is a dashboard that lies.

    The route answers 200 with zero killed either way; the ONLY thing
    distinguishing "your machine is quiet" from "vaibify would not
    touch a run it cannot identify" is this list. Dropping it from the
    payload is a one-line change that no status code would catch.

    Kills: omitting ``listCancellationRefusals`` from the response.
    """
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    processSleeper = _fprocessStartSleeperInItsOwnGroup()
    try:
        _fsJournalHostExecRecord(
            S_HOST_PROJECT, processSleeper.pid, processSleeper.pid,
            sInFlightIso=_fsAnHourAgoIso(),
        )
        response, _ = _ftPostKillFor(S_HOST_PROJECT, monkeypatch)
        dictBody = response.json()
        assert dictBody["iProcessesKilled"] == 0
        listRefusals = dictBody["listCancellationRefusals"]
        assert len(listRefusals) == 1, dictBody
        assert listRefusals[0]["sOperationLabel"] == "pipeline-step:A01"
        assert "reconcile" in listRefusals[0]["sReason"]
    finally:
        _fnKillLeftoverSleeper(processSleeper)
