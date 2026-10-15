"""The tests and the sign-off follow the plot's bytes, never its clock.

The whole contract, driven against a real hub in host mode through the
routes and socket the dashboard uses, with the dashboard's own client
behavior and no client-side sign-off write anywhere:

* a full run that redraws a plot with new bytes leaves the step's tests
  passed (the run's marker hashed the new plot) and voids the sign-off;
* drawing the plots alone with new bytes leaves the tests not current
  and voids the sign-off;
* running the tests alone leaves the sign-off standing;
* a plot replaced with different bytes and an OLDER mtime is caught by
  content, for the tests and the sign-off alike;
* a byte-identical plot with a NEWER mtime changes nothing, on every
  poll response, including no transient ``stale``;
* a marker from before plots were hashed falls back to the timestamp,
  and says why;
* a sign-off over an unreadable or missing plot is refused, and a
  signed-off plot that becomes unreadable voids the sign-off;
* a byte-identical DATA file with a newer mtime changes nothing either:
  no modified-file report, no PROOF regression.

Every case asserts the state it starts from, then asserts on EACH poll
response after the action, because the dashboard renders each one. The
polls are the browser's: they carry the client's ``iWorkflowEpoch``, so
an up-to-date client is never re-sent the workflow, and the test reads
only what such a client renders -- the sign-off from
``dictSignOffByStep`` and the tests from ``dictTestMarkers``.
"""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from tests.testARunKeepsThePassingTestsItRecordedLive import (  # noqa: F401
    S_OUTPUT,
    S_PLOT_RELATIVE,
    S_PROJECT,
    S_STEP,
    _fnActAsTheDashboard,
    hubHost,
)

pytestmark = pytest.mark.exclusive

I_POLLS_PER_CASE = 5
# A tested, signed-off step with nothing else declared reaches Level 1.
I_LEVEL_SIGNED_OFF = 1
S_KEY = "dictUserVerifiedHashes"
S_REASON_NEWER = "outputs-newer-than-run"
T_TERMINAL_EVENTS = ("completed", "failed", "error")

# Draws the same bytes every time: a redraw of an unchanged figure.
_S_IDENTICAL_PLOT_SCRIPT = """import argparse, time
time.sleep(1.1)
parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
with open(args.output, "wb") as f:
    f.write(b"numbers plot, always the same")
"""


class _Dashboard:
    """One dashboard session: its socket, its saves and its polls."""

    def __init__(self, client, sRepo, websocket, dictBound):
        self.client = client
        self.sRepo = sRepo
        self.websocket = websocket
        self.sFingerprint = dictBound["sExactSourceFingerprint"]
        self.sWorkflowPath = dictBound["sWorkflowPath"]
        self.iWorkflowEpoch = -1

    def fsPlotPath(self):
        return os.path.join(self.sRepo, S_PLOT_RELATIVE)

    def flistRun(self, dictAction):
        """Dispatch an action as the dashboard does; return its events."""
        listEvents = self._flistSendAndFollow(dictAction)
        if listEvents and listEvents[-1].get("sReason") == "workflowSuperseded":
            self.sFingerprint = listEvents[-1]["sCurrentSourceFingerprint"]
            listEvents = self._flistSendAndFollow(dictAction)
        assert listEvents[-1]["sType"] == "completed", listEvents[-3:]
        return listEvents

    def _flistSendAndFollow(self, dictAction):
        self.websocket.send(json.dumps(dict(
            dictAction, sAcknowledgedSourceFingerprint=self.sFingerprint,
            sAcknowledgedWorkflowPath=self.sWorkflowPath)))
        listEvents, bStarted = [], False
        fDeadline = time.monotonic() + 300
        while time.monotonic() < fDeadline:
            dictEvent = json.loads(self.websocket.recv(timeout=300))
            if dictEvent.get("sType") == "started":
                bStarted = True
            if not bStarted and dictEvent.get("sType") != "runRefused":
                continue
            listEvents.append(dictEvent)
            _fnActAsTheDashboard(self.client, S_PROJECT, dictEvent)
            if dictEvent.get("sType") == "runRefused" or (
                    dictEvent.get("sType") in T_TERMINAL_EVENTS):
                return listEvents
        raise AssertionError(f"no terminal event: {listEvents[-3:]}")

    def fdictStoredVerification(self):
        response = self.client.get(f"/api/steps/{S_PROJECT}/0")
        assert response.status_code == 200, response.text
        return response.json()["dictVerification"]

    def fresponseSignOff(self):
        """Click verify as the dashboard does: PUT the block with sUser passed."""
        dictVerification = dict(self.fdictStoredVerification(), sUser="passed")
        response = self.client.put(
            f"/api/steps/{S_PROJECT}/0",
            json={"dictVerification": dictVerification})
        if response.status_code == 200:
            self.sFingerprint = response.json()["sExactSourceFingerprint"]
        return response

    def flistPoll(self, iCount=I_POLLS_PER_CASE):
        """Poll as the dashboard does; return what each response says."""
        listStates = []
        for _ in range(iCount):
            listStates.append(self.fdictPollOnce())
            time.sleep(1.0)
        return listStates

    def fdictPollOnce(self):
        """One poll carrying this client's epoch, as scriptPolling sends it."""
        response = self.client.get(
            f"/api/pipeline/{S_PROJECT}/file-status",
            params={"iWorkflowEpoch": self.iWorkflowEpoch})
        assert response.status_code == 200, response.text
        dictPoll = response.json()
        bResent = self.iWorkflowEpoch != -1 and dictPoll.get("dictWorkflow")
        self.iWorkflowEpoch = dictPoll["iWorkflowEpoch"]
        dictState = self._fdictStateOfPoll(dictPoll)
        dictState["bWorkflowResent"] = bool(bResent)
        return dictState

    def _fdictStateOfPoll(self, dictPoll):
        dictMarker = (dictPoll.get("dictTestMarkers") or {}).get("0") or {}
        dictIntegrity = (dictMarker.get("dictCategoryStates") or {}).get(
            "integrity") or {}
        dictStored = self.fdictStoredVerification()
        return {
            "sUnitTest": dictIntegrity.get("sState"),
            "bStale": dictMarker.get("bStale"),
            "sUser": (dictPoll.get("dictSignOffByStep") or {}).get(
                "0", {}).get("sUser"),
            "dictHashes": dictStored.get(S_KEY),
            "listModifiedFiles": dictStored.get("listModifiedFiles") or [],
            "iProofLevel": dictPoll.get("iProofLevel"),
            "sReason": dictIntegrity.get("sNotCurrentBecause", ""),
        }


def _fdashboardOpen(client, sRepo, fnBody):
    """Claim, connect and hold the pipeline socket open while fnBody runs."""
    from websockets.sync.client import connect
    responseClaim = client.post(f"/api/registry/{S_PROJECT}/claim")
    assert responseClaim.status_code == 200, responseClaim.text
    sLeaseId = responseClaim.json()["sLeaseId"]
    client.dictHeaders = {"X-Vaibify-Lease": sLeaseId}
    listWorkflows = client.get(f"/api/workflows/{S_PROJECT}").json()
    sWorkflowPath = [d for d in listWorkflows
                     if d["sPath"].endswith("project.json")][0]["sPath"]
    responseConnect = client.post(
        f"/api/connect/{S_PROJECT}", params={"sWorkflowPath": sWorkflowPath})
    assert responseConnect.status_code == 200, responseConnect.text
    sUrl = (f"ws://127.0.0.1:{client.iPort}/ws/pipeline/{S_PROJECT}"
            f"?sToken={client.sCredential}&sLeaseId={sLeaseId}")
    with connect(sUrl, origin=f"http://127.0.0.1:{client.iPort}",
                 open_timeout=30) as websocket:
        dictBound = json.loads(websocket.recv(timeout=60))
        assert dictBound["sType"] == "workflowBound", dictBound
        dashboard = _Dashboard(client, sRepo, websocket, dictBound)
        dashboard.fdictPollOnce()
        fnBody(dashboard)


def _fnAssertEvery(listStates, **dictExpected):
    dictExpected.setdefault("bWorkflowResent", False)
    for dictState in listStates:
        for sKey, expected in dictExpected.items():
            assert dictState[sKey] == expected, (sKey, listStates)


def _fnSetMtime(sPath, fMtime):
    os.utime(sPath, (fMtime, fMtime))


def _fsSha256(sPath):
    import hashlib
    with open(sPath, "rb") as fileIn:
        return hashlib.sha256(fileIn.read()).hexdigest()


def _fnRunTheFirstRunAndSignOff(dashboard):
    dashboard.flistRun({"sAction": "runAll"})
    _fnAssertEvery(dashboard.flistPoll(2), sUnitTest="passed")
    response = dashboard.fresponseSignOff()
    assert response.status_code == 200, response.text
    dictHashes = dashboard.fdictStoredVerification()[S_KEY]
    assert dictHashes == {S_PLOT_RELATIVE: _fsSha256(dashboard.fsPlotPath())}
    _fnAssertEvery(dashboard.flistPoll(2), sUnitTest="passed", sUser="passed",
                   iProofLevel=I_LEVEL_SIGNED_OFF)


def _fnCaseRunningTheTestsKeepsTheSignOff(dashboard):
    """Case c."""
    dashboard.flistRun({"sAction": "runAllTests"})
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="passed", sUser="passed")


def _fnCaseIdenticalBytesWithANewerMtimeChangeNothing(dashboard):
    """Case e."""
    dictBefore = dashboard.fdictStoredVerification()
    assert dictBefore["sUser"] == "passed" and dictBefore["sUnitTest"] == "passed"
    _fnSetMtime(dashboard.fsPlotPath(), time.time() + 120)
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="passed", sUser="passed",
                   dictHashes=dictBefore[S_KEY], listModifiedFiles=[],
                   iProofLevel=I_LEVEL_SIGNED_OFF)


def _fnCaseASignedOffPlotThatBecomesUnreadableVoidsTheSignOff(dashboard):
    """Case j: delivered to an up-to-date client, then healed."""
    dictBefore = dashboard.fdictStoredVerification()
    assert dictBefore["sUser"] == "passed"
    sPlot = dashboard.fsPlotPath()
    os.chmod(sPlot, 0)
    try:
        _fnAssertEvery(dashboard.flistPoll(2), sUser="stale",
                       dictHashes=dictBefore[S_KEY])
    finally:
        os.chmod(sPlot, stat.S_IRUSR | stat.S_IWUSR)
    _fnAssertEvery(dashboard.flistPoll(2), sUser="passed",
                   dictHashes=dictBefore[S_KEY])


def _fnCaseIdenticalDataWithANewerMtimeChangesNothing(dashboard):
    """Case i: no modified-file report and no PROOF regression."""
    dictBefore = dashboard.fdictPollOnce()
    assert dictBefore["sUser"] == "passed" and dictBefore["sUnitTest"] == "passed"
    assert dictBefore["iProofLevel"] == I_LEVEL_SIGNED_OFF, dictBefore
    sData = os.path.join(dashboard.sRepo, S_STEP, S_OUTPUT)
    _fnSetMtime(sData, time.time() + 120)
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="passed", sUser="passed",
                   listModifiedFiles=[], iProofLevel=I_LEVEL_SIGNED_OFF,
                   bStale=False)


def _fnCaseDifferentBytesWithAnOlderMtimeAreCaught(dashboard):
    """Case d."""
    dictBefore = dashboard.fdictStoredVerification()
    assert dictBefore["sUser"] == "passed" and dictBefore["sUnitTest"] == "passed"
    sPlot = dashboard.fsPlotPath()
    fOlder = os.stat(sPlot).st_mtime - 3600
    Path(sPlot).write_bytes(b"a plot replaced by hand")
    _fnSetMtime(sPlot, fOlder)
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="untested", sReason="drift",
                   sUser="stale", dictHashes=dictBefore[S_KEY], bStale=True)


def _fnCaseAFullRunWithNewBytesVoidsOnlyTheSignOff(dashboard):
    """Case a: re-sign the hand-replaced plot, then run everything."""
    assert dashboard.fdictStoredVerification()["sUser"] == "stale"
    assert dashboard.fresponseSignOff().status_code == 200
    dictBefore = dashboard.fdictStoredVerification()
    assert dictBefore["sUser"] == "passed"
    dashboard.flistRun({"sAction": "runAll"})
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="passed", sUser="stale",
                   dictHashes=dictBefore[S_KEY])


def _fnCaseDrawingThePlotsAloneVoidsBoth(dashboard):
    """Case b."""
    assert dashboard.fresponseSignOff().status_code == 200
    dictBefore = dashboard.fdictStoredVerification()
    assert dictBefore["sUser"] == "passed" and dictBefore["sUnitTest"] == "passed"
    dashboard.flistRun({"sAction": "runSelected", "listStepIndices": [0],
                        "sRunMode": "plotsOnly"})
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="untested", sReason="drift",
                   sUser="stale", dictHashes=dictBefore[S_KEY])


def _fnCaseASignOffOverAnUnreadablePlotIsRefused(dashboard):
    """Case g."""
    sPlot = dashboard.fsPlotPath()
    dictBefore = dashboard.fdictStoredVerification()
    assert dictBefore["sUser"] == "stale"
    os.chmod(sPlot, 0)
    try:
        response = dashboard.fresponseSignOff()
    finally:
        os.chmod(sPlot, stat.S_IRUSR | stat.S_IWUSR)
    assert response.status_code == 409, response.text
    assert S_PLOT_RELATIVE in response.text
    assert dashboard.fdictStoredVerification() == dictBefore
    os.rename(sPlot, sPlot + ".away")
    try:
        response = dashboard.fresponseSignOff()
    finally:
        os.rename(sPlot + ".away", sPlot)
    assert response.status_code == 409, response.text
    assert dashboard.fdictStoredVerification() == dictBefore


def _fnCaseAMarkerWithoutPlotDigestsFallsBackToTheClock(dashboard):
    """Case h: a marker written before plots were hashed, edited by hand."""
    dashboard.flistRun({"sAction": "runAll"})
    _fnAssertEvery(dashboard.flistPoll(2), sUnitTest="passed")
    listMarkers = list(Path(dashboard.sRepo, ".vaibify", "test_markers").rglob("*.json"))
    assert len(listMarkers) == 1, listMarkers
    dictMarker = json.loads(listMarkers[0].read_text())
    for dictRun in dictMarker["dictRuns"].values():
        assert dictRun["dictOutputHashes"].pop(S_PLOT_RELATIVE)
    listMarkers[0].write_text(json.dumps(dictMarker))
    _fnAssertEvery(dashboard.flistPoll(2), sUnitTest="passed")
    _fnSetMtime(dashboard.fsPlotPath(), time.time() + 120)
    _fnAssertEvery(dashboard.flistPoll(), sUnitTest="untested",
                   sReason=S_REASON_NEWER)


def test_the_tests_and_the_sign_off_follow_the_plot_bytes(hubHost):
    client, sRepo = hubHost

    def fnBody(dashboard):
        _fnRunTheFirstRunAndSignOff(dashboard)
        _fnCaseRunningTheTestsKeepsTheSignOff(dashboard)
        _fnCaseIdenticalBytesWithANewerMtimeChangeNothing(dashboard)
        _fnCaseASignedOffPlotThatBecomesUnreadableVoidsTheSignOff(dashboard)
        _fnCaseIdenticalDataWithANewerMtimeChangesNothing(dashboard)
        _fnCaseDifferentBytesWithAnOlderMtimeAreCaught(dashboard)
        _fnCaseAFullRunWithNewBytesVoidsOnlyTheSignOff(dashboard)
        _fnCaseDrawingThePlotsAloneVoidsBoth(dashboard)
        _fnCaseASignOffOverAnUnreadablePlotIsRefused(dashboard)
        _fnCaseAMarkerWithoutPlotDigestsFallsBackToTheClock(dashboard)
    _fdashboardOpen(client, sRepo, fnBody)


@pytest.mark.parametrize(
    "hubHost", [_S_IDENTICAL_PLOT_SCRIPT], indirect=True, ids=["identicalPlot"])
def test_redrawing_a_byte_identical_plot_keeps_the_tests_and_the_sign_off(hubHost):
    """Case f."""
    client, sRepo = hubHost

    def fnBody(dashboard):
        _fnRunTheFirstRunAndSignOff(dashboard)
        dictBaseline = dashboard.fdictStoredVerification()[S_KEY]
        dashboard.flistRun({"sAction": "runAll"})
        _fnAssertEvery(dashboard.flistPoll(), sUnitTest="passed",
                       sUser="passed", dictHashes=dictBaseline)
        dashboard.flistRun({"sAction": "runSelected", "listStepIndices": [0],
                            "sRunMode": "plotsOnly"})
        _fnAssertEvery(dashboard.flistPoll(), sUnitTest="passed",
                       sUser="passed", dictHashes=dictBaseline)
    _fdashboardOpen(client, sRepo, fnBody)
