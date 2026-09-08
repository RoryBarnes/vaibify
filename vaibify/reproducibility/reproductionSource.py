"""Stage a PUBLISHED project as an exact, validated snapshot.

"Reproduce a published project" starts from something a stranger has:
a git URL, or a clone already on this machine. Everything downstream --
the image acquisition, the shadow rerun, the reproduction report --
consumes ONE staged snapshot, never the source it came from, because
the source can move between staging and running (a branch advances, a
working tree is edited) and a host path is invisible inside a
container with no mounts.

Three decisions shape this module, each recorded with its reason:

* **A reproduction is of a COMMIT.** A git URL is cloned in full (no
  ``--depth``: the history and the source-date epoch matter) and the
  commit it resolved to is recorded. A local clone is admitted only
  when ``git status`` reports nothing at all -- tracked, untracked and
  ignored alike -- because a dirty clone is not a published project,
  and it is materialized by cloning the LOCAL REPOSITORY rather than
  copying its working tree, so the staged bytes are the commit's bytes
  and nothing else.

* **Validation is strict, not advisory.** ``vaibify reproduce`` grades
  a researcher's own repository and warns; this lane grades somebody
  else's and refuses, naming the first rule that failed and the file
  that failed it. The six rules are the ones a rerun DEPENDS on -- a
  loadable workflow, a pinned image and platform, a manifest that
  parses, matches the staged bytes and covers the selected workflow's
  declarations, and a deposit that covers the pin if one is recorded.
  They are deliberately not the author's Level 3 gate: an attestation,
  a published mirror or a dependency lock are the AUTHOR's claims, and
  requiring them before a stranger may reproduce the work would put the
  claim ahead of the check. The verdict is "reproduction-ready", never
  "Level 3".

* **A report may only carry what :func:`fdictDescribeStagedSource`
  returns.** Kind, resolved commit, remote URL with any userinfo
  STRIPPED, workflow name. Never a host path, because a reproduction
  report may one day be deposited publicly under the reproducer's own
  name.

Host paths, ``os.path``. Subprocess only for ``git``, and every
invocation carries both hardening lists from ``gitHardening`` plus
``GIT_TERMINAL_PROMPT=0``, so no ambient credential helper can answer
and no prompt can hang the caller. The ONE deliberate exception is the
clone of a LOCAL repository: ``protocol.file.allow=never`` refuses
every file transport, and a local path IS the file transport (measured: a
plain path, a ``file://`` URL and a bundle are all
refused). That clone alone appends ``protocol.file.allow=always``
AFTER the hardening list, and only after the source path was admitted
under the researcher's home. It touches no submodule -- recursion is
off and the clone never asks for one -- so the hostile ``.gitmodules``
the setting defends against is never read.
"""

import contextlib
import fcntl
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from vaibify.gui import workflowMigrations
from vaibify.gui.workflowManager import (
    VAIBIFY_PROJECTS_DIR,
    VAIBIFY_WORKFLOWS_DIR,
    fnMigrateLegacyRemotes,
    fsDescribeValidationFailure,
)
from vaibify.reproducibility import imageArchive
from vaibify.reproducibility.credentialRedactor import (
    _TUPLE_QUERY_PARAM_NAMES,
    fsRedactCredentials,
    fsRedactUrlCredentials,
)
from vaibify.reproducibility.environmentSnapshot import (
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.gitHardening import (
    LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
    LIST_GIT_HARDENING_CONFIG,
)
from vaibify.reproducibility.l3Attestation import fsCurrentManifestDigest
from vaibify.reproducibility.manifestWriter import (
    flistDeclaredButMissingFromManifest,
    flistParseManifestLines,
    flistVerifyManifestEntries,
)
from vaibify.reproducibility.repoFiles import HostRepoFiles
from vaibify.reproducibility.shadowRerun import (
    ShadowRerunRefusedError,
    fsResolvePinnedImageReference,
)


logger = logging.getLogger(__name__)


__all__ = [
    "ReproductionSourceRefusedError",
    "WorkflowSelectionRequiredError",
    "F_STAGING_TTL_SECONDS",
    "I_STAGING_SIZE_CEILING_BYTES",
    "S_KIND_GIT_URL",
    "S_STAGE_PHASE_MATERIALIZING",
    "S_STAGE_PHASE_VALIDATING",
    "S_KIND_LOCAL_CLONE",
    "T_ACCEPTED_URL_SCHEMES",
    "fbaExportStagedSnapshot",
    "fcontextHoldStagedSource",
    "ffnHoldStagedSource",
    "fdictClassifySource",
    "fdictDescribeStagedSource",
    "fdictLoadStagedWorkflow",
    "fdictSelectWorkflowEntry",
    "fdictStageSource",
    "flistAdmittedLocalCloneRoots",
    "fnDiscardStagedSource",
    "flistSweepAbandonedStaging",
    "fsRequiredPlatformFromArchitecture",
    "fsStagedClonePath",
]


class ReproductionSourceRefusedError(Exception):
    """A source could not be staged as a reproduction-ready snapshot.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision silently
    downgrades into an I/O hiccup.
    """


class WorkflowSelectionRequiredError(ReproductionSourceRefusedError):
    """The snapshot hosts several workflows and none was named.

    A refusal like any other to the CLI, which prints it; a structured
    one to the dashboard, which reads ``listWorkflowNames`` off it and
    offers the choice instead of the sentence. The names are the
    declared workflow names, never paths on this host.
    """

    def __init__(self, sMessage, listWorkflowNames):
        super().__init__(sMessage)
        self.listWorkflowNames = list(listWorkflowNames)


# The two phases a stage passes through, reported to a caller that
# keeps a record. Spelled here because staging owns the sequence; the
# hub's job record maps them onto its own vocabulary.
S_STAGE_PHASE_MATERIALIZING = "staging"
S_STAGE_PHASE_VALIDATING = "validating"

S_KIND_GIT_URL = "git-url"
S_KIND_LOCAL_CLONE = "local-clone"

# The clone URL shapes a reproduction accepts. Matched by SHAPE, never
# by forge hostname: a published project lives wherever its authors put
# it. ``http`` is absent on purpose -- a clone over plain HTTP can be
# rewritten in flight -- and ``file``, ``git`` and ``ext`` are refused
# because each is a way to make git run something or read something on
# this host.
T_ACCEPTED_URL_SCHEMES = ("https", "ssh")

# Where staging lives. Reports (phase 2) live beside it, never inside
# it, because staging is scratch that is deleted after every run and a
# report is the reproducer's durable artefact.
_S_REPRODUCTIONS_DIRECTORY = os.path.expanduser("~/.vaibify/reproductions")
_S_STAGING_SUBDIRECTORY = "staging"
_S_SOURCE_RECORD_NAME = "source.json"
_S_LIVE_LOCK_NAME = "live.lock"
_S_EXPORT_SPOOL_NAME = "export.tar"
_I_PRIVATE_DIRECTORY_MODE = 0o700

# A clone larger than this is refused WHILE it grows, not after it has
# filled the disk. Two gibibytes holds any repository vaibify can run a
# workflow from; a project that needs more is a project whose data
# belongs in a declared remote, not in git.
I_STAGING_SIZE_CEILING_BYTES = 2 * 1024 * 1024 * 1024
_F_SIZE_POLL_INTERVAL_SECONDS = 0.25

# A staging directory nobody holds after this long is abandoned: a
# crashed hub, an interrupted CLI. A LIVE job holds its directory's
# lock, and a held lock is never swept whatever its age -- age is not
# evidence that a directory is garbage, reachability is.
F_STAGING_TTL_SECONDS = 24 * 60 * 60

_F_GIT_QUERY_TIMEOUT_SECONDS = 60.0
_F_GIT_CLONE_TIMEOUT_SECONDS = 30 * 60.0
_I_MAX_DIRTY_PATHS_NAMED = 10

# ``user@host:path``. No scheme, no ``://``, and the path never starts
# with ``/`` -- git treats ``host:/abs`` as scp-like too, but a local
# directory that happens to contain a colon is checked for first, so
# the shape can never claim a path on this host.
_REGEX_SCP_LIKE = re.compile(
    r"^(?:(?P<user>[A-Za-z0-9._\-]+)@)?"
    r"(?P<host>[A-Za-z0-9][A-Za-z0-9.\-]*):(?P<path>[^/\s][^\s]*)$"
)
_REGEX_BARE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")
_REGEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Docker's bare ``.Architecture`` (``amd64``, ``arm64``, ``arm/v7``, ...).
_REGEX_ARCHITECTURE = re.compile(r"^[a-z0-9]+(?:/v[0-9]+)?$")


# ---------------------------------------------------------------------
# 1a. Classify
# ---------------------------------------------------------------------


def fdictClassifySource(sInput):
    """Return ``{"sKind", "sSource"}`` for an accepted source, or refuse.

    ``sSource`` is the value the materializer will hand to git: the
    realpath of a local clone, or the URL exactly as given (a report
    receives it only after :func:`_fsStripUserinfo`). Refuses with the
    accepted shapes named, so a researcher who typed something else
    learns what would have worked.
    """
    sTrimmed = (sInput or "").strip()
    if not sTrimmed:
        raise ReproductionSourceRefusedError(_fsAcceptedShapes("nothing"))
    if "::" in sTrimmed:
        raise ReproductionSourceRefusedError(
            "a remote helper address (ext::, fd::) is refused; "
            + _fsAcceptedShapes(sTrimmed)
        )
    if os.path.isdir(sTrimmed):
        return {
            "sKind": S_KIND_LOCAL_CLONE,
            "sSource": _fsAdmitLocalPath(sTrimmed, "the local clone"),
        }
    if "://" in sTrimmed:
        return {"sKind": S_KIND_GIT_URL, "sSource": _fsAdmitUrl(sTrimmed)}
    matchScp = _REGEX_SCP_LIKE.match(sTrimmed)
    if matchScp:
        return {"sKind": S_KIND_GIT_URL, "sSource": sTrimmed}
    raise ReproductionSourceRefusedError(_fsAcceptedShapes(sTrimmed))


def _fsAcceptedShapes(sInput):
    """Return the refusal naming every shape a source may take."""
    return (
        f"{sInput!r} is not a source vaibify can reproduce from. "
        "Accepted: an https:// or ssh:// clone URL without a username "
        "or password in it, a user@host:path clone address, or the "
        "path of a clean git clone under your home directory. "
        "file://, git://, ext:: and plain http:// are refused."
    )


def _fsAdmitUrl(sUrl):
    """Return the URL when its scheme and userinfo are acceptable."""
    tParts = urlsplit(sUrl)
    sScheme = (tParts.scheme or "").lower()
    if sScheme not in T_ACCEPTED_URL_SCHEMES:
        raise ReproductionSourceRefusedError(
            f"the {sScheme or 'schemeless'}:// transport is refused; "
            + _fsAcceptedShapes(sUrl)
        )
    if not tParts.hostname:
        raise ReproductionSourceRefusedError(
            f"{sUrl!r} names no host; " + _fsAcceptedShapes(sUrl)
        )
    if tParts.password is not None or (
        sScheme == "https" and tParts.username is not None
    ):
        raise ReproductionSourceRefusedError(
            "the clone URL carries a username or password. Credentials "
            "in a URL end up in shell history and in reports; use a "
            "credential-free URL and let git ask its own helper. "
            + _fsAcceptedShapes(sUrl)
        )
    _fnRefuseCredentialQueryParameters(sUrl, tParts.query)
    return sUrl


def _fnRefuseCredentialQueryParameters(sUrl, sQuery):
    """Refuse a URL whose QUERY carries a credential.

    Userinfo is not the only place a token rides. A forge that accepts
    ``?access_token=...`` puts the secret in the same string a report
    records, and stripping userinfo alone left it there (found by
    review, 2026-09-07). The parameter names are the redactor's own
    tuple rather than a second list, so the refusal and the scrub can
    never disagree about what counts as a credential.
    """
    for sPair in (sQuery or "").split("&"):
        sName = sPair.split("=", 1)[0].strip().lower()
        if sName in _TUPLE_QUERY_PARAM_NAMES:
            # The refused URL is never echoed: it holds the secret
            # this refusal is about, and a message is printed, logged
            # and read over shoulders.
            raise ReproductionSourceRefusedError(
                f"the clone URL carries a {sName!r} query parameter, "
                "which is a credential. It would be recorded in the "
                "reproduction report and copied into the container; "
                "use a credential-free URL and let git ask its own "
                "helper. " + _fsAcceptedShapes(
                    fsRedactUrlCredentials(sUrl),
                )
            )


def _fsStripUserinfo(sUrl):
    """Return the URL with any ``user[:password]@`` removed.

    The only spelling of a remote a report may carry. Applied to every
    recorded URL, whether or not the classifier admitted userinfo on
    it, so the rule holds by construction rather than by branch.
    """
    if "://" in sUrl:
        tParts = urlsplit(sUrl)
        sHost = tParts.hostname or ""
        if tParts.port:
            sHost = f"{sHost}:{tParts.port}"
        # The query is scrubbed through the redactor as well as the
        # userinfo: the classifier refuses a credential parameter, and
        # this is the second line that holds for a URL reaching here by
        # any other path (a recorded origin, a future caller).
        return fsRedactUrlCredentials(urlunsplit(
            (tParts.scheme, sHost, tParts.path, tParts.query, ""),
        ))
    matchScp = _REGEX_SCP_LIKE.match(sUrl)
    if matchScp:
        return f"{matchScp.group('host')}:{matchScp.group('path')}"
    return sUrl


def _fnScrubStagedGitMetadata(sClonePath, sRemoteUrl):
    """Remove every record of WHERE the staged clone came from.

    ``git clone`` writes the source it was given into TWO places, and
    fixing only the obvious one leaves the leak: ``.git/config`` holds
    it as ``remote.origin.url``, and ``.git/logs/HEAD`` holds it in the
    reflog line ``clone: from <source>``. A local clone's source is an
    absolute host path and a URL clone's is the URL as typed; the
    staged tree, ``.git`` included, is copied into a container built
    from somebody else's image, so either would put the reproducer's
    filesystem layout inside an untrusted runtime (found by review
    2026-09-07; the reflog half was found by the test written for the
    first half, which is why the guard asserts over every archive
    member rather than over one file).

    The remote is rewritten to the same redacted string a report may
    carry, and removed outright when there is none. The reflog is
    deleted: it is local bookkeeping about one machine's fetches, no
    part of any commit, and nothing downstream reads it.
    """
    _fnRemoveTreeQuietly(os.path.join(sClonePath, ".git", "logs"))
    if sRemoteUrl:
        _fsGitQueryOrRefuse(
            ["remote", "set-url", "origin", sRemoteUrl], sClonePath,
            "rewriting the staged clone's origin",
        )
        return
    processGit = _fprocessRunGit(["remote", "remove", "origin"], sCwd=sClonePath)
    if processGit.returncode not in (0, 2, 128):
        raise ReproductionSourceRefusedError(
            "the staged clone's origin could not be removed, so it "
            "would carry the source's location into the container: "
            + fsRedactCredentials((processGit.stderr or "").strip())
        )


def _fnRemoveTreeQuietly(sPath):
    """Delete a directory tree, tolerating its absence."""
    shutil.rmtree(sPath, ignore_errors=True)


def flistAdmittedLocalCloneRoots():
    """Return the directories a local clone may live under.

    The researcher's home directory: the same root the host-mode
    registration and the dashboard's project browser operate within.
    A clone elsewhere is refused rather than read, because "a path on
    this machine" is the one input shape that can name anything.
    """
    return [os.path.realpath(os.path.expanduser("~"))]


def _fsAdmitLocalPath(sPath, sWhat):
    """Return the realpath of ``sPath`` when it lies under an admitted root."""
    sRealPath = os.path.realpath(sPath)
    for sRoot in flistAdmittedLocalCloneRoots():
        if sRealPath == sRoot or sRealPath.startswith(sRoot + os.sep):
            return sRealPath
    raise ReproductionSourceRefusedError(
        f"{sWhat} at {sPath!r} lies outside the directories a "
        "reproduction may read (your home directory). Clone it under "
        "your home directory, or give its URL instead."
    )


# ---------------------------------------------------------------------
# Staging directories and the live lock
# ---------------------------------------------------------------------


def _fsStagingRoot():
    """Return the staging root, resolved at call time so tests redirect it."""
    return os.path.join(_S_REPRODUCTIONS_DIRECTORY, _S_STAGING_SUBDIRECTORY)


def _fsStagingDirectory(sToken):
    """Return one staged snapshot's directory, refusing a non-bare token."""
    if not _REGEX_BARE_NAME.match(sToken or ""):
        raise ReproductionSourceRefusedError(
            f"staging token {sToken!r} is not a bare name"
        )
    return os.path.join(_fsStagingRoot(), sToken)


def fsStagedClonePath(sToken):
    """Return the host path of the staged clone for ``sToken``.

    For the machinery that runs the snapshot; NEVER for a report,
    which reads :func:`fdictDescribeStagedSource` instead.
    """
    dictRecord = _fdictReadSourceRecord(sToken)
    return os.path.join(
        _fsStagingDirectory(sToken), dictRecord["sRepositoryName"],
    )


def _fnEnsurePrivateDirectory(sPath):
    """Create ``sPath`` and any missing ancestor at mode 0700."""
    if os.path.isdir(sPath):
        return
    sParent = os.path.dirname(sPath)
    if sParent and sParent != sPath:
        _fnEnsurePrivateDirectory(sParent)
    os.makedirs(sPath, mode=_I_PRIVATE_DIRECTORY_MODE, exist_ok=True)
    os.chmod(sPath, _I_PRIVATE_DIRECTORY_MODE)


def _fsCreateStagingDirectory():
    """Create a fresh private staging directory and return it."""
    _fnEnsurePrivateDirectory(_fsStagingRoot())
    return tempfile.mkdtemp(prefix="snapshot", dir=_fsStagingRoot())


def _ffnHoldLiveLock(sStagingDirectory):
    """Take the directory's live lock; return the function that releases it.

    Refuses when another holder has it. Closing the file releases the
    lock, so the returned function is the file's own ``close``.
    """
    sLockPath = os.path.join(sStagingDirectory, _S_LIVE_LOCK_NAME)
    fileLock = open(sLockPath, "a+")
    try:
        fcntl.flock(fileLock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        fileLock.close()
        raise ReproductionSourceRefusedError(
            f"staged snapshot {os.path.basename(sStagingDirectory)!r} "
            "is held by another job"
        ) from error
    return fileLock.close


@contextlib.contextmanager
def _fcontextHoldLiveLock(sStagingDirectory):
    """Hold the directory's live lock for a ``with`` block."""
    fnRelease = _ffnHoldLiveLock(sStagingDirectory)
    try:
        yield
    finally:
        fnRelease()


def ffnHoldStagedSource(sToken):
    """Hold a staged snapshot outside a ``with`` block; return the releaser.

    For a holder whose life is not a code block: the dashboard's
    reproduction job takes the hold in the request that staged the
    snapshot and releases it from the task that settles the job. Same
    lock, same sweep protection as :func:`fcontextHoldStagedSource`.
    """
    return _ffnHoldLiveLock(_fsStagingDirectory(sToken))


@contextlib.contextmanager
def fcontextHoldStagedSource(sToken):
    """Hold a staged snapshot for the life of a job.

    The sweep skips a held directory whatever its age, so a job that
    runs past the TTL keeps its snapshot; a job that crashes releases
    the lock with its process, and the sweep reclaims the directory
    once it is old enough.
    """
    with _fcontextHoldLiveLock(_fsStagingDirectory(sToken)):
        yield


def fnDiscardStagedSource(sToken):
    """Delete a staged snapshot; a missing one is already discarded."""
    shutil.rmtree(_fsStagingDirectory(sToken), ignore_errors=True)


def flistSweepAbandonedStaging(fMaxAgeSeconds=F_STAGING_TTL_SECONDS):
    """Delete staging directories older than the TTL that no job holds.

    Returns the tokens swept. A directory whose live lock cannot be
    taken is skipped, never deleted: the lock is the job's proof of
    life, and the ``~/.vaibify/tmp`` sweep that once destroyed a
    mounted credential file is why age alone decides nothing here.
    """
    sRoot = _fsStagingRoot()
    if not os.path.isdir(sRoot):
        return []
    fCutoff = time.time() - fMaxAgeSeconds
    listSwept = []
    for sToken in sorted(os.listdir(sRoot)):
        sDirectory = os.path.join(sRoot, sToken)
        if not os.path.isdir(sDirectory):
            continue
        if os.path.getmtime(sDirectory) > fCutoff:
            continue
        try:
            with _fcontextHoldLiveLock(sDirectory):
                shutil.rmtree(sDirectory, ignore_errors=True)
        except ReproductionSourceRefusedError:
            continue
        listSwept.append(sToken)
    return listSwept


# ---------------------------------------------------------------------
# git
# ---------------------------------------------------------------------


def _fdictGitEnvironment():
    """Return the environment every git call runs under: no prompts.

    ``GIT_TERMINAL_PROMPT=0`` silences git's own credential prompt and
    nothing else: ssh asks for a passphrase or a host-key confirmation
    through ``/dev/tty`` on its own, so the ssh transport is put in
    batch mode too, as the FIRST option so nothing inherited outranks it. An unknown host or a locked key then fails the
    clone, which the caller reports, instead of hanging an unattended
    run on a question nobody will answer. A researcher's own
    ``GIT_SSH_COMMAND`` is kept and the option appended to it.
    """
    dictEnvironment = os.environ.copy()
    dictEnvironment["GIT_TERMINAL_PROMPT"] = "0"
    dictEnvironment["GIT_SSH_COMMAND"] = _fsBatchModeSshCommand(
        dictEnvironment.get("GIT_SSH_COMMAND"),
    )
    return dictEnvironment


def _fsBatchModeSshCommand(sInherited):
    """Return the ssh command with batch mode as its FIRST option.

    OpenSSH keeps the first value it sees for an option, so an inherited
    ``-o BatchMode=no`` would beat one appended after it (measured with
    ``ssh -G``: ``-o BatchMode=no -o BatchMode=yes`` resolves to no). The
    enforced option therefore goes immediately after the program word,
    ahead of anything the researcher's own command carries. A command
    that cannot be parsed as shell words is replaced outright rather
    than trusted.
    """
    try:
        listWords = shlex.split(sInherited or "")
    except ValueError:
        listWords = []
    if not listWords:
        listWords = ["ssh"]
    return shlex.join([listWords[0], "-o", "BatchMode=yes", *listWords[1:]])


def _fprocessRunGit(listArguments, sCwd=None):
    """Run one hardened git query; never raises.

    Both hardening lists on every call, in the order
    ``gitHardening`` prescribes (the credential reset first, because
    ``-c`` flags apply in order and a reset after an explicit helper
    would disable it -- there is no explicit helper here, and the
    order is kept so a future one composes correctly).
    """
    try:
        return subprocess.run(
            ["git", *LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
             *LIST_GIT_HARDENING_CONFIG, *listArguments],
            cwd=sCwd, env=_fdictGitEnvironment(),
            capture_output=True, text=True,
            timeout=_F_GIT_QUERY_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as error:
        return _fprocessSyntheticFailure(listArguments, 127, str(error))
    except subprocess.TimeoutExpired:
        return _fprocessSyntheticFailure(
            listArguments, 124, "git command timed out",
        )


def _fprocessSyntheticFailure(listArguments, iReturnCode, sStderr):
    """Return a CompletedProcess standing in for a git that never ran."""
    return subprocess.CompletedProcess(
        args=["git"] + list(listArguments), returncode=iReturnCode,
        stdout="", stderr=sStderr,
    )


def _fsGitQueryOrRefuse(listArguments, sCwd, sWhat):
    """Return a git query's stripped stdout, or refuse naming ``sWhat``."""
    processGit = _fprocessRunGit(listArguments, sCwd=sCwd)
    if processGit.returncode != 0:
        raise ReproductionSourceRefusedError(
            f"{sWhat}: git exited {processGit.returncode}: "
            + fsRedactCredentials((processGit.stderr or "").strip())
        )
    return (processGit.stdout or "").strip()


def _fnCloneBounded(listCloneArguments, sClonePath):
    """Run ``git clone`` and refuse while the clone outgrows the ceiling.

    The size is measured as the clone grows, not after it finished: a
    ceiling checked at the end has already let the disk fill. The
    process is killed on breach and the partial clone removed.
    """
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as fileStderr:
        try:
            processClone = subprocess.Popen(
                ["git", *LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
                 *LIST_GIT_HARDENING_CONFIG, *listCloneArguments],
                env=_fdictGitEnvironment(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=fileStderr,
            )
        except FileNotFoundError as error:
            raise ReproductionSourceRefusedError(
                "git is not installed on this machine, and a reproduction "
                "starts with a clone."
            ) from error
        sFailure = _fsWatchCloneUntilDone(processClone, sClonePath, fileStderr)
    if sFailure:
        shutil.rmtree(sClonePath, ignore_errors=True)
        raise ReproductionSourceRefusedError(sFailure)


def _fsWatchCloneUntilDone(processClone, sClonePath, fileStderr):
    """Poll the clone; return a refusal message, or empty on success.

    stderr goes to a temporary file rather than a pipe, so a chatty
    git can never fill a pipe nobody is draining and stall the poll.
    """
    fDeadline = time.monotonic() + _F_GIT_CLONE_TIMEOUT_SECONDS
    while processClone.poll() is None:
        sBreach = _fsDescribeSizeBreach(sClonePath)
        if sBreach:
            processClone.kill()
            processClone.wait()
            return sBreach
        if time.monotonic() > fDeadline:
            processClone.kill()
            processClone.wait()
            return "the clone did not finish within its time limit."
        time.sleep(_F_SIZE_POLL_INTERVAL_SECONDS)
    fileStderr.seek(0)
    sStderr = (fileStderr.read() or "").strip()
    if processClone.returncode != 0:
        return (
            f"git clone exited {processClone.returncode}: "
            + fsRedactCredentials(sStderr)
        )
    # A clone that finished between two polls is measured once more:
    # the ceiling is a property of the result, not only of the race.
    return _fsDescribeSizeBreach(sClonePath)


def _fsDescribeSizeBreach(sClonePath):
    """Return the refusal for a clone over the ceiling, or empty."""
    iBytes = _fiDirectoryBytes(sClonePath)
    if iBytes <= I_STAGING_SIZE_CEILING_BYTES:
        return ""
    return (
        f"the clone exceeded {I_STAGING_SIZE_CEILING_BYTES} bytes "
        f"({iBytes} bytes) and was stopped. A reproduction stages the "
        "repository alone; data this large belongs in a declared remote."
    )


def _fiDirectoryBytes(sDirectory):
    """Return the apparent size of every regular file under a directory."""
    iTotal = 0
    for sParent, _listDirectories, listFiles in os.walk(sDirectory):
        for sName in listFiles:
            try:
                iTotal += os.lstat(os.path.join(sParent, sName)).st_size
            except OSError:
                continue
    return iTotal


# ---------------------------------------------------------------------
# 1b. Materialize exactly one commit
# ---------------------------------------------------------------------


def _fsRepositoryNameFromSource(sSource):
    """Return a bare directory name derived from the source's last segment."""
    sLast = sSource.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if sLast.endswith(".git"):
        sLast = sLast[:-len(".git")]
    sName = re.sub(r"[^A-Za-z0-9._\-]", "-", sLast).strip("-.")
    return sName or "project"


def _fdictMaterializeGitUrl(sSource, sStagingDirectory):
    """Clone a URL in full and record the commit it resolved to."""
    sRepositoryName = _fsRepositoryNameFromSource(sSource)
    sClonePath = os.path.join(sStagingDirectory, sRepositoryName)
    _fnCloneBounded(["clone", "--quiet", "--", sSource, sClonePath],
                    sClonePath)
    return {
        "sRepositoryName": sRepositoryName,
        "sResolvedCommit": _fsResolvedHead(sClonePath),
        "sRemoteUrl": _fsStripUserinfo(sSource),
    }


def _fdictMaterializeLocalClone(sSourcePath, sStagingDirectory):
    """Clone a clean local repository at its HEAD commit.

    A working-tree copy would carry whatever the tree holds; a clone
    carries the commit. The commit is checked out explicitly and read
    back afterwards, so a source whose HEAD is detached, or whose
    default branch differs from its checked-out one, still stages the
    commit the researcher was looking at.
    """
    _fnRefuseUnlessRepositoryRoot(sSourcePath)
    _fnRefuseUnlessClean(sSourcePath)
    sResolvedCommit = _fsResolvedHead(sSourcePath)
    sRemoteUrl = _fsAdmittedOriginUrl(sSourcePath)
    sRepositoryName = _fsRepositoryNameFromSource(sSourcePath)
    sClonePath = os.path.join(sStagingDirectory, sRepositoryName)
    _fnCloneBounded(
        ["-c", "protocol.file.allow=always", "clone", "--quiet",
         "--no-hardlinks", "--", sSourcePath, sClonePath],
        sClonePath,
    )
    _fsGitQueryOrRefuse(
        ["checkout", "--quiet", "--detach", sResolvedCommit], sClonePath,
        "checking out the staged commit",
    )
    sStagedCommit = _fsResolvedHead(sClonePath)
    if sStagedCommit != sResolvedCommit:
        raise ReproductionSourceRefusedError(
            f"the staged clone is at {sStagedCommit} but the source is "
            f"at {sResolvedCommit}; nothing was staged."
        )
    return {
        "sRepositoryName": sRepositoryName,
        "sResolvedCommit": sResolvedCommit,
        "sRemoteUrl": sRemoteUrl,
    }


def _fnRefuseUnlessRepositoryRoot(sSourcePath):
    """Refuse a directory that is not the root of a git work tree."""
    sTopLevel = _fsGitQueryOrRefuse(
        ["rev-parse", "--show-toplevel"], sSourcePath,
        f"{sSourcePath!r} is not inside a git repository",
    )
    if os.path.realpath(sTopLevel) != os.path.realpath(sSourcePath):
        raise ReproductionSourceRefusedError(
            f"{sSourcePath!r} is inside a repository whose root is "
            f"{sTopLevel!r}; point at the root."
        )


def _fnRefuseUnlessClean(sSourcePath):
    """Refuse a clone with any tracked, untracked or ignored change."""
    sStatus = _fsGitQueryOrRefuse(
        ["status", "--porcelain", "--untracked-files=all", "--ignored"],
        sSourcePath, "reading the clone's status",
    )
    listDirty = [sLine for sLine in sStatus.splitlines() if sLine.strip()]
    if not listDirty:
        return
    listNamed = listDirty[:_I_MAX_DIRTY_PATHS_NAMED]
    sMore = (
        f" and {len(listDirty) - len(listNamed)} more"
        if len(listDirty) > len(listNamed) else ""
    )
    raise ReproductionSourceRefusedError(
        f"{sSourcePath!r} is not a clean clone, so it is not a published "
        "project. git reports:\n  " + "\n  ".join(listNamed) + sMore
        + "\nCommit the changes, stash them (git stash "
        "--include-untracked), remove the ignored files (git clean "
        "-fdX), or give the published URL instead."
    )


def _fsResolvedHead(sRepositoryPath):
    """Return the full HEAD commit of a repository, or refuse."""
    sCommit = _fsGitQueryOrRefuse(
        ["rev-parse", "HEAD"], sRepositoryPath,
        f"{sRepositoryPath!r} has no commit to reproduce",
    )
    if not _REGEX_COMMIT.match(sCommit):
        raise ReproductionSourceRefusedError(
            f"git reported {sCommit!r} as HEAD, which is not a commit."
        )
    return sCommit


def _fsAdmittedOriginUrl(sSourcePath):
    """Return the clone's origin with userinfo stripped, or empty.

    An origin that is itself a local path is admitted under the same
    roots as the clone, because a report would otherwise carry a host
    path under the name of a remote.
    """
    processGit = _fprocessRunGit(
        ["remote", "get-url", "origin"], sCwd=sSourcePath,
    )
    if processGit.returncode != 0:
        return ""
    sOrigin = (processGit.stdout or "").strip()
    if not sOrigin:
        return ""
    if "://" in sOrigin and not sOrigin.lower().startswith("file://"):
        return _fsStripUserinfo(sOrigin)
    if _REGEX_SCP_LIKE.match(sOrigin) and not os.path.isdir(sOrigin):
        return _fsStripUserinfo(sOrigin)
    sLocal = sOrigin[len("file://"):] if "://" in sOrigin else sOrigin
    _fsAdmitLocalPath(sLocal, "the clone's origin")
    return ""


# ---------------------------------------------------------------------
# 1c. Select the workflow
# ---------------------------------------------------------------------


def _flistDiscoverWorkflowFiles(sClonePath):
    """Return ``{sPath, sName}`` for every Project file in the clone.

    Canonical directory first, then the legacy one -- the same two the
    reproduce CLI's own discovery reads -- with paths kept
    repo-relative so a report never learns where the clone sits.
    """
    listEntries = []
    for sDirectory in (VAIBIFY_PROJECTS_DIR, VAIBIFY_WORKFLOWS_DIR):
        sAbsoluteDirectory = os.path.join(sClonePath, sDirectory)
        if not os.path.isdir(sAbsoluteDirectory):
            continue
        for sName in sorted(os.listdir(sAbsoluteDirectory)):
            if not sName.endswith(".json"):
                continue
            sRelativePath = f"{sDirectory}/{sName}"
            listEntries.append({
                "sPath": sRelativePath,
                "sName": _fsWorkflowNameOrStem(
                    os.path.join(sClonePath, sDirectory, sName), sName,
                ),
            })
    return listEntries


def _fsWorkflowNameOrStem(sAbsolutePath, sFileName):
    """Return the declared workflow name, or the file's stem."""
    try:
        with open(sAbsolutePath, "r", encoding="utf-8") as fileHandle:
            dictWorkflow = json.load(fileHandle)
    except (OSError, ValueError):
        dictWorkflow = {}
    sDeclared = (
        dictWorkflow.get("sWorkflowName")
        if isinstance(dictWorkflow, dict) else None
    )
    return sDeclared or sFileName[:-len(".json")]


def fdictSelectWorkflowEntry(listWorkflows, sWorkflowName, sWhere):
    """Return the one discovered workflow to use, or raise ValueError.

    Ambiguity is refused rather than resolved by sort order: picking a
    workflow here would attest -- or report on -- an envelope the
    rerun did not produce. ``sWhere`` names the place searched ("the
    running container", "the staged snapshot") so the refusal reads
    correctly from both callers.
    """
    if not listWorkflows:
        raise ValueError(f"no vaibify workflow found in {sWhere}")
    if sWorkflowName:
        return _fdictMatchWorkflowByName(listWorkflows, sWorkflowName, sWhere)
    if len(listWorkflows) > 1:
        raise ValueError(
            f"{sWhere} hosts {len(listWorkflows)} workflows ("
            + ", ".join(sorted(
                dictEntry.get("sName", "") for dictEntry in listWorkflows
            ))
            + "); name the one to re-run with --workflow, because "
            "attesting a workflow other than the one that ran would "
            "certify a run that never happened"
        )
    return listWorkflows[0]


def _fdictMatchWorkflowByName(listWorkflows, sWorkflowName, sWhere):
    """Return the single discovered workflow matching a researcher's name."""
    listMatches = [
        dictEntry for dictEntry in listWorkflows
        if sWorkflowName in (
            dictEntry.get("sName", ""), dictEntry.get("sPath", ""),
        )
    ]
    if not listMatches:
        raise ValueError(
            f"no workflow named '{sWorkflowName}' in {sWhere}; "
            "--workflow accepts "
            + ", ".join(sorted(
                dictEntry.get("sName", "") for dictEntry in listWorkflows
            ))
        )
    if len(listMatches) > 1:
        raise ValueError(
            f"'{sWorkflowName}' matches more than one workflow in "
            f"{sWhere}; pass the full path to --workflow instead"
        )
    return listMatches[0]


# ---------------------------------------------------------------------
# 1d. Validate the selected workflow as reproduction-ready
# ---------------------------------------------------------------------


def _fdictLoadWorkflowStrictly(sClonePath, sWorkflowRelativePath):
    """Parse, migrate and validate one project.json, refusing any failure.

    The same migrations and the same validator the hub applies on
    load, minus the runtime state merge -- a published project carries
    no machine-local state, and reading one would write a state file
    into a snapshot whose every byte is about to be compared.
    """
    sAbsolutePath = os.path.join(sClonePath, sWorkflowRelativePath)
    try:
        with open(sAbsolutePath, "r", encoding="utf-8") as fileHandle:
            dictWorkflow = json.load(fileHandle)
    except (OSError, ValueError) as error:
        raise ReproductionSourceRefusedError(
            f"rule 1 (project file loads): {sWorkflowRelativePath} could "
            f"not be read as JSON: {error}"
        ) from error
    if not isinstance(dictWorkflow, dict):
        raise ReproductionSourceRefusedError(
            f"rule 1 (project file loads): {sWorkflowRelativePath} is "
            "not a JSON object"
        )
    try:
        workflowMigrations.fiApplyMigrations(dictWorkflow)
        fnMigrateLegacyRemotes(dictWorkflow)
        sFailure = fsDescribeValidationFailure(dictWorkflow)
    except (ValueError, KeyError, TypeError) as error:
        # A file written by a newer vaibify refuses its own migration
        # with a ValueError; that is a rule-1 refusal with a reason, not
        # a traceback the CLI cannot name.
        raise ReproductionSourceRefusedError(
            f"rule 1 (project file loads): {sWorkflowRelativePath}: {error}"
        ) from error
    if sFailure:
        raise ReproductionSourceRefusedError(
            f"rule 1 (project file validates): {sWorkflowRelativePath}: "
            f"{sFailure}"
        )
    workflowMigrations.fnEnsureStepIds(dictWorkflow)
    return dictWorkflow


def _fdictReadEnvelopeOrRefuse(filesRepo):
    """Return the envelope payload with a content-pinned image, or refuse."""
    dictEnvironment = fdictReadEnvironmentJson(filesRepo)
    if dictEnvironment is None:
        raise ReproductionSourceRefusedError(
            "rule 2 (environment envelope): .vaibify/environment.json is "
            "missing or is not a JSON object"
        )
    try:
        sPinned = fsResolvePinnedImageReference(dictEnvironment)
    except ShadowRerunRefusedError as error:
        raise ReproductionSourceRefusedError(
            f"rule 2 (environment envelope): {error}"
        ) from error
    dictEnvironment["_sPinnedImageReference"] = sPinned
    dictContainer = dictEnvironment.get("dictContainer") or {}
    sArchitecture = str(dictContainer.get("sArchitecture") or "").strip()
    if not _REGEX_ARCHITECTURE.match(sArchitecture):
        raise ReproductionSourceRefusedError(
            "rule 2 (environment envelope): .vaibify/environment.json "
            + ("records no image architecture" if not sArchitecture
               else f"records {sArchitecture!r} as the image architecture, "
               "which is not one Docker names")
            + ", so the pinned platform cannot be requested. The source "
            "names its environment: there is no architecture picker and "
            "no host-derived default. Regenerate the envelope while the "
            "container is running."
        )
    return dictEnvironment


def _flistParseManifestOrRefuse(filesRepo):
    """Return the parsed manifest entries, or refuse naming the defect."""
    try:
        return flistParseManifestLines(filesRepo)
    except FileNotFoundError as error:
        raise ReproductionSourceRefusedError(
            "rule 3 (manifest parses): MANIFEST.sha256 is missing"
        ) from error
    except ValueError as error:
        raise ReproductionSourceRefusedError(
            f"rule 3 (manifest parses): MANIFEST.sha256: {error}"
        ) from error


def _fnRefuseUnlessManifestMatches(filesRepo, listEntries):
    """Refuse when any manifest entry differs from the staged bytes."""
    listMismatches = flistVerifyManifestEntries(filesRepo, listEntries)
    if not listMismatches:
        return
    dictFirst = listMismatches[0]
    sActual = dictFirst["sActual"] or "missing"
    raise ReproductionSourceRefusedError(
        f"rule 4 (manifest matches the staged bytes): "
        f"{len(listMismatches)} of {len(listEntries)} entries differ; "
        f"first {dictFirst['sPath']} (recorded "
        f"{dictFirst['sExpected'][:12]}..., staged {sActual[:12]}...)"
    )


def _fnRefuseUnlessManifestComplete(filesRepo, dictWorkflow):
    """Refuse when the workflow declares a file the manifest omits."""
    listMissing = flistDeclaredButMissingFromManifest(filesRepo, dictWorkflow)
    if listMissing:
        raise ReproductionSourceRefusedError(
            f"rule 5 (manifest complete): {len(listMissing)} declared "
            f"path(s) are not pinned; first {listMissing[0]}"
        )


def _fdictDepositFacts(dictEnvironment):
    """Return what the envelope says about an archived image.

    A deposit on record must cover the pinned image and platform; an
    absent deposit is recorded, not refused, because the registry link
    may still serve the image.
    """
    dictRecord = imageArchive.fdictReadArchiveRecord(dictEnvironment)
    if dictRecord is None:
        return {"bDepositOnRecord": False, "sDepositVersionDoi": ""}
    try:
        listReasons = imageArchive.flistDescribeArchiveMismatch(
            dictEnvironment,
        )
    except LookupError as error:
        raise ReproductionSourceRefusedError(
            f"rule 6 (deposit covers the envelope): {error}"
        ) from error
    if listReasons:
        raise ReproductionSourceRefusedError(
            "rule 6 (deposit covers the envelope): " + " ".join(listReasons)
        )
    return {
        "bDepositOnRecord": True,
        "sDepositVersionDoi": str(dictRecord.get("sVersionDoi") or ""),
    }


def fsRequiredPlatformFromArchitecture(sArchitecture):
    """Return ``linux/<arch>`` for an envelope architecture, or empty.

    The envelope records Docker's bare ``.Architecture``; every vaibify
    image is a Linux image, so the OS half is fixed. Empty in, empty
    out: an absent architecture is announced by the caller, never
    quietly defaulted to the host's.
    """
    sBare = (sArchitecture or "").strip().lower()
    if not sBare:
        return ""
    if sBare.startswith("linux/"):
        return sBare
    return f"linux/{sBare}"


def _fdictValidateStagedProject(sClonePath, sWorkflowRelativePath):
    """Apply rules 1-6 in order and return the facts a rerun would use."""
    dictWorkflow = _fdictLoadWorkflowStrictly(sClonePath, sWorkflowRelativePath)
    filesRepo = HostRepoFiles(sClonePath)
    dictEnvironment = _fdictReadEnvelopeOrRefuse(filesRepo)
    listEntries = _flistParseManifestOrRefuse(filesRepo)
    _fnRefuseUnlessManifestMatches(filesRepo, listEntries)
    _fnRefuseUnlessManifestComplete(filesRepo, dictWorkflow)
    dictDeposit = _fdictDepositFacts(dictEnvironment)
    dictContainer = dictEnvironment.get("dictContainer") or {}
    sArchitecture = str(dictContainer.get("sArchitecture") or "")
    return {
        "sPinnedImageReference": dictEnvironment["_sPinnedImageReference"],
        "sRequiredArchitecture": sArchitecture,
        "sRequiredPlatform": fsRequiredPlatformFromArchitecture(sArchitecture),
        "sManifestDigest": fsCurrentManifestDigest(filesRepo),
        "iManifestEntries": len(listEntries),
        "iStepCount": len(dictWorkflow.get("listSteps") or []),
        **dictDeposit,
    }


# ---------------------------------------------------------------------
# The staging entry point, and what a report may read
# ---------------------------------------------------------------------


def fdictStageSource(sInput, sWorkflowName=None, fnStatusCallback=None):
    """Stage ``sInput`` as a validated snapshot; return its description.

    Runs the whole of phase 1 -- classify, materialize one commit,
    select the workflow, validate it as reproduction-ready -- and
    writes the redacted source record the later phases read. On any
    refusal the staging directory is removed and the refusal
    re-raised, so a failed stage leaves nothing behind. The returned
    dict is :func:`fdictDescribeStagedSource`'s answer plus
    ``sToken``.
    """
    dictClassified = fdictClassifySource(sInput)
    flistSweepAbandonedStaging()
    fnStatusCallback = fnStatusCallback or (lambda sPhase: None)
    sStagingDirectory = _fsCreateStagingDirectory()
    sToken = os.path.basename(sStagingDirectory)
    try:
        with _fcontextHoldLiveLock(sStagingDirectory):
            dictRecord = _fdictStageIntoDirectory(
                dictClassified, sStagingDirectory, sWorkflowName,
                fnStatusCallback,
            )
            _fnWriteSourceRecord(sStagingDirectory, dictRecord)
    except BaseException:
        shutil.rmtree(sStagingDirectory, ignore_errors=True)
        raise
    return {"sToken": sToken, **dictRecord}


def _fdictStageIntoDirectory(
    dictClassified, sStagingDirectory, sWorkflowName, fnStatusCallback=None,
):
    """Materialize, select and validate inside a held staging directory."""
    if dictClassified["sKind"] == S_KIND_LOCAL_CLONE:
        dictMaterialized = _fdictMaterializeLocalClone(
            dictClassified["sSource"], sStagingDirectory,
        )
    else:
        dictMaterialized = _fdictMaterializeGitUrl(
            dictClassified["sSource"], sStagingDirectory,
        )
    sClonePath = os.path.join(
        sStagingDirectory, dictMaterialized["sRepositoryName"],
    )
    _fnScrubStagedGitMetadata(sClonePath, dictMaterialized["sRemoteUrl"])
    if fnStatusCallback is not None:
        # The clone is on disk; what follows is the six rules. A job
        # record that says "validating" is the difference between a
        # researcher watching a long clone and one watching a hang.
        fnStatusCallback(S_STAGE_PHASE_VALIDATING)
    listWorkflows = _flistDiscoverWorkflowFiles(sClonePath)
    try:
        dictEntry = fdictSelectWorkflowEntry(
            listWorkflows, sWorkflowName, "the staged snapshot",
        )
    except ValueError as error:
        if not sWorkflowName and len(listWorkflows) > 1:
            raise WorkflowSelectionRequiredError(
                str(error),
                [dictOne.get("sName", "") for dictOne in listWorkflows],
            ) from error
        raise ReproductionSourceRefusedError(str(error)) from error
    dictFacts = _fdictValidateStagedProject(sClonePath, dictEntry["sPath"])
    return {
        "sKind": dictClassified["sKind"],
        "sRepositoryName": dictMaterialized["sRepositoryName"],
        "sResolvedCommit": dictMaterialized["sResolvedCommit"],
        "sRemoteUrl": dictMaterialized["sRemoteUrl"],
        "sWorkflowName": dictEntry["sName"],
        "sWorkflowPath": dictEntry["sPath"],
        "sStagedAtIso": datetime.now(timezone.utc).isoformat(),
        **dictFacts,
    }


def _fnWriteSourceRecord(sStagingDirectory, dictRecord):
    """Persist the redacted source record beside the clone."""
    sRecordPath = os.path.join(sStagingDirectory, _S_SOURCE_RECORD_NAME)
    with open(sRecordPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictRecord, fileHandle, indent=2, sort_keys=True)
        fileHandle.write("\n")


def _fdictReadSourceRecord(sToken):
    """Return a staged snapshot's record, or refuse an unknown token."""
    sRecordPath = os.path.join(
        _fsStagingDirectory(sToken), _S_SOURCE_RECORD_NAME,
    )
    try:
        with open(sRecordPath, "r", encoding="utf-8") as fileHandle:
            return json.load(fileHandle)
    except (OSError, ValueError) as error:
        raise ReproductionSourceRefusedError(
            f"no staged snapshot is recorded under token {sToken!r}"
        ) from error


def fdictLoadStagedWorkflow(sToken):
    """Return the staged snapshot's selected workflow, loaded strictly.

    The same loader staging validated it through, so a rerun runs the
    workflow the six rules passed and no other, with step labels
    attached the way the hub attaches them on load.
    """
    from vaibify.gui.pipelineUtils import fnAttachStepLabels
    dictRecord = _fdictReadSourceRecord(sToken)
    dictWorkflow = _fdictLoadWorkflowStrictly(
        os.path.join(_fsStagingDirectory(sToken), dictRecord["sRepositoryName"]),
        dictRecord["sWorkflowPath"],
    )
    fnAttachStepLabels(dictWorkflow)
    return dictWorkflow


def fdictDescribeStagedSource(sToken):
    """Return what a report may carry about a staged source.

    The ONLY function a report reads source facts from. Every value
    was written by :func:`fdictStageSource` after redaction -- the
    remote URL with userinfo stripped, paths relative to the
    repository -- and the staging directory itself is not among them.
    """
    return dict(_fdictReadSourceRecord(sToken))


# ---------------------------------------------------------------------
# 1e. Export
# ---------------------------------------------------------------------


def fbaExportStagedSnapshot(sToken, iMaxBytes=None):
    """Return the staged clone as tar bytes named ``<repository>/...``.

    The shape ``container.get_archive`` produces and
    ``disposableSpecification.fbufferRepackArchiveStamped`` accepts:
    every member relative to the parent of the exported directory.
    Every later step consumes THIS archive -- never a re-clone, and
    never the source -- so a branch that moved after staging, or a
    working tree edited since, changes nothing about what runs.

    ``iMaxBytes`` is the SAME bound the live shadow lane applies to its
    own export (``daemonCapacity``'s ``iArchiveTotalBytes``), and both
    production callers pass it, because the staging ceiling bounds what
    a clone may occupy on DISK and says nothing about what the hub may
    materialise in its own address space. It is checked before the
    archive is built and again on the built archive, so neither a large
    tree nor a tree that grew during the walk can be handed on. The
    default is the staging ceiling, for a caller with no daemon to ask.

    The archive is spooled to a private file inside the staging
    directory rather than assembled in memory. One in-memory copy
    remains at the end, and a second is made by the repack the copy-in
    performs; that residual is bounded by this check rather than
    removed, and removing it means changing the copy-in interface,
    which this change deliberately does not touch.
    """
    sClonePath = fsStagedClonePath(sToken)
    iBound = iMaxBytes or I_STAGING_SIZE_CEILING_BYTES
    _fnRefuseAnExportOverTheBound(_fiDirectoryBytes(sClonePath), iBound,
                                  "the staged clone occupies")
    sArchivePath = os.path.join(
        _fsStagingDirectory(sToken), _S_EXPORT_SPOOL_NAME,
    )
    try:
        _fnSpoolStagedArchive(sClonePath, sArchivePath)
        _fnRefuseAnExportOverTheBound(
            os.path.getsize(sArchivePath), iBound, "the staged archive is",
        )
        with open(sArchivePath, "rb") as fileArchive:
            return fileArchive.read()
    finally:
        _fnRemoveQuietly(sArchivePath)


def _fnRefuseAnExportOverTheBound(iBytes, iBound, sWhat):
    """Refuse to materialise an archive the hub cannot afford to hold."""
    if iBytes <= iBound:
        return
    raise ReproductionSourceRefusedError(
        f"{sWhat} {iBytes} bytes, over the {iBound} this host can "
        "copy into a container in one piece. A reproduction stages the "
        "repository alone; data this large belongs in a declared remote."
    )


def _fnSpoolStagedArchive(sClonePath, sArchivePath):
    """Write the staged clone to a private tar file, one member at a time."""
    sRepositoryName = os.path.basename(sClonePath)
    iDescriptor = os.open(
        sArchivePath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
    )
    with os.fdopen(iDescriptor, "wb") as fileArchive:
        with tarfile.open(fileobj=fileArchive, mode="w") as fileTar:
            fileTar.add(sClonePath, arcname=sRepositoryName, recursive=False)
            for sParent, listDirectories, listFiles in os.walk(sClonePath):
                listDirectories.sort()
                sRelativeParent = os.path.relpath(sParent, sClonePath)
                for sName in sorted(listDirectories) + sorted(listFiles):
                    sAbsolute = os.path.join(sParent, sName)
                    sMember = (
                        sName if sRelativeParent == os.curdir
                        else "/".join(sRelativeParent.split(os.sep) + [sName])
                    )
                    fileTar.add(
                        sAbsolute, arcname=f"{sRepositoryName}/{sMember}",
                        recursive=False,
                    )


def _fnRemoveQuietly(sPath):
    """Delete a file, tolerating its absence."""
    try:
        os.remove(sPath)
    except OSError:
        pass
