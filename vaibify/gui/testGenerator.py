"""Generate pytest unit tests for workflow steps via LLM."""

import asyncio
import posixpath
import re


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
    return _fsPreviewText(connectionDocker, sContainerId, sAbsPath)


def _fsResolvePath(sFilePath, sDirectory):
    """Return absolute path, joining with directory if relative."""
    if posixpath.isabs(sFilePath):
        return sFilePath
    return posixpath.join(sDirectory, sFilePath)


def _fsPreviewNpy(connectionDocker, sContainerId, sAbsPath):
    """Preview a .npy file's shape and dtype."""
    from .pipelineRunner import fsShellQuote

    sCommand = (
        f"python3 -c \"import numpy as np; "
        f"d=np.load({fsShellQuote(sAbsPath)}); "
        f"print(f'shape={{d.shape}} dtype={{d.dtype}}')\""
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"


def _fsPreviewText(connectionDocker, sContainerId, sAbsPath):
    """Preview first 10 lines of a text file."""
    from .pipelineRunner import fsShellQuote
    sCommand = f"head -10 {fsShellQuote(sAbsPath)} 2>/dev/null"
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
    connectionDocker, sContainerId, sPrompt,
):
    """Run claude --print inside the container, return (exitCode, output)."""
    from .pipelineRunner import fsShellQuote

    sCommand = f"CLAUDECODE= claude --print {fsShellQuote(sPrompt)}"
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
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
        r"```(?:python)?\s*\n(.*?)```",
        sStripped, re.DOTALL,
    )
    if matchFenced:
        return matchFenced.group(1).strip()
    return sStripped


def fsTestFilePath(sDirectory, iStepIndex):
    """Return the test file path for a given step."""
    sFilename = f"test_step{iStepIndex + 1:02d}.py"
    return posixpath.join(sDirectory, sFilename)


def _fdictBuildTestResult(
    connectionDocker, sContainerId, sCode, sFilePath,
):
    """Write the test file and return the result dict."""
    connectionDocker.fnWriteFile(
        sContainerId, sFilePath, sCode.encode("utf-8")
    )
    sFilename = posixpath.basename(sFilePath)
    return {
        "sFilePath": sFilePath,
        "sContent": sCode,
        "saTestCommands": [f"pytest {sFilename}"],
    }


def fdictGenerateTest(
    connectionDocker, sContainerId, iStepIndex,
    dictWorkflow, dictVariables, bUseApi=False, sApiKey=None,
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
        connectionDocker, sContainerId, sPrompt, bUseApi, sApiKey
    )
    sCode = fsParseGeneratedCode(sRawOutput)
    sFilePath = fsTestFilePath(sDirectory, iStepIndex)
    return _fdictBuildTestResult(
        connectionDocker, sContainerId, sCode, sFilePath
    )


def _fsInvokeLlm(
    connectionDocker, sContainerId, sPrompt, bUseApi, sApiKey,
):
    """Call the appropriate LLM provider and return raw text."""
    if bUseApi:
        return fsGenerateViaApi(sPrompt, sApiKey)
    iExitCode, sOutput = ftResultGenerateViaClaude(
        connectionDocker, sContainerId, sPrompt
    )
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
