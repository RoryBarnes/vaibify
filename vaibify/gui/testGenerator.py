"""Generate pytest unit tests for workflow steps via LLM."""

__all__ = [
    "S_CONFTEST_LOADER_SOURCE",
    "S_CONFTEST_VERSION",
    "T_GENERATED_TEST_CATEGORIES",
    "fbContainerHasClaude",
    "fbValidatePythonSyntax",
    "fdictGenerateAllTests",
    "fdictGenerateAllTestsDeterministic",
    "flistMissingStepOutputs",
    "fnRequireStepOutputsExist",
    "fdictGenerateTest",
    "fdictParseCombinedOutput",
    "fdictParseQuantitativeJson",
    "fdictReadInstalledConftestVersions",
    "fnEnsureClaudeMdInstructions",
    "fnEnsureConftestsCurrent",
    "fnEnsureTestsDirectory",
    "fnMigrateFlatMarkers",
    "fnWriteConftestMarker",
    "fbWriteConftestMarkersBatch",
    "fbWriteMarkerPlugin",
    "flistMarkerPluginPaths",
    "fsBuildIntegrityTestCode",
    "fsBuildPrompt",
    "fsBuildQualitativeTestCode",
    "fsBuildQuantitativeTestCode",
    "ftBuildStepContext",
    "fsConftestContent",
    "fsBuildConftestSource",
    "fsConftestPath",
    "fsGenerateViaApi",
    "fsIntegrityStandardsPath",
    "fsIntegrityTemplateHash",
    "fbIsVaibifyTemplateHash",
    "SET_PRIOR_TEMPLATE_HASHES",
    "fsIntegrityTestPath",
    "fsParseGeneratedCode",
    "fsPreviewDataFile",
    "fsQualitativeStandardsPath",
    "fsQualitativeTemplateHash",
    "fsQualitativeTestPath",
    "fsStepTestSuffix",
    "fsQuantitativeStandardsPath",
    "fsQuantitativeTemplateHash",
    "fsQuantitativeTestPath",
    "fsReadFileFromContainer",
    "fsReadInstalledConftestVersion",
    "fsRepairMissingImports",
    "fsTestFilePath",
    "ftResultGenerateViaClaude",
]

import hashlib
import json
import logging
import math
import os
import posixpath
import re

logger = logging.getLogger("vaibify")

# The category names a caller may ask the deterministic generator for,
# spelled as the run/verify lanes spell them (``run-test-category``
# takes the same words), so a researcher or an agent uses ONE
# vocabulary across generating, running and reading a category.
T_GENERATED_TEST_CATEGORIES = ("integrity", "qualitative", "quantitative")

_LIST_STOCHASTIC_PATTERNS = [
    r"np\.random",
    r"numpy\.random",
    r"random\.seed",
    r"RandomState",
    r"default_rng",
    r"\bdynesty\b",
    r"\bemcee\b",
    r"\bultranest\b",
    r"\bpymultinest\b",
    r"\bmultinest\b",
]
_I_STOCHASTIC_MIN_SAMPLES = 64
_F_SIGMA_MULT = 3.0
_F_FLOOR_RTOL = 1e-6
_F_UNSEEDED_RTOL = 0.10
_T_DISTRIBUTIONAL_KINDS = (
    "mean", "std",
    "percentile_5", "percentile_25", "percentile_50",
    "percentile_75", "percentile_95",
)
_T_SINGLE_SAMPLE_KINDS = ("first", "last", "min", "max")

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
    S_CONFTEST_VERSION,
    S_CONFTEST_LOADER_SOURCE,
    fsConftestPath,
    fsConftestContent,
    fsBuildConftestSource,
    fsReadInstalledConftestVersion,
    fdictReadInstalledConftestVersions,
    fnWriteConftestMarker,
    fbWriteConftestMarkersBatch,
    fbWriteMarkerPlugin,
    flistMarkerPluginPaths,
    _CONFTEST_MARKER_TEMPLATE,
    fnEnsureTestsDirectory,
    fnEnsureConftestsCurrent,
    flistRefreshConftestsForRun,
    fnMigrateFlatMarkers,
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

from .pipelineUtils import MissingStepOutputsError
from .standardsFiles import (  # noqa: F401
    S_STANDARDS_DIGEST_KEY,
    fbStandardsTextIsUnmodified,
    fsComputeStandardsDigest,
    fsSerializeStandards,
)

from .templateManager import (  # noqa: F401
    _fsComputeTemplateHash,
    _fsEmbedTemplateHash,
    _fbFileMatchesTemplate,
    fsQuantitativeTemplateHash,
    fsIntegrityTemplateHash,
    fsQualitativeTemplateHash,
    fbIsVaibifyTemplateHash,
    fbStampedFileIsUnmodified,
    SET_PRIOR_TEMPLATE_HASHES,
    S_GENERATOR_DETERMINISTIC,
    S_GENERATOR_LLM,
    S_GENERATOR_LLM_FALLBACK,
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


def fsStepTestSuffix(sStepDirectory):
    """Return the step-derived filename suffix for generated tests.

    The step directory's basename with its first letter lowered —
    ``AiPowerOverTime`` → ``aiPowerOverTime`` — the convention the
    fielded suffixed test names already follow. The suffix is what
    keeps generated basenames unique across steps: a Zenodo deposit is
    FLAT (the bucket API refuses path-containing keys, probed
    2026-08-27), so two steps' identically-named ``test_qualitative.py``
    silently overwrote each other in a published record (approved
    ruling, 2026-08-27).
    """
    sBase = posixpath.basename((sStepDirectory or "").rstrip("/"))
    if not sBase:
        return ""
    return sBase[0].lower() + sBase[1:]


def _fsSuffixedTestFilename(sStem, sStepDirectory, sExtension):
    """Compose ``<sStem>_<stepSuffix>.<ext>``, unsuffixed when no step."""
    sSuffix = fsStepTestSuffix(sStepDirectory)
    if sSuffix:
        return f"{sStem}_{sSuffix}.{sExtension}"
    return f"{sStem}.{sExtension}"


def fsIntegrityTestPath(sStepDirectory):
    """Return the integrity test file path for a step."""
    return posixpath.join(
        sStepDirectory, "tests",
        _fsSuffixedTestFilename("test_integrity", sStepDirectory, "py"),
    )


def fsQualitativeTestPath(sStepDirectory):
    """Return the qualitative test file path for a step."""
    return posixpath.join(
        sStepDirectory, "tests",
        _fsSuffixedTestFilename(
            "test_qualitative", sStepDirectory, "py",
        ),
    )


def fsQuantitativeTestPath(sStepDirectory):
    """Return the quantitative test file path for a step."""
    return posixpath.join(
        sStepDirectory, "tests",
        _fsSuffixedTestFilename(
            "test_quantitative", sStepDirectory, "py",
        ),
    )


def fsQuantitativeStandardsPath(sStepDirectory):
    """Return the quantitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests",
        _fsSuffixedTestFilename(
            "quantitative_standards", sStepDirectory, "json",
        ),
    )


def fsIntegrityStandardsPath(sStepDirectory):
    """Return the integrity standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests",
        _fsSuffixedTestFilename(
            "integrity_standards", sStepDirectory, "json",
        ),
    )


def fsQualitativeStandardsPath(sStepDirectory):
    """Return the qualitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests",
        _fsSuffixedTestFilename(
            "qualitative_standards", sStepDirectory, "json",
        ),
    )


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def ftBuildStepContext(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Gather script source code and data file previews for a step.

    The step ``sDirectory`` is resolved against ``sRepoRoot`` from
    ``dictVariables`` so the docker file-fetch APIs see absolute
    container paths. A repo-relative directory used as-is would be
    resolved by Docker against ``/`` and miss the project repo,
    yielding 404 from the archive endpoint.
    """
    sDirectory = dictStep.get("sDirectory", "")
    sRepoRoot = (dictVariables or {}).get("sRepoRoot", "")
    if sDirectory and sRepoRoot and not posixpath.isabs(sDirectory):
        sDirectory = posixpath.join(sRepoRoot, sDirectory)
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
    for sFile in dictStep.get("saOutputDataFiles", []):
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
    except Exception as errorCaught:
        raise RuntimeError(
            f"Failed to write test file {sFilePath}: {errorCaught}"
        ) from errorCaught
    sFilename = posixpath.basename(sFilePath)
    return {
        "sFilePath": sFilePath,
        "sContent": sCode,
        "saCommands": [f"python3 -m pytest tests/{sFilename}"],
    }


def _ftExtractStepInfo(dictWorkflow, iStepIndex):
    """Return (dictStep, sAbsoluteDirectory) for the given step index.

    The directory is resolved to its container-absolute form by
    joining with ``dictWorkflow['sProjectRepoPath']`` when the
    workflow's stored ``sDirectory`` is repo-relative. Callers
    write files and run scripts via Docker APIs that need absolute
    container paths, so resolution at this single boundary keeps
    every downstream path correct without spreading the join logic.
    """
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    sDirectory = dictStep.get("sDirectory", "")
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    if sDirectory and sRepoRoot and not posixpath.isabs(sDirectory):
        sDirectory = posixpath.join(sRepoRoot, sDirectory)
    return dictStep, sDirectory


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
    sScripts, sPreviews = ftBuildStepContext(
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
    """Produce the integrity standards dict from introspection reports.

    Deprecated: kept for backward compatibility. Use
    _fdictBuildIntegrityStandards instead.
    """
    dictStandards = _fdictBuildIntegrityStandards(listdictReports)
    return json.dumps(dictStandards, indent=4)


def _fsGenerateQualitativeCode(listdictReports):
    """Produce the qualitative standards dict from introspection reports.

    Deprecated: kept for backward compatibility. Use
    _fdictBuildQualitativeStandards instead.
    """
    dictStandards = _fdictBuildQualitativeStandards(listdictReports)
    return json.dumps(dictStandards, indent=4)


def fbStepProducesStochasticOutputs(dictStep, sScriptContents):
    """Return True when script source matches a stochastic-framework pattern.

    Combined with a sample-size check on each introspected array
    (see ``_fsClassifyStochasticity``) before a step is treated as
    stochastic. Patterns are framework-level identifiers (e.g.
    ``np.random``, ``dynesty``), never science-specific symbols.
    """
    del dictStep
    if not sScriptContents:
        return False
    for sPattern in _LIST_STOCHASTIC_PATTERNS:
        if re.search(sPattern, sScriptContents, re.IGNORECASE):
            return True
    return False


def _fbReportHasStochasticArray(dictReport):
    """Return True if any benchmark in the report has enough samples."""
    for dictBenchmark in dictReport.get("listBenchmarks", []):
        if dictBenchmark.get("iSampleSize", 0) >= _I_STOCHASTIC_MIN_SAMPLES:
            return True
    return False


def _fsClassifyStochasticity(
    dictStep, sScriptContents, listdictReports,
):
    """Return one of deterministic/stochastic/stochastic_unseeded/unintrospectable.

    The unseeded variant is opt-in via ``dictVerification`` flagged by
    the per-step lint (see ``fileStatusManager``). The unintrospectable
    branch fires when the introspector saw output files but produced no
    numeric benchmarks (binary format with no loader, parse failure,
    non-tabular content).
    """
    dictVerification = dictStep.get("dictVerification", {}) or {}
    bUnseeded = dictVerification.get("bUnseededRandomnessWarning", False)
    if listdictReports and not any(
        r.get("listBenchmarks") for r in listdictReports
    ):
        return "unintrospectable"
    if not fbStepProducesStochasticOutputs(dictStep, sScriptContents):
        return "deterministic"
    bAnyStochasticArray = any(
        _fbReportHasStochasticArray(r) for r in listdictReports
    )
    if not bAnyStochasticArray:
        return "deterministic"
    if bUnseeded:
        return "stochastic_unseeded"
    return "stochastic"


def _ffMeanToleranceFromCv(fObservedCv, iSampleSize):
    """Return rtol for a sample mean given coefficient of variation and N.

    Uses the within-sample standard error ``CV / sqrt(N)`` scaled by
    ``_F_SIGMA_MULT`` (k=3 → 99.7% Gaussian-tail target). Rationale
    for trusting a single chain's dispersion to set this tolerance:
    Vehtari, Gelman, Simpson, Carpenter & Bürkner (2021),
    "Rank-normalization, folding, and localization: An improved R̂
    for assessing convergence of MCMC", *Bayesian Analysis*
    16(2):667-718 — sufficient sample size lets within-sample
    statistics stand in for cross-chain diagnostics.
    """
    if iSampleSize <= 0:
        return _F_FLOOR_RTOL
    fSe = _F_SIGMA_MULT * abs(fObservedCv) / math.sqrt(iSampleSize)
    return max(fSe, _F_FLOOR_RTOL)


def _ffStdToleranceFromN(iSampleSize):
    """Return rtol for the sample standard deviation given N.

    Uses the asymptotic Gaussian standard error ``sqrt(2/(N-1))`` for
    the sample standard deviation, scaled by ``_F_SIGMA_MULT`` (k=3).
    Same single-chain dispersion rationale as ``_ffMeanToleranceFromCv``;
    see Vehtari et al. (2021).
    """
    if iSampleSize < 2:
        return _F_FLOOR_RTOL
    fSe = _F_SIGMA_MULT * math.sqrt(2.0 / (iSampleSize - 1))
    return max(fSe, _F_FLOOR_RTOL)


def _ffPercentileToleranceFromN(fProbability, iSampleSize, fObservedCv, fValue):
    """Return rtol for an empirical percentile via asymptotic SE.

    Reference: Oberkampf & Roy (2010), *Verification and Validation in
    Scientific Computing*, Cambridge University Press, sec 2.3.
    """
    if iSampleSize <= 0 or fValue == 0.0:
        return _F_FLOOR_RTOL
    fVar = fProbability * (1.0 - fProbability) / iSampleSize
    fScale = abs(fObservedCv) * math.sqrt(2.0 * math.pi)
    fSe = _F_SIGMA_MULT * math.sqrt(fVar) / abs(fValue) * fScale
    return max(fSe, _F_FLOOR_RTOL)


_T_PERCENTILE_KIND_TO_PROB = {
    "percentile_5": 0.05, "percentile_25": 0.25,
    "percentile_50": 0.50, "percentile_75": 0.75,
    "percentile_95": 0.95,
}


def _ffToleranceForStochasticKind(dictStandard, fDefaultRtol):
    """Return the SE-derived rtol for one stochastic-classified entry."""
    sKind = dictStandard.get("sMetricKind", "single")
    iN = int(dictStandard.get("iSampleSize", 0) or 0)
    fObservedCv = dictStandard.get("fObservedCv") or 0.0
    if sKind == "mean":
        return _ffMeanToleranceFromCv(fObservedCv, iN)
    if sKind == "std":
        return _ffStdToleranceFromN(iN)
    if sKind in _T_PERCENTILE_KIND_TO_PROB:
        return _ffPercentileToleranceFromN(
            _T_PERCENTILE_KIND_TO_PROB[sKind], iN, fObservedCv,
            dictStandard.get("fValue", 0.0),
        )
    return fDefaultRtol


def _fdictAssignTolerance(dictStandard, sClassification, fDefaultRtol):
    """Return a copy of dictStandard with derived tolerance fields."""
    dictResult = dict(dictStandard)
    if sClassification == "stochastic_unseeded":
        dictResult["fRtol"] = _F_UNSEEDED_RTOL
        dictResult["sNote"] = (
            "Placeholder tolerance: seed the source of randomness "
            "to derive a statistically defensible value."
        )
        return dictResult
    if sClassification == "stochastic":
        dictResult["fRtol"] = _ffToleranceForStochasticKind(
            dictResult, fDefaultRtol,
        )
        return dictResult
    dictResult["fRtol"] = fDefaultRtol
    return dictResult


def _fbBenchmarkPassesFilter(dictBenchmark, sClassification):
    """Return True when the benchmark belongs in the standards for this class."""
    sKind = dictBenchmark.get("sMetricKind", "single")
    if sClassification == "deterministic":
        return sKind in ("single", "mean")
    if sClassification == "stochastic":
        return sKind in _T_DISTRIBUTIONAL_KINDS
    if sClassification == "stochastic_unseeded":
        return sKind in ("mean", "percentile_50")
    return False


def _fdictBuildOneQuantitativeEntry(dictBenchmark):
    """Project an introspection benchmark into a standards entry."""
    dictEntry = {
        "sName": dictBenchmark["sName"],
        "sDataFile": dictBenchmark["sDataFile"],
        "sAccessPath": dictBenchmark["sAccessPath"],
        "fValue": dictBenchmark["fValue"],
        "sUnit": "",
    }
    for sField in ("sFormat", "sMetricKind", "iSampleSize", "fObservedCv"):
        if sField in dictBenchmark:
            dictEntry[sField] = dictBenchmark[sField]
    return dictEntry


def _fdictBuildQuantitativeStandards(
    listdictReports, fTolerance, sClassification="deterministic",
):
    """Build the quantitative standards dict tagged by stochasticity class."""
    listStandards = []
    for dictReport in listdictReports:
        for dictBenchmark in dictReport.get("listBenchmarks", []):
            if not _fbBenchmarkPassesFilter(dictBenchmark, sClassification):
                continue
            dictEntry = _fdictBuildOneQuantitativeEntry(dictBenchmark)
            listStandards.append(_fdictAssignTolerance(
                dictEntry, sClassification, fTolerance,
            ))
    dictResult = {
        "fDefaultRtol": fTolerance,
        "sStochasticityClassification": sClassification,
        "listStandards": listStandards,
    }
    if sClassification == "unintrospectable":
        dictResult["sIntrospectorError"] = _fsCollectIntrospectorErrors(
            listdictReports,
        )
    return dictResult


def _fsCollectIntrospectorErrors(listdictReports):
    """Concatenate per-file error messages for the unintrospectable banner."""
    listMessages = []
    for dictReport in listdictReports:
        sError = dictReport.get("sError", "")
        if sError:
            listMessages.append(
                f"{dictReport.get('sFileName', '')}: {sError}"
            )
    return "; ".join(listMessages)


def _fdictMergePreservingOverrides(dictNew, dictOld):
    """Return dictNew with user-edited overrides from dictOld merged in.

    Preserves ``fRtol``, ``fAtol``, ``sNote``, and ``sUnit`` fields on
    matching ``sName`` entries — these are the fields a researcher
    most often hand-edits between regenerations.
    """
    dictByName = {
        dictEntry["sName"]: dictEntry
        for dictEntry in dictOld.get("listStandards", [])
    }
    for dictEntry in dictNew.get("listStandards", []):
        dictPrior = dictByName.get(dictEntry["sName"])
        if dictPrior is None:
            continue
        for sField in ("fRtol", "fAtol", "sNote", "sUnit"):
            if sField in dictPrior:
                dictEntry[sField] = dictPrior[sField]
    return dictNew


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
    """Build the integrity standards dict from introspection reports."""
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
    """Build the qualitative standards dict from introspection reports."""
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


def flistMissingStepOutputs(connectionDocker, sContainerId, dictWorkflow,
                            iStepIndex):
    """Return the step's declared outputs that do not exist, in order.

    A typed read, so it is safe before any carrier opens. Paths resolve
    against the step directory, as the introspection program and the
    dashboard's "No Data for Tests" check both read them.
    """
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    listDataFiles = list(dictStep.get("saOutputDataFiles", []))
    if not listDataFiles:
        return []
    listPaths = [
        sFile if posixpath.isabs(sFile) else posixpath.join(sDirectory, sFile)
        for sFile in listDataFiles
    ]
    listExists = connectionDocker.flistContainerPathsExist(
        sContainerId, listPaths,
    )
    return [
        sFile for sFile, bExists in zip(listDataFiles, listExists)
        if not bExists
    ]


def fnRequireStepOutputsExist(connectionDocker, sContainerId, dictWorkflow,
                              iStepIndex):
    """Raise ``MissingStepOutputsError`` before anything is written.

    Standards derived from files that do not exist judge nothing, and
    their placeholder tests ran as passes.
    """
    listMissing = flistMissingStepOutputs(
        connectionDocker, sContainerId, dictWorkflow, iStepIndex,
    )
    if listMissing:
        raise MissingStepOutputsError(listMissing)


def fdictGenerateAllTestsDeterministic(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bForceOverwrite=False,
    setCategories=None,
):
    """Generate the requested test categories deterministically.

    ``setCategories`` defaults to all three, preserving every existing
    caller. The introspection runs once regardless of how many
    categories are wanted: it reads the step's declared outputs, and
    reading them three times would be three container round-trips for
    one answer. Missing outputs refuse before anything is written,
    the tests directory included.
    """
    fnRequireStepOutputsExist(
        connectionDocker, sContainerId, dictWorkflow, iStepIndex,
    )
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    listDataFiles = dictStep.get("saOutputDataFiles", [])
    if not listDataFiles:
        logger.warning(
            "No data files for step %d; no output-derived tests apply",
            iStepIndex,
        )
    sScripts, _sPreviews = ftBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables,
    )
    bScriptStochastic = fbStepProducesStochasticOutputs(
        dictStep, sScripts,
    )
    listdictReports = _fsRunIntrospection(
        connectionDocker, sContainerId, sDirectory, listDataFiles,
        bScriptStochastic=bScriptStochastic,
    ) if listDataFiles else []
    _fnRefuseOutputsIntrospectionCouldNotFind(listdictReports)
    _fnWarnIfAllUnloadable(listdictReports)
    sClassification = _fsClassifyStochasticity(
        dictStep, sScripts, listdictReports,
    )
    from .deterministicTestWriter import fdictWriteAllDeterministicTests
    return fdictWriteAllDeterministicTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, fTolerance, bForceOverwrite,
        dictWorkflow.get("sProjectRepoPath", ""),
        sClassification, setCategories,
    )


def _fnRefuseOutputsIntrospectionCouldNotFind(listdictReports):
    """Refuse when an output vanished between the probe and the analysis."""
    listMissing = [
        dictReport["sFileName"] for dictReport in listdictReports
        if dictReport.get("sError") == "file not found"
    ]
    if listMissing:
        raise MissingStepOutputsError(listMissing)


def _fdictWriteQuantitativeFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write model-derived quantitative standards and the test file.

    The language-model lane's writer. The test file is checked BEFORE
    either file is written, so a pending confirmation touches nothing.
    """
    sStandardsPath = fsQuantitativeStandardsPath(sDirectory)
    dictStandards.setdefault("sGenerator", S_GENERATOR_DETERMINISTIC)
    dictMerged = _fdictMergeWithExistingStandards(
        connectionDocker, sContainerId, sStandardsPath, dictStandards,
    )
    sTestCode = fsBuildQuantitativeTestCode()
    sTestPath = fsQuantitativeTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    sJsonContent = fsSerializeStandards(dictMerged)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath, sJsonContent.encode("utf-8"),
    )
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath, sTestCode.encode("utf-8"))
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        **_fdictQuantitativeSummary(dictMerged),
        "saCommands": [f"python3 -m pytest tests/{sFilename}"],
    }


def _fdictQuantitativeSummary(dictStandards):
    """Return the classification fields a quantitative result reports."""
    return {
        "sStochasticityClassification": dictStandards.get(
            "sStochasticityClassification", "deterministic",
        ),
        "sIntrospectorError": dictStandards.get("sIntrospectorError", ""),
        "iStandardCount": len(dictStandards.get("listStandards", [])),
    }


def _fdictMergeWithExistingStandards(
    connectionDocker, sContainerId, sStandardsPath, dictStandards,
):
    """Merge user-edited overrides from any prior standards file."""
    sExisting = fsReadFileFromContainer(
        connectionDocker, sContainerId, sStandardsPath,
    )
    if not sExisting:
        return dictStandards
    try:
        dictPrior = json.loads(sExisting)
    except (ValueError, TypeError):
        return dictStandards
    return _fdictMergePreservingOverrides(dictStandards, dictPrior)


# ---------------------------------------------------------------------------
# LLM-based test generation
# ---------------------------------------------------------------------------


def fdictGenerateAllTests(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None, bDeterministic=True, bForceOverwrite=False,
):
    """Generate all three test categories via LLM or deterministically.

    Deterministic generation falls back to a consented language model
    only where it could not understand the outputs; ``llmTestGeneration``
    holds that allow-list and records which generator wrote each
    category.
    """
    from .llmTestGeneration import fdictGenerateAndStampTests
    return fdictGenerateAndStampTests(
        connectionDocker, sContainerId, iStepIndex, dictWorkflow,
        dictVariables, bUseApi, sApiKey, sUser, bDeterministic,
        bForceOverwrite,
    )


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
