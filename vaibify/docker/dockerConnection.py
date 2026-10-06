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
import io
import json
import shlex
import warnings
from dataclasses import dataclass
from typing import Optional

from vaibify.config import mutationAdmission
from vaibify.docker import confinedRead
from vaibify.docker import confinedWrite
from vaibify.docker.execArgumentBudget import (
    I_EXEC_ARGUMENT_BUDGET_BYTES,
    flistBatchPathsForOneExec,
)


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

# Where ``fnWriteTreeViaTar`` stops holding the archive in memory and
# starts spilling to disk. A tree copy carries a researcher's whole
# directory, whose size nothing bounds, so the archive is spooled; the
# threshold only decides where the bytes live, never how many are
# allowed.
_I_TREE_TAR_SPOOL_BYTES = 32 * 1024 * 1024

# How much of the spooled archive goes onto the exec socket per send.
_I_STDIN_CHUNK_BYTES = 1024 * 1024
_I_MAX_READ_STDERR_BYTES = 64 * 1024
_I_EXEC_SETTLE_ATTEMPTS = 50
_F_EXEC_SETTLE_INTERVAL_SECONDS = 0.1


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
    fCpuSeconds : Optional[float]
        User+system CPU seconds of the reaped child, measured by the
        HOST leg's ``os.wait4`` reap. ``None`` means nobody measured
        it — the Docker leg always leaves it ``None`` (its CPU
        reading arrives in-band via the ``/usr/bin/time`` wrapper),
        and the host leg leaves it ``None`` when the reap was lost or
        the process was killed. Absent is never spelled 0.0.
    """

    iExitCode: int
    sStdout: str
    sStderr: str
    fCpuSeconds: Optional[float] = None


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
    """Set DOCKER_HOST from the active Docker context.

    The read itself lives in ``dockerContext``, which is also what
    ``vaibify doctor`` asks before any connection is attempted. Two
    copies of that command would let the report and the connection
    disagree about where vaibify is pointing -- and the report exists
    precisely because the two disagreeing is what a researcher cannot
    otherwise see.

    A researcher's own exported DOCKER_HOST always wins and is never
    overwritten. What IS re-read on every call is a value this
    function itself wrote, because the guard used to be "is
    DOCKER_HOST set?" and this function is what set it -- so the first
    resolution won for the life of the hub process and the Docker
    banner's Retry could never recover from a context change. A
    researcher whose active context pointed at a stopped runtime would
    fix it with ``docker context use``, click Retry, and be handed the
    identical failure naming the identical dead socket; restarting
    vaibify was the only way out, and nothing said so.
    """
    import os
    from .dockerContext import (
        fbDockerHostIsExportedByVaibify, fnRecordDockerHostExportedByVaibify,
        fsReadActiveContextEndpoint,
    )
    if os.environ.get("DOCKER_HOST") and not fbDockerHostIsExportedByVaibify():
        return
    sHost = fsReadActiveContextEndpoint()
    if sHost:
        os.environ["DOCKER_HOST"] = sHost
        fnRecordDockerHostExportedByVaibify(sHost)


# The complete set of programs the audited-read exemption will run,
# as FIXED module source text. The only thing substituted into one is a
# path, embedded through ``repr`` as a Python literal; the assembled
# program is then quoted whole as a single shell argument. An adapter
# chooses a NAME from this table and supplies a path -- it cannot
# supply a command, so it cannot supply a bad one.
# The poll snapshot's program body, shared VERBATIM by two
# transports: the typed read below (flat prefixed-argument
# preamble) and the legacy base64-JSON embedded command in
# repoFiles, which imports this constant so the two lanes cannot
# drift. Everything the snapshot reads is also hashed -- the
# reasoning lives with repoFiles._fsBuildSnapshotScriptCommand.
S_REPO_SNAPSHOT_PROGRAM_CORE = '''sRoot = dictArgs["sRoot"]
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
def _flistStatKey(statResult):
    # Nanosecond mtime AND ctime, size and inode: a program can set a
    # file's mtime back but not its ctime, and a replaced file has a
    # new inode. A whole-second mtime alone is a key a same-second
    # rewrite does not move.
    return [statResult.st_mtime_ns, statResult.st_ctime_ns,
            statResult.st_size, statResult.st_ino]
def _fdictReadOnceAgainstKey(sAbs, listCachedKey):
    iFlags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        iFd = os.open(sAbs, iFlags)
    except OSError:
        return {"sSha256": None}
    try:
        listKey = _flistStatKey(os.fstat(iFd))
        if listCachedKey and listKey == list(listCachedKey):
            return {"sSha256": None, "listStatKey": listKey,
                    "bCacheHit": True}
        h = hashlib.sha256()
        while True:
            ba = os.read(iFd, 65536)
            if not ba:
                break
            h.update(ba)
        bSteady = (_flistStatKey(os.fstat(iFd)) == listKey
                   and _flistStatKey(os.stat(sAbs)) == listKey)
    except OSError:
        return {"sSha256": None}
    finally:
        os.close(iFd)
    if not bSteady:
        return {"sSha256": None, "listStatKey": listKey, "bTornRead": True}
    return {"sSha256": h.hexdigest(), "listStatKey": listKey}
def _fdictHashAgainstKey(sAbs, listCachedKey):
    # One immediate retry; a file still changing is reported as torn,
    # never hashed and never matched.
    dictRead = _fdictReadOnceAgainstKey(sAbs, listCachedKey)
    if dictRead.get("bTornRead"):
        dictRead = _fdictReadOnceAgainstKey(sAbs, listCachedKey)
    return dictRead
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
    d.update(_fdictHashAgainstKey(
        sReal, dictArgs.get("dictCachedKeys", {}).get(sRel)))
    return d
listHashPaths = list(dictArgs["listHashPaths"])
if dictArgs.get("bHashManifestEntries"):
    try:
        with open(os.path.join(sRoot, "MANIFEST.sha256"), "r") as f:
            sManifestText = f.read()
    except (OSError, UnicodeDecodeError):
        sManifestText = ""
    for sLine in sManifestText.splitlines():
        if not sLine or sLine.startswith("#"):
            continue
        if sLine.startswith("\\\\"):
            dictOut["bManifestHasEscapedPaths"] = True
            continue
        sHashPart, sSeparator, sPathPart = sLine.partition("  ")
        if sSeparator:
            listHashPaths.append(sPathPart)
for sRel in listHashPaths:
    dictOut["dictHashes"][sRel] = _fdictEntry(sRel)
if dictArgs.get("bReadReproductions"):
    import subprocess
    dictOut["dictReproductionRecords"] = {}
    sRecordDir = os.path.join(sRoot, ".vaibify", "reproductions")
    try:
        listNames = sorted(
            [s for s in os.listdir(sRecordDir) if s.endswith(".json")],
            reverse=True)
    except FileNotFoundError:
        listNames = []
    except OSError as error:
        listNames = []
        dictOut["sReproductionsError"] = type(error).__name__
    if len(listNames) > 500:
        listNames = []
        dictOut["sReproductionsError"] = "TooManyRecords"
    setWorkflowsDecided = set()
    for sName in listNames:
        try:
            iFd = os.open(os.path.join(sRecordDir, sName),
                          os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(iFd, "rb") as f:
                baBody = f.read(4194305)
            if len(baBody) > 4194304:
                raise OSError("record too large")
            sBody = baBody.decode("utf-8")
            dictRecord = json.loads(sBody)
            if not isinstance(dictRecord, dict):
                raise ValueError("not a record")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            dictOut["sReproductionsError"] = type(error).__name__
            continue
        sWorkflow = dictRecord.get("sWorkflowRelativePath")
        if (dictRecord.get("sVerdict") in ("reproduced", "diverged")
                and sWorkflow and sWorkflow not in setWorkflowsDecided):
            setWorkflowsDecided.add(sWorkflow)
            dictOut["dictReproductionRecords"][sName] = sBody
    if dictOut["dictReproductionRecords"]:
        dictFacts = {}
        for tQuestion in @@QUESTIONS@@:
            try:
                processGit = subprocess.run(
                    ["git", "-C", sRoot] + @@HARDENING@@ + list(tQuestion),
                    capture_output=True, text=True, timeout=15)
                dictFacts[" ".join(tQuestion)] = [
                    processGit.returncode, processGit.stdout]
            except Exception:
                dictFacts[" ".join(tQuestion)] = [None, ""]
        dictOut["dictOwnershipFacts"] = dictFacts
for sAbs in dictArgs.get("listAbsHashPaths", []):
    dictOut["dictAbsHashes"][sAbs] = _fsHashFollow(sAbs)
sys.stdout.write(json.dumps(dictOut))
'''

from vaibify.reproducibility.gitHardening import (  # noqa: E402
    LIST_GIT_HARDENING_CONFIG as _LIST_SNAPSHOT_GIT_HARDENING,
    T_MANIFEST_OWNERSHIP_GIT_QUESTIONS as _T_SNAPSHOT_GIT_QUESTIONS,
)

# The program is fixed text; the two lists it embeds are fixed too, and
# are spelled in gitHardening so the predicate that reads the answers
# and the program that gives them cannot drift.
# A literal percent sign would be eaten by the legacy transport, which
# %-formats the preamble and this body together, so each one is spelled
# as an expression the program evaluates (chr(37)).
S_REPO_SNAPSHOT_PROGRAM_CORE = S_REPO_SNAPSHOT_PROGRAM_CORE.replace(
    "@@QUESTIONS@@",
    repr(tuple(_T_SNAPSHOT_GIT_QUESTIONS)).replace("%", "' + chr(37) + '"),
).replace("@@HARDENING@@", repr(list(_LIST_SNAPSHOT_GIT_HARDENING)))

_S_TYPED_READ_PATH_SLOT = "<<PATH>>"
S_TYPED_READ_FILE_BASE64 = "readFileBase64"
S_TYPED_READ_DIRECTORY = "listDirectory"
S_TYPED_READ_FILESYSTEM_USAGE = "filesystemUsage"
S_TYPED_READ_CLOCK_UTC = "clockUtc"
S_TYPED_READ_FILE_EXISTS = "fileExists"
S_TYPED_READ_DIRECTORY_EXISTS = "directoryExists"
S_TYPED_READ_PATHS_EXIST = "pathsExist"
S_TYPED_READ_DIRECTORIES_EXIST = "directoriesExist"
S_TYPED_READ_PATH_MTIMES = "pathMtimes"
# Many small documents in ONE exec. The poll read each step's test
# marker with its own round trip -- 73 of them took 4.95 s on the hub's
# event loop, every poll (measured 2026-10-05) -- so this exists to
# make "read N small files" one container exec per argument batch.
S_TYPED_READ_SMALL_FILES_BASE64 = "smallFilesBase64"
S_TYPED_READ_FILE_SHA256 = "fileSha256"
S_TYPED_READ_REPO_HASHES = "repoRelativeHashes"
S_TYPED_READ_REPO_SNAPSHOT = "repoSnapshot"
S_TYPED_READ_GIT_REPO_STATUS = "gitRepoStatus"
S_TYPED_READ_GIT_WORKTREE_IDENTITIES = "gitWorktreeIdentities"
S_TYPED_READ_REPOSITORY_WEIGHT = "repositoryWeight"
# The council's git-tracked snapshot scope (2026-09-29). The index is
# read from TWO enumerations joined by path, because neither alone says
# enough: ``ls-files -s`` carries mode and merge stage but hides the
# skip-worktree bit, and ``ls-files -t`` shows that bit as the tag
# ``S``. Presence is then decided by ``lstat`` on the worktree, never
# inferred from a bit, and content identity is the git blob sha of the
# CURRENT worktree bytes -- the same raw-byte domain the whole-directory
# observation uses, so the two scopes compare on the same terms.
S_TYPED_READ_GIT_TRACKED_IDENTITIES = "gitTrackedIdentities"
# What a git-tracked snapshot leaves behind: untracked and ignored
# files, with their sizes, enumerated ONCE and bounded. A wholly ignored
# directory is listed by git as one entry and walked here, so the walk
# itself stops at the budget instead of git first materializing a
# third of a million names.
S_TYPED_READ_GIT_UNTRACKED_INVENTORY = "gitUntrackedInventory"
I_MAX_OMISSION_INVENTORY_PATHS = 1000000
I_MAX_OMISSION_INVENTORY_NAME_BYTES = 64 * 1024 * 1024
# The probe stops counting past this many files. Comfortably above the
# council's own 20,000-member bound, so a repository that the snapshot
# would accept is always counted exactly; only one that is already
# refused gets a truncated answer, and "too many to count" is the same
# verdict as "too many".
I_MAX_REPOSITORY_WEIGHT_PROBE_FILES = 50000
# The probe names its largest files so the dashboard can offer the
# oversized ones for exclusion by NAME rather than saying only "too
# big". Bounded because the list is an interactive checklist: past this
# many, ticking them individually is not the action a researcher wants
# anyway, and the answer becomes "exclude them all" or "this repository
# is the wrong shape for a council".
I_REPOSITORY_WEIGHT_LARGEST_FILES = 64

# Path components the probe does not count, because the council
# snapshot does not capture them. A DELIBERATE MIRROR of
# agentCouncilContext.DICT_EXCLUDED_COMPONENT_REASONS, which owns the
# policy: a container program cannot import from the host environment
# (the same boundary that makes introspectionScript duplicate
# dataLoaders), so the names are spelled twice and pinned together by
# testTheProbePrunesExactlyWhatTheSnapshotExcludes.
#
# Not cosmetic. Measured on a real research repository (2026-08-22):
# without pruning, the probe reported 463 MB and named a 315 MB git
# pack as the largest file — refusing a council over an object store
# the snapshot never carries, and offering the pack for "exclusion".
# The same repository weighs 148 MB of actual content.
_TUPLE_REPOSITORY_WEIGHT_PRUNED_COMPONENTS = (
    ".aws", ".claude", ".cline", ".clinerules", ".codex", ".docker",
    ".env", ".gemini", ".git", ".git-credentials", ".ipynb_checkpoints",
    ".mcp.json", ".netrc", ".npmrc", ".opencode", ".openhands", ".pi",
    ".pypirc", ".pytest_cache", ".ssh", ".vaibify", "AGENTS.md",
    "AGENTS.override.md", "CLAUDE.local.md", "CLAUDE.md", "GEMINI.md",
    "__pycache__",
)
# Mirrors of DICT_EXCLUDED_COMPONENT_PREFIX_REASONS and
# DICT_EXCLUDED_COMPONENT_SEQUENCE_REASONS, pinned the same way.
_TUPLE_REPOSITORY_WEIGHT_PRUNED_PREFIXES = (".env.",)
_TUPLE_REPOSITORY_WEIGHT_PRUNED_SEQUENCES = ((".config", "gh"),)
# The two network-state reads the container-scope diagnostic runs.
# They mutate nothing, and they carry no credential: a name lookup
# sends a query to whatever resolver the container is configured with,
# and the handshake completes a TLS negotiation and sends no request
# bytes at all. The hostname reaches the program as a Python string
# literal through the same ``repr`` slot every other typed read uses,
# so a project-controlled remote URL cannot become a command.
S_TYPED_READ_RESOLVE_HOSTNAME = "resolveHostname"
# Files inside the workspace that the container user does not own. The
# probe reports the two cases SEPARATELY because only one of them is
# repaired by a restart: the entrypoint's migration triggers on a
# root-owned path and skips entirely when /proc/self/mountinfo is
# unreadable, so a file owned by some third uid survives exactly the
# restart a naive finding would recommend.
S_TYPED_READ_FOREIGN_OWNED_PATHS = "foreignOwnedPaths"
S_TYPED_READ_TCP_HANDSHAKE = "probeTcpHandshake"

S_TYPED_READ_CREDENTIAL_FILE = "credentialFileBase64"

# The environment-archive deposit reads the researcher's Zenodo token
# out of the CONTAINER keyring, because that is the only place vaibify
# stores it, while `docker save` can only run on the host. The value
# is held in the hub process for the length of one upload and is
# written to no file and no log.
S_TYPED_READ_KEYRING_SECRET = "keyringSecretValue"

# A provider login document is kilobytes. The council's credential read
# bounds itself IN the container at this ceiling rather than inheriting
# the 64 MB general-file cap, which can only reject after the bytes
# have already crossed the socket and been decoded.
I_MAX_CREDENTIAL_FILE_BYTES = 256 * 1024
# The per-file ceiling of the batched small-file read, enforced IN the
# container for the same reason as the credential ceiling: the read
# stops one byte past it, so an oversized file costs the ceiling, never
# its own size, and the extra byte tells "at the limit" from "over".
I_MAX_SMALL_FILE_BYTES = 4 * 1024 * 1024

_DICT_TYPED_READ_PROGRAMS = {
    S_TYPED_READ_FILE_BASE64: (
        "import base64,sys; "
        "sys.stdout.buffer.write(base64.b64encode(open("
        + _S_TYPED_READ_PATH_SLOT + ",'rb').read()))"
    ),
    # A credential document read with the bound enforced IN THE
    # CONTAINER. The general file read above materializes the whole
    # file and base64s it before the host can check any cap, so a
    # host-side cap rejects a hostile multi-gigabyte file only AFTER
    # paying for it twice — which is what the council's credential
    # read used to do. This program reads one byte past the ceiling
    # and stops: an oversized file costs the ceiling, never its size,
    # and the extra byte is what lets the host tell "at the limit"
    # from "over it". The ceiling is server-owned text, never a
    # caller's value, so the typed-read seam still takes only an
    # operation name and a path.
    S_TYPED_READ_RESOLVE_HOSTNAME: (
        "import json,socket,sys\n"
        "listArgs = " + _S_TYPED_READ_PATH_SLOT + "\n"
        "sHostname = listArgs[0]\n"
        "socket.setdefaulttimeout(float(listArgs[1]))\n"
        "dictAnswer = {'sHostname': sHostname, 'listAddresses': [],\n"
        "              'listFamilies': [], 'sError': ''}\n"
        "try:\n"
        "    listInfo = socket.getaddrinfo(sHostname, None)\n"
        "    dictAnswer['listAddresses'] = sorted(\n"
        "        {tEntry[4][0] for tEntry in listInfo})\n"
        "    dictAnswer['listFamilies'] = sorted(\n"
        "        {tEntry[0].name for tEntry in listInfo})\n"
        "except Exception as errorLookup:\n"
        "    dictAnswer['sError'] = (type(errorLookup).__name__ + ': '\n"
        "                            + str(errorLookup))\n"
        "sys.stdout.write(json.dumps(dictAnswer))\n"
    ),
    S_TYPED_READ_FOREIGN_OWNED_PATHS: (
        "import json,os,sys\n"
        "listArgs = " + _S_TYPED_READ_PATH_SLOT + "\n"
        "sRoot = listArgs[0]\n"
        "iExpectedUid = int(listArgs[1])\n"
        "iMaxNamed = int(listArgs[2])\n"
        "iMaxVisits = int(listArgs[3])\n"
        "dictAnswer = {'listRootOwned': [], 'listOtherOwned': [],\n"
        "              'bTruncated': False,\n"
        "              'bMountInfoReadable': False}\n"
        "try:\n"
        "    with open('/proc/self/mountinfo') as fileMounts:\n"
        "        fileMounts.read()\n"
        "    dictAnswer['bMountInfoReadable'] = True\n"
        "except Exception:\n"
        "    pass\n"
        "iVisited = 0\n"
        "for sDirectory, listDirNames, listFileNames in os.walk(sRoot):\n"
        "    for sName in list(listDirNames) + list(listFileNames):\n"
        "        iVisited += 1\n"
        "        if iVisited > iMaxVisits:\n"
        "            dictAnswer['bTruncated'] = True\n"
        "            break\n"
        "        sPath = os.path.join(sDirectory, sName)\n"
        "        try:\n"
        "            iOwnerUid = os.lstat(sPath).st_uid\n"
        "        except OSError:\n"
        "            continue\n"
        "        if iOwnerUid == iExpectedUid:\n"
        "            continue\n"
        "        sKey = ('listRootOwned' if iOwnerUid == 0\n"
        "                else 'listOtherOwned')\n"
        "        if len(dictAnswer[sKey]) < iMaxNamed:\n"
        "            dictAnswer[sKey].append(sPath)\n"
        "    if dictAnswer['bTruncated']:\n"
        "        break\n"
        "sys.stdout.write(json.dumps(dictAnswer))\n"
    ),
    S_TYPED_READ_TCP_HANDSHAKE: (
        "import json,socket,ssl,sys\n"
        "listArgs = " + _S_TYPED_READ_PATH_SLOT + "\n"
        "sHostname = listArgs[0]\n"
        "iPort = int(listArgs[1])\n"
        "fTimeout = float(listArgs[2])\n"
        "bUseTls = listArgs[3] == 'tls'\n"
        "sProxyHost = listArgs[4]\n"
        "iProxyPort = int(listArgs[5] or 0)\n"
        "dictAnswer = {'sHostname': sHostname, 'iPort': iPort,\n"
        "              'bConnected': False, 'bTlsVerified': False,\n"
        "              'bThroughProxy': bool(sProxyHost),\n"
        "              'sAddress': '', 'sError': '', 'sTlsError': '',\n"
        "              'sProxyError': ''}\n"
        "def fnOpenDirect():\n"
        "    return socket.create_connection((sHostname, iPort), fTimeout)\n"
        "def fnOpenThroughProxy():\n"
        "    connectionProxy = socket.create_connection(\n"
        "        (sProxyHost, iProxyPort), fTimeout)\n"
        "    sRequest = ('CONNECT ' + sHostname + ':' + str(iPort)\n"
        "                + ' HTTP/1.1\\r\\nHost: ' + sHostname + ':'\n"
        "                + str(iPort) + '\\r\\n\\r\\n')\n"
        "    connectionProxy.sendall(sRequest.encode('ascii'))\n"
        "    baStatus = connectionProxy.recv(256)\n"
        "    sStatus = baStatus.decode('latin-1').split('\\r\\n')[0]\n"
        "    if ' 200' not in sStatus:\n"
        "        connectionProxy.close()\n"
        "        dictAnswer['sProxyError'] = sStatus.strip()\n"
        "        return None\n"
        "    return connectionProxy\n"
        "try:\n"
        "    connectionSocket = (fnOpenThroughProxy() if sProxyHost\n"
        "                        else fnOpenDirect())\n"
        "    if connectionSocket is not None:\n"
        "        dictAnswer['bConnected'] = True\n"
        "        dictAnswer['sAddress'] = connectionSocket.getpeername()[0]\n"
        "        if not bUseTls:\n"
        "            connectionSocket.close()\n"
        "        else:\n"
        "            try:\n"
        "                contextTls = ssl.create_default_context()\n"
        "                with contextTls.wrap_socket(\n"
        "                        connectionSocket,\n"
        "                        server_hostname=sHostname) as connectionTls:\n"
        "                    dictAnswer['bTlsVerified'] = bool(\n"
        "                        connectionTls.getpeercert())\n"
        "            except Exception as errorTls:\n"
        "                dictAnswer['sTlsError'] = (type(errorTls).__name__\n"
        "                                           + ': ' + str(errorTls))\n"
        "                connectionSocket.close()\n"
        "except Exception as errorConnect:\n"
        "    dictAnswer['sError'] = (type(errorConnect).__name__ + ': '\n"
        "                            + str(errorConnect))\n"
        "sys.stdout.write(json.dumps(dictAnswer))\n"
    ),
    S_TYPED_READ_CREDENTIAL_FILE: (
        "import base64,sys\n"
        "with open(" + _S_TYPED_READ_PATH_SLOT + ",'rb') as fileIn:\n"
        "    baHead=fileIn.read(" + str(I_MAX_CREDENTIAL_FILE_BYTES + 1)
        + ")\n"
        "sys.stdout.buffer.write(base64.b64encode(baHead))\n"
    ),
    # The slot NAME occupies the literal slot, exactly as a path does
    # everywhere else here: the program is server-owned text and the
    # caller chooses only which stored value to ask for. Callers
    # validate the slot against the closed set of vaibify credential
    # slots before asking, the same discipline every path caller
    # applies to its path.
    S_TYPED_READ_KEYRING_SECRET: (
        "import keyring,sys\n"
        "sys.stdout.write(keyring.get_password('vaibify', "
        + _S_TYPED_READ_PATH_SLOT + ") or '')\n"
    ),
    S_TYPED_READ_DIRECTORY: (
        "import os,sys; "
        "sys.stdout.write(chr(10).join(sorted(os.listdir("
        + _S_TYPED_READ_PATH_SLOT + "))))"
    ),
    # The container's own wall clock, in the format a sign-off is
    # stamped in. The read takes no argument: the slot is bound to a
    # throwaway name so every program in this table carries it as one
    # literal (the table's invariant), and nothing reads it.
    S_TYPED_READ_CLOCK_UTC: (
        "_=" + _S_TYPED_READ_PATH_SLOT + "; import sys,time; "
        "sys.stdout.write(time.strftime("
        "'%Y-%m-%d %H:%M:%S UTC', time.gmtime()))"
    ),
    # The three figures `df` reports, computed the way `df` computes
    # them. Free is f_bavail -- the space available to an unprivileged
    # user -- because that is what df's Available column has always
    # meant, and the dashboard's low-disk warning is calibrated to it.
    S_TYPED_READ_FILESYSTEM_USAGE: (
        "import json,os,sys; "
        "st=os.statvfs(" + _S_TYPED_READ_PATH_SLOT + "); "
        "sys.stdout.write(json.dumps({"
        "'iTotalBytes': st.f_blocks*st.f_frsize, "
        "'iUsedBytes': (st.f_blocks-st.f_bfree)*st.f_frsize, "
        "'iFreeBytes': st.f_bavail*st.f_frsize}))"
    ),
    # The council snapshot pre-flight: how many files a repository holds
    # and how many bytes, so the dashboard can say "this will not fit"
    # BEFORE the researcher composes a question. Walking metadata is
    # cheap where streaming 30 GB through get_archive to discover the
    # same refusal is not, and the alternative — finding out at convene
    # — throws away the researcher's actual thinking (live report,
    # 2026-08-22). It stops counting once BOTH declared bounds are
    # exceeded, so an enormous tree cannot make the probe itself the
    # slow thing it exists to prevent. It prunes exactly what the
    # snapshot excludes, so it weighs what would actually be captured
    # rather than the directory that happens to contain it.
    S_TYPED_READ_REPOSITORY_WEIGHT: (
        "import heapq,json,os,stat,sys\n"
        "root=" + _S_TYPED_READ_PATH_SLOT + "\n"
        "cap=" + str(I_MAX_REPOSITORY_WEIGHT_PROBE_FILES) + "\n"
        "top=" + str(I_REPOSITORY_WEIGHT_LARGEST_FILES) + "\n"
        "skip=" + repr(set(_TUPLE_REPOSITORY_WEIGHT_PRUNED_COMPONENTS))
        + "\n"
        "prefixes=" + repr(_TUPLE_REPOSITORY_WEIGHT_PRUNED_PREFIXES)
        + "\n"
        "sequences=" + repr(_TUPLE_REPOSITORY_WEIGHT_PRUNED_SEQUENCES)
        + "\n"
        "def pruned(dirpath,name):\n"
        "    if name in skip or name.startswith(prefixes): return True\n"
        "    rel=tuple(os.path.relpath(\n"
        "        os.path.join(dirpath,name),root).split(os.sep))\n"
        "    return any(rel[-len(q):]==q for q in sequences)\n"
        "n=0; b=0; truncated=False; heap=[]\n"
        "escaping=[]; special=[]; submodules=[]\n"
        "for dirpath,dirnames,filenames in os.walk(root):\n"
        "    dirnames[:]=[d for d in dirnames if not pruned(dirpath,d)]\n"
        # A checked-out submodule's files are enumerated by no
        # superproject git command, so every one of them is an
        # unobserved member and the capture refuses. Its marker is a
        # .git that is a FILE rather than a directory — and .git is
        # pruned above, so this looks for it before the prune applies
        # to the next level down.
        "    if dirpath!=root and os.path.isfile(\n"
        "            os.path.join(dirpath,'.git')):\n"
        "        submodules.append(os.path.relpath(dirpath,root))\n"
        "        dirnames[:]=[]\n"
        "        continue\n"
        "    for name in filenames:\n"
        "        if pruned(dirpath,name): continue\n"
        "        p=os.path.join(dirpath,name)\n"
        # A symlink contributes NO content bytes, because the snapshot
        # stores it as a link rather than following it. os.lstat would
        # report the length of the target NAME, which is neither the
        # link's cost nor the target's — a live comparison against the
        # capture caught the probe over-reporting by exactly
        # len('dataFile.txt').
        "        try:\n"
        "            st=os.lstat(p)\n"
        "            size=0 if stat.S_ISLNK(st.st_mode) else st.st_size\n"
        "        except OSError: st=None; size=0\n"
        # The capture's NON-SIZE refusals, spotted on the same walk.
        # They are all properties of the tree as it sits, so a
        # researcher can be told about them while choosing a directory
        # instead of after writing a question — the same complaint the
        # size bounds answered. A symlink out of the repository and an
        # unrepresentable special file each refuse the whole capture.
        "        if st is not None and stat.S_ISLNK(st.st_mode):\n"
        "            try: target=os.readlink(p)\n"
        "            except OSError: target=''\n"
        "            resolved=os.path.normpath(os.path.join(\n"
        "                os.path.dirname(p),target))\n"
        "            if (not target or os.path.isabs(target)\n"
        "                    or os.path.relpath(\n"
        "                        resolved,root).startswith(os.pardir)):\n"
        "                escaping.append({'sPath':os.path.relpath(p,root),\n"
        "                    'sTarget':target})\n"
        "        elif st is not None and not stat.S_ISREG(st.st_mode):\n"
        "            special.append(os.path.relpath(p,root))\n"
        "        b+=size; n+=1\n"
        "        if len(heap)<top:\n"
        "            heapq.heappush(heap,(size,os.path.relpath(p,root)))\n"
        "        elif size>heap[0][0]:\n"
        "            heapq.heapreplace("
        "heap,(size,os.path.relpath(p,root)))\n"
        "    if n>cap:\n"
        "        truncated=True; break\n"
        "sys.stdout.write(json.dumps({"
        "'iFileCount': n, 'iTotalBytes': b, 'bTruncated': truncated, "
        "'bLargestFilesTruncated': len(heap)>=top, "
        "'listEscapingSymlinks': escaping[:top], "
        "'listSpecialFiles': special[:top], "
        "'listSubmodules': submodules[:top], "
        "'listLargestFiles': [{'sPath': q, 'iSizeBytes': s} "
        "for s,q in sorted(heap,reverse=True)]}))"
    ),
    # Existence probes, replacing `test -f` / `test -d` assembled by a
    # repo-files adapter and run through the general exec primitive.
    # They are reads by any reading, but the primitive cannot know that
    # from command text, so under an enforced lane every one of them was
    # refused -- and a level gate that catches OSError turned the
    # refusal into "not verified", quietly downgrading a workflow's
    # reproducibility level. `os.path.isfile`/`isdir` follow symlinks
    # exactly as `test -f`/`test -d` do, so the answer is unchanged.
    # The result travels as stdout rather than an exit code because a
    # non-zero exit is how this layer spells "the read itself failed",
    # and "the file is absent" must not be the same answer.
    S_TYPED_READ_FILE_EXISTS: (
        "import os,sys; "
        "sys.stdout.write('1' if os.path.isfile("
        + _S_TYPED_READ_PATH_SLOT + ") else '0')"
    ),
    S_TYPED_READ_DIRECTORY_EXISTS: (
        "import os,sys; "
        "sys.stdout.write('1' if os.path.isdir("
        + _S_TYPED_READ_PATH_SLOT + ") else '0')"
    ),
    # The BATCHED existence probe. It is a separate program rather than
    # a loop over the single-path one because the dashboard's file
    # panel probes up to a thousand paths on a debounced keystroke, and
    # a thousand container round-trips is not a UI. `os.path.exists`
    # matches `test -e` -- file OR directory, following symlinks --
    # which is what the route it replaces asked.
    #
    # What that route did before is the reason this exists. It built a
    # shell heredoc with every path interpolated raw between
    # `<<'__VAIBIFY_EOF__'` and its terminator, so a path that
    # contained that terminator on a line of its own ended the heredoc
    # and the remainder of the path became shell. Here the paths are a
    # Python list literal inside a program that is quoted whole as one
    # argument: there is no layer left for a path to escape into.
    S_TYPED_READ_PATHS_EXIST: (
        "import json,os,sys; "
        "sys.stdout.write(json.dumps("
        "[os.path.exists(s) for s in "
        + _S_TYPED_READ_PATH_SLOT + "]))"
    ),
    # The batched repo-relative HASH, replacing an embedded script the
    # repo-files adapter assembled and ran through the general exec
    # primitive -- which the mutation gate must treat as mutating, so
    # under an enforced lane every remote verify's hashing was refused
    # (surfaced as both Published-envelope rows "could not check",
    # 2026-09-02; the same class as the `test -f` and mtime
    # migrations above). The slot is a flat list of path strings whose
    # FIRST element is the repository root and the rest are
    # repo-relative paths; the semantics are the adapter script's,
    # preserved exactly: a symlink component is reported by segment
    # name, a path resolving outside the root's realpath answers
    # bEscapesRoot instead of a hash, and the content read opens with
    # O_NOFOLLOW so the file hashed is the file checked.
    S_TYPED_READ_REPO_HASHES: (
        "import hashlib,json,os,sys\n"
        "listArgs = " + _S_TYPED_READ_PATH_SLOT + "\n"
        "sRoot = listArgs[0]\n"
        "sRootReal = os.path.realpath(sRoot)\n"
        "dictOut = {}\n"
        "for sRel in listArgs[1:]:\n"
        "    dictEntry = {'sSha256': None, 'sSymlinkSegment': None,\n"
        "                 'bEscapesRoot': False}\n"
        "    dictOut[sRel] = dictEntry\n"
        "    if os.path.isabs(sRel):\n"
        "        dictEntry['bEscapesRoot'] = True\n"
        "        continue\n"
        "    sCur = sRoot\n"
        "    for sSeg in [s for s in sRel.split('/') if s]:\n"
        "        sCur = os.path.join(sCur, sSeg)\n"
        "        if os.path.islink(sCur):\n"
        "            dictEntry['sSymlinkSegment'] = sSeg\n"
        "            break\n"
        "    sReal = os.path.realpath(os.path.join(sRootReal, sRel))\n"
        "    if sReal != sRootReal and not sReal.startswith("
        "sRootReal + os.sep):\n"
        "        dictEntry['bEscapesRoot'] = True\n"
        "        continue\n"
        "    try:\n"
        "        iFd = os.open(sReal, os.O_RDONLY | "
        "getattr(os, 'O_NOFOLLOW', 0))\n"
        "    except OSError:\n"
        "        continue\n"
        "    hashFile = hashlib.sha256()\n"
        "    with os.fdopen(iFd, 'rb') as fileIn:\n"
        "        for baChunk in iter(lambda: fileIn.read(65536), b''):\n"
        "            hashFile.update(baChunk)\n"
        "    dictEntry['sSha256'] = hashFile.hexdigest()\n"
        "sys.stdout.write(json.dumps(dictOut))"
    ),
    # The same batch, asking whether each path is a DIRECTORY. It is
    # its own program because the question cannot be spelled as a path
    # and asked through the one above: `<name>/.` exists only for a
    # directory on both legs, but the host guard resolves every path
    # through `realpath`, which strips the `/.` and answers about the
    # file. Repository discovery asks both questions about the same
    # entries in one round trip -- "is it a repo" and "is it even a
    # directory" -- because a plain file answering no to the first was
    # being offered to the researcher as somewhere to run `git init`.
    S_TYPED_READ_DIRECTORIES_EXIST: (
        "import json,os,sys; "
        "sys.stdout.write(json.dumps("
        "[os.path.isdir(s) for s in "
        + _S_TYPED_READ_PATH_SLOT + "]))"
    ),
    # One answer per requested path: base64 of the first
    # I_MAX_SMALL_FILE_BYTES + 1 bytes, or null when the file cannot be
    # opened. The host decoder refuses an answer that omits a path.
    S_TYPED_READ_SMALL_FILES_BASE64: (
        "import base64,json,sys\n"
        "dictFiles={}\n"
        "for sPath in " + _S_TYPED_READ_PATH_SLOT + ":\n"
        "    try:\n"
        "        with open(sPath,'rb') as fileIn:\n"
        "            baHead=fileIn.read(" + str(I_MAX_SMALL_FILE_BYTES + 1)
        + ")\n"
        "    except OSError:\n"
        "        dictFiles[sPath]=None\n"
        "        continue\n"
        "    dictFiles[sPath]=base64.b64encode(baHead).decode('ascii')\n"
        "sys.stdout.write(json.dumps(dictFiles))\n"
    ),
    # The file panel's five-second poll, which is the hottest read in
    # the product. It replaced a WRITE plus an exec: the old shape
    # pushed a newline-delimited path list into /tmp and ran `xargs -d
    # -a <file> stat -c '%n %Y'` over it, because a shell argv will not
    # hold a thousand paths. A Python list literal will, so the write
    # is simply gone -- and with it the only container mutation on the
    # dashboard's timer, which is what let this route stay outside the
    # commit-guard boundary. `xargs -d`, `stat -c` and `sha256sum` are
    # also GNU-only spellings that fail on a BSD userland.
    #
    # A path that vanishes between the listing and the stat is SKIPPED,
    # not an error, exactly as `2>/dev/null` made it: the poll's answer
    # is "these are the files that exist and when they changed", and a
    # file deleted mid-poll is absent by the next tick anyway.
    #
    # Seconds are truncated to an integer string because that is what
    # `stat -c %Y` returned. `st_mtime` carries sub-second precision and
    # keeping it would be an improvement -- and would also make every
    # cached mtime in every workflow compare unequal exactly once, on
    # the tick after an upgrade, reporting every file as modified. That
    # is a change worth making deliberately, not as a side effect of
    # this one.
    S_TYPED_READ_PATH_MTIMES: (
        "import json,os,sys\n"
        "dictMtimes={}\n"
        "for sPath in " + _S_TYPED_READ_PATH_SLOT + ":\n"
        "    try:\n"
        "        dictMtimes[sPath]=str(int(os.stat(sPath).st_mtime))\n"
        "    except OSError:\n"
        "        continue\n"
        "sys.stdout.write(json.dumps(dictMtimes))\n"
    ),
    # The content fingerprint that rides with the poll, replacing
    # `sha256sum <path> | cut -d' ' -f1`. Streamed rather than read
    # whole: the workflow document this hashes is small today, and a
    # program that reads an arbitrary path into memory is one large
    # file away from being a problem nobody predicted.
    #
    # An unreadable file answers with the EMPTY STRING rather than
    # failing, which is the contract the reload detector already had
    # from `2>/dev/null`: no fingerprint means "cannot compare", and it
    # falls back to its other signals.
    S_TYPED_READ_FILE_SHA256: (
        "import hashlib,sys\n"
        "hashFile=hashlib.sha256()\n"
        "try:\n"
        "    with open(" + _S_TYPED_READ_PATH_SLOT + ",'rb') as fileIn:\n"
        "        for baChunk in iter(lambda: fileIn.read(65536), b''):\n"
        "            hashFile.update(baChunk)\n"
        "    sDigest=hashFile.hexdigest()\n"
        "except OSError:\n"
        "    sDigest=''\n"
        "sys.stdout.write(sDigest)\n"
    ),
    # The Repositories panel's five-second poll. The FIRST typed read
    # that runs an external program rather than reading the filesystem
    # directly, which is worth saying out loud: what the caller may
    # vary is still only the path literal, and the argv around it is
    # fixed text in this file, so the property that makes a typed read
    # safe is unchanged. Git is simply the only way to ask git.
    #
    # It replaces a shell script this module's own callers ASSEMBLED,
    # interpolating repository names raw into `echo "..."` and
    # `git -C /workspace/<name>`, and that shape had four defects at
    # once. A name is user-chosen text reaching a shell. `echo -n` is
    # not portable. Porcelain output was squeezed through `tr` with a
    # pipe as the record separator, so a filename containing `|`
    # silently corrupted the parse. And, because it was an exec, it
    # kept the whole route outside the commit-guard boundary: a route
    # on a five-second timer cannot hold the mutation drain without
    # making Run Step randomly refuse.
    #
    # `-c core.fsmonitor=false` is load-bearing rather than tidiness:
    # `core.fsmonitor` may name a HOOK COMMAND that `git status`
    # executes, so a repository could otherwise choose what this poll
    # runs. The timeout bounds a repository large enough to make
    # status slow; a repo that times out reports as it does when git
    # fails, which is the honest answer for "we could not read it".
    #
    # Every field falls back to the empty string on any failure, and
    # bMissing is decided by the presence of `.git` alone, so a
    # directory that is not a repository is reported as missing rather
    # than as an error.
    # The poll snapshot: one exec that answers existence, content,
    # mtime and sha for the fixed envelope set, the declared outputs
    # and the declared binaries. Arguments arrive as a FLAT list of
    # prefixed strings -- "r:<root>", "c:<content path>",
    # "k:<skip-text path>", "h:<hash path>", "a:<absolute binary>" --
    # because the slot admits exactly a path or a flat sequence of
    # paths, never structure and never a command. The body is the
    # SHARED core: repoFiles wraps the same constant for its legacy
    # embedded transport, so the two lanes cannot drift.
    S_TYPED_READ_REPO_SNAPSHOT: (
        "import hashlib, json, os, sys\n"
        "listArgs = " + _S_TYPED_READ_PATH_SLOT + "\n"
        "dictArgs = {\"sRoot\": \"\", \"listContentPaths\": [],\n"
        "            \"listSkipTextPaths\": [], \"listHashPaths\": [],\n"
        "            \"listAbsHashPaths\": [], \"dictCachedKeys\": {},\n"
        "            \"bHashManifestEntries\": False,\n"
        "            \"bReadReproductions\": False}\n"
        "dictKeyByPrefix = {\"c\": \"listContentPaths\",\n"
        "                   \"k\": \"listSkipTextPaths\",\n"
        "                   \"h\": \"listHashPaths\",\n"
        "                   \"a\": \"listAbsHashPaths\"}\n"
        "for sArg in listArgs:\n"
        "    if sArg[:2] == \"r:\":\n"
        "        dictArgs[\"sRoot\"] = sArg[2:]\n"
        "    elif sArg == \"f:manifestEntries\":\n"
        "        dictArgs[\"bHashManifestEntries\"] = True\n"
        "    elif sArg == \"f:reproductions\":\n"
        "        dictArgs[\"bReadReproductions\"] = True\n"
        "    elif sArg[:2] == \"x:\":\n"
        "        sKeyText, _sSep, sKeyPath = sArg[2:].partition(\"|\")\n"
        "        dictArgs[\"dictCachedKeys\"][sKeyPath] = [\n"
        "            int(s) for s in sKeyText.split(\",\")]\n"
        "    elif sArg[1:2] == \":\" and sArg[:1] in dictKeyByPrefix:\n"
        "        dictArgs[dictKeyByPrefix[sArg[:1]]].append(sArg[2:])\n"
        + S_REPO_SNAPSHOT_PROGRAM_CORE
    ),
    S_TYPED_READ_GIT_REPO_STATUS: (
        "import json,os,subprocess,sys\n"
        "T_FIELDS=(('sBranch',('rev-parse','--abbrev-ref','HEAD')),"
        "('sUrl',('config','--get','remote.origin.url')),"
        "('sPorcelain',('status','--porcelain')))\n"
        "listStatuses=[]\n"
        "for sPath in " + _S_TYPED_READ_PATH_SLOT + ":\n"
        "    if not os.path.isdir(os.path.join(sPath,'.git')):\n"
        "        listStatuses.append({'sPath':sPath,'bMissing':True})\n"
        "        continue\n"
        "    dictStatus={'sPath':sPath,'bMissing':False}\n"
        "    for sField,tArgs in T_FIELDS:\n"
        "        try:\n"
        "            processGit=subprocess.run(\n"
        "                ['git','-c','core.fsmonitor=false','-C',sPath]\n"
        "                +list(tArgs),\n"
        "                capture_output=True,text=True,timeout=30)\n"
        "            dictStatus[sField]=(processGit.stdout\n"
        "                if processGit.returncode==0 else '')\n"
        "        except Exception:\n"
        "            dictStatus[sField]=''\n"
        "    listStatuses.append(dictStatus)\n"
        "sys.stdout.write(json.dumps(listStatuses))\n"
    ),
    # The index view of a repository: every index entry's mode, merge
    # stages and skip-worktree flag, with the worktree type and raw-byte
    # blob identity of each path, plus HEAD and a porcelain digest.
    # Fail-CLOSED like the worktree read below.
    S_TYPED_READ_GIT_TRACKED_IDENTITIES: (
        "import hashlib,json,os,stat,subprocess,sys\n"
        "sRepo=" + _S_TYPED_READ_PATH_SLOT + "\n"
        "def fnFail(sReason):\n"
        "    sys.stdout.write(json.dumps({'bSuccess':False,"
        "'sReason':sReason,'dictEntries':{}}))\n"
        "    sys.exit(0)\n"
        "def fprocessRunGit(listArguments):\n"
        "    return subprocess.run(\n"
        "        ['git','-c','core.fsmonitor=false','-C',sRepo]\n"
        "        +listArguments,\n"
        "        capture_output=True,text=True,timeout=120)\n"
        "def fsBlobSha(sAbsolute):\n"
        "    hashBlob=hashlib.sha1()\n"
        "    hashBlob.update(('blob '+str(os.path.getsize(sAbsolute))\n"
        "        +chr(0)).encode())\n"
        "    with open(sAbsolute,'rb') as fileIn:\n"
        "        for baChunk in iter(lambda: fileIn.read(65536), b''):\n"
        "            hashBlob.update(baChunk)\n"
        "    return hashBlob.hexdigest()\n"
        # lstat does not follow the FINAL component but does follow
        # every parent, so a tracked parent directory replaced by a link
        # would have its target's files read as the tracked path. Such a
        # path is reported as lying beyond a link, never described.
        "sRepoReal=os.path.realpath(sRepo)\n"
        "def fbBeyondSymlink(sRelative):\n"
        "    listParts=sRelative.split('/')[:-1]\n"
        "    for iDepth in range(1,len(listParts)+1):\n"
        "        if os.path.islink(os.path.join(sRepo,*listParts[:iDepth])):\n"
        "            return True\n"
        "    sParent=os.path.realpath(os.path.dirname(\n"
        "        os.path.join(sRepo,sRelative)))\n"
        "    return not (sParent==sRepoReal\n"
        "        or sParent.startswith(sRepoReal+os.sep))\n"
        "def fdictDescribe(sRelative):\n"
        "    sAbsolute=os.path.join(sRepo,sRelative)\n"
        "    if fbBeyondSymlink(sRelative):\n"
        "        return {'sType':'beyondSymlink','sIdentity':'','iSizeBytes':0}\n"
        "    try: st=os.lstat(sAbsolute)\n"
        "    except FileNotFoundError:\n"
        "        return {'sType':'missing','sIdentity':'','iSizeBytes':0}\n"
        "    if stat.S_ISLNK(st.st_mode):\n"
        "        return {'sType':'symlink',\n"
        "            'sIdentity':os.readlink(sAbsolute),'iSizeBytes':0}\n"
        "    if stat.S_ISREG(st.st_mode):\n"
        "        return {'sType':'file','sIdentity':fsBlobSha(sAbsolute),\n"
        "            'iSizeBytes':st.st_size}\n"
        "    if stat.S_ISDIR(st.st_mode):\n"
        "        return {'sType':'directory','sIdentity':'','iSizeBytes':0}\n"
        "    return {'sType':'special','sIdentity':'','iSizeBytes':0}\n"
        "try:\n"
        "    if fprocessRunGit(\n"
        "            ['rev-parse','--is-inside-work-tree']).returncode!=0:\n"
        "        fnFail('not a git work tree')\n"
        "    processStage=fprocessRunGit(['ls-files','-s','-z'])\n"
        "    processTag=fprocessRunGit(['ls-files','-t','-z'])\n"
        "    if processStage.returncode!=0 or processTag.returncode!=0:\n"
        "        fnFail('index enumeration failed')\n"
        "    dictTags={}\n"
        "    for sLine in processTag.stdout.split(chr(0)):\n"
        "        if len(sLine)>2: dictTags[sLine[2:]]=sLine[0]\n"
        "    dictEntries={}\n"
        "    for sLine in processStage.stdout.split(chr(0)):\n"
        "        if not sLine: continue\n"
        "        sMeta,sRelative=sLine.split(chr(9),1)\n"
        "        sMode,sIndexSha,sStage=sMeta.split(' ')\n"
        "        dictEntry=dictEntries.setdefault(sRelative,\n"
        "            {'sMode':sMode,'listStages':[],\n"
        "             'bSkipWorktree':dictTags.get(sRelative,'H')=='S'})\n"
        "        dictEntry['listStages'].append(int(sStage))\n"
        "    for sRelative,dictEntry in dictEntries.items():\n"
        "        if dictEntry['sMode']!='160000':\n"
        "            dictEntry.update(fdictDescribe(sRelative))\n"
        "    processHead=fprocessRunGit(['rev-parse','--verify','HEAD'])\n"
        "    sHeadSha=(processHead.stdout.strip()\n"
        "        if processHead.returncode==0 else '')\n"
        "    processStatus=fprocessRunGit(\n"
        "        ['status','--porcelain=v2','--untracked-files=no'])\n"
        "    if processStatus.returncode!=0:\n"
        "        fnFail('status enumeration failed')\n"
        "except Exception as error:\n"
        "    fnFail(type(error).__name__+': '+str(error))\n"
        "listChanged=[sLine for sLine in processStatus.stdout.splitlines()\n"
        "    if sLine and not sLine.startswith('#')]\n"
        "sys.stdout.write(json.dumps({'bSuccess':True,'sReason':'',\n"
        "    'sHeadSha':sHeadSha,'iChangedCount':len(listChanged),\n"
        "    'sPorcelainDigest':hashlib.sha256(\n"
        "        processStatus.stdout.encode()).hexdigest(),\n"
        "    'dictEntries':dictEntries}))\n"
    ),
    S_TYPED_READ_GIT_UNTRACKED_INVENTORY: (
        "import json,os,stat,subprocess,sys\n"
        "sRepo=" + _S_TYPED_READ_PATH_SLOT + "\n"
        "iMaxPaths=" + str(I_MAX_OMISSION_INVENTORY_PATHS) + "\n"
        "iMaxNameBytes=" + str(I_MAX_OMISSION_INVENTORY_NAME_BYTES) + "\n"
        "listEntries=[]; iNameBytes=0; bComplete=True\n"
        "def fnFail(sReason):\n"
        "    sys.stdout.write(json.dumps({'bSuccess':False,"
        "'sReason':sReason,'listEntries':[]}))\n"
        "    sys.exit(0)\n"
        "def fbAppend(sRelative,sReason):\n"
        "    global iNameBytes,bComplete\n"
        "    if len(listEntries)>=iMaxPaths or iNameBytes>=iMaxNameBytes:\n"
        "        bComplete=False\n"
        "        return False\n"
        "    try: st=os.lstat(os.path.join(sRepo,sRelative))\n"
        "    except OSError: return True\n"
        "    iSize=st.st_size if stat.S_ISREG(st.st_mode) else 0\n"
        "    listEntries.append([sRelative,sReason,iSize])\n"
        "    iNameBytes+=len(sRelative)\n"
        "    return True\n"
        "def fbWalk(sRelativeDirectory,sReason):\n"
        "    for sDirectory,listDirs,listFiles in os.walk(\n"
        "            os.path.join(sRepo,sRelativeDirectory)):\n"
        "        listDirs.sort()\n"
        "        for sName in sorted(listFiles):\n"
        "            if not fbAppend(os.path.relpath(\n"
        "                    os.path.join(sDirectory,sName),sRepo),sReason):\n"
        "                return False\n"
        "    return True\n"
        "def flistRunGit(listArguments):\n"
        "    processGit=subprocess.run(\n"
        "        ['git','-c','core.fsmonitor=false','-C',sRepo]\n"
        "        +listArguments,capture_output=True,text=True,timeout=300)\n"
        "    if processGit.returncode!=0:\n"
        "        fnFail('enumeration failed: '+' '.join(listArguments))\n"
        "    return sorted(s for s in processGit.stdout.split(chr(0)) if s)\n"
        "try:\n"
        "    for sReason,listArguments in (\n"
        "            ('untracked',['ls-files','--others','--exclude-standard',\n"
        "                          '-z']),\n"
        "            ('ignored',['ls-files','--others','--ignored',\n"
        "                        '--exclude-standard','--directory','-z'])):\n"
        "        for sRelative in flistRunGit(listArguments):\n"
        "            bGoOn=(fbWalk(sRelative.rstrip('/'),sReason)\n"
        "                if sRelative.endswith('/')\n"
        "                else fbAppend(sRelative,sReason))\n"
        "            if not bGoOn: break\n"
        "        if not bComplete: break\n"
        "except Exception as error:\n"
        "    fnFail(type(error).__name__+': '+str(error))\n"
        "sys.stdout.write(json.dumps({'bSuccess':True,'sReason':'',\n"
        "    'bComplete':bComplete,'listEntries':listEntries}))\n"
    ),
    # The coherence observation behind every bulk repository export:
    # the type and content identity of EVERY present worktree path —
    # tracked, untracked and ignored alike — read immediately
    # before and immediately after the archive streams, so a capture
    # the repository moved under is refused rather than sealed. Full
    # width, not changed-paths-only, is the point: a CLEAN tracked
    # file changed mid-stream and reverted leaves HEAD, the porcelain
    # digest, and the changed-path set all equal, and only a raw-byte
    # identity taken outside the stream can contradict the archive's
    # intermediate bytes. Git enumerates (it is the only honest way to
    # ask git what exists); the blob identity is then computed HERE
    # over the raw worktree bytes — sha1 over ``blob <size>\\0`` +
    # content, byte-identical to ``git hash-object --no-filters`` — so
    # no clean-filter rewriting can make two different byte states
    # report one identity, and the comparison never crosses into the
    # filtered object-store domain (which would break repositories
    # using content filters). A symlink records its readlink target
    # instead, because hashing reads THROUGH a link. Fail-CLOSED: any
    # enumeration fault answers ``bSuccess`` False, never an empty
    # observation masquerading as a quiet repository. A tracked path
    # deleted from the worktree (or one that vanishes between
    # enumeration and stat) reports as ``missing``; the caller decides
    # what a missing path means for its lane.
    S_TYPED_READ_GIT_WORKTREE_IDENTITIES: (
        "import hashlib,json,os,subprocess,sys\n"
        "sRepo=" + _S_TYPED_READ_PATH_SLOT + "\n"
        "def fnFail(sReason):\n"
        "    sys.stdout.write(json.dumps({'bSuccess':False,"
        "'sReason':sReason,'dictPathIdentities':{}}))\n"
        "    sys.exit(0)\n"
        "def fprocessRunGit(listArguments):\n"
        "    return subprocess.run(\n"
        "        ['git','-c','core.fsmonitor=false','-C',sRepo]\n"
        "        +listArguments,\n"
        "        capture_output=True,text=True,timeout=60)\n"
        "dictIdentities={}\n"
        "try:\n"
        "    if fprocessRunGit(\n"
        "            ['rev-parse','--is-inside-work-tree']).returncode!=0:\n"
        "        fnFail('not a git work tree')\n"
        "    listEnumerations=[\n"
        "        ['ls-files','-z'],\n"
        "        ['ls-files','--others','--exclude-standard','-z']]\n"
        "    setPresent=set()\n"
        "    for listArguments in listEnumerations:\n"
        "        processGit=fprocessRunGit(listArguments)\n"
        "        if processGit.returncode!=0:\n"
        "            fnFail('enumeration failed: '+' '.join(listArguments))\n"
        "        setPresent.update(\n"
        "            sPath for sPath in processGit.stdout.split(chr(0))\n"
        "            if sPath)\n"
        # The IGNORED set. Enumerated on its own AND merged into
        # setPresent, which are two different jobs. Merging is what
        # makes an ignored file a first-class observed path: the
        # snapshot carries it (ruling 2026-08-24 — a derived artifact
        # that costs an hour to regenerate is worth carrying, and a
        # researcher expects the whole repository in the shadow
        # container), so it must be coherence-pinned like any other
        # file, or shipping it would be the one unpinned thing in an
        # otherwise fully pinned snapshot. Keeping the separate list is
        # what lets the manifest say WHICH included paths git does not
        # track, which is information a participant reasoning about
        # reproducibility needs and cannot recover from the tree.
        "    processIgnored=fprocessRunGit(\n"
        "        ['ls-files','--others','--ignored','--exclude-standard',\n"
        "         '-z'])\n"
        "    if processIgnored.returncode!=0:\n"
        "        fnFail('ignored enumeration failed')\n"
        "    listIgnored=sorted(\n"
        "        sPath for sPath in processIgnored.stdout.split(chr(0))\n"
        "        if sPath)\n"
        "    setPresent.update(listIgnored)\n"
        "    for sRelative in sorted(setPresent):\n"
        "        sAbsolute=os.path.join(sRepo,sRelative)\n"
        "        if os.path.islink(sAbsolute):\n"
        "            dictIdentities[sRelative]={'sType':'symlink',\n"
        "                'sIdentity':os.readlink(sAbsolute)}\n"
        "        elif os.path.isfile(sAbsolute):\n"
        "            hashBlob=hashlib.sha1()\n"
        "            hashBlob.update(('blob '\n"
        "                +str(os.path.getsize(sAbsolute))\n"
        "                +chr(0)).encode())\n"
        "            with open(sAbsolute,'rb') as fileIn:\n"
        "                for baChunk in iter(\n"
        "                        lambda: fileIn.read(65536), b''):\n"
        "                    hashBlob.update(baChunk)\n"
        "            dictIdentities[sRelative]={'sType':'file',\n"
        "                'sIdentity':hashBlob.hexdigest()}\n"
        "        else:\n"
        "            dictIdentities[sRelative]={'sType':'missing',\n"
        "                'sIdentity':''}\n"
        "except Exception as error:\n"
        "    fnFail(type(error).__name__+': '+str(error))\n"
        "try:\n"
        "    processHead=fprocessRunGit(['rev-parse','--verify','HEAD'])\n"
        "    sHeadSha=(processHead.stdout.strip()\n"
        "        if processHead.returncode==0 else '')\n"
        "    processStatus=fprocessRunGit(\n"
        "        ['status','--porcelain=v2','--untracked-files=normal'])\n"
        "    if processStatus.returncode!=0:\n"
        "        fnFail('status enumeration failed')\n"
        "    sPorcelainDigest=hashlib.sha256(\n"
        "        processStatus.stdout.encode()).hexdigest()\n"
        "except Exception as error:\n"
        "    fnFail(type(error).__name__+': '+str(error))\n"
        "sys.stdout.write(json.dumps({'bSuccess':True,'sReason':'',\n"
        "    'sHeadSha':sHeadSha,'sPorcelainDigest':sPorcelainDigest,\n"
        "    'listIgnoredPaths':listIgnored,\n"
        "    'dictPathIdentities':dictIdentities}))\n"
    ),
}


def fsRenderBatchedTypedReadProgram(sOperation, listPaths):
    """Return the program text for a BATCHED typed-read operation.

    The host leg needs the SAME program text the Docker leg runs, and
    must not grow a second copy of the table: two tables that had to
    agree would be a divergence bug waiting for the day somebody edits
    one. So the table is here and both legs read it.

    List-only by signature, and that is why this is a separate function
    rather than the assembly inside :meth:`_ftRunTypedRead`: that one
    accepts a single path OR a sequence, and sharing it would have
    meant one parameter holding either a string or a list — a shape the
    naming doctrine has no cast for and would have to grandfather.
    Every batched program embeds a list literal, so the host leg's
    caller has no such ambiguity to express.

    The operation NAME is looked up; an unknown one raises rather than
    running anything, and :func:`_fsTypedReadPathLiteral` still admits
    nothing but strings into the slot.
    """
    sTemplate = _DICT_TYPED_READ_PROGRAMS.get(sOperation)
    if sTemplate is None:
        raise ValueError(
            f"{sOperation!r} is not a declared typed-read operation; "
            f"the declared set is {sorted(_DICT_TYPED_READ_PROGRAMS)}"
        )
    return sTemplate.replace(
        _S_TYPED_READ_PATH_SLOT,
        _fsTypedReadPathLiteral(list(listPaths)),
    )



def _fdictDecodeProbeAnswer(tExecResult):
    """Decode one JSON-emitting typed read, or report it unanswered.

    Deliberately NOT a wrapper that also runs the read: every call to
    :meth:`DockerConnection._ftRunTypedRead` names its operation
    CONSTANT at the call site, so the boundary check can read the
    whole set from the source. A helper that took the operation as a
    parameter would hide one behind a variable, which is the shape
    ``testEveryTypedReadNamesADeclaredOperation`` exists to refuse.

    ``bAnswered`` False means the probe could not run at all -- which
    a caller must render as unassessed, never as the answer the probe
    would have given.
    """
    if tExecResult.iExitCode != 0:
        return {
            "bAnswered": False,
            "sError": (tExecResult.sStderr or "").strip()[:400],
        }
    try:
        dictAnswer = json.loads(tExecResult.sStdout or "")
    except (ValueError, TypeError):
        return {"bAnswered": False, "sError": "unreadable probe output"}
    dictAnswer["bAnswered"] = True
    return dictAnswer


def _fdictParseJsonTypedRead(tExecResult, sWhat):
    """Return a JSON typed read's answer; raise OSError on any failure."""
    if tExecResult.iExitCode != 0:
        raise OSError(
            f"Cannot read the {sWhat} in container "
            f"({tExecResult.sStderr.strip()})")
    try:
        return json.loads(tExecResult.sStdout.strip() or "{}")
    except ValueError as errorParse:
        raise OSError(
            f"The {sWhat} read answered unparseable output: {errorParse}")


def _fiRenderedSnapshotBytes(listArgs):
    """Return the byte size of the snapshot program once its arguments are in."""
    return len(
        _DICT_TYPED_READ_PROGRAMS[S_TYPED_READ_REPO_SNAPSHOT]
        .replace(_S_TYPED_READ_PATH_SLOT, _fsTypedReadPathLiteral(listArgs))
        .encode("utf-8")
    )


def _fsTypedReadPathLiteral(objPaths):
    """Return the Python literal a typed-read program embeds for its paths.

    One path string, or a list/tuple of path strings for a batched
    operation. **Anything else raises**, and the type check is the
    load-bearing part rather than defensive tidiness: the value is
    embedded in the program through ``repr``, and ``repr`` of an
    arbitrary object is whatever that object's class chooses to print.
    Only ``str`` has a ``repr`` that is a quoted string literal and
    nothing else, so only ``str`` may reach the slot.

    This is strictly tighter than the single-path form it generalizes,
    which called ``repr`` on whatever it was handed.
    """
    if isinstance(objPaths, str):
        return repr(objPaths)
    if isinstance(objPaths, (list, tuple)) and all(
        isinstance(sPath, str) for sPath in objPaths
    ):
        return repr(list(objPaths))
    raise TypeError(
        "A typed read takes a path string or a flat sequence of path "
        f"strings; it was given {type(objPaths).__name__}. Only str has "
        "a repr that is a string literal, so nothing else may be "
        "embedded in the program."
    )


def _flistInterpretBooleanBatch(tExecResult, listPaths, sProbeName):
    """Return one boolean per path from a batched probe's output.

    A failed READ raises ``OSError``. An answer count that does not
    match the request is also an ``OSError`` -- a short list would
    otherwise silently realign every answer after the gap onto the
    wrong path.
    """
    if tExecResult.iExitCode != 0:
        raise OSError(
            "Cannot probe paths in container "
            f"({tExecResult.sStderr.strip()})"
        )
    listAnswers = json.loads(tExecResult.sStdout.strip() or "[]")
    if len(listAnswers) != len(listPaths):
        raise OSError(
            f"The batched {sProbeName} probe answered "
            f"{len(listAnswers)} of {len(listPaths)} paths; refusing to "
            "realign the answers onto the wrong paths."
        )
    return [bool(bAnswer) for bAnswer in listAnswers]


def _fdictDecodeSmallFilesBatch(tExecResult, listPaths):
    """Decode one batch of the small-file read into ``{sPath: bytes}``.

    Every requested path must be answered: a missing key would read as
    "absent" without the container ever having said so.
    """
    if tExecResult.iExitCode != 0:
        raise OSError(
            "Cannot read files in container "
            f"({tExecResult.sStderr.strip()})"
        )
    try:
        dictEncoded = json.loads(tExecResult.sStdout.strip() or "{}")
    except ValueError as errorParse:
        raise OSError(
            f"The batched file read answered unparseable output: "
            f"{errorParse}"
        )
    if set(dictEncoded) != set(listPaths):
        raise OSError(
            f"The batched file read answered {len(dictEncoded)} of "
            f"{len(set(listPaths))} paths."
        )
    dictFiles = {}
    for sPath, sEncoded in dictEncoded.items():
        if sEncoded is None:
            dictFiles[sPath] = None
            continue
        baContent = base64.b64decode(sEncoded)
        if len(baContent) > I_MAX_SMALL_FILE_BYTES:
            raise ValueError(
                f"{sPath} exceeds the {I_MAX_SMALL_FILE_BYTES}-byte "
                "small-file ceiling."
            )
        dictFiles[sPath] = baContent
    return dictFiles


def _fbInterpretPathProbe(tExecResult, sPath):
    """Return the yes/no answer a declared path probe printed.

    Shared by the two existence adapters, and deliberately takes the
    RESULT rather than the operation name: threading the name through a
    helper would make the call to the exemption pass a variable, and
    ``testEveryTypedReadNamesADeclaredOperation`` fails on that -- a
    computed name would put the choice of program back in a caller's
    hands, which is the property that makes the exemption enumerable.
    """
    if tExecResult.iExitCode != 0:
        raise OSError(
            f"Cannot probe path in container: {sPath} "
            f"({tExecResult.sStderr.strip()})"
        )
    return tExecResult.sStdout.strip() == "1"


S_IMAGE_STATE_BUILT = "built"
S_IMAGE_STATE_MISSING = "missing"
S_IMAGE_STATE_UNANSWERED = "unanswered"


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

    def fsImageState(self, sImageName):
        """Return whether a local image exists: built, missing, unanswered.

        Three answers rather than a boolean because the start guard
        that asks may refuse only on a POSITIVE "no such image". A
        daemon that did not answer is not evidence that nothing was
        built, and reading it that way would refuse every start the
        moment Docker was briefly busy.
        """
        from docker.errors import ImageNotFound
        try:
            self._clientDocker.images.get(sImageName)
        except ImageNotFound:
            return S_IMAGE_STATE_MISSING
        except Exception:  # noqa: BLE001 -- an unanswered probe is not a refusal
            return S_IMAGE_STATE_UNANSWERED
        return S_IMAGE_STATE_BUILT

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
                    # The immutable content-addressed id, beside the
                    # display tag: a tag can be repointed without the
                    # containers changing, so anything that PINS an
                    # identity (the council credential gate, runner
                    # launches) must read this field, never sImage.
                    "sImageIdentity": str(container.image.id),
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

    def flistRunningExecIdentifiers(self, sContainerId):
        """Return the ids of exec sessions the daemon still reports running.

        Metadata only: this asks the daemon what it is running, it never
        runs anything, so it needs no command authority and no admission.

        It is EVIDENCE of work in the container, never proof of its
        absence. An exec whose shell has exited leaves nothing here even
        when a ``setsid`` descendant of it is still running — the same
        limit ``terminalContainment`` documents for its process-group
        prover. What the signal does carry is the property this exists
        for, observed 2026-08-29: an exec outlives the death of the
        client that started it, so a hub that crashed and restarted can
        still see the work its predecessor launched.

        The daemon prunes finished execs from ``ExecIDs``, so the list
        alone would nearly answer the question; each id is confirmed
        through ``exec_inspect`` anyway, because the pruning is
        observed behaviour of one daemon and ``Running`` is a stated
        one.
        """
        container = self.fcontainerGetById(sContainerId)
        container.reload()
        listExecIds = container.attrs.get("ExecIDs") or []
        listRunning = []
        for sExecId in listExecIds:
            if self._fbExecIsRunning(sExecId):
                listRunning.append(sExecId)
        return listRunning

    def _fbExecIsRunning(self, sExecId):
        """Return True when the daemon reports one exec still running.

        Routed through :meth:`fdictInspectExec` — the gateway method the
        operation journal's exec verifier already uses — rather than
        touching the SDK again: a second raw ``exec_inspect`` would be a
        second untraceable root for the mutation scan to answer for, and
        the reading of ``Running`` is the same reading in both places.

        A daemon failure is deliberately NOT caught here. Swallowing it
        would answer "not running" for an exec nobody could read, and
        the caller would withdraw a keep-alive under work that may be
        live; letting it propagate lets the single decision point
        (``sleepPrevention.fbContainerShowsRunningWorkEvidence``) break
        the tie in the direction that cannot lose a job.
        """
        return bool(self.fdictInspectExec(sExecId).get("Running"))

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
        tExecResult = self.ftRunInContainerStreamed(
            sContainerId, sCommand, sWorkdir=sWorkdir, sUser=sUser,
        )
        sOutput = tExecResult.sStdout + tExecResult.sStderr
        return (tExecResult.iExitCode, sOutput)

    def _ftRunTypedRead(self, sContainerId, sOperation, objPaths):
        """Run one NAMED read operation against a path or paths, as a read.

        The single place the audited-read exemption is granted, and it
        does not accept a command. It accepts the NAME of an operation
        from :data:`_DICT_TYPED_READ_PROGRAMS` and a path — or, for a
        batched operation, a flat sequence of paths — and builds the
        command itself from fixed module source text with the value
        embedded as a Python literal.

        Widening the third parameter to a COLLECTION changes nothing
        about that property, because the parameter never carried a
        command and still cannot: what varies is the literal in the
        slot, never the program around it, and
        :func:`_fsTypedReadPathLiteral` admits only strings into the
        literal. A batched program exists because the alternative was
        looping this method once per path, which on the file panel's
        debounced probe is up to a thousand container round-trips.

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
            sTemplate.replace(
                _S_TYPED_READ_PATH_SLOT,
                _fsTypedReadPathLiteral(objPaths),
            ),
        )
        tokenRead = mutationAdmission.ftokenEnterAuditedRead()
        try:
            return self.ftRunInContainerStreamed(sContainerId, sCommand)
        finally:
            mutationAdmission.fnExitAuditedRead(tokenRead)

    def fbaFetchCredentialFile(self, sContainerId, sFilePath):
        """Fetch a provider login, bounded IN the container.

        The council's credential read. Unlike :meth:`fbaFetchFile`,
        whose cap can only reject a payload the host has already
        received and decoded, this one stops reading at
        :data:`I_MAX_CREDENTIAL_FILE_BYTES` inside the container — so a
        hostile multi-gigabyte file planted at the credential path
        costs the ceiling rather than its own size. Over-ceiling
        raises ``ValueError`` (the program returned the one extra byte
        that distinguishes "at the limit" from "over" it); an
        unreadable path raises ``FileNotFoundError``, as the general
        read does.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_CREDENTIAL_FILE, sFilePath,
        )
        if tExecResult.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot read credential file from container: {sFilePath}"
            )
        baContent = base64.b64decode(tExecResult.sStdout.strip())
        if len(baContent) > I_MAX_CREDENTIAL_FILE_BYTES:
            raise ValueError(
                f"Credential file exceeds the {I_MAX_CREDENTIAL_FILE_BYTES} "
                f"byte ceiling: {sFilePath}"
            )
        return baContent

    def fsFetchKeyringSecret(self, sContainerId, sSlotName):
        """Read one stored credential out of the container's keyring.

        Vaibify keeps the Zenodo token in the CONTAINER keyring, and
        the environment-archive deposit runs on the host because
        ``docker save`` talks to the daemon. So the value has to
        cross once. Nothing about the value reaches a log, a file, or
        an exception message here: an unreadable slot raises with the
        SLOT name only, and an empty slot returns ``""`` rather than
        an error, because "no token stored" is an answer the caller
        turns into a researcher-facing instruction.

        The caller is responsible for holding the value no longer
        than the operation that needs it.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_KEYRING_SECRET, sSlotName,
        )
        if tExecResult.iExitCode != 0:
            raise LookupError(
                "Could not read the credential slot "
                f"{sSlotName!r} from this container's keyring."
            )
        return tExecResult.sStdout.strip()

    def fdictResolveHostnameInContainer(
        self, sContainerId, sHostname, fTimeoutSeconds=2.0,
    ):
        """Resolve one name using the CONTAINER's resolver, as a read.

        The container's answer is the only witness to the failure this
        exists for: a container whose ``/etc/resolv.conf`` is a
        snapshot of a network the laptop has since left resolves
        nothing, while the host beside it resolves everything. Asking
        the host twice cannot see that, and neither can reading the
        file -- a user-defined network's embedded resolver forwards to
        upstreams the file does not name.

        Never raises for a lookup failure: the failure IS the answer.
        ``sError`` carries it, and an exec that could not run at all
        answers ``bAnswered`` False, which a caller must render as
        unassessed rather than as a resolver fault.
        """
        return _fdictDecodeProbeAnswer(self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_RESOLVE_HOSTNAME,
            [str(sHostname), str(fTimeoutSeconds)],
        ))

    def fdictProbeTcpHandshakeInContainer(
        self, sContainerId, sHostname, iPort=443, fTimeoutSeconds=2.0,
        bUseTls=True, sProxyHost="", iProxyPort=0,
    ):
        """Open one connection along the EFFECTIVE path, sending nothing.

        Deliberately mute: it opens the connection, optionally
        negotiates TLS, and closes. No request line, no headers, no
        credential -- which matters because the hostname can come from
        a project-controlled remote URL, and a malicious one must gain
        nothing from being probed.

        Three parameters exist because a naive dial answers the wrong
        question. The PORT is the one the project's own remote uses --
        a git server reachable over ssh answers on 22 and nothing on
        443, so probing 443 reports a working network as broken. TLS
        is negotiated only where the scheme actually uses it. And a
        container configured to reach the network through a PROXY is
        probed through that proxy with a ``CONNECT``, because a direct
        dial from behind a corporate proxy fails for every container,
        working or not.

        No ``Proxy-Authorization`` is ever sent. A proxy that demands
        credentials answers 407, and that refusal is itself the
        diagnosis -- guessing at a credential would be a worse answer
        and a worse idea.
        """
        return _fdictDecodeProbeAnswer(self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_TCP_HANDSHAKE,
            [
                str(sHostname), str(int(iPort)), str(fTimeoutSeconds),
                "tls" if bUseTls else "plain",
                str(sProxyHost), str(int(iProxyPort)),
            ],
        ))

    def fdictFindForeignOwnedPaths(
        self, sContainerId, sRootPath, iExpectedUid=1000,
        iMaxNamed=20, iMaxVisits=20000,
    ):
        """Find workspace paths the container user does not own.

        Two lists, never one. The entrypoint's ownership migration
        triggers on a ROOT-owned path and skips entirely when
        ``/proc/self/mountinfo`` cannot be read, so root-owned files
        with readable mount information are the only case a restart
        repairs. Collapsing the two would produce a finding that
        recommends a restart which provably does nothing.

        Bounded in both directions -- the number of paths NAMED and
        the number visited -- because a workspace can hold a million
        files and this runs on a diagnostic's budget.
        """
        return _fdictDecodeProbeAnswer(self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_FOREIGN_OWNED_PATHS,
            [
                str(sRootPath), str(int(iExpectedUid)),
                str(int(iMaxNamed)), str(int(iMaxVisits)),
            ],
        ))

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
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_FILE_BASE64, sFilePath,
        )
        if tExecResult.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot read file from container: {sFilePath}"
            )
        baContent = base64.b64decode(tExecResult.sStdout.strip())
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
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_DIRECTORY, sDirectoryPath,
        )
        if tExecResult.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot list directory in container: {sDirectoryPath}"
            )
        return [
            sEntry for sEntry in tExecResult.sStdout.split("\n") if sEntry
        ]

    def fbContainerPathIsFile(self, sContainerId, sPath):
        """Return True iff the container path is an existing file.

        An AUDITED ADAPTER on the same terms as the other three: the
        caller supplies a PATH, this method supplies only the NAME of a
        declared read operation, and the program is fixed source text.

        It replaced ``"test -f " + fsShellQuotePosix(sPath)`` assembled
        by ``ContainerRepoFiles`` and run through the general exec
        primitive. That made an existence check indistinguishable from
        an arbitrary command, so under an enforced lane it was refused
        -- and its caller, a level gate catching ``OSError``, read the
        refusal as "unverified" and downgraded the workflow's badge.

        A failed READ raises ``OSError``; an ABSENT path returns False.
        Collapsing the two would put the old bug back one layer down.
        """
        return _fbInterpretPathProbe(
            self._ftRunTypedRead(
                sContainerId, S_TYPED_READ_FILE_EXISTS, sPath,
            ),
            sPath,
        )

    def fbContainerPathIsDirectory(self, sContainerId, sPath):
        """Return True iff the container path is an existing directory.

        The ``test -d`` half of :meth:`fbContainerPathIsFile`; see there
        for why these are typed reads rather than execs.
        """
        return _fbInterpretPathProbe(
            self._ftRunTypedRead(
                sContainerId, S_TYPED_READ_DIRECTORY_EXISTS, sPath,
            ),
            sPath,
        )

    def flistContainerPathsExist(self, sContainerId, listPaths):
        """Return one exists/absent answer per path, in the order given.

        The BATCHED sibling of the two probes above, and the reason the
        exemption takes a collection at all: the file panel probes up to
        a thousand paths on one debounced keystroke, and looping a
        single-path adapter would be a thousand container round-trips.
        The caller still supplies only PATHS, and this method still
        supplies only the NAME of a declared read operation.

        It replaced a shell heredoc that interpolated every path raw,
        which meant a path containing the heredoc's own terminator on a
        line of its own ended the heredoc and turned the rest into
        shell.

        A failed READ raises ``OSError``; absent paths come back False.
        An answer count that does not match the request is also an
        ``OSError`` -- a short list would otherwise silently realign
        every answer after the gap onto the wrong path.
        """
        if not listPaths:
            return []
        # Batched against the exec argument budget, because the whole
        # list is rendered into ONE argument: unbatched this raised
        # "argument list too long" at ~1,845 paths of 59 bytes, and
        # inside a carrier worker that raise poisons the journal
        # record and quarantines the container. See
        # execArgumentBudget for the measurement.
        listAnswers = []
        for listBatch in flistBatchPathsForOneExec(list(listPaths)):
            listAnswers.extend(_flistInterpretBooleanBatch(
                self._ftRunTypedRead(
                    sContainerId, S_TYPED_READ_PATHS_EXIST, listBatch,
                ),
                listBatch, "existence",
            ))
        return listAnswers

    def fdictHashContainerRepoPaths(
        self, sContainerId, sRootPath, listRelPaths,
    ):
        """Hash repo-relative container files as a typed READ, batched.

        The hashing sibling of :meth:`flistContainerPathsExist`. It
        exists because the repo-files adapter's embedded hash script
        travelled through the GENERAL exec primitive, which the gate
        must treat as mutating -- so a remote verify running in an
        enforced lane had every comparison refused (2026-09-02). The
        caller supplies the repository root and repo-relative paths;
        the program, its symlink walk and its realpath containment are
        fixed module text.

        Batched against the exec argument budget like every batched
        probe; the root rides at the head of each batch. A batch that
        fails or answers garbage collapses the WHOLE result to ``{}``,
        never a partial map -- a partial map reads as a claim about
        the files it omits, which is the badge-probe lesson.
        """
        if not listRelPaths:
            return {}
        dictMerged = {}
        for listBatch in flistBatchPathsForOneExec(list(listRelPaths)):
            tExecResult = self._ftRunTypedRead(
                sContainerId, S_TYPED_READ_REPO_HASHES,
                [sRootPath] + listBatch,
            )
            if tExecResult.iExitCode != 0:
                return {}
            try:
                dictBatch = json.loads(tExecResult.sStdout or "{}")
            except ValueError:
                return {}
            if not isinstance(dictBatch, dict):
                return {}
            dictMerged.update(dictBatch)
        return dictMerged

    def ftReadRepoSnapshot(
        self, sContainerId, sRootPath, listContentPaths,
        listSkipTextPaths, listHashPaths, listAbsHashPaths,
        dictCachedKeys=None, bHashManifestEntries=False,
        bReadReproductions=False,
    ):
        """Run the one-exec poll snapshot as a DECLARED read.

        The snapshot used to travel through the general exec
        primitive, which the mutation gate must treat as mutating --
        so inside an enforced lane it could only run under an
        admission, and the readiness route parked it inside the
        pausable lock probe. Every dashboard open lost that race, the
        probe paused, and the gates fell back to file-by-file reads:
        ten seconds of round trips whose only cause was WHERE the
        snapshot sat. The caller supplies the root and four path
        groups; the program is fixed module text shared with the
        legacy embedded transport.

        NOT batched: one snapshot is one coherent answer, so an
        over-budget path list is REFUSED with the counts named rather
        than split or silently truncated -- the badge-probe lesson is
        that the silent shape of this failure reads as a claim about
        every file it dropped. The caller falls back to the live
        adapter, which is slow and correct.
        """
        listArgs = ["r:" + (sRootPath or "")]
        for sPrefix, listGroup in (
            ("c", listContentPaths), ("k", listSkipTextPaths),
            ("h", listHashPaths), ("a", listAbsHashPaths),
        ):
            for sPath in listGroup or []:
                listArgs.append(sPrefix + ":" + sPath)
        if bHashManifestEntries:
            listArgs.append("f:manifestEntries")
        if bReadReproductions:
            listArgs.append("f:reproductions")
        listKeyArgs = [
            "x:" + ",".join(str(int(i)) for i in listKey) + "|" + sPath
            for sPath, listKey in sorted((dictCachedKeys or {}).items())
        ]
        # The RENDERED single argument, not an estimate of the path
        # bytes going into it: repr() doubles every backslash and
        # escapes what it must, so an estimate admits a command the
        # kernel still refuses -- measured with backslash-heavy POSIX
        # names rendering to twice their estimate (external review,
        # 2026-09-16). This renders the same program the typed read
        # will run, so the number is the argument's actual size.
        iRenderedBytes = _fiRenderedSnapshotBytes(listArgs + listKeyArgs)
        if listKeyArgs and iRenderedBytes <= I_EXEC_ARGUMENT_BUDGET_BYTES:
            listArgs = listArgs + listKeyArgs
        else:
            # The cached keys are an optimisation: past the budget the
            # program simply rehashes what it was not told it may skip,
            # which is slower and correct.
            iRenderedBytes = _fiRenderedSnapshotBytes(listArgs)
        if iRenderedBytes > I_EXEC_ARGUMENT_BUDGET_BYTES:
            raise ValueError(
                f"the repository snapshot's {len(listArgs)} paths "
                f"render to a {iRenderedBytes}-byte exec argument, "
                f"over the {I_EXEC_ARGUMENT_BUDGET_BYTES}-byte "
                "budget; refusing loudly instead of splitting one "
                "snapshot into two moments or truncating it silently"
            )
        return self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_REPO_SNAPSHOT, listArgs,
        )

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        """Return one is-a-directory answer per path, in the order given.

        The type sibling of ``flistContainerPathsExist``, batched for
        the same reason and answered by its own declared program. A
        caller that needs both asks both and gets two round trips
        rather than one per entry.

        The operation name is written out here rather than threaded in
        beside the paths, as it is in the sibling above: the exemption
        is granted to a NAME this class chooses, and a name arriving as
        a variable is a name a caller could choose.
        """
        if not listPaths:
            return []
        return _flistInterpretBooleanBatch(
            self._ftRunTypedRead(
                sContainerId, S_TYPED_READ_DIRECTORIES_EXIST,
                list(listPaths),
            ),
            listPaths, "directory",
        )

    def flistReadGitRepoStatuses(self, sContainerId, listRepoPaths):
        """Return one raw status record per repository path, in order.

        Each record carries ``sPath``, ``bMissing``, and — for a
        present repository — ``sBranch``, ``sUrl`` and ``sPorcelain``
        exactly as git printed them. Interpreting those (filtering
        artefacts, deciding dirtiness) is the caller's business and
        stays out here: this method's whole job is to get the bytes
        back without a shell in the middle.

        A failed READ raises ``OSError``, and so does an unparseable
        answer. A repository git could not answer about is NOT a
        failed read — it comes back with empty fields, which is the
        same distinction the poll has always drawn.
        """
        if not listRepoPaths:
            return []
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_GIT_REPO_STATUS,
            list(listRepoPaths),
        )
        if tExecResult.iExitCode != 0:
            raise OSError(
                "Cannot read repository status in container "
                f"({tExecResult.sStderr.strip()})"
            )
        try:
            return json.loads(tExecResult.sStdout.strip() or "[]")
        except ValueError as errorParse:
            raise OSError(
                "The repository status read answered unparseable "
                f"output: {errorParse}"
            )

    def fdictFetchTrackedIdentities(self, sContainerId, sRepoPath):
        """Return the git-tracked index joined to the worktree, per path.

        The council's git-tracked snapshot scope reads through this: the
        declared ``gitTrackedIdentities`` program joins ``ls-files -s``
        (mode, merge stages) with ``ls-files -t`` (the skip-worktree
        tag), then ``lstat``s each path and hashes the current worktree
        bytes in the container. Returns the program's ``{"bSuccess",
        "sReason", "sHeadSha", "sPorcelainDigest", "iChangedCount",
        "dictEntries"}``; ``bSuccess`` False is a refusal to observe.
        A failed READ, or unparseable output, raises ``OSError``.
        """
        return _fdictParseJsonTypedRead(self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_GIT_TRACKED_IDENTITIES, sRepoPath,
        ), "tracked identities")

    def fdictFetchUntrackedInventory(self, sContainerId, sRepoPath):
        """Return the bounded untracked and ignored inventory for a repo.

        Names and sizes only -- contents are never read. The program
        stops at :data:`I_MAX_OMISSION_INVENTORY_PATHS` paths or
        :data:`I_MAX_OMISSION_INVENTORY_NAME_BYTES` of names and says so
        with ``bComplete`` False, so every screen can say "at least".
        """
        return _fdictParseJsonTypedRead(self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_GIT_UNTRACKED_INVENTORY, sRepoPath,
        ), "untracked inventory")

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
        """Return the worktree path identity observation for one repo.

        The coherence read behind a bulk repository export: the declared
        ``gitWorktreeIdentities`` program enumerates every present worktree
        path (tracked, untracked and ignored) and computes each one's content
        identity in the container, over the raw bytes. The command is not built
        here — this names a declared read operation and :meth:`_ftRunTypedRead`
        builds it, so the repository path cannot become program or shell
        syntax. Returns the program's ``{"bSuccess", "sReason", "sHeadSha",
        "sPorcelainDigest", "listIgnoredPaths", "dictPathIdentities"}`` answer
        (a failure carries only ``bSuccess``, ``sReason`` and an empty
        ``dictPathIdentities``); callers must treat ``bSuccess`` False as a
        refusal to observe, never as a quiet repository. A failed READ raises
        ``OSError``, and so does an unparseable answer.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_GIT_WORKTREE_IDENTITIES, sRepoPath,
        )
        if tExecResult.iExitCode != 0:
            raise OSError(
                "Cannot observe worktree identities in container "
                f"({tExecResult.sStderr.strip()})"
            )
        try:
            return json.loads(tExecResult.sStdout.strip() or "{}")
        except ValueError as errorParse:
            raise OSError(
                "The worktree identity read answered unparseable "
                f"output: {errorParse}"
            )

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        """Return ``{sAbsPath: sMtime}`` for the paths that exist.

        The file panel's poll, as a typed read. Absent paths are simply
        missing from the answer — the caller asks "which of these exist
        and when did they change", and there is no third state. A failed
        READ raises ``OSError``; an unparseable answer is a failed read.

        Unlike :meth:`flistContainerPathsExist` this does NOT check the
        answer's length against the request, and must not: a short list
        there would silently realign positional answers onto the wrong
        paths, while here every answer carries its own key.
        """
        if not listPaths:
            return {}
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_PATH_MTIMES, list(listPaths),
        )
        if tExecResult.iExitCode != 0:
            raise OSError(
                "Cannot stat paths in container "
                f"({tExecResult.sStderr.strip()})"
            )
        try:
            return json.loads(tExecResult.sStdout.strip() or "{}")
        except ValueError as errorParse:
            raise OSError(
                f"The batched stat answered unparseable output: "
                f"{errorParse}"
            )

    def fdictFetchSmallFiles(self, sContainerId, listPaths):
        """Return ``{sPath: bytes or None}`` for small files, batched.

        The many-file sibling of :meth:`fbaFetchFile`, for documents
        bounded by design (test markers, state JSON). ``None`` means the
        file could not be opened -- absent or unreadable -- exactly the
        case in which :meth:`fbaFetchFile` raises ``FileNotFoundError``.
        A file over :data:`I_MAX_SMALL_FILE_BYTES` raises ``ValueError``
        naming it, and a failed batch raises ``OSError``: a partial map
        would read as "these files are absent" for the ones it omits.
        """
        dictFiles = {}
        for listBatch in flistBatchPathsForOneExec(list(listPaths)):
            dictFiles.update(_fdictDecodeSmallFilesBatch(
                self._ftRunTypedRead(
                    sContainerId, S_TYPED_READ_SMALL_FILES_BASE64,
                    listBatch,
                ),
                listBatch,
            ))
        return dictFiles

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        """Return a file's sha256 hex digest, or ``''`` when unreadable.

        The empty answer is the contract, not a swallowed failure: the
        reload detector compares fingerprints and treats "no
        fingerprint" as "cannot compare", falling back to its other
        signals. A file that does not exist yet is the ordinary case on
        a fresh workflow.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_FILE_SHA256, sPath,
        )
        if tExecResult.iExitCode != 0:
            raise OSError(
                f"Cannot hash file in container: {sPath} "
                f"({tExecResult.sStderr.strip()})"
            )
        return tExecResult.sStdout.strip()

    def fsReadClockUtc(self, sContainerId):
        """Return the container's wall clock as ``YYYY-MM-DD HH:MM:SS UTC``.

        The clock that stamps the container's files, which is the one a
        sign-off must be dated by: every freshness check compares file
        mtimes against it, and the hub's own clock is a different
        clock. An AUDITED ADAPTER taking no argument at all.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_CLOCK_UTC, "/",
        )
        if tExecResult.iExitCode != 0:
            raise OSError(
                "Cannot read the container's clock: "
                f"{tExecResult.sStderr.strip()}"
            )
        return tExecResult.sStdout.strip()

    def fdictReadFilesystemUsage(self, sContainerId, sPath):
        """Return total/used/free bytes for the filesystem holding a path.

        An AUDITED ADAPTER, on the same terms as the other two: the
        caller supplies a PATH and this method supplies only the NAME of
        a declared read operation, so a path cannot become program or
        shell syntax.

        This replaced ``docker exec -u <user> <id> df -PB1 /`` assembled
        in a GUI module. That was a container EXEC outside every guarded
        primitive, and a disk reading is the least of what an exec can
        do -- which is precisely why the boundary treats arbitrary
        command execution as mutating and why a read has to be typed
        rather than trusted.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_FILESYSTEM_USAGE, sPath,
        )
        if tExecResult.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot stat filesystem in container: {sPath}"
            )
        return json.loads(tExecResult.sStdout.strip())

    def fdictReadDaemonCapacity(self):
        """Return ``{iMemoryBytes, iCpuCount}`` the DAEMON has to give.

        Not a container read: a daemon-API query, so no typed-read seam
        applies. It exists because the host's memory is the wrong number
        for anything that runs in a container. On Linux the daemon
        shares the host's kernel and the two agree; on macOS and Windows
        the daemon lives in a virtual machine with its own, usually much
        smaller, allocation -- 16 GB of host RAM over an 8.3 GB Docker
        VM on the machine this was measured on. Sizing a container from
        host RAM would over-provision it there and the kill would arrive
        at run time.

        A daemon that will not answer yields zeroes rather than an
        exception: every caller has a declared floor to fall back to,
        and refusing work because ``docker info`` hiccuped would be a
        worse answer than using the conservative bound.
        """
        return fdictReadDaemonCapacityFromClient(self._clientDocker)

    def fbaFetchDirectoryArchive(
        self, sContainerId, sDirectoryPath, iMaxBytes,
    ):
        """Return one container directory as tar bytes, under a hard cap.

        Bulk export is a different primitive from a file read, and it is
        deliberately NOT a typed read. The typed-read carve-out is
        granted at exactly one private method, and its whole safety
        argument is that each entry is a small fixed program over a path
        literal; a repository-scale export does not belong in that
        table. ``container.get_archive`` is neither a typed read nor a
        general command: it is a daemon API read that executes NO
        program in the container -- the daemon itself serializes the
        filesystem -- so nothing caller-supplied can become program text
        and the container gains no process. The same API already backs
        :meth:`fiterStreamFile`.

        ``iMaxBytes`` is a HOST bound: the archive is materialised in
        the hub's own address space, so the cap is a fraction of the
        researcher's RAM rather than of the daemon's. Exceeding it
        raises ``ValueError`` mid-stream, before the whole tree has been
        paid for -- a cap applied after materialisation would refuse
        only what it had already accepted.
        """
        container = self.fcontainerGetById(sContainerId)
        try:
            tStreamStat = container.get_archive(sDirectoryPath)
        except Exception as error:
            raise FileNotFoundError(
                "Cannot read directory from container: "
                f"{sDirectoryPath}: {error}"
            )
        iterTarStream, _ = tStreamStat
        return _fbaCollectBoundedTarStream(
            iterTarStream, iMaxBytes, sDirectoryPath,
        )

    def fdictWeighRepository(self, sContainerId, sRepositoryPath):
        """Return ``{iFileCount, iTotalBytes, bTruncated}`` for a repo.

        An audited adapter on the same terms as its neighbours: the
        caller supplies a PATH and this supplies the NAME of a declared
        read, so a path cannot become program text.

        It exists so the council can answer "would a snapshot of this
        repository be accepted?" from metadata, before a researcher
        composes a question. ``bTruncated`` means the walk stopped at
        its own cap — an answer of "more files than we will ever
        accept", which is the same verdict as an exact count too large.
        """
        tExecResult = self._ftRunTypedRead(
            sContainerId, S_TYPED_READ_REPOSITORY_WEIGHT, sRepositoryPath,
        )
        if tExecResult.iExitCode != 0:
            raise FileNotFoundError(
                f"Cannot weigh repository in container: {sRepositoryPath}"
            )
        return json.loads(tExecResult.sStdout.strip())

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

    def fiterReadFileConfined(
        self, sContainerId, sFilePath, sAuthorizedRoot=None,
    ):
        """Yield a container file's bytes in chunks, read as the container user.

        The race-free replacement for :meth:`fiterStreamFile` on a
        download. ``get_archive`` reads as root through the daemon, so a
        component an agent swapped for a symlink after the caller's path
        check redirected the read anywhere in the container. This execs
        a fixed program (see :mod:`vaibify.docker.confinedRead`) that
        walks the path with ``O_NOFOLLOW`` against descriptors it holds.

        A final symlink is followed only when its target, resolved as
        text against ``sAuthorizedRoot``, is inside it. A refusal or a
        missing path raises BEFORE the first chunk is yielded, so a route
        that pulls once can still answer an HTTP error. Memory is bounded
        by the chunk whatever the file's size.
        """
        sProgram = self._fsRenderReadProgramOrRefuse(
            confinedRead.fsRenderConfinedReadProgram, sFilePath,
            sAuthorizedRoot,
        )
        yield from self._fiterRunProgramStdout(
            sContainerId, ["python3", "-c", sProgram], sFilePath,
        )

    def fiterReadDirectoryAsTar(
        self, sContainerId, sDirectoryPath, sAuthorizedRoot=None,
    ):
        """Yield a tar of a container directory, read as the container user.

        The folder sibling of :meth:`fiterReadFileConfined`: a fixed
        program walks the tree with held descriptors, stores links as
        links without ever following one, and skips and counts devices,
        sockets and FIFOs. The archive holds no member beneath a link.
        """
        sProgram = self._fsRenderReadProgramOrRefuse(
            confinedRead.fsRenderConfinedArchiveProgram, sDirectoryPath,
            sAuthorizedRoot,
        )
        yield from self._fiterRunProgramStdout(
            sContainerId, ["python3", "-c", sProgram], sDirectoryPath,
        )

    @staticmethod
    def _fsRenderReadProgramOrRefuse(fsRender, sPath, sAuthorizedRoot):
        """Render a read program; a path the renderer rejects is a refusal.

        The renderer raises ``ValueError`` for a path that is not below
        its root, which a researcher can supply; it is answered with the
        same refusal the program gives, so a route handles one error.
        """
        try:
            return fsRender(sPath, sAuthorizedRoot)
        except ValueError as error:
            raise confinedRead.ContainerReadRefusedError(
                f"Read of {sPath} refused: {error}"
            ) from error

    def _fiterRunProgramStdout(self, sContainerId, listCommand, sPath):
        """Exec a fixed read program as the container user; yield its stdout.

        Not a general exec: it is private, it takes a program the
        confined-read renderer built, and it only reads. The output is
        yielded as the daemon delivers it, never collected, and the
        program's exit status is checked once the stream ends: a program
        that failed after sending bytes raises here, which aborts the
        caller's response rather than letting a truncated body pass for
        a whole one.
        """
        import logging
        import socket
        from docker.utils.socket import STDERR, frames_iter
        sExecId = self.fsExecCreate(
            sContainerId, listCommand=listCommand, bTty=False,
        )
        socketExec = self.fsocketExecStart(sExecId, bTty=False)
        baStderr = bytearray()
        try:
            getattr(socketExec, "_sock", socketExec).shutdown(socket.SHUT_WR)
            for iStream, baChunk in frames_iter(socketExec, tty=False):
                if iStream != STDERR:
                    yield baChunk
                elif len(baStderr) < _I_MAX_READ_STDERR_BYTES:
                    baStderr.extend(baChunk)
        finally:
            socketExec.close()
        sStderr = bytes(baStderr).decode("utf-8", errors="replace")
        confinedRead.fnRaiseWhenReadFailed(
            self._fiAwaitExecExitCode(sExecId), sStderr, sPath,
        )
        iSkipped = confinedRead.fiParseSkippedCount(sStderr)
        if iSkipped:
            logging.getLogger("vaibify").warning(
                "Archived %s without %d special or too-deep entries",
                sPath, iSkipped,
            )

    def _fiAwaitExecExitCode(self, sExecId):
        """Return an exec's exit status once the daemon says it settled.

        The stream ends when the program closes its output, which can be
        a moment before the daemon records how it exited. Treating that
        gap as exit status 0 would let a failed read pass as a complete
        one, so an unsettled exec is an error, never a success.
        """
        import time
        for _ in range(_I_EXEC_SETTLE_ATTEMPTS):
            dictInspect = self.fdictInspectExec(sExecId)
            if (not dictInspect.get("Running")
                    and dictInspect.get("ExitCode") is not None):
                return int(dictInspect["ExitCode"])
            time.sleep(_F_EXEC_SETTLE_INTERVAL_SECONDS)
        raise OSError(
            "The container did not report how the read ended, so the "
            "bytes received cannot be trusted as complete"
        )

    def fnWriteFile(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Write bytes to a file inside the container, as the container user.

        ``iMode`` is the requested permission mask (default 0644); a
        secret-bearing file passes 0600 and is never readable by anyone
        else, not even for the instant before a follow-up ``chmod``.
        ``sAuthorizedRoot`` and ``tForbiddenNames`` confine a
        caller-supplied path to a project: the path must lie below the
        root and may not pass through any forbidden name. ``iUid`` and
        ``iGid`` are accepted for the duck type shared with the host
        connection and are ignored: the file is created by the container
        user, so that user owns it.
        """
        self.fnWriteFileViaTar(
            sContainerId, sFilePath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
            sAuthorizedRoot=sAuthorizedRoot,
            tForbiddenNames=tForbiddenNames,
        )

    def fnWriteFileViaTar(
        self, sContainerId, sFilePath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Write ``baContent`` to one file; the bytes-in-memory entry point.

        The name is historical: this no longer builds a tarball. It is
        :meth:`fnWriteFileFromStream` over a buffer, stating the byte
        count so a transfer that ends short is refused rather than
        renamed into place. ``iUid`` and ``iGid`` are accepted for the
        duck type shared with the host connection and are ignored.
        """
        del iUid, iGid
        self.fnWriteFileFromStream(
            sContainerId, sFilePath, io.BytesIO(baContent),
            iExpectedBytes=len(baContent), iMode=iMode,
            sAuthorizedRoot=sAuthorizedRoot,
            tForbiddenNames=tForbiddenNames,
        )

    def fnWriteFileFromStream(
        self, sContainerId, sFilePath, fileSource,
        iExpectedBytes=None, bReplaceAllowed=True, iMode=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        """Write one file from a readable stream, symlink-safe, unprivileged.

        Handing the daemon an archive made a write run as root and
        follow every symlink the in-container agent had planted, so the
        funnel execs a fixed program (see
        :mod:`vaibify.docker.confinedWrite`) as the container user and
        streams the bytes on its stdin in chunks: a file of any size is
        written in bounded memory. The program opens each path
        component with ``O_NOFOLLOW`` against the descriptor it already
        holds, so a swapped component is refused and cannot redirect
        the write.

        ``iExpectedBytes`` refuses a stream whose length differs and
        ``bReplaceAllowed`` False refuses an existing target
        (:class:`~vaibify.docker.confinedWrite.ContainerWriteExistsError`);
        either way the old file is untouched. A full disk raises
        ``OSError`` with ``errno.ENOSPC``.

        This is the workspace-file-write funnel the commit-guard
        carrier guards (design §8): in an enforced lane (an HTTP
        request or a carrier-launched durable task) the write refuses
        to proceed without a live, still-current carrier admission for
        this container — before any byte reaches the daemon. The exec
        beneath it is private to this method and carries no command
        text a caller chose, so it needs no second admission.
        """
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteFileFromStream",
        )
        sProgram = confinedWrite.fsRenderConfinedWriteProgram(
            sFilePath, iMode=iMode, sAuthorizedRoot=sAuthorizedRoot,
            tForbiddenNames=tForbiddenNames,
            bReplaceAllowed=bReplaceAllowed, iExpectedBytes=iExpectedBytes,
        )
        tExecResult = self._ftRunProgramWithStdin(
            sContainerId, ["python3", "-c", sProgram], fileStdin=fileSource,
        )
        confinedWrite.fnRaiseWhenWriteFailed(tExecResult, sFilePath)

    def ftRunProgramWithStdin(self, sContainerId, listCommand, baStdin):
        """Run a program as the container user with ``baStdin`` as its input.

        The way to hand a program a secret: the argument vector is exact
        (no shell composes it) and the bytes travel on the exec's stdin,
        so a credential appears in neither the exec's command line nor
        ``docker inspect``. It is arbitrary command execution and is
        gated as such: in an enforced lane it refuses without a live
        carrier admission. Returns the :class:`ExecResult`.
        """
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftRunProgramWithStdin",
        )
        return self._ftRunProgramWithStdin(
            sContainerId, listCommand, baStdin,
        )

    def _ftRunProgramWithStdin(
        self, sContainerId, listCommand, baStdin=None, fileStdin=None,
    ):
        """Exec ``listCommand`` as the container user, feeding it stdin.

        No shell sits between the daemon and the program: the argument
        vector is exact. The write half of the hijacked connection is
        shut down once the payload is sent so the program sees EOF; a
        program that exits before reading everything (a refusal) is not
        an error here, its exit code is the answer. The payload is
        ``baStdin`` held in memory, or ``fileStdin`` streamed in chunks
        so a whole directory tree never has to fit in memory.
        """
        sExecId = self.fsExecCreate(
            sContainerId, listCommand=listCommand, bTty=False,
        )
        socketExec = self.fsocketExecStart(sExecId, bTty=False)
        try:
            baStdout, baStderr = _ftExchangeWithExecSocket(
                socketExec, baStdin, fileStdin=fileStdin,
            )
        finally:
            socketExec.close()
        dictInspect = self.fdictInspectExec(sExecId)
        return ExecResult(
            iExitCode=int(dictInspect.get("ExitCode") or 0),
            sStdout=baStdout.decode("utf-8", errors="replace"),
            sStderr=baStderr.decode("utf-8", errors="replace"),
        )

    def fnWriteTreeViaTar(
        self, sContainerId, sDestinationDirectory, listHostPaths,
        iUid=None, iGid=None, sArchiveName=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
        bCreateDestination=False,
    ):
        """Copy host files and directories into a container directory.

        The bulk sibling of :meth:`fnWriteFileViaTar`, and the only way
        host-side content reaches a workspace volume: until this
        existed, a container was populated exclusively by the
        entrypoint's git clones, so a researcher converting a local
        directory to a container got an empty workspace and no
        indication anything was missing (2026-08-21).

        One archive, one round trip, whatever the tree's shape --
        ``tarfile`` walks a directory natively. Symlinks are archived
        AS symlinks (tarfile's default), so a link pointing outside the
        project copies the link and never the host bytes it names.

        The archive is NOT handed to the daemon. ``put_archive`` extracts
        as root and follows every symlink it meets, so a link the
        in-container agent planted at the destination, or at any
        directory on the way to it, redirected the copy anywhere in the
        container and handed an existing root-owned directory to the
        container user. The spooled archive is instead streamed to a
        fixed program (see :mod:`vaibify.docker.confinedWrite`) that
        runs as the container user and lands each member relative to
        directory descriptors it holds, refusing a symlinked directory
        rather than following it. Ownership needs no stamp: the user
        who creates a file owns it. ``iUid`` and ``iGid`` are accepted
        for the duck type shared with the host connection and ignored.

        The destination must already exist unless ``bCreateDestination``
        asks the program to create the components below
        ``sAuthorizedRoot``. Each path lands under its own basename.
        ``sArchiveName``, for a call that copies exactly one path, is
        the name that path lands under instead: how a directory is
        copied to a destination that does not exist yet and is to be
        created with a different name. A refusal or failure raises with
        ``iMembersLanded`` set, so a caller can tell a clean refusal
        (zero) from a partial copy.
        """
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteTreeViaTar",
        )
        del iUid, iGid
        sProgram = confinedWrite.fsRenderConfinedTreeProgram(
            sDestinationDirectory, sAuthorizedRoot=sAuthorizedRoot,
            tForbiddenNames=tForbiddenNames,
            bCreateDestination=bCreateDestination,
        )
        fileTar = self._ffileBuildTreeTar(listHostPaths, sArchiveName)
        try:
            tExecResult = self._ftRunProgramWithStdin(
                sContainerId, ["python3", "-c", sProgram], fileStdin=fileTar,
            )
        finally:
            fileTar.close()
        confinedWrite.fnRaiseWhenTreeWriteFailed(
            tExecResult, sDestinationDirectory,
        )

    def fnCopyHostPathIntoContainer(
        self, sContainerId, sHostSource, sContainerDestination,
    ):
        """Copy one host file or directory in, owned by the container user.

        The composed form of the two writers above, and the reason the
        CLI's ``vaibify push`` no longer shells out to ``docker cp``:
        that lands the destination owned by root, and the container
        user is unprivileged with no sudo by design, so everything
        pushed became unmodifiable by the researcher and by the
        in-container agent. It lives HERE rather than beside the CLI
        because both writers and the directory probe it needs are
        here, and a copy assembled anywhere else is a mutation-capable
        reach around the gateway that owns them.

        ``docker cp``'s destination reading is preserved: a
        destination naming an existing directory receives the source
        under its own basename; any other destination IS the path to
        write, so a directory copied to a path that does not exist yet
        is created AT that path, under that path's own name.
        """
        import os
        import posixpath
        bDestinationIsDirectory = self.fbContainerPathIsDirectory(
            sContainerId, sContainerDestination,
        )
        if os.path.isdir(sHostSource):
            sDestinationPath = sContainerDestination.rstrip("/") or "/"
            self.fnWriteTreeViaTar(
                sContainerId,
                sContainerDestination if bDestinationIsDirectory
                else posixpath.dirname(sDestinationPath),
                [sHostSource],
                sArchiveName=(
                    None if bDestinationIsDirectory
                    else posixpath.basename(sDestinationPath)),
            )
            return
        with open(sHostSource, "rb") as fileSource:
            baContent = fileSource.read()
        self.fnWriteFileViaTar(
            sContainerId,
            posixpath.join(
                sContainerDestination, os.path.basename(sHostSource),
            ) if bDestinationIsDirectory else sContainerDestination,
            baContent,
        )

    @staticmethod
    def _ffileBuildTreeTar(listHostPaths, sArchiveName=None):
        """Return a rewound tar of the host paths, claiming no ownership.

        ``sArchiveName`` renames the one path being archived; naming it
        for several paths would put them all at one name, so it is
        refused.

        Spooled rather than held in a ``BytesIO``: a researcher's
        directory is arbitrarily large, and the single-file path's
        in-memory buffer is only safe because its caller already holds
        the bytes. Every entry's owner is cleared rather than inherited:
        the receiving program ignores it (the user who creates a file
        owns it), and a host login name has no business on the wire.
        """
        import os
        import tarfile
        import tempfile
        if sArchiveName is not None and len(listHostPaths) != 1:
            raise ValueError(
                "an archive name renames exactly one host path, not "
                f"{len(listHostPaths)}"
            )
        fileTar = tempfile.SpooledTemporaryFile(
            max_size=_I_TREE_TAR_SPOOL_BYTES,
        )
        with tarfile.open(fileobj=fileTar, mode="w") as fileArchive:
            for sHostPath in listHostPaths:
                fileArchive.add(
                    sHostPath,
                    arcname=sArchiveName or os.path.basename(sHostPath),
                    filter=_finfoClearOwnership,
                )
        fileTar.seek(0)
        return fileTar

    def fsExecCreate(
        self, sContainerId, sCommand="/bin/bash", sUser=None,
        listCommand=None, bTty=True,
    ):
        """Create an interactive exec instance, return exec id.

        Defaults to the unprivileged container user when ``sUser`` is
        omitted so terminal sessions opened from the dashboard do not
        land as root. ``listCommand`` bypasses docker-py's shlex split
        of a string command for callers that need an exact argv (the
        terminal containment wrapper's ``/bin/sh -c`` script would be
        destroyed by tokenization). ``bTty`` False asks for the plain
        multiplexed stream, which the stdin-fed program write needs so
        its standard error stays separate from its standard output.
        """
        container = self.fcontainerGetById(sContainerId)
        if sUser is None:
            sUser = _fsResolveContainerUser(container)
        dictKwargs = {
            "cmd": listCommand if listCommand is not None else sCommand,
            "tty": bTty,
            "stdin": True,
            "stdout": True,
            "stderr": True,
            "user": sUser,
        }
        sExecId = self._clientDocker.api.exec_create(
            container.id, **dictKwargs
        )["Id"]
        return sExecId

    def fsocketExecStart(self, sExecId, bTty=True):
        """Start exec and return the raw socket."""
        return self._clientDocker.api.exec_start(
            sExecId, socket=True, tty=bTty
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

    def ftRunRootShellProbe(self, sContainerId, sScript, sUser="root"):
        """Run a ``/bin/sh`` script as ``sUser``; return (iExitCode, sOutput).

        The containment-probe primitive (design v13 §6.1): group
        discovery, group signalling, and group-emptiness proof all run
        through it. It deliberately uses ``/bin/sh`` — not the bash the
        ordinary exec paths assume — so the probes work in minimal
        images. Root is the default for the READ probes, which walk
        ``/proc`` and need no capability. The SIGNAL leg cannot rely on
        root: vaibify's containers run with every capability dropped
        except a named few, and without ``CAP_KILL`` in-container root
        gets EPERM signalling the unprivileged terminal user's
        processes — silently, so the group survived every TERM/KILL and
        the record quarantined. A signal caller therefore passes the
        target's OWN user, whose same-uid signals need no capability.
        Probes are part of the authority machinery, not route-reachable
        mutations, so they carry no journal record.
        """
        container = self.fcontainerGetById(sContainerId)
        iExitCode, baOutput = container.exec_run(
            ["/bin/sh", "-c", sScript], user=sUser, demux=False,
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
            if fbErrorMeansContainerGone(error):
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
        # Two passes, one per process owner the container can hold. The
        # container drops CAP_KILL, so root's kill reaches only
        # root-owned members and EPERMs on the terminal user's shell --
        # which is every terminal, since sessions spawn unprivileged.
        # The container-user pass reaches those with same-uid signals,
        # which need no capability. Each pass is swallowed
        # independently: the terminate-and-prove caller decides on the
        # PROOF, never on delivery.
        for sExecUser in ("root", str(_I_CONTAINER_DEFAULT_UID)):
            try:
                self.ftRunRootShellProbe(
                    sContainerId, sScript, sUser=sExecUser,
                )
            except Exception:
                pass


# POSIX-sh walk of /proc/*/stat matching a target session/process
# group. The comm field can contain spaces and parentheses, so the
# fields after it are recovered by stripping through the LAST ')'
# (``${sStatContent##*) }``); positional field 1 is then the state,
# 3 is pgrp and 4 is session. The probe excludes its own shell by pid,
# and IGROUPTARGET is substituted only after integer validation, so no
# caller-supplied text can reach the script.
#
# A Z-state entry is skipped: a zombie is an exit record awaiting a
# parent that may never collect it, not a process — it cannot execute,
# write a file, or hold a socket, so every risk the quiescence claim
# guards against is provably false for it. Counting zombies made a
# quarantine unclearable short of a container restart whenever PID 1
# does not reap (observed 2026-08-14: a defunct agent under a
# sleep-infinity init refused reconcile forever). A stat line that
# cannot be read still falls out of the walk, and an unparseable COUNT
# is still inconclusive at the caller — the fail-closed shape is
# unchanged.
_S_PROCESS_GROUP_SCRIPT_TEMPLATE = """iCount=0
for sStatPath in /proc/[0-9]*/stat; do
  sStatContent=$(cat "$sStatPath" 2>/dev/null) || continue
  sStatTail="${sStatContent##*) }"
  set -- $sStatTail
  [ "$3" = "IGROUPTARGET" ] || [ "$4" = "IGROUPTARGET" ] || continue
  [ "$1" = "Z" ] && continue
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


def fdictReadDaemonCapacityFromClient(dockerClient):
    """Return ``{iMemoryBytes, iCpuCount}`` a docker-py client's daemon has.

    The one reading of ``docker info``, shared by this connection and by
    the council, whose gateway holds a bare client. Zeroes when the
    daemon will not answer.
    """
    try:
        dictInfo = dockerClient.info()
    except Exception:
        return {"iMemoryBytes": 0, "iCpuCount": 0}
    return {
        "iMemoryBytes": int(dictInfo.get("MemTotal") or 0),
        "iCpuCount": int(dictInfo.get("NCPU") or 0),
    }


def fbErrorMeansContainerGone(error):
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


def fbErrorMeansContainerUnreachable(error):
    """Return True when the substrate failed to answer for a container.

    Weaker than :func:`fbErrorMeansContainerGone`: it proves nothing
    about the container's processes, only that the connection layer
    could not complete the operation — so a poll-lane caller should
    degrade to "no answer this tick" instead of crashing the poll.

    Connection-level on purpose. GUI modules used to name the Docker
    SDK's exception types in their own ``except`` clauses, which
    misclassifies any other connection implementation (a host-mode
    connection raises plain ``OSError``\\ s). They now ask this
    predicate, so teaching the classification a new connection's error
    shapes is one edit here, not one per poll lane.

    Docker leg: exactly the SDK's ``APIError`` family (``NotFound``
    included), the set the poll lanes historically caught. The lazy
    import keeps this module loadable without docker-py, and no
    ``APIError`` can exist in-process without docker-py.
    """
    try:
        from docker.errors import APIError
    except ImportError:
        return False
    return isinstance(error, APIError)


def _fbaCollectBoundedTarStream(iterTarStream, iMaxBytes, sDirectoryPath):
    """Concatenate a get_archive stream, refusing past ``iMaxBytes``.

    The bound is checked as each chunk arrives rather than on the
    finished archive: a cap that inspects the total has already paid
    for every byte it refuses, which on a repository-scale export is
    the cost the cap exists to avoid.
    """
    listChunks = []
    iTotalBytes = 0
    for baChunk in iterTarStream:
        iTotalBytes += len(baChunk)
        if iTotalBytes > iMaxBytes:
            raise ValueError(
                f"The archive of {sDirectoryPath} exceeds the "
                f"{iMaxBytes} byte ceiling; nothing was returned."
            )
        listChunks.append(baChunk)
    return b"".join(listChunks)


def _ftExchangeWithExecSocket(socketExec, baStdin, fileStdin=None):
    """Send a payload on a hijacked exec socket; return its output.

    The payload is ``baStdin``, or the rest of ``fileStdin`` read in
    chunks. Half-closes the write side after the payload so the program
    sees EOF, and splits the multiplexed stream into ``(baStdout,
    baStderr)``.

    The output is read WHILE the payload is sent, not after: a program
    that stops early (a full disk, a refusal) makes the daemon close the
    connection with the host still sending, which on Linux is a reset
    that fails the host's next ``send`` and its next ``recv``. Reading
    afterwards lost the reason the program had written to its standard
    error. A peer that closed early is not an error here: its exit
    code, read by the caller, is the answer, so the reset is swallowed
    on both sides.
    """
    import socket
    import threading
    from docker.utils.socket import STDERR, frames_iter
    socketRaw = getattr(socketExec, "_sock", socketExec)
    baStdout = bytearray()
    baStderr = bytearray()

    def _fnCollectOutput():
        try:
            for iStream, baChunk in frames_iter(socketExec, tty=False):
                (baStderr if iStream == STDERR else baStdout).extend(baChunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    threadReader = threading.Thread(target=_fnCollectOutput, daemon=True)
    threadReader.start()
    try:
        if fileStdin is None:
            socketRaw.sendall(baStdin)
        else:
            for baChunk in iter(
                lambda: fileStdin.read(_I_STDIN_CHUNK_BYTES), b"",
            ):
                socketRaw.sendall(baChunk)
        socketRaw.shutdown(socket.SHUT_WR)
    except (BrokenPipeError, ConnectionResetError):
        pass
    threadReader.join()
    return bytes(baStdout), bytes(baStderr)


def _finfoClearOwnership(infoTar):
    """Tarfile filter: drop the host's uid, gid and login names."""
    infoTar.uid = 0
    infoTar.gid = 0
    infoTar.uname = ""
    infoTar.gname = ""
    return infoTar


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
