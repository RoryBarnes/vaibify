"""Git-based Overleaf synchronisation for figures and TeX sources.

Pushes generated figures to an Overleaf project and pulls TeX sources
back, using git credential helpers so that tokens never appear in URLs
or command history.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path


class OverleafError(Exception):
    """General Overleaf sync error."""


class OverleafAuthError(OverleafError):
    """Authentication with Overleaf failed."""


class OverleafRateLimitError(OverleafError):
    """Overleaf rate limit encountered."""


_OVERLEAF_GIT_HOST = "git.overleaf.com"
_COMMIT_MARKER = "[vaibcask]"
_RATE_LIMIT_HINT = "rate limit"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def fnPushFiguresToOverleaf(
    listFigurePaths, sOverleafId, sTargetDirectory
):
    """Push figure files into an Overleaf project via git.

    Parameters
    ----------
    listFigurePaths : list of str
        Absolute paths to figure files.
    sOverleafId : str
        Overleaf project identifier.
    sTargetDirectory : str
        Subdirectory inside the Overleaf repo for figures.
    """
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_push_")
    try:
        fnConfigureGitCredentials(sOverleafId)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir)
        _fnCopyFiguresToRepo(listFigurePaths, sTmpDir, sTargetDirectory)
        _fnCommitAndPush(sTmpDir)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)


def fnPullTexFromOverleaf(
    sOverleafId, listPullPaths, sTargetDirectory
):
    """Pull specified files from an Overleaf project.

    Parameters
    ----------
    sOverleafId : str
        Overleaf project identifier.
    listPullPaths : list of str
        Relative paths within the Overleaf repo to copy out.
    sTargetDirectory : str
        Local directory to receive the pulled files.
    """
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_pull_")
    try:
        fnConfigureGitCredentials(sOverleafId)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir)
        _fnCopyPulledFiles(sTmpDir, listPullPaths, sTargetDirectory)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)


def fnConfigureGitCredentials(sOverleafId):
    """Configure a git credential helper for the Overleaf host.

    The helper calls secretManager at runtime so that the token
    is never embedded in a URL or shell history.
    """
    sHelper = _fsBuildCredentialHelper()
    _fnRunGitConfig(sHelper)


# ------------------------------------------------------------------
# Cloning
# ------------------------------------------------------------------


def _fnCloneOverleafRepo(sOverleafId, sDestination):
    """Clone an Overleaf project into the destination directory."""
    sRepoUrl = f"https://{_OVERLEAF_GIT_HOST}/{sOverleafId}"
    listCommand = [
        "git", "clone", "--depth", "1", sRepoUrl, sDestination,
    ]
    _fnRunSubprocess(listCommand, "Failed to clone Overleaf project")


# ------------------------------------------------------------------
# Figure push helpers
# ------------------------------------------------------------------


def _fnCopyFiguresToRepo(listFigurePaths, sRepoDir, sTargetDirectory):
    """Copy figure files into the target subdirectory of the repo."""
    pathTarget = Path(sRepoDir) / sTargetDirectory
    pathTarget.mkdir(parents=True, exist_ok=True)
    for sFilePath in listFigurePaths:
        _fnCopySingleFile(sFilePath, pathTarget)


def _fnCopySingleFile(sFilePath, pathTarget):
    """Copy one file into the target directory."""
    pathSource = Path(sFilePath)
    if not pathSource.is_file():
        raise FileNotFoundError(f"Figure not found: '{sFilePath}'")
    shutil.copy2(str(pathSource), str(pathTarget / pathSource.name))


def _fnCommitAndPush(sRepoDir):
    """Stage all changes, commit with marker, and push."""
    if not _fbHasUncommittedChanges(sRepoDir):
        return
    _fnGitAdd(sRepoDir)
    _fnGitCommit(sRepoDir)
    _fnGitPush(sRepoDir)


def _fbHasUncommittedChanges(sRepoDir):
    """Return True if the repo has staged or unstaged changes."""
    resultProcess = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=sRepoDir, capture_output=True, text=True,
    )
    return len(resultProcess.stdout.strip()) > 0


def _fnGitAdd(sRepoDir):
    """Stage all changes in the repository."""
    _fnRunSubprocess(
        ["git", "add", "-A"], "git add failed", sCwd=sRepoDir,
    )


def _fnGitCommit(sRepoDir):
    """Create a commit with the vaibcask marker."""
    sMessage = f"{_COMMIT_MARKER} Update figures"
    _fnRunSubprocess(
        ["git", "commit", "-m", sMessage],
        "git commit failed", sCwd=sRepoDir,
    )


def _fnGitPush(sRepoDir):
    """Push to origin, detecting rate limits."""
    try:
        _fnRunSubprocess(
            ["git", "push"], "git push failed", sCwd=sRepoDir,
        )
    except OverleafError as error:
        _fnDetectRateLimit(str(error))
        raise


# ------------------------------------------------------------------
# File pull helpers
# ------------------------------------------------------------------


def _fnCopyPulledFiles(sRepoDir, listPullPaths, sTargetDirectory):
    """Copy requested files from the cloned repo to the target."""
    pathTarget = Path(sTargetDirectory)
    pathTarget.mkdir(parents=True, exist_ok=True)
    for sRelativePath in listPullPaths:
        _fnCopyPulledFile(sRepoDir, sRelativePath, pathTarget)


def _fnCopyPulledFile(sRepoDir, sRelativePath, pathTarget):
    """Copy one file from the repo clone to the target directory."""
    pathSource = Path(sRepoDir) / sRelativePath
    if not pathSource.is_file():
        raise FileNotFoundError(
            f"Overleaf file not found: '{sRelativePath}'"
        )
    pathDestFile = pathTarget / Path(sRelativePath).name
    shutil.copy2(str(pathSource), str(pathDestFile))


# ------------------------------------------------------------------
# Credential and subprocess helpers
# ------------------------------------------------------------------


def _fsBuildCredentialHelper():
    """Build a shell command string for the git credential helper."""
    return (
        "!f() { "
        "python3 -c \""
        "from vaibcask.config.secretManager import fsRetrieveSecret; "
        "print('password=' + fsRetrieveSecret('overleaf_token', 'keyring'))"
        "\"; }; f"
    )


def _fnRunGitConfig(sHelper):
    """Set the credential helper in the global git config."""
    _fnRunSubprocess(
        [
            "git", "config", "--global",
            f"credential.https://{_OVERLEAF_GIT_HOST}.helper",
            sHelper,
        ],
        "Failed to configure git credentials",
    )


def _fnRunSubprocess(listCommand, sErrorMessage, sCwd=None):
    """Run a subprocess and raise OverleafError on failure."""
    try:
        resultProcess = subprocess.run(
            listCommand, cwd=sCwd,
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        raise OverleafError(
            f"{sErrorMessage}: command not found "
            f"('{listCommand[0]}')"
        )
    except subprocess.CalledProcessError as error:
        sOutput = _fsCombineErrorOutput(error)
        _fnDetectAuthFailure(sOutput)
        _fnDetectRateLimit(sOutput)
        raise OverleafError(f"{sErrorMessage}: {sOutput}")
    return resultProcess


def _fsCombineErrorOutput(error):
    """Combine stdout and stderr from a CalledProcessError."""
    sStdout = (error.stdout or "").strip()
    sStderr = (error.stderr or "").strip()
    return f"{sStdout} {sStderr}".strip()


def _fnDetectAuthFailure(sOutput):
    """Raise OverleafAuthError if output indicates auth failure."""
    sLower = sOutput.lower()
    if "authentication" in sLower or "401" in sLower:
        raise OverleafAuthError(
            f"Overleaf authentication failed: {sOutput}"
        )


def _fnDetectRateLimit(sOutput):
    """Raise OverleafRateLimitError if output indicates rate limit."""
    if _RATE_LIMIT_HINT in sOutput.lower():
        raise OverleafRateLimitError(
            f"Overleaf rate limit exceeded: {sOutput}"
        )
