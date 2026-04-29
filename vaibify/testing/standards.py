"""Quantitative-standards write side: bulk generators and CLI entry points.

This module owns the *write side* of the standards contract — the bulk
generators that ``vaibify generate-standards`` calls to seed or refresh
the ``fValue`` column from live data after a deliberate, seeded rerun.

The matching *read side* (``fLoadValue`` + access-path parser) is the
canonical loader in :mod:`vaibify.gui.dataLoaders`; this module
re-exports the two read-side entry points so callers and the CLI can
import everything they need from one place. Sharing one loader keeps
the read schema and the write schema from drifting apart.
"""

import json
import os
import pathlib
import re

import numpy as np

from vaibify.gui.dataLoaders import (
    _fdictParseAccessPath as _fdictParseAccessPathDataLoaders,
    fLoadValue as _fLoadValueDataLoaders,
)


# ===========================================================================
# Access path parser + value loader (delegate to dataLoaders)
# ===========================================================================


def fdictParseAccessPath(sAccessPath):
    """Parse an access path into a dict of components.

    Thin re-export of :func:`vaibify.gui.dataLoaders._fdictParseAccessPath`
    so the parser implementation cannot drift from the loader.
    """
    return _fdictParseAccessPathDataLoaders(sAccessPath)


def fLoadValue(sDataFile, sAccessPath, sStepDirectory):
    """Load a single numeric value from a data file using an access path.

    Thin re-export of :func:`vaibify.gui.dataLoaders.fLoadValue` so the
    write-side bulk generator and the read-side regression tests load
    values through identical code.
    """
    return _fLoadValueDataLoaders(sDataFile, sAccessPath, sStepDirectory)


# ===========================================================================
# Write side — bulk-load files and emit standards lists
# ===========================================================================

_DICT_FORMAT_MAP = {
    ".npy": "npy",
    ".npz": "npz",
    ".json": "json",
    ".csv": "csv",
    ".txt": "whitespace",
    ".dat": "whitespace",
    ".h5": "hdf5",
    ".hdf5": "hdf5",
}


def _fsInferFormat(sFilePath):
    """Infer data format from file extension; default to whitespace."""
    sExt = pathlib.Path(sFilePath).suffix.lower()
    return _DICT_FORMAT_MAP.get(sExt, "whitespace")


def _daLoadNpy(sFilePath):
    """Load .npy as a 2D array (1D shapes are reshaped to (N, 1))."""
    daData = np.load(sFilePath, allow_pickle=False)
    if daData.ndim == 1:
        daData = daData.reshape(-1, 1)
    return daData


def _dictLoadNpz(sFilePath):
    """Load .npz archive into a dict of 2D arrays keyed by archive name."""
    archiveNpz = np.load(sFilePath, allow_pickle=False)
    dictArrays = {}
    for sKey in archiveNpz.files:
        daArray = archiveNpz[sKey]
        if daArray.ndim == 1:
            daArray = daArray.reshape(-1, 1)
        dictArrays[sKey] = daArray
    return dictArrays


def _dictLoadJson(sFilePath):
    """Load a JSON file (handles doubly-serialised payloads)."""
    with open(sFilePath, "r", encoding="utf-8") as fileHandle:
        dictData = json.load(fileHandle)
    if isinstance(dictData, str):
        dictData = json.loads(dictData)
    return dictData


def _daLoadWhitespace(sFilePath):
    """Load whitespace-delimited text into a 2D array."""
    return np.loadtxt(sFilePath, ndmin=2)


def _daLoadCsv(sFilePath):
    """Load a CSV with one header row into a 2D array."""
    return np.loadtxt(sFilePath, delimiter=",", skiprows=1, ndmin=2)


def _dictLoadKeyValueText(sFilePath):
    """Parse a structured ``key = value`` text report into a flat dict."""
    dictData = {}
    with open(sFilePath, "r", encoding="utf-8") as fileHandle:
        for sLine in fileHandle:
            matchKV = re.match(
                r"\s*([A-Za-z_][A-Za-z0-9_.^()*\- ]*?)\s*=\s*"
                r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*",
                sLine,
            )
            if matchKV:
                dictData[matchKV.group(1).strip()] = float(matchKV.group(2))
    return dictData


# ---------------------------------------------------------------------------
# Standard-entry builders
# ---------------------------------------------------------------------------


def _fdictMakeStandard(sName, sDataFile, sAccessPath, fValue):
    """Build a single standards-list entry."""
    return {
        "sName": sName,
        "sDataFile": sDataFile,
        "sAccessPath": sAccessPath,
        "fValue": float(fValue),
        "sUnit": "",
    }


def _listColumnStats(daCol, sBase, sDataFile, sColumnPrefix):
    """Build first/last/mean/min/max standards for one 1D column."""
    listStats = [
        ("First", f"{sColumnPrefix}index:0", float(daCol[0])),
        ("Last", f"{sColumnPrefix}index:-1", float(daCol[-1])),
        ("Mean", f"{sColumnPrefix}index:mean", float(daCol.mean())),
        ("Min", f"{sColumnPrefix}index:min", float(daCol.min())),
        ("Max", f"{sColumnPrefix}index:max", float(daCol.max())),
    ]
    listOut = []
    for sStat, sAccess, fValue in listStats:
        if np.isfinite(fValue):
            listOut.append(_fdictMakeStandard(
                f"f{sBase}{sStat}", sDataFile, sAccess, fValue))
    return listOut


def _listGlobalAggregates(daArray, sDataFile, sBase):
    """Build mean/min/max standards over the whole array (binary formats)."""
    listOut = []
    for sStat, sAccess, fValue in [
        ("Mean", "index:mean", float(daArray.mean())),
        ("Min", "index:min", float(daArray.min())),
        ("Max", "index:max", float(daArray.max())),
    ]:
        if np.isfinite(fValue):
            listOut.append(_fdictMakeStandard(
                f"f{sBase}{sStat}", sDataFile, sAccess, fValue))
    return listOut


def _listPerColumnFirstLast(daArray, sDataFile, sPrefix, iNumCols):
    """Build first/last per-column standards (binary multi-column arrays)."""
    listOut = []
    for iCol in range(iNumCols):
        daCol = daArray[:, iCol]
        sBase = f"{sPrefix}{iCol}" if sPrefix else f"col{iCol}"
        for sStat, sAccess, fValue in [
            ("First", f"index:0,{iCol}", float(daCol[0])),
            ("Last", f"index:-1,{iCol}", float(daCol[-1])),
        ]:
            if np.isfinite(fValue):
                listOut.append(_fdictMakeStandard(
                    f"f{sBase}{sStat}", sDataFile, sAccess, fValue))
    return listOut


def _listStandardsFromArray(daArray, sDataFile, sPrefix="", sFormat="npy"):
    """Top-level array-to-standards extractor (dispatches binary vs text)."""
    iNumCols = daArray.shape[1] if daArray.ndim > 1 else 1
    bTextFormat = sFormat in ("whitespace", "csv")
    sBase = sPrefix if sPrefix else sDataFile.split(".")[0]
    if not bTextFormat and iNumCols > 1:
        return (_listGlobalAggregates(daArray, sDataFile, sBase)
                + _listPerColumnFirstLast(daArray, sDataFile, sPrefix, iNumCols))
    listOut = []
    for iCol in range(iNumCols):
        daCol = daArray[:, iCol] if daArray.ndim > 1 else daArray
        sSuffix = str(iCol) if iNumCols > 1 else ""
        sColBase = f"{sPrefix}{sSuffix}" if sPrefix else f"col{iCol}"
        sColumnPrefix = f"column:col{iCol}," if bTextFormat else ""
        listOut.extend(_listColumnStats(daCol, sColBase, sDataFile, sColumnPrefix))
    return listOut


def _listStandardsFromScalarJson(sName, sDataFile, sKey, value):
    """Emit a single scalar JSON entry if finite."""
    if not (isinstance(value, (int, float)) and np.isfinite(value)):
        return []
    return [_fdictMakeStandard(sName, sDataFile, f"key:{sKey}", value)]


def _listStandardsFromJsonList(sName, sDataFile, sKey, listValues):
    """Emit standards for a JSON list — aggregates if long, per-index if short."""
    listNumeric = [v for v in listValues
                   if isinstance(v, (int, float)) and np.isfinite(v)]
    if len(listNumeric) > 10:
        daArray = np.array(listNumeric)
        listAggSpec = [
            ("First", "0", float(daArray[0])),
            ("Last", "-1", float(daArray[-1])),
            ("Mean", "mean", float(daArray.mean())),
            ("Min", "min", float(daArray.min())),
            ("Max", "max", float(daArray.max())),
        ]
        return [_fdictMakeStandard(
                    f"{sName}_{sStat}", sDataFile,
                    f"key:{sKey},index:{sIdx}", fVal)
                for sStat, sIdx, fVal in listAggSpec if np.isfinite(fVal)]
    listOut = []
    for i, v in enumerate(listValues):
        if isinstance(v, (int, float)) and np.isfinite(v):
            listOut.append(_fdictMakeStandard(
                f"{sName}_{i}", sDataFile, f"key:{sKey},index:{i}", v))
    return listOut


def _listStandardsFromJson(dictData, sDataFile, sPrefix=""):
    """Generate standards from JSON scalar and array values."""
    listOut = []
    for sKey, value in dictData.items():
        sName = f"f{sPrefix}{sKey}"
        if isinstance(value, (int, float)):
            listOut.extend(_listStandardsFromScalarJson(
                sName, sDataFile, sKey, value))
        elif isinstance(value, list):
            listOut.extend(_listStandardsFromJsonList(
                sName, sDataFile, sKey, value))
    return listOut


def _listStandardsFromNpzScalar(daArray, sDataFile, sKey):
    """Emit standard for a 0-d (scalar) array stored in an npz."""
    fValue = float(daArray)
    if not np.isfinite(fValue):
        return []
    return [_fdictMakeStandard(
        f"f{sKey}", sDataFile, f"key:{sKey}", fValue)]


def _listStandardsFromNpz1d(daArray, sDataFile, sKey):
    """Emit standards for a 1-D array stored in an npz."""
    daFlat = daArray.ravel()
    listSpec = [
        ("First", f"key:{sKey},index:0", float(daFlat[0])),
        ("Last", f"key:{sKey},index:-1", float(daFlat[-1])),
        ("Mean", f"key:{sKey},index:mean", float(daFlat.mean())),
        ("Min", f"key:{sKey},index:min", float(daFlat.min())),
        ("Max", f"key:{sKey},index:max", float(daFlat.max())),
    ]
    return [_fdictMakeStandard(f"f{sKey}{sStat}", sDataFile, sAcc, fVal)
            for sStat, sAcc, fVal in listSpec if np.isfinite(fVal)]


def _listStandardsFromNpz2d(daArray, sDataFile, sKey, iNumCols):
    """Emit standards for a 2-D array stored in an npz."""
    listOut = []
    for sStat, sAgg, fValue in [
        ("Mean", "mean", float(daArray.mean())),
        ("Min", "min", float(daArray.min())),
        ("Max", "max", float(daArray.max())),
    ]:
        if np.isfinite(fValue):
            listOut.append(_fdictMakeStandard(
                f"f{sKey}{sStat}", sDataFile,
                f"key:{sKey},index:{sAgg}", fValue))
    for iCol in range(iNumCols):
        daCol = daArray[:, iCol]
        for sStat, sIdx, fValue in [
            ("First", f"0,{iCol}", float(daCol[0])),
            ("Last", f"-1,{iCol}", float(daCol[-1])),
        ]:
            if np.isfinite(fValue):
                listOut.append(_fdictMakeStandard(
                    f"f{sKey}{iCol}{sStat}", sDataFile,
                    f"key:{sKey},index:{sIdx}", fValue))
    return listOut


def _listStandardsFromNpz(dictArrays, sDataFile):
    """Generate standards from each array in an npz archive."""
    listOut = []
    for sKey, daArray in dictArrays.items():
        if daArray.ndim == 0:
            listOut.extend(_listStandardsFromNpzScalar(daArray, sDataFile, sKey))
        elif daArray.ndim == 1 or daArray.shape[1] <= 1:
            listOut.extend(_listStandardsFromNpz1d(daArray, sDataFile, sKey))
        else:
            listOut.extend(_listStandardsFromNpz2d(
                daArray, sDataFile, sKey, daArray.shape[1]))
    return listOut


# ---------------------------------------------------------------------------
# File-level dispatcher and full-step generator
# ---------------------------------------------------------------------------


def _listStandardsFromFile(sStepDirectory, sDataFile):
    """Build standards for one data file by sniffing format and dispatching."""
    sFullPath = os.path.join(sStepDirectory, sDataFile)
    if not os.path.isfile(sFullPath):
        print(f"  WARNING: {sFullPath} does not exist, skipping")
        return []
    sFormat = _fsInferFormat(sFullPath)
    print(f"  Processing {sDataFile} (format: {sFormat})")
    if sFormat == "npy":
        return _listStandardsFromArray(_daLoadNpy(sFullPath), sDataFile,
                                       sFormat="npy")
    if sFormat == "npz":
        return _listStandardsFromNpz(_dictLoadNpz(sFullPath), sDataFile)
    if sFormat == "json":
        return _listStandardsFromJson(_dictLoadJson(sFullPath), sDataFile)
    if sFormat in ("whitespace", "csv"):
        return _listStandardsFromTextOrKv(sFullPath, sDataFile, sFormat)
    print(f"  WARNING: unsupported format {sFormat} for {sDataFile}")
    return []


def _listStandardsFromTextOrKv(sFullPath, sDataFile, sFormat):
    """Try numeric text load first; fall back to key=value parsing."""
    fnLoader = _daLoadCsv if sFormat == "csv" else _daLoadWhitespace
    try:
        return _listStandardsFromArray(fnLoader(sFullPath), sDataFile,
                                       sFormat=sFormat)
    except ValueError:
        dictData = _dictLoadKeyValueText(sFullPath)
        if dictData:
            print(f"    Parsed as key-value text ({len(dictData)} entries)")
            return _listStandardsFromJson(dictData, sDataFile)
        print(f"  WARNING: could not parse {sDataFile}")
        return []


def fdictGenerateQuantitativeStandards(sStepDirectory, listDataFiles,
                                       fDefaultRtol=1e-6):
    """Generate the full standards dict for a step from live data files."""
    listAllStandards = []
    for sDataFile in listDataFiles:
        listAllStandards.extend(
            _listStandardsFromFile(sStepDirectory, sDataFile))
    return {
        "fDefaultRtol": fDefaultRtol,
        "listStandards": listAllStandards,
    }


def fnWriteStandards(dictStandards, sOutputPath):
    """Write a standards dict to a JSON file (creates parent dirs)."""
    os.makedirs(os.path.dirname(sOutputPath), exist_ok=True)
    with open(sOutputPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictStandards, fileHandle, indent=4)
    iCount = len(dictStandards.get("listStandards", []))
    print(f"  Wrote {iCount} standards to {sOutputPath}")


def fnUpdateWorkflowStandards(sWorkflowPath, iStepIndex, sStandardsContent):
    """Inline a standards JSON blob into a workflow's step entry."""
    with open(sWorkflowPath, "r", encoding="utf-8") as fileHandle:
        dictWorkflow = json.load(fileHandle)
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    dictTests = dictStep.get("dictTests", {})
    dictQuant = dictTests.get("dictQuantitative", {})
    dictQuant["sStandardsContent"] = sStandardsContent
    dictTests["dictQuantitative"] = dictQuant
    dictStep["dictTests"] = dictTests
    with open(sWorkflowPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictWorkflow, fileHandle, indent=2)
    print(f"  Updated workflow step {iStepIndex} standards")


def _fsResolveStepDir(sWorkflowPath, sDirectory):
    """Resolve a workflow step directory relative to the workflow file."""
    sWorkflowRoot = os.path.dirname(sWorkflowPath)
    sStepDir = os.path.join(sWorkflowRoot, sDirectory)
    if os.path.isdir(sStepDir):
        return sStepDir
    sRepoRoot = sWorkflowRoot
    for sSuffix in ("/.vaibify/workflows", "/.vaibify"):
        if sRepoRoot.endswith(sSuffix):
            sRepoRoot = sRepoRoot[:-len(sSuffix)]
    return os.path.join(sRepoRoot, sDirectory)


def fnGenerateFromWorkflow(sWorkflowPath, iStepIndex, fDefaultRtol=1e-6,
                           bCheckSeeds=False):
    """Generate standards for a workflow step by index, writing both step and JSON."""
    with open(sWorkflowPath, "r", encoding="utf-8") as fileHandle:
        dictWorkflow = json.load(fileHandle)
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    sStepDir = _fsResolveStepDir(sWorkflowPath, dictStep["sDirectory"])
    listDataFiles = dictStep.get("saDataFiles", [])
    sStepName = dictStep.get("sName", f"Step {iStepIndex + 1}")
    print(f"\n{'=' * 60}\nGenerating standards for: {sStepName}")
    print(f"  Directory: {sStepDir}\n  Data files: {listDataFiles}")
    if bCheckSeeds:
        _fnCheckSeedsForStep(sStepDir, dictStep)
    if not listDataFiles:
        print("  No data files defined, skipping")
        return
    dictStandards = fdictGenerateQuantitativeStandards(
        sStepDir, listDataFiles, fDefaultRtol)
    sOutputPath = os.path.join(sStepDir, "tests", "quantitative_standards.json")
    fnWriteStandards(dictStandards, sOutputPath)
    fnUpdateWorkflowStandards(
        sWorkflowPath, iStepIndex, json.dumps(dictStandards))


def _fnCheckSeedsForStep(sStepDir, dictStep):
    """Run the stochastic detector on each data*.py script of a step."""
    from vaibify.testing.stochasticDetector import (
        ftDetectStochastic, fnPrintReport)
    for sCommand in dictStep.get("saDataCommands", []):
        for sToken in sCommand.split():
            if sToken.endswith(".py"):
                sScriptPath = os.path.join(sStepDir, sToken)
                if os.path.isfile(sScriptPath):
                    bStoch, listSrc, listSeeds = ftDetectStochastic(sScriptPath)
                    fnPrintReport(sScriptPath, bStoch, listSrc, listSeeds)


# ---------------------------------------------------------------------------
# Symmetric regenerator: reuse existing schema, refresh fValue from live data
# ---------------------------------------------------------------------------


def fnRegenerateStandardsFile(sStandardsPath, sStepDirectory):
    """Refresh ``fValue`` of every entry in an existing standards JSON.

    The schema (sName, sDataFile, sAccessPath, fRtol, …) is preserved in
    place; only fValue is recomputed from live data using ``fLoadValue``.
    Use this after a deliberate, seeded rerun produces new bit-exact
    baselines for a step that already has a curated standards list.
    """
    with open(sStandardsPath, "r", encoding="utf-8") as fileHandle:
        dictStandards = json.load(fileHandle)
    listStandards = dictStandards.get("listStandards", [])
    for dictStandard in listStandards:
        dictStandard["fValue"] = fLoadValue(
            dictStandard["sDataFile"],
            dictStandard["sAccessPath"],
            sStepDirectory,
        )
    with open(sStandardsPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictStandards, fileHandle, indent=4)
    print(f"  Refreshed {len(listStandards)} fValue entries in {sStandardsPath}")
