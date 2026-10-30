"""A failed archive read names a deleted managed secret, and only that.

On Colima 0.10.3 (virtiofs) with Docker Engine 28.4.0, once a
``--mount`` bind source is deleted, every ``get_archive`` out of that
container fails with the SDK's ``NotFound`` while ``exec`` keeps working
(measured 2026-10-30). The message used to blame the path being read.
It now names the missing file, but only on positive evidence: the SDK
said ``NotFound``, the failing connection talks to this host's own
socket, the mount is a vaibify-managed secret, and ``os.lstat`` of its
source raised ``FileNotFoundError``. Anything short of that makes no
claim. No host bind-source path is ever quoted.

Every file here is real, created and then unlinked under ``tmp_path``,
with ``HOME`` pointed there so the ephemeral root is too, and every
mount's source differs from its destination.
"""

import os
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from docker.errors import APIError, NotFound

from vaibify.config import ephemeralStore
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.dockerConnection import DockerConnection
from vaibify.gui import agentCouncilContext


S_CONTAINER_ID = "c0ffee0000archive"
S_READ_PATH = "/workspace/archiveRepo"
S_SECRET_TARGET = "/run/secrets/archiveProbe"
S_USER_TARGET = "/home/researcher/userData"
S_VOLUME_TARGET = "/mnt/projectVolume"
S_TCP_DOCKER_HOST = "tcp://build-host.internal:2376"
S_UNIX_DOCKER_HOST = "unix:///var/run/docker.sock"


def _fsWriteThenUnlink(sPath, bKeep=False):
    """Create a real file at ``sPath``; unlink it unless asked to keep it."""
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileWritten:
        fileWritten.write("value")
    if not bKeep:
        os.unlink(sPath)
    return sPath


def _fsManagedSecretSource(bKeep=False):
    """Return a managed secret's host path inside the ephemeral root."""
    return _fsWriteThenUnlink(os.path.join(
        ephemeralStore.fsGetEphemeralRoot(),
        f"{ephemeralStore.S_SECRET_FILE_PREFIX}archiveProbe_x1y2.tmp",
    ), bKeep=bKeep)


def _fdictBind(sSource, sDestination):
    return {"Type": "bind", "Source": sSource, "Destination": sDestination}


def _fdictVolume():
    return {"Type": "volume", "Name": "workspaceVolume",
            "Source": "/var/lib/docker/volumes/workspaceVolume/_data",
            "Destination": S_VOLUME_TARGET}


def _fmockContainer(listMounts, errorArchive):
    mockContainer = MagicMock()
    mockContainer.attrs = {"Mounts": listMounts}
    mockContainer.get_archive.side_effect = errorArchive
    return mockContainer


@pytest.fixture
def fnBuildConnection(monkeypatch, tmp_path):
    """Return a builder of a connection over a patched SDK, HOME in tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        dockerConnectionModule, "_fnEnsureDockerHost", lambda: None)

    def fconnectionBuild(mockContainer, sDockerHost=""):
        if sDockerHost:
            monkeypatch.setenv("DOCKER_HOST", sDockerHost)
        else:
            monkeypatch.delenv("DOCKER_HOST", raising=False)
        mockDocker = MagicMock()
        mockDocker.from_env.return_value.containers.get.return_value = (
            mockContainer)
        monkeypatch.setattr(
            dockerConnectionModule, "_fmoduleGetDocker", lambda: mockDocker)
        return DockerConnection()

    return fconnectionBuild


def _fsArchiveFailureMessage(connection):
    """Return the FileNotFoundError text the directory archive read raises."""
    with pytest.raises(FileNotFoundError) as excinfo:
        connection.fbaFetchDirectoryArchive(S_CONTAINER_ID, S_READ_PATH, 1024)
    return str(excinfo.value)


def _fsTodaysMessage(error):
    """The message this read raised before it could explain anything."""
    return f"Cannot read directory from container: {S_READ_PATH}: {error}"


@pytest.mark.falsification
def testAMissingManagedSecretIsNamedAsTheLikelyCause(
    fnBuildConnection, tmp_path,
):
    """Kills: the lstat state always answering *present*.

    Only the deleted managed secret is named: the user's bind still
    exists, and a volume is never a host file this host can check.
    """
    sSecret = _fsManagedSecretSource()
    sUserData = _fsWriteThenUnlink(
        str(tmp_path / "userData" / "values.csv"), bKeep=True)
    connection = fnBuildConnection(_fmockContainer([
        _fdictBind(sSecret, S_SECRET_TARGET),
        _fdictBind(sUserData, S_USER_TARGET), _fdictVolume(),
    ], NotFound("Could not find the file /workspace/archiveRepo")))
    sMessage = _fsArchiveFailureMessage(connection)
    assert f"is missing ({S_SECRET_TARGET})," in sMessage
    assert "likely" in sMessage and "Restart Container" in sMessage
    assert S_USER_TARGET not in sMessage and S_VOLUME_TARGET not in sMessage


@pytest.mark.falsification
def testAMissingSourceVaibifyDidNotWriteMakesNoClaim(
    fnBuildConnection, tmp_path,
):
    """Kills: ``fbIsManagedSecretSource`` always answering true.

    A researcher's own bind can name a path the daemon sees and this
    host does not, so its absence here proves nothing about the daemon.
    """
    sUserData = _fsWriteThenUnlink(str(tmp_path / "userData" / "values.csv"))
    errorArchive = NotFound("Could not find the file /workspace/archiveRepo")
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sUserData, S_USER_TARGET)], errorArchive))
    assert _fsArchiveFailureMessage(connection) == _fsTodaysMessage(
        errorArchive)


@pytest.mark.falsification
def testAnInaccessibleSourceIsNotReportedAsMissing(fnBuildConnection):
    """Kills: treating every ``OSError`` from lstat as *missing*.

    A permission failure says this host could not look, which is not
    evidence that the file is gone.
    """
    sSecret = _fsManagedSecretSource(bKeep=True)
    errorArchive = NotFound("Could not find the file /workspace/archiveRepo")
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sSecret, S_SECRET_TARGET)], errorArchive))
    fnRealLstat = os.lstat

    def fnLstatRefusingTheSecret(sPath, *args, **kwargs):
        if sPath == sSecret:
            raise PermissionError(13, "Permission denied", sPath)
        return fnRealLstat(sPath, *args, **kwargs)

    with patch.object(os, "lstat", fnLstatRefusingTheSecret):
        sMessage = _fsArchiveFailureMessage(connection)
    assert sMessage == _fsTodaysMessage(errorArchive)


@pytest.mark.falsification
def testADaemonOffTheLocalSocketIsNeverDiagnosed(fnBuildConnection):
    """Kills: dropping the local-socket condition from the diagnosis.

    A remote daemon's mount sources are paths on ITS host, so this
    host's lstat says nothing about them.
    """
    sSecret = _fsManagedSecretSource()
    errorArchive = NotFound("Could not find the file /workspace/archiveRepo")
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sSecret, S_SECRET_TARGET)], errorArchive),
        sDockerHost=S_TCP_DOCKER_HOST)
    assert _fsArchiveFailureMessage(connection) == _fsTodaysMessage(
        errorArchive)


@pytest.mark.falsification
def testTheEndpointIsTheFailingConnectionsOwn(
    fnBuildConnection, monkeypatch,
):
    """Kills: the check re-reading ``DOCKER_HOST`` at failure time.

    The connection was built for a remote daemon; a later change to the
    environment must not relabel it as local.
    """
    sSecret = _fsManagedSecretSource()
    errorArchive = NotFound("Could not find the file /workspace/archiveRepo")
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sSecret, S_SECRET_TARGET)], errorArchive),
        sDockerHost=S_TCP_DOCKER_HOST)
    monkeypatch.setenv("DOCKER_HOST", S_UNIX_DOCKER_HOST)
    assert _fsArchiveFailureMessage(connection) == _fsTodaysMessage(
        errorArchive)


@pytest.mark.falsification
def testAFailedDiagnosisKeepsTheOriginalError(fnBuildConnection):
    """Kills: removing the guard around the diagnosis.

    The expected text is the one this read raised before the diagnosis
    existed, copied from it rather than from the code under test.
    """
    errorArchive = NotFound("Could not find the file /workspace/archiveRepo")
    mockContainer = MagicMock()
    mockContainer.get_archive.side_effect = errorArchive
    type(mockContainer).attrs = PropertyMock(
        side_effect=RuntimeError("the container handle could not be read"))
    connection = fnBuildConnection(mockContainer)
    with pytest.raises(FileNotFoundError) as excinfo:
        connection.fbaFetchDirectoryArchive(S_CONTAINER_ID, S_READ_PATH, 1024)
    assert type(excinfo.value) is FileNotFoundError
    assert str(excinfo.value) == (
        "Cannot read directory from container: /workspace/archiveRepo: "
        "Could not find the file /workspace/archiveRepo")


def _flistErrorsQuoting(sHostPath):
    """Return a NotFound and an APIError whose text quotes a host path."""
    return [
        NotFound("Could not find the file /workspace/archiveRepo",
                 explanation=f"bind source {sHostPath} is gone"),
        APIError(f"500 Server Error: mount {sHostPath} failed"),
    ]


@pytest.mark.falsification
@pytest.mark.parametrize("bSecretMissing", [True, False])
def testNoHostBindSourceIsQuoted(fnBuildConnection, bSecretMissing):
    """Kills: removing the redaction of host sources from Docker's words.

    With and without a missing secret, for the SDK's NotFound and for
    any other APIError, the host source becomes ``<host path>``.
    """
    sSecret = _fsManagedSecretSource(bKeep=not bSecretMissing)
    for errorArchive in _flistErrorsQuoting(sSecret):
        connection = fnBuildConnection(_fmockContainer(
            [_fdictBind(sSecret, S_SECRET_TARGET)], errorArchive))
        sMessage = _fsArchiveFailureMessage(connection)
        assert sSecret not in sMessage, sMessage
        assert "<host path>" in sMessage, sMessage


@pytest.mark.falsification
def testRedactionPrecedesTheBound(fnBuildConnection):
    """Kills: redacting the quoted Docker text after bounding it.

    The host path straddles the 240-character bound on the daemon's
    words, so a bound taken first leaves its opening characters, which
    no replacement of the whole path can match.
    """
    sSecret = _fsManagedSecretSource()
    sPadding = "x" * 230 + " "
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sSecret, S_SECRET_TARGET)],
        NotFound(sPadding + sSecret + " is gone")))
    sMessage = _fsArchiveFailureMessage(connection)
    sOpeningFragment = sSecret[:240 - len(sPadding)]
    assert f"({S_SECRET_TARGET})" in sMessage
    assert sOpeningFragment not in sMessage, sMessage


def _fnReadThroughBothCouncilSites(connection):
    """Drive the council's whole-directory read, then its per-path read."""
    container = connection.fcontainerGetById(S_CONTAINER_ID)
    yield lambda: agentCouncilContext._fdictStreamValidatedArchive(
        connection, S_CONTAINER_ID, S_READ_PATH, "/nonexistent", {}, set())
    yield lambda: agentCouncilContext._fnFetchOneTrackedMember(
        connection, S_CONTAINER_ID, container, S_READ_PATH,
        "archiveRepo", "analysis/fit.py", None, None)


@pytest.mark.falsification
def testTheCouncilRefusesInsteadOfRaisingTheSdkError(fnBuildConnection):
    """Kills: removing the council's conversion to a refusal.

    Inside the council's carrier worker an SDK exception poisons the
    journal record; a refusal is carried back as a 409. Only a missing
    managed secret converts: any other failure is the SDK's own error,
    re-raised as the same object.
    """
    sSecret = _fsManagedSecretSource()
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sSecret, S_SECRET_TARGET)],
        NotFound("Could not find the file /workspace/archiveRepo")))
    for fnRead in _fnReadThroughBothCouncilSites(connection):
        with pytest.raises(agentCouncilContext.SnapshotRefusedError) as excinfo:
            fnRead()
        assert f"({S_SECRET_TARGET})" in str(excinfo.value)
        assert sSecret not in str(excinfo.value)
    errorOther = APIError("500 Server Error: daemon busy")
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(sSecret, S_SECRET_TARGET)], errorOther))
    for fnRead in _fnReadThroughBothCouncilSites(connection):
        with pytest.raises(APIError) as excinfo:
            fnRead()
        assert excinfo.value is errorOther


def testAnUnrelatedFailureKeepsTheDaemonsWords(fnBuildConnection):
    """An APIError quoting no host path reads exactly as it always did."""
    errorArchive = APIError("500 Server Error: daemon busy")
    connection = fnBuildConnection(_fmockContainer(
        [_fdictBind(_fsManagedSecretSource(), S_SECRET_TARGET)],
        errorArchive))
    assert _fsArchiveFailureMessage(connection) == _fsTodaysMessage(
        errorArchive)
