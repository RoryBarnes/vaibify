"""Antigravity CLI adapter exposing Gemini models to Agent Council."""

import asyncio
import datetime
import json
import posixpath
import re
import time

from . import agentCouncilDockerGateway
from . import agentCouncilProviders
from . import agentCouncilRunner
from ..config import secretManager

__all__ = [
    "AntigravityRunnerConnection",
    "S_PROVIDER_GEMINI",
    "S_ANTIGRAVITY_HOME_ENV",
    "S_RUNNER_ANTIGRAVITY_HOME",
    "LIST_ANTIGRAVITY_EGRESS_HOSTNAMES",
    "fdictBuildAntigravityCapabilityContract",
    "fdictExtractAntigravityRunnerCredential",
    "fdictExtractAntigravityStructuredResult",
    "fbaBuildAntigravityConfigTarball",
    "fbaComposeAntigravityStdin",
    "flistComposeAntigravityArgv",
    "flistNormalizeAntigravityEvents",
    "fsComposeAntigravityCredentialContainerPath",
    "fsExplainUnusableAntigravityCredential",
    "fsStageAntigravityRunnerCredentialFile",
]

S_PROVIDER_GEMINI = "gemini"
LIST_ANTIGRAVITY_EGRESS_HOSTNAMES = [
    "antigravity-unleash.goog",
    "daily-cloudcode-pa.googleapis.com",
    "cloudcode-pa.googleapis.com",
    "lh3.googleusercontent.com",
    "www.googleapis.com",
]
S_ANTIGRAVITY_CONFIG_COMPONENT = ".gemini"
S_ANTIGRAVITY_CREDENTIAL_BASENAME = "antigravity-oauth-token"
S_RUNNER_ANTIGRAVITY_HOME = "/tmp/vaibifyCouncilAntigravity"
S_RUNNER_ANTIGRAVITY_SCHEMA_PATH = (
    S_RUNNER_ANTIGRAVITY_HOME + "/turn-schema.json")
S_ANTIGRAVITY_HOME_ENV = "HOME"
S_ANTIGRAVITY_AGENT_NAME = "vaibify-council"
LIST_ANTIGRAVITY_CLI_PROGRAM = ["agy"]


def fsComposeAntigravityCredentialContainerPath(sWorkspaceRoot):
    """Compose the persisted Antigravity login path below a project root."""
    if not sWorkspaceRoot or not posixpath.isabs(sWorkspaceRoot):
        raise agentCouncilProviders.RunnerCredentialError(
            "the workspace root must be an absolute container path")
    return posixpath.join(
        sWorkspaceRoot, S_ANTIGRAVITY_CONFIG_COMPONENT,
        "antigravity-cli", S_ANTIGRAVITY_CREDENTIAL_BASENAME)


def _fiParseExpiryMilliseconds(jsonExpiry):
    """Parse the Antigravity expiry form into epoch milliseconds."""
    if isinstance(jsonExpiry, (int, float)):
        fExpiry = float(jsonExpiry)
        return int(fExpiry if fExpiry > 10_000_000_000 else fExpiry * 1000)
    if not isinstance(jsonExpiry, str) or not jsonExpiry:
        return 0
    try:
        sNormalizedExpiry = re.sub(
            r"(\.\d{6})\d+(?=Z|[+-]\d\d:\d\d$)", r"\1", jsonExpiry)
        fExpiry = datetime.datetime.fromisoformat(
            sNormalizedExpiry.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0
    return int(fExpiry * 1000)


def _fnRefuseExpiredAntigravityAccess(iExpiresAtEpochMilliseconds):
    if iExpiresAtEpochMilliseconds <= 0:
        return
    fSecondsRemaining = iExpiresAtEpochMilliseconds / 1000.0 - time.time()
    if fSecondsRemaining >= (
            agentCouncilProviders.I_MINIMUM_TURN_WALL_CLOCK_SECONDS):
        return
    raise agentCouncilProviders.RunnerCredentialError(
        "the project's Antigravity access token has expired or is too "
        "near expiry for a council turn. Run `agy` in this project's "
        "container and log in again, then retry.")


def fdictExtractAntigravityRunnerCredential(
        connectionDocker, sContainerId, sCredentialContainerPath):
    """Extract Antigravity access-token fields without its refresh token."""
    try:
        baContent = connectionDocker.fbaFetchCredentialFile(
            sContainerId, sCredentialContainerPath)
    except FileNotFoundError as errorMissing:
        raise agentCouncilProviders.RunnerCredentialError(
            "no persisted Antigravity login was found in this project") from (
                errorMissing)
    except ValueError as errorOversize:
        raise agentCouncilProviders.RunnerCredentialError(
            "the Antigravity login file is too large to be a login document") from (
                errorOversize)
    try:
        dictLogin = json.loads(baContent.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as errorParse:
        raise agentCouncilProviders.RunnerCredentialError(
            "the persisted Antigravity login is not readable JSON") from (
                errorParse)
    dictToken = dictLogin.get("token") or {}
    if not isinstance(dictToken, dict):
        dictToken = {}
    sAccessToken = dictToken.get("access_token")
    if not isinstance(sAccessToken, str) or not sAccessToken:
        raise agentCouncilProviders.RunnerCredentialError(
            "the persisted Antigravity login carries no access token to copy")
    iExpiresAt = _fiParseExpiryMilliseconds(dictToken.get("expiry"))
    _fnRefuseExpiredAntigravityAccess(iExpiresAt)
    return {
        "sAccessToken": sAccessToken,
        "sTokenType": str(dictToken.get("token_type") or "Bearer"),
        "jsonExpiry": dictToken.get("expiry"),
        "sAuthMethod": str(dictLogin.get("auth_method") or "oauth"),
        "iExpiresAtEpochMilliseconds": iExpiresAt,
    }


def fsExplainUnusableAntigravityCredential(
        connectionDocker, sContainerId, sCredentialContainerPath):
    """Return why the Antigravity login cannot be copied, or empty."""
    try:
        fdictExtractAntigravityRunnerCredential(
            connectionDocker, sContainerId, sCredentialContainerPath)
    except agentCouncilProviders.RunnerCredentialError as errorCredential:
        return str(errorCredential)
    return ""


def fsStageAntigravityRunnerCredentialFile(dictCredential):
    """Materialize the measured minimum refresh-free Antigravity login."""
    dictToken = {
        "access_token": dictCredential["sAccessToken"],
        "token_type": dictCredential["sTokenType"],
        "expiry": dictCredential["jsonExpiry"],
    }
    dictLogin = {
        "token": dictToken,
        "auth_method": dictCredential["sAuthMethod"],
    }
    return secretManager.fsMaterializeSecretValue(
        "antigravityCouncilAccessToken", json.dumps(dictLogin))


def fdictBuildAntigravityCapabilityContract(
        dictEvidenceRecord=None, bRunnerBackendEnabled=False):
    """Describe Gemini models exposed through the Antigravity backend."""
    listModelIds = list((dictEvidenceRecord or {}).get("listModelIds") or [])
    return {
        "sProvider": S_PROVIDER_GEMINI,
        "sBackend": "antigravityRunner",
        "bAvailable": bRunnerBackendEnabled,
        "dictModelDiscovery": {
            "sSource": ("credentialEvidenceModelCatalog"
                        if listModelIds else "manualEntry"),
            "bVerified": bool(listModelIds),
            "listModelIds": listModelIds,
        },
        "saEgressAllowlist": list(LIST_ANTIGRAVITY_EGRESS_HOSTNAMES),
    }


def flistComposeAntigravityArgv(sModelId, saCliProgram=None,
                                bStructured=True):
    """Compose the fixed noninteractive Antigravity command surface."""
    saProgram = list(saCliProgram) if saCliProgram else list(
        LIST_ANTIGRAVITY_CLI_PROGRAM)
    saArgv = saProgram + [
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", sModelId,
        "--agent", S_ANTIGRAVITY_AGENT_NAME,
        "--sandbox", "--disable-slash-commands",
    ]
    if bStructured:
        saArgv.extend(["--json-schema", S_RUNNER_ANTIGRAVITY_SCHEMA_PATH])
    return saArgv


def fbaComposeAntigravityStdin(listQuotedMaterial):
    """Encode one final user event and let EOF end the headless session."""
    sPrompt = agentCouncilProviders.fsComposeUntrustedPromptText(
        listQuotedMaterial)
    return (json.dumps({
        "event": "user", "message": {"content": sPrompt},
    }, separators=(",", ":")) + "\n").encode("utf-8")


def _flistDecodeConcatenatedObjects(sText):
    """Decode whitespace-separated JSON objects from one response string."""
    iOffset = 0
    listObjects = []
    while iOffset < len(sText):
        while iOffset < len(sText) and sText[iOffset].isspace():
            iOffset += 1
        if iOffset >= len(sText):
            break
        try:
            jsonObject, iOffset = json.JSONDecoder().raw_decode(
                sText, iOffset)
        except ValueError:
            return []
        if not isinstance(jsonObject, dict):
            return []
        listObjects.append(jsonObject)
    return listObjects


def _fsUnwrapSingleJsonFence(sText):
    """Unwrap one complete fenced result without guessing through prose."""
    sCandidate = sText.strip()
    if not sCandidate.startswith("```") or not sCandidate.endswith("```"):
        return sCandidate
    iFirstLineEnd = sCandidate.find("\n")
    if iFirstLineEnd < 0:
        return sCandidate
    sOpening = sCandidate[:iFirstLineEnd].strip().lower()
    if sOpening not in ("```", "```json"):
        return sCandidate
    return sCandidate[iFirstLineEnd + 1:-3].strip()


def fdictExtractAntigravityStructuredResult(listNativeEvents,
                                             dictExecution=None):
    """Extract one result, accepting only identical schema retry outputs."""
    dictResult = {}
    for dictEvent in reversed(listNativeEvents):
        if dictEvent.get("event") == "result":
            dictResult = dictEvent.get("result") or {}
            break
    jsonStructured = dictResult.get("structured_output")
    if isinstance(jsonStructured, dict):
        return jsonStructured
    sResponse = dictResult.get("response")
    if isinstance(sResponse, str):
        listObjects = _flistDecodeConcatenatedObjects(
            _fsUnwrapSingleJsonFence(sResponse))
        if listObjects and all(
                jsonObject == listObjects[0] for jsonObject in listObjects):
            return listObjects[0]
        return {"sRawResultText": sResponse}
    return {"sRawResultText": "", "sEmptyResultReason": (
        "antigravityProducedNoResult")}


def flistNormalizeAntigravityEvents(listNativeEvents):
    """Map Antigravity events into the common display event shape."""
    listEvents = []
    for dictNative in listNativeEvents:
        if dictNative.get("event") == "init":
            dictInit = dictNative.get("init") or {}
            listEvents.append({"type": "system", "model": dictInit.get(
                "model", ""), "providerEventType": "antigravityInit"})
        elif dictNative.get("event") == "step_update":
            dictStep = dictNative.get("step_update") or {}
            sText = dictStep.get("text_delta")
            if sText and dictStep.get("step_type") == "agent_response":
                listEvents.append({"type": "assistant", "message": {
                    "content": [{"type": "text", "text": sText}]}})
    dictTerminal = next((
        (dictEvent.get("result") or {}) for dictEvent in reversed(
            listNativeEvents) if dictEvent.get("event") == "result"), {})
    listEvents.append({
        "type": "result",
        "result": str(dictTerminal.get("response") or dictTerminal.get(
            "error") or ""),
        "is_error": str(dictTerminal.get("status") or "").upper()
                    not in ("", "SUCCESS"),
        "usage": dictTerminal.get("usage") or {},
        "subtype": "antigravityHeadless",
    })
    return listEvents


def _fsComposeAgentDocument(sInstructionChannel):
    """Compose the custom agent carrying the server-owned charter."""
    return (
        "---\nname: vaibify-council\n"
        "description: Vaibify Agent Council participant\n---\n\n"
        + sInstructionChannel + "\n")


def fbaBuildAntigravityConfigTarball(sCredentialPath,
                                     sInstructionChannel,
                                     dictOutputSchema=None):
    with open(sCredentialPath, "rb") as fileCredential:
        baCredential = fileCredential.read()
    dictSettings = {
        "permissions": {
            "allow": ["read_file(/council)", "write_file(/council)",
                      "command(*)"],
            "deny": ["read_url(*)", "execute_url(*)", "mcp(*)",
                     "unsandboxed(*)"],
            "ask": [],
        }
    }
    return agentCouncilRunner.fbaBuildStampedFilesTarball({
        "vaibifyCouncilAntigravity/.gemini/antigravity-cli/"
        "antigravity-oauth-token": baCredential,
        "vaibifyCouncilAntigravity/.gemini/antigravity-cli/settings.json":
            json.dumps(dictSettings).encode("utf-8"),
        "vaibifyCouncilAntigravity/.gemini/config/agents/"
        "vaibify-council/agent.md": _fsComposeAgentDocument(
            sInstructionChannel).encode("utf-8"),
        "vaibifyCouncilAntigravity/turn-schema.json": json.dumps(
            dictOutputSchema or {"type": "object"}).encode("utf-8"),
    })


class AntigravityRunnerConnection(
        agentCouncilProviders.ClaudeRunnerConnection):
    """One disposable Antigravity runner per Gemini council turn."""

    sProvider = S_PROVIDER_GEMINI

    def _fdictComposeRunnerEnvironment(self):
        dictEnvironment = super()._fdictComposeRunnerEnvironment()
        dictEnvironment.pop(
            agentCouncilProviders.S_CLAUDE_CONFIG_DIRECTORY_ENV, None)
        dictEnvironment[S_ANTIGRAVITY_HOME_ENV] = (
            S_RUNNER_ANTIGRAVITY_HOME)
        return dictEnvironment

    def _fbaBuildTurnCredentialTarball(self, dictTurnRequest):
        if self.ftStageRunnerCredential is None:
            return None
        sStagedPath, self._iLoginExpiresAtEpochMilliseconds = (
            self.ftStageRunnerCredential())
        try:
            return fbaBuildAntigravityConfigTarball(
                sStagedPath, dictTurnRequest["sInstructionChannel"],
                dictTurnRequest["dictOutputSchema"])
        finally:
            secretManager.fnCleanupSecretFiles([sStagedPath])

    async def fnStartTurn(self, dictTurnRequest):
        saArgv = flistComposeAntigravityArgv(
            self.sRequestedModel, self.saCliProgram)
        fEffectiveWallClock = agentCouncilProviders.ffClampTurnBudgetToLoginLife(
            self.fWallClockSeconds,
            self._iLoginExpiresAtEpochMilliseconds)
        try:
            self._dictTurnExecution = await asyncio.to_thread(
                agentCouncilDockerGateway.fdictExecuteBoundedTurn,
                self.dictGateway, self._sHandle, saArgv,
                self.iOutputByteCap, fEffectiveWallClock,
                agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT,
                fbaComposeAntigravityStdin(
                    dictTurnRequest["listQuotedMaterial"]),
                self.fStallSeconds)
            listNativeEvents = agentCouncilProviders.flistParseStreamJsonEvents(
                self._dictTurnExecution["sOutput"])
            self._listEvents = flistNormalizeAntigravityEvents(
                listNativeEvents)
            self._listNativeEvents = listNativeEvents
            dictInit = next((
                dictEvent.get("init") or {} for dictEvent in listNativeEvents
                if dictEvent.get("event") == "init"), {})
            sResolvedModel = str(dictInit.get("model") or "")
            if dictInit.get("agent") not in (None, S_ANTIGRAVITY_AGENT_NAME):
                sResolvedModel = ""
            self.dictModelIdentity = {
                "sRequestedModel": self.sRequestedModel,
                "sResolvedModel": sResolvedModel,
                "sResolutionEvidence": "antigravityInitEvent",
                "dictUsage": self._listEvents[-1].get("usage") or {},
                "dictModelUsage": {},
            }
        except BaseException:
            await self._fnDestroyHandleAfterFailure()
            raise

    async def fdictCollectStructuredResult(self):
        try:
            return fdictExtractAntigravityStructuredResult(
                self._listNativeEvents, self._dictTurnExecution)
        except BaseException:
            await self._fnDestroyHandleAfterFailure()
            raise
