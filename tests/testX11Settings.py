"""The project settings route reads and writes the X11 forwarding opt-in.

Existing projects have no wizard to pass through, so the settings dialog
is where the opt-in is turned on. Every test drives request -> guard ->
file on disk -> reload, because the failure that matters is a value the
next load cannot read or a launch that then refuses.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config.projectConfig import fconfigLoadFromFile
from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes

S_PROJECT_NAME = "x11-settings"


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
            "sConfigPath": sConfigPath,
        },
    )
    return TestClient(app), sConfigPath, patchProject


def test_the_settings_payload_reports_the_opt_in_and_the_isolation(tmp_path):
    clientHttp, _, patchProject = _fclientForConfig(
        tmp_path, "networkIsolation: true\n")
    with patchProject:
        dictSettings = clientHttp.get(
            f"/api/containers/{S_PROJECT_NAME}/settings").json()
    assert dictSettings["bX11Forwarding"] is False
    assert dictSettings["bNetworkIsolation"] is True


@pytest.mark.falsification
def test_turning_the_opt_in_on_persists_and_asks_for_a_recreate(tmp_path):
    """Kills: a settings save that drops the X11 forwarding choice."""
    clientHttp, sConfigPath, patchProject = _fclientForConfig(tmp_path)
    with patchProject:
        responseSave = clientHttp.post(
            f"/api/containers/{S_PROJECT_NAME}/settings",
            json={"bX11Forwarding": True})
    assert responseSave.status_code == 200
    assert responseSave.json()["bRestartRequired"] is True
    assert fconfigLoadFromFile(sConfigPath).bX11Forwarding is True


def test_saving_the_unchanged_choice_asks_for_no_recreate(tmp_path):
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "x11Forwarding: true\n")
    with patchProject:
        responseSave = clientHttp.post(
            f"/api/containers/{S_PROJECT_NAME}/settings",
            json={"bX11Forwarding": True})
    assert responseSave.json()["bRestartRequired"] is False
    assert fconfigLoadFromFile(sConfigPath).bX11Forwarding is True


def test_turning_the_opt_in_off_persists_and_asks_for_a_recreate(tmp_path):
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "x11Forwarding: true\n")
    with patchProject:
        responseSave = clientHttp.post(
            f"/api/containers/{S_PROJECT_NAME}/settings",
            json={"bX11Forwarding": False})
    assert responseSave.json()["bRestartRequired"] is True
    assert fconfigLoadFromFile(sConfigPath).bX11Forwarding is False


@pytest.mark.falsification
def test_the_opt_in_is_refused_beside_network_isolation(tmp_path):
    """Kills: saving a configuration the launch will then refuse."""
    clientHttp, sConfigPath, patchProject = _fclientForConfig(
        tmp_path, "networkIsolation: true\n")
    with open(sConfigPath, encoding="utf-8") as fileHandle:
        sBefore = fileHandle.read()
    with patchProject:
        responseSave = clientHttp.post(
            f"/api/containers/{S_PROJECT_NAME}/settings",
            json={"bX11Forwarding": True})
    assert responseSave.status_code == 409
    assert "network isolation" in responseSave.text
    with open(sConfigPath, encoding="utf-8") as fileHandle:
        assert fileHandle.read() == sBefore
