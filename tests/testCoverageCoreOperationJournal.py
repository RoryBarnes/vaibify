"""operationJournal's fail-closed read and its per-kind probes.

The journal files are REAL bytes in tmp_path; every malformed shape is
written to disk and read back through the hardened reader, which must
answer ``malformed`` (quarantine) rather than an empty journal. The
probes are driven with record dicts and a Docker connection recorder,
because the daemon is the external boundary; each asserts which of
settled / busy / indeterminate / permanent the record maps to, since
that verdict is what keeps or releases a container.
"""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from vaibify.config import operationJournal


S_CONTAINER_NAME = "containerAlpha"


@pytest.fixture(autouse=True)
def fixtureIsolateJournal(tmp_path, monkeypatch):
    """Redirect the operation journal into tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    os.makedirs(str(tmp_path / "journal"))


def fnWriteJournalBytes(baContent):
    """Write raw bytes where the container's journal file lives."""
    with open(operationJournal.fsJournalPathFor(S_CONTAINER_NAME), "wb") as fileHandle:
        fileHandle.write(baContent)


def fdictValidRecord(**dictOverrides):
    """Return a valid in-flight record, with overrides applied."""
    dictRecord = {
        "sState": operationJournal.S_OPERATION_STATE_IN_FLIGHT,
        "sKind": "exec", "sTarget": "targetAlpha",
        "sPreparedIso": "2026-01-01T00:00:00",
        "sInFlightIso": "2026-01-01T00:00:01",
    }
    dictRecord.update(dictOverrides)
    return dictRecord


def fnWritePayload(dictOperations, **dictTopLevel):
    """Write a schema-current payload holding dictOperations."""
    dictPayload = {
        "iSchemaVersion": operationJournal._I_JOURNAL_SCHEMA_VERSION,
        "sContainerName": S_CONTAINER_NAME,
        "dictOperations": dictOperations,
    }
    dictPayload.update(dictTopLevel)
    fnWriteJournalBytes(json.dumps(dictPayload).encode("utf-8"))


def testInvalidContainerNameIsRefusedBeforeAnyPathIsBuilt():
    """A traversal-shaped name never becomes a journal path."""
    with pytest.raises(operationJournal.OperationJournalError, match="Invalid"):
        operationJournal.fsJournalPathFor("../escape")


def testOversizedJournalIsMalformed(monkeypatch):
    """A file beyond the byte bound is quarantined, not parsed."""
    monkeypatch.setattr(operationJournal, "_I_MAXIMUM_JOURNAL_BYTES", 10)
    fnWritePayload({})
    dictOutcome = operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)
    assert dictOutcome["sReadState"] == "malformed"
    assert "bounded size" in dictOutcome["sDetail"]


def testJournalPathThatIsADirectoryIsUnreadable():
    """An unreadable journal path is malformed, never absent."""
    os.makedirs(operationJournal.fsJournalPathFor(S_CONTAINER_NAME))
    dictOutcome = operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)
    assert dictOutcome["sReadState"] == "malformed"
    assert "unreadable" in dictOutcome["sDetail"]


@pytest.mark.parametrize(
    "baContent,sExpectedFragment",
    [
        (b"[1, 2]", "top level is not an object"),
        (b'{"iSchemaVersion": true}', "no integer iSchemaVersion"),
        (b'{"iSchemaVersion": 0}', "is unknown"),
    ],
)
def testWrongShapedTopLevelIsMalformed(baContent, sExpectedFragment):
    """Each wrong top-level shape is named in the quarantine detail."""
    fnWriteJournalBytes(baContent)
    dictOutcome = operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)
    assert dictOutcome["sReadState"] == "malformed"
    assert sExpectedFragment in dictOutcome["sDetail"]


def testNewerSchemaRequiresUpgradeNotReconciliation():
    """A journal from a newer build asks for an upgrade by name."""
    fnWriteJournalBytes(json.dumps({
        "iSchemaVersion": operationJournal._I_JOURNAL_SCHEMA_VERSION + 1,
    }).encode("utf-8"))
    dictOutcome = operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)
    assert dictOutcome["sReadState"] == "requiresUpgrade"
    assert "upgrade vaibify" in dictOutcome["sDetail"]


def testTopLevelShapeViolationsAreMalformed(monkeypatch):
    """Extra keys, a non-object map, and too many records all quarantine."""
    fnWritePayload({}, sSmuggled="x")
    assert "non-allowlisted keys" in operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME)["sDetail"]
    fnWritePayload([])
    assert "no dictOperations object" in (
        operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)["sDetail"]
    )
    monkeypatch.setattr(operationJournal, "_I_MAXIMUM_OPERATION_RECORDS", 1)
    fnWritePayload({"a": fdictValidRecord(), "b": fdictValidRecord()})
    assert "bounded record count" in (
        operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)["sDetail"]
    )


@pytest.mark.parametrize(
    "sOperationId,valueRecord,sExpectedFragment",
    [
        ("x" * 600, fdictValidRecord(), "invalid operation id"),
        ("operationAlpha", "not a record", "is not an object"),
        ("operationAlpha", {"sKind": "exec", "sTarget": "t",
                            "sPreparedIso": "p"}, "is missing sState"),
        ("operationAlpha", fdictValidRecord(sState="exploded"),
         "has an unknown state"),
        ("operationAlpha", {"sState": "IN_FLIGHT", "sKind": "exec",
                            "sTarget": "t", "sPreparedIso": "p"},
         "in flight without sInFlightIso"),
        ("operationAlpha", fdictValidRecord(sTarget="t" * 600),
         "field sTarget is not a bounded string"),
        ("operationAlpha", fdictValidRecord(iHolderPid="12"),
         "field iHolderPid is not an integer"),
    ],
)
def testMalformedRecordsAreNamedInTheQuarantine(
    sOperationId, valueRecord, sExpectedFragment,
):
    """Every record-level violation is malformed with its reason."""
    fnWritePayload({sOperationId: valueRecord})
    dictOutcome = operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)
    assert dictOutcome["sReadState"] == "malformed"
    assert sExpectedFragment in dictOutcome["sDetail"]


def testFullJournalRefusesANewPreparedRecord(monkeypatch):
    """The bounded record count is enforced at prepare time too."""
    monkeypatch.setattr(operationJournal, "_I_MAXIMUM_OPERATION_RECORDS", 1)
    operationJournal.fsPrepareOperation(S_CONTAINER_NAME, "exec", "targetAlpha")
    with pytest.raises(operationJournal.OperationJournalError, match="is full"):
        operationJournal.fsPrepareOperation(S_CONTAINER_NAME, "exec", "targetBeta")


def testPromotionRejectsMistypedIdentityAndNonInFlightAmendment():
    """Typed identity is enforced; only in-flight records take amendments."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "exec", "targetAlpha",
    )
    with pytest.raises(operationJournal.OperationJournalRecordError,
                       match="not an integer"):
        operationJournal.fnPromoteOperationToInFlight(
            S_CONTAINER_NAME, sOperationId, {"iHolderPid": "123"},
        )
    with pytest.raises(operationJournal.OperationJournalRecordError,
                       match="only an in-flight operation"):
        operationJournal.fnAmendInFlightHolderIdentity(
            S_CONTAINER_NAME, sOperationId, {"iHolderProcessGroup": 5},
        )


def testBreakGlassRefusesAbsentNewerAndValidJournals():
    """Only a MALFORMED marker may be blindly unlinked."""
    with pytest.raises(operationJournal.OperationJournalRecordError,
                       match="no journal marker"):
        operationJournal.fnAssertJournalIsBreakGlassClearable(S_CONTAINER_NAME, "")
    fnWriteJournalBytes(json.dumps({
        "iSchemaVersion": operationJournal._I_JOURNAL_SCHEMA_VERSION + 1,
    }).encode("utf-8"))
    with pytest.raises(operationJournal.OperationJournalUnreadableError):
        operationJournal.fnAssertJournalIsBreakGlassClearable(S_CONTAINER_NAME, "")
    fnWritePayload({"operationAlpha": fdictValidRecord()})
    with pytest.raises(operationJournal.OperationJournalRecordError,
                       match="is valid"):
        operationJournal.fnAssertJournalIsBreakGlassClearable(S_CONTAINER_NAME, "")


class ProbeConnectionRecorder:
    """Docker stand-in answering exec/group/container/hash probes."""

    def __init__(self, **dictAnswers):
        self.dictAnswers = dictAnswers

    def fnAnswer(self, sKey):
        valueAnswer = self.dictAnswers.get(sKey)
        if isinstance(valueAnswer, BaseException):
            raise valueAnswer
        return valueAnswer

    def fdictInspectExec(self, sExecId):
        return self.fnAnswer("dictExec")

    def fdictProbeProcessGroupMembers(self, sContainerId, iGroup):
        return self.fnAnswer("dictGroup")

    def fdictInspectContainerIfPresent(self, sContainerId):
        return self.fnAnswer("dictContainer")

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        return self.fnAnswer("tExec")


class ConnectionWithoutMethods:
    """A reachable connection that models none of the probe methods."""


def ftVerdict(dictRecord, connectionDocker):
    """Return the resolver's (verdict, detail) for one record."""
    return operationJournal._ftResolveOperationRecord(dictRecord, connectionDocker)


class DaemonNotFoundError(Exception):
    """An SDK-shaped error carrying a 404 on its response."""

    def __init__(self):
        super().__init__("no such exec")
        self.response = type("Response", (), {"status_code": 404})()


@pytest.mark.parametrize(
    "dictRecordFields,connectionDocker,sVerdict,sFragment",
    [
        ({}, ProbeConnectionRecorder(), "quarantinePermanent", "missing its exec id"),
        ({"sDockerExecId": "e"}, None, "quarantineTransient", "no Docker connection"),
        ({"sDockerExecId": "e"}, ConnectionWithoutMethods(),
         "quarantinePermanent", "cannot inspect execs"),
        ({"sDockerExecId": "e"},
         ProbeConnectionRecorder(dictExec=DaemonNotFoundError()),
         "settled", "no record of the exec"),
        ({"sDockerExecId": "e"},
         ProbeConnectionRecorder(dictExec=RuntimeError("socket closed")),
         "quarantineTransient", "exec inspect failed"),
        ({"sDockerExecId": "e"}, ProbeConnectionRecorder(dictExec={"Running": True}),
         "busy", "still running"),
        ({"sDockerExecId": "e"}, ProbeConnectionRecorder(dictExec={}),
         "quarantineTransient", "no conclusive exec state"),
    ],
)
def testExecProbeVerdicts(dictRecordFields, connectionDocker, sVerdict, sFragment):
    """Each exec answer maps to exactly one verdict."""
    dictRecord = fdictValidRecord(sKind="exec", **dictRecordFields)
    tVerdict = ftVerdict(dictRecord, connectionDocker)
    assert tVerdict[0] == sVerdict
    assert sFragment in tVerdict[1]


def fdictTerminalRecord(**dictFields):
    """Return an in-flight Docker terminal record."""
    dictBase = {"sDockerExecId": "execAlpha", "sDockerContainerId": "cidAlpha"}
    dictBase.update(dictFields)
    return fdictValidRecord(sKind="terminal", **dictBase)


@pytest.mark.parametrize(
    "dictRecord,connectionDocker,sVerdict,sFragment",
    [
        (fdictTerminalRecord(sDockerContainerId=""), ProbeConnectionRecorder(),
         "quarantinePermanent", "missing its exec id or container id"),
        (fdictTerminalRecord(), None, "quarantineTransient", "no Docker connection"),
        (fdictTerminalRecord(),
         ProbeConnectionRecorder(dictExec={"Running": True}),
         "busy", "still running"),
        (fdictTerminalRecord(),
         ProbeConnectionRecorder(dictExec={"Running": False}),
         "quarantinePermanent", "never learned its process group"),
        (fdictTerminalRecord(iHolderProcessGroup=77),
         ProbeConnectionRecorder(dictExec={"Running": False},
                                 dictGroup=RuntimeError("probe died")),
         "quarantineTransient", "process-group probe failed"),
        (fdictTerminalRecord(iHolderProcessGroup=77),
         ProbeConnectionRecorder(dictExec={"Running": False},
                                 dictGroup={"bConclusive": False, "sDetail": "d"}),
         "quarantineTransient", "inconclusive"),
        (fdictTerminalRecord(iHolderProcessGroup=77),
         ProbeConnectionRecorder(dictExec={"Running": False},
                                 dictGroup={"bConclusive": True, "iMemberCount": 0}),
         "settled", "group is empty"),
        (fdictTerminalRecord(iHolderProcessGroup=77),
         ProbeConnectionRecorder(dictExec={"Running": False},
                                 dictGroup={"bConclusive": True, "iMemberCount": 2}),
         "quarantinePermanent", "2 process(es) outlived"),
    ],
)
def testTerminalProbeSettlesOnlyOnAProvenEmptyGroup(
    dictRecord, connectionDocker, sVerdict, sFragment,
):
    """A dead exec is not enough; the recorded group must be proven empty."""
    tVerdict = ftVerdict(dictRecord, connectionDocker)
    assert tVerdict[0] == sVerdict
    assert sFragment in tVerdict[1]


def testTerminalGroupProbeUnsupportedByConnectionIsPermanent():
    """A connection that cannot probe groups cannot settle a terminal."""

    class ExecOnlyConnection:
        def fdictInspectExec(self, sExecId):
            return {"Running": False}

    tVerdict = ftVerdict(
        fdictTerminalRecord(iHolderProcessGroup=77), ExecOnlyConnection(),
    )
    assert tVerdict == (
        "quarantinePermanent",
        "this Docker connection cannot probe process groups; the verifier "
        "is unsupported and reconciliation is required",
    )


def fiReapedPid():
    """Return the pid of a child that has exited and been reaped."""
    processChild = subprocess.Popen([sys.executable, "-c", "pass"])
    processChild.wait()
    return processChild.pid


def testHostTerminalProbeSweepsTheSession(monkeypatch):
    """A dead host shell settles only when its whole session is empty."""
    iPid = fiReapedPid()
    dictRecord = fdictValidRecord(
        sKind="terminal", iHolderPid=iPid, iHolderProcessGroup=iPid,
    )
    monkeypatch.setattr(
        operationJournal, "ftEnumerateSessionMembers",
        lambda iSession: (False, []),
    )
    assert ftVerdict(dictRecord, None) == (
        "quarantineTransient", "the session-wide terminal sweep could not run",
    )
    monkeypatch.setattr(
        operationJournal, "ftEnumerateSessionMembers",
        lambda iSession: (True, [4242]),
    )
    tVerdict = ftVerdict(dictRecord, None)
    assert tVerdict[0] == "quarantinePermanent"
    assert "1 live member" in tVerdict[1]
    monkeypatch.setattr(
        operationJournal, "ftEnumerateSessionMembers",
        lambda iSession: (True, []),
    )
    assert ftVerdict(dictRecord, None)[0] == "settled"


@pytest.mark.parametrize(
    "dictFields,connectionDocker,sVerdict,sFragment",
    [
        ({}, ProbeConnectionRecorder(), "quarantinePermanent",
         "neither a container id nor a reservation label"),
        ({"sDockerContainerId": "cidAlpha"}, None, "quarantineTransient",
         "no Docker connection"),
        ({"sReservationLabel": "r"}, ProbeConnectionRecorder(),
         "quarantinePermanent", "label-only start probing"),
        ({"sDockerContainerId": "cidAlpha"}, ConnectionWithoutMethods(),
         "quarantinePermanent", "cannot inspect containers"),
        ({"sDockerContainerId": "cidAlpha"},
         ProbeConnectionRecorder(dictContainer=RuntimeError("down")),
         "quarantineTransient", "container inspect failed"),
        ({"sDockerContainerId": "cidAlpha"},
         ProbeConnectionRecorder(dictContainer={"Id": "cidAlpha"}),
         "quarantinePermanent", "still exists"),
    ],
)
def testStartProbeSettlesOnlyOnDefinitiveAbsence(
    dictFields, connectionDocker, sVerdict, sFragment,
):
    """A start record is released only when its container is proven gone."""
    tVerdict = ftVerdict(
        fdictValidRecord(sKind="start", **dictFields), connectionDocker,
    )
    assert tVerdict[0] == sVerdict
    assert sFragment in tVerdict[1]


def testLiveLaunchingProcessKeepsTheStartBusy():
    """The hub's own live pid holds the start record busy."""
    tVerdict = ftVerdict(fdictValidRecord(
        sKind="start", iHolderPid=os.getpid(),
        sInFlightIso=operationJournal._fsNowIso(),
    ), None)
    assert tVerdict[0] == "busy"


def fsSha256Of(baContent):
    """Return the hex sha256 of bytes."""
    return hashlib.sha256(baContent).hexdigest()


def testHostFileWriteSettlesOnNewOrPriorBytesOnly(tmp_path):
    """New bytes or untouched prior bytes settle; anything else does not."""
    pathTarget = tmp_path / "written.txt"
    pathTarget.write_bytes(b"new content")
    dictRecord = fdictValidRecord(
        sKind="file-write", sTarget=str(pathTarget),
        sExpectedSha256=fsSha256Of(b"new content"),
        sPriorSha256=fsSha256Of(b"old content"),
    )
    assert ftVerdict(dictRecord, None)[0] == "settled"
    pathTarget.write_bytes(b"old content")
    assert "never landed" in ftVerdict(dictRecord, None)[1]
    pathTarget.write_bytes(b"torn cont")
    assert ftVerdict(dictRecord, None)[0] == "quarantinePermanent"
    pathTarget.unlink()
    dictRecord["sPriorSha256"] = ""
    assert ftVerdict(dictRecord, None)[0] == "settled"


def testHostFileWriteWithoutExpectationOrReadableTargetQuarantines(tmp_path):
    """No expected hash is permanent; an unreadable target is transient."""
    assert ftVerdict(fdictValidRecord(
        sKind="file-write", sTarget=str(tmp_path / "x"),
    ), None)[0] == "quarantinePermanent"
    os.makedirs(str(tmp_path / "directoryTarget"))
    tVerdict = ftVerdict(fdictValidRecord(
        sKind="file-write", sTarget=str(tmp_path / "directoryTarget"),
        sExpectedSha256="0" * 64,
    ), None)
    assert tVerdict[0] == "quarantineTransient"
    assert "unreadable" in tVerdict[1]


@pytest.mark.parametrize(
    "connectionDocker,sVerdict",
    [
        (None, "quarantineTransient"),
        (ConnectionWithoutMethods(), "quarantinePermanent"),
        (ProbeConnectionRecorder(tExec=RuntimeError("exec died")),
         "quarantineTransient"),
        (ProbeConnectionRecorder(tExec=(0, "a" * 64 + "  /t\n")), "settled"),
        (ProbeConnectionRecorder(tExec=(1, "")), "quarantinePermanent"),
    ],
)
def testContainerFileWriteIsJudgedByAnInContainerHash(connectionDocker, sVerdict):
    """The container leg hashes in the container and compares the same way."""
    dictRecord = fdictValidRecord(
        sKind="file-write", sTarget="/workspace/t", sDockerContainerId="cid",
        sExpectedSha256="a" * 64, sPriorSha256="b" * 64,
    )
    assert ftVerdict(dictRecord, connectionDocker)[0] == sVerdict


def testUnknownKindAndCancelRequestedAreQuarantined():
    """An unknown kind, or a dead cancel-requested worker, never settles."""
    assert ftVerdict(fdictValidRecord(sKind="mystery"), None)[0] == (
        "quarantinePermanent"
    )
    dictRecord = fdictValidRecord(
        sKind="start", sDockerContainerId="cid",
        sState=operationJournal.S_OPERATION_STATE_CANCEL_REQUESTED,
    )
    tVerdict = ftVerdict(dictRecord, ProbeConnectionRecorder(dictContainer=None))
    assert tVerdict == (
        "quarantinePermanent",
        "cancellation was requested but its cleanup is unproven",
    )


def testHostExecHoldersAreLiveOnAnUnreadableJournal():
    """The veto predicate fails safe; the naming call refuses to guess."""
    fnWriteJournalBytes(b"not json")
    assert operationJournal.fbAnyHostExecHolderLive(S_CONTAINER_NAME) is True
    with pytest.raises(operationJournal.OperationJournalUnreadableError):
        operationJournal.flistDescribeHostExecHolders(S_CONTAINER_NAME)


def testHostExecHolderDescriptionReportsProvenLiveness():
    """Only a live, recycle-proof holder is marked proven; others are named."""
    iPid = fiReapedPid()
    fnWritePayload({
        "operationLive": fdictValidRecord(
            sKind="host-exec", sTarget="stepAlpha run",
            iHolderPid=os.getpid(), iHolderProcessGroup=os.getpgrp(),
            sInFlightIso=operationJournal._fsNowIso(),
        ),
        "operationDead": fdictValidRecord(
            sKind="host-exec", sTarget="stepBeta run",
            iHolderPid=iPid, iHolderProcessGroup=iPid,
        ),
        "operationOther": fdictValidRecord(sKind="exec"),
    })
    listHolders = operationJournal.flistDescribeHostExecHolders(S_CONTAINER_NAME)
    dictByLabel = {d["sOperationLabel"]: d for d in listHolders}
    assert sorted(dictByLabel) == ["stepAlpha run", "stepBeta run"]
    assert dictByLabel["stepAlpha run"]["bHolderProven"] is True
    assert dictByLabel["stepBeta run"]["bHolderProven"] is False
    assert operationJournal.fbAnyHostExecHolderLive(S_CONTAINER_NAME) is True
