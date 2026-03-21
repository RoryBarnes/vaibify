"""Tests for pure functions in vaibify.gui.testGenerator."""

from vaibify.gui.testGenerator import (
    fsParseGeneratedCode,
    fsTestFilePath,
    fsBuildPrompt,
)


def test_fsParseGeneratedCode_strips_fences():
    sInput = "```python\nimport pytest\n```"
    assert fsParseGeneratedCode(sInput) == "import pytest"


def test_fsParseGeneratedCode_no_fences():
    sInput = "import pytest\ndef test_foo(): pass"
    assert fsParseGeneratedCode(sInput) == sInput


def test_fsParseGeneratedCode_triple_backtick_only():
    sInput = "```\ncode here\n```"
    assert fsParseGeneratedCode(sInput) == "code here"


def test_fsTestFilePath_index_zero():
    sPath = fsTestFilePath("/workspace/dir", 0)
    assert "test_step01" in sPath
    assert sPath.endswith(".py")


def test_fsTestFilePath_index_nine():
    sPath = fsTestFilePath("/workspace/dir", 9)
    assert "test_step10" in sPath


def test_fsBuildPrompt_includes_directory():
    dictStep = {
        "saDataCommands": ["python analysis.py"],
        "saDataFiles": ["data.npy"],
    }
    sPrompt = fsBuildPrompt(
        "/workspace/dir", dictStep,
        "script content here", "",
    )
    assert "/workspace/dir" in sPrompt
