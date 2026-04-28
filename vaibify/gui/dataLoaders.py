"""Data file loaders for quantitative benchmark tests.

This module is the single source of truth for all data-loading logic used
by vaibify's quantitative test templates.  The function
``fsReadLoaderSource()`` returns the Python source between the
begin/end markers in this file.
testGenerator.py embeds this source verbatim into the self-contained
test file deployed to containers.

Public API
----------
fLoadValue(sDataFile, sAccessPath, sStepDirectory, sFormat="")
    Load a single scalar value from any supported data file.

DICT_FORMAT_MAP : dict
    Maps file extensions (e.g. ".csv") to canonical format strings.

fsReadLoaderSource() -> str
    Return the embeddable loader source code between the markers.
"""

__all__ = [
    "DICT_FORMAT_MAP",
    "DICT_LOADERS",
    "fLoadValue",
    "fsReadLoaderSource",
]

import json
import pathlib
import re

import numpy as np


# -- begin loader source ---------------------------------------------------
import json
import pathlib
import re

import numpy as np


_DICT_FORMAT_MAP = {
    ".npy": "npy",
    ".npz": "npz",
    ".json": "json",
    ".csv": "csv",
    ".h5": "hdf5",
    ".hdf5": "hdf5",
    ".dat": "whitespace",
    ".txt": "whitespace",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".xlsx": "excel",
    ".xls": "excel",
    ".fits": "fits",
    ".fit": "fits",
    ".mat": "matlab",
    ".parquet": "parquet",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tiff": "image",
    ".tif": "image",
    ".fasta": "fasta",
    ".fa": "fasta",
    ".fastq": "fastq",
    ".fq": "fastq",
    ".vcf": "vcf",
    ".bed": "bed",
    ".gff": "gff",
    ".gtf": "gff",
    ".gff3": "gff",
    ".sam": "sam",
    ".log": "syslog",
    ".cef": "cef",
    ".bam": "bam",
    ".unf": "fortran",
    ".sav": "spss",
    ".dta": "stata",
    ".sas7bdat": "sas",
    ".rds": "rdata",
    ".RData": "rdata",
    ".rda": "rdata",
    ".vot": "votable",
    ".ipac": "ipac",
    ".pcap": "pcap",
    ".pcapng": "pcap",
    ".vtk": "vtk",
    ".vtu": "vtk",
    ".cgns": "cgns",
    ".safetensors": "safetensors",
    ".tfrecord": "tfrecord",
}


def _fsInferFormat(sFullPath):
    """Infer the data format from the file extension."""
    sExtension = pathlib.Path(sFullPath).suffix.lower()
    return _DICT_FORMAT_MAP.get(sExtension, None)


def _fdictParseAccessPath(sAccessPath):
    """Parse an access path string into a dict of components."""
    dictResult = {}
    matchKey = re.match(r"key:([^,]+)", sAccessPath)
    if matchKey:
        dictResult["key"] = matchKey.group(1)
    matchColumn = re.search(r"column:([^,]+)", sAccessPath)
    if matchColumn:
        dictResult["column"] = matchColumn.group(1)
    matchDataset = re.search(r"dataset:([^,]+)", sAccessPath)
    if matchDataset:
        dictResult["dataset"] = matchDataset.group(1)
    matchSection = re.search(r"section:(\d+)", sAccessPath)
    if matchSection:
        dictResult["iSection"] = int(matchSection.group(1))
    matchHdu = re.search(r"hdu:(\d+)", sAccessPath)
    if matchHdu:
        dictResult["iHdu"] = int(matchHdu.group(1))
    matchAggregate = re.search(
        r"index:(mean|min|max|std|p25|p50|p75|p95|p5)\b",
        sAccessPath,
    )
    if matchAggregate:
        dictResult["sAggregate"] = matchAggregate.group(1)
    else:
        matchIndex = re.search(r"index:([-\d,]+)", sAccessPath)
        if matchIndex:
            dictResult["listIndices"] = [
                int(x) for x in matchIndex.group(1).split(",")
                if x.strip()
            ]
    return dictResult


_DICT_PERCENTILE_AGGREGATES = {
    "p5": 5, "p25": 25, "p50": 50, "p75": 75, "p95": 95,
}


def _fApplyAggregate(daData, sAggregate):
    """Return a scalar aggregate (mean/min/max/std/percentile) of daData."""
    if sAggregate == "mean":
        return float(daData.mean())
    if sAggregate == "min":
        return float(daData.min())
    if sAggregate == "max":
        return float(daData.max())
    if sAggregate == "std":
        return float(daData.std(ddof=1))
    if sAggregate in _DICT_PERCENTILE_AGGREGATES:
        return float(np.percentile(
            daData, _DICT_PERCENTILE_AGGREGATES[sAggregate],
        ))
    raise ValueError(f"Unknown aggregate: {sAggregate}")


def _fExtractArrayValue(daData, dictAccess):
    """Extract a scalar from an array by aggregate or index."""
    if daData.ndim == 0:
        return float(daData)
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate:
        return _fApplyAggregate(daData, sAggregate)
    listIndices = dictAccess.get("listIndices", [-1])
    if len(listIndices) == 1 and daData.ndim > 1:
        return float(daData.flat[listIndices[0]])
    return float(daData[tuple(listIndices)])


def _fExtractTabularValue(listHeaders, listRows, dictAccess):
    """Extract a value from parsed tabular data."""
    sColumn = dictAccess.get("column", "")
    if sColumn and listHeaders:
        iCol = listHeaders.index(sColumn)
    else:
        iCol = 0
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate:
        daValues = np.array([float(r[iCol]) for r in listRows])
        return _fApplyAggregate(daValues, sAggregate)
    listIndices = dictAccess.get("listIndices", [-1])
    iRow = listIndices[0] if listIndices else -1
    return float(listRows[iRow][iCol])


def _fExtractDataframeValue(dfData, dictAccess, sFullPath=""):
    """Extract a value from a pandas DataFrame."""
    try:
        sColumn = dictAccess.get("column", dfData.columns[0])
        sAggregate = dictAccess.get("sAggregate")
        if sAggregate:
            daValues = dfData[sColumn].astype(float).to_numpy()
            return _fApplyAggregate(daValues, sAggregate)
        listIndices = dictAccess.get("listIndices", [-1])
        iRow = listIndices[0] if listIndices else -1
        return float(dfData[sColumn].iloc[iRow])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Failed to access dataframe column in {sFullPath}: {exc}",
        ) from exc


def _fNavigateJsonValue(dictData, dictAccess):
    """Traverse a parsed JSON structure and return a scalar."""
    sKey = dictAccess.get("key", "")
    listKeys = sKey.split(".") if sKey else []
    value = dictData
    for sSubKey in listKeys:
        if isinstance(value, list):
            value = value[int(sSubKey)]
        else:
            value = value[sSubKey]
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate and isinstance(value, list):
        daArray = np.array(value, dtype=float)
        return _fApplyAggregate(daArray, sAggregate)
    listIndices = dictAccess.get("listIndices", None)
    if listIndices is not None:
        for iIdx in listIndices:
            value = value[iIdx]
    return float(value)


def _flistFilterDataLines(listLines):
    """Strip blank and comment lines from raw file lines."""
    return [
        s for s in listLines
        if s.strip() and not s.strip().startswith("#")
    ]


def _fbIsNumericToken(sToken):
    """Return True if sToken can be parsed as a float."""
    try:
        float(sToken)
        return True
    except ValueError:
        return False


def _ftSplitHeaderAndData(listDataLines):
    """Detect if first line is header or data."""
    if not listDataLines:
        return ("", [])
    listTokens = listDataLines[0].split()
    bAllNumeric = all(_fbIsNumericToken(s) for s in listTokens)
    if bAllNumeric:
        return ("", listDataLines)
    return (listDataLines[0], listDataLines[1:])


def _fLoadTabularWithComments(
    sFullPath, dictAccess, sCommentPrefix="##", sHeaderPrefix="#",
):
    """Load a value from a tab-delimited file with comment/header lines."""
    listHeaders = []
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if sLine.startswith(sCommentPrefix):
                continue
            if sLine.startswith(sHeaderPrefix) and not listHeaders:
                listHeaders = (
                    sLine.lstrip(sHeaderPrefix).strip().split("\t")
                )
                continue
            listRows.append(sLine.strip().split("\t"))
    return _fExtractTabularValue(listHeaders, listRows, dictAccess)


def _fLoadNumpyValue(sFullPath, dictAccess):
    """Load a value from a numpy file."""
    try:
        daData = np.load(sFullPath, allow_pickle=False)
    except (ValueError, OSError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as npy: {exc}",
        ) from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadNpzValue(sFullPath, dictAccess):
    """Load a value from a numpy .npz archive."""
    try:
        archiveNpz = np.load(sFullPath, allow_pickle=False)
    except (ValueError, OSError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as npz: {exc}",
        ) from exc
    sKey = dictAccess.get("key", list(archiveNpz.files)[0])
    daData = archiveNpz[sKey]
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadKeyvalueValue(sFullPath, dictAccess):
    """Load a value from a key = value text file."""
    sTargetKey = dictAccess.get("key", "")
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            sStripped = sLine.strip()
            if not sStripped or sStripped.startswith("#"):
                continue
            if "=" not in sStripped:
                continue
            sKey, sVal = sStripped.split("=", 1)
            if sKey.strip() == sTargetKey:
                return float(sVal.strip())
    raise KeyError(f"Key {sTargetKey!r} not found in {sFullPath}")


def _fLoadJsonValue(sFullPath, dictAccess):
    """Load a value from a JSON file."""
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fh:
            dictData = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as json: {exc}",
        ) from exc
    try:
        return _fNavigateJsonValue(dictData, dictAccess)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"Failed to access json path in {sFullPath}: {exc}",
        ) from exc


def _fLoadCsvValue(sFullPath, dictAccess):
    """Load a value from a CSV file."""
    import csv
    sColumn = dictAccess.get("column", "")
    try:
        with open(
            sFullPath, newline="", encoding="utf-8", errors="replace",
        ) as fileHandle:
            reader = csv.DictReader(fileHandle)
            listRows = list(reader)
    except csv.Error as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as csv: {exc}",
        ) from exc
    try:
        sAggregate = dictAccess.get("sAggregate")
        if sAggregate and sColumn:
            daValues = np.array([float(r[sColumn]) for r in listRows])
            return _fApplyAggregate(daValues, sAggregate)
        listIndices = dictAccess.get("listIndices", [-1])
        iIndex = listIndices[0] if listIndices else -1
        return float(listRows[iIndex][sColumn])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"Failed to access csv column in {sFullPath}: {exc}",
        ) from exc


def _fLoadHdf5Value(sFullPath, dictAccess):
    """Load a value from an HDF5 file."""
    import h5py
    sDataset = dictAccess.get("dataset", "")
    try:
        with h5py.File(sFullPath, "r") as fileHdf5:
            daData = np.array(fileHdf5[sDataset])
    except (OSError, KeyError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as hdf5: {exc}",
        ) from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadWhitespaceValue(sFullPath, dictAccess):
    """Load a value from a whitespace-delimited text file."""
    sColumn = dictAccess.get("column", "")
    listIndices = dictAccess.get("listIndices", [-1])
    iIndex = listIndices[0] if listIndices else -1
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fh:
            listRawLines = fh.readlines()
    except OSError as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as whitespace: {exc}",
        ) from exc
    listDataLines = _flistFilterDataLines(listRawLines)
    sHeader, listDataRows = _ftSplitHeaderAndData(listDataLines)
    listColumns = sHeader.split() if sHeader else []
    listRows = [sRow.split() for sRow in listDataRows]
    try:
        if sColumn and listColumns:
            iColumn = listColumns.index(sColumn)
        else:
            iColumn = listIndices[1] if len(listIndices) > 1 else 0
    except ValueError as exc:
        raise ValueError(
            f"Column {sColumn!r} not found in {sFullPath}",
        ) from exc
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate:
        daValues = np.array([float(r[iColumn]) for r in listRows])
        return _fApplyAggregate(daValues, sAggregate)
    return float(listRows[iIndex][iColumn])


def _fLoadJsonlValue(sFullPath, dictAccess):
    """Load a value from a JSON Lines file."""
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fh:
            listRecords = [
                json.loads(sLine) for sLine in fh if sLine.strip()
            ]
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as jsonl: {exc}",
        ) from exc
    sKey = dictAccess.get("key", "")
    sAggregate = dictAccess.get("sAggregate")
    try:
        if sAggregate and sKey:
            daValues = np.array([float(r[sKey]) for r in listRecords])
            return _fApplyAggregate(daValues, sAggregate)
        listIndices = dictAccess.get("listIndices", [0])
        iRow = listIndices[0] if listIndices else 0
        if sKey:
            return float(listRecords[iRow][sKey])
        return float(listRecords[iRow])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(
            f"Failed to access jsonl data in {sFullPath}: {exc}",
        ) from exc


def _fLoadExcelValue(sFullPath, dictAccess):
    """Load a value from an Excel file."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl is required to load Excel files")
    try:
        workbook = openpyxl.load_workbook(sFullPath, read_only=True)
        sheet = workbook.active
        listRows = list(sheet.iter_rows(values_only=True))
        workbook.close()
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as excel: {exc}",
        ) from exc
    try:
        listHeaders = [
            str(c) if c else f"col{i}"
            for i, c in enumerate(listRows[0])
        ]
        sColumn = dictAccess.get("column", listHeaders[0])
        iCol = listHeaders.index(sColumn)
        sAggregate = dictAccess.get("sAggregate")
        if sAggregate:
            daValues = np.array([float(r[iCol]) for r in listRows[1:]])
            return _fApplyAggregate(daValues, sAggregate)
        listIndices = dictAccess.get("listIndices", [-1])
        iRow = listIndices[0] if listIndices else -1
        return float(listRows[1:][iRow][iCol])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Failed to access excel data in {sFullPath}: {exc}",
        ) from exc


def _fLoadFitsValue(sFullPath, dictAccess):
    """Load a value from a FITS file."""
    try:
        from astropy.io import fits as fitsLib
    except ImportError:
        raise ImportError("astropy is required to load FITS files")
    iHdu = dictAccess.get("iHdu", 0)
    sAggregate = dictAccess.get("sAggregate")
    sColumn = dictAccess.get("column", "")
    listIndices = dictAccess.get("listIndices", [0])
    try:
        with fitsLib.open(sFullPath) as hduList:
            hdu = hduList[iHdu]
            if hdu.data is None:
                raise ValueError(f"HDU {iHdu} has no data")
            if sColumn and hasattr(hdu, "columns"):
                daData = np.array(hdu.data[sColumn], dtype=float)
            else:
                daData = np.array(hdu.data, dtype=float).flatten()
    except (OSError, KeyError, TypeError, IndexError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as fits: {exc}",
        ) from exc
    if sAggregate:
        return _fApplyAggregate(daData, sAggregate)
    iDataIdx = listIndices[1] if len(listIndices) > 1 else 0
    return float(daData[iDataIdx])


def _fLoadMatlabValue(sFullPath, dictAccess):
    """Load a value from a MATLAB .mat file."""
    try:
        from scipy.io import loadmat
    except ImportError:
        raise ImportError("scipy is required to load MATLAB files")
    try:
        dictMat = loadmat(sFullPath)
    except (NotImplementedError, OSError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as matlab: {exc}",
        ) from exc
    sKey = dictAccess.get("key", "")
    if not sKey:
        listKeys = [k for k in dictMat if not k.startswith("__")]
        sKey = listKeys[0]
    try:
        daData = np.array(dictMat[sKey], dtype=float).flatten()
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Failed to access matlab variable in {sFullPath}: {exc}",
        ) from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadParquetValue(sFullPath, dictAccess):
    """Load a value from a Parquet file."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ImportError("pyarrow is required to load Parquet files")
    try:
        table = pq.read_table(sFullPath)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as parquet: {exc}",
        ) from exc
    try:
        sColumn = dictAccess.get("column", table.column_names[0])
        daValues = table.column(sColumn).to_numpy().astype(float)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Failed to access parquet column in {sFullPath}: {exc}",
        ) from exc
    return _fExtractArrayValue(daValues, dictAccess)


def _fLoadImageValue(sFullPath, dictAccess):
    """Load a value from an image file."""
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow is required to load image files")
    try:
        daPixels = np.array(Image.open(sFullPath), dtype=float)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as image: {exc}",
        ) from exc
    return _fExtractArrayValue(daPixels.flatten(), dictAccess)


def _fLoadFastaValue(sFullPath, dictAccess):
    """Load a value from a FASTA file (sequence length)."""
    listLengths = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        iCurrentLength = 0
        for sLine in fh:
            if sLine.startswith(">"):
                if iCurrentLength > 0:
                    listLengths.append(iCurrentLength)
                iCurrentLength = 0
            else:
                iCurrentLength += len(sLine.strip())
        if iCurrentLength > 0:
            listLengths.append(iCurrentLength)
    daLengths = np.array(listLengths, dtype=float)
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate:
        return _fApplyAggregate(daLengths, sAggregate)
    listIndices = dictAccess.get("listIndices", [0])
    return float(daLengths[listIndices[0]])


def _fLoadFastqValue(sFullPath, dictAccess):
    """Load a value from a FASTQ file (sequence length or quality)."""
    listLengths = []
    listQualities = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listLines = fh.readlines()
    for i in range(0, len(listLines) - 3, 4):
        sSeq = listLines[i + 1].strip()
        sQual = listLines[i + 3].strip()
        listLengths.append(len(sSeq))
        listQualities.append(np.mean([ord(c) - 33 for c in sQual]))
    sKey = dictAccess.get("key", "length")
    daValues = np.array(
        listLengths if sKey == "length" else listQualities, dtype=float,
    )
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate:
        return _fApplyAggregate(daValues, sAggregate)
    listIndices = dictAccess.get("listIndices", [0])
    return float(daValues[listIndices[0]])


def _fLoadVcfValue(sFullPath, dictAccess):
    """Load a value from a VCF file."""
    return _fLoadTabularWithComments(
        sFullPath, dictAccess, sCommentPrefix="##", sHeaderPrefix="#",
    )


def _fLoadBedValue(sFullPath, dictAccess):
    """Load a value from a BED file."""
    listHeaders = [
        "chrom", "chromStart", "chromEnd", "name", "score", "strand",
    ]
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if sLine.strip() and not sLine.startswith("#"):
                listRows.append(sLine.strip().split("\t"))
    return _fExtractTabularValue(listHeaders, listRows, dictAccess)


def _fLoadGffValue(sFullPath, dictAccess):
    """Load a value from a GFF/GTF file."""
    listHeaders = [
        "seqid", "source", "type", "start", "end",
        "score", "strand", "phase", "attributes",
    ]
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if sLine.strip() and not sLine.startswith("#"):
                listRows.append(sLine.strip().split("\t"))
    return _fExtractTabularValue(listHeaders, listRows, dictAccess)


def _fLoadSamValue(sFullPath, dictAccess):
    """Load a value from a SAM file."""
    listHeaders = [
        "QNAME", "FLAG", "RNAME", "POS", "MAPQ", "CIGAR",
        "RNEXT", "PNEXT", "TLEN", "SEQ", "QUAL",
    ]
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if not sLine.startswith("@") and sLine.strip():
                listRows.append(sLine.strip().split("\t"))
    return _fExtractTabularValue(listHeaders, listRows, dictAccess)


def _fLoadSyslogValue(sFullPath, dictAccess):
    """Load a value from a syslog file (line count)."""
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listLines = [s for s in fh if s.strip()]
    listIndices = dictAccess.get("listIndices", [0])
    return float(
        len(listLines) if listIndices == [0] else listIndices[0],
    )


def _fLoadCefValue(sFullPath, dictAccess):
    """Load a value from a CEF file (record count)."""
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listRecords = [s for s in fh if s.strip().startswith("CEF:")]
    listIndices = dictAccess.get("listIndices", [0])
    return float(
        len(listRecords) if listIndices == [0] else listIndices[0],
    )


def _fLoadFixedwidthValue(sFullPath, dictAccess):
    """Load a value from a fixed-width text file."""
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listRawLines = fh.readlines()
    listDataLines = [s for s in listRawLines if s.strip()]
    if not listDataLines:
        raise ValueError("Empty fixed-width file")
    sColumn = dictAccess.get("column", "")
    listIndices = dictAccess.get("listIndices", [-1])
    iRow = listIndices[0] if listIndices else -1
    listTokens = listDataLines[iRow].split()
    iCol = int(sColumn) if sColumn.isdigit() else 0
    return float(listTokens[iCol])


def _fLoadMultitableValue(sFullPath, dictAccess):
    """Load a value from a multi-table text file."""
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        sContent = fh.read()
    listSections = re.split(r"\n\s*\n|\n[=\-]{3,}\n", sContent)
    listSections = [s.strip() for s in listSections if s.strip()]
    iSection = dictAccess.get("iSection", 0)
    sSection = listSections[iSection]
    listLines = sSection.strip().splitlines()
    listFiltered = [s for s in listLines if s.strip()]
    sHeader, listDataRows = _ftSplitHeaderAndData(listFiltered)
    listColumns = sHeader.split() if sHeader else []
    sColumn = dictAccess.get("column", "")
    iCol = (
        listColumns.index(sColumn) if sColumn and listColumns else 0
    )
    sAggregate = dictAccess.get("sAggregate")
    listParsedRows = [r.split() for r in listDataRows]
    if sAggregate:
        daValues = np.array([float(r[iCol]) for r in listParsedRows])
        return _fApplyAggregate(daValues, sAggregate)
    listIndices = dictAccess.get("listIndices", [-1])
    iRow = listIndices[0] if listIndices else -1
    return float(listParsedRows[iRow][iCol])


def _fLoadBamValue(sFullPath, dictAccess):
    """Load a value from a BAM file."""
    try:
        import pysam
    except ImportError:
        raise ImportError("pysam is required to load BAM files")
    try:
        samfile = pysam.AlignmentFile(sFullPath, "rb")
    except (ValueError, OSError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as bam: {exc}",
        ) from exc
    listValues = []
    sKey = dictAccess.get("key", "mapq")
    for read in samfile.fetch(until_eof=True):
        if sKey == "mapq":
            listValues.append(float(read.mapping_quality))
        elif sKey == "tlen":
            listValues.append(float(read.template_length))
    samfile.close()
    daValues = np.array(listValues, dtype=float)
    return _fExtractArrayValue(daValues, dictAccess)


def _fLoadFortranValue(sFullPath, dictAccess):
    """Load a value from a FORTRAN binary file."""
    try:
        from scipy.io import FortranFile
    except ImportError:
        raise ImportError(
            "scipy is required to load FORTRAN binary files",
        )
    try:
        fortranFile = FortranFile(sFullPath, "r")
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as fortran: {exc}",
        ) from exc
    listRecords = []
    try:
        while True:
            listRecords.append(fortranFile.read_reals())
    except Exception:
        pass
    fortranFile.close()
    if not listRecords:
        raise ValueError(f"No records found in {sFullPath}")
    sKey = dictAccess.get("key", "")
    iRecord = int(sKey) if sKey.isdigit() else 0
    daData = listRecords[iRecord]
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadSpssValue(sFullPath, dictAccess):
    """Load a value from an SPSS .sav file."""
    try:
        import pyreadstat
    except ImportError:
        raise ImportError("pyreadstat is required to load SPSS files")
    try:
        dfData, _ = pyreadstat.read_sav(sFullPath)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as spss: {exc}",
        ) from exc
    return _fExtractDataframeValue(dfData, dictAccess, sFullPath)


def _fLoadStataValue(sFullPath, dictAccess):
    """Load a value from a Stata .dta file."""
    try:
        import pyreadstat
    except ImportError:
        raise ImportError("pyreadstat is required to load Stata files")
    try:
        dfData, _ = pyreadstat.read_dta(sFullPath)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as stata: {exc}",
        ) from exc
    return _fExtractDataframeValue(dfData, dictAccess, sFullPath)


def _fLoadSasValue(sFullPath, dictAccess):
    """Load a value from a SAS .sas7bdat file."""
    try:
        import pyreadstat
    except ImportError:
        raise ImportError("pyreadstat is required to load SAS files")
    try:
        dfData, _ = pyreadstat.read_sas7bdat(sFullPath)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as sas: {exc}",
        ) from exc
    return _fExtractDataframeValue(dfData, dictAccess, sFullPath)


def _fLoadRdataValue(sFullPath, dictAccess):
    """Load a value from an R data file."""
    try:
        import pyreadr
    except ImportError:
        raise ImportError("pyreadr is required to load R data files")
    try:
        dictFrames = pyreadr.read_r(sFullPath)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as rdata: {exc}",
        ) from exc
    sKey = dictAccess.get("key", list(dictFrames.keys())[0])
    dfData = dictFrames[sKey]
    return _fExtractDataframeValue(dfData, dictAccess, sFullPath)


def _fLoadVotableValue(sFullPath, dictAccess):
    """Load a value from a VOTable file."""
    try:
        from astropy.io.votable import parse as votableParse
    except ImportError:
        raise ImportError("astropy is required to load VOTable files")
    try:
        votable = votableParse(sFullPath)
        table = votable.get_first_table().to_table()
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as votable: {exc}",
        ) from exc
    try:
        sColumn = dictAccess.get("column", table.colnames[0])
        daValues = np.array(table[sColumn], dtype=float)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Failed to access votable column in {sFullPath}: {exc}",
        ) from exc
    return _fExtractArrayValue(daValues, dictAccess)


def _fLoadIpacValue(sFullPath, dictAccess):
    """Load a value from an IPAC table file."""
    try:
        from astropy.io import ascii as astropyAscii
    except ImportError:
        raise ImportError(
            "astropy is required to load IPAC table files",
        )
    try:
        table = astropyAscii.read(sFullPath, format="ipac")
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as ipac: {exc}",
        ) from exc
    try:
        sColumn = dictAccess.get("column", table.colnames[0])
        daValues = np.array(table[sColumn], dtype=float)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Failed to access ipac column in {sFullPath}: {exc}",
        ) from exc
    return _fExtractArrayValue(daValues, dictAccess)


def _fLoadPcapValue(sFullPath, dictAccess):
    """Load a value from a PCAP file (packet count or length)."""
    try:
        from scapy.all import rdpcap
    except ImportError:
        raise ImportError("scapy is required to load PCAP files")
    try:
        listPackets = rdpcap(sFullPath)
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as pcap: {exc}",
        ) from exc
    listLengths = [float(len(p)) for p in listPackets]
    daValues = np.array(listLengths, dtype=float)
    return _fExtractArrayValue(daValues, dictAccess)


def _fLoadVtkValue(sFullPath, dictAccess):
    """Load a value from a VTK file."""
    try:
        import pyvista
    except ImportError:
        raise ImportError("pyvista is required to load VTK files")
    try:
        mesh = pyvista.read(sFullPath)
    except (FileNotFoundError, Exception) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as vtk: {exc}",
        ) from exc
    sKey = dictAccess.get("key", "")
    if not sKey and mesh.array_names:
        sKey = mesh.array_names[0]
    try:
        daData = np.array(mesh[sKey], dtype=float).flatten()
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Failed to access vtk array in {sFullPath}: {exc}",
        ) from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadCgnsValue(sFullPath, dictAccess):
    """Load a value from a CGNS file (HDF5 under the hood)."""
    import h5py
    sDataset = dictAccess.get("dataset", "")
    try:
        with h5py.File(sFullPath, "r") as fileHdf5:
            daData = np.array(fileHdf5[sDataset])
    except (OSError, KeyError) as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as cgns: {exc}",
        ) from exc
    return _fExtractArrayValue(daData.flatten(), dictAccess)


def _fLoadSafetensorsValue(sFullPath, dictAccess):
    """Load a value from a safetensors file."""
    try:
        from safetensors import safe_open
    except ImportError:
        raise ImportError(
            "safetensors is required to load safetensors files",
        )
    sKey = dictAccess.get("key", "")
    try:
        with safe_open(sFullPath, framework="numpy") as fh:
            if not sKey:
                sKey = list(fh.keys())[0]
            daData = fh.get_tensor(sKey).astype(float).flatten()
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as safetensors: {exc}",
        ) from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadTfrecordValue(sFullPath, dictAccess):
    """Load a value from a TFRecord file."""
    try:
        from tfrecord.reader import tfrecord_iterator
    except ImportError:
        raise ImportError(
            "tfrecord is required to load TFRecord files",
        )
    try:
        listRecords = list(tfrecord_iterator(sFullPath))
    except Exception as exc:
        raise ValueError(
            f"Failed to load {sFullPath} as tfrecord: {exc}",
        ) from exc
    sKey = dictAccess.get("key", "")
    try:
        if sKey and listRecords:
            daValues = np.array(
                [float(r[sKey]) for r in listRecords], dtype=float,
            )
        else:
            daValues = np.array([float(len(r)) for r in listRecords])
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"Failed to access tfrecord key in {sFullPath}: {exc}",
        ) from exc
    return _fExtractArrayValue(daValues, dictAccess)


_DICT_LOADERS = {
    "npy": _fLoadNumpyValue,
    "npz": _fLoadNpzValue,
    "json": _fLoadJsonValue,
    "csv": _fLoadCsvValue,
    "hdf5": _fLoadHdf5Value,
    "whitespace": _fLoadWhitespaceValue,
    "keyvalue": _fLoadKeyvalueValue,
    "jsonl": _fLoadJsonlValue,
    "excel": _fLoadExcelValue,
    "fits": _fLoadFitsValue,
    "matlab": _fLoadMatlabValue,
    "parquet": _fLoadParquetValue,
    "image": _fLoadImageValue,
    "fasta": _fLoadFastaValue,
    "fastq": _fLoadFastqValue,
    "vcf": _fLoadVcfValue,
    "bed": _fLoadBedValue,
    "gff": _fLoadGffValue,
    "sam": _fLoadSamValue,
    "syslog": _fLoadSyslogValue,
    "cef": _fLoadCefValue,
    "fixedwidth": _fLoadFixedwidthValue,
    "multitable": _fLoadMultitableValue,
    "bam": _fLoadBamValue,
    "fortran": _fLoadFortranValue,
    "spss": _fLoadSpssValue,
    "stata": _fLoadStataValue,
    "sas": _fLoadSasValue,
    "rdata": _fLoadRdataValue,
    "votable": _fLoadVotableValue,
    "ipac": _fLoadIpacValue,
    "pcap": _fLoadPcapValue,
    "vtk": _fLoadVtkValue,
    "cgns": _fLoadCgnsValue,
    "safetensors": _fLoadSafetensorsValue,
    "tfrecord": _fLoadTfrecordValue,
}


def _fLoadValue(sDataFile, sAccessPath, sStepDirectory, sFormat=""):
    """Load a single value from a data file using the access path."""
    sFullPath = str(pathlib.Path(sStepDirectory) / sDataFile)
    dictAccess = _fdictParseAccessPath(sAccessPath)
    if not sFormat:
        sFormat = _fsInferFormat(sFullPath)
    if sFormat is None:
        sFormat = "whitespace"
    fLoader = _DICT_LOADERS.get(sFormat)
    if fLoader is None:
        raise ValueError(f"Unsupported format: {sFormat}")
    return fLoader(sFullPath, dictAccess)

# -- end loader source -----------------------------------------------------


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

DICT_FORMAT_MAP = _DICT_FORMAT_MAP
DICT_LOADERS = _DICT_LOADERS


def fLoadValue(sDataFile, sAccessPath, sStepDirectory, sFormat=""):
    """Load a single scalar value from any supported data file.

    Parameters
    ----------
    sDataFile : str
        Filename (relative to sStepDirectory).
    sAccessPath : str
        Access path descriptor (e.g. "column:Temp,index:-1").
    sStepDirectory : str
        Directory containing the data file.
    sFormat : str, optional
        Explicit format override; inferred from extension when empty.

    Returns
    -------
    float
        The extracted scalar value.
    """
    return _fLoadValue(sDataFile, sAccessPath, sStepDirectory, sFormat)


def fsReadLoaderSource():
    """Return the embeddable loader source between the markers.

    testGenerator.py calls this to build the self-contained quantitative
    test template without duplicating the loader code.
    """
    sSourcePath = str(pathlib.Path(__file__))
    with open(sSourcePath, encoding="utf-8") as fh:
        sFullSource = fh.read()
    sBeginMarker = "# -- begin loader source"
    sEndMarker = "# -- end loader source"
    iStart = sFullSource.index(sBeginMarker)
    iStartLine = sFullSource.index("\n", iStart) + 1
    iEnd = sFullSource.index(sEndMarker)
    return sFullSource[iStartLine:iEnd]
