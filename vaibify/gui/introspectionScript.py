"""Introspection script builder for container-based data analysis.

This module contains the functions that build, execute, and parse the
output of a self-contained Python script that runs inside Docker
containers to introspect data files and generate benchmark values.

The introspection script is a large f-string that duplicates format
handling logic from dataLoaders.py.  This duplication is inherent:
the script runs inside containers that cannot import from the host
Python environment.
"""

__all__ = []

import json
import posixpath
import re


def _fsFormatSafeName(sFileName):
    """Convert a filename to a valid Python identifier."""
    sBase = posixpath.splitext(sFileName)[0]
    sSafe = re.sub(r"[^a-zA-Z0-9]", "_", sBase)
    if sSafe and sSafe[0].isdigit():
        sSafe = "f" + sSafe
    return sSafe


def _fsBuildIntrospectionScript(
    listDataFiles, sDirectory, bScriptStochastic=False,
    iStochasticMinSamples=64,
):
    """Return a self-contained Python script that introspects data files.

    ``bScriptStochastic`` and ``iStochasticMinSamples`` configure
    whether arrays large enough to support distributional metrics
    receive percentile/std benchmarks alongside the single-sample
    ones. Both signals must agree before distributional metrics are
    emitted; the host-side filter chooses which ones survive.
    """
    sFileListRepr = repr(listDataFiles)
    sDirectoryRepr = repr(sDirectory)
    sScriptStochasticRepr = repr(bool(bScriptStochastic))
    sStochasticMinSamplesRepr = repr(int(iStochasticMinSamples))
    return f'''import json
import os
import sys
import traceback

import numpy as np

_I_MAX_FILE_BYTES = 500_000_000
_I_MAX_BENCHMARKS_PER_FILE = 250
_B_SCRIPT_STOCHASTIC = {sScriptStochasticRepr}
_I_STOCHASTIC_MIN_SAMPLES = {sStochasticMinSamplesRepr}

_DICT_FORMAT_MAP = {{
    ".npy": "npy", ".npz": "npz", ".json": "json", ".csv": "csv",
    ".h5": "hdf5", ".hdf5": "hdf5", ".dat": "whitespace",
    ".txt": "whitespace", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".xlsx": "excel", ".xls": "excel", ".fits": "fits", ".fit": "fits",
    ".mat": "matlab", ".parquet": "parquet",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".tiff": "image", ".tif": "image",
    ".fasta": "fasta", ".fa": "fasta",
    ".fastq": "fastq", ".fq": "fastq",
    ".vcf": "vcf", ".bed": "bed",
    ".gff": "gff", ".gtf": "gff", ".gff3": "gff",
    ".sam": "sam", ".log": "syslog", ".cef": "cef",
    ".bam": "bam", ".unf": "fortran",
    ".sav": "spss", ".dta": "stata", ".sas7bdat": "sas",
    ".rds": "rdata", ".RData": "rdata", ".rda": "rdata",
    ".vot": "votable", ".ipac": "ipac",
    ".pcap": "pcap", ".pcapng": "pcap",
    ".vtk": "vtk", ".vtu": "vtk",
    ".cgns": "cgns", ".safetensors": "safetensors",
    ".tfrecord": "tfrecord",
}}

def _fbIsDividerLine(sLine):
    sStripped = sLine.strip()
    if len(sStripped) < 3:
        return False
    return all(c == sStripped[0] for c in sStripped)

def _fbLooksLikeKeyvalue(sFullPath):
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fh:
            listLines = [
                s.strip() for s in fh.readlines()
                if s.strip() and not s.strip().startswith("#")
                and not _fbIsDividerLine(s)
            ]
        if not listLines:
            return False
        iWithEquals = sum(1 for s in listLines if "=" in s)
        return iWithEquals > len(listLines) / 3
    except Exception:
        return False

def _fsDetectFormat(sFullPath):
    sExt = os.path.splitext(sFullPath)[1].lower()
    sFormat = _DICT_FORMAT_MAP.get(sExt, None)
    if sFormat == "whitespace" and _fbLooksLikeKeyvalue(sFullPath):
        return "keyvalue"
    if sFormat is None:
        try:
            with open(sFullPath, "rb") as fh:
                baHead = fh.read(4)
            if baHead and any(b > 127 for b in baHead):
                return None
        except Exception:
            return None
        return "whitespace"
    return sFormat

def _fdictIntrospectFile(sFileName, sDirectory):
    sFullPath = os.path.join(sDirectory, sFileName)
    dictReport = {{
        "sFileName": sFileName, "sFormat": "", "bExists": False,
        "iByteSize": 0, "bLoadable": False, "sError": "",
        "tShape": None, "sDtype": "", "iNanCount": 0, "iInfCount": 0,
        "listColumnNames": [], "bHasHeader": False,
        "listJsonTopKeys": [], "dictJsonScalars": {{}},
        "listBenchmarks": [],
    }}
    sRealFull = os.path.realpath(sFullPath)
    sRealDir = os.path.realpath(sDirectory)
    if not sRealFull.startswith(sRealDir + os.sep):
        dictReport["sError"] = "path traversal blocked"
        return dictReport
    if not os.path.isfile(sFullPath):
        dictReport["sError"] = "file not found"
        return dictReport
    dictReport["bExists"] = True
    dictReport["iByteSize"] = os.path.getsize(sFullPath)
    if dictReport["iByteSize"] > _I_MAX_FILE_BYTES:
        dictReport["sError"] = "file exceeds size limit"
        return dictReport
    sFormat = _fsDetectFormat(sFullPath)
    if sFormat is None:
        dictReport["sError"] = "unsupported binary format"
        return dictReport
    dictReport["sFormat"] = sFormat
    try:
        _fnLoadAndBenchmark(sFullPath, sFileName, sFormat, dictReport)
        dictReport["bLoadable"] = True
    except Exception as e:
        dictReport["sError"] = str(e)
    return dictReport

def _fnLoadAndBenchmark(sFullPath, sFileName, sFormat, dictReport):
    if sFormat == "npy":
        _fnBenchmarkNpy(sFullPath, sFileName, dictReport)
    elif sFormat == "npz":
        _fnBenchmarkNpz(sFullPath, sFileName, dictReport)
    elif sFormat == "json":
        _fnBenchmarkJson(sFullPath, sFileName, dictReport)
    elif sFormat == "csv":
        _fnBenchmarkCsv(sFullPath, sFileName, dictReport)
    elif sFormat == "hdf5":
        _fnBenchmarkHdf5(sFullPath, sFileName, dictReport)
    elif sFormat == "keyvalue":
        _fnBenchmarkKeyvalue(sFullPath, sFileName, dictReport)
    elif sFormat == "whitespace":
        _fnBenchmarkWhitespace(sFullPath, sFileName, dictReport)
    elif sFormat == "jsonl":
        _fnBenchmarkJsonl(sFullPath, sFileName, dictReport)
    elif sFormat == "excel":
        _fnBenchmarkExcel(sFullPath, sFileName, dictReport)
    elif sFormat == "fits":
        _fnBenchmarkFits(sFullPath, sFileName, dictReport)
    elif sFormat == "matlab":
        _fnBenchmarkMatlab(sFullPath, sFileName, dictReport)
    elif sFormat == "parquet":
        _fnBenchmarkParquet(sFullPath, sFileName, dictReport)
    elif sFormat == "image":
        _fnBenchmarkImage(sFullPath, sFileName, dictReport)
    elif sFormat == "fasta":
        _fnBenchmarkFasta(sFullPath, sFileName, dictReport)
    elif sFormat == "fastq":
        _fnBenchmarkFastq(sFullPath, sFileName, dictReport)
    elif sFormat == "vcf":
        _fnBenchmarkVcf(sFullPath, sFileName, dictReport)
    elif sFormat == "bed":
        _fnBenchmarkBed(sFullPath, sFileName, dictReport)
    elif sFormat == "gff":
        _fnBenchmarkGff(sFullPath, sFileName, dictReport)
    elif sFormat == "sam":
        _fnBenchmarkSam(sFullPath, sFileName, dictReport)
    elif sFormat == "syslog":
        _fnBenchmarkSyslog(sFullPath, sFileName, dictReport)
    elif sFormat == "cef":
        _fnBenchmarkCef(sFullPath, sFileName, dictReport)
    elif sFormat == "fixedwidth":
        _fnBenchmarkFixedwidth(sFullPath, sFileName, dictReport)
    elif sFormat == "multitable":
        _fnBenchmarkMultitable(sFullPath, sFileName, dictReport)
    elif sFormat == "bam":
        _fnBenchmarkBam(sFullPath, sFileName, dictReport)
    elif sFormat == "fortran":
        _fnBenchmarkFortran(sFullPath, sFileName, dictReport)
    elif sFormat == "spss":
        _fnBenchmarkSpss(sFullPath, sFileName, dictReport)
    elif sFormat == "stata":
        _fnBenchmarkStata(sFullPath, sFileName, dictReport)
    elif sFormat == "sas":
        _fnBenchmarkSas(sFullPath, sFileName, dictReport)
    elif sFormat == "rdata":
        _fnBenchmarkRdata(sFullPath, sFileName, dictReport)
    elif sFormat == "votable":
        _fnBenchmarkVotable(sFullPath, sFileName, dictReport)
    elif sFormat == "ipac":
        _fnBenchmarkIpac(sFullPath, sFileName, dictReport)
    elif sFormat == "pcap":
        _fnBenchmarkPcap(sFullPath, sFileName, dictReport)
    elif sFormat == "vtk":
        _fnBenchmarkVtk(sFullPath, sFileName, dictReport)
    elif sFormat == "cgns":
        _fnBenchmarkCgns(sFullPath, sFileName, dictReport)
    elif sFormat == "safetensors":
        _fnBenchmarkSafetensors(sFullPath, sFileName, dictReport)
    elif sFormat == "tfrecord":
        _fnBenchmarkTfrecord(sFullPath, sFileName, dictReport)

def _fnBenchmarkNpy(sFullPath, sFileName, dictReport):
    daData = np.load(sFullPath, allow_pickle=False)
    dictReport["tShape"] = list(daData.shape)
    dictReport["sDtype"] = str(daData.dtype)
    if np.issubdtype(daData.dtype, np.number):
        dictReport["iNanCount"] = int(np.isnan(daData).sum())
        dictReport["iInfCount"] = int(np.isinf(daData).sum())
    daFlat = daData.flatten()
    _fnAddArrayBenchmarks(daFlat, sFileName, "", dictReport)

def _fnBenchmarkNpz(sFullPath, sFileName, dictReport):
    archiveNpz = np.load(sFullPath, allow_pickle=False)
    listKeys = list(archiveNpz.files)
    dictReport["listColumnNames"] = listKeys
    for sKey in listKeys:
        daData = archiveNpz[sKey]
        dictReport["tShape"] = list(daData.shape)
        dictReport["sDtype"] = str(daData.dtype)
        if np.issubdtype(daData.dtype, np.number):
            _fnAddArrayBenchmarks(
                daData.flatten(), sFileName, sKey, dictReport,
                sKeyPrefix=f"key:{{sKey}},",
            )

def _fdictBenchMeta(iSampleSize, daValues):
    if iSampleSize <= 0:
        return {{"iSampleSize": 0, "fObservedCv": None}}
    fMean = float(daValues.mean())
    fStd = float(daValues.std(ddof=1)) if iSampleSize > 1 else 0.0
    if fMean == 0.0:
        fObservedCv = None
    else:
        fObservedCv = fStd / abs(fMean)
    return {{"iSampleSize": int(iSampleSize), "fObservedCv": fObservedCv}}

def _fnAttachMeta(dictBench, sMetricKind, dictMeta):
    dictBench["sMetricKind"] = sMetricKind
    dictBench["iSampleSize"] = dictMeta["iSampleSize"]
    dictBench["fObservedCv"] = dictMeta["fObservedCv"]

def _fnAppendSingleBench(
    listBench, sName, sFileName, sAccessPath, fValue, sMetricKind, dictMeta,
):
    dictBench = {{
        "sName": sName, "sDataFile": sFileName,
        "sAccessPath": sAccessPath, "fValue": float(fValue),
    }}
    _fnAttachMeta(dictBench, sMetricKind, dictMeta)
    listBench.append(dictBench)

def _fnAddDistributionalBenchmarks(
    daValues, sLabel, sFileName, sAccessPrefix, dictReport, dictMeta,
):
    listBench = dictReport["listBenchmarks"]
    fStd = float(daValues.std(ddof=1)) if len(daValues) > 1 else 0.0
    _fnAppendSingleBench(
        listBench, f"f{{sLabel}}Std", sFileName,
        f"{{sAccessPrefix}}index:std", fStd, "std", dictMeta,
    )
    listPercentiles = (
        (5, "percentile_5", "p5"), (25, "percentile_25", "p25"),
        (50, "percentile_50", "p50"), (75, "percentile_75", "p75"),
        (95, "percentile_95", "p95"),
    )
    for iPct, sKind, sToken in listPercentiles:
        fValue = float(np.percentile(daValues, iPct))
        _fnAppendSingleBench(
            listBench, f"f{{sLabel}}P{{iPct}}", sFileName,
            f"{{sAccessPrefix}}index:{{sToken}}", fValue, sKind, dictMeta,
        )

def _fbShouldEmitDistributional(daValues):
    return (
        _B_SCRIPT_STOCHASTIC
        and len(daValues) >= _I_STOCHASTIC_MIN_SAMPLES
        and np.issubdtype(daValues.dtype, np.number)
    )

def _fdictMetaForArray(daValues):
    if np.issubdtype(daValues.dtype, np.number):
        return _fdictBenchMeta(len(daValues), daValues)
    return {{"iSampleSize": int(len(daValues)), "fObservedCv": None}}

def _fnAppendNumericAggregates(
    listBench, daValues, sLabel, sFileName, sAccessPrefix, dictMeta,
):
    _fnAppendSingleBench(
        listBench, f"f{{sLabel}}Mean", sFileName,
        f"{{sAccessPrefix}}index:mean", float(daValues.mean()),
        "mean", dictMeta,
    )
    _fnAppendSingleBench(
        listBench, f"f{{sLabel}}Min", sFileName,
        f"{{sAccessPrefix}}index:min", float(daValues.min()),
        "min", dictMeta,
    )
    _fnAppendSingleBench(
        listBench, f"f{{sLabel}}Max", sFileName,
        f"{{sAccessPrefix}}index:max", float(daValues.max()),
        "max", dictMeta,
    )

def _fnAppendFirstLast(
    listBench, daValues, sLabel, sFileName, sAccessPrefix, dictMeta,
):
    _fnAppendSingleBench(
        listBench, f"f{{sLabel}}First", sFileName,
        f"{{sAccessPrefix}}index:0", float(daValues[0]), "first", dictMeta,
    )
    _fnAppendSingleBench(
        listBench, f"f{{sLabel}}Last", sFileName,
        f"{{sAccessPrefix}}index:-1", float(daValues[-1]), "last", dictMeta,
    )

def _fnAddArrayBenchmarks(
    daFlat, sFileName, sLabel, dictReport, sKeyPrefix="",
):
    sPrefix = sLabel or os.path.splitext(sFileName)[0]
    if len(daFlat) == 0:
        return
    dictMeta = _fdictMetaForArray(daFlat)
    listBench = dictReport["listBenchmarks"]
    _fnAppendFirstLast(
        listBench, daFlat, sPrefix, sFileName, sKeyPrefix, dictMeta,
    )
    if np.issubdtype(daFlat.dtype, np.number):
        _fnAppendNumericAggregates(
            listBench, daFlat, sPrefix, sFileName,
            sKeyPrefix, dictMeta,
        )
        if _fbShouldEmitDistributional(daFlat):
            _fnAddDistributionalBenchmarks(
                daFlat, sPrefix, sFileName, sKeyPrefix,
                dictReport, dictMeta,
            )

def _fnAddStatsBenchmarks(
    daValues, sLabel, sFileName, sAccessPrefix, dictReport,
):
    dictMeta = _fdictMetaForArray(daValues)
    listBench = dictReport["listBenchmarks"]
    _fnAppendFirstLast(
        listBench, daValues, sLabel, sFileName,
        sAccessPrefix, dictMeta,
    )
    _fnAppendNumericAggregates(
        listBench, daValues, sLabel, sFileName,
        sAccessPrefix, dictMeta,
    )
    if _fbShouldEmitDistributional(daValues):
        _fnAddDistributionalBenchmarks(
            daValues, sLabel, sFileName, sAccessPrefix,
            dictReport, dictMeta,
        )

def _fnBenchmarkJson(sFullPath, sFileName, dictReport):
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        dictData = json.load(fh)
    if isinstance(dictData, dict):
        dictReport["listJsonTopKeys"] = list(dictData.keys())
        _fnWalkJsonValues(
            dictData, sFileName, "", dictReport,
        )

def _fnWalkJsonValues(value, sFileName, sKeyPath, dictReport, iDepth=0):
    if iDepth > 10:
        return
    if isinstance(value, (int, float)):
        sName = sKeyPath.replace(".", "_") if sKeyPath else "root"
        dictReport["dictJsonScalars"][sKeyPath] = value
        dictReport["listBenchmarks"].append({{
            "sName": f"f{{sName}}",
            "sDataFile": sFileName,
            "sAccessPath": f"key:{{sKeyPath}}",
            "fValue": float(value),
        }})
    elif isinstance(value, dict):
        for sKey, subValue in value.items():
            sSubPath = f"{{sKeyPath}}.{{sKey}}" if sKeyPath else sKey
            _fnWalkJsonValues(
                subValue, sFileName, sSubPath, dictReport, iDepth + 1,
            )
    elif isinstance(value, list):
        _fnBenchmarkJsonArray(value, sFileName, sKeyPath, dictReport, iDepth)

def _fnBenchmarkJsonArray(listValues, sFileName, sKeyPath, dictReport, iDepth=0):
    listNumeric = [v for v in listValues if isinstance(v, (int, float))]
    if listNumeric:
        _fnAddJsonArrayBenchmarks(
            listNumeric, sFileName, sKeyPath, dictReport,
        )
    for iIdx, item in enumerate(listValues):
        if isinstance(item, dict):
            sSubPath = f"{{sKeyPath}}.{{iIdx}}"
            _fnWalkJsonValues(
                item, sFileName, sSubPath, dictReport, iDepth + 1,
            )

def _fnAddJsonArrayBenchmarks(
    listNumeric, sFileName, sKeyPath, dictReport,
):
    sName = sKeyPath.replace(".", "_") if sKeyPath else "root"
    daValues = np.array(listNumeric, dtype=float)
    _fnAddStatsBenchmarks(
        daValues, sName, sFileName, f"key:{{sKeyPath}},", dictReport,
    )

def _fnBenchmarkCsv(sFullPath, sFileName, dictReport):
    import csv
    with open(sFullPath, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        listColumns = reader.fieldnames or []
        listRows = list(reader)
    dictReport["listColumnNames"] = list(listColumns)
    dictReport["bHasHeader"] = True
    dictReport["tShape"] = [len(listRows), len(listColumns)]
    for sCol in listColumns:
        _fnAddColumnBenchmarks(
            listRows, sCol, sFileName, dictReport,
        )

def _fnAddColumnBenchmarks(listRows, sCol, sFileName, dictReport):
    if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
        return
    try:
        daValues = np.array(
            [float(row[sCol]) for row in listRows]
        )
    except (ValueError, KeyError):
        return
    _fnAddStatsBenchmarks(
        daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
    )

def _fnBenchmarkHdf5(sFullPath, sFileName, dictReport):
    import h5py
    with h5py.File(sFullPath, "r") as fh:
        listDatasets = []
        fh.visititems(
            lambda n, o: listDatasets.append(n)
            if isinstance(o, h5py.Dataset) else None
        )
        for sDataset in listDatasets[:50]:
            daData = np.array(fh[sDataset])
            dictReport["tShape"] = list(daData.shape)
            dictReport["sDtype"] = str(daData.dtype)
            if np.issubdtype(daData.dtype, np.number):
                _fnAddArrayBenchmarks(
                    daData.flatten(), sFileName, sDataset,
                    dictReport,
                    sKeyPrefix=f"dataset:{{sDataset}},",
                )

def _fnBenchmarkKeyvalue(sFullPath, sFileName, dictReport):
    dictReport["sFormat"] = "keyvalue"
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            sStripped = sLine.strip()
            if not sStripped or sStripped.startswith("#"):
                continue
            if "=" not in sStripped:
                continue
            sKey, sVal = sStripped.split("=", 1)
            sKey = sKey.strip()
            try:
                fVal = float(sVal.strip())
                dictReport["listBenchmarks"].append({{
                    "sName": f"f{{sKey}}",
                    "sDataFile": sFileName,
                    "sAccessPath": f"key:{{sKey}}",
                    "sFormat": "keyvalue",
                    "fValue": fVal,
                }})
            except ValueError:
                dictReport["listColumnNames"].append(sKey)

def _fbIsNumericToken(sToken):
    try:
        float(sToken)
        return True
    except ValueError:
        return False

def _fnBenchmarkWhitespace(sFullPath, sFileName, dictReport):
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listRawLines = fh.readlines()
    listFiltered = [
        s.strip() for s in listRawLines
        if s.strip() and not s.strip().startswith("#")
    ]
    if not listFiltered:
        return
    listTokens = listFiltered[0].split()
    bAllNumeric = all(_fbIsNumericToken(s) for s in listTokens)
    if bAllNumeric:
        dictReport["bHasHeader"] = False
        listDataRows = listFiltered
        listColumns = []
    else:
        dictReport["bHasHeader"] = True
        listColumns = listTokens
        listDataRows = listFiltered[1:]
        dictReport["listColumnNames"] = listColumns
    if not listDataRows:
        return
    iNumCols = len(listDataRows[0].split())
    dictReport["tShape"] = [len(listDataRows), iNumCols]
    if listColumns:
        for iCol, sCol in enumerate(listColumns):
            _fnAddWhitespaceColBenchmarks(
                listDataRows, iCol, sCol, sFileName, dictReport,
            )
    else:
        for iCol in range(iNumCols):
            _fnAddWhitespaceColBenchmarks(
                listDataRows, iCol, f"col{{iCol}}",
                sFileName, dictReport,
            )

def _fnAddWhitespaceColBenchmarks(
    listDataRows, iCol, sLabel, sFileName, dictReport,
):
    if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
        return
    try:
        daValues = np.array(
            [float(row.split()[iCol]) for row in listDataRows]
        )
    except (ValueError, IndexError):
        return
    sAccessPrefix = f"column:{{sLabel}}," if sLabel else ""
    _fnAddStatsBenchmarks(
        daValues, sLabel, sFileName, sAccessPrefix, dictReport,
    )

def _fnBenchmarkJsonl(sFullPath, sFileName, dictReport):
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listRecords = [json.loads(sLine) for sLine in fh if sLine.strip()]
    if not listRecords:
        return
    if isinstance(listRecords[0], dict):
        dictReport["listColumnNames"] = list(listRecords[0].keys())
    dictReport["tShape"] = [len(listRecords)]
    for sKey in dictReport["listColumnNames"]:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array(
                [float(r[sKey]) for r in listRecords], dtype=float,
            )
        except (ValueError, KeyError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sKey, sFileName, f"key:{{sKey}},", dictReport,
        )

def _fnBenchmarkExcel(sFullPath, sFileName, dictReport):
    try:
        import openpyxl
    except ImportError:
        dictReport["sError"] = "openpyxl not installed"
        return
    wb = openpyxl.load_workbook(sFullPath, read_only=True)
    ws = wb.active
    listRows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not listRows:
        return
    listHeaders = [str(c) if c else f"col{{i}}" for i, c in enumerate(listRows[0])]
    dictReport["listColumnNames"] = listHeaders
    dictReport["bHasHeader"] = True
    dictReport["tShape"] = [len(listRows) - 1, len(listHeaders)]
    for iCol, sCol in enumerate(listHeaders):
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array([float(r[iCol]) for r in listRows[1:]])
        except (ValueError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkFits(sFullPath, sFileName, dictReport):
    try:
        from astropy.io import fits as fitsLib
    except ImportError:
        dictReport["sError"] = "astropy not installed"
        return
    with fitsLib.open(sFullPath) as hduList:
        for iHdu, hdu in enumerate(hduList):
            if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
                break
            if hdu.data is None:
                continue
            if hasattr(hdu, "columns") and hdu.columns is not None:
                dictReport["listColumnNames"] = list(hdu.columns.names)
                for sCol in hdu.columns.names:
                    try:
                        daCol = np.array(hdu.data[sCol], dtype=float).flatten()
                        _fnAddArrayBenchmarks(daCol, sFileName, sCol, dictReport, sKeyPrefix=f"hdu:{{iHdu}},column:{{sCol}},")
                    except (ValueError, TypeError):
                        continue
            else:
                daFlat = np.array(hdu.data, dtype=float).flatten()
                dictReport["tShape"] = list(hdu.data.shape)
                dictReport["sDtype"] = str(hdu.data.dtype)
                if np.issubdtype(daFlat.dtype, np.number):
                    dictReport["iNanCount"] = int(np.isnan(daFlat).sum())
                    dictReport["iInfCount"] = int(np.isinf(daFlat).sum())
                _fnAddArrayBenchmarks(daFlat, sFileName, f"hdu{{iHdu}}", dictReport, sKeyPrefix=f"hdu:{{iHdu}},")

def _fnBenchmarkMatlab(sFullPath, sFileName, dictReport):
    try:
        from scipy.io import loadmat
    except ImportError:
        dictReport["sError"] = "scipy not installed"
        return
    dictMat = loadmat(sFullPath)
    listKeys = [k for k in dictMat if not k.startswith("__")]
    dictReport["listColumnNames"] = listKeys
    for sKey in listKeys:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daData = np.array(dictMat[sKey], dtype=float).flatten()
        except (ValueError, TypeError):
            continue
        dictReport["tShape"] = list(dictMat[sKey].shape)
        _fnAddArrayBenchmarks(daData, sFileName, sKey, dictReport, sKeyPrefix=f"key:{{sKey}},")

def _fnBenchmarkParquet(sFullPath, sFileName, dictReport):
    try:
        import pyarrow.parquet as pq
    except ImportError:
        dictReport["sError"] = "pyarrow not installed"
        return
    table = pq.read_table(sFullPath)
    listColumns = table.column_names
    dictReport["listColumnNames"] = listColumns
    dictReport["tShape"] = [table.num_rows, len(listColumns)]
    dictReport["bHasHeader"] = True
    for sCol in listColumns:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = table.column(sCol).to_numpy().astype(float)
        except (ValueError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkImage(sFullPath, sFileName, dictReport):
    try:
        from PIL import Image
    except ImportError:
        dictReport["sError"] = "Pillow not installed"
        return
    img = Image.open(sFullPath)
    daPixels = np.array(img, dtype=float)
    dictReport["tShape"] = list(daPixels.shape)
    dictReport["sDtype"] = str(daPixels.dtype)
    dictReport["iNanCount"] = int(np.isnan(daPixels).sum())
    dictReport["iInfCount"] = int(np.isinf(daPixels).sum())
    daFlat = daPixels.flatten()
    _fnAddArrayBenchmarks(daFlat, sFileName, "", dictReport)

def _fnBenchmarkFasta(sFullPath, sFileName, dictReport):
    listIds = []
    listLengths = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        iCurrentLength = 0
        for sLine in fh:
            if sLine.startswith(">"):
                if iCurrentLength > 0:
                    listLengths.append(iCurrentLength)
                listIds.append(sLine[1:].strip().split()[0])
                iCurrentLength = 0
            else:
                iCurrentLength += len(sLine.strip())
        if iCurrentLength > 0:
            listLengths.append(iCurrentLength)
    dictReport["listColumnNames"] = listIds
    dictReport["tShape"] = [len(listLengths)]
    if listLengths:
        daLengths = np.array(listLengths, dtype=float)
        _fnAddArrayBenchmarks(daLengths, sFileName, "seqLength", dictReport)

def _fnBenchmarkFastq(sFullPath, sFileName, dictReport):
    listLengths = []
    listQualities = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listLines = fh.readlines()
    for i in range(0, len(listLines) - 3, 4):
        sSeq = listLines[i + 1].strip()
        sQual = listLines[i + 3].strip()
        listLengths.append(len(sSeq))
        listQualities.append(np.mean([ord(c) - 33 for c in sQual]))
    dictReport["tShape"] = [len(listLengths)]
    if listLengths:
        daLengths = np.array(listLengths, dtype=float)
        _fnAddArrayBenchmarks(daLengths, sFileName, "seqLength", dictReport)
    if listQualities:
        daQuals = np.array(listQualities, dtype=float)
        _fnAddArrayBenchmarks(
            daQuals, sFileName, "quality", dictReport,
            sKeyPrefix="key:quality,",
        )

def _fnBenchmarkTabularWithComments(
    sFullPath, sFileName, dictReport, sCommentPrefix, sHeaderPrefix,
    listDefaultHeaders,
):
    listHeaders = listDefaultHeaders
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if sLine.startswith(sCommentPrefix):
                continue
            if sLine.startswith(sHeaderPrefix) and not listRows:
                listHeaders = sLine.lstrip(sHeaderPrefix).strip().split("\\t")
                continue
            if sLine.strip():
                listRows.append(sLine.strip().split("\\t"))
    dictReport["listColumnNames"] = listHeaders
    dictReport["tShape"] = [len(listRows), len(listHeaders)]
    for iCol, sCol in enumerate(listHeaders):
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array([float(r[iCol]) for r in listRows])
        except (ValueError, IndexError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkVcf(sFullPath, sFileName, dictReport):
    _fnBenchmarkTabularWithComments(
        sFullPath, sFileName, dictReport, "##", "#", [],
    )

def _fnBenchmarkBed(sFullPath, sFileName, dictReport):
    listDefaultHeaders = [
        "chrom", "chromStart", "chromEnd", "name", "score", "strand",
    ]
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if sLine.strip() and not sLine.startswith("#"):
                listRows.append(sLine.strip().split("\\t"))
    if listRows:
        iNumCols = len(listRows[0])
        listHeaders = listDefaultHeaders[:iNumCols]
    else:
        listHeaders = listDefaultHeaders
    dictReport["listColumnNames"] = listHeaders
    dictReport["tShape"] = [len(listRows), len(listHeaders)]
    for iCol, sCol in enumerate(listHeaders):
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array([float(r[iCol]) for r in listRows])
        except (ValueError, IndexError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkGff(sFullPath, sFileName, dictReport):
    listDefaultHeaders = [
        "seqid", "source", "type", "start", "end",
        "score", "strand", "phase", "attributes",
    ]
    _fnBenchmarkTabularWithComments(
        sFullPath, sFileName, dictReport, "#", "\\x00", listDefaultHeaders,
    )

def _fnBenchmarkSam(sFullPath, sFileName, dictReport):
    listHeaders = [
        "QNAME", "FLAG", "RNAME", "POS", "MAPQ", "CIGAR",
        "RNEXT", "PNEXT", "TLEN", "SEQ", "QUAL",
    ]
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if not sLine.startswith("@") and sLine.strip():
                listRows.append(sLine.strip().split("\\t"))
    dictReport["listColumnNames"] = listHeaders
    dictReport["tShape"] = [len(listRows), len(listHeaders)]
    for iCol, sCol in enumerate(listHeaders):
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array([float(r[iCol]) for r in listRows])
        except (ValueError, IndexError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkSyslog(sFullPath, sFileName, dictReport):
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listLines = [s for s in fh if s.strip()]
    dictReport["tShape"] = [len(listLines)]

def _fnBenchmarkCef(sFullPath, sFileName, dictReport):
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listRecords = [s for s in fh if s.strip().startswith("CEF:")]
    dictReport["tShape"] = [len(listRecords)]

def _fnBenchmarkFixedwidth(sFullPath, sFileName, dictReport):
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listLines = [s for s in fh.readlines() if s.strip()]
    if not listLines:
        return
    listTokens = listLines[0].split()
    bAllNumeric = all(_fbIsNumericToken(s) for s in listTokens)
    if bAllNumeric:
        listDataRows = listLines
        listColumns = []
    else:
        listColumns = listTokens
        listDataRows = listLines[1:]
        dictReport["listColumnNames"] = listColumns
    dictReport["tShape"] = [len(listDataRows), len(listDataRows[0].split()) if listDataRows else 0]
    for iCol in range(len(listDataRows[0].split()) if listDataRows else 0):
        sLabel = listColumns[iCol] if iCol < len(listColumns) else f"col{{iCol}}"
        _fnAddWhitespaceColBenchmarks(
            listDataRows, iCol, sLabel, sFileName, dictReport,
        )

def _fnBenchmarkMultitable(sFullPath, sFileName, dictReport):
    import re as reModule
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        sContent = fh.read()
    listSections = reModule.split(r"\\n\\s*\\n|\\n[=\\-]{{3,}}\\n", sContent)
    listSections = [s.strip() for s in listSections if s.strip()]
    dictReport["tShape"] = [len(listSections)]
    for iSec, sSection in enumerate(listSections):
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        listLines = [s for s in sSection.splitlines() if s.strip()]
        if not listLines:
            continue
        listTokens = listLines[0].split()
        bAllNumeric = all(_fbIsNumericToken(s) for s in listTokens)
        if bAllNumeric:
            listDataRows = listLines
        else:
            dictReport["listColumnNames"].extend(listTokens)
            listDataRows = listLines[1:]
        for iCol in range(len(listDataRows[0].split()) if listDataRows else 0):
            _fnAddWhitespaceColBenchmarks(
                listDataRows, iCol, f"sec{{iSec}}_col{{iCol}}",
                sFileName, dictReport,
            )

def _fnBenchmarkBam(sFullPath, sFileName, dictReport):
    try:
        import pysam
    except ImportError:
        dictReport["sError"] = "pysam not installed"
        return
    samfile = pysam.AlignmentFile(sFullPath, "rb")
    listMapq = []
    listTlen = []
    for read in samfile.fetch(until_eof=True):
        listMapq.append(float(read.mapping_quality))
        listTlen.append(float(read.template_length))
        if len(listMapq) >= 100000:
            break
    samfile.close()
    dictReport["tShape"] = [len(listMapq)]
    if listMapq:
        _fnAddArrayBenchmarks(
            np.array(listMapq, dtype=float), sFileName, "MAPQ", dictReport,
            sKeyPrefix="key:mapq,",
        )
    if listTlen:
        _fnAddArrayBenchmarks(
            np.array(listTlen, dtype=float), sFileName, "TLEN", dictReport,
            sKeyPrefix="key:tlen,",
        )

def _fnBenchmarkFortran(sFullPath, sFileName, dictReport):
    try:
        from scipy.io import FortranFile
    except ImportError:
        dictReport["sError"] = "scipy not installed"
        return
    fortranFile = FortranFile(sFullPath, "r")
    listRecords = []
    try:
        while True:
            listRecords.append(fortranFile.read_reals())
    except Exception:
        pass
    fortranFile.close()
    dictReport["tShape"] = [len(listRecords)]
    for iRec, daRecord in enumerate(listRecords):
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        _fnAddArrayBenchmarks(
            daRecord, sFileName, f"record{{iRec}}", dictReport,
            sKeyPrefix=f"key:{{iRec}},",
        )

def _fnBenchmarkDataframe(dfData, sFileName, dictReport):
    listColumns = list(dfData.columns)
    dictReport["listColumnNames"] = listColumns
    dictReport["tShape"] = [len(dfData), len(listColumns)]
    dictReport["bHasHeader"] = True
    for sCol in listColumns:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = dfData[sCol].values.astype(float)
        except (ValueError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkSpss(sFullPath, sFileName, dictReport):
    try:
        import pyreadstat
    except ImportError:
        dictReport["sError"] = "pyreadstat not installed"
        return
    dfData, _ = pyreadstat.read_sav(sFullPath)
    _fnBenchmarkDataframe(dfData, sFileName, dictReport)

def _fnBenchmarkStata(sFullPath, sFileName, dictReport):
    try:
        import pyreadstat
    except ImportError:
        dictReport["sError"] = "pyreadstat not installed"
        return
    dfData, _ = pyreadstat.read_dta(sFullPath)
    _fnBenchmarkDataframe(dfData, sFileName, dictReport)

def _fnBenchmarkSas(sFullPath, sFileName, dictReport):
    try:
        import pyreadstat
    except ImportError:
        dictReport["sError"] = "pyreadstat not installed"
        return
    dfData, _ = pyreadstat.read_sas7bdat(sFullPath)
    _fnBenchmarkDataframe(dfData, sFileName, dictReport)

def _fnBenchmarkRdata(sFullPath, sFileName, dictReport):
    try:
        import pyreadr
    except ImportError:
        dictReport["sError"] = "pyreadr not installed"
        return
    dictFrames = pyreadr.read_r(sFullPath)
    sFirstKey = list(dictFrames.keys())[0]
    dfData = dictFrames[sFirstKey]
    dictReport["listColumnNames"] = list(dictFrames.keys())
    _fnBenchmarkDataframe(dfData, sFileName, dictReport)

def _fnBenchmarkVotable(sFullPath, sFileName, dictReport):
    try:
        from astropy.io.votable import parse as votableParse
    except ImportError:
        dictReport["sError"] = "astropy not installed"
        return
    votable = votableParse(sFullPath)
    table = votable.get_first_table().to_table()
    listColumns = list(table.colnames)
    dictReport["listColumnNames"] = listColumns
    dictReport["tShape"] = [len(table), len(listColumns)]
    for sCol in listColumns:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array(table[sCol], dtype=float)
        except (ValueError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkIpac(sFullPath, sFileName, dictReport):
    try:
        from astropy.io import ascii as astropyAscii
    except ImportError:
        dictReport["sError"] = "astropy not installed"
        return
    table = astropyAscii.read(sFullPath, format="ipac")
    listColumns = list(table.colnames)
    dictReport["listColumnNames"] = listColumns
    dictReport["tShape"] = [len(table), len(listColumns)]
    for sCol in listColumns:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daValues = np.array(table[sCol], dtype=float)
        except (ValueError, TypeError):
            continue
        _fnAddStatsBenchmarks(
            daValues, sCol, sFileName, f"column:{{sCol}},", dictReport,
        )

def _fnBenchmarkPcap(sFullPath, sFileName, dictReport):
    try:
        from scapy.all import rdpcap
    except ImportError:
        dictReport["sError"] = "scapy not installed"
        return
    listPackets = rdpcap(sFullPath)
    listLengths = [float(len(p)) for p in listPackets]
    dictReport["tShape"] = [len(listLengths)]
    if listLengths:
        _fnAddArrayBenchmarks(
            np.array(listLengths, dtype=float), sFileName,
            "packetLength", dictReport,
        )

def _fnBenchmarkVtk(sFullPath, sFileName, dictReport):
    try:
        import pyvista
    except ImportError:
        dictReport["sError"] = "pyvista not installed"
        return
    mesh = pyvista.read(sFullPath)
    listArrayNames = list(mesh.array_names)
    dictReport["listColumnNames"] = listArrayNames
    for sArrayName in listArrayNames:
        if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
            break
        try:
            daData = np.array(mesh[sArrayName], dtype=float).flatten()
        except (ValueError, TypeError):
            continue
        _fnAddArrayBenchmarks(
            daData, sFileName, sArrayName, dictReport,
            sKeyPrefix=f"key:{{sArrayName}},",
        )

def _fnBenchmarkCgns(sFullPath, sFileName, dictReport):
    import h5py
    with h5py.File(sFullPath, "r") as fh:
        listDatasets = []
        fh.visititems(
            lambda n, o: listDatasets.append(n)
            if isinstance(o, h5py.Dataset) else None
        )
        for sDataset in listDatasets[:50]:
            if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
                break
            daData = np.array(fh[sDataset])
            dictReport["tShape"] = list(daData.shape)
            dictReport["sDtype"] = str(daData.dtype)
            if np.issubdtype(daData.dtype, np.number):
                _fnAddArrayBenchmarks(
                    daData.flatten(), sFileName, sDataset,
                    dictReport,
                    sKeyPrefix=f"dataset:{{sDataset}},",
                )

def _fnBenchmarkSafetensors(sFullPath, sFileName, dictReport):
    try:
        from safetensors import safe_open
    except ImportError:
        dictReport["sError"] = "safetensors not installed"
        return
    with safe_open(sFullPath, framework="numpy") as fh:
        listTensorNames = list(fh.keys())
        dictReport["listColumnNames"] = listTensorNames
        for sTensorName in listTensorNames:
            if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
                break
            daData = fh.get_tensor(sTensorName).astype(float).flatten()
            dictReport["tShape"] = list(fh.get_tensor(sTensorName).shape)
            _fnAddArrayBenchmarks(
                daData, sFileName, sTensorName, dictReport,
                sKeyPrefix=f"key:{{sTensorName}},",
            )

def _fnBenchmarkTfrecord(sFullPath, sFileName, dictReport):
    try:
        from tfrecord.reader import tfrecord_iterator
    except ImportError:
        dictReport["sError"] = "tfrecord not installed"
        return
    listRecords = []
    for record in tfrecord_iterator(sFullPath):
        listRecords.append(record)
    dictReport["tShape"] = [len(listRecords)]
    if listRecords and isinstance(listRecords[0], dict):
        listKeys = list(listRecords[0].keys())
        dictReport["listColumnNames"] = listKeys
        for sKey in listKeys:
            if len(dictReport["listBenchmarks"]) >= _I_MAX_BENCHMARKS_PER_FILE:
                break
            try:
                daValues = np.array(
                    [float(r[sKey]) for r in listRecords], dtype=float,
                )
            except (ValueError, KeyError, TypeError):
                continue
            _fnAddStatsBenchmarks(
                daValues, sKey, sFileName, f"key:{{sKey}},", dictReport,
            )

sDirectory = {sDirectoryRepr}
listDataFiles = {sFileListRepr}
listReports = []
for sFile in listDataFiles:
    listReports.append(_fdictIntrospectFile(sFile, sDirectory))
print(json.dumps(listReports))
'''


def _fsRunIntrospection(
    connectionDocker, sContainerId, sDirectory, listDataFiles,
    bScriptStochastic=False,
):
    """Run introspection script in container, return parsed reports."""
    import secrets
    sScript = _fsBuildIntrospectionScript(
        listDataFiles, sDirectory,
        bScriptStochastic=bScriptStochastic,
    )
    sScriptPath = f"/tmp/_vaibify_introspect_{secrets.token_hex(8)}.py"
    connectionDocker.fnWriteFile(
        sContainerId, sScriptPath, sScript.encode("utf-8"),
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"python3 {sScriptPath}",
    )
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -f {sScriptPath}",
    )
    if iExitCode != 0:
        raise RuntimeError(
            f"Introspection failed (exit {iExitCode}): {sOutput}"
        )
    return _flistParseIntrospectionOutput(sOutput)


def _flistParseIntrospectionOutput(sOutput):
    """Extract JSON from introspection output, ignoring non-JSON lines."""
    sStripped = sOutput.strip()
    try:
        return json.loads(sStripped)
    except json.JSONDecodeError:
        pass
    for sLine in reversed(sStripped.splitlines()):
        sLine = sLine.strip()
        if sLine.startswith("["):
            try:
                return json.loads(sLine)
            except json.JSONDecodeError:
                continue
    raise ValueError(
        f"Introspection output is not valid JSON: {sStripped[:200]}"
    )

