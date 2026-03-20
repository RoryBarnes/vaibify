#!/usr/bin/env python3
"""
Vaibify pipeline director — execute workflow steps inside a container.

Reads a workflow JSON file defining a sequence of steps. Each step has
a working directory, data analysis commands (heavy computation),
plot commands (visualization), and expected output files. Output files from
earlier steps are available as {StepNN.stem} variables in later steps.

Usage:
    python director.py --config workflow.json
    python director.py --config workflow.json --verify-only
    python director.py --config workflow.json --start-step 3
"""

import argparse
import io
import json
import multiprocessing
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Log file tee
# ---------------------------------------------------------------------------


class TeeWriter(io.TextIOBase):
    """Write to both a terminal stream and a log file simultaneously."""

    def __init__(self, streamTerminal, fileLog):
        self._streamTerminal = streamTerminal
        self._fileLog = fileLog

    def write(self, sText):
        self._streamTerminal.write(sText)
        self._fileLog.write(sText)
        self._fileLog.flush()
        return len(sText)

    def flush(self):
        self._streamTerminal.flush()
        self._fileLog.flush()


def fnSetupLogFile(sLogPath):
    """Redirect stdout and stderr to both the terminal and a log file."""
    os.makedirs(os.path.dirname(sLogPath), exist_ok=True)
    fileLog = open(sLogPath, "w")
    sys.stdout = TeeWriter(sys.__stdout__, fileLog)
    sys.stderr = TeeWriter(sys.__stderr__, fileLog)
    return fileLog


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------


def fsResolveVariables(sTemplate, dictVariables):
    """Replace {name} tokens in sTemplate with values from dictVariables."""

    def fnReplace(match):
        sToken = match.group(1)
        if sToken in dictVariables:
            return str(dictVariables[sToken])
        raise KeyError(f"Unresolved variable: {{{sToken}}}")

    return re.sub(r"\{([^}]+)\}", fnReplace, sTemplate)


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def fdictLoadWorkflow(sWorkflowPath):
    """Load and validate sWorkflowPath, returning the parsed dictionary."""
    if not os.path.isfile(sWorkflowPath):
        print(f"ERROR: Workflow file not found: {sWorkflowPath}")
        sys.exit(1)
    with open(sWorkflowPath, "r") as fileHandle:
        dictWorkflow = json.load(fileHandle)
    if not fbValidateWorkflow(dictWorkflow):
        print("ERROR: Invalid workflow file.")
        sys.exit(1)
    return dictWorkflow


def fbValidateWorkflow(dictWorkflow):
    """Return True when all required keys and step structures are valid."""
    if "listSteps" not in dictWorkflow:
        print("Missing required key: listSteps")
        return False
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        sLabel = f"Step{iIndex + 1:02d}"
        for sField in ("sName", "sDirectory", "saPlotCommands", "saPlotFiles"):
            if sField not in dictStep:
                print(f"{sLabel}: missing required field '{sField}'")
                return False
    return True


# ---------------------------------------------------------------------------
# Global variable construction
# ---------------------------------------------------------------------------


def fiResolveCoreCount(iRequested):
    """Return a usable core count from the requested value (-1 = auto)."""
    iTotal = multiprocessing.cpu_count()
    if iRequested == -1:
        return max(1, iTotal - 1)
    return min(iRequested, iTotal)


def fdictBuildGlobalVariables(dictWorkflow, sWorkflowRoot):
    """Extract top-level workflow keys into a variables dictionary."""
    sPlotDirectory = os.path.join(
        sWorkflowRoot, dictWorkflow.get("sPlotDirectory", "Plot")
    )
    os.makedirs(sPlotDirectory, exist_ok=True)
    return {
        "sPlotDirectory": sPlotDirectory,
        "sRepoRoot": sWorkflowRoot,
        "iNumberOfCores": fiResolveCoreCount(
            dictWorkflow.get("iNumberOfCores", -1)),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf").lower(),
    }


# ---------------------------------------------------------------------------
# Executable name extraction
# ---------------------------------------------------------------------------


def fsExtractExecutableName(sCommand):
    """Extract a display name for the executable from a command string."""
    listTokens = sCommand.split()
    if not listTokens:
        return "unknown"
    sFirst = os.path.basename(listTokens[0])
    if sFirst == "python" and len(listTokens) > 1:
        return os.path.basename(listTokens[1])
    if "&&" in listTokens:
        iIndex = listTokens.index("&&")
        if iIndex + 1 < len(listTokens):
            return os.path.basename(listTokens[iIndex + 1])
    return sFirst


# ---------------------------------------------------------------------------
# Command execution with prefixed logging
# ---------------------------------------------------------------------------


def fnStreamPrefixedOutput(stream, sPrefix):
    """Read stream line-by-line and print each line with sPrefix."""
    for sLine in stream:
        print(f"{sPrefix} {sLine}", end="", flush=True)
    stream.close()


def fnStreamAndWait(process, sPrefix):
    """Spawn reader threads for stdout/stderr and wait for completion."""
    threadOut = threading.Thread(
        target=fnStreamPrefixedOutput, args=(process.stdout, sPrefix))
    threadErr = threading.Thread(
        target=fnStreamPrefixedOutput, args=(process.stderr, sPrefix))
    threadOut.start()
    threadErr.start()
    threadOut.join()
    threadErr.join()
    process.wait()


def fnExecuteCommand(sCommand, sWorkingDirectory, sStepName):
    """Run sCommand via shell, streaming prefixed output."""
    if not os.path.isdir(sWorkingDirectory):
        raise FileNotFoundError(
            f"Working directory does not exist: {sWorkingDirectory}")
    sExecutable = fsExtractExecutableName(sCommand)
    sPrefix = f"[{sStepName}][{sExecutable}]"
    print(f"  Running: {sCommand}")

    dictEnv = os.environ.copy()
    dictEnv["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        sCommand, shell=True, cwd=sWorkingDirectory,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=dictEnv,
    )
    fnStreamAndWait(process, sPrefix)
    if process.returncode != 0:
        raise RuntimeError(
            f"Exit code {process.returncode}: {sCommand}")


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------


def _fnRunTestsIfPresent(dictStep, dictVariables, sAbsDirectory):
    """Run test commands, printing results without aborting on failure."""
    for sCommand in dictStep.get("saTestCommands", []):
        sResolved = fsResolveVariables(sCommand, dictVariables)
        try:
            fnExecuteCommand(
                sResolved, sAbsDirectory, dictStep["sName"]
            )
        except RuntimeError as error:
            print(f"  TEST FAILED: {error}")
            return False
    if dictStep.get("saTestCommands"):
        print("  TESTS PASSED")
    return True


def fnExecuteStep(dictStep, dictVariables, sWorkflowRoot):
    """Execute all commands in a step, respecting bPlotOnly."""
    sDirectory = fsResolveVariables(
        dictStep["sDirectory"], dictVariables)
    sAbsDirectory = os.path.join(sWorkflowRoot, sDirectory)
    bPlotOnly = dictStep.get("bPlotOnly", True)

    if not bPlotOnly:
        for sCommand in dictStep.get("saDataCommands", []):
            sResolved = fsResolveVariables(sCommand, dictVariables)
            fnExecuteCommand(sResolved, sAbsDirectory, dictStep["sName"])

    _fnRunTestsIfPresent(dictStep, dictVariables, sAbsDirectory)

    for sCommand in dictStep["saPlotCommands"]:
        sResolved = fsResolveVariables(sCommand, dictVariables)
        fnExecuteCommand(sResolved, sAbsDirectory, dictStep["sName"])


def fsResolveOutputPath(sOutputFile, dictVariables, sAbsDirectory):
    """Resolve an output file spec to an absolute path."""
    sResolvedPath = fsResolveVariables(sOutputFile, dictVariables)
    if os.path.isabs(sResolvedPath):
        return sResolvedPath
    return os.path.join(sAbsDirectory, sResolvedPath)


def _fnRegisterFiles(listFiles, dictVariables, sStepLabel, sAbsDirectory):
    """Verify files exist and register them as {sStepLabel.stem} variables."""
    for sOutputFile in listFiles:
        sAbsPath = fsResolveOutputPath(
            sOutputFile, dictVariables, sAbsDirectory)
        if not os.path.exists(sAbsPath):
            raise FileNotFoundError(
                f"{sStepLabel} expected output not found: {sAbsPath}")
        iFileSize = os.path.getsize(sAbsPath)
        if iFileSize < 1024:
            print(f"  WARNING: {sAbsPath} is only {iFileSize} bytes")
        sStem = os.path.splitext(os.path.basename(sAbsPath))[0]
        sKey = f"{sStepLabel}.{sStem}"
        dictVariables[sKey] = sAbsPath
        print(f"  Registered: {{{sKey}}} -> {sAbsPath}")


def fnRegisterStepOutputs(dictStep, dictVariables, sStepLabel, sWorkflowRoot):
    """Verify output files exist and register them as variables."""
    sDirectory = fsResolveVariables(
        dictStep["sDirectory"], dictVariables)
    sAbsDirectory = os.path.join(sWorkflowRoot, sDirectory)

    _fnRegisterFiles(
        dictStep.get("saDataFiles", []),
        dictVariables, sStepLabel, sAbsDirectory)
    _fnRegisterFiles(
        dictStep["saPlotFiles"],
        dictVariables, sStepLabel, sAbsDirectory)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def fnPrintStepBanner(sStepLabel, dictStep, dictVariables):
    """Print a visual separator with step metadata."""
    print(f"\n{'=' * 60}")
    print(f"{sStepLabel}: {dictStep['sName']}")
    print(f"  bPlotOnly: {dictStep.get('bPlotOnly', True)}"
          f" | sFigureType: {dictVariables['sFigureType']}"
          f" | sDirectory: {dictStep['sDirectory']}")
    print(f"{'=' * 60}")


def fnPrintSummary(listResults):
    """Print a table summarising the outcome of each step."""
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    iPass = sum(1 for _, _, bOk, _ in listResults if bOk)
    iFail = len(listResults) - iPass
    for sLabel, sName, bSuccess, sError in listResults:
        sStatus = "PASS" if bSuccess else "FAIL"
        sSuffix = f"  {sError[:50]}" if sError else ""
        print(f"  {sLabel} {sName:40s}  {sStatus:4s}{sSuffix}")
    print(f"\nTotal: {iPass} passed, {iFail} failed out of {len(listResults)}")


# ---------------------------------------------------------------------------
# Pipeline runners
# ---------------------------------------------------------------------------


def fnRunVerifyOnly(dictWorkflow, dictVariables, sWorkflowRoot):
    """Check that all expected output files exist without executing."""
    listResults = []
    for iStep, dictStep in enumerate(dictWorkflow["listSteps"]):
        if not dictStep.get("bEnabled", True):
            continue
        sLabel = f"Step{iStep + 1:02d}"
        try:
            fnRegisterStepOutputs(
                dictStep, dictVariables, sLabel, sWorkflowRoot
            )
            listResults.append((sLabel, dictStep["sName"], True, ""))
        except FileNotFoundError as error:
            listResults.append(
                (sLabel, dictStep["sName"], False, str(error)))
    fnPrintSummary(listResults)
    return all(bOk for _, _, bOk, _ in listResults)


def _fbSkipAndRegisterStep(
    dictStep, dictVariables, sLabel, sWorkflowRoot, listResults,
):
    """Register outputs for a skipped step, returning True on success."""
    try:
        fnRegisterStepOutputs(
            dictStep, dictVariables, sLabel, sWorkflowRoot
        )
        print(f"  SKIPPED (--start-step): {sLabel} {dictStep['sName']}")
        listResults.append((sLabel, dictStep["sName"], True, ""))
        return True
    except FileNotFoundError as error:
        print(f"  FAILED: {sLabel} — outputs missing "
              f"for skipped step: {error}")
        listResults.append(
            (sLabel, dictStep["sName"], False, str(error)))
        return False


def _fbExecuteOneStep(
    dictStep, dictVariables, sLabel, sWorkflowRoot, listResults,
):
    """Execute a single step and record its result."""
    fnPrintStepBanner(sLabel, dictStep, dictVariables)
    try:
        fnExecuteStep(dictStep, dictVariables, sWorkflowRoot)
        fnRegisterStepOutputs(
            dictStep, dictVariables, sLabel, sWorkflowRoot
        )
        listResults.append((sLabel, dictStep["sName"], True, ""))
        print(f"  SUCCESS: {sLabel}")
        return True
    except Exception as error:
        listResults.append(
            (sLabel, dictStep["sName"], False, str(error)))
        print(f"  FAILED: {sLabel} \u2014 {error}")
        return False


def fnRunPipeline(dictWorkflow, dictVariables, sWorkflowRoot, iStartStep=1):
    """Execute all enabled steps, halting on first failure."""
    listResults = []
    for iStep, dictStep in enumerate(dictWorkflow["listSteps"]):
        if not dictStep.get("bEnabled", True):
            continue
        iStepNumber = iStep + 1
        sLabel = f"Step{iStepNumber:02d}"
        dictVariables["sFigureType"] = dictStep.get(
            "sFigureType",
            dictWorkflow.get("sFigureType", "pdf"),
        ).lower()

        if iStepNumber < iStartStep:
            if not _fbSkipAndRegisterStep(
                dictStep, dictVariables, sLabel, sWorkflowRoot,
                listResults,
            ):
                fnPrintSummary(listResults)
                return False
            continue

        if not _fbExecuteOneStep(
            dictStep, dictVariables, sLabel, sWorkflowRoot,
            listResults,
        ):
            fnPrintSummary(listResults)
            return False
    fnPrintSummary(listResults)
    return all(bOk for _, _, bOk, _ in listResults)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def fsGenerateLogPath(sLogDir, sWorkflowName):
    """Return a timestamped log file path."""
    sTimestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sCleanName = re.sub(r"[^a-zA-Z0-9_-]", "_", sWorkflowName)
    return os.path.join(sLogDir, f"{sCleanName}_{sTimestamp}.log")


def fnConfigureEnvironment(dictWorkflow, sWorkflowRoot):
    """Set PATH from workflow configuration."""
    sUserBinDir = os.path.join(os.path.expanduser("~"), ".local", "bin")
    sVplanetDir = dictWorkflow.get("sVplanetBinaryDirectory", "")
    listPrependPaths = [
        sDir for sDir in [sVplanetDir, sUserBinDir]
        if sDir and os.path.isdir(sDir)
    ]
    if listPrependPaths:
        sExistingPath = os.environ.get("PATH", "")
        os.environ["PATH"] = ":".join(
            listPrependPaths + [sExistingPath]
        )


def fnDownloadDatasets(dictWorkflow, sWorkflowRoot):
    """Download missing datasets from Zenodo before running."""
    listDatasets = dictWorkflow.get("listDatasets", [])
    if not listDatasets:
        return
    for dictDataset in listDatasets:
        sDoi = dictDataset.get("sDoi", "")
        sFileName = dictDataset.get("sFileName", "")
        sDestination = dictDataset.get("sDestination", "")
        if not sDoi or not sFileName:
            continue
        sDestPath = os.path.join(sWorkflowRoot, sDestination, sFileName)
        if os.path.isfile(sDestPath):
            print(f"  Dataset exists: {sDestPath}")
            continue
        print(f"  Downloading: {sFileName} from {sDoi}")
        _fnDownloadFromZenodo(sDoi, sFileName, sDestPath)


def _fnDownloadFromZenodo(sDoi, sFileName, sDestPath):
    """Download a single file from a Zenodo deposit by DOI."""
    try:
        import requests
        sRecordId = sDoi.split(".")[-1]
        sApiUrl = (
            f"https://zenodo.org/api/records/{sRecordId}"
        )
        dictRecord = requests.get(sApiUrl, timeout=30).json()
        for dictFile in dictRecord.get("files", []):
            if dictFile.get("key") == sFileName:
                sFileUrl = dictFile["links"]["self"]
                os.makedirs(
                    os.path.dirname(sDestPath), exist_ok=True)
                response = requests.get(
                    sFileUrl, stream=True, timeout=300)
                response.raise_for_status()
                with open(sDestPath, "wb") as fileHandle:
                    for baChunk in response.iter_content(65536):
                        fileHandle.write(baChunk)
                print(f"  Downloaded: {sDestPath}")
                return
        print(f"  WARNING: {sFileName} not found in {sDoi}")
    except Exception as error:
        print(f"  WARNING: Download failed: {error}")


def fnsParseArguments():
    """Parse and return command-line arguments as a namespace."""
    parser = argparse.ArgumentParser(
        description="Vaibify pipeline director.")
    parser.add_argument(
        "--config", required=True,
        help="Path to the workflow JSON file.")
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Only verify that all expected output files exist.")
    parser.add_argument(
        "--start-step", type=int, default=1, metavar="N",
        help="Skip execution of steps before N (1-based).")
    parser.add_argument(
        "--log-dir",
        default=os.path.join("/workspace", ".vaibify", "logs"),
        help="Directory for log files.")
    return parser.parse_args()


def main():
    """Parse arguments and run the pipeline."""
    args = fnsParseArguments()
    sWorkflowPath = os.path.abspath(args.config)
    sWorkflowRoot = os.path.dirname(sWorkflowPath)
    dictWorkflow = fdictLoadWorkflow(sWorkflowPath)

    sWorkflowName = dictWorkflow.get("sWorkflowName", "pipeline")
    sLogPath = fsGenerateLogPath(args.log_dir, sWorkflowName)
    fileLog = fnSetupLogFile(sLogPath)

    print(f"Vaibify Director - {sWorkflowName}")
    print(f"Workflow: {sWorkflowPath}")
    print(f"Log: {sLogPath}\n")

    fnConfigureEnvironment(dictWorkflow, sWorkflowRoot)
    fnDownloadDatasets(dictWorkflow, sWorkflowRoot)
    dictVariables = fdictBuildGlobalVariables(dictWorkflow, sWorkflowRoot)

    if args.verify_only:
        bSuccess = fnRunVerifyOnly(
            dictWorkflow, dictVariables, sWorkflowRoot)
    else:
        bSuccess = fnRunPipeline(
            dictWorkflow, dictVariables, sWorkflowRoot,
            args.start_step)
    fileLog.close()
    sys.exit(0 if bSuccess else 1)


if __name__ == "__main__":
    main()
