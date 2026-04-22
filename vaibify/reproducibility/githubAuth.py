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

import re
import sys

from vaibify.reproducibility.askpassHelper import fsWriteExecutableScript

__all__ = [
    "fnValidateOwnerRepo",
    "fsKeyringSlotFor",
    "fsKeyringSlotFromRemoteUrl",
    "fsWriteAskpassScript",
    "fsResolveToken",
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


def fsKeyringSlotFromRemoteUrl(sRemoteUrl):
    """Derive a per-repo keyring key from an HTTPS or SSH git URL.

    Returns an empty string when the URL doesn't match either pattern;
    the caller can fall back to a catch-all slot or prompt the user.
    """
    if not sRemoteUrl:
        return ""
    for pattern in (_PATTERN_HTTPS_REMOTE, _PATTERN_SSH_REMOTE):
        match = pattern.match(sRemoteUrl.strip())
        if match:
            sOwner, sRepo = match.group(1), match.group(2)
            try:
                return fsKeyringSlotFor(sOwner, sRepo)
            except ValueError:
                return ""
    return ""


def _fsBuildAskpassSource(sKeyringSlot):
    """Return python source for an askpass helper bound to one keyring slot.

    The helper tries the per-repo keyring slot first, then ``gh auth
    token`` as a fallback, then fails. GitHub accepts the token as
    both the username and the password, so a single lookup suffices.
    """
    return (
        "#!" + sys.executable + "\n"
        "import subprocess\n"
        "import sys\n"
        "from vaibify.config.secretManager import (\n"
        "    fsRetrieveSecret, fbSecretExists,\n"
        ")\n"
        "sSlot = " + repr(sKeyringSlot) + "\n"
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
