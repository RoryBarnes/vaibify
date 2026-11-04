"""A confirmation records only what was reviewed, and only on evidence that succeeded.

Source: ``vaibify/gui/routes/bindingRoutes.py`` (the GET and the
confirm), ``vaibify/gui/remoteBindingContext.py`` (the coded refusals)
and ``vaibify/cli/commandBindings.py`` (``vaibify bindings``).

Before 2026-11-02 the confirm route saved whatever evidence came back,
``unreachable`` included, and with no target in the body it confirmed
``project.json``'s target as it stood at POST time, in whichever
workflow was loaded then. Every refusal here is checked against the
record on disk: byte-equal when it existed, still absent when it did
not.
"""

import json
import os
import shlex
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from vaibify.config import remoteBindings
from vaibify.gui import zenodoTokenAccess
from vaibify.gui.routes import bindingRoutes
from vaibify.reproducibility import zenodoClient
from tests.testRemoteBindings import (  # noqa: F401 -- fixtures
    S_CONTAINER_ID, S_KEY, S_PROJECT_NAME, _fdictEntry, _fdictWorkflow,
    _fresponseConfirmReviewed, fnIsolateHostState, tApp,
)

S_CONFIRM_PATH = f"/api/bindings/{S_CONTAINER_ID}/confirm"
S_READ_PATH = f"/api/bindings/{S_CONTAINER_ID}"


def _fdictReviewOf(client, sService, sRecordKind="", sInstance=""):
    dictDescribed = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    return _fdictEntry(dictDescribed, sService, sRecordKind, sInstance)["dictReview"]


def _fbaRecordOrNone(sUuid):
    sPath = remoteBindings.fsBindingPath(sUuid)
    if not os.path.exists(sPath):
        return None
    with open(sPath, "rb") as fileRecord:
        return fileRecord.read()


def _fnFetchEvidenceAs(monkeypatch, sService, dictEvidence):
    monkeypatch.setitem(
        bindingRoutes._DICT_EVIDENCE_FETCHERS, sService,
        lambda dictCtx, sContainerId, sRecordKind, dictTarget: dict(dictEvidence),
    )


def _fdictRefusal(responseHttp):
    assert responseHttp.status_code == 409, responseHttp.text
    return responseHttp.json()["detail"]


# ---------------------------------------------------------------------------
# Evidence is enforced on the server
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testUnreachableEvidenceIsRefusedAndTheRecordIsUnchanged(tApp, monkeypatch):
    """A confirmation whose evidence could not be fetched writes nothing.

    Kills: removing the evidence gate, which saved an "unreachable"
    answer as if the researcher had seen something.
    """
    client, _, sUuid = tApp
    _fresponseConfirmReviewed(client, "overleaf")
    baBefore = _fbaRecordOrNone(sUuid)
    _fnFetchEvidenceAs(monkeypatch, "zenodo", {
        "bSucceeded": False, "sState": "unavailable",
        "sMessage": "ConnectionError: zenodo.org did not answer",
    })
    dictDetail = _fdictRefusal(client.post(
        S_CONFIRM_PATH, json=_fdictReviewOf(client, "zenodo", "project", "sandbox"),
    ))
    assert dictDetail["sError"] == "BINDING-EVIDENCE-UNAVAILABLE"
    assert "did not answer" in dictDetail["sMessage"]
    assert _fbaRecordOrNone(sUuid) == baBefore


def testAMissingTokenIsRefusedBeforeAnyFetch(tApp, monkeypatch):
    client, _, sUuid = tApp
    monkeypatch.setitem(
        bindingRoutes._DICT_EVIDENCE_FETCHERS, "zenodo",
        bindingRoutes._fdictFetchZenodoEvidence,
    )
    monkeypatch.setattr(zenodoTokenAccess, "fsActiveZenodoToken", lambda *tArgs: "")

    def fnRefuseToConstruct(*tArgs, **dictKeywords):
        raise AssertionError("a Zenodo client was built with no token to spend")

    monkeypatch.setattr(zenodoClient, "ZenodoClient", fnRefuseToConstruct)
    dictDetail = _fdictRefusal(client.post(
        S_CONFIRM_PATH, json=_fdictReviewOf(client, "zenodo", "environment", "zenodo"),
    ))
    assert (dictDetail["sError"], dictDetail["sInstance"]) == ("ZENODO-TOKEN-MISSING", "zenodo")
    assert "No token for zenodo.org (permanent)" in dictDetail["sMessage"]
    assert _fbaRecordOrNone(sUuid) is None


# ---------------------------------------------------------------------------
# The confirmation is pinned to what was reviewed
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAReviewOfAnotherWorkflowIsRefused(tApp):
    """A review made while workflow A was loaded cannot confirm anything once B is.

    B names the same Overleaf project as A, so only the workflow key
    tells the two reviews apart.

    Kills: removing the check that the review's workflow key is the
    loaded workflow's.
    """
    client, dictCtx, sUuid = tApp
    dictReview = _fdictReviewOf(client, "overleaf")
    dictCtx["workflows"][S_CONTAINER_ID] = dict(
        _fdictWorkflow(), sWorkflowName="otherStudy",
    )
    dictDetail = _fdictRefusal(client.post(S_CONFIRM_PATH, json=dictReview))
    assert dictDetail["sError"] == "BINDING-REVIEW-STALE"
    assert _fbaRecordOrNone(sUuid) is None


def testATargetThatMovedAfterTheReviewIsRefused(tApp):
    client, dictCtx, sUuid = tApp
    dictReview = _fdictReviewOf(client, "overleaf")
    dictCtx["workflows"][S_CONTAINER_ID]["sOverleafProjectId"] = "olEditedAfterReview"
    dictDetail = _fdictRefusal(client.post(S_CONFIRM_PATH, json=dictReview))
    assert dictDetail["sError"] == "BINDING-REVIEW-STALE"
    assert _fbaRecordOrNone(sUuid) is None


def testARecordCannotBeConfirmedWhereProjectJsonNamesNone(tApp):
    """On the instance project.json does not declare, only the instance is confirmable."""
    client, _, sUuid = tApp
    dictReview = _fdictReviewOf(client, "zenodo", "project", "zenodo")
    assert dictReview["dictTarget"] == {
        "sInstance": "zenodo", "sConceptDoi": "", "sDepositionId": "",
    }
    dictReview["dictTarget"]["sConceptDoi"] = "10.5281/zenodo.900"
    dictDetail = _fdictRefusal(client.post(S_CONFIRM_PATH, json=dictReview))
    assert (dictDetail["sError"], dictDetail["sInstance"]) == ("BINDING-REVIEW-STALE", "zenodo")
    assert _fbaRecordOrNone(sUuid) is None


def testABareConfirmationIsRefusedWithTheWayToReview(tApp):
    client, _, sUuid = tApp
    responseHttp = client.post(S_CONFIRM_PATH, json={"sService": "zenodo"})
    assert responseHttp.status_code == 400
    assert "vaibify bindings" in responseHttp.json()["detail"]
    assert _fbaRecordOrNone(sUuid) is None


def testAnInvalidRecordRefusesTheConfirmationAndStaysAsItWas(tApp):
    client, _, sUuid = tApp
    os.makedirs(remoteBindings.fsBindingsDirectory(), exist_ok=True)
    with open(remoteBindings.fsBindingPath(sUuid), "w") as fileRecord:
        fileRecord.write('{"sProjectUuid": "%s", "dictWorkflows": {}}' % sUuid)
    baPlanted = _fbaRecordOrNone(sUuid)
    dictDetail = _fdictRefusal(client.post(S_CONFIRM_PATH, json={
        "sService": "overleaf", "sRecordKind": "", "sWorkflowKey": S_KEY,
        "dictTarget": {"sProjectId": "ol-1"},
    }))
    assert dictDetail["sError"] == "BINDING-RECORD-INVALID"
    assert _fbaRecordOrNone(sUuid) == baPlanted


def testAConfirmationRecordsItsEvidenceAndOnlyItsEntry(tApp):
    client, _, sUuid = tApp
    responseHttp = _fresponseConfirmReviewed(client, "zenodo", "environment", "zenodo")
    assert responseHttp.status_code == 200, responseHttp.text
    dictRecord = remoteBindings.fdictReadBindings(sUuid)
    dictServices = dictRecord["dictWorkflows"][S_KEY]["dictServices"]
    assert list(dictServices) == ["zenodo"]
    assert list(dictServices["zenodo"]["dictRecordKinds"]) == ["environment"]
    dictEntry = dictServices["zenodo"]["dictRecordKinds"]["environment"]["dictInstances"]["zenodo"]
    assert dictEntry["dictEvidence"]["bSucceeded"] is True


# ---------------------------------------------------------------------------
# The read
# ---------------------------------------------------------------------------


def testNoEvidenceIsFetchedWhenTheReadDoesNotAskForIt(tApp, monkeypatch):
    client, _, _ = tApp

    def fnRefuseToFetch(*tArgs):
        raise AssertionError("evidence was fetched for a notice")

    for sService in remoteBindings.T_BINDING_SERVICES:
        monkeypatch.setitem(bindingRoutes._DICT_EVIDENCE_FETCHERS, sService, fnRefuseToFetch)
    dictDescribed = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    assert dictDescribed["listEntries"]
    assert not any("dictEvidence" in dictEntry for dictEntry in dictDescribed["listEntries"])


def testTheReadFetchesEvidenceForTheOneEntryNamed(tApp, monkeypatch):
    client, _, _ = tApp
    listAsked = []

    def fdictRecordTheAsk(dictCtx, sContainerId, sRecordKind, dictTarget):
        listAsked.append((sRecordKind, dictTarget["sInstance"]))
        return {"bSucceeded": True, "sState": "token-accepted"}

    monkeypatch.setitem(bindingRoutes._DICT_EVIDENCE_FETCHERS, "zenodo", fdictRecordTheAsk)
    client.get(S_READ_PATH, params={
        "sService": "zenodo", "sRecordKind": "project", "sInstance": "zenodo",
    })
    assert listAsked == [("project", "zenodo")]


def testTheReadNamesWhatAppliesInTheDashboardsWords(tApp, monkeypatch):
    client, _, _ = tApp
    monkeypatch.setattr(
        bindingRoutes, "_fsEnvironmentArchiveInstance",
        lambda dictCtx, sContainerId, dictWorkflow: "zenodo",
    )
    dictDescribed = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    assert dictDescribed["bEnvironmentArchiveInUse"] is True
    listApplying = [
        (dictEntry["sService"], dictEntry["sRecordKind"], dictEntry["sInstance"])
        for dictEntry in dictDescribed["listEntries"] if dictEntry["bApplies"]
    ]
    assert listApplying == [
        ("overleaf", "", ""), ("zenodo", "project", "sandbox"),
        ("zenodo", "environment", "zenodo"),
    ]
    dictEnvironment = _fdictEntry(dictDescribed, "zenodo", "environment", "zenodo")
    assert dictEnvironment["sNotice"] == (
        "Not confirmed: this workflow's environment archive on zenodo.org (permanent)."
    )
    assert "production" not in json.dumps(dictDescribed)


def testOnlyTheEnvironmentArchiveNamesNoTargetAndSaysWhatItAuthorizes(tApp):
    """The environment kind authorizes an instance; every other kind names a record."""
    client, _, _ = tApp
    dictDescribed = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    dictByAddress = {
        (dictEntry["sService"], dictEntry["sRecordKind"], dictEntry["sInstance"]): dictEntry
        for dictEntry in dictDescribed["listEntries"]
    }
    assert {tAddress for tAddress, dictEntry in dictByAddress.items()
            if not dictEntry["bHasTarget"]} == {
        ("zenodo", "environment", "sandbox"), ("zenodo", "environment", "zenodo")}
    assert all(dictEntry["sAuthorizes"] == "" for dictEntry in dictByAddress.values()
               if dictEntry["bHasTarget"])
    assert dictByAddress[("zenodo", "environment", "zenodo")]["sAuthorizes"] == (
        "This lets vaibify deposit this workflow's container images on "
        "zenodo.org (permanent) with your token. Each deposit records its own "
        "DOI in .vaibify/environment.json.")
    assert dictByAddress[("zenodo", "environment", "sandbox")]["sAuthorizes"] == (
        "This lets vaibify deposit this workflow's container images on "
        "the Zenodo sandbox (testing only) with your token. Each deposit "
        "records its own DOI in .vaibify/environment.json.")


def testADifferingEntryNamesBothValues(tApp):
    client, dictCtx, _ = tApp
    _fresponseConfirmReviewed(client, "overleaf")
    dictCtx["workflows"][S_CONTAINER_ID]["sOverleafProjectId"] = "olEdited"
    dictDescribed = client.get(S_READ_PATH, params={"bFetchEvidence": "false"}).json()
    dictOverleaf = _fdictEntry(dictDescribed, "overleaf")
    assert dictOverleaf["sState"] == "differs"
    assert dictOverleaf["sNotice"] == (
        "project.json names olEdited for this workflow's Overleaf project; "
        "you confirmed ol-1."
    )


@pytest.mark.parametrize("dictArchive, sExpected", [
    ({"sZenodoService": "zenodo", "sVersionDoi": "10.5281/zenodo.5"}, "zenodo"),
    ({"sVersionDoi": "10.5072/zenodo.5"}, "sandbox"),
    (None, ""),
])
def testTheEnvironmentArchiveInstanceComesFromTheEnvelope(monkeypatch, dictArchive, sExpected):
    from vaibify.reproducibility import environmentSnapshot
    monkeypatch.setattr(bindingRoutes, "ffilesForWorkflow", lambda *tArgs: None)
    dictContainer = {} if dictArchive is None else {"dictImageArchive": dictArchive}
    monkeypatch.setattr(
        environmentSnapshot, "fdictReadEnvironmentJson",
        lambda filesRepo: {"dictContainer": dictContainer},
    )
    assert bindingRoutes._fsEnvironmentArchiveInstance({}, "cid", {}) == sExpected


# ---------------------------------------------------------------------------
# The evidence each service can give
# ---------------------------------------------------------------------------


class _ZenodoDouble:
    """A Zenodo client that answers from fixed records and records what it was asked."""

    listAsked = []

    def __init__(self, sService, sToken=""):
        self.sService = sService

    def fdictGetDeposit(self, iDepositionId):
        self.listAsked.append(("deposition", iDepositionId))
        return {"metadata": {"title": "A deposit"}, "owner": 42}

    def fdictFetchPublishedRecord(self, sRecordId):
        self.listAsked.append(("record", sRecordId))
        return {"metadata": {"title": "A record"}, "owners": [42]}


@pytest.fixture
def listZenodoAsked(monkeypatch):
    _ZenodoDouble.listAsked = []
    monkeypatch.setattr(zenodoTokenAccess, "fsActiveZenodoToken", lambda *tArgs: "a-token")
    monkeypatch.setattr(zenodoClient, "ZenodoClient", _ZenodoDouble)
    monkeypatch.setattr(
        zenodoClient, "fdictProbeToken",
        lambda sService, sToken: (
            _ZenodoDouble.listAsked.append(("probe", sService))
            or {"sOutcome": "accepted", "sDetail": "", "listDepositTitles": ["One"]}
        ),
    )
    return _ZenodoDouble.listAsked


@pytest.mark.parametrize("sRecordKind, dictTarget, tExpected", [
    ("project", {"sInstance": "zenodo", "sConceptDoi": "10.5281/zenodo.7",
                 "sDepositionId": "8"}, ("deposition", 8)),
    ("project", {"sInstance": "zenodo", "sConceptDoi": "10.5281/zenodo.7",
                 "sDepositionId": ""}, ("record", "7")),
    ("project", {"sInstance": "zenodo", "sConceptDoi": "", "sDepositionId": ""},
     ("probe", "zenodo")),
    ("environment", {"sInstance": "sandbox"}, ("probe", "sandbox")),
])
def testTheStrongestEvidenceIsAsked(listZenodoAsked, sRecordKind, dictTarget, tExpected):
    dictEvidence = bindingRoutes._fdictFetchZenodoEvidence(
        {"docker": None}, "cid", sRecordKind, dictTarget,
    )
    assert dictEvidence["bSucceeded"] is True
    assert listZenodoAsked == [tExpected]


def testATokenZenodoRefusesIsEvidenceUnavailable(listZenodoAsked, monkeypatch):
    monkeypatch.setattr(
        zenodoClient, "fdictProbeToken",
        lambda sService, sToken: {"sOutcome": "rejected", "sDetail": "401", "listDepositTitles": []},
    )
    dictEvidence = bindingRoutes._fdictFetchZenodoEvidence(
        {"docker": None}, "cid", "environment", {"sInstance": "zenodo"},
    )
    assert dictEvidence["bSucceeded"] is False
    assert "rejected" in dictEvidence["sMessage"]


def testOverleafEvidenceClaimsOnlyThatTheProjectIsReadable(monkeypatch):
    from vaibify.gui import syncDispatcher
    monkeypatch.setattr(
        syncDispatcher, "ftRefreshOverleafMirror",
        lambda sProjectId: (True, {"sHeadSha": "abc1234def", "iFileCount": 12, "sRefreshedAt": "now"}),
    )
    dictEvidence = bindingRoutes._fdictFetchOverleafEvidence(None, "cid", "", {"sProjectId": "ol-1"})
    assert dictEvidence == {
        "bSucceeded": True, "sState": "readable", "iFileCount": 12,
        "sHeadSha": "abc1234def", "sRefreshedAt": "now",
    }
    monkeypatch.setattr(
        syncDispatcher, "ftRefreshOverleafMirror",
        lambda sProjectId: (False, "No Overleaf token stored on host"),
    )
    assert bindingRoutes._fdictFetchOverleafEvidence(None, "cid", "", {"sProjectId": "ol-1"}) == {
        "bSucceeded": False, "sState": "unavailable",
        "sMessage": "No Overleaf token stored on host",
    }


# ---------------------------------------------------------------------------
# vaibify bindings prints a confirm line the route accepts
# ---------------------------------------------------------------------------


S_CONTAINER_WORKFLOW_PATH = "/workspace/repoAlpha/.vaibify/projects/study.json"


def _flistConfirmLines(sOutput):
    return [
        shlex.split(sLine.strip()) for sLine in sOutput.splitlines()
        if sLine.strip().startswith("vaibify do confirm-remote-binding")
    ]


def testTheCliPrintsAConfirmLineTheRouteAccepts(tApp, monkeypatch):
    from vaibify.cli import commandBindings, configLoader, hubSession
    from vaibify.cli.actionCommands import ftSplitFieldArguments
    client, _, sUuid = tApp
    monkeypatch.setattr(
        configLoader, "fconfigResolveProject",
        lambda sProjectName: SimpleNamespace(sProjectName=S_PROJECT_NAME),
    )
    monkeypatch.setattr(
        hubSession, "fdictOpenResearcherSession",
        lambda sName, iPort, sPath: {
            "sContainerId": S_CONTAINER_ID, "sWorkflowPath": S_CONTAINER_WORKFLOW_PATH,
        },
    )
    monkeypatch.setattr(hubSession, "fnReleaseContainer", lambda dictSession: None)
    monkeypatch.setattr(
        hubSession, "ftSendSessionRequest",
        lambda dictSession, sMethod, sPath, dictFields: (
            lambda responseHttp: (responseHttp.status_code, responseHttp.json())
        )(client.get(sPath, params=dictFields)),
    )
    resultCli = CliRunner().invoke(commandBindings.fnBindingsCommand, [])
    assert resultCli.exit_code == 0, resultCli.output
    listLines = _flistConfirmLines(resultCli.output)
    assert len(listLines) == 2
    for listWords in listLines:
        assert listWords[3:7] == ["-p", S_PROJECT_NAME, "--workflow", S_CONTAINER_WORKFLOW_PATH]
        _, dictFields = ftSplitFieldArguments(tuple(listWords[7:]))
        responseHttp = client.post(S_CONFIRM_PATH, json=dictFields)
        assert responseHttp.status_code == 200, responseHttp.text
    assert remoteBindings.fdictConfirmedBinding(sUuid, S_KEY, "overleaf")
    assert remoteBindings.fdictConfirmedBinding(sUuid, S_KEY, "zenodo", "project", "sandbox")
