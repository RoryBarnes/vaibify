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
