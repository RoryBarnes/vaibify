"""Plot standardization route handlers."""

import asyncio
import posixpath

from fastapi import HTTPException, Request

from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    fdictRequireWorkflow,
    _fsPlotStandardPath,
    _fsBuildConvertCommand,
)
from ..fileStatusManager import _flistResolvePlotPaths


def _flistStandardizedBasenames(listPlots, sTargetFile):
    """Return basenames of plots that were standardized."""
    listResult = []
    for _sResolved, sBasename in listPlots:
        if sTargetFile and sBasename != sTargetFile:
            continue
        listResult.append(sBasename)
    return listResult


def _fsFindPlotPath(listPlots, sFileName):
    """Return the resolved plot path for a given filename."""
    for sResolved, sBasename in listPlots:
        if (sBasename == sFileName
                or sResolved.endswith(sFileName)):
            return sResolved
    return ""


def _fsFindStandardForFile(listPlots, sFileName):
    """Return the standard PNG path for a given plot filename."""
    for sResolved, sBasename in listPlots:
        if (sBasename == sFileName
                or sResolved.endswith(sFileName)):
            sBase = posixpath.splitext(sBasename)[0]
            sDir = posixpath.dirname(sResolved)
            return posixpath.join(
                sDir, _fsPlotStandardPath(sBase))
    return ""


async def _flistConvertToStandards(
    dictCtx, sContainerId, listPlots, sTargetFile,
):
    """Convert plot files to standard PNGs inside the container."""
    listCommands = []
    listConverted = []
    for sResolved, sBasename in listPlots:
        if sTargetFile and sBasename != sTargetFile:
            continue
        sOutputDir = posixpath.dirname(sResolved)
        sCommand = _fsBuildConvertCommand(
            sResolved, sOutputDir, sBasename)
        listCommands.append(sCommand)
        sBase = posixpath.splitext(sBasename)[0]
        listConverted.append(_fsPlotStandardPath(sBase))
    if not listCommands:
        return []
    sFullCommand = " && ".join(listCommands)
    await asyncio.to_thread(
        dictCtx["docker"].ftResultExecuteCommand,
        sContainerId, sFullCommand,
    )
    return await _flistVerifyConverted(
        dictCtx, sContainerId, listPlots,
        listConverted, sTargetFile,
    )


async def _flistVerifyConverted(
    dictCtx, sContainerId, listPlots, listConverted,
    sTargetFile,
):
    """Return only the basenames whose standard PNGs exist."""
    listVerified = []
    for sConverted, (sResolved, sBasename) in zip(
        listConverted, listPlots,
    ):
        if sTargetFile and sBasename != sTargetFile:
            continue
        sDir = posixpath.dirname(sResolved)
        sFullPath = posixpath.join(sDir, sConverted)
        iExitCode, _ = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId,
            f"test -f {fsShellQuote(sFullPath)}",
        )
        if iExitCode == 0:
            listVerified.append(sConverted)
    return listVerified


async def _fdictCheckStandardsExist(
    dictCtx, sContainerId, listPlots,
):
    """Check which standard PNGs exist in the container."""
    if not listPlots:
        return {}
    listPaths = []
    listBasenames = []
    for sResolved, sBasename in listPlots:
        sBase = posixpath.splitext(sBasename)[0]
        sDir = posixpath.dirname(sResolved)
        sStandardPath = posixpath.join(
            sDir, _fsPlotStandardPath(sBase))
        listPaths.append(sStandardPath)
        listBasenames.append(sBasename)
    sCheckCommand = " && ".join(
        f'test -f {fsShellQuote(sPath)} && echo "Y"'
        f' || echo "N"'
        for sPath in listPaths
    )
    tResult = await asyncio.to_thread(
        dictCtx["docker"].ftResultExecuteCommand,
        sContainerId, sCheckCommand,
    )
    sOutput = tResult[1] if tResult else ""
    listLines = sOutput.strip().split("\n")
    dictResult = {}
    for iIdx, sBasename in enumerate(listBasenames):
        if iIdx < len(listLines):
            dictResult[sBasename] = (
                listLines[iIdx].strip() == "Y")
        else:
            dictResult[sBasename] = False
    return dictResult


def _fnRegisterStandardizePlots(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/standardize-plots."""

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/standardize-plots"
    )
    async def fnStandardizePlots(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        from datetime import datetime, timezone
        dictCtx["require"]()
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
        listConverted = await _flistConvertToStandards(
            dictCtx, sContainerId, listPlots, sTargetFile)
        if not listConverted:
            raise HTTPException(
                500, "Conversion failed: no standard PNGs "
                "were created. Check that ghostscript or "
                "poppler-utils is installed in the container.")
        listStdBasenames = _flistStandardizedBasenames(
            listPlots, sTargetFile)
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        sTimestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
        dictVerification["sLastStandardized"] = sTimestamp
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bSuccess": True,
            "listConverted": listConverted,
            "listStandardizedBasenames": listStdBasenames,
            "sTimestamp": sTimestamp,
        }

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/compare-plot"
    )
    async def fnComparePlot(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        dictCtx["require"]()
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
    async def fnCheckPlotStandards(
        sContainerId: str, iStepIndex: int,
    ):
        dictCtx["require"]()
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
