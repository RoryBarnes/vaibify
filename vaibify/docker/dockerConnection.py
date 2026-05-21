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


def _fsResolveContainerUser(container):
    """Return the unprivileged user baked into the image, cached per id.

    Defaults to ``researcher`` when the image has no ``User`` field
    (older images built before USER was pinned). Docker SDK exposes
    the value via ``container.attrs["Config"]["User"]``; we cache by
    container id so we only inspect once per session.
    """
    sContainerId = getattr(container, "id", None) or ""
    if isinstance(sContainerId, str) and sContainerId in _CACHED_CONTAINER_USER:
        return _CACHED_CONTAINER_USER[sContainerId]
    sUser = "researcher"
    try:
        sValue = container.attrs["Config"]["User"]
        if isinstance(sValue, str) and sValue:
            sUser = sValue
    except (AttributeError, KeyError, TypeError):
        pass
    if isinstance(sContainerId, str):
        _CACHED_CONTAINER_USER[sContainerId] = sUser
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
        self._clientDocker = _fmoduleGetDocker().from_env()
        self._dictContainers = {}

    def flistGetRunningContainers(self):
        """Return list of dicts with container id, name, image."""
        listContainers = self._clientDocker.containers.list(
            filters={"status": "running"}
        )
        listResult = []
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
        return listResult

    def fcontainerGetById(self, sContainerId):
        """Return the container object, refreshing if needed."""
        if sContainerId in self._dictContainers:
            return self._dictContainers[sContainerId]
        container = self._clientDocker.containers.get(sContainerId)
        self._dictContainers[sContainerId] = container
        return container

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
