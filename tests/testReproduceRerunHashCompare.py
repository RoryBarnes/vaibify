"""Acceptance test: ``vaibify reproduce --rerun`` must compare hashes.

Tier 5 of ``vaibify reproduce`` advertises "rebuild and hash compare".
The adversarial case that separates a real compare from an exit-code
check is a step that *succeeds* while changing an output byte: Tier 1
runs before the rerun and therefore passes on the pre-existing bytes,
and the pipeline exits zero, so nothing except a post-rerun re-hash can
notice that the reproduction did not reproduce.

This module drives exactly that case end-to-end through the click
command and asserts on the persisted attestation, because the
attestation is the artifact a third party reads when deciding whether
to believe the L3 claim.
"""

import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli import commandReproduce


S_OUTPUT_FILENAME = "result.txt"


def _fcompletedProcess(iReturnCode, sStdout="", sStderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=iReturnCode, stdout=sStdout, stderr=sStderr,
    )


@pytest.fixture
def fixtureReadyRepo(tmp_path):
    """Build a project repo whose tiers 1-4 all pass on the pinned bytes."""
    pathOutput = tmp_path / S_OUTPUT_FILENAME
    pathOutput.write_text("answer = 42\n")
    pathDocker = tmp_path / "Dockerfile"
    pathDocker.write_text(
        "FROM python@sha256:" + "b" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )
    pathReproduce = tmp_path / "reproduce.sh"
    pathReproduce.write_text("#!/usr/bin/env bash\nset -e\n")
    pathReproduce.chmod(0o755)
    (tmp_path / "MANIFEST.sha256").write_text("".join(
        f"{hashlib.sha256(pathFile.read_bytes()).hexdigest()}  "
        f"{pathFile.name}\n"
        for pathFile in (pathOutput, pathReproduce, pathDocker)
    ))
    (tmp_path / "requirements.lock").write_text(
        "click==8.1.7 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    )
    pathVaibify = tmp_path / ".vaibify"
    pathVaibify.mkdir(parents=True, exist_ok=True)
    (pathVaibify / "environment.json").write_text(json.dumps({
        "sImageDigest": "img@sha256:" + "c" * 64,
        "dictContainer": {"sImageDigest": "img@sha256:" + "c" * 64},
        "sSchemaVersion": "1",
    }))
    pathWorkflows = pathVaibify / "workflows"
    pathWorkflows.mkdir(parents=True, exist_ok=True)
    (pathWorkflows / "wf.json").write_text(json.dumps({
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
    }))
    return tmp_path


def _fdictInvokeRerunWithDivergentStep(pathRepo, sNewContent):
    """Run ``reproduce --rerun`` where the step succeeds but changes a byte.

    Returns ``(clickResult, dictAttestation)``. The fake rerun writes
    ``sNewContent`` over the pinned output and reports success, which is
    precisely the state an exit-code-only Tier 5 cannot distinguish from
    a faithful reproduction.
    """

    def fbRerunMutatingOutput(sProjectRepo):
        (pathRepo / S_OUTPUT_FILENAME).write_text(sNewContent)
        return True

    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        return_value=_fcompletedProcess(0),
    ), patch(
        "vaibify.cli.commandReproduce.fbRerunWorkflow",
        side_effect=fbRerunMutatingOutput,
    ):
        resultClick = CliRunner().invoke(
            commandReproduce.reproduce,
            ["--repo", str(pathRepo), "--rerun"],
        )
    pathAttestation = pathRepo / ".vaibify" / "l3_attestation.json"
    dictAttestation = (
        json.loads(pathAttestation.read_text())
        if pathAttestation.is_file() else None
    )
    return resultClick, dictAttestation


def test_zero_exit_rerun_that_changes_an_output_byte_fails(fixtureReadyRepo):
    """A successful step that changes one output byte must fail Tier 5."""
    resultClick, _dictAttestation = _fdictInvokeRerunWithDivergentStep(
        fixtureReadyRepo, "answer = 43\n",
    )
    assert resultClick.exit_code == 1, (
        "reproduce --rerun certified a reproduction whose output bytes "
        f"changed; output was:\n{resultClick.output}"
    )
    assert "L3 reproduction failed" in resultClick.output


def test_divergent_rerun_attestation_names_the_diverged_file(
    fixtureReadyRepo,
):
    """The attestation must record the diverged path, not an exit code."""
    _resultClick, dictAttestation = _fdictInvokeRerunWithDivergentStep(
        fixtureReadyRepo, "answer = 43\n",
    )
    assert dictAttestation is not None, "no attestation was written"
    assert dictAttestation["sStatus"] == "failed"
    assert S_OUTPUT_FILENAME in dictAttestation["listDivergedHashes"], (
        "listDivergedHashes must name the file whose hash diverged; got "
        f"{dictAttestation['listDivergedHashes']!r}"
    )


def test_divergent_rerun_attestation_counts_matched_below_total(
    fixtureReadyRepo,
):
    """Two of the three pinned artefacts still match; the record says so."""
    _resultClick, dictAttestation = _fdictInvokeRerunWithDivergentStep(
        fixtureReadyRepo, "answer = 43\n",
    )
    assert dictAttestation is not None, "no attestation was written"
    assert dictAttestation["iOutputHashesTotal"] == 3
    assert dictAttestation["iOutputHashesMatched"] == 2, (
        "matched must be derived from the re-hash, not asserted from "
        "the pipeline exit code"
    )


def test_faithful_rerun_still_attests_pass(fixtureReadyRepo):
    """An unchanged rerun keeps attesting a pass — the fix is not a veto."""
    resultClick, dictAttestation = _fdictInvokeRerunWithDivergentStep(
        fixtureReadyRepo, "answer = 42\n",
    )
    assert resultClick.exit_code == 0, resultClick.output
    assert dictAttestation["sStatus"] == "passed"
    assert dictAttestation["listDivergedHashes"] == []
    assert dictAttestation["iOutputHashesMatched"] == 3
    assert dictAttestation["iOutputHashesTotal"] == 3
