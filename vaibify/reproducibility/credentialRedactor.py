"""Shared credential-redaction helpers for user-facing error messages.

Single source of truth for scrubbing tokens, passwords, and other
secret material out of strings that might surface in logs, exception
messages, or GUI toasts. Previously the same scrubbing logic was
duplicated across :mod:`githubMirror`, :mod:`overleafMirror`, and
:mod:`zenodoClient` with subtly different rules; consolidating here
keeps the rules consistent and lets every caller benefit when a new
leak vector is discovered.

Two public functions:

* :func:`fsRedactCredentials` — the union of all three predecessors.
  Scrubs URL-embedded ``user:token@`` segments, GitHub PAT prefixes
  (``ghp_``, ``gho_``, ``ghu_``, ``ghs_``, ``ghr_``, ``github_pat_``),
  whole-line ``Authorization:`` / ``password:`` / ``token=`` patterns,
  generic ``access_token=...`` / ``api_key=...`` query-string-style
  parameters, and Bearer token grammar.
* :func:`fsRedactUrlCredentials` — URL-only form, used where only the
  URL itself (not its surrounding text) needs scrubbing.

All regular expressions are bounded (no nested quantifiers); a
ReDoS-style adversarial input runs in linear time.
"""

import re
from urllib.parse import urlparse, urlunparse


__all__ = [
    "fsRedactCredentials",
    "fsRedactUrlCredentials",
]


_S_REDACTED = "<redacted>"
_S_REDACTED_URL_PREFIX = "https://<redacted>@"

_REGEX_URL_WITH_CREDENTIALS = re.compile(
    # ``user:token@`` only counts when it sits between ``://`` and the
    # *first* ``/``, ``?``, ``#``, or whitespace — i.e. inside the URL
    # netloc. Permitting slashes in the user-info segment caused
    # benign query values like ``?path=user:thing@x`` to look like a
    # credential URL and over-redact downstream text.
    r"https?://[^:@\s/?#]+:[^@\s/?#]+@",
)
_REGEX_BEARER_TOKEN = re.compile(
    r"(?i)(authorization|bearer|token)[\s:=]+\S+",
)
_REGEX_GITHUB_TOKEN = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b",
)
_REGEX_GENERIC_TOKEN_PARAM = re.compile(
    r"(?i)\b(access_token|api_key|apikey|secret|password)"
    r"\s*=\s*[A-Za-z0-9_\-\.]+",
)
_LIST_SENSITIVE_KEYWORDS = (
    "password", "token", "bearer", "authorization",
)
_TUPLE_QUERY_PARAM_NAMES = ("access_token", "token")


def fsRedactCredentials(sMessage):
    """Return ``sMessage`` with all known credential shapes redacted.

    Operates in this order: URL-embedded credentials first (so the
    ``user:token@`` form is scrubbed before per-line keyword
    redaction), whole-line redaction for lines naming a credential
    keyword, then the generic token-parameter form, then known token
    shapes (GitHub PAT, Bearer-style tokens). Empty / ``None`` input
    returns ``""`` so callers can pass an unchecked stderr blob.
    """
    if not sMessage:
        return ""
    sRedacted = _REGEX_URL_WITH_CREDENTIALS.sub(
        _S_REDACTED_URL_PREFIX, sMessage,
    )
    sRedacted = _fsRedactSensitiveLines(sRedacted)
    sRedacted = _REGEX_GENERIC_TOKEN_PARAM.sub(
        lambda m: m.group(1) + "=" + _S_REDACTED, sRedacted,
    )
    sRedacted = _REGEX_GITHUB_TOKEN.sub(_S_REDACTED, sRedacted)
    sRedacted = _REGEX_BEARER_TOKEN.sub(_S_REDACTED, sRedacted)
    return sRedacted


def fsRedactUrlCredentials(sUrl):
    """Return ``sUrl`` with embedded credentials and token query params stripped.

    Strips both the ``user:token@`` segment of the netloc and any
    ``access_token=...`` / ``token=...`` query parameters. Non-URL
    inputs are returned unchanged.
    """
    if not sUrl:
        return ""
    sBase = _REGEX_URL_WITH_CREDENTIALS.sub(
        _S_REDACTED_URL_PREFIX, sUrl,
    )
    return _fsScrubUrlQueryParts(sBase)


def _fsRedactSensitiveLines(sMessage):
    """Replace any line naming a credential keyword with ``<redacted>``."""
    listLines = [
        _fsRedactLineIfSensitive(sLine)
        for sLine in sMessage.splitlines()
    ]
    return "\n".join(listLines)


def _fsRedactLineIfSensitive(sLine):
    """Return ``<redacted>`` when the line names a credential concept."""
    sLower = sLine.lower()
    for sKeyword in _LIST_SENSITIVE_KEYWORDS:
        if sKeyword in sLower:
            return _S_REDACTED
    return sLine


def _fsScrubUrlQueryParts(sCandidate):
    """If ``sCandidate`` parses as a URL, drop credential query params."""
    if "://" not in sCandidate:
        return sCandidate
    try:
        result = urlparse(sCandidate)
    except ValueError:
        return sCandidate
    if not result.scheme or not result.netloc:
        return sCandidate
    sQuery = _fsScrubQueryString(result.query)
    return urlunparse(result._replace(query=sQuery))


def _fsScrubQueryString(sQuery):
    """Remove access_token / token parameters from a URL query string."""
    if not sQuery:
        return sQuery
    listKept = []
    for sPair in sQuery.split("&"):
        sName = sPair.split("=", 1)[0].lower()
        if sName in _TUPLE_QUERY_PARAM_NAMES:
            continue
        listKept.append(sPair)
    return "&".join(listKept)
