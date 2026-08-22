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
import json
import shlex
import warnings
from dataclasses import dataclass
from typing import Optional

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

# Where ``fnWriteTreeViaTar`` stops holding the archive in memory and
# starts spilling to disk. A tree copy carries a researcher's whole
# directory, whose size nothing bounds, so the archive is spooled; the
# threshold only decides where the bytes live, never how many are
# allowed.
_I_TREE_TAR_SPOOL_BYTES = 32 * 1024 * 1024


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
    """Set DOCKER_HOST from active Docker context if not already set."""
    import os
    import subprocess
    if os.environ.get("DOCKER_HOST"):
        return
    try:
        processResult = subprocess.run(
            ["docker", "context", "inspect", "--format",
             "{{.Endpoints.docker.Host}}"],
            capture_output=True, text=True,
        )
        sHost = processResult.stdout.strip()
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
S_TYPED_READ_FILESYSTEM_USAGE = "filesystemUsage"
S_TYPED_READ_FILE_EXISTS = "fileExists"
S_TYPED_READ_DIRECTORY_EXISTS = "directoryExists"
S_TYPED_READ_PATHS_EXIST = "pathsExist"
S_TYPED_READ_DIRECTORIES_EXIST = "directoriesExist"
S_TYPED_READ_PATH_MTIMES = "pathMtimes"
S_TYPED_READ_FILE_SHA256 = "fileSha256"
S_TYPED_READ_GIT_REPO_STATUS = "gitRepoStatus"
S_TYPED_READ_GIT_WORKTREE_IDENTITIES = "gitWorktreeIdentities"
S_TYPED_READ_REPOSITORY_WEIGHT = "repositoryWeight"
# The probe stops counting past this many files. Comfortably above the
# council's own 20,000-member bound, so a repository that the snapshot
# would accept is always counted exactly; only one that is already
# refused gets a truncated answer, and "too many to count" is the same
# verdict as "too many".
I_MAX_REPOSITORY_WEIGHT_PROBE_FILES = 50000
S_TYPED_READ_CREDENTIAL_FILE = "credentialFileBase64"

# A provider login document is kilobytes. The council's credential read
# bounds itself IN the container at this ceiling rather than inheriting
# the 64 MB general-file cap, which can only reject after the bytes
# have already crossed the socket and been decoded.
I_MAX_CREDENTIAL_FILE_BYTES = 256 * 1024

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
    S_TYPED_READ_CREDENTIAL_FILE: (
        "import base64,sys\n"
        "with open(" + _S_TYPED_READ_PATH_SLOT + ",'rb') as fileIn:\n"
        "    baHead=fileIn.read(" + str(I_MAX_CREDENTIAL_FILE_BYTES + 1)
        + ")\n"
        "sys.stdout.buffer.write(base64.b64encode(baHead))\n"
    ),
    S_TYPED_READ_DIRECTORY: (
        "import os,sys; "
        "sys.stdout.write(chr(10).join(sorted(os.listdir("
        + _S_TYPED_READ_PATH_SLOT + "))))"
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
    # slow thing it exists to prevent.
    S_TYPED_READ_REPOSITORY_WEIGHT: (
        "import json,os,sys; "
        "root=" + _S_TYPED_READ_PATH_SLOT + "; "
        "cap=" + str(I_MAX_REPOSITORY_WEIGHT_PROBE_FILES) + "; "
        "n=0; b=0; truncated=False\n"
        "for dirpath,dirnames,filenames in os.walk(root):\n"
        "    for name in filenames:\n"
        "        try: b+=os.lstat(os.path.join(dirpath,name)).st_size\n"
        "        except OSError: pass\n"
        "        n+=1\n"
        "    if n>cap:\n"
        "        truncated=True; break\n"
        "sys.stdout.write(json.dumps({"
        "'iFileCount': n, 'iTotalBytes': b, 'bTruncated': truncated}))"
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
    # The council snapshot's coherence observation (remediation R5):
    # the type and content identity of EVERY present worktree path —
    # all tracked paths plus untracked-not-ignored — read immediately
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
        return _flistInterpretBooleanBatch(
            self._ftRunTypedRead(
                sContainerId, S_TYPED_READ_PATHS_EXIST, list(listPaths),
            ),
            listPaths, "existence",
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

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
        """Return the changed-path identity observation for one repo.

        The council snapshot's coherence read (remediation R5): the
        declared ``gitWorktreeIdentities`` program enumerates every
        changed worktree path and computes each one's content identity
        in the container, over the raw bytes. The command is not built
        here — this names a declared read operation and
        :meth:`_ftRunTypedRead` builds it, so the repository path
        cannot become program or shell syntax. Returns the program's
        ``{"bSuccess", "sReason", "dictPathIdentities"}`` answer;
        callers must treat ``bSuccess`` False as a refusal to observe,
        never as a quiet repository. A failed READ raises ``OSError``,
        and so does an unparseable answer.
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

    def fnWriteTreeViaTar(
        self, sContainerId, sDestinationDirectory, listHostPaths,
        iUid=None, iGid=None,
    ):
        """Copy host files and directories into a container directory.

        The bulk sibling of :meth:`fnWriteFileViaTar`, and the only way
        host-side content reaches a workspace volume: until this
        existed, a container was populated exclusively by the
        entrypoint's git clones, so a researcher converting a local
        directory to a container got an empty workspace and no
        indication anything was missing (2026-08-21).

        One archive, one round trip, whatever the tree's shape --
        ``tarfile`` walks a directory natively. The destination
        directory must already exist; ``put_archive`` does not create
        it. Symlinks are archived AS symlinks (tarfile's default), so
        a link pointing outside the project copies the link and never
        the host bytes it names.

        Ownership is stamped, never inherited. ``tar.add`` would carry
        the HOST's uid/gid onto every entry, which lands the files
        foreign-owned inside the container and silently unwritable by
        the in-container agent, which has no sudo by design -- the
        same defect ``_finfoBuildTarEntry`` exists to prevent for
        single writes.
        """
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteTreeViaTar",
        )
        fileTar = self._ffileBuildTreeTar(listHostPaths, iUid, iGid)
        try:
            container = self.fcontainerGetById(sContainerId)
            container.put_archive(sDestinationDirectory, fileTar)
        finally:
            fileTar.close()

    @staticmethod
    def _ffileBuildTreeTar(listHostPaths, iUid, iGid):
        """Return a rewound tar of the host paths, owned by the container user.

        Spooled rather than held in a ``BytesIO``: a researcher's
        directory is arbitrarily large, and the single-file path's
        in-memory buffer is only safe because its caller already holds
        the bytes.
        """
        import os
        import tarfile
        import tempfile
        ffnStampOwnership = DockerConnection._ffnBuildOwnershipFilter(
            iUid, iGid,
        )
        fileTar = tempfile.SpooledTemporaryFile(
            max_size=_I_TREE_TAR_SPOOL_BYTES,
        )
        with tarfile.open(fileobj=fileTar, mode="w") as fileArchive:
            for sHostPath in listHostPaths:
                fileArchive.add(
                    sHostPath, arcname=os.path.basename(sHostPath),
                    filter=ffnStampOwnership,
                )
        fileTar.seek(0)
        return fileTar

    @staticmethod
    def _ffnBuildOwnershipFilter(iUid, iGid):
        """Return a tarfile filter stamping the container user on every entry.

        The NAME fields are cleared alongside the numeric ids: tar
        readers that find a ``uname``/``gname`` resolve ownership by
        name in preference to the number, so leaving the host's login
        name on the entry would re-open the defect the numbers close.
        """
        iOwnerUid = iUid if iUid is not None else _I_CONTAINER_DEFAULT_UID
        iOwnerGid = iGid if iGid is not None else _I_CONTAINER_DEFAULT_GID

        def finfoStampOwnership(infoTar):
            infoTar.uid = iOwnerUid
            infoTar.gid = iOwnerGid
            infoTar.uname = ""
            infoTar.gname = ""
            return infoTar

        return finfoStampOwnership

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
