"""Canonical git-tracked set for a vaibify workspace.

A vaibify workspace is a git repo; session state (workflow.json, test
markers, archive plots, supporting plots, scripts) round-trips through
git so that ``git clone`` restores the dashboard. This module defines
what round-trips and what stays local.

Tracked:
- ``.vaibify/workflows/*.json``  workflow definitions
- ``.vaibify/test_markers/*.json``  verification + hashes (survives clone)
- ``.vaibify/zenodo-refs.json``  pointers to externalized large data
- Step scripts referenced by saDataCommands / saPlotCommands
- Step outputs (plot + data files, archive and supporting) under the
  size threshold
- Test standards referenced by dictTests[*].sStandardsPath
- Workspace-root config: requirements.txt, environment.yml, Dockerfile,
  pyproject.toml

Ignored:
- ``.vaibify/logs/``                ephemeral pipeline output
- ``.vaibify/pipeline_state.json``  volatile runtime state
- ``.vaibify/overleaf-mirrors/``    reconstructible local cache
- ``.vaibify/mtime_cache.json``     local-only staleness cache (Phase 2)
- Files above ``I_LARGE_FILE_THRESHOLD_BYTES``  (hand off to Zenodo)
- Files listed under a step's ``dictExcludedFiles`` (Phase 4 tri-state star)

Paths flowing in and out of this module are repo-root-relative posix
strings. Host resolution (joining against a filesystem root) is the
caller's concern.
"""

import os
import posixpath

from . import workflowManager
from vaibify.reproducibility import manifestPaths

__all__ = [
    "I_LARGE_FILE_THRESHOLD_BYTES",
    "S_CONTAINER_WORKSPACE_PREFIX",
    "S_VAIBIFY_WORKFLOWS_GLOB",
    "S_VAIBIFY_MARKERS_GLOB",
    "S_VAIBIFY_ZENODO_REFS",
    "TUPLE_ROOT_CONFIG_FILES",
    "TUPLE_ALWAYS_IGNORED",
    "fsToRepoRelative",
    "flistCanonicalTrackedFiles",
    "flistCanonicalTrackedFilesFromScans",
    "flistOversizedFiles",
    "fsGenerateGitignore",
]


I_LARGE_FILE_THRESHOLD_BYTES = 50 * 1024 * 1024

S_CONTAINER_WORKSPACE_PREFIX = "/workspace/"

S_VAIBIFY_WORKFLOWS_GLOB = ".vaibify/workflows/*.json"
S_VAIBIFY_MARKERS_GLOB = ".vaibify/test_markers/*.json"
S_VAIBIFY_ZENODO_REFS = ".vaibify/zenodo-refs.json"

TUPLE_ROOT_CONFIG_FILES = (
    "requirements.txt",
    "environment.yml",
    "Dockerfile",
    "pyproject.toml",
)

TUPLE_ALWAYS_IGNORED = (
    ".vaibify/logs/",
    ".vaibify/pipeline_state.json",
    ".vaibify/overleaf-mirrors/",
    ".vaibify/mtime_cache.json",
)


def fsToRepoRelative(sPath):
    """Return a repo-root-relative posix path.

    Strips a leading ``/workspace/`` so host callers can safely join
    the result against a host workspace root. Paths already relative
    pass through unchanged.
    """
    if not sPath:
        return ""
    sNormal = posixpath.normpath(sPath)
    if sNormal.startswith(S_CONTAINER_WORKSPACE_PREFIX):
        return sNormal[len(S_CONTAINER_WORKSPACE_PREFIX):]
    if sNormal == "/workspace":
        return ""
    return sNormal.lstrip("/")


def _flistStepOutputRepoPaths(dictStep, dictVars=None):
    """Return repo-relative paths of plot + data files for one step.

    Templated entries (``{sPlotDirectory}/foo.pdf``) are expected to
    resolve to repo-relative paths and are NOT joined with the step
    directory; non-templated entries are joined with the step
    directory (where the step actually writes them).
    """
    sDirectory = dictStep.get("sDirectory", "")
    listPaths = []
    for sKey in ("saDataFiles", "saPlotFiles"):
        for sFile in dictStep.get(sKey, []):
            if "{" in sFile:
                if dictVars is None:
                    continue
                sResolved = workflowManager.fsResolveVariables(
                    sFile, dictVars,
                )
                if "{" in sResolved:
                    continue
                sJoined = posixpath.normpath(sResolved)
            else:
                sJoined = posixpath.normpath(
                    posixpath.join(sDirectory, sFile)
                )
            listPaths.append(fsToRepoRelative(sJoined))
    return listPaths


def _flistStepScriptRepoPaths(dictStep):
    """Return repo-relative paths of scripts referenced by a step.

    Delegates to ``manifestPaths.flistStepScriptRepoPaths`` so the
    canonical-tracked-files set and the manifest envelope share a
    single extraction path; lockstep is enforced rather than
    convention.
    """
    return manifestPaths.flistStepScriptRepoPaths(dictStep)


def _flistStepStandardsRepoPaths(dictStep):
    """Return repo-relative paths of test standards for one step.

    Delegates to ``manifestPaths.flistStepStandardsRepoPaths`` for the
    same lockstep reason as the script-path helper above.
    """
    return manifestPaths.flistStepStandardsRepoPaths(dictStep)


def _flistVaibifyTrackedFiles(sWorkspaceRoot):
    """Return repo-relative paths under .vaibify/ that round-trip through git.

    Scans the host filesystem for concrete workflow JSON and test
    marker files, plus zenodo-refs.json if present.
    """
    listPaths = []
    sVaibifyDir = os.path.join(sWorkspaceRoot, ".vaibify")
    for sSubdir, sExt in (
        ("workflows", ".json"),
        ("test_markers", ".json"),
    ):
        sFullDir = os.path.join(sVaibifyDir, sSubdir)
        if not os.path.isdir(sFullDir):
            continue
        for sEntry in sorted(os.listdir(sFullDir)):
            if sEntry.endswith(sExt):
                listPaths.append(
                    posixpath.join(".vaibify", sSubdir, sEntry)
                )
    if os.path.isfile(os.path.join(sVaibifyDir, "zenodo-refs.json")):
        listPaths.append(S_VAIBIFY_ZENODO_REFS)
    return listPaths


def _flistRootConfigFiles(sWorkspaceRoot):
    """Return repo-relative paths of workspace-root config files that exist."""
    listPaths = []
    for sName in TUPLE_ROOT_CONFIG_FILES:
        if os.path.isfile(os.path.join(sWorkspaceRoot, sName)):
            listPaths.append(sName)
    return listPaths


def _fsetExcludedRepoPaths(dictStep):
    """Return repo-relative paths the step has explicitly marked excluded."""
    dictExcluded = dictStep.get("dictExcludedFiles", {}) or {}
    return {
        fsToRepoRelative(sPath)
        for sPath, bExcluded in dictExcluded.items()
        if bExcluded
    }


def flistCanonicalTrackedFilesFromScans(
    dictWorkflow, listVaibifyTracked, listRootConfigs,
):
    """Pure variant: combines pre-scanned file lists with workflow paths.

    Transport-agnostic core used by both the host-side
    ``flistCanonicalTrackedFiles`` and the container route that can't
    walk the host filesystem (Docker-volume workspace case).
    ``listVaibifyTracked`` is the concrete list of ``.vaibify/**``
    files that exist; ``listRootConfigs`` is the intersection of
    ``TUPLE_ROOT_CONFIG_FILES`` with what exists at the workspace
    root.
    """
    dictVars = {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
    }
    listPaths = []
    setSeen = set()
    setExcluded = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        setExcluded.update(_fsetExcludedRepoPaths(dictStep))
    for sPath in listVaibifyTracked or []:
        if sPath not in setSeen and sPath not in setExcluded:
            listPaths.append(sPath)
            setSeen.add(sPath)
    for sPath in listRootConfigs or []:
        if sPath not in setSeen and sPath not in setExcluded:
            listPaths.append(sPath)
            setSeen.add(sPath)
    for dictStep in dictWorkflow.get("listSteps", []):
        for sPath in _flistStepScriptRepoPaths(dictStep):
            if sPath and sPath not in setSeen and sPath not in setExcluded:
                listPaths.append(sPath)
                setSeen.add(sPath)
        for sPath in _flistStepOutputRepoPaths(dictStep, dictVars):
            if sPath and sPath not in setSeen and sPath not in setExcluded:
                listPaths.append(sPath)
                setSeen.add(sPath)
        for sPath in _flistStepStandardsRepoPaths(dictStep):
            if sPath and sPath not in setSeen and sPath not in setExcluded:
                listPaths.append(sPath)
                setSeen.add(sPath)
    return listPaths


def flistCanonicalTrackedFiles(dictWorkflow, sWorkspaceRoot):
    """Host-side canonical tracked list. Requires filesystem access.

    Scans the workspace root for ``.vaibify`` state and top-level
    config files, then delegates to
    ``flistCanonicalTrackedFilesFromScans``.
    """
    listVaibify = _flistVaibifyTrackedFiles(sWorkspaceRoot)
    listRoot = _flistRootConfigFiles(sWorkspaceRoot)
    return flistCanonicalTrackedFilesFromScans(
        dictWorkflow, listVaibify, listRoot,
    )


def flistOversizedFiles(
    listRepoRelPaths, sWorkspaceRoot,
    iThresholdBytes=I_LARGE_FILE_THRESHOLD_BYTES,
):
    """Return the subset of paths whose on-disk size exceeds the threshold."""
    listOversized = []
    for sRelPath in listRepoRelPaths:
        sHostPath = os.path.join(
            sWorkspaceRoot, *sRelPath.split("/")
        )
        try:
            iSize = os.path.getsize(sHostPath)
        except OSError:
            continue
        if iSize > iThresholdBytes:
            listOversized.append(sRelPath)
    return listOversized


def _flistExcludedPathsFromWorkflow(dictWorkflow):
    """Collect excluded repo-relative paths across all steps, sorted."""
    setPaths = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        setPaths.update(_fsetExcludedRepoPaths(dictStep))
    return sorted(p for p in setPaths if p)


def fsGenerateGitignore(dictWorkflow, listOversized=None):
    """Return a ``.gitignore`` body for a workspace with this workflow.

    The content has four sections: always-ignored vaibify internals,
    files flagged oversized (hand off to Zenodo), files the user
    explicitly excluded via the tri-state star, and comment headers
    that tell a reader what the contract is.
    """
    listLines = ["# Generated by vaibify \u2014 workspace-as-git-repo contract"]
    listLines.append("# See vaibify/gui/stateContract.py for the rules.")
    listLines.append("")
    listLines.append("# Always ignored: ephemeral vaibify runtime state")
    for sPath in TUPLE_ALWAYS_IGNORED:
        listLines.append(sPath)
    listLines.append("")
    if listOversized:
        listLines.append(
            "# Too large for git \u2014 archive to Zenodo instead"
        )
        for sPath in sorted(set(listOversized)):
            listLines.append(sPath)
        listLines.append("")
    listExcluded = _flistExcludedPathsFromWorkflow(dictWorkflow)
    if listExcluded:
        listLines.append("# Excluded by user (tri-state star \u2014 Phase 4)")
        for sPath in listExcluded:
            listLines.append(sPath)
        listLines.append("")
    return "\n".join(listLines) + "\n"
