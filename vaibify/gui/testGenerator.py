"""Generate pytest unit tests for workflow steps via LLM."""

import json
import logging
import os
import posixpath
import re

logger = logging.getLogger("vaibify")

# ---------------------------------------------------------------------------
# Re-exports from leaf modules -- every name that was previously defined here
# remains importable from vaibify.gui.testGenerator.
# ---------------------------------------------------------------------------

from .testParser import (  # noqa: F401
    fsParseGeneratedCode,
    fbValidatePythonSyntax,
    fsRepairMissingImports,
    fdictParseCombinedOutput,
    fdictParseQuantitativeJson,
)

from .dataPreview import (  # noqa: F401
    fsPreviewDataFile,
    _fsResolvePath,
    _fsPreviewNpy,
    _fsPreviewHdf5,
    _fsPreviewText,
)

from .conftestManager import (  # noqa: F401
    fsConftestPath,
    fsConftestContent,
    fnWriteConftestMarker,
    _CONFTEST_MARKER_TEMPLATE,
    fnEnsureTestsDirectory,
)

from .llmInvoker import (  # noqa: F401
    _PROMPT_TEMPLATE,
    _CLAUDE_MD_TEST_SECTION,
    _CLAUDE_MD_MARKER,
    _CLAUDE_MD_VERSION,
    _CLAUDE_MD_VERSION_TAG,
    fnEnsureClaudeMdInstructions,
    _fsRemoveOldTestSection,
    fbContainerHasClaude,
    fsReadFileFromContainer,
    fsBuildPrompt,
    ftResultGenerateViaClaude,
    fsGenerateViaApi,
    _fbOutputLooksValid,
    _fnRaiseClaudeError,
    _fsInvokeLlm,
    _fsBuildCategoryPrompt,
    _fsBuildQuantitativePrompt,
)

from .templateManager import (  # noqa: F401
    _fsComputeTemplateHash,
    _fsEmbedTemplateHash,
    _fbFileMatchesTemplate,
    fsQuantitativeTemplateHash,
    fsIntegrityTemplateHash,
    fsQualitativeTemplateHash,
    _QUANTITATIVE_TEMPLATE_HEADER,
    _QUANTITATIVE_TEMPLATE_FOOTER,
    fsBuildQuantitativeTestCode,
    _INTEGRITY_TEST_TEMPLATE,
    fsBuildIntegrityTestCode,
    _QUALITATIVE_TEST_TEMPLATE,
    fsBuildQualitativeTestCode,
)

# ---------------------------------------------------------------------------
# Path helpers (remain in orchestrator)
# ---------------------------------------------------------------------------


def fsTestFilePath(sDirectory, iStepIndex):
    """Return the test file path for a given step."""
    sFilename = f"test_step{iStepIndex + 1:02d}.py"
    return posixpath.join(sDirectory, sFilename)


def fsIntegrityTestPath(sStepDirectory):
    """Return the integrity test file path for a step."""
    return posixpath.join(sStepDirectory, "tests", "test_integrity.py")


def fsQualitativeTestPath(sStepDirectory):
    """Return the qualitative test file path for a step."""
    return posixpath.join(sStepDirectory, "tests", "test_qualitative.py")


def fsQuantitativeTestPath(sStepDirectory):
    """Return the quantitative test file path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "test_quantitative.py",
    )


def fsQuantitativeStandardsPath(sStepDirectory):
    """Return the quantitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "quantitative_standards.json",
    )


def fsIntegrityStandardsPath(sStepDirectory):
    """Return the integrity standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "integrity_standards.json",
    )


def fsQualitativeStandardsPath(sStepDirectory):
    """Return the qualitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "qualitative_standards.json",
    )


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def fsBuildStepContext(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Gather script source code and data file previews for a step."""
    sDirectory = dictStep.get("sDirectory", "")
    sScripts = _fsBuildScriptContents(
        connectionDocker, sContainerId, dictStep, sDirectory
    )
    sPreviews = _fsBuildDataPreviews(
        connectionDocker, sContainerId, dictStep, sDirectory
    )
    return sScripts, sPreviews


def _fsBuildScriptContents(
    connectionDocker, sContainerId, dictStep, sDirectory,
):
    """Read and concatenate source code of data analysis scripts."""
    listParts = []
    for sCommand in dictStep.get("saDataCommands", []):
        sScript = _fsExtractScriptFromCommand(sCommand)
        if not sScript:
            continue
        sPath = _fsResolvePath(sScript, sDirectory)
        sContent = fsReadFileFromContainer(
            connectionDocker, sContainerId, sPath
        )
        if sContent:
            listLines = sContent.splitlines()[:200]
            listParts.append(
                f"--- {sScript} ---\n" + "\n".join(listLines)
            )
    return "\n\n".join(listParts) if listParts else "(no scripts found)"


def _fsExtractScriptFromCommand(sCommand):
    """Extract the Python script path from a command string."""
    from .commandUtilities import fsExtractScriptPath
    return fsExtractScriptPath(sCommand) or None


def _fsBuildDataPreviews(
    connectionDocker, sContainerId, dictStep, sDirectory,
):
    """Generate previews for each data output file."""
    listParts = []
    for sFile in dictStep.get("saDataFiles", []):
        sPreview = fsPreviewDataFile(
            connectionDocker, sContainerId, sFile, sDirectory
        )
        listParts.append(f"{sFile}: {sPreview}")
    return "\n".join(listParts) if listParts else "(no data files)"


# ---------------------------------------------------------------------------
# Test file writing helpers
# ---------------------------------------------------------------------------


def _fdictWriteTestFile(connectionDocker, sContainerId, sCode, sFilePath):
    """Write a test file to the container and return result dict."""
    try:
        connectionDocker.fnWriteFile(
            sContainerId, sFilePath, sCode.encode("utf-8"),
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to write test file {sFilePath}: {exc}"
        ) from exc
    sFilename = posixpath.basename(sFilePath)
    return {
        "sFilePath": sFilePath,
        "sContent": sCode,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _ftExtractStepInfo(dictWorkflow, iStepIndex):
    """Return (dictStep, sDirectory) for the given step index."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    return dictStep, dictStep.get("sDirectory", "")


# ---------------------------------------------------------------------------
# Single-step LLM generation
# ---------------------------------------------------------------------------


def fdictGenerateTest(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None,
):
    """Orchestrate test generation: gather context, call LLM, save."""
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    sScripts, sPreviews = fsBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables
    )
    sPrompt = fsBuildPrompt(
        sDirectory, dictStep, sScripts, sPreviews
    )
    sRawOutput = _fsInvokeLlm(
        connectionDocker, sContainerId, sPrompt, bUseApi, sApiKey,
        sUser=sUser,
    )
    sCode = fsParseGeneratedCode(sRawOutput)
    sFilePath = fsTestFilePath(sDirectory, iStepIndex)
    return _fdictWriteTestFile(
        connectionDocker, sContainerId, sCode, sFilePath,
    )



from .introspectionScript import (  # noqa: F401
    _fsBuildIntrospectionScript,
    _fsFormatSafeName,
    _flistParseIntrospectionOutput,
    _fsRunIntrospection,
)

# ---------------------------------------------------------------------------
# Standards builders
# ---------------------------------------------------------------------------


_SET_NONAN_FORMATS = {
    "npy", "npz", "csv", "whitespace", "fits", "matlab",
    "parquet", "image", "vcf", "bed", "gff", "sam",
    "fortran", "spss", "stata", "sas", "votable", "ipac",
    "vtk", "cgns", "safetensors", "excel", "rdata", "hdf5",
}


def _fbShouldAddNoNanTest(dictReport):
    """Return True if this report qualifies for a no-NaN test."""
    if not dictReport.get("bLoadable"):
        return False
    if dictReport.get("iNanCount", 0) != 0:
        return False
    if dictReport.get("iInfCount", 0) != 0:
        return False
    return dictReport.get("sFormat", "") in _SET_NONAN_FORMATS


def _fsGenerateIntegrityCode(listdictReports):
    """Produce integrity_standards.json dict from introspection reports.

    Deprecated: kept for backward compatibility. Use
    _fdictBuildIntegrityStandards instead.
    """
    dictStandards = _fdictBuildIntegrityStandards(listdictReports)
    return json.dumps(dictStandards, indent=4)


def _fsGenerateQualitativeCode(listdictReports):
    """Produce qualitative_standards.json dict from introspection reports.

    Deprecated: kept for backward compatibility. Use
    _fdictBuildQualitativeStandards instead.
    """
    dictStandards = _fdictBuildQualitativeStandards(listdictReports)
    return json.dumps(dictStandards, indent=4)


def _fdictBuildQuantitativeStandards(listdictReports, fTolerance):
    """Build quantitative_standards.json dict from introspection reports."""
    listStandards = []
    for dictReport in listdictReports:
        for dictBenchmark in dictReport.get("listBenchmarks", []):
            dictStandard = {
                "sName": dictBenchmark["sName"],
                "sDataFile": dictBenchmark["sDataFile"],
                "sAccessPath": dictBenchmark["sAccessPath"],
                "fValue": dictBenchmark["fValue"],
                "sUnit": "",
            }
            if "sFormat" in dictBenchmark:
                dictStandard["sFormat"] = dictBenchmark["sFormat"]
            listStandards.append(dictStandard)
    return {
        "fDefaultRtol": fTolerance,
        "listStandards": listStandards,
    }


def _fdictBuildOneIntegrityEntry(dictReport):
    """Build one integrity standard entry from an introspection report."""
    return {
        "sFileName": dictReport["sFileName"],
        "sFormat": dictReport.get("sFormat", ""),
        "tExpectedShape": dictReport.get("tShape"),
        "sDtype": dictReport.get("sDtype", ""),
        "bCheckNaN": _fbShouldAddNoNanTest(dictReport),
        "bCheckInf": _fbShouldAddNoNanTest(dictReport),
        "iExpectedByteSize": dictReport.get("iByteSize", 0),
    }


def _fdictBuildIntegrityStandards(listdictReports):
    """Build integrity_standards.json dict from introspection reports."""
    listStandards = [
        _fdictBuildOneIntegrityEntry(r) for r in listdictReports
        if r.get("bExists", False)
    ]
    return {"listStandards": listStandards}


def _fdictBuildOneQualitativeEntry(dictReport):
    """Build one qualitative standard entry from a report."""
    return {
        "sFileName": dictReport["sFileName"],
        "sFormat": dictReport.get("sFormat", ""),
        "listExpectedColumns": dictReport.get("listColumnNames", []),
        "listExpectedJsonKeys": dictReport.get("listJsonTopKeys", []),
    }


def _fbHasQualitativeContent(dictReport):
    """Return True if report has column names or JSON keys."""
    if dictReport.get("listColumnNames"):
        return True
    return bool(dictReport.get("listJsonTopKeys"))


def _fdictBuildQualitativeStandards(listdictReports):
    """Build qualitative_standards.json dict from introspection reports."""
    listStandards = [
        _fdictBuildOneQualitativeEntry(r) for r in listdictReports
        if _fbHasQualitativeContent(r)
    ]
    return {"listStandards": listStandards}


def _fnWarnIfAllUnloadable(listdictReports):
    """Log a warning if every report failed to load."""
    bAllUnloadable = all(
        not r.get("bLoadable") for r in listdictReports
    )
    if bAllUnloadable and listdictReports:
        listErrors = [r.get("sError", "") for r in listdictReports]
        logger.warning("All files unloadable: %s", listErrors)


# ---------------------------------------------------------------------------
# Deterministic test generation
# ---------------------------------------------------------------------------


def fdictGenerateAllTestsDeterministic(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bForceOverwrite=False,
):
    """Generate all three test categories deterministically."""
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    listDataFiles = dictStep.get("saDataFiles", [])
    if not listDataFiles:
        logger.warning(
            "No data files for step %d; generating minimal tests",
            iStepIndex,
        )
    fnEnsureTestsDirectory(connectionDocker, sContainerId, sDirectory)
    listdictReports = _fsRunIntrospection(
        connectionDocker, sContainerId, sDirectory, listDataFiles,
    )
    _fnWarnIfAllUnloadable(listdictReports)
    return _fdictWriteAllDeterministicTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, fTolerance, bForceOverwrite,
    )


def _fdictWriteAllDeterministicTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, fTolerance, bForceOverwrite=False,
):
    """Write all three deterministic test files and return result dict."""
    fnWriteConftestMarker(connectionDocker, sContainerId, sDirectory)
    dictResult = {}
    listModified = []
    dictResult["dictIntegrity"] = _fdictWriteIntegrityTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, bForceOverwrite,
    )
    if dictResult["dictIntegrity"].get("bNeedsOverwriteConfirm"):
        listModified.append(dictResult["dictIntegrity"]["sFilePath"])
    dictResult["dictQualitative"] = _fdictWriteQualitativeTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, bForceOverwrite,
    )
    if dictResult["dictQualitative"].get("bNeedsOverwriteConfirm"):
        listModified.append(
            dictResult["dictQualitative"]["sFilePath"]
        )
    dictResult["dictQuantitative"] = _fdictWriteQuantitativeTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, fTolerance, bForceOverwrite,
    )
    if dictResult["dictQuantitative"].get("bNeedsOverwriteConfirm"):
        listModified.append(
            dictResult["dictQuantitative"]["sFilePath"]
        )
    if listModified:
        dictResult["bNeedsOverwriteConfirm"] = True
        dictResult["listModifiedFiles"] = listModified
    return dictResult


def _fdictWriteQuantitativeFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write quantitative standards JSON and test file, return dict."""
    sStandardsPath = fsQuantitativeStandardsPath(sDirectory)
    sJsonContent = json.dumps(dictStandards, indent=4)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath,
        sJsonContent.encode("utf-8"),
    )
    sTestCode = fsBuildQuantitativeTestCode()
    sTestPath = fsQuantitativeTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath,
        sTestCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _fdictWriteQuantitativeTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, fTolerance, bForceOverwrite=False,
):
    """Build standards from reports and write quantitative test files."""
    dictStandards = _fdictBuildQuantitativeStandards(
        listdictReports, fTolerance,
    )
    return _fdictWriteQuantitativeFiles(
        connectionDocker, sContainerId, sDirectory,
        dictStandards, bForceOverwrite,
    )


def _fdictWriteIntegrityFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write integrity standards JSON and test file, return dict."""
    sStandardsPath = fsIntegrityStandardsPath(sDirectory)
    sJsonContent = json.dumps(dictStandards, indent=4)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath,
        sJsonContent.encode("utf-8"),
    )
    sTestCode = fsBuildIntegrityTestCode()
    sTestPath = fsIntegrityTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath, sTestCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _fdictWriteIntegrityTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, bForceOverwrite=False,
):
    """Build standards and write integrity test files."""
    dictStandards = _fdictBuildIntegrityStandards(listdictReports)
    return _fdictWriteIntegrityFiles(
        connectionDocker, sContainerId, sDirectory,
        dictStandards, bForceOverwrite,
    )


def _fdictWriteQualitativeFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write qualitative standards JSON and test file, return dict."""
    sStandardsPath = fsQualitativeStandardsPath(sDirectory)
    sJsonContent = json.dumps(dictStandards, indent=4)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath,
        sJsonContent.encode("utf-8"),
    )
    sTestCode = fsBuildQualitativeTestCode()
    sTestPath = fsQualitativeTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath, sTestCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _fdictWriteQualitativeTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, bForceOverwrite=False,
):
    """Build standards and write qualitative test files."""
    dictStandards = _fdictBuildQualitativeStandards(listdictReports)
    return _fdictWriteQualitativeFiles(
        connectionDocker, sContainerId, sDirectory,
        dictStandards, bForceOverwrite,
    )


# ---------------------------------------------------------------------------
# LLM-based test generation
# ---------------------------------------------------------------------------


def fdictGenerateAllTests(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None, bDeterministic=True, bForceOverwrite=False,
):
    """Generate all three test categories via LLM or deterministically."""
    if bDeterministic:
        return fdictGenerateAllTestsDeterministic(
            connectionDocker, sContainerId, iStepIndex,
            dictWorkflow, dictVariables, bForceOverwrite,
        )
    return _fdictGenerateAllTestsViaLlm(
        connectionDocker, sContainerId, iStepIndex,
        dictWorkflow, dictVariables, bUseApi, sApiKey, sUser,
    )


def _fdictGenerateAllTestsViaLlm(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi, sApiKey, sUser,
):
    """Generate all three test categories via LLM."""
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    sDataFiles = ", ".join(dictStep.get("saDataFiles", []))
    sScripts, sPreviews = fsBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables,
    )
    if not bUseApi:
        fnEnsureClaudeMdInstructions(connectionDocker, sContainerId)
    fnEnsureTestsDirectory(connectionDocker, sContainerId, sDirectory)
    fnWriteConftestMarker(connectionDocker, sContainerId, sDirectory)
    return _fdictDispatchLlmCategories(
        connectionDocker, sContainerId, sDirectory,
        sDataFiles, sScripts, sPreviews,
        fTolerance, bUseApi, sApiKey, sUser,
    )


def _fdictDispatchLlmCategories(
    connectionDocker, sContainerId, sDirectory,
    sDataFiles, sScripts, sPreviews,
    fTolerance, bUseApi, sApiKey, sUser,
):
    """Dispatch LLM generation for each test category."""
    dictResult = {}
    for sCategory in ("integrity", "qualitative"):
        dictResult[f"dict{sCategory.capitalize()}"] = (
            _fdictGenerateSingleCategory(
                connectionDocker, sContainerId, sDirectory,
                sCategory, sDataFiles, sScripts, sPreviews,
                bUseApi, sApiKey, sUser,
            )
        )
    dictResult["dictQuantitative"] = _fdictGenerateQuantitativeCategory(
        connectionDocker, sContainerId, sDirectory,
        sDataFiles, sScripts, sPreviews,
        fTolerance, bUseApi, sApiKey, sUser,
    )
    return dictResult


_DICT_CATEGORY_PATHS = {
    "integrity": fsIntegrityTestPath,
    "qualitative": fsQualitativeTestPath,
}


def _fdictGenerateSingleCategory(
    connectionDocker, sContainerId, sDirectory,
    sCategory, sDataFiles, sScriptContents, sDataPreviews,
    bUseApi, sApiKey, sUser,
):
    """Generate one Python test category via LLM, with error isolation."""
    sPrompt = _fsBuildCategoryPrompt(
        sCategory, sDirectory, sDataFiles, sScriptContents, sDataPreviews,
    )
    sFilePath = _DICT_CATEGORY_PATHS[sCategory](sDirectory)
    sRaw = ""
    try:
        sRaw = _fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        sCode = fsParseGeneratedCode(sRaw)
        return _fdictWriteTestFile(
            connectionDocker, sContainerId, sCode, sFilePath,
        )
    except Exception as error:
        _fnAppendErrorLog(
            f"[{sCategory}] {error}\n"
            f"First 300 chars of raw output:\n{sRaw[:300]}"
        )
        return _fdictErrorResult(str(error))


def _fdictGenerateQuantitativeCategory(
    connectionDocker, sContainerId, sDirectory,
    sDataFiles, sScriptContents, sDataPreviews,
    fTolerance, bUseApi, sApiKey, sUser,
):
    """Generate quantitative standards JSON via LLM."""
    sPrompt = _fsBuildQuantitativePrompt(
        sDirectory, sDataFiles, sScriptContents,
        sDataPreviews, fTolerance,
    )
    try:
        sRaw = _fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        logger.debug("Quantitative raw output: %s", sRaw[:500])
        dictStandards = fdictParseQuantitativeJson(sRaw)
        dictStandards["fDefaultRtol"] = fTolerance
        return _fdictWriteQuantitativeFiles(
            connectionDocker, sContainerId, sDirectory,
            dictStandards,
        )
    except Exception as error:
        return _fdictErrorResult(str(error))


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _fdictErrorResult(sMessage):
    """Return a standard error dict for a failed category."""
    logger.error("Test category error: %s", sMessage)
    _fnAppendErrorLog(sMessage)
    return {
        "sFilePath": "",
        "sContent": "",
        "saCommands": [],
        "sError": sMessage,
    }


def _fnAppendErrorLog(sMessage):
    """Append error details to a local log file for debugging."""
    import tempfile
    sLogPath = os.path.join(tempfile.gettempdir(), "vaibify_test_errors.log")
    try:
        with open(sLogPath, "a", encoding="utf-8") as fLog:
            fLog.write(sMessage + "\n---\n")
    except Exception:
        pass
