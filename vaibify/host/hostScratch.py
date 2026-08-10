"""The host-diagnostics scratch subtree: where it is, and what retires it.

A host project has no container to throw away, so the working files a
run leaves behind — path lists, staged writes, environment captures,
the input a diagnostic tool was handed — accumulate in the researcher's
home directory until something removes them. This module owns that
subtree end to end: where it lives, how a project's corner of it is
keyed, and the sweeper that retires what is no longer reachable.

**One definition of the layout, deliberately.** The path guard admits
the subtree as one of its two permitted roots, and the sweeper deletes
inside it; a sweeper that derived the layout independently is the
divergence bug this repository's guidance names first — the two would
have to agree forever, and one day would not.

**The key is a digest of the canonical directory, never the display
name.** A researcher who deletes a project and registers a new one
under the same name must not inherit the old one's scratch space, and
the digest is filesystem-safe by construction where an arbitrary
project name is not.

**This sweeper is separate from ``ephemeralStore``'s, and must stay
separate.** That one handles only the immediate regular files in
``~/.vaibify/tmp`` and carries protected-path semantics for mounted
credential files whose deletion permanently bricks a container — a
startup sweep once deleted a live bind-mounted credential and left an
existing container unstartable. Making it recursive so it could reach
this subtree would put that protection in the path of a traversal it
was never designed for. This one goes the other way: it recurses, but
only inside ``host-diagnostics``, and it never follows a symlink out.

**Age is evidence here in a way it was not there.** Nothing in this
subtree is bind-mounted into anything, and nothing outside vaibify
reads it, so an operation directory older than the cutoff is
unreachable garbage rather than possibly-live state.
"""

__all__ = [
    "fsHostDiagnosticsRoot",
    "fsHostScratchRootForProject",
    "fsCreateOperationScratchDirectory",
    "fnSweepStaleHostScratch",
    "F_STALE_HOST_SCRATCH_AGE_SECONDS",
    "I_HOST_SCRATCH_BYTE_CAP",
]

import hashlib
import logging
import os
import shutil
import time


logger = logging.getLogger("vaibify")

_S_HOST_DIAGNOSTICS_ROOT = os.path.join(
    os.path.expanduser("~"), ".vaibify", "tmp", "host-diagnostics",
)

I_SCRATCH_DIRECTORY_MODE = 0o700

# Long enough to outlive any investigation a researcher is still in the
# middle of, matching the ephemeral store's cutoff so there is one
# number to reason about rather than two.
F_STALE_HOST_SCRATCH_AGE_SECONDS = 7 * 24 * 60 * 60

# A ceiling on what the subtree may hold regardless of age. The TTL
# alone bounds nothing over a week of heavy use, and this directory
# lives in the researcher's home: a diagnostic capture that grew is
# still a diagnostic capture, and the oldest one is the one they are
# least likely to be reading.
I_HOST_SCRATCH_BYTE_CAP = 256 * 1024 * 1024


def fsHostDiagnosticsRoot():
    """Return the host-diagnostics root path, without creating it."""
    return _S_HOST_DIAGNOSTICS_ROOT


def fsHostScratchRootForProject(sProjectRoot):
    """Return the project's scratch subtree, keyed by canonical identity."""
    sCanonicalIdentity = hashlib.sha256(
        os.path.realpath(sProjectRoot).encode("utf-8"),
    ).hexdigest()[:16]
    return os.path.join(_S_HOST_DIAGNOSTICS_ROOT, sCanonicalIdentity)


def fsCreateOperationScratchDirectory(sProjectRoot, sOperationId):
    """Create and return one operation's scratch directory, mode 0700.

    ``sOperationId`` is rejected unless it is a bare name. It reaches
    here from a journal record, and a value carrying a separator would
    place the directory outside the subtree the path guard admits and
    the sweeper retires — the one place a scratch path could escape
    both. ``.`` and ``..`` are bare names to ``basename`` and are
    refused by name: either would resolve to a directory this function
    did not intend to hand out.
    """
    if (
        not sOperationId
        or sOperationId in (os.curdir, os.pardir)
        or os.path.basename(sOperationId) != sOperationId
    ):
        raise ValueError(
            f"Operation id is not a bare name: {sOperationId!r}"
        )
    sDirectory = os.path.join(
        fsHostScratchRootForProject(sProjectRoot), sOperationId,
    )
    _fnEnsurePrivateDirectory(sDirectory)
    return sDirectory


def _fnEnsurePrivateDirectory(sPath):
    """Create a directory, and any missing ancestor, mode 0700.

    ``os.makedirs``'s ``mode`` applies to the LEAF only — every
    intermediate it creates lands at the default, which left the
    subtree's own root world-readable while its operation directories
    looked correct. Scratch holds staged writes and environment
    captures, so every level it creates is private. Recursion stops at
    the first ancestor that already exists: tightening a directory
    somebody else made is not this function's business, and the chmod
    after ``makedirs`` is there because ``umask`` strips bits from the
    mode argument.
    """
    if os.path.isdir(sPath):
        return
    sParent = os.path.dirname(sPath)
    if sParent and sParent != sPath:
        _fnEnsurePrivateDirectory(sParent)
    os.makedirs(sPath, mode=I_SCRATCH_DIRECTORY_MODE, exist_ok=True)
    os.chmod(sPath, I_SCRATCH_DIRECTORY_MODE)


def fnSweepStaleHostScratch(
    fMaxAgeSeconds=F_STALE_HOST_SCRATCH_AGE_SECONDS,
    iByteCap=I_HOST_SCRATCH_BYTE_CAP,
):
    """Retire stale operation directories, then enforce the size cap.

    Failures are swallowed, on the same terms as every other startup
    sweep: housekeeping must never be the reason a hub refuses to
    serve. Nothing outside ``host-diagnostics`` is touched, and nothing
    is followed through a symlink — an entry that is a link is unlinked
    where it stands, so a link planted inside the subtree can delete
    only itself.
    """
    try:
        listOperations = _flistDescribeOperationDirectories()
    except OSError as error:
        logger.debug("Host scratch sweep could not enumerate: %s", error)
        return
    fCutoff = time.time() - fMaxAgeSeconds
    listSurviving = [
        dictOperation for dictOperation in listOperations
        if not _fbRetireWhenStale(dictOperation, fCutoff)
    ]
    _fnEnforceByteCap(listSurviving, iByteCap)
    _fnRemoveEmptyProjectDirectories()


def _fbRetireWhenStale(dictOperation, fCutoff):
    """Remove one operation directory when it predates the cutoff."""
    if dictOperation["fModifiedEpoch"] >= fCutoff:
        return False
    _fnRemoveScratchEntry(dictOperation["sPath"])
    return True


def _fnEnforceByteCap(listOperations, iByteCap):
    """Remove the oldest surviving operations until the subtree fits."""
    iTotalBytes = sum(
        dictOperation["iBytes"] for dictOperation in listOperations
    )
    for dictOperation in sorted(
        listOperations, key=lambda dictEntry: dictEntry["fModifiedEpoch"],
    ):
        if iTotalBytes <= iByteCap:
            return
        _fnRemoveScratchEntry(dictOperation["sPath"])
        iTotalBytes -= dictOperation["iBytes"]


def _flistDescribeOperationDirectories():
    """Return one description per operation directory under the root.

    A symlink at either level is described as an entry in its own
    right rather than descended into, so the sweep retires the LINK on
    the link's own age and never reaches what it points at.
    """
    listOperations = []
    for sProjectPath in _flistScanPathsQuietly(_S_HOST_DIAGNOSTICS_ROOT):
        if not _fbIsRealDirectory(sProjectPath):
            listOperations.append(_fdictDescribeEntry(sProjectPath))
            continue
        for sOperationPath in _flistScanPathsQuietly(sProjectPath):
            listOperations.append(_fdictDescribeEntry(sOperationPath))
    return listOperations


def _fbIsRealDirectory(sPath):
    """Return True for a directory that is not a symlink to one.

    The whole symlink discipline of this module is this one line.
    ``os.path.isdir`` follows links, so on its own it calls a link to
    somebody else's project a directory — and every traversal and
    removal below would then treat it as one.
    """
    return os.path.isdir(sPath) and not os.path.islink(sPath)


def _fdictDescribeEntry(sPath):
    """Return one scratch entry's path, age and size, following nothing."""
    try:
        tStat = os.lstat(sPath)
    except OSError:
        return {"sPath": sPath, "fModifiedEpoch": 0.0, "iBytes": 0}
    iBytes = (
        _fiMeasureDirectoryBytes(sPath)
        if _fbIsRealDirectory(sPath) else tStat.st_size
    )
    return {
        "sPath": sPath,
        "fModifiedEpoch": tStat.st_mtime,
        "iBytes": iBytes,
    }


def _fiMeasureDirectoryBytes(sDirectory):
    """Return the bytes held under a directory, following no symlink."""
    iBytes = 0
    for sChildPath in _flistScanPathsQuietly(sDirectory):
        if _fbIsRealDirectory(sChildPath):
            iBytes += _fiMeasureDirectoryBytes(sChildPath)
            continue
        try:
            iBytes += os.lstat(sChildPath).st_size
        except OSError:
            continue
    return iBytes


def _flistScanPathsQuietly(sDirectory):
    """Return the full paths directly inside a directory, or ``[]``."""
    try:
        return [
            os.path.join(sDirectory, sName)
            for sName in os.listdir(sDirectory)
        ]
    except OSError:
        return []


def _fnRemoveScratchEntry(sPath):
    """Remove one scratch entry, unlinking a symlink rather than walking it.

    ``shutil.rmtree`` refuses a symlinked root and unlinks (never
    follows) the links it meets inside, so the only decision this
    function has to get right is the one at the top.
    """
    try:
        if not _fbIsRealDirectory(sPath):
            os.unlink(sPath)
            return
        shutil.rmtree(sPath, ignore_errors=True)
    except OSError as error:
        logger.debug("Host scratch sweep could not remove %s: %s",
                     sPath, error)


def _fnRemoveEmptyProjectDirectories():
    """Drop project directories the sweep emptied, leaving no husks."""
    for sProjectPath in _flistScanPathsQuietly(_S_HOST_DIAGNOSTICS_ROOT):
        if not _fbIsRealDirectory(sProjectPath):
            continue
        try:
            os.rmdir(sProjectPath)
        except OSError:
            continue
