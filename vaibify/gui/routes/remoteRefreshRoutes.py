"""The dashboard's open-time refresh of every configured remote.

A researcher who reopens a project after a day away meets orange
Published-copies badges: the cached verify has aged past
``levelGates.F_MAX_STALE_HOURS`` even though it was clean. That is a
black-box status change with nothing visibly wrong, which is exactly
the experience vaibify exists to prevent. So the dashboard asks again
on entry and on reconnect. This route starts one check per CONFIGURED
remote; the poll reports checking / settled / uncheckable per service
while they run, and each badge pulses until its own answer arrives.

The check cannot live in the poll.
``pipelineRoutes._fdictBuildWorkflowEnvelopeDetail`` is built with no
extra container execs and no network I/O, and a poll that reached four
remotes every few seconds would be a different product. So the refresh
is a separate, explicitly-triggered route and the poll only REPORTS.

Three properties are load-bearing:

* A check that cannot complete is UNCHECKABLE with a reason, never
  diverged. It writes nothing, so the last good cached record stands —
  ``scheduledReverify.fdictAttemptOneVerify`` already declines to write
  on failure, and nothing here may add a write of its own.
* A service the workflow has not configured is never marked at all, so
  its badge never pulses for an answer that is not coming. The
  predicate is ``scheduledReverify.flistSelectConfiguredServices``, the
  same one the scheduled loop skips on.
* The checks must not occupy the container. They are NETWORK work —
  ~7s for 29 published paths on a good connection, linear in the file
  count — with a few milliseconds of writing per service. The first
  version registered the sweep as one durable task, which held the
  container's single durable slot for the whole round-trip and refused
  the researcher's own Level 3 verification with "the container is
  busy". Now only the per-service WRITE takes a carrier, so the
  container is genuinely free while the network is in flight
  (reported 2026-08-30).
"""

__all__ = ["fnRegisterAll"]

import asyncio
import logging

from fastapi import Request

from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import (
    fdictRequireLaneTupleForCommit,
    ffilesForWorkflow,
)
from ..routeScope import (
    S_CARRIER_MODE_B_LOCK_HELD,
    ffnDeclareCarrierMode,
)
from ...reproducibility import remoteCheckState, scheduledReverify


logger = logging.getLogger("vaibify")

S_NETWORK_ISOLATED_REASON = (
    "this project's container runs with networking disabled, so no "
    "remote can be reached from it"
)


def _fbContainerSealedOffTheNetwork(sContainerId):
    """Return True when this resource cannot reach any remote.

    A host project has no container to have sealed, and asking Docker
    about a name it never heard of costs the inspect timeout on every
    call — the same reasoning ``syncRoutes._fnRequireNetworkAccess``
    records for the push lane.
    """
    from vaibify.config.registryManager import fbIsHostProject
    from vaibify.docker.containerManager import (
        fbContainerIsNetworkIsolated,
    )
    if fbIsHostProject(sContainerId):
        return False
    return fbContainerIsNetworkIsolated(sContainerId)


def _fnMarkEveryServiceUncheckable(sContainerId, listServices, sReason):
    """Settle each service to uncheckable, naming why nobody asked."""
    for sService in listServices:
        remoteCheckState.fnMarkUncheckable(
            sContainerId, sService, sReason,
        )


def _fdictVerifyWithoutWriting(dictWorkflow, filesRepo, sService):
    """Compare one remote and return the status; write NOTHING.

    The network half, split out so it can run with no carrier at all.
    Everything it touches in the container is a typed READ (hashing
    the published paths), which the mutation gate exempts inside the
    adapter, so it needs no admission — and holding one across a
    round-trip is exactly what this split exists to stop.
    """
    try:
        return {
            "dictStatus": scheduledReverify.fdictVerifyRemoteService(
                filesRepo, dictWorkflow, sService,
            ),
            "sError": "",
        }
    except Exception as errorAny:  # noqa: BLE001 — carried, not raised
        return {"dictStatus": None, "sError": str(errorAny)}


async def _fnWriteOneStatusUnderACarrier(
    dictCarrier, filesRepo, dictStatus,
):
    """Persist one service's status under a briefly-held mode-(b) drain.

    The ONLY container mutation the refresh makes. It takes its own
    short carrier per service rather than one long-lived durable task
    for the whole sweep, which is the correction to the first version
    of this route: mode (c) held the container's single durable slot
    for the entire network round-trip, so a researcher who clicked
    Verify Level 3 in that window was told the container was busy —
    by the refresh their own project-open had started
    (reported 2026-08-30). Measured at ~7s for 29 published paths on
    a good connection, and linear in the file count.
    """
    from .. import commitCarrier

    def fnWriteTheStatus(supervisor=None):
        del supervisor
        scheduledReverify.fnWriteSyncStatus(filesRepo, dictStatus)

    await commitCarrier.fdictRunLockHeldMutation(
        dictCarrier["appState"], dictCarrier["sContainerName"],
        dictCarrier["sContainerId"], dictCarrier["dictLaneTuple"],
        "helper", "remote-status write " + dictStatus["sService"],
        fnWriteTheStatus,
    )


async def _fnCheckOneRemote(
    dictCarrier, dictWorkflow, filesRepo, sService,
):
    """Compare one remote off-carrier, then write its result on one."""
    sContainerId = dictCarrier["sContainerId"]
    dictOutcome = await asyncio.to_thread(
        _fdictVerifyWithoutWriting, dictWorkflow, filesRepo, sService,
    )
    if dictOutcome["dictStatus"] is None:
        remoteCheckState.fnMarkUncheckable(
            sContainerId, sService, dictOutcome["sError"],
        )
        return
    await _fnWriteOneStatusUnderACarrier(
        dictCarrier, filesRepo, dictOutcome["dictStatus"],
    )
    remoteCheckState.fnMarkSettled(sContainerId, sService)


async def _fnRunRefreshWorker(
    dictCtx, dictCarrier, dictWorkflow, filesRepo, listServices,
):
    """Check each configured remote in turn, settling each as it answers.

    Runs as a PLAIN background task, deliberately not a registered
    durable one. Mode (c) exists to make long-running CONTAINER work
    visible to a hand-over, the shutdown drain and the idle watchdog —
    and this is long-running NETWORK work whose container footprint is
    a few milliseconds of writing per service. Registering it as
    durable made the container read busy for the whole round-trip and
    refused the researcher's own Level 3 verification, which needs the
    same single slot. Between writes the container really is idle, so
    being invisible then is the honest answer; while a write is in
    flight its mode-(b) carrier holds the drain and is visible like any
    other mutation.
    """
    sContainerId = dictCarrier["sContainerId"]
    for sService in listServices:
        try:
            await _fnCheckOneRemote(
                dictCarrier, dictWorkflow, filesRepo, sService,
            )
        except Exception as errorCheck:  # noqa: BLE001 — see below
            # Never propagate: a raise from a detached background task
            # is an unretrieved exception nobody sees, and a remote
            # being down must cost a badge reading, not a traceback.
            logger.warning(
                "Remote refresh of %s failed: %s", sService, errorCheck,
            )
            remoteCheckState.fnMarkUncheckable(
                sContainerId, sService, str(errorCheck),
            )
    _fnBumpSoTheBadgesRepaint(dictCtx, sContainerId)


def _fnBumpSoTheBadgesRepaint(dictCtx, sContainerId):
    """Bump the sync epoch so the per-file badges pick the result up.

    Every route that rewrites ``syncStatus.json`` bumps this — the
    verify route says so explicitly, because the epoch is the
    dashboard's only poll-free invalidation signal and the per-file
    octocats read exactly the cache these checks just rewrote. Without
    it the refresh silently improves a record nothing repaints, which
    is how this shipped the first time.

    Once at the end, not once per service: the badge map is repo-wide,
    so one refresh after the last check repaints everything, and a bump
    per service would spend four container git reads on every project
    open. The per-service settling the badges DO show independently is
    the pulse, which rides the poll and needs no epoch at all.
    """
    from ..pipelineServer import fnBumpSyncEpoch
    fnBumpSyncEpoch(dictCtx, sContainerId)


async def _fdictStartTheRefresh(
    dictCtx, sContainerId, dictWorkflow, filesRepo, listServices,
    requestHttp,
):
    """Start the checks in the background and answer immediately.

    A PLAIN task, not a registered durable one — see
    ``_fnRunRefreshWorker`` for why mode (c) was the wrong mode here.
    The lane tuple is resolved NOW, while the request still exists,
    and carried to each per-service write; the carrier revalidates it
    at every commit, so a write attempted after the container changed
    hands is refused rather than landing under a stale owner.
    """
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The remote refresh",
    )
    for sService in listServices:
        remoteCheckState.fnMarkChecking(sContainerId, sService)
    dictCarrier = {
        "appState": requestHttp.app.state,
        "sContainerName": dictLaneTuple["sContainerName"],
        "sContainerId": sContainerId,
        "dictLaneTuple": dictLaneTuple,
    }
    asyncio.create_task(_fnRunRefreshWorker(
        dictCtx, dictCarrier, dictWorkflow, filesRepo, listServices,
    ))
    return {"listChecking": listServices, "listUncheckable": []}


def _fnRegisterRefreshRemotes(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/remotes/refresh."""

    @app.post("/api/workflow/{sContainerId}/remotes/refresh")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictRefreshRemotes(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        # The repo adapter is resolved BEFORE the selection, not after:
        # GitHub's configuration is DERIVED from the checkout's origin
        # remote when no dictRemotes entry records it, and without the
        # adapter the selection silently drops exactly those projects.
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        listServices = scheduledReverify.flistSelectConfiguredServices(
            dictWorkflow, filesRepo,
        )
        if not listServices:
            return {"listChecking": [], "listUncheckable": []}
        if _fbContainerSealedOffTheNetwork(sContainerId):
            _fnMarkEveryServiceUncheckable(
                sContainerId, listServices, S_NETWORK_ISOLATED_REASON,
            )
            return {"listChecking": [], "listUncheckable": listServices}
        return await _fdictStartTheRefresh(
            dictCtx, sContainerId, dictWorkflow, filesRepo,
            listServices, requestHttp,
        )


def fnRegisterAll(app, dictCtx):
    """Register all remote-refresh routes."""
    _fnRegisterRefreshRemotes(app, dictCtx)
