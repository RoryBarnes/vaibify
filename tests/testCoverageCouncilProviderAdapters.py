"""Refusal paths and parsers of the Claude, Codex and Antigravity adapters.

Every credential extraction here reads through a fake of the one typed
container read the adapters use (``fbaFetchCredentialFile``); nothing
else is stubbed. The refusals matter because each one is what a
researcher sees instead of a runner that silently fails to authenticate,
so every test asserts the refusal's words, not merely that one happened.
"""

import base64
import json
import time

import pytest

from vaibify.gui import agentCouncilAntigravityProvider as antigravity
from vaibify.gui import agentCouncilCodexProvider as codex
from vaibify.gui import agentCouncilProviders as providers
from vaibify.gui import agentCouncilRunner


S_CONTAINER_ID = "containerIdAlpha"
S_CODEX_PATH = "/workspace/projectAlpha/.codex/auth.json"
S_ANTIGRAVITY_PATH = (
    "/workspace/projectAlpha/.gemini/antigravity-cli/antigravity-oauth-token")
S_ACCOUNT_ID = "accountRoutingAlpha"


class _CredentialReadFake:
    """The typed credential read: answers bytes or raises one fault."""

    def __init__(self, jsonAnswer):
        self.jsonAnswer = jsonAnswer
        self.listRequests = []

    def fbaFetchCredentialFile(self, sContainerId, sPath):
        self.listRequests.append((sContainerId, sPath))
        if isinstance(self.jsonAnswer, Exception):
            raise self.jsonAnswer
        if isinstance(self.jsonAnswer, bytes):
            return self.jsonAnswer
        return json.dumps(self.jsonAnswer).encode("utf-8")


def fsEncodeJwtSegment(jsonPayload):
    return base64.urlsafe_b64encode(json.dumps(jsonPayload).encode(
        "utf-8")).rstrip(b"=").decode("ascii")


def fsEncodeJwt(jsonClaims):
    return ".".join((fsEncodeJwtSegment({"alg": "none"}),
                     fsEncodeJwtSegment(jsonClaims), "signature"))


def fdictCodexLogin(**dictTokenOverrides):
    dictTokens = {
        "access_token": fsEncodeJwt({"exp": int(time.time()) + 7200}),
        "id_token": fsEncodeJwt({"https://api.openai.com/auth": {
            "chatgpt_account_id": S_ACCOUNT_ID,
            "chatgpt_plan_type": "planAlpha"}}),
        "account_id": S_ACCOUNT_ID,
    }
    dictTokens.update(dictTokenOverrides)
    return {"auth_mode": "chatgpt", "tokens": dictTokens}


def fsExplainCodex(jsonAnswer):
    return codex.fsExplainUnusableCodexCredential(
        _CredentialReadFake(jsonAnswer), S_CONTAINER_ID, S_CODEX_PATH)


# ----- Codex credential lane ----------------------------------------------


@pytest.mark.parametrize("sRoot", ["", "relative/root"])
def testCodexCredentialPathRefusesANonAbsoluteRoot(sRoot):
    with pytest.raises(providers.RunnerCredentialError,
                       match="absolute container path"):
        codex.fsComposeCodexCredentialContainerPath(sRoot)


def testCodexCredentialPathSitsUnderTheProjectRoot():
    assert codex.fsComposeCodexCredentialContainerPath(
        "/workspace/projectAlpha") == S_CODEX_PATH


def testAUsableCodexLoginIsExplainedAsEmptyAndReadsTheRightPath():
    readFake = _CredentialReadFake(fdictCodexLogin())
    assert codex.fsExplainUnusableCodexCredential(
        readFake, S_CONTAINER_ID, S_CODEX_PATH) == ""
    assert readFake.listRequests == [(S_CONTAINER_ID, S_CODEX_PATH)]


@pytest.mark.parametrize("jsonAnswer, sExpected", [
    (FileNotFoundError("absent"), "no persisted Codex login was found"),
    (ValueError("over the ceiling"), "too large to be a login document"),
    (b"\xff\xfe not text", "not readable JSON"),
    (b"{not json", "not readable JSON"),
    ({"tokens": ["not", "a", "mapping"]}, "carries no access token"),
    (fdictCodexLogin(access_token=""), "carries no access token"),
    (fdictCodexLogin(id_token=None), "no identity routing document"),
    (fdictCodexLogin(id_token="onlyOneSegment"),
     "identity token is not a readable JWT"),
    (fdictCodexLogin(id_token="head.@@@notbase64@@@.sig"),
     "identity token is not a readable JWT"),
    (fdictCodexLogin(id_token=fsEncodeJwt(["a", "list"])),
     "identity token has no claim mapping"),
    (fdictCodexLogin(id_token=fsEncodeJwt(
        {"https://api.openai.com/auth": "notAMapping"})),
     "no ChatGPT account routing id"),
    (fdictCodexLogin(id_token=fsEncodeJwt({})),
     "no ChatGPT account routing id"),
    (fdictCodexLogin(account_id="accountRoutingOther"),
     "account identifiers disagree"),
    (fdictCodexLogin(access_token="notAJwtAtAll"),
     "access token is not a readable JWT"),
    (fdictCodexLogin(access_token=fsEncodeJwt(
        {"exp": int(time.time()) - 60})),
     "expired or is too near expiry"),
])
def testEveryUnusableCodexLoginIsRefusedInWords(jsonAnswer, sExpected):
    assert sExpected in fsExplainCodex(jsonAnswer)


def testACodexAccessTokenWithoutAnExpiryIsAcceptedRatherThanGuessed():
    dictCredential = codex.fdictExtractCodexRunnerCredential(
        _CredentialReadFake(fdictCodexLogin(
            access_token=fsEncodeJwt({"sub": "x"}), account_id="")),
        S_CONTAINER_ID, S_CODEX_PATH)
    assert dictCredential["iExpiresAtEpochMilliseconds"] == 0
    assert dictCredential["sAccountId"] == S_ACCOUNT_ID
    assert dictCredential["sPlanType"] == "planAlpha"


def testTheCodexExpiryIsReadInEpochMilliseconds():
    iExpirySeconds = int(time.time()) + 7200
    dictCredential = codex.fdictExtractCodexRunnerCredential(
        _CredentialReadFake(fdictCodexLogin(
            access_token=fsEncodeJwt({"exp": iExpirySeconds}))),
        S_CONTAINER_ID, S_CODEX_PATH)
    assert dictCredential["iExpiresAtEpochMilliseconds"] == (
        iExpirySeconds * 1000)


def testTheCodexRoutingShellOmitsAnAbsentPlanType(monkeypatch):
    dictCaptured = {}
    monkeypatch.setattr(
        codex.secretManager, "fsMaterializeSecretValue",
        lambda sName, sValue: dictCaptured.update(
            sName=sName, jsonLogin=json.loads(sValue)) or "/staged/path")
    assert codex.fsStageCodexRunnerCredentialFile({
        "sAccessToken": "accessAlpha", "sAccountId": S_ACCOUNT_ID,
        "sPlanType": ""}) == "/staged/path"
    assert dictCaptured["sName"] == "codexCouncilAccessToken"
    sPayload = dictCaptured["jsonLogin"]["tokens"]["id_token"].split(".")[1]
    dictClaims = json.loads(base64.urlsafe_b64decode(
        sPayload + "=" * (-len(sPayload) % 4)))
    assert dictClaims["https://api.openai.com/auth"] == {
        "chatgpt_account_id": S_ACCOUNT_ID}


def testCodexConfigTarballDefaultsTheSchemaAndStampsTheCouncilUser(
        tmp_path):
    import io
    import tarfile
    pathLogin = tmp_path / "login.json"
    pathLogin.write_bytes(b'{"auth_mode": "chatgpt"}')
    baTar = codex.fbaBuildCodexConfigTarball(str(pathLogin))
    with tarfile.open(fileobj=io.BytesIO(baTar)) as fileTar:
        dictMembers = {infoMember.name: infoMember for infoMember in fileTar}
        baSchema = fileTar.extractfile(
            dictMembers["vaibifyCouncilCodex/turn-schema.json"]).read()
    assert json.loads(baSchema) == {"type": "object"}
    assert {infoMember.uid for infoMember in dictMembers.values()} == {1000}


def testCodexCapabilityContractTracksCredentialEvidence():
    dictManual = codex.fdictBuildCodexCapabilityContract()
    assert dictManual["bAvailable"] is False
    assert dictManual["dictModelDiscovery"] == {
        "sSource": "manualEntry", "bVerified": False, "listModelIds": []}
    dictVerified = codex.fdictBuildCodexCapabilityContract(
        {"listModelIds": ["modelAlpha"]}, bRunnerBackendEnabled=True)
    assert dictVerified["bAvailable"] is True
    assert dictVerified["dictModelDiscovery"]["sSource"] == (
        "credentialEvidenceModelCatalog")
    assert dictVerified["saEgressAllowlist"] == ["chatgpt.com"]


def testCodexArgvPlacesTheSchemaOnlyForStructuredTurns():
    saStructured = codex.flistComposeCodexArgv("modelAlpha", "charter")
    saChat = codex.flistComposeCodexArgv(
        "modelAlpha", "charter", saCliProgram=["fakeCodex"],
        bStructured=False)
    assert "--output-schema" in saStructured
    assert "--output-schema" not in saChat
    assert saChat[0] == "fakeCodex"
    assert saStructured[-3:] == [
        "--cd", agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT, "-"]
    assert 'developer_instructions="charter"' in saStructured


def fsJsonLines(listEvents):
    return "".join(json.dumps(dictEvent) + "\n" for dictEvent in listEvents)


def testCodexNormalizationKeepsTheLastAnswerAndTheUsage():
    listEvents = codex.flistNormalizeCodexEvents(fsJsonLines([
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "first draft"}},
        {"type": "item.completed", "item": {"type": "reasoning"}},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "final answer"}},
        {"type": "turn.completed", "usage": {"output_tokens": 3}},
    ]), 0)
    assert [dictEvent["type"] for dictEvent in listEvents] == [
        "assistant", "assistant", "result"]
    assert listEvents[-1] == {
        "type": "result", "result": "final answer", "is_error": False,
        "usage": {"output_tokens": 3}, "subtype": "codexExec"}


def testCodexFailureWithoutAnAnswerIsAnErrorResultNamingTheFailure():
    listEvents = codex.flistNormalizeCodexEvents(fsJsonLines([
        {"type": "error", "message": "authentication required"}]), 1)
    assert listEvents[-1]["is_error"] is True
    assert listEvents[-1]["result"] == "authentication required"


def testACodexExitWithNoWordsStillReportsAFailure():
    listEvents = codex.flistNormalizeCodexEvents("", 2)
    assert listEvents == [{
        "type": "result",
        "result": "Codex exited before producing an answer",
        "is_error": True, "usage": {}, "subtype": "codexExec"}]


def testAnAnswerOutranksALaterFailureEvent():
    listEvents = codex.flistNormalizeCodexEvents(fsJsonLines([
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "answer"}},
        {"type": "turn.failed"},
    ]), 0)
    assert listEvents[-1]["result"] == "answer"
    assert listEvents[-1]["is_error"] is False


# ----- Antigravity credential lane ----------------------------------------


def fdictAntigravityLogin(**dictTokenOverrides):
    dictToken = {"access_token": "accessAlpha", "token_type": "Bearer",
                 "refresh_token": "refreshAlpha",
                 "expiry": int(time.time()) + 7200}
    dictToken.update(dictTokenOverrides)
    return {"token": dictToken, "auth_method": "oauth"}


def fsExplainAntigravity(jsonAnswer):
    return antigravity.fsExplainUnusableAntigravityCredential(
        _CredentialReadFake(jsonAnswer), S_CONTAINER_ID, S_ANTIGRAVITY_PATH)


def testAntigravityCredentialPathRefusesARelativeRoot():
    with pytest.raises(providers.RunnerCredentialError):
        antigravity.fsComposeAntigravityCredentialContainerPath("projectAlpha")
    assert antigravity.fsComposeAntigravityCredentialContainerPath(
        "/workspace/projectAlpha") == S_ANTIGRAVITY_PATH


@pytest.mark.parametrize("jsonAnswer, sExpected", [
    (FileNotFoundError("absent"), "no persisted Antigravity login"),
    (ValueError("over the ceiling"), "too large to be a login document"),
    (b"[unterminated", "not readable JSON"),
    ({"token": "notAMapping"}, "carries no access token"),
    (fdictAntigravityLogin(access_token=""), "carries no access token"),
    (fdictAntigravityLogin(expiry=int(time.time()) - 5),
     "expired or is too near expiry"),
])
def testEveryUnusableAntigravityLoginIsRefusedInWords(jsonAnswer, sExpected):
    assert sExpected in fsExplainAntigravity(jsonAnswer)


def testAUsableAntigravityLoginIsExplainedAsEmpty():
    assert fsExplainAntigravity(fdictAntigravityLogin()) == ""


@pytest.mark.parametrize("jsonExpiry, iExpected", [
    (1_700_000_000, 1_700_000_000_000),
    (1_700_000_000_123, 1_700_000_000_123),
    ("2023-11-14T22:13:20Z", 1_700_000_000_000),
    ("2023-11-14T22:13:20.123456789Z", 1_700_000_000_123),
    ("2023-11-14T23:13:20+01:00", 1_700_000_000_000),
    ("not a timestamp", 0),
    ("", 0),
    (None, 0),
])
def testAntigravityExpiryFormsAllReadAsEpochMilliseconds(
        jsonExpiry, iExpected):
    assert antigravity._fiParseExpiryMilliseconds(jsonExpiry) == iExpected


def testAnAntigravityLoginWithDefaultsCarriesBearerAndOauth():
    dictCredential = antigravity.fdictExtractAntigravityRunnerCredential(
        _CredentialReadFake({"token": {"access_token": "accessAlpha"}}),
        S_CONTAINER_ID, S_ANTIGRAVITY_PATH)
    assert dictCredential == {
        "sAccessToken": "accessAlpha", "sTokenType": "Bearer",
        "jsonExpiry": None, "sAuthMethod": "oauth",
        "iExpiresAtEpochMilliseconds": 0}


def testAntigravityCapabilityContractTracksCredentialEvidence():
    dictManual = antigravity.fdictBuildAntigravityCapabilityContract()
    assert dictManual["sProvider"] == "gemini"
    assert dictManual["dictModelDiscovery"]["bVerified"] is False
    dictVerified = antigravity.fdictBuildAntigravityCapabilityContract(
        {"listModelIds": ["modelBeta"]}, bRunnerBackendEnabled=True)
    assert dictVerified["bAvailable"] is True
    assert dictVerified["dictModelDiscovery"]["listModelIds"] == [
        "modelBeta"]


def testAntigravityArgvCarriesTheSchemaOnlyForStructuredTurns():
    saStructured = antigravity.flistComposeAntigravityArgv("modelAlpha")
    saChat = antigravity.flistComposeAntigravityArgv(
        "modelAlpha", saCliProgram=["fakeAgy"], bStructured=False)
    assert saStructured[0] == "agy"
    assert "--json-schema" in saStructured
    assert saChat[0] == "fakeAgy"
    assert "--json-schema" not in saChat


# ----- Antigravity result parsing -----------------------------------------


def flistResultWithResponse(sResponse):
    return [{"event": "result", "result": {"response": sResponse}}]


@pytest.mark.parametrize("sResponse, jsonExpected", [
    ('{"sVerdict": "accept"}\n  {"sVerdict": "accept"}  ',
     {"sVerdict": "accept"}),
    ('```\n{"sVerdict": "accept"}\n```', {"sVerdict": "accept"}),
    ('```python\n{"sVerdict": "accept"}\n```',
     {"sRawResultText": '```python\n{"sVerdict": "accept"}\n```'}),
    ('```{"sVerdict": "accept"}```',
     {"sRawResultText": '```{"sVerdict": "accept"}```'}),
    ('[1, 2]', {"sRawResultText": "[1, 2]"}),
    ('{"sVerdict": "accept"} trailing prose',
     {"sRawResultText": '{"sVerdict": "accept"} trailing prose'}),
])
def testAntigravityResponsesAreAdoptedOnlyWhenUnambiguous(
        sResponse, jsonExpected):
    assert antigravity.fdictExtractAntigravityStructuredResult(
        flistResultWithResponse(sResponse)) == jsonExpected


def testAnAntigravityStreamWithNoResultSaysSo():
    dictResult = antigravity.fdictExtractAntigravityStructuredResult(
        [{"event": "init", "init": {}}])
    assert dictResult["sRawResultText"] == ""
    assert dictResult["sEmptyResultReason"] == "noResultEvent"


def testAnAntigravityResponseOfOnlyWhitespaceIsAnEmptyAnswer():
    dictResult = antigravity.fdictExtractAntigravityStructuredResult(
        flistResultWithResponse("   "))
    assert dictResult["sRawResultText"] == ""
    assert dictResult["sEmptyResultReason"] == "resultEventCarriedNoText"


def testTheLastAntigravityResultEventIsTheOneAdopted():
    listNative = [
        {"event": "result", "result": {"structured_output": {"iRound": 1}}},
        {"event": "result", "result": {"structured_output": {"iRound": 2}}},
    ]
    assert antigravity.fdictExtractAntigravityStructuredResult(
        listNative) == {"iRound": 2}


def testAntigravityNormalizationMarksAFailedStatusAsAnError():
    listEvents = antigravity.flistNormalizeAntigravityEvents([
        {"event": "step_update", "step_update": {
            "step_type": "tool_call", "text_delta": "ignored"}},
        {"event": "step_update", "step_update": {
            "step_type": "agent_response", "text_delta": ""}},
        {"event": "result", "result": {
            "status": "ERROR", "error": "quota exhausted"}},
    ])
    assert listEvents == [{
        "type": "result", "result": "quota exhausted", "is_error": True,
        "usage": {}, "subtype": "antigravityHeadless"}]


def testAntigravityNormalizationWithNoResultIsANonErrorEmptyResult():
    listEvents = antigravity.flistNormalizeAntigravityEvents([])
    assert listEvents[-1]["result"] == ""
    assert listEvents[-1]["is_error"] is False


def testAntigravityStdinIsOneUserEventCarryingTheQuotedMaterial():
    baStdin = antigravity.fbaComposeAntigravityStdin([{
        "sSourceKind": "peerTurn", "sAuthorIdentity": "participantBeta",
        "sContent": "--agent attacker"}])
    assert baStdin.endswith(b"\n")
    dictEvent = json.loads(baStdin.decode("utf-8"))
    assert dictEvent["event"] == "user"
    assert "--agent attacker" in dictEvent["message"]["content"]
    assert "(author: participantBeta)" in dictEvent["message"]["content"]


# ----- Claude adapter branches --------------------------------------------


def testAStallIsNamedOnlyWhenNoBoundWasBreached():
    dictStalled = providers.fdictExtractStructuredResult(
        [], {"bStalled": True, "fStallSeconds": 600.0, "iExitCode": None})
    assert dictStalled["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_STALL)
    assert dictStalled["fStallSeconds"] == 600.0
    dictCapFirst = providers.fdictExtractStructuredResult(
        [], {"bStalled": True, "bOutputCapExceeded": True})
    assert dictCapFirst["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_OUTPUT_CAP)


def testASingleModelUsageKeyResolvesTheModelWhenNoInitNamedIt():
    listEvents = [{"type": "result", "result": "{}",
                   "modelUsage": {"modelAlpha-2026": {"outputTokens": 1}}}]
    dictIdentity = providers.fdictExtractModelIdentity(listEvents, "alpha")
    assert dictIdentity["sResolvedModel"] == "modelAlpha-2026"
    listAmbiguous = [{"type": "result", "result": "{}", "modelUsage": {
        "modelAlpha": {}, "modelBeta": {}}}]
    assert providers.fdictExtractModelIdentity(
        listAmbiguous, "alpha")["sResolvedModel"] == ""


def testAMalformedModelUsageIsNeverRecordedAsAMapping():
    dictIdentity = providers.fdictExtractModelIdentity(
        [{"type": "result", "modelUsage": ["notAMapping"]}], "alpha")
    assert dictIdentity["dictModelUsage"] == {}
    assert dictIdentity["sResolvedModel"] == ""


def testLiveModelDiscoveryIsUsedWhenAnApiKeyIsConfigured(monkeypatch):
    listKeys = []
    monkeypatch.setattr(
        providers.providerApiTransport, "flistDiscoverAnthropicModels",
        lambda sApiKey: listKeys.append(sApiKey) or ["modelAlpha-live"])
    dictDiscovered = providers.fdictDiscoverClaudeModels("keyAlpha")
    assert dictDiscovered == {"sSource": "anthropicApiLiveDiscovery",
                              "bVerified": True,
                              "listModelIds": ["modelAlpha-live"]}
    assert listKeys == ["keyAlpha"]


def testAnUndecodableClaudeLoginIsRefusedAsUnreadable():
    with pytest.raises(providers.RunnerCredentialError,
                       match="not readable JSON"):
        providers.fdictExtractRunnerCredential(
            _CredentialReadFake(b"\x80\x81 binary"), S_CONTAINER_ID,
            "/workspace/projectAlpha/.claude/.credentials.json")
