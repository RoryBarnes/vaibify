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
    "fsDescribeMemberEscape",
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


def fsDescribeMemberEscape(infoMember):
    """Return how a tar member escapes the extraction root, or an empty string.

    The ONE judgement both archive repackers make. A member's own name
    is normalized and must stay inside. A link's target is read the way
    the archive format reads it, which differs by kind and is the whole
    reason this is shared: a SYMBOLIC link's target is relative to the
    directory the link sits in, but a HARD link's target is relative to
    the archive root, whatever the member's depth. Resolving both from
    the member's directory accepted a nested hard link naming
    ``../outside``. An absolute target is refused for either kind.
    """
    sNormalized = posixpath.normpath(infoMember.name)
    if fbNormalizedPathEscapesTheRoot(sNormalized):
        return (
            f"member {infoMember.name!r} escapes the extraction root."
        )
    if not (infoMember.issym() or infoMember.islnk()):
        return ""
    if infoMember.islnk():
        sTarget = infoMember.linkname
    else:
        sTarget = posixpath.join(
            posixpath.dirname(sNormalized), infoMember.linkname)
    if posixpath.isabs(infoMember.linkname) or \
            fbNormalizedPathEscapesTheRoot(sTarget):
        return (
            f"link member {infoMember.name!r} targets "
            f"{infoMember.linkname!r} outside the extraction root."
        )
    return ""
