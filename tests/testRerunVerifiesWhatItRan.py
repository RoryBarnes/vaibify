"""Tier 5 must hash the filesystem the rerun wrote to, and only that.

Since the shadow lane there are THREE trees in play, and the whole
value of this file is that they are genuinely distinct directories on
disk:

* the researcher's host clone, which ``vaibify reproduce --repo <path>``
  names and which tiers 1-4 read;
* the live project container's repository, which the shadow is SEEDED
  from and which the rerun must no longer write to at all;
* the shadow container's copy, which the rerun writes and which the
  post-rerun re-hash must read.

A verification rooted on either of the first two after a shadow rerun
is reading a tree the rerun never touched: every entry still matches,
and the attestation certifies a reproduction that was never observed.
That is the same false-pass shape as trusting the pipeline exit code,
arrived at from another side — and the shadow lane added a second way
to arrive at it, which is why the fixture grew a third root rather than
keeping two.

Four properties are asserted here, each with the fixture built so the
property can actually fail:

1. The three trees are **distinct directories**. A test whose fake
   rerun writes into the clone cannot see the defect at all — that is
   precisely why an earlier acceptance test passed against broken
   plumbing.
2. The researcher's own container repository is **left alone**. Tier 5
   used to overwrite it, and a lane that quietly kept doing so would
   satisfy every hash assertion here while destroying the researcher's
   outputs.
3. ``MANIFEST.sha256`` is the *expected* side of the comparison, so a
   step that rewrites it mid-run must not be able to bless its own
   change. The expected hashes have to be frozen before execution.
4. A container may host several workflows. The workflow that is
   attested must be the workflow that was rerun, never whichever one
   sorts first.

The container stand-in below runs its commands for real, against real
directories, so the hashing, the manifest parse and the writes are all
genuine IO — and the shadow's creation, archive copy-in and destruction
run through the REAL ``disposableContainer`` code against a fake daemon
that materialises the tarball on disk. What none of it models is Docker
itself; the real transport is exercised in
``tests/testDisposableContainerLive.py`` and Lane 2's
``tests/testContainerAcceptance.py``.
"""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli import commandReproduce
from vaibify.docker.dockerConnection import DockerConnection


S_OUTPUT_FILENAME = "result.txt"
S_CONTAINER_ID = "rerunAcceptanceContainerId"
S_CONTAINER_NAME = "rerun-acceptance-container"


# ----------------------------------------------------------------------
# A container stand-in whose filesystem is real and separate
# ----------------------------------------------------------------------


class _ExecResult:
    """The ``ftRunInContainerStreamed`` result shape."""

    def __init__(self, iExitCode, sStdout, sStderr):
        self.iExitCode = iExitCode
        self.sStdout = sStdout
        self.sStderr = sStderr


class LocalShellContainer:
    """Runs container commands for real, on a directory that is not the clone.

    Every path handed to it is a genuine directory on this machine, but
    deliberately NOT the researcher's ``--repo`` clone. Nothing here
    answers a canned value: the hashes, the manifest reads and the file
    writes all happen. So a verification pointed at the wrong root sees
    an untouched tree and says so, which is the failure this module
    exists to surface.
    """

    def __init__(self):
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        """Return ``(iExitCode, sStdout)`` from a real shell run."""
        self.listCommands.append(sCommand)
        completed = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return completed.returncode, completed.stdout

    def ftRunInContainerStreamed(self, sContainerId, sCommand, **kwargs):
        """Return the streamed-exec result shape from a real shell run."""
        self.listCommands.append(sCommand)
        completed = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return _ExecResult(
            completed.returncode, completed.stdout, completed.stderr,
        )

    def fbaFetchFile(self, sContainerId, sPath):
        """Read real bytes back out of the container filesystem."""
        with open(sPath, "rb") as fileHandle:
            return fileHandle.read()

    def fnWriteFile(self, sContainerId, sPath, baContent, **kwargs):
        """Write real bytes into the container filesystem."""
        os.makedirs(os.path.dirname(sPath), exist_ok=True)
        with open(sPath, "wb") as fileHandle:
            fileHandle.write(baContent)

    def fbaFetchDirectoryArchive(self, sContainerId, sPath, iMaxBytes):
        """Tar a real directory, the way ``get_archive`` names members.

        Members are relative to the exported directory's PARENT, which
        is the property ``ftResolveShadowPaths`` is written against: a
        repo at ``/x/myRepo`` arrives as ``myRepo/...``. Getting that
        wrong here would make the shadow copy land one level off and
        every test below would fail for a reason unrelated to what it
        asserts, so it is worth stating.
        """
        del sContainerId
        bufferTar = io.BytesIO()
        with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
            fileTar.add(sPath, arcname=os.path.basename(sPath))
        baArchive = bufferTar.getvalue()
        if len(baArchive) > iMaxBytes:
            raise ValueError("archive exceeds the ceiling")
        return baArchive

    def fdictReadDaemonCapacity(self):
        """Answer as an unreachable daemon does: zeroes, never an error."""
        return {"iMemoryBytes": 0, "iCpuCount": 0}

    # The existence probes are typed reads, and these are the REAL
    # implementations borrowed off DockerConnection: they need only
    # ``ftRunInContainerStreamed``, which this class runs for real,
    # so the shipped program text executes against the real tree like
    # everything else here. Nothing answers a canned value.
    #
    # The worktree observation is borrowed on the same terms and it
    # matters more than the others: it is what the export's coherence
    # check compares, so a hand-written stand-in for it would let the
    # check pass by agreeing with a fiction. Borrowed, it runs the
    # shipped container program -- git enumeration and all -- against
    # the real git repository the fixture seeds.
    _ftRunTypedRead = DockerConnection._ftRunTypedRead
    fbContainerPathIsFile = DockerConnection.fbContainerPathIsFile
    fbContainerPathIsDirectory = DockerConnection.fbContainerPathIsDirectory
    fdictFetchWorktreeIdentities = (
        DockerConnection.fdictFetchWorktreeIdentities)


class FakeDisposableDaemon:
    """A daemon whose containers are real directories on this machine.

    It answers only what ``disposableContainer`` actually asks of it, so
    the create, the archive copy-in, the identity-verified destroy and
    the absence probe are all the REAL shipped code — only the daemon
    behind them is local. ``put_archive`` materialises the tarball on
    disk at the path it was given, which is what makes the shadow tree
    a genuine third directory the rerun can write to and the re-hash
    can read.
    """

    class _NotFound(Exception):
        """Stands in for ``docker.errors.NotFound``."""

    def __init__(self):
        self.listCreated = []
        self.setRemoved = set()
        self.dictLabelsById = {}
        self.api = self
        self.containers = self
        self.images = self

    # --- containers collection ---
    def create(self, sImageReference, **dictKeywords):
        sIdentifier = "shadow-" + str(len(self.listCreated))
        self.listCreated.append((sImageReference, dictKeywords))
        self.dictLabelsById[sIdentifier] = dictKeywords["labels"]
        self.dictLabelsById[dictKeywords["name"]] = dictKeywords["labels"]
        return _FakeCreatedContainer(sIdentifier, dictKeywords["name"])

    def list(self, all=False, filters=None):
        del all, filters
        return []

    # --- low-level api ---
    def put_archive(self, sContainerId, sPath, baArchive):
        del sContainerId
        with tarfile.open(fileobj=io.BytesIO(baArchive), mode="r") as fTar:
            fTar.extractall(sPath)
        return True

    def inspect_container(self, sContainerId):
        if sContainerId in self.setRemoved:
            raise self._NotFound(sContainerId)
        return {"Config": {"Labels": self.dictLabelsById.get(
            sContainerId, {})}}

    def remove_container(self, sContainerId, force=False, v=False):
        del force, v
        self.setRemoved.add(sContainerId)


class _FakeCreatedContainer:
    """What the fake daemon's ``create`` hands back."""

    def __init__(self, sIdentifier, sName):
        self.id = sIdentifier
        self.name = sName

    def start(self):
        """Accept the start the gateway performs after create."""


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _fnSeedEnvelope(pathRepo):
    """Write an L3 envelope whose tiers 1-4 all pass on the pinned bytes.

    A REAL git repository, because the export's coherence check
    enumerates with git and a bare directory would answer "not a git
    work tree" -- a refusal, correctly, but one that would make every
    test here fail for a reason unrelated to what it asserts.
    """
    pathRepo.mkdir(parents=True, exist_ok=True)
    _fnMakeGitRepository(pathRepo)
    pathOutput = pathRepo / S_OUTPUT_FILENAME
    pathOutput.write_text("answer = 42\n")
    pathDocker = pathRepo / "Dockerfile"
    pathDocker.write_text(
        "FROM python@sha256:" + "b" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )
    pathReproduce = pathRepo / "reproduce.sh"
    pathReproduce.write_text("#!/usr/bin/env bash\nset -e\n")
    pathReproduce.chmod(0o755)
    _fnWriteManifestFor(
        pathRepo, (pathOutput, pathReproduce, pathDocker),
    )
    # Pinned to a package the LOCAL python3 actually has, at the
    # version it actually has, read through the same enumeration the
    # lock-satisfaction gate runs. This harness executes real commands
    # on this machine — the machine IS the image — so a fictional
    # `click==8.1.7` pin made the gate refuse every rerun here the day
    # it landed (ruling B, 2026-09-01): a correct refusal of an
    # incoherent fixture, not a defect in the gate.
    (pathRepo / "requirements.lock").write_text(
        _fsOneTruePinnedRequirement()
        + " \\\n    --hash=sha256:" + "a" * 64 + "\n"
    )
    pathVaibify = pathRepo / ".vaibify"
    pathVaibify.mkdir(parents=True, exist_ok=True)
    (pathVaibify / "environment.json").write_text(json.dumps({
        "sImageDigest": "img@sha256:" + "c" * 64,
        "dictContainer": {"sImageDigest": "img@sha256:" + "c" * 64},
        "sSchemaVersion": "1",
    }))
    pathWorkflows = pathVaibify / "workflows"
    pathWorkflows.mkdir(parents=True, exist_ok=True)
    (pathWorkflows / "project.json").write_text(json.dumps({
        "listSteps": [{
            "sName": "GenerateSamples",
            "bRunEnabled": True,
            "saCommands": ["true"],
        }],
        "dictDeterminism": {
            # All three questions answered (2026-08-30 ruling).
            # A lone waiver used to satisfy the gate; it is now
            # one answer of three, so a fixture carrying only it
            # builds a project that is NOT L3-ready.
            "sBlasVarianceAnswer": "accepted",
            "sOmpThreadsAnswer": "unpinned",
            "sMklModeAnswer": "not-used",
        },
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
    }))
    _fnCommitEverything(pathRepo)
    return pathRepo


def _fnMakeGitRepository(pathRepo):
    """Initialise a repository, pinning the branch name explicitly.

    ``git init`` inherits ``init.defaultBranch`` from the machine, and a
    fixture that inherits it passes on a laptop defaulting to ``main``
    and behaves differently on a runner defaulting to ``master``.
    ``symbolic-ref`` works on every git version, unlike ``init -b``.
    """
    subprocess.run(["git", "init", "-q", str(pathRepo)], check=True)
    subprocess.run(
        ["git", "-C", str(pathRepo), "symbolic-ref", "HEAD",
         "refs/heads/main"], check=True)


def _fnCommitEverything(pathRepo):
    """Commit the seeded envelope so the repository has a HEAD.

    Identity is passed with ``-c`` rather than written into the repo
    config, so the fixture never depends on (or disturbs) whatever the
    developer's global git identity is.
    """
    subprocess.run(
        ["git", "-C", str(pathRepo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(pathRepo),
         "-c", "user.email=fixture@example.invalid",
         "-c", "user.name=Fixture",
         "commit", "-qm", "seed"], check=True)


def _fsOneTruePinnedRequirement():
    """Return one ``name==version`` line the local python3 will confirm.

    Read through the SAME command the lock-satisfaction gate runs in
    the shadow, against the same interpreter this harness's real
    command execution resolves, so the fixture's lock is satisfied by
    construction. Falls back to an empty-pin lock (which the gate
    deliberately skips) on a machine whose python3 cannot enumerate.
    """
    import subprocess
    processResult = subprocess.run(
        ["python3", "-m", "pip", "list", "--format=freeze",
         "--disable-pip-version-check"],
        capture_output=True, text=True,
    )
    if processResult.returncode != 0:
        return "# no exact pins on this machine"
    for sLine in processResult.stdout.splitlines():
        if "==" in sLine and "@" not in sLine:
            return sLine.strip()
    return "# no exact pins on this machine"


def _fnWriteManifestFor(pathRepo, tPaths):
    """(Re)write MANIFEST.sha256 pinning the current bytes of tPaths."""
    (pathRepo / "MANIFEST.sha256").write_text("".join(
        f"{hashlib.sha256(pathFile.read_bytes()).hexdigest()}  "
        f"{pathFile.name}\n"
        for pathFile in tPaths
    ))


@pytest.fixture(autouse=True)
def fnRepointShadowRoot(tmp_path, monkeypatch):
    """Put the shadow workspace somewhere this machine can create.

    In production ``S_SHADOW_WORKSPACE_ROOT`` is an absolute CONTAINER
    path; here the stand-in runs the rerun's commands as real shell
    commands against real directories, so it has to be a real host
    path. Autouse because forgetting it is not a test failure -- it is
    a test that reaches the developer's actual Docker daemon and tries
    to write at the filesystem root, which is how one of these tests
    was found creating containers on a live machine.
    """
    from vaibify.reproducibility import shadowRerun

    pathShadowRoot = tmp_path / "shadowRoot"
    pathShadowRoot.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        shadowRerun, "S_SHADOW_WORKSPACE_ROOT", str(pathShadowRoot))
    return pathShadowRoot


@pytest.fixture
def fixtureTwoRoots(tmp_path, monkeypatch):
    """Return ``(pathHostClone, pathContainerRepo)`` — distinct trees.

    Both hold byte-identical envelopes, so tiers 1-4 pass either way and
    the only thing separating a real verification from a fictional one
    is *which* tree it hashes after the rerun.

    A THIRD root is created here too, and it is not returned because no
    test should name it directly: the shadow's location is the shadow
    lane's own business, and a test that hard-coded it would keep
    passing after the lane stopped putting anything there. Tests reach
    it through :func:`_fpathShadowCopyOf`, which derives it exactly as
    the production resolver does.

    The shadow workspace root is repointed by ``fnRepointShadowRoot``,
    which every test in this file needs.
    """
    del monkeypatch
    pathHostClone = _fnSeedEnvelope(tmp_path / "hostClone")
    pathContainerRepo = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "ProjectRepo",
    )
    return pathHostClone, pathContainerRepo


def _fpathShadowCopyOf(pathContainerRepo):
    """Return where the shadow copy of a repository lands, as the lane says.

    Derived through the production resolver rather than re-typed, so a
    test cannot go on asserting against a location the lane abandoned --
    the constant-that-must-equal-a-derivation trap.
    """
    import pathlib

    from vaibify.reproducibility import shadowRerun

    tPaths = shadowRerun.ftResolveShadowPaths(
        str(pathContainerRepo),
        str(pathContainerRepo / ".vaibify" / "workflows" / "project.json"),
    )
    return pathlib.Path(tPaths[1])


def _fdictWorkflowFor(pathRepo, sName):
    """Return a minimal workflow rooted at a project repo path."""
    return {
        "sWorkflowName": sName,
        "sProjectRepoPath": str(pathRepo),
        "listSteps": [{
            "sName": "GenerateSamples",
            "bRunEnabled": True,
            "saCommands": ["true"],
        }],
        "dictDeterminism": {
            # All three questions answered (2026-08-30 ruling).
            # A lone waiver used to satisfy the gate; it is now
            # one answer of three, so a fixture carrying only it
            # builds a project that is NOT L3-ready.
            "sBlasVarianceAnswer": "accepted",
            "sOmpThreadsAnswer": "unpinned",
            "sMklModeAnswer": "not-used",
        },
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
        "dictRemotes": {},
    }


def _fnPatchContainerLane(
    connectionContainer, listWorkflowEntries, dictWorkflowByPath,
    fnRunSideEffect, daemonDisposable=None,
):
    """Patch every seam between the CLI and a container, leaving IO real.

    Workflow discovery and workflow loading are stubbed because they are
    not what is under test; the docker connection, the hashing, the
    manifest parse and the file writes are all genuine.
    """
    listRan = []
    daemonDisposable = daemonDisposable or FakeDisposableDaemon()

    async def _fiRunAllSteps(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        sWorkdir, fnStatusCallback, **kwargs,
    ):
        listRan.append(dictWorkflow)
        fnRunSideEffect()
        return 0

    def _fdictLoad(connectionDocker, sContainerId, sWorkflowPath=None):
        return dictWorkflowByPath[sWorkflowPath]

    listPatches = [
        patch(
            "vaibify.cli.configLoader.fconfigResolveProject",
            return_value=object(),
        ),
        patch(
            "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
            return_value=connectionContainer,
        ),
        patch(
            "vaibify.cli.commandUtilsDocker.fsRequireRunningContainer",
            return_value=S_CONTAINER_NAME,
        ),
        patch(
            "vaibify.gui.workflowManager.flistFindWorkflowsInContainer",
            return_value=listWorkflowEntries,
        ),
        patch(
            "vaibify.gui.workflowManager.fdictLoadWorkflowFromContainer",
            side_effect=_fdictLoad,
        ),
        patch(
            "vaibify.gui.pipelineRunner.fiRunAllSteps",
            side_effect=_fiRunAllSteps,
        ),
        # The shadow lane's daemon. Everything ABOVE it -- the gateway,
        # the reservation ledger, the archive repack and stamping, the
        # identity-verified destroy -- is the real shipped code; only
        # the daemon is local, and its containers are real directories.
        patch(
            "vaibify.docker.disposableContainer."
            "fdockerCreateDisposableClient",
            return_value=daemonDisposable,
        ),
        patch(
            "vaibify.docker.disposableContainer._fmoduleGetDocker",
            return_value=type("_M", (), {"errors": type(
                "_E", (), {"NotFound": FakeDisposableDaemon._NotFound})}),
        ),
    ]
    return listPatches, listRan


def _fnEnterAll(listPatches):
    """Start every patch and return a callable that stops them all."""
    for patcher in listPatches:
        patcher.start()

    def _fnStop():
        for patcher in reversed(listPatches):
            patcher.stop()
    return _fnStop


def _ftInvokeReproduce(saExtraArgs, pathRepo):
    """Run ``vaibify reproduce --rerun`` and return (result, attestation).

    Tiers 2 and 3 are skipped rather than mocked. They shell out to pip
    and ``docker pull``, and the usual way to neutralise them —
    ``patch("...commandReproduce.subprocess.run")`` — rebinds ``fnRunCommand`` on
    the shared ``subprocess`` module object, so it would also silence
    the container stand-in's real shell calls and turn every hash into
    ``None``. Skipping is honest and leaves the tiers that matter here
    (1 and 4 on the clone, 5 in the container) genuinely executed.
    """
    resultClick = CliRunner().invoke(
        commandReproduce.fnReproduceCommand,
        [
            "--repo", str(pathRepo), "--rerun",
            "--skip-tier", "2", "--skip-tier", "3",
        ] + list(saExtraArgs),
    )
    pathAttestation = pathRepo / ".vaibify" / "l3_attestation.json"
    dictAttestation = (
        json.loads(pathAttestation.read_text())
        if pathAttestation.is_file() else None
    )
    return resultClick, dictAttestation


# ----------------------------------------------------------------------
# Defect 1 — the rerun writes to the container; the check read the host
# ----------------------------------------------------------------------


def test_container_side_byte_change_fails_even_though_the_clone_is_clean(
    fixtureTwoRoots,
):
    """A zero-exit step that changes one shadow byte must fail Tier 5.

    Both of the other two trees are untouched throughout, so a
    verification rooted on either of them re-hashes three files that
    were never in the rerun's path and finds them all clean. Only a
    verification rooted on the filesystem the rerun actually wrote to
    can see the change -- and since the shadow lane, that filesystem is
    a third one, so this now falsifies two wrong roots rather than one.
    """
    pathHostClone, pathContainerRepo = fixtureTwoRoots
    sWorkflowPath = str(
        pathContainerRepo / ".vaibify" / "workflows" / "project.json"
    )
    dictWorkflow = _fdictWorkflowFor(pathContainerRepo, "Project")
    connectionContainer = LocalShellContainer()

    def _fnMutateShadowOutput():
        (_fpathShadowCopyOf(pathContainerRepo) / S_OUTPUT_FILENAME
         ).write_text("answer = 43\n")

    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer,
        [{
            "sPath": sWorkflowPath, "sName": "Project",
            "sProjectRepoPath": str(pathContainerRepo),
        }],
        {sWorkflowPath: dictWorkflow},
        _fnMutateShadowOutput,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        resultClick, dictAttestation = _ftInvokeReproduce(
            [], pathHostClone,
        )
    finally:
        fnStop()

    assert (pathHostClone / S_OUTPUT_FILENAME).read_text() == \
        "answer = 42\n", "the clone must stay untouched for this to bite"
    assert (pathContainerRepo / S_OUTPUT_FILENAME).read_text() == \
        "answer = 42\n", (
            "the rerun wrote into the RESEARCHER's own container "
            "repository; the shadow lane exists so that it does not"
        )
    assert resultClick.exit_code == 1, (
        "reproduce --rerun certified a reproduction whose shadow-side "
        "output changed; the verification hashed a tree the rerun never "
        f"touched. Output was:\n{resultClick.output}"
    )
    assert dictAttestation is not None, "no attestation was written"
    assert dictAttestation["sStatus"] == "failed"
    assert S_OUTPUT_FILENAME in dictAttestation["listDivergedHashes"], (
        "the attestation must name the shadow-side file whose hash "
        f"moved; got {dictAttestation['listDivergedHashes']!r}"
    )


def test_faithful_container_rerun_still_attests_a_pass(fixtureTwoRoots):
    """Rooting the check correctly must not turn every rerun into a fail."""
    pathHostClone, pathContainerRepo = fixtureTwoRoots
    sWorkflowPath = str(
        pathContainerRepo / ".vaibify" / "workflows" / "project.json"
    )
    connectionContainer = LocalShellContainer()
    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer,
        [{
            "sPath": sWorkflowPath, "sName": "Project",
            "sProjectRepoPath": str(pathContainerRepo),
        }],
        {sWorkflowPath: _fdictWorkflowFor(pathContainerRepo, "Project")},
        lambda: (
            _fpathShadowCopyOf(pathContainerRepo) / S_OUTPUT_FILENAME
        ).write_text("answer = 42\n"),
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        resultClick, dictAttestation = _ftInvokeReproduce(
            [], pathHostClone,
        )
    finally:
        fnStop()

    assert resultClick.exit_code == 0, resultClick.output
    assert dictAttestation["sStatus"] == "passed"
    assert dictAttestation["iOutputHashesMatched"] == 3
    assert dictAttestation["iOutputHashesTotal"] == 3


def test_attestation_names_the_manifest_it_actually_compared_against(
    fixtureTwoRoots,
):
    """The recorded digest must be the container's, not the clone's.

    Tiers 1-4 read the clone and tier 5 reads the container, so the two
    manifests can differ. Labelling the record with the clone's digest
    would name an envelope the comparison never touched — the same class
    of quiet mislabelling as attesting the wrong workflow. The clone's
    manifest here carries an extra comment line: same entries, so tier 1
    still passes, but different bytes and therefore a different digest.
    """
    from vaibify.reproducibility.l3Attestation import (
        fsCurrentManifestDigest,
    )

    pathHostClone, pathContainerRepo = fixtureTwoRoots
    pathCloneManifest = pathHostClone / "MANIFEST.sha256"
    pathCloneManifest.write_text(
        "# a clone-only annotation\n" + pathCloneManifest.read_text()
    )
    sContainerDigest = fsCurrentManifestDigest(str(pathContainerRepo))
    assert sContainerDigest != fsCurrentManifestDigest(str(pathHostClone))

    sWorkflowPath = str(
        pathContainerRepo / ".vaibify" / "workflows" / "project.json"
    )
    listPatches, _listRan = _fnPatchContainerLane(
        LocalShellContainer(),
        [{
            "sPath": sWorkflowPath, "sName": "Project",
            "sProjectRepoPath": str(pathContainerRepo),
        }],
        {sWorkflowPath: _fdictWorkflowFor(pathContainerRepo, "Project")},
        lambda: None,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        _resultClick, dictAttestation = _ftInvokeReproduce(
            [], pathHostClone,
        )
    finally:
        fnStop()

    assert dictAttestation is not None, "no attestation was written"
    assert dictAttestation["sManifestDigestAtAttestation"] == (
        sContainerDigest
    ), (
        "the attestation named a manifest the comparison never read"
    )


# ----------------------------------------------------------------------
# Defect 3 — the expected side of the comparison must be immutable
# ----------------------------------------------------------------------


def test_a_step_that_rewrites_the_manifest_cannot_bless_its_own_change(
    fixtureTwoRoots,
):
    """Re-pinning MANIFEST.sha256 during the run must fail, not pass.

    The mutation and the comparison both happen in the SHADOW here, so
    the wrong-root defect is out of the way and the only thing under
    test is *when* the expected hashes are read. A step that changes an
    output and then re-pins the manifest leaves a perfectly
    self-consistent tree: a comparison performed afterwards has nothing
    to notice.
    """
    _pathHostClone, pathRepo = fixtureTwoRoots
    sWorkflowPath = str(
        pathRepo / ".vaibify" / "workflows" / "project.json"
    )
    connectionContainer = LocalShellContainer()

    def _fnMutateAndRepin():
        pathShadow = _fpathShadowCopyOf(pathRepo)
        (pathShadow / S_OUTPUT_FILENAME).write_text("answer = 43\n")
        _fnWriteManifestFor(pathShadow, (
            pathShadow / S_OUTPUT_FILENAME,
            pathShadow / "reproduce.sh",
            pathShadow / "Dockerfile",
        ))

    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer,
        [{
            "sPath": sWorkflowPath, "sName": "Project",
            "sProjectRepoPath": str(pathRepo),
        }],
        {sWorkflowPath: _fdictWorkflowFor(pathRepo, "Project")},
        _fnMutateAndRepin,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        resultClick, dictAttestation = _ftInvokeReproduce([], pathRepo)
    finally:
        fnStop()

    assert resultClick.exit_code == 1, (
        "a rerun that re-pinned the manifest over its own changed output "
        "was certified as a reproduction; the expected hashes must be "
        f"frozen before the run. Output was:\n{resultClick.output}"
    )
    assert dictAttestation is not None, "no attestation was written"
    assert dictAttestation["sStatus"] == "failed"
    assert any(
        "MANIFEST" in sEntry
        for sEntry in dictAttestation["listDivergedHashes"]
    ), (
        "the attestation must say the manifest itself moved; got "
        f"{dictAttestation['listDivergedHashes']!r}"
    )


# ----------------------------------------------------------------------
# Defect 2 — the attested workflow must be the workflow that ran
# ----------------------------------------------------------------------


def test_cli_reruns_the_named_workflow_not_the_first_one(tmp_path):
    """With two workflows in one container, ``--workflow`` must decide.

    Running workflow A and attesting workflow B is the shape of a
    silently wrong claim: the attestation looks complete and names the
    right project while describing a run that never happened.
    """
    pathRepoFirst = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "Alpha",
    )
    pathRepoSecond = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "Beta",
    )
    sPathFirst = str(
        pathRepoFirst / ".vaibify" / "workflows" / "project.json"
    )
    sPathSecond = str(
        pathRepoSecond / ".vaibify" / "workflows" / "project.json"
    )
    listPatches, listRan = _fnPatchContainerLane(
        LocalShellContainer(),
        [
            {"sPath": sPathFirst, "sName": "Alpha",
             "sProjectRepoPath": str(pathRepoFirst)},
            {"sPath": sPathSecond, "sName": "Beta",
             "sProjectRepoPath": str(pathRepoSecond)},
        ],
        {
            sPathFirst: _fdictWorkflowFor(pathRepoFirst, "Alpha"),
            sPathSecond: _fdictWorkflowFor(pathRepoSecond, "Beta"),
        },
        lambda: None,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        resultClick, _dictAttestation = _ftInvokeReproduce(
            ["--workflow", "Beta"], pathRepoSecond,
        )
    finally:
        fnStop()

    assert len(listRan) == 1, (
        "exactly one workflow should have been rerun; "
        f"output was:\n{resultClick.output}"
    )
    assert listRan[0]["sWorkflowName"] == "Beta", (
        "the CLI reran a workflow other than the one named, so the "
        "attestation would describe a run that did not happen"
    )


def test_cli_refuses_to_guess_when_the_container_holds_two_workflows(
    tmp_path,
):
    """Ambiguity must be reported, never resolved by sort order."""
    pathRepoFirst = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "Alpha",
    )
    pathRepoSecond = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "Beta",
    )
    sPathFirst = str(
        pathRepoFirst / ".vaibify" / "workflows" / "project.json"
    )
    sPathSecond = str(
        pathRepoSecond / ".vaibify" / "workflows" / "project.json"
    )
    listPatches, listRan = _fnPatchContainerLane(
        LocalShellContainer(),
        [
            {"sPath": sPathFirst, "sName": "Alpha",
             "sProjectRepoPath": str(pathRepoFirst)},
            {"sPath": sPathSecond, "sName": "Beta",
             "sProjectRepoPath": str(pathRepoSecond)},
        ],
        {
            sPathFirst: _fdictWorkflowFor(pathRepoFirst, "Alpha"),
            sPathSecond: _fdictWorkflowFor(pathRepoSecond, "Beta"),
        },
        lambda: None,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        resultClick, _dictAttestation = _ftInvokeReproduce(
            [], pathRepoFirst,
        )
    finally:
        fnStop()

    assert listRan == [], (
        "the CLI picked a workflow out of an ambiguous container "
        f"instead of refusing; output was:\n{resultClick.output}"
    )
    assert resultClick.exit_code == 1
    assert "--workflow" in resultClick.output, (
        "the refusal must tell the researcher how to disambiguate; got:"
        f"\n{resultClick.output}"
    )


# ----------------------------------------------------------------------
# Defect 2, dashboard half — the route knows the workflow; it must use it
# ----------------------------------------------------------------------


def test_dashboard_verify_reruns_the_workflow_it_was_given(
    fixtureTwoRoots,
):
    """The route holds the active workflow and must rerun exactly that one.

    ``sProjectRepoPath`` is a container path, so the host CLI resolver
    cannot open it; routing the dashboard through that resolver means
    the rerun never happens and the attestation records a failure that
    says nothing about the workflow.

    The workflow the runner receives must be the SAME OBJECT the route
    was holding -- asserted with ``is``, because an equal copy
    rediscovered from the container would satisfy ``==`` and is exactly
    the substitution that produces a complete-looking record of a run
    that never happened. Its path, though, must have MOVED to the
    shadow: the same workflow, re-rooted, is what the shadow lane is
    for, and a path still pointing at the live container repo would mean
    the rerun was about to overwrite the researcher's outputs.
    """
    from vaibify.gui.routes.reproducibilityRoutes import (
        _fdictRunReproductionSync,
    )
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles

    _pathHostClone, pathContainerRepo = fixtureTwoRoots
    sWorkflowPath = str(
        pathContainerRepo / ".vaibify" / "workflows" / "project.json"
    )
    dictWorkflow = _fdictWorkflowFor(pathContainerRepo, "Project")
    connectionContainer = LocalShellContainer()
    filesRepo = ContainerRepoFiles(
        connectionContainer, S_CONTAINER_ID, str(pathContainerRepo),
    )
    listRan = []

    async def _fiRunAllSteps(
        connectionDocker, sContainerId, dictRunWorkflow, sRunWorkflowPath,
        sWorkdir, fnStatusCallback, **kwargs,
    ):
        listRan.append((dictRunWorkflow, sRunWorkflowPath))
        (_fpathShadowCopyOf(pathContainerRepo) / S_OUTPUT_FILENAME
         ).write_text("answer = 43\n")
        return 0

    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer, [], {}, lambda: None,
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        with patch(
            "vaibify.gui.pipelineRunner.fiRunAllSteps",
            side_effect=_fiRunAllSteps,
        ):
            dictResult = _fdictRunReproductionSync(
                connectionContainer, S_CONTAINER_ID, dictWorkflow,
                sWorkflowPath, filesRepo,
            )
    finally:
        fnStop()

    assert len(listRan) == 1, (
        "the dashboard verify did not run the workflow at all"
    )
    assert listRan[0][0] is dictWorkflow, (
        "the dashboard reran a workflow rediscovered from the container "
        "rather than the active one the route was holding"
    )
    assert listRan[0][1] == str(
        _fpathShadowCopyOf(pathContainerRepo)
        / ".vaibify" / "workflows" / "project.json"
    ), (
        "the rerun was pointed at the researcher's own container repo, "
        "not at the shadow copy"
    )
    assert dictResult["bPassed"] is False, (
        "the shadow-side output changed; the attestation must not pass"
    )
    assert S_OUTPUT_FILENAME in dictResult["listDivergedHashes"]
    assert dictResult["bShadowContainerUsed"] is True
    assert (pathContainerRepo / S_OUTPUT_FILENAME).read_text() == (
        "answer = 42\n"
    ), "the dashboard rerun wrote into the researcher's own repository"
