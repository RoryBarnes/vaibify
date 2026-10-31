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

One request is deliberately NOT authenticated: the public-access probe
(:func:`fdictProbePublicAccess`) asks GitHub what a READER is served,
and a reader has no token. Its code path builds its request without
one and never consults the keyring.
"""

import hashlib
import http.client
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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
    "S_ACCESS_NOT_PUBLIC",
    "S_ACCESS_PUBLIC",
    "fdictProbePublicAccess",
    "fnClearAnonymousBackoff",
    "fsResolveBranchHeadSha",
    "fdictFetchRemoteHashes",
    "fsRedactStderr",
]

# The two verdicts a probe can reach. Anything else is UNCHECKABLE and
# raises, because "vaibify could not ask" is not a claim about the
# repository.
S_ACCESS_PUBLIC = "public"
S_ACCESS_NOT_PUBLIC = "not-public"


_S_RAW_HOST = "https://raw.githubusercontent.com"
# Branch heads are resolved here, never read off the raw CDN: the API
# answers from the repository, the CDN answers from an edge cache.
_S_API_HOST = "https://api.github.com"
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


def _fhttpresponseOpenRequest(objectRequest, fTimeoutSeconds):
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


def _frequestBuildGithub(sUrl, sToken):
    """Build a urllib Request with optional bearer-token authorization."""
    objectRequest = urllib.request.Request(sUrl, method="GET")
    objectRequest.add_header("Accept", "application/octet-stream")
    objectRequest.add_header("User-Agent", "vaibify-githubMirror")
    if sToken:
        objectRequest.add_header("Authorization", f"Bearer {sToken}")
    return objectRequest


def _fsHashResponseStream(objectResponse):
    """Stream the response body through SHA-256 and return the hex digest."""
    from vaibify.reproducibility._hashing import fsHashFileObjectSha256
    return fsHashFileObjectSha256(objectResponse, _I_HASH_BLOCK_SIZE)


_I_ERROR_BODY_READ_BYTES = 4096


def _fsReadHeader(errorHttp, sName):
    """Return one response header of an HTTPError, "" when unreadable."""
    try:
        return str(errorHttp.headers.get(sName, "") or "").strip()
    except AttributeError:
        return ""


def _fsReadErrorBodySafely(errorHttp):
    """Return the start of an HTTPError's body, "" when it has none.

    KeyError is Python 3.9's answer for an HTTPError built with no body:
    its tempfile wrapper looks the missing file up in ``__dict__``.
    """
    try:
        baBody = errorHttp.read(_I_ERROR_BODY_READ_BYTES) or b""
    except (AttributeError, KeyError, OSError, ValueError):
        return ""
    return baBody.decode("utf-8", "replace")


def _fbIsRateLimited(errorHttp):
    """Return True when an HTTPError is one of GitHub's rate limits.

    The primary limit is a 403 with ``X-RateLimit-Remaining: 0``. The
    SECONDARY limits answer 403 or 429, and GitHub does not promise a
    ``retry-after`` on them (REST rate-limit documentation), so a 429
    is always a limit and a 403 is one when a header or the body says
    so. Recognizing only the primary limit reported a secondary one as
    a generic failure with no hint that waiting would fix it.
    """
    if errorHttp.code == 429:
        return True
    if errorHttp.code != 403:
        return False
    if _fsReadHeader(errorHttp, "X-RateLimit-Remaining") == "0":
        return True
    if _fsReadHeader(errorHttp, "Retry-After"):
        return True
    return "rate limit" in _fsReadErrorBodySafely(errorHttp).lower()


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
    objectRequest = _frequestBuildGithub(sUrl, sToken)
    try:
        with _fhttpresponseOpenRequest(
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
    raw.githubusercontent.com **at the branch's resolved commit
    sha**, never at the branch name. A branch-name raw URL is a CDN
    cache, and verifying a cache is not verifying the repository: one
    edge served a file's PREVIOUS commit fifteen hours after the push
    that replaced it, so a verify recorded "diverged" as fact about
    two files GitHub itself already held correctly — and the
    researcher's freshly-pushed envelope stayed red
    (researcher-reported, 2026-09-02). The branch head is resolved
    through api.github.com, which answers from the repository; a
    sha-addressed raw URL is immutable, so a stale edge cannot
    misreport it.

    A 404 response records ``None`` for that path (the caller wants a
    complete picture of what is and isn't there). The returned
    mapping preserves the input order. Raises
    :class:`GithubMirrorError` on rate limits, auth failures, network
    errors, and an unresolvable branch — an unpinned fetch would
    quietly reintroduce the cache comparison, so there is no
    fall-back to the branch name.
    """
    fnValidateOwnerRepo(sOwner, sRepo)
    if not listRelativePaths:
        return {}
    sToken = _fsResolveTokenSafely(sOwner, sRepo)
    sPinnedSha = fsResolveBranchHeadSha(sOwner, sRepo, sBranch, sToken)
    dictResult = {}
    for sRelativePath in listRelativePaths:
        sUrl = _fsBuildRawUrl(sOwner, sRepo, sPinnedSha, sRelativePath)
        dictResult[sRelativePath] = _fsHashOneRemote(sUrl, sToken)
    return dictResult


def fsResolveBranchHeadSha(sOwner, sRepo, sBranch, sToken):
    """Return the commit sha a branch currently points at.

    Asked of the GitHub API rather than the raw CDN, because the API
    answers from the repository. A caller already holding a 40-hex
    sha gets it back untouched — sha-addressed fetches need no
    resolution, and skipping the round trip keeps pinned callers
    working when the API is rate-limited.
    """
    sCandidate = (sBranch or "").strip()
    if len(sCandidate) == 40 and all(
        sCharacter in "0123456789abcdef" for sCharacter in sCandidate
    ):
        return sCandidate
    if _fbContainsPathTraversal(sCandidate):
        raise ValueError(
            f"Invalid GitHub branch (path traversal): {sBranch!r}"
        )
    sUrl = (
        f"{_S_API_HOST}/repos/{sOwner}/{sRepo}/commits/"
        + urllib.parse.quote(sCandidate, safe="")
    )
    requestCommit = _frequestBuildGithub(sUrl, sToken)
    # The sha media type answers the bare 40-hex commit id, so there
    # is no JSON to parse and nothing else to trust.
    requestCommit.add_header("Accept", "application/vnd.github.sha")
    try:
        with _fhttpresponseOpenRequest(
            requestCommit, _F_REQUEST_TIMEOUT_SECONDS,
        ) as httpresponseCommit:
            sSha = httpresponseCommit.read(64).decode(
                "ascii", "replace",
            ).strip()
    except urllib.error.HTTPError as errorHttp:
        _fnRaiseClassifiedHttpError(errorHttp, sUrl)
    except urllib.error.URLError as errorUrl:
        _fnRaiseClassifiedUrlError(errorUrl, sUrl)
    if len(sSha) != 40 or any(
        sCharacter not in "0123456789abcdef" for sCharacter in sSha
    ):
        raise GithubMirrorError(
            f"GitHub answered no commit sha for branch {sBranch!r} "
            f"of {sOwner}/{sRepo}; the verify cannot pin its ref."
        )
    return sSha


# ----------------------------------------------------------------------
# What a reader is served: the anonymous public-access probe
# ----------------------------------------------------------------------

# GitHub's budget for requests without a login is 60 an hour, shared by
# every client behind the same address (REST rate-limit documentation).
# So a refusal is a statement about EVERY probe this hub would send next,
# and it is remembered as a deadline. Never a sleep: the probe runs in a
# verify worker, and a sleeping worker holds its thread for nothing.
_F_BACKOFF_FALLBACK_SECONDS = 60.0
# A ceiling on any deadline GitHub's headers name. Its primary window is
# one hour, so a reset beyond that is a clock or header fault, and
# honouring it would leave every project uncheckable until a restart.
_F_BACKOFF_CEILING_SECONDS = 3600.0
# A repository's metadata is a few kilobytes; the cap only stops an
# unexpected body from being read into memory whole.
_I_PROBE_BODY_BYTE_CAP = 64 * 1024
_DICT_ANONYMOUS_BACKOFF = {"fDeadlineEpoch": 0.0}
_LOCK_ANONYMOUS_BACKOFF = threading.Lock()


def fnClearAnonymousBackoff():
    """Forget the anonymous-probe deadline (a fresh hub has none)."""
    with _LOCK_ANONYMOUS_BACKOFF:
        _DICT_ANONYMOUS_BACKOFF["fDeadlineEpoch"] = 0.0


def _ffAnonymousBackoffDeadline():
    """Return the epoch before which no anonymous probe may be sent."""
    with _LOCK_ANONYMOUS_BACKOFF:
        return _DICT_ANONYMOUS_BACKOFF["fDeadlineEpoch"]


def _fnExtendAnonymousBackoff(fDeadlineEpoch):
    """Move the deadline later, never earlier."""
    with _LOCK_ANONYMOUS_BACKOFF:
        _DICT_ANONYMOUS_BACKOFF["fDeadlineEpoch"] = max(
            _DICT_ANONYMOUS_BACKOFF["fDeadlineEpoch"], fDeadlineEpoch,
        )


def _ffParseNonNegativeNumber(sValue):
    """Return a header's non-negative number, or None when it is not one."""
    try:
        fValue = float(sValue)
    except (TypeError, ValueError):
        return None
    return fValue if fValue >= 0 else None


def _ffComputeBackoffDeadline(errorHttp, fNowEpoch):
    """Return when GitHub said the next request may go, as an epoch.

    GitHub's documented order: ``retry-after`` seconds when present;
    otherwise ``x-ratelimit-reset`` when the remaining count is 0;
    otherwise at least one minute.
    """
    fRetryAfter = _ffParseNonNegativeNumber(
        _fsReadHeader(errorHttp, "Retry-After"))
    fReset = _ffParseNonNegativeNumber(
        _fsReadHeader(errorHttp, "X-RateLimit-Reset"))
    bExhausted = _fsReadHeader(errorHttp, "X-RateLimit-Remaining") == "0"
    if fRetryAfter is not None:
        fDeadline = fNowEpoch + fRetryAfter
    elif bExhausted and fReset is not None:
        fDeadline = fReset
    else:
        fDeadline = fNowEpoch + _F_BACKOFF_FALLBACK_SECONDS
    return min(max(fDeadline, fNowEpoch),
               fNowEpoch + _F_BACKOFF_CEILING_SECONDS)


def _fsFormatDeadline(fDeadlineEpoch):
    """Return a deadline as a UTC clock time a researcher can read."""
    return datetime.fromtimestamp(
        fDeadlineEpoch, timezone.utc,
    ).strftime("%H:%M:%S UTC")


def _fsDescribeBackoff(sOwner, sRepo, fDeadlineEpoch):
    """Name why the public-access check cannot run, and until when."""
    return (
        f"GitHub asked vaibify to wait before checking whether "
        f"{sOwner}/{sRepo} is public: its limit for requests without a "
        "login is shared by everything on this network address. vaibify "
        f"asks again after {_fsFormatDeadline(fDeadlineEpoch)}; until "
        "then this check cannot be completed."
    )


def _frequestBuildAnonymousProbe(sUrl):
    """Build the probe's request. It takes no token, so it can carry none."""
    requestProbe = urllib.request.Request(sUrl, method="GET")
    requestProbe.add_header("Accept", "application/vnd.github+json")
    requestProbe.add_header("User-Agent", "vaibify-githubMirror")
    return requestProbe


def fdictProbePublicAccess(sOwner, sRepo):
    """Return ``{sState, sReason}``: does GitHub serve owner/repo to a READER?

    Level 2 asks what a reader can retrieve, and a reader has no login,
    so this sends one ``GET /repos/{owner}/{repo}`` with no
    ``Authorization`` header and consults no keyring. The verify's own
    hash fetch uses the researcher's token, which is why a PRIVATE
    repository whose files matched used to reach Level 2.

    ``public`` needs positive evidence: a 200 whose ``private`` is
    exactly false and whose ``full_name`` is the repository asked about.
    A 404 (private, or internal to an organization) and a 451 (withheld
    for legal reasons) are ``not-public``. Everything else -- a rate
    limit, a server error, a network failure, a body that does not say
    -- raises :class:`GithubMirrorError`, because "vaibify could not
    ask" is not a verdict. A 403 or 429 also sets the hub-wide deadline,
    before which every probe raises at once without sending anything.
    """
    fnValidateOwnerRepo(sOwner, sRepo)
    fDeadline = _ffAnonymousBackoffDeadline()
    if time.time() < fDeadline:
        raise GithubMirrorError(_fsDescribeBackoff(sOwner, sRepo, fDeadline))
    sUrl = f"{_S_API_HOST}/repos/{sOwner}/{sRepo}"
    try:
        with _fhttpresponseOpenRequest(
            _frequestBuildAnonymousProbe(sUrl), _F_REQUEST_TIMEOUT_SECONDS,
        ) as httpresponseProbe:
            baBody = httpresponseProbe.read(_I_PROBE_BODY_BYTE_CAP)
    except urllib.error.HTTPError as errorHttp:
        return _fdictJudgeProbeRefusal(errorHttp, sOwner, sRepo)
    except (OSError, http.client.HTTPException) as errorNetwork:
        sReason = fsRedactStderr(str(errorNetwork))
        raise GithubMirrorError(
            f"vaibify could not reach GitHub to check whether "
            f"{sOwner}/{sRepo} is public: {sReason}"
        ) from None
    return _fdictJudgeProbeBody(baBody, sOwner, sRepo)


def _fdictJudgeProbeRefusal(errorHttp, sOwner, sRepo):
    """Map a refused probe onto a verdict, or raise when it is none."""
    if errorHttp.code == 404:
        return {"sState": S_ACCESS_NOT_PUBLIC, "sReason": (
            f"GitHub serves {sOwner}/{sRepo} to your login but not to a "
            "reader: it is private, or internal to an organization. Make "
            "it public under Settings → General → Danger Zone → Change "
            "visibility, then verify again."
        )}
    if errorHttp.code == 451:
        return {"sState": S_ACCESS_NOT_PUBLIC, "sReason": (
            f"GitHub withholds {sOwner}/{sRepo} from readers for legal "
            "reasons (HTTP 451). Changing its visibility will not restore "
            "access; the notice on the repository's page gives the reason."
        )}
    if errorHttp.code in (403, 429):
        fDeadline = _ffComputeBackoffDeadline(errorHttp, time.time())
        _fnExtendAnonymousBackoff(fDeadline)
        raise GithubMirrorError(
            _fsDescribeBackoff(sOwner, sRepo, fDeadline)) from None
    raise GithubMirrorError(
        f"GitHub answered the check of whether {sOwner}/{sRepo} is public "
        f"with HTTP {errorHttp.code}, which says neither; verify again "
        "later."
    ) from None


def _fdictJudgeProbeBody(baBody, sOwner, sRepo):
    """Return ``public`` for an answer that proves it; raise otherwise."""
    try:
        dictBody = json.loads(baBody.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        dictBody = None
    if not isinstance(dictBody, dict) or dictBody.get("private") is not False:
        raise GithubMirrorError(
            f"GitHub's answer for {sOwner}/{sRepo} did not say whether it "
            "is public; verify again later."
        )
    sServedName = fsRedactStderr(str(dictBody.get("full_name") or ""))[:200]
    if sServedName.lower() != f"{sOwner}/{sRepo}".lower():
        raise GithubMirrorError(
            f"GitHub now serves {sOwner}/{sRepo} as {sServedName}. Push to "
            "the repository under its current name, then verify again; "
            "the check follows the push."
        )
    return {"sState": S_ACCESS_PUBLIC, "sReason": (
        f"GitHub serves {sOwner}/{sRepo} to readers without a login."
    )}
