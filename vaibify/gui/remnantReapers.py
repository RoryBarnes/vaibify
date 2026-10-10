"""The hub's reapers: one cadence, one record of what each pass did.

A reaper deletes garbage the hub can PROVE is garbage: a lock file no
process holds, a secret file no container mounts, a terminal whose
owning hub is dead. Before this module each reaper was its own
startup hook, and a hook that raised was logged once and never ran
again; the secret sweep in particular called an attribute its client
did not have, swallowed the ``AttributeError``, and silently did
nothing for as long as it ran. Every reaper now runs through one pass, and the
pass records one outcome per reaper -- ``ran`` with a count,
``forbidden`` with the reason it declined, or ``failed`` with the
remedy -- so a reaper that cannot run is visible rather than absent.

The pass runs each reaper in a worker thread. It never blocks the
event loop, and it never delays readiness: the first pass is a task
created by a startup hook, and the loop repeats it on a cadence or at
once when a rescan is requested.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("vaibify")

__all__ = [
    "F_REAPER_PASS_INTERVAL_SECONDS",
    "S_OUTCOME_FAILED",
    "S_OUTCOME_FORBIDDEN",
    "S_OUTCOME_RAN",
    "S_REAPER_PASS_IN_FLIGHT_SENTENCE",
    "fbReaperPassInFlight",
    "fdictBuildReaperOutcome",
    "fdictReaperHealth",
    "ffnWrapSweepAsReaper",
    "fnRecordReaperOutcome",
    "fnRegisterPostPassScan",
    "fnRegisterReaper",
    "fnRegisterReaperLoop",
    "fnRequestRescan",
    "fnRunReaperOnce",
    "fnRunReaperPass",
]

S_OUTCOME_RAN = "ran"
S_OUTCOME_FORBIDDEN = "forbidden"
S_OUTCOME_FAILED = "failed"

# Matches the disposable-reclaim cadence: the garbage a reaper removes
# costs nothing while it waits, and a researcher who wants it gone now
# has the rescan request.
F_REAPER_PASS_INTERVAL_SECONDS = 600.0

S_REAPER_PASS_IN_FLIGHT_SENTENCE = (
    "vaibify is ending sessions left by an earlier vaibify window; "
    "try again in a moment."
)


def fdictBuildReaperOutcome(sOutcome, iRemoved=0, sReason="", sRemedy=""):
    """Return the one shape every reaper reports."""
    return {
        "iRemoved": int(iRemoved),
        "sOutcome": sOutcome,
        "sReason": sReason,
        "sRemedy": sRemedy,
    }


def fnRegisterReaper(app, sReaperName, fnReaper):
    """Add a named reaper to the app's registry.

    ``fnReaper`` is a synchronous function of the route context that
    returns :func:`fdictBuildReaperOutcome`'s shape. It runs in a
    worker thread, so it may block on files or the daemon.
    """
    listReapers = getattr(app.state, "listRemnantReapers", None)
    if listReapers is None:
        listReapers = []
        app.state.listRemnantReapers = listReapers
    listReapers.append((sReaperName, fnReaper))


def ffnWrapSweepAsReaper(fnSweep):
    """Adapt a zero-argument sweep returning a count or None to a reaper."""

    def fdictRunSweep(dictCtx):
        del dictCtx
        iRemoved = fnSweep()
        return fdictBuildReaperOutcome(
            S_OUTCOME_RAN, iRemoved=iRemoved if isinstance(iRemoved, int) else 0,
        )
    return fdictRunSweep


def fdictReaperHealth(app):
    """Return the per-reaper outcome record, creating it on first use."""
    dictHealth = getattr(app.state, "dictReaperHealth", None)
    if dictHealth is None:
        dictHealth = {}
        app.state.dictReaperHealth = dictHealth
    return dictHealth


def fnRecordReaperOutcome(app, sReaperName, dictOutcome):
    """Record one reaper's outcome and log it on one line."""
    dictRecord = {
        "sReaperName": sReaperName,
        "sLastRunIso": datetime.now(timezone.utc).isoformat(),
        "sOutcome": dictOutcome["sOutcome"],
        "iRemoved": int(dictOutcome.get("iRemoved", 0)),
        "sReason": dictOutcome.get("sReason", ""),
        "sRemedy": dictOutcome.get("sRemedy", ""),
    }
    fdictReaperHealth(app)[sReaperName] = dictRecord
    logger.info(
        "REAPER %s: %s, removed %d%s",
        sReaperName, dictRecord["sOutcome"], dictRecord["iRemoved"],
        f" ({dictRecord['sReason']})" if dictRecord["sReason"] else "",
    )


def _fdictRunOneReaper(sReaperName, fnReaper, dictCtx):
    """Run one reaper in the calling thread; a raise becomes ``failed``."""
    try:
        dictOutcome = fnReaper(dictCtx)
    except Exception as errorAny:  # noqa: BLE001 — recorded, never raised
        logger.warning("REAPER %s raised", sReaperName, exc_info=True)
        return fdictBuildReaperOutcome(
            S_OUTCOME_FAILED,
            sReason=f"{type(errorAny).__name__}: {errorAny}",
            sRemedy=(
                "This cleanup could not run. Check vaibify.log for the "
                "traceback and restart the hub once the cause is fixed."
            ),
        )
    if not isinstance(dictOutcome, dict) or "sOutcome" not in dictOutcome:
        return fdictBuildReaperOutcome(
            S_OUTCOME_FAILED,
            sReason="the reaper returned no outcome",
            sRemedy="This is a vaibify defect; report it with vaibify.log.",
        )
    return dictOutcome


async def fnRunReaperOnce(app, sReaperName, fnReaper, dictCtx):
    """Run one reaper in a worker thread and record its outcome."""
    dictOutcome = await asyncio.to_thread(
        _fdictRunOneReaper, sReaperName, fnReaper, dictCtx,
    )
    fnRecordReaperOutcome(app, sReaperName, dictOutcome)


def fbReaperPassInFlight(appState):
    """Return True while a pass is running its reapers.

    Takes the state rather than the app because the claim path, its
    one caller outside this module, holds only the state.
    """
    return bool(getattr(appState, "bReaperPassInFlight", False))


async def fnRunReaperPass(app, dictCtx):
    """Run every registered reaper once, each in a thread, and record it.

    Reapers run one after another: several of them take the same flocks
    and journal lock, and serializing them keeps "which reaper ended
    this" answerable from the log.
    """
    app.state.bReaperPassInFlight = True
    try:
        for sReaperName, fnReaper in list(
            getattr(app.state, "listRemnantReapers", []),
        ):
            await fnRunReaperOnce(app, sReaperName, fnReaper, dictCtx)
    finally:
        app.state.bReaperPassInFlight = False
    fnScan = getattr(app.state, "fnRemnantScanAfterPass", None)
    if fnScan is not None:
        await fnScan(app, dictCtx)


def fnRegisterPostPassScan(app, fnScan):
    """Name the coroutine function every pass ends with: the scan.

    The scan classifies what the reapers could not prove, so it runs
    after them, from the same pass, and never on a request path.
    """
    app.state.fnRemnantScanAfterPass = fnScan


def fnRequestRescan(app):
    """Ask the loop to start a pass now instead of at the next tick."""
    if hasattr(app.state, "eventReaperRescan"):
        app.state.eventReaperRescan.set()


async def _fnReaperLoop(app, dictCtx, fInterval):
    """Run a pass at once, then every ``fInterval`` seconds or on request."""
    while True:
        # Cleared BEFORE the pass: a rescan requested while a pass is
        # running asks for what that pass cannot have seen, so it must
        # start the next one rather than be erased by its end.
        app.state.eventReaperRescan.clear()
        try:
            await fnRunReaperPass(app, dictCtx)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 — one pass, not the loop
            logger.warning("reaper pass failed", exc_info=True)
        try:
            await asyncio.wait_for(
                app.state.eventReaperRescan.wait(), fInterval)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            return


def fnRegisterReaperLoop(app, dictCtx, fInterval=None):
    """Install the reaper loop as a lifespan task.

    The start hook only creates the task, so the first pass never
    delays readiness; the stop hook cancels it.
    """
    from . import serverLifespan
    fIntervalEffective = (
        fInterval if fInterval is not None
        else F_REAPER_PASS_INTERVAL_SECONDS
    )

    async def fnStartReaperLoop(app):
        app.state.eventReaperRescan = asyncio.Event()
        app.state.taskRemnantReapers = asyncio.create_task(
            _fnReaperLoop(app, dictCtx, fIntervalEffective),
            name="vaibify-remnant-reapers",
        )

    async def fnStopReaperLoop(app):
        await serverLifespan.fnCancelBackgroundTask(
            app, "taskRemnantReapers")

    serverLifespan.fnRegisterLifespanTask(
        app, fnStartReaperLoop, fnStopReaperLoop)
