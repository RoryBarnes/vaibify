"""Dispatch sync operations to run inside Docker containers."""

import json
import posixpath
import re
import subprocess
import uuid

from . import workflowManager
from .pipelineUtils import fsShellQuote

__all__ = [
    "fbValidateOverleafCredentials",
    "fbValidateZenodoToken",
    "fdictCheckConnectivity",
    "fdictClassifyError",
    "fdictParseTestMarkerOutput",
    "fdictSyncResult",
    "flistCollectOutputFiles",
    "flistExtractAllScriptPaths",
    "flistGetDirtyFiles",
    "fnDeleteCredentialFromContainer",
    "fnStoreCredentialInContainer",
    "fnValidateOverleafProjectId",
    "fnValidateServiceName",
    "fsBuildDagDot",
    "fsBuildTestMarkerCheckCommand",
    "fsPythonCommand",
    "ftResultAddFileToGithub",
    "ftResultArchiveProject",
    "ftResultArchiveToZenodo",
    "DICT_DAG_MEDIA_TYPES",
    "ftResultExportDag",
    "ftResultGenerateDagSvg",
    "ftResultGenerateLatex",
    "ftResultPullFromOverleaf",
    "ftResultPushScriptsToGithub",
    "ftResultPushStagedToGithub",
    "ftResultPushToGithub",
    "ftResultPushToOverleaf",
]

SET_VALID_SERVICES = {"github", "overleaf", "zenodo"}
SET_VALID_TOKEN_NAMES = {"overleaf_token", "zenodo_token", "gh_token"}


_LIST_AUTH_PATTERNS = [
    "authentication", "401", "403", "forbidden",
    "invalid credentials", "bad credentials",
]
_LIST_RATE_LIMIT_PATTERNS = ["rate limit", "429", "too many requests"]
_LIST_NOT_FOUND_PATTERNS = ["not found", "404", "no such"]
_LIST_NETWORK_PATTERNS = [
    "timeout", "connection refused", "network",
    "could not resolve", "no route",
]


def fdictClassifyError(iExitCode, sOutput):
    """Classify a sync command failure by scanning output text."""
    sLower = sOutput.lower()
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


def fsPythonCommand(sImportLine, sFunctionCall):
    """Build a python3 -c command string from import and call."""
    return f'python3 -c "{sImportLine}; {sFunctionCall}"'


def ftResultPushToOverleaf(
    connectionDocker, sContainerId,
    listFilePaths, sProjectId, sTargetDirectory,
    dictWorkflow=None, sGithubBaseUrl="", sDoi="",
    sTexFilename="main.tex",
):
    """Push figures to Overleaf, optionally annotating the TeX."""
    fnValidateOverleafProjectId(sProjectId)
    if dictWorkflow and sGithubBaseUrl:
        return _ftResultAnnotatedPush(
            connectionDocker, sContainerId, listFilePaths,
            sProjectId, sTargetDirectory, dictWorkflow,
            sGithubBaseUrl, sDoi, sTexFilename,
        )
    return _ftResultPlainPush(
        connectionDocker, sContainerId, listFilePaths,
        sProjectId, sTargetDirectory,
    )


_S_OVERLEAF_SCRIPT = "/usr/share/vaibify/overleafSync.py"


def _fsOverleafCliBase(sSubcommand, sProjectId, sTargetDirectory=None):
    """Build the python3 invocation prefix for an overleafSync CLI call."""
    listParts = [
        "python3", _S_OVERLEAF_SCRIPT, sSubcommand,
        "--project", fsShellQuote(sProjectId),
    ]
    if sTargetDirectory is not None:
        listParts += ["--target", fsShellQuote(sTargetDirectory)]
    return " ".join(listParts)


def _fsPipeStdinCommand(sStdinData, sCommand):
    """Compose a ``printf ... | command`` string with safe quoting."""
    return f"printf '%s' {fsShellQuote(sStdinData)} | {sCommand}"


def _fsFetchOverleafToken():
    """Fetch the Overleaf token from the host keyring for per-push use."""
    from vaibify.config.secretManager import fsRetrieveSecret
    return fsRetrieveSecret("overleaf_token", "keyring")


def _fsPrependToken(sToken, sPayload):
    """Prepend the token as the first stdin line before a payload."""
    return sToken + "\n" + sPayload


def _ftResultPlainPush(
    connectionDocker, sContainerId,
    listFilePaths, sProjectId, sTargetDirectory,
):
    """Push figures without TeX annotation."""
    sStdin = _fsPrependToken(
        _fsFetchOverleafToken(), "\n".join(listFilePaths))
    sCli = _fsOverleafCliBase("push", sProjectId, sTargetDirectory)
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, _fsPipeStdinCommand(sStdin, sCli),
    )


def _ftResultAnnotatedPush(
    connectionDocker, sContainerId, listFilePaths,
    sProjectId, sTargetDirectory, dictWorkflow,
    sGithubBaseUrl, sDoi, sTexFilename,
):
    """Push figures and annotate the TeX with source links."""
    sPayload = json.dumps({
        "listFigurePaths": listFilePaths,
        "dictWorkflow": dictWorkflow,
    })
    sStdin = _fsPrependToken(_fsFetchOverleafToken(), sPayload)
    sCli = (
        _fsOverleafCliBase(
            "push-annotated", sProjectId, sTargetDirectory)
        + f" --github-base-url {fsShellQuote(sGithubBaseUrl)}"
        + f" --doi {fsShellQuote(sDoi)}"
        + f" --tex-filename {fsShellQuote(sTexFilename)}"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, _fsPipeStdinCommand(sStdin, sCli),
    )


def ftResultPullFromOverleaf(
    connectionDocker, sContainerId,
    sProjectId, listPullPaths, sTargetDirectory,
):
    """Pull TeX files from Overleaf inside the container."""
    fnValidateOverleafProjectId(sProjectId)
    sStdin = _fsPrependToken(
        _fsFetchOverleafToken(), "\n".join(listPullPaths))
    sCli = _fsOverleafCliBase("pull", sProjectId, sTargetDirectory)
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, _fsPipeStdinCommand(sStdin, sCli),
    )


def ftResultArchiveToZenodo(
    connectionDocker, sContainerId, sService, listFilePaths,
):
    """Upload files to Zenodo inside the container."""
    fnValidateServiceName(sService)
    sImport = (
        "from vaibify.reproducibility.zenodoClient "
        "import ZenodoClient"
    )
    sCall = (
        f"c=ZenodoClient({repr(sService)}); "
        f"d=c.fdictCreateDraft(); "
        f"[c.fnUploadFile(d['id'], f) for f in {repr(listFilePaths)}]; "
        f"c.fnPublishDraft(d['id']); "
        f"print('Published deposit:', d['id'])"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall)
    )


def ftResultPushToGithub(
    connectionDocker, sContainerId,
    listFilePaths, sCommitMessage, sWorkdir,
):
    """Git add, commit, and push files inside the container."""
    sQuotedPaths = " ".join(fsShellQuote(s) for s in listFilePaths)
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git add {sQuotedPaths} && "
        f"git commit -m {fsShellQuote(sCommitMessage)} && "
        f"git push && "
        f"git rev-parse --short HEAD"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )


def ftResultPushStagedToGithub(
    connectionDocker, sContainerId, sCommitMessage, sWorkdir,
):
    """Commit whatever is staged in sWorkdir and push to origin.

    Does NOT run `git add`. Returns (iExitCode, sCombinedOutput).
    """
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git commit -m {fsShellQuote(sCommitMessage)} && "
        f"git push && "
        f"git rev-parse --short HEAD"
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


def _flistBuildStepCopyCommandList(dictWorkflow):
    """Build per-step copy commands for scripts and archive plots."""
    dictDirMap = workflowManager.fdictBuildStepDirectoryMap(dictWorkflow)
    listCommands = []
    for iStep, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sCamelDir = dictDirMap.get(iStep, "")
        if not sCamelDir:
            continue
        listScripts = workflowManager.flistExtractStepScripts(dictStep)
        sStepDir = dictStep.get("sDirectory", "")
        listArchivePlots = _flistArchivePlotPaths(
            dictStep, sStepDir, workflowManager.fsGetPlotCategory)
        if not listScripts and not listArchivePlots:
            continue
        listCommands.append(
            _fsBuildStepCopyCommands(
                sStepDir, sCamelDir, listScripts, listArchivePlots
            )
        )
    return listCommands


def ftResultPushScriptsToGithub(
    connectionDocker, sContainerId,
    dictWorkflow, sCommitMessage, sWorkdir,
):
    """Organize scripts + archive PNGs into camelCase dirs and push."""
    listCommands = _flistBuildStepCopyCommandList(dictWorkflow)
    if not listCommands:
        return (1, "No scripts found to push")
    sGitIgnore = _fsGenerateGitIgnore()
    sReadme = _fsGenerateReadme(dictWorkflow)
    sSetup = " && ".join(listCommands)
    sGitCommand = (
        f"cd {fsShellQuote(sWorkdir)} && {sSetup} && "
        f"echo {fsShellQuote(sGitIgnore)} > .gitignore && "
        f"echo {fsShellQuote(sReadme)} > README.md && "
        f"git add -A && "
        f"git commit -m {fsShellQuote(sCommitMessage)} && "
        f"git push && git rev-parse --short HEAD"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sGitCommand
    )


def _flistArchivePlotPaths(dictStep, sStepDir, fsGetCategory):
    """Return absolute paths of archive plot PDFs for PNG conversion."""
    listPaths = []
    for sFile in dictStep.get("saPlotFiles", []):
        if fsGetCategory(dictStep, sFile) != "archive":
            continue
        sAbsPath = sFile if sFile.startswith("/") else (
            f"{sStepDir}/{sFile}" if sStepDir else sFile
        )
        listPaths.append(sAbsPath)
    return listPaths


def _fsBuildStepCopyCommands(
    sStepDir, sCamelDir, listScripts, listArchivePlots
):
    """Build shell commands to populate a camelCase step dir."""
    sMkdir = f"mkdir -p {fsShellQuote(sCamelDir)}"
    listCopy = [sMkdir]
    for sScript in listScripts:
        sSrc = f"{sStepDir}/{sScript}" if sStepDir else sScript
        sDest = f"{sCamelDir}/{posixpath.basename(sScript)}"
        listCopy.append(
            f"cp {fsShellQuote(sSrc)} {fsShellQuote(sDest)}"
        )
    for sPlotPath in listArchivePlots:
        sBasename = posixpath.splitext(
            posixpath.basename(sPlotPath))[0]
        sPng = f"{sCamelDir}/{sBasename}.png"
        listCopy.append(
            f"pdftoppm -png -r 150 -singlefile "
            f"{fsShellQuote(sPlotPath)} "
            f"{fsShellQuote(sCamelDir + '/' + sBasename)} "
            f"2>/dev/null || cp {fsShellQuote(sPlotPath)} "
            f"{fsShellQuote(sPng)} 2>/dev/null || true"
        )
    return " && ".join(listCopy)


def _fsGenerateGitIgnore():
    """Return a .gitignore for vaibified repos."""
    return (
        "# Generated outputs\n"
        "Plot/*.pdf\n"
        "*.npy\n*.npz\n*.h5\n*.hdf5\n*.pkl\n*.pickle\n"
        "*.bpa\n*.csv\n"
        "# Logs\n"
        ".vaibify/logs/\n"
    )


def _fsGenerateReadme(dictWorkflow):
    """Return a README.md summarizing the workflow."""
    sName = dictWorkflow.get("sWorkflowName", "Vaibify Workflow")
    sTitle = dictWorkflow.get("sProjectTitle", sName)
    listLines = [f"# {sTitle}", "", "## Pipeline Steps", ""]
    for iStep, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sStepName = dictStep.get("sName", f"Step {iStep + 1}")
        listLines.append(f"{iStep + 1}. {sStepName}")
    listLines.append("")
    listLines.append("Generated by [Vaibify]"
                     "(https://github.com/RoryBarnes/vaibify)")
    return "\n".join(listLines)


def ftResultAddFileToGithub(
    connectionDocker, sContainerId,
    sFilePath, sCommitMessage, sWorkdir,
):
    """Git add, commit, push a single file inside the container."""
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git add {fsShellQuote(sFilePath)} && "
        f"git commit -m {fsShellQuote(sCommitMessage)} && "
        f"git push && git rev-parse --short HEAD"
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
        sContainerId, fsPythonCommand(sImport, sCall)
    )


def fdictCheckConnectivity(
    connectionDocker, sContainerId, sService,
):
    """Check if credentials are available for a service."""
    fnValidateServiceName(sService)
    if sService == "github":
        return _fdictCheckGithub(connectionDocker, sContainerId)
    if sService == "overleaf":
        return _fdictCheckHostKeyring("overleaf_token")
    if sService == "zenodo":
        return _fdictCheckKeyring(
            connectionDocker, sContainerId, "zenodo_token"
        )
    return {"bConnected": False, "sMessage": "Unknown service"}


def _fdictCheckHostKeyring(sTokenName):
    """Check whether a credential exists in the host OS keyring."""
    from vaibify.config.secretManager import fbSecretExists
    if fbSecretExists(sTokenName, "keyring"):
        return {"bConnected": True, "sMessage": "Connected"}
    return {"bConnected": False, "sMessage": "Token not found"}


def _fdictCheckGithub(connectionDocker, sContainerId):
    """Check GitHub connectivity by testing a git remote."""
    sTestCommand = (
        "for sDir in /workspace/*/; do "
        "  if [ -d \"$sDir/.git\" ]; then "
        "    cd \"$sDir\" && "
        "    git ls-remote --exit-code origin HEAD "
        "    >/dev/null 2>&1 && exit 0; "
        "  fi; "
        "done; exit 1"
    )
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, sTestCommand
    )
    if iExitCode == 0:
        return {"bConnected": True, "sMessage": "Connected"}
    return {
        "bConnected": False,
        "sMessage": "Cannot reach GitHub from container",
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
    if not _fbKeyringBackendHealthy(connectionDocker, sContainerId):
        return {
            "bConnected": False,
            "sMessage": S_KEYRING_BACKEND_FAIL_MESSAGE,
        }
    return _fdictProbeKeyringToken(
        connectionDocker, sContainerId, sTokenName,
    )


def fnStoreCredentialInContainer(
    connectionDocker, sContainerId, sName, sValue,
):
    """Store a credential in the container's keyring via temp file."""
    if sName not in SET_VALID_TOKEN_NAMES:
        raise ValueError(f"Invalid token name: {sName}")
    sTempPath = f"/tmp/_vc_cred_{uuid.uuid4().hex[:12]}"
    sCommand = fsPythonCommand(
        "import keyring",
        f"keyring.set_password('vaibify', {repr(sName)}, "
        f"open({repr(sTempPath)}).read().strip())",
    )
    connectionDocker.fnWriteFile(
        sContainerId, sTempPath, sValue.encode("utf-8")
    )
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand
        )
        if iExitCode != 0:
            raise RuntimeError(
                f"Keyring storage failed: {(sOutput or '').strip()}"
            )
    finally:
        connectionDocker.ftResultExecuteCommand(
            sContainerId, f"rm -f {fsShellQuote(sTempPath)}"
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
    return _fbValidateOverleafOnHost(sProjectId)


_S_OVERLEAF_HOST = "git.overleaf.com"


def _fbValidateOverleafOnHost(sProjectId):
    """Run git ls-remote from the host with a transient askpass helper."""
    from vaibify.config.secretManager import fbSecretExists
    if not fbSecretExists("overleaf_token", "keyring"):
        return (False, "No Overleaf token stored on host")
    sAskpass = _fsWriteAskpassScript()
    try:
        return _ftRunHostLsRemote(sProjectId, sAskpass)
    finally:
        _fnRemovePath(sAskpass)


def _ftRunHostLsRemote(sProjectId, sAskpass):
    """Execute git ls-remote under the prepared askpass and env."""
    import os
    sUrl = f"https://{_S_OVERLEAF_HOST}/{sProjectId}"
    dictEnv = os.environ.copy()
    dictEnv["GIT_ASKPASS"] = sAskpass
    dictEnv["GIT_TERMINAL_PROMPT"] = "0"
    resultProcess = subprocess.run(
        ["git", "ls-remote", sUrl, "HEAD"],
        capture_output=True, text=True, env=dictEnv,
    )
    sDetail = (resultProcess.stderr or "").strip()
    return (resultProcess.returncode == 0, sDetail)


def _fsWriteAskpassScript():
    """Write a mode-700 askpass script that prints the stored token."""
    import os
    import stat
    import sys as sysModule
    import tempfile
    sScript = (
        f"#!{sysModule.executable}\n"
        "import sys\n"
        "from vaibify.config.secretManager import fsRetrieveSecret\n"
        "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if 'Username' in prompt:\n"
        "    print('git')\n"
        "else:\n"
        "    print(fsRetrieveSecret('overleaf_token', 'keyring'))\n"
    )
    iFileDescriptor, sPath = tempfile.mkstemp(
        prefix="vc_askpass_", suffix=".py",
    )
    try:
        os.write(iFileDescriptor, sScript.encode("utf-8"))
    finally:
        os.close(iFileDescriptor)
    os.chmod(sPath, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return sPath


def _fnRemovePath(sPath):
    """Remove a file, ignoring absent paths."""
    import os
    try:
        os.remove(sPath)
    except FileNotFoundError:
        pass


def fbValidateZenodoToken(connectionDocker, sContainerId):
    """Test Zenodo token with a lightweight API call."""
    sCommand = fsPythonCommand(
        "from vaibify.reproducibility.zenodoClient "
        "import ZenodoClient",
        "c=ZenodoClient('sandbox'); "
        "c.fdictListDepositions(iSize=1); print('ok')",
    )
    iExit, sOut = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return iExit == 0 and "ok" in sOut


def fnValidateOverleafProjectId(sProjectId):
    """Raise ValueError if the Overleaf project ID is malformed."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", sProjectId):
        raise ValueError(
            f"Invalid Overleaf project ID: {sProjectId}"
        )


def _fbSafeDirectoryName(sDirectory):
    """Return True if a directory name is safe for shell embedding."""
    return bool(re.match(r'^[A-Za-z0-9_./ -]+$', sDirectory))


def fsBuildTestMarkerCheckCommand(listStepDirectories):
    """Build a docker exec command to read test markers and scan dirs."""
    listSafe = [
        s for s in listStepDirectories if _fbSafeDirectoryName(s)
    ]
    sJsonDirs = json.dumps(listSafe)
    sScript = _fsBuildTestMarkerScript(sJsonDirs)
    return "python3 -c " + fsShellQuote(sScript)


def _fsBuildTestMarkerScript(sJsonDirs):
    """Build the Python script that reads markers and scans dirs.

    All string literals use double quotes so the script survives
    single-quote shell wrapping by fsShellQuote.
    """
    return (
        "import json, os\n"
        'R = {"markers": {}, "testFiles": {}, "missingConftest": []}\n'
        'mdir = "/workspace/.vaibify/test_markers"\n'
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
        "        with open(sPath) as fh:\n"
        "            for sLine in fh:\n"
        '                m = re.match(r"^# vaibify-template-hash:'
        ' ([0-9a-f]+)", sLine)\n'
        "                if m: return m.group(1)\n"
        "    except Exception: pass\n"
        "    return None\n"
        "for d in json.loads(" + json.dumps(sJsonDirs) + "):\n"
        '    td = os.path.join(d, "tests")\n'
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
        '    if not os.path.isfile(os.path.join(td, "conftest.py")):\n'
        '        R["missingConftest"].append(d)\n'
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
    """Write DOT to container, convert to SVG, return bytes."""
    sDotContent = fsBuildDagDot(dictWorkflow, dictCachedDeps)
    sDotPath = "/tmp/_vaibify_dag.dot"
    sSvgPath = "/tmp/_vaibify_dag.svg"
    connectionDocker.fnWriteFile(
        sContainerId, sDotPath, sDotContent.encode("utf-8")
    )
    sPersistPath = "/workspace/.vaibify/dag.svg"
    sConvert = (
        f"dot -Tsvg {sDotPath} -o {sSvgPath} && "
        f"cp {sSvgPath} {sPersistPath}"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sConvert
    )
    if iExitCode != 0:
        return (iExitCode, sOutput)
    baSvg = connectionDocker.fbaFetchFile(sContainerId, sSvgPath)
    return (0, baSvg)


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
    sDotContent = fsBuildDagDot(dictWorkflow, dictCachedDeps)
    sDotPath = "/tmp/_vaibify_dag.dot"
    sOutPath = f"/tmp/_vaibify_dag.{sFormat}"
    connectionDocker.fnWriteFile(
        sContainerId, sDotPath, sDotContent.encode("utf-8")
    )
    sPersistPath = f"/workspace/.vaibify/dag.{sFormat}"
    sConvert = (
        f"dot -T{sFormat} {sDotPath} -o {sOutPath} && "
        f"cp {sOutPath} {sPersistPath}"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sConvert
    )
    if iExitCode != 0:
        return (iExitCode, sOutput)
    baContent = connectionDocker.fbaFetchFile(
        sContainerId, sOutPath
    )
    return (0, baContent)


def ftResultArchiveProject(
    connectionDocker, sContainerId, dictWorkflow,
):
    """Build a Zenodo archive with data + archive plots + metadata."""
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
        sContainerId, fsPythonCommand(sImport, sCall)
    )


_FROZENSET_OVERLEAF_EXTENSIONS = frozenset({
    ".tex", ".pdf", ".png", ".jpg", ".jpeg",
    ".eps", ".svg", ".bib",
})


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
    """Collect raw (resolved) output-file entries across every step."""
    listFiles = []
    for dictStep in dictWorkflow.get("listSteps", []):
        _fnAppendStepOutputFiles(
            dictStep, dictSyncStatus, dictVars,
            sWorkflowRoot, listFiles,
        )
    return listFiles


def _fnAppendStepOutputFiles(
    dictStep, dictSyncStatus, dictVars, sWorkflowRoot, listFiles,
):
    """Append one step's data and plot files with resolved paths."""
    for sKey in ("saDataFiles", "saPlotFiles"):
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
        "dictSync": dictSyncStatus.get(sAbsolute, {}),
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
