"""Doctor check: the running container's bind-mount modes match vaibify.yml.

The sentences come from :mod:`vaibify.docker.bindMountDrift`, the same
module the dashboard's readiness banner renders, so the terminal and
the browser say one thing about a container whose mounts predate an
edit to the file.
"""

from vaibify.docker.bindMountDrift import (
    S_RECREATE_COMMAND, flistDescribeBindMountDrift,
)

from .preflightResult import (
    S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN, S_SCOPE_CONTAINER,
    PreflightResult,
)

__all__ = ["flistCheckBindMountModes"]

S_BIND_MOUNT_CHECK_NAME = "bind-mount-modes"


def flistCheckBindMountModes(config, jsonInspect):
    """Say whether the container's bind mounts are the ones the file declares."""
    listMounts = list(getattr(config, "listBindMounts", None) or [])
    if not jsonInspect:
        return [PreflightResult(
            sName=S_BIND_MOUNT_CHECK_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_CONTAINER,
            sMessage="The container could not be inspected.",
        )]
    listLines = flistDescribeBindMountDrift(listMounts, jsonInspect)
    if not listLines:
        return [PreflightResult(
            sName=S_BIND_MOUNT_CHECK_NAME, sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_CONTAINER,
            sMessage=(
                "the container's bind mounts have the modes vaibify.yml "
                "declares" if listMounts else "vaibify.yml declares no "
                "bind mounts"
            ),
        )]
    return [
        PreflightResult(
            sName=S_BIND_MOUNT_CHECK_NAME, sLevel=S_LEVEL_WARN,
            sScope=S_SCOPE_CONTAINER, sMessage=sLine,
            sRemediation="Create the container again.",
            sCommand=S_RECREATE_COMMAND,
        )
        for sLine in listLines
    ]
