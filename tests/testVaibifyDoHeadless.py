"""End-to-end tests for ``vaibify do`` (design §6b, slice 8).

The gate for the researcher-lane repair, and the replacement for the
``testHubCredentialEndpointIsServed`` tripwire it retires. That tripwire
could only assert that a route existed; this file drives the whole flow
against real boundaries — a real hub served by a real uvicorn on a
loopback port, its host control socket bound as a real Unix domain
socket, the real ``mint-bootstrap`` round trip over it, the real
``/api/bootstrap`` redemption over HTTP, a real claim, and a real
owner-scoped action carrying the minted lease.

That distinction is the point. The rest of the CLI's tests mock
``requests`` and assert against the mock's return, which is exactly how
this lane shipped 404ing on a retired endpoint with a green suite: the
fixtures agreed with each other and nothing ever spoke to a hub.

The container NAME stays distinct from the Docker ID throughout (repo
epistemics rule): the owner map is name-keyed while every route is
pathed by id, and a fixture where the two agree once hid a bug that
would have closed every real session.
"""

import os
import socket
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import browserSession, hostControlChannel
from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
from tests.browser.fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_WORKFLOW_PATH,
)

S_PROBE_ACTION = "get-pipeline-state"


@pytest.fixture(autouse=True)
def fixtureIsolateHostDirectories(tmp_path, monkeypatch):
    """Keep the journal, locks, and control sockets out of ~/.vaibify."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    sControlDirectory = tempfile.mkdtemp(prefix="vaibifyCtl")
    if len(sControlDirectory) > 70:
        sControlDirectory = tempfile.mkdtemp(prefix="vaibifyCtl", dir="/tmp")
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY", sControlDirectory,
    )
    yield
    for sEntry in os.listdir(sControlDirectory):
        os.unlink(os.path.join(sControlDirectory, sEntry))
    os.rmdir(sControlDirectory)


def _fiFreeLoopbackPort():
    """Ask the kernel for a currently-free loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socketProbe:
        socketProbe.bind(("127.0.0.1", 0))
        return socketProbe.getsockname()[1]


def _fnWaitUntilServing(iPort, fTimeoutSeconds=15.0):
    """Block until the hub answers TCP connects on the port."""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        try:
            with socket.create_connection(("127.0.0.1", iPort), 0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"the test hub never served port {iPort}")


@pytest.fixture
def tLiveHub(tmp_path):
    """Yield ``(app, iPort)`` for a real hub served by a real uvicorn."""
    import uvicorn
    from vaibify.config import registryManager
    from vaibify.gui import pipelineServer
    from vaibify.gui.appFactory import fappCreateHubApplication
    from tests.browser.fakeDockerAdapter import FailClosedDockerAdapter
    iPort = _fiFreeLoopbackPort()
    sRegistryHome = str(tmp_path / "registryHome")
    os.makedirs(sRegistryHome, exist_ok=True)
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda *args, **kwargs: FailClosedDockerAdapter(),
    ), patch.object(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryHome,
    ), patch.object(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryHome, "registry.json"),
    ), patch.object(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryHome, "registry.lock"),
    ):
        app = fappCreateHubApplication(iExpectedPort=iPort)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=iPort, log_level="warning",
        ))
        threadServer = threading.Thread(target=server.run, daemon=True)
        threadServer.start()
        try:
            _fnWaitUntilServing(iPort)
            yield (app, iPort)
        finally:
            server.should_exit = True
            threadServer.join(timeout=10)


def _fdictProbeAction():
    """Return the catalog entry the end-to-end tests drive.

    A read-only, container-scoped GET: it is owner-scoped exactly like
    every mutation (``container-read`` runs the same bound-lease
    authority), so it proves the lease reached the hub without asking a
    fake container to perform work.
    """
    for dictEntry in LIST_AGENT_ACTIONS:
        if dictEntry["sName"] == S_PROBE_ACTION:
            return dictEntry
    raise AssertionError(
        f"the catalog no longer defines the {S_PROBE_ACTION!r} action"
    )


def _fdictCommandParameters(iPort):
    """Return the click parameter dict one generated command receives.

    The project path is given explicitly, as ``--workflow`` does. It is
    not a shortcut around the lane under test: discovery is a separate
    container probe (``git rev-parse`` per candidate) that this fail-
    closed adapter answers with "no repo", so a discovered path would
    be the fake's limit, not the CLI's.
    """
    return {
        "sProjectName": None,
        "iPort": iPort,
        "sWorkflowPath": S_WORKFLOW_PATH,
        "bJson": True,
        "bDryRun": False,
        "fTimeoutSeconds": 30.0,
        "tfields": (),
    }


def _fiRunDoAgainstHub(iPort, monkeypatch):
    """Drive the real ``vaibify do`` entry; return its exit code.

    Only the project-name resolution is stubbed — that reads the
    researcher's own config file and has nothing to do with the lane
    under test. Everything below it is real: socket, HTTP, claim,
    connect, action, release.
    """
    from vaibify.cli import actionCommands
    monkeypatch.setattr(
        actionCommands, "_fsResolveContainerName",
        lambda sProjectName: S_CONTAINER_NAME,
    )
    with pytest.raises(SystemExit) as excInfo:
        actionCommands.fnRunCatalogAction(
            _fdictProbeAction(), _fdictCommandParameters(iPort),
        )
    return int(excInfo.value.code or 0)


def _tClaimAsDashboard(app, iPort):
    """Claim the container the way a dashboard tab does, over real HTTP.

    Not a hand-built owner record: a genuine capability redemption
    followed by a genuine ``POST .../claim``, so the record under test
    holds a real flock and a real session binding — the conditions the
    CLI's claim is actually arbitrated against.
    """
    import requests
    sBaseUrl = f"http://127.0.0.1:{iPort}"
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )
    responseBootstrap = requests.post(
        f"{sBaseUrl}/api/bootstrap",
        json={"sCapability": sCapability}, timeout=10,
    )
    assert responseBootstrap.status_code == 200, responseBootstrap.text
    sCredential = responseBootstrap.json()["sCredential"]
    responseClaim = requests.post(
        f"{sBaseUrl}/api/registry/{S_CONTAINER_NAME}/claim",
        headers={"X-Session-Token": sCredential}, timeout=10,
    )
    assert responseClaim.status_code == 200, responseClaim.text
    recordOwner = app.state.dictContainerOwners[S_CONTAINER_NAME]
    assert recordOwner.sContainerId == S_CONTAINER_ID
    return (recordOwner, recordOwner.sBrowserSessionId, sCredential)


@pytest.mark.falsification
def test_do_bootstraps_over_the_socket_and_acts_under_its_lease(
    tLiveHub, monkeypatch, capsys,
):
    """Socket bootstrap → HTTP redemption → claim → leased action → release.

    The slice-8 gate (design §12). Every boundary is real, so this
    fails if the CLI asks for a credential the hub does not serve, if
    the capability cannot be redeemed, if the claim is refused, or if
    the owner-scoped action arrives without the lease the hub requires.
    The lease is not fabricated anywhere: it comes back from the claim
    the CLI itself made.

    Kills: dropping ``sLeaseId=dictSession["sLeaseId"]`` from
    ``ftSendSessionRequest`` — the omission this slice repairs.
    Confirmed by hand: the flow then dies at the first owner-scoped
    call, "Project connect failed (HTTP 409): In use in another browser
    session", and the command exits 4 instead of 0.
    """
    app, iPort = tLiveHub
    iExitCode = _fiRunDoAgainstHub(iPort, monkeypatch)
    tCaptured = capsys.readouterr()
    sOutput = tCaptured.out + tCaptured.err
    assert iExitCode == 0, sOutput
    assert "bRunning" in sOutput, sOutput
    # The lease was released on the way out: the record is gone, so the
    # next command — or a dashboard tab — can claim the container.
    assert app.state.dictContainerOwners == {}
    assert app.state.dictSessionOwner == {}


def test_do_releases_its_lease_even_when_the_action_fails(
    tLiveHub, monkeypatch, capsys,
):
    """A failing action must not strand the claim.

    The container would otherwise stay held until the reaper noticed,
    and every later command — including the dashboard's claim — would
    be refused as in use.
    """
    app, iPort = tLiveHub
    dictEntry = dict(
        _fdictProbeAction(), sPath="/api/pipeline/{sContainerId}/no-such",
    )
    from vaibify.cli import actionCommands
    monkeypatch.setattr(
        actionCommands, "_fsResolveContainerName",
        lambda sProjectName: S_CONTAINER_NAME,
    )
    with pytest.raises(SystemExit) as excInfo:
        actionCommands.fnRunCatalogAction(
            dictEntry, _fdictCommandParameters(iPort),
        )
    assert int(excInfo.value.code or 0) == 1, capsys.readouterr().out
    assert app.state.dictContainerOwners == {}


def test_do_refuses_and_names_the_agent_lane_when_a_dashboard_holds_it(
    tLiveHub, monkeypatch, capsys,
):
    """The named residual (design §6b): 409, explained, nothing taken.

    One session per container is a behavior change for "dashboard open,
    run a quick CLI action", so the refusal must say WHY and name the
    in-container agent lane, which exists precisely for acting on a
    live-dashboard container. It must NOT transfer or revoke the
    dashboard's session to let itself in.
    """
    app, iPort = tLiveHub
    recordOwner, sSessionId, sCredential = _tClaimAsDashboard(app, iPort)
    sLeaseBefore = recordOwner.sLeaseId
    iExitCode = _fiRunDoAgainstHub(iPort, monkeypatch)
    sErrorOutput = capsys.readouterr().err
    assert iExitCode == 4
    assert "held by another vaibify session" in sErrorOutput
    assert "one session per container" in sErrorOutput
    assert "vaibify-do" in sErrorOutput
    # Nothing was taken from the dashboard: same owner, same lease, same
    # session, still ACTIVE, and no generation bump (that would be a
    # transfer, which this lane may never perform).
    assert app.state.dictContainerOwners[S_CONTAINER_NAME] is recordOwner
    assert recordOwner.sLeaseId == sLeaseBefore
    assert recordOwner.sBrowserSessionId == sSessionId
    assert recordOwner.iOwnerGeneration == 1
    assert browserSession.fbValidateCredential(
        app.state.dictBrowserSessions, sCredential,
    ) is True


def test_do_mints_a_session_that_never_carries_a_second_container(
    tLiveHub, monkeypatch,
):
    """Each invocation redeems its own credential, bound to nothing yet.

    The headless bootstrap must produce an ORDINARY browser session:
    one that holds no container until it claims one, so the cardinality
    rule applies to the CLI exactly as it does to a tab.
    """
    from vaibify.cli import hubSession
    app, iPort = tLiveHub
    sCredential = hubSession.fsRedeemHostLaneCredential(
        iPort, f"http://127.0.0.1:{iPort}",
    )
    del monkeypatch
    assert browserSession.fbValidateCredential(
        app.state.dictBrowserSessions, sCredential,
    ) is True
    sSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential,
    )
    assert sSessionId
    assert sSessionId not in app.state.dictSessionOwner
