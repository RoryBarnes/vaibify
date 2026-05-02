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
    _fnAddCpuAllocation(saRunArgs)
    _fnAddVolumeMount(config, saRunArgs)
    _fnAddCredentialsVolume(config, saRunArgs)
    _fnAddPortForwarding(config, saRunArgs)
    _fnAddBindMounts(config, saRunArgs)
    _fnAddGpuPassthrough(config, saRunArgs)
    _fnAddClaudeEnv(config, saRunArgs)
    _fnAddAgentHostBridge(saRunArgs)
    _fnAddNetworkIsolation(config, saRunArgs)
    saRunArgs.extend(flistConfigureX11Args())
    return saRunArgs


def _fnAddCpuAllocation(saRunArgs):
    """Add CPU limit to run args (total cores minus one)."""
    iCpuCount = max(1, (os.cpu_count() or 2) - 1)
    saRunArgs.extend(["--cpus", str(iCpuCount)])


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
    """Add port forwarding flags from config.listPorts."""
    for dictPort in config.listPorts:
        sHost = str(dictPort.get("host", dictPort.get("container")))
        sContainer = str(dictPort.get("container"))
        saRunArgs.extend(["-p", f"{sHost}:{sContainer}"])


def _fnAddBindMounts(config, saRunArgs):
    """Add bind mount flags from config.listBindMounts."""
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


def _fnAddAgentHostBridge(saRunArgs):
    """Resolve ``host.docker.internal`` to the host gateway.

    The in-container ``vaibify-do`` agent dials back to the host
    backend through this hostname. Docker Desktop resolves it
    automatically; explicit ``--add-host`` makes it work on Linux and
    is harmless elsewhere. When ``--network none`` is also set the
    hosts-file entry remains but is unreachable by design, which is
    the correct behavior for a sealed container.
    """
    saRunArgs.extend([
        "--add-host", "host.docker.internal:host-gateway",
    ])


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
