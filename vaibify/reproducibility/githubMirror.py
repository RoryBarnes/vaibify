"""Host-side GitHub mirror for fetching remote file hashes.

This module is the GitHub analogue of :mod:`overleafMirror`, but the
GitHub case is materially simpler: there is no shallow git clone, no
case-collision quirk, and no per-project working directory. We just
issue authenticated HTTPS GETs against
``https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>``,
stream each response through :mod:`hashlib` to keep peak memory low,
and return a path-to-digest mapping.

Authenticated requests reuse the per-repo keyring slot from
:mod:`githubAuth`. Tokens never appear in argv, environment, or
exception messages. ``fsRedactStderr`` defends user-visible error
strings against accidentally-echoed credentials.
"""

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request

from vaibify.reproducibility.githubAuth import (
    fnValidateOwnerRepo,
    fsKeyringSlotFor,
    fsResolveToken,
)


__all__ = [
    "GithubMirrorError",
    "fdictFetchRemoteHashes",
    "fsRedactStderr",
]


_S_RAW_HOST = "https://raw.githubusercontent.com"
_I_HASH_BLOCK_SIZE = 65536

_REGEX_URL_WITH_CREDENTIALS = re.compile(
    r"https?://[^:@\s]+:[^@\s]+@",
)
_REGEX_BEARER_TOKEN = re.compile(
    r"(?i)(authorization|bearer|token)[\s:=]+\S+",
)
_REGEX_GITHUB_TOKEN = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b",
)
_LIST_SENSITIVE_KEYWORDS = (
    "password", "token", "bearer", "authorization",
)


class GithubMirrorError(RuntimeError):
    """Raised when a GitHub mirror operation fails in a user-actionable way."""


def fsRedactStderr(sMessage):
    """Return sMessage with URL credentials and token-like strings scrubbed.

    Defence in depth: any user-facing error message that may have
    captured a token (malformed URL, helper exception, etc.) is run
    through this filter before being raised or logged.
    """
    if not sMessage:
        return ""
    sRedacted = _REGEX_URL_WITH_CREDENTIALS.sub(
        "https://<redacted>@", sMessage,
    )
    listLines = [
        _fsRedactLineIfSensitive(sLine)
        for sLine in sRedacted.splitlines()
    ]
    sRedacted = "\n".join(listLines)
    sRedacted = _REGEX_GITHUB_TOKEN.sub("<redacted>", sRedacted)
    sRedacted = _REGEX_BEARER_TOKEN.sub("<redacted>", sRedacted)
    return sRedacted


def _fsRedactLineIfSensitive(sLine):
    """Return ``<redacted>`` when a line names a credential concept."""
    sLower = sLine.lower()
    for sKeyword in _LIST_SENSITIVE_KEYWORDS:
        if sKeyword in sLower:
            return "<redacted>"
    return sLine


def _fsBuildRawUrl(sOwner, sRepo, sBranch, sRelativePath):
    """Compose the raw.githubusercontent.com URL for a repo-relative path."""
    sQuotedBranch = urllib.parse.quote(sBranch, safe="")
    sQuotedPath = urllib.parse.quote(sRelativePath, safe="/")
    return (
        f"{_S_RAW_HOST}/{sOwner}/{sRepo}/{sQuotedBranch}/{sQuotedPath}"
    )


def _fobjectBuildRequest(sUrl, sToken):
    """Build a urllib Request with optional bearer-token authorization."""
    objectRequest = urllib.request.Request(sUrl, method="GET")
    objectRequest.add_header("Accept", "application/octet-stream")
    objectRequest.add_header("User-Agent", "vaibify-githubMirror")
    if sToken:
        objectRequest.add_header("Authorization", f"Bearer {sToken}")
    return objectRequest


def _fsHashResponseStream(objectResponse):
    """Stream the response body through SHA-256 and return the hex digest."""
    hasher = hashlib.sha256()
    while True:
        baBlock = objectResponse.read(_I_HASH_BLOCK_SIZE)
        if not baBlock:
            break
        hasher.update(baBlock)
    return hasher.hexdigest()


def _fbIsRateLimited(errorHttp):
    """Return True when an HTTPError indicates GitHub primary rate limiting."""
    if errorHttp.code != 403:
        return False
    sRemaining = ""
    try:
        sRemaining = errorHttp.headers.get("X-RateLimit-Remaining", "")
    except AttributeError:
        return False
    return (sRemaining or "").strip() == "0"


def _fnRaiseClassifiedHttpError(errorHttp, sUrl):
    """Translate an HTTPError into a redacted GithubMirrorError."""
    sSafeUrl = fsRedactStderr(sUrl)
    if _fbIsRateLimited(errorHttp):
        raise GithubMirrorError(
            "GitHub API rate limit exhausted while fetching "
            f"{sSafeUrl}. Wait until the limit resets or supply a "
            "token with higher quota."
        )
    if errorHttp.code == 401:
        raise GithubMirrorError(
            "GitHub authentication failed (HTTP 401) while fetching "
            f"{sSafeUrl}. Verify the stored token is valid."
        )
    raise GithubMirrorError(
        f"GitHub fetch failed for {sSafeUrl}: HTTP {errorHttp.code}."
    )


def _fnRaiseClassifiedUrlError(errorUrl, sUrl):
    """Translate a URLError (network failure) into a redacted error."""
    sSafeUrl = fsRedactStderr(sUrl)
    sReason = fsRedactStderr(str(getattr(errorUrl, "reason", errorUrl)))
    raise GithubMirrorError(
        f"GitHub fetch failed for {sSafeUrl}: network error: {sReason}"
    )


def _fsHashOneRemote(sUrl, sToken):
    """Return the SHA-256 hex digest of one remote file, or None on 404."""
    objectRequest = _fobjectBuildRequest(sUrl, sToken)
    try:
        with urllib.request.urlopen(objectRequest) as objectResponse:
            return _fsHashResponseStream(objectResponse)
    except urllib.error.HTTPError as errorHttp:
        if errorHttp.code == 404:
            return None
        _fnRaiseClassifiedHttpError(errorHttp, sUrl)
    except urllib.error.URLError as errorUrl:
        _fnRaiseClassifiedUrlError(errorUrl, sUrl)


def _fsResolveTokenSafely(sOwner, sRepo):
    """Return a token for the owner/repo pair, empty string on failure."""
    try:
        sSlot = fsKeyringSlotFor(sOwner, sRepo)
    except ValueError:
        return ""
    try:
        return fsResolveToken(sSlot) or ""
    except Exception:
        return ""


def fdictFetchRemoteHashes(sOwner, sRepo, sBranch, listRelativePaths):
    """Return ``{relpath: sha256_hex_or_None}`` for each requested file.

    Each entry in ``listRelativePaths`` is fetched from
    raw.githubusercontent.com. A 404 response records ``None`` for
    that path (the caller wants a complete picture of what is and
    isn't there). The returned mapping preserves the input order.
    Raises :class:`GithubMirrorError` on rate limits, auth failures,
    and network errors.
    """
    fnValidateOwnerRepo(sOwner, sRepo)
    if not listRelativePaths:
        return {}
    sToken = _fsResolveTokenSafely(sOwner, sRepo)
    dictResult = {}
    for sRelativePath in listRelativePaths:
        sUrl = _fsBuildRawUrl(sOwner, sRepo, sBranch, sRelativePath)
        dictResult[sRelativePath] = _fsHashOneRemote(sUrl, sToken)
    return dictResult
