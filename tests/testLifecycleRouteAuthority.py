"""What each container-lifecycle route ACTUALLY does, audited one at a time.

``lifecycle-transaction`` is a FINDING, not a default. The migration plan
says so, and the stop route is why: it holds no lock, writes no journal
record, registers no durable work, and is invisible to an ownership
transfer -- none of the four things the name implies. A declaration
applied to the family because of what the family is called would have
recorded a transaction that does not exist.

So this file audits the three routes individually and records what was
found. Each fact is asserted rather than described, because a note in a
docstring cannot notice when the code moves underneath it.

The three, as resolved from the live application (not from a list
somebody maintained by hand):

===========================  ====  =======  =========  ==========
route                        lock  journal  admission  transfer
===========================  ====  =======  =========  ==========
POST .../{sName}/stop        none  none     ambient    invisible
POST .../{sName}/start/cancel part  marks   ambient    adopted
POST .../{sName}/settings    none  none     ambient    n/a (host)
===========================  ====  =======  =========  ==========

"ambient" is the same for all three and is not a property of the route:
``ContainerAwareRoute`` opens ``S_ADMISSION_MODE_REQUEST`` for every
authorized container-scoped request, so the primitive gate passes for
anything reached during it whether or not the route chose a mode. That
is the hole the migration exists to close, and it is recorded here so
that no lifecycle route can be read as already having chosen.

**The finding that cost the most to establish** is under
:func:`testTheTransferRefusalForACancellingTaskCannotFire`. The transfer
carries a refusal for "a durable task whose cancellation is in
progress". Nothing in the package ever moves a durable task out of the
``running`` state, so that branch is unreachable, and with it the
identical branch at the commit point and half of the durable task's own
commit-time check. It is coherent -- ``fdictRequestDurableTaskCancel``
is deliberately parked and refuses everything (AGENTS.md, known debt) --
but three guards that READ as live protection are not, and an audit that
credited the cancel route with a refusal that cannot fire would be
recording a protection nobody has.
"""

import ast
import inspect
import pathlib

import pytest

from vaibify.gui import routeScope
from vaibify.gui.appFactory import fappCreateHubApplication


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

# The audited facts, one entry per lifecycle route. Written by hand from
# reading the handler and everything it calls; every field is checked
# below against the source it describes.
DICT_LIFECYCLE_AUDIT = {
    ("POST", "/api/containers/{sName}/stop"): {
        "sHandler": "fdictStopContainer",
        "bHoldsMutationLock": False,
        "bWritesJournalRecord": False,
        "sTransfer": "invisible",
        "sFinding": (
            "None of the four. The handler runs _fnExecuteStop in a bare "
            "asyncio.to_thread: no mutation lock, no journal record, no "
            "durable registration. A transfer arriving mid-stop sees an "
            "unlocked container with no registered work and commits, so "
            "the departing session's stop lands in the successor's "
            "container. This is the plan's named example of why "
            "lifecycle-transaction is a finding rather than a default."
        ),
    },
    ("POST", "/api/containers/{sName}/start/cancel"): {
        "sHandler": "fresponseHandleCancelStartContainer",
        "bHoldsMutationLock": True,
        "bWritesJournalRecord": True,
        "sTransfer": "adopted",
        "sFinding": (
            "The only one with real machinery, and it is PARTIAL in a way "
            "worth naming. The mutation lock is held for the authorize-"
            "and-flag phase only; it is released before the launch is "
            "terminated and before the bounded wait for the start task to "
            "settle, so the longest part of a cancel runs unlocked. It "
            "writes no record of its own -- it marks the START "
            "operation's record CANCEL_REQUESTED, tolerating a race with "
            "the task's own settlement, because the task stays the single "
            "settler. A transfer during the cancel ADOPTS the running "
            "start; it does not refuse. The refusal that would seem to "
            "cover this case cannot fire -- see "
            "testTheTransferRefusalForACancellingTaskCannotFire."
        ),
    },
    ("POST", "/api/containers/{sName}/settings"): {
        "sHandler": "fdictSetContainerSettings",
        "bHoldsMutationLock": False,
        "bWritesJournalRecord": False,
        "sTransfer": "not-applicable",
        "sFinding": (
            "Classified container-lifecycle for AUTHORIZATION -- one "
            "session must not reconfigure a container another owns -- but "
            "it reaches no container at all. It rewrites vaibify.yml on "
            "the HOST, and the settings take effect at the next start. So "
            "the transfer question does not arise; the race it does have "
            "is two sessions rewriting the same YAML file, which the "
            "mutation lock would not address either."
        ),
    },
}


def _flistResolveLifecycleRoutes():
    """Return the live application's container-lifecycle routes."""
    app = fappCreateHubApplication()
    listResolved = []
    for route in app.routes:
        setMethods = getattr(route, "methods", None)
        if setMethods is None:
            continue
        dictScope = routeScope.fdictResolveRouteScope(
            setMethods, route.path, route.endpoint,
        )
        if dictScope is None:
            continue
        if dictScope["sScope"] != routeScope.S_SCOPE_CONTAINER_LIFECYCLE:
            continue
        for sMethod in sorted(setMethods):
            listResolved.append(
                ((sMethod, route.path), route.endpoint),
            )
    return listResolved


def _fsReachableSource(fnEndpoint, listExtraCallables):
    """Return the handler's source plus that of the callables it drives.

    A lock taken one call down is still a lock the route holds, and the
    lifecycle handlers are all two or three lines that delegate. Reading
    only the handler would report every one of them as holding nothing.
    """
    listSources = [inspect.getsource(fnEndpoint)]
    for fnCallable in listExtraCallables:
        listSources.append(inspect.getsource(fnCallable))
    return "\n".join(listSources)


def _flistCallablesBehind(tRoute):
    """Return the functions each audited handler delegates its work to."""
    from vaibify.gui import registryRoutes, startReservation
    dictBehind = {
        ("POST", "/api/containers/{sName}/stop"): [
            registryRoutes._fnExecuteStop,
        ],
        ("POST", "/api/containers/{sName}/start/cancel"): [
            startReservation.ftCancelStart,
            startReservation._ftMarkCancelRequested,
        ],
        ("POST", "/api/containers/{sName}/settings"): [
            registryRoutes._fbApplyAgentAutoUpdate,
            registryRoutes._fnUpdateYamlScalarField,
        ],
    }
    return dictBehind[tRoute]


# ---------------------------------------------------------------------
# The audit is complete and stays complete.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testEveryLifecycleRouteHasBeenAuditedIndividually():
    """A new lifecycle route must be audited, not inherited into.

    Fail-closed in both directions. The population is resolved from the
    live app rather than from a maintained list, so a route that acquires
    the lifecycle scope arrives here whatever anyone remembered to write
    down.

    Kills: adding a container-lifecycle route without auditing it.
    """
    setLive = {tRoute for tRoute, _ in _flistResolveLifecycleRoutes()}
    assert setLive == set(DICT_LIFECYCLE_AUDIT), (
        f"unaudited lifecycle routes: {sorted(setLive - set(DICT_LIFECYCLE_AUDIT))}; "
        f"audited routes that no longer exist: "
        f"{sorted(set(DICT_LIFECYCLE_AUDIT) - setLive)}"
    )


def testEachAuditedRouteResolvesToTheHandlerItNames():
    """The audit describes the handler that actually serves the path."""
    for tRoute, fnEndpoint in _flistResolveLifecycleRoutes():
        assert fnEndpoint.__name__ == DICT_LIFECYCLE_AUDIT[tRoute][
            "sHandler"
        ], f"{tRoute} is served by {fnEndpoint.__name__}"


@pytest.mark.falsification
def testTheRecordedLockAndJournalFactsMatchTheSource():
    """Each recorded fact is checked against the code it describes.

    The stop route's four "no"s are the reason this file exists, so they
    are asserted rather than asserted-about: if somebody gives the stop
    route a mutation lock, this fails and the audit gets re-read, which
    is the correct outcome for a finding that has stopped being true.

    Kills: recording a lock or a journal write that the route does not
    perform, in either direction.
    """
    listMismatches = []
    for tRoute, fnEndpoint in _flistResolveLifecycleRoutes():
        dictAudit = DICT_LIFECYCLE_AUDIT[tRoute]
        sSource = _fsReachableSource(
            fnEndpoint, _flistCallablesBehind(tRoute),
        )
        bLock = "flockContainerMutationForAppState" in sSource
        bJournal = (
            "operationJournal." in sSource
            or "_fnRequestJournalCancelQuietly" in sSource
        )
        if bLock != dictAudit["bHoldsMutationLock"]:
            listMismatches.append((tRoute, "lock", bLock))
        if bJournal != dictAudit["bWritesJournalRecord"]:
            listMismatches.append((tRoute, "journal", bJournal))
    assert listMismatches == [], (
        f"the audit disagrees with the source it describes: "
        f"{listMismatches}. Re-read the route and update the finding; do "
        f"not edit the flag to match."
    )


def testEveryLifecycleRouteRunsOnTheAmbientAdmission():
    """None of the three has chosen a carrier mode; the record says so.

    The lifecycle scope is inside _SET_AUTHORIZED_CONTAINER_SCOPES, so
    ContainerAwareRoute opens a generic request admission for each of
    these routes -- the same one every other authorized container route
    gets. Recorded because "lifecycle-transaction" invites the reading
    that these routes already carry their own authority, and they do not.
    """
    assert routeScope.S_SCOPE_CONTAINER_LIFECYCLE in (
        routeScope._SET_AUTHORIZED_CONTAINER_SCOPES
    ), (
        "the lifecycle scope left the authorized set, so these routes no "
        "longer get the ambient admission this audit recorded"
    )


@pytest.mark.falsification
def testTheTransferRefusalForACancellingTaskCannotFire():
    """A documented refusal that no state transition can reach.

    ``sessionLifecycle`` refuses a transfer when a durable task's
    ``sState != "running"``, naming "cancellation in progress". The same
    comparison guards the commit point and half of the durable task's own
    commit-time currency check. Nothing in vaibify/ ever ASSIGNS that
    field: it is initialised to "running" on the dataclass and never
    written again, so all three branches are unreachable.

    Coherent rather than broken -- fdictRequestDurableTaskCancel is
    deliberately parked and refuses everything, because Python cannot
    interrupt a worker in asyncio.to_thread -- but three guards that read
    as live protection are not, and the cancel route's audit must not
    credit itself with a refusal that cannot fire.

    This test PINS the finding. It fails the moment somebody writes the
    field, which is exactly when the audit above needs re-reading.

    Kills: writing DurableTaskRecord.sState anywhere in vaibify/ without
    revisiting the lifecycle audit.
    """
    listAssignments = []
    for pathModule in PATH_PACKAGE.rglob("*.py"):
        if "__pycache__" in pathModule.parts:
            continue
        for nodeAssign in ast.walk(ast.parse(pathModule.read_text())):
            listTargets = []
            if isinstance(nodeAssign, ast.Assign):
                listTargets = nodeAssign.targets
            elif isinstance(nodeAssign, ast.AugAssign):
                listTargets = [nodeAssign.target]
            for nodeTarget in listTargets:
                if not isinstance(nodeTarget, ast.Attribute):
                    continue
                if nodeTarget.attr != "sState":
                    continue
                sBase = getattr(nodeTarget.value, "id", "")
                if "Task" not in sBase:
                    continue
                listAssignments.append(
                    f"{pathModule.relative_to(PATH_REPOSITORY)}:"
                    f"{nodeAssign.lineno}"
                )
    assert listAssignments == [], (
        f"a durable task's sState is now written at {listAssignments}, so "
        f"the transfer's cancellation refusal may finally be reachable. "
        f"Re-read DICT_LIFECYCLE_AUDIT for the start/cancel route: its "
        f"transfer behaviour was recorded as 'adopted' precisely because "
        f"that refusal could not fire."
    )
