"""Tests for fpreflightLinuxDockerService probe.

The probe runs only on Linux, and only for a daemon whose unit it can
actually speak about: it asks the runtime classifier, skips Colima and
Docker Desktop entirely, and queries ``systemctl --user`` for a
ROOTLESS daemon. The probe is mocked end-to-end on macOS so it can be
exercised in CI without a live systemd.
"""

from unittest.mock import patch

from vaibify.cli.preflightChecks import fpreflightLinuxDockerService
from vaibify.docker.dockerContext import (
    S_RUNTIME_COLIMA, S_RUNTIME_DOCKER_DESKTOP, S_RUNTIME_LINUX_ROOTFUL,
    S_RUNTIME_LINUX_ROOTLESS,
)


def _fdictRuntime(sRuntime):
    """Return a classifier answer naming one runtime."""
    return {
        "sRuntime": sRuntime, "sContextName": "", "sColimaProfile": "",
        "sEndpoint": "", "bDaemonAnswered": True,
    }


def test_silent_when_not_linux():
    """Non-Linux platforms return None without touching systemctl."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "darwin",
    ):
        assert fpreflightLinuxDockerService() is None


def test_silent_when_colima_active_on_linux():
    """Linux + Colima yields None — Colima paths own the diagnosis."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_COLIMA),
    ):
        assert fpreflightLinuxDockerService() is None


def test_silent_when_systemctl_missing():
    """A missing systemctl binary yields None (can't diagnose)."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
    ), patch(
        "vaibify.cli.preflightChecks._fsSystemDockerServiceStatus",
        return_value="",
    ):
        assert fpreflightLinuxDockerService() is None


def test_silent_when_service_active():
    """An active docker.service yields None."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
    ), patch(
        "vaibify.cli.preflightChecks._fsSystemDockerServiceStatus",
        return_value="active",
    ):
        assert fpreflightLinuxDockerService() is None


def test_fails_when_service_inactive():
    """An inactive docker.service yields fail with systemctl hint."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
    ), patch(
        "vaibify.cli.preflightChecks._fsSystemDockerServiceStatus",
        return_value="inactive",
    ), patch(
        "vaibify.cli.preflightChecks._fsRecentDockerJournalTail",
        return_value="",
    ):
        resultPreflight = fpreflightLinuxDockerService()
    assert resultPreflight is not None
    assert resultPreflight.sLevel == "fail"
    assert resultPreflight.sName == "docker-service"
    assert resultPreflight.sCommand == "sudo systemctl start docker"


def test_fails_when_service_failed_with_journal_diagnosis():
    """A failed service whose journal matches the catalog wins specific hint."""
    sJournal = (
        "May 28 12:00:00 host dockerd[1234]: failed to start: "
        "Cannot connect to the Docker daemon. "
        "Is the docker daemon running?"
    )
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_LINUX_ROOTFUL),
    ), patch(
        "vaibify.cli.preflightChecks._fsSystemDockerServiceStatus",
        return_value="failed",
    ), patch(
        "vaibify.cli.preflightChecks._fsRecentDockerJournalTail",
        return_value=sJournal,
    ):
        resultPreflight = fpreflightLinuxDockerService()
    assert resultPreflight is not None
    assert resultPreflight.sLevel == "fail"
    assert "failed" in resultPreflight.sMessage
    assert resultPreflight.sCommand == "sudo systemctl start docker"


def test_silent_for_docker_desktop_on_linux():
    """Docker Desktop's daemon is not the systemd docker.service."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_DOCKER_DESKTOP),
    ):
        assert fpreflightLinuxDockerService() is None


def test_a_rootless_daemon_is_queried_as_a_user_unit():
    """The rootless lane queries `systemctl --user`, not the system bus.

    Asserting the composed ARGUMENT VECTOR, because the two lanes
    return the same string for a healthy daemon and a wrong query
    reports a running rootless daemon as inactive.
    """
    listCommands = []

    def _fnRecordCommand(saCommand, **kwargs):
        listCommands.append(list(saCommand))

        class _Result:
            stdout = "active"
        return _Result()

    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_LINUX_ROOTLESS),
    ), patch(
        "vaibify.cli.preflightChecks.subprocess.run", _fnRecordCommand,
    ):
        assert fpreflightLinuxDockerService() is None
    assert listCommands == [["systemctl", "--user", "is-active", "docker"]]


def test_a_rootless_failure_never_advises_sudo():
    """`sudo systemctl start docker` starts the WRONG daemon here."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fdictClassifyDockerRuntime",
        return_value=_fdictRuntime(S_RUNTIME_LINUX_ROOTLESS),
    ), patch(
        "vaibify.cli.preflightChecks._fsSystemDockerServiceStatus",
        return_value="inactive",
    ), patch(
        "vaibify.cli.preflightChecks._fsRecentDockerJournalTail",
        return_value="",
    ):
        resultPreflight = fpreflightLinuxDockerService()
    assert resultPreflight is not None
    assert resultPreflight.sCommand == "systemctl --user start docker"
    assert "sudo" not in resultPreflight.sCommand
