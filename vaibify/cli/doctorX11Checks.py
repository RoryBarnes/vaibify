"""Doctor checks for X11 display forwarding.

X11 forwarding is opt-in per project, and two facts decide whether a
graphical program in the container can open a window: whether this
host can serve a display, and whether the running container was
created with one. DISPLAY is fixed when a container is created, so a
container made before the setting was turned on keeps running without
a display and every program in it fails with "Cannot open display".
These checks name that cause and the one action that fixes it, say
what the container's own authorization is (a copied server cookie is
full display access), and notice a Mac X server left accepting network
clients that no project needs.
"""

import platform

from vaibify.docker.x11Authorizations import (
    S_SERVER_COOKIE_NOTICE, flistDescribeAuthorization,
)
from vaibify.docker.x11Forwarding import (
    S_STATE_NOT_CHECKED, S_STATE_UNSUPPORTED, X11_TCP_BASE_PORT,
    fbMacXServerAcceptingNetworkConnections, fdictAssessContainerX11,
    fdictAssessHostX11, fdictFindMacXServer, fiResolveHostDisplayNumber,
)

from .preflightResult import (
    S_LEVEL_INFO, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
    S_SCOPE_CONTAINER, S_SCOPE_HOST, PreflightResult,
)


__all__ = ["flistCheckX11Container", "flistCheckX11Host"]

S_X11_HOST_CHECK_NAME = "x11-host-display"
S_X11_CONTAINER_CHECK_NAME = "x11-container-display"
S_X11_AUTHORIZATION_CHECK_NAME = "x11-container-authorization"
S_X11_NETWORK_CLIENTS_CHECK_NAME = "x11-network-clients"


def _fpreflightFromAssessment(
    dictAssessment, sName, sScope, sReadyMessage,
):
    """Translate one assessment into a doctor result."""
    sState = dictAssessment["sState"]
    if dictAssessment["bReady"]:
        return PreflightResult(
            sName=sName, sLevel=S_LEVEL_OK, sScope=sScope,
            sMessage=sReadyMessage,
        )
    if sState in (S_STATE_NOT_CHECKED, S_STATE_UNSUPPORTED):
        return PreflightResult(
            sName=sName, sLevel=S_LEVEL_NOT_CHECKED, sScope=sScope,
            sMessage=dictAssessment["sMessage"],
        )
    return PreflightResult(
        sName=sName, sLevel=S_LEVEL_WARN, sScope=sScope,
        sMessage=dictAssessment["sMessage"],
        sRemediation=dictAssessment["sFix"],
        sCommand=dictAssessment["sCommand"],
    )


def flistCheckX11Host(config):
    """Say whether this host can serve the display the project asked for."""
    if not getattr(config, "bX11Forwarding", False):
        return [PreflightResult(
            sName=S_X11_HOST_CHECK_NAME, sLevel=S_LEVEL_INFO,
            sScope=S_SCOPE_HOST,
            sMessage=(
                "X11 forwarding is off for this project, so graphical "
                "programs in its container cannot open a window. Set "
                "`x11Forwarding: true` in vaibify.yml to enable it."
            ),
        )] + flistCheckUnneededNetworkClients()
    return [_fpreflightFromAssessment(
        fdictAssessHostX11(), S_X11_HOST_CHECK_NAME, S_SCOPE_HOST,
        "this host has an X display a container can reach",
    )]


def _fbAnyRegisteredProjectForwards():
    """True when some registered project's vaibify.yml opts in to X11."""
    from vaibify.cli.configLoader import fconfigLoadFromPath
    from vaibify.config.registryManager import flistGetAllProjects
    for dictProject in flistGetAllProjects():
        try:
            if fconfigLoadFromPath(dictProject["sConfigPath"]).bX11Forwarding:
                return True
        except Exception:  # noqa: BLE001 -- an unreadable project opts in to nothing
            continue
    return False


def flistCheckUnneededNetworkClients():
    """Warn when a Mac X server accepts network clients and no project needs it.

    vaibify turns the setting on at the first opt-in and never off,
    because it cannot know what else the researcher uses it for; it
    says so here, with the command that turns it back.
    """
    if platform.system() != "Darwin":
        return []
    dictServer = fdictFindMacXServer()
    if dictServer is None or not fbMacXServerAcceptingNetworkConnections(
        X11_TCP_BASE_PORT + fiResolveHostDisplayNumber(),
    ) or _fbAnyRegisteredProjectForwards():
        return []
    sDomain = dictServer["sPreferenceDomain"]
    return [PreflightResult(
        sName=S_X11_NETWORK_CLIENTS_CHECK_NAME, sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_HOST,
        sMessage=(
            f"{dictServer['sProduct']} accepts network clients, and no "
            "registered project uses X11 forwarding."
        ),
        sRemediation=(
            "Turn the setting back off, then quit the X server completely "
            "and start it again."
        ),
        sCommand=f"defaults write {sDomain} nolisten_tcp -bool true",
    )]


def _flistCheckAuthorization(sContainerName):
    """Report the container's own authorization: a copied cookie, or a failed setup."""
    listResults = []
    for sLine in flistDescribeAuthorization(sContainerName):
        bFullAccess = sLine == S_SERVER_COOKIE_NOTICE
        listResults.append(PreflightResult(
            sName=S_X11_AUTHORIZATION_CHECK_NAME,
            sLevel=S_LEVEL_INFO if bFullAccess else S_LEVEL_WARN,
            sScope=S_SCOPE_CONTAINER, sMessage=sLine,
        ))
    return listResults


def flistCheckX11Container(config, jsonInspect):
    """Say whether the running container matches the project's X11 setting."""
    return [_fpreflightFromAssessment(
        fdictAssessContainerX11(
            getattr(config, "bX11Forwarding", False), jsonInspect),
        S_X11_CONTAINER_CHECK_NAME, S_SCOPE_CONTAINER,
        "the container was created consistently with the x11Forwarding "
        "setting",
    )] + _flistCheckAuthorization(getattr(config, "sProjectName", ""))
