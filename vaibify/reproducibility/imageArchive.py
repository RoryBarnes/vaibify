"""The environment archive: what was deposited, and does it match.

A registry digest names bytes somebody else is storing. When the
registry forgets them, ``reproduce.sh`` fails and the result stops
being checkable. Depositing a ``docker save`` of the pinned image into
Zenodo puts the environment under an actual preservation commitment, so
a result stays verifiable for decades.

The scope is deliberately bounded: preserve a RUNNABLE environment, not
a REBUILDABLE one. Guix/Nix-style full-source reconstruction was
considered and declined.

TWO RECORDS, IN TWO FILES, AND THEY ARE NOT THE SAME THING
----------------------------------------------------------

- ``project.json`` carries the researcher's ANSWER, under
  ``dictImageArchive`` (:data:`DICT_IMAGE_ARCHIVE_QUESTION`). Level 2
  asks only whether the question was answered; ``declined`` passes.
- ``.vaibify/environment.json`` carries the DEPOSIT RECORD, under
  ``dictContainer.dictImageArchive``. Level 3 asks only whether an
  archive exists that matches the envelope, and never reads the answer.

Keeping them apart is what makes declining reversible rather than a
lock: a decline is simply an absent record, so changing the answer and
depositing opens Level 3 with nothing to undo.

WHY THE RECORD RE-STATES THE DIGEST AND THE ARCHITECTURE
--------------------------------------------------------

It sits beside ``dictContainer.sImageDigest``, and data recorded beside
what it describes with no check binding the two goes silently stale.
The envelope is regenerated whenever a workflow crosses Level 1, which
rebuilds ``dictContainer`` from a fresh capture; a record carried
forward on adjacency alone would then describe an image nobody
deposited. It carries its own copy of both fields so the carry-forward
has something to compare, and the ARCHITECTURE is half of that
comparison rather than a decoration: a **manifest list** digest spans
several platforms and pins none of them, so matching digests do not
imply matching architecture and the platform must be recorded, never
inferred.

WHAT THE TARBALL HASH BINDS
---------------------------

``sTarballSha256`` is computed over the bytes actually uploaded, and it
is the binding. The image store's digest is NOT usable for this: the
containerd store preserves the registry manifest digest through
``docker save`` and the classic store does not (measured), so a check
written against either behaves differently on a laptop than in CI.
"""

__all__ = [
    "DICT_IMAGE_ARCHIVE_QUESTION",
    "S_ANSWER_ARCHIVED",
    "S_ANSWER_DECLINED",
    "S_ANSWER_REFERENCED",
    "S_ARCHIVE_CHECK_SERVICE",
    "S_IMAGE_ARCHIVE_KEY",
    "S_PROVENANCE_ORIGINAL",
    "S_PROVENANCE_VERIFIED_EQUIVALENT",
    "S_STATE_ARCHIVED",
    "S_STATE_ARCHIVING",
    "S_STATE_CLOSED",
    "S_STATE_MISMATCHED",
    "S_STATE_NOT_APPLICABLE",
    "S_STATE_NOT_ARCHIVED",
    "S_STATE_UNCHECKED",
    "S_LOADED_FROM_ARCHIVE_MARKER",
    "S_RECHECK_DIFFERS",
    "S_RECHECK_MATCHED",
    "S_RECHECK_UNAVAILABLE",
    "S_RECHECK_VACUOUS",
    "fbImageArchiveMatchesEnvelope",
    "fbImageWasLoadedFromArchive",
    "fdictJudgeArchiveRecheck",
    "fbWorkflowAnswersImageArchive",
    "S_DEPOSIT_FINGERPRINT_PREFIX",
    "fdictBuildArchiveRecord",
    "fdictParseDepositFingerprint",
    "fdictStampDepositMetadata",
    "flistDescribeReferenceProblems",
    "fdictReadArchiveRecord",
    "flistDescribeArchiveMismatch",
    "fsResolveArchiveState",
]

import json
import re

from vaibify.reproducibility import answeredQuestion


S_IMAGE_ARCHIVE_KEY = "dictImageArchive"

S_ANSWER_ARCHIVED = "archived"
S_ANSWER_REFERENCED = "referenced"
S_ANSWER_DECLINED = "declined"

S_PROVENANCE_ORIGINAL = "original"
S_PROVENANCE_VERIFIED_EQUIVALENT = "verified-equivalent"

# The service name this feature's in-flight checks are recorded under
# in `remoteCheckState`, which already owns "is vaibify asking right
# now" for every other remote.
S_ARCHIVE_CHECK_SERVICE = "image-archive"

# The question, in the shared representation
# (:mod:`vaibify.reproducibility.answeredQuestion`). Blocking Level 2
# on it is deliberate: it forces the decision at the one moment the
# image certainly still exists. A warning beside an optional button
# gets read by nobody. No answer carries a value -- the DOI lives in
# the deposit record, and duplicating it here would be two authorities
# on one string.
DICT_IMAGE_ARCHIVE_QUESTION = {
    "sKey": "imageArchive",
    "sAnswerKey": "sAnswer",
    "tAnswers": (S_ANSWER_ARCHIVED, S_ANSWER_REFERENCED, S_ANSWER_DECLINED),
    "sValueKey": "",
    "tAnswersNeedingValue": (),
    "sLabel": "Environment archive",
    "sPlainQuestion": (
        "Your results were produced inside a container image. Its "
        "digest is recorded, but a digest only names bytes a registry "
        "is storing for you — when the registry forgets them, nobody "
        "can rebuild the environment and the result stops being "
        "checkable. Depositing the image itself puts it under a "
        "permanent archive's preservation commitment. Do you want to "
        "deposit it, point at a deposit that already holds it, or "
        "decline?"
    ),
}

# Row states. Mismatched and Closed share a colour and differ in SHAPE,
# because their remedies are opposite: "fix your deposit" versus
# "nothing can be done". Shape is also the channel that survives
# colour-blindness.
S_STATE_ARCHIVED = "attained"
S_STATE_NOT_ARCHIVED = "none"
S_STATE_ARCHIVING = "running"
S_STATE_UNCHECKED = "unknown"
S_STATE_MISMATCHED = "diverged"
S_STATE_CLOSED = "closed"
S_STATE_NOT_APPLICABLE = "not-applicable"

_RE_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_RE_DOI = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


def fbWorkflowAnswersImageArchive(dictWorkflow):
    """Return True iff the environment-archive question was answered.

    ANSWERING is the criterion. ``declined`` is a complete answer and
    passes; only silence fails. The consequence of declining is real
    and is stated to the researcher rather than enforced here: the
    image is a mutable local resource, so a prune, a rebuild or a new
    laptop can remove the opportunity even though the answer itself
    stays revocable.
    """
    return answeredQuestion.fbQuestionIsAnswered(
        (dictWorkflow or {}).get(S_IMAGE_ARCHIVE_KEY),
        DICT_IMAGE_ARCHIVE_QUESTION,
    )


def fdictBuildArchiveRecord(
    sVersionDoi, sConceptDoi, sTarballSha256, iTarballBytes,
    sDepositedIso, sProvenance, sImageDigest, sArchitecture,
    sTarballName, sImageStreamSha256="", sZenodoService="",
):
    """Assemble the deposit record written into the environment snapshot.

    ``sTarballSha256`` binds the UPLOADED bytes -- what a downloader
    verifies. ``sImageStreamSha256`` binds ``docker save``'s
    uncompressed output, which is what a later re-check compares,
    because the compressed bytes depend on which codec and which build
    of it produced them and an identical image would otherwise report
    as diverged.

    ``sZenodoService`` names WHICH Zenodo holds the deposit (the
    client's own service key, ``sandbox`` or ``zenodo``). A reader
    maps it through the client's host table and refuses any other
    value; nothing in the record is ever fetched as a URL. Records
    written before the field existed carry ``""`` and are classified
    by their DOI prefix.
    """
    return {
        "sVersionDoi": sVersionDoi,
        "sConceptDoi": sConceptDoi,
        "sZenodoService": sZenodoService,
        "sTarballName": sTarballName,
        "sTarballSha256": sTarballSha256,
        "sImageStreamSha256": sImageStreamSha256,
        "iTarballBytes": int(iTarballBytes),
        "sDepositedIso": sDepositedIso,
        "sProvenance": sProvenance,
        "sImageDigest": sImageDigest,
        "sArchitecture": sArchitecture,
    }


def fdictReadArchiveRecord(dictEnvironment):
    """Return the deposit record from an environment payload, or ``None``."""
    dictContainer = (dictEnvironment or {}).get("dictContainer")
    if not isinstance(dictContainer, dict):
        return None
    dictRecord = dictContainer.get(S_IMAGE_ARCHIVE_KEY)
    return dictRecord if isinstance(dictRecord, dict) else None


def flistDescribeArchiveMismatch(dictEnvironment):
    """Return the reasons the deposit record fails to cover the envelope.

    An empty list means the archive covers this envelope. Callers that
    only need the verdict use :func:`fbImageArchiveMatchesEnvelope`;
    the reasons exist because a refusal that names its cause is the
    difference between a researcher fixing a deposit and a researcher
    opening a tab.

    Raises ``LookupError`` when nothing could be compared -- an absent
    envelope, an absent container block, or a capture that recorded no
    architecture. That is UNCHECKED, not mismatched: red means the
    deposit diverges from the envelope, a claim nobody has earned when
    one side of the comparison is missing.
    """
    dictContainer = (dictEnvironment or {}).get("dictContainer")
    if not isinstance(dictContainer, dict):
        raise LookupError(
            "This project has no environment snapshot yet, so there "
            "is nothing to compare a deposit against."
        )
    sEnvelopeDigest = str(dictContainer.get("sImageDigest") or "")
    sEnvelopeArchitecture = str(dictContainer.get("sArchitecture") or "")
    if not sEnvelopeDigest:
        raise LookupError(
            "The environment snapshot pins no image, so there is "
            "nothing a deposit could be checked against. Regenerate "
            "the envelope while the container is running."
        )
    # ABSENCE is answerable without comparing anything, so it is
    # answered before the architecture is required. The two checks
    # were the other way round until 2026-09-08, which made a project
    # with a complete-enough envelope and NO deposit report UNCHECKED
    # -- the row showed a grey "?" and the Level 3 cell followed it,
    # over a question whose answer was plainly "no archive exists".
    # A researcher who had just declined saw Level 2 go green and
    # Level 3 stay a question mark (researcher-reported).
    #
    # This does not weaken "unchecked is never red": that rule forbids
    # claiming DIVERGENCE with one side of the comparison missing, and
    # "no deposit was made" is not a divergence claim. The architecture
    # is what a COMPARISON needs, so it is required on the comparison
    # path only.
    dictRecord = dictContainer.get(S_IMAGE_ARCHIVE_KEY)
    if not isinstance(dictRecord, dict):
        return ["No image archive has been deposited for this envelope."]
    if not sEnvelopeArchitecture:
        raise LookupError(
            "The environment snapshot records no architecture, so "
            "the deposit on record cannot be checked against it. "
            "Regenerate the envelope while the container is running."
        )
    return _flistCompareRecordToEnvelope(
        dictRecord, sEnvelopeDigest, sEnvelopeArchitecture,
    )


def _flistCompareRecordToEnvelope(
    dictRecord, sEnvelopeDigest, sEnvelopeArchitecture,
):
    """Return every way one deposit record fails to cover one envelope."""
    listReasons = []
    listReasons.extend(_flistDescribeDoiProblems(dictRecord))
    if not _RE_SHA256.match(str(dictRecord.get("sTarballSha256") or "")):
        listReasons.append(
            "The deposit records no sha256 of the uploaded tarball, "
            "so nothing binds the archived bytes to this project."
        )
    iBytes = dictRecord.get("iTarballBytes")
    if not isinstance(iBytes, int) or isinstance(iBytes, bool) or iBytes <= 0:
        listReasons.append("The deposit records no tarball size.")
    if dictRecord.get("sProvenance") not in (
        S_PROVENANCE_ORIGINAL, S_PROVENANCE_VERIFIED_EQUIVALENT,
    ):
        listReasons.append(
            "The deposit does not say whether the archived environment "
            "IS the one that produced these results or merely "
            "reproduces them."
        )
    if str(dictRecord.get("sImageDigest") or "") != sEnvelopeDigest:
        listReasons.append(
            "The deposit covers a different image than the envelope "
            "pins. Deposit the image the envelope now records."
        )
    if str(dictRecord.get("sArchitecture") or "") != sEnvelopeArchitecture:
        listReasons.append(
            "The deposit covers the "
            + (str(dictRecord.get("sArchitecture") or "") or "unrecorded")
            + " build of this image and the envelope records "
            + sEnvelopeArchitecture + "."
        )
    return listReasons


def _flistDescribeDoiProblems(dictRecord):
    """Return the DOI problems that void a deposit record.

    THE CONCEPT DOI IS THE TRAP. Zenodo mints two: the version DOI
    names one immutable deposit, and the concept DOI always resolves to
    the NEWEST version of the record. Recording the concept DOI would
    quietly repoint every earlier paper at whatever image was deposited
    last -- the link still resolves, nothing errors, and the archived
    environment is simply not the one that produced those numbers.
    """
    sVersionDoi = str(dictRecord.get("sVersionDoi") or "")
    if not _RE_DOI.match(sVersionDoi):
        return ["The deposit records no version DOI."]
    if sVersionDoi == str(dictRecord.get("sConceptDoi") or ""):
        return [
            "The deposit records Zenodo's CONCEPT DOI, which always "
            "resolves to the newest version of the record rather than "
            "to the environment that produced these results. Record "
            "the version DOI of the specific deposit."
        ]
    return []


def fbImageArchiveMatchesEnvelope(dictEnvironment):
    """Return True iff a deposit covers the image the envelope pins.

    The Level 3 criterion, and it never reads the Level 2 answer: it
    asks one thing, whether a matching archive exists. Unproven is not
    passed, so an envelope this cannot be compared against fails here
    and reports UNCHECKED on the row.
    """
    try:
        return not flistDescribeArchiveMismatch(dictEnvironment)
    except LookupError:
        return False


def fsResolveArchiveState(
    dictEnvironment, dictWorkflow, sCheckState="",
    bPinnedImageInLocalStore=None, bHostProject=False,
):
    """Return the row state for this project's environment archive.

    ``bPinnedImageInLocalStore`` is a THREE-state answer and the third
    state is load-bearing: ``None`` means nobody looked, and a project
    that declined is then merely *not archived*, never CLOSED. Closed
    says the opportunity is gone and Level 3 is unreachable for this
    result -- a statement that needs positive evidence of absence, not
    the absence of evidence.
    """
    if bHostProject:
        return S_STATE_NOT_APPLICABLE
    if sCheckState == "checking":
        return S_STATE_ARCHIVING
    if sCheckState == "uncheckable":
        return S_STATE_UNCHECKED
    try:
        listReasons = flistDescribeArchiveMismatch(dictEnvironment)
    except LookupError:
        return S_STATE_UNCHECKED
    if not listReasons:
        return S_STATE_ARCHIVED
    if fdictReadArchiveRecord(dictEnvironment) is not None:
        return S_STATE_MISMATCHED
    if (
        bPinnedImageInLocalStore is False
        and _fsRecordedAnswer(dictWorkflow) == S_ANSWER_DECLINED
    ):
        return S_STATE_CLOSED
    return S_STATE_NOT_ARCHIVED


def _fsRecordedAnswer(dictWorkflow):
    """Return the workflow's recorded archive answer, or ``''``."""
    dictAnswer = (dictWorkflow or {}).get(S_IMAGE_ARCHIVE_KEY)
    if not isinstance(dictAnswer, dict):
        return ""
    return str(dictAnswer.get("sAnswer") or "")


# The machine-readable line vaibify writes into the deposit's Zenodo
# description, and reads back when a researcher points at an existing
# record. Zenodo's metadata has no field for "which image is this", so
# without it a REFERENCE could not be verified at all -- the record
# would have to be taken on the researcher's word, and the Level 3
# criterion would be asserting something nobody checked.
S_DEPOSIT_FINGERPRINT_PREFIX = "vaibify-environment-archive: "

_T_FINGERPRINT_FIELDS = (
    "sImageDigest", "sArchitecture", "sTarballName",
    "sTarballSha256", "sImageStreamSha256", "iTarballBytes",
)


def fdictStampDepositMetadata(dictMetadata, dictRecord):
    """Return the deposit metadata with the archive fingerprint appended."""
    dictStamped = dict(dictMetadata or {})
    sFingerprint = S_DEPOSIT_FINGERPRINT_PREFIX + json.dumps(
        {sField: dictRecord.get(sField) for sField in _T_FINGERPRINT_FIELDS},
        sort_keys=True,
    )
    sDescription = str(dictStamped.get("sDescription") or "").strip()
    dictStamped["sDescription"] = (
        (sDescription + "\n\n" if sDescription else "") + sFingerprint
    )
    return dictStamped


def fdictParseDepositFingerprint(sDescription):
    """Return the archive fingerprint carried by a description, or ``None``.

    ``None`` covers every way it is not there -- no line, malformed
    JSON, a deposit vaibify did not make -- because a caller cannot
    act differently on those: none of them establishes what the
    deposit contains.
    """
    for sLine in str(sDescription or "").splitlines():
        sStripped = sLine.strip()
        if not sStripped.startswith(S_DEPOSIT_FINGERPRINT_PREFIX):
            continue
        try:
            jsonParsed = json.loads(
                sStripped[len(S_DEPOSIT_FINGERPRINT_PREFIX):],
            )
        except ValueError:
            return None
        return jsonParsed if isinstance(jsonParsed, dict) else None
    return None


def flistDescribeReferenceProblems(
    dictZenodoRecord, sSuppliedDoi, sEnvelopeDigest, sEnvelopeArchitecture,
):
    """Return why an existing Zenodo record cannot serve as this archive.

    An empty list means it can. Two refusals matter most and both are
    silent failures if skipped.

    A CONCEPT DOI resolves to the record's newest version, so the
    record that comes back carries a different ``doi`` than the one
    asked for -- that inequality is the only reliable way to tell the
    two apart from a string, and without it a researcher pointing at
    the concept doi would pin their paper to whatever is deposited
    next.

    And a record vaibify cannot READ the fingerprint out of is refused
    rather than accepted on trust. Referencing is meant for papers
    2..N sharing one image, so the deposit is one vaibify made and
    stamped; accepting an unstamped record would turn the Level 3
    criterion into an assertion about bytes nobody looked at.
    """
    if not isinstance(dictZenodoRecord, dict):
        return ["Zenodo returned no record for that DOI."]
    sRecordDoi = str(
        (dictZenodoRecord.get("metadata") or {}).get("doi")
        or dictZenodoRecord.get("doi") or ""
    )
    if sRecordDoi and sRecordDoi != str(sSuppliedDoi or "").strip():
        return [
            "That is Zenodo's concept DOI, which always resolves to "
            "the newest version of the record — currently "
            + sRecordDoi + ". Use the version DOI of the deposit that "
            "actually holds the image these results were produced in."
        ]
    dictFingerprint = fdictParseDepositFingerprint(
        (dictZenodoRecord.get("metadata") or {}).get("description")
        or dictZenodoRecord.get("description") or "",
    )
    if dictFingerprint is None:
        return [
            "That record does not describe which container image it "
            "holds, so vaibify cannot confirm it covers this project's "
            "environment. Reference a deposit vaibify made, or deposit "
            "the image from here."
        ]
    return _flistCompareFingerprintToEnvelope(
        dictFingerprint, sEnvelopeDigest, sEnvelopeArchitecture,
    )


def _flistCompareFingerprintToEnvelope(
    dictFingerprint, sEnvelopeDigest, sEnvelopeArchitecture,
):
    """Return the ways a referenced deposit does not cover this envelope."""
    listReasons = []
    if str(dictFingerprint.get("sImageDigest") or "") != sEnvelopeDigest:
        listReasons.append(
            "That deposit holds a different image than this project's "
            "envelope pins."
        )
    if str(dictFingerprint.get("sArchitecture") or "") != sEnvelopeArchitecture:
        listReasons.append(
            "That deposit holds the "
            + (str(dictFingerprint.get("sArchitecture") or "") or "unrecorded")
            + " build and this project's envelope records "
            + sEnvelopeArchitecture + "."
        )
    if not _RE_SHA256.match(str(dictFingerprint.get("sTarballSha256") or "")):
        listReasons.append(
            "That deposit records no sha256 for its image tarball."
        )
    return listReasons


# The verdicts of the attestation-time re-hash. VACUOUS is the one
# that must not collapse into any other: an image obtained by loading
# the deposit matches its own bytes always, so reporting that as a
# pass would attest a comparison nobody made. Same shape as rooting a
# rerun's comparison on the shadow rather than the live repository.
S_RECHECK_MATCHED = "matched"
S_RECHECK_DIFFERS = "differs"
S_RECHECK_VACUOUS = "vacuous"
S_RECHECK_UNAVAILABLE = "unavailable"

# Written by whoever obtains the image BY LOADING THE DEPOSIT --
# today only reproduce.sh's fallback path. Its presence is the only
# thing that can tell the re-check its two sides have one origin.
#
# A FILE beside the envelope rather than a field inside it, because
# `.vaibify/environment.json` is pinned in `MANIFEST.sha256` and
# `reproduce.sh` ends by verifying that manifest: writing the marker
# into the envelope would make every fallback reproduction fail its
# own final check.
S_LOADED_FROM_ARCHIVE_MARKER = ".vaibify/image_loaded_from_archive"


def fbImageWasLoadedFromArchive(filesRepo):
    """Return True iff this clone obtained its image from the deposit.

    Takes whatever the reproducibility layer's callers take -- an
    adapter or a repo path -- through the same normalizer every other
    gate here uses. A snapshot adapter that never sampled this path
    raises ``KeyError``, which reads as "no marker": the marker's
    absence is the ordinary case, and a poll must never fail over it.
    """
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    try:
        return bool(ffilesEnsureRepoFiles(filesRepo).fbIsFile(
            S_LOADED_FROM_ARCHIVE_MARKER,
        ))
    except (OSError, ValueError, KeyError, AttributeError):
        return False


def fdictJudgeArchiveRecheck(
    dictEnvironment, sLocalImageStreamSha256, bLoadedFromArchive=False,
):
    """Return the attestation-time verdict on the deposited environment.

    ``sLocalImageStreamSha256`` is the sha256 of ``docker save``'s
    UNCOMPRESSED output for the pinned image on THIS machine, or ``""``
    when one could not be produced. The caller does the saving; this
    decides what the answer means, which is where the trap is: a
    project whose image was obtained by loading the deposit would
    compare a download against itself and match every time.
    """
    dictRecord = fdictReadArchiveRecord(dictEnvironment)
    if dictRecord is None:
        return {
            "sVerdict": S_RECHECK_UNAVAILABLE,
            "sReason": "No environment archive is recorded.",
        }
    if bLoadedFromArchive:
        return {
            "sVerdict": S_RECHECK_VACUOUS,
            "sReason": (
                "This image was obtained by loading the archived "
                "deposit, so re-hashing it compares the download "
                "against itself and establishes nothing."
            ),
        }
    sDeposited = str(dictRecord.get("sImageStreamSha256") or "")
    if not sDeposited:
        return {
            "sVerdict": S_RECHECK_UNAVAILABLE,
            "sReason": (
                "This deposit predates the image-content hash, so "
                "there is nothing a fresh save can be compared to."
            ),
        }
    if not sLocalImageStreamSha256:
        return {
            "sVerdict": S_RECHECK_UNAVAILABLE,
            "sReason": (
                "The image the envelope pins is not on this machine, "
                "so it could not be re-saved and compared."
            ),
        }
    if sLocalImageStreamSha256 == sDeposited:
        return {
            "sVerdict": S_RECHECK_MATCHED,
            "sReason": (
                "A fresh save of the local image hashes to the bytes "
                "deposited under " + str(dictRecord.get("sVersionDoi") or "")
                + "."
            ),
        }
    return {
        "sVerdict": S_RECHECK_DIFFERS,
        "sReason": (
            "A fresh save of the local image does not hash to the "
            "bytes deposited under "
            + str(dictRecord.get("sVersionDoi") or "") + "."
        ),
    }
