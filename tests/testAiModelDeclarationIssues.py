"""The AI models row's reasons and the gate's verdict never disagree.

The row renders ``listAiModelDeclarationIssues`` beside the light that
``fbWorkflowDeclaresAiModels`` sets. A row that went red with no reason,
or listed a reason under a green light, would contradict itself -- the
failure the dashboard-state-honesty skill exists to prevent. So the two
are checked together over every shape a declaration list can take.
"""

import pytest

from vaibify.gui.routes.pipelineRoutes import (
    _flistDescribeAiModelDeclarationIssues,
)
from vaibify.reproducibility.replayGate import fbWorkflowDeclaresAiModels


def _fdictWorkflow(listModels):
    return {"dictAiProvenance": {"listDeclaredModels": listModels}}


DICT_COMPLETE_MODEL = {
    "sVendor": "Example", "sModelId": "model-a",
    "sUseStartDate": "2026-01-01", "sUseEndDate": "2026-02-01",
}


@pytest.mark.falsification
@pytest.mark.parametrize("listModels", [
    [],
    [DICT_COMPLETE_MODEL],
    [dict(DICT_COMPLETE_MODEL, sUseEndDate="")],
    [DICT_COMPLETE_MODEL, dict(DICT_COMPLETE_MODEL, bOpenWeights=True)],
])
def test_the_reasons_are_empty_exactly_when_the_gate_passes(listModels):
    """Kills: dropping a declaration's gaps from the reasons."""
    dictWorkflow = _fdictWorkflow(listModels)
    listIssues = _flistDescribeAiModelDeclarationIssues(dictWorkflow)
    assert (listIssues == []) is fbWorkflowDeclaresAiModels(dictWorkflow), (
        listIssues
    )


def test_a_reason_names_the_model_and_its_missing_fields_in_words():
    listIssues = _flistDescribeAiModelDeclarationIssues(_fdictWorkflow([
        dict(DICT_COMPLETE_MODEL, bOpenWeights=True),
    ]))
    assert listIssues == [
        "Example / model-a is missing its weights source, weights "
        "revision hash.",
    ]
