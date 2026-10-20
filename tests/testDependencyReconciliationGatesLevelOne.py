"""A scan-found cross-step read the workflow does not declare blocks Level 1.

The workflow JSON is the dependency graph. When the scanner finds a
step's script reading another step's declared output, the edge must be
declared (a ``{step:<id>.<stem>}`` token or ``saDependencies``) or
dismissed by the researcher; otherwise the step is not Self-Consistent
(approval F2). A finding is evidence only while the script it was found
in and the output declaration it matched still hold: a moved script or
declaration, or a failed scan, reads "dependency scan not current" --
unknown, never a pass and never red from old data.

Every fixture keeps step id, label and index distinct, and the steps are
ordered so that no id equals its position, because the defect class this
repository fears is a map keyed by one and read by another.

The gate tests run over a REAL host repository (real script files,
hashed by the real adapter); the scanner test drives the real scanner
over a script served by a container double.
"""

import asyncio
import copy
import hashlib

import pytest

from vaibify.gui import stepDependencyScan
from vaibify.reproducibility import dependencyFindings, levelGates
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_PLOT_SCRIPT = "PlotResults/plot.py"
S_PLOT_SOURCE = (
    "import pandas as pd\n"
    "dfSamples = pd.read_csv('../MakeData/samples.csv')\n")


def _fdictPassedVerification():
    return {"sUnitTest": "passed", "sIntegrity": "passed",
            "sQualitative": "passed", "sQuantitative": "passed",
            "sUser": "passed"}


def _fdictStep(sStepId, sLabel, sDirectory, sOutput, sCommand):
    return {"sStepId": sStepId, "sName": sDirectory, "sLabel": sLabel,
            "sDirectory": sDirectory, "bNoInputData": True,
            "saOutputDataFiles": [sOutput], "saPlotFiles": [],
            "saDataCommands": [sCommand],
            "dictVerification": _fdictPassedVerification()}


def _fsSha256(sText):
    return hashlib.sha256(sText.encode("utf-8")).hexdigest()


def _fdictFinding(sScriptSha256):
    return {"sUpstreamStepId": "make-data", "sMatchedOutput": "samples.csv",
            "sScannedPath": "../MakeData/samples.csv",
            "sScriptPath": S_PLOT_SCRIPT, "sScriptSha256": sScriptSha256,
            "iLineNumber": 2, "sLoadFunction": "pd.read_csv"}


def _fdictWorkflow(sRepo, dictScan=None):
    """fit-model (A01) first, make-data (A02), then plot-results (A03)."""
    dictPlot = _fdictStep("plot-results", "A03", "PlotResults",
                          "figure.json", "python plot.py")
    dictWorkflow = {"sProjectRepoPath": sRepo, "listSteps": [
        _fdictStep("fit-model", "A01", "FitModel", "fit.json",
                   "python fit.py"),
        _fdictStep("make-data", "A02", "MakeData", "samples.csv",
                   "python make.py"),
        dictPlot,
    ]}
    if dictScan is not None:
        _fnRecordScan(dictWorkflow, dictScan)
    return dictWorkflow


def _fnRecordScan(dictWorkflow, dictScan, sScriptSha256=None):
    """Attach a scan of plot-results, stamped with what it examined."""
    dictPlot = next(dictStep for dictStep in dictWorkflow["listSteps"]
                    if dictStep["sStepId"] == "plot-results")
    dictScan.setdefault("dictScriptSha256", {
        S_PLOT_SCRIPT: sScriptSha256 or _fsSha256(S_PLOT_SOURCE)})
    dictScan.setdefault(
        "sScanInputsFingerprint",
        dependencyFindings.fsScanInputsFingerprint(dictWorkflow, dictPlot))
    dictPlot["dictDependencyScan"] = dictScan


@pytest.fixture
def sRepo(tmp_path):
    pathRepo = tmp_path / "repo"
    (pathRepo / "PlotResults").mkdir(parents=True)
    (pathRepo / S_PLOT_SCRIPT).write_text(S_PLOT_SOURCE)
    return str(pathRepo)


def _fdictScanned(sRepo):
    return {"sState": "scanned", "sError": "",
            "listFindings": [_fdictFinding(_fsSha256(S_PLOT_SOURCE))]}


def _flistLevel1(dictWorkflow, sRepo):
    return levelGates.flistLevel1Blockers(
        dictWorkflow, {}, HostRepoFiles(sRepo))


def _fdictPlotEntry(listBlockers):
    return next((dictEntry for dictEntry in listBlockers
                 if dictEntry["iStepIndex"] == 2), None)


def _fdictPlotLevel1Requirement(dictWorkflow, listBlockers):
    dictCell = levelGates.fdictComputeStepLevelStates(
        dictWorkflow, listBlockers, [], [])[2]["s1"]
    return dict((dictReq["sName"], dictReq["bMet"])
                for dictReq in dictCell["listRequirements"]), dictCell


# --------------------------------------------------- the blocker itself


@pytest.mark.falsification
def testAnUndeclaredScanFoundReadBlocksLevelOne(sRepo):
    """Kills: dropping the dependency-unreconciled criterion from the gate."""
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    listBlockers = _flistLevel1(dictWorkflow, sRepo)
    dictEntry = _fdictPlotEntry(listBlockers)
    assert dictEntry["sCriterion"] == "dependency-unreconciled"
    assert dictEntry["listOffendingFiles"] == [S_PLOT_SCRIPT]
    assert dictEntry["listOffendingUpstreamSteps"] == [1]
    assert "make-data" in dictEntry["sRemediationHint"]
    assert levelGates._fbComputeLevel1(
        dictWorkflow, HostRepoFiles(sRepo)) is False
    dictReqs, dictCell = _fdictPlotLevel1Requirement(
        dictWorkflow, listBlockers)
    assert dictReqs["dependencies-reconciled"] is False
    assert dictCell["sState"] != "attained"


def testAnUnscannedWorkflowMakesNoClaim(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo)
    listBlockers = _flistLevel1(dictWorkflow, sRepo)
    assert listBlockers == []
    dictReqs, _ = _fdictPlotLevel1Requirement(dictWorkflow, listBlockers)
    assert "dependencies-reconciled" not in dictReqs


def testDeclaringTheEdgeWithASymbolicTokenReconcilesIt(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    dictWorkflow["listSteps"][2]["saDataCommands"] = [
        "python plot.py {step:make-data.samples}"]
    assert _flistLevel1(dictWorkflow, sRepo) == []


def testDeclaringTheEdgeInSaDependenciesReconcilesIt(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    dictWorkflow["listSteps"][2]["saDependencies"] = [
        "{step:make-data.samples}"]
    assert _flistLevel1(dictWorkflow, sRepo) == []


@pytest.mark.falsification
def testADismissalHoldsOnlyAtTheScriptHashItWasMadeAgainst(sRepo):
    """Kills: honoring a dismissal whatever the script's hash."""
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    dictDismissal = {"sScannedPath": "../MakeData/samples.csv",
                     "sUpstreamStepId": "make-data",
                     "sScriptPath": S_PLOT_SCRIPT,
                     "sScriptSha256": _fsSha256(S_PLOT_SOURCE)}
    dictWorkflow["listSteps"][2]["listDismissedDependencyFindings"] = [
        dictDismissal]
    assert _flistLevel1(dictWorkflow, sRepo) == []
    dictDismissal["sScriptSha256"] = _fsSha256("an older script\n")
    assert _fdictPlotEntry(_flistLevel1(dictWorkflow, sRepo))[
        "sCriterion"] == "dependency-unreconciled"


# ------------------------------------------------------------ staleness


@pytest.mark.falsification
def testAScriptEditedSinceTheScanReadsNotCurrentUntilRescanned(sRepo):
    """Kills: trusting a finding whose script hash no longer matches."""
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    sEdited = S_PLOT_SOURCE + "# a later edit\n"
    with open(sRepo + "/" + S_PLOT_SCRIPT, "w") as fileOut:
        fileOut.write(sEdited)
    listBlockers = _flistLevel1(dictWorkflow, sRepo)
    dictEntry = _fdictPlotEntry(listBlockers)
    assert dictEntry["sCriterion"] == "dependency-scan-not-current"
    assert "changed since the dependency scan" in dictEntry[
        "sRemediationHint"]
    dictReqs, dictCell = _fdictPlotLevel1Requirement(
        dictWorkflow, listBlockers)
    assert dictReqs["dependencies-reconciled"] is None
    assert dictCell["sState"] != "attained"
    assert levelGates._fbComputeLevel1(
        dictWorkflow, HostRepoFiles(sRepo)) is False
    _fnRecordScan(dictWorkflow, {
        "sState": "scanned", "sError": "",
        "listFindings": [_fdictFinding(_fsSha256(sEdited))]},
        _fsSha256(sEdited))
    assert _fdictPlotEntry(_flistLevel1(dictWorkflow, sRepo))[
        "sCriterion"] == "dependency-unreconciled"


@pytest.mark.falsification
def testACleanScanGoesStaleWhenItsScriptGainsARead(sRepo):
    """Kills: proving a scan current only through the scripts it found in."""
    sClean = "print('nothing read')\n"
    with open(sRepo + "/" + S_PLOT_SCRIPT, "w") as fileOut:
        fileOut.write(sClean)
    dictWorkflow = _fdictWorkflow(sRepo, {
        "sState": "scanned", "sError": "", "listFindings": []})
    dictWorkflow["listSteps"][2]["dictDependencyScan"][
        "dictScriptSha256"] = {S_PLOT_SCRIPT: _fsSha256(sClean)}
    assert _flistLevel1(dictWorkflow, sRepo) == []
    with open(sRepo + "/" + S_PLOT_SCRIPT, "w") as fileOut:
        fileOut.write(S_PLOT_SOURCE)
    dictEntry = _fdictPlotEntry(_flistLevel1(dictWorkflow, sRepo))
    assert dictEntry["sCriterion"] == "dependency-scan-not-current"
    assert f"{S_PLOT_SCRIPT} changed since" in dictEntry["sRemediationHint"]


def testACleanScanGoesStaleWhenTheStepGainsACommand(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo, {
        "sState": "scanned", "sError": "", "listFindings": []})
    assert _flistLevel1(dictWorkflow, sRepo) == []
    dictWorkflow["listSteps"][2]["saPlotCommands"] = ["python extra.py"]
    dictEntry = _fdictPlotEntry(_flistLevel1(dictWorkflow, sRepo))
    assert dictEntry["sCriterion"] == "dependency-scan-not-current"
    assert "the step's scripts, or the workflow's steps" in dictEntry[
        "sRemediationHint"]


def testAChangedUpstreamDeclarationReadsNotCurrent(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    dictWorkflow["listSteps"][1]["saOutputDataFiles"] = ["draws.csv"]
    dictEntry = _fdictPlotEntry(_flistLevel1(dictWorkflow, sRepo))
    assert dictEntry["sCriterion"] == "dependency-scan-not-current"
    assert "no longer declares samples.csv" in dictEntry["sRemediationHint"]


def testAFailedScanReadsNotCurrentAndKeepsNoFinding(sRepo):
    dictWorkflow = _fdictWorkflow(sRepo, {
        "sState": "failed", "sError": "daemon unreachable",
        "listFindings": []})
    dictEntry = _fdictPlotEntry(_flistLevel1(dictWorkflow, sRepo))
    assert dictEntry["sCriterion"] == "dependency-scan-not-current"
    assert "daemon unreachable" in dictEntry["sRemediationHint"]


def testAReorderMovesTheBlockerWithTheStepIds(sRepo):
    """A read matches only an earlier step's output, so a reorder leaves
    the scan not current until it is redone; either way the blocker
    follows the step id, never the old position."""
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    dictWorkflow["listSteps"] = [dictWorkflow["listSteps"][i]
                                 for i in (1, 2, 0)]
    listBlockers = _flistLevel1(dictWorkflow, sRepo)
    assert [(dictEntry["iStepIndex"], dictEntry["sCriterion"])
            for dictEntry in listBlockers] == [
        (1, "dependency-scan-not-current")]
    _fnRecordScan(dictWorkflow, _fdictScanned(sRepo))
    listBlockers = _flistLevel1(dictWorkflow, sRepo)
    dictEntry = next(dictEntry for dictEntry in listBlockers
                     if dictEntry["sCriterion"] == "dependency-unreconciled")
    assert dictEntry["iStepIndex"] == 1
    assert dictEntry["listOffendingUpstreamSteps"] == [0]


def testTheStateRidesOnWhicheverBlockerDominates(sRepo):
    """Input data undeclared outranks it; the cell must still see it."""
    dictWorkflow = _fdictWorkflow(sRepo, _fdictScanned(sRepo))
    dictWorkflow["listSteps"][2]["bNoInputData"] = False
    listBlockers = _flistLevel1(dictWorkflow, sRepo)
    dictEntry = _fdictPlotEntry(listBlockers)
    assert dictEntry["sCriterion"] == "input-data-undeclared"
    assert dictEntry["sDependencyState"] == "unreconciled"
    dictReqs, _ = _fdictPlotLevel1Requirement(dictWorkflow, listBlockers)
    assert dictReqs["dependencies-reconciled"] is False


# ------------------------------------------------------- the real scan


class _ScriptServingDocker:
    """Serves one script; any other path is absent."""

    def __init__(self, dictFiles, errorOnFetch=None):
        self.dictFiles = dictFiles
        self.errorOnFetch = errorOnFetch

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if self.errorOnFetch is not None:
            raise self.errorOnFetch
        if sPath in self.dictFiles:
            return self.dictFiles[sPath].encode("utf-8")
        raise FileNotFoundError(sPath)


def testTheRealScanFilesAFindingByIdWithItsScriptHash(sRepo):
    dictWorkflow = _fdictWorkflow("/workspace/repo")
    dictCtx = {"docker": _ScriptServingDocker(
        {"/workspace/repo/" + S_PLOT_SCRIPT: S_PLOT_SOURCE}),
        "sourceCodeDeps": {}}
    dictScan = asyncio.run(stepDependencyScan.fdictScanWorkflowDependencies(
        dictCtx, "cid", dictWorkflow))
    assert dictScan["plot-results"]["sState"] == "scanned"
    dictFinding = dictScan["plot-results"]["listFindings"][0]
    assert {sKey: dictFinding[sKey] for sKey in (
        "sUpstreamStepId", "sMatchedOutput", "sScriptPath",
        "sScriptSha256")} == {
        "sUpstreamStepId": "make-data", "sMatchedOutput": "samples.csv",
        "sScriptPath": S_PLOT_SCRIPT,
        "sScriptSha256": _fsSha256(S_PLOT_SOURCE)}
    assert dictScan["make-data"]["listFindings"] == []
    assert dictScan["make-data"]["dictScriptSha256"] == {
        "MakeData/make.py": ""}
    assert dictScan["plot-results"]["dictScriptSha256"] == {
        S_PLOT_SCRIPT: _fsSha256(S_PLOT_SOURCE)}
    stepDependencyScan.fnStoreScanResults(
        dictCtx, "cid", dictWorkflow, dictScan)
    assert dictWorkflow["listSteps"][2]["saSourceCodeDeps"] == ["make-data"]
    dictReloaded = copy.deepcopy(dictWorkflow)
    for dictStep in dictReloaded["listSteps"]:
        dictStep.pop("dictDependencyScan", None)
    stepDependencyScan.fnReapplyCachedScan(dictCtx, "cid", dictReloaded)
    assert dictReloaded["listSteps"][2]["dictDependencyScan"] == dictScan[
        "plot-results"]


def testARaisingScanRecordsTheStepFailedWithItsReason():
    dictWorkflow = _fdictWorkflow("/workspace/repo")
    dictCtx = {"docker": _ScriptServingDocker(
        {}, errorOnFetch=RuntimeError("exec refused")),
        "sourceCodeDeps": {}}
    dictScan = asyncio.run(stepDependencyScan.fdictScanWorkflowDependencies(
        dictCtx, "cid", dictWorkflow))
    assert dictScan["plot-results"] == {
        "sState": "failed", "listFindings": [], "sError": "exec refused"}
