"""A run's passing tests stay passed on the polls that follow it.

Reproduces, against a real hub in host mode and through the routes the
dashboard uses, what a researcher saw on 2026-10-14: Run all Steps ran
the step's tests, the test result event set the badge to passed, and
ten seconds after the run ended the poll reported a declared input file
as modified although nothing had changed it, and demoted the tests.
The cause was the step acknowledgement REPLACING the poll's mtime
baseline with the step's outputs alone, so every other path the poll
measures -- the declared inputs first -- was absent from the baseline
and read as changed on the next poll. The acknowledgement now merges.

A second demotion had the same face. The runner ran each step as data,
then tests, then plots, so every plot was written after the run its
tests recorded, and the first poll read the step's outputs as newer
than that run and demoted the tests. A step now runs data, plots, then
tests. The project's step therefore draws a templated plot
(``{sPlotDirectory}/numbers.{sFigureType}``) whose bytes differ on
every run, as a researcher's plot of new data does.

The test mimics the browser: it acknowledges each finished step and
saves each test result as the dashboard does -- the whole verification
block with the unit and category axes set, and never a sign-off write --
then polls as the dashboard does with the pipeline socket held open, and
records every poll's verdict for the step. Run at the commit before the
merge fix, it lists the input on every poll after the run; run at the
commit before the reorder, it reads the tests untested on poll 0.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from tests.sessionTokenTestHelper import fsBootstrapCredential


pytestmark = pytest.mark.exclusive

S_PROJECT = "inputHoldsProject"
S_STEP = "MakeNumbers"
S_INPUT = "input.json"
S_OUTPUT = "numbers.json"
S_PLOT_TEMPLATE = "{sPlotDirectory}/numbers.{sFigureType}"
S_PLOT_RELATIVE = "Plot/numbers.png"
S_COUNTER = "plotCounter.txt"
# A second step reads the first step's data through a step token, as a
# downstream analysis does; it never reads the first step's plot.
S_DOWNSTREAM = "SumNumbers"
S_DOWNSTREAM_OUTPUT = "total.json"
# A raw input of the step below. That step has no tests, so no test run
# ever records this file's digest: only the mtime baseline speaks for
# it, which is what keeps the acknowledgement's merge observable.
S_SCALE = "scale.json"
I_POLLS_AFTER_RUN = 20

_S_SCRIPT = """import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
with open(args.input) as f:
    d = json.load(f)
with open(args.output, "w") as f:
    json.dump({"listValues": [v * 2 for v in d["listValues"]]}, f)
"""

# Each run draws different bytes, as a plot of new data does: the
# counter beside the step advances on every call. The poll compares
# WHOLE-second mtimes, and a real plotting library takes over a second
# to import and draw; the pause stands in for that, so the plot's
# whole-second mtime is always later than any run that came before it.
_S_PLOT_SCRIPT = """import argparse, os, time
time.sleep(1.1)
parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
iRun = int(open("plotCounter.txt").read()) + 1 if os.path.exists("plotCounter.txt") else 1
with open("plotCounter.txt", "w") as f:
    f.write(str(iRun))
with open(args.output, "wb") as f:
    f.write(("numbers plot, run %d" % iRun).encode())
"""

_S_SUM_SCRIPT = """import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--scale", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
with open(args.input) as f:
    d = json.load(f)
with open(args.scale) as f:
    iScale = json.load(f)["iScale"]
with open(args.output, "w") as f:
    json.dump({"iTotal": iScale * sum(d["listValues"])}, f)
"""

_S_TEST = """import json, os
def test_numbers_were_written():
    with open("numbers.json") as f:
        assert json.load(f)["listValues"] == [2, 4, 6]
"""


def _fdictProjectDocument():
    return {
        "sPlotDirectory": "Plot", "sFigureType": "png", "iNumberOfCores": 1,
        "listSteps": [{
            "sName": S_STEP, "sStepId": "make-numbers", "sDirectory": S_STEP,
            "bRunEnabled": True, "bPlotOnly": False,
            "saDataCommands": [
                f"python3 makeNumbers.py --input ../{S_INPUT} --output {S_OUTPUT}",
            ],
            "saInputDataFiles": [S_INPUT],
            "saOutputDataFiles": [S_OUTPUT],
            "saTestCommands": ["python3 -m pytest tests/test_integrity_makeNumbers.py"],
            "saPlotCommands": [
                f"python3 makePlot.py --output {S_PLOT_TEMPLATE}",
            ],
            "saPlotFiles": [S_PLOT_TEMPLATE],
        }, {
            "sName": S_DOWNSTREAM, "sStepId": "sum-numbers",
            "sDirectory": S_DOWNSTREAM, "bRunEnabled": True,
            "bPlotOnly": False,
            "saDataCommands": [
                "python3 sumNumbers.py --input {step:make-numbers.numbers} "
                f"--scale ../{S_SCALE} --output {S_DOWNSTREAM_OUTPUT}",
            ],
            "saInputDataFiles": [S_SCALE],
            "saOutputDataFiles": [S_DOWNSTREAM_OUTPUT],
            "saTestCommands": [], "saPlotCommands": [], "saPlotFiles": [],
        }],
    }


def _fnGit(sRepo, *listArguments):
    subprocess.run(
        ["git", *listArguments], cwd=sRepo, check=True,
        capture_output=True, text=True,
    )


def _fsSeedProject(sHome, sPlotScript=None):
    sRepo = os.path.join(sHome, S_PROJECT)
    os.makedirs(os.path.join(sRepo, S_STEP, "tests"))
    os.makedirs(os.path.join(sRepo, ".vaibify", "projects"))
    Path(sRepo, "vaibify.yml").write_text(f"projectName: {S_PROJECT}\n")
    Path(sRepo, S_INPUT).write_text(json.dumps({"listValues": [1, 2, 3]}))
    Path(sRepo, S_SCALE).write_text(json.dumps({"iScale": 1}))
    Path(sRepo, S_STEP, "makeNumbers.py").write_text(_S_SCRIPT)
    Path(sRepo, S_STEP, "makePlot.py").write_text(sPlotScript or _S_PLOT_SCRIPT)
    os.makedirs(os.path.join(sRepo, S_DOWNSTREAM))
    Path(sRepo, S_DOWNSTREAM, "sumNumbers.py").write_text(_S_SUM_SCRIPT)
    Path(sRepo, ".gitignore").write_text(f"{S_STEP}/{S_COUNTER}\n")
    Path(sRepo, S_STEP, "tests", "test_integrity_makeNumbers.py").write_text(_S_TEST)
    Path(sRepo, ".vaibify", "projects", "project.json").write_text(
        json.dumps(_fdictProjectDocument(), indent=2))
    _fnGit(sRepo, "init", "-q")
    _fnGit(sRepo, "config", "user.email", "test@example.invalid")
    _fnGit(sRepo, "config", "user.name", "Test")
    _fnGit(sRepo, "add", "-A")
    _fnGit(sRepo, "commit", "-q", "-m", "seed")
    return sRepo


@pytest.fixture
def hubHost(monkeypatch, request):
    """The real hub, its state redirected, serving one host project.

    Indirect parametrization replaces the step's plot script.
    """
    from vaibify.config import (
        containerLock, operationJournal, preferencesStore, registryManager,
    )
    from vaibify.gui import agentCouncilStore, pipelineServer
    from tests.browser.conftest import _fnWaitUntilServing, fsocketBindLoopbackPort
    import requests
    import uvicorn
    pathRoot = Path(__file__).resolve().parent.parent / ".browserLaneTmp"
    pathRoot.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(pathRoot)) as sHome:
        sRegistry = os.path.join(sHome, "registry.json")
        Path(sRegistry).write_text(json.dumps({"listProjects": []}))
        for moduleTarget, sName, sValue in (
            (registryManager, "_S_REGISTRY_DIRECTORY", sHome),
            (registryManager, "_S_REGISTRY_PATH", sRegistry),
            (registryManager, "_S_LOCK_PATH", os.path.join(sHome, "registry.lock")),
            (preferencesStore, "_S_PREFERENCES_DIRECTORY", sHome),
            (preferencesStore, "_S_PREFERENCES_PATH", os.path.join(sHome, "preferences.json")),
            (preferencesStore, "_S_LOCK_PATH", os.path.join(sHome, "preferences.lock")),
            (operationJournal, "_S_JOURNAL_DIRECTORY", os.path.join(sHome, "journal")),
            (containerLock, "_S_LOCK_DIRECTORY", os.path.join(sHome, "locks")),
        ):
            monkeypatch.setattr(moduleTarget, sName, sValue)
        sRepo = _fsSeedProject(sHome, getattr(request, "param", None))
        registryManager.fnAddProject(sRepo, sMode="host")
        socketListening = fsocketBindLoopbackPort()
        iPort = socketListening.getsockname()[1]
        appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=iPort)
        appHub.state.dictCouncilCampaignStore = (
            agentCouncilStore.fdictCreateCampaignStore(
                sDurableStoreRoot=os.path.join(sHome, "agentCouncils")))
        server = uvicorn.Server(uvicorn.Config(
            appHub, host="127.0.0.1", port=iPort, log_level="warning"))
        threadServer = threading.Thread(
            target=server.run, kwargs={"sockets": [socketListening]}, daemon=True)
        threadServer.start()
        _fnWaitUntilServing(iPort)
        sCredential = fsBootstrapCredential(appHub)
        sessionHttp = requests.Session()
        sessionHttp.headers["X-Session-Token"] = sCredential
        # uvicorn closes a connection idle for its keep-alive timeout
        # (five seconds by default), and a request sent on it in that
        # instant is reset; requests never retries a POST. A browser
        # retries; this client opens a connection per request instead.
        sessionHttp.headers["Connection"] = "close"
        sBaseUrl = f"http://127.0.0.1:{iPort}"

        class HubClient:
            def __init__(self):
                self.iPort = iPort
                self.sCredential = sCredential
                self.dictHeaders = {}

            def get(self, sPath, **dictArguments):
                return sessionHttp.get(
                    sBaseUrl + sPath, timeout=120, headers=self.dictHeaders,
                    **dictArguments)

            def post(self, sPath, **dictArguments):
                return sessionHttp.post(
                    sBaseUrl + sPath, timeout=300, headers=self.dictHeaders,
                    **dictArguments)

            def put(self, sPath, **dictArguments):
                return sessionHttp.put(
                    sBaseUrl + sPath, timeout=120, headers=self.dictHeaders,
                    **dictArguments)
        try:
            yield HubClient(), sRepo
        finally:
            server.should_exit = True
            threadServer.join(timeout=15)
            socketListening.close()


def _fdictStepVerdict(dictPoll):
    """Return the first step's verification as the poll carries it."""
    for sKey in ("dictWorkflow", "dictWorkflowEnvelope"):
        dictWorkflow = dictPoll.get(sKey) or {}
        listSteps = dictWorkflow.get("listSteps") or []
        if listSteps:
            return dict(listSteps[0].get("dictVerification") or {})
    listSteps = dictPoll.get("listSteps") or []
    if listSteps:
        return dict(listSteps[0].get("dictVerification") or {})
    return {"_sPollKeys": sorted(dictPoll.keys())}


_T_CATEGORY_AXES = (
    ("dictIntegrity", "sIntegrity"),
    ("dictQualitative", "sQualitative"),
    ("dictQuantitative", "sQuantitative"),
)


def _fnSaveTestResultAsTheDashboard(client, sProject, dictEvent):
    """Save a test result as ``fnHandleTestResult`` does.

    The browser sets the unit axis and every category axis that has
    commands, then PUTs its WHOLE verification block, which the route
    stores in place of the old one. It never writes the sign-off here.
    """
    iStep = dictEvent["iStepNumber"] - 1
    responseStep = client.get(f"/api/steps/{sProject}/{iStep}")
    assert responseStep.status_code == 200, responseStep.text
    dictStep = responseStep.json()
    dictVerification = dict(dictStep.get("dictVerification") or {
        "sUnitTest": "untested", "sUser": "untested"})
    dictVerification["sUnitTest"] = dictEvent["sResult"]
    dictCategoryResults = dictEvent.get("dictCategoryResults") or {}
    for sCategoryKey, sVerifyKey in _T_CATEGORY_AXES:
        dictCategory = (dictStep.get("dictTests") or {}).get(sCategoryKey) or {}
        if not dictCategory.get("saCommands"):
            continue
        entryResult = dictCategoryResults.get(sCategoryKey)
        if isinstance(entryResult, dict):
            dictVerification[sVerifyKey] = entryResult.get("sStatus") or dictEvent["sResult"]
        elif isinstance(entryResult, str):
            dictVerification[sVerifyKey] = entryResult
        else:
            dictVerification[sVerifyKey] = dictEvent["sResult"]
    dictUpdate = {"dictVerification": dictVerification}
    if dictStep.get("dictTests"):
        dictUpdate["dictTests"] = dictStep["dictTests"]
    response = client.put(f"/api/steps/{sProject}/{iStep}", json=dictUpdate)
    assert response.status_code == 200, response.text


def _fnActAsTheDashboard(client, sProject, dictEvent):
    """Do what the dashboard does on the events the run emits."""
    if dictEvent.get("sType") == "testResult":
        _fnSaveTestResultAsTheDashboard(client, sProject, dictEvent)
    if dictEvent.get("sType") == "stepPass":
        iStep = dictEvent["iStepNumber"] - 1
        response = client.post(f"/api/pipeline/{sProject}/acknowledge-step/{iStep}")
        assert response.status_code == 200, response.text


def _flistRunAllAsTheDashboard(client, sProject, sLeaseId):
    from websockets.sync.client import connect
    sUrl = (f"ws://127.0.0.1:{client.iPort}/ws/pipeline/{sProject}"
            f"?sToken={client.sCredential}&sLeaseId={sLeaseId}")
    listEvents = []
    with connect(sUrl, origin=f"http://127.0.0.1:{client.iPort}", open_timeout=30) as websocket:
        dictBound = json.loads(websocket.recv(timeout=60))
        assert dictBound["sType"] == "workflowBound", dictBound
        websocket.send(json.dumps({
            "sAction": "runAll",
            "sAcknowledgedSourceFingerprint": dictBound["sExactSourceFingerprint"],
            "sAcknowledgedWorkflowPath": dictBound["sWorkflowPath"],
        }))
        fDeadline = time.monotonic() + 300
        while time.monotonic() < fDeadline:
            dictEvent = json.loads(websocket.recv(timeout=300))
            listEvents.append(dictEvent)
            _fnActAsTheDashboard(client, sProject, dictEvent)
            if dictEvent.get("sType") in ("completed", "failed", "runRefused", "error"):
                break
        # The dashboard keeps its pipeline socket open after a run; the
        # polls that follow are made while it is held, as the browser's are.
        listHistory = _flistPollAfterTheRun(client, sProject)
    return listEvents, listHistory


def _flistPollAfterTheRun(client, sProject):
    """Poll as scriptPolling does, carrying the client's workflow epoch.

    An up-to-date client is not re-sent the workflow, so the step's
    verdict is read back from the server when the poll carries none.
    """
    listHistory = []
    iWorkflowEpoch = -1
    for iPoll in range(I_POLLS_AFTER_RUN):
        response = client.get(
            f"/api/pipeline/{sProject}/file-status",
            params={"iWorkflowEpoch": iWorkflowEpoch})
        if response.status_code != 200:
            listHistory.append((iPoll, f"HTTP {response.status_code}: {response.text[:200]}", None))
            print("POLL", listHistory[-1])
            time.sleep(1.0)
            continue
        dictPoll = response.json()
        iWorkflowEpoch = dictPoll["iWorkflowEpoch"]
        dictVerdict = _fdictStepVerdict(dictPoll)
        if "_sPollKeys" in dictVerdict:
            dictVerdict = client.get(f"/api/steps/{sProject}/0").json()[
                "dictVerification"]
        dictDownstream = client.get(f"/api/steps/{sProject}/1").json()[
            "dictVerification"]
        listHistory.append((iPoll, dictVerdict.get("sUnitTest"),
                            (dictVerdict.get("listModifiedFiles") or [])
                            + (dictDownstream.get("listModifiedFiles") or [])))
        print("POLL", listHistory[-1], "epoch=", dictPoll.get("iWorkflowEpoch"),
              "reloaded=", dictPoll.get("bWorkflowReloaded"))
        time.sleep(1.0)
    return listHistory


@pytest.mark.falsification
def test_a_run_keeps_the_passing_tests_it_recorded(hubHost):
    """Kills: the acknowledgement replacing the mtime baseline with the
    step's outputs, which reports every declared input as modified on
    the next poll and demotes the tests that just passed; and the
    runner drawing a step's plots after its tests, which leaves every
    plot newer than the run the tests recorded.
    """
    client, sRepo = hubHost
    dictClaim = client.post(f"/api/registry/{S_PROJECT}/claim")
    assert dictClaim.status_code == 200, dictClaim.text
    sLeaseId = dictClaim.json()["sLeaseId"]
    client.dictHeaders = {"X-Vaibify-Lease": sLeaseId}
    listWorkflows = client.get(f"/api/workflows/{S_PROJECT}").json()
    sWorkflowPath = [d for d in listWorkflows if d["sPath"].endswith("project.json")][0]["sPath"]
    responseConnect = client.post(
        f"/api/connect/{S_PROJECT}", params={"sWorkflowPath": sWorkflowPath})
    assert responseConnect.status_code == 200, responseConnect.text
    for _ in range(3):
        client.get(f"/api/pipeline/{S_PROJECT}/file-status").json()
        time.sleep(1.0)

    listEvents, listHistory = _flistRunAllAsTheDashboard(client, S_PROJECT, sLeaseId)
    assert listEvents[-1]["sType"] == "completed", listEvents[-3:]
    listTestResults = [e for e in listEvents if e.get("sType") == "testResult"]
    assert listTestResults and listTestResults[0]["sResult"] == "passed", listEvents
    assert Path(sRepo, S_PLOT_RELATIVE).is_file(), "the plot command drew nothing"
    assert all(sUnitTest == "passed" for _, sUnitTest, _ in listHistory), listHistory
    assert not any(S_INPUT in (listModified or []) for _, _, listModified in listHistory), (
        listHistory)
    assert not any(S_SCALE in listModified for _, _, listModified in listHistory), (
        listHistory)
