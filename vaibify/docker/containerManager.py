"""Container lifecycle management using subprocess Docker CLI calls."""

import os
import subprocess

from . import fnRunDockerCommand
from .volumeManager import fsGetCredentialsVolumeName, fsGetVolumeName
from .x11Forwarding import flistConfigureX11Args


def fnStartContainer(config, sDockerDir, saCommand=None):
    """Start a container with run args derived from config.

    Host secret files are intentionally not cleaned up here. See
    ``fsStartContainerDetached`` for the rationale (Colima's
    virtiofs bridge re-resolves bind-mount sources during later
    operations).
    """
    listCleanupFiles = []
    saRunArgs = flistBuildRunArgs(config)
    fnMountSecrets(config, saRunArgs, listCleanupFiles)
    saFullCommand = _flistAssembleRunCommand(config, saRunArgs, saCommand)
    _fnRunDockerCommand(saFullCommand)


def fsStartContainerDetached(config, sDockerDir):
    """Start a container in detached mode and return its ID.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.
    sDockerDir : str
        Path to the docker build context directory.

    Returns
    -------
    str
        The Docker container ID.

    Note
    ----
    The ephemeral host files holding each mounted secret are
    intentionally NOT cleaned up here. On Colima/macOS the daemon
    lazily re-resolves bind-mount sources during later operations
    like ``put_archive``; deleting the host file mid-session makes
    those operations fail with "not a directory" mount errors. Let
    the files outlive the container (they are mode 600 in
    ``~/.vaibify/tmp/`` and get overwritten on the next container
    start).
    """
    listCleanupFiles = []
    saRunArgs = flistBuildRunArgs(config, bDetached=True)
    fnMountSecrets(config, saRunArgs, listCleanupFiles)
    saFullCommand = _flistAssembleRunCommand(
        config, saRunArgs, ["sleep", "infinity"],
    )
    return _fsRunDetachedCommand(saFullCommand)


def _fsRunDetachedCommand(saCommand):
    """Run a docker command and return stdout (container ID)."""
    resultProcess = subprocess.run(
        saCommand, capture_output=True, text=True,
    )
    if resultProcess.returncode != 0:
        sError = resultProcess.stderr.strip()
        raise RuntimeError(f"Docker run failed: {sError}")
    return resultProcess.stdout.strip()


def _flistAssembleRunCommand(config, saRunArgs, saCommand):
    """Combine docker run prefix, args, image tag, and user command."""
    sImageTag = f"{config.sProjectName}:latest"
    saFullCommand = ["docker", "run"] + saRunArgs + [sImageTag]
    if saCommand is not None:
        saFullCommand.extend(saCommand)
    return saFullCommand


def flistBuildRunArgs(config, bDetached=False):
    """Build list of docker run arguments from project config."""
    saRunArgs = ["-d", "-t"] if bDetached else ["--rm", "-it"]
    saRunArgs.extend(["--name", config.sProjectName])
    saRunArgs.extend(["--hostname", config.sProjectName])
    _fnAddEntrypointUser(saRunArgs)
    _fnAddCapabilityDrops(saRunArgs)
    _fnAddCpuAllocation(config, saRunArgs)
    _fnAddMemoryAllocation(config, saRunArgs)
    _fnAddVolumeMount(config, saRunArgs)
    _fnAddCredentialsVolume(config, saRunArgs)
    _fnAddPortForwarding(config, saRunArgs)
    _fnAddBindMounts(config, saRunArgs)
    _fnAddGpuPassthrough(config, saRunArgs)
    _fnAddClaudeEnv(config, saRunArgs)
    _fnAddAgentHostBridge(config, saRunArgs)
    _fnAddNetworkIsolation(config, saRunArgs)
    saRunArgs.extend(flistConfigureX11Args())
    return saRunArgs


_T_ENTRYPOINT_CAPABILITIES = (
    "CHOWN",        # one-time migration chown of pre-existing workspaces
    "FOWNER",       # chown across existing ownerships during migration
    "DAC_OVERRIDE", # gosu reads /etc/passwd + the user's shell init files
    "SETUID",       # gosu setuid to the container user
    "SETGID",       # gosu setgid to the container user's group
)


def _fnAddCapabilityDrops(saRunArgs):
    """Drop Linux capabilities to the minimum the entrypoint requires.

    The entrypoint's root phase writes to system paths (``/etc/gitconfig``,
    ``/usr/local/bin/``, ``/etc/profile.d/``) and performs a one-time
    migration ``chown`` for pre-existing workspace volumes, then drops to
    the container user via ``exec gosu``. The five capabilities in
    ``_T_ENTRYPOINT_CAPABILITIES`` are the minimum set that satisfies
    these operations — everything else (CAP_NET_RAW, CAP_NET_ADMIN,
    CAP_SYS_ADMIN, ptrace, raw sockets, kernel capability abuse) is gone.

    ``--security-opt=no-new-privileges`` still prevents any setuid
    binary inside the image from picking up additional capabilities
    after the gosu drop completes. Feature flags that legitimately
    require additional capabilities must re-add them under that
    feature's own ``--cap-add`` argument.
    """
    saRunArgs.extend(["--cap-drop", "ALL"])
    for sCapability in _T_ENTRYPOINT_CAPABILITIES:
        saRunArgs.extend(["--cap-add", sCapability])
    saRunArgs.extend(["--security-opt", "no-new-privileges"])


def _fnAddEntrypointUser(saRunArgs):
    """Force the entrypoint root phase to run as root.

    The Dockerfile pins ``USER ${CONTAINER_USER}`` so every ``docker exec``
    issued by the GUI/CLI lands unprivileged. The entrypoint's root phase
    writes to system paths and then re-invokes itself as the container user
    via ``exec gosu``; ``--user 0`` restores root for that initial phase.
    """
    saRunArgs.extend(["--user", "0"])


def _fnAddCpuAllocation(config, saRunArgs):
    """Add CPU limit to run args (config cap or total cores minus one).

    ``iCpuLimit`` of zero means "no explicit limit", which keeps the
    historical default of all host cores minus one. A configured cap
    is clamped to the host's core count so a config written on a
    larger machine cannot ask Docker for cores that do not exist.
    """
    iHostCores = os.cpu_count() or 2
    iConfiguredLimit = getattr(config, "iCpuLimit", 0)
    if iConfiguredLimit > 0:
        iCpuCount = min(iConfiguredLimit, iHostCores)
    else:
        iCpuCount = max(1, iHostCores - 1)
    saRunArgs.extend(["--cpus", str(iCpuCount)])


def _fnAddMemoryAllocation(config, saRunArgs):
    """Add the optional memory cap (0 = unlimited, the default)."""
    fMemoryGigabytes = getattr(config, "fMemoryLimitGigabytes", 0.0)
    if fMemoryGigabytes > 0:
        saRunArgs.extend(["--memory", f"{fMemoryGigabytes:g}g"])


def _fnAddVolumeMount(config, saRunArgs):
    """Add the workspace volume mount to run args."""
    sVolumeName = fsGetVolumeName(config)
    sWorkspaceRoot = config.sWorkspaceRoot
    saRunArgs.extend(["-v", f"{sVolumeName}:{sWorkspaceRoot}"])


def _fnAddCredentialsVolume(config, saRunArgs):
    """Mount the credentials volume at the container keyring data dir.

    Persists ``PlaintextKeyring`` passwords across container
    recreations. The Dockerfile pre-creates
    ``~/.local/share/python_keyring/`` with mode 700 and the
    container user as owner; Docker's copy-on-mount behaviour
    copies that empty directory into the named volume the first
    time the container runs, so subsequent rebuilds reuse
    whatever was stored.
    """
    sVolumeName = fsGetCredentialsVolumeName(config)
    sUser = getattr(config, "sContainerUser", "researcher")
    sContainerPath = f"/home/{sUser}/.local/share/python_keyring"
    saRunArgs.extend(["-v", f"{sVolumeName}:{sContainerPath}"])


def _fnAddPortForwarding(config, saRunArgs):
    """Add port forwarding flags from ``config.listPorts``.

    Binds every forwarded host port to ``127.0.0.1`` so the service
    inside the container is only reachable from the host itself, not
    from the LAN. A user who knowingly wants LAN exposure (e.g. to
    pair-program against the container's web UI from another laptop)
    can set ``lanExpose: true`` on the per-port entry to opt out of
    the loopback binding.
    """
    for dictPort in config.listPorts:
        sHost = str(dictPort.get("host", dictPort.get("container")))
        sContainer = str(dictPort.get("container"))
        sSpec = _fsBuildPortSpec(sHost, sContainer, dictPort)
        saRunArgs.extend(["-p", sSpec])


def _fsBuildPortSpec(sHost, sContainer, dictPort):
    """Return the ``-p`` value with the right loopback/LAN binding."""
    if bool(dictPort.get("lanExpose", False)):
        return f"{sHost}:{sContainer}"
    return f"127.0.0.1:{sHost}:{sContainer}"


def _fnAddBindMounts(config, saRunArgs):
    """Add bind mount flags from config.listBindMounts.

    Re-validates each entry against the allowlist so any
    ``vaibify.yml`` that bypassed the config loader (hand-crafted dict,
    in-memory mutation, future config sources) still cannot smuggle in
    a Docker-socket or ``/etc`` bind mount.
    """
    from vaibify.config.bindMountValidator import (
        fnValidateBindMountList,
    )
    fnValidateBindMountList(config.listBindMounts)
    for dictMount in config.listBindMounts:
        sMountSpec = f"{dictMount['host']}:{dictMount['container']}"
        if dictMount.get("readOnly", False):
            sMountSpec += ":ro"
        saRunArgs.extend(["-v", sMountSpec])


def _fnAddGpuPassthrough(config, saRunArgs):
    """Add GPU passthrough flag if GPU feature is enabled."""
    if config.features.bGpu:
        saRunArgs.extend(["--gpus", "all"])


def _fnAddClaudeEnv(config, saRunArgs):
    """Pass Claude Code auto-update flag into the container via env var."""
    if not config.features.bClaude:
        return
    sFlag = "true" if config.features.bClaudeAutoUpdate else "false"
    saRunArgs.extend(["-e", f"VAIBIFY_CLAUDE_AUTO_UPDATE={sFlag}"])


def _fnAddNetworkIsolation(config, saRunArgs):
    """Add network isolation flag if enabled in config."""
    if config.bNetworkIsolation:
        saRunArgs.extend(["--network", "none"])


def _fnAddAgentHostBridge(config, saRunArgs):
    """Resolve ``host.docker.internal`` only when the agent needs it.

    The hosts-file entry is only useful for the in-container
    ``vaibify-do`` agent calling back to the host backend. Adding it
    unconditionally widens the container's egress surface for projects
    that never run an agent. Emit ``--add-host`` only when Claude is
    enabled (so the agent bridge is actually used) and network
    isolation is off (no point poking a hole in a sealed container).
    """
    if not _fbAgentBridgeRequired(config):
        return
    saRunArgs.extend([
        "--add-host", "host.docker.internal:host-gateway",
    ])


def _fbAgentBridgeRequired(config):
    """Return True when the agent host bridge should be wired up."""
    if getattr(config, "bNetworkIsolation", False):
        return False
    features = getattr(config, "features", None)
    if features is None:
        return False
    return bool(getattr(features, "bClaude", False))


def fnMountSecrets(config, saRunArgs, listCleanupFiles):
    """Mount each secret as a read-only temp file with mode 600."""
    from vaibify.config.secretManager import fsMountSecret
    for dictSecret in config.listSecrets:
        _fnMountSingleSecret(
            dictSecret, saRunArgs, listCleanupFiles, fsMountSecret,
        )


def _fnMountSingleSecret(
    dictSecret, saRunArgs, listCleanupFiles, fnMount,
):
    """Retrieve one secret via secretManager and add its mount arg."""
    sName = dictSecret["name"]
    sMethod = dictSecret["method"]
    sTempPath = fnMount(sName, sMethod)
    listCleanupFiles.append(sTempPath)
    sContainerPath = f"/run/secrets/{sName}"
    saRunArgs.extend(["-v", f"{sTempPath}:{sContainerPath}:ro"])


def _fnCleanupTempFiles(listCleanupFiles):
    """Remove temporary secret files, ignoring errors."""
    for sPath in listCleanupFiles:
        try:
            os.unlink(sPath)
        except OSError:
            pass


def fnStopContainer(sProjectName):
    """Stop and remove a container by project name.

    Parameters
    ----------
    sProjectName : str
        Name of the container to stop.
    """
    resultProcess = subprocess.run(
        ["docker", "stop", sProjectName],
        capture_output=True, text=True,
    )
    if resultProcess.returncode != 0:
        raise RuntimeError(
            f"docker stop failed: "
            f"{resultProcess.stderr.strip()}"
        )
    fnRemoveStopped(sProjectName)


def fnRemoveStopped(sProjectName):
    """Remove a stopped container if it still exists."""
    saCommand = ["docker", "rm", sProjectName]
    try:
        subprocess.run(
            saCommand, capture_output=True, text=True, check=False,
        )
    except Exception:
        pass


def fbContainerIsRunning(sProjectName):
    """Check if a container with the given name is currently running.

    Parameters
    ----------
    sProjectName : str
        Container name to check.

    Returns
    -------
    bool
        True if the container is running.
    """
    resultProcess = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", sProjectName],
        capture_output=True,
        text=True,
    )
    return resultProcess.stdout.strip() == "true"


def fdictGetContainerStatus(sProjectName):
    """Return status dict with keys: bExists, bRunning, sStatus."""
    sRawStatus = _fsInspectContainerState(sProjectName)
    return _fdictParseContainerState(sRawStatus)


def _fsInspectContainerState(sProjectName):
    """Query docker inspect for the container state, or empty string."""
    resultProcess = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", sProjectName],
        capture_output=True,
        text=True,
    )
    if resultProcess.returncode != 0:
        return ""
    return resultProcess.stdout.strip()


def _fdictParseContainerState(sRawStatus):
    """Parse raw status string into a structured status dict."""
    bExists = len(sRawStatus) > 0
    sStatus = sRawStatus if bExists else "not found"
    bRunning = sStatus == "running"
    return {"bExists": bExists, "bRunning": bRunning, "sStatus": sStatus}


def fbContainerIsNetworkIsolated(sContainerIdentifier):
    """Return True when the container's NetworkMode is ``none``.

    Reflects the runtime ground truth (the value passed to
    ``docker run --network``) rather than the saved ``vaibify.yml``.
    Used by host-side routes that must refuse to dispatch external
    network calls (Overleaf, Zenodo) when the container is sealed,
    so the user sees an actionable error instead of a 30-second DNS
    timeout (audit finding F-R-08).

    Fail-open semantics on inspect failure
    --------------------------------------
    Returns ``False`` (i.e., "not isolated") when ``docker inspect``
    cannot answer — the container was just stopped, removed, or
    never started; the docker CLI is missing (e.g., on a CI runner
    that doesn't ship docker); or the call times out. This is
    intentional: if the container is unreachable, no egress can
    occur regardless, so the gating routes can return their normal
    "container not running" error rather than a confusing isolation
    message. Tightening this to fail-closed would block legitimate
    calls during transient docker-daemon hiccups; do not change
    without revisiting the gating routes' caller-facing semantics.
    """
    try:
        resultProcess = subprocess.run(
            [
                "docker", "inspect", "-f",
                "{{.HostConfig.NetworkMode}}", sContainerIdentifier,
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if resultProcess.returncode != 0:
        return False
    return resultProcess.stdout.strip() == "none"


_fnRunDockerCommand = fnRunDockerCommand
