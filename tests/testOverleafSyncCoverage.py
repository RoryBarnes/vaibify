"""Behaviour tests for the Overleaf sync helpers (no network).

These cover the stderr redaction, the subprocess wrapper's error
classification (auth / rate-limit / generic), the CLI input parsing
and project-id validation, and the target/pull path-safety guards —
all without touching a real Overleaf.
"""

import subprocess
from unittest.mock import patch

import pytest

from vaibify.reproducibility import overleafSync as ov


# --- stderr redaction ---

def test_redact_strips_url_credentials():
    sOut = ov._fsRedactStderr(
        "fatal: https://user:tok3n@git.overleaf.com/abc failed")
    assert "tok3n" not in sOut
    assert "<redacted>@" in sOut


def test_redact_masks_sensitive_lines():
    sOut = ov._fsRedactStderr("Authorization: Bearer abc\nplain line")
    assert "plain line" in sOut
    assert "Bearer abc" not in sOut


def test_redact_empty_is_empty():
    assert ov._fsRedactStderr("") == ""


# --- subprocess error classification ---

def test_run_subprocess_returns_result_on_success():
    with patch("subprocess.run") as mockRun:
        mockRun.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        result = ov._fprocessRunSubprocess(["git", "status"], "failed")
    assert result.returncode == 0


def test_run_subprocess_maps_missing_command():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(ov.OverleafError) as excinfo:
            ov._fprocessRunSubprocess(["git"], "clone failed")
    assert "command not found" in str(excinfo.value)


def test_run_subprocess_detects_auth_failure():
    error = subprocess.CalledProcessError(1, ["git"], "", "HTTP 401")
    with patch("subprocess.run", side_effect=error):
        with pytest.raises(ov.OverleafAuthError):
            ov._fprocessRunSubprocess(["git"], "push failed")


def test_run_subprocess_detects_rate_limit():
    sHint = ov._RATE_LIMIT_HINT
    error = subprocess.CalledProcessError(1, ["git"], "", sHint)
    with patch("subprocess.run", side_effect=error):
        with pytest.raises(ov.OverleafRateLimitError):
            ov._fprocessRunSubprocess(["git"], "push failed")


def test_run_subprocess_generic_failure():
    error = subprocess.CalledProcessError(1, ["git"], "", "disk full")
    with patch("subprocess.run", side_effect=error):
        with pytest.raises(ov.OverleafError) as excinfo:
            ov._fprocessRunSubprocess(["git"], "push failed")
    assert not isinstance(excinfo.value, ov.OverleafAuthError)
    assert "disk full" in str(excinfo.value)


def test_detect_helpers_are_silent_on_clean_output():
    ov._fnDetectAuthFailure("everything fine")
    ov._fnDetectRateLimit("everything fine")


# --- CLI input parsing / validation ---

def test_validate_project_id_accepts_a_valid_id():
    ov._fnValidateProjectIdOrDie("60f1a2b3c4d5e6f7a8b9c0d1")


def test_validate_project_id_rejects_metacharacters():
    with pytest.raises(SystemExit) as excinfo:
        ov._fnValidateProjectIdOrDie("../etc; rm -rf /")
    assert excinfo.value.code == ov._EXIT_USAGE


def test_read_token_and_rest_splits_first_line(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("tok3n\nrest line\n"))
    sToken, sRest = ov._ftReadTokenAndRest()
    assert sToken == "tok3n"
    assert "rest line" in sRest


def test_read_token_exits_when_absent(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("\nrest\n"))
    with pytest.raises(SystemExit) as excinfo:
        ov._ftReadTokenAndRest()
    assert excinfo.value.code == ov._EXIT_AUTH


def test_split_remainder_drops_blank_lines():
    assert ov._flistSplitRemainderLines("a\n\n  b  \n") == ["a", "b"]


def test_remove_token_file_ignores_missing(tmp_path):
    ov._fnRemoveTokenFile(str(tmp_path / "nope.token"))  # must not raise


# --- path-safety guards ---

def test_validate_target_directory_allows_empty_and_relative():
    ov.fnValidateTargetDirectory("")
    ov.fnValidateTargetDirectory("figures/sub")


@pytest.mark.parametrize("sBad", ["/abs", "\\abs", "a/../b", "x\x00y"])
def test_validate_target_directory_rejects_unsafe(sBad):
    with pytest.raises(ov.OverleafError):
        ov.fnValidateTargetDirectory(sBad)


def test_validate_target_directory_requires_a_value():
    with pytest.raises(ov.OverleafError):
        ov.fnValidateTargetDirectory(None)


@pytest.mark.parametrize("sBad", ["", "/abs", "a/../b", "x\x00y"])
def test_validate_pull_path_rejects_unsafe(sBad):
    with pytest.raises(ov.OverleafError):
        ov.fnValidatePullRelativePath(sBad)


def test_validate_pull_path_accepts_a_relative_path():
    ov.fnValidatePullRelativePath("main.tex")
