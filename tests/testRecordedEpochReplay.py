"""The determinism epoch must be recorded at capture and replayed on rerun.

``SOURCE_DATE_EPOCH`` and matplotlib's ``svg.hashsalt`` derive from the
project repo's HEAD commit epoch at run time. The commit that publishes
``MANIFEST.sha256`` moves HEAD, so an epoch re-derived on the
reproducing side is guaranteed to differ from the one that salted the
pinned figures — every timestamped artefact would diverge on exactly
the workflows the envelope certifies, and no commit could ever pin
artefacts salted with its own epoch. The fix has two halves asserted
here: envelope capture records the epoch (``iSourceDateEpoch`` in
``environment.json``), and the tier 5 rerun lane exports the *recorded*
epoch instead of querying HEAD.
"""

import asyncio
import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from vaibify.gui.determinismEnvironment import (
    _fsBuildDeterminismEnvPrefix,
)
from vaibify.reproducibility import rerunVerification
from vaibify.reproducibility.environmentSnapshot import (
    fiCaptureSourceDateEpoch,
    fiRecordedSourceDateEpoch,
)


I_RECORDED_EPOCH = 1745798400


def _fnCommitRepo(pathRepo):
    """Initialise a git repo with one commit and return its HEAD epoch."""
    subprocess.run(
        ["git", "init", "-q"], cwd=pathRepo, check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=test@test.invalid",
         "-c", "user.name=Test", "commit", "-q",
         "--allow-empty", "-m", "seed"],
        cwd=pathRepo, check=True,
    )
    sEpoch = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "HEAD"],
        cwd=pathRepo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    return int(sEpoch)


def _fnWriteEnvelopeEpoch(pathRepo, valueEpoch):
    pathVaibify = pathRepo / ".vaibify"
    pathVaibify.mkdir(exist_ok=True)
    (pathVaibify / "environment.json").write_text(json.dumps({
        "iSourceDateEpoch": valueEpoch,
    }))


def test_capture_records_the_head_epoch_of_a_git_repo(tmp_path):
    iHeadEpoch = _fnCommitRepo(tmp_path)
    assert fiCaptureSourceDateEpoch(str(tmp_path)) == iHeadEpoch


def test_capture_returns_zero_outside_a_git_repo(tmp_path):
    assert fiCaptureSourceDateEpoch(str(tmp_path)) == 0


def test_recorded_epoch_reads_environment_json(tmp_path):
    _fnWriteEnvelopeEpoch(tmp_path, I_RECORDED_EPOCH)
    assert fiRecordedSourceDateEpoch(str(tmp_path)) == I_RECORDED_EPOCH


@pytest.mark.parametrize("valueBad", ["1745798400", True, -5, None, 1.5])
def test_recorded_epoch_rejects_non_positive_integers(tmp_path, valueBad):
    """A malformed record must fall back to 0, never crash the rerun."""
    _fnWriteEnvelopeEpoch(tmp_path, valueBad)
    assert fiRecordedSourceDateEpoch(str(tmp_path)) == 0


def test_recorded_epoch_is_zero_without_an_envelope(tmp_path):
    assert fiRecordedSourceDateEpoch(str(tmp_path)) == 0


@pytest.mark.falsification
def test_override_bypasses_the_head_derivation():
    """A positive override must be exported without touching git.

    ``connectionDocker`` is None, so any fallthrough to the HEAD query
    raises — the prefix can only come from the override.

    Kills: In _fsBuildDeterminismEnvPrefix, always derive iEpoch from
    _fiQueryHeadCommitEpoch, ignoring iSourceDateEpochOverride.
    """
    sPrefix = asyncio.run(_fsBuildDeterminismEnvPrefix(
        None, "container", "/workspace/repo",
        iSourceDateEpochOverride=I_RECORDED_EPOCH,
    ))
    assert f"export SOURCE_DATE_EPOCH={I_RECORDED_EPOCH} && " in sPrefix
    assert f"svg.hashsalt: {I_RECORDED_EPOCH}" in sPrefix


@pytest.mark.falsification
def test_rerun_lane_passes_the_recorded_epoch_to_the_runner(tmp_path):
    """Tier 5 must salt the rerun with the envelope's epoch, not HEAD's.

    Kills: In fdictRerunAndVerifyWorkflow, pass
    iSourceDateEpochOverride=0 instead of the value
    fiRecordedSourceDateEpoch read from the envelope.
    """
    pathOutput = tmp_path / "result.txt"
    pathOutput.write_text("answer = 42\n")
    (tmp_path / "MANIFEST.sha256").write_text(
        f"{hashlib.sha256(pathOutput.read_bytes()).hexdigest()}"
        f"  result.txt\n"
    )
    _fnWriteEnvelopeEpoch(tmp_path, I_RECORDED_EPOCH)
    dictSeen = {}

    def fbCaptureRun(*taArguments, **dictArguments):
        dictSeen.update(dictArguments)
        return True

    dictWorkflow = {"listSteps": [{
        "sName": "GenerateSamples",
        "bRunEnabled": True,
        "saCommands": ["true"],
    }]}
    with patch.object(
        rerunVerification, "fbRunWorkflowInContainer",
        side_effect=fbCaptureRun,
    ):
        dictOutcome = rerunVerification.fdictRerunAndVerifyWorkflow(
            None, "container", dictWorkflow,
            # Inside the repo, not an unrelated path: the rerun
            # refuses when the runner's resolved root differs from the
            # one the comparison reads, because steps would then write
            # where the comparison never looks.
            str(tmp_path) + "/.vaibify/projects/project.json",
            str(tmp_path),
        )
    assert dictOutcome["bPassed"] is True
    assert dictSeen["iSourceDateEpochOverride"] == I_RECORDED_EPOCH


def test_archiver_payload_records_the_epoch(tmp_path):
    """The envelope builder must write the epoch beside the digests."""
    from vaibify.reproducibility.dataArchiver import (
        _fdictBuildEnvironmentPayload,
    )
    iHeadEpoch = _fnCommitRepo(tmp_path)
    with patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureContainerImageDigest",
        return_value={"sImageDigest": ""},
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureSystemTools",
        return_value={},
    ):
        dictPayload = _fdictBuildEnvironmentPayload(
            str(tmp_path), "container", None,
        )
    assert dictPayload["iSourceDateEpoch"] == iHeadEpoch
