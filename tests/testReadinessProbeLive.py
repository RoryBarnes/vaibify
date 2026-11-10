"""The readiness probe dates the marker by the container's own clock.

``tests/testReadinessRoute.py`` drives the parser with canned output.
Only a real Linux container can say whether the probe's shell reads
PID 1's start out of ``/proc`` correctly, which is the whole of the
difference between this run's marker and the one a previous run left in
the persistent volume.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import json
import subprocess

import pytest

from tests.liveContainerLabels import flistLabelArguments
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui.routes import systemRoutes

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.12-slim"


class _CliDockerConnection:
    """Run the probe's command with ``docker exec``, as the hub's exec does."""

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        processExec = subprocess.run(
            ["docker", "exec", sContainerId, "sh", "-c", sCommand],
            capture_output=True, text=True, timeout=60)
        return processExec.returncode, processExec.stdout


@pytest.fixture
def sContainerId():
    fnRequireDaemonReachable()
    sId = subprocess.run(
        ["docker", "run", "-d", "--rm", *flistLabelArguments(), S_IMAGE,
         "sh", "-c", "mkdir -p /workspace/.vaibify && sleep 600"],
        check=True, capture_output=True, text=True, timeout=600,
    ).stdout.strip()
    yield sId
    subprocess.run(["docker", "rm", "-f", sId], capture_output=True)


def _fnWriteMarker(sContainerId, sStatus, sReason, sTouch=""):
    sJson = json.dumps({"sStatus": sStatus, "sReason": sReason})
    subprocess.run(
        ["docker", "exec", sContainerId, "sh", "-c",
         f"printf '%s\\n' '{sJson}' > /workspace/.vaibify/.entrypoint_ready"
         + (f" && touch -d '{sTouch}' /workspace/.vaibify/.entrypoint_ready"
            if sTouch else "")],
        check=True, timeout=60)


def _fdictProbe(sContainerId):
    return systemRoutes._fdictProbeContainerReadiness(
        _CliDockerConnection(), sContainerId)


def testAMarkerOlderThanTheContainerReadsAsBooting(sContainerId):
    _fnWriteMarker(sContainerId, "ok", "", sTouch="2001-01-01 00:00:00")
    dictResult = _fdictProbe(sContainerId)
    assert (dictResult["bReady"], dictResult["sStatus"]) == (False, "booting")
    assert 0 <= dictResult["iSecondsSinceStart"] < 600


def testAMarkerThisRunWroteIsBelieved(sContainerId):
    _fnWriteMarker(sContainerId, "ok", "")
    assert _fdictProbe(sContainerId)["bReady"] is True


def testABootingMarkerNamesItsStep(sContainerId):
    _fnWriteMarker(sContainerId, "booting", "Installing vplot (3 of 9)")
    dictResult = _fdictProbe(sContainerId)
    assert dictResult["sStep"] == "Installing vplot (3 of 9)"
    assert 0 <= dictResult["iSecondsSinceProgress"] < 60
