"""The one line a remote helper speaks, and how a client validates it.

The local client learns everything it needs from a single JSON object
on the helper's stdout. Every field in it arrives from another machine
and is therefore untrusted until checked here, which is why the schema
is small: each field is one more thing an attacker-controlled or
merely mismatched remote can say.

The rule that shapes the whole module: **the client never opens a URL
the remote sent it.** It builds the URL from the port it already chose
and forwarded, with the scheme and host fixed to loopback. A remote
that could name the URL could turn "open the dashboard" into "open
anything", so no URL-shaped field exists in the schema at all.

Stdout is protocol. Diagnostics go to stderr. A helper that mixes them
corrupts its own record, so the record is bounded and parsed as
exactly one object.
"""

import json
import re

# Bumped only when the client must refuse an older or newer helper.
# The client demands an exact match: guessing how to drive a protocol
# it does not know is how a "compatible" remote becomes an incident.
I_PROTOCOL_VERSION = 1

# A whole record must fit in this. It carries one capability and four
# short scalars; anything larger is a helper whose stderr leaked into
# its stdout, or a remote saying something it should not.
I_MAX_RECORD_BYTES = 4096

# secrets.token_urlsafe(32) — base64url, no padding.
_RE_CAPABILITY = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

# A hostname is DISPLAY TEXT and nothing else. It is never resolved,
# never dialed, and never joined into a path or a command; it exists so
# the dashboard can say which machine the researcher is looking at.
_RE_HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")

T_EXECUTION_MODES = ("docker", "host")

# Direct execution is the only placement implemented. A scheduled
# placement (an allocation whose lifecycle vaibify does not own) is a
# separate product surface; a client that met one and guessed would be
# claiming authority over work it cannot see.
T_EXECUTION_PLACEMENTS = ("direct",)

# The two things a capability can be. A client that could not tell
# them apart would put a transfer capability in a #bootstrap fragment,
# where the bootstrap lane refuses it outright -- deliberately, because
# redeeming a transfer without committing the ownership hand-over would
# be a bare credential with the transfer skipped.
S_CAPABILITY_BOOTSTRAP = "bootstrap"
S_CAPABILITY_TRANSFER = "transfer"
T_CAPABILITY_KINDS = (S_CAPABILITY_BOOTSTRAP, S_CAPABILITY_TRANSFER)

S_STARTUP_RECORD_PREFIX = "VAIBIFY-REMOTE "

__all__ = [
    "I_MAX_RECORD_BYTES",
    "I_PROTOCOL_VERSION",
    "RemoteProtocolError",
    "S_CAPABILITY_BOOTSTRAP",
    "S_CAPABILITY_TRANSFER",
    "S_STARTUP_RECORD_PREFIX",
    "T_EXECUTION_MODES",
    "T_EXECUTION_PLACEMENTS",
    "fdictParseStartupRecord",
    "fsFormatStartupRecord",
    "fsLocalDashboardUrl",
]


class RemoteProtocolError(Exception):
    """A startup record could not be trusted. Never a transport fault."""


def fsFormatStartupRecord(
    iPort, sBootstrapCapability, sExecutionMode, sHostname,
    sExecutionPlacement="direct", sCapabilityKind=S_CAPABILITY_BOOTSTRAP,
    sReattachedContainerName="",
):
    """Return the single stdout line a remote helper emits.

    The capability field carries one of two KINDS. A bootstrap signs a
    fresh browser in; a transfer hands a session that is already the
    researcher's back to a new browser, which is what a return after
    the hold window needs -- the old credential is revoked but the
    project, its flock and its running work are all still theirs.
    """
    return S_STARTUP_RECORD_PREFIX + json.dumps({
        "iProtocolVersion": I_PROTOCOL_VERSION,
        "iPort": iPort,
        "sBootstrapCapability": sBootstrapCapability,
        "sCapabilityKind": sCapabilityKind,
        "sReattachedContainerName": sReattachedContainerName,
        "sExecutionMode": sExecutionMode,
        "sExecutionPlacement": sExecutionPlacement,
        "sHostname": sHostname,
    }, separators=(",", ":"), sort_keys=True)


def fdictParseStartupRecord(sLine, iExpectedPort):
    """Return the validated record, or raise RemoteProtocolError.

    ``iExpectedPort`` is the port the CLIENT chose and forwarded. A
    record naming any other port is refused rather than followed: the
    forward already exists, so a different port would be one the
    browser cannot reach and the Host check would reject anyway.
    """
    sPayload = _fsExtractPayload(sLine)
    try:
        dictRecord = json.loads(sPayload)
    except ValueError as error:
        raise RemoteProtocolError(
            f"the remote's startup record was not valid JSON: {error}",
        )
    if not isinstance(dictRecord, dict):
        raise RemoteProtocolError(
            "the remote's startup record was not a JSON object",
        )
    _fnValidateProtocolVersion(dictRecord)
    _fnValidatePort(dictRecord, iExpectedPort)
    _fnValidateCapability(dictRecord)
    _fnValidateVocabularyFields(dictRecord)
    _fnValidateCapabilityKind(dictRecord)
    _fnValidateHostname(dictRecord)
    return dictRecord


def _fsExtractPayload(sLine):
    """Return the JSON text of the one record line, bounds checked."""
    if sLine is None:
        raise RemoteProtocolError(
            "the remote produced no startup record at all; it may not "
            "have vaibify on its non-interactive PATH",
        )
    if len(sLine.encode("utf-8")) > I_MAX_RECORD_BYTES:
        raise RemoteProtocolError(
            "the remote's startup record was oversized "
            f"(over {I_MAX_RECORD_BYTES} bytes)",
        )
    sStripped = sLine.strip()
    if not sStripped.startswith(S_STARTUP_RECORD_PREFIX):
        raise RemoteProtocolError(
            "the remote's output did not begin with a vaibify startup "
            "record; the line was not protocol",
        )
    sPayload = sStripped[len(S_STARTUP_RECORD_PREFIX):]
    if "\n" in sPayload or "\r" in sPayload:
        raise RemoteProtocolError(
            "the remote's startup record spanned several lines",
        )
    return sPayload


def _fnValidateProtocolVersion(dictRecord):
    """Refuse anything but the exact supported version."""
    jsonVersion = dictRecord.get("iProtocolVersion")
    if jsonVersion != I_PROTOCOL_VERSION:
        raise RemoteProtocolError(
            f"the remote speaks protocol version {jsonVersion!r} and "
            f"this vaibify speaks {I_PROTOCOL_VERSION}. Install "
            "matching versions on both machines.",
        )


def _fnValidatePort(dictRecord, iExpectedPort):
    """The remote must have bound the port the client forwarded."""
    jsonPort = dictRecord.get("iPort")
    if jsonPort != iExpectedPort:
        raise RemoteProtocolError(
            f"the remote bound port {jsonPort!r} but the tunnel "
            f"forwards {iExpectedPort}; the dashboard would be "
            "unreachable.",
        )


def _fnValidateCapability(dictRecord):
    """A capability is a bounded base64url token or nothing."""
    jsonCapability = dictRecord.get("sBootstrapCapability")
    if not isinstance(jsonCapability, str):
        raise RemoteProtocolError(
            "the remote sent no bootstrap capability",
        )
    if not _RE_CAPABILITY.match(jsonCapability):
        # An EMPTY capability is the specific shape a hub at its armed
        # cap produces, and it fails opaquely at redemption. Naming it
        # here turns a 401 nobody can explain into a sentence.
        if jsonCapability == "":
            raise RemoteProtocolError(
                "the remote hub minted an empty capability, which "
                "means it is at its limit of outstanding sign-in "
                "links. Wait a few minutes and try again.",
            )
        raise RemoteProtocolError(
            "the remote's bootstrap capability was not the expected "
            "shape",
        )


def _fnValidateVocabularyFields(dictRecord):
    """Execution mode and placement come from closed vocabularies."""
    jsonMode = dictRecord.get("sExecutionMode")
    if jsonMode not in T_EXECUTION_MODES:
        raise RemoteProtocolError(
            f"the remote reported execution mode {jsonMode!r}, which "
            f"this vaibify does not know. Known: {T_EXECUTION_MODES}",
        )
    jsonPlacement = dictRecord.get("sExecutionPlacement")
    if jsonPlacement not in T_EXECUTION_PLACEMENTS:
        raise RemoteProtocolError(
            f"the remote reported execution placement "
            f"{jsonPlacement!r}. This vaibify can only drive "
            "directly-executed work, and will not guess how to "
            "control an allocation it does not understand.",
        )


def _fnValidateCapabilityKind(dictRecord):
    """The kind decides which fragment, so it is closed vocabulary."""
    jsonKind = dictRecord.get("sCapabilityKind", S_CAPABILITY_BOOTSTRAP)
    if jsonKind not in T_CAPABILITY_KINDS:
        raise RemoteProtocolError(
            f"the remote sent capability kind {jsonKind!r}, which this "
            f"vaibify does not know. Known: {T_CAPABILITY_KINDS}",
        )
    jsonName = dictRecord.get("sReattachedContainerName", "")
    if not isinstance(jsonName, str):
        raise RemoteProtocolError(
            "the remote's reattached project name was not text",
        )


def _fnValidateHostname(dictRecord):
    """A hostname is display text; refuse anything that is not."""
    jsonHostname = dictRecord.get("sHostname")
    if not isinstance(jsonHostname, str):
        raise RemoteProtocolError("the remote sent no hostname")
    if not _RE_HOSTNAME.match(jsonHostname):
        raise RemoteProtocolError(
            "the remote's hostname was not a plain hostname",
        )


def fsLocalDashboardUrl(
    iPort, sBootstrapCapability,
    sCapabilityKind=S_CAPABILITY_BOOTSTRAP,
):
    """Return the loopback URL the local browser is sent to.

    Built entirely from values the CLIENT holds. The scheme and host
    are literals here and cannot be influenced by the remote, which is
    the point: a remote able to name this string could turn a browser
    launch into an arbitrary local navigation. The capability rides
    the FRAGMENT so it stays out of access logs and out of the
    terminal.
    """
    sFragment = (
        "transfer" if sCapabilityKind == S_CAPABILITY_TRANSFER
        else "bootstrap"
    )
    return (
        f"http://127.0.0.1:{int(iPort)}"
        f"/#{sFragment}={sBootstrapCapability}"
    )
