"""A run's matplotlib salt lives in a directory no other run can write to.

Source: ``vaibify/gui/determinismEnvironment.py`` (the salt builder, the
per-run directory and the stale sweep) and the ``reproduce.sh`` preamble.

The salt directory used to be one fixed path holding the project's HEAD
epoch. Concurrent runs are allowed, so a run could import matplotlib and
read the salt another run had just written (measured: run A wrote 111,
run B wrote 222, and A then read 222), which grades a reproducible SVG as
not reproducible. These tests run the real shell text in a real ``bash``
against real directories; ``tests/testMatplotlibSaltIsolationLive.py`` does
the same against matplotlib in a container.
"""

import os
import re
import shlex
import stat
import subprocess
import threading
import time

import pytest

from vaibify.gui import determinismEnvironment
from vaibify.gui.determinismEnvironment import (
    fsBuildMatplotlibSaltShell, fsBuildMatplotlibStaleSweepShell,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    _fsBuildContainerPreamble,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="the salt prefix is shell text",
)


def _fprocessBash(sScript, dictEnvironment=None):
    return subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True, timeout=60,
        env=dictEnvironment,
    )


def _fsPrefixFor(iEpoch, sRoot):
    """The runner's real prefix for one epoch, rooted under ``sRoot``."""
    import asyncio
    determinismEnvironment.S_MATPLOTLIB_CONFIG_ROOT = sRoot
    return asyncio.run(determinismEnvironment._fsBuildDeterminismEnvPrefix(
        None, "cid", "", iSourceDateEpochOverride=iEpoch,
    ))


@pytest.fixture(autouse=True)
def fixtureRestoreTheRoot():
    sOriginal = determinismEnvironment.S_MATPLOTLIB_CONFIG_ROOT
    yield
    determinismEnvironment.S_MATPLOTLIB_CONFIG_ROOT = sOriginal


# ---------------------------------------------------------------------
# What the builder produces
# ---------------------------------------------------------------------


def testTheSaltIsWrittenAtomicallyIntoAPrivateDirectoryAndExported(tmp_path):
    sDirectory = shlex.quote(str(tmp_path / "run"))
    processBash = _fprocessBash(
        fsBuildMatplotlibSaltShell("1745798400", sDirectory)
        + ' && echo "dir=$MPLCONFIGDIR"')
    assert processBash.returncode == 0, processBash.stderr
    pathRun = tmp_path / "run"
    assert (pathRun / "matplotlibrc").read_text() == (
        "svg.hashsalt: 1745798400\n")
    assert stat.S_IMODE(os.stat(str(pathRun)).st_mode) == 0o700
    assert sorted(os.listdir(str(pathRun))) == ["matplotlibrc"]
    assert f"dir={pathRun}" in processBash.stdout


def testTheEpochMayBeAShellExpansion(tmp_path):
    """``reproduce.sh`` cannot know the epoch when the script is written."""
    sDirectory = shlex.quote(str(tmp_path / "run"))
    processBash = _fprocessBash(
        "SOURCE_DATE_EPOCH=42; "
        + fsBuildMatplotlibSaltShell("$SOURCE_DATE_EPOCH", sDirectory))
    assert processBash.returncode == 0, processBash.stderr
    assert (tmp_path / "run" / "matplotlibrc").read_text() == (
        "svg.hashsalt: 42\n")


def testAnUnwritableDirectoryReportsAndDoesNotBreakTheChain(tmp_path):
    """A step still runs when its determinism cannot be guaranteed."""
    pathLocked = tmp_path / "locked"
    pathLocked.mkdir()
    os.chmod(str(pathLocked), 0o500)
    try:
        if os.access(str(pathLocked), os.W_OK):
            pytest.skip("this account ignores directory permissions")
        sDirectory = shlex.quote(str(pathLocked / "run"))
        processBash = _fprocessBash(
            fsBuildMatplotlibSaltShell("1", sDirectory) + " && echo reached")
    finally:
        os.chmod(str(pathLocked), 0o700)
    assert "reached" in processBash.stdout
    assert "svg.hashsalt not pinned" in processBash.stderr


def testASiblingRewritingTheSaltNeverLeavesAnEmptyFileForAReader(tmp_path):
    """The file is renamed into place, so a reader never sees it truncated.

    Two commands of one run write the same value to the same file; a
    ``>`` in place truncates it first, and a process starting then reads
    nothing.
    """
    sDirectory = shlex.quote(str(tmp_path / "run"))
    sBuild = fsBuildMatplotlibSaltShell("777", sDirectory)
    _fprocessBash(sBuild)
    pathRc = str(tmp_path / "run" / "matplotlibrc")
    listObservations = []
    eventDone = threading.Event()

    def fnReader():
        while not eventDone.is_set():
            with open(pathRc) as fileRc:
                listObservations.append(fileRc.read())

    threadReader = threading.Thread(target=fnReader, daemon=True)
    threadReader.start()
    for _ in range(150):
        assert _fprocessBash(sBuild).returncode == 0
    eventDone.set()
    threadReader.join(timeout=10)
    assert listObservations
    assert set(listObservations) == {"svg.hashsalt: 777\n"}


# ---------------------------------------------------------------------
# The runner's prefix: one directory per run
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testTwoRunsGetTwoDirectoriesAndEachReadsItsOwnSalt(tmp_path):
    """The measured overlap: A salts 111, B salts 222, then A reads.

    Run A's prefix writes its salt and the command waits; run B's prefix
    runs and finishes; then A reads the salt matplotlib would read. With
    one shared directory A reads B's value.

    Kills: giving every run the same directory.
    """
    sRoot = str(tmp_path)
    sPrefixA = _fsPrefixFor(111, sRoot)
    sPrefixB = _fsPrefixFor(222, sRoot)
    sReadSalt = 'cat "$MPLCONFIGDIR/matplotlibrc"'
    processA = subprocess.Popen(
        ["bash", "-c", sPrefixA + "sleep 1.5; " + sReadSalt],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.5)
    processB = _fprocessBash(sPrefixB + sReadSalt)
    sOutputA, sErrorA = processA.communicate(timeout=60)
    assert processB.stdout == "svg.hashsalt: 222\n", processB.stderr
    assert sOutputA == "svg.hashsalt: 111\n", sErrorA


def testEveryRunsDirectoryIsInsideTheRootAndNamedForItsRun(tmp_path):
    sPrefixA = _fsPrefixFor(111, str(tmp_path))
    sPrefixB = _fsPrefixFor(111, str(tmp_path))
    listDirectories = [
        re.search(r"export MPLCONFIGDIR='([^']+)'", sPrefix).group(1)
        for sPrefix in (sPrefixA, sPrefixB)
    ]
    assert listDirectories[0] != listDirectories[1]
    for sDirectory in listDirectories:
        assert os.path.dirname(sDirectory) == str(tmp_path)
        assert os.path.basename(sDirectory).startswith("vaibifyMatplotlib.")


# ---------------------------------------------------------------------
# The stale sweep
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testOnlyStaleDirectoriesOfTheFamilyAreSwept(tmp_path):
    """A live run's directory and anything else in the root survive.

    Kills: sweeping directories that were touched recently, which would
    delete the salt of a run in progress.
    """
    iOld = time.time() - 24 * 3600
    pathStale = tmp_path / "vaibifyMatplotlib.stale"
    pathFresh = tmp_path / "vaibifyMatplotlib.fresh"
    pathOther = tmp_path / "somebodysData"
    pathLookalike = tmp_path / "vaibifyMatplotlibNotOurs"
    for pathDirectory in (pathStale, pathFresh, pathOther, pathLookalike):
        pathDirectory.mkdir()
        (pathDirectory / "file").write_text("x")
    for pathDirectory in (pathStale, pathOther, pathLookalike):
        os.utime(str(pathDirectory), (iOld, iOld))
    processBash = _fprocessBash(fsBuildMatplotlibStaleSweepShell(str(tmp_path)))
    assert processBash.returncode == 0, processBash.stderr
    assert not pathStale.exists()
    assert pathFresh.exists() and pathOther.exists() and pathLookalike.exists()


def testTheSweepNeverFailsTheChainItIsPartOf(tmp_path):
    processBash = _fprocessBash(
        fsBuildMatplotlibStaleSweepShell(str(tmp_path / "absent"))
        + " && echo reached")
    assert "reached" in processBash.stdout


# ---------------------------------------------------------------------
# reproduce.sh: its own directory, removed on exit, never somebody else's
# ---------------------------------------------------------------------


def _fsRunPreambleThen(sAfter, tmp_path, dictExtraEnvironment=None):
    determinismEnvironment.S_MATPLOTLIB_CONFIG_ROOT = str(tmp_path)
    sPreamble = _fsBuildContainerPreamble().split("pip install")[0]
    dictEnvironment = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
    dictEnvironment.update(dictExtraEnvironment or {})
    return _fprocessBash(sPreamble + sAfter, dictEnvironment)


def testTheReproductionPinsTheSaltInItsOwnDirectoryAndRemovesItOnExit(
    tmp_path,
):
    processBash = _fsRunPreambleThen(
        'echo "dir=$MPLCONFIGDIR"; cat "$MPLCONFIGDIR/matplotlibrc"', tmp_path)
    assert processBash.returncode == 0, processBash.stderr
    sDirectory = re.search(r"dir=(\S+)", processBash.stdout).group(1)
    assert os.path.dirname(sDirectory) == str(tmp_path)
    assert "svg.hashsalt: 1700000000" in processBash.stdout
    assert not os.path.exists(sDirectory), "left behind after exit"


@pytest.mark.falsification
def testTheExitTrapNeverDeletesADirectoryAStepExportedItself(tmp_path):
    """A step may set its own ``MPLCONFIGDIR``; the trap must not take it.

    Kills: removing ``$MPLCONFIGDIR`` on exit instead of the script's own
    directory, which deletes whatever a step pointed it at.
    """
    pathOwn = tmp_path / "researchersOwn"
    processBash = _fsRunPreambleThen(
        f'mkdir -p {shlex.quote(str(pathOwn))}; '
        f'echo precious > {shlex.quote(str(pathOwn))}/file; '
        f'export MPLCONFIGDIR={shlex.quote(str(pathOwn))}', tmp_path)
    assert processBash.returncode == 0, processBash.stderr
    assert (pathOwn / "file").read_text() == "precious\n"
