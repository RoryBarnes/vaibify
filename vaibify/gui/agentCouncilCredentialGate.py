"""Version-bound, machine-readable credential enablement (R10).

The council's runner backend reuses the researcher's own provider
subscription, which is the single highest-trust thing vaibify touches —
so the backend is DISABLED BY DEFAULT. What enables it, per provider,
per immutable image, per host platform, is two separate facts held in
the host-side credential document
(``agentCouncilCredentialStore``): the researcher's CONSENT, given in
an authenticated browser session holding the container's lease, and a
credential test that PASSED under that consent's current generation.
vaibify runs that test itself after the consent (design section 2.7 /
9.7; ruling of 2026-09-29, which reversed the earlier rule that
nothing in this repository could enable the backend). The manual,
maintainer-run check that used to be the only way in is now the
fallback, and a legacy v1/v2 record it wrote still enables exactly
what it enabled before — until the researcher withdraws consent for
that key, after which it can never re-enable it.

No test in this repository stands in for the live properties of a real
subscription token; a green suite proves the gate that reads the
outcome, not the outcome.

Every path fails CLOSED: a missing document, a damaged one, a missing
consent, a withdrawn one, a test in flight, a failed or unfinished
test, or a pass recorded under an older generation all evaluate to
DISABLED with the reason named. The image identity is the pin that
does the runtime work, and it is the IMMUTABLE content-addressed image
id (``sha256:...``), never a tag: a tag can be repointed at a different
image — a different CLI — without anything about the record changing.
The START path resolves that id first and always passes it, the
runners launch from the same id, and every per-turn admission re-asks
this question for the same id under the store lock
(``councilRouteGuards.ffnBuildCredentialStager``), so a withdrawal
reaches the very next turn. Even with a match, login PRESENCE is probed
live at START (``fbRunnerCredentialIsPresent``, whose boolean answer
holds no credential material), and the first turn's failure
classification is the live usable-model probe.

Enablement never weakens the disclosure: the residual
token-exfiltration risk (a prompt-injected model reading its own
copied token) is structural, and the UI keeps stating it after
verification — verified means "the sharing works as designed", not
"proven secure".
"""

import os

from . import agentCouncilCredentialStore

__all__ = [
    "LIST_EVIDENCE_REQUIRED_KEYS",
    "S_EXPECTED_CREDENTIAL_SCHEMA",
    "fdictEvaluateCredentialEnablement",
    "fsExplainCredentialState",
    "fsResolveCredentialEvidencePath",
    "fsResolveCredentialStoreDirectory",
    "flistDescribeCredentialKeys",
]

LIST_EVIDENCE_REQUIRED_KEYS = list(
    agentCouncilCredentialStore.LIST_LEGACY_REQUIRED_KEYS)

# The narrowest authenticating field the extraction lane copies. A
# record naming any other schema was verified against a different
# handling path and enables nothing.
S_EXPECTED_CREDENTIAL_SCHEMA = "claudeAiOauth.accessToken"


def fsResolveCredentialEvidencePath():
    """Return the host app-data path the credential document lives at.

    Beside the campaign store under the researcher's home — host
    app-data, never the repository: the document is a statement about
    THIS machine's consent and tests, and committing one would enable
    the backend for machines it was never given on.
    """
    return os.path.join(
        os.path.expanduser("~"), ".vaibify", "agentCouncils",
        "credentialEvidence.json")


def fsResolveCredentialStoreDirectory():
    """Return the directory holding the document, its locks and jobs."""
    return os.path.dirname(fsResolveCredentialEvidencePath())


def _fdictDisable(sReason, sState, dictRecord=None):
    """Return the disabled evaluation with its reason and state named."""
    return {"bEnabled": False, "sReason": sReason, "sState": sState,
            "dictRecord": dictRecord}


_DICT_STATE_REASONS = {
    agentCouncilCredentialStore.S_STATE_NO_CONSENT: (
        "no credential test has been run for this provider in this "
        "project's image on this computer. Click the council button to "
        "consent and let vaibify run it."),
    agentCouncilCredentialStore.S_STATE_WITHDRAWN: (
        "you withdrew consent for councils to use this provider's login "
        "in this project's image on this computer. Consent again and "
        "re-run the credential test to use it."),
    agentCouncilCredentialStore.S_STATE_NEVER_TESTED: (
        "you consented, but no credential test has finished for this "
        "provider in this project's image yet. Run the credential test."),
    agentCouncilCredentialStore.S_STATE_TEST_IN_FLIGHT: (
        "a credential test is running for this provider in this "
        "project's image; the provider is suspended until it passes."),
    agentCouncilCredentialStore.S_STATE_LAST_TEST_FAILED: (
        "the last credential test for this provider in this project's "
        "image failed{sCheck}. Re-run the test once the cause is fixed."),
    agentCouncilCredentialStore.S_STATE_LAST_TEST_INCOMPLETE: (
        "the last credential test for this provider in this project's "
        "image did not finish; re-run it."),
    agentCouncilCredentialStore.S_STATE_STALE_GENERATION: (
        "the credential test that passed was run before your current "
        "consent; re-run the test to use this provider."),
}


def fsExplainCredentialState(dictEvaluation):
    """Return the display-ready reason for a non-authorized key."""
    sTemplate = _DICT_STATE_REASONS[dictEvaluation["sState"]]
    sFailedCheck = (dictEvaluation.get("dictOutcome") or {}).get(
        "sFailedCheck", "")
    return sTemplate.replace(
        "{sCheck}", f" at the check '{sFailedCheck}'" if sFailedCheck
        else "")


def _fdictRecordForEnabledKey(dictEvaluation):
    """Return the record the capability contract is built from."""
    dictOutcome = dict(dictEvaluation.get("dictOutcome") or {})
    return dictOutcome.pop("dictLegacyRecord", None) or dictOutcome


def fdictEvaluateCredentialEnablement(sProvider, sImageIdentity=None):
    """Evaluate the runner-backend enablement for one provider.

    Fails CLOSED on every path with the reason named, and reports the
    key's ``sState`` so readiness can tell "needs a test" from
    "withdrawn". ``sImageIdentity`` is compared when the caller knows
    it (the launch path resolves the project image first); a reader
    that does not know it (the doctor) is answered for ANY image this
    provider is enabled in, exactly as the image-blind read always was.
    """
    dictRead = agentCouncilCredentialStore.fdictReadCredentialDocument(
        fsResolveCredentialEvidencePath())
    if dictRead["sDamage"]:
        return _fdictDisable(
            f"the credential document on this computer is "
            f"{dictRead['sDamage']}; the runner backend stays disabled. "
            "It will be set aside (renamed, never deleted) the next time "
            "you consent to a credential test.",
            agentCouncilCredentialStore.S_STATE_DAMAGED)
    dictDocument = dictRead["dictDocument"]
    if sImageIdentity is None:
        return _fdictEvaluateAnyImage(dictDocument, sProvider)
    return _fdictEvaluateKnownImage(dictDocument, sProvider, sImageIdentity)


def _fdictEvaluateAnyImage(dictDocument, sProvider):
    """Answer for any image this provider is authorized in, image-blind."""
    listImages = agentCouncilCredentialStore.flistDescribeKeysForProvider(
        dictDocument, sProvider)
    dictLastRefusal = None
    for sImage in listImages:
        dictAnswer = _fdictEvaluateKnownImage(dictDocument, sProvider, sImage)
        if dictAnswer["bEnabled"]:
            return dictAnswer
        dictLastRefusal = dictAnswer
    if dictLastRefusal is not None:
        return dictLastRefusal
    return _fdictDisable(
        _fsAppendLegacyDetail(
            _DICT_STATE_REASONS[
                agentCouncilCredentialStore.S_STATE_NO_CONSENT],
            dictDocument, sProvider, None),
        agentCouncilCredentialStore.S_STATE_NO_CONSENT)


def _fsAppendLegacyDetail(sReason, dictDocument, sProvider, sImageIdentity):
    """Append why a manually written record does not enable this key."""
    sDetail = agentCouncilCredentialStore.fsExplainLegacyRefusals(
        dictDocument, sProvider, sImageIdentity)
    if not sDetail:
        return sReason
    return (f"{sReason} The manually recorded evidence on this computer "
            f"does not enable it: {sDetail}.")


def _fdictEvaluateKnownImage(dictDocument, sProvider, sImageIdentity):
    """Evaluate one (provider, image) key against a read document."""
    dictEvaluation = agentCouncilCredentialStore.fdictEvaluateCredentialKey(
        dictDocument, sProvider, sImageIdentity)
    if dictEvaluation["bAuthorized"]:
        return {"bEnabled": True, "sReason": "",
                "sState": dictEvaluation["sState"],
                "dictRecord": _fdictRecordForEnabledKey(dictEvaluation)}
    sReason = fsExplainCredentialState(dictEvaluation)
    if dictEvaluation["sState"] == (
            agentCouncilCredentialStore.S_STATE_NO_CONSENT):
        sReason = _fsAppendLegacyDetail(
            sReason, dictDocument, sProvider, sImageIdentity)
    return _fdictDisable(sReason, dictEvaluation["sState"])


def flistDescribeCredentialKeys():
    """Return one plain line per recorded (provider, image) key, read-only.

    For ``vaibify doctor``: consent, the latest outcome, and the project
    each test was run from. It reads the document and the job records
    and writes neither — a damaged document is described, never renamed.
    """
    from . import agentCouncilCredentialTestRecords
    from . import agentCouncilProviderRegistry
    dictRead = agentCouncilCredentialStore.fdictReadCredentialDocument(
        fsResolveCredentialEvidencePath())
    if dictRead["sDamage"]:
        return [f"the credential document is {dictRead['sDamage']}; it is "
                "set aside at the next consent"]
    dictProjectsByJob = {
        dictJob["sJobId"]: dictJob.get("sResourceName", "")
        for dictJob in agentCouncilCredentialTestRecords.flistReadAllJobRecords()}
    listLines = []
    for sProvider in sorted(agentCouncilProviderRegistry.SET_COUNCIL_PROVIDERS):
        for sImage in agentCouncilCredentialStore.flistDescribeKeysForProvider(
                dictRead["dictDocument"], sProvider):
            listLines.append(_fsDescribeOneKey(
                dictRead["dictDocument"], sProvider, sImage,
                dictProjectsByJob))
    return listLines


def _fsDescribeOneKey(dictDocument, sProvider, sImage, dictProjectsByJob):
    """Compose one key's doctor line."""
    dictEvaluation = agentCouncilCredentialStore.fdictEvaluateCredentialKey(
        dictDocument, sProvider, sImage)
    dictConsent = dictEvaluation["dictConsent"] or {}
    dictOutcome = dictEvaluation["dictOutcome"] or {}
    sOutcome = dictOutcome.get("sOutcome", "never tested")
    if dictOutcome:
        sOutcome += (f" {str(dictOutcome.get('sFinishedIso', ''))[:10]} "
                     f"({dictOutcome.get('sVerificationMethod', '')})")
    sProject = dictProjectsByJob.get(dictOutcome.get("sJobId", ""), "")
    return (f"{sProvider}, image {sImage[:19]}…: consent "
            f"{dictConsent.get('sState', 'not given')}; latest test "
            f"{sOutcome}" + (f", run from {sProject}" if sProject else "")
            + ("; ENABLED" if dictEvaluation["bAuthorized"] else ""))
