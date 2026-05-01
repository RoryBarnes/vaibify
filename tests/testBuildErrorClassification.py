"""Tests for build-error classification (F-B-08, F-B-11) and stderr capture."""

import subprocess
import sys

from unittest.mock import patch

import pytest

from vaibify.cli.commandBuild import (
    _LIST_BUILD_ERROR_PATTERNS,
    _fnHandleBuildError,
    _fsBuildErrorHint,
    _fsClassifyBuildError,
)
from vaibify.docker.imageBuilder import _fnRunDockerBuildCapturing


# -------------------------------------------------------------------
# _fsClassifyBuildError -- pattern detection
# -------------------------------------------------------------------

def test_fsClassifyBuildError_empty_returns_blank():
    assert _fsClassifyBuildError("") == ""


def test_fsClassifyBuildError_unknown_returns_blank():
    assert _fsClassifyBuildError("nothing matches here") == ""


def test_fsClassifyBuildError_docker_hub_rate_limit():
    sStderr = (
        "ERROR: failed to solve: ubuntu:24.04: failed to authorize: "
        "toomanyrequests: You have reached your pull rate limit. "
        "You may increase the limit by authenticating ..."
    )
    assert _fsClassifyBuildError(sStderr) == "docker-hub-rate-limit"


def test_fsClassifyBuildError_pull_rate_limit_phrase():
    sStderr = "Error response from daemon: pull rate limit exceeded"
    assert _fsClassifyBuildError(sStderr) == "docker-hub-rate-limit"


def test_fsClassifyBuildError_manifest_unknown():
    sStderr = (
        "ERROR: failed to solve: ubuntu:99.99: "
        "manifest unknown: manifest unknown"
    )
    assert _fsClassifyBuildError(sStderr) == "manifest-not-found"


def test_fsClassifyBuildError_not_found_manifest():
    sStderr = "docker: not found: manifest for fake/image:tag"
    assert _fsClassifyBuildError(sStderr) == "manifest-not-found"


def test_fsClassifyBuildError_network_tls():
    sStderr = (
        "curl: (35) error:0A00010B:SSL routines::wrong version number"
    )
    assert _fsClassifyBuildError(sStderr) == "network-tls"


def test_fsClassifyBuildError_ssl_eof_error():
    sStderr = "Python error: ssl.SSLEOFError: EOF occurred in violation"
    assert _fsClassifyBuildError(sStderr) == "network-tls"


def test_fsClassifyBuildError_tls_or_network_failure_phrase():
    """The Dockerfile diagnostic phrase routes to network-tls (Round 4)."""
    sStderr = (
        "Possible TLS or network failure during apt update; "
        "see network workarounds in the README."
    )
    assert _fsClassifyBuildError(sStderr) == "network-tls"


def test_fsClassifyBuildError_pip_source_build_gcc():
    sStderr = (
        "      gcc: error: unrecognized command-line option '-Wfoo'\n"
        "      error: command '/usr/bin/gcc' failed with exit code 1\n"
    )
    assert _fsClassifyBuildError(sStderr) == "pip-source-build"


def test_fsClassifyBuildError_pip_source_build_failed_wheel():
    sStderr = (
        "ERROR: Failed building wheel for h5py\n"
        "ERROR: Could not build wheels for h5py"
    )
    assert _fsClassifyBuildError(sStderr) == "pip-source-build"


def test_fsClassifyBuildError_pip_source_build_legacy_bdist():
    sStderr = "error: invalid command 'bdist_wheel'"
    assert _fsClassifyBuildError(sStderr) == "pip-source-build"


def test_fsClassifyBuildError_oom_exit_137():
    sStderr = "Docker command failed (exit 137): docker build ..."
    assert _fsClassifyBuildError(sStderr) == "oom"


def test_fsClassifyBuildError_oom_killed_signal():
    sStderr = "process was Killed signal 9"
    assert _fsClassifyBuildError(sStderr) == "oom"


def test_fsClassifyBuildError_rate_limit_takes_priority_over_tls():
    # Order matters: rate-limit comes before network-tls in the list,
    # so a stderr that mentions both should classify as the
    # actionable rate-limit.
    sStderr = "ssl: warning ... toomanyrequests for image"
    assert _fsClassifyBuildError(sStderr) == "docker-hub-rate-limit"


def test_LIST_BUILD_ERROR_PATTERNS_keys_unique():
    listKeys = [sKey for sKey, _ in _LIST_BUILD_ERROR_PATTERNS]
    assert len(listKeys) == len(set(listKeys))


# -------------------------------------------------------------------
# _fsBuildErrorHint -- hint text per classification
# -------------------------------------------------------------------

def test_fsBuildErrorHint_each_classification_has_hint():
    for sKey, _ in _LIST_BUILD_ERROR_PATTERNS:
        assert _fsBuildErrorHint(sKey), (
            f"Missing hint for classification key '{sKey}'"
        )


def test_fsBuildErrorHint_blank_for_empty_classification():
    assert _fsBuildErrorHint("") == ""


def test_fsBuildErrorHint_rate_limit_mentions_docker_login():
    sHint = _fsBuildErrorHint("docker-hub-rate-limit")
    assert "docker login" in sHint.lower()


def test_fsBuildErrorHint_pip_mentions_prefer_binary():
    sHint = _fsBuildErrorHint("pip-source-build")
    assert "--prefer-binary" in sHint


def test_fsBuildErrorHint_oom_mentions_memory():
    sHint = _fsBuildErrorHint("oom")
    assert "OOM" in sHint or "memory" in sHint.lower()


# -------------------------------------------------------------------
# _fnHandleBuildError -- end-to-end appending
# -------------------------------------------------------------------

def test_fnHandleBuildError_appends_rate_limit_hint(capsys):
    error = RuntimeError("Docker command failed (exit 1): docker build ...")
    error.sStderrTail = (
        "ERROR: toomanyrequests: You have reached your pull rate limit"
    )
    with pytest.raises(SystemExit):
        _fnHandleBuildError(error)
    sCaptured = capsys.readouterr().err
    assert "Docker build failed" in sCaptured
    assert "rate-limited" in sCaptured.lower()


def test_fnHandleBuildError_appends_pip_source_hint(capsys):
    error = RuntimeError("Docker command failed (exit 1): docker build ...")
    error.sStderrTail = (
        "      error: command '/usr/bin/gcc' failed with exit code 1\n"
    )
    with pytest.raises(SystemExit):
        _fnHandleBuildError(error)
    sCaptured = capsys.readouterr().err
    assert "Docker build failed" in sCaptured
    assert "pip package" in sCaptured.lower()


def test_fnHandleBuildError_oom_still_works_via_message_only(capsys):
    # The Round 2 OOM path: message contains "exit 137", no stderr tail.
    error = RuntimeError("Docker command failed (exit 137): docker build ...")
    with pytest.raises(SystemExit):
        _fnHandleBuildError(error)
    sCaptured = capsys.readouterr().err
    assert "OOM" in sCaptured


def test_fnHandleBuildError_no_classification_emits_base_message(capsys):
    error = RuntimeError("Docker command failed (exit 1): docker build ...")
    with pytest.raises(SystemExit):
        _fnHandleBuildError(error)
    sCaptured = capsys.readouterr().err
    assert "Docker build failed" in sCaptured
    # No parenthetical hint suffix when nothing matches.
    assert "(" not in sCaptured.split("Docker build failed")[1]


def test_fnHandleBuildError_handles_missing_stderr_tail_attribute(capsys):
    # Errors without sStderrTail must not crash the handler.
    error = RuntimeError("Docker command failed (exit 1): docker build ...")
    assert not hasattr(error, "sStderrTail")
    with pytest.raises(SystemExit):
        _fnHandleBuildError(error)
    sCaptured = capsys.readouterr().err
    assert "Docker build failed" in sCaptured


# -------------------------------------------------------------------
# _fnRunDockerBuildCapturing -- streams + captures stderr
# -------------------------------------------------------------------

def test_fnRunDockerBuildCapturing_success_returns_none(capsys):
    saCommand = [sys.executable, "-c", "import sys; sys.stderr.write('ok\\n')"]
    assert _fnRunDockerBuildCapturing(saCommand) is None
    sCaptured = capsys.readouterr().err
    assert "ok" in sCaptured


def test_fnRunDockerBuildCapturing_failure_attaches_stderr_tail(capsys):
    sScript = (
        "import sys\n"
        "for i in range(70):\n"
        "    sys.stderr.write(f'line{i}\\n')\n"
        "sys.exit(1)\n"
    )
    saCommand = [sys.executable, "-c", sScript]
    with pytest.raises(RuntimeError) as excInfo:
        _fnRunDockerBuildCapturing(saCommand)
    sStderrTail = getattr(excInfo.value, "sStderrTail", "")
    assert "line69" in sStderrTail
    # Tail capped at 50 lines: earliest lines should NOT be in tail.
    assert "line0\n" not in sStderrTail
    sCapturedStream = capsys.readouterr().err
    # Streaming preserves the full stream regardless of capture cap.
    assert "line0" in sCapturedStream
    assert "line69" in sCapturedStream


def test_fnRunDockerBuildCapturing_classifies_rate_limit_via_handler(capsys):
    # End-to-end: a captured rate-limit message reaches the handler.
    sScript = (
        "import sys\n"
        "sys.stderr.write('toomanyrequests: pull rate limit hit\\n')\n"
        "sys.exit(1)\n"
    )
    saCommand = [sys.executable, "-c", sScript]
    try:
        _fnRunDockerBuildCapturing(saCommand)
    except RuntimeError as error:
        with pytest.raises(SystemExit):
            _fnHandleBuildError(error)
    sCaptured = capsys.readouterr().err
    assert "rate-limited" in sCaptured.lower()
