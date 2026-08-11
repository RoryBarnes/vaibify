"""Falsification tests for the rename-cascade bypass guard.

AGENTS.md ("Traps" -> "A step's directory basename is a function of its
name") states: "never let a name change bypass the rename cascade
(``stepRename.py``): the generic update-step path 400s renames
precisely so the directory, marker, and manifest can never drift from
the name."

The guard that makes this true is
``stepRoutes._fnRejectContractBreakingUpdates``. Before these tests it
had no coverage of any kind — not a unit call, not a route exercise —
so deleting the call from ``fnUpdateStep`` would have left a fully
green suite while every generic step edit could silently rename a step
and strand its directory, its ``.vaibify/test_markers`` entry and its
manifest rows under the old slug.

The assertions go through a real HTTP PUT rather than calling the
helper directly, because the failure mode that shipped in this
repository before was a guard that existed and was not called.
"""

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vaibify.gui.routes import stepRoutes  # noqa: E402


pytestmark = pytest.mark.falsification


_S_CONTAINER_ID = "container-rename"
_S_ORIGINAL_NAME = "Corner Plot"
_S_ORIGINAL_DIRECTORY = "analysis/CornerPlot"


def _fdictBuildWorkflow():
    """Return a one-step workflow whose directory matches its name slug."""
    return {
        "sWorkflowName": "Rename Guard",
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": 1,
        "listSteps": [{
            "sName": _S_ORIGINAL_NAME,
            "sDirectory": _S_ORIGINAL_DIRECTORY,
            "bPlotOnly": False,
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": [],
            "saOutputDataFiles": [],
            "saTestCommands": ["pytest tests/"],
            "saPlotCommands": [],
            "saPlotFiles": [],
        }],
    }


@pytest.fixture
def tClientAndWorkflow(monkeypatch):
    """Return ``(clientHttp, dictWorkflow)`` with only stepRoutes wired.

    The carrier is stood down because update-step now runs its level
    read, save and auto-archive under a mode-(b) drain, which a bare
    ``FastAPI()`` cannot bind to an owner record. These tests are about
    the name<->directory contract the handler enforces BEFORE any of
    that, and prove nothing about the admission -- see
    ``tests/carrierStandDown.py``.
    """
    from tests.carrierStandDown import fnStandCarrierDown

    fnStandCarrierDown(monkeypatch, stepRoutes)
    dictWorkflow = _fdictBuildWorkflow()
    dictContext = {
        "workflows": {_S_CONTAINER_ID: dictWorkflow},
        "require": lambda *aArgs: None,
        "save": lambda sContainerId, dictWorkflowIn: None,
        "variables": lambda sContainerId: {},
        "docker": None,
    }
    application = FastAPI()
    stepRoutes.fnRegisterAll(application, dictContext)
    return TestClient(application), dictWorkflow


def _fresponseUpdateStep(clientHttp, dictUpdates):
    """Issue the generic update-step PUT with the given field updates."""
    return clientHttp.put(
        f"/api/steps/{_S_CONTAINER_ID}/0", json=dictUpdates,
    )


def testGenericStepUpdateRefusesARename(tClientAndWorkflow):
    """A name change through the generic edit path is refused with 400.

    Kills: deleting the ``_fnRejectContractBreakingUpdates(...)`` call
    from ``stepRoutes.fnUpdateStep`` — the rename would be applied
    while the directory, the test marker and the manifest stayed under
    the old slug, which is the legacy-mismatch state AGENTS.md marks as
    a red error requiring the align-step-directories migration.
    """
    clientHttp, dictWorkflow = tClientAndWorkflow
    responseHttp = _fresponseUpdateStep(clientHttp, {"sName": "Corner Grid"})
    assert responseHttp.status_code == 400, (
        "the generic update-step path must 400 a rename and send the "
        f"caller to the rename cascade; got {responseHttp.status_code}"
    )
    assert "rename" in responseHttp.json()["detail"].lower()
    assert dictWorkflow["listSteps"][0]["sName"] == _S_ORIGINAL_NAME, (
        "a refused rename must leave the step untouched"
    )


def testGenericStepUpdateRefusesADirectoryOffTheSlug(tClientAndWorkflow):
    """A directory whose final component is not the name's slug is refused.

    Kills: relaxing the basename comparison in
    ``_fnRejectContractBreakingUpdates`` from
    ``posixpath.basename(sDirectory) != sSlug`` to a containment test —
    ``analysis/Corner`` would then pass against the slug ``CornerPlot``
    and the directory would drift out from under the step name by the
    back door the rename guard exists to close.
    """
    clientHttp, dictWorkflow = tClientAndWorkflow
    responseHttp = _fresponseUpdateStep(
        clientHttp, {"sDirectory": "analysis/Corner"},
    )
    assert responseHttp.status_code == 400, (
        "only the parent path is free; the final component is a "
        f"function of the step name. Got {responseHttp.status_code}"
    )
    assert "CornerPlot" in responseHttp.json()["detail"], (
        "the refusal must name the required slug so the researcher can act"
    )
    assert (
        dictWorkflow["listSteps"][0]["sDirectory"] == _S_ORIGINAL_DIRECTORY
    )


def testGenericStepUpdateStillMovesTheParentPath(tClientAndWorkflow):
    """The guard discriminates: a parent-path move is still allowed.

    Kills: widening the refusal in ``_fnRejectContractBreakingUpdates``
    to reject every ``sDirectory`` edit — the documented contract frees
    the parent path, and a guard that refuses everything would block
    legitimate reorganisation while looking like correct enforcement.
    """
    clientHttp, dictWorkflow = tClientAndWorkflow
    responseHttp = _fresponseUpdateStep(
        clientHttp, {"sDirectory": "systems/CornerPlot"},
    )
    assert responseHttp.status_code == 200, (
        "moving a step under a new parent while keeping the slug is "
        f"legal; got {responseHttp.status_code} {responseHttp.text}"
    )
    assert (
        dictWorkflow["listSteps"][0]["sDirectory"] == "systems/CornerPlot"
    )
