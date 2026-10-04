"""The Open in VS Code link names the container and the daemon it lives on.

The oracle is the Dev Containers extension's own authority parser,
re-stated here from the shipped extension source (functions that split
``<name>+<hex>[@<parent>]``, hex-decode the payload, and read a JSON
object whose ``containerName`` or ``containerId`` selects the container
and whose ``settings.host`` or ``settings.context`` selects the daemon).
The same parser, extracted from the extension and run under node, was
fed the link built here and resolved it to the container id and the
daemon endpoint. The old link, ``vscode://ms-vscode-remote.remote-
containers/attach?containerId=...``, reaches a handler that acts only on
``/cloneInVolume``.
"""

import json
import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from vaibify.docker import dockerContext
from vaibify.gui.routes import systemRoutes
from vaibify.gui.vscodeAttachLink import fsBuildAttachUri

S_CONTAINER_ID = "0123456789abcdef" * 4
S_CONTAINER_NAME = "namedProject"
S_COLIMA_SOCKET = "unix:///home/someone/.colima/default/docker.sock"


def _fdictParseLikeTheExtension(sUri):
    """Return (authority name, decoded config, path) the extension would see."""
    tParts = urlsplit(sUri)
    assert tParts.scheme == "vscode" and tParts.netloc == "vscode-remote"
    sAuthority, _, sPath = tParts.path[1:].partition("/")
    matchAuthority = re.match(
        r"^(?P<name>[^@+]+)\+(?P<config>[^@]+)(?:@(?P<parent>.*))?$",
        sAuthority)
    assert matchAuthority, sAuthority
    sConfig = bytes.fromhex(matchAuthority.group("config")).decode("utf-8")
    assert sConfig.startswith("{")
    return matchAuthority.group("name"), json.loads(sConfig), "/" + sPath


@pytest.mark.falsification
def testTheLinkIsTheAttachedContainerFormTheExtensionParses():
    """Kills: reverting to the remote-containers /attach path nothing handles."""
    sUri = fsBuildAttachUri(S_CONTAINER_ID, "/workspace", S_COLIMA_SOCKET)
    sName, dictConfig, sPath = _fdictParseLikeTheExtension(sUri)
    assert sName == "attached-container"
    assert dictConfig["containerId"] == S_CONTAINER_ID
    assert dictConfig["settings"] == {"host": S_COLIMA_SOCKET}
    assert sPath == "/workspace"
    assert "remote-containers" not in sUri and "attach?" not in sUri


@pytest.mark.falsification
def testTheLinkAsksForANewWindowSoNoOpenFileIsDisturbed():
    """Kills: dropping ``windowId=_blank``, so VS Code opens the container
    in the window the researcher already has and asks whether to save
    that window's unsaved files before replacing it (reported 2026-10-04).

    VS Code's own URL handler reads the parameter; it was confirmed in
    the shipped 1.138.0 source, which passes ``forceNewWindow`` when it
    finds ``windowId=_blank`` on the link. That a window really opens
    needs a real VS Code and was not run here.
    """
    sUri = fsBuildAttachUri(S_CONTAINER_ID, "/workspace", S_COLIMA_SOCKET)

    assert urlsplit(sUri).query == "windowId=_blank"
    _, dictConfig, sPath = _fdictParseLikeTheExtension(sUri)
    assert dictConfig["containerId"] == S_CONTAINER_ID
    assert sPath == "/workspace"


def testTheDefaultSocketOrNoEndpointAddsNoSetting():
    for sHost in ("", "unix:///var/run/docker.sock"):
        _, dictConfig, _ = _fdictParseLikeTheExtension(
            fsBuildAttachUri(S_CONTAINER_ID, "/workspace", sHost))
        assert "settings" not in dictConfig


def testAWorkspaceRootWithASpaceIsQuoted():
    sUri = fsBuildAttachUri(S_CONTAINER_ID, "/work space")
    assert urlsplit(sUri).path.endswith("/work%20space")


def test_the_endpoint_is_DOCKER_HOST_first_then_the_context(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", S_COLIMA_SOCKET)
    assert dockerContext.fsReadEffectiveDockerHost() == S_COLIMA_SOCKET
    monkeypatch.delenv("DOCKER_HOST")
    with patch.object(
        dockerContext, "fsReadActiveContextEndpoint",
        return_value="unix:///from/context.sock",
    ):
        assert (
            dockerContext.fsReadEffectiveDockerHost()
            == "unix:///from/context.sock")


@pytest.mark.falsification
def testTheRouteLinksTheIdNotTheNameAndUsesTheProjectsWorkspace():
    """Kills: linking the registry NAME where the daemon needs the id."""
    with patch.object(
        systemRoutes, "_fconfigForContainerOrNone",
        return_value=SimpleNamespace(sWorkspaceRoot="/work"),
    ), patch(
        "vaibify.docker.dockerContext.fsReadEffectiveDockerHost",
        return_value=S_COLIMA_SOCKET,
    ):
        dictLink = systemRoutes._fdictBuildVsCodeLink(None, S_CONTAINER_ID)
    _, dictConfig, sPath = _fdictParseLikeTheExtension(dictLink["sUri"])
    assert dictConfig["containerId"] == S_CONTAINER_ID
    assert S_CONTAINER_NAME not in json.dumps(dictConfig)
    assert sPath == "/work"
    assert dictConfig["settings"]["host"] == S_COLIMA_SOCKET


def testAnUnregisteredContainerFallsBackToTheDefaultWorkspace():
    with patch.object(
        systemRoutes, "_fconfigForContainerOrNone", return_value=None,
    ), patch(
        "vaibify.docker.dockerContext.fsReadEffectiveDockerHost",
        return_value="",
    ):
        dictLink = systemRoutes._fdictBuildVsCodeLink(None, S_CONTAINER_ID)
    assert _fdictParseLikeTheExtension(dictLink["sUri"])[2] == "/workspace"


def test_the_config_lookup_resolves_the_id_to_a_name_first():
    dictSeen = {}

    def fdictLookUp(sKey):
        dictSeen["sRegistryKey"] = sKey
        return {"sConfigPath": "/unused/vaibify.yml"}

    with patch(
        "vaibify.gui.pipelineServer.fsContainerNameForId",
        return_value=S_CONTAINER_NAME,
    ), patch(
        "vaibify.config.registryManager.fdictGetProject",
        side_effect=fdictLookUp,
    ), patch(
        "vaibify.cli.configLoader.fconfigLoadFromPath",
        return_value=SimpleNamespace(sWorkspaceRoot="/w"),
    ):
        configFound = systemRoutes._fconfigForContainerOrNone(
            None, S_CONTAINER_ID)
    assert dictSeen["sRegistryKey"] == S_CONTAINER_NAME
    assert configFound.sWorkspaceRoot == "/w"


def test_the_lookup_gives_none_rather_than_raising():
    with patch(
        "vaibify.gui.pipelineServer.fsContainerNameForId",
        side_effect=RuntimeError("daemon went away"),
    ):
        assert systemRoutes._fconfigForContainerOrNone(
            None, S_CONTAINER_ID) is None


def _fclientForTheLinkRoute(listRequired):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    app = FastAPI()
    systemRoutes._fnRegisterVsCodeLink(
        app, {"require": listRequired.append, "docker": None})
    return TestClient(app)


def test_the_route_authorizes_the_id_and_answers_the_built_link():
    listRequired = []
    with patch.object(
        systemRoutes, "_fdictBuildVsCodeLink",
        return_value={"sUri": "vscode://vscode-remote/x"},
    ):
        responseHttp = _fclientForTheLinkRoute(listRequired).get(
            f"/api/containers/{S_CONTAINER_ID}/vscode-link")
    assert responseHttp.json() == {"sUri": "vscode://vscode-remote/x"}
    assert listRequired == [S_CONTAINER_ID]


@pytest.mark.falsification
def test_the_in_container_agent_lane_is_refused_the_hosts_docker_endpoint():
    """Kills: telling the in-container agent where the host's socket is."""
    listRequired = []
    with patch.object(
        systemRoutes, "_fdictBuildVsCodeLink",
        return_value={"sUri": "vscode://vscode-remote/x"},
    ):
        responseHttp = _fclientForTheLinkRoute(listRequired).get(
            f"/api/containers/{S_CONTAINER_ID}/vscode-link",
            headers={"X-Vaibify-Session": "agent-token"})
    assert responseHttp.status_code == 403
    assert listRequired == []
