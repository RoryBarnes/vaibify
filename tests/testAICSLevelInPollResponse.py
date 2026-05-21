"""Contract tests for ``iAICSLevel`` in the file-status poll response.

Every poll-tick response carries the integer level so the frontend
can apply ``body.aics-level-N`` without an extra round-trip. The
dashboard cannot honor "show me the current verification state"
without this key, so it is a wire-contract concern, not an
implementation detail.
"""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.routes.pipelineRoutes import _fdictFetchOutputStatus


def _fdictCommonPatches(dictCommon=None):
    """Return list of context managers stubbing every disk-touching helper."""
    listTargets = [
        "_flistCollectOutputPaths",
        "_fdictGetModTimes",
        "fdictCollectOutputPathsByStep",
        "fnCollectMarkerPathsByStep",
        "_fbCheckStaleUserVerification",
        "_flistDetectAndInvalidate",
        "_fdictComputeMaxMtimeByStep",
        "_fdictComputeMaxPlotMtimeByStep",
        "_fdictComputeMaxDataMtimeByStep",
        "_fdictComputeMarkerMtimeByStep",
        "_fdictComputeMaxTestSourceMtimeByStep",
        "_fdictComputeTestCategoryMtimes",
        "_fdictBuildScriptStatus",
    ]
    dictReturnsByTarget = dictCommon or {}
    listManagers = []
    for sTarget in listTargets:
        listManagers.append(patch(
            f"vaibify.gui.routes.pipelineRoutes.{sTarget}",
            return_value=dictReturnsByTarget.get(sTarget, {}),
        ))
    return listManagers


@pytest.mark.asyncio
async def test_poll_response_carries_iAICSLevel_zero_when_no_repo():
    """Workflow without sProjectRepoPath reads as L0 in the response."""
    dictWorkflow = {"listSteps": [], "sProjectRepoPath": ""}
    dictCtx = {
        "docker": MagicMock(),
        "save": MagicMock(),
        "paths": {},
    }
    listManagers = _fdictCommonPatches({
        "_flistCollectOutputPaths": [],
        "_fdictGetModTimes": {},
    })
    for mgr in listManagers:
        mgr.start()
    try:
        dictResult = await _fdictFetchOutputStatus(
            dictCtx, "cid1", dictWorkflow, {},
        )
    finally:
        for mgr in listManagers:
            mgr.stop()
    assert "iAICSLevel" in dictResult
    assert dictResult["iAICSLevel"] == 0


@pytest.mark.asyncio
async def test_poll_response_carries_iAICSLevel_one_when_all_green():
    """All-green workflow with a repo reads as L1 in the response."""
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/repo",
        "listSteps": [
            {
                "sName": "A", "sDirectory": "A",
                "dictVerification": {
                    "sUser": "passed",
                    "sUnitTest": "passed",
                    "sIntegrity": "passed",
                    "sQualitative": "passed",
                    "sQuantitative": "passed",
                },
            },
        ],
    }
    dictCtx = {
        "docker": MagicMock(),
        "save": MagicMock(),
        "paths": {},
    }
    listManagers = _fdictCommonPatches({
        "_flistCollectOutputPaths": [],
        "_fdictGetModTimes": {},
    })
    for mgr in listManagers:
        mgr.start()
    try:
        dictResult = await _fdictFetchOutputStatus(
            dictCtx, "cid1", dictWorkflow, {},
        )
    finally:
        for mgr in listManagers:
            mgr.stop()
    assert dictResult["iAICSLevel"] == 1
