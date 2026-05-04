"""X11 display forwarding for Docker containers."""

import os
import platform
import socket
import subprocess
import sys


XQUARTZ_APP_PATH = "/Applications/Utilities/XQuartz.app"
SA_XQUARTZ_APP_PATHS = (
    "/Applications/XQuartz.app",
    "/Applications/Utilities/XQuartz.app",
)
XQUARTZ_BUNDLE_ID = "org.macosforge.xquartz.X11"
SA_XQUARTZ_BUNDLE_IDS = (
    "org.xquartz.X11",
    "org.macosforge.xquartz.X11",
)
XQUARTZ_TCP_PORT = 6000
MAC_CONTAINER_HOST = "host.docker.internal"

_setNoticesShownThisInvocation = set()


def flistConfigureX11Args():
    """Return docker run args for X11 forwarding on the current platform.

    Detects macOS vs Linux and delegates to the appropriate setup.

    Returns
    -------
    list of str
        Docker run arguments enabling X11 display forwarding.
    """
    saRunArgs = []
    sPlatform = platform.system()
    if sPlatform == "Darwin":
        fnConfigureMacX11(saRunArgs)
    elif sPlatform == "Linux":
        fnConfigureLinuxX11(saRunArgs)
    return saRunArgs


def fnConfigureMacX11(saRunArgs):
    """Configure X11 forwarding for macOS via XQuartz.

    Detects XQuartz presence first; if missing, prints a notice and
    leaves DISPLAY unset. Otherwise starts XQuartz, disables auth,
    probes the TCP listener for the network-clients setting, and sets
    DISPLAY based on the host's own DISPLAY value.

    Parameters
    ----------
    saRunArgs : list of str
        Docker run argument list to extend in place.
    """
    if not fbXquartzInstalled():
        _fnPrintXquartzMissingNotice()
        return
    fnStartXquartz()
    fnDisableX11Auth()
    if not fbXquartzAcceptingNetworkConnections():
        _fnPrintXquartzNetworkBlockedNotice()
    saRunArgs.extend(["-e", f"DISPLAY={fsResolveMacContainerDisplay()}"])


def fnConfigureLinuxX11(saRunArgs):
    """Configure X11 forwarding for Linux with safer xhost policy.

    Uses xhost +SI:localuser:$USER instead of the overly permissive
    xhost +local:docker, then mounts the X11 socket and passes DISPLAY.

    Parameters
    ----------
    saRunArgs : list of str
        Docker run argument list to extend in place.
    """
    _fnGrantLocalUserXhostAccess()
    sDisplay = os.environ.get("DISPLAY", ":0")
    saRunArgs.extend(["-e", f"DISPLAY={sDisplay}"])
    saRunArgs.extend(["-v", "/tmp/.X11-unix:/tmp/.X11-unix:ro"])


def fbXquartzInstalled():
    """Return True if XQuartz appears to be installed on this Mac.

    Checks both common install paths first, then falls back to a
    Spotlight bundle-identifier query against the modern and legacy
    bundle IDs. The modern bundle is ``org.xquartz.X11`` (the
    project moved off macosforge years ago); the legacy
    ``org.macosforge.xquartz.X11`` is kept for very old installs
    that still carry the original ID.
    """
    for sAppPath in SA_XQUARTZ_APP_PATHS:
        if os.path.exists(sAppPath):
            return True
    return _fbXquartzFoundViaSpotlight()


def _fbXquartzFoundViaSpotlight():
    """Use mdfind to locate XQuartz by any known CFBundleIdentifier."""
    for sBundleId in SA_XQUARTZ_BUNDLE_IDS:
        if _fbBundleFoundByMdfind(sBundleId):
            return True
    return False


def _fbBundleFoundByMdfind(sBundleId):
    """Return True if mdfind locates a bundle with the given identifier."""
    sQuery = f"kMDItemCFBundleIdentifier == '{sBundleId}'"
    try:
        resultProcess = subprocess.run(
            ["mdfind", sQuery],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return (
        resultProcess.returncode == 0
        and bool(resultProcess.stdout.strip())
    )


def fbXquartzAcceptingNetworkConnections():
    """Return True if XQuartz is listening on localhost:6000.

    XQuartz only opens this TCP port when "Allow connections from
    network clients" is enabled in its security preferences.
    """
    try:
        with socket.create_connection(
            ("localhost", XQUARTZ_TCP_PORT), timeout=1.0
        ):
            return True
    except (ConnectionRefusedError, OSError):
        return False


def fsResolveMacContainerDisplay():
    """Return the DISPLAY value to pass into the macOS container.

    Reads the host's DISPLAY env var, replaces any local-host portion
    with host.docker.internal, and preserves the display/screen suffix.
    Falls back to host.docker.internal:0 when DISPLAY is unset.
    """
    sHostDisplay = os.environ.get("DISPLAY", "")
    if not sHostDisplay:
        return f"{MAC_CONTAINER_HOST}:0"
    sDisplaySuffix = _fsExtractDisplaySuffix(sHostDisplay)
    return f"{MAC_CONTAINER_HOST}{sDisplaySuffix}"


def _fsExtractDisplaySuffix(sDisplay):
    """Return the ':N[.M]' portion of a DISPLAY string."""
    iColonIndex = sDisplay.rfind(":")
    if iColonIndex < 0:
        return ":0"
    return sDisplay[iColonIndex:]


def _fnPrintXquartzMissingNotice():
    """Inform the user that XQuartz is not installed."""
    sKey = "xquartz-missing"
    if sKey in _setNoticesShownThisInvocation:
        return
    _setNoticesShownThisInvocation.add(sKey)
    print(
        "[vaibify] XQuartz not installed; X11 plot forwarding disabled.",
        file=sys.stderr,
    )
    print(
        "[vaibify]   Install from xquartz.org and restart the container "
        "to enable.",
        file=sys.stderr,
    )


def _fnPrintXquartzNetworkBlockedNotice():
    """Inform the user that XQuartz blocks network clients."""
    sKey = "xquartz-network-blocked"
    if sKey in _setNoticesShownThisInvocation:
        return
    _setNoticesShownThisInvocation.add(sKey)
    print(
        "[vaibify] XQuartz security setting blocks network clients.",
        file=sys.stderr,
    )
    print(
        "[vaibify]   Enable: XQuartz Preferences -> Security -> 'Allow "
        "connections",
        file=sys.stderr,
    )
    print(
        "[vaibify]   from network clients', then restart XQuartz.",
        file=sys.stderr,
    )


def _fnRunBestEffort(saArgs):
    """Run a subprocess, ignoring missing binaries and non-zero exits.

    X11 setup is optional: a host without xhost, XQuartz, or pgrep
    should not crash container start.
    """
    try:
        subprocess.run(saArgs, capture_output=True)
    except FileNotFoundError:
        return


def _fnGrantLocalUserXhostAccess():
    """Grant the current local user X11 access via xhost."""
    sUser = os.environ.get("USER", "")
    if not sUser:
        return
    _fnRunBestEffort(["xhost", "+SI:localuser:" + sUser])


def fnStartXquartz():
    """Launch XQuartz on macOS if it is not already running.

    Silently does nothing if XQuartz cannot be started.
    """
    if _fbProcessIsRunning("Xquartz"):
        return
    _fnRunBestEffort(["open", "-a", "XQuartz"])


def _fbProcessIsRunning(sProcessName):
    """Check whether a process with the given name is running."""
    try:
        resultProcess = subprocess.run(
            ["pgrep", "-x", sProcessName],
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return resultProcess.returncode == 0


def fnDisableX11Auth():
    """Allow Docker X11 connections on macOS by running xhost +localhost."""
    _fnRunBestEffort(["xhost", "+localhost"])
