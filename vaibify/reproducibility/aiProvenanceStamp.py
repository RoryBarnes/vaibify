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


_LIST_STAMP_HASH_FIELDS = [
    "sWorkspacePromptSha256",
    "sProjectContextSha256",
]


def _fbHashFieldWellFormed(dictStamp, sField):
    """Return True iff the field is '' or a 64-character hex digest."""
    sValue = dictStamp.get(sField)
    if sValue == "":
        return True
    if not isinstance(sValue, str) or len(sValue) != 64:
        return False
    return all(sCharacter in "0123456789abcdef" for sCharacter in sValue)


def _fbStampShapeIntact(dictStamp):
    """Return True iff every machine-written field still has its shape.

    The shape check is what covers the fields no exec-free caller can
    recompute: the workspace prompt hash (a container fact) and the
    isolation probe. A hand edit that keeps the shape survives, but it
    can no longer be an arbitrary string, and the fields that CAN be
    recomputed are checked by value in
    :func:`fbStampMatchesDeclaration`.
    """
    if dictStamp.get("sTrustBaseStatement") != S_TRUST_BASE_STATEMENT:
        return False
    bIsolated = dictStamp.get("bNetworkIsolatedAtCapture")
    if bIsolated is not None and not isinstance(bIsolated, bool):
        return False
    if not all(
        _fbHashFieldWellFormed(dictStamp, sField)
        for sField in _LIST_STAMP_HASH_FIELDS
    ):
        return False
    return _fbCapturedAtPlausible(dictStamp.get("sCapturedAtUtc"))


def _fbCapturedAtPlausible(sCapturedAtUtc):
    """Return True iff the capture time parses and is not in the future."""
    try:
        dtCaptured = datetime.fromisoformat(str(sCapturedAtUtc or ""))
    except (TypeError, ValueError):
        return False
    if dtCaptured.tzinfo is None:
        dtCaptured = dtCaptured.replace(tzinfo=timezone.utc)
    return dtCaptured <= datetime.now(timezone.utc)


def fbStampMatchesDeclaration(dictStamp, dictWorkflow, filesRepo=None):
    """Return True iff the stamp still reflects captured reality.

    The poll side-effect uses this to keep the stamp machine-written:
    any drift — a new declaration, a removal, or a hand edit to the
    stamp file — makes it stale and triggers a rewrite. Comparing only
    the declared model list left five of the six captured fields
    hand-editable forever, and every one of them is folded into the L3
    attestation, so an edited stamp became an attested claim.

    Every field is now checked to the strongest degree an exec-free
    caller can: by VALUE where it is recomputable (the declared
    models, the trust-base constant, and — when ``filesRepo`` is given
    — the project-context hash), and by SHAPE where it is a live
    container fact this caller cannot re-probe without an exec.
    """
    if not isinstance(dictStamp, dict):
        return False
    dictProvenance = (dictWorkflow or {}).get(S_AI_PROVENANCE_KEY) or {}
    listDeclaredModels = list(dictProvenance.get(S_DECLARED_MODELS_KEY) or [])
    if dictStamp.get("listDeclaredModels") != listDeclaredModels:
        return False
    if not _fbStampShapeIntact(dictStamp):
        return False
    if filesRepo is None:
        return True
    return dictStamp.get("sProjectContextSha256") == _sHashProjectContext(
        filesRepo,
    )


def fnWriteAiProvenanceStamp(filesRepo, dictStamp):
    """Atomically write the stamp to its canonical repo path."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    filesRepo.fnWriteJsonAtomic(fsStampRelativePath(), dictStamp)
