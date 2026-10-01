"""Removing a project from the list needs the container's lease.

``DELETE /api/registry/{sName}`` was classified ``browser-hub`` while
every other route that ends or reconfigures a container's life carried
the lease, so a second browser session could remove a project another
session was working in, and re-adding it as a host project flipped the
running work to host semantics mid-run. It is ``container-lifecycle`` now:
refused (403) for a session that does not hold the lease of an OWNED
container, still answerable for an unowned one.

Same harness as ``tests/testContainerLifecycleGating.py``: the real hub
application, genuine per-session credentials, name distinct from id.
"""

import pytest

from tests.testContainerLifecycleGating import (  # noqa: F401
    appHub,
    fclientAuthenticated,
    fixtureIsolateHostState,
    fnRegisterProject,
)
from tests.testAgentLaneEnforcement import S_CONTAINER_NAME
from vaibify.config import registryManager


def _flistRegisteredNames():
    return [dictProject["sName"]
            for dictProject in registryManager.flistGetAllProjects()]


@pytest.mark.falsification
def testASessionWithoutTheLeaseCannotRemoveAnOwnedProject(appHub, tmp_path):
    """Kills: classifying the registry removal as browser-hub again."""
    clientOwner = fclientAuthenticated(appHub)
    fnRegisterProject(clientOwner, tmp_path, S_CONTAINER_NAME)
    responseClaim = clientOwner.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim")
    assert responseClaim.status_code == 200, responseClaim.text
    sOwningLease = responseClaim.json()["sLeaseId"]

    clientIntruder = fclientAuthenticated(appHub)
    for dictLeaseHeader in (
        {}, {"X-Vaibify-Lease": "forged-lease-value"},
        {"X-Vaibify-Lease": sOwningLease},
    ):
        responseForeign = clientIntruder.delete(
            f"/api/registry/{S_CONTAINER_NAME}", headers=dictLeaseHeader)
        assert responseForeign.status_code == 403, (
            dictLeaseHeader, responseForeign.text)
        assert S_CONTAINER_NAME in _flistRegisteredNames()

    responseOwner = clientOwner.delete(
        f"/api/registry/{S_CONTAINER_NAME}",
        headers={"X-Vaibify-Lease": sOwningLease})
    assert responseOwner.status_code == 200, responseOwner.text
    assert S_CONTAINER_NAME not in _flistRegisteredNames()


def testAnUnownedProjectCanStillBeRemovedFromTheList(appHub, tmp_path):
    clientOnly = fclientAuthenticated(appHub)
    fnRegisterProject(clientOnly, tmp_path, S_CONTAINER_NAME)
    responseRemove = clientOnly.delete(f"/api/registry/{S_CONTAINER_NAME}")
    assert responseRemove.status_code == 200, responseRemove.text
    assert S_CONTAINER_NAME not in _flistRegisteredNames()
