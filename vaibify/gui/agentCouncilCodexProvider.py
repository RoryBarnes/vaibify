"""Codex CLI runner adapter for Agent Council subscription turns."""

import asyncio
import base64
import json
import posixpath
import time

from . import agentCouncilDockerGateway
from . import agentCouncilEgress
from . import agentCouncilProviders
from . import agentCouncilRunner
from ..config import secretManager

__all__ = [
    "CodexRunnerConnection",
    "S_PROVIDER_CODEX",
    "S_CODEX_CONFIG_DIRECTORY_ENV",
    "S_CODEX_HOME_ENV",
    "S_RUNNER_CODEX_CONFIG_DIRECTORY",
    "S_RUNNER_CODEX_HOME",
    "LIST_CODEX_EGRESS_HOSTNAMES",
    "fdictBuildCodexCapabilityContract",
    "fdictExtractCodexRunnerCredential",
    "fbaBuildCodexConfigTarball",
    "flistComposeCodexArgv",
    "flistNormalizeCodexEvents",
    "fsComposeCodexCredentialContainerPath",
    "fsExplainUnusableCodexCredential",
    "fsStageCodexRunnerCredentialFile",
]

S_PROVIDER_CODEX = "codex"
LIST_CODEX_EGRESS_HOSTNAMES = ["chatgpt.com"]
S_CODEX_CONFIG_COMPONENT = ".codex"
S_CODEX_CREDENTIAL_BASENAME = "auth.json"
S_RUNNER_CODEX_HOME = "/tmp/vaibifyCouncilCodex"
S_RUNNER_CODEX_CONFIG_DIRECTORY = S_RUNNER_CODEX_HOME + "/.codex"
S_RUNNER_CODEX_SCHEMA_PATH = S_RUNNER_CODEX_HOME + "/turn-schema.json"
S_CODEX_CONFIG_DIRECTORY_ENV = "CODEX_HOME"
S_CODEX_HOME_ENV = "HOME"
LIST_CODEX_CLI_PROGRAM = ["codex"]


def fsComposeCodexCredentialContainerPath(sWorkspaceRoot):
    """Compose the persisted Codex login path below a project root."""
    if not sWorkspaceRoot or not posixpath.isabs(sWorkspaceRoot):
        raise agentCouncilProviders.RunnerCredentialError(
            "the workspace root must be an absolute container path")
    return posixpath.join(
        sWorkspaceRoot, S_CODEX_CONFIG_COMPONENT,
        S_CODEX_CREDENTIAL_BASENAME)


def _fdictDecodeJwtClaims(sToken, sFieldName):
    """Decode one JWT payload locally without logging or verifying it."""
    try:
        sPayload = sToken.split(".")[1]
        sPadded = sPayload + "=" * (-len(sPayload) % 4)
        jsonClaims = json.loads(base64.urlsafe_b64decode(
            sPadded.encode("ascii")).decode("utf-8"))
    except (IndexError, ValueError, UnicodeDecodeError) as error:
        raise agentCouncilProviders.RunnerCredentialError(
            f"the persisted Codex {sFieldName} is not a readable JWT") from error
    if not isinstance(jsonClaims, dict):
        raise agentCouncilProviders.RunnerCredentialError(
            f"the persisted Codex {sFieldName} has no claim mapping")
    return jsonClaims


def _fiReadAccessExpiry(sAccessToken):
    """Read the access token expiry as epoch milliseconds, or zero."""
    dictClaims = _fdictDecodeJwtClaims(sAccessToken, "access token")
    jsonExpiry = dictClaims.get("exp")
    return int(float(jsonExpiry) * 1000) if isinstance(
        jsonExpiry, (int, float)) else 0


def _ftReadCodexRoutingClaims(sIdToken):
    """Return only the non-personal routing claims required by the CLI."""
    dictClaims = _fdictDecodeJwtClaims(sIdToken, "identity token")
    dictAuthentication = dictClaims.get(
        "https://api.openai.com/auth") or {}
    if not isinstance(dictAuthentication, dict):
        dictAuthentication = {}
    sAccountId = str(
        dictAuthentication.get("chatgpt_account_id") or "")
    sPlanType = str(dictAuthentication.get("chatgpt_plan_type") or "")
    if not sAccountId:
        raise agentCouncilProviders.RunnerCredentialError(
            "the persisted Codex login carries no ChatGPT account routing id")
    return sAccountId, sPlanType


def fdictExtractCodexRunnerCredential(connectionDocker, sContainerId,
                                      sCredentialContainerPath):
    """Extract a refresh-free, API-key-free Codex credential subset."""
    try:
        baContent = connectionDocker.fbaFetchCredentialFile(
            sContainerId, sCredentialContainerPath)
    except FileNotFoundError as errorMissing:
        raise agentCouncilProviders.RunnerCredentialError(
            "no persisted Codex login was found in this project") from errorMissing
    except ValueError as errorOversize:
        raise agentCouncilProviders.RunnerCredentialError(
            "the Codex login file is too large to be a login document") from errorOversize
    try:
        dictLogin = json.loads(baContent.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as errorParse:
        raise agentCouncilProviders.RunnerCredentialError(
            "the persisted Codex login is not readable JSON") from errorParse
    dictTokens = dictLogin.get("tokens") or {}
    if not isinstance(dictTokens, dict):
        dictTokens = {}
    sAccessToken = dictTokens.get("access_token")
    sIdToken = dictTokens.get("id_token")
    if not isinstance(sAccessToken, str) or not sAccessToken:
        raise agentCouncilProviders.RunnerCredentialError(
            "the persisted Codex login carries no access token to copy")
    if not isinstance(sIdToken, str) or not sIdToken:
        raise agentCouncilProviders.RunnerCredentialError(
            "the persisted Codex login carries no identity routing document")
    sAccountId, sPlanType = _ftReadCodexRoutingClaims(sIdToken)
    sRecordedAccountId = str(dictTokens.get("account_id") or "")
    if sRecordedAccountId and sRecordedAccountId != sAccountId:
        raise agentCouncilProviders.RunnerCredentialError(
            "the Codex login's account identifiers disagree")
    iExpiresAt = _fiReadAccessExpiry(sAccessToken)
    _fnRefuseExpiredCodexAccess(iExpiresAt)
    return {
        "sAccessToken": sAccessToken,
        "sAccountId": sAccountId,
        "sPlanType": sPlanType,
        "iExpiresAtEpochMilliseconds": iExpiresAt,
    }


def _fnRefuseExpiredCodexAccess(iExpiresAtEpochMilliseconds):
    """Refuse a Codex token too near expiry for a minimum turn."""
    if iExpiresAtEpochMilliseconds <= 0:
        return
    fSecondsRemaining = iExpiresAtEpochMilliseconds / 1000.0 - time.time()
    if fSecondsRemaining >= (
            agentCouncilProviders.I_MINIMUM_TURN_WALL_CLOCK_SECONDS):
        return
    raise agentCouncilProviders.RunnerCredentialError(
        "the project's Codex access token has expired or is too near "
        "expiry for a council turn. Run `codex login` in this project's "
        "container, then try again.")


def fsExplainUnusableCodexCredential(connectionDocker, sContainerId,
                                     sCredentialContainerPath):
    """Return why the Codex login cannot be copied, or an empty string."""
    try:
        fdictExtractCodexRunnerCredential(
            connectionDocker, sContainerId, sCredentialContainerPath)
    except agentCouncilProviders.RunnerCredentialError as errorCredential:
        return str(errorCredential)
    return ""


def _fsEncodeJwtPart(dictPayload):
    baEncoded = base64.urlsafe_b64encode(
        json.dumps(dictPayload, separators=(",", ":")).encode("utf-8"))
    return baEncoded.rstrip(b"=").decode("ascii")


def _fsBuildSyntheticRoutingToken(sAccountId, sPlanType):
    """Build the PII-free JWT-shaped routing shell accepted by Codex."""
    dictAuthentication = {"chatgpt_account_id": sAccountId}
    if sPlanType:
        dictAuthentication["chatgpt_plan_type"] = sPlanType
    dictClaims = {
        "https://api.openai.com/auth": dictAuthentication,
        "exp": int(time.time()) + 3600,
    }
    return ".".join((
        _fsEncodeJwtPart({"alg": "none", "typ": "JWT"}),
        _fsEncodeJwtPart(dictClaims),
        "vaibify",
    ))


def fsStageCodexRunnerCredentialFile(dictCredential):
    """Materialize the measured minimum Codex ChatGPT login document."""
    dictLogin = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": _fsBuildSyntheticRoutingToken(
                dictCredential["sAccountId"], dictCredential["sPlanType"]),
            "access_token": dictCredential["sAccessToken"],
            "refresh_token": "",
            "account_id": dictCredential["sAccountId"],
        },
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return secretManager.fsMaterializeSecretValue(
        "codexCouncilAccessToken", json.dumps(dictLogin))


def fbaBuildCodexConfigTarball(sCredentialPath, dictOutputSchema=None):
    """Build the runner config tree from a staged minimal login."""
    with open(sCredentialPath, "rb") as fileCredential:
        baCredential = fileCredential.read()
    return agentCouncilRunner.fbaBuildStampedFilesTarball({
        "vaibifyCouncilCodex/.codex/auth.json": baCredential,
        "vaibifyCouncilCodex/turn-schema.json": json.dumps(
            dictOutputSchema or {"type": "object"}).encode("utf-8"),
    })


def fdictBuildCodexCapabilityContract(dictEvidenceRecord=None,
                                      bRunnerBackendEnabled=False):
    """Describe Codex availability and evidence-backed model discovery."""
    listModelIds = list((dictEvidenceRecord or {}).get("listModelIds") or [])
    return {
        "sProvider": S_PROVIDER_CODEX,
        "sBackend": "runner",
        "bAvailable": bRunnerBackendEnabled,
        "dictModelDiscovery": {
            "sSource": ("credentialEvidenceModelCatalog"
                        if listModelIds else "manualEntry"),
            "bVerified": bool(listModelIds),
            "listModelIds": listModelIds,
        },
        "saEgressAllowlist": list(LIST_CODEX_EGRESS_HOSTNAMES),
    }


def flistComposeCodexArgv(sModelId, sInstructionChannel,
                          saCliProgram=None, bStructured=True):
    """Compose a fixed Codex exec argv with a separate instruction lane."""
    saProgram = list(saCliProgram) if saCliProgram else list(
        LIST_CODEX_CLI_PROGRAM)
    saArgv = saProgram + [
        "exec", "--ephemeral", "--json", "--ignore-user-config",
        "--ignore-rules", "--strict-config", "--sandbox", "read-only",
        "--skip-git-repo-check", "--model", sModelId,
        "-c", f"developer_instructions={json.dumps(sInstructionChannel)}",
        "-c", 'web_search="disabled"',
    ]
    if bStructured:
        saArgv.extend(["--output-schema", S_RUNNER_CODEX_SCHEMA_PATH])
    return saArgv + ["--cd", agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT, "-"]


def flistNormalizeCodexEvents(sStreamText, iExitCode):
    """Map Codex JSONL into the provider-neutral Claude-shaped stream."""
    listNative = agentCouncilProviders.flistParseStreamJsonEvents(sStreamText)
    listEvents = []
    sFinalText = ""
    dictUsage = {}
    sFailure = ""
    for dictNative in listNative:
        sType = dictNative.get("type")
        dictItem = dictNative.get("item") or {}
        if sType == "item.completed" and dictItem.get("type") == (
                "agent_message"):
            sFinalText = str(dictItem.get("text") or "")
            listEvents.append({"type": "assistant", "message": {
                "content": [{"type": "text", "text": sFinalText}]}})
        elif sType == "turn.completed":
            dictUsage = dictNative.get("usage") or {}
        elif sType in ("turn.failed", "error"):
            sFailure = str(dictNative.get("message") or dictNative)
    if not sFinalText and iExitCode not in (0, None):
        sFailure = sFailure or "Codex exited before producing an answer"
    listEvents.append({
        "type": "result",
        "result": sFinalText or sFailure,
        "is_error": bool(sFailure and not sFinalText),
        "usage": dictUsage,
        "subtype": "codexExec",
    })
    return listEvents


class CodexRunnerConnection(agentCouncilProviders.ClaudeRunnerConnection):
    """One disposable Codex runner per council turn."""

    sProvider = S_PROVIDER_CODEX

    def _fdictComposeRunnerEnvironment(self):
        dictEnvironment = super()._fdictComposeRunnerEnvironment()
        dictEnvironment.pop(
            agentCouncilProviders.S_CLAUDE_CONFIG_DIRECTORY_ENV, None)
        dictEnvironment[S_CODEX_HOME_ENV] = S_RUNNER_CODEX_HOME
        dictEnvironment[S_CODEX_CONFIG_DIRECTORY_ENV] = (
            S_RUNNER_CODEX_CONFIG_DIRECTORY)
        return dictEnvironment

    def _fbaBuildTurnCredentialTarball(self, dictTurnRequest):
        if self.ftStageRunnerCredential is None:
            return None
        sStagedPath, self._iLoginExpiresAtEpochMilliseconds = (
            self.ftStageRunnerCredential())
        try:
            return fbaBuildCodexConfigTarball(
                sStagedPath, dictTurnRequest["dictOutputSchema"])
        finally:
            secretManager.fnCleanupSecretFiles([sStagedPath])

    async def fnStartTurn(self, dictTurnRequest):
        saArgv = flistComposeCodexArgv(
            self.sRequestedModel, dictTurnRequest["sInstructionChannel"],
            self.saCliProgram)
        baStdin = agentCouncilProviders.fsComposeUntrustedPromptText(
            dictTurnRequest["listQuotedMaterial"]).encode("utf-8")
        fEffectiveWallClock = agentCouncilProviders.ffClampTurnBudgetToLoginLife(
            self.fWallClockSeconds,
            self._iLoginExpiresAtEpochMilliseconds)
        try:
            self._dictTurnExecution = await asyncio.to_thread(
                agentCouncilDockerGateway.fdictExecuteBoundedTurn,
                self.dictGateway, self._sHandle, saArgv,
                self.iOutputByteCap, fEffectiveWallClock,
                agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT, baStdin,
                self.fStallSeconds)
            self._listEvents = flistNormalizeCodexEvents(
                self._dictTurnExecution["sOutput"],
                self._dictTurnExecution["iExitCode"])
            self.dictModelIdentity = {
                "sRequestedModel": self.sRequestedModel,
                "sResolvedModel": (
                    self.sRequestedModel
                    if self._dictTurnExecution["iExitCode"] == 0 else ""),
                "sResolutionEvidence": "pinnedModelFlag",
                "dictUsage": self._listEvents[-1].get("usage") or {},
                "dictModelUsage": {},
            }
        except BaseException:
            await self._fnDestroyHandleAfterFailure()
            raise
