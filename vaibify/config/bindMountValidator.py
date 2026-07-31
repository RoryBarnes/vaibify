"""Validate ``bindMounts:`` host paths against an allowlist.

A bind mount lets a host path appear inside the container at a chosen
path. Any value that escapes the user's home directory, hits a known
sensitive host path (Docker socket, /etc, /root, ssh/aws/gh config
dirs), or contains ``..`` segments is rejected before docker run sees
it. Audit finding H2.
"""

import os
import posixpath


__all__ = [
    "fnValidateBindMount",
    "fnValidateBindMountList",
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
)


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
    _fnRequireWithinAllowedRoot(sResolved, sProjectRepoPath)
    _fnValidateContainerTarget(dictMount.get("container"))


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
