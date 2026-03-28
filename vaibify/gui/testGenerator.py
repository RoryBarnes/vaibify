"""Generate pytest unit tests for workflow steps via LLM."""

import asyncio
import json
import logging
import posixpath
import re

logger = logging.getLogger("vaibify")


_PROMPT_TEMPLATE = """Generate a pytest file that validates scientific data analysis outputs.

Step directory: {sDirectory}
Data analysis commands:
{sDataCommands}

Expected output files:
{sDataFiles}

Source code of the analysis scripts:
{sScriptContents}

Previews of existing output data:
{sDataPreviews}

Generate tests that validate:
1. All expected output files exist and are non-empty
2. Data formats are correct (loadable, correct shape/columns)
3. Numerical values are within physically reasonable ranges
4. No NaN or Inf values in numerical outputs

Return ONLY the Python code for a single pytest file. No explanations."""


_CLAUDE_MD_TEST_SECTION = """
# Vaibify Test Generation Instructions

When asked to generate tests for a workflow step, follow these instructions.
Each step has a directory, data commands, output files, and analysis scripts.
Vaibify uses three categories of tests stored in a `tests/` subdirectory.

## CRITICAL OUTPUT RULES

When generating all three test categories in one response, use this
exact format with three labeled fenced code blocks:

```INTEGRITY
import os
import pytest
# ... integrity test code ...
```

```QUALITATIVE
import pytest
# ... qualitative test code ...
```

```QUANTITATIVE
{
    "listStandards": [ ... ]
}
```

- No prose, no markdown bullets, no explanations between blocks.
- Every Python block must begin with all necessary imports.
  Always include `import os` and any other modules used.
- Do NOT describe what you would test. Write the actual pytest code.
- Python code must compile: `ast.parse(code)` must succeed.
- If you are unsure about file formats, write a test that loads the
  file and asserts it is not empty, rather than skipping it.

## Integrity Tests (test_integrity.py)

Generate a pytest file that validates structural integrity of data files:
1. All expected output files exist and are non-empty
2. Data files are loadable in their expected format (CSV, NPY, HDF5, etc.)
3. Data shapes and dimensions match what the analysis scripts produce
4. No NaN or Inf values in numerical columns or arrays
5. Column names or array keys match what the scripts produce

Do NOT test specific numerical values or string content.
Return ONLY the Python code for a single pytest file. No explanations.

## Qualitative Tests (test_qualitative.py)

Generate a pytest file that validates categorical and string outputs:
1. String or categorical values in output files (column headers, model names, labels)
2. Enum-like values (kernel types, method names, planet identifiers)
3. Boolean flags or status indicators
4. Any non-numeric metadata embedded in the outputs

For each categorical value found, assert it equals the exact expected string.
If no categorical outputs exist, generate a minimal test file with a single
test function named test_no_qualitative_outputs that asserts True.

IMPORTANT rules for qualitative tests:
- Only assert the PRESENCE of expected values. Never assert the ABSENCE
  of unexpected keys, columns, or values. Data files may contain fields
  beyond what the analysis script produces.
- Do not test exact key sets, column counts, or row counts — those
  belong in integrity tests.
Return ONLY the Python code for a single pytest file. No explanations.

## Quantitative Standards (quantitative_standards.json)

Extract numerical benchmark values into a JSON file (not Python code).
For each significant numerical result visible in the output data:
1. Identify the parameter with a descriptive camelCase name prefixed with "f"
2. Record the value at FULL double precision — use repr() or f"{value:.17g}"
   to capture all 17 significant digits. Never round or truncate
3. Identify the physical unit (e.g., "K", "W/m^2", "kg", or "" if dimensionless)
4. Specify the access path. The supported formats by file type are:

   **CSV** (`.csv`):
   - "column:ColName,index:N" (N=-1 for last row)

   **Numpy** (`.npy`):
   - "index:N" for 1D arrays
   - "index:R,C" for 2D arrays
   - "index:mean", "index:min", "index:max" for aggregates

   **JSON** (`.json`):
   - "key:path.to.field" (dot-separated nested keys)
   - "key:path.to.array,index:N" for arrays (N=-1 for last)

   **HDF5** (`.h5`, `.hdf5`):
   - "dataset:/group/datasetName,index:N"
   - "dataset:/group/datasetName,index:R,C"
   - "dataset:/group/datasetName,index:mean" (also min, max)

   **Whitespace-delimited text** (`.dat`, `.txt`):
   - "column:ColName,index:N" (first row is the header)

5. Optionally set "sFormat" to override extension-based format detection.
   Valid values: "csv", "npy", "json", "hdf5", "whitespace".
   Omit this field unless the extension is ambiguous.
6. Optionally override the default relative tolerance with "fRtol".

IMPORTANT: Only use access path formats listed above. Do not invent
new formats or combine aggregates with indices.

Return ONLY a JSON object (no markdown fences, no explanation) with this structure:
```json
{
    "listStandards": [
        {
            "sName": "fParameterName",
            "sDataFile": "filename.csv",
            "sAccessPath": "column:ColName,index:-1",
            "fValue": 1.234567890123e+02,
            "sUnit": "K"
        }
    ]
}
```

Focus on physically meaningful quantities.
Include initial and final state values where available.
"""


_CLAUDE_MD_MARKER = "# Vaibify Test Generation Instructions"
_CLAUDE_MD_VERSION = "v7"
_CLAUDE_MD_VERSION_TAG = "<!-- vaibify-test-instructions-v7 -->"


def fnEnsureClaudeMdInstructions(
    connectionDocker, sContainerId, sWorkspaceRoot="/workspace",
):
    """Write or update test generation instructions in CLAUDE.md."""
    sClaudeMdPath = posixpath.join(sWorkspaceRoot, "CLAUDE.md")
    sExisting = fsReadFileFromContainer(
        connectionDocker, sContainerId, sClaudeMdPath,
    )
    if _CLAUDE_MD_VERSION_TAG in sExisting:
        return
    sWithoutOld = _fsRemoveOldTestSection(sExisting)
    sContent = (
        sWithoutOld.rstrip() + "\n"
        + _CLAUDE_MD_VERSION_TAG + "\n"
        + _CLAUDE_MD_TEST_SECTION
    )
    connectionDocker.fnWriteFile(
        sContainerId, sClaudeMdPath, sContent.encode("utf-8"),
    )


def _fsRemoveOldTestSection(sContent):
    """Strip any prior vaibify test instruction block from CLAUDE.md."""
    iStart = sContent.find(_CLAUDE_MD_MARKER)
    if iStart == -1:
        return sContent
    return sContent[:iStart].rstrip()


def fbContainerHasClaude(connectionDocker, sContainerId):
    """Return True if the claude CLI is available in the container."""
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, "which claude"
    )
    return iExitCode == 0


def fsReadFileFromContainer(connectionDocker, sContainerId, sFilePath):
    """Read a text file from the container, returning empty on failure."""
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, sFilePath
        )
        return baContent.decode("utf-8", errors="replace")
    except Exception:
        return ""


def fsPreviewDataFile(
    connectionDocker, sContainerId, sFilePath, sDirectory,
):
    """Return a short preview of a data file's contents or structure."""
    sAbsPath = _fsResolvePath(sFilePath, sDirectory)
    sExtension = posixpath.splitext(sAbsPath)[1].lower()
    if sExtension == ".npy":
        return _fsPreviewNpy(connectionDocker, sContainerId, sAbsPath)
    if sExtension in (".h5", ".hdf5"):
        return _fsPreviewHdf5(connectionDocker, sContainerId, sAbsPath)
    return _fsPreviewText(connectionDocker, sContainerId, sAbsPath)


def _fsResolvePath(sFilePath, sDirectory):
    """Return absolute path, joining with directory if relative."""
    if posixpath.isabs(sFilePath):
        return sFilePath
    return posixpath.join(sDirectory, sFilePath)


def _fsPreviewNpy(connectionDocker, sContainerId, sAbsPath):
    """Preview a .npy file with shape, dtype, and summary statistics."""
    sCommand = (
        "python3 -c \""
        "import numpy as np; "
        "d=np.load(" + repr(sAbsPath) + "); "
        "print(f'shape={d.shape} dtype={d.dtype}'); "
        "f=d.flatten(); "
        "print(f'first={f[0]!r} last={f[-1]!r}'); "
        "print(f'min={f.min()!r} max={f.max()!r} mean={f.mean()!r}')"
        "\""
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"


def _fsPreviewHdf5(connectionDocker, sContainerId, sAbsPath):
    """Preview an HDF5 file's datasets with shape and summary stats."""
    sCommand = (
        "python3 -c \""
        "import h5py, numpy as np; "
        "f=h5py.File(" + repr(sAbsPath) + ",'r'); "
        "items=[]; "
        "f.visititems(lambda n,o: items.append(n) "
        "if isinstance(o,h5py.Dataset) else None); "
        "[print(f'dataset:{n} shape={f[n].shape} dtype={f[n].dtype} "
        "first={np.array(f[n]).flatten()[0]!r} "
        "last={np.array(f[n]).flatten()[-1]!r}') "
        "for n in items[:10]]; "
        "f.close()\""
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"


def _fsPreviewText(connectionDocker, sContainerId, sAbsPath):
    """Preview first and last lines of a text file."""
    from .pipelineRunner import fsShellQuote
    sQuoted = fsShellQuote(sAbsPath)
    sCommand = (
        f"head -10 {sQuoted} 2>/dev/null;"
        f" echo '...';"
        f" tail -3 {sQuoted} 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"


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


def fsBuildPrompt(sDirectory, dictStep, sScriptContents, sPreviews):
    """Construct the LLM prompt from the template and context."""
    sDataCommands = "\n".join(
        f"  {s}" for s in dictStep.get("saDataCommands", [])
    )
    sDataFiles = "\n".join(
        f"  {s}" for s in dictStep.get("saDataFiles", [])
    )
    return _PROMPT_TEMPLATE.format(
        sDirectory=sDirectory,
        sDataCommands=sDataCommands or "(none)",
        sDataFiles=sDataFiles or "(none)",
        sScriptContents=sScriptContents,
        sDataPreviews=sPreviews,
    )


def ftResultGenerateViaClaude(
    connectionDocker, sContainerId, sPrompt, sUser=None,
):
    """Run claude --print from /workspace so CLAUDE.md is loaded."""
    from .pipelineRunner import fsShellQuote

    sCommand = (
        f"cd /workspace && CLAUDECODE= claude --print"
        f" {fsShellQuote(sPrompt)}"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand, sUser=sUser
    )


def fsGenerateViaApi(sPrompt, sApiKey):
    """Call the Anthropic API directly, return generated text."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package is not installed. "
            "Install with: pip install anthropic"
        )
    client = anthropic.Anthropic(api_key=sApiKey)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": sPrompt}],
    )
    return message.content[0].text


def fsParseGeneratedCode(sRawOutput):
    """Extract Python code from LLM output, stripping markdown fences."""
    sStripped = sRawOutput.strip()
    matchFenced = re.search(
        r"```\w*\s*\n(.*?)```",
        sStripped, re.DOTALL,
    )
    sCode = matchFenced.group(1).strip() if matchFenced else sStripped
    sCode = fsRepairMissingImports(sCode)
    fbValidatePythonSyntax(sCode)
    return sCode


def fbValidatePythonSyntax(sCode):
    """Raise ValueError if code is not valid Python."""
    import ast
    try:
        ast.parse(sCode)
    except SyntaxError as error:
        raise ValueError(
            f"Generated code has syntax error: {error.msg} "
            f"(line {error.lineno})"
        )


def fsRepairMissingImports(sCode):
    """Add missing standard imports detected by compile check."""
    import ast
    try:
        ast.parse(sCode)
    except SyntaxError:
        return sCode
    listNeeded = []
    for sModule in ("os", "sys", "json", "pathlib", "csv"):
        if sModule + "." in sCode and f"import {sModule}" not in sCode:
            listNeeded.append(f"import {sModule}")
    if not listNeeded:
        return sCode
    return "\n".join(listNeeded) + "\n\n" + sCode


def fdictParseCombinedOutput(sRawOutput):
    """Parse a combined LLM response into three labeled sections."""
    dictSections = {}
    listLabels = ["INTEGRITY", "QUALITATIVE", "QUANTITATIVE"]
    for sLabel in listLabels:
        matchBlock = re.search(
            r"```" + sLabel + r"\s*\n(.*?)```",
            sRawOutput, re.DOTALL,
        )
        if matchBlock:
            dictSections[sLabel] = matchBlock.group(1).strip()
    return dictSections


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
    return posixpath.join(sStepDirectory, "tests", "test_quantitative.py")


def fsQuantitativeStandardsPath(sStepDirectory):
    """Return the quantitative standards JSON path for a step."""
    return posixpath.join(
        sStepDirectory, "tests", "quantitative_standards.json",
    )


def fnEnsureTestsDirectory(connectionDocker, sContainerId, sStepDirectory):
    """Create the tests subdirectory in the container if missing."""
    from .pipelineRunner import fsShellQuote
    sTestsDir = posixpath.join(sStepDirectory, "tests")
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sTestsDir)}"
    )


def fdictParseQuantitativeJson(sRawOutput):
    """Extract and parse JSON from LLM output for quantitative standards."""
    sStripped = sRawOutput.strip()
    matchFenced = re.search(
        r"```(?:json)?\s*\n(.*?)```", sStripped, re.DOTALL,
    )
    if matchFenced:
        sStripped = matchFenced.group(1).strip()
    try:
        return json.loads(sStripped)
    except json.JSONDecodeError:
        matchBrace = re.search(r"\{.*\}", sStripped, re.DOTALL)
        if matchBrace:
            return json.loads(matchBrace.group(0))
        return {"listStandards": []}


def _fdictWriteTestFile(connectionDocker, sContainerId, sCode, sFilePath):
    """Write a test file to the container and return result dict."""
    connectionDocker.fnWriteFile(
        sContainerId, sFilePath, sCode.encode("utf-8"),
    )
    sFilename = posixpath.basename(sFilePath)
    return {
        "sFilePath": sFilePath,
        "sContent": sCode,
        "saCommands": [f"pytest tests/{sFilename}"],
    }


def fdictGenerateTest(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None,
):
    """Orchestrate test generation: gather context, call LLM, save."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    sDirectory = dictStep.get("sDirectory", "")
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


def _fbOutputLooksValid(sOutput):
    """Return True if the output contains plausible LLM-generated content."""
    sStripped = sOutput.strip()
    if "```" in sStripped:
        return True
    if "def test_" in sStripped:
        return True
    if '"listStandards"' in sStripped:
        return True
    return False


def _fsInvokeLlm(
    connectionDocker, sContainerId, sPrompt, bUseApi, sApiKey,
    sUser=None,
):
    """Call the appropriate LLM provider and return raw text."""
    if bUseApi:
        return fsGenerateViaApi(sPrompt, sApiKey)
    iExitCode, sOutput = ftResultGenerateViaClaude(
        connectionDocker, sContainerId, sPrompt, sUser=sUser
    )
    if iExitCode != 0 and _fbOutputLooksValid(sOutput):
        return sOutput
    if iExitCode != 0:
        sHint = ""
        sLower = sOutput.lower()
        if "not logged in" in sLower or "/login" in sLower:
            sHint = (
                "\n\nClaude Code is not authenticated. "
                "Open a terminal and run 'claude' to log in."
            )
        raise RuntimeError(
            f"Claude CLI failed (exit {iExitCode}): "
            f"{sOutput.strip()}{sHint}"
        )
    return sOutput


_QUANTITATIVE_TEST_TEMPLATE = '''"""Quantitative benchmark tests generated by vaibify."""

import json
import pathlib

import numpy as np
import pytest


def _fdictParseAccessPath(sAccessPath):
    """Parse an access path into a dict with key, column, indices, and aggregate."""
    import re
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
    matchAggregate = re.search(r"index:(mean|min|max)", sAccessPath)
    if matchAggregate:
        dictResult["sAggregate"] = matchAggregate.group(1)
    else:
        matchIndex = re.search(r"index:([-\\d,]+)", sAccessPath)
        if matchIndex:
            dictResult["listIndices"] = [
                int(x) for x in matchIndex.group(1).split(",")
                if x.strip()
            ]
    return dictResult


_DICT_FORMAT_MAP = {
    ".npy": "npy",
    ".json": "json",
    ".csv": "csv",
    ".h5": "hdf5",
    ".hdf5": "hdf5",
    ".dat": "whitespace",
    ".txt": "whitespace",
}


def _fsInferFormat(sFullPath):
    """Infer the data format from the file extension."""
    sExtension = pathlib.Path(sFullPath).suffix.lower()
    return _DICT_FORMAT_MAP.get(sExtension, "csv")


def _fLoadValue(sDataFile, sAccessPath, sStepDirectory, sFormat=""):
    """Load a single value from a data file using the access path."""
    sFullPath = str(pathlib.Path(sStepDirectory) / sDataFile)
    dictAccess = _fdictParseAccessPath(sAccessPath)
    if not sFormat:
        sFormat = _fsInferFormat(sFullPath)
    dictLoaders = {
        "npy": _fLoadNumpyValue,
        "json": _fLoadJsonValue,
        "csv": _fLoadCsvValue,
        "hdf5": _fLoadHdf5Value,
        "whitespace": _fLoadWhitespaceValue,
    }
    fLoader = dictLoaders.get(sFormat)
    if fLoader is None:
        raise ValueError(f"Unsupported format: {sFormat}")
    return fLoader(sFullPath, dictAccess)


def _fLoadNumpyValue(sFullPath, dictAccess):
    """Load a value from a numpy file."""
    daData = np.load(sFullPath)
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate == "mean":
        return float(daData.mean())
    if sAggregate == "min":
        return float(daData.min())
    if sAggregate == "max":
        return float(daData.max())
    listIndices = dictAccess.get("listIndices", [-1])
    return float(daData[tuple(listIndices)])


def _fLoadJsonValue(sFullPath, dictAccess):
    """Load a value from a JSON file."""
    with open(sFullPath) as fileHandle:
        dictData = json.load(fileHandle)
    sKey = dictAccess.get("key", "")
    listKeys = sKey.split(".") if sKey else []
    value = dictData
    for sSubKey in listKeys:
        if isinstance(value, list):
            value = value[int(sSubKey)]
        else:
            value = value[sSubKey]
    listIndices = dictAccess.get("listIndices", None)
    if listIndices is not None:
        for iIdx in listIndices:
            value = value[iIdx]
    return float(value)


def _fLoadCsvValue(sFullPath, dictAccess):
    """Load a value from a CSV file."""
    import csv
    sColumn = dictAccess.get("column", "")
    listIndices = dictAccess.get("listIndices", [-1])
    iIndex = listIndices[0] if listIndices else -1
    with open(sFullPath, newline="") as fileHandle:
        reader = csv.DictReader(fileHandle)
        listRows = list(reader)
    return float(listRows[iIndex][sColumn])


def _fLoadHdf5Value(sFullPath, dictAccess):
    """Load a value from an HDF5 file."""
    import h5py
    sDataset = dictAccess.get("dataset", "")
    with h5py.File(sFullPath, "r") as fileHdf5:
        daData = np.array(fileHdf5[sDataset])
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate == "mean":
        return float(daData.mean())
    if sAggregate == "min":
        return float(daData.min())
    if sAggregate == "max":
        return float(daData.max())
    listIndices = dictAccess.get("listIndices", [-1])
    return float(daData[tuple(listIndices)])


def _fLoadWhitespaceValue(sFullPath, dictAccess):
    """Load a value from a whitespace-delimited text file."""
    sColumn = dictAccess.get("column", "")
    listIndices = dictAccess.get("listIndices", [-1])
    iIndex = listIndices[0] if listIndices else -1
    with open(sFullPath) as fileHandle:
        sHeader = fileHandle.readline()
        listColumns = sHeader.split()
        listRows = []
        for sLine in fileHandle:
            sStripped = sLine.strip()
            if not sStripped or sStripped.startswith("#"):
                continue
            listRows.append(sStripped.split())
    if sColumn:
        iColumn = listColumns.index(sColumn)
    else:
        iColumn = listIndices[1] if len(listIndices) > 1 else 0
    return float(listRows[iIndex][iColumn])


def _fdictLoadStandardsFile():
    """Load the quantitative standards JSON file."""
    sJsonPath = str(
        pathlib.Path(__file__).parent / "quantitative_standards.json"
    )
    with open(sJsonPath) as fileHandle:
        return json.load(fileHandle)


_DICT_STANDARDS = _fdictLoadStandardsFile()
_F_DEFAULT_RTOL = _DICT_STANDARDS.get("fDefaultRtol", 1e-6)
_LIST_STANDARDS = _DICT_STANDARDS["listStandards"]
_STEP_DIRECTORY = str(pathlib.Path(__file__).parent.parent)


@pytest.mark.parametrize(
    "dictStandard",
    _LIST_STANDARDS,
    ids=[s["sName"] for s in _LIST_STANDARDS],
)
def test_quantitative_benchmark(dictStandard):
    """Compare output value against stored benchmark within tolerance."""
    fActual = _fLoadValue(
        dictStandard["sDataFile"],
        dictStandard["sAccessPath"],
        _STEP_DIRECTORY,
        sFormat=dictStandard.get("sFormat", ""),
    )
    fExpected = dictStandard["fValue"]
    fRtol = dictStandard.get("fRtol", _F_DEFAULT_RTOL)
    fAtol = dictStandard.get("fAtol", 1e-08)
    assert np.allclose(fActual, fExpected, rtol=fRtol, atol=fAtol), (
        f"{dictStandard['sName']}: expected {fExpected}, got {fActual}"
    )
'''


def fsBuildQuantitativeTestCode():
    """Return the deterministic quantitative test file content."""
    return _QUANTITATIVE_TEST_TEMPLATE


def fdictGenerateAllTests(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None,
):
    """Generate all three test categories via separate LLM calls."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    sDirectory = dictStep.get("sDirectory", "")
    fTolerance = dictWorkflow.get("fTolerance", 1e-6)
    sDataFiles = ", ".join(dictStep.get("saDataFiles", []))
    sScripts, sPreviews = fsBuildStepContext(
        connectionDocker, sContainerId, dictStep, dictVariables,
    )
    if not bUseApi:
        fnEnsureClaudeMdInstructions(
            connectionDocker, sContainerId,
        )
    fnEnsureTestsDirectory(connectionDocker, sContainerId, sDirectory)
    dictResult = {}
    dictResult["dictIntegrity"] = _fdictGenerateSingleCategory(
        connectionDocker, sContainerId, sDirectory,
        "integrity", sDataFiles, sScripts, sPreviews,
        bUseApi, sApiKey, sUser,
    )
    dictResult["dictQualitative"] = _fdictGenerateSingleCategory(
        connectionDocker, sContainerId, sDirectory,
        "qualitative", sDataFiles, sScripts, sPreviews,
        bUseApi, sApiKey, sUser,
    )
    dictResult["dictQuantitative"] = _fdictGenerateQuantitativeCategory(
        connectionDocker, sContainerId, sDirectory,
        sDataFiles, sScripts, sPreviews,
        fTolerance, bUseApi, sApiKey, sUser,
    )
    return dictResult


def _fdictGenerateSingleCategory(
    connectionDocker, sContainerId, sDirectory,
    sCategory, sDataFiles, sScriptContents, sDataPreviews,
    bUseApi, sApiKey, sUser,
):
    """Generate one Python test category via LLM, with error isolation."""
    sPrompt = (
        f"Generate {sCategory} tests for the step in {sDirectory}.\n"
        f"See CLAUDE.md for instructions.\n"
        f"Output files: {sDataFiles}\n\n"
        f"Source code of analysis scripts:\n{sScriptContents}\n\n"
        f"Data file previews:\n{sDataPreviews}\n\n"
        f"Return ONLY Python code, no explanations."
    )
    dictPaths = {
        "integrity": fsIntegrityTestPath,
        "qualitative": fsQualitativeTestPath,
    }
    sFilePath = dictPaths[sCategory](sDirectory)
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
    """Generate quantitative standards JSON via LLM, with error isolation."""
    sPrompt = (
        f"Generate quantitative standards JSON for the step in "
        f"{sDirectory}. Default tolerance: {fTolerance}.\n"
        f"See CLAUDE.md for instructions.\n"
        f"Output files: {sDataFiles}\n\n"
        f"Source code of analysis scripts:\n{sScriptContents}\n\n"
        f"Data file previews (use these actual values):\n"
        f"{sDataPreviews}\n\n"
        f"IMPORTANT: Extract benchmark values from the data previews "
        f"above. Use repr() precision — never round or guess values.\n"
        f"Return ONLY a JSON object, no explanations."
    )
    try:
        sRaw = _fsInvokeLlm(
            connectionDocker, sContainerId, sPrompt,
            bUseApi, sApiKey, sUser=sUser,
        )
        logger.debug("Quantitative raw output: %s", sRaw[:500])
        dictStandards = fdictParseQuantitativeJson(sRaw)
        dictStandards["fDefaultRtol"] = fTolerance
        sStandardsPath = fsQuantitativeStandardsPath(sDirectory)
        sJsonContent = json.dumps(dictStandards, indent=4)
        connectionDocker.fnWriteFile(
            sContainerId, sStandardsPath,
            sJsonContent.encode("utf-8"),
        )
        sTestCode = fsBuildQuantitativeTestCode()
        sTestPath = fsQuantitativeTestPath(sDirectory)
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
    except Exception as error:
        return _fdictErrorResult(str(error))


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
    try:
        with open("/tmp/vaibify_test_errors.log", "a") as fLog:
            fLog.write(sMessage + "\n---\n")
    except Exception:
        pass
