"""Git-based Overleaf synchronisation for figures and TeX sources.

Pushes generated figures to an Overleaf project and pulls TeX sources
back, using git credential helpers so that tokens never appear in URLs
or command history.

Self-contained on stdlib + ``keyring`` + ``git`` so the module can be
shipped into a Docker container and executed as a standalone script at
``/usr/share/vaibify/overleafSync.py``. The sibling import of
``latexConnector`` below tolerates both the packaged layout (host-side
unit tests) and the flat container layout (script invocation).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from vaibify.reproducibility.latexConnector import fsAnnotateTexFile
except ImportError:
    from latexConnector import fsAnnotateTexFile


class OverleafError(Exception):
    """General Overleaf sync error."""


class OverleafAuthError(OverleafError):
    """Authentication with Overleaf failed."""


class OverleafRateLimitError(OverleafError):
    """Overleaf rate limit encountered."""


_OVERLEAF_GIT_HOST = "git.overleaf.com"
_COMMIT_MARKER = "[vaibify]"
_RATE_LIMIT_HINT = "rate limit"
_PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_AUTH = 3
_EXIT_RATE_LIMIT = 4
_EXIT_ERROR = 1


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def fnPushFiguresToOverleaf(
    listFigurePaths, sOverleafId, sTargetDirectory, sToken,
):
    """Push figure files into an Overleaf project via git."""
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_push_")
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        fnConfigureGitCredentials(sTokenPath)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir)
        _fnCopyFiguresToRepo(listFigurePaths, sTmpDir, sTargetDirectory)
        _fnCommitAndPush(sTmpDir)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)
        _fnRemoveTokenFile(sTokenPath)


def fnPullTexFromOverleaf(
    sOverleafId, listPullPaths, sTargetDirectory, sToken,
):
    """Pull specified files from an Overleaf project."""
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_pull_")
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        fnConfigureGitCredentials(sTokenPath)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir)
        _fnCopyPulledFiles(sTmpDir, listPullPaths, sTargetDirectory)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)
        _fnRemoveTokenFile(sTokenPath)


def fnPushAnnotatedToOverleaf(
    listFigurePaths, sOverleafId, sTargetDirectory,
    dictWorkflow, sGithubBaseUrl, sDoi, sToken,
    sTexFilename="main.tex",
):
    """Push figures and annotate the TeX file with source links."""
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_annotate_")
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        fnConfigureGitCredentials(sTokenPath)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir)
        _fnCopyFiguresToRepo(
            listFigurePaths, sTmpDir, sTargetDirectory)
        _fnAnnotateTexInRepo(
            sTmpDir, sTexFilename, dictWorkflow,
            sGithubBaseUrl, sDoi)
        _fnCommitAndPush(sTmpDir)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)
        _fnRemoveTokenFile(sTokenPath)


def _fnAnnotateTexInRepo(
    sRepoDir, sTexFilename, dictWorkflow,
    sGithubBaseUrl, sDoi,
):
    """Read, annotate, and write back the TeX file."""
    pathTex = Path(sRepoDir) / sTexFilename
    if not pathTex.exists():
        raise OverleafError(
            f"TeX file not found in Overleaf: {sTexFilename}"
        )
    sOriginal = pathTex.read_text(encoding="utf-8")
    sAnnotated = fsAnnotateTexFile(
        sOriginal, dictWorkflow, sGithubBaseUrl, sDoi
    )
    if sAnnotated != sOriginal:
        pathTex.write_text(sAnnotated, encoding="utf-8")


def fnConfigureGitCredentials(sTokenFilePath):
    """Configure a git credential helper pointing at sTokenFilePath.

    The helper reads the token out of the supplied file at each git
    credential-helper invocation, so the token is never embedded in a
    URL, process argv, or shell history.
    """
    sHelper = _fsBuildCredentialHelper(sTokenFilePath)
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


_S_COMMIT_USER_NAME = "vaibify"
_S_COMMIT_USER_EMAIL = "vaibify@localhost"


def _fnSetLocalCommitIdentity(sRepoDir):
    """Set user.name/user.email locally so commit doesn't need globals."""
    _fnRunSubprocess(
        ["git", "config", "user.name", _S_COMMIT_USER_NAME],
        "git config user.name failed", sCwd=sRepoDir,
    )
    _fnRunSubprocess(
        ["git", "config", "user.email", _S_COMMIT_USER_EMAIL],
        "git config user.email failed", sCwd=sRepoDir,
    )


def _fnGitCommit(sRepoDir):
    """Create a commit with the vaibify marker."""
    _fnSetLocalCommitIdentity(sRepoDir)
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


def _fsBuildCredentialHelper(sTokenFilePath):
    """Build a shell command string for the git credential helper.

    Git credential helpers must emit both a ``username=`` and a
    ``password=`` line, otherwise git falls through to prompting for
    the username on stdin, which fails in non-interactive contexts
    with ``could not read Username: No such device or address``.
    Overleaf's git bridge accepts the literal username ``git`` with
    the project's git-authentication token as the password.
    """
    import shlex
    return (
        "!f() { "
        "echo 'username=git'; "
        "cat " + shlex.quote(sTokenFilePath)
        + " | sed 's/^/password=/'; "
        "}; f"
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


def _fsWriteTokenFile(sToken):
    """Write the token to a mode-600 temp file and return its path."""
    import stat as statModule
    iFd, sPath = tempfile.mkstemp(prefix="_vc_overleaf_", suffix=".tok")
    try:
        os.fchmod(
            iFd, statModule.S_IRUSR | statModule.S_IWUSR)
        os.write(iFd, sToken.encode("utf-8"))
    finally:
        os.close(iFd)
    return sPath


def _fnRemoveTokenFile(sPath):
    """Remove the token temp file, ignoring missing-file errors."""
    try:
        os.remove(sPath)
    except FileNotFoundError:
        pass


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


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


def _fnValidateProjectIdOrDie(sProjectId):
    """Reject malformed project IDs before touching any subprocess."""
    if not _PROJECT_ID_PATTERN.match(sProjectId or ""):
        sys.stderr.write(
            f"Invalid Overleaf project ID: {sProjectId!r}\n"
        )
        sys.exit(_EXIT_USAGE)


def _ftReadTokenAndRest():
    """Return (sToken, sRemainder) from stdin: first line is the token."""
    sAll = sys.stdin.read()
    sToken, _, sRemainder = sAll.partition("\n")
    sToken = sToken.strip()
    if not sToken:
        sys.stderr.write(
            "Overleaf token not provided on stdin (first line).\n")
        sys.exit(_EXIT_AUTH)
    return sToken, sRemainder


def _flistSplitRemainderLines(sRemainder):
    """Return stripped non-empty lines from the stdin remainder."""
    return [
        sLine.strip()
        for sLine in sRemainder.splitlines()
        if sLine.strip()
    ]


def _fnRunLsRemote(args):
    """Validate credentials via git ls-remote; exit code mirrors git."""
    _fnValidateProjectIdOrDie(args.project)
    sToken, _ = _ftReadTokenAndRest()
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        fnConfigureGitCredentials(sTokenPath)
        sUrl = f"https://{_OVERLEAF_GIT_HOST}/{args.project}"
        resultProcess = subprocess.run(
            ["git", "ls-remote", sUrl, "HEAD"],
            capture_output=True, text=True,
        )
    finally:
        _fnRemoveTokenFile(sTokenPath)
    if resultProcess.returncode != 0:
        sys.stderr.write(resultProcess.stderr or "")
        sys.exit(resultProcess.returncode or _EXIT_ERROR)
    sys.exit(_EXIT_OK)


def _fnRunPush(args):
    """Push newline-separated figure paths from stdin (after token)."""
    _fnValidateProjectIdOrDie(args.project)
    sToken, sRemainder = _ftReadTokenAndRest()
    listPaths = _flistSplitRemainderLines(sRemainder)
    fnPushFiguresToOverleaf(
        listPaths, args.project, args.target, sToken)
    sys.stdout.write("ok\n")


def _fnRunPushAnnotated(args):
    """Push with TeX annotation; token on line 1, JSON on rest of stdin."""
    _fnValidateProjectIdOrDie(args.project)
    sToken, sRemainder = _ftReadTokenAndRest()
    dictPayload = json.loads(sRemainder or "{}")
    fnPushAnnotatedToOverleaf(
        dictPayload.get("listFigurePaths", []),
        args.project, args.target,
        dictPayload.get("dictWorkflow", {}),
        args.github_base_url, args.doi, sToken,
        args.tex_filename,
    )
    sys.stdout.write("ok\n")


def _fnRunPull(args):
    """Pull newline-separated repo paths from stdin (after token)."""
    _fnValidateProjectIdOrDie(args.project)
    sToken, sRemainder = _ftReadTokenAndRest()
    listPaths = _flistSplitRemainderLines(sRemainder)
    fnPullTexFromOverleaf(
        args.project, listPaths, args.target, sToken)
    sys.stdout.write("ok\n")


def _fnBuildParser():
    """Build the argparse parser with four subcommands."""
    parser = argparse.ArgumentParser(
        prog="overleafSync",
        description="Sync figures and TeX with an Overleaf project.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _fnAddLsRemoteParser(subparsers)
    _fnAddPushParser(subparsers)
    _fnAddPushAnnotatedParser(subparsers)
    _fnAddPullParser(subparsers)
    return parser


def _fnAddLsRemoteParser(subparsers):
    """Register the ls-remote subcommand."""
    sub = subparsers.add_parser(
        "ls-remote", help="Validate credentials via git ls-remote.",
    )
    sub.add_argument("--project", required=True)
    sub.set_defaults(func=_fnRunLsRemote)


def _fnAddPushParser(subparsers):
    """Register the push subcommand."""
    sub = subparsers.add_parser(
        "push", help="Push figures (paths from stdin).",
    )
    sub.add_argument("--project", required=True)
    sub.add_argument("--target", required=True)
    sub.set_defaults(func=_fnRunPush)


def _fnAddPushAnnotatedParser(subparsers):
    """Register the push-annotated subcommand."""
    sub = subparsers.add_parser(
        "push-annotated",
        help="Push figures + annotate TeX (JSON payload on stdin).",
    )
    sub.add_argument("--project", required=True)
    sub.add_argument("--target", required=True)
    sub.add_argument("--github-base-url", required=True)
    sub.add_argument("--doi", default="")
    sub.add_argument("--tex-filename", default="main.tex")
    sub.set_defaults(func=_fnRunPushAnnotated)


def _fnAddPullParser(subparsers):
    """Register the pull subcommand."""
    sub = subparsers.add_parser(
        "pull", help="Pull TeX files (paths from stdin).",
    )
    sub.add_argument("--project", required=True)
    sub.add_argument("--target", required=True)
    sub.set_defaults(func=_fnRunPull)


def main(listArgv=None):
    """CLI entry point; dispatches to the requested subcommand."""
    parser = _fnBuildParser()
    args = parser.parse_args(listArgv)
    try:
        args.func(args)
    except OverleafAuthError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(_EXIT_AUTH)
    except OverleafRateLimitError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(_EXIT_RATE_LIMIT)
    except OverleafError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(_EXIT_ERROR)


if __name__ == "__main__":
    main()
