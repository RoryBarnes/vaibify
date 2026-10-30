"""Whether a running container's bind mounts have the modes vaibify.yml declares.

A bind mount's mode is fixed when the container is created, so turning
``readWrite`` on or off in ``vaibify.yml`` changes nothing until the
container is created again -- the same shape as the X11 "created
without" finding. The dashboard's readiness banner and ``vaibify
doctor`` both render the sentences this module builds, from ``docker
inspect``'s ``Mounts`` list (``Destination`` and ``RW``), so the two
surfaces can never disagree about what the container actually mounts.

An empty inspect payload means the daemon did not answer: nothing is
claimed, in either direction.
"""

__all__ = [
    "S_RECREATE_COMMAND",
    "flistDescribeBindMountDrift",
]

from vaibify.config.bindMountValidator import fbBindMountIsReadWrite

S_RECREATE_COMMAND = "vaibify restart"


def _fdictActualBindModes(jsonInspect):
    """Return ``{container path: bRW}`` for every bind mount the container has."""
    return {
        dictMount.get("Destination"): bool(dictMount.get("RW"))
        for dictMount in jsonInspect.get("Mounts") or []
        if dictMount.get("Type") == "bind"
    }


def _fsModeWord(bReadWrite):
    return "read-write" if bReadWrite else "read-only"


def flistDescribeBindMountDrift(listBindMounts, jsonInspect):
    """Return one sentence per declared mount the container disagrees with.

    A declared mount the container does not have, or has in the other
    mode, each get a sentence naming both sides and the one action that
    fixes it. A container that matches, or that could not be inspected,
    yields nothing.
    """
    if not jsonInspect:
        return []
    dictActual = _fdictActualBindModes(jsonInspect)
    listLines = []
    for dictMount in listBindMounts or []:
        sTarget = dictMount.get("container", "")
        bWanted = fbBindMountIsReadWrite(dictMount)
        if sTarget not in dictActual:
            listLines.append(
                f"This project declares a bind mount at {sTarget}, but this "
                f"container was created without it. Create the container "
                f"again to add it. Run `{S_RECREATE_COMMAND}`."
            )
        elif dictActual[sTarget] != bWanted:
            listLines.append(
                f"This project declares the bind mount at {sTarget} as "
                f"{_fsModeWord(bWanted)}, but this container mounts it "
                f"{_fsModeWord(dictActual[sTarget])}. Create the container "
                f"again to apply the declared mode. Run `{S_RECREATE_COMMAND}`."
            )
    return listLines
