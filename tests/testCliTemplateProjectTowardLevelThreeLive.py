"""A template project, from creation toward Level 3, through the CLI only.

Every step a researcher takes is a ``vaibify`` command or a ``vaibify
do`` action, against a real hub on a loopback port and a real container
on the daemon; the project's name is never its container's id. The
only steps that are not the CLI are the ones the CLI does not offer and
a researcher does in their own shell: ``git init`` and ``git commit``
on the host, and writing a pinned Dockerfile (vaibify writes its own
only for an image it built, which this fixture image is not).

What it can and cannot show. Level 2 and the archive half of Level 3
rest on GitHub and Zenodo, and vaibify's in-container publishers use
fixed service URLs that no test seam redirects (deliberately: a seam
that moves where tokens are sent is a security decision). So the
remote rows are asserted to read never-verified, and never pass, and
the project's level is asserted to stay at 1. Full CLI-to-Level-3
attainment is therefore UNVERIFIED by this test: remote publication was
not exercised.

Byte identity is never asserted. The Level 3 verdict is the server's
sentence, and an output the shadow rerun made differently must carry
both recorded contexts.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one.
"""

import io
import json
import os
import subprocess
import tarfile
import tempfile
import threading

import pytest
from click.testing import CliRunner

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable

# It serves a real hub on a port and binds a control socket, so the
# falsification harness must not run it beside another such test.
pytestmark = [pytest.mark.docker_live, pytest.mark.exclusive]

S_PROJECT_NAME = "vaibifycliendtoend"
S_BASE_IMAGE = "python:3.12-slim"
S_FIXTURE_DOCKERFILE = (
    f"FROM {S_BASE_IMAGE}\n"
    "RUN apt-get update -qq && apt-get install -y -qq git >/dev/null "
    "&& useradd --uid 1000 --create-home researcher "
    # What vaibify's own entrypoint leaves in the workspace, and the
    # mark the hub recognizes a vaibify container by. Docker copies it
    # into the new, empty workspace volume on first mount.
    "&& mkdir -p /workspace/.vaibify/logs "
    "&& touch /workspace/.vaibify/.entrypoint_ready "
    "&& chown -R researcher:researcher /workspace\n"
    # The recipe label is what the start route reads as "built by
    # vaibify"; its value is never compared on the start path.
    "LABEL vaibify-recipe-sha256=cli-end-to-end-fixture\n"
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


@pytest.fixture(scope="module")
def dockerClientWithImage():
    """The fixture image, tagged as the project's own image."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    dockerClient = docker.from_env()
    dockerClient.images.build(
        fileobj=_fbaBuildContext(S_FIXTURE_DOCKERFILE), custom_context=True,
        tag=S_PROJECT_NAME + ":latest", rm=True, labels=fdictLabels(),
    )
    try:
        yield dockerClient
    finally:
        for sReference in (S_PROJECT_NAME + ":latest",):
            try:
                dockerClient.images.remove(sReference, force=True)
            except Exception:  # noqa: BLE001 -- cleanup only
                pass


def _fnRedirectHubState(monkeypatch, sHome):
    """Point the registry, preferences and control socket at ``sHome``."""
    from vaibify.config import preferencesStore, registryManager
    from vaibify.gui import hostControlChannel
    sRegistry = os.path.join(sHome, "registry.json")
    with open(sRegistry, "w", encoding="utf-8") as fileHandle:
        json.dump({"listProjects": []}, fileHandle)
    for objModule, sName, sValue in (
        (registryManager, "_S_REGISTRY_DIRECTORY", sHome),
        (registryManager, "_S_REGISTRY_PATH", sRegistry),
        (registryManager, "_S_LOCK_PATH",
         os.path.join(sHome, "registry.lock")),
        (preferencesStore, "_S_PREFERENCES_DIRECTORY", sHome),
        (preferencesStore, "_S_PREFERENCES_PATH",
         os.path.join(sHome, "preferences.json")),
        (preferencesStore, "_S_LOCK_PATH",
         os.path.join(sHome, "preferences.lock")),
    ):
        monkeypatch.setattr(objModule, sName, sValue)
    # A Unix socket path must fit in about a hundred bytes, which a
    # pytest temporary directory does not.
    sControl = tempfile.mkdtemp(prefix="vaibifyCtl", dir="/tmp")
    monkeypatch.setattr(hostControlChannel, "_S_CONTROL_DIRECTORY", sControl)
    return sControl


@pytest.fixture
def dictLiveHub(dockerClientWithImage, tmp_path, monkeypatch):
    """A real hub on a loopback port, its state under ``tmp_path``."""
    import uvicorn
    from tests.browser.conftest import (
        _fnWaitUntilServing, fsocketBindLoopbackPort,
    )
    from vaibify.gui import agentCouncilStore, pipelineServer
    sHome = str(tmp_path / "home")
    os.makedirs(sHome)
    sControl = _fnRedirectHubState(monkeypatch, sHome)
    socketListening = fsocketBindLoopbackPort()
    iPort = socketListening.getsockname()[1]
    appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=iPort)
    appHub.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=os.path.join(sHome, "agentCouncils")))
    server = uvicorn.Server(uvicorn.Config(
        appHub, host="127.0.0.1", port=iPort, log_level="warning"))
    threadServer = threading.Thread(
        target=server.run, kwargs={"sockets": [socketListening]},
        daemon=True)
    threadServer.start()
    _fnWaitUntilServing(iPort)
    try:
        yield {"iPort": iPort, "appHub": appHub,
               "dockerClient": dockerClientWithImage,
               "pathProjects": tmp_path / "projects"}
    finally:
        server.should_exit = True
        threadServer.join(timeout=15)
        socketListening.close()
        _fnRemoveProjectContainer(dockerClientWithImage)
        for sEntry in os.listdir(sControl):
            os.unlink(os.path.join(sControl, sEntry))
        os.rmdir(sControl)


def _fnRemoveProjectContainer(dockerClient):
    for sKind, sName in (
        ("container", S_PROJECT_NAME),
        ("volume", S_PROJECT_NAME + "-workspace"),
        ("volume", S_PROJECT_NAME + "-credentials"),
    ):
        try:
            if sKind == "container":
                dockerClient.containers.get(sName).remove(force=True)
            else:
                dockerClient.volumes.get(sName).remove(force=True)
        except Exception:  # noqa: BLE001 -- cleanup only
            pass


def _fresultVaibify(listArguments, sDirectory=None):
    """Run one ``vaibify`` command in process, as the researcher would."""
    from vaibify.cli.main import main
    sPrevious = os.getcwd()
    if sDirectory:
        os.chdir(sDirectory)
    try:
        return CliRunner(mix_stderr=False).invoke(
            main, listArguments, catch_exceptions=False)
    finally:
        os.chdir(sPrevious)


def _fdictDo(dictHub, sAction, *listFields):
    """Run ``vaibify do <action> --json`` and return its last JSON line."""
    resultDo = _fresultVaibify([
        "do", sAction, "-p", S_PROJECT_NAME, "--port", str(dictHub["iPort"]),
        "--json", *listFields])
    assert resultDo.exit_code == 0, (
        sAction, resultDo.stdout[-2000:], resultDo.stderr)
    assert "Warning" not in resultDo.stderr, (sAction, resultDo.stderr)
    listLines = [sLine for sLine in resultDo.stdout.splitlines()
                 if sLine.strip().startswith(("{", "["))]
    return json.loads(listLines[-1]) if listLines else {}


def _fsCreateTemplateProject(dictHub):
    """``vaibify init`` the workflow template, then commit it with git.

    ``--minimal`` declares no packages the fixture image lacks; Verify
    Level 3 refuses an image that does not hold what vaibify.yml says.
    """
    pathProject = dictHub["pathProjects"] / S_PROJECT_NAME
    pathProject.mkdir(parents=True)
    resultInit = _fresultVaibify(
        ["init", "--template", "workflow", "--name", S_PROJECT_NAME,
         "--minimal"],
        str(pathProject))
    assert resultInit.exit_code == 0, resultInit.stdout + resultInit.stderr
    for listGit in (
        ["init", "--quiet", "--initial-branch", "main"],
        ["add", "-A"],
        ["-c", "user.email=researcher@example.invalid",
         "-c", "user.name=Researcher", "commit", "--quiet", "-m",
         "start the project"],
    ):
        subprocess.run(["git", *listGit], cwd=str(pathProject), check=True)
    return str(pathProject)


def _fnStartAndCopyIn(dictHub, sProjectDirectory):
    """``vaibify start --detach``, then ``vaibify push`` the project in."""
    resultStart = _fresultVaibify(
        ["start", "--detach", "-p", S_PROJECT_NAME], sProjectDirectory)
    assert resultStart.exit_code == 0, (
        resultStart.stdout + resultStart.stderr)
    resultPush = _fresultVaibify(
        ["push", "-p", S_PROJECT_NAME, sProjectDirectory, "/workspace/"],
        sProjectDirectory)
    assert resultPush.exit_code == 0, resultPush.stdout + resultPush.stderr


def _flistLevelOneCriteria(dictHub):
    dictReadiness = _fdictDo(dictHub, "check-l2-readiness")
    return [(dictBlocker.get("sStepLabel"), dictBlocker["sCriterion"])
            for dictBlocker in dictReadiness.get("listLevel1Blockers", [])]


def _fnSignOff(dictHub, sStep):
    resultSign = _fresultVaibify([
        "verify-step", "-p", S_PROJECT_NAME, "--port", str(dictHub["iPort"]),
        "--step", sStep, "--status", "passed"])
    assert resultSign.exit_code == 0, resultSign.stdout + resultSign.stderr


def _fnClimbLevelOne(dictHub):
    """Every Level 1 row, through ``vaibify do`` and ``verify-step``."""
    assert set(_flistLevelOneCriteria(dictHub)) == {
        ("A01", "input-data-undeclared"), ("A02", "input-data-undeclared")}
    _fdictDo(dictHub, "declare-no-input-data")
    dictScan = _fdictDo(dictHub, "rescan-dependencies")
    assert {dictStep["sState"] for dictStep in dictScan["dictSteps"].values()
            } == {"scanned"}
    dictRun = _fdictDo(dictHub, "run-all", "--timeout", "600")
    assert (dictRun["sType"], dictRun["iExitCode"]) == ("completed", 0)
    # No poll measures the outputs during a CLI run, so the next connect
    # once compared them with the mtimes from before it and flagged the
    # run's own work as modified. The run's recorded measurement is now
    # merged into the baseline: no step is modified, nothing to
    # acknowledge.
    assert set(_flistLevelOneCriteria(dictHub)) == {
        ("A01", "user-not-approved"), ("A02", "user-not-approved")}
    for sStep in ("A01", "A02"):
        _fnSignOff(dictHub, sStep)
    assert _flistLevelOneCriteria(dictHub) == []


def _fnAnswerLocalLevelTwo(dictHub):
    """The Level 2 rows that need no remote, through ``vaibify do``."""
    dictAdded = _fdictDo(dictHub, "add-ai-declaration-step")
    assert dictAdded["dictStep"]["sLabel"] == "I01"
    _fdictDo(dictHub, "generate-ai-declaration-template")
    _fdictDo(dictHub, "declare-ai-model", "sVendor=ExampleVendor",
             "sModelId=example-model-1", "sUseStartDate=2026-01-01",
             "sUseEndDate=2026-02-01")
    _fdictDo(dictHub, "declare-personal-layer", "sStatus=none")
    _fnSignOff(dictHub, "I01")
    dictGaps = _fdictDo(dictHub, "check-l2-readiness")["dictLevel2Gaps"]
    for sKey in ("bAtLeastLevel1", "bAiDeclarationAttested",
                 "bAiModelsDeclared", "bPersonalLayerDeclared"):
        assert dictGaps[sKey] is True, (sKey, dictGaps)


def _fnWriteProjectFile(dictHub, sRelativePath, sContent):
    _fdictDo(dictHub, "write-file", sRelativePath,
             json.dumps({"sContent": sContent}),
             "sWorkdir=/workspace/" + S_PROJECT_NAME)


def _fsBaseImageDigestReference(dictHub):
    return dictHub["dockerClient"].images.get(S_BASE_IMAGE).attrs[
        "RepoDigests"][0]


T_LOCAL_LEVEL_THREE_ROWS = (
    "bManifestComplete", "bManifestMatchesTheFiles", "bDependencyLockHashed",
    "bEnvironmentDigestPinned", "bDockerfilePinned", "bReproduceScriptPinned",
    "bReproduceScriptCurrent", "bDeterminismDeclared",
    "bBinariesDeclaredOrWaived", "bL3ReadinessOK",
)
T_REMOTE_LEVEL_THREE_ROWS = (
    "bEnvelopeInGithubMirror", "bEnvelopeInZenodoArchive", "bImageArchived",
)


def _fnPrepareLocalLevelThree(dictHub):
    """Every Level 3 readiness row that needs no remote."""
    # A researcher writes their own pinned Dockerfile: vaibify composes
    # one only for an image it built, which the fixture image is not.
    _fnWriteProjectFile(dictHub, "Dockerfile", (
        "FROM " + _fsBaseImageDigestReference(dictHub) + "\n"
        "ENV SOURCE_DATE_EPOCH=1767225600\n"
        "RUN apt-get update && apt-get install -y git"
        "  # allow-unpinned\n"))
    # The template needs only the standard library: the project declares
    # no dependency, and the generated lock must say so and still pass.
    _fnWriteProjectFile(dictHub, "requirements.in", "")
    _fdictDo(dictHub, "declare-determinism", "sRepeatabilityAnswer=speed")
    _fdictDo(dictHub, "declare-standalone-binaries", json.dumps({
        "bNoStandaloneBinaries": True, "listDeclaredBinaries": []}))
    assert _fdictDo(dictHub, "generate-reproduce-script")["bWritten"]
    dictTiers = _fdictDo(dictHub, "regenerate-envelope")["dictTierResults"]
    assert all(dictTier["bWritten"] for dictTier in dictTiers.values()), (
        dictTiers)
    _fdictDo(dictHub, "answer-environment-archive", "sAnswer=declined")
    dictGaps = _fdictDo(dictHub, "check-l3-readiness")["dictL3ReadinessGaps"]
    for sKey in T_LOCAL_LEVEL_THREE_ROWS:
        assert dictGaps[sKey] is True, (sKey, dictGaps)
    for sKey in T_REMOTE_LEVEL_THREE_ROWS:
        assert dictGaps[sKey] is False, (sKey, dictGaps)


def _fdictVerifyLevelThree(dictHub):
    """Verify Level 3; the command waits for the rerun it started."""
    assert _fdictDo(dictHub, "verify-l3-reproducibility",
                    "--timeout", "900")["bAccepted"] is True
    dictView = _fdictDo(dictHub, "view-l3-attestation")
    assert not dictView.get("dictInFlight"), dictView
    assert dictView.get("dictCurrentAttestation"), (
        dictView.get("dictLastNoVerdict"))
    return dictView["dictCurrentAttestation"]


def _fnAssertTheVerdictWithoutByteIdentity(dictAttestation):
    """The server's verdict, and both contexts for any output that differs.

    Byte identity is not the acceptance rule: a pass with differing
    bytes is a pass, so the verdict is read as the server's sentence.
    An output the rerun made differently must carry the bytes on both
    sides and both recorded contexts -- the pinned run's in the
    envelope, the rerun's in the assessment. In this run every output
    happened to match, so that branch had nothing to check.
    """
    assert dictAttestation["sStatus"] == "passed", dictAttestation
    assert dictAttestation["sVerdictRendered"].startswith("Reproduced")
    assert dictAttestation["listCarriedPaths"] == ["AI_USAGE.md"]
    dictReport = dictAttestation["dictAssessmentReport"]
    assert dictReport["dictTally"]["iOutputsGraded"] == 2
    for dictOutput in dictReport["listOutputs"]:
        if dictOutput["sOutcome"] in ("byte-identical", "carried"):
            continue
        assert dictOutput["sExpectedSha256"] and dictOutput["sObservedSha256"]
        assert dictReport["dictContexts"], dictOutput


def _fdictReadFileStatus(dictHub):
    """The dashboard's poll, read through the CLI's own researcher session."""
    from vaibify.cli import hubSession
    dictSession = hubSession.fdictOpenResearcherSession(
        S_PROJECT_NAME, dictHub["iPort"])
    try:
        iStatusCode, jsonBody = hubSession.ftSendSessionRequest(
            dictSession, "GET",
            "/api/pipeline/%s/file-status" % dictSession["sContainerId"])
    finally:
        hubSession.fnReleaseContainer(dictSession)
    assert iStatusCode == 200, jsonBody
    assert dictSession["sContainerId"] != S_PROJECT_NAME
    return jsonBody


def _fnAssertRemotesReadNotChecked(dictHub):
    """GitHub and Zenodo were never verified, and never read as passed."""
    assert _fdictDo(dictHub, "check-l2-readiness")["iProofLevel"] == 1
    dictPoll = _fdictReadFileStatus(dictHub)
    assert dictPoll["iProofLevel"] == 1
    dictSyncs = dictPoll["dictWorkflowEnvelopeDetail"]["dictRemoteSyncs"]
    assert dictSyncs["github"] is None and dictSyncs["zenodo"] is None
    setLevelTwo = {dictBlocker["sCriterion"]
                   for dictBlocker in dictPoll["listLevel2Blockers"]}
    assert {"github-verify-stale", "zenodo-verify-stale"} <= setLevelTwo
    # This hub made every output, so none reads as made elsewhere.
    assert dictPoll["dictOutputContextNotes"] == {}


def test_a_template_project_climbs_through_the_cli_alone(dictLiveHub):
    """Creation to a passed Level 3 verification, the CLI the only hand.

    Kills, among others: a host CLI whose run frames the hub refuses
    for want of the workflow acknowledgment; a release that races the
    run's settlement and leaves every next command refused; an empty
    body on an action whose fields are all optional; and a preflight
    that demands a directory of a step that runs nothing, which stopped
    the Level 3 rerun of any project with an AI Declaration step.
    """
    sProjectDirectory = _fsCreateTemplateProject(dictLiveHub)
    _fnStartAndCopyIn(dictLiveHub, sProjectDirectory)
    _fnClimbLevelOne(dictLiveHub)
    _fnAnswerLocalLevelTwo(dictLiveHub)
    _fnPrepareLocalLevelThree(dictLiveHub)
    _fnAssertTheVerdictWithoutByteIdentity(_fdictVerifyLevelThree(dictLiveHub))
    _fnAssertRemotesReadNotChecked(dictLiveHub)
