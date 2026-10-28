"""A researcher's sign-off waits for the Level 1 crossing and nothing more.

The step save read the full PROOF ladder before the edit and again after
it, to detect one thing: a crossing of Level 1 that refreshes the
envelope and may auto-archive. Once a project is at Level 2 those
readings ran the Level 2 and 3 gates, which hash every published file
through the container, so each click of a researcher's badge waited
seconds for answers nothing read (researcher-reported, 2026-10-27).
"""

import logging

import pytest

from tests.testAgentUpdateStepAllowlist import (
    ClockedDockerDouble,
    S_CONTAINER_ID,
    ftBuildLanes,
)
from vaibify.reproducibility import levelGates


def _fnFailIfTheUpperLadderRuns(*args, **kwargs):
    raise AssertionError("the step save evaluated the Level 2 or 3 gate")


@pytest.mark.falsification
def testASignOffNeverEvaluatesTheLevelTwoOrThreeGate(monkeypatch, caplog):
    """The sign-off's level readings run with the upper gates rigged to fail.

    Level 1 is stubbed to PASS, or a full-ladder reading would stop at
    Level 1 on this fixture and never reach the rigged gates -- the
    mutation below would then survive. The workflow save's own level
    derivation, which still computes the whole ladder to refresh the
    cached ``iProofLevel``, is stubbed out because it is a separate
    question from the two readings under test here.

    Kills: computing either level reading through the full ladder
    (``fiProofLevel``) instead of ``fiComputeProofLevelUpToOne``.
    """
    from vaibify.gui import workflowManager
    clientBrowser, _clientAgent, _app = ftBuildLanes(ClockedDockerDouble)
    monkeypatch.setattr(levelGates, "fbAtLeastLevel1", lambda *a, **k: True)
    monkeypatch.setattr(levelGates, "fbAtLeastLevel2", _fnFailIfTheUpperLadderRuns)
    monkeypatch.setattr(levelGates, "fbAtLeastLevel3", _fnFailIfTheUpperLadderRuns)
    monkeypatch.setattr(
        workflowManager, "_fnDeriveProofLevel",
        lambda dictWorkflow, filesRepo, bHostProject: None)
    with caplog.at_level(logging.INFO, logger="vaibify.gui.routes.stepRoutes"):
        responseHttp = clientBrowser.put(
            f"/api/steps/{S_CONTAINER_ID}/0",
            json={"dictVerification": {"sUser": "passed"}},
        )
    assert responseHttp.status_code == 200, responseHttp.text
    assert any(
        "Step save for container=" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.falsification
@pytest.mark.parametrize(
    "bMarkers, bLevel1, bLevel2, bLevel3",
    [
        (True, True, True, True),
        (False, False, True, True),
        (False, True, False, False),
        (False, True, True, False),
        (False, True, True, True),
    ],
)
def testTheLevelOneReadingEqualsTheLadderCappedAtOne(
    monkeypatch, bMarkers, bLevel1, bLevel2, bLevel3,
):
    """``fiComputeProofLevelUpToOne`` is ``min(fiProofLevel, 1)``, case by case.

    Kills: dropping the remote-data-marker rule from the Level 1
    reading, which then reports Level 1 for a project the ladder holds
    at 0.
    """
    monkeypatch.setattr(levelGates, "fbAtLeastLevel1", lambda *a, **k: bLevel1)
    monkeypatch.setattr(levelGates, "fbAtLeastLevel2", lambda *a, **k: bLevel2)
    monkeypatch.setattr(levelGates, "fbAtLeastLevel3", lambda *a, **k: bLevel3)
    dictWorkflow = {"listSteps": []}
    if bMarkers:
        dictWorkflow["listUnresolvedRemoteDataMarkers"] = ["A01"]
    iLadder = levelGates.fiProofLevel(
        dictWorkflow, "/nonexistent-for-shape", bHostProject=False)
    assert levelGates.fiComputeProofLevelUpToOne(
        dictWorkflow, "/nonexistent-for-shape") == min(iLadder, 1)
