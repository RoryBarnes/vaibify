"""Host-side GitHub authentication helpers for vaibify push flows.

Parallels ``overleafAuth`` but with a hybrid credential strategy:
by default we look up the namespaced keyring slot
``github_token:<owner>/<repo>`` and fall back to ``gh auth token`` when
no per-repo PAT is stored. The user's local ``gh`` login makes zero-
configuration pushes work on the developer's own machine; keyring is
the deployment path for automated or headless contexts.

All functions operate on the host; a token never reaches process
argv or environment variables — ``fsWriteAskpassScript`` produces a
mode-700 file that git consults synchronously when it needs
credentials.
"""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from vaibify.reproducibility.askpassHelper import fsWriteExecutableScript

__all__ = [
    "fnValidateOwnerRepo",
    "fsKeyringSlotFor",
    "fsKeyringSlotFromRemoteUrl",
    "ftParseOwnerRepoFromRemoteUrl",
    "fsWriteAskpassScript",
    "fsResolveToken",
    "fsResolveTokenLoginOrEmpty",
    "ftResolveTokenLogin",
    "fsKeyringSlotInUse",
    "fnAssertTokenOwnerBinding",
    "fnClearTokenLoginCache",
    "fdictRevokeGitHubToken",
]


_PATTERN_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

# ``gh_auth`` ignores the secret name entirely — it shells out to
# ``gh auth token`` — but ``secretManager.fsRetrieveSecret`` validates
# the name BEFORE dispatching on the method, so an empty name raises
# and the whole fallback becomes unreachable. This is the slot name
# ``registryRoutes`` already mounts the same credential under.
_S_GH_AUTH_SLOT_NAME = "gh_token"

_PATTERN_HTTPS_REMOTE = re.compile(
    r"^https?://[^/]+/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$"
)
_PATTERN_SSH_REMOTE = re.compile(
    r"^git@[^:]+:([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?$"
)


def fnValidateOwnerRepo(sOwner, sRepo):
    """Raise ValueError if owner or repo violate GitHub naming rules."""
    if not sOwner or not _PATTERN_SEGMENT.match(sOwner):
        raise ValueError(f"Invalid GitHub owner: {sOwner!r}")
    if not sRepo or not _PATTERN_SEGMENT.match(sRepo):
        raise ValueError(f"Invalid GitHub repository: {sRepo!r}")


def fsKeyringSlotFor(sOwner, sRepo):
    """Return the per-repo keyring key name for this owner/repo pair."""
    fnValidateOwnerRepo(sOwner, sRepo)
    return "github_token:" + sOwner + "/" + sRepo


def ftParseOwnerRepoFromRemoteUrl(sRemoteUrl):
    """Return (sOwner, sRepo) parsed from an HTTPS or SSH git URL, or ("","")."""
    if not sRemoteUrl:
        return ("", "")
    for pattern in (_PATTERN_HTTPS_REMOTE, _PATTERN_SSH_REMOTE):
        match = pattern.match(sRemoteUrl.strip())
        if match:
            return (match.group(1), match.group(2))
    return ("", "")


def fsKeyringSlotFromRemoteUrl(sRemoteUrl):
    """Derive a per-repo keyring key from an HTTPS or SSH git URL.

    Returns an empty string when the URL doesn't match either pattern;
    the caller can fall back to a catch-all slot or prompt the user.
    """
    sOwner, sRepo = ftParseOwnerRepoFromRemoteUrl(sRemoteUrl)
    if not sOwner or not sRepo:
        return ""
    try:
        return fsKeyringSlotFor(sOwner, sRepo)
    except ValueError:
        return ""


_S_ASKPASS_BODY_TEMPLATE = (
    "import subprocess\n"
    "import sys\n"
    "from vaibify.config.secretManager import (\n"
    "    fsRetrieveSecret, fbSecretExists,\n"
    ")\n"
    "sSlot = {sSlotRepr}\n"
    "sToken = ''\n"
    "if sSlot and fbSecretExists(sSlot, 'keyring'):\n"
    "    try:\n"
    "        sToken = fsRetrieveSecret(sSlot, 'keyring') or ''\n"
    "    except Exception:\n"
    "        sToken = ''\n"
    "if not sToken:\n"
    "    try:\n"
    "        sToken = fsRetrieveSecret({sGhAuthNameRepr}, 'gh_auth') or ''\n"
    "    except Exception:\n"
    "        sToken = ''\n"
    "print(sToken)\n"
)


def _fsBuildAskpassSource(sKeyringSlot):
    """Return python source for an askpass helper bound to one keyring slot.

    The helper tries the per-repo keyring slot first, then ``gh auth
    token`` as a fallback, then fails. GitHub accepts the token as
    both the username and the password, so a single lookup suffices.
    """
    sShebang = "#!" + sys.executable + "\n"
    sBody = _S_ASKPASS_BODY_TEMPLATE.format(
        sSlotRepr=repr(sKeyringSlot),
        sGhAuthNameRepr=repr(_S_GH_AUTH_SLOT_NAME),
    )
    return sShebang + sBody


def fsWriteAskpassScript(sKeyringSlot):
    """Write a mode-700 askpass script tied to sKeyringSlot; return path.

    An empty sKeyringSlot is valid — the helper still falls back to
    ``gh auth token`` in that case. The caller is responsible for
    deleting the file once the subprocess that reads it has exited.
    """
    sSource = _fsBuildAskpassSource(sKeyringSlot or "")
    return fsWriteExecutableScript(sSource, "vc_gh_askpass_")


def fsResolveToken(sKeyringSlot):
    """Return the GitHub token for sKeyringSlot, or empty on failure.

    Tries the namespaced keyring slot first, then ``gh auth token``.
    Never raises — the keyring probe is inside the guarded block
    because ``fbSecretExists`` validates the slot name and raises on a
    shape it dislikes. An empty return value tells the caller auth
    isn't available so it can surface a clear UI error instead of
    failing somewhere deep inside git.
    """
    from vaibify.config.secretManager import (
        fbSecretExists, fsRetrieveSecret,
    )
    try:
        if sKeyringSlot and fbSecretExists(sKeyringSlot, "keyring"):
            return fsRetrieveSecret(sKeyringSlot, "keyring") or ""
    except Exception:
        pass
    try:
        return fsRetrieveSecret(_S_GH_AUTH_SLOT_NAME, "gh_auth") or ""
    except Exception:
        return ""


_S_GITHUB_USER_URL = "https://api.github.com/user"
_F_TOKEN_LOGIN_TTL_SECONDS = 300.0
_dictTokenLoginCache = {}


def fnClearTokenLoginCache():
    """Clear the cached GitHub /user lookups (test hook)."""
    _dictTokenLoginCache.clear()


def _fsClassifyUserEndpointFailure(errorHttp):
    """Return the credential failure class an HTTP error from /user names.

    GitHub answers a bad token with 401 "Bad credentials" (recorded with
    a fake token, 2026-11-24). A spent rate limit is a 403 or 429 with
    ``X-RateLimit-Remaining: 0``, per GitHub's REST documentation, and is
    not a token problem. Any other 403 is the token lacking access (an
    organization's SSO, a scope); a 5xx is GitHub not answering.
    """
    from vaibify.config import credentialFailures
    dictHeaders = getattr(errorHttp, "headers", None) or {}
    if errorHttp.code == 401:
        return credentialFailures.S_CLASS_TOKEN_REJECTED
    if errorHttp.code == 429 or (
        errorHttp.code == 403
        and str(dictHeaders.get("X-RateLimit-Remaining", "")) == "0"
    ):
        return credentialFailures.S_CLASS_RATE_LIMITED
    if errorHttp.code == 403:
        return credentialFailures.S_CLASS_ACCESS_DENIED
    return credentialFailures.S_CLASS_NO_ANSWER


def _ftFetchLoginFresh(sToken):
    """Call GitHub /user with sToken; return ``(sLogin, sFailureClass)``.

    The second item is '' on success and otherwise a class from
    :mod:`vaibify.config.credentialFailures`.
    """
    requestUser = urllib.request.Request(
        _S_GITHUB_USER_URL,
        headers={
            "Authorization": "Bearer " + sToken,
            "Accept": "application/vnd.github+json",
            "User-Agent": "vaibify-token-binding-check",
        },
    )
    try:
        with urllib.request.urlopen(requestUser, timeout=10) as resp:
            dictBody = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as errorHttp:
        return ("", _fsClassifyUserEndpointFailure(errorHttp))
    except (urllib.error.URLError, OSError, ValueError):
        from vaibify.config import credentialFailures
        return ("", credentialFailures.S_CLASS_NO_ANSWER)
    return (str(dictBody.get("login") or ""), "")


def ftResolveTokenLogin(sToken):
    """Return ``(sLogin, sFailureClass)`` for sToken via /user, cached.

    Only a login is cached, for ~5 minutes, so repeated pushes don't
    hammer the API; a failure is asked again next time. Callers must
    treat an empty login as "unable to verify" and fail closed.
    """
    if not sToken:
        return ("", "")
    fNow = time.time()
    tCached = _dictTokenLoginCache.get(sToken)
    if tCached is not None and fNow - tCached[1] < _F_TOKEN_LOGIN_TTL_SECONDS:
        return (tCached[0], "")
    sLogin, sFailureClass = _ftFetchLoginFresh(sToken)
    if sLogin:
        _dictTokenLoginCache[sToken] = (sLogin, fNow)
    return (sLogin, sFailureClass)


def fsResolveTokenLoginOrEmpty(sToken):
    """Return the login for sToken via the /user endpoint, or ''."""
    return ftResolveTokenLogin(sToken)[0]


def fsKeyringSlotInUse(sOwner, sRepo):
    """Return the repository's slot when a token there outranks gh, else ''.

    :func:`fsResolveToken` reads that slot BEFORE ``gh auth token``, so
    a stale token in it silently overrides a working gh login; naming it
    is what lets a refusal say which credential to remove. Guarded like
    the resolver: a remote whose owner or repository cannot name a slot
    answers '', never an exception a push route would turn into a 500.
    """
    from vaibify.config.secretManager import fbSecretExists
    try:
        sKeyringSlot = fsKeyringSlotFor(sOwner, sRepo)
        if fbSecretExists(sKeyringSlot, "keyring"):
            return sKeyringSlot
    except Exception:
        return ""
    return ""


_S_GH_LOGIN_REMEDY = (
    "Run 'gh auth login' in a terminal on this computer; vaibify pushes "
    "with that login and stores no GitHub token of its own."
)


def _fsDescribeCredentialInUse(sKeyringSlotInUse):
    """Name the credential a refusal is about, and how to change it."""
    if sKeyringSlotInUse:
        return (
            f"the token stored for this repository in this computer's "
            f"keyring (slot {sKeyringSlotInUse}), which vaibify uses "
            f"instead of your gh login. Remove it with: vaibify credential "
            f"delete github --keyring-slot {sKeyringSlotInUse}"
        )
    return "your gh login. " + _S_GH_LOGIN_REMEDY


def _fsDescribeLoginFailure(sFailureClass, sKeyringSlotInUse):
    """Return the refusal sentence for a /user lookup that found no login."""
    from vaibify.config import credentialFailures as failures
    if sFailureClass == failures.S_CLASS_TOKEN_REJECTED:
        return (
            "GitHub rejected the credential vaibify would push with: "
            + _fsDescribeCredentialInUse(sKeyringSlotInUse)
        )
    if sFailureClass == failures.S_CLASS_RATE_LIMITED:
        return (
            "GitHub is limiting requests from this computer, so the "
            "credential's owner could not be checked. Try again after "
            "the limit resets (GitHub resets it within the hour); this is "
            "not a problem with the credential."
        )
    if sFailureClass == failures.S_CLASS_ACCESS_DENIED:
        return (
            "GitHub refused to say who the credential belongs to; it may "
            "lack access or need single sign-on authorized for an "
            "organization. It is " + _fsDescribeCredentialInUse(
                sKeyringSlotInUse)
        )
    return (
        "vaibify could not get an answer from GitHub to check who the "
        "credential belongs to: a network or service problem, not a "
        "problem with the credential. Try again."
    )


def _fsBindingFailureReason(sToken, sExpectedOwner, sKeyringSlotInUse=""):
    """Return the binding-failure reason string, or empty when OK."""
    if not sToken:
        return (
            "No GitHub login is available to vaibify on this computer. "
            + _S_GH_LOGIN_REMEDY
        )
    if not sExpectedOwner:
        return "Cannot bind token: remote owner is empty."
    sLogin, sFailureClass = ftResolveTokenLogin(sToken)
    if not sLogin:
        return _fsDescribeLoginFailure(sFailureClass, sKeyringSlotInUse)
    if sLogin.lower() != sExpectedOwner.lower():
        return (
            f"The GitHub token belongs to {sLogin}, but the remote "
            f"repository is owned by {sExpectedOwner}. vaibify pushes "
            f"only to repositories owned by the token's own account, so "
            f"pushing here needs a token for {sExpectedOwner}. A "
            f"repository owned by an organization cannot be pushed "
            f"from vaibify; push it from a terminal with your own git "
            f"credentials."
        )
    return ""


def fnAssertTokenOwnerBinding(sToken, sExpectedOwner, sKeyringSlotInUse=""):
    """Raise ValueError if sToken's GitHub login != sExpectedOwner.

    Empty token or unreachable /user endpoint also raises so the caller
    fails closed. Owner comparison is case-insensitive because GitHub
    treats logins that way in URL routing. ``sKeyringSlotInUse`` names
    the per-repository slot the token came from, when it did, so the
    refusal can say which credential to remove.
    """
    sReason = _fsBindingFailureReason(
        sToken, sExpectedOwner, sKeyringSlotInUse,
    )
    if sReason:
        raise ValueError(sReason)


def fdictRevokeGitHubToken(sKeyringSlot=""):
    """Clear ONE per-repository slot, or log out of gh; never both.

    With ``sKeyringSlot`` only that keyring slot is cleared and the gh
    login is left alone: the slot outranks gh (:func:`fsResolveToken`),
    so removing a stale slot is how a researcher falls back to a working
    gh login, and logging gh out at the same time would remove the very
    login they were falling back to (found by review, 2026-11-24).
    Without a slot, the gh login itself is logged out.

    Returns a dict describing what happened so the CLI can surface a
    precise status to the user:
      ``bUpstreamRevoked``  — True iff an upstream revocation call
                              succeeded (or ``gh auth logout`` returned 0).
      ``bLocalCleared``     — True iff the keyring slot was cleared.
      ``sMessage``          — Human-readable summary.

    Upstream PAT revocation requires a separate OAuth-app client id,
    which vaibify doesn't have. We therefore shell out to
    ``gh auth logout --hostname github.com`` for the gh-managed
    credential and log a no-op message for direct PATs.
    """
    if sKeyringSlot:
        bUpstream, sUpstreamMessage = (
            False, "Your gh login was left in place.",
        )
    else:
        bUpstream, sUpstreamMessage = _ftRevokeGitHubUpstream()
    bLocal, sLocalMessage = _ftClearLocalGithubCredential(sKeyringSlot)
    return {
        "bUpstreamRevoked": bUpstream,
        "bLocalCleared": bLocal,
        "sMessage": sUpstreamMessage + " " + sLocalMessage,
    }


def _ftRevokeGitHubUpstream():
    """Best-effort ``gh auth logout`` for the github.com host."""
    try:
        processResult = subprocess.run(
            ["gh", "auth", "logout", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=10, encoding="utf-8",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (False, "gh CLI unavailable; upstream not revoked.")
    if processResult.returncode == 0:
        return (True, "gh logout for github.com succeeded.")
    return (
        False,
        "Direct PATs cannot be revoked from this CLI; "
        "revoke at github.com/settings/tokens.",
    )


def _ftClearLocalGithubCredential(sKeyringSlot):
    """Delete the keyring slot for sKeyringSlot if any."""
    if not sKeyringSlot:
        return (False, "No keyring slot specified; nothing to clear.")
    from vaibify.config.credentialReplacement import fnDeleteSlotUnderLock
    try:
        fnDeleteSlotUnderLock(sKeyringSlot)
    except Exception as errorDelete:
        return (
            False,
            f"Local clear failed: {type(errorDelete).__name__}.",
        )
    return (True, f"Local keyring slot '{sKeyringSlot}' cleared.")
