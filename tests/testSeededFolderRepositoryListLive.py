"""The start's repository list, read from and mounted into a real image.

The unit tests answer the three docker commands with a stand-in; these
ask a real daemon. They pin what a stand-in cannot: that ``docker cp``
of a never-started container yields the file as one regular member,
that a symlink in the image arrives as a LINK (so refusing link members
is what keeps the read off the host), that the reader container is gone
afterwards, and that a read-only file mount really replaces the file
the image baked in.
"""

import os
import shutil
import subprocess
import tempfile

import pytest

from tests.liveContainerLabels import flistLabelArguments
from tests.testContainerManager import _fConfigMinimal
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import containerConfig, registryManager
from vaibify.docker import containerManager

pytestmark = pytest.mark.docker_live

S_LIST_IMAGE = "vaibifyrepositorylist:file"
S_LINK_IMAGE = "vaibifyrepositorylist:link"
S_BARE_IMAGE = "vaibifyrepositorylist:bare"
S_PROJECT_LINE = "aigreen|https://example.invalid/aigreen.git|main|reference"
S_DEPENDENCY_LINE = "solver|https://example.invalid/solver.git|main|pip_editable"
S_LIST_DOCKERFILE = (
    "FROM alpine:3.20\n"
    "RUN mkdir -p /etc/vaibify && printf '%s\\n%s\\n' "
    f"'{S_DEPENDENCY_LINE}' '{S_PROJECT_LINE}' > /etc/vaibify/container.conf\n"
)
S_BARE_DOCKERFILE = "FROM alpine:3.20\nRUN mkdir -p /etc/vaibify\n"
S_LINK_DOCKERFILE = (
    "FROM alpine:3.20\n"
    "RUN mkdir -p /etc/vaibify && ln -s /etc/passwd /etc/vaibify/container.conf\n"
)


def _fnBuildImage(sTag, sDockerfile):
    processBuild = subprocess.run(
        ["docker", "build", "-t", sTag, "-"],
        input=sDockerfile, capture_output=True, text=True,
    )
    if processBuild.returncode != 0:
        pytest.skip(f"could not build {sTag}: {processBuild.stderr}")


def _fsImageId(sTag):
    return subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", sTag],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _flistReaderContainers():
    return subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter",
         f"label={containerManager.S_REPOSITORY_LIST_READER_LABEL}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()


@pytest.fixture(scope="module", autouse=True)
def fnBuildListImages():
    fnRequireDaemonReachable()
    _fnBuildImage(S_LIST_IMAGE, S_LIST_DOCKERFILE)
    _fnBuildImage(S_LINK_IMAGE, S_LINK_DOCKERFILE)
    _fnBuildImage(S_BARE_IMAGE, S_BARE_DOCKERFILE)
    yield


@pytest.fixture
def fixtureListsUnderHome(monkeypatch):
    """Lists the daemon can mount: Colima shares the home, not the temp tree."""
    sDirectory = tempfile.mkdtemp(
        prefix=".vaibifyLiveRepositoryLists", dir=os.path.expanduser("~"))
    monkeypatch.setattr(
        containerConfig, "_S_REPOSITORY_LISTS_DIRECTORY", sDirectory)
    yield sDirectory
    shutil.rmtree(sDirectory, ignore_errors=True)


def test_the_list_is_read_whole_and_the_reader_is_gone():
    sList = containerManager._fsReadRepositoryListOfImage(
        "liveProbe", _fsImageId(S_LIST_IMAGE))
    assert sList == f"{S_DEPENDENCY_LINE}\n{S_PROJECT_LINE}\n"
    assert _flistReaderContainers() == []


def test_an_image_without_a_list_is_told_from_a_failed_copy():
    """The daemon's own answer for an absent file, not any failure, means "no list"."""
    assert containerManager._fsReadRepositoryListOfImage(
        "liveProbe", _fsImageId(S_BARE_IMAGE)) == ""
    assert _flistReaderContainers() == []
    assert containerManager._ftCopyRepositoryListOutOf("vaibifyNoSuchReader") == (
        None, False)


def test_a_list_the_image_links_elsewhere_arrives_as_a_link_and_is_refused():
    with pytest.raises(RuntimeError) as errorRefused:
        containerManager._fsReadRepositoryListOfImage(
            "liveProbe", _fsImageId(S_LINK_IMAGE))
    assert "not a single regular file" in str(errorRefused.value)
    assert _flistReaderContainers() == []


def test_the_started_container_sees_the_list_without_the_project(
    fixtureListsUnderHome, tmp_path, monkeypatch,
):
    os.makedirs(tmp_path / "aigreen")
    monkeypatch.setattr(registryManager, "fdictGetProject", lambda sName: {
        "sName": sName, "sMode": "container",
        "sDirectory": str(tmp_path / "aigreen"),
        registryManager.S_WORKSPACE_SEEDED_KEY: True,
    })
    saRunArgs = []
    containerManager._fnMountRepositoryListWithoutSeededFolder(
        _fConfigMinimal(), saRunArgs, {
            "sMode": "as-built", "bWithCredentials": False,
            "sImageDigest": _fsImageId(S_LIST_IMAGE),
        })
    processRun = subprocess.run(
        ["docker", "run", "--rm"] + flistLabelArguments() + saRunArgs
        + [S_LIST_IMAGE, "cat", containerConfig.S_IMAGE_REPOSITORY_LIST_PATH],
        capture_output=True, text=True,
    )
    assert processRun.returncode == 0, processRun.stderr
    assert processRun.stdout == f"{S_DEPENDENCY_LINE}\n"
