"""Export a container repository as an archive, or refuse a torn one.

``container.get_archive`` walks a directory and streams the files out
one at a time. Nothing is frozen while it walks, so anything writing
inside the container -- a researcher in a terminal, an agent with a
shell, a background process -- can change a file the walk has already
passed or has yet to reach. The archive then holds a mixture of two
moments, a combination that may never have existed on disk at any
single instant.

That matters wherever the export is EVIDENCE. For the PROOF Level 3
shadow rerun the archive is the state an attestation claims to have
reproduced from; if the state never existed, the claim describes
fiction. The usual symptom is not a false pass but a baffling failure:
a torn input file makes the rerun compute something different, the
hashes diverge, and the researcher reasonably concludes their workflow
is non-deterministic when in fact the copy was mixed. Vaibify exists so
a researcher is never in that position, so this module refuses instead.

**How the refusal is decided.** Immediately before and immediately
after the stream, an observation is taken OUTSIDE it: the HEAD commit,
a digest of git's porcelain state map, and -- for every present path
git can enumerate, tracked, untracked and ignored alike -- the path's
type and content identity. The identity is the git blob sha computed in
the container over the RAW worktree bytes, so no clean filter can make
two different byte states report one identity; a symlink records its
readlink target instead, because hashing would read through the link.
The export is refused unless:

- the two observations are exactly equal, and
- every archive member matches the BEFORE observation, by an identity
  recomputed host-side over the archived bytes.

Both halves are load-bearing and neither implies the other. A file
changed mid-stream and changed back leaves the two observations equal
while the archive holds the intermediate bytes -- only the member check
sees that. A file changed after the walk passed it leaves the archive
self-consistent with the before-observation while the repository has
moved on -- only the observation comparison sees that.

**What the observation does NOT cover, stated rather than implied.**
Git enumerates the working tree; it does not enumerate its own
internals. ``.git/`` is therefore exempt from the member check by an
explicit, narrow rule (:data:`TUPLE_UNOBSERVED_PATH_PREFIXES`), and a
concurrent write inside it is not detected. That is tolerable precisely
because no manifest pins anything there: the bytes a workflow reads and
writes are all in the working tree, which IS covered. Any OTHER
unobserved member is refused rather than exempted -- a checked-out
submodule is the case that produces one, and refusing names it instead
of quietly exporting files nothing verified.

**Cost.** Every file in the repository is hashed twice in the
container, plus once more host-side per archive member. That is real,
and it is bounded by the same repository the export already streams; an
attestation that takes minutes can afford it, and the alternative is a
claim about a tree nobody checked.

Extracted from the Agent Council's snapshot capture so the two lanes
decide coherence the same way rather than twice.
"""

import hashlib
import io
import posixpath
import tarfile


__all__ = [
    "ExportTornError",
    "TUPLE_UNOBSERVED_PATH_PREFIXES",
    "fbaExportRepositoryCoherently",
    "fsComputeGitBlobIdentity",
    "fsFindTornProperty",
]


class ExportTornError(Exception):
    """The repository moved while it was being exported.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision silently
    downgrades into an I/O hiccup.
    """


# The one exemption from the member check, and it is a statement about
# what git can SEE rather than a convenience. Everything under a
# repository's own ``.git`` is invisible to ``ls-files``, so an
# observation cannot cover it; nothing a manifest pins lives there.
TUPLE_UNOBSERVED_PATH_PREFIXES = (".git/",)

# Archive member types that carry no bytes and no link target, so there
# is nothing about them for an identity to disagree with.
_TUPLE_UNCHECKED_MEMBER_TYPES = (tarfile.DIRTYPE,)

# What the observation reports for a tracked path that is not on disk.
# It legitimately has no archive member, so the presence check skips it;
# whether a missing tracked path matters at all is the caller's
# question, not this module's.
S_IDENTITY_TYPE_MISSING = "missing"


def fbaExportRepositoryCoherently(
    connectionDocker, sContainerId, sRepoPath, iMaxBytes,
):
    """Return the repository as tar bytes, or refuse a torn export.

    Raises :class:`ExportTornError` naming what moved. An observation
    that could not be taken at all is also a refusal, never an empty
    observation treated as a quiet repository -- fail-closed, because
    "nothing changed" and "we could not look" are the two answers a
    coherence check must never conflate.
    """
    dictBefore = _fdictObserveOrRefuse(
        connectionDocker, sContainerId, sRepoPath, "before")
    baArchive = connectionDocker.fbaFetchDirectoryArchive(
        sContainerId, sRepoPath, iMaxBytes)
    dictAfter = _fdictObserveOrRefuse(
        connectionDocker, sContainerId, sRepoPath, "after")
    _fnRefuseTornObservation(dictBefore, dictAfter)
    _fnRefuseArchiveMismatch(dictBefore, baArchive)
    return baArchive


def _fdictObserveOrRefuse(
    connectionDocker, sContainerId, sRepoPath, sWhen,
):
    """Take one observation, refusing when it could not be taken."""
    dictObservation = connectionDocker.fdictFetchWorktreeIdentities(
        sContainerId, sRepoPath)
    if not dictObservation.get("bSuccess"):
        raise ExportTornError(
            f"The repository state could not be observed {sWhen} the "
            f"copy: {dictObservation.get('sReason') or 'no reason given'}. "
            "Nothing was exported, because an unobservable repository "
            "and an unchanged one are not the same answer."
        )
    return dictObservation


def _fnRefuseTornObservation(dictBefore, dictAfter):
    """Refuse when the two observations disagree, naming what moved."""
    sTorn = fsFindTornProperty(dictBefore, dictAfter)
    if sTorn:
        raise ExportTornError(
            "The repository changed while it was being copied, so the "
            f"copy holds a mixture of two moments: {sTorn}. Nothing was "
            "exported. Make sure nothing is writing inside the "
            "container -- an agent, a terminal, or a running step -- "
            "and try again."
        )


def fsFindTornProperty(dictBefore, dictAfter):
    """Return a description of the first property that moved, or "".

    Ordered from coarsest to finest so the message names the most
    intelligible cause available: a new commit reads better than the
    forty file identities it changed.
    """
    if dictBefore.get("sHeadSha") != dictAfter.get("sHeadSha"):
        return (
            f"HEAD moved from {dictBefore.get('sHeadSha', '')[:12]} to "
            f"{dictAfter.get('sHeadSha', '')[:12]}"
        )
    sPathChange = _fsFindChangedPath(
        dictBefore.get("dictPathIdentities") or {},
        dictAfter.get("dictPathIdentities") or {},
    )
    if sPathChange:
        return sPathChange
    if dictBefore.get("sPorcelainDigest") != dictAfter.get(
            "sPorcelainDigest"):
        return "the repository's git status changed"
    return ""


def _fsFindChangedPath(dictBefore, dictAfter):
    """Return the first path that appeared, vanished or moved."""
    for sPath in sorted(set(dictBefore) | set(dictAfter)):
        dictOne = dictBefore.get(sPath)
        dictTwo = dictAfter.get(sPath)
        if dictOne is None:
            return f"{sPath} appeared during the copy"
        if dictTwo is None:
            return f"{sPath} disappeared during the copy"
        if dictOne != dictTwo:
            return f"{sPath} was rewritten during the copy"
    return ""


def _fnRefuseArchiveMismatch(dictBefore, baArchive):
    """Refuse when any archive member disagrees with the observation.

    This is the half that catches a file changed and changed back: the
    two observations agree perfectly, and only the archived BYTES
    contradict them.
    """
    dictIdentities = dictBefore.get("dictPathIdentities") or {}
    setSeen = set()
    with tarfile.open(fileobj=io.BytesIO(baArchive), mode="r") as fileTar:
        for infoMember in fileTar:
            sRelative = _fsRepoRelativeMemberPath(infoMember.name)
            if sRelative is None or _fbMemberIsUnobservable(
                    infoMember, sRelative):
                continue
            setSeen.add(sRelative)
            _fnRefuseMemberMismatch(
                infoMember, sRelative, dictIdentities.get(sRelative),
                fileTar)
    _fnRefuseObservedPathMissingFromArchive(dictIdentities, setSeen)


def _fsRepoRelativeMemberPath(sMemberName):
    """Return a member's path relative to the repository root, or None.

    ``get_archive`` names members relative to the exported directory's
    PARENT, so every member carries the repository's own basename as its
    first component. Stripping it is what puts the archive and the
    observation in one vocabulary; a member with no second component is
    the repository directory itself and has nothing to compare.
    """
    listParts = posixpath.normpath(sMemberName).split("/")
    if len(listParts) < 2:
        return None
    return "/".join(listParts[1:])


def _fbMemberIsUnobservable(infoMember, sRelative):
    """Return True for members no observation could have covered."""
    if infoMember.type in _TUPLE_UNCHECKED_MEMBER_TYPES:
        return True
    return any(
        sRelative == sPrefix.rstrip("/")
        or sRelative.startswith(sPrefix)
        for sPrefix in TUPLE_UNOBSERVED_PATH_PREFIXES
    )


def _fnRefuseMemberMismatch(
    infoMember, sRelative, dictObserved, fileTar,
):
    """Refuse one archive member the observation cannot account for."""
    if dictObserved is None:
        raise ExportTornError(
            f"The copy contains {sRelative!r}, which git could not "
            "enumerate, so nothing verified it. The usual cause is a "
            "checked-out submodule, whose files no superproject git "
            "command lists. Nothing was exported."
        )
    if infoMember.issym():
        if dictObserved.get("sIdentity") != infoMember.linkname:
            raise ExportTornError(
                f"The symlink {sRelative!r} in the copy points at "
                f"{infoMember.linkname!r}, but the repository recorded "
                f"{dictObserved.get('sIdentity')!r}. Nothing was "
                "exported."
            )
        return
    fileExtracted = fileTar.extractfile(infoMember)
    if fileExtracted is None:
        return
    if fsComputeGitBlobIdentity(fileExtracted.read()) != dictObserved.get(
            "sIdentity"):
        raise ExportTornError(
            f"The file {sRelative!r} was rewritten while it was being "
            "copied, so the copy holds bytes the repository never "
            "settled on. Nothing was exported. Make sure nothing is "
            "writing inside the container -- an agent, a terminal, or a "
            "running step -- and try again."
        )


def _fnRefuseObservedPathMissingFromArchive(dictIdentities, setSeen):
    """Refuse when a path the repository holds never reached the copy."""
    listAbsent = sorted(
        sPath for sPath, dictObserved in dictIdentities.items()
        if sPath not in setSeen
        and dictObserved.get("sType") != S_IDENTITY_TYPE_MISSING
    )
    if listAbsent:
        raise ExportTornError(
            f"{len(listAbsent)} path(s) the repository holds are absent "
            f"from the copy, beginning with {listAbsent[0]!r}. Nothing "
            "was exported."
        )


def fsComputeGitBlobIdentity(baContent):
    """Return the git blob sha of some bytes.

    ``sha1`` over ``blob <size>\\0`` plus the content -- byte-identical
    to ``git hash-object --no-filters``, verified against it. The
    identity stays in the RAW-BYTE domain on both sides of the
    comparison, never the filtered object-store domain, so a repository
    using content filters is neither falsely refused nor able to alias
    two byte states to one identity.

    Hashed in one call rather than streamed in chunks: the caller
    already holds the whole member in memory (it came out of a tar the
    hub materialised), so a streaming form would save nothing and cost
    a binding the naming contract has no cast for.
    """
    return hashlib.sha1(
        f"blob {len(baContent)}\0".encode() + baContent,
    ).hexdigest()
