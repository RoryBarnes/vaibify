"""Pinned files that differ from the last commit, and putting them back.

A reader who re-runs a published project outside the author's pinned
environment changes, by design, files the author's manifest pins: the
fitted numbers differ in their last digits and the figures carry
another matplotlib. That is the lesson the rerun exists to teach, and
it leaves the clone in a state the Level 3 readiness gate refuses --
the manifest now misdescribes the files it names. The gate is right.
What the reader needs is a way back to the committed bytes that does
not cost them their own run.

So the restore runs ONLY where the repository is not the researcher's
own directory: inside a container, whose copy of the project is a copy.
The host clone keeps whatever the reader produced. An adapter whose
root is a local host path is refused before git is asked anything.

"The committed version" means HEAD, and the pinned set is the one the
manifest AT HEAD names, plus the manifest itself -- never the manifest
on disk, which a reader may already have regenerated into a
description of their own outputs. Every git question is settled by
git's exit code, and any answer git could not give raises
``CommittedFilesUndeterminedError`` rather than reading as "nothing
differs".
"""

__all__ = [
    "CommittedFilesUndeterminedError",
    "flistPinnedPathsDifferingFromHead",
    "flistRestorePinnedPathsFromHead",
]

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.docker.execArgumentBudget import flistBatchPathsForOneExec
from vaibify.reproducibility import gitEvidence
from vaibify.reproducibility.manifestWriter import flistParseManifestText

_S_MANIFEST_RELATIVE_PATH = "MANIFEST.sha256"


class CommittedFilesUndeterminedError(Exception):
    """Git could not say which pinned files differ, or could not restore them."""


def flistPinnedPathsDifferingFromHead(filesRepo):
    """Return pinned paths whose working copy differs from HEAD, in git's order.

    Pinned means named by the manifest HEAD holds, or the manifest
    itself. A file deleted from the working tree differs. A repository
    whose HEAD tracks no manifest pins nothing and answers ``[]``.
    """
    ftRunGit = gitEvidence.ffnBuildGitRunnerForRepoFiles(filesRepo)
    try:
        if not gitEvidence.fbHeadTracksManifest(ftRunGit):
            return []
    except gitEvidence.RecordKindUndeterminedError as error:
        raise CommittedFilesUndeterminedError(str(error)) from error
    setPinnedPaths = _fsetPathsPinnedAtHead(ftRunGit)
    return [
        sPath for sPath in _flistTrackedPathsChangedSinceHead(ftRunGit)
        if sPath in setPinnedPaths
    ]


def flistRestorePinnedPathsFromHead(filesRepo, listPathsToRestore):
    """Write HEAD's version of each path into a CONTAINER copy; return them.

    Refuses a host adapter: this never touches the researcher's own
    directory. Only paths that still differ from HEAD are written, so a
    stale list cannot restore a file the caller did not see differ.
    Afterwards the question is asked again, and a path that still
    differs raises -- a restore that did not take must not be reported
    as one that did.
    """
    if filesRepo.fsLocalRootOrNone() is not None:
        raise CommittedFilesUndeterminedError(
            "Committed files are restored only inside a container; "
            "this repository is a directory on this machine, and "
            "restoring there would discard the researcher's own files."
        )
    setStillDiffering = set(flistPinnedPathsDifferingFromHead(filesRepo))
    listRestoring = [
        sPath for sPath in listPathsToRestore if sPath in setStillDiffering
    ]
    ftRunGit = gitEvidence.ffnBuildGitRunnerForRepoFiles(filesRepo)
    for listBatch in flistBatchPathsForOneExec(listRestoring):
        _fnRestoreOneBatch(ftRunGit, listBatch)
    listUnrestored = sorted(
        set(listRestoring) & set(flistPinnedPathsDifferingFromHead(filesRepo))
    )
    if listUnrestored:
        raise CommittedFilesUndeterminedError(
            "git reported success but these files still differ from "
            "the last commit: " + ", ".join(listUnrestored)
        )
    return listRestoring


def _fsetPathsPinnedAtHead(ftRunGit):
    """Return the manifest's paths at HEAD, plus the manifest itself."""
    iExitCode, sManifestText = _ftRunGitOrRaise(
        ftRunGit, ["show", "HEAD:" + _S_MANIFEST_RELATIVE_PATH],
        "read the manifest committed at HEAD",
    )
    if iExitCode != 0:
        raise CommittedFilesUndeterminedError(
            f"git could not read the manifest at HEAD (exit {iExitCode})"
        )
    try:
        listEntries = flistParseManifestText(sManifestText)
    except ValueError as error:
        raise CommittedFilesUndeterminedError(
            f"the manifest at HEAD could not be parsed: {error}"
        ) from error
    setPinnedPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    setPinnedPaths.add(_S_MANIFEST_RELATIVE_PATH)
    return setPinnedPaths


def _flistTrackedPathsChangedSinceHead(ftRunGit):
    """Return tracked paths whose working copy differs from HEAD.

    NUL-separated, so a path git would otherwise C-quote is compared
    as the manifest spells it rather than silently missing the match.
    """
    iExitCode, sOutput = _ftRunGitOrRaise(
        ftRunGit,
        ["diff", "--no-renames", "--name-only", "-z", "HEAD"],
        "list the files that differ from HEAD",
    )
    if iExitCode != 0:
        raise CommittedFilesUndeterminedError(
            f"git could not compare the working copy with HEAD "
            f"(exit {iExitCode})"
        )
    return [sPath for sPath in sOutput.split("\0") if sPath]


def _fnRestoreOneBatch(ftRunGit, listBatch):
    """Restore one exec's worth of paths from HEAD, working tree only.

    ``--literal-pathspecs`` because these names come from a cloned
    repository: a path spelled like pathspec magic must be the file of
    that name, never a pattern matching others.
    """
    iExitCode, _sOutput = _ftRunGitOrRaise(
        ftRunGit,
        ["--literal-pathspecs", "restore", "--source=HEAD",
         "--worktree", "--", *listBatch],
        "restore the committed files",
    )
    if iExitCode != 0:
        raise CommittedFilesUndeterminedError(
            f"git could not restore the committed files (exit {iExitCode})"
        )


def _ftRunGitOrRaise(ftRunGit, listArguments, sPurpose):
    """Run git; a runner that cannot run it is undetermined, never empty."""
    try:
        iExitCode, sOutput = ftRunGit(listArguments)
    except Exception as error:  # noqa: BLE001 -- turned into a refusal
        fnReRaiseControlPlaneRefusal(error)
        raise CommittedFilesUndeterminedError(
            f"git could not be asked to {sPurpose}: {error}"
        ) from error
    return int(iExitCode), sOutput or ""
