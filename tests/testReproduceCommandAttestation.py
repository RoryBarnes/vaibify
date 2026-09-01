"""Additional tests for ``vaibify reproduce`` covering Tier 5 attestation.

The base ``testReproduceCommand.py`` covers the cheap-tier paths and
exit-code semantics; this module focuses on the L3 attestation write
that fires when ``--rerun`` runs end-to-end, plus the helpers
``_fsRecordedImageDigest`` and ``_fiManifestEntryCount`` which the
attestation builder consumes.
"""

import hashlib
import json
import os
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli import commandReproduce
from vaibify.reproducibility.rerunVerification import (
    fdictUnrunOutcome,
    S_DIVERGENCE_PIPELINE_FAILED,
    fbRunWorkflowInContainer,
    fdictVerifyRerunOutputs,
    fiCountManifestEntriesOrZero,
)


def _fcompletedProcess(iReturnCode, sStdout="", sStderr=""):
    return subprocess.CompletedProcess(
        args=[], returncode=iReturnCode,
        stdout=sStdout, stderr=sStderr,
    )


def _fnPatchAllSubprocessesSucceeding():
    return patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        return_value=_fcompletedProcess(0),
    )


@pytest.fixture
def fixtureRepo(tmp_path):
    """Build a minimal repo passing tiers 1-3 (Tier 4 readiness extras seeded too)."""
    sBody = "answer = 42\n"
    pathFile = tmp_path / "result.txt"
    pathFile.write_text(sBody)
    sHash = hashlib.sha256(pathFile.read_bytes()).hexdigest()
    # Dockerfile pinned by digest, with SOURCE_DATE_EPOCH set.
    pathDocker = tmp_path / "Dockerfile"
    pathDocker.write_text(
        "FROM python@sha256:" + "b" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )
    sDockerHash = hashlib.sha256(pathDocker.read_bytes()).hexdigest()
    # reproduce.sh, must appear in the manifest.
    pathRepro = tmp_path / "reproduce.sh"
    pathRepro.write_text("#!/usr/bin/env bash\nset -e\n")
    pathRepro.chmod(0o755)
    sReproHash = hashlib.sha256(pathRepro.read_bytes()).hexdigest()
    # Manifest covers all three files.
    pathManifest = tmp_path / "MANIFEST.sha256"
    pathManifest.write_text(
        f"{sHash}  result.txt\n"
        f"{sReproHash}  reproduce.sh\n"
        f"{sDockerHash}  Dockerfile\n"
    )
    # requirements.lock with one hash-pinned dependency.
    (tmp_path / "requirements.lock").write_text(
        "click==8.1.7 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    # environment.json with digest-pinned image (both layouts).
    pathDotDir = tmp_path / ".vaibify"
    pathDotDir.mkdir(parents=True, exist_ok=True)
    dictEnv = {
        "sImageDigest": "img@sha256:" + "c" * 64,
        "dictContainer": {"sImageDigest": "img@sha256:" + "c" * 64},
        "sSchemaVersion": "1",
        "sTimestamp": "2026-01-01T00:00:00+00:00",
    }
    (pathDotDir / "environment.json").write_text(
        json.dumps(dictEnv, indent=2)
    )
    # workflow.json declaring determinism.
    pathWorkflows = pathDotDir / "workflows"
    pathWorkflows.mkdir(parents=True, exist_ok=True)
    (pathWorkflows / "wf.json").write_text(json.dumps({
        "listSteps": [],
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
    return tmp_path


# ============================================================================
# _fsRecordedImageDigest — lines 499, 503-504, 507
# ============================================================================


def test_recorded_image_digest_returns_empty_when_missing(tmp_path):
    """Line 499: missing environment.json returns empty string."""
    assert commandReproduce._fsRecordedImageDigest(str(tmp_path)) == ""


def test_recorded_image_digest_returns_empty_on_malformed_json(tmp_path):
    """Lines 503-504: malformed JSON returns empty string, not raise."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text("{ not json")
    assert commandReproduce._fsRecordedImageDigest(str(tmp_path)) == ""


def test_recorded_image_digest_prefers_nested_field(tmp_path):
    """Line 507: dictContainer.sImageDigest wins over flat sImageDigest."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(json.dumps({
        "dictContainer": {"sImageDigest": "nested@sha256:abc"},
        "sImageDigest": "flat@sha256:def",
    }))
    assert (
        commandReproduce._fsRecordedImageDigest(str(tmp_path))
        == "nested@sha256:abc"
    )


def test_recorded_image_digest_falls_back_to_flat_when_nested_empty(tmp_path):
    """Lines 506-510: an empty nested digest must fall back to the flat key.

    Mirrors environmentSnapshot._fsExtractImageDigest. Earlier audit
    pinned the previous (buggy) behaviour where an empty nested digest
    short-circuited without trying the flat key; that behaviour was a
    bug and has been corrected. This test now asserts the fixed
    semantics so the divergence with environmentSnapshot cannot
    silently return.
    """
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(json.dumps({
        "dictContainer": {"sImageDigest": ""},
        "sImageDigest": "flat@sha256:def",
    }))
    assert (
        commandReproduce._fsRecordedImageDigest(str(tmp_path))
        == "flat@sha256:def"
    )


def test_recorded_image_digest_agrees_with_environmentSnapshot_extractor(tmp_path):
    """Regression guard: the two helpers must agree on every layout.

    ``commandReproduce._fsRecordedImageDigest`` and
    ``environmentSnapshot._fsExtractImageDigest`` both read the same
    environment.json layouts; they previously diverged on the
    empty-nested case (see the test above). Lock them in lockstep
    here so any future drift fails this test loudly.
    """
    from vaibify.reproducibility.environmentSnapshot import (
        _fsExtractImageDigest,
    )
    listLayouts = [
        {"dictContainer": {"sImageDigest": "nested@sha256:abc"}},
        {"dictContainer": {"sImageDigest": ""},
         "sImageDigest": "flat@sha256:def"},
        {"sImageDigest": "flat@sha256:def"},
        {"dictContainer": "not-a-dict",
         "sImageDigest": "flat@sha256:def"},
        {},
    ]
    for dictLayout in listLayouts:
        pathDir = tmp_path / ".vaibify"
        pathDir.mkdir(parents=True, exist_ok=True)
        (pathDir / "environment.json").write_text(json.dumps(dictLayout))
        sFromCli = commandReproduce._fsRecordedImageDigest(str(tmp_path))
        sFromSnapshot = _fsExtractImageDigest(dictLayout)
        assert sFromCli == sFromSnapshot, (
            f"divergence on layout {dictLayout!r}: "
            f"cli={sFromCli!r} snapshot={sFromSnapshot!r}"
        )


def test_recorded_image_digest_uses_flat_when_no_nested_field(tmp_path):
    """Line 508: when dictContainer is absent, the flat sImageDigest wins."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(json.dumps({
        "sImageDigest": "flat@sha256:def",
    }))
    assert (
        commandReproduce._fsRecordedImageDigest(str(tmp_path))
        == "flat@sha256:def"
    )


def test_recorded_image_digest_handles_non_dict_container(tmp_path):
    """Line 508: dictContainer that is not a dict falls back to flat sImageDigest."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(json.dumps({
        "dictContainer": "not a dict",
        "sImageDigest": "flat@sha256:def",
    }))
    assert (
        commandReproduce._fsRecordedImageDigest(str(tmp_path))
        == "flat@sha256:def"
    )


# ============================================================================
# fiCountManifestEntriesOrZero — the defensive count both lanes share
# ============================================================================


def test_manifest_entry_count_returns_zero_on_oserror(tmp_path):
    """An OSError from the parser maps to zero."""
    with patch(
        "vaibify.reproducibility.rerunVerification.fiCountManifestEntries",
        side_effect=OSError("io"),
    ):
        assert fiCountManifestEntriesOrZero(str(tmp_path)) == 0


def test_manifest_entry_count_returns_zero_on_filenotfounderror(tmp_path):
    """A FileNotFoundError maps to zero."""
    assert fiCountManifestEntriesOrZero(str(tmp_path)) == 0


def test_manifest_entry_count_returns_zero_on_valueerror(tmp_path):
    """A ValueError from the parser maps to zero."""
    with patch(
        "vaibify.reproducibility.rerunVerification.fiCountManifestEntries",
        side_effect=ValueError("corrupt"),
    ):
        assert fiCountManifestEntriesOrZero(str(tmp_path)) == 0


# ============================================================================
# _fbWriteAttestationFromRun
# ============================================================================


def test_write_attestation_from_run_writes_passed_record(fixtureRepo):
    """A passing rerun over unchanged artefacts writes a passed record."""
    dictOutcome = fdictVerifyRerunOutputs(str(fixtureRepo), True)
    bWritten = commandReproduce._fbWriteAttestationFromRun(
        str(fixtureRepo), dictOutcome, 2.5,
    )
    assert bWritten is True
    pathAttestation = (
        fixtureRepo / ".vaibify" / "l3_attestation.json"
    )
    dictPayload = json.loads(pathAttestation.read_text())
    assert dictPayload["sStatus"] == "passed"
    assert dictPayload["listDivergedHashes"] == []
    assert dictPayload["fDurationSeconds"] == 2.5
    assert dictPayload["iOutputHashesMatched"] == 3
    assert dictPayload["iOutputHashesTotal"] == 3


def test_write_attestation_from_run_writes_failed_record(fixtureRepo):
    """A non-zero pipeline exit is named in listDivergedHashes.

    The artefacts on disk are untouched here, so the re-hash honestly
    reports 3 of 3 matching; the rerun itself is what failed, and that
    fact is the first diverged entry rather than a zeroed count.
    """
    dictOutcome = fdictVerifyRerunOutputs(str(fixtureRepo), False)
    bWritten = commandReproduce._fbWriteAttestationFromRun(
        str(fixtureRepo), dictOutcome, 1.0,
    )
    assert bWritten is True
    pathAttestation = (
        fixtureRepo / ".vaibify" / "l3_attestation.json"
    )
    dictPayload = json.loads(pathAttestation.read_text())
    assert dictPayload["sStatus"] == "failed"
    assert dictPayload["listDivergedHashes"] == [
        S_DIVERGENCE_PIPELINE_FAILED
    ]
    assert dictPayload["iOutputHashesMatched"] == 3


def test_a_rerun_that_reached_no_verdict_writes_nothing(fixtureRepo):
    """The CLI lane must make the same distinction the dashboard does.

    Both write the SAME file, so a lane that attests a refusal
    reintroduces the defect on the other lane's disk. A refused rerun
    established nothing about whether the workflow reproduces, and
    writing "failed" would also destroy an earlier passing attestation
    that the unchanged manifest still entitles the project to.
    """
    dictOutcome = fdictUnrunOutcome("step 'Sign Off' needs a researcher")
    bWritten = commandReproduce._fbWriteAttestationFromRun(
        str(fixtureRepo), dictOutcome, 2.5,
    )
    assert bWritten is False
    assert not (fixtureRepo / ".vaibify" / "l3_attestation.json").exists()


def test_a_no_verdict_run_does_not_destroy_an_earlier_pass(fixtureRepo):
    """The earlier attestation is still the project's best evidence."""
    dictPassed = fdictVerifyRerunOutputs(str(fixtureRepo), True)
    commandReproduce._fbWriteAttestationFromRun(
        str(fixtureRepo), dictPassed, 2.5,
    )
    commandReproduce._fbWriteAttestationFromRun(
        str(fixtureRepo),
        fdictUnrunOutcome("step 'Sign Off' needs a researcher"),
        0.1,
    )
    dictPayload = json.loads(
        (fixtureRepo / ".vaibify" / "l3_attestation.json").read_text(),
    )
    assert dictPayload["sStatus"] == "passed"


def test_write_attestation_from_run_handles_oserror(fixtureRepo):
    """An OSError during write surfaces as False and a warning."""
    dictOutcome = fdictVerifyRerunOutputs(str(fixtureRepo), True)
    with patch(
        "vaibify.cli.commandReproduce.fnWriteAttestation",
        side_effect=OSError("disk full"),
    ):
        bWritten = commandReproduce._fbWriteAttestationFromRun(
            str(fixtureRepo), dictOutcome, 1.0,
        )
    assert bWritten is False


# ============================================================================
# reproduce --rerun path — line 451 (success message)
# ============================================================================


def test_reproduce_rerun_passes_emits_confirmed_line(fixtureRepo):
    """Line 451: a clean rerun emits ``L3 reproduction confirmed and attested.``"""
    with _fnPatchAllSubprocessesSucceeding(), patch(
        "vaibify.cli.commandReproduce.fdictRerunAndVerify",
        return_value=fdictVerifyRerunOutputs(str(fixtureRepo), True),
    ):
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(fixtureRepo), "--rerun"],
        )
    assert result.exit_code == 0
    assert "L3 reproduction confirmed and attested." in result.output


def test_reproduce_rerun_failure_emits_failed_line(fixtureRepo):
    """Line 459: a failed rerun emits ``L3 reproduction failed; ...``"""
    with _fnPatchAllSubprocessesSucceeding(), patch(
        "vaibify.cli.commandReproduce.fdictRerunAndVerify",
        return_value=fdictVerifyRerunOutputs(str(fixtureRepo), False),
    ):
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(fixtureRepo), "--rerun"],
        )
    assert result.exit_code == 1
    assert "L3 reproduction failed" in result.output


# ============================================================================
# fbVerifyTier4 — line 336-340 (failure path), 393-402 (pipeline runner)
# ============================================================================


def test_tier4_failure_emits_per_verifier_status(fixtureRepo, tmp_path):
    """Lines 336-340: a failing tier 4 prints OK/FAIL per row."""
    # Break the Dockerfile pin so tier 4 fails.
    (fixtureRepo / "Dockerfile").write_text("FROM python:3.11\n")
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(fixtureRepo),
             "--skip-tier", "1",
             "--skip-tier", "2",
             "--skip-tier", "3"],
        )
    # Tier 4 verifiers should report FAIL for at least one row.
    assert "FAIL" in result.output
    assert "Dockerfile pinned" in result.output


# ============================================================================
# fbRunWorkflowInContainer — the "did it run again" half of tier 5.
#
# Previously asserted against the CLI's _fbInvokePipelineRunner, which
# resolved the container from a host working directory. The exit-code
# mapping it checked is unchanged; it now lives in the shared lane both
# the CLI and the dashboard enter through.
# ============================================================================


def _fnPatchRunAllStepsWithExit(iExitCode):
    """Patch the pipeline runner to report a fixed exit code."""
    async def _fiRunAllSteps(*args, **kwargs):
        return iExitCode
    return patch(
        "vaibify.gui.pipelineRunner.fiRunAllSteps",
        side_effect=_fiRunAllSteps,
    )


def test_run_workflow_in_container_zero_exit_returns_true():
    """A zero-exit pipeline means the workflow did run again."""
    with _fnPatchRunAllStepsWithExit(0):
        assert fbRunWorkflowInContainer(
            None, "ctr", {"listSteps": []}, "/workspace/Repo/wf.json",
            "/workspace/Repo",
        ) is True


def test_run_workflow_in_container_nonzero_exit_returns_false():
    """A non-zero pipeline exit means the rerun did not complete."""
    with _fnPatchRunAllStepsWithExit(2):
        assert fbRunWorkflowInContainer(
            None, "ctr", {"listSteps": []}, "/workspace/Repo/wf.json",
            "/workspace/Repo",
        ) is False


# ============================================================================
# _fdictAggregateAllWorkflows — lines 168, 174, 180
# ============================================================================


def test_aggregate_returns_none_when_workflows_dir_missing(tmp_path):
    """Line 168: a missing .vaibify/workflows dir yields None."""
    assert commandReproduce._fdictAggregateAllWorkflows(str(tmp_path)) is None


def test_aggregate_returns_none_when_all_workflows_unreadable(tmp_path):
    """Lines 174 + 180: every workflow file failing to parse yields None.

    Each malformed file returns None from _fdictLoadWorkflowFile, hitting
    the continue branch; with no steps and no determinism, the helper
    returns None.
    """
    pathDir = tmp_path / ".vaibify" / "workflows"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "broken.json").write_text("{ not json")
    (pathDir / "alsobroken.json").write_text("not json either")
    assert commandReproduce._fdictAggregateAllWorkflows(str(tmp_path)) is None


def test_report_incomplete_coverage_skips_when_no_workflows(tmp_path):
    """Line 145: an empty workflows dir short-circuits before parsing."""
    # No .vaibify/workflows directory at all.
    # _fnReportIncompleteCoverage just returns silently.
    from click.testing import CliRunner
    # Build a minimal fixture: manifest covers nothing extra.
    pathManifest = tmp_path / "MANIFEST.sha256"
    pathManifest.write_text("# empty manifest\n")
    pathLock = tmp_path / "requirements.lock"
    pathLock.write_text("click==8.1.7\n")
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(json.dumps({
        "sImageDigest": "img@sha256:" + "a" * 64,
    }))
    # Confirm there is no workflows dir present.
    assert not (pathDir / "workflows").exists()
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(tmp_path),
             "--skip-tier", "2", "--skip-tier", "3", "--skip-tier", "4"],
        )
    assert result.exit_code == 0
    assert "warning" not in result.output.lower()


def test_aggregate_returns_workflow_with_determinism_only(tmp_path):
    """Lines 182-184: a workflow contributing only dictDeterminism still surfaces."""
    pathDir = tmp_path / ".vaibify" / "workflows"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "wf.json").write_text(json.dumps({
        "listSteps": [],
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
    dictResult = commandReproduce._fdictAggregateAllWorkflows(str(tmp_path))
    assert dictResult is not None
    assert "dictDeterminism" in dictResult


# ============================================================================
# _fbRunUvFallback — lines 253-264 (uv-fallback paths)
# ============================================================================


def test_uv_fallback_success_path(tmp_path):
    """Lines 257-261: a zero-exit uv install returns True and prints pass marker."""
    pathLock = tmp_path / "requirements.lock"
    pathLock.write_text("click==8.1.7\n")
    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        return_value=_fcompletedProcess(0),
    ):
        bResult = commandReproduce._fbRunUvFallback(pathLock)
    assert bResult is True


def test_uv_fallback_failure_path(tmp_path):
    """Lines 262-264: a non-zero uv install returns False and prints fail marker."""
    pathLock = tmp_path / "requirements.lock"
    pathLock.write_text("click==8.1.7\n")
    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        return_value=_fcompletedProcess(1, sStderr="uv error"),
    ):
        bResult = commandReproduce._fbRunUvFallback(pathLock)
    assert bResult is False


def test_uv_fallback_invoked_when_pip_hash_failure(fixtureRepo):
    """Lines 225-226: a pip stderr containing 'hash' routes to uv fallback."""

    def fakeSubprocessRun(saCommand, **kwargs):
        # Identify pip-via-python first (the pip path) vs uv-direct.
        if saCommand and saCommand[0] == "uv":
            return _fcompletedProcess(0)
        if "pip" in saCommand and "install" in saCommand:
            return _fcompletedProcess(1, sStderr="hash mismatch")
        return _fcompletedProcess(0)

    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        side_effect=fakeSubprocessRun,
    ), patch(
        "vaibify.cli.commandReproduce.shutil.which",
        return_value="/usr/bin/uv",  # uv on PATH
    ):
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(fixtureRepo),
             "--skip-tier", "1",
             "--skip-tier", "3",
             "--skip-tier", "4"],
        )
    # uv fallback succeeded → exit 0.
    assert result.exit_code == 0


# ============================================================================
# _fsLoadImageDigest missing-digest exit — lines 299-303
# ============================================================================


def test_load_image_digest_exits_when_digest_missing(fixtureRepo):
    """Lines 299-303: missing sImageDigest yields exit 2 with actionable text."""
    pathEnv = fixtureRepo / ".vaibify" / "environment.json"
    dictPayload = json.loads(pathEnv.read_text())
    dictPayload.pop("sImageDigest", None)
    # Also clear the nested layout so the loader hits the missing branch.
    pathEnv.write_text(json.dumps({}))
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(fixtureRepo),
             "--skip-tier", "1", "--skip-tier", "2",
             "--skip-tier", "4"],
        )
    assert result.exit_code == 2
    assert "sImageDigest" in result.output


# ============================================================================
# Tier 2 fallback negative path — line 247-248 (no hash in stderr)
# ============================================================================


def test_pip_install_failure_without_hash_in_stderr_no_uv_fallback(fixtureRepo):
    """Lines 247-248: pip fails but stderr lacks 'hash' → no uv fallback."""

    def fakeSubprocessRun(saCommand, **kwargs):
        if "pip" in saCommand and "install" in saCommand:
            return _fcompletedProcess(1, sStderr="ImportError: not a hash issue")
        return _fcompletedProcess(0)

    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        side_effect=fakeSubprocessRun,
    ), patch(
        "vaibify.cli.commandReproduce.shutil.which",
        return_value="/usr/bin/uv",  # uv on PATH
    ):
        result = CliRunner().invoke(
            commandReproduce.fnReproduceCommand,
            ["--repo", str(fixtureRepo),
             "--skip-tier", "1", "--skip-tier", "3",
             "--skip-tier", "4"],
        )
    assert result.exit_code == 1
    # stderr should have been echoed (the "ImportError" line).
    assert "importerror" in result.output.lower() or "not a hash issue" in result.output.lower()


# ============================================================================
# init -> reproduce: the canonical Project directory must be scanned
# ============================================================================


def _fnScaffoldProjectWithInit(pathRepo):
    """Run the real ``vaibify init --template`` inside pathRepo."""
    from unittest.mock import patch

    from click.testing import CliRunner

    from vaibify.cli.commandInit import fnInitCommand

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=str(pathRepo)) as sCwd:
        # The global registry path resolves at import from ~/.vaibify,
        # so it cannot be redirected by an environment variable.
        with patch("vaibify.cli.commandInit.fnAddProject"):
            resultInit = runner.invoke(
                fnInitCommand, ["--template", "workflow"],
                catch_exceptions=False,
            )
        assert resultInit.exit_code == 0, resultInit.output
        return sCwd


def test_aggregation_sees_a_project_created_by_init(tmp_path):
    """Reproduce must read the directory init actually writes to.

    Both helpers scanned only the legacy ``.vaibify/workflows``. Once
    init started writing the canonical ``.vaibify/projects``, every
    project a researcher created was invisible here: aggregation
    returned None, so the coverage check was skipped, and the binary
    declaration verifier passed for having checked nothing. Neither
    reported that it had seen no project.
    """
    sRepo = _fnScaffoldProjectWithInit(tmp_path)

    dictAggregate = commandReproduce._fdictAggregateAllWorkflows(sRepo)

    assert dictAggregate is not None, (
        "aggregation found no Project in a repo that vaibify init just "
        "scaffolded; the coverage check silently does nothing"
    )
    assert dictAggregate["listSteps"], dictAggregate


def test_binary_declaration_is_not_vacuous_after_init(tmp_path):
    """The verifier must evaluate the scaffolded Project, not skip it."""
    sRepo = _fnScaffoldProjectWithInit(tmp_path)
    listSeen = []

    def fbRecordAndAccept(dictWorkflow):
        listSeen.append(dictWorkflow)
        return True

    with patch.object(
        commandReproduce, "fbWorkflowDeclaresBinaries", fbRecordAndAccept,
    ):
        bResult = commandReproduce._fbEveryWorkflowDeclaresBinaries(sRepo)

    assert bResult is True
    assert listSeen, (
        "no Project was evaluated, so a True here means 'nothing was "
        "checked', not 'every Project declares its binaries'"
    )
