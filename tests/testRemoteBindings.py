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
    BindingMismatchError, BindingRecordInvalidError, BindingRequiredError,
    fdictConfirmedBinding, fdictRequireEnvironmentArchiveTarget,
    fdictRequireOverleafTarget, fdictRequireProjectRecordTarget,
    flistDeleteBindings, fnConfirmBinding, fnUpdateBoundTarget,
    fsWorkflowBindingKey,
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
    fnConfirmBinding(sUuid, S_KEY, "study", "overleaf", "", {"sProjectId": "ol-1"}, "researcher")
    sBindingPath = os.path.join(remoteBindings.fsBindingsDirectory(), sUuid + ".json")
    assert os.path.exists(sBindingPath)
    registryManager.fnRemoveProject(S_PROJECT_NAME)
    assert not os.path.exists(sBindingPath)
    sUuidAgain = _fsRegister(tmp_path, S_PROJECT_NAME)
    assert sUuidAgain != sUuid
    assert fdictConfirmedBinding(sUuidAgain, S_KEY, "overleaf") is None


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


S_SANDBOX_CONCEPT = "10.5072/zenodo.100"
S_PERMANENT_CONCEPT = "10.5281/zenodo.200"


def _fnConfirmZenodo(sUuid, sRecordKind, dictTarget, sKey=S_KEY):
    fnConfirmBinding(sUuid, sKey, "study", "zenodo", sRecordKind, dictTarget, "researcher")


@pytest.mark.falsification
def testNothingBindsUntilTheResearcherConfirms():
    """A workflow with a target in project.json but no confirmation is refused.

    Kills: ``fdictRequireOverleafTarget`` answering the project.json
    target when no binding exists, which binds from the agent-editable
    file automatically.
    """
    with pytest.raises(BindingRequiredError, match="no confirmed overleaf binding"):
        fdictRequireOverleafTarget("a" * 32, S_KEY, {"sProjectId": "ol-1"})
    fnConfirmBinding("a" * 32, S_KEY, "study", "overleaf", "", {"sProjectId": "ol-1"}, "researcher")
    assert fdictRequireOverleafTarget(
        "a" * 32, S_KEY, {"sProjectId": "ol-1"},
    ) == {"sProjectId": "ol-1"}


def testAMismatchRefusesNamingBothValues():
    _fnConfirmZenodo("b" * 32, "project", {
        "sInstance": "sandbox", "sConceptDoi": S_SANDBOX_CONCEPT, "sDepositionId": "7",
    })
    dictDeclared = {"sInstance": "sandbox", "sConceptDoi": S_SANDBOX_CONCEPT}
    with pytest.raises(BindingMismatchError) as errorInfo:
        fdictRequireProjectRecordTarget(
            "b" * 32, S_KEY, "sandbox", dict(dictDeclared, sDepositionId="9"),
        )
    assert (errorInfo.value.sProjectValue, errorInfo.value.sBoundValue) == ("9", "7")
    # an empty project value constrains nothing: a concept not yet minted
    assert fdictRequireProjectRecordTarget(
        "b" * 32, S_KEY, "sandbox", {"sInstance": "sandbox"},
    )["sDepositionId"] == "7"
    # the other instance has its own entry, and this workflow has none there
    with pytest.raises(BindingRequiredError, match="'zenodo'"):
        fdictRequireProjectRecordTarget("b" * 32, S_KEY, "zenodo", dictDeclared)


def testAMovedWorkflowRefusesUntilReBoundAndCanCarryTheOldBindingAcross():
    fnConfirmBinding("c" * 32, S_KEY, "study", "overleaf", "", {"sProjectId": "ol-1"}, "researcher")
    sMovedKey = fsWorkflowBindingKey("repoAlpha/.vaibify/projects/renamed.json", "study")
    with pytest.raises(BindingRequiredError):
        fdictRequireOverleafTarget("c" * 32, sMovedKey, {"sProjectId": "ol-1"})
    fnConfirmBinding(
        "c" * 32, sMovedKey, "study", "overleaf", "", {"sProjectId": "ol-1"},
        "researcher", sCarryFromWorkflowKey=S_KEY,
    )
    assert fdictConfirmedBinding("c" * 32, sMovedKey, "overleaf")["dictTarget"] == {"sProjectId": "ol-1"}
    assert fdictConfirmedBinding("c" * 32, S_KEY, "overleaf") is None


def testTheHubMovesAConfirmedZenodoTargetButCannotCreateOne():
    with pytest.raises(BindingRequiredError):
        fnUpdateBoundTarget("d" * 32, S_KEY, "sandbox", {"sDepositionId": "7"})
    _fnConfirmZenodo("d" * 32, "project", {"sInstance": "sandbox"})
    fnUpdateBoundTarget("d" * 32, S_KEY, "sandbox", {
        "sConceptDoi": S_SANDBOX_CONCEPT, "sDepositionId": "7",
    })
    assert fdictConfirmedBinding("d" * 32, S_KEY, "zenodo", "project", "sandbox")["dictTarget"] == {
        "sInstance": "sandbox", "sConceptDoi": S_SANDBOX_CONCEPT, "sDepositionId": "7",
    }
    with pytest.raises(ValueError, match="moves only"):
        fnUpdateBoundTarget("d" * 32, S_KEY, "sandbox", {"sInstance": "zenodo"})


def testTheRecordIsPrivateAndHoldsNoSecret():
    fnConfirmBinding("e" * 32, S_KEY, "study", "overleaf", "", {"sProjectId": "ol-1"}, "researcher")
    sDirectory = remoteBindings.fsBindingsDirectory()
    sPath = os.path.join(sDirectory, "e" * 32 + ".json")
    assert stat.S_IMODE(os.stat(sDirectory).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
    dictRecord = json.load(open(sPath))
    assert dictRecord["iSchemaVersion"] == 2
    assert dictRecord["dictWorkflows"][S_KEY]["dictServices"]["overleaf"]["sConfirmedBy"] == "researcher"
    assert flistDeleteBindings("e" * 32) == ["overleaf"]
    assert not os.path.exists(sPath)


@pytest.mark.falsification
def testAnUnknownServiceOrFieldIsRefused():
    """Only overleaf and zenodo targets exist; a stray key is refused too.

    Kills: ``_fnValidateService`` accepting any service name.
    """
    with pytest.raises(ValueError, match="not a bindable service"):
        fnConfirmBinding("f" * 32, S_KEY, "study", "github", "", {"sUrl": "x"}, "researcher")
    with pytest.raises(ValueError, match="no field"):
        fnConfirmBinding("f" * 32, S_KEY, "study", "overleaf", "", {"sUrl": "x"}, "researcher")
    with pytest.raises(ValueError, match="not a Zenodo record kind"):
        _fnConfirmZenodo("f" * 32, "manuscript", {"sInstance": "sandbox"})
    with pytest.raises(ValueError, match="no field"):
        _fnConfirmZenodo("f" * 32, "environment", {
            "sInstance": "sandbox", "sConceptDoi": S_SANDBOX_CONCEPT,
        })


def testAConfirmationWritesOneEntryAndNothingElse():
    _fnConfirmZenodo("9" * 32, "project", {"sInstance": "sandbox", "sDepositionId": "7"})
    _fnConfirmZenodo("9" * 32, "environment", {"sInstance": "zenodo"})
    _fnConfirmZenodo("9" * 32, "project", {"sInstance": "zenodo"})
    assert fdictConfirmedBinding("9" * 32, S_KEY, "zenodo", "project", "sandbox")[
        "dictTarget"]["sDepositionId"] == "7"
    assert fdictConfirmedBinding("9" * 32, S_KEY, "zenodo", "environment", "zenodo")
    assert fdictConfirmedBinding("9" * 32, S_KEY, "zenodo", "environment", "sandbox") is None
    assert fdictRequireEnvironmentArchiveTarget("9" * 32, S_KEY, "zenodo") == {"sInstance": "zenodo"}
    with pytest.raises(BindingRequiredError, match="environment-archive"):
        fdictRequireEnvironmentArchiveTarget("9" * 32, S_KEY, "sandbox")


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
        {sService: (lambda dictCtx, sContainerId, dictTarget: {
            "sState": "fetched", "sTitle": "stub",
        }) for sService in remoteBindings.T_BINDING_SERVICES},
    )
    dictCtx = {
        "require": lambda sContainerId: None,
        "workflows": {S_CONTAINER_ID: _fdictWorkflow()},
        "docker": SimpleNamespace(),
    }
    app = FastAPI()
    bindingRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictCtx, sUuid


def _fdictEntry(dictDescribed, sService, sRecordKind="", sInstance=""):
    return next(
        dictEntry for dictEntry in dictDescribed["listEntries"]
        if (dictEntry["sService"], dictEntry["sRecordKind"], dictEntry["sInstance"])
        == (sService, sRecordKind, sInstance)
    )


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
    assert dictBefore["dictRecordState"] == {"bValid": True}
    assert _fdictEntry(dictBefore, "overleaf")["dictConfirmed"] is None
    assert _fdictEntry(dictBefore, "overleaf")["sState"] == "unconfirmed"
    responseHttp = client.post(
        f"/api/bindings/{S_CONTAINER_ID}/confirm", json={"sService": "overleaf"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["dictTarget"] == {"sProjectId": "ol-1"}
    dictAfter = client.get(f"/api/bindings/{S_CONTAINER_ID}").json()
    assert _fdictEntry(dictAfter, "overleaf")["sState"] == "agrees"
    assert _fdictEntry(dictAfter, "overleaf")["dictFetched"]["sState"] == "fetched"
    assert _fdictEntry(dictAfter, "zenodo", "project", "sandbox")["dictProjectTarget"] == {
        "sInstance": "sandbox", "sConceptDoi": "", "sDepositionId": "",
    }
    assert _fdictEntry(dictAfter, "zenodo", "project", "zenodo")["dictProjectTarget"] is None
    assert _fdictEntry(dictAfter, "zenodo", "environment", "zenodo")["dictProjectTarget"] is None


def testTheContextRefusesAnEditedTargetNamingBothValues(tApp):
    client, dictCtx, sUuid = tApp
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fsRequireBoundOverleafProjectId(
            dictCtx, S_CONTAINER_ID, dictWorkflow,
        )
    assert errorInfo.value.status_code == 409
    assert errorInfo.value.detail["sError"] == "BINDING-REQUIRED"
    client.post(f"/api/bindings/{S_CONTAINER_ID}/confirm", json={"sService": "overleaf"})
    assert remoteBindingContext.fsRequireBoundOverleafProjectId(
        dictCtx, S_CONTAINER_ID, dictWorkflow,
    ) == "ol-1"
    dictWorkflow["sOverleafProjectId"] = "ol-attacker"
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fsRequireBoundOverleafProjectId(
            dictCtx, S_CONTAINER_ID, dictWorkflow,
        )
    dictDetail = errorInfo.value.detail
    assert dictDetail["sError"] == "BINDING-MISMATCH"
    assert (dictDetail["sService"], dictDetail["sRecordKind"]) == ("overleaf", "")
    assert "ol-attacker" in dictDetail["sMessage"] and "ol-1" in dictDetail["sMessage"]
    assert "an agent edited project.json" in dictDetail["sMessage"]


def testAnUnregisteredContainerHasNoBinding(tApp):
    client, dictCtx, _ = tApp
    dictCtx["workflows"]["cid-unknown"] = _fdictWorkflow()
    responseHttp = client.get("/api/bindings/cid-unknown")
    assert responseHttp.status_code == 409
    assert "not a registered project" in responseHttp.text


def testAPromotionNeedsTheProductionInstanceConfirmed(tApp):
    client, dictCtx, sUuid = tApp
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    client.post(f"/api/bindings/{S_CONTAINER_ID}/confirm", json={
        "sService": "zenodo", "sRecordKind": "project",
    })
    assert remoteBindingContext.fdictRequireBoundZenodoTarget(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "project",
    )["sInstance"] == "sandbox"
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fdictRequireBoundZenodoTarget(
            dictCtx, S_CONTAINER_ID, dictWorkflow, "project", "zenodo", bMint=True,
        )
    dictDetail = errorInfo.value.detail
    assert (dictDetail["sError"], dictDetail["sRecordKind"], dictDetail["sInstance"]) == (
        "BINDING-REQUIRED", "project", "zenodo",
    )
    assert "zenodo.org (permanent)" in dictDetail["sMessage"]
    assert "production" not in dictDetail["sMessage"]
    remoteBindingContext.fnRecordZenodoOutcomeInBinding(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "sandbox", S_SANDBOX_CONCEPT, "11",
    )
    assert fdictConfirmedBinding(sUuid, S_KEY, "zenodo", "project", "sandbox")[
        "dictTarget"]["sDepositionId"] == "11"


# ---------------------------------------------------------------------------
# Two kinds of authorization (F4, F5)
# ---------------------------------------------------------------------------


def _fdictPublishedOnTheSandbox(dictCtx):
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    dictWorkflow.update(sZenodoConceptDoi=S_SANDBOX_CONCEPT, sZenodoDepositionId="2")
    return dictWorkflow


@pytest.mark.falsification
def testAPromotionPassesWithASandboxEntryAndAMintAuthorization(tApp):
    """project.json names the sandbox record; the promotion mints on zenodo.org.

    Before 2026-11-02 no binding let a promotion through: a sandbox
    binding refused the zenodo.org publish, and a zenodo.org binding
    failed the comparison with the sandbox record project.json names.

    Kills: comparing an entry on the OTHER instance with project.json
    (rule 3), which compares the sandbox record with the mint
    authorization's empty one.
    """
    _, dictCtx, sUuid = tApp
    dictWorkflow = _fdictPublishedOnTheSandbox(dictCtx)
    _fnConfirmZenodo(sUuid, "project", {
        "sInstance": "sandbox", "sConceptDoi": S_SANDBOX_CONCEPT, "sDepositionId": "2",
    })
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "zenodo"})
    assert remoteBindingContext.fdictRequireBoundZenodoTarget(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "project", "zenodo", bMint=True,
    ) == {"sInstance": "zenodo", "sConceptDoi": "", "sDepositionId": ""}


def testAPromotionIsRefusedOverAConfirmedPermanentRecord(tApp):
    _, dictCtx, sUuid = tApp
    dictWorkflow = _fdictPublishedOnTheSandbox(dictCtx)
    _fnConfirmZenodo(sUuid, "project", {
        "sInstance": "zenodo", "sConceptDoi": S_PERMANENT_CONCEPT, "sDepositionId": "201",
    })
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fdictRequireBoundZenodoTarget(
            dictCtx, S_CONTAINER_ID, dictWorkflow, "project", "zenodo", bMint=True,
        )
    assert errorInfo.value.detail["sError"] == "BINDING-MISMATCH"
    assert S_PERMANENT_CONCEPT in errorInfo.value.detail["sMessage"]


def testAnEnvironmentDepositNeedsOnlyItsInstanceAuthorized(tApp):
    """F5: an environment deposit to the instance project.json does not name."""
    _, dictCtx, sUuid = tApp
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    dictWorkflow.update(
        sZenodoService="zenodo", sZenodoConceptDoi=S_PERMANENT_CONCEPT,
        sZenodoDepositionId="201",
    )
    _fnConfirmZenodo(sUuid, "environment", {"sInstance": "sandbox"})
    assert remoteBindingContext.fdictRequireBoundZenodoTarget(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "environment", "sandbox",
    ) == {"sInstance": "sandbox"}
    with pytest.raises(HTTPException) as errorInfo:
        remoteBindingContext.fdictRequireBoundZenodoTarget(
            dictCtx, S_CONTAINER_ID, dictWorkflow, "environment", "zenodo",
        )
    assert errorInfo.value.detail["sError"] == "BINDING-REQUIRED"
    assert "environment archive on zenodo.org (permanent)" in (
        errorInfo.value.detail["sMessage"]
    )


def _fsRecordBytes(sUuid):
    with open(remoteBindings.fsBindingPath(sUuid), "rb") as fileRecord:
        return fileRecord.read()


@pytest.mark.falsification
def testAMintMovesOnlyTheProjectKindsMintedEntry(tApp):
    """After a project mint on zenodo.org, nothing else in the record moves.

    The record is read as raw JSON, so a write that lands in the wrong
    kind is seen as the wrong entry changing.

    Kills: the post-mint update writing the environment kind's entry.
    """
    _, dictCtx, sUuid = tApp
    dictWorkflow = _fdictPublishedOnTheSandbox(dictCtx)
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "sandbox", "sDepositionId": "2"})
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "zenodo"})
    for sInstance in ("sandbox", "zenodo"):
        _fnConfirmZenodo(sUuid, "environment", {"sInstance": sInstance})
    dictBefore = json.loads(_fsRecordBytes(sUuid))
    remoteBindingContext.fnRecordZenodoOutcomeInBinding(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "zenodo", S_PERMANENT_CONCEPT, "201",
    )
    dictAfter = json.loads(_fsRecordBytes(sUuid))
    dictKindsBefore = dictBefore["dictWorkflows"][S_KEY]["dictServices"]["zenodo"]["dictRecordKinds"]
    dictKindsAfter = dictAfter["dictWorkflows"][S_KEY]["dictServices"]["zenodo"]["dictRecordKinds"]
    assert dictKindsAfter["environment"] == dictKindsBefore["environment"]
    assert dictKindsAfter["project"]["dictInstances"]["sandbox"] == (
        dictKindsBefore["project"]["dictInstances"]["sandbox"]
    )
    assert dictKindsAfter["project"]["dictInstances"]["zenodo"]["dictTarget"] == {
        "sInstance": "zenodo", "sConceptDoi": S_PERMANENT_CONCEPT, "sDepositionId": "201",
    }


# ---------------------------------------------------------------------------
# The environment-promotion route, adoption, and a failed promotion
# ---------------------------------------------------------------------------


@pytest.fixture
def tPromotion(tApp, monkeypatch):
    """The real environment-promotion route, every check before its guard passed.

    The token reader, its validation and the durable launch record
    their calls, so a test sees exactly how far the route got.
    """
    _, dictCtx, sUuid = tApp
    from vaibify.gui.routes import environmentArchiveRoutes as routesArchive
    listCalls = []
    for sName in ("fnRequireNetworkAccess", "_fsRequireProjectRepo",
                  "_fnRefuseIfDepositInFlight", "_fnRequireSandboxImageDeposit"):
        monkeypatch.setattr(routesArchive, sName, lambda *tArgs: None)
    monkeypatch.setattr(routesArchive, "ffilesForWorkflow", lambda *tArgs: None)
    monkeypatch.setattr(
        routesArchive, "_fdictRequireEnvelopeContainerBlock", lambda filesRepo: {},
    )
    monkeypatch.setattr(
        routesArchive, "_fsReadProductionTokenFromHost",
        lambda *tArgs: listCalls.append("read-token") or "a-token",
    )
    monkeypatch.setattr(
        routesArchive, "_fnValidateProductionToken",
        lambda sToken: listCalls.append("validate-token"),
    )

    async def fdictLaunch(*tArgs):
        listCalls.append("launch")
        return {"bStarted": True}

    monkeypatch.setattr(routesArchive, "_fdictLaunchPromotionDurably", fdictLaunch)
    app = FastAPI()
    routesArchive.fnRegisterAll(app, dictCtx)
    return TestClient(app), listCalls, sUuid


S_PROMOTE_PATH = f"/api/workflow/{S_CONTAINER_ID}/environment-archive/promote"


@pytest.mark.falsification
def testTheEnvironmentPromotionRefusesBeforeReadingAnyToken(tPromotion):
    """No environment authorization on zenodo.org: refused before the token is touched.

    Until 2026-11-02 this route checked no binding at all, and spent the
    researcher's zenodo.org token unconfirmed.

    Kills: removing the environment-kind guard from the promotion route.
    """
    client, listCalls, _ = tPromotion
    responseHttp = client.post(S_PROMOTE_PATH)
    assert responseHttp.status_code == 409, responseHttp.text
    dictDetail = responseHttp.json()["detail"]
    assert (dictDetail["sError"], dictDetail["sRecordKind"], dictDetail["sInstance"]) == (
        "BINDING-REQUIRED", "environment", "zenodo",
    )
    assert listCalls == []


def testTheEnvironmentPromotionProceedsAndLeavesTheProjectKindAlone(tPromotion, monkeypatch):
    client, listCalls, sUuid = tPromotion
    _fnConfirmZenodo(sUuid, "project", {
        "sInstance": "zenodo", "sConceptDoi": S_PERMANENT_CONCEPT, "sDepositionId": "201",
    })
    _fnConfirmZenodo(sUuid, "environment", {"sInstance": "zenodo"})
    baBefore = _fsRecordBytes(sUuid)
    listWrites = []
    for sWriter in ("fnConfirmBinding", "fnUpdateBoundTarget"):
        monkeypatch.setattr(
            remoteBindings, sWriter,
            lambda *tArgs, sName=sWriter, **dictKeywords: listWrites.append(sName),
        )
    responseHttp = client.post(S_PROMOTE_PATH)
    assert responseHttp.status_code == 200, responseHttp.text
    assert listCalls == ["read-token", "validate-token", "launch"]
    assert _fsRecordBytes(sUuid) == baBefore
    assert listWrites == []


def testAnImageAdoptionWritesNoBinding(tApp, monkeypatch):
    import asyncio
    _, dictCtx, sUuid = tApp
    from vaibify.gui.routes import promotionRecoveryRoutes as routesRecovery
    _fnConfirmZenodo(sUuid, "environment", {"sInstance": "zenodo"})
    baBefore = _fsRecordBytes(sUuid)
    monkeypatch.setattr(routesRecovery, "_fnAdoptImageDeposit", lambda *tArgs: None)
    monkeypatch.setattr(
        routesRecovery.syncBookkeeping, "fnRemovePendingPromotion", lambda *tArgs: None,
    )
    monkeypatch.setattr(
        routesRecovery.syncBookkeeping, "fnMirrorPendingPromotions", lambda *tArgs: None,
    )
    asyncio.run(routesRecovery._fdictAdoptForLane(
        dictCtx, S_CONTAINER_ID, dictCtx["workflows"][S_CONTAINER_ID], None, "key",
        {"sLane": "image", "sPromotionId": "p1", "sTargetService": "zenodo"},
        {"sDoi": "10.5281/zenodo.300", "sConceptDoi": "10.5281/zenodo.299"}, None,
    ))
    assert _fsRecordBytes(sUuid) == baBefore


@pytest.mark.falsification
def testAnAdoptedProjectPromotionMovesTheProjectRecord(tApp, monkeypatch):
    """An interrupted promotion, adopted, leaves the binding where the deposit is.

    Kills: removing the binding update from ``_fnAdoptProjectDeposit``,
    after which the next deposit to the adopted record is refused.
    """
    _, dictCtx, sUuid = tApp
    from vaibify.gui.routes import promotionRecoveryRoutes as routesRecovery
    monkeypatch.setattr(routesRecovery, "fdictCommitWorkflowSave", lambda *tArgs: None)
    dictWorkflow = _fdictPublishedOnTheSandbox(dictCtx)
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "zenodo"})
    routesRecovery._fnAdoptProjectDeposit(
        dictCtx, S_CONTAINER_ID, dictWorkflow,
        {"iDepositId": 201, "sTargetService": "zenodo", "sLane": "project"},
        {"sDoi": "10.5281/zenodo.201", "sConceptDoi": S_PERMANENT_CONCEPT}, None,
    )
    assert fdictConfirmedBinding(sUuid, S_KEY, "zenodo", "project", "zenodo")["dictTarget"] == {
        "sInstance": "zenodo", "sConceptDoi": S_PERMANENT_CONCEPT, "sDepositionId": "201",
    }
    # The researcher then declares the instance the record lives on, the
    # cross-instance remedy, and the next project deposit is allowed.
    dictWorkflow["sZenodoService"] = "zenodo"
    assert remoteBindingContext.fdictRequireBoundZenodoTarget(
        dictCtx, S_CONTAINER_ID, dictWorkflow, "project",
    )["sDepositionId"] == "201"


def testAFailedProjectPromotionWritesNoBinding(tApp, monkeypatch):
    _, dictCtx, sUuid = tApp
    from vaibify.gui.routes import syncRoutes
    _fdictPublishedOnTheSandbox(dictCtx)
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "sandbox", "sDepositionId": "2"})
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "zenodo"})
    _fnConfirmZenodo(sUuid, "environment", {"sInstance": "zenodo"})
    baBefore = _fsRecordBytes(sUuid)
    monkeypatch.setattr(syncRoutes, "fdictCommitWorkflowSave", lambda *tArgs: None)

    async def ftUploadRefused(*tArgs, **dictKeywords):
        return {"bSuccess": False, "sErrorType": "auth", "sMessage": "refused"}, "zenodo", {}

    monkeypatch.setattr(syncRoutes, "_ftPerformZenodoArchive", ftUploadRefused)
    for sGuard in ("_fnRequireNetworkAccess", "_fnRequireSandboxProjectDeposit",
                   "_fnRefuseWhileOtherRecordsAreDeclared",
                   "_fnRefuseWhileACompletionIsInFlight", "_fnRefuseMissingPushFiles",
                   "_fnRefuseBasenameCollisions"):
        monkeypatch.setattr(syncRoutes, sGuard, lambda *tArgs: None)
    monkeypatch.setattr(syncRoutes, "_flistResolveArchivePaths", lambda listPaths, sRoot: listPaths)
    app = FastAPI()
    syncRoutes._fnRegisterZenodoPromote(app, dictCtx)
    responseHttp = TestClient(app).post(
        f"/api/zenodo/{S_CONTAINER_ID}/promote", json={"listFilePaths": ["a.txt"]},
    )
    assert responseHttp.json()["bSuccess"] is False
    assert _fsRecordBytes(sUuid) == baBefore


# ---------------------------------------------------------------------------
# A record that is not schema version 2 is refused by name, everywhere
# ---------------------------------------------------------------------------


def _fsTodaysFormat(sUuid):
    return json.dumps({"sProjectUuid": sUuid, "dictWorkflows": {S_KEY: {
        "sWorkflowName": "study", "dictServices": {"zenodo": {
            "dictTarget": {"sInstance": "zenodo", "sConceptDoi": "", "sDepositionId": ""},
            "sConfirmedBy": "researcher", "sConfirmedAt": "2026-01-01T00:00:00+00:00",
            "dictEvidence": {},
        }},
    }}})


LIST_INVALID_RECORDS = [
    pytest.param(_fsTodaysFormat, id="the format before schema version 2"),
    pytest.param(
        lambda sUuid: json.dumps({"iSchemaVersion": 3, "sProjectUuid": sUuid, "dictWorkflows": {}}),
        id="a schema version this vaibify does not know",
    ),
    pytest.param(lambda sUuid: "{not json", id="an unparseable file"),
]


def _fnPlantRecord(sUuid, fsContent):
    os.makedirs(remoteBindings.fsBindingsDirectory(), exist_ok=True)
    with open(remoteBindings.fsBindingPath(sUuid), "w") as fileRecord:
        fileRecord.write(fsContent(sUuid))


@pytest.mark.parametrize("fsContent", LIST_INVALID_RECORDS)
def testAnInvalidRecordIsRefusedByNameAndNeverReadAsUnbound(fsContent, tPromotion, tApp):
    clientPromotion, listCalls, sUuid = tPromotion
    clientBindings = tApp[0]
    _fnPlantRecord(sUuid, fsContent)
    baPlanted = _fsRecordBytes(sUuid)
    with pytest.raises(BindingRecordInvalidError):
        remoteBindings.fdictReadBindings(sUuid)
    for responseHttp in (
        clientPromotion.post(S_PROMOTE_PATH),
        clientBindings.post(f"/api/bindings/{S_CONTAINER_ID}/confirm", json={
            "sService": "zenodo", "sRecordKind": "environment",
        }),
    ):
        assert responseHttp.status_code == 409, responseHttp.text
        dictDetail = responseHttp.json()["detail"]
        assert dictDetail["sError"] == "BINDING-RECORD-INVALID"
        assert sUuid + ".json" in dictDetail["sMessage"]
        assert "Move it aside" in dictDetail["sMessage"]
    dictDescribed = clientBindings.get(f"/api/bindings/{S_CONTAINER_ID}").json()
    assert dictDescribed["dictRecordState"]["sError"] == "BINDING-RECORD-INVALID"
    assert dictDescribed["listEntries"] == []
    assert listCalls == []
    assert _fsRecordBytes(sUuid) == baPlanted


@pytest.mark.falsification
def testAnInstanceThatIsNeitherSiteIsRefused():
    """A Zenodo entry names the sandbox or zenodo.org, never a slot name or a guess.

    Kills: ``_fnValidateInstance`` accepting any instance.
    """
    with pytest.raises(ValueError, match="sandbox or zenodo"):
        _fnConfirmZenodo("8" * 32, "project", {"sInstance": "production"})
    with pytest.raises(ValueError, match="sandbox or zenodo"):
        fdictConfirmedBinding("8" * 32, S_KEY, "zenodo", "environment", "")
    assert not os.path.exists(remoteBindings.fsBindingPath("8" * 32))


@pytest.mark.falsification
def testEveryRefusalFromTheRecordIsTheCodedConflict(tApp):
    """Nothing confirmed, a disagreement and an unreadable record are each a coded 409.

    Kills: the context letting a binding error escape uncoded, which a
    route answers as a 500.
    """
    _, dictCtx, sUuid = tApp
    dictWorkflow = _fdictPublishedOnTheSandbox(dictCtx)

    def fdictRefusal():
        with pytest.raises(HTTPException) as errorInfo:
            remoteBindingContext.fdictRequireBoundZenodoTarget(
                dictCtx, S_CONTAINER_ID, dictWorkflow, "project")
        assert errorInfo.value.status_code == 409
        return errorInfo.value.detail

    assert fdictRefusal()["sError"] == "BINDING-REQUIRED"
    _fnConfirmZenodo(sUuid, "project", {"sInstance": "sandbox", "sDepositionId": "999"})
    assert fdictRefusal()["sError"] == "BINDING-MISMATCH"
    _fnPlantRecord(sUuid, lambda sIdentity: "{not json")
    assert fdictRefusal()["sError"] == "BINDING-RECORD-INVALID"
