"""Shared Overleaf authentication helpers used by host-side callers.

This module contains the small set of helpers that both the GUI sync
dispatcher (``vaibify.gui.syncDispatcher``) and the reproducibility
mirror (``vaibify.reproducibility.overleafMirror``) need in order to
talk to Overleaf's git bridge. Keeping them here — outside the GUI
package — avoids a reproducibility-to-GUI reverse dependency that
would otherwise tie host-side infrastructure to frontend concerns.

All functions operate on the host; none touch a container. Tokens are
never placed on process argv or environment variables: an askpass
helper script reads them from the OS keyring at subprocess time.
"""

import re
import sys

from vaibify.reproducibility.askpassHelper import fsWriteExecutableScript


__all__ = [
    "fnValidateOverleafProjectId",
    "fsWriteAskpassScript",
    "fdictRevokeOverleafToken",
]


_S_OVERLEAF_KEYRING_SLOT = "overleaf_token"


_PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def fnValidateOverleafProjectId(sProjectId):
    """Raise ValueError if the Overleaf project ID is malformed."""
    if not _PROJECT_ID_PATTERN.match(sProjectId or ""):
        raise ValueError(
            f"Invalid Overleaf project ID: {sProjectId}"
        )


def fsWriteAskpassScript():
    """Write a mode-700 askpass script that prints the stored token.

    Returns the on-disk path. The caller is responsible for removing
    the file after the git subprocess that reads it has exited. The
    script looks up the Overleaf token via the host keyring so the
    token itself never reaches process argv or environment variables.
    """
    return fsWriteExecutableScript(_fsBuildAskpassSource(), "vc_askpass_")


def _fsBuildAskpassSource():
    """Return the python source for the askpass helper script."""
    return (
        f"#!{sys.executable}\n"
        "import sys\n"
        "from vaibify.config.secretManager import fsRetrieveSecret\n"
        "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if 'Username' in prompt:\n"
        "    print('git')\n"
        "else:\n"
        "    print(fsRetrieveSecret('overleaf_token', 'keyring'))\n"
    )


def fdictRevokeOverleafToken():
    """Clear the Overleaf token from the local keyring.

    Overleaf personal access tokens currently have no machine-callable
    revocation endpoint, so the upstream side records a no-op message
    pointing the user at the web UI. Returns a dict shaped the same
    way as the GitHub and Zenodo revokers so the CLI can render a
    consistent status.
    """
    bLocal, sLocalMessage = _ftClearLocalOverleafCredential()
    return {
        "bUpstreamRevoked": False,
        "bLocalCleared": bLocal,
        "sMessage": (
            "Overleaf does not expose a revocation API; "
            "revoke at overleaf.com/user/account, then "
        ) + sLocalMessage,
    }


def _ftClearLocalOverleafCredential():
    """Delete the Overleaf keyring slot if it exists."""
    from vaibify.config.secretManager import fnDeleteSecret
    try:
        fnDeleteSecret(_S_OVERLEAF_KEYRING_SLOT, "keyring")
    except Exception as errorDelete:
        return (
            False,
            f"local clear failed ({type(errorDelete).__name__}).",
        )
    return (True, f"local slot '{_S_OVERLEAF_KEYRING_SLOT}' cleared.")
