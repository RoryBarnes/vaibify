"""Capture container-side AI-provenance facts for the Replay axis.

The pure stamp builder (:mod:`vaibify.reproducibility.aiProvenanceStamp`)
takes container facts as parameters so the reproducibility layer stays
free of Docker imports. This module is the hub-side glue that gathers
those facts — the workspace prompt hash, the live network-isolation
probe, the hub invoker model — and assembles the full stamp. Shared by
the attestation writer and the poll side-effect so the two can never
capture differently.
"""

__all__ = ["fdictCaptureAiProvenanceStamp"]

import io
import logging

from vaibify.config.modelIdentity import fsResolveApiModelId
from vaibify.reproducibility._hashing import fsHashFileObjectSha256
from vaibify.reproducibility.aiProvenanceStamp import (
    S_WORKSPACE_PROMPT_PATH,
    fdictBuildAiProvenanceStamp,
)

logger = logging.getLogger("vaibify.hub")


def _fsHashWorkspacePrompt(connectionDocker, sContainerId):
    """Return the SHA-256 of the generated workspace prompt, '' if absent."""
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, S_WORKSPACE_PROMPT_PATH,
        )
    except Exception as errorCaught:  # noqa: BLE001 — absence is a provenance fact
        logger.info("Workspace prompt not hashable: %s", errorCaught)
        return ""
    return fsHashFileObjectSha256(io.BytesIO(baContent))


def _fdictCaptureAgentCliVersions(connectionDocker, sContainerId):
    """Return installed CLI versions observed inside the live container."""
    sCommand = (
        'for sAgent in claude codex gemini opencode cline openhands pi; do '
        'if command -v "${sAgent}" >/dev/null 2>&1; then '
        'sVersion=$(timeout 5 "${sAgent}" --version 2>/dev/null | head -n 1); '
        'printf "%s\\t%s\\n" "${sAgent}" "${sVersion}"; fi; done'
    )
    try:
        tExecResult = connectionDocker.ftRunInContainerStreamed(
            sContainerId, sCommand,
        )
    except Exception as errorCaught:  # noqa: BLE001 — absence is a provenance fact
        logger.info("Agent CLI versions not capturable: %s", errorCaught)
        return {}
    if tExecResult.iExitCode != 0:
        return {}
    dictVersions = {}
    for sLine in tExecResult.sStdout.splitlines():
        sAgent, sSeparator, sVersion = sLine.partition("\t")
        if sSeparator and sAgent in {
            "claude", "codex", "gemini", "opencode", "cline",
            "openhands", "pi",
        }:
            dictVersions[sAgent] = sVersion[:200]
    return dictVersions


def fdictCaptureAiProvenanceStamp(
    dictWorkflow, filesRepo, sContainerId, connectionDocker,
):
    """Assemble the machine-captured stamp with live container facts."""
    from vaibify.docker.containerManager import ftProbeNetworkIsolation

    # An unanswerable probe is recorded as None ("unknown"), never as
    # False. This value is evidence inside the L3 attestation, and the
    # boolean helper's fail-open False would assert "not isolated"
    # about a container whose state could not be read.
    bAnswered, bIsolated = ftProbeNetworkIsolation(sContainerId)
    return fdictBuildAiProvenanceStamp(
        dictWorkflow,
        filesRepo,
        sWorkspacePromptSha256=_fsHashWorkspacePrompt(
            connectionDocker, sContainerId,
        ),
        bNetworkIsolatedAtCapture=bIsolated if bAnswered else None,
        sHubInvokerModelId=fsResolveApiModelId(),
        dictAgentCliVersions=_fdictCaptureAgentCliVersions(
            connectionDocker, sContainerId,
        ),
    )
