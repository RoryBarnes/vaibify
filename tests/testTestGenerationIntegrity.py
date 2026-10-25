"""Generated tests judge something real, or they say why they cannot.

Test generation against outputs that did not exist wrote standards that
judged nothing, and the placeholder tests built on them asserted True:
"2 passed, 1 skipped" for a step that had never run (alpha tester,
2026-10-23). The rulings, not the code, are the oracle here:

* R2 -- missing outputs refuse outright: nothing written, the tests
  directory included; no model asked; the container not quarantined;
  and when an agent asked, the researcher is told in the dashboard;
* R3 -- a tier with nothing to check is N/A (no file, the existing
  "unnecessary" axis), and N/A means inapplicable, never unknown;
* R5 -- regenerating into N/A removes vaibify's own unedited files and
  asks before touching anything a researcher edited;
* a malformed standards file is refused at vaibify's write boundary and
  reported, by name, when a test run meets one written elsewhere.

Driven over real HTTP through the real carrier and journal, with the
agent presenting its container's own minted token.
"""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from tests.testCarrierMigratedRoutes import (
    DockerDoubleThatCallsTheRealGates,
    S_INTROSPECT_COMMAND_MARKER,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import (
    DICT_WORKFLOW,
    S_CONTAINER_ID,
    S_PROJECT_REPO,
    S_WORKFLOW_PATH,
)
from vaibify.config import operationJournal
from vaibify.gui import (
    actionCatalog,
    deterministicTestWriter,
    agentClientFreshness,
    researcherNotices,
    testGenerator,
)
from vaibify.gui.webSocketAuthorization import fbContainerIsPoisoned


S_STEP = S_PROJECT_REPO + "/stepA"
S_TESTS = S_STEP + "/tests"
S_ARRAY_OUTPUT = "values.npy"
S_TABLE_OUTPUT = "table.csv"
S_QUALITATIVE_TEST = testGenerator.fsQualitativeTestPath(S_STEP)
S_QUALITATIVE_STANDARDS = testGenerator.fsQualitativeStandardsPath(S_STEP)
S_QUANTITATIVE_STANDARDS = testGenerator.fsQuantitativeStandardsPath(S_STEP)
S_MALFORMED_STANDARDS = (
    '{"listStandards": [{"sName": "a", "fValue": 1}. '
    '{"sName": "b", "fValue": 2}]}'
)


def _fdictArrayReport():
    """A numeric array, as the introspection program reports one."""
    return {
        "sFileName": S_ARRAY_OUTPUT, "sFormat": "npy", "bExists": True,
        "iByteSize": 160, "bLoadable": True, "sError": "", "tShape": [4],
        "sDtype": "float64", "iNanCount": 0, "iInfCount": 0,
        "listColumnNames": [], "listJsonTopKeys": [],
        "listBenchmarks": [{
            "sName": "fFirst", "sDataFile": S_ARRAY_OUTPUT,
            "sAccessPath": "index:0", "fValue": 0.5,
        }],
    }


def _fdictTableReport():
    """A table with named columns, which qualitative checks can pin."""
    return {
        "sFileName": S_TABLE_OUTPUT, "sFormat": "csv", "bExists": True,
        "iByteSize": 64, "bLoadable": True, "sError": "",
        "tShape": [3, 2], "listColumnNames": ["time", "flux"],
        "bHasHeader": True, "listJsonTopKeys": [],
        "listBenchmarks": [{
            "sName": "fTimeFirst", "sDataFile": S_TABLE_OUTPUT,
            "sAccessPath": "column:time,index:0", "fValue": 0.0,
        }],
    }


def _fdictUnloadableReport():
    """An output that exists but is in a format nothing can read."""
    return {
        "sFileName": S_ARRAY_OUTPUT, "sFormat": "", "bExists": True,
        "iByteSize": 64, "bLoadable": False,
        "sError": "unsupported binary format", "listColumnNames": [],
        "listJsonTopKeys": [], "listBenchmarks": [],
    }


class DockerDoubleForGeneration(DockerDoubleThatCallsTheRealGates):
    """One step, one declared output; the introspection answer is settable."""

    def __init__(self, sOutput=S_ARRAY_OUTPUT, bOutputExists=True,
                 dictReport=None):
        super().__init__()
        self.sOutput = sOutput
        self.dictReport = dictReport or _fdictArrayReport()
        if bOutputExists:
            self.setExistingPaths.add(S_STEP + "/" + sOutput)

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath == S_WORKFLOW_PATH and sPath not in self._dictFiles:
            dictWorkflow = copy.deepcopy(DICT_WORKFLOW)
            dictWorkflow["listSteps"][0]["saOutputDataFiles"] = [self.sOutput]
            self._dictFiles[sPath] = json.dumps(dictWorkflow).encode("utf-8")
        return super().fbaFetchFile(sContainerId, sPath, iMaxBytes)

    def fnWriteFile(self, sContainerId, sPath, baContent, **dictKeywords):
        super().fnWriteFile(sContainerId, sPath, baContent, **dictKeywords)
        self.setExistingPaths.add(sPath)

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        tExec = super().ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir)
        if (S_INTROSPECT_COMMAND_MARKER in sCommand
                and sCommand.startswith("python3 ")):
            return (0, json.dumps([self.dictReport]))
        if sCommand.startswith("rm -f -- "):
            for sPath in list(self._dictFiles):
                if f"'{sPath}'" in sCommand:
                    del self._dictFiles[sPath]
                    self.setExistingPaths.discard(sPath)
        return tExec


@pytest.fixture
def listModelCalls(monkeypatch):
    """Count every language-model call; none should ever happen here."""
    listCalls = []

    def fsRecordTheCall(*args, **kwargs):
        listCalls.append(1)
        return "{}"

    monkeypatch.setattr(testGenerator, "_fsInvokeLlm", fsRecordTheCall)
    return listCalls


def _fclientAgentFor(client):
    sAgentToken = next(
        iter(client.app.state.dictContainerOwners.values())
    ).sAgentToken
    assert sAgentToken and sAgentToken != client.headers.get(
        "X-Session-Token")
    return TestClient(client.app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: sAgentToken,
        agentClientFreshness.S_CLIENT_DIGEST_HEADER:
            agentClientFreshness.fsShippedClientDigest(),
        "Host": "host.docker.internal:8050",
    }, raise_server_exceptions=False)


def _fresponseGenerate(client, bAgentRoute, bForceOverwrite=False):
    if bAgentRoute:
        return _fclientAgentFor(client).post(
            f"/api/steps/{S_CONTAINER_ID}/0/generate-test-deterministic",
            json={},
        )
    return client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/generate-test",
        json={"bDeterministic": True, "bForceOverwrite": bForceOverwrite},
    )


def _fdictServedStep(client):
    return client.app.state.dictRouteContext["workflows"][
        S_CONTAINER_ID]["listSteps"][0]


def _fsContainerName(client):
    return next(iter(client.app.state.dictContainerOwners))


def _flistWritesUnder(connectionDocker, sPrefix):
    return [
        dictReached for dictReached in connectionDocker.listAdmittedPrimitives
        if sPrefix in (dictReached.get("sPath") or "")
        or sPrefix in (dictReached.get("sCommand") or "")
    ]


# ------------------------------------------------- R2: missing outputs


@pytest.mark.falsification
@pytest.mark.parametrize("bAgentRoute", [False, True])
def testGeneratingBeforeTheOutputsExistIsRefused(
        listModelCalls, bAgentRoute):
    """Kills: deriving tests without asking whether the outputs exist.

    Both routes refuse by name; nothing is written, the tests directory
    included; the workflow is unchanged; no model is asked; the
    container is not quarantined and no write is left in the journal.
    """
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration(bOutputExists=False))
    dictStepBefore = copy.deepcopy(_fdictServedStep(client))
    response = _fresponseGenerate(client, bAgentRoute)
    assert response.status_code == 409, response.text
    dictDetail = response.json()["detail"]
    assert dictDetail["sRefusal"] == "outputs-missing"
    assert dictDetail["listMissingFiles"] == [S_ARRAY_OUTPUT]
    assert S_ARRAY_OUTPUT in dictDetail["sMessage"]
    assert _flistWritesUnder(connectionDocker, S_TESTS) == []
    assert _fdictServedStep(client) == dictStepBefore
    assert listModelCalls == []
    assert not fbContainerIsPoisoned(
        client.app.state.dictContainerOwners, _fsContainerName(client))
    assert operationJournal.fdictReadJournalOutcome(
        _fsContainerName(client)).get("dictOperations", {}) == {}


def testAnAgentsRefusalIsShownToTheResearcher():
    client, _ = _tConnectGatedClient(
        DockerDoubleForGeneration(bOutputExists=False))
    _fresponseGenerate(client, bAgentRoute=True)
    listNotices = researcherNotices.flistNoticesAfter(
        client.app.state.dictRouteContext, S_CONTAINER_ID, 0)
    assert len(listNotices) == 1
    assert listNotices[0]["sLevel"] == "error"
    assert S_ARRAY_OUTPUT in listNotices[0]["sMessage"]
    assert "Nothing was written" in listNotices[0]["sMessage"]


def testAResearchersRefusalQueuesNoNotice():
    client, _ = _tConnectGatedClient(
        DockerDoubleForGeneration(bOutputExists=False))
    _fresponseGenerate(client, bAgentRoute=False)
    assert researcherNotices.flistNoticesAfter(
        client.app.state.dictRouteContext, S_CONTAINER_ID, 0) == []


@pytest.mark.falsification
def testAMissingOutputRefusalOpensNoCarrier(monkeypatch):
    """Kills: leaving the refusal to the generator, inside the carrier.

    The route answers from a typed read before any drain is taken, so a
    step that has not run never holds the container while it is refused.
    """
    from vaibify.gui import commitCarrier
    listCarrierCalls = []
    fnReal = commitCarrier.fdictRunLockHeldMutation

    async def fdictRecordTheCarrier(*args, **kwargs):
        listCarrierCalls.append(args)
        return await fnReal(*args, **kwargs)

    client, _ = _tConnectGatedClient(
        DockerDoubleForGeneration(bOutputExists=False))
    monkeypatch.setattr(
        commitCarrier, "fdictRunLockHeldMutation", fdictRecordTheCarrier)
    response = _fresponseGenerate(client, bAgentRoute=False)
    assert response.status_code == 409, response.text
    assert listCarrierCalls == []


@pytest.mark.falsification
def testAnOutputTheAnalysisCannotFindIsRefused(listModelCalls):
    """The probe saw the file; the analysis did not. Still a refusal.

    Kills: writing standards from a report that says the file is gone.
    """
    client, connectionDocker = _tConnectGatedClient(DockerDoubleForGeneration(
        dictReport={
            "sFileName": S_ARRAY_OUTPUT, "bExists": False,
            "bLoadable": False, "sError": "file not found",
            "listColumnNames": [], "listJsonTopKeys": [],
            "listBenchmarks": [],
        }))
    response = _fresponseGenerate(client, bAgentRoute=False)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["listMissingFiles"] == [S_ARRAY_OUTPUT]
    assert _flistWritesUnder(connectionDocker, S_TESTS + "/") == []
    assert listModelCalls == []


@pytest.mark.falsification
def testAnOutputThatVanishesUnderTheRouteIsRefusedWithoutQuarantine(
        monkeypatch, listModelCalls):
    """The generator's own check, inside the carrier, carries its refusal.

    Kills: letting the generator's refusal escape the worker, which
    settles it through the failure path and quarantines the container.

    The route's probe is made to miss the race; the generator, already
    holding the drain, finds the output gone. Its refusal must settle
    the record rather than poison it.
    """
    from vaibify.gui.routes import testRoutes
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration(bOutputExists=False))
    monkeypatch.setattr(
        testGenerator, "flistMissingStepOutputs",
        _fflistMissOnlyInsideTheCarrier(testGenerator.flistMissingStepOutputs),
    )
    response = _fresponseGenerate(client, bAgentRoute=False)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["sRefusal"] == "outputs-missing"
    assert _flistWritesUnder(connectionDocker, S_TESTS) == []
    assert not fbContainerIsPoisoned(
        client.app.state.dictContainerOwners, _fsContainerName(client))
    assert operationJournal.fdictReadJournalOutcome(
        _fsContainerName(client)).get("dictOperations", {}) == {}
    assert listModelCalls == []
    del testRoutes


def _fflistMissOnlyInsideTheCarrier(fnReal):
    """Answer 'nothing missing' on the first call, the truth after."""
    listCalls = []

    def flistAnswer(*args, **kwargs):
        listCalls.append(1)
        if len(listCalls) == 1:
            return []
        return fnReal(*args, **kwargs)
    return flistAnswer


# ------------------------------------------------------ R3: N/A tiers


@pytest.mark.falsification
def testAnArrayOutputMakesOnlyTheQualitativeTierNotApplicable():
    """Kills: writing a placeholder for a tier with nothing to check."""
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration())
    response = _fresponseGenerate(client, bAgentRoute=False)
    assert response.status_code == 200, response.text
    dictStep = _fdictServedStep(client)
    dictTests = dictStep["dictTests"]
    assert dictTests["dictIntegrity"]["saCommands"]
    assert dictTests["dictQuantitative"]["saCommands"]
    assert dictTests["dictQualitative"]["saCommands"] == []
    assert dictTests["dictQualitative"]["sNotApplicableReason"]
    assert dictStep["dictVerification"]["sQualitative"] == "unnecessary"
    assert S_QUALITATIVE_TEST not in connectionDocker._dictFiles
    assert response.json()["dictStep"]["dictVerification"][
        "sQualitative"] == "unnecessary"


def testANotApplicableTierDoesNotBlockLevelOne():
    from vaibify.reproducibility.levelGates import flistLevel1Blockers
    from vaibify.gui.routeContext import ffilesForWorkflow
    client, _ = _tConnectGatedClient(DockerDoubleForGeneration())
    _fresponseGenerate(client, bAgentRoute=False)
    dictCtx = client.app.state.dictRouteContext
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    listBlockers = flistLevel1Blockers(
        dictWorkflow, {}, ffilesForWorkflow(dictCtx, S_CONTAINER_ID,
                                            dictWorkflow))
    assert not [
        dictBlocker for dictBlocker in listBlockers
        if "qualitative" in json.dumps(dictBlocker).lower()
    ], listBlockers


@pytest.mark.falsification
@pytest.mark.parametrize("bAgentRoute", [False, True])
def testAnUnreadableOutputIsUnknownNotInapplicable(
        listModelCalls, bAgentRoute):
    """Kills: treating an output nothing could read as having nothing to check.

    No tier is N/A; each says what could not be read, and its axis is
    an error -- never the green N/A an empty command list derives to.
    The researcher's route still offers the quantitative tier to the
    consented model (this container has a Claude CLI) and the agent's
    route never does; the model here answers with no standards, so the
    tier is an error naming both failures.
    """
    client, _ = _tConnectGatedClient(DockerDoubleForGeneration(
        dictReport=_fdictUnloadableReport()))
    response = _fresponseGenerate(client, bAgentRoute)
    assert response.status_code == 200, response.text
    dictStep = _fdictServedStep(client)
    for sKey, sAxis in (("dictQualitative", "sQualitative"),
                        ("dictQuantitative", "sQuantitative")):
        assert not dictStep["dictTests"][sKey].get("sNotApplicableReason")
        assert "unsupported binary format" in (
            dictStep["dictTests"][sKey].get("sError", "")), sKey
        assert dictStep["dictVerification"][sAxis] == "error", sKey
    assert listModelCalls == ([] if bAgentRoute else [1])


def testATierThatGainsChecksStartsUntested():
    """N/A on the first generation, checks on the second."""
    connectionDocker = DockerDoubleForGeneration(sOutput=S_TABLE_OUTPUT)
    client, _ = _tConnectGatedClient(connectionDocker)
    connectionDocker.dictReport = dict(
        _fdictTableReport(), listColumnNames=[], bHasHeader=False)
    _fresponseGenerate(client, bAgentRoute=False)
    assert _fdictServedStep(client)["dictVerification"][
        "sQualitative"] == "unnecessary"
    connectionDocker.dictReport = _fdictTableReport()
    response = _fresponseGenerate(client, bAgentRoute=False)
    assert response.status_code == 200, response.text
    dictStep = _fdictServedStep(client)
    assert dictStep["dictTests"]["dictQualitative"]["saCommands"]
    assert "sNotApplicableReason" not in dictStep["dictTests"][
        "dictQualitative"]
    assert dictStep["dictVerification"]["sQualitative"] == "untested"
    assert S_QUALITATIVE_TEST in connectionDocker._dictFiles


def testAnExistingOutputAlwaysYieldsIntegrityStandards():
    with pytest.raises(RuntimeError):
        deterministicTestWriter._fsIntegrityNotApplicableReason(
            [_fdictArrayReport()], {"listStandards": []})


# ------------------------------------- R5: regenerating into N/A


def _fclientWithGeneratedQualitativeTier():
    """Generate once from a table, so the qualitative tier has files."""
    connectionDocker = DockerDoubleForGeneration(sOutput=S_TABLE_OUTPUT)
    connectionDocker.dictReport = _fdictTableReport()
    client, _ = _tConnectGatedClient(connectionDocker)
    assert _fresponseGenerate(client, bAgentRoute=False).status_code == 200
    assert S_QUALITATIVE_TEST in connectionDocker._dictFiles
    assert S_QUALITATIVE_STANDARDS in connectionDocker._dictFiles
    connectionDocker.dictReport = dict(
        _fdictTableReport(), listColumnNames=[], bHasHeader=False)
    connectionDocker.listAdmittedPrimitives.clear()
    return client, connectionDocker


def testRegeneratingIntoNotApplicableRemovesVaibifysUneditedFiles():
    client, connectionDocker = _fclientWithGeneratedQualitativeTier()
    response = _fresponseGenerate(client, bAgentRoute=False)
    assert response.status_code == 200, response.text
    assert S_QUALITATIVE_TEST not in connectionDocker._dictFiles
    assert S_QUALITATIVE_STANDARDS not in connectionDocker._dictFiles
    dictStep = _fdictServedStep(client)
    assert dictStep["dictTests"]["dictQualitative"]["saCommands"] == []
    assert dictStep["dictVerification"]["sQualitative"] == "unnecessary"
    assert "listFilesToRemove" not in dictStep["dictTests"]["dictQualitative"]


def _fnAssertConfirmationAndNothingTouched(
        client, connectionDocker, response, sModifiedPath):
    assert response.status_code == 200, response.text
    assert response.json()["bNeedsOverwriteConfirm"] is True
    assert sModifiedPath in response.json()["listModifiedFiles"]
    assert _flistWritesUnder(connectionDocker, S_TESTS) == []
    assert S_QUALITATIVE_TEST in connectionDocker._dictFiles
    assert S_QUALITATIVE_STANDARDS in connectionDocker._dictFiles
    assert _fdictServedStep(client)["dictTests"]["dictQualitative"][
        "saCommands"]


@pytest.mark.parametrize("bAgentRoute", [False, True])
def testAnEditedTestFileIsAskedAboutAndNothingIsTouched(bAgentRoute):
    client, connectionDocker = _fclientWithGeneratedQualitativeTier()
    connectionDocker._dictFiles[S_QUALITATIVE_TEST] += (
        b"\n\ndef test_my_own_check():\n    assert True\n")
    response = _fresponseGenerate(client, bAgentRoute)
    _fnAssertConfirmationAndNothingTouched(
        client, connectionDocker, response, S_QUALITATIVE_TEST)


@pytest.mark.falsification
def testAnEditedStandardsFileIsAskedAboutAndNothingIsTouched():
    """Kills: removing a standards file without checking its digest."""
    client, connectionDocker = _fclientWithGeneratedQualitativeTier()
    dictStandards = json.loads(connectionDocker._dictFiles[
        S_QUALITATIVE_STANDARDS])
    dictStandards["listStandards"][0]["listExpectedColumns"].append("mine")
    connectionDocker._dictFiles[S_QUALITATIVE_STANDARDS] = json.dumps(
        dictStandards).encode("utf-8")
    response = _fresponseGenerate(client, bAgentRoute=False)
    _fnAssertConfirmationAndNothingTouched(
        client, connectionDocker, response, S_QUALITATIVE_STANDARDS)


def testAStandardsFileWithoutADigestIsAskedAbout():
    client, connectionDocker = _fclientWithGeneratedQualitativeTier()
    dictStandards = json.loads(connectionDocker._dictFiles[
        S_QUALITATIVE_STANDARDS])
    del dictStandards[testGenerator.S_STANDARDS_DIGEST_KEY]
    connectionDocker._dictFiles[S_QUALITATIVE_STANDARDS] = json.dumps(
        dictStandards).encode("utf-8")
    response = _fresponseGenerate(client, bAgentRoute=False)
    _fnAssertConfirmationAndNothingTouched(
        client, connectionDocker, response, S_QUALITATIVE_STANDARDS)


# ----------------------------------------- standards JSON validity


def _fresponseSaveAndRun(clientCaller, sRelativePath, sContent):
    return clientCaller.post(
        f"/api/steps/{S_CONTAINER_ID}/0/save-and-run-test",
        json={"sFilePath": sRelativePath, "sContent": sContent},
    )


S_STANDARDS_RELATIVE = S_QUANTITATIVE_STANDARDS[len(S_PROJECT_REPO) + 1:]


@pytest.mark.falsification
def testAMalformedStandardsFileIsRefusedBeforeItIsWritten():
    """Kills: writing a standards file without parsing it first."""
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration())
    response = _fresponseSaveAndRun(
        _fclientAgentFor(client), S_STANDARDS_RELATIVE,
        S_MALFORMED_STANDARDS)
    assert response.status_code == 422, response.text
    sMessage = response.json()["detail"]["sMessage"]
    assert "is not valid JSON" in sMessage
    assert "line 1, column" in sMessage
    assert S_QUANTITATIVE_STANDARDS not in connectionDocker._dictFiles


def testTheEditorRefusesAMalformedStandardsFileToo():
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration())
    response = client.put(
        f"/api/file/{S_CONTAINER_ID}/{S_STANDARDS_RELATIVE}",
        json={"sContent": S_MALFORMED_STANDARDS},
    )
    assert response.status_code == 422, response.text
    assert "line 1, column" in response.json()["detail"]["sMessage"]
    assert S_QUANTITATIVE_STANDARDS not in connectionDocker._dictFiles


def testAnyOtherJsonFileIsWrittenAsBefore():
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration())
    response = client.put(
        f"/api/file/{S_CONTAINER_ID}/stepA/notes.json",
        json={"sContent": S_MALFORMED_STANDARDS},
    )
    assert response.status_code == 200, response.text
    assert connectionDocker._dictFiles[S_STEP + "/notes.json"] == (
        S_MALFORMED_STANDARDS.encode("utf-8"))


def _fclientWithAStandardsFileWrittenElsewhere():
    """A malformed standards file the agent's shell wrote directly."""
    client, connectionDocker = _tConnectGatedClient(
        DockerDoubleForGeneration())
    assert _fresponseGenerate(client, bAgentRoute=False).status_code == 200
    connectionDocker._dictFiles[S_QUANTITATIVE_STANDARDS] = (
        S_MALFORMED_STANDARDS.encode("utf-8"))
    return client, connectionDocker


def testARunMeetingAMalformedStandardsFileReportsItByName():
    client, _ = _fclientWithAStandardsFileWrittenElsewhere()
    response = client.post(
        f"/api/steps/{S_CONTAINER_ID}/0/run-test-category",
        json={"sCategory": "quantitative"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["bPassed"] is False
    assert "is not valid JSON" in response.json()["sOutput"]
    assert _fdictServedStep(client)["dictVerification"][
        "sQuantitative"] == "error"


def testRunningEveryTierReportsTheMalformedOneAsAnError():
    client, _ = _fclientWithAStandardsFileWrittenElsewhere()
    response = client.post(f"/api/steps/{S_CONTAINER_ID}/0/run-tests")
    assert response.status_code == 200, response.text
    dictVerification = _fdictServedStep(client)["dictVerification"]
    assert dictVerification["sQuantitative"] == "error"
    assert dictVerification["sQuantitative"] != "passed"
