"""The rendered assessor runs standalone and says what the imported modules say.

``reproduce.sh`` runs the pinned ``.vaibify/rerunAssessment.py`` with the
image's own interpreter under ``-I``, where vaibify cannot be imported.
The dashboard grades with the same modules imported. Parity between the
lanes therefore rests on the rendered text being a faithful
transcription: this test renders it, runs every sub-command it needs as
a separate isolated interpreter in a real repository, and requires the
report it prints to equal, field for field, the report the imported
functions produce from the same scratch and evidence directories.
"""

import json
import os
import subprocess
import sys

import pytest
from PIL import Image

from vaibify.reproducibility import (
    comparisonPolicyV1,
    rerunAssessment,
    rerunProcedure,
)
from tests.rerunAssessmentHarness import (
    fdictBuildStep,
    fdictBuildWorkflow,
    fdictMakeRunDirectories,
    fdictQuantitativeStandard,
    fdictWriteGeneratedTests,
    fnPinManifest,
    fnRunStepsLikeTheRunner,
    fnWriteBytes,
    fnWriteLedger,
    fnWriteText,
    fsWriteJsonCommand,
)


def _fbaPng(tColor):
    """Return a small RGB PNG of one color."""
    import io
    bufferOut = io.BytesIO()
    Image.new("RGB", (32, 32), tColor).save(bufferOut, format="PNG")
    return bufferOut.getvalue()


def _fdictPublishMixedRepository(tmp_path):
    """Publish a step with a changed data file, a passing standard and a figure."""
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot)
    sRerunFigure = str(tmp_path / "rerun.png")
    with open(sRerunFigure, "wb") as fileOut:
        fileOut.write(_fbaPng((10, 20, 31)))
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, "StepOne", "quantitative",
        [fdictQuantitativeStandard("xValue", "values.json", "x", 1.0)])}
    fnWriteText(sRoot, "StepOne/values.json",
                json.dumps({"note": "a", "x": 1.0}, sort_keys=True))
    fnWriteBytes(sRoot, "StepOne/figure.png", _fbaPng((10, 20, 30)))
    dictStep = fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=[fsWriteJsonCommand(
            "values.json", {"note": "b", "x": 1.0})],
        saPlotCommands=[f"cp {sRerunFigure} figure.png"],
        saOutputDataFiles=["values.json"], saPlotFiles=["figure.png"],
        dictTests=dictTests)
    dictWorkflow = fdictBuildWorkflow([dictStep])
    fnPinManifest(sRoot, dictWorkflow)
    return sRoot, dictWorkflow


def _fcompletedRunRendered(sScript, listArguments):
    """Run the rendered file in an isolated interpreter."""
    return subprocess.run(
        [sys.executable, "-I", sScript] + listArguments,
        capture_output=True, text=True, timeout=300)


@pytest.mark.falsification
def testTheRenderedAssessorReportsWhatTheImportedModulesReport(tmp_path):
    """Run prepare, clear and assess standalone; compare with the imports.

    Kills: the rendered file losing the policy's entry point, so a
    standalone ``assess`` can grade nothing.
    """
    sRoot, dictWorkflow = _fdictPublishMixedRepository(tmp_path)
    dictDirectories = fdictMakeRunDirectories(str(tmp_path / "run"))
    sScratch, sEvidence = (dictDirectories["sScratch"],
                           dictDirectories["sEvidence"])
    sScript = os.path.join(sScratch, "rerunAssessment.py")
    with open(sScript, "w", encoding="utf-8") as fileOut:
        fileOut.write(rerunAssessment.fsRenderRerunAssessmentScript(
            dictWorkflow, rerunAssessment.S_CURRENT_COMPARISON_POLICY))
    completedPrepare = _fcompletedRunRendered(
        sScript, ["prepare", "--repo", sRoot, "--scratch", sScratch])
    assert completedPrepare.returncode == 0, completedPrepare.stderr
    completedClear = _fcompletedRunRendered(
        sScript, ["clear", "--repo", sRoot])
    assert completedClear.returncode == 0, completedClear.stderr
    assert not os.path.exists(os.path.join(sRoot, "StepOne/values.json"))
    dictPlan = rerunAssessment.fdictBuildAssessmentPlan(dictWorkflow)
    listLedger = []
    fnRunStepsLikeTheRunner(sRoot, dictWorkflow, dictPlan, sEvidence,
                            listLedger)
    fnWriteLedger(sEvidence, listLedger)
    completedAssess = _fcompletedRunRendered(sScript, [
        "assess", "--repo", sRoot, "--scratch", sScratch,
        "--evidence", sEvidence, "--machine"])
    listLines = completedAssess.stdout.splitlines()
    assert listLines[-1].startswith(rerunProcedure.S_ASSESSMENT_RESULT_PREFIX)
    dictStandalone = json.loads(
        listLines[-1][len(rerunProcedure.S_ASSESSMENT_RESULT_PREFIX):])
    dictObservations = rerunProcedure.fdictGatherProcedureObservations(
        sRoot, sScratch, sEvidence, dictPlan)
    dictObservations["sComparisonPolicySource"] = "recorded"
    dictImported = json.loads(json.dumps(
        comparisonPolicyV1.fdictAssessRerunEvidence(
            dictPlan, dictObservations)))
    assert dictStandalone == dictImported
    assert dictStandalone["sVerdict"] == "comparisons-passed"
    assert completedAssess.returncode == 0
    assert listLines[0] == dictStandalone["sVerdictRendered"]
    assert listLines[1] == dictStandalone["sTallyRendered"]
    assert rerunAssessment.fdictRederiveAssessment(
        dictStandalone, dictPlan)["bConsistent"] is True


def testAnUnexpectedErrorInTheRenderedFileIsCouldNotRun(tmp_path):
    """Every exit path is one of D6's codes; a crash is "Could not run"."""
    sRoot, dictWorkflow = _fdictPublishMixedRepository(tmp_path)
    sScript = str(tmp_path / "rerunAssessment.py")
    with open(sScript, "w", encoding="utf-8") as fileOut:
        fileOut.write(rerunAssessment.fsRenderRerunAssessmentScript(
            dictWorkflow, rerunAssessment.S_CURRENT_COMPARISON_POLICY))
    completedMissingScratch = _fcompletedRunRendered(sScript, [
        "prepare", "--repo", sRoot, "--scratch",
        str(tmp_path / "absent" / "scratch")])
    assert completedMissingScratch.returncode == 4
    assert "Could not run" in completedMissingScratch.stderr
    completedUsage = _fcompletedRunRendered(sScript, ["no-such-command"])
    assert completedUsage.returncode == 4
