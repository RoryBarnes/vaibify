"""Load, validate, and CRUD operations on workflow.json."""

import json
import os
import posixpath
import re


DEFAULT_SEARCH_ROOT = "/workspace"

VAIBIFY_DIRECTORY = ".vaibify"
VAIBIFY_WORKFLOWS_DIR = ".vaibify/workflows"
VAIBIFY_LOGS_DIR = ".vaibify/logs"

REQUIRED_WORKFLOW_KEYS = ("sPlotDirectory", "listSteps")
REQUIRED_STEP_KEYS = ("sName", "sDirectory", "saPlotCommands", "saPlotFiles")


def flistFindWorkflowsInContainer(
    connectionDocker, sContainerId, sSearchRoot=None
):
    """Search all repos in workspace for workflow JSON files."""
    if sSearchRoot is None:
        sSearchRoot = DEFAULT_SEARCH_ROOT
    sCommand = (
        f"find {sSearchRoot} -maxdepth 4"
        f" -path '*/.vaibify/workflows/*.json'"
        f" -type f 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    listResults = []
    for sLine in sOutput.splitlines():
        sPath = sLine.strip()
        if sPath.endswith(".json"):
            sRepoName = _fsExtractRepoName(sPath, sSearchRoot)
            sName = _fsReadWorkflowName(
                connectionDocker, sContainerId, sPath
            )
            listResults.append({
                "sPath": sPath,
                "sName": sName,
                "sRepoName": sRepoName,
            })
    return sorted(listResults, key=lambda d: d["sName"])


def _fsExtractRepoName(sWorkflowPath, sSearchRoot):
    """Extract the repo directory name from a workflow path."""
    sRelative = sWorkflowPath.replace(sSearchRoot + "/", "", 1)
    listParts = sRelative.split("/")
    if listParts[0] == ".vaibify":
        return "(container root)"
    return listParts[0]


def _fsReadWorkflowName(connectionDocker, sContainerId, sPath):
    """Read sWorkflowName from a workflow JSON file in the container."""
    try:
        baContent = connectionDocker.fbaFetchFile(sContainerId, sPath)
        dictWorkflow = json.loads(baContent.decode("utf-8"))
        return dictWorkflow.get(
            "sWorkflowName", posixpath.basename(sPath)
        )
    except Exception:
        return posixpath.basename(sPath)


def fdictLoadWorkflowFromContainer(
    connectionDocker, sContainerId, sWorkflowPath=None
):
    """Fetch and parse workflow.json from a Docker container."""
    if sWorkflowPath is None:
        listWorkflows = flistFindWorkflowsInContainer(
            connectionDocker, sContainerId
        )
        if not listWorkflows:
            raise FileNotFoundError(
                "No workflow.json found under search root"
            )
        sWorkflowPath = listWorkflows[0]["sPath"]
    baContent = connectionDocker.fbaFetchFile(sContainerId, sWorkflowPath)
    dictWorkflow = json.loads(baContent.decode("utf-8"))
    if not fbValidateWorkflow(dictWorkflow):
        raise ValueError(f"Invalid workflow.json: {sWorkflowPath}")
    return dictWorkflow


def fbValidateWorkflow(dictWorkflow):
    """Return True when all required keys and step structures exist."""
    for sKey in REQUIRED_WORKFLOW_KEYS:
        if sKey not in dictWorkflow:
            return False
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        for sField in REQUIRED_STEP_KEYS:
            if sField not in dictStep:
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


def fdictBuildGlobalVariables(dictWorkflow, sWorkflowPath):
    """Build the global variable dict from workflow.json top-level keys."""
    sWorkflowDirectory = posixpath.dirname(sWorkflowPath)
    sRepoRoot = sWorkflowDirectory
    if "/.vaibify" in sRepoRoot:
        sRepoRoot = sRepoRoot[:sRepoRoot.index("/.vaibify")]
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sRepoRoot": sRepoRoot,
        "iNumberOfCores": dictWorkflow.get("iNumberOfCores", -1),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf").lower(),
    }


def flistResolveOutputFiles(dictStep, dictVariables):
    """Return output file paths with template variables resolved."""
    listResolved = []
    for sPath in dictStep.get("saPlotFiles", []):
        listResolved.append(fsResolveVariables(sPath, dictVariables))
    return listResolved


def flistExtractStepNames(dictWorkflow):
    """Return a list of step summary dicts."""
    listSteps = []
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        listSteps.append(
            {
                "iIndex": iIndex,
                "iNumber": iIndex + 1,
                "sName": dictStep["sName"],
                "bEnabled": dictStep.get("bEnabled", True),
                "bPlotOnly": dictStep.get("bPlotOnly", True),
                "sDirectory": dictStep["sDirectory"],
            }
        )
    return listSteps


VERIFICATION_STATES = ("untested", "passed", "failed", "error")


def fbStepRequiresTests(dictStep):
    """Return True when data commands exist but no test commands."""
    bHasData = len(dictStep.get("saDataCommands", [])) > 0
    bHasTests = len(dictStep.get("saTestCommands", [])) > 0
    return bHasData and not bHasTests


def fdictCreateStep(
    sName,
    sDirectory,
    bPlotOnly=True,
    saDataCommands=None,
    saDataFiles=None,
    saTestCommands=None,
    saPlotCommands=None,
    saPlotFiles=None,
):
    """Return a new step dictionary with validated fields."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "bEnabled": True,
        "bPlotOnly": bPlotOnly,
        "saDataCommands": saDataCommands if saDataCommands else [],
        "saDataFiles": saDataFiles if saDataFiles else [],
        "saTestCommands": saTestCommands if saTestCommands else [],
        "saPlotCommands": saPlotCommands if saPlotCommands else [],
        "saPlotFiles": saPlotFiles if saPlotFiles else [],
        "dictVerification": {
            "sUnitTest": "untested",
            "sUser": "untested",
        },
        "dictRunStats": {},
    }


def fdictGetStep(dictWorkflow, iStepIndex):
    """Return a copy of the step at iStepIndex."""
    if iStepIndex < 0 or iStepIndex >= len(dictWorkflow["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    return dict(dictWorkflow["listSteps"][iStepIndex])


def fsRemapStepReferences(sText, fnRemap):
    """Apply fnRemap to all {StepNN.variable} tokens in sText."""

    def fnReplace(resultMatch):
        iOldNumber = int(resultMatch.group(1))
        sVariable = resultMatch.group(2)
        iNewNumber = fnRemap(iOldNumber)
        if iNewNumber == iOldNumber:
            return resultMatch.group(0)
        return "{" + f"Step{iNewNumber:02d}" + "." + sVariable + "}"

    return re.sub(r"\{Step(\d+)\.([^}]+)\}", fnReplace, sText)


def fnRenumberAllReferences(dictWorkflow, fnRemap):
    """Update all {StepNN.*} references in every step per fnRemap."""
    for dictStep in dictWorkflow["listSteps"]:
        for sKey in ("saDataCommands", "saTestCommands",
                     "saPlotCommands", "saPlotFiles"):
            if sKey in dictStep and dictStep[sKey]:
                dictStep[sKey] = [
                    fsRemapStepReferences(sItem, fnRemap)
                    for sItem in dictStep[sKey]
                ]


def fnInsertStep(dictWorkflow, iPosition, dictStep):
    """Insert a step at iPosition, renumbering downstream references."""

    def fnRemap(iStepNumber):
        if iStepNumber >= iPosition + 1:
            return iStepNumber + 1
        return iStepNumber

    fnRenumberAllReferences(dictWorkflow, fnRemap)
    dictWorkflow["listSteps"].insert(iPosition, dictStep)


def fnUpdateStep(dictWorkflow, iStepIndex, dictUpdates):
    """Update step at iStepIndex with dictUpdates."""
    if iStepIndex < 0 or iStepIndex >= len(dictWorkflow["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    for sKey, value in dictUpdates.items():
        dictStep[sKey] = value


def fnDeleteStep(dictWorkflow, iStepIndex):
    """Remove step at iStepIndex, renumbering references."""
    if iStepIndex < 0 or iStepIndex >= len(dictWorkflow["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    iDeletedNumber = iStepIndex + 1

    def fnRemap(iStepNumber):
        if iStepNumber > iDeletedNumber:
            return iStepNumber - 1
        return iStepNumber

    dictWorkflow["listSteps"].pop(iStepIndex)
    fnRenumberAllReferences(dictWorkflow, fnRemap)


def _fnValidateReorderIndices(iFromIndex, iToIndex, iMaxIndex):
    """Raise IndexError if either reorder index is out of range."""
    if iFromIndex < 0 or iFromIndex > iMaxIndex:
        raise IndexError(f"From index {iFromIndex} out of range")
    if iToIndex < 0 or iToIndex > iMaxIndex:
        raise IndexError(f"To index {iToIndex} out of range")


def _fiRemapReorder(iStepNumber, iFromNumber, iFromIndex, iToIndex):
    """Return the remapped step number for a reorder operation."""
    if iStepNumber == iFromNumber:
        return iToIndex + 1
    if iFromIndex < iToIndex:
        if iFromNumber < iStepNumber <= iToIndex + 1:
            return iStepNumber - 1
    elif iFromIndex > iToIndex:
        if iToIndex + 1 <= iStepNumber < iFromNumber:
            return iStepNumber + 1
    return iStepNumber


def fnReorderStep(dictWorkflow, iFromIndex, iToIndex):
    """Move a step from iFromIndex to iToIndex, renumbering references."""
    listSteps = dictWorkflow["listSteps"]
    _fnValidateReorderIndices(iFromIndex, iToIndex, len(listSteps) - 1)
    iFromNumber = iFromIndex + 1

    def fnRemap(iStepNumber):
        return _fiRemapReorder(
            iStepNumber, iFromNumber, iFromIndex, iToIndex
        )

    dictStep = listSteps.pop(iFromIndex)
    listSteps.insert(iToIndex, dictStep)
    fnRenumberAllReferences(dictWorkflow, fnRemap)


def fnSaveWorkflowToContainer(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath=None
):
    """Serialize dictWorkflow to JSON and write to container."""
    if sWorkflowPath is None:
        raise ValueError("sWorkflowPath is required for saving")
    sJson = json.dumps(dictWorkflow, indent=2) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sWorkflowPath, sJson.encode("utf-8")
    )


def fsetExtractStepReferences(sText):
    """Return all {StepNN.variable} tokens found in sText as tuples."""
    return set(re.findall(r"\{Step(\d+)\.([^}]+)\}", sText))


def fdictBuildStemRegistry(dictWorkflow):
    """Map each StepNN.stem to the step that produces it."""
    dictRegistry = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        listAllOutputs = (
            dictStep.get("saDataFiles", [])
            + dictStep.get("saPlotFiles", [])
        )
        for sOutputFile in listAllOutputs:
            sBasename = posixpath.basename(sOutputFile)
            sStem = posixpath.splitext(sBasename)[0]
            sKey = f"Step{iNumber:02d}.{sStem}"
            dictRegistry[sKey] = iNumber
    return dictRegistry


def flistValidateReferences(dictWorkflow):
    """Return a list of warnings about cross-step reference problems."""
    dictRegistry = fdictBuildStemRegistry(dictWorkflow)
    listWarnings = []

    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepLabel = f"Step{iNumber:02d}"

        for sKey in ("saDataCommands", "saTestCommands",
                     "saPlotCommands"):
            for sCommand in dictStep.get(sKey, []):
                _fnCheckCommandReferences(
                    sCommand, sStepLabel, iNumber,
                    dictWorkflow, dictRegistry, listWarnings,
                )

    return listWarnings


def _fsClassifyReference(iRefNumber, sRefKey, iNumber, iStepCount, dictRegistry):
    """Return a warning suffix string or empty string if reference is valid."""
    if iRefNumber > iStepCount:
        return "points beyond the last step"
    if sRefKey not in dictRegistry:
        return f"has no matching output file in Step{iRefNumber:02d}"
    if iRefNumber >= iNumber:
        return "points to a later step (circular dependency)"
    return ""


def _fnCheckCommandReferences(
    sCommand, sStepLabel, iNumber,
    dictWorkflow, dictRegistry, listWarnings,
):
    """Append warnings for invalid references in a single command."""
    iStepCount = len(dictWorkflow["listSteps"])
    for sRefNumber, sRefVariable in fsetExtractStepReferences(sCommand):
        iRefNumber = int(sRefNumber)
        sRefKey = f"Step{iRefNumber:02d}.{sRefVariable}"
        sSuffix = _fsClassifyReference(
            iRefNumber, sRefKey, iNumber, iStepCount, dictRegistry
        )
        if sSuffix:
            listWarnings.append(
                f"{sStepLabel}: reference {{{sRefKey}}} {sSuffix}"
            )


def fdictBuildStepVariables(dictWorkflow, dictGlobalVars):
    """Map StepNN.stem to resolved absolute output paths."""
    dictStepVars = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepDirectory = dictStep.get("sDirectory", "")
        listAllOutputs = (
            dictStep.get("saDataFiles", [])
            + dictStep.get("saPlotFiles", [])
        )
        for sOutputFile in listAllOutputs:
            sResolved = fsResolveVariables(sOutputFile, dictGlobalVars)
            sAbsPath = _fsResolveStepOutputPath(
                sResolved, sStepDirectory, dictGlobalVars
            )
            sStem = posixpath.splitext(posixpath.basename(sAbsPath))[0]
            sKey = f"Step{iNumber:02d}.{sStem}"
            dictStepVars[sKey] = sAbsPath
    return dictStepVars


def _fsResolveStepOutputPath(sResolvedFile, sStepDirectory, dictGlobalVars):
    """Return an absolute path for a step output file."""
    if posixpath.isabs(sResolvedFile):
        return sResolvedFile
    sResolvedDir = fsResolveVariables(sStepDirectory, dictGlobalVars)
    sRepoRoot = dictGlobalVars.get("sRepoRoot", "")
    return posixpath.join(sRepoRoot, sResolvedDir, sResolvedFile)


def fsResolveCommand(sCommand, dictVariables):
    """Resolve template variables in a command string."""
    return fsResolveVariables(sCommand, dictVariables)


def flistFilterFigureFiles(listOutputPaths):
    """Return only paths ending in figure extensions."""
    setFigureExtensions = {".pdf", ".png", ".jpg", ".jpeg", ".svg"}
    listFigures = []
    for sPath in listOutputPaths:
        sExtension = posixpath.splitext(sPath)[1].lower()
        if sExtension in setFigureExtensions:
            listFigures.append(sPath)
    return listFigures


def flistExtractOutputFiles(dictStep):
    """Return list of output file paths for a step."""
    return list(dictStep.get("saPlotFiles", []))


# ---------------------------------------------------------------------------
# Figure categorization — archive vs. supporting
# ---------------------------------------------------------------------------


def fsGetPlotCategory(dictStep, sFilePath):
    """Return 'archive' or 'supporting' for a plot file."""
    dictCategories = dictStep.get("dictPlotFileCategories", {})
    return dictCategories.get(sFilePath, "archive")


def flistCollectArchivePlots(dictWorkflow):
    """Return all plot files categorized as archive."""
    listArchive = []
    for dictStep in dictWorkflow.get("listSteps", []):
        for sFile in dictStep.get("saPlotFiles", []):
            if fsGetPlotCategory(dictStep, sFile) == "archive":
                listArchive.append(sFile)
    return listArchive


def flistCollectSupportingPlots(dictWorkflow):
    """Return all plot files categorized as supporting."""
    listSupporting = []
    for dictStep in dictWorkflow.get("listSteps", []):
        for sFile in dictStep.get("saPlotFiles", []):
            if fsGetPlotCategory(dictStep, sFile) == "supporting":
                listSupporting.append(sFile)
    return listSupporting


# ---------------------------------------------------------------------------
# Step-to-directory mapping
# ---------------------------------------------------------------------------


def fsCamelCaseDirectory(sStepName):
    """Convert a step name to a camelCase directory name."""
    import re
    listWords = sStepName.split()
    listCapitalized = [
        sWord.capitalize() for sWord in listWords
    ]
    sJoined = "".join(listCapitalized)
    return re.sub(r"[^a-zA-Z0-9]", "", sJoined)


def flistExtractStepScripts(dictStep):
    """Extract .py script paths from data and plot commands."""
    listScripts = []
    for sKey in ("saDataCommands", "saPlotCommands"):
        for sCommand in dictStep.get(sKey, []):
            listTokens = sCommand.split()
            if not listTokens:
                continue
            if listTokens[0] in ("python", "python3"):
                if len(listTokens) > 1:
                    listScripts.append(listTokens[1])
            elif listTokens[0].endswith(".py"):
                listScripts.append(listTokens[0])
    return listScripts


def fdictAutoDetectScripts(listFileNames):
    """Classify files by data*/plot* prefix convention."""
    listDataScripts = []
    listPlotScripts = []
    for sName in listFileNames:
        sLower = sName.lower()
        if not sLower.endswith(".py"):
            continue
        sBase = os.path.basename(sName)
        if sBase.lower().startswith("data"):
            listDataScripts.append(sName)
        elif sBase.lower().startswith("plot"):
            listPlotScripts.append(sName)
    return {
        "listDataScripts": listDataScripts,
        "listPlotScripts": listPlotScripts,
    }


def fdictBuildStepDirectoryMap(dictWorkflow):
    """Map step indices to camelCase directory names."""
    dictMap = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sName = dictStep.get("sName", f"Step{iIndex + 1}")
        dictMap[iIndex] = fsCamelCaseDirectory(sName)
    return dictMap


# ---------------------------------------------------------------------------
# Sync status tracking
# ---------------------------------------------------------------------------


def fdictGetSyncStatus(dictWorkflow):
    """Return the sync status dict, defaulting to empty."""
    return dictWorkflow.get("dictSyncStatus", {})


def fdictInitializeSyncEntry():
    """Return a fresh sync entry with all services unsynced."""
    return {
        "bOverleaf": False, "sOverleafTimestamp": "",
        "bGithub": False, "sGithubTimestamp": "",
        "bZenodo": False, "sZenodoTimestamp": "",
    }


def fnUpdateSyncStatus(dictWorkflow, listFilePaths, sService):
    """Mark files as synced to sService with current timestamp."""
    from datetime import datetime, timezone

    if "dictSyncStatus" not in dictWorkflow:
        dictWorkflow["dictSyncStatus"] = {}
    sTimestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    sBoolKey = f"b{sService}"
    sTimeKey = f"s{sService}Timestamp"
    for sPath in listFilePaths:
        if sPath not in dictWorkflow["dictSyncStatus"]:
            dictWorkflow["dictSyncStatus"][sPath] = (
                fdictInitializeSyncEntry()
            )
        dictWorkflow["dictSyncStatus"][sPath][sBoolKey] = True
        dictWorkflow["dictSyncStatus"][sPath][sTimeKey] = sTimestamp
