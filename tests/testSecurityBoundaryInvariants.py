"""Security / data-integrity boundary invariants the green suite missed.

Each test here pins a *class* of gap that the existing invariants leave
open because they are scoped too narrowly. They are written to FAIL
against the current holes — that failure is the proof they catch the
class — and to PASS once the boundary is enforced.

The four classes, and why the standing suite does not see them:

1. Agent-action governance is checked only on the workflow-VIEWER app
   (``testArchitecturalInvariants.testAgentActionRegistered`` builds
   ``fappCreateApplication``), so every hub-only state-mutating route —
   container build / start / stop, registry claim / release — is
   governed by nobody. ``testHubAppStateMutatingRoutesAreGoverned``
   builds the HUB app and applies the same rule.

2. Container-scoped mutating HTTP routes authorize on the hub-wide
   session token alone, never on the per-container owning lease. A
   second same-hub browser that never claimed a container can mutate
   it. ``testContainerScopedHttpMutationRequiresOwningLease`` drives it
   through a real client (container name != id, per AGENTS.md);
   ``testContainerScopedMutatingRoutesConsultTheLease`` is the source
   invariant that enumerates the class.

3. (lives in ``testBrowserLaneContract.py``) the browser-lane Docker
   fake fails closed on its ordinary exec API but not on its streamed
   exec API.

4. ``workflowMigrations.fiApplyMigrations`` unconditionally stamps the
   current version at the end, silently DOWNGRADING a future-version
   workflow. ``testFutureSchemaVersionIsNotSilentlyDowngraded`` pins it.

The rule for this file mirrors ``testAgentLaneEnforcement``: build the
real application, keep the container name distinct from the container
id, and assert the refusal rather than the happy path.
"""

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.gui import actionCatalog
from vaibify.gui import containerOwnership
from vaibify.gui import pipelineServer
from vaibify.gui import routeScope
from vaibify.gui import workflowMigrations


REPO_ROOT = Path(__file__).resolve().parent.parent

_SET_STATE_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE"})


# ─────────────────────────────────────────────────────────────────
# Gap 1: agent-action governance must cover the HUB app, not only the
# viewer app.
# ─────────────────────────────────────────────────────────────────


def _flistAppStateMutatingRoutes(app):
    """Return sorted (sMethod, sPath) for every state-mutating route."""
    listResult = []
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        for sMethod in sorted(
            _SET_STATE_MUTATING_METHODS & set(route.methods or ())
        ):
            listResult.append((sMethod, route.path))
    return sorted(set(listResult))


def _fappBuildHubApplication():
    """Build the hub FastAPI app with the Docker connection mocked."""
    with patch(
        "vaibify.gui.pipelineServer._fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


def testHubAppStateMutatingRoutesAreGoverned():
    """Every hub state-mutating route is catalogued or excluded.

    THE GENERAL RULE. ``AGENTS.md`` requires that *every* state-mutating
    route a researcher can invoke is either an entry in
    ``LIST_AGENT_ACTIONS`` (agent-invokable) or an explicit member of
    ``SET_INTENTIONALLY_EXCLUDED_PATHS`` (deliberately not). The standing
    invariant ``testAgentActionRegistered`` enforces that rule, but only
    on the workflow-viewer application, so the hub's control-plane routes
    — the ones that build, start, and stop containers, and claim or
    release the lease — are governed by nobody. A hub route added
    tomorrow is invisible to this check and would silently drift out of
    the agent-safety model, exactly the failure the viewer-app invariant
    exists to prevent.

    This test builds the HUB application
    (``pipelineServer.fappCreateHubApplication``) and applies the same
    rule. It currently FAILS, naming ``/api/containers/{sName}/build``,
    ``/start``, ``/stop`` and the registry routes, because none of them
    is registered or excluded.
    """
    app = _fappBuildHubApplication()
    setCatalogPaths = {
        (dictEntry["sMethod"], dictEntry["sPath"])
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["sMethod"] != "WS"
    }
    listUngoverned = [
        (sMethod, sPath)
        for sMethod, sPath in _flistAppStateMutatingRoutes(app)
        if (sMethod, sPath) not in setCatalogPaths
        and (sMethod, sPath)
        not in actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS
    ]
    assert listUngoverned == [], (
        "Hub state-mutating routes governed by neither LIST_AGENT_ACTIONS "
        "nor SET_INTENTIONALLY_EXCLUDED_PATHS (a hub control-plane route "
        "must be one or the other):\n  "
        + "\n  ".join(f"{sMethod} {sPath}" for sMethod, sPath in listUngoverned)
    )


# ─────────────────────────────────────────────────────────────────
# Gap 2: container-scoped HTTP mutations must consult the owning lease,
# not merely the hub-wide session token.
# ─────────────────────────────────────────────────────────────────


S_CONTAINER_ID = "abc123container"
S_CONTAINER_NAME = "owned-container"          # name != id, on purpose
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

_DICT_WORKFLOW = {
    "sWorkflowName": "Test Pipeline",
    "sPlotDirectory": "Plot",
    "listSteps": [
        {
            "sName": "Step A", "sDirectory": "stepA",
            "bRunEnabled": True, "bInteractive": False,
            "saDataCommands": [], "saOutputDataFiles": [],
            "saTestCommands": [], "saPlotCommands": [], "saPlotFiles": [],
            "saInputDataFiles": [], "dictVerification": {},
        },
    ],
}


class _MockDockerConnection:
    """The Docker surface the container-scoped routes touch.

    The reported container *name* deliberately differs from the *id*:
    the owner map is name-keyed while every URL carries the id, and the
    repo has already shipped one fatal bug a name == id fixture hid.
    """

    def __init__(self):
        self._dictFiles = {}

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID, "sShortId": "abc123",
            "sName": S_CONTAINER_NAME, "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if "git rev-parse --show-toplevel" in sCommand:
            return (0, "/workspace\n")
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "pipeline_state" in sCommand:
            return (1, "")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(_DICT_WORKFLOW).encode("utf-8")
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, sContainerId, sPath, baContent,
                    iMode=None, iUid=None, iGid=None):
        self._dictFiles[sPath] = baContent

    def fnWriteFileViaTar(self, sContainerId, sPath, baContent,
                          iMode=None, iUid=None, iGid=None):
        self._dictFiles[sPath] = baContent

    def ftRunInContainerStreamed(self, sContainerId, sCommand,
                                    sWorkdir=None, sUser=None):
        from types import SimpleNamespace
        return SimpleNamespace(iExitCode=0, sStdout="ok", sStderr="")


@pytest.fixture
def appViewer(appViewerAndDocker):
    """The real viewer application over a mocked Docker."""
    return appViewerAndDocker[0]


@pytest.fixture
def appViewerAndDocker():
    """Return ``(app, dockerConnection)`` sharing one mock instance.

    A single ``_MockDockerConnection`` instance is threaded into the app so
    the live test can snapshot ``_dictFiles`` and prove a refused mutation
    wrote nothing.
    """
    connectionDocker = _MockDockerConnection()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    return app, connectionDocker


def _fdictOwnerFromConnect(client):
    """Connect ``client`` as owner; return its lease + the owner map name."""
    responseConnect = client.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseConnect.status_code == 200, responseConnect.text
    return responseConnect.json()["sLeaseId"]


@pytest.mark.falsification
def testContainerScopedHttpMutationRequiresOwningLease(appViewerAndDocker):
    """A container-scoped HTTP mutation must reject a non-owning session.

    THE GENERAL RULE. ``AGENTS.md`` names the per-container lease the
    single exclusivity principal: a container is owned by exactly one
    browser session, recorded in ``dictContainerOwners``, and every access
    must authorize through that lease. The WebSocket gates and the connect
    handler do. Before Sweep B the container-scoped *HTTP* routes did not —
    they were protected only by ``SessionTokenMiddleware``, whose credential
    proves "some valid browser", never "the session that owns THIS
    container", so a second tab that never claimed the container could
    mutate it.

    Driven through live clients with the container name distinct from the
    container id. Session A connects and takes the lease. Session B — a
    genuinely distinct ``BrowserSessionRecord`` credential — then drives a
    container-scoped mutation three ways: with no lease, with a forged
    lease, and with session A's REAL lease copied. All three must be
    refused, the copied-lease case included, because the lease is bound to
    A's session, not merely valid. The container's file state is snapshotted
    and proven unchanged, so the refusal is a true no-write, not a 500 after
    a partial mutation. As a positive control, the owner presenting its own
    credential + lease still succeeds, proving the gate is not blanket-deny.

    Kills: in routeScope.ContainerAwareRoute.get_route_handler, neutralize
    the ``if iCode:`` short-circuit so a refused container-owner request
    falls through to the endpoint body — the non-owning session's mutation
    then succeeds instead of being rejected.
    """
    appViewer, connectionDocker = appViewerAndDocker
    clientOwner = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    sOwningLease = _fdictOwnerFromConnect(clientOwner)
    assert sOwningLease
    # The record must be keyed by NAME, and the id must never be a key —
    # the same distinction the name-vs-id bug turned on.
    assert S_CONTAINER_NAME in appViewer.state.dictContainerOwners
    assert S_CONTAINER_ID not in appViewer.state.dictContainerOwners

    # Session B: a distinct credential (its own bootstrapped session id).
    clientIntruder = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    dictFilesBefore = dict(connectionDocker._dictFiles)
    for dictLeaseHeader in (
        {},
        {"X-Vaibify-Lease": "forged-not-the-owning-lease"},
        {"X-Vaibify-Lease": sOwningLease},  # A's real lease, copied
    ):
        responseMutate = clientIntruder.post(
            f"/api/steps/{S_CONTAINER_ID}/declare-no-input-data",
            headers=dictLeaseHeader, json={},
        )
        assert responseMutate.status_code in (401, 403, 409), (
            "A session that does not OWN the container mutated a "
            "container-scoped route (declare-no-input-data) and got "
            f"{responseMutate.status_code}; the bound owning lease is not "
            f"enforced at the HTTP boundary (lease header={dictLeaseHeader})."
        )
    assert connectionDocker._dictFiles == dictFilesBefore, (
        "A refused container-scoped mutation still wrote to the container; "
        "the authority must short-circuit before the handler body runs."
    )

    # Positive control: the owner, presenting its own lease, is authorized.
    responseOwner = clientOwner.post(
        f"/api/steps/{S_CONTAINER_ID}/declare-no-input-data",
        headers={"X-Vaibify-Lease": sOwningLease}, json={},
    )
    assert responseOwner.status_code == 200, (
        "The owning session was refused its own container — the gate is "
        f"blanket-denying rather than authorizing ({responseOwner.text})."
    )


@pytest.mark.falsification
def testContainerScopedHttpReadRequiresOwningLease(appViewerAndDocker):
    """A container-scoped HTTP read must reject a non-owning session.

    THE GENERAL RULE, extended to reads (the read-enforcement slice of the
    ORPHANED_SESSION foundation). Reading an owned container leaks its
    files, logs, git state, and workflow contents to a session that never
    claimed it — the same boundary violation as a foreign mutation, minus
    the write. ``ContainerAwareRoute`` therefore runs the identical
    bound-lease authority for the ``container-read`` scope that it runs for
    ``container-owner``.

    Driven through live clients with the container name distinct from the
    container id, exactly like the mutation gate above. Session A connects
    and takes the lease. Session B — a genuinely distinct
    ``BrowserSessionRecord`` credential — then drives a container-read GET
    three ways: with no lease, with a forged lease, and with session A's
    REAL lease copied. All three must be refused, and the refusal body must
    carry none of the workflow content the endpoint would have served, so
    the gate is a true no-read, not a leak with an error code. As a
    positive control, the owner presenting its own credential + lease still
    reads its steps, proving the gate is not blanket-deny.

    Kills: in routeScope._SET_LEASE_ENFORCED_SCOPES, drop
    S_SCOPE_CONTAINER_READ from the enforced set — reverting reads to the
    declared-but-unenforced state — and the non-owning session's GET
    succeeds instead of being rejected.
    """
    appViewer, _connectionDocker = appViewerAndDocker
    clientOwner = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    sOwningLease = _fdictOwnerFromConnect(clientOwner)
    assert sOwningLease
    assert S_CONTAINER_NAME in appViewer.state.dictContainerOwners
    assert S_CONTAINER_ID not in appViewer.state.dictContainerOwners

    clientIntruder = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    for dictLeaseHeader in (
        {},
        {"X-Vaibify-Lease": "forged-not-the-owning-lease"},
        {"X-Vaibify-Lease": sOwningLease},  # A's real lease, copied
    ):
        responseRead = clientIntruder.get(
            f"/api/steps/{S_CONTAINER_ID}", headers=dictLeaseHeader,
        )
        assert responseRead.status_code in (401, 403), (
            "A session that does not OWN the container read a "
            "container-scoped route (GET /api/steps) and got "
            f"{responseRead.status_code}; the bound owning lease is not "
            f"enforced on reads (lease header={dictLeaseHeader})."
        )
        assert "Step A" not in responseRead.text, (
            "The refusal response still leaked workflow content to the "
            "non-owning session; the authority must short-circuit before "
            "the handler body runs."
        )

    # Positive control: the owner, presenting its own lease, reads freely.
    responseOwner = clientOwner.get(
        f"/api/steps/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": sOwningLease},
    )
    assert responseOwner.status_code == 200, (
        "The owning session was refused its own container read — the gate "
        f"is blanket-denying rather than authorizing ({responseOwner.text})."
    )
    assert "Step A" in responseOwner.text


# ---- source invariant: enforcement is mechanical, not inspected ------


def _flistApplicableMutatingRoutes(app):
    """Return the state-mutating ``APIRoute`` objects on ``app``.

    The enumeration policy the isinstance invariant rests on: only
    :class:`APIRoute` instances carrying a state-mutating method are
    "applicable". WebSocket routes (no ``methods``), static ``Mount``\\ s
    (no ``endpoint``), and FastAPI's read-only ``/openapi.json`` and docs
    routes are classified out — the first two are not ``APIRoute``\\ s, the
    last carry only ``GET``/``HEAD``.
    """
    listRoutes = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        setMethods = set(getattr(route, "methods", None) or ())
        if actionCatalog.SET_STATE_MUTATING_METHODS & setMethods:
            listRoutes.append(route)
    return listRoutes


def _ftBuildBothApplications():
    """Return ``(viewerApp, hubApp)`` over a mocked Docker connection."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", _MockDockerConnection,
    ):
        appViewer = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    with patch(
        "vaibify.gui.pipelineServer._fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    return appViewer, appHub


def testContainerScopedMutatingRoutesConsultTheLease():
    """Every mutating route is a ``ContainerAwareRoute`` — a runtime invariant.

    THE GENERAL RULE, proven mechanically rather than by source inspection.
    ``app.router.route_class`` governs only routes registered AFTER it is
    assigned, so an ordering slip would silently leave a mutating route
    served by a plain ``APIRoute`` that never consults the owning lease.
    Asserting that every applicable route IS a
    :class:`routeScope.ContainerAwareRoute` instance turns that ordering
    accident into a loud failure. Both the viewer and the hub application
    are checked, because the hub's control-plane routes were governed by
    nobody under the old viewer-only invariants.
    """
    for app in _ftBuildBothApplications():
        listRoutes = _flistApplicableMutatingRoutes(app)
        assert listRoutes, "no mutating routes enumerated — policy is wrong"
        listPlain = [
            (sorted(route.methods), route.path)
            for route in listRoutes
            if not isinstance(route, routeScope.ContainerAwareRoute)
        ]
        assert listPlain == [], (
            "Mutating routes served by a plain APIRoute, so the "
            "container-owner authority never runs for them (route_class "
            "was assigned too late, or a router was built separately):\n  "
            + "\n  ".join(f"{listM} {sPath}" for listM, sPath in listPlain)
        )
        _fnAssertEveryMutatingRouteResolvesToItsDeclaredScope(listRoutes)


def _fnAssertEveryMutatingRouteResolvesToItsDeclaredScope(listRoutes):
    """Prove each mutating route's RESOLVED scope routes it to an authority.

    ``isinstance(route, ContainerAwareRoute)`` proves the wrapper is
    installed, but not that the wrapper's scope RESOLUTION sends the route
    through the lease authority — a browser-hub control-plane route is a
    ``ContainerAwareRoute`` too, yet bypasses the lease. So for every
    mutating route this asserts the resolved scope is exactly what its class
    demands:

    * an explicit ``@fnRouteScope`` stamp (e.g. the owner-establishing
      connect route) must resolve to that stamped scope;
    * a ``{sContainerId}`` route with no stamp must resolve to
      ``container-owner`` — the scope ``ContainerAwareRoute`` actually
      enforces — so a mis-resolution that quietly drops it out of the
      authority fails here;
    * a control-plane route must resolve to the scope ENUMERATED for it in
      ``DICT_CONTROL_PLANE_SCOPES`` — the browser-hub classification is an
      asserted allowlist, never an implied default, so a new mutating
      control-plane route added without classifying it fails here.
    """
    for route in listRoutes:
        dictResolved = routeScope.fdictResolveRouteScope(
            route.methods, route.path, route.endpoint,
        )
        assert dictResolved is not None, (
            f"{sorted(route.methods)} {route.path} resolves to no "
            "authorization scope; a mutating route with no scope ships "
            "unauthorized"
        )
        dictExplicit = getattr(route.endpoint, "_dictRouteScope", None)
        if dictExplicit is not None:
            assert dictResolved["sScope"] == dictExplicit["sScope"], (
                f"{route.path} carries an explicit scope "
                f"{dictExplicit['sScope']} but resolved to "
                f"{dictResolved['sScope']}"
            )
            continue
        if "{sContainerId}" in route.path:
            assert dictResolved["sScope"] == (
                routeScope.S_SCOPE_CONTAINER_OWNER
            ), (
                f"{route.path} carries {{sContainerId}} but resolved to "
                f"{dictResolved['sScope']}, not container-owner, so "
                "ContainerAwareRoute never runs the lease authority for it"
            )
            continue
        listDeclared = [
            (sMethod, route.path) for sMethod in route.methods
            if (sMethod, route.path) in routeScope.DICT_CONTROL_PLANE_SCOPES
        ]
        assert listDeclared, (
            f"{sorted(route.methods)} {route.path} is a mutating "
            "control-plane route absent from DICT_CONTROL_PLANE_SCOPES; "
            "classify it there so the browser-hub scope is an asserted "
            "allowlist, not an implied default"
        )
        for sMethod, sPath in listDeclared:
            assert dictResolved["sScope"] == (
                routeScope.DICT_CONTROL_PLANE_SCOPES[(sMethod, sPath)]
            ), (
                f"{sMethod} {sPath} resolved to {dictResolved['sScope']} "
                "but is enumerated as "
                f"{routeScope.DICT_CONTROL_PLANE_SCOPES[(sMethod, sPath)]}"
            )


def testUnscopedMutatingRouteFailsAppConstruction():
    """A mutating route with no declared scope must fail construction.

    Default-deny made mechanical: a route that is neither a
    ``{sContainerId}`` container route, an explicit ``@fnRouteScope``, nor a
    named control-plane entry has no authorization scope, so it would ship
    unauthorized. :func:`routeScope.fnValidateRouteScopesOrRaise` refuses to
    build such an app. A properly scoped route passes the same check.
    """
    appUnscoped = FastAPI()
    appUnscoped.router.route_class = routeScope.ContainerAwareRoute

    @appUnscoped.post("/api/rogue-control-plane-action")
    async def fnRogue():
        return {"ok": True}

    with pytest.raises(RuntimeError, match="no declared authorization scope"):
        routeScope.fnValidateRouteScopesOrRaise(appUnscoped)

    appScoped = FastAPI()
    appScoped.router.route_class = routeScope.ContainerAwareRoute

    @appScoped.post("/api/steps/{sContainerId}/probe")
    async def fnScoped(sContainerId: str):
        return {"ok": True}

    routeScope.fnValidateRouteScopesOrRaise(appScoped)  # must not raise


def testContainerAwareRouteRunsAuthorityBeforeEndpoint(monkeypatch):
    """The route wrapper must run the authority BEFORE the endpoint body.

    A gate that runs after the side effect is no gate. This drives a real
    ``ContainerAwareRoute`` on a container-owner path: when the authority
    refuses, the endpoint body must never execute and the client sees the
    refusal; when it allows, the body runs.
    """
    listCalls = []

    async def fnEndpoint(sContainerId: str):
        listCalls.append("endpoint")
        return {"ok": True}

    app = FastAPI()
    app.router.route_class = routeScope.ContainerAwareRoute
    app.post("/api/steps/{sContainerId}/probe")(fnEndpoint)
    app.state.dictContainerOwners = {}
    app.state.dictBrowserSessions = {}
    client = TestClient(app)

    monkeypatch.setattr(routeScope, "fiAuthorizeContainerHttp", lambda *a: 403)
    responseRefused = client.post("/api/steps/anything/probe")
    assert responseRefused.status_code == 403
    assert listCalls == [], "endpoint body ran despite an authority refusal"

    monkeypatch.setattr(routeScope, "fiAuthorizeContainerHttp", lambda *a: 0)
    responseAllowed = client.post("/api/steps/anything/probe")
    assert responseAllowed.status_code == 200
    assert listCalls == ["endpoint"]


def testContainerReadScopeIsAFrozenRatchetedAllowlist():
    """No route may adopt the ``container-read`` scope off-list.

    Owned-container GETs RESOLVE to ``container-read`` by the
    GET/{sContainerId} convention (now lease-ENFORCED by
    ``ContainerAwareRoute``), so the ratchet is checked against the resolved
    scope, not merely an explicit stamp. Every route that resolves to
    ``container-read`` must be enumerated in the frozen
    ``SET_CONTAINER_READ_ROUTES``; a NEW owned-container GET fails this
    check until it is acknowledged there — the machine tripwire that makes
    adopting the enforced scope a deliberate, reviewed act.
    """
    for app in _ftBuildBothApplications():
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            dictScope = routeScope.fdictResolveRouteScope(
                route.methods, route.path, route.endpoint,
            )
            if dictScope is None:
                continue
            if dictScope["sScope"] != routeScope.S_SCOPE_CONTAINER_READ:
                continue
            for sMethod in route.methods:
                assert (sMethod, route.path) in (
                    routeScope.SET_CONTAINER_READ_ROUTES
                ), (
                    f"{sMethod} {route.path} resolves to container-read but "
                    "is not in the frozen SET_CONTAINER_READ_ROUTES "
                    "allowlist; add it there to acknowledge the new "
                    "owned-container read."
                )


# ─────────────────────────────────────────────────────────────────
# Gap 4: a future schema version must fail closed, never be downgraded.
# ─────────────────────────────────────────────────────────────────


def testFutureSchemaVersionIsNotSilentlyDowngraded():
    """A workflow newer than this build must not be silently downgraded.

    The falsification-of-record for this behaviour is the canonical,
    registered ``testWorkflowSchemaForwardCompat.py::
    test_future_schema_version_is_refused_not_downgraded`` (same mutation,
    stronger ``pytest.raises`` assertion). This copy stays as a plain
    regression check inside the security bundle; it carries no
    ``falsification`` marker so it does not claim a second, redundant
    registry entry for the identical mutant.

    THE GENERAL RULE. ``fiApplyMigrations`` runs forward migrators until
    the dict reaches ``I_CURRENT_WORKFLOW_VERSION`` and then
    unconditionally stamps that version. When the input's version already
    EXCEEDS the current — a project.json written by a newer vaibify,
    opened by an older one — no migrator runs, but the final stamp still
    rewrites the version field DOWNWARD. The document is then treated as
    this build's schema and, on the next save, persisted with the lower
    number and whatever fields this build does not understand silently
    dropped. Forward compatibility must fail closed: an unknown-future
    workflow is refused or left untouched, never quietly downgraded.

    Reproduced and pinned here: a version ``I_CURRENT + 1`` document is
    passed through ``fiApplyMigrations``; today the version field comes
    back as ``I_CURRENT`` (a downgrade) and the dict is mutated, so this
    FAILS. A fix that leaves the dict untouched — or raises a refusal —
    makes it pass. (Raising is an equally valid fix; this asserts the
    weaker, sufficient property: no silent downgrade.)
    """
    iFutureVersion = workflowMigrations.I_CURRENT_WORKFLOW_VERSION + 1
    dictFuture = {
        workflowMigrations.S_VERSION_KEY: iFutureVersion,
        "sWorkflowName": "From a newer vaibify",
        "listSteps": [],
        "sFieldThisBuildDoesNotKnow": "must not be dropped",
    }
    dictBefore = copy.deepcopy(dictFuture)

    try:
        iReturned = workflowMigrations.fiApplyMigrations(dictFuture)
    except (ValueError, RuntimeError):
        # Refusing to touch a future schema is an acceptable fix.
        return

    assert dictFuture.get(workflowMigrations.S_VERSION_KEY) >= iFutureVersion, (
        "fiApplyMigrations silently DOWNGRADED a future-version workflow "
        f"from {iFutureVersion} to "
        f"{dictFuture.get(workflowMigrations.S_VERSION_KEY)}; a newer "
        "project.json opened by an older build must fail closed, not be "
        "re-stamped to this build's schema."
    )
    assert iReturned >= iFutureVersion, (
        f"fiApplyMigrations reported version {iReturned} for a "
        f"{iFutureVersion} workflow — the future version was lost."
    )
    assert dictFuture == dictBefore, (
        "fiApplyMigrations mutated a future-version workflow it does not "
        "understand; unknown-future documents must be left untouched. "
        f"before={dictBefore} after={dictFuture}"
    )
