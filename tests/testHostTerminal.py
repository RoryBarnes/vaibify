"""The host terminal: a real PTY, journaled, contained, and drained.

Every test here drives a REAL shell on this machine through the REAL
``HostConnection`` launch and the REAL journal in a temp directory —
never a stub keyed the same way as the code under test. The claims:

* the ``terminal``-kind record is in flight BEFORE the shell's first
  instruction (the suspended-gate split, ruling 12);
* the containment probe matches the SESSION, not the group — a
  backgrounded job in its own process group is seen and killed;
* the drain terminates-and-PROVES, and the settled record releases the
  quiescence claim; a record that cannot be proven quarantines;
* the standing demonstration: a ``setsid`` descendant escapes the
  session and the probe CANNOT see it. That test is evidence for the
  stated limit — quiescence unproven, never quiet — not a gate.
"""

import json
import os
import time

import pytest
from unittest.mock import patch

from vaibify.config import containerLock, operationJournal
from vaibify.gui import terminalContainment
from vaibify.gui.terminalSession import HostTerminalSession
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection

S_PROJECT_NAME = "host-terminal-proj"


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


class _AppStateStub:
    """The one attribute the terminal registry reaches for."""


@pytest.fixture()
def tProjectAndSession(tmp_path):
    """Return (sProjectRoot, HostTerminalSession), not yet started."""
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot)
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sProjectRoot,
    )
    session = HostTerminalSession(
        connection, S_PROJECT_NAME,
        {"appState": _AppStateStub(), "iOwnerGeneration": 1},
    )
    return sProjectRoot, session


def _fbAwaitOutputContains(session, sNeedle, fTimeoutSeconds=30.0):
    """Poll the session's output until sNeedle appears."""
    baCollected = b""
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        baChunk = session.fbaReadOutput()
        if baChunk:
            baCollected += baChunk
            if sNeedle.encode() in baCollected:
                return True
        else:
            time.sleep(0.05)
    return False


def _fdictReadSingleTerminalRecord():
    """Return the journal's single terminal-kind record."""
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        S_PROJECT_NAME,
    )
    listRecords = [
        dictRecord
        for dictRecord in dictOutcomeRead["dictOperations"].values()
        if dictRecord.get("sKind") == "terminal"
    ]
    assert len(listRecords) == 1, dictOutcomeRead["dictOperations"]
    return listRecords[0]


@pytest.mark.falsification
def test_a_real_shell_round_trips_and_the_drain_proves_it_gone(
    tProjectAndSession,
):
    """The end-to-end claim: start, echo, drain, prove, settle.

    Kills: the route/session wiring that makes a host shell real —
    and, through the drain assertion, any regression that lets the
    close path stop proving.
    """
    _, session = tProjectAndSession
    session.fnStart()
    try:
        assert session.recordContainment is not None
        assert session.recordContainment.iProcessGroup == (
            session._processChild.pid
        )
        session.fnSendInput(b"echo TERMINAL-$((6*7))\n")
        assert _fbAwaitOutputContains(session, "TERMINAL-42"), (
            "the shell never echoed through the PTY"
        )
    finally:
        dictOutcome = terminalContainment.fdictDrainSessionRecord(
            session,
        )
        session.fnClose()
    assert dictOutcome["bProvenEmpty"] is True, dictOutcome
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        S_PROJECT_NAME,
    )
    assert dictOutcomeRead["dictOperations"] == {}, (
        "a proven-empty terminal must leave no journal residue"
    )


@pytest.mark.falsification
def test_the_record_is_in_flight_before_the_shell_can_run(
    tProjectAndSession,
):
    """Journal-before-first-instruction, observed at the promote.

    While the promote runs, the gate byte has not been written, so the
    child MUST still be the suspended stub — its session id is the
    hub's, not its own. The observation window after the real promote
    is generous (0.4 s): under the healthy ordering the child cannot
    setsid no matter how long we watch, because its gate is still
    closed; under the swapped ordering it becomes a session leader
    within milliseconds.

    Kills: releasing the gate before the promote — the shell's first
    instructions would run with no durable identity on disk, which is
    the crash window ruling 12 exists to close.
    """
    _, session = tProjectAndSession
    dictSeen = {}
    fnRealPromote = terminalContainment.fnPromoteHostTerminalOperation

    def fnObservingPromote(sResourceName, sOperationId, iPid, iGeneration):
        fnRealPromote(sResourceName, sOperationId, iPid, iGeneration)
        dictRecord = _fdictReadSingleTerminalRecord()
        dictSeen["sStateAtPromote"] = dictRecord["sState"]
        dictSeen["iJournaledPid"] = dictRecord.get("iHolderPid")
        bBecameLeader = False
        fDeadline = time.monotonic() + 0.4
        while time.monotonic() < fDeadline:
            if os.getsid(iPid) == iPid:
                bBecameLeader = True
                break
            time.sleep(0.02)
        dictSeen["bLeaderBeforeRelease"] = bBecameLeader

    with patch.object(
        terminalContainment, "fnPromoteHostTerminalOperation",
        fnObservingPromote,
    ):
        session.fnStart()
    try:
        assert dictSeen["sStateAtPromote"] == (
            operationJournal.S_OPERATION_STATE_IN_FLIGHT
        )
        assert dictSeen["iJournaledPid"] == session._processChild.pid
        assert dictSeen["bLeaderBeforeRelease"] is False, (
            "the shell led its own session while the record was still "
            "being journaled; the gate opened too early"
        )
    finally:
        terminalContainment.fdictDrainSessionRecord(session)
        session.fnClose()


def _fsDescribeSessionForensics(session, iLeader, iJobPid=0):
    """Return the evidence a probe-miss needs to be diagnosable in CI."""
    import subprocess
    sTable = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,sess=,stat=,command="],
        capture_output=True, text=True, timeout=10,
    ).stdout
    listRelevant = [
        sLine for sLine in sTable.splitlines()
        if str(iLeader) in sLine or "sleep 300" in sLine
        or (iJobPid and str(iJobPid) in sLine)
    ]
    sJobSid = "-"
    if iJobPid:
        try:
            sJobSid = str(os.getsid(iJobPid))
        except OSError as errorSid:
            sJobSid = f"raised:{errorSid.errno}"
    return (
        f"leader={iLeader} jobPid={iJobPid} jobSid={sJobSid} "
        f"shellPoll={session._processChild.poll()!r} "
        f"psRows={listRelevant!r}"
    )


def _ftStartSessionWithStrayGroupJob(tProjectAndSession):
    """Start a session and background a disowned job in its own group.

    The fixture WAITS until the probe sees at least the shell and the
    job before handing the session over: the member is long-lived, so
    eventual visibility within the bound is the honest precondition,
    and a saturated CI runner's scheduling lag stops masquerading as
    a containment miss. Under the probe-narrowed mutant the job never
    becomes visible, so the wait times out and both dependents fail —
    the levers stay killed. On timeout the failure message carries
    the forensics (shell liveness, the raw ps rows) that a bare count
    could never explain.
    """
    _, session = tProjectAndSession
    session.fnStart()
    connection = session._connectionHost
    iLeader = session._processChild.pid
    session.fnSendInput(b"sleep 300 & disown; echo STARTED-$!\n")
    iJobPid = _fiAwaitStartedJobPid(session, iLeader)
    fDeadline = time.monotonic() + 15.0
    dictProbe = {}
    while time.monotonic() < fDeadline:
        dictProbe = connection.fdictProbeProcessGroupMembers(
            S_PROJECT_NAME, iLeader,
        )
        if iJobPid in (dictProbe.get("listMemberPids") or []):
            break
        time.sleep(0.2)
    assert iJobPid in (dictProbe.get("listMemberPids") or []), (
        f"the probe never saw the backgrounded job: {dictProbe}; "
        + _fsDescribeSessionForensics(session, iLeader, iJobPid)
    )
    return session, iLeader, iJobPid


def _fiAwaitStartedJobPid(session, iLeader):
    """Return the backgrounded job's pid, parsed from STARTED-<pid>.

    The pid comes from bash's own ``$!``, so the assertions downstream
    name the EXACT process they claim about — a bare member count can
    be satisfied by a transient shell fork while the real job is
    missing, which is precisely the ambiguity that made a CI flake
    undiagnosable.
    """
    baCollected = b""
    fDeadline = time.monotonic() + 30.0
    while time.monotonic() < fDeadline:
        baChunk = session.fbaReadOutput()
        if baChunk:
            baCollected += baChunk
            listMatches = [
                sWord for sWord in
                baCollected.decode("utf-8", "replace").split()
                if sWord.startswith("STARTED-")
            ]
            for sMatch in listMatches:
                try:
                    return int(sMatch.split("-", 1)[1])
                except ValueError:
                    continue
        else:
            time.sleep(0.05)
    raise AssertionError(
        "the background job never started: "
        + _fsDescribeSessionForensics(session, iLeader)
        + f" ptyTail={baCollected[-300:]!r}"
    )


@pytest.mark.falsification
def test_a_backgrounded_job_in_its_own_group_is_seen(
    tProjectAndSession,
):
    """The probe matches the SESSION, not the group.

    bash job control puts a background job in its OWN process group
    within the shell's session (verified live), so a group-only probe
    would report the session empty while the job runs — the container
    terminal's codex-round-12 hole, transposed to the host.

    Kills: narrowing the probe program's match to the process group.
    """
    session, iLeader, iJobPid = _ftStartSessionWithStrayGroupJob(
        tProjectAndSession,
    )
    try:
        dictProbe = session._connectionHost.fdictProbeProcessGroupMembers(
            S_PROJECT_NAME, iLeader,
        )
        assert dictProbe["bConclusive"] is True
        assert iJobPid in dictProbe["listMemberPids"], (
            "the probe missed the backgrounded job in its own process "
            f"group: {dictProbe}; "
            + _fsDescribeSessionForensics(session, iLeader, iJobPid)
        )
    finally:
        terminalContainment.fdictDrainSessionRecord(session)
        session.fnClose()


@pytest.mark.falsification
def test_the_drain_kills_the_stray_group_job_and_proves_it(
    tProjectAndSession,
):
    """Delivery is per-member, because ``killpg`` cannot reach a job
    the shell moved to its own group. The drain must kill the stray
    and PROVE the session empty — a drain that only sweeps the leader
    group would quarantine here (or worse, settle over a survivor).

    Kills: the signaller dropping its per-member delivery.
    """
    session, iLeader, iJobPid = _ftStartSessionWithStrayGroupJob(
        tProjectAndSession,
    )
    connection = session._connectionHost
    try:
        dictOutcome = terminalContainment.fdictDrainSessionRecord(
            session,
        )
    finally:
        session.fnClose()
    assert dictOutcome["bProvenEmpty"] is True, (
        f"the drain could not prove the session empty: {dictOutcome}; "
        + _fsDescribeSessionForensics(session, iLeader, iJobPid)
    )
    dictAfter = connection.fdictProbeProcessGroupMembers(
        S_PROJECT_NAME, iLeader,
    )
    assert dictAfter["iMemberCount"] == 0, (
        "the backgrounded job survived the drain"
    )
    try:
        os.kill(iJobPid, 0)
        bJobStillAlive = True
    except ProcessLookupError:
        bJobStillAlive = False
    except PermissionError:
        bJobStillAlive = True
    assert not bJobStillAlive, (
        f"the named job {iJobPid} outlived a drain that claimed proof"
    )


def test_a_setsid_descendant_escapes_the_probe(tProjectAndSession):
    """The standing demonstration of the stated limit — NOT a gate.

    A descendant that calls ``setsid`` leaves the recorded session
    entirely; nothing here can see it, which is exactly why a host
    project in which a terminal ran reports quiescence UNPROVEN and
    never quiet. If this test ever FAILS because the probe starts
    seeing the escapee, the honest response is to strengthen the
    quiescence claim deliberately — not to adjust the probe.
    """
    sProjectRoot, session = tProjectAndSession
    session.fnStart()
    connection = session._connectionHost
    iLeader = session._processChild.pid
    sPidPath = os.path.join(sProjectRoot, "escapee.pid")
    try:
        session.fnSendInput(
            b"python3 -c 'import os,sys,time\n"
            b"iPid=os.fork()\n"
            b"if iPid==0:\n"
            b"    os.setsid()\n"
            b"    open(sys.argv[1],\"w\").write(str(os.getpid()))\n"
            b"    time.sleep(300)\n' "
            + sPidPath.encode() + b" &\nwait\necho FORKED\n",
        )
        assert _fbAwaitOutputContains(session, "FORKED")
        fDeadline = time.monotonic() + 5.0
        while not os.path.exists(sPidPath):
            assert time.monotonic() < fDeadline, "no escapee pid file"
            time.sleep(0.05)
        iEscapeePid = int(open(sPidPath).read())
        dictProbe = connection.fdictProbeProcessGroupMembers(
            S_PROJECT_NAME, iLeader,
        )
        listMembers = dictProbe.get("listMemberPids") or []
        assert iEscapeePid not in listMembers, (
            "the probe SAW a setsid escapee; the quiescence claim can "
            "be strengthened — do that deliberately, not here"
        )
    finally:
        terminalContainment.fdictDrainSessionRecord(session)
        session.fnClose()
        try:
            iEscapeePid = int(open(sPidPath).read())
            os.kill(iEscapeePid, 9)
        except (OSError, ValueError):
            pass


def test_a_zombie_is_not_a_live_session_member():
    """A corpse is not containment's problem, on either platform.

    The drained shell is the hub's own Popen child and stays a ZOMBIE
    until the close path reaps it — still listed by ps, and on Linux
    still answering os.getsid — while the drain proves BEFORE the
    close. Counting it made every Linux drain quarantine over a
    corpse (found by CI; macOS masked it because its getsid refuses
    zombies). The enumerator must exclude it explicitly.
    """
    import subprocess
    import sys
    from vaibify.config.processLiveness import ftEnumerateSessionMembers
    processZombie = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    iZombiePid = processZombie.pid
    try:
        # Deliberately NOT poll(): polling would reap the corpse this
        # test exists to leave lying around. The child runs `pass`,
        # so after a beat it has exited and sits unreaped.
        time.sleep(0.5)
        bConclusive, listMemberPids = ftEnumerateSessionMembers(
            iZombiePid,
        )
        assert bConclusive is True
        assert listMemberPids == [], (
            "a zombie was counted as a live session member; every "
            f"drain would quarantine over a corpse: {listMemberPids}"
        )
    finally:
        processZombie.wait()


@pytest.mark.falsification
def test_a_dead_host_terminal_record_settles_at_reconcile_time(
    tmp_path,
):
    """The journal probe routes host records to the host-native provers.

    A ``terminal`` record with a pid identity and no exec id belongs
    to a host shell; probing it through the Docker exec inspection
    answers "missing exec id" forever, which would quarantine every
    crashed host hub permanently. The prover must be the helper-style
    pid liveness plus the session-wide sweep.

    Kills: the host branch of the terminal probe dropping away.
    """
    import subprocess
    import sys
    sProjectRoot = str(tmp_path / "project")
    os.makedirs(sProjectRoot, exist_ok=True)
    processDead = subprocess.Popen(
        [sys.executable, "-c", "import os; os.setsid()"],
        start_new_session=True,
    )
    iDeadPid = processDead.pid
    processDead.wait()
    dictRecord = {
        "sKind": "terminal",
        "sState": operationJournal.S_OPERATION_STATE_IN_FLIGHT,
        "sTarget": S_PROJECT_NAME,
        "iHolderPid": iDeadPid,
        "iHolderProcessGroup": iDeadPid,
        "sInFlightIso": "2026-01-01T00:00:00",
    }
    with patch(
        "vaibify.host.hostConnection._fsResolveRegisteredHostProjectRoot",
        lambda sResourceId: sProjectRoot,
    ):
        dictProbe = operationJournal._fdictProbeTerminalOperation(
            dictRecord, None,
        )
    assert dictProbe["bHolderAlive"] is False
    assert dictProbe["bSettled"] is True, dictProbe
