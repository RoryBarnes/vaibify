"""Plot standardization route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import posixpath

from fastapi import HTTPException, Request

from ..actionCatalog import ffnAgentAction
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    fdictRequireWorkflow,
    fbPlotFormatIsStandardizable,
    _fsPlotStandardPath,
    _fsBuildConvertCommand,
)
from ..fileStatusManager import _flistResolvePlotPaths
from ..routeContext import (
    fdictRequireLaneTupleForCommit,
    fdictCommitWorkflowSave,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)


def _flistStandardizedBasenames(listPlots, sTargetFile):
    """Return basenames of plots that were standardized."""
    listResult = []
    for _sResolved, sBasename in listPlots:
        if sTargetFile and sBasename != sTargetFile:
            continue
        listResult.append(sBasename)
    return listResult


def _fnRejectUnstandardizableFormats(listPlots, sTargetFile):
    """Refuse before converting when no selected plot could convert.

    The refusal names the format, because the message it replaces named
    a cause that was never true: a PNG project got "Check that
    ghostscript or poppler-utils is installed" while both were
    installed and working, and the researcher had no way from that
    sentence to reach the real answer.
    """
    listSelected = _flistStandardizedBasenames(listPlots, sTargetFile)
    listUnsupported = [
        sBasename for sBasename in listSelected
        if not fbPlotFormatIsStandardizable(sBasename)
    ]
    if not listSelected or len(listUnsupported) < len(listSelected):
        return
    raise HTTPException(
        400,
        "Cannot make a standard from "
        + ", ".join(listUnsupported)
        + ". Vaibify renders PDF, PS and EPS figures to a standard "
        "PNG and copies PNG ones; other formats are not supported. "
        "Re-run the step with one of those figure formats.",
    )


def _fsFindPlotPath(listPlots, sFileName):
    """Return the resolved plot path for a given filename."""
    for sResolved, sBasename in listPlots:
        if (sBasename == sFileName
                or sResolved.endswith(sFileName)):
            return sResolved
    return ""


def _fsStandardPathForPlot(sResolved, sBasename):
    """Return the container path of one plot's standard PNG.

    "The standard for this plot" is a thing the panel, the workflow and
    the conversion all name, and the code had no representation for it:
    the same three lines were derived independently everywhere it was
    needed. One derivation means the lookup and the existence check
    cannot disagree about where a standard lives.
    """
    sBase = posixpath.splitext(sBasename)[0]
    return posixpath.join(
        posixpath.dirname(sResolved), _fsPlotStandardPath(sBase),
    )


def _fsFindStandardForFile(listPlots, sFileName):
    """Return the standard PNG path for a given plot filename."""
    for sResolved, sBasename in listPlots:
        if (sBasename == sFileName
                or sResolved.endswith(sFileName)):
            return _fsStandardPathForPlot(sResolved, sBasename)
    return ""


def _flistConvertToStandards(
    dictCtx, sContainerId, listPlots, sTargetFile,
):
    """Convert plot files to standard PNGs inside the container.

    Synchronous because the only caller is the lock-held carrier's
    worker, which already runs in a thread: an ``async def`` here would
    be CALLED in that thread and hand back a coroutine nobody awaits,
    so no plot would ever be converted and the route would report
    success for having done nothing.
    """
    listCommands = []
    listTargets = []
    for sResolved, sBasename in listPlots:
        if sTargetFile and sBasename != sTargetFile:
            continue
        if not fbPlotFormatIsStandardizable(sBasename):
            continue
        sOutputDir = posixpath.dirname(sResolved)
        listCommands.append(_fsBuildConvertCommand(
            sResolved, sOutputDir, sBasename))
        sStandardName = _fsPlotStandardPath(
            posixpath.splitext(sBasename)[0])
        listTargets.append((
            posixpath.join(sOutputDir, sStandardName), sStandardName,
        ))
    if not listCommands:
        return []
    sFullCommand = " && ".join(listCommands)
    dictCtx["docker"].ftResultExecuteCommand(
        sContainerId, sFullCommand,
    )
    return _flistVerifyConverted(dictCtx, sContainerId, listTargets)


def _flistVerifyConverted(dictCtx, sContainerId, listTargets):
    """Return the standard names that actually exist in the container.

    Takes the ``(absolute path, standard name)`` pairs the conversion
    itself built, so what is checked cannot drift from what was
    converted. It used to zip the converted list against the FULL plot
    list and re-apply the target filter -- but the converted list had
    already been filtered, so the two ran offset whenever the target
    was not the first plot in its step: the target's standard was
    paired with a different plot's basename, the filter then dropped
    it, and a conversion that had SUCCEEDED was reported to the
    researcher as "Conversion failed".

    Runs inside the same worker as the conversion that produced them,
    so the check and the effect it checks cannot be separated by an
    ownership hand-over landing between them.
    """
    listVerified = []
    for sAbsolutePath, sStandardName in listTargets:
        iExitCode, _ = dictCtx["docker"].ftResultExecuteCommand(
            sContainerId,
            f"test -f {fsShellQuote(sAbsolutePath)}",
        )
        if iExitCode == 0:
            listVerified.append(sStandardName)
    return listVerified


async def _flistConvertPlotsUnderTheDrain(
    dictCtx, sContainerId, listPlots, sTargetFile, requestHttp,
):
    """Convert this step's plots to standards holding the drain.

    Mode (b) rather than mode (a) for the same reason ``clean-outputs``
    is: the effect is a batch of ``convert``/``gs`` invocations that can
    run for many seconds and crosses a worker-thread ``await``, so the
    drain has to be held for the WORKER's life rather than the
    requesting coroutine's. The supervisor also records the operation
    name, which is what lets a hand-over refusal say
    "standardize-plots is running" instead of "busy".

    The verification execs run inside the same worker deliberately:
    each is a ``test -f`` against a file the conversion just wrote, and
    a report that a standard exists is only worth having if nothing
    could have replaced it in between.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "Standardizing the step's plots",
    )

    def flistConvertThePlots(supervisor=None):
        del supervisor
        return _flistConvertToStandards(
            dictCtx, sContainerId, listPlots, sTargetFile,
        )

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "standardize-plots",
        flistConvertThePlots,
    )
    return dictOutcome["result"]


async def _fdictCheckStandardsExist(
    dictCtx, sContainerId, listPlots,
):
    """Return ``{basename: bool}`` for each plot's standard PNG.

    One TYPED READ per plot, concurrently, rather than one batched
    ``test -f … && echo Y || echo N`` through the general exec
    primitive. The batch was cheaper by one round-trip and cost the
    route its honesty: a primitive handed command text cannot tell an
    existence check from a delete, so the whole route had to be treated
    as mutating and would be refused on the enforced branch.

    N round-trips are affordable HERE specifically, and the reason is a
    measurement rather than an intuition: the panel calls this on step
    EXPANSION — a deliberate click — never on a poll, and it returns
    early when the step declares no plots, so N is one step's plot
    count. ``gather`` puts them in flight together, so the wall cost is
    one round-trip plus change.

    A failed READ now propagates instead of being reported as "no
    standard exists". The old batch answered ``N`` for every plot when
    the exec itself failed, which told the researcher their standards
    were missing when the truth was that vaibify could not look.
    """
    if not listPlots:
        return {}
    listBasenames = [sBasename for _sResolved, sBasename in listPlots]
    listExists = await asyncio.gather(*[
        asyncio.to_thread(
            dictCtx["docker"].fbContainerPathIsFile,
            sContainerId, _fsStandardPathForPlot(sResolved, sBasename),
        )
        for sResolved, sBasename in listPlots
    ])
    return dict(zip(listBasenames, listExists))


def _fnRegisterStandardizePlots(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/standardize-plots."""

    @ffnAgentAction("accept-plots-as-standard")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/standardize-plots"
    )
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_MODE_A_SYNCHRONOUS,
    )
    async def fdictStandardizePlots(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        from datetime import datetime, timezone
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictVars = dictCtx["variables"](sContainerId)
        dictBody = await request.json()
        sTargetFile = dictBody.get("sFileName", "")
        listPlots = _flistResolvePlotPaths(dictStep, dictVars)
        if not listPlots:
            raise HTTPException(
                400, "No plot files in this step")
        _fnRejectUnstandardizableFormats(listPlots, sTargetFile)
        listConverted = await _flistConvertPlotsUnderTheDrain(
            dictCtx, sContainerId, listPlots, sTargetFile, request)
        if not listConverted:
            raise HTTPException(
                500, "Conversion failed: no standard PNGs were "
                "created for "
                + ", ".join(_flistStandardizedBasenames(
                    listPlots, sTargetFile))
                + ". The figure files exist but the converter "
                "produced nothing from them.")
        listStdBasenames = _flistStandardizedBasenames(
            listPlots, sTargetFile)
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        sTimestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
        dictVerification["sLastStandardized"] = sTimestamp
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, request,
            "Recording the standardized plots",
        )
        return {
            "bSuccess": True,
            "listConverted": listConverted,
            "listStandardizedBasenames": listStdBasenames,
            "sTimestamp": sTimestamp,
        }

    @ffnAgentAction("compare-plot")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/compare-plot"
    )
    # typed-read, and here it is the STRONGEST of the six rather than a
    # near-miss: this route resolves two container PATHS out of the
    # workflow document and returns them. It opens no connection at all
    # — not a write, not an exec, not even a typed read — so it reaches
    # no mutation-capable primitive, which is exactly what the
    # declaration asserts. The viewer fetches the two files afterwards
    # through the ``container-read`` figure route, under that route's
    # own authority.
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictComparePlot(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictVars = dictCtx["variables"](sContainerId)
        dictBody = await request.json()
        sFileName = dictBody.get("sFileName", "")
        if not sFileName:
            raise HTTPException(
                400, "sFileName is required")
        listPlots = _flistResolvePlotPaths(dictStep, dictVars)
        sPlotPath = _fsFindPlotPath(listPlots, sFileName)
        sStandardPath = _fsFindStandardForFile(
            listPlots, sFileName)
        if not sStandardPath:
            raise HTTPException(
                404, "No standard found for this file")
        return {
            "sPlotPath": sPlotPath,
            "sStandardPath": sStandardPath,
        }

    @app.get(
        "/api/steps/{sContainerId}/{iStepIndex}/plot-standards"
    )
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictCheckPlotStandards(
        sContainerId: str, iStepIndex: int,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictVars = dictCtx["variables"](sContainerId)
        listPlots = _flistResolvePlotPaths(dictStep, dictVars)
        dictStandards = await _fdictCheckStandardsExist(
            dictCtx, sContainerId, listPlots)
        return {"dictStandards": dictStandards}


def fnRegisterAll(app, dictCtx):
    """Register all plot standardization routes."""
    _fnRegisterStandardizePlots(app, dictCtx)
