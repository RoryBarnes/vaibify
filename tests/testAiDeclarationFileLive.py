"""The AI Declaration's file routes, against a real container.

The route tests answer existence from a host directory or a double. Only
a real container proves the three answers come from the typed reads the
routes really run there: absent before the template is written, present
after, and the starter template's bytes in the container file the step
is then attached to. A stopped container must answer unknown, never
absent, because absent is what offers to write over a file.

The carrier is stood down (``tests/carrierStandDown.py``): the admission
the routes run under is asserted in ``tests/testCarrierMigratedRoutes.py``;
this module proves what they do to a real filesystem.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui.routes import levelRoutes
from vaibify.reproducibility.aiDeclarationStep import (
    S_DECLARATION_TEMPLATE,
    fdictBuildAiDeclarationStep,
)
from vaibify.reproducibility.repoFiles import ContainerRepoFiles

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_PROJECT = "/home/researcher/project"
S_ROUTE_CONTAINER_KEY = "declaration-live-key"
S_DECLARATION = "declarations/AI_USAGE.md"


@pytest.fixture
def liveContainer():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "600"], detach=True, labels=fdictLabels(),
    )
    try:
        for sSetup in (
            f"useradd -m {S_USER}",
            f"mkdir -p {S_PROJECT}",
            f"chown -R {S_USER} /home/{S_USER}",
        ):
            iExit, _ = container.exec_run(["sh", "-c", sSetup], user="root")
            assert iExit == 0, sSetup
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _ftClientOverTheContainer(container, connection, monkeypatch):
    """Return a client whose routes read and write the real container.

    The workflow is keyed by a route key that is NOT the container's
    id, and the repo adapter is bound to the real id, so a route that
    passed its own key to the daemon would fail here rather than pass.
    """
    fnStandCarrierDown(monkeypatch, levelRoutes)
    dictStep = fdictBuildAiDeclarationStep(sDeclarationFile="")
    dictStep["dictVerification"]["sUser"] = "passed"
    dictWorkflow = {"sProjectRepoPath": S_PROJECT, "listSteps": [dictStep]}
    listSaves = []
    app = FastAPI()
    dictCtx = {
        "docker": connection,
        "workflows": {S_ROUTE_CONTAINER_KEY: dictWorkflow},
        "paths": {},
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: listSaves.append(sId),
        "files": lambda sId: ContainerRepoFiles(
            connection, container.id, S_PROJECT),
    }
    levelRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictStep, listSaves


def _fsFileState(client):
    return client.get(
        f"/api/workflow/{S_ROUTE_CONTAINER_KEY}/ai-declaration/file-state",
        params={"sRelativePath": S_DECLARATION},
    ).json()


def testGenerateThenAttachInARealContainer(liveContainer, monkeypatch):
    container, connection = liveContainer
    client, dictStep, listSaves = _ftClientOverTheContainer(
        container, connection, monkeypatch)
    assert _fsFileState(client)["sFileState"] == "absent"
    responseRefused = client.post(
        f"/api/workflow/{S_ROUTE_CONTAINER_KEY}/ai-declaration/attach",
        json={"sRelativePath": S_DECLARATION},
    )
    assert responseRefused.status_code == 409, responseRefused.text
    responseGenerated = client.post(
        f"/api/workflow/{S_ROUTE_CONTAINER_KEY}"
        "/ai-declaration/generate-template",
        json={"sRelativePath": S_DECLARATION},
    )
    assert responseGenerated.status_code == 200, responseGenerated.text
    assert _fsFileState(client)["sFileState"] == "present"
    responseAttached = client.post(
        f"/api/workflow/{S_ROUTE_CONTAINER_KEY}/ai-declaration/attach",
        json={"sRelativePath": S_DECLARATION},
    )
    assert responseAttached.status_code == 200, responseAttached.text
    assert responseAttached.json()["bSignOffWithdrawn"] is True
    assert dictStep["sDeclarationFile"] == S_DECLARATION
    assert dictStep["dictVerification"]["sUser"] == "untested"
    assert listSaves == [S_ROUTE_CONTAINER_KEY]
    iExit, baOutput = container.exec_run(
        ["cat", f"{S_PROJECT}/{S_DECLARATION}"], user=S_USER)
    assert iExit == 0
    assert baOutput.decode("utf-8") == S_DECLARATION_TEMPLATE


def testAStoppedContainerIsUnknownNotAbsent(liveContainer, monkeypatch):
    container, connection = liveContainer
    client, dictStep, listSaves = _ftClientOverTheContainer(
        container, connection, monkeypatch)
    container.stop(timeout=1)
    dictState = _fsFileState(client)
    assert dictState["sFileState"] == "unknown", dictState
    assert dictState["sReason"]
    responseAttach = client.post(
        f"/api/workflow/{S_ROUTE_CONTAINER_KEY}/ai-declaration/attach",
        json={"sRelativePath": S_DECLARATION},
    )
    assert responseAttach.status_code == 503, responseAttach.text
    assert dictStep["sDeclarationFile"] == ""
    assert listSaves == []


# ---------------------------------------------------------------------
# The stale-sign-off check against real file hashes in a container.
# Both readers the dashboard uses are driven: the live typed-read hash
# (the readiness route) and the one-exec poll snapshot.
# ---------------------------------------------------------------------

S_PRODUCER_DIRECTORY = "ValuesStage"
S_COVERED_OUTPUT = S_PRODUCER_DIRECTORY + "/values.json"
S_COVERED_SCRIPT = S_PRODUCER_DIRECTORY + "/makeValues.py"


def _fdictCoveredWorkflow():
    dictDeclaration = fdictBuildAiDeclarationStep()
    dictDeclaration["dictVerification"]["sUser"] = "passed"
    return {
        "sProjectRepoPath": S_PROJECT,
        "listSteps": [{
            "sName": "Make Values", "sStepId": "producer-live-1",
            "sDirectory": S_PRODUCER_DIRECTORY,
            "saDataCommands": ["python makeValues.py"],
            "saOutputDataFiles": ["values.json"],
        }, dictDeclaration],
    }


def _fnRunInContainer(container, sCommand, sUser=S_USER):
    iExit, baOutput = container.exec_run(["sh", "-c", sCommand], user=sUser)
    assert iExit == 0, baOutput


def _fsSnapshotVerdict(connection, container, dictWorkflow):
    from vaibify.reproducibility.declarationFreshness import (
        fdictEvaluateDeclarationFreshness, flistPathsToHashForFreshness,
    )
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesSnapshot = SnapshotRepoFiles.ffilesFetch(
        connection, container.id, S_PROJECT,
        listHashRelPaths=flistPathsToHashForFreshness(dictWorkflow),
    )
    return fdictEvaluateDeclarationFreshness(
        dictWorkflow, filesSnapshot)["sVerdict"]


def testAStaleSignOffIsSeenThroughBothRealReaders(liveContainer):
    from vaibify.reproducibility.declarationFreshness import (
        S_BASELINE_KEY,
        fdictBuildDeclarationBaseline,
        fdictEvaluateDeclarationFreshness,
    )
    container, connection = liveContainer
    _fnRunInContainer(container, (
        f"mkdir -p {S_PROJECT}/{S_PRODUCER_DIRECTORY} && "
        f"echo 'print(1)' > {S_PROJECT}/{S_COVERED_SCRIPT} && "
        f"echo '{{\"a\": 1}}' > {S_PROJECT}/{S_COVERED_OUTPUT}"))
    filesLive = ContainerRepoFiles(connection, container.id, S_PROJECT)
    dictWorkflow = _fdictCoveredWorkflow()
    dictWorkflow["listSteps"][1]["dictVerification"][S_BASELINE_KEY] = (
        fdictBuildDeclarationBaseline(dictWorkflow, filesLive, ""))

    def fsLiveVerdict():
        return fdictEvaluateDeclarationFreshness(
            dictWorkflow, filesLive)["sVerdict"]

    assert fsLiveVerdict() == "fresh"
    assert _fsSnapshotVerdict(connection, container, dictWorkflow) == (
        "fresh")
    _fnRunInContainer(container, (
        f"touch -d '2001-01-01' {S_PROJECT}/{S_COVERED_OUTPUT}"))
    assert fsLiveVerdict() == "fresh", "an mtime alone is not a change"
    _fnRunInContainer(container, (
        f"echo '{{\"a\": 2}}' > {S_PROJECT}/{S_COVERED_OUTPUT}"))
    assert fsLiveVerdict() == "stale"
    assert _fsSnapshotVerdict(connection, container, dictWorkflow) == (
        "stale")
    _fnRunInContainer(container, f"rm {S_PROJECT}/{S_COVERED_OUTPUT}")
    dictDeleted = fdictEvaluateDeclarationFreshness(dictWorkflow, filesLive)
    assert dictDeleted["listChanges"][0]["sChange"] == "deleted"
    assert _fsSnapshotVerdict(connection, container, dictWorkflow) == (
        "stale"), "the poll snapshot must see a deleted file as deleted"
    _fnRunInContainer(container, (
        f"echo '{{\"a\": 1}}' > {S_PROJECT}/{S_COVERED_OUTPUT} && "
        f"chown root {S_PROJECT}/{S_COVERED_OUTPUT} && "
        f"chmod 600 {S_PROJECT}/{S_COVERED_OUTPUT}"), sUser="root")
    assert fsLiveVerdict() == "unknown", (
        "a file the container user cannot read is could-not-check, "
        "never deleted")
    assert _fsSnapshotVerdict(connection, container, dictWorkflow) == (
        "unknown")


def testASymlinkOutOfTheProjectIsRefusedInARealContainer(
    liveContainer, monkeypatch,
):
    """The link lives INSIDE the project and points outside it, so only
    the containment the container itself resolves can refuse it."""
    container, connection = liveContainer
    client, dictStep, listSaves = _ftClientOverTheContainer(
        container, connection, monkeypatch)
    iExit, _ = container.exec_run(["sh", "-c", (
        f"mkdir -p /home/{S_USER}/outside && "
        f"echo private > /home/{S_USER}/outside/notes.md && "
        f"ln -s /home/{S_USER}/outside/notes.md {S_PROJECT}/linked.md")],
        user=S_USER)
    assert iExit == 0
    sBase = f"/api/workflow/{S_ROUTE_CONTAINER_KEY}/ai-declaration"
    assert client.get(sBase + "/file-state", params={
        "sRelativePath": "linked.md"}).status_code == 403
    assert client.post(sBase + "/attach", json={
        "sRelativePath": "linked.md"}).status_code == 403
    assert dictStep["sDeclarationFile"] == ""
    assert listSaves == []
