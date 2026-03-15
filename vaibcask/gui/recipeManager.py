"""Load, validate, and CRUD operations on recipe.json."""

import json
import os
import posixpath
import re


DEFAULT_SEARCH_ROOT = "/workspace"

REQUIRED_RECIPE_KEYS = ("sPlotDirectory", "listSteps")
REQUIRED_STEP_KEYS = ("sName", "sDirectory", "saCommands", "saOutputFiles")


def flistFindRecipesInContainer(
    connectionDocker, sContainerId, sSearchRoot=None
):
    """Search for recipe.json files under sSearchRoot and return paths."""
    if sSearchRoot is None:
        sSearchRoot = DEFAULT_SEARCH_ROOT
    sCommand = (
        f"find {sSearchRoot} -maxdepth 3 -name recipe.json"
        f" -type f 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    listPaths = [
        sLine.strip()
        for sLine in sOutput.splitlines()
        if sLine.strip().endswith("recipe.json")
    ]
    return sorted(listPaths)


def fdictLoadRecipeFromContainer(
    connectionDocker, sContainerId, sRecipePath=None
):
    """Fetch and parse recipe.json from a Docker container."""
    if sRecipePath is None:
        listPaths = flistFindRecipesInContainer(
            connectionDocker, sContainerId
        )
        if not listPaths:
            raise FileNotFoundError(
                "No recipe.json found under search root"
            )
        sRecipePath = listPaths[0]
    baContent = connectionDocker.fbaFetchFile(sContainerId, sRecipePath)
    dictRecipe = json.loads(baContent.decode("utf-8"))
    if not fbValidateRecipe(dictRecipe):
        raise ValueError(f"Invalid recipe.json: {sRecipePath}")
    return dictRecipe


def fbValidateRecipe(dictRecipe):
    """Return True when all required keys and step structures exist."""
    for sKey in REQUIRED_RECIPE_KEYS:
        if sKey not in dictRecipe:
            return False
    for iIndex, dictStep in enumerate(dictRecipe["listSteps"]):
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


def fdictBuildGlobalVariables(dictRecipe, sRecipePath):
    """Build the global variable dict from recipe.json top-level keys."""
    sRecipeDirectory = posixpath.dirname(sRecipePath)
    return {
        "sPlotDirectory": dictRecipe.get("sPlotDirectory", "Plot"),
        "sRepoRoot": sRecipeDirectory,
        "iNumberOfCores": dictRecipe.get("iNumberOfCores", -1),
        "sFigureType": dictRecipe.get("sFigureType", "pdf").lower(),
    }


def flistResolveOutputFiles(dictStep, dictVariables):
    """Return output file paths with template variables resolved."""
    listResolved = []
    for sPath in dictStep.get("saOutputFiles", []):
        listResolved.append(fsResolveVariables(sPath, dictVariables))
    return listResolved


def flistExtractStepNames(dictRecipe):
    """Return a list of step summary dicts."""
    listSteps = []
    for iIndex, dictStep in enumerate(dictRecipe["listSteps"]):
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


def fdictCreateStep(
    sName,
    sDirectory,
    bPlotOnly=True,
    saSetupCommands=None,
    saCommands=None,
    saOutputFiles=None,
):
    """Return a new step dictionary with validated fields."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "bEnabled": True,
        "bPlotOnly": bPlotOnly,
        "saSetupCommands": saSetupCommands if saSetupCommands else [],
        "saCommands": saCommands if saCommands else [],
        "saOutputFiles": saOutputFiles if saOutputFiles else [],
    }


def fdictGetStep(dictRecipe, iStepIndex):
    """Return a copy of the step at iStepIndex."""
    if iStepIndex < 0 or iStepIndex >= len(dictRecipe["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    return dict(dictRecipe["listSteps"][iStepIndex])


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


def fnRenumberAllReferences(dictRecipe, fnRemap):
    """Update all {StepNN.*} references in every step per fnRemap."""
    for dictStep in dictRecipe["listSteps"]:
        for sKey in ("saSetupCommands", "saCommands", "saOutputFiles"):
            if sKey in dictStep and dictStep[sKey]:
                dictStep[sKey] = [
                    fsRemapStepReferences(sItem, fnRemap)
                    for sItem in dictStep[sKey]
                ]


def fnInsertStep(dictRecipe, iPosition, dictStep):
    """Insert a step at iPosition, renumbering downstream references."""

    def fnRemap(iStepNumber):
        if iStepNumber >= iPosition + 1:
            return iStepNumber + 1
        return iStepNumber

    fnRenumberAllReferences(dictRecipe, fnRemap)
    dictRecipe["listSteps"].insert(iPosition, dictStep)


def fnUpdateStep(dictRecipe, iStepIndex, dictUpdates):
    """Update step at iStepIndex with dictUpdates."""
    if iStepIndex < 0 or iStepIndex >= len(dictRecipe["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    dictStep = dictRecipe["listSteps"][iStepIndex]
    for sKey, value in dictUpdates.items():
        dictStep[sKey] = value


def fnDeleteStep(dictRecipe, iStepIndex):
    """Remove step at iStepIndex, renumbering references."""
    if iStepIndex < 0 or iStepIndex >= len(dictRecipe["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    iDeletedNumber = iStepIndex + 1

    def fnRemap(iStepNumber):
        if iStepNumber > iDeletedNumber:
            return iStepNumber - 1
        return iStepNumber

    dictRecipe["listSteps"].pop(iStepIndex)
    fnRenumberAllReferences(dictRecipe, fnRemap)


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


def fnReorderStep(dictRecipe, iFromIndex, iToIndex):
    """Move a step from iFromIndex to iToIndex, renumbering references."""
    listSteps = dictRecipe["listSteps"]
    _fnValidateReorderIndices(iFromIndex, iToIndex, len(listSteps) - 1)
    iFromNumber = iFromIndex + 1

    def fnRemap(iStepNumber):
        return _fiRemapReorder(
            iStepNumber, iFromNumber, iFromIndex, iToIndex
        )

    dictStep = listSteps.pop(iFromIndex)
    listSteps.insert(iToIndex, dictStep)
    fnRenumberAllReferences(dictRecipe, fnRemap)


def fnSaveRecipeToContainer(
    connectionDocker, sContainerId, dictRecipe, sRecipePath=None
):
    """Serialize dictRecipe to JSON and write to container."""
    if sRecipePath is None:
        raise ValueError("sRecipePath is required for saving")
    sJson = json.dumps(dictRecipe, indent=2) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sRecipePath, sJson.encode("utf-8")
    )


def fsetExtractStepReferences(sText):
    """Return all {StepNN.variable} tokens found in sText as tuples."""
    return set(re.findall(r"\{Step(\d+)\.([^}]+)\}", sText))


def fdictBuildStemRegistry(dictRecipe):
    """Map each StepNN.stem to the step that produces it."""
    dictRegistry = {}
    for iIndex, dictStep in enumerate(dictRecipe["listSteps"]):
        iNumber = iIndex + 1
        for sOutputFile in dictStep.get("saOutputFiles", []):
            sBasename = posixpath.basename(sOutputFile)
            sStem = posixpath.splitext(sBasename)[0]
            sKey = f"Step{iNumber:02d}.{sStem}"
            dictRegistry[sKey] = iNumber
    return dictRegistry


def flistValidateReferences(dictRecipe):
    """Return a list of warnings about cross-step reference problems."""
    dictRegistry = fdictBuildStemRegistry(dictRecipe)
    listWarnings = []

    for iIndex, dictStep in enumerate(dictRecipe["listSteps"]):
        iNumber = iIndex + 1
        sStepLabel = f"Step{iNumber:02d}"

        for sKey in ("saSetupCommands", "saCommands"):
            for sCommand in dictStep.get(sKey, []):
                _fnCheckCommandReferences(
                    sCommand, sStepLabel, iNumber,
                    dictRecipe, dictRegistry, listWarnings,
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
    dictRecipe, dictRegistry, listWarnings,
):
    """Append warnings for invalid references in a single command."""
    iStepCount = len(dictRecipe["listSteps"])
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


def flistFilterFigureFiles(listOutputPaths):
    """Return only paths ending in figure extensions."""
    setFigureExtensions = {".pdf", ".png", ".jpg", ".jpeg", ".svg"}
    listFigures = []
    for sPath in listOutputPaths:
        sExtension = os.path.splitext(sPath)[1].lower()
        if sExtension in setFigureExtensions:
            listFigures.append(sPath)
    return listFigures


def flistExtractOutputFiles(dictStep):
    """Return list of output file paths for a step."""
    return list(dictStep.get("saOutputFiles", []))
