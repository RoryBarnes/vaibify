"""The council Docker gateway is the single SDK authority, and it verifies.

Remediation R4's architectural contract, in three falsifiable pieces:

1. No council module other than ``agentCouncilDockerGateway`` may reach
   the Docker SDK — the scan names file and line on any hit, so a
   regression is a build failure, not a review note.
   ``agentCouncilContext.py`` is the one allowed exception (it uses the
   reviewed ``DockerConnection`` adapter, a different governed lane),
   and even it may never construct its own client.
2. Every gateway operation resolves an opaque, gateway-minted handle; a
   raw container id, a guessed token, or a foreign handle is refused
   before any daemon call.
3. A handle-keyed destruction verifies the target's council label
   against the handle's reservation id FIRST and refuses — destroying
   nothing — on a mismatch, which is what makes it impossible to aim
   council destruction at the active project container. Driven with a
   fake docker double whose container NAME differs from its ID, per the
   repository's name-vs-id lesson.
"""

import pathlib

import pytest

from vaibify.gui import agentCouncilDockerGateway as gateway
from vaibify.gui import agentCouncilRegistry as registry
from vaibify.gui import agentCouncilRunner

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_GUI = PATH_REPOSITORY / "vaibify" / "gui"

# Any of these tokens in a council module is Docker-SDK reach: the
# client constructor, the three SDK attribute families, and the two
# dockerConnection acquisition helpers.
LIST_FORBIDDEN_SDK_TOKENS = [
    "from_env",
    ".containers.",
    ".networks.",
    ".api.",
    "_fmoduleGetDocker",
    "_fnEnsureDockerHost",
]

S_GATEWAY_BASENAME = "agentCouncilDockerGateway.py"
S_CONTEXT_BASENAME = "agentCouncilContext.py"


def flistCouncilModulePaths():
    """Return every council module path under vaibify/gui."""
    listPaths = sorted(PATH_GUI.glob("agentCouncil*.py"))
    assert listPaths, "no agentCouncil modules found; the scan is broken"
    return listPaths


def testOnlyTheGatewayReachesTheDockerSdk():
    """No council module outside the gateway carries an SDK token.

    Kills: reintroducing a ``dockerCouncil.api.*`` call (or a client
    constructor) into the runner, egress, providers, registry, or any
    future council module. ``agentCouncilContext.py`` is excluded here
    because it uses the DockerConnection adapter — a separately
    reviewed lane — and is pinned by its own narrower assertion below.
    """
    listViolations = []
    for pathModule in flistCouncilModulePaths():
        if pathModule.name in (S_GATEWAY_BASENAME, S_CONTEXT_BASENAME):
            continue
        for iLineNumber, sLine in enumerate(
                pathModule.read_text().splitlines(), start=1):
            for sToken in LIST_FORBIDDEN_SDK_TOKENS:
                if sToken in sLine:
                    listViolations.append(
                        f"{pathModule.name}:{iLineNumber}: {sToken!r} in "
                        f"{sLine.strip()!r}")
    assert listViolations == [], (
        "Docker-SDK reach outside the council gateway:\n"
        + "\n".join(listViolations)
    )


def testTheContextModuleNeverConstructsItsOwnClient():
    """The adapter-lane exception is narrow: no ``from_env`` in context."""
    sSource = (PATH_GUI / S_CONTEXT_BASENAME).read_text()
    assert "from_env" not in sSource, (
        "agentCouncilContext.py may use the DockerConnection adapter but "
        "must never construct its own Docker client"
    )


# ── the gateway refuses an unknown handle ──────────────────────────


class _RecordingDockerImageApi:
    """Record the immutable proxy-image pull request."""

    def __init__(self):
        self.listCalls = []

    def pull(self, sImageReference):
        self.listCalls.append(("pull", sImageReference))


class _ImageAwareDockerDouble:
    """Expose only the image-pull surface the resolver consults."""

    def __init__(self):
        self.api = _RecordingDockerImageApi()


def testTheProxyCreatePullsThePinnedImageOnlyOnAMiss():
    """The create proves presence; the pull runs only on ImageNotFound.

    The pin is content-addressed, so a local image that satisfies the
    create IS the reviewed bytes; a pull-always resolver refused a
    researcher's retry over broken registry DNS while those bytes sat
    on disk (2026-08-27).
    """
    import docker as moduleDocker
    dockerRecording = _ImageAwareDockerDouble()
    listCreateCalls = []

    def fcontainerCreateMissingOnce():
        listCreateCalls.append("create")
        if len(listCreateCalls) == 1:
            raise moduleDocker.errors.ImageNotFound("pinned image absent")
        return {"sProxy": "created"}

    dictCreated = gateway._fcontainerCreateProxyOrPullOnMiss(
        dockerRecording, fcontainerCreateMissingOnce)

    assert dictCreated == {"sProxy": "created"}
    assert listCreateCalls == ["create", "create"]
    assert dockerRecording.api.listCalls == [
        ("pull", gateway.agentCouncilEgress.S_PROXY_IMAGE),
    ]


def testAPresentPinnedImageIsNeverRePulled():
    """A create the local image satisfies touches no network at all."""
    dockerRecording = _ImageAwareDockerDouble()

    dictCreated = gateway._fcontainerCreateProxyOrPullOnMiss(
        dockerRecording, lambda: {"sProxy": "created"})

    assert dictCreated == {"sProxy": "created"}
    assert dockerRecording.api.listCalls == []


def _fdictBuildGatewayWithRegistry(dockerCouncil=None):
    dictRegistry = registry.fdictCreateCouncilRegistry()
    return gateway.fdictCreateCouncilDockerGateway(
        dockerCouncil, dictRegistry)


def testEveryHandleKeyedOperationRefusesAnUnknownHandle():
    """A raw container id (or any unminted token) is refused up front.

    Kills: an operation that resolves its target from caller input
    instead of the gateway's own handle map. The fake client records
    every attribute access, so the assertion also proves NO daemon
    call was attempted before the refusal.
    """
    dockerRecording = _RecordingDockerDouble()
    dictGateway = _fdictBuildGatewayWithRegistry(dockerRecording)
    with pytest.raises(gateway.CouncilGatewayError):
        gateway.fdictDestroyAndSettle(dictGateway, "raw-container-id")
    with pytest.raises(gateway.CouncilGatewayError):
        gateway.fnCopySnapshotIntoRunner(
            dictGateway, "raw-container-id", b"")
    with pytest.raises(gateway.CouncilGatewayError):
        gateway.fdictExecuteBoundedTurn(
            dictGateway, "raw-container-id", ["/bin/sh", "-c", "true"])
    assert dockerRecording.listApiCalls == [], (
        "the gateway touched the daemon before refusing an unknown "
        f"handle: {dockerRecording.listApiCalls}"
    )


# ── label-verified destruction ─────────────────────────────────────


class _RecordingDockerApi:
    """An ``api`` double that records calls and answers inspects."""

    def __init__(self, listApiCalls, dictInspectAnswer=None):
        self._listApiCalls = listApiCalls
        self._dictInspectAnswer = dictInspectAnswer

    def inspect_container(self, sContainerId):
        self._listApiCalls.append(("inspect_container", sContainerId))
        if self._dictInspectAnswer is None:
            raise RuntimeError("no inspect answer scripted")
        return self._dictInspectAnswer

    def remove_container(self, sContainerId, force=False, v=False):
        self._listApiCalls.append(("remove_container", sContainerId))

    def kill(self, sContainerId):
        self._listApiCalls.append(("kill", sContainerId))


class _RecordingDockerDouble:
    """A client double: name != id everywhere, every call recorded."""

    def __init__(self, dictInspectAnswer=None):
        self.listApiCalls = []
        self.api = _RecordingDockerApi(self.listApiCalls, dictInspectAnswer)


def _tMintHandleForFakeContainer(dictGateway, sReservationId,
                                 sContainerId, sContainerName):
    """Reserve through the real registry and bind a handle record.

    The reservation is real (budget held, epoch minted); only the SDK
    create is bypassed, because the double under test answers inspect,
    not create. Name and id are DISTINCT by construction.
    """
    assert sContainerName != sContainerId
    dictReserved = registry.fdictReserveRunner(
        dictGateway["dictRegistry"], "campaign-authority", sReservationId,
        "claude", {"iMemoryBytes": 1024, "fCpuCount": 1.0})
    assert dictReserved["bReserved"] is True
    registry.fnMarkRunnerCreated(
        dictGateway["dictRegistry"], sReservationId, sContainerId)
    sHandle = "handle-under-test"
    dictGateway["dictHandlesById"][sHandle] = {
        "sReservationId": sReservationId,
        "sContainerId": sContainerId,
        "sContainerName": sContainerName,
        "sCampaignId": "campaign-authority",
        "sRole": agentCouncilRunner.S_ROLE_RUNNER,
        "iEpoch": dictReserved["dictReservation"]["iEpoch"],
    }
    return sHandle


def testDestructionRefusesAContainerLackingTheCouncilLabel():
    """A target without the council label is never removed.

    The inspected container carries NO labels at all — the exact shape
    of the active project container — so the destroy must raise and the
    double must show an inspect but NO remove and NO kill.
    """
    dockerRecording = _RecordingDockerDouble(dictInspectAnswer={
        "Id": "project-container-id-0001",
        "Name": "/the-active-project-container",
        "Config": {"Labels": {}},
    })
    dictGateway = _fdictBuildGatewayWithRegistry(dockerRecording)
    sHandle = _tMintHandleForFakeContainer(
        dictGateway, "council-campaign-authority-res1",
        "project-container-id-0001", "the-active-project-container")
    with pytest.raises(gateway.CouncilGatewayError):
        gateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert ("remove_container", "project-container-id-0001") not in (
        dockerRecording.listApiCalls)
    assert ("kill", "project-container-id-0001") not in (
        dockerRecording.listApiCalls)
    assert ("inspect_container", "project-container-id-0001") in (
        dockerRecording.listApiCalls)
    # The refusal destroyed nothing, so the reservation must still be
    # live — the budget stays held until a verified settle.
    assert "council-campaign-authority-res1" in (
        dictGateway["dictRegistry"]["dictReservationsById"])


def testDestructionRefusesAMismatchedCouncilLabel():
    """A council label naming ANOTHER reservation is also a refusal.

    Kills: verifying only that SOME council label exists rather than
    that it equals this handle's reservation id.
    """
    dockerRecording = _RecordingDockerDouble(dictInspectAnswer={
        "Id": "other-runner-id-0002",
        "Config": {"Labels": {
            agentCouncilRunner.S_COUNCIL_LABEL: "someone-elses-reservation",
        }},
    })
    dictGateway = _fdictBuildGatewayWithRegistry(dockerRecording)
    sHandle = _tMintHandleForFakeContainer(
        dictGateway, "council-campaign-authority-res2",
        "other-runner-id-0002", "other-runner-name")
    with pytest.raises(gateway.CouncilGatewayError):
        gateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert ("remove_container", "other-runner-id-0002") not in (
        dockerRecording.listApiCalls)


def testIndeterminateInspectQuarantinesWithoutARemovalAttempt():
    """A daemon that cannot answer the identity inspect quarantines.

    No removal may be attempted over an unverified identity, and the
    reservation must stay visible with its budget held — the corrected
    quarantine semantics R4 exists for.
    """
    dockerRecording = _RecordingDockerDouble(dictInspectAnswer=None)
    dictGateway = _fdictBuildGatewayWithRegistry(dockerRecording)
    sHandle = _tMintHandleForFakeContainer(
        dictGateway, "council-campaign-authority-res3",
        "unanswerable-id-0003", "unanswerable-name")
    dictOutcome = gateway.fdictDestroyAndSettle(dictGateway, sHandle)
    assert dictOutcome["sOutcome"] == (
        agentCouncilRunner.S_OUTCOME_QUARANTINED)
    assert ("remove_container", "unanswerable-id-0003") not in (
        dockerRecording.listApiCalls)
    dictReservation = dictGateway["dictRegistry"][
        "dictReservationsById"]["council-campaign-authority-res3"]
    assert dictReservation["sStatus"] == registry.S_RESERVATION_QUARANTINED
    assert gateway.flistDescribeQuarantinedReservations(
        dictGateway, "campaign-authority") == [{
            "sReservationId": "council-campaign-authority-res3",
            "sCampaignId": "campaign-authority",
            "sProvider": "claude",
        }]


# ── an executor that cannot prove destruction reverts the claim ────


def testARaisingBaselineExecutorRevertsTheConfirmedClaim():
    """When the baseline executor raises, no evidence becomes confirmed.

    The R4 propagation contract end-to-end at the engine seam: the
    executor raising (which is exactly what an unproven sandbox
    destruction now does) makes ``_fnRecordBaselineClaim`` revert the
    claim to ``asserted`` and ledger nothing.
    """
    from tests.agentCouncilHarness import (
        fdictDecideCompleted, fdictMakeTurnResult, fixtureBuildCouncil)

    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "pytest -q"}]

    def ffnDecide(sHandle, dictTurnRequest):
        if sHandle == "A" and dictTurnRequest["sPhase"] == (
                "independentProposals"):
            return fdictDecideCompleted(
                fdictMakeTurnResult("accept", listEvidence=listEvidence))
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))

    def ffnBaselineExecuteRaises(dictRequest):
        raise gateway.CouncilGatewayError(
            "baseline sandbox destruction is unproven (quarantined)")

    fixture = fixtureBuildCouncil(
        [{"sHandle": "A", "sProvider": "prov-a",
          "sRequestedModel": "model-a"},
         {"sHandle": "B", "sProvider": "prov-b",
          "sRequestedModel": "model-b"}],
        ffnDecide, sChairbotHandle="A",
        ffnBaselineExecute=ffnBaselineExecuteRaises)
    dictOut = fixture.fdictDrive()
    dictClaim = None
    for dictRound in dictOut["listRounds"]:
        for dictRecord in dictRound["dictTurnsByPhase"].get(
                "independentProposals", []):
            if fixture.fsHandleForId(dictRecord["sParticipantId"]) == "A":
                dictClaim = dictRecord["dictResult"]["listEvidence"][0]
    assert dictClaim is not None
    assert dictClaim["sStatus"] == "asserted"
    assert dictClaim["sReversionReason"].startswith(
        "baselineExecutorFailed")
    assert fixture.ledger.flistCollectEntries() == []
