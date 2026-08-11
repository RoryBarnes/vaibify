"""Shared utilities for parsing pipeline commands."""

__all__ = [
    "DICT_COMMAND_PREFIXES",
    "DICT_EXTENSION_TO_LANGUAGE",
    "fsExtractScriptPath",
    "ftExtractScriptPathForLanguage",
    "flistExtractScripts",
]

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
    "gfortran": "fortran",
    "gcc": "c",
    "g++": "c",
    "rustc": "rust",
    "cargo": "rust",
    "go": "go",
    "ruby": "ruby",
    "php": "php",
}

DICT_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".R": "r",
    ".r": "r",
    ".c": "c",
    ".cpp": "c",
    ".h": "c",
    ".hpp": "c",
    ".f90": "fortran",
    ".f": "fortran",
    ".f95": "fortran",
    ".rs": "rust",
    ".js": "javascript",
    ".ts": "javascript",
    ".pl": "perl",
    ".pm": "perl",
    ".sh": "shell",
    ".bash": "shell",
    ".jl": "julia",
    ".m": "matlab",
    ".go": "go",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}


# Interpreter flags whose argument is CODE or a MODULE NAME, not a file
# on disk. `python3 -c "import time"` names no script, and treating the
# flag itself as one is how a perfectly ordinary command came to fail
# pre-flight with `command not found: -c`.
_SET_FLAGS_MEANING_NO_SCRIPT_FILE = frozenset({"-c", "-m", "-e"})

# Flags that consume the NEXT token as their own value, so that token is
# not the script either (`python -W ignore run.py`).
_SET_FLAGS_TAKING_A_VALUE = frozenset({
    "-W", "-X", "--check-hash-based-pycs",
})


def fsExtractScriptPathFromArguments(listArguments):
    """Return the script path among an interpreter's arguments, or "".

    An empty answer means "this command runs no script file" and is a
    real answer, not a failure: inline code (``-c``), a module
    (``-m``), and a bare interpreter all legitimately have none. Every
    caller must treat "" as "nothing to check" rather than falling back
    to a token, because the tokens here are flags and code.
    """
    iIndex = 0
    while iIndex < len(listArguments):
        sToken = listArguments[iIndex]
        if sToken in _SET_FLAGS_MEANING_NO_SCRIPT_FILE:
            return ""
        if sToken in _SET_FLAGS_TAKING_A_VALUE:
            iIndex += 2
            continue
        if sToken.startswith("-"):
            iIndex += 1
            continue
        return sToken
    return ""


def fsExtractScriptPath(sCommand):
    """Extract the Python script path from a command string.

    Returns the script filename, or an empty string when the command is
    not Python or runs no script file at all.
    """
    listTokens = sCommand.strip().split()
    if not listTokens:
        return ""
    if listTokens[0] in ("python", "python3") and len(listTokens) > 1:
        return fsExtractScriptPathFromArguments(listTokens[1:])
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
        # Same flag handling as fsExtractScriptPath, for the same
        # reason: the token after an interpreter is not always a file.
        # The LANGUAGE is still known and still returned -- inline code
        # is Python whether or not it lives in a file.
        return (
            fsExtractScriptPathFromArguments(listTokens[1:]), sLanguage,
        )
    sExtension = os.path.splitext(sFirstToken)[1]
    if sExtension in DICT_EXTENSION_TO_LANGUAGE:
        return (sFirstToken, DICT_EXTENSION_TO_LANGUAGE[sExtension])
    if len(listTokens) > 1:
        sSecondToken = listTokens[1]
        sSecondExt = os.path.splitext(sSecondToken)[1]
        if sSecondExt in DICT_EXTENSION_TO_LANGUAGE:
            return (sSecondToken, DICT_EXTENSION_TO_LANGUAGE[sSecondExt])
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
