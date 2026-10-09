"""An AI agent with a small memory limit is advised, never refused.

The threshold (5 GB) and the sentence have one authority,
``resourceAdequacy.flistDescribeResourceAdvisories``. The second half of
this file proves each surface calls it rather than keeping a copy: the
function is replaced by a sentinel, and each surface must then say the
sentinel, on the model of ``testDeterminismRowMatchesItsGate``.
"""

from types import SimpleNamespace

import pytest

from vaibify.config import resourceAdequacy
from vaibify.docker.imageBuilder import (
    T_AGENT_OVERLAY_NAMES,
    fsFeatureFieldForOverlay,
)


S_SENTINEL = "SENTINEL advisory from the one authority"


def _fconfig(fMemoryLimitGigabytes, listAgentFields=("bClaude",)):
    features = SimpleNamespace(**{
        fsFeatureFieldForOverlay(sOverlay): False
        for sOverlay in T_AGENT_OVERLAY_NAMES
    })
    for sField in listAgentFields:
        setattr(features, sField, True)
    return SimpleNamespace(
        fMemoryLimitGigabytes=fMemoryLimitGigabytes, iCpuLimit=0,
        features=features, sProjectName="adequacy-lane-project",
        listSecrets=[])


@pytest.mark.parametrize("sOverlay", T_AGENT_OVERLAY_NAMES)
def testEveryAgentOverlayIsAdvisedUnderTheThreshold(sOverlay):
    listAdvisories = resourceAdequacy.flistDescribeResourceAdvisories(
        _fconfig(1.0, [fsFeatureFieldForOverlay(sOverlay)]))
    assert listAdvisories == [
        "This project runs an AI agent with a 1 GB memory limit. Agents "
        "and the jobs they start often need several GB; 5 GB is a "
        "starting point, not a requirement. Raise or remove the limit in "
        "Settings."]


@pytest.mark.parametrize("fMemory,iExpected", [
    (0.0, 0), (1.0, 1), (4.75, 1), (5.0, 0), (6.0, 0),
])
def testOnlyAFiniteLimitBelowFiveGigabytesIsAdvised(fMemory, iExpected):
    assert len(resourceAdequacy.flistDescribeResourceAdvisories(
        _fconfig(fMemory))) == iExpected


def testAProjectWithNoAgentIsNeverAdvised():
    assert resourceAdequacy.flistDescribeResourceAdvisories(
        _fconfig(1.0, [])) == []


# ---------------------------------------------------------------------
# Every surface calls the one authority
# ---------------------------------------------------------------------

@pytest.fixture
def fnInstallSentinel(monkeypatch):
    monkeypatch.setattr(
        resourceAdequacy, "flistDescribeResourceAdvisories",
        lambda config: [S_SENTINEL])


def testTheDoctorSaysTheAuthoritysSentence(fnInstallSentinel, monkeypatch):
    from vaibify.cli import doctorHostChecks
    from vaibify.docker import dockerContext
    for jsonInfo in ({}, {"NCPU": 8, "MemTotal": 16 * 2 ** 30}):
        monkeypatch.setattr(
            dockerContext, "_fdictReadDockerInfoJson", lambda j=jsonInfo: j)
        listResults = doctorHostChecks.flistCheckResourceAllocation(
            _fconfig(1.0))
        listAdvised = [r for r in listResults if r.sMessage == S_SENTINEL]
        assert len(listAdvised) == 1
        assert listAdvised[0].sLevel == "warn"


def testTheStartPreflightSaysTheAuthoritysSentence(
    fnInstallSentinel, monkeypatch,
):
    from vaibify.cli import commandStart
    from vaibify.cli.preflightResult import PreflightResult
    fresultOk = PreflightResult(sName="ok", sLevel="ok", sMessage="ok")
    monkeypatch.setattr(commandStart, "fpreflightDaemon", lambda s: fresultOk)
    monkeypatch.setattr(commandStart, "_fpreflightImage", lambda c: fresultOk)
    monkeypatch.setattr(
        commandStart, "_fpreflightContainerName", lambda c: fresultOk)
    for sHelper in (
        "flistPreflightSecrets", "_flistPreflightPorts",
        "_flistPreflightBindMounts", "_flistPreflightBindMountFormats",
        "_flistPreflightColimaSharedRoots",
    ):
        monkeypatch.setattr(commandStart, sHelper, lambda c: [])
    monkeypatch.setattr(commandStart, "fpreflightColimaVersion", lambda: None)
    listResults = commandStart.flistRunStartPreflight(_fconfig(1.0))
    assert [r.sMessage for r in listResults if r.sLevel == "warn"] == [
        S_SENTINEL]


def testTheReadinessAnswerCarriesTheAuthoritysSentence(
    fnInstallSentinel, monkeypatch,
):
    from vaibify.gui.routes import systemRoutes
    monkeypatch.setattr(
        systemRoutes, "_fdictProbeContainerReadiness",
        lambda *a: {"bReady": True, "sStatus": "ok", "sReason": "",
                    "saWarnings": [], "iWarningCount": 0})
    monkeypatch.setattr(
        systemRoutes, "_flistDescribeUnresolvableSecrets", lambda *a: [])
    monkeypatch.setattr(
        systemRoutes, "_flistDescribeConfigurationDrift", lambda *a: [])
    monkeypatch.setattr(
        systemRoutes, "_ftDescribeX11Findings", lambda *a: ([], []))
    monkeypatch.setattr(
        systemRoutes, "_flistDescribeResourceLimitDrift", lambda *a: [])
    monkeypatch.setattr(
        systemRoutes, "_fconfigForContainerOrNone", lambda *a: _fconfig(1.0))
    dictReadiness = systemRoutes._fdictReadinessWithSecretWarnings(
        None, "adequacy0container0id")
    assert dictReadiness["listResourceAdvisories"] == [S_SENTINEL]
    assert S_SENTINEL not in dictReadiness["saWarnings"]


def testTheSettingsSaveCarriesTheAuthoritysSentence(tmp_path, monkeypatch):
    """The save answers with the advisory for the file as it now stands."""
    from tests.testContainerLifecycleGating import (
        fclientAuthenticated, fnRegisterProject,
    )
    import os
    from unittest.mock import patch
    from vaibify.config import containerLock, registryManager
    from vaibify.gui import pipelineServer
    from tests.testAgentLaneEnforcement import MockDockerConnection
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    sRegistry = str(tmp_path / ".vaibify")
    monkeypatch.setattr(registryManager, "_S_REGISTRY_DIRECTORY", sRegistry)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistry, "registry.json"))
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistry, "registry.lock"))
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    with fclientAuthenticated(appHub) as client:
        sDirectory = fnRegisterProject(client, tmp_path, "adequacy-lane")
        with open(os.path.join(sDirectory, "vaibify.yml"), "a") as fileConfig:
            fileConfig.write("features:\n  claude: true\n")
        dictSaved = client.post(
            "/api/containers/adequacy-lane/settings",
            json={"iCpuLimit": 0, "fMemoryLimitGigabytes": 2.0}).json()
        dictRaised = client.post(
            "/api/containers/adequacy-lane/settings",
            json={"iCpuLimit": 0, "fMemoryLimitGigabytes": 8.0}).json()
    assert dictSaved["listResourceAdvisories"] == [
        "This project runs an AI agent with a 2 GB memory limit. Agents "
        "and the jobs they start often need several GB; 5 GB is a "
        "starting point, not a requirement. Raise or remove the limit in "
        "Settings."]
    assert dictRaised["listResourceAdvisories"] == []
