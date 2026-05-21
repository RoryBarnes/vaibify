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
    "fnAssertTokenOwnerBinding",
    "fnClearTokenLoginCache",
    "fdictRevokeGitHubToken",
]


_PATTERN_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

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
    "        sToken = fsRetrieveSecret('', 'gh_auth') or ''\n"
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
    sBody = _S_ASKPASS_BODY_TEMPLATE.format(sSlotRepr=repr(sKeyringSlot))
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
    Never raises; an empty return value tells the caller auth isn't
    available so it can surface a clear UI error instead of failing
    somewhere deep inside git.
    """
    from vaibify.config.secretManager import (
        fbSecretExists, fsRetrieveSecret,
    )
    if sKeyringSlot and fbSecretExists(sKeyringSlot, "keyring"):
        try:
            return fsRetrieveSecret(sKeyringSlot, "keyring") or ""
        except Exception:
            pass
    try:
        return fsRetrieveSecret("", "gh_auth") or ""
    except Exception:
        return ""


_S_GITHUB_USER_URL = "https://api.github.com/user"
_F_TOKEN_LOGIN_TTL_SECONDS = 300.0
_dictTokenLoginCache = {}


def fnClearTokenLoginCache():
    """Clear the cached GitHub /user lookups (test hook)."""
    _dictTokenLoginCache.clear()


def _ftFetchLoginFresh(sToken):
    """Call GitHub /user with sToken and return (sLogin, sError)."""
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
        return ("", f"GitHub /user returned HTTP {errorHttp.code}")
    except urllib.error.URLError as errorUrl:
        return ("", f"GitHub /user unreachable: {errorUrl.reason}")
    except (ValueError, json.JSONDecodeError):
        return ("", "GitHub /user returned malformed JSON")
    return (str(dictBody.get("login") or ""), "")


def fsResolveTokenLoginOrEmpty(sToken):
    """Return the login for sToken via the /user endpoint, cached.

    Cache TTL is ~5 minutes so repeated pushes don't hammer the API.
    Returns empty string on any failure; callers must treat empty as
    "unable to verify" and fail closed.
    """
    if not sToken:
        return ""
    fNow = time.time()
    tCached = _dictTokenLoginCache.get(sToken)
    if tCached is not None and fNow - tCached[1] < _F_TOKEN_LOGIN_TTL_SECONDS:
        return tCached[0]
    sLogin, _sError = _ftFetchLoginFresh(sToken)
    if sLogin:
        _dictTokenLoginCache[sToken] = (sLogin, fNow)
    return sLogin


def _fsBindingFailureReason(sToken, sExpectedOwner):
    """Return the binding-failure reason string, or empty when OK."""
    if not sToken:
        return "No GitHub token available; configure one in Settings."
    if not sExpectedOwner:
        return "Cannot bind token: remote owner is empty."
    sLogin = fsResolveTokenLoginOrEmpty(sToken)
    if not sLogin:
        return "Could not verify token owner against GitHub /user."
    if sLogin.lower() != sExpectedOwner.lower():
        return (
            f"Token belongs to user {sLogin} but the remote is owned "
            f"by user {sExpectedOwner}. Configure a per-repo token "
            f"in Settings."
        )
    return ""


def fnAssertTokenOwnerBinding(sToken, sExpectedOwner):
    """Raise ValueError if sToken's GitHub login != sExpectedOwner.

    Empty token or unreachable /user endpoint also raises so the caller
    fails closed. Owner comparison is case-insensitive because GitHub
    treats logins that way in URL routing.
    """
    sReason = _fsBindingFailureReason(sToken, sExpectedOwner)
    if sReason:
        raise ValueError(sReason)


def fdictRevokeGitHubToken(sKeyringSlot=""):
    """Revoke a GitHub credential locally and announce upstream status.

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
        resultProcess = subprocess.run(
            ["gh", "auth", "logout", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (False, "gh CLI unavailable; upstream not revoked.")
    if resultProcess.returncode == 0:
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
    from vaibify.config.secretManager import fnDeleteSecret
    try:
        fnDeleteSecret(sKeyringSlot, "keyring")
    except Exception as errorDelete:
        return (
            False,
            f"Local clear failed: {type(errorDelete).__name__}.",
        )
    return (True, f"Local keyring slot '{sKeyringSlot}' cleared.")
