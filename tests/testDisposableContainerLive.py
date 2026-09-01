"""The disposable-container lane, driven against a real Docker daemon.

The unit suite for this lane can only assert that the right SDK calls
were composed. What it cannot see is whether the daemon HONOURS them,
and that is where every defect this module has had actually lived: a
tarball whose intermediate directories the daemon created root-owned,
so the container user owned its own files and could not create a
sibling beside them; a posture whose capability drop is asserted in a
dict and never read from ``/proc``; a destruction "proof" that is only
a proof if the daemon really answers 404 afterwards.

So this file exercises the real adapter, against a real image, with the
container's own answers as the evidence -- the discipline the repository
learned when a fully green suite shipped an owner map keyed by name and
read by id.

``ubuntu:24.04`` is the image because the lane must work for whatever
image an envelope pins, and a stock base image is the weakest
assumption available. Nothing here is science-specific.

Skipped automatically when no daemon is reachable, unless
``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one -- a lane advertised as
live coverage that reports success without a daemon is a false green.
"""

import io
import tarfile

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.docker import disposableContainer
from vaibify.docker import disposableSpecification


pytestmark = pytest.mark.docker_live

S_PROBE_IMAGE = "ubuntu:24.04"
S_PROBE_RESOURCE = "disposableProbeResource"


def _fbaBuildProbeArchive():
    """Build a tarball that names files WITHOUT naming their parents.

    The omission is the point: this is the shape ``get_archive`` and
    every ordinary ``tar`` produce, and it is the shape that exposed the
    root-owned-parent defect.
    """
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for sName, baBody in (
            ("repo/alpha.txt", b"alpha\n"),
            ("repo/nested/beta.txt", b"beta\n"),
        ):
            infoMember = tarfile.TarInfo(name=sName)
            infoMember.size = len(baBody)
            infoMember.uid = 0
            infoMember.gid = 0
            infoMember.uname = "root"
            infoMember.gname = "root"
            fileTar.addfile(infoMember, io.BytesIO(baBody))
    return bufferTar.getvalue()


@pytest.fixture
def tGatewayAndHandle():
    """Yield ``(gateway, handle)`` for one live shadow container.

    The teardown destroys unconditionally and asserts nothing, so a
    failing assertion inside a test reports its own reason rather than a
    cleanup error -- but it must still leave no container behind on the
    researcher's daemon.
    """
    fnRequireDaemonReachable()
    dockerDisposable = (
        disposableContainer.fdockerCreateDisposableClient())
    _fnRequireProbeImage(dockerDisposable)
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerDisposable, S_PROBE_RESOURCE)
    dictCreated = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, "shadow", S_PROBE_IMAGE)
    try:
        yield (dictGateway, dictCreated)
    finally:
        if dictCreated["sHandle"] in dictGateway["dictHandlesById"]:
            disposableContainer.fdictDestroyAndSettle(
                dictGateway, dictCreated["sHandle"])


def _fnRequireProbeImage(dockerDisposable):
    """Skip when the stock probe image is absent rather than pulling it."""
    try:
        dockerDisposable.images.get(S_PROBE_IMAGE)
    except Exception:
        pytest.skip(
            f"{S_PROBE_IMAGE} is not in the local image store; "
            f"`docker pull {S_PROBE_IMAGE}` to run this lane"
        )


def test_the_copied_archive_is_writable_by_the_container_user(
    tGatewayAndHandle,
):
    """Every synthesized parent must land owned by the container user.

    The falsifiable claim: a file the container user owns is useless if
    its DIRECTORY is root-owned, because the workflow that reruns there
    cannot create anything beside it. Asserted by writing, not by
    reading a mode -- a write is the thing the shadow rerun actually
    needs, and it cannot be satisfied by an ownership that merely looks
    right.
    """
    dictGateway, dictCreated = tGatewayAndHandle
    disposableContainer.fnCopyArchiveIntoContainer(
        dictGateway, dictCreated["sHandle"], _fbaBuildProbeArchive(),
        sDestinationDirectory="/", sPathPrefix="shadow",
    )
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"],
        ["/bin/sh", "-c",
         "stat -c '%u:%g %n' /shadow /shadow/repo /shadow/repo/nested; "
         "cat /shadow/repo/nested/beta.txt; "
         "echo produced > /shadow/repo/output.txt && echo WROTE_OK"],
        fWallClockSeconds=60,
    )
    assert dictOutcome["iExitCode"] == 0, dictOutcome["sOutput"]
    assert "WROTE_OK" in dictOutcome["sOutput"], (
        "the container user could not create a file beside its own "
        f"copied files: {dictOutcome['sOutput']}"
    )
    assert "beta" in dictOutcome["sOutput"], "the archive content is missing"
    for sDirectory in ("/shadow", "/shadow/repo", "/shadow/repo/nested"):
        assert f"1000:1000 {sDirectory}" in dictOutcome["sOutput"], (
            f"{sDirectory} is not owned by the unprivileged container "
            f"user: {dictOutcome['sOutput']}"
        )


def test_the_posture_reaches_the_daemon(tGatewayAndHandle):
    """Read the hardening back out of the container, not out of the dict.

    A create-specification asserted against itself proves only that the
    composer agrees with the test. These four facts come from the
    kernel's and the daemon's own view: every capability dropped, the
    unprivileged uid in effect, and no network interface but loopback.
    """
    dictGateway, dictCreated = tGatewayAndHandle
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"],
        ["/bin/sh", "-c",
         "id -u; grep -i '^CapEff' /proc/self/status; "
         "ls /sys/class/net"],
        fWallClockSeconds=60,
    )
    assert dictOutcome["iExitCode"] == 0, dictOutcome["sOutput"]
    assert "\n1000\n" in "\n" + dictOutcome["sOutput"], (
        f"the command did not run as uid 1000: {dictOutcome['sOutput']}"
    )
    assert "0000000000000000" in dictOutcome["sOutput"], (
        f"capabilities were not all dropped: {dictOutcome['sOutput']}"
    )
    listInterfaces = dictOutcome["sOutput"].split("\n")[-1].split()
    assert listInterfaces in ([], ["lo"]), (
        f"the shadow reached a network it should not have: "
        f"{listInterfaces}"
    )


def test_a_runaway_command_is_killed_at_its_output_cap(tGatewayAndHandle):
    """The cap must stop the CONTAINER, not merely truncate the buffer.

    Truncating host-side while the process keeps producing is the
    plausible wrong implementation, and it looks identical from the
    returned bytes. The discriminator is the exit code: a killed
    container can establish none, so ``iExitCode`` is None rather than a
    fabricated zero.
    """
    dictGateway, dictCreated = tGatewayAndHandle
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"],
        ["/bin/sh", "-c", "yes ABCDEFGHIJ"],
        iOutputByteCap=4096, fWallClockSeconds=60,
    )
    assert dictOutcome["bOutputCapExceeded"] is True
    assert dictOutcome["iOutputBytes"] <= 4096
    assert dictOutcome["iExitCode"] is None, (
        "a container killed at its output cap can establish no exit "
        f"code; got {dictOutcome['iExitCode']}"
    )


def test_destruction_is_settled_only_on_a_proven_absence(
    tGatewayAndHandle,
):
    """A destroyed container must be gone by the DAEMON's own answer."""
    dictGateway, dictCreated = tGatewayAndHandle
    dictDestroyed = disposableContainer.fdictDestroyAndSettle(
        dictGateway, dictCreated["sHandle"])
    assert dictDestroyed["sOutcome"] == (
        disposableSpecification.S_OUTCOME_DESTROYED
    ), dictDestroyed["sReason"]
    assert dictDestroyed["dictProbe"]["sAnswer"] == (
        disposableSpecification.S_ABSENCE_ABSENT
    )
    assert dictGateway["dictReservationsById"] == {}, (
        "a proven destruction must release its reservation"
    )
    assert disposableContainer.flistDescribeQuarantinedReservations(
        dictGateway) == []
    dictProbe = disposableContainer.fdictProbeContainerAbsence(
        dictGateway["dockerDisposable"], dictCreated["sContainerName"])
    assert dictProbe["sAnswer"] == (
        disposableSpecification.S_ABSENCE_ABSENT
    ), "the daemon still knows the container this lane reported destroyed"


def test_destruction_refuses_a_container_this_lane_did_not_create(
    tGatewayAndHandle,
):
    """The identity check must refuse, and refuse WITHOUT destroying.

    The canonical thing this protects is the researcher's own project
    container. Simulated by pointing a live handle at a foreign
    container id, which is the only way the mistake can actually happen:
    the handle is server-minted, so the id is the mutable half.
    """
    dictGateway, dictCreated = tGatewayAndHandle
    dockerDisposable = dictGateway["dockerDisposable"]
    containerBystander = dockerDisposable.containers.create(
        S_PROBE_IMAGE, entrypoint=["/bin/sh"],
        command=["-c", "sleep 120"], network_mode="none",
    )
    containerBystander.start()
    try:
        dictGateway["dictHandlesById"][dictCreated["sHandle"]][
            "sContainerId"] = containerBystander.id
        with pytest.raises(
            disposableContainer.DisposableContainerError,
            match="does not carry the disposable label",
        ):
            disposableContainer.fdictDestroyAndSettle(
                dictGateway, dictCreated["sHandle"])
        containerBystander.reload()
        assert containerBystander.status == "running", (
            "the refusal destroyed a container it was supposed to leave "
            "alone"
        )
    finally:
        containerBystander.remove(force=True, v=True)
        dictGateway["dictHandlesById"][dictCreated["sHandle"]][
            "sContainerId"] = dictCreated["sContainerName"]


def test_an_unminted_handle_is_refused(tGatewayAndHandle):
    """A raw container id must not be usable as a handle.

    The boundary the opaque handle exists for: a caller holding an
    arbitrary Docker id cannot drive any operation at it.
    """
    dictGateway, dictCreated = tGatewayAndHandle
    for sIdentifier in ("", dictCreated["sContainerName"], "deadbeef" * 4):
        with pytest.raises(
            disposableContainer.DisposableContainerError,
            match="unknown disposable gateway handle",
        ):
            disposableContainer.fdictExecuteBoundedCommand(
                dictGateway, sIdentifier, ["/bin/sh", "-c", "true"])


def test_the_shadow_lane_seeds_a_real_container_from_a_real_repository():
    """Drive the whole shadow lane against a real daemon, end to end.

    The unit tests know the lane's decisions and the local-shell tests
    know its file handling; neither has ever seen a real container. This
    one closes that gap: a repository is read out of one live container
    with the real ``DockerConnection``, coherence-pinned by the real
    observation program, a shadow is created from a real image, the
    archive lands in it, and the comparison reads the SHADOW — with the
    two container identities distinct throughout, so a lane that quietly
    kept reading the source container cannot pass.

    The source must be a REAL git repository in an image carrying git,
    python and bash, because the export observes with git before it
    streams. It used to be a bare directory in a stock base image, which
    passed until the coherence check landed and then failed with
    "unable to find user researcher" — a message about the image, three
    layers away from the thing that changed. That is the shape of
    breakage a live lane exists to catch.

    The comparison is a stub because what is under test is the seeding,
    not the manifest arithmetic; the stub reads the shadow back through
    ``get_archive``, which runs no program and so holds for any image.
    """
    fnRequireDaemonReachable()
    from tests.testCoherentExportLive import (
        S_COHERENCE_PROBE_IMAGE, _fnRequireProbeImage as fnRequireGitImage,
    )
    from vaibify.docker.dockerConnection import DockerConnection
    from vaibify.reproducibility import shadowRerun

    dockerDisposable = (
        disposableContainer.fdockerCreateDisposableClient())
    fnRequireGitImage(dockerDisposable)
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerDisposable, S_PROBE_RESOURCE)
    dictSource = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, "source", S_COHERENCE_PROBE_IMAGE)
    listSeen = []
    try:
        _fnSeedGitRepositoryInSource(dictGateway, dictSource)
        connectionDocker = DockerConnection()

        def fdictCompare(connection, sContainerId, dictWorkflow,
                         sWorkflowPath, filesRepo, fnStatusCallback=None):
            del dictWorkflow, fnStatusCallback
            listSeen.append({
                "sContainerId": sContainerId,
                "sWorkflowPath": sWorkflowPath,
                "sRepoRoot": filesRepo.sRootPath,
                "dictMembers": _fdictDescribeArchiveMembers(
                    connection.fbaFetchDirectoryArchive(
                        sContainerId, filesRepo.sRootPath, 1 << 20)),
            })
            return {"bPassed": True, "iOutputHashesMatched": 1,
                    "iOutputHashesTotal": 1, "listDivergedHashes": []}

        dictOutcome = shadowRerun.fdictRerunInShadowContainer(
            connectionDocker, dictSource["sContainerName"],
            {"sProjectRepoPath": S_SOURCE_REPO},
            S_SOURCE_REPO + "/project.json", S_SOURCE_REPO,
            {"dictContainer": {
                "sImageDigest": S_COHERENCE_PROBE_IMAGE}},
            fdictRunAndVerify=fdictCompare,
        )
    finally:
        if dictSource["sHandle"] in dictGateway["dictHandlesById"]:
            disposableContainer.fdictDestroyAndSettle(
                dictGateway, dictSource["sHandle"])

    assert len(listSeen) == 1, "the comparison never ran"
    dictMembers = listSeen[0]["dictMembers"]
    # Members are named relative to the exported directory's PARENT,
    # so the shadow's own copy carries the repository basename.
    assert dictMembers.get("sourceRepo/alpha.txt", {}).get(
        "baContent") == b"alpha\n", (
        f"the repository did not arrive in the shadow: "
        f"{sorted(dictMembers)[:8]}"
    )
    assert "sourceRepo/nested/beta.txt" in dictMembers, (
        "a nested member did not survive the export/repack/copy round "
        f"trip: {sorted(dictMembers)}"
    )
    listWrongOwner = sorted(
        sName for sName, dictMember in dictMembers.items()
        if dictMember["tOwner"] != (1000, 1000)
    )
    assert listWrongOwner == [], (
        "the copy landed with an owner the container user is not: "
        f"{listWrongOwner[:8]}"
    )
    assert listSeen[0]["sRepoRoot"] == "/shadow/sourceRepo"
    assert listSeen[0]["sWorkflowPath"] == (
        "/shadow/sourceRepo/project.json"
    )
    assert listSeen[0]["sContainerId"] != dictSource["sContainerName"], (
        "the comparison read the SOURCE container, not the shadow"
    )
    assert dictOutcome["sShadowTeardown"] == (
        disposableSpecification.S_OUTCOME_DESTROYED
    ), dictOutcome.get("sShadowTeardownReason", "")
    dictProbe = disposableContainer.fdictProbeContainerAbsence(
        dockerDisposable, listSeen[0]["sContainerId"])
    assert dictProbe["sAnswer"] == (
        disposableSpecification.S_ABSENCE_ABSENT
    ), "the shadow container outlived the attestation it was built for"


# Not under /tmp: the disposable posture mounts a tmpfs there and the
# daemon's archive endpoint cannot read out of one -- it answers 404 for
# a directory an exec in the same container lists happily.
S_SOURCE_REPO = "/home/researcher/sourceRepo"


def _fnSeedGitRepositoryInSource(dictGateway, dictSource):
    """Commit a small real repository inside the source container."""
    sScript = (
        "set -e; mkdir -p " + S_SOURCE_REPO + "/nested; cd "
        + S_SOURCE_REPO + "; git init -q .; "
        "git symbolic-ref HEAD refs/heads/main; "
        "git config user.email probe@example.invalid; "
        "git config user.name Probe; "
        "printf 'alpha\\n' > alpha.txt; "
        "printf 'beta\\n' > nested/beta.txt; "
        "printf '{}' > project.json; "
        "git add -A; git commit -qm seed; echo SEEDED"
    )
    dictSeeded = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictSource["sHandle"],
        ["/bin/sh", "-c", sScript], fWallClockSeconds=120)
    assert "SEEDED" in dictSeeded["sOutput"], dictSeeded["sOutput"]


def _fdictDescribeArchiveMembers(baArchive):
    """Return ``{name: {tOwner, baContent}}`` for one tar archive.

    Read back through ``get_archive``, which runs no program in the
    container -- so this assertion holds for any image, including a
    stock base with no interpreter. That matters: the shadow is built
    from whatever image the envelope pins, and a probe that needed
    python inside it would be testing the fixture's image rather than
    the lane.
    """
    dictMembers = {}
    with tarfile.open(fileobj=io.BytesIO(baArchive), mode="r") as fileTar:
        for infoMember in fileTar:
            fileExtracted = (
                fileTar.extractfile(infoMember)
                if infoMember.isreg() else None
            )
            dictMembers[infoMember.name] = {
                "tOwner": (infoMember.uid, infoMember.gid),
                "baContent": (
                    fileExtracted.read() if fileExtracted else b""),
            }
    return dictMembers
