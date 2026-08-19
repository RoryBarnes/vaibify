"""Container-owner authorization for HTTP routes (the strong predicate).

Sweep A gave every browser request a per-session credential and upgraded
the WebSocket gate to the strong predicate — a valid ``BrowserSessionRecord``
credential AND a lease bound to that session
(:func:`containerOwnership.fbBrowserSessionOwnsLease`). This module carries
the same predicate onto the container-scoped HTTP routes, closing gap-2: a
same-hub browser tab that never claimed a container could previously mutate
it because ``SessionTokenMiddleware`` authorizes only the hub-wide credential,
never the per-container owning lease.

The mechanism is a scope marker plus a route class:

* :func:`ffnRouteScope` (and the :func:`ffnContainerOwner` specialization)
  stamps a scope declaration onto an endpoint, exactly as
  ``actionCatalog.ffnAgentAction`` stamps an agent-action name. A
  ``container-owner`` declaration names its target path parameter and the
  identity kind (``"id"`` resolves via ``OwnerRecord.sContainerId``;
  ``"name"`` is the owner-map key directly).
* :class:`ContainerAwareRoute`, installed on ``app.router.route_class``
  before any route registers, calls :func:`fiAuthorizeContainerHttp` before
  the endpoint body for every ``container-owner`` and ``container-read``
  route.
* :func:`ffnDeclareCarrierMode` stamps a second, ORTHOGONAL declaration:
  what the route DOES to its container, from the closed set the
  migration plan's R2 defines. It authorizes nothing — the route class
  reads it only to decide that the route no longer wants the legacy
  ambient commit-guard admission, which
  :data:`SET_ROUTES_AWAITING_CARRIER_MODE` still grants to every route
  that has not declared. See that record's comment for the ratchet.

A mutating route carries a ``container-owner`` scope implicitly when its path
template carries ``{sContainerId}`` — the marker of a viewer container-session
route — and a GET/HEAD route with that segment carries ``container-read`` by
the mirror convention; both scopes are enforced with the same bound-lease
predicate, so the 100-odd such routes need no per-handler decoration. The
override decorator is for the exceptions, such as the owner-*establishing*
connect route. Non-container control-plane routes are named in
:data:`DICT_CONTROL_PLANE_SCOPES`; a mutating route matching none of these
fails at app construction (:func:`fnValidateRouteScopesOrRaise`), so an
unscoped route can never ship (default-deny).
"""

import json

from fastapi.routing import APIRoute
from starlette.responses import Response

from . import actionCatalog
from . import browserSession
from . import containerOwnership

__all__ = [
    "S_LEASE_HEADER_NAME",
    "S_SCOPE_PUBLIC_STATIC",
    "S_SCOPE_BOOTSTRAP_CAPABILITY",
    "S_SCOPE_BROWSER_HUB",
    "S_SCOPE_CONTAINER_OWNER",
    "S_SCOPE_OWNER_ESTABLISHING",
    "S_SCOPE_CONTAINER_READ",
    "S_SCOPE_CONTAINER_LIFECYCLE",
    "S_CARRIER_TYPED_READ",
    "S_CARRIER_MODE_A_SYNCHRONOUS",
    "S_CARRIER_MODE_B_LOCK_HELD",
    "S_CARRIER_MODE_C_DURABLE",
    "S_CARRIER_LIFECYCLE_TRANSACTION",
    "S_CARRIER_SEPARATE_AUTHORITY",
    "S_CARRIER_DECLARATION_SEPARATOR",
    "DICT_CONTROL_PLANE_SCOPES",
    "SET_CONTAINER_READ_ROUTES",
    "SET_ROUTES_AWAITING_CARRIER_MODE",
    "ContainerAwareRoute",
    "ffnRouteScope",
    "ffnContainerOwner",
    "ffnDeclareCarrierMode",
    "fdictResolveRouteScope",
    "ftResolveCarrierDeclaration",
    "fsFormatCarrierDeclaration",
    "ftParseCarrierDeclaration",
    "fbRouteAwaitsCarrierMode",
    "fiAuthorizeContainerHttp",
    "fiAuthorizeContainerLifecycleHttp",
    "fnValidateRouteScopesOrRaise",
    "fsLeaseFromRequest",
]


# The lease rides an HTTP header, never a query param: a query string leaks
# into access logs and browser history. The WebSocket transport keeps its
# query-param lease (its URL is never logged like an XHR is).
S_LEASE_HEADER_NAME = "X-Vaibify-Lease"

S_SCOPE_PUBLIC_STATIC = "public-static"
S_SCOPE_BOOTSTRAP_CAPABILITY = "bootstrap-capability"
S_SCOPE_BROWSER_HUB = "browser-hub"
S_SCOPE_CONTAINER_OWNER = "container-owner"
S_SCOPE_OWNER_ESTABLISHING = "owner-establishing"
# Owned-container GETs: ENFORCED since the read-enforcement slice with the
# same bound-lease predicate as container-owner. The frozen allowlist below
# remains the ratchet, so a new route cannot silently adopt the scope.
S_SCOPE_CONTAINER_READ = "container-read"
# Name-keyed container lifecycle (stop, settings, cancel-a-start):
# lease-enforced WHENEVER the container is owned, permitted when it has
# no owner record at all. See :func:`fiAuthorizeContainerLifecycleHttp`
# for why that differs from ``container-owner``.
S_SCOPE_CONTAINER_LIFECYCLE = "container-lifecycle"

_SET_VALID_SCOPES = frozenset({
    S_SCOPE_PUBLIC_STATIC,
    S_SCOPE_BOOTSTRAP_CAPABILITY,
    S_SCOPE_BROWSER_HUB,
    S_SCOPE_CONTAINER_OWNER,
    S_SCOPE_OWNER_ESTABLISHING,
    S_SCOPE_CONTAINER_READ,
    S_SCOPE_CONTAINER_LIFECYCLE,
})

_SET_STATE_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Both container scopes run the identical bound-lease authority: reading an
# owned container leaks its files, logs, and credentials-adjacent state to a
# non-owning session just as surely as mutating it corrupts them.
_SET_LEASE_ENFORCED_SCOPES = frozenset({
    S_SCOPE_CONTAINER_OWNER,
    S_SCOPE_CONTAINER_READ,
})

# Every scope ``ContainerAwareRoute`` runs a container authority for. The
# lifecycle scope answers a DIFFERENT question for an unowned container
# than the two above, so it carries its own authority rather than joining
# their set.
_SET_AUTHORIZED_CONTAINER_SCOPES = (
    _SET_LEASE_ENFORCED_SCOPES | frozenset({S_SCOPE_CONTAINER_LIFECYCLE})
)

I_AUTHORIZED = 0
I_REJECT_FORBIDDEN = 403
# A mutation aimed at a Supervised workflow opened in HOST mode.
# Supervised mode's claim is that every change to the repository has a
# recorded cause; vaibify can only make it while it mediates every path
# to the files, and on the host the researcher's own editor, git and
# IDE bypass the hub entirely. The flags are permanent and
# hash-chained, so there is no accumulate-then-clean: the only honest
# move is to stop mutating until supervision is deliberately ended.
#
# NOT 403, for the reason the withdrawn terminal recorded: a refusal a
# client reads as "your credential was rejected" sends the researcher
# to re-claim a project that is already theirs.
I_REJECT_SUPERVISED_ON_HOST = 409
# The one mutation that IS permitted, because it is how the refusal
# ends. It records the transition in the append-only attribution log
# before anything else may write.
S_END_SUPERVISION_ON_HOST_PATH = (
    "/api/workflow/{sContainerId}/supervision/end-on-host"
)
# A lifecycle mutation arriving while the container's start reservation
# is live: refused as a CONFLICT, not a forbidden — the caller may well
# be the owner, and the honest answer is "not yet", not "not you".
I_REJECT_STARTING = 409


# Non-container control-plane routes and the scope each carries. A mutating
# HTTP route whose path template carries no ``{sContainerId}`` segment is
# authorized by the hub-wide browser credential (``browser-hub``), mints or
# arbitrates ownership (``owner-establishing``), or is the credential
# bootstrap itself (``bootstrap-capability``). This map is the frozen
# allowlist: a new mutating control-plane route must be classified here or
# fail app construction.
#
# The name-keyed container-lifecycle family, and why each lands where it
# does (design §12, slice 9). ``stop`` and ``settings`` are
# ``container-lifecycle``: lease-enforced whenever the container is owned,
# so one browser session can no longer stop or reconfigure a container
# another session is working in, and refused 409 while a start reservation
# is live. The dashboard's "Rebuild" is a client-side composite of stop
# then build, so gating stop gates rebuild's destructive half. Cancelling
# a start joins them: only the session that started a container may kill
# its start. ``start`` itself is ``owner-establishing`` — like ``claim``,
# it ARBITRATES ownership, minting the server-owned reservation under the
# host flock and the cardinality lock (design §10b), so it cannot require
# the lease it is about to create.
#
# DELIBERATE RESIDUAL — ``build`` and ``claim`` stay ``browser-hub``.
# Build is an IMAGE operation with no owner: a project whose container has
# never run has no owner record and no lease, so blanket-gating it would
# make building impossible for exactly the projects that need it. Claim is
# the route that MINTS the lease, so it cannot require one. The hub is
# single-user, so for both the browser credential is the boundary and the
# lease is live-session coordination. ``connect`` and ``release`` are
# session-bound by their own authorities (owner-establishing / the
# bound-lease check in ``fnReleaseOwnership``). See docs/architecture.md,
# "Single browser session per container".
DICT_CONTROL_PLANE_SCOPES = {
    ("POST", "/api/bootstrap"): S_SCOPE_BOOTSTRAP_CAPABILITY,
    ("POST", "/api/transfer"): S_SCOPE_BOOTSTRAP_CAPABILITY,
    ("POST", "/api/registry"): S_SCOPE_BROWSER_HUB,
    ("DELETE", "/api/registry/{sName}"): S_SCOPE_BROWSER_HUB,
    ("POST", "/api/containers/{sName}/build"): S_SCOPE_BROWSER_HUB,
    ("POST", "/api/containers/{sName}/start"): S_SCOPE_OWNER_ESTABLISHING,
    ("POST", "/api/containers/{sName}/start/cancel"):
        S_SCOPE_CONTAINER_LIFECYCLE,
    ("POST", "/api/containers/{sName}/stop"): S_SCOPE_CONTAINER_LIFECYCLE,
    ("POST", "/api/containers/{sName}/settings"):
        S_SCOPE_CONTAINER_LIFECYCLE,
    ("POST", "/api/host-directories/create"): S_SCOPE_BROWSER_HUB,
    ("PUT", "/api/preferences/host-warning-acknowledged"):
        S_SCOPE_BROWSER_HUB,
    ("PUT", "/api/preferences/idle-timeout"): S_SCOPE_BROWSER_HUB,
    ("POST", "/api/projects/create"): S_SCOPE_BROWSER_HUB,
    ("POST", "/api/registry/{sName}/claim"): S_SCOPE_OWNER_ESTABLISHING,
    ("POST", "/api/registry/{sName}/release"): S_SCOPE_BROWSER_HUB,
    # Converting a host sandbox to a container is a pre-container hub
    # operation like create/build: there is no container yet to own, so
    # it carries the browser-hub credential and does its own
    # owned/locked/journal refusal in the handler.
    ("POST", "/api/registry/{sName}/convert-to-container"):
        S_SCOPE_BROWSER_HUB,
    # Promoting a host sandbox to a host-based Project is the same kind
    # of pre-container hub operation: there is no container to own, so it
    # carries the browser-hub credential and does its own owned/locked/
    # journal refusal in the handler.
    ("POST", "/api/registry/{sName}/promote-to-host-project"):
        S_SCOPE_BROWSER_HUB,
    # Reconcile is the recovery path for a container nobody can claim,
    # so like stop it must stay answerable for an UNOWNED container —
    # container-lifecycle, never a lease-enforced scope.
    ("POST", "/api/registry/{sName}/reconcile"):
        S_SCOPE_CONTAINER_LIFECYCLE,
    ("POST", "/api/session/spawn"): S_SCOPE_BROWSER_HUB,
    ("POST", "/api/system/docker-status/retry"): S_SCOPE_BROWSER_HUB,
}

# The lifecycle routes that must still be reachable WHILE a start
# reservation is live. Cancelling a start is the whole set, and it has
# to be: the starting-409 exists to stop a mutation landing underneath a
# running create, and cancelling the start is not such a mutation -- it
# is the one action that ends it. Refusing it told the researcher to
# "wait for the start to finish or cancel it", and then refused the
# cancel, so a wedged start could only be escaped by killing the hub.
_SET_LIFECYCLE_PATHS_PERMITTED_WHILE_STARTING = frozenset({
    "/api/containers/{sName}/start/cancel",
})

# Owned-container GETs resolve to ``container-read`` by the GET/{sContainerId}
# convention (see :func:`fdictResolveRouteScope`) and are ENFORCED:
# ``ContainerAwareRoute`` runs the same bound-lease authority for them as for
# ``container-owner`` mutations. Pre-claim surfaces stay out of this scope by
# construction — the picker and registry operate on name-keyed or listing
# routes that carry no ``{sContainerId}`` segment, and the id-keyed reads
# here (``/ready``, ``/workflows``) are only issued after the claim minted
# the lease. This frozen allowlist is the ratchet — every route resolving to
# ``container-read`` must appear here, so a NEW owned-container GET fails
# ``testContainerReadScopeIsAFrozenRatchetedAllowlist`` until acknowledged.
SET_CONTAINER_READ_ROUTES = frozenset({
    ("GET", "/api/containers/{sContainerId}/isolation"),
    ("GET", "/api/containers/{sContainerId}/ready"),
    ("GET", "/api/draft/{sContainerId}/{sFilePath:path}"),
    ("GET", "/api/drafts/{sContainerId}"),
    ("GET", "/api/figure/{sContainerId}/{sFilePath:path}"),
    ("HEAD", "/api/figure/{sContainerId}/{sFilePath:path}"),
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
    ("GET", "/api/repos/{sContainerId}/status"),
    ("GET", "/api/repos/{sContainerId}/{sRepoName}/dirty-files"),
    ("GET", "/api/settings/{sContainerId}"),
    ("GET", "/api/steps/{sContainerId}"),
    ("GET", "/api/steps/{sContainerId}/by-label/{sLabel}"),
    ("GET", "/api/steps/{sContainerId}/resolve-commands"),
    ("GET", "/api/steps/{sContainerId}/validate"),
    ("GET", "/api/steps/{sContainerId}/{iStepIndex}"),
    ("GET", "/api/steps/{sContainerId}/{iStepIndex}/falsification"),
    ("GET", "/api/steps/{sContainerId}/{iStepIndex}/plot-standards"),
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
})


# ---------------------------------------------------------------------
# Carrier-mode declarations (migration plan phase 1c; rules R1, R2, R6).
# ---------------------------------------------------------------------

# R2's closed set. A declaration records what a container-scoped entry
# point DOES. It authorizes NOTHING: it opens no admission, and no
# authority consults it. The declared behaviour lives in the handler's
# carrier calls, and a handler that forgets one is refused at the
# primitive with MutationNotAdmittedError -- that refusal IS the proof,
# and a declaration that pre-admitted the handler would delete it. This
# is the bAgentSafe mistake one level up, and it has shipped here once.
S_CARRIER_TYPED_READ = "typed-read"
S_CARRIER_MODE_A_SYNCHRONOUS = "mode-a-synchronous"
S_CARRIER_MODE_B_LOCK_HELD = "mode-b-lock-held"
S_CARRIER_MODE_C_DURABLE = "mode-c-durable"
S_CARRIER_LIFECYCLE_TRANSACTION = "lifecycle-transaction"
S_CARRIER_SEPARATE_AUTHORITY = "separate-authority"

_SET_VALID_CARRIER_DECLARATIONS = frozenset({
    S_CARRIER_TYPED_READ,
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_MODE_C_DURABLE,
    S_CARRIER_LIFECYCLE_TRANSACTION,
    S_CARRIER_SEPARATE_AUTHORITY,
})

# One entry point may declare SEVERAL -- a handler that writes
# synchronously and then starts durable work is a real shape, and
# forcing it to pick one would make the record lie. The observation
# artifact carries a declaration as a single string, so the several are
# joined on this separator and split back on it here; one authority for
# the encoding, never two derivations of it.
S_CARRIER_DECLARATION_SEPARATOR = "+"

# R6's ratchet. A container-scoped route keeps the legacy ambient
# admission (``S_ADMISSION_MODE_REQUEST``, minted below by
# :class:`ContainerAwareRoute`) only while it is recorded here. A route
# that DECLARES takes the enforced branch instead, and a route that is
# NEITHER declared nor recorded here also takes the enforced branch --
# fail closed, so a route added later cannot inherit the ambient mint by
# being forgotten.
#
# Seeded with the 130 container-scoped routes ``ContainerAwareRoute``
# actually serves, measured from ``fappCreateHubApplication()`` rather
# than transcribed. It may only ever SHRINK
# (``tests/testCarrierModeDeclaration.py``); when it is empty, the
# ambient branch and this set are deleted together (plan phase 4).
#
# The plan's population figure is 132 and it counts two routes this set
# deliberately omits. Both are WebSocket routes that resolve to
# ``container-read`` by the ``{sContainerId}`` convention, but an
# ``APIWebSocketRoute`` is not an ``APIRoute``, so
# ``app.router.route_class`` never governs them: they take NEITHER
# branch and are gated by ``webSocketAuthorization`` instead. Seeding
# them here would record an HTTP admission they never receive, and
# nothing could ever migrate them out of it.
# ``tests/testCarrierModeDeclaration.py`` names them and asserts the
# three records still add back up to the resolved population, so
# neither can quietly leave it.
SET_ROUTES_AWAITING_CARRIER_MODE = frozenset({
    ("GET", "/api/containers/{sContainerId}/isolation"),
    ("GET", "/api/containers/{sContainerId}/ready"),
    ("GET", "/api/draft/{sContainerId}/{sFilePath:path}"),
    ("GET", "/api/drafts/{sContainerId}"),
    ("GET", "/api/figure/{sContainerId}/{sFilePath:path}"),
    ("GET", "/api/files/{sContainerId}/download/{sFilePath:path}"),
    ("GET", "/api/files/{sContainerId}/{sDirectoryPath:path}"),
    ("GET", "/api/git/{sContainerId}/manifest-check"),
    ("GET", "/api/git/{sContainerId}/status"),
    ("GET", "/api/logs/{sContainerId}"),
    ("GET", "/api/logs/{sContainerId}/{sLogFilename}"),
    ("GET", "/api/monitor/{sContainerId}"),
    ("GET", "/api/overleaf/{sContainerId}/mirror/tree"),
    ("GET", "/api/pipeline/{sContainerId}/file-status"),
    ("GET", "/api/pipeline/{sContainerId}/host-log-tail"),
    ("GET", "/api/pipeline/{sContainerId}/workflow-discovery"),
    ("GET", "/api/repos/{sContainerId}/{sRepoName}/dirty-files"),
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
    ("POST", "/api/zenodo/{sContainerId}/download"),
})


def ffnRouteScope(sScope, sTargetParam=None, sIdentityKind=None):
    """Stamp an authorization scope onto an HTTP route endpoint.

    Metadata only, exactly like ``actionCatalog.ffnAgentAction``: the
    behaviour lives in :class:`ContainerAwareRoute`, which reads this stamp
    when it builds the route handler. A ``container-owner`` (or
    ``owner-establishing``) declaration must also name its ``sTargetParam``
    path parameter and ``sIdentityKind`` (``"id"`` or ``"name"``) so the
    authority can resolve which owner record governs the request.

    Placed BELOW the ``@app.<verb>(...)`` line (decorators apply bottom-up)
    so it stamps the same function object FastAPI registers. Applying it to
    anything but a plain endpoint function is rejected, so a marker attached
    to an already-built ``APIRoute`` — the misuse that would silently do
    nothing — raises instead.
    """
    if sScope not in _SET_VALID_SCOPES:
        raise ValueError(f"Unknown route scope: {sScope!r}")

    def _ffnDecorator(fnEndpoint):
        if isinstance(fnEndpoint, APIRoute) or not callable(fnEndpoint):
            raise TypeError(
                "ffnRouteScope must decorate an endpoint function, below the "
                "@app.<verb> line; it received a "
                f"{type(fnEndpoint).__name__}."
            )
        fnEndpoint._dictRouteScope = {
            "sScope": sScope,
            "sTargetParam": sTargetParam,
            "sIdentityKind": sIdentityKind,
        }
        return fnEndpoint
    return _ffnDecorator


def ffnContainerOwner(sTargetParam="sContainerId", sIdentityKind="id"):
    """Declare a route as container-owner scoped (the common specialization).

    A thin wrapper over :func:`ffnRouteScope`; use it to gate a mutating
    route whose owner is keyed by ``sIdentityKind`` on ``sTargetParam``.
    Most container routes need no decoration — the ``{sContainerId}``
    segment implies this scope — so this exists for the routes whose target
    parameter or identity kind departs from that default.
    """
    return ffnRouteScope(
        S_SCOPE_CONTAINER_OWNER, sTargetParam, sIdentityKind,
    )


def ffnDeclareCarrierMode(*saDeclarations):
    """Stamp what a container-scoped route DOES, from R2's closed set.

    Metadata only, exactly like :func:`ffnRouteScope` and
    ``actionCatalog.ffnAgentAction``. **A declaration authorizes
    nothing.** It mints no admission and no authority reads it;
    :class:`ContainerAwareRoute` consults it for one purpose only — to
    learn that the route no longer wants the legacy ambient mint. The
    declared behaviour lives in the handler's carrier calls, and
    forgetting one raises ``MutationNotAdmittedError`` at the primitive.
    That refusal is the proof, and a decorator that pre-admitted the
    handler would delete it.

    Placed BELOW the ``@app.<verb>(...)`` line, like the scope stamp, so
    it marks the same function object FastAPI registers.
    """
    _fnValidateCarrierDeclarations(saDeclarations)

    def _ffnDecorator(fnEndpoint):
        if isinstance(fnEndpoint, APIRoute) or not callable(fnEndpoint):
            raise TypeError(
                "ffnDeclareCarrierMode must decorate an endpoint function, "
                "below the @app.<verb> line; it received a "
                f"{type(fnEndpoint).__name__}."
            )
        fnEndpoint._tupleCarrierDeclaration = tuple(saDeclarations)
        return fnEndpoint
    return _ffnDecorator


def _fnValidateCarrierDeclarations(saDeclarations):
    """Raise on an empty, unknown, or self-contradicting declaration.

    ``typed-read`` is exclusive. It claims the entry point reaches no
    mutation-capable primitive at all, so pairing it with a carrier mode
    would state both that the route mutates and that it does not — and
    would hand the intent-vs-execution comparison a declaration under
    which the mode rule absorbs every typed-read violation.
    """
    if not saDeclarations:
        raise ValueError(
            "A carrier declaration must name at least one member of the "
            f"closed set: {sorted(_SET_VALID_CARRIER_DECLARATIONS)}."
        )
    setUnknown = set(saDeclarations) - _SET_VALID_CARRIER_DECLARATIONS
    if setUnknown:
        raise ValueError(
            f"Unknown carrier declaration(s): {sorted(setUnknown)}; the "
            f"set is closed at {sorted(_SET_VALID_CARRIER_DECLARATIONS)}."
        )
    if S_CARRIER_TYPED_READ in saDeclarations and len(
        set(saDeclarations),
    ) > 1:
        raise ValueError(
            f"'{S_CARRIER_TYPED_READ}' claims the route reaches no "
            "mutation-capable primitive, so it cannot be combined with a "
            f"declaration saying it does: {sorted(set(saDeclarations))}."
        )


def ftResolveCarrierDeclaration(fnEndpoint):
    """Return an endpoint's declarations, or ``()`` when it has none."""
    return tuple(getattr(fnEndpoint, "_tupleCarrierDeclaration", ()) or ())


def fsFormatCarrierDeclaration(tDeclarations):
    """Return the single-string form an observation artifact records."""
    return S_CARRIER_DECLARATION_SEPARATOR.join(sorted(tDeclarations))


def ftParseCarrierDeclaration(sDeclaration):
    """Return the declarations encoded in one artifact string.

    Anything outside the closed set is dropped rather than returned, so
    the absent-declaration mark an observation carries when its entry
    point has not declared parses to ``()`` — the same answer as an
    endpoint carrying no stamp.
    """
    return tuple(
        sPart
        for sPart in sDeclaration.split(S_CARRIER_DECLARATION_SEPARATOR)
        if sPart in _SET_VALID_CARRIER_DECLARATIONS
    )


def fbRouteAwaitsCarrierMode(setMethods, sPath):
    """Return True while a route is still recorded as awaiting (R6).

    EVERY method the route serves must be recorded. A route that
    acquired a new verb is only partly accounted for, and the honest
    answer for the whole route is then "not awaiting" — which sends it
    to the enforced branch, where a mutation it forgot to carry is
    refused rather than silently admitted.
    """
    setKeys = {(sMethod, sPath) for sMethod in (setMethods or ())}
    if not setKeys:
        return False
    return setKeys <= SET_ROUTES_AWAITING_CARRIER_MODE


def fdictResolveRouteScope(setMethods, sPath, fnEndpoint):
    """Return the scope declaration for a route, or ``None`` when unscoped.

    Resolution order: an explicit :func:`ffnRouteScope` stamp wins; else a
    mutating ``{sContainerId}`` path is container-owner by convention, and a
    non-mutating (GET/HEAD) ``{sContainerId}`` path is container-read by the
    mirror convention (both enforced by ``ContainerAwareRoute`` with the
    bound-lease authority); else a control-plane route named in
    :data:`DICT_CONTROL_PLANE_SCOPES` carries its declared scope. Anything
    else is ``None`` — which :func:`fnValidateRouteScopesOrRaise` treats as a
    construction error for a mutating route (default-deny).
    """
    dictExplicit = getattr(fnEndpoint, "_dictRouteScope", None)
    if dictExplicit is not None:
        return dictExplicit
    setNormalized = set(setMethods or ())
    bMutating = bool(_SET_STATE_MUTATING_METHODS & setNormalized)
    if "{sContainerId}" in sPath and bMutating:
        return {
            "sScope": S_SCOPE_CONTAINER_OWNER,
            "sTargetParam": "sContainerId",
            "sIdentityKind": "id",
        }
    if "{sContainerId}" in sPath:
        return {
            "sScope": S_SCOPE_CONTAINER_READ,
            "sTargetParam": "sContainerId",
            "sIdentityKind": "id",
        }
    for sMethod in setNormalized:
        sScope = DICT_CONTROL_PLANE_SCOPES.get((sMethod, sPath))
        if sScope is not None:
            return _fdictDeclareControlPlaneScope(sScope, sPath)
    return None


def _fdictDeclareControlPlaneScope(sScope, sPath=""):
    """Return the scope declaration for a named control-plane route.

    Only ``container-lifecycle`` names a target: it is the one
    control-plane scope with an owner to resolve, and it is name-keyed by
    definition (the lifecycle routes are the ``{sName}`` family, which
    the picker reaches before any container id exists).
    """
    if sScope == S_SCOPE_CONTAINER_LIFECYCLE:
        return {
            "sScope": sScope,
            "sTargetParam": "sName",
            "sIdentityKind": "name",
            "bPermittedWhileStarting": (
                sPath in _SET_LIFECYCLE_PATHS_PERMITTED_WHILE_STARTING
            ),
        }
    return {"sScope": sScope, "sTargetParam": None, "sIdentityKind": None}


def fsLeaseFromRequest(request):
    """Return the ``X-Vaibify-Lease`` header value, or '' when absent."""
    return request.headers.get(S_LEASE_HEADER_NAME.lower(), "")


def _ftResolveOwnerTarget(dictContainerOwners, dictScope, sTargetValue):
    """Return ``(sName, sContainerId)`` the scope's target resolves to.

    For the ``"name"`` identity kind the path value IS the owner-map key.
    For ``"id"`` the owner record carrying that Docker id is found and its
    name returned; an id no owner serves yields ``(None, sContainerId)``, so
    the browser lane below answers "claim first".
    """
    if dictScope.get("sIdentityKind") == "name":
        return (sTargetValue, "")
    for sName, recordOwner in dictContainerOwners.items():
        if recordOwner.sContainerId == sTargetValue:
            return (sName, sTargetValue)
    return (None, sTargetValue)


def fiAuthorizeContainerHttp(request, appState, dictScope):
    """Return ``0`` when the request may act on its target container, else 403.

    The strong predicate on the HTTP boundary. The in-container agent lane
    is decided first: an ``X-Vaibify-Session`` header present but not
    authorizing this container id is refused immediately, never falling
    through to the browser check. The browser lane requires BOTH a valid
    per-session credential and a lease bound to that session
    (:func:`containerOwnership.fbBrowserSessionOwnsLease`), so a second tab
    replaying a copied lease is refused even though the lease value is
    genuine. A container with no owner record answers "claim first".
    """
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    dictBrowserSessions = getattr(appState, "dictBrowserSessions", {}) or {}
    sTargetValue = request.path_params.get(dictScope.get("sTargetParam"), "")
    sName, sContainerId = _ftResolveOwnerTarget(
        dictContainerOwners, dictScope, sTargetValue,
    )
    sAgentToken = request.headers.get(
        actionCatalog.S_SESSION_HEADER_NAME.lower(), "",
    )
    if sAgentToken:
        if containerOwnership.fbAgentTokenAuthorizesContainerId(
            dictContainerOwners, sAgentToken, sContainerId,
        ):
            return I_AUTHORIZED
        return I_REJECT_FORBIDDEN
    sCredential = request.headers.get("x-session-token", "")
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        dictBrowserSessions, sCredential,
    )
    if not sBrowserSessionId:
        return I_REJECT_FORBIDDEN
    if sName is None or dictContainerOwners.get(sName) is None:
        return I_REJECT_FORBIDDEN
    if containerOwnership.fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId,
        fsLeaseFromRequest(request),
    ):
        return I_AUTHORIZED
    return I_REJECT_FORBIDDEN


def fiAuthorizeContainerLifecycleHttp(request, appState, dictScope):
    """Return ``0``, ``403``, or ``409`` for a name-keyed lifecycle route.

    Lease-enforced WHENEVER the container is owned, and permitted when it
    carries no owner record at all. The asymmetry is deliberate, and it is
    why this is not simply ``container-owner``: stopping or reconfiguring a
    container another browser session is working in destroys that session's
    live work, so it is refused (403); but a container nobody holds has no
    session to protect, and the picker legitimately operates on one before
    any claim exists, so refusing there would make an unowned container
    unstoppable from the dashboard that shows it running.

    A live start reservation (design §10b) refuses even the owner with
    ``409``: the container is mid-create/mid-start, and mutating it
    underneath the running start is the partial-state hazard the
    reservation exists to bound. The in-container agent lane is refused
    outright — an agent never operates the container control plane.
    """
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    dictBrowserSessions = getattr(appState, "dictBrowserSessions", {}) or {}
    sName = request.path_params.get(dictScope.get("sTargetParam") or "", "")
    if request.headers.get(actionCatalog.S_SESSION_HEADER_NAME.lower(), ""):
        return I_REJECT_FORBIDDEN
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        dictBrowserSessions, request.headers.get("x-session-token", ""),
    )
    if not sBrowserSessionId:
        return I_REJECT_FORBIDDEN
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return I_AUTHORIZED
    # The starting refusal is answered to the record's OWN session first,
    # keyed on the session rather than the lease: the session that
    # requested the start has no lease yet (a start hands none out), so a
    # lease-first order would tell the initiator "not yours" about the
    # very container it is starting. For the same reason, a route that is
    # permitted while starting is AUTHORIZED here rather than falling
    # through to the lease check it could not possibly satisfy; the
    # handler re-arbitrates on the session itself.
    if recordOwner.sBrowserSessionId in ("", sBrowserSessionId) and (
        getattr(recordOwner, "reservation", None) is not None
    ):
        if dictScope.get("bPermittedWhileStarting"):
            return I_AUTHORIZED
        return I_REJECT_STARTING
    if not containerOwnership.fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId,
        fsLeaseFromRequest(request),
    ):
        return I_REJECT_FORBIDDEN
    return I_AUTHORIZED


class ContainerAwareRoute(APIRoute):
    """Route class that enforces the bound-lease predicate before serving.

    Installed on ``app.router.route_class`` before any route registers, so
    every route becomes an instance and none can slip past the authority
    through a registration-ordering accident. For a ``container-owner``
    or ``container-read`` route the wrapper runs
    :func:`fiAuthorizeContainerHttp` first and short-circuits the endpoint
    on refusal.

    It then chooses between two lanes that both already existed
    (migration plan phase 1c). A route still recorded in
    :data:`SET_ROUTES_AWAITING_CARRIER_MODE` is served under the legacy
    ambient commit-guard request admission (design §8), so the write
    funnel can revalidate the lane tuple at each commit point. Every
    other route — one that has DECLARED, and equally one that is
    neither declared nor recorded as awaiting — is served with the
    request lane merely MARKED and NO admission, so a container
    mutation it did not carry through a carrier is refused at the
    funnel. That is also how every non-container scope is served: a
    route without container authority that reaches a container-mutating
    primitive is refused there, which is what makes the primitives
    unreachable directly from a route.
    """

    def get_route_handler(self):
        fnOriginalHandler = super().get_route_handler()

        async def fresponseHandleAuthorized(request):
            from . import commitCarrier
            dictScope = fdictResolveRouteScope(
                self.methods, self.path, self.endpoint,
            )
            bContainerScoped = (
                dictScope is not None
                and dictScope["sScope"] in _SET_AUTHORIZED_CONTAINER_SCOPES
            )
            if bContainerScoped:
                iCode = _fiAuthorizeForScope(
                    request, request.app.state, dictScope,
                )
                if iCode:
                    return _fresponseRefused(iCode)
            if _fbRefuseSupervisedHostMutation(
                self.methods, self.path, request,
            ):
                return _fresponseSupervisedHostRefusal()
            if bContainerScoped and _fbServeOnAmbientAdmission(self):
                tAdmissionTokens = commitCarrier.ftOpenRequestAdmission(
                    request.app.state, dictScope, request,
                )
                try:
                    return await fnOriginalHandler(request)
                finally:
                    commitCarrier.fnCloseRequestAdmission(tAdmissionTokens)
            tokenLane = commitCarrier.ftokenMarkEnforcedRequestLane()
            try:
                return await fnOriginalHandler(request)
            finally:
                commitCarrier.fnResetEnforcedRequestLane(tokenLane)

        return fresponseHandleAuthorized


def _fbRefuseSupervisedHostMutation(setMethods, sPath, request):
    """Return True for a mutation aimed at a Supervised HOST workflow.

    Checked HERE because this class is the only thing that runs before
    every container-scoped handler and already knows whether a request
    mutates; a second classifier in middleware would drift from this
    one, and per-route guards would be a rule that a new route can
    forget.

    Four conditions, all necessary. The route MUTATES — reading and
    browsing a Supervised host workflow stays available, because
    nothing about a read makes the attribution log lie. It is not the
    end-supervision route, which is the transition out. The resource is
    a registered HOST project. And supervision is actually on: an
    ordinary host workflow is refused nothing by this.

    Fails OPEN when the workflow cannot be read, and that is
    deliberate: this guard exists to keep a claim honest, not to
    protect anything, and a hub whose context is missing (a bare
    ``FastAPI()`` in a test, a route registered outside the factory)
    must not have every mutation refused by a guard that cannot tell
    what it is guarding.
    """
    from vaibify.config.registryManager import fbIsHostProject
    if not (_SET_STATE_MUTATING_METHODS & set(setMethods or ())):
        return False
    if sPath == S_END_SUPERVISION_ON_HOST_PATH:
        return False
    sResourceId = request.path_params.get("sContainerId", "")
    if not sResourceId or not fbIsHostProject(sResourceId):
        return False
    dictContext = getattr(request.app.state, "dictRouteContext", None)
    if dictContext is None:
        return False
    dictWorkflow = (dictContext.get("workflows") or {}).get(sResourceId)
    if dictWorkflow is None:
        return False
    from . import attributionLog
    return attributionLog.fbSupervisionEnabled(dictWorkflow)


def _fresponseSupervisedHostRefusal():
    """Return the refusal a Supervised host workflow answers mutations with."""
    return _fresponseJson(I_REJECT_SUPERVISED_ON_HOST, (
        "This workflow is in Supervised mode, which vaibify can only "
        "vouch for inside a container: on this machine your editor, "
        "git and IDE change the files without the hub seeing it, so "
        "every recorded cause would be a guess. Reads stay available. "
        "End Supervised mode to continue working here — the change is "
        "recorded permanently in the attribution log."
    ))


def _fbServeOnAmbientAdmission(route):
    """Return True while a route still holds the legacy ambient mint.

    A DECLARED route never does: the enforced branch has no admission,
    its handler opens one per operation through a carrier, and
    forgetting raises ``MutationNotAdmittedError`` loudly. Pre-admitting
    a declared route would delete exactly that proof (R1).

    A route that is neither declared nor recorded in
    :data:`SET_ROUTES_AWAITING_CARRIER_MODE` also fails to the enforced
    branch. The allow-list is what GRANTS the legacy mint, not the
    absence of a declaration, so a route added after this list was
    seeded cannot inherit the ambient admission by being forgotten.
    """
    if ftResolveCarrierDeclaration(route.endpoint):
        return False
    return fbRouteAwaitsCarrierMode(route.methods, route.path)


def _fiAuthorizeForScope(request, appState, dictScope):
    """Route a scoped request to its authority; return its verdict code.

    The two container authorities differ only in how they treat an
    UNOWNED container (see :func:`fiAuthorizeContainerLifecycleHttp`), so
    the dispatch lives here rather than inside either of them. The
    module-global lookups are deliberate: a test that substitutes an
    authority sees its substitute honoured.
    """
    if dictScope["sScope"] == S_SCOPE_CONTAINER_LIFECYCLE:
        return fiAuthorizeContainerLifecycleHttp(request, appState, dictScope)
    return fiAuthorizeContainerHttp(request, appState, dictScope)


def _fresponseRefused(iStatusCode):
    """Return the JSON refusal body matching an authority's verdict."""
    if iStatusCode == I_REJECT_STARTING:
        return _fresponseJson(iStatusCode, (
            "This container is still starting; wait for the start to "
            "finish or cancel it, then try again."
        ))
    return _fresponseForbidden(iStatusCode)


def _fresponseForbidden(iStatusCode):
    """Return the JSON refusal for a caller that does not own the container."""
    return _fresponseJson(iStatusCode, (
        "You do not hold this container's lease; claim or connect to it "
        "first."
    ))


def _fresponseJson(iStatusCode, sDetail):
    """Return a JSON ``detail`` body — the shape the client's reader wants."""
    return Response(
        status_code=iStatusCode,
        content=json.dumps({"detail": sDetail}),
        media_type="application/json",
    )


def _fbIsApplicableMutatingRoute(route):
    """Return True for an HTTP APIRoute the default-deny check must classify.

    WebSocket routes carry no ``methods`` and are gated separately by the
    WebSocket authority; static mounts have no ``endpoint``; FastAPI's own
    ``/openapi.json`` and docs routes are read-only. So the applicable set
    is exactly the state-mutating :class:`APIRoute` instances.
    """
    if not isinstance(route, APIRoute):
        return False
    setMethods = set(getattr(route, "methods", None) or ())
    return bool(_SET_STATE_MUTATING_METHODS & setMethods)


def fnValidateRouteScopesOrRaise(app):
    """Raise when any mutating route on ``app`` declares no authorization scope.

    Default-deny at construction: a state-mutating route that is neither a
    ``{sContainerId}`` container route nor a named control-plane route has no
    scope, so it would ship unauthorized. Failing here makes that impossible
    to merge.
    """
    listUnscoped = []
    for route in app.routes:
        if not _fbIsApplicableMutatingRoute(route):
            continue
        dictScope = fdictResolveRouteScope(
            route.methods, route.path, route.endpoint,
        )
        if dictScope is None:
            listUnscoped.append(
                (sorted(route.methods), route.path),
            )
    if listUnscoped:
        raise RuntimeError(
            "Mutating routes with no declared authorization scope "
            "(default-deny — add a {sContainerId} segment, an explicit "
            "@ffnRouteScope, or a DICT_CONTROL_PLANE_SCOPES entry):\n  "
            + "\n  ".join(
                f"{listMethods} {sPath}"
                for listMethods, sPath in sorted(listUnscoped)
            )
        )
