"""Workflow-aware archival of outputs to Zenodo.

After a workflow run, identifies new or changed outputs via the
provenance tracker and uploads them to Zenodo through the client.
Generates archive READMEs, checksums, and structured ZIP files.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from vaibify.reproducibility._hashing import fsHashFileSha256
from vaibify.reproducibility.manifestPaths import (
    TUPLE_OUTPUT_KEYS,
    flistStepStandardsRepoPaths,
)
from vaibify.reproducibility.manifestWriter import (
    fbWorkflowArchivesTests,
    flistStepTestFileRepoPaths,
)
from vaibify.reproducibility.provenanceTracker import (
    flistDetectChangedOutputs,
    fnUpdateProvenance,
    fsComputeFileHash,
)
from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)
from vaibify.reproducibility.zenodoClient import (
    ZenodoClient,
    ZenodoError,
)

logger = logging.getLogger("vaibify")


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def fnArchiveOutputs(config, dictWorkflow, sWorkdir):
    """Identify changed outputs and upload them to Zenodo.

    Parameters
    ----------
    config : dict
        Project configuration (must contain "sZenodoService" key).
    dictWorkflow : dict
        Parsed workflow.json with step definitions.
    sWorkdir : str
        Working directory for the workflow.
    """
    dictProvenance = _fdictLoadOrCreateProvenance(sWorkdir)
    listChanged = flistDetectChangedOutputs(dictProvenance, dictWorkflow)
    fnGenerateReproducibilityEnvelope(
        sWorkdir, dictWorkflow,
        sContainerName=config.get("sContainerName"),
        listHostBinaries=config.get("listHostBinaries"),
    )
    if not listChanged:
        return
    fnUploadToZenodo(config, listChanged)
    fnUpdateProvenance(dictProvenance, dictWorkflow, sWorkdir)
    _fnSaveProvenanceFile(dictProvenance, sWorkdir)


def fnGenerateReproducibilityEnvelope(filesRepo, dictWorkflow,
                                      sContainerName=None,
                                      listHostBinaries=None):
    """Write the three-tier AICS Level 3 reproducibility envelope.

    ``filesRepo`` is a project-repo path string (host clone) or a
    repo-file adapter (container). Tier 1 writes ``MANIFEST.sha256``
    at the project repo root via ``manifestWriter.fnWriteManifest``.
    Tier 2 writes ``requirements.lock`` via
    ``dependencyPinning.fnGenerateRequirementsLock``; when ``uv`` is
    missing or the project has no dependency input the failure is
    logged and the other tiers continue. Tier 3 writes
    ``.vaibify/environment.json`` via ``environmentSnapshot`` only when
    ``sContainerName`` is supplied. Each tier's failure is isolated so
    a partial envelope is preferred over no envelope.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    _fnWriteManifestTier(filesRepo, dictWorkflow)
    _fnWriteLockTier(filesRepo)
    _fnWriteEnvironmentTier(
        filesRepo, sContainerName, listHostBinaries,
    )


def _fnWriteManifestTier(filesRepo, dictWorkflow):
    """Write MANIFEST.sha256 (Tier 1); log and swallow any failure."""
    from vaibify.reproducibility import manifestWriter
    try:
        manifestWriter.fnWriteManifest(filesRepo, dictWorkflow)
    except (OSError, ValueError) as error:
        logger.warning(
            "Reproducibility envelope: MANIFEST.sha256 write "
            "failed for '%s': %s", fsRepoRootOf(filesRepo), error,
        )


def _fnWriteLockTier(filesRepo):
    """Write requirements.lock (Tier 2); log and swallow any failure."""
    from vaibify.reproducibility import dependencyPinning
    try:
        dependencyPinning.fnGenerateRequirementsLock(filesRepo)
    except FileNotFoundError as error:
        logger.warning(
            "Reproducibility envelope: requirements.lock skipped "
            "for '%s': %s", fsRepoRootOf(filesRepo), error,
        )
    except subprocess.CalledProcessError as error:
        logger.warning(
            "Reproducibility envelope: uv compile failed for "
            "'%s' (exit %s): %s",
            fsRepoRootOf(filesRepo), error.returncode, error.stderr,
        )


def _fnWriteEnvironmentTier(filesRepo, sContainerName,
                             listHostBinaries):
    """Write .vaibify/environment.json (Tier 3); skip when container absent."""
    if not sContainerName:
        return
    from vaibify.reproducibility import environmentSnapshot
    try:
        dictEnvironment = _fdictBuildEnvironmentPayload(
            filesRepo, sContainerName, listHostBinaries,
        )
        environmentSnapshot.fnWriteEnvironmentJson(
            filesRepo, dictEnvironment,
        )
    except (FileNotFoundError, OSError,
            subprocess.CalledProcessError) as error:
        logger.warning(
            "Reproducibility envelope: environment.json failed "
            "for '%s': %s", fsRepoRootOf(filesRepo), error,
        )


def _fdictBuildEnvironmentPayload(filesRepo, sContainerName,
                                  listHostBinaries):
    """Assemble the environment.json payload from snapshot helpers."""
    from vaibify.reproducibility import environmentSnapshot
    dictPayload = {
        "dictContainer": environmentSnapshot.
            fdictCaptureContainerImageDigest(sContainerName),
        "dictSystemTools": environmentSnapshot.
            fdictCaptureSystemTools(filesRepo),
    }
    if listHostBinaries:
        dictPayload["dictHostBinaries"] = environmentSnapshot.\
            fdictCaptureHostBinaryHashes(filesRepo, listHostBinaries)
    return dictPayload


def flistCollectArchiveFilePaths(dictWorkflow, sWorkdir):
    """Return absolute paths of every artefact the archive should cover.

    Spans every declared output (``saOutputFiles``, ``saPlotFiles``,
    ``saDataFiles``) plus — unless the workflow opts out via
    ``bArchiveTests`` (default True) — each step's declared test files
    and test standards. Enumeration is pure path logic; existence is
    the caller's concern so a missing file surfaces as a divergence
    downstream, never as a silent exclusion here.
    """
    bArchiveTests = fbWorkflowArchivesTests(dictWorkflow)
    listAbsolutePaths = []
    for dictStep in dictWorkflow.get("listSteps", []):
        for sDeclared in _flistStepArchiveDeclaredPaths(
            dictStep, bArchiveTests,
        ):
            listAbsolutePaths.append(
                str(_fpathResolveOutput(sDeclared, sWorkdir))
            )
    return listAbsolutePaths


def _flistStepArchiveDeclaredPaths(dictStep, bArchiveTests):
    """Return one step's declared archive-relevant paths."""
    listDeclared = []
    for sKey in TUPLE_OUTPUT_KEYS:
        listDeclared.extend(dictStep.get(sKey, []) or [])
    if bArchiveTests:
        listDeclared.extend(flistStepTestFileRepoPaths(dictStep))
        listDeclared.extend(flistStepStandardsRepoPaths(dictStep))
    return listDeclared


def fdictCollectOutputFiles(dictWorkflow, sWorkdir):
    """Collect archivable file paths from workflow.json steps.

    Covers every declared output plus, by default, each step's test
    files and test standards (see ``flistCollectArchiveFilePaths``).

    Parameters
    ----------
    dictWorkflow : dict
        Parsed workflow.json with step definitions.
    sWorkdir : str
        Working directory (used for resolving relative paths).

    Returns
    -------
    dict
        Mapping of file path to SHA-256 hash for every existing
        archivable file.
    """
    dictOutputs = {}
    for sPath in flistCollectArchiveFilePaths(dictWorkflow, sWorkdir):
        if Path(sPath).is_file():
            dictOutputs[sPath] = fsComputeFileHash(sPath)
    return dictOutputs


def fnUploadToZenodo(config, listFilePaths):
    """Create a Zenodo deposit, upload files, and publish.

    Parameters
    ----------
    config : dict
        Must contain "sZenodoService" (e.g. "sandbox" or "zenodo").
    listFilePaths : list of str
        Absolute paths to files to upload.
    """
    sService = config.get("sZenodoService", "sandbox")
    clientZenodo = ZenodoClient(sService=sService)
    dictDraft = clientZenodo.fdictCreateDraft()
    iDepositId = dictDraft["id"]
    try:
        _fnUploadAllFiles(clientZenodo, iDepositId, listFilePaths)
        clientZenodo.fnPublishDraft(iDepositId)
    except ZenodoError:
        _fnCleanupFailedDraft(clientZenodo, iDepositId)
        raise


def fsRecordDoi(dictProvenance, sDoi):
    """Store a DOI string in the provenance metadata.

    Parameters
    ----------
    dictProvenance : dict
        Provenance dictionary to update in place.
    sDoi : str
        DOI string to record.

    Returns
    -------
    str
        The stored DOI (echoed back for convenience).
    """
    dictProvenance["sDoi"] = sDoi
    return sDoi


# ------------------------------------------------------------------
# Archive generation
# ------------------------------------------------------------------


def _flistReadmeStepLines(dictWorkflow):
    """Return the numbered step lines for the archive README body."""
    listLines = []
    for iStep, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        sName = dictStep.get("sName", f"Step {iStep + 1}")
        listLines.append(f"{iStep + 1}. {sName}")
    return listLines


def _flistReadmeHeaderLines(sTitle, sTimestamp):
    """Return the static header (title + contents) lines for the README."""
    return [
        f"# {sTitle}",
        "",
        f"Generated by Vaibify on {sTimestamp}.",
        "",
        "## Contents",
        "",
        "- `config/` — Pipeline workflow definition",
        "- `data/` — Data analysis outputs by step",
        "- `figures/` — Archive-quality publication figures",
        "",
        "## Pipeline Steps",
        "",
    ]


def _flistReadmeFooterLines():
    """Return the static reproduction-instruction lines for the README."""
    return [
        "",
        "## Reproduction",
        "",
        "Install vaibify and run:",
        "```",
        "python director.py --config config/workflow.json",
        "```",
    ]


def fsGenerateArchiveReadme(dictWorkflow):
    """Generate a README.md for a Zenodo archive."""
    sTitle = dictWorkflow.get("sProjectTitle",
        dictWorkflow.get("sWorkflowName", "Vaibify Workflow"))
    sTimestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")
    listLines = _flistReadmeHeaderLines(sTitle, sTimestamp)
    listLines.extend(_flistReadmeStepLines(dictWorkflow))
    listLines.extend(_flistReadmeFooterLines())
    return "\n".join(listLines)


def fsGenerateChecksums(listFilePaths):
    """Generate SHA256 checksums for a list of files."""
    listLines = []
    for sPath in sorted(listFilePaths):
        if not os.path.isfile(sPath):
            continue
        sHash = fsHashFileSha256(sPath)
        sRelative = os.path.basename(sPath)
        listLines.append(f"{sHash}  {sRelative}")
    return "\n".join(listLines) + "\n"


def fdictBuildZenodoMetadata(dictWorkflow):
    """Build Zenodo metadata from workflow fields."""
    sTitle = dictWorkflow.get("sProjectTitle",
        dictWorkflow.get("sWorkflowName", "Dataset"))
    listCreators = dictWorkflow.get("listCreators", [])
    if not listCreators:
        listCreators = [{"name": "Vaibify User"}]
    return {
        "title": f"Data for: {sTitle}",
        "upload_type": "dataset",
        "description": fsGenerateArchiveReadme(dictWorkflow),
        "creators": listCreators,
        "license": dictWorkflow.get("sLicense", "CC-BY-4.0"),
        "keywords": dictWorkflow.get("listKeywords", []),
    }


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _fdictLoadOrCreateProvenance(sWorkdir):
    """Load existing provenance or return a fresh empty dict."""
    pathProvenance = Path(sWorkdir) / ".provenance.json"
    if pathProvenance.is_file():
        from vaibify.reproducibility.provenanceTracker import (
            fdictLoadProvenance,
        )
        return fdictLoadProvenance(str(pathProvenance))
    return {"saSteps": [], "dictFileHashes": {}, "sTimestamp": ""}


def _fnSaveProvenanceFile(dictProvenance, sWorkdir):
    """Save provenance data to the standard location."""
    from vaibify.reproducibility.provenanceTracker import (
        fnSaveProvenance,
    )
    sPath = str(Path(sWorkdir) / ".provenance.json")
    fnSaveProvenance(dictProvenance, sPath)


def _fnCollectStepOutputs(dictStep, sWorkdir, dictOutputs):
    """Hash each output file in a step and add to dictOutputs."""
    for sOutputPath in dictStep.get("saPlotFiles", []):
        pathFile = _fpathResolveOutput(sOutputPath, sWorkdir)
        if pathFile.is_file():
            dictOutputs[str(pathFile)] = fsComputeFileHash(
                str(pathFile)
            )


def _fpathResolveOutput(sOutputPath, sWorkdir):
    """Resolve an output path, making it absolute if necessary."""
    pathOutput = Path(sOutputPath)
    if not pathOutput.is_absolute():
        pathOutput = Path(sWorkdir) / pathOutput
    return pathOutput


def _fnUploadAllFiles(clientZenodo, iDepositId, listFilePaths):
    """Upload every file in the list to the given deposit."""
    for sFilePath in listFilePaths:
        clientZenodo.fnUploadFile(iDepositId, sFilePath)


def _fnCleanupFailedDraft(clientZenodo, iDepositId):
    """Attempt to delete a draft that failed during upload."""
    try:
        clientZenodo.fnDeleteDraft(iDepositId)
    except ZenodoError:
        pass
