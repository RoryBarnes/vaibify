"""Filesystem adapter seam for the reproducibility envelope.

Every reproducibility module performs its file IO through one of the
adapters defined here so the same gate/verifier code is honest on a
host clone (``HostRepoFiles``), inside a running container
(``ContainerRepoFiles``), or against a single-poll snapshot
(``SnapshotRepoFiles``). All paths in the adapter API are
repo-relative POSIX strings; the adapter owns the root.

``ContainerRepoFiles`` takes a duck-typed docker connection (the same
contract as ``reproduceScriptGenerator.fnGenerateReproduceScript``) so
this module never imports ``vaibify.docker``. Symlink-component and
realpath-escape enforcement live in the adapter: the container
implementation runs them *inside* the container, where the truth
about container-side symlinks actually lives.
"""

import base64
import fcntl
import hashlib
import json
import os
import posixpath
import subprocess
import threading
import time

__all__ = [
    "HostRepoFiles",
    "ContainerRepoFiles",
    "SnapshotRepoFiles",
    "ffilesEnsureRepoFiles",
    "fnInjectManifestTextIntoSnapshot",
    "fsRepoRootOf",
    "fsShellQuotePosix",
    "TUPLE_SNAPSHOT_CONTENT_PATHS",
    "TUPLE_SNAPSHOT_SKIP_TEXT_PATHS",
]


_I_STAT_BATCH_SIZE = 200
_I_LOCK_RETRY_MAX = 30
_F_LOCK_RETRY_SLEEP = 0.05
_I_HASH_BLOCK_SIZE = 65536


def ffilesEnsureRepoFiles(filesOrPath):
    """Return filesOrPath unchanged if it is an adapter; wrap str in HostRepoFiles.

    ``None`` maps to a ``HostRepoFiles("")`` whose probes all return
    False, so legacy callers that pass a missing project-repo path keep
    their conservative behavior.
    """
    if isinstance(filesOrPath, (str, type(None))):
        return HostRepoFiles(filesOrPath or "")
    return filesOrPath


def fsRepoRootOf(filesOrPath):
    """Return the repo-root path string for a str or adapter argument."""
    if isinstance(filesOrPath, str):
        return filesOrPath
    if filesOrPath is None:
        return ""
    return getattr(filesOrPath, "sRootPath", "") or ""


def fsShellQuotePosix(sValue):
    """Return sValue as a POSIX single-quoted shell argument."""
    return "'" + sValue.replace("'", "'\\''") + "'"


def _fbRelativePathSane(sRelPath):
    """Return True iff sRelPath is relative and free of ``..`` segments."""
    if not sRelPath or os.path.isabs(sRelPath):
        return False
    listSegments = [s for s in sRelPath.split("/") if s]
    return ".." not in listSegments


def _fnRequireWritableRelativePath(sRootPath, sRelPath):
    """Raise ValueError when a write target could leave the repo root.

    Every adapter write lands at ``<root>/<sRelPath>``; an empty root
    would silently write relative to the process working directory and
    an absolute or ``..``-bearing relative path would escape the
    repository, so both are refused before any IO happens.
    """
    if not sRootPath:
        raise ValueError(
            f"refusing to write without a repository root: '{sRelPath}'"
        )
    if not _fbRelativePathSane(sRelPath):
        raise ValueError(
            f"refusing to write outside the repository root: '{sRelPath}'"
        )


class _RepoLockHolder:
    """Context manager wrapping an open flock file descriptor."""

    def __init__(self, iFileDescriptor):
        self.iFileDescriptor = iFileDescriptor

    def __enter__(self):
        return self

    def __exit__(self, classExc, valueExc, traceback):
        try:
            fcntl.flock(self.iFileDescriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.iFileDescriptor)


class HostRepoFiles:
    """Repo-file adapter backed by the host filesystem.

    A thin wrapper over the pathlib/os/fcntl idioms the reproducibility
    modules used before the adapter seam existed, so host-side callers
    (the reproduce CLI, director, unit tests) keep bit-identical
    semantics.
    """

    def __init__(self, sRootPath):
        self.sRootPath = sRootPath or ""

    def fsLocalRootOrNone(self):
        """Return the host root path, or None when unset."""
        return self.sRootPath or None

    def _fsAbsolute(self, sRelPath):
        """Return the host-absolute path for a repo-relative path."""
        return os.path.join(self.sRootPath, *sRelPath.split("/"))

    def fbIsFile(self, sRelPath):
        """Return True iff the repo-relative path is an existing file."""
        if not self.sRootPath:
            return False
        return os.path.isfile(self._fsAbsolute(sRelPath))

    def fbIsDir(self, sRelPath):
        """Return True iff the repo-relative path is an existing directory."""
        if not self.sRootPath:
            return False
        return os.path.isdir(self._fsAbsolute(sRelPath))

    def fsReadText(self, sRelPath):
        """Return the file's text contents; raises OSError when unreadable."""
        with open(self._fsAbsolute(sRelPath), "r", encoding="utf-8") as f:
            return f.read()

    def fbaReadBytes(self, sRelPath):
        """Return the file's raw bytes; raises OSError when unreadable."""
        with open(self._fsAbsolute(sRelPath), "rb") as fileHandle:
            return fileHandle.read()

    def fnWriteTextAtomic(self, sRelPath, sContent):
        """Write text atomically via a sibling temp file + os.replace."""
        _fnRequireWritableRelativePath(self.sRootPath, sRelPath)
        sAbsolute = self._fsAbsolute(sRelPath)
        os.makedirs(os.path.dirname(sAbsolute) or ".", exist_ok=True)
        sTempPath = sAbsolute + ".tmp"
        try:
            with open(sTempPath, "w", encoding="utf-8", newline="\n") as f:
                f.write(sContent)
            os.replace(sTempPath, sAbsolute)
        except OSError:
            _fnRemoveQuietly(sTempPath)
            raise

    def fnWriteJsonAtomic(self, sRelPath, dictPayload):
        """Write a JSON payload atomically (indent=2, sorted keys)."""
        self.fnWriteTextAtomic(
            sRelPath,
            json.dumps(dictPayload, indent=2, sort_keys=True),
        )

    def fbRemoveFile(self, sRelPath):
        """Remove the file; return True iff a file was actually removed."""
        if not self.sRootPath or not _fbRelativePathSane(sRelPath):
            return False
        sAbsolute = self._fsAbsolute(sRelPath)
        if not os.path.isfile(sAbsolute):
            return False
        try:
            os.remove(sAbsolute)
        except OSError:
            return False
        return True

    def flistListJsonFilenames(self, sRelDir):
        """Return ``*.json`` filenames in the directory, sorted descending."""
        sAbsolute = self._fsAbsolute(sRelDir)
        if not os.path.isdir(sAbsolute):
            return []
        return sorted(
            (sName for sName in os.listdir(sAbsolute)
             if sName.endswith(".json")),
            reverse=True,
        )

    def fdictReadDirJsonContents(self, sRelDir):
        """Return ``{sFilename: sContents}`` for every readable ``*.json``."""
        dictContents = {}
        for sName in self.flistListJsonFilenames(sRelDir):
            try:
                dictContents[sName] = self.fsReadText(
                    posixpath.join(sRelDir, sName),
                )
            except OSError:
                continue
        return dictContents

    def fdictStatMtimes(self, listRelPaths):
        """Return ``{sRelPath: iMtime}``; missing files are omitted."""
        dictResult = {}
        for sRelPath in listRelPaths:
            try:
                dictResult[sRelPath] = int(
                    os.stat(self._fsAbsolute(sRelPath)).st_mtime,
                )
            except OSError:
                continue
        return dictResult

    def fdictHashFiles(self, listRelPaths):
        """Hash repo-relative files with symlink + escape enforcement.

        Returns ``{sRelPath: {"sSha256": str|None,
        "sSymlinkSegment": str|None, "bEscapesRoot": bool}}``. A
        missing or unreadable file yields ``sSha256 = None``; the
        enforcement fields let callers (``manifestWriter``) raise the
        same errors they historically raised.
        """
        dictResult = {}
        for sRelPath in listRelPaths:
            dictResult[sRelPath] = self._fdictHashOneFile(sRelPath)
        return dictResult

    def _fdictHashOneFile(self, sRelPath):
        """Return the hash-entry dict for one repo-relative path.

        A symlink component is reported in ``sSymlinkSegment`` but is
        only fatal when its resolved target escapes the repo root
        (``bEscapesRoot``). An in-root symlink hashes its resolved
        target so the manifest can pin the content under the declared
        path; an out-of-root target is never opened or hashed.
        """
        dictEntry = {
            "sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False,
        }
        if os.path.isabs(sRelPath) or not self.sRootPath:
            dictEntry["bEscapesRoot"] = True
            return dictEntry
        dictEntry["sSymlinkSegment"] = self._fsFirstSymlinkSegment(sRelPath)
        if self._fbEscapesRoot(sRelPath):
            dictEntry["bEscapesRoot"] = True
            return dictEntry
        dictEntry["sSha256"] = _fsHashHostFileOrNone(
            os.path.realpath(self._fsAbsolute(sRelPath)),
        )
        return dictEntry

    def _fsFirstSymlinkSegment(self, sRelPath):
        """Return the first symlinked segment along the path, or None."""
        sCurrent = self.sRootPath
        for sSegment in (s for s in sRelPath.split("/") if s):
            sCurrent = os.path.join(sCurrent, sSegment)
            if os.path.islink(sCurrent):
                return sSegment
        return None

    def _fbEscapesRoot(self, sRelPath):
        """Return True iff the path's realpath leaves the repo root."""
        sRepoReal = os.path.realpath(self.sRootPath)
        sCandidateReal = os.path.realpath(
            os.path.join(sRepoReal, sRelPath),
        )
        return sCandidateReal != sRepoReal and not sCandidateReal.startswith(
            sRepoReal + os.sep,
        )

    def fdictHashAbsolutePaths(self, listAbsPaths):
        """Return ``{sAbsPath: sSha256|None}`` for host-absolute paths."""
        dictResult = {}
        for sAbsPath in listAbsPaths:
            if not sAbsPath or not os.path.isfile(sAbsPath):
                dictResult[sAbsPath] = None
                continue
            dictResult[sAbsPath] = _fsHashHostFileOrNone(sAbsPath)
        return dictResult

    def ftRunCommand(self, saCommand, fTimeoutSeconds):
        """Run a host command; return ``(iExitCode, sStdout, sStderr)``.

        Failure to launch (missing binary, timeout) maps to exit code
        127 with empty output so callers can treat all failures
        uniformly.
        """
        try:
            resultProcess = subprocess.run(
                saCommand, capture_output=True, text=True,
                timeout=fTimeoutSeconds,
            )
        except (OSError, subprocess.SubprocessError):
            return (127, "", "")
        return (
            resultProcess.returncode,
            resultProcess.stdout or "",
            resultProcess.stderr or "",
        )

    def fnWithLock(self, sRelPath):
        """Return a context manager holding an exclusive flock.

        Locks a sibling ``<file>.lock`` with the bounded non-blocking
        retry loop the syncStatus writer historically used; raises
        ``RuntimeError`` on retry exhaustion so a stale holder is
        surfaced, never silently overwritten.
        """
        _fnRequireWritableRelativePath(self.sRootPath, sRelPath)
        sAbsolute = self._fsAbsolute(sRelPath)
        os.makedirs(os.path.dirname(sAbsolute) or ".", exist_ok=True)
        return _fnAcquireHostLock(sAbsolute + ".lock")


def _fnAcquireHostLock(sLockPath):
    """Acquire an exclusive flock on sLockPath with bounded retries."""
    iFileDescriptor = os.open(sLockPath, os.O_WRONLY | os.O_CREAT, 0o600)
    for _iAttempt in range(_I_LOCK_RETRY_MAX):
        try:
            fcntl.flock(iFileDescriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _RepoLockHolder(iFileDescriptor)
        except BlockingIOError:
            time.sleep(_F_LOCK_RETRY_SLEEP)
    os.close(iFileDescriptor)
    raise RuntimeError(
        f"could not acquire lock at '{sLockPath}' "
        f"after {_I_LOCK_RETRY_MAX} attempts"
    )


def _fsHashHostFileOrNone(sAbsPath):
    """Return the SHA-256 of a host file, refusing symlinks; None on error."""
    iFlags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        iFileDescriptor = os.open(sAbsPath, iFlags)
    except OSError:
        return None
    hasher = hashlib.sha256()
    with os.fdopen(iFileDescriptor, "rb", closefd=True) as fileHandle:
        while True:
            baBlock = fileHandle.read(_I_HASH_BLOCK_SIZE)
            if not baBlock:
                break
            hasher.update(baBlock)
    return hasher.hexdigest()


def _fnRemoveQuietly(sPath):
    """Remove a path, swallowing OSError (temp-file cleanup)."""
    try:
        os.remove(sPath)
    except OSError:
        pass


# ------------------------------------------------------------------
# Container adapter
# ------------------------------------------------------------------


# Executed inside the container via ``python3 -c "exec(base64...)"``.
# Receives a base64-JSON payload {sRoot, listRelPaths} and prints one
# JSON blob mapping each path to its hash entry, enforcing symlink-
# component rejection and realpath containment inside the container.
_S_HASH_SCRIPT = '''
import base64, hashlib, json, os, sys
dictArgs = json.loads(base64.b64decode(%(payload)s).decode())
sRoot = dictArgs["sRoot"]
dictOut = {}
def _fsHash(sAbs):
    iFlags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        iFd = os.open(sAbs, iFlags)
    except OSError:
        return None
    h = hashlib.sha256()
    with os.fdopen(iFd, "rb") as f:
        for ba in iter(lambda: f.read(65536), b""):
            h.update(ba)
    return h.hexdigest()
def _fdictEntry(sRel):
    d = {"sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False}
    if os.path.isabs(sRel):
        d["bEscapesRoot"] = True
        return d
    sCur = sRoot
    for sSeg in [s for s in sRel.split("/") if s]:
        sCur = os.path.join(sCur, sSeg)
        if os.path.islink(sCur):
            d["sSymlinkSegment"] = sSeg
            break
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRel))
    if sReal != sRootReal and not sReal.startswith(sRootReal + os.sep):
        d["bEscapesRoot"] = True
        return d
    d["sSha256"] = _fsHash(sReal)
    return d
for sRel in dictArgs["listRelPaths"]:
    dictOut[sRel] = _fdictEntry(sRel)
sys.stdout.write(json.dumps(dictOut))
'''


# Hashes absolute container paths (declared binaries); no containment
# checks because binaries legitimately live outside the project repo.
_S_HASH_ABSOLUTE_SCRIPT = '''
import base64, hashlib, json, sys
listPaths = json.loads(base64.b64decode(%(payload)s).decode())
dictOut = {}
for sPath in listPaths:
    try:
        h = hashlib.sha256()
        with open(sPath, "rb") as f:
            for ba in iter(lambda: f.read(65536), b""):
                h.update(ba)
        dictOut[sPath] = h.hexdigest()
    except (OSError, TypeError):
        dictOut[sPath] = None
sys.stdout.write(json.dumps(dictOut))
'''


# Reads every *.json file in a directory; prints {filename: contents}.
_S_READ_DIR_JSON_SCRIPT = '''
import base64, json, os, sys
sDir = json.loads(base64.b64decode(%(payload)s).decode())
dictOut = {}
if os.path.isdir(sDir):
    for sName in sorted(os.listdir(sDir), reverse=True):
        if not sName.endswith(".json"):
            continue
        try:
            with open(os.path.join(sDir, sName), "r") as f:
                dictOut[sName] = f.read()
        except OSError:
            continue
sys.stdout.write(json.dumps(dictOut))
'''


def _fsBuildEmbeddedScriptCommand(sScriptTemplate, jsonPayload):
    """Return a python3 command running the base64-embedded script.

    The payload travels as base64-JSON inside a ``repr()`` literal so
    no shell or Python quoting ever depends on user-controlled path
    contents (same defense as ``syncDispatcher``'s archive script).
    """
    sPayloadB64 = base64.b64encode(
        json.dumps(jsonPayload).encode("utf-8"),
    ).decode("ascii")
    sScript = sScriptTemplate % {"payload": repr(sPayloadB64)}
    sScriptB64 = base64.b64encode(sScript.encode("utf-8")).decode("ascii")
    return (
        "python3 -c \"import base64; "
        f"exec(base64.b64decode('{sScriptB64}').decode())\""
    )


def _fdictParseEmbeddedScriptOutput(sStdout):
    """Parse the JSON blob an embedded script printed; {} on garbage."""
    try:
        dictParsed = json.loads(sStdout or "{}")
    except ValueError:
        return {}
    return dictParsed if isinstance(dictParsed, dict) else {}


# Process-local write locks for container-side read-modify-write
# files, keyed by (sContainerId, sRelPath). Honest because every
# container-path writer (verify route, scheduled loop, UI button)
# lives in the single FastAPI process.
_DICT_CONTAINER_LOCKS = {}
_LOCK_REGISTRY = threading.Lock()


class ContainerRepoFiles:
    """Repo-file adapter that routes every operation through docker exec.

    ``connectionDocker`` is duck-typed: it must provide
    ``texecRunInContainerStreamed``, ``fbaFetchFile``, and
    ``fnWriteFile`` (the ``DockerConnection`` contract). All repo
    paths are container-side POSIX paths rooted at ``sRootPath``.
    """

    def __init__(self, connectionDocker, sContainerId, sRootPath):
        self.connectionDocker = connectionDocker
        self.sContainerId = sContainerId
        self.sRootPath = sRootPath or ""

    def fsLocalRootOrNone(self):
        """Return None: the root is a container path, not a host path."""
        return None

    def _fsAbsolute(self, sRelPath):
        """Return the container-absolute path for a repo-relative path."""
        return posixpath.join(self.sRootPath, sRelPath)

    def _ftExec(self, sCommand):
        """Run one container command; return ``(iExitCode, sStdout)``."""
        resultExec = self.connectionDocker.texecRunInContainerStreamed(
            self.sContainerId, sCommand,
        )
        return (resultExec.iExitCode, resultExec.sStdout)

    def fbIsFile(self, sRelPath):
        """Return True iff the repo-relative path is a container file."""
        if not self.sRootPath:
            return False
        iExitCode, _s = self._ftExec(
            "test -f " + fsShellQuotePosix(self._fsAbsolute(sRelPath)),
        )
        return iExitCode == 0

    def fbIsDir(self, sRelPath):
        """Return True iff the repo-relative path is a container directory."""
        if not self.sRootPath:
            return False
        iExitCode, _s = self._ftExec(
            "test -d " + fsShellQuotePosix(self._fsAbsolute(sRelPath)),
        )
        return iExitCode == 0

    def fsReadText(self, sRelPath):
        """Return the container file's text; raises FileNotFoundError."""
        return self.fbaReadBytes(sRelPath).decode("utf-8")

    def fbaReadBytes(self, sRelPath):
        """Return the container file's bytes via exec+base64 fetch."""
        return self.connectionDocker.fbaFetchFile(
            self.sContainerId, self._fsAbsolute(sRelPath),
        )

    def fnWriteTextAtomic(self, sRelPath, sContent):
        """Write text into the container atomically (tmp + mv -f)."""
        _fnRequireWritableRelativePath(self.sRootPath, sRelPath)
        sAbsolute = self._fsAbsolute(sRelPath)
        sTempPath = sAbsolute + ".tmp"
        self._ftExec(
            "mkdir -p " + fsShellQuotePosix(posixpath.dirname(sAbsolute)),
        )
        self.connectionDocker.fnWriteFile(
            self.sContainerId, sTempPath, sContent.encode("utf-8"),
        )
        iExitCode, sStdout = self._ftExec(
            "mv -f " + fsShellQuotePosix(sTempPath)
            + " " + fsShellQuotePosix(sAbsolute),
        )
        if iExitCode != 0:
            raise OSError(
                f"container atomic write failed for '{sAbsolute}': {sStdout}"
            )

    def fnWriteJsonAtomic(self, sRelPath, dictPayload):
        """Write a JSON payload atomically (indent=2, sorted keys)."""
        self.fnWriteTextAtomic(
            sRelPath,
            json.dumps(dictPayload, indent=2, sort_keys=True),
        )

    def fbRemoveFile(self, sRelPath):
        """Remove the container file; True iff a file was removed."""
        if not self.sRootPath or not _fbRelativePathSane(sRelPath):
            return False
        sQuoted = fsShellQuotePosix(self._fsAbsolute(sRelPath))
        iExitCode, _s = self._ftExec(
            f"test -f {sQuoted} && rm -f {sQuoted}",
        )
        return iExitCode == 0

    def flistListJsonFilenames(self, sRelDir):
        """Return ``*.json`` filenames in the directory, sorted descending."""
        return sorted(self.fdictReadDirJsonContents(sRelDir), reverse=True)

    def fdictReadDirJsonContents(self, sRelDir):
        """Return ``{sFilename: sContents}`` in one container exec."""
        sCommand = _fsBuildEmbeddedScriptCommand(
            _S_READ_DIR_JSON_SCRIPT, self._fsAbsolute(sRelDir),
        )
        _iExitCode, sStdout = self._ftExec(sCommand)
        return _fdictParseEmbeddedScriptOutput(sStdout)

    def fdictStatMtimes(self, listRelPaths):
        """Return ``{sRelPath: iMtime}`` via batched ``stat`` execs."""
        dictResult = {}
        for iStart in range(0, len(listRelPaths), _I_STAT_BATCH_SIZE):
            dictResult.update(self._fdictStatBatch(
                listRelPaths[iStart:iStart + _I_STAT_BATCH_SIZE],
            ))
        return dictResult

    def _fdictStatBatch(self, listRelPaths):
        """Stat one batch of paths; parse ``name mtime`` lines."""
        if not listRelPaths:
            return {}
        dictRelByAbsolute = {
            self._fsAbsolute(sRel): sRel for sRel in listRelPaths
        }
        sPathArgs = " ".join(
            fsShellQuotePosix(s) for s in dictRelByAbsolute
        )
        _iExitCode, sStdout = self._ftExec(
            f"stat -c '%n %Y' {sPathArgs} 2>/dev/null || true",
        )
        return self._fdictParseStatLines(sStdout, dictRelByAbsolute)

    @staticmethod
    def _fdictParseStatLines(sStdout, dictRelByAbsolute):
        """Map ``name mtime`` stat lines back to repo-relative keys."""
        dictResult = {}
        for sLine in (sStdout or "").strip().split("\n"):
            listParts = sLine.strip().rsplit(" ", 1)
            if len(listParts) != 2:
                continue
            sRel = dictRelByAbsolute.get(listParts[0])
            if sRel is None:
                continue
            try:
                dictResult[sRel] = int(listParts[1])
            except ValueError:
                continue
        return dictResult

    def fdictHashFiles(self, listRelPaths):
        """Hash repo-relative container files in ONE exec.

        Same result shape as ``HostRepoFiles.fdictHashFiles``; the
        symlink-component and realpath-containment enforcement run
        inside the container, where container-side symlinks are
        actually visible.
        """
        if not listRelPaths:
            return {}
        sCommand = _fsBuildEmbeddedScriptCommand(
            _S_HASH_SCRIPT,
            {"sRoot": self.sRootPath, "listRelPaths": list(listRelPaths)},
        )
        _iExitCode, sStdout = self._ftExec(sCommand)
        return _fdictParseEmbeddedScriptOutput(sStdout)

    def fdictHashAbsolutePaths(self, listAbsPaths):
        """Return ``{sAbsPath: sSha256|None}`` hashed inside the container."""
        if not listAbsPaths:
            return {}
        sCommand = _fsBuildEmbeddedScriptCommand(
            _S_HASH_ABSOLUTE_SCRIPT, list(listAbsPaths),
        )
        _iExitCode, sStdout = self._ftExec(sCommand)
        return _fdictParseEmbeddedScriptOutput(sStdout)

    def ftRunCommand(self, saCommand, fTimeoutSeconds):
        """Run a command inside the container with a timeout guard.

        Returns ``(iExitCode, sStdout, sStderr)``. The container's
        ``timeout`` utility bounds the run so a stdin-waiting binary
        cannot hang the exec forever.
        """
        sJoined = " ".join(fsShellQuotePosix(s) for s in saCommand)
        sCommand = f"timeout {int(max(fTimeoutSeconds, 1))} {sJoined}"
        resultExec = self.connectionDocker.texecRunInContainerStreamed(
            self.sContainerId, sCommand,
        )
        return (
            resultExec.iExitCode, resultExec.sStdout, resultExec.sStderr,
        )

    def fnWithLock(self, sRelPath):
        """Return a process-local lock for a container-side file.

        A ``threading.Lock`` keyed by ``(sContainerId, sRelPath)`` is
        honest here because every writer of container-side vaibify
        state files (verify route, scheduled re-verify loop, UI
        buttons) runs inside the single FastAPI process.
        """
        tKey = (self.sContainerId, sRelPath)
        with _LOCK_REGISTRY:
            lockFile = _DICT_CONTAINER_LOCKS.setdefault(
                tKey, threading.Lock(),
            )
        return lockFile


# ------------------------------------------------------------------
# Snapshot adapter (one exec per poll)
# ------------------------------------------------------------------


# Collects the fixed envelope-file set (contents + mtimes + existence)
# plus hash entries for the requested script/output paths in one pass.
# Paths in ``listSkipTextPaths`` get mtime + bIsFile only — never their
# body. ``MANIFEST.sha256`` lives there because its body is hundreds of
# KB for a real sweep and is parsed lazily by a sha-keyed host cache
# once per manifest version, not once per poll.
_S_SNAPSHOT_SCRIPT = '''
import base64, hashlib, json, os, sys
dictArgs = json.loads(base64.b64decode(%(payload)s).decode())
sRoot = dictArgs["sRoot"]
setSkipText = set(dictArgs.get("listSkipTextPaths", []))
dictOut = {"dictFiles": {}, "dictHashes": {}, "dictAbsHashes": {}}
def _fsHash(sAbs):
    iFlags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        iFd = os.open(sAbs, iFlags)
    except OSError:
        return None
    h = hashlib.sha256()
    with os.fdopen(iFd, "rb") as f:
        for ba in iter(lambda: f.read(65536), b""):
            h.update(ba)
    return h.hexdigest()
def _fsHashFollow(sAbs):
    # Follows symlinks — declared binaries in ~/.local/bin are
    # commonly symlinks to the real executable, and we want the
    # content that actually runs. These are explicit, out-of-repo
    # workflow declarations, so there is no repo-escape concern.
    try:
        h = hashlib.sha256()
        with open(sAbs, "rb") as f:
            for ba in iter(lambda: f.read(65536), b""):
                h.update(ba)
        return h.hexdigest()
    except OSError:
        return None
for sRel in dictArgs["listContentPaths"]:
    sAbs = os.path.join(sRoot, sRel)
    dictEntry = {"bIsFile": os.path.isfile(sAbs), "sText": None,
                 "iMtime": None}
    if dictEntry["bIsFile"]:
        try:
            dictEntry["iMtime"] = int(os.stat(sAbs).st_mtime)
            if sRel not in setSkipText:
                with open(sAbs, "r") as f:
                    dictEntry["sText"] = f.read()
        except (OSError, UnicodeDecodeError):
            dictEntry["sText"] = None
    dictOut["dictFiles"][sRel] = dictEntry
def _fdictEntry(sRel):
    d = {"sSha256": None, "sSymlinkSegment": None, "bEscapesRoot": False}
    if os.path.isabs(sRel):
        d["bEscapesRoot"] = True
        return d
    sCur = sRoot
    for sSeg in [s for s in sRel.split("/") if s]:
        sCur = os.path.join(sCur, sSeg)
        if os.path.islink(sCur):
            d["sSymlinkSegment"] = sSeg
            break
    sRootReal = os.path.realpath(sRoot)
    sReal = os.path.realpath(os.path.join(sRootReal, sRel))
    if sReal != sRootReal and not sReal.startswith(sRootReal + os.sep):
        d["bEscapesRoot"] = True
        return d
    d["sSha256"] = _fsHash(sReal)
    return d
for sRel in dictArgs["listHashPaths"]:
    dictOut["dictHashes"][sRel] = _fdictEntry(sRel)
for sAbs in dictArgs.get("listAbsHashPaths", []):
    dictOut["dictAbsHashes"][sAbs] = _fsHashFollow(sAbs)
sys.stdout.write(json.dumps(dictOut))
'''


# The fixed envelope-file set every snapshot fetch reads. Existence,
# contents, and mtimes for these drive the cheap L2/L3 conjuncts.
TUPLE_SNAPSHOT_CONTENT_PATHS = (
    "MANIFEST.sha256",
    "requirements.lock",
    "Dockerfile",
    "reproduce.sh",
    ".vaibify/environment.json",
    ".vaibify/l3_attestation.json",
    ".vaibify/syncStatus.json",
    ".vaibify/overleafPushManifest.json",
    ".vaibify/ai_provenance.json",
    ".vaibify/AGENTS.md",
    ".vaibify/promptRecord/index.json",
    "CLAUDE.md",
    "AGENTS.md",
)

# Paths whose body is intentionally excluded from the poll snapshot.
# Their mtime + bIsFile + sha (via the hash batch) are still collected
# so the gates can detect a change; their text is fetched lazily by an
# explicit caller (manifest viewer route, sha-keyed parse cache).
TUPLE_SNAPSHOT_SKIP_TEXT_PATHS = (
    "MANIFEST.sha256",
    ".vaibify/AGENTS.md",
    "CLAUDE.md",
    "AGENTS.md",
)


def _fsBuildSnapshotScriptCommand(
    sRootPath, listScriptRelPaths, listHashRelPaths,
    listAbsHashPaths=None,
):
    """Return the one-exec command collecting the poll snapshot.

    ``listAbsHashPaths`` are absolute, out-of-repo paths (declared
    binaries) hashed in the SAME exec so the poll stays one round
    trip — the snapshot then answers ``fdictHashAbsolutePaths`` from
    pre-fetched values instead of a forbidden second exec.
    """
    listHashPaths = sorted(
        set(["MANIFEST.sha256"])
        | set(listScriptRelPaths or [])
        | set(listHashRelPaths or []),
    )
    return _fsBuildEmbeddedScriptCommand(
        _S_SNAPSHOT_SCRIPT, {
            "sRoot": sRootPath,
            "listContentPaths": list(TUPLE_SNAPSHOT_CONTENT_PATHS),
            "listSkipTextPaths": list(TUPLE_SNAPSHOT_SKIP_TEXT_PATHS),
            "listHashPaths": listHashPaths,
            "listAbsHashPaths": sorted(set(listAbsHashPaths or [])),
        },
    )


def fnInjectManifestTextIntoSnapshot(filesSnapshot, sManifestText):
    """Splice a lazily-fetched manifest body into a snapshot entry.

    The snapshot script omits ``MANIFEST.sha256`` text by default so a
    100-step workflow does not pay the cost of carrying a multi-KB
    body over docker exec on every poll. Callers that need the body
    (gate logic, viewer route) fetch it once per manifest sha and pass
    the text in here so subsequent ``fsReadText`` calls on the
    snapshot return the same content a live read would have produced.
    """
    if not isinstance(filesSnapshot, SnapshotRepoFiles):
        return
    dictEntry = filesSnapshot._dictFiles.get("MANIFEST.sha256")
    if not isinstance(dictEntry, dict):
        return
    dictEntry["sText"] = sManifestText
    if sManifestText is not None and not dictEntry.get("bIsFile"):
        dictEntry["bIsFile"] = True


def _fdictSnapshotFilesOrConservative(dictParsed):
    """Return the fetched file entries, or all-absent on a failed exec.

    A snapshot exec that crashed or printed garbage yields the
    conservative reading — every envelope file reported absent — so
    the gates degrade toward "not verified" for one poll (matching
    the conservative-on-error convention used throughout the gates)
    rather than crashing the whole file-status poll or, worse,
    reporting a greener state than was actually observed.
    """
    dictFiles = dictParsed.get("dictFiles")
    if isinstance(dictFiles, dict) and dictFiles:
        return dictFiles
    return {
        sRelPath: {"bIsFile": False, "sText": None, "iMtime": None}
        for sRelPath in TUPLE_SNAPSHOT_CONTENT_PATHS
    }


class SnapshotRepoFiles:
    """Read-only RepoFiles backed by a single-poll snapshot dict.

    The snapshot's lifetime is exactly one poll request — every poll
    re-fetches the container truth, so there is no TTL and nothing to
    invalidate. Every write method raises ``NotImplementedError`` so
    a future code path can never silently "write" into a cache.
    Reads of paths outside the snapshot raise ``KeyError`` rather
    than guessing.
    """

    def __init__(
        self, sRootPath, dictFiles, dictHashes, dictAbsHashes=None,
    ):
        self.sRootPath = sRootPath or ""
        self._dictFiles = dictFiles or {}
        self._dictHashes = dictHashes or {}
        self._dictAbsHashes = dictAbsHashes or {}

    @classmethod
    def ffilesFetch(
        cls, connectionDocker, sContainerId, sRootPath,
        listScriptRelPaths=None, listHashRelPaths=None,
        dictSeedHashes=None, listAbsHashPaths=None,
    ):
        """Fetch one snapshot with exactly ONE container exec.

        ``dictSeedHashes`` carries cache-validated hash entries (the
        caller revalidated each entry against the container mtime it
        fetched this same poll) for paths the exec does not need to
        rehash. Freshly fetched entries always win over seeds.
        ``listAbsHashPaths`` are out-of-repo absolute paths (declared
        binaries) hashed in the same exec and answered later via
        ``fdictHashAbsolutePaths``.
        """
        sCommand = _fsBuildSnapshotScriptCommand(
            sRootPath, listScriptRelPaths, listHashRelPaths,
            listAbsHashPaths=listAbsHashPaths,
        )
        resultExec = connectionDocker.texecRunInContainerStreamed(
            sContainerId, sCommand,
        )
        dictParsed = _fdictParseEmbeddedScriptOutput(resultExec.sStdout)
        dictHashes = dict(dictSeedHashes or {})
        dictHashes.update(dictParsed.get("dictHashes") or {})
        return cls(
            sRootPath,
            _fdictSnapshotFilesOrConservative(dictParsed),
            dictHashes,
            dictAbsHashes=dictParsed.get("dictAbsHashes") or {},
        )

    def fsLocalRootOrNone(self):
        """Return None: the snapshot mirrors container state."""
        return None

    def _fdictFileEntry(self, sRelPath):
        """Return the snapshot entry for a path; KeyError when unsampled."""
        if sRelPath not in self._dictFiles:
            raise KeyError(
                f"path not in poll snapshot: '{sRelPath}'"
            )
        return self._dictFiles[sRelPath]

    def fbIsFile(self, sRelPath):
        """Return the snapshotted existence of an envelope file."""
        if not self.sRootPath:
            return False
        return bool(self._fdictFileEntry(sRelPath).get("bIsFile"))

    def fbIsDir(self, sRelPath):
        """Directories are not sampled; snapshot has no honest answer."""
        raise NotImplementedError(
            "SnapshotRepoFiles does not sample directories"
        )

    def fsReadText(self, sRelPath):
        """Return snapshotted file text; FileNotFoundError when absent."""
        dictEntry = self._fdictFileEntry(sRelPath)
        if not dictEntry.get("bIsFile") or dictEntry.get("sText") is None:
            raise FileNotFoundError(
                f"not in container at snapshot time: '{sRelPath}'"
            )
        return dictEntry["sText"]

    def fbaReadBytes(self, sRelPath):
        """Return snapshotted file text as UTF-8 bytes."""
        return self.fsReadText(sRelPath).encode("utf-8")

    def fdictStatMtimes(self, listRelPaths):
        """Return snapshotted mtimes; unsampled paths are omitted.

        Omission (rather than raising) keeps the contract "missing
        files are omitted" and forces hash-based consumers down their
        conservative recompute path for paths the snapshot did not
        sample.
        """
        dictResult = {}
        for sRelPath in listRelPaths:
            dictEntry = self._dictFiles.get(sRelPath) or {}
            iMtime = dictEntry.get("iMtime")
            if iMtime is not None:
                dictResult[sRelPath] = iMtime
        return dictResult

    def fdictHashFiles(self, listRelPaths):
        """Return snapshotted hash entries; unsampled paths map to missing."""
        dictResult = {}
        for sRelPath in listRelPaths:
            dictResult[sRelPath] = self._dictHashes.get(sRelPath) or {
                "sSha256": None, "sSymlinkSegment": None,
                "bEscapesRoot": False,
            }
        return dictResult

    def flistListJsonFilenames(self, sRelDir):
        """Directory listings are not sampled in the poll snapshot."""
        raise NotImplementedError(
            "SnapshotRepoFiles does not sample directory listings"
        )

    def fdictReadDirJsonContents(self, sRelDir):
        """Directory contents are not sampled in the poll snapshot."""
        raise NotImplementedError(
            "SnapshotRepoFiles does not sample directory listings"
        )

    def fdictHashAbsolutePaths(self, listAbsPaths):
        """Return ``{sAbsPath: sSha256|None}`` from the pre-fetched batch.

        The poll hashes declared-binary absolute paths in its single
        exec (``listAbsHashPaths``), so this reads the snapshot rather
        than running a forbidden second exec. A path the snapshot did
        not sample maps to None — the honest "not measured" answer,
        never a guess.
        """
        return {
            sAbsPath: self._dictAbsHashes.get(sAbsPath)
            for sAbsPath in listAbsPaths
        }

    def ftRunCommand(self, saCommand, fTimeoutSeconds):
        """Command execution is a live-adapter operation."""
        raise NotImplementedError(
            "SnapshotRepoFiles cannot run commands"
        )

    def fnWriteTextAtomic(self, sRelPath, sContent):
        """Snapshots are read-only; writes must use a live adapter."""
        raise NotImplementedError("SnapshotRepoFiles is read-only")

    def fnWriteJsonAtomic(self, sRelPath, dictPayload):
        """Snapshots are read-only; writes must use a live adapter."""
        raise NotImplementedError("SnapshotRepoFiles is read-only")

    def fbRemoveFile(self, sRelPath):
        """Snapshots are read-only; writes must use a live adapter."""
        raise NotImplementedError("SnapshotRepoFiles is read-only")

    def fnWithLock(self, sRelPath):
        """Snapshots are read-only; locking implies an intent to write."""
        raise NotImplementedError("SnapshotRepoFiles is read-only")
