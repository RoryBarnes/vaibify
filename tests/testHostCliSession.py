"""The researcher-lane CLI against a HOST project, over a real hub.

Phase D's ``vaibify do`` deliverable is PROOF, not new code: the claim,
connect and action routes grew their host branches in earlier waves,
and the CLI speaks to them by name over real HTTP — so what a test can
add is the end-to-end fact that ``vaibify do`` drives a host project
through a real uvicorn with no Docker leg at all. Everything below the
project-name stub is real: the control-socket bootstrap, the claim
(host branch — no agent token, no readiness wait), the connect (a real
workflow file loaded through the real ``HostConnection``), the action,
and the release.

Mirrors ``testVaibifyDoHeadless`` deliberately; the differences ARE
the subject: the registry names a host project, the Docker factory
returns ``None`` (a daemon-less machine), and the workflow lives in a
real git repository on disk.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

from unittest.mock import patch

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import hostControlChannel
from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
from vaibify.host import hostScratch

S_HOST_PROJECT = "hostCliProject"
S_PROBE_ACTION = "get-pipeline-state"

# Binds a real port and a control socket; the re-kill harness keeps
# this out of its parallel workers.
pytestmark = pytest.mark.exclusive


@pytest.fixture(autouse=True)
def fixtureIsolateHostDirectories(tmp_path, monkeypatch):
    """Keep the journal, locks, scratch and sockets out of ~/.vaibify."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
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


def _fsSeedHostProject(sHome, sWorkflowRelativePath):
    """Create a real host project: git repo, workflow, one step.

    ``sWorkflowRelativePath`` places the Project file — the canonical
    ``.vaibify/projects/`` home, or the legacy repo ROOT that early
    scaffolds used — so the live-hub journeys prove the whole
    claim/connect/act/release chain for both layouts.
    """
    sProjectRoot = os.path.join(sHome, S_HOST_PROJECT)
    sStepDirectory = os.path.join(sProjectRoot, "MakeNumbers")
    os.makedirs(sStepDirectory, exist_ok=True)
    with open(os.path.join(sStepDirectory, "makeNumbers.py"), "w") as f:
        f.write("print('host cli step')\n")
    sWorkflowFullPath = os.path.join(sProjectRoot, sWorkflowRelativePath)
    os.makedirs(os.path.dirname(sWorkflowFullPath), exist_ok=True)
    with open(sWorkflowFullPath, "w") as fileWorkflow:
        json.dump({
            "sPlotDirectory": "Plot",
            "listSteps": [{
                "sName": "MakeNumbers",
                "sStepId": "make-numbers",
                "sDirectory": "MakeNumbers",
                "bRunEnabled": True,
                "saDataCommands": ["python3 makeNumbers.py"],
                "saOutputDataFiles": [],
                "saPlotCommands": [],
                "saPlotFiles": [],
            }],
        }, fileWorkflow)
    for listCommand in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "lane@example.invalid"],
        ["git", "config", "user.name", "Host CLI Lane"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(
            listCommand, cwd=sProjectRoot, check=True,
            capture_output=True,
        )
    return sProjectRoot


@pytest.fixture(params=[
    os.path.join(".vaibify", "projects", "hostCliProject.json"),
    "project.json",
], ids=["canonical", "legacyRoot"])
def tLiveHostHub(request, tmp_path):
    """Yield ``(app, iPort, sWorkflowPath)`` — a hub with NO Docker leg.

    Parametrized over the Project file's two admitted homes, because
    the connect guard once refused the legacy repo-root layout AFTER
    discovery began listing it — the researcher was shown a project
    card whose click bounced with a 400 (live incident, 2026-08-20).
    Every journey through this fixture now proves both layouts.
    """
    import uvicorn
    from vaibify.config import registryManager
    from vaibify.gui import pipelineServer
    from vaibify.gui.appFactory import fappCreateHubApplication
    iPort = _fiFreeLoopbackPort()
    sHome = str(tmp_path / "home")
    os.makedirs(sHome, exist_ok=True)
    sProjectRoot = _fsSeedHostProject(sHome, request.param)
    sWorkflowPath = os.path.join(sProjectRoot, request.param)
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({"listProjects": [{
        "sName": S_HOST_PROJECT,
        "sContainerName": S_HOST_PROJECT,
        "sMode": "host",
        "sDirectory": sProjectRoot,
    }]}))
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda *tArgs, **dictKeywords: None,
    ), patch.object(
        registryManager, "_S_REGISTRY_DIRECTORY", str(tmp_path),
    ), patch.object(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    ), patch.object(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    ):
        app = fappCreateHubApplication(iExpectedPort=iPort)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=iPort, log_level="warning",
        ))
        threadServer = threading.Thread(target=server.run, daemon=True)
        threadServer.start()
        try:
            _fnWaitUntilServing(iPort)
            yield (app, iPort, sWorkflowPath)
        finally:
            server.should_exit = True
            threadServer.join(timeout=10)


def _fdictProbeAction():
    """Return the read-only catalog entry the end-to-end test drives."""
    for dictEntry in LIST_AGENT_ACTIONS:
        if dictEntry["sName"] == S_PROBE_ACTION:
            return dictEntry
    raise AssertionError(
        f"the catalog no longer defines the {S_PROBE_ACTION!r} action"
    )


@pytest.mark.falsification
def test_do_drives_a_host_project_with_no_docker_leg(
    tLiveHostHub, monkeypatch, capsys,
):
    """Claim, connect, act, release — a host project, no daemon.

    The claim must mint a lease with no agent token and no readiness
    wait (both server-side host branches from wave 2); the connect
    must load the real workflow through the real ``HostConnection``;
    and the whole run must leave no lease behind.

    Kills: the resolver's host branch dropping away — the CLI then
    tells a host project's owner to ``vaibify start --detach`` a
    container that never existed, which is Phase D's found defect
    restored.
    """
    from vaibify.cli import actionCommands
    app, iPort, sWorkflowPath = tLiveHostHub
    monkeypatch.setattr(
        actionCommands, "_fsResolveContainerName",
        lambda sProjectName: S_HOST_PROJECT,
    )
    with pytest.raises(SystemExit) as excInfo:
        actionCommands.fnRunCatalogAction(_fdictProbeAction(), {
            "sProjectName": None,
            "iPort": iPort,
            "sWorkflowPath": sWorkflowPath,
            "bJson": True,
            "bDryRun": False,
            "fTimeoutSeconds": 30.0,
            "tfields": (),
        })
    tCaptured = capsys.readouterr()
    assert int(excInfo.value.code or 0) == 0, (
        f"stdout={tCaptured.out!r} stderr={tCaptured.err!r}"
    )
    assert dict(app.state.dictContainerOwners) == {}, (
        "the CLI session left its lease behind"
    )


# -----------------------------------------------------------------------
# The resolver's mode rule, unit-scoped for the symmetric direction
# -----------------------------------------------------------------------


def _fnPatchListing(monkeypatch, dictRow):
    """Serve one listing row through the resolver's HTTP seam."""
    from vaibify.cli import hubSession
    monkeypatch.setattr(
        hubSession, "ftSendHttpRequest",
        lambda *tArgs, **dictKeywords: (
            200, {"listContainers": [dictRow]},
        ),
    )


@pytest.mark.falsification
def test_resolve_id_container_still_requires_a_running_id(monkeypatch):
    """The container direction of the mode rule.

    Kills: inverting the host branch — every container project would
    be addressed by NAME, and each later route addressed by an id
    that is not a Docker id would fail somewhere less legible than
    the start hint this resolver owns.
    """
    from vaibify.cli import hubSession
    _fnPatchListing(monkeypatch, {
        "sName": "containerProj", "sContainerId": "cid-123",
    })
    assert hubSession.fsResolveContainerId(
        "http://x", "", "containerProj",
    ) == "cid-123"
    _fnPatchListing(monkeypatch, {
        "sName": "containerProj", "sContainerId": "",
    })
    with pytest.raises(hubSession.HubSessionError) as excInfo:
        hubSession.fsResolveContainerId("http://x", "", "containerProj")
    assert "vaibify start" in str(excInfo.value)
