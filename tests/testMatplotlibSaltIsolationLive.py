"""The matplotlib salt, against real matplotlib in a real container.

``tests/testMatplotlibSaltIsolation.py`` runs the shell text against real
directories. These tests add what only matplotlib can say: that the salt
a process READS is its own run's even while another run writes a
different one, and that the path of the directory holding the salt does
not reach the figure's bytes -- only the salt does. The second is the
claim the builder's docstring makes, so it is measured here rather than
assumed.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``). The container
installs matplotlib from the package index.
"""

import asyncio
import hashlib
import threading

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import determinismEnvironment

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_HOME = "/home/researcher"
S_READ_SALT = (
    "python3 -c \"import matplotlib; "
    "print(matplotlib.rcParams['svg.hashsalt'])\""
)
S_RENDER_SVG = (
    "python3 -c \"import matplotlib; matplotlib.use('Agg'); "
    "import matplotlib.pyplot as plt; plt.plot([1, 2, 3]); "
    "plt.savefig('SVGPATH', format='svg')\""
)


@pytest.fixture(scope="module")
def liveContainer():
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "900"], detach=True, labels=fdictLabels(),
    )
    try:
        iExit, baOutput = container.exec_run(
            ["sh", "-c",
             f"useradd -m {S_USER} && pip install --no-cache-dir "
             "--quiet matplotlib"], user="root")
        assert iExit == 0, baOutput.decode("utf-8", errors="replace")
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _fsPrefixFor(iEpoch):
    """The runner's real prefix for one epoch (no git query is made)."""
    return asyncio.run(determinismEnvironment._fsBuildDeterminismEnvPrefix(
        None, "cid", "", iSourceDateEpochOverride=iEpoch))


def _ftRun(liveContainer, sCommand):
    container, connection = liveContainer
    tResult = connection.ftRunInContainerStreamed(
        container.id, sCommand, sWorkdir=S_HOME)
    return tResult.iExitCode, tResult.sStdout.strip(), tResult.sStderr


def _fsSha256Of(liveContainer, sPath):
    iExit, sOutput, sError = _ftRun(liveContainer, f"sha256sum {sPath}")
    assert iExit == 0, sError
    return sOutput.split()[0]


@pytest.mark.falsification
def testARunReadsItsOwnSaltWhileAnotherRunWritesADifferentOne(liveContainer):
    """The measured overlap, with real matplotlib reading the salt.

    Run A's prefix writes salt 111 and its command waits; run B's prefix
    writes 222 and its command runs to completion; then A imports
    matplotlib. Matplotlib reports the salt it loaded.

    Kills: giving every run the same directory (A reads 222).
    """
    dictResults = {}

    def fnRunA():
        dictResults["A"] = _ftRun(
            liveContainer, _fsPrefixFor(111) + "sleep 4; " + S_READ_SALT)

    threadA = threading.Thread(target=fnRunA)
    threadA.start()
    threading.Event().wait(1.5)
    dictResults["B"] = _ftRun(liveContainer, _fsPrefixFor(222) + S_READ_SALT)
    threadA.join(timeout=120)
    assert dictResults["B"][:2] == (0, "222"), dictResults["B"]
    assert dictResults["A"][:2] == (0, "111"), dictResults["A"]


def testOnlyTheSaltReachesTheFiguresBytesNotThePathOfItsDirectory(
    liveContainer,
):
    """Two runs, two directories, one salt: the SVG is byte-identical.

    The control makes the claim falsifiable: a different salt gives a
    different file, so identical bytes are not an artefact of matplotlib
    ignoring the salt altogether.
    """
    for sName, iEpoch in (("a", 1700000000), ("b", 1700000000),
                          ("c", 1700000001)):
        iExit, _, sError = _ftRun(
            liveContainer,
            _fsPrefixFor(iEpoch)
            + S_RENDER_SVG.replace("SVGPATH", f"/tmp/figure_{sName}.svg"))
        assert iExit == 0, sError
    sFirst = _fsSha256Of(liveContainer, "/tmp/figure_a.svg")
    sSecond = _fsSha256Of(liveContainer, "/tmp/figure_b.svg")
    sOtherSalt = _fsSha256Of(liveContainer, "/tmp/figure_c.svg")
    assert sFirst == sSecond
    assert sFirst != sOtherSalt


def testEachRunsDirectoryIsPrivateAndAnAgedOneIsSweptByTheNextRun(
    liveContainer,
):
    iExit, sLeft, sError = _ftRun(
        liveContainer, _fsPrefixFor(5) + 'stat -c "%a" "$MPLCONFIGDIR"; '
        'mkdir -p /tmp/vaibifyMatplotlib.aged && '
        'touch -d "2 days ago" /tmp/vaibifyMatplotlib.aged; '
        'mkdir -p /tmp/unrelatedByAnotherTool && '
        'touch -d "2 days ago" /tmp/unrelatedByAnotherTool')
    assert iExit == 0, sError
    assert sLeft.splitlines()[-1] == "700"
    iExit, sListing, _ = _ftRun(
        liveContainer, _fsPrefixFor(6) + "ls -d /tmp/vaibifyMatplotlib.* "
        "/tmp/unrelatedByAnotherTool")
    assert "/tmp/vaibifyMatplotlib.aged" not in sListing
    assert "/tmp/unrelatedByAnotherTool" in sListing


def testTheReproductionPreambleSaltsInItsOwnDirectoryAndRemovesIt(
    liveContainer,
):
    from vaibify.reproducibility.reproduceScriptGenerator import (
        _fsBuildContainerPreamble,
    )
    sPreamble = _fsBuildContainerPreamble().split("pip install")[0]
    sScript = (
        sPreamble + 'echo "dir=$MPLCONFIGDIR"; ' + S_READ_SALT)
    iExit, sOutput, sError = _ftRun(
        liveContainer, "SOURCE_DATE_EPOCH=1700000009 bash -c "
        + _fsShellQuote(sScript))
    assert iExit == 0, sError
    listLines = sOutput.splitlines()
    assert listLines[-1] == "1700000009"
    sDirectory = listLines[-2].split("=", 1)[1]
    assert sDirectory.startswith("/tmp/vaibifyMatplotlib.")
    iExit, _, _ = _ftRun(liveContainer, f"test -e {sDirectory}")
    assert iExit != 0, "the script left its directory behind"


def _fsShellQuote(sValue):
    return "'" + sValue.replace("'", "'\\''") + "'"
