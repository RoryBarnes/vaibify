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

import os
import re
import sys
import tempfile

from vaibify.reproducibility.askpassHelper import fsWriteExecutableScript


__all__ = [
    "S_OVERLEAF_KEYRING_SLOT",
    "fnValidateOverleafProjectId",
    "fbIsOverleafProjectId",
    "fsWriteAskpassScript",
    "fsWriteCandidateTokenFile",
    "fsWriteCandidateAskpassScript",
    "fsClassifyOverleafGitFailure",
    "fsClassifyStoredOverleafToken",
    "fdictDescribeOverleafFailure",
    "fsDescribeOverleafGitFailure",
    "fdictRevokeOverleafToken",
]


S_OVERLEAF_KEYRING_SLOT = "overleaf_token"
_S_OVERLEAF_KEYRING_SLOT = S_OVERLEAF_KEYRING_SLOT


_PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def fbIsOverleafProjectId(sProjectId):
    """Return True when the text has the shape of an Overleaf project ID."""
    return bool(_PROJECT_ID_PATTERN.match(sProjectId or ""))


def fnValidateOverleafProjectId(sProjectId):
    """Raise ValueError if the Overleaf project ID is malformed."""
    if not fbIsOverleafProjectId(sProjectId):
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


def fsWriteCandidateTokenFile(sCandidateToken):
    """Write a token not yet stored to a mode-600 file; return its path.

    The token box checks a token BEFORE it replaces the stored one, so
    git must be handed a token the keyring does not hold. It goes to a
    file in the ephemeral root (``~/.vaibify/tmp``, mode 0700), never to
    argv or the environment; the caller removes it in ``finally``, and
    an orphan left by a crash is collected by the ``ephemeralSecretFiles``
    reaper. Containers mount single named files from that root, never
    the directory, so no container can read it.
    """
    from vaibify.config.ephemeralStore import fsGetEphemeralRoot
    iDescriptor, sPath = tempfile.mkstemp(
        prefix="vc_candidate_", suffix=".token", dir=fsGetEphemeralRoot(),
    )
    try:
        os.write(iDescriptor, sCandidateToken.encode("utf-8"))
    finally:
        os.close(iDescriptor)
    return sPath


def fsWriteCandidateAskpassScript(sTokenFilePath):
    """Write a mode-700 askpass script that prints the candidate token file."""
    return fsWriteExecutableScript(
        _fsBuildCandidateAskpassSource(sTokenFilePath), "vc_askpass_",
    )


def _fsBuildCandidateAskpassSource(sTokenFilePath):
    """Return the askpass source that reads the candidate from its file."""
    return (
        f"#!{sys.executable}\n"
        "import sys\n"
        "prompt = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if 'Username' in prompt:\n"
        "    print('git')\n"
        "else:\n"
        f"    with open({sTokenFilePath!r}, encoding='utf-8') as fileToken:\n"
        "        print(fileToken.read().strip())\n"
    )


# What git prints, matched on the words Overleaf and git actually send.
# Both shapes were recorded on 2026-11-24 against git.overleaf.com with a
# fake token: a wrong token for a real-shaped ID exits 128 with "fatal:
# Authentication failed for '<url>'", and an ID naming no project exits
# 128 with "remote: This Overleaf project does not exist." and "fatal:
# repository '<url>' not found", before any token is asked for. The
# network and HTTP-status shapes are git's own for every remote. Anything
# else is a refusal whose cause the text does not settle (the account's
# access to the project, or Git integration on its plan), and the
# sentence says so instead of guessing.
_REGEX_OVERLEAF_AUTH_FAILED = re.compile(r"Authentication failed", re.I)
_REGEX_OVERLEAF_NO_SUCH_PROJECT = re.compile(
    r"project does not exist|repository '[^']*' not found", re.I,
)
_REGEX_GIT_RATE_LIMITED = re.compile(r"returned error: 429\b")
_REGEX_GIT_NO_ANSWER = re.compile(
    r"Could not resolve host|Failed to connect|timed out|Connection refused"
    r"|Connection reset|Network is unreachable|SSL|TLS"
    r"|returned error: 5\d\d\b",
    re.I,
)


def fsClassifyOverleafGitFailure(sStderr):
    """Return the credential failure class a failed Overleaf git call names."""
    from vaibify.config import credentialFailures
    sText = sStderr or ""
    if _REGEX_OVERLEAF_AUTH_FAILED.search(sText):
        return credentialFailures.S_CLASS_TOKEN_REJECTED
    if _REGEX_OVERLEAF_NO_SUCH_PROJECT.search(sText):
        return credentialFailures.S_CLASS_ACCESS_DENIED
    if _REGEX_GIT_RATE_LIMITED.search(sText):
        return credentialFailures.S_CLASS_RATE_LIMITED
    if _REGEX_GIT_NO_ANSWER.search(sText):
        return credentialFailures.S_CLASS_NO_ANSWER
    return credentialFailures.S_CLASS_SERVICE_REFUSED


def fsClassifyStoredOverleafToken():
    """Return '' when an Overleaf token is stored, else its failure class."""
    from vaibify.config import credentialFailures
    from vaibify.config import credentialReplacement, zenodoTokenSlots
    try:
        if credentialReplacement.fbSlotHoldsValue(S_OVERLEAF_KEYRING_SLOT):
            return ""
    except zenodoTokenSlots.TokenStoreUnreadable:
        return credentialFailures.S_CLASS_KEYRING_UNREADABLE
    return credentialFailures.S_CLASS_TOKEN_MISSING


_S_OVERLEAF_TOKEN_HELP = (
    "Overleaf's Git authentication token comes from overleaf.com: "
    "Account Settings, Git integration. It is not your login password, "
    "and one token serves every project on this computer."
)


def _fdictOverleafSentences():
    """Return the fixed sentence for each class an Overleaf check can end in."""
    from vaibify.config import credentialFailures as failures
    return {
        failures.S_CLASS_TOKEN_MISSING: (
            "No Overleaf token is stored on this computer. Store one. "
            + _S_OVERLEAF_TOKEN_HELP
        ),
        failures.S_CLASS_KEYRING_UNREADABLE: (
            "vaibify could not read this computer's keychain, so whether "
            "an Overleaf token is stored is unknown. Unlock the keychain, "
            "or allow vaibify to use it, and try again."
        ),
        failures.S_CLASS_KEYRING_UNWRITABLE: (
            "This computer's keychain did not confirm storing the Overleaf "
            "token, so it is not treated as stored. Unlock the keychain and "
            "try again."
        ),
        failures.S_CLASS_TOKEN_REJECTED: (
            "Overleaf rejected the token. Replace it with a new one. "
            + _S_OVERLEAF_TOKEN_HELP
        ),
        failures.S_CLASS_ACCESS_DENIED: (
            "Overleaf says no project with this ID exists. Check the "
            "project ID: the letters and digits after /project/ in the "
            "project's address on overleaf.com. If the ID is right, the "
            "token's account may not have access to the project; store "
            "the token of an account that does."
        ),
        failures.S_CLASS_SERVICE_REFUSED: (
            "Overleaf refused the request without rejecting the token "
            "itself. The account may not have access to this project, or "
            "its plan may not include Git integration; check the project's "
            "sharing on overleaf.com. If the project belongs to another "
            "account, store that account's token."
        ),
        failures.S_CLASS_RATE_LIMITED: (
            "Overleaf is limiting requests from this computer. Wait a few "
            "minutes and try again; this is not a problem with the token."
        ),
        failures.S_CLASS_NO_ANSWER: (
            "This computer could not get an answer from Overleaf: a network "
            "or service problem, not a problem with the token. Nothing "
            "about the token was decided; try again."
        ),
        failures.S_CLASS_INPUT_INVALID: (
            "That is not an Overleaf project ID. The ID is the letters and "
            "digits after /project/ in the project's address on overleaf.com."
        ),
        failures.S_CLASS_SLOT_CHANGED: (
            "The Overleaf token was changed by another project or window "
            "while this one was being checked, so this one was not stored. "
            "One token serves every project on this computer; check again "
            "to replace the newer one."
        ),
    }


def fdictDescribeOverleafFailure(sClass, sDetail=""):
    """Return ``{sClass, sMessage, listActions}`` for an Overleaf failure."""
    from vaibify.config import credentialFailures
    return credentialFailures.fdictDescribeFailure(
        sClass, _fdictOverleafSentences()[sClass], sDetail,
    )


_S_REPLACE_OVERLEAF_TOKEN_HERE = (
    " To replace the stored token, choose \u201cEdit Overleaf "
    "settings\u2026\u201d from an Overleaf badge's menu."
)


def fsDescribeOverleafGitFailure(sOutput):
    """Say why an Overleaf git call failed, and what to do, from git's words.

    The sentence comes from the failure class, never from the raw text,
    and git's tail rides along redacted. A rejected token also says where
    in the dashboard the stored token is replaced.
    """
    from vaibify.config import credentialFailures
    sClass = fsClassifyOverleafGitFailure(sOutput or "")
    sMessage = fdictDescribeOverleafFailure(
        sClass, (sOutput or "")[-400:],
    )["sMessage"]
    if sClass == credentialFailures.S_CLASS_TOKEN_REJECTED:
        sMessage += _S_REPLACE_OVERLEAF_TOKEN_HERE
    return sMessage


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
    """Delete the Overleaf keyring slot, under the slot's host-wide lock."""
    from vaibify.config.credentialReplacement import fnDeleteSlotUnderLock
    try:
        fnDeleteSlotUnderLock(_S_OVERLEAF_KEYRING_SLOT)
    except Exception as errorDelete:
        return (
            False,
            f"local clear failed ({type(errorDelete).__name__}).",
        )
    return (True, f"local slot '{_S_OVERLEAF_KEYRING_SLOT}' cleared.")
