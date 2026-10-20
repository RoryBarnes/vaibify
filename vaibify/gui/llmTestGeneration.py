"""Language-model test generation, and the narrow fallback to it.

Vaibify derives a step's tests deterministically from its own outputs.
A language model writes them in two cases only, and every payload
category and every file it writes says which:

* ``llm`` -- the researcher asked for model-written tests
  (``bDeterministic`` false);
* ``llm-fallback`` -- deterministic generation could not understand
  the outputs, and the researcher had already consented to a model: a
  Claude CLI installed in the container, or the stored API key they
  asked to use (``bUseApi``). Vaibify never prompts for a key here and
  never reaches for one on its own.

"Could not understand the outputs" is an ALLOW-LIST, never a broad
``except``: the introspection program failed or printed nothing
parseable (``OutputAnalysisError``), or no output could be loaded so
no quantitative standards could be derived (the ``unintrospectable``
classification). Everything else surfaces unchanged -- a control-plane
refusal, a write failure, a pending overwrite confirmation, a missing
step -- because a model cannot fix any of them and asking one would
hide them. With no consented model the error names both failures.

The author is recorded because generated standards are the published
criteria that judge an output whose bytes moved: the standards JSON
carries ``sGenerator``, a model-written test file opens with
``# vaibify-generator: <generator>``, and a deterministic test file
carries the template-hash stamp that already identifies it.

The agent-safe route calls ``fdictGenerateAllTestsDeterministic``
directly, so it cannot reach this module.

Every generator helper is looked up on the ``testGenerator`` module at
call time, so a patch of a ``testGenerator`` name applies here too.
"""

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal

from . import testGenerator as moduleGenerator
from .pipelineUtils import OutputAnalysisError
from .templateManager import (
    S_GENERATOR_DETERMINISTIC,
    S_GENERATOR_LLM,
    S_GENERATOR_LLM_FALLBACK,
)


__all__ = [
    "S_GENERATOR_STAMP_PREFIX",
    "S_NO_MODEL_AVAILABLE",
    "fdictGenerateAndStampTests",
    "fsResolveConsentedModel",
    "fsStampGeneratedCode",
]

S_GENERATOR_STAMP_PREFIX = "# vaibify-generator: "
S_NO_MODEL_AVAILABLE = (
    "No language-model fallback is available: this container has no "
    "Claude CLI, and no stored API key was requested.")
T_CATEGORY_KEYS = ("dictIntegrity", "dictQualitative", "dictQuantitative")


def fdictGenerateAndStampTests(
    connectionDocker, sContainerId, iStepIndex, dictWorkflow,
    dictVariables, bUseApi, sApiKey, sUser, bDeterministic,
    bForceOverwrite,
):
    """Generate a step's tests and record which generator wrote each category."""
    if not bDeterministic:
        return _fdictStampPayload(_fdictGenerateAllTestsViaLlm(
            connectionDocker, sContainerId, iStepIndex, dictWorkflow,
            dictVariables, bUseApi, sApiKey, sUser,
        ), S_GENERATOR_LLM)
    try:
        dictResult = moduleGenerator.fdictGenerateAllTestsDeterministic(
            connectionDocker, sContainerId, iStepIndex, dictWorkflow,
            dictVariables, bForceOverwrite,
        )
    except OutputAnalysisError as error:
        return _fdictFallBackForEveryCategory(
            connectionDocker, sContainerId, iStepIndex, dictWorkflow,
            dictVariables, bUseApi, sApiKey, sUser, str(error))
    _fdictStampPayload(dictResult, S_GENERATOR_DETERMINISTIC)
    sFailure = _fsQuantitativeAnalysisFailure(
        dictResult.get("dictQuantitative"))
    if dictResult.get("bNeedsOverwriteConfirm") or not sFailure:
        return dictResult
    dictResult["dictQuantitative"] = _fdictFallBackForQuantitative(
        connectionDocker, sContainerId, iStepIndex, dictWorkflow,
        dictVariables, bUseApi, sApiKey, sUser, sFailure)
    return dictResult


def fsResolveConsentedModel(connectionDocker, sContainerId, bUseApi, sApiKey):
    """Return "api", "cli" or "" for the model the researcher already consented to."""
    if bUseApi and sApiKey:
        return "api"
    if moduleGenerator.fbContainerHasClaude(connectionDocker, sContainerId):
        return "cli"
    return ""


def fsStampGeneratedCode(sCode, sGenerator):
    """Return model-written test code opening with its generator stamp."""
    return S_GENERATOR_STAMP_PREFIX + sGenerator + "\n" + sCode


def _fdictStampPayload(dictResult, sGenerator):
    """Record the generator on every category that was written."""
    for sKey in T_CATEGORY_KEYS:
        dictCategory = dictResult.get(sKey)
        if (isinstance(dictCategory, dict) and not dictCategory.get("sError")
                and not dictCategory.get("bNeedsOverwriteConfirm")):
            dictCategory.setdefault("sGenerator", sGenerator)
    return dictResult


def _fsQuantitativeAnalysisFailure(dictQuantitative):
    """Return why no quantitative standards could be derived, or ''."""
    if not isinstance(dictQuantitative, dict) or dictQuantitative.get(
            "sStochasticityClassification") != "unintrospectable":
        return ""
    sError = dictQuantitative.get("sIntrospectorError") or (
        "no output could be loaded")
    return f"No quantitative standards could be derived ({sError})."


def _fsNameBothFailures(sPriorFailure, error):
    """Return a model failure, after the analysis failure that led to it."""
    if not sPriorFailure:
        return str(error)
    return f"{sPriorFailure} The language-model fallback also failed: {error}"


def _fdictFallBackForEveryCategory(
    connectionDocker, sContainerId, iStepIndex, dictWorkflow,
    dictVariables, bUseApi, sApiKey, sUser, sFailure,
):
    """Ask the consented model for all three categories, or name both failures."""
    sModel = fsResolveConsentedModel(
        connectionDocker, sContainerId, bUseApi, sApiKey)
    if not sModel:
        raise OutputAnalysisError(f"{sFailure} {S_NO_MODEL_AVAILABLE}")
    return _fdictGenerateAllTestsViaLlm(
        connectionDocker, sContainerId, iStepIndex, dictWorkflow,
        dictVariables, sModel == "api", sApiKey, sUser,
        sGenerator=S_GENERATOR_LLM_FALLBACK, sPriorFailure=sFailure)


def _fdictFallBackForQuantitative(
    connectionDocker, sContainerId, iStepIndex, dictWorkflow,
    dictVariables, bUseApi, sApiKey, sUser, sFailure,
):
    """Ask the consented model for quantitative standards, or name both failures."""
    sModel = fsResolveConsentedModel(
        connectionDocker, sContainerId, bUseApi, sApiKey)
    if not sModel:
        return moduleGenerator._fdictErrorResult(
            f"{sFailure} {S_NO_MODEL_AVAILABLE}")
    dictStep, sDirectory = moduleGenerator._ftExtractStepInfo(
        dictWorkflow, iStepIndex)
    sScripts, sPreviews = moduleGenerator.ftBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables)
    if sModel == "cli":
        moduleGenerator.fnEnsureClaudeMdInstructions(
            connectionDocker, sContainerId)
    return _fdictGenerateQuantitativeCategory(
        connectionDocker, sContainerId, sDirectory,
        ", ".join(dictStep.get("saOutputDataFiles", [])), sScripts,
        sPreviews, dictWorkflow.get("fTolerance", 1e-6), sModel == "api",
        sApiKey, sUser, S_GENERATOR_LLM_FALLBACK, sFailure)


def _fdictGenerateAllTestsViaLlm(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi, sApiKey, sUser,
    sGenerator=S_GENERATOR_LLM, sPriorFailure="",
):
    """Generate all three test categories via LLM."""
    dictStep, sDirectory = moduleGenerator._ftExtractStepInfo(
        dictWorkflow, iStepIndex)
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    sDataFiles = ", ".join(dictStep.get("saOutputDataFiles", []))
    sScripts, sPreviews = moduleGenerator.ftBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables,
    )
    if not bUseApi:
        moduleGenerator.fnEnsureClaudeMdInstructions(
            connectionDocker, sContainerId)
    moduleGenerator.fnEnsureTestsDirectory(
        connectionDocker, sContainerId, sDirectory)
    moduleGenerator.fnWriteConftestMarker(
        connectionDocker, sContainerId, sDirectory,
        dictWorkflow.get("sProjectRepoPath", ""),
    )
    dictResult = {}
    for sCategory in ("integrity", "qualitative"):
        dictResult[f"dict{sCategory.capitalize()}"] = (
            _fdictGenerateSingleCategory(
                connectionDocker, sContainerId, sDirectory,
                sCategory, sDataFiles, sScripts, sPreviews,
                bUseApi, sApiKey, sUser, sGenerator, sPriorFailure,
            )
        )
    dictResult["dictQuantitative"] = _fdictGenerateQuantitativeCategory(
        connectionDocker, sContainerId, sDirectory,
        sDataFiles, sScripts, sPreviews,
        fTolerance, bUseApi, sApiKey, sUser, sGenerator, sPriorFailure,
    )
    return dictResult


def _fsCategoryTestPath(sCategory, sDirectory):
    """Return the test file path a model-written category is saved to."""
    if sCategory == "integrity":
        return moduleGenerator.fsIntegrityTestPath(sDirectory)
    return moduleGenerator.fsQualitativeTestPath(sDirectory)


def _fdictGenerateSingleCategory(
    connectionDocker, sContainerId, sDirectory,
    sCategory, sDataFiles, sScriptContents, sDataPreviews,
    bUseApi, sApiKey, sUser, sGenerator=S_GENERATOR_LLM, sPriorFailure="",
):
    """Generate one Python test category via LLM, with error isolation.

    Only the model's answer is isolated: a failed call or an
    unparseable reply becomes this category's error, while a failed
    write surfaces as itself.
    """
    sPrompt = moduleGenerator._fsBuildCategoryPrompt(
        sCategory, sDirectory, sDataFiles, sScriptContents, sDataPreviews,
    )
    sRaw = ""
    try:
        sRaw = moduleGenerator._fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        sCode = moduleGenerator.fsParseGeneratedCode(sRaw)
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        moduleGenerator._fnAppendErrorLog(
            f"[{sCategory}] {error}\n"
            f"First 300 chars of raw output:\n{sRaw[:300]}"
        )
        return moduleGenerator._fdictErrorResult(
            _fsNameBothFailures(sPriorFailure, error))
    dictResult = moduleGenerator._fdictWriteTestFile(
        connectionDocker, sContainerId,
        fsStampGeneratedCode(sCode, sGenerator),
        _fsCategoryTestPath(sCategory, sDirectory),
    )
    dictResult["sGenerator"] = sGenerator
    return dictResult


def _fdictGenerateQuantitativeCategory(
    connectionDocker, sContainerId, sDirectory,
    sDataFiles, sScriptContents, sDataPreviews,
    fTolerance, bUseApi, sApiKey, sUser,
    sGenerator=S_GENERATOR_LLM, sPriorFailure="",
):
    """Generate quantitative standards JSON via LLM."""
    sPrompt = moduleGenerator._fsBuildQuantitativePrompt(
        sDirectory, sDataFiles, sScriptContents,
        sDataPreviews, fTolerance,
    )
    try:
        sRaw = moduleGenerator._fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        dictStandards = moduleGenerator.fdictParseQuantitativeJson(sRaw)
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        return moduleGenerator._fdictErrorResult(
            _fsNameBothFailures(sPriorFailure, error))
    dictStandards["fDefaultRtol"] = fTolerance
    dictStandards["sGenerator"] = sGenerator
    dictResult = moduleGenerator._fdictWriteQuantitativeFiles(
        connectionDocker, sContainerId, sDirectory, dictStandards,
    )
    if not dictResult.get("bNeedsOverwriteConfirm"):
        dictResult["sGenerator"] = sGenerator
    return dictResult
