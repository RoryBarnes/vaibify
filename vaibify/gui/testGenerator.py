"""Generate pytest unit tests for workflow steps via LLM."""

import json
import logging
import os
import posixpath
import re

logger = logging.getLogger("vaibify")

# ---------------------------------------------------------------------------
# Re-exports from leaf modules -- every name that was previously defined here
# remains importable from vaibify.gui.testGenerator.
# ---------------------------------------------------------------------------

from .testParser import (  # noqa: F401
    fsParseGeneratedCode,
    fbValidatePythonSyntax,
    fsRepairMissingImports,
    fdictParseCombinedOutput,
    fdictParseQuantitativeJson,
)

from .dataPreview import (  # noqa: F401
    fsPreviewDataFile,
    _fsResolvePath,
    _fsPreviewNpy,
    _fsPreviewHdf5,
    _fsPreviewText,
)

from .conftestManager import (  # noqa: F401
    fsConftestPath,
    fsConftestContent,
    fnWriteConftestMarker,
    _CONFTEST_MARKER_TEMPLATE,
    fnEnsureTestsDirectory,
)

from .llmInvoker import (  # noqa: F401
    _PROMPT_TEMPLATE,
    _CLAUDE_MD_TEST_SECTION,
    _CLAUDE_MD_MARKER,
    _CLAUDE_MD_VERSION,
    _CLAUDE_MD_VERSION_TAG,
    fnEnsureClaudeMdInstructions,
    _fsRemoveOldTestSection,
    fbContainerHasClaude,
    fsReadFileFromContainer,
    fsBuildPrompt,
    ftResultGenerateViaClaude,
    fsGenerateViaApi,
    _fbOutputLooksValid,
    _fnRaiseClaudeError,
    _fsInvokeLlm,
    _fsBuildCategoryPrompt,
    _fsBuildQuantitativePrompt,
)

from .templateManager import (  # noqa: F401
    _fsComputeTemplateHash,
    _fsEmbedTemplateHash,
    _fbFileMatchesTemplate,
    fsQuantitativeTemplateHash,
    fsIntegrityTemplateHash,
    fsQualitativeTemplateHash,
    _QUANTITATIVE_TEMPLATE_HEADER,
    _QUANTITATIVE_TEMPLATE_FOOTER,
    fsBuildQuantitativeTestCode,
    _INTEGRITY_TEST_TEMPLATE,
    fsBuildIntegrityTestCode,
    _QUALITATIVE_TEST_TEMPLATE,
    fsBuildQualitativeTestCode,
)

# ---------------------------------------------------------------------------
# Path helpers (remain in orchestrator)
# ---------------------------------------------------------------------------


def fsTestFilePath(sDirectory, iStepIndex):
    """Return the test file path for a given step."""
    sFilename = f"test_step{iStepIndex + 1:02d}.py"
    return posixpath.join(sDirectory, sFilename)


def fsIntegrityTestPath(sStepDirectory):
    """Return the integrity test file path for a step."""
    return posixpath.join(sStepDirectory, "tests", "test_integrity.py")


def fsQualitativeTestPath(sStepDirectory):
    """Return the qualitative test file path for a step."""
    return posixpath.join(sStepDirectory, "tests", "test_qualitative.py")


def fsQuantitativeTestPath(sStepDirectory):
    """Return the quantitative test file path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "test_quantitative.py",
    )


def fsQuantitativeStandardsPath(sStepDirectory):
    """Return the quantitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "quantitative_standards.json",
    )


def fsIntegrityStandardsPath(sStepDirectory):
    """Return the integrity standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "integrity_standards.json",
    )


def fsQualitativeStandardsPath(sStepDirectory):
    """Return the qualitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "qualitative_standards.json",
    )


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------


def fsBuildStepContext(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Gather script source code and data file previews for a step."""
    sDirectory = dictStep.get("sDirectory", "")
    sScripts = _fsBuildScriptContents(
        connectionDocker, sContainerId, dictStep, sDirectory
    )
    sPreviews = _fsBuildDataPreviews(
        connectionDocker, sContainerId, dictStep, sDirectory
    )
    return sScripts, sPreviews


def _fsBuildScriptContents(
    connectionDocker, sContainerId, dictStep, sDirectory,
):
    """Read and concatenate source code of data analysis scripts."""
    listParts = []
    for sCommand in dictStep.get("saDataCommands", []):
        sScript = _fsExtractScriptFromCommand(sCommand)
        if not sScript:
            continue
        sPath = _fsResolvePath(sScript, sDirectory)
        sContent = fsReadFileFromContainer(
            connectionDocker, sContainerId, sPath
        )
        if sContent:
            listLines = sContent.splitlines()[:200]
            listParts.append(
                f"--- {sScript} ---\n" + "\n".join(listLines)
            )
    return "\n\n".join(listParts) if listParts else "(no scripts found)"


def _fsExtractScriptFromCommand(sCommand):
    """Extract the Python script path from a command string."""
    from .commandUtilities import fsExtractScriptPath
    return fsExtractScriptPath(sCommand) or None


def _fsBuildDataPreviews(
    connectionDocker, sContainerId, dictStep, sDirectory,
):
    """Generate previews for each data output file."""
    listParts = []
    for sFile in dictStep.get("saDataFiles", []):
        sPreview = fsPreviewDataFile(
            connectionDocker, sContainerId, sFile, sDirectory
        )
        listParts.append(f"{sFile}: {sPreview}")
    return "\n".join(listParts) if listParts else "(no data files)"


# ---------------------------------------------------------------------------
# Test file writing helpers
# ---------------------------------------------------------------------------


def _fdictWriteTestFile(connectionDocker, sContainerId, sCode, sFilePath):
    """Write a test file to the container and return result dict."""
    try:
        connectionDocker.fnWriteFile(
            sContainerId, sFilePath, sCode.encode("utf-8"),
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to write test file {sFilePath}: {exc}"
        ) from exc
    sFilename = posixpath.basename(sFilePath)
    return {
        "sFilePath": sFilePath,
        "sContent": sCode,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _ftExtractStepInfo(dictWorkflow, iStepIndex):
    """Return (dictStep, sDirectory) for the given step index."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    return dictStep, dictStep.get("sDirectory", "")


# ---------------------------------------------------------------------------
# Single-step LLM generation
# ---------------------------------------------------------------------------


def fdictGenerateTest(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None,
):
    """Orchestrate test generation: gather context, call LLM, save."""
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    sScripts, sPreviews = fsBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables
    )
    sPrompt = fsBuildPrompt(
        sDirectory, dictStep, sScripts, sPreviews
    )
    sRawOutput = _fsInvokeLlm(
        connectionDocker, sContainerId, sPrompt, bUseApi, sApiKey,
        sUser=sUser,
    )
    sCode = fsParseGeneratedCode(sRawOutput)
    sFilePath = fsTestFilePath(sDirectory, iStepIndex)
    return _fdictWriteTestFile(
        connectionDocker, sContainerId, sCode, sFilePath,
    )


# ---------------------------------------------------------------------------
# Introspection script (large f-string, stays in orchestrator)
# ---------------------------------------------------------------------------


def _fsFormatSafeName(sFileName):
    """Convert a filename to a valid Python identifier."""
    sBase = posixpath.splitext(sFileName)[0]
    sSafe = re.sub(r"[^a-zA-Z0-9]", "_", sBase)
    if sSafe and sSafe[0].isdigit():
        sSafe = "f" + sSafe
    return sSafe


def _fsBuildIntrospectionScript(listDataFiles, sDirectory):
    """Return a self-contained Python script that introspects data files."""
    sFileListRepr = repr(listDataFiles)
    sDirectoryRepr = repr(sDirectory)
    return f'''import json
import os
import sys
import traceback

import numpy as np

_I_MAX_FILE_BYTES = 500_000_000
_I_MAX_BENCHMARKS_PER_FILE = 250

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

def _fnAddArrayBenchmarks(
    daFlat, sFileName, sLabel, dictReport, sKeyPrefix="",
):
    sPrefix = sLabel or os.path.splitext(sFileName)[0]
    if len(daFlat) == 0:
        return
    listBench = dictReport["listBenchmarks"]
    listBench.append({{
        "sName": f"f{{sPrefix}}First",
        "sDataFile": sFileName,
        "sAccessPath": f"{{sKeyPrefix}}index:0",
        "fValue": float(daFlat[0]),
    }})
    listBench.append({{
        "sName": f"f{{sPrefix}}Last",
        "sDataFile": sFileName,
        "sAccessPath": f"{{sKeyPrefix}}index:-1",
        "fValue": float(daFlat[-1]),
    }})
    if np.issubdtype(daFlat.dtype, np.number):
        listBench.append({{
            "sName": f"f{{sPrefix}}Mean",
            "sDataFile": sFileName,
            "sAccessPath": f"{{sKeyPrefix}}index:mean",
            "fValue": float(daFlat.mean()),
        }})
        listBench.append({{
            "sName": f"f{{sPrefix}}Min",
            "sDataFile": sFileName,
            "sAccessPath": f"{{sKeyPrefix}}index:min",
            "fValue": float(daFlat.min()),
        }})
        listBench.append({{
            "sName": f"f{{sPrefix}}Max",
            "sDataFile": sFileName,
            "sAccessPath": f"{{sKeyPrefix}}index:max",
            "fValue": float(daFlat.max()),
        }})

def _fnAddStatsBenchmarks(
    daValues, sLabel, sFileName, sAccessPrefix, dictReport,
):
    listBench = dictReport["listBenchmarks"]
    listBench.append({{
        "sName": f"f{{sLabel}}First", "sDataFile": sFileName,
        "sAccessPath": f"{{sAccessPrefix}}index:0",
        "fValue": float(daValues[0]),
    }})
    listBench.append({{
        "sName": f"f{{sLabel}}Last", "sDataFile": sFileName,
        "sAccessPath": f"{{sAccessPrefix}}index:-1",
        "fValue": float(daValues[-1]),
    }})
    listBench.append({{
        "sName": f"f{{sLabel}}Mean", "sDataFile": sFileName,
        "sAccessPath": f"{{sAccessPrefix}}index:mean",
        "fValue": float(daValues.mean()),
    }})
    listBench.append({{
        "sName": f"f{{sLabel}}Min", "sDataFile": sFileName,
        "sAccessPath": f"{{sAccessPrefix}}index:min",
        "fValue": float(daValues.min()),
    }})
    listBench.append({{
        "sName": f"f{{sLabel}}Max", "sDataFile": sFileName,
        "sAccessPath": f"{{sAccessPrefix}}index:max",
        "fValue": float(daValues.max()),
    }})

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


# ---------------------------------------------------------------------------
# Introspection execution
# ---------------------------------------------------------------------------


def _fsRunIntrospection(
    connectionDocker, sContainerId, sDirectory, listDataFiles,
):
    """Run introspection script in container, return parsed reports."""
    import secrets
    sScript = _fsBuildIntrospectionScript(listDataFiles, sDirectory)
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


# ---------------------------------------------------------------------------
# Standards builders
# ---------------------------------------------------------------------------


_SET_NONAN_FORMATS = {
    "npy", "npz", "csv", "whitespace", "fits", "matlab",
    "parquet", "image", "vcf", "bed", "gff", "sam",
    "fortran", "spss", "stata", "sas", "votable", "ipac",
    "vtk", "cgns", "safetensors", "excel", "rdata", "hdf5",
}


def _fbShouldAddNoNanTest(dictReport):
    """Return True if this report qualifies for a no-NaN test."""
    if not dictReport.get("bLoadable"):
        return False
    if dictReport.get("iNanCount", 0) != 0:
        return False
    if dictReport.get("iInfCount", 0) != 0:
        return False
    return dictReport.get("sFormat", "") in _SET_NONAN_FORMATS


def _fsGenerateIntegrityCode(listdictReports):
    """Produce integrity_standards.json dict from introspection reports.

    Deprecated: kept for backward compatibility. Use
    _fdictBuildIntegrityStandards instead.
    """
    dictStandards = _fdictBuildIntegrityStandards(listdictReports)
    return json.dumps(dictStandards, indent=4)


def _fsGenerateQualitativeCode(listdictReports):
    """Produce qualitative_standards.json dict from introspection reports.

    Deprecated: kept for backward compatibility. Use
    _fdictBuildQualitativeStandards instead.
    """
    dictStandards = _fdictBuildQualitativeStandards(listdictReports)
    return json.dumps(dictStandards, indent=4)


def _fdictBuildQuantitativeStandards(listdictReports, fTolerance):
    """Build quantitative_standards.json dict from introspection reports."""
    listStandards = []
    for dictReport in listdictReports:
        for dictBenchmark in dictReport.get("listBenchmarks", []):
            dictStandard = {
                "sName": dictBenchmark["sName"],
                "sDataFile": dictBenchmark["sDataFile"],
                "sAccessPath": dictBenchmark["sAccessPath"],
                "fValue": dictBenchmark["fValue"],
                "sUnit": "",
            }
            if "sFormat" in dictBenchmark:
                dictStandard["sFormat"] = dictBenchmark["sFormat"]
            listStandards.append(dictStandard)
    return {
        "fDefaultRtol": fTolerance,
        "listStandards": listStandards,
    }


def _fdictBuildOneIntegrityEntry(dictReport):
    """Build one integrity standard entry from an introspection report."""
    return {
        "sFileName": dictReport["sFileName"],
        "sFormat": dictReport.get("sFormat", ""),
        "tExpectedShape": dictReport.get("tShape"),
        "sDtype": dictReport.get("sDtype", ""),
        "bCheckNaN": _fbShouldAddNoNanTest(dictReport),
        "bCheckInf": _fbShouldAddNoNanTest(dictReport),
        "iExpectedByteSize": dictReport.get("iByteSize", 0),
    }


def _fdictBuildIntegrityStandards(listdictReports):
    """Build integrity_standards.json dict from introspection reports."""
    listStandards = [
        _fdictBuildOneIntegrityEntry(r) for r in listdictReports
        if r.get("bExists", False)
    ]
    return {"listStandards": listStandards}


def _fdictBuildOneQualitativeEntry(dictReport):
    """Build one qualitative standard entry from a report."""
    return {
        "sFileName": dictReport["sFileName"],
        "sFormat": dictReport.get("sFormat", ""),
        "listExpectedColumns": dictReport.get("listColumnNames", []),
        "listExpectedJsonKeys": dictReport.get("listJsonTopKeys", []),
    }


def _fbHasQualitativeContent(dictReport):
    """Return True if report has column names or JSON keys."""
    if dictReport.get("listColumnNames"):
        return True
    return bool(dictReport.get("listJsonTopKeys"))


def _fdictBuildQualitativeStandards(listdictReports):
    """Build qualitative_standards.json dict from introspection reports."""
    listStandards = [
        _fdictBuildOneQualitativeEntry(r) for r in listdictReports
        if _fbHasQualitativeContent(r)
    ]
    return {"listStandards": listStandards}


def _fnWarnIfAllUnloadable(listdictReports):
    """Log a warning if every report failed to load."""
    bAllUnloadable = all(
        not r.get("bLoadable") for r in listdictReports
    )
    if bAllUnloadable and listdictReports:
        listErrors = [r.get("sError", "") for r in listdictReports]
        logger.warning("All files unloadable: %s", listErrors)


# ---------------------------------------------------------------------------
# Deterministic test generation
# ---------------------------------------------------------------------------


def fdictGenerateAllTestsDeterministic(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bForceOverwrite=False,
):
    """Generate all three test categories deterministically."""
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    listDataFiles = dictStep.get("saDataFiles", [])
    if not listDataFiles:
        logger.warning(
            "No data files for step %d; generating minimal tests",
            iStepIndex,
        )
    fnEnsureTestsDirectory(connectionDocker, sContainerId, sDirectory)
    listdictReports = _fsRunIntrospection(
        connectionDocker, sContainerId, sDirectory, listDataFiles,
    )
    _fnWarnIfAllUnloadable(listdictReports)
    return _fdictWriteAllDeterministicTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, fTolerance, bForceOverwrite,
    )


def _fdictWriteAllDeterministicTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, fTolerance, bForceOverwrite=False,
):
    """Write all three deterministic test files and return result dict."""
    fnWriteConftestMarker(connectionDocker, sContainerId, sDirectory)
    dictResult = {}
    listModified = []
    dictResult["dictIntegrity"] = _fdictWriteIntegrityTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, bForceOverwrite,
    )
    if dictResult["dictIntegrity"].get("bNeedsOverwriteConfirm"):
        listModified.append(dictResult["dictIntegrity"]["sFilePath"])
    dictResult["dictQualitative"] = _fdictWriteQualitativeTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, bForceOverwrite,
    )
    if dictResult["dictQualitative"].get("bNeedsOverwriteConfirm"):
        listModified.append(
            dictResult["dictQualitative"]["sFilePath"]
        )
    dictResult["dictQuantitative"] = _fdictWriteQuantitativeTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, fTolerance, bForceOverwrite,
    )
    if dictResult["dictQuantitative"].get("bNeedsOverwriteConfirm"):
        listModified.append(
            dictResult["dictQuantitative"]["sFilePath"]
        )
    if listModified:
        dictResult["bNeedsOverwriteConfirm"] = True
        dictResult["listModifiedFiles"] = listModified
    return dictResult


def _fdictWriteQuantitativeFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write quantitative standards JSON and test file, return dict."""
    sStandardsPath = fsQuantitativeStandardsPath(sDirectory)
    sJsonContent = json.dumps(dictStandards, indent=4)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath,
        sJsonContent.encode("utf-8"),
    )
    sTestCode = fsBuildQuantitativeTestCode()
    sTestPath = fsQuantitativeTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath,
        sTestCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _fdictWriteQuantitativeTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, fTolerance, bForceOverwrite=False,
):
    """Build standards from reports and write quantitative test files."""
    dictStandards = _fdictBuildQuantitativeStandards(
        listdictReports, fTolerance,
    )
    return _fdictWriteQuantitativeFiles(
        connectionDocker, sContainerId, sDirectory,
        dictStandards, bForceOverwrite,
    )


def _fdictWriteIntegrityFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write integrity standards JSON and test file, return dict."""
    sStandardsPath = fsIntegrityStandardsPath(sDirectory)
    sJsonContent = json.dumps(dictStandards, indent=4)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath,
        sJsonContent.encode("utf-8"),
    )
    sTestCode = fsBuildIntegrityTestCode()
    sTestPath = fsIntegrityTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath, sTestCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _fdictWriteIntegrityTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, bForceOverwrite=False,
):
    """Build standards and write integrity test files."""
    dictStandards = _fdictBuildIntegrityStandards(listdictReports)
    return _fdictWriteIntegrityFiles(
        connectionDocker, sContainerId, sDirectory,
        dictStandards, bForceOverwrite,
    )


def _fdictWriteQualitativeFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards, bForceOverwrite=False,
):
    """Write qualitative standards JSON and test file, return dict."""
    sStandardsPath = fsQualitativeStandardsPath(sDirectory)
    sJsonContent = json.dumps(dictStandards, indent=4)
    connectionDocker.fnWriteFile(
        sContainerId, sStandardsPath,
        sJsonContent.encode("utf-8"),
    )
    sTestCode = fsBuildQualitativeTestCode()
    sTestPath = fsQualitativeTestPath(sDirectory)
    if not bForceOverwrite and not _fbFileMatchesTemplate(
        connectionDocker, sContainerId, sTestPath, sTestCode,
    ):
        return {"bNeedsOverwriteConfirm": True, "sFilePath": sTestPath}
    connectionDocker.fnWriteFile(
        sContainerId, sTestPath, sTestCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sTestPath)
    return {
        "sFilePath": sTestPath,
        "sContent": sTestCode,
        "sStandardsPath": sStandardsPath,
        "sStandardsContent": sJsonContent,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def _fdictWriteQualitativeTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, bForceOverwrite=False,
):
    """Build standards and write qualitative test files."""
    dictStandards = _fdictBuildQualitativeStandards(listdictReports)
    return _fdictWriteQualitativeFiles(
        connectionDocker, sContainerId, sDirectory,
        dictStandards, bForceOverwrite,
    )


# ---------------------------------------------------------------------------
# LLM-based test generation
# ---------------------------------------------------------------------------


def fdictGenerateAllTests(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None, bDeterministic=True, bForceOverwrite=False,
):
    """Generate all three test categories via LLM or deterministically."""
    if bDeterministic:
        return fdictGenerateAllTestsDeterministic(
            connectionDocker, sContainerId, iStepIndex,
            dictWorkflow, dictVariables, bForceOverwrite,
        )
    return _fdictGenerateAllTestsViaLlm(
        connectionDocker, sContainerId, iStepIndex,
        dictWorkflow, dictVariables, bUseApi, sApiKey, sUser,
    )


def _fdictGenerateAllTestsViaLlm(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi, sApiKey, sUser,
):
    """Generate all three test categories via LLM."""
    dictStep, sDirectory = _ftExtractStepInfo(dictWorkflow, iStepIndex)
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    sDataFiles = ", ".join(dictStep.get("saDataFiles", []))
    sScripts, sPreviews = fsBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables,
    )
    if not bUseApi:
        fnEnsureClaudeMdInstructions(connectionDocker, sContainerId)
    fnEnsureTestsDirectory(connectionDocker, sContainerId, sDirectory)
    fnWriteConftestMarker(connectionDocker, sContainerId, sDirectory)
    return _fdictDispatchLlmCategories(
        connectionDocker, sContainerId, sDirectory,
        sDataFiles, sScripts, sPreviews,
        fTolerance, bUseApi, sApiKey, sUser,
    )


def _fdictDispatchLlmCategories(
    connectionDocker, sContainerId, sDirectory,
    sDataFiles, sScripts, sPreviews,
    fTolerance, bUseApi, sApiKey, sUser,
):
    """Dispatch LLM generation for each test category."""
    dictResult = {}
    for sCategory in ("integrity", "qualitative"):
        dictResult[f"dict{sCategory.capitalize()}"] = (
            _fdictGenerateSingleCategory(
                connectionDocker, sContainerId, sDirectory,
                sCategory, sDataFiles, sScripts, sPreviews,
                bUseApi, sApiKey, sUser,
            )
        )
    dictResult["dictQuantitative"] = _fdictGenerateQuantitativeCategory(
        connectionDocker, sContainerId, sDirectory,
        sDataFiles, sScripts, sPreviews,
        fTolerance, bUseApi, sApiKey, sUser,
    )
    return dictResult


_DICT_CATEGORY_PATHS = {
    "integrity": fsIntegrityTestPath,
    "qualitative": fsQualitativeTestPath,
}


def _fdictGenerateSingleCategory(
    connectionDocker, sContainerId, sDirectory,
    sCategory, sDataFiles, sScriptContents, sDataPreviews,
    bUseApi, sApiKey, sUser,
):
    """Generate one Python test category via LLM, with error isolation."""
    sPrompt = _fsBuildCategoryPrompt(
        sCategory, sDirectory, sDataFiles, sScriptContents, sDataPreviews,
    )
    sFilePath = _DICT_CATEGORY_PATHS[sCategory](sDirectory)
    sRaw = ""
    try:
        sRaw = _fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        sCode = fsParseGeneratedCode(sRaw)
        return _fdictWriteTestFile(
            connectionDocker, sContainerId, sCode, sFilePath,
        )
    except Exception as error:
        _fnAppendErrorLog(
            f"[{sCategory}] {error}\n"
            f"First 300 chars of raw output:\n{sRaw[:300]}"
        )
        return _fdictErrorResult(str(error))


def _fdictGenerateQuantitativeCategory(
    connectionDocker, sContainerId, sDirectory,
    sDataFiles, sScriptContents, sDataPreviews,
    fTolerance, bUseApi, sApiKey, sUser,
):
    """Generate quantitative standards JSON via LLM."""
    sPrompt = _fsBuildQuantitativePrompt(
        sDirectory, sDataFiles, sScriptContents,
        sDataPreviews, fTolerance,
    )
    try:
        sRaw = _fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        logger.debug("Quantitative raw output: %s", sRaw[:500])
        dictStandards = fdictParseQuantitativeJson(sRaw)
        dictStandards["fDefaultRtol"] = fTolerance
        return _fdictWriteQuantitativeFiles(
            connectionDocker, sContainerId, sDirectory,
            dictStandards,
        )
    except Exception as error:
        return _fdictErrorResult(str(error))


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _fdictErrorResult(sMessage):
    """Return a standard error dict for a failed category."""
    logger.error("Test category error: %s", sMessage)
    _fnAppendErrorLog(sMessage)
    return {
        "sFilePath": "",
        "sContent": "",
        "saCommands": [],
        "sError": sMessage,
    }


def _fnAppendErrorLog(sMessage):
    """Append error details to a local log file for debugging."""
    import tempfile
    sLogPath = os.path.join(tempfile.gettempdir(), "vaibify_test_errors.log")
    try:
        with open(sLogPath, "a", encoding="utf-8") as fLog:
            fLog.write(sMessage + "\n---\n")
    except Exception:
        pass
