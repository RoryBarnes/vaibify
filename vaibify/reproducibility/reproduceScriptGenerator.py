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


__all__ = [
    "S_REPRODUCE_SCRIPT_FILENAME",
    "S_REPRODUCTION_REPO_ROOT",
    "fsGenerateReproduceScript",
    "fsRenderReproduceScript",
    "flistRenderStepCommands",
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
fnFetchWithinAllowlist() {
    local sUrl="$1" sOutput="$2" iHop=0 sAnswer sCode sNext sHost
    while :; do
        if ! fnUrlWithinAllowlist "$sUrl"; then
            sHost=${sUrl#*://}
            echo "error: refusing to fetch from ${sHost%%/*}: not a" >&2
            echo "       Zenodo host this script knows." >&2
            return 1
        fi
        sAnswer=$(curl -fsS -o "$sOutput" \\
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
    local sDoi sName sSha sService sRecordUrl sTarball iOutcome
    sDoi=$(jq -r '.dictContainer.dictImageArchive.sVersionDoi // ""' \\
        .vaibify/environment.json)
    sName=$(jq -r '.dictContainer.dictImageArchive.sTarballName // ""' \\
        .vaibify/environment.json)
    sSha=$(jq -r '.dictContainer.dictImageArchive.sTarballSha256 // ""' \\
        .vaibify/environment.json)
    sService=$(jq -r '.dictContainer.dictImageArchive.sZenodoService // ""' \\
        .vaibify/environment.json)
    if [ -z "$sDoi" ] || [ -z "$sName" ] || [ -z "$sSha" ]; then
        echo "error: the registry does not serve $sImageRef and this" >&2
        echo "       project archived no copy of its image." >&2
        return 1
    fi
    echo "Registry pull failed; fetching the archived image from $sDoi"
    sRecordUrl=$(fnResolveRecordUrl "$sService" "$sDoi") || return 1
    sTarball=$(mktemp -t vaibifyImage.XXXXXXXX)
    # An `if` rather than a bare list: under `set -e` a failing LAST
    # element of an and-list exits the script on the spot, skipping
    # the cleanup and the message below and leaving the tarball.
    if fnFetchWithinAllowlist "$sRecordUrl/files/$sName?download=1" \\
            "$sTarball" >/dev/null \\
        && echo "${sSha#sha256:}  $sTarball" | sha256sum -c - >/dev/null \\
        && fnLoadCheckedTarball "$sTarball" "$sName"; then
        iOutcome=0
    else
        iOutcome=1
    fi
    rm -f "$sTarball"
    if [ "$iOutcome" -ne 0 ]; then
        echo "error: the archived image could not be fetched, did not" >&2
        echo "       match the hash the envelope records, or could not" >&2
        echo "       be loaded." >&2
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
set -euo pipefail

sImageRef=$(jq -r .dictContainer.sImageDigest \\
    .vaibify/environment.json)
if [ -z "$sImageRef" ] || [ "$sImageRef" = "null" ]; then
    echo "error: sImageDigest missing from .vaibify/environment.json" >&2
    exit 2
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
# from the pinned ones and the closing sha256sum -c could not pass.
sSourceDateEpoch=$(jq -r '.{sEpochKey} // ""' \\
    .vaibify/environment.json)
case "$sSourceDateEpoch" in
    ''|0|*[!0-9]*) sSourceDateEpoch="" ;;
esac
if [ -z "$sSourceDateEpoch" ]; then
    echo "vaibify: .vaibify/environment.json records no {sEpochKey};" >&2
    echo "  this run's figures and archives will carry its wall-clock" >&2
    echo "  time, so sha256sum -c will report every timestamped" >&2
    echo "  artefact (PDF, EPS, PS, SVG) as differing. Regenerate the" >&2
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
            echo "error: cannot pull $sImageRef, no archived copy could" >&2
            echo "  be loaded, and no local copy exists" >&2
            exit 2
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

    Applies the same two guarantees the live runner applies — the
    recorded ``SOURCE_DATE_EPOCH`` and matplotlib's ``svg.hashsalt``
    derived from it — by calling the runner's own salt builder rather
    than respelling the rcParam here. A second authority on what the
    determinism guarantee *is* would drift from the runner, and the two
    lanes disagreeing is the defect this exists to fix.

    An absent epoch is unset rather than exported empty (an empty
    ``SOURCE_DATE_EPOCH`` is a value some tools reject and others read
    as "now", which is two wrong answers), and the host preamble has
    already said on stderr what that costs.
    """
    from vaibify.gui.determinismEnvironment import (
        fsBuildMatplotlibSaltShell,
    )
    return "\n".join([
        "set -euo pipefail",
        'if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then',
        "    " + fsBuildMatplotlibSaltShell("$SOURCE_DATE_EPOCH"),
        "else",
        "    unset SOURCE_DATE_EPOCH",
        "fi",
        "pip install --require-hashes -r requirements.lock",
        "",
    ])


_S_SCRIPT_EPILOGUE = """\
sha256sum -c MANIFEST.sha256
{sDelimiter}
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
    sScript = fsRenderReproduceScript(dictWorkflow)
    sContainerPath = posixpath.join(
        sProjectRepo, S_REPRODUCE_SCRIPT_FILENAME,
    )
    connectionDocker.fnWriteFile(
        sContainerId, sContainerPath, sScript.encode("utf-8"),
    )
    _fnMarkExecutableInContainer(
        connectionDocker, sContainerId, sContainerPath,
    )
    return sContainerPath


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
    """
    listStepLines = flistRenderStepCommands(dictWorkflow)
    _fnRejectUnresolvedTokens(listStepLines)
    sBody = "\n".join(listStepLines)
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
    """Return the shell lines that execute every workflow step in order.

    Each step contributes its ``saDataCommands`` then
    ``saPlotCommands``; ``cd`` into the step directory keeps relative
    paths inside the step's working tree the way the live runner does.
    Steps with no commands (e.g., ai-declaration) are skipped.
    """
    dictVariables = _fdictBuildReproductionVariables(dictWorkflow)
    listLines = []
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        listLines.extend(_flistRenderOneStep(dictStep, dictVariables))
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


def _flistRenderOneStep(dictStep, dictVariables):
    """Return the shell lines for one step's commands."""
    if not isinstance(dictStep, dict):
        return []
    listCommands = _flistGatherStepCommands(dictStep, dictVariables)
    if not listCommands:
        return []
    from vaibify.gui.workflowManager import fsResolveCommand
    sName = _fsSanitizeCommentText(dictStep.get("sName", "?"))
    sDirectory = fsResolveCommand(
        (dictStep.get("sDirectory") or "").strip(), dictVariables,
    )
    listLines = [f"# Step: {sName}"]
    if sDirectory and sDirectory != ".":
        listLines.append(f"( cd {_fsShellQuote(sDirectory)} && \\")
        for sCommand in listCommands:
            listLines.append(f"    {sCommand} && \\")
        listLines[-1] = listLines[-1][:-4]
        listLines.append(")")
    else:
        listLines.extend(listCommands)
    return listLines


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


def _flistGatherStepCommands(dictStep, dictVariables):
    """Return data-then-plot commands for one step, skipping empties.

    Each command is substituted the way the live runner substitutes
    it. Copying the raw text instead emitted a script that created a
    literal ``{sPlotDirectory}`` directory and failed on the first
    cross-step path -- while ``fbVerifyReproduceScript`` reported it
    green, because that gate checks existence and manifest membership,
    not whether the script can run.
    """
    from vaibify.gui.workflowManager import fsResolveCommand
    listOut = []
    for sKey in ("saDataCommands", "saPlotCommands"):
        for sCommand in dictStep.get(sKey, []) or []:
            sStripped = (sCommand or "").strip()
            if sStripped:
                listOut.append(
                    fsResolveCommand(sStripped, dictVariables),
                )
    return listOut


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
