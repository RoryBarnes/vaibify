"""Tests for fpreflightLinuxDockerService probe.

The probe runs only on Linux when Colima is not the active runtime.
It calls `systemctl is-active docker` to detect a stopped daemon and
suggests `sudo systemctl start docker`. The probe is mocked end-to-end
on macOS so it can be exercised in CI without a live systemd.
"""

from unittest.mock import patch

from vaibify.cli.preflightChecks import fpreflightLinuxDockerService


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
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ):
        assert fpreflightLinuxDockerService() is None


def test_silent_when_systemctl_missing():
    """A missing systemctl binary yields None (can't diagnose)."""
    with patch(
        "vaibify.cli.preflightChecks.sys.platform", "linux",
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
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
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
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
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
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
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
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
