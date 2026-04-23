"""File management route handlers."""

__all__ = ["fnRegisterAll"]

import os
import posixpath

from fastapi import HTTPException
from fastapi.responses import Response

from ..actionCatalog import fnAgentAction
from ..figureServer import fsMimeTypeForFile
from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    FileUploadRequest,
    FilePullRequest,
    FileWriteRequest,
    WORKSPACE_ROOT,
    flistQueryDirectory,
    fnValidatePathWithinRoot,
    fsResolveFigurePath,
    _fsSanitizeServerError,
)


def _fnValidateHostDestination(sResolvedPath):
    """Raise 403 if the destination escapes the user's home directory."""
    sHome = os.path.expanduser("~")
    if sResolvedPath != sHome and not sResolvedPath.startswith(
            sHome + os.sep):
        raise HTTPException(
            403, "Destination outside home directory")


def _fnDockerCopy(sContainerId, sContainerPath, sHostDest):
    """Run docker cp to copy from container to host."""
    import subprocess
    sSource = f"{sContainerId}:{sContainerPath}"
    subprocess.run(
        ["docker", "cp", sSource, sHostDest],
        check=True, capture_output=True,
    )


def _fnRegisterFiles(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/files route."""

    @app.get("/api/files/{sContainerId}/{sDirectoryPath:path}")
    async def fnListDirectory(
        sContainerId: str, sDirectoryPath: str
    ):
        import asyncio
        dictCtx["require"]()
        sAbsPath = (
            f"/{sDirectoryPath}"
            if not sDirectoryPath.startswith("/")
            else sDirectoryPath
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        return await asyncio.to_thread(
            flistQueryDirectory,
            dictCtx["docker"], sContainerId, sAbsPath,
        )


def _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/upload."""
    import base64

    @fnAgentAction("upload-file")
    @app.post("/api/files/{sContainerId}/upload")
    async def fnUploadFile(
        sContainerId: str, request: FileUploadRequest,
    ):
        import asyncio
        dictCtx["require"]()
        sSafeFilename = posixpath.basename(request.sFilename)
        sDestPath = posixpath.join(
            request.sDestination, sSafeFilename)
        fnValidatePathWithinRoot(sDestPath, sWorkspaceRoot)
        try:
            baContent = base64.b64decode(request.sContentBase64)
            await asyncio.to_thread(
                dictCtx["docker"].fnWriteFile,
                sContainerId, sDestPath, baContent,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        return {"bSuccess": True, "sPath": sDestPath}


def _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/files/{id}/download."""

    @app.get(
        "/api/files/{sContainerId}/download/{sFilePath:path}"
    )
    async def fnDownloadFile(
        sContainerId: str, sFilePath: str
    ):
        import asyncio
        dictCtx["require"]()
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        try:
            baContent = await asyncio.to_thread(
                dictCtx["docker"].fbaFetchFile,
                sContainerId, sAbsPath,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        sFilename = posixpath.basename(sAbsPath)
        sMimeType = fsMimeTypeForFile(sAbsPath)
        return Response(
            content=baContent,
            media_type=sMimeType,
            headers={
                "Content-Disposition":
                    f'attachment; filename="{sFilename}"',
            },
        )


def _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/pull."""

    @fnAgentAction("pull-file")
    @app.post("/api/files/{sContainerId}/pull")
    async def fnPullFile(
        sContainerId: str, request: FilePullRequest,
    ):
        import asyncio
        dictCtx["require"]()
        fnValidatePathWithinRoot(
            request.sContainerPath, sWorkspaceRoot)
        sHostDest = os.path.realpath(
            os.path.expanduser(request.sHostDestination))
        _pipelineServer._fnValidateHostDestination(sHostDest)
        try:
            await asyncio.to_thread(
                _pipelineServer._fnDockerCopy,
                sContainerId,
                request.sContainerPath, sHostDest,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        return {"bSuccess": True, "sHostPath": sHostDest}


def _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot):
    """Register PUT /api/file route for saving edited text files."""

    @fnAgentAction("write-file")
    @app.put("/api/file/{sContainerId}/{sFilePath:path}")
    async def fnWriteFile(
        sContainerId: str, sFilePath: str,
        request: FileWriteRequest, sWorkdir: str = "",
    ):
        dictCtx["require"]()
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        baContent = request.sContent.encode("utf-8")
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sAbsPath, baContent
            )
        except Exception as error:
            raise HTTPException(
                500,
                f"Write failed: "
                f"{_fsSanitizeServerError(str(error))}",
            )
        return {"bSuccess": True, "sPath": sAbsPath}


def fnRegisterAll(app, dictCtx, sWorkspaceRoot):
    """Register all file management routes.

    Registration order matters: specific paths like download/
    and upload must be registered before the catch-all directory
    listing route to prevent incorrect route matching.
    """
    _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFiles(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot)
