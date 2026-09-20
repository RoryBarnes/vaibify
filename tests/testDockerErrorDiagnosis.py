"""Tests for fdictDiagnoseDockerError pattern matching.

Each common Docker init failure should map to a specific hint and
copy-pasteable command. Unrecognized errors must still produce a
non-empty hint and the verbatim error must travel along separately
(verified in tests/testDockerStatusEndpoint.py).
"""

from unittest.mock import patch

from vaibify.docker.dockerContext import (
    S_RUNTIME_COLIMA, S_RUNTIME_DOCKER_DESKTOP, S_RUNTIME_LINUX_ROOTFUL,
    S_RUNTIME_LINUX_ROOTLESS, S_RUNTIME_UNKNOWN,
)
from vaibify.docker.dockerErrorDiagnosis import (
    fdictDiagnoseContainerOperationError, fdictDiagnoseDockerError,
    flistDecisiveBuildLines, fsExplainBuildFailure,
    fsExplainContainerOperationFailure,
)


def _fdictRuntime(sRuntime, sColimaProfile=""):
    """Return a classifier answer naming one runtime.

    The runtime is what the three runtime-dependent branches are
    decided from, so a test that does not name one is not testing the
    thing that decides. Passing it also keeps these tests honest on
    every machine: without it they would silently assert against
    whatever runtime the developer happens to be running.
    """
    return {
        "sRuntime": sRuntime, "sContextName": "", "sEndpoint": "",
        "sColimaProfile": sColimaProfile, "bDaemonAnswered": True,
    }


def test_colima_stale_disk_lock_recognized():
    """The Colima 'in use by instance' error suggests force-restart."""
    sError = (
        "failed to run attach disk \"colima\", in use by instance "
        "\"colima\""
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "default"),
    )
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
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "default"),
    )
    assert "colima start" in dictDiagnosis["sCommand"]
    assert "colima" in dictDiagnosis["sHint"].lower()


def test_daemon_unreachable_with_colima_context_names_colima():
    """When sContext='colima', the hint mentions Colima explicitly."""
    sError = "Cannot connect to the Docker daemon at unix:///foo/docker.sock."
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="colima",
        dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "default"),
    )
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
        dictRuntime=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
    )
    assert dictDiagnosis["sCommand"] == "sudo systemctl start docker"
    assert "system Docker daemon" in dictDiagnosis["sHint"]


def test_linux_permission_denied_recommends_usermod():
    """Linux permission-denied points the user at the docker group."""
    sError = (
        "permission denied while trying to connect to the Docker "
        "daemon socket"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="default", sPlatform="linux",
        dictRuntime=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
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
        dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "default"),
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
        dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "default"),
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
        dictRuntime=_fdictRuntime(S_RUNTIME_UNKNOWN),
    )
    assert "not found on path" not in dictDiagnosis["sHint"].lower()
    assert "apt-get" not in dictDiagnosis["sCommand"]
    assert "socket" in dictDiagnosis["sHint"].lower()


def test_docker_py_socket_absent_names_both_causes_when_runtime_unknown():
    """With no runtime identified, the hint must not pick a cause.

    No socket at the resolved endpoint means the daemon is stopped OR
    it listens where vaibify did not look. Naming only one repeats the
    wrong-remedy failure this branch exists to end, so the hint states
    both and the command is the one that tells them apart.
    """
    dictDiagnosis = fdictDiagnoseDockerError(
        S_DOCKER_PY_SOCKET_ABSENT, sContext="default", sPlatform="linux",
        dictRuntime=_fdictRuntime(S_RUNTIME_UNKNOWN),
    )
    sHint = dictDiagnosis["sHint"].lower()
    assert "stopped" in sHint
    assert "context" in sHint
    assert dictDiagnosis["sCommand"] == "docker context ls"


def test_docker_py_socket_absent_on_a_known_runtime_names_that_runtime():
    """A classified runtime turns the absent socket into one cause.

    The dashboard reported "No Docker socket exists ... compare the
    endpoint" while `vaibify doctor`, from the same stopped Colima,
    said "The Colima virtual machine is not running" and named
    `colima start`. The socket-absent branch must give the runtime's
    own remedy whenever the classifier has one, so the two surfaces
    cannot disagree about one failure.
    """
    dictDiagnosis = fdictDiagnoseDockerError(
        S_DOCKER_PY_SOCKET_ABSENT, sContext="colima", sPlatform="darwin",
        dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "gpu"),
    )
    assert "Colima virtual machine is not running" in dictDiagnosis["sHint"]
    assert dictDiagnosis["sCommand"] == "colima start --profile gpu"
    # The Desktop remedy reads the HOST platform, so pin it: on Linux
    # CI the same runtime answers `systemctl --user start docker-desktop`.
    with patch("vaibify.docker.runtimeRemedies.sys.platform", "darwin"):
        dictDesktop = fdictDiagnoseDockerError(
            S_DOCKER_PY_SOCKET_ABSENT, sContext="desktop-linux",
            sPlatform="darwin",
            dictRuntime=_fdictRuntime(S_RUNTIME_DOCKER_DESKTOP),
        )
    assert "Docker Desktop is not running" in dictDesktop["sHint"]
    assert dictDesktop["sCommand"] == "open -a Docker"


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
        dictRuntime=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
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


# -----------------------------------------------------------------------
# The three answers this catalog used to get wrong, each pinned.
#
# It decided them from the CONTEXT NAME, so a Docker Desktop user on
# macOS was told to run `colima start`, somebody running `colima
# --profile gpu` was given the DEFAULT profile's command (which
# operates on a different virtual machine than their context points
# at), and a rootless daemon was answered with `sudo systemctl start
# docker` -- which starts a second, rootful daemon their context does
# not point at, so the advice appears simply not to work.
# -----------------------------------------------------------------------


def test_docker_desktop_is_never_told_to_start_colima():
    """The wrong-product answer: Colima is not installed on that machine."""
    sError = "Cannot connect to the Docker daemon. Is the docker daemon running?"
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="desktop-linux", sPlatform="darwin",
        dictRuntime=_fdictRuntime(S_RUNTIME_DOCKER_DESKTOP),
    )
    assert "colima" not in dictDiagnosis["sCommand"].lower()
    assert "colima" not in dictDiagnosis["sHint"].lower()
    assert "Docker Desktop" in dictDiagnosis["sHint"]


def test_a_named_colima_profile_gets_its_own_profile():
    """The default profile's command operates on a DIFFERENT machine."""
    sError = "Cannot connect to the Docker daemon at unix:///foo/docker.sock."
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="colima-gpu", sPlatform="darwin",
        dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "gpu"),
    )
    assert dictDiagnosis["sCommand"] == "colima start --profile gpu"


def test_a_rootless_daemon_is_never_told_to_sudo():
    """`sudo systemctl start docker` starts the wrong daemon entirely."""
    sError = (
        "Cannot connect to the Docker daemon at "
        "unix:///run/user/1000/docker.sock. Is the docker daemon running?"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="rootless", sPlatform="linux",
        dictRuntime=_fdictRuntime(S_RUNTIME_LINUX_ROOTLESS),
    )
    assert dictDiagnosis["sCommand"] == "systemctl --user start docker"
    assert "sudo" not in dictDiagnosis["sCommand"]


def test_a_rootless_permission_denial_is_not_a_group_problem():
    """The docker group governs a rootful socket and nothing else."""
    sError = (
        "permission denied while trying to connect to the Docker "
        "daemon socket"
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, sContext="rootless", sPlatform="linux",
        dictRuntime=_fdictRuntime(S_RUNTIME_LINUX_ROOTLESS),
    )
    assert "usermod" not in dictDiagnosis["sCommand"]


def test_an_unidentified_runtime_names_a_diagnostic_not_a_guess():
    """A command that does not apply is worse than saying "I cannot tell"."""
    sError = "Cannot connect to the Docker daemon. Is the docker daemon running?"
    dictDiagnosis = fdictDiagnoseDockerError(
        sError, dictRuntime=_fdictRuntime(S_RUNTIME_UNKNOWN),
    )
    assert dictDiagnosis["sCommand"] == "docker context ls"
    assert "could not identify" in dictDiagnosis["sHint"]


def test_a_stale_colima_lock_names_the_active_profile():
    """Force-stopping the default VM does nothing for a named profile."""
    dictDiagnosis = fdictDiagnoseDockerError(
        "the instance is in use by instance colima-gpu",
        dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA, "gpu"),
    )
    assert "--profile gpu" in dictDiagnosis["sCommand"]


# -----------------------------------------------------------------------
# Container operations: the daemon's stderr, in the researcher's terms
# -----------------------------------------------------------------------


S_DAEMON_IMAGE_MISSING = (
    "docker create failed: Unable to find image 'fillet:latest' locally "
    "Error response from daemon: pull access denied for fillet, "
    "repository does not exist or may require 'docker login'"
)


def test_a_missing_image_is_explained_as_not_built():
    """Docker Hub's wording becomes a sentence about the project.

    A researcher who had just created a project read "repository does
    not exist" as vaibify denying that the project existed; the image
    had simply never been built.
    """
    dictDiagnosis = fdictDiagnoseContainerOperationError(
        S_DAEMON_IMAGE_MISSING, "fillet",
    )
    assert "has not been built yet" in dictDiagnosis["sHint"]
    assert "fillet" in dictDiagnosis["sHint"]
    assert "Rebuild" in dictDiagnosis["sHint"]
    assert dictDiagnosis["sCommand"] == "vaibify build -p fillet"


def test_a_taken_port_names_the_port_and_where_to_change_it():
    dictDiagnosis = fdictDiagnoseContainerOperationError(
        "docker start failed: Error response from daemon: driver failed "
        "programming external connectivity on endpoint x: Bind for "
        "0.0.0.0:8888 failed: port is already allocated", "proj",
    )
    assert "port 8888" in dictDiagnosis["sHint"]
    assert "settings" in dictDiagnosis["sHint"]
    assert "8888" in dictDiagnosis["sCommand"]


def test_a_stale_container_name_names_the_removal():
    dictDiagnosis = fdictDiagnoseContainerOperationError(
        'docker create failed: Error response from daemon: Conflict. The '
        'container name "/proj" is already in use by container "abc"',
        "proj",
    )
    assert "already exists" in dictDiagnosis["sHint"]
    assert dictDiagnosis["sCommand"] == "docker rm -f proj"


def test_a_full_disk_uses_the_runtime_remedy_not_a_blanket_prune():
    dictDiagnosis = fdictDiagnoseContainerOperationError(
        "docker create failed: write /var/lib/docker: no space left on "
        "device", "proj", dictRuntime=_fdictRuntime(S_RUNTIME_COLIMA),
    )
    assert dictDiagnosis["sCommand"] == "docker builder prune"
    assert "system prune -a" in dictDiagnosis["sHint"]


def test_unrecognised_daemon_text_is_not_guessed_at():
    assert fdictDiagnoseContainerOperationError(
        "docker create failed: something entirely new", "proj",
    ) is None


def test_the_explained_sentence_keeps_the_daemons_words_as_evidence():
    """A translation that hid its evidence could not be checked."""
    sSentence = fsExplainContainerOperationFailure(
        "Start", "fillet", S_DAEMON_IMAGE_MISSING,
    )
    assert sSentence.startswith("Start of 'fillet' failed. The image for")
    assert "Command: vaibify build -p fillet" in sSentence
    assert "(Docker said: docker create failed: Unable to find image" in sSentence
    sUntranslated = fsExplainContainerOperationFailure(
        "Stop", "proj", "docker stop failed: something new",
    )
    assert sUntranslated == "Stop of 'proj' failed: docker stop failed: something new"


# -----------------------------------------------------------------------
# Image builds: the reason, not the command line
# -----------------------------------------------------------------------


S_TOOLCHAIN_BUILD_TAIL = """#14 12.31 E: Version '13.3.0-6ubuntu2~24.04.1' for 'gcc-13-x86-64-linux-gnu' was not found
#14 12.40 =============================================================
#14 12.40 This build stopped on purpose. The compiler is an INPUT to your
#14 12.40 results: code compiled by a different gcc can differ in the last
#14 12.41 Currently available in this archive:
#14 ERROR: process "/bin/bash -o pipefail -c set -eu; apt-get install" did not complete successfully: exit code: 1
------
 > [toolchain 3/9] RUN set -eu; apt-get install:
------
Dockerfile:250
--------------------
 249 |     RUN set -eu; \\
 250 | >>>     apt-get install -y --no-install-recommends \\
 251 | >>>         gcc-13-x86-64-linux-gnu=13.3.0-6ubuntu2~24.04.1 \\
--------------------
ERROR: failed to solve: process "/bin/bash -o pipefail -c set -eu" did not complete successfully: exit code: 1
"""


def test_decisive_lines_skip_the_dockerfile_echo_and_keep_the_cause():
    """The sixty-line step echo is not evidence; the apt line is."""
    listLines = flistDecisiveBuildLines(S_TOOLCHAIN_BUILD_TAIL)
    assert listLines[0].startswith("E: Version '13.3.0")
    assert not any(">>>" in sLine for sLine in listLines)
    assert not any(sLine.startswith("#14") for sLine in listLines)
    assert any("stopped on purpose" in sLine for sLine in listLines)


def test_the_toolchain_pin_failure_is_named_as_vaibifys_not_the_projects():
    """An arm64 daemon meeting x86-64 pin names read as a project fault.

    The researcher saw "Docker command failed (exit 1): docker buildx
    build -f ... --build-arg" -- the command, cut off, and never the
    reason. The reason was the pin, which no project setting can fix.
    """
    sSentence = fsExplainBuildFailure(
        "fillet", "Docker command failed (exit 1): docker buildx build",
        S_TOOLCHAIN_BUILD_TAIL,
    )
    assert sSentence.startswith("Build of 'fillet' failed. vaibify's pinned")
    assert "not your project's packages" in sSentence
    # The catalog once said the names were x86-64 only. The Dockerfile
    # has carried an arm64 list since 2026-09-20, so that sentence would
    # send an Apple Silicon researcher to fight an architecture that is
    # supported.
    assert "x86-64 only" not in sSentence
    assert "Command: python tools/checkToolchainEpoch.py --verify" in sSentence
    assert "(Docker said: " in sSentence
    assert "docker buildx build" not in sSentence


def test_an_unrecognised_build_failure_shows_its_decisive_lines_not_the_argv():
    sSentence = fsExplainBuildFailure(
        "proj", "Docker command failed (exit 1): docker buildx build -f x",
        "#9 2.10 Something entirely new went wrong\n#9 ERROR: process did not complete successfully: exit code: 1\n",
    )
    # A line carrying no failure marker is context, not evidence; the
    # marked line is what the sentence shows.
    assert sSentence == (
        "Build of 'proj' failed: ERROR: process did not complete "
        "successfully: exit code: 1"
    )


def test_a_build_with_no_output_falls_back_to_the_raw_error():
    sSentence = fsExplainBuildFailure("proj", "config not found", "")
    assert sSentence == "Build of 'proj' failed: config not found"
