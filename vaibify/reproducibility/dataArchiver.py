"""Workflow-aware archival of outputs to Zenodo.

After a workflow run, identifies new or changed outputs via the
provenance tracker and uploads them to Zenodo through the client.
"""

from pathlib import Path

from vaibify.reproducibility.provenanceTracker import (
    flistDetectChangedOutputs,
    fnUpdateProvenance,
    fsComputeFileHash,
)
from vaibify.reproducibility.zenodoClient import (
    ZenodoClient,
    ZenodoError,
)


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
    if not listChanged:
        return
    fnUploadToZenodo(config, listChanged)
    fnUpdateProvenance(dictProvenance, dictWorkflow, sWorkdir)
    _fnSaveProvenanceFile(dictProvenance, sWorkdir)


def fdictCollectOutputFiles(dictWorkflow, sWorkdir):
    """Collect all output file paths from workflow.json steps.

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
        output file.
    """
    dictOutputs = {}
    for dictStep in dictWorkflow.get("listSteps", []):
        _fnCollectStepOutputs(dictStep, sWorkdir, dictOutputs)
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
