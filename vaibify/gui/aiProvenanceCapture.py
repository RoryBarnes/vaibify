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
    except Exception as exc:  # noqa: BLE001 — absence is a provenance fact
        logger.info("Workspace prompt not hashable: %s", exc)
        return ""
    return fsHashFileObjectSha256(io.BytesIO(baContent))


def fdictCaptureAiProvenanceStamp(
    dictWorkflow, filesRepo, sContainerId, connectionDocker,
):
    """Assemble the machine-captured stamp with live container facts."""
    from vaibify.docker.containerManager import fbContainerIsNetworkIsolated

    return fdictBuildAiProvenanceStamp(
        dictWorkflow,
        filesRepo,
        sWorkspacePromptSha256=_fsHashWorkspacePrompt(
            connectionDocker, sContainerId,
        ),
        bNetworkIsolatedAtCapture=fbContainerIsNetworkIsolated(sContainerId),
        sHubInvokerModelId=fsResolveApiModelId(),
    )
