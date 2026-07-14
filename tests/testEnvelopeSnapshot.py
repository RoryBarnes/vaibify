"""Tests for ``SnapshotRepoFiles`` — the one-exec-per-poll adapter.

The fake docker connection executes the embedded snapshot script in a
host shell rooted at ``tmp_path``, so the in-container collection
logic runs for real. The key guarantees under test: the fetch costs
exactly one exec; the snapshot is read-only; gate-relevant reads agree
with a live adapter over the same fixture tree (truth equivalence);
seeded cache entries merge under fetched results; and a failed exec
degrades conservatively instead of crashing or reporting green.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.reproducibility.repoFiles import (
    TUPLE_SNAPSHOT_CONTENT_PATHS,
    HostRepoFiles,
    SnapshotRepoFiles,
)


class FakeExecDockerConnection:
    """Run the snapshot exec in a host shell, recording every call."""

    def __init__(self):
        self.listCommands = []

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


class FailingExecConnection:
    """Simulate a container whose exec crashes (stopped container)."""

    def __init__(self):
        self.listCommands = []

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        self.listCommands.append((sContainerId, sCommand))
        return SimpleNamespace(iExitCode=1, sStdout="", sStderr="boom")


def _fnSeedEnvelopeTree(tmp_path):
    """Write a small envelope fixture tree under tmp_path."""
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / "MANIFEST.sha256").write_text("# vaibify manifest v1\n")
    (tmp_path / "Dockerfile").write_text("FROM x@sha256:abc\n")
    (tmp_path / ".vaibify" / "environment.json").write_text(
        json.dumps({"sImageDigest": "img@sha256:def"}),
    )
    (tmp_path / "analyze.py").write_text("print('analysis')\n")
    (tmp_path / "out.csv").write_text("1,2\n")


@pytest.fixture
def filesSnapshot(tmp_path):
    _fnSeedEnvelopeTree(tmp_path)
    connectionFake = FakeExecDockerConnection()
    filesFetched = SnapshotRepoFiles.ffilesFetch(
        connectionFake, "cid", str(tmp_path),
        listScriptRelPaths=["analyze.py"],
        listHashRelPaths=["out.csv"],
    )
    filesFetched.connectionUsed = connectionFake
    return filesFetched


def test_fetch_is_exactly_one_exec(filesSnapshot):
    assert len(filesSnapshot.connectionUsed.listCommands) == 1


def test_snapshot_matches_live_adapter_truth(filesSnapshot, tmp_path):
    """Gate reads from the snapshot equal reads from a live adapter.

    ``MANIFEST.sha256`` text is deliberately excluded from the
    snapshot to amortize its body cost across polls (it is hydrated
    lazily by the poll route via a sha-keyed cache). The truth check
    here covers existence, mtime, hashes, and the body of every other
    envelope file.
    """
    from vaibify.reproducibility.repoFiles import (
        TUPLE_SNAPSHOT_SKIP_TEXT_PATHS,
    )
    filesLive = HostRepoFiles(str(tmp_path))
    for sRelPath in TUPLE_SNAPSHOT_CONTENT_PATHS:
        assert filesSnapshot.fbIsFile(sRelPath) == filesLive.fbIsFile(
            sRelPath,
        ), sRelPath
        if filesLive.fbIsFile(sRelPath) and (
            sRelPath not in TUPLE_SNAPSHOT_SKIP_TEXT_PATHS
        ):
            assert filesSnapshot.fsReadText(
                sRelPath,
            ) == filesLive.fsReadText(sRelPath), sRelPath
    listHashPaths = ["MANIFEST.sha256", "analyze.py", "out.csv"]
    assert filesSnapshot.fdictHashFiles(
        listHashPaths,
    ) == filesLive.fdictHashFiles(listHashPaths)


def test_snapshot_omits_manifest_body_but_keeps_mtime_and_sha(
    filesSnapshot, tmp_path,
):
    """The manifest body is excluded; mtime and sha remain available."""
    # The mtime is collected even for skipped-text paths so callers can
    # invalidate caches keyed by it.
    dictMtimes = filesSnapshot.fdictStatMtimes(["MANIFEST.sha256"])
    assert "MANIFEST.sha256" in dictMtimes
    # The body is intentionally not part of the snapshot — readers must
    # hydrate it via the lazy path.
    with pytest.raises(FileNotFoundError):
        filesSnapshot.fsReadText("MANIFEST.sha256")
    # The hash batch still carries the sha so a sha-keyed parse cache
    # can short-circuit on subsequent polls.
    dictHash = filesSnapshot.fdictHashFiles(["MANIFEST.sha256"])
    assert dictHash["MANIFEST.sha256"]["sSha256"] is not None


def test_manifest_text_injection_unblocks_fsReadText(filesSnapshot):
    """Once injected, the snapshot returns the manifest body."""
    from vaibify.reproducibility.repoFiles import (
        fnInjectManifestTextIntoSnapshot,
    )
    fnInjectManifestTextIntoSnapshot(filesSnapshot, "hydrated\n")
    assert filesSnapshot.fsReadText("MANIFEST.sha256") == "hydrated\n"


def test_snapshot_read_of_absent_file_raises_file_not_found(filesSnapshot):
    assert filesSnapshot.fbIsFile("reproduce.sh") is False
    with pytest.raises(FileNotFoundError):
        filesSnapshot.fsReadText("reproduce.sh")


def test_snapshot_unsampled_path_raises_key_error(filesSnapshot):
    with pytest.raises(KeyError):
        filesSnapshot.fbIsFile("never/sampled.txt")


def test_snapshot_stat_mtimes_omits_unsampled(filesSnapshot):
    dictMtimes = filesSnapshot.fdictStatMtimes(
        ["MANIFEST.sha256", "never/sampled.txt"],
    )
    assert set(dictMtimes) == {"MANIFEST.sha256"}


def test_every_write_method_raises(filesSnapshot):
    with pytest.raises(NotImplementedError):
        filesSnapshot.fnWriteTextAtomic("a.txt", "x")
    with pytest.raises(NotImplementedError):
        filesSnapshot.fnWriteJsonAtomic("a.json", {})
    with pytest.raises(NotImplementedError):
        filesSnapshot.fbRemoveFile("a.txt")
    with pytest.raises(NotImplementedError):
        filesSnapshot.fnWithLock("a.txt")
    with pytest.raises(NotImplementedError):
        filesSnapshot.ftRunCommand(["true"], 1.0)
    with pytest.raises(NotImplementedError):
        filesSnapshot.flistListJsonFilenames(".vaibify")


def test_hash_absolute_paths_reads_prefetched_batch():
    """The snapshot answers absolute-path hashing from its pre-fetched
    batch — no second exec — and maps unsampled paths to None.

    The poll hashes declared-binary absolute paths in its single exec
    (the out-of-repo guard was lifted deliberately), so a later
    fdictHashAbsolutePaths call must read those values, not raise.
    """
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesSnapshot = SnapshotRepoFiles(
        "/repo", {}, {},
        dictAbsHashes={"/home/u/.local/bin/vplanet": "a" * 64},
    )
    dictResult = filesSnapshot.fdictHashAbsolutePaths([
        "/home/u/.local/bin/vplanet",
        "/home/u/.local/bin/maxlev",
    ])
    assert dictResult["/home/u/.local/bin/vplanet"] == "a" * 64
    assert dictResult["/home/u/.local/bin/maxlev"] is None


def test_seed_hashes_merge_and_fetched_results_win(tmp_path):
    _fnSeedEnvelopeTree(tmp_path)
    connectionFake = FakeExecDockerConnection()
    dictSeed = {
        "cached.dat": {
            "sSha256": "cachedsha", "sSymlinkSegment": None,
            "bEscapesRoot": False,
        },
        "out.csv": {
            "sSha256": "staleseed", "sSymlinkSegment": None,
            "bEscapesRoot": False,
        },
    }
    filesFetched = SnapshotRepoFiles.ffilesFetch(
        connectionFake, "cid", str(tmp_path),
        listHashRelPaths=["out.csv"], dictSeedHashes=dictSeed,
    )
    dictHashes = filesFetched.fdictHashFiles(["cached.dat", "out.csv"])
    assert dictHashes["cached.dat"]["sSha256"] == "cachedsha"
    sFreshSha = HostRepoFiles(str(tmp_path)).fdictHashFiles(
        ["out.csv"],
    )["out.csv"]["sSha256"]
    assert dictHashes["out.csv"]["sSha256"] == sFreshSha


def test_failed_exec_degrades_to_all_absent(tmp_path):
    """A crashed snapshot exec reports the conservative reading.

    Every envelope file reads as absent (gates degrade toward "not
    verified" for one poll) rather than crashing the file-status poll
    or reporting state greener than what was actually observed.
    """
    _fnSeedEnvelopeTree(tmp_path)
    filesFetched = SnapshotRepoFiles.ffilesFetch(
        FailingExecConnection(), "cid", str(tmp_path),
        listHashRelPaths=["out.csv"],
    )
    for sRelPath in TUPLE_SNAPSHOT_CONTENT_PATHS:
        assert filesFetched.fbIsFile(sRelPath) is False
    assert filesFetched.fdictHashFiles(
        ["out.csv"],
    )["out.csv"]["sSha256"] is None


def test_empty_root_snapshot_probes_false():
    filesFetched = SnapshotRepoFiles("", {}, {})
    assert filesFetched.fbIsFile("MANIFEST.sha256") is False
    assert filesFetched.fsLocalRootOrNone() is None


def test_gates_compute_same_level_from_snapshot_and_live(tmp_path):
    """fiAICSLevel agrees between the poll snapshot and a live adapter."""
    from vaibify.reproducibility.levelGates import fiAICSLevel
    _fnSeedEnvelopeTree(tmp_path)
    dictWorkflow = {
        "sProjectRepoPath": str(tmp_path),
        "listSteps": [{
            "sName": "OnlyStep",
            "saDataFiles": ["out.csv"],
            "dictVerification": {"sUser": "untested"},
        }],
    }
    filesFetched = SnapshotRepoFiles.ffilesFetch(
        FakeExecDockerConnection(), "cid", str(tmp_path),
        listScriptRelPaths=["analyze.py"],
        listHashRelPaths=["out.csv"],
    )
    iLevelSnapshot = fiAICSLevel(dictWorkflow, filesFetched)
    iLevelLive = fiAICSLevel(dictWorkflow, HostRepoFiles(str(tmp_path)))
    assert iLevelSnapshot == iLevelLive
