"""An agent overlay stacks on an obtained image through the real builder.

A reader's clone obtains the author's pinned image by ID and stacks the
coding agents they asked for on it. The unit tests record the build
commands without running them, and so recorded ``BASE_IMAGE=sha256:...``
as correct for as long as it existed -- while BuildKit reads a bare ID in
``FROM`` as a Docker Hub repository named ``sha256`` and refuses every
such build with "pull access denied". This test runs the real build on a
locally held image that has no tag the build could fall back on, and
checks that what came out stands on that image's layers.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import json
import subprocess
import uuid

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config.imageOrigins import S_PINNED_BASE_LABEL
from vaibify.docker import imageBuilder

pytestmark = pytest.mark.docker_live

S_STAND_IN = "busybox:1.36"


def _fsRunDocker(listArguments):
    return subprocess.run(
        ["docker", *listArguments], capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=600,
    ).stdout.strip()


def _fdictInspect(sImage):
    return json.loads(_fsRunDocker(["image", "inspect", sImage]))[0]


@pytest.fixture
def tObtainedBase():
    """An image held only by ID, as an obtained archive is; removed after."""
    fnRequireDaemonReachable()
    sProjectName = f"vaibify-stack-probe-{uuid.uuid4().hex[:12]}"
    _fsRunDocker(["pull", "--quiet", S_STAND_IN])
    sBaseImageId = _fdictInspect(S_STAND_IN)["Id"]
    yield sProjectName, sBaseImageId
    for sReference in imageBuilder.flistProjectImageReferences(sProjectName):
        imageBuilder.fbRemoveImage(sReference)


def testAnAgentStacksOnAnImageNamedOnlyByItsId(tmp_path, tObtainedBase):
    """The stage builds, stands on the base's layers, and records the ID."""
    sProjectName, sBaseImageId = tObtainedBase
    (tmp_path / "Dockerfile.claude").write_text(
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
        "RUN echo stacked > /stackedAgentMarker\n",
        encoding="utf-8",
    )
    sLastReference = imageBuilder.fsStackOverlaysOnObtainedBase(
        sProjectName, sBaseImageId, ["claude"], str(tmp_path), "", [],
    )
    dictBase = _fdictInspect(sBaseImageId)
    dictStacked = _fdictInspect(sLastReference)
    listBaseLayers = dictBase["RootFS"]["Layers"]
    listStackedLayers = dictStacked["RootFS"]["Layers"]
    assert listStackedLayers[:len(listBaseLayers)] == listBaseLayers
    assert len(listStackedLayers) == len(listBaseLayers) + 1
    assert dictStacked["Config"]["Labels"][S_PINNED_BASE_LABEL] == (
        sBaseImageId
    )


S_INTEL_BASE_DOCKERFILE = (
    "FROM debian:bookworm-slim\n"
    "RUN useradd -m -s /bin/bash researcher\n"
    "ENV CONTAINER_USER=researcher\n"
    "USER researcher\n"
)


@pytest.mark.agent_cli_live
def testClaudeRunsInAnIntelImageOnAnArmDaemon(tmp_path, tObtainedBase):
    """Claude's ARM build runs inside the Intel image; the image stays Intel.

    Claude Code's Intel binary crashes or spins under qemu, so on an
    Apple Silicon daemon a reader's Claude never installed. This stacks
    the real overlay through the shipped build context, runs the
    installed ``claude`` as the container user, and runs the same
    separation check the acquisition runs before tagging.
    """
    from vaibify.cli.configLoader import fsDockerDir
    from vaibify.reproducibility.agentLayerSeparation import (
        fdictCheckAgentLayerSeparation,
    )
    if _fsRunDocker(["version", "--format", "{{.Server.Arch}}"]) != "arm64":
        pytest.skip("needs an ARM daemon, where an Intel image is emulated")
    sProjectName, _ = tObtainedBase
    (tmp_path / "Dockerfile").write_text(S_INTEL_BASE_DOCKERFILE, encoding="utf-8")
    sBaseTag = f"{sProjectName}:intel-base"
    _fsRunDocker([
        "build", "--platform", "linux/amd64", "-t", sBaseTag, str(tmp_path),
    ])
    sBaseImageId = _fdictInspect(sBaseTag)["Id"]
    sLastReference = imageBuilder.fsStackOverlaysOnObtainedBase(
        sProjectName, sBaseImageId, ["claude"], fsDockerDir(), "linux/amd64",
        [], sDaemonArchitecture="arm64",
    )
    sOutput = _fsRunDocker([
        "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "bash",
        sLastReference, "-lc", "claude --version; uname -m",
    ])
    assert "(Claude Code)" in sOutput
    assert sOutput.splitlines()[-1] == "x86_64"
    dictChecked = fdictCheckAgentLayerSeparation(
        sBaseImageId, _fdictInspect(sLastReference)["Id"],
    )
    assert dictChecked["listViolations"] == []
