"""Stand the commit-guard carrier down for a bare-``FastAPI()`` test.

WHAT THIS IS FOR
----------------

A route migrated onto the carrier binds its request to the container's
owner record and writes a two-phase journal record against the hub's
application state. A test module that builds a bare ``FastAPI()`` over a
fake docker has neither, so every migrated route in it answers 403 the
moment it is migrated. That refusal is correct — the request genuinely
holds no lease — but it is not what those tests are about, so they stand
the carrier down: the lane tuple resolves to a fixed stand-in and each
carrier runs its effect or worker directly.

WHAT IT COSTS, STATED PLAINLY
-----------------------------

An unexplained permissive mock is how this suite acquired about twenty
of them, so: a module using this proves what its routes DO — which
bodies are refused, what lands in the sidecar, what the git tree looks
like afterwards — and **NOTHING** about the admission the mutation runs
under. A route whose carrier call was deleted outright passes every test
in such a module. That guarantee lives in
``tests/testCarrierMigratedRoutes.py``, against a double that calls the
real gates and records the live admission MODE.

WHY IT IS SHARED
----------------

Seven test modules had grown a private copy of these same three patches
before this file existed, and the copies had already begun to diverge —
one patched the name on ``routeContext``, another the copy the route
module imported, and a module whose routes used both shapes failed in a
way that read as a route bug. This is the rule of three several times
over, and the divergence is the force the guidance asks for.

USE IT AS A FIXTURE, NOT AN AUTOUSE
-----------------------------------

Request it explicitly, so a test that deliberately builds its own client
still meets the real refusal.
"""

__all__ = ["fnStandCarrierDown"]

S_STAND_IN_CONTAINER_NAME = "fake-container"

_S_LANE_TUPLE_NAME = "fdictRequireLaneTupleForCommit"


def _fdictStandInLaneTuple(requestHttp, sContainerId, sOperationName):
    """Return the lane tuple a stood-down carrier binds every request to."""
    del requestHttp, sContainerId, sOperationName
    return {"sContainerName": S_STAND_IN_CONTAINER_NAME}


def _fdictCommitWithoutTheJournal(
    appState, sName, sContainerId, dictLaneTuple, sOperationKind,
    sTarget, fnEffect, dictHolderIdentity,
):
    """Run a mode-(a) effect with no journal record and no lane check."""
    del appState, sName, sContainerId, dictLaneTuple
    del sOperationKind, sTarget, dictHolderIdentity
    return {"bCommitted": True, "result": fnEffect()}


async def _fdictRunWorkerWithoutTheDrain(
    appState, sName, sContainerId, dictLaneTuple, sOperationKind,
    sTarget, fnWorker, dictHolderIdentity=None, fnTerminateWorker=None,
):
    """Run a mode-(b) worker inline, holding no lock and no record.

    The worker is called with ``None`` for its supervisor, which is the
    shape every carrier worker already accepts: the real supervisor is
    an optional argument precisely so a worker can be exercised without
    one.
    """
    del appState, sName, sContainerId, dictLaneTuple
    del sOperationKind, sTarget, dictHolderIdentity, fnTerminateWorker
    return {"bCommitted": True, "result": fnWorker(None)}


def fnStandCarrierDown(monkeypatch, *listRouteModules):
    """Patch out the three carrier entry points for one test module.

    Both bindings of the lane-tuple resolver are patched: the definition
    on ``routeContext`` (which ``fnCommitWorkflowSave`` calls through its
    own module globals) and the copy any named route module imported into
    its namespace. Patching only one leaves the other live, which is a
    403 arriving from a carrier the test believes it stood down.

    Pass the route modules under test; passing none patches only the
    shared definitions, which is right for a module that reaches the
    carrier solely through ``fnCommitWorkflowSave``.
    """
    from vaibify.gui import commitCarrier, routeContext

    monkeypatch.setattr(
        routeContext, _S_LANE_TUPLE_NAME, _fdictStandInLaneTuple,
    )
    for moduleRoute in listRouteModules:
        if hasattr(moduleRoute, _S_LANE_TUPLE_NAME):
            monkeypatch.setattr(
                moduleRoute, _S_LANE_TUPLE_NAME, _fdictStandInLaneTuple,
            )
    monkeypatch.setattr(
        commitCarrier, "fdictCommitSynchronousMutation",
        _fdictCommitWithoutTheJournal,
    )
    monkeypatch.setattr(
        commitCarrier, "fdictRunLockHeldMutation",
        _fdictRunWorkerWithoutTheDrain,
    )
