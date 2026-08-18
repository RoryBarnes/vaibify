"""The reconnect ladder is sized from the server's hold window.

These were two constants in two languages that had to agree and did
not. The browser retried a dropped socket for 31 seconds; the server
revoked the credential after 15 plus an evaluator pass. The attempts
that landed in the gap were refused 4401, the frontend read 4401 as
"unauthorized", and the researcher was told the server had restarted
and their session expired — while the server was healthy and the run
was still going.

The fix is not a better pair of numbers. It is that only one number
exists: the server sends its window at connect and the client derives
its schedule from that, so the two cannot drift apart.

The connect assertion drives real HTTP with the container name
distinct from its docker id, because a payload field is exactly the
kind of thing a unit stub will happily invent.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock
from vaibify.gui import pipelineServer, sessionLifecycle
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)
from tests.testNetworkEfficiencyFrontendContract import _fsReadStaticFile


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    """Redirect the host flock directory to a per-test tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def appHub():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


@pytest.fixture
def clientBrowser(appHub):
    return TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
    )


@pytest.mark.falsification
def test_connect_tells_the_browser_its_reconnect_window(clientBrowser):
    """The window reaches the client, over a real request.

    Kills: dropping ``fReconnectWindowSeconds`` from the connect
    payload. The client then falls back to its built-in default, which
    is the two-constants-that-must-agree arrangement this replaced —
    and it fails silently, because a browser with a stale default
    still reconnects, just for the wrong length of time.
    """
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    responseConnect = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": responseClaim.json()["sLeaseId"]},
    )
    assert responseConnect.status_code == 200, responseConnect.text
    dictPayload = responseConnect.json()
    assert "fReconnectWindowSeconds" in dictPayload, (
        "the browser cannot size its ladder to a window it was never "
        f"told: {sorted(dictPayload)}"
    )
    assert dictPayload["fReconnectWindowSeconds"] == (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS
    )
    assert dictPayload["fReconnectWindowSeconds"] > 0


def test_the_published_window_is_the_one_the_orphan_trigger_uses():
    """The accessor must not become a second, separate number."""
    assert sessionLifecycle.ffReconnectWindowSecondsForSession("") == (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS
    )


def _flistScheduleReconnectDelays(fWindowSeconds):
    """Return the delays the client schedules for a given window.

    A transcription of ``_ffNextReconnectDelaySeconds``. It is a
    transcription and therefore proves agreement with the algorithm
    only as long as somebody keeps it honest — the browser lane is
    what exercises the real thing. What it does prove, and what no
    source scan can, is the ARITHMETIC: that the schedule this shape
    of backoff produces actually terminates inside the window.
    """
    fMaxDelay = 30.0
    fMargin = 2.0
    listDelays = []
    fElapsed = 0.0
    iAttempt = 0
    while True:
        fDelay = min(2.0 ** iAttempt, fMaxDelay)
        if fElapsed + fDelay > fWindowSeconds - fMargin:
            return listDelays
        listDelays.append(fDelay)
        fElapsed += fDelay
        iAttempt += 1
        if iAttempt > 1000:
            raise AssertionError("backoff did not terminate")


@pytest.mark.parametrize(
    "fWindowSeconds", [15.0, 30.0, 60.0, 300.0, 900.0],
)
def test_every_scheduled_attempt_lands_inside_the_window(
    fWindowSeconds,
):
    """No attempt may be scheduled past the credential's life.

    An attempt landing after the window is not merely wasted: it is
    refused 4401, and a 4401 is reported as a dead session.
    """
    listDelays = _flistScheduleReconnectDelays(fWindowSeconds)
    assert listDelays, (
        f"a {fWindowSeconds}s window scheduled no attempt at all"
    )
    assert sum(listDelays) < fWindowSeconds, (
        f"the ladder {listDelays} totals {sum(listDelays)}s against a "
        f"{fWindowSeconds}s window — the tail is refused 4401 and "
        "misreported as an expired session"
    )


def test_a_longer_window_buys_more_attempts():
    """The point of deriving it: a long-lived lane retries longer."""
    iShort = len(_flistScheduleReconnectDelays(15.0))
    iLong = len(_flistScheduleReconnectDelays(900.0))
    assert iLong > iShort, (
        "a 15-minute window must produce a longer ladder than a "
        f"15-second one, got {iLong} vs {iShort}"
    )


def test_the_client_distinguishes_an_expired_window_from_a_refusal():
    """Three different facts must not share one message.

    "The server restarted", "the server refused you", and "we retried
    for as long as it promised and it expired" have different
    recoveries. The third used to be reported as the first.
    """
    sMonitor = _fsReadStaticFile("scriptConnectionMonitor.js")
    assert "windowExhausted" in sMonitor
    assert "still going on the server" in sMonitor, (
        "the expired-window message must say the run survived; that "
        "is the fact the researcher needs and the one the "
        "server-restarted message denied"
    )
    sSocket = _fsReadStaticFile("scriptWebSocket.js")
    assert "bWindowExhausted" in sSocket, (
        "the socket module must report WHY it stopped retrying"
    )
