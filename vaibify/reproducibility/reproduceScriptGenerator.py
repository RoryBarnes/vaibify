"""Render ``reproduce.sh`` from a workflow + the L3 envelope.

The output script is the canonical "stock-host reproduction recipe":
given the project repo plus the PROOF L3 envelope
(``.vaibify/environment.json``, ``requirements.lock``,
``MANIFEST.sha256``), running ``./reproduce.sh`` on a fresh host
with Docker installed should reproduce every declared artefact.

The script lives at ``<projectRepo>/reproduce.sh`` so it is part of
the repo (and therefore pinned in ``MANIFEST.sha256``). The
``manifestWriter`` envelope already covers files at the repo root, so
no additional wiring is needed beyond regenerating the manifest after
``fsGenerateReproduceScript`` writes a new script body.

Note on host-vs-container paths: ``sProjectRepo`` here is the
container-side path (e.g. ``/workspace/foo``). The FastAPI process
runs on the host, so a naive ``Path.write_text`` would write to a
host file that happens to share the container's absolute path —
which silently leaks artefacts onto the host and never reaches the
repo inside the container. The writer therefore takes a docker
connection and routes the bytes through ``fnWriteFile`` /
``ftResultExecuteCommand``.
"""

import posixpath
import re

from vaibify.reproducibility import zenodoClient
from vaibify.reproducibility.imageArchive import (
    S_LOADED_FROM_ARCHIVE_MARKER,
)
from vaibify.reproducibility.repoFiles import fsShellQuotePosix
from vaibify.reproducibility.rerunAssessment import (
    S_RERUN_ASSESSMENT_REPO_PATH,
)


__all__ = [
    "S_REPRODUCE_SCRIPT_FILENAME",
    "S_REPRODUCTION_REPO_ROOT",
    "fsGenerateReproduceScript",
    "fsRenderReproduceScript",
    "flistRenderStepCommands",
    "ftRenderReproductionFiles",
]


S_REPRODUCE_SCRIPT_FILENAME = "reproduce.sh"

# The repo root AS THE REPRODUCER SEES IT. The generated script runs
# ``docker run -v "$PWD":/work -w /work``, so every step command must
# be substituted against this root, never against the authoring
# container's ``/workspace/<repo>``: that path names a directory the
# reproducer's container does not have. Absolute paths under this root
# are correct from inside any step directory the body ``cd``s into,
# which relative ones would not be. Changing this constant means
# changing the ``docker run`` mount in the preamble with it.
S_REPRODUCTION_REPO_ROOT = "/work"

# The reproduction body (step comments and commands) is delivered to
# the container's shell through a *quoted* heredoc, never through a
# host-side ``bash -c '...'`` argument. A quoted heredoc passes its
# body to the container verbatim: the host shell performs no
# expansion and no quote interpretation, so a single quote in a step
# command — or a workflow-controlled step name — cannot close a host
# argument and inject a command onto the reproducer's host. The one
# residual escape is a body line that forges the terminator; that is
# rejected before the script is emitted (``_fnRejectDelimiterForgery``).
_S_HEREDOC_DELIMITER = "VAIBIFY_REPRODUCE_EOF"

_RE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# The environment key holding the epoch the pinned artefacts were
# dated and salted with. Read on the reproducing host and handed to
# the container as a real environment entry, because a *quoted*
# heredoc — the thing that keeps workflow-controlled step text off the
# host shell — cannot carry a host value into its body.
_S_ENVELOPE_EPOCH_KEY = "iSourceDateEpoch"

# The archived-environment fallback. A registry digest names bytes
# somebody else is storing, and `docker pull` is the moment that stops
# being true: a deleted tag, a retired registry, an account that
# lapsed, and the reproduction dies at step one with the environment
# irrecoverable. When the project deposited its image, the envelope
# records a DOI, and this fetches from the archive instead.
#
# Five properties of the shell below are load-bearing:
#
#  * WHICH Zenodo holds the deposit comes from the record's service
#    name, mapped through a table rendered from the Python client's
#    own -- so the two lanes cannot disagree about a host -- and a
#    record naming any other service is refused. Only a record written
#    before the service was recorded is resolved through doi.org.
#    Nothing read from the envelope is ever fetched as a URL: the
#    envelope is a file in a cloned repository.
#  * no redirect is followed before its target host is checked. `curl
#    -L` has already contacted the redirected host by the time its
#    final URL can be read, so every fetch here is sent WITHOUT -L,
#    reads %{redirect_url}, checks that host, and only then sends the
#    next request -- for a bounded number of hops.
#  * the download is VERIFIED against the recorded sha256 before
#    `docker load` sees it. Without that the fallback would trust
#    whatever the URL served, which is a worse failure than not
#    reproducing.
#  * a marker file records that the image came FROM the archive. A
#    later attestation on this clone must report its re-hash VACUOUS
#    rather than passed -- comparing a download against itself matches
#    always. The marker is a file rather than a field in
#    environment.json because this script ends by verifying
#    MANIFEST.sha256, which pins that file.
#  * the image that RUNS is the one `docker load` reports, never the
#    registry reference the pull just failed on. A tarball saved by
#    digest carries no tag (measured: `docker save repo@sha256:...`
#    writes RepoTags null and `docker load` answers "Loaded image
#    ID: sha256:..."), so the loaded image answers to its ID alone;
#    a `docker run` by the registry reference would attempt the pull
#    again and die on the line after the fallback rescued it.
_I_ARCHIVE_REDIRECT_HOP_LIMIT = 5

_S_ARCHIVE_FALLBACK_TEMPLATE = """\
# The Zenodo hosts, and nothing else. Rendered from the Python
# client's own table; a record naming any other service is refused.
fnZenodoBaseUrl() {
    case "$1" in
__SERVICE_CASES__
        *) return 1 ;;
    esac
}

# Whether one URL is on a host a deposit fetch may contact. The DOI
# resolver is admitted because a record with no recorded service is
# resolved through it; every hop it names is checked here too.
fnUrlWithinAllowlist() {
    case "$1" in
        __ALLOWLIST_PATTERNS__) return 0 ;;
    esac
    return 1
}

# Fetch one URL into a file, following redirects BY HAND: every hop's
# host is checked before the request for it is sent, never after.
# Prints the URL that finally answered, for a caller resolving a DOI.
# A third argument bounds the body: curl refuses a larger announced
# size up front (and recent versions stop a transfer that grows past
# it), so a broken permitted host cannot fill the disk.
fnFetchWithinAllowlist() {
    local sUrl="$1" sOutput="$2" iMaxBytes="${3:-}" iHop=0
    local sAnswer sCode sNext sHost
    local aCurlLimit=()
    if [ -n "$iMaxBytes" ]; then
        aCurlLimit=(--max-filesize "$iMaxBytes")
    fi
    while :; do
        if ! fnUrlWithinAllowlist "$sUrl"; then
            sHost=${sUrl#*://}
            echo "error: refusing to fetch from ${sHost%%/*}: not a" >&2
            echo "       Zenodo host this script knows." >&2
            return 1
        fi
        sAnswer=$(curl -fsS -o "$sOutput" \\
            ${aCurlLimit[@]+"${aCurlLimit[@]}"} \\
            -w '%{http_code} %{redirect_url}' "$sUrl") || return 1
        sCode=${sAnswer%% *}
        sNext=${sAnswer#* }
        case "$sCode" in
            3??) ;;
            *) printf '%s\\n' "$sUrl"; return 0 ;;
        esac
        iHop=$((iHop + 1))
        if [ -z "$sNext" ] || [ "$iHop" -gt __HOP_LIMIT__ ]; then
            echo "error: the deposit redirected without a target, or" >&2
            echo "       more than __HOP_LIMIT__ times; giving up." >&2
            return 1
        fi
        sUrl=$sNext
    done
}

# The record's landing URL. A record that names its service composes
# it from the table and fetches nothing to learn it; a record written
# before the service was recorded is resolved through doi.org, hop by
# hop through the same host check.
fnResolveRecordUrl() {
    local sService="$1" sDoi="$2" sBase
    if [ -n "$sService" ]; then
        if ! sBase=$(fnZenodoBaseUrl "$sService"); then
            echo "error: the deposit record names a Zenodo service this" >&2
            echo "       script does not know: $sService" >&2
            return 1
        fi
        printf '%s/records/%s\\n' "$sBase" "${sDoi##*zenodo.}"
        return 0
    fi
    fnFetchWithinAllowlist "__DOI_RESOLVER__$sDoi" /dev/null
}

fnLoadImageFromArchive() {
    local sDoi sName sSha sService iBytes sRecordUrl sTarball iOutcome
    sDoi=$(jq -r '.dictContainer.dictImageArchive.sVersionDoi // ""' \\
        .vaibify/environment.json)
    sName=$(jq -r '.dictContainer.dictImageArchive.sTarballName // ""' \\
        .vaibify/environment.json)
    sSha=$(jq -r '.dictContainer.dictImageArchive.sTarballSha256 // ""' \\
        .vaibify/environment.json)
    sService=$(jq -r '.dictContainer.dictImageArchive.sZenodoService // ""' \\
        .vaibify/environment.json)
    iBytes=$(jq -r '.dictContainer.dictImageArchive.iTarballBytes // 0' \\
        .vaibify/environment.json)
    if [ -z "$sDoi" ] || [ -z "$sName" ] || [ -z "$sSha" ]; then
        echo "error: the registry does not serve $sImageRef and this" >&2
        echo "       project archived no copy of its image." >&2
        return 1
    fi
    # The recorded size bounds the download; without one the fetch
    # could be made to fill the disk, so it is refused, never unbounded.
    case "$iBytes" in
        ''|0|*[!0-9]*)
            echo "error: the envelope records no size for the archived" >&2
            echo "       image, so its download cannot be bounded;" >&2
            echo "       refusing to fetch it." >&2
            return 1 ;;
    esac
    echo "Registry pull failed; fetching the archived image from $sDoi"
    sRecordUrl=$(fnResolveRecordUrl "$sService" "$sDoi") || return 1
    sTarball=$(mktemp -t vaibifyImage.XXXXXXXX)
    # An `if` rather than a bare list: under `set -e` a failing LAST
    # element of an and-list exits the script on the spot, skipping
    # the cleanup and the message below and leaving the tarball.
    if fnFetchWithinAllowlist "$sRecordUrl/files/$sName?download=1" \\
            "$sTarball" "$iBytes" >/dev/null \\
        && [ "$(( $(wc -c < "$sTarball") ))" -eq "$iBytes" ] \\
        && echo "${sSha#sha256:}  $sTarball" | sha256sum -c - >/dev/null \\
        && fnLoadCheckedTarball "$sTarball" "$sName"; then
        iOutcome=0
    else
        iOutcome=1
    fi
    rm -f "$sTarball"
    if [ "$iOutcome" -ne 0 ]; then
        echo "error: the archived image could not be fetched, was not" >&2
        echo "       the size the envelope records, did not match the" >&2
        echo "       hash it records, or could not be loaded." >&2
    fi
    return "$iOutcome"
}

fnLoadCheckedTarball() {
    local sLoaded
    case "$2" in
        *.zst) sLoaded=$(zstd -dc "$1" | docker load) ;;
        *.gz)  sLoaded=$(gzip -dc "$1" | docker load) ;;
        *)     sLoaded=$(docker load -i "$1") ;;
    esac || return 1
    sImageRef=$(printf '%s\\n' "$sLoaded" \\
        | sed -n 's/^Loaded image\\( ID\\)\\{0,1\\}: //p' | head -n 1)
    [ -n "$sImageRef" ] || return 1
    : > __LOADED_FROM_ARCHIVE_MARKER__
}

"""


def _fsRenderZenodoServiceCases():
    """Return the ``case`` arms mapping each service name to its host."""
    return "\n".join(
        f"        {sService}) printf '%s' '{sBaseUrl}' ;;"
        for sService, sBaseUrl in sorted(
            zenodoClient.fdictZenodoServiceTable().items(),
        )
    )


def _fsRenderZenodoAllowlistPatterns():
    """Return the ``case`` pattern admitting the Zenodo hosts and the resolver."""
    listBases = sorted(zenodoClient.fdictZenodoServiceTable().values())
    listBases.append(zenodoClient.S_DOI_RESOLVER_BASE.rstrip("/"))
    return "|".join(f"{sBase}/*" for sBase in listBases)


_S_ARCHIVE_FALLBACK = (
    _S_ARCHIVE_FALLBACK_TEMPLATE
    .replace("__SERVICE_CASES__", _fsRenderZenodoServiceCases())
    .replace("__ALLOWLIST_PATTERNS__", _fsRenderZenodoAllowlistPatterns())
    .replace("__DOI_RESOLVER__", zenodoClient.S_DOI_RESOLVER_BASE)
    .replace("__HOP_LIMIT__", str(_I_ARCHIVE_REDIRECT_HOP_LIMIT))
    .replace("__LOADED_FROM_ARCHIVE_MARKER__", S_LOADED_FROM_ARCHIVE_MARKER)
)

_S_HOST_PREAMBLE = """\
#!/usr/bin/env bash
# Auto-generated by vaibify. Reproduces this workflow on a stock host.
# Requires: docker, jq, sha256sum, curl (and zstd for a .zst archive).
#
# Exit codes, shared with `vaibify reproduce` and the dashboard:
#   0 reproduced (byte-identical, or published comparisons passed)
#   1 not reproduced (a comparison failed, an output is missing, or a
#     step's data or plot command failed)
#   3 published inputs changed
#   4 could not run
#   5 not enough evidence
set -euo pipefail
trap 'echo "Could not run: reproduce.sh failed at line $LINENO" >&2; exit 4' ERR

sImageRef=$(jq -r .dictContainer.sImageDigest \\
    .vaibify/environment.json)
if [ -z "$sImageRef" ] || [ "$sImageRef" = "null" ]; then
    echo "Could not run: sImageDigest missing from .vaibify/environment.json" >&2
    exit 4
fi

# The platform the envelope PINS is requested of the pull and of the
# run, in Docker's own spelling (linux/<arch>): without the request a
# multi-architecture reference silently yields whatever build THIS
# host prefers, and a run of the wrong build reproduces nothing. An
# envelope that recorded no architecture is announced, never defaulted
# to this host's -- the same treatment as an absent epoch below. The
# option rides in an array expanded with the guarded-expansion idiom,
# because an empty array under set -u is an error on bash 3.
sImageArchitecture=$(jq -r '.dictContainer.sArchitecture // ""' \\
    .vaibify/environment.json)
case "$sImageArchitecture" in
    ''|null) sImageArchitecture="" ;;
esac
if [ -z "$sImageArchitecture" ]; then
    echo "vaibify: .vaibify/environment.json records no image architecture;" >&2
    echo "  the pull and the run cannot request the pinned platform, so a" >&2
    echo "  multi-architecture reference yields this host's build. Regenerate" >&2
    echo "  the environment envelope on the authoring machine to fix it." >&2
    saPlatformOption=()
else
    case "$sImageArchitecture" in
        linux/*) sPlatform="$sImageArchitecture" ;;
        *)       sPlatform="linux/$sImageArchitecture" ;;
    esac
    saPlatformOption=(--platform "$sPlatform")
fi

# The epoch RECORDED when the manifest was pinned, never one derived
# from HEAD here: the commit that published the manifest moved HEAD,
# so a re-derived epoch would date and salt every figure differently
# from the pinned ones and no timestamped figure could match its bytes.
sSourceDateEpoch=$(jq -r '.{sEpochKey} // ""' \\
    .vaibify/environment.json)
case "$sSourceDateEpoch" in
    ''|0|*[!0-9]*) sSourceDateEpoch="" ;;
esac
if [ -z "$sSourceDateEpoch" ]; then
    echo "vaibify: .vaibify/environment.json records no {sEpochKey};" >&2
    echo "  this run's figures and archives will carry its wall-clock" >&2
    echo "  time, so the assessment will find every timestamped" >&2
    echo "  artefact (PDF, EPS, PS, SVG) differing. Regenerate the" >&2
    echo "  environment envelope on the authoring machine to fix it." >&2
fi

{sFallback}# A pull that fails is answered in the order a STRANGER needs. The
# archived copy comes first, because that is the only path open to a
# reproducer without the author's daemon: the registry has forgotten
# the bytes and the deposit still holds them. The copy already on this
# host comes last, because it is survivable for the AUTHOR only -- the
# pinned reference is a digest, so a local copy at that digest IS the
# pinned image, but what the failed pull and the failed load together
# prove is that nobody ELSE can run this script yet. Say which one
# happened rather than silently doing either.
if ! docker pull ${{saPlatformOption[@]+"${{saPlatformOption[@]}}"}} "$sImageRef"; then
    if ! fnLoadImageFromArchive; then
        if docker image inspect "$sImageRef" > /dev/null 2>&1; then
            echo "vaibify: could not pull $sImageRef; running the copy" >&2
            echo "  already on this host. A reproducer without that copy" >&2
            echo "  cannot run this script -- archive the image so a" >&2
            echo "  stranger can load it; a registry copy is optional." >&2
        else
            echo "Could not run: cannot pull $sImageRef, no archived copy" >&2
            echo "  could be loaded, and no local copy exists" >&2
            exit 4
        fi
    fi
fi

# --entrypoint bash bypasses the image's development entrypoint. That
# entrypoint configures an interactive workspace: it writes system git
# config (which needs the root phase this invocation does not run, so
# it exits non-zero here), clones the project's configured repos, and
# installs agent tooling. A reproduction wants none of it -- it wants
# the pinned steps, over the mounted repo, and nothing that reaches the
# network or the wall clock.
#
# Its exit status is the assessment's, so set -e must not act on it:
# 0, 1, 3, 4 and 5 pass through, and anything else (125, 126, 127, a
# signal) is docker itself failing, which is "Could not run".
set +e
trap - ERR
docker run --rm -i --entrypoint bash \\
    ${{saPlatformOption[@]+"${{saPlatformOption[@]}}"}} \\
    -e "SOURCE_DATE_EPOCH=$sSourceDateEpoch" \\
    -v "$PWD":/work -w /work "$sImageRef" \\
    -s <<'{sDelimiter}'
""".format(
    sDelimiter=_S_HEREDOC_DELIMITER,
    sEpochKey=_S_ENVELOPE_EPOCH_KEY,
    sFallback=_S_ARCHIVE_FALLBACK,
)


def _fsBuildContainerPreamble():
    """Return the heredoc lines that run before the first step command.

    In the runner's order, and ending in one of the shared exit codes
    on every path: shell errors are "Could not run" (the ERR trap);
    the rerun's scratch and evidence directories are this run's own,
    created by ``mktemp -d`` outside ``/work`` and removed on exit; the
    determinism guarantees are the runner's own; the lock installs or
    the run could not run; and ``prepare`` checks every published input
    and sets the published figures aside before anything is deleted.
    A changed input goes straight to ``assess``, whose verdict says so.

    The salt applies the same two guarantees the live runner applies --
    the recorded ``SOURCE_DATE_EPOCH`` and matplotlib's ``svg.hashsalt``
    derived from it -- through the runner's own salt builder rather than
    respelling the rcParam here. An absent epoch is unset rather than
    exported empty, and the host preamble has already said what that
    costs. The salt lives in the script's OWN temporary directory
    (named by its process id); the exit trap names it literally, never
    ``$MPLCONFIGDIR``, because a step may export its own.
    """
    from vaibify.gui.determinismEnvironment import (
        S_MATPLOTLIB_CONFIG_ROOT, S_MATPLOTLIB_DIRECTORY_PREFIX,
        fsBuildMatplotlibSaltShell,
    )
    sDirectory = (
        f'"{S_MATPLOTLIB_CONFIG_ROOT}/{S_MATPLOTLIB_DIRECTORY_PREFIX}$$"'
    )
    return "\n".join([
        "set -euo pipefail",
        "trap 'echo \"Could not run: reproduce.sh failed at line "
        "$LINENO\" >&2; exit 4' ERR",
        'sVaibifyScratch=$(mktemp -d "${TMPDIR:-/tmp}/vaibifyScratch.XXXXXXXX")',
        'sVaibifyEvidence=$(mktemp -d "${TMPDIR:-/tmp}/vaibifyEvidence.XXXXXXXX")',
        "trap 'rm -rf " + sDirectory + ' "$sVaibifyScratch" '
        '"$sVaibifyEvidence"\' EXIT',
        'if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then',
        "    " + fsBuildMatplotlibSaltShell("$SOURCE_DATE_EPOCH", sDirectory),
        "else",
        "    unset SOURCE_DATE_EPOCH",
        "fi",
        "pip install --require-hashes -r requirements.lock || {",
        '    echo "Could not run: requirements.lock could not be installed" >&2',
        "    exit 4",
        "}",
        _S_LEDGER_FUNCTION,
        f"sVaibifyAssessor=/work/{S_RERUN_ASSESSMENT_REPO_PATH}",
        'if python3 -I "$sVaibifyAssessor" prepare --repo /work \\',
        '        --scratch "$sVaibifyScratch"; then',
        "    iVaibifyStatus=0",
        "else",
        "    iVaibifyStatus=$?",
        "fi",
        _S_ASSESS_FUNCTION,
        'case "$iVaibifyStatus" in',
        "    0) ;;",
        "    3) fnVaibifyAssess ;;",
        "    4) exit 4 ;;",
        "    *)",
        '        echo "Could not run: the assessor could not prepare the rerun'
        ' (exit $iVaibifyStatus)" >&2',
        "        exit 4 ;;",
        "esac",
        "",
    ])


# The assessment's exit code is the script's -- but only once the
# assessor has written its report. A Python that failed before grading
# (a missing file, a syntax error) exits 1 or 2 with no report, and 1
# must never be read as "not reproduced".
_S_ASSESS_FUNCTION = """\
fnVaibifyAssess() {
    if python3 -I "$sVaibifyAssessor" assess --repo /work \\
            --scratch "$sVaibifyScratch" --evidence "$sVaibifyEvidence"; then
        iVaibifyStatus=0
    else
        iVaibifyStatus=$?
    fi
    if [ ! -s "$sVaibifyScratch/assessment.json" ]; then
        echo "Could not run: the assessor reported no verdict (exit $iVaibifyStatus)" >&2
        exit 4
    fi
    exit "$iVaibifyStatus"
}"""


# Every command's exit status goes to the invocation ledger, the
# account the assessment reads of what ran. A planned command with no
# line never ran.
_S_LEDGER_FUNCTION = """\
: > "$sVaibifyEvidence/invocations.tsv"
fnVaibifyRecord() {
    printf '%s\\t%s\\n' "$1" "$2" >> "$sVaibifyEvidence/invocations.tsv"
    [ "$2" = 0 ]
}"""


def _flistRenderClearingNotice(dictPlan):
    """Return the lines that say what will be deleted, then delete it.

    ``reproduce.sh`` regenerates in the reader's own directory, so it
    first deletes what it regenerates, exactly as the shadow does --
    without that, a command that exits 0 and writes nothing would pass
    on the published file. The reader is told which files and how to
    get them back before anything is touched.
    """
    listProduced = dictPlan["dictClearingJob"]["listProduced"]
    listLines = [
        'echo "vaibify: deleting the outputs this script regenerates:"',
    ]
    listLines += [
        f"printf '  %s\\n' {_fsShellQuote(sPath)}" for sPath in listProduced
    ]
    listLines += [
        'echo "  To restore them: git checkout -- <the paths above>, or"',
        'echo "  download them from the project\'s archived deposit."',
        'if python3 -I "$sVaibifyAssessor" clear --repo /work; then',
        "    :",
        "else",
        "    exit 4",
        "fi",
        "",
    ]
    return listLines


# The assessment is the script's verdict, and its exit code is the
# script's. Before 2026-10-17 this was an unconditional
# ``sha256sum -c MANIFEST.sha256``, which graded bytes alone.
_S_SCRIPT_EPILOGUE = """\
fnVaibifyAssess
{sDelimiter}
iVaibifyExit=$?
case "$iVaibifyExit" in
    0|1|3|4|5) exit "$iVaibifyExit" ;;
    *)
        echo "Could not run: docker exited $iVaibifyExit" >&2
        exit 4 ;;
esac
""".format(sDelimiter=_S_HEREDOC_DELIMITER)


def fsGenerateReproduceScript(
    sProjectRepo, dictWorkflow,
    connectionDocker=None, sContainerId="",
):
    """Write ``reproduce.sh`` inside the container at the project repo root.

    Idempotent: writing the same workflow twice produces the same
    bytes. Returns the container-absolute path written so callers can
    update the manifest or surface the path to the user.

    Routes the write through ``connectionDocker.fnWriteFile`` because
    ``sProjectRepo`` is a container path (e.g. ``/workspace/foo``);
    a host-side ``Path.write_text`` would land in a host file at the
    same absolute path rather than inside the container. Both
    ``connectionDocker`` and ``sContainerId`` are required; passing
    either as falsy raises ``ValueError`` so callers can never silently
    fall back to the host path.
    """
    from vaibify.config.connectionAvailability import (
        fbDockerReachable,
    )
    if not fbDockerReachable(connectionDocker) or not sContainerId:
        raise ValueError(
            "fsGenerateReproduceScript requires a docker connection "
            "and container id; reproduce.sh must be written inside "
            "the container because sProjectRepo is a container path."
        )
    for sRelativePath, sContent in ftRenderReproductionFiles(dictWorkflow):
        connectionDocker.fnWriteFile(
            sContainerId, posixpath.join(sProjectRepo, sRelativePath),
            sContent.encode("utf-8"),
        )
    sContainerPath = posixpath.join(
        sProjectRepo, S_REPRODUCE_SCRIPT_FILENAME,
    )
    _fnMarkExecutableInContainer(
        connectionDocker, sContainerId, sContainerPath,
    )
    return sContainerPath


def ftRenderReproductionFiles(dictWorkflow):
    """Return ``((path, text), ...)``: the script and the assessor it runs.

    Both are rendered before either is written, so a workflow the
    renderer refuses leaves neither file half-updated. The assessor is
    rendered under the CURRENT comparison policy: regenerating
    ``reproduce.sh`` is how an author moves a project to a new policy.
    """
    from vaibify.reproducibility.rerunAssessment import (
        S_CURRENT_COMPARISON_POLICY,
        fsRenderRerunAssessmentScript,
    )
    sScript = fsRenderReproduceScript(dictWorkflow)
    sAssessor = fsRenderRerunAssessmentScript(
        dictWorkflow, S_CURRENT_COMPARISON_POLICY)
    return (
        (S_REPRODUCE_SCRIPT_FILENAME, sScript),
        (S_RERUN_ASSESSMENT_REPO_PATH, sAssessor),
    )


def _fnMarkExecutableInContainer(
    connectionDocker, sContainerId, sContainerPath,
):
    """Add owner+group+other execute bits to a container-side file."""
    sCommand = "chmod a+x " + _fsShellQuote(sContainerPath)
    connectionDocker.ftResultExecuteCommand(sContainerId, sCommand)


def fsRenderReproduceScript(dictWorkflow):
    """Return the full ``reproduce.sh`` body as a string.

    Pure function so tests can compare against a fixture without
    touching the filesystem. Step commands are rendered as lines of
    the container heredoc body; the host shell never interprets them.

    The assessment plan is built FIRST, so a classification the shadow
    would refuse -- an ambiguous or unsafe output -- refuses to render
    here too, and a reader's script never carries a job the shadow
    would refuse. Raises ``ValueError`` for that, as for an unresolved
    token.
    """
    from vaibify.reproducibility.rerunAssessment import (
        fdictBuildAssessmentPlan,
    )
    from vaibify.reproducibility.rerunPreparation import (
        RerunPreparationRefusedError,
    )
    try:
        dictPlan = fdictBuildAssessmentPlan(dictWorkflow or {})
    except RerunPreparationRefusedError as errorRefused:
        raise ValueError(str(errorRefused)) from errorRefused
    listStepLines = flistRenderStepCommands(dictWorkflow)
    _fnRejectUnresolvedTokens(listStepLines)
    sBody = "\n".join(_flistRenderClearingNotice(dictPlan) + listStepLines)
    if sBody:
        sBody = sBody + "\n"
    sScript = (
        _S_HOST_PREAMBLE + _fsBuildContainerPreamble()
        + sBody + _S_SCRIPT_EPILOGUE
    )
    _fnRejectDelimiterForgery(sScript)
    return sScript


def _fnRejectDelimiterForgery(sScript):
    """Refuse to emit a script whose body forges the heredoc terminator.

    The reproduction body reaches the container shell through a quoted
    heredoc, so a step command or name occupying a line equal to the
    terminator would close the heredoc early and hand the remaining
    lines to the host shell — the exact breakout the heredoc exists to
    prevent. The terminator appears legitimately on exactly one line
    (the closing delimiter); any other exact-line occurrence is a
    tampering attempt, so fail loud rather than emit an injectable
    script.
    """
    iExactMatches = sum(
        1 for sLine in sScript.splitlines()
        if sLine == _S_HEREDOC_DELIMITER
    )
    if iExactMatches != 1:
        raise ValueError(
            "reproduce.sh body contains the reserved heredoc "
            "terminator; refusing to emit an injectable script."
        )


def flistRenderStepCommands(dictWorkflow):
    """Return the shell lines that execute the workflow's steps in order.

    Exactly the steps the attested rerun executes
    (:func:`~vaibify.reproducibility.rerunPreparation.flistSelectStepsTheRerunExecutes`):
    an interactive step needs a researcher, so its outputs are given
    and its commands are not run, and a stranger's script and the
    attestation must agree on what "reproduced" means. Each step runs
    as the runner runs it (design fact 24): its data commands, then
    only if every one succeeded its plot commands, then only if those
    succeeded each test group, a failing command ending its group and
    every group running -- and the next step runs regardless. Steps
    with no commands are skipped.
    """
    from vaibify.gui.pipelineUtils import flistComputeAllStepLabels
    from vaibify.reproducibility.rerunPreparation import (
        flistSelectStepsTheRerunExecutes,
    )
    dictVariables = _fdictBuildReproductionVariables(dictWorkflow)
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    if not isinstance(listSteps, list):
        return []
    listLabels = flistComputeAllStepLabels(listSteps)
    setExecuted = {id(dictStep) for dictStep in
                   flistSelectStepsTheRerunExecutes(dictWorkflow)}
    listLines = []
    for iIndex, dictStep in enumerate(listSteps):
        if id(dictStep) in setExecuted:
            listLines.extend(_flistRenderOneStep(
                dictStep, listLabels[iIndex], dictVariables))
    return listLines


def _fdictBuildReproductionVariables(dictWorkflow):
    """Return the substitution variables as seen from the reproduction mount.

    The same two builders the live runner uses, rebuilt against
    :data:`S_REPRODUCTION_REPO_ROOT`. Sharing the builders is the
    point: a private copy here would drift from the runner, and a
    reproduction that resolves its paths differently from the run it
    reproduces is not a reproduction.
    """
    from vaibify.gui.workflowManager import (
        fdictBuildGlobalVariablesForRoot,
        fdictBuildStepVariables,
    )
    dictWorkflow = dictWorkflow or {}
    dictVariables = fdictBuildGlobalVariablesForRoot(
        dictWorkflow, S_REPRODUCTION_REPO_ROOT,
    )
    listSteps = _flistStepsSafeToIndex(dictWorkflow.get("listSteps"))
    if listSteps:
        dictVariables.update(
            fdictBuildStepVariables(
                {"listSteps": listSteps}, dictVariables,
            ),
        )
    return dictVariables


def _flistStepsSafeToIndex(listSteps):
    """Return listSteps with non-dict entry replaced by an empty step.

    This renderer skips a corrupt step rather than refusing the whole
    script, and the shared variable builder does not -- it indexes
    ``sStepId`` straight off each entry. REPLACED, never filtered: the
    deprecated positional ``{StepNN.stem}`` form is keyed on position,
    so dropping an entry would silently renumber every step after it
    and resolve those tokens to the wrong file. An empty dict declares
    no outputs, so it contributes no variables and holds its place.
    """
    if not isinstance(listSteps, list):
        return []
    return [
        dictStep if isinstance(dictStep, dict) else {}
        for dictStep in listSteps
    ]


def _flistRenderOneStep(dictStep, sStepLabel, dictVariables):
    """Return the shell lines for one step's sections, in the runner's order."""
    if not isinstance(dictStep, dict):
        return []
    dictSections = _fdictGatherStepSections(dictStep, dictVariables)
    if not any(dictSections["data"] + dictSections["plot"] + [
            sCommand for _sGroup, listGroup in dictSections["tests"]
            for sCommand in listGroup]):
        return []
    from vaibify.gui.workflowManager import fsResolveCommand
    sDirectory = fsResolveCommand(
        (dictStep.get("sDirectory") or "").strip(), dictVariables,
    )
    if sDirectory == ".":
        sDirectory = ""
    listLines = [
        f"# Step: {_fsSanitizeCommentText(dictStep.get('sName', '?'))}",
        "mkdir -p " + _fsShellQuote(
            dictVariables.get("sPlotDirectory", "Plot")),
        "bVaibifyStep=1",
    ]
    for sSection in ("data", "plot"):
        listLines += _flistRenderSection(
            sStepLabel, sSection, dictSections[sSection], sDirectory,
            "bVaibifyStep")
    listGroups = [tGroup for tGroup in dictSections["tests"] if tGroup[1]]
    if not listGroups:
        return listLines
    listLines.append('if [ "$bVaibifyStep" = 1 ]; then')
    for sSection, listGroup in listGroups:
        listLines.append("    bVaibifyGroup=1")
        listLines += ["    " + sLine for sLine in _flistRenderSection(
            sStepLabel, sSection, listGroup, sDirectory, "bVaibifyGroup")]
    listLines.append("fi")
    return listLines


def _flistRenderSection(sStepLabel, sSection, listCommands, sDirectory,
                        sGuard):
    """Return one section's commands, each guarded by the section's flag."""
    from vaibify.reproducibility.rerunAssessment import (
        flistNumberInvocations,
    )
    listIds = flistNumberInvocations(sStepLabel, sSection, listCommands)
    listLines = []
    for sId, sCommand in zip(listIds, listCommands):
        listLines += _flistRenderCommand(
            sId, sCommand, sDirectory, sGuard,
            sSection not in ("data", "plot"))
    return listLines


def _flistRenderCommand(sId, sCommand, sDirectory, sGuard, bTest):
    """Return the lines that run one command and record its exit status.

    Each command gets its own subshell, because the runner gives each
    command its own exec and shares no shell state between commands,
    and runs with bash's DEFAULT options, as the runner's ``bash -c``
    does: no ``-e`` (already off inside an ``if`` condition), no ``-u``,
    no ``pipefail``. Under this script's own ``set -euo pipefail`` a
    command such as ``a | tee log`` would exit differently in the two
    lanes. The command text is delivered literally, and the closing
    parenthesis sits on its own line so a trailing comment in a command
    cannot swallow it. A test command's JUnit report is pointed at this
    run's evidence directory under its identity.
    """
    sCd = f"cd {_fsShellQuote(sDirectory)} && " if sDirectory else ""
    sExport = (
        f'export PYTEST_ADDOPTS="--junitxml=$sVaibifyEvidence/{sId}.xml" && '
        if bTest else "")
    return [
        f'if [ "${sGuard}" = 1 ]; then',
        f"    if ( {sCd}{sExport}set +e +u +o pipefail && {sCommand}",
        "    ); then",
        "        iVaibifyStatus=0",
        "    else",
        "        iVaibifyStatus=$?",
        "    fi",
        f'    fnVaibifyRecord {_fsShellQuote(sId)} "$iVaibifyStatus" || '
        f"{sGuard}=0",
        "fi",
    ]


def _fsSanitizeCommentText(sValue):
    """Collapse a value to a single safe line for a shell comment.

    The step name is workflow-controlled (it originates from
    ``project.json``, which the in-container agent can write).
    Rendered raw into the reproduction body, a newline would end the
    ``# Step:`` comment and turn the remainder of the name into live
    script. Replacing every control character (newlines, tabs, escape
    sequences) with a space and collapsing the result keeps the name a
    human-readable, single-line label that can never leave its comment.
    """
    sCleaned = _RE_CONTROL_CHARACTERS.sub(" ", str(sValue))
    return " ".join(sCleaned.split()) or "?"


def _flistResolveCommands(listCommands, dictVariables):
    """Return the non-empty commands, substituted as the runner substitutes.

    Copying the raw text instead emitted a script that created a
    literal ``{sPlotDirectory}`` directory and failed on the first
    cross-step path -- while ``fbVerifyReproduceScript`` reported it
    green, because that gate checks existence and manifest membership,
    not whether the script can run.
    """
    from vaibify.gui.workflowManager import fsResolveCommand
    return [
        fsResolveCommand((sCommand or "").strip(), dictVariables)
        for sCommand in listCommands or [] if (sCommand or "").strip()
    ]


def _fdictGatherStepSections(dictStep, dictVariables):
    """Return ``{data, plot, tests}`` for one step, in the runner's order.

    The rerun ignores ``bPlotOnly``, so the data commands always run.
    The test groups are the runner's own
    (``workflowManager.fdictResolveTestCommandGroups``), named as the
    rerun's invocation sections.
    """
    from vaibify.gui.workflowManager import fdictResolveTestCommandGroups
    dictSectionNames = {
        "dictIntegrity": "integrity", "dictQualitative": "qualitative",
        "dictQuantitative": "quantitative", "legacy": "legacy",
    }
    return {
        "data": _flistResolveCommands(
            dictStep.get("saDataCommands"), dictVariables),
        "plot": _flistResolveCommands(
            dictStep.get("saPlotCommands"), dictVariables),
        "tests": [
            (dictSectionNames[sGroup],
             _flistResolveCommands(listGroup, dictVariables))
            for sGroup, listGroup in fdictResolveTestCommandGroups(
                dictStep).items()
        ],
    }


def _fnRejectUnresolvedTokens(listStepLines):
    """Refuse to emit a script still carrying vaibify template tokens.

    The same fail-loud stance as :func:`_fnRejectDelimiterForgery`,
    for the same reason: a script that cannot execute is worse than no
    script, because the L3 gate that reads it reports a green row
    either way and the researcher only finds out at the end of an
    hours-long rebuild. A residual token here means the workflow
    references an output no step declares -- ``resolve-commands``
    reports which.
    """
    from vaibify.gui.workflowManager import flistResidualWorkflowTokens
    listTokens = []
    for sLine in listStepLines:
        listTokens.extend(flistResidualWorkflowTokens(sLine))
    if listTokens:
        raise ValueError(
            "reproduce.sh commands carry unresolved tokens "
            + ", ".join(sorted(set(listTokens)))
            + "; every cross-step reference must name a declared "
            + "output (see resolve-commands)."
        )


def _fsShellQuote(sValue):
    """Return sValue as a POSIX single-quoted shell argument."""
    return fsShellQuotePosix(sValue)
