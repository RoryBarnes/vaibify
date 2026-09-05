"""``reproduce.sh`` carries the determinism guarantees the runner does.

Until 2026-09-05 it did not. The pipeline runner prefixes every step
with ``SOURCE_DATE_EPOCH`` and a matplotlib ``svg.hashsalt`` derived
from it; the generated ``reproduce.sh`` exported neither, so every
vector figure it rendered carried the reproducer's wall clock and a
fresh per-process salt, and the script's own closing
``sha256sum -c MANIFEST.sha256`` could not pass for any workflow with a
PDF, EPS, PS, or SVG among its outputs. The dashboard's shadow rerun
was determinism-correct throughout, so the lane vaibify ran for itself
passed and the artefact it handed the world did not.

A string assertion that the preamble mentions ``SOURCE_DATE_EPOCH`` is
necessary and nowhere near sufficient -- it passes against a script
exporting the WRONG value, which is precisely the pre-existing bug in
another spelling. So the tests here drive the host half of the real
script through ``bash``, with the envelope's recorded epoch and the
repository's HEAD epoch made DISTINCT, and read what the script hands
the container. A re-derivation from HEAD cannot pass them.

The live lane then runs the whole thing against a real daemon, because
the container half -- the salt file, the entrypoint bypass -- is
invisible to any amount of host-side text inspection.
"""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from vaibify.gui.determinismEnvironment import fsBuildMatplotlibSaltShell
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
    fsRenderReproduceScript,
)


# The envelope's epoch and the repository's HEAD epoch, deliberately
# far apart and deliberately unequal: every assertion below checks for
# one and against the other, so a generator that shells out to
# ``git log -1 --format=%ct`` fails rather than coincidentally passing.
I_RECORDED_EPOCH = 1700000000
I_HEAD_EPOCH = 1750000000

S_PROBE_IMAGE = "python:3.12-slim"

_bToolingPresent = all(
    shutil.which(sTool) is not None
    for sTool in ("bash", "jq", "git")
)
_skipWithoutTooling = pytest.mark.skipif(
    not _bToolingPresent,
    reason=(
        "bash, jq and git are required to execute the host half of "
        "reproduce.sh; jq is a declared requirement of the script "
        "itself, so its absence means this host could not run the "
        "artefact under test either"
    ),
)


def _fdictBuildFigureWorkflow(listCommands=("python makeFigure.py",)):
    """Return a one-step workflow whose step renders into ``plots``."""
    return {"listSteps": [{
        "sName": "Figure",
        "sStepId": "figure",
        "sDirectory": "plots",
        "saPlotCommands": list(listCommands),
    }]}


def _fsPrepareProjectRepo(pathRepo, dictEnvelope, dictWorkflow=None):
    """Lay out a project repo and return the reproduce.sh path.

    The repository gets a real commit at :data:`I_HEAD_EPOCH` so that a
    HEAD re-derivation has something plausible to find. Nothing in the
    product is supposed to read it.
    """
    (pathRepo / ".vaibify").mkdir(parents=True, exist_ok=True)
    (pathRepo / "plots").mkdir(exist_ok=True)
    (pathRepo / ".vaibify" / "environment.json").write_text(
        json.dumps(dictEnvelope), encoding="utf-8",
    )
    (pathRepo / "requirements.lock").write_text("", encoding="utf-8")
    _fnWriteManifest(pathRepo, ["requirements.lock"])
    sScript = fsRenderReproduceScript(
        dictWorkflow or _fdictBuildFigureWorkflow(),
    )
    pathScript = pathRepo / S_REPRODUCE_SCRIPT_FILENAME
    pathScript.write_text(sScript, encoding="utf-8")
    _fnCommitAtHeadEpoch(pathRepo)
    return pathScript


def _fnWriteManifest(pathRepo, listRelativePaths):
    """Write the MANIFEST.sha256 the script's last line verifies.

    Without it the script ends on a missing-file error, and a test that
    tolerated that would be tolerating the exact failure mode this
    change exists to remove.
    """
    listLines = []
    for sRelative in listRelativePaths:
        baContent = (pathRepo / sRelative).read_bytes()
        listLines.append(
            hashlib.sha256(baContent).hexdigest() + "  " + sRelative,
        )
    (pathRepo / "MANIFEST.sha256").write_text(
        "\n".join(listLines) + "\n", encoding="utf-8",
    )


def _fbBindMountReachesTheDaemon(pathRepo):
    """Return True iff the daemon can see pathRepo through a bind mount.

    A host directory the daemon's VM does not share is mounted EMPTY
    rather than refused -- on macOS the default Lima/colima share is the
    home directory only, so a repository under the system temp directory
    arrives as an empty /work and every step fails on a missing file.
    That is a property of the developer's machine, not of the code under
    test, so it must skip with its own reason rather than red.
    """
    tResult = subprocess.run(
        [
            "docker", "run", "--rm", "--entrypoint", "ls",
            "-v", f"{pathRepo}:/work", S_PROBE_IMAGE,
            "/work/requirements.lock",
        ],
        capture_output=True, text=True,
    )
    return tResult.returncode == 0


def _fnCommitAtHeadEpoch(pathRepo):
    """Give pathRepo a HEAD whose commit epoch is I_HEAD_EPOCH."""
    dictEnvironment = dict(os.environ)
    sStamp = f"@{I_HEAD_EPOCH} +0000"
    dictEnvironment.update({
        "GIT_AUTHOR_DATE": sStamp,
        "GIT_COMMITTER_DATE": sStamp,
        "GIT_AUTHOR_NAME": "Probe",
        "GIT_AUTHOR_EMAIL": "probe@example.invalid",
        "GIT_COMMITTER_NAME": "Probe",
        "GIT_COMMITTER_EMAIL": "probe@example.invalid",
    })
    for listCommand in (
        ["git", "init", "-q", "."],
        # Pin the branch name rather than inheriting the machine's
        # init.defaultBranch, which differs between developer laptops
        # and CI runners.
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "pinned state"],
    ):
        subprocess.run(
            listCommand, cwd=str(pathRepo), env=dictEnvironment,
            check=True, capture_output=True,
        )


def _fsInstallRecordingDocker(pathBinDirectory, pathRecord):
    """Write a fake ``docker`` that records its argv and succeeds.

    It answers every subcommand with success and reads nothing from
    stdin, so the heredoc body is never executed -- this stub exists to
    observe what the HOST half decided to hand the container, which is
    where the epoch choice is made.
    """
    pathBinDirectory.mkdir(parents=True, exist_ok=True)
    pathFake = pathBinDirectory / "docker"
    pathFake.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {pathRecord}\n'
        "exit 0\n",
        encoding="utf-8",
    )
    pathFake.chmod(0o755)
    return pathFake


def _ftRunReproduceScript(pathRepo, pathBinDirectory=None):
    """Run reproduce.sh in pathRepo; return the completed process."""
    dictEnvironment = dict(os.environ)
    if pathBinDirectory is not None:
        dictEnvironment["PATH"] = (
            str(pathBinDirectory) + os.pathsep + dictEnvironment["PATH"]
        )
    return subprocess.run(
        ["bash", S_REPRODUCE_SCRIPT_FILENAME],
        cwd=str(pathRepo), env=dictEnvironment,
        capture_output=True, text=True,
    )


# ------------------------------------------------------------------
# The host half: which epoch reaches the container
# ------------------------------------------------------------------


@_skipWithoutTooling
def test_the_script_hands_the_container_the_recorded_epoch(tmp_path):
    """The epoch comes from the envelope, never from the local HEAD.

    Driven with the two made distinct because they are indistinguishable
    on the authoring machine at pin time and guaranteed to differ
    everywhere else: the commit that publishes the manifest moves HEAD,
    so a re-derived epoch dates and salts every figure differently from
    the pinned ones. Asserting the recorded value alone would pass
    against a HEAD derivation on a repository that had not moved.
    """
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    _fsPrepareProjectRepo(pathRepo, {
        "dictContainer": {"sImageDigest": S_PROBE_IMAGE},
        "iSourceDateEpoch": I_RECORDED_EPOCH,
    })
    pathRecord = tmp_path / "argv.txt"
    _fsInstallRecordingDocker(tmp_path / "bin", pathRecord)

    tResult = _ftRunReproduceScript(pathRepo, tmp_path / "bin")

    assert tResult.returncode == 0, tResult.stderr
    sRecorded = pathRecord.read_text(encoding="utf-8")
    assert f"SOURCE_DATE_EPOCH={I_RECORDED_EPOCH}" in sRecorded
    assert str(I_HEAD_EPOCH) not in sRecorded


@_skipWithoutTooling
def test_an_envelope_with_no_epoch_says_so_instead_of_using_the_clock(
    tmp_path,
):
    """An unrecorded epoch is announced, never quietly degraded.

    ``determinismEnvironment`` treats a guarantee it could not make as
    something to RECORD -- a reproduction that later fails on differing
    bytes has to be explainable. The script keeps that property: it
    runs, and it says on stderr both that the epoch is missing and what
    that costs at the closing ``sha256sum -c``.
    """
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    _fsPrepareProjectRepo(pathRepo, {
        "dictContainer": {"sImageDigest": S_PROBE_IMAGE},
    })
    pathRecord = tmp_path / "argv.txt"
    _fsInstallRecordingDocker(tmp_path / "bin", pathRecord)

    tResult = _ftRunReproduceScript(pathRepo, tmp_path / "bin")

    assert tResult.returncode == 0, tResult.stderr
    assert "iSourceDateEpoch" in tResult.stderr
    assert "sha256sum" in tResult.stderr
    assert "SOURCE_DATE_EPOCH=" in pathRecord.read_text(encoding="utf-8")
    assert str(I_HEAD_EPOCH) not in pathRecord.read_text(encoding="utf-8")


@_skipWithoutTooling
def test_a_zero_epoch_is_treated_as_unrecorded(tmp_path):
    """``0`` is the envelope's "could not determine", not an epoch.

    ``fiCaptureSourceDateEpoch`` returns 0 when git cannot answer, and
    ``fiRecordedSourceDateEpoch`` reads that back as absent. The script
    is the third reader of the same value and must agree with the other
    two, or a project whose capture failed reproduces against midnight
    on 1 January 1970 and says nothing.
    """
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    _fsPrepareProjectRepo(pathRepo, {
        "dictContainer": {"sImageDigest": S_PROBE_IMAGE},
        "iSourceDateEpoch": 0,
    })
    pathRecord = tmp_path / "argv.txt"
    _fsInstallRecordingDocker(tmp_path / "bin", pathRecord)

    tResult = _ftRunReproduceScript(pathRepo, tmp_path / "bin")

    assert tResult.returncode == 0, tResult.stderr
    assert "iSourceDateEpoch" in tResult.stderr
    assert "SOURCE_DATE_EPOCH=0" not in pathRecord.read_text(
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# Structure: one authority, and the entrypoint bypass
# ------------------------------------------------------------------


def test_the_salt_comes_from_the_runners_own_builder():
    """The reproduction respells nothing about the guarantee.

    Both lanes must pin the same rcParam, to the same value, in the
    same directory. A private copy of that shell in the generator would
    drift from the runner, and the two lanes disagreeing is the whole
    defect this change fixes -- so the exact text the runner's builder
    produces has to appear in the script.
    """
    sScript = fsRenderReproduceScript(_fdictBuildFigureWorkflow())
    assert fsBuildMatplotlibSaltShell("$SOURCE_DATE_EPOCH") in sScript


def test_the_run_bypasses_the_images_entrypoint():
    """A vaibify image's entrypoint refuses this invocation outright.

    Measured, 2026-09-05: ``docker run <vaibify image> bash -s`` exits
    255 with "could not lock config file /etc/gitconfig". The image
    declares ``USER researcher`` and its entrypoint's first phase needs
    root -- the Dockerfile says so, in as many words: invocations that
    must run the entrypoint pass ``--user 0`` explicitly. The
    reproduction wants none of that phase anyway; it configures git
    credentials, clones the project's configured repos and installs
    agent tooling, all of which reach the network and none of which a
    reproduction should depend on.
    """
    sScript = fsRenderReproduceScript(_fdictBuildFigureWorkflow())
    assert "--entrypoint bash" in sScript


# ------------------------------------------------------------------
# The live lane: what the container actually received
# ------------------------------------------------------------------


@pytest.mark.docker_live
@_skipWithoutTooling
def test_the_epoch_and_the_salt_arrive_inside_the_container(tmp_path):
    """Run the real script and read the guarantees back out.

    The host-side tests observe an argv; this one observes the
    environment and the file that argv was supposed to produce. Both
    halves have to hold for a figure to come out byte-stable, and no
    amount of text inspection can tell you the second one did.
    """
    from tests.testDockerConnectionLive import fnRequireDaemonReachable
    fnRequireDaemonReachable()

    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    _fsPrepareProjectRepo(pathRepo, {
        "dictContainer": {"sImageDigest": S_PROBE_IMAGE},
        "iSourceDateEpoch": I_RECORDED_EPOCH,
    }, _fdictBuildFigureWorkflow([
        'printf "%s\\n" "$SOURCE_DATE_EPOCH" > observed.txt',
        'cat "$MPLCONFIGDIR/matplotlibrc" >> observed.txt',
    ]))
    if not _fbBindMountReachesTheDaemon(pathRepo):
        pytest.skip(
            "the daemon cannot see " + str(pathRepo) + " through a bind "
            "mount, so /work would arrive empty; run with a temporary "
            "directory inside a shared path"
        )

    tResult = _ftRunReproduceScript(pathRepo)

    assert tResult.returncode == 0, tResult.stdout + tResult.stderr
    sObserved = (pathRepo / "plots" / "observed.txt").read_text(
        encoding="utf-8",
    )
    assert str(I_RECORDED_EPOCH) in sObserved
    assert f"svg.hashsalt: {I_RECORDED_EPOCH}" in sObserved
