"""Scan source code for file-loading calls across multiple languages."""

import os
import re

from .commandUtilities import DICT_EXTENSION_TO_LANGUAGE


DICT_SHEBANG_KEYWORDS = {
    "python": "python",
    "Rscript": "r",
    "julia": "julia",
    "perl": "perl",
    "bash": "shell",
    "sh": "shell",
    "node": "javascript",
}

SET_FILE_EXTENSIONS = {
    ".csv", ".dat", ".txt", ".tsv", ".json", ".hdf5", ".h5",
    ".fits", ".mat", ".npy", ".npz", ".pkl", ".parquet",
    ".xlsx", ".xls", ".sav", ".sas7bdat", ".dta",
    ".nc", ".xml", ".yaml", ".yml", ".ini", ".cfg",
    ".log", ".out", ".in", ".png", ".jpg", ".pdf",
    ".svg", ".bmp", ".tif", ".tiff", ".gif",
}

DICT_COMMENT_PREFIX = {
    "python": "#",
    "r": "#",
    "shell": "#",
    "julia": "#",
    "perl": "#",
    "c": "//",
    "rust": "//",
    "javascript": "//",
    "go": "//",
    "fortran": "!",
    "matlab": "%",
}


def fsDetectLanguage(sFilePath, sCommandPrefix="", sFirstLine=""):
    """Detect programming language from extension, shebang, or command."""
    sExtension = os.path.splitext(sFilePath)[1]
    if sExtension in DICT_EXTENSION_TO_LANGUAGE:
        return DICT_EXTENSION_TO_LANGUAGE[sExtension]
    if sFirstLine.startswith("#!"):
        for sKeyword, sLanguage in DICT_SHEBANG_KEYWORDS.items():
            if sKeyword in sFirstLine:
                return sLanguage
    if sCommandPrefix:
        sWord = sCommandPrefix.strip().split()[0] if sCommandPrefix.strip() else ""
        from .commandUtilities import DICT_COMMAND_PREFIXES
        if sWord in DICT_COMMAND_PREFIXES:
            return DICT_COMMAND_PREFIXES[sWord]
    return "unknown"


def fsExtractStringLiteral(sMatch):
    """Strip surrounding single or double quotes from a matched string."""
    if not sMatch:
        return sMatch
    if (sMatch.startswith('"') and sMatch.endswith('"')) or \
       (sMatch.startswith("'") and sMatch.endswith("'")):
        return sMatch[1:-1]
    return sMatch


def fbLooksLikeFilePath(sCandidate):
    """Return True when sCandidate resembles a file path, not a module or URL."""
    if not sCandidate or not sCandidate.strip():
        return False
    sCandidate = sCandidate.strip()
    if sCandidate.startswith("http://") or sCandidate.startswith("https://"):
        return False
    if sCandidate.startswith("ftp://"):
        return False
    if "/" in sCandidate:
        return True
    if "{" in sCandidate:
        return True
    sDotPart = os.path.splitext(sCandidate)[1].lower()
    if sDotPart in SET_FILE_EXTENSIONS:
        return True
    if "." in sCandidate and len(sDotPart) <= 6 and len(sDotPart) >= 2:
        return True
    return False


def fbLooksLikeDataFile(sCandidate):
    """Return True only when sCandidate has a known data extension or a path separator."""
    if not sCandidate or not sCandidate.strip():
        return False
    sCandidate = sCandidate.strip()
    if sCandidate.startswith("http://") or sCandidate.startswith("https://"):
        return False
    if sCandidate.startswith("ftp://"):
        return False
    sExtension = os.path.splitext(sCandidate)[1].lower()
    if sExtension in SET_FILE_EXTENSIONS:
        return True
    if "/" in sCandidate:
        return True
    return False


def _fbIsCommentLine(sStripped, sCommentPrefix):
    """Return True when a stripped line begins with the comment prefix."""
    if not sCommentPrefix:
        return False
    return sStripped.startswith(sCommentPrefix)


def _flistMatchPatterns(sSourceCode, listPatterns, sCommentPrefix="#"):
    """Apply regex patterns line-by-line and return list of match dicts."""
    listResults = []
    for iLineNumber, sLine in enumerate(sSourceCode.splitlines(), start=1):
        sStripped = sLine.strip()
        if not sStripped or _fbIsCommentLine(sStripped, sCommentPrefix):
            continue
        for tPattern in listPatterns:
            sRegex, sLabel = tPattern[0], tPattern[1]
            resultMatch = re.search(sRegex, sLine)
            if resultMatch:
                try:
                    sRawFileName = resultMatch.group("sFileName")
                except IndexError:
                    continue
                sFileName = fsExtractStringLiteral(sRawFileName)
                if sFileName and fbLooksLikeFilePath(sFileName):
                    listResults.append({
                        "sFileName": sFileName,
                        "sLoadFunction": sLabel,
                        "iLineNumber": iLineNumber,
                    })
    return listResults


def _flistScanPython(sSourceCode):
    """Scan Python source for file-loading calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'(?:np\.load|numpy\.load)\s*\(\s*' + sQuoted, "np.load"),
        (r'(?:np\.loadtxt|numpy\.loadtxt)\s*\(\s*' + sQuoted, "np.loadtxt"),
        (r'(?:np\.genfromtxt|numpy\.genfromtxt)\s*\(\s*' + sQuoted, "np.genfromtxt"),
        (r'(?:pd|pandas)\.read_csv\s*\(\s*' + sQuoted, "pd.read_csv"),
        (r'(?:pd|pandas)\.read_excel\s*\(\s*' + sQuoted, "pd.read_excel"),
        (r'(?:pd|pandas)\.read_parquet\s*\(\s*' + sQuoted, "pd.read_parquet"),
        (r'(?:pd|pandas)\.read_json\s*\(\s*' + sQuoted, "pd.read_json"),
        (r'(?:pd|pandas)\.read_hdf\s*\(\s*' + sQuoted, "pd.read_hdf"),
        (r'(?:pd|pandas)\.read_stata\s*\(\s*' + sQuoted, "pd.read_stata"),
        (r'(?:pd|pandas)\.read_spss\s*\(\s*' + sQuoted, "pd.read_spss"),
        (r'(?:pd|pandas)\.read_sas\s*\(\s*' + sQuoted, "pd.read_sas"),
        (r'h5py\.File\s*\(\s*' + sQuoted, "h5py.File"),
        (r'(?<![.\w])fits\.open\s*\(\s*' + sQuoted, "fits.open"),
        (r'loadmat\s*\(\s*' + sQuoted, "loadmat"),
        (r'Image\.open\s*\(\s*' + sQuoted, "Image.open"),
        (r'(?:csv\.reader|csv\.DictReader)\s*\(\s*open\s*\(\s*' + sQuoted, "csv.reader(open)"),
        (r'json\.load\s*\(\s*open\s*\(\s*' + sQuoted, "json.load(open)"),
        (r'os\.path\.join\s*\(\s*' + sQuoted, "os.path.join"),
        (r'(?<![.\w])open\s*\(\s*' + sQuoted, "open"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "#")


def _flistScanR(sSourceCode):
    """Scan R source for file-loading calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'read\.csv\s*\(\s*' + sQuoted, "read.csv"),
        (r'read\.table\s*\(\s*' + sQuoted, "read.table"),
        (r'read\.delim\s*\(\s*' + sQuoted, "read.delim"),
        (r'readRDS\s*\(\s*' + sQuoted, "readRDS"),
        (r'(?<!\w)load\s*\(\s*' + sQuoted, "load"),
        (r'readLines\s*\(\s*' + sQuoted, "readLines"),
        (r'scan\s*\(\s*' + sQuoted, "scan"),
        (r'read_csv\s*\(\s*' + sQuoted, "read_csv"),
        (r'read_excel\s*\(\s*' + sQuoted, "read_excel"),
        (r'fread\s*\(\s*' + sQuoted, "fread"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "#")


def _flistScanC(sSourceCode):
    """Scan C/C++ source for file-loading calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'fopen\s*\(\s*' + sQuoted, "fopen"),
        (r'ifstream\s+\w+\s*\(\s*' + sQuoted, "ifstream"),
        (r'H5Fopen\s*\(\s*' + sQuoted, "H5Fopen"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "//")


def _flistScanFortran(sSourceCode):
    """Scan Fortran source for OPEN(FILE=...) calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'(?i)OPEN\s*\([^)]*FILE\s*=\s*' + sQuoted, "OPEN(FILE=)"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "!")


def _flistScanRust(sSourceCode):
    """Scan Rust source for file-opening calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'File::open\s*\(\s*' + sQuoted, "File::open"),
        (r'fs::read\s*\(\s*' + sQuoted, "fs::read"),
        (r'fs::read_to_string\s*\(\s*' + sQuoted, "fs::read_to_string"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "//")


def _flistScanJavaScript(sSourceCode):
    """Scan JavaScript/TypeScript source for file-loading calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'fs\.readFileSync\s*\(\s*' + sQuoted, "fs.readFileSync"),
        (r'fs\.readFile\s*\(\s*' + sQuoted, "fs.readFile"),
        (r'require\s*\(\s*' + sQuoted, "require"),
    ]
    listRawResults = _flistMatchPatterns(sSourceCode, listPatterns, "//")
    return _flistFilterRequireResults(listRawResults)


def _flistFilterRequireResults(listRawResults):
    """Remove require() matches that look like module imports, not files."""
    listFiltered = []
    for dictResult in listRawResults:
        if dictResult["sLoadFunction"] != "require":
            listFiltered.append(dictResult)
            continue
        sFileName = dictResult["sFileName"]
        if _fbLooksLikeRequiredFile(sFileName):
            listFiltered.append(dictResult)
    return listFiltered


def _fbLooksLikeRequiredFile(sFileName):
    """Return True when a require() argument looks like a file, not a module."""
    if sFileName.startswith("./") or sFileName.startswith("../"):
        return True
    sDotPart = os.path.splitext(sFileName)[1].lower()
    if sDotPart in SET_FILE_EXTENSIONS:
        return True
    return False


def _flistScanPerl(sSourceCode):
    """Scan Perl source for file-opening calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'(?<!\w)open\s*\(\s*\w+\s*,\s*' + sQuoted, "open"),
        (r'IO::File->new\s*\(\s*' + sQuoted, "IO::File->new"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "#")


def _flistScanShell(sSourceCode):
    """Scan shell scripts for file-reading patterns."""
    sUnquotedOrQuoted = r"""(?P<sFileName>["'][^"']+["']|\S+)"""
    listPatterns = [
        (r'(?<!\w)cat\s+' + sUnquotedOrQuoted, "cat"),
        (r'(?<!\w)source\s+' + sUnquotedOrQuoted, "source"),
        (r'(?<!<)<(?!<)\s*' + sUnquotedOrQuoted, "<"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "#")


def _flistScanJulia(sSourceCode):
    """Scan Julia source for file-loading calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'CSV\.read\s*\(\s*' + sQuoted, "CSV.read"),
        (r'(?<![.\w])open\s*\(\s*' + sQuoted, "open"),
        (r'(?<![.\w])read\s*\(\s*' + sQuoted, "read"),
        (r'(?<![.\w])load\s*\(\s*' + sQuoted, "load"),
        (r'(?<![.\w])readlines\s*\(\s*' + sQuoted, "readlines"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "#")


def _flistScanMatlab(sSourceCode):
    """Scan MATLAB source for file-loading calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'(?<!\w)load\s*\(\s*' + sQuoted, "load"),
        (r'fopen\s*\(\s*' + sQuoted, "fopen"),
        (r'readtable\s*\(\s*' + sQuoted, "readtable"),
        (r'csvread\s*\(\s*' + sQuoted, "csvread"),
        (r'dlmread\s*\(\s*' + sQuoted, "dlmread"),
        (r'importdata\s*\(\s*' + sQuoted, "importdata"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "%")


def _flistScanGo(sSourceCode):
    """Scan Go source for file-opening calls."""
    sQuoted = r"""(?P<sFileName>["'][^"']+["'])"""
    listPatterns = [
        (r'os\.Open\s*\(\s*' + sQuoted, "os.Open"),
        (r'os\.ReadFile\s*\(\s*' + sQuoted, "os.ReadFile"),
        (r'ioutil\.ReadFile\s*\(\s*' + sQuoted, "ioutil.ReadFile"),
        (r'os\.OpenFile\s*\(\s*' + sQuoted, "os.OpenFile"),
    ]
    return _flistMatchPatterns(sSourceCode, listPatterns, "//")


def _flistScanConfigFile(sSourceCode):
    """Extract file-path-like string values from JSON or YAML config."""
    listResults = []
    sQuotedPattern = r'["\']((?:[^"\'\\]|\\.)+)["\']'
    for iLineNumber, sLine in enumerate(sSourceCode.splitlines(), 1):
        for matchResult in re.finditer(sQuotedPattern, sLine):
            sValue = matchResult.group(1)
            if fbLooksLikeFilePath(sValue):
                listResults.append({
                    "sFileName": sValue,
                    "sLoadFunction": "config-reference",
                    "iLineNumber": iLineNumber,
                })
    return listResults


DICT_LANGUAGE_SCANNERS = {
    "python": _flistScanPython,
    "r": _flistScanR,
    "c": _flistScanC,
    "fortran": _flistScanFortran,
    "rust": _flistScanRust,
    "javascript": _flistScanJavaScript,
    "perl": _flistScanPerl,
    "shell": _flistScanShell,
    "julia": _flistScanJulia,
    "matlab": _flistScanMatlab,
    "go": _flistScanGo,
    "json": _flistScanConfigFile,
    "yaml": _flistScanConfigFile,
}


def _flistScanByLanguage(sSourceCode, sLanguage):
    """Dispatch to language-specific scanner and return load call list."""
    fnScanner = DICT_LANGUAGE_SCANNERS.get(sLanguage)
    if fnScanner is None:
        return []
    return fnScanner(sSourceCode)


def _flistHarvestStringLiterals(sSourceCode, sLanguage):
    """Extract all quoted strings that look like data files."""
    sCommentPrefix = DICT_COMMENT_PREFIX.get(sLanguage, "#")
    sPattern = r'["\']((?:[^"\'\\]|\\.)+)["\']'
    listResults = []
    for iLineNumber, sLine in enumerate(sSourceCode.splitlines(), start=1):
        sStripped = sLine.strip()
        if not sStripped or _fbIsCommentLine(sStripped, sCommentPrefix):
            continue
        for matchResult in re.finditer(sPattern, sLine):
            sValue = matchResult.group(1)
            if fbLooksLikeDataFile(sValue):
                listResults.append({
                    "sFileName": sValue,
                    "sLoadFunction": "string-literal",
                    "iLineNumber": iLineNumber,
                })
    return listResults


def _flistMergeAndDeduplicate(listFunctionCalls, listStringLiterals):
    """Merge function-call and string-literal results, preferring function calls."""
    setFunctionFileNames = {d["sFileName"] for d in listFunctionCalls}
    listMerged = list(listFunctionCalls)
    for dictItem in listStringLiterals:
        if dictItem["sFileName"] not in setFunctionFileNames:
            listMerged.append(dictItem)
    return listMerged


def flistScanForLoadCalls(sSourceCode, sLanguage):
    """Scan source for file references via function calls and string literals."""
    listFunctionCalls = _flistScanByLanguage(sSourceCode, sLanguage)
    listStringLiterals = _flistHarvestStringLiterals(sSourceCode, sLanguage)
    return _flistMergeAndDeduplicate(listFunctionCalls, listStringLiterals)
