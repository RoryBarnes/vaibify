"""Tests for fdictDiagnoseDockerError pattern matching.

Each common Docker init failure should map to a specific hint and
copy-pasteable command. Unrecognized errors must still produce a
non-empty hint and the verbatim error must travel along separately
(verified in tests/testDockerStatusEndpoint.py).
"""

from vaibify.docker.dockerErrorDiagnosis import fdictDiagnoseDockerError


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


def test_daemon_unreachable_with_colima_context_names_colima():
    """When sContext='colima', the hint mentions Colima explicitly."""
    sError = "Cannot connect to the Docker daemon at unix:///foo/docker.sock."
    dictDiagnosis = fdictDiagnoseDockerError(sError, sContext="colima")
    assert dictDiagnosis["sCommand"] == "colima start"
    assert "colima" in dictDiagnosis["sHint"].lower()


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


# -----------------------------------------------------------------------
# Linux platform branches
# -----------------------------------------------------------------------


def test_linux_daemon_unreachable_recommends_systemctl():
    """On Linux without Colima, recommend `sudo systemctl start docker`."""
    sError = (
        "Cannot connect to the Docker daemon at "
        "unix:///var/run/docker.sock. Is the docker daemon running?"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="default", sPlatform="linux",
    )
    assert dictDiagnosis["sCommand"] == "sudo systemctl start docker"
    assert "docker.service" in dictDiagnosis["sHint"]


def test_linux_permission_denied_recommends_usermod():
    """Linux permission-denied points the user at the docker group."""
    sError = (
        "permission denied while trying to connect to the Docker "
        "daemon socket"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="default", sPlatform="linux",
    )
    assert "usermod" in dictDiagnosis["sCommand"]
    assert "docker" in dictDiagnosis["sHint"].lower()


def test_linux_binary_missing_recommends_apt():
    """Linux binary-missing recommends a distro package install."""
    sError = "FileNotFoundError: [Errno 2] No such file or directory: 'docker'"
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="", sPlatform="linux",
    )
    assert "apt-get" in dictDiagnosis["sCommand"]


def test_linux_with_colima_context_uses_colima_branch():
    """Colima-on-Linux is uncommon but should still get Colima hints."""
    sError = "Cannot connect to the Docker daemon at unix:///foo/docker.sock."
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="colima", sPlatform="linux",
    )
    assert dictDiagnosis["sCommand"] == "colima start"


def test_macos_diagnosis_unchanged_when_context_passed():
    """Passing sContext/sPlatform on macOS preserves the legacy hint."""
    sError = (
        "Cannot connect to the Docker daemon at "
        "unix:///Users/x/.colima/default/docker.sock."
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="colima", sPlatform="darwin",
    )
    assert dictDiagnosis["sCommand"] == "colima start"
