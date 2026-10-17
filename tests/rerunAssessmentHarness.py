"""Real repositories for the Level 3 assessment tests.

Every repository built here is a genuine directory: the steps' commands
run in ``bash``, the generated tests are vaibify's own templates run by
a real ``pytest`` that writes a real JUnit report, the manifest is
written by the real manifest writer, and the figures are real image
files. Nothing here hands the assessor a canned report. The expected
outcome in each test comes from the design's precedence table
(``design/cpuClassReproducibility.md``), not from the code under test.

``fdictRunRerunInPython`` mirrors the runner's order and failure
handling (design fact 24) -- data, then plots, then the test groups,
every step regardless of an earlier one's failure -- so the assessor
grades a run shaped like the ones the shadow and ``reproduce.sh``
produce. It is a test double for the LANE, never for the evidence.
"""

import json
import os
import subprocess
import sys

from vaibify.gui import templateManager
from vaibify.gui.testGenerator import (
    fsIntegrityStandardsPath,
    fsIntegrityTestPath,
    fsQualitativeStandardsPath,
    fsQualitativeTestPath,
    fsQuantitativeStandardsPath,
    fsQuantitativeTestPath,
)
from vaibify.reproducibility import (
    comparisonPolicyV1,
    rerunAssessment,
    rerunProcedure,
)
from vaibify.reproducibility.manifestWriter import fnWriteManifest
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider"

_DICT_CATEGORY_WRITERS = {
    "quantitative": (fsQuantitativeTestPath, fsQuantitativeStandardsPath,
                     templateManager.fsBuildQuantitativeTestCode),
    "integrity": (fsIntegrityTestPath, fsIntegrityStandardsPath,
                  templateManager.fsBuildIntegrityTestCode),
    "qualitative": (fsQualitativeTestPath, fsQualitativeStandardsPath,
                    templateManager.fsBuildQualitativeTestCode),
}


def fdictBuildStep(
    sName, sDirectory, saDataCommands=(), saPlotCommands=(),
    saOutputDataFiles=(), saPlotFiles=(), dictTests=None,
    saTestCommands=(), bInteractive=False, bPlotOnly=False,
):
    """Return one step in the workflow's own shape."""
    return {
        "sName": sName, "sDirectory": sDirectory,
        "bRunEnabled": True, "bInteractive": bInteractive,
        "bPlotOnly": bPlotOnly,
        "saDataCommands": list(saDataCommands),
        "saPlotCommands": list(saPlotCommands),
        "saOutputDataFiles": list(saOutputDataFiles),
        "saPlotFiles": list(saPlotFiles),
        "saTestCommands": list(saTestCommands),
        "dictTests": dict(dictTests or {}),
    }


def fdictBuildWorkflow(listSteps):
    """Return a workflow holding these steps."""
    return {"sWorkflowName": "assessment", "listSteps": list(listSteps)}


def fnWriteText(sRoot, sRelative, sText):
    """Write a text file into the repository, creating its directory."""
    sPath = os.path.join(sRoot, sRelative)
    os.makedirs(os.path.dirname(sPath) or sRoot, exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileOut:
        fileOut.write(sText)


def fnWriteBytes(sRoot, sRelative, baContent):
    """Write a binary file into the repository, creating its directory."""
    sPath = os.path.join(sRoot, sRelative)
    os.makedirs(os.path.dirname(sPath) or sRoot, exist_ok=True)
    with open(sPath, "wb") as fileOut:
        fileOut.write(baContent)


def fdictWriteGeneratedTests(sRoot, sStepDirectory, sCategory, listStandards):
    """Write a generated test file and its standards; return its dictTests entry."""
    fnTestPath, fnStandardsPath, fnCode = _DICT_CATEGORY_WRITERS[sCategory]
    sTestPath = fnTestPath(sStepDirectory)
    sStandardsPath = fnStandardsPath(sStepDirectory)
    fnWriteText(sRoot, sTestPath, fnCode())
    fnWriteText(sRoot, sStandardsPath, json.dumps(
        {"fDefaultRtol": 1e-6, "listStandards": listStandards}, indent=1))
    sRelativeTest = os.path.relpath(sTestPath, sStepDirectory)
    return {
        "saCommands": [f"{S_PYTEST} {sRelativeTest}"],
        "sFilePath": sTestPath, "sStandardsPath": sStandardsPath,
    }


def fdictQuantitativeStandard(sName, sDataFile, sKey, fValue):
    """Return one quantitative standard reading ``sKey`` from a JSON file."""
    return {"sName": sName, "sDataFile": sDataFile,
            "sAccessPath": f"key:{sKey}", "fValue": fValue,
            "sFormat": "json", "fRtol": 1e-6}


def fdictFileStandard(sFileName):
    """Return one integrity or qualitative standard naming a file."""
    return {"sFileName": sFileName, "sFormat": "json"}


def fnPinManifest(sRoot, dictWorkflow):
    """Pin the tree as it stands with vaibify's own manifest writer."""
    fnWriteManifest(HostRepoFiles(sRoot), dictWorkflow)


def fsWriteJsonCommand(sFileName, dictValues):
    """Return a data command that writes a JSON file with these values."""
    sJson = json.dumps(dictValues, sort_keys=True)
    return f"printf '%s' '{sJson}' > {sFileName}"


# ----------------------------------------------------------------------
# Running a rerun the way the lanes do
# ----------------------------------------------------------------------


def fiRunOneCommand(sRoot, sStepDirectory, sCommand, sJunitPath=""):
    """Run one command in bash from the step directory; return its status."""
    dictEnvironment = dict(os.environ)
    dictEnvironment.pop("PYTEST_ADDOPTS", None)
    if sJunitPath:
        dictEnvironment["PYTEST_ADDOPTS"] = f"--junitxml={sJunitPath}"
    completed = subprocess.run(
        ["bash", "-c", sCommand], cwd=os.path.join(sRoot, sStepDirectory),
        env=dictEnvironment, capture_output=True, text=True, timeout=300)
    return completed.returncode


def fbRunSection(sRoot, dictPlanStep, listIds, listCommands, sEvidence,
                 listLedger, bTest):
    """Run one section's commands until one fails; return True if all passed."""
    for sId, sCommand in zip(listIds, listCommands):
        sJunit = os.path.join(sEvidence, sId + ".xml") if bTest else ""
        iStatus = fiRunOneCommand(
            sRoot, dictPlanStep["sStepDirectory"], sCommand, sJunit)
        listLedger.append((sId, iStatus))
        if iStatus != 0:
            return False
    return True


def flistNonEmpty(listCommands):
    """Return the commands the plan numbers."""
    return [sCommand for sCommand in listCommands or []
            if (sCommand or "").strip()]


def fnRunStepsLikeTheRunner(sRoot, dictWorkflow, dictPlan, sEvidence,
                            listLedger):
    """Run every planned step: data, plots, then each test group."""
    from vaibify.gui.pipelineUtils import flistComputeAllStepLabels
    from vaibify.gui.workflowManager import fdictResolveTestCommandGroups
    dictStepsByLabel = dict(zip(
        flistComputeAllStepLabels(dictWorkflow["listSteps"]),
        dictWorkflow["listSteps"]))
    for dictPlanStep in dictPlan["listSteps"]:
        dictStep = dictStepsByLabel[dictPlanStep["sStepLabel"]]
        os.makedirs(os.path.join(sRoot, dictPlanStep["sStepDirectory"]),
                    exist_ok=True)
        if not fbRunSection(
                sRoot, dictPlanStep, dictPlanStep["listDataInvocations"],
                flistNonEmpty(dictStep["saDataCommands"]), sEvidence,
                listLedger, False):
            continue
        if not fbRunSection(
                sRoot, dictPlanStep, dictPlanStep["listPlotInvocations"],
                flistNonEmpty(dictStep["saPlotCommands"]), sEvidence,
                listLedger, False):
            continue
        dictGroups = fdictResolveTestCommandGroups(dictStep)
        for sGroup, listCommands in dictGroups.items():
            sSection = {"dictIntegrity": "integrity",
                        "dictQualitative": "qualitative",
                        "dictQuantitative": "quantitative"}.get(
                            sGroup, "legacy")
            fbRunSection(
                sRoot, dictPlanStep,
                dictPlanStep["dictTestGroups"][sSection],
                flistNonEmpty(listCommands), sEvidence, listLedger, True)


def fnWriteLedger(sEvidence, listLedger):
    """Write the invocation ledger the lanes write."""
    with open(os.path.join(sEvidence, "invocations.tsv"), "w") as fileOut:
        for sId, iStatus in listLedger:
            fileOut.write(f"{sId}\t{iStatus}\n")


def fdictMakeRunDirectories(sBase):
    """Return fresh scratch and evidence directories outside the repo."""
    sScratch = os.path.join(sBase, "scratch")
    sEvidence = os.path.join(sBase, "evidence")
    os.makedirs(sScratch)
    os.makedirs(sEvidence)
    return {"sScratch": sScratch, "sEvidence": sEvidence}


def fdictRunRerunInPython(sRoot, sBase, dictWorkflow, fnBetweenSteps=None,
                          dictPlan=None):
    """Prepare, clear, run the steps, and assess with the imported modules.

    Returns ``{iPrepare, iClear, iAssess, dictReport, dictPlan,
    listLedger}``. ``fnBetweenSteps`` runs after the steps and before the
    assessment, so a test can change a file mid-run.
    """
    dictPlan = dictPlan or rerunAssessment.fdictBuildAssessmentPlan(
        dictWorkflow)
    dictDirectories = fdictMakeRunDirectories(sBase)
    sScratch, sEvidence = dictDirectories["sScratch"], dictDirectories[
        "sEvidence"]
    dictResult = {"dictPlan": dictPlan, "listLedger": []}
    dictResult["iPrepare"] = rerunProcedure.fiRunMain(
        ["prepare", "--repo", sRoot, "--scratch", sScratch], dictPlan)
    if dictResult["iPrepare"] == 0:
        dictResult["iClear"] = rerunProcedure.fiRunMain(
            ["clear", "--repo", sRoot], dictPlan)
        fnRunStepsLikeTheRunner(
            sRoot, dictWorkflow, dictPlan, sEvidence, dictResult["listLedger"])
    if fnBetweenSteps is not None:
        fnBetweenSteps()
    fnWriteLedger(sEvidence, dictResult["listLedger"])
    dictObservations = rerunProcedure.fdictGatherProcedureObservations(
        sRoot, sScratch, sEvidence, dictPlan)
    dictObservations["sComparisonPolicySource"] = "recorded"
    dictResult["dictReport"] = comparisonPolicyV1.fdictAssessRerunEvidence(
        dictPlan, dictObservations)
    dictResult["iAssess"] = dictResult["dictReport"]["iExitCode"]
    dictResult["sScratch"], dictResult["sEvidence"] = sScratch, sEvidence
    return dictResult


def fdictFindOutput(dictReport, sPath):
    """Return the report's record for one output path."""
    for dictOutput in dictReport["listOutputs"]:
        if dictOutput["sPath"] == sPath:
            return dictOutput
    raise AssertionError(f"{sPath} is not in the report")
