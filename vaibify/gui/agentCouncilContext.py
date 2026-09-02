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

**Coherence algorithm (R5; the lock stays the controller's job).**
Section 9.2 wants the capture under a bounded project lock. That lock
is controller integration wiring (R1b: a mode-b lock-held carrier
around this call); this module does not fake one. What it does
instead: immediately before and immediately after streaming it takes
an independent observation OUTSIDE the archive stream -- the HEAD
commit, a digest of the porcelain file-state map, and, for EVERY
present path (tracked, untracked, AND git-ignored alike), the
path's type and a content identity -- the git blob sha computed in
the container over the raw worktree bytes (byte-identical to ``git
hash-object --no-filters``), through the declared
``gitWorktreeIdentities`` typed read rather than a general container
command; a symlink records its readlink target instead, because
hashing reads THROUGH a link. The capture REFUSES, naming the torn
property, unless the two observations are exactly equal -- and
additionally refuses unless EVERY archive member matches the
PRE-observation: each observed path's member carries the same git
blob identity (recomputed host-side over the archived bytes) or
symlink target, and a file or symlink member the observation never
saw refuses outright. Full observation width is load-bearing, not
thoroughness for its own sake: a CLEAN tracked file changed
mid-stream and reverted leaves HEAD, the porcelain digest, and the
changed-path set all equal, and only its raw-byte identity taken
outside the stream contradicts the archive's intermediate bytes. The
identities stay in the raw-worktree-byte domain on both sides, never
the filtered object-store domain, so content filters cannot alias two
byte states -- and cannot falsely refuse a clean repository. (A
consequence, recorded: a submodule's files exist on disk but are
enumerated by no superproject git command, so a repository carrying a
checked-out submodule REFUSES capture as unobserved members --
fail-closed, until submodule pinning is designed on its own terms.) A
torn capture is therefore detected, never silently sealed; the
manifest records the method and both observation digests (digests
only, never observation content) so the guarantee travels with the
snapshot.

**Observation scope (decision, recorded).** The per-path content
observation is limited to paths the snapshot INCLUDES: a path under a
policy-excluded component is dropped from the observation set, so
churn inside an excluded tree -- an agent config store rewriting
itself mid-capture -- cannot spuriously refuse a capture of content
the snapshot does not carry (git never reports ``.git`` internals in
the first place). The porcelain-state digest deliberately keeps its
full working-tree width, so a rename, add, delete, or type change
among tracked files still refuses even under an excluded parent.

**Agent-instruction-file policy (R11, DECIDED).** Project agent docs
-- ``CLAUDE.md`` / ``AGENTS.md`` / ``GEMINI.md`` and the agent config
directories -- are EXCLUSIONS, not evidence, at EVERY depth: they are
meta-instructions to an agent, not source under review, and shipping
one hands a hostile repository a steering channel into a participant.
The exclusion is belt one. Belt two is the delivery mechanism: the
council charter reaches the CLI as ``--append-system-prompt`` (a flag,
never a file written into the snapshot tree), so even a doc that
somehow survived would sit in user-level context below the charter's
system-level instruction. Both belts are pinned by tests
(``testAgentDocExclusionPolicyIsPinned`` here,
``test_charter_rides_the_instruction_flag_never_a_snapshot_file`` in
the provider suite); whether a REAL model obeys a hostile surviving
doc over the charter is a per-adapter empiric that needs a paid model
turn -- the maintainer's live-check lane, recorded, never assumed.

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
    "fbaReadSealedSnapshotArchive",
    "fdictCaptureProjectContextSnapshot",
    "fsComputePathIdentitiesDigest",
    "fsResolveSnapshotDirectory",
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
from vaibify.gui import agentCouncilCapacity, containerGit


# The three bounds below are FLOORS, not the bounds a capture actually
# enforces. What a given machine allows is resolved by
# agentCouncilCapacity and threaded in as ``dictBounds``; these are what
# a caller gets when nothing was resolved, and what every supported
# machine is guaranteed. They are re-exported from the capacity module
# rather than re-typed, so the two can never disagree.

# A research repository with more members than this is carrying bulk
# data that belongs in data storage, not in a council context window;
# the cap also bounds the manifest and the runner copy-in time. Counts
# every included member (files, directories, symlinks).
I_MAX_SNAPSHOT_FILE_COUNT = agentCouncilCapacity.I_FLOOR_SNAPSHOT_FILE_COUNT

# Each included file is materialised in memory once while it is hashed
# and re-archived, so the per-member cap is a HOST RAM bound and scales
# with the researcher's own machine, never with the daemon's.
I_MAX_SNAPSHOT_MEMBER_BYTES = (
    agentCouncilCapacity.I_FLOOR_SNAPSHOT_MEMBER_BYTES)

# Bounds the host disk a single campaign can claim under ~/.vaibify and
# the bytes copied into every disposable runner (design section 9.6).
# A DAEMON bound: the copy lands in a tmpfs charged to the runner's
# memory cgroup.
I_MAX_SNAPSHOT_TOTAL_BYTES = (
    agentCouncilCapacity.I_FLOOR_SNAPSHOT_TOTAL_BYTES)

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
    "CLAUDE.md": "agent instruction file; the council delivers its own "
                 "charter through the CLI instruction channel and must not "
                 "let a snapshot agent doc steer the participant",
    "AGENTS.md": "agent instruction file; the council delivers its own "
                 "charter through the CLI instruction channel and must not "
                 "let a snapshot agent doc steer the participant",
    "GEMINI.md": "agent instruction file; the council delivers its own "
                 "charter through the CLI instruction channel and must not "
                 "let a snapshot agent doc steer the participant",
    ".openhands": "agent credential and configuration store",
    ".pi": "agent credential and configuration store",
    ".ssh": "credential store",
    ".netrc": "credential store",
    ".git-credentials": "credential store",
    # Added 2026-08-24, when the ruling arrived that git-IGNORED files
    # DO ship (a derived artifact that costs an hour to regenerate is
    # worth carrying, and researchers expect the whole repository in
    # the shadow container). Until then .gitignore kept the
    # conventional dotenv out incidentally; now nothing else would. It
    # is a conventional credential store like the three above, not a
    # widening of the policy — and matching it as a COMPONENT also
    # drops a virtualenv that happens to be named .env, which is bulk
    # the snapshot is better without.
    ".env": "credential store (conventional dotenv)",
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
    "two independent pre/post observations outside the archive stream "
    "(HEAD commit + porcelain state digest + per-path git content "
    "identities and symlink targets of every non-excluded present "
    "path) compared exactly, plus EVERY archive member matched to the "
    "pre-observation by git blob identity, unobserved members refused; "
    "the bounded project lock is controller (R1b) wiring"
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
    sSnapshotStoreRoot=None, dictBounds=None, listExcludedPaths=None,
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

    ``dictBounds`` is this machine's resolved capacity; omitting it
    uses the guaranteed floors, which is what every caller got before
    the bounds were machine-scaled.

    ``listExcludedPaths`` is the researcher's reviewed decision to omit
    named oversized files. It can only ever omit a member the bounds
    would have REFUSED outright (see ``_fbExcludeOversizedByRequest``),
    so it converts a dead end into a recorded partial snapshot and can
    never be used to hide an ordinary file from a council.
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
            dictBounds or agentCouncilCapacity.fdictFloorCouncilCapacity(),
            _fsetValidateExclusionRequest(listExcludedPaths),
        )
        dictIdentityAfter = _fdictReadRepositoryIdentity(
            connectionDocker, sContainerId, sRepoRoot,
        )
        _fnRefuseIncoherentCapture(dictIdentityBefore, dictIdentityAfter)
        _fnRefuseArchiveObservationMismatch(dictIdentityBefore, dictCapture)
        sArchivePath = os.path.join(
            sSnapshotDirectory, S_SNAPSHOT_ARCHIVE_BASENAME,
        )
        os.replace(sArchivePath + _S_PARTIAL_SUFFIX, sArchivePath)
        return _fdictWriteSnapshotManifest(
            sSnapshotDirectory, sContainerId, sRepoRoot, sCampaignId,
            dictIdentityBefore, dictIdentityAfter, sCaptureStartIso,
            dictCapture,
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
    """Return one full repository observation via the git authority.

    Carries the HEAD commit, a digest of the porcelain file-state map
    (an edit, add, or delete anywhere in the working tree changes it),
    and the per-path type/content identities of every present path the
    snapshot would include (see the module docstring's observation
    scope). No remote URL is read or recorded: a remote URL can embed
    a credential, and nothing secret may enter the manifest.
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
    dictObservation = connectionDocker.fdictFetchWorktreeIdentities(
        sContainerId, sRepoRoot,
    )
    if not dictObservation.get("bSuccess"):
        raise SnapshotRefusedError(
            "The per-path identity observation failed "
            f"({dictObservation.get('sReason') or 'no detail'}); a "
            "capture whose coherence cannot be established is refused."
        )
    dictPathIdentities = {
        sPath: dictPathIdentity
        for sPath, dictPathIdentity in sorted(
            dictObservation["dictPathIdentities"].items(),
        )
        if _ftFindExcludedComponent(sPath) is None
    }
    return {
        "sCommitSha": dictGitStatus.get("sHeadSha") or "",
        "sDirtyStateDigest": sDirtyStateDigest,
        "sObservedHeadSha": dictObservation.get("sHeadSha", ""),
        "sObservedPorcelainDigest": dictObservation.get(
            "sPorcelainDigest", ""),
        "dictPathIdentities": dictPathIdentities,
        # A LIST, not a set, because this dict is json.dumps'd into the
        # observation digest the manifest records. Carrying it there
        # rather than beside it means the ignore decision is pinned by
        # the same digest as everything else: a .gitignore rewritten
        # mid-capture changes the observation, and the pre/post
        # comparison refuses instead of sealing a snapshot whose
        # omissions nobody can reconstruct.
        "listIgnoredPaths": sorted(
            dictObservation.get("listIgnoredPaths") or []),
    }


def fsResolveSnapshotDirectory(sSnapshotStoreRoot, sCampaignId):
    """Return ``<store>/<campaign>/snapshot`` without creating anything.

    The layout is this module's, because this module is what writes it.
    It was being re-composed by hand wherever a reader needed the
    sealed archive or its manifest, which is a path spelled in several
    places and checked in none.
    """
    return os.path.join(sSnapshotStoreRoot, sCampaignId, "snapshot")


def fbaReadSealedSnapshotArchive(sSnapshotStoreRoot, sCampaignId):
    """Read a campaign's sealed snapshot tarball from host app-data.

    Every runner a campaign builds — a deliberation turn's, a baseline
    sandbox's, an ask-the-chairbot conversation's — is seeded from THIS
    archive rather than from a fresh read of the repository, which is
    what makes "the council reasoned about the repository as it stood
    at capture" a true statement rather than a hope.
    """
    with open(os.path.join(
            fsResolveSnapshotDirectory(sSnapshotStoreRoot, sCampaignId),
            S_SNAPSHOT_ARCHIVE_BASENAME), "rb") as fileSnapshot:
        return fileSnapshot.read()


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
    sSnapshotDirectory = fsResolveSnapshotDirectory(sStoreRoot, sCampaignId)
    if os.path.exists(sSnapshotDirectory):
        raise SnapshotRefusedError(
            f"A snapshot already exists for campaign {sCampaignId!r}; "
            "snapshots are immutable and are never overwritten."
        )
    os.makedirs(sSnapshotDirectory, mode=0o700)
    for sDirectory in (sStoreRoot, sCampaignDirectory, sSnapshotDirectory):
        os.chmod(sDirectory, 0o700)
    return sSnapshotDirectory


def _fsetValidateExclusionRequest(listExcludedPaths):
    """Return the requested exclusions as a set of repo-relative paths.

    The values arrive from an HTTP body, so they are validated on the
    same terms as an archive member: relative, normalized, no ``..``.
    A malformed entry is REFUSED rather than dropped -- silently
    ignoring one would produce a snapshot the researcher did not ask
    for, and the capture is the one place that mistake becomes
    permanent.
    """
    setExcluded = set()
    for sRequestedPath in listExcludedPaths or []:
        if not isinstance(sRequestedPath, str) or not sRequestedPath:
            raise SnapshotRefusedError(
                "An exclusion request must be a non-empty path; capture "
                "refused."
            )
        if sRequestedPath.startswith("/") or ".." in sRequestedPath.split(
                "/"):
            raise SnapshotRefusedError(
                f"Exclusion request {sRequestedPath!r} is refused: it "
                "must be a relative in-project path."
            )
        setExcluded.add(posixpath.normpath(sRequestedPath))
    return setExcluded


def _fdictStreamValidatedArchive(
    connectionDocker, sContainerId, sRepoRoot, sSnapshotDirectory,
    dictBounds, setExcludedPaths,
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
        "dictBounds": dictBounds,
        "setExcludedPaths": setExcludedPaths,
        "setHonouredExclusions": set(),
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
    if _fbExcludeOversizedByRequest(dictCapture, infoMember, sRelativePath):
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


def _fbExcludeOversizedByRequest(dictCapture, infoMember, sRelativePath):
    """Honour a researcher's exclusion, but ONLY for a refusing member.

    The size test is what keeps this feature what it says it is. An
    exclusion list that omitted any named path would be a general "hide
    this from the council" switch, and a council that can be shown a
    curated subset of a repository is worth less than no council: the
    one thing a participant cannot check is what it was not given. So a
    request for a member the capture would have accepted anyway is
    IGNORED, not honoured, and the member is captured normally.

    The bound is re-read from ``dictBounds`` rather than from the
    pre-flight's answer, so the file that is dropped is exactly the
    file that would otherwise have refused, on this machine, in this
    capture.
    """
    if not infoMember.isfile():
        return False
    if sRelativePath not in dictCapture["setExcludedPaths"]:
        return False
    iMemberBound = dictCapture["dictBounds"]["iMaxSnapshotMemberBytes"]
    if infoMember.size <= iMemberBound:
        return False
    dictCapture["dictOmissionReasons"].setdefault(
        sRelativePath,
        f"excluded by the researcher at convene time: {infoMember.size} "
        f"bytes, over this machine's {iMemberBound}-byte per-file limit",
    )
    dictCapture["setHonouredExclusions"].add(sRelativePath)
    return True


def _fnEnforceCaptureLimits(dictCapture, infoMember, sRelativePath):
    """Refuse the capture the moment any declared bound is exceeded."""
    dictBounds = dictCapture["dictBounds"]
    iMemberCount = dictCapture["iIncludedMemberCount"] + 1
    if iMemberCount > dictBounds["iMaxSnapshotFileCount"]:
        raise SnapshotRefusedError(
            f"The repository exceeds the snapshot member limit "
            f"({dictBounds['iMaxSnapshotFileCount']}); capture refused."
        )
    if infoMember.isfile():
        if infoMember.size > dictBounds["iMaxSnapshotMemberBytes"]:
            raise SnapshotRefusedError(
                f"File {sRelativePath!r} is {infoMember.size} bytes, "
                f"over the per-file limit "
                f"({dictBounds['iMaxSnapshotMemberBytes']}); capture "
                "refused."
            )
        if (
            dictCapture["iTotalContentBytes"] + infoMember.size
            > dictBounds["iMaxSnapshotTotalBytes"]
        ):
            raise SnapshotRefusedError(
                f"The repository exceeds the total snapshot size limit "
                f"({dictBounds['iMaxSnapshotTotalBytes']} bytes); capture "
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
        "sGitBlobSha": _fsComputeGitBlobIdentity(baContent),
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
    """Refuse a capture the repository changed underneath, naming the tear."""
    sTornProperty = _fsFindTornIdentityProperty(
        dictIdentityBefore, dictIdentityAfter,
    )
    if sTornProperty is None:
        return
    raise SnapshotRefusedError(
        "The project repository changed while the snapshot was "
        f"streaming: {sTornProperty}. The partial capture is "
        "discarded. Re-run the capture when the project is quiet -- "
        "the bounded project lock that will serialize capture against "
        "pipeline work is controller (R1b) wiring."
    )


def _fsFindTornIdentityProperty(dictIdentityBefore, dictIdentityAfter):
    """Name the first pre/post observation property that differs, or None."""
    if dictIdentityBefore["sCommitSha"] != dictIdentityAfter["sCommitSha"]:
        return (
            "the HEAD commit moved from "
            f"{dictIdentityBefore['sCommitSha'] or '(none)'} to "
            f"{dictIdentityAfter['sCommitSha'] or '(none)'}"
        )
    if (
        dictIdentityBefore["sDirtyStateDigest"]
        != dictIdentityAfter["sDirtyStateDigest"]
    ):
        return "the porcelain working-tree state digest differs"
    dictPathsBefore = dictIdentityBefore["dictPathIdentities"]
    dictPathsAfter = dictIdentityAfter["dictPathIdentities"]
    if set(dictPathsBefore) != set(dictPathsAfter):
        listTornPaths = sorted(set(dictPathsBefore) ^ set(dictPathsAfter))
        return (
            "the changed-path set differs "
            f"({', '.join(repr(sPath) for sPath in listTornPaths[:3])})"
        )
    for sPath in sorted(dictPathsBefore):
        dictBefore = dictPathsBefore[sPath]
        dictAfter = dictPathsAfter[sPath]
        if dictBefore["sType"] != dictAfter["sType"]:
            return (
                f"path {sPath!r} changed type from "
                f"{dictBefore['sType']} to {dictAfter['sType']}"
            )
        if dictBefore["sIdentity"] != dictAfter["sIdentity"]:
            if dictBefore["sType"] == "symlink":
                return f"the symlink target of {sPath!r} changed"
            return f"the content identity of {sPath!r} changed"
    return None


def _fnRefuseArchiveObservationMismatch(dictIdentityBefore, dictCapture):
    """Refuse when the archive disagrees with the pre-capture observation.

    Ties what the archive CONTAINS to an identity observed outside the
    stream, which is what catches a change-then-revert: pre equals
    post, but the archive holds the intermediate bytes. EVERY member
    is matched, in both directions: each observed present path's
    member must carry the observed blob identity or symlink target,
    and each file or symlink member must be an observed path — a
    member the observation never saw (created and deleted between the
    observations, or a submodule's un-enumerable files) refuses
    outright. The old changed-paths-only match left clean tracked
    files pinned by nothing an archive could contradict; the full
    present-path observation closes that.

    A researcher-excluded oversized file is observed and deliberately
    absent, so it is exempted from the first direction only. The
    exemption is narrow by construction: a path reaches
    ``setHonouredExclusions`` only after the stream saw a member that
    genuinely breached the per-file bound, so this cannot be widened
    from the outside into "any path the caller names may go missing".
    The reverse direction is NOT relaxed — an excluded path that
    somehow appeared in the archive would still have to match its
    observed identity.
    """
    dictEntriesByPath = {
        dictEntry["sPath"]: dictEntry
        for dictEntry in dictCapture["listIncludedEntries"]
    }
    dictObservedPaths = dictIdentityBefore["dictPathIdentities"]
    for sPath, dictObserved in sorted(dictObservedPaths.items()):
        if dictObserved["sType"] == "missing":
            continue
        if sPath in dictCapture["setHonouredExclusions"]:
            continue
        sMismatch = _fsDescribeArchiveEntryMismatch(
            sPath, dictObserved, dictEntriesByPath.get(sPath),
        )
        if sMismatch is not None:
            raise SnapshotRefusedError(
                "The snapshot archive disagrees with the pre-capture "
                f"observation: {sMismatch}. The repository changed "
                "while the daemon was serializing it; the partial "
                "capture is discarded."
            )
    for sPath, dictEntry in sorted(dictEntriesByPath.items()):
        if dictEntry["sType"] not in ("file", "symlink"):
            continue
        if sPath not in dictObservedPaths:
            raise SnapshotRefusedError(
                f"The snapshot archive holds a {dictEntry['sType']} at "
                f"{sPath!r} that the pre-capture observation never "
                "saw; an unobserved member cannot be coherence-pinned, "
                "so the partial capture is discarded."
            )


def _fsDescribeArchiveEntryMismatch(sPath, dictObserved, dictEntry):
    """Return prose for one archive-versus-observation tear, or None."""
    if dictEntry is None:
        return f"observed path {sPath!r} is absent from the archive"
    if dictObserved["sIdentity"] == "unreadable":
        return f"path {sPath!r} could not be content-identified by git"
    if dictObserved["sType"] != dictEntry["sType"]:
        return (
            f"path {sPath!r} was observed as a {dictObserved['sType']} "
            f"but the archive holds a {dictEntry['sType']}"
        )
    if dictObserved["sType"] == "file" and (
        dictEntry.get("sGitBlobSha") != dictObserved["sIdentity"]
    ):
        return (
            f"the archive's content identity of {sPath!r} does not "
            "match the pre-capture observation (the archive holds "
            "intermediate bytes -- a change-then-revert)"
        )
    if dictObserved["sType"] == "symlink" and (
        dictEntry.get("sLinkTarget") != dictObserved["sIdentity"]
    ):
        return (
            f"the symlink target of {sPath!r} in the archive does not "
            "match the pre-capture observation"
        )
    return None


def _fsComputeGitBlobIdentity(baContent):
    """Return git's blob sha1 for raw bytes (hash-object --no-filters)."""
    baHeader = b"blob " + str(len(baContent)).encode("ascii") + b"\x00"
    return hashlib.sha1(baHeader + baContent).hexdigest()


def fsComputePathIdentitiesDigest(dictPathIdentitiesRaw):
    """Digest the non-excluded per-path identity map, for staleness.

    The one comparison the porcelain digest cannot make: a DIRTY file
    whose content changes again moves no porcelain line (v2 status
    hashes index and HEAD objects, never worktree bytes), but it moves
    this digest, because the map carries the raw-worktree blob identity
    of every present path. Filters excluded components itself so the
    capture-time manifest field and the poll-time recomputation agree
    on scope by construction, whether the caller pre-filtered or not.
    """
    dictFiltered = {
        sPath: dictPathIdentity
        for sPath, dictPathIdentity in sorted(dictPathIdentitiesRaw.items())
        if _ftFindExcludedComponent(sPath) is None
    }
    return hashlib.sha256(
        json.dumps(dictFiltered, sort_keys=True).encode("utf-8"),
    ).hexdigest()


def _fsComputeObservationDigest(dictIdentity):
    """Return the sha256 the manifest records for one observation.

    The manifest carries only this digest -- never the observation's
    path list, blob identities, or symlink targets -- so nothing about
    the repository's contents enters the manifest beyond what the
    included entries already state.
    """
    return hashlib.sha256(
        json.dumps(dictIdentity, sort_keys=True).encode("utf-8"),
    ).hexdigest()


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
    dictIdentityBefore, dictIdentityAfter, sCaptureStartIso, dictCapture,
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
        "sCommitSha": dictIdentityBefore["sCommitSha"],
        "sDirtyStateDigest": dictIdentityBefore["sDirtyStateDigest"],
        "sBaselineHeadSha": dictIdentityBefore["sObservedHeadSha"],
        "sBaselinePorcelainDigest": dictIdentityBefore[
            "sObservedPorcelainDigest"],
        "sBaselinePathIdentitiesDigest": fsComputePathIdentitiesDigest(
            dictIdentityBefore["dictPathIdentities"]),
        "sPreObservationDigest": _fsComputeObservationDigest(
            dictIdentityBefore,
        ),
        "sPostObservationDigest": _fsComputeObservationDigest(
            dictIdentityAfter,
        ),
        "sCaptureStartIso": sCaptureStartIso,
        "sCaptureEndIso": datetime.now(timezone.utc).isoformat(),
        "iIncludedMemberCount": dictCapture["iIncludedMemberCount"],
        "iTotalContentBytes": dictCapture["iTotalContentBytes"],
        # What the researcher chose to leave out, recorded separately
        # from the policy omissions above it: a reader asking "was this
        # council shown the whole repository?" must not have to tell a
        # standing policy exclusion from a one-off human decision.
        "listResearcherExcludedPaths": sorted(
            dictCapture["setHonouredExclusions"]),
        # Which INCLUDED paths git does not track. The snapshot carries
        # them (ruling 2026-08-24), and a participant reasoning about
        # reproducibility cannot recover the fact from the tree — a
        # build artifact and a source file look identical once copied.
        "listGitIgnoredPaths": list(dictIdentityBefore["listIgnoredPaths"]),
        # The bounds ENFORCED, not the machine that produced them: they
        # explain why a member was refused or excluded, where the host's
        # RAM would only describe the laptop.
        "dictBoundsApplied": {
            sBoundName: dictCapture["dictBounds"][sBoundName]
            for sBoundName in (
                "iMaxSnapshotFileCount", "iMaxSnapshotMemberBytes",
                "iMaxSnapshotTotalBytes")
        },
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


def fdictAssessSnapshotFeasibility(connectionDocker, sContainerId,
                                   sProjectRepoPath, dictBounds=None):
    """Report, from METADATA, whether this repo could be snapshotted.

    The capture bounds are enforced mid-stream, so a repository that
    breaches them is refused only after the researcher has chosen
    participants and written a question — and the question is the
    expensive part, the researcher's actual thinking. This answers the
    same question from a metadata walk instead, cheaply enough to run
    when a project is opened.

    It is deliberately ADVISORY and deliberately not authoritative: the
    bounds are re-enforced during the real capture, because a walk is a
    different reading of the tree than a tar stream (a member can grow
    between them, and the per-file bound is only knowable member by
    member). A "fits" answer here is "nothing obvious stops you", never
    a promise; a "does not fit" answer is reliable, because the two
    totals it compares only grow as the capture proceeds.
    """
    dictBounds = dictBounds or agentCouncilCapacity.fdictResolveCouncilCapacity(
        connectionDocker)
    dictWeight = connectionDocker.fdictWeighRepository(
        sContainerId, sProjectRepoPath)
    listOversizedFiles = [
        dictFile for dictFile in dictWeight.get("listLargestFiles", [])
        if dictFile["iSizeBytes"] > dictBounds["iMaxSnapshotMemberBytes"]
    ]
    listReasons = []
    if dictWeight["bTruncated"] or (
            dictWeight["iFileCount"] > dictBounds["iMaxSnapshotFileCount"]):
        listReasons.append(
            f"it holds {'over ' if dictWeight['bTruncated'] else ''}"
            f"{dictWeight['iFileCount']} files, above the "
            f"{dictBounds['iMaxSnapshotFileCount']} a council snapshot "
            "accepts")
    if dictWeight["iTotalBytes"] > dictBounds["iMaxSnapshotTotalBytes"]:
        listReasons.append(
            f"it holds {dictWeight['iTotalBytes'] // (1024 * 1024)} MB, "
            f"above the "
            f"{dictBounds['iMaxSnapshotTotalBytes'] // (1024 * 1024)} MB "
            "a council snapshot accepts")
    # The per-file bound was MISSING from the first pre-flight, which
    # checked two of the three bounds the capture enforces. A research
    # repository comfortably inside the count and the total still hit
    # the member cap at convene time on one 85 MB data file — exactly
    # the late refusal this pre-flight exists to prevent (2026-08-22).
    if listOversizedFiles:
        listReasons.append(_fsDescribeOversizedFiles(
            listOversizedFiles, dictWeight, dictBounds))
    listReasons.extend(_flistDescribeStructuralRefusals(dictWeight))
    return {
        "bFits": not listReasons,
        # Every reason is an oversized FILE, so the researcher has a
        # move: exclude them and convene anyway. Distinguished from the
        # count and total bounds, where no per-file choice helps and the
        # honest answer is that the repository is the wrong shape.
        "bResolvableByExcludingFiles": bool(
            listOversizedFiles) and len(listReasons) == 1,
        "listOversizedFiles": listOversizedFiles,
        "bOversizedListTruncated": bool(
            dictWeight.get("bLargestFilesTruncated")) and bool(
                listOversizedFiles),
        "iFileCount": dictWeight["iFileCount"],
        "iTotalBytes": dictWeight["iTotalBytes"],
        "bTruncated": dictWeight["bTruncated"],
        "iMaxSnapshotMemberBytes": dictBounds["iMaxSnapshotMemberBytes"],
        "sReason": (
            "" if not listReasons else
            "This project cannot be snapshotted for a council because "
            + "; and ".join(listReasons)
            + ". A council ships an immutable copy of the repository to "
            "each participant, so the whole directory has to fit — "
            "including files git ignores, which is usually what makes "
            "a research repository too large."),
    }


def _flistDescribeStructuralRefusals(dictWeight):
    """Describe the capture's NON-SIZE refusals the walk can foresee.

    The pre-flight began as a size check, which left the researcher to
    discover every other refusal at convene time — the exact complaint
    the size check existed to answer, just narrower. These three are
    properties of the tree as it sits, so they can be told in advance:
    a symlink out of the repository, a file type a tar snapshot cannot
    represent, and a checked-out submodule (whose files no superproject
    git command enumerates, so every one of them refuses as an
    unobserved member).

    What deliberately remains undetectable is the coherence tear: the
    repository changing WHILE the daemon serializes it is a race, and a
    pre-flight that claimed to foresee it would be lying.
    """
    listReasons = []
    for dictLink in dictWeight.get("listEscapingSymlinks") or []:
        listReasons.append(
            f"its symlink {dictLink['sPath']} points outside the "
            f"repository (at {dictLink['sTarget'] or 'nothing'}), and a "
            "council snapshot must be self-contained")
    for sSpecialPath in dictWeight.get("listSpecialFiles") or []:
        listReasons.append(
            f"{sSpecialPath} is a device, socket or pipe, which a "
            "snapshot cannot represent")
    for sSubmodulePath in dictWeight.get("listSubmodules") or []:
        listReasons.append(
            f"{sSubmodulePath} is a git submodule, and a submodule's "
            "files are enumerated by no superproject git command, so "
            "their contents cannot be pinned to an observation")
    return listReasons


def _fsDescribeOversizedFiles(listOversizedFiles, dictWeight, dictBounds):
    """Name the files over the per-member bound, or count them.

    Naming the file is the difference between a researcher who can act
    and one who cannot: the bound is a number, but "which file" is the
    question they actually have. Past a handful, the names stop helping
    and the count is the information.
    """
    iLimitMegabytes = dictBounds["iMaxSnapshotMemberBytes"] // (1024 * 1024)
    sSuffix = (
        f", above the {iLimitMegabytes} MB a single snapshot member "
        "accepts on this machine")
    if len(listOversizedFiles) > 3:
        return (
            f"{'at least ' if dictWeight.get('bLargestFilesTruncated') else ''}"
            f"{len(listOversizedFiles)} of its files are individually over "
            f"{iLimitMegabytes} MB, the most a single snapshot member "
            "accepts on this machine")
    return "; and ".join(
        f"its file {dictFile['sPath']} is "
        f"{dictFile['iSizeBytes'] // (1024 * 1024)} MB{sSuffix}"
        for dictFile in listOversizedFiles)
