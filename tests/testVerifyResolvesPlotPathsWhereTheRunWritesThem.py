"""Verify must look for a templated plot where the run wrote it.

``{sPlotDirectory}/fig.{sFigureType}`` is the shipped template's own
form. The run resolves it from the global variables: the plot directory
joined onto the repository root and the figure type lowercased. Verify
built its variables a different way -- the directory left relative, the
figure type as typed -- so a relative result was then joined under the
STEP directory and a figure type spelled ``PDF`` never matched the
``.pdf`` the run produced. The template's own figure reported Missing.

The container double answers "present" only for the path the run wrote,
so Verify passes only by looking exactly there.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from vaibify.gui import pipelineRunner

S_REPO = "/workspace/repo"
S_WORKFLOW_DIRECTORY = S_REPO + "/.vaibify/projects"
S_WRITTEN_BY_THE_RUN = S_REPO + "/Plot/fig.pdf"


def _fdictWorkflow(sFigureType="PDF"):
    return {
        "sProjectRepoPath": S_REPO,
        "sPlotDirectory": "Plot",
        "sFigureType": sFigureType,
        "listSteps": [{
            "sName": "Plot", "sDirectory": "stepOne",
            "saDataCommands": [], "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"],
            "saOutputDataFiles": [],
        }],
    }


def _fmockDockerHoldingOnly(sPresentPath):
    mockDocker = MagicMock()
    mockDocker.flistContainerPathsExist.side_effect = (
        lambda sContainerId, listPaths: [
            sPath == sPresentPath for sPath in listPaths])
    return mockDocker


@pytest.mark.falsification
def testATemplatedPlotIsLookedForWhereTheRunWroteIt():
    """Kills: pipelineRunner._fbVerifyStepList: the run's variable builder
    `_fdictBuildVariables(dictWorkflow, sWorkdir)` replaced by the
    verify-only `_fdictBuildWorkflowVars(dictWorkflow)`."""
    mockDocker = _fmockDockerHoldingOnly(S_WRITTEN_BY_THE_RUN)
    fnStatus = AsyncMock()
    iExitCode = asyncio.run(pipelineRunner.fiVerifyOnly(
        mockDocker, "cid", _fdictWorkflow(),
        S_WORKFLOW_DIRECTORY + "/project.json", S_WORKFLOW_DIRECTORY,
        fnStatus,
    ))
    assert iExitCode == 0, [
        c.args[0].get("sLine") for c in fnStatus.call_args_list
        if c.args and isinstance(c.args[0], dict)
    ]


def testAFigureNeverWrittenStillReportsMissing():
    """The complement: the fix must not make Verify pass everything."""
    mockDocker = _fmockDockerHoldingOnly("/somewhere/else.pdf")
    iExitCode = asyncio.run(pipelineRunner.fiVerifyOnly(
        mockDocker, "cid", _fdictWorkflow(),
        S_WORKFLOW_DIRECTORY + "/project.json", S_WORKFLOW_DIRECTORY,
        AsyncMock(),
    ))
    assert iExitCode == 1


def testVerifyAndTheRunResolveThePlotDirectoryTheSameWay():
    dictVerify = pipelineRunner._fdictBuildVariables(
        _fdictWorkflow(), S_WORKFLOW_DIRECTORY)
    assert dictVerify["sPlotDirectory"] == S_REPO + "/Plot"
    assert dictVerify["sFigureType"] == "pdf"
