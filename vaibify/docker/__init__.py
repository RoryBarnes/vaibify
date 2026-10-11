"""Docker integration utilities."""

import socket
import subprocess
import sys


__all__ = [
    "fnRunDockerCommand",
    "fbDockerDaemonReachable",
    "fbImageExists",
    "fbForwardedHostPortFree",
]


def fnRunDockerCommand(saCommand, bCaptureOutput=False):
    """Execute a docker command, raising RuntimeError on failure.

    The output streams to the caller's terminal by default, which a
    foreground ``docker run`` needs. ``bCaptureOutput`` keeps it off
    the terminal for a command whose only output is an echo of its
    argument (``docker volume rm`` printed bare volume names into the
    hub's terminal mid-deletion), and puts Docker's own reason into
    the error instead.
    """
    dictStreams = (
        {"capture_output": True, "encoding": "utf-8", "errors": "replace"}
        if bCaptureOutput else {"stdout": sys.stdout, "stderr": sys.stderr}
    )
    processResult = subprocess.run(saCommand, **dictStreams)
    if processResult.returncode != 0:
        sCommandStr = " ".join(saCommand)
        sReason = (processResult.stderr or "").strip() if bCaptureOutput else ""
        raise RuntimeError(
            f"Docker command failed "
            f"(exit {processResult.returncode}): "
            f"{sCommandStr}" + (f": {sReason}" if sReason else "")
        )


def fbDockerDaemonReachable():
    """Return True if the Docker daemon responds to ``docker info``."""
    try:
        processResult = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return processResult.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def fbForwardedHostPortFree(iPort):
    """True iff the given TCP host port can be bound on localhost.

    Mirrors the docker run -p check — if a port is already in use,
    docker run fails with an opaque "ports are not available"
    error. Pre-checking here lets the CLI emit a clear message.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", iPort))
        return True
    except OSError:
        return False


# Late re-export: ``imageBuilder`` imports ``fnRunDockerCommand`` from
# this package, so the import must come after the function is defined.
from .imageBuilder import fbImageExists  # noqa: E402
