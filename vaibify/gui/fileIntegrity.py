"""File integrity helpers: path normalization and script-path extraction."""

import posixpath

from .commandUtilities import flistExtractScripts as _flistCommandScripts

__all__ = [
    "flistExtractAllScriptPaths",
]


def _fsNormalizePath(sDirectory, sScript):
    """Normalize a script path relative to a directory."""
    if sScript.startswith("/"):
        return sScript
    sJoined = posixpath.join(sDirectory, sScript)
    return posixpath.normpath(sJoined)


def flistExtractAllScriptPaths(dictWorkflow):
    """Extract all unique script paths from all steps."""
    listAllPaths = []
    setAdded = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        sDirectory = dictStep.get("sDirectory", "")
        for sKey in ("saDataCommands", "saPlotCommands",
                     "saSetupCommands", "saCommands"):
            for sScript in _flistCommandScripts(
                dictStep.get(sKey, [])
            ):
                sPath = _fsNormalizePath(sDirectory, sScript)
                if sPath not in setAdded:
                    listAllPaths.append(sPath)
                    setAdded.add(sPath)
    return listAllPaths
