"""The dashboard is told where its files live; it never assumes.

``/workspace`` was written as a constant in twenty-five places in the
frontend. It is true of a container and false of a host project, whose
files live in the directory the researcher registered — so the file
browser opened a path that does not exist, the dependency picker
browsed nothing, and every badge key kept an absolute prefix the step
list would never look it up by.

The fix is the same one the mode got: the SERVER answers, on the
connect handshake, and the dashboard stores the answer. This file
pins the server half. Both directions, because a root that is always
the registered directory breaks every containerized project just as
thoroughly as one that is always ``/workspace`` breaks every host one.

The honest limit of a resolution failure is asserted too. A host entry
with no directory has no correct answer; failing the whole connect
over it would lock a researcher out of a project they could otherwise
read, so the handshake falls back and the server-side path guards —
which raise rather than guess — refuse whatever is built from it.
"""

import os

import pytest

from vaibify.config import registryManager
from vaibify.gui import pipelineServer


S_HOST_PROJECT = "handshake-host-project"
S_CONTAINER_PROJECT = "handshake-container-project"


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


@pytest.mark.falsification
def testAHostProjectIsToldItsOwnDirectory(tmp_path):
    """The registered directory, not the container constant.

    Kills: answering ``/workspace`` for every resource, under which a
    host researcher's file panel opens a path that exists on nobody's
    machine and reports their project empty.
    """
    sProjectDirectory = _fsRegisterProject(
        tmp_path, S_HOST_PROJECT, "host",
    )
    assert pipelineServer.fsWorkspaceRootOfResource(
        S_HOST_PROJECT,
    ) == sProjectDirectory


@pytest.mark.falsification
def testAContainerProjectIsStillToldTheContainerRoot(tmp_path):
    """The other direction: containers keep the root they have.

    Kills: answering the registered directory unconditionally, which
    would point every containerized dashboard at a HOST path — one the
    container cannot see and the path guards will refuse.
    """
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    assert pipelineServer.fsWorkspaceRootOfResource(
        S_CONTAINER_PROJECT,
    ) == pipelineServer.WORKSPACE_ROOT


def testAnUnregisteredResourceIsToldTheContainerRoot():
    """A viewer connected straight to a container id is a container."""
    assert pipelineServer.fsWorkspaceRootOfResource(
        "0123456789abcdef",
    ) == pipelineServer.WORKSPACE_ROOT


def testAHostEntryWithNoDirectoryDoesNotFailTheHandshake(
    tmp_path, monkeypatch,
):
    """A resolution with no honest answer falls back rather than 500s.

    The guards downstream raise on a path they cannot validate, which
    is where the refusal belongs; refusing the handshake itself would
    lock the researcher out of a project they could otherwise read and
    repair.
    """
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    monkeypatch.setattr(
        "vaibify.gui.projectRoots.fsResolveProjectRoot",
        _fnRaiseValueError,
    )
    assert pipelineServer.fsWorkspaceRootOfResource(
        S_HOST_PROJECT,
    ) == pipelineServer.WORKSPACE_ROOT


def _fnRaiseValueError(sResourceId, sContainerRoot):
    """Stand in for a host entry that records no directory."""
    del sResourceId, sContainerRoot
    raise ValueError("host entry records no directory")


# ── The roots the first real host workflow found missing ─────────────
#
# Every entry below was found the same way: by opening a host workflow
# in a browser and watching the network log. Each is a backend path
# that was written as the container constant, and each broke a
# different part of the journey — which is why they are asserted
# separately rather than as one "no /workspace anywhere" sweep.


@pytest.mark.falsification
def testThePipelineStatePathFollowsTheProject(tmp_path):
    """The state file the whole dashboard reads to know if a run is live.

    Rooted at /workspace it landed outside a host project, the path
    guard refused the read, and the file-status poll answered 500 four
    times a minute for as long as the workflow was open.

    Kills: rooting the state file at the container constant.
    """
    from vaibify.gui import pipelineState
    sProjectDirectory = _fsRegisterProject(
        tmp_path, S_HOST_PROJECT, "host",
    )
    assert pipelineState.fsStatePathFor(S_HOST_PROJECT) == (
        sProjectDirectory + "/.vaibify/pipeline_state.json"
    )
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    assert pipelineState.fsStatePathFor(
        S_CONTAINER_PROJECT,
    ) == pipelineState.S_STATE_PATH


@pytest.mark.falsification
def testTheLogsDirectoryFollowsTheProject(tmp_path):
    """The defect with the most misleading symptom of the four.

    The step's command SUCCEEDED and wrote its output; the run then
    failed writing its log to /workspace, and the dashboard reported
    "Pipeline failed (exit 1)" for a step that had done its job. A
    researcher would have gone looking at their script.

    Kills: rooting the logs directory at the container constant.
    """
    from vaibify.gui import workflowManager
    sProjectDirectory = _fsRegisterProject(
        tmp_path, S_HOST_PROJECT, "host",
    )
    assert workflowManager.fsLogsDirectoryFor(S_HOST_PROJECT) == (
        sProjectDirectory + "/" + workflowManager.VAIBIFY_LOGS_DIR
    )
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    assert workflowManager.fsLogsDirectoryFor(
        S_CONTAINER_PROJECT,
    ) == (
        workflowManager.DEFAULT_SEARCH_ROOT + "/"
        + workflowManager.VAIBIFY_LOGS_DIR
    )


@pytest.mark.falsification
def testTheTrackedRepositoriesSidecarFollowsTheProject(tmp_path):
    """The Repositories panel's own state file.

    Kills: rooting the sidecar at the container constant, which made
    the panel 500 on every five-second tick for a host project.
    """
    from vaibify.gui import trackedReposManager
    sProjectDirectory = _fsRegisterProject(
        tmp_path, S_HOST_PROJECT, "host",
    )
    assert trackedReposManager.fsSidecarPathFor(S_HOST_PROJECT) == (
        sProjectDirectory + "/.vaibify/tracked_repos.json"
    )
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    assert trackedReposManager.fsSidecarPathFor(
        S_CONTAINER_PROJECT,
    ) == trackedReposManager.S_TRACKED_REPOS_PATH
