"""Tests for vaibify.gui.workflowManager step CRUD and references."""

import pytest

from vaibify.gui.workflowManager import (
    fbValidateWorkflow,
    fsResolveVariables,
    fdictCreateStep,
    fnInsertStep,
    fnDeleteStep,
    fnReorderStep,
    flistValidateReferences,
    flistFindWorkflowsInContainer,
    DEFAULT_SEARCH_ROOT,
)


def _fdictBuildMinimalWorkflow(iStepCount=2):
    """Return a valid workflow dict with iStepCount simple steps."""
    listSteps = []
    for iIndex in range(iStepCount):
        listSteps.append({
            "sName": f"Step {iIndex + 1}",
            "sDirectory": f"/workspace/step{iIndex + 1}",
            "saPlotCommands": [f"python run{iIndex + 1}.py"],
            "saPlotFiles": [
                f"/workspace/step{iIndex + 1}/output.pdf"
            ],
        })
    return {
        "sPlotDirectory": "Plot",
        "listSteps": listSteps,
    }


def test_fbValidateWorkflow_valid():
    dictWorkflow = _fdictBuildMinimalWorkflow()
    assert fbValidateWorkflow(dictWorkflow) is True


def test_fbValidateWorkflow_missing_keys():
    dictMissingPlotDir = {"listSteps": []}
    assert fbValidateWorkflow(dictMissingPlotDir) is False

    dictMissingSteps = {"sPlotDirectory": "Plot"}
    assert fbValidateWorkflow(dictMissingSteps) is False

    dictMissingStepField = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "/tmp",
                "saPlotCommands": ["echo hi"],
            }
        ],
    }
    assert fbValidateWorkflow(dictMissingStepField) is False


def test_fsResolveVariables_replaces_tokens():
    sTemplate = "cp {sPlotDirectory}/{sFileName} /output/"
    dictVariables = {
        "sPlotDirectory": "Plot",
        "sFileName": "figure.pdf",
    }

    sResolved = fsResolveVariables(sTemplate, dictVariables)

    assert sResolved == "cp Plot/figure.pdf /output/"


def test_fsResolveVariables_leaves_unknown_tokens():
    sTemplate = "cp {sKnown}/{sUnknown} /out/"
    dictVariables = {"sKnown": "data"}

    sResolved = fsResolveVariables(sTemplate, dictVariables)

    assert sResolved == "cp data/{sUnknown} /out/"


def test_fdictCreateStep_returns_valid_dict():
    dictStep = fdictCreateStep(
        sName="TestStep",
        sDirectory="/workspace/test",
        bPlotOnly=False,
        saDataCommands=["make"],
        saPlotCommands=["python plot.py"],
        saPlotFiles=["output.pdf"],
    )

    assert dictStep["sName"] == "TestStep"
    assert dictStep["sDirectory"] == "/workspace/test"
    assert dictStep["bEnabled"] is True
    assert dictStep["bPlotOnly"] is False
    assert dictStep["saDataCommands"] == ["make"]
    assert dictStep["saPlotCommands"] == ["python plot.py"]
    assert dictStep["saPlotFiles"] == ["output.pdf"]


def test_fdictCreateStep_defaults():
    dictStep = fdictCreateStep(
        sName="MinimalStep",
        sDirectory="/workspace/min",
    )

    assert dictStep["bPlotOnly"] is True
    assert dictStep["saDataCommands"] == []
    assert dictStep["saPlotCommands"] == []
    assert dictStep["saPlotFiles"] == []


def test_fnInsertStep_renumbers_references():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "/workspace/s1",
                "saPlotCommands": ["python s1.py"],
                "saPlotFiles": ["/workspace/s1/out.pdf"],
            },
            {
                "sName": "Step 2",
                "sDirectory": "/workspace/s2",
                "saPlotCommands": [
                    "cp {Step01.out} /workspace/s2/input.pdf"
                ],
                "saPlotFiles": ["/workspace/s2/result.pdf"],
            },
        ],
    }

    dictNewStep = fdictCreateStep(
        sName="Inserted",
        sDirectory="/workspace/inserted",
        saPlotCommands=["echo inserted"],
        saPlotFiles=["/workspace/inserted/new.pdf"],
    )

    fnInsertStep(dictWorkflow, 1, dictNewStep)

    assert len(dictWorkflow["listSteps"]) == 3
    assert dictWorkflow["listSteps"][1]["sName"] == "Inserted"

    sUpdatedCommand = dictWorkflow["listSteps"][2]["saPlotCommands"][0]
    assert "Step01" in sUpdatedCommand


def test_fnDeleteStep_renumbers_references():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "/workspace/s1",
                "saPlotCommands": ["echo s1"],
                "saPlotFiles": ["/workspace/s1/out.pdf"],
            },
            {
                "sName": "Step 2",
                "sDirectory": "/workspace/s2",
                "saPlotCommands": ["echo s2"],
                "saPlotFiles": ["/workspace/s2/out.pdf"],
            },
            {
                "sName": "Step 3",
                "sDirectory": "/workspace/s3",
                "saPlotCommands": [
                    "cp {Step02.out} /workspace/s3/"
                ],
                "saPlotFiles": ["/workspace/s3/result.pdf"],
            },
        ],
    }

    fnDeleteStep(dictWorkflow, 0)

    assert len(dictWorkflow["listSteps"]) == 2
    assert dictWorkflow["listSteps"][0]["sName"] == "Step 2"

    sUpdatedCommand = dictWorkflow["listSteps"][1]["saPlotCommands"][0]
    assert "Step01" in sUpdatedCommand


def test_fnReorderStep_updates_references():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "/workspace/a",
                "saPlotCommands": ["echo a"],
                "saPlotFiles": ["/workspace/a/a.pdf"],
            },
            {
                "sName": "B",
                "sDirectory": "/workspace/b",
                "saPlotCommands": ["echo b"],
                "saPlotFiles": ["/workspace/b/b.pdf"],
            },
            {
                "sName": "C",
                "sDirectory": "/workspace/c",
                "saPlotCommands": [
                    "cp {Step01.a} /workspace/c/"
                ],
                "saPlotFiles": ["/workspace/c/c.pdf"],
            },
        ],
    }

    fnReorderStep(dictWorkflow, 0, 2)

    assert dictWorkflow["listSteps"][2]["sName"] == "A"
    assert dictWorkflow["listSteps"][0]["sName"] == "B"


def test_flistValidateReferences_detects_broken_refs():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Step 1",
                "sDirectory": "/workspace/s1",
                "saPlotCommands": [
                    "cp {Step05.nonexistent} /tmp/"
                ],
                "saPlotFiles": ["/workspace/s1/out.pdf"],
            },
        ],
    }

    listWarnings = flistValidateReferences(dictWorkflow)

    assert len(listWarnings) > 0
    bFoundBrokenRef = any(
        "Step05" in sWarning for sWarning in listWarnings
    )
    assert bFoundBrokenRef


def test_flistValidateReferences_clean_workflow():
    dictWorkflow = _fdictBuildMinimalWorkflow(iStepCount=1)

    listWarnings = flistValidateReferences(dictWorkflow)

    assert listWarnings == []


def test_flistFindWorkflowsInContainer_custom_search_root():
    class MockDockerConnection:
        def __init__(self):
            self.listCommands = []

        def ftResultExecuteCommand(self, sContainerId, sCommand):
            self.listCommands.append(sCommand)
            return (0, "")

    mockConnection = MockDockerConnection()

    flistFindWorkflowsInContainer(
        mockConnection, "abc123", sSearchRoot="/custom/root"
    )

    bFoundCustomRoot = any(
        "/custom/root" in s for s in mockConnection.listCommands
    )
    assert bFoundCustomRoot
