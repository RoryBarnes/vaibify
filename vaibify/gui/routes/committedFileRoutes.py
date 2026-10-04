"""Pinned files that differ from the last commit, and restoring them in a container.

The remedy the Level 3 readiness gate needs on a clone a reader has
re-run outside the author's environment. The gate refuses because the
manifest misdescribes the files it names; for a manifest someone else
committed, regenerating it would replace the author's claim with the
reader's own bytes. Restoring the committed versions answers the gate
without that -- and it runs only inside a container, whose copy of the
project is a copy, so nothing on the researcher's machine is touched.

Both routes run git through the exec primitive, so both hold the drain
for the worker's life. A refusal decided before anything was written
travels back as a value and is raised here, so it never quarantines
the container.
"""

__all__ = ["fnRegisterAll"]

from fastapi import HTTPException, Request

from ...reproducibility import committedFiles, gitEvidence
from ..actionCatalog import ffnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    ffilesForWorkflow,
    fgenericRunWorkerUnderTheDrain,
)
from ..routeScope import S_CARRIER_MODE_B_LOCK_HELD, ffnDeclareCarrierMode


def _fdictDescribeDifferences(filesRepo):
    """Return the differing pinned paths, whose manifest it is, and whether a restore can run."""
    try:
        listDiffering = committedFiles.flistPinnedPathsDifferingFromHead(
            filesRepo,
        )
    except committedFiles.CommittedFilesUndeterminedError as error:
        raise HTTPException(409, detail={"sMessage": (
            "Git could not say which files differ from the last "
            f"commit: {error}"
        )}) from error
    return {
        "listDifferingPaths": listDiffering,
        "sManifestOwnership": gitEvidence.fsManifestOwnershipForRepoFiles(
            filesRepo,
        ),
        "bRestoreRunsInContainer": filesRepo.fsLocalRootOrNone() is None,
    }


def _fdictRestoreDifferences(filesRepo):
    """Restore every pinned path that differs; return what was restored.

    A project on this machine is refused by the restore itself, before
    git is asked anything, and reaches the caller as this 409.
    """
    try:
        listRestored = committedFiles.flistRestorePinnedPathsFromHead(
            filesRepo,
            committedFiles.flistPinnedPathsDifferingFromHead(filesRepo),
        )
    except committedFiles.CommittedFilesUndeterminedError as error:
        raise HTTPException(409, detail={"sMessage": (
            f"The committed files were not restored: {error}"
        )}) from error
    return {"listRestoredPaths": listRestored}


async def _fdictRunUnderTheDrain(
    dictCtx, sContainerId, requestHttp, fdictEffect, sOperationTarget,
):
    """Resolve the project's repository, then run one effect under the drain."""
    dictCtx["require"](sContainerId)
    dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)

    def fdictCarryingWorker(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: fdictEffect(filesRepo),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictCarryingWorker, sOperationTarget, requestHttp,
    )


def _fnRegisterListDifferences(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/committed-file-differences."""

    @ffnAgentAction("list-committed-file-differences")
    @app.get("/api/workflow/{sContainerId}/committed-file-differences")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictListCommittedFileDifferences(
        sContainerId: str, requestHttp: Request,
    ):
        return await _fdictRunUnderTheDrain(
            dictCtx, sContainerId, requestHttp, _fdictDescribeDifferences,
            "committed-file-differences",
        )


def _fnRegisterRestoreDifferences(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/restore-committed-files."""

    @ffnAgentAction("restore-committed-files")
    @app.post("/api/workflow/{sContainerId}/restore-committed-files")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictRestoreCommittedFiles(
        sContainerId: str, requestHttp: Request,
    ):
        return await _fdictRunUnderTheDrain(
            dictCtx, sContainerId, requestHttp, _fdictRestoreDifferences,
            "restore-committed-files",
        )


def fnRegisterAll(app, dictCtx):
    """Register the committed-file routes."""
    _fnRegisterListDifferences(app, dictCtx)
    _fnRegisterRestoreDifferences(app, dictCtx)
