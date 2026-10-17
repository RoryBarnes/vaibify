"""Whether a project has been REPRODUCED, for its author or for a reader.

The dashboard's top bar carries a "Reproduced" badge beside the project
name only when this module says so; the browser renders the answer and
never re-derives it. Whose project it is is asked of git NOW, from the
snapshot's ownership facts, and the two answers have different
evidence.

THE AUTHOR (the manifest is this identity's own) sees the badge exactly
when their own Level 3 attestation passed and still covers the live
manifest -- ``l3Attestation.fbL3AttestationCurrent``, the predicate the
Level 3 cell reads, never a second derivation. A later failed
verification replaces the attestation and so clears the badge; a
manifest that moved makes the attestation stale and clears it too. A
later attempt that reached no verdict writes no attestation, so it
keeps the badge and is named beside it. The hover carries the
attestation's own date, platform and emulation, and nothing it does not
record.

A READER reaches no new level (the level gates are untouched): Level 2
records the author's publication, so a reader's copy tops out at Level
1, and "Reproduced" is the reader's own achievement, shown beside it.
The author's attestation, which a clone carries, is never the reader's
evidence.

WHAT MUST HOLD, all of it, for a reader's label to show:

- the newest DECISIVE record (``reproduced`` or ``diverged``) among the
  records for the selected workflow says ``reproduced``;
- the manifest is another identity's, asked of git NOW: the snapshot
  program puts the fixed ownership questions to git inside the
  container and this module answers the one predicate from the
  replies, so an author who opens their own project never sees a
  reader's label;
- the live ``MANIFEST.sha256`` and the live workflow file hash to the
  digests the record bound, read from the snapshot taken before any
  step ran;
- every manifest entry -- inputs, scripts, standards, tests, envelope
  files and outputs alike -- hashes today to the bytes the record
  observed. HEAD is deliberately not part of the test: the record's own
  commit moves it.

A later ``diverged`` record clears the label. A later attempt that
reached no verdict does not, and the hover text says both. Evidence
that cannot be read is ``unreadable``, which is neither a reproduction
nor the absence of one.
"""

import json
import posixpath
from datetime import datetime

from vaibify.reproducibility import (
    gitEvidence,
    l3Attestation,
    reproductionReport,
)
from vaibify.reproducibility.manifestWriter import flistParseManifestText
from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

__all__ = [
    "S_STATE_ABSENT",
    "S_STATE_DIVERGED",
    "S_STATE_REPRODUCED",
    "S_STATE_STALE",
    "S_STATE_UNREADABLE",
    "fdictBuildReproductionLabel",
    "fsRelativeWorkflowPath",
]

S_STATE_ABSENT = "absent"
S_STATE_REPRODUCED = "reproduced"
S_STATE_DIVERGED = "diverged"
S_STATE_STALE = "evidence-changed"
S_STATE_UNREADABLE = "unreadable"

_S_MANIFEST_PATH = "MANIFEST.sha256"


def fsRelativeWorkflowPath(sWorkflowPath, sRepoRoot):
    """Return the workflow file's path relative to the repository, or ``""``."""
    if not sWorkflowPath or not sRepoRoot:
        return ""
    sRelative = posixpath.relpath(sWorkflowPath, sRepoRoot)
    return "" if sRelative.startswith("..") else sRelative


def _fdictLabel(sState, sReason="", **dictFields):
    dictLabel = {
        "bShow": False, "sState": sState, "sReason": sReason,
        "bEmulated": False, "sPlatform": "", "sRecordedIso": "",
        "sLatestAttemptIso": "", "sLatestAttemptVerdict": "",
        "sVerdictRendered": "",
    }
    dictLabel.update(dictFields)
    return dictLabel


def _flistParseRecords(dictRecordTexts):
    """Return the records newest first, or ``None`` when any is unreadable.

    A record that cannot be parsed might have been the newest decisive
    one, so the honest answer is that the evidence cannot be read --
    never that the remaining ones are all there is.
    """
    listRecords = []
    for sFilename in sorted(dictRecordTexts, reverse=True):
        try:
            dictRecord = json.loads(dictRecordTexts[sFilename])
        except ValueError:
            return None
        if not isinstance(dictRecord, dict):
            return None
        listRecords.append(dictRecord)
    return listRecords


def fdictBuildReproductionLabel(
    filesPoll, sWorkflowRelativePath, dictLastNoVerdict=None,
):
    """Return the label payload for one poll of one workflow."""
    if not isinstance(filesPoll, SnapshotRepoFiles):
        return _fdictLabel(S_STATE_ABSENT)
    if gitEvidence.fsManifestOwnershipFromSnapshotFacts(
        filesPoll.dictOwnershipFacts,
    ) == gitEvidence.S_MANIFEST_OWNERSHIP_OWN:
        return _fdictLabelForAuthor(filesPoll, dictLastNoVerdict)
    dictRecordTexts = filesPoll.dictReproductionRecords
    if dictRecordTexts is None:
        # A snapshot that did not carry the records did not READ them:
        # a failed read is not a project with nothing in it.
        return _fdictLabel(
            S_STATE_UNREADABLE, "the reproduction records were not read",
        )
    if getattr(filesPoll, "sReproductionsError", "") or getattr(
        filesPoll, "bManifestHasEscapedPaths", False,
    ):
        return _fdictLabel(
            S_STATE_UNREADABLE,
            "the reproduction records or the manifest could not be read",
        )
    listRecords = _flistParseRecords(dictRecordTexts)
    if listRecords is None:
        return _fdictLabel(
            S_STATE_UNREADABLE, "a reproduction record could not be parsed",
        )
    return _fdictLabelFromRecords(
        filesPoll, listRecords, sWorkflowRelativePath, dictLastNoVerdict,
    )


def _fdictLabelFromRecords(
    filesPoll, listRecords, sWorkflowRelativePath, dictLastNoVerdict,
):
    listForWorkflow = [
        dictRecord for dictRecord in listRecords
        if sWorkflowRelativePath
        and dictRecord.get("sWorkflowRelativePath") == sWorkflowRelativePath
    ]
    listDecisive = [
        dictRecord for dictRecord in listForWorkflow
        if dictRecord.get("sVerdict") in (
            reproductionReport.SET_VERDICTS_THAT_REPRODUCE
            | reproductionReport.SET_VERDICTS_THAT_DO_NOT_REPRODUCE)
    ]
    if not listDecisive:
        return _fdictLabel(S_STATE_ABSENT)
    dictNewest = listDecisive[0]
    if dictNewest.get("sVerdict") in (
            reproductionReport.SET_VERDICTS_THAT_DO_NOT_REPRODUCE):
        return _fdictLabel(
            S_STATE_DIVERGED,
            reproductionReport.fsRenderVerdict(dictNewest)
            if dictNewest.get("sVerdictRendered")
            else "the newest reproduction did not match",
        )
    sOwnership = gitEvidence.fsManifestOwnershipFromSnapshotFacts(
        filesPoll.dictOwnershipFacts)
    if sOwnership == gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED:
        return _fdictLabel(
            S_STATE_UNREADABLE,
            "git could not say whose manifest this is",
        )
    # An OWN manifest was routed to the author's attestation before any
    # record was read, so the manifest here is another identity's.
    sStale = _fsReasonEvidenceChanged(filesPoll, dictNewest)
    if sStale:
        return _fdictLabel(S_STATE_STALE, sStale)
    return _fdictLabelForReproduced(dictNewest, dictLastNoVerdict)


def _fdictLabelForReproduced(dictRecord, dictLastNoVerdict):
    sRecordedIso = str(dictRecord.get("sCreatedAtIso") or "")
    dictPlatform = dictRecord.get("dictPlatform") or {}
    dictLabel = _fdictLabel(
        S_STATE_REPRODUCED, bShow=True, sRecordedIso=sRecordedIso,
        bEmulated=bool(dictPlatform.get("bEmulated")),
        sPlatform=str(dictPlatform.get("sObtainedPlatform") or ""),
        sVerdictRendered=(
            reproductionReport.fsRenderVerdict(dictRecord)
            if dictRecord.get("sVerdictRendered") else ""),
    )
    _fnNameLaterNoVerdict(dictLabel, dictLastNoVerdict, sRecordedIso)
    return dictLabel


def _fdictLabelForAuthor(filesPoll, dictLastNoVerdict):
    """Return the author's label: their own CURRENT Level 3 attestation."""
    if not l3Attestation.fbL3AttestationCurrent(filesPoll):
        return _fdictLabel(*_ftAuthorLabelAbsence(filesPoll))
    dictAttestation = l3Attestation.fdictReadAttestation(filesPoll) or {}
    sRecordedIso = str(dictAttestation.get("sAttestedAtUtc") or "")
    dictProvenance = dictAttestation.get("dictReproductionProvenance")
    if not isinstance(dictProvenance, dict):
        dictProvenance = {}
    dictLabel = _fdictLabel(
        S_STATE_REPRODUCED, bShow=True, sRecordedIso=sRecordedIso,
        bEmulated=bool(dictProvenance.get("bEmulated")),
        sPlatform=str(dictProvenance.get("sPlatform") or ""),
        sVerdictRendered=str(dictAttestation.get("sVerdictRendered") or ""),
    )
    _fnNameLaterNoVerdict(dictLabel, dictLastNoVerdict, sRecordedIso)
    return dictLabel


def _ftAuthorLabelAbsence(filesPoll):
    """Return ``(sState, sReason)`` for an author with no current attestation.

    Only the reason is read here; whether the label shows was already
    answered by the gate's own predicate.
    """
    dictAttestation = l3Attestation.fdictReadAttestation(filesPoll)
    if dictAttestation is None:
        return S_STATE_ABSENT, "no Level 3 attestation is on file"
    if dictAttestation.get("sStatus") != l3Attestation.S_STATUS_PASSED:
        return (
            S_STATE_DIVERGED,
            "the latest Level 3 verification did not reproduce the manifest",
        )
    if l3Attestation.fbAttestationIsHistorical(dictAttestation):
        return (
            S_STATE_STALE,
            "the Level 3 attestation is from the previous check, which "
            "the current Level 3 requirement does not accept",
        )
    return (
        S_STATE_STALE,
        "the Level 3 attestation does not cover the current manifest",
    )


def _fnNameLaterNoVerdict(dictLabel, dictLastNoVerdict, sRecordedIso):
    """Name a no-verdict attempt made AFTER the evidence the label shows.

    Nothing was established, so the label stays; the dashboard puts the
    attempt's date and the server's reason beside it.
    """
    sAttemptIso = str((dictLastNoVerdict or {}).get("sRecordedIso") or "")
    if not dictLastNoVerdict or not _fbIsLaterInstant(
        sAttemptIso, sRecordedIso,
    ):
        return
    dictLabel["sLatestAttemptIso"] = sAttemptIso
    dictLabel["sLatestAttemptVerdict"] = "no-verdict"
    listReasons = dictLastNoVerdict.get("listReasons") or []
    dictLabel["sReason"] = str(listReasons[0]) if listReasons else ""


def _fbIsLaterInstant(sLaterIso, sEarlierIso):
    """True iff the first timestamp is strictly later than the second.

    Compared as INSTANTS: an attestation is stamped ``...:00Z`` and a
    no-verdict record ``...:00.5+00:00``, and as strings ``"Z" > "."``
    orders a later attempt in the same second before the attestation.
    A timestamp that does not parse falls back to the string order.
    """
    try:
        return _fdatetimeParseUtc(sLaterIso) > _fdatetimeParseUtc(sEarlierIso)
    except (TypeError, ValueError):
        return sLaterIso > sEarlierIso


def _fdatetimeParseUtc(sIso):
    """Parse an ISO 8601 timestamp, accepting the ``Z`` suffix on 3.9."""
    sText = str(sIso or "")
    if sText.endswith("Z"):
        sText = sText[:-1] + "+00:00"
    return datetime.fromisoformat(sText)


def _fsReasonEvidenceChanged(filesPoll, dictRecord):
    """Return why the record's evidence no longer holds, or ``""`` if it does."""
    sBoundManifest = _fsBareDigest(dictRecord.get("sManifestDigest"))
    if not sBoundManifest or _fsLiveDigest(
        filesPoll, _S_MANIFEST_PATH,
    ) != sBoundManifest:
        return "MANIFEST.sha256 is not the manifest the reproduction graded"
    sBoundWorkflow = str(dictRecord.get("sWorkflowDigest") or "")
    sWorkflowPath = str(dictRecord.get("sWorkflowRelativePath") or "")
    if not sBoundWorkflow or _fsLiveDigest(
        filesPoll, sWorkflowPath,
    ) != sBoundWorkflow:
        return "the workflow file is not the one the reproduction ran"
    return _fsReasonAnEntryMoved(filesPoll, dictRecord)


def _fsBareDigest(sDigest):
    """Return the hex of a digest, with or without the ``sha256:`` prefix.

    The shadow rerun records the manifest's digest in the form
    ``fsCurrentManifestDigest`` gives (prefixed), while a snapshot
    hashes bare. Found by driving the whole journey: comparing the two
    forms as strings never matched, so a real reproduction never earned
    its label.
    """
    sText = str(sDigest or "")
    return sText[len("sha256:"):] if sText.startswith("sha256:") else sText


def _fsLiveDigest(filesPoll, sRelativePath):
    dictEntry = filesPoll.fdictHashFiles([sRelativePath]).get(
        sRelativePath) or {}
    if dictEntry.get("sSymlinkSegment") or dictEntry.get("bEscapesRoot"):
        return ""
    return str(dictEntry.get("sSha256") or "")


def _fsReasonAnEntryMoved(filesPoll, dictRecord):
    try:
        listEntries = flistParseManifestText(
            filesPoll.fsReadText(_S_MANIFEST_PATH))
    except (OSError, ValueError, KeyError):
        return "the manifest could not be read to compare its entries"
    dictObserved = {
        dictOutcome.get("sPath"): dictOutcome.get("sObserved")
        for dictOutcome in dictRecord.get("listFileOutcomes") or []
    }
    for dictEntry in listEntries:
        sPath = dictEntry["sPath"]
        sObserved = dictObserved.get(sPath)
        if not sObserved or _fsLiveDigest(filesPoll, sPath) != sObserved:
            return f"{sPath} is not what the reproduction observed"
    return ""
