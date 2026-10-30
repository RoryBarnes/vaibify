"""The rerun lane grades with the assessor, and the host checks the assessor.

Driven through ``rerunVerification.fdictRerunAndVerifyWorkflow`` -- what
the shadow runs inside its own admission -- with the REAL pipeline runner
and a container stand-in whose commands really run against real
directories (``tests/assessedRerunLaneHarness.py``). The runner names
every command, points every test command's JUnit report at the rerun's
private evidence directory and records every exit status; a real pytest
writes the reports; vaibify's own render of the assessor grades them;
the host re-derives the verdict and cross-checks the observations.

The expected outcomes are the design's precedence table and its rules
for the recorded comparison policy and historical attestations
(``design/cpuClassReproducibility.md``), never the code under test.
"""

import importlib.util
import io
import json
import os

import pytest

from tests.levelGateStubs import fdictCurrentAssessmentFields
from PIL import Image

from vaibify.gui import pipelineRunner
from vaibify.reproducibility import (
    assessmentLane,
    comparisonPolicyV1,
    l3Attestation,
    rerunAssessment,
)
from vaibify.reproducibility.l3Attestation import (
    fbL3AttestationCurrent,
    fdictBuildAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)
from tests.assessedRerunLaneHarness import fdictRerunThroughTheLane
from tests.rerunAssessmentHarness import (
    S_PYTEST,
    fdictBuildStep,
    fdictBuildWorkflow,
    fdictQuantitativeStandard,
    fdictWriteGeneratedTests,
    fnPinManifest,
    fnWriteBytes,
    fnWriteText,
    fsWriteJsonCommand,
)


S_VALUES = "values.json"


def _fsRepository(tmp_path):
    """Return a fresh repository directory."""
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot, exist_ok=True)
    return sRoot


def _fdictPublish(sRoot, listSteps):
    """Write the workflow file, pin the manifest, return the workflow."""
    dictWorkflow = fdictBuildWorkflow(listSteps)
    dictWorkflow["sProjectRepoPath"] = sRoot
    fnWriteText(sRoot, ".vaibify/workflows/project.json",
                json.dumps(dictWorkflow))
    fnPinManifest(sRoot, dictWorkflow)
    return dictWorkflow


def _fdictQuantitativeStep(sRoot, sDirectory, dictValues, fExpected,
                           sExtraData=""):
    """Publish a step writing ``dictValues``, checked by one standard."""
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, sDirectory, "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", fExpected)])}
    fnWriteText(sRoot, f"{sDirectory}/{S_VALUES}",
                json.dumps(dictValues, sort_keys=True))
    listData = [fsWriteJsonCommand(S_VALUES, dictValues)]
    if sExtraData:
        listData.append(sExtraData)
    return fdictBuildStep(
        f"Step {sDirectory}", sDirectory, saDataCommands=listData,
        saOutputDataFiles=[S_VALUES], dictTests=dictTests)


def _fbaPng(tColor, tChanged=None):
    """Return a 16x16 RGB PNG, one pixel optionally recolored."""
    imageFigure = Image.new("RGB", (16, 16), tColor)
    if tChanged is not None:
        imageFigure.putpixel((0, 0), tChanged)
    bufferOut = io.BytesIO()
    imageFigure.save(bufferOut, format="PNG")
    return bufferOut.getvalue()


# ----------------------------------------------------------------------
# The precedence, end to end through the real runner
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testAFailingTestWithByteIdenticalOutputsIsNotReproduced(tmp_path):
    """F10 through the real runner: byte equality never overrides a failure.

    Kills: the runner not pointing a test command's JUnit report at the
    rerun's evidence directory, which leaves the check unasserted.
    """
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 2.0)])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["bRerunAttempted"] is True
    assert dictOutcome["iOutputHashesMatched"] == 1
    assert dictOutcome["sVerdict"] == "not-reproduced"
    assert dictOutcome["iAssessmentExitCode"] == 1
    assert dictOutcome["bPassed"] is False
    assert any("PYTEST_ADDOPTS=" in sCommand and "A01.quantitative.1.xml"
               in sCommand for sCommand in dictOutcome["listCommandsRun"])


@pytest.mark.falsification
def testASkippedTestIsNotEnoughEvidence(tmp_path):
    """A check that skipped itself leaves its byte-identical output unassessed.

    Kills: the lane counting "not enough evidence" as a pass.
    """
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 1.0)])
    fnWriteText(sRoot, "StepOne/conftest.py",
                "import pytest\n\n\ndef pytest_runtest_setup(item):\n"
                "    pytest.skip('skipped on purpose')\n")
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["sVerdict"] == "insufficient-evidence"
    assert dictOutcome["iAssessmentExitCode"] == 5
    assert dictOutcome["bPassed"] is False


@pytest.mark.falsification
def testADeletedTestFileIsPublishedInputsChangedAndNothingRuns(tmp_path):
    """A changed published input stops the rerun before anything is deleted.

    Kills: clearing and running the steps after ``prepare`` already
    found a published input missing.
    """
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 1.0)])
    os.remove(os.path.join(
        sRoot, "StepOne/tests/test_quantitative_stepOne.py"))
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["sVerdict"] == "inputs-changed"
    assert dictOutcome["iAssessmentExitCode"] == 3
    assert dictOutcome["dictPreRerunClearing"] == {}
    assert not any("printf" in sCommand
                   for sCommand in dictOutcome["listCommandsRun"])


@pytest.mark.falsification
def testAToleranceEditedDuringTheRunIsPublishedInputsChanged(tmp_path):
    """A step that loosens its own tolerance changed a published input.

    Kills: the assessor judging integrity only before the steps ran; the
    host's own re-hash then disagrees with the report.
    """
    sRoot = _fsRepository(tmp_path)
    sStandards = "tests/quantitative_standards_stepOne.json"
    sLoosen = (
        f"python3 -c \"import json; p='{sStandards}'; d=json.load(open(p)); "
        "d['listStandards'][0]['fRtol']=0.5; json.dump(d, open(p, 'w'))\"")
    dictWorkflow = _fdictPublish(sRoot, [_fdictQuantitativeStep(
        sRoot, "StepOne", {"x": 1.0}, 1.0, sExtraData=sLoosen)])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["sVerdict"] == "inputs-changed"
    assert dictOutcome["dictAssessmentReport"]["dictIntegrity"][
        "bChangedDuringRun"] is True


@pytest.mark.falsification
def testADataCommandThatWritesNothingIsFailedMissing(tmp_path):
    """A command that exits 0 and writes nothing is caught by the shared clearing.

    Kills: the shared clearing program deleting nothing, which leaves the
    published file in place to be graded as regenerated.
    """
    sRoot = _fsRepository(tmp_path)
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", "{}")
    dictWorkflow = _fdictPublish(sRoot, [fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"],
        saOutputDataFiles=[S_VALUES])])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictOutput = dictOutcome["dictAssessmentReport"]["listOutputs"][0]
    assert dictOutput["sOutcome"] == "failed"
    assert "missing" in dictOutput["sReason"]
    assert dictOutcome["iAssessmentExitCode"] == 1


@pytest.mark.falsification
def testAnEarlyDataFailureStillRunsAndGradesTheLaterSteps(tmp_path):
    """A failed step does not stop the run; later steps are still graded.

    Kills: the runner stopping the whole run at the first failed step.
    """
    sRoot = _fsRepository(tmp_path)
    dictFirst = fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["false"],
        saOutputDataFiles=["first.json"])
    fnWriteText(sRoot, "StepOne/first.json", "{}")
    dictSecond = _fdictQuantitativeStep(sRoot, "StepTwo", {"x": 1.0}, 1.0)
    dictWorkflow = _fdictPublish(sRoot, [dictFirst, dictSecond])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictReport = dictOutcome["dictAssessmentReport"]
    dictOutcomes = {dictOutput["sPath"]: dictOutput["sOutcome"]
                    for dictOutput in dictReport["listOutputs"]}
    assert dictOutcomes == {"StepOne/first.json": "failed",
                            f"StepTwo/{S_VALUES}": "byte-identical"}
    dictStatuses = {dictRecord["sInvocationId"]: dictRecord["iExitStatus"]
                    for dictRecord in dictReport["listInvocations"]}
    assert dictStatuses["A01.data.1"] == 1
    assert dictStatuses["A02.data.1"] == 0
    assert dictStatuses["A02.quantitative.1"] == 0
    assert dictOutcome["sVerdict"] == "not-reproduced"


@pytest.mark.falsification
def testTheAuthorsPassingMarkersCannotStandInForTestsThatDidNotRun(tmp_path):
    """Stale evidence cannot pass: markers are never read for the assessment.

    The tree carries the author's passing test markers, exactly as a
    shadow seeded from the project does; the rerun's own test command
    selects nothing, so the standard was never evaluated.

    Kills: reading a standard absent from this run's reports as passed.
    """
    sRoot = _fsRepository(tmp_path)
    dictStep = _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 1.0)
    dictStep["dictTests"]["dictQuantitative"]["saCommands"] = [
        f"{S_PYTEST} tests/test_quantitative_stepOne.py -k selectsNothing"]
    fnWriteText(sRoot, ".vaibify/test_markers/assessment/stepStepone.json",
                json.dumps({"dictRuns": {"run": {"dictOutcomes": {
                    "test_quantitative_benchmark[xValue]": "passed"}}}}))
    dictWorkflow = _fdictPublish(sRoot, [dictStep])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    dictCheck = dictOutcome["dictAssessmentReport"]["listOutputs"][0][
        "listAgreementChecks"][0]
    assert dictCheck["sOutcome"] == "not executed"
    assert dictOutcome["sVerdict"] == "insufficient-evidence"


# ----------------------------------------------------------------------
# The host re-derives, and cross-checks what the assessor observed
# ----------------------------------------------------------------------


def _fnForgeTheAssessorReport(monkeypatch, fnForge):
    """Let the assessor answer, then change its report before the host reads it."""
    fdictReal = assessmentLane.fdictParseAssessorReport

    def fdictForged(iExitCode, sStdout, sStderr):
        dictParsed = fdictReal(iExitCode, sStdout, sStderr)
        if "dictReport" in dictParsed:
            fnForge(dictParsed["dictReport"])
        return dictParsed

    monkeypatch.setattr(
        assessmentLane, "fdictParseAssessorReport", fdictForged)


def _fnRegradeAsConsistent(dictReport):
    """Make a tampered report consistent with its own observations."""
    dictRegraded = comparisonPolicyV1.fdictRederiveReport(
        dictReport, _DICT_PLAN_HOLDER["dictPlan"])
    dictReport.clear()
    dictReport.update(dictRegraded)


_DICT_PLAN_HOLDER = {}


@pytest.mark.falsification
def testAForgedVerdictIsCaughtByReDerivation(tmp_path, monkeypatch):
    """A report whose verdict does not follow from its observations is refused.

    Kills: adopting the assessor's verdict without re-deriving it.
    """
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(sRoot, [
        _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 2.0)])

    def fnForge(dictReport):
        dictReport.update({"sVerdict": "byte-identical", "iExitCode": 0,
                           "sVerdictRendered": "Reproduced: every file "
                           "byte-identical"})

    _fnForgeTheAssessorReport(monkeypatch, fnForge)
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["bPassed"] is False
    assert dictOutcome["listDivergedHashes"][0].startswith(
        "inconsistent assessment")


@pytest.mark.falsification
def testASelfConsistentForgeryOfAnExitStatusIsCaught(tmp_path, monkeypatch):
    """An exit status the host never saw is refused, however consistent.

    The forged report is re-graded from its own tampered observation, so
    re-derivation agrees with it; only the host's own ledger can tell.

    Kills: dropping the cross-check of every command's exit status.
    """
    sRoot = _fsRepository(tmp_path)
    dictFirst = fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["false"],
        saOutputDataFiles=["first.json"])
    fnWriteText(sRoot, "StepOne/first.json", "{}")
    dictWorkflow = _fdictPublish(sRoot, [dictFirst])
    _DICT_PLAN_HOLDER["dictPlan"] = rerunAssessment.fdictBuildAssessmentPlan(
        dictWorkflow)

    def fnForge(dictReport):
        for dictRecord in dictReport["listInvocations"]:
            if dictRecord["sInvocationId"] == "A01.data.1":
                dictRecord["iExitStatus"] = 0
        _fnRegradeAsConsistent(dictReport)

    _fnForgeTheAssessorReport(monkeypatch, fnForge)
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["bRerunAttempted"] is False
    assert "A01.data.1" in dictOutcome["listDivergedHashes"][0]


@pytest.mark.falsification
def testASelfConsistentForgeryOfAHashIsCaught(tmp_path, monkeypatch):
    """An observed hash the host did not see is refused, however consistent.

    Kills: dropping the cross-check of every graded output's hash.
    """
    sRoot = _fsRepository(tmp_path)
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", "{}")
    dictWorkflow = _fdictPublish(sRoot, [fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"],
        saOutputDataFiles=[S_VALUES])])
    _DICT_PLAN_HOLDER["dictPlan"] = rerunAssessment.fdictBuildAssessmentPlan(
        dictWorkflow)

    def fnForge(dictReport):
        dictOutput = dictReport["listOutputs"][0]
        dictOutput["sObservedSha256"] = dictOutput["sExpectedSha256"]
        _fnRegradeAsConsistent(dictReport)

    _fnForgeTheAssessorReport(monkeypatch, fnForge)
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["bRerunAttempted"] is False
    assert "observed hash differs" in dictOutcome["listDivergedHashes"][0]


# ----------------------------------------------------------------------
# The recorded comparison policy is replayed (I9)
# ----------------------------------------------------------------------


def _fmoduleBuildTestPolicy(tmp_path, iThreshold):
    """Return a test-only policy, ``comparison-vtest``, from v1's own source."""
    with open(comparisonPolicyV1.__file__, encoding="utf-8") as fileSource:
        sSource = fileSource.read()
    sSource = sSource.replace(
        'S_COMPARISON_POLICY = "comparison-v1"',
        'S_COMPARISON_POLICY = "comparison-vtest"').replace(
        "I_IMAGE_LEVEL_THRESHOLD = 2",
        f"I_IMAGE_LEVEL_THRESHOLD = {iThreshold}")
    pathModule = tmp_path / "comparisonPolicyVtest.py"
    pathModule.write_text(sSource)
    specModule = importlib.util.spec_from_file_location(
        "comparisonPolicyVtest", str(pathModule))
    moduleTest = importlib.util.module_from_spec(specModule)
    specModule.loader.exec_module(moduleTest)
    return moduleTest


def _fdictFigureStep(sRoot, sRerunFigure):
    """Publish a figure whose rerun redraws it one level differently."""
    fnWriteBytes(sRoot, "StepOne/figure.png", _fbaPng((100, 100, 100)))
    return fdictBuildStep(
        "Figures", "StepOne", saPlotCommands=[f"cp {sRerunFigure} figure.png"],
        saPlotFiles=["figure.png"])


@pytest.fixture
def fixtureTestPolicyIsCurrent(tmp_path, monkeypatch):
    """Register ``comparison-vtest`` (threshold 0) and make it current."""
    moduleTest = _fmoduleBuildTestPolicy(tmp_path, 0)
    monkeypatch.setitem(
        rerunAssessment.DICT_COMPARISON_POLICIES, "comparison-vtest",
        moduleTest)
    monkeypatch.setattr(
        rerunAssessment, "S_CURRENT_COMPARISON_POLICY", "comparison-vtest")
    sFigure = str(tmp_path / "rerunFigure.png")
    with open(sFigure, "wb") as fileOut:
        fileOut.write(_fbaPng((100, 100, 100), (101, 100, 100)))
    return sFigure


@pytest.mark.falsification
def testAProjectGradesUnderTheComparisonPolicyItRecorded(
    tmp_path, fixtureTestPolicyIsCurrent,
):
    """A newer current policy never re-grades a recorded one.

    The project's pinned assessor records ``comparison-v1``; a
    test-only ``comparison-vtest`` with a threshold of 0 is current. The
    one-level redraw still passes under v1 -- and the attestation it
    would make is not current, because eligibility asks for the current
    policy.

    Kills: grading a recorded project under the current policy.
    """
    sRoot = _fsRepository(tmp_path)
    dictStep = _fdictFigureStep(sRoot, fixtureTestPolicyIsCurrent)
    dictWorkflow = fdictBuildWorkflow([dictStep])
    fnWriteText(sRoot, rerunAssessment.S_RERUN_ASSESSMENT_REPO_PATH,
                rerunAssessment.fsRenderRerunAssessmentScript(
                    dictWorkflow, "comparison-v1"))
    dictWorkflow = _fdictPublish(sRoot, [dictStep])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["sComparisonPolicy"] == "comparison-v1"
    assert dictOutcome["sComparisonPolicySource"] == "recorded"
    assert dictOutcome["sVerdict"] == "comparisons-passed"
    fnWriteAttestation(sRoot, fdictBuildAttestation(
        "passed", fsCurrentManifestDigest(sRoot), "img@sha256:i", 1.0, 1, 1,
        dictAssessmentFields=l3Attestation.fdictAssessmentFieldsFromOutcome(
            dictOutcome)))
    assert fbL3AttestationCurrent(sRoot, {}) is False


@pytest.mark.falsification
def testAProjectWithNoRecordedPolicyGradesUnderTheCurrentOne(
    tmp_path, fixtureTestPolicyIsCurrent,
):
    """No pinned assessor: the current policy, reported as none recorded.

    Kills: grading a project with no record under v1 regardless of the
    current policy.
    """
    sRoot = _fsRepository(tmp_path)
    dictWorkflow = _fdictPublish(
        sRoot, [_fdictFigureStep(sRoot, fixtureTestPolicyIsCurrent)])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["sComparisonPolicy"] == "comparison-vtest"
    assert dictOutcome["sComparisonPolicySource"] == "current; none recorded"
    assert dictOutcome["sVerdict"] == "insufficient-evidence"


@pytest.mark.falsification
def testAPolicyThisVaibifyLacksIsCouldNotRunWithTheUpgradeRemedy(tmp_path):
    """A project recorded under ``comparison-v9`` cannot be graded here.

    Kills: falling back to the current policy for an unknown one.
    """
    sRoot = _fsRepository(tmp_path)
    dictStep = _fdictQuantitativeStep(sRoot, "StepOne", {"x": 1.0}, 1.0)
    dictWorkflow = fdictBuildWorkflow([dictStep])
    sRendered = rerunAssessment.fsRenderRerunAssessmentScript(
        dictWorkflow, "comparison-v1").replace(
        "# vaibify-comparison-policy: comparison-v1",
        "# vaibify-comparison-policy: comparison-v9", 1)
    fnWriteText(sRoot, rerunAssessment.S_RERUN_ASSESSMENT_REPO_PATH,
                sRendered)
    dictWorkflow = _fdictPublish(sRoot, [dictStep])
    dictOutcome = fdictRerunThroughTheLane(sRoot, dictWorkflow)
    assert dictOutcome["bRerunAttempted"] is False
    assert dictOutcome["listDivergedHashes"] == [
        "this project was assessed under comparison-v9, which this "
        "version of vaibify does not include; upgrade vaibify."]
    assert not any("printf" in sCommand
                   for sCommand in dictOutcome["listCommandsRun"])


# ----------------------------------------------------------------------
# Historical attestations stay historical (F11, D7)
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testAHistoricalAttestationNeverSatisfiesTheRebuildRequirement(tmp_path):
    """A previous check's pass is history, whatever else it matches.

    The byte-only verifier's record migrates to historical with no
    assessment fields at all. The record that isolates the rule is a
    current-schema one graded under another comparison policy: it names
    the unchanged manifest AND the execution profile the workflow asks
    for, so being historical is the only thing that can retire it.

    Kills: reading a historical attestation as current.
    """
    sRoot = _fsRepository(tmp_path)
    fnWriteText(sRoot, "MANIFEST.sha256", "a" * 64 + "  a.txt\n")
    sDigest = fsCurrentManifestDigest(sRoot)
    fnWriteText(sRoot, ".vaibify/l3_attestation.json", json.dumps({
        "iSchemaVersion": 6, "sStatus": "passed",
        "sManifestDigestAtAttestation": sDigest,
    }))
    dictRead = l3Attestation.fdictReadAttestation(sRoot)
    assert dictRead["bHistorical"] is True
    assert fbL3AttestationCurrent(sRoot, {}) is False
    fnWriteAttestation(sRoot, fdictBuildAttestation(
        "passed", sDigest, "img@sha256:i", 1.0, 1, 1,
        dictAssessmentFields=dict(
            fdictCurrentAssessmentFields(),
            sComparisonPolicy="comparison-v0")))
    assert fbL3AttestationCurrent(sRoot, {}) is False
    fnWriteAttestation(sRoot, fdictBuildAttestation(
        "passed", sDigest, "img@sha256:i", 1.0, 1, 1,
        dictAssessmentFields=fdictCurrentAssessmentFields()))
    assert fbL3AttestationCurrent(sRoot, {}) is True


# ----------------------------------------------------------------------
# Ordinary runs build today's command strings
# ----------------------------------------------------------------------


@pytest.mark.falsification
def testAnOrdinaryRunCarriesNoEvidenceExportAndNoInvocationIdentity():
    """Without the rerun's evidence context, every command string is today's.

    Kills: naming commands and recording exit statuses whenever a scope
    is passed, with or without the rerun's evidence context.
    """
    import asyncio
    from unittest.mock import MagicMock
    from vaibify.docker.dockerConnection import ExecResult

    listCommands = []

    def fnRecord(sContainerId, sCommand, fnEmitChunk, sWorkdir=None,
                 sUser=None, sLane=None):
        listCommands.append(sCommand)
        return ExecResult(iExitCode=0, sStdout="", sStderr="")

    connectionMock = MagicMock()
    connectionMock.ftRunInContainerStreamedWithChunks.side_effect = fnRecord
    connectionMock.ftResultExecuteCommand.return_value = (0, "")

    async def fnCallback(dictEvent):
        del dictEvent

    for dictVariables in ({}, {pipelineRunner.S_RERUN_EVIDENCE_KEY: {
            "sEvidenceDirectory": "/evidence", "listLedger": []}}):
        asyncio.run(pipelineRunner._ftRunCommandList(
            connectionMock, "containerOrdinary", ["pytest tests"],
            "/work/StepOne", dictVariables, fnCallback,
            sInvocationScope="A01.quantitative"))
    assert "PYTEST_ADDOPTS" not in listCommands[0]
    assert listCommands[0] == pipelineRunner._fsWrapWithTime("pytest tests")
    assert listCommands[1] == (
        "export PYTEST_ADDOPTS='--junitxml=/evidence/A01.quantitative.1.xml'"
        " && " + pipelineRunner._fsWrapWithTime("pytest tests"))
