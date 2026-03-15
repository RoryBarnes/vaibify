"""Load, validate, and CRUD operations on script.json."""

import json
import os
import posixpath
import re


DEFAULT_SEARCH_ROOT = "/workspace"

REQUIRED_SCRIPT_KEYS = ("sPlotDirectory", "listScenes")
REQUIRED_SCENE_KEYS = ("sName", "sDirectory", "saCommands", "saOutputFiles")


def flistFindScriptsInContainer(
    connectionDocker, sContainerId, sSearchRoot=None
):
    """Search for script.json files under sSearchRoot and return paths."""
    if sSearchRoot is None:
        sSearchRoot = DEFAULT_SEARCH_ROOT
    sCommand = (
        f"find {sSearchRoot} -maxdepth 3 -name script.json"
        f" -type f 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    listPaths = [
        sLine.strip()
        for sLine in sOutput.splitlines()
        if sLine.strip().endswith("script.json")
    ]
    return sorted(listPaths)


def fdictLoadScriptFromContainer(
    connectionDocker, sContainerId, sScriptPath=None
):
    """Fetch and parse script.json from a Docker container."""
    if sScriptPath is None:
        listPaths = flistFindScriptsInContainer(
            connectionDocker, sContainerId
        )
        if not listPaths:
            raise FileNotFoundError(
                "No script.json found under search root"
            )
        sScriptPath = listPaths[0]
    baContent = connectionDocker.fbaFetchFile(sContainerId, sScriptPath)
    dictScript = json.loads(baContent.decode("utf-8"))
    if not fbValidateScript(dictScript):
        raise ValueError(f"Invalid script.json: {sScriptPath}")
    return dictScript


def fbValidateScript(dictScript):
    """Return True when all required keys and scene structures exist."""
    for sKey in REQUIRED_SCRIPT_KEYS:
        if sKey not in dictScript:
            return False
    for iIndex, dictScene in enumerate(dictScript["listScenes"]):
        for sField in REQUIRED_SCENE_KEYS:
            if sField not in dictScene:
                return False
    return True


def fsResolveVariables(sTemplate, dictVariables):
    """Replace {name} tokens in sTemplate with values from dictVariables."""

    def fnReplace(resultMatch):
        sToken = resultMatch.group(1)
        if sToken in dictVariables:
            return str(dictVariables[sToken])
        return resultMatch.group(0)

    return re.sub(r"\{([^}]+)\}", fnReplace, sTemplate)


def fdictBuildGlobalVariables(dictScript, sScriptPath):
    """Build the global variable dict from script.json top-level keys."""
    sScriptDirectory = posixpath.dirname(sScriptPath)
    return {
        "sPlotDirectory": dictScript.get("sPlotDirectory", "Plot"),
        "sRepoRoot": sScriptDirectory,
        "iNumberOfCores": dictScript.get("iNumberOfCores", -1),
        "sFigureType": dictScript.get("sFigureType", "pdf").lower(),
    }


def flistResolveOutputFiles(dictScene, dictVariables):
    """Return output file paths with template variables resolved."""
    listResolved = []
    for sPath in dictScene.get("saOutputFiles", []):
        listResolved.append(fsResolveVariables(sPath, dictVariables))
    return listResolved


def flistExtractSceneNames(dictScript):
    """Return a list of scene summary dicts."""
    listScenes = []
    for iIndex, dictScene in enumerate(dictScript["listScenes"]):
        listScenes.append(
            {
                "iIndex": iIndex,
                "iNumber": iIndex + 1,
                "sName": dictScene["sName"],
                "bEnabled": dictScene.get("bEnabled", True),
                "bPlotOnly": dictScene.get("bPlotOnly", True),
                "sDirectory": dictScene["sDirectory"],
            }
        )
    return listScenes


def fdictCreateScene(
    sName,
    sDirectory,
    bPlotOnly=True,
    saSetupCommands=None,
    saCommands=None,
    saOutputFiles=None,
):
    """Return a new scene dictionary with validated fields."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "bEnabled": True,
        "bPlotOnly": bPlotOnly,
        "saSetupCommands": saSetupCommands if saSetupCommands else [],
        "saCommands": saCommands if saCommands else [],
        "saOutputFiles": saOutputFiles if saOutputFiles else [],
    }


def fdictGetScene(dictScript, iSceneIndex):
    """Return a copy of the scene at iSceneIndex."""
    if iSceneIndex < 0 or iSceneIndex >= len(dictScript["listScenes"]):
        raise IndexError(f"Scene index {iSceneIndex} out of range")
    return dict(dictScript["listScenes"][iSceneIndex])


def fsRemapSceneReferences(sText, fnRemap):
    """Apply fnRemap to all {SceneNN.variable} tokens in sText."""

    def fnReplace(resultMatch):
        iOldNumber = int(resultMatch.group(1))
        sVariable = resultMatch.group(2)
        iNewNumber = fnRemap(iOldNumber)
        if iNewNumber == iOldNumber:
            return resultMatch.group(0)
        return "{" + f"Scene{iNewNumber:02d}" + "." + sVariable + "}"

    return re.sub(r"\{Scene(\d+)\.([^}]+)\}", fnReplace, sText)


def fnRenumberAllReferences(dictScript, fnRemap):
    """Update all {SceneNN.*} references in every scene per fnRemap."""
    for dictScene in dictScript["listScenes"]:
        for sKey in ("saSetupCommands", "saCommands", "saOutputFiles"):
            if sKey in dictScene and dictScene[sKey]:
                dictScene[sKey] = [
                    fsRemapSceneReferences(sItem, fnRemap)
                    for sItem in dictScene[sKey]
                ]


def fnInsertScene(dictScript, iPosition, dictScene):
    """Insert a scene at iPosition, renumbering downstream references."""

    def fnRemap(iSceneNumber):
        if iSceneNumber >= iPosition + 1:
            return iSceneNumber + 1
        return iSceneNumber

    fnRenumberAllReferences(dictScript, fnRemap)
    dictScript["listScenes"].insert(iPosition, dictScene)


def fnUpdateScene(dictScript, iSceneIndex, dictUpdates):
    """Update scene at iSceneIndex with dictUpdates."""
    if iSceneIndex < 0 or iSceneIndex >= len(dictScript["listScenes"]):
        raise IndexError(f"Scene index {iSceneIndex} out of range")
    dictScene = dictScript["listScenes"][iSceneIndex]
    for sKey, value in dictUpdates.items():
        dictScene[sKey] = value


def fnDeleteScene(dictScript, iSceneIndex):
    """Remove scene at iSceneIndex, renumbering references."""
    if iSceneIndex < 0 or iSceneIndex >= len(dictScript["listScenes"]):
        raise IndexError(f"Scene index {iSceneIndex} out of range")
    iDeletedNumber = iSceneIndex + 1

    def fnRemap(iSceneNumber):
        if iSceneNumber > iDeletedNumber:
            return iSceneNumber - 1
        return iSceneNumber

    dictScript["listScenes"].pop(iSceneIndex)
    fnRenumberAllReferences(dictScript, fnRemap)


def _fnValidateReorderIndices(iFromIndex, iToIndex, iMaxIndex):
    """Raise IndexError if either reorder index is out of range."""
    if iFromIndex < 0 or iFromIndex > iMaxIndex:
        raise IndexError(f"From index {iFromIndex} out of range")
    if iToIndex < 0 or iToIndex > iMaxIndex:
        raise IndexError(f"To index {iToIndex} out of range")


def _fiRemapReorder(iSceneNumber, iFromNumber, iFromIndex, iToIndex):
    """Return the remapped scene number for a reorder operation."""
    if iSceneNumber == iFromNumber:
        return iToIndex + 1
    if iFromIndex < iToIndex:
        if iFromNumber < iSceneNumber <= iToIndex + 1:
            return iSceneNumber - 1
    elif iFromIndex > iToIndex:
        if iToIndex + 1 <= iSceneNumber < iFromNumber:
            return iSceneNumber + 1
    return iSceneNumber


def fnReorderScene(dictScript, iFromIndex, iToIndex):
    """Move a scene from iFromIndex to iToIndex, renumbering references."""
    listScenes = dictScript["listScenes"]
    _fnValidateReorderIndices(iFromIndex, iToIndex, len(listScenes) - 1)
    iFromNumber = iFromIndex + 1

    def fnRemap(iSceneNumber):
        return _fiRemapReorder(
            iSceneNumber, iFromNumber, iFromIndex, iToIndex
        )

    dictScene = listScenes.pop(iFromIndex)
    listScenes.insert(iToIndex, dictScene)
    fnRenumberAllReferences(dictScript, fnRemap)


def fnSaveScriptToContainer(
    connectionDocker, sContainerId, dictScript, sScriptPath=None
):
    """Serialize dictScript to JSON and write to container."""
    if sScriptPath is None:
        raise ValueError("sScriptPath is required for saving")
    sJson = json.dumps(dictScript, indent=2) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sScriptPath, sJson.encode("utf-8")
    )


def fsetExtractSceneReferences(sText):
    """Return all {SceneNN.variable} tokens found in sText as tuples."""
    return set(re.findall(r"\{Scene(\d+)\.([^}]+)\}", sText))


def fdictBuildStemRegistry(dictScript):
    """Map each SceneNN.stem to the scene that produces it."""
    dictRegistry = {}
    for iIndex, dictScene in enumerate(dictScript["listScenes"]):
        iNumber = iIndex + 1
        for sOutputFile in dictScene.get("saOutputFiles", []):
            sBasename = posixpath.basename(sOutputFile)
            sStem = posixpath.splitext(sBasename)[0]
            sKey = f"Scene{iNumber:02d}.{sStem}"
            dictRegistry[sKey] = iNumber
    return dictRegistry


def flistValidateReferences(dictScript):
    """Return a list of warnings about cross-scene reference problems."""
    dictRegistry = fdictBuildStemRegistry(dictScript)
    listWarnings = []

    for iIndex, dictScene in enumerate(dictScript["listScenes"]):
        iNumber = iIndex + 1
        sSceneLabel = f"Scene{iNumber:02d}"

        for sKey in ("saSetupCommands", "saCommands"):
            for sCommand in dictScene.get(sKey, []):
                _fnCheckCommandReferences(
                    sCommand, sSceneLabel, iNumber,
                    dictScript, dictRegistry, listWarnings,
                )

    return listWarnings


def _fsClassifyReference(iRefNumber, sRefKey, iNumber, iSceneCount, dictRegistry):
    """Return a warning suffix string or empty string if reference is valid."""
    if iRefNumber > iSceneCount:
        return "points beyond the last scene"
    if sRefKey not in dictRegistry:
        return f"has no matching output file in Scene{iRefNumber:02d}"
    if iRefNumber >= iNumber:
        return "points to a later scene (circular dependency)"
    return ""


def _fnCheckCommandReferences(
    sCommand, sSceneLabel, iNumber,
    dictScript, dictRegistry, listWarnings,
):
    """Append warnings for invalid references in a single command."""
    iSceneCount = len(dictScript["listScenes"])
    for sRefNumber, sRefVariable in fsetExtractSceneReferences(sCommand):
        iRefNumber = int(sRefNumber)
        sRefKey = f"Scene{iRefNumber:02d}.{sRefVariable}"
        sSuffix = _fsClassifyReference(
            iRefNumber, sRefKey, iNumber, iSceneCount, dictRegistry
        )
        if sSuffix:
            listWarnings.append(
                f"{sSceneLabel}: reference {{{sRefKey}}} {sSuffix}"
            )


def flistFilterFigureFiles(listOutputPaths):
    """Return only paths ending in figure extensions."""
    setFigureExtensions = {".pdf", ".png", ".jpg", ".jpeg", ".svg"}
    listFigures = []
    for sPath in listOutputPaths:
        sExtension = os.path.splitext(sPath)[1].lower()
        if sExtension in setFigureExtensions:
            listFigures.append(sPath)
    return listFigures


def flistExtractOutputFiles(dictScene):
    """Return list of output file paths for a scene."""
    return list(dictScene.get("saOutputFiles", []))
