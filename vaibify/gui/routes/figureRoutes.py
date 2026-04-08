"""Figure serving route handlers."""

import posixpath

from fastapi import HTTPException
from fastapi.responses import Response

from ..figureServer import fsMimeTypeForFile
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    WORKSPACE_ROOT,
    fbaFetchFigureWithFallback,
    fnValidatePathWithinRoot,
    fsResolveFigurePath,
)


def _flistBuildFigureCheckPaths(
    sAbsPath, sWorkdir, sDir, sFilePath,
):
    """Build list of paths to check for figure existence."""
    listPaths = [sAbsPath]
    if sWorkdir and not sFilePath.startswith("/"):
        if sWorkdir.startswith("/"):
            listPaths.append(
                posixpath.join(sWorkdir, sFilePath))
        else:
            listPaths.append(
                posixpath.join(sDir, sWorkdir, sFilePath))
    return listPaths


def _fnRegisterFigure(app, dictCtx):
    """Register GET and HEAD /api/figure routes."""

    @app.head(
        "/api/figure/{sContainerId}/{sFilePath:path}"
    )
    async def fnCheckFigure(
        sContainerId: str, sFilePath: str,
        sWorkdir: str = "",
    ):
        import asyncio
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        sAbsPath = fsResolveFigurePath(sDir, sFilePath)
        fnValidatePathWithinRoot(sAbsPath, WORKSPACE_ROOT)
        listPaths = _flistBuildFigureCheckPaths(
            sAbsPath, sWorkdir, sDir, sFilePath,
        )
        sTestCmd = " || ".join(
            f"test -f {fsShellQuote(p)}"
            for p in listPaths
        )
        iExitCode, _ = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sTestCmd,
        )
        if iExitCode == 0:
            return Response(status_code=200)
        raise HTTPException(404, "Not found")

    @app.get(
        "/api/figure/{sContainerId}/{sFilePath:path}"
    )
    async def fnServeFigure(
        sContainerId: str, sFilePath: str,
        sWorkdir: str = "",
    ):
        import asyncio
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        sAbsPath = fsResolveFigurePath(sDir, sFilePath)
        fnValidatePathWithinRoot(sAbsPath, WORKSPACE_ROOT)
        baContent = await asyncio.to_thread(
            fbaFetchFigureWithFallback,
            dictCtx["docker"], sContainerId, sAbsPath,
            sDir, sWorkdir, sFilePath,
        )
        return Response(
            content=baContent,
            media_type=fsMimeTypeForFile(sAbsPath),
        )


def fnRegisterAll(app, dictCtx):
    """Register all figure serving routes."""
    _fnRegisterFigure(app, dictCtx)
