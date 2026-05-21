"""Tests for ``vaibify/reproducibility/reproduceScriptGenerator.py``.

The renderer is a pure function so the tests primarily compare its
output against fixtures. The write helper now routes through a
docker connection because ``sProjectRepo`` is a container path; the
host filesystem must NEVER receive ``reproduce.sh`` even when a path
collision exists.
"""

import os

import pytest

from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
    flistRenderStepCommands,
    fnGenerateReproduceScript,
    fsRenderReproduceScript,
)


class _FakeDockerConnection:
    """Capture container-side write + chmod calls in dict form."""

    def __init__(self):
        self.dictWritten = {}
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sFilePath, baContent):
        self.dictWritten[(sContainerId, sFilePath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return (0, "")


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


def test_generate_writes_into_container_not_host(tmp_path):
    """The script must land in the container; never on the host."""
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    sContainerRepo = "/workspace/foo"
    sReturned = fnGenerateReproduceScript(
        sContainerRepo, dictWorkflow,
        connectionDocker=fakeConnection,
        sContainerId="cid-xyz",
    )
    sExpectedPath = sContainerRepo + "/" + S_REPRODUCE_SCRIPT_FILENAME
    assert sReturned == sExpectedPath
    assert ("cid-xyz", sExpectedPath) in fakeConnection.dictWritten
    baContent = fakeConnection.dictWritten[("cid-xyz", sExpectedPath)]
    assert b"#!/usr/bin/env bash" in baContent


def test_generate_chmods_inside_container(tmp_path):
    """Executable bits must be set via docker exec, not host chmod."""
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    fnGenerateReproduceScript(
        "/workspace/foo", dictWorkflow,
        connectionDocker=fakeConnection,
        sContainerId="cid-xyz",
    )
    listChmodCommands = [
        sCommand
        for _, sCommand in fakeConnection.listCommands
        if "chmod" in sCommand
    ]
    assert listChmodCommands
    assert "a+x" in listChmodCommands[0]


def test_generate_never_writes_to_host_at_container_path(tmp_path):
    """Regression: never create a host file at /workspace/<...>."""
    sHostShadow = "/workspace/foo/" + S_REPRODUCE_SCRIPT_FILENAME
    bExistedBefore = os.path.exists(sHostShadow)
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    fnGenerateReproduceScript(
        "/workspace/foo", dictWorkflow,
        connectionDocker=fakeConnection,
        sContainerId="cid-xyz",
    )
    bExistsAfter = os.path.exists(sHostShadow)
    assert bExistsAfter == bExistedBefore


def test_generate_refuses_when_docker_connection_missing():
    """Caller must provide a docker connection — no host fallback."""
    dictWorkflow = _fdictBuildWorkflow([])
    with pytest.raises(ValueError):
        fnGenerateReproduceScript("/workspace/foo", dictWorkflow)


def test_generate_refuses_when_container_id_empty():
    """Empty sContainerId must be rejected, not silently fall back."""
    dictWorkflow = _fdictBuildWorkflow([])
    fakeConnection = _FakeDockerConnection()
    with pytest.raises(ValueError):
        fnGenerateReproduceScript(
            "/workspace/foo", dictWorkflow,
            connectionDocker=fakeConnection,
            sContainerId="",
        )
