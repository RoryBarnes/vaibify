"""The host-side git invocations vaibify makes, as callable probe cells.

Each entry calls the REAL vaibify function (or renders and runs the
real typed-read program text) against a repository the harness built,
so a probe measures what vaibify does rather than what a copy of its
argument list would do. A shim connection stands in for the host
connection's shell leg: it runs the command string the vaibify
function composed with ``bash -c`` on this machine, which is what the
host leg does after its gating and journaling.
"""

import os
import subprocess
import sys

from tests.hostGitProbeHarness import fnModifyTrackedFile
# Bound at import, before the suite-wide fixture replaces the module's
# probe with a stub that never runs git: a probe of the stub measures
# nothing.
from vaibify.cli.repositoryPreflight import (
    RemoteUnreachableError, fdictProbeRepositoryBranch,
)


class LocalShellConnection:
    """Runs a composed command string on this machine, as host mode does."""

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        """Return ``(exit code, combined output)`` like the real adapters."""
        processShell = subprocess.run(
            ["bash", "-c", sCommand], cwd=sWorkdir,
            capture_output=True, text=True, timeout=60,
        )
        return processShell.returncode, processShell.stdout + processShell.stderr


def _fnRunTypedReadProgram(sOperation, sRepo, bBatched):
    """Run the real typed-read program for one operation on a path."""
    from vaibify.docker import dockerConnection
    sTemplate = dockerConnection._DICT_TYPED_READ_PROGRAMS[sOperation]
    sLiteral = dockerConnection._fsTypedReadPathLiteral(
        [sRepo] if bBatched else sRepo
    )
    sProgram = sTemplate.replace(
        dockerConnection._S_TYPED_READ_PATH_SLOT, sLiteral,
    )
    subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True,
        text=True, timeout=120,
    )


def fnCallGitStatusWorkspace(sRepo):
    from vaibify.gui import gitStatus
    gitStatus.fdictGitStatusForWorkspace(sRepo)


def fnCallReadOriginUrl(sRepo):
    from vaibify.gui import gitStatus
    gitStatus.fsReadOriginUrl(sRepo)


def fnCallEvidenceManifestDiffersWhenUnchanged(sRepo):
    from vaibify.cli.commandReproduce import _ffnBuildHostGitRunner
    from vaibify.reproducibility import gitEvidence
    gitEvidence.fbManifestDiffersFromHead(_ffnBuildHostGitRunner(sRepo))


def fnCallEvidenceManifestDiffersWhenChanged(sRepo):
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileManifest:
        fileManifest.write("changed\n")
    fnCallEvidenceManifestDiffersWhenUnchanged(sRepo)


def fnCallEvidenceForeignManifest(sRepo):
    from vaibify.cli.commandReproduce import _ffnBuildHostGitRunner
    from vaibify.reproducibility import gitEvidence
    gitEvidence.fbRepositoryCarriesForeignManifest(
        _ffnBuildHostGitRunner(sRepo)
    )


def fnCallReproductionRefuseUnlessClean(sRepo):
    from vaibify.reproducibility import reproductionSource
    try:
        reproductionSource._fnRefuseUnlessClean(sRepo)
    except reproductionSource.ReproductionSourceRefusedError:
        pass


def fnCallReproductionRepositoryRoot(sRepo):
    from vaibify.reproducibility import reproductionSource
    reproductionSource._fnRefuseUnlessRepositoryRoot(sRepo)


def fnCallReproductionShowCommitted(sRepo):
    from vaibify.reproducibility import reproductionSource
    reproductionSource.fsReadCommittedFileOrNone(sRepo, "data.txt")


def fnCallReproductionLocalClone(sRepo):
    from vaibify.reproducibility import reproductionSource
    sStaging = sRepo + "Staging"
    os.makedirs(sStaging, exist_ok=True)
    try:
        reproductionSource._fdictMaterializeLocalClone(sRepo, sStaging)
    except reproductionSource.ReproductionSourceRefusedError:
        pass


def fnCallTypedReadRepoStatus(sRepo):
    _fnRunTypedReadProgram("gitRepoStatus", sRepo, True)


def fnCallTypedReadTrackedIdentities(sRepo):
    _fnRunTypedReadProgram("gitTrackedIdentities", sRepo, False)


def fnCallTypedReadUntrackedInventory(sRepo):
    _fnRunTypedReadProgram("gitUntrackedInventory", sRepo, False)


def fnCallTypedReadWorktreeIdentities(sRepo):
    _fnRunTypedReadProgram("gitWorktreeIdentities", sRepo, False)


def fnCallBuildRemoteAndBranch(sRepo):
    from vaibify.cli import commandBuild
    commandBuild._fsGitRemoteUrl(sRepo)
    commandBuild._fsGitBranch(sRepo)


def fnCallRepositoryPreflightProbe(sRepo):
    """Ask a repository's own origin URL, with the hub started inside it.

    The probe runs from whatever directory vaibify was launched in; a
    repository there carries its own config. The URL asked is the
    cell's ``origin`` (an ssh, git or http address for the network
    mechanisms), so each mechanism has the address that would reach it.
    """
    sOrigin = subprocess.run(
        ["git", "config", "remote.origin.url"], cwd=sRepo,
        capture_output=True, text=True,
    ).stdout.strip()
    sPreviousDirectory = os.getcwd()
    os.chdir(sRepo)
    try:
        fdictProbeRepositoryBranch(sOrigin, "main")
    except RemoteUnreachableError:
        pass
    finally:
        os.chdir(sPreviousDirectory)


def fnCallContainerGitStatus(sRepo):
    from vaibify.gui import containerGit
    containerGit.fdictGitStatusInContainer(
        LocalShellConnection(), "probe", sRepo,
    )


def fnCallContainerGitAdd(sRepo):
    from vaibify.gui import containerGit
    fnModifyTrackedFile(sRepo)
    containerGit.ftResultGitAddInContainer(
        LocalShellConnection(), "probe", ["data.txt"], sRepo,
    )


def fnCallContainerGitCommit(sRepo):
    from vaibify.gui import containerGit
    fnModifyTrackedFile(sRepo)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
         "add", "-A"],
        cwd=sRepo, capture_output=True,
    )
    containerGit.ftResultGitCommitInContainer(
        LocalShellConnection(), "probe", "probe commit", sRepo,
    )


def fnCallContainerGitFetch(sRepo):
    from vaibify.gui import containerGit
    containerGit.ftResultGitFetchInContainer(
        LocalShellConnection(), "probe", sRepo,
    )


def fnCallContainerMergePreview(sRepo):
    from vaibify.gui import containerGit
    containerGit.ftResultMergePreviewInContainer(
        LocalShellConnection(), "probe", sRepo,
    )


def fnCallContainerMergeUpstream(sRepo):
    from vaibify.gui import containerGit
    containerGit.ftResultGitMergeUpstreamInContainer(
        LocalShellConnection(), "probe", sRepo,
    )


def fnCallContainerRemoteHeads(sRepo):
    from vaibify.gui import containerGit
    containerGit.fdictRemoteHeadsInContainer(
        LocalShellConnection(), "probe", sRepo,
    )


def fnCallSyncDispatcherPublishSuffix(sRepo):
    from vaibify.gui import syncDispatcher
    fnModifyTrackedFile(sRepo)
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
         "add", "-A"],
        cwd=sRepo, capture_output=True,
    )
    sCommand = (
        "cd " + sRepo + " && "
        + syncDispatcher._fsComposePublishSuffix(
            syncDispatcher._fsGithubHardeningFlags(), "probe publish",
        )
    )
    LocalShellConnection().ftResultExecuteCommand("probe", sCommand)


DICT_PROBE_CALLS = {
    "gitStatusFdictGitStatusForWorkspace": fnCallGitStatusWorkspace,
    "gitStatusFsReadOriginUrl": fnCallReadOriginUrl,
    "gitEvidenceFbManifestDiffersFromHeadWhenUnchanged":
        fnCallEvidenceManifestDiffersWhenUnchanged,
    "gitEvidenceFbManifestDiffersFromHeadWhenChanged":
        fnCallEvidenceManifestDiffersWhenChanged,
    "gitEvidenceFbRepositoryCarriesForeignManifest":
        fnCallEvidenceForeignManifest,
    "reproductionSourceRefuseUnlessClean": fnCallReproductionRefuseUnlessClean,
    "reproductionSourceRepositoryRoot": fnCallReproductionRepositoryRoot,
    "reproductionSourceShowCommitted": fnCallReproductionShowCommitted,
    "reproductionSourceLocalClone": fnCallReproductionLocalClone,
    "typedReadGitRepoStatus": fnCallTypedReadRepoStatus,
    "typedReadGitTrackedIdentities": fnCallTypedReadTrackedIdentities,
    "typedReadGitUntrackedInventory": fnCallTypedReadUntrackedInventory,
    "typedReadGitWorktreeIdentities": fnCallTypedReadWorktreeIdentities,
    "commandBuildRemoteAndBranch": fnCallBuildRemoteAndBranch,
    "repositoryPreflightProbeFromARepository":
        fnCallRepositoryPreflightProbe,
    "containerGitStatusViaShell": fnCallContainerGitStatus,
    "containerGitAddViaShell": fnCallContainerGitAdd,
    "containerGitCommitViaShell": fnCallContainerGitCommit,
    "containerGitFetchViaShell": fnCallContainerGitFetch,
    "containerGitMergePreviewViaShell": fnCallContainerMergePreview,
    "containerGitMergeUpstreamViaShell": fnCallContainerMergeUpstream,
    "containerGitRemoteHeadsViaShell": fnCallContainerRemoteHeads,
    "syncDispatcherPublishSuffix": fnCallSyncDispatcherPublishSuffix,
}
