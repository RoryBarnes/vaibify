"""Machine-captured AI-provenance stamp for the Replay axis.

The stamp is the evidentiary half of the AI Declaration: where the
declaration markdown is the researcher's attested statement, the stamp
is captured by the backend and never hand-typed — the declared model
list, the SHA-256 of both standing prompt files, the container's live
network-isolation state, and an explicit trust-base statement naming
the components assumed honest rather than recorded.

This module is pure over ``dictWorkflow`` and a ``filesRepo`` adapter,
matching the rest of :mod:`vaibify.reproducibility`. Container-side
facts (the workspace prompt hash, the isolation probe, the hub invoker
model) are computed by the GUI layer and passed in as parameters.

The stamp lives at ``<repo>/.vaibify/ai_provenance.json`` and is
rewritten idempotently by the poll side-effect whenever it drifts from
the current declaration — a hand-edited stamp does not survive the
next poll.
"""

import posixpath
from datetime import datetime, timezone

from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
from vaibify.reproducibility.replayGate import (
    S_AI_PROVENANCE_KEY,
    S_DECLARED_MODELS_KEY,
)


__all__ = [
    "S_STAMP_FILENAME",
    "S_TRUST_BASE_STATEMENT",
    "S_WORKSPACE_PROMPT_PATH",
    "S_PROJECT_CONTEXT_RELATIVE_PATH",
    "fsStampRelativePath",
    "fdictBuildAiProvenanceStamp",
    "fbStampMatchesDeclaration",
    "fnWriteAiProvenanceStamp",
]


S_STAMP_FILENAME = "ai_provenance.json"
S_WORKSPACE_PROMPT_PATH = "/workspace/CLAUDE.md"
S_PROJECT_CONTEXT_RELATIVE_PATH = ".vaibify/AGENTS.md"
_S_VAIBIFY_DIRECTORY = ".vaibify"

S_TRUST_BASE_STATEMENT = (
    "This record is complete assuming the host kernel, the Docker "
    "daemon, and the vaibify hub were unmodified, and no host-root "
    "actor bypassed them."
)


def fsStampRelativePath():
    """Return the repo-relative path of the provenance stamp file."""
    return posixpath.join(_S_VAIBIFY_DIRECTORY, S_STAMP_FILENAME)


def _sHashProjectContext(filesRepo):
    """Return the SHA-256 of the project context file, '' when absent."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictHashes = filesRepo.fdictHashFiles([S_PROJECT_CONTEXT_RELATIVE_PATH])
    dictEntry = dictHashes.get(S_PROJECT_CONTEXT_RELATIVE_PATH) or {}
    return dictEntry.get("sSha256") or ""


def fdictBuildAiProvenanceStamp(
    dictWorkflow,
    filesRepo,
    sWorkspacePromptSha256="",
    bNetworkIsolatedAtCapture=None,
    sHubInvokerModelId="",
):
    """Build the machine-captured stamp for the current declaration.

    ``sWorkspacePromptSha256`` and ``bNetworkIsolatedAtCapture`` are
    container facts supplied by the caller; the CLI, which has no hub
    context, honestly passes ``""`` and ``None``. A missing prompt file
    is recorded as an empty hash, never an error — absence is itself a
    provenance fact.
    """
    dictProvenance = (dictWorkflow or {}).get(S_AI_PROVENANCE_KEY) or {}
    listDeclaredModels = list(dictProvenance.get(S_DECLARED_MODELS_KEY) or [])
    return {
        "listDeclaredModels": listDeclaredModels,
        "sHubInvokerModelId": sHubInvokerModelId,
        "sWorkspacePromptSha256": sWorkspacePromptSha256,
        "sProjectContextSha256": _sHashProjectContext(filesRepo),
        "bNetworkIsolatedAtCapture": bNetworkIsolatedAtCapture,
        "sTrustBaseStatement": S_TRUST_BASE_STATEMENT,
        "sCapturedAtUtc": datetime.now(timezone.utc).isoformat(),
        **_fdictSupervisionEvidence(filesRepo),
    }


def _fdictSupervisionEvidence(filesRepo):
    """Fold the Recorded/Supervised evidence into the stamp.

    The Prompt Record's coverage intervals are the supervised
    windows (both ride the same polling cadence), so the attestation
    claims attribution only over them; the permanent flags travel
    with the record so an archived attestation carries its own
    breach history. Absent files yield empty lists — honestly "no
    evidence", never an error.
    """
    import json as jsonModule
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listIntervals = []
    sIndexPath = ".vaibify/promptRecord/index.json"
    if filesRepo.fbIsFile(sIndexPath):
        try:
            dictIndex = jsonModule.loads(filesRepo.fsReadText(sIndexPath))
            listIntervals = list(
                dictIndex.get("listCoverageIntervals") or [],
            )
        except (OSError, ValueError):
            pass
    listFlags = []
    sFlagsPath = ".vaibify/promptRecord/attribution/flags.jsonl"
    if filesRepo.fbIsFile(sFlagsPath):
        try:
            for sLine in filesRepo.fsReadText(sFlagsPath).splitlines():
                dictFlag = jsonModule.loads(sLine)
                if isinstance(dictFlag, dict):
                    listFlags.append(dictFlag)
        except (OSError, ValueError):
            pass
    return {
        "listSupervisionIntervals": listIntervals,
        "listUnattributedFlags": listFlags,
    }


def fbStampMatchesDeclaration(dictStamp, dictWorkflow):
    """Return True iff the stamp reflects the current declared models.

    The poll side-effect uses this to keep the stamp machine-written:
    any drift — a new declaration, a removal, or a hand edit to the
    stamp file — makes it stale and triggers a rewrite.
    """
    if not isinstance(dictStamp, dict):
        return False
    dictProvenance = (dictWorkflow or {}).get(S_AI_PROVENANCE_KEY) or {}
    listDeclaredModels = list(dictProvenance.get(S_DECLARED_MODELS_KEY) or [])
    return dictStamp.get("listDeclaredModels") == listDeclaredModels


def fnWriteAiProvenanceStamp(filesRepo, dictStamp):
    """Atomically write the stamp to its canonical repo path."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    filesRepo.fnWriteJsonAtomic(fsStampRelativePath(), dictStamp)
