"""Tests for vaibify.reproducibility.repoFiles — the adapter seam.

``HostRepoFiles`` is exercised against real ``tmp_path`` trees so the
atomicity, symlink-containment, and escape-rejection semantics that
the reproducibility modules enforce are preserved bit-for-bit. ``ContainerRepoFiles`` is exercised against a fake docker
connection that *actually executes* the adapter's shell commands in a
host shell rooted at a tmp directory, so the embedded hash/stat/read
scripts are tested for real behavior — while every exec is recorded so
batching guarantees (one exec for an N-path hash) can be asserted.
"""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.reproducibility.repoFiles import (
    ContainerRepoFiles,
    HostRepoFiles,
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
    fsShellQuotePosix,
)


class FakeExecDockerConnection:
    """Duck-typed docker connection that runs commands in a host shell.

    The "container" filesystem is just the host tmp directory the test
    rooted the adapter at, so the embedded python scripts and shell
    commands execute for real. Every exec is recorded for batching
    assertions; every write goes through ``fnWriteFile`` exactly as the
    production ``DockerConnection`` contract requires.
    """

    def __init__(self):
        self.listCommands = []
        self.listWrites = []

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        self.listCommands.append((sContainerId, sCommand))
        resultProcess = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return SimpleNamespace(
            iExitCode=resultProcess.returncode,
            sStdout=resultProcess.stdout,
            sStderr=resultProcess.stderr,
        )

    def fbaFetchFile(self, sContainerId, sPath):
        with open(sPath, "rb") as fileHandle:
            return fileHandle.read()

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.listWrites.append((sContainerId, sPath))
        os.makedirs(os.path.dirname(sPath) or ".", exist_ok=True)
        with open(sPath, "wb") as fileHandle:
            fileHandle.write(baContent)


@pytest.fixture
def filesHost(tmp_path):
    return HostRepoFiles(str(tmp_path))


@pytest.fixture
def connectionFake():
    return FakeExecDockerConnection()


@pytest.fixture
def filesContainer(tmp_path, connectionFake):
    return ContainerRepoFiles(connectionFake, "cid", str(tmp_path))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_ensure_wraps_string_in_host_adapter(tmp_path):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    assert isinstance(filesRepo, HostRepoFiles)
    assert filesRepo.sRootPath == str(tmp_path)


def test_ensure_none_maps_to_empty_root():
    filesRepo = ffilesEnsureRepoFiles(None)
    assert filesRepo.sRootPath == ""
    assert filesRepo.fbIsFile("anything") is False


def test_ensure_passes_adapter_through(filesHost):
    assert ffilesEnsureRepoFiles(filesHost) is filesHost


def test_repo_root_of_handles_all_forms(filesHost, tmp_path):
    assert fsRepoRootOf(str(tmp_path)) == str(tmp_path)
    assert fsRepoRootOf(filesHost) == str(tmp_path)
    assert fsRepoRootOf(None) == ""


def test_shell_quote_escapes_single_quote():
    sQuoted = fsShellQuotePosix("a'b")
    sEchoed = subprocess.run(
        ["bash", "-c", "printf %s " + sQuoted],
        capture_output=True, text=True,
    ).stdout
    assert sEchoed == "a'b"


# ---------------------------------------------------------------------------
# HostRepoFiles
# ---------------------------------------------------------------------------


def test_host_write_read_round_trip(filesHost, tmp_path):
    filesHost.fnWriteTextAtomic("subdir/data.txt", "alpha\n")
    assert filesHost.fbIsFile("subdir/data.txt")
    assert filesHost.fbIsDir("subdir")
    assert filesHost.fsReadText("subdir/data.txt") == "alpha\n"
    assert filesHost.fbaReadBytes("subdir/data.txt") == b"alpha\n"
    assert not (tmp_path / "subdir" / "data.txt.tmp").exists()


def test_host_json_round_trip(filesHost):
    filesHost.fnWriteJsonAtomic(".vaibify/state.json", {"iValue": 3})
    assert json.loads(filesHost.fsReadText(".vaibify/state.json")) == {
        "iValue": 3,
    }


def test_host_remove_file(filesHost):
    filesHost.fnWriteTextAtomic("gone.txt", "x")
    assert filesHost.fbRemoveFile("gone.txt") is True
    assert filesHost.fbRemoveFile("gone.txt") is False


def test_host_list_json_filenames_sorted_descending(filesHost):
    for sName in ("a.json", "c.json", "b.json", "ignored.txt"):
        filesHost.fnWriteTextAtomic(f"history/{sName}", "{}")
    assert filesHost.flistListJsonFilenames("history") == [
        "c.json", "b.json", "a.json",
    ]
    assert filesHost.flistListJsonFilenames("missing") == []


def test_host_stat_mtimes_omits_missing(filesHost):
    filesHost.fnWriteTextAtomic("present.txt", "x")
    dictMtimes = filesHost.fdictStatMtimes(["present.txt", "absent.txt"])
    assert set(dictMtimes) == {"present.txt"}
    assert isinstance(dictMtimes["present.txt"], int)


def test_host_hash_files_round_trip(filesHost):
    filesHost.fnWriteTextAtomic("out.csv", "1,2\n")
    dictHashes = filesHost.fdictHashFiles(["out.csv", "missing.csv"])
    assert dictHashes["out.csv"]["sSha256"]
    assert dictHashes["out.csv"]["sSymlinkSegment"] is None
    assert dictHashes["out.csv"]["bEscapesRoot"] is False
    assert dictHashes["missing.csv"]["sSha256"] is None


def test_host_hash_resolves_in_root_symlink_to_target_content(
    filesHost, tmp_path,
):
    (tmp_path / "real.txt").write_text("x")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    dictEntry = filesHost.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["sSymlinkSegment"] == "link.txt"
    assert dictEntry["bEscapesRoot"] is False
    sDirectHash = filesHost.fdictHashFiles(["real.txt"])["real.txt"][
        "sSha256"
    ]
    assert dictEntry["sSha256"] == sDirectHash


def test_host_hash_resolves_in_root_symlinked_directory_component(
    filesHost, tmp_path,
):
    (tmp_path / "actual").mkdir()
    (tmp_path / "actual" / "f.txt").write_text("x")
    (tmp_path / "alias").symlink_to(tmp_path / "actual")
    dictEntry = filesHost.fdictHashFiles(["alias/f.txt"])["alias/f.txt"]
    assert dictEntry["sSymlinkSegment"] == "alias"
    assert dictEntry["bEscapesRoot"] is False
    assert dictEntry["sSha256"] == filesHost.fdictHashFiles(
        ["actual/f.txt"],
    )["actual/f.txt"]["sSha256"]


def test_host_hash_refuses_symlink_escaping_root(tmp_path):
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    pathOutside = tmp_path / "outside.txt"
    pathOutside.write_text("loot")
    (pathRepo / "link.txt").symlink_to(pathOutside)
    filesHost = HostRepoFiles(str(pathRepo))
    dictEntry = filesHost.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["sSymlinkSegment"] == "link.txt"
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sSha256"] is None


def test_host_hash_rejects_absolute_path(filesHost):
    dictEntry = filesHost.fdictHashFiles(["/etc/hostname"])["/etc/hostname"]
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sSha256"] is None


def test_host_hash_absolute_paths(filesHost, tmp_path):
    sBinary = str(tmp_path / "tool")
    (tmp_path / "tool").write_text("#!/bin/sh\n")
    dictHashes = filesHost.fdictHashAbsolutePaths([sBinary, "/no/such"])
    assert dictHashes[sBinary]
    assert dictHashes["/no/such"] is None


def test_host_run_command_captures_output(filesHost):
    iExitCode, sStdout, _sStderr = filesHost.ftRunCommand(
        ["printf", "hello"], 5.0,
    )
    assert iExitCode == 0
    assert sStdout == "hello"


def test_host_run_command_missing_binary_is_soft_failure(filesHost):
    iExitCode, sStdout, sStderr = filesHost.ftRunCommand(
        ["/no/such/binary", "--version"], 5.0,
    )
    assert iExitCode == 127
    assert sStdout == "" and sStderr == ""


def test_host_lock_round_trip(filesHost):
    with filesHost.fnWithLock(".vaibify/syncStatus.json"):
        filesHost.fnWriteJsonAtomic(".vaibify/syncStatus.json", {})
    assert filesHost.fbIsFile(".vaibify/syncStatus.json")


def test_empty_root_probes_are_false():
    filesEmpty = HostRepoFiles("")
    assert filesEmpty.fbIsFile("a") is False
    assert filesEmpty.fbIsDir("a") is False
    assert filesEmpty.fsLocalRootOrNone() is None
    assert filesEmpty.fdictHashFiles(["a"])["a"]["bEscapesRoot"] is True


def test_host_write_rejects_traversal_and_empty_root(filesHost):
    with pytest.raises(ValueError):
        filesHost.fnWriteTextAtomic("../escape.txt", "x")
    with pytest.raises(ValueError):
        filesHost.fnWriteTextAtomic("/etc/escape.txt", "x")
    with pytest.raises(ValueError):
        HostRepoFiles("").fnWriteTextAtomic("a.txt", "x")
    assert filesHost.fbRemoveFile("../escape.txt") is False
    with pytest.raises(ValueError):
        HostRepoFiles("").fnWithLock("a.json")


def test_container_write_rejects_traversal_and_empty_root(
    filesContainer, connectionFake,
):
    with pytest.raises(ValueError):
        filesContainer.fnWriteTextAtomic("../escape.txt", "x")
    with pytest.raises(ValueError):
        ContainerRepoFiles(connectionFake, "cid", "").fnWriteTextAtomic(
            "a.txt", "x",
        )
    assert filesContainer.fbRemoveFile("../escape.txt") is False
    assert connectionFake.listWrites == []


# ---------------------------------------------------------------------------
# ContainerRepoFiles (fake exec runs the commands for real)
# ---------------------------------------------------------------------------


def test_container_write_read_round_trip(filesContainer, tmp_path):
    filesContainer.fnWriteTextAtomic("subdir/data.txt", "beta\n")
    assert (tmp_path / "subdir" / "data.txt").read_text() == "beta\n"
    assert not (tmp_path / "subdir" / "data.txt.tmp").exists()
    assert filesContainer.fbIsFile("subdir/data.txt") is True
    assert filesContainer.fsReadText("subdir/data.txt") == "beta\n"


def test_container_writes_only_via_connection(
    filesContainer, connectionFake, tmp_path,
):
    filesContainer.fnWriteJsonAtomic(".vaibify/env.json", {"iA": 1})
    assert connectionFake.listWrites == [
        ("cid", str(tmp_path / ".vaibify" / "env.json.tmp")),
    ]


def test_container_remove_file(filesContainer, tmp_path):
    (tmp_path / "gone.txt").write_text("x")
    assert filesContainer.fbRemoveFile("gone.txt") is True
    assert not (tmp_path / "gone.txt").exists()
    assert filesContainer.fbRemoveFile("gone.txt") is False


def test_container_hash_files_is_one_exec(
    filesContainer, connectionFake, tmp_path,
):
    for iIndex in range(5):
        (tmp_path / f"out{iIndex}.dat").write_text(str(iIndex))
    listPaths = [f"out{iIndex}.dat" for iIndex in range(5)] + ["nope.dat"]
    dictHashes = filesContainer.fdictHashFiles(listPaths)
    assert len(connectionFake.listCommands) == 1
    assert all(dictHashes[f"out{iIndex}.dat"]["sSha256"]
               for iIndex in range(5))
    assert dictHashes["nope.dat"]["sSha256"] is None


def test_container_hash_matches_host_hash(
    filesContainer, filesHost, tmp_path,
):
    (tmp_path / "same.bin").write_bytes(b"\x00\x01payload")
    sContainerSha = filesContainer.fdictHashFiles(
        ["same.bin"],
    )["same.bin"]["sSha256"]
    sHostSha = filesHost.fdictHashFiles(["same.bin"])["same.bin"]["sSha256"]
    assert sContainerSha == sHostSha


def test_container_hash_resolves_in_root_symlink(
    filesContainer, tmp_path,
):
    (tmp_path / "real.txt").write_text("x")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    dictEntry = filesContainer.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["sSymlinkSegment"] == "link.txt"
    assert dictEntry["bEscapesRoot"] is False
    assert dictEntry["sSha256"] == filesContainer.fdictHashFiles(
        ["real.txt"],
    )["real.txt"]["sSha256"]


def test_container_hash_refuses_symlink_escaping_root(
    connectionFake, tmp_path,
):
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    pathOutside = tmp_path / "outside.txt"
    pathOutside.write_text("loot")
    (pathRepo / "link.txt").symlink_to(pathOutside)
    filesContainer = ContainerRepoFiles(
        connectionFake, "cid", str(pathRepo),
    )
    dictEntry = filesContainer.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["sSymlinkSegment"] == "link.txt"
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sSha256"] is None


class _CannedExecConnection:
    """Fake connection returning a fixed exec result (records commands)."""

    def __init__(self, iExitCode=0, sStdout=""):
        self.listCommands = []
        self.iExitCode = iExitCode
        self.sStdout = sStdout

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        self.listCommands.append((sContainerId, sCommand))
        return SimpleNamespace(
            iExitCode=self.iExitCode, sStdout=self.sStdout, sStderr="",
        )


def test_container_stat_mtimes_batch_parses_gnu_stat_lines():
    """One exec stats N paths; ``stat -c`` lines map back to rel keys.

    ``stat -c`` is GNU coreutils (always present inside vaibify
    containers, absent on macOS hosts), so the exec output is canned
    here while the command shape and the parser run for real.
    """
    connectionCanned = _CannedExecConnection(
        sStdout="/repo/a.txt 1700000001\n/repo/b.txt 1700000002\n",
    )
    filesRepo = ContainerRepoFiles(connectionCanned, "cid", "/repo")
    dictMtimes = filesRepo.fdictStatMtimes(
        ["a.txt", "b.txt", "missing.txt"],
    )
    assert len(connectionCanned.listCommands) == 1
    sCommand = connectionCanned.listCommands[0][1]
    assert sCommand.startswith("stat -c '%n %Y'")
    assert "'/repo/missing.txt'" in sCommand
    assert dictMtimes == {"a.txt": 1700000001, "b.txt": 1700000002}


def test_container_read_dir_json_is_one_exec(
    filesContainer, connectionFake, tmp_path,
):
    (tmp_path / "hist").mkdir()
    (tmp_path / "hist" / "x.json").write_text('{"iA": 1}')
    (tmp_path / "hist" / "y.json").write_text('{"iB": 2}')
    dictContents = filesContainer.fdictReadDirJsonContents("hist")
    assert len(connectionFake.listCommands) == 1
    assert json.loads(dictContents["x.json"]) == {"iA": 1}
    assert filesContainer.flistListJsonFilenames("hist") == [
        "y.json", "x.json",
    ]


def test_container_run_command_is_timeout_guarded():
    """The command runs under the container's ``timeout`` utility.

    ``timeout`` is GNU coreutils (always present inside vaibify
    containers, absent on macOS hosts), so the exec is canned while
    the command shape and the result plumbing are asserted for real.
    """
    connectionCanned = _CannedExecConnection(sStdout="v1.2.3\n")
    filesRepo = ContainerRepoFiles(connectionCanned, "cid", "/repo")
    iExitCode, sStdout, sStderr = filesRepo.ftRunCommand(
        ["/usr/bin/tool", "--version"], 5.0,
    )
    sCommand = connectionCanned.listCommands[0][1]
    assert sCommand == "timeout 5 '/usr/bin/tool' '--version'"
    assert (iExitCode, sStdout, sStderr) == (0, "v1.2.3\n", "")


def test_container_path_with_quote_is_safely_quoted(
    filesContainer, tmp_path,
):
    (tmp_path / "o'data.txt").write_text("q")
    assert filesContainer.fbIsFile("o'data.txt") is True
    dictEntry = filesContainer.fdictHashFiles(
        ["o'data.txt"],
    )["o'data.txt"]
    assert dictEntry["sSha256"]


def test_container_local_root_is_none(filesContainer):
    assert filesContainer.fsLocalRootOrNone() is None


def test_container_lock_is_shared_per_container_and_path(connectionFake):
    filesOne = ContainerRepoFiles(connectionFake, "cid", "/repo")
    filesTwo = ContainerRepoFiles(connectionFake, "cid", "/repo")
    lockOne = filesOne.fnWithLock(".vaibify/syncStatus.json")
    lockTwo = filesTwo.fnWithLock(".vaibify/syncStatus.json")
    assert lockOne is lockTwo
    lockOther = filesOne.fnWithLock(".vaibify/other.json")
    assert lockOther is not lockOne


def test_container_never_touches_host_at_container_path(connectionFake):
    """A container root that does not exist on the host stays untouched."""
    filesRepo = ContainerRepoFiles(
        connectionFake, "cid", "/no/such/container/root",
    )
    assert filesRepo.fbIsFile("MANIFEST.sha256") is False
    dictEntry = filesRepo.fdictHashFiles(["out.dat"])["out.dat"]
    assert dictEntry["sSha256"] is None
    assert not os.path.exists("/no/such/container/root")
