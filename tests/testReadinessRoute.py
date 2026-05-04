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


def _fsMarkerPayload(sJsonBody):
    """Wrap a marker JSON body in the new MARKER_PRESENT wire format."""
    return "MARKER_PRESENT\n" + sJsonBody


def _fsDirOnlyPayload(iAgeSeconds):
    """Wrap a stale-image probe response in the new DIR_ONLY format."""
    return f"DIR_ONLY {iAgeSeconds}\n"


def test_probe_missing_marker_reports_booting():
    """A NOTHING probe response (no marker, no .vaibify dir) is booting."""
    connectionDocker = _MockDockerConnection((0, "NOTHING\n"))
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is False
    assert dictResult["sStatus"] == "booting"


def test_probe_exec_failure_reports_booting():
    """An exec failure (exit non-zero) is treated as still booting."""
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
        {
            "sStatus": "ok", "sReason": "",
            "saWarnings": [], "sEntrypointVersion": "1",
        }
    )
    connectionDocker = _MockDockerConnection(
        (0, _fsMarkerPayload(sJson)),
    )
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
            "sEntrypointVersion": "1",
        }
    )
    connectionDocker = _MockDockerConnection(
        (0, _fsMarkerPayload(sJson)),
    )
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
            "sEntrypointVersion": "1",
        }
    )
    connectionDocker = _MockDockerConnection(
        (0, _fsMarkerPayload(sJson)),
    )
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


def test_probe_stale_image_when_dir_old_and_no_marker():
    """The .vaibify dir aging past the threshold flags a stale image."""
    iAge = systemRoutes._I_STALE_IMAGE_THRESHOLD_SECONDS + 5
    connectionDocker = _MockDockerConnection(
        (0, _fsDirOnlyPayload(iAge)),
    )
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is True
    assert dictResult["sStatus"] == "stale-image"
    assert "Rebuild" in dictResult["sReason"]


def test_probe_dir_only_below_threshold_reports_booting():
    """A young .vaibify dir (entrypoint mid-flight) stays in booting."""
    connectionDocker = _MockDockerConnection(
        (0, _fsDirOnlyPayload(2)),
    )
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["sStatus"] == "booting"


def test_probe_stale_version_when_marker_version_differs():
    """A marker whose sEntrypointVersion differs flags stale-version."""
    sJson = json.dumps(
        {
            "sStatus": "ok",
            "sReason": "",
            "saWarnings": [],
            "sEntrypointVersion": "0",
        }
    )
    connectionDocker = _MockDockerConnection(
        (0, _fsMarkerPayload(sJson)),
    )
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["bReady"] is True
    assert dictResult["sStatus"] == "stale-version"
    assert "Rebuild" in dictResult["sReason"]


def test_probe_legacy_marker_without_version_is_not_flagged():
    """A marker missing sEntrypointVersion stays at the marker's sStatus."""
    sJson = json.dumps(
        {"sStatus": "ok", "sReason": "", "saWarnings": []}
    )
    connectionDocker = _MockDockerConnection(
        (0, _fsMarkerPayload(sJson)),
    )
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
    assert dictResult["sStatus"] == "ok"


def test_probe_matching_version_passes_through():
    """A marker version equal to the host's expected version is silent."""
    sJson = json.dumps(
        {
            "sStatus": "ok", "sReason": "",
            "saWarnings": [],
            "sEntrypointVersion":
                systemRoutes._S_EXPECTED_ENTRYPOINT_VERSION,
        }
    )
    connectionDocker = _MockDockerConnection(
        (0, _fsMarkerPayload(sJson)),
    )
    dictResult = systemRoutes._fdictProbeContainerReadiness(
        connectionDocker, "abc",
    )
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
