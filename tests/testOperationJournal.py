"""Tests for the write-ahead operation journal (design §8, sub-step 3a).

Covers the two-phase write API, the bounded fail-closed schema, the
probe catalog's structural completeness, the two-tier resolution with
real (spawned and reaped) holder processes, and the choke-point wiring:
``fnAcquireContainerLock``, the stale-lock reaper, the hub startup
hook, the claim arbitration, and the registry listing annotation.

The falsification (kill-confirmed) halves of cases 27, 36, 37, 42 and
45 live in ``testOperationJournalMutationCoverage.py``; case 39 lives
in ``testBindMountValidator.py``.
"""

import asyncio
import json
import os
import stat
import subprocess
import sys

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.config.operationJournal import (
    DICT_OPERATION_PROBE_CATALOG,
    OperationJournalRecordError,
    OperationJournalUnreadableError,
    S_OPERATION_STATE_CANCEL_REQUESTED,
    S_OPERATION_STATE_IN_FLIGHT,
    S_OPERATION_STATE_NEEDS_RECONCILIATION,
    S_OPERATION_STATE_PREPARED,
    S_RESOLUTION_BUSY,
    S_RESOLUTION_QUARANTINED,
    S_RESOLUTION_SETTLED,
    fdictReadJournalOutcome,
    fdictResolveContainerJournal,
    flistJournaledContainerNames,
    fnMarkOperationNeedsReconciliation,
    fnPromoteOperationToInFlight,
    fnRequestOperationCancel,
    fnSettleOperation,
    fsJournalPathFor,
    fsPrepareOperation,
)

S_PROJECT = "demo"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirs(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/journal and ~/.vaibify/locks to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    return tmp_path


class FakeDockerConnectionExecSettled:
    """A Docker connection whose recorded execs have all exited."""

    def fdictInspectExec(self, sDockerExecId):
        del sDockerExecId
        return {"Running": False}


class FakeDockerConnectionWithoutVerifiers:
    """A connection object supporting none of the probe verifiers."""


def _fiSpawnExitedProcessInOwnGroup():
    """Spawn a process as its own group leader and reap it; return its pid."""
    processHolder = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processHolder.wait()
    return processHolder.pid


def _fnWriteRawJournalBytes(sContainerName, byteContent):
    """Write raw bytes straight to a container's journal path."""
    sPath = fsJournalPathFor(sContainerName)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(byteContent)


# -------------------------------------------------------------------
# Two-phase write API
# -------------------------------------------------------------------


def test_prepare_persists_before_launch_and_promote_adds_identity():
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    dictOutcome = fdictReadJournalOutcome(S_PROJECT)
    assert dictOutcome["sReadState"] == "valid"
    dictRecord = dictOutcome["dictOperations"][sOperationId]
    assert dictRecord["sState"] == S_OPERATION_STATE_PREPARED
    assert dictRecord["sKind"] == "helper"
    assert dictRecord["sTarget"] == "runStepBatch"
    assert "sPreparedIso" in dictRecord
    assert "iHolderPid" not in dictRecord
    iMode = stat.S_IMODE(os.stat(fsJournalPathFor(S_PROJECT)).st_mode)
    assert iMode == 0o600
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": 12345, "iHolderProcessGroup": 12345},
    )
    dictRecord = fdictReadJournalOutcome(S_PROJECT)["dictOperations"][
        sOperationId
    ]
    assert dictRecord["sState"] == S_OPERATION_STATE_IN_FLIGHT
    assert dictRecord["iHolderPid"] == 12345
    assert "sInFlightIso" in dictRecord


def test_prepare_refuses_unknown_kind_and_unbounded_target():
    with pytest.raises(OperationJournalRecordError):
        fsPrepareOperation(S_PROJECT, "teleport", "somewhere")
    with pytest.raises(OperationJournalRecordError):
        fsPrepareOperation(S_PROJECT, "helper", "")
    with pytest.raises(OperationJournalRecordError):
        fsPrepareOperation(S_PROJECT, "helper", "x" * 513)
    assert fdictReadJournalOutcome(S_PROJECT)["sReadState"] == "absent"


def test_promote_refuses_missing_record_empty_and_foreign_identity():
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    with pytest.raises(OperationJournalRecordError):
        fnPromoteOperationToInFlight(S_PROJECT, "no-such-id", {"iHolderPid": 1})
    with pytest.raises(OperationJournalRecordError):
        fnPromoteOperationToInFlight(S_PROJECT, sOperationId, {})
    with pytest.raises(OperationJournalRecordError):
        fnPromoteOperationToInFlight(
            S_PROJECT, sOperationId, {"sCommandLine": "secret --token=abc"},
        )
    fnPromoteOperationToInFlight(S_PROJECT, sOperationId, {"iHolderPid": 7})
    with pytest.raises(OperationJournalRecordError):
        fnPromoteOperationToInFlight(S_PROJECT, sOperationId, {"iHolderPid": 7})


def test_cancel_and_poison_transitions():
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    with pytest.raises(OperationJournalRecordError):
        fnRequestOperationCancel(S_PROJECT, sOperationId)
    fnPromoteOperationToInFlight(S_PROJECT, sOperationId, {"iHolderPid": 7})
    fnRequestOperationCancel(S_PROJECT, sOperationId)
    dictRecord = fdictReadJournalOutcome(S_PROJECT)["dictOperations"][
        sOperationId
    ]
    assert dictRecord["sState"] == S_OPERATION_STATE_CANCEL_REQUESTED
    fnMarkOperationNeedsReconciliation(S_PROJECT, sOperationId, "test poison")
    dictRecord = fdictReadJournalOutcome(S_PROJECT)["dictOperations"][
        sOperationId
    ]
    assert dictRecord["sState"] == S_OPERATION_STATE_NEEDS_RECONCILIATION
    assert dictRecord["sNote"] == "test poison"


def test_settle_removes_record_but_refuses_a_poisoned_one():
    sFirstId = fsPrepareOperation(S_PROJECT, "helper", "firstOperation")
    sSecondId = fsPrepareOperation(S_PROJECT, "helper", "secondOperation")
    fnMarkOperationNeedsReconciliation(S_PROJECT, sSecondId)
    with pytest.raises(OperationJournalRecordError):
        fnSettleOperation(S_PROJECT, sSecondId)
    fnSettleOperation(S_PROJECT, sFirstId)
    dictOperations = fdictReadJournalOutcome(S_PROJECT)["dictOperations"]
    assert set(dictOperations) == {sSecondId}


def test_settling_the_last_record_unlinks_the_journal_file():
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    assert os.path.exists(fsJournalPathFor(S_PROJECT))
    fnSettleOperation(S_PROJECT, sOperationId)
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))
    assert flistJournaledContainerNames() == []


# -------------------------------------------------------------------
# Probe catalog: structural completeness
# -------------------------------------------------------------------


def test_every_journaled_kind_has_a_probe_and_a_cleanup_handler():
    """Structural (design §8): the catalog covers every accepted kind."""
    assert set(DICT_OPERATION_PROBE_CATALOG) == {
        "start", "exec", "helper", "file-write", "terminal",
    }
    for sKind, dictHandler in DICT_OPERATION_PROBE_CATALOG.items():
        assert callable(dictHandler["fdictProbe"]), sKind
        assert callable(dictHandler["fnCleanupAfterSettledProbe"]), sKind
        sOperationId = fsPrepareOperation(S_PROJECT, sKind, "structuralCheck")
        fnSettleOperation(S_PROJECT, sOperationId)


# -------------------------------------------------------------------
# Hardened read: fail closed
# -------------------------------------------------------------------


def test_read_fails_closed_on_empty_object_foreign_keys_and_symlink(tmp_path):
    _fnWriteRawJournalBytes("emptyobject", b"{}")
    assert fdictReadJournalOutcome("emptyobject")["sReadState"] == "malformed"
    assert (
        fdictResolveContainerJournal("emptyobject")["sResolution"]
        == S_RESOLUTION_QUARANTINED
    )
    _fnWriteRawJournalBytes("foreigntop", json.dumps({
        "iSchemaVersion": 1, "dictOperations": {}, "sInjected": "x",
    }).encode("utf-8"))
    assert fdictReadJournalOutcome("foreigntop")["sReadState"] == "malformed"
    _fnWriteRawJournalBytes("foreignrecord", json.dumps({
        "iSchemaVersion": 1,
        "dictOperations": {"op1": {
            "sState": "PREPARED", "sKind": "helper", "sTarget": "t",
            "sPreparedIso": "2026-01-01T00:00:00",
            "sEnvironmentDump": "leaked",
        }},
    }).encode("utf-8"))
    assert (
        fdictReadJournalOutcome("foreignrecord")["sReadState"] == "malformed"
    )
    sRealFile = tmp_path / "elsewhere.json"
    sRealFile.write_text("{}")
    os.makedirs(operationJournal._S_JOURNAL_DIRECTORY, exist_ok=True)
    os.symlink(str(sRealFile), fsJournalPathFor("symlinked"))
    assert fdictReadJournalOutcome("symlinked")["sReadState"] == "malformed"


def test_listing_ignores_partial_write_artifacts():
    fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    sArtifactPath = f"{fsJournalPathFor(S_PROJECT)}.partialWrite.999"
    with open(sArtifactPath, "wb") as fileHandle:
        fileHandle.write(b"torn")
    assert flistJournaledContainerNames() == [S_PROJECT]


# -------------------------------------------------------------------
# Two-tier resolution: verifier availability
# -------------------------------------------------------------------


def test_unavailable_verifier_quarantines_transiently_then_settles():
    sOperationId = fsPrepareOperation(S_PROJECT, "exec", "listStepOutputs")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "feedface"},
    )
    dictResolution = fdictResolveContainerJournal(S_PROJECT, None)
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED
    dictRecord = fdictReadJournalOutcome(S_PROJECT)["dictOperations"][
        sOperationId
    ]
    assert dictRecord["sState"] == S_OPERATION_STATE_IN_FLIGHT
    dictResolution = fdictResolveContainerJournal(
        S_PROJECT, FakeDockerConnectionExecSettled(),
    )
    assert dictResolution["sResolution"] == S_RESOLUTION_SETTLED
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))


def test_unsupported_verifier_poisons_the_record_permanently():
    sOperationId = fsPrepareOperation(S_PROJECT, "exec", "listStepOutputs")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "feedface"},
    )
    dictResolution = fdictResolveContainerJournal(
        S_PROJECT, FakeDockerConnectionWithoutVerifiers(),
    )
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED
    dictRecord = fdictReadJournalOutcome(S_PROJECT)["dictOperations"][
        sOperationId
    ]
    assert dictRecord["sState"] == S_OPERATION_STATE_NEEDS_RECONCILIATION
    dictResolution = fdictResolveContainerJournal(
        S_PROJECT, FakeDockerConnectionExecSettled(),
    )
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED


# -------------------------------------------------------------------
# Choke points: reaper, startup hook, claim, registry listing
# -------------------------------------------------------------------


def test_reaper_auto_probe_clears_settled_and_skips_a_held_flock():
    fileHandleLock = containerLock.fnAcquireContainerLock(S_PROJECT, 8050)
    iDeadPid = _fiSpawnExitedProcessInOwnGroup()
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": iDeadPid, "iHolderProcessGroup": iDeadPid},
    )
    containerLock.fnReapStaleContainerLocks()
    assert os.path.exists(fsJournalPathFor(S_PROJECT)), (
        "a held flock means a live owner manages its own journal"
    )
    containerLock.fnReleaseContainerLock(fileHandleLock)
    containerLock.fnReapStaleContainerLocks()
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))


def test_hub_startup_hook_runs_the_journal_auto_probe():
    from vaibify.gui import appFactory

    class _FakeAppState:
        def __init__(self):
            self.listLifespanStartup = []

    class _FakeApp:
        def __init__(self):
            self.state = _FakeAppState()

    iDeadPid = _fiSpawnExitedProcessInOwnGroup()
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": iDeadPid, "iHolderProcessGroup": iDeadPid},
    )
    appFake = _FakeApp()
    appFactory._fnRegisterHubStartupReapStaleClaims(appFake)
    assert len(appFake.state.listLifespanStartup) == 1
    asyncio.run(appFake.state.listLifespanStartup[0](appFake))
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))


def test_claim_refuses_a_quarantined_container_with_the_reason():
    from vaibify.gui.containerOwnership import ftClaim
    _fnWriteRawJournalBytes(S_PROJECT, b"this is not a journal")
    iStatusCode, dictPayload = ftClaim({}, S_PROJECT, "", 8050)
    assert iStatusCode == 409
    assert dictPayload["bClaimed"] is False
    assert dictPayload["bQuarantined"] is True
    assert "quarantined" in dictPayload["sMessage"]


def test_registry_listing_annotates_quarantine_never_available():
    from vaibify.gui.registryRoutes import _fnAnnotateJournalState
    _fnWriteRawJournalBytes(S_PROJECT, b"this is not a journal")
    dictContainer = {"sName": S_PROJECT, "bLocked": False}
    _fnAnnotateJournalState(dictContainer, S_PROJECT, {"docker": None})
    assert dictContainer["sJournalState"] == S_RESOLUTION_QUARANTINED
    assert dictContainer["bQuarantined"] is True
    assert dictContainer["bLocked"] is True
    dictSettled = {"sName": "pristine", "bLocked": False}
    _fnAnnotateJournalState(dictSettled, "pristine", {"docker": None})
    assert dictSettled["sJournalState"] == S_RESOLUTION_SETTLED
    assert dictSettled["bQuarantined"] is False
    assert dictSettled["bLocked"] is False


def test_busy_resolution_reaches_the_claim_as_locked_not_quarantined():
    processLive = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
        fnPromoteOperationToInFlight(
            S_PROJECT, sOperationId,
            {
                "iHolderPid": processLive.pid,
                "iHolderProcessGroup": processLive.pid,
            },
        )
        dictResolution = fdictResolveContainerJournal(S_PROJECT)
        assert dictResolution["sResolution"] == S_RESOLUTION_BUSY
        with pytest.raises(containerLock.ContainerBusyOperationError):
            containerLock.fnAcquireContainerLock(S_PROJECT, 8050)
    finally:
        processLive.terminate()
        processLive.wait()
