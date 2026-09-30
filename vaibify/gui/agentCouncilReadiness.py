"""What the council button should do right now, as one named state.

``bAvailable`` answers exactly one question — can a council START now —
and that stays true to its name. It cannot say what a click should do
when the answer is no, and "no" has two very different shapes: a gate
the researcher can open from here (consent to a credential test; choose
to copy only the files git tracks) and a wall they cannot (a host
project, an image that cannot be resolved, a repository that does not
fit even when trimmed). ``sCouncilReadiness`` names which:

=====================  ========  ===============================
state                  button    a click opens
=====================  ========  ===============================
``ready``              enabled   the convene form
``needsSnapshotChoice``  enabled   the size modal
``needsCredentialTest``  enabled   the consent modal
``needsBoth``          enabled   the size modal, then consent
``blocked``            greyed    the explanation
=====================  ========  ===============================

The state is DERIVED from the facts the capabilities read already
gathered, never stored, and START re-validates every one of those facts
itself — so no state here can be used to bypass a gate by calling start
directly. The order at convene (plan section D) puts the free question
before the paid one: size and scope first, then consent and the test.
"""

__all__ = [
    "S_READY",
    "S_NEEDS_SNAPSHOT_CHOICE",
    "S_NEEDS_CREDENTIAL_TEST",
    "S_NEEDS_BOTH",
    "S_BLOCKED",
    "SET_BLOCKING_MARKERS",
    "fdictComposeCouncilReadiness",
    "fnApplyCouncilReadiness",
]

S_READY = "ready"
S_NEEDS_SNAPSHOT_CHOICE = "needsSnapshotChoice"
S_NEEDS_CREDENTIAL_TEST = "needsCredentialTest"
S_NEEDS_BOTH = "needsBoth"
S_BLOCKED = "blocked"

# Unavailability markers that no modal can resolve. The credential
# marker is deliberately absent: a shut credential gate is a consent the
# researcher can give, not a wall.
SET_BLOCKING_MARKERS = frozenset({
    "host-mode", "no-dominant-directory", "snapshot-too-large",
    "image-unresolvable",
})

S_NO_LOGIN_REASON = (
    "No provider is logged in inside this project's container, so there "
    "is no login a council could use. Log in from the project's "
    "terminal first (claude, codex login, or agy), then convene.")


def fdictComposeCouncilReadiness(bCredentialAuthorized, bAnyProviderHasLogin,
                                 bNeedsSnapshotChoice, sBlockingMarker,
                                 sBlockingReason):
    """Return ``{"sCouncilReadiness", "sReadinessReason"}`` from the facts.

    Pure: the five inputs are everything the table above depends on.
    A blocking marker wins over every need, because a researcher who
    consented and chose a scope would still meet the same wall at start.
    """
    if sBlockingMarker:
        return {"sCouncilReadiness": S_BLOCKED,
                "sReadinessReason": sBlockingReason}
    bNeedsCredentialTest = not bCredentialAuthorized
    if bNeedsCredentialTest and not bAnyProviderHasLogin:
        return {"sCouncilReadiness": S_BLOCKED,
                "sReadinessReason": S_NO_LOGIN_REASON}
    if bNeedsCredentialTest and bNeedsSnapshotChoice:
        sState = S_NEEDS_BOTH
    elif bNeedsCredentialTest:
        sState = S_NEEDS_CREDENTIAL_TEST
    elif bNeedsSnapshotChoice:
        sState = S_NEEDS_SNAPSHOT_CHOICE
    else:
        sState = S_READY
    return {"sCouncilReadiness": sState, "sReadinessReason": ""}


def fnApplyCouncilReadiness(dictCapabilities, bCredentialAuthorized):
    """Stamp the readiness onto a capabilities answer, in place.

    ``bCredentialAuthorized`` is the credential half measured BEFORE the
    snapshot pre-flight could overwrite ``bAvailable``. ``bAvailable`` is
    then re-stated as "ready", so it keeps meaning "can start now".
    """
    sMarker = dictCapabilities.get("sUnavailableIn", "")
    bBlocking = sMarker in SET_BLOCKING_MARKERS
    dictCapabilities.update(fdictComposeCouncilReadiness(
        bCredentialAuthorized,
        any(dictProvider.get("bHasProjectLogin")
            for dictProvider in dictCapabilities.get("listProviders", [])),
        bool(dictCapabilities.get("bNeedsSnapshotChoice")),
        sMarker if bBlocking else "",
        dictCapabilities.get("sReason", "") if bBlocking else ""))
    dictCapabilities["bAvailable"] = (
        dictCapabilities["sCouncilReadiness"] == S_READY)
