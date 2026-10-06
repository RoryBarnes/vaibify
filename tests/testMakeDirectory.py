"""Creating directories through the confined receiver, on both legs.

Source: ``DockerConnection.fnMakeDirectory`` and
``HostConnection.fnMakeDirectory``.

A folder dropped onto the Files panel is recreated, empty directories
included, so something has to make a directory without writing a file. The
container leg hands the tree receiver an EMPTY archive: the receiver
creates the destination chain with ``O_NOFOLLOW`` against held descriptors
and lands nothing. These tests run that real program.
"""

import os

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from tests.testConfinedContainerWrite import _fconnectionOverDaemon
from tests.testHostConnection import (  # noqa: F401  (fixtures)
    S_PROJECT_NAME,
    fixtureIsolateJournalAndScratch,
    tProjectAndConnection,
)
from vaibify.docker.confinedWrite import (
    ContainerWriteRefusedError,
    T_WRITE_DENYLISTED_NAMES,
)


def _fsRealPath(pathTmp):
    return os.path.realpath(str(pathTmp))


@pytest.mark.falsification
def testTheContainerLegCreatesNestedDirectoriesBelowTheRoot(tmp_path):
    """Kills: passing ``bCreateDestination=False``, which refuses a missing
    directory instead of creating it.
    """
    sRoot = _fsRealPath(tmp_path) + "/project"
    os.makedirs(sRoot)
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-mkdir")
    connection.fnMakeDirectory(
        "cid-mkdir", sRoot + "/a/b/c", sAuthorizedRoot=sRoot,
        tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
    assert os.path.isdir(sRoot + "/a/b/c")
    connection.fnMakeDirectory(
        "cid-mkdir", sRoot + "/a/b/c", sAuthorizedRoot=sRoot)
    assert os.listdir(sRoot + "/a/b/c") == []


@pytest.mark.falsification
def testTheContainerLegRefusesToCreateThroughASymlinkOrAForbiddenName(
    tmp_path,
):
    """Kills: dropping the receiver's ``O_NOFOLLOW`` walk or its denylist."""
    sRoot = _fsRealPath(tmp_path) + "/project"
    sOutside = _fsRealPath(tmp_path) + "/outside"
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    os.symlink(sOutside, sRoot + "/linked")
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-mkdir2")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnMakeDirectory(
            "cid-mkdir2", sRoot + "/linked/planted", sAuthorizedRoot=sRoot,
            tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
    assert os.listdir(sOutside) == []
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnMakeDirectory(
            "cid-mkdir2", sRoot + "/.git/hooks", sAuthorizedRoot=sRoot,
            tForbiddenNames=T_WRITE_DENYLISTED_NAMES)
    assert not os.path.exists(sRoot + "/.git")


def testTheContainerLegWillNotCreateAboveTheAuthorizedRoot(tmp_path):
    sRoot = _fsRealPath(tmp_path) + "/project"
    os.makedirs(sRoot)
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-mkdir3")
    with pytest.raises((ContainerWriteRefusedError, ValueError)):
        connection.fnMakeDirectory(
            "cid-mkdir3", _fsRealPath(tmp_path) + "/elsewhere",
            sAuthorizedRoot=sRoot)
    assert not os.path.exists(_fsRealPath(tmp_path) + "/elsewhere")


def testTheHostLegCreatesNestedDirectoriesAndIsIdempotent(
    tProjectAndConnection,
):
    sProjectRoot, connection = tProjectAndConnection
    sTarget = os.path.join(sProjectRoot, "x", "y")
    connection.fnMakeDirectory(S_PROJECT_NAME, sTarget)
    connection.fnMakeDirectory(S_PROJECT_NAME, sTarget)
    assert os.path.isdir(sTarget)
