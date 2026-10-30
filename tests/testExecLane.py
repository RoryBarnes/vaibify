"""The hub's own programs never resolve through the agent's PATH.

Source: ``vaibify/docker/execLane.py`` and the exec primitives in
``vaibify/docker/dockerConnection.py``.

The image PATH leads with the container user's ``~/.local/bin``, and
the containment probes run ``/bin/sh`` scripts as ROOT through it: a
``cat`` planted there ran as root at the next terminal open. Every
hub-internal exec now carries the fixed system PATH the entrypoint's
root phase already pins, and every hub-run Python program runs as
``python3 -I``. The oracle for ``-I`` is the interpreter's own
documentation: it implies ``-E`` (ignore ``PYTHON*``), ``-s`` (no user
site) and ``-P`` (no script directory or working directory on
``sys.path``), which are exactly the three channels an unprivileged
agent can write.
"""

import os
import re
from unittest.mock import MagicMock, patch

import pytest

from vaibify.docker import execLane
from vaibify.docker.dockerConnection import (
    DockerConnection, _CACHED_CONTAINER_USER, _CACHED_IMAGE_PATH,
    S_TYPED_READ_FILE_BASE64,
)
from vaibify.docker.execLane import (
    LIST_FIXED_SYSTEM_PATH, S_CONDA_BIN_DIRECTORY,
    S_EXEC_LANE_HUB_INTERNAL, S_EXEC_LANE_RESEARCHER_COMMAND,
    S_HUB_PYTHON_COMMAND, fdictResolveExecEnvironment,
    fsBuildHubInternalPath, fsImagePathFromEnvironmentList,
)

S_FIXED_PATH = ":".join(LIST_FIXED_SYSTEM_PATH)
S_IMAGE_PATH = "/home/researcher/.local/bin:/usr/local/bin:/usr/bin:/bin"
S_CONDA_IMAGE_PATH = (
    "/home/researcher/.local/bin:/opt/conda/bin:/usr/local/bin:/usr/bin"
)
_S_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "entrypoint.sh",
))


# ---------------------------------------------------------------------------
# The pure builder
# ---------------------------------------------------------------------------


def testTheFixedPathIsTheOneTheEntrypointPins():
    """Two spellings of one list: the entrypoint's and this module's."""
    with open(_S_ENTRYPOINT, encoding="utf-8") as fileEntrypoint:
        sSource = fileEntrypoint.read()
    matchBody = re.search(
        r"^fnUseFixedSystemPath\(\) \{\n(.*?)^\}\n", sSource,
        re.DOTALL | re.MULTILINE,
    )
    assert matchBody, "fnUseFixedSystemPath is not defined in entrypoint.sh"
    matchPath = re.search(r'export PATH="([^"]+)"', matchBody.group(1))
    assert matchPath
    assert matchPath.group(1) == S_FIXED_PATH


def testNoFixedPathDirectoryIsBelowAHome():
    for sDirectory in LIST_FIXED_SYSTEM_PATH:
        assert sDirectory.startswith(("/usr/", "/sbin", "/bin")), sDirectory


def testTheImagePathIsReadFromTheEnvironmentList():
    assert fsImagePathFromEnvironmentList(
        ["HOME=/home/researcher", "PATH=" + S_IMAGE_PATH, "LANG=C"],
    ) == S_IMAGE_PATH
    assert fsImagePathFromEnvironmentList(["HOME=/root"]) == ""
    assert fsImagePathFromEnvironmentList(None) == ""
    assert fsImagePathFromEnvironmentList(MagicMock()) == ""


def testAPipImageGetsTheFixedPathAlone():
    assert fsBuildHubInternalPath(S_IMAGE_PATH) == S_FIXED_PATH
    assert fsBuildHubInternalPath("") == S_FIXED_PATH


def testACondaImagePrependsItsRootOwnedBinDirectory():
    sPath = fsBuildHubInternalPath(S_CONDA_IMAGE_PATH)
    assert sPath == S_CONDA_BIN_DIRECTORY + ":" + S_FIXED_PATH
    assert ".local" not in sPath


def testACondaLookalikeDirectoryIsNotEnough():
    """Only the exact entry counts, never a prefix an agent could plant."""
    assert fsBuildHubInternalPath(
        "/home/researcher/.local/bin/opt/conda/bin:/usr/bin",
    ) == S_FIXED_PATH


def testTheHubLaneLaysTheFixedPathOverACallersVariables():
    dictEnvironment = fdictResolveExecEnvironment(
        S_EXEC_LANE_HUB_INTERNAL, S_IMAGE_PATH,
        {"SWITCH": "1", "PATH": "/home/researcher/.local/bin"},
    )
    assert dictEnvironment == {"SWITCH": "1", "PATH": S_FIXED_PATH}


def testTheResearcherLanePassesACallersVariablesUntouched():
    dictEnvironment = {"SWITCH": "1"}
    assert fdictResolveExecEnvironment(
        S_EXEC_LANE_RESEARCHER_COMMAND, S_IMAGE_PATH, dictEnvironment,
    ) == {"SWITCH": "1"}
    assert fdictResolveExecEnvironment(
        S_EXEC_LANE_RESEARCHER_COMMAND, S_IMAGE_PATH, None,
    ) is None


@pytest.mark.falsification
def testAnUndeclaredLaneIsRefused():
    """A lane outside the closed set is refused before anything runs.

    Kills: ``fnValidateExecLane`` accepting any lane name.
    """
    with pytest.raises(ValueError, match="not an exec lane"):
        fdictResolveExecEnvironment("root", S_IMAGE_PATH)
    with pytest.raises(ValueError):
        execLane.fnValidateExecLane(None)


def testTheHubPythonIsIsolated():
    """``-I`` is the one flag that closes user site, PYTHON* and cwd."""
    assert execLane.LIST_HUB_PYTHON_ARGV == ["python3", "-I"]
    assert S_HUB_PYTHON_COMMAND == "python3 -I"


# ---------------------------------------------------------------------------
# The connection applies it to every exec
# ---------------------------------------------------------------------------


def _fMockDockerModule():
    mockDocker = MagicMock()
    mockClient = MagicMock()
    mockDocker.from_env.return_value = mockClient
    return mockDocker, mockClient


def _fMockContainer(sImagePath=S_IMAGE_PATH, sId="abc123"):
    mockContainer = MagicMock()
    mockContainer.id = sId
    mockContainer.name = "test-container"
    mockContainer.attrs = {"Config": {"Env": ["PATH=" + sImagePath]}}
    mockContainer.image.attrs = {"Config": {"User": "scientist"}}
    mockContainer.exec_run.return_value = (0, (b"ok", b""))
    return mockContainer


@pytest.fixture
def tConnection():
    """Yield (connection, container, client) over a mocked daemon."""
    _CACHED_CONTAINER_USER.clear()
    _CACHED_IMAGE_PATH.clear()
    mockDocker, mockClient = _fMockDockerModule()
    mockContainer = _fMockContainer()
    mockClient.containers.get.return_value = mockContainer
    mockClient.api.exec_create.return_value = {"Id": "exec_id"}
    with patch(
        "vaibify.docker.dockerConnection._fmoduleGetDocker",
        return_value=mockDocker,
    ):
        yield DockerConnection(), mockContainer, mockClient
    _CACHED_IMAGE_PATH.clear()


def testAHubInternalExecCarriesTheFixedPath(tConnection):
    connection, mockContainer, _ = tConnection
    connection.ftRunInContainerStreamed("abc123", "git status")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["environment"] == {"PATH": S_FIXED_PATH}
    assert dictKwargs["user"] == "scientist"


def testAResearcherCommandKeepsTheImagePath(tConnection):
    connection, mockContainer, _ = tConnection
    connection.ftRunInContainerStreamed(
        "abc123", "pytest", sLane=S_EXEC_LANE_RESEARCHER_COMMAND,
    )
    assert "environment" not in mockContainer.exec_run.call_args[1]


def testTheCompatWrapperForwardsTheLane(tConnection):
    connection, mockContainer, _ = tConnection
    connection.ftResultExecuteCommand("abc123", "ls")
    assert mockContainer.exec_run.call_args[1]["environment"] == {
        "PATH": S_FIXED_PATH,
    }
    connection.ftResultExecuteCommand(
        "abc123", "ls", sLane=S_EXEC_LANE_RESEARCHER_COMMAND,
    )
    assert "environment" not in mockContainer.exec_run.call_args[1]


def testACondaContainerPutsCondaFirst(tConnection):
    connection, mockContainer, mockClient = tConnection
    mockClient.containers.get.return_value = _fMockContainer(
        S_CONDA_IMAGE_PATH, sId="conda1",
    )
    connection.ftRunInContainerStreamed("conda1", "git status")
    dictKwargs = mockClient.containers.get.return_value.exec_run.call_args[1]
    assert dictKwargs["environment"]["PATH"].startswith(
        S_CONDA_BIN_DIRECTORY + ":",
    )


def testTheDurableExecCarriesTheLaneToo(tConnection):
    connection, _, mockClient = tConnection
    mockClient.api.exec_start.return_value = iter([])
    mockClient.api.exec_inspect.return_value = {
        "Running": False, "ExitCode": 0,
    }
    connection.ftRunInContainerStreamedWithChunks("abc123", "ls", None)
    assert mockClient.api.exec_create.call_args[1]["environment"] == {
        "PATH": S_FIXED_PATH,
    }
    connection.ftRunInContainerStreamedWithChunks(
        "abc123", "ls", None, sLane=S_EXEC_LANE_RESEARCHER_COMMAND,
    )
    assert "environment" not in mockClient.api.exec_create.call_args[1]


def testAnInteractiveExecMergesTheFixedPathOverItsOwnVariables(tConnection):
    connection, _, mockClient = tConnection
    connection.fsExecCreate(
        "abc123", dictEnvironment={"SWITCH": "1", "PATH": "/tmp/evil"},
    )
    assert mockClient.api.exec_create.call_args[1]["environment"] == {
        "SWITCH": "1", "PATH": S_FIXED_PATH,
    }
    connection.fsExecCreate(
        "abc123", dictEnvironment={"SWITCH": "1"},
        sLane=S_EXEC_LANE_RESEARCHER_COMMAND,
    )
    assert mockClient.api.exec_create.call_args[1]["environment"] == {
        "SWITCH": "1",
    }


@pytest.mark.falsification
def testTheRootProbeNeverResolvesThroughTheImagePath(tConnection):
    """The ``/bin/sh`` probe that runs as root carries the fixed PATH.

    The probe's script calls ``cat`` and ``rm``; through the image PATH
    those resolve first in ``~/.local/bin``, which the container user
    owns, so a planted program ran as root at every terminal open and
    close. The exec's environment is what stops that, and nothing
    else does.

    Kills: dropping the ``environment`` argument from
    ``ftRunRootShellProbe``'s ``exec_run`` call.
    """
    connection, mockContainer, _ = tConnection
    mockContainer.exec_run.return_value = (0, b"iMembers=0\n")
    connection.ftRunRootShellProbe("abc123", "cat /tmp/marker")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "root"
    assert dictKwargs["environment"] == {"PATH": S_FIXED_PATH}
    assert ".local" not in dictKwargs["environment"]["PATH"]


@pytest.mark.falsification
def testEveryHubRunPythonProgramIsIsolated(tConnection):
    """Typed reads and the confined programs run ``python3 -I``.

    A ``usercustomize.py`` in the user site, a ``.pth`` file there, or a
    ``PYTHONPATH`` the agent exported into its own shell would otherwise
    run inside the program that hashes a file for the dashboard.

    Kills: dropping ``-I`` from ``LIST_HUB_PYTHON_ARGV``.
    """
    connection, mockContainer, mockClient = tConnection
    sEncoded = "aGVsbG8="
    mockContainer.exec_run.return_value = (0, (sEncoded.encode(), b""))
    connection.fbaFetchFile("abc123", "/workspace/data.csv")
    listCommand = mockContainer.exec_run.call_args[1]["cmd"]
    assert listCommand[:2] == ["/bin/bash", "-c"]
    assert listCommand[2].startswith("python3 -I -c ")
    assert S_TYPED_READ_FILE_BASE64  # the operation the read declares
    mockClient.api.exec_create.return_value = {"Id": "exec_id"}
    with patch.object(
        connection, "fsocketExecStart", side_effect=RuntimeError("stop"),
    ):
        with pytest.raises(RuntimeError):
            connection.fnWriteFile("abc123", "/workspace/out.txt", b"x")
    listArgv = mockClient.api.exec_create.call_args[1]["cmd"]
    assert listArgv[:3] == ["python3", "-I", "-c"]


def testTheHelperInterpreterInAContainerIsIsolated():
    from vaibify.gui.helperPrograms import fsResolveHelperInterpreter
    assert fsResolveHelperInterpreter("some-container-id") == "python3 -I"


# ---------------------------------------------------------------------------
# The integrity-read lane: a separate read-only user for what the
# dashboard trusts
# ---------------------------------------------------------------------------

from vaibify.docker.execLane import (  # noqa: E402
    DICT_INTEGRITY_READ_ENVIRONMENT, I_INTEGRITY_READ_UID,
    S_EXEC_LANE_INTEGRITY_READ, fsIntegrityReadUser,
)
from vaibify.docker.dockerConnection import (  # noqa: E402
    T_TYPED_READS_AS_CONTAINER_USER, UnreadableSmallFile,
    _fsResolveTypedReadLane, S_TYPED_READ_KEYRING_SECRET,
)


def testTheReadOnlyUserTakesTheContainerUsersGroup():
    """Ubuntu's home directories are 750, so the group is what gets in."""
    assert fsIntegrityReadUser("researcher") == "65532:researcher"
    assert fsIntegrityReadUser("1000:1000") == "65532:1000"
    assert fsIntegrityReadUser("1000") == "65532:1000"
    assert fsIntegrityReadUser("") == "65532:1000"
    assert I_INTEGRITY_READ_UID == 65532


def testTheIntegrityLaneCarriesTheFixedPathARootHomeAndGitScope():
    dictEnvironment = fdictResolveExecEnvironment(
        S_EXEC_LANE_INTEGRITY_READ, S_IMAGE_PATH,
    )
    assert dictEnvironment["PATH"] == S_FIXED_PATH
    assert dictEnvironment["HOME"] == "/"
    assert dictEnvironment["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert dictEnvironment["GIT_CONFIG_VALUE_0"] == "*"
    assert set(DICT_INTEGRITY_READ_ENVIRONMENT) <= set(dictEnvironment)


def testTheKeyringAndCredentialReadsStayWithTheContainerUser():
    for sOperation in T_TYPED_READS_AS_CONTAINER_USER:
        assert _fsResolveTypedReadLane(sOperation, None) == (
            S_EXEC_LANE_HUB_INTERNAL
        )
    assert _fsResolveTypedReadLane("repoSnapshot", None) == (
        S_EXEC_LANE_INTEGRITY_READ
    )
    assert _fsResolveTypedReadLane(
        "repoSnapshot", S_EXEC_LANE_HUB_INTERNAL,
    ) == S_EXEC_LANE_HUB_INTERNAL


def testATypedReadRefusesTheResearcherLane():
    with pytest.raises(ValueError, match="researcher-command"):
        _fsResolveTypedReadLane("repoSnapshot", S_EXEC_LANE_RESEARCHER_COMMAND)


@pytest.mark.falsification
def testTheHasherRunsAsTheReadOnlyUser(tConnection):
    """A typed read execs as the read-only uid in the container user's group.

    The value the dashboard reports cannot be forged by a process the
    agent controls when the process that reads it is another user's:
    the agent (uid 1000) can neither ptrace it nor substitute the
    interpreter it runs, which commit 1's fixed PATH already pins.

    Kills: defaulting the typed-read lane to hub-internal in
    ``_fsResolveTypedReadLane``.
    """
    connection, mockContainer, _ = tConnection
    mockContainer.exec_run.return_value = (0, (b"aGVsbG8=", b""))
    connection.fbaFetchFile("abc123", "/workspace/data.csv")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "65532:scientist"
    assert dictKwargs["environment"]["HOME"] == "/"
    assert dictKwargs["environment"]["PATH"] == S_FIXED_PATH


def testTheKeyringReadRunsAsTheContainerUser(tConnection):
    connection, mockContainer, _ = tConnection
    mockContainer.exec_run.return_value = (0, (b"", b""))
    connection._ftRunTypedRead("abc123", S_TYPED_READ_KEYRING_SECRET, "slot")
    dictKwargs = mockContainer.exec_run.call_args[1]
    assert dictKwargs["user"] == "scientist"
    assert "HOME" not in dictKwargs["environment"]


def testACallerMayReadTheAgentsOwnFilesAsTheContainerUser(tConnection):
    connection, mockContainer, _ = tConnection
    mockContainer.exec_run.return_value = (0, (b"aGVsbG8=", b""))
    connection.fbaFetchFile(
        "abc123", "/home/scientist/.claude/projects/x.jsonl",
        sLane=S_EXEC_LANE_HUB_INTERNAL,
    )
    assert mockContainer.exec_run.call_args[1]["user"] == "scientist"


def testTheBatchedReadTellsAbsentFromUnreadable(tConnection):
    connection, mockContainer, _ = tConnection
    sAnswer = (
        '{"/p/absent.json": null, "/p/locked.json": {"sError": '
        '"PermissionError"}, "/p/ok.json": "e30="}'
    )
    mockContainer.exec_run.return_value = (0, (sAnswer.encode(), b""))
    dictFiles = connection.fdictFetchSmallFiles(
        "abc123", ["/p/absent.json", "/p/locked.json", "/p/ok.json"],
    )
    assert dictFiles["/p/absent.json"] is None
    assert dictFiles["/p/locked.json"] == UnreadableSmallFile("PermissionError")
    assert dictFiles["/p/ok.json"] == b"{}"


def testAnUnreadableMarkerIsUnknownNeverMissing():
    from vaibify.gui.stateManager import _fdictParseMarker
    dictMarker = _fdictParseMarker(
        UnreadableSmallFile("PermissionError"), "/p/Step01.json",
    )
    assert dictMarker["bMarkerUnreadable"] is True
    assert dictMarker["sMarkerPath"] == "/p/Step01.json"
    assert "PermissionError" in dictMarker["sReason"]
    assert _fdictParseMarker(None, "/p/Step01.json") is None


# ---------------------------------------------------------------------------
# A path the reader is refused is "could not check", never absent
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402
import sys  # noqa: E402

from vaibify.docker.dockerConnection import (  # noqa: E402
    _DICT_TYPED_READ_PROGRAMS, _S_TYPED_READ_PATH_SLOT, _fsTypedReadPathLiteral,
    S_TYPED_READ_DIRECTORIES_EXIST, S_TYPED_READ_DIRECTORY_EXISTS,
    S_TYPED_READ_FILE_EXISTS, S_TYPED_READ_PATHS_EXIST,
)


def _ftRunExistenceProgram(sOperation, objPaths):
    sProgram = _DICT_TYPED_READ_PROGRAMS[sOperation].replace(
        _S_TYPED_READ_PATH_SLOT, _fsTypedReadPathLiteral(objPaths),
    )
    resultProc = subprocess.run(
        [sys.executable, "-I", "-c", sProgram], capture_output=True,
        text=True, timeout=30,
    )
    return resultProc.returncode, resultProc.stdout, resultProc.stderr


@pytest.fixture
def dictProbeTree(tmp_path):
    """A readable file, an absent path, and a file behind a closed directory."""
    import stat as statModule
    if os.geteuid() == 0:
        pytest.skip("root is never refused a directory")
    pathClosed = tmp_path / "closed"
    pathClosed.mkdir()
    (pathClosed / "hidden.txt").write_text("x")
    (tmp_path / "plain.txt").write_text("y")
    os.chmod(pathClosed, 0)
    yield {
        "sPlain": str(tmp_path / "plain.txt"),
        "sAbsent": str(tmp_path / "absent.txt"),
        "sHidden": str(pathClosed / "hidden.txt"),
        "sClosed": str(pathClosed),
    }
    os.chmod(pathClosed, statModule.S_IRWXU)


@pytest.mark.falsification
def testARefusedPathIsNeverReportedAbsentByTheExistenceProbes(dictProbeTree):
    """The probes stat the path; EACCES exits non-zero, absence prints 0.

    The read-only reader can be refused a directory the container user
    owns. ``os.path.isfile`` answers False to that exactly as to a
    missing file, which read a manifest behind a mode-700 home as "not
    found" (CI, 2026-10-28). The host turns the non-zero exit into
    ``OSError``, which every gate reports as "could not check".

    Kills: turning the refusal branch of ``fbExistsOrRefuse`` back into
    ``return False``.
    """
    for sOperation in (S_TYPED_READ_FILE_EXISTS, S_TYPED_READ_DIRECTORY_EXISTS):
        iExit, sOut, sErr = _ftRunExistenceProgram(sOperation, dictProbeTree["sHidden"])
        assert iExit != 0 and "refused: PermissionError" in sErr, (sOperation, sOut, sErr)
        iExit, sOut, _ = _ftRunExistenceProgram(sOperation, dictProbeTree["sAbsent"])
        assert (iExit, sOut) == (0, "0"), sOperation
    iExit, sOut, _ = _ftRunExistenceProgram(S_TYPED_READ_FILE_EXISTS, dictProbeTree["sPlain"])
    assert (iExit, sOut) == (0, "1")
    iExit, sOut, _ = _ftRunExistenceProgram(S_TYPED_READ_DIRECTORY_EXISTS, dictProbeTree["sClosed"])
    assert (iExit, sOut) == (0, "1"), "the closed directory itself can be stat-ed"
    for sOperation in (S_TYPED_READ_PATHS_EXIST, S_TYPED_READ_DIRECTORIES_EXIST):
        iExit, sOut, sErr = _ftRunExistenceProgram(
            sOperation, [dictProbeTree["sPlain"], dictProbeTree["sHidden"]],
        )
        assert iExit != 0 and "refused" in sErr, (sOperation, sOut)
        iExit, sOut, _ = _ftRunExistenceProgram(
            sOperation, [dictProbeTree["sPlain"], dictProbeTree["sAbsent"]],
        )
        assert iExit == 0 and sOut.strip() == (
            "[true, false]" if sOperation == S_TYPED_READ_PATHS_EXIST
            else "[false, false]"
        ), (sOperation, sOut)


def testTheSnapshotAdapterRaisesForAPathTheReaderWasRefused():
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesSnapshot = SnapshotRepoFiles("/p", {
        "present.txt": {"bIsFile": True, "sText": "t", "iMtime": 1},
        "absent.txt": {"bIsFile": False, "sText": None, "iMtime": None},
        "refused.txt": {"bIsFile": None, "sText": None, "iMtime": None},
    }, {})
    assert filesSnapshot.fbIsFile("present.txt") is True
    assert filesSnapshot.fbIsFile("absent.txt") is False
    with pytest.raises(OSError, match="refused") as errorInfo:
        filesSnapshot.fbIsFile("refused.txt")
    assert not isinstance(errorInfo.value, FileNotFoundError)
    with pytest.raises(OSError, match="refused"):
        filesSnapshot.fsReadText("refused.txt")
    with pytest.raises(FileNotFoundError):
        filesSnapshot.fsReadText("absent.txt")
