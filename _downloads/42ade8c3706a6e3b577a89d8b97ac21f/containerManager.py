"""Container lifecycle management using subprocess Docker CLI calls.

The GUI's start path is deliberately a CREATE-then-START pair rather
than a single ``docker run`` (design §10b). ``docker run`` gives the
hub no identity for the container until the command returns, so a start
that has to be killed mid-flight leaves a container the hub can only
GUESS at; create-then-start carries the reservation id as an immutable
container label and hands back the container id before anything runs,
so the write-ahead journal records the exact identity a cleanup may
remove — and no other incarnation. Both halves run under ``Popen`` so a
hung start can actually be signalled: a blocking ``subprocess.run`` in
a worker thread cannot be terminated, which made "abort the start"
unachievable as written.
"""

import os
import re
import subprocess
import time

from . import fnRunDockerCommand
from .volumeManager import fsGetCredentialsVolumeName, fsGetVolumeName
from .x11Forwarding import flistConfigureX11Args

# The immutable label a reservation-owned container carries. Cleanup
# keys on THIS, never on the container name: a name is reused by the
# next incarnation, a reservation id never is.
S_RESERVATION_LABEL_KEY = "vaibify.reservation"

# A reservation id is server-minted hex. Validating it before it reaches
# a command line is what makes the label value shell-safe and keeps a
# crafted value from smuggling an extra Docker flag.
_RE_RESERVATION_ID = re.compile(r"^[0-9a-f]{32}$")

# TERM, then this long, then KILL, then wait for the REAL exit.
_F_TERMINATE_GRACE_SECONDS = 5.0
# How long a killed create is watched for a container the daemon may
# still be finishing. Nothing appearing within it is NOT proof of
# absence — it makes the settlement inconclusive, never "clean".
_F_SETTLEMENT_WINDOW_SECONDS = 3.0
_F_SETTLEMENT_POLL_SECONDS = 0.25
# Every probe/removal call is bounded, so a wedged daemon cannot pin a
# worker thread forever; an expired probe reads as "did not answer".
_F_DOCKER_PROBE_TIMEOUT_SECONDS = 10.0


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


def fsCreateContainerForReservation(
    config, sReservationId, fnRegisterProcess=None,
):
    """Create (not start) the container labelled with a reservation id.

    The first half of the create-then-start pair. The returned container
    id is the identity the caller journals BEFORE the container is
    started, so a cleanup after a kill removes exactly this incarnation.
    ``fnRegisterProcess`` receives the live ``Popen`` handle so the
    reservation's cancel path can signal it (design §10b).
    """
    fnValidateReservationIdOrRaise(sReservationId)
    saRunArgs = flistBuildRunArgs(config, bCreateOnly=True)
    fnMountSecrets(config, saRunArgs, [])
    saRunArgs.extend([
        "--label", f"{S_RESERVATION_LABEL_KEY}={sReservationId}",
    ])
    saFullCommand = _flistAssembleDockerCommand(
        "create", config, saRunArgs, ["sleep", "infinity"],
    )
    return _fsRunKillableDockerCommand(saFullCommand, fnRegisterProcess)


def fnStartCreatedContainer(sContainerId, fnRegisterProcess=None):
    """Start an already-created container by its recorded id."""
    _fsRunKillableDockerCommand(
        ["docker", "start", sContainerId], fnRegisterProcess,
    )


def fnValidateReservationIdOrRaise(sReservationId):
    """Raise unless a reservation id is the server-minted hex it claims.

    The id becomes a Docker label value and a filter expression, so it
    is validated before it can reach a command line at all.
    """
    if not _RE_RESERVATION_ID.match(sReservationId or ""):
        raise ValueError(
            "A reservation id must be 32 hexadecimal characters; refusing "
            "to put an unrecognized value on a Docker command line."
        )


def _fsRunKillableDockerCommand(saCommand, fnRegisterProcess=None):
    """Run one docker command under Popen; return its trimmed stdout.

    ``Popen`` rather than ``subprocess.run`` because the start path must
    be terminable: a worker thread blocked in ``run`` cannot be
    signalled, so a hung pull would strand the researcher with a
    container they can neither reach nor clear.
    """
    processDocker = subprocess.Popen(
        saCommand, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    if fnRegisterProcess is not None:
        fnRegisterProcess(processDocker)
    sStdout, sStderr = processDocker.communicate()
    if processDocker.returncode != 0:
        raise RuntimeError(
            f"docker {saCommand[1]} failed: {(sStderr or '').strip()}"
        )
    return (sStdout or "").strip()


def fdictTerminateDockerProcess(
    processDocker, fGraceSeconds=_F_TERMINATE_GRACE_SECONDS,
):
    """TERM, wait the grace, KILL, then wait for the process to REALLY exit.

    Returns what actually happened, because "we sent a signal" is not
    "the writer stopped": the caller may only clean up after the
    process is confirmed exited, and ``bExited`` is that confirmation.
    """
    if processDocker.poll() is not None:
        return _fdictTerminationOutcome(processDocker, False, False)
    processDocker.terminate()
    try:
        processDocker.wait(timeout=fGraceSeconds)
        return _fdictTerminationOutcome(processDocker, True, False)
    except subprocess.TimeoutExpired:
        processDocker.kill()
    processDocker.wait()
    return _fdictTerminationOutcome(processDocker, True, True)


def _fdictTerminationOutcome(processDocker, bTerminated, bKilled):
    """Return the uniform termination report for one docker process."""
    return {
        "bExited": processDocker.poll() is not None,
        "bTerminated": bTerminated,
        "bKilled": bKilled,
        "iReturnCode": processDocker.returncode,
    }


def fdictFindContainersForReservation(sReservationId):
    """Return ``{bAnswered, listContainerIds}`` for the reservation label.

    ``bAnswered`` False means the daemon did not answer at all (absent
    CLI, timeout, error) — which is NOT the same as "no such container"
    and must never be read as one.
    """
    fnValidateReservationIdOrRaise(sReservationId)
    tAnswer = _ftRunProbeCommand([
        "docker", "ps", "-a", "-q", "--filter",
        f"label={S_RESERVATION_LABEL_KEY}={sReservationId}",
    ])
    bAnswered, sOutput = tAnswer
    return {
        "bAnswered": bAnswered,
        "listContainerIds": sOutput.split() if bAnswered else [],
    }


def _ftRunProbeCommand(saCommand):
    """Run a bounded read-only docker command; return ``(bAnswered, sOut)``."""
    try:
        processResult = subprocess.run(
            saCommand, capture_output=True, text=True,
            timeout=_F_DOCKER_PROBE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return (False, "")
    if processResult.returncode != 0:
        return (False, "")
    return (True, processResult.stdout)


def fdictSettleReservationContainers(sReservationId, bLaunchWasKilled):
    """Remove the reservation's container(s) and report CONCLUSIVENESS.

    Design §10b: "reconcile long enough" is not a safety condition.
    Killing the CLI does not prove the daemon abandoned the request, so
    a settlement is conclusive only when the daemon answered and the
    labelled container is definitively gone (or was definitively never
    created). When a killed create leaves nothing behind, the daemon may
    still be finishing one, so the answer is INCONCLUSIVE and the caller
    must keep the container quarantined rather than claimable.
    """
    dictFound = fdictFindContainersForReservation(sReservationId)
    if not dictFound["bAnswered"]:
        return _fdictSettlement(False, [], (
            "the Docker daemon did not answer the reservation-label query"
        ))
    listContainerIds = dictFound["listContainerIds"]
    if not listContainerIds and bLaunchWasKilled:
        listContainerIds = _flistAwaitLateReservationContainer(
            sReservationId,
        )
        if not listContainerIds:
            return _fdictSettlement(False, [], (
                "the container create was killed mid-flight and no "
                "labelled container appeared within the settlement "
                "window; the daemon may still create one"
            ))
    for sContainerId in listContainerIds:
        _fnForceRemoveContainer(sContainerId)
    return _fdictConfirmReservationRemoval(sReservationId, listContainerIds)


def _flistAwaitLateReservationContainer(sReservationId):
    """Watch the label for a container a killed create may still produce."""
    fDeadline = time.monotonic() + _F_SETTLEMENT_WINDOW_SECONDS
    while time.monotonic() < fDeadline:
        time.sleep(_F_SETTLEMENT_POLL_SECONDS)
        dictFound = fdictFindContainersForReservation(sReservationId)
        if dictFound["bAnswered"] and dictFound["listContainerIds"]:
            return dictFound["listContainerIds"]
    return []


def _fdictConfirmReservationRemoval(sReservationId, listRemovedIds):
    """Re-query the label; only a definitively empty answer is conclusive."""
    dictAfter = fdictFindContainersForReservation(sReservationId)
    if not dictAfter["bAnswered"]:
        return _fdictSettlement(False, listRemovedIds, (
            "the Docker daemon stopped answering before the removal could "
            "be confirmed"
        ))
    if dictAfter["listContainerIds"]:
        return _fdictSettlement(False, listRemovedIds, (
            "a container bearing the reservation label survived removal"
        ))
    return _fdictSettlement(True, listRemovedIds, (
        "no container bearing the reservation label remains"
    ))


def _fnForceRemoveContainer(sContainerId):
    """Force-remove one container by id, tolerating an already-gone id."""
    _ftRunProbeCommand(["docker", "rm", "-f", sContainerId])


def _fdictSettlement(bConclusive, listRemovedIds, sDetail):
    """Return the uniform settlement verdict for a reservation cleanup."""
    return {
        "bConclusive": bConclusive,
        "listRemovedContainerIds": list(listRemovedIds),
        "sDetail": sDetail,
    }


def _fsRunDetachedCommand(saCommand):
    """Run a docker command and return stdout (container ID)."""
    processResult = subprocess.run(
        saCommand, capture_output=True, text=True,
    )
    if processResult.returncode != 0:
        sError = processResult.stderr.strip()
        raise RuntimeError(f"Docker run failed: {sError}")
    return processResult.stdout.strip()


def _flistAssembleRunCommand(config, saRunArgs, saCommand):
    """Combine docker run prefix, args, image tag, and user command."""
    return _flistAssembleDockerCommand("run", config, saRunArgs, saCommand)


def _flistAssembleDockerCommand(sSubcommand, config, saRunArgs, saCommand):
    """Combine a docker subcommand, its args, the image tag, and a command."""
    sImageTag = f"{config.sProjectName}:latest"
    saFullCommand = ["docker", sSubcommand] + saRunArgs + [sImageTag]
    if saCommand is not None:
        saFullCommand.extend(saCommand)
    return saFullCommand


def flistBuildRunArgs(config, bDetached=False, bCreateOnly=False):
    """Build list of docker run arguments from project config.

    ``bCreateOnly`` builds the argument list for ``docker create``,
    which accepts no ``-d`` (there is nothing to detach from yet) and
    must not carry ``--rm``: a created-but-unstarted container that
    removed itself would defeat the whole point of recording its id.
    """
    saRunArgs = _flistBuildProcessModeArgs(bDetached, bCreateOnly)
    # --init puts Docker's tini in front of the sleep-infinity
    # keepalive as a reaping PID 1. Without it every orphaned child
    # that dies — a terminal shell's agent, a detached script — stays
    # a zombie forever, and one permanent zombie in a journaled
    # process group made a terminal quarantine unclearable short of a
    # container restart (2026-08-14). Reaping is the only thing tini
    # adds; it grants no capability and changes no isolation.
    saRunArgs.append("--init")
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
    _fnAddAgentUpdateEnvs(config, saRunArgs)
    _fnAddAgentHostBridge(config, saRunArgs)
    _fnAddNetworkIsolation(config, saRunArgs)
    saRunArgs.extend(flistConfigureX11Args())
    return saRunArgs


def _flistBuildProcessModeArgs(bDetached, bCreateOnly):
    """Return the leading run/create flags for one launch mode."""
    if bCreateOnly:
        return ["-t"]
    if bDetached:
        return ["-d", "-t"]
    return ["--rm", "-it"]


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


def _fnAddAgentUpdateEnvs(config, saRunArgs):
    """Pass every enabled agent's auto-update preference into the container."""
    features = config.features
    for sAgent, sFeature, sAutoUpdate in (
        ("CLAUDE", "bClaude", "bClaudeAutoUpdate"),
        ("CODEX", "bCodex", "bCodexAutoUpdate"),
        ("GEMINI", "bGemini", "bGeminiAutoUpdate"),
        ("ANTIGRAVITY", "bAntigravity", "bAntigravityAutoUpdate"),
        ("OPENCODE", "bOpenCode", "bOpenCodeAutoUpdate"),
        ("CLINE", "bCline", "bClineAutoUpdate"),
        ("OPENHANDS", "bOpenHands", "bOpenHandsAutoUpdate"),
        ("PI", "bPi", "bPiAutoUpdate"),
    ):
        if not getattr(features, sFeature, False):
            continue
        sFlag = "true" if getattr(features, sAutoUpdate, True) else "false"
        saRunArgs.extend(["-e", f"VAIBIFY_{sAgent}_AUTO_UPDATE={sFlag}"])


def _fnAddNetworkIsolation(config, saRunArgs):
    """Add network isolation flag if enabled in config."""
    if config.bNetworkIsolation:
        saRunArgs.extend([
            "--network", "none",
            "-e", "VAIBIFY_NETWORK_ISOLATED=true",
        ])


def _fnAddAgentHostBridge(config, saRunArgs):
    """Resolve ``host.docker.internal`` only when the agent needs it.

    The hosts-file entry is only useful for the in-container
    ``vaibify-do`` agent calling back to the host backend. Adding it
    unconditionally widens the container's egress surface for projects
    that never run an agent. Emit ``--add-host`` only when an agent is
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
    return any(
        getattr(features, sFeature, False)
        for sFeature in (
            "bClaude", "bCodex", "bGemini", "bOpenCode", "bCline",
            "bOpenHands", "bPi",
        )
    )


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
    processResult = subprocess.run(
        ["docker", "stop", sProjectName],
        capture_output=True, text=True,
    )
    if processResult.returncode != 0:
        raise RuntimeError(
            f"docker stop failed: "
            f"{processResult.stderr.strip()}"
        )
    fnRemoveStopped(sProjectName)


def fdictProbeContainerPresence(sProjectName):
    """Return ``{bAnswered, bPresent}`` for a container NAME.

    ``bAnswered`` False means the daemon did not answer at all (absent
    CLI, timeout, error) — which is NOT the same as "no such container"
    and must never be read as one. ``fdictGetContainerStatus`` conflates
    the two into ``bExists=False``, which is safe for a display surface
    and unsafe for anything that clears a quarantine.
    """
    bAnswered, sOutput = _ftRunProbeCommand([
        "docker", "ps", "-a", "-q",
        "--filter", f"name=^{sProjectName}$",
    ])
    return {"bAnswered": bAnswered, "bPresent": bool(sOutput.strip())}


def fbStopContainerProvenSettled(sProjectName):
    """Stop the container and return True only when that is PROVEN.

    Two outcomes are settlements: the stop succeeded, or the daemon
    positively answered that no container by that name exists (nothing
    to stop). Everything else — a failed stop, an unreachable daemon, a
    probe that did not answer — is False, and a caller that clears
    quarantine state on the strength of this must refuse.
    """
    try:
        fnStopContainer(sProjectName)
        return True
    except Exception:
        pass
    dictPresence = fdictProbeContainerPresence(sProjectName)
    return dictPresence["bAnswered"] and not dictPresence["bPresent"]


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
    processResult = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", sProjectName],
        capture_output=True,
        text=True,
    )
    return processResult.stdout.strip() == "true"


def fdictGetContainerStatus(sProjectName):
    """Return status dict with keys: bExists, bRunning, sStatus."""
    sRawStatus = _fsInspectContainerState(sProjectName)
    return _fdictParseContainerState(sRawStatus)


def _fsInspectContainerState(sProjectName):
    """Query docker inspect for the container state, or empty string."""
    processResult = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", sProjectName],
        capture_output=True,
        text=True,
    )
    if processResult.returncode != 0:
        return ""
    return processResult.stdout.strip()


def _fdictParseContainerState(sRawStatus):
    """Parse raw status string into a structured status dict."""
    bExists = len(sRawStatus) > 0
    sStatus = sRawStatus if bExists else "not found"
    bRunning = sStatus == "running"
    return {"bExists": bExists, "bRunning": bRunning, "sStatus": sStatus}


def ftProbeNetworkIsolation(sContainerIdentifier):
    """Return ``(bAnswered, bIsolated)`` for the container's NetworkMode.

    ``bAnswered`` is False when ``docker inspect`` could not answer at
    all — the container was just stopped or removed, the docker CLI is
    absent, or the call timed out. Callers that need a *decision* want
    :func:`fbContainerIsNetworkIsolated`, which collapses this to a
    fail-open boolean. Callers that record isolation as *evidence*
    (the AI-provenance stamp) must keep the two apart: writing
    "not isolated" when the truth was unavailable turns an unknown
    into an asserted fact inside an attestation.
    """
    try:
        processResult = subprocess.run(
            [
                "docker", "inspect", "-f",
                "{{.HostConfig.NetworkMode}}", sContainerIdentifier,
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (False, False)
    if processResult.returncode != 0:
        return (False, False)
    return (True, processResult.stdout.strip() == "none")


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
    bAnswered, bIsolated = ftProbeNetworkIsolation(sContainerIdentifier)
    return bAnswered and bIsolated


_fnRunDockerCommand = fnRunDockerCommand
