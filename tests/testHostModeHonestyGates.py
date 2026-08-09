"""The two claims host mode gives up, given up BY NAME.

Reproducibility Level 3 is DEFINED by a pinned container image — a
digest, a linted Dockerfile, a docker-based reproduce.sh, an
in-container rerun attestation. Supervised mode's claim is that every
change to the repository has a recorded cause, which holds only while
vaibify mediates every path to the files. A host project can satisfy
neither, ever, by any amount of work.

The failure these guard against is not a crash. It is a researcher
being told to pin a Dockerfile that does not exist, or an attribution
log that goes on claiming a supervised period while the researcher's
own editor rewrites the files beside it. Both would look like the
product working.

Both directions are asserted throughout: a container project must keep
every criterion and every capability it had.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.config import registryManager
from vaibify.gui import routeScope
from vaibify.reproducibility.levelGates import (
    S_L3_HOST_MODE_CRITERION,
    flistLevel3Blockers,
)


S_HOST_PROJECT = "host-honesty-project"
S_CONTAINER_PROJECT = "containerized-honesty-project"


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the registry to a temp directory for every test."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )


def _fnRegisterProject(tmp_path, sProjectName, sMode):
    """Create and register a project directory in the given mode."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)


# ---------------------------------------------------------------------
# Level 3
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAHostProjectGetsOneL3BlockerNamingTheReason(tmp_path):
    """One blocker, not the container cascade.

    The seven workflow-scope criteria each carry a remediation hint
    telling the researcher to do something — pin a Dockerfile, publish
    an image, capture a digest — and every one of those is impossible
    and pointless for a project with no container. Emitting them would
    be a to-do list that can never be completed.

    Kills: dropping the host branch from ``flistLevel3Blockers``, so a
    host project is graded against the container criteria.
    """
    listBlockers = flistLevel3Blockers({}, str(tmp_path), True)
    assert [d["sCriterion"] for d in listBlockers] == [
        S_L3_HOST_MODE_CRITERION
    ], listBlockers
    assert "containerized" in listBlockers[0]["sRemediationHint"]
    assert listBlockers[0]["iLevel"] == 3
    assert listBlockers[0]["sScope"] == "workflow"


def testTheHostBranchIsNotReachableWithoutAskingForIt():
    """The mode argument has no default, and that is the guard.

    A caller that forgets to ask which mode it is grading gets a
    TypeError. The alternative — defaulting to container — would hand a
    host project the cascade silently, which is the exact failure the
    branch above exists to prevent.
    """
    with pytest.raises(TypeError):
        flistLevel3Blockers({}, "/nonexistent")


def testAContainerProjectKeepsItsL3Criteria(tmp_path):
    """The other direction: nothing about the container grading moved.

    A bare temp directory is not a project repo, so the honest answer
    is an empty list — but it must be empty for the REPO reason, never
    because a host branch swallowed it.
    """
    listBlockers = flistLevel3Blockers({}, str(tmp_path), False)
    assert [
        d for d in listBlockers
        if d["sCriterion"] == S_L3_HOST_MODE_CRITERION
    ] == [], listBlockers


# ---------------------------------------------------------------------
# Supervised mode
# ---------------------------------------------------------------------


def _fdictSupervisedWorkflow():
    """Return a workflow document with supervision switched on."""
    return {
        "sWorkflowName": "demo",
        "listSteps": [],
        "sProjectRepoPath": "/tmp/repo",
        "dictAiProvenance": {
            "dictSupervision": {"bEnabled": True},
            "dictPromptRecord": {
                "bEnabled": True, "bFirstCaptureReviewed": True,
            },
        },
    }


@pytest.fixture
def tclientSupervised(tmp_path, monkeypatch):
    """A hub-shaped app over one supervised host and container project.

    The carrier is stood down: these routes save through it and this
    app has no owner record for a request to bind to. What the
    admission IS lives in ``tests/testCarrierMigratedRoutes.py``; this
    module is about which requests are refused before they get there.
    """
    from vaibify.gui.routes import replayRoutes
    fnStandCarrierDown(monkeypatch, replayRoutes)
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    _fnRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    app = FastAPI()
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": None,
        "workflows": {
            S_HOST_PROJECT: _fdictSupervisedWorkflow(),
            S_CONTAINER_PROJECT: _fdictSupervisedWorkflow(),
        },
        "paths": {},
        "save": lambda sId, dictWorkflow: None,
    }
    app.state.dictRouteContext = dictCtx
    app.state.dictContainerOwners = {}
    app.state.dictBrowserSessions = {}
    replayRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictCtx


@pytest.mark.falsification
def testEnteringSupervisedModeIsRefusedForAHostProject(tclientSupervised):
    """The flag is permanent, so the refusal is at the door.

    A workflow that entered Supervised mode wrongly cannot be cleaned
    up afterwards: the flags are permanent and the event log is
    hash-chained. There is no accumulate-then-repair, which is why this
    refuses rather than warns.

    Kills: removing the host check from
    ``_fnRefuseSupervisionOnHost``.
    """
    client, _ = tclientSupervised
    response = client.post(
        f"/api/workflow/{S_HOST_PROJECT}/supervision/configure",
        json={"bEnabled": True},
    )
    assert response.status_code == 409, response.text
    assert "runs on this machine" in response.text


def testLeavingSupervisedModeIsNeverRefused(tclientSupervised):
    """Turning supervision OFF is always allowed.

    An unsupervised workflow claims nothing, so there is nothing for a
    refusal to protect — and a guard that blocked the exit would strand
    a workflow in a mode it must not stay in.
    """
    client, _ = tclientSupervised
    response = client.post(
        f"/api/workflow/{S_HOST_PROJECT}/supervision/configure",
        json={"bEnabled": False},
    )
    assert response.status_code == 200, response.text


def testAContainerProjectMayStillEnterSupervisedMode(tclientSupervised):
    """The other direction: the capability is intact where it is honest."""
    client, _ = tclientSupervised
    response = client.post(
        f"/api/workflow/{S_CONTAINER_PROJECT}/supervision/configure",
        json={"bEnabled": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["dictSupervision"]["bEnabled"] is True


# ---------------------------------------------------------------------
# Supervised mode: the refusal that covers every OTHER mutation.
# ---------------------------------------------------------------------


@pytest.fixture
def tclientSupervisedUnderTheRouteClass(tmp_path, monkeypatch):
    """The same app, served by the real ``ContainerAwareRoute``.

    The lease authority is substituted, and only that: these tests are
    about the Supervised-on-host refusal, which runs after
    authorization in the same class. ``_fiAuthorizeForScope`` looks its
    authorities up on the module deliberately so a test can do this —
    the alternative here was minting a real lease, which would make
    every one of these tests also a test of the ownership model.
    """
    from vaibify.gui.routes import replayRoutes
    fnStandCarrierDown(monkeypatch, replayRoutes)
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    _fnRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    monkeypatch.setattr(
        routeScope, "fiAuthorizeContainerHttp",
        lambda request, appState, dictScope: routeScope.I_AUTHORIZED,
    )
    app = FastAPI()
    app.router.route_class = routeScope.ContainerAwareRoute
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": None,
        "workflows": {
            S_HOST_PROJECT: _fdictSupervisedWorkflow(),
            S_CONTAINER_PROJECT: _fdictSupervisedWorkflow(),
        },
        "paths": {},
        "save": lambda sId, dictWorkflow: None,
    }
    app.state.dictRouteContext = dictCtx
    app.state.dictContainerOwners = {}
    app.state.dictBrowserSessions = {}
    replayRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictCtx


@pytest.mark.falsification
def testASupervisedHostWorkflowRefusesAnOrdinaryMutation(
    tclientSupervisedUnderTheRouteClass,
):
    """Every mutating route is refused while supervision is on.

    Not the supervision routes specifically — ANY of them. The
    attribution log claims a recorded cause for every change, and on
    the host the researcher's editor, git and IDE write to the same
    files without the hub seeing it. Continuing to accept mutations
    would extend a claim the evidence cannot support, and the flags are
    permanent, so it cannot be repaired afterwards.

    Kills: deleting the ``_fbRefuseSupervisedHostMutation`` call from
    ``ContainerAwareRoute``.
    """
    client, _ = tclientSupervisedUnderTheRouteClass
    response = client.post(
        f"/api/workflow/{S_HOST_PROJECT}/ai-models/declare",
        json={"sModelName": "some-model"},
    )
    assert response.status_code == (
        routeScope.I_REJECT_SUPERVISED_ON_HOST
    ), response.text
    assert response.status_code != 403, (
        "the refusal must not be the authorization code: a client that "
        "reads it as a credential failure tells the researcher to "
        "re-claim a project that is already theirs"
    )
    assert "Supervised" in response.json()["detail"]


@pytest.mark.falsification
def testTheRecordedExitIsTheOneMutationPermitted(
    tclientSupervisedUnderTheRouteClass,
):
    """The way out is not refused, and it records itself.

    A refusal with no exit would strand the workflow: every mutation
    blocked, including the one that would unblock them. The event is
    appended BEFORE the flag is cleared, so a failure in between leaves
    a log saying supervision ended over a flag still set — honest — and
    never a cleared flag with no event, which would be a supervised
    period that quietly stopped being recorded.

    Kills: adding the end-on-host path back into the refusal, i.e.
    dropping its exemption in ``_fbRefuseSupervisedHostMutation``.
    """
    client, dictCtx = tclientSupervisedUnderTheRouteClass
    listAppended = []

    from vaibify.gui import attributionLog

    def fnRecordEvent(filesRepo, dictWorkflow, sChannel, sActor, sDetail):
        listAppended.append((sChannel, sActor, sDetail))

    original = attributionLog.fnAppendAttributionEvent
    attributionLog.fnAppendAttributionEvent = fnRecordEvent
    try:
        response = client.post(
            f"/api/workflow/{S_HOST_PROJECT}/supervision/end-on-host",
        )
    finally:
        attributionLog.fnAppendAttributionEvent = original
    assert response.status_code == 200, response.text
    assert response.json()["dictSupervision"]["bEnabled"] is False
    assert listAppended, "the exit recorded no attribution event"
    assert listAppended[0][0] == (
        attributionLog.S_SUPERVISION_ENDED_CHANNEL
    )


def testASupervisedCONTAINERWorkflowMutatesNormally(
    tclientSupervisedUnderTheRouteClass,
):
    """The other direction, and the one that would be silent.

    Supervised mode is honest in a container, so a supervised container
    workflow must be refused nothing. A guard that keyed on
    supervision alone — forgetting the mode — would freeze every
    supervised project in the product, which is a far worse failure
    than the one it was written to prevent.
    """
    client, _ = tclientSupervisedUnderTheRouteClass
    response = client.post(
        f"/api/workflow/{S_CONTAINER_PROJECT}/supervision/configure",
        json={"bEnabled": True},
    )
    assert response.status_code == 200, response.text


def testAnUnsupervisedHostWorkflowMutatesNormally(
    tclientSupervisedUnderTheRouteClass,
):
    """And the third direction: host mode alone refuses nothing here."""
    client, dictCtx = tclientSupervisedUnderTheRouteClass
    dictCtx["workflows"][S_HOST_PROJECT]["dictAiProvenance"][
        "dictSupervision"
    ]["bEnabled"] = False
    response = client.post(
        f"/api/workflow/{S_HOST_PROJECT}/supervision/configure",
        json={"bEnabled": False},
    )
    assert response.status_code == 200, response.text
