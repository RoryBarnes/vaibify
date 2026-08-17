"""The dashboard's reconcile: prove-and-clear from the quarantine modal.

``POST /api/registry/{sName}/reconcile`` is the browser face of
``vaibify reconcile`` restricted to the non-destructive prove: it runs
the same proving transaction the CLI reaches and either clears the
quarantine or answers 409 with the refusal. The destructive exits
(break-glass, force-abandon, abandon-host-journal) stay CLI-only, and
the agent lane is refused outright — clearing a quarantine asserts the
container is safe, which a compromised agent must never assert about
its own container (the exclusion in ``actionCatalog`` carries that
rationale; ``testAgentLaneEnforcement`` drives it).

These tests run the REAL journal machinery over an isolated journal
directory — only the Docker probes are doubles, in the same shapes
``testReconciliation.py`` uses.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import containerLock, operationJournal
from vaibify.config.operationJournal import (
    fdictReadJournalOutcome,
    fnMarkOperationNeedsReconciliation,
    fnPromoteOperationToInFlight,
    fsPrepareOperation,
)

S_PROJECT = "quarantined-demo"


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


class FakeConnectionGroupProbe:
    """Exec settled; the group probe answers a configured member count."""

    def __init__(self, iMemberCount):
        self.iMemberCount = iMemberCount

    def fdictInspectExec(self, sDockerExecId):
        del sDockerExecId
        return {"Running": False}

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        del sContainerId, iProcessGroup
        return {
            "bConclusive": True, "iMemberCount": self.iMemberCount,
            "sDetail": f"{self.iMemberCount} live member(s)",
        }


def _fappBuildHub(connectionDocker):
    """Build a minimal hub app with the registry routes and no flock."""
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *aArgs: None, "docker": connectionDocker}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


def _fsJournalTerminalRecord():
    """Journal a NEEDS_RECONCILIATION terminal record; return its id."""
    sOperationId = fsPrepareOperation(S_PROJECT, "terminal", "cid-gone")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {
            "sDockerExecId": "deadbeef",
            "sDockerContainerId": "cid-gone",
            "iHolderProcessGroup": 2126,
        },
    )
    fnMarkOperationNeedsReconciliation(
        S_PROJECT, sOperationId, "containment unproven at session end",
    )
    return sOperationId


def _fbJournalStillHasRecord(sOperationId):
    dictOutcome = fdictReadJournalOutcome(S_PROJECT)
    return sOperationId in (dictOutcome.get("dictOperations") or {})


def testReconcileClearsAProvenSettledQuarantine():
    """The happy path: dead exec, empty group, journal cleared."""
    sOperationId = _fsJournalTerminalRecord()
    clientHub = TestClient(_fappBuildHub(FakeConnectionGroupProbe(0)))
    response = clientHub.post(
        f"/api/registry/{S_PROJECT}/reconcile",
        json={"listExpectedOperationIds": [sOperationId]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bReconciled"] is True
    assert not _fbJournalStillHasRecord(sOperationId), (
        "the route answered success without clearing the journal"
    )


def testReconcileRefusesOverALiveProcessAndKeepsTheRecord():
    """A surviving group member refuses with the reason, changing nothing.

    This is the guarantee that makes the button honest: it can never
    clear a quarantine over a process that is still alive, exactly as
    the CLI cannot.
    """
    sOperationId = _fsJournalTerminalRecord()
    clientHub = TestClient(_fappBuildHub(FakeConnectionGroupProbe(1)))
    response = clientHub.post(
        f"/api/registry/{S_PROJECT}/reconcile",
        json={"listExpectedOperationIds": [sOperationId]},
    )
    assert response.status_code == 409, response.text
    assert "outlived" in response.json()["detail"]
    assert _fbJournalStillHasRecord(sOperationId), (
        "a refused reconcile must leave the journal untouched"
    )


def testReconcileRefusesStaleExpectedIds():
    """A record the modal never showed is never cleared blind."""
    sOperationId = _fsJournalTerminalRecord()
    clientHub = TestClient(_fappBuildHub(FakeConnectionGroupProbe(0)))
    response = clientHub.post(
        f"/api/registry/{S_PROJECT}/reconcile",
        json={"listExpectedOperationIds": ["someStaleOperationId"]},
    )
    assert response.status_code == 409, response.text
    assert _fbJournalStillHasRecord(sOperationId)


def testReconcileRequiresExpectedIds():
    _fsJournalTerminalRecord()
    clientHub = TestClient(_fappBuildHub(FakeConnectionGroupProbe(0)))
    response = clientHub.post(
        f"/api/registry/{S_PROJECT}/reconcile",
        json={"listExpectedOperationIds": []},
    )
    assert response.status_code == 400


def testQuarantineDetailNamesTheDashboardPath():
    """The detail payload says whether the modal may offer the button.

    A valid journal with records is reconcilable from the dashboard; a
    malformed one is not, and the modal must route it to the CLI where
    the destructive exits live.
    """
    sOperationId = _fsJournalTerminalRecord()
    clientHub = TestClient(_fappBuildHub(FakeConnectionGroupProbe(0)))
    dictDetail = clientHub.get(
        f"/api/registry/{S_PROJECT}/quarantine",
    ).json()
    assert dictDetail["bReconcilableHere"] is True
    assert dictDetail["bHostProject"] is False
    assert dictDetail["listRecords"][0]["sOperationId"] == sOperationId
