"""Closed registry of reviewed Agent Council provider adapters."""

from . import agentCouncilAntigravityProvider
from . import agentCouncilCodexProvider
from . import agentCouncilEgress
from . import agentCouncilProviders

__all__ = [
    "SET_COUNCIL_PROVIDERS",
    "fdictBuildProviderCapability",
    "fdictComposeProviderRunnerEnvironment",
    "fdictExtractProviderCredential",
    "fconnectionBuildProviderConnection",
    "flistCollectProviderEgressHostnames",
    "flistComposeProviderChatArgv",
    "fbaBuildProviderConfigTarball",
    "fbaComposeProviderChatStdin",
    "fsComposeProviderCredentialPath",
    "fsExplainUnusableProviderCredential",
    "fsComposeProviderEgressScope",
    "fsGetProviderCredentialSchema",
    "fsStageProviderCredential",
    "ftParseProviderChatResult",
]

SET_COUNCIL_PROVIDERS = frozenset({"claude", "codex", "gemini"})


def _fdictRequireProvider(sProvider):
    """Return one closed adapter descriptor or reject the provider."""
    dictAdapters = {
        "claude": {
            "sCredentialSchema": "claudeAiOauth.accessToken",
            "listEgressHostnames": [
                agentCouncilProviders.S_ANTHROPIC_API_HOSTNAME],
            "fconnectionClass": agentCouncilProviders.ClaudeRunnerConnection,
            "fsComposeCredentialPath":
                agentCouncilProviders.fsComposeCredentialContainerPath,
            "fdictExtractCredential":
                agentCouncilProviders.fdictExtractRunnerCredential,
            "fsExplainCredential":
                agentCouncilProviders.fsExplainUnusableRunnerCredential,
            "fsStageCredential": lambda dictCredential:
                agentCouncilProviders.fsStageRunnerCredentialFile(
                    dictCredential["sAccessToken"],
                    dictCredential["listScopes"]),
            "fdictBuildCapability": lambda dictRecord, bEnabled:
                agentCouncilProviders.fdictClaudeCapabilityContract(
                    bRunnerBackendEnabled=bEnabled),
        },
        "codex": {
            "sCredentialSchema": "codexChatgpt.accessToken+accountRouting",
            "listEgressHostnames":
                agentCouncilCodexProvider.LIST_CODEX_EGRESS_HOSTNAMES,
            "fconnectionClass":
                agentCouncilCodexProvider.CodexRunnerConnection,
            "fsComposeCredentialPath":
                agentCouncilCodexProvider.fsComposeCodexCredentialContainerPath,
            "fdictExtractCredential":
                agentCouncilCodexProvider.fdictExtractCodexRunnerCredential,
            "fsExplainCredential":
                agentCouncilCodexProvider.fsExplainUnusableCodexCredential,
            "fsStageCredential":
                agentCouncilCodexProvider.fsStageCodexRunnerCredentialFile,
            "fdictBuildCapability":
                agentCouncilCodexProvider.fdictBuildCodexCapabilityContract,
        },
        "gemini": {
            "sCredentialSchema": "antigravityOauth.accessToken",
            "listEgressHostnames":
                agentCouncilAntigravityProvider.
                LIST_ANTIGRAVITY_EGRESS_HOSTNAMES,
            "fconnectionClass":
                agentCouncilAntigravityProvider.AntigravityRunnerConnection,
            "fsComposeCredentialPath": agentCouncilAntigravityProvider.
                fsComposeAntigravityCredentialContainerPath,
            "fdictExtractCredential": agentCouncilAntigravityProvider.
                fdictExtractAntigravityRunnerCredential,
            "fsExplainCredential": agentCouncilAntigravityProvider.
                fsExplainUnusableAntigravityCredential,
            "fsStageCredential": agentCouncilAntigravityProvider.
                fsStageAntigravityRunnerCredentialFile,
            "fdictBuildCapability": agentCouncilAntigravityProvider.
                fdictBuildAntigravityCapabilityContract,
        },
    }
    if sProvider not in dictAdapters:
        raise ValueError(
            f"provider {sProvider!r} has no reviewed council adapter")
    return dictAdapters[sProvider]


def fsGetProviderCredentialSchema(sProvider):
    """Return the evidence schema pinned for one provider."""
    return _fdictRequireProvider(sProvider)["sCredentialSchema"]


def fsComposeProviderEgressScope(sCampaignId, sProvider):
    """Return the isolated egress-resource scope for one provider."""
    _fdictRequireProvider(sProvider)
    if sProvider == "claude":
        return sCampaignId
    return f"{sCampaignId}-{sProvider}"


def flistCollectProviderEgressHostnames(setProviders):
    """Return the exact union of selected providers' allowed hosts."""
    return sorted({
        sHostname
        for sProvider in setProviders
        for sHostname in _fdictRequireProvider(
            sProvider)["listEgressHostnames"]
    })


def fconnectionBuildProviderConnection(sProvider, *args, **dictArguments):
    """Instantiate the reviewed connection class for one participant."""
    return _fdictRequireProvider(sProvider)["fconnectionClass"](
        *args, **dictArguments)


def fsComposeProviderCredentialPath(sProvider, sWorkspaceRoot):
    """Compose one provider's persisted login path."""
    return _fdictRequireProvider(sProvider)["fsComposeCredentialPath"](
        sWorkspaceRoot)


def fdictExtractProviderCredential(sProvider, connectionDocker,
                                   sContainerId, sCredentialPath):
    """Extract the reviewed refresh-free credential subset."""
    return _fdictRequireProvider(sProvider)["fdictExtractCredential"](
        connectionDocker, sContainerId, sCredentialPath)


def fsExplainUnusableProviderCredential(sProvider, connectionDocker,
                                        sContainerId, sCredentialPath):
    """Return why one provider credential is unusable, or empty."""
    return _fdictRequireProvider(sProvider)["fsExplainCredential"](
        connectionDocker, sContainerId, sCredentialPath)


def fsStageProviderCredential(sProvider, dictCredential):
    """Materialize one provider's minimal credential document."""
    return _fdictRequireProvider(sProvider)["fsStageCredential"](
        dictCredential)


def fdictBuildProviderCapability(sProvider, dictEvidenceRecord,
                                 bEnabled):
    """Build one provider's UI capability contract."""
    return _fdictRequireProvider(sProvider)["fdictBuildCapability"](
        dictEvidenceRecord, bEnabled)


def fdictComposeProviderRunnerEnvironment(sProvider, dictEgress):
    """Compose proxy and provider-home variables for a runner."""
    dictEnvironment = agentCouncilEgress.fdictBuildRunnerProxyEnvironment(
        dictEgress["sProxyInternalAddress"], dictEgress["iProxyPort"])
    if sProvider == "claude":
        dictEnvironment[
            agentCouncilProviders.S_CLAUDE_CONFIG_DIRECTORY_ENV] = (
                agentCouncilProviders.S_RUNNER_CLAUDE_CONFIG_DIRECTORY)
    elif sProvider == "codex":
        dictEnvironment[agentCouncilCodexProvider.S_CODEX_HOME_ENV] = (
            agentCouncilCodexProvider.S_RUNNER_CODEX_HOME)
        dictEnvironment[
            agentCouncilCodexProvider.S_CODEX_CONFIG_DIRECTORY_ENV] = (
                agentCouncilCodexProvider.S_RUNNER_CODEX_CONFIG_DIRECTORY)
    else:
        dictEnvironment[
            agentCouncilAntigravityProvider.S_ANTIGRAVITY_HOME_ENV] = (
                agentCouncilAntigravityProvider.
                S_RUNNER_ANTIGRAVITY_HOME)
    return dictEnvironment


def fbaBuildProviderConfigTarball(sProvider, sCredentialPath,
                                  sInstructionChannel):
    """Build the provider-specific minimal runner configuration tree."""
    if sProvider == "claude":
        return agentCouncilProviders.fbaBuildCredentialTarball(
            sCredentialPath)
    if sProvider == "codex":
        return agentCouncilCodexProvider.fbaBuildCodexConfigTarball(
            sCredentialPath)
    return (
        agentCouncilAntigravityProvider.
        fbaBuildAntigravityConfigTarball(
            sCredentialPath, sInstructionChannel))


def flistComposeProviderChatArgv(sProvider, sModelId,
                                 sInstructionChannel):
    """Compose one provider's unstructured chairbot command."""
    if sProvider == "claude":
        return agentCouncilProviders.flistComposeClaudeArgv(
            sModelId, sInstructionChannel)
    if sProvider == "codex":
        return agentCouncilCodexProvider.flistComposeCodexArgv(
            sModelId, sInstructionChannel, bStructured=False)
    return agentCouncilAntigravityProvider.flistComposeAntigravityArgv(
        sModelId, bStructured=False)


def fbaComposeProviderChatStdin(sProvider, listQuotedMaterial):
    """Encode quoted chat material for one provider's stdin protocol."""
    if sProvider == "gemini":
        return agentCouncilAntigravityProvider.fbaComposeAntigravityStdin(
            listQuotedMaterial)
    return agentCouncilProviders.fsComposeUntrustedPromptText(
        listQuotedMaterial).encode("utf-8")


def ftParseProviderChatResult(sProvider, sOutput, iExitCode,
                              sRequestedModel, dictExecution):
    """Return normalized events, model identity, answer, and failure."""
    if sProvider == "claude":
        listEvents = agentCouncilProviders.flistParseStreamJsonEvents(sOutput)
        dictIdentity = agentCouncilProviders.fdictExtractModelIdentity(
            listEvents, sRequestedModel)
    elif sProvider == "codex":
        listEvents = agentCouncilCodexProvider.flistNormalizeCodexEvents(
            sOutput, iExitCode)
        dictIdentity = {
            "sRequestedModel": sRequestedModel,
            "sResolvedModel": sRequestedModel if iExitCode == 0 else "",
            "sResolutionEvidence": "pinnedModelFlag",
            "dictUsage": listEvents[-1].get("usage") or {},
            "dictModelUsage": {},
        }
    else:
        listNativeEvents = (
            agentCouncilProviders.flistParseStreamJsonEvents(sOutput))
        listEvents = (
            agentCouncilAntigravityProvider.
            flistNormalizeAntigravityEvents(listNativeEvents))
        dictInit = next((
            dictEvent.get("init") or {} for dictEvent in listNativeEvents
            if dictEvent.get("event") == "init"), {})
        dictIdentity = {
            "sRequestedModel": sRequestedModel,
            "sResolvedModel": str(dictInit.get("model") or ""),
            "sResolutionEvidence": "antigravityInitEvent",
            "dictUsage": listEvents[-1].get("usage") or {},
            "dictModelUsage": {},
        }
    sAnswer = agentCouncilProviders.fsExtractResultText(listEvents)
    sFailure = "" if sAnswer else agentCouncilProviders.fsExplainEmptyResult(
        listEvents, dictExecution)
    return listEvents, dictIdentity, sAnswer, sFailure
