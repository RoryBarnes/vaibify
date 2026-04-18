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
_REGEX_URL_WITH_CREDENTIALS = re.compile(
    r"https?://[^:@\s]+:[^@\s]+@",
)
_LIST_SENSITIVE_KEYWORDS = (
    "password", "token", "bearer", "authorization",
)

# Hardening flags prepended to every ``git clone`` / ``git fetch`` run.
# Block file-transport submodules (``file://`` pointing at host paths),
# disable local symlink checkout (malicious tree entries cannot write
# outside the repo), and refuse submodule recursion entirely.
_LIST_GIT_HARDENING_CONFIG = [
    "-c", "protocol.file.allow=never",
    "-c", "protocol.allow=user",
    "-c", "core.symlinks=false",
    "-c", "submodule.recurse=false",
]


def _fsRedactStderr(sStderr):
    """Return sStderr with embedded URL credentials and secrets redacted.

    Applied before any git error text is embedded into an OverleafError
    that may reach the GUI or stderr. Local duplicate of the host-side
    helper; overleafSync is shipped into the container as a standalone
    script and cannot import from ``vaibify.reproducibility``.
    """
    if not sStderr:
        return ""
    sRedacted = _REGEX_URL_WITH_CREDENTIALS.sub(
        "https://<redacted>@", sStderr,
    )
    listLines = []
    for sLine in sRedacted.splitlines():
        sLower = sLine.lower()
        bSensitive = any(
            sKeyword in sLower
            for sKeyword in _LIST_SENSITIVE_KEYWORDS
        )
        listLines.append("<redacted>" if bSensitive else sLine)
    return "\n".join(listLines)

_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_AUTH = 3
_EXIT_RATE_LIMIT = 4
_EXIT_ERROR = 1


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def fnValidateTargetDirectory(sTargetDirectory):
    """Reject target-directory paths that are unsafe or would silently misroute.

    pathlib treats ``Path('/a') / '/b'`` as absolute ``/b``, so a
    leading ``/`` would copy files outside the cloned repo and produce
    a deceptively successful but no-op push.
    """
    if sTargetDirectory is None:
        raise OverleafError("Target directory must be provided.")
    if sTargetDirectory == "":
        return
    sFirst = sTargetDirectory[0]
    if sFirst == "/" or sFirst == "\\":
        raise OverleafError(
            f"Target directory must not start with a slash: "
            f"'{sTargetDirectory}'"
        )
    if "\x00" in sTargetDirectory:
        raise OverleafError(
            "Target directory must not contain null bytes."
        )
    for sSegment in sTargetDirectory.split("/"):
        if sSegment == "..":
            raise OverleafError(
                f"Target directory must not contain '..' segments: "
                f"'{sTargetDirectory}'"
            )


def fnValidatePullRelativePath(sRelativePath):
    """Reject pull paths that would escape the cloned repo.

    Enforces: no leading slash/backslash, no ``..`` segments, no NUL
    bytes. Applied to every entry in ``listPullPaths`` before any
    filesystem operation.
    """
    if sRelativePath is None or sRelativePath == "":
        raise OverleafError("Pull path must not be empty.")
    if "\x00" in sRelativePath:
        raise OverleafError(
            "Pull path must not contain null bytes."
        )
    if sRelativePath[0] == "/" or sRelativePath[0] == "\\":
        raise OverleafError(
            f"Pull path must not start with a slash: "
            f"'{sRelativePath}'"
        )
    for sSegment in sRelativePath.replace("\\", "/").split("/"):
        if sSegment == "..":
            raise OverleafError(
                f"Pull path must not contain '..' segments: "
                f"'{sRelativePath}'"
            )


def fnPushFiguresToOverleaf(
    listFigurePaths, sOverleafId, sTargetDirectory, sToken,
    sMirrorSha="",
):
    """Push figure files into an Overleaf project via git."""
    fnValidateTargetDirectory(sTargetDirectory)
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_push_")
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        listCredArgs = flistBuildCredentialHelperArgs(sTokenPath)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir, listCredArgs)
        _fnCopyFiguresToRepo(listFigurePaths, sTmpDir, sTargetDirectory)
        _fnCommitAndPush(sTmpDir, sMirrorSha, listCredArgs)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)
        _fnRemoveTokenFile(sTokenPath)


def fnPullTexFromOverleaf(
    sOverleafId, listPullPaths, sTargetDirectory, sToken,
):
    """Pull specified files from an Overleaf project.

    ``sTargetDirectory`` is a local filesystem location where the
    pulled files are written; it may be absolute. The per-file
    ``listPullPaths`` entries address files inside the cloned
    Overleaf repo and must therefore be relative and free of
    traversal metacharacters.
    """
    for sRelativePath in listPullPaths:
        fnValidatePullRelativePath(sRelativePath)
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_pull_")
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        listCredArgs = flistBuildCredentialHelperArgs(sTokenPath)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir, listCredArgs)
        _fnCopyPulledFiles(sTmpDir, listPullPaths, sTargetDirectory)
    finally:
        shutil.rmtree(sTmpDir, ignore_errors=True)
        _fnRemoveTokenFile(sTokenPath)


def fnPushAnnotatedToOverleaf(
    listFigurePaths, sOverleafId, sTargetDirectory,
    dictWorkflow, sGithubBaseUrl, sDoi, sToken,
    sTexFilename="main.tex", sMirrorSha="",
):
    """Push figures and annotate the TeX file with source links."""
    fnValidateTargetDirectory(sTargetDirectory)
    sTmpDir = tempfile.mkdtemp(prefix="vc_overleaf_annotate_")
    sTokenPath = _fsWriteTokenFile(sToken)
    try:
        listCredArgs = flistBuildCredentialHelperArgs(sTokenPath)
        _fnCloneOverleafRepo(sOverleafId, sTmpDir, listCredArgs)
        _fnCopyFiguresToRepo(
            listFigurePaths, sTmpDir, sTargetDirectory)
        _fnAnnotateTexInRepo(
            sTmpDir, sTexFilename, dictWorkflow,
            sGithubBaseUrl, sDoi)
        _fnCommitAndPush(sTmpDir, sMirrorSha, listCredArgs)
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


def flistBuildCredentialHelperArgs(sTokenFilePath):
    """Return git ``-c`` args that wire a one-shot credential helper.

    The helper reads the token out of ``sTokenFilePath`` at each git
    credential-helper invocation, so the token is never embedded in a
    URL, process argv, or shell history. Unlike the previous global
    ``git config`` approach, this leaves zero residue in the caller's
    ``~/.gitconfig`` — the helper only applies to git commands that
    receive these ``-c`` args.
    """
    sHelper = _fsBuildCredentialHelper(sTokenFilePath)
    sKey = f"credential.https://{_OVERLEAF_GIT_HOST}.helper"
    return ["-c", f"{sKey}={sHelper}"]


# ------------------------------------------------------------------
# Cloning
# ------------------------------------------------------------------


def _fnCloneOverleafRepo(sOverleafId, sDestination, listCredArgs=None):
    """Clone an Overleaf project into the destination directory."""
    sRepoUrl = f"https://{_OVERLEAF_GIT_HOST}/{sOverleafId}"
    listCommand = ["git"]
    if listCredArgs:
        listCommand.extend(listCredArgs)
    listCommand.extend(_LIST_GIT_HARDENING_CONFIG)
    listCommand.extend([
        "clone", "--depth", "1", "--no-recurse-submodules",
        sRepoUrl, sDestination,
    ])
    _fnRunSubprocess(listCommand, "Failed to clone Overleaf project")


# ------------------------------------------------------------------
# Figure push helpers
# ------------------------------------------------------------------


def _fnCopyFiguresToRepo(listFigurePaths, sRepoDir, sTargetDirectory):
    """Copy figure files into the target subdirectory of the repo."""
    pathTarget = Path(sRepoDir) / sTargetDirectory
    pathTarget.mkdir(parents=True, exist_ok=True)
    _fnAssertRealPathUnderRoot(str(pathTarget), sRepoDir)
    for sFilePath in listFigurePaths:
        _fnCopySingleFile(sFilePath, pathTarget)


def _fnAssertRealPathUnderRoot(sPath, sRoot):
    """Refuse to proceed when sPath's realpath escapes sRoot.

    Used before writing to the target directory so a symlinked
    ``pathTarget`` cannot redirect writes outside the cloned repo.
    """
    sRealPath = os.path.realpath(sPath)
    sRealRoot = os.path.realpath(sRoot)
    if sRealPath == sRealRoot:
        return
    if sRealPath.startswith(sRealRoot + os.sep):
        return
    raise OverleafError(
        f"Refusing to traverse symlink out of repo: '{sPath}'"
    )


def _fnCopySingleFile(sFilePath, pathTarget):
    """Copy one file into the target directory; refuses to follow symlinks."""
    pathSource = Path(sFilePath)
    if os.path.islink(sFilePath):
        raise OverleafError(
            f"Refusing to push symlink: '{sFilePath}'"
        )
    if not pathSource.is_file():
        raise FileNotFoundError(f"Figure not found: '{sFilePath}'")
    sDestination = str(pathTarget / pathSource.name)
    _fnAssertDestinationParentSafe(sDestination, str(pathTarget))
    shutil.copy2(
        str(pathSource), sDestination, follow_symlinks=False,
    )


def _fnAssertDestinationParentSafe(sDestination, sExpectedRoot):
    """Refuse to write when the destination's parent escapes sExpectedRoot."""
    sParent = os.path.dirname(sDestination) or "."
    sRealParent = os.path.realpath(sParent)
    sRealRoot = os.path.realpath(sExpectedRoot)
    if sRealParent == sRealRoot:
        return
    if sRealParent.startswith(sRealRoot + os.sep):
        return
    raise OverleafError(
        f"Refusing to write via symlink: '{sDestination}'"
    )


def _fnCommitAndPush(sRepoDir, sMirrorSha="", listCredArgs=None):
    """Stage all changes, commit with marker, and push.

    When ``sMirrorSha`` is set, the commit message records which mirror
    snapshot the push was built on. After a successful push, the
    post-push HEAD SHA is emitted to stdout as ``HEAD_SHA=<sha>`` so
    the host dispatcher can persist it into the sync-status baseline.
    """
    if not _fbHasUncommittedChanges(sRepoDir):
        sys.stdout.write("PUSH_STATUS=no-changes\n")
        _fnEmitHeadSha(sRepoDir)
        return
    _fnGitAdd(sRepoDir)
    _fnGitCommit(sRepoDir, sMirrorSha)
    _fnGitPush(sRepoDir, listCredArgs)
    sys.stdout.write("PUSH_STATUS=pushed\n")
    _fnEmitHeadSha(sRepoDir)


def _fnEmitHeadSha(sRepoDir):
    """Print a ``HEAD_SHA=<sha>`` line for the repo's current HEAD."""
    try:
        resultProcess = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=sRepoDir, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return
    if resultProcess.returncode != 0:
        return
    sHead = (resultProcess.stdout or "").strip()
    if sHead:
        sys.stdout.write(f"HEAD_SHA={sHead}\n")


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


def _fnGitCommit(sRepoDir, sMirrorSha=""):
    """Create a commit with the vaibify marker.

    When ``sMirrorSha`` is provided, the short form of the SHA is
    appended so Overleaf collaborators can see which mirror snapshot
    the push was built against.
    """
    _fnSetLocalCommitIdentity(sRepoDir)
    sMessage = f"{_COMMIT_MARKER} Update figures"
    if sMirrorSha:
        sMessage += f" (from mirror {sMirrorSha[:7]})"
    _fnRunSubprocess(
        ["git", "commit", "-m", sMessage],
        "git commit failed", sCwd=sRepoDir,
    )


def _fnGitPush(sRepoDir, listCredArgs=None):
    """Push to origin, detecting rate limits."""
    listCommand = ["git"]
    if listCredArgs:
        listCommand.extend(listCredArgs)
    listCommand.append("push")
    try:
        _fnRunSubprocess(
            listCommand, "git push failed", sCwd=sRepoDir,
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
    sSource = str(pathSource)
    _fnAssertRealPathUnderRoot(sSource, sRepoDir)
    if os.path.islink(sSource):
        raise OverleafError(
            f"Refusing to copy symlink from Overleaf: '{sRelativePath}'"
        )
    if not pathSource.is_file():
        raise FileNotFoundError(
            f"Overleaf file not found: '{sRelativePath}'"
        )
    pathDestFile = pathTarget / Path(sRelativePath).name
    sDestination = str(pathDestFile)
    _fnAssertDestinationParentSafe(sDestination, str(pathTarget))
    shutil.copy2(
        sSource, sDestination, follow_symlinks=False,
    )


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
    """Combine stdout and stderr from a CalledProcessError; redacted."""
    sStdout = (error.stdout or "").strip()
    sStderr = (error.stderr or "").strip()
    sCombined = f"{sStdout} {sStderr}".strip()
    return _fsRedactStderr(sCombined)


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
        listCredArgs = flistBuildCredentialHelperArgs(sTokenPath)
        sUrl = f"https://{_OVERLEAF_GIT_HOST}/{args.project}"
        listCommand = ["git"] + listCredArgs + _LIST_GIT_HARDENING_CONFIG
        listCommand.extend(["ls-remote", sUrl, "HEAD"])
        resultProcess = subprocess.run(
            listCommand, capture_output=True, text=True,
        )
    finally:
        _fnRemoveTokenFile(sTokenPath)
    if resultProcess.returncode != 0:
        sys.stderr.write(_fsRedactStderr(resultProcess.stderr or ""))
        sys.exit(resultProcess.returncode or _EXIT_ERROR)
    sys.exit(_EXIT_OK)


def _fnRunPush(args):
    """Push newline-separated figure paths from stdin (after token)."""
    _fnValidateProjectIdOrDie(args.project)
    sToken, sRemainder = _ftReadTokenAndRest()
    listPaths = _flistSplitRemainderLines(sRemainder)
    fnPushFiguresToOverleaf(
        listPaths, args.project, args.target, sToken,
        getattr(args, "mirror_sha", "") or "",
    )
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
        getattr(args, "mirror_sha", "") or "",
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
    sub.add_argument("--mirror-sha", default="")
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
    sub.add_argument("--mirror-sha", default="")
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
