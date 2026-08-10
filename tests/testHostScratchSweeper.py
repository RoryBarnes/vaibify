"""The host-diagnostics sweeper, and the things it must never reach.

A sweeper is the one piece of housekeeping that can do permanent harm
by working correctly on the wrong tree. This repository has already
lost a container to one: a startup sweep deleted a "stale" credential
file that had been bind-mounted for months, and Docker then created a
directory stub where the file was. The lesson recorded from it — age is
not evidence that a host file is garbage, reachability is — is why
``ephemeralStore``'s sweep is file-only and carries protected paths,
and why this one is a SEPARATE traversal over a subtree that nothing
mounts and nothing outside vaibify reads.

So the tests below are as much about the boundary as the deletion:

* a planted symlink pointing OUT of the subtree is unlinked where it
  stands and its target survives — the sweep must not be a way to
  delete an arbitrary file by putting a link where the sweeper looks;
* files beside the subtree, in ``~/.vaibify/tmp`` itself, are never
  touched — that is the credential store's ground, and this sweep has
  no business there;
* recent work survives, because a sweeper that deletes what the
  researcher is still using is worse than one that never runs.
"""

import os
import time

import pytest

from vaibify.host import hostScratch


F_ONE_DAY_SECONDS = 24 * 60 * 60


@pytest.fixture(autouse=True)
def fixtureIsolateTheSubtree(tmp_path, monkeypatch):
    """Point the subtree at tmp_path so no real scratch is at risk."""
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "vaibifyTmp" / "host-diagnostics"),
    )


def _fsMakeOperationDirectory(sProjectRoot, sOperationId, fAgeSeconds=0.0):
    """Create one operation directory holding a file, aged if asked."""
    sDirectory = hostScratch.fsCreateOperationScratchDirectory(
        sProjectRoot, sOperationId,
    )
    sFilePath = os.path.join(sDirectory, "capture.txt")
    with open(sFilePath, "w") as fileCapture:
        fileCapture.write("diagnostic output")
    if fAgeSeconds:
        fWhen = time.time() - fAgeSeconds
        os.utime(sFilePath, (fWhen, fWhen))
        os.utime(sDirectory, (fWhen, fWhen))
    return sDirectory


@pytest.mark.falsification
def testStaleOperationDirectoriesGoAndRecentOnesStay(tmp_path):
    """The TTL, in both directions, in one traversal.

    Kills: sweeping unconditionally, which deletes the capture a
    researcher is in the middle of reading.
    """
    sProjectRoot = str(tmp_path / "project")
    sStale = _fsMakeOperationDirectory(
        sProjectRoot, "opStale", fAgeSeconds=30 * F_ONE_DAY_SECONDS,
    )
    sFresh = _fsMakeOperationDirectory(sProjectRoot, "opFresh")
    hostScratch.fnSweepStaleHostScratch()
    assert not os.path.exists(sStale), "a stale operation directory survived"
    assert os.path.isdir(sFresh), (
        "a directory written moments ago was swept"
    )


@pytest.mark.falsification
def testAPlantedSymlinkIsUnlinkedAndItsTargetSurvives(tmp_path):
    """The sweep is not a way to delete an arbitrary file.

    A link inside the subtree, aged past the cutoff, pointing at
    something precious outside it. A traversal that followed the link
    would delete the target; this one deletes the link.

    Kills: describing or removing entries with symlinks followed —
    ``stat()`` instead of ``lstat()``, or ``rmtree`` on a path checked
    with ``isdir`` alone.
    """
    sProjectRoot = str(tmp_path / "project")
    pathPrecious = tmp_path / "somebodyElsesData.txt"
    pathPrecious.write_text("not the sweeper's to delete")
    sScratchRoot = hostScratch.fsHostScratchRootForProject(sProjectRoot)
    os.makedirs(sScratchRoot, mode=0o700, exist_ok=True)
    sLinkPath = os.path.join(sScratchRoot, "opLink")
    os.symlink(str(pathPrecious), sLinkPath)
    fWhen = time.time() - 30 * F_ONE_DAY_SECONDS
    os.utime(sLinkPath, (fWhen, fWhen), follow_symlinks=False)

    hostScratch.fnSweepStaleHostScratch()

    assert pathPrecious.exists(), (
        "the sweeper followed a planted symlink and deleted its target"
    )
    assert not os.path.lexists(sLinkPath), "the stale link itself survived"


@pytest.mark.falsification
def testAPlantedSymlinkToADirectoryIsUnlinkedNotWalked(tmp_path):
    """The link-to-a-DIRECTORY case, which the file case cannot see.

    ``os.path.isdir`` follows links, so a stale link pointing at a
    directory is a directory as far as the obvious check is concerned.
    Deciding on that alone hands the link to ``shutil.rmtree``, which
    refuses a symlinked root — with errors ignored, the entry is
    silently never retired and the subtree keeps a link to somebody
    else's data forever.

    Kills: dropping the ``islink`` half of the removal's decision.
    """
    sProjectRoot = str(tmp_path / "project")
    pathPreciousDirectory = tmp_path / "somebodyElsesProject"
    pathPreciousDirectory.mkdir()
    (pathPreciousDirectory / "results.csv").write_text("years of work")
    sScratchRoot = hostScratch.fsHostScratchRootForProject(sProjectRoot)
    os.makedirs(sScratchRoot, mode=0o700, exist_ok=True)
    sLinkPath = os.path.join(sScratchRoot, "opDirectoryLink")
    os.symlink(str(pathPreciousDirectory), sLinkPath)
    fWhen = time.time() - 30 * F_ONE_DAY_SECONDS
    os.utime(sLinkPath, (fWhen, fWhen), follow_symlinks=False)

    hostScratch.fnSweepStaleHostScratch()

    assert (pathPreciousDirectory / "results.csv").exists(), (
        "the sweeper walked a link to a directory it does not own"
    )
    assert not os.path.lexists(sLinkPath), (
        "a stale link to a directory was left in place forever"
    )


def testADirectoryWhoseContentsLinkOutwardLosesOnlyTheLink(tmp_path):
    """The same guarantee one level down, where rmtree does the walking.

    Carries no falsification mark on purpose. What it pins is
    ``shutil.rmtree``'s own contract — it unlinks the links it meets
    rather than following them — and there is no guard of ours here to
    mutate. Registering a mutation would mean inventing one, and an
    entry whose mutant nothing in this module could plausibly contain
    reports a defence that does not exist. The test still earns its
    place: this module leans on that contract, and a future removal
    written by hand would break it silently.
    """
    sProjectRoot = str(tmp_path / "project")
    pathPrecious = tmp_path / "alsoNotYours.txt"
    pathPrecious.write_text("still not the sweeper's")
    sDirectory = _fsMakeOperationDirectory(
        sProjectRoot, "opWithLink", fAgeSeconds=30 * F_ONE_DAY_SECONDS,
    )
    os.symlink(
        str(pathPrecious), os.path.join(sDirectory, "escapeHatch"),
    )
    fWhen = time.time() - 30 * F_ONE_DAY_SECONDS
    os.utime(sDirectory, (fWhen, fWhen))

    hostScratch.fnSweepStaleHostScratch()

    assert not os.path.exists(sDirectory)
    assert pathPrecious.exists(), (
        "removing a stale directory followed a link out of the subtree"
    )


@pytest.mark.falsification
def testTheCredentialStoreBesideTheSubtreeIsNeverTouched(tmp_path):
    """``~/.vaibify/tmp`` is the other sweep's ground, and stays so.

    A mounted secret there outlives any number of hub restarts, and
    deleting one leaves its container permanently unstartable. This
    sweep recurses, which the other one deliberately does not, so the
    thing to prove is that its recursion starts BELOW the shared
    parent rather than at it.

    Kills: rooting the traversal at the ephemeral store instead of the
    host-diagnostics subtree.
    """
    sProjectRoot = str(tmp_path / "project")
    pathEphemeralRoot = tmp_path / "vaibifyTmp"
    pathEphemeralRoot.mkdir(parents=True, exist_ok=True)
    pathMountedSecret = pathEphemeralRoot / "mountedSecret"
    pathMountedSecret.write_text("token")
    fWhen = time.time() - 365 * F_ONE_DAY_SECONDS
    os.utime(pathMountedSecret, (fWhen, fWhen))
    _fsMakeOperationDirectory(
        sProjectRoot, "opStale", fAgeSeconds=30 * F_ONE_DAY_SECONDS,
    )

    hostScratch.fnSweepStaleHostScratch()

    assert pathMountedSecret.exists(), (
        "the host sweep reached into the credential store"
    )


@pytest.mark.falsification
def testTheByteCapRetiresTheOldestSurvivorsFirst(tmp_path):
    """A week of heavy use is bounded by size, not only by age.

    Kills: dropping the cap, under which nothing bounds the subtree
    inside the TTL window; and retiring newest-first, which throws
    away the capture most likely to be wanted.
    """
    sProjectRoot = str(tmp_path / "project")
    listDirectories = [
        _fsMakeOperationDirectory(
            sProjectRoot, f"op{iIndex}",
            fAgeSeconds=(3 - iIndex) * F_ONE_DAY_SECONDS,
        )
        for iIndex in range(3)
    ]
    iOneEntry = sum(
        os.lstat(os.path.join(sDirectory, "capture.txt")).st_size
        for sDirectory in listDirectories[:1]
    )

    hostScratch.fnSweepStaleHostScratch(iByteCap=iOneEntry)

    assert not os.path.exists(listDirectories[0]), (
        "the oldest capture survived a cap it broke"
    )
    assert os.path.isdir(listDirectories[2]), (
        "the newest capture was retired before older ones"
    )


def testAnOperationIdCarryingASeparatorIsRefused(tmp_path):
    """A scratch path may not be steered out of the swept subtree.

    The id comes from a journal record. One carrying a separator would
    place the directory somewhere the path guard does not admit and
    this sweeper never visits — a scratch write with no boundary and
    no cleanup.
    """
    sProjectRoot = str(tmp_path / "project")
    for sBadId in ("../escape", "nested/deep", "", "."):
        with pytest.raises(ValueError):
            hostScratch.fsCreateOperationScratchDirectory(
                sProjectRoot, sBadId,
            )


def testAProjectDirectoryEmptiedBySweepingIsRemoved(tmp_path):
    """No husks: an emptied project directory goes with its contents."""
    sProjectRoot = str(tmp_path / "project")
    _fsMakeOperationDirectory(
        sProjectRoot, "opStale", fAgeSeconds=30 * F_ONE_DAY_SECONDS,
    )
    sScratchRoot = hostScratch.fsHostScratchRootForProject(sProjectRoot)
    hostScratch.fnSweepStaleHostScratch()
    assert not os.path.exists(sScratchRoot)


def testAnAbsentSubtreeIsNotAnError():
    """A hub that has never run a host project sweeps nothing, quietly."""
    hostScratch.fnSweepStaleHostScratch()


@pytest.mark.falsification
def testEveryLevelOfTheSubtreeIsPrivateToTheUser(tmp_path):
    """0700 all the way up, not just on the directory that was asked for.

    ``os.makedirs``'s ``mode`` applies to the leaf alone, so the
    obvious one-liner leaves the subtree root and the per-project
    directory at the default — readable and listable by every other
    account on the machine, while the operation directory inside them
    looks correct. Scratch holds staged writes and environment
    captures, and a filename is often enough.

    Kills: creating the tree with a bare ``os.makedirs(mode=0o700)``.
    """
    sProjectRoot = str(tmp_path / "project")
    sDirectory = hostScratch.fsCreateOperationScratchDirectory(
        sProjectRoot, "opModes",
    )
    for sLevel in (
        sDirectory,
        os.path.dirname(sDirectory),
        hostScratch.fsHostDiagnosticsRoot(),
    ):
        assert os.stat(sLevel).st_mode & 0o777 == 0o700, (
            f"{sLevel} is not private to this user"
        )
