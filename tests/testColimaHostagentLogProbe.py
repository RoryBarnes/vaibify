"""Tests for fpreflightColimaHostagentLog probe.

The probe tails the Colima hostagent stderr log when Colima is the
active runtime, looks for the most recent fatal/error line, and runs
it through the diagnosis catalog. Surfaces known patterns (stale disk
lock, daemon unreachable) proactively in `vaibify doctor`.
"""

from unittest.mock import patch

from vaibify.cli.preflightChecks import fpreflightColimaHostagentLog


_S_STALE_LOCK_LOG = (
    '{"level":"info","msg":"hostagent socket created","time":"2026-05-28T11:23:30-07:00"}\n'
    '{"level":"info","msg":"Starting VZ","time":"2026-05-28T11:23:30-07:00"}\n'
    '{"level":"fatal","msg":"failed to run attach disk \\"colima\\", in use by instance \\"colima\\"","time":"2026-05-28T11:23:30-07:00"}\n'
)


_S_GENERIC_NOISE_LOG = (
    '{"level":"info","msg":"hostagent socket created","time":"2026-05-28T11:00:00-07:00"}\n'
    '{"level":"info","msg":"Starting VZ","time":"2026-05-28T11:00:00-07:00"}\n'
    '{"level":"info","msg":"READY","time":"2026-05-28T11:00:05-07:00"}\n'
)


def _fpathFakeLog(tmp_path, sContent):
    """Write sContent to a temp ha.stderr.log and return its path."""
    pathLog = tmp_path / "ha.stderr.log"
    pathLog.write_text(sContent)
    return pathLog


def test_silent_when_colima_not_active():
    """Non-Colima context returns None without reading the log."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
    ):
        assert fpreflightColimaHostagentLog() is None


def test_silent_when_log_missing(tmp_path):
    """A missing hostagent log file returns None."""
    pathMissing = tmp_path / "does-not-exist.log"
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.preflightChecks._fpathColimaHostagentLog",
        return_value=pathMissing,
    ):
        assert fpreflightColimaHostagentLog() is None


def test_silent_when_log_has_only_info_lines(tmp_path):
    """A log with no fatal/error entries returns None."""
    pathLog = _fpathFakeLog(tmp_path, _S_GENERIC_NOISE_LOG)
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.preflightChecks._fpathColimaHostagentLog",
        return_value=pathLog,
    ):
        assert fpreflightColimaHostagentLog() is None


def test_surfaces_stale_disk_lock(tmp_path):
    """A 'in use by instance' fatal line yields a warn with the fix."""
    pathLog = _fpathFakeLog(tmp_path, _S_STALE_LOCK_LOG)
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.preflightChecks._fpathColimaHostagentLog",
        return_value=pathLog,
    ):
        resultPreflight = fpreflightColimaHostagentLog()
    assert resultPreflight is not None
    assert resultPreflight.sLevel == "warn"
    assert resultPreflight.sName == "colima-hostagent-log"
    assert resultPreflight.sCommand == "colima stop --force && colima start"
    assert "stale" in resultPreflight.sRemediation.lower()


def test_picks_most_recent_fatal_line(tmp_path):
    """When multiple fatal lines exist, the latest one wins."""
    sLog = (
        '{"level":"fatal","msg":"old daemon error","time":"2026-05-27T00:00:00Z"}\n'
        '{"level":"info","msg":"recovered","time":"2026-05-27T00:01:00Z"}\n'
        '{"level":"fatal","msg":"failed to run attach disk \\"colima\\", in use by instance \\"colima\\"","time":"2026-05-28T00:00:00Z"}\n'
    )
    pathLog = _fpathFakeLog(tmp_path, sLog)
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.preflightChecks._fpathColimaHostagentLog",
        return_value=pathLog,
    ):
        resultPreflight = fpreflightColimaHostagentLog()
    assert resultPreflight is not None
    assert "stale" in resultPreflight.sRemediation.lower()


def test_silent_when_log_unparseable(tmp_path):
    """Non-JSON lines are ignored; an entirely non-JSON log returns None."""
    pathLog = _fpathFakeLog(
        tmp_path,
        "plain text line\nanother plain line\n",
    )
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.cli.preflightChecks._fpathColimaHostagentLog",
        return_value=pathLog,
    ):
        assert fpreflightColimaHostagentLog() is None
