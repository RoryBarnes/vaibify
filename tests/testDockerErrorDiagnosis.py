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


# -----------------------------------------------------------------------
# An absent socket is not an absent binary
# -----------------------------------------------------------------------


S_DOCKER_PY_SOCKET_ABSENT = (
    "Error while fetching server API version: ('Connection aborted.', "
    "FileNotFoundError(2, 'No such file or directory'))"
)


def test_docker_py_socket_absent_does_not_blame_the_binary():
    """The docker-py socket error must not read as a missing CLI.

    This is the verbatim string ``docker.from_env()`` raises when no
    socket exists at the endpoint it resolved. It names neither the
    socket path nor the word "docker", so the binary-missing patterns
    matched it and told an Ubuntu researcher with a working ``docker``
    command to ``apt-get install docker.io``.

    Kills: deleting the socket-absent branch, or moving it below
    the binary-missing one. It does NOT kill a restored bare
    ``filenotfounderror`` clause -- the branch above answers first,
    so that guard needs its own input; see the next test.
    """
    dictDiagnosis = fdictDiagnoseDockerError(
        S_DOCKER_PY_SOCKET_ABSENT, sContext="default", sPlatform="linux",
    )
    assert "not found on path" not in dictDiagnosis["sHint"].lower()
    assert "apt-get" not in dictDiagnosis["sCommand"]
    assert "socket" in dictDiagnosis["sHint"].lower()


def test_docker_py_socket_absent_names_both_of_its_causes():
    """The hint must not pick one of two causes it cannot distinguish.

    No socket at the resolved endpoint means the daemon is stopped OR
    it listens where vaibify did not look. Naming only one repeats the
    wrong-remedy failure this branch exists to end, so the hint states
    both and the command is the one that tells them apart.
    """
    dictDiagnosis = fdictDiagnoseDockerError(
        S_DOCKER_PY_SOCKET_ABSENT, sContext="default", sPlatform="linux",
    )
    sHint = dictDiagnosis["sHint"].lower()
    assert "not running" in sHint
    assert "context" in sHint
    assert dictDiagnosis["sCommand"] == "docker context ls"


def test_a_genuinely_missing_binary_still_says_so():
    """The other direction: a subprocess FileNotFoundError names 'docker'.

    That string DOES support the claim, and must keep producing the
    install hint -- a socket-absent branch that swallowed it would
    leave a researcher with no Docker at all reading about contexts.
    """
    sError = "FileNotFoundError: [Errno 2] No such file or directory: 'docker'"
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="", sPlatform="linux",
    )
    assert "not found on PATH" in dictDiagnosis["sHint"]
    assert "apt-get" in dictDiagnosis["sCommand"]


def test_docker_py_permission_denied_still_reaches_the_group_hint():
    """The neighbouring docker-py failure must keep its own diagnosis.

    ``PermissionError`` on the socket is the one that really is fixed
    by the docker group, and the new connection-failure predicate sits
    above it -- so this pins that it was not captured too.
    """
    sError = (
        "Error while fetching server API version: ('Connection aborted.', "
        "PermissionError(13, 'Permission denied'))"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="default", sPlatform="linux",
    )
    assert "usermod" in dictDiagnosis["sCommand"]


def test_an_unrelated_missing_file_is_not_read_as_a_missing_binary():
    """A ``FileNotFoundError`` naming something else says nothing about the CLI.

    The socket-absent branch cannot cover this: the text is not a
    connection failure at all, so it falls straight through to the
    binary-missing patterns. Without the "docker" guard there, ANY
    missing file raised during connection setup -- a certificate, a
    config -- is reported as an uninstalled Docker.

    Kills: dropping the ``if "docker" not in sLower`` guard from
    ``_fbErrorIsBinaryMissing``.
    """
    sError = (
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/etc/ssl/certs/ca.pem'"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="default", sPlatform="linux",
    )
    assert "not found on PATH" not in dictDiagnosis["sHint"]
    assert "apt-get" not in dictDiagnosis["sCommand"]
