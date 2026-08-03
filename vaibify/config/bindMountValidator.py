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
every endpoint the Docker configuration names -- from ``DOCKER_HOST``
and from the context tree, wherever ``DOCKER_CONFIG`` puts it -- plus
any path that IS a socket or CONTAINS one. A name-matched list cannot
keep up with the next runtime, and this is not a boundary to be caught
up with later.

**What this file cannot do.** Three residuals, and they need three
DIFFERENT controls -- an earlier draft of this docstring filed all of
them under one, which would have left two of them unowned:

1. *A ``tcp://`` daemon.* There is no path to deny, so no bind-mount
   rule touches it. It is answered by network isolation or an explicit
   reachability policy, not by mount inspection.
2. *A socket created after validation.* Inspecting a running
   container's mounts narrows the window but does not close it: a
   socket appearing after that assertion is a TOCTOU residual that
   only an in-container control (or refusing directory mounts
   entirely) removes.
3. *Unix-socket exposure at the moment of inspection.* This is the one
   a running-container mount assertion actually answers, and it is
   still owed.
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

# How many directory entries a mount may hold before the socket scan
# gives up. Generous, because a researcher's data directory is the
# ordinary case; bounded, because an unbounded walk of $HOME at
# validation time is its own problem. Exceeding it REFUSES.
_I_SOCKET_SCAN_BUDGET = 200_000


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
    _fnRejectContainedSocket(sResolved)


def _fbIsUnixSocket(sPath):
    """True when the path is a Unix domain socket; raise if unknowable.

    An absent path is honestly not a socket -- the other layers decide
    whether absence is acceptable. Anything else (a permission error, a
    vanished entry, an I/O failure) means the type could not be
    determined, and returning False there would have admitted a mount
    on the strength of a failed check. That is precisely the shape of
    fail-open this module exists to avoid, so it refuses instead.
    """
    try:
        return stat.S_ISSOCK(os.stat(sPath).st_mode)
    except FileNotFoundError:
        return False
    except OSError as errorStat:
        raise BindMountValidationError(
            f"bindMounts host path '{sPath}' could not be inspected "
            f"({errorStat.strerror}), so it cannot be shown not to be a "
            f"daemon socket"
        ) from errorStat


def _fnRejectContainedSocket(sResolved):
    """Reject a directory that CONTAINS a socket, not merely one that is one.

    Mounting a directory grants everything beneath it, so checking only
    the mount source itself was the ancestor-direction hole the deny
    list had already been fixed for once: the configured endpoint's
    parent was refused by layer 1, but a parent holding an
    *unconfigured* socket was accepted. Verified before this check
    existed.

    Fail-closed on EVERY inability to prove, not merely on size. Too
    large, unreadable, untypeable, vanished mid-scan -- each refuses.
    The first version of this walk skipped an unreadable directory and
    a failed ``stat`` silently, so a permission error or a race
    admitted a mount the scan had not actually inspected: a check that
    fails open is worse than no check, because the docstring above it
    says the tree was proven clean. The remedy for a refusal is to name
    a narrower path, which is better practice for a bind mount anyway.

    Measured cost, so the budget is a judgement and not a guess: this
    repository (~5k entries) scans in 0.18s; a source tree that exceeds
    the budget takes 5.6s to reach it and is then refused. A researcher
    mounting a dataset directory pays well under a second; someone
    mounting their whole home directory waits, and is told to be more
    specific.

    **The residual, which no host-side check can close.** A socket
    created after validation is not visible here, and a running-
    container assertion narrows that window without closing it. See the
    module docstring, which keeps the three residuals apart because
    they need three different controls.
    """
    if not os.path.isdir(sResolved):
        return
    iExamined = 0
    listPending = [sResolved]
    while listPending:
        sDirectory = listPending.pop()
        try:
            listEntries = list(os.scandir(sDirectory))
        except OSError as errorScan:
            raise BindMountValidationError(
                f"bindMounts host path '{sResolved}' contains "
                f"'{sDirectory}', which could not be read "
                f"({errorScan.strerror}), so the tree cannot be shown to "
                f"be free of daemon sockets"
            ) from errorScan
        for entryChild in listEntries:
            iExamined += 1
            if iExamined > _I_SOCKET_SCAN_BUDGET:
                raise BindMountValidationError(
                    f"bindMounts host path '{sResolved}' holds more than "
                    f"{_I_SOCKET_SCAN_BUDGET} entries, so it cannot be "
                    f"shown to be free of daemon sockets; mount a "
                    f"narrower path"
                )
            if _fbEntryIsSymlink(entryChild, sResolved):
                continue
            if _fbIsUnixSocket(entryChild.path):
                raise BindMountValidationError(
                    f"bindMounts host path '{sResolved}' contains the "
                    f"Unix socket '{entryChild.path}'; mounting the "
                    f"directory would grant it"
                )
            if _fbEntryIsDirectory(entryChild, sResolved):
                listPending.append(entryChild.path)


def _fbEntryIsSymlink(entryChild, sMountRoot):
    """True when a directory entry is a symlink; raise if unknowable."""
    try:
        return entryChild.is_symlink()
    except OSError as errorType:
        raise BindMountValidationError(
            f"bindMounts host path '{sMountRoot}' contains "
            f"'{entryChild.path}', whose type could not be read "
            f"({errorType.strerror}), so the tree cannot be shown to be "
            f"free of daemon sockets"
        ) from errorType


def _fbEntryIsDirectory(entryChild, sMountRoot):
    """True when a directory entry is a directory; raise if unknowable.

    An entry that vanished mid-scan refuses rather than passes. The
    honest reading of a disappearance during validation is that the
    tree changed while being inspected, and a scan of a moving target
    proves nothing about the tree that will actually be mounted.
    """
    try:
        return entryChild.is_dir(follow_symlinks=False)
    except OSError as errorType:
        raise BindMountValidationError(
            f"bindMounts host path '{sMountRoot}' contains "
            f"'{entryChild.path}', whose type could not be read "
            f"({errorType.strerror}), so the tree cannot be shown to be "
            f"free of daemon sockets"
        ) from errorType


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


def _fsDockerConfigRoot():
    """Return the Docker configuration directory, honouring DOCKER_CONFIG.

    Docker lets the whole configuration tree move, so reading only
    ``~/.docker`` misses every endpoint of a relocated install --
    verified: with ``DOCKER_CONFIG`` set, a context naming a socket
    under ``$HOME`` was invisible here and its parent directory was
    accepted as a mount.
    """
    sConfigured = os.environ.get("DOCKER_CONFIG")
    if sConfigured:
        return os.path.realpath(os.path.expanduser(sConfigured))
    return posixpath.join(
        os.path.realpath(os.path.expanduser("~")),
        _S_DOCKER_CONFIG_DIRECTORY,
    )


def _flistContextEndpoints():
    """Return the endpoints of every Docker context stored on disk."""
    sMetaRoot = posixpath.join(
        _fsDockerConfigRoot(), _S_DOCKER_CONTEXT_META,
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
