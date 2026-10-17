"""Evidence is bound to its step and command, and every command is counted.

The rules are I8 of ``design/cpuClassReproducibilityImplementation.md``:
an expected check is looked for only in the JUnit reports of its own
step's commands of its own category; repeated results combine worst
first; a command expected to report that wrote no report leaves its
checks unasserted; a command expected to report nothing is judged by its
exit status; a non-zero exit with no failure in its report asserted
nothing; a planned command with no ledger line never ran; and ambiguity
is never resolved by guessing.

The reports are written by a real pytest, so the classnames are the
ones pytest really produces. Two steps' identically named test files
with no root configuration report the SAME classname (design fact 22),
which is the case that makes step binding necessary rather than tidy.
"""

import json
import os

import pytest

from vaibify.gui import templateManager
from vaibify.reproducibility import comparisonPolicyV1, rerunProcedure
from tests.rerunAssessmentHarness import (
    S_PYTEST,
    fdictBuildStep,
    fdictBuildWorkflow,
    fdictFindOutput,
    fdictQuantitativeStandard,
    fdictRunRerunInPython,
    fdictWriteGeneratedTests,
    fnPinManifest,
    fnWriteLedger,
    fnWriteText,
    fsWriteJsonCommand,
)


S_VALUES = "values.json"


def _fdictLegacyNamedQuantitative(sRoot, sDirectory, listStandards,
                                  sCommandSuffix=""):
    """Write the generated quantitative test under its LEGACY unsuffixed name."""
    fnWriteText(sRoot, f"{sDirectory}/tests/test_quantitative.py",
                templateManager.fsBuildQuantitativeTestCode())
    fnWriteText(sRoot, f"{sDirectory}/tests/quantitative_standards.json",
                json.dumps({"listStandards": listStandards}))
    return {
        "saCommands": [f"{S_PYTEST} tests/test_quantitative.py"
                       + sCommandSuffix],
        "sFilePath": "tests/test_quantitative.py",
        "sStandardsPath": f"{sDirectory}/tests/quantitative_standards.json",
    }


def _fdictDataStep(sRoot, sDirectory, dictValues, dictTests,
                   saTestCommands=()):
    """Publish one step writing ``dictValues``, byte-identical on rerun."""
    fnWriteText(sRoot, f"{sDirectory}/{S_VALUES}",
                json.dumps(dictValues, sort_keys=True))
    return fdictBuildStep(
        f"Step {sDirectory}", sDirectory,
        saDataCommands=[fsWriteJsonCommand(S_VALUES, dictValues)],
        saOutputDataFiles=[S_VALUES], dictTests=dictTests,
        saTestCommands=list(saTestCommands))


def _fdictRun(tmp_path, listSteps):
    """Pin and rerun; return the harness result."""
    sRoot = str(tmp_path / "repo")
    dictWorkflow = fdictBuildWorkflow(listSteps)
    fnPinManifest(sRoot, dictWorkflow)
    return fdictRunRerunInPython(sRoot, str(tmp_path), dictWorkflow)


def _fdictReassess(tmp_path, dictResult):
    """Grade the same scratch and evidence again, after a test edits them."""
    dictObservations = rerunProcedure.fdictGatherProcedureObservations(
        str(tmp_path / "repo"), dictResult["sScratch"],
        dictResult["sEvidence"], dictResult["dictPlan"])
    dictObservations["sComparisonPolicySource"] = "recorded"
    return comparisonPolicyV1.fdictAssessRerunEvidence(
        dictResult["dictPlan"], dictObservations)


def _fsRoot(tmp_path):
    """Return the repository directory for this test, created."""
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot, exist_ok=True)
    return sRoot


@pytest.mark.falsification
def testOneStepsPassingReportNeverSatisfiesAnothersAbsentCheck(tmp_path):
    """Identically named test files in two steps report the same classname.

    Step one's command runs and passes its check. Step two's command
    selects nothing, so its check never appears in step two's report --
    and step one's report, which names the same classname and testcase,
    must not stand in for it.

    Kills: looking an expected check up in every step's reports.
    """
    sRoot = _fsRoot(tmp_path)
    listStandards = [fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0)]
    dictFirst = _fdictDataStep(sRoot, "StepOne", {"x": 1.0}, {
        "dictQuantitative": _fdictLegacyNamedQuantitative(
            sRoot, "StepOne", listStandards)})
    dictSecond = _fdictDataStep(sRoot, "StepTwo", {"x": 1.0}, {
        "dictQuantitative": _fdictLegacyNamedQuantitative(
            sRoot, "StepTwo", listStandards, " -k selectsNothing")})
    dictReport = _fdictRun(tmp_path, [dictFirst, dictSecond])["dictReport"]
    dictCases = {dictRecord["sInvocationId"]: dictRecord["listTestcases"]
                 for dictRecord in dictReport["listInvocations"]}
    assert dictCases["A01.quantitative.1"][0]["sClassname"] == (
        "tests.test_quantitative")
    assert dictCases["A02.quantitative.1"] == []
    dictFirstCheck = fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "listAgreementChecks"][0]
    dictSecondCheck = fdictFindOutput(dictReport, f"StepTwo/{S_VALUES}")[
        "listAgreementChecks"][0]
    assert dictFirstCheck["sOutcome"] == "passed"
    assert dictSecondCheck["sOutcome"] == "not executed"
    assert "A02.quantitative.1" in dictSecondCheck["sReason"]
    assert dictReport["sVerdict"] == "insufficient-evidence"


@pytest.mark.falsification
def testAFailureIsNotErasedByALaterPass(tmp_path):
    """The same check failing in one report and passing in a later one fails.

    Kills: letting the last report a check appears in decide it.
    """
    sRoot = _fsRoot(tmp_path)
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, "StepOne", "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0)])}
    sTest = "tests/test_quantitative_stepOne.py"
    dictTests["dictQuantitative"]["saCommands"] = [
        f"mv {S_VALUES} kept.json && "
        + fsWriteJsonCommand(S_VALUES, {"x": 9.0})
        + f" && ({S_PYTEST} {sTest}; true) && mv kept.json {S_VALUES}",
        f"{S_PYTEST} {sTest}",
    ]
    dictStep = _fdictDataStep(sRoot, "StepOne", {"x": 1.0}, dictTests)
    dictReport = _fdictRun(tmp_path, [dictStep])["dictReport"]
    dictCheck = fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "listAgreementChecks"][0]
    assert dictCheck["sOutcome"] == "failed"
    assert "A01.quantitative.1" in dictCheck["sReason"]
    assert dictReport["sVerdict"] == "not-reproduced"


@pytest.mark.falsification
def testACommandThatWritesNoReportLeavesItsChecksUnasserted(tmp_path):
    """A generated category's command naming its own --junitxml reports nothing here.

    Kills: treating a structured command with no report as passing.
    """
    sRoot = _fsRoot(tmp_path)
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, "StepOne", "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0)])}
    dictTests["dictQuantitative"]["saCommands"] = [
        f"{S_PYTEST} tests/test_quantitative_stepOne.py "
        "--junitxml=../elsewhere.xml"]
    dictStep = _fdictDataStep(sRoot, "StepOne", {"x": 1.0}, dictTests)
    dictReport = _fdictRun(tmp_path, [dictStep])["dictReport"]
    dictCheck = fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "listAgreementChecks"][0]
    assert dictCheck["sOutcome"] == "did not assert"
    assert "--junitxml" in dictCheck["sReason"]
    assert dictReport["sVerdict"] == "insufficient-evidence"


@pytest.mark.falsification
def testALegacyCommandWithNoReportIsJudgedByItsExitStatus(tmp_path):
    """A custom test command expects no report; its exit status is the check.

    Kills: passing a legacy command whatever its exit status.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictDataStep(sRoot, "StepOne", {"x": 1.0}, {},
                              saTestCommands=["exit 1", "exit 0"])
    dictReport = _fdictRun(tmp_path, [dictStep])["dictReport"]
    dictChecks = {dictCheck["sCheck"]: dictCheck["sOutcome"]
                  for dictCheck in dictReport["listStepLevelChecks"]}
    assert dictChecks["A01.legacy.1"] == "failed"
    assert dictReport["sVerdict"] == "not-reproduced"
    pathSecond = tmp_path / "second"
    os.makedirs(pathSecond / "repo")
    dictPassing = _fdictDataStep(str(pathSecond / "repo"), "StepTwo",
                                 {"x": 1.0}, {}, saTestCommands=["exit 0"])
    dictSecond = _fdictRun(pathSecond, [dictPassing])["dictReport"]
    assert {dictCheck["sCheck"]: dictCheck["sOutcome"]
            for dictCheck in dictSecond["listStepLevelChecks"]} == {
        "A01.legacy.1": "passed"}


@pytest.mark.falsification
def testANonZeroExitWithNoFailureAssertedNothing(tmp_path):
    """Pytest exits 5 when it collects nothing; that is not a pass.

    Kills: ignoring a test command's non-zero exit when its report shows
    no failure.
    """
    sRoot = _fsRoot(tmp_path)
    dictStep = _fdictDataStep(
        sRoot, "StepOne", {"x": 1.0}, {},
        saTestCommands=[f"{S_PYTEST} tests -k selectsNothing"])
    os.makedirs(os.path.join(sRoot, "StepOne", "tests"), exist_ok=True)
    fnWriteText(sRoot, "StepOne/tests/test_custom.py",
                "def test_present():\n    assert True\n")
    dictReport = _fdictRun(tmp_path, [dictStep])["dictReport"]
    dictChecks = {dictCheck["sCheck"]: dictCheck
                  for dictCheck in dictReport["listStepLevelChecks"]}
    assert dictChecks["A01.legacy.1"]["sOutcome"] == "did not assert"
    assert "exited 5" in dictChecks["A01.legacy.1"]["sReason"]
    assert dictReport["sVerdict"] == "insufficient-evidence"


@pytest.mark.falsification
def testAPlannedCommandWithNoLedgerLineNeverRan(tmp_path):
    """A command the ledger does not account for left its checks unexecuted.

    Kills: grading a never-run invocation as one that reported nothing.
    """
    sRoot = _fsRoot(tmp_path)
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, "StepOne", "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0)])}
    dictStep = _fdictDataStep(sRoot, "StepOne", {"x": 1.0}, dictTests)
    dictResult = _fdictRun(tmp_path, [dictStep])
    fnWriteLedger(dictResult["sEvidence"], [
        tEntry for tEntry in dictResult["listLedger"]
        if tEntry[0] != "A01.quantitative.1"])
    os.remove(os.path.join(dictResult["sEvidence"], "A01.quantitative.1.xml"))
    dictReport = _fdictReassess(tmp_path, dictResult)
    dictCheck = fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "listAgreementChecks"][0]
    assert dictCheck["sOutcome"] == "not executed"
    assert "never ran" in dictCheck["sReason"]


@pytest.mark.falsification
def testAReportWithNoLedgerLineIsNeverRead(tmp_path):
    """Stale evidence cannot pass: a report for a command that did not run.

    The evidence directory is fresh in every lane, so a report without a
    ledger line is not this run's. Its passing testcases must not be
    read.

    Kills: reading the report of an invocation the ledger says never ran.
    """
    sRoot = _fsRoot(tmp_path)
    dictTests = {"dictQuantitative": fdictWriteGeneratedTests(
        sRoot, "StepOne", "quantitative",
        [fdictQuantitativeStandard("xValue", S_VALUES, "x", 1.0)])}
    dictStep = _fdictDataStep(sRoot, "StepOne", {"x": 1.0}, dictTests)
    dictResult = _fdictRun(tmp_path, [dictStep])
    fnWriteLedger(dictResult["sEvidence"], [
        tEntry for tEntry in dictResult["listLedger"]
        if tEntry[0] != "A01.quantitative.1"])
    dictReport = _fdictReassess(tmp_path, dictResult)
    dictInvocation = [dictRecord for dictRecord in dictReport["listInvocations"]
                      if dictRecord["sInvocationId"] == "A01.quantitative.1"][0]
    assert dictInvocation["iExitStatus"] is None
    assert dictInvocation["listTestcases"] == []
    assert fdictFindOutput(dictReport, f"StepOne/{S_VALUES}")[
        "sOutcome"] == "insufficient evidence"


@pytest.mark.falsification
def testATestcaseTwoExpectedFilesCouldClaimIsAmbiguous():
    """A classname that names two of a step's expected files is never guessed.

    Graded from observations built by hand, because a plan the builder
    makes pairs one file per category: the rule exists for any plan a
    frozen policy may be handed.

    Kills: crediting an ambiguous testcase to the first file it matches.
    """
    dictPlan = {
        "listCarriedPaths": [],
        "listSteps": [{
            "sStepLabel": "A01", "sStepDirectory": "S",
            "listDataInvocations": [], "listPlotInvocations": [],
            "dictTestGroups": {"quantitative": ["A01.quantitative.1"]},
            "listOutputs": [{"sPath": "S/out.json", "sSection": "data",
                             "sKind": "data"}],
            "listStandardSets": []}],
    }
    listEntries = [{"sCheckName": "xValue", "sTargetFile": "out.json"}]
    dictGrading = {
        "sComparisonPolicySource": "recorded", "sManifestState": "read",
        "sLedgerState": "read",
        "dictIntegrity": {"bInputsUnchanged": True},
        "dictInvocations": {"A01.quantitative.1": {
            "iExitStatus": 0, "sReportState": "read", "sReportDetail": "",
            "listTestcases": [{"sClassname": "tests.test_quantitative_s",
                               "sName": "test_quantitative_benchmark[xValue]",
                               "sResult": "passed"}]}},
        "listStandardSets": [
            {"sStepLabel": "A01", "sCategory": "quantitative",
             "sStandardsPath": "S/tests/quantitative_standards_s.json",
             "sTestPath": "S/tests/test_quantitative_s.py",
             "sReadState": "read", "sReadDetail": "",
             "listEntries": listEntries},
            {"sStepLabel": "A01", "sCategory": "integrity",
             "sStandardsPath": "S/other/tests/integrity_standards.json",
             "sTestPath": "S/other/tests/test_quantitative_s.py",
             "sReadState": "read", "sReadDetail": "",
             "listEntries": listEntries}],
        "dictOutputs": {"S/out.json": {
            "sExpectedSha256": "a", "sObservedSha256": "b",
            "dictImageCheck": None}},
        "dictContexts": {},
    }
    dictReport = comparisonPolicyV1.fdictGradeAssessment(dictPlan, dictGrading)
    dictOutput = dictReport["listOutputs"][0]
    dictQuantitative = dictOutput["listAgreementChecks"][0]
    assert dictQuantitative["sOutcome"] == "ambiguous"
    assert "S/tests/test_quantitative_s.py" in dictQuantitative["sReason"]
    assert "S/other/tests/test_quantitative_s.py" in dictQuantitative["sReason"]
    assert dictOutput["sOutcome"] == "insufficient evidence"
