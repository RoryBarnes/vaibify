"""The host-side record of council credential consent and test outcomes.

One document per machine, beside the campaign store, holding two kinds
of fact that used to be one:

* a CONSENT entry per key — "the researcher permits councils to use
  this provider's login in this image on this computer" — carrying a
  monotonically increasing ``iConsentGeneration``; and
* OUTCOME entries — "these checks ended this way at this time" — each
  stamped with the generation it ran under, appended and never
  rewritten.

A key is provider + immutable image id + host platform. The key is
AUTHORIZED only when its consent is active, no test is in flight for
it, and its LATEST outcome is ``passed`` at the CURRENT generation.
The generation is what makes withdrawal final: a test that started
before a withdrawal publishes at an old generation and enables nothing,
and a legacy record cannot resurrect a key the researcher withdrew.

Legacy v1 (one record) and v2 (``listRecords``) files are still read.
Each record that passes the original evidence rules is a ``passed``
outcome at generation 1 with an IMPLIED consent, and that implied
consent exists only while no explicit consent entry exists for its
key. A damaged file is never rewritten in place: evaluation reports
it, and the next write moves it aside as
``credentialEvidence.damaged-<iso>.json`` before starting afresh.

Two locks, both ``fcntl.flock`` on DEDICATED lock files — never on the
document, which ``os.replace`` swaps, so a lock on it would exclude
nothing after the first write:

* the STORE lock serializes every read-modify-write. Nothing slow runs
  under it — no Docker call, no network, no runner wait — and
  :func:`fbIsStoreLockHeld` lets a test fake prove that; and
* a per-key TEST lock held for a whole credential test, whose only
  job is duplicate prevention.

The document never holds a token, a token digest, or a credential
path; it records who agreed to what, and how each check ended.
"""

import copy
import datetime
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager

__all__ = [
    "I_SCHEMA_VERSION",
    "I_MAX_RECORDED_ADMISSIONS",
    "LIST_LEGACY_REQUIRED_KEYS",
    "S_CONSENT_ACTIVE",
    "S_CONSENT_WITHDRAWN",
    "S_OUTCOME_PASSED",
    "S_OUTCOME_FAILED",
    "S_OUTCOME_INCOMPLETE",
    "SET_OUTCOMES",
    "S_METHOD_MANUAL",
    "S_METHOD_IN_APP_TEST",
    "S_STATE_AUTHORIZED",
    "S_STATE_NO_CONSENT",
    "S_STATE_WITHDRAWN",
    "S_STATE_NEVER_TESTED",
    "S_STATE_TEST_IN_FLIGHT",
    "S_STATE_LAST_TEST_FAILED",
    "S_STATE_LAST_TEST_INCOMPLETE",
    "S_STATE_STALE_GENERATION",
    "S_STATE_DAMAGED",
    "S_STORE_LOCK_BASENAME",
    "S_FAILURE_CLASS_ADMISSION_REFUSED",
    "CredentialStoreError",
    "CredentialAdmissionRefusedError",
    "fsComposeCredentialKey",
    "fsComposeTestLockBasename",
    "fbIsStoreLockHeld",
    "fcontextHoldStoreLock",
    "ffileTryAcquireTestLock",
    "fdictReadCredentialDocument",
    "fdictEvaluateCredentialKey",
    "fsExplainLegacyRecordRefusal",
    "fsExplainLegacyRefusals",
    "flistDescribeKeysForProvider",
    "fdictRecordConsent",
    "fdictWithdrawConsent",
    "fdictBeginCredentialTest",
    "fdictPublishCredentialTestOutcome",
    "fnRecordAdmission",
    "fdictMutateCredentialDocument",
    "fnWriteJsonAtomically",
    "fsNowIso",
]

I_SCHEMA_VERSION = 3

# The admission log is an audit trail, not a ledger of record: it is
# bounded so a long-running council cannot grow the file without limit.
I_MAX_RECORDED_ADMISSIONS = 500

# The keys the original (v1/v2) evidence rules required. A legacy
# record missing any of them never enabled anything and still does not.
LIST_LEGACY_REQUIRED_KEYS = [
    "sProvider",
    "sBackend",
    "sCliVersion",
    "sImageIdentity",
    "sCredentialSchema",
    "sCredentialSource",
    "sHostPlatform",
    "sVerificationDate",
]

S_CONSENT_ACTIVE = "active"
S_CONSENT_WITHDRAWN = "withdrawn"

S_OUTCOME_PASSED = "passed"
S_OUTCOME_FAILED = "failed"
S_OUTCOME_INCOMPLETE = "incomplete"
SET_OUTCOMES = frozenset(
    {S_OUTCOME_PASSED, S_OUTCOME_FAILED, S_OUTCOME_INCOMPLETE})

S_METHOD_MANUAL = "manual"
S_METHOD_IN_APP_TEST = "inAppTest"

S_STATE_AUTHORIZED = "authorized"
S_STATE_NO_CONSENT = "noConsent"
S_STATE_WITHDRAWN = "withdrawn"
S_STATE_NEVER_TESTED = "neverTested"
S_STATE_TEST_IN_FLIGHT = "testInFlight"
S_STATE_LAST_TEST_FAILED = "lastTestFailed"
S_STATE_LAST_TEST_INCOMPLETE = "lastTestIncomplete"
S_STATE_STALE_GENERATION = "staleGeneration"
S_STATE_DAMAGED = "damaged"

S_STORE_LOCK_BASENAME = "credentialStore.lock"

# The turn failure class a refused admission is recorded under; mirrored
# in agentCouncilCampaign.SET_RETRYABLE_TURN_FAILURE_REASONS.
S_FAILURE_CLASS_ADMISSION_REFUSED = "credentialAdmissionRefused"

_S_KEY_SEPARATOR = "|"
_I_IMAGE_DIGEST_PREFIX_LENGTH = 16

_threadLocalLockState = threading.local()


class CredentialStoreError(Exception):
    """The credential document cannot be changed as asked."""


class CredentialAdmissionRefusedError(Exception):
    """A council turn may not stage a token: consent or a test is missing.

    Classified apart from every provider failure on purpose. A refused
    admission is not the model, the network, or the login failing — it
    is the researcher's own consent saying no — so the campaign shows
    it as needing the researcher rather than as a provider error. The
    engine reads ``sCouncilFailureClass`` off the exception, which
    keeps the pure engine free of this module.
    """

    sCouncilFailureClass = S_FAILURE_CLASS_ADMISSION_REFUSED


def fsNowIso():
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def fsComposeCredentialKey(sProvider, sImageIdentity, sHostPlatform=None):
    """Return the single-string key for provider + image + platform."""
    return _S_KEY_SEPARATOR.join((
        sProvider, sImageIdentity,
        sHostPlatform if sHostPlatform is not None else sys.platform))


def fsComposeTestLockBasename(sProvider, sImageIdentity):
    """Return the stable per-key test-lock file name.

    The image id is reduced to a digest prefix so the name is a safe
    file name whatever the id looks like; the provider is validated by
    the caller against the closed provider vocabulary.
    """
    sDigest = hashlib.sha256(
        sImageIdentity.encode("utf-8")).hexdigest()[
            :_I_IMAGE_DIGEST_PREFIX_LENGTH]
    return f"credentialTest-{sProvider}-{sDigest}.lock"


# ----- locks -------------------------------------------------------------


def fbIsStoreLockHeld():
    """Return True while THIS thread holds the store lock.

    Exists so a test double standing in for Docker can refuse to be
    called under the lock, which is how "nothing slow runs under it"
    is made falsifiable rather than asserted.
    """
    return getattr(_threadLocalLockState, "iDepth", 0) > 0


def _ffileOpenLockFile(sDirectory, sBasename):
    """Open (creating 0600) a dedicated lock file, refusing a symlink."""
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    iDescriptor = os.open(
        os.path.join(sDirectory, sBasename),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    return os.fdopen(iDescriptor, "r+", encoding="utf-8")


@contextmanager
def fcontextHoldStoreLock(sDirectory):
    """Hold the store lock for one read-modify-write, then release it."""
    fileLock = _ffileOpenLockFile(sDirectory, S_STORE_LOCK_BASENAME)
    try:
        fcntl.flock(fileLock, fcntl.LOCK_EX)
        _threadLocalLockState.iDepth = (
            getattr(_threadLocalLockState, "iDepth", 0) + 1)
        try:
            yield fileLock
        finally:
            _threadLocalLockState.iDepth -= 1
            fcntl.flock(fileLock, fcntl.LOCK_UN)
    finally:
        fileLock.close()


def ffileTryAcquireTestLock(sDirectory, sProvider, sImageIdentity):
    """Return the held per-key test lock, or None when a test holds it.

    Non-blocking: a second request for the same key must learn at once
    that a test is running, never queue behind it. The caller releases
    by closing the returned file.
    """
    fileLock = _ffileOpenLockFile(
        sDirectory, fsComposeTestLockBasename(sProvider, sImageIdentity))
    try:
        fcntl.flock(fileLock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fileLock.close()
        return None
    return fileLock


# ----- reading -----------------------------------------------------------


def _fdictEmptyDocument():
    """Return a fresh, empty v3 document."""
    return {"iSchemaVersion": I_SCHEMA_VERSION, "listLegacyRecords": [],
            "dictConsents": {}, "dictInFlight": {}, "listOutcomes": [],
            "listAdmissions": []}


def _fbIsWellFormedVersionThree(jsonDocument):
    """Return True when a v3 document has every container it needs."""
    return (
        isinstance(jsonDocument.get("listLegacyRecords"), list)
        and isinstance(jsonDocument.get("dictConsents"), dict)
        and isinstance(jsonDocument.get("dictInFlight"), dict)
        and isinstance(jsonDocument.get("listOutcomes"), list)
        and isinstance(jsonDocument.get("listAdmissions"), list)
        and all(isinstance(dictEntry, dict) for dictEntry in
                jsonDocument["dictConsents"].values())
        and all(isinstance(dictEntry, dict)
                for dictEntry in jsonDocument["listOutcomes"]))


def _fdictNormaliseDocument(jsonDocument):
    """Return the v3 form of any readable document, or None if damaged."""
    if isinstance(jsonDocument, list):
        dictDocument = _fdictEmptyDocument()
        dictDocument["listLegacyRecords"] = list(jsonDocument)
        return dictDocument
    if not isinstance(jsonDocument, dict):
        return None
    if "iSchemaVersion" in jsonDocument:
        if jsonDocument["iSchemaVersion"] != I_SCHEMA_VERSION or not (
                _fbIsWellFormedVersionThree(jsonDocument)):
            return None
        return jsonDocument
    dictDocument = _fdictEmptyDocument()
    listRecords = jsonDocument.get("listRecords")
    dictDocument["listLegacyRecords"] = (
        list(listRecords) if isinstance(listRecords, list)
        else [jsonDocument])
    return dictDocument


def fdictReadCredentialDocument(sEvidencePath):
    """Read the document as v3; report damage instead of raising.

    Returns ``{"dictDocument", "bExists", "sDamage"}``. A missing file
    is an empty document; an unreadable or wrongly shaped one is an
    empty document with ``sDamage`` naming why, so every key evaluates
    to "needs a test" and the damage is shown rather than hidden.
    """
    if not os.path.isfile(sEvidencePath):
        return {"dictDocument": _fdictEmptyDocument(), "bExists": False,
                "sDamage": ""}
    try:
        with open(sEvidencePath, encoding="utf-8") as fileEvidence:
            jsonDocument = json.load(fileEvidence)
    except (OSError, ValueError) as error:
        return {"dictDocument": _fdictEmptyDocument(), "bExists": True,
                "sDamage": f"unreadable ({type(error).__name__})"}
    dictDocument = _fdictNormaliseDocument(jsonDocument)
    if dictDocument is None:
        return {"dictDocument": _fdictEmptyDocument(), "bExists": True,
                "sDamage": "not a credential document this version reads"}
    return {"dictDocument": dictDocument, "bExists": True, "sDamage": ""}


# ----- the legacy rules --------------------------------------------------


def fsExplainLegacyRecordRefusal(jsonRecord, sProvider, sImageIdentity,
                                 sHostPlatform):
    """Return why ONE legacy record does not enable this key, or "".

    The original v1/v2 rules, unchanged: every required key present,
    the runner backend, the provider's credential schema, this host
    platform, and — when the caller knows it — this image.
    """
    if not isinstance(jsonRecord, dict):
        return "a legacy evidence record is not a mapping"
    for sRequiredKey in LIST_LEGACY_REQUIRED_KEYS:
        if not jsonRecord.get(sRequiredKey):
            return f"a legacy evidence record is missing '{sRequiredKey}'"
    from . import agentCouncilProviderRegistry
    for sKey, sExpected, sWhat in (
            ("sProvider", sProvider, "provider"),
            ("sBackend", "runner", "backend"),
            ("sCredentialSchema",
             agentCouncilProviderRegistry.fsGetProviderCredentialSchema(
                 sProvider), "credential schema"),
            ("sHostPlatform", sHostPlatform, "host platform")):
        if jsonRecord[sKey] != sExpected:
            return (f"a legacy evidence record's {sWhat} is "
                    f"{jsonRecord[sKey]!r}, not {sExpected!r}")
    if sImageIdentity is not None and (
            jsonRecord["sImageIdentity"] != sImageIdentity):
        return (f"a legacy evidence record was made for image "
                f"{jsonRecord['sImageIdentity']!r}, not {sImageIdentity!r}; "
                "evidence does not carry over between images, so the "
                "check must be re-run for this one")
    return ""


def fsExplainLegacyRefusals(dictDocument, sProvider, sImageIdentity,
                            sHostPlatform=None):
    """Return why no legacy record enables this key, or "" if none tried.

    Kept so a manually written record that is one key short still says
    WHICH key, rather than reading as though it did not exist.
    """
    sPlatform = sHostPlatform if sHostPlatform is not None else sys.platform
    setReasons = {
        fsExplainLegacyRecordRefusal(
            jsonRecord, sProvider, sImageIdentity, sPlatform)
        for jsonRecord in dictDocument["listLegacyRecords"]}
    return "; ".join(sorted(sReason for sReason in setReasons if sReason))


def _fdictLegacyPassFor(dictDocument, sProvider, sImageIdentity,
                        sHostPlatform):
    """Return the synthesized legacy ``passed`` outcome for a key, or None."""
    for jsonRecord in dictDocument["listLegacyRecords"]:
        if not fsExplainLegacyRecordRefusal(
                jsonRecord, sProvider, sImageIdentity, sHostPlatform):
            return {"sOutcome": S_OUTCOME_PASSED, "iConsentGeneration": 1,
                    "sVerificationMethod": S_METHOD_MANUAL,
                    "sFinishedIso": str(jsonRecord["sVerificationDate"]),
                    "sCliVersion": str(jsonRecord["sCliVersion"]),
                    "dictLegacyRecord": copy.deepcopy(jsonRecord)}
    return None


# ----- evaluation ----------------------------------------------------------


def _fdictCurrentConsent(dictDocument, sKey, dictLegacyPass):
    """Return the explicit consent, else the implied legacy one, else None."""
    dictExplicit = dictDocument["dictConsents"].get(sKey)
    if dictExplicit is not None:
        return dictExplicit
    if dictLegacyPass is not None:
        return {"iConsentGeneration": 1, "sState": S_CONSENT_ACTIVE,
                "bImplied": True, "sConsentIso": ""}
    return None


def _fdictLatestOutcome(dictDocument, sKey, dictLegacyPass):
    """Return the latest outcome for a key (a legacy pass counts first)."""
    for dictOutcome in reversed(dictDocument["listOutcomes"]):
        if dictOutcome.get("sKey") == sKey:
            return dictOutcome
    return dictLegacyPass


def fdictEvaluateCredentialKey(dictDocument, sProvider, sImageIdentity,
                               sHostPlatform=None):
    """Evaluate ONE key against the transition table; never raises.

    Returns ``{"bAuthorized", "sState", "dictConsent", "dictOutcome",
    "dictInFlight"}``. This is the single authority: the gate, the
    per-turn admission and the test admission all ask it, under the
    store lock where they act on the answer.
    """
    sPlatform = sHostPlatform if sHostPlatform is not None else sys.platform
    sKey = fsComposeCredentialKey(sProvider, sImageIdentity, sPlatform)
    dictLegacyPass = _fdictLegacyPassFor(
        dictDocument, sProvider, sImageIdentity, sPlatform)
    dictConsent = _fdictCurrentConsent(dictDocument, sKey, dictLegacyPass)
    dictOutcome = _fdictLatestOutcome(dictDocument, sKey, dictLegacyPass)
    dictInFlight = dictDocument["dictInFlight"].get(sKey)
    dictAnswer = {"bAuthorized": False, "sState": "",
                  "dictConsent": dictConsent, "dictOutcome": dictOutcome,
                  "dictInFlight": dictInFlight}
    dictAnswer["sState"] = _fsClassifyKeyState(
        dictConsent, dictOutcome, dictInFlight)
    dictAnswer["bAuthorized"] = dictAnswer["sState"] == S_STATE_AUTHORIZED
    return dictAnswer


def _fsClassifyKeyState(dictConsent, dictOutcome, dictInFlight):
    """Return the one state name the transition table assigns."""
    if dictConsent is None:
        return S_STATE_NO_CONSENT
    if dictConsent.get("sState") != S_CONSENT_ACTIVE:
        return S_STATE_WITHDRAWN
    if dictInFlight is not None:
        return S_STATE_TEST_IN_FLIGHT
    if dictOutcome is None:
        return S_STATE_NEVER_TESTED
    if dictOutcome.get("iConsentGeneration") != (
            dictConsent.get("iConsentGeneration")):
        return S_STATE_STALE_GENERATION
    if dictOutcome.get("sOutcome") == S_OUTCOME_PASSED:
        return S_STATE_AUTHORIZED
    if dictOutcome.get("sOutcome") == S_OUTCOME_FAILED:
        return S_STATE_LAST_TEST_FAILED
    return S_STATE_LAST_TEST_INCOMPLETE


def flistDescribeKeysForProvider(dictDocument, sProvider,
                                 sHostPlatform=None):
    """Return every image id this document knows for one provider.

    Explicit keys and legacy records both count, so an image-blind
    reader (the doctor, which has no container to ask) can still say
    whether ANY image is authorized for the provider.
    """
    sPlatform = sHostPlatform if sHostPlatform is not None else sys.platform
    setImages = set()
    for sKey in list(dictDocument["dictConsents"]) + [
            dictOutcome.get("sKey", "")
            for dictOutcome in dictDocument["listOutcomes"]]:
        listParts = sKey.split(_S_KEY_SEPARATOR)
        if len(listParts) == 3 and listParts[0] == sProvider and (
                listParts[2] == sPlatform):
            setImages.add(listParts[1])
    for jsonRecord in dictDocument["listLegacyRecords"]:
        if isinstance(jsonRecord, dict) and jsonRecord.get(
                "sProvider") == sProvider and jsonRecord.get(
                    "sImageIdentity"):
            setImages.add(str(jsonRecord["sImageIdentity"]))
    return sorted(setImages)


# ----- writing -------------------------------------------------------------


def fnWriteJsonAtomically(sEvidencePath, dictDocument):
    """Write a JSON document through a 0600 temp file and ``os.replace``."""
    sDirectory = os.path.dirname(sEvidencePath)
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    iDescriptor, sTemporaryPath = tempfile.mkstemp(
        prefix=".credentialDocument-", suffix=".tmp", dir=sDirectory)
    try:
        os.fchmod(iDescriptor, 0o600)
        with os.fdopen(iDescriptor, "w", encoding="utf-8") as fileTemporary:
            json.dump(dictDocument, fileTemporary, indent=2, sort_keys=True)
            fileTemporary.flush()
            os.fsync(fileTemporary.fileno())
        os.replace(sTemporaryPath, sEvidencePath)
    except BaseException:
        if os.path.exists(sTemporaryPath):
            os.remove(sTemporaryPath)
        raise


def _fsSetDamagedDocumentAside(sEvidencePath):
    """Rename a damaged document aside; return the name it now has."""
    sStamp = fsNowIso().replace(":", "").replace("+", "Z")
    sDamagedPath = os.path.join(
        os.path.dirname(sEvidencePath),
        f"credentialEvidence.damaged-{sStamp}.json")
    os.replace(sEvidencePath, sDamagedPath)
    return sDamagedPath


def fdictMutateCredentialDocument(sEvidencePath, fgenericMutation):
    """Run one read-modify-write under the store lock; return its result.

    ``fgenericMutation(dictDocument)`` edits the document in place and
    returns whatever the caller should receive. It must be pure host
    bookkeeping — this function holds the store lock for its whole
    duration. A damaged file is renamed aside before the first write,
    never rewritten in place; a mutation that changes nothing writes
    nothing, so a legacy file is only converted when a fact changes.
    """
    with fcontextHoldStoreLock(os.path.dirname(sEvidencePath)):
        dictRead = fdictReadCredentialDocument(sEvidencePath)
        dictDocument = dictRead["dictDocument"]
        sBefore = json.dumps(dictDocument, sort_keys=True)
        if dictRead["sDamage"]:
            _fsSetDamagedDocumentAside(sEvidencePath)
        genericResult = fgenericMutation(dictDocument)
        if not dictRead["sDamage"] and (
                json.dumps(dictDocument, sort_keys=True) == sBefore):
            return genericResult
        dictDocument["iSchemaVersion"] = I_SCHEMA_VERSION
        fnWriteJsonAtomically(sEvidencePath, dictDocument)
        return genericResult


# ----- the transitions -----------------------------------------------------


def _fdictKeyEvaluation(dictDocument, sProvider, sImageIdentity):
    """Evaluate a key on this platform (a short alias for the mutators)."""
    return fdictEvaluateCredentialKey(dictDocument, sProvider, sImageIdentity)


def fdictRecordConsent(sEvidencePath, sProvider, sImageIdentity):
    """Record (or keep) active consent for a key; return the consent.

    Active already: kept, and an implied legacy consent is made
    explicit at the SAME generation. Withdrawn at g: active at g+1, so
    nothing tested before the withdrawal can authorize. None: g=1.
    """
    sKey = fsComposeCredentialKey(sProvider, sImageIdentity)

    def _fdictApply(dictDocument):
        dictConsent = _fdictKeyEvaluation(
            dictDocument, sProvider, sImageIdentity)["dictConsent"]
        if dictConsent is not None and dictConsent.get(
                "sState") == S_CONSENT_ACTIVE:
            iGeneration = dictConsent["iConsentGeneration"]
            sConsentIso = dictConsent.get("sConsentIso") or fsNowIso()
        else:
            iGeneration = (dictConsent["iConsentGeneration"] + 1
                           if dictConsent is not None else 1)
            sConsentIso = fsNowIso()
        dictDocument["dictConsents"][sKey] = {
            "sProvider": sProvider, "sImageIdentity": sImageIdentity,
            "sHostPlatform": sys.platform, "sState": S_CONSENT_ACTIVE,
            "iConsentGeneration": iGeneration, "sConsentIso": sConsentIso,
            "sChangedIso": fsNowIso()}
        return copy.deepcopy(dictDocument["dictConsents"][sKey])

    return fdictMutateCredentialDocument(sEvidencePath, _fdictApply)


def fdictWithdrawConsent(sEvidencePath, sProvider, sImageIdentity):
    """Withdraw consent for a key: g+1, withdrawn. Never waits on a test.

    Only the store lock is taken, so a running test is not waited for;
    its next admission and its publication both meet the new
    generation and enable nothing. With nothing active, nothing changes
    and ``None`` is returned.
    """
    sKey = fsComposeCredentialKey(sProvider, sImageIdentity)

    def _fdictApply(dictDocument):
        dictConsent = _fdictKeyEvaluation(
            dictDocument, sProvider, sImageIdentity)["dictConsent"]
        if dictConsent is None or dictConsent.get(
                "sState") != S_CONSENT_ACTIVE:
            return None
        dictDocument["dictConsents"][sKey] = {
            "sProvider": sProvider, "sImageIdentity": sImageIdentity,
            "sHostPlatform": sys.platform, "sState": S_CONSENT_WITHDRAWN,
            "iConsentGeneration": dictConsent["iConsentGeneration"] + 1,
            "sConsentIso": dictConsent.get("sConsentIso", ""),
            "sChangedIso": fsNowIso()}
        return copy.deepcopy(dictDocument["dictConsents"][sKey])

    return fdictMutateCredentialDocument(sEvidencePath, _fdictApply)


def _fdictComposeOutcome(sKey, dictMarker, sOutcome, dictDetails):
    """Compose one immutable outcome entry from its in-flight marker."""
    dictOutcome = {
        "sKey": sKey, "sOutcome": sOutcome,
        "sJobId": dictMarker["sJobId"],
        "iConsentGeneration": dictMarker["iConsentGeneration"],
        "sConsentIso": dictMarker.get("sConsentIso", ""),
        "sStartedIso": dictMarker.get("sStartedIso", ""),
        "sFinishedIso": fsNowIso(),
        "sVerificationMethod": S_METHOD_IN_APP_TEST,
        "listPassedChecks": [], "sFailedCheck": "", "sDetail": "",
        "sCliVersion": "", "bStale": False,
    }
    for sField in ("listPassedChecks", "sFailedCheck", "sDetail",
                   "sCliVersion", "listModelIds"):
        if sField in (dictDetails or {}):
            dictOutcome[sField] = copy.deepcopy(dictDetails[sField])
    return dictOutcome


def fdictBeginCredentialTest(sEvidencePath, sProvider, sImageIdentity,
                             sJobId, dictJobFacts=None):
    """Record a test's in-flight marker at the current generation.

    Refuses without active consent. A marker left behind by an earlier
    job (its hub died before cleanup ran) is converted to an
    ``incomplete`` outcome first, so no job's end is ever lost. From
    this point the key is NOT authorized: starting a re-test suspends
    the older pass (ruling 3).
    """
    sKey = fsComposeCredentialKey(sProvider, sImageIdentity)

    def _fdictApply(dictDocument):
        dictEvaluation = _fdictKeyEvaluation(
            dictDocument, sProvider, sImageIdentity)
        dictConsent = dictEvaluation["dictConsent"]
        if dictConsent is None or dictConsent.get(
                "sState") != S_CONSENT_ACTIVE:
            raise CredentialStoreError(
                f"{sProvider}: no active consent for this image; a "
                "credential test runs only after you consent to it")
        _fnConvertOrphanMarker(dictDocument, sKey)
        dictMarker = {"sJobId": sJobId, "sProvider": sProvider,
                      "sImageIdentity": sImageIdentity,
                      "iConsentGeneration": dictConsent["iConsentGeneration"],
                      "sConsentIso": dictConsent.get("sConsentIso", ""),
                      "sStartedIso": fsNowIso()}
        dictMarker.update(copy.deepcopy(dictJobFacts or {}))
        dictDocument["dictInFlight"][sKey] = dictMarker
        return copy.deepcopy(dictMarker)

    return fdictMutateCredentialDocument(sEvidencePath, _fdictApply)


def _fnConvertOrphanMarker(dictDocument, sKey):
    """Turn a leftover in-flight marker into an ``incomplete`` outcome."""
    dictMarker = dictDocument["dictInFlight"].pop(sKey, None)
    if dictMarker is None:
        return
    dictDocument["listOutcomes"].append(_fdictComposeOutcome(
        sKey, dictMarker, S_OUTCOME_INCOMPLETE,
        {"sDetail": "a later test started before this one reported an "
                    "end; it did not finish"}))


def fdictPublishCredentialTestOutcome(sEvidencePath, sProvider,
                                      sImageIdentity, sJobId, sOutcome,
                                      dictDetails=None):
    """Append a test's outcome at the generation it STARTED under.

    Durable for every outcome — passed, failed and incomplete alike.
    A publication whose job no longer holds the marker (another test
    replaced it, or restart cleanup already recorded it) is refused
    rather than appended twice. A publication after a withdrawal is
    written and flagged ``bStale``; its old generation already keeps
    it from authorizing anything.
    """
    if sOutcome not in SET_OUTCOMES:
        raise CredentialStoreError(f"unknown test outcome {sOutcome!r}")
    sKey = fsComposeCredentialKey(sProvider, sImageIdentity)

    def _fdictApply(dictDocument):
        dictMarker = dictDocument["dictInFlight"].get(sKey)
        if dictMarker is None or dictMarker.get("sJobId") != sJobId:
            raise CredentialStoreError(
                f"credential test {sJobId!r} is no longer the test in "
                "flight for this key; its outcome was already recorded")
        del dictDocument["dictInFlight"][sKey]
        dictOutcome = _fdictComposeOutcome(
            sKey, dictMarker, sOutcome, dictDetails)
        dictConsent = dictDocument["dictConsents"].get(sKey) or {}
        dictOutcome["bStale"] = (
            dictConsent.get("sState") != S_CONSENT_ACTIVE
            or dictConsent.get("iConsentGeneration")
            != dictMarker["iConsentGeneration"])
        dictDocument["listOutcomes"].append(dictOutcome)
        return copy.deepcopy(dictOutcome)

    return fdictMutateCredentialDocument(sEvidencePath, _fdictApply)


def fnRecordAdmission(dictDocument, sKey, dictAdmission):
    """Append one admission to the bounded audit log, in place.

    Called INSIDE a store-lock mutation by the two admitters; it is a
    pure edit of the document they already hold.
    """
    dictEntry = {"sKey": sKey, "sAdmittedIso": fsNowIso()}
    dictEntry.update(dictAdmission)
    dictDocument["listAdmissions"].append(dictEntry)
    del dictDocument["listAdmissions"][:-I_MAX_RECORDED_ADMISSIONS]
