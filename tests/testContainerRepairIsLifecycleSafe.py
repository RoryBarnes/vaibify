"""A repair is a lifecycle operation, and it must behave like one.

Restarting a container re-runs the entrypoint and kills every shell,
agent and pipeline step inside it. A repair that does that quietly,
over live work, or from a tag that has moved is not a fix -- it is a
second incident on top of the first.

The properties pinned here are the ones that would be quietly lost:
the refusal that cannot be repaired by a restart is REFUSED rather
than attempted, the recreation pins an image IDENTITY, live work
refuses by name, and the repair re-probes instead of asserting
success.
"""

from unittest.mock import patch

import pytest

from vaibify.cli import commandRepair
from vaibify.docker import containerLifecycleRepair
from vaibify.docker.containerLifecycleRepair import RepairRefusedError


class _ConnectionStub:
    """A Docker connection that answers only what these tests read."""

    def __init__(self, sResolvConf="", listAddresses=()):
        self.sResolvConf = sResolvConf
        self.listAddresses = list(listAddresses)

    def fbaFetchFile(self, sContainerId, sPath):
        return self.sResolvConf.encode("utf-8")

    def fdictResolveHostnameInContainer(self, *args, **kwargs):
        return {"bAnswered": True, "listAddresses": self.listAddresses}


class _ConfigStub:
    sProjectName = "repairProbe"
    listRepositories = [{"url": "https://example.test/team/thing.git"}]
    features = None


def _fnPatchInspect(jsonInspect):
    """Patch the container inspect both modules read."""
    return patch(
        "vaibify.docker.containerManager.fjsonInspectContainer",
        return_value=jsonInspect,
    )


@pytest.mark.falsification
def test_an_explicit_dns_container_is_refused_a_restart():
    """A restart cannot change a baked-in resolver, so it is not offered.

    Kills: In commandRepair._fiRepairDnsForProject, drop the
    explicit-DNS branch, so the command restarts a container whose
    baked-in resolver a restart cannot change.
    """
    listRepairs = []
    with _fnPatchInspect({"HostConfig": {"Dns": ["10.0.0.1"]}}), patch.object(
        commandRepair, "_fconnectionOpenDockerOrExit",
        return_value=_ConnectionStub(),
    ), patch.object(
        commandRepair, "_fiRunRepair",
        side_effect=lambda *a: listRepairs.append(a) or 0,
    ):
        iExit = commandRepair._fiRepairDnsForProject(
            _ConfigStub(), False, True,
        )
    assert iExit == 1
    assert listRepairs == [], (
        "the repair restarted a container whose resolver a restart "
        "cannot change"
    )


def test_the_explicit_dns_refusal_names_the_recreation_it_needs():
    """A refusal that does not say what WOULD work leaves nowhere to go."""
    with _fnPatchInspect({"HostConfig": {"Dns": ["10.0.0.1"]}}), patch.object(
        commandRepair, "_fconnectionOpenDockerOrExit",
        return_value=_ConnectionStub(),
    ), patch.object(commandRepair.click, "echo") as mockEcho:
        commandRepair._fiRepairDnsForProject(_ConfigStub(), False, True)
    sOutput = " ".join(str(tCall[0][0]) for tCall in mockEcho.call_args_list)
    assert "--recreate" in sOutput
    assert "outside vaibify" in sOutput


def test_a_default_bridge_container_is_restarted():
    """The case a restart DOES repair still gets repaired."""
    listRepairs = []
    with _fnPatchInspect({"HostConfig": {"NetworkMode": "bridge"}}), patch.object(
        commandRepair, "_fconnectionOpenDockerOrExit",
        return_value=_ConnectionStub(listAddresses=["93.184.216.34"]),
    ), patch.object(
        commandRepair, "_fiRunRepair",
        side_effect=lambda *a: listRepairs.append(a) or 0,
    ):
        iExit = commandRepair._fiRepairDnsForProject(
            _ConfigStub(), False, True,
        )
    assert iExit == 0
    assert listRepairs and listRepairs[0][2] == "restart"


@pytest.mark.falsification
def test_a_repair_that_did_not_work_is_reported_as_failed():
    """Re-probing is the point; without it the researcher walks away wrong.

    Kills: In commandRepair._fiRepairDnsForProject, return 0 from the
    repair's own exit code instead of calling _fiReportReprobe, so a
    repair that changed nothing reads as a fix.
    """
    with _fnPatchInspect({"HostConfig": {"NetworkMode": "bridge"}}), patch.object(
        commandRepair, "_fconnectionOpenDockerOrExit",
        return_value=_ConnectionStub(listAddresses=[]),
    ), patch.object(commandRepair, "_fiRunRepair", return_value=0):
        iExit = commandRepair._fiRepairDnsForProject(
            _ConfigStub(), False, True,
        )
    assert iExit == 1


def test_a_recreation_refuses_when_the_image_identity_is_unknown():
    """`<project>:latest` may have moved; the repair will not fall back."""
    with _fnPatchInspect({}), patch.object(
        containerLifecycleRepair, "flistNameLiveWork", return_value=[],
    ):
        with pytest.raises(RepairRefusedError) as errorRaised:
            containerLifecycleRepair.fdictRecreateUnderJournal(
                _ConfigStub(), "repairProbe",
            )
    assert "could not be resolved to an ID" in str(errorRaised.value)


@pytest.mark.falsification
def test_a_recreation_relaunches_from_the_image_id_not_the_tag():
    """A recreation pins the image identity, never the moving tag.

    Kills: In containerLifecycleRepair._fsStopAndRelaunch, relaunch
    from `<project>:latest` instead of the resolved image identity.
    """
    listLaunched = []
    with _fnPatchInspect({"Image": "sha256:abc123"}), patch.object(
        containerLifecycleRepair, "flistNameLiveWork", return_value=[],
    ), patch.object(
        containerLifecycleRepair, "_fsPrepareJournalRecord",
        return_value="op1",
    ), patch(
        "vaibify.config.operationJournal.fnSettleOperation",
    ), patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value={"bExists": False, "bRunning": False,
                      "sStatus": "not found"},
    ), patch(
        "vaibify.docker.containerManager."
        "fsRecreateContainerDetachedFromImage",
        side_effect=lambda config, sImage: listLaunched.append(sImage) or "cid",
    ):
        dictOutcome = containerLifecycleRepair.fdictRecreateUnderJournal(
            _ConfigStub(), "repairProbe",
        )
    assert listLaunched == ["sha256:abc123"]
    assert dictOutcome["sImageIdentity"] == "sha256:abc123"


def test_live_work_refuses_the_repair_and_names_it():
    """"Try again later" is unactionable; the researcher gets the id."""
    with patch(
        "vaibify.config.operationJournal.fdictResolveContainerJournal",
        return_value={
            "sResolution": "BUSY",
            "listBusyOperationIds": ["deadbeef"],
            "sQuarantineReason": "",
        },
    ):
        with pytest.raises(RepairRefusedError) as errorRaised:
            containerLifecycleRepair.fdictRestartUnderJournal("repairProbe")
    assert "deadbeef" in str(errorRaised.value)


def test_a_quarantined_container_is_sent_to_reconcile():
    """A repair is not a way around a quarantine."""
    with patch(
        "vaibify.config.operationJournal.fdictResolveContainerJournal",
        return_value={
            "sResolution": "QUARANTINED",
            "listBusyOperationIds": [],
            "sQuarantineReason": "a write never landed",
        },
    ):
        with pytest.raises(RepairRefusedError) as errorRaised:
            containerLifecycleRepair.fdictRestartUnderJournal("repairProbe")
    assert "vaibify reconcile" in str(errorRaised.value)


def test_the_consequences_are_stated_before_anything_happens():
    """The researcher is told what a restart does, in the same breath."""
    sDescription = containerLifecycleRepair.fsDescribeRestartConsequences(
        "repairProbe",
    )
    assert "entrypoint" in sDescription
    assert "kill every shell" in sDescription
    assert "workspace volume" in sDescription


# -----------------------------------------------------------------------
# The two ways a repair used to overstate itself.
# -----------------------------------------------------------------------


@pytest.mark.falsification
def test_a_repair_nobody_could_verify_is_not_reported_as_a_fix():
    """An unprobed repair exits 2 and says nothing was established.

    The defect: with no name the project depends on, the re-probe
    returned True and the caller printed that the container "resolves
    names again" -- a claim about a probe that never ran, after a
    restart that killed every shell in the container.

    Kills: In commandRepair._fsReprobeResolution, answer
    S_REPROBE_CONFIRMED instead of S_REPROBE_UNVERIFIED when the
    project names no host to resolve.
    """
    class _NoRemotes:
        sProjectName = "repairProbe"
        listRepositories = []
        features = None

    with _fnPatchInspect({"HostConfig": {"NetworkMode": "bridge"}}), patch.object(
        commandRepair, "_fconnectionOpenDockerOrExit",
        return_value=_ConnectionStub(),
    ), patch.object(commandRepair, "_fiRunRepair", return_value=0):
        iExit = commandRepair._fiRepairDnsForProject(
            _NoRemotes(), False, True,
        )
    assert iExit == commandRepair.I_REPAIR_UNVERIFIED
    assert iExit != commandRepair.I_REPAIR_CONFIRMED


def test_a_container_that_cannot_answer_a_lookup_is_unverified():
    """An exec that never ran establishes nothing either way."""
    class _MuteConnection(_ConnectionStub):
        def fdictResolveHostnameInContainer(self, *args, **kwargs):
            return {"bAnswered": False, "sError": "exec refused"}

    with _fnPatchInspect({"HostConfig": {"NetworkMode": "bridge"}}), patch.object(
        commandRepair, "_fconnectionOpenDockerOrExit",
        return_value=_MuteConnection(),
    ), patch.object(commandRepair, "_fiRunRepair", return_value=0):
        iExit = commandRepair._fiRepairDnsForProject(
            _ConfigStub(), False, True,
        )
    assert iExit == commandRepair.I_REPAIR_UNVERIFIED


@pytest.mark.falsification
def test_a_recreate_is_refused_while_a_hub_holds_the_container():
    """Recreation gives a NEW id, and the hub's session is bound to the old.

    The owner record holds sContainerId and the workflow and path
    caches are keyed by it, so a hub-lane recreate would leave a live
    session authorized and cached against a container that no longer
    exists.

    Kills: In commandRepair._fiRunRepair, route a recreate to the live
    hub instead of refusing it.
    """
    listRouted = []
    with patch.object(
        commandRepair, "fdictReadLockHolder",
        return_value={"iPid": 4242, "iPort": 8050},
    ), patch.object(
        commandRepair, "_fiRouteRepairToLiveHub",
        side_effect=lambda *a: listRouted.append(a) or 0,
    ):
        iExit = commandRepair._fiRunRepair(
            _ConfigStub(), "repairProbe", "recreate",
        )
    assert iExit == 1
    assert listRouted == []


def test_a_restart_still_routes_to_the_live_hub():
    """A restart keeps the id, so every hub-side binding stays true."""
    listRouted = []
    with patch.object(
        commandRepair, "fdictReadLockHolder",
        return_value={"iPid": 4242, "iPort": 8050},
    ), patch.object(
        commandRepair, "_fiRouteRepairToLiveHub",
        side_effect=lambda *a: listRouted.append(a) or 0,
    ):
        iExit = commandRepair._fiRunRepair(
            _ConfigStub(), "repairProbe", "restart",
        )
    assert iExit == 0
    assert len(listRouted) == 1


def test_the_hub_refuses_a_recreate_itself():
    """A refusal only the client enforces is not a refusal."""
    import asyncio

    from vaibify.gui import hostControlChannel

    class _AppStub:
        state = None

    dictResponse = asyncio.get_event_loop().run_until_complete(
        hostControlChannel._fdictHandleRepairContainer(
            _AppStub(), {}, {
                "sContainerName": "repairProbe",
                "sRepairOperation": "recreate",
            },
        )
    )
    assert dictResponse["bAccepted"] is False
    assert "new id" in dictResponse["sError"].lower()
