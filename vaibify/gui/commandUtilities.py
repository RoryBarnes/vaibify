"""Shared utilities for parsing pipeline commands."""

import os


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
