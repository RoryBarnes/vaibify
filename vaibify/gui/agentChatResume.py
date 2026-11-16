"""How a saved agent chat is resumed, and with what permissions.

Pure functions: nothing here reads a file or runs a program. Given a
chat (provider, session id, working directory and the arguments it was
launched with, all already validated by
:mod:`vaibify.gui.agentChatLifecycle` or the catalog), the provider's
permission defaults, and the mode its own transcript last recorded,
they decide

- which launch arguments are replayed: a fixed per-provider allow-list
  of flags with safe values. Everything else -- a prompt, an unknown
  flag, a value with shell syntax -- is dropped, and every drop is
  named so the modal can show it;
- the ONE effective permission mode, and whether it is bypass. Bypass
  from the flags, the provider's config defaults, or the mode the
  transcript last recorded all count (plan section 6, as amended by
  Stage 0), so the modal's gate errs toward asking;
- the resume command, from a fixed table with every argument quoted.
  A forged record can therefore choose which transcript a CLI opens,
  and nothing more.

"Resume with standard permissions" never just removes flags: it strips
the bypass flags AND adds the provider's explicit standard ones, because
Claude's ``--dangerously-skip-permissions`` beats
``--permission-mode default``, Codex's bypass flag beats its config, and
Gemini refuses ``--yolo`` beside ``--approval-mode`` (all measured,
plan section 13).
"""

__all__ = [
    "DICT_ALLOWED_FLAGS",
    "DICT_STANDARD_FLAGS",
    "fdictBuildResumePlan",
    "fdictEffectivePermission",
    "flistStandardFlagTokens",
    "fsComposeResumeCommand",
    "ftSplitLaunchArguments",
]

import posixpath
import re
import shlex

from .agentChatLifecycle import SET_AGENT_PROVIDERS, fbIsSessionId

# Flag -> how many values it takes. Only these are ever replayed.
DICT_ALLOWED_FLAGS = {
    "claude": {"--dangerously-skip-permissions": 0, "--permission-mode": 1,
               "--model": 1},
    "gemini": {"-y": 0, "--yolo": 0, "--approval-mode": 1, "-m": 1,
               "--model": 1},
    "codex": {"--dangerously-bypass-approvals-and-sandbox": 0, "-a": 1,
              "--ask-for-approval": 1, "-s": 1, "--sandbox": 1, "-m": 1,
              "--model": 1},
}
# Flags that choose WHICH conversation runs. The resume command names
# the conversation itself, so these are neither replayed nor reported.
_DICT_IDENTITY_FLAGS = {
    "claude": {"-r": 1, "--resume": 1, "-c": 0, "--continue": 0,
               "--session-id": 1, "--fork-session": 0},
    "gemini": {"-r": 1, "--resume": 1, "--session-id": 1},
    "codex": {},
}
_SET_PERMISSION_FLAGS = frozenset({
    "--dangerously-skip-permissions", "--permission-mode", "-y", "--yolo",
    "--approval-mode", "--dangerously-bypass-approvals-and-sandbox", "-a",
    "--ask-for-approval", "-s", "--sandbox",
})
DICT_STANDARD_FLAGS = {
    "claude": ["--permission-mode", "default"],
    "gemini": ["--approval-mode", "default"],
    "codex": ["-a", "on-request", "-s", "workspace-write"],
}
# The fixed resume table: the words before the session id.
_DICT_RESUME_WORDS = {
    "claude": ["claude", "--resume"],
    "gemini": ["gemini", "--resume"],
    "codex": ["codex", "resume"],
}
_REGEX_FLAG_VALUE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")


def _ftFlagAndInlineValue(sArgument):
    if sArgument.startswith("--") and "=" in sArgument:
        sFlag, sValue = sArgument.split("=", 1)
        return sFlag, sValue
    return sArgument, None


def _flistCodexArguments(listArguments):
    """A ``codex resume <id>`` launch: the subcommand and id are identity."""
    if listArguments[:1] == ["resume"]:
        iSkip = 2 if len(listArguments) > 1 and fbIsSessionId(
            listArguments[1].lower()) else 1
        return listArguments[iSkip:]
    return listArguments


def ftSplitLaunchArguments(sProvider, listArguments):
    """Return ``(listKeptPairs, listDropped)`` for a chat's launch arguments.

    ``listKeptPairs`` holds ``(sFlag, sValue or None)`` in order.
    """
    if sProvider == "codex":
        listArguments = _flistCodexArguments(list(listArguments))
    dictAllowed = DICT_ALLOWED_FLAGS[sProvider]
    dictIdentity = _DICT_IDENTITY_FLAGS[sProvider]
    listKept, listDropped = [], []
    iIndex = 0
    while iIndex < len(listArguments):
        sFlag, sInline = _ftFlagAndInlineValue(listArguments[iIndex])
        iIndex += 1
        if sFlag in dictIdentity:
            if dictIdentity[sFlag] and sInline is None:
                iIndex += 1
            continue
        if sFlag not in dictAllowed:
            listDropped.append(listArguments[iIndex - 1])
            continue
        if dictAllowed[sFlag] == 0:
            listKept.append((sFlag, None))
            continue
        sValue = sInline
        if sValue is None and iIndex < len(listArguments):
            sValue = listArguments[iIndex]
            iIndex += 1
        if sValue is None or not _REGEX_FLAG_VALUE.match(sValue):
            listDropped.append(sFlag + ("" if sValue is None else " " + sValue))
            continue
        listKept.append((sFlag, sValue))
    return listKept, listDropped


def _fsFlagValue(listKept, setNames):
    for sFlag, sValue in listKept:
        if sFlag in setNames:
            return sValue
    return None


def _fbHasFlag(listKept, setNames):
    return any(sFlag in setNames for sFlag, _ in listKept)


def _ftClaudePermission(listKept, dictConfig, sRecordedMode):
    bDanger = _fbHasFlag(listKept, {"--dangerously-skip-permissions"})
    sFlagMode = _fsFlagValue(listKept, {"--permission-mode"})
    sConfigMode = dictConfig.get("sClaudeDefaultMode") or ""
    sMode = ("bypassPermissions" if bDanger else sFlagMode) or sConfigMode or "default"
    return sMode, {
        "flags": bDanger or sFlagMode == "bypassPermissions",
        "config": not bDanger and not sFlagMode
        and sConfigMode == "bypassPermissions",
        "transcript": sRecordedMode == "bypassPermissions",
    }


def _ftGeminiPermission(listKept, dictConfig, sRecordedMode):
    del dictConfig, sRecordedMode
    sFlagMode = _fsFlagValue(listKept, {"--approval-mode"})
    bYolo = _fbHasFlag(listKept, {"-y", "--yolo"})
    sMode = "yolo" if bYolo else (sFlagMode or "default")
    return sMode, {"flags": sMode == "yolo", "config": False,
                   "transcript": False}


def _ftCodexPermission(listKept, dictConfig, sRecordedMode):
    bDanger = _fbHasFlag(
        listKept, {"--dangerously-bypass-approvals-and-sandbox"})
    sFlagApproval = _fsFlagValue(listKept, {"-a", "--ask-for-approval"})
    sFlagSandbox = _fsFlagValue(listKept, {"-s", "--sandbox"})
    sApproval = sFlagApproval or dictConfig.get("sCodexApprovalPolicy") or ""
    sSandbox = sFlagSandbox or dictConfig.get("sCodexSandboxMode") or ""
    bFlags = bDanger or (sFlagApproval == "never"
                         and sFlagSandbox == "danger-full-access")
    sMode = "bypass" if bDanger else (
        (sApproval or "default") + ":" + (sSandbox or "default"))
    return sMode, {
        "flags": bFlags,
        "config": not bFlags and sApproval == "never"
        and sSandbox == "danger-full-access",
        "transcript": sRecordedMode == "never:danger-full-access",
    }


_DICT_PERMISSION_RULES = {
    "claude": _ftClaudePermission,
    "gemini": _ftGeminiPermission,
    "codex": _ftCodexPermission,
}


def fdictEffectivePermission(sProvider, listKept, dictConfig, sRecordedMode):
    """Return ``{sMode, bBypass, listBypassSources}`` for one chat."""
    sMode, dictSources = _DICT_PERMISSION_RULES[sProvider](
        listKept, dictConfig or {}, sRecordedMode or "")
    listSources = [sName for sName in ("flags", "config", "transcript")
                   if dictSources[sName]]
    return {"sMode": sMode, "bBypass": bool(listSources),
            "listBypassSources": listSources}


def _flistTokens(listPairs):
    listTokens = []
    for sFlag, sValue in listPairs:
        listTokens.append(sFlag)
        if sValue is not None:
            listTokens.append(sValue)
    return listTokens


def flistStandardFlagTokens(sProvider, listKept):
    """The kept flags with every permission flag replaced by the standard pair."""
    listOthers = [(sFlag, sValue) for sFlag, sValue in listKept
                  if sFlag not in _SET_PERMISSION_FLAGS]
    return _flistTokens(listOthers) + list(DICT_STANDARD_FLAGS[sProvider])


def fsComposeResumeCommand(sProvider, sSessionId, sCwd, listFlagTokens):
    """Return the shell command that resumes one chat, every word quoted."""
    if sProvider not in SET_AGENT_PROVIDERS:
        raise ValueError(f"not an agent provider: {sProvider!r}")
    if not fbIsSessionId(sSessionId):
        raise ValueError("a chat with no session id is never resumed")
    if (not isinstance(sCwd, str) or not sCwd.startswith("/")
            or posixpath.normpath(sCwd) != sCwd):
        raise ValueError("the chat's directory is not a normalized path")
    listWords = _DICT_RESUME_WORDS[sProvider] + [sSessionId] + list(
        listFlagTokens)
    return "cd -- " + shlex.quote(sCwd) + " && " + " ".join(
        shlex.quote(sWord) for sWord in listWords)


def fdictBuildResumePlan(dictChat, dictConfig, sRecordedMode):
    """Return both resume commands, the permission verdict and the drops.

    ``dictChat`` carries sProvider, sSessionId, sCwd and listArguments.
    """
    sProvider = dictChat["sProvider"]
    listKept, listDropped = ftSplitLaunchArguments(
        sProvider, dictChat.get("listArguments") or [])
    return {
        "sResumeCommand": fsComposeResumeCommand(
            sProvider, dictChat["sSessionId"], dictChat["sCwd"],
            _flistTokens(listKept)),
        "sStandardCommand": fsComposeResumeCommand(
            sProvider, dictChat["sSessionId"], dictChat["sCwd"],
            flistStandardFlagTokens(sProvider, listKept)),
        "dictPermission": fdictEffectivePermission(
            sProvider, listKept, dictConfig, sRecordedMode),
        "listDroppedArguments": listDropped,
    }
