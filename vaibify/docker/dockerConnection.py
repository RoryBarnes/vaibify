"""Docker container discovery, command execution, and file transfer.

Wraps the docker-py SDK with lazy import so the module can be loaded
even when docker-py is not installed.

Stream separation
-----------------

``texecRunInContainerStreamed`` is the canonical execution entry
point. It captures stdout and stderr separately and returns an
``ExecResult`` dataclass so callers can render real container output
distinctly from container-side error noise. The legacy
``ftResultExecuteCommand`` is preserved as a thin backward-compat
wrapper that merges the two streams and emits a ``DeprecationWarning``
on every call, giving downstream callers an audit trail to migrate
on their own schedule (audit finding F-R-01).
"""

import base64
import warnings
from dataclasses import dataclass


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

    docker-py exposes its ``requests.Session`` as ``client.api``.
    Replacing both schemes covers TCP daemons; the unix-socket
    adapter is mounted by docker-py at ``http+docker://`` with its
    default pool size and is replaced here in-place.
    """
    from requests.adapters import HTTPAdapter
    session = getattr(clientDocker, "api", None)
    if session is None or not hasattr(session, "mount"):
        return
    for sPrefix in ("http://", "https://", "http+docker://"):
        try:
            session.mount(sPrefix, HTTPAdapter(
                pool_connections=I_DOCKER_POOL_MAX_SIZE,
                pool_maxsize=I_DOCKER_POOL_MAX_SIZE,
            ))
        except Exception:
            continue


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
            "docker package required for GUI features. "
            "Install with: pip install vaibify[docker]"
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

    def texecRunInContainerStreamed(
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

    def texecRunInContainerStreamedWithChunks(
        self, sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None,
    ):
        """Run a command, invoking ``fnEmitChunk(sStream, sLine)`` per line.

        ``sStream`` is ``"stdout"`` or ``"stderr"``; ``sLine`` is the
        decoded text with the trailing newline stripped. Partial
        trailing data is buffered across docker-py chunks and flushed
        on process exit. Returns an :class:`ExecResult` with the same
        contract as :meth:`texecRunInContainerStreamed` so callers can
        keep their post-exec bookkeeping unchanged.
        """
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = self._fdictBuildExecCreateKwargs(
            sCommand, sWorkdir, sUser,
        )
        sExecId = self._clientDocker.api.exec_create(
            container.id, **dictKwargs,
        )["Id"]
        sStdout, sStderr = self._ftStreamExecLines(sExecId, fnEmitChunk)
        dictInspect = self._clientDocker.api.exec_inspect(sExecId)
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
        in ``texecRunInContainerStreamedWithChunks``; the only in-tree
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
        in audits while migrating to ``texecRunInContainerStreamed``.
        """
        warnings.warn(
            "ftResultExecuteCommand merges stdout and stderr; "
            "migrate to texecRunInContainerStreamed for split "
            "streams.",
            DeprecationWarning,
            stacklevel=2,
        )
        resultExec = self.texecRunInContainerStreamed(
            sContainerId, sCommand, sWorkdir=sWorkdir, sUser=sUser,
        )
        sOutput = resultExec.sStdout + resultExec.sStderr
        return (resultExec.iExitCode, sOutput)

    def fbaFetchFile(self, sContainerId, sFilePath):
        """Fetch a file from the container and return its bytes."""
        sSafePath = repr(sFilePath)
        sCommand = (
            "python3 -c \"import base64,sys; "
            "sys.stdout.buffer.write("
            "base64.b64encode(open("
            + sSafePath + ",'rb').read()))\""
        )
        resultExec = self.texecRunInContainerStreamed(
            sContainerId, sCommand,
        )
        if resultExec.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot read file from container: {sFilePath}"
            )
        return base64.b64decode(resultExec.sStdout.strip())

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
        """
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
        """Return a TarInfo with the requested mode/owner stamps."""
        import tarfile
        infoTar = tarfile.TarInfo(name=sFilename)
        infoTar.size = iSize
        if iMode is not None:
            infoTar.mode = iMode
        if iUid is not None:
            infoTar.uid = iUid
        if iGid is not None:
            infoTar.gid = iGid
        return infoTar

    def fsExecCreate(
        self, sContainerId, sCommand="/bin/bash", sUser=None
    ):
        """Create an interactive exec instance, return exec id.

        Defaults to the unprivileged container user when ``sUser`` is
        omitted so terminal sessions opened from the dashboard do not
        land as root.
        """
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = {
            "cmd": sCommand,
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
