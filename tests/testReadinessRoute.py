"""Tests for the GUI readiness probe in vaibify/gui/routes/systemRoutes.py.

The readiness route is the user's only ground-truth signal that the
container's entrypoint finished. It must distinguish four cases that
cause four very different UI states:

1. No marker → entrypoint still booting.
2. Empty (legacy `touch`) marker → ok with no warnings.
3. Structured JSON with sStatus="ok" → ready.
4. Structured JSON with sStatus="failed" → ready-with-failure (so the GUI
   can render an actionable error, not an infinite spinner).
5. saWarnings non-empty → ready-with-warnings.
6. docker exec hangs past the configured timeout → "stalled" status.
"""

import json
import time

from vaibify.gui.routes import systemRoutes


class _MockDockerConnection:
    """Fake DockerConnection that returns canned exec results."""

    def __init__(self, tResult, fSleepSeconds=0.0):
        self._tResult = tResult
        self._fSleepSeconds = fSleepSeconds

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if self._fSleepSeconds:
            time.sleep(self._fSleepSeconds)
        return self._tResult


def test_probe_missing_marker_reports_booting():
    """A missing marker is reported as booting, not ready."""
    connectionDocker = _MockDockerConnection((1, ""))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is False
    assert dictResult["sStatus"] == "booting"


def test_probe_empty_marker_treated_as_ok():
    """A zero-byte legacy marker (touched, not JSON) is treated as ok."""
    connectionDocker = _MockDockerConnection((0, ""))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is True
    assert dictResult["sStatus"] == "ok"
    assert dictResult["saWarnings"] == []


def test_probe_ok_marker_with_no_warnings():
    """A structured ok marker yields ready=True and no warnings."""
    sJson = json.dumps(
        {"sStatus": "ok", "sReason": "", "saWarnings": []}
    )
    connectionDocker = _MockDockerConnection((0, sJson))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is True
    assert dictResult["sStatus"] == "ok"
    assert dictResult["iWarningCount"] == 0


def test_probe_failed_marker_surfaces_reason():
    """A structured failed marker is ready=True with a populated sReason."""
    sJson = json.dumps(
        {
            "sStatus": "failed",
            "sReason": "binary build crashed",
            "saWarnings": [],
        }
    )
    connectionDocker = _MockDockerConnection((0, sJson))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    # bReady=True so the GUI exits its 'spinner' state and shows the error.
    assert dictResult["bReady"] is True
    assert dictResult["sStatus"] == "failed"
    assert dictResult["sReason"] == "binary build crashed"


def test_probe_warnings_count_and_payload():
    """saWarnings flow through to the response with their count."""
    sJson = json.dumps(
        {
            "sStatus": "ok",
            "sReason": "",
            "saWarnings": [
                "vplanet: pip-install: wheel missing",
                "vplot: c-build: make opt failed",
            ],
        }
    )
    connectionDocker = _MockDockerConnection((0, sJson))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is True
    assert dictResult["iWarningCount"] == 2
    assert "vplanet: pip-install: wheel missing" in dictResult["saWarnings"]


def test_probe_garbage_marker_falls_back_to_ok():
    """Unparseable marker contents must not crash the probe."""
    connectionDocker = _MockDockerConnection((0, "not-json-at-all"))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is True
    assert dictResult["sStatus"] == "ok"


def test_probe_timeout_returns_stalled(monkeypatch):
    """When docker exec hangs past the timeout, status is 'stalled'."""
    monkeypatch.setattr(
        systemRoutes, "_F_READY_PROBE_TIMEOUT_SECONDS", 0.1,
    )
    # Sleep longer than the timeout to trip TimeoutError.
    connectionDocker = _MockDockerConnection((0, ""), fSleepSeconds=1.0)
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is False
    assert dictResult["sStatus"] == "stalled"
    assert "vaibify stop" in dictResult["sReason"]


def test_probe_docker_exception_returns_error():
    """A docker exec failure surfaces as an error status, not a hang."""

    class _BoomConnection:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            raise RuntimeError("daemon unreachable")

    dictResult = systemRoutes._fdictProbeContainerReadiness(
        _BoomConnection(), "abc",
    )
    assert dictResult["bReady"] is False
    assert dictResult["sStatus"] == "error"
    assert "daemon unreachable" in dictResult["sReason"]
