"""Dispatch sync operations to run inside Docker containers."""

import posixpath
import re
import uuid

from .pipelineRunner import fsShellQuote

VALID_SERVICES = {"github", "overleaf", "zenodo"}
VALID_TOKEN_NAMES = {"overleaf_token", "zenodo_token", "gh_token"}


def fnValidateServiceName(sService):
    """Raise ValueError if sService is not a known service."""
    if sService not in VALID_SERVICES:
        raise ValueError(f"Invalid service: {sService}")


def fsPythonCommand(sImportLine, sFunctionCall):
    """Build a python3 -c command string from import and call."""
    return f'python3 -c "{sImportLine}; {sFunctionCall}"'


def ftResultPushToOverleaf(
    connectionDocker, sContainerId,
    listFilePaths, sProjectId, sTargetDirectory,
):
    """Push figure files to Overleaf inside the container."""
    fnValidateOverleafProjectId(sProjectId)
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
    listQuoted = " ".join(fsShellQuote(s) for s in listFilePaths)
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git add {listQuoted} && "
        f"git commit -m {fsShellQuote(sCommitMessage)} && "
        f"git push && "
        f"git rev-parse --short HEAD"
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
    """Check GitHub auth via credential helper, token, or gh CLI."""
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        "git config --get credential.https://github.com.helper "
        ">/dev/null 2>&1 || "
        "test -f /run/secrets/gh_token || "
        "gh auth status >/dev/null 2>&1"
    )
    bConnected = iExitCode == 0
    sMessage = "Connected" if bConnected else "No GitHub credentials"
    return {"bConnected": bConnected, "sMessage": sMessage}


def _fdictCheckKeyring(
    connectionDocker, sContainerId, sTokenName,
):
    """Check if a keyring token exists inside the container."""
    if sTokenName not in VALID_TOKEN_NAMES:
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
    if sName not in VALID_TOKEN_NAMES:
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


def fnValidateOverleafProjectId(sProjectId):
    """Raise ValueError if the Overleaf project ID is malformed."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", sProjectId):
        raise ValueError(
            f"Invalid Overleaf project ID: {sProjectId}"
        )


def fsBuildDagDot(dictWorkflow):
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
    setEdges = set()
    for iIndex, dictStep in enumerate(listSteps):
        sTarget = f"step{iIndex + 1}"
        for sKey in ("saDataCommands", "saPlotCommands",
                      "saTestCommands"):
            for sCmd in dictStep.get(sKey, []):
                for match in re.finditer(
                    r"\{Step(\d+)\.\w+\}", sCmd
                ):
                    iSource = int(match.group(1))
                    sEdge = f"  step{iSource} -> {sTarget};"
                    if sEdge not in setEdges:
                        setEdges.add(sEdge)
                        listLines.append(sEdge)
    listLines.append("}")
    return "\n".join(listLines)


def ftResultGenerateDagSvg(
    connectionDocker, sContainerId, dictWorkflow,
):
    """Write DOT to container, convert to SVG, return bytes."""
    sDotContent = fsBuildDagDot(dictWorkflow)
    sDotPath = "/tmp/_vaibify_dag.dot"
    sSvgPath = "/tmp/_vaibify_dag.svg"
    connectionDocker.fnWriteFile(
        sContainerId, sDotPath, sDotContent.encode("utf-8")
    )
    sConvert = f"dot -Tsvg {sDotPath} -o {sSvgPath}"
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sConvert
    )
    if iExitCode != 0:
        return (iExitCode, sOutput)
    baSvg = connectionDocker.fbaFetchFile(sContainerId, sSvgPath)
    return (0, baSvg)


def ftResultGenerateActions(
    connectionDocker, sContainerId, sOutputPath,
):
    """Generate GitHub Actions workflow YAML inside the container."""
    sImport = (
        "from vaibify.reproducibility.githubWorkflow "
        "import fnWriteWorkflow"
    )
    sCall = f"fnWriteWorkflow({{}}, {repr(sOutputPath)})"
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, fsPythonCommand(sImport, sCall)
    )


def _fsHashFileCommand(sPath):
    """Build a shell-safe hash command for a file path."""
    return (
        f"python3 -c \"import hashlib; "
        f"print(hashlib.sha256("
        f"open({repr(sPath)},'rb').read()).hexdigest())\""
    )


def _fsNormalizePath(sDirectory, sScript):
    """Normalize a script path relative to a directory."""
    if sScript.startswith("/"):
        return sScript
    sJoined = posixpath.join(sDirectory, sScript)
    return posixpath.normpath(sJoined)


def fbStepInputsUnchanged(
    connectionDocker, sContainerId, dictStep, iStepNumber,
):
    """Check if a step's inputs have changed since last run."""
    sDirectory = dictStep.get("sDirectory", "")
    dictRunStats = dictStep.get("dictRunStats", {})
    dictHashes = dictRunStats.get("dictInputHashes", {})
    if not dictHashes:
        return False
    listScripts = _flistExtractScripts(dictStep)
    for sScript in listScripts:
        sPath = _fsNormalizePath(sDirectory, sScript)
        sCommand = _fsHashFileCommand(sPath)
        iExit, sHash = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand
        )
        sHash = sHash.strip()
        if iExit != 0 or dictHashes.get(sPath) != sHash:
            return False
    return True


def _flistExtractScripts(dictStep):
    """Extract script paths from data analysis commands."""
    listScripts = []
    for sCommand in dictStep.get("saDataCommands", []):
        listTokens = sCommand.split()
        if not listTokens:
            continue
        if listTokens[0] in ("python", "python3"):
            if len(listTokens) > 1:
                listScripts.append(listTokens[1])
        elif listTokens[0].endswith(".py"):
            listScripts.append(listTokens[0])
    return listScripts


def fdictComputeInputHashes(
    connectionDocker, sContainerId, dictStep,
):
    """Compute SHA-256 hashes of a step's input scripts."""
    sDirectory = dictStep.get("sDirectory", "")
    dictHashes = {}
    for sScript in _flistExtractScripts(dictStep):
        sPath = _fsNormalizePath(sDirectory, sScript)
        sCommand = _fsHashFileCommand(sPath)
        iExit, sHash = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand
        )
        if iExit == 0:
            dictHashes[sPath] = sHash.strip()
    return dictHashes
