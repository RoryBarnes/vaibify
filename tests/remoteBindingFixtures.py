"""Let a route test that is about something else satisfy the binding guard.

Since 2026-10-29 every token-bearing Overleaf and Zenodo route reads its
target from the host-side binding the researcher confirmed
(``vaibify/config/remoteBindings.py``) and refuses when ``project.json``
disagrees or no binding exists. The tests of the pushes, deposits and
mirrors predate that and are about THEIR behaviour; this fixture stands
in for a researcher who confirmed exactly what the test's ``project.json``
says (and the instance a promotion names), so those tests keep asserting
what they always asserted. It is imported explicitly by the modules that
need it, never applied globally, and ``tests/testRemoteBindings.py``
tests the guard itself with the real code, so the guard's own
falsification entries are killed there.
"""

import pytest
from fastapi import HTTPException

from vaibify.gui import remoteBindingContext


@pytest.fixture(autouse=True)
def fixtureBindingsFollowProjectJson(monkeypatch):
    """Answer every binding question with the project's own target."""

    def fsOverleafFromProject(dictCtx, sContainerId, dictWorkflow):
        del dictCtx, sContainerId
        sProjectId = str(dictWorkflow.get("sOverleafProjectId") or "")
        if not sProjectId:
            raise HTTPException(
                409, "No Overleaf project is bound to this project (no confirmed overleaf binding).",
            )
        return sProjectId

    def fdictZenodoFromProject(
        dictCtx, sContainerId, dictWorkflow, sInstanceOverride=None,
        bInstanceOnly=False,
    ):
        del dictCtx, sContainerId, bInstanceOnly
        return {
            "sInstance": sInstanceOverride
            or str(dictWorkflow.get("sZenodoService") or "sandbox"),
            "sConceptDoi": str(dictWorkflow.get("sZenodoConceptDoi") or ""),
            "sDepositionId": str(dictWorkflow.get("sZenodoDepositionId") or ""),
        }

    for sModule in (
        "vaibify.gui.routes.syncRoutes",
        "vaibify.gui.routes.environmentArchiveRoutes",
        "vaibify.gui.routes.promotionRecoveryRoutes",
    ):
        import importlib
        module = importlib.import_module(sModule)
        if hasattr(module, "fsRequireBoundOverleafProjectId"):
            monkeypatch.setattr(
                module, "fsRequireBoundOverleafProjectId", fsOverleafFromProject,
            )
        if hasattr(module, "fdictRequireBoundZenodoTarget"):
            monkeypatch.setattr(
                module, "fdictRequireBoundZenodoTarget", fdictZenodoFromProject,
            )
        if hasattr(module, "fnRecordZenodoOutcomeInBinding"):
            monkeypatch.setattr(
                module, "fnRecordZenodoOutcomeInBinding",
                lambda *tArgs, **dictKeywords: None,
            )
    monkeypatch.setattr(
        remoteBindingContext, "fsRequireBoundOverleafProjectId",
        fsOverleafFromProject,
    )
    monkeypatch.setattr(
        remoteBindingContext, "fdictRequireBoundZenodoTarget",
        fdictZenodoFromProject,
    )
