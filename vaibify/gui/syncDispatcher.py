"""Dispatch sync operations to run inside Docker containers."""

import json
import posixpath
import re
import uuid

from . import workflowManager
from .pipelineUtils import fsShellQuote

__all__ = [
    "fbStepInputsUnchanged",
    "fbValidateOverleafCredentials",
    "fbValidateZenodoToken",
    "fdictCheckConnectivity",
    "fdictClassifyError",
    "fdictComputeAllScriptHashes",
    "fdictComputeInputHashes",
    "fdictParseTestMarkerOutput",
    "fdictSyncResult",
    "flistCollectOutputFiles",
    "flistExtractAllScriptPaths",
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


def _ftResultPlainPush(
    connectionDocker, sContainerId,
    listFilePaths, sProjectId, sTargetDirectory,
):
    """Push figures without TeX annotation."""
    sImport = (
        "from vaibify.reproducibility.overleafSync "
        "import fnPushFiguresToOverleaf"
    )
    sCall = (
        f"fnPushFiguresToOverleaf("
        f"{repr(listFilePaths)}, "
        f"{repr(sProjectId)}, "
        f"{repr(sTargetDirectory)})"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall)
    )


def _ftResultAnnotatedPush(
    connectionDocker, sContainerId, listFilePaths,
    sProjectId, sTargetDirectory, dictWorkflow,
    sGithubBaseUrl, sDoi, sTexFilename,
):
    """Push figures and annotate the TeX with source links."""
    import json
    sWorkflowJson = json.dumps(dictWorkflow)
    sImport = (
        "import json; "
        "from vaibify.reproducibility.overleafSync "
        "import fnPushAnnotatedToOverleaf"
    )
    sCall = (
        f"fnPushAnnotatedToOverleaf("
        f"{repr(listFilePaths)}, "
        f"{repr(sProjectId)}, "
        f"{repr(sTargetDirectory)}, "
        f"json.loads({repr(sWorkflowJson)}), "
        f"{repr(sGithubBaseUrl)}, "
        f"{repr(sDoi)}, "
        f"{repr(sTexFilename)})"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall)
    )


def ftResultPullFromOverleaf(
    connectionDocker, sContainerId,
    sProjectId, listPullPaths, sTargetDirectory,
):
    """Pull TeX files from Overleaf inside the container."""
    fnValidateOverleafProjectId(sProjectId)
    sImport = (
        "from vaibify.reproducibility.overleafSync "
        "import fnPullTexFromOverleaf"
    )
    sCall = (
        f"fnPullTexFromOverleaf("
        f"{repr(sProjectId)}, "
        f"{repr(listPullPaths)}, "
        f"{repr(sTargetDirectory)})"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall)
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
        return _fdictCheckKeyring(
            connectionDocker, sContainerId, "overleaf_token"
        )
    if sService == "zenodo":
        return _fdictCheckKeyring(
            connectionDocker, sContainerId, "zenodo_token"
        )
    return {"bConnected": False, "sMessage": "Unknown service"}


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


def _fdictCheckKeyring(
    connectionDocker, sContainerId, sTokenName,
):
    """Check if a keyring token exists inside the container."""
    if sTokenName not in SET_VALID_TOKEN_NAMES:
        raise ValueError(f"Invalid token name: {sTokenName}")
    sCommand = fsPythonCommand(
        "import keyring",
        f"t=keyring.get_password('vaibify',{repr(sTokenName)}); "
        f"print('ok' if t else 'missing')",
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    bConnected = "ok" in sOutput
    return {
        "bConnected": bConnected,
        "sMessage": "Connected" if bConnected else "Token not found",
    }


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
        connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand
        )
    finally:
        connectionDocker.ftResultExecuteCommand(
            sContainerId, f"rm -f {fsShellQuote(sTempPath)}"
        )


def fbValidateOverleafCredentials(
    connectionDocker, sContainerId, sProjectId,
):
    """Test Overleaf credentials with git ls-remote."""
    fnValidateOverleafProjectId(sProjectId)
    sCommand = (
        f"git ls-remote "
        f"https://git.overleaf.com/{sProjectId} "
        f"HEAD >/dev/null 2>&1"
    )
    iExit, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return iExit == 0


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


def flistCollectOutputFiles(dictWorkflow, dictSyncStatus):
    """Collect all output files with their sync and category info."""
    listFiles = []
    for dictStep in dictWorkflow.get("listSteps", []):
        for sKey in ("saDataFiles", "saPlotFiles"):
            for sFile in dictStep.get(sKey, []):
                dictSync = dictSyncStatus.get(sFile, {})
                sCategory = "archive"
                if sKey == "saPlotFiles":
                    sCategory = workflowManager.fsGetPlotCategory(
                        dictStep, sFile)
                listFiles.append({
                    "sPath": sFile,
                    "sCategory": sCategory,
                    "dictSync": dictSync,
                })
    return listFiles


# Re-export file integrity functions for backward compatibility.
# Canonical implementations live in fileIntegrity.py.
from .fileIntegrity import (  # noqa: F401
    _fdictParseHashOutput,
    _fsHashFileCommand,
    _fsNormalizePath,
    fbStepInputsUnchanged,
    fdictComputeAllScriptHashes,
    fdictComputeInputHashes,
    flistExtractAllScriptPaths,
)
