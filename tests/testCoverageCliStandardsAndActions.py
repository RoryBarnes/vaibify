"""Branch coverage for ``vaibify generate-standards`` and ``vaibify do``.

``generate-standards`` works on real files in a tmp step directory, so
what is asserted is the file it writes. ``vaibify do`` is generated
from the agent-action catalog; its only external boundary is the hub
(session inspection and dispatch), which is replaced here, and the
dry run is used to observe exactly the call the command would make.

Four tests drive an error a researcher can type and assert that it
ends in a sentence and a nonzero exit rather than a Python traceback.
"""

import json
import os
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from vaibify.cli import actionCommands, configLoader, hubSession
from vaibify.cli.commandGenerateStandards import (
    _flistDiscoverDataFiles,
    fnGenerateStandardsCommand,
)
from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS

S_CONTAINER_NAME = "projectAlpha"
S_CONTAINER_ID = "cid-fedcba987654"
S_BASE_URL = "http://127.0.0.1:8123"


def fnAssertCleanExit(resultInvoke, iExpectedCode):
    """Assert the exit code and that no Python exception escaped."""
    if resultInvoke.exception is not None:
        assert isinstance(resultInvoke.exception, SystemExit), (
            f"uncaught {resultInvoke.exception!r}"
        )
    assert resultInvoke.exit_code == iExpectedCode, resultInvoke.output
    assert "Traceback" not in resultInvoke.output


# ---------------------------------------------------------------------
# generate-standards
# ---------------------------------------------------------------------


def fsMakeStepDirectory(tmp_path):
    """Return a step directory holding one CSV and one nested JSON."""
    pathStep = tmp_path / "stepAlpha"
    (pathStep / "output").mkdir(parents=True)
    (pathStep / "tests").mkdir()
    (pathStep / "Plot").mkdir()
    (pathStep / "dataFile.csv").write_text("1.5,2.5\n3.5,4.5\n")
    (pathStep / "output" / "summary.json").write_text(
        json.dumps({"fMean": 2.75}),
    )
    (pathStep / "tests" / "ignored.csv").write_text("9\n")
    (pathStep / "Plot" / "ignored.dat").write_text("9\n")
    (pathStep / "notes.md").write_text("not data\n")
    return str(pathStep)


def testDiscoveryFindsTopLevelAndOneDeepButSkipsScaffolding(tmp_path):
    sStepDir = fsMakeStepDirectory(tmp_path)
    assert _flistDiscoverDataFiles(sStepDir) == [
        "dataFile.csv", os.path.join("output", "summary.json"),
    ]


def testFreshStandardsAreGeneratedFromDiscoveredFilesAtTheGivenTolerance(
    tmp_path,
):
    sStepDir = fsMakeStepDirectory(tmp_path)
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand,
        ["--step-dir", sStepDir, "--rtol", "0.001"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert "No standards file at" in resultInvoke.output
    with open(os.path.join(
        sStepDir, "tests", "quantitative_standards.json",
    )) as fileHandle:
        dictWritten = json.load(fileHandle)
    assert dictWritten["fDefaultRtol"] == 0.001
    setDataFiles = {
        dictStandard["sDataFile"]
        for dictStandard in dictWritten["listStandards"]
    }
    assert setDataFiles == {
        "dataFile.csv", os.path.join("output", "summary.json"),
    }
    listMeans = [
        dictStandard["fValue"] for dictStandard in dictWritten["listStandards"]
        if dictStandard["sDataFile"].endswith("summary.json")
    ]
    assert listMeans == [2.75]


def testStepWithoutDataFilesExitsTwoAndWritesNothing(tmp_path):
    pathStep = tmp_path / "stepEmpty"
    pathStep.mkdir()
    (pathStep / "README").write_text("nothing here\n")
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand, ["--step-dir", str(pathStep)],
    )
    fnAssertCleanExit(resultInvoke, 2)
    assert "no data files found under step directory" in resultInvoke.output
    assert not (pathStep / "tests").exists()


def testDetectStochasticReportsAnUnseededScriptBeforeGenerating(tmp_path):
    sStepDir = fsMakeStepDirectory(tmp_path)
    with open(os.path.join(sStepDir, "dataAlpha.py"), "w") as fileHandle:
        fileHandle.write(
            "import numpy as np\ndaSample = np.random.rand(10)\n",
        )
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand,
        ["--step-dir", sStepDir, "--detect-stochastic"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert "dataAlpha.py" in resultInvoke.output
    assert "numpy legacy random" in resultInvoke.output
    assert os.path.isfile(os.path.join(
        sStepDir, "tests", "quantitative_standards.json",
    ))


def fsWriteWorkflow(tmp_path, listSteps):
    """Write a workflow JSON beside tmp step directories; return its path."""
    pathWorkflow = tmp_path / "workflow.json"
    pathWorkflow.write_text(json.dumps({"listSteps": listSteps}))
    return str(pathWorkflow)


def testUnknownStepLabelExitsTwoWithTheWorkflowsStepCount(tmp_path):
    sWorkflowPath = fsWriteWorkflow(tmp_path, [
        {"sName": "stepAlpha", "sDirectory": "stepAlpha"},
    ])
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand,
        ["--workflow", sWorkflowPath, "--step-label", "A05"],
    )
    fnAssertCleanExit(resultInvoke, 2)
    assert "no step 'A05'" in resultInvoke.output
    assert "1 automated step(s)" in resultInvoke.output


def testWorkflowLabelResolvesToTheStepAndInlinesItsStandards(tmp_path):
    fsMakeStepDirectory(tmp_path)
    sWorkflowPath = fsWriteWorkflow(tmp_path, [
        {"sName": "stepInteractive", "sDirectory": "stepAlpha",
         "bInteractive": True},
        {"sName": "stepAlpha", "sDirectory": "stepAlpha",
         "saOutputDataFiles": ["dataFile.csv"]},
    ])
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand,
        ["--workflow", sWorkflowPath, "--step-label", "A01"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert "Generating standards for: stepAlpha" in resultInvoke.output
    with open(sWorkflowPath) as fileHandle:
        dictWorkflow = json.load(fileHandle)
    assert "dictTests" not in dictWorkflow["listSteps"][0]
    sInlined = dictWorkflow["listSteps"][1]["dictTests"][
        "dictQuantitative"
    ]["sStandardsContent"]
    assert {
        dictStandard["sDataFile"]
        for dictStandard in json.loads(sInlined)["listStandards"]
    } == {"dataFile.csv"}


def testMalformedWorkflowFileExitsWithASentence(tmp_path):
    pathWorkflow = tmp_path / "workflow.json"
    pathWorkflow.write_text("{not json")
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand,
        ["--workflow", str(pathWorkflow), "--step-label", "A01"],
    )
    assert resultInvoke.exit_code != 0
    fnAssertCleanExit(resultInvoke, resultInvoke.exit_code)
    assert "Error:" in resultInvoke.output


def testRefreshNamingAMissingDataFileExitsWithASentence(tmp_path):
    pathStep = tmp_path / "stepAlpha"
    (pathStep / "tests").mkdir(parents=True)
    (pathStep / "tests" / "quantitative_standards.json").write_text(
        json.dumps({"listStandards": [{
            "sName": "firstValue", "sDataFile": "removedFile.csv",
            "sAccessPath": "index:0", "fValue": 1.0,
        }]}),
    )
    resultInvoke = CliRunner().invoke(
        fnGenerateStandardsCommand, ["--step-dir", str(pathStep)],
    )
    assert resultInvoke.exit_code != 0
    fnAssertCleanExit(resultInvoke, resultInvoke.exit_code)
    assert "removedFile.csv" in resultInvoke.output


# ---------------------------------------------------------------------
# vaibify do: template parsing helpers
# ---------------------------------------------------------------------


def fdictSyntheticEntry(sName, sMethod, sPath, **dictExtra):
    """Return a catalog-shaped entry that is not in the real catalog."""
    dictEntry = {
        "sName": sName, "sMethod": sMethod, "sPath": sPath,
        "sCategory": "files", "bAgentSafe": True,
        "sDescription": "A probe entry. With a second sentence.",
    }
    dictEntry.update(dictExtra)
    return dictEntry


def testUnclosedPlaceholderEndsTheScan():
    assert actionCommands.flistPathPlaceholders(
        "/api/{sContainerId}/files/{sFilePath",
    ) == ["sContainerId"]


def testCapitalisedPlaceholderIsUppercasedWhole():
    assert actionCommands.fsMetavarForPlaceholder("URLTarget") == "URLTARGET"


@pytest.mark.parametrize("sPath, sExpected", [
    ("/api/logs/tail", "/api/logs/tail?iLines=50"),
    ("/api/logs/tail?bFollow=true", "/api/logs/tail?bFollow=true&iLines=50"),
])
def testQueryFieldsAreAppendedWithTheRightSeparator(sPath, sExpected):
    assert actionCommands.fsAppendQueryString(
        sPath, {"iLines": 50},
    ) == sExpected


def testEmptyQueryLeavesThePathAlone():
    assert actionCommands.fsAppendQueryString("/api/x", {}) == "/api/x"


def testUndispatchableMethodBuildsNoCommand():
    assert actionCommands.fcommandBuildActionCommand(
        fdictSyntheticEntry("probe-patch", "PATCH", "/api/x"),
    ) is None


def testActionsWithoutACliAreSkippedAtRegistration(monkeypatch):
    sSkipped = LIST_AGENT_ACTIONS[0]["sName"]
    monkeypatch.setattr(
        actionCommands, "SET_ACTIONS_WITHOUT_CLI", frozenset({sSkipped}),
    )
    groupFresh = click.Group("doProbe")
    actionCommands.fnRegisterGeneratedActions(groupFresh)
    assert sSkipped not in groupFresh.commands
    assert LIST_AGENT_ACTIONS[1]["sName"] in groupFresh.commands


def testBareDoPrintsTheCatalogGroupedByCategory():
    resultInvoke = CliRunner().invoke(actionCommands.fnDoCommand, [])
    fnAssertCleanExit(resultInvoke, 0)
    for dictEntry in LIST_AGENT_ACTIONS:
        assert dictEntry["sName"] in resultInvoke.output
    listCategories = sorted({d["sCategory"] for d in LIST_AGENT_ACTIONS})
    listPositions = [
        resultInvoke.output.index("\n" + sCategory + "\n")
        for sCategory in listCategories
    ]
    assert listPositions == sorted(listPositions)
    assert "vaibify do <action> --help" in resultInvoke.output


# ---------------------------------------------------------------------
# vaibify do: a generated command end to end, dry run
# ---------------------------------------------------------------------


def fnPinHubInspection(monkeypatch, objAnswer=None):
    """Resolve the project and answer the hub inspection."""
    monkeypatch.setattr(
        configLoader, "fconfigResolveProject",
        lambda sName: SimpleNamespace(sProjectName=S_CONTAINER_NAME),
    )
    listInspected = []

    def fdictInspect(sContainerName, iPort):
        listInspected.append((sContainerName, iPort))
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return {"sContainerId": S_CONTAINER_ID, "sBaseUrl": S_BASE_URL}

    monkeypatch.setattr(hubSession, "fdictInspectHubSession", fdictInspect)
    return listInspected


def fresultRunSynthetic(dictEntry, listArguments):
    """Build the entry's generated command and invoke it."""
    return CliRunner().invoke(
        actionCommands.fcommandBuildActionCommand(dictEntry), listArguments,
    )


def testDryRunShowsTheQueryAndBodySplitAgainstTheContainerId(monkeypatch):
    listInspected = fnPinHubInspection(monkeypatch)
    dictEntry = fdictSyntheticEntry(
        "probe-tail", "GET", "/api/logs/{sContainerId}/{sFilePath:path}",
        saQueryFields=["iLines"],
    )
    resultInvoke = fresultRunSynthetic(dictEntry, [
        "output/run log.txt", "iLines=50", "sFilter=error", "--dry-run",
        "--port", "8123",
    ])
    fnAssertCleanExit(resultInvoke, 0)
    assert listInspected == [(S_CONTAINER_NAME, 8123)]
    jsonTarget = json.loads(resultInvoke.output)
    assert jsonTarget == {
        "sTransport": "HTTP", "sMethod": "GET",
        "sUrl": (
            S_BASE_URL + f"/api/logs/{S_CONTAINER_ID}/output/run%20log.txt"
            "?iLines=50"
        ),
        "dictFields": {"sFilter": "error"},
    }


def testHubRefusalExitsFourWithTheReason(monkeypatch):
    fnPinHubInspection(
        monkeypatch, hubSession.HubSessionError("no hub holds projectAlpha"),
    )
    resultInvoke = fresultRunSynthetic(
        fdictSyntheticEntry("probe-get", "GET", "/api/x/{sContainerId}"),
        ["--dry-run"],
    )
    fnAssertCleanExit(resultInvoke, 4)
    assert "Error: no hub holds projectAlpha" in resultInvoke.output


def testMalformedJsonFieldArgumentExitsWithASentence(monkeypatch):
    fnPinHubInspection(monkeypatch)
    resultInvoke = fresultRunSynthetic(
        fdictSyntheticEntry("probe-post", "POST", "/api/x/{sContainerId}"),
        ["{sName: stepAlpha}", "--dry-run"],
    )
    assert resultInvoke.exit_code != 0
    fnAssertCleanExit(resultInvoke, resultInvoke.exit_code)
    assert "not valid JSON" in resultInvoke.output


def testNonNumericStartStepExitsWithASentence(monkeypatch):
    fnPinHubInspection(monkeypatch)
    resultInvoke = CliRunner().invoke(
        actionCommands.fnDoCommand, ["run-from-step", "first", "--dry-run"],
    )
    assert resultInvoke.exit_code != 0
    fnAssertCleanExit(resultInvoke, resultInvoke.exit_code)
    assert "step label such as A09" in resultInvoke.output


def testRunFromStepDryRunCarriesTheLabelNotAnIndex(monkeypatch):
    fnPinHubInspection(monkeypatch)
    resultInvoke = CliRunner().invoke(
        actionCommands.fnDoCommand, ["run-from-step", "A09", "--dry-run"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    jsonTarget = json.loads(resultInvoke.output)
    assert jsonTarget["sTransport"] == "WS"
    assert jsonTarget["sContainerId"] == S_CONTAINER_ID
    assert jsonTarget["dictPayload"] == {
        "sAction": "runFrom", "sStartStepLabel": "A09",
    }
