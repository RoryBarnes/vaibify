"""File integrity checks: script hashing, path normalization, change detection."""

import posixpath


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


def _flistExtractScripts(dictStep):
    """Extract script paths from data and plot commands."""
    from .commandUtilities import flistExtractScripts
    listAll = (
        dictStep.get("saDataCommands", [])
        + dictStep.get("saPlotCommands", [])
    )
    return flistExtractScripts(listAll)


def _fdictParseHashOutput(sOutput):
    """Parse 'path hash' lines into a dictionary."""
    dictHashes = {}
    for sLine in (sOutput or "").strip().split("\n"):
        sLine = sLine.strip()
        if not sLine:
            continue
        listParts = sLine.rsplit(" ", 1)
        if len(listParts) == 2 and listParts[1] != "MISSING":
            dictHashes[listParts[0]] = listParts[1]
    return dictHashes


def flistExtractAllScriptPaths(dictWorkflow):
    """Extract all unique script paths from all steps."""
    from .commandUtilities import flistExtractScripts
    listAllPaths = []
    setAdded = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        sDirectory = dictStep.get("sDirectory", "")
        for sKey in ("saDataCommands", "saPlotCommands"):
            for sScript in flistExtractScripts(
                dictStep.get(sKey, [])
            ):
                sPath = _fsNormalizePath(sDirectory, sScript)
                if sPath not in setAdded:
                    listAllPaths.append(sPath)
                    setAdded.add(sPath)
    return listAllPaths


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


def fdictComputeAllScriptHashes(
    connectionDocker, sContainerId, dictWorkflow,
):
    """Compute SHA-256 hashes of all scripts in one Docker exec."""
    listAllPaths = flistExtractAllScriptPaths(dictWorkflow)
    if not listAllPaths:
        return {}
    sCommand = (
        "python3 -c \"import hashlib,os,sys; "
        "[print(p + ' ' + hashlib.sha256(open(p,'rb').read())"
        ".hexdigest() "
        "if os.path.isfile(p) else p + ' MISSING') "
        "for p in " + repr(listAllPaths) + "]\""
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExit != 0:
        return {}
    return _fdictParseHashOutput(sOutput)
