"""Falsification tests for Codex and Antigravity council adapters."""

import base64
import io
import json
import tarfile
import time

from vaibify.gui import agentCouncilAntigravityProvider as antigravity
from vaibify.gui import agentCouncilCodexProvider as codex
from vaibify.gui import agentCouncilProviderRegistry as providers
from vaibify.gui import agentCouncilRunner


class _CredentialConnection:
    def __init__(self, jsonDocument):
        self.baDocument = json.dumps(jsonDocument).encode("utf-8")
        self.listPaths = []

    def fbaFetchCredentialFile(self, sContainerId, sPath):
        self.listPaths.append(sPath)
        return self.baDocument


def _fsEncodeJwt(dictClaims):
    def _fsPart(dictPart):
        return base64.urlsafe_b64encode(json.dumps(dictPart).encode(
            "utf-8")).rstrip(b"=").decode("ascii")
    return _fsPart({"alg": "none"}) + "." + _fsPart(dictClaims) + ".x"


def testCodexExtractionNeverReturnsRefreshApiKeyOrPersonalClaims():
    iExpiry = int(time.time()) + 7200
    sAccountId = "account-routing-id"
    connectionCredential = _CredentialConnection({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": "must-not-leave",
        "tokens": {
            "access_token": _fsEncodeJwt({"exp": iExpiry}),
            "refresh_token": "must-not-leave",
            "account_id": sAccountId,
            "id_token": _fsEncodeJwt({
                "email": "must-not-leave@example.invalid",
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": sAccountId,
                    "chatgpt_plan_type": "subscription",
                },
            }),
        },
    })
    dictCredential = codex.fdictExtractCodexRunnerCredential(
        connectionCredential, "container-id", "/project/.codex/auth.json")
    assert set(dictCredential) == {
        "sAccessToken", "sAccountId", "sPlanType",
        "iExpiresAtEpochMilliseconds"}
    assert "must-not-leave" not in json.dumps(dictCredential)
    assert "email" not in json.dumps(dictCredential)


def testCodexStagingBuildsAPiiFreeRefreshFreeRoutingShell(monkeypatch):
    dictCaptured = {}

    def _fsCapture(sName, sValue):
        dictCaptured["sName"] = sName
        dictCaptured["jsonValue"] = json.loads(sValue)
        return "/temporary/credential"

    monkeypatch.setattr(codex.secretManager, "fsMaterializeSecretValue",
                        _fsCapture)
    codex.fsStageCodexRunnerCredentialFile({
        "sAccessToken": "access-only", "sAccountId": "account-id",
        "sPlanType": "subscription"})
    dictLogin = dictCaptured["jsonValue"]
    assert dictLogin["OPENAI_API_KEY"] is None
    assert dictLogin["tokens"]["refresh_token"] == ""
    assert dictLogin["tokens"]["access_token"] == "access-only"
    sIdentityToken = dictLogin["tokens"]["id_token"]
    sPayload = sIdentityToken.split(".")[1]
    dictClaims = json.loads(base64.urlsafe_b64decode(
        sPayload + "=" * (-len(sPayload) % 4)))
    assert set(dictClaims) == {"https://api.openai.com/auth", "exp"}


def testAntigravityExtractionAndStagingOmitTheRefreshToken(monkeypatch):
    iExpiry = int(time.time()) + 7200
    connectionCredential = _CredentialConnection({
        "token": {"access_token": "access-only", "token_type": "Bearer",
                  "refresh_token": "must-not-leave", "expiry": iExpiry},
        "auth_method": "oauth",
    })
    dictCredential = antigravity.fdictExtractAntigravityRunnerCredential(
        connectionCredential, "container-id",
        "/project/.gemini/antigravity-cli/antigravity-oauth-token")
    assert "refresh" not in json.dumps(dictCredential).lower()
    dictCaptured = {}
    monkeypatch.setattr(
        antigravity.secretManager, "fsMaterializeSecretValue",
        lambda sName, sValue: dictCaptured.setdefault(
            "jsonValue", json.loads(sValue)) or "/temporary/credential")
    antigravity.fsStageAntigravityRunnerCredentialFile(dictCredential)
    assert dictCaptured["jsonValue"]["token"] == {
        "access_token": "access-only", "token_type": "Bearer",
        "expiry": iExpiry}


def testProviderArgvNeverCarriesQuotedUntrustedMaterial():
    sInstruction = "server-owned instruction"
    sUntrusted = "--model attacker-model"
    listQuoted = [{"sSourceKind": "researcherQuestion",
                   "sAuthorIdentity": "researcher",
                   "sContent": sUntrusted}]
    saCodex = codex.flistComposeCodexArgv("model-id", sInstruction)
    saAntigravity = antigravity.flistComposeAntigravityArgv("model-id")
    assert sUntrusted not in " ".join(saCodex + saAntigravity)
    assert sInstruction in " ".join(saCodex)
    assert sUntrusted in providers.fbaComposeProviderChatStdin(
        "codex", listQuoted).decode("utf-8")
    assert sUntrusted in providers.fbaComposeProviderChatStdin(
        "gemini", listQuoted).decode("utf-8")


def testAntigravityAcceptsOnlyIdenticalRepeatedSchemaResults():
    dictAnswer = {"sSummary": "one"}
    sAnswer = json.dumps(dictAnswer)
    listEvents = [{"event": "result", "result": {
        "status": "SUCCESS", "response": sAnswer + "\n" + sAnswer}}]
    assert antigravity.fdictExtractAntigravityStructuredResult(
        listEvents) == dictAnswer
    listEvents[0]["result"]["response"] = (
        sAnswer + "\n" + json.dumps({"sSummary": "two"}))
    assert antigravity.fdictExtractAntigravityStructuredResult(
        listEvents) == {"sRawResultText": listEvents[0]["result"][
            "response"]}


def testAntigravityAcceptsOneCompleteJsonFenceButNoPreamble():
    dictAnswer = {"sSummary": "one"}
    sAnswer = json.dumps(dictAnswer)
    listEvents = [{"event": "result", "result": {
        "status": "SUCCESS", "response": "```json\n" + sAnswer + "\n```"}}]
    assert antigravity.fdictExtractAntigravityStructuredResult(
        listEvents) == dictAnswer
    listEvents[0]["result"]["response"] = (
        "preamble\n```json\n" + sAnswer + "\n```")
    assert antigravity.fdictExtractAntigravityStructuredResult(
        listEvents) == {"sRawResultText": listEvents[0]["result"][
            "response"]}


def testAntigravityModelIdentityComesFromTheInitEvent():
    listNative = [
        {"event": "init", "init": {
            "model": "resolved-gemini", "agent": "vaibify-council"}},
        {"event": "result", "result": {
            "status": "SUCCESS", "response": "answer", "usage": {}}},
    ]
    sOutput = "\n".join(json.dumps(dictEvent)
                         for dictEvent in listNative)
    _, dictIdentity, sAnswer, sFailure = providers.ftParseProviderChatResult(
        "gemini", sOutput, 0, "requested-alias", {})
    assert dictIdentity["sResolvedModel"] == "resolved-gemini"
    assert dictIdentity["sResolvedModel"] != "requested-alias"
    assert sAnswer == "answer"
    assert sFailure == ""


def testSelectedProviderEgressIsAnExactUnion():
    listHosts = providers.flistCollectProviderEgressHostnames(
        {"claude", "codex", "gemini"})
    assert set(listHosts) == {
        "api.anthropic.com", "chatgpt.com",
        "antigravity-unleash.goog",
        "daily-cloudcode-pa.googleapis.com",
        "cloudcode-pa.googleapis.com",
        "lh3.googleusercontent.com",
        "www.googleapis.com",
    }


def testAntigravityParsesNanosecondRfc3339Expiry():
    assert antigravity._fiParseExpiryMilliseconds(
        "2026-09-03T20:12:07.032621304Z") == 1788466327032


def testCodexOutputSchemaClosesEveryEvidenceObject():
    from vaibify.gui import agentCouncilCampaign, agentCouncilCharter

    listParticipants = [
        agentCouncilCampaign.fdictCreateParticipant("codex", "model-a"),
        agentCouncilCampaign.fdictCreateParticipant("codex", "model-b"),
    ]
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "What should happen?", listParticipants)
    dictSchema = agentCouncilCharter.fdictComposeTurnResultJsonSchema(
        dictCampaign, agentCouncilCharter.S_PHASE_PROPOSAL)
    listBranches = dictSchema["properties"]["listEvidence"]["items"][
        "anyOf"]
    assert listBranches
    assert all(dictBranch["additionalProperties"] is False
               for dictBranch in listBranches)


def testMultiFileTarRejectsTraversalAndStampsEveryMember():
    try:
        agentCouncilRunner.fbaBuildStampedFilesTarball({"../escape": b"x"})
    except ValueError:
        pass
    else:
        raise AssertionError("an escaping config path entered the tarball")
    baArchive = agentCouncilRunner.fbaBuildStampedFilesTarball({
        "provider/config/login.json": b"{}",
        "provider/agent.md": b"instruction",
    })
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as fileTar:
        listMembers = fileTar.getmembers()
    assert {infoMember.name for infoMember in listMembers} == {
        "provider", "provider/config", "provider/config/login.json",
        "provider/agent.md"}
    assert all(infoMember.uid == 1000 and infoMember.gid == 1000
               for infoMember in listMembers)
