"""Bounded immutable project-context snapshots for the Agent Council.

Captures the active project repository out of its container into local
application data (``~/.vaibify/agentCouncils/<campaign-id>/snapshot/``),
where the council's participants read it without ever consulting the
active project container again (design/agentCouncil.md section 9.2).

**Phase 0 decision (recorded).** Bulk project export is a new security
primitive, and the design left three shapes for it: extend the
typed-read table, relabel a general container command as a read, or
review a narrow Docker archive read. This module implements the third.
The typed-read carve-out is granted at exactly one private method
(``DockerConnection._ftRunTypedRead``, pinned by ``S_EXEMPTION_METHOD``
in ``tests/testMutationBoundary.py``), and a repository export does not
belong in a table whose whole safety argument is "each entry is a small
fixed program over a path literal" -- growing that table toward bulk
export would stretch the one exemption the boundary allows. Relabeling
a general command (``tar`` piped over exec) is forbidden outright by
section 9.2. ``container.get_archive`` is neither: it is a Docker
daemon API read that executes NO command in the container -- the
daemon itself serializes the filesystem -- so nothing caller-supplied
can become program text, and the container gains no process. The same
API already backs :meth:`DockerConnection.fiterStreamFile`. This module
is the single home of the repository-scale use of it; the unit suite
pins that no third module grows a ``get_archive`` call site.

**Coherence caveat (honest, not assumed).** Section 9.2 wants the
capture under a bounded project lock. That lock is Phase 3 route
wiring (a mode-b lock-held carrier around this call); this module
does not fake one. Instead it reads the repository identity (commit
plus a digest of the porcelain working-tree state) immediately before
and immediately after streaming, and REFUSES the capture when the two
disagree. A torn capture is therefore detected, never silently sealed;
the manifest records the method so the caveat travels with the
snapshot.

**Symlink policy (reviewed, recorded).** A symbolic link whose target
stays inside the project root is captured as a symlink. A link whose
target is absolute or resolves outside the root REFUSES the whole
capture, rather than being recorded-and-excluded: a repository whose
links reach outside itself is not a self-contained context, and a
snapshot that silently dropped the link would misrepresent the
repository to every council participant. Structural violations
(absolute member paths, ``..`` escapes, duplicates, devices, FIFOs,
hard links, unknown member types) refuse for the same reason. Only the
REVIEWED EXCLUSION POLICY below excludes-and-records; refusal and
recorded omission are deliberately different answers.

**Integration seams left open.** Campaign-id provenance (who minted
the id, where it is registered) is Phase 2; this module validates only
the identifier's shape. The carrier-mode declaration and any mutation
inventory rows this module's call sites produce are dispositioned at
route integration, not here.
"""

__all__ = [
    "SnapshotRefusedError",
    "I_MAX_SNAPSHOT_FILE_COUNT",
    "I_MAX_SNAPSHOT_MEMBER_BYTES",
    "I_MAX_SNAPSHOT_TOTAL_BYTES",
    "DICT_EXCLUDED_COMPONENT_REASONS",
    "S_SNAPSHOT_ARCHIVE_BASENAME",
    "S_SNAPSHOT_MANIFEST_BASENAME",
    "fdictCaptureProjectContextSnapshot",
]

import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import tarfile
from datetime import datetime, timezone

from vaibify.docker import dockerConnection
from vaibify.gui import containerGit


# A research repository with more members than this is carrying bulk
# data that belongs in data storage, not in a council context window;
# the cap also bounds the manifest and the runner copy-in time. Counts
# every included member (files, directories, symlinks).
I_MAX_SNAPSHOT_FILE_COUNT = 20000

# Matches I_MAX_FETCH_FILE_BYTES in dockerConnection, for the same
# reason: each included file is materialised in memory once while it is
# hashed and re-archived, so the per-member cap is also the RAM bound.
# Larger files are datasets, not context.
I_MAX_SNAPSHOT_MEMBER_BYTES = 64 * 1024 * 1024

# Bounds the host disk a single campaign can claim under ~/.vaibify and
# the bytes copied into every disposable runner (design section 9.6).
I_MAX_SNAPSHOT_TOTAL_BYTES = 512 * 1024 * 1024

S_SNAPSHOT_ARCHIVE_BASENAME = "snapshot.tar"
S_SNAPSHOT_MANIFEST_BASENAME = "manifest.json"
S_SNAPSHOT_MANIFEST_SCHEMA_VERSION = "1"

_S_PARTIAL_SUFFIX = ".partial"

# The reviewed exclusion policy: path components that exclude a subtree
# from the snapshot, each with the reason recorded in the manifest. The
# credential entries are the agent config directories the container
# entrypoint persists onto the workspace volume (fnPersistAgentConfig in
# vaibify/containerImage/entrypoint.sh) plus the conventional credential
# stores; they normally live BESIDE the project repository, but an agent
# run from inside the repository can create one there, so the exclusion
# matches at any depth. Repository internals are excluded because the
# manifest records the commit and dirty-state digest instead; the
# generated-output seeds are conservative and grow by review, never by
# pattern-widening.
DICT_EXCLUDED_COMPONENT_REASONS = {
    ".git": "repository internals; commit and dirty-state digest are "
            "recorded in the manifest instead",
    ".vaibify": "vaibify runtime state",
    ".claude": "agent credential and configuration store",
    ".codex": "agent credential and configuration store",
    ".gemini": "agent credential and configuration store",
    ".opencode": "agent credential and configuration store",
    ".cline": "agent credential and configuration store",
    ".clinerules": "agent instruction directory",
    ".openhands": "agent credential and configuration store",
    ".pi": "agent credential and configuration store",
    ".ssh": "credential store",
    ".netrc": "credential store",
    ".git-credentials": "credential store",
    "__pycache__": "generated bytecode cache",
    ".pytest_cache": "generated test cache",
    ".ipynb_checkpoints": "generated notebook checkpoints",
}

_DICT_REFUSED_MEMBER_TYPE_NAMES = {
    tarfile.CHRTYPE: "character device",
    tarfile.BLKTYPE: "block device",
    tarfile.FIFOTYPE: "FIFO",
    tarfile.LNKTYPE: "hard link",
}

_S_CAMPAIGN_IDENTIFIER_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"

_S_COHERENCE_METHOD = (
    "repository identity (commit + dirty-state digest) compared before "
    "and after streaming; the bounded project lock is Phase 3 wiring"
)


class SnapshotRefusedError(Exception):
    """A snapshot capture was refused before it could be sealed.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision silently
    downgrades into an I/O hiccup (the lesson recorded for
    ``ControlPlaneRefusalError``).
    """


def fdictCaptureProjectContextSnapshot(
    connectionDocker, sContainerId, sProjectRepoPath, sCampaignId,
    sSnapshotStoreRoot=None,
):
    """Capture one bounded, validated, immutable project snapshot.

    Returns the manifest dict after writing the validated tar and the
    manifest under ``<store>/<campaign>/snapshot/`` with owner-only
    permissions. Raises :class:`SnapshotRefusedError` on any policy
    refusal; underlying stream or daemon errors propagate as-is. On
    ANY failure after the snapshot directory was created, the partial
    snapshot is removed before the error is re-raised.

    ``sSnapshotStoreRoot`` overrides the application-data root
    (``~/.vaibify/agentCouncils``) so tests never touch real state.
    """
    _fnValidateCampaignIdentifier(sCampaignId)
    sRepoRoot = _fsValidateProjectRepositoryRoot(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    dictIdentityBefore = _fdictReadRepositoryIdentity(
        connectionDocker, sContainerId, sRepoRoot,
    )
    sCaptureStartIso = datetime.now(timezone.utc).isoformat()
    sSnapshotDirectory = _fsCreateSnapshotDirectory(
        sCampaignId, sSnapshotStoreRoot,
    )
    try:
        dictCapture = _fdictStreamValidatedArchive(
            connectionDocker, sContainerId, sRepoRoot, sSnapshotDirectory,
        )
        dictIdentityAfter = _fdictReadRepositoryIdentity(
            connectionDocker, sContainerId, sRepoRoot,
        )
        _fnRefuseIncoherentCapture(dictIdentityBefore, dictIdentityAfter)
        sArchivePath = os.path.join(
            sSnapshotDirectory, S_SNAPSHOT_ARCHIVE_BASENAME,
        )
        os.replace(sArchivePath + _S_PARTIAL_SUFFIX, sArchivePath)
        return _fdictWriteSnapshotManifest(
            sSnapshotDirectory, sContainerId, sRepoRoot, sCampaignId,
            dictIdentityBefore, sCaptureStartIso, dictCapture,
        )
    except BaseException:
        _fnRemovePartialSnapshot(sSnapshotDirectory)
        raise


def _fnValidateCampaignIdentifier(sCampaignId):
    """Refuse a campaign id that could become a host path component."""
    if not isinstance(sCampaignId, str) or not re.fullmatch(
        _S_CAMPAIGN_IDENTIFIER_PATTERN, sCampaignId,
    ):
        raise SnapshotRefusedError(
            f"Campaign identifier {sCampaignId!r} is refused: it must "
            f"match {_S_CAMPAIGN_IDENTIFIER_PATTERN} so it can never "
            "traverse the snapshot store."
        )


def _fsValidateProjectRepositoryRoot(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Return the validated repo root, confirmed by the git authority.

    The path must be the normalized absolute container path of the git
    work-tree root, and git itself -- through the existing
    ``containerGit`` authority -- must agree that it IS the top level.
    A subdirectory, a non-repository, or a path that only looks like a
    root is refused rather than exported.
    """
    if not isinstance(sProjectRepoPath, str) or not sProjectRepoPath:
        raise SnapshotRefusedError(
            "No project repository path was supplied; a snapshot needs "
            "the workflow's sProjectRepoPath."
        )
    if not posixpath.isabs(sProjectRepoPath):
        raise SnapshotRefusedError(
            f"Project repository path {sProjectRepoPath!r} is refused: "
            "it must be an absolute container path."
        )
    sNormalized = posixpath.normpath(sProjectRepoPath)
    if sNormalized != sProjectRepoPath or sNormalized == "/":
        raise SnapshotRefusedError(
            f"Project repository path {sProjectRepoPath!r} is refused: "
            "pass the normalized work-tree root, never / itself."
        )
    sDetectedRoot = containerGit.fsDetectProjectRepoInContainer(
        connectionDocker, sContainerId,
        posixpath.join(sNormalized, "project.json"),
    )
    if sDetectedRoot != sNormalized:
        sDetectedDescription = (
            repr(sDetectedRoot) if sDetectedRoot else "no repository"
        )
        raise SnapshotRefusedError(
            f"Project repository path {sNormalized!r} is refused: git "
            f"reports the enclosing work-tree root as "
            f"{sDetectedDescription}, and only the root may be "
            "snapshotted."
        )
    return sNormalized


def _fdictReadRepositoryIdentity(connectionDocker, sContainerId, sRepoRoot):
    """Return the commit and dirty-state digest via the git authority.

    The dirty digest hashes the porcelain file-state map, so an edit,
    an add, or a delete anywhere in the working tree changes it. No
    remote URL is read or recorded: a remote URL can embed a
    credential, and nothing secret may enter the manifest.
    """
    dictGitStatus = containerGit.fdictGitStatusInContainer(
        connectionDocker, sContainerId, sWorkspace=sRepoRoot,
    )
    if not dictGitStatus.get("bIsRepo"):
        raise SnapshotRefusedError(
            f"{sRepoRoot!r} is not a git repository "
            f"({dictGitStatus.get('sReason') or 'no detail'}); every "
            "vaibify project lives in one, so there is nothing "
            "coherent to snapshot."
        )
    sDirtyStateDigest = hashlib.sha256(
        json.dumps(
            dictGitStatus.get("dictFileStates") or {}, sort_keys=True,
        ).encode("utf-8"),
    ).hexdigest()
    return {
        "sCommitSha": dictGitStatus.get("sHeadSha") or "",
        "sDirtyStateDigest": sDirtyStateDigest,
    }


def _fsCreateSnapshotDirectory(sCampaignId, sSnapshotStoreRoot):
    """Create ``<store>/<campaign>/snapshot`` owner-only; refuse reuse.

    An existing snapshot directory refuses BEFORE anything is created,
    so the failure-cleanup path can never delete a previous capture: a
    snapshot is immutable, and replacing one is a deliberate delete
    plus a fresh capture, never an overwrite.
    """
    sStoreRoot = sSnapshotStoreRoot or os.path.join(
        os.path.expanduser("~"), ".vaibify", "agentCouncils",
    )
    sCampaignDirectory = os.path.join(sStoreRoot, sCampaignId)
    sSnapshotDirectory = os.path.join(sCampaignDirectory, "snapshot")
    if os.path.exists(sSnapshotDirectory):
        raise SnapshotRefusedError(
            f"A snapshot already exists for campaign {sCampaignId!r}; "
            "snapshots are immutable and are never overwritten."
        )
    os.makedirs(sSnapshotDirectory, mode=0o700)
    for sDirectory in (sStoreRoot, sCampaignDirectory, sSnapshotDirectory):
        os.chmod(sDirectory, 0o700)
    return sSnapshotDirectory


def _fdictStreamValidatedArchive(
    connectionDocker, sContainerId, sRepoRoot, sSnapshotDirectory,
):
    """Stream get_archive into a validated tar; return the accounting.

    ``container.get_archive`` is the Docker daemon API read this module
    exists to review (see the module docstring): the daemon serializes
    the path itself, no command runs in the container, and every member
    is validated here before a byte of it is kept.
    """
    container = connectionDocker.fcontainerGetById(sContainerId)
    iterTarStream, _ = container.get_archive(sRepoRoot)
    sPartialPath = os.path.join(
        sSnapshotDirectory, S_SNAPSHOT_ARCHIVE_BASENAME + _S_PARTIAL_SUFFIX,
    )
    dictCapture = {
        "setSeenPaths": set(),
        "dictOmissionReasons": {},
        "listIncludedEntries": [],
        "iIncludedMemberCount": 0,
        "iTotalContentBytes": 0,
    }
    filePipe = dockerConnection._BytesGeneratorPipe(iterTarStream)
    fileArchiveOutput = os.fdopen(
        os.open(
            sPartialPath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
        ),
        "wb",
    )
    try:
        with tarfile.open(fileobj=filePipe, mode="r|") as fileTarSource, \
                tarfile.open(
                    fileobj=fileArchiveOutput, mode="w",
                ) as fileTarOutput:
            for infoMember in fileTarSource:
                _fnAppendValidatedMember(
                    fileTarSource, fileTarOutput, infoMember,
                    posixpath.basename(sRepoRoot), dictCapture,
                )
    finally:
        fileArchiveOutput.close()
        _fnCloseArchiveStream(iterTarStream)
    return dictCapture


def _fnAppendValidatedMember(
    fileTarSource, fileTarOutput, infoMember, sRootComponent, dictCapture,
):
    """Validate one member; copy it into the snapshot, record, or refuse."""
    sRelativePath = _fsValidateMemberPath(
        infoMember.name, sRootComponent, dictCapture["setSeenPaths"],
    )
    if sRelativePath is None:
        return
    tExclusion = _ftFindExcludedComponent(sRelativePath)
    if tExclusion is not None:
        sOmissionPath, sReason = tExclusion
        dictCapture["dictOmissionReasons"].setdefault(sOmissionPath, sReason)
        return
    _fnEnforceCaptureLimits(dictCapture, infoMember, sRelativePath)
    if infoMember.issym():
        _fnRefuseEscapingSymlink(sRelativePath, infoMember.linkname)
        fileTarOutput.addfile(
            _finfoBuildOutputEntry(infoMember, sRelativePath),
        )
        dictEntry = {
            "sPath": sRelativePath, "sType": "symlink",
            "iSizeBytes": 0, "sSha256": "",
            "sLinkTarget": infoMember.linkname,
        }
    elif infoMember.isdir():
        fileTarOutput.addfile(
            _finfoBuildOutputEntry(infoMember, sRelativePath),
        )
        dictEntry = {
            "sPath": sRelativePath, "sType": "directory",
            "iSizeBytes": 0, "sSha256": "",
        }
    elif infoMember.isfile():
        dictEntry = _fdictCopyFileMember(
            fileTarSource, fileTarOutput, infoMember, sRelativePath,
        )
        dictCapture["iTotalContentBytes"] += dictEntry["iSizeBytes"]
    else:
        sTypeName = _DICT_REFUSED_MEMBER_TYPE_NAMES.get(
            infoMember.type, f"unsupported type {infoMember.type!r}",
        )
        raise SnapshotRefusedError(
            f"Archive member {sRelativePath!r} is a {sTypeName}, which "
            "a project snapshot cannot represent; capture refused."
        )
    dictCapture["listIncludedEntries"].append(dictEntry)


def _fsValidateMemberPath(sMemberName, sRootComponent, setSeenPaths):
    """Return the repo-relative path, ``None`` for the root, or refuse."""
    if sMemberName.startswith("/"):
        raise SnapshotRefusedError(
            f"Archive member {sMemberName!r} has an absolute path; "
            "capture refused."
        )
    if ".." in sMemberName.split("/"):
        raise SnapshotRefusedError(
            f"Archive member {sMemberName!r} contains a '..' "
            "component; capture refused."
        )
    sNormalized = posixpath.normpath(sMemberName)
    if sNormalized in (".", sRootComponent):
        return None
    if not sNormalized.startswith(sRootComponent + "/"):
        raise SnapshotRefusedError(
            f"Archive member {sMemberName!r} falls outside the "
            f"archive root {sRootComponent!r}; capture refused."
        )
    sRelativePath = sNormalized[len(sRootComponent) + 1:]
    if sRelativePath in setSeenPaths:
        raise SnapshotRefusedError(
            f"Archive member {sRelativePath!r} appears twice; a "
            "duplicate would let the second write shadow the first, so "
            "capture is refused."
        )
    setSeenPaths.add(sRelativePath)
    return sRelativePath


def _ftFindExcludedComponent(sRelativePath):
    """Return ``(sOmissionPath, sReason)`` for a policy-excluded path.

    The omission is recorded at the SHALLOWEST excluded component, so a
    ``.git`` tree is one manifest row, not one per object file: the
    omission of the subtree is the information, its internal layout is
    not. Returns ``None`` for a path the policy does not exclude.
    """
    listParts = sRelativePath.split("/")
    for iDepth, sPart in enumerate(listParts):
        sReason = DICT_EXCLUDED_COMPONENT_REASONS.get(sPart)
        if sReason is not None:
            return ("/".join(listParts[:iDepth + 1]), sReason)
    return None


def _fnEnforceCaptureLimits(dictCapture, infoMember, sRelativePath):
    """Refuse the capture the moment any declared bound is exceeded."""
    iMemberCount = dictCapture["iIncludedMemberCount"] + 1
    if iMemberCount > I_MAX_SNAPSHOT_FILE_COUNT:
        raise SnapshotRefusedError(
            f"The repository exceeds the snapshot member limit "
            f"({I_MAX_SNAPSHOT_FILE_COUNT}); capture refused."
        )
    if infoMember.isfile():
        if infoMember.size > I_MAX_SNAPSHOT_MEMBER_BYTES:
            raise SnapshotRefusedError(
                f"File {sRelativePath!r} is {infoMember.size} bytes, "
                f"over the per-file limit "
                f"({I_MAX_SNAPSHOT_MEMBER_BYTES}); capture refused."
            )
        if (
            dictCapture["iTotalContentBytes"] + infoMember.size
            > I_MAX_SNAPSHOT_TOTAL_BYTES
        ):
            raise SnapshotRefusedError(
                f"The repository exceeds the total snapshot size limit "
                f"({I_MAX_SNAPSHOT_TOTAL_BYTES} bytes); capture "
                "refused."
            )
    dictCapture["iIncludedMemberCount"] = iMemberCount


def _fnRefuseEscapingSymlink(sRelativePath, sLinkTarget):
    """Refuse a symlink whose target leaves the project root."""
    if not sLinkTarget or posixpath.isabs(sLinkTarget):
        raise SnapshotRefusedError(
            f"Symlink {sRelativePath!r} targets {sLinkTarget!r}, which "
            "is not a relative in-project path; capture refused (see "
            "the module docstring for the reviewed symlink policy)."
        )
    sResolved = posixpath.normpath(
        posixpath.join(posixpath.dirname(sRelativePath), sLinkTarget),
    )
    if sResolved == ".." or sResolved.startswith("../"):
        raise SnapshotRefusedError(
            f"Symlink {sRelativePath!r} resolves outside the project "
            f"root (target {sLinkTarget!r}); capture refused."
        )


def _fdictCopyFileMember(
    fileTarSource, fileTarOutput, infoMember, sRelativePath,
):
    """Copy one regular file into the snapshot; return its manifest entry.

    The content is held in memory once, bounded by the per-member limit
    already enforced, so it can be hashed and re-archived without a
    second pass over the daemon stream.
    """
    fileExtract = fileTarSource.extractfile(infoMember)
    if fileExtract is None:
        raise SnapshotRefusedError(
            f"Archive member {sRelativePath!r} advertises a file but "
            "carries no readable payload; capture refused."
        )
    baContent = fileExtract.read()
    infoOutput = _finfoBuildOutputEntry(infoMember, sRelativePath)
    infoOutput.size = len(baContent)
    fileTarOutput.addfile(infoOutput, io.BytesIO(baContent))
    return {
        "sPath": sRelativePath,
        "sType": "file",
        "iSizeBytes": len(baContent),
        "sSha256": hashlib.sha256(baContent).hexdigest(),
    }


def _finfoBuildOutputEntry(infoMember, sRelativePath):
    """Return a sanitized TarInfo for the validated snapshot archive.

    Ownership is normalized to 0:0 with empty names so the snapshot
    carries no container account details; the runner copy-in restamps
    ownership for its own container (Phase 2). Mode and mtime are
    preserved so scripts stay executable and timestamps stay honest.
    """
    infoOutput = tarfile.TarInfo(name=sRelativePath)
    if infoMember.issym():
        infoOutput.type = tarfile.SYMTYPE
        infoOutput.linkname = infoMember.linkname
    elif infoMember.isdir():
        infoOutput.type = tarfile.DIRTYPE
    else:
        infoOutput.type = tarfile.REGTYPE
    infoOutput.mode = infoMember.mode
    infoOutput.mtime = infoMember.mtime
    infoOutput.uid = 0
    infoOutput.gid = 0
    infoOutput.uname = ""
    infoOutput.gname = ""
    return infoOutput


def _fnCloseArchiveStream(iterTarStream):
    """Release the daemon stream's socket if the SDK exposes a close."""
    fnClose = getattr(iterTarStream, "close", None)
    if callable(fnClose):
        try:
            fnClose()
        except Exception:
            pass


def _fnRefuseIncoherentCapture(dictIdentityBefore, dictIdentityAfter):
    """Refuse a capture the repository changed underneath."""
    if dictIdentityBefore == dictIdentityAfter:
        return
    raise SnapshotRefusedError(
        "The project repository changed while the snapshot was "
        "streaming (the commit or working-tree state differs between "
        "the pre- and post-capture reads); the partial capture is "
        "discarded. Re-run the capture when the project is quiet -- "
        "the bounded project lock that will serialize capture against "
        "pipeline work is Phase 3 wiring."
    )


def _fsComputeSnapshotContentHash(listIncludedEntries):
    """Return the deterministic identity hash the evidence ledger cites.

    Hashes the sorted (path, type, content digest, link target) rows,
    so the identity depends on what the snapshot CONTAINS and not on
    tar framing or capture timestamps: two captures of an unchanged
    repository carry the same identity.
    """
    listLines = sorted(
        "\x00".join(
            (
                dictEntry["sPath"],
                dictEntry["sType"],
                dictEntry.get("sSha256", ""),
                dictEntry.get("sLinkTarget", ""),
            ),
        )
        for dictEntry in listIncludedEntries
    )
    return hashlib.sha256("\n".join(listLines).encode("utf-8")).hexdigest()


def _fdictWriteSnapshotManifest(
    sSnapshotDirectory, sContainerId, sRepoRoot, sCampaignId,
    dictIdentity, sCaptureStartIso, dictCapture,
):
    """Compose and write the manifest; return it as the capture record."""
    listOmissions = [
        {"sPath": sPath, "sReason": sReason}
        for sPath, sReason in sorted(
            dictCapture["dictOmissionReasons"].items(),
        )
    ]
    dictManifest = {
        "sSchemaVersion": S_SNAPSHOT_MANIFEST_SCHEMA_VERSION,
        "sCampaignId": sCampaignId,
        "sContainerId": sContainerId,
        "sProjectRepoPath": sRepoRoot,
        "sCaptureMethod": "docker get_archive (daemon API read)",
        "sCoherenceMethod": _S_COHERENCE_METHOD,
        "sCommitSha": dictIdentity["sCommitSha"],
        "sDirtyStateDigest": dictIdentity["sDirtyStateDigest"],
        "sCaptureStartIso": sCaptureStartIso,
        "sCaptureEndIso": datetime.now(timezone.utc).isoformat(),
        "iIncludedMemberCount": dictCapture["iIncludedMemberCount"],
        "iTotalContentBytes": dictCapture["iTotalContentBytes"],
        "sSnapshotSha256": _fsComputeSnapshotContentHash(
            dictCapture["listIncludedEntries"],
        ),
        "listIncludedEntries": dictCapture["listIncludedEntries"],
        "listOmissions": listOmissions,
    }
    sManifestPath = os.path.join(
        sSnapshotDirectory, S_SNAPSHOT_MANIFEST_BASENAME,
    )
    _fnWritePrivateFile(
        sManifestPath + _S_PARTIAL_SUFFIX,
        json.dumps(dictManifest, indent=2, sort_keys=True).encode("utf-8"),
    )
    os.replace(sManifestPath + _S_PARTIAL_SUFFIX, sManifestPath)
    return dictManifest


def _fnWritePrivateFile(sFilePath, baContent):
    """Write bytes to a new owner-only (0600) file."""
    iDescriptor = os.open(
        sFilePath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600,
    )
    with os.fdopen(iDescriptor, "wb") as fileOutput:
        fileOutput.write(baContent)


def _fnRemovePartialSnapshot(sSnapshotDirectory):
    """Remove a failed capture's directory; never mask the real error.

    ``ignore_errors`` is deliberate: the caller is already unwinding
    the exception that matters, and a cleanup stumble must not replace
    it. The campaign directory is retired too when the failed snapshot
    was the only thing in it.
    """
    shutil.rmtree(sSnapshotDirectory, ignore_errors=True)
    try:
        os.rmdir(os.path.dirname(sSnapshotDirectory))
    except OSError:
        pass
