"""Containerizing from the author's pinned image: obtain, prove, stack, commit.

A rebuild from the Dockerfile produces a different digest and cannot
reproduce the author's bytes. These tests drive the lane that OBTAINS
the pinned image instead, piece by piece and end to end against a fake
image store, and pin the guards the plan named: the baseline is proven
against the image and never taken from a comment line, unproven never
fails open, the origin record is the commit point the launch guard
admits a start on, and a derived image is never reported as the pin.
Every falsification test names the mutation it fails against.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from tests.reproductionSourceFixtures import fdictBuildEnvelope, fnWriteJson
from vaibify.config import imageOrigins
from vaibify.config import registryManager
from vaibify.config.imageOrigins import (
    S_PINNED_BASE_LABEL,
    fdictBuildOriginRecord,
    fdictJudgeOriginRecord,
    fdictReadLiveOriginRecord,
    fdictReadOriginRecord,
    fnRemoveOriginRecord,
    fnRenameOriginRecord,
    fnWriteOriginRecord,
)
from vaibify.docker import containerManager
from vaibify.docker import imageBuilder
from vaibify.docker import pinnedImageAcquisition
from vaibify.docker.imageBuilder import (
    T_AGENT_OVERLAY_NAMES,
    T_BASE_OVERLAY_NAMES,
    T_PREREQUISITE_OVERLAY_NAMES,
    _DICT_OVERLAY_DOCKERFILE_MAP,
    _LIST_OVERLAY_ORDER,
    _flistRecipeLabelArguments,
    flistOverlaysForFeatures,
)
from vaibify.docker.pinnedImageAcquisition import (
    PinnedImageAcquisitionRefusedError,
    fdictAcquireForProject,
    flistProveOverlayBaseline,
    flistResolveDifferentialChain,
)
from vaibify.gui import buildRoutes
from vaibify.gui import pipelineServer
from vaibify.gui import registryRoutes
from vaibify.gui.pinnedEnvironmentConversion import (
    T_BASE_FEATURE_KEYS as _T_BASE_FEATURE_KEYS,
    T_BASE_YAML_KEYS as _T_BASE_YAML_KEYS,
    T_RUNTIME_YAML_KEYS as _T_RUNTIME_YAML_KEYS,
    fdictOverlayRuntimeFieldsOnly as _fdictOverlayRuntimeFieldsOnly,
)
from vaibify.gui.registryRoutes import (
    ConvertToContainerRequest,
    _fdictBuildYamlFromRequest,
)
from vaibify.reproducibility import shadowRerun
from vaibify.reproducibility.dockerfileComposer import (
    S_OVERLAYS_IMAGE_LABEL,
    S_RECIPE_IMAGE_LABEL,
    flistExtractOverlayOrder,
    flistParseOverlaysLabel,
    fsComputeRecipeFingerprint,
    fsRenderOverlaysLabelValue,
)
from vaibify.reproducibility.reproductionSource import (
    fdictDescribePinnedEnvironment,
)


S_BASE_ID = "sha256:" + "b" * 64
S_DERIVED_ID = "sha256:" + "d" * 64
S_PIN = "registry.example/project@sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def fnIsolateHostState(tmp_path, monkeypatch):
    """Registry and origin records under tmp, never the researcher's own."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    os.makedirs(sRegistryDirectory)
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )
    sOrigins = str(tmp_path / ".vaibify" / "imageOrigins")
    monkeypatch.setattr(imageOrigins, "fsOriginsDirectory", lambda: (
        os.makedirs(sOrigins, exist_ok=True) or sOrigins
    ))


def _fdictRecord(sRunning=S_BASE_ID, listOverlays=(), sObtainedFrom="archive"):
    return fdictBuildOriginRecord(
        S_PIN, S_BASE_ID, sRunning, list(listOverlays), sObtainedFrom,
        "sandbox", "10.5072/zenodo.1", "linux/amd64", "linux/amd64", False,
    )


# ---------------------------------------------------------------------
# The origin record and its staleness rule
# ---------------------------------------------------------------------


def test_the_record_round_trips_renames_and_is_removed(tmp_path):
    fnWriteOriginRecord("proj", _fdictRecord())
    assert fdictReadOriginRecord("proj")["sBaseImageId"] == S_BASE_ID
    assert oct(os.stat(
        os.path.join(imageOrigins.fsOriginsDirectory(), "proj.json"),
    ).st_mode & 0o777) == "0o600"
    fnRenameOriginRecord("proj", "renamed")
    assert fdictReadOriginRecord("proj") is None
    assert fdictReadOriginRecord("renamed")["sPinnedImageReference"] == S_PIN
    fnRemoveOriginRecord("renamed")
    assert fdictReadOriginRecord("renamed") is None
    fnRemoveOriginRecord("renamed")
    with pytest.raises(ValueError):
        fnWriteOriginRecord("../escape", _fdictRecord())


def test_a_record_is_fresh_only_for_the_base_or_a_labelled_derivative():
    dictRecord = _fdictRecord(sRunning=S_DERIVED_ID, listOverlays=["claude"])
    assert fdictJudgeOriginRecord(None, {"sId": S_BASE_ID})["bStale"]
    assert fdictJudgeOriginRecord(dictRecord, None)["bStale"]
    assert fdictJudgeOriginRecord(
        dictRecord, {"sId": "sha256:" + "0" * 64, "dictLabels": {}},
    )["bStale"]
    assert not fdictJudgeOriginRecord(
        _fdictRecord(), {"sId": S_BASE_ID, "dictLabels": {}},
    )["bStale"]
    assert not fdictJudgeOriginRecord(dictRecord, {
        "sId": S_DERIVED_ID, "dictLabels": {S_PINNED_BASE_LABEL: S_BASE_ID},
    })["bStale"]


@pytest.mark.falsification
def test_a_derivative_without_the_base_label_is_stale():
    """A docker build outside vaibify must not inherit the archive's provenance.

    Kills: accepting any running image whose ID the record names.
    """
    dictRecord = _fdictRecord(sRunning=S_DERIVED_ID, listOverlays=["claude"])
    dictJudgement = fdictJudgeOriginRecord(
        dictRecord, {"sId": S_DERIVED_ID, "dictLabels": {}},
    )
    assert dictJudgement["bStale"]
    assert "labelled" in dictJudgement["sReason"]
    fnWriteOriginRecord("proj", dictRecord)
    assert fdictReadLiveOriginRecord(
        "proj", {"sId": S_DERIVED_ID, "dictLabels": {}},
    ) is None


# ---------------------------------------------------------------------
# The composer's header and label
# ---------------------------------------------------------------------


def test_the_header_line_is_read_back_as_a_claim():
    assert flistExtractOverlayOrder("#   base + overlays in order: (none)\n") == []
    assert flistExtractOverlayOrder(
        "# x\n#   base + overlays in order: node, claude\n",
    ) == ["node", "claude"]
    assert flistExtractOverlayOrder("# no header\n") is None


@pytest.mark.falsification
def test_a_malformed_overlays_label_is_refused_not_repaired():
    """Kills: accepting a label that lists overlays out of canonical order."""
    listOrder = ["node", "claude", "gemini"]
    assert flistParseOverlaysLabel("node,gemini", listOrder) == ["node", "gemini"]
    assert flistParseOverlaysLabel("", listOrder) == []
    with pytest.raises(ValueError):
        flistParseOverlaysLabel("gemini,node", listOrder)
    with pytest.raises(ValueError):
        flistParseOverlaysLabel("node,node", listOrder)
    with pytest.raises(ValueError):
        flistParseOverlaysLabel("node,mystery", listOrder)


def test_the_build_stamps_the_overlays_label_beside_the_recipe():
    listArguments = _flistRecipeLabelArguments("abc", ["node", "claude"])
    assert f"{S_RECIPE_IMAGE_LABEL}=abc" in listArguments
    assert f"{S_OVERLAYS_IMAGE_LABEL}=node,claude" in listArguments
    assert _flistRecipeLabelArguments("", []) == [
        "--label", f"{S_OVERLAYS_IMAGE_LABEL}=",
    ]
    assert _flistRecipeLabelArguments("", None) == []


def test_every_overlay_is_classified_and_every_dockerfile_is_an_overlay():
    """Adding an overlay Dockerfile without classifying it fails here."""
    from vaibify.resources import fpathContainerImageRoot
    setClassified = (
        set(T_AGENT_OVERLAY_NAMES) | set(T_BASE_OVERLAY_NAMES)
        | set(T_PREREQUISITE_OVERLAY_NAMES)
    )
    assert setClassified == set(_LIST_OVERLAY_ORDER)
    assert set(_DICT_OVERLAY_DOCKERFILE_MAP) == set(_LIST_OVERLAY_ORDER)
    sImageDirectory = str(fpathContainerImageRoot())
    setShipped = {
        sName for sName in os.listdir(sImageDirectory)
        if sName.startswith("Dockerfile.")
    } | {
        "overlays/" + sName
        for sName in os.listdir(os.path.join(sImageDirectory, "overlays"))
        if sName.endswith(".dockerfile")
    }
    assert setShipped == set(_DICT_OVERLAY_DOCKERFILE_MAP.values())


# ---------------------------------------------------------------------
# Proving the baseline against the image
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_labelled_image_that_disagrees_with_the_recipe_is_refused_even_without_additions():
    """Kills: skipping the comparison when no agent was requested."""
    dictLabels = {S_OVERLAYS_IMAGE_LABEL: "node,claude"}
    assert flistProveOverlayBaseline(dictLabels, ["node", "claude"], []) == [
        "node", "claude",
    ]
    with pytest.raises(PinnedImageAcquisitionRefusedError) as excinfo:
        flistProveOverlayBaseline(dictLabels, ["claude"], [])
    assert "node, claude" in str(excinfo.value) and "claude]" in str(excinfo.value)
    with pytest.raises(PinnedImageAcquisitionRefusedError):
        flistProveOverlayBaseline(dictLabels, ["node", "claude", "gemini"], ["codex"])
    with pytest.raises(PinnedImageAcquisitionRefusedError) as excinfo:
        flistProveOverlayBaseline({S_OVERLAYS_IMAGE_LABEL: "claude,node"}, ["node", "claude"], [])
    assert "malformed" in str(excinfo.value)


def _fpathShippedDockerfiles(tmp_path):
    pathDocker = tmp_path / "docker"
    pathDocker.mkdir()
    (pathDocker / "Dockerfile").write_text("FROM ubuntu\n")
    (pathDocker / "Dockerfile.node").write_text("FROM ${BASE_IMAGE}\nRUN apt\n")
    (pathDocker / "Dockerfile.claude").write_text("FROM ${BASE_IMAGE}\nRUN npm\n")
    return str(pathDocker)


def test_a_recipe_fingerprint_over_the_shipped_texts_proves_the_candidate(tmp_path):
    sDockerDir = _fpathShippedDockerfiles(tmp_path)
    sFingerprint = fsComputeRecipeFingerprint(
        "FROM ubuntu\n",
        [("node", "FROM ${BASE_IMAGE}\nRUN apt\n"),
         ("claude", "FROM ${BASE_IMAGE}\nRUN npm\n")],
    )
    assert imageBuilder.fsComputeShippedRecipeFingerprint(
        sDockerDir, ["node", "claude"],
    ) == sFingerprint
    dictLabels = {S_RECIPE_IMAGE_LABEL: sFingerprint}
    assert flistProveOverlayBaseline(
        dictLabels, ["node", "claude"], ["gemini"], sDockerDir,
    ) == ["node", "claude"]
    # A header line edited to claim a different set fails the proof.
    with pytest.raises(PinnedImageAcquisitionRefusedError):
        flistProveOverlayBaseline(dictLabels, ["claude"], ["gemini"], sDockerDir)


@pytest.mark.falsification
def test_an_unproven_baseline_never_fails_open(tmp_path):
    """No label, no proof: fine alone, refused the moment something must stack.

    Kills: treating an absent label as a proven candidate.
    """
    assert flistProveOverlayBaseline({}, ["claude"], []) == []
    with pytest.raises(PinnedImageAcquisitionRefusedError) as excinfo:
        flistProveOverlayBaseline(
            {S_RECIPE_IMAGE_LABEL: "0" * 64}, ["claude"], ["gemini"],
            _fpathShippedDockerfiles(tmp_path),
        )
    assert "cannot be proven" in str(excinfo.value)
    assert "without adding agents" in str(excinfo.value)


def _fsWriteAuthorConfig(tmp_path, dictFeatures):
    pathConfig = tmp_path / "vaibify.yml"
    pathConfig.write_text(
        "projectName: author\npythonVersion: '3.12'\nfeatures:\n"
        + "".join(f"  {sKey}: {str(bValue).lower()}\n"
                  for sKey, bValue in dictFeatures.items())
    )
    return str(pathConfig)


@pytest.mark.falsification
def test_the_chain_is_differential_against_the_proven_set(tmp_path):
    """Kills: stacking the whole merged chain instead of the difference."""
    sConfigPath = _fsWriteAuthorConfig(tmp_path, {"claude": True})
    assert flistResolveDifferentialChain(sConfigPath, ["claude"], ["gemini"]) == [
        "node", "gemini",
    ]
    assert flistResolveDifferentialChain(
        sConfigPath, ["node", "claude"], ["gemini"],
    ) == ["gemini"]
    assert flistResolveDifferentialChain(sConfigPath, ["claude"], []) == []
    assert flistOverlaysForFeatures({"bGemini": True}) == ["node", "gemini"]


# ---------------------------------------------------------------------
# The lane end to end, against a fake image store
# ---------------------------------------------------------------------


class _FakeImage:
    def __init__(self, sId, dictLabels):
        self.id = sId
        self.attrs = {"Os": "linux", "Architecture": "amd64",
                      "Config": {"Labels": dictLabels}}
        self.listTags = []

    def tag(self, sRepository, tag=None):
        self.listTags.append(f"{sRepository}:{tag}")


class _FakeStore:
    def __init__(self, dictLabels):
        self.dictHeld = {S_BASE_ID: _FakeImage(S_BASE_ID, dictLabels)}
        self.images = self

    def get(self, sReference):
        if sReference not in self.dictHeld:
            raise KeyError(sReference)
        return self.dictHeld[sReference]


def _fdictRegisterObtainedProject(tmp_path, listAuthorOverlays, listAdditional,
                                  bAllowEmulation=False):
    sDirectory = str(tmp_path / "clone")
    os.makedirs(sDirectory)
    sConfigPath = os.path.join(sDirectory, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(
            "projectName: proj\npythonVersion: '3.12'\nfeatures:\n"
            "  claude: true\n  latex: true\n"
        )
    fnWriteJson(sDirectory, ".vaibify/environment.json", fdictBuildEnvelope())
    registryManager.fnAddProject(sDirectory, sMode="host")
    registryManager.fnConvertProjectToContainer("proj", "proj", {
        "sSource": "archive", "bAllowEmulation": bAllowEmulation,
        "sPinnedImageReference": S_PIN, "sRequiredPlatform": "linux/amd64",
        "listAuthorOverlays": list(listAuthorOverlays),
        "sAuthorRecipeFingerprint": "", "listAdditionalAgents": list(listAdditional),
        "listResolvedOverlays": None,
    })
    return registryManager.fdictGetProject("proj")


def _fnPatchTheChain(monkeypatch, listAcquisitionCalls):
    monkeypatch.setattr(
        pinnedImageAcquisition, "_fdictRecheckTheClone",
        lambda dictProject, dictSource, fnReport: {
            "bObtainable": True, "sRefusal": "", "sPinnedImageReference": S_PIN,
            "sRequiredPlatform": "linux/amd64", "bDepositOnRecord": True,
            "sDepositVersionDoi": "10.5072/zenodo.1", "sZenodoService": "sandbox",
        },
    )

    def fdictAcquire(dictEnvironment, sPlatform, fnStatus, bEmulation, store):
        listAcquisitionCalls.append(bEmulation)
        return {
            "sImageReference": S_BASE_ID, "sImageId": S_BASE_ID,
            "sPinnedImageReference": S_PIN, "sObtainedFrom": "archive",
            "sRequiredPlatform": sPlatform, "sObtainedPlatform": sPlatform,
            "sDaemonArchitecture": "amd64", "bEmulated": False, "listAttempts": [],
        }
    monkeypatch.setattr(
        "vaibify.reproducibility.imageAcquisition.fdictAcquirePinnedImage",
        fdictAcquire,
    )


def test_an_image_with_no_additions_runs_as_obtained_and_is_committed(
    tmp_path, monkeypatch,
):
    dictProject = _fdictRegisterObtainedProject(tmp_path, ["claude"], [])
    listCalls = []
    _fnPatchTheChain(monkeypatch, listCalls)
    store = _FakeStore({S_OVERLAYS_IMAGE_LABEL: "claude"})
    listStacked = []
    monkeypatch.setattr(
        pinnedImageAcquisition, "_fsStackAndResolve",
        lambda *aArgs, **kwargs: listStacked.append(aArgs) or S_BASE_ID,
    )
    listLines = []
    dictRecord = fdictAcquireForProject(
        dictProject, False, listLines.append, dockerDisposable=store,
    )
    assert dictRecord["sBaseImageId"] == S_BASE_ID
    assert dictRecord["sRunningImageId"] == S_BASE_ID
    assert dictRecord["listResolvedOverlays"] == ["claude"]
    assert store.dictHeld[S_BASE_ID].listTags == ["proj:latest"]
    assert fdictReadOriginRecord("proj")["sObtainedFrom"] == "archive"
    assert registryManager.fdictGetProject("proj")["dictImageSource"][
        "listResolvedOverlays"
    ] == ["claude"]
    assert listStacked == [] or listStacked[0][2] == []


@pytest.mark.falsification
def test_emulation_needs_both_consents(tmp_path, monkeypatch):
    """Kills: trusting the per-attempt body alone."""
    dictProject = _fdictRegisterObtainedProject(
        tmp_path, [], [], bAllowEmulation=False,
    )
    listCalls = []
    _fnPatchTheChain(monkeypatch, listCalls)
    monkeypatch.setattr(
        pinnedImageAcquisition, "_fsStackAndResolve",
        lambda *aArgs, **kwargs: S_BASE_ID,
    )
    fdictAcquireForProject(
        dictProject, True, dockerDisposable=_FakeStore({S_OVERLAYS_IMAGE_LABEL: ""}),
    )
    assert listCalls == [False]


@pytest.mark.falsification
def test_the_origin_record_is_written_last(tmp_path, monkeypatch):
    """A failure after the tag leaves no record; the next click retries.

    Kills: writing the record before the config and registry describe it.
    """
    dictProject = _fdictRegisterObtainedProject(tmp_path, ["claude"], ["gemini"])
    _fnPatchTheChain(monkeypatch, [])
    store = _FakeStore({S_OVERLAYS_IMAGE_LABEL: "claude"})
    store.dictHeld[S_DERIVED_ID] = _FakeImage(
        S_DERIVED_ID, {S_PINNED_BASE_LABEL: S_BASE_ID},
    )
    monkeypatch.setattr(
        pinnedImageAcquisition, "_fsStackAndResolve",
        lambda *aArgs, **kwargs: S_DERIVED_ID,
    )
    monkeypatch.setattr(
        pinnedImageAcquisition, "_fnWriteAgentInstallKeys",
        lambda *aArgs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        fdictAcquireForProject(dictProject, False, dockerDisposable=store)
    assert store.dictHeld[S_DERIVED_ID].listTags == ["proj:latest"]
    assert fdictReadOriginRecord("proj") is None


def test_a_changed_pin_refuses_before_anything_is_obtained(tmp_path, monkeypatch):
    dictProject = _fdictRegisterObtainedProject(tmp_path, [], [])
    monkeypatch.setattr(
        "vaibify.reproducibility.reproductionSource.fdictDescribePinnedEnvironment",
        lambda sDirectory: {
            "bObtainable": True, "sRefusal": "",
            "sPinnedImageReference": "other@sha256:" + "c" * 64,
            "sRequiredPlatform": "linux/amd64", "bDepositOnRecord": False,
            "sDepositVersionDoi": "", "sZenodoService": "",
        },
    )
    with pytest.raises(PinnedImageAcquisitionRefusedError) as excinfo:
        fdictAcquireForProject(dictProject, False, dockerDisposable=_FakeStore({}))
    assert "different image" in str(excinfo.value)


# ---------------------------------------------------------------------
# The registry transitions and the build refusal
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_switching_to_building_clears_the_source_and_the_record_together(tmp_path):
    """Kills: clearing the registry's source and leaving the record."""
    _fdictRegisterObtainedProject(tmp_path, [], [])
    fnWriteOriginRecord("proj", _fdictRecord())
    registryManager.fnSwitchProjectToBuilding("proj")
    assert "dictImageSource" not in registryManager.fdictGetProject("proj")
    assert fdictReadOriginRecord("proj") is None


def test_unregistering_removes_the_record(tmp_path):
    _fdictRegisterObtainedProject(tmp_path, [], [])
    fnWriteOriginRecord("proj", _fdictRecord())
    registryManager.fnRemoveProject("proj")
    assert fdictReadOriginRecord("proj") is None


@pytest.mark.falsification
def test_a_plain_build_of_an_obtained_image_is_refused_by_name():
    """Kills: building over an obtained image without the explicit switch."""
    with pytest.raises(HTTPException) as excinfo:
        buildRoutes._fnRefuseBuildOfObtainedImage({
            "dictImageSource": {"sSource": "archive"},
        })
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["sAction"] == "switch-to-building"
    buildRoutes._fnRefuseBuildOfObtainedImage({"sName": "built"})


# ---------------------------------------------------------------------
# The launch guard
# ---------------------------------------------------------------------


def _fconfigNamed(sProjectName):
    return SimpleNamespace(sProjectName=sProjectName)


@pytest.mark.falsification
def test_a_start_without_a_good_record_is_refused_never_the_tag(
    tmp_path, monkeypatch,
):
    """Kills: falling back to whatever the project's tag resolves to."""
    _fdictRegisterObtainedProject(tmp_path, [], [])
    monkeypatch.setattr(
        containerManager, "fdictInspectImageTag",
        lambda sReference: {"sId": S_BASE_ID, "dictLabels": {}},
    )
    monkeypatch.setattr(
        containerManager, "fsReadDaemonArchitectureQuietly", lambda: "amd64",
    )
    with pytest.raises(RuntimeError) as excinfo:
        containerManager._fnAdmitObtainedImageOrRefuse(_fconfigNamed("proj"), [])
    assert "no origin record" in str(excinfo.value)
    fnWriteOriginRecord("proj", _fdictRecord(sRunning=S_DERIVED_ID))
    with pytest.raises(RuntimeError):
        containerManager._fnAdmitObtainedImageOrRefuse(_fconfigNamed("proj"), [])
    fnWriteOriginRecord("proj", _fdictRecord())
    saRunArgs = []
    containerManager._fnAdmitObtainedImageOrRefuse(_fconfigNamed("proj"), saRunArgs)
    assert saRunArgs == ["--platform", "linux/amd64"]


@pytest.mark.falsification
def test_a_switched_daemon_never_emulates_silently(tmp_path, monkeypatch):
    """Kills: ignoring the daemon's architecture at launch."""
    _fdictRegisterObtainedProject(tmp_path, [], [])
    fnWriteOriginRecord("proj", _fdictRecord())
    monkeypatch.setattr(
        containerManager, "fdictInspectImageTag",
        lambda sReference: {"sId": S_BASE_ID, "dictLabels": {}},
    )
    monkeypatch.setattr(
        containerManager, "fsReadDaemonArchitectureQuietly", lambda: "arm64",
    )
    with pytest.raises(RuntimeError) as excinfo:
        containerManager._fnAdmitObtainedImageOrRefuse(_fconfigNamed("proj"), [])
    assert "emulate" in str(excinfo.value)


@patch("vaibify.docker.containerManager.flistConfigureX11Args", return_value=[])
def test_the_guard_sits_on_every_launch_path(mockX11, tmp_path, monkeypatch):
    """flistBuildRunArgs itself refuses; the guard is not a separate call."""
    _fdictRegisterObtainedProject(tmp_path, [], [])
    monkeypatch.setattr(
        containerManager, "fdictInspectImageTag", lambda sReference: None,
    )
    features = SimpleNamespace(
        bGpu=False, bClaude=False, bClaudeAutoUpdate=True, bCodex=False,
        bCodexAutoUpdate=True, bGemini=False, bGeminiAutoUpdate=True,
        bAntigravity=False, bAntigravityAutoUpdate=True, bOpenCode=False,
        bOpenCodeAutoUpdate=True, bCline=False, bClineAutoUpdate=True,
        bOpenHands=False, bOpenHandsAutoUpdate=True, bPi=False, bPiAutoUpdate=True,
    )
    config = SimpleNamespace(
        sProjectName="proj", sWorkspaceRoot="/workspace",
        sContainerUser="researcher", listPorts=[], listBindMounts=[],
        listSecrets=[], features=features, bNetworkIsolation=False,
    )
    with pytest.raises(RuntimeError):
        containerManager.flistBuildRunArgs(config)


# ---------------------------------------------------------------------
# Identity at connect: base, derived, never the pin when derived
# ---------------------------------------------------------------------


def _fdictContextWith(dictDerivation, sRunningId):
    return {"dictLiveImageIdentities": {"cid": {
        "sImageDigest": sRunningId, "sImageId": sRunningId,
        "dictDerivation": dictDerivation,
    }}}


def _fpathEnvelopeRepo(tmp_path):
    sRepo = str(tmp_path / "repo")
    dictEnvelope = fdictBuildEnvelope()
    dictEnvelope["dictContainer"]["sImageDigest"] = S_PIN
    fnWriteJson(sRepo, ".vaibify/environment.json", dictEnvelope)
    return sRepo


@pytest.mark.falsification
def test_the_running_base_is_the_pin_live_and_a_derivative_is_a_note(tmp_path):
    """Kills: reporting a derived running image as the pinned image."""
    sRepo = _fpathEnvelopeRepo(tmp_path)
    dictBase = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContextWith({"sPinnedBaseImageId": S_BASE_ID,
                           "sPinnedImageReference": S_PIN,
                           "bRunningIsBase": True}, S_BASE_ID),
        "cid", sRepo,
    )
    assert dictBase["bPinnedImageIsLive"] is True
    assert dictBase["sRelation"] == "base"
    assert dictBase["bEnvironmentObtained"] is True
    dictDerived = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContextWith({"sPinnedBaseImageId": S_BASE_ID,
                           "sPinnedImageReference": S_PIN,
                           "bRunningIsBase": False}, S_DERIVED_ID),
        "cid", sRepo,
    )
    assert dictDerived["bPinnedImageIsLive"] is not True
    assert dictDerived["sRelation"] == "derived"
    assert dictDerived["sPinnedBaseImageId"] == S_BASE_ID
    dictBuilt = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContextWith(None, S_DERIVED_ID), "cid", sRepo,
    )
    assert dictBuilt["bPinnedImageIsLive"] is False
    assert dictBuilt["bEnvironmentObtained"] is False
    assert "sRelation" not in dictBuilt


def test_regenerating_an_obtained_envelope_is_refused():
    from vaibify.gui.routes.reproducibilityRoutes import (
        _fnRefuseRegenerationOfAnObtainedEnvelope,
    )
    with pytest.raises(HTTPException) as excinfo:
        _fnRefuseRegenerationOfAnObtainedEnvelope(
            _fdictContextWith({"bRunningIsBase": True}, S_BASE_ID), "cid",
        )
    assert excinfo.value.status_code == 409
    _fnRefuseRegenerationOfAnObtainedEnvelope(
        _fdictContextWith(None, S_BASE_ID), "cid",
    )


# ---------------------------------------------------------------------
# The shadow runs the BASE the origin record names
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_shadow_is_created_from_the_base_id_on_the_obtained_platform(
    tmp_path, monkeypatch,
):
    """Kills: creating the shadow from the overlay result the researcher runs."""
    listCreated = []

    def fdictDrive(connectionDocker, dictWorkflow, sImage, tPaths, baArchive,
                   dictCapacity, sResource, fnStatus, fdictRun, sPlatform=None):
        listCreated.append((sImage, sPlatform, baArchive))
        return {"bPassed": True, "iSourceDateEpoch": 0, "sRerunStartedIso": ""}
    monkeypatch.setattr(shadowRerun, "_fdictDriveShadowLifecycle", fdictDrive)
    monkeypatch.setattr(
        shadowRerun.coherentExport, "fbaExportRepositoryCoherently",
        lambda *aArgs: _fbaEmptyTar(),
    )
    monkeypatch.setattr(
        shadowRerun.daemonCapacity, "fdictResolveDaemonCapacity",
        lambda connectionDocker: {"iArchiveTotalBytes": 10 ** 6},
    )
    dictEnvelope = {"dictContainer": {"sImageDigest": S_PIN, "sArchitecture": "amd64"}}
    dictOrigin = _fdictRecord(sRunning=S_DERIVED_ID, listOverlays=["claude"])
    dictOutcome = shadowRerun.fdictRerunInShadowContainer(
        None, "cid", {}, "/repo/.vaibify/workflows/project.json", "/repo",
        dictEnvelope, fdictRunAndVerify=lambda *aArgs, **kwargs: {},
        dictImageOrigin=dictOrigin,
    )
    assert listCreated[0][0] == S_BASE_ID
    assert listCreated[0][1] == "linux/amd64"
    assert dictOutcome["sImageDigest"] == S_PIN
    assert dictOutcome["dictReproductionProvenance"]["sObtainedFrom"] == "archive"
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(listCreated[0][2])) as fileTar:
        assert "repo/.vaibify/image_loaded_from_archive" in fileTar.getnames()
    # A record about ANOTHER pin is ignored: the envelope wins.
    dictOther = dict(dictOrigin, sPinnedImageReference="other@sha256:" + "c" * 64)
    shadowRerun.fdictRerunInShadowContainer(
        None, "cid", {}, "/repo/.vaibify/workflows/project.json", "/repo",
        dictEnvelope, fdictRunAndVerify=lambda *aArgs, **kwargs: {},
        dictImageOrigin=dictOther,
    )
    assert listCreated[1][0] == S_PIN and listCreated[1][1] is None


def _fbaEmptyTar():
    import io
    import tarfile
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        infoMember = tarfile.TarInfo("repo/README")
        infoMember.size = 0
        fileTar.addfile(infoMember, io.BytesIO(b""))
    return bufferTar.getvalue()


# ---------------------------------------------------------------------
# The conversion's whitelist and the pinned-environment description
# ---------------------------------------------------------------------


def test_the_wizard_translation_is_classified_in_full():
    """Every key the wizard emits is RUNTIME or BASE; every feature is typed."""
    dictYaml = _fdictBuildYamlFromRequest(ConvertToContainerRequest(
        sProjectName="proj", listSystemPackages=["curl"],
        listPythonPackages=["numpy"], sPipInstallFlags="-q", iCpuLimit=2,
        fMemoryLimitGigabytes=4.0,
    ))
    setKnown = set(_T_RUNTIME_YAML_KEYS) | set(_T_BASE_YAML_KEYS) | {"features"}
    assert set(dictYaml) <= setKnown, set(dictYaml) - setKnown
    setAgents = set(T_AGENT_OVERLAY_NAMES)
    setAutoUpdate = {f"{sAgent}AutoUpdate" for sAgent in setAgents}
    assert set(dictYaml["features"]) <= (
        set(_T_BASE_FEATURE_KEYS) | setAgents | setAutoUpdate
    )


@pytest.mark.falsification
def test_an_obtained_image_keeps_the_authors_base_fields_and_agents(tmp_path):
    """Kills: merging every requested field onto the author's file."""
    pathConfig = tmp_path / "vaibify.yml"
    pathConfig.write_text(
        "projectName: author\nbaseImage: 'ubuntu:22.04'\npythonVersion: '3.11'\n"
        "dashboardPort: 9999\npythonPackages: [scipy]\n"
        "features:\n  claude: true\n  latex: true\n  claudeAutoUpdate: true\n"
    )
    dictMerged = _fdictOverlayRuntimeFieldsOnly(str(pathConfig), ConvertToContainerRequest(
        sProjectName="proj", sPythonVersion="3.12", sBaseImage="ubuntu:24.04",
        listPythonPackages=["numpy"], listFeatures=["gemini"],
        bClaudeAutoUpdate=False, iCpuLimit=3,
    ))
    assert dictMerged["projectName"] == "proj"
    assert dictMerged["cpuLimit"] == 3
    assert dictMerged["baseImage"] == "ubuntu:22.04"
    assert dictMerged["pythonVersion"] == "3.11"
    assert dictMerged["pythonPackages"] == ["scipy"]
    assert dictMerged["dashboardPort"] == 9999
    assert dictMerged["features"]["claude"] is True
    assert "gemini" not in dictMerged["features"]
    assert dictMerged["features"]["latex"] is True
    assert dictMerged["features"]["claudeAutoUpdate"] is False


def test_the_pinned_environment_is_described_from_the_clone(tmp_path):
    sRepo = str(tmp_path / "clone")
    fnWriteJson(sRepo, ".vaibify/environment.json", fdictBuildEnvelope())
    dictDescribed = fdictDescribePinnedEnvironment(sRepo)
    assert dictDescribed["bObtainable"] is True
    assert dictDescribed["sRequiredPlatform"].startswith("linux/")
    assert dictDescribed["bDepositOnRecord"] is False
    dictEnvelope = fdictBuildEnvelope()
    dictEnvelope["dictContainer"].pop("sArchitecture")
    fnWriteJson(sRepo, ".vaibify/environment.json", dictEnvelope)
    dictRefused = fdictDescribePinnedEnvironment(sRepo)
    assert dictRefused["bObtainable"] is False
    assert "architecture" in dictRefused["sRefusal"]
    assert fdictDescribePinnedEnvironment(str(tmp_path / "nowhere"))[
        "bObtainable"
    ] is False


def test_the_conversion_result_names_the_acquire_hand_off(tmp_path):
    _fdictRegisterObtainedProject(tmp_path, [], [])
    dictResult = registryRoutes._fdictConversionResult("proj", True)
    assert dictResult["bAcquireRequired"] is True
    assert dictResult["bBuildRequired"] is False
    assert dictResult["sAcquirePath"] == "/api/containers/proj/acquire-image"
    dictBuilt = registryRoutes._fdictConversionResult("proj", False)
    assert dictBuilt["bBuildRequired"] is True and dictBuilt["bAcquireRequired"] is False
    assert json.dumps(dictResult)
