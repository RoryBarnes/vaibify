"""The project-bookkeeping sidecar: mutable sync state, out of project.json.

The Zenodo archive uploads ``project.json`` and then — as a consequence
of succeeding — used to write the publish record INTO it: the deposit
id, the DOIs, and the per-file last-pushed digests. The local file
therefore always differed from the copy it had just archived, and
re-archiving minted a new deposit id, which changed the file again — a
treadmill by construction. The fix is structural: ``project.json``
holds only the DEFINITION the researcher declares, and every field a
push, archive, or verify writes lives here, in the uncompared
``.vaibify/syncStatus.json`` sidecar, under one section keyed by the
workflow's repo-relative path (the same namespacing lesson
``state.json`` schema v3 learned: a repo may hold several projects,
and a flat section lets one project's save discard another's record).

The in-memory workflow dict keeps the merged shape: the load path
grafts this section back in, so route handlers, the badge layers, and
the frontend continue to see one dict. The save path extracts it
again, so the serialized ``project.json`` can byte-match an immutable
archive forever. Sidecar values REPLACE same-named keys read from a
legacy fielded ``project.json``, deliberately: restoring an old
definition from git must not roll back the record of what was actually
published, and the produced fields' owner is the archive flow, never a
hand edit.

Which fields move is decided by WHO WRITES THEM. Researcher
declarations (``dictRemotes.zenodo.listRecords``, the legacy
``sZenodoDoi`` / ``sOverleafProjectId``, ``dictZenodoMetadata``) stay
in the definition. Fields stamped by a push, archive, or verify — the
whole per-file ``dictSyncStatus``, the four legacy top-level Zenodo
publish-record keys, and the produced ``dictRemotes`` fields — move.
"""

import json

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal

from .repoFiles import ffilesEnsureRepoFiles
from .scheduledReverify import fsSyncStatusRelativePath


__all__ = [
    "DICT_REMOTE_PRODUCED_FIELDS",
    "S_BOOKKEEPING_SECTION_KEY",
    "S_REMOTE_BOOKKEEPING_KEY",
    "T_BOOKKEEPING_TOP_KEYS",
    "fdictExtractSyncBookkeeping",
    "fdictReadSyncBookkeeping",
    "fnMergeSyncBookkeepingIntoWorkflow",
    "fnWriteSyncBookkeeping",
]


# The top-level project.json keys a push or archive writes. The four
# sZenodo* keys are the publish record `_fnPersistZenodoPublishRecord`
# stamps after every successful archive; dictSyncStatus carries the
# per-file last-pushed digests, endpoints, timestamps, and tracking
# flags (the flags ride along on purpose: they live inside the same
# per-file entries, and splitting entries field-by-field would put one
# dict's halves in two files).
T_BOOKKEEPING_TOP_KEYS = (
    "dictSyncStatus",
    "sZenodoDepositionId",
    "sZenodoLatestDoi",
    "sZenodoConceptDoi",
    "sZenodoLatestUrl",
)

# The dictRemotes fields a push, archive, or verify produces, per
# service. Everything else in a dictRemotes entry is a researcher
# declaration and stays in the definition — most importantly
# ``zenodo.listRecords``, the declared additional records. The zenodo
# identity trio is produced: `_fnPersistZenodoPublishRecord` advances
# all three on every publish ("a publish is new ground truth").
DICT_REMOTE_PRODUCED_FIELDS = {
    "github": ("sCommittedSha",),
    "overleaf": ("sLastPushCommit",),
    "zenodo": ("sRecordId", "sDoi", "sService"),
}

S_BOOKKEEPING_SECTION_KEY = "dictProjectBookkeeping"
S_REMOTE_BOOKKEEPING_KEY = "dictRemoteBookkeeping"


def fdictExtractSyncBookkeeping(dictDeclarative):
    """Pop every produced bookkeeping field; return what was taken.

    Mutates ``dictDeclarative`` (the save path's private deep copy,
    never the live merged dict). A ``dictRemotes`` service entry left
    empty by the extraction is dropped, as is an emptied
    ``dictRemotes`` itself, so a definition that declares nothing
    serializes without vestigial empty containers.
    """
    dictBookkeeping = {}
    for sKey in T_BOOKKEEPING_TOP_KEYS:
        if sKey in dictDeclarative:
            dictBookkeeping[sKey] = dictDeclarative.pop(sKey)
    dictProduced = _fdictExtractRemoteProducedFields(dictDeclarative)
    if dictProduced:
        dictBookkeeping[S_REMOTE_BOOKKEEPING_KEY] = dictProduced
    return dictBookkeeping


def _fdictExtractRemoteProducedFields(dictDeclarative):
    """Pop the produced fields out of each ``dictRemotes`` entry."""
    dictRemotes = dictDeclarative.get("dictRemotes")
    if not isinstance(dictRemotes, dict):
        return {}
    dictProduced = {}
    for sService, tProducedFields in DICT_REMOTE_PRODUCED_FIELDS.items():
        dictEntry = dictRemotes.get(sService)
        if not isinstance(dictEntry, dict):
            continue
        dictTaken = {
            sField: dictEntry.pop(sField)
            for sField in tProducedFields if sField in dictEntry
        }
        if dictTaken:
            dictProduced[sService] = dictTaken
        if not dictEntry:
            dictRemotes.pop(sService)
    if not dictRemotes:
        dictDeclarative.pop("dictRemotes", None)
    return dictProduced


def fnMergeSyncBookkeepingIntoWorkflow(dictWorkflow, dictBookkeeping):
    """Graft one workflow's sidecar section into the merged dict.

    Runs on load, BEFORE the schema migrations and the legacy-remotes
    derivation, so both see the full merged shape. Sidecar values win
    over same-named keys a legacy fielded project.json still carries
    (see the module docstring for why); keys absent from the sidecar
    keep whatever the file said, which is the pre-migration fallback.
    """
    if not dictBookkeeping:
        return
    for sKey in T_BOOKKEEPING_TOP_KEYS:
        if sKey in dictBookkeeping:
            dictWorkflow[sKey] = dictBookkeeping[sKey]
    dictProduced = dictBookkeeping.get(S_REMOTE_BOOKKEEPING_KEY) or {}
    for sService, dictFields in dictProduced.items():
        if not isinstance(dictFields, dict) or not dictFields:
            continue
        dictRemotes = dictWorkflow.setdefault("dictRemotes", {})
        dictEntry = dictRemotes.setdefault(sService, {})
        dictEntry.update(dictFields)


def _fdictFetchDocumentOrEmpty(filesRepo):
    """Return the parsed syncStatus.json document, or ``{}``.

    Fetch-and-catch rather than probe-then-fetch, deliberately — the
    same shape as the ``state.json`` load beside it. It runs on every
    workflow load and save, so the missing-file probe would be a
    second container exec per call; a missing or corrupt file reads
    empty either way. Never hand this a ``SnapshotRepoFiles`` — its
    reader raises ``KeyError`` for unsampled paths, which is exactly
    the failure mode ``scheduledReverify.fdictReadSyncStatusDocument``
    keeps its existence probe for.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    try:
        dictDocument = json.loads(
            filesRepo.fsReadText(fsSyncStatusRelativePath()),
        )
    except (OSError, ValueError) as errorRead:
        fnReRaiseControlPlaneRefusal(errorRead)
        return {}
    return dictDocument if isinstance(dictDocument, dict) else {}


def fdictReadSyncBookkeeping(filesRepo, sWorkflowKey):
    """Return one workflow's sidecar bookkeeping section, or ``{}``.

    An empty ``sWorkflowKey`` cannot be attributed to a project and
    reads empty, matching ``state.json``'s fail-conservative rule.
    """
    if not sWorkflowKey:
        return {}
    dictSections = _fdictFetchDocumentOrEmpty(filesRepo).get(
        S_BOOKKEEPING_SECTION_KEY,
    )
    if not isinstance(dictSections, dict):
        return {}
    dictSection = dictSections.get(sWorkflowKey)
    return dictSection if isinstance(dictSection, dict) else {}


def fnWriteSyncBookkeeping(filesRepo, sWorkflowKey, dictBookkeeping):
    """Persist one workflow's bookkeeping section atomically.

    Holds the same per-file write lock as ``fnWriteSyncStatus`` across
    the read-modify-write, so a concurrent service verify cannot be
    lost, and vice versa. A section identical to what the file already
    holds is not rewritten: the save path calls this on EVERY workflow
    save, and rewriting unchanged bytes would spend a container exec
    and churn the mtime the poll snapshot watches. An empty
    ``sWorkflowKey`` is skipped — unattributable, like state.json.
    """
    if not sWorkflowKey:
        return
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = fsSyncStatusRelativePath()
    with filesRepo.flockAcquireForFile(sRelPath):
        dictDocument = _fdictFetchDocumentOrEmpty(filesRepo)
        dictSections = dictDocument.get(S_BOOKKEEPING_SECTION_KEY)
        if not isinstance(dictSections, dict):
            dictSections = {}
        dictExisting = dictSections.get(sWorkflowKey)
        if dictExisting == dictBookkeeping:
            return
        if not dictExisting and not dictBookkeeping:
            return
        if dictBookkeeping:
            dictSections[sWorkflowKey] = dictBookkeeping
        else:
            dictSections.pop(sWorkflowKey, None)
        dictDocument[S_BOOKKEEPING_SECTION_KEY] = dictSections
        filesRepo.fnWriteJsonAtomic(sRelPath, dictDocument)
