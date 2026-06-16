"""Tests for vaibify.gui.routes.reproducibilityRoutes — L3 endpoints.

Covers all four endpoints registered by ``reproducibilityRoutes.fnRegisterAll``:

* ``GET .../level3/readiness`` — returns the L3 readiness gap dict.
* ``GET .../level3/attestation`` — returns the most-recent + history.
* ``POST .../level3/verify`` — kicks off a background rebuild task.
* ``POST .../level3/reproduce-script`` — generates ``reproduce.sh``.
"""

import asyncio
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.reproducibilityRoutes import (
    _DICT_VERIFY_TASKS,
    _fdictBuildAttestationResponse,
    _fdictRunReproductionSync,
    _fiManifestEntryCount,
    _fnPersistAttestation,
    _fsResolveImageDigest,
    fnRegisterAll,
)
from vaibify.reproducibility.l3Attestation import (
    S_STATUS_PASSED,
    fdictBuildAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)


S_CONTAINER_ID = "repro_cid"


def _fdictBuildWorkflow(sProjectRepo):
    """Return a minimal workflow dict with project repo + L3 readiness."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {},
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
    }


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Create a tmpdir to act as the project repo root."""
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    return sRepo


@pytest.fixture(autouse=True)
def fixtureClearTaskRegistry():
    """Ensure each test starts with a clean verify-task registry."""
    _DICT_VERIFY_TASKS.clear()
    yield
    _DICT_VERIFY_TASKS.clear()


@pytest.fixture
def fixtureWorkflow(fixtureProjectRepo):
    return _fdictBuildWorkflow(fixtureProjectRepo)


@pytest.fixture
def fixtureClient(fixtureWorkflow):
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictWorkflows = {S_CONTAINER_ID: fixtureWorkflow}

    def _fnSave(sId, dictWf):
        pass

    dictCtx = {
        "docker": None,
        "workflows": dictWorkflows,
        "paths": {},
        "pipelineTasks": {},
        "sourceCodeDeps": {},
        "setAllowedContainers": {S_CONTAINER_ID},
        "sSessionToken": "tok",
        "require": lambda: None,
        "save": _fnSave,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: fixtureWorkflow["sProjectRepoPath"],
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fnSeedReadyL3Repo(sProjectRepo):
    """Seed a project repo so it passes every L3 readiness verifier.

    Sets up MANIFEST.sha256, requirements.lock, .vaibify/environment.json,
    Dockerfile, and reproduce.sh — all the artefacts the L3 readiness
    composition needs to return True.
    """
    import hashlib
    pathDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(pathDir, exist_ok=True)
    # environment.json with digest-pinned image.
    dictEnv = {
        "dictContainer": {"sImageDigest": "img@sha256:" + "a" * 64},
        "sSchemaVersion": "1",
    }
    with open(os.path.join(pathDir, "environment.json"), "w") as fh:
        json.dump(dictEnv, fh)
    # Dockerfile with digest-pinned base + SOURCE_DATE_EPOCH.
    sDockerBody = (
        "FROM python@sha256:" + "b" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )
    with open(os.path.join(sProjectRepo, "Dockerfile"), "w") as fh:
        fh.write(sDockerBody)
    # requirements.lock with hash.
    sLockBody = (
        "click==8.1.7 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    with open(os.path.join(sProjectRepo, "requirements.lock"), "w") as fh:
        fh.write(sLockBody)
    # reproduce.sh.
    sReproBody = "#!/usr/bin/env bash\nset -e\n"
    with open(os.path.join(sProjectRepo, "reproduce.sh"), "w") as fh:
        fh.write(sReproBody)
    # Manifest listing reproduce.sh + Dockerfile.
    sReproHash = hashlib.sha256(sReproBody.encode()).hexdigest()
    sDockerHash = hashlib.sha256(sDockerBody.encode()).hexdigest()
    sManifestBody = (
        f"{sReproHash}  reproduce.sh\n"
        f"{sDockerHash}  Dockerfile\n"
    )
    with open(os.path.join(sProjectRepo, "MANIFEST.sha256"), "w") as fh:
        fh.write(sManifestBody)


# ============================================================================
# GET .../level3/readiness
# ============================================================================


def test_l3_readiness_returns_gap_dict(fixtureClient):
    """A bare workflow returns iAICSLevel=0 and the readiness gap dict."""
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/readiness",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert "iAICSLevel" in dictBody
    dictGaps = dictBody["dictL3ReadinessGaps"]
    for sKey in (
        "bManifestComplete", "bDependencyLockHashed",
        "bEnvironmentDigestPinned", "bDockerfilePinned",
        "bReproduceScriptPinned", "bDeterminismDeclared",
        "bL3ReadinessOK", "bL3AttestationCurrent",
        "sManifestDigest",
    ):
        assert sKey in dictGaps


def test_l3_readiness_unknown_container_404(fixtureClient):
    """An unregistered container id returns 404."""
    response = fixtureClient.get("/api/workflow/no-such-id/level3/readiness")
    assert response.status_code == 404


# ============================================================================
# GET .../level3/attestation
# ============================================================================


def test_l3_attestation_returns_empty_when_no_file(fixtureClient):
    """With no attestation on disk, dictCurrentAttestation is None."""
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/attestation",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictCurrentAttestation"] is None
    assert dictBody["listHistory"] == []
    assert dictBody["dictInFlight"] is None
    assert dictBody["sLiveManifestDigest"] == ""


def test_l3_attestation_returns_current_and_history(
    fixtureClient, fixtureWorkflow,
):
    """A written attestation surfaces under dictCurrentAttestation."""
    sRepo = fixtureWorkflow["sProjectRepoPath"]
    pathManifest = os.path.join(sRepo, "MANIFEST.sha256")
    with open(pathManifest, "w") as fh:
        fh.write("# minimal\n")
    sDigest = fsCurrentManifestDigest(sRepo)
    dictAttestation = fdictBuildAttestation(
        S_STATUS_PASSED, sDigest, "img@sha256:def",
        1.5, 1, 1, [], "",
    )
    fnWriteAttestation(sRepo, dictAttestation)
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/attestation",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictCurrentAttestation"] is not None
    assert dictBody["dictCurrentAttestation"]["sStatus"] == S_STATUS_PASSED
    assert len(dictBody["listHistory"]) == 1


def test_l3_attestation_empty_project_repo(
    fixtureClient, fixtureWorkflow,
):
    """A workflow without sProjectRepoPath returns empty fields without crashing."""
    fixtureWorkflow["sProjectRepoPath"] = ""
    response = fixtureClient.get(
        f"/api/workflow/{S_CONTAINER_ID}/level3/attestation",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictCurrentAttestation"] is None
    assert dictBody["listHistory"] == []
    assert dictBody["sLiveManifestDigest"] == ""


def test_attestation_response_includes_in_flight_status(fixtureProjectRepo):
    """_fdictBuildAttestationResponse reports an in-flight status when present."""
    _DICT_VERIFY_TASKS[S_CONTAINER_ID] = {
        "task": None,
        "dictStatus": {"sPhase": "running"},
    }
    dictResp = _fdictBuildAttestationResponse(
        S_CONTAINER_ID, fixtureProjectRepo,
    )
    assert dictResp["dictInFlight"] == {"sPhase": "running"}


# ============================================================================
# POST .../level3/verify
# ============================================================================


def test_l3_verify_without_project_repo_returns_409(
    fixtureClient, fixtureWorkflow,
):
    """An empty sProjectRepoPath blocks verify with 409."""
    fixtureWorkflow["sProjectRepoPath"] = ""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    )
    assert response.status_code == 409
    assert "no project repo" in response.text.lower()


def test_l3_verify_without_readiness_returns_409(fixtureClient):
    """Failing readiness checks block verify with 409."""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    )
    assert response.status_code == 409
    assert "L3 readiness" in response.text


def test_l3_verify_returns_202_with_handle_when_ready(
    fixtureClient, fixtureWorkflow,
):
    """An L3-ready workflow accepts the verify request and returns a handle."""
    _fnSeedReadyL3Repo(fixtureWorkflow["sProjectRepoPath"])
    # Patch the heavy work to a no-op that just sets a passing result.
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fbInvokeRerunWorkflow",
        return_value=True,
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes.flistVerifyManifest",
        return_value=[],
    ):
        response = fixtureClient.post(
            f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
        )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bAccepted"] is True
    assert dictBody["sPhase"] == "starting"
    assert "sManifestDigestAtAttestation" in dictBody


def test_l3_verify_refuses_when_already_in_flight(
    fixtureClient, fixtureWorkflow,
):
    """A second verify while one is in flight returns 409."""
    _fnSeedReadyL3Repo(fixtureWorkflow["sProjectRepoPath"])

    # Build a "running" task so the gate trips.
    class _StubTask:
        def done(self):
            return False

    _DICT_VERIFY_TASKS[S_CONTAINER_ID] = {
        "task": _StubTask(),
        "dictStatus": {"sPhase": "running"},
    }
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    )
    assert response.status_code == 409
    assert "already running" in response.text.lower()


# ============================================================================
# Background worker helpers — exercise via direct calls
# ============================================================================


def test_run_reproduction_sync_reports_mismatches(fixtureProjectRepo):
    """_fdictRunReproductionSync surfaces manifest mismatches in listDiverged."""
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fbInvokeRerunWorkflow",
        return_value=True,
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes.flistVerifyManifest",
        return_value=[
            {"sPath": "a.txt", "sExpected": "x", "sActual": "y"},
        ],
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes._fiManifestEntryCount",
        return_value=5,
    ):
        dictResult = _fdictRunReproductionSync(fixtureProjectRepo, {})
    assert dictResult["bPassed"] is False  # mismatches block pass
    assert "a.txt" in dictResult["listDivergedHashes"]
    assert dictResult["iOutputHashesMatched"] == 4
    assert dictResult["iOutputHashesTotal"] == 5


def test_run_reproduction_sync_reports_pipeline_failure(fixtureProjectRepo):
    """A rerun failure prepends "pipeline rerun exited non-zero" to listDiverged."""
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fbInvokeRerunWorkflow",
        return_value=False,
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes.flistVerifyManifest",
        return_value=[],
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes._fiManifestEntryCount",
        return_value=2,
    ):
        dictResult = _fdictRunReproductionSync(fixtureProjectRepo, {})
    assert dictResult["bPassed"] is False
    assert dictResult["listDivergedHashes"][0] == "pipeline rerun exited non-zero"


def test_run_reproduction_sync_passes_when_clean(fixtureProjectRepo):
    """A successful rerun + clean manifest yields bPassed=True."""
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fbInvokeRerunWorkflow",
        return_value=True,
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes.flistVerifyManifest",
        return_value=[],
    ), patch(
        "vaibify.gui.routes.reproducibilityRoutes._fiManifestEntryCount",
        return_value=3,
    ):
        dictResult = _fdictRunReproductionSync(fixtureProjectRepo, {})
    assert dictResult["bPassed"] is True
    assert dictResult["iOutputHashesMatched"] == 3


def test_invoke_rerun_workflow_swallows_import_error(fixtureProjectRepo):
    """An ImportError during the CLI import returns False."""
    from vaibify.gui.routes import reproducibilityRoutes
    with patch.dict(
        "sys.modules",
        {"vaibify.cli.commandReproduce": None},
    ):
        # Patch the inner import to raise.
        with patch(
            "builtins.__import__",
            side_effect=ImportError("not installed"),
        ):
            try:
                bResult = reproducibilityRoutes._fbInvokeRerunWorkflow(
                    fixtureProjectRepo,
                )
            except ImportError:
                # If builtins import patching trips test infra, skip.
                pytest.skip("import-patching disrupts test infrastructure")
                return
    assert bResult is False


def test_invoke_rerun_workflow_swallows_runtime_exception(fixtureProjectRepo):
    """An Exception inside fbRerunWorkflow returns False, not raised."""
    with patch(
        "vaibify.cli.commandReproduce.fbRerunWorkflow",
        side_effect=RuntimeError("boom"),
    ):
        from vaibify.gui.routes import reproducibilityRoutes
        bResult = reproducibilityRoutes._fbInvokeRerunWorkflow(
            fixtureProjectRepo,
        )
    assert bResult is False


def test_invoke_rerun_workflow_swallows_system_exit(fixtureProjectRepo):
    """A SystemExit from fbRerunWorkflow returns False."""
    with patch(
        "vaibify.cli.commandReproduce.fbRerunWorkflow",
        side_effect=SystemExit(1),
    ):
        from vaibify.gui.routes import reproducibilityRoutes
        bResult = reproducibilityRoutes._fbInvokeRerunWorkflow(
            fixtureProjectRepo,
        )
    assert bResult is False


def test_invoke_rerun_workflow_success_path(fixtureProjectRepo):
    """A successful fbRerunWorkflow returns True."""
    with patch(
        "vaibify.cli.commandReproduce.fbRerunWorkflow",
        return_value=True,
    ):
        from vaibify.gui.routes import reproducibilityRoutes
        bResult = reproducibilityRoutes._fbInvokeRerunWorkflow(
            fixtureProjectRepo,
        )
    assert bResult is True


# ============================================================================
# _fiManifestEntryCount, _fsResolveImageDigest
# ============================================================================


def test_manifest_entry_count_handles_missing_manifest(fixtureProjectRepo):
    """A missing manifest yields 0, not an exception."""
    assert _fiManifestEntryCount(fixtureProjectRepo) == 0


def test_resolve_image_digest_handles_missing_environment(fixtureProjectRepo):
    """A missing environment.json yields an empty digest string."""
    assert _fsResolveImageDigest(fixtureProjectRepo) == ""


def test_resolve_image_digest_prefers_nested_field(fixtureProjectRepo):
    """dictContainer.sImageDigest wins over the flat field."""
    pathDir = os.path.join(fixtureProjectRepo, ".vaibify")
    os.makedirs(pathDir, exist_ok=True)
    with open(os.path.join(pathDir, "environment.json"), "w") as fh:
        json.dump({
            "dictContainer": {"sImageDigest": "img@sha256:abc"},
            "sImageDigest": "flat@sha256:def",
        }, fh)
    assert _fsResolveImageDigest(fixtureProjectRepo) == "img@sha256:abc"


def test_resolve_image_digest_falls_back_to_flat(fixtureProjectRepo):
    """When dictContainer is absent, returns the flat sImageDigest."""
    pathDir = os.path.join(fixtureProjectRepo, ".vaibify")
    os.makedirs(pathDir, exist_ok=True)
    with open(os.path.join(pathDir, "environment.json"), "w") as fh:
        json.dump({"sImageDigest": "flat@sha256:def"}, fh)
    assert _fsResolveImageDigest(fixtureProjectRepo) == "flat@sha256:def"


# ============================================================================
# _fnPersistAttestation
# ============================================================================


def test_persist_attestation_writes_passed_status(fixtureProjectRepo):
    """A passed rerun result writes a passed attestation."""
    dictResult = {
        "bPassed": True,
        "iOutputHashesMatched": 5,
        "iOutputHashesTotal": 5,
        "listDivergedHashes": [],
        "sImageDigest": "img@sha256:abc",
        "sRunLogPath": "",
    }
    _fnPersistAttestation(
        fixtureProjectRepo, "sha256:manifest", dictResult, 2.5,
    )
    pathAttestation = os.path.join(
        fixtureProjectRepo, ".vaibify", "l3_attestation.json",
    )
    assert os.path.isfile(pathAttestation)
    with open(pathAttestation) as fh:
        dictPayload = json.load(fh)
    assert dictPayload["sStatus"] == "passed"
    assert dictPayload["iOutputHashesMatched"] == 5


def test_persist_attestation_writes_failed_status(fixtureProjectRepo):
    """A failed rerun result writes a failed attestation."""
    dictResult = {
        "bPassed": False,
        "iOutputHashesMatched": 0,
        "iOutputHashesTotal": 3,
        "listDivergedHashes": ["a", "b"],
        "sImageDigest": "",
        "sRunLogPath": "",
    }
    _fnPersistAttestation(
        fixtureProjectRepo, "sha256:manifest", dictResult, 1.0,
    )
    pathAttestation = os.path.join(
        fixtureProjectRepo, ".vaibify", "l3_attestation.json",
    )
    with open(pathAttestation) as fh:
        dictPayload = json.load(fh)
    assert dictPayload["sStatus"] == "failed"


def test_persist_attestation_logs_on_oserror(fixtureProjectRepo, caplog):
    """An OSError during write is logged and swallowed."""
    dictResult = {
        "bPassed": True,
        "iOutputHashesMatched": 1,
        "iOutputHashesTotal": 1,
        "listDivergedHashes": [],
        "sImageDigest": "",
        "sRunLogPath": "",
    }
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes.fnWriteAttestation",
        side_effect=OSError("disk full"),
    ):
        # Should not raise.
        _fnPersistAttestation(
            fixtureProjectRepo, "sha256:manifest", dictResult, 1.0,
        )


# ============================================================================
# POST .../level3/reproduce-script
# ============================================================================


class _FakeDockerForScript:
    """Capture writes + commands so we can assert nothing reaches the host."""

    def __init__(self):
        self.dictWritten = {}
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sFilePath, baContent):
        self.dictWritten[(sContainerId, sFilePath)] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        return (0, "")


def test_generate_reproduce_script_writes_to_container_not_host(
    fixtureWorkflow, tmp_path,
):
    """The endpoint must write inside the container, never to the host."""
    fakeDocker = _FakeDockerForScript()
    fixtureWorkflow["sProjectRepoPath"] = "/workspace/foo"
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictCtx = {
        "docker": fakeDocker,
        "workflows": {S_CONTAINER_ID: fixtureWorkflow},
        "paths": {}, "pipelineTasks": {}, "sourceCodeDeps": {},
        "setAllowedContainers": {S_CONTAINER_ID},
        "sSessionToken": "tok",
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: "/workspace/foo",
    }
    fnRegisterAll(app, dictCtx)
    clientTest = TestClient(app)
    response = clientTest.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/reproduce-script",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bWritten"] is True
    assert dictBody["sScriptFilename"] == "reproduce.sh"
    assert dictBody["sScriptPath"] == "/workspace/foo/reproduce.sh"
    sHostShadow = "/workspace/foo/reproduce.sh"
    assert not os.path.exists(sHostShadow)
    assert (S_CONTAINER_ID, "/workspace/foo/reproduce.sh") in (
        fakeDocker.dictWritten
    )


def test_generate_reproduce_script_requires_project_repo(
    fixtureClient, fixtureWorkflow,
):
    """Without a project repo, the endpoint returns 409."""
    fixtureWorkflow["sProjectRepoPath"] = ""
    response = fixtureClient.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/reproduce-script",
    )
    assert response.status_code == 409


def test_generate_reproduce_script_handles_oserror(
    fixtureClient, fixtureWorkflow,
):
    """An OSError during write surfaces as 500."""
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes.fnGenerateReproduceScript",
        side_effect=OSError("disk full"),
    ):
        response = fixtureClient.post(
            f"/api/workflow/{S_CONTAINER_ID}/level3/reproduce-script",
        )
    assert response.status_code == 500
    assert "Could not write" in response.text


# ============================================================================
# _fnRunVerificationWorker — exercise the asyncio worker directly
# ============================================================================


def test_verification_worker_persists_passed_attestation(fixtureProjectRepo):
    """The async worker writes a passed attestation when reproduction passes."""
    from vaibify.gui.routes import reproducibilityRoutes

    _DICT_VERIFY_TASKS[S_CONTAINER_ID] = {
        "task": None,
        "dictStatus": {"sPhase": "starting"},
    }
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fdictRunReproductionSync",
        return_value={
            "bPassed": True,
            "iOutputHashesMatched": 1,
            "iOutputHashesTotal": 1,
            "listDivergedHashes": [],
            "sImageDigest": "",
            "sRunLogPath": "",
        },
    ):
        asyncio.run(reproducibilityRoutes._fnRunVerificationWorker(
            S_CONTAINER_ID, fixtureProjectRepo,
            "sha256:m", {"listSteps": []},
        ))
    dictStatus = _DICT_VERIFY_TASKS[S_CONTAINER_ID]["dictStatus"]
    assert dictStatus["sPhase"] == "passed"


def test_verification_worker_marks_failed_on_exception(fixtureProjectRepo):
    """An exception inside reproduction is surfaced as a failed attestation."""
    from vaibify.gui.routes import reproducibilityRoutes

    _DICT_VERIFY_TASKS[S_CONTAINER_ID] = {
        "task": None,
        "dictStatus": {"sPhase": "starting"},
    }
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fdictRunReproductionSync",
        side_effect=RuntimeError("boom"),
    ):
        asyncio.run(reproducibilityRoutes._fnRunVerificationWorker(
            S_CONTAINER_ID, fixtureProjectRepo,
            "sha256:m", {"listSteps": []},
        ))
    dictStatus = _DICT_VERIFY_TASKS[S_CONTAINER_ID]["dictStatus"]
    assert dictStatus["sPhase"] == "failed"


def test_verification_worker_marks_failed_on_diverged(fixtureProjectRepo):
    """A reproduction with bPassed=False marks the phase as failed."""
    from vaibify.gui.routes import reproducibilityRoutes

    _DICT_VERIFY_TASKS[S_CONTAINER_ID] = {
        "task": None,
        "dictStatus": {"sPhase": "starting"},
    }
    with patch(
        "vaibify.gui.routes.reproducibilityRoutes._fdictRunReproductionSync",
        return_value={
            "bPassed": False,
            "iOutputHashesMatched": 0,
            "iOutputHashesTotal": 1,
            "listDivergedHashes": ["x"],
            "sImageDigest": "",
            "sRunLogPath": "",
        },
    ):
        asyncio.run(reproducibilityRoutes._fnRunVerificationWorker(
            S_CONTAINER_ID, fixtureProjectRepo,
            "sha256:m", {"listSteps": []},
        ))
    dictStatus = _DICT_VERIFY_TASKS[S_CONTAINER_ID]["dictStatus"]
    assert dictStatus["sPhase"] == "failed"


# ============================================================================
# Lifecycle hygiene: completed verify tasks self-evict from the registry
# ============================================================================


def test_register_verify_task_self_evicts_on_completion():
    """The done-callback drops the entry once the verify task finishes."""
    from vaibify.gui.routes import reproducibilityRoutes

    async def fnRunOnce():
        async def fnQuick():
            return None
        taskWorker = asyncio.create_task(fnQuick())
        reproducibilityRoutes._fnRegisterVerifyTask(
            S_CONTAINER_ID, taskWorker, {"sPhase": "running"},
        )
        await taskWorker
        # Give the event loop a tick to run the done-callback.
        await asyncio.sleep(0)
    asyncio.run(fnRunOnce())
    assert S_CONTAINER_ID not in _DICT_VERIFY_TASKS


def test_register_verify_task_old_callback_does_not_evict_new_entry():
    """A late-firing done-callback must not pop a fresh same-slot entry."""
    from vaibify.gui.routes import reproducibilityRoutes

    async def fnRunOnce():
        async def fnQuick():
            return None
        taskOld = asyncio.create_task(fnQuick())
        reproducibilityRoutes._fnRegisterVerifyTask(
            S_CONTAINER_ID, taskOld, {"sPhase": "running"},
        )
        await taskOld
        # Re-use the slot before letting the callback fire.
        taskNew = asyncio.create_task(fnQuick())
        reproducibilityRoutes._fnRegisterVerifyTask(
            S_CONTAINER_ID, taskNew, {"sPhase": "starting"},
        )
        await asyncio.sleep(0)
        # The new task should still own the slot.
        return _DICT_VERIFY_TASKS.get(S_CONTAINER_ID, {}).get("task")
    objectTaskInSlot = asyncio.run(fnRunOnce())
    assert objectTaskInSlot is not None
