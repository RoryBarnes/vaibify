"""Extended tests for vaibify.gui.testGenerator."""

import pytest
from unittest.mock import MagicMock

from vaibify.gui.testGenerator import (
    _fsResolvePath,
    fsParseGeneratedCode,
    fsBuildPrompt,
    fsBuildStepContext,
)


# -----------------------------------------------------------------------
# _fsResolvePath
# -----------------------------------------------------------------------


def test_fsResolvePath_absolute_path_unchanged():
    sResult = _fsResolvePath("/abs/script.py", "/workspace")
    assert sResult == "/abs/script.py"


def test_fsResolvePath_relative_joined():
    sResult = _fsResolvePath("script.py", "/workspace/step1")
    assert sResult == "/workspace/step1/script.py"


def test_fsResolvePath_empty_directory():
    sResult = _fsResolvePath("script.py", "")
    assert sResult == "script.py"


def test_fsResolvePath_nested_relative():
    sResult = _fsResolvePath("sub/dir/script.py", "/workspace")
    assert sResult == "/workspace/sub/dir/script.py"


# -----------------------------------------------------------------------
# fsParseGeneratedCode — edge cases
# -----------------------------------------------------------------------


def test_fsParseGeneratedCode_empty_string():
    assert fsParseGeneratedCode("") == ""


def test_fsParseGeneratedCode_whitespace_only():
    assert fsParseGeneratedCode("   \n  ") == ""


def test_fsParseGeneratedCode_multiple_fences():
    sInput = (
        "Some text\n"
        "```python\nimport os\n```\n"
        "More text\n"
        "```python\nimport sys\n```"
    )
    sResult = fsParseGeneratedCode(sInput)
    assert sResult == "import os"


def test_fsParseGeneratedCode_no_language_specifier():
    sInput = "```\nimport os\nprint(os.getcwd())\n```"
    sResult = fsParseGeneratedCode(sInput)
    assert "import os" in sResult


def test_fsParseGeneratedCode_plain_code_returned():
    sInput = "import pytest\n\ndef test_foo():\n    pass"
    sResult = fsParseGeneratedCode(sInput)
    assert sResult == sInput


# -----------------------------------------------------------------------
# fsBuildPrompt — various step configs
# -----------------------------------------------------------------------


def test_fsBuildPrompt_no_data_commands():
    dictStep = {"saDataFiles": ["data.npy"]}
    sPrompt = fsBuildPrompt(
        "/workspace", dictStep, "scripts", "previews")
    assert "(none)" in sPrompt
    assert "data.npy" in sPrompt


def test_fsBuildPrompt_no_data_files():
    dictStep = {"saDataCommands": ["python run.py"]}
    sPrompt = fsBuildPrompt(
        "/workspace", dictStep, "scripts", "previews")
    assert "(none)" in sPrompt
    assert "python run.py" in sPrompt


def test_fsBuildPrompt_includes_script_contents():
    dictStep = {
        "saDataCommands": ["python run.py"],
        "saDataFiles": ["out.npy"],
    }
    sPrompt = fsBuildPrompt(
        "/workspace", dictStep,
        "def fnAnalyze(): pass", "shape=(100,)")
    assert "def fnAnalyze(): pass" in sPrompt
    assert "shape=(100,)" in sPrompt


def test_fsBuildPrompt_includes_directory():
    dictStep = {}
    sPrompt = fsBuildPrompt(
        "/workspace/myStep", dictStep, "", "")
    assert "/workspace/myStep" in sPrompt


# -----------------------------------------------------------------------
# fsBuildStepContext — with mocked Docker
# -----------------------------------------------------------------------


def _fMockConnection():
    """Return a mock DockerConnection."""
    mockConn = MagicMock()
    mockConn.fbaFetchFile.return_value = (
        b"import numpy as np\nprint('hello')"
    )
    mockConn.ftResultExecuteCommand.return_value = (
        0, "shape=(10,) dtype=float64"
    )
    return mockConn


def test_fsBuildStepContext_reads_scripts():
    mockConn = _fMockConnection()
    dictStep = {
        "sDirectory": "/workspace/step1",
        "saDataCommands": ["python analyze.py"],
        "saDataFiles": ["output.npy"],
    }
    sScripts, sPreviews = fsBuildStepContext(
        mockConn, "cid123", dictStep, {})
    assert "import numpy" in sScripts
    assert "shape=" in sPreviews


def test_fsBuildStepContext_no_scripts():
    mockConn = _fMockConnection()
    dictStep = {
        "sDirectory": "/workspace",
        "saDataCommands": [],
        "saDataFiles": [],
    }
    sScripts, sPreviews = fsBuildStepContext(
        mockConn, "cid123", dictStep, {})
    assert "no scripts" in sScripts
    assert "no data" in sPreviews


def test_fsBuildStepContext_handles_fetch_failure():
    mockConn = MagicMock()
    mockConn.fbaFetchFile.side_effect = Exception("not found")
    mockConn.ftResultExecuteCommand.return_value = (1, "")
    dictStep = {
        "sDirectory": "/workspace",
        "saDataCommands": ["python missing.py"],
        "saDataFiles": ["data.csv"],
    }
    sScripts, sPreviews = fsBuildStepContext(
        mockConn, "cid123", dictStep, {})
    assert "no scripts" in sScripts


# -----------------------------------------------------------------------
# fdictGenerateAllTests — with mocked Docker/LLM
# -----------------------------------------------------------------------


@pytest.fixture
def mockConnectionDocker():
    """Return a MagicMock Docker connection."""
    return MagicMock()


def test_fdictGenerateAllTests_creates_three_categories(
    mockConnectionDocker,
):
    """Verify fdictGenerateAllTests returns all three test categories."""
    from vaibify.gui.testGenerator import fdictGenerateAllTests

    sIntegrityCode = "import os\ndef test_exists(): assert True"
    sQualitativeCode = "import pytest\ndef test_label(): assert True"
    sQuantitativeJson = (
        '{"listStandards": [{"sName": "fTemp", "sDataFile": "out.csv",'
        ' "sAccessPath": "column:T,index:-1",'
        ' "fValue": 300.0, "sUnit": "K"}]}'
    )

    def fMockExecute(sContainerId, sCommand, sUser=None):
        if "mkdir" in sCommand:
            return (0, "")
        if "which claude" in sCommand:
            return (0, "/usr/bin/claude")
        if "claude --print" in sCommand:
            if "integrity" in sCommand.lower():
                return (0, sIntegrityCode)
            if "qualitative" in sCommand.lower():
                return (0, sQualitativeCode)
            if "quantitative" in sCommand.lower():
                return (0, sQuantitativeJson)
        return (0, "")

    mockConnectionDocker.ftResultExecuteCommand.side_effect = fMockExecute
    mockConnectionDocker.fnWriteFile = MagicMock()
    mockConnectionDocker.fbaFetchFile = MagicMock(return_value=b"")

    dictWorkflow = {
        "listSteps": [{
            "sName": "Process",
            "sDirectory": "/work/step01",
            "saDataCommands": ["python run.py"],
            "saDataFiles": ["output.csv"],
            "saTestCommands": [],
            "dictTests": {
                "dictQualitative": {
                    "saCommands": [], "sFilePath": "",
                },
                "dictQuantitative": {
                    "saCommands": [], "sFilePath": "",
                    "sStandardsPath": "",
                },
                "dictIntegrity": {
                    "saCommands": [], "sFilePath": "",
                },
                "listUserTests": [],
            },
        }],
    }
    dictResult = fdictGenerateAllTests(
        mockConnectionDocker, "container123", 0,
        dictWorkflow, {}, bUseApi=False,
    )
    assert "dictIntegrity" in dictResult
    assert "dictQualitative" in dictResult
    assert "dictQuantitative" in dictResult
    assert dictResult["dictIntegrity"]["sFilePath"].endswith(
        "test_integrity.py"
    )
    assert dictResult["dictQualitative"]["sFilePath"].endswith(
        "test_qualitative.py"
    )
    assert dictResult["dictQuantitative"]["sFilePath"].endswith(
        "test_quantitative.py"
    )
