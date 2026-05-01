"""Docker integration utilities."""

import subprocess
import sys


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
