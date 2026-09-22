"""The host-side leftovers one environment owns, and nothing else.

Deleting an environment removed its container, its volumes, its image
tags and its registry entry — and left two things on the host: the
kept build contexts under ``~/.vaibify/build/`` and the build-argument
hash under ``~/.vaibify/cache/``. A researcher who had deleted seven
environments still had all seven cached in ``cache/`` and four build
contexts for one of them (measured, 2026-09-21). Small in bytes; the
cost is a stale key that can make a later build reason from a dead
project's inputs.

**THIS MODULE'S ONLY JOB IS TO BE NARROW.** It is called from a
destructive path, so the interesting property is not what it removes
but what it refuses to. Three rules, and every one of them exists to
bound the blast radius to the named project:

1. **The build-context name must match EXACTLY.**
   ``fsStageBuildContext`` builds ``<project>-<8 mkdtemp characters>``,
   so a candidate is accepted only when the remainder after
   ``<project>-`` is exactly those eight characters from mkdtemp's own
   alphabet. A prefix match would be the bug this rule exists to stop:
   deleting ``fillet`` would sweep ``fillet-extra``'s contexts, because
   ``fillet-extra-2it2ks98`` starts with ``fillet-``.

2. **No path is removed that another REGISTERED project could own.**
   The belt to rule 1's braces: even if the naming rule were wrong, a
   directory whose name another registered project's prefix explains
   is left alone. A mistake here costs somebody else's environment,
   so the check is made from the registry rather than from reasoning.

3. **Nothing outside the two known directories is ever considered.**
   The roots are module constants, candidates are taken from a single
   non-recursive listing of each, and every returned path is verified
   to sit directly beneath its root. A name is never joined blindly.

The same ownership walk serves a second, non-destructive-path
disposition: RETENTION. A failed build keeps its context on purpose,
and nothing collected them, so a living project accrued one per
failure forever. ``flistPruneStagedContextsForProject`` keeps the
newest few and removes the rest — it is narrow for the same reason
and by the same route, because a retention rule that selected by
prefix or glob would reach a neighbour's contexts exactly as a sweep
would.

Orphans — residue whose project is no longer registered — are
DESCRIBED here and never removed. "Remove from list" un-registers a
project and leaves every byte where it was, by design, so a sweep
keyed on "not in the registry" would delete the build contexts of
exactly the projects a researcher chose to keep. Reporting them is
honest; reclaiming them is the researcher's call.
"""

__all__ = [
    "flistDescribeResidueForProject",
    "flistRemoveResidueForProject",
    "flistDescribeStagedContextsForProject",
    "flistPruneStagedContextsForProject",
    "fdictDescribeOrphanedResidue",
    "S_BUILD_CONTEXT_ROOT",
    "S_BUILD_HASH_ROOT",
]

import logging
import os
import re
import shutil

logger = logging.getLogger("vaibify")

S_BUILD_CONTEXT_ROOT = os.path.expanduser("~/.vaibify/build")
S_BUILD_HASH_ROOT = os.path.expanduser("~/.vaibify/cache")

# What ``tempfile.mkdtemp`` appends: exactly eight characters from its
# own alphabet. Pinned rather than inferred, because a looser pattern
# is how a prefix match sneaks back in.
_REGEX_MKDTEMP_SUFFIX = re.compile(r"^[A-Za-z0-9_]{8}$")

_S_HASH_SUFFIX = "-arg-hash"


def _fbNameIsAStagedContextOf(sEntryName, sProjectName):
    """Return True when a directory name is this project's staged context."""
    sPrefix = sProjectName + "-"
    if not sEntryName.startswith(sPrefix):
        return False
    return bool(_REGEX_MKDTEMP_SUFFIX.match(sEntryName[len(sPrefix):]))


def _fbAnotherProjectCouldOwn(sEntryName, sProjectName, listRegisteredNames):
    """Return True when a registered project other than this one explains it.

    The second line of defence. It answers the only question that
    matters on a destructive path: could this path belong to somebody
    else's environment? A longer registered name that also explains
    the entry wins, and the entry is left alone.
    """
    for sOtherName in listRegisteredNames or []:
        if sOtherName == sProjectName:
            continue
        if sEntryName == sOtherName + _S_HASH_SUFFIX:
            return True
        if _fbNameIsAStagedContextOf(sEntryName, sOtherName):
            return True
        # An entry whose name IS another registered project's is
        # ambiguous: ``build/demo-2it2ks98`` is a legal staged context
        # for ``demo`` and also the exact name of a project called
        # ``demo-2it2ks98``. Nothing in the name settles it, so the
        # destructive path takes the safe reading. The cost of being
        # wrong here is half a megabyte of stale context; the cost of
        # being wrong the other way is another environment's build
        # state.
        if sEntryName == sOtherName:
            return True
    return False


def _flistEntriesIn(sRoot):
    """Return the immediate entry names of a directory, or none."""
    try:
        return sorted(os.listdir(sRoot))
    except OSError:
        return []


def _flistOwnedPathsIn(sRoot, sProjectName, listRegisteredNames, fbBelongs):
    """Return the paths directly under one root this project alone owns.

    Every one of the module's three rules is applied here, so a new
    disposition cannot acquire a path by a looser route than the
    existing ones.
    """
    listPaths = []
    for sEntryName in _flistEntriesIn(sRoot):
        if not fbBelongs(sEntryName):
            continue
        if _fbAnotherProjectCouldOwn(
            sEntryName, sProjectName, listRegisteredNames,
        ):
            logger.warning(
                "Residue %s left alone: another registered project "
                "could own it.", sEntryName,
            )
            continue
        sPath = os.path.join(sRoot, sEntryName)
        # The third rule, enforced rather than assumed: a path that
        # does not sit directly beneath its root is not ours.
        if os.path.dirname(os.path.abspath(sPath)) != sRoot:
            continue
        listPaths.append(sPath)
    return listPaths


def flistDescribeStagedContextsForProject(
    sProjectName, listRegisteredNames=None,
):
    """Return this project's kept build contexts, under the build root."""
    if not sProjectName:
        return []
    return _flistOwnedPathsIn(
        S_BUILD_CONTEXT_ROOT, sProjectName, listRegisteredNames,
        lambda sName: _fbNameIsAStagedContextOf(sName, sProjectName),
    )


def flistDescribeResidueForProject(sProjectName, listRegisteredNames=None):
    """Return the absolute paths this project alone owns on the host.

    Read-only. The delete path calls this first and the reporting
    surfaces call it on its own, so what is removed and what is
    described can never disagree.
    """
    if not sProjectName:
        return []
    return flistDescribeStagedContextsForProject(
        sProjectName, listRegisteredNames,
    ) + _flistOwnedPathsIn(
        S_BUILD_HASH_ROOT, sProjectName, listRegisteredNames,
        lambda sName: sName == sProjectName + _S_HASH_SUFFIX,
    )


def flistRemoveResidueForProject(sProjectName, listRegisteredNames=None):
    """Remove this project's host-side residue; return what actually went.

    Failures are logged and skipped rather than raised: this runs at
    the end of a deletion that has already removed the container, the
    volumes and the images, and taking that outcome down over a
    leftover temporary directory would report a deletion that did not
    happen.
    """
    return _flistRemoveEach(flistDescribeResidueForProject(
        sProjectName, listRegisteredNames,
    ))


def _flistRemoveEach(listPaths):
    """Remove each path, logging and skipping what will not go."""
    listRemoved = []
    for sPath in listPaths:
        try:
            if os.path.isdir(sPath) and not os.path.islink(sPath):
                shutil.rmtree(sPath)
            else:
                os.remove(sPath)
            listRemoved.append(sPath)
        except OSError as error:
            logger.error("Could not remove residue %s: %s", sPath, error)
    return listRemoved


def _fdModifiedTimeOrZero(sPath):
    """Return a path's mtime, or 0 for one that cannot be stated."""
    try:
        return os.path.getmtime(sPath)
    except OSError:
        return 0.0


def flistPruneStagedContextsForProject(
    sProjectName, iKeepMostRecent, listRegisteredNames=None,
):
    """Keep this project's newest kept build contexts; remove the rest.

    A failed build deliberately keeps its context so the researcher can
    read what produced the failure, and nothing ever collected them: a
    living project accrued one per failure forever. Retention is a
    COUNT rather than an age, because what a researcher wants is the
    failure they just saw and the couple before it, whenever they
    happened.

    Narrow on exactly the terms the rest of this module is: candidates
    come from the same ownership walk, so a context this project cannot
    prove it owns is never a prune candidate, let alone a victim.
    """
    if iKeepMostRecent < 0:
        raise ValueError("iKeepMostRecent must not be negative")
    listContexts = sorted(
        flistDescribeStagedContextsForProject(
            sProjectName, listRegisteredNames),
        key=_fdModifiedTimeOrZero, reverse=True,
    )
    return _flistRemoveEach(listContexts[iKeepMostRecent:])


def fdictDescribeOrphanedResidue(listRegisteredNames):
    """Return ``{project name: [paths]}`` for residue nobody is registered for.

    NEVER removed from here, and the reason is a contract rather than
    caution: "Remove from list" un-registers a project and leaves every
    byte where it was, deliberately, so residue whose project is absent
    from the registry is exactly what a researcher may have chosen to
    keep. This describes it so a surface can offer the choice.
    """
    setRegistered = set(listRegisteredNames or [])
    dictOrphans = {}
    for sEntryName in _flistEntriesIn(S_BUILD_HASH_ROOT):
        if not sEntryName.endswith(_S_HASH_SUFFIX):
            continue
        sProjectName = sEntryName[: -len(_S_HASH_SUFFIX)]
        if sProjectName in setRegistered:
            continue
        dictOrphans.setdefault(sProjectName, []).append(
            os.path.join(S_BUILD_HASH_ROOT, sEntryName)
        )
    for sEntryName in _flistEntriesIn(S_BUILD_CONTEXT_ROOT):
        for sProjectName in list(dictOrphans):
            if _fbNameIsAStagedContextOf(sEntryName, sProjectName):
                dictOrphans[sProjectName].append(
                    os.path.join(S_BUILD_CONTEXT_ROOT, sEntryName)
                )
                break
    return dictOrphans
