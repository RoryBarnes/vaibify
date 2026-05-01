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


def fnRunDockerCommand(saCommand):
    """Execute a docker command, raising RuntimeError on failure."""
    resultProcess = subprocess.run(
        saCommand,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if resultProcess.returncode != 0:
        sCommandStr = " ".join(saCommand)
        raise RuntimeError(
            f"Docker command failed "
            f"(exit {resultProcess.returncode}): "
            f"{sCommandStr}"
        )


def fbDockerDaemonReachable():
    """Return True if the Docker daemon responds to ``docker info``."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return resultProcess.returncode == 0
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
