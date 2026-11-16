"""The researcher's approval of what a container may reach, held on this host.

A container's network mode, its display forwarding, its bind mounts and
its allowed hosts are read from ``vaibify.yml`` when the container is
created, and that file can be changed by a hand that is not vaibify's:
a text edit, or a commit an agent made in its clone and the researcher
pulled. ``docs/security.md`` ("Declared bind mounts") names the class:
a container that could edit the file declaring its own confinement
would choose that confinement at the next start. It closed the
bind-mount path; this module closes the git path the same way the
repository already treats images it did not build (``imageTrust``): a
record in the host registry, keyed by the project, holding a
fingerprint of the settings it approves, compared at every creation,
set only through a route the in-container agent cannot reach, and
recorded automatically when a researcher-only route writes those
settings (ruling D2, 2026-11-04).

THE RULE
--------

A configuration that widens nothing needs no record, as an image
vaibify built needs no trust answer. One that widens anything --
``networkMode: open``, ``x11Forwarding``, a ``bindMounts`` entry, an
``allowedHosts`` entry -- is refused by every launch path until the
project's record covers exactly its current values. Vaibify's own
writes (the settings dialog, the allow-host button, project creation,
conversion) record the approval as they write, because each of those
routes is already researcher-only, so the call IS the approval; the
one path that prompts is a change to the file made some other way,
once per change, and that is the point.
"""

__all__ = [
    "ReachApprovalRequiredError",
    "S_REACH_PROMPT_TITLE",
    "T_REACH_WIDENING_FIELDS",
    "fbApprovalCoversConfiguration",
    "fbConfigurationWidensReach",
    "fdictBuildReachApprovalRecord",
    "fdictBuildReachPrompt",
    "fdictDescribeReachSettings",
    "fnRecordApprovalForConfigurationFile",
    "fsComputeReachFingerprint",
]

import hashlib
import json
from datetime import datetime, timezone

from vaibify.config.configurationFingerprint import (
    fjsonNormalizeConfigurationValue,
)

# The fields whose value, read from vaibify.yml at creation, lets the
# container reach something it otherwise could not: a host service
# (open mode), the display (X11), a host path (a bind mount), a network
# host (an allowed host). That is the test of membership. Resource
# limits, ports and secrets are not on it: a port is a listener, not a
# reach, and secrets are already mounted only under the image-trust
# answer.
T_REACH_WIDENING_FIELDS = (
    "sNetworkMode", "bX11Forwarding", "listBindMounts", "listAllowedHosts",
)
_DICT_FIELD_DEFAULTS = {
    "sNetworkMode": "", "bX11Forwarding": False,
    "listBindMounts": [], "listAllowedHosts": [],
}
S_NETWORK_MODE_OPEN = "open"
S_REACH_PROMPT_TITLE = "Approve what this container may reach?"


class ReachApprovalRequiredError(RuntimeError):
    """The file widens the container's reach and no record covers it."""

    def __init__(self, sProjectName, dictPrompt):
        self.sProjectName = sProjectName
        self.dictPrompt = dictPrompt
        super().__init__(
            f"'{sProjectName}' cannot be started: {dictPrompt['sText']} "
            "Approve in the dashboard, or pass --approve-reach to "
            "vaibify start."
        )


def fdictDescribeReachSettings(config):
    """Return the four widening fields in plain form."""
    return {
        sField: fjsonNormalizeConfigurationValue(
            getattr(config, sField, _DICT_FIELD_DEFAULTS[sField]))
        for sField in T_REACH_WIDENING_FIELDS
    }


def fbConfigurationWidensReach(config):
    """True when any widening field holds a value that reaches further."""
    dictSettings = fdictDescribeReachSettings(config)
    return bool(
        dictSettings["sNetworkMode"] == S_NETWORK_MODE_OPEN
        or dictSettings["bX11Forwarding"]
        or dictSettings["listBindMounts"]
        or dictSettings["listAllowedHosts"]
    )


def fsComputeReachFingerprint(config):
    """Return the SHA-256 of the canonical JSON of the four fields."""
    sCanonical = json.dumps(
        fdictDescribeReachSettings(config), sort_keys=True,
        separators=(",", ":"))
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def fdictBuildReachApprovalRecord(config):
    """Return the record stored for an approval of this configuration."""
    return {
        "sReachFingerprint": fsComputeReachFingerprint(config),
        "sRecordedIso": datetime.now(timezone.utc).isoformat(),
        "dictReachSettings": fdictDescribeReachSettings(config),
    }


def fbApprovalCoversConfiguration(dictRecord, config):
    """True when the stored fingerprint equals the configuration's."""
    return bool(dictRecord) and (
        dictRecord.get("sReachFingerprint") == fsComputeReachFingerprint(config))


def _fsApprovalDate(dictRecord):
    """Return the day the record was made, for a sentence."""
    return str((dictRecord or {}).get("sRecordedIso") or "")[:10] or "an unknown date"


def _fsStatusOfField(sField, dictSettings, dictRecord):
    """Return the parenthetical for one bullet: approved, changed, or not yet."""
    if not dictRecord:
        return "not yet approved"
    dictApproved = dictRecord.get("dictReachSettings") or {}
    if sField in dictApproved and dictApproved[sField] == dictSettings[sField]:
        return f"approved on {_fsApprovalDate(dictRecord)}"
    return f"changed since you approved it on {_fsApprovalDate(dictRecord)}"


def _flistBuildReachBullets(dictSettings, dictRecord):
    """Return one bullet per widening field that applies, with its status."""
    listBullets = []
    if dictSettings["sNetworkMode"] == S_NETWORK_MODE_OPEN:
        listBullets.append(
            "Network mode open: anything running in the container, "
            "including an AI agent, may be able to reach services on this "
            "computer (loopback-only ones included on macOS under Colima) "
            "and on your network. Web mode reaches the public internet "
            "without exposing this computer "
            f"({_fsStatusOfField('sNetworkMode', dictSettings, dictRecord)}).")
    if dictSettings["bX11Forwarding"]:
        listBullets.append(
            "X11 forwarding: programs in the container may open windows "
            "on your display "
            f"({_fsStatusOfField('bX11Forwarding', dictSettings, dictRecord)}).")
    if dictSettings["listBindMounts"]:
        listBullets.append(
            "Bind mounts: " + ", ".join(
                _fsDescribeBindMount(dictMount)
                for dictMount in dictSettings["listBindMounts"])
            + f" ({_fsStatusOfField('listBindMounts', dictSettings, dictRecord)}).")
    if dictSettings["listAllowedHosts"]:
        listBullets.append(
            "Allowed hosts: " + ", ".join(
                str(sHost) for sHost in dictSettings["listAllowedHosts"])
            + f" ({_fsStatusOfField('listAllowedHosts', dictSettings, dictRecord)}).")
    return listBullets


def _fsDescribeBindMount(dictMount):
    """Return one bind mount as ``host path (read-write)``."""
    from vaibify.config.bindMountValidator import fbBindMountIsReadWrite
    sHost = str((dictMount or {}).get("host") or (dictMount or {}).get("source") or "?")
    sMode = "read-write" if fbBindMountIsReadWrite(dictMount or {}) else "read-only"
    return f"{sHost} ({sMode})"


def _fsOriginSentence(dictRecord, bRegistered):
    """Return the sentence about where these settings came from."""
    if not bRegistered:
        return (
            "This project is not registered on this computer, so there is "
            "nowhere to record your approval; register it first.")
    if dictRecord:
        return (
            "These were written to vaibify.yml by something other than this "
            "dashboard: a text editor, or a commit pulled into the project. "
            "Approve them only if you made or expected these changes. You "
            "will be asked again only when they change.")
    return (
        "This project has not yet been approved on this computer. You will "
        "be asked again only when these settings change.")


def fdictBuildReachPrompt(config, dictRecord, bRegistered=True):
    """Return the one text the GUI modal and the command line both show.

    ``listBullets`` names each widening field that applies with what it
    exposes and whether it is approved, changed or not yet approved;
    ``sText`` is the whole prompt as one string; ``sReachFingerprint`` is
    what an answer must name, so a file changed after the question was
    shown is refused rather than approved blind.
    """
    dictSettings = fdictDescribeReachSettings(config)
    listBullets = _flistBuildReachBullets(dictSettings, dictRecord)
    sIntro = (
        "vaibify.yml asks for settings that widen what the container "
        "reaches, and " + (
            f"they changed since you approved them on {_fsApprovalDate(dictRecord)}:"
            if dictRecord else
            "this computer has no record that you approved them:"))
    sText = "\n".join(
        [sIntro] + [f"• {sBullet}" for sBullet in listBullets]
        + [_fsOriginSentence(dictRecord, bRegistered)])
    return {
        "sTitle": S_REACH_PROMPT_TITLE,
        "sText": sText,
        "listBullets": listBullets,
        "sReachFingerprint": fsComputeReachFingerprint(config),
        "dictReachSettings": dictSettings,
        "bRegistered": bool(bRegistered),
        "bAnswerCoversConfiguration": fbApprovalCoversConfiguration(
            dictRecord, config),
        "dictCurrentAnswer": dict(dictRecord or {}),
    }


def fnRecordApprovalForConfigurationFile(sName, sConfigPath):
    """Record approval for the file as vaibify just wrote it.

    Called at the end of every researcher-only route that writes a
    widening field: the route being researcher-only is what makes the
    call an approval. A file that widens nothing leaves no record (any
    stale one is removed), so a later edit that widens is asked about.
    """
    from vaibify.config.projectConfig import fconfigLoadFromFile
    from vaibify.config.registryManager import fnRecordReachApproval
    config = fconfigLoadFromFile(sConfigPath)
    fnRecordReachApproval(
        sName,
        fdictBuildReachApprovalRecord(config)
        if fbConfigurationWidensReach(config) else None,
    )
