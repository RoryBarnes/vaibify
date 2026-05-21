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


def _fnRejectDeniedPrefix(sResolved):
    """Reject paths matching the absolute or home-relative denylist."""
    sHome = os.path.expanduser("~")
    for sDenied in _LIST_DENY_PREFIXES:
        if sResolved == sDenied or sResolved.startswith(sDenied + os.sep):
            raise BindMountValidationError(
                f"bindMounts host path '{sResolved}' is a denied location"
            )
    for sRelDenied in _LIST_HOME_RELATIVE_DENY_PREFIXES:
        sFullDenied = posixpath.join(sHome, sRelDenied)
        if sResolved == sFullDenied or sResolved.startswith(
            sFullDenied + os.sep
        ):
            raise BindMountValidationError(
                f"bindMounts host path '{sResolved}' is a denied location"
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
