"""Allowing a network host is the researcher's; the setting changes the mode (rulings D4, D8).

The hub's registry routes over a real vaibify.yml: the allow-host route
appends one validated entry to ``allowedHosts`` and says a restart is
needed, refuses the agent lane by name, and refuses a malformed entry;
the settings route changes the mode, writing both spellings in
agreement, and refuses a sealing mode beside X11 forwarding.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes

S_PROJECT_NAME = "sealedSettings"


def _fclientForConfig(tmp_path, sExtraYaml=""):
    pathProject = tmp_path / S_PROJECT_NAME
    os.makedirs(str(pathProject), exist_ok=True)
    sConfigPath = str(pathProject / "vaibify.yml")
    with open(sConfigPath, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(f"projectName: {S_PROJECT_NAME}\n{sExtraYaml}")
    app = FastAPI()
    fnRegisterRegistryRoutes(
        app, {"require": lambda *aArgs: None, "docker": None})
    patchProject = patch(
        "vaibify.config.registryManager.fdictGetProject",
        return_value={
            "sName": S_PROJECT_NAME, "sDirectory": str(pathProject),
            "sConfigPath": sConfigPath, "sContainerName": S_PROJECT_NAME,
        },
    )
    return TestClient(app), sConfigPath, patchProject


def _fsAllowPath():
    return f"/api/containers/{S_PROJECT_NAME}/network/allow-host"


def _fsSettingsPath():
    return f"/api/containers/{S_PROJECT_NAME}/settings"


@pytest.mark.falsification
def testAllowingAHostAppendsItAndAsksForARestart(tmp_path):
    """Kills: a save that never reaches allowedHosts."""
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "networkMode: allowlist\nallowedHosts: []\n")
    with patchProject:
        responseFirst = clientHttp.post(_fsAllowPath(), json={"sHost": "Data.Example.org"})
        responseSecond = clientHttp.post(_fsAllowPath(), json={"sHost": "mirror.example.org:8443"})
        responseAgain = clientHttp.post(_fsAllowPath(), json={"sHost": "data.example.org:443"})
    assert responseFirst.status_code == 200, responseFirst.text
    assert responseFirst.json()["bRestartRequired"] is True
    assert responseSecond.json()["listAllowedHosts"] == [
        "data.example.org", "mirror.example.org:8443"]
    assert responseAgain.json()["listAllowedHosts"] == [
        "data.example.org", "mirror.example.org:8443"]
    assert fconfigLoadFromFile(sConfigPath).listAllowedHosts == [
        "data.example.org", "mirror.example.org:8443"]


def testAllowingAHostInAFileWithoutTheKeyAppendsTheKey(tmp_path):
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "networkMode: allowlist\n")
    with patchProject:
        assert clientHttp.post(_fsAllowPath(), json={"sHost": "data.example.org"}).status_code == 200
    with open(sConfigPath, encoding="utf-8") as fileHandle:
        sText = fileHandle.read()
    assert "allowedHosts:\n- data.example.org\n" in sText
    assert "networkMode: allowlist" in sText


@pytest.mark.falsification
def testTheAgentLaneCannotAllowAHost(tmp_path):
    """An agent that could extend its own allowlist would have none.

    Kills: dropping the agent-lane refusal from the allow-host handler.
    """
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "networkMode: allowlist\nallowedHosts: []\n")
    with patchProject:
        responseHttp = clientHttp.post(
            _fsAllowPath(), json={"sHost": "evil.example.org"},
            headers={"X-Vaibify-Session": "agent-token"})
    assert responseHttp.status_code == 403
    assert fconfigLoadFromFile(sConfigPath).listAllowedHosts == []


@pytest.mark.parametrize("sHost", ["bad host", "10.0.0.5", "x:70000", ""])
def testAMalformedHostIsA400AndNothingIsWritten(tmp_path, sHost):
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "networkMode: allowlist\nallowedHosts: []\n")
    with patchProject:
        assert clientHttp.post(_fsAllowPath(), json={"sHost": sHost}).status_code == 400
    assert fconfigLoadFromFile(sConfigPath).listAllowedHosts == []


def testTheSettingsReportTheModeAndTheAllowedHosts(tmp_path):
    clientHttp, _, patchProject = _fclientForConfig(
        tmp_path, "networkMode: allowlist\nallowedHosts:\n- data.example.org\n")
    with patchProject:
        dictSettings = clientHttp.get(_fsSettingsPath()).json()
    assert dictSettings["sNetworkMode"] == "allowlist"
    assert dictSettings["listAllowedHosts"] == ["data.example.org"]
    assert dictSettings["bNetworkIsolation"] is False


@pytest.mark.falsification
def testChangingTheModeWritesBothSpellingsInAgreement(tmp_path):
    """Kills: writing networkMode and leaving networkIsolation contradicting it."""
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "networkIsolation: false\n")
    with patchProject:
        responseSave = clientHttp.post(_fsSettingsPath(), json={"sNetworkMode": "isolated"})
    assert responseSave.status_code == 200, responseSave.text
    assert responseSave.json()["bRestartRequired"] is True
    config = fconfigLoadFromFile(sConfigPath)
    assert (config.sNetworkMode, config.bNetworkIsolation) == ("isolated", True)
    with patchProject:
        assert clientHttp.post(_fsSettingsPath(), json={"sNetworkMode": "isolated"}).json()[
            "bRestartRequired"] is False
        assert clientHttp.post(_fsSettingsPath(), json={"sNetworkMode": "allowlist"}).status_code == 200
    config = fconfigLoadFromFile(sConfigPath)
    assert (config.sNetworkMode, config.bNetworkIsolation) == ("allowlist", False)


@pytest.mark.falsification
def testASealingModeIsRefusedBesideX11AndX11BesideASealingMode(tmp_path):
    """Ruling D4, from both sides.

    Kills: saving allowlist mode while X11 forwarding is on.
    """
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "x11Forwarding: true\n")
    with patchProject:
        responseMode = clientHttp.post(_fsSettingsPath(), json={"sNetworkMode": "allowlist"})
    assert responseMode.status_code == 409
    assert fconfigLoadFromFile(sConfigPath).sNetworkMode == "open"
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path / "second", "networkMode: allowlist\n")
    with patchProject:
        responseX11 = clientHttp.post(_fsSettingsPath(), json={"bX11Forwarding": True})
    assert responseX11.status_code == 409
    assert "allowlist" in responseX11.text
    assert fconfigLoadFromFile(sConfigPath).bX11Forwarding is False


def testAnUnknownModeIsA400(tmp_path):
    clientHttp, _, patchProject = _fclientForConfig(tmp_path)
    with patchProject:
        assert clientHttp.post(_fsSettingsPath(), json={"sNetworkMode": "sealed"}).status_code == 400
