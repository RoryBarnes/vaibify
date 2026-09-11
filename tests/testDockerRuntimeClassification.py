"""Which Docker runtime is this host talking to, and how sure are we.

Every remediation a diagnostic offers depends on the answer, so a
wrong one is not a cosmetic problem: it prints a command that does not
apply to the machine the researcher is sitting at. Two real defects
are pinned here. A named Colima profile (``colima start --profile
gpu``) produced the context ``colima-gpu``, which the exact-equality
test did not recognise, so every piece of Colima-specific advice went
silent on exactly the setup that needed it. And an unidentifiable
runtime must answer ``unknown`` rather than falling through to a
guess.
"""

from unittest.mock import patch

import pytest

from vaibify.docker import dockerContext


def _fnStubEvidence(sContext, sEndpoint="", jsonInfo=None):
    """Return a patch context that fixes all three evidence sources."""
    return (
        patch.object(
            dockerContext, "fsActiveDockerContext", return_value=sContext,
        ),
        patch.object(
            dockerContext, "fsReadActiveContextEndpoint",
            return_value=sEndpoint,
        ),
        patch.object(
            dockerContext, "_fdictReadDockerInfoJson",
            return_value=jsonInfo or {},
        ),
    )


def _fdictClassifyWith(sContext, sEndpoint="", jsonInfo=None, sPlatform="linux"):
    """Classify against fixed evidence, on a fixed platform."""
    tPatches = _fnStubEvidence(sContext, sEndpoint, jsonInfo)
    with tPatches[0], tPatches[1], tPatches[2], patch.object(
        dockerContext.os, "environ", {},
    ), patch.object(dockerContext.sys, "platform", sPlatform):
        return dockerContext.fdictClassifyDockerRuntime()


@pytest.mark.parametrize("sContext,sProfile", [
    ("colima", "default"),
    ("colima-gpu", "gpu"),
    ("colima-research-vm", "research-vm"),
])
def test_a_named_colima_profile_is_recognised(sContext, sProfile):
    """`colima-<profile>` is Colima; the exact-name test missed it."""
    with patch.object(
        dockerContext, "fsActiveDockerContext", return_value=sContext,
    ):
        assert dockerContext.fbColimaActive() is True
        assert dockerContext.fsColimaProfileName() == sProfile


@pytest.mark.parametrize("sContext", ["default", "desktop-linux", "rancher"])
def test_a_non_colima_context_reports_no_profile(sContext):
    """A context that merely mentions nothing Colima answers ''."""
    with patch.object(
        dockerContext, "fsActiveDockerContext", return_value=sContext,
    ):
        assert dockerContext.fbColimaActive() is False
        assert dockerContext.fsColimaProfileName() == ""


def test_colima_is_classified_with_its_profile():
    """The classification carries the profile, so a report can name it."""
    dictRuntime = _fdictClassifyWith(
        "colima-gpu", "unix:///Users/r/.colima/gpu/docker.sock",
    )
    assert dictRuntime["sRuntime"] == dockerContext.S_RUNTIME_COLIMA
    assert dictRuntime["sColimaProfile"] == "gpu"


def test_docker_desktop_is_classified_from_the_daemon_answer():
    """Docker Desktop declares itself in `docker info`."""
    dictRuntime = _fdictClassifyWith(
        "desktop-linux", "unix:///Users/r/.docker/run/docker.sock",
        {"OperatingSystem": "Docker Desktop"},
    )
    assert dictRuntime["sRuntime"] == dockerContext.S_RUNTIME_DOCKER_DESKTOP


def test_a_rootless_socket_is_classified_rootless():
    """The socket under /run/user/<uid> is decisive without the daemon."""
    dictRuntime = _fdictClassifyWith(
        "default", "unix:///run/user/1000/docker.sock",
    )
    assert dictRuntime["sRuntime"] == dockerContext.S_RUNTIME_LINUX_ROOTLESS


def test_rootless_is_recognised_from_the_security_options():
    """A rootless daemon reachable at an unusual socket still classifies."""
    dictRuntime = _fdictClassifyWith(
        "default", "tcp://127.0.0.1:2375",
        {"SecurityOptions": ["name=rootless", "name=seccomp"]},
    )
    assert dictRuntime["sRuntime"] == dockerContext.S_RUNTIME_LINUX_ROOTLESS


def test_a_plain_linux_socket_is_classified_rootful():
    """The ordinary Engine install."""
    dictRuntime = _fdictClassifyWith(
        "default", "unix:///var/run/docker.sock",
        {"OperatingSystem": "Ubuntu 24.04.1 LTS"},
    )
    assert dictRuntime["sRuntime"] == dockerContext.S_RUNTIME_LINUX_ROOTFUL


def test_an_unidentifiable_runtime_says_so_rather_than_guessing():
    """No context, no endpoint, no daemon answer -> unknown, not a guess."""
    dictRuntime = _fdictClassifyWith("", "", {}, sPlatform="darwin")
    assert dictRuntime["sRuntime"] == dockerContext.S_RUNTIME_UNKNOWN
    assert dictRuntime["bDaemonAnswered"] is False
