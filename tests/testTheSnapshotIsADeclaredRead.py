"""The poll snapshot is a declared typed read, on one shared program.

The snapshot used to travel through the general exec primitive, which
the mutation gate must treat as mutating. Inside the readiness route's
enforced lane that meant it could only run under an admission, so it
was parked inside the pausable lock probe -- and the open-time race
paused that probe on every dashboard open. The result, measured
2026-09-16 on a real project: ``container probe 0.01s, gates 10.91s``
-- the snapshot fix existed and never ran, and the researcher paid ten
seconds of file-by-file reads per open.

So the snapshot is now a DECLARED read (`ftReadRepoSnapshot`,
operation ``repoSnapshot``), needing no admission. Two transports run
ONE program body (``S_REPO_SNAPSHOT_PROGRAM_CORE``): the typed read
with a flat prefixed-argument preamble, and the legacy base64-JSON
embedded command kept for adapters without the method. The tests here
hold the transports together and the refusals loud.
"""

import json
import shlex
import subprocess

import pytest

from vaibify.docker import dockerConnection
from vaibify.reproducibility import repoFiles


def _fnSeedRepo(pathRepo):
    (pathRepo / ".vaibify").mkdir()
    (pathRepo / "notes.md").write_text("alpha\n", encoding="utf-8")
    (pathRepo / "big.txt").write_text("beta\n", encoding="utf-8")
    (pathRepo / "out.csv").write_text("1,2\n", encoding="utf-8")


def _fdictRunTypedTransport(pathRepo, listContent, listSkipText,
                            listHash, listAbsolute):
    listArgs = ["r:" + str(pathRepo)]
    for sPrefix, listGroup in (
        ("c", listContent), ("k", listSkipText),
        ("h", listHash), ("a", listAbsolute),
    ):
        listArgs.extend(sPrefix + ":" + sPath for sPath in listGroup)
    sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
        dockerConnection.S_TYPED_READ_REPO_SNAPSHOT
    ].replace(
        dockerConnection._S_TYPED_READ_PATH_SLOT,
        dockerConnection._fsTypedReadPathLiteral(listArgs),
    )
    tDone = subprocess.run(
        ["python3", "-c", sProgram], capture_output=True, text=True,
    )
    assert tDone.returncode == 0, tDone.stderr
    return json.loads(tDone.stdout)


def _fdictRunEmbeddedTransport(pathRepo, listContent, listSkipText,
                               listHash, listAbsolute):
    sCore = dockerConnection.S_REPO_SNAPSHOT_PROGRAM_CORE
    sTemplate = repoFiles._S_SNAPSHOT_PAYLOAD_PREAMBLE + sCore
    sCommand = repoFiles._fsBuildEmbeddedScriptCommand(sTemplate, {
        "sRoot": str(pathRepo),
        "listContentPaths": listContent,
        "listSkipTextPaths": listSkipText,
        "listHashPaths": listHash,
        "listAbsHashPaths": listAbsolute,
    })
    tDone = subprocess.run(
        shlex.split(sCommand), capture_output=True, text=True,
    )
    assert tDone.returncode == 0, tDone.stderr
    return json.loads(tDone.stdout)


@pytest.mark.falsification
def test_the_typed_and_embedded_transports_answer_identically(tmp_path):
    """One program, two arg transports, byte-identical answers.

    The two lanes disagreeing is the class of bug this repo keeps
    finding (runner vs reproduce.sh, writer vs comparer), so parity
    is asserted over the WHOLE decoded answer, not per-field.

    Kills: dropping the "k" prefix from the typed preamble's key map,
    which silently empties the skip-text set -- the typed lane then
    carries file bodies the embedded lane withholds, and the poll
    pays the transfer the skip list exists to avoid.
    """
    _fnSeedRepo(tmp_path)
    listContent = ["notes.md", "big.txt", "absent.md"]
    listSkipText = ["big.txt"]
    listHash = ["notes.md", "big.txt", "out.csv"]
    listAbsolute = [str(tmp_path / "out.csv")]
    dictTyped = _fdictRunTypedTransport(
        tmp_path, listContent, listSkipText, listHash, listAbsolute,
    )
    dictEmbedded = _fdictRunEmbeddedTransport(
        tmp_path, listContent, listSkipText, listHash, listAbsolute,
    )
    assert dictTyped == dictEmbedded
    assert dictTyped["dictFiles"]["big.txt"]["sText"] is None, (
        "the skip-text list was not honored, so this parity run "
        "proves nothing about the mutation it exists to kill"
    )
    assert dictTyped["dictFiles"]["notes.md"]["sText"] == "alpha\n"


def test_a_hostile_filename_stays_data_in_the_typed_program(tmp_path):
    """Quotes and newlines in a path never become program syntax.

    The table-wide guard in testMutationBoundary parses the program;
    this one RUNS it, over a file whose name is hostile in shell and
    Python alike, and reads the hash back out under that exact name.
    """
    _fnSeedRepo(tmp_path)
    sHostile = "a'; import os #\" -- b"
    (tmp_path / sHostile).write_text("gamma\n", encoding="utf-8")
    dictAnswer = _fdictRunTypedTransport(
        tmp_path, [], [], [sHostile], [],
    )
    dictEntry = dictAnswer["dictHashes"][sHostile]
    assert dictEntry["sSha256"], dictEntry


def test_an_oversize_snapshot_refuses_loudly_with_counts():
    """Over the exec argument budget is a NAMED refusal, never a split.

    One snapshot is one coherent answer; the badge-probe lesson is
    that this failure's silent shape reads as a claim about every
    file it dropped. The caller's fallback is the live adapter --
    slow, correct, and logged.
    """
    listHuge = ["Data/" + ("x" * 60) + str(i) for i in range(2000)]
    with pytest.raises(ValueError) as errorInfo:
        dockerConnection.DockerConnection.ftReadRepoSnapshot(
            None, "cid-1", "/workspace/repo", [], [], listHuge, [],
        )
    sMessage = str(errorInfo.value)
    assert "2001 paths" in sMessage and "budget" in sMessage, sMessage


class _StubTypedConnection:
    """An adapter offering BOTH transports, recording which ran."""

    def __init__(self, sStdout):
        self.listTypedCalls = []
        self.listGeneralCalls = []
        self._sStdout = sStdout

    def ftReadRepoSnapshot(self, sContainerId, sRootPath,
                           listContentPaths, listSkipTextPaths,
                           listHashPaths, listAbsHashPaths):
        self.listTypedCalls.append(sRootPath)
        import collections
        tShape = collections.namedtuple(
            "tExec", "iExitCode sStdout sStderr")
        return tShape(0, self._sStdout, "")

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        self.listGeneralCalls.append(sCommand)
        raise AssertionError(
            "the snapshot fetch used the general exec primitive on "
            "an adapter that declares the typed read"
        )


@pytest.mark.falsification
def test_the_fetch_prefers_the_declared_read(tmp_path):
    """An adapter with the typed read is never asked for a general exec.

    Kills: ignoring ``ftReadRepoSnapshot`` in ``ffilesFetch`` -- the
    snapshot then rides the mutation-capable primitive again, an
    enforced lane refuses it outside an admission, and the readiness
    route is back to parking it inside the pausable probe.
    """
    sStdout = json.dumps({
        "dictFiles": {"MANIFEST.sha256": {
            "bIsFile": False, "sText": None, "iMtime": None}},
        "dictHashes": {}, "dictAbsHashes": {},
    })
    connectionStub = _StubTypedConnection(sStdout)
    filesSnapshot = repoFiles.SnapshotRepoFiles.ffilesFetch(
        connectionStub, "cid-1", str(tmp_path),
    )
    assert connectionStub.listTypedCalls == [str(tmp_path)]
    assert connectionStub.listGeneralCalls == []
    assert filesSnapshot.sRootPath == str(tmp_path)


class _StubFailingConnection:
    """A typed-read adapter whose exec fails the way daemons fail."""

    def __init__(self, iExitCode, sStdout, sStderr=""):
        import collections
        self._tResult = collections.namedtuple(
            "tExec", "iExitCode sStdout sStderr",
        )(iExitCode, sStdout, sStderr)

    def ftReadRepoSnapshot(self, *tArgs, **dictKwargs):
        return self._tResult


@pytest.mark.falsification
def test_a_failed_exec_raises_instead_of_fabricating_absence(tmp_path):
    """A nonzero exit is a failure, even when stdout looks whole.

    The failure mode this pins: the exec died AFTER printing (a
    traceback on stderr, a partial hash walk), so the payload parses
    and only the exit code says the answer is not trustworthy.
    Swallowed, it became eighteen absent-file entries presented to
    the readiness gates as fact (external review, 2026-09-16); each
    caller now owns its own degradation at its own catch.

    Kills: dropping the exit-code check -- valid-looking stdout then
    sails through, and the fabricated answer is back.
    """
    sWholeLookingPayload = json.dumps({
        "dictFiles": {"MANIFEST.sha256": {
            "bIsFile": True, "sText": None, "iMtime": 1}},
        "dictHashes": {}, "dictAbsHashes": {},
    })
    with pytest.raises(OSError) as errorInfo:
        repoFiles.SnapshotRepoFiles.ffilesFetch(
            _StubFailingConnection(1, sWholeLookingPayload, "boom"),
            "cid-1", str(tmp_path),
        )
    assert "exited 1" in str(errorInfo.value)


def test_garbage_output_raises_instead_of_fabricating_absence(tmp_path):
    """Unparseable stdout is a failure, never an all-absent answer."""
    for sGarbage in ("", "not json", "[]"):
        with pytest.raises(OSError):
            repoFiles.SnapshotRepoFiles.ffilesFetch(
                _StubFailingConnection(0, sGarbage), "cid-1",
                str(tmp_path),
            )


def test_the_readiness_seam_falls_back_to_the_live_adapter(monkeypatch):
    """The route-context promise: a failed snapshot means SLOW, not wrong."""
    from vaibify.gui import routeContext
    objLive = object()
    monkeypatch.setattr(
        routeContext, "ffilesForWorkflow",
        lambda dictCtx, sContainerId, dictWorkflow: objLive,
    )
    filesAnswer = routeContext.ffilesSnapshotForWorkflow(
        {"files": object(),
         "docker": _StubFailingConnection(1, "", "daemon fell over")},
        "cid-1", {"sProjectRepoPath": "/workspace/repo",
                  "listSteps": []},
    )
    assert filesAnswer is objLive, (
        "a failed snapshot did not fall back to the live adapter; "
        "whatever it returned instead is an answer nobody measured"
    )


@pytest.mark.falsification
def test_backslash_heavy_names_are_measured_as_rendered():
    """The budget bounds the RENDERED argument, not an estimate.

    repr() doubles every backslash, so an estimate of the raw path
    bytes admits a command roughly twice its own number -- reviewed
    with names whose estimate sat 75 bytes under the budget while
    the rendered command sat past the kernel's ceiling.

    Kills: reverting the measurement to the per-path estimate, which
    accepts this batch and hands the kernel boundary a failure that
    finding-2's fabrication would then have dressed as absent files.
    """
    listBackslashHeavy = [
        ("\\" * 40) + str(iIndex).zfill(4) for iIndex in range(1000)
    ]
    iEstimate = sum(
        len(sPath.encode("utf-8")) + 4 for sPath in listBackslashHeavy
    )
    assert iEstimate < 64 * 1024, (
        "the fixture no longer sits under the budget by estimate, so "
        "it cannot tell the two measurements apart"
    )
    with pytest.raises(ValueError) as errorInfo:
        dockerConnection.DockerConnection.ftReadRepoSnapshot(
            None, "cid-1", "/workspace/repo", [], [],
            listBackslashHeavy, [],
        )
    assert "render" in str(errorInfo.value)


@pytest.mark.falsification
def test_the_readiness_snapshot_carries_the_manifest_body(monkeypatch):
    """The seam hydrates the manifest, or the manifest gates lie.

    The snapshot omits the MANIFEST.sha256 body by design; the poll
    hydrates it through a sha-keyed cache. The readiness seam did
    NOT, and nothing noticed while the snapshot sat inside the
    paused probe -- the lane never ran. The day it started running,
    the verify pre-flight told a researcher their complete manifest
    "does not cover every declared file" and their pinned
    reproduce.sh was "not pinned" (live, 2026-09-16): every gate
    that reads the manifest text saw absence.

    Kills: dropping the hydration call from
    ``ffilesSnapshotForWorkflow`` -- reading the manifest through
    the returned snapshot then raises instead of answering, which
    the gates read as an absent manifest.
    """
    from vaibify.gui import routeContext
    sBody = "a" * 64 + "  out.csv\n"
    sPayload = json.dumps({
        "dictFiles": {"MANIFEST.sha256": {
            "bIsFile": True, "sText": None, "iMtime": 5}},
        "dictHashes": {"MANIFEST.sha256": {
            "sSha256": "b" * 64, "sSymlinkSegment": None,
            "bEscapesRoot": False}},
        "dictAbsHashes": {},
    })
    monkeypatch.setattr(
        routeContext, "ffilesForWorkflow",
        lambda dictCtx, sContainerId, dictWorkflow: object(),
    )
    monkeypatch.setattr(
        routeContext, "fsFetchManifestTextFromContainer",
        lambda dictCtx, sContainerId, sRepoRoot: sBody,
    )
    filesAnswer = routeContext.ffilesSnapshotForWorkflow(
        {"files": object(), "docker": _StubTypedConnection(sPayload)},
        "cid-1", {"sProjectRepoPath": "/workspace/repo",
                  "listSteps": []},
    )
    assert filesAnswer.fsReadText("MANIFEST.sha256") == sBody, (
        "the readiness snapshot answered without the manifest body; "
        "every manifest gate downstream reads a complete manifest "
        "as absent"
    )


class _StubDirectoryConnection:
    """Typed directory listing plus a booby-trapped general exec."""

    def __init__(self, listEntries):
        self._listEntries = listEntries
        self.listAskedDirectories = []

    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        self.listAskedDirectories.append(sDirectoryPath)
        return list(self._listEntries)

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        raise AssertionError(
            "a names-only directory listing ran the general exec "
            "primitive; in an enforced lane outside a carrier the "
            "gate refuses it, which is how Make Permanent 500d"
        )


@pytest.mark.falsification
def test_listing_json_filenames_is_a_declared_read(tmp_path):
    """Names come from the typed directory read, never a general exec.

    The promote route's candidate collection lists the project
    definitions in the bare request lane -- legal for a declared
    read, refused for the general primitive. The old implementation
    fetched every file's BODY through an embedded script to answer a
    question about names, and Make Permanent died in its own
    pre-flight (live, 2026-09-16).

    Kills: reverting ``flistListJsonFilenames`` to the
    body-fetching ``fdictReadDirJsonContents`` path.
    """
    connectionStub = _StubDirectoryConnection(
        ["b.json", "a.json", "notes.md", "c.JSON"],
    )
    filesRepo = repoFiles.ContainerRepoFiles(
        connectionStub, "cid-1", "/workspace/repo",
    )
    listNames = filesRepo.flistListJsonFilenames(".vaibify/projects")
    assert listNames == ["b.json", "a.json"], listNames
    assert connectionStub.listAskedDirectories == [
        "/workspace/repo/.vaibify/projects",
    ]


def test_listing_an_absent_directory_is_empty_not_an_error():
    """Absent directory -> [], matching the host adapter's answer."""
    class _StubAbsent:
        def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
            raise FileNotFoundError(sDirectoryPath)

    filesRepo = repoFiles.ContainerRepoFiles(
        _StubAbsent(), "cid-1", "/workspace/repo",
    )
    assert filesRepo.flistListJsonFilenames(".vaibify/projects") == []
