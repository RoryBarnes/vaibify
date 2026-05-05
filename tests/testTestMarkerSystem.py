"""Tests for test marker system: testGenerator, syncDispatcher, pipelineServer, pipelineRunner."""

import json

import pytest

from vaibify.gui.testGenerator import (
    fsConftestPath,
    fsConftestContent,
    _CONFTEST_MARKER_TEMPLATE,
)
from vaibify.gui.syncDispatcher import (
    fsBuildTestMarkerCheckCommand,
    fdictParseTestMarkerOutput,
)
from vaibify.gui.pipelineServer import (
    _flistExtractStepDirectories,
    _fdictBuildTestMarkerStatus,
    _fbMarkerStale,
    _fnApplyExternalTestResults,
    _fnApplyMarkerCategory,
    _fdictBuildTestFileChanges,
    _fsetExtractRegisteredTestFiles,
    _flistResolveTestCommands as flistResolveTestCommandsServer,
)
from vaibify.gui.pipelineRunner import (
    _fdictBuildWorkflowVars,
    _flistResolveTestCommands as flistResolveTestCommandsRunner,
)


# ---- testGenerator: fsConftestPath ----

def test_fsConftestPath_basic():
    sResult = fsConftestPath("/workspace/step1")
    assert sResult == "/workspace/step1/tests/conftest.py"


def test_fsConftestPath_nested():
    sResult = fsConftestPath("/workspace/project/deep/step")
    assert sResult.endswith("tests/conftest.py")
    assert "/workspace/project/deep/step/" in sResult


def test_fsConftestPath_trailing_slash():
    sResult = fsConftestPath("step1/")
    assert "conftest.py" in sResult


# ---- testGenerator: fsConftestContent ----

def test_fsConftestContent_returns_string():
    sContent = fsConftestContent()
    assert isinstance(sContent, str)
    assert len(sContent) > 0


def test_fsConftestContent_matches_template():
    assert fsConftestContent() == _CONFTEST_MARKER_TEMPLATE


# ---- testGenerator: _CONFTEST_MARKER_TEMPLATE ----

def test_conftestTemplate_valid_python():
    compile(_CONFTEST_MARKER_TEMPLATE, "<conftest>", "exec")


def test_conftestTemplate_contains_pytest_hook():
    assert "pytest_sessionfinish" in _CONFTEST_MARKER_TEMPLATE


def test_conftestTemplate_references_marker_base():
    # Marker base dir is defined in the parameterized prologue that
    # fsBuildConftestSource prepends; the template body resolves the
    # active workflow's slug at run time and joins it onto _MARKER_BASE.
    assert "_MARKER_BASE" in _CONFTEST_MARKER_TEMPLATE


def test_conftestTemplate_reads_active_workflow_slug_env():
    # The pipeline runner sets VAIBIFY_ACTIVE_WORKFLOW_SLUG when
    # invoking pytest; the conftest reads it to namespace markers per
    # workflow.
    assert "VAIBIFY_ACTIVE_WORKFLOW_SLUG" in _CONFTEST_MARKER_TEMPLATE


# ---- syncDispatcher: fsBuildTestMarkerCheckCommand ----

def test_fsBuildTestMarkerCheckCommand_non_empty():
    sCommand = fsBuildTestMarkerCheckCommand(
        ["/workspace/step1"], "/workspace/DemoRepo", "demo")
    assert len(sCommand) > 0
    assert "python3" in sCommand


def test_fsBuildTestMarkerCheckCommand_includes_directories():
    listDirs = ["/workspace/step1", "/workspace/step2"]
    sCommand = fsBuildTestMarkerCheckCommand(
        listDirs, "/workspace/DemoRepo", "demo")
    assert "/workspace/step1" in sCommand
    assert "/workspace/step2" in sCommand


def test_fsBuildTestMarkerCheckCommand_empty_list():
    sCommand = fsBuildTestMarkerCheckCommand(
        [], "/workspace/DemoRepo", "demo")
    assert len(sCommand) > 0


def test_fsBuildTestMarkerCheckCommand_scopes_to_workflow_slug():
    sCommand = fsBuildTestMarkerCheckCommand(
        ["/workspace/step1"], "/workspace/DemoRepo", "wfa")
    assert "/workspace/DemoRepo/.vaibify/test_markers/wfa" in sCommand


# ---- syncDispatcher: fdictParseTestMarkerOutput ----

def test_fdictParseTestMarkerOutput_empty_string():
    dictResult = fdictParseTestMarkerOutput("")
    assert dictResult == {"markers": {}, "testFiles": {}, "missingConftest": []}


def test_fdictParseTestMarkerOutput_none():
    dictResult = fdictParseTestMarkerOutput(None)
    assert dictResult == {"markers": {}, "testFiles": {}, "missingConftest": []}


def test_fdictParseTestMarkerOutput_invalid_json():
    dictResult = fdictParseTestMarkerOutput("not json at all")
    assert dictResult == {"markers": {}, "testFiles": {}, "missingConftest": []}


def test_fdictParseTestMarkerOutput_valid_json():
    dictInput = {
        "markers": {"step1.json": {"iExitStatus": 0}},
        "testFiles": {"/workspace/step1": {"listFiles": ["test_foo.py"]}},
        "missingConftest": [],
    }
    dictResult = fdictParseTestMarkerOutput(json.dumps(dictInput))
    assert dictResult["markers"]["step1.json"]["iExitStatus"] == 0
    assert "test_foo.py" in dictResult["testFiles"]["/workspace/step1"]["listFiles"]


def test_fdictParseTestMarkerOutput_with_whitespace():
    dictInput = {"markers": {}, "testFiles": {}, "missingConftest": []}
    sJson = "  \n" + json.dumps(dictInput) + "\n  "
    dictResult = fdictParseTestMarkerOutput(sJson)
    assert dictResult == dictInput


def test_fdictParseTestMarkerOutput_real_marker_data():
    dictInput = {
        "markers": {
            "step1.json": {
                "sDirectory": "step1",
                "iExitStatus": 0,
                "fTimestamp": 1700000000.0,
                "iCollected": 5,
                "dictCategories": {
                    "integrity": {"iPassed": 3, "iFailed": 0},
                    "qualitative": {"iPassed": 2, "iFailed": 0},
                },
            }
        },
        "testFiles": {
            "step1": {
                "listFiles": ["test_integrity.py", "test_qualitative.py"],
                "dictMtimes": {
                    "test_integrity.py": 1699999990.0,
                    "test_qualitative.py": 1699999995.0,
                },
            }
        },
        "missingConftest": ["/workspace/step2"],
    }
    dictResult = fdictParseTestMarkerOutput(json.dumps(dictInput))
    assert len(dictResult["markers"]) == 1
    assert dictResult["missingConftest"] == ["/workspace/step2"]


# ---- pipelineServer: _flistExtractStepDirectories ----

def test_flistExtractStepDirectories_basic():
    dictWorkflow = {
        "listSteps": [
            {"sDirectory": "step1", "sName": "Step 1"},
            {"sDirectory": "step2", "sName": "Step 2"},
        ]
    }
    listDirs = _flistExtractStepDirectories(dictWorkflow)
    assert listDirs == ["step1", "step2"]


def test_flistExtractStepDirectories_skips_empty():
    dictWorkflow = {
        "listSteps": [
            {"sDirectory": "step1"},
            {"sDirectory": ""},
            {"sName": "No dir"},
        ]
    }
    listDirs = _flistExtractStepDirectories(dictWorkflow)
    assert listDirs == ["step1"]


def test_flistExtractStepDirectories_empty_workflow():
    assert _flistExtractStepDirectories({}) == []
    assert _flistExtractStepDirectories({"listSteps": []}) == []


# ---- pipelineServer: _fdictBuildTestMarkerStatus ----

def test_fdictBuildTestMarkerStatus_matches_marker():
    dictWorkflow = {
        "listSteps": [
            {"sDirectory": "step1"},
        ]
    }
    dictTestInfo = {
        "markers": {
            "step1.json": {
                "fTimestamp": 1700000000.0,
                "dictCategories": {},
            }
        },
        "testFiles": {},
    }
    dictResult = _fdictBuildTestMarkerStatus(dictWorkflow, dictTestInfo)
    assert "0" in dictResult
    assert "dictMarker" in dictResult["0"]


def test_fdictBuildTestMarkerStatus_no_matching_marker():
    dictWorkflow = {
        "listSteps": [{"sDirectory": "step1"}]
    }
    dictTestInfo = {"markers": {}, "testFiles": {}}
    dictResult = _fdictBuildTestMarkerStatus(dictWorkflow, dictTestInfo)
    assert dictResult == {}


def test_fdictBuildTestMarkerStatus_stale_detection():
    dictWorkflow = {
        "listSteps": [{"sDirectory": "step1"}]
    }
    dictTestInfo = {
        "markers": {
            "step1.json": {
                "fTimestamp": 1700000000.0,
            }
        },
        "testFiles": {
            "step1": {
                "dictMtimes": {"test_foo.py": 1700000001.0},
            }
        },
    }
    dictResult = _fdictBuildTestMarkerStatus(dictWorkflow, dictTestInfo)
    assert dictResult["0"]["bStale"] is True


# ---- pipelineServer: _fbMarkerStale ----

def test_fbMarkerStale_no_files():
    dictMarker = {"fTimestamp": 100.0, "sRunAtUtc": "2026-04-23T00:00:00Z"}
    assert _fbMarkerStale(dictMarker, {}) is False


def test_fbMarkerStale_older_files():
    dictMarker = {"fTimestamp": 100.0, "sRunAtUtc": "2026-04-23T00:00:00Z"}
    dictFileInfo = {"dictMtimes": {"test_a.py": 90.0, "test_b.py": 95.0}}
    assert _fbMarkerStale(dictMarker, dictFileInfo) is False


def test_fbMarkerStale_newer_file():
    dictMarker = {"fTimestamp": 100.0, "sRunAtUtc": "2026-04-23T00:00:00Z"}
    dictFileInfo = {"dictMtimes": {"test_a.py": 90.0, "test_b.py": 110.0}}
    assert _fbMarkerStale(dictMarker, dictFileInfo) is True


def test_fbMarkerStale_missing_timestamp():
    dictMarker = {}
    dictFileInfo = {"dictMtimes": {"test_a.py": 1.0}}
    assert _fbMarkerStale(dictMarker, dictFileInfo) is True


# ---- pipelineServer: _fnApplyExternalTestResults ----

def test_fnApplyExternalTestResults_applies_passed():
    dictWorkflow = {
        "listSteps": [{"sDirectory": "step1"}]
    }
    dictTestMarkers = {
        "0": {
            "bStale": False,
            "dictMarker": {
                "dictCategories": {
                    "integrity": {"iPassed": 3, "iFailed": 0},
                }
            },
        }
    }
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sIntegrity"] == "passed"


def test_fnApplyExternalTestResults_applies_failed():
    dictWorkflow = {
        "listSteps": [{"sDirectory": "step1"}]
    }
    dictTestMarkers = {
        "0": {
            "bStale": False,
            "dictMarker": {
                "dictCategories": {
                    "qualitative": {"iPassed": 2, "iFailed": 1},
                }
            },
        }
    }
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sQualitative"] == "failed"


def test_fnApplyExternalTestResults_resets_stale_categories_to_untested():
    """A stale marker's categories are reset to "untested" rather than
    leaving stale "passed"/"failed" values in place."""
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictVerification": {"sIntegrity": "passed"},
            },
        ],
    }
    dictTestMarkers = {
        "0": {
            "bStale": True,
            "dictMarker": {
                "dictCategories": {
                    "integrity": {"iPassed": 3, "iFailed": 0},
                },
            },
        },
    }
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    assert dictVerify["sIntegrity"] == "untested"


def test_fnApplyExternalTestResults_skips_out_of_range():
    dictWorkflow = {"listSteps": [{"sDirectory": "step1"}]}
    dictTestMarkers = {
        "5": {
            "bStale": False,
            "dictMarker": {"dictCategories": {}},
        }
    }
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
    assert "dictVerification" not in dictWorkflow["listSteps"][0]


# ---- pipelineServer: _fnApplyMarkerCategory ----

def test_fnApplyMarkerCategory_passed():
    dictVerify = {}
    dictCategories = {"integrity": {"iPassed": 5, "iFailed": 0}}
    _fnApplyMarkerCategory(dictVerify, dictCategories, "integrity", "sIntegrity")
    assert dictVerify["sIntegrity"] == "passed"


def test_fnApplyMarkerCategory_failed():
    dictVerify = {}
    dictCategories = {"integrity": {"iPassed": 3, "iFailed": 2}}
    _fnApplyMarkerCategory(dictVerify, dictCategories, "integrity", "sIntegrity")
    assert dictVerify["sIntegrity"] == "failed"


def test_fnApplyMarkerCategory_missing_category():
    dictVerify = {}
    dictCategories = {}
    _fnApplyMarkerCategory(dictVerify, dictCategories, "integrity", "sIntegrity")
    assert dictVerify == {}


def test_fnApplyMarkerCategory_zero_passed_zero_failed():
    dictVerify = {}
    dictCategories = {"integrity": {"iPassed": 0, "iFailed": 0}}
    _fnApplyMarkerCategory(dictVerify, dictCategories, "integrity", "sIntegrity")
    assert "sIntegrity" not in dictVerify


# ---- pipelineServer: _fsetExtractRegisteredTestFiles ----

def test_fsetExtractRegisteredTestFiles_basic():
    dictStep = {
        "dictTests": {
            "integrity": {
                "saCommands": ["pytest test_integrity.py -v"],
            },
            "qualitative": {
                "saCommands": ["pytest test_qualitative.py"],
            },
        }
    }
    setResult = _fsetExtractRegisteredTestFiles(dictStep)
    assert "test_integrity.py" in setResult
    assert "test_qualitative.py" in setResult


def test_fsetExtractRegisteredTestFiles_with_tests_prefix():
    dictStep = {
        "dictTests": {
            "integrity": {
                "saCommands": ["pytest tests/test_integrity.py"],
            },
        }
    }
    setResult = _fsetExtractRegisteredTestFiles(dictStep)
    assert "test_integrity.py" in setResult


def test_fsetExtractRegisteredTestFiles_empty():
    assert _fsetExtractRegisteredTestFiles({}) == set()
    assert _fsetExtractRegisteredTestFiles({"dictTests": {}}) == set()


# ---- pipelineServer: _fdictBuildTestFileChanges ----

def test_fdictBuildTestFileChanges_detects_new_file():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictTests": {
                    "integrity": {
                        "saCommands": ["pytest test_integrity.py"],
                    },
                },
            }
        ]
    }
    dictTestInfo = {
        "testFiles": {
            "step1": {
                "listFiles": ["test_integrity.py", "test_new.py"],
            }
        }
    }
    dictResult = _fdictBuildTestFileChanges(dictWorkflow, dictTestInfo)
    assert "0" in dictResult
    assert "test_new.py" in dictResult["0"]["listNew"]


def test_fdictBuildTestFileChanges_detects_missing_file():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictTests": {
                    "integrity": {
                        "saCommands": ["pytest test_integrity.py"],
                    },
                },
            }
        ]
    }
    dictTestInfo = {
        "testFiles": {
            "step1": {"listFiles": []},
        }
    }
    dictResult = _fdictBuildTestFileChanges(dictWorkflow, dictTestInfo)
    assert "0" in dictResult
    assert "test_integrity.py" in dictResult["0"]["listMissing"]


def test_fdictBuildTestFileChanges_no_changes():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "step1",
                "dictTests": {
                    "integrity": {
                        "saCommands": ["pytest test_integrity.py"],
                    },
                },
            }
        ]
    }
    dictTestInfo = {
        "testFiles": {
            "step1": {"listFiles": ["test_integrity.py"]},
        }
    }
    dictResult = _fdictBuildTestFileChanges(dictWorkflow, dictTestInfo)
    assert dictResult == {}


def test_fdictBuildTestFileChanges_no_test_info():
    dictWorkflow = {
        "listSteps": [{"sDirectory": "step1"}]
    }
    dictResult = _fdictBuildTestFileChanges(dictWorkflow, {"testFiles": {}})
    assert dictResult == {}


# ---- pipelineRunner: _fdictBuildWorkflowVars ----

def test_fdictBuildWorkflowVars_with_values():
    dictWorkflow = {
        "sPlotDirectory": "Figures",
        "sFigureType": "png",
    }
    dictVars = _fdictBuildWorkflowVars(dictWorkflow)
    assert dictVars["sPlotDirectory"] == "Figures"
    assert dictVars["sFigureType"] == "png"


def test_fdictBuildWorkflowVars_defaults():
    dictVars = _fdictBuildWorkflowVars({})
    assert dictVars["sPlotDirectory"] == "Plot"
    assert dictVars["sFigureType"] == "pdf"


def test_fdictBuildWorkflowVars_partial_defaults():
    dictWorkflow = {"sPlotDirectory": "Output"}
    dictVars = _fdictBuildWorkflowVars(dictWorkflow)
    assert dictVars["sPlotDirectory"] == "Output"
    assert dictVars["sFigureType"] == "pdf"


def test_fdictBuildWorkflowVars_extra_keys_ignored():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "sWorkflowName": "MyWorkflow",
        "listSteps": [],
    }
    dictVars = _fdictBuildWorkflowVars(dictWorkflow)
    assert set(dictVars.keys()) == {
        "sPlotDirectory", "sFigureType", "sRepoRoot",
    }
    assert "sWorkflowName" not in dictVars


def test_fdictBuildWorkflowVars_includes_repo_root():
    dictWorkflow = {"sProjectRepoPath": "/workspace/Proj"}
    dictVars = _fdictBuildWorkflowVars(dictWorkflow)
    assert dictVars["sRepoRoot"] == "/workspace/Proj"


# ---- _flistResolveTestCommands (pipelineRunner) ----


def test_flistResolveTestCommandsRunner_with_dictTests():
    dictStep = {
        "dictTests": {
            "dictIntegrity": {
                "saCommands": ["pytest test_integrity.py"],
            },
            "dictQualitative": {
                "saCommands": ["pytest test_qualitative.py"],
            },
        },
    }
    listResult = flistResolveTestCommandsRunner(dictStep)
    assert "pytest test_qualitative.py" in listResult
    assert "pytest test_integrity.py" in listResult
    assert len(listResult) == 2


def test_flistResolveTestCommandsRunner_legacy_saTestCommands():
    dictStep = {
        "saTestCommands": ["pytest test_old.py -v", "pytest test_legacy.py"],
    }
    listResult = flistResolveTestCommandsRunner(dictStep)
    assert listResult == ["pytest test_old.py -v", "pytest test_legacy.py"]


def test_flistResolveTestCommandsRunner_empty_dictTests():
    dictStep = {"dictTests": {}}
    listResult = flistResolveTestCommandsRunner(dictStep)
    assert listResult == []


def test_flistResolveTestCommandsRunner_no_tests_at_all():
    dictStep = {"sName": "Step with no tests"}
    listResult = flistResolveTestCommandsRunner(dictStep)
    assert listResult == []


def test_flistResolveTestCommandsRunner_dictTests_takes_precedence():
    dictStep = {
        "dictTests": {
            "dictIntegrity": {
                "saCommands": ["pytest test_new.py"],
            },
        },
        "saTestCommands": ["pytest test_old.py"],
    }
    listResult = flistResolveTestCommandsRunner(dictStep)
    assert listResult == ["pytest test_new.py"]
    assert "pytest test_old.py" not in listResult


# ---- _flistResolveTestCommands (pipelineServer) ----


def test_flistResolveTestCommandsServer_with_dictTests():
    dictStep = {
        "dictTests": {
            "dictQuantitative": {
                "saCommands": ["pytest test_quant.py"],
            },
        },
    }
    listResult = flistResolveTestCommandsServer(dictStep)
    assert listResult == ["pytest test_quant.py"]


def test_flistResolveTestCommandsServer_legacy_saTestCommands():
    dictStep = {
        "saTestCommands": ["pytest test_legacy.py"],
    }
    listResult = flistResolveTestCommandsServer(dictStep)
    assert listResult == ["pytest test_legacy.py"]


def test_flistResolveTestCommandsServer_no_tests():
    dictStep = {}
    listResult = flistResolveTestCommandsServer(dictStep)
    assert listResult == []


def test_flistResolveTestCommandsServer_empty_dictTests():
    dictStep = {"dictTests": {}}
    listResult = flistResolveTestCommandsServer(dictStep)
    assert listResult == []
