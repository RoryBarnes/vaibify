"""A step field no run path executes must be refused, not accepted.

``saSetupCommands`` and a step's own ``saCommands`` were validated,
dependency-graphed and listed among the commands "a run executes", but
the runner executes only ``saDataCommands`` and ``saPlotCommands``
(tests are a separate action). A step whose work lived in the other two
lists therefore ran nothing and still passed, and every output it
declared kept whatever bytes it already had.

Refusing at the load boundary is the honest repair: a project that
carries such a field is told which step, which field, and where the
commands belong, instead of being run as though they were honoured.
"""

import json
from unittest.mock import MagicMock

import pytest

from vaibify.gui import workflowManager


def _fdictWorkflow(**dictStepExtra):
    dictStep = {
        "sName": "Simulate", "sDirectory": "sim",
        "saPlotCommands": ["python plot.py"], "saPlotFiles": ["f.pdf"],
    }
    dictStep.update(dictStepExtra)
    return {"sPlotDirectory": "Plot", "listSteps": [dictStep]}


def _fdictLoad(dictWorkflow):
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = json.dumps(
        dictWorkflow).encode("utf-8")
    return workflowManager.fdictLoadWorkflowFromContainer(
        mockDocker, "containerId",
        "/workspace/repo/.vaibify/projects/project.json",
    )


@pytest.mark.falsification
@pytest.mark.parametrize("sField", ["saSetupCommands", "saCommands"])
def testAStepCarryingACommandListNoRunExecutesIsRefusedByName(sField):
    """Kills: workflowManager.fsDescribeValidationFailure: the unexecuted
    command-field check removed."""
    dictWorkflow = _fdictWorkflow(**{sField: ["python hidden.py"]})
    with pytest.raises(ValueError) as errorRaised:
        _fdictLoad(dictWorkflow)
    sMessage = str(errorRaised.value)
    assert sField in sMessage
    assert "Simulate" in sMessage or "Step01" in sMessage
    assert "saDataCommands" in sMessage


@pytest.mark.parametrize("sField", ["saSetupCommands", "saCommands"])
def testAnEmptyListIsStillAccepted(sField):
    """Empty is the shape older files and the editors write; it executes
    nothing and claims nothing."""
    dictLoaded = _fdictLoad(_fdictWorkflow(**{sField: []}))
    assert dictLoaded["listSteps"][0]["sName"] == "Simulate"


def testTheExecutedListsAreStillAccepted():
    dictLoaded = _fdictLoad(_fdictWorkflow(saDataCommands=["python go.py"]))
    assert dictLoaded["listSteps"][0]["saDataCommands"] == ["python go.py"]


def testTheDescriptionNamesTheStepWhenAskedDirectly():
    sFailure = workflowManager.fsDescribeValidationFailure(
        _fdictWorkflow(saCommands=["true"]))
    assert "saCommands" in sFailure
