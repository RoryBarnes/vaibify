"""Shared utilities for parsing pipeline commands."""

import os


DICT_COMMAND_PREFIXES = {
    "python": "python",
    "python3": "python",
    "Rscript": "r",
    "julia": "julia",
    "matlab": "matlab",
    "perl": "perl",
    "bash": "shell",
    "sh": "shell",
    "node": "javascript",
}

DICT_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".R": "r",
    ".r": "r",
    ".c": "c",
    ".cpp": "c",
    ".f90": "fortran",
    ".rs": "rust",
    ".js": "javascript",
    ".ts": "javascript",
    ".pl": "perl",
    ".sh": "shell",
    ".jl": "julia",
    ".m": "matlab",
}


def fsExtractScriptPath(sCommand):
    """Extract the Python script path from a command string.

    Returns the script filename or empty string if not a Python command.
    """
    listTokens = sCommand.strip().split()
    if not listTokens:
        return ""
    if listTokens[0] in ("python", "python3") and len(listTokens) > 1:
        return listTokens[1]
    if listTokens[0].endswith(".py"):
        return listTokens[0]
    return ""


def ftExtractScriptPathForLanguage(sCommand):
    """Return (sScriptPath, sLanguage) for a command string."""
    listTokens = sCommand.strip().split()
    if not listTokens:
        return ("", "unknown")
    sFirstToken = listTokens[0]
    if sFirstToken in DICT_COMMAND_PREFIXES:
        sLanguage = DICT_COMMAND_PREFIXES[sFirstToken]
        sScriptPath = listTokens[1] if len(listTokens) > 1 else ""
        return (sScriptPath, sLanguage)
    sExtension = os.path.splitext(sFirstToken)[1]
    if sExtension in DICT_EXTENSION_TO_LANGUAGE:
        return (sFirstToken, DICT_EXTENSION_TO_LANGUAGE[sExtension])
    return ("", "unknown")


def flistExtractScripts(listCommands):
    """Return a list of unique script paths from a command list."""
    listScripts = []
    setAdded = set()
    for sCommand in listCommands:
        sScript = fsExtractScriptPath(sCommand)
        if sScript and sScript not in setAdded:
            listScripts.append(sScript)
            setAdded.add(sScript)
    return listScripts
