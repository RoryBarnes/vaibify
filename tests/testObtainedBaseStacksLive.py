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
