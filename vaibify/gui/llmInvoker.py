"""Invoke LLM providers (Claude CLI or Anthropic API) for test generation."""

__all__ = [
    "fnEnsureClaudeMdInstructions",
    "fbContainerHasClaude",
    "fsReadFileFromContainer",
    "fsBuildPrompt",
    "ftResultGenerateViaClaude",
    "fsGenerateViaApi",
]

import logging
import posixpath

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

## CRITICAL: Protected Files

The following files are monitored by the vaibify dashboard.
Do NOT modify them without explicit user approval:
- tests/conftest.py
- tests/test_quantitative.py
- tests/test_integrity.py
- tests/test_qualitative.py
- .vaibify/generate_standards.py

If you need to modify these files to handle a new data format,
STOP and explain to the user what change is needed and why
before making any edits.

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
- Do not test exact key sets, column counts, or row counts \u2014 those
  belong in integrity tests.
Return ONLY the Python code for a single pytest file. No explanations.

## Quantitative Standards (quantitative_standards.json)

Extract numerical benchmark values into a JSON file (not Python code).
For each significant numerical result visible in the output data:
1. Identify the parameter with a descriptive camelCase name prefixed with "f"
2. Record the value at FULL double precision \u2014 use repr() or f"{value:.17g}"
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
Do NOT modify or delete conftest.py \u2014 vaibify owns this file.

## Test Infrastructure Contract (DO NOT VIOLATE)

The dashboard parses test results by matching specific patterns.
If you modify test files WITH USER APPROVAL, you MUST preserve:

### Required function names:
- `test_quantitative_benchmark` in test_quantitative.py
- Parametrized with `dictStandard` from a `_LIST_STANDARDS` list

### Required JSON schema for quantitative_standards.json:
{"listStandards": [{"sName": str, "sDataFile": str, "sAccessPath": str, "fValue": float, "sUnit": str}], "fDefaultRtol": float}

### Never modify or delete:
- conftest.py (vaibify's test result marker plugin)
- The `# vaibify-template-hash:` comment line

### Where to add custom data format loaders:
- Add new `_fLoad*Value` functions in test_quantitative.py
- Register them in the format dispatch dict
- Do NOT rename `test_quantitative_benchmark` or change its parametrize pattern

### Persistence
After modifying any test infrastructure file with user approval,
commit the changes so they survive container recreation:
git add tests/test_*.py tests/*_standards.json
git commit -m "Extend test infrastructure for [format/edge case]"
"""


_CLAUDE_MD_MARKER = "# Vaibify Test Generation Instructions"
_CLAUDE_MD_VERSION = "v10"
_CLAUDE_MD_VERSION_TAG = "<!-- vaibify-test-instructions-v10 -->"


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
        f"above. Use repr() precision \u2014 never round or guess values.\n"
        f"Return ONLY a JSON object, no explanations."
    )
