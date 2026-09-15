"""Does ``requirements.lock`` describe the image that will run it?

A lock is a claim about an environment. When it stops describing the
image the envelope pins, ``reproduce.sh`` installs the difference
before running -- so a "hermetic" rerun tests an environment neither
the lock nor the image describes, and the shadow refuses.

That refusal was the ONLY place this question was asked. The
Dependency-lock row stayed green throughout, because
``fbVerifyDependencyLock`` asks whether every entry carries a hash and
nothing about whether the entries are true; the readiness pre-flight
asked a DIFFERENT question (vaibify.yml's declared packages against
the repository mirror); and no criterion, row or arrow named it. A
researcher found out by spending a verification
(researcher-reported, 2026-09-15), and the diagnosis cost more than
the fix: the lock was five days older than the image, and one
"Regenerate now" settles it.

WHY THE DIFF LIVES HERE

The shadow had its own copy. Two derivations of "which packages
disagree" are two authorities on one question, and the drift between
this question and the pre-flight's different one is precisely what let
the mismatch through. One function, both callers.

THREE STATES, AND THE THIRD IS NOT A FAILURE

Answering needs the installed list, which needs an exec into the
image. Anywhere that cannot exec -- the poll, by contract -- the
answer is ``unknown``, and unknown must render as it always has rather
than as a fault: a row reddened by a question nobody asked is the
inverse of the bug this module exists for.
"""

__all__ = [
    "DICT_LAST_LOCK_SATISFACTION",
    "fbLockBlocksVerification",
    "fdictReadLockSatisfaction",
    "fnForgetLockSatisfaction",
    "fnRecordLockSatisfaction",
    "fsFingerprintLockState",
    "S_LOCK_CLEAN",
    "S_LOCK_MISMATCH",
    "S_LOCK_UNKNOWN",
    "S_LOCK_FILENAME",
    "fdictDescribeLockSatisfaction",
    "flistDescribeLockMismatch",
]


S_LOCK_CLEAN = "clean"
S_LOCK_MISMATCH = "mismatch"
S_LOCK_UNKNOWN = "unknown"

S_LOCK_FILENAME = "requirements.lock"

_S_REASON_STATE_MOVED = (
    "the lock or the running image changed since this was last asked"
)


def flistDescribeLockMismatch(dictLocked, dictInstalled):
    """Return one line per locked package the image does not satisfy.

    Directional on purpose: the lock is the claim and the image is the
    evidence, so every line reads "the lock says X, the image has Y".
    A package the image lacks entirely says so rather than showing an
    empty version, which read as a formatting fault the first time a
    researcher met it.

    Packages the image has and the lock does not are NOT reported. The
    lock is a floor for the rerun, and an image carrying extra
    packages still satisfies every line of it.
    """
    return [
        f"{sName}=={sVersion} (image has "
        f"{dictInstalled.get(sName) or 'nothing'})"
        for sName, sVersion in sorted((dictLocked or {}).items())
        if (dictInstalled or {}).get(sName) != sVersion
    ]


def fdictDescribeLockSatisfaction(
    dictLocked, dictInstalled, sUnknownReason="",
):
    """Return the three-state verdict the row and the pre-flight share.

    ``dictInstalled`` of ``None`` means the installed list could not be
    read at all, which is UNKNOWN -- distinct from an empty image,
    which would be a mismatch against any non-empty lock. Collapsing
    the two would let "vaibify could not look" arrive as "the image has
    nothing", the loudest possible wrong answer.
    """
    if dictInstalled is None:
        return {
            "sState": S_LOCK_UNKNOWN,
            "listMismatches": [],
            "sReason": sUnknownReason or "the installed packages "
                                         "could not be read",
        }
    listMismatches = flistDescribeLockMismatch(dictLocked, dictInstalled)
    return {
        "sState": S_LOCK_MISMATCH if listMismatches else S_LOCK_CLEAN,
        "listMismatches": listMismatches,
        "sReason": "",
    }


def fbLockBlocksVerification(dictVerdict, dictImageCurrency):
    """Return True iff this mismatch is evidence about the PINNED image.

    ONE truth table, read by the pre-flight checklist and by the "Do
    this next" arrow. Two surfaces answering one question from two
    derivations is the duplication this module was extracted to end,
    and the arrow drifted from the pre-flight the first time they were
    written apart.

    The verification re-runs the image the envelope PINS, and the
    measurement beside it was taken against the container the
    researcher is RUNNING (ruling of 2026-09-15: asking the pin means
    launching it, which is the cost the check exists to avoid). A
    verdict about one is evidence about the other only when they are
    known to be the same image, which is exactly
    ``bPinnedImageIsLive`` being ``True``. Anywhere else the
    pinned-image answer is UNKNOWN and nothing may be blocked on it:
    the shadow is authoritative and refuses with a good message of its
    own, whereas blocking here would refuse a rerun whose pinned image
    is fine.
    """
    if (dictVerdict or {}).get("sState") != S_LOCK_MISMATCH:
        return False
    return (dictImageCurrency or {}).get("bPinnedImageIsLive") is True


# The last answer per container, so the POLL can report a verdict it
# is forbidden to compute. Reading the installed list needs an exec
# and the poll may add none, so the check runs where execs are already
# permitted (the readiness route, and after a regeneration) and leaves
# its answer here. Absent means unknown, which is how every surface
# renders before the first check -- never as a fault.
DICT_LAST_LOCK_SATISFACTION = {}


def fsFingerprintLockState(filesRepo, sRunningImageIdentity):
    """Return the identity a cached verdict was measured against.

    BOTH halves are load-bearing, because the answer is a comparison
    of two things and either can move on its own: the lock is rewritten
    by a regeneration, and the running image is replaced by a rebuild
    or a switch. A lock-only fingerprint keeps answering "clean" about
    a container that has since been rebuilt -- and the running
    container is the very thing this check measures, so that is the
    stale answer that matters most.

    Costs no exec on the poll path: the lock is one of the envelope
    paths the poll snapshot already hashes. An unsampled or unreadable
    lock fingerprints as the empty digest, which differs from any real
    one and so downgrades rather than confirms.
    """
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    try:
        dictHashed = ffilesEnsureRepoFiles(filesRepo).fdictHashFiles(
            [S_LOCK_FILENAME],
        )
    except (OSError, ValueError, KeyError, NotImplementedError):
        dictHashed = {}
    sDigest = (dictHashed.get(S_LOCK_FILENAME) or {}).get("sSha256") or ""
    return f"{sDigest}|{sRunningImageIdentity or ''}"


def fnRecordLockSatisfaction(sContainerId, dictVerdict, sFingerprint=""):
    """Remember the last answer, stamped with the state it describes."""
    if not sContainerId:
        return
    if dictVerdict is None:
        DICT_LAST_LOCK_SATISFACTION.pop(sContainerId, None)
        return
    DICT_LAST_LOCK_SATISFACTION[sContainerId] = dict(
        dictVerdict, sFingerprint=sFingerprint,
    )


def fdictReadLockSatisfaction(sContainerId, sFingerprint=""):
    """Return the last answer, or UNKNOWN once the state it describes moved.

    The FINGERPRINT is the authority, not an invalidation call. A
    cache invalidated by hand fails silently the first time a new
    write path forgets to call the forgetter -- which is how this
    feature arrived with ``fnForgetLockSatisfaction`` and zero callers
    of it. Comparing costs no exec, so the poll can do it on every
    tick, and a verdict whose lock or whose running image has moved
    reports UNKNOWN rather than an answer about a state that no longer
    exists.
    """
    dictVerdict = DICT_LAST_LOCK_SATISFACTION.get(sContainerId)
    if not dictVerdict:
        return None
    if dictVerdict.get("sFingerprint", "") == sFingerprint:
        return dictVerdict
    return fdictDescribeLockSatisfaction({}, None, _S_REASON_STATE_MOVED)


def fnForgetLockSatisfaction(sContainerId):
    """Drop the cached answer; the envelope changed under it.

    DEFENCE IN DEPTH, never the guarantee: the fingerprint in
    :func:`fdictReadLockSatisfaction` is what makes a moved lock read
    as unknown, and it holds whether or not a write path remembers to
    call this.
    """
    DICT_LAST_LOCK_SATISFACTION.pop(sContainerId, None)
