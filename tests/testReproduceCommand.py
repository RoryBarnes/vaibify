"""Tests for the `vaibify reproduce` CLI subcommand."""

import hashlib
import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli.commandReproduce import reproduce


_S_FIXTURE_FILE_NAME = "result.txt"
_S_FIXTURE_FILE_BODY = "answer = 42\n"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _fnSeedFixtureFile(pathRepo, sName=_S_FIXTURE_FILE_NAME):
    """Write a deterministic small file inside the fixture repo."""
    pathFile = pathRepo / sName
    pathFile.write_text(_S_FIXTURE_FILE_BODY)
    return pathFile


def _fsHashFile(pathFile):
    """Return the SHA-256 hex digest of a file's bytes."""
    return hashlib.sha256(pathFile.read_bytes()).hexdigest()


def _fnWriteManifest(pathRepo, dictPathToHash):
    """Write a BSD-format SHA-256 manifest covering the given paths."""
    pathManifest = pathRepo / "MANIFEST.sha256"
    listLines = ["# SHA-256 manifest of workflow outputs\n"]
    for sRelative, sHash in sorted(dictPathToHash.items()):
        listLines.append(f"{sHash}  {sRelative}\n")
    pathManifest.write_text("".join(listLines))


def _fnWriteLockfile(pathRepo, sBody=None):
    """Write a minimal hash-pinned requirements.lock for tests."""
    if sBody is None:
        sBody = (
            "click==8.1.7 \\\n"
            "    --hash=sha256:"
            "ae74fb96c20a0277a1d615f1e4d73c8414f5a98db8b799a7931d1582f3390c28\n"
        )
    (pathRepo / "requirements.lock").write_text(sBody)


def _fnWriteEnvironment(pathRepo, sImageDigest):
    """Write a .vaibify/environment.json carrying the supplied digest."""
    pathDotDir = pathRepo / ".vaibify"
    pathDotDir.mkdir(parents=True, exist_ok=True)
    dictPayload = {
        "sImageDigest": sImageDigest,
        "sSchemaVersion": "1",
        "sTimestamp": "2026-01-01T00:00:00+00:00",
    }
    (pathDotDir / "environment.json").write_text(
        json.dumps(dictPayload, indent=2, sort_keys=True)
    )


@pytest.fixture
def fixtureRepo(tmp_path):
    """Build a fixture project repo with a manifest, lockfile, and env JSON."""
    pathFile = _fnSeedFixtureFile(tmp_path)
    _fnWriteManifest(tmp_path, {_S_FIXTURE_FILE_NAME: _fsHashFile(pathFile)})
    _fnWriteLockfile(tmp_path)
    _fnWriteEnvironment(tmp_path, "registry/example@sha256:" + "a" * 64)
    return tmp_path


def _fnPatchAllSubprocessesSucceeding():
    """Patch subprocess.run inside commandReproduce to succeed by default."""
    classDone = subprocess.CompletedProcess
    return patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        return_value=classDone(args=[], returncode=0, stdout="", stderr=""),
    )


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_reproduce_happy_path_exit_zero(fixtureRepo):
    """All three tiers pass with mocks; exit 0 and success line printed."""
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert result.exit_code == 0, result.output
    assert "L3 reproduction confirmed." in result.output
    assert "[1/4]" in result.output and "OK" in result.output
    assert "[2/4]" in result.output
    assert "[3/4]" in result.output


# ----------------------------------------------------------------------
# Tier 1 - manifest mismatch
# ----------------------------------------------------------------------


def test_reproduce_tier1_mismatch_exit_one(fixtureRepo):
    """A modified output triggers exit 1 with the mismatch line printed."""
    (fixtureRepo / _S_FIXTURE_FILE_NAME).write_text("tampered\n")
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert result.exit_code == 1
    assert _S_FIXTURE_FILE_NAME in result.output
    assert "FAIL" in result.output


# ----------------------------------------------------------------------
# Missing manifest -> usage error
# ----------------------------------------------------------------------


def test_reproduce_missing_manifest_exit_two(fixtureRepo):
    """A missing MANIFEST.sha256 yields an actionable usage error and exit 2."""
    os.remove(fixtureRepo / "MANIFEST.sha256")
    result = CliRunner().invoke(reproduce, ["--repo", str(fixtureRepo)])
    assert result.exit_code == 2
    assert "MANIFEST.sha256" in result.output


# ----------------------------------------------------------------------
# Tier 2 - install failure
# ----------------------------------------------------------------------


def _fcompletedProcess(iReturnCode, sStdout="", sStderr=""):
    """Return a fresh CompletedProcess used to script subprocess.run mocks."""
    return subprocess.CompletedProcess(
        args=[], returncode=iReturnCode, stdout=sStdout, stderr=sStderr,
    )


def test_reproduce_tier2_install_failure_exit_one(fixtureRepo):
    """A non-zero pip install exit propagates as overall exit 1."""

    def fakeSubprocessRun(saCommand, **kwargs):
        if "pip" in saCommand and "install" in saCommand:
            return _fcompletedProcess(1, sStderr="pip install error")
        return _fcompletedProcess(0)

    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        side_effect=fakeSubprocessRun,
    ), patch(
        "vaibify.cli.commandReproduce.shutil.which", return_value=None,
    ):
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert result.exit_code == 1
    assert "pip install" in result.output.lower()


# ----------------------------------------------------------------------
# Missing lockfile when tier 2 selected
# ----------------------------------------------------------------------


def test_reproduce_missing_lockfile_exit_two(fixtureRepo):
    """Missing requirements.lock with tier 2 active yields exit 2."""
    os.remove(fixtureRepo / "requirements.lock")
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert result.exit_code == 2
    assert "requirements.lock" in result.output


# ----------------------------------------------------------------------
# Tier 3 - missing environment.json
# ----------------------------------------------------------------------


def test_reproduce_missing_environment_json_exit_two(fixtureRepo):
    """Missing .vaibify/environment.json yields an actionable exit 2."""
    os.remove(fixtureRepo / ".vaibify" / "environment.json")
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert result.exit_code == 2
    assert "environment.json" in result.output


# ----------------------------------------------------------------------
# Tier 3 - docker pull failure
# ----------------------------------------------------------------------


def test_reproduce_docker_pull_failure_exit_one(fixtureRepo):
    """A failing docker pull surfaces as overall exit 1."""

    def fakeSubprocessRun(saCommand, **kwargs):
        if "docker" in saCommand and "pull" in saCommand:
            return _fcompletedProcess(1, sStderr="manifest unknown")
        return _fcompletedProcess(0)

    with patch(
        "vaibify.cli.commandReproduce.subprocess.run",
        side_effect=fakeSubprocessRun,
    ):
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert result.exit_code == 1
    assert "docker pull" in result.output.lower()


# ----------------------------------------------------------------------
# Skip every tier
# ----------------------------------------------------------------------


def test_reproduce_all_tiers_skipped_exit_zero(fixtureRepo):
    """Skipping all three tiers exits 0 and prints the step-4 placeholder."""
    result = CliRunner().invoke(
        reproduce, [
            "--repo", str(fixtureRepo),
            "--skip-tier", "1",
            "--skip-tier", "2",
            "--skip-tier", "3",
        ],
    )
    assert result.exit_code == 0
    assert "[1/4] skipped" in result.output
    assert "[2/4] skipped" in result.output
    assert "[3/4] skipped" in result.output
    assert "[4/4]" in result.output


# ----------------------------------------------------------------------
# Behavioral parity with sha256sum
# ----------------------------------------------------------------------


def _fsResolveSha256SumBinary():
    """Return the platform's BSD-format sha256 verifier or None if missing."""
    if shutil.which("sha256sum") is not None:
        return "sha256sum"
    if shutil.which("shasum") is not None:
        return "shasum"
    return None


def _fbInvokeSha256SumCheck(pathRepo, sBinary):
    """Run the platform sha256 verifier in BSD-check mode against the manifest."""
    if sBinary == "sha256sum":
        saCommand = [sBinary, "-c", "MANIFEST.sha256"]
    else:
        saCommand = [sBinary, "-a", "256", "-c", "MANIFEST.sha256"]
    completed = subprocess.run(
        saCommand, cwd=str(pathRepo),
        capture_output=True, text=True,
    )
    return completed.returncode == 0


def test_reproduce_tier1_matches_sha256sum_check(fixtureRepo):
    """Tier 1 outcome must agree with the system's sha256 verifier on parity."""
    sBinary = _fsResolveSha256SumBinary()
    if sBinary is None:
        pytest.skip("no sha256sum/shasum available on PATH")
    bShasumOk = _fbInvokeSha256SumCheck(fixtureRepo, sBinary)
    with _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            reproduce, [
                "--repo", str(fixtureRepo),
                "--skip-tier", "2", "--skip-tier", "3",
            ],
        )
    bReproduceOk = (result.exit_code == 0)
    assert bShasumOk == bReproduceOk
    # Now mutate a file and confirm both detect the mismatch.
    (fixtureRepo / _S_FIXTURE_FILE_NAME).write_text("changed\n")
    bShasumOkAfter = _fbInvokeSha256SumCheck(fixtureRepo, sBinary)
    with _fnPatchAllSubprocessesSucceeding():
        resultAfter = CliRunner().invoke(
            reproduce, [
                "--repo", str(fixtureRepo),
                "--skip-tier", "2", "--skip-tier", "3",
            ],
        )
    bReproduceOkAfter = (resultAfter.exit_code == 0)
    assert bShasumOkAfter == bReproduceOkAfter
    assert bReproduceOkAfter is False


# ----------------------------------------------------------------------
# Idempotence
# ----------------------------------------------------------------------


def _fsScrubVariableLines(sOutput):
    """Return reproduce output with absolute-path lines collapsed."""
    listScrubbed = []
    for sLine in sOutput.splitlines():
        if "OK" in sLine and "Pulling" in sLine:
            listScrubbed.append("[3/4] image-line")
            continue
        listScrubbed.append(sLine)
    return "\n".join(listScrubbed)


def test_reproduce_is_idempotent(fixtureRepo):
    """Running reproduce twice yields identical exit codes and stable output."""
    with _fnPatchAllSubprocessesSucceeding():
        resultFirst = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
        resultSecond = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo)],
        )
    assert resultFirst.exit_code == resultSecond.exit_code == 0
    assert _fsScrubVariableLines(resultFirst.output) == \
        _fsScrubVariableLines(resultSecond.output)


# ----------------------------------------------------------------------
# Registration on the top-level CLI
# ----------------------------------------------------------------------


def test_reproduce_command_registered_on_main_cli():
    """`vaibify reproduce --help` is reachable through the main Click group."""
    from vaibify.cli.main import main
    result = CliRunner().invoke(main, ["reproduce", "--help"])
    assert result.exit_code == 0
    assert "Verify" in result.output


# ----------------------------------------------------------------------
# Rerun handles SystemExit from registry resolution gracefully (Fix C2)
# ----------------------------------------------------------------------


def test_rerun_resolves_project_from_repo_not_cwd(fixtureRepo, tmp_path):
    """``--repo`` must drive project resolution even when cwd differs."""
    from vaibify.cli import commandReproduce

    pathOtherCwd = tmp_path / "elsewhere"
    pathOtherCwd.mkdir()
    sOriginalCwd = os.getcwd()
    listSeenCwd = []

    def _fnRecordCwd(sProjectName=None):
        listSeenCwd.append(os.getcwd())
        raise SystemExit(1)

    try:
        os.chdir(str(pathOtherCwd))
        with patch(
            "vaibify.cli.configLoader.fconfigResolveProject",
            side_effect=_fnRecordCwd,
        ):
            commandReproduce.fbRerunWorkflow(str(fixtureRepo))
    finally:
        os.chdir(sOriginalCwd)

    assert len(listSeenCwd) == 1
    assert os.path.realpath(listSeenCwd[0]) == \
        os.path.realpath(str(fixtureRepo))


def test_rerun_restores_cwd_after_failure(fixtureRepo, tmp_path):
    """An exception inside resolution must not leak the chdir."""
    from vaibify.cli import commandReproduce

    pathOtherCwd = tmp_path / "elsewhere"
    pathOtherCwd.mkdir()
    sOriginalCwd = os.getcwd()

    def _fnRaise(sProjectName=None):
        raise RuntimeError("boom")

    try:
        os.chdir(str(pathOtherCwd))
        sCwdBeforeCall = os.getcwd()
        with patch(
            "vaibify.cli.configLoader.fconfigResolveProject",
            side_effect=_fnRaise,
        ):
            commandReproduce.fbRerunWorkflow(str(fixtureRepo))
        assert os.getcwd() == sCwdBeforeCall
    finally:
        os.chdir(sOriginalCwd)


def test_rerun_handles_unregistered_project_gracefully(fixtureRepo):
    """SystemExit from fconfigResolveProject is caught; reproduce exits 1."""
    from vaibify.cli import commandReproduce

    def _fnRaiseSystemExit(*args, **kwargs):
        raise SystemExit(1)

    with patch(
        "vaibify.cli.configLoader.fconfigResolveProject",
        side_effect=_fnRaiseSystemExit,
    ), _fnPatchAllSubprocessesSucceeding():
        bResult = commandReproduce.fbRerunWorkflow(str(fixtureRepo))
    assert bResult is False

    with patch(
        "vaibify.cli.configLoader.fconfigResolveProject",
        side_effect=_fnRaiseSystemExit,
    ), _fnPatchAllSubprocessesSucceeding():
        result = CliRunner().invoke(
            reproduce, ["--repo", str(fixtureRepo), "--rerun"],
        )
    assert result.exit_code == 1
    assert "failed to invoke pipeline runner" in result.output
