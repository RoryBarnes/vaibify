"""The directory copy, against a real daemon and a real container.

The unit tests drive the receiver program in a subprocess on the test
machine. Only a real container proves what the 2026-10-03 measurement
found: that the daemon, extracting an archive as root, FOLLOWED a symlink
the container user planted and changed the owner of a root-owned
directory it reached through it -- which made the old copy a privilege
escalation inside the container (a root login shell then ran a script the
user had planted in a directory the user now owned). Every test below
plants the redirect as the unprivileged user and then reads the root-owned
target AS ROOT, so a copy that still follows the link is caught by what it
did to the target, not by an exception it may or may not raise.

Skipped when no daemon answers, unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
demands one (see ``tests/testDockerConnectionLive.py``).
"""

import hashlib
import io
import os
import tarfile
import time

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker import confinedWrite
from vaibify.docker.confinedWrite import ContainerWriteRefusedError

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_PROJECT = "/home/researcher/project"
S_REDIRECT = "/opt/redirect"
S_MARKER = f"{S_REDIRECT}/marker"
S_MARKER_BYTES = "root-only secret that the user cannot overwrite\n"


def _fclientDocker():
    import docker
    from vaibify.docker.dockerConnection import _fnEnsureDockerHost
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    return clientDocker


def _fnRunSetup(container, listCommands):
    for sSetup in listCommands:
        iExit, baOutput = container.exec_run(["sh", "-c", sSetup], user="root")
        assert iExit == 0, (sSetup, baOutput)


@pytest.fixture(scope="module")
def liveContainer():
    fnRequireDaemonReachable()
    from vaibify.docker.dockerConnection import DockerConnection
    container = _fclientDocker().containers.run(
        S_IMAGE, ["sleep", "900"], detach=True, labels=fdictLabels(),
    )
    try:
        _fnRunSetup(container, [
            f"useradd -m {S_USER}",
            f"mkdir -p {S_PROJECT} && chown -R {S_USER} /home/{S_USER}",
        ])
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


@pytest.fixture(autouse=True)
def fixtureFreshScene(liveContainer):
    """A root-owned redirect target the user can see but not change."""
    container, _ = liveContainer
    _fnRunSetup(container, [
        f"rm -rf {S_PROJECT}/* {S_REDIRECT}",
        f"mkdir -p {S_REDIRECT}/inputData {S_REDIRECT}/inner",
        f"printf '%s' '{S_MARKER_BYTES.strip()}' > {S_MARKER}",
        f"chmod 600 {S_MARKER}",
        f"chmod 755 {S_REDIRECT} {S_REDIRECT}/inputData {S_REDIRECT}/inner",
    ])


def _fsRun(container, sCommand, sUser=S_USER):
    iExit, baOutput = container.exec_run(["sh", "-c", sCommand], user=sUser)
    return iExit, baOutput.decode("utf-8", errors="replace")


@pytest.fixture
def pathHostTree(tmp_path):
    pathTree = tmp_path / "inputData"
    (pathTree / "nested").mkdir(parents=True)
    (pathTree / "dataFile.csv").write_bytes(b"1,2,3\n")
    (pathTree / "nested" / "notes.txt").write_bytes(b"notes")
    (pathTree / "run.sh").write_bytes(b"#!/bin/sh\necho ok\n")
    os.chmod(str(pathTree / "run.sh"), 0o755)
    os.chmod(str(pathTree / "dataFile.csv"), 0o640)
    os.utime(str(pathTree / "dataFile.csv"), (1_600_000_000, 1_600_000_000))
    os.symlink("relativeTarget", str(pathTree / "aLink"))
    return pathTree


def _fnAssertTheRedirectTargetIsUntouched(container):
    """The root-owned target keeps its owner, its contents and its marker."""
    iExit, sOwners = _fsRun(
        container,
        f"stat -c '%U %a' {S_REDIRECT} {S_REDIRECT}/inputData "
        f"{S_REDIRECT}/inner {S_MARKER}", sUser="root")
    assert iExit == 0, sOwners
    assert sOwners.split("\n")[:4] == [
        "root 755", "root 755", "root 755", "root 600"], sOwners
    iExit, sContents = _fsRun(
        container,
        f"find {S_REDIRECT}/inputData {S_REDIRECT}/inner -mindepth 1",
        sUser="root")
    assert sContents.strip() == "", sContents
    iExit, sMarker = _fsRun(container, f"cat {S_MARKER}", sUser="root")
    assert sMarker.strip() == S_MARKER_BYTES.strip()


# ---------------------------------------------------------------------
# The measured redirects, replayed
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAPlantedSymlinkAtTheDestinationDoesNotRedirectOrChangeOwnership(
    liveContainer, pathHostTree,
):
    """Kills: handing the tree to the daemon (``put_archive``) again.

    The daemon follows the link and, because the archive's top-level
    name matches the root-owned ``inputData`` in the target, changes
    that directory's owner to the container user.
    """
    container, connection = liveContainer
    _fsRun(container, f"ln -sfn {S_REDIRECT} {S_PROJECT}/dest")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteTreeViaTar(
            container.id, f"{S_PROJECT}/dest", [str(pathHostTree)])
    _fnAssertTheRedirectTargetIsUntouched(container)


@pytest.mark.falsification
def testAPlantedSymlinkAtAnIntermediateComponentDoesNotRedirect(
    liveContainer, pathHostTree,
):
    """Kills: handing the tree to the daemon (``put_archive``) again.

    The second measured redirect: a link in the middle of the path.
    """
    container, connection = liveContainer
    _fsRun(container, f"ln -sfn {S_REDIRECT} {S_PROJECT}/hop")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteTreeViaTar(
            container.id, f"{S_PROJECT}/hop/inner", [str(pathHostTree)])
    _fnAssertTheRedirectTargetIsUntouched(container)


@pytest.mark.falsification
def testAMemberDirectoryThatIsASymlinkIsRefusedAndKeptNotDeleted(
    liveContainer, pathHostTree,
):
    """Kills: handing the tree to the daemon (``put_archive``) again.

    The daemon deletes the researcher's link without a word and puts a
    directory in its place.
    """
    container, connection = liveContainer
    _fsRun(container,
           f"mkdir -p {S_PROJECT}/dest && "
           f"ln -sfn {S_REDIRECT}/inputData {S_PROJECT}/dest/inputData")
    with pytest.raises(ContainerWriteRefusedError) as infoError:
        connection.fnWriteTreeViaTar(
            container.id, f"{S_PROJECT}/dest", [str(pathHostTree)])
    assert infoError.value.iMembersLanded == 0
    assert "inputData" in str(infoError.value)
    iExit, sTarget = _fsRun(
        container, f"readlink {S_PROJECT}/dest/inputData")
    assert sTarget.strip() == f"{S_REDIRECT}/inputData"
    _fnAssertTheRedirectTargetIsUntouched(container)


def testASymlinkAtAMemberFilePathIsReplacedAndItsTargetIsNeverWritten(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    _fsRun(container,
           f"mkdir -p {S_PROJECT}/dest/inputData && "
           f"ln -sfn {S_MARKER} {S_PROJECT}/dest/inputData/dataFile.csv")
    connection.fnWriteTreeViaTar(
        container.id, f"{S_PROJECT}/dest", [str(pathHostTree)])
    iExit, sState = _fsRun(
        container,
        f"stat -c '%F %U' {S_PROJECT}/dest/inputData/dataFile.csv; "
        f"cat {S_PROJECT}/dest/inputData/dataFile.csv")
    assert sState.split("\n")[:2] == ["regular file researcher", "1,2,3"]
    iExit, sMarker = _fsRun(container, f"cat {S_MARKER}", sUser="root")
    assert sMarker.strip() == S_MARKER_BYTES.strip()


# ---------------------------------------------------------------------
# What an honest copy still does
# ---------------------------------------------------------------------


def testAnOrdinaryCopyKeepsOwnershipModesTimesAndLinks(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    connection.fnWriteTreeViaTar(
        container.id, S_PROJECT, [str(pathHostTree)])
    iExit, sStat = _fsRun(
        container,
        f"cd {S_PROJECT}/inputData && "
        "stat -c '%U:%G %a %Y' dataFile.csv run.sh nested nested/notes.txt; "
        "readlink aLink; cat nested/notes.txt")
    assert iExit == 0, sStat
    listLines = sStat.split("\n")
    assert listLines[0] == f"{S_USER}:{S_USER} 640 1600000000"
    assert listLines[1].startswith(f"{S_USER}:{S_USER} 755 ")
    assert listLines[2].startswith(f"{S_USER}:{S_USER} 755 ")
    assert listLines[3].startswith(f"{S_USER}:{S_USER} 644 ")
    assert listLines[4:6] == ["relativeTarget", "notes"]


def testASeedCreatesItsDestinationBelowTheRootAsTheUser(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    connection.fnWriteTreeViaTar(
        container.id, f"{S_PROJECT}/seeded/project", [str(pathHostTree)],
        sAuthorizedRoot=S_PROJECT, bCreateDestination=True)
    iExit, sOwner = _fsRun(
        container,
        f"stat -c '%U' {S_PROJECT}/seeded {S_PROJECT}/seeded/project "
        f"{S_PROJECT}/seeded/project/inputData/dataFile.csv")
    assert sOwner.split() == [S_USER] * 3


def testACopyToAMissingDestinationIsRefusedWithoutTheCreateFlag(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteTreeViaTar(
            container.id, f"{S_PROJECT}/no/such/place", [str(pathHostTree)])
    iExit, _ = _fsRun(container, f"test -e {S_PROJECT}/no")
    assert iExit != 0


def testAPushOfADirectoryToANewNameLandsAtThatName(
    liveContainer, pathHostTree,
):
    container, connection = liveContainer
    connection.fnCopyHostPathIntoContainer(
        container.id, str(pathHostTree), f"{S_PROJECT}/renamed")
    iExit, sListing = _fsRun(
        container, f"cat {S_PROJECT}/renamed/dataFile.csv")
    assert sListing.strip() == "1,2,3"
    iExit, _ = _fsRun(container, f"test -e {S_PROJECT}/inputData")
    assert iExit != 0


# ---------------------------------------------------------------------
# Archives nobody should be able to send
# ---------------------------------------------------------------------


def _fbaCraftArchive(listMembers):
    fileBuffer = io.BytesIO()
    with tarfile.open(fileobj=fileBuffer, mode="w") as archive:
        for sName, iType, sLink, baContent in listMembers:
            infoMember = tarfile.TarInfo(sName)
            infoMember.type = iType
            infoMember.linkname = sLink
            infoMember.mode = 0o644
            infoMember.size = len(baContent)
            archive.addfile(infoMember, io.BytesIO(baContent))
    return fileBuffer.getvalue()


def _ftSendArchive(container, connection, sDestination, baArchive):
    sProgram = confinedWrite.fsRenderConfinedTreeProgram(sDestination)
    return connection.ftRunProgramWithStdin(
        container.id, ["python3", "-c", sProgram], baArchive)


@pytest.mark.parametrize("sName, iType, sLink", [
    ("/opt/redirect/planted", tarfile.REGTYPE, ""),
    ("../escaped", tarfile.REGTYPE, ""),
    ("hard", tarfile.LNKTYPE, "/opt/redirect/marker"),
    ("device", tarfile.CHRTYPE, ""),
    ("pipe", tarfile.FIFOTYPE, ""),
])
def testAHostileArchiveIsRefusedByTheRealProgram(
    liveContainer, sName, iType, sLink,
):
    container, connection = liveContainer
    tExecResult = _ftSendArchive(
        container, connection, S_PROJECT,
        _fbaCraftArchive([(sName, iType, sLink, b"")]))
    assert tExecResult.iExitCode == confinedWrite.I_REFUSED_EXIT_CODE, (
        tExecResult.sStderr)
    _fnAssertTheRedirectTargetIsUntouched(container)
    iExit, sListing = _fsRun(container, f"ls -A /home/{S_USER}/..; ls -A {S_PROJECT}")
    assert "escaped" not in sListing


# ---------------------------------------------------------------------
# The race, a full disk, and a cancelled copy
# ---------------------------------------------------------------------


def testNothingEverLandsOutsideWhileTheDestinationIsSwappedForASymlink(
    liveContainer, tmp_path,
):
    """A swapper flips a component between a directory and a link.

    Each copy is a whole tree of many members, so the swap lands in the
    middle of one. A copy either lands inside the project, is refused,
    or fails because the component momentarily did not exist; no file
    may appear in the directory the link points at.
    """
    container, connection = liveContainer
    pathTree = tmp_path / "bulk"
    pathTree.mkdir()
    for iFile in range(200):
        (pathTree / f"member{iFile:03d}.txt").write_bytes(b"payload" * 100)
    _fsRun(container, f"mkdir -p {S_PROJECT}/racing {S_REDIRECT}/open && "
           f"chmod 777 {S_REDIRECT}/open", sUser="root")
    sSwapper = (
        f"cd {S_PROJECT}; while true; do "
        f"mv racing racing.kept 2>/dev/null; ln -s {S_REDIRECT}/open racing; "
        f"rm racing; mv racing.kept racing; done"
    )
    container.exec_run(["sh", "-c", sSwapper], user=S_USER, detach=True)
    dictOutcomes = {"landed": 0, "refused": 0, "failed": 0}
    fDeadline = time.monotonic() + 25
    iAttempt = 0
    while time.monotonic() < fDeadline and iAttempt < 40:
        iAttempt += 1
        try:
            connection.fnWriteTreeViaTar(
                container.id, f"{S_PROJECT}/racing", [str(pathTree)])
            dictOutcomes["landed"] += 1
        except ContainerWriteRefusedError:
            dictOutcomes["refused"] += 1
        except OSError:
            dictOutcomes["failed"] += 1
    _fsRun(container, "pkill -f 'while true' ; pkill -f 'mv racing'; true")
    iExit, sListing = _fsRun(
        container, f"ls -A {S_REDIRECT}/open", sUser="root")
    assert sListing.strip() == "", (dictOutcomes, sListing)
    assert sum(dictOutcomes.values()) == iAttempt


def testAFullDiskFailsTheCopyHonestlyAndLeavesNoTemporary(tmp_path):
    fnRequireDaemonReachable()
    from vaibify.docker.dockerConnection import DockerConnection
    container = _fclientDocker().containers.run(
        S_IMAGE, ["sleep", "300"], detach=True, labels=fdictLabels(),
        tmpfs={"/small": "size=2m,mode=1777"},
    )
    try:
        _fnRunSetup(container, [f"useradd -m {S_USER}"])
        pathTree = tmp_path / "tooBig"
        pathTree.mkdir()
        (pathTree / "a.txt").write_bytes(b"fits")
        (pathTree / "b.bin").write_bytes(os.urandom(1 << 20) * 5)
        with pytest.raises(OSError) as infoError:
            DockerConnection().fnWriteTreeViaTar(
                container.id, "/small", [str(pathTree)])
        assert not isinstance(infoError.value, ContainerWriteRefusedError)
        assert "No space left" in str(infoError.value)
        assert infoError.value.iMembersLanded is not None
        iExit, sListing = _fsRun(container, "ls -A /small/tooBig")
        assert ".vaibify-write-" not in sListing, sListing
        assert "b.bin" not in sListing
    finally:
        container.remove(force=True)


def testACancelledCopyStopsCleanlyAndNeverLeavesAPartialFile(
    liveContainer, tmp_path,
):
    """The stream ends mid-member, as it does when the hub is killed."""
    container, connection = liveContainer
    pathTree = tmp_path / "cancelled"
    pathTree.mkdir()
    (pathTree / "first.txt").write_bytes(b"complete")
    (pathTree / "second.bin").write_bytes(os.urandom(1 << 20) * 4)
    fileTar = connection._ffileBuildTreeTar([str(pathTree)])
    baWhole = fileTar.read()
    fileTar.close()
    sProgram = confinedWrite.fsRenderConfinedTreeProgram(S_PROJECT)
    tExecResult = connection.ftRunProgramWithStdin(
        container.id, ["python3", "-c", sProgram],
        baWhole[:len(baWhole) // 2])
    assert tExecResult.iExitCode == confinedWrite.I_FAILED_EXIT_CODE
    assert "vaibify-landed=" in tExecResult.sStderr
    iExit, sListing = _fsRun(container, f"ls -A {S_PROJECT}/cancelled")
    assert ".vaibify-write-" not in sListing
    assert "second.bin" not in sListing


def testTheCopyHashesMatchForALargeTree(liveContainer, tmp_path):
    """A tree large enough to be spooled to disk arrives byte for byte."""
    container, connection = liveContainer
    pathTree = tmp_path / "large"
    pathTree.mkdir()
    dictHashes = {}
    for iFile in range(12):
        baPayload = os.urandom(3 << 20)
        (pathTree / f"part{iFile:02d}.bin").write_bytes(baPayload)
        dictHashes[f"part{iFile:02d}.bin"] = hashlib.sha256(
            baPayload).hexdigest()
    connection.fnWriteTreeViaTar(
        container.id, S_PROJECT, [str(pathTree)])
    iExit, sSums = _fsRun(
        container, f"cd {S_PROJECT}/large && sha256sum part*.bin")
    assert iExit == 0, sSums
    dictLanded = {
        sName: sSum for sSum, sName in (
            sLine.split() for sLine in sSums.strip().splitlines())
    }
    assert dictLanded == dictHashes
