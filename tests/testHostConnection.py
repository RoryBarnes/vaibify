"""Tests for the host-mode connection: path guard, reads, writes, exec.

Every exec assertion here drives a REAL subprocess and a REAL journal
file in a temp directory — never a stub keyed the same way as the code
under test (the repo's epistemics rule). The journal write-ahead
ordering and the group-kill tests are registered as falsifications.
"""

import os
import stat

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.host import hostScratch
from vaibify.host.hostConnection import (
    HostConnection,
    HostPathOutsideProjectError,
    UnknownHostProjectError,
)
from vaibify.host.hostScratch import fsHostScratchRootForProject

S_PROJECT_NAME = "host-conn-proj"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    # Patched on hostScratch, which OWNS the subtree layout; the
    # connection imports the derivation rather than keeping a
    # second copy of it, so there is one place to redirect.
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


@pytest.fixture()
def tProjectAndConnection(tmp_path):
    """Return (sProjectRoot, HostConnection) over a temp project."""
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot)
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    return sProjectRoot, connection


# --- the path guard (plan §8) ---

class TestHostPathGuard:

    def test_relative_path_resolves_against_the_project_root(
        self, tProjectAndConnection,
    ):
        """Superseding "a relative path is refused" (2026-08-10).

        That rule was written before any host workflow had been
        opened, and the first one that was found it wrong: the file
        poll asks about ``MakeNumbers/analysis.py``, because
        repo-relative is the wire contract for every step directory,
        script and output. The container leg has always resolved such
        a path against the container root -- docker exec runs in the
        image's working directory -- so refusing it here made one
        connection answer one input two ways depending on the leg.

        The escaping relative path beside it is the reason nothing is
        given up: the join happens BEFORE containment is checked.
        """
        sProjectRoot, connection = tProjectAndConnection
        with open(
            os.path.join(sProjectRoot, "inside.txt"), "w",
        ) as fileInside:
            fileInside.write("here")
        assert connection.fbContainerPathIsFile(
            S_PROJECT_NAME, "inside.txt",
        )
        with pytest.raises(HostPathOutsideProjectError):
            connection.fbContainerPathIsFile(
                S_PROJECT_NAME, "../escaped.txt",
            )

    def test_absolute_smuggling_is_refused(self, tProjectAndConnection):
        _, connection = tProjectAndConnection
        with pytest.raises(HostPathOutsideProjectError):
            connection.fbContainerPathIsFile(S_PROJECT_NAME, "/etc/passwd")

    def test_traversal_is_refused(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        with pytest.raises(HostPathOutsideProjectError):
            connection.fbContainerPathIsFile(
                S_PROJECT_NAME,
                os.path.join(sProjectRoot, "..", "outside.txt"),
            )

    def test_symlink_escape_is_refused(
        self, tProjectAndConnection, tmp_path,
    ):
        sProjectRoot, connection = tProjectAndConnection
        sOutsideDir = str(tmp_path / "outside")
        os.makedirs(sOutsideDir)
        sLinkPath = os.path.join(sProjectRoot, "sneaky")
        os.symlink(sOutsideDir, sLinkPath)
        with pytest.raises(HostPathOutsideProjectError):
            connection.fbContainerPathIsFile(
                S_PROJECT_NAME, os.path.join(sLinkPath, "file.txt"),
            )

    def test_prefix_collision_is_refused(
        self, tProjectAndConnection, tmp_path,
    ):
        sProjectRoot, connection = tProjectAndConnection
        sSiblingDir = sProjectRoot + "XY"
        os.makedirs(sSiblingDir)
        with pytest.raises(HostPathOutsideProjectError):
            connection.fbContainerPathIsFile(
                S_PROJECT_NAME, os.path.join(sSiblingDir, "file.txt"),
            )

    def test_scratch_root_is_admitted(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        sScratchRoot = fsHostScratchRootForProject(sProjectRoot)
        os.makedirs(sScratchRoot)
        sScratchFile = os.path.join(sScratchRoot, "diag.txt")
        with open(sScratchFile, "w") as fileHandle:
            fileHandle.write("x")
        assert connection.fbContainerPathIsFile(
            S_PROJECT_NAME, sScratchFile,
        )

    def test_unknown_project_is_refused_by_default_resolver(self):
        connection = HostConnection()
        with pytest.raises(UnknownHostProjectError):
            connection.fbContainerPathIsFile(
                "no-such-project", "/anywhere",
            )


# --- typed reads ---

class TestTypedReads:

    def test_fetch_file_round_trip(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        sFilePath = os.path.join(sProjectRoot, "data.bin")
        with open(sFilePath, "wb") as fileHandle:
            fileHandle.write(b"payload-bytes")
        assert connection.fbaFetchFile(
            S_PROJECT_NAME, sFilePath,
        ) == b"payload-bytes"

    def test_fetch_missing_file_raises(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        with pytest.raises(FileNotFoundError):
            connection.fbaFetchFile(
                S_PROJECT_NAME, os.path.join(sProjectRoot, "absent"),
            )

    def test_fetch_cap_is_enforced(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        sFilePath = os.path.join(sProjectRoot, "big.bin")
        with open(sFilePath, "wb") as fileHandle:
            fileHandle.write(b"x" * 32)
        with pytest.raises(ValueError, match="cap"):
            connection.fbaFetchFile(S_PROJECT_NAME, sFilePath, iMaxBytes=16)

    def test_directory_entries_and_probes(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        os.makedirs(os.path.join(sProjectRoot, "sub"))
        with open(os.path.join(sProjectRoot, "b.txt"), "w") as fileHandle:
            fileHandle.write("x")
        with open(os.path.join(sProjectRoot, "a.txt"), "w") as fileHandle:
            fileHandle.write("x")
        assert connection.flistDirectoryEntries(
            S_PROJECT_NAME, sProjectRoot,
        ) == ["a.txt", "b.txt", "sub"]
        assert connection.fbContainerPathIsFile(
            S_PROJECT_NAME, os.path.join(sProjectRoot, "a.txt"),
        )
        assert connection.fbContainerPathIsDirectory(
            S_PROJECT_NAME, os.path.join(sProjectRoot, "sub"),
        )
        assert connection.flistContainerPathsExist(
            S_PROJECT_NAME,
            [
                os.path.join(sProjectRoot, "a.txt"),
                os.path.join(sProjectRoot, "missing.txt"),
            ],
        ) == [True, False]

    def test_filesystem_usage_shape(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        dictUsage = connection.fdictReadFilesystemUsage(
            S_PROJECT_NAME, sProjectRoot,
        )
        assert set(dictUsage) == {
            "iTotalBytes", "iUsedBytes", "iFreeBytes",
        }
        assert dictUsage["iTotalBytes"] > 0

    def test_stream_file_yields_all_bytes(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        sFilePath = os.path.join(sProjectRoot, "stream.bin")
        baPayload = os.urandom(3000)
        with open(sFilePath, "wb") as fileHandle:
            fileHandle.write(baPayload)
        baCollected = b"".join(connection.fiterStreamFile(
            S_PROJECT_NAME, sFilePath, iChunkSizeBytes=1024,
        ))
        assert baCollected == baPayload


# --- writes ---

class TestAtomicWrites:

    def test_write_lands_and_new_file_mode(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        sFilePath = os.path.join(sProjectRoot, "out.json")
        connection.fnWriteFile(S_PROJECT_NAME, sFilePath, b'{"iKey": 1}')
        with open(sFilePath, "rb") as fileHandle:
            assert fileHandle.read() == b'{"iKey": 1}'
        assert stat.S_IMODE(os.stat(sFilePath).st_mode) == 0o644

    def test_replaced_executable_keeps_its_bit(
        self, tProjectAndConnection,
    ):
        sProjectRoot, connection = tProjectAndConnection
        sScriptPath = os.path.join(sProjectRoot, "run.sh")
        with open(sScriptPath, "w") as fileHandle:
            fileHandle.write("#!/bin/bash\n")
        os.chmod(sScriptPath, 0o755)
        connection.fnWriteFile(
            S_PROJECT_NAME, sScriptPath, b"#!/bin/bash\necho new\n",
        )
        assert stat.S_IMODE(os.stat(sScriptPath).st_mode) == 0o755

    def test_explicit_mode_wins(self, tProjectAndConnection):
        sProjectRoot, connection = tProjectAndConnection
        sSecretPath = os.path.join(sProjectRoot, "secret.env")
        connection.fnWriteFile(
            S_PROJECT_NAME, sSecretPath, b"quiet", iMode=0o600,
        )
        assert stat.S_IMODE(os.stat(sSecretPath).st_mode) == 0o600

    def test_write_outside_root_is_refused(
        self, tProjectAndConnection, tmp_path,
    ):
        _, connection = tProjectAndConnection
        sOutsidePath = str(tmp_path / "outside.txt")
        with pytest.raises(HostPathOutsideProjectError):
            connection.fnWriteFile(S_PROJECT_NAME, sOutsidePath, b"nope")
        assert not os.path.exists(sOutsidePath)


# --- the gated, journaled exec primitive (plan §4-5) ---

def _fdictReadSingleOperation():
    """Return the single journal record for the test project."""
    dictOutcome = operationJournal.fdictReadJournalOutcome(S_PROJECT_NAME)
    dictOperations = dictOutcome["dictOperations"]
    assert len(dictOperations) == 1, dictOperations
    return next(iter(dictOperations.values()))


class TestGatedExec:

    def test_echo_round_trip_and_stream_split(
        self, tProjectAndConnection,
    ):
        _, connection = tProjectAndConnection
        tExecResult = connection.ftRunInContainerStreamed(
            S_PROJECT_NAME, "echo out-line; echo err-line >&2",
        )
        assert tExecResult.iExitCode == 0
        assert tExecResult.sStdout == "out-line"
        assert tExecResult.sStderr == "err-line"

    def test_default_workdir_is_the_project_root(
        self, tProjectAndConnection,
    ):
        sProjectRoot, connection = tProjectAndConnection
        tExecResult = connection.ftRunInContainerStreamed(
            S_PROJECT_NAME, "pwd",
        )
        assert tExecResult.sStdout == os.path.realpath(sProjectRoot)

    def test_workdir_outside_root_is_refused(
        self, tProjectAndConnection, tmp_path,
    ):
        _, connection = tProjectAndConnection
        with pytest.raises(HostPathOutsideProjectError):
            connection.ftRunInContainerStreamed(
                S_PROJECT_NAME, "pwd", sWorkdir=str(tmp_path),
            )

    def test_explicit_user_is_refused(self, tProjectAndConnection):
        _, connection = tProjectAndConnection
        with pytest.raises(ValueError, match="invoking user"):
            connection.ftRunInContainerStreamed(
                S_PROJECT_NAME, "id", sUser="root",
            )

    def test_chunk_callback_receives_ordered_lines(
        self, tProjectAndConnection,
    ):
        _, connection = tProjectAndConnection
        listChunks = []
        tExecResult = connection.ftRunInContainerStreamedWithChunks(
            S_PROJECT_NAME, "echo one; echo two",
            lambda sStream, sLine: listChunks.append((sStream, sLine)),
        )
        assert tExecResult.iExitCode == 0
        assert [
            tChunk for tChunk in listChunks if tChunk[0] == "stdout"
        ] == [("stdout", "one"), ("stdout", "two")]

    def test_merged_wrapper_contract(self, tProjectAndConnection):
        _, connection = tProjectAndConnection
        iExitCode, sOutput = connection.ftResultExecuteCommand(
            S_PROJECT_NAME, "echo merged; exit 3",
        )
        assert iExitCode == 3
        assert "merged" in sOutput

    def test_identity_is_journaled_before_the_gate_opens(
        self, tProjectAndConnection,
    ):
        """The write-ahead ordering: journal in-flight, THEN release.

        At the ``promoted`` phase the child exists but is still held
        behind the gate, so the journal must already carry its
        recycle-proof identity and the command's marker file must not
        exist yet. Registered as a falsification: swapping the release
        ahead of the promotion kills this test.
        """
        sProjectRoot, connection = tProjectAndConnection
        sMarkerPath = os.path.join(sProjectRoot, "gate-marker")
        dictSeen = {}

        def fnRecordPhase(sPhase):
            if sPhase == "promoted":
                import time as moduleTime
                moduleTime.sleep(0.3)
                dictSeen["bMarkerBeforeRelease"] = os.path.exists(
                    sMarkerPath,
                )
                dictRecord = _fdictReadSingleOperation()
                dictSeen["sStateAtPromoted"] = dictRecord["sState"]
                dictSeen["iJournaledPid"] = dictRecord.get("iHolderPid")

        tExecResult = connection.ftRunInContainerStreamedWithChunks(
            S_PROJECT_NAME, f"touch {sMarkerPath}", None,
            fnPhaseCallback=fnRecordPhase,
        )
        assert tExecResult.iExitCode == 0
        assert dictSeen["bMarkerBeforeRelease"] is False
        assert dictSeen["sStateAtPromoted"] == (
            operationJournal.S_OPERATION_STATE_IN_FLIGHT
        )
        assert dictSeen["iJournaledPid"] is not None
        assert os.path.exists(sMarkerPath)
        dictOutcome = operationJournal.fdictReadJournalOutcome(
            S_PROJECT_NAME,
        )
        assert dictOutcome["dictOperations"] == {}

    def test_timeout_terminates_the_whole_recorded_group(
        self, tProjectAndConnection,
    ):
        """A timed-out launch leaves NOTHING alive in its group.

        The command backgrounds a second sleeper, so killing only the
        direct child would leave a survivor — registered as a
        falsification: narrowing the group kill to a single PID kills
        this test.
        """
        _, connection = tProjectAndConnection
        dictSeen = {}

        def fnCapturePromotedIdentity(sPhase):
            if sPhase == "promoted":
                dictSeen["iProcessGroup"] = _fdictReadSingleOperation()[
                    "iHolderProcessGroup"
                ]

        import time as moduleTime
        fStarted = moduleTime.monotonic()
        tExecResult = connection.ftRunInContainerStreamed(
            S_PROJECT_NAME,
            "sleep 30 >/dev/null 2>&1 & sleep 30",
            fTimeoutSeconds=1.0,
            fnPhaseCallback=fnCapturePromotedIdentity,
        )
        fElapsedSeconds = moduleTime.monotonic() - fStarted
        assert fElapsedSeconds < 15.0, (
            "the bounded lane must return promptly; blocking until the "
            "survivors die naturally is the unbounded-pipe defect"
        )
        assert tExecResult.iExitCode == 124
        assert "timed out" in tExecResult.sStderr
        with pytest.raises(ProcessLookupError):
            os.killpg(dictSeen["iProcessGroup"], 0)
        dictOutcome = operationJournal.fdictReadJournalOutcome(
            S_PROJECT_NAME,
        )
        assert dictOutcome["dictOperations"] == {}

    def test_bounded_lane_is_not_hostage_to_a_held_pipe(
        self, tProjectAndConnection,
    ):
        """A backgrounded pipe-holder must not unbound the bounded lane.

        The command exits immediately but its background child inherits
        the output pipes, so a naive collection blocks until that child
        dies naturally (the defect a surviving falsification mutant
        exposed). The fix drains with a bound and escalates to a group
        termination.
        """
        _, connection = tProjectAndConnection
        import time as moduleTime
        dictSeen = {}

        def fnCapturePromotedIdentity(sPhase):
            if sPhase == "promoted":
                dictSeen["iProcessGroup"] = _fdictReadSingleOperation()[
                    "iHolderProcessGroup"
                ]

        fStarted = moduleTime.monotonic()
        tExecResult = connection.ftRunInContainerStreamed(
            S_PROJECT_NAME, "sleep 30 & echo started",
            fTimeoutSeconds=60.0,
            fnPhaseCallback=fnCapturePromotedIdentity,
        )
        fElapsedSeconds = moduleTime.monotonic() - fStarted
        assert fElapsedSeconds < 15.0
        assert "started" in tExecResult.sStdout
        with pytest.raises(ProcessLookupError):
            os.killpg(dictSeen["iProcessGroup"], 0)

    def test_lingering_group_member_blocks_settlement(
        self, tProjectAndConnection,
    ):
        """A survivor in the group leaves the record honestly unsettled."""
        _, connection = tProjectAndConnection
        tExecResult = connection.ftRunInContainerStreamed(
            S_PROJECT_NAME,
            "sleep 3 >/dev/null 2>&1 & echo done",
        )
        assert tExecResult.iExitCode == 0
        dictRecord = _fdictReadSingleOperation()
        assert dictRecord["sState"] == (
            operationJournal.S_OPERATION_STATE_IN_FLIGHT
        )
        os.killpg(dictRecord["iHolderProcessGroup"], 15)
