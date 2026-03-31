"""Tests to increase coverage of pipelineServer, workflowManager,
syncDispatcher, and pipelineRunner.

Targets uncovered lines identified via coverage reports. Uses mocks
for all Docker interactions.
"""

import json
import posixpath

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer
from vaibify.gui import workflowManager
from vaibify.gui.syncDispatcher import (
    _fsNormalizePath,
    _fdictParseHashOutput,
    fdictComputeAllScriptHashes,
    flistExtractAllScriptPaths,
)
from vaibify.gui.pipelineRunner import fnClearOutputModifiedFlags


# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

S_CONTAINER_ID = "boost123container"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW_MULTI = {
    "sWorkflowName": "Multi Step Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "listSteps": [
        {
            "sName": "Generate Data",
            "sDirectory": "/workspace/step1",
            "bPlotOnly": False,
            "bEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python generate.py"],
            "saDataFiles": ["data.csv"],
            "saTestCommands": ["python -m pytest test_step01.py -v"],
            "saPlotCommands": ["python plotData.py"],
            "saPlotFiles": ["{sPlotDirectory}/fig1.{sFigureType}"],
            "dictRunStats": {
                "dictInputHashes": {
                    "/workspace/step1/generate.py": "aaa111",
                    "/workspace/step1/plotData.py": "bbb222",
                },
            },
            "dictVerification": {
                "sUnitTest": "passed",
                "sUser": "untested",
            },
        },
        {
            "sName": "Analyze",
            "sDirectory": "/workspace/step2",
            "bPlotOnly": True,
            "bEnabled": True,
            "bInteractive": False,
            "saDataCommands": [],
            "saDataFiles": [],
            "saTestCommands": [],
            "saPlotCommands": [
                "python analyze.py {Step01.data}",
            ],
            "saPlotFiles": ["{sPlotDirectory}/fig2.{sFigureType}"],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested",
                "sUser": "untested",
            },
        },
        {
            "sName": "Interactive Review",
            "sDirectory": "/workspace/step3",
            "bPlotOnly": False,
            "bEnabled": True,
            "bInteractive": True,
            "saDataCommands": [],
            "saDataFiles": ["review.txt"],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested",
                "sUser": "untested",
            },
        },
    ],
}


# -----------------------------------------------------------------------
# Mock Docker for route tests
# -----------------------------------------------------------------------


class MockDockerBoost:
    """Mock Docker connection for coverage boost tests."""

    def __init__(self):
        self._dictFiles = {}
        self._iTestExitCode = 0
        self._sTestOutput = "All tests passed"

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": "boost1",
            "sName": "boost-container",
            "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(self, sContainerId, sCommand,
                                sWorkdir=None):
        if "test -d" in sCommand and ".vaibify" in sCommand:
            return (0, "")
        if "find" in sCommand and "workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "find" in sCommand and "logs" in sCommand:
            return (0, "")
        if "find" in sCommand:
            return (0, "")
        if "stat -c" in sCommand:
            return (0, "/workspace/step1/data.csv 1700000000\n"
                    "/workspace/step1/Plot/fig1.pdf 1700000001\n")
        if "cat" in sCommand and "pipeline_state" in sCommand:
            return (1, "")
        if "ps aux" in sCommand and "grep" not in sCommand:
            return (0, "0\n")
        if "ps aux" in sCommand:
            return (0, "0\n")
        if "test -f" in sCommand:
            return (0, "")
        if "rm -f" in sCommand:
            return (0, "")
        if "pytest" in sCommand:
            return (self._iTestExitCode, self._sTestOutput)
        if "python3 -c" in sCommand and "hashlib" in sCommand:
            return (0,
                    "/workspace/step1/generate.py aaa111\n"
                    "/workspace/step1/plotData.py bbb222\n")
        if "which claude" in sCommand:
            return (1, "")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW_MULTI).encode("utf-8")
        if sPath.endswith(".png"):
            return b"\x89PNG\r\n\x1a\n"
        if sPath.endswith(".pdf"):
            return b"%PDF-1.4"
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self._dictFiles[sPath] = baContent


    def fsExecCreate(self, sContainerId, sCommand=None,
                     sUser=None):
        return "exec-id-boost"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


def _fmockCreateDockerBoost():
    return MockDockerBoost()


@pytest.fixture
def clientHttp():
    """Create a TestClient with mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDockerBoost,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


def _fnConnectToContainer(clientHttp):
    """Connect and return the response dict."""
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200
    return responseHttp.json()


# =======================================================================
# pipelineServer: file status and invalidation (lines 1273-1389)
# =======================================================================


def test_file_status_returns_mod_times(clientHttp):
    """GET /api/pipeline/{id}/file-status returns dictModTimes."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/file-status"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "dictModTimes" in dictResult
    assert "dictScriptStatus" in dictResult


def test_file_status_returns_script_status(clientHttp):
    """Script status should be computed for each step."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/file-status"
    )
    dictResult = responseHttp.json()
    dictScriptStatus = dictResult["dictScriptStatus"]
    assert isinstance(dictScriptStatus, dict)


def test_file_status_invalidation_on_second_call(clientHttp):
    """Second file-status call should detect changes."""
    _fnConnectToContainer(clientHttp)
    clientHttp.get(f"/api/pipeline/{S_CONTAINER_ID}/file-status")
    responseHttp = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/file-status"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "dictInvalidatedSteps" in dictResult


# =======================================================================
# pipelineServer: _fdictFindChangedFiles (line 1280-1293)
# =======================================================================


def test_fdictFindChangedFiles_detects_changes():
    dictPathsByStep = {
        0: ["/workspace/a.dat", "/workspace/b.dat"],
        1: ["/workspace/c.dat"],
    }
    dictOldModTimes = {
        "/workspace/a.dat": "100",
        "/workspace/b.dat": "200",
        "/workspace/c.dat": "300",
    }
    dictNewModTimes = {
        "/workspace/a.dat": "100",
        "/workspace/b.dat": "999",
        "/workspace/c.dat": "300",
    }
    dictChanged = pipelineServer._fdictFindChangedFiles(
        dictPathsByStep, dictOldModTimes, dictNewModTimes,
    )
    assert 0 in dictChanged
    assert "/workspace/b.dat" in dictChanged[0]
    assert 1 not in dictChanged


def test_fdictFindChangedFiles_no_changes():
    dictPathsByStep = {0: ["/a.dat"]}
    dictModTimes = {"/a.dat": "100"}
    dictChanged = pipelineServer._fdictFindChangedFiles(
        dictPathsByStep, dictModTimes, dictModTimes,
    )
    assert dictChanged == {}


def test_fdictFindChangedFiles_new_file():
    dictPathsByStep = {0: ["/new.dat"]}
    dictOld = {}
    dictNew = {"/new.dat": "500"}
    dictChanged = pipelineServer._fdictFindChangedFiles(
        dictPathsByStep, dictOld, dictNew,
    )
    assert 0 in dictChanged


# =======================================================================
# pipelineServer: _fnInvalidateStepFiles (line 1296-1306)
# =======================================================================


def test_fnInvalidateStepFiles_marks_modified():
    dictStep = {
        "dictVerification": {
            "sUnitTest": "passed",
            "sUser": "untested",
        },
    }
    pipelineServer._fnInvalidateStepFiles(
        dictStep, ["/workspace/a.py"],
    )
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"
    assert "/workspace/a.py" in (
        dictStep["dictVerification"]["listModifiedFiles"]
    )


def test_fnInvalidateStepFiles_merges_existing():
    dictStep = {
        "dictVerification": {
            "sUnitTest": "untested",
            "listModifiedFiles": ["/workspace/old.py"],
        },
    }
    pipelineServer._fnInvalidateStepFiles(
        dictStep, ["/workspace/new.py"],
    )
    listModified = dictStep["dictVerification"]["listModifiedFiles"]
    assert "/workspace/old.py" in listModified
    assert "/workspace/new.py" in listModified


# =======================================================================
# pipelineServer: _fnInvalidateDownstreamStep (line 1308-1314)
# =======================================================================


def test_fnInvalidateDownstreamStep_sets_flag():
    dictStep = {
        "dictVerification": {
            "sUnitTest": "passed",
            "sUser": "verified",
        },
    }
    pipelineServer._fnInvalidateDownstreamStep(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"
    assert dictStep["dictVerification"]["bUpstreamModified"] is True


def test_fnInvalidateDownstreamStep_no_verification():
    dictStep = {}
    pipelineServer._fnInvalidateDownstreamStep(dictStep)
    assert dictStep["dictVerification"]["bUpstreamModified"] is True


# =======================================================================
# pipelineServer: _fdictBuildScriptStatus (lines 1317-1345)
# =======================================================================


def test_fdictBuildScriptStatus_unknown_no_hashes():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": [],
                "dictRunStats": {},
            },
        ],
    }
    dictResult = pipelineServer._fdictBuildScriptStatus(
        dictWorkflow, {},
    )
    assert dictResult[0] == "unknown"


def test_fdictBuildScriptStatus_unchanged():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": [],
                "dictRunStats": {
                    "dictInputHashes": {
                        "/workspace/step1/gen.py": "hash123",
                    },
                },
            },
        ],
    }
    dictCurrentHashes = {
        "/workspace/step1/gen.py": "hash123",
    }
    dictResult = pipelineServer._fdictBuildScriptStatus(
        dictWorkflow, dictCurrentHashes,
    )
    assert dictResult[0] == "unchanged"


def test_fdictBuildScriptStatus_modified():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": ["python plot.py"],
                "dictRunStats": {
                    "dictInputHashes": {
                        "/workspace/step1/gen.py": "hash123",
                        "/workspace/step1/plot.py": "hash456",
                    },
                },
            },
        ],
    }
    dictCurrentHashes = {
        "/workspace/step1/gen.py": "hash123",
        "/workspace/step1/plot.py": "CHANGED",
    }
    dictResult = pipelineServer._fdictBuildScriptStatus(
        dictWorkflow, dictCurrentHashes,
    )
    assert dictResult[0] == "modified"


def test_fdictBuildScriptStatus_missing_current():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": [],
                "dictRunStats": {
                    "dictInputHashes": {
                        "/workspace/step1/gen.py": "hash123",
                    },
                },
            },
        ],
    }
    dictResult = pipelineServer._fdictBuildScriptStatus(
        dictWorkflow, {},
    )
    assert dictResult[0] == "modified"


# =======================================================================
# pipelineServer: clean outputs skips interactive (lines 1457-1488)
# =======================================================================


def test_clean_outputs_skips_interactive(clientHttp):
    """Interactive steps should not have their files cleaned."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/pipeline/{S_CONTAINER_ID}/clean"
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


def test_clean_outputs_resets_verification(clientHttp):
    """Cleaning should reset dictRunStats and dictVerification."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/pipeline/{S_CONTAINER_ID}/clean"
    )
    assert responseHttp.status_code == 200
    responseSteps = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}"
    )
    listSteps = responseSteps.json()
    for dictStep in listSteps:
        assert dictStep.get("dictRunStats", {}) == {}
        dictVerify = dictStep.get("dictVerification", {})
        assert dictVerify.get("sUnitTest", "untested") == "untested"


# =======================================================================
# pipelineServer: figure check endpoint (lines 1055-1075)
# =======================================================================


def test_check_figure_head_found(clientHttp):
    """HEAD /api/figure should return 200 if file exists."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.head(
        f"/api/figure/{S_CONTAINER_ID}/Plot/fig.pdf"
    )
    assert responseHttp.status_code == 200


def test_check_figure_head_with_workdir(clientHttp):
    """HEAD /api/figure with sWorkdir param."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.head(
        f"/api/figure/{S_CONTAINER_ID}/fig.pdf",
        params={"sWorkdir": "/workspace/step1"},
    )
    assert responseHttp.status_code == 200


def test_check_figure_head_relative_workdir(clientHttp):
    """HEAD /api/figure with relative sWorkdir."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.head(
        f"/api/figure/{S_CONTAINER_ID}/fig.pdf",
        params={"sWorkdir": "step1"},
    )
    assert responseHttp.status_code == 200


# =======================================================================
# pipelineServer: figure serve endpoint (lines 1077-1093)
# =======================================================================


def test_serve_figure_returns_content(clientHttp):
    """GET /api/figure should return figure content."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/figure/{S_CONTAINER_ID}/Plot/fig.pdf"
    )
    assert responseHttp.status_code == 200
    assert responseHttp.headers["content-type"] == "application/pdf"


# =======================================================================
# pipelineServer: test run/save endpoints (lines 1672-1827)
# =======================================================================


def test_run_tests_success(clientHttp):
    """POST run-tests should return bPassed when tests pass."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/run-tests"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bPassed"] is True
    assert dictResult["iExitCode"] == 0


def test_run_tests_no_commands(clientHttp):
    """POST run-tests with no test commands should return 400."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/run-tests"
    )
    assert responseHttp.status_code == 400


def test_save_and_run_test_success(clientHttp):
    """POST save-and-run-test should write file and run."""
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sContent": "def test_example(): assert True",
        "sFilePath": "/workspace/step1/test_step01.py",
    }
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/save-and-run-test",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bPassed"] is True


def test_delete_generated_test(clientHttp):
    """DELETE generated-test should clear test commands."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.delete(
        f"/api/steps/{S_CONTAINER_ID}/0/generated-test"
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


def test_generate_test_no_claude(clientHttp):
    """POST generate-test without Claude should return bNeedsFallback."""
    _fnConnectToContainer(clientHttp)
    dictPayload = {"bUseApi": False, "bDeterministic": False}
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/generate-test",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bNeedsFallback"] is True


# =======================================================================
# pipelineServer: _fnClearDownstreamUpstreamFlags (lines 1818-1828)
# =======================================================================


def test_fnClearDownstreamUpstreamFlags():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Step1",
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saDataFiles": ["data.csv"],
                "saPlotCommands": [],
                "saPlotFiles": [],
                "saTestCommands": [],
                "dictVerification": {"sUnitTest": "passed"},
            },
            {
                "sName": "Step2",
                "sDirectory": "/workspace/step2",
                "saDataCommands": [
                    "python analyze.py {Step01.data}",
                ],
                "saDataFiles": [],
                "saPlotCommands": [],
                "saPlotFiles": [],
                "saTestCommands": [],
                "dictVerification": {
                    "sUnitTest": "untested",
                    "bUpstreamModified": True,
                },
            },
        ],
    }
    pipelineServer._fnClearDownstreamUpstreamFlags(dictWorkflow, 0)
    dictVerify = dictWorkflow["listSteps"][1]["dictVerification"]
    assert "bUpstreamModified" not in dictVerify


# =======================================================================
# pipelineServer: fbaFetchFigureWithFallback
# =======================================================================


def test_fbaFetchFigureWithFallback_primary_success():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"PNG_DATA"
    baResult = pipelineServer.fbaFetchFigureWithFallback(
        mockDocker, "cid", "/workspace/Plot/fig.png",
        "/workspace", "", "Plot/fig.png",
    )
    assert baResult == b"PNG_DATA"


def test_fbaFetchFigureWithFallback_fallback_workdir():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = [
        FileNotFoundError("nope"),
        b"FALLBACK_DATA",
    ]
    baResult = pipelineServer.fbaFetchFigureWithFallback(
        mockDocker, "cid", "/workspace/Plot/fig.png",
        "/workspace", "/workspace/step1", "fig.png",
    )
    assert baResult == b"FALLBACK_DATA"


def test_fbaFetchFigureWithFallback_relative_workdir():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = [
        FileNotFoundError("nope"),
        b"REL_DATA",
    ]
    baResult = pipelineServer.fbaFetchFigureWithFallback(
        mockDocker, "cid", "/workspace/Plot/fig.png",
        "/workspace", "step1", "fig.png",
    )
    assert baResult == b"REL_DATA"


def test_fbaFetchFigureWithFallback_no_workdir_raises():
    from fastapi import HTTPException
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = FileNotFoundError("nope")
    with pytest.raises(HTTPException):
        pipelineServer.fbaFetchFigureWithFallback(
            mockDocker, "cid", "/workspace/Plot/fig.png",
            "/workspace", "", "Plot/fig.png",
        )


# =======================================================================
# pipelineServer: fdictCollectOutputPathsByStep (lines 1230-1245)
# =======================================================================


def test_fdictCollectOutputPathsByStep_resolves():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataFiles": ["data.csv"],
                "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"],
            },
        ],
    }
    dictResult = pipelineServer.fdictCollectOutputPathsByStep(
        dictWorkflow,
    )
    assert 0 in dictResult
    assert "/workspace/step1/data.csv" in dictResult[0]
    assert "/workspace/step1/Plot/fig.pdf" in dictResult[0]


def test_fdictCollectOutputPathsByStep_with_vars():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataFiles": ["{sPlotDirectory}/out.dat"],
                "saPlotFiles": [],
            },
        ],
    }
    dictVars = {"sPlotDirectory": "Results"}
    dictResult = pipelineServer.fdictCollectOutputPathsByStep(
        dictWorkflow, dictVars,
    )
    assert "/workspace/step1/Results/out.dat" in dictResult[0]


# =======================================================================
# workflowManager: fdictBuildDownstreamMap (lines 582-616)
# =======================================================================


def test_fdictBuildDownstreamMap_linear_chain():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "A",
                "saDataCommands": [],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": ["data.csv"],
                "saPlotFiles": [],
            },
            {
                "sName": "B",
                "saDataCommands": [
                    "python analyze.py {Step01.data}",
                ],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": ["result.csv"],
                "saPlotFiles": [],
            },
            {
                "sName": "C",
                "saDataCommands": [
                    "python final.py {Step02.result}",
                ],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
        ],
    }
    dictDownstream = workflowManager.fdictBuildDownstreamMap(
        dictWorkflow,
    )
    assert 1 in dictDownstream[0]
    assert 2 in dictDownstream[0]
    assert 2 in dictDownstream[1]
    assert dictDownstream[2] == set()


def test_fdictBuildDownstreamMap_no_deps():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "A",
                "saDataCommands": [],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
            {
                "sName": "B",
                "saDataCommands": [],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
        ],
    }
    dictDownstream = workflowManager.fdictBuildDownstreamMap(
        dictWorkflow,
    )
    assert dictDownstream[0] == set()
    assert dictDownstream[1] == set()


def test_fdictBuildDirectDependencies():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "A",
                "saDataCommands": [],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": ["out.dat"],
                "saPlotFiles": [],
            },
            {
                "sName": "B",
                "saDataCommands": [
                    "python run.py {Step01.out}",
                ],
                "saPlotCommands": [],
                "saTestCommands": [],
                "saDataFiles": [],
                "saPlotFiles": [],
            },
        ],
    }
    dictDirect = workflowManager.fdictBuildDirectDependencies(
        dictWorkflow,
    )
    assert 1 in dictDirect.get(0, set())


# =======================================================================
# workflowManager: file category functions (lines 449-496)
# =======================================================================


def test_fsGetFileCategory_default():
    dictStep = {}
    assert workflowManager.fsGetFileCategory(
        dictStep, "file.pdf"
    ) == "archive"


def test_fsGetFileCategory_plot_category():
    dictStep = {
        "dictPlotFileCategories": {"fig.pdf": "supporting"},
    }
    assert workflowManager.fsGetFileCategory(
        dictStep, "fig.pdf"
    ) == "supporting"


def test_fsGetFileCategory_data_category():
    dictStep = {
        "dictDataFileCategories": {"data.csv": "supporting"},
    }
    assert workflowManager.fsGetFileCategory(
        dictStep, "data.csv"
    ) == "supporting"


def test_fsGetPlotCategory_default():
    dictStep = {}
    assert workflowManager.fsGetPlotCategory(
        dictStep, "fig.pdf"
    ) == "archive"


def test_fsGetPlotCategory_custom():
    dictStep = {
        "dictPlotFileCategories": {"fig.pdf": "supporting"},
    }
    assert workflowManager.fsGetPlotCategory(
        dictStep, "fig.pdf"
    ) == "supporting"


def test_flistCollectArchiveFiles():
    dictWorkflow = {
        "listSteps": [
            {
                "saPlotFiles": ["fig1.pdf", "fig2.pdf"],
                "dictPlotFileCategories": {
                    "fig2.pdf": "supporting",
                },
            },
        ],
    }
    listArchive = workflowManager.flistCollectArchiveFiles(
        dictWorkflow, "saPlotFiles",
    )
    assert "fig1.pdf" in listArchive
    assert "fig2.pdf" not in listArchive


def test_flistCollectArchivePlots():
    dictWorkflow = {
        "listSteps": [
            {"saPlotFiles": ["a.pdf", "b.pdf"]},
        ],
    }
    listPlots = workflowManager.flistCollectArchivePlots(dictWorkflow)
    assert len(listPlots) == 2


def test_flistCollectArchiveDataFiles():
    dictWorkflow = {
        "listSteps": [
            {"saDataFiles": ["data.csv"]},
        ],
    }
    listData = workflowManager.flistCollectArchiveDataFiles(
        dictWorkflow,
    )
    assert "data.csv" in listData


def test_flistCollectSupportingFiles():
    dictWorkflow = {
        "listSteps": [
            {
                "saPlotFiles": ["fig1.pdf", "fig2.pdf"],
                "dictPlotFileCategories": {
                    "fig2.pdf": "supporting",
                },
            },
        ],
    }
    listSupporting = workflowManager.flistCollectSupportingFiles(
        dictWorkflow, "saPlotFiles",
    )
    assert "fig2.pdf" in listSupporting
    assert "fig1.pdf" not in listSupporting


def test_flistCollectSupportingPlots():
    dictWorkflow = {
        "listSteps": [
            {
                "saPlotFiles": ["fig.pdf"],
                "dictPlotFileCategories": {
                    "fig.pdf": "supporting",
                },
            },
        ],
    }
    listSupporting = workflowManager.flistCollectSupportingPlots(
        dictWorkflow,
    )
    assert "fig.pdf" in listSupporting


def test_flistCollectSupportingDataFiles():
    dictWorkflow = {
        "listSteps": [
            {
                "saDataFiles": ["data.csv"],
                "dictDataFileCategories": {
                    "data.csv": "supporting",
                },
            },
        ],
    }
    listSupporting = workflowManager.flistCollectSupportingDataFiles(
        dictWorkflow,
    )
    assert "data.csv" in listSupporting


# =======================================================================
# workflowManager: fsetExtractUpstreamIndices (line 580-582)
# =======================================================================


def test_fsetExtractUpstreamIndices():
    setIndices = workflowManager.fsetExtractUpstreamIndices(
        "use {Step01.data} and {Step03.fig}"
    )
    assert 0 in setIndices
    assert 2 in setIndices


def test_fsetExtractUpstreamIndices_empty():
    setIndices = workflowManager.fsetExtractUpstreamIndices(
        "no references"
    )
    assert setIndices == set()


# =======================================================================
# workflowManager: fdictInitializeSyncEntry (line 571-577)
# =======================================================================


def test_fdictInitializeSyncEntry():
    dictEntry = workflowManager.fdictInitializeSyncEntry()
    assert dictEntry["bOverleaf"] is False
    assert dictEntry["sOverleafTimestamp"] == ""
    assert dictEntry["bGithub"] is False
    assert dictEntry["bZenodo"] is False


# =======================================================================
# workflowManager: _fsClassifyReference (lines 357-365)
# =======================================================================


def test_fsClassifyReference_beyond_last():
    sResult = workflowManager._fsClassifyReference(
        5, "Step05.data", 1, 3, {},
    )
    assert "beyond" in sResult


def test_fsClassifyReference_no_matching_output():
    sResult = workflowManager._fsClassifyReference(
        1, "Step01.missing", 2, 3, {},
    )
    assert "no matching" in sResult


def test_fsClassifyReference_circular():
    dictRegistry = {"Step02.data": 2}
    sResult = workflowManager._fsClassifyReference(
        2, "Step02.data", 1, 3, dictRegistry,
    )
    assert "circular" in sResult


def test_fsClassifyReference_valid():
    dictRegistry = {"Step01.data": 1}
    sResult = workflowManager._fsClassifyReference(
        1, "Step01.data", 2, 3, dictRegistry,
    )
    assert sResult == ""


# =======================================================================
# syncDispatcher: _fsNormalizePath (line 573-578)
# =======================================================================


def test_fsNormalizePath_absolute():
    sResult = _fsNormalizePath("/workspace/step1", "/abs/script.py")
    assert sResult == "/abs/script.py"


def test_fsNormalizePath_relative():
    sResult = _fsNormalizePath("/workspace/step1", "script.py")
    assert sResult == "/workspace/step1/script.py"


def test_fsNormalizePath_normalizes():
    sResult = _fsNormalizePath(
        "/workspace/step1", "../shared/script.py"
    )
    assert sResult == "/workspace/shared/script.py"


# =======================================================================
# syncDispatcher: _fdictParseHashOutput (lines 670-680)
# =======================================================================


def test_fdictParseHashOutput_normal():
    sOutput = (
        "/workspace/gen.py abc123\n"
        "/workspace/plot.py def456\n"
    )
    dictResult = _fdictParseHashOutput(sOutput)
    assert dictResult["/workspace/gen.py"] == "abc123"
    assert dictResult["/workspace/plot.py"] == "def456"


def test_fdictParseHashOutput_missing():
    sOutput = "/workspace/gen.py MISSING\n"
    dictResult = _fdictParseHashOutput(sOutput)
    assert "/workspace/gen.py" not in dictResult


def test_fdictParseHashOutput_empty():
    assert _fdictParseHashOutput("") == {}
    assert _fdictParseHashOutput(None) == {}


def test_fdictParseHashOutput_blank_lines():
    sOutput = "\n  \n/a.py hash1\n\n"
    dictResult = _fdictParseHashOutput(sOutput)
    assert dictResult["/a.py"] == "hash1"


# =======================================================================
# syncDispatcher: fdictComputeAllScriptHashes (lines 648-667)
# =======================================================================


def test_fdictComputeAllScriptHashes_success():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0, "/workspace/step1/gen.py abc123\n"
    )
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": [],
            },
        ],
    }
    dictResult = fdictComputeAllScriptHashes(
        mockDocker, "cid", dictWorkflow,
    )
    assert "/workspace/step1/gen.py" in dictResult


def test_fdictComputeAllScriptHashes_empty_workflow():
    mockDocker = MagicMock()
    dictWorkflow = {"listSteps": []}
    dictResult = fdictComputeAllScriptHashes(
        mockDocker, "cid", dictWorkflow,
    )
    assert dictResult == {}


def test_fdictComputeAllScriptHashes_exec_failure():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "error")
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": [],
            },
        ],
    }
    dictResult = fdictComputeAllScriptHashes(
        mockDocker, "cid", dictWorkflow,
    )
    assert dictResult == {}


# =======================================================================
# syncDispatcher: flistExtractAllScriptPaths (lines 625-645)
# =======================================================================


def test_flistExtractAllScriptPaths():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": ["python plot.py"],
            },
            {
                "sDirectory": "/workspace/step2",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": [],
            },
        ],
    }
    listPaths = flistExtractAllScriptPaths(dictWorkflow)
    assert "/workspace/step1/gen.py" in listPaths
    assert "/workspace/step1/plot.py" in listPaths
    assert "/workspace/step2/gen.py" in listPaths


def test_flistExtractAllScriptPaths_no_duplicates():
    dictWorkflow = {
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataCommands": ["python gen.py"],
                "saPlotCommands": ["python gen.py"],
            },
        ],
    }
    listPaths = flistExtractAllScriptPaths(dictWorkflow)
    iCount = listPaths.count("/workspace/step1/gen.py")
    assert iCount == 1


# =======================================================================
# pipelineRunner: fnClearOutputModifiedFlags (lines 312-319)
# =======================================================================


def test_fnClearOutputModifiedFlags_clears_all():
    dictWorkflow = {
        "listSteps": [
            {
                "dictVerification": {
                    "sUnitTest": "passed",
                    "bOutputModified": True,
                    "listModifiedFiles": ["/a.py"],
                    "bUpstreamModified": True,
                },
            },
            {
                "dictVerification": {
                    "sUnitTest": "untested",
                },
            },
        ],
    }
    fnClearOutputModifiedFlags(dictWorkflow)
    dictV0 = dictWorkflow["listSteps"][0]["dictVerification"]
    assert "bOutputModified" not in dictV0
    assert "listModifiedFiles" not in dictV0
    assert "bUpstreamModified" not in dictV0
    assert dictV0["sUnitTest"] == "passed"


def test_fnClearOutputModifiedFlags_empty():
    dictWorkflow = {"listSteps": []}
    fnClearOutputModifiedFlags(dictWorkflow)
    assert dictWorkflow["listSteps"] == []


def test_fnClearOutputModifiedFlags_no_verification():
    dictWorkflow = {
        "listSteps": [
            {},
            {"dictVerification": {}},
        ],
    }
    fnClearOutputModifiedFlags(dictWorkflow)
    assert dictWorkflow["listSteps"][0]["dictVerification"] == {}
    assert dictWorkflow["listSteps"][1]["dictVerification"] == {}


# =======================================================================
# workflowManager: fsCamelCaseDirectory (line 504-512)
# =======================================================================


def test_fsCamelCaseDirectory():
    assert workflowManager.fsCamelCaseDirectory(
        "Generate Data"
    ) == "GenerateData"


def test_fsCamelCaseDirectory_special_chars():
    assert workflowManager.fsCamelCaseDirectory(
        "step-1 result"
    ) == "Step1Result"


# =======================================================================
# workflowManager: flistExtractStepScripts (line 515-527)
# =======================================================================


def test_flistExtractStepScripts():
    dictStep = {
        "saDataCommands": ["python gen.py", "echo done"],
        "saPlotCommands": ["python plot.py"],
    }
    listScripts = workflowManager.flistExtractStepScripts(dictStep)
    assert "gen.py" in listScripts
    assert "plot.py" in listScripts


def test_flistExtractStepScripts_empty():
    dictStep = {}
    listScripts = workflowManager.flistExtractStepScripts(dictStep)
    assert listScripts == []
