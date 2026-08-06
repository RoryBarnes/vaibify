"""Docker container discovery, command execution, and file transfer.

Wraps the docker-py SDK with lazy import so the module can be loaded
even when docker-py is not installed.

Stream separation
-----------------

``ftRunInContainerStreamed`` is the canonical execution entry
point. It captures stdout and stderr separately and returns an
``ExecResult`` dataclass so callers can render real container output
distinctly from container-side error noise. The legacy
``ftResultExecuteCommand`` is preserved as a thin backward-compat
wrapper that merges the two streams and emits a ``DeprecationWarning``
on every call, giving downstream callers an audit trail to migrate
on their own schedule (audit finding F-R-01).
"""

import base64
import shlex
import warnings
from dataclasses import dataclass

from vaibify.config import mutationAdmission


_CACHED_CONTAINER_USER = {}

# docker-py defaults to a 60-second read timeout, which a slow
# ``git push`` over docker exec routinely exceeds: the host raises
# ReadTimeout while the push completes inside the container, leaving
# the outcome indeterminate. Ten minutes covers large pushes; routes
# still probe the repository state as a safety net when even this
# limit is hit.
I_DOCKER_CLIENT_TIMEOUT_SECONDS = 600

# docker-py defaults the urllib3 connection pool to 10 sockets. The
# streaming run permanently pins one (``exec_start(stream=True)``);
# the badge collector fans out three concurrent execs; the heartbeat
# loop competes every 5 s; the file-status poll adds more on every
# refresh. Raise the ceiling so transient saturation never starves
# the heartbeat thread (audit CRITICAL #3).
I_DOCKER_POOL_MAX_SIZE = 32

# ``fbaFetchFile`` round-trips a file through base64 over docker exec
# stdout, which peaks at roughly 3x the file size in RAM (raw +
# base64-encoded + decoded). Cap the small-file path at 64 MB so a
# caller cannot accidentally pull a multi-GB output file through it;
# large files must go through :meth:`DockerConnection.fiterStreamFile`
# instead, which streams via ``container.get_archive``.
I_MAX_FETCH_FILE_BYTES = 64 * 1024 * 1024

# The heartbeat loop calls ``ftResultExecuteCommand`` on every tick,
# which currently raises a ``DeprecationWarning`` per invocation.
# Multi-day runs flood the host log; the migration to the streamed
# entry point is tracked elsewhere. Filter the specific warning at
# import time so production logs stay readable (audit MEDIUM #18).
warnings.filterwarnings(
    "ignore",
    message=r".*ftResultExecuteCommand merges stdout and stderr.*",
    category=DeprecationWarning,
)


def _fnTuneDockerSessionPool(clientDocker):
    """Mount oversized HTTPAdapters on the docker client's session.

    docker-py exposes its ``requests.Session`` as ``client.api`` and
    registers its own ``UnixHTTPAdapter`` at ``http+docker://`` so
    requests over a unix socket can drive a path-based URL. Replacing
    that with a vanilla ``HTTPAdapter`` breaks the scheme — urllib3
    raises ``URLSchemeUnknown: http+docker`` and every docker call
    fails. The TCP schemes use a vanilla adapter; the unix-socket
    scheme is rebuilt from docker-py's own class with the larger pool.
    """
    import logging
    from requests.adapters import HTTPAdapter
    sessionDocker = getattr(clientDocker, "api", None)
    if sessionDocker is None or not hasattr(sessionDocker, "mount"):
        return
    _fnMountTcpAdapter(sessionDocker, "http://", HTTPAdapter)
    _fnMountTcpAdapter(sessionDocker, "https://", HTTPAdapter)
    _fnMountUnixAdapter(sessionDocker)


def _fnMountTcpAdapter(sessionDocker, sPrefix, classAdapter):
    """Mount a vanilla TCP HTTPAdapter with the oversized pool."""
    import logging
    try:
        sessionDocker.mount(sPrefix, classAdapter(
            pool_connections=I_DOCKER_POOL_MAX_SIZE,
            pool_maxsize=I_DOCKER_POOL_MAX_SIZE,
        ))
    except Exception as error:
        logging.getLogger("vaibify").warning(
            "docker pool tune failed for %s: %s", sPrefix, error,
        )


def _fnMountUnixAdapter(sessionDocker):
    """Remount docker-py's UnixHTTPAdapter with a 32-connection pool.

    The unix-socket adapter parses ``http+docker://`` URLs against a
    real filesystem socket path. Replacing it with a vanilla
    ``HTTPAdapter`` makes urllib3 raise ``URLSchemeUnknown`` on the
    first call. Read the existing adapter's ``socket_path`` and pass
    it back to a fresh ``UnixHTTPAdapter`` with the larger pool —
    same transport, bigger ceiling. The constructor's first arg
    expects a ``http+unix://...`` URL whose path part is the socket;
    do not reuse the session's ``base_url`` (it is the docker-py
    pseudo-host ``http+docker://localhost`` and points at no file).
    Failures are non-fatal so a docker SDK packaging change cannot
    brick the GUI.
    """
    import logging
    try:
        from docker.transport.unixconn import UnixHTTPAdapter
        adapterExisting = sessionDocker.adapters.get("http+docker://")
        sSocketPath = getattr(adapterExisting, "socket_path", "") or ""
        if not sSocketPath:
            return
        sessionDocker.mount("http+docker://", UnixHTTPAdapter(
            "http+unix://" + sSocketPath,
            timeout=I_DOCKER_CLIENT_TIMEOUT_SECONDS,
            pool_connections=I_DOCKER_POOL_MAX_SIZE,
            max_pool_size=I_DOCKER_POOL_MAX_SIZE,
        ))
    except Exception as error:
        logging.getLogger("vaibify").warning(
            "docker unix-socket pool tune failed: %s", error,
        )


# Numeric UID/GID of the container's unprivileged user. The Dockerfile
# pins ``useradd -u 1000`` (and gid follows the user) for every image
# variant; the ``testContainerUserUidIsOneThousand`` architectural
# invariant in ``tests/testArchitecturalInvariants.py`` keeps the two in
# lock-step. Used as the default ownership stamp on tarballs written by
# ``fnWriteFile`` / ``fnWriteFileViaTar`` so that backend-authored files
# (project.json, state JSON, generated tests, log files, credential
# scratch, etc.) land owned by the in-container user — not root, which
# would silently lock the file against subsequent in-container edits
# (the in-container agent has no sudo by design).
_I_CONTAINER_DEFAULT_UID = 1000
_I_CONTAINER_DEFAULT_GID = 1000


def _fsResolveContainerUser(container):
    """Return the unprivileged user baked into the image, cached per id.

    Reads the image's ``USER`` directive
    (``container.image.attrs["Config"]["User"]``) rather than the
    container's effective user. The container's effective user can be
    overridden by ``docker run --user`` (vaibify does this so the
    entrypoint's root phase can chown the workspace before ``gosu``-ing
    down), but the image's USER is the install identity and is what
    every dispatched command should run as. Falls back to
    ``researcher`` when the image has no USER pinned.
    """
    sContainerId = getattr(container, "id", None) or ""
    if isinstance(sContainerId, str) and sContainerId in _CACHED_CONTAINER_USER:
        return _CACHED_CONTAINER_USER[sContainerId]
    sUser = "researcher"
    try:
        sValue = container.image.attrs["Config"]["User"]
        if isinstance(sValue, str) and sValue:
            sUser = sValue
    except (AttributeError, KeyError, TypeError):
        pass
    if isinstance(sContainerId, str):
        return _CACHED_CONTAINER_USER.setdefault(sContainerId, sUser)
    return sUser


@dataclass
class ExecResult:
    """Outcome of a single ``docker exec`` call with split streams.

    Attributes
    ----------
    iExitCode : int
        Exit status reported by the container's exec instance.
    sStdout : str
        UTF-8-decoded standard output. Empty string if nothing was
        written to stdout.
    sStderr : str
        UTF-8-decoded standard error. Empty string if nothing was
        written to stderr.
    """

    iExitCode: int
    sStdout: str
    sStderr: str


def _fmoduleGetDocker():
    """Lazily import and return the docker module.

    Returns
    -------
    module
        The docker Python package.

    Raises
    ------
    ImportError
        If docker-py is not installed.
    """
    try:
        import docker
        return docker
    except ImportError:
        raise ImportError(
            "The docker Python package is missing. It installs with "
            "vaibify itself, so this vaibify installation is broken. "
            "Repair it with: pip install --force-reinstall vaibify"
        )


def _fnEnsureDockerHost():
    """Set DOCKER_HOST from active Docker context if not already set."""
    import os
    import subprocess
    if os.environ.get("DOCKER_HOST"):
        return
    try:
        resultProcess = subprocess.run(
            ["docker", "context", "inspect", "--format",
             "{{.Endpoints.docker.Host}}"],
            capture_output=True, text=True,
        )
        sHost = resultProcess.stdout.strip()
        if sHost:
            os.environ["DOCKER_HOST"] = sHost
    except Exception:
        pass


# The complete set of programs the audited-read exemption will run,
# as FIXED module source text. The only thing substituted into one is a
# path, embedded through ``repr`` as a Python literal; the assembled
# program is then quoted whole as a single shell argument. An adapter
# chooses a NAME from this table and supplies a path -- it cannot
# supply a command, so it cannot supply a bad one.
_S_TYPED_READ_PATH_SLOT = "<<PATH>>"
S_TYPED_READ_FILE_BASE64 = "readFileBase64"
S_TYPED_READ_DIRECTORY = "listDirectory"

_DICT_TYPED_READ_PROGRAMS = {
    S_TYPED_READ_FILE_BASE64: (
        "import base64,sys; "
        "sys.stdout.buffer.write(base64.b64encode(open("
        + _S_TYPED_READ_PATH_SLOT + ",'rb').read()))"
    ),
    S_TYPED_READ_DIRECTORY: (
        "import os,sys; "
        "sys.stdout.write(chr(10).join(sorted(os.listdir("
        + _S_TYPED_READ_PATH_SLOT + "))))"
    ),
}


class DockerConnection:
    """Wraps docker-py client for container operations."""

    def __init__(self):
        _fnEnsureDockerHost()
        self._clientDocker = _fmoduleGetDocker().from_env(
            timeout=I_DOCKER_CLIENT_TIMEOUT_SECONDS,
        )
        _fnTuneDockerSessionPool(self._clientDocker)
        self._dictContainers = {}

    def fnEvictAbsentContainers(self, setRunningContainerIds):
        """Drop instance + module caches for ids no longer running.

        Without this the per-container caches grew unbounded across
        rebuilds; multi-week uptimes accumulate stale handles for
        every container that ever existed (audit HIGH #13). Callers
        should invoke this from the same sweep that powers
        ``flistGetRunningContainers``.
        """
        for sContainerId in list(self._dictContainers.keys()):
            if sContainerId not in setRunningContainerIds:
                self._dictContainers.pop(sContainerId, None)
        for sContainerId in list(_CACHED_CONTAINER_USER.keys()):
            if sContainerId not in setRunningContainerIds:
                _CACHED_CONTAINER_USER.pop(sContainerId, None)

    def flistGetRunningContainers(self):
        """Return list of dicts with container id, name, image.

        Refreshes the instance container cache and evicts entries for
        ids no longer running so multi-week uptimes do not accumulate
        stale handles (audit HIGH #13).
        """
        listContainers = self._clientDocker.containers.list(
            filters={"status": "running"}
        )
        listResult = []
        setRunning = set()
        for container in listContainers:
            listResult.append(
                {
                    "sContainerId": container.id,
                    "sShortId": container.short_id,
                    "sName": container.name,
                    "sImage": str(container.image.tags[0])
                    if container.image.tags
                    else str(container.image.id[:12]),
                }
            )
            self._dictContainers[container.id] = container
            setRunning.add(container.id)
        self.fnEvictAbsentContainers(setRunning)
        return listResult

    def fcontainerGetById(self, sContainerId):
        """Return the container object, refreshing if needed.

        Uses ``setdefault`` on the write so that concurrent fetches for
        the same id (e.g. the parallel badge collector) end up returning
        the same cached object instead of racing on dict assignment.
        """
        if sContainerId in self._dictContainers:
            return self._dictContainers[sContainerId]
        container = self._clientDocker.containers.get(sContainerId)
        return self._dictContainers.setdefault(sContainerId, container)

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None
    ):
        """Run a command, capturing stdout and stderr separately.

        When ``sUser`` is ``None``, the call defaults to the
        unprivileged container user resolved from the image's
        ``Config.User`` field. Callers that genuinely need root must
        opt in explicitly with ``sUser="root"`` (or ``"0"``).

        Returns
        -------
        ExecResult
            Dataclass carrying the exit code, decoded stdout, and
            decoded stderr. Callers can route each stream to the
            appropriate UI surface (e.g. show stdout as command
            output, surface stderr as a distinct error region).
        """
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftRunInContainerStreamed",
        )
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = self._fdictBuildExecKwargs(
            sCommand, sWorkdir, sUser)
        iExitCode, tOutput = container.exec_run(**dictKwargs)
        baStdout, baStderr = self._ftSplitDemuxedOutput(tOutput)
        return ExecResult(
            iExitCode=iExitCode,
            sStdout=baStdout.decode("utf-8", errors="replace"),
            sStderr=baStderr.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _fdictBuildExecKwargs(sCommand, sWorkdir, sUser):
        """Assemble keyword arguments for docker-py's ``exec_run``."""
        dictKwargs = {
            "cmd": ["/bin/bash", "-c", sCommand],
            "demux": True,
        }
        if sWorkdir:
            dictKwargs["workdir"] = sWorkdir
        if sUser:
            dictKwargs["user"] = sUser
        return dictKwargs

    @staticmethod
    def _ftSplitDemuxedOutput(tOutput):
        """Normalise docker-py's demuxed output to two byte buffers.

        ``exec_run(demux=True)`` returns either a ``(stdout, stderr)``
        tuple where each element may be ``None`` or, on the legacy
        non-demuxed path, a single bytes object. Centralising the
        normalisation here keeps both the streamed entry point and
        the backward-compat wrapper symmetrical.
        """
        if isinstance(tOutput, tuple):
            baStdout, baStderr = tOutput
        else:
            baStdout, baStderr = tOutput, None
        return baStdout or b"", baStderr or b""

    def ftRunInContainerStreamedWithChunks(
        self, sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None,
    ):
        """Run a command, invoking ``fnEmitChunk(sStream, sLine)`` per line.

        ``sStream`` is ``"stdout"`` or ``"stderr"``; ``sLine`` is the
        decoded text with the trailing newline stripped. Partial
        trailing data is buffered across docker-py chunks and flushed
        on process exit. Returns an :class:`ExecResult` with the same
        contract as :meth:`ftRunInContainerStreamed` so callers can
        keep their post-exec bookkeeping unchanged.

        This is the durable-task exec primitive the carrier guards
        (design §8): in an enforced lane it refuses to launch without a
        carrier-minted mode-(c) durable-task guard, and under such a
        guard the launch is the two-phase create -> journal -> start
        split — the real exec id is journaled BEFORE ``exec_start``, so
        a crash between the two leaves an identified, probeable record,
        never a writer nobody can name (the hazard ``exec_run`` hides).
        """
        mutationAdmission.fnAssertDurableExecAdmitted(
            sContainerId, "ftRunInContainerStreamedWithChunks",
        )
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = self._fdictBuildExecCreateKwargs(
            sCommand, sWorkdir, sUser,
        )
        dictExecHandle = mutationAdmission.fdictBeginJournaledExec(
            sContainerId,
        )
        sExecId = self._clientDocker.api.exec_create(
            container.id, **dictKwargs,
        )["Id"]
        mutationAdmission.fnPromoteJournaledExec(dictExecHandle, sExecId)
        sStdout, sStderr = self._ftStreamExecLines(sExecId, fnEmitChunk)
        dictInspect = self._clientDocker.api.exec_inspect(sExecId)
        mutationAdmission.fnSettleJournaledExec(dictExecHandle)
        return ExecResult(
            iExitCode=int(dictInspect.get("ExitCode") or 0),
            sStdout=sStdout, sStderr=sStderr,
        )

    @staticmethod
    def _fdictBuildExecCreateKwargs(sCommand, sWorkdir, sUser):
        """Assemble keyword arguments for docker-py's ``exec_create``."""
        dictKwargs = {"cmd": ["/bin/bash", "-c", sCommand]}
        if sWorkdir:
            dictKwargs["workdir"] = sWorkdir
        if sUser:
            dictKwargs["user"] = sUser
        return dictKwargs

    def _ftStreamExecLines(self, sExecId, fnEmitChunk):
        """Stream demuxed exec output, emitting one line at a time.

        ``dictAccum`` mirrors the streamed text for the legacy contract
        in ``ftRunInContainerStreamedWithChunks``; the only in-tree
        caller (the runner's chunk emitter) never reads ``sStdout`` /
        ``sStderr``. Multi-day runs accumulating every line in memory
        leak proportional to throughput, so when ``fnEmitChunk`` is
        passed by that caller we discard rather than retain (audit
        HIGH #7). Test paths that consume the strings pass ``None`` to
        opt back in.
        """
        dictBuf = {"stdout": b"", "stderr": b""}
        dictAccum = {"stdout": [], "stderr": []}
        bAccumulate = fnEmitChunk is None
        generator = self._clientDocker.api.exec_start(
            sExecId, stream=True, demux=True,
        )
        for tDuplet in generator:
            for sStream, baChunk in zip(
                ("stdout", "stderr"), tDuplet,
            ):
                if baChunk:
                    self._fnEmitLines(
                        sStream, baChunk, dictBuf, dictAccum,
                        fnEmitChunk, bAccumulate,
                    )
        return self._ftFinalizeStreamBuffers(
            dictBuf, dictAccum, fnEmitChunk, bAccumulate,
        )

    def _fnEmitLines(
        self, sStream, baChunk, dictBuf, dictAccum, fnEmitChunk,
        bAccumulate=True,
    ):
        """Emit complete lines from a chunk; buffer the partial tail."""
        listLines, dictBuf[sStream] = self._ftSplitChunkOnNewlines(
            baChunk, dictBuf[sStream],
        )
        for baLine in listLines:
            sLine = baLine.decode("utf-8", errors="replace")
            if fnEmitChunk is not None:
                fnEmitChunk(sStream, sLine)
            if bAccumulate:
                dictAccum[sStream].append(sLine)

    @staticmethod
    def _ftSplitChunkOnNewlines(baChunk, baCarry):
        """Return (list of complete lines, leftover bytes) after baChunk."""
        listLines = (baCarry + baChunk).split(b"\n")
        return listLines[:-1], listLines[-1]

    @staticmethod
    def _ftFinalizeStreamBuffers(
        dictBuf, dictAccum, fnEmitChunk, bAccumulate=True,
    ):
        """Flush any trailing partial line; return (sStdout, sStderr)."""
        for sStream in ("stdout", "stderr"):
            baLeftover = dictBuf[sStream]
            if baLeftover:
                sLine = baLeftover.decode("utf-8", errors="replace")
                if fnEmitChunk is not None:
                    fnEmitChunk(sStream, sLine)
                if bAccumulate:
                    dictAccum[sStream].append(sLine)
        return (
            "\n".join(dictAccum["stdout"]),
            "\n".join(dictAccum["stderr"]),
        )

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None
    ):
        """Backward-compat wrapper returning ``(iExitCode, sOutput)``.

        Merges stdout and stderr, matching the historical contract.
        Emits a ``DeprecationWarning`` so existing call sites surface
        in audits while migrating to ``ftRunInContainerStreamed``.
        """
        warnings.warn(
            "ftResultExecuteCommand merges stdout and stderr; "
            "migrate to ftRunInContainerStreamed for split "
            "streams.",
            DeprecationWarning,
            stacklevel=2,
        )
        resultExec = self.ftRunInContainerStreamed(
            sContainerId, sCommand, sWorkdir=sWorkdir, sUser=sUser,
        )
        sOutput = resultExec.sStdout + resultExec.sStderr
        return (resultExec.iExitCode, sOutput)

    def _ftRunTypedRead(self, sContainerId, sOperation, sPath):
        """Run one NAMED read operation against a path, as a read.

        The single place the audited-read exemption is granted, and it
        does not accept a command. It accepts the NAME of an operation
        from :data:`_DICT_TYPED_READ_PROGRAMS` and a path, and builds
        the command itself from fixed module source text with the path
        embedded as a Python literal.

        The earlier shape took adapter-built command TEXT, guarded by a
        source check that no caller-derived value reached it. That check
        was defeated by two levels of assignment -- and would have been
        defeated by the next spelling nobody thought of, because it was
        enumerating bad shapes rather than permitting a good one. An
        exemption that cannot carry a command cannot carry a bad one:
        the only thing an adapter chooses is which of a fixed set of
        programs to run, and an unknown name raises rather than
        executing anything.
        """
        sTemplate = _DICT_TYPED_READ_PROGRAMS.get(sOperation)
        if sTemplate is None:
            raise ValueError(
                f"{sOperation!r} is not a declared typed-read "
                f"operation; the audited-read exemption runs only "
                f"{sorted(_DICT_TYPED_READ_PROGRAMS)}"
            )
        sCommand = "python3 -c " + shlex.quote(
            sTemplate.replace(_S_TYPED_READ_PATH_SLOT, repr(sPath)),
        )
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            return self.ftRunInContainerStreamed(sContainerId, sCommand)
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)

    def fbaFetchFile(
        self, sContainerId, sFilePath, iMaxBytes=I_MAX_FETCH_FILE_BYTES,
    ):
        """Fetch a small file from the container and return its bytes.

        Use this for state JSON, markers, configs, and anything else that
        is bounded in size by design. Large files (HDF5, NetCDF, plot
        bundles) must go through :meth:`fiterStreamFile` instead — this
        path round-trips through base64 over exec stdout which inflates
        memory by ~3x.

        ``iMaxBytes`` is a safety cap (default 64 MB). If the fetched
        payload exceeds it, ``ValueError`` is raised so callers cannot
        accidentally pull a multi-GB output file into RAM via the small
        path.

        The command is not built here: this names a declared read
        operation and :meth:`_ftRunTypedRead` builds it, so a path
        cannot become program or shell syntax.
        """
        resultExec = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_FILE_BASE64, sFilePath,
        )
        if resultExec.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot read file from container: {sFilePath}"
            )
        baContent = base64.b64decode(resultExec.sStdout.strip())
        if iMaxBytes is not None and len(baContent) > iMaxBytes:
            raise ValueError(
                f"File exceeds fbaFetchFile cap "
                f"({len(baContent)} > {iMaxBytes} bytes): "
                f"{sFilePath}; use fiterStreamFile for large files"
            )
        return baContent

    def flistDirectoryEntries(self, sContainerId, sDirectoryPath):
        """Return the names directly inside a container directory.

        An AUDITED ADAPTER, in the sense the mutation boundary means it:
        the caller supplies a PATH and never a command, and this method
        supplies only the NAME of a declared read operation. The program
        is fixed source text in :data:`_DICT_TYPED_READ_PROGRAMS`, and
        :meth:`_ftRunTypedRead` does the substitution and the
        quoting -- so an adapter cannot pass a command even by mistake.

        Callers used to assemble ``f"ls -1 {sPath}"`` themselves and
        hand it to a shell, which made a directory listing an arbitrary
        command execution triggered by a path argument -- and broke on
        any path containing a space.

        A missing or unreadable directory raises ``FileNotFoundError``;
        an empty directory returns an empty list, which is a different
        answer and must stay one.
        """
        resultExec = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_DIRECTORY, sDirectoryPath,
        )
        if resultExec.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot list directory in container: {sDirectoryPath}"
            )
        return [
            sEntry for sEntry in resultExec.sStdout.split("\n") if sEntry
        ]

    def fiterStreamFile(
        self, sContainerId, sFilePath, iChunkSizeBytes=1048576,
    ):
        """Yield the container file's bytes in chunks via get_archive.

        ``container.get_archive`` returns a ``(tar_stream, stat)`` pair
        where ``tar_stream`` is an iterable of raw tar bytes. The tar
        holds a single file entry; this generator parses the tar inline
        and yields only the file's payload bytes, never holding the
        full file in memory at once. Memory usage stays bounded by
        ``iChunkSizeBytes`` regardless of file size.
        """
        container = self.fcontainerGetById(sContainerId)
        try:
            tStreamStat = container.get_archive(sFilePath)
        except Exception as error:
            raise FileNotFoundError(
                f"Cannot read file from container: {sFilePath}: {error}"
            )
        iterTarStream, _ = tStreamStat
        yield from _fiterChunksFromTarStream(
            iterTarStream, iChunkSizeBytes,
        )

    def fnWriteFile(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        """Write bytes to a file inside the container via tar archive.

        ``iMode``/``iUid``/``iGid`` are forwarded so callers writing
        secret-bearing files can bake mode 0600 and the target uid/gid
        into the tarball entry itself, closing the readable window
        between landing and a subsequent ``chmod``.
        """
        self.fnWriteFileViaTar(
            sContainerId, sFilePath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
        )

    def fnWriteFileViaTar(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        """Write bytes to a file using put_archive (no exec size limit).

        Sets ``infoTar.mtime`` to the current epoch so the file lands
        in the container with a real modification time. tarfile's
        ``TarInfo`` defaults ``mtime`` to ``0``, which downstream
        lineage checks treat as "ancient" and surface as "1970-01-01".

        Optional ``iMode``/``iUid``/``iGid`` are stamped onto the
        TarInfo so the file appears in the container with the
        requested permissions and ownership atomically — there is no
        post-write ``chmod`` window during which a secret-bearing
        file is world-readable (audit finding M1).

        This is the workspace-file-write funnel the commit-guard
        carrier guards (design §8): in an enforced lane (an HTTP
        request or a carrier-launched durable task) the write refuses
        to proceed without a live, still-current carrier admission for
        this container — before any byte reaches the daemon.
        """
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFileViaTar",
        )
        import posixpath
        import time

        bufferTar = self._fbufferBuildTar(
            sFilePath, baContent, iMode, iUid, iGid, int(time.time()),
        )
        sDirectory = posixpath.dirname(sFilePath)
        container = self.fcontainerGetById(sContainerId)
        container.put_archive(sDirectory, bufferTar)

    @staticmethod
    def _fbufferBuildTar(
        sFilePath, baContent, iMode, iUid, iGid, iMtime,
    ):
        """Return a BytesIO holding the tarball for put_archive."""
        import io
        import posixpath
        import tarfile
        sFilename = posixpath.basename(sFilePath)
        bufferTar = io.BytesIO()
        with tarfile.open(fileobj=bufferTar, mode="w") as tar:
            infoTar = DockerConnection._finfoBuildTarEntry(
                sFilename, len(baContent), iMode, iUid, iGid,
            )
            infoTar.mtime = iMtime
            tar.addfile(infoTar, io.BytesIO(baContent))
        bufferTar.seek(0)
        return bufferTar

    @staticmethod
    def _finfoBuildTarEntry(sFilename, iSize, iMode, iUid, iGid):
        """Return a TarInfo with the requested mode/owner stamps.

        Defaults ``iUid``/``iGid`` to the container's unprivileged user
        (``_I_CONTAINER_DEFAULT_UID`` / ``_I_CONTAINER_DEFAULT_GID``)
        rather than letting ``tarfile.TarInfo``'s native default of 0
        through to ``put_archive``. Without this, every backend write
        materialises root-owned inside the container and silently locks
        the file against any later in-container edit — the in-container
        agent has no sudo by design (commit 426f6b7).
        """
        import tarfile
        infoTar = tarfile.TarInfo(name=sFilename)
        infoTar.size = iSize
        if iMode is not None:
            infoTar.mode = iMode
        infoTar.uid = iUid if iUid is not None else _I_CONTAINER_DEFAULT_UID
        infoTar.gid = iGid if iGid is not None else _I_CONTAINER_DEFAULT_GID
        return infoTar

    def fsExecCreate(
        self, sContainerId, sCommand="/bin/bash", sUser=None,
        listCommand=None,
    ):
        """Create an interactive exec instance, return exec id.

        Defaults to the unprivileged container user when ``sUser`` is
        omitted so terminal sessions opened from the dashboard do not
        land as root. ``listCommand`` bypasses docker-py's shlex split
        of a string command for callers that need an exact argv (the
        terminal containment wrapper's ``/bin/sh -c`` script would be
        destroyed by tokenization).
        """
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = {
            "cmd": listCommand if listCommand is not None else sCommand,
            "tty": True,
            "stdin": True,
            "stdout": True,
            "stderr": True,
            "user": sUser,
        }
        sExecId = self._clientDocker.api.exec_create(
            container.id, **dictKwargs
        )["Id"]
        return sExecId

    def fsocketExecStart(self, sExecId):
        """Start exec and return the raw socket."""
        return self._clientDocker.api.exec_start(
            sExecId, socket=True, tty=True
        )

    def fnExecResize(self, sExecId, iRows, iColumns):
        """Resize the PTY of an exec instance."""
        self._clientDocker.api.exec_resize(
            sExecId, height=iRows, width=iColumns
        )

    def fdictInspectExec(self, sExecId):
        """Return the daemon's inspect payload for an exec instance.

        The probe half of the operation journal's exec and terminal
        verifiers (design §8): ``Running`` distinguishes a live exec
        from a settled one. Named to match the duck-typed contract the
        journal's probe catalog checks with ``hasattr``.
        """
        return self._clientDocker.api.exec_inspect(sExecId)

    def ftRunRootShellProbe(self, sContainerId, sScript):
        """Run a ``/bin/sh`` script as root; return (iExitCode, sOutput).

        The containment-probe primitive (design v13 §6.1): group
        discovery, group signalling, and group-emptiness proof all run
        through it. It deliberately uses ``/bin/sh`` — not the bash the
        ordinary exec paths assume — so the probes work in minimal
        images, and root so they can signal the unprivileged terminal
        user's processes. Probes are part of the authority machinery,
        not route-reachable mutations, so they carry no journal record.
        """
        container = self.fcontainerGetById(sContainerId)
        iExitCode, baOutput = container.exec_run(
            ["/bin/sh", "-c", sScript], user="root", demux=False,
        )
        sOutput = (baOutput or b"").decode("utf-8", errors="replace")
        return (-1 if iExitCode is None else int(iExitCode), sOutput)

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        """Count in-container processes in a session/process group.

        Walks ``/proc/*/stat`` inside the container and counts every
        process whose process group OR session equals
        ``iProcessGroup`` (a terminal shell's job control moves
        children to new groups within the same session, so matching
        the group alone would miss exactly the detached descendants
        this probe exists to find). Returns ``bConclusive`` False when
        the probe could not run; a definitively absent or stopped
        container is conclusive with zero members, since no process
        survives its container.
        """
        sScript = _fsBuildProcessGroupScript(iProcessGroup, ":")
        try:
            iExitCode, sOutput = self.ftRunRootShellProbe(
                sContainerId, sScript,
            )
        except Exception as error:
            if _fbErrorMeansContainerGone(error):
                return {
                    "bConclusive": True, "iMemberCount": 0,
                    "sDetail": f"container is gone or stopped: {error}",
                }
            return {
                "bConclusive": False, "iMemberCount": -1,
                "sDetail": f"process-group probe failed: {error}",
            }
        iMemberCount = _fiParseMemberCount(sOutput)
        if iExitCode != 0 or iMemberCount < 0:
            return {
                "bConclusive": False, "iMemberCount": -1,
                "sDetail": (
                    "process-group probe gave no parseable count "
                    f"(exit {iExitCode}): {sOutput[:200]!r}"
                ),
            }
        return {
            "bConclusive": True, "iMemberCount": iMemberCount,
            "sDetail": f"{iMemberCount} live member(s)",
        }

    def fnSignalProcessGroupMembers(
        self, sContainerId, iProcessGroup, sSignalName,
    ):
        """Signal every in-container process of a session/process group.

        ``sSignalName`` is allowlisted to TERM and KILL. A container
        that is already gone or stopped needs no signal and is treated
        as a quiet success; any other probe failure is also quiet —
        the terminate-and-prove caller decides on the PROOF, never on
        the signal delivery.
        """
        if sSignalName not in ("TERM", "KILL"):
            raise ValueError(
                f"Unsupported process-group signal {sSignalName!r}; "
                "only TERM and KILL are allowlisted"
            )
        sScript = _fsBuildProcessGroupScript(
            iProcessGroup, f'kill -{sSignalName} "$iMemberPid" 2>/dev/null',
        )
        try:
            self.ftRunRootShellProbe(sContainerId, sScript)
        except Exception:
            pass


# POSIX-sh walk of /proc/*/stat matching a target session/process
# group. The comm field can contain spaces and parentheses, so the
# fields after it are recovered by stripping through the LAST ')'
# (``${sStatContent##*) }``); positional field 3 is then pgrp and 4 is
# session. The probe excludes its own shell by pid, and IGROUPTARGET
# is substituted only after integer validation, so no caller-supplied
# text can reach the script.
_S_PROCESS_GROUP_SCRIPT_TEMPLATE = """iCount=0
for sStatPath in /proc/[0-9]*/stat; do
  sStatContent=$(cat "$sStatPath" 2>/dev/null) || continue
  sStatTail="${sStatContent##*) }"
  set -- $sStatTail
  [ "$3" = "IGROUPTARGET" ] || [ "$4" = "IGROUPTARGET" ] || continue
  iMemberPid="${sStatPath#/proc/}"
  iMemberPid="${iMemberPid%/stat}"
  [ "$iMemberPid" = "$$" ] && continue
  iCount=$((iCount+1))
  PERMEMBERACTION
done
printf 'iMembers=%s\\n' "$iCount"
"""


def _fsBuildProcessGroupScript(iProcessGroup, sPerMemberAction):
    """Return the /proc-walk script for one validated group id."""
    if not isinstance(iProcessGroup, int) or isinstance(
        iProcessGroup, bool,
    ) or iProcessGroup <= 0:
        raise ValueError(
            f"A process group id must be a positive integer, got "
            f"{iProcessGroup!r}"
        )
    return _S_PROCESS_GROUP_SCRIPT_TEMPLATE.replace(
        "IGROUPTARGET", str(iProcessGroup),
    ).replace("PERMEMBERACTION", sPerMemberAction)


def _fiParseMemberCount(sOutput):
    """Return the iMembers count from probe output, or -1 unparseable."""
    for sLine in sOutput.splitlines():
        sLine = sLine.strip()
        if sLine.startswith("iMembers="):
            try:
                return int(sLine[len("iMembers="):])
            except ValueError:
                return -1
    return -1


def _fbErrorMeansContainerGone(error):
    """Return True when an exec error proves the container has no processes.

    A 404 (no such container) or a 409 "is not running" both mean no
    process can remain inside it — the container-stop fallback the
    design names (v13 §6.1) observed working. Anything else proves
    nothing.
    """
    iStatusCode = getattr(error, "status_code", None)
    if iStatusCode is None:
        iStatusCode = getattr(
            getattr(error, "response", None), "status_code", None,
        )
    if iStatusCode == 404:
        return True
    return iStatusCode == 409 and "not running" in str(error).lower()


def _fiterChunksFromTarStream(iterTarStream, iChunkSizeBytes):
    """Yield the single-file payload from a docker get_archive stream.

    ``iterTarStream`` is the first element of the tuple returned by
    ``container.get_archive``: a generator of raw tar bytes. We pipe
    those bytes into a ``tarfile`` opened in streaming mode
    (``mode="r|"``), pull the first (and only) member, and copy its
    payload to the caller in ``iChunkSizeBytes``-sized chunks. The
    file is never fully materialised on the host.

    The ``try/finally`` releases the underlying docker-py HTTP socket
    if the consumer stops iterating early (e.g. a downstream
    StreamingResponse cancelled by an HTTP client disconnect). Without
    it, urllib3 reclaims the connection on its own schedule and the
    docker pool can run hot on a long-uptime host.
    """
    import tarfile
    fileTarPipe = _BytesGeneratorPipe(iterTarStream)
    try:
        with tarfile.open(fileobj=fileTarPipe, mode="r|") as tar:
            for infoMember in tar:
                if not infoMember.isfile():
                    continue
                fileExtract = tar.extractfile(infoMember)
                if fileExtract is None:
                    continue
                yield from _fiterFileChunks(fileExtract, iChunkSizeBytes)
                return
    finally:
        fnClose = getattr(iterTarStream, "close", None)
        if callable(fnClose):
            try:
                fnClose()
            except Exception:
                pass


def _fiterFileChunks(fileObj, iChunkSizeBytes):
    """Yield successive ``iChunkSizeBytes``-sized chunks from fileObj."""
    while True:
        baChunk = fileObj.read(iChunkSizeBytes)
        if not baChunk:
            return
        yield baChunk


class _BytesGeneratorPipe:
    """Read-only file-like adapter over a generator of bytes chunks.

    ``tarfile.open(mode="r|")`` consumes a file-like object exposing
    ``.read(n)``; ``container.get_archive`` produces a generator of
    arbitrary-sized byte chunks. This adapter buffers across chunk
    boundaries so each read returns the requested length without
    accumulating the whole archive in memory.
    """

    def __init__(self, iterChunks):
        self._iterChunks = iter(iterChunks)
        self._baBuffer = b""
        self._bExhausted = False

    def read(self, iSize=-1):
        if iSize is None or iSize < 0:
            return self._fbaDrainAll()
        while len(self._baBuffer) < iSize and not self._bExhausted:
            self._fnPullOneChunk()
        baOut = self._baBuffer[:iSize]
        self._baBuffer = self._baBuffer[iSize:]
        return baOut

    def _fnPullOneChunk(self):
        try:
            self._baBuffer += next(self._iterChunks)
        except StopIteration:
            self._bExhausted = True

    def _fbaDrainAll(self):
        while not self._bExhausted:
            self._fnPullOneChunk()
        baOut = self._baBuffer
        self._baBuffer = b""
        return baOut
