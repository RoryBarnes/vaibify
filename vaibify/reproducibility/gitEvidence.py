"""Git evidence about who owns a tracked file, asked of a real git.

A low-level module (not a leaf: it imports ``repoFiles``, ``gitHardening`` and
``mutationAdmission``, never ``vaibify.gui``): the reproducibility layer's
questions about a repository's committed files -- whose identity last committed
one, whether the working copy differs from HEAD -- with the runner that binds
them to a repo-files adapter, host or container. It exists apart from
``reproductionRecord`` because the callers that need the MANIFEST question sit
on the poll path (``fileStatusManager``) and in the manifest check
(``pipelineRoutes``), and an architectural test keeps every reader of the
reproduction RECORDS off that path; the git questions are not records, so they
live here and both modules import them.

Every answer is settled by git's own exit code, measured 2026-09-12:
``rev-parse --verify --quiet HEAD`` exits 1 with no commits and 128
outside a repository; ``ls-tree`` exits 0 with empty output for an
untracked path and 128 when HEAD is unusable; ``config`` exits 1 for
an unset key; ``diff --quiet`` exits 1 for a difference. Any other
answer, and a runner that raises, is UNDETERMINED and refused -- a
broken git must never decide by its silence.
"""

__all__ = [
    "RecordKindUndeterminedError",
    "S_MANIFEST_OWNERSHIP_FOREIGN",
    "S_MANIFEST_OWNERSHIP_OWN",
    "S_MANIFEST_OWNERSHIP_UNDETERMINED",
    "fbHeadTracksManifest",
    "fbManifestDiffersFromHead",
    "fbRepositoryCarriesForeignManifest",
    "fbRepositoryCarriesForeignTrackedFile",
    "fdictManifestProvenanceForRepoFiles",
    "ffnBuildGitRunnerForRepoFiles",
    "fdictReadBaselineEvidence",
    "fsManifestOwnershipForGitRunner",
    "fsManifestOwnershipForRepoFiles",
    "fsManifestOwnershipFromSnapshotFacts",
]

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.reproducibility.gitHardening import LIST_GIT_HARDENING_CONFIG
from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)

# Whose MANIFEST.sha256 a repository carries. OWN is the only answer
# that lets a writer proceed without consent; FOREIGN is the author's
# claim on a clone; UNDETERMINED is a git that could not say.
S_MANIFEST_OWNERSHIP_OWN = "own"
S_MANIFEST_OWNERSHIP_FOREIGN = "foreign"
S_MANIFEST_OWNERSHIP_UNDETERMINED = "undetermined"

_S_MANIFEST_RELATIVE_PATH = "MANIFEST.sha256"
_F_GIT_QUESTION_TIMEOUT_SECONDS = 15.0


class RecordKindUndeterminedError(Exception):
    """Git could not say whose attestation the repository carries.

    Raised instead of answering, because either answer written on a
    guess is wrong in a way that cannot be undone: an attestation
    would overwrite somebody else's tracked claim, and a reproduction
    record would file the author's own verification as a stranger's.
    Both lanes refuse the write -- before any step runs, where they
    can.
    """


def _ftAskGit(ftRunGit, listArguments, sQuestion):
    """Run one git question; a runner that cannot run it is undetermined.

    A control-plane refusal is not uncertainty about the repository: the
    carrier declined to run the exec at all, and reading that as "could
    not say" would hide the refusal behind a guess about whose record
    it is.
    """
    try:
        iExitCode, sOutput = ftRunGit(listArguments)
    except Exception as error:  # noqa: BLE001 -- turned into a refusal
        fnReRaiseControlPlaneRefusal(error)
        raise RecordKindUndeterminedError(
            f"git could not be asked {sQuestion}: {error}"
        ) from error
    return int(iExitCode), (sOutput or "").strip()


def _fbRepositoryHasACommit(ftRunGit):
    """True iff HEAD names a commit; False for an initialised, empty repository."""
    iExitCode, _sHead = _ftAskGit(
        ftRunGit, ["rev-parse", "--verify", "--quiet", "HEAD"],
        "whether the repository has a commit",
    )
    if iExitCode == 0:
        return True
    # Exit 1 is git's own "no such revision" -- but a runner that
    # chains ``cd`` before git exits 1 for a missing directory too, so
    # the repository is asked to confirm it is one before an empty
    # history is believed.
    iExitCode, sInside = _ftAskGit(
        ftRunGit, ["rev-parse", "--is-inside-work-tree"],
        "whether the directory is a repository",
    )
    if iExitCode == 0 and sInside == "true":
        return False
    raise RecordKindUndeterminedError(
        "the repository that would receive the record could not be "
        f"read by git (exit {iExitCode})"
    )


def fbRepositoryCarriesForeignManifest(ftRunGit):
    """True iff HEAD tracks a MANIFEST.sha256 last committed by another identity.

    The same question as for the attestation, asked of the manifest,
    because the manifest is the author's claim about their bytes and
    every rewrite of it on a clone -- the automatic refresh on the
    Level 1 crossing, Regenerate, the re-pins that follow a
    reproduce.sh or archive-record write -- replaced that claim with
    the reproducer's own and let the manifest check pass against it
    (researcher-reported, 2026-09-13).
    """
    return fbRepositoryCarriesForeignTrackedFile(
        ftRunGit, _S_MANIFEST_RELATIVE_PATH, "the manifest",
    )


def fbRepositoryCarriesForeignTrackedFile(ftRunGit, sRelativePath, sWhat):
    """True iff HEAD tracks ``sRelativePath`` under another committer identity."""
    if not _fbRepositoryHasACommit(ftRunGit):
        return False
    iExitCode, sTracked = _ftAskGit(
        ftRunGit, ["ls-tree", "--name-only", "HEAD", "--", sRelativePath],
        f"whether {sWhat} is tracked",
    )
    if iExitCode != 0:
        raise RecordKindUndeterminedError(
            f"git could not list HEAD to see whether {sWhat} is "
            f"tracked (exit {iExitCode})"
        )
    if not sTracked:
        return False
    iExitCode, sCommitter = _ftAskGit(
        ftRunGit, ["log", "-1", "--format=%ce", "--", sRelativePath],
        f"who committed {sWhat}",
    )
    if iExitCode != 0 or not sCommitter:
        raise RecordKindUndeterminedError(
            f"{sWhat} is tracked at HEAD but git could not say who "
            f"committed it (exit {iExitCode})"
        )
    iExitCode, sOwnEmail = _ftAskGit(
        ftRunGit, ["config", "user.email"], "for the receiving identity",
    )
    if iExitCode == 1 or (iExitCode == 0 and not sOwnEmail):
        return True
    if iExitCode != 0:
        raise RecordKindUndeterminedError(
            f"git could not read the receiving identity (exit {iExitCode})"
        )
    return sCommitter.lower() != sOwnEmail.lower()


def fbHeadTracksManifest(ftRunGit):
    """True iff HEAD names a commit and that commit tracks MANIFEST.sha256."""
    if not _fbRepositoryHasACommit(ftRunGit):
        return False
    iExitCode, sTracked = _ftAskGit(
        ftRunGit, ["ls-tree", "--name-only", "HEAD", "--", _S_MANIFEST_RELATIVE_PATH],
        "whether the manifest is tracked",
    )
    if iExitCode != 0:
        raise RecordKindUndeterminedError(
            "git could not list HEAD to see whether the manifest is "
            f"tracked (exit {iExitCode})"
        )
    return bool(sTracked)


def fbManifestDiffersFromHead(ftRunGit):
    """True iff HEAD tracks MANIFEST.sha256 and the working copy differs from it.

    A manifest HEAD does not track has nothing to differ from and
    answers False. ``git diff --quiet`` exits 0 for identical content
    and 1 for a difference; any other exit is UNDETERMINED, because a
    check that cannot tell reads as "the same" only by silence.
    """
    if not fbHeadTracksManifest(ftRunGit):
        return False
    iExitCode, _sOutput = _ftAskGit(
        ftRunGit, ["diff", "--quiet", "HEAD", "--", _S_MANIFEST_RELATIVE_PATH],
        "whether the manifest differs from HEAD",
    )
    if iExitCode in (0, 1):
        return iExitCode == 1
    raise RecordKindUndeterminedError(
        f"git could not compare the manifest with HEAD (exit {iExitCode})"
    )


def ffnBuildGitRunnerForRepoFiles(filesRepo):
    """Return ``ftRunGit(listArguments)`` bound to a repo-files adapter.

    Host or container, the adapter runs commands where the repository
    lives, so one runner serves every lane that holds an adapter rather
    than a connection. Hardened like every other git vaibify runs.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRoot = fsRepoRootOf(filesRepo)

    def ftRunGit(listArguments):
        iExitCode, sOutput, _sError = filesRepo.ftRunCommand(
            ["git", "-C", sRoot, *LIST_GIT_HARDENING_CONFIG,
             *[str(sArgument) for sArgument in listArguments]],
            _F_GIT_QUESTION_TIMEOUT_SECONDS,
        )
        return int(iExitCode), sOutput or ""
    return ftRunGit


def fsManifestOwnershipForRepoFiles(filesRepo):
    """Say whose manifest a repository carries: OWN, FOREIGN or UNDETERMINED.

    The one predicate behind every writer of MANIFEST.sha256. OWN is
    the only answer that lets a write proceed without consent: a
    FOREIGN manifest is the author's claim, and UNDETERMINED is a git
    that could not say -- which must never fail open into rewriting
    somebody else's record. Adapter failures are undetermined too.
    """
    try:
        ftRunGit = ffnBuildGitRunnerForRepoFiles(filesRepo)
    except Exception as error:  # noqa: BLE001 -- an adapter that cannot ask is undetermined
        fnReRaiseControlPlaneRefusal(error)
        return S_MANIFEST_OWNERSHIP_UNDETERMINED
    return fsManifestOwnershipForGitRunner(ftRunGit)


def fsManifestOwnershipForGitRunner(ftRunGit):
    """Answer the ownership predicate from any git runner."""
    try:
        bForeign = fbRepositoryCarriesForeignManifest(ftRunGit)
    except RecordKindUndeterminedError:
        return S_MANIFEST_OWNERSHIP_UNDETERMINED
    except Exception as error:  # noqa: BLE001 -- a runner that cannot ask is undetermined
        fnReRaiseControlPlaneRefusal(error)
        return S_MANIFEST_OWNERSHIP_UNDETERMINED
    return (
        S_MANIFEST_OWNERSHIP_FOREIGN if bForeign else S_MANIFEST_OWNERSHIP_OWN
    )


def fsManifestOwnershipFromSnapshotFacts(dictFacts):
    """Answer the ownership predicate from the replies a snapshot carried.

    The poll cannot run git, so the snapshot program asked the fixed
    questions of ``gitHardening.T_MANIFEST_OWNERSHIP_GIT_QUESTIONS``
    inside the container and keyed each reply by its arguments. A
    question outside that set, or a reply the program could not get,
    makes the predicate undetermined -- never "own".
    """
    if not isinstance(dictFacts, dict):
        return S_MANIFEST_OWNERSHIP_UNDETERMINED

    def ftAnswerFromFacts(listArguments):
        listReply = dictFacts.get(" ".join(str(s) for s in listArguments))
        if not listReply or listReply[0] is None:
            raise RecordKindUndeterminedError(
                "the snapshot carried no answer to this git question")
        return int(listReply[0]), listReply[1] or ""

    return fsManifestOwnershipForGitRunner(ftAnswerFromFacts)


def fdictReadBaselineEvidence(filesRepo):
    """Return what HEAD says about the tree a reproduction is about to export.

    ``sResolvedCommit`` is HEAD as the BASELINE, and
    ``listPathsDifferingFromBaseline`` the tracked paths whose bytes
    differ from it plus the untracked ones: empty means the tree was
    clean. A git that cannot answer leaves ``bBaselineKnown`` False --
    never an empty list that would read as a clean tree. Read by the
    lane BEFORE the export and carried through the outcome, never
    re-read when the record is written: HEAD may have moved by then.
    """
    ftRunGit = ffnBuildGitRunnerForRepoFiles(filesRepo)
    dictUnknown = {
        "sResolvedCommit": "", "listPathsDifferingFromBaseline": None,
        "bBaselineKnown": False,
    }
    try:
        iHeadCode, sHead = ftRunGit(["rev-parse", "--verify", "--quiet", "HEAD"])
        iDiffCode, sDiffering = ftRunGit(
            ["-c", "core.quotepath=off", "diff", "--name-only", "HEAD"])
        iOtherCode, sUntracked = ftRunGit(
            ["-c", "core.quotepath=off", "ls-files", "--others",
             "--exclude-standard"])
    except Exception as error:  # noqa: BLE001 -- a git that cannot answer is unknown
        fnReRaiseControlPlaneRefusal(error)
        return dictUnknown
    if iHeadCode != 0 or iDiffCode != 0 or iOtherCode != 0:
        return dictUnknown
    return {
        "sResolvedCommit": (sHead or "").strip(),
        "listPathsDifferingFromBaseline": sorted(set(
            (sDiffering or "").split("\n") + (sUntracked or "").split("\n")
        ) - {""}),
        "bBaselineKnown": True,
    }


def fdictManifestProvenanceForRepoFiles(filesRepo):
    """Return what a manifest check should say about the manifest it read.

    ``sManifestOwnership`` is the three-state answer above and
    ``bManifestDiffersFromHead`` is True, False or None (undetermined):
    a check that passes against a manifest this machine rewrote is a
    self-comparison, and the dashboard must say so beside the count.
    """
    sOwnership = fsManifestOwnershipForRepoFiles(filesRepo)
    bDiffers = None
    try:
        bDiffers = fbManifestDiffersFromHead(
            ffnBuildGitRunnerForRepoFiles(filesRepo),
        )
    except RecordKindUndeterminedError:
        bDiffers = None
    except Exception as error:  # noqa: BLE001 -- an adapter that cannot ask is undetermined
        fnReRaiseControlPlaneRefusal(error)
        bDiffers = None
    return {
        "sManifestOwnership": sOwnership,
        "bManifestDiffersFromHead": bDiffers,
    }
