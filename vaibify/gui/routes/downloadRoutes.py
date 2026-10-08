"""Download one file, or one folder as a tar, to the researcher's computer.

The bytes are read by the confined readers
(:mod:`vaibify.docker.confinedRead` and its host twin), which walk the path
without following a symlink they were not told to and stream in bounded
memory, so a component an in-container agent swaps after the path check
cannot redirect a download and a file of any size passes through.

A link is followed only when it stays inside the project (a link out of it
is refused naming it), and a folder is archived with its links as links.
``HEAD`` answers whether the same GET would succeed, so the page can toast
the reason a download would fail: an anchor download cannot show an HTTP
error, and the browser would otherwise save an error page as the file.
"""

__all__ = ["fnRegisterAll", "fsBuildContentDisposition"]

import asyncio
import posixpath
from urllib.parse import quote

from fastapi import HTTPException, Response
from fastapi.responses import StreamingResponse

from ...docker.confinedRead import ContainerReadRefusedError
from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.host.hostConfinedRead import HostReadUnsupportedError
from vaibify.host.hostConnection import HostPathOutsideProjectError
from .. import projectRoots
from ..routeScope import S_CARRIER_TYPED_READ, ffnDeclareCarrierMode
from ..pipelineServer import (
    _fsSanitizeServerError,
    fsResolveFigurePath,
    fsValidatePathWithinRoot,
)

S_FILE_MEDIA_TYPE = "application/octet-stream"
S_FOLDER_MEDIA_TYPE = "application/x-tar"


def _fnRaiseHttpForFailedRead(error, sAbsPath):
    """Answer a failed confined read with the status and words that fit it."""
    sName = posixpath.basename(sAbsPath) or sAbsPath
    if isinstance(error, (ContainerReadRefusedError,
                          HostPathOutsideProjectError)):
        raise HTTPException(403, str(error))
    if isinstance(error, HostReadUnsupportedError):
        raise HTTPException(501, str(error))
    if isinstance(error, FileNotFoundError):
        raise HTTPException(404, f"{sName} does not exist.")
    raise HTTPException(
        500, f"{sName} could not be read: "
        f"{_fsSanitizeServerError(str(error))}")


def _ftProbeFirstChunk(
    connectionDocker, sContainerId, sAbsPath, sProjectRoot, bFolder,
):
    """Open the confined read and pull the first chunk eagerly.

    A refusal or a missing path is raised BEFORE the first chunk is
    yielded; pulling one here forces it to surface as an HTTP error
    before the StreamingResponse commits a 200 status, after which the
    client could only see a truncated body.
    """
    fiterOpen = (connectionDocker.fiterReadDirectoryAsTar if bFolder
                 else connectionDocker.fiterReadFileConfined)
    iterChunks = fiterOpen(
        sContainerId, sAbsPath, sAuthorizedRoot=sProjectRoot)
    try:
        baFirst = next(iterChunks)
    except StopIteration:
        baFirst = b""
    return baFirst, iterChunks


async def _ftOpenReadOrRaiseHttp(
    connectionDocker, sContainerId, sAbsPath, sProjectRoot, bFolder,
):
    """Begin the read in a worker thread; map every failure to HTTP."""
    try:
        return await asyncio.to_thread(
            _ftProbeFirstChunk, connectionDocker, sContainerId, sAbsPath,
            sProjectRoot, bFolder)
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        _fnRaiseHttpForFailedRead(error, sAbsPath)


def _fiterReplayThenRest(baFirst, iterChunks):
    """Re-yield ``baFirst`` then drain ``iterChunks`` for StreamingResponse."""
    if baFirst:
        yield baFirst
    yield from iterChunks


def fsBuildContentDisposition(sFilename):
    """Return an attachment header safe for any filename (RFC 6266).

    HTTP header values are Latin-1, so the quoted form carries an ASCII
    fallback with the quote and backslash escaped, and the exact name
    travels percent-encoded in the ``filename*`` parameter.
    """
    sFallback = "".join(
        sCharacter if 32 <= ord(sCharacter) < 127 else "_"
        for sCharacter in sFilename
    ).replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'attachment; filename="{sFallback}"; '
        f"filename*=UTF-8''{quote(sFilename, safe='')}"
    )


def _fdictDownloadHeaders(sAbsPath, bFolder):
    sFilename = posixpath.basename(sAbsPath) + (".tar" if bFolder else "")
    return {"Content-Disposition": fsBuildContentDisposition(sFilename)}


def _fresponseStreamDownload(iterBytes, sAbsPath, bFolder):
    """Wrap a byte iterator as an attachment StreamingResponse."""
    return StreamingResponse(
        iterBytes,
        media_type=S_FOLDER_MEDIA_TYPE if bFolder else S_FILE_MEDIA_TYPE,
        headers=_fdictDownloadHeaders(sAbsPath, bFolder),
    )


def _fsResolveDownloadPath(dictCtx, sContainerId, sFilePath, sProjectRoot):
    sAbsPath = fsResolveFigurePath(
        dictCtx["workflowDir"](sContainerId), sFilePath, sProjectRoot)
    fsValidatePathWithinRoot(sAbsPath, sProjectRoot)
    return sAbsPath


def _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot):
    """Register GET and HEAD /api/files/{id}/download/{path}."""

    async def _ftOpenDownload(sContainerId, sFilePath, bFolder):
        dictCtx["require"](sContainerId)
        sProjectRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, sWorkspaceRoot)
        sAbsPath = _fsResolveDownloadPath(
            dictCtx, sContainerId, sFilePath, sProjectRoot)
        baFirst, iterChunks = await _ftOpenReadOrRaiseHttp(
            dictCtx["docker"], sContainerId, sAbsPath, sProjectRoot, bFolder)
        return sAbsPath, baFirst, iterChunks

    # typed-read: the only container work is the confined READ program,
    # which runs as the container user and reaches no mutation-capable
    # primitive. The GET and HEAD carry the same declaration.
    @app.get("/api/files/{sContainerId}/download/{sFilePath:path}")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fresponseDownloadFile(
        sContainerId: str, sFilePath: str, bFolder: bool = False,
    ):
        sAbsPath, baFirst, iterChunks = await _ftOpenDownload(
            sContainerId, sFilePath, bFolder)
        return _fresponseStreamDownload(
            _fiterReplayThenRest(baFirst, iterChunks), sAbsPath, bFolder)

    @app.head("/api/files/{sContainerId}/download/{sFilePath:path}")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fresponseProbeDownload(
        sContainerId: str, sFilePath: str, bFolder: bool = False,
    ):
        sAbsPath, _, iterChunks = await _ftOpenDownload(
            sContainerId, sFilePath, bFolder)
        await asyncio.to_thread(iterChunks.close)
        return Response(
            headers=_fdictDownloadHeaders(sAbsPath, bFolder),
            media_type=S_FOLDER_MEDIA_TYPE if bFolder else S_FILE_MEDIA_TYPE)


def fnRegisterAll(app, dictCtx, sWorkspaceRoot):
    """Register the download routes.

    Registered BEFORE ``fileRoutes``: its catch-all directory listing
    would otherwise answer ``download/...`` as a directory named
    "download".
    """
    _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot)
