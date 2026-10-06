"""The host-mode twin of DockerConnection: same duck type, host execution.

A host project runs its pipeline directly on the host machine, so this
class implements the connection surface the rest of the hub already
speaks — the exec trio, the typed reads, the file writes — against the
host filesystem and ``subprocess`` instead of the Docker daemon. The
resource id in every signature is the project's registry NAME (host
projects have no Docker id), resolved to a project directory through
the registry.

Three properties are the contract, in priority order:

1. **Every subprocess is gated and journaled** (host-mode plan §4-5,
   ruling 12). The child is spawned SUSPENDED behind a stdin gate in
   its own session; a ``host-exec`` journal record carrying its
   recycle-proof identity (PID + process group + in-flight stamp) is
   persisted and identity-gated; only then is the gate released. A
   crash at any point leaves an identified, probeable record — never a
   process nobody can name. This module is the ONLY place under
   ``vaibify/host/`` that may launch a subprocess.
2. **Every direct path argument and working directory is validated**
   against exactly two roots before any open or exec: the project
   directory and the project's host-diagnostics scratch subtree. The
   guard defends against hostile wire input (traversal, absolute
   smuggling, symlink escape at validation time); it deliberately
   cannot see paths embedded inside opaque workflow shell text — the
   host-mode warning modal owns that disclosure.
3. **The mutation-admission gates run unchanged.** The same
   ``fnAssertContainerCommandAdmitted`` / ``fnAssertContainerWriteAdmitted``
   / ``fnAssertDurableExecAdmitted`` calls the Docker leg makes run
   here, before any byte lands or any process starts, so a route that
   forgets its carrier raises ``MutationNotAdmittedError`` identically
   in both modes.

   With ONE named exception, mirroring the Docker leg's:
   :meth:`HostConnection._ftRunTypedReadProgram` asserts no admission,
   because a five-second poll cannot hold the mutation drain without
   making Run Step refuse at random. It is the host's single grant
   point, it takes an operation NAME from a fixed table and builds the
   command itself, and it is still gated and journaled — the record is
   never what is skipped.

What this class deliberately does NOT implement: container discovery
(the connection router answers it from the registry) and the root
shell probe (nothing host-mode needs root — the host user IS the
user). The terminal PTY cluster IS here (2026-08-15): the suspended
shell launch, and the session-wide signal/probe pair the drain
machinery is duck-typed over.
"""

__all__ = [
    "HostConnection",
    "HostPathOutsideProjectError",
    "UnknownHostProjectError",
    "F_DEFAULT_HOST_EXEC_TIMEOUT_SECONDS",
]

import hashlib
import json
import os
import pty
import shlex
import subprocess
import sys
import tempfile
import threading
import time

from vaibify.config import mutationAdmission
from vaibify.config import processLiveness
from vaibify.docker.confinedWrite import (
    ContainerWriteExistsError,
    ContainerWriteRefusedError,
)
from vaibify.docker.dockerConnection import (
    ExecResult,
    I_MAX_SMALL_FILE_BYTES,
    S_TYPED_READ_GIT_REPO_STATUS,
    fsRenderBatchedTypedReadProgram,
)
from vaibify.host import hostConfinedRead
from vaibify.host.hostCancellation import (
    fbProcessGroupProvedEmpty,
    fnSignalSessionMembers,
    fnTerminateProcessGroup,
)
from vaibify.host.hostScratch import fsHostScratchRootForProject

I_MAX_FETCH_FILE_BYTES = 64 * 1024 * 1024
I_STREAM_CHUNK_BYTES = 1048576
F_DEFAULT_HOST_EXEC_TIMEOUT_SECONDS = 300.0
# A typed read answers a poll, so it is bounded far tighter than an
# ordinary command: a read that has not finished in this long is not
# going to make the next tick either, and the poll's honest answer is
# that it could not read.
F_TYPED_READ_TIMEOUT_SECONDS = 60.0
I_NEW_FILE_MODE = 0o644
S_STAGING_PREFIX = ".vaibify-write-"

# The child blocks on its stdin until the parent has journaled its
# identity, then becomes the command via exec — so the command's first
# instruction cannot run before the record that names the process
# exists. The host analogue of Docker's create -> journal -> start.
# It is a /bin/bash script, not a python -c stub, deliberately: the
# gate needs only "read a line, then exec", and a python interpreter
# boot (~0.2s on a framework macOS build, vs ~0.01s for bash) taxed
# EVERY host command with it — the promotion hand-off alone runs ~16
# gated launches serially, and that tax was most of its multi-second
# stall (2026-08-20). The command rides in as "$1", data the outer
# bash never parses; exec keeps the pid, so the journaled identity
# survives unchanged.
_S_GATED_LAUNCH_STUB = (
    "read -r _sGate\n"
    "exec /bin/bash -c \"$1\"\n"
)

# The terminal's launch stub. The gate rides a dedicated pipe (its fd
# number arrives in argv — ``pass_fds`` preserves numbers, it does not
# renumber) because the child's stdin IS the PTY, and a gate byte
# through the PTY would echo into the researcher's terminal. After the
# gate: ``setsid`` makes the journaled pid the session id, and
# re-opening the tty as the fresh session's leader acquires it as the
# CONTROLLING terminal — which is what gives bash working job control,
# and job control is why the containment probe must match the SESSION,
# not the group (verified live: a backgrounded job wears its own
# pgid). SIGINT and SIGQUIT are reset to their defaults first: a hub
# launched in the background starts with both ignored, an ignored
# signal survives exec, and the shell's jobs would then never take
# Ctrl-C or the Kill button.
_S_TERMINAL_LAUNCH_STUB = (
    "import os,signal,sys\n"
    "signal.signal(signal.SIGINT,signal.SIG_DFL)\n"
    "signal.signal(signal.SIGQUIT,signal.SIG_DFL)\n"
    "os.read(int(sys.argv[1]),1)\n"
    "os.setsid()\n"
    "iTty = os.open(os.ttyname(0), os.O_RDWR)\n"
    "os.close(iTty)\n"
    "os.execv('/bin/bash', ['/bin/bash', '-i'])\n"
)


class HostPathOutsideProjectError(RuntimeError):
    """A host path argument escaped the project and scratch roots."""


class UnknownHostProjectError(RuntimeError):
    """The resource id does not name a registered host project."""


def _fiResolveWriteMode(sRealPath, iMode):
    """Return ``iMode``, else the replaced file's own mode, else 0644."""
    if iMode is not None:
        return iMode
    try:
        return os.stat(sRealPath).st_mode & 0o7777
    except FileNotFoundError:
        return I_NEW_FILE_MODE


def _fnUnlinkQuietly(sPath):
    try:
        os.unlink(sPath)
    except OSError:
        pass


def _fnCopyStreamBounded(fileSource, fileStaged, iExpectedBytes):
    """Copy in chunks; refuse a stream whose length differs from stated."""
    iReceived = 0
    for baChunk in iter(lambda: fileSource.read(I_STREAM_CHUNK_BYTES), b""):
        iReceived += len(baChunk)
        if iExpectedBytes is not None and iReceived > iExpectedBytes:
            break
        fileStaged.write(baChunk)
    if iExpectedBytes is not None and iReceived != iExpectedBytes:
        raise ContainerWriteRefusedError(
            f"Write refused: {iReceived} bytes arrived but "
            f"{iExpectedBytes} were expected"
        )


def _fnPublishStagedFile(sTempPath, sRealPath, bReplaceAllowed):
    """Rename the staged file into place, atomically refusing a taken name.

    Replacement is a rename. Forbidding it is a hard link, which fails if
    the name exists at the instant of publishing -- an existence check
    made earlier cannot see a file that appeared during a long upload.
    A filesystem without hard links falls back to check-then-rename.
    """
    if bReplaceAllowed:
        os.rename(sTempPath, sRealPath)
        return
    try:
        os.link(sTempPath, sRealPath)
    except FileExistsError as error:
        raise ContainerWriteExistsError(
            f"Write to {sRealPath} refused: it already exists"
        ) from error
    except OSError:
        if os.path.lexists(sRealPath):
            raise ContainerWriteExistsError(
                f"Write to {sRealPath} refused: it already exists"
            )
        os.rename(sTempPath, sRealPath)
        return
    os.unlink(sTempPath)


def _fsResolveRegisteredHostProjectRoot(sResourceId):
    """Return the project directory for a registered host project."""
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(sResourceId)
    if dictProject is None or dictProject.get("sMode") != "host":
        raise UnknownHostProjectError(
            f"'{sResourceId}' is not a registered host project"
        )
    return dictProject["sDirectory"]


class HostConnection:
    """Duck-typed connection executing against the host filesystem."""

    def __init__(self, fnResolveProjectRoot=None):
        self._fnResolveProjectRoot = (
            fnResolveProjectRoot or _fsResolveRegisteredHostProjectRoot
        )

    # -----------------------------------------------------------------
    # The path guard (host-mode plan §8).
    # -----------------------------------------------------------------

    def _fsValidateHostPath(self, sResourceId, sPath):
        """Return the realpath of a path proven inside the two roots.

        Symlinks are resolved BEFORE containment is checked, so a link
        inside the project pointing outside it fails closed. The
        ``os.sep`` suffix on the prefix comparison is load-bearing:
        without it, a sibling directory whose name extends the root's
        (``projectX`` vs ``projectXY``) would pass.

        A RELATIVE path resolves against the project root, and that is
        the container leg's behaviour rather than a concession. Docker
        exec runs with the image's working directory, so a
        repo-relative path like ``MakeNumbers/analysis.py`` — which is
        the wire contract for every step directory, output and script —
        has always resolved against the container root. Refusing it
        here made the same connection answer the same input two
        different ways depending on the leg, which is the bug; the file
        poll hit it on the first host workflow ever opened.

        Nothing is given up by admitting it. The join happens BEFORE
        the containment check, so ``../../etc/passwd`` becomes an
        absolute path outside the roots and is refused exactly as its
        absolute spelling is.
        """
        sProjectRoot = os.path.realpath(
            self._fnResolveProjectRoot(sResourceId),
        )
        if not os.path.isabs(sPath):
            sPath = os.path.join(sProjectRoot, sPath)
        sRealPath = os.path.realpath(sPath)
        sScratchRoot = fsHostScratchRootForProject(sProjectRoot)
        for sAllowedRoot in (sProjectRoot, sScratchRoot):
            if sRealPath == sAllowedRoot or sRealPath.startswith(
                sAllowedRoot + os.sep,
            ):
                return sRealPath
        raise HostPathOutsideProjectError(
            f"Path escapes the project and scratch roots: {sPath!r}"
        )

    # -----------------------------------------------------------------
    # Typed reads: direct os.* calls. No subprocess, so these enter no
    # exemption at all -- there is no admission here to suppress. The
    # one read that DOES need a subprocess (git status, below) has its
    # own named grant point.
    # -----------------------------------------------------------------

    def fbaFetchFile(
        self, sContainerId, sFilePath, iMaxBytes=I_MAX_FETCH_FILE_BYTES,
    ):
        """Return a small file's bytes; ``ValueError`` beyond the cap."""
        sRealPath = self._fsValidateHostPath(sContainerId, sFilePath)
        try:
            with open(sRealPath, "rb") as fileHandle:
                baContent = fileHandle.read()
        except (OSError, IsADirectoryError) as error:
            raise FileNotFoundError(
                f"Cannot read host file: {sFilePath}"
            ) from error
        if iMaxBytes is not None and len(baContent) > iMaxBytes:
            raise ValueError(
                f"File exceeds fbaFetchFile cap "
                f"({len(baContent)} > {iMaxBytes} bytes): {sFilePath}; "
                "use fiterStreamFile for large files"
            )
        return baContent

    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        """Return the names directly inside a host directory."""
        sRealPath = self._fsValidateHostPath(sContainerId, sDirectoryPath)
        try:
            return sorted(os.listdir(sRealPath))
        except (NotADirectoryError, OSError) as error:
            raise FileNotFoundError(
                f"Cannot list host directory: {sDirectoryPath}"
            ) from error

    def fbContainerPathIsFile(self, sContainerId, sPath):
        """Return True iff the host path is an existing regular file."""
        return os.path.isfile(self._fsValidateHostPath(sContainerId, sPath))

    def fbContainerPathIsDirectory(self, sContainerId, sPath):
        """Return True iff the host path is an existing directory."""
        return os.path.isdir(self._fsValidateHostPath(sContainerId, sPath))

    def flistContainerPathsExist(self, sContainerId, listPaths):
        """Return one exists/absent answer per path, in the order given."""
        return [
            os.path.exists(self._fsValidateHostPath(sContainerId, sPath))
            for sPath in listPaths
        ]

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        """Return one is-a-directory answer per path, in the order given."""
        return [
            os.path.isdir(self._fsValidateHostPath(sContainerId, sPath))
            for sPath in listPaths
        ]

    def fdictHashContainerRepoPaths(
        self, sContainerId, sRootPath, listRelPaths,
    ):
        """Hash repo-relative host files with the shared entry shape.

        The host twin of the container leg's typed read, answering the
        one contract both legs serve. It delegates to the host repo
        adapter, whose symlink-segment and realpath-containment
        enforcement is the same rule the container program applies —
        after this leg's own path guard has vetted the root.
        """
        from vaibify.reproducibility.repoFiles import HostRepoFiles
        sRootReal = self._fsValidateHostPath(sContainerId, sRootPath)
        return HostRepoFiles(sRootReal).fdictHashFiles(
            list(listRelPaths),
        )

    def fdictFetchSmallFiles(self, sContainerId, listPaths):
        """Return ``{sPath: bytes or None}`` for small host files.

        The host twin of the container leg's batched read, on its terms:
        ``None`` for a file that cannot be opened, ``ValueError`` for one
        over the ceiling, and every path through this leg's guard first.
        Keyed by the path the caller gave, as the mtime read is.
        """
        dictFiles = {}
        for sPath in listPaths:
            sRealPath = self._fsValidateHostPath(sContainerId, sPath)
            try:
                with open(sRealPath, "rb") as fileHandle:
                    baContent = fileHandle.read(I_MAX_SMALL_FILE_BYTES + 1)
            except OSError:
                dictFiles[sPath] = None
                continue
            if len(baContent) > I_MAX_SMALL_FILE_BYTES:
                raise ValueError(
                    f"{sPath} exceeds the {I_MAX_SMALL_FILE_BYTES}-byte "
                    "small-file ceiling."
                )
            dictFiles[sPath] = baContent
        return dictFiles

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        """Return ``{sPath: sMtime}`` for the host paths that exist.

        Keyed by the path the CALLER gave, not by the realpath the
        guard resolved: the file panel looks its answers up by the
        path it asked about, and a symlinked project directory would
        otherwise hand back keys that match nothing.

        Seconds truncated to an integer string, matching the container
        leg — the two legs answer one contract, and a host project
        whose mtimes carried sub-second precision would compare
        unequal against a cache written by the same dashboard.
        """
        dictMtimes = {}
        for sPath in listPaths:
            try:
                tStat = os.stat(
                    self._fsValidateHostPath(sContainerId, sPath),
                )
            except OSError:
                continue
            dictMtimes[sPath] = str(int(tStat.st_mtime))
        return dictMtimes

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        """Return the host file's sha256 hex digest, or ``''``.

        Empty when unreadable, on the same terms as the container leg:
        no fingerprint means "cannot compare", which is the ordinary
        answer for a workflow whose file does not exist yet.

        Hashed in chunks, as the container program does, so the answer
        does not depend on the file's size. It used to read the file
        through the capped fetch and answer ``''`` past 64 MiB, which
        made the write-ahead journal's prior hash "unproven" for exactly
        the large files a replacement is most likely to interrupt.
        """
        sRealPath = self._fsValidateHostPath(sContainerId, sPath)
        hasherFile = hashlib.sha256()
        try:
            with open(sRealPath, "rb") as fileHandle:
                for baChunk in iter(
                    lambda: fileHandle.read(I_STREAM_CHUNK_BYTES), b"",
                ):
                    hasherFile.update(baChunk)
        except OSError:
            return ""
        return hasherFile.hexdigest()

    def fsReadClockUtc(self, sContainerId):
        """Return the host's wall clock as ``YYYY-MM-DD HH:MM:SS UTC``.

        A host project's files live on the machine the hub runs on, so
        the hub's clock IS the clock that stamps them.
        """
        del sContainerId
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    def fdictReadFilesystemUsage(self, sContainerId, sPath):
        """Return total/used/free bytes for the filesystem holding a path."""
        sRealPath = self._fsValidateHostPath(sContainerId, sPath)
        try:
            tStatvfs = os.statvfs(sRealPath)
        except OSError as error:
            raise FileNotFoundError(
                f"Cannot stat host filesystem: {sPath}"
            ) from error
        return {
            "iTotalBytes": tStatvfs.f_blocks * tStatvfs.f_frsize,
            "iUsedBytes": (
                (tStatvfs.f_blocks - tStatvfs.f_bfree)
                * tStatvfs.f_frsize
            ),
            "iFreeBytes": tStatvfs.f_bavail * tStatvfs.f_frsize,
        }

    def fiterStreamFile(
        self, sContainerId, sFilePath, iChunkSizeBytes=I_STREAM_CHUNK_BYTES,
    ):
        """Yield the host file's bytes in bounded chunks."""
        sRealPath = self._fsValidateHostPath(sContainerId, sFilePath)
        with open(sRealPath, "rb") as fileHandle:
            while True:
                baChunk = fileHandle.read(iChunkSizeBytes)
                if not baChunk:
                    return
                yield baChunk

    def fiterReadFileConfined(
        self, sContainerId, sFilePath, sAuthorizedRoot=None,
    ):
        """Yield a host file's bytes in chunks; the twin of the Docker leg's.

        The shared path guard runs first, so every hostile path is
        refused exactly as every other host read refuses it. The read
        itself then walks the path with ``O_NOFOLLOW`` against held
        descriptors (:mod:`vaibify.host.hostConfinedRead`), so a
        component swapped after the guard ran cannot redirect it. A
        final link is followed only if it stays inside the root.
        """
        sRealRoot, sAbsolutePath = self._ftResolveConfinedReadTarget(
            sContainerId, sFilePath, sAuthorizedRoot,
        )
        yield from hostConfinedRead.fiterStreamFileInsideRoot(
            sRealRoot, sAbsolutePath,
        )

    def fiterReadDirectoryAsTar(
        self, sContainerId, sDirectoryPath, sAuthorizedRoot=None,
    ):
        """Yield a tar of a host directory; links stay links, none followed."""
        sRealRoot, sAbsolutePath = self._ftResolveConfinedReadTarget(
            sContainerId, sDirectoryPath, sAuthorizedRoot,
        )
        yield from hostConfinedRead.fiterStreamDirectoryAsTar(
            sRealRoot, sAbsolutePath,
        )

    def _ftResolveConfinedReadTarget(
        self, sContainerId, sPath, sAuthorizedRoot,
    ):
        """Return ``(real root, path spelled under it)`` for a confined read.

        The path guard (:meth:`_fsValidateHostPath`) refuses anything
        outside the project, links included. What it returns is a
        resolved path, which would hide a final link from the walk, so
        the path handed on is the one the caller NAMED, made absolute
        and re-spelled under the real root.
        """
        self._fsValidateHostPath(sContainerId, sPath)
        sProjectRoot = self._fnResolveProjectRoot(sContainerId)
        sRealProjectRoot = os.path.realpath(sProjectRoot)
        sRealRoot = (
            self._fsValidateHostPath(sContainerId, sAuthorizedRoot)
            if sAuthorizedRoot else sRealProjectRoot
        )
        sAbsolute = os.path.normpath(
            sPath if os.path.isabs(sPath)
            else os.path.join(sRealProjectRoot, sPath)
        )
        for sSpelling in (
            os.path.normpath(sAuthorizedRoot or sProjectRoot), sRealRoot,
        ):
            if sAbsolute == sSpelling or sAbsolute.startswith(
                sSpelling + os.sep,
            ):
                return sRealRoot, sRealRoot + sAbsolute[len(sSpelling):]
        raise HostPathOutsideProjectError(
            f"Path is outside the authorized root: {sPath!r}"
        )

    # -----------------------------------------------------------------
    # File writes: atomic, mode-preserving, admission-gated.
    # -----------------------------------------------------------------

    def fnWriteFile(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Write bytes atomically; the write is the real primitive here.

        On the Docker leg ``fnWriteFile`` is the facade and the tar
        writer does the work; on the host the facade direction reverses
        and :meth:`fnWriteFileViaTar` (kept only for the duck type)
        delegates HERE. ``iUid``/``iGid`` are accepted for signature
        parity and ignored — the writer already IS the user, and there
        is no privilege to drop. ``iMode`` wins when given; otherwise a
        replaced file keeps its own mode (a replaced executable script
        keeps its executable bit) and a new file lands 0644. The mode
        is applied to the temp file BEFORE the rename, so no reader
        ever sees the target with interim permissions.

        ``sAuthorizedRoot``/``tForbiddenNames`` are accepted for the
        duck type the container leg confines paths with; a host write is
        confined by :meth:`_fsValidateHostPath` and by the route's own
        lexical denylist, so they are not consulted here.
        """
        del iUid, iGid, sAuthorizedRoot, tForbiddenNames
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFile",
        )
        sRealPath = self._fsValidateHostPath(sContainerId, sFilePath)
        iEffectiveMode = _fiResolveWriteMode(sRealPath, iMode)
        iDescriptor, sTempPath = tempfile.mkstemp(
            dir=os.path.dirname(sRealPath),
        )
        try:
            os.fchmod(iDescriptor, iEffectiveMode)
            os.write(iDescriptor, baContent)
            os.fsync(iDescriptor)
            os.close(iDescriptor)
            os.rename(sTempPath, sRealPath)
        except Exception:
            try:
                os.close(iDescriptor)
            except OSError:
                pass
            _fnUnlinkQuietly(sTempPath)
            raise

    def fnWriteFileFromStream(
        self, sContainerId, sFilePath, fileSource,
        iExpectedBytes=None, bReplaceAllowed=True, iMode=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Write a file from a readable stream; the host sibling of the
        container's :meth:`DockerConnection.fnWriteFileFromStream`.

        The same contract: bounded memory whatever the size,
        ``iExpectedBytes`` refuses a stream that ends short or runs long,
        ``bReplaceAllowed`` False refuses an existing target, and a
        refusal leaves the old file untouched. The bytes are staged in
        the DESTINATION directory, never in a scratch directory, so the
        final rename stays on one filesystem and is atomic. A symlinked
        final component is refused rather than written through: a link
        in the project is something the researcher placed and an upload
        must not silently rewrite whatever it points at.
        ``sAuthorizedRoot``/``tForbiddenNames`` are accepted for the duck
        type and not consulted, as in :meth:`fnWriteFile`.
        """
        del sAuthorizedRoot, tForbiddenNames
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFileFromStream",
        )
        sRealPath = self._fsValidateHostPath(sContainerId, sFilePath)
        self._fnRefuseUnwritableFinalComponent(
            sContainerId, sFilePath, sRealPath, bReplaceAllowed,
        )
        iEffectiveMode = _fiResolveWriteMode(sRealPath, iMode)
        iDescriptor, sTempPath = tempfile.mkstemp(
            dir=os.path.dirname(sRealPath), prefix=S_STAGING_PREFIX,
        )
        try:
            with os.fdopen(iDescriptor, "wb") as fileStaged:
                os.fchmod(iDescriptor, iEffectiveMode)
                _fnCopyStreamBounded(fileSource, fileStaged, iExpectedBytes)
                fileStaged.flush()
                os.fsync(iDescriptor)
            _fnPublishStagedFile(sTempPath, sRealPath, bReplaceAllowed)
        except BaseException:
            _fnUnlinkQuietly(sTempPath)
            raise

    def _fnRefuseUnwritableFinalComponent(
        self, sContainerId, sFilePath, sRealPath, bReplaceAllowed,
    ):
        """Raise when the target is a link, a directory, or taken.

        ``_fsValidateHostPath`` resolves links, so the final component
        is looked at UNRESOLVED here: its parent is resolved and the
        name is examined as it stands.
        """
        sProjectRoot = os.path.realpath(
            self._fnResolveProjectRoot(sContainerId),
        )
        sAbsolute = (sFilePath if os.path.isabs(sFilePath)
                     else os.path.join(sProjectRoot, sFilePath))
        sAsNamed = os.path.join(
            os.path.realpath(os.path.dirname(sAbsolute)),
            os.path.basename(sAbsolute),
        )
        if os.path.islink(sAsNamed) or os.path.isdir(sRealPath):
            raise ContainerWriteRefusedError(
                f"Write to {sFilePath} refused: it is a symlink or a "
                "directory"
            )
        if not bReplaceAllowed and os.path.lexists(sAsNamed):
            raise ContainerWriteExistsError(
                f"Write to {sFilePath} refused: it already exists"
            )

    def fnWriteFileViaTar(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Duck-type alias for :meth:`fnWriteFile`; no tar is involved."""
        self.fnWriteFile(
            sContainerId, sFilePath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
            sAuthorizedRoot=sAuthorizedRoot,
            tForbiddenNames=tForbiddenNames,
        )

    def fnMakeDirectory(
        self, sContainerId, sDirectoryPath,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Create a directory, and any missing parents, inside the project.

        The host twin of the Docker leg's method: idempotent, and
        confined by the same path guard as every host write. A symlinked
        directory anywhere in the path resolves outside the project or
        stays inside it, and in neither case does this follow one to
        create something outside.
        """
        del sAuthorizedRoot, tForbiddenNames
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnMakeDirectory",
        )
        os.makedirs(
            self._fsValidateHostPath(sContainerId, sDirectoryPath),
            exist_ok=True,
        )

    def fnWriteTreeViaTar(
        self, sResourceId, sDestinationDirectory, listHostPaths,
        iUid=None, iGid=None, sArchiveName=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
        bCreateDestination=False,
    ):
        """Refuse: a host project's files are already where they run.

        The Docker leg's tree copy exists to carry host content across
        into a workspace volume. A host project HAS no such volume --
        its workspace IS the researcher's directory -- so there is no
        crossing to make, and every plausible reading of "copy these
        into the project" would either duplicate the tree onto itself
        or overwrite the originals. The refusal names that rather than
        silently succeeding, which is what an alias to a host-side
        copy would do.
        """
        del sDestinationDirectory, listHostPaths, iUid, iGid, sArchiveName
        del sAuthorizedRoot, tForbiddenNames, bCreateDestination
        raise HostPathOutsideProjectError(
            f"'{sResourceId}' is a host project: its files already live "
            "where the project runs, so there is no container workspace "
            "to copy them into."
        )

    # -----------------------------------------------------------------
    # The exec primitive: gated, journaled, group-bounded (plan §4).
    # -----------------------------------------------------------------

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
        fTimeoutSeconds=F_DEFAULT_HOST_EXEC_TIMEOUT_SECONDS,
        sOperationLabel="exec", fnPhaseCallback=None,
    ):
        """Run a command on the host; return an :class:`ExecResult`.

        The bounded-timeout lane: reads and short operations. A launch
        that outlives ``fTimeoutSeconds`` has its recorded process
        group terminated and reports exit code 124 (the shell timeout
        convention) with a stderr note. Long-running pipeline steps
        belong on :meth:`ftRunInContainerStreamedWithChunks`, which is
        durable-task-guarded and cancellable instead of wall-clocked.
        """
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftRunInContainerStreamed",
        )
        return self._ftLaunchGatedAndStream(
            sContainerId, sCommand, sWorkdir, sUser, None,
            fTimeoutSeconds, sOperationLabel,
            fnPhaseCallback=fnPhaseCallback,
        )

    def ftRunInContainerStreamedWithChunks(
        self, sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None, sOperationLabel="pipeline-exec",
        fnPhaseCallback=None, dictEnvironmentOverlay=None,
    ):
        """Run a durable command, invoking ``fnEmitChunk`` per line.

        No wall-clock timeout: a legitimate science run may take days,
        and killing it at an arbitrary bound is exactly what vaibify
        must not do. The bound on this lane is the durable-task
        machinery — the journaled group identity makes Cancel and the
        quiescence probes possible instead.

        ``dictEnvironmentOverlay`` lays run-scoped variables (the
        determinism guarantees) over the inherited environment as
        DATA. Only this leg takes it: the container lane necessarily
        carries its environment as shell text, and the runner passes
        the argument on the host branch alone.
        """
        mutationAdmission.fnAssertDurableExecAdmitted(
            sContainerId, "ftRunInContainerStreamedWithChunks",
        )
        return self._ftLaunchGatedAndStream(
            sContainerId, sCommand, sWorkdir, sUser, fnEmitChunk,
            None, sOperationLabel, fnPhaseCallback=fnPhaseCallback,
            dictEnvironmentOverlay=dictEnvironmentOverlay,
        )

    # -----------------------------------------------------------------
    # The terminal PTY cluster (host leg).
    # -----------------------------------------------------------------

    def fdictLaunchTerminalShellSuspended(self, sResourceId):
        """Spawn the terminal shell SUSPENDED; return its live handle.

        The host terminal's half of the journal-before-first-
        instruction split (ruling 12): the caller — the terminal seam,
        which alone prepares execution records — journals the pid this
        returns and only then calls ``fnReleaseGate``. Until the gate
        byte arrives the child is a stub blocked on a pipe read; a
        crash in between leaves an identified, probeable record, never
        a shell nobody can name.

        The handle is ``{"processChild", "iMasterFd", "fnReleaseGate"}``.
        The caller owns the master fd's lifetime.
        """
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sResourceId, "fdictLaunchTerminalShellSuspended",
        )
        sWorkdir = os.path.realpath(
            self._fnResolveProjectRoot(sResourceId),
        )
        iMasterFd, iSlaveFd = pty.openpty()
        iGateRead, iGateWrite = os.pipe()
        os.set_inheritable(iGateRead, True)
        try:
            processChild = subprocess.Popen(
                [sys.executable, "-c", _S_TERMINAL_LAUNCH_STUB,
                 str(iGateRead)],
                stdin=iSlaveFd, stdout=iSlaveFd, stderr=iSlaveFd,
                cwd=sWorkdir, pass_fds=(iGateRead,), close_fds=True,
            )
        except Exception:
            os.close(iMasterFd)
            os.close(iGateWrite)
            raise
        finally:
            os.close(iSlaveFd)
            os.close(iGateRead)

        def fnReleaseGate():
            os.write(iGateWrite, b"G")
            os.close(iGateWrite)

        return {
            "processChild": processChild,
            "iMasterFd": iMasterFd,
            "fnReleaseGate": fnReleaseGate,
        }

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        """Count host processes in the recorded session or group.

        The host twin of the Docker leg's probe, with the same answer
        shape and the same SESSION-wide match. The enumeration is
        ``processLiveness.ftEnumerateSessionMembers`` — the in-process
        probe primitive beside the recycle-proof start-clock read —
        rather than a journaled launch, because the journal's own
        resolver runs this probe while holding the journal write
        lock, and a journaled sweep deadlocks by construction (found
        the hard way: every hub hung at startup over one crashed
        terminal record). ``listMemberPids`` rides the answer so the
        signaller can deliver to members ``killpg`` cannot see.
        """
        del sContainerId
        if not isinstance(iProcessGroup, int) or iProcessGroup <= 0:
            return {
                "bConclusive": False, "iMemberCount": -1,
                "sDetail": f"unusable process group {iProcessGroup!r}",
            }
        bConclusive, listMemberPids = (
            processLiveness.ftEnumerateSessionMembers(iProcessGroup)
        )
        if not bConclusive:
            return {
                "bConclusive": False, "iMemberCount": -1,
                "sDetail": "the session enumeration could not run",
            }
        return {
            "bConclusive": True, "iMemberCount": len(listMemberPids),
            "listMemberPids": listMemberPids,
            "sDetail": f"{len(listMemberPids)} live member(s)",
        }

    def fnSignalProcessGroupMembers(
        self, sContainerId, iProcessGroup, sSignalName,
    ):
        """Signal every host process of the recorded session or group.

        Enumeration through the session-wide probe, delivery through
        ``hostCancellation``'s judged signaller (per-member ``os.kill``
        plus ``killpg`` on the leader group for anything that joined
        between the enumeration and now). Quiet failures — the
        terminate-and-prove caller decides on the PROOF, never on the
        delivery — exactly the Docker leg's contract.
        """
        if sSignalName not in ("TERM", "KILL"):
            raise ValueError(
                f"Unsupported process-group signal {sSignalName!r}; "
                "only TERM and KILL are allowlisted"
            )
        dictProbe = self.fdictProbeProcessGroupMembers(
            sContainerId, iProcessGroup,
        )
        fnSignalSessionMembers(
            iProcessGroup,
            dictProbe.get("listMemberPids") or [],
            sSignalName,
        )

    # -----------------------------------------------------------------
    # The typed-read grant point (host leg).
    # -----------------------------------------------------------------

    def flistReadGitRepoStatuses(self, sContainerId, listRepoPaths):
        """Return one raw status record per repository path, in order.

        The host twin of the Docker leg's method of the same name, and
        the same JSON shape, so the caller above cannot tell which leg
        answered.
        """
        if not listRepoPaths:
            return []
        for sRepoPath in listRepoPaths:
            self._fsValidateHostPath(sContainerId, sRepoPath)
        tExecResult = self._ftRunTypedReadProgram(
            sContainerId, S_TYPED_READ_GIT_REPO_STATUS,
            list(listRepoPaths),
        )
        if tExecResult.iExitCode != 0:
            raise OSError(
                "Cannot read repository status on the host "
                f"({tExecResult.sStderr.strip()})"
            )
        try:
            return json.loads(tExecResult.sStdout.strip() or "[]")
        except ValueError as errorParse:
            raise OSError(
                "The repository status read answered unparseable "
                f"output: {errorParse}"
            )

    def _ftRunTypedReadProgram(self, sResourceId, sOperationName, listPaths):
        """Run one NAMED read program; the host leg's single grant point.

        WHY THIS EXISTS AT ALL, since every other host read is a plain
        ``os.*`` call and needs nothing. Reading a repository's status
        means running ``git``: there is no other way to ask git. And
        the Repositories panel asks on a five-second timer, so this
        cannot assert a command admission the way the two exec methods
        do — a poll holding the mutation drain is what makes Run Step
        refuse at random, which this product has already shipped once.

        So this is the host analogue of
        ``DockerConnection._ftRunTypedRead``, with the same shape and
        the same reason for it. It takes an operation NAME from a fixed
        table plus a flat sequence of paths, and **builds the command
        itself**; it never accepts one. A caller can choose which
        directories are read, never what runs in them.

        What it does NOT skip is the record. Ruling 12 says every
        subprocess a host project starts is gated and journaled, and
        this one is: the launch goes through the same
        :meth:`_ftLaunchGatedAndStream` as everything else, so its
        process group is on disk before its first instruction runs and
        Cancel and the quiescence probes can see it. The admission is
        what is absent, and only that.
        """
        sProgram = fsRenderBatchedTypedReadProgram(
            sOperationName, listPaths,
        )
        return self._ftLaunchGatedAndStream(
            sResourceId,
            f"{shlex.quote(sys.executable)} -c "
            f"{shlex.quote(sProgram)}",
            None, None, None,
            F_TYPED_READ_TIMEOUT_SECONDS,
            f"typed-read:{sOperationName}",
        )

    def ftRunProgramWithStdin(self, sContainerId, listCommand, baStdin):
        """Run a program on the host with ``baStdin`` as its standard input.

        The way to hand a program a secret: the argument vector carries
        no credential, so no other user's ``ps`` can read one. The words
        are quoted into the one gated launch every host subprocess goes
        through, and the bytes follow the launch gate on the same pipe.
        """
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftRunProgramWithStdin",
        )
        return self._ftLaunchGatedAndStream(
            sContainerId, shlex.join(listCommand), None, None, None,
            F_DEFAULT_HOST_EXEC_TIMEOUT_SECONDS, "exec",
            baStdin=baStdin,
        )

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        """Backward-compat wrapper returning ``(iExitCode, sOutput)``."""
        tExecResult = self.ftRunInContainerStreamed(
            sContainerId, sCommand, sWorkdir=sWorkdir, sUser=sUser,
        )
        return (
            tExecResult.iExitCode,
            tExecResult.sStdout + tExecResult.sStderr,
        )

    def _ftLaunchGatedAndStream(
        self, sResourceId, sCommand, sWorkdir, sUser, fnEmitChunk,
        fTimeoutSeconds, sOperationLabel, fnPhaseCallback=None,
        dictEnvironmentOverlay=None, baStdin=b"",
    ):
        """The single gated launch every host subprocess goes through.

        Order is the contract: PREPARED record -> suspended spawn in a
        fresh session -> identity promoted and gated -> gate released.
        ``fnPhaseCallback(sPhase)`` (``prepared``/``spawned``/
        ``promoted``/``released``) exists so tests can hold the launch
        at each boundary; production callers leave it ``None``.
        """
        if sUser is not None:
            raise ValueError(
                "Host execution always runs as the invoking user; "
                f"sUser={sUser!r} cannot be honored"
            )
        sEffectiveWorkdir = self._fsResolveWorkdir(sResourceId, sWorkdir)
        dictHostExecHandle = mutationAdmission.fdictBeginJournaledHostExec(
            sResourceId, sOperationLabel,
        )
        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, "prepared")
        processChild = subprocess.Popen(
            ["/bin/bash", "-c", _S_GATED_LAUNCH_STUB,
             "vaibifyGatedLaunch", sCommand],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=sEffectiveWorkdir,
            start_new_session=True,
            env=_fdictComposeLaunchEnvironment(dictEnvironmentOverlay),
        )
        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, "spawned")
        mutationAdmission.fnPromoteJournaledHostExec(
            dictHostExecHandle, processChild.pid, processChild.pid,
        )
        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, "promoted")
        processChild.stdin.write(b"GO\n")
        processChild.stdin.flush()
        _fnFeedStdinThenClose(processChild, baStdin)
        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, "released")
        tStreams = _ftDrainProcessStreams(processChild, fnEmitChunk)
        bCompleted, fCpuSeconds = _ftAwaitProcessWithinBound(
            processChild, fTimeoutSeconds,
        )
        bTimedOut = not bCompleted
        if bTimedOut:
            fnTerminateProcessGroup(processChild.pid)
            processChild.wait()
        sStdout, sStderr = _ftCollectStreamsBounded(
            tStreams, processChild.pid, fTimeoutSeconds,
        )
        if bTimedOut:
            sStderr = sStderr + (
                f"\nhost exec timed out after {fTimeoutSeconds}s; "
                "the recorded process group was terminated"
            )
        if fbProcessGroupProvedEmpty(processChild.pid):
            mutationAdmission.fnSettleJournaledHostExec(dictHostExecHandle)
        return ExecResult(
            iExitCode=(
                124 if bTimedOut else int(processChild.returncode or 0)
            ),
            sStdout=sStdout, sStderr=sStderr,
            fCpuSeconds=fCpuSeconds,
        )

    def _fsResolveWorkdir(self, sResourceId, sWorkdir):
        """Validate the working directory, defaulting to the project root."""
        if sWorkdir is None:
            return os.path.realpath(
                self._fnResolveProjectRoot(sResourceId),
            )
        return self._fsValidateHostPath(sResourceId, sWorkdir)


def _fnFeedStdinThenClose(processChild, baStdin):
    """Send the program's input after the gate line, then close stdin.

    The gate stub reads exactly one line, so whatever follows it is the
    exec'd program's standard input. A payload is written from a thread:
    the child's output is not being drained yet, and a write larger than
    the pipe would otherwise wait on a reader that has not started.
    """
    if not baStdin:
        processChild.stdin.close()
        return

    def fnFeedThenClose():
        try:
            processChild.stdin.write(baStdin)
            processChild.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        finally:
            try:
                processChild.stdin.close()
            except (BrokenPipeError, ValueError):
                pass

    threading.Thread(target=fnFeedThenClose, daemon=True).start()


def _fnInvokeLaunchPhaseCallback(fnPhaseCallback, sPhase):
    """Invoke the test-only phase hook when one was provided."""
    if fnPhaseCallback is not None:
        fnPhaseCallback(sPhase)


def _fdictComposeLaunchEnvironment(dictEnvironmentOverlay):
    """Return inherited env + overlay, or None to inherit untouched.

    The base is ALWAYS the hub's own environment (the plan's
    inherited-env-only ruling): the overlay may add or shadow entries,
    never replace the environment wholesale — a child stripped of PATH
    and HOME is not the process the researcher's own shell would have
    started.
    """
    if not dictEnvironmentOverlay:
        return None
    dictEnvironment = dict(os.environ)
    dictEnvironment.update(dictEnvironmentOverlay)
    return dictEnvironment


class _StreamLineCollector:
    """Reader thread turning one pipe into emitted, collected lines."""

    def __init__(self, sStreamName, fileHandle, fnEmitChunk):
        self._sStreamName = sStreamName
        self._fileHandle = fileHandle
        self._fnEmitChunk = fnEmitChunk
        self._listLines = []
        self.threadReader = threading.Thread(
            target=self._fnReadUntilClosed, daemon=True,
        )
        self.threadReader.start()

    def _fnReadUntilClosed(self):
        for baLine in self._fileHandle:
            sLine = baLine.decode(
                "utf-8", errors="replace",
            ).rstrip("\n")
            if self._fnEmitChunk is not None:
                self._fnEmitChunk(self._sStreamName, sLine)
            self._listLines.append(sLine)

    def fbJoinWithin(self, fTimeoutSeconds):
        """Wait up to the bound for pipe EOF; False when still open."""
        self.threadReader.join(fTimeoutSeconds)
        return not self.threadReader.is_alive()

    def fsJoin(self):
        """Wait for the pipe to close and return the collected text."""
        self.threadReader.join()
        return self.fsCollectedText()

    def fsCollectedText(self):
        """Return the lines collected so far, joined."""
        return "\n".join(self._listLines)


F_PIPE_DRAIN_GRACE_SECONDS = 5.0


def _ftCollectStreamsBounded(tStreams, iProcessGroup, fTimeoutSeconds):
    """Return (stdout, stderr), refusing to hang on a held pipe.

    A pipe stays open until EVERY holder exits, so ``fTimeoutSeconds``
    bounding the direct child is not enough: a quick command that
    backgrounds a pipe-holding survivor would otherwise block this
    collection until the survivor dies naturally — the "bounded" lane
    silently unbounded (found by a falsification mutant that survived
    by waiting out the test's sleepers). On the bounded lane an
    undrained pipe escalates to a group termination and, if something
    unkillable-by-group still holds it (a ``setsid`` escapee), the
    streams are abandoned with a truncation note rather than hanging —
    the reader threads are daemons and cost nothing. The durable lane
    (``fTimeoutSeconds is None``) keeps Docker-parity semantics: the
    stream ends when its holders exit.
    """
    if fTimeoutSeconds is None:
        return tStreams[0].fsJoin(), tStreams[1].fsJoin()
    bDrained = _fbJoinBothStreamsWithin(
        tStreams, F_PIPE_DRAIN_GRACE_SECONDS,
    )
    if not bDrained:
        fnTerminateProcessGroup(iProcessGroup)
        bDrained = _fbJoinBothStreamsWithin(
            tStreams, F_PIPE_DRAIN_GRACE_SECONDS,
        )
    sStdout = tStreams[0].fsCollectedText()
    sStderr = tStreams[1].fsCollectedText()
    if not bDrained:
        sStderr = sStderr + (
            "\nhost exec output truncated: a process outside the "
            "recorded group still holds the output pipe"
        )
    return sStdout, sStderr


def _ftDrainProcessStreams(processChild, fnEmitChunk):
    """Return (stdout, stderr) collectors draining the child's pipes."""
    return (
        _StreamLineCollector("stdout", processChild.stdout, fnEmitChunk),
        _StreamLineCollector("stderr", processChild.stderr, fnEmitChunk),
    )


def _fbJoinBothStreamsWithin(tStreams, fTimeoutSeconds):
    """Join both collectors within the bound; True when both drained.

    A list literal, not ``and`` short-circuiting: both joins must be
    attempted even when the first reports an open pipe.
    """
    return all([
        tStreams[0].fbJoinWithin(fTimeoutSeconds),
        tStreams[1].fbJoinWithin(fTimeoutSeconds),
    ])


_F_REAP_POLL_INTERVAL_SECONDS = 0.05


def _ftAwaitProcessWithinBound(processChild, fTimeoutSeconds):
    """Reap the child via ``os.wait4``; return (bCompleted, fCpuSeconds).

    The reap is claimed here rather than left to ``Popen.wait``
    because ``wait4`` is the only call that surfaces the child's
    rusage, and a pid can be collected exactly once. ``Popen`` is
    handed the returncode afterwards so its own bookkeeping (the
    timeout-kill path's ``wait()``, ``__del__``) never double-reaps.
    Nothing else in this module waits on the pid: the group prover
    and Cancel only signal, and the stream collectors read pipes.

    Scope of the measurement, stated honestly: on macOS and Linux the
    rusage covers the reaped process plus any children IT already
    waited for — a step that backgrounds a survivor does not
    accumulate it. There is no fabricated group total.

    ``fCpuSeconds`` is ``None`` (absent), never 0.0, when the bound
    expired with the child still live or when the reap was lost to
    another collector (``ChildProcessError``); the durable lane
    (``fTimeoutSeconds is None``) blocks in ``wait4`` with no
    polling.
    """
    fDeadline = (
        None if fTimeoutSeconds is None
        else time.monotonic() + fTimeoutSeconds
    )
    while True:
        try:
            tReaped = os.wait4(
                processChild.pid,
                0 if fDeadline is None else os.WNOHANG,
            )
        except ChildProcessError:
            return _fbAwaitReapedElsewhere(processChild, fTimeoutSeconds), None
        if tReaped[0] == processChild.pid:
            processChild.returncode = os.waitstatus_to_exitcode(tReaped[1])
            return True, tReaped[2].ru_utime + tReaped[2].ru_stime
        if fDeadline is not None and time.monotonic() >= fDeadline:
            return False, None
        time.sleep(_F_REAP_POLL_INTERVAL_SECONDS)


def _fbAwaitReapedElsewhere(processChild, fTimeoutSeconds):
    """Fall back to Popen's own wait after wait4 lost the reap race."""
    try:
        processChild.wait(timeout=fTimeoutSeconds)
        return True
    except subprocess.TimeoutExpired:
        return False
