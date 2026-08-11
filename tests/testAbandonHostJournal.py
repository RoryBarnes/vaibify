"""Giving up on a proof, attributably — and never by accident.

A container's quarantine has an exit that proves something: stop the
container, and the writer a malformed marker describes is demonstrably
gone. A host project has neither the container nor the proof, so its
exit is an assertion by a human, and the only thing vaibify can add is
attribution: who, which project, which exact marker bytes, when.

The two failure modes worth testing are opposite and both quiet:

* a containerized project routed into the ASSERTING exit, which throws
  away a proof it could have made;
* a host project routed into the PROVING exit, whose entire containment
  is stopping a container it does not have — it would find nothing,
  prove nothing by finding it, and clear the marker anyway.

And the ordering. The audit is appended and fsynced BEFORE the marker
is unlinked, so a crash between them re-runs to completion. Written the
other way round, the one state nobody can recover from — a marker
abandoned with no record of who abandoned it — becomes reachable by a
power cut.
"""

import json
import os
from unittest.mock import patch

import pytest

from vaibify.config import (
    abandonmentAudit,
    containerLock,
    operationJournal,
    reconciliation,
    registryManager,
)


S_HOST_PROJECT = "abandon-host-project"
S_CONTAINER_PROJECT = "abandon-container-project"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Redirect the registry and the container locks into tmp_path.

    The journal directory is already redirected for every test by the
    suite-wide fixture, and the audit derives its path from the
    journal's, so it follows without a second patch — which is the
    point of deriving it there.
    """
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fsRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory and register it in the given mode."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)
    return sProjectDirectory


def _fsPlantMalformedMarker(sProjectName):
    """Write an unparseable journal marker; return its sha256."""
    sPath = operationJournal.fsJournalPathFor(sProjectName)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileJournal:
        fileJournal.write("{ torn bytes, not json")
    return operationJournal.fsComputeJournalFileSha256(sProjectName)


def _contextCrashInsteadOfClearing():
    """Interrupt the transaction exactly between the audit and the clear.

    ``patch.object`` rather than the ``monkeypatch`` fixture: that
    fixture is ONE instance shared with every other fixture in the
    test, so ``undo()`` also reverts the registry redirection — which
    it did, and the symptom was the second half of the test being told
    its host project is containerized.
    """
    def fnCrashInsteadOfClearing(sContainerName, sExpectedSha256):
        del sContainerName, sExpectedSha256
        raise KeyboardInterrupt("power cut")
    return patch.object(
        operationJournal, "fnBreakGlassClearMalformedJournal",
        fnCrashInsteadOfClearing,
    )


@pytest.mark.falsification
def testAbandoningRecordsTheAssertionAndThenClearsTheMarker(tmp_path):
    """The happy path, and every field a later reader will want.

    Kills: clearing the marker without writing the audit, which turns
    an attributable assertion into an anonymous deletion.
    """
    sProjectDirectory = _fsRegisterProject(
        tmp_path, S_HOST_PROJECT, "host",
    )
    sMarkerSha256 = _fsPlantMalformedMarker(S_HOST_PROJECT)

    reconciliation.fdictAbandonHostJournal(S_HOST_PROJECT, sMarkerSha256)

    assert not os.path.exists(
        operationJournal.fsJournalPathFor(S_HOST_PROJECT),
    ), "the marker survived an accepted abandonment"
    listEntries = abandonmentAudit.flistReadAbandonments(S_HOST_PROJECT)
    assert len(listEntries) == 1, listEntries
    dictEntry = listEntries[0]
    assert dictEntry["sMarkerSha256"] == sMarkerSha256
    assert dictEntry["sContainerName"] == S_HOST_PROJECT
    assert dictEntry["sProjectDirectory"] == os.path.realpath(
        sProjectDirectory,
    ), "the audit cannot name the directory the assertion was about"
    assert dictEntry["iPrincipalUid"] == os.getuid()
    assert dictEntry["sAbandonedIso"].endswith("+00:00"), (
        "the timestamp is not UTC, so two machines' audits cannot be "
        f"compared: {dictEntry['sAbandonedIso']}"
    )


@pytest.mark.falsification
def testTheAuditIsOnDiskBeforeTheMarkerIsUnlinked(tmp_path):
    """The ordering, proven by crashing exactly between the two.

    A crash after the unlink and before the append leaves a project
    whose proof was abandoned with nothing on disk saying so — and
    unlike every other failure here, nothing can recover it
    afterwards.

    Kills: unlinking first and recording after.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sMarkerSha256 = _fsPlantMalformedMarker(S_HOST_PROJECT)

    with _contextCrashInsteadOfClearing():
        with pytest.raises(KeyboardInterrupt):
            reconciliation.fdictAbandonHostJournal(
                S_HOST_PROJECT, sMarkerSha256,
            )

    listEntries = abandonmentAudit.flistReadAbandonments(S_HOST_PROJECT)
    assert len(listEntries) == 1, (
        "the abandonment was not recorded before the clear was "
        f"attempted: {listEntries}"
    )


@pytest.mark.falsification
def testRerunningAfterACrashRecordsOneEventNotTwo(tmp_path):
    """Idempotent by marker hash: the interrupted run completes.

    Kills: appending unconditionally, which turns one interrupted
    abandonment into two audit entries and makes the record of how
    often a researcher gave up on proof unreliable.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sMarkerSha256 = _fsPlantMalformedMarker(S_HOST_PROJECT)

    with _contextCrashInsteadOfClearing():
        with pytest.raises(KeyboardInterrupt):
            reconciliation.fdictAbandonHostJournal(
                S_HOST_PROJECT, sMarkerSha256,
            )

    reconciliation.fdictAbandonHostJournal(S_HOST_PROJECT, sMarkerSha256)

    assert len(
        abandonmentAudit.flistReadAbandonments(S_HOST_PROJECT),
    ) == 1, "one interrupted abandonment was recorded as two events"


@pytest.mark.falsification
def testAContainerProjectCannotBeAbandoned(tmp_path):
    """The proving exit exists for it, so the asserting one is refused.

    Kills: dropping the mode requirement, which lets a containerized
    project throw away a proof it could have made by stopping its
    container.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    sMarkerSha256 = _fsPlantMalformedMarker(S_CONTAINER_PROJECT)
    with pytest.raises(
        reconciliation.ReconciliationRefusedError,
    ) as errorInfo:
        reconciliation.fdictAbandonHostJournal(
            S_CONTAINER_PROJECT, sMarkerSha256,
        )
    assert "break-glass" in str(errorInfo.value)
    assert os.path.exists(
        operationJournal.fsJournalPathFor(S_CONTAINER_PROJECT),
    ), "a refused abandonment cleared the marker anyway"
    assert abandonmentAudit.flistReadAbandonments(
        S_CONTAINER_PROJECT,
    ) == [], "a refused abandonment still wrote an audit entry"


@pytest.mark.falsification
def testAHostProjectCannotBeBreakGlassed(tmp_path):
    """The proving exit refuses a project it can prove nothing about.

    The break-glass's whole containment is stopping the container the
    journal is named for. Run against a host project it stops nothing,
    learns nothing by stopping nothing, and deletes the marker — which
    is exactly the behaviour the proven-stop rewrite removed.

    Kills: dropping the host refusal from the break-glass.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sMarkerSha256 = _fsPlantMalformedMarker(S_HOST_PROJECT)
    with pytest.raises(
        reconciliation.ReconciliationRefusedError,
    ) as errorInfo:
        reconciliation.fdictExecuteBreakGlass(
            S_HOST_PROJECT, sMarkerSha256,
            fnStopContainerByName=lambda sName: True,
        )
    assert "no container to stop" in str(errorInfo.value)
    assert os.path.exists(
        operationJournal.fsJournalPathFor(S_HOST_PROJECT),
    ), "a refused break-glass cleared the marker anyway"


@pytest.mark.falsification
def testAStaleHashWritesNoAuditAndClearsNothing(tmp_path):
    """The pre-check has no side effect, so a misdirected request acts.

    ...on nothing at all. A hash that no longer matches means the
    marker was replaced since the researcher read it, so the thing
    they inspected is not the thing they are about to destroy — and an
    audit entry claiming they abandoned it would be false.

    Kills: recording the assertion before checking the hash.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    _fsPlantMalformedMarker(S_HOST_PROJECT)
    with pytest.raises(reconciliation.ReconciliationRefusedError):
        reconciliation.fdictAbandonHostJournal(
            S_HOST_PROJECT, "0" * 64,
        )
    assert os.path.exists(
        operationJournal.fsJournalPathFor(S_HOST_PROJECT),
    )
    assert abandonmentAudit.flistReadAbandonments(S_HOST_PROJECT) == []


def testAValidJournalIsNotAbandonable(tmp_path):
    """A readable marker is reconciliation's business, not this exit's.

    Abandonment is for a marker nobody can parse. A valid one names
    its operations, so the probes can answer about them and Cancel can
    act on them; giving up on proof there would discard information
    vaibify actually has.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sOperationId = operationJournal.fsPrepareOperation(
        S_HOST_PROJECT, "host-exec", "pipeline-step:A01",
    )
    assert sOperationId
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(
        S_HOST_PROJECT,
    )
    with pytest.raises(
        reconciliation.ReconciliationRefusedError,
    ) as errorInfo:
        reconciliation.fdictAbandonHostJournal(
            S_HOST_PROJECT, sMarkerSha256,
        )
    assert "valid" in str(errorInfo.value)


def testTheAuditFileIsPrivateAndSitsBesideTheJournal(tmp_path):
    """0600, in the sweeper-free journal directory, one JSON per line."""
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sMarkerSha256 = _fsPlantMalformedMarker(S_HOST_PROJECT)
    reconciliation.fdictAbandonHostJournal(S_HOST_PROJECT, sMarkerSha256)
    sAuditPath = abandonmentAudit.fsAbandonmentAuditPathFor(S_HOST_PROJECT)
    assert os.path.dirname(sAuditPath) == os.path.dirname(
        operationJournal.fsJournalPathFor(S_HOST_PROJECT),
    ), "the audit does not follow the journal directory it belongs to"
    assert os.stat(sAuditPath).st_mode & 0o777 == 0o600
    with open(sAuditPath) as fileAudit:
        for sLine in fileAudit:
            assert isinstance(json.loads(sLine), dict)


def testAnUnparseableAuditLineDoesNotBlockAnAbandonment(tmp_path):
    """A damaged audit must not wedge the exit it exists to record."""
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    sAuditPath = abandonmentAudit.fsAbandonmentAuditPathFor(S_HOST_PROJECT)
    os.makedirs(os.path.dirname(sAuditPath), exist_ok=True)
    with open(sAuditPath, "w") as fileAudit:
        fileAudit.write("half a line, cut off by a full disk\n")
    sMarkerSha256 = _fsPlantMalformedMarker(S_HOST_PROJECT)
    reconciliation.fdictAbandonHostJournal(S_HOST_PROJECT, sMarkerSha256)
    assert len(
        abandonmentAudit.flistReadAbandonments(S_HOST_PROJECT),
    ) == 1
