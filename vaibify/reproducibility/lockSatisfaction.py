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
    "fdictReadLockSatisfaction",
    "fnForgetLockSatisfaction",
    "fnRecordLockSatisfaction",
    "S_LOCK_CLEAN",
    "S_LOCK_MISMATCH",
    "S_LOCK_UNKNOWN",
    "fdictDescribeLockSatisfaction",
    "flistDescribeLockMismatch",
]


S_LOCK_CLEAN = "clean"
S_LOCK_MISMATCH = "mismatch"
S_LOCK_UNKNOWN = "unknown"


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


# The last answer per container, so the POLL can report a verdict it
# is forbidden to compute. Reading the installed list needs an exec
# and the poll may add none, so the check runs where execs are already
# permitted (the readiness route, and after a regeneration) and leaves
# its answer here. Absent means unknown, which is how every surface
# renders before the first check -- never as a fault.
DICT_LAST_LOCK_SATISFACTION = {}


def fnRecordLockSatisfaction(sContainerId, dictVerdict):
    """Remember the last lock-satisfaction answer for a container."""
    if sContainerId:
        DICT_LAST_LOCK_SATISFACTION[sContainerId] = dictVerdict


def fdictReadLockSatisfaction(sContainerId):
    """Return the last answer for a container, or ``None`` if never asked."""
    return DICT_LAST_LOCK_SATISFACTION.get(sContainerId)


def fnForgetLockSatisfaction(sContainerId):
    """Drop the cached answer; the envelope changed under it."""
    DICT_LAST_LOCK_SATISFACTION.pop(sContainerId, None)
