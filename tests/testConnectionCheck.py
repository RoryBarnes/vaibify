"""Check connection: bounded probes that report what they saw.

Every test serves the deposit from the fault-injecting loopback server
and points both Zenodo services and the DOI resolver at it, so the check
reaches nothing beyond this machine. The bounds are the contract: a
server that ignores ``Range`` cannot make the check read more than its
cap, a server that stalls or trickles cannot hold it past its budget,
and what the check says is what it observed.
"""

import os
import threading
import time

import pytest

from tests.depositFaultServer import FaultInjectingDepositServer
from vaibify.reproducibility import connectionCheck, zenodoClient


I_FILE_BYTES = 3 * 1024 * 1024


@pytest.fixture
def fnPointEverythingAt(monkeypatch):
    """Make both Zenodo services and the DOI resolver resolve to a server."""
    def fnPoint(serverFault):
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": serverFault.sBase, "sandbox": serverFault.sBase,
        })
        monkeypatch.setattr(
            connectionCheck, "_S_DOI_PROBE_URL", serverFault.sBase + "/doi/",
        )
    return fnPoint


@pytest.fixture
def fnShortenTheBounds(monkeypatch):
    """A small budget and probe window, so a stall test takes seconds."""
    def fnShorten(fBudget=4.0, fProbe=2.0):
        monkeypatch.setattr(connectionCheck, "F_CHECK_BUDGET_SECONDS", fBudget)
        monkeypatch.setattr(connectionCheck, "F_THROUGHPUT_PROBE_SECONDS", fProbe)
        monkeypatch.setattr(connectionCheck, "_F_RESOLVER_PROBE_SECONDS", fProbe)
    return fnShorten


def _fdictSidecarFor(serverFault, iDurableBytes=0, bWithUrl=True):
    return {
        "sZenodoService": "zenodo",
        "sVersionDoi": "10.5281/zenodo.5150",
        "sFileUrl": serverFault.sFileUrl if bWithUrl else "",
        "iDurableBytes": iDurableBytes,
        "iExpectedBytes": I_FILE_BYTES,
    }


def _fsJoined(dictCheck):
    return " ".join(dictCheck["listObservations"] + dictCheck["listExplanations"])


@pytest.mark.falsification
def test_a_server_that_ignores_range_cannot_make_the_check_read_past_its_cap(
    fnPointEverythingAt,
):
    """Kills: the cap enforced on the header rather than on the bytes read."""
    baFile = os.urandom(I_FILE_BYTES)
    with FaultInjectingDepositServer(baFile) as serverFault:
        serverFault.dictBehavior["bIgnoreRange"] = True
        fnPointEverythingAt(serverFault)
        dictCheck = connectionCheck.fdictProbeArchiveConnection(
            _fdictSidecarFor(serverFault, iDurableBytes=2 * 1024 * 1024),
        )
    dictMeasured = dictCheck["dictMeasured"]
    assert dictMeasured["sServiceOutcome"] == "reached"
    assert dictMeasured["iStatus"] == 200
    assert dictMeasured["iBytesRead"] <= connectionCheck.I_PROBE_BYTES_CAP
    assert dictMeasured["iBytesRead"] > 0
    assert "Reached 127.0.0.1." in dictCheck["listObservations"][0]
    assert "Reached doi.org." in dictCheck["listObservations"]
    assert "would take about" in _fsJoined(dictCheck)


@pytest.mark.falsification
def test_a_stalling_server_cannot_hold_the_check_past_its_budget(
    fnPointEverythingAt, fnShortenTheBounds,
):
    """Kills: a check whose deadline does not reach a blocked read."""
    fnShortenTheBounds(fBudget=3.0, fProbe=30.0)
    baFile = os.urandom(256 * 1024)
    with FaultInjectingDepositServer(baFile) as serverFault:
        serverFault.dictBehavior["tStallAfterBytes"] = (4096, 60.0)
        fnPointEverythingAt(serverFault)
        fStarted = time.monotonic()
        try:
            dictCheck = connectionCheck.fdictProbeArchiveConnection(
                _fdictSidecarFor(serverFault),
            )
        finally:
            fElapsed = time.monotonic() - fStarted
            serverFault.eventRelease.set()
    assert fElapsed < 3.0 + 2.0, fElapsed
    assert dictCheck["dictMeasured"]["bOutOfTime"] is True
    assert "3 s limit" in _fsJoined(dictCheck)


def test_a_trickling_server_is_reported_as_slow_and_the_check_still_ends(
    fnPointEverythingAt, fnShortenTheBounds,
):
    fnShortenTheBounds(fBudget=6.0, fProbe=1.5)
    baFile = os.urandom(64 * 1024)
    with FaultInjectingDepositServer(baFile) as serverFault:
        serverFault.dictBehavior["tTrickle"] = (64, 0.1)
        fnPointEverythingAt(serverFault)
        fStarted = time.monotonic()
        try:
            dictCheck = connectionCheck.fdictProbeArchiveConnection(
                _fdictSidecarFor(serverFault),
            )
        finally:
            fElapsed = time.monotonic() - fStarted
            serverFault.eventRelease.set()
    dictMeasured = dictCheck["dictMeasured"]
    assert fElapsed < 6.0, fElapsed
    assert dictMeasured["sServiceOutcome"] == "reached"
    assert 0 < dictMeasured["fBytesPerSecond"] < 100_000
    assert "sent at" in _fsJoined(dictCheck)
    assert "continues, slowly" in _fsJoined(dictCheck)


def test_a_busy_answer_reports_the_wait_it_asked_for(fnPointEverythingAt):
    with FaultInjectingDepositServer(b"x" * 1024) as serverFault:
        serverFault.dictBehavior["listCannedAnswers"] = [
            (429, {"Retry-After": "60", "Content-Type": "text/plain"}, b"slow down"),
        ]
        fnPointEverythingAt(serverFault)
        dictCheck = connectionCheck.fdictProbeArchiveConnection(_fdictSidecarFor(serverFault))
    assert "asked vaibify to wait 60 s" in _fsJoined(dictCheck)
    assert "rate-limiting or in maintenance" in _fsJoined(dictCheck)


def test_a_web_page_instead_of_the_file_is_named(fnPointEverythingAt):
    with FaultInjectingDepositServer(b"<html>login</html>") as serverFault:
        serverFault.dictBehavior["sContentType"] = "text/html; charset=utf-8"
        fnPointEverythingAt(serverFault)
        dictCheck = connectionCheck.fdictProbeArchiveConnection(_fdictSidecarFor(serverFault))
    assert "web page instead of the file" in _fsJoined(dictCheck)


def test_an_unreachable_service_beside_a_reachable_resolver_says_so(
    fnPointEverythingAt, fnShortenTheBounds, monkeypatch,
):
    fnShortenTheBounds(fBudget=8.0, fProbe=2.0)
    with FaultInjectingDepositServer(b"x" * 1024) as serverFault:
        fnPointEverythingAt(serverFault)
        # A loopback port nobody listens on: the connect is refused at once.
        socketClosed = _fsocketReserveAClosedPort()
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": f"http://127.0.0.1:{socketClosed}",
            "sandbox": serverFault.sBase,
        })
        dictCheck = connectionCheck.fdictProbeArchiveConnection({
            **_fdictSidecarFor(serverFault),
            "sFileUrl": f"http://127.0.0.1:{socketClosed}/files/x?download=1",
        })
    sJoined = _fsJoined(dictCheck)
    assert "Could not reach 127.0.0.1" in sJoined
    assert "Reached doi.org." in dictCheck["listObservations"]
    assert "This computer is online" in sJoined


def test_nothing_reachable_reads_as_offline(fnShortenTheBounds, monkeypatch):
    fnShortenTheBounds(fBudget=8.0, fProbe=2.0)
    iPort = _fsocketReserveAClosedPort()
    monkeypatch.setattr(zenodoClient, "_SERVICES", {
        "zenodo": f"http://127.0.0.1:{iPort}", "sandbox": f"http://127.0.0.1:{iPort}",
    })
    monkeypatch.setattr(connectionCheck, "_S_DOI_PROBE_URL", f"http://127.0.0.1:{iPort}/")
    dictCheck = connectionCheck.fdictProbeArchiveConnection({
        "sZenodoService": "zenodo", "sVersionDoi": "10.5281/zenodo.1",
        "sFileUrl": f"http://127.0.0.1:{iPort}/files/x", "iDurableBytes": 0,
        "iExpectedBytes": 10,
    })
    assert "Could not reach doi.org." in dictCheck["listObservations"]
    assert "offline" in _fsJoined(dictCheck)


def test_a_download_with_no_address_yet_probes_the_service_root(fnPointEverythingAt):
    with FaultInjectingDepositServer(b"x" * 1024) as serverFault:
        fnPointEverythingAt(serverFault)
        dictCheck = connectionCheck.fdictProbeArchiveConnection(
            _fdictSidecarFor(serverFault, bWithUrl=False),
        )
    assert dictCheck["dictMeasured"]["sServiceOutcome"] == "reached"
    assert dictCheck["listObservations"][0].startswith("Reached 127.0.0.1.")


def _fsocketReserveAClosedPort():
    """Return a loopback port that was just closed, so a connect is refused."""
    import socket
    socketProbe = socket.socket()
    socketProbe.bind(("127.0.0.1", 0))
    iPort = socketProbe.getsockname()[1]
    socketProbe.close()
    return iPort


def test_the_route_refuses_a_path_as_an_id_and_answers_for_a_held_download(
    monkeypatch, tmp_path,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vaibify.gui.routes import connectionCheckRoutes
    from vaibify.reproducibility import downloadPartial
    monkeypatch.setattr(downloadPartial, "_S_DOWNLOAD_ROOT", str(tmp_path))
    app = FastAPI()
    connectionCheckRoutes.fnRegisterAll(app, {})
    clientHttp = TestClient(app)
    assert clientHttp.get("/api/connection-check/..%2Fetc").status_code == 404
    assert clientHttp.get("/api/connection-check/tmpabc").status_code == 404
    assert clientHttp.get("/api/connection-check/acquisition-missing").status_code == 404
    with FaultInjectingDepositServer(b"y" * 4096) as serverFault:
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": serverFault.sBase, "sandbox": serverFault.sBase,
        })
        monkeypatch.setattr(
            connectionCheck, "_S_DOI_PROBE_URL", serverFault.sBase + "/doi/",
        )
        dictAcquisition = downloadPartial.fdictOpenAcquisition({
            "sTarballName": "image.tar.zst", "sTarballSha256": "sha256:" + "a" * 64,
            "iTarballBytes": 4096, "sVersionDoi": "10.5281/zenodo.5150",
            "sZenodoService": "zenodo",
        }, {"sKind": "containerize", "sHostName": "demo"})
        downloadPartial.fnRecordSidecar(dictAcquisition, {"sFileUrl": serverFault.sFileUrl})
        sAcquisitionId = dictAcquisition["sAcquisitionId"]
        downloadPartial.fnReleaseAcquisition(dictAcquisition)
        responseHttp = clientHttp.get(f"/api/connection-check/{sAcquisitionId}")
    assert responseHttp.status_code == 200
    assert responseHttp.json()["listObservations"][0].startswith("Reached 127.0.0.1.")


def test_the_check_runs_the_resolver_probe_before_the_file_probe(
    fnPointEverythingAt,
):
    """The resolver first, under its own bound; the service after it.

    The service probe waits as long as the download does for a slow
    handshake, so run first it could spend the whole budget and leave
    the resolver -- the probe that tells "offline" from "Zenodo is
    unreachable" -- unasked.
    """
    with FaultInjectingDepositServer(b"z" * 2048) as serverFault:
        fnPointEverythingAt(serverFault)
        connectionCheck.fdictProbeArchiveConnection(_fdictSidecarFor(serverFault))
        listPaths = [dictRequest["sPath"] for dictRequest in serverFault.listRequests]
    assert listPaths[0] == "/doi/"
    assert listPaths[-1].startswith("/files/") or listPaths[-1] == serverFault.sFileUrl.split(
        serverFault.sBase)[1].split("?")[0]


def test_cancel_wakes_a_blocked_probe(fnPointEverythingAt, fnShortenTheBounds):
    """The deadline shares Cancel's path: shutting the live socket wakes the read."""
    fnShortenTheBounds(fBudget=2.0, fProbe=30.0)
    with FaultInjectingDepositServer(os.urandom(65536)) as serverFault:
        serverFault.dictBehavior["tStallAfterBytes"] = (100, 60.0)
        fnPointEverythingAt(serverFault)
        dictResult = {}

        def fnRun():
            dictResult["dictCheck"] = connectionCheck.fdictProbeArchiveConnection(
                _fdictSidecarFor(serverFault),
            )
        threadCheck = threading.Thread(target=fnRun, daemon=True)
        threadCheck.start()
        threadCheck.join(6.0)
        bEnded = not threadCheck.is_alive()
        serverFault.eventRelease.set()
    assert bEnded, "the check did not end within three times its budget"
    assert dictResult["dictCheck"]["dictMeasured"]["bOutOfTime"] is True
