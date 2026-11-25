"""What went wrong with a credential, in classes the dashboard renders and never re-derives.

Every check that spends or stores a researcher's token -- the token
box's store, a remote binding's evidence, a push's GitHub owner check
-- answers a failure with one class from this module, a fixed sentence
naming the remedy, and the actions that can help. The classes exist
because a researcher acts differently on each: a missing or rejected
token is replaced, a rate limit is waited out, a token that works but
cannot reach a record needs the owning account's token, and a service
that did not answer needs only a retry. The review dialog once offered
"Retry" for a token that was simply missing and "Store your token" at
most once per dialog, which left a researcher whose first store failed
with no way forward and no word of why (researcher-reported,
2026-11-24).

Which actions a class earns is decided HERE, once; the page renders
``listActions`` as given. An unknown class raises rather than defaulting
to a guess.

Detail from a service (git's stderr, an HTTP body, an exception's text)
is passed through the shared redactor before it joins a sentence. The
fixed sentences themselves never are: the redactor replaces any line
that names a credential concept, so "the service rejected this token"
would come out as ``<redacted>``.
"""

from vaibify.reproducibility.credentialRedactor import fsRedactCredentials


__all__ = [
    "S_CLASS_TOKEN_MISSING",
    "S_CLASS_KEYRING_UNREADABLE",
    "S_CLASS_KEYRING_UNWRITABLE",
    "S_CLASS_TOKEN_REJECTED",
    "S_CLASS_ACCESS_DENIED",
    "S_CLASS_RATE_LIMITED",
    "S_CLASS_SERVICE_REFUSED",
    "S_CLASS_NO_ANSWER",
    "S_CLASS_INPUT_INVALID",
    "S_CLASS_SLOT_CHANGED",
    "S_ACTION_STORE",
    "S_ACTION_REPLACE",
    "S_ACTION_RETRY",
    "flistActionsForClass",
    "fsRedactServiceDetail",
    "fsScrubKnownSecret",
    "fdictDescribeFailure",
]


S_CLASS_TOKEN_MISSING = "token-missing"
S_CLASS_KEYRING_UNREADABLE = "keyring-unreadable"
S_CLASS_KEYRING_UNWRITABLE = "keyring-unwritable"
S_CLASS_TOKEN_REJECTED = "token-rejected"
S_CLASS_ACCESS_DENIED = "access-denied"
S_CLASS_RATE_LIMITED = "rate-limited"
S_CLASS_SERVICE_REFUSED = "service-refused"
S_CLASS_NO_ANSWER = "no-answer"
S_CLASS_INPUT_INVALID = "input-invalid"
S_CLASS_SLOT_CHANGED = "slot-changed"

S_ACTION_STORE = "store"
S_ACTION_REPLACE = "replace"
S_ACTION_RETRY = "retry"

_I_DETAIL_MAX_LENGTH = 200

# Replace is offered beside Retry where the evidence cannot tell a
# token problem from an access or plan problem (a token that works but
# cannot reach this record may belong to another account); it leads
# only where the token itself is known to be the problem.
_DICT_ACTIONS_FOR_CLASS = {
    S_CLASS_TOKEN_MISSING: (S_ACTION_STORE,),
    S_CLASS_KEYRING_UNREADABLE: (S_ACTION_RETRY,),
    S_CLASS_KEYRING_UNWRITABLE: (S_ACTION_RETRY,),
    S_CLASS_TOKEN_REJECTED: (S_ACTION_REPLACE,),
    S_CLASS_ACCESS_DENIED: (S_ACTION_RETRY, S_ACTION_REPLACE),
    S_CLASS_RATE_LIMITED: (S_ACTION_RETRY,),
    S_CLASS_SERVICE_REFUSED: (S_ACTION_RETRY, S_ACTION_REPLACE),
    S_CLASS_NO_ANSWER: (S_ACTION_RETRY,),
    S_CLASS_INPUT_INVALID: (),
    S_CLASS_SLOT_CHANGED: (S_ACTION_RETRY,),
}


def flistActionsForClass(sClass):
    """Return the actions a failure class offers, in the order to show them."""
    if sClass not in _DICT_ACTIONS_FOR_CLASS:
        raise KeyError(f"{sClass!r} is not a credential failure class")
    return list(_DICT_ACTIONS_FOR_CLASS[sClass])


def fsScrubKnownSecret(sText, sSecret):
    """Return ``sText`` with every copy of a secret the caller holds removed.

    The shared redactor recognizes credential SHAPES; a service that
    echoes an unusual token verbatim would pass it. Where the caller
    knows the exact value it sent, that value is removed outright.
    """
    if not sText or not sSecret:
        return sText or ""
    return str(sText).replace(sSecret, "<redacted>")


def fsRedactServiceDetail(sDetail):
    """Return a service's own words, redacted and bounded, or ''."""
    sRedacted = fsRedactCredentials(str(sDetail or "")).strip()
    if len(sRedacted) > _I_DETAIL_MAX_LENGTH:
        sRedacted = sRedacted[:_I_DETAIL_MAX_LENGTH].rstrip() + "..."
    return sRedacted


def fdictDescribeFailure(sClass, sSentence, sDetail=""):
    """Return ``{sClass, sMessage, listActions}`` for one failure.

    ``sSentence`` is a fixed sentence written for the researcher and is
    never redacted; ``sDetail`` is the service's own text, which always
    is. The detail is appended in parentheses when anything survives
    redaction.
    """
    listActions = flistActionsForClass(sClass)
    sRedactedDetail = fsRedactServiceDetail(sDetail)
    sMessage = sSentence
    if sRedactedDetail:
        sMessage = f"{sSentence} ({sRedactedDetail})"
    return {
        "sClass": sClass, "sMessage": sMessage, "listActions": listActions,
    }
