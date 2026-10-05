"""The pinned-image lane refuses loudly and reports every link it walks.

``fdictAcquireForProject`` is driven end to end here against the REAL
registry, the REAL clone envelope reader, the REAL origin-record store
(all under ``tmp_path``) and the REAL overlay-stacking argv assembly.
Only the true external boundaries are faked: the acquisition chain
(registry pull, Zenodo download), the Docker image store, and the
``docker build`` that stacks an overlay. Image IDs, tags and the
project name are all distinct strings, so a lane that tagged by the
wrong one would fail.
"""

import os

import pytest

from tests.reproductionSourceFixtures import (
    S_FIXTURE_IMAGE_DIGEST,
    fdictBuildEnvelope,
    fnWriteJson,
)
from vaibify.config import imageOrigins, registryManager
from vaibify.config.imageOrigins import S_PINNED_BASE_LABEL, fdictReadOriginRecord
from vaibify.docker import disposableContainer, imageBuilder
from vaibify.docker.pinnedImageAcquisition import (
    PinnedImageAcquisitionRefusedError,
    fdictAcquireForProject,
)
from vaibify.reproducibility import agentLayerSeparation
from vaibify.reproducibility.dockerfileComposer import S_OVERLAYS_IMAGE_LABEL
from vaibify.reproducibility.imageAcquisition import (
    ImageAcquisitionRefusedError,
    S_LINK_ARCHIVE,
    S_LINK_REGISTRY,
)


S_PROJECT_NAME = "pinnedProject"
S_BASE_ID = "sha256:" + "b" * 64
S_DERIVED_ID = "sha256:" + "d" * 64
S_ACQUIRE_TARGET = "vaibify.reproducibility.imageAcquisition.fdictAcquirePinnedImage"


class _FakeImage:
    """One image held by the fake daemon; records the tags applied to it."""

    def __init__(self, sId, dictLabels):
        self.id = sId
        self.attrs = {
            "Os": "linux", "Architecture": "amd64",
            "Config": {"Labels": dict(dictLabels)},
        }
        self.listTags = []

    def tag(self, sRepository, tag=None):
        self.listTags.append(f"{sRepository}:{tag}")


class _FakeImageStore:
    """The ``images`` half of a docker SDK client, keyed by reference."""

    def __init__(self, dictLabels):
        self.dictHeld = {S_BASE_ID: _FakeImage(S_BASE_ID, dictLabels)}
        self.images = self

    def get(self, sReference):
        if sReference not in self.dictHeld:
            raise KeyError(sReference)
        return self.dictHeld[sReference]


@pytest.fixture(autouse=True)
def fnIsolateRegistryAndOrigins(tmp_path, monkeypatch):
    """Registry and origin records under tmp, never the researcher's own."""
    sRegistryDirectory = str(tmp_path / "vaibifyHome")
    os.makedirs(sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )
    sOrigins = os.path.join(sRegistryDirectory, "imageOrigins")
    monkeypatch.setattr(imageOrigins, "fsOriginsDirectory", lambda: (
        os.makedirs(sOrigins, exist_ok=True) or sOrigins
    ))


def fdictRegisterObtainedProject(tmp_path, listAdditional, bWriteEnvelope=True):
    """Register a clone whose image is obtained from the envelope's pin."""
    sDirectory = str(tmp_path / "clone")
    os.makedirs(sDirectory)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileHandle:
        fileHandle.write(
            f"projectName: {S_PROJECT_NAME}\npythonVersion: '3.12'\n"
            "features:\n  latex: false\n"
        )
    if bWriteEnvelope:
        fnWriteJson(
            sDirectory, ".vaibify/environment.json", fdictBuildEnvelope(),
        )
    registryManager.fnAddProject(sDirectory, sMode="host")
    registryManager.fnConvertProjectToContainer(
        S_PROJECT_NAME, S_PROJECT_NAME, {
            "sSource": "archive", "bAllowEmulation": True,
            "sPinnedImageReference": S_FIXTURE_IMAGE_DIGEST,
            "sRequiredPlatform": "linux/amd64",
            "listAuthorOverlays": [], "sAuthorRecipeFingerprint": "",
            "listAdditionalAgents": list(listAdditional),
            "listResolvedOverlays": None,
        },
    )
    return registryManager.fdictGetProject(S_PROJECT_NAME)


def fnStubAcquisitionChain(monkeypatch, listEvents=(), sImageId=S_BASE_ID,
                           exceptionRefusal=None, listCalls=None):
    """Replace the registry/deposit chain with a scripted one."""

    def fdictAcquire(dictEnvironment, sPlatform, fnStatus, bEmulation, store):
        if listCalls is not None:
            listCalls.append({
                "dictEnvironment": dictEnvironment, "sPlatform": sPlatform,
                "bEmulation": bEmulation, "store": store,
            })
        for dictEvent in listEvents:
            fnStatus(dictEvent)
        if exceptionRefusal is not None:
            raise exceptionRefusal
        return {
            "sImageReference": S_FIXTURE_IMAGE_DIGEST, "sImageId": sImageId,
            "sObtainedFrom": "registry", "sRequiredPlatform": sPlatform,
            "sObtainedPlatform": sPlatform, "sDaemonArchitecture": "amd64",
            "bEmulated": False, "listAttempts": [],
        }

    monkeypatch.setattr(S_ACQUIRE_TARGET, fdictAcquire)


def testADockerfileBuiltProjectIsSentToRebuild():
    """Only an obtained image can be acquired; a built one is refused."""
    with pytest.raises(PinnedImageAcquisitionRefusedError) as errorRaised:
        fdictAcquireForProject(
            {"sName": S_PROJECT_NAME, "dictImageSource": {"sSource": "build"}},
            False, dockerDisposable=_FakeImageStore({}),
        )
    assert "use Rebuild" in str(errorRaised.value)
    assert errorRaised.value.sAction == ""


def testEveryChainLinkIsReportedAndTheBaseIsTaggedById(
    tmp_path, monkeypatch,
):
    """With no store supplied, the lane opens one and reports each event."""
    dictProject = fdictRegisterObtainedProject(tmp_path, [])
    storeFake = _FakeImageStore({S_OVERLAYS_IMAGE_LABEL: ""})
    monkeypatch.setattr(
        disposableContainer, "fdockerCreateDisposableClient",
        lambda: storeFake,
    )
    listCalls = []
    fnStubAcquisitionChain(monkeypatch, listEvents=[
        {"sPhase": "attempt", "sLink": S_LINK_REGISTRY, "bSucceeded": False,
         "sDetail": "manifest unknown"},
        {"sPhase": "attempt", "sLink": S_LINK_ARCHIVE, "bSucceeded": True},
        {"sPhase": "downloading", "iBytes": 5, "iTotalBytes": 10},
        {"sPhase": "pulling", "sImageReference": "registry.example/x",
         "sPlatform": "linux/amd64"},
        {"sPhase": "acquired", "sImageReference": "registry.example/x",
         "sObtainedFrom": "archive", "bEmulated": True},
        {"sPhase": "verifying"},
    ], listCalls=listCalls)
    listLines = []
    dictRecord = fdictAcquireForProject(
        dictProject, True, fnReportLine=listLines.append,
    )
    assert listCalls[0]["store"] is storeFake
    assert listCalls[0]["bEmulation"] is True
    assert listCalls[0]["dictEnvironment"]["dictContainer"][
        "sImageDigest"
    ] == S_FIXTURE_IMAGE_DIGEST
    assert f"Pinned image: {S_FIXTURE_IMAGE_DIGEST} (linux/amd64)" in listLines
    for sExpected in (
        "no registry has this image -- normal for an image that was "
        "archived rather than published -- so vaibify tries the next "
        "source (Docker said: manifest unknown)",
        f"{S_LINK_ARCHIVE}: served",
        "downloading the archived image: 0 MB of 0 MB (50%)",
        "pulling registry.example/x for linux/amd64",
        "obtained registry.example/x from the archive (emulated)",
        "verifying",
    ):
        assert sExpected in listLines
    assert storeFake.dictHeld[S_BASE_ID].listTags == [f"{S_PROJECT_NAME}:latest"]
    assert dictRecord["sRunningImageId"] == S_BASE_ID
    assert fdictReadOriginRecord(S_PROJECT_NAME)["sBaseImageId"] == S_BASE_ID


def testAnUnobtainableCloneIsRefusedBeforeTheChainRuns(tmp_path, monkeypatch):
    """No envelope means nothing is pinned; the chain is never walked."""
    dictProject = fdictRegisterObtainedProject(
        tmp_path, [], bWriteEnvelope=False,
    )
    listCalls = []
    fnStubAcquisitionChain(monkeypatch, listCalls=listCalls)
    with pytest.raises(PinnedImageAcquisitionRefusedError) as errorRaised:
        fdictAcquireForProject(
            dictProject, False, dockerDisposable=_FakeImageStore({}),
        )
    assert "can no longer be containerized" in str(errorRaised.value)
    assert listCalls == []


def testARefusedChainBecomesALaneRefusalAndTagsNothing(tmp_path, monkeypatch):
    """The chain's own refusal is carried, chained, and nothing is recorded."""
    dictProject = fdictRegisterObtainedProject(tmp_path, [])
    errorChain = ImageAcquisitionRefusedError("no link served the pin")
    fnStubAcquisitionChain(monkeypatch, exceptionRefusal=errorChain)
    storeFake = _FakeImageStore({})
    with pytest.raises(PinnedImageAcquisitionRefusedError) as errorRaised:
        fdictAcquireForProject(dictProject, False, dockerDisposable=storeFake)
    assert "could not be obtained: no link served the pin" in str(
        errorRaised.value,
    )
    assert errorRaised.value.__cause__ is errorChain
    assert storeFake.dictHeld[S_BASE_ID].listTags == []
    assert fdictReadOriginRecord(S_PROJECT_NAME) is None


def testAnAcquisitionWithNoImageIdIsRefused(tmp_path, monkeypatch):
    """Nothing can be tagged by identity when the daemon named none."""
    dictProject = fdictRegisterObtainedProject(tmp_path, [])
    fnStubAcquisitionChain(monkeypatch, sImageId="")
    storeFake = _FakeImageStore({})
    with pytest.raises(PinnedImageAcquisitionRefusedError) as errorRaised:
        fdictAcquireForProject(dictProject, False, dockerDisposable=storeFake)
    assert "reported no image ID" in str(errorRaised.value)
    assert storeFake.dictHeld[S_BASE_ID].listTags == []
    assert fdictReadOriginRecord(S_PROJECT_NAME) is None


def flistRecordDockerBuilds(monkeypatch):
    """Capture every ``docker build`` argv instead of running it."""
    listBuilds = []
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: True)
    monkeypatch.setattr(
        imageBuilder, "_fnRunDockerBuild",
        lambda saCommand: listBuilds.append(list(saCommand)),
    )
    return listBuilds


def fnStubBuildContextPreparation(monkeypatch):
    """Keep the project-repo copy and generated files out of the stack."""
    monkeypatch.setattr(
        "vaibify.cli.commandBuild.fnPrepareBuildContext",
        lambda config, sStagedDir, sProjectDirectory: None,
    )


def testAStackedOverlayIsBuiltOnTheBaseIdForThePinnedPlatform(
    tmp_path, monkeypatch,
):
    """An added agent is stacked on the ID and the DERIVED image is tagged."""
    dictProject = fdictRegisterObtainedProject(tmp_path, ["claude"])
    fnStubAcquisitionChain(monkeypatch)
    fnStubBuildContextPreparation(monkeypatch)
    listBuilds = flistRecordDockerBuilds(monkeypatch)
    storeFake = _FakeImageStore({S_OVERLAYS_IMAGE_LABEL: ""})
    storeFake.dictHeld[f"{S_PROJECT_NAME}:claude"] = _FakeImage(
        S_DERIVED_ID, {S_PINNED_BASE_LABEL: S_BASE_ID},
    )
    storeFake.dictHeld[S_DERIVED_ID] = storeFake.dictHeld[
        f"{S_PROJECT_NAME}:claude"
    ]
    monkeypatch.setattr(
        agentLayerSeparation, "fdictCheckAgentLayerSeparation",
        lambda sBaseImageId, sStackedImageId: {"listViolations": []},
    )
    dictRecord = fdictAcquireForProject(
        dictProject, False, dockerDisposable=storeFake,
    )
    assert len(listBuilds) == 1
    saBuild = listBuilds[0]
    assert saBuild[:3] == ["docker", "buildx", "build"]
    assert f"BASE_IMAGE={S_BASE_ID}" in saBuild
    assert saBuild[saBuild.index("-t") + 1] == f"{S_PROJECT_NAME}:claude"
    assert saBuild[saBuild.index("--platform") + 1] == "linux/amd64"
    assert f"{S_PINNED_BASE_LABEL}={S_BASE_ID}" in saBuild
    assert f"{S_OVERLAYS_IMAGE_LABEL}=claude" in saBuild
    assert os.path.isdir(saBuild[-1]) is False
    assert storeFake.dictHeld[S_BASE_ID].listTags == []
    assert storeFake.dictHeld[S_DERIVED_ID].listTags == [
        f"{S_PROJECT_NAME}:latest",
    ]
    assert dictRecord["sBaseImageId"] == S_BASE_ID
    assert dictRecord["sRunningImageId"] == S_DERIVED_ID
    assert dictRecord["listResolvedOverlays"] == ["claude"]
    with open(dictProject["sConfigPath"]) as fileHandle:
        assert "claude: true" in fileHandle.read()


def testAStackedImageTheDaemonCannotInspectIsNeverTagged(
    tmp_path, monkeypatch,
):
    """A build that left nothing inspectable refuses before the tag."""
    dictProject = fdictRegisterObtainedProject(tmp_path, ["claude"])
    fnStubAcquisitionChain(monkeypatch)
    fnStubBuildContextPreparation(monkeypatch)
    flistRecordDockerBuilds(monkeypatch)
    storeFake = _FakeImageStore({S_OVERLAYS_IMAGE_LABEL: ""})
    with pytest.raises(PinnedImageAcquisitionRefusedError) as errorRaised:
        fdictAcquireForProject(dictProject, False, dockerDisposable=storeFake)
    assert f"{S_PROJECT_NAME}:claude could not be inspected" in str(
        errorRaised.value,
    )
    assert storeFake.dictHeld[S_BASE_ID].listTags == []
    assert fdictReadOriginRecord(S_PROJECT_NAME) is None
