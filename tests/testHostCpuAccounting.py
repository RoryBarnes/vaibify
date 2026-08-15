"""Host CPU accounting: the ``os.wait4`` reading reaches the run stats.

The container lane measures CPU via the GNU ``/usr/bin/time`` wrapper's
in-band marker line; the host lane measures at the ``os.wait4`` reap in
``HostConnection`` and carries it on ``ExecResult.fCpuSeconds``. These
tests pin the runner-side threading for the host lane with a REAL
registry file deciding the mode (the same file ``fbIsHostProject``
reads in production, per the epistemics rule), plus the
absent-propagation honesty: one absent reading makes the step's total
absent, never a partial sum and never a crash.

The container direction lives beside the container tests:
``testPipelineRunnerBranches.py::
test_ftRunSingleCommand_cpu_line_not_emitted_as_output`` asserts the
container branch still answers from the parsed marker line, and is
registered as the falsification killing a branch swap.
"""

import asyncio
import json

import pytest
from unittest.mock import MagicMock

from vaibify.config import registryManager
from vaibify.docker.dockerConnection import ExecResult
from vaibify.gui.pipelineRunner import (
    _ftRunSingleCommand,
    ftRunStepCommands,
)

S_HOST_PROJECT = "hostCpuProject"


@pytest.fixture()
def fixtureHostRegistry(tmp_path, monkeypatch):
    """Point the registry at a real file naming one host project."""
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({
        "listProjects": [{
            "sName": S_HOST_PROJECT,
            "sMode": "host",
            "sDirectory": str(tmp_path / "repo"),
        }],
    }))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )
    return pathRegistry


def _fMockHostConnection(fCpuSeconds):
    """Build a connection double answering execs with one CPU reading."""
    mockConnection = MagicMock()
    mockConnection.ftResultExecuteCommand.return_value = (0, "")

    def fnStreamingSideEffect(
        sContainerId, sCommand, fnEmitChunk, sWorkdir=None, sUser=None,
    ):
        return ExecResult(
            iExitCode=0, sStdout="", sStderr="",
            fCpuSeconds=fCpuSeconds,
        )

    mockConnection.ftRunInContainerStreamedWithChunks.side_effect = (
        fnStreamingSideEffect
    )
    return mockConnection


def _fnBuildCallback():
    """Return an async no-op status callback."""
    async def fnCallback(dictEvent):
        pass
    return fnCallback


@pytest.mark.falsification
def testAHostCommandsCpuReadingReachesTheRunner(fixtureHostRegistry):
    """The reap's fCpuSeconds is what the host branch returns.

    Kills: restoring the pre-wait4 host branch (``return
    (tExecResult.iExitCode, None)``), which recorded every host step's
    CPU as absent even though the connection now measures it.
    """
    mockConnection = _fMockHostConnection(fCpuSeconds=1.5)
    iExitCode, fCpu = asyncio.run(_ftRunSingleCommand(
        mockConnection, S_HOST_PROJECT, "cmd", "cmd", "work",
        _fnBuildCallback(),
    ))
    assert iExitCode == 0
    assert fCpu == pytest.approx(1.5)


@pytest.mark.falsification
def testAnAbsentReadingMakesTheStepTotalAbsentNotACrash(
    fixtureHostRegistry,
):
    """A step with an unmeasured command completes with an absent total.

    Kills: the blind add at the plot join — the shipped defect where a
    ``None`` reading raised ``TypeError`` AFTER the step's command had
    succeeded, so the researcher saw "Pipeline Failed" for completed
    work. An absent part must make the TOTAL absent (see
    ``_ffTotalCpuTime``), never a partial sum and never an exception.
    """
    mockConnection = _fMockHostConnection(fCpuSeconds=None)
    dictStep = {
        "sDirectory": "step",
        "saDataCommands": ["run data"],
        "saPlotCommands": ["run plot"],
        "bPlotOnly": False,
    }
    iExitCode, fCpu = asyncio.run(ftRunStepCommands(
        mockConnection, S_HOST_PROJECT, dictStep, "ws", {},
        _fnBuildCallback(),
    ))
    assert iExitCode == 0
    assert fCpu is None
