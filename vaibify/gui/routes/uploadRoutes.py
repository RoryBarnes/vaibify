"""Streamed upload of one file or folder into the open project.

``PUT /api/upload/{id}/stream`` receives raw bytes, so a file of any size
crosses in bounded memory (the JSON/base64 route in ``fileRoutes`` stays
for the in-container agent). ``GET /api/upload/{id}/verdict`` tells the
panel whether a directory accepts a drop and whether the disks have room,
by the same code the PUT applies, so the panel renders a verdict and never
derives one.

The order inside the PUT is the security contract: the researcher is
authenticated and the request bound to the container's current owner by
the route class (``routeScope.ContainerAwareRoute``), and every name and
path is checked, BEFORE a single body byte is read; the body is then
spooled and hashed; and the request is bound to its owner again at the
commit, so a claim lost during a long spool writes nothing. Only then is
the carrier opened, the journal entry recorded with both hashes and the
spool copied into place. The in-container agent is refused here: it keeps the
repository-confined route it already has.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import errno
import os
import posixpath

from fastapi import HTTPException, Request

from ..actionCatalog import ffnAgentAction
from ...docker.confinedWrite import (
    ContainerWriteExistsError,
    ContainerWriteRefusedError,
)
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    fdictStampDockerIdForJournal,
    fgenericRunWorkerUnderTheDrain,
    fnRejectAgentTokenLane,
    fsHashContainerFileOrEmpty,
)
from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.config.registryManager import fbIsHostProject
from ..routeScope import (
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)
from .. import projectRoots
from .. import uploadStaging
from ..pipelineServer import fdictConfinedWriteKeywords

S_UPLOAD_FOLDER_OPERATION = "upload-folder"
I_STATUS_NO_SPACE = 507


def _fnRequireDeclaredSize(iSizeBytes):
    if iSizeBytes < 0:
        raise HTTPException(400, "iSizeBytes may not be negative.")


async def _fnRequireSpaceForFile(dictCtx, sContainerId, sDestination, iBytes):
    """Refuse upfront, naming the short disk, when the file will not fit.

    The container's free space costs a round trip, so it is read only for
    a file big enough to matter; a batch's total is checked once, before
    its first byte, by the verdict route.
    """
    if iBytes < uploadStaging.I_PER_FILE_SPACE_PROBE_MIN_BYTES:
        return
    sShortage = await asyncio.to_thread(
        uploadStaging.fsDescribeSpaceShortage, dictCtx["docker"],
        sContainerId, sDestination, iBytes, fbIsHostProject(sContainerId),
    )
    if sShortage:
        raise HTTPException(I_STATUS_NO_SPACE, sShortage)


def _fnRaiseForRefusedWrite(error, sTarget):
    """Turn what the confined writer raised into the HTTP answer for it."""
    if isinstance(error, ContainerWriteExistsError):
        raise HTTPException(409, (
            f"{posixpath.basename(sTarget)} already exists here. "
            "Nothing was replaced."))
    if isinstance(error, ContainerWriteRefusedError):
        raise HTTPException(403, str(error))
    if isinstance(error, FileNotFoundError):
        raise HTTPException(404, (
            f"The folder for {posixpath.basename(sTarget)} does not "
            "exist."))
    if isinstance(error, OSError) and error.errno in (
        errno.ENOSPC, errno.EDQUOT,
    ):
        raise HTTPException(I_STATUS_NO_SPACE, (
            "The disk filled up while the file was being placed. The "
            "previous file, if any, is untouched."))


def _fnCopySpoolIntoTarget(
    dictCtx, sContainerId, dictSpool, sTarget, bReplaceAllowed,
    sWritableRoot, bCreateParents,
):
    """Copy the spool to its destination; carry every refusal as an answer."""
    try:
        with open(dictSpool["sPath"], "rb") as fileSource:
            dictCtx["docker"].fnWriteFileFromStream(
                sContainerId, sTarget, fileSource,
                iExpectedBytes=dictSpool["iBytes"],
                bReplaceAllowed=bReplaceAllowed,
                bCreateParents=bCreateParents,
                **fdictConfinedWriteKeywords(sWritableRoot),
            )
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        _fnRaiseForRefusedWrite(error, sTarget)
        raise HTTPException(500, f"Upload failed: {error}")


def _fnRequirePriorUnchanged(dictCtx, sContainerId, sTarget, sPriorSha256):
    """Refuse when a file the upload replaces changed while it queued.

    The prior hash in the journal was read before the carrier opened, and
    the carrier may wait for a run to finish. A file that changed in that
    wait is not the one the record describes, so nothing is replaced.
    """
    if not sPriorSha256:
        return
    if fsHashContainerFileOrEmpty(
        dictCtx, sContainerId, sTarget,
    ) != sPriorSha256:
        raise HTTPException(409, (
            f"{posixpath.basename(sTarget)} changed while the upload "
            "waited its turn. Nothing was replaced; upload it again."))


def _ffnBuildUploadWorker(
    dictCtx, sContainerId, dictSpool, sTarget, bReplaceAllowed,
    sWritableRoot, bCreateParents, sPriorSha256,
):
    def fdictWorker(supervisor=None):
        del supervisor

        def fnEffect():
            _fnRequirePriorUnchanged(
                dictCtx, sContainerId, sTarget, sPriorSha256)
            _fnCopySpoolIntoTarget(
                dictCtx, sContainerId, dictSpool, sTarget, bReplaceAllowed,
                sWritableRoot, bCreateParents)

        # 507 is carried back as an answer, not raised: the writer removed
        # its temporary file and left the old one, so the container's
        # state is KNOWN and quarantining it for "disk full" would take a
        # working container out of service.
        return fdictCarryARefusalBackInsteadOfRaising(
            fnEffect, setAlsoCarriedStatusCodes=frozenset({I_STATUS_NO_SPACE}),
        )
    return fdictWorker


async def _fdictCommitSpooledFile(
    dictCtx, requestHttp, sContainerId, dictSpool, sTarget,
    bReplaceAllowed, sWritableRoot, bCreateParents,
):
    """Open the carrier, journal both hashes, copy the spool into place."""
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The file upload")
    sPriorSha256 = await asyncio.to_thread(
        fsHashContainerFileOrEmpty, dictCtx, sContainerId, sTarget)
    dictIdentity = {
        **fdictStampDockerIdForJournal(sContainerId),
        "sExpectedSha256": dictSpool["hasherContent"].hexdigest(),
        "sPriorSha256": sPriorSha256,
        "iHolderPid": os.getpid(),
        "iHolderProcessGroup": os.getpgrp(),
    }
    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sTarget,
        _ffnBuildUploadWorker(
            dictCtx, sContainerId, dictSpool, sTarget, bReplaceAllowed,
            sWritableRoot, bCreateParents, sPriorSha256),
        dictHolderIdentity=dictIdentity,
    )
    dictCarried = dictOutcome["result"]
    if dictCarried["errorRefused"] is not None:
        raise dictCarried["errorRefused"]


async def _fdictMakeUploadFolder(
    dictCtx, requestHttp, sContainerId, sTarget, sWritableRoot,
):
    """Create one (possibly empty) folder of a dropped tree."""

    def fdictWorker(supervisor=None):
        del supervisor

        def fnEffect():
            try:
                dictCtx["docker"].fnMakeDirectory(
                    sContainerId, sTarget,
                    **fdictConfinedWriteKeywords(sWritableRoot))
            except ContainerWriteRefusedError as error:
                raise HTTPException(403, str(error))
            except Exception as error:
                fnReRaiseControlPlaneRefusal(error)
                raise HTTPException(500, f"Could not create the folder: {error}")
        return fdictCarryARefusalBackInsteadOfRaising(fnEffect)

    await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictWorker, S_UPLOAD_FOLDER_OPERATION, requestHttp)
    return {"bSuccess": True, "sPath": sTarget, "iBytes": 0}


def _fnRegisterUploadStream(app, dictCtx, sWorkspaceRoot):
    """Register PUT /api/upload/{id}/stream."""

    @ffnAgentAction("upload-file-stream")
    @app.put("/api/upload/{sContainerId}/stream")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictUploadStream(
        requestHttp: Request, sContainerId: str, sDestination: str,
        sFilename: str, iSizeBytes: int, sRelativePath: str = "",
        bReplaceAllowed: bool = False, bDirectory: bool = False,
    ):
        dictCtx["require"](sContainerId)
        fnRejectAgentTokenLane(requestHttp)
        _fnRequireDeclaredSize(iSizeBytes)
        sWritableRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, sWorkspaceRoot)
        sTarget = uploadStaging.fsComposeUploadTarget(
            sDestination, sRelativePath, sFilename)
        uploadStaging.fnRequireUploadAllowed(sTarget, sWritableRoot)
        if bDirectory:
            return await _fdictMakeUploadFolder(
                dictCtx, requestHttp, sContainerId, sTarget, sWritableRoot)
        await _fnRequireSpaceForFile(
            dictCtx, sContainerId, sDestination, iSizeBytes)
        dictSpool = await uploadStaging.fdictSpoolBody(
            requestHttp.stream(), iSizeBytes)
        try:
            await _fdictCommitSpooledFile(
                dictCtx, requestHttp, sContainerId, dictSpool, sTarget,
                bReplaceAllowed, sWritableRoot, bool(sRelativePath))
        finally:
            uploadStaging.fnDiscardSpool(dictSpool)
        return {"bSuccess": True, "sPath": sTarget, "iBytes": iSizeBytes}


def _fnRegisterUploadVerdict(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/upload/{id}/verdict."""

    # typed-read: the verdict is lexical, plus at most one declared read of
    # a filesystem's free bytes. It reaches no mutation-capable primitive.
    @app.get("/api/upload/{sContainerId}/verdict")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictDescribeVerdict(
        sContainerId: str, sDirectory: str, iTotalBytes: int = 0,
    ):
        dictCtx["require"](sContainerId)
        sWritableRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, sWorkspaceRoot)
        dictVerdict = uploadStaging.fdictDescribeUploadVerdict(
            sDirectory, sWritableRoot)
        if dictVerdict["bUploadAllowed"] and iTotalBytes > 0:
            sShortage = await asyncio.to_thread(
                uploadStaging.fsDescribeSpaceShortage, dictCtx["docker"],
                sContainerId, sDirectory, iTotalBytes,
                fbIsHostProject(sContainerId))
            if sShortage:
                dictVerdict["bUploadAllowed"] = False
                dictVerdict["sUploadRefusal"] = sShortage
        return dictVerdict


def fnRegisterAll(app, dictCtx, sWorkspaceRoot):
    """Register the streamed upload routes."""
    _fnRegisterUploadStream(app, dictCtx, sWorkspaceRoot)
    _fnRegisterUploadVerdict(app, dictCtx, sWorkspaceRoot)
