"""X11 forwarding is opt-in per project and refused beside network isolation.

Forwarding a display lets a container program draw on the screen, so a
project gets it only by setting ``x11Forwarding: true``, and what it
gets is its OWN authorization, never a host-wide ``xhost`` grant. These
drive the real argument builder and the real Linux configuration (only
the host's display and the X11 helper are faked), so a default project
is proven to carry no DISPLAY, no socket mount and no authorization.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.cli.configFieldPreflight import (
    flistDescribeUnusableConfiguration,
)
from vaibify.config import projectConfig
from vaibify.docker import containerManager, x11Authorizations, x11Forwarding
from vaibify.host import x11Authority


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
def fixtureLinuxHost(monkeypatch, tmp_path):
    """A Linux host with a display; records every call to the X11 helper."""
    listHelperCalls = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(x11Forwarding.platform, "system", lambda: "Linux")
    monkeypatch.setenv("DISPLAY", ":7")
    monkeypatch.setattr(
        x11Forwarding, "fdictAssessLinuxX11",
        lambda: x11Forwarding._fdictAssessment(x11Forwarding.S_STATE_READY, "", ""))

    def fdictAnswerGenerated(listArguments, fTimeoutSeconds=15.0):
        listHelperCalls.append(list(listArguments))
        return {"sOutcome": x11Authority.S_OUTCOME_GENERATED, "iAuthorizationId": 7}

    monkeypatch.setattr(x11Authorizations, "fdictRunHelper", fdictAnswerGenerated)
    return listHelperCalls


@pytest.mark.falsification
def testAProjectThatDidNotOptInGetsNoDisplayNoSocketAndNoAuthorization(
    fixtureLinuxHost,
):
    """Kills: forwarding X11 into every container regardless of the opt-in."""
    saRunArgs = containerManager.flistBuildRunArgs(_fconfigForLaunch())
    assert not any("DISPLAY" in sArg for sArg in saRunArgs)
    assert not any(".X11-unix" in sArg for sArg in saRunArgs)
    assert not any("XAUTHORITY" in sArg for sArg in saRunArgs)
    assert fixtureLinuxHost == []
    assert x11Authorizations.fdictReadAuthorizations() == {}


def testAnOptedInLinuxProjectGetsTheDisplayTheSocketAndItsOwnAuthorization(
    fixtureLinuxHost,
):
    saRunArgs = containerManager.flistBuildRunArgs(
        _fconfigForLaunch(bX11Forwarding=True))
    assert "DISPLAY=:7" in saRunArgs
    assert (
        "type=bind,source=/tmp/.X11-unix,target=/tmp/.X11-unix,readonly"
        in saRunArgs
    )
    assert f"XAUTHORITY={x11Authorizations.S_CONTAINER_AUTHORITY_PATH}" in saRunArgs
    sDirectory = x11Authorizations.fdictReadAuthorizations()["optInProject"]["sDirectory"]
    assert (
        f"type=bind,source={sDirectory},"
        f"target={x11Authorizations.S_CONTAINER_AUTHORITY_DIRECTORY},readonly"
        in saRunArgs
    )
    assert [listCall[0] for listCall in fixtureLinuxHost] == ["generate"]
    assert "xhost" not in " ".join(saRunArgs)


def testAnOptedInProjectWhoseAuthorizationFailedStartsWithoutADisplay(
    fixtureLinuxHost, monkeypatch, capsys,
):
    monkeypatch.setattr(
        x11Authorizations, "fdictRunHelper",
        lambda listArguments, fTimeoutSeconds=15.0: {
            "sOutcome": x11Authority.S_OUTCOME_SERVER_UNREACHABLE,
            "sReason": "the trusted connection was refused",
        })
    x11Forwarding._setNoticesShownThisInvocation.clear()
    saRunArgs = containerManager.flistBuildRunArgs(
        _fconfigForLaunch(bX11Forwarding=True))
    assert not any("DISPLAY" in sArg for sArg in saRunArgs)
    assert not any(".X11-unix" in sArg for sArg in saRunArgs)
    assert "no authorization could be made" in capsys.readouterr().err
    listLines = x11Authorizations.flistDescribeAuthorization("optInProject")
    assert len(listLines) == 1 and "serverUnreachable" in listLines[0]


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
