"""Tests for fdictDiagnoseDockerError pattern matching.

Each common Docker init failure should map to a specific hint and
copy-pasteable command. Unrecognized errors must still produce a
non-empty hint and the verbatim error must travel along separately
(verified in tests/testDockerStatusEndpoint.py).
"""

from vaibify.gui.pipelineServer import fdictDiagnoseDockerError


def test_colima_stale_disk_lock_recognized():
    """The Colima 'in use by instance' error suggests force-restart."""
    sError = (
        "failed to run attach disk \"colima\", in use by instance "
        "\"colima\""
    )
    dictDiagnosis = fdictDiagnoseDockerError(sError)
    assert "colima stop --force" in dictDiagnosis["sCommand"]
    assert "colima start" in dictDiagnosis["sCommand"]
    assert dictDiagnosis["sHint"]


def test_daemon_unreachable_recognized():
    """A 'Cannot connect to the Docker daemon' error suggests start."""
    sError = (
        "Cannot connect to the Docker daemon at "
        "unix:///Users/rory/.colima/default/docker.sock. "
        "Is the docker daemon running?"
    )
    dictDiagnosis = fdictDiagnoseDockerError(sError)
    assert "colima start" in dictDiagnosis["sCommand"]
    assert "daemon" in dictDiagnosis["sHint"].lower()


def test_docker_binary_missing_recognized():
    """A FileNotFoundError on 'docker' suggests installing it."""
    sError = "FileNotFoundError: [Errno 2] No such file or directory: 'docker'"
    dictDiagnosis = fdictDiagnoseDockerError(sError)
    assert "install" in dictDiagnosis["sHint"].lower()
    assert dictDiagnosis["sCommand"]


def test_socket_permission_denied_recognized():
    """A permission-denied error on the socket suggests restart."""
    sError = (
        "Permission denied while trying to connect to the Docker "
        "daemon socket at unix:///var/run/docker.sock"
    )
    dictDiagnosis = fdictDiagnoseDockerError(sError)
    assert "permission" in dictDiagnosis["sHint"].lower()
    assert dictDiagnosis["sCommand"]


def test_unknown_error_still_yields_actionable_hint():
    """An unrecognized error must still produce a non-empty hint."""
    sError = "kernel panic: keyboard not found"
    dictDiagnosis = fdictDiagnoseDockerError(sError)
    assert dictDiagnosis["sHint"]
    assert dictDiagnosis["sCommand"]


def test_empty_error_does_not_crash():
    """Empty input must not raise and must still produce a hint."""
    dictDiagnosis = fdictDiagnoseDockerError("")
    assert dictDiagnosis["sHint"]
    assert "sCommand" in dictDiagnosis


def test_none_input_does_not_crash():
    """``None`` is treated as no diagnostic text, not a crash."""
    dictDiagnosis = fdictDiagnoseDockerError(None)
    assert dictDiagnosis["sHint"]
    assert "sCommand" in dictDiagnosis
