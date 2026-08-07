"""Tier 5 must hash the filesystem the rerun wrote to, and only that.

The rerun executes inside a container whose ``/workspace`` is a
Docker-managed named volume. ``vaibify reproduce --repo <path>`` names a
*host* directory. Those are different filesystems, so a verification
that re-hashes the host clone after a container rerun is reading a tree
the rerun never touched: every entry still matches, and the attestation
certifies a reproduction that was never observed. That is the same
false-pass shape as trusting the pipeline exit code, arrived at from the
other side.

Three properties are asserted here, each with the fixture built so the
property can actually fail:

1. The container filesystem and the researcher's clone are **distinct
   directories**. A test whose fake rerun writes into the clone cannot
   see the defect at all — that is precisely why the previous
   acceptance test passed against broken plumbing.
2. ``MANIFEST.sha256`` is the *expected* side of the comparison, so a
   step that rewrites it mid-run must not be able to bless its own
   change. The expected hashes have to be frozen before execution.
3. A container may host several workflows. The workflow that is
   attested must be the workflow that was rerun, never whichever one
   sorts first.

The container stand-in below runs its commands for real, against a real
directory, so the hashing, the manifest parse and the writes are all
genuine IO. What it does not model is Docker itself; the real transport
is Lane 2's job (``tests/testContainerAcceptance.py``).
"""

import hashlib
import json
import os
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli import commandReproduce


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


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _fnSeedEnvelope(pathRepo):
    """Write an L3 envelope whose tiers 1-4 all pass on the pinned bytes."""
    pathRepo.mkdir(parents=True, exist_ok=True)
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
    (pathRepo / "requirements.lock").write_text(
        "click==8.1.7 \\\n    --hash=sha256:" + "a" * 64 + "\n"
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
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
    }))
    return pathRepo


def _fnWriteManifestFor(pathRepo, tPaths):
    """(Re)write MANIFEST.sha256 pinning the current bytes of tPaths."""
    (pathRepo / "MANIFEST.sha256").write_text("".join(
        f"{hashlib.sha256(pathFile.read_bytes()).hexdigest()}  "
        f"{pathFile.name}\n"
        for pathFile in tPaths
    ))


@pytest.fixture
def fixtureTwoRoots(tmp_path):
    """Return ``(pathHostClone, pathContainerRepo)`` — two distinct trees.

    Both hold byte-identical envelopes, so tiers 1-4 pass either way and
    the only thing separating a real verification from a fictional one
    is *which* tree it hashes after the rerun.
    """
    pathHostClone = _fnSeedEnvelope(tmp_path / "hostClone")
    pathContainerRepo = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "ProjectRepo",
    )
    return pathHostClone, pathContainerRepo


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
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
        "dictRemotes": {},
    }


def _fnPatchContainerLane(
    connectionContainer, listWorkflowEntries, dictWorkflowByPath,
    fnRunSideEffect,
):
    """Patch every seam between the CLI and a container, leaving IO real.

    Workflow discovery and workflow loading are stubbed because they are
    not what is under test; the docker connection, the hashing, the
    manifest parse and the file writes are all genuine.
    """
    listRan = []

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
    """A zero-exit step that changes one container byte must fail Tier 5.

    The host clone is untouched throughout, so a verification rooted
    there re-hashes three files that were never in the rerun's path and
    finds them all clean. Only a verification rooted on the filesystem
    the rerun actually wrote to can see the change.
    """
    pathHostClone, pathContainerRepo = fixtureTwoRoots
    sWorkflowPath = str(
        pathContainerRepo / ".vaibify" / "workflows" / "project.json"
    )
    dictWorkflow = _fdictWorkflowFor(pathContainerRepo, "Project")
    connectionContainer = LocalShellContainer()

    def _fnMutateContainerOutput():
        (pathContainerRepo / S_OUTPUT_FILENAME).write_text("answer = 43\n")

    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer,
        [{
            "sPath": sWorkflowPath, "sName": "Project",
            "sProjectRepoPath": str(pathContainerRepo),
        }],
        {sWorkflowPath: dictWorkflow},
        _fnMutateContainerOutput,
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
    assert resultClick.exit_code == 1, (
        "reproduce --rerun certified a reproduction whose container-side "
        "output changed; the verification hashed the host clone the "
        f"rerun never touched. Output was:\n{resultClick.output}"
    )
    assert dictAttestation is not None, "no attestation was written"
    assert dictAttestation["sStatus"] == "failed"
    assert S_OUTPUT_FILENAME in dictAttestation["listDivergedHashes"], (
        "the attestation must name the container-side file whose hash "
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
        lambda: (pathContainerRepo / S_OUTPUT_FILENAME).write_text(
            "answer = 42\n",
        ),
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

    The clone and the container repo are the same directory here, so the
    wrong-root defect is out of the way and the only thing under test is
    *when* the expected hashes are read. A step that changes an output
    and then re-pins the manifest leaves a perfectly self-consistent
    tree: a comparison performed afterwards has nothing to notice.
    """
    _pathHostClone, pathRepo = fixtureTwoRoots
    sWorkflowPath = str(
        pathRepo / ".vaibify" / "workflows" / "project.json"
    )
    connectionContainer = LocalShellContainer()

    def _fnMutateAndRepin():
        (pathRepo / S_OUTPUT_FILENAME).write_text("answer = 43\n")
        _fnWriteManifestFor(pathRepo, (
            pathRepo / S_OUTPUT_FILENAME,
            pathRepo / "reproduce.sh",
            pathRepo / "Dockerfile",
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
        (pathContainerRepo / S_OUTPUT_FILENAME).write_text("answer = 43\n")
        return 0

    with patch(
        "vaibify.gui.pipelineRunner.fiRunAllSteps",
        side_effect=_fiRunAllSteps,
    ):
        dictResult = _fdictRunReproductionSync(
            connectionContainer, S_CONTAINER_ID, dictWorkflow,
            sWorkflowPath, filesRepo,
        )

    assert len(listRan) == 1, (
        "the dashboard verify did not run the workflow at all"
    )
    assert listRan[0][0] is dictWorkflow, (
        "the dashboard reran a workflow rediscovered from the container "
        "rather than the active one the route was holding"
    )
    assert listRan[0][1] == sWorkflowPath
    assert dictResult["bPassed"] is False, (
        "the container-side output changed; the attestation must not pass"
    )
    assert S_OUTPUT_FILENAME in dictResult["listDivergedHashes"]
