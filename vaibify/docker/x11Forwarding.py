"""X11 display forwarding for Docker containers.

Forwarding is opt-in per project (``x11Forwarding: true`` in
``vaibify.yml``); nothing here runs for a project that did not ask for
it. An opted-in container is admitted to the display by its OWN X
authorization, made and recorded by :mod:`x11Authorizations` and
mounted read-only into the container; no ``xhost`` grant is made, so a
container without a cookie is refused however it reaches the server.
On macOS the container reaches the host's X server over TCP at
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

from .x11Authorizations import (
    S_SERVER_COOKIE_NOTICE,
    S_TRUST_SERVER_COOKIE,
    fbAuthorizationUsable,
    fdictPrepareAuthorization,
    flistMountArguments,
)


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
S_LINUX_SOCKET_DIRECTORY = "/tmp/.X11-unix"
F_SERVER_START_WAIT_SECONDS = 10.0
F_POLL_INTERVAL_SECONDS = 0.25
F_BEST_EFFORT_TIMEOUT_SECONDS = 10.0

S_STATE_READY = "ready"
S_STATE_NO_SERVER = "no-server"
S_STATE_NETWORK_BLOCKED = "network-blocked"
S_STATE_NOT_LISTENING = "not-listening"
S_STATE_NOT_RUNNING = "not-running"
S_STATE_NO_DISPLAY = "no-display"
S_STATE_UNSUPPORTED = "unsupported"
S_STATE_NOT_CHECKED = "not-checked"
S_STATE_CREATED_WITHOUT = "created-without-x11"
S_STATE_CREATED_WITH = "created-with-x11"
S_RECREATE_COMMAND = "vaibify stop && vaibify start"

_setNoticesShownThisInvocation = set()


def flistConfigureX11Args(sContainerName, sProjectUuid):
    """Return docker run args forwarding the display to one container.

    Detects macOS vs Linux and delegates to the appropriate setup. The
    caller decides whether the project opted in; this function always
    configures. The arguments carry the container's own authorization,
    made for ``sContainerName`` and recorded against ``sProjectUuid``;
    when none could be made, no DISPLAY is passed and the record says
    why.
    """
    saRunArgs = []
    sPlatform = platform.system()
    if sPlatform == "Darwin":
        fnConfigureMacX11(saRunArgs, sContainerName, sProjectUuid)
    elif sPlatform == "Linux":
        fnConfigureLinuxX11(saRunArgs, sContainerName, sProjectUuid)
    return saRunArgs


def fnConfigureMacX11(saRunArgs, sContainerName, sProjectUuid):
    """Configure X11 forwarding for macOS with whichever X server exists.

    Lets the server accept network clients if it does not yet, starts
    it if it is not running, and, once something listens on the port
    the container will dial, gives the container its own authorization
    and sets DISPLAY to the host's own display number. When the server
    is missing or not yet listening, the reason and the one fix are
    printed and no DISPLAY is passed.
    """
    dictServer = fdictFindMacXServer()
    if dictServer is not None:
        _fnAllowNetworkClients(dictServer)
        fnStartMacXServer(dictServer)
    iNumber = fiResolveHostDisplayNumber()
    dictAssessment = fdictAssessMacX11(
        dictServer, X11_TCP_BASE_PORT + iNumber, bWaitForPort=True,
    )
    if not dictAssessment["bReady"]:
        _fnPrintAssessmentNotice(dictAssessment)
        return
    _fbExtendWithAuthorization(
        saRunArgs, sContainerName, sProjectUuid,
        sDisplay=os.environ.get("DISPLAY", ""),
        sProbeDisplay=f"localhost:{iNumber}", sNumber=str(iNumber),
        sContainerDisplay=fsResolveMacContainerDisplay(),
    )


def fnConfigureLinuxX11(saRunArgs, sContainerName, sProjectUuid):
    """Configure X11 forwarding for Linux through the socket directory.

    The container's own authorization admits it; the X11 socket
    directory is mounted read-only and DISPLAY is the host's own.
    """
    dictAssessment = fdictAssessLinuxX11()
    if not dictAssessment["bReady"]:
        _fnPrintAssessmentNotice(dictAssessment)
        return
    sDisplay = os.environ["DISPLAY"]
    bUsable = _fbExtendWithAuthorization(
        saRunArgs, sContainerName, sProjectUuid, sDisplay=sDisplay,
        sProbeDisplay=sDisplay, sNumber=str(fiResolveHostDisplayNumber()),
        sContainerDisplay=sDisplay,
    )
    if bUsable:
        saRunArgs.extend([
            "--mount",
            f"type=bind,source={S_LINUX_SOCKET_DIRECTORY},"
            f"target={S_LINUX_SOCKET_DIRECTORY},readonly",
        ])


def _fbExtendWithAuthorization(
    saRunArgs, sContainerName, sProjectUuid, sDisplay, sProbeDisplay,
    sNumber, sContainerDisplay,
):
    """Make the container's authorization; add DISPLAY and its mount when usable."""
    dictOutcome = fdictPrepareAuthorization(
        sContainerName, sProjectUuid, sDisplay, sProbeDisplay, sNumber,
    )
    if not fbAuthorizationUsable(dictOutcome):
        _fnPrintNoticeOnce(
            dictOutcome["sOutcome"],
            "X11 forwarding is enabled but no authorization could be made "
            f"for this container ({dictOutcome['sOutcome']}: "
            f"{dictOutcome.get('sReason') or 'no detail'}); it starts "
            "without a display.",
        )
        return False
    saRunArgs.extend(["-e", f"DISPLAY={sContainerDisplay}"])
    saRunArgs.extend(flistMountArguments(dictOutcome["sDirectory"]))
    if dictOutcome.get("sFallbackCause"):
        _fnPrintNoticeOnce(S_TRUST_SERVER_COOKIE, S_SERVER_COOKIE_NOTICE)
    return True


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


def _fnAllowNetworkClients(dictServer):
    """Turn the server's network-client preference on, if it is not.

    A container is a network client of the Mac's X server, and both
    servers ship refusing them. The preference is read by the server
    when it starts, so a server already running keeps refusing until it
    is quit and started again; the assessment says exactly that.
    """
    sDomain = dictServer["sPreferenceDomain"]
    if fsReadNetworkClientPreference(sDomain) == "allowed":
        return
    _fnRunBestEffort(
        ["defaults", "write", sDomain, "nolisten_tcp", "-bool", "false"],
    )
    _fnPrintNoticeOnce(
        "network-clients-enabled",
        f"{dictServer['sProduct']} is now set to accept network clients "
        "(a container is one). If it was already running, quit it "
        "completely and start it again for the setting to take effect.",
    )


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


def _fdictAssessment(sState, sMessage, sFix, sCommand=""):
    """Build one assessment record."""
    return {
        "sState": sState, "bReady": sState == S_STATE_READY,
        "sMessage": sMessage, "sFix": sFix, "sCommand": sCommand,
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
    if not os.path.isdir(S_LINUX_SOCKET_DIRECTORY):
        return _fdictAssessment(
            S_STATE_NO_SERVER,
            f"The X11 socket directory {S_LINUX_SOCKET_DIRECTORY} does "
            "not exist.",
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


def fbContainerForwardsDisplay(jsonInspect):
    """Return True when a container was created with a forwarded display.

    Reads what ``docker inspect`` reports the container was created
    with: a DISPLAY variable that is reachable, meaning either the X11
    socket is mounted (Linux) or DISPLAY names the macOS TCP host.
    """
    listEnvironment = (jsonInspect.get("Config") or {}).get("Env") or []
    sDisplay = next(
        (sEntry[len("DISPLAY="):] for sEntry in listEnvironment
         if sEntry.startswith("DISPLAY=")), "",
    )
    bSocketMounted = any(
        dictMount.get("Destination") == S_LINUX_SOCKET_DIRECTORY
        for dictMount in jsonInspect.get("Mounts") or []
    )
    return bool(sDisplay) and (
        bSocketMounted or sDisplay.startswith(MAC_CONTAINER_HOST)
    )


def fdictAssessContainerX11(bRequested, jsonInspect):
    """Compare a container's real X11 wiring with the project's setting.

    DISPLAY and the Linux socket mount are fixed when a container is
    created, so changing ``x11Forwarding`` later changes nothing until
    the container is created again. An empty ``jsonInspect`` means the
    daemon did not answer and nothing is claimed.
    """
    if not jsonInspect:
        return _fdictAssessment(
            S_STATE_NOT_CHECKED, "The container could not be inspected.", "",
        )
    bForwarded = fbContainerForwardsDisplay(jsonInspect)
    if bRequested and not bForwarded:
        return _fdictAssessment(
            S_STATE_CREATED_WITHOUT,
            "This project sets x11Forwarding, but this container was "
            "created without X11 forwarding, so it has no DISPLAY and "
            "graphical programs cannot open a window.",
            "Create the container again to enable it.", S_RECREATE_COMMAND,
        )
    if bForwarded and not bRequested:
        return _fdictAssessment(
            S_STATE_CREATED_WITH,
            "This project has x11Forwarding off, but this container was "
            "created with it. Its X authorization was revoked when the "
            "setting changed, and DISPLAY stays set until the container "
            "is created again.",
            "Create the container again to remove it.", S_RECREATE_COMMAND,
        )
    return _fdictAssessment(S_STATE_READY, "", "")


def flistDescribeAssessment(dictAssessment):
    """Return the one sentence for a not-ready assessment, else nothing."""
    if dictAssessment["bReady"] or dictAssessment["sState"] in (
        S_STATE_NOT_CHECKED, S_STATE_UNSUPPORTED,
    ):
        return []
    sCommand = dictAssessment.get("sCommand", "")
    sTail = f" Run `{sCommand}`." if sCommand else ""
    return [f"{dictAssessment['sMessage']} {dictAssessment['sFix']}{sTail}"]


def ftDescribeX11Findings(bRequested, jsonInspect):
    """Return ``(container lines, host lines)`` a surface should show.

    Container lines say the running container disagrees with the
    project's setting. Host lines say the setting is on but this host
    cannot serve a display; they are only produced when it is on.
    """
    listContainerLines = flistDescribeAssessment(
        fdictAssessContainerX11(bRequested, jsonInspect))
    listHostLines = (
        flistDescribeAssessment(fdictAssessHostX11()) if bRequested else []
    )
    return listContainerLines, listHostLines


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
    _fnPrintNoticeOnce(
        dictAssessment["sState"],
        "X11 forwarding is enabled but not usable: "
        + dictAssessment["sMessage"] + "\n[vaibify]   " + dictAssessment["sFix"],
    )


def _fnPrintNoticeOnce(sKey, sNotice):
    """Print one notice to stderr, once per invocation per key."""
    if sKey in _setNoticesShownThisInvocation:
        return
    _setNoticesShownThisInvocation.add(sKey)
    print("[vaibify] " + sNotice, file=sys.stderr)


def _fnRunBestEffort(saArgs):
    """Run a subprocess, ignoring missing binaries, hangs and non-zero exits.

    X11 setup is optional: a host without xhost, XQuartz, or pgrep
    should not crash container start.
    """
    try:
        subprocess.run(
            saArgs, capture_output=True, timeout=F_BEST_EFFORT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
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


def fnRevokeLegacyXhostGrants():
    """Withdraw the host-based grants earlier versions made.

    ``xhost +localhost`` admitted every client arriving over loopback
    and ``+SI:localuser:$USER`` every local client of the researcher's
    account, container or not; with per-container cookies neither is
    needed, and either would let a container without a cookie in. This
    also removes such grants the researcher made by hand, which the
    documentation says. Nothing runs without a display, or on a Mac
    whose X server is not running: connecting to the launchd socket
    would start it.
    """
    if not os.environ.get("DISPLAY"):
        return
    if platform.system() == "Darwin" and not fbMacXServerIsRunning():
        return
    _fnRunBestEffort([_fsFindXhost(), "-localhost"])
    sUser = os.environ.get("USER", "")
    if sUser:
        _fnRunBestEffort([_fsFindXhost(), "-SI:localuser:" + sUser])


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
