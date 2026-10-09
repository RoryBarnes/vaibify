"""A Python built without ``dir_fd`` says so, with the remedy, and reads nothing.

The host's confined reads open each path component against the descriptor
above it, which needs ``dir_fd`` on ``open``, ``stat`` and ``readlink``.
Some builds are made without it (the python.org macOS 3.9 installer is one:
``os.supports_dir_fd`` is empty). On such a Python the read failed deep
inside with a bare ``NotImplementedError``, and the download route answered
"could not be read: dir_fd unavailable on this platform", which names no
remedy.

The refusal has to stay closed. A read that fell back to following links
would remove the very protection the descriptors give, so the only
acceptable behavior is to decline before anything is opened and to say what
to do. ``os.supports_dir_fd`` is patched here, so these tests mean the same
on every interpreter.
"""

import os

import pytest
from fastapi import HTTPException

from vaibify.gui.routes import downloadRoutes
from vaibify.host import hostConfinedRead
from vaibify.host.hostConfinedRead import (
    HostReadUnsupportedError,
    fiterStreamDirectoryAsTar,
    fiterStreamFileInsideRoot,
    fnRequireDirectoryRelativeAccess,
)


@pytest.fixture
def pathProject(tmp_path):
    pathRoot = os.path.realpath(str(tmp_path))
    with open(os.path.join(pathRoot, "data.txt"), "w") as fileOut:
        fileOut.write("payload")
    os.mkdir(os.path.join(pathRoot, "folder"))
    return pathRoot


@pytest.fixture
def fixtureNoDirectoryRelativeAccess(monkeypatch):
    """A Python whose ``os.supports_dir_fd`` is empty, and that opens nothing."""
    monkeypatch.setattr(os, "supports_dir_fd", set())

    def fnOpenNothing(*listArguments, **dictKeywords):
        raise AssertionError("a path was opened on a Python without dir_fd")

    monkeypatch.setattr(os, "open", fnOpenNothing)


@pytest.mark.falsification
def test_a_file_read_declines_before_opening_anything_and_names_the_remedy(
    pathProject, fixtureNoDirectoryRelativeAccess,
):
    """Kills: dropping the up-front check from the file read.

    Without it the read goes on to open a path, which this test forbids,
    or fails with a bare NotImplementedError on a real build.
    """
    with pytest.raises(HostReadUnsupportedError) as infoError:
        next(fiterStreamFileInsideRoot(
            pathProject, os.path.join(pathProject, "data.txt")))
    sMessage = str(infoError.value)
    assert "directory-relative" in sMessage
    assert "Run vaibify with a Python that has it" in sMessage


@pytest.mark.falsification
def test_a_folder_read_declines_before_opening_anything(
    pathProject, fixtureNoDirectoryRelativeAccess,
):
    """Kills: dropping the up-front check from the folder archive."""
    with pytest.raises(HostReadUnsupportedError):
        next(fiterStreamDirectoryAsTar(
            pathProject, os.path.join(pathProject, "folder")))


@pytest.mark.parametrize("fnMissing", (os.open, os.stat, os.readlink))
def test_support_for_any_one_of_the_three_calls_missing_is_enough_to_decline(
    monkeypatch, fnMissing,
):
    setSupported = {os.open, os.stat, os.readlink} - {fnMissing}
    monkeypatch.setattr(os, "supports_dir_fd", setSupported)
    with pytest.raises(HostReadUnsupportedError):
        fnRequireDirectoryRelativeAccess()


def test_a_python_with_all_three_proceeds(monkeypatch):
    monkeypatch.setattr(
        os, "supports_dir_fd", {os.open, os.stat, os.readlink})
    assert fnRequireDirectoryRelativeAccess() is None


def test_the_refusal_is_its_own_kind_not_a_refused_path():
    """It is not a 403: nothing the researcher asked for was forbidden."""
    assert not issubclass(
        HostReadUnsupportedError, hostConfinedRead.ContainerReadRefusedError)


@pytest.mark.falsification
def test_the_download_route_answers_501_with_the_sentence():
    """Kills: letting the route fold this into a generic 500.

    A 500 reads as a fault in vaibify; the read is impossible on this
    Python, and the researcher's way out is in the sentence.
    """
    with pytest.raises(HTTPException) as infoHttp:
        downloadRoutes._fnRaiseHttpForFailedRead(
            HostReadUnsupportedError("Run vaibify with a Python that has it"),
            "/project/data.txt")
    assert infoHttp.value.status_code == 501
    assert infoHttp.value.detail == "Run vaibify with a Python that has it"
