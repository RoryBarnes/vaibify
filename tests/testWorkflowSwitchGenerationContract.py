"""A superseded workflow load must not overwrite the active workflow.

Selecting workflow B while A is still loading must not let A's late result
land on B (the double-click / switch-during-load race). fnSelectWorkflow
bumps a generation counter and drops any load or refresh whose generation
is no longer current before it applies the result.

JavaScript is not executed by the Python suite; these are structural
assertions in the established frontend-contract pattern, and the behaviour
is additionally exercised in the browser lane.
"""

import os

_S_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadManager():
    sPath = os.path.join(_S_STATIC_DIR, "scriptWorkflowManager.js")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsFunctionBody(sSource, sSignature):
    iStart = sSource.find(sSignature)
    assert iStart != -1, sSignature + " missing"
    iNext = sSource.find("\n    async function ", iStart + 1)
    iPlain = sSource.find("\n    function ", iStart + 1)
    iEnd = min(x for x in (iNext, iPlain, len(sSource)) if x != -1)
    return sSource[iStart:iEnd]


def test_select_workflow_guards_on_a_generation():
    """fnSelectWorkflow bumps the generation and checks it after the await."""
    sBody = _fsFunctionBody(
        _fsReadManager(), "async function fnSelectWorkflow(",
    )
    iBump = sBody.find("_iWorkflowGeneration += 1")
    iCapture = sBody.find("iThisGeneration = _iWorkflowGeneration")
    iAwait = sBody.find("await _fdictFetchWorkflow")
    iCheck = sBody.find("iThisGeneration !== _iWorkflowGeneration")
    iApply = sBody.find("fnActivateWorkflow")
    assert -1 < iBump < iCapture < iAwait < iCheck < iApply, (
        "the generation must be bumped and captured before the fetch, and "
        "re-checked after the await before the result is applied"
    )


def test_refresh_workflow_drops_a_superseded_result():
    """A workflow switch during a refresh supersedes and drops it."""
    sBody = _fsFunctionBody(
        _fsReadManager(), "async function fnRefreshWorkflow(",
    )
    iCapture = sBody.find("iThisGeneration = _iWorkflowGeneration")
    iAwait = sBody.find("await _fdictFetchWorkflow")
    iCheck = sBody.find("iThisGeneration !== _iWorkflowGeneration")
    iApply = sBody.find("fnRefreshWorkflowData")
    assert -1 < iCapture < iAwait < iCheck < iApply, (
        "fnRefreshWorkflow must capture the generation before the fetch "
        "and drop the result if a switch superseded it"
    )
