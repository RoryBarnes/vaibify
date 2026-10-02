"""X11 display forwarding for Docker containers.

Forwarding is opt-in per project (``x11Forwarding: true`` in
``vaibify.yml``); nothing here runs for a project that did not ask for
it. On macOS the container reaches the host's X server over TCP at
``host.docker.internal``, so the server must be installed, running, and
accepting network clients. XQuartz and the MacPorts X11 application are
both recognized; they differ in application path, bundle identifier,
and preference domain.
"""

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time


MAC_X_SERVER_PRODUCTS = (
    {
        "sProduct": "XQuartz",
        "saAppPaths": (
            "/Applications/XQuartz.app",
            "/Applications/Utilities/XQuartz.app",
        ),
        "saBundleIds": ("org.xquartz.X11", "org.macosforge.xquartz.X11"),
        "sPreferenceDomain": "org.xquartz.X11",
    },
    {
        "sProduct": "MacPorts X11",
        "saAppPaths": ("/Applications/MacPorts/X11.app",),
        "saBundleIds": ("org.macports.X11",),
        "sPreferenceDomain": "org.macports.X11",
    },
)
MAC_BINARY_PREFERENCE_DOMAINS = (
    ("/opt/X11/bin", "org.xquartz.X11"),
    ("/opt/local/bin", "org.macports.X11"),
)
SA_X_SERVER_PROCESS_NAMES = ("Xquartz",)
X11_TCP_BASE_PORT = 6000
MAC_CONTAINER_HOST = "host.docker.internal"
F_SERVER_START_WAIT_SECONDS = 10.0
F_POLL_INTERVAL_SECONDS = 0.25

S_STATE_READY = "ready"
S_STATE_NO_SERVER = "no-server"
S_STATE_NETWORK_BLOCKED = "network-blocked"
S_STATE_NOT_LISTENING = "not-listening"
S_STATE_NOT_RUNNING = "not-running"
S_STATE_NO_DISPLAY = "no-display"
S_STATE_UNSUPPORTED = "unsupported"

_setNoticesShownThisInvocation = set()


def flistConfigureX11Args():
    """Return docker run args for X11 forwarding on the current platform.

    Detects macOS vs Linux and delegates to the appropriate setup. The
    caller decides whether the project opted in; this function always
    configures.

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
    """Configure X11 forwarding for macOS with whichever X server exists.

    Starts the server if it is not running, grants the container access,
    and sets DISPLAY from the host's own display number. When the server
    is missing or refuses network clients, the reason and the one fix
    are printed; DISPLAY is left unset only when there is no server.

    Parameters
    ----------
    saRunArgs : list of str
        Docker run argument list to extend in place.
    """
    dictServer = fdictFindMacXServer()
    if dictServer is not None:
        fnStartMacXServer(dictServer)
        fnGrantMacXhostAccess()
    iPort = X11_TCP_BASE_PORT + fiResolveHostDisplayNumber()
    dictAssessment = fdictAssessMacX11(dictServer, iPort, bWaitForPort=True)
    if not dictAssessment["bReady"]:
        _fnPrintAssessmentNotice(dictAssessment)
    if dictServer is not None:
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


def fdictFindMacXServer():
    """Return the X server installed on this Mac, or None.

    The result names the product, the application bundle to launch
    (empty when only the server binary exists), and the preference
    domain that holds its ``nolisten_tcp`` setting.
    """
    for dictProduct in MAC_X_SERVER_PRODUCTS:
        sAppPath = _fsLocateProductApp(dictProduct)
        if sAppPath:
            return {
                "sProduct": dictProduct["sProduct"],
                "sAppPath": sAppPath,
                "sPreferenceDomain": dictProduct["sPreferenceDomain"],
            }
    return _fdictFindBinaryOnlyServer()


def _fsLocateProductApp(dictProduct):
    """Return the installed application path for a product, or ''."""
    for sAppPath in dictProduct["saAppPaths"]:
        if os.path.isdir(sAppPath):
            return sAppPath
    for sBundleId in dictProduct["saBundleIds"]:
        sFound = _fsFindBundleByMdfind(sBundleId)
        if sFound:
            return sFound
    return ""


def _fsFindBundleByMdfind(sBundleId):
    """Return the first path mdfind reports for a bundle identifier."""
    sQuery = f"kMDItemCFBundleIdentifier == '{sBundleId}'"
    try:
        processResult = subprocess.run(
            ["mdfind", sQuery],
            capture_output=True,
            text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return ""
    if processResult.returncode != 0:
        return ""
    listLines = processResult.stdout.strip().splitlines()
    return listLines[0] if listLines else ""


def _fdictFindBinaryOnlyServer():
    """Return a server record for a bare Xquartz binary, or None."""
    sOnPath = shutil.which("Xquartz")
    if sOnPath:
        return _fdictBinaryServer(sOnPath)
    for sDirectory, _sDomain in MAC_BINARY_PREFERENCE_DOMAINS:
        sCandidate = os.path.join(sDirectory, "Xquartz")
        if os.path.isfile(sCandidate):
            return _fdictBinaryServer(sCandidate)
    return None


def _fdictBinaryServer(sBinaryPath):
    """Describe an Xquartz binary that has no application bundle."""
    sDomain = "org.xquartz.X11"
    for sDirectory, sCandidateDomain in MAC_BINARY_PREFERENCE_DOMAINS:
        if sBinaryPath.startswith(sDirectory + os.sep):
            sDomain = sCandidateDomain
    return {
        "sProduct": "Xquartz binary", "sAppPath": "",
        "sPreferenceDomain": sDomain,
    }


def fbMacXServerIsRunning():
    """Return True when an X server process is running on this Mac."""
    return any(
        _fbProcessIsRunning(sName) for sName in SA_X_SERVER_PROCESS_NAMES
    )


def fnStartMacXServer(dictServer):
    """Launch the found X server's own application if it is not running.

    ``open -a`` is given the application path that was found, never the
    product name, because MacPorts' application is ``X11.app`` and a
    name lookup for ``XQuartz`` cannot find it. A bare server binary
    has no application to open and is left for the user to start.
    """
    if fbMacXServerIsRunning() or not dictServer["sAppPath"]:
        return
    _fnRunBestEffort(["open", "-a", dictServer["sAppPath"]])
    _fbWaitUntil(fbMacXServerIsRunning, F_SERVER_START_WAIT_SECONDS)


def _fbWaitUntil(fbCondition, fSeconds):
    """Poll a condition until it holds or the time runs out."""
    fDeadline = time.monotonic() + fSeconds
    while True:
        if fbCondition():
            return True
        if time.monotonic() >= fDeadline:
            return False
        time.sleep(F_POLL_INTERVAL_SECONDS)


def fbMacXServerAcceptingNetworkConnections(iPort=X11_TCP_BASE_PORT):
    """Return True if the X server is listening on localhost:<port>.

    The server only opens this TCP port when network clients are
    allowed in its security preferences (``nolisten_tcp`` is false).
    """
    try:
        with socket.create_connection(("localhost", iPort), timeout=1.0):
            return True
    except (ConnectionRefusedError, OSError):
        return False


def fsReadNetworkClientPreference(sPreferenceDomain):
    """Return 'allowed', 'blocked', or 'unknown' for ``nolisten_tcp``."""
    try:
        processResult = subprocess.run(
            ["defaults", "read", sPreferenceDomain, "nolisten_tcp"],
            capture_output=True,
            text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return "unknown"
    sValue = processResult.stdout.strip()
    if processResult.returncode != 0 or sValue not in ("0", "1"):
        return "unknown"
    return "allowed" if sValue == "0" else "blocked"


def fdictAssessMacX11(dictServer, iPort, bWaitForPort=False):
    """Say whether a container could reach this Mac's X server, and why not.

    Returns ``{"sState", "bReady", "sMessage", "sFix"}``. ``bReady`` is
    True only when something is actually listening on the TCP port the
    container will dial; every other state names its cause and the one
    action that fixes it. ``bWaitForPort`` gives a server that was just
    started time to open its socket.
    """
    if dictServer is None:
        return _fdictAssessment(
            S_STATE_NO_SERVER,
            "No X server is installed on this Mac.",
            "Install XQuartz (xquartz.org) or the MacPorts xorg-server "
            "port, then restart the container.",
        )
    sDomain = dictServer["sPreferenceDomain"]
    if bWaitForPort and fsReadNetworkClientPreference(sDomain) != "blocked":
        _fbWaitUntil(
            lambda: fbMacXServerAcceptingNetworkConnections(iPort),
            F_SERVER_START_WAIT_SECONDS,
        )
    if fbMacXServerAcceptingNetworkConnections(iPort):
        return _fdictAssessment(S_STATE_READY, "", "")
    return _fdictAssessUnreachableServer(dictServer, iPort)


def _fdictAssessUnreachableServer(dictServer, iPort):
    """Explain why an installed X server is not listening on its port."""
    sDomain = dictServer["sPreferenceDomain"]
    if fsReadNetworkClientPreference(sDomain) == "blocked":
        return _fdictAssessment(
            S_STATE_NETWORK_BLOCKED,
            f"{dictServer['sProduct']} refuses network clients, and a "
            "container is one.",
            f"Run `defaults write {sDomain} nolisten_tcp -bool false`, "
            "quit the X server completely, then restart the container.",
        )
    if not fbMacXServerIsRunning():
        return _fdictAssessment(
            S_STATE_NOT_RUNNING,
            f"{dictServer['sProduct']} is not running.",
            _fsStartInstruction(dictServer),
        )
    return _fdictAssessment(
        S_STATE_NOT_LISTENING,
        f"{dictServer['sProduct']} is running but nothing is listening "
        f"on TCP port {iPort}.",
        "Quit the X server completely and start it again so its "
        "network-client setting takes effect, then restart the container.",
    )


def _fsStartInstruction(dictServer):
    """Say how to start the found server."""
    if dictServer["sAppPath"]:
        return f"Open {dictServer['sAppPath']}, then restart the container."
    return "Start the Xquartz server, then restart the container."


def _fdictAssessment(sState, sMessage, sFix):
    """Build one assessment record."""
    return {
        "sState": sState, "bReady": sState == S_STATE_READY,
        "sMessage": sMessage, "sFix": sFix,
    }


def fdictAssessLinuxX11():
    """Say whether a container could reach this Linux host's X display."""
    if not os.environ.get("DISPLAY"):
        return _fdictAssessment(
            S_STATE_NO_DISPLAY,
            "This session has no DISPLAY, so there is no X display to "
            "forward.",
            "Run vaibify from a graphical session, then restart the "
            "container.",
        )
    if not os.path.isdir("/tmp/.X11-unix"):
        return _fdictAssessment(
            S_STATE_NO_SERVER,
            "The X11 socket directory /tmp/.X11-unix does not exist.",
            "Start an X server or a Wayland session with XWayland, then "
            "restart the container.",
        )
    return _fdictAssessment(S_STATE_READY, "", "")


def fdictAssessHostX11():
    """Assess X11 forwarding readiness on this host without changing it."""
    sPlatform = platform.system()
    if sPlatform == "Darwin":
        iPort = X11_TCP_BASE_PORT + fiResolveHostDisplayNumber()
        return fdictAssessMacX11(fdictFindMacXServer(), iPort)
    if sPlatform == "Linux":
        return fdictAssessLinuxX11()
    return _fdictAssessment(
        S_STATE_UNSUPPORTED,
        f"X11 forwarding is not supported on {sPlatform}.", "",
    )


def fdictAssessContainerX11(bContainerHasDisplay):
    """Say whether a running container was created with X11 forwarding.

    DISPLAY and the Linux socket mount are fixed when the container is
    created, so enabling X11 later changes nothing until it is recreated.
    """
    if bContainerHasDisplay:
        return _fdictAssessment(S_STATE_READY, "", "")
    return _fdictAssessment(
        S_STATE_NO_DISPLAY,
        "This container was created without X11 forwarding, so it has "
        "no DISPLAY and graphical programs cannot open a window.",
        "Stop and start the container so it is created again with X11.",
    )


def fiResolveHostDisplayNumber():
    """Return the host's X display number (the N in ``:N``), default 0."""
    matchDisplay = re.search(r":(\d+)", os.environ.get("DISPLAY", ""))
    return int(matchDisplay.group(1)) if matchDisplay else 0


def fsResolveMacContainerDisplay():
    """Return the DISPLAY value to pass into the macOS container.

    Takes the display number from the host's DISPLAY, which on macOS is
    often a launchd socket path such as ``/private/tmp/.../org.xquartz:0``
    rather than a host name, and aims the container at
    host.docker.internal over TCP. Falls back to display 0 when the
    host DISPLAY is unset.
    """
    sHostDisplay = os.environ.get("DISPLAY", "")
    if not sHostDisplay:
        return f"{MAC_CONTAINER_HOST}:0"
    return f"{MAC_CONTAINER_HOST}{_fsExtractDisplaySuffix(sHostDisplay)}"


def _fsExtractDisplaySuffix(sDisplay):
    """Return the ':N[.M]' portion of a DISPLAY string."""
    iColonIndex = sDisplay.rfind(":")
    if iColonIndex < 0:
        return ":0"
    return sDisplay[iColonIndex:]


def _fnPrintAssessmentNotice(dictAssessment):
    """Print a not-ready assessment to stderr once per invocation."""
    sKey = dictAssessment["sState"]
    if sKey in _setNoticesShownThisInvocation:
        return
    _setNoticesShownThisInvocation.add(sKey)
    print(
        "[vaibify] X11 forwarding is enabled but not usable: "
        + dictAssessment["sMessage"],
        file=sys.stderr,
    )
    print("[vaibify]   " + dictAssessment["sFix"], file=sys.stderr)


def _fnRunBestEffort(saArgs):
    """Run a subprocess, ignoring missing binaries and non-zero exits.

    X11 setup is optional: a host without xhost, XQuartz, or pgrep
    should not crash container start.
    """
    try:
        subprocess.run(saArgs, capture_output=True)
    except FileNotFoundError:
        return


def _fsFindXhost():
    """Return the xhost executable, looking where X packages install it."""
    sOnPath = shutil.which("xhost")
    if sOnPath:
        return sOnPath
    for sDirectory, _sDomain in MAC_BINARY_PREFERENCE_DOMAINS:
        sCandidate = os.path.join(sDirectory, "xhost")
        if os.path.isfile(sCandidate):
            return sCandidate
    return "xhost"


def _fnGrantLocalUserXhostAccess():
    """Grant the current local user X11 access via xhost."""
    sUser = os.environ.get("USER", "")
    if not sUser:
        return
    _fnRunBestEffort([_fsFindXhost(), "+SI:localuser:" + sUser])


def fnGrantMacXhostAccess():
    """Let the container's TCP connection past the X server's host check.

    ``+SI:localuser:$USER`` is evaluated against local (Unix-socket)
    clients only, so it cannot admit a container that arrives over TCP;
    ``+localhost`` is the narrowest host entry that does (the container
    engine forwards its connection from the loopback address). Both
    grants are made. Revoke the TCP grant with ``xhost -localhost``.
    """
    _fnGrantLocalUserXhostAccess()
    _fnRunBestEffort([_fsFindXhost(), "+localhost"])


def fnRevokeMacXhostAccess():
    """Withdraw the TCP host entry that fnGrantMacXhostAccess added."""
    _fnRunBestEffort([_fsFindXhost(), "-localhost"])


def _fbProcessIsRunning(sProcessName):
    """Check whether a process with the given name is running."""
    try:
        processResult = subprocess.run(
            ["pgrep", "-x", sProcessName],
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return processResult.returncode == 0
