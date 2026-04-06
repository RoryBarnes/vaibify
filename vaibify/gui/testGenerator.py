"""Generate pytest unit tests for workflow steps via LLM."""

import json
import logging
import os
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
   Valid values: "csv", "npy", "npz", "json", "jsonl", "hdf5",
   "whitespace", "keyvalue", "excel", "fits", "matlab", "parquet",
   "image", "fasta", "fastq", "vcf", "bed", "gff", "sam", "bam",
   "fortran", "spss", "stata", "sas", "rdata", "votable", "ipac",
   "pcap", "vtk", "cgns", "safetensors", "tfrecord", "syslog", "cef",
   "fixedwidth", "multitable".
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

## Test Result Tracking

Every `tests/` directory must contain the vaibify `conftest.py` marker
plugin. This plugin writes a JSON result marker after each pytest run so
the dashboard can detect test outcomes automatically. If creating a new
`tests/` directory, copy the marker plugin:
`cp /usr/share/vaibify/conftest_marker.py tests/conftest.py`
Do NOT modify or delete conftest.py — vaibify owns this file.
"""


_CLAUDE_MD_MARKER = "# Vaibify Test Generation Instructions"
_CLAUDE_MD_VERSION = "v9"
_CLAUDE_MD_VERSION_TAG = "<!-- vaibify-test-instructions-v9 -->"


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
        "d=np.load(" + repr(sAbsPath) + ",allow_pickle=False); "
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


def fnEnsureTestsDirectory(connectionDocker, sContainerId, sStepDirectory):
    """Create the tests subdirectory in the container if missing."""
    from .pipelineRunner import fsShellQuote
    sTestsDir = posixpath.join(sStepDirectory, "tests")
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sTestsDir)}"
    )


def fsConftestPath(sStepDirectory):
    """Return the conftest.py path for a step's tests directory."""
    return posixpath.join(sStepDirectory, "tests", "conftest.py")


def fsConftestContent():
    """Return the conftest.py marker plugin source code."""
    return _CONFTEST_MARKER_TEMPLATE


def fnWriteConftestMarker(
    connectionDocker, sContainerId, sStepDirectory,
):
    """Write the conftest.py marker plugin into a step's tests dir."""
    sPath = fsConftestPath(sStepDirectory)
    connectionDocker.fnWriteFile(
        sContainerId, sPath,
        _CONFTEST_MARKER_TEMPLATE.encode("utf-8"),
    )


_CONFTEST_MARKER_TEMPLATE = '''\
"""Vaibify test result marker plugin.

Auto-generated by vaibify. Do not remove.
Writes a JSON result marker after every pytest session so the
dashboard can detect test outcomes regardless of how pytest was invoked.
"""

import json
import os
import time
from pathlib import Path

_MARKER_DIR = Path("/workspace/.vaibify/test_markers")

_CATEGORY_MAP = {
    "test_integrity": "integrity",
    "test_qualitative": "qualitative",
    "test_quantitative": "quantitative",
}


def _fsStepDirToMarkerName(sStepDir):
    """Convert a step directory path to a safe marker filename."""
    return sStepDir.strip("/").replace("/", "_") + ".json"


def _fsGetCategory(sNodeId):
    """Map a test node ID to a category name."""
    for sPrefix, sCategory in _CATEGORY_MAP.items():
        if sPrefix in sNodeId:
            return sCategory
    return "other"


def _fdictBuildCategoryResults(session):
    """Tally pass/fail counts per test category from session items."""
    dictCategories = {}
    for item in session.items:
        sCategory = _fsGetCategory(item.nodeid)
        dictCat = dictCategories.setdefault(
            sCategory, {"iPassed": 0, "iFailed": 0}
        )
        bPassed = (
            hasattr(item, "rep_call")
            and item.rep_call is not None
            and item.rep_call.passed
        )
        if bPassed:
            dictCat["iPassed"] += 1
        else:
            dictCat["iFailed"] += 1
    return dictCategories


def pytest_sessionfinish(session, exitstatus):
    """Write a JSON marker after every pytest run."""
    sStepDir = str(Path(__file__).resolve().parent.parent)
    dictMarker = {
        "sDirectory": sStepDir,
        "iExitStatus": exitstatus,
        "fTimestamp": time.time(),
        "iCollected": session.testscollected,
        "dictCategories": _fdictBuildCategoryResults(session),
    }
    _MARKER_DIR.mkdir(parents=True, exist_ok=True)
    sFilename = _fsStepDirToMarkerName(sStepDir)
    (_MARKER_DIR / sFilename).write_text(
        json.dumps(dictMarker, indent=2)
    )


import pytest

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store the call report on the item for sessionfinish access."""
    outcome = yield
    if call.when == "call":
        item.rep_call = outcome.get_result()
'''


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


def _fnRaiseClaudeError(iExitCode, sOutput):
    """Raise RuntimeError with a helpful hint for Claude CLI failures."""
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
        _fnRaiseClaudeError(iExitCode, sOutput)
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
    matchSection = re.search(r"section:(\\d+)", sAccessPath)
    if matchSection:
        dictResult["iSection"] = int(matchSection.group(1))
    matchHdu = re.search(r"hdu:(\\d+)", sAccessPath)
    if matchHdu:
        dictResult["iHdu"] = int(matchHdu.group(1))
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


def _fLoadValue(sDataFile, sAccessPath, sStepDirectory, sFormat=""):
    """Load a single value from a data file using the access path."""
    sFullPath = str(pathlib.Path(sStepDirectory) / sDataFile)
    dictAccess = _fdictParseAccessPath(sAccessPath)
    if not sFormat:
        sFormat = _fsInferFormat(sFullPath)
    if sFormat is None:
        sFormat = "whitespace"
    dictLoaders = {
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
    fLoader = dictLoaders.get(sFormat)
    if fLoader is None:
        raise ValueError(f"Unsupported format: {sFormat}")
    return fLoader(sFullPath, dictAccess)


def _fLoadNumpyValue(sFullPath, dictAccess):
    """Load a value from a numpy file."""
    try:
        daData = np.load(sFullPath, allow_pickle=False)
    except (ValueError, OSError) as exc:
        raise ValueError(f"Failed to load {sFullPath} as npy: {exc}") from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fExtractArrayValue(daData, dictAccess):
    """Extract a scalar from an array by aggregate or index."""
    if daData.ndim == 0:
        return float(daData)
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate == "mean":
        return float(daData.mean())
    if sAggregate == "min":
        return float(daData.min())
    if sAggregate == "max":
        return float(daData.max())
    listIndices = dictAccess.get("listIndices", [-1])
    if len(listIndices) == 1 and daData.ndim > 1:
        return float(daData.flat[listIndices[0]])
    return float(daData[tuple(listIndices)])


def _fLoadNpzValue(sFullPath, dictAccess):
    """Load a value from a numpy .npz archive."""
    try:
        archiveNpz = np.load(sFullPath, allow_pickle=False)
    except (ValueError, OSError) as exc:
        raise ValueError(f"Failed to load {sFullPath} as npz: {exc}") from exc
    sKey = dictAccess.get("key", list(archiveNpz.files)[0])
    daData = archiveNpz[sKey]
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadKeyvalueValue(sFullPath, dictAccess):
    """Load a value from a key = value text file."""
    sTargetKey = dictAccess.get("key", "")
    with open(sFullPath, encoding="utf-8", errors="replace") as fileHandle:
        for sLine in fileHandle:
            sStripped = sLine.strip()
            if not sStripped or sStripped.startswith("#"):
                continue
            if "=" not in sStripped:
                continue
            sKey, sVal = sStripped.split("=", 1)
            if sKey.strip() == sTargetKey:
                return float(sVal.strip())
    raise KeyError(f"Key {sTargetKey!r} not found in {sFullPath}")


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
    """Detect if first line is header or data, return (sHeader, listRows)."""
    if not listDataLines:
        return ("", [])
    listTokens = listDataLines[0].split()
    bAllNumeric = all(_fbIsNumericToken(s) for s in listTokens)
    if bAllNumeric:
        return ("", listDataLines)
    return (listDataLines[0], listDataLines[1:])


def _fLoadJsonValue(sFullPath, dictAccess):
    """Load a value from a JSON file."""
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fileHandle:
            dictData = json.load(fileHandle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to load {sFullPath} as json: {exc}") from exc
    try:
        return _fNavigateJsonValue(dictData, dictAccess)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Failed to access json path in {sFullPath}: {exc}") from exc


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
        return float(getattr(daArray, sAggregate)())
    listIndices = dictAccess.get("listIndices", None)
    if listIndices is not None:
        for iIdx in listIndices:
            value = value[iIdx]
    return float(value)


def _fLoadCsvValue(sFullPath, dictAccess):
    """Load a value from a CSV file."""
    import csv
    sColumn = dictAccess.get("column", "")
    try:
        with open(sFullPath, newline="", encoding="utf-8", errors="replace") as fileHandle:
            reader = csv.DictReader(fileHandle)
            listRows = list(reader)
    except csv.Error as exc:
        raise ValueError(f"Failed to load {sFullPath} as csv: {exc}") from exc
    try:
        sAggregate = dictAccess.get("sAggregate")
        if sAggregate and sColumn:
            daValues = np.array([float(r[sColumn]) for r in listRows])
            return float(getattr(daValues, sAggregate)())
        listIndices = dictAccess.get("listIndices", [-1])
        iIndex = listIndices[0] if listIndices else -1
        return float(listRows[iIndex][sColumn])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"Failed to access csv column in {sFullPath}: {exc}") from exc


def _fLoadHdf5Value(sFullPath, dictAccess):
    """Load a value from an HDF5 file."""
    import h5py
    sDataset = dictAccess.get("dataset", "")
    try:
        with h5py.File(sFullPath, "r") as fileHdf5:
            daData = np.array(fileHdf5[sDataset])
    except (OSError, KeyError) as exc:
        raise ValueError(f"Failed to load {sFullPath} as hdf5: {exc}") from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadWhitespaceValue(sFullPath, dictAccess):
    """Load a value from a whitespace-delimited text file."""
    sColumn = dictAccess.get("column", "")
    listIndices = dictAccess.get("listIndices", [-1])
    iIndex = listIndices[0] if listIndices else -1
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fileHandle:
            listRawLines = fileHandle.readlines()
    except OSError as exc:
        raise ValueError(f"Failed to load {sFullPath} as whitespace: {exc}") from exc
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
        raise ValueError(f"Column {sColumn!r} not found in {sFullPath}") from exc
    sAggregate = dictAccess.get("sAggregate")
    if sAggregate:
        daValues = np.array([float(r[iColumn]) for r in listRows])
        return float(getattr(daValues, sAggregate)())
    return float(listRows[iIndex][iColumn])


def _fLoadJsonlValue(sFullPath, dictAccess):
    """Load a value from a JSON Lines file."""
    import json as jsonMod
    try:
        with open(sFullPath, encoding="utf-8", errors="replace") as fh:
            listRecords = [jsonMod.loads(sLine) for sLine in fh if sLine.strip()]
    except jsonMod.JSONDecodeError as exc:
        raise ValueError(f"Failed to load {sFullPath} as jsonl: {exc}") from exc
    sKey = dictAccess.get("key", "")
    sAggregate = dictAccess.get("sAggregate")
    try:
        if sAggregate and sKey:
            daValues = np.array([float(r[sKey]) for r in listRecords])
            return float(getattr(daValues, sAggregate)())
        listIndices = dictAccess.get("listIndices", [0])
        iRow = listIndices[0] if listIndices else 0
        if sKey:
            return float(listRecords[iRow][sKey])
        return float(listRecords[iRow])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Failed to access jsonl data in {sFullPath}: {exc}") from exc


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
        raise ValueError(f"Failed to load {sFullPath} as excel: {exc}") from exc
    try:
        listHeaders = [str(c) if c else f"col{i}" for i, c in enumerate(listRows[0])]
        sColumn = dictAccess.get("column", listHeaders[0])
        iCol = listHeaders.index(sColumn)
        sAggregate = dictAccess.get("sAggregate")
        if sAggregate:
            daValues = np.array([float(r[iCol]) for r in listRows[1:]])
            return float(getattr(daValues, sAggregate)())
        listIndices = dictAccess.get("listIndices", [-1])
        iRow = listIndices[0] if listIndices else -1
        return float(listRows[1:][iRow][iCol])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(f"Failed to access excel data in {sFullPath}: {exc}") from exc


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
        raise ValueError(f"Failed to load {sFullPath} as fits: {exc}") from exc
    if sAggregate:
        return float(getattr(daData, sAggregate)())
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
        raise ValueError(f"Failed to load {sFullPath} as matlab: {exc}") from exc
    sKey = dictAccess.get("key", "")
    if not sKey:
        listKeys = [k for k in dictMat if not k.startswith("__")]
        sKey = listKeys[0]
    try:
        daData = np.array(dictMat[sKey], dtype=float).flatten()
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Failed to access matlab variable in {sFullPath}: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as parquet: {exc}") from exc
    try:
        sColumn = dictAccess.get("column", table.column_names[0])
        daValues = table.column(sColumn).to_numpy().astype(float)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Failed to access parquet column in {sFullPath}: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as image: {exc}") from exc
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
        return float(getattr(daLengths, sAggregate)())
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
        return float(getattr(daValues, sAggregate)())
    listIndices = dictAccess.get("listIndices", [0])
    return float(daValues[listIndices[0]])


def _fLoadVcfValue(sFullPath, dictAccess):
    """Load a value from a VCF file."""
    return _fLoadTabularWithComments(
        sFullPath, dictAccess, sCommentPrefix="##", sHeaderPrefix="#",
    )


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
                listHeaders = sLine.lstrip(sHeaderPrefix).strip().split("\\t")
                continue
            listRows.append(sLine.strip().split("\\t"))
    return _fExtractTabularValue(listHeaders, listRows, dictAccess)


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
        return float(getattr(daValues, sAggregate)())
    listIndices = dictAccess.get("listIndices", [-1])
    iRow = listIndices[0] if listIndices else -1
    return float(listRows[iRow][iCol])


def _fLoadBedValue(sFullPath, dictAccess):
    """Load a value from a BED file."""
    listHeaders = [
        "chrom", "chromStart", "chromEnd", "name", "score", "strand",
    ]
    listRows = []
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        for sLine in fh:
            if sLine.strip() and not sLine.startswith("#"):
                listRows.append(sLine.strip().split("\\t"))
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
                listRows.append(sLine.strip().split("\\t"))
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
                listRows.append(sLine.strip().split("\\t"))
    return _fExtractTabularValue(listHeaders, listRows, dictAccess)


def _fLoadSyslogValue(sFullPath, dictAccess):
    """Load a value from a syslog file (line count)."""
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listLines = [s for s in fh if s.strip()]
    listIndices = dictAccess.get("listIndices", [0])
    return float(len(listLines) if listIndices == [0] else listIndices[0])


def _fLoadCefValue(sFullPath, dictAccess):
    """Load a value from a CEF file (record count)."""
    with open(sFullPath, encoding="utf-8", errors="replace") as fh:
        listRecords = [s for s in fh if s.strip().startswith("CEF:")]
    listIndices = dictAccess.get("listIndices", [0])
    return float(len(listRecords) if listIndices == [0] else listIndices[0])


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
    import re as reModule
    listSections = reModule.split(r"\\n\\s*\\n|\\n[=\\-]{3,}\\n", sContent)
    listSections = [s.strip() for s in listSections if s.strip()]
    iSection = dictAccess.get("iSection", 0)
    sSection = listSections[iSection]
    listLines = sSection.strip().splitlines()
    listFiltered = [s for s in listLines if s.strip()]
    sHeader, listDataRows = _ftSplitHeaderAndData(listFiltered)
    listColumns = sHeader.split() if sHeader else []
    sColumn = dictAccess.get("column", "")
    iCol = listColumns.index(sColumn) if sColumn and listColumns else 0
    sAggregate = dictAccess.get("sAggregate")
    listParsedRows = [r.split() for r in listDataRows]
    if sAggregate:
        daValues = np.array([float(r[iCol]) for r in listParsedRows])
        return float(getattr(daValues, sAggregate)())
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
        raise ValueError(f"Failed to load {sFullPath} as bam: {exc}") from exc
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
        raise ImportError("scipy is required to load FORTRAN binary files")
    try:
        fortranFile = FortranFile(sFullPath, "r")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Failed to load {sFullPath} as fortran: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as spss: {exc}") from exc
    return _fExtractDataframeValue(dfData, dictAccess, sFullPath)


def _fExtractDataframeValue(dfData, dictAccess, sFullPath=""):
    """Extract a value from a pandas DataFrame."""
    try:
        sColumn = dictAccess.get("column", dfData.columns[0])
        sAggregate = dictAccess.get("sAggregate")
        if sAggregate:
            return float(getattr(dfData[sColumn].astype(float), sAggregate)())
        listIndices = dictAccess.get("listIndices", [-1])
        iRow = listIndices[0] if listIndices else -1
        return float(dfData[sColumn].iloc[iRow])
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ValueError(f"Failed to access dataframe column in {sFullPath}: {exc}") from exc


def _fLoadStataValue(sFullPath, dictAccess):
    """Load a value from a Stata .dta file."""
    try:
        import pyreadstat
    except ImportError:
        raise ImportError("pyreadstat is required to load Stata files")
    try:
        dfData, _ = pyreadstat.read_dta(sFullPath)
    except Exception as exc:
        raise ValueError(f"Failed to load {sFullPath} as stata: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as sas: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as rdata: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as votable: {exc}") from exc
    try:
        sColumn = dictAccess.get("column", table.colnames[0])
        daValues = np.array(table[sColumn], dtype=float)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Failed to access votable column in {sFullPath}: {exc}") from exc
    return _fExtractArrayValue(daValues, dictAccess)


def _fLoadIpacValue(sFullPath, dictAccess):
    """Load a value from an IPAC table file."""
    try:
        from astropy.io import ascii as astropyAscii
    except ImportError:
        raise ImportError("astropy is required to load IPAC table files")
    try:
        table = astropyAscii.read(sFullPath, format="ipac")
    except Exception as exc:
        raise ValueError(f"Failed to load {sFullPath} as ipac: {exc}") from exc
    try:
        sColumn = dictAccess.get("column", table.colnames[0])
        daValues = np.array(table[sColumn], dtype=float)
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Failed to access ipac column in {sFullPath}: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as pcap: {exc}") from exc
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
        raise ValueError(f"Failed to load {sFullPath} as vtk: {exc}") from exc
    sKey = dictAccess.get("key", "")
    if not sKey and mesh.array_names:
        sKey = mesh.array_names[0]
    try:
        daData = np.array(mesh[sKey], dtype=float).flatten()
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Failed to access vtk array in {sFullPath}: {exc}") from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadCgnsValue(sFullPath, dictAccess):
    """Load a value from a CGNS file (HDF5 under the hood)."""
    import h5py
    sDataset = dictAccess.get("dataset", "")
    try:
        with h5py.File(sFullPath, "r") as fileHdf5:
            daData = np.array(fileHdf5[sDataset])
    except (OSError, KeyError) as exc:
        raise ValueError(f"Failed to load {sFullPath} as cgns: {exc}") from exc
    return _fExtractArrayValue(daData.flatten(), dictAccess)


def _fLoadSafetensorsValue(sFullPath, dictAccess):
    """Load a value from a safetensors file."""
    try:
        from safetensors import safe_open
    except ImportError:
        raise ImportError("safetensors is required to load safetensors files")
    sKey = dictAccess.get("key", "")
    try:
        with safe_open(sFullPath, framework="numpy") as fh:
            if not sKey:
                sKey = list(fh.keys())[0]
            daData = fh.get_tensor(sKey).astype(float).flatten()
    except Exception as exc:
        raise ValueError(f"Failed to load {sFullPath} as safetensors: {exc}") from exc
    return _fExtractArrayValue(daData, dictAccess)


def _fLoadTfrecordValue(sFullPath, dictAccess):
    """Load a value from a TFRecord file."""
    try:
        from tfrecord.reader import tfrecord_iterator
    except ImportError:
        raise ImportError("tfrecord is required to load TFRecord files")
    try:
        listRecords = list(tfrecord_iterator(sFullPath))
    except Exception as exc:
        raise ValueError(f"Failed to load {sFullPath} as tfrecord: {exc}") from exc
    sKey = dictAccess.get("key", "")
    try:
        if sKey and listRecords:
            daValues = np.array(
                [float(r[sKey]) for r in listRecords], dtype=float,
            )
        else:
            daValues = np.array([float(len(r)) for r in listRecords])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Failed to access tfrecord key in {sFullPath}: {exc}") from exc
    return _fExtractArrayValue(daValues, dictAccess)


def _fdictLoadStandardsFile():
    """Load the quantitative standards JSON file."""
    sJsonPath = str(
        pathlib.Path(__file__).parent / "quantitative_standards.json"
    )
    with open(sJsonPath, encoding="utf-8") as fileHandle:
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


_INTEGRITY_TEST_TEMPLATE = '''"""Integrity tests generated by vaibify."""

import json
import os
import pathlib

import numpy as np
import pytest

try:
    import h5py
except ImportError:
    h5py = None

try:
    import csv as csvModule
except ImportError:
    csvModule = None


def _fdictLoadIntegrityStandards():
    """Load the integrity standards JSON file."""
    sJsonPath = str(
        pathlib.Path(__file__).parent / "integrity_standards.json"
    )
    with open(sJsonPath, encoding="utf-8") as fileHandle:
        return json.load(fileHandle)


_DICT_STANDARDS = _fdictLoadIntegrityStandards()
_LIST_STANDARDS = _DICT_STANDARDS["listStandards"]
_STEP_DIRECTORY = str(pathlib.Path(__file__).parent.parent)


def _fnCheckFileExists(sFullPath):
    """Assert the file exists and is non-empty."""
    assert os.path.isfile(sFullPath), f"File not found: {sFullPath}"
    assert os.path.getsize(sFullPath) > 0, f"File is empty: {sFullPath}"


def _fnCheckShape(daData, tExpectedShape, sFullPath):
    """Assert the data array shape matches the expected shape."""
    if tExpectedShape is not None:
        assert list(daData.shape) == tExpectedShape, (
            f"{sFullPath}: expected shape {tExpectedShape}, "
            f"got {list(daData.shape)}"
        )


def _fnCheckNanInf(daData, bCheckNaN, bCheckInf, sFullPath):
    """Assert no NaN or Inf values in numeric data."""
    if not np.issubdtype(daData.dtype, np.number):
        return
    if bCheckNaN:
        assert not np.any(np.isnan(daData)), (
            f"{sFullPath}: contains NaN values"
        )
    if bCheckInf:
        assert not np.any(np.isinf(daData)), (
            f"{sFullPath}: contains Inf values"
        )


def _fnCheckNpy(sFullPath, dictStandard):
    """Validate a numpy .npy file."""
    daData = np.load(sFullPath, allow_pickle=False)
    tShape = dictStandard.get("tExpectedShape")
    _fnCheckShape(daData, tShape, sFullPath)
    _fnCheckNanInf(
        daData, dictStandard.get("bCheckNaN", False),
        dictStandard.get("bCheckInf", False), sFullPath,
    )


def _fnCheckNpz(sFullPath, dictStandard):
    """Validate a numpy .npz archive."""
    archiveNpz = np.load(sFullPath, allow_pickle=False)
    assert len(archiveNpz.files) > 0, f"{sFullPath}: empty npz archive"
    for sKey in archiveNpz.files:
        daData = archiveNpz[sKey]
        if np.issubdtype(daData.dtype, np.number):
            _fnCheckNanInf(
                daData, dictStandard.get("bCheckNaN", False),
                dictStandard.get("bCheckInf", False), sFullPath,
            )


def _fnCheckCsv(sFullPath, dictStandard):
    """Validate a CSV file."""
    import csv
    with open(sFullPath, newline="", encoding="utf-8") as fh:
        listRows = list(csv.DictReader(fh))
    tShape = dictStandard.get("tExpectedShape")
    if tShape is not None and len(tShape) >= 1:
        assert len(listRows) == tShape[0], (
            f"{sFullPath}: expected {tShape[0]} rows, got {len(listRows)}"
        )
    if dictStandard.get("bCheckNaN", False):
        for dictRow in listRows:
            for sVal in dictRow.values():
                try:
                    fVal = float(sVal)
                    assert not np.isnan(fVal), (
                        f"{sFullPath}: contains NaN"
                    )
                    if dictStandard.get("bCheckInf", False):
                        assert not np.isinf(fVal), (
                            f"{sFullPath}: contains Inf"
                        )
                except ValueError:
                    pass


def _fnCheckJson(sFullPath, dictStandard):
    """Validate a JSON file."""
    with open(sFullPath, encoding="utf-8") as fh:
        json.load(fh)


def _fnCheckHdf5(sFullPath, dictStandard):
    """Validate an HDF5 file."""
    assert h5py is not None, "h5py is not installed"
    with h5py.File(sFullPath, "r") as fh:
        assert len(fh.keys()) > 0, f"{sFullPath}: empty HDF5 file"
        if dictStandard.get("bCheckNaN", False):
            def fnCheckDataset(sName, obj):
                if isinstance(obj, h5py.Dataset):
                    daData = np.array(obj)
                    if np.issubdtype(daData.dtype, np.number):
                        _fnCheckNanInf(
                            daData, True,
                            dictStandard.get("bCheckInf", False),
                            sFullPath,
                        )
            fh.visititems(fnCheckDataset)


def _fnCheckWhitespace(sFullPath, dictStandard):
    """Validate a whitespace-delimited file."""
    with open(sFullPath, encoding="utf-8") as fh:
        listRows = [
            s for s in fh if s.strip()
            and not s.strip().startswith("#")
        ]
    tShape = dictStandard.get("tExpectedShape")
    if tShape is not None and len(tShape) >= 1:
        assert len(listRows) >= tShape[0], (
            f"{sFullPath}: expected >= {tShape[0]} rows"
        )
    if dictStandard.get("bCheckNaN", False):
        for sLine in listRows:
            for sToken in sLine.split():
                try:
                    fVal = float(sToken)
                    assert not np.isnan(fVal)
                    if dictStandard.get("bCheckInf", False):
                        assert not np.isinf(fVal)
                except ValueError:
                    pass


def _fnCheckKeyvalue(sFullPath, dictStandard):
    """Validate a key=value text file."""
    with open(sFullPath, encoding="utf-8") as fh:
        listLines = [s for s in fh if "=" in s]
    assert len(listLines) > 0, f"{sFullPath}: no key=value pairs"


def _fnCheckJsonl(sFullPath, dictStandard):
    """Validate a JSON Lines file."""
    import json as jsonMod
    with open(sFullPath, encoding="utf-8") as fh:
        listRecords = [jsonMod.loads(s) for s in fh if s.strip()]
    assert len(listRecords) > 0, f"{sFullPath}: empty JSONL"


def _fnCheckGenericText(sFullPath, dictStandard):
    """Validate a generic text file by checking it is non-empty."""
    with open(sFullPath, encoding="utf-8") as fh:
        sContent = fh.read()
    assert len(sContent.strip()) > 0, f"{sFullPath}: empty file"


_DICT_INTEGRITY_CHECKERS = {
    "npy": _fnCheckNpy,
    "npz": _fnCheckNpz,
    "csv": _fnCheckCsv,
    "json": _fnCheckJson,
    "hdf5": _fnCheckHdf5,
    "whitespace": _fnCheckWhitespace,
    "keyvalue": _fnCheckKeyvalue,
    "jsonl": _fnCheckJsonl,
}


def _fnDispatchIntegrityCheck(sFullPath, dictStandard):
    """Load and check the file using the appropriate format checker."""
    sFormat = dictStandard.get("sFormat", "")
    fnChecker = _DICT_INTEGRITY_CHECKERS.get(
        sFormat, _fnCheckGenericText,
    )
    fnChecker(sFullPath, dictStandard)


if not _LIST_STANDARDS:
    def test_no_integrity_outputs():
        """Placeholder when no data files are present."""
        assert True
else:
    @pytest.mark.parametrize(
        "dictStandard",
        _LIST_STANDARDS,
        ids=[s["sFileName"] for s in _LIST_STANDARDS],
    )
    def test_integrity_check(dictStandard):
        """Validate structural integrity of a data file."""
        sFullPath = os.path.join(
            _STEP_DIRECTORY, dictStandard["sFileName"],
        )
        _fnCheckFileExists(sFullPath)
        _fnDispatchIntegrityCheck(sFullPath, dictStandard)
'''


_QUALITATIVE_TEST_TEMPLATE = '''"""Qualitative tests generated by vaibify."""

import json
import os
import pathlib

import pytest


def _fdictLoadQualitativeStandards():
    """Load the qualitative standards JSON file."""
    sJsonPath = str(
        pathlib.Path(__file__).parent / "qualitative_standards.json"
    )
    with open(sJsonPath, encoding="utf-8") as fileHandle:
        return json.load(fileHandle)


_DICT_STANDARDS = _fdictLoadQualitativeStandards()
_LIST_STANDARDS = _DICT_STANDARDS["listStandards"]
_STEP_DIRECTORY = str(pathlib.Path(__file__).parent.parent)


def _flistLoadColumns(sFullPath, sFormat):
    """Load column names from a tabular or array file."""
    if sFormat == "npz":
        import numpy as np
        return list(np.load(sFullPath, allow_pickle=False).files)
    if sFormat in ("npy", "hdf5"):
        return []
    if sFormat == "csv":
        import csv
        with open(sFullPath, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            return list(reader.fieldnames or [])
    with open(sFullPath, encoding="utf-8") as fh:
        return fh.readline().split()


def _flistLoadJsonKeys(sFullPath):
    """Load top-level keys from a JSON file."""
    with open(sFullPath, encoding="utf-8") as fh:
        dictData = json.load(fh)
    if isinstance(dictData, dict):
        return list(dictData.keys())
    return []


def _fnCheckExpectedColumns(sFullPath, sFormat, listExpected):
    """Assert all expected columns are present in the file."""
    if not listExpected:
        return
    listActual = _flistLoadColumns(sFullPath, sFormat)
    for sColumn in listExpected:
        assert sColumn in listActual, (
            f"{sFullPath}: missing column {sColumn!r}"
        )


def _fnCheckExpectedJsonKeys(sFullPath, listExpected):
    """Assert all expected JSON keys are present in the file."""
    if not listExpected:
        return
    listActual = _flistLoadJsonKeys(sFullPath)
    for sKey in listExpected:
        assert sKey in listActual, (
            f"{sFullPath}: missing JSON key {sKey!r}"
        )


if not _LIST_STANDARDS:
    def test_no_qualitative_outputs():
        """Placeholder when no qualitative checks are needed."""
        assert True
else:
    @pytest.mark.parametrize(
        "dictStandard",
        _LIST_STANDARDS,
        ids=[s["sFileName"] for s in _LIST_STANDARDS],
    )
    def test_qualitative_check(dictStandard):
        """Validate qualitative properties of a data file."""
        sFullPath = os.path.join(
            _STEP_DIRECTORY, dictStandard["sFileName"],
        )
        sFormat = dictStandard.get("sFormat", "")
        _fnCheckExpectedColumns(
            sFullPath, sFormat,
            dictStandard.get("listExpectedColumns", []),
        )
        _fnCheckExpectedJsonKeys(
            sFullPath,
            dictStandard.get("listExpectedJsonKeys", []),
        )
'''


def fsBuildIntegrityTestCode():
    """Return the deterministic integrity test file content."""
    return _INTEGRITY_TEST_TEMPLATE


def fsBuildQualitativeTestCode():
    """Return the deterministic qualitative test file content."""
    return _QUALITATIVE_TEST_TEMPLATE


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


def fdictGenerateAllTestsDeterministic(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables,
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
        listdictReports, fTolerance,
    )


def _fdictWriteAllDeterministicTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, fTolerance,
):
    """Write all three deterministic test files and return result dict."""
    fnWriteConftestMarker(connectionDocker, sContainerId, sDirectory)
    dictResult = {}
    dictResult["dictIntegrity"] = _fdictWriteIntegrityTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports,
    )
    dictResult["dictQualitative"] = _fdictWriteQualitativeTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports,
    )
    dictResult["dictQuantitative"] = _fdictWriteQuantitativeTests(
        connectionDocker, sContainerId, sDirectory,
        listdictReports, fTolerance,
    )
    return dictResult


def _fdictWriteQuantitativeFiles(
    connectionDocker, sContainerId, sDirectory,
    dictStandards,
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
    listdictReports, fTolerance,
):
    """Build standards from reports and write quantitative test files."""
    dictStandards = _fdictBuildQuantitativeStandards(
        listdictReports, fTolerance,
    )
    return _fdictWriteQuantitativeFiles(
        connectionDocker, sContainerId, sDirectory, dictStandards,
    )


def _fdictWriteIntegrityFiles(
    connectionDocker, sContainerId, sDirectory, dictStandards,
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
    connectionDocker, sContainerId, sDirectory, listdictReports,
):
    """Build standards and write integrity test files."""
    dictStandards = _fdictBuildIntegrityStandards(listdictReports)
    return _fdictWriteIntegrityFiles(
        connectionDocker, sContainerId, sDirectory, dictStandards,
    )


def _fdictWriteQualitativeFiles(
    connectionDocker, sContainerId, sDirectory, dictStandards,
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
    connectionDocker, sContainerId, sDirectory, listdictReports,
):
    """Build standards and write qualitative test files."""
    dictStandards = _fdictBuildQualitativeStandards(listdictReports)
    return _fdictWriteQualitativeFiles(
        connectionDocker, sContainerId, sDirectory, dictStandards,
    )


def fdictGenerateAllTests(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
    sUser=None, bDeterministic=True,
):
    """Generate all three test categories via LLM or deterministically."""
    if bDeterministic:
        return fdictGenerateAllTestsDeterministic(
            connectionDocker, sContainerId, iStepIndex,
            dictWorkflow, dictVariables,
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


def _fsBuildCategoryPrompt(
    sCategory, sDirectory, sDataFiles, sScriptContents, sDataPreviews,
):
    """Build an LLM prompt for a single test category."""
    return (
        f"Generate {sCategory} tests for the step in {sDirectory}.\n"
        f"See CLAUDE.md for instructions.\n"
        f"Output files: {sDataFiles}\n\n"
        f"Source code of analysis scripts:\n{sScriptContents}\n\n"
        f"Data file previews:\n{sDataPreviews}\n\n"
        f"Return ONLY Python code, no explanations."
    )


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


def _fsBuildQuantitativePrompt(
    sDirectory, sDataFiles, sScriptContents, sDataPreviews, fTolerance,
):
    """Build the LLM prompt for quantitative standards generation."""
    return (
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
