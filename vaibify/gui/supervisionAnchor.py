"""Host-side tamper anchor for the in-container supervision flag log.

Both attribution chains are prefix-valid: any prefix of a valid chain
is itself a valid chain. Deleting trailing flag records — or the whole
file, which then verifies "intact" with count zero — is therefore
invisible to the chain check. The workflow's persisted
``iUnattributedFlagCount`` narrows that hole but does not close it,
because ``project.json`` lives in the same container-writable
repository: an agent that truncates ``flags.jsonl`` and zeroes the
count leaves two artifacts that agree with each other.

This module closes the remaining half by putting the anchor somewhere
the supervised party cannot reach. Vaibify's threat model is an agent
confined to a container, acting on host-owned data; the host
filesystem is outside that confinement, and the hub already runs
there. So the hub records ``(iFlagCount, sHeadSha256)`` under
``~/.vaibify/supervision/`` after every observation, and a later
observation that shows *fewer* flags — or the same count with a
different head — is a contradiction the container cannot manufacture.

The anchor is monotonic by construction: it only ever advances. It is
evidence of tampering, never of cleanliness — a missing anchor (first
run, a new host, a cleared state directory) is simply unknown, and
unknown must not be graded as clean.
"""

__all__ = [
    "S_ANCHOR_DIRECTORY_NAME",
    "fsAnchorKeyFor",
    "fdictReadAnchor",
    "fnRecordAnchor",
    "fbAnchorContradictedBy",
]

import hashlib
import json
import os


S_ANCHOR_DIRECTORY_NAME = "supervision"

_S_COUNT_KEY = "iFlagCount"
_S_HEAD_KEY = "sHeadSha256"


def _fsAnchorDirectory():
    """Return the host directory holding supervision anchors."""
    return os.path.join(
        os.path.expanduser("~"), ".vaibify", S_ANCHOR_DIRECTORY_NAME,
    )


def fsAnchorKeyFor(sProjectRepoPath):
    """Return a filesystem-safe key for one project repository.

    Hashed rather than sanitized so an arbitrary repo path can never
    escape the anchor directory, and so the key never embeds a host
    path that would then be readable from the anchor file itself.

    The project repo path is the identity available on the workflow
    dict at every call site. Two *different* containers holding the
    same in-container path (``/workspace/SomeRepo``) therefore share an
    anchor. That collision is deliberate rather than unhandled: it can
    only ever raise a false tamper signal, never suppress a true one,
    so it fails loud. Narrowing the key needs a stable per-container
    identifier on the workflow dict, which does not exist today.
    """
    if not sProjectRepoPath:
        return ""
    return hashlib.sha256(
        sProjectRepoPath.encode("utf-8"),
    ).hexdigest()[:32]


def _fsAnchorPath(sKey):
    """Return the absolute path of one anchor file."""
    return os.path.join(_fsAnchorDirectory(), sKey + ".json")


def fdictReadAnchor(sKey):
    """Return the recorded anchor, or ``{}`` when none exists.

    An unreadable or malformed anchor reads as absent. That is the
    honest degradation: it yields "unknown", and the caller must not
    treat unknown as clean.
    """
    try:
        with open(_fsAnchorPath(sKey), "r") as fileAnchor:
            dictAnchor = json.load(fileAnchor)
    except (OSError, ValueError):
        return {}
    return dictAnchor if isinstance(dictAnchor, dict) else {}


def fnRecordAnchor(sKey, iFlagCount, sHeadSha256):
    """Advance the anchor; never lower it.

    Monotonicity is what makes the anchor evidence. Writing a smaller
    count would let a truncation launder itself into the anchor on the
    very next poll, which is precisely the attack this defends.
    """
    dictExisting = fdictReadAnchor(sKey)
    if int(dictExisting.get(_S_COUNT_KEY) or 0) > int(iFlagCount):
        return
    sDirectory = _fsAnchorDirectory()
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    sPath = _fsAnchorPath(sKey)
    sTemporary = sPath + ".tmp"
    with open(sTemporary, "w") as fileAnchor:
        json.dump(
            {_S_COUNT_KEY: int(iFlagCount), _S_HEAD_KEY: sHeadSha256},
            fileAnchor,
        )
    os.replace(sTemporary, sPath)


def fbAnchorContradictedBy(dictAnchor, listFlags, sHeadSha256):
    """Return True when the observed flags contradict the anchor.

    Two contradictions are detectable, and both are things a truncating
    or rewriting agent cannot avoid:

    * fewer flags than the host last saw — records were removed;
    * the same count under a different head digest — records were
      rewritten in place.

    A missing anchor is not a contradiction. It is unknown, and the
    caller decides what unknown means; this function never invents
    evidence it does not have.
    """
    if not dictAnchor:
        return False
    iAnchored = int(dictAnchor.get(_S_COUNT_KEY) or 0)
    if len(listFlags) < iAnchored:
        return True
    if len(listFlags) == iAnchored:
        return bool(dictAnchor.get(_S_HEAD_KEY)) and (
            dictAnchor.get(_S_HEAD_KEY) != sHeadSha256
        )
    return False
