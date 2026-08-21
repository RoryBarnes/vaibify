"""Build progress: live tail, duplicate refusal, and lifecycle truth.

The dashboard build used to be a blank spinner holding one POST open
for up to an hour. The progress record these tests cover is what
replaced it, and the properties that matter are the honest ones: the
record must close (with the true outcome) no matter how the build
ends, a second build for the same project must be refused while one
is live — which is also how a reopened tab re-attaches — and every
line shown to the researcher must have passed credential redaction.
"""

import subprocess
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.docker import imageBuilder
from vaibify.gui import buildRoutes


@pytest.fixture(autouse=True)
def fixtureClearProgressRecords():
    buildRoutes._DICT_BUILD_PROGRESS.clear()
    yield
    buildRoutes._DICT_BUILD_PROGRESS.clear()


@pytest.fixture
def fixtureClient():
    app = FastAPI()
    buildRoutes.fnRegisterAll(
        app, {"require": lambda *aArgs: None, "docker": None},
    )
    return TestClient(app)


def _fnFakeBuildEmittingLines(
    configProject, sDockerDir, bNoCache=False, sProjectDirectory=None,
):
    """Stand in for fnBuildFromConfig: emit two lines through the sink.

    ``sProjectDirectory`` is asserted, not merely accepted: the hub
    serves projects from its own launch directory, so a build left to
    resolve the directory itself reads an unrelated repository's git
    remote (2026-08-21). A double that swallowed the argument would
    keep passing after the route stopped sending it.
    """
    assert sProjectDirectory, (
        "the route must tell the build which project directory it is "
        "building; without it the build reads the hub's own directory"
    )
    del configProject, sDockerDir, bNoCache
    sinkLocal = imageBuilder._threadLocalBuildSink
    fnLineSink = getattr(sinkLocal, "fnLineSink", None)
    assert fnLineSink is not None, (
        "the route must install the build line sink before building"
    )
    fnLineSink("Step 1/2 : FROM base\n")
    fnLineSink("Step 2/2 : RUN prepare\n")


def _fdictPatchedBuildDependencies(fnBuild):
    """Patch every seam _fnExecuteBuild reaches, returning the managers."""
    return [
        patch(
            "vaibify.gui.registryRoutes._fdictRequireProject",
            return_value={"sConfigPath": "/tmp/unused/vaibify.yml"},
        ),
        patch(
            "vaibify.cli.configLoader.fconfigLoadFromPath",
            return_value=object(),
        ),
        patch(
            "vaibify.cli.configLoader.fsDockerDir",
            return_value="/tmp/unused/docker",
        ),
        patch(
            "vaibify.cli.commandBuild.fnBuildFromConfig",
            side_effect=fnBuild,
        ),
    ]


def _fdictRunBuild(fixtureClient, fnBuild, sName="proj"):
    """POST a build with all external seams patched; return the response."""
    listPatches = _fdictPatchedBuildDependencies(fnBuild)
    for managerPatch in listPatches:
        managerPatch.start()
    try:
        return fixtureClient.post(f"/api/containers/{sName}/build")
    finally:
        for managerPatch in listPatches:
            managerPatch.stop()


def test_progress_is_unknown_before_any_build(fixtureClient):
    dictProgress = fixtureClient.get(
        "/api/containers/proj/build/progress"
    ).json()
    assert dictProgress["bKnown"] is False
    assert dictProgress["bLive"] is False


def test_successful_build_records_lines_and_outcome(fixtureClient):
    response = _fdictRunBuild(fixtureClient, _fnFakeBuildEmittingLines)
    assert response.status_code == 200
    dictProgress = fixtureClient.get(
        "/api/containers/proj/build/progress"
    ).json()
    assert dictProgress["bKnown"] is True
    assert dictProgress["bLive"] is False
    assert dictProgress["sOutcome"] == "succeeded"
    assert dictProgress["iLineCount"] == 2
    assert dictProgress["saTailLines"] == [
        "Step 1/2 : FROM base", "Step 2/2 : RUN prepare",
    ]


@pytest.mark.falsification
def test_failed_build_closes_the_record_as_failed(fixtureClient):
    """A dead build must never leave a live record behind.

    A record stuck at bLive=True after the build died would make every
    later build click answer 409 "already running" forever, and a
    re-attached tab would watch a build that no longer exists.

    Kills: In buildRoutes._fnExecuteBuild, drop the
    _fnCloseBuildProgress(dictProgress, "failed") call from the
    BaseException handler.
    """

    def fnExplodingBuild(*taArguments, **dictArguments):
        raise RuntimeError("simulated docker failure")

    response = _fdictRunBuild(fixtureClient, fnExplodingBuild)
    assert response.status_code == 500
    dictProgress = fixtureClient.get(
        "/api/containers/proj/build/progress"
    ).json()
    assert dictProgress["bLive"] is False
    assert dictProgress["sOutcome"] == "failed"


def test_config_load_failure_also_closes_the_record(fixtureClient):
    """A failure before the build starts must close the record too."""
    listPatches = [
        patch(
            "vaibify.gui.registryRoutes._fdictRequireProject",
            return_value={"sConfigPath": "/tmp/unused/vaibify.yml"},
        ),
        patch(
            "vaibify.cli.configLoader.fconfigLoadFromPath",
            side_effect=ValueError("bad vaibify.yml"),
        ),
    ]
    for managerPatch in listPatches:
        managerPatch.start()
    try:
        response = fixtureClient.post("/api/containers/proj/build")
    finally:
        for managerPatch in listPatches:
            managerPatch.stop()
    assert response.status_code == 500
    dictProgress = fixtureClient.get(
        "/api/containers/proj/build/progress"
    ).json()
    assert dictProgress["bLive"] is False
    assert dictProgress["sOutcome"] == "failed"


def test_second_build_is_refused_while_one_is_live(fixtureClient):
    buildRoutes._fdictOpenBuildProgress("proj")
    with patch(
        "vaibify.gui.registryRoutes._fdictRequireProject",
        return_value={"sConfigPath": "/tmp/unused/vaibify.yml"},
    ):
        response = fixtureClient.post("/api/containers/proj/build")
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]["sMessage"]


def test_finished_build_does_not_block_the_next_one(fixtureClient):
    response = _fdictRunBuild(fixtureClient, _fnFakeBuildEmittingLines)
    assert response.status_code == 200
    response = _fdictRunBuild(fixtureClient, _fnFakeBuildEmittingLines)
    assert response.status_code == 200


@pytest.mark.falsification
def test_sink_lines_pass_credential_redaction(tmp_path):
    """Lines reaching the sink must be credential-redacted.

    The build tail shown in the dashboard goes through the same
    redaction the failure tail does; an unredacted sink would put a
    researcher's token on screen (and in any screenshot they share).

    Kills: In imageBuilder._fnOfferLineToSink, hand the sink sLine
    directly instead of fsRedactBuildOutputCredentials(sLine).
    """
    listCaptured = []
    imageBuilder.fnSetThreadBuildLineSink(listCaptured.append)
    try:
        procBuild = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys; sys.stderr.write("
                "'fetch https://user:secretToken@example.com/repo\\n')",
            ],
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        imageBuilder._fsStreamAndCaptureStderr(procBuild)
        procBuild.wait()
    finally:
        imageBuilder.fnSetThreadBuildLineSink(None)
    assert len(listCaptured) == 1
    assert "secretToken" not in listCaptured[0]
    assert "example.com/repo" in listCaptured[0]


def test_sink_failure_does_not_fail_the_stream(capsys):
    """A broken progress display must never break the build itself."""

    def fnExplodingSink(sLine):
        raise ValueError("display gone")

    imageBuilder.fnSetThreadBuildLineSink(fnExplodingSink)
    try:
        procBuild = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys; sys.stderr.write('one line\\n')",
            ],
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        sTail = imageBuilder._fsStreamAndCaptureStderr(procBuild)
        procBuild.wait()
    finally:
        imageBuilder.fnSetThreadBuildLineSink(None)
    assert "one line" in sTail
