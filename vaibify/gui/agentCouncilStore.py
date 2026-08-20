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
import re

__all__ = [
    "CouncilEventRing",
    "CouncilEvidenceLedger",
    "InMemoryCampaignCheckpoint",
    "fbDetectCredentialText",
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
