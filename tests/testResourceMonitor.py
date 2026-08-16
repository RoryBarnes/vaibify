"""Tests for vaibify.gui.resourceMonitor.

The two halves are driven differently on purpose, because they reach the
daemon differently. CPU and memory still come from ``docker stats``, a
daemon query, so those tests still drive ``subprocess.run``. Disk went
through ``docker exec ... df`` -- a container exec assembled in a GUI
module -- and is now a TYPED READ through the gateway, so those tests
drive a connection stub instead.

Every degraded reason the old suite asserted is asserted here. That is
the point of rewriting rather than replacing them: the dashboard's
ability to say "daemon unreachable" rather than render a plausible zero
is the behaviour, and it must survive the change of transport.
"""

import concurrent.futures
import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from vaibify.gui.resourceMonitor import fdictGetContainerStats


class _DockerApiError(Exception):
    """An error carrying the daemon's own status code, as docker-py does."""

    def __init__(self, sMessage, iStatusCode):
        super().__init__(sMessage)
        self.status_code = iStatusCode


class _StubConnectionDisk:
    """A connection whose typed filesystem read is scripted."""

    def __init__(self, dictUsage=None, errorToRaise=None, fSleepSeconds=0.0):
        self._dictUsage = dictUsage
        self._errorToRaise = errorToRaise
        self._fSleepSeconds = fSleepSeconds
        self.listCalls = []

    def fdictReadFilesystemUsage(self, sContainerId, sPath):
        self.listCalls.append((sContainerId, sPath))
        if self._fSleepSeconds:
            import time
            time.sleep(self._fSleepSeconds)
        if self._errorToRaise is not None:
            raise self._errorToRaise
        return self._dictUsage


def _fconnectionWithUsage(iTotal, iUsed, iFree):
    """Return a stub reporting one filesystem reading."""
    return _StubConnectionDisk(dictUsage={
        "iTotalBytes": iTotal, "iUsedBytes": iUsed, "iFreeBytes": iFree,
    })


def _fmockCompletedProcess(sStdout="", sStderr="", iReturncode=0):
    """Build a mock subprocess.CompletedProcess."""
    mockResult = MagicMock()
    mockResult.stdout = sStdout
    mockResult.stderr = sStderr
    mockResult.returncode = iReturncode
    return mockResult


def _fbuildHealthyStatsJson():
    """Return a representative docker stats JSON payload."""
    return json.dumps({
        "CPUPerc": "25.50%",
        "MemPerc": "12.34%",
        "MemUsage": "512MiB / 4GiB",
    })


def test_fdictGetContainerStats_parses_healthy_state():
    connectionDocker = _fconnectionWithUsage(
        100 * 1024 ** 3, 40 * 1024 ** 3, 60 * 1024 ** 3,
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["bAvailable"] is True
    assert dictStats["sReason"] == ""
    assert dictStats["fCpuPercent"] == pytest.approx(25.50)
    assert dictStats["fMemoryPercent"] == pytest.approx(12.34)
    assert dictStats["sMemoryUsage"] == "512MiB"
    assert dictStats["sMemoryLimit"] == "4GiB"
    assert dictStats["dictDisk"]["bAvailable"] is True
    assert dictStats["dictDisk"]["iTotalBytes"] == 100 * 1024 ** 3
    assert dictStats["dictDisk"]["fFreeFraction"] == pytest.approx(0.6)
    assert dictStats["bDiskWarning"] is False


def test_the_disk_read_names_a_path_and_never_a_command():
    """The adapter contract, asserted from the caller's side.

    The exec this replaced put ``df -PB1 /`` in a GUI module's argv. What
    crosses the boundary now is the container id and a PATH; the program
    is fixed source text inside the gateway, which is what makes the read
    exemption enumerable rather than a matter of trust.
    """
    connectionDocker = _fconnectionWithUsage(4, 1, 3)
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        fdictGetContainerStats(connectionDocker, "container_abc")
    assert connectionDocker.listCalls == [("container_abc", "/")]


def test_the_module_no_longer_execs_into_the_container():
    """No `docker exec` argv survives anywhere in this module.

    Asserted on the source because that is where the R4 rule bites: a
    module that can assemble one exec can assemble any, and the boundary
    cannot tell a `df` from an `rm -rf` by looking at the primitive.
    """
    import inspect
    from vaibify.gui import resourceMonitor
    sSource = inspect.getsource(resourceMonitor)
    assert '"exec"' not in sSource, "an exec argv is back in this module"
    assert '"df"' not in sSource, "a df argv is back in this module"
    assert "fdictReadFilesystemUsage" in sSource


def test_fdictGetContainerStats_signals_daemon_unreachable():
    connectionDocker = _StubConnectionDisk(
        errorToRaise=RuntimeError("cannot reach the daemon"),
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        side_effect=FileNotFoundError("docker missing"),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "daemon-unreachable"
    assert dictStats["fCpuPercent"] == 0.0
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "daemon-unreachable"
    assert dictStats["bDiskWarning"] is False


def test_fdictGetContainerStats_signals_timeout():
    connectionDocker = _StubConnectionDisk(
        errorToRaise=RuntimeError("cannot reach the daemon"),
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            cmd="docker stats", timeout=10,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "timeout_container")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "timeout"
    assert dictStats["sMemoryUsage"] == "0B"


def test_fdictGetContainerStats_signals_container_not_running():
    """A gone container is recognised by the daemon's code, not by English.

    The old classification matched "no such container" in stderr. The
    typed read surfaces docker-py's own 404, and the gateway's
    ``fbErrorMeansContainerGone`` -- one predicate, not a second copy --
    is what reads it.
    """
    connectionDocker = _StubConnectionDisk(
        errorToRaise=_DockerApiError("No such container: bad_id", 404),
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStderr="Error: No such container: bad_id", iReturncode=1,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "bad_id")

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "container-not-running"
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "container-not-running"


def test_a_stopped_container_is_also_recognised_from_its_conflict_code():
    """409 "is not running" is the other half of the same predicate."""
    connectionDocker = _StubConnectionDisk(
        errorToRaise=_DockerApiError(
            "Container abc is not running", 409,
        ),
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "abc")
    assert dictStats["dictDisk"]["sReason"] == "container-not-running"


def test_fdictGetContainerStats_classifies_daemon_stderr():
    connectionDocker = _fconnectionWithUsage(4, 1, 3)
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStderr="Cannot connect to the Docker daemon at unix:///",
            iReturncode=1,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["sReason"] == "daemon-unreachable"


def test_fdictGetContainerStats_handles_malformed_stats_json():
    connectionDocker = _fconnectionWithUsage(
        100 * 1024 ** 3, 40 * 1024 ** 3, 60 * 1024 ** 3,
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout="not valid json{{{", iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(
            connectionDocker, "malformed_container",
        )

    assert dictStats["bAvailable"] is False
    assert dictStats["sReason"] == "parse-error"
    assert dictStats["dictDisk"]["bAvailable"] is True


def test_fdictGetContainerStats_flags_low_disk_warning():
    connectionDocker = _fconnectionWithUsage(
        100 * 1024 ** 3, 97 * 1024 ** 3, 3 * 1024 ** 3,
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "nearly_full")

    assert dictStats["bDiskWarning"] is True
    assert dictStats["dictDisk"]["fFreeFraction"] == pytest.approx(0.03)
    assert "GiB" in dictStats["dictDisk"]["sFreeHuman"]


def test_fdictGetContainerStats_handles_disk_parse_failure():
    """An unreadable reading is parse-error, not a plausible zero."""
    connectionDocker = _StubConnectionDisk(
        errorToRaise=json.JSONDecodeError("bad", "not json", 0),
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["bAvailable"] is True
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "parse-error"
    assert dictStats["bDiskWarning"] is False


def test_fdictGetContainerStats_handles_garbage_disk_fields():
    """A reading missing a field is parse-error, not a partial answer."""
    connectionDocker = _StubConnectionDisk(dictUsage={"iTotalBytes": 4})
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "parse-error"


def test_fdictGetContainerStats_disk_handles_zero_total():
    connectionDocker = _fconnectionWithUsage(0, 0, 0)
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["dictDisk"]["bAvailable"] is True
    assert dictStats["dictDisk"]["fFreeFraction"] == 0.0


def test_fdictGetContainerStats_disk_timeout_does_not_break_stats(
    monkeypatch,
):
    """The deadline survived the change of transport.

    The gateway's own client timeout is ten MINUTES, so a wedged
    container would otherwise hold the monitor request for that long.
    Driven with a read that really blocks and a deadline shortened to
    keep the suite quick -- a mocked TimeoutError would prove only that
    the except clause is spelled correctly.
    """
    monkeypatch.setattr(
        "vaibify.gui.resourceMonitor._F_DISK_QUERY_TIMEOUT_SECONDS", 0.05,
    )
    connectionDocker = _StubConnectionDisk(
        dictUsage={"iTotalBytes": 1, "iUsedBytes": 0, "iFreeBytes": 1},
        fSleepSeconds=2.0,
    )
    with patch(
        "vaibify.gui.resourceMonitor.subprocess.run",
        return_value=_fmockCompletedProcess(
            sStdout=_fbuildHealthyStatsJson(), iReturncode=0,
        ),
    ):
        dictStats = fdictGetContainerStats(connectionDocker, "container_abc")

    assert dictStats["bAvailable"] is True
    assert dictStats["dictDisk"]["bAvailable"] is False
    assert dictStats["dictDisk"]["sReason"] == "timeout"


def test_the_deadline_bounds_the_caller_and_not_the_worker():
    """The executor must not re-join the worker on the way out.

    ``with ThreadPoolExecutor(...)`` calls ``shutdown(wait=True)`` on
    exit, so a timeout reports a stall and then blocks for the operation's
    full duration anyway -- a bound that is not one. This asserts the
    elapsed time, which is the only thing that distinguishes the two.
    """
    import time
    connectionDocker = _StubConnectionDisk(
        dictUsage={"iTotalBytes": 1, "iUsedBytes": 0, "iFreeBytes": 1},
        fSleepSeconds=3.0,
    )
    from vaibify.gui import resourceMonitor
    with patch.object(
        resourceMonitor, "_F_DISK_QUERY_TIMEOUT_SECONDS", 0.05,
    ):
        fStarted = time.monotonic()
        tResult = resourceMonitor._ftReadFilesystemUsage(
            connectionDocker, "container_abc",
        )
        fElapsed = time.monotonic() - fStarted
    assert tResult[1] == "timeout"
    # The discriminator is "returned well before the 3s worker sleep":
    # a re-joining executor costs the full 3s, so any ceiling below
    # that keeps the test's power. 2.0s tolerates a capacity-bound
    # macOS runner (1.23s observed on CI, 2026-08-16) without
    # admitting the failure this test exists to catch.
    assert fElapsed < 2.0, (
        f"the deadline waited {fElapsed:.2f}s for a 3s operation it was "
        f"supposed to bound at 0.05s"
    )


def test_a_timeout_error_is_not_mistaken_for_a_daemon_failure():
    """The two degraded reasons stay distinguishable.

    concurrent.futures.TimeoutError inherits from Exception like every
    other failure here, so an except order that caught the general case
    first would silently relabel every stall as an unreachable daemon.
    """
    assert issubclass(concurrent.futures.TimeoutError, Exception)
