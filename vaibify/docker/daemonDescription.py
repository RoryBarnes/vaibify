"""The daemon's architecture beside an envelope's requirement.

Three facts, kept apart by name: the platform an envelope REQUIRES,
the architecture this DAEMON has, and whether the second matches the
first -- which decides whether an emulation checkbox is offered. A
daemon that cannot be asked is reported as unknown, never as a match.

Beside ``daemonCapacity`` rather than in a route module, because two
routes need the same answer -- the "+" card that reproduces a published
project, and the wizard page that offers to containerize from the
author's pinned image -- and a route importing another route is
coupling nobody can audit.
"""

__all__ = ["fdictDescribeDaemonForPlatform"]

import logging

from vaibify.config.connectionAvailability import fbDockerReachable
from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from vaibify.docker import daemonCapacity


logger = logging.getLogger(__name__)


def fdictDescribeDaemonForPlatform(connectionDocker, sRequiredPlatform):
    """Return ``{bReachable, sArchitecture, sRequiredPlatform, bArchitectureMatches}``."""
    from vaibify.reproducibility.imageAcquisition import (
        fsArchitectureOfPlatform,
    )
    sRequiredPlatform = str(sRequiredPlatform or "")
    bReachable = fbDockerReachable(connectionDocker)
    sDaemonArchitecture = (
        _fsReadDaemonArchitectureQuietly() if bReachable else ""
    )
    return {
        "bReachable": bReachable,
        "sArchitecture": sDaemonArchitecture,
        "sRequiredPlatform": sRequiredPlatform,
        "bArchitectureMatches": bool(
            sDaemonArchitecture
            and sDaemonArchitecture
            == fsArchitectureOfPlatform(sRequiredPlatform)
        ),
    }


def _fsReadDaemonArchitectureQuietly():
    """Return the daemon's architecture, or empty when it cannot be asked."""
    from vaibify.docker import disposableContainer
    try:
        return str(daemonCapacity.fsReadDaemonArchitecture(
            disposableContainer.fdockerCreateDisposableClient(),
        ) or "")
    except Exception as error:  # noqa: BLE001 - reported as unknown, never guessed
        fnReRaiseControlPlaneRefusal(error)
        logger.info("daemon architecture unavailable: %s", error)
        return ""
