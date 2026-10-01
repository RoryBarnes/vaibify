"""The researcher's sign-off is dated by the clock that dates the files.

Whether a plot changed after the sign-off is decided by comparing the
plot's mtime -- stamped by the CONTAINER's filesystem -- with
``sLastUserUpdate``. When the stamp came from the hub's wall clock (or,
earlier, the browser's), a hub clock running ahead of the container's
made a plot edited after the sign-off look older than it, the
modification was not noticed, and the changed plot's new hash was
adopted as the one the researcher had verified.

The double reports a container clock one hour BEHIND the hub's, so the
two clocks cannot be confused: the stored stamp must equal the
container's reading, and the freshness comparison, run with plot mtimes
from the container's clock, must call a later edit stale.
"""

import calendar
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.testAgentUpdateStepAllowlist import (
    ClockedDockerDouble,
    S_CONTAINER_ID,
    ftBuildLanes,
)
from vaibify.gui import fileStatusManager


def _fsClockOneHourBehindTheHub():
    dtBehind = datetime.now(timezone.utc) - timedelta(hours=1)
    return dtBehind.strftime("%Y-%m-%d %H:%M:%S UTC")


def _fiEpochOf(sClock):
    return calendar.timegm(time.strptime(sClock, "%Y-%m-%d %H:%M:%S UTC"))


class BehindDockerDouble(ClockedDockerDouble):
    sClockReading = _fsClockOneHourBehindTheHub()


class UnreadableClockDockerDouble(ClockedDockerDouble):
    sClockReading = None


def _fdictStoredVerification(clientBrowser):
    return clientBrowser.get(
        f"/api/steps/{S_CONTAINER_ID}/0").json()["dictVerification"]


@pytest.mark.falsification
def testTheSignOffIsStampedFromTheContainerClockNotTheHubs():
    """Kills: stepRoutes._fnStampServerSideUserUpdate: the container-clock
    read `_fsReadContainerClockUtc(...)` replaced by the hub's own
    `time.strftime(...)`-style wall-clock reading."""
    clientBrowser, _clientAgent, _app = ftBuildLanes(BehindDockerDouble)
    responseHttp = clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {"sUser": "passed"}},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert _fdictStoredVerification(clientBrowser)["sLastUserUpdate"] == (
        BehindDockerDouble.sClockReading)


def testAPlotEditedAfterTheSignOffIsNoticedDespiteAHubClockAhead():
    """The consequence, with plot mtimes from the container's clock."""
    clientBrowser, _clientAgent, _app = ftBuildLanes(BehindDockerDouble)
    clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {"sUser": "passed"}},
    )
    dictVerification = _fdictStoredVerification(clientBrowser)
    # The moment of the sign-off ON THE CONTAINER'S CLOCK, taken from
    # the double and not from the stored stamp, which is what is tested.
    iSignedOff = _fiEpochOf(BehindDockerDouble.sClockReading)
    dictStep = {
        "saPlotFiles": ["figure.pdf"], "dictVerification": dictVerification,
    }
    # Ten minutes after the sign-off by the container's clock: still
    # fifty minutes BEFORE the hub's, which is the case that adopted the
    # changed plot.
    dictModTimes = {"stepA/figure.pdf": str(iSignedOff + 600)}
    assert fileStatusManager._fbPlotNewerThanUserVerification(
        dictStep, ["stepA/figure.pdf"], dictModTimes,
    ) is True


def testAnUnreadableContainerClockRefusesTheSignOffInsteadOfGuessing():
    clientBrowser, _clientAgent, _app = ftBuildLanes(
        UnreadableClockDockerDouble)
    dictBefore = dict(_fdictStoredVerification(clientBrowser))
    responseHttp = clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"dictVerification": {"sUser": "passed"}},
    )
    assert responseHttp.status_code == 409, responseHttp.text
    assert "clock" in responseHttp.text
    assert _fdictStoredVerification(clientBrowser) == dictBefore


def testAnEditThatChangesNoSignOffDoesNotNeedTheClock():
    """A description edit must not depend on the container's clock."""
    clientBrowser, _clientAgent, _app = ftBuildLanes(
        UnreadableClockDockerDouble)
    responseHttp = clientBrowser.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json={"sDescription": "Fits the model."},
    )
    assert responseHttp.status_code == 200, responseHttp.text
