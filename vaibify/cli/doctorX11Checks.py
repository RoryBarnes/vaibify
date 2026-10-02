"""Doctor checks for X11 display forwarding.

X11 forwarding is opt-in per project, and two facts decide whether a
graphical program in the container can open a window: whether this
host can serve a display, and whether the running container was
created with one. DISPLAY is fixed when a container is created, so a
container made before the setting was turned on keeps running without
a display and every program in it fails with "Cannot open display".
These checks name that cause and the one action that fixes it.
"""

from vaibify.docker.x11Forwarding import (
    S_STATE_NOT_CHECKED, S_STATE_UNSUPPORTED,
    fdictAssessContainerX11, fdictAssessHostX11,
)

from .preflightResult import (
    S_LEVEL_INFO, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
    S_SCOPE_CONTAINER, S_SCOPE_HOST, PreflightResult,
)


__all__ = ["flistCheckX11Container", "flistCheckX11Host"]

S_X11_HOST_CHECK_NAME = "x11-host-display"
S_X11_CONTAINER_CHECK_NAME = "x11-container-display"


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
        )]
    return [_fpreflightFromAssessment(
        fdictAssessHostX11(), S_X11_HOST_CHECK_NAME, S_SCOPE_HOST,
        "this host has an X display a container can reach",
    )]


def flistCheckX11Container(config, jsonInspect):
    """Say whether the running container matches the project's X11 setting."""
    return [_fpreflightFromAssessment(
        fdictAssessContainerX11(
            getattr(config, "bX11Forwarding", False), jsonInspect),
        S_X11_CONTAINER_CHECK_NAME, S_SCOPE_CONTAINER,
        "the container was created consistently with the x11Forwarding "
        "setting",
    )]
