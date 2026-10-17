"""The dashboard's rerun and a reader's ``reproduce.sh`` agree on one tree.

Two checks of the one promise Phase 4 makes (``design/
cpuClassReproducibility.md``, "the same verdict and exit code as the
dashboard on the same tree"):

* at the source, the assessor the shadow lane writes and the one the
  generator writes beside ``reproduce.sh`` are the same text, so the two
  lanes cannot grade by different rules;
* in behavior, one published tree is copied, the lane's core runs on one
  copy (``tests/assessedRerunLaneHarness.py``, the real runner) and the
  script's container half on the other (``tests/reproduceScriptHarness.py``,
  real bash), and both reach the same verdict, the same exit code and
  the same exit status for every command they ran.

Both lanes run here on this machine's interpreter; the same comparison
inside real containers is ``tests/testReproduceScriptParityLive.py``.
The expected verdict of each case comes from the precedence table, so
two lanes agreeing on a wrong answer still fails.
"""

import io
import json
import os
import shutil

import pytest
from PIL import Image

from tests.assessedRerunLaneHarness import fdictRerunThroughTheLane
from tests.reproduceScriptHarness import (
    fdictRunContainerHalf,
    fnPublishForTheScript,
)
from tests.rerunAssessmentHarness import (
    fdictBuildStep,
    fdictBuildWorkflow,
    fdictQuantitativeStandard,
    fdictWriteGeneratedTests,
    fnWriteBytes,
    fnWriteText,
    fsWriteJsonCommand,
)
from vaibify.reproducibility import assessmentLane
from vaibify.reproducibility.rerunAssessment import (
    S_RERUN_ASSESSMENT_REPO_PATH,
)


S_VALUES = "values.json"
_S_REDRAW_ONE_PIXEL_UP = (
    "python3 -c \"from PIL import Image; "
    "im = Image.new('RGB', (16, 16), (100, 100, 100)); "
    "im.putpixel((0, 0), (101, 100, 100)); im.save('figure.png')\"")


def _fbaUniformFigure():
    """Return the published figure: the redrawn one minus its one pixel."""
    bufferOut = io.BytesIO()
    Image.new("RGB", (16, 16), (100, 100, 100)).save(bufferOut, format="PNG")
    return bufferOut.getvalue()


def _fdictQuantitativeStep(sRoot, dictValues, fExpected, **dictExtra):
    """Return a published step writing ``dictValues``, checked by one standard."""
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, "StepOne", "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", fExpected)])}
    fnWriteText(sRoot, f"StepOne/{S_VALUES}",
                json.dumps(dictValues, sort_keys=True))
    return fdictBuildStep(
        "Step one", "StepOne",
        saDataCommands=[fsWriteJsonCommand(S_VALUES, dictValues)],
        saOutputDataFiles=[S_VALUES], dictTests=dictTests, **dictExtra)


def _flistByteIdentical(sRoot):
    return [_fdictQuantitativeStep(sRoot, {"x": 1.0}, 1.0)]


def _flistComparisonsPassed(sRoot):
    fnWriteBytes(sRoot, "StepOne/figure.png", _fbaUniformFigure())
    return [_fdictQuantitativeStep(
        sRoot, {"x": 1.0}, 1.0, saPlotCommands=[_S_REDRAW_ONE_PIXEL_UP],
        saPlotFiles=["figure.png"])]


def _flistFailedCheck(sRoot):
    return [_fdictQuantitativeStep(sRoot, {"x": 1.0}, 2.0)]


def _flistSkippedCheck(sRoot):
    fnWriteText(sRoot, "StepOne/conftest.py",
                "import pytest\n\n\ndef pytest_runtest_setup(item):\n"
                "    pytest.skip('skipped on purpose')\n")
    return [_fdictQuantitativeStep(sRoot, {"x": 1.0}, 1.0)]


def _flistWritesNothing(sRoot):
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", "{}")
    return [fdictBuildStep("Step one", "StepOne", saDataCommands=["true"],
                           saOutputDataFiles=[S_VALUES])]


_LIST_CASES = [
    pytest.param(_flistByteIdentical, "byte-identical", 0,
                 id="byte-identical"),
    pytest.param(_flistComparisonsPassed, "comparisons-passed", 0,
                 id="comparisons-passed"),
    pytest.param(_flistFailedCheck, "not-reproduced", 1, id="failed-check"),
    pytest.param(_flistSkippedCheck, "insufficient-evidence", 5,
                 id="insufficient-evidence"),
    pytest.param(_flistWritesNothing, "not-reproduced", 1,
                 id="writes-nothing"),
]


def _fsPublishTheTree(tmp_path, flistBuildSteps):
    """Publish one tree with its script and assessor; return its root."""
    sRoot = str(tmp_path / "published")
    os.makedirs(sRoot)
    dictWorkflow = fdictBuildWorkflow(flistBuildSteps(sRoot))
    fnWriteText(sRoot, ".vaibify/workflows/project.json",
                json.dumps(dictWorkflow))
    fnPublishForTheScript(sRoot, dictWorkflow)
    return sRoot, dictWorkflow


def _fdictStatuses(listInvocations):
    """Return ``{sInvocationId: iExitStatus}`` for the commands that ran."""
    return {dictRecord["sInvocationId"]: dictRecord["iExitStatus"]
            for dictRecord in listInvocations
            if dictRecord.get("iExitStatus") is not None}


@pytest.mark.parametrize("flistBuildSteps, sVerdict, iExitCode", _LIST_CASES)
def testBothLanesGradeOneTreeAlike(
    tmp_path, flistBuildSteps, sVerdict, iExitCode,
):
    """Same verdict, same exit code, same status for every command run."""
    sPublished, dictWorkflow = _fsPublishTheTree(tmp_path, flistBuildSteps)
    sLaneRoot = str(tmp_path / "lane")
    sScriptRoot = str(tmp_path / "script")
    shutil.copytree(sPublished, sLaneRoot, symlinks=True)
    shutil.copytree(sPublished, sScriptRoot, symlinks=True)
    sRun = str(tmp_path / "run")
    os.makedirs(sRun)
    dictLane = fdictRerunThroughTheLane(sLaneRoot, dictWorkflow)
    dictScript = fdictRunContainerHalf(sScriptRoot, sRun)
    dictLaneReport = dictLane["dictAssessmentReport"]
    dictScriptReport = dictScript["dictReport"]
    assert dictScriptReport is not None, dictScript["sStderr"]
    assert dictLane["sVerdict"] == sVerdict
    assert dictScriptReport["sVerdict"] == sVerdict
    assert dictLane["iAssessmentExitCode"] == iExitCode
    assert dictScript["iExitCode"] == iExitCode, dictScript["sStderr"]
    assert _fdictStatuses(dictLaneReport["listInvocations"]) == (
        _fdictStatuses(dictScriptReport["listInvocations"]))
    assert dictLaneReport["dictTally"] == dictScriptReport["dictTally"]


@pytest.mark.falsification
def testTheLaneAndTheScriptRunTheSameAssessor(tmp_path, monkeypatch):
    """The shadow's assessor and the one beside ``reproduce.sh`` are one text.

    Kills: the generator writing the assessor in any form other than the
    shadow lane's own render of it.
    """
    sPublished, dictWorkflow = _fsPublishTheTree(tmp_path, _flistByteIdentical)
    listWritten = []
    fnOriginal = assessmentLane.fnWriteLaneFile

    def fnRecordThenWrite(filesRepo, sAbsolutePath, sText):
        listWritten.append((sAbsolutePath, sText))
        fnOriginal(filesRepo, sAbsolutePath, sText)

    monkeypatch.setattr(assessmentLane, "fnWriteLaneFile", fnRecordThenWrite)
    dictLane = fdictRerunThroughTheLane(sPublished, dictWorkflow)
    assert dictLane["sVerdict"] == "byte-identical"
    listAssessors = [sText for sPath, sText in listWritten
                     if sPath.endswith("rerunAssessment.py")]
    assert len(listAssessors) == 1
    with open(os.path.join(sPublished, S_RERUN_ASSESSMENT_REPO_PATH),
              encoding="utf-8") as fileIn:
        assert fileIn.read() == listAssessors[0]
