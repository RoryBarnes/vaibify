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
(the connection router answers it from the registry), the terminal PTY
cluster (the terminal is disabled product-wide), and the root shell
probe (nothing host-mode needs root — the host user IS the user).
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
import shlex
import subprocess
import sys
import tempfile
import threading
import time

from vaibify.config import mutationAdmission
from vaibify.docker.dockerConnection import (
    ExecResult,
    S_TYPED_READ_GIT_REPO_STATUS,
    fsRenderBatchedTypedReadProgram,
)
from vaibify.host.hostCancellation import (
    fbProcessGroupProvedEmpty,
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

# The child blocks on its stdin until the parent has journaled its
# identity, then becomes the command via exec — so the command's first
# instruction cannot run before the record that names the process
# exists. The host analogue of Docker's create -> journal -> start.
_S_GATED_LAUNCH_STUB = (
    "import os,sys\n"
    "sys.stdin.readline()\n"
    "os.execv('/bin/bash', ['/bin/bash', '-c', sys.argv[1]])\n"
)


class HostPathOutsideProjectError(RuntimeError):
    """A host path argument escaped the project and scratch roots."""


class UnknownHostProjectError(RuntimeError):
    """The resource id does not name a registered host project."""


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
        answer for a workflow whose file does not exist yet — and, via
        the cap below, for one too large to be a fingerprint target.

        Reads through this class's own capped read rather than
        streaming as the container program does. The asymmetry is
        deliberate: the container program streams because it runs
        somewhere with a memory ceiling nobody here can see, while this
        leg already has one bounded reader and a second one would be a
        second place to get the path guard right.
        """
        try:
            baContent = self.fbaFetchFile(sContainerId, sPath)
        except (OSError, ValueError):
            return ""
        return hashlib.sha256(baContent).hexdigest()

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

    # -----------------------------------------------------------------
    # File writes: atomic, mode-preserving, admission-gated.
    # -----------------------------------------------------------------

    def fnWriteFile(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
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
        """
        del iUid, iGid
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFile",
        )
        sRealPath = self._fsValidateHostPath(sContainerId, sFilePath)
        iEffectiveMode = iMode
        if iEffectiveMode is None:
            try:
                iEffectiveMode = os.stat(sRealPath).st_mode & 0o7777
            except FileNotFoundError:
                iEffectiveMode = I_NEW_FILE_MODE
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
            try:
                os.unlink(sTempPath)
            except OSError:
                pass
            raise

    def fnWriteFileViaTar(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        """Duck-type alias for :meth:`fnWriteFile`; no tar is involved."""
        self.fnWriteFile(
            sContainerId, sFilePath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
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
        fnPhaseCallback=None,
    ):
        """Run a durable command, invoking ``fnEmitChunk`` per line.

        No wall-clock timeout: a legitimate science run may take days,
        and killing it at an arbitrary bound is exactly what vaibify
        must not do. The bound on this lane is the durable-task
        machinery — the journaled group identity makes Cancel and the
        quiescence probes possible instead.
        """
        mutationAdmission.fnAssertDurableExecAdmitted(
            sContainerId, "ftRunInContainerStreamedWithChunks",
        )
        return self._ftLaunchGatedAndStream(
            sContainerId, sCommand, sWorkdir, sUser, fnEmitChunk,
            None, sOperationLabel, fnPhaseCallback=fnPhaseCallback,
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
            [sys.executable, "-c", _S_GATED_LAUNCH_STUB, sCommand],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=sEffectiveWorkdir,
            start_new_session=True,
        )
        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, "spawned")
        mutationAdmission.fnPromoteJournaledHostExec(
            dictHostExecHandle, processChild.pid, processChild.pid,
        )
        _fnInvokeLaunchPhaseCallback(fnPhaseCallback, "promoted")
        processChild.stdin.write(b"GO\n")
        processChild.stdin.flush()
        processChild.stdin.close()
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


def _fnInvokeLaunchPhaseCallback(fnPhaseCallback, sPhase):
    """Invoke the test-only phase hook when one was provided."""
    if fnPhaseCallback is not None:
        fnPhaseCallback(sPhase)


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
