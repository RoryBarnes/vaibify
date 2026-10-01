"""Branch coverage for the host-lane commands that act on one container.

``vaibify reconcile``, ``vaibify repair dns`` and ``vaibify open`` each
branch on the same discovery -- does a live hub hold this container's
flock? -- and then either act directly or route over that hub's control
socket. The flock read, the control socket, the hub's HTTP redemption
and the Docker daemon are the external boundaries replaced here; the
operation journal and the project registry are real files under the
suite's redirected state directories.

Every error path asserts a nonzero exit, a sentence on stderr, and no
Python traceback.
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from vaibify.cli import commandOpen, commandReconcile, commandRepair
from vaibify.cli import hubSession
from vaibify.cli.commandReconcile import fnReconcileCommand
from vaibify.cli.commandRepair import fnRepairCommand
from vaibify.config import operationJournal, registryManager
from vaibify.docker import containerLifecycleRepair, containerManager
from vaibify.docker.containerLifecycleRepair import RepairRefusedError
from vaibify.gui import hostControlChannel

S_CONTAINER_NAME = "projectAlpha"
S_HOST_PROJECT = "hostProjectBeta"
I_HUB_PORT = 8123
I_HUB_PID = 4242
S_CAPABILITY = "k" * 43
DICT_LIVE_HOLDER = {"iPid": I_HUB_PID, "iPort": I_HUB_PORT}


def frunnerSeparatingStreams():
    """Return a CliRunner whose result keeps stderr apart from stdout."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def fnAssertCleanExit(resultInvoke, iExpectedCode):
    """Assert the exit code and that no Python traceback escaped."""
    assert resultInvoke.exit_code == iExpectedCode, (
        resultInvoke.stdout, resultInvoke.stderr,
    )
    if resultInvoke.exception is not None:
        assert isinstance(resultInvoke.exception, SystemExit), (
            f"uncaught {resultInvoke.exception!r}"
        )
    assert "Traceback" not in resultInvoke.stdout + resultInvoke.stderr


@pytest.fixture(autouse=True)
def fixtureNoRealDockerDaemon(monkeypatch):
    """Make every Docker connection attempt fail as an absent daemon."""
    from vaibify.docker import dockerConnection

    def fnRefuse():
        raise RuntimeError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(dockerConnection, "DockerConnection", fnRefuse)


@pytest.fixture
def fixtureRegistry(tmp_path, monkeypatch):
    """Register one host project in a tmp registry; return its directory."""
    sDirectory = str(tmp_path / "hostProject")
    os.makedirs(sDirectory)
    pathRegistry = tmp_path / "registry.json"
    pathRegistry.write_text(json.dumps({"listProjects": [
        {"sName": S_HOST_PROJECT, "sMode": "host", "sDirectory": sDirectory},
    ]}))
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(pathRegistry),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )
    return sDirectory


def fnPinLockHolder(monkeypatch, moduleTarget, dictHolder):
    """Answer the container flock discovery with dictHolder."""
    monkeypatch.setattr(
        moduleTarget, "fdictReadLockHolder", lambda sName: dict(dictHolder),
    )


def flistScriptControlSocket(monkeypatch, listResponses):
    """Answer successive control-socket requests; return what was sent."""
    listRequests = []

    def fdictAnswer(iPort, dictRequest):
        listRequests.append((iPort, dictRequest))
        objResponse = listResponses.pop(0)
        if isinstance(objResponse, Exception):
            raise objResponse
        return objResponse

    monkeypatch.setattr(
        hostControlChannel, "fdictSendHostControlRequest", fdictAnswer,
    )
    return listRequests


def fnWriteMalformedJournal(sContainerName):
    """Write journal bytes that no schema version can parse."""
    sPath = operationJournal.fsJournalPathFor(sContainerName)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(b"\x00not a journal")


def fnJournalDeadWriterRecord(sContainerName, sNote="writer died"):
    """Journal one record whose writer is provably dead."""
    processDead = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processDead.wait()
    sOperationId = operationJournal.fsPrepareOperation(
        sContainerName, "helper", "dataFile.csv",
    )
    operationJournal.fnPromoteOperationToInFlight(
        sContainerName, sOperationId,
        {"iHolderPid": processDead.pid,
         "iHolderProcessGroup": processDead.pid},
    )
    operationJournal.fnMarkOperationNeedsReconciliation(
        sContainerName, sOperationId, sNote,
    )
    return sOperationId


def fresultReconcile(listArguments, sInput=None):
    """Invoke ``vaibify reconcile`` with separated streams."""
    return frunnerSeparatingStreams().invoke(
        fnReconcileCommand, listArguments, input=sInput,
    )


# ---------------------------------------------------------------------
# reconcile: crash-time lane
# ---------------------------------------------------------------------


def testReconcileDockerConnectionIsNoneWhenTheDaemonIsAbsent():
    assert commandReconcile._fconnectionCreateDockerQuietly() is None


def testCrashTimeReconcileShowsTheRecordNoteThenProves():
    fnJournalDeadWriterRecord(S_CONTAINER_NAME, "writer died mid-copy")
    resultInvoke = fresultReconcile([S_CONTAINER_NAME, "--yes"])
    fnAssertCleanExit(resultInvoke, 0)
    assert "note:      writer died mid-copy" in resultInvoke.stdout
    assert "it is claimable again" in resultInvoke.stdout
    assert not os.path.exists(
        operationJournal.fsJournalPathFor(S_CONTAINER_NAME),
    )


def testNewerJournalSchemaAsksForAnUpgradeNotABreakGlass():
    sPath = operationJournal.fsJournalPathFor(S_CONTAINER_NAME)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileHandle:
        json.dump({
            "iSchemaVersion": 99, "sContainerName": S_CONTAINER_NAME,
            "dictOperations": {},
        }, fileHandle)
    resultInvoke = fresultReconcile([S_CONTAINER_NAME, "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert "Upgrade vaibify" in resultInvoke.stderr
    assert "--break-glass" not in resultInvoke.stderr
    assert os.path.exists(sPath)


def testMalformedHostJournalNamesTheAbandonExitNotTheBreakGlass(
    fixtureRegistry,
):
    fnWriteMalformedJournal(S_HOST_PROJECT)
    resultInvoke = fresultReconcile([S_HOST_PROJECT, "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(
        S_HOST_PROJECT,
    )
    assert f"--abandon-host-journal {sMarkerSha256}" in resultInvoke.stderr
    assert "--break-glass" not in resultInvoke.stderr


def testTerminateRecordedRefusesAContainerProject(fixtureRegistry):
    resultInvoke = fresultReconcile(
        [S_CONTAINER_NAME, "--terminate-recorded"],
    )
    fnAssertCleanExit(resultInvoke, 2)
    assert "--terminate-recorded is for host projects" in (
        resultInvoke.stderr
    )


def testTerminateRecordedReportsRefusalsThenReconciles(
    monkeypatch, fixtureRegistry,
):
    from vaibify.host import hostCancellation
    monkeypatch.setattr(
        hostCancellation, "fdictCancelJournaledHostRun",
        lambda sName: {
            "iGroupsTerminated": 1,
            "listTerminated": [{"iHolderPid": 11}],
            "listAlreadyExited": [{"iHolderPid": 12}, {"iHolderPid": 13}],
            "listRefused": [{
                "sOperationLabel": "stepAlpha", "iHolderPid": 14,
                "iHolderProcessGroup": 15,
                "sReason": "its start time no longer matches",
            }],
        },
    )
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--terminate-recorded", "--yes"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert "Terminated 1 recorded process group(s); 2 had already ended." in (
        resultInvoke.stdout
    )
    assert "NOT signalled: stepAlpha (pid 14, group 15)" in (
        resultInvoke.stderr
    )
    assert "nothing to reconcile" in resultInvoke.stdout


def testTerminateRecordedThatCannotReadRecordsExitsOne(
    monkeypatch, fixtureRegistry,
):
    from vaibify.host import hostCancellation

    def fdictRaise(sName):
        raise OSError("journal directory unreadable")

    monkeypatch.setattr(
        hostCancellation, "fdictCancelJournaledHostRun", fdictRaise,
    )
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--terminate-recorded"],
    )
    fnAssertCleanExit(resultInvoke, 1)
    assert "could not read the records: journal directory unreadable" in (
        resultInvoke.stderr
    )


def testTerminateRecordedWithALiveHubRefuses(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    resultInvoke = fresultReconcile(
        [S_CONTAINER_NAME, "--terminate-recorded"],
    )
    fnAssertCleanExit(resultInvoke, 2)
    assert f"pid={I_HUB_PID} still holds" in resultInvoke.stderr


def testDeclinedHostAbandonmentLeavesTheMarker(fixtureRegistry):
    fnWriteMalformedJournal(S_HOST_PROJECT)
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(
        S_HOST_PROJECT,
    )
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--abandon-host-journal", sMarkerSha256],
        sInput="n\n",
    )
    fnAssertCleanExit(resultInvoke, 1)
    assert "does not prove that the work it describes has ended" in (
        resultInvoke.stdout
    )
    assert "Abandonment cancelled" in resultInvoke.stdout
    assert os.path.exists(operationJournal.fsJournalPathFor(S_HOST_PROJECT))


def testHostAbandonmentWithAStaleHashIsRefused(fixtureRegistry):
    fnWriteMalformedJournal(S_HOST_PROJECT)
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--abandon-host-journal", "0" * 64, "--yes"],
    )
    fnAssertCleanExit(resultInvoke, 1)
    assert "Abandonment refused" in resultInvoke.stderr
    assert os.path.exists(operationJournal.fsJournalPathFor(S_HOST_PROJECT))


def testHostAbandonmentWithTheMatchingHashClearsTheMarker(fixtureRegistry):
    fnWriteMalformedJournal(S_HOST_PROJECT)
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(
        S_HOST_PROJECT,
    )
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--abandon-host-journal", sMarkerSha256, "--yes"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert "Nothing was proven" in resultInvoke.stdout
    assert not os.path.exists(
        operationJournal.fsJournalPathFor(S_HOST_PROJECT),
    )


def testBreakGlassStopsTheContainerByNameBeforeClearing(monkeypatch):
    fnWriteMalformedJournal(S_CONTAINER_NAME)
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(
        S_CONTAINER_NAME,
    )
    listStopped = []

    def fbStop(sName):
        listStopped.append(sName)
        return True

    monkeypatch.setattr(
        containerManager, "fbStopContainerProvenSettled", fbStop,
    )
    resultInvoke = fresultReconcile(
        [S_CONTAINER_NAME, "--break-glass", sMarkerSha256],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert listStopped == [S_CONTAINER_NAME]
    assert "Break-glass cleared" in resultInvoke.stdout


# ---------------------------------------------------------------------
# reconcile: live-hub lane
# ---------------------------------------------------------------------


def testLiveHolderWithoutAPortIsExplained(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, {"iPid": I_HUB_PID})
    resultInvoke = fresultReconcile([S_CONTAINER_NAME, "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert "reports no hub port" in resultInvoke.stderr


def testForceAbandonIsRoutedAndThePoisonIsReported(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    listRequests = flistScriptControlSocket(monkeypatch, [
        {"bAccepted": True, "bPoisoned": True,
         "listRecordNotes": ["worker 77 poisoned"]},
    ])
    resultInvoke = fresultReconcile(
        [S_CONTAINER_NAME, "--force-abandon", "opIdentifier01"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert listRequests == [(I_HUB_PORT, {
        "sOperation": "force-abandon", "sContainerName": S_CONTAINER_NAME,
        "sExpectedOperationId": "opIdentifier01",
    })]
    assert "proven: worker 77 poisoned" in resultInvoke.stdout
    assert "Force-abandoned" in resultInvoke.stdout


def testDeclinedAbandonmentOnALiveHubSendsNothing(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    listRequests = flistScriptControlSocket(monkeypatch, [])
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--abandon-host-journal", "a" * 64], sInput="n\n",
    )
    fnAssertCleanExit(resultInvoke, 1)
    assert listRequests == []
    assert "Abandonment cancelled" in resultInvoke.stdout


def testConfirmedAbandonmentOnALiveHubCarriesTheHash(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    listRequests = flistScriptControlSocket(
        monkeypatch, [{"bAccepted": True}],
    )
    resultInvoke = fresultReconcile(
        [S_HOST_PROJECT, "--abandon-host-journal", "a" * 64, "--yes"],
    )
    fnAssertCleanExit(resultInvoke, 0)
    assert listRequests[0][1] == {
        "sOperation": "abandon-host-journal",
        "sContainerName": S_HOST_PROJECT, "sMarkerSha256": "a" * 64,
    }
    assert f"the hub reconciled '{S_HOST_PROJECT}'" in resultInvoke.stdout


def testMalformedJournalOnALiveHubIsReportedWithoutARequest(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    fnWriteMalformedJournal(S_CONTAINER_NAME)
    listRequests = flistScriptControlSocket(monkeypatch, [])
    resultInvoke = fresultReconcile([S_CONTAINER_NAME, "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert listRequests == []
    assert "--break-glass" in resultInvoke.stderr


def testEmptyJournalOnALiveHubSendsNothing(monkeypatch):
    """The exit status is deliberately not asserted; see the report."""
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    listRequests = flistScriptControlSocket(monkeypatch, [])
    resultInvoke = fresultReconcile([S_CONTAINER_NAME, "--yes"])
    assert listRequests == []
    assert "nothing to reconcile" in resultInvoke.stdout
    assert "Traceback" not in resultInvoke.stdout + resultInvoke.stderr


def testDeclinedReconcileOnALiveHubSendsNothing(monkeypatch):
    fnPinLockHolder(monkeypatch, commandReconcile, DICT_LIVE_HOLDER)
    fnJournalDeadWriterRecord(S_CONTAINER_NAME)
    listRequests = flistScriptControlSocket(monkeypatch, [])
    resultInvoke = fresultReconcile([S_CONTAINER_NAME], sInput="n\n")
    fnAssertCleanExit(resultInvoke, 1)
    assert listRequests == []
    assert "Reconciliation cancelled; the quarantine stands." in (
        resultInvoke.stdout
    )


# ---------------------------------------------------------------------
# repair dns
# ---------------------------------------------------------------------


class _RepairConnection:
    """The two container reads a DNS repair performs."""

    def __init__(self, sResolvConf, dictResolution):
        self.sResolvConf = sResolvConf
        self.dictResolution = dictResolution
        self.listResolved = []

    def fbaFetchFile(self, sContainerName, sPath):
        return self.sResolvConf.encode("utf-8")

    def fdictResolveHostnameInContainer(self, sContainerName, sHost, *args):
        self.listResolved.append((sContainerName, sHost))
        return self.dictResolution


def fconfigForRepair(listRepositories=None):
    """Return the config attributes a DNS repair reads."""
    return SimpleNamespace(
        sProjectName=S_CONTAINER_NAME,
        listRepositories=listRepositories if listRepositories is not None
        else [{"url": "https://git.example.test/team/projectAlpha.git"}],
        features=SimpleNamespace(bClaude=False),
    )


def fnPrepareRepair(
    monkeypatch, connectionRepair, jsonHostConfig=None, config=None,
):
    """Pin config, connection, inspect and flock for one repair run."""
    monkeypatch.setattr(
        commandRepair, "fconfigResolveProject",
        lambda sName: config or fconfigForRepair(),
    )
    monkeypatch.setattr(
        commandRepair, "_fconnectionOpenDockerOrExit",
        lambda sName: connectionRepair,
    )
    monkeypatch.setattr(
        containerManager, "fjsonInspectContainer",
        lambda sName: {"HostConfig": jsonHostConfig or {
            "NetworkMode": "bridge",
        }},
    )
    fnPinLockHolder(monkeypatch, commandRepair, {})


def fresultRepair(listArguments, sInput=None):
    """Invoke ``vaibify repair`` with separated streams."""
    return frunnerSeparatingStreams().invoke(
        fnRepairCommand, listArguments, input=sInput,
    )


def testRepairWithoutADaemonExplainsOnTheCommandLine(monkeypatch):
    monkeypatch.setattr(
        commandRepair, "fconfigResolveProject",
        lambda sName: fconfigForRepair(),
    )
    resultInvoke = fresultRepair(["dns", "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert f"cannot reach the Docker daemon, so '{S_CONTAINER_NAME}'" in (
        resultInvoke.stderr
    )


def testDeclinedRepairChangesNothing(monkeypatch):
    fnPrepareRepair(monkeypatch, _RepairConnection("", {}))
    listRepairs = []
    monkeypatch.setattr(
        containerLifecycleRepair, "fdictRepairDirectlyUnderFlock",
        lambda *args: listRepairs.append(args),
    )
    resultInvoke = fresultRepair(["dns"], sInput="n\n")
    fnAssertCleanExit(resultInvoke, 1)
    assert "Restarting 'projectAlpha' will:" in resultInvoke.stdout
    assert "Nothing was changed." in resultInvoke.stdout
    assert listRepairs == []


def testRefusedDirectRepairExitsOneWithTheRefusal(monkeypatch):
    fnPrepareRepair(monkeypatch, _RepairConnection("", {}))

    def fnRefuse(config, sName, sOperation, fnAnnounce):
        raise RepairRefusedError("a live vaibify process holds it")

    monkeypatch.setattr(
        containerLifecycleRepair, "fdictRepairDirectlyUnderFlock", fnRefuse,
    )
    resultInvoke = fresultRepair(["dns", "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert "Refused: a live vaibify process holds it" in resultInvoke.stderr


@pytest.mark.parametrize("dictResolution, iExpected, sFragment", [
    ({"bAnswered": True, "listAddresses": ["192.0.2.10"]}, 0,
     "resolves names again"),
    ({"bAnswered": True, "listAddresses": []}, 1,
     "still does not resolve"),
    ({"bAnswered": False}, 2, "NOTHING was verified"),
])
def testRecreateRepairReportsWhatTheReprobeFound(
    monkeypatch, dictResolution, iExpected, sFragment,
):
    connectionRepair = _RepairConnection("", dictResolution)
    fnPrepareRepair(monkeypatch, connectionRepair)
    listRepairs = []
    monkeypatch.setattr(
        containerLifecycleRepair, "fdictRepairDirectlyUnderFlock",
        lambda config, sName, sOperation, fnAnnounce: listRepairs.append(
            (sName, sOperation),
        ),
    )
    resultInvoke = fresultRepair(["dns", "--recreate", "--yes"])
    fnAssertCleanExit(resultInvoke, iExpected)
    assert listRepairs == [(S_CONTAINER_NAME, "recreate")]
    assert "recreate the container from the image" in resultInvoke.stdout
    assert sFragment in resultInvoke.stdout + resultInvoke.stderr
    assert connectionRepair.listResolved == [
        (S_CONTAINER_NAME, "git.example.test"),
    ]


def testRepairOfAProjectWithNoTargetIsUnverified(monkeypatch):
    connectionRepair = _RepairConnection("", {})
    fnPrepareRepair(
        monkeypatch, connectionRepair,
        config=fconfigForRepair(listRepositories=[]),
    )
    monkeypatch.setattr(
        containerLifecycleRepair, "fdictRepairDirectlyUnderFlock",
        lambda *args: None,
    )
    resultInvoke = fresultRepair(["dns", "--yes"])
    fnAssertCleanExit(resultInvoke, 2)
    assert connectionRepair.listResolved == [], (
        "a name nobody configured was resolved"
    )


def fnPrepareHubRoutedRepair(monkeypatch, listResponses):
    """Pin a live hub holder and its control socket answers."""
    fnPrepareRepair(monkeypatch, _RepairConnection("", {
        "bAnswered": True, "listAddresses": ["192.0.2.10"],
    }))
    fnPinLockHolder(monkeypatch, commandRepair, DICT_LIVE_HOLDER)
    return flistScriptControlSocket(monkeypatch, listResponses)


def testHubRoutedRepairCarriesTheOperationAndPrintsAnnouncements(
    monkeypatch,
):
    listRequests = fnPrepareHubRoutedRepair(monkeypatch, [{
        "bAccepted": True, "listAnnouncements": ["restarting projectAlpha"],
    }])
    resultInvoke = fresultRepair(["dns", "--yes"])
    fnAssertCleanExit(resultInvoke, 0)
    assert listRequests == [(I_HUB_PORT, {
        "sOperation": hostControlChannel.S_SOCKET_OPERATION_REPAIR_CONTAINER,
        "sContainerName": S_CONTAINER_NAME, "sRepairOperation": "restart",
    })]
    assert "restarting projectAlpha" in resultInvoke.stdout


def testHubRefusedRepairExitsOne(monkeypatch):
    fnPrepareHubRoutedRepair(monkeypatch, [{
        "bAccepted": False, "sError": "a pipeline run is in flight",
    }])
    resultInvoke = fresultRepair(["dns", "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert "Refused by the hub: a pipeline run is in flight" in (
        resultInvoke.stderr
    )


def testUnreachableHubSocketExitsOne(monkeypatch):
    fnPrepareHubRoutedRepair(monkeypatch, [
        hostControlChannel.HostControlError("control socket refused"),
    ])
    resultInvoke = fresultRepair(["dns", "--yes"])
    fnAssertCleanExit(resultInvoke, 1)
    assert "Error: control socket refused" in resultInvoke.stderr


# ---------------------------------------------------------------------
# open
# ---------------------------------------------------------------------


def fresultOpen(sContainerName=S_CONTAINER_NAME):
    """Invoke ``vaibify open`` with separated streams."""
    return frunnerSeparatingStreams().invoke(
        commandOpen.fnOpenContainerCommand, [sContainerName],
    )


def testOpenWithNoHubAnywhereSaysToStartOne(monkeypatch):
    fnPinLockHolder(monkeypatch, commandOpen, {})
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert "no live vaibify hub is running" in resultInvoke.stderr


def testOpenOfAnUnheldContainerNamesTheLiveHubs(monkeypatch):
    fnPinLockHolder(monkeypatch, commandOpen, {})
    monkeypatch.setattr(
        hubSession, "flistFindLiveHubSessions",
        lambda: [{"iPort": 8123}, {"iPort": 8124}],
    )
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert "hub port(s) 8123, 8124" in resultInvoke.stderr


def testOpenOfAHolderWithoutAPortIsExplained(monkeypatch):
    fnPinLockHolder(monkeypatch, commandOpen, {"iPid": I_HUB_PID})
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert f"(pid={I_HUB_PID})" in resultInvoke.stderr
    assert "reports no hub port" in resultInvoke.stderr


@pytest.mark.parametrize("listResponses, sFragment", [
    ([{"bAccepted": False, "sError": "no owner to describe"}],
     "Refused by the hub: no owner to describe"),
    ([{"bAccepted": True, "iCurrentOwnerGeneration": 3},
      {"bAccepted": True, "bMinted": False, "sError": "generation moved"}],
     "Refused by the hub: generation moved"),
    ([hostControlChannel.HostControlError("socket went away")],
     "Error: socket went away"),
])
def testOpenMintRefusalsExitOne(monkeypatch, listResponses, sFragment):
    fnPinLockHolder(monkeypatch, commandOpen, DICT_LIVE_HOLDER)
    flistScriptControlSocket(monkeypatch, listResponses)
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert sFragment in resultInvoke.stderr


def fnPrepareMintedOpen(monkeypatch, tRedemption):
    """Pin a holder, a successful mint, and the HTTP redemption answer."""
    fnPinLockHolder(monkeypatch, commandOpen, DICT_LIVE_HOLDER)
    listRequests = flistScriptControlSocket(monkeypatch, [
        {"bAccepted": True, "iCurrentOwnerGeneration": 3},
        {"bAccepted": True, "bMinted": True,
         "sTransferCapability": S_CAPABILITY},
    ])
    listPosted = []

    def tRedeem(sBaseUrl, sCredential, sMethod, sPath, **dictKeywords):
        listPosted.append((sBaseUrl, sMethod, sPath, dictKeywords))
        if isinstance(tRedemption, Exception):
            raise tRedemption
        return tRedemption

    monkeypatch.setattr(hubSession, "ftSendHttpRequest", tRedeem)
    return listRequests, listPosted


def testOpenUnreadableRedemptionExitsOne(monkeypatch):
    fnPrepareMintedOpen(monkeypatch, (502, "Bad Gateway"))
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert "status 502 and an unreadable body" in resultInvoke.stderr


def testOpenRedemptionTransportErrorExitsOne(monkeypatch):
    fnPrepareMintedOpen(
        monkeypatch, hubSession.HubSessionError("connection reset"),
    )
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert "Error: connection reset" in resultInvoke.stderr


def testOpenRejectedTransferReportsTheOutcome(monkeypatch):
    fnPrepareMintedOpen(monkeypatch, (409, {
        "sOutcome": "rejected", "sMessage": "the capability expired",
    }))
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 1)
    assert "Transfer rejected: the capability expired" in resultInvoke.stderr


def testOpenTransferMintsAtTheDescribedGenerationAndLaunches(monkeypatch):
    from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
    monkeypatch.delenv(S_SUPPRESS_BROWSER_ENV, raising=False)
    listOpened = []
    monkeypatch.setattr(
        commandOpen.webbrowser, "open",
        lambda sUrl: listOpened.append(sUrl) or True,
    )
    listRequests, listPosted = fnPrepareMintedOpen(monkeypatch, (200, {
        "sOutcome": "transferred", "sContainerName": S_CONTAINER_NAME,
        "iOwnerGeneration": 4,
    }))
    resultInvoke = fresultOpen()
    fnAssertCleanExit(resultInvoke, 0)
    assert listRequests[1][1]["iExpectedOwnerGeneration"] == 3
    assert listPosted[0][:3] == (
        f"http://127.0.0.1:{I_HUB_PORT}", "POST", "/api/transfer",
    )
    assert listPosted[0][3]["dictFields"] == {"sCapability": S_CAPABILITY}
    assert listOpened == [
        f"http://127.0.0.1:{I_HUB_PORT}/#transfer={S_CAPABILITY}",
    ]
    assert "owner generation 4" in resultInvoke.stdout
    assert S_CAPABILITY not in resultInvoke.stdout, (
        "the one-time capability reached the terminal though a browser "
        "carried it"
    )
