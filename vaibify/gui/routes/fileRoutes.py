"""File management route handlers."""

__all__ = ["fnRegisterAll"]

import hashlib
import os
import posixpath

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List

from ..actionCatalog import fnAgentAction
from ..pipelineUtils import fsShellQuote
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


I_MAX_EXISTENCE_BATCH = 1000


class FileExistenceRequest(BaseModel):
    """Payload for batched file-existence checks."""

    saRelativePaths: List[str]


def _fnRejectWriteDenylistedPath(sNormalized, sProjectRepoPath):
    """Refuse writes to vaibify-managed metadata or the project contract file.

    Writes that target paths under ``.git/`` (git internals at any depth),
    under ``.vaibify/`` (vaibify-managed metadata), or that match the
    basename ``project.json`` (which must only be edited via the dedicated
    project routes) are rejected with HTTP 403.
    """
    sRepo = posixpath.normpath(sProjectRepoPath)
    sRelative = posixpath.relpath(sNormalized, sRepo)
    listSegments = sRelative.split("/")
    if ".git" in listSegments:
        raise HTTPException(403, "Writes under .git/ are not permitted")
    if ".vaibify" in listSegments:
        raise HTTPException(
            403, "Writes under .vaibify/ are not permitted")
    if posixpath.basename(sNormalized) == "project.json":
        raise HTTPException(
            403, "Direct writes to project.json are not permitted")


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


def _fsResolveExistencePath(sRawPath, sProjectRepoPath, sWorkspaceRoot):
    """Return the validated absolute container path for one input entry.

    Inputs may already be absolute container paths (used by callers
    that pre-resolved via ``workflowDir``) or repo-relative paths from
    workflow.json. Both are normalized and validated against the most
    permissive of (project repo, workspace root) so traversal is
    impossible. Raises ``HTTPException`` 403 on escape.
    """
    if sRawPath.startswith("/"):
        sAbs = sRawPath
    else:
        sBase = sProjectRepoPath or sWorkspaceRoot
        sAbs = posixpath.join(sBase, sRawPath)
    return fnValidatePathWithinRoot(sAbs, sWorkspaceRoot)


def _fdictTestExistenceBatch(
    connectionDocker, sContainerId, listAbsPaths,
):
    """Run a single shell loop to test each path; return ``{path: bool}``."""
    if not listAbsPaths:
        return {}
    sJoined = "\n".join(listAbsPaths)
    sScript = (
        "while IFS= read -r p; do "
        "if [ -e \"$p\" ]; then echo \"$p\"; fi; "
        "done <<'__VAIBIFY_EOF__'\n" + sJoined + "\n__VAIBIFY_EOF__"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sScript,
    )
    setExisting = set(
        sLine for sLine in sOutput.splitlines() if sLine
    )
    return {sPath: (sPath in setExisting) for sPath in listAbsPaths}


def _fnRegisterFileExistenceBatch(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/exist for batched existence checks."""

    @fnAgentAction("check-files-exist")
    @app.post("/api/files/{sContainerId}/exist")
    async def fnCheckFilesExist(
        sContainerId: str, request: FileExistenceRequest,
    ):
        import asyncio
        dictCtx["require"]()
        listInput = request.saRelativePaths or []
        if len(listInput) > I_MAX_EXISTENCE_BATCH:
            raise HTTPException(
                400,
                f"Batch capped at {I_MAX_EXISTENCE_BATCH} paths",
            )
        dictWorkflow = dictCtx["workflows"].get(sContainerId) or {}
        sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
        listResolved = [
            _fsResolveExistencePath(
                sRaw, sProjectRepoPath, sWorkspaceRoot,
            )
            for sRaw in listInput
        ]
        dictResolved = await asyncio.to_thread(
            _fdictTestExistenceBatch,
            dictCtx["docker"], sContainerId, listResolved,
        )
        dictExists = {
            sRaw: dictResolved[sResolved]
            for sRaw, sResolved in zip(listInput, listResolved)
        }
        return {"dictExists": dictExists}


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
        sProjectRepoPath = _fsRequireProjectRepoForWrite(
            dictCtx, sContainerId)
        sSafeFilename = posixpath.basename(request.sFilename)
        sDestPath = posixpath.join(
            request.sDestination, sSafeFilename)
        sNormalized = fnValidatePathWithinRoot(
            sDestPath, sProjectRepoPath)
        _fnRejectWriteDenylistedPath(sNormalized, sProjectRepoPath)
        try:
            baContent = base64.b64decode(request.sContentBase64)
            await asyncio.to_thread(
                dictCtx["docker"].fnWriteFile,
                sContainerId, sNormalized, baContent,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        return {"bSuccess": True, "sPath": sNormalized}


def _fnProbeFirstChunk(connectionDocker, sContainerId, sAbsPath):
    """Open the streaming iterator and pull the first chunk eagerly.

    docker-py raises ``NotFound`` / ``APIError`` from
    ``container.get_archive`` synchronously; that error must surface as
    HTTP 500 *before* the StreamingResponse starts writing, otherwise
    FastAPI has already committed the 200 status and the client sees a
    truncated body instead of an error. Pulling one chunk here forces
    the iterator to materialise the get_archive call.
    """
    iterChunks = connectionDocker.fnIterStreamFile(
        sContainerId, sAbsPath,
    )
    try:
        baFirst = next(iterChunks)
    except StopIteration:
        baFirst = b""
    return baFirst, iterChunks


async def _ttIterStreamOrRaiseHttp(
    connectionDocker, sContainerId, sAbsPath,
):
    """Begin streaming the file via a worker thread; map errors to HTTP 500."""
    import asyncio
    try:
        return await asyncio.to_thread(
            _fnProbeFirstChunk,
            connectionDocker, sContainerId, sAbsPath,
        )
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error))


def _fiterReplayThenRest(baFirst, iterChunks):
    """Re-yield ``baFirst`` then drain ``iterChunks`` for StreamingResponse."""
    if baFirst:
        yield baFirst
    yield from iterChunks


def _fresponseStreamDownload(iterBytes, sAbsPath):
    """Wrap a byte iterator as an attachment StreamingResponse."""
    sFilename = posixpath.basename(sAbsPath)
    return StreamingResponse(
        iterBytes,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{sFilename}"',
        },
    )


def _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/files/{id}/download."""

    @app.get(
        "/api/files/{sContainerId}/download/{sFilePath:path}"
    )
    async def fnDownloadFile(
        sContainerId: str, sFilePath: str
    ):
        dictCtx["require"]()
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath,
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        baFirst, iterChunks = await _ttIterStreamOrRaiseHttp(
            dictCtx["docker"], sContainerId, sAbsPath,
        )
        return _fresponseStreamDownload(
            _fiterReplayThenRest(baFirst, iterChunks), sAbsPath,
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


def _fsRequireProjectRepoForWrite(dictCtx, sContainerId):
    """Return the active workflow's project-repo path or raise HTTP 400."""
    dictWorkflow = dictCtx["workflows"].get(sContainerId)
    if not dictWorkflow:
        raise HTTPException(400, "Not connected to container")
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sProjectRepoPath:
        raise HTTPException(
            400, "Active project has no repository path")
    return sProjectRepoPath


def _ftFetchCurrentBytesOrNone(dictCtx, sContainerId, sNormalized):
    """Return ``(baBytes, bAvailable)`` for the on-disk file.

    A missing file returns ``(b"", True)`` so a fresh write does not
    look like a conflict — the contract is "your base reflects what
    is on disk right now," and absence trivially matches the
    empty-base case. A file too large to fetch (above
    ``fbaFetchFile``'s safety cap) returns ``(b"", False)`` so the
    caller skips the conflict check rather than blocking the save.
    """
    try:
        return (
            dictCtx["docker"].fbaFetchFile(sContainerId, sNormalized),
            True,
        )
    except FileNotFoundError:
        return (b"", True)
    except ValueError:
        return (b"", False)


def _fnRaiseConflictIfBaseHashMismatch(
    dictCtx, sContainerId, sNormalized, sBaseHash,
):
    """Raise HTTP 409 when the on-disk file diverged from ``sBaseHash``.

    The client passes the sha256 hex it captured at edit-mode entry.
    If the current disk content's sha256 differs, an external writer
    has changed the file since the editing session started, so saving
    would silently overwrite their work. The response body carries the
    current content so the frontend can render a three-way diff.
    """
    if not sBaseHash:
        return
    baCurrent, bAvailable = _ftFetchCurrentBytesOrNone(
        dictCtx, sContainerId, sNormalized,
    )
    if not bAvailable:
        return
    sCurrentHash = hashlib.sha256(baCurrent).hexdigest()
    if sCurrentHash == sBaseHash:
        return
    try:
        sCurrentContent = baCurrent.decode("utf-8")
    except UnicodeDecodeError:
        sCurrentContent = ""
    raise HTTPException(
        status_code=409,
        detail={
            "sMessage": "File changed on disk since edit started",
            "sCurrentHash": sCurrentHash,
            "sCurrentContent": sCurrentContent,
        },
    )


def _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot):
    """Register PUT /api/file route for saving edited text files."""

    @fnAgentAction("write-file")
    @app.put("/api/file/{sContainerId}/{sFilePath:path}")
    async def fnWriteFile(
        sContainerId: str, sFilePath: str,
        request: FileWriteRequest, sWorkdir: str = "",
    ):
        dictCtx["require"]()
        sProjectRepoPath = _fsRequireProjectRepoForWrite(
            dictCtx, sContainerId)
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath
        )
        sNormalized = fnValidatePathWithinRoot(
            sAbsPath, sProjectRepoPath)
        _fnRejectWriteDenylistedPath(sNormalized, sProjectRepoPath)
        _fnRaiseConflictIfBaseHashMismatch(
            dictCtx, sContainerId, sNormalized, request.sBaseHash,
        )
        baContent = request.sContent.encode("utf-8")
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sNormalized, baContent
            )
        except Exception as error:
            raise HTTPException(
                500,
                f"Write failed: "
                f"{_fsSanitizeServerError(str(error))}",
            )
        return {"bSuccess": True, "sPath": sNormalized}


def fnRegisterAll(app, dictCtx, sWorkspaceRoot):
    """Register all file management routes.

    Registration order matters: specific paths like download/, upload,
    and the batched existence endpoint must be registered before the
    catch-all directory listing route to prevent incorrect matching.
    """
    _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileExistenceBatch(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFiles(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot)
