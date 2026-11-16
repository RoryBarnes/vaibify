"""Which agent chat a terminal holds, read back from the terminal's own record.

Every terminal the dashboard opens carries a terminal id the browser
mints. The hub hands the shell, through its exec environment, the path
of an append-only record named for that id
(:data:`vaibify.config.terminalRcfile.S_LIFECYCLE_ENVIRONMENT_VARIABLE`),
and the agent wrappers in the terminal's rcfile append one JSON line per
event to it: a ``start`` when a chat begins, Claude Code's own
``SessionStart`` payload when the conversation on screen changes, and an
``exit`` when the CLI returns to bash. The path is resolved per mode by
:func:`vaibify.gui.projectRoots.fsResolveScratchDirectory`, never a
literal, and the record is read back with the existing small-file typed
read, so no new operation name joins the typed-read table.

The rules for turning lines into a chat:

- the last ``start`` with no ``exit`` after it is the chat; an ended chat
  restores as a plain shell;
- a ``SessionStart`` line replaces that chat's session id, but only for
  a Claude chat whose wrapper supplied the hook;
- a CLI killed by leaving the dashboard wrote no ``exit``, so it counts
  as running at leave time.

The record is written by the researcher's uid, which an in-container
agent shares, so every field is validated (a UUID, a known provider, a
working directory inside the workspace, bounded arguments) and the
result is DISCOVERED data, never verified: validation stops malformed
commands and establishes nothing about who wrote the line.

Identity classes (Stage 0 matrix, plan section 13):

- ``tracked``: a launch id plus the switch hook, so the id follows a
  switch inside the CLI (Claude, where its help offers ``--settings``);
- ``launched``: a launch id with no switch signal (Gemini, a Codex
  resume, a Claude too old for the hook) -- resumed only past a caveat;
- ``inferred``: no id at all (a Codex launch, ``claude --continue``
  without the hook) -- never resumed automatically.
"""

__all__ = [
    "I_MAXIMUM_TERMINAL_IDS",
    "S_CLASS_INFERRED",
    "S_CLASS_LAUNCHED",
    "S_CLASS_TRACKED",
    "SET_AGENT_PROVIDERS",
    "fbIsSessionId",
    "fbIsTerminalId",
    "fdictCurrentChatFromRecord",
    "fdictLifecycleEnvironment",
    "fdictReadTerminalChats",
    "fsLifecyclePathFor",
]

import json
import posixpath
import re

from vaibify.config.terminalRcfile import S_LIFECYCLE_ENVIRONMENT_VARIABLE

from . import projectRoots

S_CLASS_TRACKED = "tracked"
S_CLASS_LAUNCHED = "launched"
S_CLASS_INFERRED = "inferred"
SET_AGENT_PROVIDERS = frozenset({"claude", "codex", "gemini"})
I_MAXIMUM_TERMINAL_IDS = 64
I_MAXIMUM_ARGUMENTS = 64
I_MAXIMUM_ARGUMENT_LENGTH = 4096
I_MAXIMUM_PATH_LENGTH = 4096
_S_CONTAINER_SCRATCH_ROOT = "/tmp"
_REGEX_TERMINAL_ID = re.compile(r"^[0-9a-f]{32}$")
_REGEX_SESSION_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def fbIsTerminalId(sTerminalId):
    """Return True for a terminal id the browser could have minted."""
    return isinstance(sTerminalId, str) and bool(
        _REGEX_TERMINAL_ID.match(sTerminalId))


def fbIsSessionId(sSessionId):
    """Return True for a lowercase UUID, the only session id ever typed."""
    return isinstance(sSessionId, str) and bool(
        _REGEX_SESSION_ID.match(sSessionId))


def fsLifecyclePathFor(sResourceId, sTerminalId):
    """Return where one terminal's lifecycle record lives, in either mode."""
    if not fbIsTerminalId(sTerminalId):
        raise ValueError(f"not a terminal id: {sTerminalId!r}")
    sScratchDirectory = projectRoots.fsResolveScratchDirectory(
        sResourceId, "terminal-" + sTerminalId, _S_CONTAINER_SCRATCH_ROOT,
    )
    return posixpath.join(
        sScratchDirectory, "vaibify-terminal-" + sTerminalId + ".jsonl",
    )


def fdictLifecycleEnvironment(sResourceId, sTerminalId):
    """Return the shell's environment overlay; empty for no terminal id.

    A terminal opened without an id (an older page) simply records
    nothing, and its chats are never offered for resuming.
    """
    if not fbIsTerminalId(sTerminalId):
        return {}
    return {S_LIFECYCLE_ENVIRONMENT_VARIABLE: fsLifecyclePathFor(
        sResourceId, sTerminalId)}


def _flistParseLines(baContent):
    listEvents = []
    for baLine in (baContent or b"").splitlines():
        try:
            dictEvent = json.loads(baLine.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(dictEvent, dict):
            listEvents.append(dictEvent)
    return listEvents


def _fdictStartedChat(dictEvent):
    return {
        "sProvider": dictEvent.get("sProvider"),
        "sSessionId": str(dictEvent.get("sSessionId") or ""),
        "bSwitchHook": dictEvent.get("bSwitchHook") is True,
        "sCwd": dictEvent.get("sCwd"),
        "listArguments": dictEvent.get("listArguments"),
        "iStartedEpoch": dictEvent.get("iStartedEpoch"),
        "bEnded": False,
    }


def _fnApplySwitch(dictChat, dictEvent):
    """A SessionStart payload names the conversation now on screen."""
    if (dictChat is None or dictChat["bEnded"]
            or dictChat["sProvider"] != "claude" or not dictChat["bSwitchHook"]):
        return
    sSessionId = dictEvent.get("session_id")
    if fbIsSessionId(sSessionId):
        dictChat["sSessionId"] = sSessionId


def _fbPathInside(sPath, sRoot):
    if (not isinstance(sPath, str) or not sPath.startswith("/")
            or len(sPath) > I_MAXIMUM_PATH_LENGTH or "\x00" in sPath
            or posixpath.normpath(sPath) != sPath):
        return False
    sRootNormal = posixpath.normpath(sRoot)
    return sPath == sRootNormal or sPath.startswith(
        sRootNormal.rstrip("/") + "/")


def _fbArgumentsValid(listArguments):
    return isinstance(listArguments, list) and len(
        listArguments) <= I_MAXIMUM_ARGUMENTS and all(
            isinstance(sArgument, str)
            and len(sArgument) <= I_MAXIMUM_ARGUMENT_LENGTH
            and "\x00" not in sArgument
            for sArgument in listArguments)


def _fsIdentityClass(dictChat):
    if not dictChat["sSessionId"]:
        return S_CLASS_INFERRED
    if dictChat["sProvider"] == "claude" and dictChat["bSwitchHook"]:
        return S_CLASS_TRACKED
    return S_CLASS_LAUNCHED


def _fdictValidatedChat(dictChat, sWorkspaceRoot):
    """Return the chat as the hub will use it, or None when malformed."""
    if dictChat["sProvider"] not in SET_AGENT_PROVIDERS:
        return None
    if dictChat["sSessionId"] and not fbIsSessionId(dictChat["sSessionId"]):
        return None
    if not _fbPathInside(dictChat["sCwd"], sWorkspaceRoot):
        return None
    if not _fbArgumentsValid(dictChat["listArguments"]):
        return None
    iStarted = dictChat["iStartedEpoch"]
    return {
        "sProvider": dictChat["sProvider"],
        "sSessionId": dictChat["sSessionId"],
        "sCwd": dictChat["sCwd"],
        "listArguments": list(dictChat["listArguments"]),
        "sIdentityClass": _fsIdentityClass(dictChat),
        "sState": "running",
        "iStartedEpoch": iStarted if isinstance(iStarted, int) else 0,
    }


def fdictCurrentChatFromRecord(baContent, sWorkspaceRoot):
    """Return the chat a terminal holds now, or None for a plain shell."""
    dictChat = None
    for dictEvent in _flistParseLines(baContent):
        if dictEvent.get("sEvent") == "start":
            dictChat = _fdictStartedChat(dictEvent)
        elif dictEvent.get("sEvent") == "exit":
            if dictChat is not None:
                dictChat["bEnded"] = True
        elif dictEvent.get("hook_event_name") == "SessionStart":
            _fnApplySwitch(dictChat, dictEvent)
    if dictChat is None or dictChat["bEnded"]:
        return None
    return _fdictValidatedChat(dictChat, sWorkspaceRoot)


def fdictReadTerminalChats(connection, sResourceId, listTerminalIds,
                           sWorkspaceRoot):
    """Return ``{sTerminalId: chat or None}`` for the given terminals.

    One batched small-file typed read; a record that does not exist
    (the terminal never ran a chat) reads as a plain shell.
    """
    dictPathById = {
        sTerminalId: fsLifecyclePathFor(sResourceId, sTerminalId)
        for sTerminalId in listTerminalIds
    }
    dictContents = connection.fdictFetchSmallFiles(
        sResourceId, list(dictPathById.values()),
    )
    return {
        sTerminalId: fdictCurrentChatFromRecord(
            dictContents.get(sPath) if isinstance(
                dictContents.get(sPath), bytes) else None,
            sWorkspaceRoot,
        )
        for sTerminalId, sPath in dictPathById.items()
    }
