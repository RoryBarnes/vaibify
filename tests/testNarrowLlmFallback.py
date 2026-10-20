"""Test generation falls back to a language model narrowly, with consent.

The rules come from the plan's ruling, not from the code:

* fall back ONLY when vaibify could not understand the outputs -- the
  introspection program failed or printed nothing parseable, or no
  output loaded so no quantitative standards could be derived;
* never fall back on a control-plane refusal, a write failure, a
  pending overwrite confirmation or a missing step: each surfaces
  unchanged and no model is asked;
* consent is a Claude CLI already in the container, or the stored API
  key the researcher asked to use; with neither, the error names both
  failures;
* the agent-safe route cannot reach a model at all;
* every category and every written file records its generator.

The deterministic generator and the introspection runner here are the
REAL ones, driven against a container double; only the model is faked,
and every call to it is counted.
"""

import json

import pytest

from tests.testCarrierMigratedRoutes import (
    DockerDoubleForTestGeneration,
    S_INTROSPECT_COMMAND_MARKER,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import S_CONTAINER_ID
from vaibify.config.mutationAdmission import ControlPlaneRefusalError
from vaibify.gui import llmTestGeneration, testGenerator
from vaibify.gui.pipelineUtils import OutputAnalysisError


S_REPO = "/workspace/repo"
S_STEP = S_REPO + "/step"
S_UNLOADABLE_REPORT = json.dumps([{
    "sFileName": "blob.bin", "sError": "unsupported binary format",
    "bLoadable": False, "listBenchmarks": []}])
S_MODEL_REPLY = json.dumps({"listStandards": [{
    "sName": "fMean", "sDataFile": "blob.bin", "sAccessPath": "mean",
    "fValue": 1.5, "sUnit": ""}]})


class ContainerDouble:
    """Holds written files and answers the commands the generator runs."""

    def __init__(self, iIntrospectionExit=0, bHasClaude=False,
                 errorOnTestWrite=None, dictExistingFiles=None):
        self.iIntrospectionExit = iIntrospectionExit
        self.bHasClaude = bHasClaude
        self.errorOnTestWrite = errorOnTestWrite
        self.dictFiles = dict(dictExistingFiles or {})

    def fnWriteFile(self, sContainerId, sPath, baContent):
        """Fail only the integrity TEST file, so a fallback that wrongly
        caught the failure would get as far as asking the model."""
        if self.errorOnTestWrite and sPath == (
                testGenerator.fsIntegrityTestPath(S_STEP)):
            raise self.errorOnTestWrite
        self.dictFiles[sPath] = baContent

    def fnWriteFileFromStream(self, sContainerId, sPath, fileSource,
                              **dictKeywords):
        self.fnWriteFile(sContainerId, sPath, fileSource.read())

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        if sCommand == "which claude":
            return (0 if self.bHasClaude else 1), ""
        if "_vaibify_introspect_" in sCommand and not sCommand.startswith(
                "rm "):
            return self.iIntrospectionExit, S_UNLOADABLE_REPORT
        return 0, ""

    def fsReadText(self, sPath):
        return self.dictFiles[sPath].decode("utf-8")


@pytest.fixture
def listModelCalls(monkeypatch):
    """Fake the model; record each call's bUseApi."""
    listCalls = []

    def fsInvokeModel(connectionDocker, sContainerId, sPrompt, bUseApi,
                      sApiKey, sUser=None):
        listCalls.append(bUseApi)
        return S_MODEL_REPLY

    monkeypatch.setattr(testGenerator, "_fsInvokeLlm", fsInvokeModel)
    monkeypatch.setattr(testGenerator, "fnEnsureClaudeMdInstructions",
                        lambda *args, **kwargs: None)
    return listCalls


def _fdictWorkflow():
    return {"sProjectRepoPath": S_REPO, "fTolerance": 1e-6, "listSteps": [{
        "sName": "Step", "sDirectory": "step",
        "saOutputDataFiles": ["blob.bin"], "saDataCommands": [],
        "saPlotCommands": []}]}


def _fdictGenerate(containerDouble, bUseApi=False, sApiKey=None,
                   iStepIndex=0):
    return testGenerator.fdictGenerateAllTests(
        containerDouble, "cid", iStepIndex, _fdictWorkflow(), {},
        bUseApi=bUseApi, sApiKey=sApiKey)


def _fdictStandardsOnDisk(containerDouble):
    return json.loads(containerDouble.fsReadText(
        testGenerator.fsQuantitativeStandardsPath(S_STEP)))


# ------------------------------------------------- eligible failures


@pytest.mark.falsification
def testUnparseableOutputsFallBackForQuantitativeAndSayWhoWroteThem(
        listModelCalls):
    """Kills: dropping the generator stamp from model-derived standards."""
    containerDouble = ContainerDouble(bHasClaude=True)
    dictResult = _fdictGenerate(containerDouble)
    assert listModelCalls == [False]
    assert dictResult["dictQuantitative"]["sGenerator"] == "llm-fallback"
    assert dictResult["dictIntegrity"]["sGenerator"] == "deterministic"
    assert dictResult["dictQualitative"]["sGenerator"] == "deterministic"
    assert _fdictStandardsOnDisk(containerDouble)["sGenerator"] == (
        "llm-fallback")


def testAStoredKeyTheResearcherAskedToUseIsConsent(listModelCalls):
    dictResult = _fdictGenerate(
        ContainerDouble(bHasClaude=False), bUseApi=True, sApiKey="key")
    assert listModelCalls == [True]
    assert dictResult["dictQuantitative"]["sGenerator"] == "llm-fallback"


def testAFailedIntrospectionFallsBackForEveryCategory(listModelCalls):
    containerDouble = ContainerDouble(iIntrospectionExit=1, bHasClaude=True)
    dictResult = _fdictGenerate(containerDouble)
    assert listModelCalls == [False, False, False]
    for sKey in ("dictIntegrity", "dictQualitative", "dictQuantitative"):
        assert dictResult[sKey]["sGenerator"] == "llm-fallback", sKey
    sIntegrity = containerDouble.fsReadText(
        dictResult["dictIntegrity"]["sFilePath"])
    assert sIntegrity.startswith("# vaibify-generator: llm-fallback\n")


def testWithNoConsentedModelTheErrorNamesBothFailures(listModelCalls):
    dictResult = _fdictGenerate(ContainerDouble(bHasClaude=False))
    sError = dictResult["dictQuantitative"]["sError"]
    assert "blob.bin: unsupported binary format" in sError
    assert llmTestGeneration.S_NO_MODEL_AVAILABLE in sError
    with pytest.raises(OutputAnalysisError) as excInfo:
        _fdictGenerate(ContainerDouble(iIntrospectionExit=1))
    assert "Introspection failed" in str(excInfo.value)
    assert llmTestGeneration.S_NO_MODEL_AVAILABLE in str(excInfo.value)
    assert listModelCalls == []


def testWhenNothingWasMisunderstoodNoModelIsAsked(listModelCalls, monkeypatch):
    monkeypatch.setattr(
        ContainerDouble, "ftResultExecuteCommand",
        lambda self, sContainerId, sCommand, sWorkdir=None: (
            (0, "[]") if "_vaibify_introspect_" in sCommand else (0, "")))
    dictResult = _fdictGenerate(ContainerDouble(bHasClaude=True))
    assert listModelCalls == []
    assert dictResult["dictQuantitative"]["sGenerator"] == "deterministic"


# --------------------------------------------- never a reason to ask


@pytest.mark.falsification
def testAWriteFailureSurfacesUnchangedAndAsksNoModel(listModelCalls):
    """Kills: widening the allow-list to a broad except."""
    with pytest.raises(RuntimeError, match="disk full"):
        _fdictGenerate(ContainerDouble(
            bHasClaude=True, errorOnTestWrite=RuntimeError("disk full")))
    assert listModelCalls == []


def testARefusalSurfacesUnchangedAndAsksNoModel(listModelCalls):
    with pytest.raises(ControlPlaneRefusalError):
        _fdictGenerate(ContainerDouble(
            bHasClaude=True,
            errorOnTestWrite=ControlPlaneRefusalError("not admitted")))
    assert listModelCalls == []


def testAPendingOverwriteConfirmationAsksNoModel(listModelCalls):
    """The INTEGRITY file is the researcher's, so the quantitative tier
    is still unintrospectable: only the pending confirmation stops the
    fallback, which is what this asserts."""
    sCustomTest = testGenerator.fsIntegrityTestPath(S_STEP)
    dictResult = _fdictGenerate(ContainerDouble(
        bHasClaude=True,
        dictExistingFiles={sCustomTest: b"def test_mine():\n    pass\n"}))
    assert dictResult["bNeedsOverwriteConfirm"] is True
    assert dictResult["dictQuantitative"][
        "sStochasticityClassification"] == "unintrospectable"
    assert listModelCalls == []


@pytest.mark.falsification
def testTheEveryCategoryFallbackAsksBeforeReplacingAResearchersTest(
        listModelCalls):
    """Kills: the model fallback writing past the overwrite confirmation."""
    sCustomTest = testGenerator.fsIntegrityTestPath(S_STEP)
    baCustom = b"def test_mine():\n    pass\n"
    containerDouble = ContainerDouble(
        iIntrospectionExit=1, bHasClaude=True,
        dictExistingFiles={sCustomTest: baCustom})
    dictResult = _fdictGenerate(containerDouble)
    assert dictResult["bNeedsOverwriteConfirm"] is True
    assert dictResult["listModifiedFiles"] == [sCustomTest]
    assert containerDouble.dictFiles[sCustomTest] == baCustom
    assert listModelCalls == []
    dictForced = testGenerator.fdictGenerateAllTests(
        containerDouble, "cid", 0, _fdictWorkflow(), {},
        bForceOverwrite=True)
    assert dictForced["dictIntegrity"]["sGenerator"] == "llm-fallback"
    assert listModelCalls == [False, False, False]


def testAMissingStepSurfacesUnchangedAndAsksNoModel(listModelCalls):
    with pytest.raises(IndexError):
        _fdictGenerate(ContainerDouble(bHasClaude=True), iStepIndex=5)
    assert listModelCalls == []


# ------------------------------------------------------- agent lane


class DockerDoubleWithUnloadableOutputs(DockerDoubleForTestGeneration):
    """The route double, with an unloadable output and a Claude CLI."""

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        tExec = super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir)
        if sCommand == "which claude":
            return 0, "/usr/local/bin/claude"
        if (S_INTROSPECT_COMMAND_MARKER in sCommand
                and sCommand.startswith("python3 ")):
            return 0, S_UNLOADABLE_REPORT
        return tExec


def testTheAgentRouteCannotReachAModelThatTheResearcherRouteReaches(
        listModelCalls):
    """Same container, same outputs: only the researcher's route asks."""
    client, _ = _tConnectGatedClient(DockerDoubleWithUnloadableOutputs())
    responseAgent = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/generate-test-deterministic",
        json={})
    assert responseAgent.status_code == 200, responseAgent.text
    assert listModelCalls == []
    assert responseAgent.json()["dictQuantitative"][
        "sStochasticityClassification"] == "unintrospectable"
    responseResearcher = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/generate-test",
        json={"bDeterministic": True, "bForceOverwrite": True})
    assert responseResearcher.status_code == 200, responseResearcher.text
    assert listModelCalls == [False]
    assert responseResearcher.json()["dictQuantitative"]["sGenerator"] == (
        "llm-fallback")
