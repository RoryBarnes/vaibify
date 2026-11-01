"""Dispatch sync operations to run inside Docker containers."""

import json
import posixpath
import re
import subprocess
import uuid

from vaibify.reproducibility import zenodoClient
from vaibify.docker.execLane import (
    S_EXEC_LANE_INTEGRITY_READ, S_HUB_PYTHON_COMMAND,
)
from vaibify.reproducibility.gitHardening import LIST_GIT_HARDENING_CONFIG
from vaibify.reproducibility.manifestPaths import (
    flistStepScriptRepoPaths,
    flistStepStandardsRepoPaths,
)
from vaibify.reproducibility.manifestWriter import (
    fbWorkflowArchivesTests,
    flistStepTestFileRepoPaths,
)
from vaibify.reproducibility.overleafAuth import (
    fnValidateOverleafProjectId,
    fsWriteAskpassScript,
)

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.config.registryManager import fbIsHostProject

from . import stateContract
from . import workflowManager
from .helperPrograms import fsResolveHelperInterpreter
from .pipelineUtils import fsShellQuote

__all__ = [
    "fbValidateOverleafCredentials",
    "fbValidateZenodoToken",
    "fsDescribeZenodoValidationOutcome",
    "ftResultValidateZenodoToken",
    "fsZenodoInstanceToService",
    "fsZenodoTokenNameForInstance",
    "SET_VALID_ZENODO_INSTANCES",
    "fdictCheckConnectivity",
    "fdictClassifyError",
    "fdictDiffOverleafPush",
    "fdictParseTestMarkerOutput",
    "fdictSyncResult",
    "flistCheckOverleafConflicts",
    "flistCollectOutputFiles",
    "flistCollectZenodoArchiveCandidates",
    "flistDetectOverleafCaseCollisions",
    "flistExtractAllScriptPaths",
    "flistGetDirtyFiles",
    "flistListOverleafTree",
    "fnDeleteCredentialFromContainer",
    "fnValidateOverleafProjectId",
    "fnValidateServiceName",
    "fsBuildDagDot",
    "fsBuildTestMarkerCheckCommand",
    "fsPythonCommand",
    "fsWriteAskpassScript",
    "ftRefreshOverleafMirror",
    "ftResultAddFileToGithub",
    "ftResultArchiveProject",
    "ftResultArchiveToZenodo",
    "DICT_DAG_MEDIA_TYPES",
    "ftResultExportDag",
    "ftResultGenerateDagSvg",
    "ftResultGenerateLatex",
    "ftResultPullFromOverleaf",
    "ftResultPushStagedToGithub",
    "ftResultPushToGithub",
    "ftResultPushToOverleaf",
    "S_PENDING_RECEIPTS_DIRECTORY",
    "T_RECEIPT_WRITE_FORBIDDEN_NAMES",
    "fnWritePendingReceipt",
    "fsCommitPendingReceiptWrite",
    "fsCommitProofReceiptWrite",
    "fnWriteProofReceiptIntoProject",
    "fsPendingReceiptRepoPath",
    "fbaReadPendingReceiptOrNone",
    "fdictCompletionJournalHooks",
    "fdictSettlePublishedCompletion",
    "fnSettleCompletion",
    "fnSettleCompletionUnderTheDrain",
    "fnSweepOrphanPendingReceipts",
    "fsResolveSidecarKey",
    "ftPublishCompletionWithClient",
    "ftResultFinishCompletionDraft",
    "ftResultPublishCompletion",
]

SET_VALID_SERVICES = {"github", "overleaf", "zenodo"}
SET_VALID_TOKEN_NAMES = {
    "overleaf_token",
    "zenodo_token",
    "zenodo_token_sandbox",
    "zenodo_token_production",
    "gh_token",
    # Stage-validate-commit snapshot slots: the connect flow copies a
    # working token aside before a new one overwrites it, so a failed
    # validation can restore instead of destroying it.
    "zenodo_token_sandbox_backup",
    "zenodo_token_production_backup",
}
SET_VALID_ZENODO_INSTANCES = {"sandbox", "production"}
_DICT_ZENODO_INSTANCE_TO_SERVICE = {
    "sandbox": "sandbox",
    "production": "zenodo",
}
def fsZenodoInstanceToService(sZenodoInstance):
    """Map a UI instance name to the ZenodoClient service key."""
    if sZenodoInstance not in SET_VALID_ZENODO_INSTANCES:
        raise ValueError(
            f"Invalid Zenodo instance: {sZenodoInstance}"
        )
    return _DICT_ZENODO_INSTANCE_TO_SERVICE[sZenodoInstance]


def fsZenodoTokenNameForInstance(sZenodoInstance):
    """Return the keyring slot name for a Zenodo UI instance."""
    if sZenodoInstance not in SET_VALID_ZENODO_INSTANCES:
        raise ValueError(
            f"Invalid Zenodo instance: {sZenodoInstance}"
        )
    return f"zenodo_token_{sZenodoInstance}"


_LIST_AUTH_PATTERNS = [
    "authentication", "401", "403", "forbidden",
    "invalid credentials", "bad credentials",
    "keyring", "no token", "missing credentials",
    "nokeyringerror",
]
_LIST_RATE_LIMIT_PATTERNS = ["rate limit", "429", "too many requests"]
_LIST_NOT_FOUND_PATTERNS = ["not found", "404", "no such"]
_LIST_NETWORK_PATTERNS = [
    "timeout", "connection refused", "network",
    "could not resolve", "no route",
    "name resolution", "dns failure", "dns lookup",
    "unreachable",
]
_LIST_CONFLICT_PATTERNS = [
    "non-fast-forward", "fetch first",
    "updates were rejected",
    "merge conflict",
    "this action is not allowed",
    "published deposition",
    "[rejected]",
]
_LIST_AUTHOR_IDENTITY_PATTERNS = [
    "author identity unknown",
    "please tell me who you are",
    "unable to auto-detect email",
    "empty ident name",
]


def fdictClassifyError(iExitCode, sOutput):
    """Classify a sync command failure by scanning output text.

    The local-file marker is checked FIRST, because its payload is a
    repr'd FileNotFoundError whose text ("No such file or directory")
    also matches the remote not-found patterns below — the ordering is
    what keeps a missing local file from being reported as a failed
    remote lookup.
    """
    sLower = sOutput.lower()
    if "local-file-error" in sLower:
        return {
            "sErrorType": "localFileMissing", "sMessage": sOutput,
        }
    for sPattern in _LIST_AUTHOR_IDENTITY_PATTERNS:
        if sPattern in sLower:
            return {
                "sErrorType": "authorIdentity", "sMessage": sOutput,
            }
    for sPattern in _LIST_AUTH_PATTERNS:
        if sPattern in sLower:
            return {"sErrorType": "auth", "sMessage": sOutput}
    for sPattern in _LIST_RATE_LIMIT_PATTERNS:
        if sPattern in sLower:
            return {"sErrorType": "rateLimit", "sMessage": sOutput}
    for sPattern in _LIST_NOT_FOUND_PATTERNS:
        if sPattern in sLower:
            return {"sErrorType": "notFound", "sMessage": sOutput}
    for sPattern in _LIST_NETWORK_PATTERNS:
        if sPattern in sLower:
            return {"sErrorType": "network", "sMessage": sOutput}
    for sPattern in _LIST_CONFLICT_PATTERNS:
        if sPattern in sLower:
            return {"sErrorType": "conflict", "sMessage": sOutput}
    return {"sErrorType": "unknown", "sMessage": sOutput}


def fdictSyncResult(iExitCode, sOutput):
    """Build a structured sync result from an exit code and output."""
    if iExitCode == 0:
        return {"bSuccess": True, "sOutput": sOutput.strip()}
    dictError = fdictClassifyError(iExitCode, sOutput)
    dictError["bSuccess"] = False
    return dictError


def fnValidateServiceName(sService):
    """Raise ValueError if sService is not a known service."""
    if sService not in SET_VALID_SERVICES:
        raise ValueError(f"Invalid service: {sService}")


def fsPythonCommand(sImportLine, sFunctionCall, sResourceId):
    """Build a ``-c`` command string from import and call.

    Uses ``fsShellQuote`` so embedded double quotes, semicolons, or
    other shell metacharacters in ``sFunctionCall`` cannot break out
    of the argument and be interpreted by the shell. Mirrors the
    pattern in ``containerGit.fdictComputeBlobShasInContainer`` and
    ``conftestManager._fsBuildFlatMarkerMigrationCommand``. When
    ``sImportLine`` is empty the leading ``; `` separator is omitted
    so the resulting Python source is syntactically valid.

    Every program built here imports ``vaibify``, ``keyring`` or
    ``requests``, so it runs on the interpreter that HAS them:
    vaibify's own interpreter on the host (where ``python3`` is
    whatever the researcher's PATH resolves and routinely has none of
    them), and the image's ``python3`` in a container — where
    ``keyring`` and ``requests`` are installed EXPLICITLY by the
    Dockerfile. That explicitness is load-bearing: for months this
    sentence claimed the image had ``requests`` while only projects
    whose own requirements pulled it in could validate a Zenodo
    token, and the failure surfaced as "token not accepted".
    """
    if sImportLine:
        sScript = sImportLine + "; " + sFunctionCall
    else:
        sScript = sFunctionCall
    return (
        fsResolveHelperInterpreter(sResourceId) + " -c "
        + fsShellQuote(sScript)
    )


def ftResultPushToOverleaf(
    connectionDocker, sContainerId,
    listFilePaths, sProjectId, sTargetDirectory,
    dictWorkflow=None, sGithubBaseUrl="", sDoi="",
    sTexFilename="main.tex", sMirrorSha="",
):
    """Push figures to Overleaf from the HOST; return ``(iExitCode, sOutput)``.

    The figures leave the container through the confined reader into a
    private host scratch directory and are committed and pushed from
    there by :mod:`vaibify.reproducibility.overleafSync` in this
    process, with the host keyring's token; no container program runs
    and the token never enters the container. When ``sMirrorSha`` is
    provided the commit message on Overleaf records which mirror
    snapshot the push was built on. The output carries the
    ``PUSH_STATUS=`` and ``HEAD_SHA=`` lines the command line prints,
    so :func:`fsParseHeadShaFromOutput` and
    :func:`fsParsePushStatusFromOutput` read it unchanged.
    """
    from vaibify.reproducibility import overleafSync
    fnValidateOverleafProjectId(sProjectId)
    sToken = _fsFetchOverleafToken()
    if not sToken:
        return (overleafSync.fiExitCodeForError(overleafSync.OverleafAuthError()),
                S_NO_OVERLEAF_TOKEN_MESSAGE)
    sScratchDirectory = _fsCreateHostScratchDirectory()
    try:
        listLocalPaths = _flistStageFilesOnHost(
            connectionDocker, sContainerId, listFilePaths, sScratchDirectory,
        )
        tOutcome = _ftPushStagedFigures(
            overleafSync, listLocalPaths, sProjectId, sTargetDirectory, sToken,
            dictWorkflow, sGithubBaseUrl, sDoi, sTexFilename, sMirrorSha,
        )
    except overleafSync.OverleafError as error:
        return (overleafSync.fiExitCodeForError(error), str(error))
    except (OSError, ValueError) as error:
        return (1, "LOCAL-FILE-ERROR (not an Overleaf problem): " + repr(error))
    finally:
        _fnRemoveScratchDirectory(sScratchDirectory)
    return (0, overleafSync.fsFormatPushOutput(*tOutcome))


S_NO_OVERLEAF_TOKEN_MESSAGE = (
    "No Overleaf token is stored on this host; connect Overleaf from the "
    "Repos panel (authentication required)."
)


def _ftPushStagedFigures(
    overleafSync, listLocalPaths, sProjectId, sTargetDirectory, sToken,
    dictWorkflow, sGithubBaseUrl, sDoi, sTexFilename, sMirrorSha,
):
    """Push the staged copies, annotating the TeX when a GitHub base is known."""
    if dictWorkflow and sGithubBaseUrl:
        return overleafSync.ftPushAnnotatedToOverleaf(
            listLocalPaths, sProjectId, sTargetDirectory, dictWorkflow,
            sGithubBaseUrl, sDoi, sToken, sTexFilename, sMirrorSha,
        )
    return overleafSync.ftPushFiguresToOverleaf(
        listLocalPaths, sProjectId, sTargetDirectory, sToken, sMirrorSha,
    )


def fsParseHeadShaFromOutput(sOutput):
    """Extract a ``HEAD_SHA=<hex>`` line from CLI stdout, or empty."""
    for sLine in (sOutput or "").splitlines():
        sStripped = sLine.strip()
        if sStripped.startswith("HEAD_SHA="):
            return sStripped.split("=", 1)[1].strip()
    return ""


def fsParsePushStatusFromOutput(sOutput):
    """Extract a ``PUSH_STATUS=<value>`` line from CLI stdout, or empty."""
    for sLine in (sOutput or "").splitlines():
        sStripped = sLine.strip()
        if sStripped.startswith("PUSH_STATUS="):
            return sStripped.split("=", 1)[1].strip()
    return ""


def _fsFetchOverleafToken():
    """Return the host keyring's Overleaf token, or '' when none is stored."""
    from vaibify.config.secretManager import fsRetrieveSecret
    try:
        return fsRetrieveSecret("overleaf_token", "keyring") or ""
    except KeyError:
        return ""


def ftResultPullFromOverleaf(
    connectionDocker, sContainerId,
    sProjectId, listPullPaths, sTargetDirectory, sProjectRepoPath="",
):
    """Pull TeX sources from Overleaf on the HOST and land them in the project.

    The clone runs on the host with the host keyring's token, into a
    private scratch directory; the files then enter the container
    through the confined tree write, as the container user, confined
    to ``sTargetDirectory`` with the write deny list, or land in a host
    project through the host confined writer. ``sProjectRepoPath`` is
    the root the target directory is created below (the target's
    parent when not given). No container program runs and the token
    never enters the container.
    """
    from vaibify.reproducibility import overleafSync
    fnValidateOverleafProjectId(sProjectId)
    sToken = _fsFetchOverleafToken()
    if not sToken:
        return (overleafSync.fiExitCodeForError(overleafSync.OverleafAuthError()),
                S_NO_OVERLEAF_TOKEN_MESSAGE)
    sScratchDirectory = _fsCreateHostScratchDirectory()
    try:
        overleafSync.fnPullTexFromOverleaf(
            sProjectId, listPullPaths, sScratchDirectory, sToken,
        )
        _fnLandPulledSources(
            connectionDocker, sContainerId, sScratchDirectory, sTargetDirectory,
            sProjectRepoPath or posixpath.dirname(sTargetDirectory),
        )
    except overleafSync.OverleafError as error:
        return (overleafSync.fiExitCodeForError(error), str(error))
    except OSError as error:
        return (1, "LOCAL-FILE-ERROR (not an Overleaf problem): " + repr(error))
    finally:
        _fnRemoveScratchDirectory(sScratchDirectory)
    return (0, "ok\n")


def _fnLandPulledSources(
    connectionDocker, sContainerId, sScratchDirectory, sTargetDirectory,
    sCreationRoot,
):
    """Write the pulled files into the project's manuscript directory.

    A container project receives them through the confined tree write:
    the target is created one component at a time below the project
    (``.git`` refused by name; ``.vaibify`` is where the manuscript copy
    lives), then the files land rooted at the target so a planted link
    cannot redirect them and the deny list still refuses vaibify's own
    metadata names below it. A host project receives them through the
    host confined writer.
    """
    import os
    listLocalPaths = sorted(
        os.path.join(sScratchDirectory, sName)
        for sName in os.listdir(sScratchDirectory)
    )
    if fbIsHostProject(sContainerId):
        _fnLandPulledSourcesOnHost(listLocalPaths, sTargetDirectory)
        return
    from .pipelineServer import T_WRITE_DENYLISTED_NAMES
    connectionDocker.fnMakeDirectory(
        sContainerId, sTargetDirectory, sAuthorizedRoot=sCreationRoot,
        tForbiddenNames=(".git",),
    )
    connectionDocker.fnWriteTreeViaTar(
        sContainerId, sTargetDirectory, listLocalPaths,
        sAuthorizedRoot=sTargetDirectory,
        tForbiddenNames=T_WRITE_DENYLISTED_NAMES, bCreateDestination=False,
    )


def _fnLandPulledSourcesOnHost(listLocalPaths, sTargetDirectory):
    import os
    from vaibify.host.hostConfinedWrite import fsWriteFileInsideRoot
    os.makedirs(sTargetDirectory, exist_ok=True)
    sRealRoot = os.path.realpath(sTargetDirectory)
    for sLocalPath in listLocalPaths:
        with open(sLocalPath, "rb") as fileLocal:
            fsWriteFileInsideRoot(
                sRealRoot,
                os.path.join(sRealRoot, os.path.basename(sLocalPath)),
                iter(lambda: fileLocal.read(65536), b""),
            )


# ── The narrow backend write: two paths vaibify chooses, never a caller ──
#
# The caller-supplied confined write refuses ``.vaibify`` by name and
# must go on refusing it: that denylist is the metadata-integrity
# contract no request may cross. The PROOF receipt lives in
# ``.vaibify``, so it is written here instead, through the same
# symlink-safe primitives with a narrower forbidden list -- the
# Overleaf manuscript pull's precedent -- and only to two targets
# composed from constants. Neither public function takes a path.

S_PENDING_RECEIPTS_DIRECTORY = ".vaibify/pendingReceipts"
T_RECEIPT_WRITE_FORBIDDEN_NAMES = (".git", "project.json")
_RE_PROMOTION_ID = re.compile(r"promotion-[0-9a-f]{32}")


def fsPendingReceiptRepoPath(sPromotionId):
    """Return a completion's pending receipt path; refuse a malformed id first.

    The id is checked BEFORE any path is composed, so an id carrying a
    separator or ``..`` never becomes part of a path at all.
    """
    if not isinstance(sPromotionId, str) or not _RE_PROMOTION_ID.fullmatch(
            sPromotionId):
        raise ValueError(
            "refused: not a promotion id: " + repr(sPromotionId)[:80])
    return posixpath.join(S_PENDING_RECEIPTS_DIRECTORY, sPromotionId + ".json")


def fnWriteProofReceiptIntoProject(
    connectionDocker, sContainerId, sProjectRepoPath, baReceipt,
):
    """Write the repository copy of the PROOF receipt, symlink-safe."""
    from vaibify.reproducibility.publicationScope import (
        S_PROOF_RECEIPT_REPO_PATH,
    )
    _fnWriteReceiptTarget(
        connectionDocker, sContainerId, sProjectRepoPath,
        S_PROOF_RECEIPT_REPO_PATH, baReceipt,
    )


def fnWritePendingReceipt(
    connectionDocker, sContainerId, sProjectRepoPath, sPromotionId,
    baReceipt,
):
    """Store a completion receipt's bytes, symlink-safe, before any draft."""
    _fnWriteReceiptTarget(
        connectionDocker, sContainerId, sProjectRepoPath,
        fsPendingReceiptRepoPath(sPromotionId), baReceipt,
    )


def fsCommitProofReceiptWrite(
    dictCtx, sContainerId, dictWorkflow, requestHttp, baReceipt,
):
    """Write the repository receipt under carrier mode (a); return a warning.

    ``""`` when it landed. A refused or failed write is carried back as
    the warning rather than raised: the confined write is atomic, so the
    old receipt is untouched and nothing is left to reconcile, and a
    raise would quarantine a container over a file the next published
    archive step rewrites anyway.
    """
    from vaibify.reproducibility.publicationScope import (
        S_PROOF_RECEIPT_REPO_PATH,
    )
    return _fsCommitBackendFileWrite(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "proof-receipt", S_PROOF_RECEIPT_REPO_PATH, baReceipt,
        lambda sRepo: fnWriteProofReceiptIntoProject(
            dictCtx["docker"], sContainerId, sRepo, baReceipt,
        ),
    )


def fsCommitPendingReceiptWrite(
    dictCtx, sContainerId, dictWorkflow, requestHttp, sPromotionId,
    baReceipt,
):
    """Store a completion's receipt under carrier mode (a); return an error."""
    return _fsCommitBackendFileWrite(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "pending-proof-receipt", fsPendingReceiptRepoPath(sPromotionId),
        baReceipt,
        lambda sRepo: fnWritePendingReceipt(
            dictCtx["docker"], sContainerId, sRepo, sPromotionId, baReceipt,
        ),
    )


def _fsCommitBackendFileWrite(
    dictCtx, sContainerId, dictWorkflow, requestHttp, sOperationName,
    sRepoRelPath, baContent, fnWriteInto,
):
    """Journal one backend-chosen write as a ``file-write`` record."""
    import hashlib
    from vaibify.docker.confinedWrite import ContainerWriteRefusedError
    from . import commitCarrier, routeContext
    sRepo = (dictWorkflow or {}).get("sProjectRepoPath") or ""
    sTarget = posixpath.join(sRepo, sRepoRelPath)

    def fdictWrite():
        try:
            fnWriteInto(sRepo)
        except (ContainerWriteRefusedError, OSError, ValueError) as error:
            return {"sError": str(error) or type(error).__name__}
        return {"sError": ""}

    dictLaneTuple = routeContext.fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sOperationName,
    )
    dictCommitted = commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write", sTarget, fdictWrite,
        {
            **routeContext.fdictStampDockerIdForJournal(sContainerId),
            "sExpectedSha256": hashlib.sha256(baContent).hexdigest(),
            "sPriorSha256": routeContext.fsHashContainerFileOrEmpty(
                dictCtx, sContainerId, sTarget,
            ) if sRepo else "",
        },
    )
    return dictCommitted["result"]["sError"]


def _fnWriteReceiptTarget(
    connectionDocker, sContainerId, sProjectRepoPath, sRepoRelPath,
    baContent,
):
    """Land one backend-chosen file below the project repo, never via a link."""
    from vaibify.docker.confinedWrite import ContainerWriteRefusedError
    if not sProjectRepoPath:
        raise ContainerWriteRefusedError(
            "refused: this project has no repository to hold the receipt")
    listParts = sRepoRelPath.split("/")
    if set(listParts) & set(T_RECEIPT_WRITE_FORBIDDEN_NAMES):
        raise ContainerWriteRefusedError(
            "refused: the receipt write may not touch " + sRepoRelPath)
    if fbIsHostProject(sContainerId):
        _fnWriteReceiptOnHost(sProjectRepoPath, listParts, baContent)
        return
    import io
    sRoot = posixpath.normpath(sProjectRepoPath)
    connectionDocker.fnWriteFileFromStream(
        sContainerId, posixpath.join(sRoot, sRepoRelPath),
        io.BytesIO(baContent), iExpectedBytes=len(baContent),
        sAuthorizedRoot=sRoot,
        tForbiddenNames=T_RECEIPT_WRITE_FORBIDDEN_NAMES,
        bCreateParents=True,
    )


def _fnWriteReceiptOnHost(sProjectRepoPath, listParts, baContent):
    """The host leg: parents created and opened without following a link."""
    import os
    from vaibify.host.hostConfinedWrite import fsWriteFileInsideRoot
    sRealRoot = os.path.realpath(sProjectRepoPath)
    _fnMakeHostDirectoriesWithoutFollowing(sRealRoot, listParts[:-1])
    fsWriteFileInsideRoot(
        sRealRoot, os.path.join(sRealRoot, *listParts), iter([baContent]),
    )


def _fnMakeHostDirectoriesWithoutFollowing(sRealRoot, listDirectories):
    """Create each directory below the root, refusing a link at any step."""
    import os
    from vaibify.docker.confinedWrite import ContainerWriteRefusedError
    iDirectory = os.open(sRealRoot, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for sName in listDirectories:
            try:
                os.mkdir(sName, 0o755, dir_fd=iDirectory)
            except FileExistsError:
                pass
            try:
                iNext = os.open(
                    sName, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=iDirectory,
                )
            except OSError as error:
                raise ContainerWriteRefusedError(
                    f"refused: '{sName}' is a symlink or not a directory"
                ) from error
            os.close(iDirectory)
            iDirectory = iNext
    finally:
        os.close(iDirectory)


# ── The completion version: inherited files, one receipt replaced ──
#
# A checkpoint cannot contain its own verification, so once a verify
# establishes Level 2 a completion version is published: Zenodo's
# new-version draft keeps the checkpoint's files (never cleared),
# each inherited file is checked by key, MD5 and size against the
# journaled inventory, only the receipt is replaced -- with bytes
# stored before the draft existed -- and the draft is checked against
# the EXACT expected inventory before it is published. Refusals keep
# the draft: it is journaled, and the recovery routes decide.


class _CompletionRefusal(Exception):
    """A completion step vaibify declined; the message says why."""


def ftResultPublishCompletion(
    connectionDocker, sContainerId, sZenodoService, dictRecord, baReceipt,
    dictMetadata, dictJournalHooks,
):
    """Publish one journaled completion from the HOST; return ``(iExit, sOut)``.

    ``dictJournalHooks`` makes each step durable as it happens:
    ``fnNoteDraft(iDraftId)``, ``fnNoteMismatch(listKeys)``,
    ``fnNoteUploading()`` and ``fnNotePublished(dictPublished)``.
    Nothing here raises for a refused completion.
    """
    from vaibify.reproducibility import archivePromotion
    from .zenodoTokenAccess import (
        ZenodoTokenUnavailable, fsRequireActiveZenodoToken,
    )
    sStored = archivePromotion.fsDescribeStoredReceiptMismatch(
        dictRecord, baReceipt)
    if sStored:
        return (1, "RECEIPT-MISMATCH (nothing was published): " + sStored)
    try:
        sToken = fsRequireActiveZenodoToken(
            connectionDocker, sContainerId, sZenodoService)
    except ZenodoTokenUnavailable as error:
        return (1, "no-token: " + error.sMessage)
    return ftPublishCompletionWithClient(
        zenodoClient.ZenodoClient(sZenodoService, sToken=sToken),
        dictRecord, baReceipt, _fdictBuildApiMetadata(dictMetadata or {}),
        dictJournalHooks)


def ftPublishCompletionWithClient(
    clientZenodo, dictRecord, baReceipt, dictApi, dictJournalHooks,
):
    """Open (or re-find) the completion's draft and finish it with one client."""
    return _ftRunCompletionStep(lambda: _ftFinishCompletionDraft(
        clientZenodo, dictRecord, _fiOpenCompletionDraft(
            clientZenodo, dictRecord, dictApi, dictJournalHooks),
        baReceipt, dictJournalHooks))


def ftResultFinishCompletionDraft(
    clientZenodo, dictRecord, baReceipt, dictJournalHooks,
):
    """Finish the RECORDED draft of an interrupted completion (resume)."""
    def ftFinishRecorded():
        iDraft = int(dictRecord.get("iDepositId") or 0)
        _fnRequireCompletionStamp(clientZenodo.fdictGetDeposit(iDraft),
                                  dictRecord)
        return _ftFinishCompletionDraft(
            clientZenodo, dictRecord, iDraft, baReceipt, dictJournalHooks)
    return _ftRunCompletionStep(ftFinishRecorded)


def _ftRunCompletionStep(ftStep):
    """Run one completion step, turning every refusal into ``(1, message)``."""
    import requests
    try:
        return ftStep()
    except _CompletionRefusal as error:
        return (1, str(error))
    except zenodoClient.ZenodoError as error:
        return (1, "HTTP " + str(error))
    except requests.RequestException as error:
        return (1, repr(error))


def _fiOpenCompletionDraft(clientZenodo, dictRecord, dictApi, dictJournalHooks):
    """Return the completion's draft id: re-found when recorded, else new.

    Ownership is settled BEFORE anything changes. Zenodo's new-version
    call hands back an unpublished draft that already exists, and a
    researcher's own draft carries no vaibify stamp, so "no stamp" can
    never mean "fresh": an existing draft is used only when it carries
    THIS completion's stamp, and refused, untouched, otherwise. Only
    when no draft exists is one opened, recorded at once, and stamped.
    """
    from vaibify.reproducibility import archivePromotion
    if int(dictRecord.get("iDepositId") or 0):
        iDraft = int(dictRecord["iDepositId"])
        _fnRequireCompletionStamp(clientZenodo.fdictGetDeposit(iDraft),
                                  dictRecord)
        return iDraft
    iParent = int(dictRecord.get("iParentDepositId") or 0)
    dictParent = clientZenodo.fdictGetDeposit(iParent)
    _fnRefuseANewerVersion(dictParent, iParent)
    iExisting = _fiExistingDraftOfConcept(clientZenodo, dictParent, iParent)
    if iExisting:
        _fnRequireAnExistingDraftIsOurs(
            clientZenodo.fdictGetDeposit(iExisting), iExisting, dictRecord)
        dictJournalHooks["fnNoteDraft"](iExisting)
        return iExisting
    dictDraft = clientZenodo.fdictGetNewVersionDraft(iParent)
    sForeign = archivePromotion.fsForeignPromotionStamp(
        (dictDraft.get("metadata") or {}).get("description"),
        dictRecord["sPromotionId"])
    if sForeign:
        raise _CompletionRefusal(
            f"FOREIGN-DRAFT (nothing was published): Zenodo returned draft "
            f"{dictDraft.get('id')}, which belongs to another vaibify "
            f"operation ({sForeign}). Vaibify will not clear or publish "
            "someone else's draft; finish or discard it on Zenodo first.")
    iDraft = int(dictDraft["id"])
    _fnRequireAFreshCopyOfTheCheckpoint(dictDraft, iDraft, dictParent)
    dictJournalHooks["fnNoteDraft"](iDraft)
    dictStamped = dict(dictApi)
    dictStamped["description"] = archivePromotion.fsStampPromotionIdIntoDescription(
        dictApi.get("description"), dictRecord["sPromotionId"])
    clientZenodo.fnSetMetadata(iDraft, dictStamped)
    return iDraft


def _fiExistingDraftOfConcept(clientZenodo, dictParent, iParent):
    """Return the id of an unpublished draft this archive already has, or 0.

    Asked two ways, because a new version's draft is reused rather than
    reopened: the parent's ``links.latest_draft`` naming a deposit other
    than itself, then the token's own depositions of the same concept
    that are not yet submitted.
    """
    sLatestDraft = str((dictParent.get("links") or {}).get("latest_draft")
                       or "")
    sDraftId = sLatestDraft.rstrip("/").rsplit("/", 1)[-1]
    if sDraftId.isdigit() and int(sDraftId) != int(iParent):
        return int(sDraftId)
    sConcept = str(dictParent.get("conceptrecid") or "")
    if not sConcept:
        return 0
    for dictDeposit in clientZenodo.flistSearchDeposits(
            "conceptrecid:" + sConcept) or []:
        if (str(dictDeposit.get("conceptrecid") or "") == sConcept
                and dictDeposit.get("submitted") is False
                and int(dictDeposit.get("id") or 0) != int(iParent)):
            return int(dictDeposit["id"])
    return 0


def _fnRequireAFreshCopyOfTheCheckpoint(dictDraft, iDraft, dictParent):
    """Refuse, touching nothing, a returned draft that holds anyone's work.

    The new-version call answers the same whether it opened the draft or
    handed back one somebody opened since the existence check, and no
    Zenodo response tells the two apart, so ownership of an unstamped
    draft cannot be proven. What can be checked is that the draft holds
    no one's work: exactly the checkpoint's files, and its title and
    description, as the parent holds them. A draft that departs from
    either was changed by someone, and is refused.
    """
    from vaibify.reproducibility import archivePromotion
    listDeparted = archivePromotion.flistDescribeDepartureFromParent(
        dictDraft, dictParent)
    dictDraftMetadata = dictDraft.get("metadata") or {}
    dictParentMetadata = dictParent.get("metadata") or {}
    listDeparted += [
        sField for sField in ("title", "description")
        if (dictDraftMetadata.get(sField) or "") != (
            dictParentMetadata.get(sField) or "")]
    if not listDeparted:
        return
    raise _CompletionRefusal(
        f"FOREIGN-DRAFT (nothing was published): Zenodo handed back draft "
        f"{iDraft}, which is not a fresh copy of the checkpoint (it differs "
        "in " + ", ".join(listDeparted) + "), so someone else opened and "
        "changed it. Vaibify will not change or publish it; publish or "
        "discard it on Zenodo, then try again.")


def _fnRequireAnExistingDraftIsOurs(dictDraft, iDraft, dictRecord):
    """Refuse, touching nothing, a draft this completion did not open."""
    from vaibify.reproducibility import archivePromotion
    if archivePromotion.fbDescriptionCarriesPromotionId(
            (dictDraft.get("metadata") or {}).get("description"),
            dictRecord.get("sPromotionId")):
        return
    raise _CompletionRefusal(
        f"FOREIGN-DRAFT (nothing was published): this archive already has "
        f"an unpublished draft, deposit {iDraft}, that vaibify did not open "
        "for this completion. Vaibify will not change or publish someone "
        "else's draft; publish or discard it on Zenodo, then try again.")


def _fnRefuseANewerVersion(dictParentDeposit, iParent):
    """Refuse unless the checkpoint is still the concept's latest version."""
    sLatest = str((dictParentDeposit.get("links") or {}).get("latest") or "")
    sLatestId = sLatest.rstrip("/").rsplit("/", 1)[-1] if sLatest else ""
    if sLatestId.isdigit() and int(sLatestId) != int(iParent):
        raise _CompletionRefusal(
            f"NEWER-VERSION (nothing was published): record {iParent} is no "
            f"longer this archive's latest version; record {sLatestId} is. "
            "A completion only follows the checkpoint it completes.")


def _fnRequireCompletionStamp(dictDeposit, dictRecord):
    """Refuse a recorded draft that no longer names this completion."""
    from vaibify.reproducibility import archivePromotion
    if not archivePromotion.fbDescriptionCarriesPromotionId(
            (dictDeposit.get("metadata") or {}).get("description"),
            dictRecord.get("sPromotionId")):
        raise _CompletionRefusal(
            "FOREIGN-DRAFT (nothing was published): the recorded draft no "
            "longer names this completion, so vaibify will not act on it.")


def _ftFinishCompletionDraft(
    clientZenodo, dictRecord, iDraft, baReceipt, dictJournalHooks,
):
    """Check the inherited files, replace the receipt, check, publish."""
    from vaibify.reproducibility import archivePromotion
    dictDeposit = clientZenodo.fdictGetDeposit(iDraft)
    listInherited = archivePromotion.flistDescribeInheritedMismatch(
        dictRecord, dictDeposit)
    if listInherited:
        dictJournalHooks["fnNoteMismatch"](listInherited)
        raise _CompletionRefusal(
            f"INHERITED-MISMATCH (nothing was published): draft {iDraft} "
            "did not inherit the checkpoint's files intact: "
            + ", ".join(listInherited) + ".")
    _fnReplaceCompletionReceipt(
        clientZenodo, iDraft, dictDeposit, dictRecord, baReceipt,
        dictJournalHooks["fnNoteUploading"])
    listProblems = archivePromotion.flistDescribeCompletionInventoryMismatch(
        dictRecord, clientZenodo.fdictGetDeposit(iDraft))
    if listProblems:
        raise _CompletionRefusal(
            f"ARCHIVE-MISMATCH (nothing was published): draft {iDraft} does "
            "not hold exactly the intended files: "
            + ", ".join(listProblems) + ".")
    dictPublished = clientZenodo.fdictPublishDraft(iDraft)
    dictJournalHooks["fnNotePublished"](dictPublished)
    return (0, "ZENODO_RESULT=" + json.dumps({
        "iDepositId": iDraft,
        "sDoi": dictPublished.get("doi", ""),
        "sConceptDoi": dictPublished.get("conceptdoi", ""),
        "sHtmlUrl": (dictPublished.get("links") or {}).get("html", ""),
    }))


def _fnReplaceCompletionReceipt(
    clientZenodo, iDraft, dictDeposit, dictRecord, baReceipt, fnNoteUploading,
):
    """Delete the inherited receipt and upload the stored bytes, unless done.

    A draft already holding the JOURNALED receipt (its MD5 and size)
    needs no bytes at all, so a resume can publish it after the stored
    copy is gone. Otherwise the stored bytes are checked against the
    journal before they are uploaded: a resume reads them back from
    disk, where they could have changed.
    """
    import os
    from vaibify.host.hostConfinedWrite import fsWriteFileInsideRoot
    from vaibify.reproducibility import archivePromotion
    dictWanted = dictRecord["dictCompletionReceipt"]
    dictPresent = next((
        dictFile for dictFile in dictDeposit.get("files") or []
        if (dictFile.get("filename") or dictFile.get("key"))
        == dictWanted["sKey"]), None)
    if dictPresent and archivePromotion.fbDepositFileMatches(
            dictWanted, dictPresent):
        return
    sStored = archivePromotion.fsDescribeStoredReceiptMismatch(
        dictRecord, baReceipt)
    if sStored:
        raise _CompletionRefusal(
            "RECEIPT-MISMATCH (nothing was published): " + sStored)
    if dictPresent:
        clientZenodo.fnDeleteDraftFile(
            iDraft, dictPresent.get("id") or dictPresent.get("file_id"))
    fnNoteUploading()
    sScratchDirectory = _fsCreateHostScratchDirectory()
    try:
        clientZenodo.fnUploadToBucket(
            (dictDeposit.get("links") or {}).get("bucket"),
            fsWriteFileInsideRoot(
                sScratchDirectory,
                os.path.join(sScratchDirectory, dictWanted["sKey"]),
                iter([baReceipt])))
    finally:
        _fnRemoveScratchDirectory(sScratchDirectory)


def fsResolveSidecarKey(dictCtx, sContainerId, dictWorkflow):
    """Return the syncStatus.json section key for this workflow."""
    from . import stateManager
    return stateManager.fsWorkflowKeyFromPath(
        (dictCtx.get("paths") or {}).get(sContainerId, ""),
        (dictWorkflow or {}).get("sProjectRepoPath") or "",
    )


def fdictCompletionJournalHooks(filesRepo, sWorkflowKey, sPromotionId):
    """Return the hooks that make each completion step durable as it happens."""
    from datetime import datetime, timezone
    from vaibify.reproducibility import archivePromotion, syncBookkeeping

    def fnNote(dictFields):
        syncBookkeeping.fnUpdatePendingPromotion(
            filesRepo, sWorkflowKey, sPromotionId, dictFields)

    def fsNowIso():
        return datetime.now(timezone.utc).isoformat()

    return {
        "fnNoteDraft": lambda iDraft: fnNote({
            "iDepositId": int(iDraft),
            "sPhase": archivePromotion.S_PHASE_DRAFTED}),
        "fnNoteMismatch": lambda listKeys: fnNote({
            "listInheritedMismatch": list(listKeys)}),
        "fnNoteUploading": lambda: fnNote({
            "sPhase": archivePromotion.S_PHASE_UPLOADING,
            "sUploadingIso": fsNowIso()}),
        "fnNotePublished": lambda dictPublished: fnNote({
            "sPhase": archivePromotion.S_PHASE_PUBLISHED,
            "sPublishedIso": fsNowIso(),
            "sDoi": str(dictPublished.get("doi") or "")}),
    }


def fbaReadPendingReceiptOrNone(filesRepo, sPromotionId):
    """Return a completion's stored receipt bytes, or None when unreadable."""
    try:
        return filesRepo.fbaReadBytes(fsPendingReceiptRepoPath(sPromotionId))
    except (OSError, ValueError, KeyError):
        return None


def fnSettleCompletion(dictCtx, sContainerId, dictWorkflow, sPromotionId):
    """Delete the stored receipt, drop the journal entry, sweep orphans."""
    from vaibify.reproducibility import syncBookkeeping
    from .routeContext import ffilesForWorkflow
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
    sWorkflowKey = fsResolveSidecarKey(dictCtx, sContainerId, dictWorkflow)
    filesRepo.fbRemoveFile(fsPendingReceiptRepoPath(sPromotionId))
    syncBookkeeping.fnRemovePendingPromotion(
        filesRepo, sWorkflowKey, sPromotionId)
    fnSweepOrphanPendingReceipts(filesRepo)
    syncBookkeeping.fnMirrorPendingPromotions(
        filesRepo, sWorkflowKey, dictWorkflow)


def fnSweepOrphanPendingReceipts(filesRepo):
    """Delete each stored receipt that no project's journal entry names.

    A crash between storing a completion receipt and recording its
    intent leaves a file with no entry; every project in the repository
    is consulted, because the directory is shared by them all.
    """
    from vaibify.reproducibility import syncBookkeeping
    setLive = set(syncBookkeeping.flistAllPendingPromotionIds(filesRepo))
    try:
        listNames = filesRepo.flistListJsonFilenames(
            S_PENDING_RECEIPTS_DIRECTORY)
    except (OSError, ValueError):
        return
    for sName in listNames:
        sPromotionId = sName[:-len(".json")]
        if _RE_PROMOTION_ID.fullmatch(sPromotionId) and (
                sPromotionId not in setLive):
            filesRepo.fbRemoveFile(fsPendingReceiptRepoPath(sPromotionId))


async def fdictSettlePublishedCompletion(
    dictCtx, sContainerId, dictWorkflow, requestHttp, dictRecord,
    dictPublished, baReceipt,
):
    """Record, write, settle and re-verify a published completion.

    In this order: the deposit record (receipt kind completion), the
    repository receipt (only now that the version exists), the stored
    file and journal entry, then a verify of the new record -- until
    which the Zenodo copy reads unknown, because the declared record
    changed. ``baReceipt`` is None when the bytes could not be restored.
    """
    from vaibify.reproducibility import archivePromotion, syncBookkeeping
    from .remoteBindingContext import fnRecordZenodoOutcomeInBinding
    from .routeContext import (
        fdictCommitWorkflowSave, fsRefreshVerifyCacheAfterPush,
    )
    sService = str(dictRecord.get("sTargetService") or "sandbox")
    syncBookkeeping.fnRecordZenodoPublish(dictWorkflow, dictPublished, sService)
    syncBookkeeping.fnRecordPublishedReceipt(
        dictWorkflow, archivePromotion.fdictDescribeCompletionReceiptNote(
            dictRecord, dictPublished))
    fnRecordZenodoOutcomeInBinding(
        dictCtx, sContainerId, dictWorkflow, sService,
        str(dictWorkflow.get("sZenodoConceptDoi") or ""),
        str(dictWorkflow.get("sZenodoDepositionId") or ""))
    fdictCommitWorkflowSave(dictCtx, sContainerId, dictWorkflow, requestHttp,
                            "The Zenodo completion record")
    sReceiptWarning = fsCommitProofReceiptWrite(
        dictCtx, sContainerId, dictWorkflow, requestHttp, baReceipt,
    ) if baReceipt is not None else (
        "the published receipt's bytes could not be restored")
    await fnSettleCompletionUnderTheDrain(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        dictRecord["sPromotionId"])
    sVerifyWarning = await fsRefreshVerifyCacheAfterPush(
        dictCtx, sContainerId, dictWorkflow, "zenodo", requestHttp)
    _fnRememberCompletionVerifyWarning(
        dictCtx, sContainerId, dictWorkflow, requestHttp, sVerifyWarning)
    return {
        "bCompleted": True,
        "sDoi": str(dictPublished.get("sDoi") or ""),
        "sProofReceiptWarning": sReceiptWarning,
        "sPostCompletionVerifyWarning": sVerifyWarning,
    }


def _fnRememberCompletionVerifyWarning(
    dictCtx, sContainerId, dictWorkflow, requestHttp, sVerifyWarning,
):
    """Keep why the completion's verify failed, so the row can say so later.

    Bookkeeping on the receipt note (the sidecar), saved only when there
    is something to say; a later successful verify makes it moot, because
    the row reads coverage first.
    """
    from .routeContext import fdictCommitWorkflowSave
    if not sVerifyWarning:
        return
    dictNote = ((dictWorkflow.get("dictRemotes") or {}).get("zenodo") or {}
                ).get("dictReceipt")
    if not isinstance(dictNote, dict):
        return
    dictNote["sVerifyWarning"] = sVerifyWarning
    fdictCommitWorkflowSave(dictCtx, sContainerId, dictWorkflow, requestHttp,
                            "The Zenodo completion's verify warning")


async def fnSettleCompletionUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, requestHttp, sPromotionId,
):
    """Run :func:`fnSettleCompletion` as a mode-(b) worker."""
    from .routeContext import (
        fdictCarryARefusalBackInsteadOfRaising, fgenericRunWorkerUnderTheDrain,
    )

    def fdictSettle(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: fnSettleCompletion(
                dictCtx, sContainerId, dictWorkflow, sPromotionId))

    await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictSettle, "zenodo-completion-settle", requestHttp)


_DICT_ZENODO_API_BASE = {
    "sandbox": "https://sandbox.zenodo.org/api",
    "zenodo": "https://zenodo.org/api",
}


def ftResultArchiveToZenodo(
    connectionDocker, sContainerId, sZenodoService, listFilePaths,
    dictMetadata=None, iParentDepositId=0, sExtraFileKey="",
    fbaBuildExtraFile=None,
):
    """Deposit files on Zenodo from the HOST; return ``(iExitCode, sOutput)``.

    ``sZenodoService`` is the ZenodoClient service key (``"sandbox"`` or
    ``"zenodo"``). The token is the project's ACTIVE host slot
    (:mod:`vaibify.gui.zenodoTokenAccess`): nothing in the container
    holds one any more, and no in-container program runs. The selected
    files leave the container through the confined reader into a
    private host scratch directory, each is hashed immediately before
    its own upload, and the draft is asked what it holds BEFORE the
    publish, while it is still discardable. ``iParentDepositId`` drives
    the versioning flow: 0 creates a fresh deposit; a positive id takes
    the parent's ``newversion`` draft, clears the inherited files and
    updates the metadata.

    ``fbaBuildExtraFile``, when given, is handed the STAGED files --
    ``{sContainerPath, sKey, sSha256, sMd5, iSizeBytes}`` each, hashed
    from the bytes this call will upload -- and returns the bytes of one
    more file, staged as ``sExtraFileKey`` and uploaded and checked with
    the rest. The PROOF receipt rides this way; the dispatcher stays
    generic.

    The tuple and its message vocabulary (``HTTP ...``,
    ``ARCHIVE-MISMATCH``, ``LOCAL-FILE-ERROR``, ``orphan draft``) are
    the in-container script's, so ``fdictClassifyError`` and the
    dashboard read them unchanged. Nothing here raises for a failed
    deposit: a carrier worker that raised would quarantine the
    container over a rejected token.
    """
    from .zenodoTokenAccess import (
        ZenodoTokenUnavailable, fsRequireActiveZenodoToken,
    )
    if sZenodoService not in _DICT_ZENODO_API_BASE:
        raise ValueError(
            f"Invalid Zenodo service: {sZenodoService}"
        )
    _fnValidateArchiveFilePaths(listFilePaths)
    dictApi = _fdictBuildApiMetadata(dictMetadata or {})
    _fnValidateApiMetadata(dictApi)
    try:
        sToken = fsRequireActiveZenodoToken(
            connectionDocker, sContainerId, sZenodoService,
        )
    except ZenodoTokenUnavailable as error:
        return (1, "no-token: " + error.sMessage)
    sScratchDirectory = _fsCreateHostScratchDirectory()
    try:
        listLocalPaths = _flistStageFilesOnHost(
            connectionDocker, sContainerId, listFilePaths, sScratchDirectory,
        )
        if fbaBuildExtraFile is not None:
            listLocalPaths.append(_fsStageExtraFile(
                sScratchDirectory, listFilePaths, listLocalPaths,
                sExtraFileKey, fbaBuildExtraFile,
            ))
    except (OSError, ValueError) as error:
        _fnRemoveScratchDirectory(sScratchDirectory)
        return (1, "LOCAL-FILE-ERROR (not a Zenodo problem): " + repr(error))
    try:
        return _ftDepositStagedFiles(
            zenodoClient.ZenodoClient(sZenodoService, sToken=sToken),
            listLocalPaths, dictApi, int(iParentDepositId or 0),
        )
    finally:
        _fnRemoveScratchDirectory(sScratchDirectory)


def _fsCreateHostScratchDirectory():
    """Return a fresh private directory under ``~/.vaibify`` for one operation."""
    from vaibify.reproducibility.imageDeposit import (
        fsResolveDepositScratchDirectory,
    )
    return fsResolveDepositScratchDirectory()


def _fnRemoveScratchDirectory(sScratchDirectory):
    import shutil
    shutil.rmtree(sScratchDirectory, ignore_errors=True)


def _flistStageFilesOnHost(
    connectionDocker, sContainerId, listFilePaths, sScratchDirectory,
):
    """Stream each selected file out through the confined reader; return host paths.

    A Zenodo deposit and an Overleaf figure directory are both flat, so
    a file lands under its basename and a second file with the same
    basename is refused here as a local error rather than silently
    overwriting the first on the way out.
    """
    import os
    from vaibify.host.hostConfinedWrite import fsWriteFileInsideRoot
    from .projectRoots import fsResolveProjectRoot
    sProjectRoot = fsResolveProjectRoot(sContainerId, "/workspace")
    listLocalPaths = []
    setBasenames = set()
    for sFilePath in listFilePaths:
        sBasename = posixpath.basename(sFilePath)
        if not sBasename or sBasename in setBasenames:
            raise ValueError(
                f"two selected files share the name {sBasename!r}; a "
                "Zenodo deposit is flat"
            )
        setBasenames.add(sBasename)
        listLocalPaths.append(fsWriteFileInsideRoot(
            sScratchDirectory, os.path.join(sScratchDirectory, sBasename),
            connectionDocker.fiterReadFileConfined(
                sContainerId, sFilePath, sAuthorizedRoot=sProjectRoot,
            ),
        ))
    return listLocalPaths


def _fsStageExtraFile(
    sScratchDirectory, listFilePaths, listLocalPaths, sKey, fbaBuildExtraFile,
):
    """Stage one more file built from the staged ones; return its host path."""
    import os
    from vaibify.host.hostConfinedWrite import fsWriteFileInsideRoot
    if not sKey or posixpath.basename(sKey) != sKey or any(
            posixpath.basename(sPath) == sKey for sPath in listFilePaths):
        raise ValueError(
            f"a selected file is named {sKey!r}, which vaibify writes into "
            "every archive itself; a Zenodo deposit is flat")
    listStaged = [
        _fdictDescribeStagedFile(sContainerPath, sLocalPath)
        for sContainerPath, sLocalPath in zip(listFilePaths, listLocalPaths)
    ]
    baContent = fbaBuildExtraFile(listStaged)
    return fsWriteFileInsideRoot(
        sScratchDirectory, os.path.join(sScratchDirectory, sKey),
        iter([baContent]),
    )


def _fdictDescribeStagedFile(sContainerPath, sLocalPath):
    """Return one staged file's identity, hashed from the bytes to upload."""
    import os
    from vaibify.reproducibility._hashing import ftHashFileSha256AndMd5
    sSha256, sMd5 = ftHashFileSha256AndMd5(sLocalPath)
    return {
        "sContainerPath": sContainerPath,
        "sKey": os.path.basename(sLocalPath),
        "sSha256": sSha256, "sMd5": sMd5,
        "iSizeBytes": os.path.getsize(sLocalPath),
    }


def _ftDepositStagedFiles(clientZenodo, listLocalPaths, dictApi, iParent):
    """Open the draft, upload, compare, publish; abort the draft on any failure."""
    import requests
    try:
        iDepositId, sBucketUrl = _ftOpenArchiveDraft(clientZenodo, dictApi, iParent)
    except zenodoClient.ZenodoError as error:
        return (1, "HTTP " + str(error))
    except requests.RequestException as error:
        return (1, repr(error))
    try:
        listExpected = []
        for sLocalPath in listLocalPaths:
            # Hashed immediately before its own upload, so the digest
            # describes the bytes this call is about to send.
            listExpected.append(
                zenodoClient.fdictDescribeLocalFileForDeposit(sLocalPath),
            )
            clientZenodo.fnUploadToBucket(sBucketUrl, sLocalPath)
        dictDraftNow = clientZenodo.fdictGetDeposit(iDepositId)
        listProblems = (
            zenodoClient.flistDescribeDepositDisagreement(dictDraftNow, listExpected)
            + zenodoClient.flistDescribeUnexpectedDepositFiles(
                dictDraftNow, listExpected,
            )
        )
        if listProblems:
            return _ftAbortArchiveDraft(clientZenodo, iDepositId, (
                "ARCHIVE-MISMATCH (nothing was published): Zenodo does not "
                "hold what vaibify uploaded: " + " ".join(listProblems)
            ))
        dictPublished = clientZenodo.fdictPublishDraft(iDepositId)
    except (FileNotFoundError, PermissionError, IsADirectoryError) as error:
        return _ftAbortArchiveDraft(clientZenodo, iDepositId, (
            "LOCAL-FILE-ERROR (not a Zenodo problem): " + repr(error)
        ))
    except zenodoClient.ZenodoError as error:
        return _ftAbortArchiveDraft(clientZenodo, iDepositId, "HTTP " + str(error))
    except requests.RequestException as error:
        return _ftAbortArchiveDraft(clientZenodo, iDepositId, repr(error))
    return (0, "ZENODO_RESULT=" + json.dumps({
        "iDepositId": iDepositId,
        "sDoi": dictPublished.get("doi", ""),
        "sConceptDoi": dictPublished.get("conceptdoi", ""),
        "sHtmlUrl": (dictPublished.get("links") or {}).get("html", ""),
    }))


def _ftOpenArchiveDraft(clientZenodo, dictApi, iParent):
    """Return ``(iDepositId, sBucketUrl)`` for a fresh or new-version draft."""
    if iParent > 0:
        dictDraft = clientZenodo.fdictGetNewVersionDraft(iParent)
        clientZenodo.fnClearDraftFiles(dictDraft["id"])
        clientZenodo.fnSetMetadata(dictDraft["id"], dictApi)
    else:
        dictDraft = clientZenodo.fdictCreateDraft(dictApi)
    return dictDraft["id"], dictDraft["links"]["bucket"]


def _ftAbortArchiveDraft(clientZenodo, iDepositId, sMessage):
    """Discard the unpublished draft and say whether that worked."""
    import requests
    try:
        clientZenodo.fnDeleteDraft(iDepositId)
    except (zenodoClient.ZenodoError, requests.RequestException):
        return (1, f"{sMessage} | draft {iDepositId} still on Zenodo; discard manually")
    return (1, f"{sMessage} | orphan draft {iDepositId} deleted")


def _fdictBuildApiMetadata(dictMetadata):
    """Translate a vaibify metadata dict to the Zenodo API shape.

    Delegates to the Zenodo boundary, which is where the mapping now
    lives so that the science deposit and the environment-archive
    deposit cannot drift apart on it. The defaults are invisible to a
    caller -- a creators placeholder Zenodo will accept, a licence --
    and two copies of them would only ever announce their divergence
    as a rejected upload.
    """
    return zenodoClient.fdictBuildApiMetadata(dictMetadata)


def _fnValidateApiMetadata(dictApi):
    """Sanity-check the translated API metadata."""
    if not dictApi.get("title"):
        raise ValueError("Zenodo metadata title cannot be empty")
    if not dictApi.get("creators"):
        raise ValueError(
            "Zenodo metadata must have at least one creator"
        )


def _fnValidateArchiveFilePaths(listFilePaths):
    """Reject paths that could cause open() or the shell to misbehave."""
    for sPath in listFilePaths:
        if not isinstance(sPath, str) or not sPath:
            raise ValueError("Archive file path must be non-empty string")
        if "\x00" in sPath:
            raise ValueError(
                f"Null byte in archive path: {sPath!r}"
            )


def _fsGithubHardeningFlags():
    """Return the hardening ``-c`` flags joined for shell invocation."""
    return " ".join(fsShellQuote(s) for s in LIST_GIT_HARDENING_CONFIG)


def _fsComposePublishSuffix(sHardening, sCommitMessage):
    """Return the shared tail of every GitHub push: commit, push, report.

    The three push variants differ ONLY in what they stage, and shared
    this tail byte for byte. That cost twice: the empty-commit guard
    reached two of the three on 2026-07-02 and the third only after it
    shipped a bogus "sync failed" to a researcher, and the falsification
    entries had to declare iExpectedOccurrences=3 because mutating one
    copy of a three-copy guard changes nothing any test can observe.
    One copy fixes both.

    The parentheses are load-bearing in a non-obvious way: ``a && b ||
    c && d`` parses as ``((a && b) || c) && d``, so an ungrouped skip
    lets a FAILED add fall through to the commit, which publishes
    whatever else was staged. A failed add with a CLEAN index stops
    under either parse, so that case cannot detect the difference.
    """
    return (
        f"(git diff --cached --quiet || "
        f"git {sHardening} commit -m {fsShellQuote(sCommitMessage)}) && "
        f"git {sHardening} push && "
        f"git {sHardening} rev-parse --short HEAD"
    )


def ftResultPushToGithub(
    connectionDocker, sContainerId,
    listFilePaths, sCommitMessage, sWorkdir,
):
    """Git add, commit, and push files inside the container.

    Hardened in Phase 6: every git invocation carries the shared
    protocol/symlink/submodule guards so a malicious .gitmodules or
    hostile symlink target cannot hijack the push.

    The commit is guarded exactly as in ``ftResultPushStagedToGithub``:
    when a previous attempt committed but its push failed, a retry
    stages nothing, an unconditional ``git commit`` exits "nothing to
    commit", and the chain dies before the push leg — so the repo can
    never be pushed by the dashboard again AND the original push
    error is unreachable behind the commit refusal (found live,
    2026-08-17, two stranded commits in a host project). Skipping the
    empty commit lets the retry publish what is already committed.
    """
    sQuotedPaths = " ".join(fsShellQuote(s) for s in listFilePaths)
    sHardening = _fsGithubHardeningFlags()
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git {sHardening} add -- {sQuotedPaths} && "
        + _fsComposePublishSuffix(sHardening, sCommitMessage)
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )


def ftResultPushStagedToGithub(
    connectionDocker, sContainerId, sCommitMessage, sWorkdir,
):
    """Stage tracked changes, commit (if any), and push to origin.

    Returns (iExitCode, sCombinedOutput). Hardened alongside
    ``ftResultPushToGithub``. ``git add -u`` stages modifications and
    deletions of TRACKED files only (2026-08-27 ruling): the Repos
    panel's dirty-dot tooltip always promised "click Push to commit
    and push them", but until this the command committed only what
    was already staged — and nothing in the dashboard stages — so on
    a dirty repo the button silently published nothing while claiming
    success. Untracked files stay excluded on purpose; publishing a
    file nobody ever added needs the explicit selection of
    "Push files…". The commit is still skipped when the index matches
    HEAD (``git diff --cached --quiet`` exits 0), so a repo that is
    already committed but ahead of origin still pushes — an
    unconditional ``git commit`` fails with "nothing to commit" there
    and the push never runs.
    """
    sHardening = _fsGithubHardeningFlags()
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git {sHardening} add -u && "
        + _fsComposePublishSuffix(sHardening, sCommitMessage)
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )


_DICT_PORCELAIN_STATUS = {
    "M": "modified", "A": "added", "D": "deleted",
    "R": "renamed", "C": "copied", "U": "unmerged",
    "?": "untracked", "!": "ignored", "T": "modified",
}


def _fdictParsePorcelainLine(sLine):
    """Parse a single `git status --porcelain` line into a dict."""
    if len(sLine) < 4:
        return None
    sCodes = sLine[:2]
    sPath = sLine[3:].strip()
    if sCodes == "??":
        return {"sPath": sPath, "sStatus": "untracked"}
    sPrimary = sCodes[0] if sCodes[0] != " " else sCodes[1]
    sStatus = _DICT_PORCELAIN_STATUS.get(sPrimary, "unknown")
    return {"sPath": sPath, "sStatus": sStatus}


def flistGetDirtyFiles(connectionDocker, sContainerId, sWorkdir):
    """List uncommitted files in sWorkdir as status dicts."""
    sCommand = f"git -C {fsShellQuote(sWorkdir)} status --porcelain"
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExitCode != 0:
        return []
    listResults = []
    for sLine in (sOutput or "").splitlines():
        if not sLine.strip():
            continue
        dictEntry = _fdictParsePorcelainLine(sLine)
        if dictEntry is not None:
            listResults.append(dictEntry)
    return listResults


def ftResultAddFileToGithub(
    connectionDocker, sContainerId,
    sFilePath, sCommitMessage, sWorkdir,
):
    """Git add, commit, push a single file inside the container.

    Shares the hardening-flag discipline with ``ftResultPushToGithub``.

    The commit is SKIPPED when the add staged nothing, using the guard
    the two sibling pushes grew after the 2026-07-02 live bug. This
    function was left with the original defect and shipped it: an
    already-committed file stages nothing, ``git commit`` exits
    "nothing added to commit", ``&&`` swallows the push, and the
    researcher is told the sync failed about a file already on GitHub.
    That is the ordinary state of a published file, and the state an
    orange (unverified) badge is pushed FROM -- so the only action the
    badge offered failed for the researcher who had already published.

    The grouping parentheses are load-bearing, but not in the obvious
    way: a failed add with a CLEAN index stops under either parse. They
    diverge on a DIRTY index, where the ungrouped form falls through to
    the commit and publishes unrelated staged work.
    """
    sHardening = _fsGithubHardeningFlags()
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git {sHardening} add -- {fsShellQuote(sFilePath)} && "
        + _fsComposePublishSuffix(sHardening, sCommitMessage)
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )


def ftResultGenerateLatex(
    connectionDocker, sContainerId, listFigurePaths, sOutputPath,
):
    """Generate LaTeX includes file inside the container."""
    sImport = (
        "from vaibify.reproducibility.latexConnector "
        "import fnWriteLatexIncludes"
    )
    sCall = (
        f"fnWriteLatexIncludes("
        f"{repr(listFigurePaths)}, {repr(sOutputPath)})"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall, sContainerId)
    )


def fdictCheckConnectivity(
    connectionDocker, sContainerId, sService, sProjectRepoPath="",
):
    """Check if credentials are available for a service.

    ``sProjectRepoPath`` is the workflow's project repo — the exact
    directory the push will run in — and only the GitHub check uses
    it: probing any other directory lets the check and the push
    disagree, which is how a host project was told to run
    ``gh auth login`` while ``gh auth status`` was green.
    """
    fnValidateServiceName(sService)
    if sService == "github":
        return _fdictCheckGithub(
            connectionDocker, sContainerId, sProjectRepoPath,
        )
    if sService == "overleaf":
        return _fdictCheckHostKeyring("overleaf_token")
    if sService == "zenodo":
        return _fdictCheckZenodoKeyring(
            connectionDocker, sContainerId,
        )
    return {"bConnected": False, "sMessage": "Unknown service"}


def _fdictCheckZenodoKeyring(connectionDocker, sContainerId):
    """Return Connected if an ACTIVE host slot holds a token for this project."""
    from vaibify.config import zenodoTokenSlots
    from .zenodoTokenAccess import fsProjectUuidForContainer
    sProjectUuid = fsProjectUuidForContainer(connectionDocker, sContainerId)
    if not sProjectUuid:
        return {
            "bConnected": False,
            "sMessage": (
                "This container is not a registered project, so no "
                "Zenodo token is stored for it."
            ),
        }
    try:
        for sInstance in zenodoTokenSlots.T_ZENODO_INSTANCES:
            if zenodoTokenSlots.fbHasActiveToken(sInstance, sProjectUuid):
                return {"bConnected": True, "sMessage": "Connected"}
        bPending = bool(zenodoTokenSlots.flistPendingSlotsFor(sProjectUuid))
    except zenodoTokenSlots.TokenStoreUnreadable as error:
        # Could not check is not "not connected": the sentence says so.
        return {"bConnected": False, "sMessage": f"Could not check: {error}"}
    if bPending:
        return {
            "bConnected": False,
            "sMessage": (
                "A Zenodo token moved out of this project's container "
                "awaits your confirmation before it can be used."
            ),
        }
    return {"bConnected": False, "sMessage": "Token not found"}


def _fdictCheckHostKeyring(sTokenName):
    """Check whether a credential exists in the host OS keyring."""
    from vaibify.config.secretManager import fbSecretExists
    if fbSecretExists(sTokenName, "keyring"):
        return {"bConnected": True, "sMessage": "Connected"}
    return {"bConnected": False, "sMessage": "Token not found"}


_S_HOST_CREDENTIAL_MISSING_MESSAGE = (
    "The project repository reaches GitHub, but this host cannot "
    "resolve a GitHub credential, so a push from the dashboard will "
    "be refused. Run 'gh auth login' or store a per-repository token "
    "in Settings."
)

_S_NO_PROJECT_REPO_MESSAGE = (
    "No workflow project is open, so there is no repository to check "
    "against GitHub."
)


def _fsComposeGithubReachabilityCommand(sProjectRepoPath):
    """Return the command probing the project repo's origin remote."""
    return (
        f"cd {fsShellQuote(sProjectRepoPath)} && "
        "git ls-remote --exit-code origin HEAD >/dev/null 2>&1"
    )


def _fsComposeGithubRemoteUrlCommand(sProjectRepoPath):
    """Return the command reading the project repo's origin URL."""
    return (
        f"cd {fsShellQuote(sProjectRepoPath)} && "
        "git remote get-url origin"
    )


def _fdictCheckGithub(connectionDocker, sContainerId, sProjectRepoPath):
    """Report GitHub readiness for both lanes a dashboard push depends on.

    The repo lane answers "can the project repo reach its remote".
    The host lane answers "can this process resolve the credential the
    dashboard's own push will use" — the push runs on the host, not in
    the container. The two fail independently, so probing only the
    repo reports Connected while every UI push is refused. Both are
    probed and both are reported.

    Every probe runs in ``sProjectRepoPath`` — the same directory the
    push route passes as its workdir — never a hardcoded root. The
    predecessor scanned ``/workspace/*/`` for the first repo it
    found, which probed the wrong repo in a multi-repo container
    and, for a host project, probed a directory that does not exist
    on the researcher's machine — so every host push was refused
    with a credential hint while ``gh auth status`` was green. An
    empty path refuses before any command runs.
    """
    if not sProjectRepoPath:
        return {
            "bConnected": False,
            "bContainerReachesGithub": False,
            "bHostCredentialAvailable": False,
            "sMessage": _S_NO_PROJECT_REPO_MESSAGE,
        }
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        _fsComposeGithubReachabilityCommand(sProjectRepoPath),
    )
    bContainerReaches = iExitCode == 0
    bHostCredential = _fbHostGithubCredentialAvailable(
        _fsReadGithubRemoteUrl(
            connectionDocker, sContainerId, sProjectRepoPath,
        ),
    )
    return _fdictDescribeGithubConnectivity(
        bContainerReaches, bHostCredential,
    )


def _fsReadGithubRemoteUrl(connectionDocker, sContainerId, sProjectRepoPath):
    """Return the origin URL of the project repo, '' when unreadable."""
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        _fsComposeGithubRemoteUrlCommand(sProjectRepoPath),
    )
    if iExitCode != 0:
        return ""
    return (sOutput or "").strip().split("\n")[-1].strip()


def _fbHostGithubCredentialAvailable(sRemoteUrl):
    """Return True when the host resolves the token the push will use.

    Exercises exactly the resolution the push route performs — the
    per-repository keyring slot with a ``gh auth token`` fallback — so
    the dashboard cannot report Connected while the push is refused.
    An unparseable remote yields an empty slot, which still exercises
    the fallback.
    """
    from vaibify.reproducibility.githubAuth import (
        fsKeyringSlotFromRemoteUrl, fsResolveToken,
    )
    try:
        return bool(fsResolveToken(fsKeyringSlotFromRemoteUrl(sRemoteUrl)))
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        return False


def _fdictDescribeGithubConnectivity(bContainerReaches, bHostCredential):
    """Build the connectivity payload naming whichever lane failed."""
    if not bContainerReaches:
        sMessage = "Cannot reach GitHub from the project repository"
    elif not bHostCredential:
        sMessage = _S_HOST_CREDENTIAL_MISSING_MESSAGE
    else:
        sMessage = "Connected"
    return {
        "bConnected": bContainerReaches and bHostCredential,
        "bContainerReachesGithub": bContainerReaches,
        "bHostCredentialAvailable": bHostCredential,
        "sMessage": sMessage,
    }


S_KEYRING_BACKEND_FAIL_MESSAGE = (
    "Container keyring backend is unavailable. Rebuild the container "
    "image to install keyrings.alt."
)


def _fbKeyringBackendHealthy(connectionDocker, sContainerId):
    """Return True if the container keyring backend is usable."""
    sCommand = fsPythonCommand(
        "import keyring",
        "print(type(keyring.get_keyring()).__module__, "
        "type(keyring.get_keyring()).__name__)",
        sContainerId,
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExitCode != 0:
        return False
    sCombined = (sOutput or "").strip().lower()
    if "keyring.backends.fail" in sCombined:
        return False
    if "fail.keyring" in sCombined.replace(" ", "."):
        return False
    return True


def _fdictProbeKeyringToken(
    connectionDocker, sContainerId, sTokenName,
):
    """Query the container keyring for a specific token."""
    sCommand = fsPythonCommand(
        "import keyring",
        f"t=keyring.get_password('vaibify',{repr(sTokenName)}); "
        f"print('ok' if t else 'missing')",
        sContainerId,
    )
    _, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    bConnected = "ok" in sOutput
    return {
        "bConnected": bConnected,
        "sMessage": "Connected" if bConnected else "Token not found",
    }


def _fdictCheckKeyring(
    connectionDocker, sContainerId, sTokenName,
):
    """Check if a keyring token exists inside the container."""
    if sTokenName not in SET_VALID_TOKEN_NAMES:
        raise ValueError(f"Invalid token name: {sTokenName}")
    if fbIsHostProject(sContainerId):
        return _fdictCheckHostKeyring(sTokenName)
    if not _fbKeyringBackendHealthy(connectionDocker, sContainerId):
        return {
            "bConnected": False,
            "sMessage": S_KEYRING_BACKEND_FAIL_MESSAGE,
        }
    return _fdictProbeKeyringToken(
        connectionDocker, sContainerId, sTokenName,
    )


def fnDeleteCredentialFromContainer(
    connectionDocker, sContainerId, sName,
):
    """Delete a credential from the container's keyring.

    Tolerates the case where the credential does not exist
    (keyring raises PasswordDeleteError); re-raises any other
    failure so the caller can surface it.
    """
    if sName not in SET_VALID_TOKEN_NAMES:
        raise ValueError(f"Invalid token name: {sName}")
    sCommand = fsPythonCommand(
        "import keyring; "
        "from keyring.errors import PasswordDeleteError",
        f"t=keyring.delete_password('vaibify', {repr(sName)})",
        sContainerId,
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExitCode == 0:
        return
    if "PasswordDeleteError" in (sOutput or ""):
        return
    raise RuntimeError(
        f"Keyring deletion failed: {(sOutput or '').strip()}"
    )


def fbValidateOverleafCredentials(
    connectionDocker, sContainerId, sProjectId,
):
    """Test Overleaf credentials on the host using the stored token.

    Runs ``git ls-remote`` against the Overleaf project from the host
    using a transient GIT_ASKPASS helper that reads the token out of
    the host OS keyring. ``connectionDocker`` and ``sContainerId`` are
    retained for signature compatibility but are unused: validation is
    entirely host-side now that tokens are stored on the host.

    Returns ``(bSuccess, sStderr)``.
    """
    fnValidateOverleafProjectId(sProjectId)
    return _ftValidateOverleafOnHost(sProjectId)


_S_OVERLEAF_HOST = "git.overleaf.com"


def _ftValidateOverleafOnHost(sProjectId):
    """Run git ls-remote from the host with a transient askpass helper."""
    from vaibify.config.secretManager import fbSecretExists
    if not fbSecretExists("overleaf_token", "keyring"):
        return (False, "No Overleaf token stored on host")
    sAskpass = fsWriteAskpassScript()
    try:
        return _ftRunHostLsRemote(sProjectId, sAskpass)
    finally:
        _fnRemovePath(sAskpass)


def _ftRunHostLsRemote(sProjectId, sAskpass):
    """Execute git ls-remote under the prepared askpass and env.

    Prepends ``LIST_GIT_CREDENTIAL_ISOLATION_CONFIG`` so this
    validation exercises ONLY the vaibify-managed token: without the
    reset, an ambient credential helper (e.g. macOS ``osxkeychain``)
    answers first and a mistyped or expired token "validates" against
    someone else's credential, then fails later in the container where
    no ambient helper exists.

    It needs no repository, so it asks from an empty directory with the
    hardening list; git would otherwise read the config of whatever
    repository the hub was started in, beside the token this carries.
    """
    import os
    from vaibify.reproducibility.gitHardening import (
        LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
        fcontextOpenHermeticGitInvocation,
    )
    from vaibify.reproducibility.overleafMirror import fsRedactStderr
    sUrl = f"https://{_S_OVERLEAF_HOST}/{sProjectId}"
    dictEnv = os.environ.copy()
    dictEnv["GIT_ASKPASS"] = sAskpass
    dictEnv["GIT_TERMINAL_PROMPT"] = "0"
    with fcontextOpenHermeticGitInvocation(dictEnv) as (
        sWorkingDirectory, dictHermetic,
    ):
        processResult = subprocess.run(
            ["git"]
            + list(LIST_GIT_CREDENTIAL_ISOLATION_CONFIG)
            + list(LIST_GIT_HARDENING_CONFIG)
            + ["ls-remote", "--", sUrl, "HEAD"],
            capture_output=True, text=True, env=dictHermetic,
            encoding="utf-8", cwd=sWorkingDirectory,
        )
    sDetail = fsRedactStderr((processResult.stderr or "").strip())
    return (processResult.returncode == 0, sDetail)


def _fnRemovePath(sPath):
    """Remove a file, ignoring absent paths."""
    import os
    try:
        os.remove(sPath)
    except FileNotFoundError:
        pass


def fbValidateZenodoToken(
    connectionDocker, sContainerId, sService="sandbox",
):
    """Return True iff the stored Zenodo token validates (detail dropped)."""
    return ftResultValidateZenodoToken(
        connectionDocker, sContainerId, sService,
    )[0]


def ftResultValidateZenodoToken(
    connectionDocker, sContainerId, sService="sandbox",
):
    """Probe the project's ACTIVE token on the host; return ``(bPass, sDetail)``.

    ``sService`` is the ZenodoClient service key (``"sandbox"`` or
    ``"zenodo"``). Validation hits the same instance the token was
    issued for; sandbox tokens are rejected by production and vice
    versa. The probe runs on the host with ``requests``, never inside
    the container, because the token never enters the container.

    On failure the detail names what actually failed -- no token stored
    (or one awaiting confirmation), a rejection by the named instance,
    or no answer from it -- because every one of those used to be
    reported as "check that the token has deposit scopes".
    """
    from .zenodoTokenAccess import (
        ZenodoTokenUnavailable, fsRequireActiveZenodoToken,
    )
    if sService not in _DICT_ZENODO_API_BASE:
        raise ValueError(f"Invalid Zenodo service: {sService}")
    try:
        sToken = fsRequireActiveZenodoToken(
            connectionDocker, sContainerId, sService,
        )
    except ZenodoTokenUnavailable as error:
        return (False, error.sMessage)
    dictProbe = zenodoClient.fdictProbeToken(sService, sToken)
    if dictProbe["sOutcome"] == zenodoClient.S_PROBE_ACCEPTED:
        return (True, "")
    return (False, fsDescribeZenodoValidationOutcome(dictProbe, sService))


def _fsZenodoHostForService(sService):
    """Return the API host a ZenodoClient service key names."""
    return "zenodo.org" if sService == "zenodo" else "sandbox.zenodo.org"


def fsDescribeZenodoValidationOutcome(dictProbe, sService):
    """Name the failure a host-side token probe records.

    Fixed sentences rather than the raw response: a rejection names the
    instance that rejected and the one the token may have been made
    on; an undetermined probe names the instance the host could not
    get an answer from, and says it is not a token problem.
    """
    sHost = _fsZenodoHostForService(sService)
    sOther = _fsZenodoHostForService(
        "sandbox" if sService == "zenodo" else "zenodo",
    )
    if dictProbe["sOutcome"] == zenodoClient.S_PROBE_REJECTED:
        return (
            f"{sHost} rejected the token. Check that the token was "
            f"created on {sHost} — not {sOther}; the two instances "
            "have separate accounts and tokens, and the instance "
            "selector above chooses which one is tested — and that "
            "it has the deposit:write and deposit:actions scopes."
        )
    return (
        f"This computer could not get an answer from {sHost} "
        f"({dictProbe.get('sDetail') or 'no detail'}) — a network or "
        "service problem, not a token problem. Nothing about the token "
        "was decided; try again."
    )


# ---------------------------------------------------------------------------
# Overleaf mirror dispatch (host-side)
# ---------------------------------------------------------------------------


def ftRefreshOverleafMirror(sProjectId):
    """Refresh the host-side partial-clone mirror for this project.

    Returns ``(bSuccess, dictOrMessage)``. On success the second item
    is a dict with ``sHeadSha``, ``iFileCount``, ``sRefreshedAt``. On
    failure it is a string with a classified error message suitable
    for surfacing to the UI.
    """
    fnValidateOverleafProjectId(sProjectId)
    sToken = _fsFetchOverleafToken()
    if not sToken:
        return (False, "No Overleaf token stored on host")
    from vaibify.reproducibility import overleafMirror
    try:
        dictResult = overleafMirror.fbRefreshMirror(sProjectId, sToken)
    except RuntimeError as error:
        return (False, str(error))
    return (True, dictResult)


def flistListOverleafTree(sProjectId):
    """Return mirror tree entries (no network call)."""
    fnValidateOverleafProjectId(sProjectId)
    from vaibify.reproducibility import overleafMirror
    return overleafMirror.flistListMirrorTree(sProjectId)


def _fdictComputeLocalDigests(listAbsPaths):
    """Compute git-blob digests for local files on disk (host)."""
    from vaibify.reproducibility import overleafMirror
    dictDigests = {}
    for sPath in listAbsPaths:
        try:
            dictDigests[sPath] = overleafMirror.fsComputeBlobSha(sPath)
        except OSError:
            continue
    return dictDigests


_S_DIGEST_SCRIPT = (
    "import hashlib,sys,os\n"
    "for p in sys.argv[1:]:\n"
    "    try:\n"
    "        with open(p,'rb') as f: b=f.read()\n"
    "        s=hashlib.sha1(b'blob '+str(len(b)).encode()"
    "+b'\\x00'+b).hexdigest()\n"
    "        print(s+' '+p)\n"
    "    except OSError:\n"
    "        print('- '+p)\n"
)


def fdictComputeContainerDigests(
    connectionDocker, sContainerId, listAbsPaths,
):
    """Compute git-blob digests for files inside the container.

    Runs a single docker exec that iterates the paths and prints one
    ``<sha> <path>`` line per file (or ``- <path>`` for unreadable ones).
    Unreadable entries are omitted from the returned dict.
    """
    if not listAbsPaths:
        return {}
    sScript = _S_DIGEST_SCRIPT
    listArgs = [fsShellQuote(p) for p in listAbsPaths]
    sCommand = (
        S_HUB_PYTHON_COMMAND + " -c " + fsShellQuote(sScript)
        + " " + " ".join(listArgs)
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand, sLane=S_EXEC_LANE_INTEGRITY_READ,
    )
    dictDigests = {}
    if iExit != 0:
        return dictDigests
    for sLine in (sOutput or "").splitlines():
        sStripped = sLine.strip()
        if not sStripped or sStripped.startswith("- "):
            continue
        iSpace = sStripped.find(" ")
        if iSpace <= 0:
            continue
        dictDigests[sStripped[iSpace + 1:]] = sStripped[:iSpace]
    return dictDigests


def fdictDiffOverleafPush(
    sProjectId, listAbsPaths, sTargetDirectory,
    connectionDocker=None, sContainerId="",
):
    """Classify a proposed push into new/overwrite/unchanged buckets.

    When ``connectionDocker`` + ``sContainerId`` are given, digests are
    computed inside the container (frontend-sent paths are
    container-absolute). Falls back to host-side digest computation
    when no docker context is provided.
    """
    fnValidateOverleafProjectId(sProjectId)
    from vaibify.reproducibility import overleafMirror
    if connectionDocker is not None and sContainerId:
        dictDigests = fdictComputeContainerDigests(
            connectionDocker, sContainerId, listAbsPaths,
        )
    else:
        dictDigests = _fdictComputeLocalDigests(listAbsPaths)
    return overleafMirror.fdictDiffAgainstMirror(
        sProjectId, dictDigests, sTargetDirectory,
    )


def flistCheckOverleafConflicts(
    sProjectId, listAbsPaths, sTargetDirectory, dictSyncStatus,
):
    """Return remote-versus-baseline conflicts for a proposed push."""
    fnValidateOverleafProjectId(sProjectId)
    from vaibify.reproducibility import overleafMirror
    return overleafMirror.flistDetectConflicts(
        sProjectId, listAbsPaths, sTargetDirectory, dictSyncStatus,
    )


def flistDetectOverleafCaseCollisions(
    sProjectId, listAbsPaths, sTargetDirectory,
):
    """Return per-file case-collision records for a proposed push.

    Thin dispatcher wrapper that delegates to the overleafMirror
    adapter. See ``overleafMirror.flistDetectCaseCollisions`` for
    the collision rule and record shape.
    """
    fnValidateOverleafProjectId(sProjectId)
    from vaibify.reproducibility import overleafMirror
    return overleafMirror.flistDetectCaseCollisions(
        sProjectId, listAbsPaths, sTargetDirectory,
    )


def _fbSafeDirectoryName(sDirectory):
    """Return True if a directory name is safe for shell embedding."""
    return bool(re.match(r'^[A-Za-z0-9_./ -]+$', sDirectory))


def fsBuildTestMarkerCheckCommand(
    listStepDirectories, sProjectRepoPath, sWorkflowSlug,
):
    """Build a docker exec command to read test markers and scan dirs.

    ``sWorkflowSlug`` scopes the marker scan to one workflow's subdir
    so two workflows in the same project repo don't surface each
    other's markers. An empty slug yields an empty marker map.
    """
    listSafe = [
        s for s in listStepDirectories if _fbSafeDirectoryName(s)
    ]
    sJsonDirs = json.dumps(listSafe)
    sScript = _fsBuildTestMarkerScript(
        sJsonDirs, sProjectRepoPath, sWorkflowSlug,
    )
    return S_HUB_PYTHON_COMMAND + " -c " + fsShellQuote(sScript)


def _fsBuildTestMarkerScript(
    sJsonDirs, sProjectRepoPath, sWorkflowSlug,
):
    """Build the Python script that reads markers and scans dirs.

    All string literals use double quotes so the script survives
    single-quote shell wrapping by fsShellQuote. ``sProjectRepoPath``
    and ``sWorkflowSlug`` are inlined via ``json.dumps`` so they
    become properly escaped Python string literals.
    """
    if sWorkflowSlug:
        sMarkerPath = (
            sProjectRepoPath + "/.vaibify/test_markers/" + sWorkflowSlug
        )
    else:
        sMarkerPath = ""
    sMarkerDirLiteral = json.dumps(sMarkerPath)
    sRepoLiteral = json.dumps(sProjectRepoPath)
    return (
        "import json, os\n"
        'R = {"markers": {}, "testFiles": {}, "missingConftest": []}\n'
        "mdir = " + sMarkerDirLiteral + "\n"
        "repo = " + sRepoLiteral + "\n"
        "if os.path.isdir(mdir):\n"
        "    for f in os.listdir(mdir):\n"
        '        if f.endswith(".json"):\n'
        "            try:\n"
        '                R["markers"][f] = json.load('
        "open(os.path.join(mdir, f)))\n"
        "            except Exception:\n"
        "                pass\n"
        "import re\n"
        "def _fsExtractHash(sPath):\n"
        "    try:\n"
        "        with open(sPath) as fileHandle:\n"
        "            for sLine in fileHandle:\n"
        '                m = re.match(r"^# vaibify-template-hash:'
        ' ([0-9a-f]+)", sLine)\n'
        "                if m: return m.group(1)\n"
        "    except Exception: pass\n"
        "    return None\n"
        "for d in json.loads(" + json.dumps(sJsonDirs) + "):\n"
        '    abs_d = os.path.join(repo, d) if repo and not '
        "os.path.isabs(d) else d\n"
        '    td = os.path.join(abs_d, "tests")\n'
        "    if not os.path.isdir(td):\n"
        "        continue\n"
        "    fs = [f for f in os.listdir(td)"
        ' if f.startswith("test_") and f.endswith(".py")]\n'
        "    mt = {f: os.path.getmtime(os.path.join(td, f))"
        " for f in fs"
        " if os.path.isfile(os.path.join(td, f))}\n"
        "    dh = {}\n"
        "    for f in fs:\n"
        "        h = _fsExtractHash(os.path.join(td, f))\n"
        "        if h: dh[f] = h\n"
        '    R["testFiles"][d] = '
        '{"listFiles": fs, "dictMtimes": mt, "dictHashes": dh}\n'
        '    sConftestPath = os.path.join(td, "conftest.py")\n'
        "    if not os.path.isfile(sConftestPath):\n"
        '        R["missingConftest"].append(d)\n'
        "    else:\n"
        "        try:\n"
        "            with open(sConftestPath) as fileHandle:\n"
        "                sConftestSource = fileHandle.read()\n"
        "        except Exception:\n"
        "            sConftestSource = \"\"\n"
        '        if ("_PROJECT_REPO" not in sConftestSource\n'
        '            and "# vaibify-conftest-loader:" not in sConftestSource):\n'
        '            R["missingConftest"].append(d)\n'
        "print(json.dumps(R))\n"
    )


def fdictParseTestMarkerOutput(sOutput):
    """Parse the JSON output from the test marker check command."""
    sStripped = (sOutput or "").strip()
    if not sStripped:
        return {"markers": {}, "testFiles": {}, "missingConftest": []}
    try:
        return json.loads(sStripped)
    except (json.JSONDecodeError, ValueError):
        return {"markers": {}, "testFiles": {}, "missingConftest": []}


def _flistBuildDagEdges(dictWorkflow, dictCachedDeps=None):
    """Build DAG edges from explicit, implicit, and cached deps."""
    dictDirect = workflowManager.fdictBuildDirectDependencies(
        dictWorkflow
    )
    if dictCachedDeps:
        for iUp, setDown in dictCachedDeps.items():
            dictDirect.setdefault(iUp, set()).update(setDown)
    listEdgeLines = []
    for iUpstream in sorted(dictDirect):
        for iDown in sorted(dictDirect[iUpstream]):
            listEdgeLines.append(
                f"  step{iUpstream + 1} -> step{iDown + 1};"
            )
    return listEdgeLines


def fsBuildDagDot(dictWorkflow, dictCachedDeps=None):
    """Build a Graphviz DOT string from workflow step references."""
    listLines = [
        "digraph workflow {",
        "  rankdir=LR;",
        '  node [style=filled fillcolor="#282840" '
        'fontcolor="#e0e0e8" color="#3a3a58" fontname="Arial"];',
        '  edge [color="#13aed5"];',
    ]
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex, dictStep in enumerate(listSteps):
        sLabel = dictStep.get("sName", f"Step {iIndex + 1}")
        sNodeId = f"step{iIndex + 1}"
        sSafeLabel = sLabel.replace('"', '\\"')
        listLines.append(f'  {sNodeId} [label="{sSafeLabel}"];')
    listLines.extend(
        _flistBuildDagEdges(dictWorkflow, dictCachedDeps))
    listLines.append("}")
    return "\n".join(listLines)


def ftResultGenerateDagSvg(
    connectionDocker, sContainerId, dictWorkflow,
    dictCachedDeps=None,
):
    """Render the DAG to SVG where this resource may write, return bytes."""
    return _ftRenderDagThroughDot(
        connectionDocker, sContainerId, dictWorkflow, "svg",
        dictCachedDeps,
    )


DICT_DAG_MEDIA_TYPES = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "pdf": "application/pdf",
}


def ftResultExportDag(
    connectionDocker, sContainerId, dictWorkflow, sFormat,
    dictCachedDeps=None,
):
    """Export DAG in the requested format (svg, png, or pdf)."""
    sFormat = sFormat.lower().lstrip(".")
    if sFormat not in DICT_DAG_MEDIA_TYPES:
        return (1, f"Unsupported DAG format: {sFormat}")
    return _ftRenderDagThroughDot(
        connectionDocker, sContainerId, dictWorkflow, sFormat,
        dictCachedDeps,
    )


def _ftRenderDagThroughDot(
    connectionDocker, sContainerId, dictWorkflow, sFormat,
    dictCachedDeps,
):
    """Write the DOT source, run ``dot`` over it, return the rendered bytes.

    The scratch paths are resolved per resource: a container writes to
    ``/tmp``, a host project to its own operation directory under the
    host-diagnostics subtree, which is the only ephemeral root the host
    path guard admits. The operation name also makes the file names
    unique, so two exports requested at once no longer read and
    overwrite one ``/tmp/_vaibify_dag.dot``.

    ``dot`` is graphviz, which the container image installs and a
    researcher's machine may not. A missing binary comes back as the
    shell's own non-zero result and reaches the caller as the failure
    it is, rather than an empty diagram.
    """
    from . import projectRoots
    sOperationName = "dag-" + uuid.uuid4().hex[:12]
    sScratchDirectory = projectRoots.fsResolveScratchDirectory(
        sContainerId, sOperationName, "/tmp",
    )
    sDotPath = posixpath.join(sScratchDirectory, sOperationName + ".dot")
    sOutPath = posixpath.join(
        sScratchDirectory, sOperationName + "." + sFormat,
    )
    connectionDocker.fnWriteFile(
        sContainerId, sDotPath,
        fsBuildDagDot(dictWorkflow, dictCachedDeps).encode("utf-8"),
    )
    sPersistPath = posixpath.join(
        projectRoots.fsResolveProjectRoot(sContainerId, "/workspace"),
        ".vaibify", f"dag.{sFormat}",
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"dot -T{sFormat} {fsShellQuote(sDotPath)} "
        f"-o {fsShellQuote(sOutPath)} && "
        f"cp {fsShellQuote(sOutPath)} {fsShellQuote(sPersistPath)}",
    )
    if iExitCode != 0:
        return (iExitCode, sOutput)
    return (0, connectionDocker.fbaFetchFile(sContainerId, sOutPath))


def ftResultArchiveProject(
    connectionDocker, sContainerId, dictWorkflow,
):
    """NOT CALLED (kept by decision): publish a sandbox draft with no files."""
    import json
    sWorkflowJson = json.dumps(dictWorkflow)
    sImport = (
        "import json; "
        "from vaibify.reproducibility.dataArchiver import ("
        "fdictBuildZenodoMetadata, fsGenerateArchiveReadme); "
        "from vaibify.reproducibility.zenodoClient import ZenodoClient"
    )
    sCall = (
        f"dictWf=json.loads({repr(sWorkflowJson)}); "
        f"c=ZenodoClient('sandbox'); "
        f"d=c.fdictCreateDraft(); "
        f"dictMeta=fdictBuildZenodoMetadata(dictWf); "
        f"c.fnSetMetadata(d['id'], dictMeta); "
        f"c.fnPublishDraft(d['id']); "
        f"print('Published:', d['id'])"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall, sContainerId)
    )


_FROZENSET_OVERLEAF_EXTENSIONS = frozenset({
    ".tex", ".pdf", ".png", ".jpg", ".jpeg",
    ".eps", ".svg", ".bib",
})


def flistCollectZenodoArchiveCandidates(
    dictWorkflow, dictSyncStatus, dictVars, sWorkflowRoot, filesRepo,
    fnPathsExist=None,
):
    """Collect the Zenodo push-modal candidates: the PUBLICATION union.

    The L2/L3 Zenodo gates compare the full union — the project
    definition, the AI declaration, declared inputs, and the
    reproducibility envelope — but the candidate list carried only
    outputs, scripts and tests, so a researcher could never publish a
    deposit the gates would accept: the gates compared files the
    modal offered no way to upload, and project.json, the declaration
    and the envelope sat permanently diverged (live, 2026-08-27).
    Extras are existence-filtered through ``fnPathsExist`` (batched,
    best-effort: a probe failure includes them all and the archive
    pre-flight names any that are truly absent).
    """
    from vaibify.reproducibility import publicationScope
    listFiles = flistCollectOutputFiles(
        dictWorkflow, dictSyncStatus, dictVars, "zenodo",
        sWorkflowRoot,
    )
    setListed = {dictFile.get("sPath") for dictFile in listFiles}
    listExtras = []
    for sPath in publicationScope.flistCollectComparisonPaths(
        dictWorkflow, filesRepo,
    ):
        sAbsolute = (
            posixpath.join(sWorkflowRoot, sPath)
            if sWorkflowRoot and not sPath.startswith("/") else sPath
        )
        if sAbsolute not in setListed:
            listExtras.append(sAbsolute)
    listExtras = _flistFilterToExistingPaths(listExtras, fnPathsExist)
    for sAbsolute in listExtras:
        listFiles.append({
            "sPath": sAbsolute,
            "sCategory": "archive",
            "dictSync": _fdictLookupSyncForPath(
                sAbsolute, dictSyncStatus, sWorkflowRoot,
            ),
        })
    return listFiles


def _flistFilterToExistingPaths(listPaths, fnPathsExist):
    """Drop paths the batched probe says are absent; keep all on failure."""
    if fnPathsExist is None or not listPaths:
        return listPaths
    try:
        listFlags = fnPathsExist(listPaths)
    except OSError:
        return listPaths
    return [
        sPath for sPath, bExists in zip(listPaths, listFlags)
        if bExists
    ]


def flistCollectOutputFiles(
    dictWorkflow, dictSyncStatus, dictVars=None, sService=None,
    sWorkflowRoot=None,
):
    """Collect output files with resolved paths and service filtering.

    When ``sWorkflowRoot`` is provided, each path is made absolute by
    joining the workflow root with the step directory and the
    variable-resolved file path. The container CLI expects absolute
    paths; relative paths resolve against the container's WORKDIR
    which is rarely the workflow root.
    """
    listFiles = _flistCollectRawOutputFiles(
        dictWorkflow, dictSyncStatus, dictVars or {},
        sWorkflowRoot or "",
    )
    if sService == "overleaf":
        return _flistFilterByExtension(
            listFiles, _FROZENSET_OVERLEAF_EXTENSIONS)
    return listFiles


def _flistCollectRawOutputFiles(
    dictWorkflow, dictSyncStatus, dictVars, sWorkflowRoot,
):
    """Collect raw (resolved) output-file entries across every step.

    Beyond declared data and plot outputs, every step's scripts are
    offered, plus its test files and test standards unless the
    workflow opts out via ``bArchiveTests`` (absent means True) —
    mirroring ``manifestWriter._flistCollectManifestPaths`` so the
    push-modal candidate list and the archived manifest can never
    silently disagree.
    """
    listFiles = []
    bArchiveTests = fbWorkflowArchivesTests(dictWorkflow)
    for dictStep in dictWorkflow.get("listSteps", []):
        _fnAppendStepOutputFiles(
            dictStep, dictSyncStatus, dictVars,
            sWorkflowRoot, listFiles,
        )
        _fnAppendStepArchivalFiles(
            dictStep, bArchiveTests, dictSyncStatus,
            dictVars, sWorkflowRoot, listFiles,
        )
    return listFiles


def _fdictStepArchivalPathsByCategory(dictStep, bArchiveTests):
    """Map archival category to one step's repo-relative paths."""
    dictPathsByCategory = {
        "script": flistStepScriptRepoPaths(dictStep),
    }
    if bArchiveTests:
        dictPathsByCategory["test"] = (
            flistStepTestFileRepoPaths(dictStep))
        dictPathsByCategory["standards"] = (
            flistStepStandardsRepoPaths(dictStep))
    return dictPathsByCategory


def _fnAppendStepArchivalFiles(
    dictStep, bArchiveTests, dictSyncStatus, dictVars,
    sWorkflowRoot, listFiles,
):
    """Append one step's script, test-file, and standards entries."""
    setSeen = {dictFile["sPath"] for dictFile in listFiles}
    dictPathsByCategory = _fdictStepArchivalPathsByCategory(
        dictStep, bArchiveTests,
    )
    for sCategory, listRepoPaths in dictPathsByCategory.items():
        for sRepoPath in listRepoPaths:
            _fnAppendArchivalEntry(
                sRepoPath, sCategory, dictSyncStatus, dictVars,
                sWorkflowRoot, listFiles, setSeen,
            )


def _fnAppendArchivalEntry(
    sRepoPath, sCategory, dictSyncStatus, dictVars,
    sWorkflowRoot, listFiles, setSeen,
):
    """Append one resolved archival entry, skipping duplicates."""
    sResolved = workflowManager.fsResolveVariables(sRepoPath, dictVars)
    if "{" in sResolved:
        return
    sAbsolute = _fsResolveAbsoluteStepPath("", sResolved, sWorkflowRoot)
    if sAbsolute in setSeen:
        return
    setSeen.add(sAbsolute)
    listFiles.append({
        "sPath": sAbsolute,
        "sCategory": sCategory,
        "dictSync": _fdictLookupSyncForPath(
            sAbsolute, dictSyncStatus, sWorkflowRoot,
        ),
    })


def _fnAppendStepOutputFiles(
    dictStep, dictSyncStatus, dictVars, sWorkflowRoot, listFiles,
):
    """Append one step's data and plot files with resolved paths."""
    for sKey in ("saOutputDataFiles", "saPlotFiles"):
        for sFile in dictStep.get(sKey, []):
            listFiles.append(_fdictBuildOutputEntry(
                dictStep, sKey, sFile, dictSyncStatus,
                dictVars, sWorkflowRoot,
            ))


def _fsResolveAbsoluteStepPath(
    sStepDir, sResolvedFile, sWorkflowRoot,
):
    """Join workflow root + step dir + file into an absolute path."""
    import posixpath
    if sResolvedFile.startswith("/"):
        return sResolvedFile
    if not sStepDir.startswith("/") and sWorkflowRoot:
        sStepDir = posixpath.join(sWorkflowRoot, sStepDir)
    if sStepDir:
        return posixpath.join(sStepDir, sResolvedFile)
    return sResolvedFile


def _fdictLookupSyncForPath(sAbsolute, dictSyncStatus, sWorkflowRoot):
    """Return the sync-status entry for one resolved path."""
    return workflowManager.fdictLookupSyncEntry(
        dictSyncStatus,
        workflowManager.fsToSyncStatusKey(sAbsolute, sWorkflowRoot),
        sWorkflowRoot,
    )


def _fdictBuildOutputEntry(
    dictStep, sKey, sFile, dictSyncStatus, dictVars, sWorkflowRoot,
):
    """Build one resolved output-file entry for the sync modal."""
    sResolved = workflowManager.fsResolveVariables(sFile, dictVars)
    sStepDir = dictStep.get("sDirectory", "")
    sAbsolute = _fsResolveAbsoluteStepPath(
        sStepDir, sResolved, sWorkflowRoot,
    )
    sCategory = "archive"
    if sKey == "saPlotFiles":
        sCategory = workflowManager.fsGetPlotCategory(
            dictStep, sFile)
    return {
        "sPath": sAbsolute,
        "sCategory": sCategory,
        "dictSync": _fdictLookupSyncForPath(
            sAbsolute, dictSyncStatus, sWorkflowRoot,
        ),
    }


def _flistFilterByExtension(listFiles, frozensetExtensions):
    """Return only entries whose resolved path ends with an allowed ext."""
    listFiltered = []
    for dictFile in listFiles:
        sPath = dictFile.get("sPath", "")
        iDot = sPath.rfind(".")
        if iDot < 0:
            continue
        if sPath[iDot:].lower() in frozensetExtensions:
            listFiltered.append(dictFile)
    return listFiltered


# Re-export file integrity functions for backward compatibility.
# Canonical implementations live in fileIntegrity.py.
from .fileIntegrity import (  # noqa: F401
    _fsNormalizePath,
    flistExtractAllScriptPaths,
)
