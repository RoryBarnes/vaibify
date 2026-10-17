"""Every row of the design's per-output table, and the roll-up, on real trees.

The oracle is the precedence table in ``design/cpuClassReproducibility.md``
("The outcome model and precedence"), never the code under test. Each
test publishes a repository (real files, vaibify's generated tests, the
real manifest writer), reruns it the way the lanes do -- the procedure
clears what the rerun regenerates, the steps run in the runner's order,
a real pytest writes a real JUnit report -- and grades it with the
policy.

What each row guards against is the same false pass from a different
side: byte equality standing in for a failing or unexecuted check
(F10), a structural check standing in for an agreement comparison, a
section that failed or a file never written being graded as if it ran,
or a changed published input being graded at all.
"""

import json
import os

import pytest

from tests.rerunAssessmentHarness import (
    S_PYTEST,
    fdictBuildStep,
    fdictBuildWorkflow,
    fdictFileStandard,
    fdictFindOutput,
    fdictQuantitativeStandard,
    fdictRunRerunInPython,
    fdictWriteGeneratedTests,
    fnPinManifest,
    fnWriteText,
    fsWriteJsonCommand,
)


S_VALUES = "values.json"


def _fdictPublishOneDataStep(
    sRoot, dictPublished, dictRerun, listQuantitative=(),
    bStructural=False, sDirectory="StepOne", saPlotCommands=(),
    saPlotFiles=(), sExtraDataCommand="",
):
    """Publish one step whose data command writes ``dictRerun``.

    ``dictPublished`` is what the manifest pins, so equal dictionaries
    are a byte-identical rerun and different ones a rerun whose bytes
    changed.
    """
    dictTests = {}
    if listQuantitative:
        dictTests["dictQuantitative"] = fdictWriteGeneratedTests(
            sRoot, sDirectory, "quantitative", list(listQuantitative))
    if bStructural:
        dictTests["dictIntegrity"] = fdictWriteGeneratedTests(
            sRoot, sDirectory, "integrity", [fdictFileStandard(S_VALUES)])
        dictQualitative = fdictWriteGeneratedTests(
            sRoot, sDirectory, "qualitative",
            [dict(fdictFileStandard(S_VALUES),
                  listExpectedJsonKeys=sorted(dictRerun))])
        dictTests["dictQualitative"] = dictQualitative
    listData = [fsWriteJsonCommand(S_VALUES, dictRerun)]
    if sExtraDataCommand:
        listData.append(sExtraDataCommand)
    dictStep = fdictBuildStep(
        "Step one", sDirectory, saDataCommands=listData,
        saOutputDataFiles=[S_VALUES], saPlotCommands=list(saPlotCommands),
        saPlotFiles=list(saPlotFiles), dictTests=dictTests)
    fnWriteText(sRoot, f"{sDirectory}/{S_VALUES}",
                json.dumps(dictPublished, sort_keys=True))
    return dictStep


def _fdictPublishAndRerun(tmp_path, listSteps, fnBeforeRerun=None):
    """Pin the manifest, optionally tamper, rerun, and return the result."""
    sRoot = str(tmp_path / "repo")
    dictWorkflow = fdictBuildWorkflow(listSteps)
    fnPinManifest(sRoot, dictWorkflow)
    if fnBeforeRerun is not None:
        fnBeforeRerun(sRoot)
    return fdictRunRerunInPython(sRoot, str(tmp_path), dictWorkflow)


def _fsRoot(tmp_path):
    """Return the repository directory for this test, created."""
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot, exist_ok=True)
    return sRoot


@pytest.mark.falsification
def testBytesIdenticalWithAFailingCheckIsFailed(tmp_path):
    """F10: a failing expected check is never overridden by byte equality.

    Kills: dropping the rule that an executed failure fails the output.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0},
        listQuantitative=[fdictQuantitativeStandard("xValue", S_VALUES, "x",
                                                    2.0)])
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    assert dictOutput["sObservedSha256"] == dictOutput["sExpectedSha256"]
    assert dictOutput["sOutcome"] == "failed"
    assert dictResult["dictReport"]["sVerdict"] == "not-reproduced"
    assert dictResult["iAssess"] == 1


@pytest.mark.falsification
def testBytesIdenticalWithASkippedCheckIsInsufficient(tmp_path):
    """F10: a check that did not execute is never overridden by byte equality.

    Kills: grading an output byte-identical while one of its checks did
    not assert.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0},
        listQuantitative=[fdictQuantitativeStandard("xValue", S_VALUES, "x",
                                                    1.0)])
    fnWriteText(sRoot, "StepOne/tests/conftest.py",
                "import pytest\n\n\ndef pytest_runtest_setup(item):\n"
                "    pytest.skip('skipped on purpose')\n")
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    assert dictOutput["listAgreementChecks"][0]["sOutcome"] == (
        "did not assert")
    assert dictOutput["sOutcome"] == "insufficient evidence"
    assert dictResult["iAssess"] == 5


@pytest.mark.falsification
def testChangedBytesWithOnlyStructuralChecksIsInsufficient(tmp_path):
    """Integrity and qualitative checks never establish agreement.

    The review's counterexample: every structural check passes and the
    values moved, so nothing vouches for the new bytes.

    Kills: counting a required check as an agreement comparison.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.5}, bStructural=True)
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    assert {dictCheck["sOutcome"] for dictCheck in
            dictOutput["listRequiredChecks"]} == {"passed"}
    assert len(dictOutput["listRequiredChecks"]) == 2
    assert dictOutput["sOutcome"] == "insufficient evidence"
    assert dictResult["dictReport"]["sVerdict"] == "insufficient-evidence"


@pytest.mark.falsification
def testChangedBytesWithNoComparisonAtAllIsInsufficient(tmp_path):
    """Zero comparisons and changed bytes is never a pass.

    Kills: treating an output with no agreement comparison as agreeing.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(sRoot, {"x": 1.0}, {"x": 1.5})
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    assert dictOutput["sOutcome"] == "insufficient evidence"
    assert dictResult["iAssess"] == 5


@pytest.mark.falsification
def testChangedBytesWithPassingComparisonsIsComparisonsPassed(tmp_path):
    """A quantitative standard that passes vouches for changed bytes.

    Kills: grading every byte difference as a failure.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0, "note": "a"}, {"x": 1.0, "note": "b"},
        listQuantitative=[fdictQuantitativeStandard("xValue", S_VALUES, "x",
                                                    1.0)])
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictReport = dictResult["dictReport"]
    assert fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "sOutcome"] == "comparisons passed"
    assert dictReport["sVerdict"] == "comparisons-passed"
    assert dictReport["sVerdictRendered"] == (
        "Reproduced: published comparisons passed; 1 of 1 files differ in "
        "their bytes")
    assert dictResult["iAssess"] == 0


@pytest.mark.falsification
def testAFileTheRerunNeverWroteIsFailedMissing(tmp_path):
    """A data command that exits 0 and writes nothing is Failed (missing).

    Kills: grading an absent output as a byte difference rather than a
    failure.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"],
        saOutputDataFiles=[S_VALUES])
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", "{}")
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    assert dictOutput["sObservedSha256"] is None
    assert dictOutput["sOutcome"] == "failed"
    assert "missing" in dictOutput["sReason"]
    assert f"StepOne/{S_VALUES}" in dictResult["dictReport"][
        "sVerdictRendered"]


@pytest.mark.falsification
def testAFailedDataCommandFailsTheStepsDataAndPlotOutputs(tmp_path):
    """A data command that writes its file and then exits 1 is Failed.

    Its plot section never ran, so the plot output is Failed too, and
    both name the data command.

    Kills: grading a data output by its bytes when its command failed.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0}, sExtraDataCommand="exit 1",
        saPlotCommands=["printf 'drawn' > figure.svg"],
        saPlotFiles=["figure.svg"])
    fnWriteText(sRoot, "StepOne/figure.svg", "drawn")
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictReport = dictResult["dictReport"]
    dictData = fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")
    dictPlot = fdictFindOutput(dictReport, "StepOne/figure.svg")
    assert dictData["sObservedSha256"] == dictData["sExpectedSha256"]
    assert dictData["sOutcome"] == "failed"
    assert "A01.data.2 exited 1" in dictData["sReason"]
    assert dictPlot["sOutcome"] == "failed"
    assert "A01.data.2" in dictPlot["sReason"]
    assert dictResult["iAssess"] == 1


@pytest.mark.falsification
def testAFailedPlotCommandFailsOnlyThePlotOutputs(tmp_path):
    """A plot failure leaves the data outputs graded normally.

    Kills: letting a plot section's state decide the data outputs.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0},
        saPlotCommands=["printf 'drawn' > figure.svg && exit 1"],
        saPlotFiles=["figure.svg"])
    fnWriteText(sRoot, "StepOne/figure.svg", "drawn")
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictReport = dictResult["dictReport"]
    assert fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "sOutcome"] == "byte-identical"
    dictPlot = fdictFindOutput(dictReport, "StepOne/figure.svg")
    assert dictPlot["sOutcome"] == "failed"
    assert "A01.plot.1 exited 1" in dictPlot["sReason"]


@pytest.mark.falsification
def testFailuresAndMissingEvidenceTogetherAreFailedWithBothTallied(tmp_path):
    """A failure outranks missing evidence, and the tally shows both.

    Kills: letting insufficient evidence outrank a failure in the roll-up.
    """
    sRoot = _fsRoot(tmp_path)
    dictFirst = fdictBuildStep(
        "Step one", "StepOne", saDataCommands=["true"],
        saOutputDataFiles=[S_VALUES])
    fnWriteText(sRoot, f"StepOne/{S_VALUES}", "{}")
    dictSecond = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.5}, sDirectory="StepTwo")
    dictResult = _fdictPublishAndRerun(tmp_path, [dictFirst, dictSecond])
    dictReport = dictResult["dictReport"]
    assert dictReport["sVerdict"] == "not-reproduced"
    assert dictReport["dictTally"]["iFailed"] == 1
    assert dictReport["dictTally"]["iInsufficientEvidence"] == 1
    assert "1 failed, 1 not enough evidence" in dictReport["sTallyRendered"]


@pytest.mark.falsification
def testADeletedPinnedTestIsPublishedInputsChanged(tmp_path):
    """Integrity is exact: a deleted test file is a changed published input.

    Kills: ignoring a pinned input that is missing.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0},
        listQuantitative=[fdictQuantitativeStandard("xValue", S_VALUES, "x",
                                                    1.0)])
    sTest = "StepOne/tests/test_quantitative_stepOne.py"
    dictResult = _fdictPublishAndRerun(
        tmp_path, [dictStep],
        fnBeforeRerun=lambda sRepo: os.remove(os.path.join(sRepo, sTest)))
    dictReport = dictResult["dictReport"]
    assert dictResult["iPrepare"] == 3
    assert dictReport["sVerdict"] == "inputs-changed"
    assert dictReport["sVerdictRendered"] == (
        f"Published inputs changed: {sTest} (missing)")
    assert dictResult["iAssess"] == 3


@pytest.mark.falsification
def testAToleranceEditedDuringTheRunIsPublishedInputsChanged(tmp_path):
    """An input that matched before the steps and moved during them changed.

    Kills: judging integrity only before the run.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0},
        listQuantitative=[fdictQuantitativeStandard("xValue", S_VALUES, "x",
                                                    1.0)])
    sStandards = "StepOne/tests/quantitative_standards_stepOne.json"
    sRootPath = str(tmp_path / "repo")
    dictWorkflow = fdictBuildWorkflow([dictStep])
    fnPinManifest(sRootPath, dictWorkflow)

    def fnLoosenTheTolerance():
        with open(os.path.join(sRootPath, sStandards)) as fileIn:
            dictStandards = json.load(fileIn)
        dictStandards["listStandards"][0]["fRtol"] = 0.5
        fnWriteText(sRootPath, sStandards, json.dumps(dictStandards))

    dictResult = fdictRunRerunInPython(
        sRootPath, str(tmp_path), dictWorkflow,
        fnBetweenSteps=fnLoosenTheTolerance)
    assert dictResult["iPrepare"] == 0
    assert dictResult["dictReport"]["dictIntegrity"]["bChangedDuringRun"]
    assert dictResult["dictReport"]["sVerdict"] == "inputs-changed"


@pytest.mark.falsification
def testAStandardWhoseNodeIsAbsentIsInsufficient(tmp_path):
    """Each comparison is identified; other passing nodes do not cover it.

    Kills: reading a standard whose node never appeared as passed.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0, "y": 2.0}, {"x": 1.0, "y": 2.5},
        listQuantitative=[
            fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0),
            fdictQuantitativeStandard("yValue", S_VALUES, "y", 2.0)])
    dictStep["dictTests"]["dictQuantitative"]["saCommands"] = [
        f"{S_PYTEST} tests/test_quantitative_stepOne.py -k xValue"]
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    dictOutcomes = {dictCheck["sTestcaseName"]: dictCheck["sOutcome"]
                    for dictCheck in dictOutput["listAgreementChecks"]}
    assert dictOutcomes == {
        "test_quantitative_benchmark[xValue]": "passed",
        "test_quantitative_benchmark[yValue]": "not executed"}
    assert dictOutput["sOutcome"] == "insufficient evidence"


@pytest.mark.falsification
def testARepeatedStandardNameIsInsufficient(tmp_path):
    """A standards file that repeats a name cannot be read without guessing.

    Kills: ignoring a duplicate standard name.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0},
        listQuantitative=[
            fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0),
            fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0)])
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictOutput = fdictFindOutput(dictResult["dictReport"],
                                 f"StepOne/{S_VALUES}")
    assert {dictCheck["sOutcome"] for dictCheck in
            dictOutput["listAgreementChecks"]} == {"ambiguous"}
    assert "more than once" in dictOutput["listAgreementChecks"][0]["sReason"]
    assert dictOutput["sOutcome"] == "insufficient evidence"


def testAByteIdenticalRerunWithPassingChecksIsReproduced(tmp_path):
    """The plain case: every check passes and every byte matches."""
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictPublishOneDataStep(
        sRoot, {"x": 1.0}, {"x": 1.0}, bStructural=True,
        listQuantitative=[fdictQuantitativeStandard("xValue", S_VALUES, "x",
                                                    1.0)])
    dictResult = _fdictPublishAndRerun(tmp_path, [dictStep])
    dictReport = dictResult["dictReport"]
    assert dictReport["sVerdictRendered"] == (
        "Reproduced: every file byte-identical")
    assert dictReport["dictTally"]["iByteIdentical"] == 1
    assert dictResult["iAssess"] == 0
