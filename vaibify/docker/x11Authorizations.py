"""Which containers may draw on the researcher's display, and the record of it.

An opted-in container gets its OWN X authorization: untrusted where the
server supports the SECURITY extension, generated through the helper
process :mod:`vaibify.host.x11Authority` (never in the hub's process,
because an Xlib error can end the process it happens in), written to a
per-container directory ``~/.vaibify/x11/<container>/`` that the
container mounts read-only at ``/run/vaibify/x11``. A directory, not a
file: a single-file bind mount pins the inode, so a replacement cookie
would never reach a running container, where a renamed file in a
mounted directory does.

The record ``~/.vaibify/x11/authorizations.json`` (mode 600, no cookie
values) holds, per container: the project identity, the authorization
id, the display, the directory, when it was made and its trust level.
Every revocation PROBES first -- connects with the container's own
cookie -- because after an X server restart ids are reused and a stale
id can name another container's live authorization: an accepted probe
revokes over a trusted connection, a refused probe records ``gone`` and
revokes nothing, an unreachable server keeps the record for a retry.

Where the server has no SECURITY extension (this is XQuartz and the
MacPorts server today), the fallback copies the server's own cookie
into the container's directory. That is full display access, revocable
only by restarting the X server, and every surface that reports it says
so; the fallback never applies to a transient failure, which leaves the
container without X11 and the dashboard saying why.
"""

__all__ = [
    "S_CONTAINER_AUTHORITY_DIRECTORY",
    "S_CONTAINER_AUTHORITY_PATH",
    "S_TRUST_SERVER_COOKIE",
    "S_TRUST_UNTRUSTED",
    "fdictRunHelper",
    "fsX11Root",
    "fsAuthorizationDirectory",
    "fdictReadAuthorizations",
    "fdictPrepareAuthorization",
    "flistMountArguments",
    "fdictReleaseAuthorization",
    "fnReleaseAuthorizationQuietly",
    "fdictReconcile",
    "fdictRenewIfGone",
    "flistDescribeAuthorization",
]

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from vaibify.config.durableFile import fnWriteFileDurably
from vaibify.host import x11Authority

logger = logging.getLogger(__name__)

S_CONTAINER_AUTHORITY_DIRECTORY = "/run/vaibify/x11"
S_CONTAINER_AUTHORITY_PATH = (
    S_CONTAINER_AUTHORITY_DIRECTORY + "/" + x11Authority.S_COOKIE_FILE_NAME
)
S_TRUST_UNTRUSTED = "untrusted"
S_TRUST_SERVER_COOKIE = "serverCookie"
F_HELPER_TIMEOUT_SECONDS = 15.0
_RE_CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_S_RECORD_FILE_NAME = "authorizations.json"
_S_RENEWED_NOTICE = "X11 access renewed after the X server restarted."
S_SERVER_COOKIE_NOTICE = (
    "This X server has no SECURITY extension, so the container holds a "
    "copy of the server's own cookie: full display access, revocable "
    "only by restarting the X server."
)


def fsX11Root():
    """Return ``~/.vaibify/x11``, resolved when asked so tests can move HOME."""
    return os.path.join(os.path.expanduser("~"), ".vaibify", "x11")


def _fnValidateContainerName(sContainerName):
    if not _RE_CONTAINER_NAME.match(sContainerName or ""):
        raise ValueError(f"not a container name: {sContainerName!r}")


def fsAuthorizationDirectory(sContainerName):
    """Return the container's own directory under the X11 root (created 0700)."""
    _fnValidateContainerName(sContainerName)
    sDirectory = os.path.join(fsX11Root(), sContainerName)
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    os.chmod(sDirectory, 0o700)
    os.chmod(fsX11Root(), 0o700)
    return sDirectory


def _fsRecordPath():
    return os.path.join(fsX11Root(), _S_RECORD_FILE_NAME)


def fdictReadAuthorizations():
    """Return the record: ``{sContainerName: {...}}``; absent is empty."""
    try:
        with open(_fsRecordPath(), "r", encoding="utf-8") as fileRecord:
            dictRecord = json.load(fileRecord)
    except FileNotFoundError:
        return {}
    return dictRecord if isinstance(dictRecord, dict) else {}


def _fnWriteAuthorizations(dictRecord):
    os.makedirs(fsX11Root(), mode=0o700, exist_ok=True)
    fnWriteFileDurably(
        _fsRecordPath(),
        json.dumps(dictRecord, indent=2, sort_keys=True).encode("utf-8"),
        iMode=0o600,
    )


def _fnUpdateRecord(sContainerName, dictFields):
    dictRecord = fdictReadAuthorizations()
    dictEntry = dictRecord.setdefault(sContainerName, {})
    dictEntry.update(dictFields)
    dictEntry["sUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    _fnWriteAuthorizations(dictRecord)


def _fnDropRecord(sContainerName):
    dictRecord = fdictReadAuthorizations()
    if dictRecord.pop(sContainerName, None) is not None:
        _fnWriteAuthorizations(dictRecord)


def fdictRunHelper(listArguments, fTimeoutSeconds=F_HELPER_TIMEOUT_SECONDS):
    """Run one helper operation; return its outcome, ``error`` for anything else.

    A timeout, a nonzero exit or output that is not one JSON object with
    an ``sOutcome`` is reported as ``error`` with the reason, never as
    a success and never as an exception the caller has to know about.
    """
    listCommand = [sys.executable, "-I", "-m", "vaibify.host.x11Authority"] + list(
        listArguments,
    )
    try:
        processResult = subprocess.run(
            listCommand, capture_output=True, text=True, encoding="utf-8",
            timeout=fTimeoutSeconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"sOutcome": x11Authority.S_OUTCOME_ERROR,
                "sReason": f"the X11 helper did not answer within {fTimeoutSeconds:g} s"}
    except OSError as error:
        return {"sOutcome": x11Authority.S_OUTCOME_ERROR,
                "sReason": f"the X11 helper could not start: {error}"}
    return _fdictParseHelperOutput(processResult)


def _fdictParseHelperOutput(processResult):
    if processResult.returncode != 0:
        return {"sOutcome": x11Authority.S_OUTCOME_ERROR,
                "sReason": (f"the X11 helper exited {processResult.returncode}: "
                            f"{(processResult.stderr or '').strip()[-300:]}")}
    try:
        dictOutcome = json.loads((processResult.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"sOutcome": x11Authority.S_OUTCOME_ERROR,
                "sReason": "the X11 helper printed no readable answer"}
    if not isinstance(dictOutcome, dict) or not dictOutcome.get("sOutcome"):
        return {"sOutcome": x11Authority.S_OUTCOME_ERROR,
                "sReason": "the X11 helper's answer named no outcome"}
    return dictOutcome


def _flistCommonArguments(sDirectory, sDisplay, sProbeDisplay, sNumber):
    return [
        "--directory", sDirectory, "--display", sDisplay,
        "--probe-display", sProbeDisplay, "--number", sNumber,
        "--authority", os.environ.get("XAUTHORITY", ""),
    ]


def fdictPrepareAuthorization(
    sContainerName, sProjectUuid, sDisplay, sProbeDisplay, sNumber,
):
    """Give a container its own authorization; return the outcome recorded.

    ``sDisplay`` is the display as this host reaches it, ``sProbeDisplay``
    the display as the container reaches it, ``sNumber`` the display
    number the cookie entry names. ``securityUnsupported`` and
    ``libraryMissing`` fall back to the server's own cookie; every other
    failure records itself and leaves the container without X11.
    """
    sDirectory = fsAuthorizationDirectory(sContainerName)
    listCommon = _flistCommonArguments(sDirectory, sDisplay, sProbeDisplay, sNumber)
    dictOutcome = fdictRunHelper(["generate"] + listCommon)
    if dictOutcome["sOutcome"] in (
        x11Authority.S_OUTCOME_SECURITY_UNSUPPORTED,
        x11Authority.S_OUTCOME_LIBRARY_MISSING,
    ):
        dictOutcome = _fdictFallBackToServerCookie(listCommon, dictOutcome["sOutcome"])
    dictEntry = {
        "sProjectUuid": sProjectUuid, "sDisplay": sProbeDisplay,
        "sHostDisplay": sDisplay, "sNumber": sNumber, "sDirectory": sDirectory,
        "sCreatedAt": datetime.now(timezone.utc).isoformat(),
        "sLastOutcome": dictOutcome["sOutcome"],
        "sLastReason": str(dictOutcome.get("sReason", "")),
        "bRenewed": False,
    }
    if dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_GENERATED:
        dictEntry.update({
            "iAuthorizationId": int(dictOutcome["iAuthorizationId"]),
            "sTrustLevel": S_TRUST_UNTRUSTED,
        })
    elif dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_COPIED:
        dictEntry.update({
            "iAuthorizationId": 0, "sTrustLevel": S_TRUST_SERVER_COOKIE,
            "sFallbackCause": dictOutcome.get("sFallbackCause", ""),
        })
    _fnUpdateRecord(sContainerName, dictEntry)
    return dict(dictOutcome, sDirectory=sDirectory)


def _fdictFallBackToServerCookie(listCommon, sCause):
    dictOutcome = fdictRunHelper(["copy-server-cookie"] + listCommon)
    dictOutcome["sFallbackCause"] = sCause
    return dictOutcome


def fbAuthorizationUsable(dictOutcome):
    """True when a prepared authorization gives the container a display."""
    return dictOutcome.get("sOutcome") in (
        x11Authority.S_OUTCOME_GENERATED, x11Authority.S_OUTCOME_COPIED,
    )


def flistMountArguments(sDirectory):
    """Return the docker run arguments mounting the directory read-only."""
    return [
        "--mount",
        f"type=bind,source={sDirectory},target={S_CONTAINER_AUTHORITY_DIRECTORY},readonly",
        "-e", f"XAUTHORITY={S_CONTAINER_AUTHORITY_PATH}",
    ]


def fdictReleaseAuthorization(sContainerName):
    """Probe, then revoke; delete the directory; drop the record.

    Returns the helper's outcome. ``serverUnreachable`` keeps the record
    and the directory for a retry; a fallback (server-cookie) entry has
    nothing to revoke and is simply removed.
    """
    dictEntry = fdictReadAuthorizations().get(sContainerName)
    if dictEntry is None:
        return {"sOutcome": x11Authority.S_OUTCOME_GONE, "sReason": "no record"}
    dictOutcome = _fdictRevokeIfUntrusted(dictEntry)
    if dictOutcome["sOutcome"] == x11Authority.S_OUTCOME_SERVER_UNREACHABLE:
        _fnUpdateRecord(sContainerName, {
            "sLastOutcome": "undetermined", "bReleasePending": True,
        })
        return dictOutcome
    shutil.rmtree(dictEntry.get("sDirectory") or fsAuthorizationDirectory(sContainerName),
                  ignore_errors=True)
    _fnDropRecord(sContainerName)
    return dictOutcome


def _fdictRevokeIfUntrusted(dictEntry):
    if dictEntry.get("sTrustLevel") != S_TRUST_UNTRUSTED:
        return {"sOutcome": x11Authority.S_OUTCOME_GONE,
                "sReason": "a copied server cookie has no authorization to revoke"}
    return fdictRunHelper(["revoke", "--authorization-id", str(dictEntry.get("iAuthorizationId", 0))]
                          + _flistCommonArguments(
                              dictEntry["sDirectory"], dictEntry.get("sHostDisplay", ""),
                              dictEntry.get("sDisplay", ""), dictEntry.get("sNumber", "0")))


def fnReleaseAuthorizationQuietly(sContainerName):
    """Release at a stop or removal; a failure is logged, never the caller's."""
    try:
        if sContainerName in fdictReadAuthorizations():
            fdictReleaseAuthorization(sContainerName)
    except Exception:  # noqa: BLE001 -- a stop never fails on its X11 cleanup
        logger.warning("X11 authorization release failed for %s", sContainerName,
                       exc_info=True)


def fdictRenewIfGone(sContainerName):
    """Probe one container's authorization; renew it when the server lost it.

    Live: nothing changes. Gone (an X server restart): a new
    authorization is generated and the cookie file replaced by rename,
    so the running container sees it; the old id is never revoked,
    because the restarted server may have handed it to someone else.
    """
    dictEntry = fdictReadAuthorizations().get(sContainerName)
    if dictEntry is None:
        return {"sOutcome": x11Authority.S_OUTCOME_GONE, "sReason": "no record"}
    listCommon = _flistCommonArguments(
        dictEntry["sDirectory"], dictEntry.get("sHostDisplay", ""),
        dictEntry.get("sDisplay", ""), dictEntry.get("sNumber", "0"),
    )
    dictProbe = fdictRunHelper(["probe"] + listCommon)
    if dictProbe["sOutcome"] != x11Authority.S_OUTCOME_REFUSED:
        return dictProbe
    dictOutcome = fdictPrepareAuthorization(
        sContainerName, dictEntry.get("sProjectUuid", ""),
        dictEntry.get("sHostDisplay", ""), dictEntry.get("sDisplay", ""),
        dictEntry.get("sNumber", "0"),
    )
    if fbAuthorizationUsable(dictOutcome):
        _fnUpdateRecord(sContainerName, {"bRenewed": True})
        logger.info("X11 authorization renewed for %s after an X server restart",
                    sContainerName)
    return dictOutcome


def fdictReconcile(fbContainerExists):
    """Settle every recorded authorization: at the hub's start, on its cadence, at its stop.

    A record whose container is gone is released (probe, then revoke);
    a live container's authorization is kept when the probe accepts it
    and renewed when the server lost it. Returns ``{name: outcome}``.
    """
    dictOutcomes = {}
    for sContainerName in sorted(fdictReadAuthorizations()):
        try:
            if fbContainerExists(sContainerName):
                dictOutcomes[sContainerName] = fdictRenewIfGone(sContainerName)["sOutcome"]
            else:
                dictOutcomes[sContainerName] = fdictReleaseAuthorization(sContainerName)["sOutcome"]
        except Exception as error:  # noqa: BLE001 -- one record never blocks the rest
            logger.warning("X11 reconcile failed for %s", sContainerName, exc_info=True)
            dictOutcomes[sContainerName] = f"error: {error}"
    return dictOutcomes


def flistDescribeAuthorization(sContainerName):
    """Return the sentences the dashboard and doctor show about one container's X11."""
    dictEntry = fdictReadAuthorizations().get(sContainerName)
    if dictEntry is None:
        return []
    listLines = []
    if dictEntry.get("sTrustLevel") == S_TRUST_SERVER_COOKIE:
        listLines.append(S_SERVER_COOKIE_NOTICE)
    elif dictEntry.get("sLastOutcome") not in (
        x11Authority.S_OUTCOME_GENERATED, x11Authority.S_OUTCOME_COPIED,
    ):
        listLines.append(
            "X11 forwarding could not be set up for this container "
            f"({dictEntry.get('sLastOutcome')}: {dictEntry.get('sLastReason') or 'no detail'}); "
            "it runs without a display until the next start."
        )
    if dictEntry.get("bRenewed"):
        listLines.append(_S_RENEWED_NOTICE)
    return listLines
