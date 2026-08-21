"""File management route handlers."""

__all__ = ["fnRegisterAll"]

import hashlib
import os
import posixpath

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List

from ..actionCatalog import ffnAgentAction
from ..pipelineUtils import fsShellQuote
from ..serverMiddleware import fbRequestRidesAgentLane
from ..routeContext import (
    fdictRequireLaneTupleForCommit,
    fdictStampDockerIdForJournal,
    fnRejectAgentTokenLane,
    fsHashContainerFileOrEmpty,
)
from vaibify.config.mutationAdmission import ControlPlaneRefusalError
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_SEPARATE_AUTHORITY,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)
from .. import projectRoots
from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    FileUploadRequest,
    FilePullRequest,
    FileWriteRequest,
    WORKSPACE_ROOT,
    flistQueryDirectory,
    fnRejectWriteDenylistedPath,
    fsValidatePathWithinRoot,
    fsResolveFigurePath,
    _fsSanitizeServerError,
    _fnRefuseWithNoProjectOpen,
)


I_MAX_EXISTENCE_BATCH = 1000


class FileExistenceRequest(BaseModel):
    """Payload for batched file-existence checks."""

    saRelativePaths: List[str]


class WorkspaceSeedRequest(BaseModel):
    """Payload naming which of a project's own files to copy in.

    The paths are relative to the project's REGISTERED host directory,
    never absolute, so the request cannot nominate a location outside
    it; the handler proves containment again after resolving each one.
    """

    saRelativePaths: List[str]


# The write denylist moved to pipelineServer on 2026-07-25 so the test
# routes could share it without a route-to-route import. Both names stay
# bound here for callers and tests that already reference them.
_fnRejectWriteDenylistedPath = fnRejectWriteDenylistedPath


def _fnValidateHostDestination(sResolvedPath):
    """Raise 403 if the destination escapes the user's home directory."""
    sHome = os.path.expanduser("~")
    if sResolvedPath != sHome and not sResolvedPath.startswith(
            sHome + os.sep):
        raise HTTPException(
            403, "Destination outside home directory")


def _fsPullContainerFileToHost(
    connectionDocker, sContainerId, sContainerPath, sHostDestination,
):
    """Stream one container file onto the host; return where it landed.

    This replaces a ``docker cp`` the route module assembled itself.
    ``docker cp`` is bidirectional as a primitive -- the argv decides
    which way the bytes travel -- so a route holding it holds a container
    WRITE, whatever this particular call site happened to do with it. The
    gateway's streaming read cannot travel the other way.

    Two behaviours of ``docker cp`` are reproduced deliberately rather
    than dropped. A destination that is an existing DIRECTORY receives
    the source's basename, and the path returned is where the file
    actually landed rather than what the caller asked for -- the old
    route reported the directory. A DIRECTORY source is REFUSED, because
    the gateway's single-file stream would otherwise skip the directory
    entry, pull the first regular file inside it, and report success.
    """
    _fnRefuseDirectorySource(
        connectionDocker, sContainerId, sContainerPath,
    )
    sTargetPath = sHostDestination
    if os.path.isdir(sTargetPath):
        sTargetPath = os.path.join(
            sTargetPath, posixpath.basename(sContainerPath),
        )
    with open(sTargetPath, "wb") as fileTarget:
        for baChunk in connectionDocker.fiterStreamFile(
            sContainerId, sContainerPath,
        ):
            fileTarget.write(baChunk)
    return sTargetPath


def _fnRefuseDirectorySource(
    connectionDocker, sContainerId, sContainerPath,
):
    """Raise when the pull source names a directory rather than a file.

    Only ``FileNotFoundError`` is read as "not a directory", which is
    what the typed-read adapter raises for a path that is not one. Any
    other failure -- an unreachable daemon, a stopped container -- is
    left to propagate, because reinterpreting it as "this is a file"
    would answer a question the probe did not manage to ask.
    """
    try:
        connectionDocker.flistDirectoryEntries(
            sContainerId, sContainerPath,
        )
    except FileNotFoundError:
        return
    raise IsADirectoryError(
        f"{sContainerPath} is a directory; a pull names one file"
    )


def _fsResolveExistencePath(sRawPath, sProjectRepoPath, sProjectRoot):
    """Return the validated absolute container path for one input entry.

    Inputs may already be absolute container paths (used by callers
    that pre-resolved via ``workflowDir``) or repo-relative paths from
    project.json. Both are normalized and validated against the most
    permissive of (project repo, project root) so traversal is
    impossible. Raises ``HTTPException`` 403 on escape.

    ``sProjectRoot`` is the outer boundary for THIS resource — the
    container volume for a container project, the registered directory
    for a host one — never the app-wide constant it used to be.
    """
    if sRawPath.startswith("/"):
        sAbs = sRawPath
    else:
        sBase = sProjectRepoPath or sProjectRoot
        sAbs = posixpath.join(sBase, sRawPath)
    return fsValidatePathWithinRoot(sAbs, sProjectRoot)


def _fdictTestExistenceBatch(
    connectionDocker, sContainerId, listAbsPaths,
):
    """Probe every path in one typed read; return ``{path: bool}``.

    This was a shell heredoc with the paths interpolated raw between
    ``<<'__VAIBIFY_EOF__'`` and its terminator, which had two problems
    the typed read removes together. A path containing that terminator
    on a line of its own ended the heredoc and made the remainder shell.
    And the whole thing went through the general exec primitive, which
    the mutation gate treats as mutating -- because a primitive handed
    command text cannot know what the text does -- so on the enforced
    branch a file-existence probe would have been refused outright.

    It also lost duplicate and blank paths: the answer was set
    membership over the ECHOED lines, so a path that echoed nothing
    distinguishable read as absent. The typed read answers positionally,
    one boolean per requested path.
    """
    if not listAbsPaths:
        return {}
    listExists = connectionDocker.flistContainerPathsExist(
        sContainerId, listAbsPaths,
    )
    return dict(zip(listAbsPaths, listExists))


def _fnRegisterFileExistenceBatch(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/exist for batched existence checks."""

    # typed-read: after the heredoc was replaced by the batched
    # existence probe, the only container work this route does is that
    # one declared read operation, built by its adapter from the paths
    # it was given. It reaches no mutation-capable primitive, so it
    # needs no carrier -- and unlike a `separate-authority` route it
    # writes nothing anywhere, which is what `typed-read` claims.
    @ffnAgentAction("check-files-exist")
    @app.post("/api/files/{sContainerId}/exist")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictCheckFilesExist(
        sContainerId: str, request: FileExistenceRequest,
    ):
        import asyncio
        dictCtx["require"](sContainerId)
        listInput = request.saRelativePaths or []
        if len(listInput) > I_MAX_EXISTENCE_BATCH:
            raise HTTPException(
                400,
                f"Batch capped at {I_MAX_EXISTENCE_BATCH} paths",
            )
        dictWorkflow = dictCtx["workflows"].get(sContainerId) or {}
        sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
        sProjectRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, sWorkspaceRoot,
        )
        listResolved = [
            _fsResolveExistencePath(
                sRaw, sProjectRepoPath, sProjectRoot,
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
    async def flistListDirectory(
        sContainerId: str, sDirectoryPath: str
    ):
        import asyncio
        dictCtx["require"](sContainerId)
        sAbsPath = (
            f"/{sDirectoryPath}"
            if not sDirectoryPath.startswith("/")
            else sDirectoryPath
        )
        fsValidatePathWithinRoot(
            sAbsPath,
            projectRoots.fsResolveProjectRoot(
                sContainerId, sWorkspaceRoot,
            ),
        )
        try:
            return await asyncio.to_thread(
                flistQueryDirectory,
                dictCtx["docker"], sContainerId, sAbsPath,
            )
        except FileNotFoundError as error:
            # An unlistable directory used to answer with an empty
            # list, which the file panel renders as "Empty directory"
            # -- a claim about the researcher's project made because
            # the read failed. Say it could not be read instead.
            raise HTTPException(
                404, f"Cannot list directory: {sAbsPath}",
            ) from error


def _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/upload."""
    import base64

    @ffnAgentAction("upload-file")
    @app.post("/api/files/{sContainerId}/upload")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictUploadFile(
        sContainerId: str, request: FileUploadRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        sProjectRepoPath = _fsRequireProjectRepoForWrite(
            dictCtx, sContainerId)
        sSafeFilename = posixpath.basename(request.sFilename)
        sDestPath = posixpath.join(
            request.sDestination, sSafeFilename)
        sNormalized = fsValidatePathWithinRoot(
            sDestPath, sProjectRepoPath)
        fnRejectWriteDenylistedPath(sNormalized, sProjectRepoPath)
        # Decoded on the request coroutine, before the carrier, so a
        # malformed body is a 400 rather than a worker failure that
        # poisons a journal record and quarantines the container.
        try:
            baContent = base64.b64decode(request.sContentBase64)
        except Exception as error:
            raise HTTPException(
                400,
                "sContentBase64 is not valid base64: "
                f"{_fsSanitizeServerError(str(error))}",
            )
        _fnCommitUploadedFile(
            dictCtx, sContainerId, sNormalized, baContent, requestHttp,
        )
        return {"bSuccess": True, "sPath": sNormalized}


def _fnCommitUploadedFile(
    dictCtx, sContainerId, sNormalized, baContent, requestHttp,
):
    """Commit an uploaded file through carrier mode (a) (design §8).

    Deliberately NOT folded into :func:`_fnCommitFileWrite`, which the
    editor save uses. The two write the same journal record but differ
    in what else they do: the editor save appends a Supervised-mode
    attribution event and this one does not, and giving the shared
    helper a channel parameter would either start recording an
    attribution event the upload path never recorded (a behaviour
    change smuggled into a migration) or add a flag whose only job is
    to say which caller it is. Two call sites is not the rule of three.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The file upload",
    )
    sPriorSha256 = fsHashContainerFileOrEmpty(
        dictCtx, sContainerId, sNormalized,
    )

    def fnWriteTheUpload():
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sNormalized, baContent,
            )
        except PermissionError:
            # A carrier refusal is the migration's only proof that a
            # mutation was carried; flattening it into a generic 500
            # would hide exactly what this boundary exists to surface.
            raise
        except Exception as error:
            raise HTTPException(500, str(error))

    commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sNormalized,
        fnWriteTheUpload,
        {
            **fdictStampDockerIdForJournal(sContainerId),
            "sExpectedSha256": hashlib.sha256(baContent).hexdigest(),
            "sPriorSha256": sPriorSha256,
        },
    )


def _ftProbeFirstChunk(connectionDocker, sContainerId, sAbsPath):
    """Open the streaming iterator and pull the first chunk eagerly.

    docker-py raises ``NotFound`` / ``APIError`` from
    ``container.get_archive`` synchronously; that error must surface as
    HTTP 500 *before* the StreamingResponse starts writing, otherwise
    FastAPI has already committed the 200 status and the client sees a
    truncated body instead of an error. Pulling one chunk here forces
    the iterator to materialise the get_archive call.
    """
    iterChunks = connectionDocker.fiterStreamFile(
        sContainerId, sAbsPath,
    )
    try:
        baFirst = next(iterChunks)
    except StopIteration:
        baFirst = b""
    return baFirst, iterChunks


async def _ftIterStreamOrRaiseHttp(
    connectionDocker, sContainerId, sAbsPath,
):
    """Begin streaming the file via a worker thread; map errors to HTTP 500."""
    import asyncio
    try:
        return await asyncio.to_thread(
            _ftProbeFirstChunk,
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
    async def fresponseDownloadFile(
        sContainerId: str, sFilePath: str
    ):
        dictCtx["require"](sContainerId)
        sProjectRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, sWorkspaceRoot,
        )
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath,
            sProjectRoot,
        )
        fsValidatePathWithinRoot(sAbsPath, sProjectRoot)
        baFirst, iterChunks = await _ftIterStreamOrRaiseHttp(
            dictCtx["docker"], sContainerId, sAbsPath,
        )
        return _fresponseStreamDownload(
            _fiterReplayThenRest(baFirst, iterChunks), sAbsPath,
        )


S_AGENT_EXPORT_DIRECTORY = os.path.join("~", ".vaibify", "exports")


def _fsPrepareAgentExportRoot(sContainerId):
    """Create and return this container's agent-lane export directory.

    A file pull runs ``docker cp`` on the HOST, and the in-container
    agent authors the bytes it is copying, so an unrestricted
    destination is arbitrary agent-authored content landing anywhere
    under ``$HOME`` — a shell profile, an SSH authorized-keys file, a
    launch agent. Confining the agent lane to one inert, per-container
    export directory keeps the capability (the researcher can still
    collect what the agent produced) without letting it write anywhere
    the host would later execute.
    """
    sBasename = posixpath.basename(sContainerId) or "unknown"
    sRoot = os.path.realpath(os.path.expanduser(
        os.path.join(S_AGENT_EXPORT_DIRECTORY, sBasename),
    ))
    os.makedirs(sRoot, mode=0o700, exist_ok=True)
    return sRoot


def _fnValidateAgentPullDestination(sResolvedPath, sContainerId):
    """Raise 403 when an agent-lane pull lands outside the export root."""
    sRoot = _fsPrepareAgentExportRoot(sContainerId)
    if sResolvedPath == sRoot:
        return
    if not sResolvedPath.startswith(sRoot + os.sep):
        raise HTTPException(
            403,
            "Agent file pulls must land under "
            f"{S_AGENT_EXPORT_DIRECTORY}/<container>/",
        )


def _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/pull."""

    @ffnAgentAction("pull-file")
    # separate-authority, not typed-read. Nothing this route does to
    # the CONTAINER is a mutation -- the stream is a read and the
    # directory probe is a typed read -- so `typed-read` would be
    # literally true of it and would still be the wrong record, because
    # any reader would take it to mean the route writes nothing. It
    # writes to the researcher's own machine. What governs it is
    # therefore not the commit carrier but the host-side authorities:
    # ``fsValidatePathWithinRoot`` on the container side,
    # ``_fnValidateHostDestination`` on the host side, and for the agent
    # lane the narrower export root ``_fnValidateAgentPullDestination``
    # enforces. Ruling 2026-08-05.
    @app.post("/api/files/{sContainerId}/pull")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictHandlePullFile(
        requestHttp: Request,
        sContainerId: str, request: FilePullRequest,
    ):
        import asyncio
        dictCtx["require"](sContainerId)
        fsValidatePathWithinRoot(
            request.sContainerPath,
            projectRoots.fsResolveProjectRoot(
                sContainerId, sWorkspaceRoot,
            ),
        )
        sHostDest = os.path.realpath(
            os.path.expanduser(request.sHostDestination))
        _pipelineServer._fnValidateHostDestination(sHostDest)
        if fbRequestRidesAgentLane(requestHttp):
            _fnValidateAgentPullDestination(sHostDest, sContainerId)
        try:
            sLandedPath = await asyncio.to_thread(
                _pipelineServer._fsPullContainerFileToHost,
                dictCtx["docker"], sContainerId,
                request.sContainerPath, sHostDest,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        return {"bSuccess": True, "sHostPath": sLandedPath}


def _fnRegisterWorkspaceSeed(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/seed-workspace.

    Carries selected content from the researcher's own directory into a
    freshly converted container's workspace volume. Until this existed
    there was NO path by which host content reached a container: the
    volume is populated only by the entrypoint's git clones, so
    converting a local directory produced an empty workspace and said
    nothing about it (2026-08-21).

    Not agent-safe, and refused at the handler as well as in the
    catalog: the paths name the researcher's OWN filesystem, and the
    catalog cannot express "reads host state" on its own.
    """

    @ffnAgentAction("seed-workspace")
    @app.post("/api/files/{sContainerId}/seed-workspace")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictSeedWorkspace(
        sContainerId: str, request: WorkspaceSeedRequest,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        dictLaneTuple = fdictRequireLaneTupleForCommit(
            requestHttp, sContainerId, "The workspace seed",
        )
        sHostDirectory = _fsRequireHostDirectoryForSeed(
            dictLaneTuple["sContainerName"],
        )
        listHostPaths = _flistResolveSeedPaths(
            sHostDirectory,
            _flistAppendAlwaysSeededEntries(
                sHostDirectory, request.saRelativePaths,
            ),
        )
        sDestination = posixpath.join(
            sWorkspaceRoot, os.path.basename(sHostDirectory),
        )
        _fnCommitWorkspaceSeed(
            dictCtx, sContainerId, sDestination, listHostPaths,
            dictLaneTuple, requestHttp,
        )
        return {
            "bSuccess": True, "sDestination": sDestination,
            "iCopiedCount": len(listHostPaths),
        }


def _fsRequireHostDirectoryForSeed(sContainerName):
    """Return the registered host directory, refusing a host project."""
    from vaibify.config.registryManager import (
        fbIsHostProject, fdictGetProject,
    )
    dictProject = fdictGetProject(sContainerName)
    if not dictProject:
        raise HTTPException(
            404, f"'{sContainerName}' is not a registered project.")
    if fbIsHostProject(sContainerName):
        raise HTTPException(409, (
            f"'{sContainerName}' runs on this machine, so its files "
            "already live where the project runs; there is no "
            "container workspace to copy them into."
        ))
    sDirectory = dictProject.get("sDirectory", "")
    if not sDirectory or not os.path.isdir(sDirectory):
        raise HTTPException(404, (
            f"The directory registered for '{sContainerName}' no "
            "longer exists, so there is nothing to copy."
        ))
    return os.path.realpath(sDirectory)


# Infrastructure that always crosses with the project, whatever the
# researcher ticked. ".git" because a vaibify workflow must live inside
# a git repository, so a container whose copy is not one cannot run a
# pipeline; ".vaibify" because the Project file is written into it
# during the conversion itself -- AFTER the researcher chose from a
# list that therefore could not have offered it.
_T_ALWAYS_SEEDED_ENTRIES = (".git", ".vaibify")


def _flistAppendAlwaysSeededEntries(sHostDirectory, listRelativePaths):
    """Return the selection plus any infrastructure it did not name."""
    listComplete = list(listRelativePaths)
    for sEntry in _T_ALWAYS_SEEDED_ENTRIES:
        if sEntry in listComplete:
            continue
        if os.path.exists(os.path.join(sHostDirectory, sEntry)):
            listComplete.append(sEntry)
    return listComplete


def _flistResolveSeedPaths(sHostDirectory, saRelativePaths):
    """Return absolute host paths, each proven inside the project.

    HOST paths, so ``os.path`` throughout -- the container-path helper
    beside it is ``posixpath`` and would mis-validate on any host whose
    separator is not "/". Each entry is resolved and required to sit
    under the project directory, so neither ``..`` nor an absolute
    path nor a symlink pointing out of the tree can nominate a file
    the researcher never offered.
    """
    if not saRelativePaths:
        raise HTTPException(400, "No files were selected to copy.")
    listResolved = []
    for sRelativePath in saRelativePaths:
        sCandidate = os.path.realpath(
            os.path.join(sHostDirectory, sRelativePath),
        )
        if sCandidate != sHostDirectory and not sCandidate.startswith(
            sHostDirectory + os.sep,
        ):
            raise HTTPException(
                403, f"'{sRelativePath}' is outside the project.")
        if not os.path.exists(sCandidate):
            raise HTTPException(
                404, f"'{sRelativePath}' no longer exists.")
        listResolved.append(sCandidate)
    return listResolved


def _fnCommitWorkspaceSeed(
    dictCtx, sContainerId, sDestination, listHostPaths,
    dictLaneTuple, requestHttp,
):
    """Commit the tree copy through carrier mode (a) (design §8)."""
    from .. import commitCarrier

    def fnSeedTheWorkspace():
        try:
            dictCtx["docker"].ftResultExecuteCommand(
                sContainerId, f"mkdir -p {fsShellQuote(sDestination)}",
            )
            dictCtx["docker"].fnWriteTreeViaTar(
                sContainerId, sDestination, listHostPaths,
            )
        except ControlPlaneRefusalError:
            raise
        except Exception as error:
            raise HTTPException(500, str(error))

    # Journalled as a file-write, the kind it actually is, rather than
    # a "workspace-seed" kind of its own: the journal's allowlist is
    # the set of kinds `vaibify reconcile` knows how to settle, so a
    # new kind is a promise the reconciler has to keep. The seed writes
    # files into the workspace and settles exactly like the upload
    # route beside it.
    commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sDestination,
        fnSeedTheWorkspace,
        fdictStampDockerIdForJournal(sContainerId),
    )


def _fsRequireProjectRepoForWrite(dictCtx, sContainerId):
    """Return the active workflow's project-repo path or raise HTTP 400."""
    dictWorkflow = dictCtx["workflows"].get(sContainerId)
    if not dictWorkflow:
        # Same state as fdictRequireWorkflow's, so the same sentence,
        # the same refusal key AND the same status. These two answered
        # 400 for the condition the read paths answer 404 for, which
        # is one state with two answers -- exactly the drift a shared
        # refusal exists to end.
        _fnRefuseWithNoProjectOpen(sContainerId)
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

    @ffnAgentAction("write-file")
    @app.put("/api/file/{sContainerId}/{sFilePath:path}")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictWriteFile(
        sContainerId: str, sFilePath: str,
        request: FileWriteRequest, requestHttp: Request,
        sWorkdir: str = "",
    ):
        dictCtx["require"](sContainerId)
        sProjectRepoPath = _fsRequireProjectRepoForWrite(
            dictCtx, sContainerId)
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath,
            sProjectRepoPath,
        )
        sNormalized = fsValidatePathWithinRoot(
            sAbsPath, sProjectRepoPath)
        fnRejectWriteDenylistedPath(sNormalized, sProjectRepoPath)
        _fnRaiseConflictIfBaseHashMismatch(
            dictCtx, sContainerId, sNormalized, request.sBaseHash,
        )
        _fnCommitFileWrite(
            dictCtx, sContainerId, sNormalized,
            request.sContent.encode("utf-8"), requestHttp,
        )
        return {"bSuccess": True, "sPath": sNormalized}


def _fnCommitFileWrite(
    dictCtx, sContainerId, sNormalized, baContent, requestHttp,
):
    """Commit the editor's file save through carrier mode (a) (design §8).

    The Supervised-mode attribution append runs INSIDE the same effect
    because it is itself a container write: outside the carrier's
    admission the boundary refuses it, and the recorder swallows its
    own failures by contract, so supervision would go quietly blind
    rather than loudly wrong. The journal record's postcondition names
    the saved file; the audit append rides along best-effort exactly as
    it does today.
    """
    from .. import commitCarrier
    from ..routeContext import fnRecordAttributionEvent
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The file save",
    )
    sPriorSha256 = fsHashContainerFileOrEmpty(
        dictCtx, sContainerId, sNormalized,
    )

    def fnWriteTheFile():
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sNormalized, baContent
            )
        except PermissionError:
            # A carrier refusal is the migration's only proof that a
            # mutation was carried; flattening it into a generic 500
            # would hide exactly what this boundary exists to surface.
            raise
        except Exception as error:
            raise HTTPException(
                500,
                f"Write failed: "
                f"{_fsSanitizeServerError(str(error))}",
            )
        fnRecordAttributionEvent(
            dictCtx, sContainerId,
            dictCtx["workflows"].get(sContainerId) or {},
            "write-file", sNormalized,
        )

    commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sNormalized,
        fnWriteTheFile,
        {
            **fdictStampDockerIdForJournal(sContainerId),
            "sExpectedSha256": hashlib.sha256(baContent).hexdigest(),
            "sPriorSha256": sPriorSha256,
        },
    )


def fnRegisterAll(app, dictCtx, sWorkspaceRoot):
    """Register all file management routes.

    Registration order matters: specific paths like download/, upload,
    and the batched existence endpoint must be registered before the
    catch-all directory listing route to prevent incorrect matching.
    """
    _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterWorkspaceSeed(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileExistenceBatch(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFiles(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot)
