"""Tests for vaibcask.gui.sceneManager scene CRUD and references."""

import pytest

from vaibcask.gui.sceneManager import (
    fbValidateScript,
    fsResolveVariables,
    fdictCreateScene,
    fnInsertScene,
    fnDeleteScene,
    fnReorderScene,
    flistValidateReferences,
    flistFindScriptsInContainer,
    DEFAULT_SEARCH_ROOT,
)


def _fdictBuildMinimalScript(iSceneCount=2):
    """Return a valid script dict with iSceneCount simple scenes."""
    listScenes = []
    for iIndex in range(iSceneCount):
        listScenes.append({
            "sName": f"Scene {iIndex + 1}",
            "sDirectory": f"/workspace/scene{iIndex + 1}",
            "saCommands": [f"python run{iIndex + 1}.py"],
            "saOutputFiles": [
                f"/workspace/scene{iIndex + 1}/output.pdf"
            ],
        })
    return {
        "sPlotDirectory": "Plot",
        "listScenes": listScenes,
    }


def test_fbValidateScript_valid():
    dictScript = _fdictBuildMinimalScript()
    assert fbValidateScript(dictScript) is True


def test_fbValidateScript_missing_keys():
    dictMissingPlotDir = {"listScenes": []}
    assert fbValidateScript(dictMissingPlotDir) is False

    dictMissingScenes = {"sPlotDirectory": "Plot"}
    assert fbValidateScript(dictMissingScenes) is False

    dictMissingSceneField = {
        "sPlotDirectory": "Plot",
        "listScenes": [
            {
                "sName": "A",
                "sDirectory": "/tmp",
                "saCommands": ["echo hi"],
            }
        ],
    }
    assert fbValidateScript(dictMissingSceneField) is False


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


def test_fdictCreateScene_returns_valid_dict():
    dictScene = fdictCreateScene(
        sName="TestScene",
        sDirectory="/workspace/test",
        bPlotOnly=False,
        saSetupCommands=["make"],
        saCommands=["python plot.py"],
        saOutputFiles=["output.pdf"],
    )

    assert dictScene["sName"] == "TestScene"
    assert dictScene["sDirectory"] == "/workspace/test"
    assert dictScene["bEnabled"] is True
    assert dictScene["bPlotOnly"] is False
    assert dictScene["saSetupCommands"] == ["make"]
    assert dictScene["saCommands"] == ["python plot.py"]
    assert dictScene["saOutputFiles"] == ["output.pdf"]


def test_fdictCreateScene_defaults():
    dictScene = fdictCreateScene(
        sName="MinimalScene",
        sDirectory="/workspace/min",
    )

    assert dictScene["bPlotOnly"] is True
    assert dictScene["saSetupCommands"] == []
    assert dictScene["saCommands"] == []
    assert dictScene["saOutputFiles"] == []


def test_fnInsertScene_renumbers_references():
    dictScript = {
        "sPlotDirectory": "Plot",
        "listScenes": [
            {
                "sName": "Scene 1",
                "sDirectory": "/workspace/s1",
                "saCommands": ["python s1.py"],
                "saOutputFiles": ["/workspace/s1/out.pdf"],
            },
            {
                "sName": "Scene 2",
                "sDirectory": "/workspace/s2",
                "saCommands": [
                    "cp {Scene01.out} /workspace/s2/input.pdf"
                ],
                "saOutputFiles": ["/workspace/s2/result.pdf"],
            },
        ],
    }

    dictNewScene = fdictCreateScene(
        sName="Inserted",
        sDirectory="/workspace/inserted",
        saCommands=["echo inserted"],
        saOutputFiles=["/workspace/inserted/new.pdf"],
    )

    fnInsertScene(dictScript, 1, dictNewScene)

    assert len(dictScript["listScenes"]) == 3
    assert dictScript["listScenes"][1]["sName"] == "Inserted"

    sUpdatedCommand = dictScript["listScenes"][2]["saCommands"][0]
    assert "Scene01" in sUpdatedCommand


def test_fnDeleteScene_renumbers_references():
    dictScript = {
        "sPlotDirectory": "Plot",
        "listScenes": [
            {
                "sName": "Scene 1",
                "sDirectory": "/workspace/s1",
                "saCommands": ["echo s1"],
                "saOutputFiles": ["/workspace/s1/out.pdf"],
            },
            {
                "sName": "Scene 2",
                "sDirectory": "/workspace/s2",
                "saCommands": ["echo s2"],
                "saOutputFiles": ["/workspace/s2/out.pdf"],
            },
            {
                "sName": "Scene 3",
                "sDirectory": "/workspace/s3",
                "saCommands": [
                    "cp {Scene02.out} /workspace/s3/"
                ],
                "saOutputFiles": ["/workspace/s3/result.pdf"],
            },
        ],
    }

    fnDeleteScene(dictScript, 0)

    assert len(dictScript["listScenes"]) == 2
    assert dictScript["listScenes"][0]["sName"] == "Scene 2"

    sUpdatedCommand = dictScript["listScenes"][1]["saCommands"][0]
    assert "Scene01" in sUpdatedCommand


def test_fnReorderScene_updates_references():
    dictScript = {
        "sPlotDirectory": "Plot",
        "listScenes": [
            {
                "sName": "A",
                "sDirectory": "/workspace/a",
                "saCommands": ["echo a"],
                "saOutputFiles": ["/workspace/a/a.pdf"],
            },
            {
                "sName": "B",
                "sDirectory": "/workspace/b",
                "saCommands": ["echo b"],
                "saOutputFiles": ["/workspace/b/b.pdf"],
            },
            {
                "sName": "C",
                "sDirectory": "/workspace/c",
                "saCommands": [
                    "cp {Scene01.a} /workspace/c/"
                ],
                "saOutputFiles": ["/workspace/c/c.pdf"],
            },
        ],
    }

    fnReorderScene(dictScript, 0, 2)

    assert dictScript["listScenes"][2]["sName"] == "A"
    assert dictScript["listScenes"][0]["sName"] == "B"


def test_flistValidateReferences_detects_broken_refs():
    dictScript = {
        "sPlotDirectory": "Plot",
        "listScenes": [
            {
                "sName": "Scene 1",
                "sDirectory": "/workspace/s1",
                "saCommands": [
                    "cp {Scene05.nonexistent} /tmp/"
                ],
                "saOutputFiles": ["/workspace/s1/out.pdf"],
            },
        ],
    }

    listWarnings = flistValidateReferences(dictScript)

    assert len(listWarnings) > 0
    bFoundBrokenRef = any(
        "Scene05" in sWarning for sWarning in listWarnings
    )
    assert bFoundBrokenRef


def test_flistValidateReferences_clean_script():
    dictScript = _fdictBuildMinimalScript(iSceneCount=1)

    listWarnings = flistValidateReferences(dictScript)

    assert listWarnings == []


def test_flistFindScriptsInContainer_uses_search_root():
    """Verify that flistFindScriptsInContainer defaults to DEFAULT_SEARCH_ROOT."""

    class MockDockerConnection:
        def __init__(self):
            self.sCapturedCommand = None

        def ftResultExecuteCommand(self, sContainerId, sCommand):
            self.sCapturedCommand = sCommand
            return (0, "/workspace/project/script.json\n")

    mockConnection = MockDockerConnection()

    listPaths = flistFindScriptsInContainer(
        mockConnection, "abc123"
    )

    assert DEFAULT_SEARCH_ROOT in mockConnection.sCapturedCommand
    assert "/workspace/project/script.json" in listPaths


def test_flistFindScriptsInContainer_custom_search_root():
    class MockDockerConnection:
        def __init__(self):
            self.sCapturedCommand = None

        def ftResultExecuteCommand(self, sContainerId, sCommand):
            self.sCapturedCommand = sCommand
            return (0, "")

    mockConnection = MockDockerConnection()

    flistFindScriptsInContainer(
        mockConnection, "abc123", sSearchRoot="/custom/root"
    )

    assert "/custom/root" in mockConnection.sCapturedCommand
