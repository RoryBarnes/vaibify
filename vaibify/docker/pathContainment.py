"""Pure, component-wise tests of whether a path stays inside its root.

Every caller used to ask this with a string-prefix test, and a prefix is
the wrong unit: ``startswith("..")`` refuses the legitimate name
``..data`` while ``startswith("/etc")`` admits ``/etcetera``. A path is a
sequence of components, so the answer is read from components.

No filesystem is consulted. These answer for the spelling alone, which is
what a name read out of a container-writable index, sidecar or archive
header has before anything is opened.
"""

import posixpath

__all__ = [
    "fbIsPlainRelativePath",
    "fbIsPlainDirectoryName",
    "fbNormalizedPathEscapesTheRoot",
]


def fbIsPlainRelativePath(sPath):
    """True for a relative, slash-separated path of ordinary components.

    Refuses an absolute path and any empty, ``.``, ``..`` or NUL
    component. Used on names that arrive from a place the container can
    write (a git index key, a sidecar entry), where the value is later
    joined onto a root and handed to the daemon.
    """
    if not isinstance(sPath, str) or not sPath:
        return False
    if sPath.startswith("/") or "\x00" in sPath:
        return False
    return all(
        sComponent not in ("", ".", "..") for sComponent in sPath.split("/")
    )


def fbIsPlainDirectoryName(sName):
    """True for one ordinary path component: a directory's own name."""
    return fbIsPlainRelativePath(sName) and "/" not in sName


def fbNormalizedPathEscapesTheRoot(sPath):
    """True when a path, once normalized, is absolute or climbs above its root.

    For names that legitimately carry ``./`` or ``a/../b`` spellings,
    such as tar members: those normalize to a contained path and pass.
    The test is on the FIRST COMPONENT being exactly ``..``; a name that
    merely starts with two dots (``..data``) stays inside.
    """
    sNormalized = posixpath.normpath(sPath)
    return posixpath.isabs(sNormalized) or sNormalized.split("/")[0] == ".."
