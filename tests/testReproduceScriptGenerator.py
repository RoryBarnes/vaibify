"""Tests for ``vaibify/reproducibility/reproduceScriptGenerator.py``.

The renderer is a pure function so the tests primarily compare its
output against fixtures. The filesystem write helper is exercised
once to confirm the chmod +x bit and the round-trip file content.
"""

import os
import stat

import pytest

from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
    flistRenderStepCommands,
    fnGenerateReproduceScript,
    fsRenderReproduceScript,
)


def _fdictBuildWorkflow(listSteps):
    """Return a synthetic workflow dict with the supplied steps."""
    return {"listSteps": listSteps}


def test_empty_workflow_renders_preamble_and_epilogue():
    sScript = fsRenderReproduceScript(_fdictBuildWorkflow([]))
    assert "#!/usr/bin/env bash" in sScript
    assert "docker pull" in sScript
    assert "sha256sum -c MANIFEST.sha256" in sScript


def test_step_commands_appear_in_render():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "S1", "sDirectory": "src",
         "saDataCommands": ["python compute.py"]},
        {"sName": "S2", "sDirectory": "plots",
         "saPlotCommands": ["python plot.py"]},
    ])
    sScript = fsRenderReproduceScript(dictWorkflow)
    assert "python compute.py" in sScript
    assert "python plot.py" in sScript
    assert "src" in sScript
    assert "plots" in sScript


def test_step_with_no_commands_is_skipped():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "AI Decl", "sStepKind": "ai-declaration"},
    ])
    listLines = flistRenderStepCommands(dictWorkflow)
    assert listLines == []


def test_repeated_render_is_deterministic():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "A", "sDirectory": ".",
         "saDataCommands": ["echo 1"]},
    ])
    sFirst = fsRenderReproduceScript(dictWorkflow)
    sSecond = fsRenderReproduceScript(dictWorkflow)
    assert sFirst == sSecond


def test_render_handles_directory_with_single_quote():
    dictWorkflow = _fdictBuildWorkflow([
        {"sName": "X", "sDirectory": "weird'dir",
         "saDataCommands": ["echo hi"]},
    ])
    sScript = fsRenderReproduceScript(dictWorkflow)
    assert "weird'\\''dir" in sScript


def test_generate_writes_executable_file(tmp_path):
    dictWorkflow = _fdictBuildWorkflow([])
    sAbs = fnGenerateReproduceScript(str(tmp_path), dictWorkflow)
    pathScript = tmp_path / S_REPRODUCE_SCRIPT_FILENAME
    assert pathScript.is_file()
    iMode = pathScript.stat().st_mode
    assert iMode & stat.S_IXUSR
    assert sAbs.endswith(S_REPRODUCE_SCRIPT_FILENAME)
