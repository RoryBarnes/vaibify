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
]


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
