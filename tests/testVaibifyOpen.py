"""End-to-end tests for ``vaibify open`` (design §6/§6b, slice 5).

The full host-authorized transfer path against REAL boundaries: a real
hub application served by a real uvicorn on a loopback port, its host
control socket bound as a real Unix domain socket, the CLI's own mint
handshake over that socket, and the redemption POSTed over real HTTP.
The container NAME stays distinct from the Docker ID throughout (repo
epistemics rule), and the old browser session comes from a genuine
bootstrap redemption so its revocation is observable.
"""

import os
import re
import socket
import tempfile
import threading
import time
from unittest.mock import patch

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import browserSession, containerOwnership, hostControlChannel

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-fedcba987654"


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


def _tSeedOwnedContainer(app):
    """Bind a real old browser session to an owned container record."""
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )
    sOldSessionId, sOldCredential = browserSession.ftRedeemCapability(
        app.state.dictBrowserSessions, sCapability,
    )
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=containerOwnership.fsMintLease(),
        fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId=sOldSessionId,
    )
    app.state.dictContainerOwners[S_PROJECT_NAME] = recordOwner
    app.state.dictSessionOwner[sOldSessionId] = S_PROJECT_NAME
    return (recordOwner, sOldSessionId, sOldCredential)


def _fiRunOpenAgainstHub(iPort, monkeypatch, sName=S_PROJECT_NAME):
    """Drive the real CLI entry with discovery pinned to the test hub."""
    from vaibify.cli import commandOpen
    monkeypatch.setattr(
        commandOpen, "fdictReadLockHolder",
        lambda sContainerName: {"iPid": os.getpid(), "iPort": iPort},
    )
    monkeypatch.setenv("VAIBIFY_SUPPRESS_BROWSER", "1")
    return commandOpen.fiRunOpenCommand(sName)


def _fsCapabilityFromPrintedUrl(sOutput):
    """Extract the one-time capability from the printed dashboard URL."""
    oMatch = re.search(r"#transfer=([A-Za-z0-9_\-]+)", sOutput)
    assert oMatch, f"no transfer URL was printed:\n{sOutput}"
    return oMatch.group(1)


@pytest.mark.falsification
def test_open_transfers_over_real_socket_and_http(
    tLiveHub, monkeypatch, capsys,
):
    """Socket mint → HTTP redemption → the new session holds the lease.

    Case 15, end-to-end half (design §6): ``vaibify open`` against a
    live hub commits the whole transfer — the owner record carries a
    rotated lease bound to a NEW browser session at generation 2, the
    old session's credential is revoked, the agent token is untouched,
    and the printed URL carries the capability in the FRAGMENT only.

    Kills: making ``_fsMintTransferCapability`` skip the mint round
    trip and hand the redemption an unminted token (the CLI would then
    "open" a session the hub never authorized; redemption must refuse
    it and the command must fail).
    """
    app, iPort = tLiveHub
    recordOwner, sOldSessionId, sOldCredential = _tSeedOwnedContainer(app)
    sOldLease = recordOwner.sLeaseId
    iExitCode = _fiRunOpenAgainstHub(iPort, monkeypatch)
    sOutput = capsys.readouterr().out
    assert iExitCode == 0, sOutput
    assert recordOwner.iOwnerGeneration == 2
    assert recordOwner.sLeaseId != sOldLease
    assert recordOwner.sBrowserSessionId != sOldSessionId
    assert recordOwner.sBrowserSessionId != ""
    assert browserSession.fbValidateCredential(
        app.state.dictBrowserSessions, sOldCredential,
    ) is False
    assert app.state.dictSessionOwner == {
        recordOwner.sBrowserSessionId: S_PROJECT_NAME,
    }
    sCapability = _fsCapabilityFromPrintedUrl(sOutput)
    assert sCapability not in sOutput.split("#transfer=")[0], (
        "the capability may appear only inside the URL fragment"
    )


def test_browser_replay_of_the_printed_capability_returns_same_tuple(
    tLiveHub, monkeypatch, capsys,
):
    """The launched page's replay yields the tuple the CLI committed.

    The CLI redeems first (the outcome lands in the terminal); the
    browser then POSTs the same capability from its URL fragment and
    must receive the SAME session, credential, and lease — never a
    second transfer, so the generation stays at 2.
    """
    import requests
    app, iPort = tLiveHub
    recordOwner, _, _ = _tSeedOwnedContainer(app)
    iExitCode = _fiRunOpenAgainstHub(iPort, monkeypatch)
    assert iExitCode == 0
    sCapability = _fsCapabilityFromPrintedUrl(capsys.readouterr().out)
    response = requests.post(
        f"http://127.0.0.1:{iPort}/api/transfer",
        json={"sCapability": sCapability}, timeout=10,
    )
    assert response.status_code == 200
    dictReplayed = response.json()
    assert dictReplayed["sOutcome"] == "transferred"
    assert dictReplayed["bReplayed"] is True
    assert dictReplayed["sLeaseId"] == recordOwner.sLeaseId
    assert dictReplayed["sSessionId"] == recordOwner.sBrowserSessionId
    assert browserSession.fbValidateCredential(
        app.state.dictBrowserSessions, dictReplayed["sCredential"],
    ) is True
    assert recordOwner.iOwnerGeneration == 2


def test_open_reports_unowned_when_the_record_was_released(
    tLiveHub, monkeypatch, capsys,
):
    """A container reaped between looking and minting says claim normally."""
    app, iPort = tLiveHub
    del app  # no owner record is ever seeded
    iExitCode = _fiRunOpenAgainstHub(iPort, monkeypatch)
    sErrorOutput = capsys.readouterr().err
    assert iExitCode == 1
    assert "claim it normally" in sErrorOutput


def test_open_names_the_dashboard_when_no_live_process_holds_the_flock(
    monkeypatch, capsys,
):
    """With no flock holder the CLI explains instead of guessing a hub."""
    from vaibify.cli import commandOpen
    monkeypatch.setattr(
        commandOpen, "fdictReadLockHolder", lambda sContainerName: {},
    )
    monkeypatch.setattr(
        "vaibify.cli.hubSession.flistFindLiveHubSessions", lambda: [],
    )
    iExitCode = commandOpen.fiRunOpenCommand(S_PROJECT_NAME)
    sErrorOutput = capsys.readouterr().err
    assert iExitCode == 1
    assert "no live vaibify hub" in sErrorOutput
    assert "transfers an EXISTING session" in sErrorOutput


def test_open_refuses_an_invalid_container_name(capsys):
    from vaibify.cli import commandOpen
    assert commandOpen.fiRunOpenCommand("../escape") == 2
    assert "invalid container name" in capsys.readouterr().err


def test_open_reports_busy_and_preserves_the_owner(
    tLiveHub, monkeypatch, capsys,
):
    """A held drain yields the busy-retry message and changes nothing."""
    import asyncio
    from vaibify.gui import sessionLifecycle
    app, iPort = tLiveHub
    recordOwner, sOldSessionId, sOldCredential = _tSeedOwnedContainer(app)
    # No drain wait to shorten: a busy container is refused at once.
    dictHeld = {}

    async def fnHoldTheDrain():
        # Obtained ON the hub loop: a Python 3.9 asyncio.Lock binds to
        # the loop that first touches it, and the hub serves on its
        # own thread's loop, not the test thread's.
        lockMutation = sessionLifecycle.flockContainerMutationForAppState(
            app.state, S_PROJECT_NAME,
        )
        dictHeld["lockMutation"] = lockMutation
        await lockMutation.acquire()

    asyncio.run_coroutine_threadsafe(
        fnHoldTheDrain(), _floopOfRunningHub(app),
    ).result(timeout=5)
    try:
        iExitCode = _fiRunOpenAgainstHub(iPort, monkeypatch)
    finally:
        _floopOfRunningHub(app).call_soon_threadsafe(
            dictHeld["lockMutation"].release,
        )
    sErrorOutput = capsys.readouterr().err
    assert iExitCode == 1
    assert "Container busy" in sErrorOutput
    assert "Re-run 'vaibify open' shortly." in sErrorOutput
    assert recordOwner.iOwnerGeneration == 1
    assert recordOwner.sBrowserSessionId == sOldSessionId
    assert browserSession.fbValidateCredential(
        app.state.dictBrowserSessions, sOldCredential,
    ) is True


def _floopOfRunningHub(app):
    """Return the event loop the live hub is serving on.

    The host control server (an ``asyncio.Server``) is started by the
    hub's lifespan on the serving loop, so its loop IS the hub loop.
    """
    serverControl = getattr(app.state, "serverHostControl", None)
    assert serverControl is not None, (
        "the live hub never started its host control server"
    )
    return serverControl.get_loop()
