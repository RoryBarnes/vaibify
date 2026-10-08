"""The falsification routes past registration: the view, the refusals, the worker.

``tests/testFalsificationRoutesCoverage.py`` pins the helpers and
``tests/testCarrierMigratedRoutes.py`` the admission modes. What neither
drove is what a researcher sees: the expand-the-block GET, each refusal
the run button can meet, and the record the background worker leaves on
disk for each way a mutation session can end.

The routes are driven over the served application with a real lease,
and the step's applicability is judged for REAL from files the Docker
double holds -- no classification stub -- so a step that is not
falsifiable is refused for the reason the dashboard will print. The
worker is called directly, because it outlives the response and a
TestClient tears its loop down when the response returns.
"""

import asyncio
import contextlib
import json
import os
import posixpath
import threading
from types import SimpleNamespace

from unittest.mock import patch

import httpx
import pytest

from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testCarrierMigratedRoutes import (
    DockerDoubleServingALevelThreeWorkflow,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import S_CONTAINER_ID, S_WORKFLOW_PATH
from vaibify.config import mutationAdmission
from vaibify.gui import pipelineServer
from vaibify.gui.routes import falsificationRoutes
from vaibify.gui.testGenerator import (
    fsQuantitativeStandardsPath, fsQuantitativeTestPath,
)
from vaibify.reproducibility import falsificationAttestation
from vaibify.reproducibility.repoFiles import (
    HostRepoFiles,
    ffilesEnsureRepoFiles,
)


S_REPO = "/workspace"
S_STEP_DIRECTORY = "stepA"
S_QUANTITATIVE_TEST_FILE = posixpath.basename(
    fsQuantitativeTestPath(S_STEP_DIRECTORY))
S_QUANTITATIVE_STANDARDS_FILE = posixpath.basename(
    fsQuantitativeStandardsPath(S_STEP_DIRECTORY))
S_SCRIPT_NAME = "computeAlpha.py"
S_COSMIC_RAY_VERSION = "cosmic-ray 8.4.6"
S_SUMMARY_LINE = json.dumps({
    "iMutantsTotal": 4, "iMutantsKilled": 3, "iMutantsSurvived": 1,
    "listSurvivors": [{"sModule": "computeAlpha.py", "iLine": 7}],
})
DICT_DETERMINISTIC_STANDARDS = {
    "sStochasticityClassification": "deterministic",
    "listStandards": [{"sName": "valueAlpha", "fValue": 1.5}],
}


def _fdictStepFiles(sRoot, sStepDirectory=S_STEP_DIRECTORY):
    """Return ``{path: bytes}`` for a falsifiable step under ``sRoot``.

    The standards and test file are named by the generator's own path
    helpers, so each step's files carry that step's name.
    """
    sStep = posixpath.join(sRoot, sStepDirectory)
    return {
        posixpath.join(sStep, S_SCRIPT_NAME): b"fValue = 1.5\n",
        fsQuantitativeStandardsPath(sStep):
            json.dumps(DICT_DETERMINISTIC_STANDARDS).encode("utf-8"),
        fsQuantitativeTestPath(sStep):
            b"def testValue():\n    assert True\n",
    }


class _FalsifiableDocker(DockerDoubleServingALevelThreeWorkflow):
    """A repo-bound workflow whose container can answer the classifier."""

    def __init__(self, bCosmicRayInstalled=True):
        super().__init__()
        self.bCosmicRayInstalled = bCosmicRayInstalled
        self.listCommands = []

    def fbContainerPathIsFile(self, sContainerId, sPath):
        DockerDoubleServingALevelThreeWorkflow.fbContainerPathIsFile(
            self, sContainerId, sPath,
        )
        return sPath in self._dictFiles

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        tResult = DockerDoubleServingALevelThreeWorkflow.ftResultExecuteCommand(
            self, sContainerId, sCommand, sWorkdir,
        )
        self.listCommands.append(sCommand)
        if sCommand == "cosmic-ray --version":
            if self.bCosmicRayInstalled:
                return (0, S_COSMIC_RAY_VERSION + "\n")
            return (127, "bash: cosmic-ray: command not found")
        return tResult


def _fnMakeStepFalsifiable(client, connectionDocker):
    """Give the connected workflow's first step a Python data command."""
    connectionDocker._dictFiles.update(_fdictStepFiles(S_REPO))
    dictWorkflow = client.app.state.dictRouteContext["workflows"][
        S_CONTAINER_ID
    ]
    dictWorkflow["listSteps"][0]["saDataCommands"] = [
        "python " + S_SCRIPT_NAME,
    ]
    return dictWorkflow


@pytest.fixture(autouse=True)
def fixtureEmptyRunTracker(monkeypatch):
    """Each test starts with no in-flight run, and leaves none behind."""
    monkeypatch.setattr(falsificationRoutes, "_DICT_FALSIFICATION_TASKS", {})


def _fsViewPath(iStepIndex=0):
    return f"/api/steps/{S_CONTAINER_ID}/{iStepIndex}/falsification"


def _fsRunPath(iStepIndex=0):
    return f"/api/steps/{S_CONTAINER_ID}/{iStepIndex}/run-falsification"


# ── The view ──


def testAStepThatIsNotPythonIsNeverShownAsAttested():
    """A record on disk cannot turn a not-applicable step green."""
    client, connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    sRecordPath = posixpath.join(
        S_REPO, falsificationAttestation.fsFalsificationRecordRelativePath(
            S_STEP_DIRECTORY,
        ),
    )
    connectionDocker._dictFiles[sRecordPath] = json.dumps(
        {"sStatus": "attained", "sScriptDigest": "sha256:" + "f" * 64},
    ).encode("utf-8")
    responseHttp = client.get(_fsViewPath())
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["dictApplicability"]["bApplicable"] is False
    assert "not Python source" in dictBody["dictApplicability"]["sReason"]
    assert dictBody["dictRecord"]["sStatus"] == "attained"
    assert dictBody["bRecordCurrent"] is False
    assert dictBody["dictInFlight"] is None


def testARecordKeyedToTheCurrentSourcesReadsCurrentUntilEdited():
    client, connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    _fnMakeStepFalsifiable(client, connectionDocker)
    filesContainer = client.app.state.dictRouteContext["files"](
        S_CONTAINER_ID,
    )
    dictApplicability = (
        falsificationAttestation.fdictClassifyFalsificationApplicability(
            {"sDirectory": S_STEP_DIRECTORY,
             "saDataCommands": ["python " + S_SCRIPT_NAME]},
            filesContainer,
        )
    )
    sDigest = falsificationAttestation.fsCurrentFalsificationDigest(
        filesContainer,
        falsificationAttestation.flistFalsificationDigestPaths(
            dictApplicability,
        ),
    )
    assert sDigest.startswith("sha256:")
    sRecordPath = posixpath.join(
        S_REPO, falsificationAttestation.fsFalsificationRecordRelativePath(
            S_STEP_DIRECTORY,
        ),
    )
    connectionDocker._dictFiles[sRecordPath] = json.dumps(
        falsificationAttestation.fdictBuildFalsificationRecord(
            "attained", sDigest, "deterministic", 4, 4, 0,
        ),
    ).encode("utf-8")
    assert client.get(_fsViewPath()).json()["bRecordCurrent"] is True
    connectionDocker._dictFiles[
        posixpath.join(S_REPO, S_STEP_DIRECTORY, S_SCRIPT_NAME)
    ] = b"fValue = 2.5\n"
    assert client.get(_fsViewPath()).json()["bRecordCurrent"] is False


def testTheViewReportsARunInFlightForThisProjectStep():
    client, _connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    taskLive = SimpleNamespace(done=lambda: False)
    falsificationRoutes._DICT_FALSIFICATION_TASKS[
        (S_CONTAINER_ID, S_REPO, 0)
    ] = {"task": taskLive, "dictStatus": {"sPhase": "running"}}
    responseHttp = client.get(_fsViewPath())
    assert responseHttp.json()["dictInFlight"] == {"sPhase": "running"}


def testTheViewOfAStepThatDoesNotExistIsA404():
    client, _connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    responseHttp = client.get(_fsViewPath(7))
    assert responseHttp.status_code == 404
    assert responseHttp.json()["detail"] == "Step index 7 out of range"


# ── Refusals on the run button ──


def testARunOnAStepThatDoesNotExistIsA404():
    client, connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    responseHttp = client.post(_fsRunPath(3))
    assert responseHttp.status_code == 404
    assert "cosmic-ray --version" not in connectionDocker.listCommands


def testARunWithoutAProjectRepoHasNowhereToPutItsRecord():
    client, connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    client.app.state.dictRouteContext["workflows"][S_CONTAINER_ID][
        "sProjectRepoPath"
    ] = "  "
    responseHttp = client.post(_fsRunPath())
    assert responseHttp.status_code == 409
    assert "no project repo" in responseHttp.json()["detail"]
    assert "cosmic-ray --version" not in connectionDocker.listCommands


def testASecondRunOnTheSameStepIsRefusedByName():
    client, connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    falsificationRoutes._DICT_FALSIFICATION_TASKS[
        (S_CONTAINER_ID, S_REPO, 0)
    ] = {"task": SimpleNamespace(done=lambda: False),
         "dictStatus": {"sPhase": "running"}}
    responseHttp = client.post(_fsRunPath())
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == (
        "A falsification check is already running for this step."
    )
    assert "cosmic-ray --version" not in connectionDocker.listCommands


def testAStepThatIsNotFalsifiableIsRefusedWithItsReason():
    """The classifier runs for real; its first disqualifier is the answer."""
    client, connectionDocker = _tConnectGatedClient(_FalsifiableDocker())
    responseHttp = client.post(_fsRunPath())
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == (
        "Falsification check is not applicable: step computation is not "
        "Python source; there is nothing for mutation testing to mutate"
    )
    assert "cosmic-ray --version" not in connectionDocker.listCommands


def testAnImageWithoutCosmicRayIsRefusedWithTheRebuildRemedy():
    client, connectionDocker = _tConnectGatedClient(
        _FalsifiableDocker(bCosmicRayInstalled=False),
    )
    _fnMakeStepFalsifiable(client, connectionDocker)
    responseHttp = client.post(_fsRunPath())
    assert responseHttp.status_code == 409
    assert "cosmic-ray is not installed" in responseHttp.json()["detail"]
    assert "vaib build" in responseHttp.json()["detail"]
    assert "cosmic-ray --version" in connectionDocker.listCommands
    assert falsificationRoutes._DICT_FALSIFICATION_TASKS == {}


@contextlib.contextmanager
def _fnHoldTheWorkerOpen():
    """Replace the worker with one that waits until the test lets it go."""
    eventMayFinish = threading.Event()

    async def fnWaitThenReturn(*tArguments, **dictKeywords):
        while not eventMayFinish.is_set():
            await asyncio.sleep(0.005)

    with patch.object(
        falsificationRoutes, "_fnRunFalsificationWorker", fnWaitThenReturn,
    ):
        try:
            yield
        finally:
            eventMayFinish.set()


@pytest.mark.asyncio
async def testARunOnAnotherStepWhileOneRunsIsRefusedAsBusy():
    """cosmic-ray rewrites sources in place; two runs would corrupt both.

    The per-step check cannot see a run on a DIFFERENT step, so this
    refusal is the carrier's, and its words must say the container is
    busy rather than that this step is running.
    """
    connectionDocker = _FalsifiableDocker()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: connectionDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    clientAsync = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://hub",
        headers={"X-Session-Token": fsBootstrapCredential(app)},
    )
    async with clientAsync:
        responseConnect = await clientAsync.post(
            f"/api/connect/{S_CONTAINER_ID}",
            params={"sWorkflowPath": S_WORKFLOW_PATH},
        )
        assert responseConnect.status_code == 200, responseConnect.text
        clientAsync.headers["X-Vaibify-Lease"] = (
            responseConnect.json()["sLeaseId"]
        )
        dictWorkflow = app.state.dictRouteContext["workflows"][S_CONTAINER_ID]
        connectionDocker._dictFiles.update(_fdictStepFiles(S_REPO))
        dictSecondStep = dict(dictWorkflow["listSteps"][0])
        dictWorkflow["listSteps"][0]["saDataCommands"] = [
            "python " + S_SCRIPT_NAME,
        ]
        dictSecondStep.update({
            "sName": "Step B", "sDirectory": "stepB",
            "saDataCommands": ["python " + S_SCRIPT_NAME],
        })
        dictWorkflow["listSteps"].append(dictSecondStep)
        connectionDocker._dictFiles.update(
            _fdictStepFiles(S_REPO, "stepB"))
        with _fnHoldTheWorkerOpen():
            responseFirst = await clientAsync.post(_fsRunPath(0))
            assert responseFirst.status_code == 200, responseFirst.text
            responseSecond = await clientAsync.post(_fsRunPath(1))
        await asyncio.sleep(0.05)
    assert responseSecond.status_code == 409
    assert responseSecond.json()["detail"].startswith(
        "This container is busy: ",
    )


# ── The worker, called directly ──


class _MutationSessionDocker:
    """Answer the three container calls a mutation session makes."""

    def __init__(self, iExecExitCode=0, sSummaryStdout=S_SUMMARY_LINE,
                 errorOnWrite=None):
        self.iExecExitCode = iExecExitCode
        self.sSummaryStdout = sSummaryStdout
        self.errorOnWrite = errorOnWrite
        self.listCommands = []
        self.dictWritten = {}

    def ftRunInContainerStreamed(self, sContainerId, sCommand, **dictKeywords):
        self.listCommands.append(sCommand)
        if sCommand.startswith("cosmic-ray init"):
            return SimpleNamespace(
                iExitCode=self.iExecExitCode,
                sStdout="mutating computeAlpha.py\n",
                sStderr="" if self.iExecExitCode == 0 else "baseline failed",
            )
        if sCommand.startswith("python "):
            return SimpleNamespace(
                iExitCode=0, sStdout="progress\n" + self.sSummaryStdout,
                sStderr="",
            )
        return SimpleNamespace(iExitCode=0, sStdout="", sStderr="")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        if self.errorOnWrite is not None:
            raise self.errorOnWrite
        self.dictWritten[sPath] = baContent


class _UnwritableRepoFiles(HostRepoFiles):
    """A repository whose record write fails like a full disk would."""

    def fnWriteJsonAtomic(self, sRelPath, dictPayload):
        raise OSError("No space left on device")


def _tBuildWorkerInputs(tmp_path, connectionDocker, filesRepo=None):
    sRepo = str(tmp_path / "projectRepo")
    for sPath, baContent in _fdictStepFiles(sRepo).items():
        os.makedirs(os.path.dirname(sPath), exist_ok=True)
        with open(sPath, "wb") as fileOut:
            fileOut.write(baContent)
    dictStep = {
        "sName": "Step A", "sDirectory": S_STEP_DIRECTORY,
        "saDataCommands": ["python " + S_SCRIPT_NAME],
    }
    dictWorkflow = {"sProjectRepoPath": sRepo, "listSteps": [dictStep]}
    filesRepo = filesRepo or ffilesEnsureRepoFiles(sRepo)
    dictApplicability = (
        falsificationAttestation.fdictClassifyFalsificationApplicability(
            dictStep, filesRepo,
        )
    )
    assert dictApplicability["bApplicable"], dictApplicability
    dictCtx = {
        "docker": connectionDocker,
        "variables": lambda sContainerId: {"sRepoRoot": sRepo},
    }
    dictStatus = {"sPhase": "starting"}
    falsificationRoutes._DICT_FALSIFICATION_TASKS[
        (S_CONTAINER_ID, sRepo, 0)
    ] = {"task": None, "dictStatus": dictStatus}
    return (dictCtx, dictWorkflow, dictStep, dictApplicability,
            filesRepo, dictStatus, sRepo)


def _fnRunWorker(tInputs):
    dictCtx, dictWorkflow, dictStep, dictApplicability, filesRepo = (
        tInputs[:5]
    )
    asyncio.run(falsificationRoutes._fnRunFalsificationWorker(
        dictCtx, S_CONTAINER_ID, 0, dictWorkflow, dictStep,
        dictApplicability, filesRepo, S_COSMIC_RAY_VERSION,
    ))


def _fdictReadRecord(sRepo):
    return falsificationAttestation.fdictReadFalsificationRecord(
        sRepo, S_STEP_DIRECTORY,
    )


def testAGradedSessionIsRecordedAgainstTheSourcesItMutated(
    tmp_path,
):
    connectionDocker = _MutationSessionDocker()
    tInputs = _tBuildWorkerInputs(tmp_path, connectionDocker)
    sRepo, dictStatus, dictApplicability = tInputs[6], tInputs[5], tInputs[3]
    _fnRunWorker(tInputs)
    dictRecord = _fdictReadRecord(sRepo)
    assert dictRecord["sStatus"] == "attained"
    assert dictRecord["iMutantsTotal"] == 4
    assert dictRecord["fKillRate"] == pytest.approx(0.75)
    assert dictRecord["listSurvivors"] == [
        {"sModule": "computeAlpha.py", "iLine": 7},
    ]
    assert dictRecord["sCosmicRayVersion"] == S_COSMIC_RAY_VERSION
    assert dictRecord["sScriptDigest"] == (
        falsificationAttestation.fsCurrentFalsificationDigest(
            sRepo, falsificationAttestation.flistFalsificationDigestPaths(
                dictApplicability,
            ),
        )
    )
    assert dictStatus["sPhase"] == "attained"


def testTheSessionIsPreparedInScratchSpaceOutsideTheRepo(tmp_path):
    """The config names the step's own script and re-runs its data first."""
    connectionDocker = _MutationSessionDocker()
    tInputs = _tBuildWorkerInputs(tmp_path, connectionDocker)
    sRepo = tInputs[6]
    _fnRunWorker(tInputs)
    sWorkDirectory = "/tmp/vaibify-falsification/" + S_STEP_DIRECTORY
    assert connectionDocker.listCommands[0] == (
        f"rm -rf '{sWorkDirectory}' && mkdir -p '{sWorkDirectory}'"
    )
    sConfig = connectionDocker.dictWritten[
        sWorkDirectory + "/cosmic-ray.toml"
    ].decode("utf-8")
    assert posixpath.join(sRepo, S_STEP_DIRECTORY, S_SCRIPT_NAME) in sConfig
    assert "python " + S_SCRIPT_NAME in sConfig
    assert S_QUANTITATIVE_TEST_FILE in sConfig
    assert connectionDocker.dictWritten[
        sWorkDirectory + "/summarizeSession.py"
    ].decode("utf-8") == falsificationAttestation.S_SESSION_SUMMARY_SCRIPT
    assert connectionDocker.listCommands[1].startswith(
        f"cosmic-ray init '{sWorkDirectory}/cosmic-ray.toml' "
        f"'{sWorkDirectory}/session.sqlite' && cosmic-ray exec",
    )
    assert not any(
        sPath.startswith(sRepo) for sPath in connectionDocker.dictWritten
    )


def testAFailedCosmicRayRunIsRecordedWithItsExitAndOutput(
    tmp_path,
):
    connectionDocker = _MutationSessionDocker(iExecExitCode=2)
    tInputs = _tBuildWorkerInputs(tmp_path, connectionDocker)
    _fnRunWorker(tInputs)
    dictRecord = _fdictReadRecord(tInputs[6])
    assert dictRecord["sStatus"] == "error"
    assert dictRecord["sReason"] == (
        "cosmic-ray exited 2: mutating computeAlpha.py\nbaseline failed"
    )
    assert dictRecord["iMutantsTotal"] == 0
    assert dictRecord["sScriptDigest"].startswith("sha256:")
    assert tInputs[5]["sPhase"] == "error"


def testACrashedSessionBecomesAnErrorRecordNeverASilentHang(
    tmp_path,
):
    connectionDocker = _MutationSessionDocker(
        errorOnWrite=RuntimeError("container went away"),
    )
    tInputs = _tBuildWorkerInputs(tmp_path, connectionDocker)
    _fnRunWorker(tInputs)
    dictRecord = _fdictReadRecord(tInputs[6])
    assert dictRecord["sStatus"] == "error"
    assert dictRecord["sReason"] == (
        "falsification run crashed: container went away"
    )
    assert dictRecord["sScriptDigest"] == ""
    assert tInputs[5]["sPhase"] == "error"


def testACarrierRefusalIsRaisedAndNeverPersistedAsAMeasurement(
    tmp_path,
):
    """A fabricated error record would render beside real kill-rates."""
    connectionDocker = _MutationSessionDocker(
        errorOnWrite=mutationAdmission.MutationNotAdmittedError(
            "no admission for fnWriteFileViaTar",
        ),
    )
    tInputs = _tBuildWorkerInputs(tmp_path, connectionDocker)
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        _fnRunWorker(tInputs)
    assert _fdictReadRecord(tInputs[6]) is None
    assert tInputs[5]["sPhase"] == "running"


def testARecordThatCannotBeWrittenStillEndsTheRun(tmp_path):
    """The phase settles, so the dashboard stops showing it as running."""
    connectionDocker = _MutationSessionDocker()
    sRepo = str(tmp_path / "projectRepo")
    tInputs = _tBuildWorkerInputs(
        tmp_path, connectionDocker, filesRepo=_UnwritableRepoFiles(sRepo),
    )
    _fnRunWorker(tInputs)
    assert _fdictReadRecord(sRepo) is None
    assert tInputs[5]["sPhase"] == "attained"


def testASummaryWithNoGradedMutantsIsAnErrorNotAnAttainment(
    tmp_path,
):
    connectionDocker = _MutationSessionDocker(sSummaryStdout=json.dumps({
        "iMutantsTotal": 0, "iMutantsKilled": 0, "iMutantsSurvived": 0,
    }))
    tInputs = _tBuildWorkerInputs(tmp_path, connectionDocker)
    _fnRunWorker(tInputs)
    dictRecord = _fdictReadRecord(tInputs[6])
    assert dictRecord["sStatus"] == "error"
    assert dictRecord["sReason"] == (
        "cosmic-ray graded no mutants for this step"
    )


def testAFinishedRunEvictsOnlyItsOwnTrackerSlot():
    """A late callback must not evict a new run that took the slot."""
    async def fnExercise():
        tKey = (S_CONTAINER_ID, S_REPO, 0)
        taskFirst = asyncio.ensure_future(asyncio.sleep(0))
        falsificationRoutes._fnRegisterFalsificationTask(
            tKey, taskFirst, {"sPhase": "running"},
        )
        taskSecond = asyncio.ensure_future(asyncio.sleep(0.01))
        falsificationRoutes._DICT_FALSIFICATION_TASKS[tKey] = {
            "task": taskSecond, "dictStatus": {"sPhase": "running"},
        }
        await taskFirst
        await asyncio.sleep(0)
        bSecondKept = (
            falsificationRoutes._DICT_FALSIFICATION_TASKS[tKey]["task"]
            is taskSecond
        )
        await taskSecond
        return bSecondKept

    assert asyncio.run(fnExercise()) is True
