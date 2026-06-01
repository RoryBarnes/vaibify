"""Tests for the readiness-marker and structured-warning helpers in
docker/entrypoint.sh.

These tests exercise the bash helpers by sourcing the script in a
subshell with WORKSPACE pointed at a temporary directory, so the
guarded main block at the bottom of the script does not execute.
"""

import json
import os
import subprocess

import pytest


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
    )
)


def _fsRunHelperScript(sWorkspace, sBody):
    """Source entrypoint.sh in a subshell and run sBody."""
    sScript = (
        "set +e\n"
        "WORKSPACE=" + sWorkspace + "\n"
        "export WORKSPACE\n"
        "source " + _S_ENTRYPOINT + "\n"
        + sBody
    )
    resultProc = subprocess.run(
        ["bash", "-c", sScript],
        capture_output=True, text=True,
    )
    return resultProc


def _fdictReadMarker(sWorkspace):
    """Read and parse the readiness marker from the temp workspace."""
    sPath = os.path.join(sWorkspace, ".vaibify", ".entrypoint_ready")
    with open(sPath) as fileHandle:
        return json.loads(fileHandle.read())


def test_write_readiness_marker_ok(tmp_path):
    """fnWriteReadinessMarker emits structured JSON for the success case."""
    sWorkspace = str(tmp_path)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sBody = 'fnWriteReadinessMarker "ok" ""\n'
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "ok"
    assert dictMarker["sReason"] == ""
    assert dictMarker["saWarnings"] == []
    assert dictMarker["sEntrypointVersion"]


def test_write_readiness_marker_includes_version(tmp_path):
    """The marker must carry sEntrypointVersion so the host can detect drift."""
    sWorkspace = str(tmp_path)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sBody = 'fnWriteReadinessMarker "ok" ""\n'
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert "sEntrypointVersion" in dictMarker
    assert dictMarker["sEntrypointVersion"]


def test_write_readiness_marker_failed_with_reason(tmp_path):
    """fnWriteReadinessMarker captures the failure reason verbatim."""
    sWorkspace = str(tmp_path)
    sBody = 'fnWriteReadinessMarker "failed" "binary build crashed"\n'
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "failed"
    assert dictMarker["sReason"] == "binary build crashed"


def test_write_readiness_marker_includes_warnings(tmp_path):
    """saStartupWarnings must surface in the marker JSON array."""
    sWorkspace = str(tmp_path)
    sBody = (
        'fnAppendStartupWarning "vplanet" "pip-install" "wheel missing"\n'
        'fnAppendStartupWarning "vplot" "c-build" "make opt failed"\n'
        'fnWriteReadinessMarker "ok" ""\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "ok"
    assert len(dictMarker["saWarnings"]) == 2
    assert "vplanet: pip-install: wheel missing" in dictMarker["saWarnings"]
    assert "vplot: c-build: make opt failed" in dictMarker["saWarnings"]


def test_write_readiness_marker_booting_carries_current_version(tmp_path):
    """A ``"booting"`` marker must include the current
    ``sEntrypointVersion`` so that the host probe — which fires during
    workspace boot — does not surface a version-mismatch warning
    against a stale marker left behind in the persistent /workspace
    volume from a previous container session.
    """
    sWorkspace = str(tmp_path)
    sBody = 'fnWriteReadinessMarker "booting" "container initializing"\n'
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "booting"
    assert dictMarker["sReason"] == "container initializing"
    assert dictMarker["sEntrypointVersion"]
    sExpected = _fsExtractEntrypointVersionConstant()
    assert dictMarker["sEntrypointVersion"] == sExpected


def _fsExtractEntrypointVersionConstant():
    """Read the S_ENTRYPOINT_VERSION constant declared in entrypoint.sh."""
    with open(_S_ENTRYPOINT, "r", encoding="utf-8") as fileHandle:
        sSource = fileHandle.read()
    for sLine in sSource.splitlines():
        sStripped = sLine.strip()
        if sStripped.startswith("S_ENTRYPOINT_VERSION="):
            return sStripped.split("=", 1)[1].strip('"')
    raise AssertionError(
        "entrypoint.sh missing S_ENTRYPOINT_VERSION constant",
    )


def test_workspace_phase_writes_booting_marker_before_long_steps():
    """The workspace phase must claim the marker file before any
    long-running step. /workspace is a persistent named volume — a
    stale "ok" marker from a prior container session survives the
    rebuild and is read by the host probe during boot, surfacing a
    bogus version-mismatch warning for the multi-minute window
    before the new "ok" marker overwrites it."""
    with open(_S_ENTRYPOINT, "r", encoding="utf-8") as fileHandle:
        sSource = fileHandle.read()
    iStart = sSource.find("fnRunWorkspacePhase() {")
    assert iStart != -1
    iEnd = sSource.find("\n}\n", iStart)
    sBlock = sSource[iStart:iEnd]
    iBootingPos = sBlock.find('fnWriteReadinessMarker "booting"')
    iSyncPos = sBlock.find("fnSyncAllRepos")
    iInstallPos = sBlock.find("fnInstallAllRepos")
    assert iBootingPos != -1, (
        "fnRunWorkspacePhase must write a booting marker so the host "
        "probe never sees a stale marker from a previous container "
        "session during the long workspace setup."
    )
    assert iBootingPos < iSyncPos, (
        "Booting marker must precede fnSyncAllRepos; otherwise the "
        "host probe reads the stale marker for the entire repo-sync "
        "window."
    )
    assert iBootingPos < iInstallPos


def test_write_readiness_marker_escapes_quotes(tmp_path):
    """A reason containing double quotes must not break the JSON."""
    sWorkspace = str(tmp_path)
    sBody = (
        'fnWriteReadinessMarker "failed" '
        '\'broke at: "make opt"\'\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "failed"
    assert 'make opt' in dictMarker["sReason"]


@pytest.mark.parametrize(
    "sStderr, sExpectedCategory",
    [
        ("fatal: Authentication failed for 'https://...'", "auth"),
        ("Permission denied (publickey).", "auth"),
        ("fatal: could not resolve host: github.com", "network"),
        ("ssh: connect to host github.com port 22: Connection refused", "network"),
        ("fatal: Remote branch foo not found in upstream origin", "branch"),
        ("fatal: pathspec 'unrelated' did not match any file(s)", "branch"),
        ("fatal: something obscure went wrong", "unknown"),
    ],
)
def test_categorize_clone_error(tmp_path, sStderr, sExpectedCategory):
    """fsCategorizeCloneError maps stderr text to one of four buckets."""
    sWorkspace = str(tmp_path)
    sBody = (
        'fsCategorizeCloneError ' + repr(sStderr) + '\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert resultProc.stdout.strip() == sExpectedCategory


@pytest.mark.parametrize(
    "sInput, sExpectedFragment, sBannedFragment",
    [
        (
            "remote: https://x-access-token:abc123@github.com/foo.git: not found",
            "https://REDACTED@github.com/foo.git",
            "abc123",
        ),
        (
            "fatal: could not access http://user:pw@host/repo",
            "http://REDACTED@host/repo",
            "user:pw",
        ),
        (
            "Permission denied (publickey). git@github.com:foo/bar.git",
            "git@github.com:foo/bar.git",
            "REDACTED",
        ),
        (
            "fatal: could not resolve host: github.com",
            "could not resolve host: github.com",
            "REDACTED",
        ),
    ],
)
def test_redact_credentials(tmp_path, sInput, sExpectedFragment, sBannedFragment):
    """fsRedactCredentials strips embedded credentials from HTTPS URLs."""
    sWorkspace = str(tmp_path)
    sBody = 'fsRedactCredentials ' + repr(sInput) + '\n'
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    assert sExpectedFragment in resultProc.stdout
    assert sBannedFragment not in resultProc.stdout


def test_handle_clone_failure_redacts_token_in_warning(tmp_path):
    """A token embedded in a clone-error URL must not leak into the marker."""
    sWorkspace = str(tmp_path)
    sStderrFile = tmp_path / "stderr.txt"
    sStderrFile.write_text(
        "fatal: unable to access 'https://x-access-token:SECRET123@github.com/foo.git/'\n"
    )
    sBody = (
        'fnHandleCloneFailure "foo" "main" "' + str(sStderrFile) + '"\n'
        'fnWriteReadinessMarker "ok" ""\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    sJoined = json.dumps(dictMarker)
    assert "SECRET123" not in sJoined
    assert "REDACTED" in sJoined


def test_handle_clone_failure_records_auth_warning(tmp_path):
    """Auth-class clone failure appends a structured warning entry."""
    sWorkspace = str(tmp_path)
    sStderrFile = tmp_path / "stderr.txt"
    sStderrFile.write_text("fatal: Authentication failed for X\n")
    sBody = (
        'fnHandleCloneFailure "vplanet" "main" "' + str(sStderrFile) + '"\n'
        'fnWriteReadinessMarker "ok" ""\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "vplanet: clone-auth" in sWarning
        for sWarning in dictMarker["saWarnings"]
    )


def test_handle_clone_failure_records_branch_warning(tmp_path):
    """Branch-not-found clone failure surfaces the branch name."""
    sWorkspace = str(tmp_path)
    sStderrFile = tmp_path / "stderr.txt"
    sStderrFile.write_text(
        "fatal: Remote branch experimental not found in upstream origin\n"
    )
    sBody = (
        'fnHandleCloneFailure "vplanet" "experimental" "'
        + str(sStderrFile) + '"\n'
        'fnWriteReadinessMarker "ok" ""\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "vplanet: clone-branch" in sWarning
        and "experimental" in sWarning
        for sWarning in dictMarker["saWarnings"]
    )


def test_exit_trap_writes_failed_marker(tmp_path):
    """fnHandleStartupExit writes a failure marker when none exists yet."""
    sWorkspace = str(tmp_path)
    sBody = (
        'fnHandleStartupExit 7\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "failed"
    assert "7" in dictMarker["sReason"]


def test_exit_trap_preserves_existing_ok_marker(tmp_path):
    """If the success marker already exists, the exit trap must not overwrite it."""
    sWorkspace = str(tmp_path)
    sMarkerDir = os.path.join(sWorkspace, ".vaibify")
    os.makedirs(sMarkerDir, exist_ok=True)
    sMarkerPath = os.path.join(sMarkerDir, ".entrypoint_ready")
    with open(sMarkerPath, "w") as fileHandle:
        fileHandle.write(
            '{"sStatus": "ok", "sReason": "", "saWarnings": []}\n'
        )
    sBody = 'fnHandleStartupExit 0\n'
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["sStatus"] == "ok"


def test_pip_install_failure_appends_warning(tmp_path):
    """A failing pip invocation in fnPipInstall appends a pip-install warning."""
    sWorkspace = str(tmp_path)
    # Override pip to always fail; the warning helper must still record.
    sBody = (
        'pip() { return 1; }\n'
        'export -f pip\n'
        'fnPipInstall "/nonexistent" "demoRepo" --no-deps\n'
        'fnWriteReadinessMarker "ok" ""\n'
    )
    resultProc = _fsRunHelperScript(sWorkspace, sBody)
    assert resultProc.returncode == 0, resultProc.stderr
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "demoRepo: pip-install" in sWarning
        for sWarning in dictMarker["saWarnings"]
    )
