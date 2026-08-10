"""The carrier-mode declaration mechanism, and the ratchet that retires it.

Migration plan phase 1c, rules R1, R2 and R6. Three claims are checked
here, and the first one is the one the whole exercise turns on.

**A declaration authorizes NOTHING (R1).** The modes are behavioural
protocols, not labels. A route that DECLARES is served on the enforced
branch, which mints no admission at all: its handler must open one per
logical mutation through a carrier, and forgetting raises
``MutationNotAdmittedError`` at the primitive. That refusal IS the
proof, and a decorator that pre-admitted the handler would delete it --
which is the ``bAgentSafe`` mistake one level up, and this repository
has shipped that once already. So the claim is not asserted in prose;
:func:`testDeclaringMintsNoAdmission` drives a declared route over real
HTTP and watches the real primitive gate refuse it.

**Every container-scoped entry point declares, or is recorded as
awaiting (R2/R6).** The live population is resolved from the application
rather than from a list somebody maintains, so a route that acquires a
container scope arrives here whatever anyone remembered to write down.

**The allow-list may only SHRINK (R6).** The seeded record below is a
second, independently-edited copy of what was seeded: shrinking the
source set needs one edit, GROWING it needs two, and the second one is
this file. That duplication is the mechanism, not an oversight.

THE POPULATION IS 130 HERE AND 132 IN THE PLAN, AND BOTH ARE RIGHT
------------------------------------------------------------------

``fdictResolveRouteScope`` classifies 132 routes into the authorized
container scopes. Two of them -- ``/ws/pipeline/{sContainerId}`` and
``/ws/terminal/{sContainerId}`` -- are ``APIWebSocketRoute`` instances,
and ``app.router.route_class`` governs ``APIRoute`` only, so
``ContainerAwareRoute`` never serves them and they take NEITHER branch.
They are gated by ``webSocketAuthorization`` instead. Seeding them into
an allow-list that grants an HTTP admission would record something
false, and they could never be migrated out of it either. So the
allow-list holds the 130 routes this route class actually serves, the
two WebSocket routes are named HERE, and
:func:`testTheDeclaringPopulationPartitionsWithNothingLeftOver` asserts
the three records add back up to the 132 -- a route may leave the
declaring population only by being written down.

The record lives in this file rather than beside the allow-list because
``testOnlyTheWithdrawnHandlerServesTheTerminalWebSocket`` scans
PRODUCTION source for the withdrawn terminal socket's path and, quite
correctly, cannot tell a record from a handler. Naming the path in a
production module to document it would have weakened a parking control
that exists because a shell can ``setsid`` out of its containment.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from vaibify.config import mutationAdmission
from vaibify.gui import browserSession, routeScope
from vaibify.gui.appFactory import fappCreateHubApplication
from vaibify.gui.containerOwnership import OwnerRecord


# name != id throughout. The owner map is keyed by NAME while the route
# resolves by ID, and this repository shipped a fatal bug under a fully
# green suite whose fixtures used name == id (AGENTS.md, "Lessons").
S_CONTAINER_NAME = "declarationproject"
S_CONTAINER_ID = "declarationcontainerid42"
S_CREDENTIAL = "credential-for-declaration-tests"
S_SESSION_ID = "session-for-declaration-tests"
S_LEASE_ID = "lease-for-declaration-tests"

DICT_OWNER_HEADERS = {
    "x-session-token": S_CREDENTIAL,
    routeScope.S_LEASE_HEADER_NAME: S_LEASE_ID,
}

# A path that IS in the allow-list, used to drive the ambient branch
# against a real entry rather than a fabricated one. Deliberately a
# ``container-read`` GET: phase 2 migrates every MUTATING route out of
# the allow-list, so any POST named here becomes wrong the moment it is
# migrated -- and the entry pointed at the pipeline kill route until
# that migration made it wrong. The 46 container-read GETs stay
# awaiting by decision (2026-08-05), so this one cannot go stale.
S_AWAITING_METHOD = "GET"
S_AWAITING_PATH = "/api/logs/{sContainerId}"

# Container-scoped by the {sContainerId} convention, but served by the
# WebSocket authority rather than by ContainerAwareRoute. See the module
# docstring for why this record is here and not beside the allow-list.
SET_CONTAINER_SCOPED_WEBSOCKET_ROUTES = frozenset({
    "/ws/pipeline/{sContainerId}",
    "/ws/terminal/{sContainerId}",
})


# The 130 routes the allow-list was SEEDED with, transcribed here as the
# ratchet's second copy. Adding a route to
# ``routeScope.SET_ROUTES_AWAITING_CARRIER_MODE`` without also adding it
# here fails ``testTheAwaitingAllowListMayOnlyShrink``; removing one (a
# Phase 2 migration) needs no edit here at all, which is the asymmetry
# R6 asks for.
SET_SEEDED_ROUTES_AWAITING_CARRIER_MODE = frozenset({
    ("DELETE", "/api/draft/{sContainerId}/{sFilePath:path}"),
    ("DELETE", "/api/overleaf/{sContainerId}/mirror"),
    ("DELETE", "/api/steps/{sContainerId}/{iStepIndex}"),
    ("DELETE", "/api/steps/{sContainerId}/{iStepIndex}/generated-test"),
    ("GET", "/api/containers/{sContainerId}/isolation"),
    ("GET", "/api/containers/{sContainerId}/ready"),
    ("GET", "/api/draft/{sContainerId}/{sFilePath:path}"),
    ("GET", "/api/drafts/{sContainerId}"),
    ("GET", "/api/figure/{sContainerId}/{sFilePath:path}"),
    ("GET", "/api/files/{sContainerId}/download/{sFilePath:path}"),
    ("GET", "/api/files/{sContainerId}/{sDirectoryPath:path}"),
    ("GET", "/api/git/{sContainerId}/badges"),
    ("GET", "/api/git/{sContainerId}/manifest-check"),
    ("GET", "/api/git/{sContainerId}/status"),
    ("GET", "/api/logs/{sContainerId}"),
    ("GET", "/api/logs/{sContainerId}/{sLogFilename}"),
    ("GET", "/api/monitor/{sContainerId}"),
    ("GET", "/api/overleaf/{sContainerId}/mirror/tree"),
    ("GET", "/api/pipeline/{sContainerId}/file-status"),
    ("GET", "/api/pipeline/{sContainerId}/host-log-tail"),
    ("GET", "/api/pipeline/{sContainerId}/state"),
    ("GET", "/api/pipeline/{sContainerId}/workflow-discovery"),
    ("GET", "/api/repos/{sContainerId}/{sRepoName}/dirty-files"),
    ("GET", "/api/settings/{sContainerId}"),
    ("GET", "/api/steps/{sContainerId}"),
    ("GET", "/api/steps/{sContainerId}/by-label/{sLabel}"),
    ("GET", "/api/steps/{sContainerId}/resolve-commands"),
    ("GET", "/api/steps/{sContainerId}/validate"),
    ("GET", "/api/steps/{sContainerId}/{iStepIndex}"),
    ("GET", "/api/steps/{sContainerId}/{iStepIndex}/falsification"),
    ("GET", "/api/sync/{sContainerId}/check/{sService}"),
    ("GET", "/api/sync/{sContainerId}/files"),
    ("GET", "/api/sync/{sContainerId}/has-credential/{sService}"),
    ("GET", "/api/sync/{sContainerId}/reverify-schedule"),
    ("GET", "/api/sync/{sContainerId}/scripts"),
    ("GET", "/api/sync/{sContainerId}/status"),
    ("GET", "/api/sync/{sContainerId}/{sService}/status"),
    ("GET", "/api/workflow/{sContainerId}/dag"),
    ("GET", "/api/workflow/{sContainerId}/dag/export"),
    ("GET", "/api/workflow/{sContainerId}/level2/readiness"),
    ("GET", "/api/workflow/{sContainerId}/level3/attestation"),
    ("GET", "/api/workflow/{sContainerId}/level3/readiness"),
    ("GET", "/api/workflow/{sContainerId}/manifest/text"),
    ("GET", "/api/workflow/{sContainerId}/project-context"),
    ("GET", "/api/workflow/{sContainerId}/prompt-record/status"),
    ("GET", "/api/workflows/{sContainerId}"),
    ("GET", "/api/zenodo/{sContainerId}/deposit"),
    ("GET", "/api/zenodo/{sContainerId}/metadata"),
    ("HEAD", "/api/figure/{sContainerId}/{sFilePath:path}"),
    ("POST", "/api/files/{sContainerId}/pull"),
    ("POST", "/api/files/{sContainerId}/upload"),
    ("POST", "/api/git/{sContainerId}/commit-canonical"),
    ("POST", "/api/git/{sContainerId}/fetch-project-repo"),
    ("POST", "/api/git/{sContainerId}/pull-project-repo"),
    ("POST", "/api/git/{sContainerId}/reconcile-remote-state"),
    ("POST", "/api/git/{sContainerId}/refresh-remotes"),
    ("POST", "/api/git/{sContainerId}/untrack-ai-declaration"),
    ("POST", "/api/github/{sContainerId}/add-file"),
    ("POST", "/api/github/{sContainerId}/identity"),
    ("POST", "/api/overleaf/{sContainerId}/diff"),
    ("POST", "/api/overleaf/{sContainerId}/mirror/refresh"),
    ("POST", "/api/overleaf/{sContainerId}/pull-manuscript"),
    ("POST", "/api/pipeline/{sContainerId}/acknowledge-step/{iStepIndex}"),
    ("POST", "/api/pipeline/{sContainerId}/clean"),
    ("POST", "/api/pipeline/{sContainerId}/kill"),
    ("POST", "/api/steps/{sContainerId}/create"),
    ("POST", "/api/steps/{sContainerId}/declare-no-input-data"),
    ("POST", "/api/steps/{sContainerId}/insert/{iPosition}"),
    ("POST", "/api/steps/{sContainerId}/reorder"),
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/compare-plot"),
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/generate-test"),
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/input-data"),
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/scan-dependencies"),
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/scan-scripts"),
    ("POST", "/api/sync/{sContainerId}/arxiv/configure"),
    ("POST", "/api/sync/{sContainerId}/setup"),
    ("POST", "/api/sync/{sContainerId}/track"),
    ("POST", "/api/sync/{sContainerId}/{sService}/verify"),
    ("POST", "/api/workflow/{sContainerId}/ai-declaration/add-step"),
    ("POST", "/api/workflow/{sContainerId}/ai-models/declare"),
    ("POST", "/api/workflow/{sContainerId}/ai-models/remove"),
    ("POST", "/api/workflow/{sContainerId}/personal-layer/declare"),
    ("POST",
     "/api/workflow/{sContainerId}/prompt-record/approve-first-capture"),
    ("POST", "/api/workflow/{sContainerId}/prompt-record/configure"),
    ("POST", "/api/workflow/{sContainerId}/supervision/configure"),
    ("POST", "/api/workflows/{sContainerId}/create"),
    ("POST", "/api/workflows/{sContainerId}/request-creation"),
    ("POST", "/api/zenodo/{sContainerId}/archive"),
    ("POST", "/api/zenodo/{sContainerId}/download"),
    ("POST", "/api/zenodo/{sContainerId}/metadata"),
    ("PUT", "/api/draft/{sContainerId}/{sFilePath:path}"),
    ("PUT", "/api/file/{sContainerId}/{sFilePath:path}"),
    ("PUT", "/api/settings/{sContainerId}"),
})


# ---------------------------------------------------------------------
# Resolving the live population, and a real owned application to drive.
# ---------------------------------------------------------------------

def _flistResolveContainerScopedRoutes(app):
    """Return ``(route, dictScope)`` for every container-scoped route."""
    listResolved = []
    for route in app.routes:
        fnEndpoint = getattr(route, "endpoint", None)
        if fnEndpoint is None:
            continue
        dictScope = routeScope.fdictResolveRouteScope(
            getattr(route, "methods", None), route.path, fnEndpoint,
        )
        if dictScope is None:
            continue
        if dictScope["sScope"] in (
            routeScope._SET_AUTHORIZED_CONTAINER_SCOPES
        ):
            listResolved.append((route, dictScope))
    return listResolved


def _fsetRouteKeys(route):
    """Return the ``(sMethod, sPath)`` keys one route occupies."""
    return {
        (sMethod, route.path)
        for sMethod in (getattr(route, "methods", None) or ())
    }


def fappBuildOwnedApplication(listtRoutes, sMethod="POST"):
    """Return an app whose single container is genuinely owned.

    A real credential, a real bound lease, and an owner map keyed by a
    name that differs from the container id -- so the authority, the
    lane tuple and the ambient mint are all exercised for real rather
    than stubbed past. Shared with
    ``tests/testCarrierIntentAudit.py``, which drives the same real
    lanes to produce the observations it compares.

    ``sMethod`` exists because the allow-list's surviving members are
    all GETs; the ambient branch is method-blind, but allow-list
    membership is keyed by ``(method, path)``, so driving a real
    awaiting entry means registering the verb it is recorded under.
    """
    app = FastAPI()
    app.router.route_class = routeScope.ContainerAwareRoute
    for sPath, fnHandler in listtRoutes:
        app.router.api_route(sPath, methods=[sMethod])(fnHandler)
    app.state.dictContainerOwners = {
        S_CONTAINER_NAME: OwnerRecord(
            sLeaseId=S_LEASE_ID, fileHandleLock=None,
            sAgentToken="agent-for-declaration-tests",
            sContainerId=S_CONTAINER_ID, sBrowserSessionId=S_SESSION_ID,
            iOwnerGeneration=1,
        ),
    }
    app.state.dictBrowserSessions = {
        "dictCapabilities": {},
        "dictSessionsByCredential": {
            S_CREDENTIAL: browserSession.BrowserSessionRecord(
                sSessionId=S_SESSION_ID, sCredential=S_CREDENTIAL,
                fCreatedMonotonic=0.0, fLastSeenMonotonic=0.0,
            ),
        },
    }
    return app


def _fnBuildLaneReporter():
    """Return a FRESH handler reporting the lane it was served on.

    Fresh per route on purpose: the declaration is stamped onto the
    function OBJECT, so a handler shared between two registrations would
    carry one route's stamp into the other -- and the awaiting route
    would silently be tested as a declared one.

    The gate it consults is the REAL one
    (``mutationAdmission.fnAssertContainerWriteAdmitted``), the same
    call the container write funnel makes, so "no admission" is
    demonstrated by a refusal rather than by reading a variable.
    """
    async def fnReportTheLaneItWasServedOn(
        sContainerId: str, request: Request,
    ):
        del request
        admission = mutationAdmission.fadmissionActiveForContainerId(
            sContainerId,
        )
        sRefusal = ""
        try:
            mutationAdmission.fnAssertContainerWriteAdmitted(
                sContainerId, "fnWriteFileViaTar",
            )
        except mutationAdmission.MutationNotAdmittedError as errorRefused:
            sRefusal = str(errorRefused)
        return {
            "bLaneEnforced": mutationAdmission.fbLaneEnforced(),
            "sAdmissionMode": "" if admission is None else admission.sMode,
            "sRefusal": sRefusal,
        }
    return fnReportTheLaneItWasServedOn


def _fdictDriveOneRoute(sPath, tupleDeclarations, sMethod="POST"):
    """Call one freshly built owned route; return its response body."""
    fnHandler = _fnBuildLaneReporter()
    if tupleDeclarations:
        routeScope.ffnDeclareCarrierMode(*tupleDeclarations)(fnHandler)
    app = fappBuildOwnedApplication([(sPath, fnHandler)], sMethod)
    client = TestClient(app)
    response = client.request(
        sMethod,
        sPath.replace("{sContainerId}", S_CONTAINER_ID),
        headers=DICT_OWNER_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------
# R1 — a declaration authorizes nothing.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testDeclaringMintsNoAdmission():
    """A declared route is served with NO admission, and is refused.

    The whole of R1 in one assertion pair. The declared route runs on
    the enforced lane -- so the primitive gate is live -- and holds no
    admission, so the real write gate raises. An identical request to a
    route that is still AWAITING is admitted under the ambient
    ``request`` mint, which is what makes this a contrast rather than a
    coincidence: the only difference between the two is the stamp.

    Kills: pre-admitting a declared route by making
    _fbServeOnAmbientAdmission unconditional.
    """
    dictDeclared = _fdictDriveOneRoute(
        "/api/steps/{sContainerId}/probe-declared",
        (routeScope.S_CARRIER_MODE_A_SYNCHRONOUS,),
    )
    assert dictDeclared["bLaneEnforced"] is True, (
        "a declared route left the enforced lane, so the primitive gate "
        "no longer examines anything it does"
    )
    assert dictDeclared["sAdmissionMode"] == "", (
        "declaring minted an admission of mode "
        f"{dictDeclared['sAdmissionMode']!r}; a declaration authorizes "
        "nothing, and one that pre-admits deletes the refusal that is "
        "the migration's only proof a carrier call was not forgotten"
    )
    assert "commit-guard admission" in dictDeclared["sRefusal"], (
        "the real container-write gate did not refuse a declared route "
        f"that opened no carrier: {dictDeclared['sRefusal']!r}"
    )

    dictAwaiting = _fdictDriveOneRoute(
        S_AWAITING_PATH, (), S_AWAITING_METHOD,
    )
    assert dictAwaiting["sAdmissionMode"] == (
        mutationAdmission.S_ADMISSION_MODE_REQUEST
    )
    assert dictAwaiting["sRefusal"] == "", (
        "an awaiting route lost the ambient admission, which is a "
        "behaviour change phase 1c must not make"
    )


@pytest.mark.falsification
def testARouteNeitherDeclaredNorAwaitingFailsClosed():
    """A route nobody recorded gets the enforced lane, not the mint.

    The allow-list is what GRANTS the legacy admission; the absence of a
    declaration is not. Were it the other way round, every route added
    after the seeding would inherit the ambient mint by being forgotten
    -- silently, and for exactly as long as nobody looked.

    Kills: treating an unrecorded route as awaiting in
    fbRouteAwaitsCarrierMode.
    """
    dictUnlisted = _fdictDriveOneRoute(
        "/api/steps/{sContainerId}/probe-never-recorded", (),
    )
    assert dictUnlisted["bLaneEnforced"] is True
    assert dictUnlisted["sAdmissionMode"] == ""
    assert dictUnlisted["sRefusal"] != "", (
        "a container-scoped route that appears in neither record was "
        "handed the ambient admission"
    )


# ---------------------------------------------------------------------
# R2 / R6 — the population, the ratchet, and the partition.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testEveryContainerScopedRouteEitherDeclaresOrIsRecordedAsAwaiting():
    """No container-scoped route may be silent about its carrier mode.

    Resolved from the live application, so a route that acquires a
    container scope arrives here regardless of what anyone wrote down.
    R2 requires the declaration from EVERY container-scoped entry point,
    not only the ones already known to mutate: inferring that a new
    route mutates -- five calls down a shared helper -- is the hard
    problem this exists to solve, so demanding declarations only from
    known mutators would be circular.

    Kills: removing a route from SET_ROUTES_AWAITING_CARRIER_MODE
    without giving it a declaration.
    """
    listUnaccounted = []
    for route, _ in _flistResolveContainerScopedRoutes(
        fappCreateHubApplication(),
    ):
        if not isinstance(route, APIRoute):
            continue
        if routeScope.ftResolveCarrierDeclaration(route.endpoint):
            continue
        if routeScope.fbRouteAwaitsCarrierMode(route.methods, route.path):
            continue
        listUnaccounted.append(sorted(_fsetRouteKeys(route)))
    assert listUnaccounted == [], (
        f"container-scoped routes with no carrier declaration and no "
        f"entry in SET_ROUTES_AWAITING_CARRIER_MODE: {listUnaccounted}. "
        "Declare the route with @ffnDeclareCarrierMode and write the "
        "carrier calls its declaration names; do not add it to the "
        "allow-list, which may only shrink."
    )


@pytest.mark.falsification
def testTheAwaitingAllowListMayOnlyShrink():
    """R6's ratchet: the retirement is one-way.

    132 individually-testable steps with a measurement at each is the
    whole reason the ambient mint retires behind an allow-list instead
    of a flag-day. A list that can grow is not a ratchet -- it is a
    place to put anything inconvenient.

    Kills: adding an entry to SET_ROUTES_AWAITING_CARRIER_MODE.
    """
    setAdded = (
        routeScope.SET_ROUTES_AWAITING_CARRIER_MODE
        - SET_SEEDED_ROUTES_AWAITING_CARRIER_MODE
    )
    assert setAdded == set(), (
        f"SET_ROUTES_AWAITING_CARRIER_MODE grew by {sorted(setAdded)}. "
        "It may only shrink: a route that is not already on it must "
        "declare a carrier mode and carry its mutations through a "
        "carrier, never rejoin the legacy ambient admission."
    )


def testNoRouteBothDeclaresAndIsRecordedAsAwaiting():
    """A migrated route leaves the allow-list in the same change.

    The two records answer the same question, so a route in both states
    is a contradiction -- and a load-bearing one: the route class
    resolves it in favour of the declaration, so the route would run
    enforced while the record still says it holds the ambient mint, and
    the allow-list's count would stop meaning what phase 4 reads it to
    mean.
    """
    listContradictory = []
    for route, _ in _flistResolveContainerScopedRoutes(
        fappCreateHubApplication(),
    ):
        if not routeScope.ftResolveCarrierDeclaration(route.endpoint):
            continue
        setOverlap = _fsetRouteKeys(route) & (
            routeScope.SET_ROUTES_AWAITING_CARRIER_MODE
        )
        if setOverlap:
            listContradictory.append(sorted(setOverlap))
    assert listContradictory == [], (
        f"routes that declare a carrier mode AND are still recorded as "
        f"awaiting one: {listContradictory}. Remove them from "
        "SET_ROUTES_AWAITING_CARRIER_MODE in the change that declares "
        "them."
    )


def testTheDeclaringPopulationPartitionsWithNothingLeftOver():
    """The 132 split into three records, and nothing falls between them.

    The plan counts 132 container-scoped routes; this route class serves
    130 of them. The other two are WebSocket routes an ``APIRoute``
    route class cannot govern. Asserting the partition rather than the
    130 is what keeps that from becoming two routes quietly leaving the
    record: dropping either from
    SET_CONTAINER_SCOPED_WEBSOCKET_ROUTES fails here, and so does a
    THIRD container-scoped socket appearing, which would be a route
    carrying a container scope that no branch of this mechanism reaches.
    """
    listResolved = _flistResolveContainerScopedRoutes(
        fappCreateHubApplication(),
    )
    setWebSocketPaths = {
        route.path for route, _ in listResolved
        if not isinstance(route, APIRoute)
    }
    assert setWebSocketPaths == SET_CONTAINER_SCOPED_WEBSOCKET_ROUTES, (
        "the container-scoped routes this route class cannot serve are "
        f"{sorted(setWebSocketPaths)}, but the record names "
        f"{sorted(SET_CONTAINER_SCOPED_WEBSOCKET_ROUTES)}"
    )
    setServedKeys = set()
    setDeclaredKeys = set()
    for route, _ in listResolved:
        if not isinstance(route, APIRoute):
            continue
        setServedKeys |= _fsetRouteKeys(route)
        if routeScope.ftResolveCarrierDeclaration(route.endpoint):
            setDeclaredKeys |= _fsetRouteKeys(route)
    setStale = routeScope.SET_ROUTES_AWAITING_CARRIER_MODE - setServedKeys
    assert setStale == set(), (
        f"SET_ROUTES_AWAITING_CARRIER_MODE names routes this class does "
        f"not serve: {sorted(setStale)}. A stale entry can never be "
        "migrated away, so the list could not reach empty and phase 4 "
        "would never fire."
    )
    setAccounted = (
        routeScope.SET_ROUTES_AWAITING_CARRIER_MODE | setDeclaredKeys
    )
    assert setServedKeys == setAccounted, (
        "the awaiting allow-list and the declared routes no longer "
        "account for every route this class serves: unaccounted "
        f"{sorted(setServedKeys - setAccounted)}"
    )


# ---------------------------------------------------------------------
# The stamp itself refuses what it cannot mean.
# ---------------------------------------------------------------------

def testTheDeclarationVocabularyIsClosedAndTypedReadIsExclusive():
    """R2's set is closed, and typed-read cannot be paired with a mode.

    A route declaring both would state that it reaches no
    mutation-capable primitive and that it commits through a carrier.
    Allowing it would also give the intent-vs-execution comparison a
    declaration under which the carrier-mode rule absorbs every
    typed-read violation, which is a guard that cannot fire.
    """
    with pytest.raises(ValueError, match="Unknown carrier declaration"):
        routeScope.ffnDeclareCarrierMode("mode-z-improvised")
    with pytest.raises(ValueError, match="at least one member"):
        routeScope.ffnDeclareCarrierMode()
    with pytest.raises(ValueError, match="cannot be combined"):
        routeScope.ffnDeclareCarrierMode(
            routeScope.S_CARRIER_TYPED_READ,
            routeScope.S_CARRIER_MODE_A_SYNCHRONOUS,
        )
    routeScope.ffnDeclareCarrierMode(
        routeScope.S_CARRIER_MODE_A_SYNCHRONOUS,
        routeScope.S_CARRIER_MODE_C_DURABLE,
    )  # several modes are a real shape and must be accepted


def testTheDeclarationStampMustLandOnAnEndpointFunction():
    """A stamp on an already-built route would silently do nothing."""
    fnDecorator = routeScope.ffnDeclareCarrierMode(
        routeScope.S_CARRIER_TYPED_READ,
    )
    with pytest.raises(TypeError, match="endpoint function"):
        fnDecorator(
            APIRoute("/api/steps/{sContainerId}/x", lambda: None),
        )


def testTheArtifactEncodingRoundTripsEveryDeclaration():
    """One authority for the several-declarations encoding, not two."""
    tupleDeclared = (
        routeScope.S_CARRIER_MODE_C_DURABLE,
        routeScope.S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    sFormatted = routeScope.fsFormatCarrierDeclaration(tupleDeclared)
    assert set(
        routeScope.ftParseCarrierDeclaration(sFormatted),
    ) == set(tupleDeclared)
    assert routeScope.ftParseCarrierDeclaration("UNDECLARED") == ()
