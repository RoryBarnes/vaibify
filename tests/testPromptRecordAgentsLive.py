"""Codex and Gemini transcripts, captured from a real container.

The unit tests read synthetic transcripts shaped like each CLI's files.
Only the CLIs themselves can say whether that shape is still the one
they write, so this builds an image holding both, runs each once inside
a project directory and Codex once outside it, and drives the REAL
listing program, sanitizer, landing and viewer over what they wrote.
Neither CLI is given a credential: each writes its session file before
it reaches the network, which is all this needs, and no model is
called.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one. Building the fixture image installs both CLIs from npm,
which costs minutes, so the ``agent_cli_live`` marker keeps this out of
the per-PR docker-smoke lane; the nightly container-acceptance workflow
runs it with the daemon demanded.
"""

import io
import os

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import promptRecordManager, promptRecordViewer
from vaibify.gui.transcriptSanitizer import fbSanitizerAvailable
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles

pytestmark = [pytest.mark.docker_live, pytest.mark.agent_cli_live]

S_FIXTURE_TAG = "vaibify-transcript-agents:live"
S_DOCKERFILE = """FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 \\
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @openai/codex @google/gemini-cli
RUN useradd -m researcher
USER researcher
"""
S_PROJECT = "/home/researcher/projectRepo"
S_ELSEWHERE = "/home/researcher/elsewhere"
S_PROMPT = "summarize the table"


def _fnBuildFixtureImage(clientDocker):
    import docker
    try:
        clientDocker.images.get(S_FIXTURE_TAG)
        return
    except docker.errors.ImageNotFound:
        pass
    clientDocker.images.build(
        fileobj=io.BytesIO(S_DOCKERFILE.encode()), tag=S_FIXTURE_TAG,
        rm=True, labels=fdictLabels(),
    )


def _fnRunAgent(container, sDirectory, sCommand):
    """Run one CLI turn; it fails without a credential, after writing."""
    container.exec_run(
        ["bash", "-c", f"mkdir -p {sDirectory} && cd {sDirectory} && "
         f"timeout 120 {sCommand} </dev/null >/dev/null 2>&1; true"],
        environment={"GEMINI_CLI_TRUST_WORKSPACE": "true",
                     "GEMINI_API_KEY": "not-a-credential"},
    )


@pytest.fixture(scope="module")
def tAgentContainer():
    fnRequireDaemonReachable()
    if not fbSanitizerAvailable():
        sMessage = "detect-secrets not installed (vaibify[replay])"
        if os.environ.get("VAIBIFY_REQUIRE_DOCKER_DAEMON"):
            pytest.fail(sMessage + "; a lane that demands this run "
                        "must not skip it green")
        pytest.skip(sMessage)
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    _fnBuildFixtureImage(clientDocker)
    container = clientDocker.containers.run(
        S_FIXTURE_TAG, ["sleep", "900"], detach=True, labels=fdictLabels(),
    )
    try:
        _fnRunAgent(container, S_PROJECT,
                    f"codex exec --skip-git-repo-check '{S_PROMPT}'")
        _fnRunAgent(container, S_PROJECT, f"gemini -p '{S_PROMPT}'")
        _fnRunAgent(container, S_ELSEWHERE,
                    "codex exec --skip-git-repo-check 'other work'")
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def test_real_codex_and_gemini_sessions_are_listed_scoped_and_read(
    tAgentContainer, tmp_path,
):
    container, connection = tAgentContainer
    dictListing = promptRecordManager.fdictListContainerTranscripts(
        connection, container.id)
    dictByProvider = {}
    for dictEntry in dictListing.values():
        dictByProvider.setdefault(dictEntry["sProvider"], []).append(
            dictEntry["sLaunchDirectory"])
    assert sorted(dictByProvider.get("codex", [])) == [
        S_ELSEWHERE, S_PROJECT], dictListing
    assert dictByProvider.get("gemini") == [S_PROJECT], dictListing

    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    dictSanitized = promptRecordManager.fdictSanitizeNewTranscriptLines(
        connection, container.id, filesRepo, dictListing, S_PROJECT, [])
    dictSummary = promptRecordManager.fdictLandSanitizedSessions(
        filesRepo, dictSanitized)
    assert dictSummary["iSessionsOutsideProject"] == 1
    dictIndex = promptRecordManager.fdictLoadIndex(filesRepo)
    assert promptRecordManager.fbVerifyCaptureChain(dictIndex)
    listSessions = promptRecordManager.flistSummarizeSessions(dictIndex)
    assert sorted(d["sProvider"] for d in listSessions) == [
        "codex", "gemini"]
    for dictSession in listSessions:
        sText = filesRepo.fsReadText(
            promptRecordManager.S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/"
            + dictSession["sSessionFileName"])
        listTurns = promptRecordViewer.flistParseTranscriptTurns(
            sText, dictSession["sProvider"])
        listPrompts = [
            d["sText"] for d in listTurns if d["sKind"] == "prompt"]
        assert listPrompts == [S_PROMPT], (
            dictSession["sProvider"], listTurns)


def test_a_real_transcript_truncated_to_nothing_is_recaptured_empty(
    tAgentContainer, tmp_path,
):
    """Truncate the captured Gemini transcript in place: the record must
    follow it to empty, not keep the text and skip the file forever."""
    container, connection = tAgentContainer
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))

    def fnCapture():
        dictListing = promptRecordManager.fdictListContainerTranscripts(
            connection, container.id)
        promptRecordManager.fdictLandSanitizedSessions(
            filesRepo, promptRecordManager.fdictSanitizeNewTranscriptLines(
                connection, container.id, filesRepo, dictListing,
                S_PROJECT, []))
        return dictListing

    dictListing = fnCapture()
    [sGeminiPath] = [
        sPath for sPath, dictEntry in dictListing.items()
        if dictEntry["sProvider"] == "gemini"]
    iExit, _ = container.exec_run(["truncate", "-s", "0", sGeminiPath])
    assert iExit == 0
    fnCapture()
    fnCapture()
    dictIndex = promptRecordManager.fdictLoadIndex(filesRepo)
    [dictSession] = [
        d for d in promptRecordManager.flistSummarizeSessions(dictIndex)
        if d["sProvider"] == "gemini"]
    assert filesRepo.fsReadText(
        promptRecordManager.S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/"
        + dictSession["sSessionFileName"]) == ""
    assert promptRecordManager.fbVerifyCaptureChain(dictIndex)
