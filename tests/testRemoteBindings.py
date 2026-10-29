"""A token-bearing action acts only on a target the researcher confirmed.

Source: ``vaibify/config/remoteBindings.py``, the ``sProjectUuid`` in
``vaibify/config/registryManager.py``, ``vaibify/gui/remoteBindingContext.py``
and ``vaibify/gui/routes/bindingRoutes.py``.

Overleaf and Zenodo targets in ``project.json`` are the agent's to edit.
The trusted copy lives on the host, keyed by a random project identity
and the workflow, and only the researcher writes it. Every fixture here
keeps the container id, the project name, the project identity and the
workflow path DISTINCT, because the defect this guards against is a
lookup keyed by the wrong one.
"""

import json
import os
import stat
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from vaibify.config import registryManager, remoteBindings
from vaibify.config.remoteBindings import (
    BindingMismatchError, BindingRequiredError, fdictConfirmedBinding,
    fdictRequireMatchingBinding, flistDeleteBindings, fnConfirmBinding,
    fnUpdateBoundTarget, fsWorkflowBindingKey,
)
from vaibify.gui import remoteBindingContext
from vaibify.gui.routes import bindingRoutes
from vaibify.gui.workflowManager import S_LOADED_FROM_KEY

S_CONTAINER_ID = "cid-0123abcd"
S_PROJECT_NAME = "projectAlpha"
S_WORKFLOW_PATH = "/workspace/repoAlpha/.vaibify/projects/study.json"
S_KEY = fsWorkflowBindingKey("repoAlpha/.vaibify/projects/study.json", "study")


@pytest.fixture(autouse=True)
def fnIsolateHostState(tmp_path, monkeypatch):
    """A private HOME for the bindings and a private registry."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sRegistryDir = str(tmp_path / ".vaibify")
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDir, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDir, "registry.lock"),
    )


def _fsRegister(tmp_path, sProjectName, sMode="container"):
    sProjectDir = str(tmp_path / ("dir-" + sProjectName))
    os.makedirs(sProjectDir, exist_ok=True)
    with open(os.path.join(sProjectDir, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDir, sMode=sMode)
    return registryManager.fdictGetProject(sProjectName)["sProjectUuid"]


# ---------------------------------------------------------------------------
# The identity
# ---------------------------------------------------------------------------


def testEveryNewEntryCarriesARandomIdentity(tmp_path):
    sFirst = _fsRegister(tmp_path, "projectOne")
    sSecond = _fsRegister(tmp_path, "projectTwo")
    assert len(sFirst) == 32 and len(sSecond) == 32
    assert sFirst != sSecond
    assert sFirst != "projectOne"


def testTheIdentitySurvivesARename(tmp_path):
    sUuid = _fsRegister(tmp_path, "sandboxOne", sMode="host")
    registryManager.fnPromoteHostProject("sandboxOne", "projectRenamed")
    assert registryManager.fdictGetProject("projectRenamed")["sProjectUuid"] == sUuid
    assert registryManager.fsProjectUuidFor("projectRenamed") == sUuid


def testAnEntryWrittenBeforeTheIdentityIsStampedOnFirstUse(tmp_path):
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileRegistry:
        json.dump({"listProjects": [{
            "sName": "legacyProject", "sDirectory": str(tmp_path),
            "sConfigPath": str(tmp_path / "vaibify.yml"),
            "sContainerName": "legacyProject", "sMode": "container",
        }]}, fileRegistry)
    sUuid = registryManager.fsProjectUuidFor("legacyProject")
    assert len(sUuid) == 32
    assert registryManager.fsProjectUuidFor("legacyProject") == sUuid
    assert registryManager.fdictLoadRegistry()["listProjects"][0]["sProjectUuid"] == sUuid


@pytest.mark.falsification
def testReRegistrationGetsANewIdentityAndNoBindings(tmp_path):
    """Removing a project ends its bindings; the same name registers afresh.

    Kills: deriving ``sProjectUuid`` from the project's name in
    ``_fdictBuildProjectEntry``, so a re-registered name inherits the
    identity -- and would inherit any binding keyed by it.
    """
    sUuid = _fsRegister(tmp_path, S_PROJECT_NAME)
    fnConfirmBinding(sUuid, S_KEY, "study", "overleaf", {"sProjectId": "ol-1"}, "researcher")
    assert os.path.exists(os.path.join(str(tmp_path), ".vaibify", "bindings", sUuid + ".json"))
    registryManager.fnRemoveProject(S_PROJECT_NAME)
    assert not os.path.exists(os.path.join(str(tmp_path), ".vaibify", "bindings", sUuid + ".json"))
    sUuidAgain = _fsRegister(tmp_path, S_PROJECT_NAME)
    assert sUuidAgain != sUuid
    assert fdictConfirmedBinding(sUuidAgain, S_KEY, "overleaf") is None


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testNothingBindsUntilTheResearcherConfirms():
    """A workflow with a target in project.json but no confirmation is refused.

    Kills: ``fdictRequireMatchingBinding`` answering the project.json
    target when no binding exists, which binds from the agent-editable
    file automatically.
    """
    with pytest.raises(BindingRequiredError, match="no confirmed overleaf binding"):
        fdictRequireMatchingBinding("a" * 32, S_KEY, "overleaf", {"sProjectId": "ol-1"})
    fnConfirmBinding("a" * 32, S_KEY, "study", "overleaf", {"sProjectId": "ol-1"}, "researcher")
    assert fdictRequireMatchingBinding(
        "a" * 32, S_KEY, "overleaf", {"sProjectId": "ol-1"},
    ) == {"sProjectId": "ol-1"}


def testAMismatchRefusesNamingBothValues():
    fnConfirmBinding("b" * 32, S_KEY, "study", "zenodo",
                     {"sInstance": "sandbox", "sConceptDoi": "10.5072/zenodo.1", "sDepositionId": "7"},
                     "researcher")
    with pytest.raises(BindingMismatchError) as errorInfo:
        fdictRequireMatchingBinding(
            "b" * 32, S_KEY, "zenodo",
            {"sInstance": "sandbox", "sConceptDoi": "10.5072/zenodo.1", "sDepositionId": "9"},
        )
    assert "'9'" in str(errorInfo.value) and "'7'" in str(errorInfo.value)
    # an empty project value constrains nothing: a concept not yet minted
    assert fdictRequireMatchingBinding(
        "b" * 32, S_KEY, "zenodo", {"sInstance": "sandbox", "sConceptDoi": "", "sDepositionId": ""},
    )["sDepositionId"] == "7"
    with pytest.raises(BindingMismatchError, match="sInstance"):
        fdictRequireMatchingBinding(
            "b" * 32, S_KEY, "zenodo", {"sInstance": "zenodo", "sConceptDoi": "", "sDepositionId": ""},
        )


def testAMovedWorkflowRefusesUntilReBoundAndCanCarryTheOldBindingAcross():
    fnConfirmBinding("c" * 32, S_KEY, "study", "overleaf", {"sProjectId": "ol-1"}, "researcher")
    sMovedKey = fsWorkflowBindingKey("repoAlpha/.vaibify/projects/renamed.json", "study")
    with pytest.raises(BindingRequiredError):
        fdictRequireMatchingBinding("c" * 32, sMovedKey, "overleaf", {"sProjectId": "ol-1"})
    fnConfirmBinding(
        "c" * 32, sMovedKey, "study", "overleaf", {"sProjectId": "ol-1"},
        "researcher", sCarryFromWorkflowKey=S_KEY,
    )
    assert fdictConfirmedBinding("c" * 32, sMovedKey, "overleaf")["dictTarget"] == {"sProjectId": "ol-1"}
    assert fdictConfirmedBinding("c" * 32, S_KEY, "overleaf") is None


def testTheHubMovesAConfirmedZenodoTargetButCannotCreateOne():
    with pytest.raises(BindingRequiredError):
        fnUpdateBoundTarget("d" * 32, S_KEY, "zenodo", {"sDepositionId": "7"})
    fnConfirmBinding("d" * 32, S_KEY, "study", "zenodo", {"sInstance": "sandbox"}, "researcher")
    fnUpdateBoundTarget("d" * 32, S_KEY, "zenodo", {"sConceptDoi": "10.5072/zenodo.1", "sDepositionId": "7"})
    assert fdictConfirmedBinding("d" * 32, S_KEY, "zenodo")["dictTarget"] == {
        "sInstance": "sandbox", "sConceptDoi": "10.5072/zenodo.1", "sDepositionId": "7",
    }


def testTheRecordIsPrivateAndHoldsNoSecret(tmp_path):
    fnConfirmBinding("e" * 32, S_KEY, "study", "overleaf", {"sProjectId": "ol-1"}, "researcher")
    sDirectory = os.path.join(str(tmp_path), ".vaibify", "bindings")
    sPath = os.path.join(sDirectory, "e" * 32 + ".json")
    assert stat.S_IMODE(os.stat(sDirectory).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
    dictRecord = json.load(open(sPath))
    assert dictRecord["dictWorkflows"][S_KEY]["dictServices"]["overleaf"]["sConfirmedBy"] == "researcher"
    assert flistDeleteBindings("e" * 32) == ["overleaf"]
    assert not os.path.exists(sPath)


@pytest.mark.falsification
def testAnUnknownServiceOrFieldIsRefused():
    """Only overleaf and zenodo targets exist; a stray key is refused too.

    Kills: ``_fnValidateService`` accepting any service name.
    """
    with pytest.raises(ValueError, match="not a bindable service"):
        fnConfirmBinding("f" * 32, S_KEY, "study", "github", {"sUrl": "x"}, "researcher")
    with pytest.raises(ValueError, match="no field"):
        fnConfirmBinding("f" * 32, S_KEY, "study", "overleaf", {"sUrl": "x"}, "researcher")


# ---------------------------------------------------------------------------
# The route and the context
# ---------------------------------------------------------------------------


def _fdictWorkflow(sOverleafId="ol-1"):
    return {
        S_LOADED_FROM_KEY: S_WORKFLOW_PATH, "sWorkflowName": "study",
        "sOverleafProjectId": sOverleafId, "sZenodoService": "sandbox",
        "sZenodoConceptDoi": "", "sZenodoDepositionId": "",
        "sProjectRepoPath": "/workspace/repoAlpha", "listSteps": [],
    }


@pytest.fixture
def tApp(tmp_path, monkeypatch):
    """A registered project served by a container whose id is not its name."""
    sUuid = _fsRegister(tmp_path, S_PROJECT_NAME)
    monkeypatch.setattr(
        remoteBindingContext, "fsContainerNameForId",
        lambda connectionDocker, sContainerId: (
            S_PROJECT_NAME if sContainerId == S_CONTAINER_ID else sContainerId
        ),
    )
    monkeypatch.setattr(
        bindingRoutes, "_DICT_EVIDENCE_FETCHERS",
        {sService: (lambda dictTarget: {"sState": "fetched", "sTitle": "stub"})
         for sService in remoteBindings.T_BINDING_SERVICES},
    )
    dictCtx = {
        "require": lambda sContainerId: None,
        "workflows": {S_CONTAINER_ID: _fdictWorkflow()},
        "docker": SimpleNamespace(),
    }
    app = FastAPI()
    bindingRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictCtx, sUuid


@pytest.mark.falsification
def testTheAgentLaneCannotConfirmABinding(tApp):
    """The confirmation is the researcher's; an agent token is refused and nothing is written.

    Kills: dropping the agent-lane refusal from the confirm handler.
    """
    client, dictCtx, sUuid = tApp
    responseHttp = client.post(
        f"/api/bindings/{S_CONTAINER_ID}/confirm",
        json={"sService": "overleaf"},
        headers={"X-Vaibify-Session": "agent-token"},
    )
    assert responseHttp.status_code == 403
    assert fdictConfirmedBinding(sUuid, S_KEY, "overleaf") is None


def testTheResearcherConfirmsAndTheReadShowsAgreement(tApp):
    client, dictCtx, sUuid = tApp
    dictBefore = client.get(f"/api/bindings/{S_CONTAINER_ID}").json()
    assert dictBefore["sProjectUuid"] == sUuid
    assert dictBefore["sWorkflowKey"] == S_KEY
    assert dictBefore["dictServices"]["overleaf"]["dictBinding"] is None
    assert dictBefore["dictServices"]["overleaf"]["bAgrees"] is False
    responseHttp = client.post(
        f"/api/bindings/{S_CONTAINER_ID}/confirm", json={"sService": "overleaf"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["dictTarget"] == {"sProjectId": "ol-1"}
    dictAfter = client.get(f"/api/bindings/{S_CONTAINER_ID}").json()
    assert dictAfter["dictServices"]["overleaf"]["bAgrees"] is True
    assert dictAfter["dictServices"]["overleaf"]["dictFetched"]["sState"] == "fetched"


def testTheContextRefusesAnEditedTargetNamingBothValues(tApp):
    client, dictCtx, sUuid = tApp
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fsRequireBoundOverleafProjectId(
            dictCtx, S_CONTAINER_ID, dictWorkflow,
        )
    assert errorInfo.value.status_code == 409
    client.post(f"/api/bindings/{S_CONTAINER_ID}/confirm", json={"sService": "overleaf"})
    assert remoteBindingContext.fsRequireBoundOverleafProjectId(
        dictCtx, S_CONTAINER_ID, dictWorkflow,
    ) == "ol-1"
    dictWorkflow["sOverleafProjectId"] = "ol-attacker"
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fsRequireBoundOverleafProjectId(
            dictCtx, S_CONTAINER_ID, dictWorkflow,
        )
    assert "'ol-attacker'" in errorInfo.value.detail
    assert "'ol-1'" in errorInfo.value.detail


def testAnUnregisteredContainerHasNoBinding(tApp):
    client, dictCtx, _ = tApp
    dictCtx["workflows"]["cid-unknown"] = _fdictWorkflow()
    responseHttp = client.get("/api/bindings/cid-unknown")
    assert responseHttp.status_code == 409
    assert "not a registered project" in responseHttp.text


def testAPromotionNeedsTheProductionInstanceConfirmed(tApp):
    client, dictCtx, sUuid = tApp
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    client.post(f"/api/bindings/{S_CONTAINER_ID}/confirm", json={"sService": "zenodo"})
    assert remoteBindingContext.fdictRequireBoundZenodoTarget(
        dictCtx, S_CONTAINER_ID, dictWorkflow,
    )["sInstance"] == "sandbox"
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fdictRequireBoundZenodoTarget(
            dictCtx, S_CONTAINER_ID, dictWorkflow, "zenodo",
        )
    assert "'zenodo'" in errorInfo.value.detail and "'sandbox'" in errorInfo.value.detail
    remoteBindingContext.fnRecordZenodoOutcomeInBinding(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "sandbox", "10.5072/zenodo.5", "11",
    )
    assert fdictConfirmedBinding(sUuid, S_KEY, "zenodo")["dictTarget"]["sDepositionId"] == "11"
