"""Lightweight provenance tracking via file hashes and a DAG.

Tracks which scripts produce which outputs, stores SHA-256 hashes of
every artefact, and detects when outputs have changed since the last
recorded state. Can also emit a Graphviz DOT file of the data-flow
graph.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


_HASH_BLOCK_SIZE = 65536


# ------------------------------------------------------------------
# Load / Save
# ------------------------------------------------------------------


def fdictLoadProvenance(sFilePath):
    """Load a provenance JSON file and return its contents.

    Parameters
    ----------
    sFilePath : str
        Path to the provenance JSON file.

    Returns
    -------
    dict
        Provenance dictionary.
    """
    pathFile = Path(sFilePath)
    if not pathFile.is_file():
        raise FileNotFoundError(
            f"Provenance file not found: '{sFilePath}'"
        )
    with open(pathFile, "r") as fileHandle:
        return json.load(fileHandle)


def fnSaveProvenance(dictProvenance, sFilePath):
    """Write provenance data to a JSON file.

    Parameters
    ----------
    dictProvenance : dict
        Provenance dictionary to persist.
    sFilePath : str
        Destination file path.
    """
    pathFile = Path(sFilePath)
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    with open(pathFile, "w") as fileHandle:
        json.dump(dictProvenance, fileHandle, indent=2)


# ------------------------------------------------------------------
# Hashing
# ------------------------------------------------------------------


def fsComputeFileHash(sFilePath):
    """Compute the SHA-256 hex digest of a file.

    Parameters
    ----------
    sFilePath : str
        Path to the file to hash.

    Returns
    -------
    str
        Hex-encoded SHA-256 digest.
    """
    pathFile = Path(sFilePath)
    if not pathFile.is_file():
        raise FileNotFoundError(
            f"Cannot hash missing file: '{sFilePath}'"
        )
    return _fsHashFileContents(pathFile)


def _fsHashFileContents(pathFile):
    """Read a file in chunks and return its SHA-256 hex digest."""
    hasher = hashlib.sha256()
    with open(pathFile, "rb") as fileHandle:
        while True:
            baBlock = fileHandle.read(_HASH_BLOCK_SIZE)
            if not baBlock:
                break
            hasher.update(baBlock)
    return hasher.hexdigest()


# ------------------------------------------------------------------
# DAG construction
# ------------------------------------------------------------------


def fdictBuildDagFromScript(dictScript):
    """Build a dependency graph from a script.json structure.

    Each scene declares inputs and outputs; edges flow from inputs
    through the scene to its outputs.

    Parameters
    ----------
    dictScript : dict
        Parsed script.json with a "listScenes" key.

    Returns
    -------
    dict
        DAG with "listNodes" and "listEdges" keys.
    """
    listNodes = []
    listEdges = []
    for dictScene in dictScript.get("listScenes", []):
        _fnAddSceneToDag(dictScene, listNodes, listEdges)
    return {"listNodes": listNodes, "listEdges": listEdges}


def _fnAddSceneToDag(dictScene, listNodes, listEdges):
    """Append nodes and edges for a single scene."""
    sSceneName = dictScene.get("sName", "unknown")
    listNodes.append(sSceneName)
    for sInput in dictScene.get("saInputFiles", []):
        listEdges.append({"sFrom": sInput, "sTo": sSceneName})
    for sOutput in dictScene.get("saOutputFiles", []):
        listEdges.append({"sFrom": sSceneName, "sTo": sOutput})


# ------------------------------------------------------------------
# Change detection
# ------------------------------------------------------------------


def flistDetectChangedOutputs(dictProvenance, dictScript):
    """Compare current file hashes to stored hashes.

    Parameters
    ----------
    dictProvenance : dict
        Previously stored provenance data.
    dictScript : dict
        Parsed script.json with scene definitions.

    Returns
    -------
    list of str
        Paths whose current hash differs from the stored hash.
    """
    dictStoredHashes = dictProvenance.get("dictFileHashes", {})
    listChanged = []
    for dictScene in dictScript.get("listScenes", []):
        _fnCheckSceneOutputs(dictScene, dictStoredHashes, listChanged)
    return listChanged


def _fnCheckSceneOutputs(dictScene, dictStoredHashes, listChanged):
    """Check each output of a scene for hash changes."""
    for sOutputPath in dictScene.get("saOutputFiles", []):
        if _fbFileHashChanged(sOutputPath, dictStoredHashes):
            listChanged.append(sOutputPath)


def _fbFileHashChanged(sFilePath, dictStoredHashes):
    """Return True if the file's current hash differs from stored."""
    pathFile = Path(sFilePath)
    if not pathFile.is_file():
        return sFilePath in dictStoredHashes
    sCurrentHash = fsComputeFileHash(sFilePath)
    sStoredHash = dictStoredHashes.get(sFilePath, "")
    return sCurrentHash != sStoredHash


# ------------------------------------------------------------------
# Provenance update
# ------------------------------------------------------------------


def fnUpdateProvenance(dictProvenance, dictScript, sWorkdir):
    """Recompute hashes for all outputs and update provenance.

    Parameters
    ----------
    dictProvenance : dict
        Provenance dictionary to update in place.
    dictScript : dict
        Parsed script.json with scene definitions.
    sWorkdir : str
        Working directory (currently unused but reserved).
    """
    dictHashes = {}
    saScenes = []
    for dictScene in dictScript.get("listScenes", []):
        saScenes.append(dictScene.get("sName", "unknown"))
        _fnHashSceneOutputs(dictScene, dictHashes)
    dictProvenance["saScenes"] = saScenes
    dictProvenance["dictFileHashes"] = dictHashes
    dictProvenance["sTimestamp"] = _fsCurrentTimestamp()


def _fnHashSceneOutputs(dictScene, dictHashes):
    """Hash every output file in a scene and store in dictHashes."""
    for sOutputPath in dictScene.get("saOutputFiles", []):
        if Path(sOutputPath).is_file():
            dictHashes[sOutputPath] = fsComputeFileHash(sOutputPath)


def _fsCurrentTimestamp():
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# DOT file generation
# ------------------------------------------------------------------


def fnGenerateDotFile(dictProvenance, sOutputPath):
    """Write a Graphviz DOT file of the provenance DAG.

    Parameters
    ----------
    dictProvenance : dict
        Provenance dictionary (must have been built from a DAG).
    sOutputPath : str
        Path for the output .dot file.
    """
    listLines = _flistBuildDotLines(dictProvenance)
    pathOutput = Path(sOutputPath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w") as fileHandle:
        fileHandle.write("\n".join(listLines) + "\n")


def _flistBuildDotLines(dictProvenance):
    """Assemble the lines of a DOT digraph from provenance data."""
    listLines = ["digraph provenance {", "  rankdir=LR;"]
    for sScene in dictProvenance.get("saScenes", []):
        sLabel = sScene.replace('"', '\\"')
        listLines.append(f'  "{sLabel}";')
    for sFile in dictProvenance.get("dictFileHashes", {}):
        sLabel = sFile.replace('"', '\\"')
        listLines.append(f'  "{sLabel}" [shape=box];')
    listLines.append("}")
    return listLines
