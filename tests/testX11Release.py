"""Every way a container stops drawing revokes its X authorization.

A cookie that outlives its container admits whoever holds a copy, so
the release is tied to the three places a container's display ends: the
removal every stop runs, the setting being turned off, and the
environment's deletion. The authorization manager is replaced by a
recorder; what is proven is that each path calls it, by the container's
name.
"""

from types import SimpleNamespace

import pytest

from tests.testCoverageCoreEnvironmentDeletion import (  # noqa: F401 -- fixtures
    S_CONTAINER_NAME, dictDoubles, fdictProject,
)
from vaibify.config import projectConfig
from vaibify.docker import containerManager, x11Authorizations
from vaibify.gui import environmentDeletion, registryRoutes


@pytest.fixture
def listReleased(monkeypatch):
    """Record every container whose authorization is released, wherever from."""
    listNames = []
    monkeypatch.setattr(
        x11Authorizations, "fnReleaseAuthorizationQuietly", listNames.append,
    )
    monkeypatch.setattr(
        containerManager, "fnReleaseAuthorizationQuietly", listNames.append,
    )
    return listNames


@pytest.mark.falsification
def testStoppingAContainerRevokesItsAuthorization(monkeypatch, listReleased):
    """Kills: forgetting the X authorization when the stopped container is removed."""
    monkeypatch.setattr(
        containerManager, "_flistMountSourcesOfContainer", lambda sName: [])
    monkeypatch.setattr(
        containerManager.subprocess, "run",
        lambda *listArguments, **dictKeywords: SimpleNamespace(
            returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(
        containerManager, "fdictProbeContainerPresence",
        lambda sName: {"bAnswered": True, "bPresent": False})
    monkeypatch.setattr(
        containerManager, "_fnReleaseSecretSourcesIfRemoved",
        lambda dictPresence, listSources: None)
    containerManager.fnRemoveStopped("demoContainer")
    assert listReleased == ["demoContainer"]


def _fsWriteConfig(tmp_path, bX11Forwarding):
    sPath = str(tmp_path / "vaibify.yml")
    projectConfig.fnSaveToFile(projectConfig.fconfigFromYamlDict(
        {"projectName": "demo", "x11Forwarding": bX11Forwarding}), sPath)
    return sPath


@pytest.mark.falsification
def testTurningForwardingOffRevokesTheRunningContainersAuthorization(
    tmp_path, listReleased,
):
    """The setting says the container may not draw; the cookie must agree at once.

    Kills: leaving the authorization in place when x11Forwarding is turned off.
    """
    sPath = _fsWriteConfig(tmp_path, True)
    assert registryRoutes._fbApplyX11Forwarding(sPath, False, "demoContainer") is True
    assert projectConfig.fconfigLoadFromFile(sPath).bX11Forwarding is False
    assert listReleased == ["demoContainer"]


def testTurningForwardingOnOrLeavingItReleasesNothing(tmp_path, listReleased):
    sPath = _fsWriteConfig(tmp_path, False)
    assert registryRoutes._fbApplyX11Forwarding(sPath, False, "demoContainer") is False
    assert registryRoutes._fbApplyX11Forwarding(sPath, True, "demoContainer") is True
    assert listReleased == []


@pytest.mark.falsification
def testDeletingAnEnvironmentRevokesItsAuthorization(dictDoubles, listReleased):  # noqa: F811
    """Kills: deleting the container and leaving its cookie admitted."""
    environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert listReleased == [S_CONTAINER_NAME]


def testAFailedContainerRemovalReleasesNothing(dictDoubles, listReleased):  # noqa: F811
    dictDoubles["dictState"]["exceptionStatus"] = RuntimeError("daemon away")
    dictReport = environmentDeletion.fdictDeleteEnvironment(fdictProject())
    assert dictReport["bContainerRemoved"] is False
    assert listReleased == []
