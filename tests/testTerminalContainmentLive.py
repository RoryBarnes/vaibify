"""Real-container falsification for terminal containment (cases 43-45).

The browser fake cannot prove Docker exec termination (design v13
§6.1), so these tests drive REAL terminal execs in a throwaway
container: a shell detaches a signal-trapping descendant, the exec
dies, and only the group prover can tell whether anything survived.
Case 44's transfer and expiry halves land with slices 5 and 6; case
43's transfer-commit half lands with slice 5 — the registry entries
say so.

Live-daemon convention (``testDockerConnectionLive``): each test
skips when no daemon answers, and ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
turns that skip into a failure so the opt-in CI job can never report
a false green. Kill-confirmation of these entries therefore REQUIRES
a reachable daemon — without one the tests skip and a mutant would
survive vacuously; each registry entry documents this.

Container rules: throwaway only — created here from a small public
image, uniquely named, force-removed in teardown even on failure.
No pre-existing container is ever listed, signalled, or touched.
"""

import asyncio
import secrets
import time
from types import SimpleNamespace

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable

pytestmark = pytest.mark.docker_live

S_THROWAWAY_IMAGE = "alpine:3.20"
S_LEAKED_MARKER_PATH = "/tmp/leaked"
F_DESCENDANT_SLEEP_SECONDS = 8.0

# The adversarial shell line of case 43/44: a detached descendant that
# ignores every catchable signal, outlives the exec, and writes a
# marker file if it is ever allowed to finish its sleep.
S_DETACH_TRAPPING_DESCENDANT_LINE = (
    'nohup sh -c "trap \'\' HUP INT QUIT TERM; '
    f'sleep {int(F_DESCENDANT_SLEEP_SECONDS)}; '
    f'touch {S_LEAKED_MARKER_PATH}" >/dev/null 2>&1 &\n'
)


@pytest.fixture
def tLiveContainer():
    """Yield (sName, sContainerId, connection) for a throwaway container."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    clientDocker = docker.from_env()
    sName = f"vaibifyTermContain{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_THROWAWAY_IMAGE, ["sleep", "300"], name=sName, detach=True,
    )
    try:
        yield (sName, container.id, DockerConnection())
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


def _fsessionStartContainedTerminal(tLiveContainer, appState):
    """Start a containment-wired /bin/sh terminal in the container."""
    from vaibify.gui.terminalSession import TerminalSession
    sName, sContainerId, connectionDocker = tLiveContainer
    session = TerminalSession(
        connectionDocker, sContainerId, sUser="root",
        sShellCommand="/bin/sh",
        dictContainment={
            "appState": appState, "sContainerName": sName,
            "iOwnerGeneration": 1,
        },
    )
    session.fnStart()
    return session


def _fnSpawnTrappingDescendant(session, tLiveContainer):
    """Type the detach line into the live shell; wait until it exists."""
    sName, sContainerId, connectionDocker = tLiveContainer
    iProcessGroup = session.recordContainment.iProcessGroup
    session.fnSendInput(
        S_DETACH_TRAPPING_DESCENDANT_LINE.encode("utf-8"),
    )
    fDeadline = time.monotonic() + 10.0
    while time.monotonic() < fDeadline:
        dictProbe = connectionDocker.fdictProbeProcessGroupMembers(
            sContainerId, iProcessGroup,
        )
        if dictProbe["bConclusive"] and dictProbe["iMemberCount"] >= 2:
            return
        time.sleep(0.2)
    pytest.fail(
        "the detached descendant never appeared in the terminal's "
        "session within 10 seconds"
    )


def _fbLeakedMarkerExists(tLiveContainer):
    """Independently check the descendant's marker file, root exec."""
    sName, sContainerId, connectionDocker = tLiveContainer
    iExitCode, _ = connectionDocker.ftupleRunRootShellProbe(
        sContainerId, f"test -e {S_LEAKED_MARKER_PATH}",
    )
    return iExitCode == 0


def _fdictJournalOperations(sName):
    from vaibify.config import operationJournal
    return operationJournal.fdictReadJournalOutcome(sName)[
        "dictOperations"
    ]


def _fnAssertDrainedHonestly(tLiveContainer, fSpawnedMonotonic):
    """The case-44 oracle: proven-dead descendant OR quarantined record.

    Never a clean outcome with a live group: an empty journal demands
    the descendant actually died (no marker file appears even after
    its sleep window), and a non-empty journal must be an explicit
    ``NEEDS_RECONCILIATION`` quarantine, never a live-looking record.
    """
    sName, sContainerId, connectionDocker = tLiveContainer
    dictOperations = _fdictJournalOperations(sName)
    if dictOperations:
        for dictRecord in dictOperations.values():
            assert dictRecord["sState"] == "NEEDS_RECONCILIATION", (
                "an authority-ending path left a terminal record in "
                f"state {dictRecord['sState']}: neither settled nor "
                "retained-and-quarantined"
            )
        return
    fWindowEnd = fSpawnedMonotonic + F_DESCENDANT_SLEEP_SECONDS + 1.5
    while time.monotonic() < fWindowEnd:
        time.sleep(0.2)
    assert not _fbLeakedMarkerExists(tLiveContainer), (
        "the journal settled clean, but the detached descendant "
        "survived and wrote its marker — a clean release over a live "
        "group"
    )


# ---------------------------------------------------------------------
# Case 43 (groundwork half): the prover primitive itself.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_prover_reports_survivors_after_exec_inspect_says_dead(
    tLiveContainer,
):
    """``Running == false`` while the group is provably NOT empty.

    Case 43, groundwork half (the transfer-commit half lands with
    slice 5): a real detached, signal-trapping descendant survives its
    exec — the daemon reports the exec exited while the group prover
    reports a live member, and the journal's terminal probe therefore
    quarantines instead of settling. This is the exact codex-round-12
    hole, demonstrated against a real daemon.

    Kills: hardcoding ``fdictProbeProcessGroupMembers``'s member count
    to zero (the prover that agrees with ``exec_inspect``).
    Kill-confirmation requires a reachable Docker daemon; without one
    this test skips and the mutant survives vacuously.
    """
    from vaibify.config import operationJournal
    sName, sContainerId, connectionDocker = tLiveContainer
    appState = SimpleNamespace()
    session = _fsessionStartContainedTerminal(tLiveContainer, appState)
    _fnSpawnTrappingDescendant(session, tLiveContainer)
    session.fnSendInput(b"exit\n")
    fDeadline = time.monotonic() + 10.0
    while time.monotonic() < fDeadline:
        if connectionDocker.fdictInspectExec(
            session.recordContainment.sDockerExecId,
        ).get("Running") is False:
            break
        time.sleep(0.2)
    dictInspect = connectionDocker.fdictInspectExec(
        session.recordContainment.sDockerExecId,
    )
    assert dictInspect.get("Running") is False, (
        "the shell never exited; the scenario needs a dead exec over "
        "a live descendant"
    )
    dictProbe = connectionDocker.fdictProbeProcessGroupMembers(
        sContainerId, session.recordContainment.iProcessGroup,
    )
    assert dictProbe["bConclusive"] and dictProbe["iMemberCount"] >= 1, (
        "exec_inspect reports the exec dead, so the prover MUST still "
        f"see the trapped descendant; probe said {dictProbe}"
    )
    dictResolution = operationJournal.fdictResolveContainerJournal(
        sName, connectionDocker=connectionDocker,
    )
    assert dictResolution["sResolution"] == "QUARANTINED"
    from vaibify.gui import terminalContainment
    terminalContainment.fnDrainSessionRecord(session)


# ---------------------------------------------------------------------
# Case 44: every authority-ending path that exists today, real halves.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_release_kills_the_detached_descendant_or_quarantines(
    tLiveContainer,
):
    """Explicit release terminates-and-proves against a real container.

    Case 44, release half (the transfer and expiry halves land with
    slices 5 and 6): release must leave the descendant provably dead
    (its marker never appears) or the record retained-and-quarantined
    — never a clean release with a live group.

    Kills: disabling the terminal drain in
    ``sessionLifecycle.fbReleaseExplicit`` (the ``if`` guard forced
    False). Kill-confirmation requires a reachable Docker daemon;
    without one this test skips and the mutant survives vacuously.
    """
    from vaibify.gui import sessionLifecycle
    from vaibify.gui.containerOwnership import OwnerRecord
    sName, sContainerId, connectionDocker = tLiveContainer
    appState = SimpleNamespace(
        dictContainerOwners={}, dictSessionOwner={},
    )
    session = _fsessionStartContainedTerminal(tLiveContainer, appState)
    appState.dictContainerOwners[sName] = OwnerRecord(
        sLeaseId="lease-live", fileHandleLock=None,
        sContainerId=sContainerId, sBrowserSessionId="sess-live",
    )
    _fnSpawnTrappingDescendant(session, tLiveContainer)
    fSpawnedMonotonic = time.monotonic()
    bReleased = asyncio.run(
        sessionLifecycle.fbReleaseExplicit(
            appState, sName, "lease-live",
            sBrowserSessionId="sess-live",
        ),
    )
    assert bReleased is True
    assert not appState.dictTerminalExecutionRecords.get(sName), (
        "release left a live terminal record behind"
    )
    _fnAssertDrainedHonestly(tLiveContainer, fSpawnedMonotonic)


@pytest.mark.falsification
def test_reaper_kills_the_detached_descendant_or_quarantines(
    tLiveContainer,
):
    """The safe reaper drains a dead session's real terminal first.

    Case 44, reaping half: an orphaned-looking owner whose terminal
    detached a signal-trapping descendant is only released once the
    group is proven empty or the record quarantines.

    Kills: reverting ``_fnReapIdleOwnershipsForApp`` to the pre-3d
    reap (no terminal drain, no terminal veto). Kill-confirmation
    requires a reachable Docker daemon; without one this test skips
    and the mutant survives vacuously.
    """
    from vaibify.gui import serverLifespan
    from vaibify.gui.containerOwnership import OwnerRecord
    from unittest.mock import MagicMock
    sName, sContainerId, connectionDocker = tLiveContainer
    appState = SimpleNamespace(
        dictContainerOwners={}, dictSessionOwner={},
        dictMutationSupervisors={}, dictDurableTaskRecords={},
        bReapOwnerships=True,
    )
    session = _fsessionStartContainedTerminal(tLiveContainer, appState)
    recordOwner = OwnerRecord(
        sLeaseId="lease-reap", fileHandleLock=None,
        sContainerId=sContainerId,
    )
    recordOwner.fLastSeenMonotonic = time.monotonic() - 99999.0
    appState.dictContainerOwners[sName] = recordOwner
    _fnSpawnTrappingDescendant(session, tLiveContainer)
    fSpawnedMonotonic = time.monotonic()
    app = SimpleNamespace(state=appState)
    dictCtx = {"docker": MagicMock(flistGetRunningContainers=lambda: [])}
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert sName not in appState.dictContainerOwners, (
        "the drained (settled or quarantined) owner should have reaped"
    )
    _fnAssertDrainedHonestly(tLiveContainer, fSpawnedMonotonic)


@pytest.mark.falsification
def test_shutdown_drain_kills_the_detached_descendant_or_quarantines(
    tLiveContainer,
):
    """Hub shutdown drains a real terminal before any flock could free.

    Case 44, shutdown half: the §8 drain hook treats the live terminal
    record like live mutation work — after it runs, the group is
    proven empty or the record is quarantined, never silently live.

    Kills: dropping the terminal drain from ``appFactory``'s
    ``fnDrainGuardedMutations`` shutdown hook. Kill-confirmation
    requires a reachable Docker daemon; without one this test skips
    and the mutant survives vacuously.
    """
    from vaibify.gui import appFactory
    sName, sContainerId, connectionDocker = tLiveContainer
    appState = SimpleNamespace(
        listLifespanShutdown=[], dictMutationSupervisors={},
        dictDurableTaskRecords={}, bMutationAdmissionsClosed=False,
    )
    session = _fsessionStartContainedTerminal(tLiveContainer, appState)
    _fnSpawnTrappingDescendant(session, tLiveContainer)
    fSpawnedMonotonic = time.monotonic()
    app = SimpleNamespace(state=appState)
    appFactory._fnRegisterShutdownDrainGuardedMutations(app)
    asyncio.run(appState.listLifespanShutdown[0](app))
    assert not appState.dictTerminalExecutionRecords.get(sName), (
        "shutdown left a live terminal record behind"
    )
    _fnAssertDrainedHonestly(tLiveContainer, fSpawnedMonotonic)


# ---------------------------------------------------------------------
# Case 45 (terminal cardinality half): the journal is a SET.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_two_real_terminals_and_a_pipeline_record_settle_independently(
    tLiveContainer,
):
    """Settling one terminal leaves its siblings' records live.

    Case 45, terminal-cardinality half: two REAL terminal execs and a
    pipeline exec record coexist as distinct records in one
    container's journal set; terminate-and-prove on the first settles
    exactly one.

    Kills: making ``fnSettleOperation`` clear the container's whole
    record set instead of the one settled operation.
    Kill-confirmation requires a reachable Docker daemon; without one
    this test skips and the mutant survives vacuously.
    """
    from vaibify.config import operationJournal
    from vaibify.gui import terminalContainment
    sName, sContainerId, connectionDocker = tLiveContainer
    appState = SimpleNamespace()
    sessionFirst = _fsessionStartContainedTerminal(
        tLiveContainer, appState,
    )
    sessionSecond = _fsessionStartContainedTerminal(
        tLiveContainer, appState,
    )
    sPipelineExecId = connectionDocker.fsExecCreate(
        sContainerId, sUser="root", listCommand=["/bin/sh", "-c", "true"],
    )
    sPipelineOperationId = operationJournal.fsPrepareOperation(
        sName, "exec", sContainerId,
    )
    operationJournal.fnPromoteOperationToInFlight(
        sName, sPipelineOperationId, {"sDockerExecId": sPipelineExecId},
    )
    assert len(_fdictJournalOperations(sName)) == 3
    dictOutcome = terminalContainment.fnDrainSessionRecord(sessionFirst)
    assert dictOutcome["bProvenEmpty"] is True, dictOutcome
    dictRemaining = _fdictJournalOperations(sName)
    assert len(dictRemaining) == 2
    assert sessionFirst.recordContainment.sOperationId not in dictRemaining
    assert sessionSecond.recordContainment.sOperationId in dictRemaining
    assert sPipelineOperationId in dictRemaining
    assert connectionDocker.fdictInspectExec(
        sessionSecond.recordContainment.sDockerExecId,
    ).get("Running") is True, (
        "settling the first terminal must not have touched the second"
    )
    terminalContainment.fnDrainSessionRecord(sessionSecond)
