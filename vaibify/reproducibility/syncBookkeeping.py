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
    "fiResolveZenodoParentDepositId",
    "fsDescribeCrossInstanceParent",
    "fsResolveRecordedZenodoService",
    "S_BOOKKEEPING_SECTION_KEY",
    "S_REMOTE_BOOKKEEPING_KEY",
    "T_BOOKKEEPING_TOP_KEYS",
    "fdictExtractSyncBookkeeping",
    "flistReadPendingPromotions",
    "fnMirrorPendingPromotions",
    "fnRemovePendingPromotion",
    "fnUpdatePendingPromotion",
    "fdictReadSyncBookkeeping",
    "fnMergeSyncBookkeepingIntoWorkflow",
    "fnRecordZenodoPublish",
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
    # In-flight promotions, which are produced state by definition:
    # a key the extract/merge contract does not know is silently
    # erased by the next ordinary workflow save, and this is the one
    # record that exists to survive a crash.
    "listPendingPromotions",
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
    # dictSuperseded joined on 2026-09-14: the identifiers a promotion
    # retires. Produced, like the rest of this entry -- writing it into
    # project.json would stale the production deposit the moment it
    # was minted, and the GitHub commit before it.
    "zenodo": ("sRecordId", "sDoi", "sService", "dictSuperseded"),
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


# Three questions about "which Zenodo", and they are not one question.
# Confusing them is why a badge went `drifted` for every file after a
# promotion and why the deposit link pointed at the wrong instance:
#
#   Where does the RECORDED primary deposit live?  dictRemotes.zenodo
#                                                  .sService (produced)
#   Where should the NEXT publish go?              top-level
#                                                  sZenodoService
#                                                  (declared)
#   Where does the ENVIRONMENT deposit live?       that record's own
#                                                  sZenodoService
#
# Only the first two live here; the third belongs to environment.json
# and is read from the deposit record beside the bytes it describes.


def fsResolveRecordedZenodoService(dictWorkflow):
    """Return where the RECORDED primary Zenodo deposit lives.

    The produced field wins. The declaration is the fallback for a
    project that published before the field existed, and "sandbox" is
    the fallback for one that has published nothing -- neither is an
    answer about a record, so neither may outrank one.
    """
    dictRemotes = (dictWorkflow or {}).get("dictRemotes") or {}
    dictZenodo = dictRemotes.get("zenodo") or {}
    return str(
        dictZenodo.get("sService")
        or (dictWorkflow or {}).get("sZenodoService")
        or "sandbox"
    )


def fsDescribeCrossInstanceParent(dictWorkflow, sTargetService):
    """Return why a publish would cross instances, or ``""``.

    Zenodo's ``newversion`` flow asks ONE instance for a new version
    of a record it holds. A project whose recorded deposit lives on
    sandbox and whose declared target is production would send that
    sandbox deposit id to zenodo.org, which knows no such record --
    an unhelpful remote 404 in place of a local refusal that can name
    the remedy.
    """
    sRecorded = fsResolveRecordedZenodoService(dictWorkflow)
    sTarget = str(sTargetService or "").strip()
    if not fiResolveZenodoParentDepositId(dictWorkflow):
        return ""
    if not sTarget or sTarget == sRecorded:
        return ""
    return (
        "This project's Zenodo deposit was published on "
        f"{sRecorded} and the project is now set to publish to "
        f"{sTarget}. Zenodo cannot make a new version of a record "
        "held on the other instance -- sandbox and production are "
        "separate systems and nothing transfers between them. Either "
        "set the instance back to " + sRecorded + ", or start a new "
        "concept on " + sTarget + ", which publishes a first version "
        "there and keeps the old identifiers as a superseded note."
    )


def fiResolveZenodoParentDepositId(dictWorkflow):
    """Return the previous deposit id as an int, or 0 if none.

    Triggers the Zenodo ``newversion`` flow in the dispatcher when
    positive. Non-numeric or absent values fall back to 0 (first
    publish) rather than raising, so a workflow with corrupted state
    can still publish -- the next push chains off the resulting new
    deposit.
    """
    try:
        iParent = int((dictWorkflow or {}).get("sZenodoDepositionId") or 0)
    except (TypeError, ValueError):
        return 0
    return iParent if iParent > 0 else 0


# ── The pending-promotion record ──
#
# A promotion mints a permanent DOI, and a lost one cannot be
# recovered by guessing. The record that survives the crash therefore
# has to be written under the SAME per-file lock an ordinary workflow
# save takes, and mutated one entry at a time: serializing the whole
# workflow to settle one promotion would let a concurrent save revert
# the settlement, and a concurrent settlement lose the save.


def flistReadPendingPromotions(filesRepo, sWorkflowKey):
    """Return this workflow's in-flight promotion records."""
    listPending = fdictReadSyncBookkeeping(filesRepo, sWorkflowKey).get(
        "listPendingPromotions",
    )
    return [
        dictRecord for dictRecord in listPending or []
        if isinstance(dictRecord, dict)
    ]


def fnMirrorPendingPromotions(filesRepo, sWorkflowKey, dictWorkflow):
    """Refresh the merged workflow dict's mirror of the sidecar list.

    The sidecar is the one WRITER; the in-memory dict is the copy
    every route handler and the poll payload read. Refreshing it from
    the file -- rather than mutating both -- keeps that one-writer
    property, which is what stops a promotion update and an ordinary
    save from disagreeing about what is in flight.
    """
    if dictWorkflow is None:
        return
    dictWorkflow["listPendingPromotions"] = flistReadPendingPromotions(
        filesRepo, sWorkflowKey,
    )


def fnUpdatePendingPromotion(
    filesRepo, sWorkflowKey, sPromotionId, dictFields,
):
    """Merge fields into one promotion record, creating it if absent.

    Narrow on purpose: the caller names the one record it is changing
    and the fields it is changing, so nothing else in the sidecar is
    read into memory, held, and written back over whatever another
    writer did meanwhile.
    """
    def _fnApply(dictSection):
        listPending = [
            dictRecord for dictRecord in
            dictSection.get("listPendingPromotions") or []
            if isinstance(dictRecord, dict)
        ]
        for dictRecord in listPending:
            if dictRecord.get("sPromotionId") == sPromotionId:
                dictRecord.update(dictFields)
                break
        else:
            dictNew = {"sPromotionId": sPromotionId}
            dictNew.update(dictFields)
            listPending.append(dictNew)
        dictSection["listPendingPromotions"] = listPending

    _fnMutateBookkeepingSection(filesRepo, sWorkflowKey, _fnApply)


def fnRemovePendingPromotion(filesRepo, sWorkflowKey, sPromotionId):
    """Drop one settled promotion record from the sidecar."""
    def _fnApply(dictSection):
        listPending = [
            dictRecord for dictRecord in
            dictSection.get("listPendingPromotions") or []
            if isinstance(dictRecord, dict)
            and dictRecord.get("sPromotionId") != sPromotionId
        ]
        if listPending:
            dictSection["listPendingPromotions"] = listPending
        else:
            dictSection.pop("listPendingPromotions", None)

    _fnMutateBookkeepingSection(filesRepo, sWorkflowKey, _fnApply)


def _fnMutateBookkeepingSection(filesRepo, sWorkflowKey, fnApply):
    """Apply one in-place mutation to a section under the write lock."""
    if not sWorkflowKey:
        return
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = fsSyncStatusRelativePath()
    with filesRepo.flockAcquireForFile(sRelPath):
        dictDocument = _fdictFetchDocumentOrEmpty(filesRepo)
        dictSections = dictDocument.get(S_BOOKKEEPING_SECTION_KEY)
        if not isinstance(dictSections, dict):
            dictSections = {}
        dictSection = dictSections.get(sWorkflowKey)
        if not isinstance(dictSection, dict):
            dictSection = {}
        fnApply(dictSection)
        dictSections[sWorkflowKey] = dictSection
        dictDocument[S_BOOKKEEPING_SECTION_KEY] = dictSections
        filesRepo.fnWriteJsonAtomic(sRelPath, dictDocument)


def fnRecordZenodoPublish(
    dictWorkflow, dictResult, sZenodoService,
):
    """Store deposit id + DOIs + HTML URL on the workflow.

    Also advances ``dictRemotes.zenodo`` — the record every verify
    consults. The legacy-remotes migration deliberately never
    overwrites an existing entry, so after a SECOND publish the
    legacy keys advanced while ``dictRemotes.zenodo.sRecordId`` kept
    the first deposit's id, and the post-archive auto-verify compared
    the fresh files against the old immutable version — "9 of 24
    matching" about a deposit that had just been published complete
    (live, 2026-08-27). A publish is new ground truth, not a
    derivation; declared ``listRecords`` are preserved untouched.

    Every field it writes is BOOKKEEPING -- the four top-level
    ``sZenodo*`` keys and the produced ``dictRemotes.zenodo`` fields --
    so a publish leaves ``project.json`` byte-identical to the copy it
    just archived. It lives here, beside the contract that says so,
    because two lanes now write it: an ordinary archive, and the
    recovery lane adopting a DOI minted while vaibify was not
    watching.
    """
    if dictResult.get("iDepositId"):
        dictWorkflow["sZenodoDepositionId"] = str(
            dictResult["iDepositId"]
        )
        dictRemotes = dictWorkflow.setdefault("dictRemotes", {})
        dictZenodo = dictRemotes.setdefault("zenodo", {})
        dictZenodo["sRecordId"] = str(dictResult["iDepositId"])
        dictZenodo["sService"] = sZenodoService
        if dictResult.get("sDoi"):
            dictZenodo["sDoi"] = dictResult["sDoi"]
    if dictResult.get("sDoi"):
        dictWorkflow["sZenodoLatestDoi"] = dictResult["sDoi"]
    if dictResult.get("sConceptDoi"):
        dictWorkflow["sZenodoConceptDoi"] = dictResult["sConceptDoi"]
    if dictResult.get("sHtmlUrl"):
        dictWorkflow["sZenodoLatestUrl"] = dictResult["sHtmlUrl"]
