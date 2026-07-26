"""Figure serving route handlers."""

__all__ = ["fnRegisterAll"]

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
    """Build the validated list of paths to probe for figure existence.

    ``sWorkdir`` is a query parameter, so the workdir-relative fallback
    is caller-controlled and must be jailed exactly as the GET path
    jails its own fallback in ``_fbaFetchFallback``. Without that, the
    HEAD probe's ``test -f`` answers existence questions about
    arbitrary container paths — an oracle the GET route never granted.
    """
    listPaths = [sAbsPath]
    if sWorkdir and not sFilePath.startswith("/"):
        if sWorkdir.startswith("/"):
            sFallback = posixpath.join(sWorkdir, sFilePath)
        else:
            sFallback = posixpath.join(sDir, sWorkdir, sFilePath)
        listPaths.append(
            fnValidatePathWithinRoot(sFallback, WORKSPACE_ROOT))
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
