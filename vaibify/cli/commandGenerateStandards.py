"""CLI subcommand: vaibify generate-standards.

Refreshes a step's ``tests/quantitative_standards.json`` from live data
files. Two modes:

* ``--step-dir <path>`` — operates on a step directory directly. If the
  step already has a curated standards file, only the ``fValue`` of each
  entry is recomputed (schema preserved). If no standards file exists,
  one is generated from the data files inferred under the directory.
* ``--workflow <json> --step-label <A09|I01>`` — resolves the step from
  a workflow's JSON file. Convenient counterpart to ``vaibify-do
  run-unit-tests <sLabel>``.

The optional ``--detect-stochastic`` flag scans each ``data*.py`` script
in the step directory before generating, warning when an unseeded RNG
source is found.
"""

import os
import sys

import click

from vaibify.testing.standards import (
    fdictGenerateQuantitativeStandards,
    fnGenerateFromWorkflow,
    fnRegenerateStandardsFile,
    fnWriteStandards,
)
from vaibify.testing.stochasticDetector import (
    ftDetectStochastic,
    fnPrintReport,
)


def _fnReportStochasticInDir(sStepDir):
    """Print a stochastic-detection report for every data*.py in the step."""
    import glob
    for sScriptPath in sorted(glob.glob(os.path.join(sStepDir, "data*.py"))):
        bStoch, listSrc, listSeeds = ftDetectStochastic(sScriptPath)
        fnPrintReport(sScriptPath, bStoch, listSrc, listSeeds)


def _fnRefreshOrGenerateForStepDir(sStepDir, fRtol):
    """Regenerate values in an existing standards file, or build from scratch."""
    sStandardsPath = os.path.join(sStepDir, "tests", "quantitative_standards.json")
    if os.path.isfile(sStandardsPath):
        click.echo(f"Refreshing fValue entries in {sStandardsPath}")
        fnRegenerateStandardsFile(sStandardsPath, sStepDir)
        return
    click.echo(
        f"No standards file at {sStandardsPath}; "
        "generating one from data files inferred under the step directory."
    )
    listDataFiles = _flistDiscoverDataFiles(sStepDir)
    if not listDataFiles:
        click.echo(
            "Error: no data files found under step directory. "
            "Pass --workflow + --step-label to use saDataFiles from the workflow."
        )
        sys.exit(2)
    dictStandards = fdictGenerateQuantitativeStandards(
        sStepDir, listDataFiles, fDefaultRtol=fRtol)
    fnWriteStandards(dictStandards, sStandardsPath)


def _flistDiscoverDataFiles(sStepDir):
    """Discover candidate data files under a step directory.

    Looks for .npy / .npz / .json / .csv / .txt / .dat files at the top
    level and one subdirectory deep, excluding test scaffolding and
    figure outputs.
    """
    listFound = []
    listExcludeDirs = {"tests", "Plot", "__pycache__"}
    listExtensions = (".npy", ".npz", ".json", ".csv", ".txt", ".dat")
    for sName in sorted(os.listdir(sStepDir)):
        sFullPath = os.path.join(sStepDir, sName)
        if os.path.isfile(sFullPath) and sName.endswith(listExtensions):
            listFound.append(sName)
    for sName in sorted(os.listdir(sStepDir)):
        sFullPath = os.path.join(sStepDir, sName)
        if os.path.isdir(sFullPath) and sName not in listExcludeDirs:
            for sSub in sorted(os.listdir(sFullPath)):
                if sSub.endswith(listExtensions):
                    listFound.append(os.path.join(sName, sSub))
    return listFound


def _fiResolveStepIndexFromLabel(sWorkflowPath, sStepLabel):
    """Return the 0-based listSteps index for an A##/I## label."""
    import json
    from vaibify.gui.pipelineUtils import fiStepIndexFromLabel
    with open(sWorkflowPath) as fileHandle:
        dictWorkflow = json.load(fileHandle)
    try:
        return fiStepIndexFromLabel(dictWorkflow, sStepLabel)
    except (KeyError, ValueError) as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(2)


@click.command("generate-standards")
@click.option(
    "--step-dir", "sStepDir", default=None, type=click.Path(exists=True),
    help="Path to a step directory containing tests/ and data files.",
)
@click.option(
    "--workflow", "sWorkflowPath", default=None,
    type=click.Path(exists=True),
    help="Path to a workflow JSON (alternative to --step-dir).",
)
@click.option(
    "--step-label", "sStepLabel", default=None,
    help="Step label like A09 or I01 (use with --workflow).",
)
@click.option(
    "--rtol", "fRtol", default=1e-6, type=float,
    help="Default fDefaultRtol when generating a fresh standards file.",
)
@click.option(
    "--detect-stochastic", "bDetectStochastic", is_flag=True, default=False,
    help="Scan the step's data*.py scripts for unseeded RNG before generating.",
)
def generate_standards(
    sStepDir, sWorkflowPath, sStepLabel, fRtol, bDetectStochastic,
):
    """Refresh or generate a step's quantitative_standards.json from live data."""
    if sStepDir is None and not (sWorkflowPath and sStepLabel):
        click.echo(
            "Error: provide either --step-dir or "
            "--workflow + --step-label.", err=True,
        )
        sys.exit(2)
    if sStepDir is not None:
        if bDetectStochastic:
            _fnReportStochasticInDir(sStepDir)
        _fnRefreshOrGenerateForStepDir(sStepDir, fRtol)
        return
    iStepIndex = _fiResolveStepIndexFromLabel(sWorkflowPath, sStepLabel)
    fnGenerateFromWorkflow(
        sWorkflowPath, iStepIndex,
        fDefaultRtol=fRtol, bCheckSeeds=bDetectStochastic,
    )
