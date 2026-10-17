"""Each output records how it was produced, only when it actually was.

The rules come from the design (``design/cpuClassReproducibility.md``,
I7 and "Hardware and settings provenance") and the build guide's Phase
7, not from the code under test:

* a record is written only by the section that really ran -- data
  outputs after the data section, plot outputs after the plot section;
  a tests-only run, a failed section and a skipped section relabel
  nothing;
* a record is bound to the digest it names, so an output whose bytes
  changed is "production context unknown";
* nothing identifying is recorded: no CPU model string, no host name;
* a byte difference is explained from the records -- the production
  context beside the rerun's -- and the dashboard says so neutrally.

The runs go through the REAL runner against real directories, under two
contexts made to differ in the facts that matter (the ``x86-64`` level
and the visible cores), by standing in for Stage A's reading only.
"""

import asyncio
import json
import os
import socket

import pytest

from tests.assessedRerunLaneHarness import (
    S_LANE_CONTAINER_ID,
    LocalRunnerContainer,
    fdictRerunThroughTheLane,
)
from tests.rerunAssessmentHarness import (
    fdictBuildStep,
    fdictBuildWorkflow,
    fnPinManifest,
    fnWriteText,
    fsWriteJsonCommand,
)
from vaibify.gui import determinismEnvironment, outputProvenance
from vaibify.gui import pipelineRunner, pipelineTestRunner
from vaibify.reproducibility import rerunProcedure


S_DATA = "data.json"
S_PLOT = "plot.txt"
_DICT_CONTEXT_A = {"sArchitecture": "x86_64", "sVendor": "GenuineIntel",
                   "sLevel": "x86-64-v3", "iVisibleCores": 8,
                   "bReadable": True}
_DICT_CONTEXT_B = {"sArchitecture": "x86_64", "sVendor": "AuthenticAMD",
                   "sLevel": "x86-64-v2", "iVisibleCores": 2,
                   "bReadable": True}


def _fnRunUnder(monkeypatch, dictCapabilities):
    """Make Stage A report these capabilities; Stage B stays real."""
    fdictOriginal = determinismEnvironment._fdictRunProcedureStage

    def fdictStage(connectionDocker, sContainerId, sProjectRepoPath,
                   listCommand, sPrefix):
        if sPrefix == rerunProcedure.S_CAPABILITIES_RESULT_PREFIX:
            return {"dictCapabilities": dict(dictCapabilities),
                    "listSettingsPresent": []}
        return fdictOriginal(connectionDocker, sContainerId,
                             sProjectRepoPath, listCommand, sPrefix)

    monkeypatch.setattr(
        determinismEnvironment, "_fdictRunProcedureStage", fdictStage)


def _fdictPublish(sRoot, sPlotCommand="printf 'plot' > plot.txt",
                  bPlotOnly=False):
    """Publish one step with a data output, a plot output and a test."""
    os.makedirs(os.path.join(sRoot, "StepOne"), exist_ok=True)
    dictStep = fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=[fsWriteJsonCommand(S_DATA, {"x": 1.0})],
        saPlotCommands=[sPlotCommand], saTestCommands=["true"],
        saOutputDataFiles=[S_DATA], saPlotFiles=[S_PLOT],
        bPlotOnly=bPlotOnly)
    dictWorkflow = fdictBuildWorkflow([dictStep])
    dictWorkflow["sProjectRepoPath"] = sRoot
    fnWriteText(sRoot, ".vaibify/workflows/project.json",
                json.dumps(dictWorkflow))
    return dictWorkflow


def _fsWorkflowPath(sRoot):
    return os.path.join(sRoot, ".vaibify", "workflows", "project.json")


async def _fnDiscard(dictEvent):
    del dictEvent


def _fnRun(sRoot, dictWorkflow, sRunMode="full"):
    """Run the project through the real runner in one mode."""
    sPath = _fsWorkflowPath(sRoot)
    if sRunMode == "tests":
        asyncio.run(pipelineTestRunner.fiRunAllTests(
            LocalRunnerContainer(), S_LANE_CONTAINER_ID, dictWorkflow,
            os.path.dirname(sPath), _fnDiscard))
        return
    asyncio.run(pipelineRunner.fiRunSelectedSteps(
        LocalRunnerContainer(), S_LANE_CONTAINER_ID, [0], dictWorkflow,
        sPath, os.path.dirname(sPath), _fnDiscard, sRunMode=sRunMode))


def _fsLevelRecordedFor(sRoot, sRelative):
    """Return the x86-64 level of the context recorded for one output."""
    with open(os.path.join(
            sRoot, outputProvenance.S_OUTPUT_PROVENANCE_REPO_PATH)) as fileIn:
        dictRecord = json.load(fileIn)
    sContextId = dictRecord["dictOutputs"][sRelative]["sRunContextId"]
    return dictRecord["dictRunContexts"][sContextId]["dictSettings"][
        "dictCapabilities"]["sLevel"]


def _fsRoot(tmp_path):
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot)
    return sRoot


def _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow):
    """Produce every output under context A, and check it says so."""
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_A)
    _fnRun(sRoot, dictWorkflow)
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_DATA}") == "x86-64-v3"
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_PLOT}") == "x86-64-v3"


# ----------------------------------------------------------------------
# No relabelling, by run kind
# ----------------------------------------------------------------------


def testATestsOnlyRunRelabelsNothing(tmp_path, monkeypatch):
    """Running the tests under B leaves every record reading A.

    Not a falsification entry: the tests lane carries no run context at
    all, so no mutation of the recorder can reach it. The property is
    structural, and this pins it.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    _fnRun(sRoot, dictWorkflow, "tests")
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_DATA}") == "x86-64-v3"
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_PLOT}") == "x86-64-v3"


@pytest.mark.falsification
def testAPlotsOnlyRunRelabelsOnlyThePlots(tmp_path, monkeypatch):
    """Kills: recording every declared output after any section."""
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    _fnRun(sRoot, dictWorkflow, "plotsOnly")
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_PLOT}") == "x86-64-v2"
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_DATA}") == "x86-64-v3"


@pytest.mark.falsification
def testADataOnlyRunRelabelsOnlyTheData(tmp_path, monkeypatch):
    """Kills: recording every declared output after any section."""
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    _fnRun(sRoot, dictWorkflow, "dataOnly")
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_DATA}") == "x86-64-v2"
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_PLOT}") == "x86-64-v3"


@pytest.mark.falsification
def testAPlotOnlyStepsFullRunRelabelsOnlyThePlots(tmp_path, monkeypatch):
    """Its data section is skipped, so its data keeps its old record.

    Kills: recording the data section of a step whose data did not run.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    dictWorkflow["listSteps"][0]["bPlotOnly"] = True
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    _fnRun(sRoot, dictWorkflow)
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_PLOT}") == "x86-64-v2"
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_DATA}") == "x86-64-v3"


@pytest.mark.falsification
def testAFailedPlotSectionRelabelsOnlyTheData(tmp_path, monkeypatch):
    """A plot command that wrote its file and then failed relabels nothing.

    Kills: recording the plot section whatever its exit status.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    dictWorkflow["listSteps"][0]["saPlotCommands"] = [
        "printf 'plot' > plot.txt; false"]
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    _fnRun(sRoot, dictWorkflow)
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_DATA}") == "x86-64-v2"
    assert _fsLevelRecordedFor(sRoot, f"StepOne/{S_PLOT}") == "x86-64-v3"


def testAnIdenticalRerunLeavesTheRecordByteIdentical(tmp_path, monkeypatch):
    """The same computer under the same settings is one context, kept as first seen."""
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    sPath = os.path.join(sRoot, outputProvenance.S_OUTPUT_PROVENANCE_REPO_PATH)
    with open(sPath, "rb") as fileIn:
        baBefore = fileIn.read()
    _fnRun(sRoot, dictWorkflow)
    with open(sPath, "rb") as fileIn:
        assert fileIn.read() == baBefore


# ----------------------------------------------------------------------
# A record is bound to its digest, and identifies no machine
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testChangedBytesAreProductionContextUnknown(tmp_path, monkeypatch):
    """The envelope copies a context only for the bytes it was recorded for.

    Kills: copying a record whatever the file's current digest.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    dictCopy = outputProvenance.fdictBuildEnvelopeOutputProvenance(
        sRoot, dictWorkflow)
    assert dictCopy["dictOutputs"][f"StepOne/{S_DATA}"]["sState"] == "recorded"
    fnWriteText(sRoot, f"StepOne/{S_DATA}", '{"x": 2.0}')
    dictCopy = outputProvenance.fdictBuildEnvelopeOutputProvenance(
        sRoot, dictWorkflow)
    assert dictCopy["dictOutputs"][f"StepOne/{S_DATA}"] == {
        "sState": "production context unknown"}
    assert dictCopy["dictOutputs"][f"StepOne/{S_PLOT}"]["sState"] == (
        "recorded")


@pytest.mark.falsification
def testNoIdentifyingFieldIsRecorded(tmp_path, monkeypatch):
    """No CPU model string and no host name, in the record or the envelope copy.

    Kills: recording the machine's host name with its capabilities.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnRun(sRoot, dictWorkflow)
    with open(os.path.join(
            sRoot, outputProvenance.S_OUTPUT_PROVENANCE_REPO_PATH)) as fileIn:
        sRecord = fileIn.read()
    sEnvelope = json.dumps(outputProvenance.fdictBuildEnvelopeOutputProvenance(
        sRoot, dictWorkflow))
    for sText in (sRecord, sEnvelope):
        assert "model name" not in sText.lower()
        assert socket.gethostname() not in sText
        assert os.uname().nodename not in sText
        assert '"sHostName"' not in sText and '"sModel' not in sText


# ----------------------------------------------------------------------
# A difference is explained, never predicted
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testTheRerunReportsTheProductionContextBesideItsOwn(tmp_path, monkeypatch):
    """Two computers, simulated: the report names both contexts.

    The output is produced under A and published; the rerun runs under B
    and produces other bytes. The differing output carries A's context
    beside the rerun's.

    Kills: grading a byte difference without the context that produced it.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    fnPinManifest(sRoot, dictWorkflow)
    dictWorkflow["listSteps"][0]["saDataCommands"] = [
        fsWriteJsonCommand(S_DATA, {"x": 1.5})]
    fnWriteText(sRoot, ".vaibify/workflows/project.json",
                json.dumps(dictWorkflow))
    fnPinManifest(sRoot, dictWorkflow)
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictFile = [dictFile for dictFile in dictOutcome["listFileOutcomes"]
                if dictFile["sPath"] == f"StepOne/{S_DATA}"][0]
    assert dictFile["sObserved"] != dictFile["sExpected"]
    dictProduced = dictFile["dictProductionContext"]
    assert dictProduced["sState"] == "recorded"
    assert dictProduced["dictRunContext"]["dictSettings"][
        "dictCapabilities"]["sLevel"] == "x86-64-v3"
    assert dictOutcome["dictRerunContext"]["dictSettings"][
        "dictCapabilities"]["sLevel"] == "x86-64-v2"
    assert _fdictContextsOf(dictOutcome)["dictCapabilities"]


def _fdictContextsOf(dictOutcome):
    """Return the rerun's Stage A record the assessment report carries."""
    return dictOutcome["dictAssessmentReport"]["dictContexts"][
        "dictCapabilities"]


@pytest.mark.falsification
def testAStepProducedUnderAnotherContextIsNamedNeutrally(tmp_path, monkeypatch):
    """The note names the facts that differ, and only when they do.

    Kills: a note that compares no facts, which never names the
    difference a researcher needs to understand a changed result.
    """
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    dictContextA = outputProvenance.fdictCurrentContext(S_LANE_CONTAINER_ID)
    dictContextB = json.loads(json.dumps(dictContextA))
    dictContextB["dictSettings"]["dictCapabilities"].update(_DICT_CONTEXT_B)
    dictNotes = outputProvenance.fdictBuildOutputContextNotes(
        dictWorkflow, sRoot, dictContextB)
    dictNote = dictNotes["0"]
    assert dictNote["sMessage"] == (
        "Produced under different execution settings or hardware")
    assert "computer" not in dictNote["sMessage"]
    assert {"sFact": "x86-64 level", "sThere": "x86-64-v3",
            "sHere": "x86-64-v2"} in dictNote["listDifferences"]
    assert sorted(dictNote["listPaths"]) == [
        f"StepOne/{S_DATA}", f"StepOne/{S_PLOT}"]
    assert outputProvenance.fdictBuildOutputContextNotes(
        dictWorkflow, sRoot, dictContextA) == {}
    assert outputProvenance.fdictBuildOutputContextNotes(
        dictWorkflow, sRoot, None) == {}


@pytest.mark.falsification
def testAManifestMismatchExplainedByContextOffersAnOption(tmp_path, monkeypatch):
    """Every mismatch explained: the option is offered; one unexplained: it is not.

    Kills: offering the option when only some mismatches are explained.
    """
    from vaibify.reproducibility import dataArchiver
    sRoot = _fsRoot(tmp_path)
    dictWorkflow = _fdictPublish(sRoot)
    _fnProduceUnderA(monkeypatch, sRoot, dictWorkflow)
    sEnvelopePath = os.path.join(sRoot, ".vaibify", "environment.json")
    fnWriteText(sRoot, ".vaibify/environment.json", json.dumps({
        "dictOutputProvenance":
            outputProvenance.fdictBuildEnvelopeOutputProvenance(
                sRoot, dictWorkflow)}))
    assert os.path.isfile(sEnvelopePath) and dataArchiver
    fnPinManifest(sRoot, dictWorkflow)
    dictWorkflow["listSteps"][0]["saDataCommands"] = [
        fsWriteJsonCommand(S_DATA, {"x": 1.5})]
    _fnRunUnder(monkeypatch, _DICT_CONTEXT_B)
    _fnRun(sRoot, dictWorkflow, "dataOnly")
    from vaibify.reproducibility.manifestWriter import flistVerifyManifest
    listMismatches = [d for d in flistVerifyManifest(sRoot)
                      if d["sPath"].startswith("StepOne/")]
    assert [d["sPath"] for d in listMismatches] == [f"StepOne/{S_DATA}"]
    dictExplained = outputProvenance.fdictExplainManifestMismatches(
        sRoot, listMismatches)
    assert dictExplained["bOnlyContextDifferences"] is True
    assert "rerun every step here and regenerate the manifest" in (
        dictExplained["sOption"])
    assert "x86-64 level: was x86-64-v3, now x86-64-v2" in (
        dictExplained["sSummary"])
    listMixed = listMismatches + [{"sPath": "Other/unrecorded.txt",
                                   "sExpected": "0" * 64, "sActual": None}]
    dictMixed = outputProvenance.fdictExplainManifestMismatches(
        sRoot, listMixed)
    assert dictMixed["bOnlyContextDifferences"] is False
    assert dictMixed["sOption"] == ""
