"""The reader's whole journey, through the real hub, against a real daemon.

The smaller live test beside this one proves that real matplotlib in a
real container reproduces the author's vector figures once the run is
dated by the recorded epoch. It does not prove the ROUTES carry a reader
there. This does: a published project is cloned (identity B), registered
in host mode, converted to a container project, its pinned image is
obtained through the real acquisition chain from a loopback deposit, the
container is started, the files are copied in, the manifest check lists
the corrupted outputs, Run All is driven through the real pipeline
socket, the manifest check then lists nothing, and Verify Level 3 writes
a reproduction record -- after which, and only after which, the poll's
label reads reproduced.

The oracle is independent of the code under test: the author's bytes
come from an author-identity run, in the image, dated by the epoch the
author recorded; the reader is judged against the hashes that run wrote
into the manifest.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one. The image build installs git, numpy and matplotlib.
"""

import hashlib
import io
import json
import os
import subprocess
import tarfile

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.reproductionSourceFixtures import (
    fnWriteJson,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from tests.testImageAcquisition import LoopbackDeposit, fnPointZenodoAt

# It serves the real hub on a port, so the falsification harness must run
# its entry exclusively (tests/testFalsificationSharding.py).
pytestmark = [pytest.mark.docker_live, pytest.mark.exclusive]

S_BASE_IMAGE = "python:3.12-slim"
S_FIXTURE_TAG = "vaibify-journey-fixture:live"
S_PROJECT_NAME = "journeyproject"
S_STEP_DIRECTORY = "MakeGrid"
LIST_OUTPUTS = [
    S_STEP_DIRECTORY + "/value.json", S_STEP_DIRECTORY + "/grid.npz",
    S_STEP_DIRECTORY + "/figure.png", S_STEP_DIRECTORY + "/figure.svg",
    S_STEP_DIRECTORY + "/figure.pdf",
]
I_RECORDED_EPOCH = 1790631466
S_AUTHOR = "author@example.invalid"
S_READER = "reader@example.invalid"
S_DOI = "10.5281/zenodo.7000021"
S_TARBALL_NAME = "environment-image.tar"
S_UNSERVED_REFERENCE = "registry.invalid/journey@sha256:" + "e" * 64

S_SCRIPT_TEXT = '''import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
json.dump({"value": 42}, open("value.json", "w"))
np.savez("grid.npz", grid=np.arange(12.0).reshape(3, 4))
plt.plot([1, 2, 3], [1, 4, 9])
for sExtension in ("png", "svg", "pdf"):
    plt.savefig("figure." + sExtension)
'''

_S_FIXTURE_DOCKERFILE = (
    f"FROM {S_BASE_IMAGE}\n"
    "RUN apt-get update -qq && apt-get install -y -qq git >/dev/null "
    "&& pip install --no-cache-dir --quiet numpy matplotlib "
    "&& useradd --uid 1000 --create-home researcher "
    "&& mkdir -p /workspace && chown researcher:researcher /workspace\n"
    "USER researcher\n"
    "WORKDIR /workspace\n"
)


def _fbaBuildContext(sDockerfile):
    bufferContext = io.BytesIO()
    with tarfile.open(fileobj=bufferContext, mode="w") as fileTar:
        baDockerfile = sDockerfile.encode("utf-8")
        infoMember = tarfile.TarInfo("Dockerfile")
        infoMember.size = len(baDockerfile)
        fileTar.addfile(infoMember, io.BytesIO(baDockerfile))
    bufferContext.seek(0)
    return bufferContext


def _fsEpochPrefix(iEpoch):
    """The runner's real determinism prefix for one epoch."""
    import asyncio
    from vaibify.gui import determinismEnvironment
    return asyncio.run(determinismEnvironment._fsBuildDeterminismEnvPrefix(
        None, "cid", "", iSourceDateEpochOverride=iEpoch))


@pytest.fixture(scope="module")
def liveImage():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    dockerClient = docker.from_env()
    imageFixture, _iterLogs = dockerClient.images.build(
        fileobj=_fbaBuildContext(_S_FIXTURE_DOCKERFILE), custom_context=True,
        tag=S_FIXTURE_TAG, rm=True,
    )
    try:
        yield dockerClient, imageFixture
    finally:
        try:
            dockerClient.images.remove(imageFixture.id, force=True)
        except Exception:  # noqa: BLE001 -- cleanup only
            pass


def _fnPutFile(container, sPath, baContent):
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        infoMember = tarfile.TarInfo(os.path.basename(sPath))
        infoMember.size = len(baContent)
        infoMember.uid = infoMember.gid = 1000
        fileTar.addfile(infoMember, io.BytesIO(baContent))
    bufferTar.seek(0)
    container.put_archive(os.path.dirname(sPath), bufferTar)


def _fbaGetFile(container, sPath):
    iterChunks, _stat = container.get_archive(sPath)
    bufferTar = io.BytesIO(b"".join(iterChunks))
    with tarfile.open(fileobj=bufferTar) as fileTar:
        return fileTar.extractfile(fileTar.getmembers()[0]).read()


def _flockFromFreeze(sFreeze):
    """A hashed lock of exactly what the image has installed."""
    listLines = []
    for sLine in sFreeze.splitlines():
        if "==" not in sLine:
            continue
        sDigest = hashlib.sha256(sLine.encode("utf-8")).hexdigest()
        listLines.append(f"{sLine} \\\n    --hash=sha256:{sDigest}")
    return "\n".join(listLines) + "\n"


def fdictAuthorOutputs(dockerClient):
    """The author's bytes: run the step in the image, dated by the record."""
    container = dockerClient.containers.run(
        S_FIXTURE_TAG, ["sleep", "600"], detach=True, labels=fdictLabels())
    try:
        container.exec_run(["mkdir", "-p", "/workspace/author"])
        _fnPutFile(container, "/workspace/author/generate.py",
                   S_SCRIPT_TEXT.encode("utf-8"))
        iExit, baOutput = container.exec_run(
            ["bash", "-c", _fsEpochPrefix(I_RECORDED_EPOCH)
             + "cd /workspace/author && python3 generate.py"])
        assert iExit == 0, baOutput.decode("utf-8", errors="replace")
        iFreeze, baFreeze = container.exec_run(
            ["python3", "-m", "pip", "freeze", "--all"])
        assert iFreeze == 0, baFreeze
        dictOutputs = {
            sRelative: _fbaGetFile(
                container, "/workspace/author/" + os.path.basename(sRelative))
            for sRelative in LIST_OUTPUTS
        }
        dictOutputs["requirements.lock"] = _flockFromFreeze(
            baFreeze.decode("utf-8")).encode("utf-8")
        return dictOutputs
    finally:
        container.remove(force=True)


def _fnAuthorGit(sRepo, *listArguments):
    return fsRunGit(
        ["-c", f"user.email={S_AUTHOR}", "-c", "user.name=Author",
         *listArguments], sRepo)


def fsPublishAuthorsProject(sRepo, dictOutputs, sTarballSha, iTarballBytes,
                            sArchitecture):
    """Write, pin and commit the author's project as the AUTHOR."""
    os.makedirs(sRepo, exist_ok=True)
    fsRunGit(["init", "--quiet"], sRepo)
    fsRunGit(["symbolic-ref", "HEAD", "refs/heads/main"], sRepo)
    fnWriteText(sRepo, "vaibify.yml", f"projectName: {S_PROJECT_NAME}\n")
    fnWriteText(sRepo, S_STEP_DIRECTORY + "/generate.py", S_SCRIPT_TEXT)
    for sRelative in LIST_OUTPUTS:
        with open(os.path.join(sRepo, sRelative), "wb") as fileOut:
            fileOut.write(dictOutputs[sRelative])
    from vaibify.reproducibility import determinismGate
    from vaibify.reproducibility.reproduceScriptGenerator import (
        fsRenderReproduceScript,
    )
    fnWriteText(
        sRepo, "requirements.lock",
        dictOutputs["requirements.lock"].decode("utf-8"))
    fnWriteText(
        sRepo, "Dockerfile",
        "FROM python@sha256:" + "a" * 64 + "\n"
        f"ENV SOURCE_DATE_EPOCH={I_RECORDED_EPOCH}\n")
    dictWorkflow = {
        "sWorkflowName": "Journey", "sPlotDirectory": "figures",
        "bNoStandaloneBinaries": True, "listDeclaredBinaries": [],
        "dictDeterminism": {
            determinismGate.S_BLAS_ANSWER_KEY: determinismGate.S_BLAS_ACCEPTED,
            determinismGate.S_OMP_ANSWER_KEY: determinismGate.S_OMP_UNPINNED,
            determinismGate.S_MKL_ANSWER_KEY: determinismGate.S_MKL_NOT_USED,
        },
        "listSteps": [{
            "sName": "Make Grid", "sStepId": "make-grid",
            "sDirectory": S_STEP_DIRECTORY, "bRunEnabled": True,
            "bPlotOnly": False,
            "saDataCommands": ["python3 generate.py"],
            "saOutputDataFiles": [
                os.path.basename(s) for s in LIST_OUTPUTS],
            "saPlotCommands": [], "saPlotFiles": [],
        }],
    }
    fnWriteJson(sRepo, ".vaibify/projects/project.json", dictWorkflow)
    fnWriteText(sRepo, "reproduce.sh", fsRenderReproduceScript(dictWorkflow))
    fnWriteJson(sRepo, ".vaibify/environment.json", {
        "iSourceDateEpoch": I_RECORDED_EPOCH,
        "dictContainer": {
            "sImageDigest": S_UNSERVED_REFERENCE,
            "sArchitecture": sArchitecture,
            "dictImageArchive": {
                "sVersionDoi": S_DOI,
                "sConceptDoi": "10.5281/zenodo.7000020",
                "sTarballName": S_TARBALL_NAME,
                "sTarballSha256": sTarballSha, "iTarballBytes": iTarballBytes,
                "sProvenance": "original",
                "sImageDigest": S_UNSERVED_REFERENCE,
                "sArchitecture": sArchitecture,
            },
        },
    })
    fnWriteManifest(
        sRepo, [S_STEP_DIRECTORY + "/generate.py", *LIST_OUTPUTS])
    from vaibify.reproducibility import l3Attestation
    l3Attestation.fnWriteAttestation(sRepo, l3Attestation.fdictBuildAttestation(
        sStatus=l3Attestation.S_STATUS_PASSED,
        sManifestDigest=l3Attestation.fsCurrentManifestDigest(sRepo),
        sImageDigest=S_UNSERVED_REFERENCE, fDurationSeconds=1.0,
        iOutputHashesMatched=len(LIST_OUTPUTS),
        iOutputHashesTotal=len(LIST_OUTPUTS), listDivergedHashes=[],
        sAttestedAtUtc=l3Attestation.fsCurrentTimestampUtc()))
    _fnAuthorGit(sRepo, "add", "-A")
    _fnAuthorGit(sRepo, "commit", "--quiet", "-m", "author publishes")
    _fnAuthorGit(sRepo, "commit", "--quiet", "--allow-empty", "-m",
                 "publish the record")
    return sRepo


@pytest.fixture
def dictPublished(liveImage, tmp_path_factory, monkeypatch):
    dockerClient, imageFixture = liveImage
    dictOutputs = fdictAuthorOutputs(dockerClient)
    baTarball = b"".join(imageFixture.save(named=False))
    sTarballSha = "sha256:" + hashlib.sha256(baTarball).hexdigest()
    sArchitecture = str(imageFixture.attrs.get("Architecture") or "")
    pathBase = tmp_path_factory.mktemp("journey")
    pathFiles = pathBase / "zenodo" / S_DOI / "files"
    pathFiles.mkdir(parents=True)
    (pathFiles / S_TARBALL_NAME).write_bytes(baTarball)
    sRepo = fsPublishAuthorsProject(
        str(pathBase / S_PROJECT_NAME), dictOutputs, sTarballSha,
        len(baTarball), sArchitecture)
    # The reader's clone, identity B: the same files, the author's commits.
    fsRunGit(["config", "user.email", S_READER], sRepo)
    # The tag must not serve: the chain has to take the archive link.
    try:
        dockerClient.images.remove(S_FIXTURE_TAG)
    except Exception:  # noqa: BLE001 -- the image id still holds the layers
        pass
    return {
        "sRepo": sRepo, "pathZenodo": pathBase / "zenodo",
        "dictOutputs": dictOutputs, "sArchitecture": sArchitecture,
        "dockerClient": dockerClient, "pathBase": pathBase,
        "sHeadCommit": fsRunGit(["rev-parse", "HEAD"], sRepo),
    }


@pytest.fixture
def hubClient(dictPublished, tmp_path, monkeypatch):
    """The real hub over the real daemon, its state redirected to tmp_path."""
    from vaibify.config import (
        containerLock, operationJournal, preferencesStore, registryManager,
    )
    from vaibify.gui import agentCouncilStore, pipelineServer
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    sRegistry = os.path.join(sHome, "registry.json")
    with open(sRegistry, "w") as fileHandle:
        json.dump({"listProjects": []}, fileHandle)
    for objModule, sName, objValue in (
        (registryManager, "_S_REGISTRY_DIRECTORY", sHome),
        (registryManager, "_S_REGISTRY_PATH", sRegistry),
        (registryManager, "_S_LOCK_PATH", os.path.join(sHome, "registry.lock")),
        (preferencesStore, "_S_PREFERENCES_DIRECTORY", sHome),
        (preferencesStore, "_S_PREFERENCES_PATH",
         os.path.join(sHome, "preferences.json")),
        (preferencesStore, "_S_LOCK_PATH",
         os.path.join(sHome, "preferences.lock")),
        (operationJournal, "_S_JOURNAL_DIRECTORY",
         os.path.join(sHome, "journal")),
        (containerLock, "_S_LOCK_DIRECTORY", os.path.join(sHome, "locks")),
    ):
        monkeypatch.setattr(objModule, sName, objValue)
    from tests.browser.conftest import _fiFreePort, _fnWaitUntilServing
    import threading
    import uvicorn
    import requests
    iPort = _fiFreePort()
    appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=iPort)
    appHub.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=os.path.join(sHome, "agentCouncils")))
    server = uvicorn.Server(uvicorn.Config(
        appHub, host="127.0.0.1", port=iPort, log_level="warning"))
    threadServer = threading.Thread(target=server.run, daemon=True)
    threadServer.start()
    _fnWaitUntilServing(iPort)
    sBaseUrl = f"http://127.0.0.1:{iPort}"
    sCredential = fsBootstrapCredential(appHub)

    class HubClient:
        """A session against the running hub, as a browser holds one."""

        def __init__(self):
            self.session = requests.Session()
            self.session.headers["X-Session-Token"] = sCredential
            self.sCredential = sCredential
            self.iPort = iPort

        def get(self, sPath, **dictArgs):
            return self.session.get(sBaseUrl + sPath, timeout=600, **dictArgs)

        def post(self, sPath, json=None, **dictArgs):
            return self.session.post(
                sBaseUrl + sPath, json=json, timeout=900, **dictArgs)

    yield HubClient(), appHub, sHome
    server.should_exit = True
    threadServer.join(timeout=15)
    try:
        dictPublished["dockerClient"].containers.get(S_PROJECT_NAME).remove(
            force=True)
    except Exception:  # noqa: BLE001 -- cleanup only
        pass
    try:
        dictPublished["dockerClient"].volumes.get(
            f"{S_PROJECT_NAME}-workspace").remove(force=True)
    except Exception:  # noqa: BLE001 -- cleanup only
        pass


def _fnCorruptEveryOutput(sRepo):
    """Stand in for the reader's own host rerun: every output differs."""
    for sRelative in LIST_OUTPUTS:
        with open(os.path.join(sRepo, sRelative), "wb") as fileOut:
            fileOut.write(b"the reader's own computer made this\n")


def _fnStartTheContainer(client):
    """Answer the image-trust question the obtained image raises, then start."""
    import time
    responseRefused = client.post(f"/api/containers/{S_PROJECT_NAME}/start")
    assert responseRefused.status_code == 409
    assert responseRefused.json()["sAction"] == "confirm-image-trust"
    dictPrompt = client.get(
        f"/api/registry/{S_PROJECT_NAME}/image-trust").json()
    assert dictPrompt["bBuiltByVaibify"] is False
    assert dictPrompt["sObtainedFrom"] == "archive"
    responseAnswer = client.post(
        f"/api/registry/{S_PROJECT_NAME}/image-trust", json={
            "sImageDigest": dictPrompt["sImageDigest"],
            "sChoice": "as-built", "bWithCredentials": False,
        })
    assert responseAnswer.status_code == 200, responseAnswer.text
    responseStart = client.post(f"/api/containers/{S_PROJECT_NAME}/start")
    assert responseStart.status_code == 202, responseStart.text
    fDeadline = time.monotonic() + 180
    while time.monotonic() < fDeadline:
        dictStatus = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status").json()
        if dictStatus.get("sState") in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(2)
    return dictStatus


def _fdictAwaitVerification(client, sContainerId, dictHeaders):
    """Poll the attestation route until the run has left a verdict or a refusal."""
    import time
    fDeadline = time.monotonic() + 150
    while time.monotonic() < fDeadline:
        dictStatus = client.get(
            f"/api/workflow/{sContainerId}/level3/attestation",
            headers=dictHeaders).json()
        if not dictStatus.get("dictInFlight") and (
            dictStatus.get("dictLatestReproduction")
            or dictStatus.get("dictLastNoVerdict")
        ):
            return dictStatus
        time.sleep(3)
    raise AssertionError("the verification never settled")


def _flistRunPipelineAction(client, sContainerId, sLeaseId, sAction):
    """Drive one action through the real pipeline socket; return its events."""
    import time
    from websockets.sync.client import connect
    sUrl = (f"ws://127.0.0.1:{client.iPort}/ws/pipeline/{sContainerId}"
            f"?sToken={client.sCredential}&sLeaseId={sLeaseId}")
    listEvents = []
    with connect(
        sUrl, origin=f"http://127.0.0.1:{client.iPort}", open_timeout=30,
    ) as websocket:
        dictBound = json.loads(websocket.recv(timeout=60))
        assert dictBound["sType"] == "workflowBound", dictBound
        # What the dashboard does: acknowledge the workflow it displays.
        websocket.send(json.dumps({
            "sAction": sAction,
            "sAcknowledgedSourceFingerprint":
                dictBound["sExactSourceFingerprint"],
            "sAcknowledgedWorkflowPath": dictBound["sWorkflowPath"],
        }))
        fDeadline = time.monotonic() + 600
        while time.monotonic() < fDeadline:
            dictEvent = json.loads(websocket.recv(timeout=600))
            listEvents.append(dictEvent)
            if dictEvent.get("sType") in (
                "completed", "failed", "runRefused", "error",
            ):
                break
    return listEvents


@pytest.mark.falsification
def test_the_readers_journey_through_the_real_hub(
    dictPublished, hubClient, monkeypatch,
):
    """Clone, convert, obtain, start, copy in, check, run, verify, label.

    Kills: comparing the record's prefixed manifest digest with the
    snapshot's bare hash as strings, which never matched, so a real
    reproduction never earned its label. Only driving the whole journey
    found it: every unit fixture had hand-written both sides alike.

    The reader's own host sign-offs (Level 1) are not simulated; the
    assertion is that the level never rises above 1, because Level 2
    and Level 3 record the author's publication.
    """
    from vaibify.config import registryManager
    client, appHub, sHome = hubClient
    sRepo = dictPublished["sRepo"]
    _fnCorruptEveryOutput(sRepo)
    registryManager.fnAddProject(sRepo, sMode="host")
    sHostName = os.path.basename(sRepo)
    dictPinned = client.get(
        f"/api/registry/{sHostName}/pinned-environment")
    assert dictPinned.status_code == 200
    assert dictPinned.json()["bObtainable"] is True
    dictConverted = client.post(
        f"/api/registry/{sHostName}/convert-to-container", json={
            "sProjectName": S_PROJECT_NAME, "sWorkflowName": "Journey",
            "sEnvironmentSource": "archive", "bAllowEmulation": False,
            # The Files page leaves everything ticked.
            "saSeedPaths": sorted(
                sEntry for sEntry in os.listdir(sRepo) if sEntry != ".git"),
            "bRestoreCommittedFiles": False,
        })
    print("CONVERT", dictConverted.status_code, dictConverted.text[:500])
    assert dictConverted.status_code == 200, dictConverted.text
    with LoopbackDeposit(dictPublished["pathZenodo"]) as server:
        fnPointZenodoAt(monkeypatch, server)
        responseAcquire = client.post(
            f"/api/containers/{S_PROJECT_NAME}/acquire-image")
    assert responseAcquire.status_code == 200, responseAcquire.text
    assert responseAcquire.json()["dictImageOrigin"]["sObtainedFrom"] == (
        "archive")
    dictStarted = _fnStartTheContainer(client)
    assert dictStarted["sState"] == "SUCCEEDED", dictStarted
    sContainerId, sLeaseId = dictStarted["sContainerId"], dictStarted["sLeaseId"]
    dictHeaders = {"X-Vaibify-Lease": sLeaseId}
    responseSeed = client.post(
        f"/api/files/{sContainerId}/seed-workspace",
        json={"bApplyPending": True}, headers=dictHeaders)
    assert responseSeed.status_code == 200, responseSeed.text
    listWorkflows = client.get(
        f"/api/workflows/{sContainerId}", headers=dictHeaders).json()
    print("WORKFLOWS", listWorkflows)
    sWorkflowPath = [d for d in listWorkflows
                     if d["sPath"].endswith("/project.json")][0]["sPath"]
    responseConnect = client.post(
        f"/api/connect/{sContainerId}", headers=dictHeaders,
        params={"sWorkflowPath": sWorkflowPath})
    print("CONNECT", responseConnect.status_code, responseConnect.text[:300])
    responseVerify = client.post(
        f"/api/workflow/{sContainerId}/manifest/verify", headers=dictHeaders)
    assert responseVerify.status_code == 200, responseVerify.text
    dictBefore = responseVerify.json()
    assert sorted(d["sPath"] for d in dictBefore["listMismatches"]) == sorted(
        LIST_OUTPUTS)
    assert dictBefore["sManifestOwnership"] == "foreign"
    listEvents = _flistRunPipelineAction(
        client, sContainerId, sLeaseId, "runAll")
    assert listEvents[-1]["sType"] == "completed", listEvents[-3:]
    sRunLog = "\n".join(
        str(e.get("sLine") or e.get("sText") or "") for e in listEvents)
    assert (f"Run date pinned to epoch {I_RECORDED_EPOCH}: replaying the "
            "author's recorded epoch") in sRunLog, sRunLog
    responseAfter = client.post(
        f"/api/workflow/{sContainerId}/manifest/verify", headers=dictHeaders)
    assert responseAfter.status_code == 200, responseAfter.text
    dictAfter = responseAfter.json()
    assert dictAfter["listMismatches"] == [], dictAfter
    iEntriesTheAuthorPinned = len([
        sLine for sLine in fsRunGit(
            ["show", "HEAD:MANIFEST.sha256"], sRepo).splitlines()
        if sLine and not sLine.startswith("#")])
    assert dictAfter["iMatching"] == dictAfter["iTotal"] == (
        iEntriesTheAuthorPinned)
    dictLabelBefore = client.get(
        f"/api/pipeline/{sContainerId}/file-status",
        headers=dictHeaders).json()["dictReproductionLabel"]
    assert dictLabelBefore["bShow"] is False
    assert dictLabelBefore["sState"] == "absent"
    responseVerifyL3 = client.post(
        f"/api/workflow/{sContainerId}/level3/verify", headers=dictHeaders)
    assert responseVerifyL3.status_code == 200, responseVerifyL3.text
    dictSettled = _fdictAwaitVerification(client, sContainerId, dictHeaders)
    dictPoll = client.get(
        f"/api/pipeline/{sContainerId}/file-status",
        headers=dictHeaders).json()
    # A reader reaches no new level: the level gates are the author's.
    assert dictPoll["iProofLevel"] <= 1
    dictLabel = dictPoll["dictReproductionLabel"]
    assert dictLabel["bShow"] is True, dictLabel
    assert dictLabel["sState"] == "reproduced"
    assert dictLabel["bEmulated"] is False
    assert dictLabel["sPlatform"] == "linux/" + dictPublished["sArchitecture"]
    dictRecord = dictSettled["dictLatestReproduction"]
    assert dictRecord["sVerdict"] == "reproduced"
    assert dictRecord["bBaselineKnown"] is True
    assert dictRecord["dictSource"]["sResolvedCommit"] == (
        dictPublished["sHeadCommit"])
    assert dictRecord["sReproducedManifestPath"].startswith(
        ".vaibify/reproducedManifests/")
    listPinnedOrPublished = set(LIST_OUTPUTS) | {
        "MANIFEST.sha256", ".vaibify/l3_attestation.json",
        ".vaibify/environment.json"}
    assert not listPinnedOrPublished & set(
        dictRecord["listPathsDifferingFromBaseline"])
    # The author's claim is untouched, byte for byte.
    containerProject = dictPublished["dockerClient"].containers.get(
        S_PROJECT_NAME)
    for sRelative in ("MANIFEST.sha256", ".vaibify/l3_attestation.json"):
        iExit, baInContainer = containerProject.exec_run(
            ["cat", "/workspace/" + S_PROJECT_NAME + "/" + sRelative])
        assert iExit == 0
        baAuthors = subprocess.run(
            ["git", "-C", sRepo, "show",
             dictPublished["sHeadCommit"] + ":" + sRelative],
            capture_output=True, check=True).stdout
        assert baInContainer == baAuthors, sRelative
    assert dictSettled["dictCurrentAttestation"]["fDurationSeconds"] == 1.0
