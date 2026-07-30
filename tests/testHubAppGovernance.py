"""Every hub state-mutating route is catalogued or explicitly excluded.

The standing agent-action invariant
(``testArchitecturalInvariants.testAgentActionRegistered``) builds only the
workflow-VIEWER application, so the hub's control-plane routes — build,
start, stop, settings, registry add/remove, lease claim/release, and
host-side project/directory creation — were governed by nobody. A hub
route added tomorrow would silently drift out of the agent-safety model.

This builds the HUB application and applies the same rule: every
state-mutating route must be an entry in ``LIST_AGENT_ACTIONS`` or an
explicit member of ``SET_INTENTIONALLY_EXCLUDED_PATHS``.
"""

from unittest.mock import MagicMock, patch

from vaibify.gui import actionCatalog
from vaibify.gui import pipelineServer


_SET_STATE_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _flistHubStateMutatingRoutes():
    """Return sorted (sMethod, sPath) for every hub state-mutating route."""
    with patch(
        "vaibify.gui.pipelineServer._fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    listResult = []
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        for sMethod in sorted(
            _SET_STATE_MUTATING_METHODS & set(route.methods or ())
        ):
            listResult.append((sMethod, route.path))
    return sorted(set(listResult))


def testHubControlPlaneRoutesAreGoverned():
    """No hub state-mutating route may be governed by neither list."""
    setCatalogPaths = {
        (dictEntry["sMethod"], dictEntry["sPath"])
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["sMethod"] != "WS"
    }
    listUngoverned = [
        (sMethod, sPath)
        for sMethod, sPath in _flistHubStateMutatingRoutes()
        if (sMethod, sPath) not in setCatalogPaths
        and (sMethod, sPath) not in actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS
    ]
    assert listUngoverned == [], (
        "Hub state-mutating routes governed by neither LIST_AGENT_ACTIONS "
        "nor SET_INTENTIONALLY_EXCLUDED_PATHS (a hub control-plane route "
        "must be one or the other):\n  "
        + "\n  ".join(
            f"{sMethod} {sPath}" for sMethod, sPath in listUngoverned
        )
    )


def testControlPlaneRoutesAreAgentExcluded():
    """The control plane is refused on the agent lane, not merely absent.

    The agent must never build/start/stop a container, manage the
    registry, or claim/release the lease — so each is in the exclusion set
    and ``fbAgentLanePermitsRoute`` refuses it.
    """
    for sMethod, sPath in (
        ("POST", "/api/containers/{sName}/build"),
        ("POST", "/api/containers/{sName}/start"),
        ("POST", "/api/containers/{sName}/stop"),
        ("POST", "/api/containers/{sName}/settings"),
        ("POST", "/api/registry"),
        ("DELETE", "/api/registry/{sName}"),
        ("POST", "/api/registry/{sName}/claim"),
        ("POST", "/api/registry/{sName}/release"),
        ("POST", "/api/host-directories/create"),
        ("POST", "/api/projects/create"),
    ):
        assert not actionCatalog.fbAgentLanePermitsRoute(sMethod, sPath), (
            f"agent lane must not permit control-plane route {sMethod} {sPath}"
        )
