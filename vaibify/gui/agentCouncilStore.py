"""Bounded event ring, eviction-exempt evidence ledger, checkpoint seam.

Phase 1 of the Agent Council (design/agentCouncil.md sections 7.4-7.5).
The event ring is a display convenience: bounded by event count and
total bytes, with eviction visible through retained-sequence bounds.
The evidence ledger is exempt from eviction — boundedness is enforced
at admission, and a refused entry means the claim it would have backed
reverts to asserted (the engine's job; the ledger reports the refusal).
Credential redaction takes precedence over provenance: an entry whose
command or manifest trips credential detection is never persisted.

The checkpoint seam is a callback the engine invokes as each turn and
phase settles; Phase 3 supplies the durable local app-data writer, and
``InMemoryCampaignCheckpoint`` is the default until then.
"""

import copy
import json
import os
import re

__all__ = [
    "CouncilEventRing",
    "CouncilEvidenceLedger",
    "InMemoryCampaignCheckpoint",
    "DurableCampaignCheckpoint",
    "fbDetectCredentialText",
    "fjsonRedactCredentialsInRecord",
    "fsResolveDurableStoreRoot",
    "fdictCreateCampaignStore",
    "fdictRegisterStartedCampaign",
    "fsMintNextTurnId",
    "fjsonGetCampaignRecord",
    "flistSummariseCampaigns",
    "fnCheckpointStoredCampaign",
    "fdictAppendCampaignEvent",
    "fdictRecordCampaignEvidence",
    "fdictCollectCampaignEvents",
    "fsAcceptCampaignPlanLocally",
    "fbDeleteStoredCampaign",
    "fdictReloadDurableCampaigns",
    "S_COUNCIL_CAMPAIGN_STORE_STATE_KEY",
    "S_CAMPAIGN_RECORD_BASENAME",
    "S_ACCEPTED_PLAN_BASENAME",
    "S_CREDENTIAL_REDACTION_MARKER",
    "S_REFUSAL_CREDENTIAL_REDACTION",
    "S_REFUSAL_ENTRY_EXCEEDS_BOUND",
    "S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST",
    "S_REFUSAL_LEDGER_EXHAUSTED",
    "S_REFUSAL_MISSING_REQUIRED_FIELD",
    "S_REFUSAL_UNKNOWN_STATE_FORM",
    "S_STATE_FORM_BASELINE",
    "S_STATE_FORM_MODIFIED",
    "LIST_CHANGE_MANIFEST_KEYS",
]

S_STATE_FORM_BASELINE = "baseline"
S_STATE_FORM_MODIFIED = "modifiedState"

S_REFUSAL_CREDENTIAL_REDACTION = "credentialRedaction"
S_REFUSAL_ENTRY_EXCEEDS_BOUND = "entryExceedsBound"
S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST = "incompleteChangeManifest"
S_REFUSAL_LEDGER_EXHAUSTED = "ledgerByteBudgetExhausted"
S_REFUSAL_MISSING_REQUIRED_FIELD = "missingRequiredField"
S_REFUSAL_UNKNOWN_STATE_FORM = "unknownStateForm"

LIST_LEDGER_REQUIRED_FIELDS = [
    "sClaimIdentifier",
    "sCommandText",
    "sStateForm",
    "sSnapshotHash",
    "sExecutionImageIdentity",
    "iExitCode",
    "sOutputDigest",
]

# A modified-state manifest must be sufficient to reconstruct the
# tested state from the baseline snapshot (section 7.4): bounded file
# contents (not digests), deletions, mode changes and symlink targets.
LIST_CHANGE_MANIFEST_KEYS = [
    "dictModifiedFileContents",
    "listDeletedPaths",
    "dictChangedFileModes",
    "dictSymlinkTargets",
]

# Conservative default shapes for the injectable credential detector.
# Phase 3 substitutes the repository's capture-time sanitizer; the
# default errs toward refusal, which only costs a claim its confirmed
# label — never a leaked secret.
LIST_CREDENTIAL_PATTERNS = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bghp_[A-Za-z0-9]{20,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    r"\bsk-[A-Za-z0-9_-]{20,}\b",
    r"\bxox[a-z]-[A-Za-z0-9-]{10,}\b",
    r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}",
    r"(?i)\bAuthorization:\s*\S+",
]


def fbDetectCredentialText(sText):
    """Report whether the text matches a known credential shape."""
    for sPattern in LIST_CREDENTIAL_PATTERNS:
        if re.search(sPattern, sText):
            return True
    return False


def _fiMeasureSerializedBytes(dictPayload):
    """Measure a record's retained size as its serialized byte length."""
    return len(json.dumps(dictPayload, default=str, sort_keys=True)
               .encode("utf-8"))


class CouncilEventRing:
    """Sequence-numbered display event ring bounded by count and bytes.

    Eviction is visible, never silent: the lowest retained sequence
    rises past 1 and the evicted count grows, so a consumer can state
    that earlier console output is no longer retained.
    """

    def __init__(self, iMaximumEventCount, iMaximumTotalBytes):
        if iMaximumEventCount < 1 or iMaximumTotalBytes < 1:
            raise ValueError("event ring bounds must be positive")
        self.iMaximumEventCount = iMaximumEventCount
        self.iMaximumTotalBytes = iMaximumTotalBytes
        self.listRetainedEvents = []
        self.iNextSequence = 1
        self.iEvictedEventCount = 0
        self.iRetainedTotalBytes = 0

    def fdictAppendEvent(self, dictEvent):
        """Stamp, retain and return the event; evict oldest while over
        either bound. The newest event is always retained."""
        dictStamped = dict(dictEvent)
        dictStamped["iSequence"] = self.iNextSequence
        self.iNextSequence += 1
        iEventBytes = _fiMeasureSerializedBytes(dictStamped)
        self.listRetainedEvents.append((iEventBytes, dictStamped))
        self.iRetainedTotalBytes += iEventBytes
        self._fnEvictWhileOverBounds()
        return dict(dictStamped)

    def _fnEvictWhileOverBounds(self):
        while len(self.listRetainedEvents) > 1 and (
                len(self.listRetainedEvents) > self.iMaximumEventCount
                or self.iRetainedTotalBytes > self.iMaximumTotalBytes):
            iOldestBytes, _ = self.listRetainedEvents.pop(0)
            self.iRetainedTotalBytes -= iOldestBytes
            self.iEvictedEventCount += 1

    def flistCollectEventsAfter(self, iAfterSequence):
        """Return copies of retained events with sequence > iAfterSequence."""
        return [dict(dictEvent)
                for _, dictEvent in self.listRetainedEvents
                if dictEvent["iSequence"] > iAfterSequence]

    @property
    def iLowestRetainedSequence(self):
        if not self.listRetainedEvents:
            return 0
        return self.listRetainedEvents[0][1]["iSequence"]

    @property
    def iHighestRetainedSequence(self):
        if not self.listRetainedEvents:
            return 0
        return self.listRetainedEvents[-1][1]["iSequence"]

    @property
    def bEvictionHasOccurred(self):
        return self.iEvictedEventCount > 0


class CouncilEvidenceLedger:
    """Eviction-exempt ledger of the executed basis behind confirmed claims.

    Entries, once recorded, are never evicted. Boundedness is enforced
    at admission only: an entry that cannot be retained — oversize,
    ledger budget exhausted, incomplete change manifest, or credential
    detection tripped — is refused, and the caller must revert the
    claim it would have supported to asserted. Redaction wins over
    provenance: a refused entry is never partially persisted.
    """

    def __init__(self, iMaximumEntryBytes, iMaximumTotalBytes,
                 fbDetectCredential=None):
        if iMaximumEntryBytes < 1 or iMaximumTotalBytes < 1:
            raise ValueError("ledger bounds must be positive")
        self.iMaximumEntryBytes = iMaximumEntryBytes
        self.iMaximumTotalBytes = iMaximumTotalBytes
        self.fbDetectCredential = fbDetectCredential or fbDetectCredentialText
        self.listRecordedEntries = []
        self.iRecordedTotalBytes = 0
        self.iRefusedEntryCount = 0

    def fdictRecordEvidence(self, dictEntry):
        """Admit or refuse one ledger entry.

        Returns {"bRecorded", "sRefusalReason", "sEntryIdentifier"}.
        A refusal persists nothing at all.
        """
        sRefusalReason = self._fsValidateEntry(dictEntry)
        if sRefusalReason is None:
            sRefusalReason = self._fsCheckBounds(dictEntry)
        if sRefusalReason is not None:
            self.iRefusedEntryCount += 1
            return {"bRecorded": False, "sRefusalReason": sRefusalReason,
                    "sEntryIdentifier": ""}
        dictRetained = copy.deepcopy(dictEntry)
        sEntryIdentifier = f"evidence-{len(self.listRecordedEntries) + 1}"
        dictRetained["sEntryIdentifier"] = sEntryIdentifier
        self.listRecordedEntries.append(dictRetained)
        self.iRecordedTotalBytes += _fiMeasureSerializedBytes(dictRetained)
        return {"bRecorded": True, "sRefusalReason": "",
                "sEntryIdentifier": sEntryIdentifier}

    def _fsValidateEntry(self, dictEntry):
        for sFieldName in LIST_LEDGER_REQUIRED_FIELDS:
            if dictEntry.get(sFieldName) is None or (
                    dictEntry.get(sFieldName) == ""):
                return S_REFUSAL_MISSING_REQUIRED_FIELD
        sStateForm = dictEntry["sStateForm"]
        if sStateForm not in (S_STATE_FORM_BASELINE, S_STATE_FORM_MODIFIED):
            return S_REFUSAL_UNKNOWN_STATE_FORM
        if sStateForm == S_STATE_FORM_MODIFIED:
            sManifestRefusal = self._fsValidateChangeManifest(dictEntry)
            if sManifestRefusal is not None:
                return sManifestRefusal
        if self._fbEntryTripsCredentialDetection(dictEntry):
            return S_REFUSAL_CREDENTIAL_REDACTION
        return None

    def _fsValidateChangeManifest(self, dictEntry):
        dictManifest = dictEntry.get("dictChangeManifest")
        if not isinstance(dictManifest, dict):
            return S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST
        for sManifestKey in LIST_CHANGE_MANIFEST_KEYS:
            if sManifestKey not in dictManifest:
                return S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST
        if not isinstance(dictManifest["dictModifiedFileContents"], dict):
            return S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST
        for sPathKey, sContent in (
                dictManifest["dictModifiedFileContents"].items()):
            if sContent is None:
                return S_REFUSAL_INCOMPLETE_CHANGE_MANIFEST
        return None

    def _fbEntryTripsCredentialDetection(self, dictEntry):
        listScannedTexts = [dictEntry["sCommandText"]]
        dictManifest = dictEntry.get("dictChangeManifest") or {}
        for sPathKey, sContent in (
                dictManifest.get("dictModifiedFileContents") or {}).items():
            listScannedTexts.append(sPathKey)
            listScannedTexts.append(str(sContent))
        for sTargetValue in (dictManifest.get("dictSymlinkTargets")
                             or {}).values():
            listScannedTexts.append(str(sTargetValue))
        return any(self.fbDetectCredential(sText)
                   for sText in listScannedTexts)

    def _fsCheckBounds(self, dictEntry):
        iEntryBytes = _fiMeasureSerializedBytes(dictEntry)
        if iEntryBytes > self.iMaximumEntryBytes:
            return S_REFUSAL_ENTRY_EXCEEDS_BOUND
        if self.iRecordedTotalBytes + iEntryBytes > self.iMaximumTotalBytes:
            return S_REFUSAL_LEDGER_EXHAUSTED
        return None

    def flistCollectEntries(self):
        """Return deep copies of every recorded entry, admission order."""
        return copy.deepcopy(self.listRecordedEntries)


class InMemoryCampaignCheckpoint:
    """Default checkpoint seam target: keeps the latest settled record.

    The engine calls ``fnCheckpointCampaign`` as each turn and phase
    settles, so a crash loses at most the in-flight turn (section 7.5).
    Phase 3 replaces this with the durable local app-data writer behind
    the same callback signature.
    """

    def __init__(self):
        self.dictLatestCheckpoint = None
        self.iCheckpointCount = 0

    def fnCheckpointCampaign(self, dictCampaign):
        self.dictLatestCheckpoint = copy.deepcopy(dictCampaign)
        self.iCheckpointCount += 1

    def fdictLoadLatestCheckpoint(self):
        """Return a deep copy of the latest checkpoint, or None."""
        if self.dictLatestCheckpoint is None:
            return None
        return copy.deepcopy(self.dictLatestCheckpoint)


# ---------------------------------------------------------------------
# Durable local persistence (design section 7.3, 7.5). The campaign
# record is checkpointed to application-data OUTSIDE the repository as
# each turn and phase settles, so a hub crash loses at most the single
# in-flight turn. This is host app-data (``os.path`` under ``~/.vaibify``),
# NOT a repository write: it never touches the tracked-file set or the
# reproducibility envelope. Only explicit plan acceptance writes a
# researcher-facing artifact, and even that stays local until the
# researcher deliberately exports it (Copy / Download).
# ---------------------------------------------------------------------

# The single ``app.state`` attribute the routes reach the store through.
S_COUNCIL_CAMPAIGN_STORE_STATE_KEY = "dictCouncilCampaignStore"

# The durable app-data subtree, relative to the researcher's home. A
# literal ``/tmp`` or ``/workspace`` root would be wrong here for the
# reason CLAUDE.md records: those name container roots. The council is
# host-side app-data, so it lives beside the registry under ``~/.vaibify``.
S_DURABLE_STORE_SUBPATH = os.path.join(".vaibify", "agentCouncils")
S_CAMPAIGN_RECORD_BASENAME = "campaign.json"
S_ACCEPTED_PLAN_BASENAME = "plan.md"

S_CREDENTIAL_REDACTION_MARKER = "[redacted-credential]"

I_DIRECTORY_MODE_PRIVATE = 0o700
I_FILE_MODE_PRIVATE = 0o600

# Store bounds. The event ring is a display convenience; the durable
# record is the authority. Retention is bounded so a long-lived hub does
# not accumulate campaigns without limit — the oldest are evicted from
# memory and disk together, which is the deletion contract applied on a
# schedule rather than by hand.
I_DEFAULT_EVENT_COUNT_BOUND = 500
I_DEFAULT_EVENT_TOTAL_BYTES = 1 * 1024 * 1024
I_DEFAULT_LEDGER_ENTRY_BYTES = 65536
I_DEFAULT_LEDGER_TOTAL_BYTES = 262144
I_DEFAULT_RETAINED_CAMPAIGN_COUNT = 64


def fsResolveDurableStoreRoot():
    """Return the host app-data root the durable campaign records live in.

    Outside the project repository by construction (design section 7.3):
    a council artifact is reached only over HTTP, never through the
    repository, so the store may sit anywhere host-private and the
    reproducibility envelope never sees it.
    """
    return os.path.join(os.path.expanduser("~"), S_DURABLE_STORE_SUBPATH)


def fjsonRedactCredentialsInRecord(jsonValue):
    """Return a deep copy of the record with credential-shaped text redacted.

    Redaction takes precedence over provenance (design section 7.4-7.5):
    a durable record is sanitized before it is written, so a value that
    trips credential detection is replaced by a fixed marker rather than
    persisted. Walks mappings, sequences and strings; leaves other
    scalars untouched.
    """
    if isinstance(jsonValue, dict):
        return {sKey: fjsonRedactCredentialsInRecord(jsonChild)
                for sKey, jsonChild in jsonValue.items()}
    if isinstance(jsonValue, (list, tuple)):
        return [fjsonRedactCredentialsInRecord(jsonChild)
                for jsonChild in jsonValue]
    if isinstance(jsonValue, str) and fbDetectCredentialText(jsonValue):
        return S_CREDENTIAL_REDACTION_MARKER
    return jsonValue


def _fnEnsurePrivateDirectory(sDirectory):
    """Create a directory and its parents with owner-only permissions."""
    os.makedirs(sDirectory, mode=I_DIRECTORY_MODE_PRIVATE, exist_ok=True)
    os.chmod(sDirectory, I_DIRECTORY_MODE_PRIVATE)


def _fnWritePrivateFileAtomically(sPath, sText):
    """Write text to a 0600 file atomically (write-temp-then-rename)."""
    from .pipelineUtils import fsBuildUniqueTemporaryPath
    sTemporaryPath = fsBuildUniqueTemporaryPath(sPath)
    iDescriptor = os.open(
        sTemporaryPath,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        I_FILE_MODE_PRIVATE,
    )
    with os.fdopen(iDescriptor, "w", encoding="utf-8") as fileTarget:
        fileTarget.write(sText)
    os.replace(sTemporaryPath, sPath)
    os.chmod(sPath, I_FILE_MODE_PRIVATE)


class DurableCampaignCheckpoint:
    """Checkpoint a campaign record to a per-campaign app-data directory.

    Implements the same seam as :class:`InMemoryCampaignCheckpoint` — the
    engine calls ``fnCheckpointCampaign`` as each turn and phase settles —
    but writes the sanitized record to ``<root>/<campaign-id>/campaign.json``
    with owner-only permissions. The directory and file modes are the
    same 0700/0600 the registry uses for host-private state; the write is
    atomic so a crash mid-checkpoint cannot leave a half-written record.
    """

    def __init__(self, sCampaignDirectory):
        self.sCampaignDirectory = sCampaignDirectory

    def fnCheckpointCampaign(self, dictCampaign):
        """Sanitize and write the latest settled campaign record."""
        _fnEnsurePrivateDirectory(self.sCampaignDirectory)
        jsonSanitized = fjsonRedactCredentialsInRecord(dictCampaign)
        _fnWritePrivateFileAtomically(
            os.path.join(
                self.sCampaignDirectory, S_CAMPAIGN_RECORD_BASENAME),
            json.dumps(jsonSanitized, default=str, sort_keys=True, indent=2),
        )

    def fdictLoadLatestCheckpoint(self):
        """Return the last written campaign record, or None when absent."""
        sRecordPath = os.path.join(
            self.sCampaignDirectory, S_CAMPAIGN_RECORD_BASENAME)
        if not os.path.exists(sRecordPath):
            return None
        with open(sRecordPath, encoding="utf-8") as fileRecord:
            return json.load(fileRecord)

    def fsWriteAcceptedPlan(self, sPlanText):
        """Write the accepted plan locally (host app-data only) and return it.

        Local-only by design (design section 7.3): accepting a plan never
        writes the project repository and never touches the tracked-file
        or reproducibility envelope. The plan is sanitized before it
        lands, like every other durable field.
        """
        _fnEnsurePrivateDirectory(self.sCampaignDirectory)
        sSanitized = fjsonRedactCredentialsInRecord(sPlanText)
        sPlanPath = os.path.join(
            self.sCampaignDirectory, S_ACCEPTED_PLAN_BASENAME)
        _fnWritePrivateFileAtomically(sPlanPath, sSanitized)
        return sPlanPath


def fdictCreateCampaignStore(sDurableStoreRoot=None, dictBounds=None):
    """Create the empty app-owned campaign store.

    A plain dict driven by module functions, the same shape the council
    registry uses (design section 9.8 forbids threading class-instance
    identity through the protocol records): ``app.state`` owns one value.
    Each stored campaign carries its durable record, an ephemeral event
    ring, an eviction-exempt evidence ledger and its durable checkpoint.
    """
    dictResolvedBounds = {
        "iEventCountBound": I_DEFAULT_EVENT_COUNT_BOUND,
        "iEventTotalBytes": I_DEFAULT_EVENT_TOTAL_BYTES,
        "iLedgerEntryBytes": I_DEFAULT_LEDGER_ENTRY_BYTES,
        "iLedgerTotalBytes": I_DEFAULT_LEDGER_TOTAL_BYTES,
        "iRetainedCampaignCount": I_DEFAULT_RETAINED_CAMPAIGN_COUNT,
    }
    for sBoundName, iBoundValue in (dictBounds or {}).items():
        if sBoundName not in dictResolvedBounds:
            raise ValueError(f"unknown campaign-store bound '{sBoundName}'")
        dictResolvedBounds[sBoundName] = iBoundValue
    return {
        "sDurableStoreRoot": sDurableStoreRoot or fsResolveDurableStoreRoot(),
        "dictBounds": dictResolvedBounds,
        "dictEntriesById": {},
        "listInsertionOrder": [],
    }


def _fsCampaignDirectory(dictStore, sCampaignId):
    """Return the per-campaign app-data directory path."""
    return os.path.join(dictStore["sDurableStoreRoot"], sCampaignId)


def _fdictBuildEntry(dictStore, dictCampaign):
    """Build a store entry: durable record, ring, ledger, checkpoint."""
    dictBounds = dictStore["dictBounds"]
    return {
        "dictCampaign": copy.deepcopy(dictCampaign),
        "iTurnsLaunched": 0,
        "ringEvents": CouncilEventRing(
            dictBounds["iEventCountBound"], dictBounds["iEventTotalBytes"]),
        "ledgerEvidence": CouncilEvidenceLedger(
            dictBounds["iLedgerEntryBytes"], dictBounds["iLedgerTotalBytes"]),
        "checkpointDurable": DurableCampaignCheckpoint(
            _fsCampaignDirectory(dictStore, dictCampaign["sCampaignId"])),
    }


def _fdictRequireEntry(dictStore, sCampaignId):
    """Return the store entry for a campaign id, or None when unknown."""
    return dictStore["dictEntriesById"].get(sCampaignId)


def fdictRegisterStartedCampaign(dictStore, dictCampaign):
    """Store a freshly created campaign, checkpoint it, and return a summary.

    The durable checkpoint happens at registration so a crash immediately
    after start still leaves a discoverable campaign. Retention is
    enforced afterwards so the newest campaign is never the one evicted.
    """
    sCampaignId = dictCampaign["sCampaignId"]
    if sCampaignId in dictStore["dictEntriesById"]:
        raise ValueError(f"campaign {sCampaignId!r} is already stored")
    dictEntry = _fdictBuildEntry(dictStore, dictCampaign)
    dictStore["dictEntriesById"][sCampaignId] = dictEntry
    dictStore["listInsertionOrder"].append(sCampaignId)
    dictEntry["checkpointDurable"].fnCheckpointCampaign(
        dictEntry["dictCampaign"])
    _fnEvictBeyondRetention(dictStore)
    return _fdictSummariseEntry(dictEntry)


def fsMintNextTurnId(dictStore, sCampaignId):
    """Mint the next per-campaign turn id from a monotonic counter.

    Server-minted (section 10.1): the id is ``turn-<n>`` where ``n`` only
    rises, so the registry's turn-in-flight key is stable within a turn
    and a second launch of the same turn number collides rather than
    doubling.
    """
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        raise ValueError(f"no stored campaign {sCampaignId!r} to launch a turn")
    dictEntry["iTurnsLaunched"] += 1
    return "turn-" + str(dictEntry["iTurnsLaunched"])


def fjsonGetCampaignRecord(dictStore, sCampaignId):
    """Return a deep copy of a stored campaign record, or None."""
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        return None
    return copy.deepcopy(dictEntry["dictCampaign"])


def _fdictSummariseEntry(dictEntry):
    """Return the listing-safe summary of one stored campaign."""
    dictCampaign = dictEntry["dictCampaign"]
    return {
        "sCampaignId": dictCampaign["sCampaignId"],
        "sState": dictCampaign["sState"],
        "sQuestion": dictCampaign["sQuestion"],
        "iParticipantCount": len(dictCampaign.get("listParticipants", [])),
        "iHighestRetainedSequence": (
            dictEntry["ringEvents"].iHighestRetainedSequence),
    }


def flistSummariseCampaigns(dictStore, fbSelectCampaign=None):
    """Return listing summaries for stored campaigns, newest last.

    ``fbSelectCampaign(dictCampaign)`` filters the listing; the routes
    pass the principal-match predicate so one hub's store never lists a
    campaign to a principal it is not bound to (remediation R2).
    """
    listSummaries = []
    for sCampaignId in dictStore["listInsertionOrder"]:
        dictEntry = dictStore["dictEntriesById"].get(sCampaignId)
        if dictEntry is None:
            continue
        if fbSelectCampaign is not None and not fbSelectCampaign(
                dictEntry["dictCampaign"]):
            continue
        listSummaries.append(_fdictSummariseEntry(dictEntry))
    return listSummaries


def fnCheckpointStoredCampaign(dictStore, sCampaignId, dictCampaign):
    """Replace the stored record and re-checkpoint it durably."""
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        raise ValueError(f"no stored campaign {sCampaignId!r} to checkpoint")
    dictEntry["dictCampaign"] = copy.deepcopy(dictCampaign)
    dictEntry["checkpointDurable"].fnCheckpointCampaign(
        dictEntry["dictCampaign"])


def fdictAppendCampaignEvent(dictStore, sCampaignId, dictEvent):
    """Append one display event to a campaign's ephemeral ring."""
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        raise ValueError(f"no stored campaign {sCampaignId!r} for an event")
    return dictEntry["ringEvents"].fdictAppendEvent(dictEvent)


def fdictRecordCampaignEvidence(dictStore, sCampaignId, dictEvidenceEntry):
    """Record one evidence entry against a campaign's ledger.

    The engine's evidence callback, bound per campaign by the
    controller: admission or refusal is the ledger's decision, and a
    refusal reverts the claim it would have backed (the engine's job).
    """
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        raise ValueError(f"no stored campaign {sCampaignId!r} for evidence")
    return dictEntry["ledgerEvidence"].fdictRecordEvidence(dictEvidenceEntry)


def fdictCollectCampaignEvents(dictStore, sCampaignId, iAfterSequence):
    """Return retained events after a sequence, with the eviction bounds.

    The sequence-numbered HTTP poll (design section 11): all retained
    events with ``iSequence > iAfterSequence`` plus the lowest and
    highest retained sequence numbers, so a consumer sees eviction
    explicitly rather than losing events silently.
    """
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        return None
    return {
        "listEvents": (
            dictEntry["ringEvents"].flistCollectEventsAfter(iAfterSequence)),
        "iLowestRetainedSequence": (
            dictEntry["ringEvents"].iLowestRetainedSequence),
        "iHighestRetainedSequence": (
            dictEntry["ringEvents"].iHighestRetainedSequence),
        "bEvictionHasOccurred": dictEntry["ringEvents"].bEvictionHasOccurred,
    }


def fsAcceptCampaignPlanLocally(dictStore, sCampaignId, sPlanText):
    """Write an accepted plan to app-data only, and return its path.

    Never a repository write (design section 7.3). The stored record is
    left to the caller to transition and re-checkpoint; this only lands
    the ``plan.md`` artifact locally.
    """
    dictEntry = _fdictRequireEntry(dictStore, sCampaignId)
    if dictEntry is None:
        raise ValueError(f"no stored campaign {sCampaignId!r} to accept")
    return dictEntry["checkpointDurable"].fsWriteAcceptedPlan(sPlanText)


def fbDeleteStoredCampaign(dictStore, sCampaignId):
    """Delete a campaign from memory and its durable directory together.

    Campaign and snapshot are removed as one act (design section 7.3):
    the whole per-campaign app-data directory is discarded, so no
    orphaned record or snapshot metadata survives the deletion.
    """
    dictEntry = dictStore["dictEntriesById"].pop(sCampaignId, None)
    if sCampaignId in dictStore["listInsertionOrder"]:
        dictStore["listInsertionOrder"].remove(sCampaignId)
    _fnRemoveCampaignDirectory(dictStore, sCampaignId)
    return dictEntry is not None


def _fnRemoveCampaignDirectory(dictStore, sCampaignId):
    """Remove a campaign's durable directory tree if it exists."""
    import shutil
    sDirectory = _fsCampaignDirectory(dictStore, sCampaignId)
    if os.path.isdir(sDirectory):
        shutil.rmtree(sDirectory, ignore_errors=True)


def _fnEvictBeyondRetention(dictStore):
    """Evict oldest campaigns beyond the retained-count bound (FIFO)."""
    iRetained = dictStore["dictBounds"]["iRetainedCampaignCount"]
    while len(dictStore["listInsertionOrder"]) > iRetained:
        sOldest = dictStore["listInsertionOrder"][0]
        fbDeleteStoredCampaign(dictStore, sOldest)


def fdictReloadDurableCampaigns(dictStore):
    """Rebuild stored entries from the durable app-data on hub restart.

    Accepted campaigns (and any whose last settled turn was checkpointed)
    are discoverable after a crash (design section 7.5): each
    ``<campaign-id>/campaign.json`` is loaded back into a store entry so
    the researcher sees the campaign again, recovered from disk rather
    than silently reappearing. The ephemeral event ring is not restored —
    it was never durable — so the UI states console history is gone while
    the structured record survives.
    """
    sRoot = dictStore["sDurableStoreRoot"]
    if not os.path.isdir(sRoot):
        return {"iReloaded": 0}
    iReloaded = 0
    for sCampaignId in sorted(os.listdir(sRoot)):
        if sCampaignId in dictStore["dictEntriesById"]:
            continue
        jsonRecord = _fjsonLoadDurableRecord(dictStore, sCampaignId)
        if jsonRecord is None:
            continue
        dictStore["dictEntriesById"][sCampaignId] = _fdictBuildEntry(
            dictStore, jsonRecord)
        dictStore["listInsertionOrder"].append(sCampaignId)
        iReloaded += 1
    return {"iReloaded": iReloaded}


def _fjsonLoadDurableRecord(dictStore, sCampaignId):
    """Load one durable campaign record, or None when it is unreadable."""
    try:
        return DurableCampaignCheckpoint(
            _fsCampaignDirectory(dictStore, sCampaignId)
        ).fdictLoadLatestCheckpoint()
    except (OSError, ValueError):
        return None
