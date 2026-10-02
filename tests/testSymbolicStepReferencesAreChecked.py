"""Symbolic cross-step references get the same checks the positional form had.

The pre-step dependency check matched only the deprecated ``{StepNN.x}``
form, so for a migrated workflow it never fired; two outputs sharing a
basename in sibling directories collapsed onto one token; an id outside
the kebab alphabet resolved in commands but produced no dependency edge;
and the dependency-graph cache ignored ``sStepId``.
"""

import asyncio

import pytest

from vaibify.gui import workflowManager
from vaibify.gui.pipelineRunner import _fsMissingDependencyFile
from vaibify.gui.pipelineUtils import fdictMapOutputTokenStems


class _ContainerWithoutFiles:
    def __init__(self):
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        return (1, "")


@pytest.mark.falsification
def testAMissingSymbolicDependencyStopsTheStepBeforeItRuns():
    """Kills: checking only the deprecated positional token form."""
    dictStep = {"saDataCommands": ["python use.py {step:fit-model.result}"]}
    dictVariables = {"step:fit-model.result": "/workspace/fit/result.json"}
    sMissing = asyncio.run(_fsMissingDependencyFile(
        _ContainerWithoutFiles(), "cid", dictStep, dictVariables))
    assert sMissing == "/workspace/fit/result.json"


@pytest.mark.falsification
def testSiblingDirectoriesWithTheSameBasenameKeepSeparateTokens():
    """Kills: resolving output/run1/x.json and output/run2/x.json to one."""
    dictTokens = fdictMapOutputTokenStems(
        ["output/run1/x.json", "output/run2/x.json"])
    assert sorted(dictTokens.values()) == [
        "output/run1/x.json", "output/run2/x.json"]
    assert len(dictTokens) == 2


def testTheLeadingSegmentQualifierIsKeptWhenItSuffices():
    dictTokens = fdictMapOutputTokenStems(
        ["EngleBarnes/output/Converged.json", "Other/output/Converged.json"])
    assert dictTokens == {
        "EngleBarnes_Converged": "EngleBarnes/output/Converged.json",
        "Other_Converged": "Other/output/Converged.json",
    }


@pytest.mark.falsification
def testAnIdOutsideTheKebabAlphabetStillYieldsADependencyEdge():
    """Kills: an id that resolves in commands but adds no graph edge."""
    setIndices = workflowManager.fsetExtractUpstreamIndices(
        "python use.py {step:Fit_Model.result}", {"Fit_Model": 3})
    assert setIndices == {3}


@pytest.mark.falsification
def testRenamingAStepIdInvalidatesTheDependencyGraphCache():
    """Kills: a cache key that ignores sStepId."""
    def fdictWorkflow(sStepId):
        return {"listSteps": [{"sStepId": sStepId, "sDirectory": "a"}]}

    assert workflowManager._fsWorkflowDepCacheKey(fdictWorkflow("one")) != (
        workflowManager._fsWorkflowDepCacheKey(fdictWorkflow("two")))
