"""Validate ``bindMounts:`` host paths against an allowlist.

A bind mount lets a host path appear inside the container at a chosen
path. Any value that escapes the user's home directory, hits a known
sensitive host path (Docker socket, /etc, /root, ssh/aws/gh config
dirs), or contains ``..`` segments is rejected before docker run sees
it. Audit finding H2.

**The daemon socket is the one that ends the security model.** A
container holding it controls the host Docker daemon, and from there
the host (``docker run -v /:/host``) -- so the unprivileged container
user, the network isolation and the mutation boundary all stop
mattering at once. Denying the literal string ``/var/run/docker.sock``
did not achieve this: on a Colima, Rancher or rootless install the live
endpoint sits INSIDE the user's home directory, where the general home
allow-list admitted it. Verified before this fix, with a live socket:
``~/.colima/default/docker.sock -> /var/run/docker.sock`` was accepted.

So the denial is by RESOLUTION and by FILE TYPE, never by spelling:
every endpoint the Docker configuration names, plus any path that IS a
Unix socket. A name-matched list cannot keep up with the next runtime,
and this is not a boundary to be caught up with later.
"""

import json
import os
import posixpath
import stat


__all__ = [
    "fnValidateBindMount",
    "fnValidateBindMountList",
    "flistConfiguredDockerEndpoints",
    "BindMountValidationError",
]


_LIST_DENY_PREFIXES = (
    "/var/run/docker.sock",
    "/etc",
    "/root",
)


_LIST_HOME_RELATIVE_DENY_PREFIXES = (
    ".ssh",
    ".aws",
    ".config/gh",
    ".gnupg",
    ".docker",
    ".kube",
    # The operation-journal quarantine markers (design §8): a same-UID
    # agent inside a container that could mount this directory could
    # delete a quarantine marker and un-quarantine a container whose
    # past operations were never proven settled.
    ".vaibify/journal",
    # The host-control Unix sockets (design §6b/§14): the control plane
    # is host-only by construction — a container that could mount this
    # directory would reach the same-UID peer-authenticated socket and
    # drive reconcile/force-abandon/break-glass from inside.
    ".vaibify/control",
)


# Where Docker keeps the endpoint of every context it knows about. Read
# as FILES, deliberately: asking the `docker` CLI would give this
# validator a process-creation capability, and the config directory is
# the same source the CLI itself reads.
_S_DOCKER_CONFIG_DIRECTORY = ".docker"
_S_DOCKER_CONTEXT_META = "contexts/meta"
_S_UNIX_SCHEME = "unix://"


class BindMountValidationError(ValueError):
    """Raised when a vaibify.yml ``bindMounts`` entry is unsafe."""


def fnValidateBindMount(dictMount, sProjectRepoPath=None):
    """Raise :class:`BindMountValidationError` if the mount is unsafe."""
    sRaw = dictMount.get("host")
    if not isinstance(sRaw, str) or not sRaw:
        raise BindMountValidationError(
            "bindMounts entry missing 'host' string"
        )
    if ".." in sRaw.split(os.sep):
        raise BindMountValidationError(
            f"bindMounts host path '{sRaw}' contains '..'"
        )
    sResolved = _fsResolveSymlinks(sRaw)
    _fnRejectDeniedPrefix(sResolved)
    _fnRejectDaemonSocket(sResolved)
    _fnRequireWithinAllowedRoot(sResolved, sProjectRepoPath)
    _fnValidateContainerTarget(dictMount.get("container"))


def _fnRejectDaemonSocket(sResolved):
    """Reject the daemon endpoint, anything containing it, and any socket.

    Three layers, because each covers what the others cannot:

    1. **Every endpoint the Docker configuration names**, resolved from
       ``DOCKER_HOST`` and from every context Docker has on disk --
       not only the current one, since which context is current is a
       runtime detail and denying an inactive endpoint costs nothing.
       Overlap is bidirectional (:func:`_fbPathsOverlap`), so a mount of
       ``~/.colima`` is refused for containing ``~/.colima/default/
       docker.sock`` exactly as the socket itself is.
    2. **Any path that IS a Unix socket.** This is the fail-closed
       layer: it needs no list and no name, so a runtime nobody
       anticipated is covered on the day it ships. Mounting a socket
       into a workflow container is not something this tool needs to
       support, and a clear refusal is the right answer if it ever does.
    3. What layer 1 read from disk is only as good as the config being
       present -- hence layer 2 as the backstop.

    **The residual, stated rather than implied.** A TCP endpoint
    (``DOCKER_HOST=tcp://...``) has no path to deny, so no bind-mount
    rule can fence it; that is the container's network isolation to
    answer, not this validator's. And a mount of a directory that will
    LATER contain a socket cannot be seen at validation time.
    """
    for sEndpoint in _flistConfiguredDockerEndpoints():
        if _fbPathsOverlap(sResolved, sEndpoint):
            raise BindMountValidationError(
                f"bindMounts host path '{sResolved}' overlaps the Docker "
                f"daemon endpoint '{sEndpoint}'; a container holding the "
                f"daemon socket controls the host"
            )
    if _fbIsUnixSocket(sResolved):
        raise BindMountValidationError(
            f"bindMounts host path '{sResolved}' is a Unix socket; "
            f"sockets are never mounted into a workflow container"
        )


def _fbIsUnixSocket(sPath):
    """True when the path exists and is a Unix domain socket."""
    try:
        return stat.S_ISSOCK(os.stat(sPath).st_mode)
    except OSError:
        return False


def flistConfiguredDockerEndpoints():
    """Return every Unix Docker endpoint this host has configured."""
    return _flistConfiguredDockerEndpoints()


def _flistConfiguredDockerEndpoints():
    """Return resolved Unix socket paths for every known Docker endpoint."""
    listEndpoints = []
    sFromEnvironment = _fsUnixPathFromEndpoint(os.environ.get("DOCKER_HOST"))
    if sFromEnvironment:
        listEndpoints.append(sFromEnvironment)
    listEndpoints.extend(_flistContextEndpoints())
    return sorted(set(listEndpoints))


def _fsUnixPathFromEndpoint(sEndpoint):
    """Return the resolved socket path of a ``unix://`` endpoint, or ''.

    A TCP or npipe endpoint yields '' -- there is no path to deny, and
    saying so here keeps the caller from treating absence as safety.
    """
    if not isinstance(sEndpoint, str) or not sEndpoint.startswith(
        _S_UNIX_SCHEME,
    ):
        return ""
    sPath = sEndpoint[len(_S_UNIX_SCHEME):]
    return _fsResolveSymlinks(sPath) if sPath else ""


def _flistContextEndpoints():
    """Return the endpoints of every Docker context stored on disk."""
    sMetaRoot = posixpath.join(
        os.path.realpath(os.path.expanduser("~")),
        _S_DOCKER_CONFIG_DIRECTORY, _S_DOCKER_CONTEXT_META,
    )
    listEndpoints = []
    try:
        listContextDirectories = sorted(os.listdir(sMetaRoot))
    except OSError:
        return listEndpoints
    for sContextDirectory in listContextDirectories:
        sEndpoint = _fsEndpointFromContextMeta(
            posixpath.join(sMetaRoot, sContextDirectory, "meta.json"),
        )
        if sEndpoint:
            listEndpoints.append(sEndpoint)
    return listEndpoints


def _fsEndpointFromContextMeta(sMetaPath):
    """Return one context file's Docker endpoint path, or ''."""
    try:
        with open(sMetaPath, "r", encoding="utf-8") as fileMeta:
            dictMeta = json.load(fileMeta)
    except (OSError, ValueError):
        return ""
    dictEndpoints = dictMeta.get("Endpoints")
    if not isinstance(dictEndpoints, dict):
        return ""
    dictDocker = dictEndpoints.get("docker")
    if not isinstance(dictDocker, dict):
        return ""
    return _fsUnixPathFromEndpoint(dictDocker.get("Host"))


def _fnValidateContainerTarget(sTarget):
    """Reject a container-side mount target that is unset or traversing.

    The target is the path the host directory appears at inside the
    container. It must be an absolute POSIX path with no ``..`` segment
    so a mount cannot be aimed at a computed location outside its
    intended tree. The host side is the credential-exposure boundary;
    this is the lighter check that the destination is well-formed.
    """
    if not isinstance(sTarget, str) or not sTarget:
        raise BindMountValidationError(
            "bindMounts entry missing 'container' string"
        )
    if not posixpath.isabs(sTarget):
        raise BindMountValidationError(
            f"bindMounts container path '{sTarget}' must be absolute"
        )
    if ".." in sTarget.split("/"):
        raise BindMountValidationError(
            f"bindMounts container path '{sTarget}' contains '..'"
        )


def fnValidateBindMountList(listMounts, sProjectRepoPath=None):
    """Apply :func:`fnValidateBindMount` to every entry in the list."""
    for dictMount in listMounts:
        fnValidateBindMount(dictMount, sProjectRepoPath)


def _fsResolveSymlinks(sPath):
    """Resolve symlinks and ``~`` so the denylist matches the real target."""
    sExpanded = os.path.expanduser(sPath)
    try:
        return os.path.realpath(sExpanded)
    except OSError:
        return os.path.abspath(sExpanded)


def _fbPathsOverlap(sFirst, sSecond):
    """True when either path is the other, or an ancestor of the other.

    Mounting a directory grants access to everything beneath it, so a
    denied path is exposed not only when the mount IS it or sits under
    it, but equally when the mount is an ANCESTOR of it — mounting
    ``$HOME`` hands over ``~/.ssh`` just as directly as naming
    ``~/.ssh``. The original denylist checked only the descendant
    direction, so mounting the parent of every protected directory
    bypassed it. This checks both directions.
    """
    if sFirst == sSecond:
        return True
    return (
        sFirst.startswith(sSecond + os.sep)
        or sSecond.startswith(sFirst + os.sep)
    )


def _fnRejectDeniedPrefix(sResolved):
    """Reject a mount that overlaps any denied location in either direction.

    Compares against the symlink-resolved $HOME so a system whose home
    directory is itself a symlink (macOS often resolves ``/Users/foo``
    through ``/private/...``) cannot bypass the home-relative denylist
    by submitting the un-resolved form. Overlap is bidirectional: the
    mount is rejected when it is, contains, or is contained by a denied
    path (see :func:`_fbPathsOverlap`).
    """
    sHome = os.path.realpath(os.path.expanduser("~"))
    listDenied = list(_LIST_DENY_PREFIXES) + [
        posixpath.join(sHome, sRelDenied)
        for sRelDenied in _LIST_HOME_RELATIVE_DENY_PREFIXES
    ]
    for sDenied in listDenied:
        if _fbPathsOverlap(sResolved, sDenied):
            raise BindMountValidationError(
                f"bindMounts host path '{sResolved}' overlaps the denied "
                f"location '{sDenied}'"
            )


def _fnRequireWithinAllowedRoot(sResolved, sProjectRepoPath):
    """Allow only paths under $HOME or the user's project repo."""
    sHome = os.path.realpath(os.path.expanduser("~"))
    if sResolved == sHome or sResolved.startswith(sHome + os.sep):
        return
    if sProjectRepoPath:
        sRepo = os.path.realpath(sProjectRepoPath)
        if sResolved == sRepo or sResolved.startswith(sRepo + os.sep):
            return
    raise BindMountValidationError(
        f"bindMounts host path '{sResolved}' is outside the user's "
        "home directory and the project repo"
    )
