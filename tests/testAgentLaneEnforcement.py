"""Server-side enforcement of the agent lane's limits.

Every defect these tests cover survived a green 7144-test suite,
because no fixture ever drove a user-only route *with an agent token*
through a real client. The rule for this file: use ``TestClient``, keep
the container name distinct from the container id, and assert the
refusal rather than the happy path.
"""

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.gui import actionCatalog
from vaibify.gui import containerOwnership
from vaibify.gui import pipelineServer


S_CONTAINER_ID = "abc123container"
S_CONTAINER_NAME = "test-container"
S_AGENT_TOKEN = "agent-token-for-this-container"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW = {
    "sWorkflowName": "Test Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "listSteps": [
        {
            "sName": "Step A",
            "sDirectory": "stepA",
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python dataGenerate.py"],
            "saOutputDataFiles": ["output.dat"],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested", "sUser": "untested",
            },
        },
    ],
}


class MockDockerConnection:
    """Mimic the DockerConnection surface these routes touch.

    The reported container *name* deliberately differs from the
    container *id*: the owner map is name-keyed while every URL carries
    the id, and the repo has already shipped one fatal bug that a
    name == id fixture hid.
    """

    def __init__(self):
        self._dictFiles = {}

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": "abc123",
            "sName": S_CONTAINER_NAME,
            "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if "git rev-parse --show-toplevel" in sCommand:
            return (0, "/workspace\n")
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "cat" in sCommand and "pipeline_state" in sCommand:
            return (1, "")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        raise FileNotFoundError(sPath)

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        self._dictFiles[sPath] = baContent

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        from types import SimpleNamespace
        return SimpleNamespace(iExitCode=0, sStdout="ok", sStderr="")


@pytest.fixture
def appViewer():
    """Build the real viewer application over a mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


@pytest.fixture
def clientBrowser(appViewer):
    """A client authenticated as the researcher's browser."""
    return TestClient(
        appViewer,
        headers={"X-Session-Token": appViewer.state.sSessionToken},
    )


@pytest.fixture
def clientAgent(appViewer):
    """A client authenticated as the in-container agent."""
    appViewer.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId="researcher-lease", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId=S_CONTAINER_ID,
        )
    )
    return TestClient(
        appViewer,
        headers={
            actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
            "Host": "host.docker.internal:8050",
        },
    )


# ── The agent lane honours bAgentSafe ────────────────────────────

_DICT_PLACEHOLDER_VALUES = {
    "sContainerId": S_CONTAINER_ID,
    "iStepIndex": "0",
    "iPosition": "0",
    "sService": "github",
    "sRepoName": "somerepo",
    "sFilePath": "stepA/notes.txt",
}


def _fsFillRouteTemplate(sTemplate):
    """Substitute concrete values into a catalog path template."""
    sFilled = sTemplate
    for sName, sValue in _DICT_PLACEHOLDER_VALUES.items():
        sFilled = sFilled.replace("{" + sName + ":path}", sValue)
        sFilled = sFilled.replace("{" + sName + "}", sValue)
    return sFilled


def _flistCollectUserOnlyRoutes():
    """Return (sMethod, sTemplate, sLabel) for every agent-forbidden route.

    Table-driven off the catalog itself, so a user-only action added
    later is covered without anybody remembering to extend this file.

    Two exclusions. A path with no container segment cannot authenticate
    on the agent lane at all, so it is already refused a step earlier.
    And a route that also hosts an agent-safe action is admitted by
    design -- several user-only entries are prose aliases of a read-only
    diagnostic endpoint the agent may read; ``fbAgentLanePermitsRoute``
    decides per route, not per catalog entry.
    """
    setAgentSafeRoutes = {
        (dictEntry["sMethod"], dictEntry["sPath"])
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["bAgentSafe"]
    }
    listRoutes = [
        (dictEntry["sMethod"], dictEntry["sPath"], dictEntry["sName"])
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["sMethod"] != "WS"
        and not dictEntry["bAgentSafe"]
    ]
    listRoutes += [
        (sMethod, sPath, "intentionally-excluded")
        for sMethod, sPath in
        actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS
    ]
    return sorted(
        tRoute for tRoute in listRoutes
        if "{sContainerId}" in tRoute[1]
        and (tRoute[0], tRoute[1]) not in setAgentSafeRoutes
    )


def testAliasedRoutesAreOnlyReadOnlyDiagnostics():
    """A user-only entry may share a route only with a read-only GET.

    The per-route decision means an agent-safe entry admits every entry
    on the same path. That is safe only while such collisions are
    GET-only diagnostics; a user-only POST sharing a path with an
    agent-safe POST would be silently reachable.
    """
    setAgentSafeRoutes = {
        (dictEntry["sMethod"], dictEntry["sPath"])
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["bAgentSafe"]
    }
    listCollisions = [
        dictEntry["sName"]
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if not dictEntry["bAgentSafe"]
        and (dictEntry["sMethod"], dictEntry["sPath"])
        in setAgentSafeRoutes
        and dictEntry["sMethod"] in actionCatalog.
        SET_STATE_MUTATING_METHODS
    ]
    assert listCollisions == []


@pytest.mark.falsification
@pytest.mark.parametrize(
    "sMethod,sTemplate,sLabel", _flistCollectUserOnlyRoutes(),
)
def testAgentLaneRefusesEveryUserOnlyRoute(
    clientAgent, sMethod, sTemplate, sLabel,
):
    """A user-only action must be refused 403 on the agent lane.

    ``bAgentSafe`` was enforced only client-side in ``vaibify-do``,
    which a compromised agent bypasses with a direct HTTP call, so every
    user-only action -- kill-pipeline, delete-step, declare-determinism,
    supervision/configure, publish-to-zenodo -- was reachable.

    Kills: serverMiddleware.SessionTokenMiddleware.dispatch: the
    agent-lane catalog check neutralized to `if False:`, restoring the
    unconditional `return await call_next(request)`.
    """
    responseHttp = clientAgent.request(
        sMethod, _fsFillRouteTemplate(sTemplate), json={},
    )
    assert responseHttp.status_code == 403, (
        f"{sLabel} ({sMethod} {sTemplate}) reachable by the agent"
    )
    assert "User-only action" in responseHttp.text


def testAgentLaneStillAllowsAnAgentSafeAction(clientAgent):
    """The gate must not refuse the actions the agent legitimately drives."""
    responseHttp = clientAgent.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": []},
    )
    assert responseHttp.status_code == 200


def testAgentLaneStillAllowsReadOnlyRoutes(clientAgent):
    """Read-only routes outside the catalog stay reachable for diagnosis.

    ``vaibify-do`` resolves step labels through
    ``GET /api/steps/{id}/by-label/{label}``, which has no catalog entry
    because it mutates nothing. Refusing uncatalogued GETs would break
    every label-taking agent command.
    """
    for sUrl in (
        f"/api/pipeline/{S_CONTAINER_ID}/state",
        f"/api/steps/{S_CONTAINER_ID}/by-label/A01",
    ):
        assert clientAgent.get(sUrl).status_code != 403, sUrl


@pytest.mark.falsification
def testAgentLaneFailsClosedForUnregisteredMutatingRoute(clientAgent):
    """A state-mutating route absent from the catalog is refused.

    Fail-closed is the property that makes this enforcement point
    durable: a route added tomorrow without a catalog entry must be
    invisible to the agent rather than silently reachable.

    Kills: actionCatalog.fbAgentLanePermitsRoute: the unregistered-route
    fallback `return sMethod not in SET_STATE_MUTATING_METHODS` replaced
    by `return True`.
    """
    assert actionCatalog.fbAgentLanePermitsRoute(
        "POST", "/api/invented/{sContainerId}/action",
    ) is False
    responseHttp = clientAgent.post(
        f"/api/invented/{S_CONTAINER_ID}/action", json={},
    )
    assert responseHttp.status_code == 403


def testEveryWebSocketActionIsAgentSafe():
    """The middleware cannot filter WebSocket actions, so none may be user-only.

    ``BaseHTTPMiddleware`` sees only ``http`` scopes; WebSocket actions
    are dispatched inside the socket by ``fnDispatchAction``, well past
    the catalog gate. A user-only WS action would therefore carry a
    ``bAgentSafe: False`` label that nothing enforces — exactly the
    prose-without-an-enforcement-point failure this work removes.
    """
    listUnenforceable = [
        dictEntry["sName"]
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["sMethod"] == "WS" and not dictEntry["bAgentSafe"]
    ]
    assert listUnenforceable == []


def testCatalogAdmitsARouteWhoseAliasIsAgentSafe():
    """One path may host several actions; any agent-safe one admits it."""
    assert actionCatalog.fbAgentLanePermitsRoute(
        "GET", "/api/workflow/{sContainerId}/level3/readiness",
    ) is True


# ── The connect handler consults the owner-of-record ─────────────


def _fresponseConnect(client, sLeaseId=None):
    """POST /api/connect, optionally presenting a lease."""
    dictParams = {"sWorkflowPath": S_WORKFLOW_PATH}
    if sLeaseId is not None:
        dictParams["sLeaseId"] = sLeaseId
    return client.post(
        f"/api/connect/{S_CONTAINER_ID}", params=dictParams,
    )


@pytest.mark.falsification
def testConnectRefusesASessionWithoutTheOwningLease(clientBrowser):
    """A second tab must not connect to a container another session owns.

    ``architecture.md`` claims connect consults the owner-of-record map;
    it consulted nothing, so a second tab could bypass the claim route's
    409 and take the workflow, the project-repo path, and the
    container's agent session. Driven through a live client with the
    container name distinct from the container id, because the owner map
    is name-keyed while the URL carries the id.

    Kills: workflowRoutes._fnRequireOwningLeaseForConnect: the refusal
    `raise HTTPException(409, ...)` replaced by `return`.
    """
    responseFirst = _fresponseConnect(clientBrowser)
    assert responseFirst.status_code == 200
    sLeaseId = responseFirst.json()["sLeaseId"]
    assert sLeaseId

    responseSecondTab = _fresponseConnect(clientBrowser)
    assert responseSecondTab.status_code == 409

    responseForeign = _fresponseConnect(clientBrowser, "someone-elses")
    assert responseForeign.status_code == 409

    responseOwner = _fresponseConnect(clientBrowser, sLeaseId)
    assert responseOwner.status_code == 200


def testConnectOwnerRecordIsKeyedByNameNotId(clientBrowser, appViewer):
    """The record connect creates must be findable by container name."""
    _fresponseConnect(clientBrowser)
    assert S_CONTAINER_NAME in appViewer.state.dictContainerOwners
    assert S_CONTAINER_ID not in appViewer.state.dictContainerOwners


def testConnectIsRefusedOnTheAgentLane(clientAgent):
    """Connect is an excluded path, so the agent can never drive it."""
    responseHttp = _fresponseConnect(clientAgent)
    assert responseHttp.status_code == 403


# ── pull-file writes to the HOST ─────────────────────────────────


@pytest.fixture
def sHomeDirectory(tmp_path, monkeypatch):
    """Point ``~`` at a temporary directory for host-write tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return str(tmp_path)


@pytest.mark.falsification
def testAgentPullMustLandInTheExportDirectory(
    clientAgent, sHomeDirectory,
):
    """An agent-lane pull may not write arbitrary host paths.

    ``docker cp`` runs on the HOST and the agent authors the bytes, so
    an unrestricted destination is agent-authored content landing in a
    shell profile or an authorized-keys file -- host code execution out
    of the sandbox.

    Kills: fileRoutes.fnPullFile: the agent-lane branch
    `if fbRequestRidesAgentLane(requestHttp):` neutralized to
    `if False:`.
    """
    with patch.object(pipelineServer, "_fnDockerCopy"):
        responseEscape = clientAgent.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json={
                "sContainerPath": "/workspace/payload",
                "sHostDestination": os.path.join(
                    sHomeDirectory, ".zshrc"),
            },
        )
    assert responseEscape.status_code == 403
    assert "exports" in responseEscape.text


def testAgentPullIntoTheExportDirectoryIsAllowed(
    clientAgent, sHomeDirectory,
):
    """The capability survives: the agent may still export its outputs."""
    sDestination = os.path.join(
        sHomeDirectory, ".vaibify", "exports",
        S_CONTAINER_ID, "result.csv",
    )
    with patch.object(pipelineServer, "_fnDockerCopy"):
        responseHttp = clientAgent.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json={
                "sContainerPath": "/workspace/result.csv",
                "sHostDestination": sDestination,
            },
        )
    assert responseHttp.status_code == 200


def testBrowserPullIsNotConfinedToTheExportDirectory(
    clientBrowser, sHomeDirectory,
):
    """The researcher's own pull keeps its full home-directory range."""
    with patch.object(pipelineServer, "_fnDockerCopy"):
        responseHttp = clientBrowser.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json={
                "sContainerPath": "/workspace/result.csv",
                "sHostDestination": os.path.join(
                    sHomeDirectory, "Desktop", "result.csv"),
            },
        )
    assert responseHttp.status_code == 200


# ── save-and-run-test writes a caller-supplied path ──────────────


def _fresponseSaveAndRunTest(client, sFilePath):
    """POST save-and-run-test with the given target path."""
    return client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/save-and-run-test",
        json={"sContent": "def test_x(): pass",
              "sFilePath": sFilePath},
    )


@pytest.mark.falsification
def testSaveAndRunTestRefusesDenylistedPaths(clientBrowser):
    """The agent-safe test writer must not reach .git/ or .vaibify/.

    Writing ``.git/hooks/pre-commit`` is code execution on the next
    commit; writing under ``.vaibify/`` defeats the metadata-integrity
    contract the AICS truth system rests on. The generic write route has
    carried both guards for months; this one had neither.

    Kills: testRoutes._fsResolveTestFilePath: the denylist call
    `fnRejectWriteDenylistedPath(sNormalized, sRoot)` replaced by `pass`.
    """
    _fresponseConnect(clientBrowser)
    for sFilePath in (
        ".git/hooks/pre-commit",
        ".vaibify/environment.json",
    ):
        responseHttp = _fresponseSaveAndRunTest(clientBrowser, sFilePath)
        assert responseHttp.status_code == 403, sFilePath


def testSaveAndRunTestRefusesPathsOutsideTheRepo(clientBrowser):
    """A traversing test path is refused rather than written."""
    _fresponseConnect(clientBrowser)
    responseHttp = _fresponseSaveAndRunTest(
        clientBrowser, "../../etc/cron.d/payload")
    assert responseHttp.status_code == 403


def testSaveAndRunTestStillWritesALegitimatePath(clientBrowser):
    """The ordinary case keeps working after the guards were added."""
    _fresponseConnect(clientBrowser)
    responseHttp = _fresponseSaveAndRunTest(
        clientBrowser, "stepA/tests/test_quantitative.py")
    assert responseHttp.status_code == 200


# ── The figure HEAD probe is an existence oracle ─────────────────


@pytest.mark.falsification
def testFigureProbeValidatesTheWorkdirFallback(clientBrowser):
    """HEAD /api/figure must jail its workdir-relative fallback.

    The GET path validates the same fallback, so the HEAD handler's
    omission turned ``test -f`` into an existence oracle over arbitrary
    container paths.

    Kills: figureRoutes._flistBuildFigureCheckPaths: the fallback
    validation `fnValidatePathWithinRoot(sFallback, WORKSPACE_ROOT))`
    replaced by the bare `sFallback)`.
    """
    _fresponseConnect(clientBrowser)
    responseHttp = clientBrowser.head(
        f"/api/figure/{S_CONTAINER_ID}/shadow",
        params={"sWorkdir": "/etc"},
    )
    assert responseHttp.status_code == 403
