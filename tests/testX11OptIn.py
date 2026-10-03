"""X11 forwarding is opt-in per project and refused beside network isolation.

Forwarding a display lets a container program read the screen and inject
input, so a project gets it only by setting ``x11Forwarding: true``.
These drive the real argument builder and the real Linux configuration
(only the host's xhost and DISPLAY are faked), so a default project is
proven to carry no DISPLAY, no socket mount and no xhost call.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.cli.configFieldPreflight import (
    flistDescribeUnusableConfiguration,
)
from vaibify.config import projectConfig
from vaibify.docker import containerManager, x11Forwarding


def _fconfigForLaunch(bX11Forwarding=False, bNetworkIsolation=False):
    features = SimpleNamespace(
        bGpu=False, bClaude=False, bCodex=False, bGemini=False,
        bAntigravity=False, bOpenCode=False, bCline=False,
        bOpenHands=False, bPi=False,
    )
    return SimpleNamespace(
        sProjectName="optInProject", sWorkspaceRoot="/workspace",
        sContainerUser="researcher", listPorts=[], listBindMounts=[],
        listSecrets=[], features=features,
        bNetworkIsolation=bNetworkIsolation, bX11Forwarding=bX11Forwarding,
    )


@pytest.fixture
def fixtureLinuxHost(monkeypatch):
    """A Linux host with a display; records every xhost call."""
    listXhostCalls = []
    monkeypatch.setattr(x11Forwarding.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":7")
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setattr(
        x11Forwarding, "_fsFindXhost", lambda: "xhost")
    monkeypatch.setattr(
        x11Forwarding, "_fnRunBestEffort",
        lambda saArgs: listXhostCalls.append(saArgs))
    return listXhostCalls


@pytest.mark.falsification
def testAProjectThatDidNotOptInGetsNoDisplayNoSocketAndNoXhost(
    fixtureLinuxHost,
):
    """Kills: forwarding X11 into every container regardless of the opt-in."""
    saRunArgs = containerManager.flistBuildRunArgs(_fconfigForLaunch())
    assert not any("DISPLAY" in sArg for sArg in saRunArgs)
    assert not any(".X11-unix" in sArg for sArg in saRunArgs)
    assert fixtureLinuxHost == []


def testAnOptedInLinuxProjectGetsTheDisplayTheSocketAndANarrowGrant(
    fixtureLinuxHost,
):
    saRunArgs = containerManager.flistBuildRunArgs(
        _fconfigForLaunch(bX11Forwarding=True))
    assert "DISPLAY=:7" in saRunArgs
    assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in saRunArgs
    assert ["xhost", "+SI:localuser:alice"] in fixtureLinuxHost
    assert not any(
        "+local:" in sArg for saCall in fixtureLinuxHost for sArg in saCall)


@pytest.mark.falsification
def testForwardingBesideNetworkIsolationIsRefusedNamingBothKeys(
    fixtureLinuxHost,
):
    """Kills: forwarding a display into a container sealed with --network none."""
    config = _fconfigForLaunch(bX11Forwarding=True, bNetworkIsolation=True)
    with pytest.raises(RuntimeError) as errorRaised:
        containerManager.flistBuildRunArgs(config)
    sMessage = str(errorRaised.value)
    assert "x11Forwarding" in sMessage and "networkIsolation" in sMessage
    assert fixtureLinuxHost == []


def testTheOptInDefaultsOffAndRoundTripsThroughYaml(tmp_path):
    assert projectConfig.ProjectConfig().bX11Forwarding is False
    sPath = str(tmp_path / "vaibify.yml")
    config = projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "x11Forwarding": True})
    projectConfig.fnSaveToFile(config, sPath)
    assert projectConfig.fconfigLoadFromFile(sPath).bX11Forwarding is True


def testTheBuildPreflightComplainsAboutBothKeys():
    listComplaints = flistDescribeUnusableConfiguration(
        SimpleNamespace(bX11Forwarding=True, bNetworkIsolation=True))
    assert len(listComplaints) == 1
    assert "x11Forwarding" in listComplaints[0]
    assert flistDescribeUnusableConfiguration(
        SimpleNamespace(bX11Forwarding=True, bNetworkIsolation=False)) == []
    assert flistDescribeUnusableConfiguration(
        SimpleNamespace(bX11Forwarding=False, bNetworkIsolation=True)) == []


@patch("vaibify.docker.containerManager.flistConfigureX11Args")
def testTheDefaultLaunchNeverEvenAsksTheHostXModule(mockConfigure):
    containerManager.flistBuildRunArgs(_fconfigForLaunch())
    mockConfigure.assert_not_called()
