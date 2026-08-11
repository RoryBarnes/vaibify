"""Reading the host-mode disclosure must not cost you the project.

Found by opening a host project in a real browser. The sequence is the
one the product prescribes: click the tile, the claim succeeds, the
uncontained-execution warning appears, the researcher reads it, accepts,
picks a workflow — and the pick answers ``409 Claim this container
before connecting to it`` for a project they had just claimed, with no
control anywhere on the screen for claiming it again.

The cause is a clock nothing could advance. The idle reaper asks
whether a claim has been abandoned and answers from the owner record's
``fLastSeenMonotonic``, which ONLY a socket ever stamps. Between the
claim and the first socket there is no socket by definition, so the
record aged out on the researcher while they did what the modal asked.
Thirty seconds is long enough to skim a disclosure and not long enough
to read one.

It is not host-specific, and the container test below is here to say
so: a container claim followed by thirty seconds on the workflow picker
had the identical hole. Host mode only makes it CERTAIN, because the
disclosure is mandatory, is the whole point of the mode, and is a
screen a researcher is supposed to spend time on.

The fix asks the evidence the reaper actually wants: is the owning
BROWSER still there? Both directions are pinned below — a present
browser keeps its claim, and a browser that stopped speaking loses it
on the same schedule as before, which is what keeps the reaper a
reaper.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, registryManager
from vaibify.gui import pipelineServer, serverLifespan, sessionLifecycle
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)


S_HOST_PROJECT = "disclosureHostProject"

# Comfortably past the reap grace, which is what the researcher's
# reading time was measured against.
F_TIME_SPENT_READING_SECONDS = 45.0


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """A private lock directory and a registry holding one host project."""
    monkeypatch.setattr(containerLock, "_S_LOCK_DIRECTORY", str(tmp_path))
    sRegistryDirectory = str(tmp_path / ".vaibify")
    os.makedirs(sRegistryDirectory, exist_ok=True)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    sProjectDirectory = str(tmp_path / S_HOST_PROJECT)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {S_HOST_PROJECT}\n")
    with open(
        os.path.join(sRegistryDirectory, "registry.json"), "w",
    ) as fileRegistry:
        json.dump({"listProjects": [{
            "sName": S_HOST_PROJECT,
            "sContainerName": S_HOST_PROJECT,
            "sMode": "host",
            "sDirectory": sProjectDirectory,
            "sConfigPath": os.path.join(sProjectDirectory, "vaibify.yml"),
        }]}, fileRegistry)


@pytest.fixture
def appHub():
    """The real hub application over a mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        appBuilt = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    appBuilt.state.bReapOwnerships = True
    return appBuilt


@pytest.fixture
def clientBrowser(appHub):
    return TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
    )


def _fnSpendTimeOnTheModal(appHub, sName):
    """Age the owner record the way an unattended claim ages.

    Only the OWNER record's clock is moved. The browser session's own
    clock is left fresh, which is what a browser sitting on the modal
    looks like: the hub screens keep polling behind it, and every one
    of those requests refreshes the session.
    """
    recordOwner = appHub.state.dictContainerOwners[sName]
    recordOwner.fLastSeenMonotonic = (
        time.monotonic() - F_TIME_SPENT_READING_SECONDS
    )


def _fnCloseTheBrowser(appHub):
    """Age every browser session past the presence window.

    A closed tab does not stop the reaper's clock — it stops making
    requests, so its session's stamp is the one that stops advancing.
    """
    for recordSession in appHub.state.dictBrowserSessions.get(
        "dictSessionsByCredential", {},
    ).values():
        recordSession.fLastSeenMonotonic = (
            time.monotonic() - F_TIME_SPENT_READING_SECONDS
        )


def _fnRunOneReaperPass(appHub):
    """Drive the reaper exactly as the idle watchdog loop drives it."""
    serverLifespan._fnReapIdleOwnershipsForApp(
        appHub, {"docker": MockDockerConnection()},
    )


@pytest.mark.falsification
def test_a_host_claim_survives_the_time_it_takes_to_read_the_warning(
    appHub, clientBrowser,
):
    """The reported failure, end to end over real HTTP.

    Kills: dropping the presence veto from the reaper, under which the
    claim is released while the disclosure is still on screen and the
    researcher's next click is refused for a project they hold.
    """
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_HOST_PROJECT}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    _fnSpendTimeOnTheModal(appHub, S_HOST_PROJECT)
    _fnRunOneReaperPass(appHub)
    responseConnect = clientBrowser.post(
        f"/api/connect/{S_HOST_PROJECT}",
        headers={"X-Vaibify-Lease": responseClaim.json()["sLeaseId"]},
    )
    assert responseConnect.status_code == 200, responseConnect.text
    assert responseConnect.json()["sProjectMode"] == "host"


@pytest.mark.falsification
def test_a_container_claim_survives_a_pause_on_the_workflow_picker(
    appHub, clientBrowser,
):
    """The same hole on the container lane, which is why the fix is general.

    A container claim waits on readiness and then on whatever time the
    researcher spends choosing a workflow, and nothing in either screen
    stamps the owner record either.

    Kills: scoping the presence veto to host projects, which would fix
    the report and leave the class.
    """
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    _fnSpendTimeOnTheModal(appHub, S_CONTAINER_NAME)
    _fnRunOneReaperPass(appHub)
    responseConnect = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": responseClaim.json()["sLeaseId"]},
    )
    assert responseConnect.status_code == 200, responseConnect.text


@pytest.mark.falsification
def test_a_claim_whose_browser_went_away_is_still_reaped(
    appHub, clientBrowser,
):
    """The other direction, and the whole reason the reaper exists.

    A claim abandoned by a closed tab must still free the record — and
    with it the host flock another hub process is waiting on. A veto
    that answered "present" unconditionally would pass the two tests
    above and turn every abandoned claim into a permanent one.

    Kills: a presence check that ignores the session clock.
    """
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_HOST_PROJECT}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    _fnSpendTimeOnTheModal(appHub, S_HOST_PROJECT)
    _fnCloseTheBrowser(appHub)
    _fnRunOneReaperPass(appHub)
    assert S_HOST_PROJECT not in appHub.state.dictContainerOwners, (
        "an abandoned claim outlived its browser, so the flock is held "
        "by a session that is gone"
    )


@pytest.mark.falsification
def test_presence_answers_only_before_the_first_socket(appHub):
    """The veto is narrow on purpose: a record with a socket is not its business.

    Once a socket has existed, the record's clock is stamped by
    something real and the ORPHANED_SESSION path (design §4/§7) owns
    what happens when it goes away. Widening presence to cover that
    would quietly disable the orphan machinery for every open tab.

    Kills: dropping the ``bSocketEverExisted`` condition.
    """
    from vaibify.gui import containerOwnership
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="aLease", fileHandleLock=None,
        sBrowserSessionId="aSession",
    )
    with patch.object(
        sessionLifecycle.browserSession, "fdictActiveSessionLifetimes",
        lambda dictStore: {"aSession": {
            "fIdleSeconds": 0.0, "fAgeSeconds": 1.0,
        }},
    ):
        assert sessionLifecycle.fbOwningBrowserIsPresentBeforeFirstSocket(
            appHub.state, recordOwner,
        )
        recordOwner.bSocketEverExisted = True
        assert not (
            sessionLifecycle.fbOwningBrowserIsPresentBeforeFirstSocket(
                appHub.state, recordOwner,
            )
        )


@pytest.mark.falsification
def test_a_record_bound_to_no_session_keeps_the_old_behaviour(appHub):
    """The viewer and transitional records carry no session to ask.

    They record ``sBrowserSessionId == ""``, and an empty id must not
    match a live session by accident — it would pin a record nobody can
    be shown to be attending.

    Kills: consulting the session store with an unbound id.
    """
    from vaibify.gui import containerOwnership
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="aLease", fileHandleLock=None, sBrowserSessionId="",
    )
    with patch.object(
        sessionLifecycle.browserSession, "fdictActiveSessionLifetimes",
        lambda dictStore: {"": {"fIdleSeconds": 0.0, "fAgeSeconds": 1.0}},
    ):
        assert not (
            sessionLifecycle.fbOwningBrowserIsPresentBeforeFirstSocket(
                appHub.state, recordOwner,
            )
        )
