"""Quit: stop this hub, and the windows it spawned, only when nothing is lost.

A Quit is a transaction over several processes. This hub and every
child hub it spawned (each a separate process with its own state) must
all be idle at one moment, and nothing may start between that check and
the shutdown. So the sequence is:

1. Close this hub's admission gate (``mutationAdmission``), then each
   child's, recursively, over the child's peer-authenticated host control
   socket (``close-admissions``). A child's gate carries a lease, a
   backstop against a parent that dies after closing it; this hub's own
   gate has none.
2. Renew the children's leases for the whole Quit, through shutdown,
   until each child has exited.
3. Settle: wait up to :data:`F_QUIT_SETTLE_SECONDS` for the carrier
   mutations and durable tasks already admitted, here and in every
   child, to finish. The gates are closed, so these counts only fall.
4. Final check: :func:`serverLifespan.flistHubBusyReasons` here and
   ``describe-busy`` in every descendant. Anything busy, unsettled,
   unreachable or slow fails the Quit.
5. Optionally stop the environments this hub holds.

On ANY exit other than success, every gate this Quit closed is reopened
by its id (:func:`fnReopenEverything`); an unreachable child cannot be
reopened, and its lease reopens it.

Children are found from the ``Popen`` handles ``POST /api/session/spawn``
keeps, each beside its port, and are trusted only while alive and named
by the live hub slot for that port.
"""

__all__ = [
    "F_QUIT_SETTLE_SECONDS",
    "F_CHILD_GATE_LEASE_SECONDS",
    "F_CHILD_CONTROL_TIMEOUT_SECONDS",
    "S_NOT_RESPONDING",
    "QuitRefusedError",
    "fnReopenEverything",
    "flistTrustedChildPorts",
    "ffChildTimeoutFromRequest",
    "fsDescribeTerminalWarning",
    "fdictDescribeThisHub",
    "fdictCloseThisHubTree",
    "fbRenewThisHubTree",
    "fnReopenThisHubTree",
    "fdictBuildQuitPreflight",
    "fdictRunQuit",
    "fnStopLeaseRenewal",
]

import asyncio
import logging

from vaibify.config import mutationAdmission
from . import containerOwnership

logger = logging.getLogger("vaibify")

F_QUIT_SETTLE_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_QUIT_SETTLE_SECONDS", 10.0,
)
F_CHILD_GATE_LEASE_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_QUIT_CHILD_LEASE_SECONDS", 30.0,
)
F_CHILD_CONTROL_TIMEOUT_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_QUIT_CHILD_TIMEOUT_SECONDS", 5.0,
    )
)
F_SETTLE_POLL_SECONDS = 0.2
S_THIS_WINDOW = "this window"
S_NOT_RESPONDING = "a vaibify window is not responding"


class QuitRefusedError(Exception):
    """The Quit was refused; ``dictDetail`` says why, and what it undid."""

    def __init__(self, listReasons, listStoppedEnvironments=None,
                 sLeaseNote=""):
        self.dictDetail = {
            "sMessage": "vaibify did not quit: " + "; ".join(
                dictReason["sWindow"] + ": " + dictReason["sReason"]
                for dictReason in listReasons
            ) + ".",
            "listReasons": listReasons,
            "listStoppedEnvironments": list(listStoppedEnvironments or []),
            "sLeaseNote": sLeaseNote,
        }
        super().__init__(self.dictDetail["sMessage"])


# ---------------------------------------------------------------------
# Finding the children.
# ---------------------------------------------------------------------

def _fbChildIsTheHubOnItsPort(processChild):
    """Return True when a spawned child is alive and holds its hub slot.

    A port alone is not trusted: the child may have died and another
    process taken the port, so the live slot for the port must name the
    child's own pid before its socket is spoken to.
    """
    from vaibify.config.sessionRegistry import fdictReadHubSlotByPort
    iPort = getattr(processChild, "iHubPort", 0)
    if not isinstance(iPort, int) or iPort <= 0:
        return False
    if processChild.poll() is not None:
        return False
    return fdictReadHubSlotByPort(iPort).get("iPid") == processChild.pid


def flistTrustedChildPorts(appState):
    """Return the ports of the spawned children this hub may speak to."""
    return [
        processChild.iHubPort
        for processChild in list(getattr(appState, "listSpawnedChildren", []))
        if _fbChildIsTheHubOnItsPort(processChild)
    ]


def _flistUntrustedChildPorts(appState):
    """Return ports of spawned children that are alive but not verifiable."""
    return [
        getattr(processChild, "iHubPort", 0)
        for processChild in list(getattr(appState, "listSpawnedChildren", []))
        if processChild.poll() is None
        and not _fbChildIsTheHubOnItsPort(processChild)
    ]


async def _fdictAskChild(iPort, dictRequest, fTimeoutSeconds):
    """Send one control request to a child; None when it did not answer."""
    from .hostControlChannel import HostControlError, fdictSendHostControlRequest
    dictRequest = dict(dictRequest, fTimeoutSeconds=fTimeoutSeconds / 2.0)
    try:
        dictResponse = await asyncio.to_thread(
            fdictSendHostControlRequest, iPort, dictRequest, fTimeoutSeconds,
        )
    except (HostControlError, OSError) as error:
        logger.warning("Quit: vaibify window on port %s did not answer: %s",
                       iPort, error)
        return None
    return dictResponse if dictResponse.get("bAccepted") else None


def ffChildTimeoutFromRequest(dictRequest):
    """Return the time a request allows this hub's own children."""
    fTimeout = dictRequest.get("fTimeoutSeconds")
    if isinstance(fTimeout, (int, float)) and not isinstance(fTimeout, bool):
        return max(0.5, float(fTimeout))
    return F_CHILD_CONTROL_TIMEOUT_SECONDS


# ---------------------------------------------------------------------
# The three control operations, each recursive over this hub's children.
# ---------------------------------------------------------------------

def _fiCountOpenTerminals(appState):
    """Return how many terminal executions this hub holds open."""
    dictRegistry = getattr(appState, "dictTerminalExecutionRecords", None)
    if not dictRegistry:
        return 0
    return sum(len(dictByOperation) for dictByOperation in dictRegistry.values())


async def fdictDescribeThisHub(app, dictCtx, fTimeoutSeconds):
    """Return this hub's busy reasons and counts, and its children's.

    Read-only. A child that does not answer is reported as such, never
    left out, because an unanswered child is exactly the one a Quit
    must not assume idle.
    """
    from . import serverLifespan
    listReasons = await asyncio.to_thread(
        serverLifespan.flistHubBusyReasons, app, dictCtx,
    )
    listChildren = []
    for iPort in flistTrustedChildPorts(app.state):
        dictChild = await _fdictAskChild(
            iPort, {"sOperation": "describe-busy"}, fTimeoutSeconds,
        )
        listChildren.append(
            dictChild if dictChild is not None
            else {"iPort": iPort, "bResponding": False}
        )
    for iPort in _flistUntrustedChildPorts(app.state):
        listChildren.append({"iPort": iPort, "bResponding": False})
    return {
        "bAccepted": True, "bResponding": True,
        "iPort": getattr(app.state, "iHubPort", 0) or 0,
        "listReasons": listReasons,
        "iInFlightMutations": serverLifespan.fiCountInFlightCarrierWork(app),
        "iOpenTerminals": _fiCountOpenTerminals(app.state),
        "bAdmissionsClosed": mutationAdmission.fbAdmissionGateClosed(),
        "listChildren": listChildren,
    }


async def fdictCloseThisHubTree(app, fLeaseSeconds, fTimeoutSeconds):
    """Close this hub's gate with a lease, then each child's; return the tree.

    If any child cannot be closed, everything this call closed is
    reopened before refusing, so a refusal leaves no gate behind.
    """
    try:
        sGateId = mutationAdmission.fsCloseAdmissions(fLeaseSeconds)
    except mutationAdmission.AdmissionsClosedError as error:
        return {"bAccepted": False, "sError": str(error)}
    dictTree = {
        "iPort": getattr(app.state, "iHubPort", 0) or 0,
        "sGateId": sGateId, "listChildren": [],
    }
    for iPort in flistTrustedChildPorts(app.state):
        dictChild = await _fdictAskChild(iPort, {
            "sOperation": "close-admissions", "fLeaseSeconds": fLeaseSeconds,
        }, fTimeoutSeconds)
        if dictChild is None:
            await fnReopenThisHubTree(app, dictTree, fTimeoutSeconds)
            return {"bAccepted": False, "sError": (
                f"the vaibify window on port {iPort} did not close its gate"
            )}
        dictTree["listChildren"].append(dictChild["dictGateTree"])
    return {"bAccepted": True, "dictGateTree": dictTree}


async def fbRenewThisHubTree(app, dictGateTree, fLeaseSeconds,
                             fTimeoutSeconds):
    """Renew this hub's gate and every child's in the tree; True if all held."""
    bRenewed = mutationAdmission.fbRenewAdmissions(
        dictGateTree.get("sGateId", ""), fLeaseSeconds,
    )
    for dictChildTree in dictGateTree.get("listChildren", []):
        bRenewed = await _fbRenewChildTree(
            dictChildTree, fLeaseSeconds, fTimeoutSeconds,
        ) and bRenewed
    return bRenewed


async def _fbRenewChildTree(dictChildTree, fLeaseSeconds, fTimeoutSeconds):
    """Renew one child's tree over its socket; True when it held."""
    dictChild = await _fdictAskChild(dictChildTree.get("iPort", 0), {
        "sOperation": "close-admissions", "fLeaseSeconds": fLeaseSeconds,
        "dictGateTree": dictChildTree,
    }, fTimeoutSeconds)
    return dictChild is not None


async def fnReopenThisHubTree(app, dictGateTree, fTimeoutSeconds):
    """Reopen this hub's gate by its id, then each child's, best effort."""
    del app
    mutationAdmission.fnReopenAdmissions(dictGateTree.get("sGateId", ""))
    for dictChildTree in dictGateTree.get("listChildren", []):
        await _fdictAskChild(dictChildTree.get("iPort", 0), {
            "sOperation": "reopen-admissions", "dictGateTree": dictChildTree,
        }, fTimeoutSeconds)


# ---------------------------------------------------------------------
# Reasons, flattened across windows, for the preflight and the Quit.
# ---------------------------------------------------------------------

def _fsWindowName(iPort):
    return f"the vaibify window on port {iPort}"


def _flistFlattenChildReasons(listChildren):
    """Return every descendant's reasons, labelled by window."""
    listReasons = []
    for dictChild in listChildren:
        sWindow = _fsWindowName(dictChild.get("iPort", 0))
        if not dictChild.get("bResponding", True):
            listReasons.append({"sWindow": sWindow,
                                "sReason": S_NOT_RESPONDING})
            continue
        listReasons.extend(
            {"sWindow": sWindow, "sReason": sReason}
            for sReason in dictChild.get("listReasons", [])
        )
        listReasons.extend(
            _flistFlattenChildReasons(dictChild.get("listChildren", [])))
    return listReasons


def _fiSumOverTree(listChildren, sKey):
    """Return a count summed over every descendant that answered."""
    return sum(
        dictChild.get(sKey, 0)
        + _fiSumOverTree(dictChild.get("listChildren", []), sKey)
        for dictChild in listChildren
    )


def _flistWindowsOfTree(listChildren):
    """Return ``{iPort, bResponding}`` for every descendant window."""
    listWindows = []
    for dictChild in listChildren:
        listWindows.append({"iPort": dictChild.get("iPort", 0),
                            "bResponding": dictChild.get("bResponding", True)})
        listWindows.extend(_flistWindowsOfTree(
            dictChild.get("listChildren", [])))
    return listWindows


def fsDescribeTerminalWarning(iOpenTerminals):
    """Return the terminal warning, in the terminal contract's words.

    Vaibify cannot prove a terminal's programs stopped (the
    terminal-sessions contract), so the warning says they may keep
    running and names the reconcile that settles it, never "closed".
    """
    if iOpenTerminals <= 0:
        return ""
    sNoun = "terminal" if iOpenTerminals == 1 else "terminals"
    return (
        f"{iOpenTerminals} {sNoun} will disconnect. Programs started in "
        "them may "
        "keep running inside the environment; vaibify cannot prove they "
        "stopped. Run `vaibify reconcile` afterwards to settle that."
    )


def _ftHeldEnvironments(app, dictCtx):
    """Return (running held environment names, bUnknown) for this hub."""
    from vaibify.config.registryManager import fbIsHostProject
    listHeld = [
        sName for sName in list(getattr(app.state, "dictContainerOwners", {}))
        if not fbIsHostProject(sName)
    ]
    if not listHeld:
        return [], False
    try:
        listRunning = dictCtx["docker"].flistGetRunningContainers()
    except Exception as error:  # noqa: BLE001 - reported, never guessed
        mutationAdmission.fnReRaiseControlPlaneRefusal(error)
        logger.warning("Quit: could not ask Docker what runs: %s", error)
        return listHeld, True
    setRunning = {dictRow.get("sName", "") for dictRow in listRunning}
    return [sName for sName in listHeld if sName in setRunning], False


async def fdictBuildQuitPreflight(app, dictCtx):
    """Return what a Quit would leave running, close, warn, or refuse on."""
    dictThis = await fdictDescribeThisHub(
        app, dictCtx, F_CHILD_CONTROL_TIMEOUT_SECONDS,
    )
    listReasons = [
        {"sWindow": S_THIS_WINDOW, "sReason": sReason}
        for sReason in dictThis["listReasons"]
    ] + _flistFlattenChildReasons(dictThis["listChildren"])
    listRunning, bUnknown = await asyncio.to_thread(
        _ftHeldEnvironments, app, dictCtx,
    )
    iOpenTerminals = dictThis["iOpenTerminals"] + _fiSumOverTree(
        dictThis["listChildren"], "iOpenTerminals",
    )
    return {
        "listReasons": listReasons,
        "bCanQuit": not listReasons,
        "listRunningEnvironments": listRunning,
        "bEnvironmentsUnknown": bUnknown,
        "listChildWindows": _flistWindowsOfTree(dictThis["listChildren"]),
        "iOpenTerminals": iOpenTerminals,
        "sTerminalWarning": fsDescribeTerminalWarning(iOpenTerminals),
    }


# ---------------------------------------------------------------------
# The Quit itself.
# ---------------------------------------------------------------------

async def _ftCloseChildGates(app):
    """Close every trusted child's gate tree; return (trees, unreachable)."""
    listTrees, listUnreachable = [], []
    for iPort in flistTrustedChildPorts(app.state):
        dictChild = await _fdictAskChild(iPort, {
            "sOperation": "close-admissions",
            "fLeaseSeconds": F_CHILD_GATE_LEASE_SECONDS,
        }, F_CHILD_CONTROL_TIMEOUT_SECONDS)
        if dictChild is None:
            listUnreachable.append(iPort)
        else:
            listTrees.append(dictChild["dictGateTree"])
    listUnreachable.extend(_flistUntrustedChildPorts(app.state))
    return listTrees, listUnreachable


async def _fnRenewChildLeasesUntilStopped(app, listChildTrees):
    """Renew every child's lease every third of a lease, until cancelled.

    A renewal that fails before SIGTERM is scheduled fails the Quit
    (the final check reads the flag); after it, the child's ``Popen``
    handle terminates it in the shutdown hook regardless, so the failure
    is only logged.
    """
    while True:
        await asyncio.sleep(F_CHILD_GATE_LEASE_SECONDS / 3.0)
        for dictChildTree in listChildTrees:
            if await _fbRenewChildTree(
                dictChildTree, F_CHILD_GATE_LEASE_SECONDS,
                F_CHILD_CONTROL_TIMEOUT_SECONDS,
            ):
                continue
            if getattr(app.state, "bQuitTerminationScheduled", False):
                logger.warning(
                    "Quit: could not renew the gate of the vaibify window "
                    "on port %s after shutdown began; it is terminated by "
                    "its process handle.", dictChildTree.get("iPort"))
            else:
                app.state.bQuitRenewalFailed = True


def fnStopLeaseRenewal(app):
    """Cancel the Quit's lease renewal, if one is running."""
    taskRenewal = getattr(app.state, "taskQuitLeaseRenewal", None)
    if taskRenewal is not None and not taskRenewal.done():
        taskRenewal.cancel()
    app.state.taskQuitLeaseRenewal = None


async def _fdictDescribeChildren(listChildTrees):
    """Return ``describe-busy`` for each closed child, or its silence."""
    listChildren = []
    for dictChildTree in listChildTrees:
        iPort = dictChildTree.get("iPort", 0)
        dictChild = await _fdictAskChild(
            iPort, {"sOperation": "describe-busy"},
            F_CHILD_CONTROL_TIMEOUT_SECONDS,
        )
        listChildren.append(dictChild if dictChild is not None
                            else {"iPort": iPort, "bResponding": False})
    return listChildren


async def _fnSettle(app, listChildTrees):
    """Wait, bounded, for work already admitted here and in children."""
    from . import serverLifespan
    fDeadline = asyncio.get_running_loop().time() + F_QUIT_SETTLE_SECONDS
    while asyncio.get_running_loop().time() < fDeadline:
        listChildren = await _fdictDescribeChildren(listChildTrees)
        if any(not dictChild.get("bResponding", True)
               for dictChild in listChildren):
            return
        iInFlight = serverLifespan.fiCountInFlightCarrierWork(app) + (
            _fiSumOverTree(listChildren, "iInFlightMutations"))
        if iInFlight == 0:
            return
        await asyncio.sleep(F_SETTLE_POLL_SECONDS)


async def _flistFinalReasons(app, dictCtx, listChildTrees):
    """Return every reason, in every window, this Quit must not proceed."""
    from . import serverLifespan
    listReasons = [
        {"sWindow": S_THIS_WINDOW, "sReason": sReason}
        for sReason in await asyncio.to_thread(
            serverLifespan.flistHubBusyReasons, app, dictCtx)
    ]
    listReasons.extend(_flistFlattenChildReasons(
        await _fdictDescribeChildren(listChildTrees)))
    if getattr(app.state, "bQuitRenewalFailed", False):
        listReasons.append({"sWindow": S_THIS_WINDOW, "sReason": (
            "a vaibify window stopped answering while the Quit settled")})
    return listReasons


async def _flistStopHeldEnvironments(app, dictCtx):
    """Stop the environments this hub holds; name every one already stopped."""
    from vaibify.config.registryManager import fdictGetProject
    from .registryRoutes import _fnExecuteStop
    listRunning, _bUnknown = await asyncio.to_thread(
        _ftHeldEnvironments, app, dictCtx,
    )
    listStopped = []
    for sName in listRunning:
        dictProject = fdictGetProject(sName) or {}
        try:
            await asyncio.to_thread(
                _fnExecuteStop,
                dictProject.get("sContainerName") or sName,
            )
        except Exception as error:  # noqa: BLE001 - reported with its state
            mutationAdmission.fnReRaiseControlPlaneRefusal(error)
            raise QuitRefusedError(
                [{"sWindow": S_THIS_WINDOW,
                  "sReason": f"stopping '{sName}' failed ({error})"}],
                listStoppedEnvironments=listStopped,
            )
        listStopped.append(sName)
    return listStopped


async def fnReopenEverything(app, sOwnGateId, listChildTrees):
    """Reopen this Quit's own gate, then every child tree it closed."""
    mutationAdmission.fnReopenAdmissions(sOwnGateId)
    for dictChildTree in listChildTrees:
        await _fdictAskChild(dictChildTree.get("iPort", 0), {
            "sOperation": "reopen-admissions", "dictGateTree": dictChildTree,
        }, F_CHILD_CONTROL_TIMEOUT_SECONDS)


def _fsLeaseNote(listUnreachable):
    """Return the note for children whose gate only their lease reopens."""
    if not listUnreachable:
        return ""
    return (
        "A vaibify window that did not answer could not be reopened; it "
        f"accepts new work again once its lease ends, within "
        f"{int(F_CHILD_GATE_LEASE_SECONDS)} seconds."
    )


async def fdictRunQuit(app, dictCtx, bStopEnvironments):
    """Run the whole Quit; return what it did, or raise QuitRefusedError.

    Every gate this call closes is reopened on any exit but success --
    a refusal, a child that dies or stops answering, a failed stop, an
    exception, or the request being cancelled.
    """
    try:
        sOwnGateId = mutationAdmission.fsCloseAdmissions()
    except mutationAdmission.AdmissionsClosedError as error:
        raise QuitRefusedError([{"sWindow": S_THIS_WINDOW,
                                 "sReason": str(error)}])
    listChildTrees, listUnreachable = [], []
    app.state.bQuitRenewalFailed = False
    bSucceeded = False
    try:
        listChildTrees, listUnreachable = await _ftCloseChildGates(app)
        if listUnreachable:
            raise QuitRefusedError([
                {"sWindow": _fsWindowName(iPort), "sReason": S_NOT_RESPONDING}
                for iPort in listUnreachable
            ], sLeaseNote=_fsLeaseNote(listUnreachable))
        app.state.taskQuitLeaseRenewal = asyncio.ensure_future(
            _fnRenewChildLeasesUntilStopped(app, listChildTrees))
        await _fnSettle(app, listChildTrees)
        listReasons = await _flistFinalReasons(app, dictCtx, listChildTrees)
        if listReasons:
            raise QuitRefusedError(listReasons, sLeaseNote=_fsLeaseNote([
                dictReason for dictReason in listReasons
                if dictReason["sReason"] == S_NOT_RESPONDING
            ]))
        listStopped = []
        if bStopEnvironments:
            listStopped = await _flistStopHeldEnvironments(app, dictCtx)
        bSucceeded = True
        return {"bQuitting": True, "listStoppedEnvironments": listStopped,
                "iChildWindows": len(listChildTrees)}
    finally:
        if not bSucceeded:
            await fnReopenEverything(app, sOwnGateId, listChildTrees)
            fnStopLeaseRenewal(app)
