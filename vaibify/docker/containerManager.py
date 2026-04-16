"""Container lifecycle management using subprocess Docker CLI calls."""

import os
import subprocess

from . import fnRunDockerCommand
from .volumeManager import fsGetVolumeName
from .x11Forwarding import flistConfigureX11Args


def fnStartContainer(config, sDockerDir, saCommand=None):
    """Start a container with run args derived from config."""
    listCleanupFiles = []
    try:
        saRunArgs = flistBuildRunArgs(config)
        fnMountSecrets(config, saRunArgs, listCleanupFiles)
        saFullCommand = _flistAssembleRunCommand(config, saRunArgs, saCommand)
        _fnRunDockerCommand(saFullCommand)
    finally:
        _fnCleanupTempFiles(listCleanupFiles)


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
    """
    listCleanupFiles = []
    saRunArgs = flistBuildRunArgs(config, bDetached=True)
    fnMountSecrets(config, saRunArgs, listCleanupFiles)
    saFullCommand = _flistAssembleRunCommand(
        config, saRunArgs, ["sleep", "infinity"],
    )
    sContainerId = _fsRunDetachedCommand(saFullCommand)
    _fnDeferSecretCleanup(listCleanupFiles, sContainerId)
    return sContainerId


def _fnDeferSecretCleanup(listCleanupFiles, sContainerId):
    """Wait for the entrypoint to read secrets, then clean up."""
    if not listCleanupFiles:
        return
    import threading

    def _fnCleanupAfterDelay():
        import time
        time.sleep(30)
        _fnCleanupTempFiles(listCleanupFiles)

    thread = threading.Thread(
        target=_fnCleanupAfterDelay, daemon=True)
    thread.start()


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
    _fnAddPortForwarding(config, saRunArgs)
    _fnAddBindMounts(config, saRunArgs)
    _fnAddGpuPassthrough(config, saRunArgs)
    _fnAddClaudeEnv(config, saRunArgs)
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


_fnRunDockerCommand = fnRunDockerCommand
