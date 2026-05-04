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
import logging
import urllib.error
import urllib.parse
import urllib.request

from vaibify.reproducibility.credentialRedactor import (
    fsRedactCredentials,
)
from vaibify.reproducibility.githubAuth import (
    fnValidateOwnerRepo,
    fsKeyringSlotFor,
    fsResolveToken,
)


_LOGGER = logging.getLogger(__name__)


__all__ = [
    "GithubMirrorError",
    "fdictFetchRemoteHashes",
    "fsRedactStderr",
]


_S_RAW_HOST = "https://raw.githubusercontent.com"
_I_HASH_BLOCK_SIZE = 65536


class GithubMirrorError(RuntimeError):
    """Raised when a GitHub mirror operation fails in a user-actionable way."""


def fsRedactStderr(sMessage):
    """Backward-compatible alias for :func:`fsRedactCredentials`.

    Existing callers (``scheduledReverify``, ``dependencyPinning``,
    ``syncDispatcher``, ``syncRoutes``) reference this function by
    name; the canonical implementation now lives in
    :mod:`credentialRedactor`. Kept as a thin re-export so callers
    don't need a coordinated rename.
    """
    return fsRedactCredentials(sMessage)


def _fbContainsPathTraversal(sSegment):
    """Return True when sSegment contains a ``..`` path-traversal token.

    Both literal ``..`` and percent-encoded variants are caught: a
    branch name like ``feature/../etc`` would otherwise build a URL
    that GitHub may interpret outside the intended branch.
    """
    sLower = sSegment.lower()
    if ".." in sSegment:
        return True
    return "%2e%2e" in sLower


def _fsBuildRawUrl(sOwner, sRepo, sBranch, sRelativePath):
    """Compose the raw.githubusercontent.com URL for a repo-relative path.

    Rejects any branch or relative-path segment containing ``..``;
    even though ``raw.githubusercontent.com`` is unlikely to honour
    such a request, defence-in-depth keeps the URL building logic
    from emitting an attacker-shaped path at all.
    """
    if _fbContainsPathTraversal(sBranch):
        raise ValueError(
            f"Invalid GitHub branch (path traversal): {sBranch!r}"
        )
    if _fbContainsPathTraversal(sRelativePath):
        raise ValueError(
            "Invalid relative path (path traversal): "
            f"{sRelativePath!r}"
        )
    sQuotedBranch = urllib.parse.quote(sBranch, safe="")
    sQuotedPath = urllib.parse.quote(sRelativePath, safe="/")
    return (
        f"{_S_RAW_HOST}/{sOwner}/{sRepo}/{sQuotedBranch}/{sQuotedPath}"
    )


_S_RAW_HOST_NETLOC = "raw.githubusercontent.com"
_F_REQUEST_TIMEOUT_SECONDS = 60.0


class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that strips ``Authorization`` from off-host redirects.

    Defends against the bearer-token leak class where a malicious
    GitHub redirect (or man-in-the-middle on a downgrade) sends the
    next request to an attacker-controlled host that logs incoming
    headers. We only retain the bearer token when the redirect target
    stays on ``raw.githubusercontent.com``; any other host gets the
    request without the credential.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        objectNew = super().redirect_request(
            req, fp, code, msg, headers, newurl,
        )
        if objectNew is None:
            return None
        sNetloc = urllib.parse.urlparse(newurl).netloc.lower()
        if sNetloc != _S_RAW_HOST_NETLOC:
            for sHeader in list(objectNew.headers.keys()):
                if sHeader.lower() == "authorization":
                    del objectNew.headers[sHeader]
        return objectNew


_OBJECT_OPENER = urllib.request.build_opener(
    _AuthStrippingRedirectHandler(),
)


def _fobjectOpenRequest(objectRequest, fTimeoutSeconds):
    """Open a request via the auth-stripping opener.

    Routed through ``urllib.request.urlopen`` only when the
    module-level ``urllib.request.urlopen`` reference has been
    monkeypatched (the existing test suite patches that exact name);
    in production the auth-stripping ``_OBJECT_OPENER`` is used so an
    off-host redirect cannot leak the bearer token.
    """
    fnUrlOpen = urllib.request.urlopen
    if fnUrlOpen is _S_REAL_URLOPEN:
        return _OBJECT_OPENER.open(
            objectRequest, timeout=fTimeoutSeconds,
        )
    return fnUrlOpen(objectRequest, timeout=fTimeoutSeconds)


_S_REAL_URLOPEN = urllib.request.urlopen


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
    """Translate an HTTPError into a redacted GithubMirrorError.

    Uses ``raise … from None`` so the chained context (which can
    contain the original urllib traceback referencing the bearer
    header object) does not surface in the user-facing message.
    """
    sSafeUrl = fsRedactStderr(sUrl)
    if _fbIsRateLimited(errorHttp):
        raise GithubMirrorError(
            "GitHub API rate limit exhausted while fetching "
            f"{sSafeUrl}. Wait until the limit resets or supply a "
            "token with higher quota."
        ) from None
    if errorHttp.code == 401:
        raise GithubMirrorError(
            "GitHub authentication failed (HTTP 401) while fetching "
            f"{sSafeUrl}. Verify the stored token is valid."
        ) from None
    raise GithubMirrorError(
        f"GitHub fetch failed for {sSafeUrl}: HTTP {errorHttp.code}."
    ) from None


def _fnRaiseClassifiedUrlError(errorUrl, sUrl):
    """Translate a URLError (network failure) into a redacted error."""
    sSafeUrl = fsRedactStderr(sUrl)
    sReason = fsRedactStderr(str(getattr(errorUrl, "reason", errorUrl)))
    raise GithubMirrorError(
        f"GitHub fetch failed for {sSafeUrl}: network error: {sReason}"
    ) from None


def _fsHashOneRemote(sUrl, sToken):
    """Return the SHA-256 hex digest of one remote file, or None on 404.

    Uses the shared opener whose redirect handler strips
    ``Authorization`` on off-host redirects, and applies a per-request
    timeout so a stalled GitHub mirror cannot hang the verification
    sweep indefinitely.
    """
    objectRequest = _fobjectBuildRequest(sUrl, sToken)
    try:
        with _fobjectOpenRequest(
            objectRequest, _F_REQUEST_TIMEOUT_SECONDS,
        ) as objectResponse:
            return _fsHashResponseStream(objectResponse)
    except urllib.error.HTTPError as errorHttp:
        if errorHttp.code == 404:
            return None
        _fnRaiseClassifiedHttpError(errorHttp, sUrl)
    except urllib.error.URLError as errorUrl:
        _fnRaiseClassifiedUrlError(errorUrl, sUrl)


def _fsResolveTokenSafely(sOwner, sRepo):
    """Return a token for the owner/repo pair, empty string on failure.

    Failures are logged at WARNING so an operator can see why a private
    repo has unexpectedly fallen back to anonymous (and hit the much
    lower 60/hr anonymous rate limit). The log records the exception's
    class name only, never the offending token shape.

    # Lesson: an empty token returned here means the next fetch goes
    # anonymously. On a private repo that yields 404 indistinguishably
    # from "file missing on a public repo". Accepted: vaibify only
    # reports the digest mapping, never the existence-vs-permission
    # status, so a downstream caller cannot infer privacy from the
    # mapping alone. The WARNING log alerts the operator that the
    # private-repo flow is silently degrading to public.
    """
    try:
        sSlot = fsKeyringSlotFor(sOwner, sRepo)
    except ValueError as errorSlot:
        _LOGGER.warning(
            "github token slot resolution failed (%s); "
            "falling back to anonymous fetch",
            type(errorSlot).__name__,
        )
        return ""
    try:
        return fsResolveToken(sSlot) or ""
    except Exception as errorToken:
        _LOGGER.warning(
            "github keyring lookup failed (%s); falling back to "
            "anonymous fetch — rate limits will be tighter",
            type(errorToken).__name__,
        )
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
