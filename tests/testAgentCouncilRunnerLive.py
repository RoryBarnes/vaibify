"""Live falsification of the Agent Council disposable-runner lifecycle.

Phase 0 acceptance (design/agentCouncil.md sections 2.6, 9.6, 15.6):
every containment property of ``vaibify/gui/agentCouncilRunner.py`` is
driven against a REAL Docker daemon and a real container, because the
claims — a ``setsid`` descendant dies with the namespace, a fork bomb
hits the PID cap, a balloon cannot spill to swap, a disk filler hits
the tmpfs bound, the absence probe distinguishes gone from error —
are exactly the kind a unit stub confirms vacuously.

Live-daemon convention (``testDockerConnectionLive``): each test skips
when no daemon answers, and ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` turns
that skip into a failure so the opt-in CI job can never report a false
green.

Container rules: throwaway only — created here from a public image,
uniquely named and labeled with a test-unique reservation id,
force-removed in teardown even on failure. No pre-existing container
is ever listed as belonging to a test, signalled, or touched. The
image is parametrized through ``VAIBIFY_COUNCIL_TEST_IMAGE`` (default
``python:3.10-slim`` — public, so CI can pull it; point it at a local
project image to rehearse the project-image case).
"""

import io
import os
import secrets
import tarfile
import time

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.gui import agentCouncilDockerGateway as moduleGateway

pytestmark = pytest.mark.docker_live

S_RUNNER_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim",
)

I_MEBIBYTE = 1024 * 1024


def _fdictSmallLimits(**dictOverrides):
    """Small, fast limits for containment tests; overridable per test."""
    from vaibify.gui.agentCouncilRunner import fdictBuildDefaultRunnerLimits
    dictLimits = fdictBuildDefaultRunnerLimits()
    dictLimits.update({
        "iMemoryBytes": 256 * I_MEBIBYTE,
        "iWorkingTreeBytes": 64 * I_MEBIBYTE,
        "iScratchBytes": 16 * I_MEBIBYTE,
        "iPidsLimit": 64,
    })
    dictLimits.update(dictOverrides)
    return dictLimits


@pytest.fixture
def tCouncilHarness():
    """Yield (moduleRunner, dictGateway, listCreatedContainerIds).

    Every SDK-touching operation goes through the council Docker
    gateway (remediation R4); the runner module supplies only the
    constants and pure helpers the assertions read. The raw client is
    reachable as ``dictGateway["dockerCouncil"]`` for the inspect-side
    assertions.
    """
    fnRequireDaemonReachable()
    from vaibify.gui import agentCouncilDockerGateway as moduleGateway
    from vaibify.gui import agentCouncilRegistry as moduleRegistry
    from vaibify.gui import agentCouncilRunner as moduleRunner
    dockerCouncil = moduleGateway.fdockerCreateCouncilClient()
    dictGateway = moduleGateway.fdictCreateCouncilDockerGateway(
        dockerCouncil, moduleRegistry.fdictCreateCouncilRegistry())
    listCreatedContainerIds = []
    try:
        yield (moduleRunner, dictGateway, listCreatedContainerIds)
    finally:
        for sContainerId in listCreatedContainerIds:
            try:
                dockerCouncil.api.remove_container(
                    sContainerId, force=True, v=True,
                )
            except Exception:
                pass


def _fdictCreateTracked(tCouncilHarness, dictLimits=None, bSandbox=False):
    """Create a runner through the gateway; return its handle record.

    The returned dict carries ``sHandle`` (the gateway's opaque key for
    every subsequent operation) plus the container id and reservation
    id the assertions read. The container id is registered for
    teardown even on failure.
    """
    from vaibify.gui import agentCouncilDockerGateway as moduleGateway
    _, dictGateway, listCreatedContainerIds = tCouncilHarness
    dictEffectiveLimits = (dictLimits if dictLimits is not None
                           else _fdictSmallLimits())
    dictCreated = moduleGateway.fdictReserveAndCreateRunner(
        dictGateway, f"livetest{secrets.token_hex(4)}", "claude",
        {"iMemoryBytes": dictEffectiveLimits["iMemoryBytes"],
         "fCpuCount": dictEffectiveLimits["fCpuCount"]},
        S_RUNNER_TEST_IMAGE,
        dictLimits=dictEffectiveLimits,
        bSandbox=bSandbox,
    )
    assert dictCreated["bCreated"] is True, dictCreated
    sHandle = dictCreated["sHandle"]
    dictHandle = dictGateway["dictHandlesById"][sHandle]
    listCreatedContainerIds.append(dictHandle["sContainerId"])
    return {
        "sHandle": sHandle,
        "sContainerId": dictHandle["sContainerId"],
        "sContainerName": dictHandle["sContainerName"],
        "sReservationId": dictHandle["sReservationId"],
        "sRole": dictHandle["sRole"],
    }


def _fbaBuildSnapshotTarball(iFileCount, iFileSizeBytes, bRootOwned=False):
    """Build an in-memory snapshot tarball of deterministic files.

    ``bRootOwned`` builds every entry uid 0 / uname ``root`` — the
    exact input that once shipped the root-owned-copy bug — so a test
    can prove the runner path neutralizes it.
    """
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for iIndex in range(iFileCount):
            infoMember = tarfile.TarInfo(
                name=f"snapshot/file{iIndex:04d}.dat",
            )
            baContent = (b"%08d" % iIndex) * (iFileSizeBytes // 8)
            infoMember.size = len(baContent)
            infoMember.mtime = int(time.time())
            if bRootOwned:
                infoMember.uid = 0
                infoMember.gid = 0
                infoMember.uname = "root"
                infoMember.gname = "root"
            fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferTar.getvalue()


# The deterministic test-owned "provider executable": forks a
# deliberately setsid-detached, SIGTERM/SIGINT/SIGHUP-ignoring
# descendant that records its PID, then the parent exits at once. The
# falsification of process-exit-as-containment. The child re-points its
# stdio at /dev/null: an inherited exec stdout would hold the exec
# stream open and make the PARENT's turn look alive for 300 seconds.
S_DETACHED_DESCENDANT_SCRIPT = """
import os, signal, time
iChildPid = os.fork()
if iChildPid == 0:
    os.setsid()
    iDevNull = os.open("/dev/null", os.O_RDWR)
    os.dup2(iDevNull, 0)
    os.dup2(iDevNull, 1)
    os.dup2(iDevNull, 2)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    with open("/council/descendant.pid", "w") as fileMarker:
        fileMarker.write(str(os.getpid()))
    time.sleep(300)
    os._exit(0)
print("PARENT_EXITING", flush=True)
os._exit(0)
"""

S_DESCENDANT_PROBE_COMMAND = (
    'iPid=$(cat /council/descendant.pid) && '
    'kill -0 "$iPid" 2>/dev/null && echo DESCENDANT_ALIVE; '
    'kill -TERM "$iPid" 2>/dev/null; sleep 1; '
    'kill -0 "$iPid" 2>/dev/null && echo DESCENDANT_SURVIVED_SIGTERM'
)


def test_detached_descendant_survives_parent_exit_and_dies_with_namespace(
        tCouncilHarness):
    """Process exit is not containment; namespace destruction is.

    The provider parent exits cleanly while its setsid-detached,
    SIGTERM-ignoring descendant lives on — proving that 'the CLI
    exited' can never be the quietness claim. Destroying the container
    and proving absence is what actually ends the descendant: its PID
    namespace no longer exists.
    """
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictRunner = _fdictCreateTracked(tCouncilHarness)
    sHandle = dictRunner["sHandle"]

    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["python3", "-c", S_DETACHED_DESCENDANT_SCRIPT],
        fWallClockSeconds=30.0,
    )
    assert dictTurn["iExitCode"] == 0, dictTurn
    assert "PARENT_EXITING" in dictTurn["sOutput"]

    dictProbeTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["/bin/sh", "-c", S_DESCENDANT_PROBE_COMMAND],
        fWallClockSeconds=30.0,
    )
    assert "DESCENDANT_ALIVE" in dictProbeTurn["sOutput"], (
        "The detached descendant should have outlived its parent; if "
        "it did not, process exit would look like containment and this "
        "falsification has lost its adversary: " + dictProbeTurn["sOutput"]
    )
    assert "DESCENDANT_SURVIVED_SIGTERM" in dictProbeTurn["sOutput"], (
        "The descendant should ignore SIGTERM; a polite child proves "
        "nothing about a hostile one: " + dictProbeTurn["sOutput"]
    )

    dictDestroyed = moduleGateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED, (
        dictDestroyed
    )
    assert dictDestroyed["dictProbe"]["sAnswer"] == (
        moduleRunner.S_ABSENCE_ABSENT
    )


S_FORK_BOMB_SCRIPT = """
import os, time
iForkCount = 0
try:
    while True:
        iChildPid = os.fork()
        if iChildPid == 0:
            iDevNull = os.open("/dev/null", os.O_RDWR)
            os.dup2(iDevNull, 0)
            os.dup2(iDevNull, 1)
            os.dup2(iDevNull, 2)
            time.sleep(8)
            os._exit(0)
        iForkCount += 1
except OSError:
    print("FORK_REFUSED_AT", iForkCount)
"""


def _fdictProbeResponsiveWithRetry(dictGateway, sHandle,
                                   fBudgetSeconds=45.0):
    """Echo inside the container, retrying while the PID budget drains."""
    fDeadlineMonotonic = time.monotonic() + fBudgetSeconds
    while True:
        try:
            dictProbe = moduleGateway.fdictExecuteBoundedTurn(
                dictGateway, sHandle,
                ["/bin/sh", "-c", "echo STILL_RESPONSIVE"],
                fWallClockSeconds=15.0,
            )
            if "STILL_RESPONSIVE" in dictProbe["sOutput"]:
                return dictProbe
        except Exception:
            pass
        if time.monotonic() >= fDeadlineMonotonic:
            raise AssertionError(
                "the container never answered a follow-up turn after "
                "the fork bomb's children expired"
            )
        time.sleep(1.0)


def test_fork_bomb_hits_pids_limit_and_harms_only_its_turn(tCouncilHarness):
    """A fork bomb is stopped by pids_limit inside its own namespace."""
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictRunner = _fdictCreateTracked(
        tCouncilHarness, dictLimits=_fdictSmallLimits(iPidsLimit=64),
    )
    sHandle = dictRunner["sHandle"]

    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["python3", "-c", S_FORK_BOMB_SCRIPT],
        fWallClockSeconds=60.0,
    )
    assert "FORK_REFUSED_AT" in dictTurn["sOutput"], dictTurn
    iForkCount = int(dictTurn["sOutput"].split("FORK_REFUSED_AT")[1].split()[0])
    assert 0 < iForkCount < 64, iForkCount

    # "Harms only its own turn": once the bomb's children expire, the
    # same container answers a fresh turn — and destruction settles.
    _fdictProbeResponsiveWithRetry(dictGateway, sHandle)
    dictDestroyed = moduleGateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED


S_MEMORY_BALLOON_SCRIPT = """
listBalloon = []
for iRound in range(64):
    listBalloon.append(bytearray(16 * 1024 * 1024))
print("BALLOON_COMPLETED")
"""


def test_memory_balloon_is_killed_with_swap_pinned(tCouncilHarness):
    """A 1GiB balloon under a 256MiB limit dies; swap cannot absorb it.

    The config half: HostConfig.MemorySwap equals HostConfig.Memory, so
    the cgroup has zero swap allowance. The behavior half: were swap
    available to spill into, the balloon would complete and print its
    marker; it must not.
    """
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictRunner = _fdictCreateTracked(
        tCouncilHarness,
        dictLimits=_fdictSmallLimits(iMemoryBytes=256 * I_MEBIBYTE),
    )
    sContainerId = dictRunner["sContainerId"]
    sHandle = dictRunner["sHandle"]

    dictAttrs = dictGateway["dockerCouncil"].api.inspect_container(
        sContainerId)
    assert dictAttrs["HostConfig"]["Memory"] == 256 * I_MEBIBYTE
    assert dictAttrs["HostConfig"]["MemorySwap"] == 256 * I_MEBIBYTE, (
        "memswap must be pinned to the memory limit so pressure cannot "
        "spill to swap"
    )

    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["python3", "-c", S_MEMORY_BALLOON_SCRIPT],
        fWallClockSeconds=120.0,
    )
    assert "BALLOON_COMPLETED" not in dictTurn["sOutput"], (
        "the balloon completed: the memory limit did not bind, or swap "
        "absorbed the pressure"
    )
    assert dictTurn["iExitCode"] != 0, dictTurn

    dictDestroyed = moduleGateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED


S_DISK_FILL_SCRIPT = """
iWrittenBytes = 0
baChunk = b"x" * (1024 * 1024)
try:
    with open("/council/fill.bin", "wb") as fileFill:
        while iWrittenBytes < 256 * 1024 * 1024:
            fileFill.write(baChunk)
            fileFill.flush()
            iWrittenBytes += len(baChunk)
    print("DISK_FILL_COMPLETED")
except OSError:
    print("DISK_REFUSED_AT", iWrittenBytes)
"""


def test_disk_filling_script_hits_the_tmpfs_bound(tCouncilHarness):
    """The selected disk bound (read-only rootfs + sized tmpfs) binds.

    A writer aiming for 256MiB on a 64MiB working tree is refused with
    ENOSPC near the tmpfs size, far below its target — and the rootfs
    refuses writes entirely, so there is no unbounded surface left.
    """
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictRunner = _fdictCreateTracked(
        tCouncilHarness,
        dictLimits=_fdictSmallLimits(
            iMemoryBytes=256 * I_MEBIBYTE,
            iWorkingTreeBytes=64 * I_MEBIBYTE,
        ),
    )
    sHandle = dictRunner["sHandle"]

    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["python3", "-c", S_DISK_FILL_SCRIPT],
        fWallClockSeconds=120.0,
    )
    assert "DISK_REFUSED_AT" in dictTurn["sOutput"], dictTurn
    iRefusedAtBytes = int(
        dictTurn["sOutput"].split("DISK_REFUSED_AT")[1].split()[0]
    )
    assert iRefusedAtBytes <= 65 * I_MEBIBYTE, (
        f"the writer placed {iRefusedAtBytes} bytes on a 64MiB tmpfs"
    )

    dictRootfs = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["/bin/sh", "-c",
         "touch /usr/blocked 2>/dev/null && echo ROOTFS_WRITABLE "
         "|| echo ROOTFS_REFUSED"],
        fWallClockSeconds=30.0,
    )
    assert "ROOTFS_REFUSED" in dictRootfs["sOutput"], (
        "the rootfs must be read-only or the disk bound has an "
        "unbounded bypass"
    )
    dictDestroyed = moduleGateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED


def test_runner_has_no_mounts_no_socket_and_a_1000_owned_editable_snapshot(
        tCouncilHarness):
    """No host mount of any kind; the snapshot copy is editable at 1000.

    The snapshot tarball is built deliberately ROOT-owned — the input
    that once shipped the root-owned-copy bug — and must land owned by
    the unprivileged user, appendable by the very identity the CLI will
    run as.
    """
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictRunner = _fdictCreateTracked(tCouncilHarness)
    sContainerId = dictRunner["sContainerId"]
    sHandle = dictRunner["sHandle"]

    dictAttrs = dictGateway["dockerCouncil"].api.inspect_container(
        sContainerId)
    dictHostConfig = dictAttrs["HostConfig"]
    assert not dictHostConfig["Binds"], dictHostConfig["Binds"]
    assert dictAttrs["Mounts"] == [], dictAttrs["Mounts"]
    assert set(dictHostConfig["Tmpfs"].keys()) == {"/council", "/tmp"}
    assert dictHostConfig["ReadonlyRootfs"] is True
    assert dictHostConfig["Privileged"] is False
    assert not dictHostConfig["Devices"]
    assert dictHostConfig["CapDrop"] == ["ALL"]
    assert "no-new-privileges:true" in dictHostConfig["SecurityOpt"]
    assert dictHostConfig["NetworkMode"] == "none"
    assert dictHostConfig["IpcMode"] == "private"
    assert dictHostConfig["PidMode"] in ("", "private", None)
    assert dictAttrs["Config"]["User"] == "1000:1000"
    sSerializedHostConfig = repr(dictHostConfig)
    assert "docker.sock" not in sSerializedHostConfig

    moduleGateway.fnCopySnapshotIntoRunner(
        dictGateway, sHandle,
        _fbaBuildSnapshotTarball(4, 4096, bRootOwned=True),
    )
    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["/bin/sh", "-c",
         "id -u; "
         "python3 -c \"import os; "
         "statSnapshot = os.stat('/council/snapshot/file0000.dat'); "
         "print('OWNER', statSnapshot.st_uid, statSnapshot.st_gid)\"; "
         "echo appended >> /council/snapshot/file0000.dat "
         "&& echo EDIT_OK || echo EDIT_REFUSED"],
        fWallClockSeconds=30.0,
    )
    assert "OWNER 1000 1000" in dictTurn["sOutput"], (
        "a root-owned snapshot tarball must land owned by the "
        "unprivileged user: " + dictTurn["sOutput"]
    )
    assert "EDIT_OK" in dictTurn["sOutput"], (
        "the CLI's own identity must be able to edit its snapshot "
        "copy: " + dictTurn["sOutput"]
    )


S_NETWORK_PROBE_SCRIPT = """
import socket
try:
    connectionProbe = socket.create_connection(("1.1.1.1", 443), timeout=3)
    print("NETWORK_REACHED")
except OSError as error:
    print("NETWORK_REFUSED", type(error).__name__)
try:
    socket.getaddrinfo("example.com", 443)
    print("DNS_RESOLVED")
except OSError:
    print("DNS_REFUSED")
"""


def test_sandbox_variant_reaches_no_network_at_all(tCouncilHarness):
    """A sandbox has network_mode none and cannot dial or resolve."""
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictSandbox = _fdictCreateTracked(tCouncilHarness, bSandbox=True)
    sContainerId = dictSandbox["sContainerId"]
    sHandle = dictSandbox["sHandle"]
    assert dictSandbox["sRole"] == moduleRunner.S_ROLE_SANDBOX

    dictAttrs = dictGateway["dockerCouncil"].api.inspect_container(
        sContainerId)
    assert dictAttrs["HostConfig"]["NetworkMode"] == "none"

    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, sHandle,
        ["python3", "-c", S_NETWORK_PROBE_SCRIPT],
        fWallClockSeconds=60.0,
    )
    assert "NETWORK_REFUSED" in dictTurn["sOutput"], dictTurn
    assert "NETWORK_REACHED" not in dictTurn["sOutput"]
    assert "DNS_REFUSED" in dictTurn["sOutput"]

    with pytest.raises(ValueError):
        moduleRunner.fdictComposeRunnerCreateSpecification(
            S_RUNNER_TEST_IMAGE, "refused-reservation",
            bSandbox=True, sNetworkName="bridge",
        )


def test_turn_output_cap_and_wall_clock_budget_bind(tCouncilHarness):
    """Both per-turn bounds are enforced host-side and end the runner."""
    moduleRunner, dictGateway, _ = tCouncilHarness

    dictFlooder = _fdictCreateTracked(tCouncilHarness)
    dictFlood = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, dictFlooder["sHandle"],
        ["/bin/sh", "-c", "while :; do echo flooding-the-output; done"],
        iOutputByteCap=65536, fWallClockSeconds=60.0,
    )
    assert dictFlood["bOutputCapExceeded"] is True
    assert len(dictFlood["sOutput"].encode()) <= 65536
    dictDestroyedFlooder = moduleGateway.fdictDestroyAndSettle(
        dictGateway, dictFlooder["sHandle"],
    )
    assert dictDestroyedFlooder["sOutcome"] == (
        moduleRunner.S_OUTCOME_DESTROYED
    )

    dictSleeper = _fdictCreateTracked(tCouncilHarness)
    fStartedMonotonic = time.monotonic()
    dictSleep = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, dictSleeper["sHandle"],
        ["/bin/sh", "-c", "sleep 60"],
        fWallClockSeconds=3.0,
    )
    fElapsedSeconds = time.monotonic() - fStartedMonotonic
    assert dictSleep["bWallClockExceeded"] is True
    assert fElapsedSeconds < 15.0, fElapsedSeconds
    dictDestroyedSleeper = moduleGateway.fdictDestroyAndSettle(
        dictGateway, dictSleeper["sHandle"],
    )
    assert dictDestroyedSleeper["sOutcome"] == (
        moduleRunner.S_OUTCOME_DESTROYED
    )


def test_absence_probe_distinguishes_gone_from_daemon_error(
        tCouncilHarness):
    """Absent is a positive 404; a daemon fault is never completion."""
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictRunner = _fdictCreateTracked(tCouncilHarness)
    sContainerId = dictRunner["sContainerId"]
    sHandle = dictRunner["sHandle"]

    dictPresent = moduleGateway.fdictProbeRunnerAbsence(
        dictGateway["dockerCouncil"], sContainerId,
    )
    assert dictPresent["sAnswer"] == moduleRunner.S_ABSENCE_PRESENT

    dictDestroyed = moduleGateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED
    dictAbsent = moduleGateway.fdictProbeRunnerAbsence(
        dictGateway["dockerCouncil"], sContainerId,
    )
    assert dictAbsent["sAnswer"] == moduleRunner.S_ABSENCE_ABSENT

    import docker
    dockerUnreachable = docker.DockerClient(
        base_url="unix:///nonexistent/never-a-daemon.sock",
        version="1.41", timeout=2,
    )
    dictIndeterminate = moduleGateway.fdictProbeRunnerAbsence(
        dockerUnreachable, sContainerId,
    )
    assert dictIndeterminate["sAnswer"] == (
        moduleRunner.S_ABSENCE_INDETERMINATE
    ), (
        "a daemon transport error must never be read as absence: "
        + repr(dictIndeterminate)
    )
    dictQuarantined = moduleGateway.fdictDestroyRunnerAndProveAbsence(
        dockerUnreachable, sContainerId,
    )
    assert dictQuarantined["sOutcome"] == (
        moduleRunner.S_OUTCOME_QUARANTINED
    ), (
        "an indeterminate teardown must quarantine, never settle: "
        + repr(dictQuarantined)
    )


def test_crash_discovery_finds_abandoned_labeled_runner_and_settles_it(
        tCouncilHarness):
    """A restarted hub can discover a labeled survivor and settle it."""
    moduleRunner, dictGateway, _ = tCouncilHarness
    dictAbandoned = _fdictCreateTracked(tCouncilHarness)
    sReservationId = dictAbandoned["sReservationId"]

    # Simulate the hub crash: forget the handle, build a fresh client
    # as a restarted process would, and rediscover by label alone.
    dockerRestarted = moduleGateway.fdockerCreateCouncilClient()
    listDiscovered = moduleGateway.flistDiscoverLabeledRunners(
        dockerRestarted,
    )
    listMatching = [
        dictFound for dictFound in listDiscovered
        if dictFound["sReservationId"] == sReservationId
    ]
    assert len(listMatching) == 1, (
        f"expected exactly one survivor for {sReservationId}: "
        f"{listDiscovered}"
    )
    assert listMatching[0]["sContainerId"] == dictAbandoned["sContainerId"]
    assert listMatching[0]["sRole"] == moduleRunner.S_ROLE_RUNNER

    dictDestroyed = moduleGateway.fdictDestroyRunnerAndProveAbsence(
        dockerRestarted, listMatching[0]["sContainerId"],
    )
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED

    listAfter = moduleGateway.flistDiscoverLabeledRunners(dockerRestarted)
    assert not any(
        dictFound["sReservationId"] == sReservationId
        for dictFound in listAfter
    ), "the settled runner must no longer be discoverable"


def test_lifecycle_timing_reproduces_containment_measurement(
        tCouncilHarness, capsys):
    """Automated re-measurement of the 2026-08-07 lifecycle timing.

    Create, copy a realistic multi-file snapshot in, execute a turn
    that leaves a deliberately setsid-detached child behind, destroy,
    prove absence — printing each phase's duration for the record and
    asserting a generous total ceiling.
    """
    moduleRunner, dictGateway, listCreatedContainerIds = tCouncilHarness
    baSnapshotTar = _fbaBuildSnapshotTarball(120, 256 * 1024)

    fCreateStarted = time.monotonic()
    dictRunner = _fdictCreateTracked(tCouncilHarness)
    fCreateSeconds = time.monotonic() - fCreateStarted

    fCopyStarted = time.monotonic()
    moduleGateway.fnCopySnapshotIntoRunner(
        dictGateway, dictRunner["sHandle"], baSnapshotTar,
    )
    fCopySeconds = time.monotonic() - fCopyStarted

    fExecuteStarted = time.monotonic()
    dictTurn = moduleGateway.fdictExecuteBoundedTurn(
        dictGateway, dictRunner["sHandle"],
        ["python3", "-c", S_DETACHED_DESCENDANT_SCRIPT],
        fWallClockSeconds=60.0,
    )
    fExecuteSeconds = time.monotonic() - fExecuteStarted
    assert dictTurn["iExitCode"] == 0, dictTurn

    fDestroyStarted = time.monotonic()
    dictDestroyed = moduleGateway.fdictDestroyAndSettle(
        dictGateway, dictRunner["sHandle"],
    )
    fDestroySeconds = time.monotonic() - fDestroyStarted
    assert dictDestroyed["sOutcome"] == moduleRunner.S_OUTCOME_DESTROYED

    fProbeStarted = time.monotonic()
    dictProbe = moduleGateway.fdictProbeRunnerAbsence(
        dictGateway["dockerCouncil"], dictRunner["sContainerId"],
    )
    fProbeSeconds = time.monotonic() - fProbeStarted
    assert dictProbe["sAnswer"] == moduleRunner.S_ABSENCE_ABSENT

    fTotalSeconds = (
        fCreateSeconds + fCopySeconds + fExecuteSeconds
        + fDestroySeconds + fProbeSeconds
    )
    iSnapshotBytes = len(baSnapshotTar)
    with capsys.disabled():
        print(
            "\n[council lifecycle timing] image=%s snapshot=%.1fMiB "
            "create+start=%.2fs copy+extract=%.2fs execute=%.2fs "
            "destroy(with detached child)=%.2fs absence-probe=%.2fs "
            "TOTAL=%.2fs"
            % (
                S_RUNNER_TEST_IMAGE, iSnapshotBytes / I_MEBIBYTE,
                fCreateSeconds, fCopySeconds, fExecuteSeconds,
                fDestroySeconds, fProbeSeconds, fTotalSeconds,
            )
        )
    assert fTotalSeconds < 60.0, (
        f"full lifecycle took {fTotalSeconds:.1f}s; the design premise "
        "is that per-turn disposal is negligible next to model latency"
    )
