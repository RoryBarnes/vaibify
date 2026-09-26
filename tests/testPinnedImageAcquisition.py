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
    S_ACTION_REOBTAIN_WITHOUT_ADDITIONS,
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
    # None, never []: "unproven" and "proven to hold nothing" must not
    # read alike, or the resolver stacks the author's own overlays.
    assert flistProveOverlayBaseline({}, ["claude"], []) is None
    with pytest.raises(PinnedImageAcquisitionRefusedError) as excinfo:
        flistProveOverlayBaseline(
            {S_RECIPE_IMAGE_LABEL: "0" * 64}, ["claude"], ["gemini"],
            _fpathShippedDockerfiles(tmp_path),
        )
    assert "cannot be proven" in str(excinfo.value)
    assert "without adding agents" in str(excinfo.value)
    assert excinfo.value.sAction == S_ACTION_REOBTAIN_WITHOUT_ADDITIONS


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


@pytest.mark.falsification
def test_a_refusal_is_said_in_the_researchers_words_first(tmp_path):
    """Kills: showing the rule text where the researcher's sentence belongs.

    The Environment page is read before anything has run, by someone
    who has never met "rule 2" -- and the rule's own remedy (regenerate
    the envelope) would rewrite a published clone's record. The plain
    sentence names the cause and the one check a reader can make; the
    rule stays available as the detail.
    """
    sRepo = str(tmp_path / "clone")
    dictEnvelope = fdictBuildEnvelope()
    dictEnvelope["dictContainer"].pop("sArchitecture")
    fnWriteJson(sRepo, ".vaibify/environment.json", dictEnvelope)
    dictRefused = fdictDescribePinnedEnvironment(sRepo)
    sPlain = dictRefused["sPlainRefusal"]
    assert "kind of processor" in sPlain
    assert "git status" in sPlain
    assert "rule 2" not in sPlain and "Regenerate" not in sPlain
    assert dictRefused["sRefusal"].startswith("rule 2")
    assert fdictDescribePinnedEnvironment(str(tmp_path / "nowhere"))[
        "sPlainRefusal"
    ].startswith("This folder has no record")


def test_the_conversion_result_names_the_acquire_hand_off(tmp_path):
    _fdictRegisterObtainedProject(tmp_path, [], [])
    dictResult = registryRoutes._fdictConversionResult("proj", True)
    assert dictResult["bAcquireRequired"] is True
    assert dictResult["bBuildRequired"] is False
    assert dictResult["sAcquirePath"] == "/api/containers/proj/acquire-image"
    dictBuilt = registryRoutes._fdictConversionResult("proj", False)
    assert dictBuilt["bBuildRequired"] is True and dictBuilt["bAcquireRequired"] is False
    assert json.dumps(dictResult)


# ---------------------------------------------------------------------
# Review findings (2026-09-12): the label is a SET, the refusal offers
# its recovery, and a transition needs the container gone
# ---------------------------------------------------------------------


def _fnPatchTheBuildContext(monkeypatch, tmp_path):
    """Keep the overlay stack off the real build context; the label is the point."""
    monkeypatch.setattr(
        "vaibify.cli.commandBuild.fsStageBuildContext",
        lambda config, sDockerDir: str(tmp_path / "staged"),
    )
    monkeypatch.setattr(
        "vaibify.cli.commandBuild.fnPrepareBuildContext", lambda *aArgs: None,
    )
    monkeypatch.setattr(
        "vaibify.cli.commandBuild.fnDiscardBuildContext", lambda sPath: None,
    )


@pytest.mark.falsification
def test_a_derived_image_is_labelled_with_the_set_in_canonical_order(
    tmp_path, monkeypatch,
):
    """The label records WHICH overlays, in the one order its parser accepts.

    The supported case -- the author's image holds claude, the clone
    adds gemini -- stacks ``node`` then ``gemini`` on the base, so the
    build order is claude, node, gemini. Canonical order is node,
    claude, gemini. A label written in build order is refused by
    ``flistParseOverlaysLabel`` (reproduced), and an image so labelled
    could never again be acquired as a pinned image.

    Kills: stamping ``listProven + listChain`` as the label.
    """
    dictProject = _fdictRegisterObtainedProject(tmp_path, ["claude"], ["gemini"])
    _fnPatchTheChain(monkeypatch, [])
    _fnPatchTheBuildContext(monkeypatch, tmp_path)
    store = _FakeStore({S_OVERLAYS_IMAGE_LABEL: "claude"})
    sDerivedId = "sha256:" + "d" * 64
    store.dictHeld[sDerivedId] = _FakeImage(sDerivedId, {})
    listStackCalls = []

    def fsStack(sProjectName, sBaseImageId, listChain, sStagedDir, sPlatform,
                listLabelOverlays, bNoCache=False):
        listStackCalls.append((list(listChain), list(listLabelOverlays)))
        return sDerivedId
    monkeypatch.setattr(imageBuilder, "fsStackOverlaysOnObtainedBase", fsStack)
    dictRecord = fdictAcquireForProject(
        dictProject, False, dockerDisposable=store, sDockerDir=str(tmp_path),
    )
    listChain, listLabel = listStackCalls[0]
    assert listChain == ["node", "gemini"], "build order: the prerequisite first"
    listCanonical = imageBuilder.flistCanonicalizeOverlaySet(["claude", "node", "gemini"])
    assert listCanonical == ["node", "claude", "gemini"]
    assert listLabel == listCanonical
    assert listLabel != ["claude"] + listChain, "the label must not be build order"
    assert flistParseOverlaysLabel(
        fsRenderOverlaysLabelValue(listLabel), imageBuilder.flistCanonicalOverlayOrder(),
    ) == listLabel
    assert dictRecord["listResolvedOverlays"] == listCanonical
    assert dictRecord["sRunningImageId"] == sDerivedId
    assert registryManager.fdictGetProject("proj")["dictImageSource"][
        "listResolvedOverlays"
    ] == listCanonical


def test_canonicalizing_refuses_an_overlay_it_does_not_know():
    with pytest.raises(ValueError):
        imageBuilder.flistCanonicalizeOverlaySet(["claude", "not-an-overlay"])


@pytest.mark.falsification
def test_the_retry_without_additions_drops_them_before_obtaining(
    tmp_path, monkeypatch,
):
    """The refusal's recovery is a lane, not a sentence.

    An unproven base with additions refuses and NAMES the retry; the
    retry clears the additions from the registry entry first, then
    runs the base as obtained -- stacking nothing, describing nothing
    it cannot prove.

    Kills: ignoring ``bWithoutAdditions``, so the retry re-asks for
    the agents it just could not stack.
    """
    dictProject = _fdictRegisterObtainedProject(tmp_path, ["claude"], ["gemini"])
    _fnPatchTheChain(monkeypatch, [])
    store = _FakeStore({})
    with pytest.raises(PinnedImageAcquisitionRefusedError) as excinfo:
        fdictAcquireForProject(
            dictProject, False, dockerDisposable=store, sDockerDir=str(tmp_path),
        )
    assert excinfo.value.sAction == S_ACTION_REOBTAIN_WITHOUT_ADDITIONS
    assert buildRoutes._fdictBuildFailureDetail(excinfo.value, "", "proj")[
        "sAction"
    ] == S_ACTION_REOBTAIN_WITHOUT_ADDITIONS
    assert store.dictHeld[S_BASE_ID].listTags == [], "refused before tagging"

    def fsNeverStack(*aArgs, **dictKwargs):
        raise AssertionError("an unproven base must not be stacked on")
    monkeypatch.setattr(imageBuilder, "fsStackOverlaysOnObtainedBase", fsNeverStack)
    dictRecord = fdictAcquireForProject(
        dictProject, False, dockerDisposable=store, sDockerDir=str(tmp_path),
        bWithoutAdditions=True,
    )
    assert dictRecord["sRunningImageId"] == S_BASE_ID
    assert dictRecord["listResolvedOverlays"] == []
    assert registryManager.fdictGetProject("proj")["dictImageSource"][
        "listAdditionalAgents"
    ] == []


@pytest.mark.falsification
def test_an_unproven_base_with_no_additions_stacks_nothing(tmp_path, monkeypatch):
    """Kills: reading an unproven baseline as an empty PROVEN one, which
    makes the differential resolver stack the author's own agents
    onto the image that already holds them."""
    dictProject = _fdictRegisterObtainedProject(tmp_path, ["claude"], [])
    _fnPatchTheChain(monkeypatch, [])
    store = _FakeStore({})
    listStacked = []
    monkeypatch.setattr(
        pinnedImageAcquisition, "_fsStackAndResolve",
        lambda *aArgs, **kwargs: listStacked.append(aArgs[2]) or S_BASE_ID,
    )
    dictRecord = fdictAcquireForProject(
        dictProject, False, dockerDisposable=store, sDockerDir=str(tmp_path),
    )
    assert listStacked == [[]]
    assert dictRecord["sRunningImageId"] == S_BASE_ID


@pytest.mark.falsification
def test_a_transition_is_refused_while_the_container_exists():
    """Re-obtain and switch retag and re-describe the project; both must
    find the container GONE, by the daemon's word, not the page's.

    Kills: trusting the frontend's stop (dropping the guard), so a
    stop that failed leaves the old container running under a tag,
    registry entry and origin record that describe another image.
    """
    for dictStatus in (
        {"bExists": True, "bRunning": True, "sStatus": "running"},
        {"bExists": True, "bRunning": False, "sStatus": "exited"},
        None,
    ):
        with pytest.raises(HTTPException) as excinfo:
            buildRoutes._fnRefuseWhileTheContainerExists(dictStatus, "Switching")
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail["sAction"] == buildRoutes.S_ACTION_STOP_FIRST
    buildRoutes._fnRefuseWhileTheContainerExists(
        {"bExists": False, "bRunning": False, "sStatus": "not found"}, "Switching",
    )


# ---------------------------------------------------------------------
# The way FORWARD: a built project switches to the author's pinned image
# ---------------------------------------------------------------------


def _fdictRegisterBuiltProject(tmp_path, sWorkingYaml, sCommittedYaml=None):
    """A container project that BUILT its image, in a real git clone.

    The committed ``vaibify.yml`` is the author's; the working copy is
    what the build's merge left behind. ``None`` commits nothing, so
    HEAD holds no config to restore from.
    """
    import subprocess
    sDirectory = str(tmp_path / "clone")
    os.makedirs(sDirectory)
    sConfigPath = os.path.join(sDirectory, "vaibify.yml")
    listIdentity = ["-c", "user.email=a@b", "-c", "user.name=a"]
    subprocess.run(["git", "init", "-q"], cwd=sDirectory, check=True)
    subprocess.run(
        ["git", *listIdentity, "commit", "-q", "--allow-empty", "-m", "root"],
        cwd=sDirectory, check=True,
    )
    if sCommittedYaml is not None:
        with open(sConfigPath, "w") as fileHandle:
            fileHandle.write(sCommittedYaml)
        subprocess.run(["git", "add", "vaibify.yml"], cwd=sDirectory, check=True)
        subprocess.run(
            ["git", *listIdentity, "commit", "-q", "-m", "author"],
            cwd=sDirectory, check=True,
        )
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(sWorkingYaml)
    fnWriteJson(sDirectory, ".vaibify/environment.json", fdictBuildEnvelope())
    registryManager.fnAddProject(sDirectory, sMode="host")
    registryManager.fnConvertProjectToContainer("proj", "proj", None)
    return registryManager.fdictGetProject("proj")


S_AUTHOR_YAML = (
    "projectName: proj\npythonVersion: '3.11'\npythonPackages:\n  - numpy\n"
    "features:\n  claude: true\n  latex: true\n  claudeAutoUpdate: true\n"
)
S_BUILT_YAML = (
    "projectName: proj\npythonVersion: '3.12'\ncpuLimit: 1\n"
    "features:\n  codex: true\n  latex: false\n  claudeAutoUpdate: false\n"
)


@pytest.mark.falsification
def test_switching_to_obtaining_writes_the_source_and_nothing_else(tmp_path):
    """Kills: writing the origin record here (the acquisition's to write)."""
    _fdictRegisterBuiltProject(tmp_path, S_BUILT_YAML, S_AUTHOR_YAML)
    dictSource = {"sSource": "archive", "sPinnedImageReference": S_PIN}
    registryManager.fnSwitchProjectToObtaining("proj", dictSource)
    dictProject = registryManager.fdictGetProject("proj")
    assert dictProject["dictImageSource"] == dictSource
    assert dictProject["sMode"] == "container"
    assert fdictReadOriginRecord("proj") is None


def test_switching_to_obtaining_refuses_the_wrong_starting_states(tmp_path):
    with pytest.raises(ValueError):
        registryManager.fnSwitchProjectToObtaining("proj", {"sSource": "build"})
    with pytest.raises(KeyError):
        registryManager.fnSwitchProjectToObtaining("proj", {"sSource": "archive"})
    _fdictRegisterObtainedProject(tmp_path, [], [])
    with pytest.raises(ValueError, match="already obtains"):
        registryManager.fnSwitchProjectToObtaining("proj", {"sSource": "archive"})


def test_switching_to_obtaining_refuses_a_host_project(tmp_path):
    sDirectory = str(tmp_path / "host")
    os.makedirs(sDirectory)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileHandle:
        fileHandle.write("projectName: host\n")
    registryManager.fnAddProject(sDirectory, sMode="host")
    with pytest.raises(ValueError, match="host project"):
        registryManager.fnSwitchProjectToObtaining("host", {"sSource": "archive"})


@pytest.mark.falsification
def test_restoring_the_authors_base_fields_reads_head_not_the_working_tree(
    tmp_path,
):
    """The committed copy is the author's; the working copy is the build's.

    The two are made DIFFERENT in every base field so a restore that
    reads the working tree is observable.

    Kills: taking the base keys from the existing file instead of the
    committed one.
    """
    import yaml
    from vaibify.gui.pinnedEnvironmentConversion import (
        fnRestoreAuthorBaseFieldsFromGit,
    )
    dictProject = _fdictRegisterBuiltProject(tmp_path, S_BUILT_YAML, S_AUTHOR_YAML)
    fnRestoreAuthorBaseFieldsFromGit(dictProject)
    with open(dictProject["sConfigPath"]) as fileHandle:
        dictRestored = yaml.safe_load(fileHandle)
    assert dictRestored["pythonVersion"] == "3.11"
    assert dictRestored["pythonPackages"] == ["numpy"]
    assert dictRestored["features"]["claude"] is True
    assert dictRestored["features"]["latex"] is True
    # The researcher's runtime choices survive the restore.
    assert dictRestored["cpuLimit"] == 1
    assert dictRestored["features"]["claudeAutoUpdate"] is False
    # The build's agent is not in the author's features block.
    assert dictRestored["features"].get("codex") is not True


@pytest.mark.falsification
def test_restoring_refuses_when_head_holds_no_config(tmp_path):
    """Kills: falling back to the working copy when git has no author's file."""
    from vaibify.gui.pinnedEnvironmentConversion import (
        fnRestoreAuthorBaseFieldsFromGit,
    )
    dictProject = _fdictRegisterBuiltProject(tmp_path, S_BUILT_YAML, None)
    with pytest.raises(HTTPException) as excinfo:
        fnRestoreAuthorBaseFieldsFromGit(dictProject)
    assert excinfo.value.status_code == 409
    assert "git checkout" in excinfo.value.detail["sMessage"]


@pytest.mark.falsification
def test_the_switch_source_reads_the_researchers_agents_before_restoring(
    tmp_path,
):
    """The additions are the agents the built image carried that the
    author's image lacks; the baseline is the AUTHOR's.

    Kills: resolving the baseline before the restore, which reads the
    researcher's own agents as the author's and stacks none of them.
    """
    from vaibify.gui.pinnedEnvironmentConversion import (
        fdictBuildArchiveImageSourceForSwitch,
    )
    dictProject = _fdictRegisterBuiltProject(tmp_path, S_BUILT_YAML, S_AUTHOR_YAML)
    dictSource = fdictBuildArchiveImageSourceForSwitch(dictProject, False)
    assert dictSource["sSource"] == "archive"
    assert dictSource["listAuthorOverlays"] == ["claude"]
    assert dictSource["listAdditionalAgents"] == ["codex"]
    assert dictSource["sPinnedImageReference"]
    assert dictSource["bAllowEmulation"] is False


@pytest.mark.falsification
def test_the_image_origin_names_the_switch_only_for_a_built_clone_that_pins(
    tmp_path,
):
    """Kills: reading an obtained project, or a host one, as built."""
    from vaibify.gui.pinnedEnvironmentConversion import (
        fbSwitchToPinnedImageIsTheRemedy,
        fdictDescribeImageOriginForProject,
    )
    assert fdictDescribeImageOriginForProject(None) == {
        "bImageWasBuilt": False, "bPinnedImageObtainable": False,
    }
    assert fdictDescribeImageOriginForProject(
        {"sMode": "host", "sDirectory": str(tmp_path)},
    )["bImageWasBuilt"] is False
    dictObtained = _fdictRegisterObtainedProject(tmp_path, [], [])
    assert fdictDescribeImageOriginForProject(dictObtained) == {
        "bImageWasBuilt": False, "bPinnedImageObtainable": False,
    }
    registryManager.fnRemoveProject("proj")
    dictBuilt = _fdictRegisterBuiltProject(tmp_path / "b", S_BUILT_YAML, S_AUTHOR_YAML)
    dictOrigin = fdictDescribeImageOriginForProject(dictBuilt)
    assert dictOrigin == {"bImageWasBuilt": True, "bPinnedImageObtainable": True}
    assert fbSwitchToPinnedImageIsTheRemedy(dictOrigin)
    os.remove(os.path.join(dictBuilt["sDirectory"], ".vaibify", "environment.json"))
    assert fdictDescribeImageOriginForProject(dictBuilt) == {
        "bImageWasBuilt": True, "bPinnedImageObtainable": False,
    }


@pytest.mark.falsification
def test_a_container_fact_refusal_names_the_switch_for_a_built_clone(
    monkeypatch,
):
    """Both container facts have one cause on a built clone, one remedy.

    Kills: naming the package rewrite (or the Dockerfile re-export)
    when the switch is the remedy, which sends the researcher to
    rewrite the author's committed files.
    """
    from vaibify.gui.routes import reproducibilityRoutes
    monkeypatch.setattr(reproducibilityRoutes, "fbL3ReadinessOK", lambda *a: True)
    monkeypatch.setattr(
        reproducibilityRoutes, "fsCurrentManifestDigest", lambda *a: "digest",
    )
    dictMismatch = {
        "bChecked": True, "bMatches": False,
        "listMissingFromImage": [], "listExtraInImage": ["numpy"],
    }
    dictProvenanceBad = {"bDockerfileDescribesPinnedImage": False}
    dictBuiltClone = {"bImageWasBuilt": True, "bPinnedImageObtainable": True}

    with pytest.raises(HTTPException) as excinfo:
        reproducibilityRoutes._fsRequireReadinessThenDigest(
            {}, None, dictMismatch, dictProvenanceBad, dictBuiltClone,
        )
    sDetail = str(excinfo.value.detail)
    assert "Switch to the author's pinned image" in sDetail
    assert "pythonPackages" not in sDetail
    assert "re-export" not in sDetail
    assert sDetail.count("Switch to the author's pinned image") == 1

    # An author's own rebuilt image keeps the rewrite remedies.
    with pytest.raises(HTTPException) as excinfo:
        reproducibilityRoutes._fsRequireReadinessThenDigest(
            {}, None, dictMismatch, dictProvenanceBad,
            {"bImageWasBuilt": True, "bPinnedImageObtainable": False},
        )
    sDetail = str(excinfo.value.detail)
    assert "pythonPackages" in sDetail and "re-export" in sDetail
    assert "Switch to the author's pinned image" not in sDetail

    # Nothing unmet: the digest comes back.
    assert reproducibilityRoutes._fsRequireReadinessThenDigest(
        {}, None, {"bChecked": True, "bMatches": True},
        {"bDockerfileDescribesPinnedImage": True}, dictBuiltClone,
    ) == "digest"
