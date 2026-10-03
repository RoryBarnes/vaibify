"""Build the link that attaches VS Code to a running container.

The Dev Containers extension registers a URI handler that understands
only ``/cloneInVolume``; a ``vscode://ms-vscode-remote.remote-containers/
attach?...`` link reaches it and is ignored without a word. What does
work is VS Code's own ``vscode://vscode-remote/<authority><path>`` form,
which opens ``vscode-remote://<authority><path>``. For an attached
container the authority is ``attached-container+`` followed by the hex
of a JSON object naming the container and, when the daemon is not at the
default socket, the endpoint to ask. Without that endpoint the extension
runs ``docker`` with ``DOCKER_CONTEXT=default`` and cannot see a
container on a daemon reached through a named context or a socket
elsewhere, which is how a Colima user's attach fails.
"""

import json
from urllib.parse import quote

__all__ = ["fsBuildAttachUri"]

S_DEFAULT_DOCKER_SOCKET = "unix:///var/run/docker.sock"


def fsBuildAttachUri(sContainerId, sWorkspaceRoot, sDockerHost=""):
    """Return the vscode:// link that opens a running container.

    ``sContainerId`` is the container's id, not its name: it is the key
    the dashboard is already authorized for. ``sDockerHost`` is omitted
    from the link when it is empty or the default socket, so a default
    installation carries no extra setting.
    """
    dictAuthority = {"containerId": sContainerId}
    if sDockerHost and sDockerHost != S_DEFAULT_DOCKER_SOCKET:
        dictAuthority["settings"] = {"host": sDockerHost}
    sHex = json.dumps(
        dictAuthority, separators=(",", ":"),
    ).encode("utf-8").hex()
    return "vscode://vscode-remote/attached-container+" + sHex + quote(
        sWorkspaceRoot, safe="/",
    )
