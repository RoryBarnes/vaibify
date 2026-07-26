"""Step-rename cascade: plan and apply a coherent step rename.

A vaibify step's display name, its directory, its verification marker,
and every repo-relative path that mentions the directory are one
identity. Renaming only the display name would manufacture staleness
bugs (marker files are named from the directory; manifest entries and
declared paths embed it), so a rename is planned as a complete
change-set — shown to the researcher before anything moves — and then
applied atomically enough to keep every truth surface consistent:

1. ``git mv`` of the step directory inside the project repo (plain
   ``mv`` fallback for untracked directories; nothing is committed —
   the Repos panel shows the staged rename honestly).
2. The test marker file is renamed and its recorded ``sDirectory``
   rewritten, so the step keeps its verification record.
3. ``MANIFEST.sha256`` paths under the old directory are rewritten
   in place — contents did not change, so the recorded hashes stay
   true.
4. The workflow dict's ``sName``, ``sDirectory``, path arrays, remote
   data provenance, and declared-binary paths are rewritten.

The directory follows the name only when it already matched the old
name (the vaibify convention); a custom directory is left alone and
the plan says so. Cross-step ``{StepNN.varname}`` tokens are
label-based and survive any rename untouched.
"""

import posixpath

from .fileStatusManager import (
    fsMarkerNameFromStepDirectory,
    fsWorkflowSlugFromPath,
)
from .pipelineUtils import (
    fnRequireUniqueStepSlug,
    fsShellQuote,
    fsSlugFromStepName,
    fsValidateStepName,
)

__all__ = [
    "StepRenameSplitError",
    "fdictPlanStepRename",
    "fdictPlanDirectoryAlignment",
    "fdictApplyStepRename",
    "flistScanScriptsForOldName",
]


class StepRenameSplitError(RuntimeError):
    """Disk and the workflow dict genuinely disagree after a cascade.

    Raised only in the one state the cascade cannot resolve itself:
    the step directory moved, a later stage failed, and the move
    could not be undone. The workflow dict is left pointing at the
    directory that really holds the bytes while the step name stays
    unchanged, so ``fbStepDirectoryConforms`` reports the step
    nonconforming and the dashboard shows the red warning with the
    align action beside it. Callers MUST persist the workflow before
    surfacing the error — an unsaved warning is a silent one.
    """


_T_STEP_PATH_ARRAY_KEYS = (
    "saStepScripts", "saOutputDataFiles", "saPlotFiles",
    "saInputDataFiles", "saTestStandards", "saSourceCodeDeps",
    "saScratchDirs",
)

_T_STEP_COMMAND_ARRAY_KEYS = (
    "saDataCommands", "saPlotCommands", "saTestCommands",
    "saSetupCommands",
)

S_MANIFEST_FILENAME = "MANIFEST.sha256"




def _fsRewriteDirectoryPrefix(sPath, sOldDirectory, sNewDirectory):
    """Return ``sPath`` with a leading old-directory prefix swapped.

    Paths that do not start at the old directory (step-relative
    entries, other steps' paths) come back unchanged — they stay
    correct after the move by construction.
    """
    if not isinstance(sPath, str) or not sOldDirectory:
        return sPath
    if sPath == sOldDirectory:
        return sNewDirectory
    if sPath.startswith(sOldDirectory + "/"):
        return sNewDirectory + sPath[len(sOldDirectory):]
    return sPath


def _flistPlanFieldRewrites(dictStep, sOldDirectory, sNewDirectory):
    """Return ``[{sField, sOld, sNew}]`` for every step path that moves."""
    listRewrites = []
    for sField in _T_STEP_PATH_ARRAY_KEYS:
        for sPath in dictStep.get(sField) or []:
            sRewritten = _fsRewriteDirectoryPrefix(
                sPath, sOldDirectory, sNewDirectory,
            )
            if sRewritten != sPath:
                listRewrites.append({
                    "sField": sField, "sOld": sPath,
                    "sNew": sRewritten,
                })
    for dictRemote in dictStep.get("listRemoteData") or []:
        if not isinstance(dictRemote, dict):
            continue
        sRewritten = _fsRewriteDirectoryPrefix(
            dictRemote.get("sPath", ""), sOldDirectory, sNewDirectory,
        )
        if sRewritten != dictRemote.get("sPath", ""):
            listRewrites.append({
                "sField": "listRemoteData",
                "sOld": dictRemote.get("sPath", ""),
                "sNew": sRewritten,
            })
    return listRewrites


def _flistPlanBinaryRewrites(dictWorkflow, sOldDirectory, sNewDirectory):
    """Return ``[{sOld, sNew}]`` for declared binaries under the directory."""
    listRewrites = []
    for dictBinary in dictWorkflow.get("listDeclaredBinaries") or []:
        if not isinstance(dictBinary, dict):
            continue
        sPath = dictBinary.get("sBinaryPath", "")
        sRewritten = _fsRewriteDirectoryPrefix(
            sPath, sOldDirectory, sNewDirectory,
        )
        if sRewritten != sPath:
            listRewrites.append({"sOld": sPath, "sNew": sRewritten})
    return listRewrites


def _flistPlanCommandWarnings(dictWorkflow, sOldDirectory):
    """Return warnings for command strings that mention the directory.

    Commands run with the step directory as their working directory,
    so a command that spells the directory name out is either a
    doctrine violation (a cross-step hardcoded path) or an unusual
    self-reference; either way the researcher must fix it by hand —
    a mechanical rewrite of shell strings would be a guess.
    """
    if not sOldDirectory:
        return []
    listWarnings = []
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps") or []
    ):
        if not isinstance(dictStep, dict):
            continue
        for sField in _T_STEP_COMMAND_ARRAY_KEYS:
            for sCommand in dictStep.get(sField) or []:
                if isinstance(sCommand, str) \
                        and sOldDirectory in sCommand:
                    listWarnings.append(
                        f"Step {dictStep.get('sLabel') or iIndex} "
                        f"{sField}: a command mentions "
                        f"'{sOldDirectory}' and must be updated by "
                        "hand",
                    )
    return listWarnings


def fdictPlanStepRename(dictWorkflow, iStepIndex, sNewNameRaw):
    """Return the full rename change-set without mutating anything.

    Under the slug contract (2026-07-18) the directory's final
    component IS a function of the name, so a rename always realigns
    it — including a legacy directory that never matched. Raises
    ``IndexError`` for a bad step index and ``ValueError`` for an
    invalid, unchanged, or colliding name.
    """
    listSteps = dictWorkflow.get("listSteps") or []
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        raise IndexError(f"No step at index {iStepIndex}")
    sNewName = fsValidateStepName(sNewNameRaw)
    sOldName = listSteps[iStepIndex].get("sName", "")
    if sNewName == sOldName:
        raise ValueError("The new name matches the current name")
    return _fdictPlanDirectoryChange(dictWorkflow, iStepIndex, sNewName)


def fdictPlanDirectoryAlignment(dictWorkflow, iStepIndex):
    """Plan a directory-only realignment; the name stays.

    The legacy-migration path: the directory moves to
    ``<parent>/<slug(name)>``. A name that violates the contract's
    alphabet cannot be aligned — it raises, and the caller reports
    "rename the step first".
    """
    listSteps = dictWorkflow.get("listSteps") or []
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        raise IndexError(f"No step at index {iStepIndex}")
    sName = fsValidateStepName(
        listSteps[iStepIndex].get("sName") or "",
    )
    return _fdictPlanDirectoryChange(dictWorkflow, iStepIndex, sName)


def _fdictPlanDirectoryChange(dictWorkflow, iStepIndex, sNewName):
    """Build the change-set moving a step to ``sNewName``'s slug."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    sOldName = dictStep.get("sName", "")
    sOldDirectory = (dictStep.get("sDirectory") or "").strip("/")
    bTemplated = "{" in sOldDirectory
    if sOldDirectory and not bTemplated:
        sParent = posixpath.dirname(sOldDirectory)
        sSlug = fsSlugFromStepName(sNewName)
        sNewDirectory = posixpath.join(sParent, sSlug) \
            if sParent else sSlug
    else:
        sNewDirectory = sOldDirectory
    bDirectoryRenamed = bool(sOldDirectory) and not bTemplated \
        and sNewDirectory != sOldDirectory
    # Unconditional, matching step creation: the slug is a property of
    # the NAME, so a step with no directory yet (or a templated one)
    # can otherwise be renamed onto another step's slug and the
    # collision only surfaces later, when the directory is created.
    fnRequireUniqueStepSlug(dictWorkflow, iStepIndex, sNewName)
    return {
        "sOldName": sOldName,
        "sNewName": sNewName,
        "sOldDirectory": sOldDirectory,
        "sNewDirectory": sNewDirectory,
        "bDirectoryRenamed": bDirectoryRenamed,
        "sDirectoryNote": (
            f"Directory '{sOldDirectory}' contains a template token "
            "and cannot be realigned automatically"
        ) if bTemplated and sOldDirectory else "",
        "listFieldRewrites": _flistPlanFieldRewrites(
            dictStep, sOldDirectory, sNewDirectory,
        ) if bDirectoryRenamed else [],
        "listBinaryRewrites": _flistPlanBinaryRewrites(
            dictWorkflow, sOldDirectory, sNewDirectory,
        ) if bDirectoryRenamed else [],
        "listCommandWarnings": _flistPlanCommandWarnings(
            dictWorkflow, sOldDirectory,
        ) if bDirectoryRenamed else [],
    }


def flistScanScriptsForOldName(
    connectionDocker, sContainerId, dictWorkflow, dictPlan,
):
    """Return the declared scripts whose text mentions the old directory.

    A script may legally use relative paths inside its own step
    directory, but one that spells the directory name out will break
    on rename — the researcher must see that before confirming.
    """
    if not dictPlan.get("bDirectoryRenamed"):
        return []
    sRepo = dictWorkflow.get("sProjectRepoPath") or ""
    if not sRepo:
        return []
    listSteps = dictWorkflow.get("listSteps") or []
    setScriptPaths = set()
    for dictStep in listSteps:
        if not isinstance(dictStep, dict):
            continue
        for sScript in dictStep.get("saStepScripts") or []:
            if isinstance(sScript, str) and sScript:
                setScriptPaths.add(sScript)
    listMentioning = []
    for sScript in sorted(setScriptPaths):
        sAbsolute = posixpath.join(sRepo, sScript)
        iExitCode, _ = connectionDocker.ftResultExecuteCommand(
            sContainerId,
            "grep -Iq -- " +
            fsShellQuote(dictPlan["sOldDirectory"]) + " " +
            fsShellQuote(sAbsolute),
        )
        if iExitCode == 0:
            listMentioning.append(sScript)
    return listMentioning


def _fsBuildDirectoryMoveCommand(sRepo, sFromDirectory, sToDirectory):
    """Return the ``git mv`` (plain ``mv`` fallback) command for a move.

    The forward move and the undo must stay the same operation — an
    undo that reached the working tree differently could leave the
    repo in a third state — so both build the command here.
    """
    return (
        "cd " + fsShellQuote(sRepo) + " && (git mv "
        + fsShellQuote(sFromDirectory) + " "
        + fsShellQuote(sToDirectory) + " 2>/dev/null || mv "
        + fsShellQuote(sFromDirectory) + " "
        + fsShellQuote(sToDirectory) + ")"
    )


def _fnMoveStepDirectory(
    connectionDocker, sContainerId, sRepo, dictPlan,
):
    """``git mv`` (or ``mv``) the step directory; raise on failure."""
    sOldAbsolute = posixpath.join(sRepo, dictPlan["sOldDirectory"])
    sNewAbsolute = posixpath.join(sRepo, dictPlan["sNewDirectory"])
    iExists, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, "test -e " + fsShellQuote(sNewAbsolute),
    )
    if iExists == 0:
        raise ValueError(
            f"'{dictPlan['sNewDirectory']}' already exists in the "
            "project repo — choose a different name",
        )
    iMissing, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, "test -d " + fsShellQuote(sOldAbsolute),
    )
    if iMissing != 0:
        # Nothing on disk yet (the step never ran); the JSON rewrite
        # alone is the whole move.
        return False
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        _fsBuildDirectoryMoveCommand(
            sRepo, dictPlan["sOldDirectory"], dictPlan["sNewDirectory"],
        ),
    )
    if iExitCode != 0:
        raise RuntimeError(
            "Could not move the step directory: "
            + (sOutput or "").strip()[:500],
        )
    return True


def _fnUndoStepDirectoryMove(
    connectionDocker, sContainerId, sRepo, dictPlan,
):
    """Move the directory back; raise RuntimeError when the undo fails."""
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        _fsBuildDirectoryMoveCommand(
            sRepo, dictPlan["sNewDirectory"], dictPlan["sOldDirectory"],
        ),
    )
    if iExitCode != 0:
        raise RuntimeError((sOutput or "").strip()[:500])


def _fbMoveMarkerFile(filesRepo, dictPlan, sWorkflowPath):
    """Rename the marker and rewrite its recorded directory.

    Returns True when a marker was moved. The marker keeps the step's
    verification record; losing it on rename would silently demote a
    verified step to untested.
    """
    import json

    sSlug = fsWorkflowSlugFromPath(sWorkflowPath)
    if not sSlug:
        return False
    sMarkerDir = posixpath.join(".vaibify", "test_markers", sSlug)
    sOldRelative = posixpath.join(
        sMarkerDir,
        fsMarkerNameFromStepDirectory(dictPlan["sOldDirectory"]),
    )
    if not filesRepo.fbIsFile(sOldRelative):
        return False
    sNewRelative = posixpath.join(
        sMarkerDir,
        fsMarkerNameFromStepDirectory(dictPlan["sNewDirectory"]),
    )
    try:
        dictMarker = json.loads(filesRepo.fsReadText(sOldRelative))
    except (ValueError, OSError) as error:
        # Loud, not silent: a marker that exists but cannot be read is
        # a verification record about to be orphaned under a name
        # nothing will look for again.
        raise ValueError(
            f"The step's verification marker '{sOldRelative}' could "
            f"not be read ({error}) — refusing a rename that would "
            "orphan the step's test record. Re-run the step's tests "
            "or delete the marker, then rename.",
        ) from error
    if isinstance(dictMarker, dict) \
            and dictMarker.get("sDirectory"):
        dictMarker["sDirectory"] = dictPlan["sNewDirectory"]
    filesRepo.fnWriteTextAtomic(
        sNewRelative, json.dumps(dictMarker, indent=2) + "\n",
    )
    filesRepo.fbRemoveFile(sOldRelative)
    return True


def _fbRewriteManifestPaths(filesRepo, dictPlan):
    """Swap the directory prefix on manifest entries, hashes intact.

    A rename moves bytes without changing them, so rewriting the
    paths mechanically keeps every recorded hash true; re-hashing an
    entire archive for a rename would be pure waste. Returns True
    when the manifest changed.
    """
    from vaibify.reproducibility.manifestWriter import (
        fbRewriteManifestPathPrefix,
    )
    try:
        return fbRewriteManifestPathPrefix(
            filesRepo, dictPlan["sOldDirectory"],
            dictPlan["sNewDirectory"],
        )
    except FileNotFoundError:
        return False


def _fnApplyWorkflowRewrites(dictWorkflow, iStepIndex, dictPlan):
    """Mutate the workflow dict per the plan (name, paths, binaries)."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    dictStep["sName"] = dictPlan["sNewName"]
    if not dictPlan["bDirectoryRenamed"]:
        return
    dictStep["sDirectory"] = dictPlan["sNewDirectory"]
    for sField in _T_STEP_PATH_ARRAY_KEYS:
        listPaths = dictStep.get(sField)
        if listPaths:
            dictStep[sField] = [
                _fsRewriteDirectoryPrefix(
                    sPath, dictPlan["sOldDirectory"],
                    dictPlan["sNewDirectory"],
                ) for sPath in listPaths
            ]
    for dictRemote in dictStep.get("listRemoteData") or []:
        if isinstance(dictRemote, dict) and dictRemote.get("sPath"):
            dictRemote["sPath"] = _fsRewriteDirectoryPrefix(
                dictRemote["sPath"], dictPlan["sOldDirectory"],
                dictPlan["sNewDirectory"],
            )
    for dictBinary in dictWorkflow.get("listDeclaredBinaries") or []:
        if isinstance(dictBinary, dict) \
                and dictBinary.get("sBinaryPath"):
            dictBinary["sBinaryPath"] = _fsRewriteDirectoryPrefix(
                dictBinary["sBinaryPath"], dictPlan["sOldDirectory"],
                dictPlan["sNewDirectory"],
            )


def _fnUndoOrRecordSplit(
    connectionDocker, sContainerId, sRepo, dictWorkflow, iStepIndex,
    dictPlan, dictReport, errorCascade,
):
    """Reverse a completed directory move, or make the split loud.

    The marker and manifest stages follow bytes that have already
    moved, so when one of them fails the honest repair is to put the
    bytes back: the caller then re-raises into an error toast with the
    workflow dict untouched. When even the undo fails, disk and the
    workflow have genuinely split — the step is pointed at the
    directory that really exists (its NAME deliberately left alone, so
    the pair reads nonconforming) and ``StepRenameSplitError`` carries
    both failures to the researcher.
    """
    if not dictReport["bDirectoryMoved"]:
        return
    try:
        _fnUndoStepDirectoryMove(
            connectionDocker, sContainerId, sRepo, dictPlan,
        )
    except RuntimeError as errorUndo:
        _fnApplyWorkflowRewrites(dictWorkflow, iStepIndex, dictPlan)
        dictWorkflow["listSteps"][iStepIndex]["sName"] = \
            dictPlan["sOldName"]
        raise StepRenameSplitError(
            f"The step directory moved to "
            f"'{dictPlan['sNewDirectory']}' but the rename could not "
            f"be completed ({errorCascade}) and could not be undone "
            f"({errorUndo}). The project now points at the directory "
            "on disk and the step is flagged as not matching its "
            "name — align the directories or rename the step again.",
        ) from errorCascade
    dictReport["bDirectoryMoved"] = False


def fdictApplyStepRename(
    connectionDocker, sContainerId, filesRepo,
    dictWorkflow, iStepIndex, dictPlan, sWorkflowPath,
):
    """Execute the planned rename; return a report of what moved.

    Transactional in both directions. The directory moves first (it
    can fail — name collision, git error — and nothing else must have
    changed when it does); if the marker or manifest stage then fails,
    the move is UNDONE before the error propagates, so the workflow
    dict and disk still describe the same world. Only an undo that
    itself fails leaves them split, and that case raises
    ``StepRenameSplitError`` into the dashboard's warning path rather
    than the silently-conforming state that made the old
    rewrite-the-JSON-last ordering dishonest.
    """
    dictReport = {
        "bDirectoryMoved": False,
        "bMarkerMoved": False,
        "bManifestRewritten": False,
    }
    if dictPlan["bDirectoryRenamed"]:
        sRepo = dictWorkflow.get("sProjectRepoPath") or ""
        if not sRepo:
            raise ValueError(
                "The workflow has no project repo — cannot move the "
                "step directory",
            )
        if not fsWorkflowSlugFromPath(sWorkflowPath):
            # Loud, not silent: without the marker namespace the
            # verification marker cannot follow the directory, and a
            # quiet no-op orphans the step's test record (shipped
            # live 2026-07-18 — the route passed a key the workflow
            # dict never carried).
            raise ValueError(
                "Internal error: the workflow file path is missing, "
                "so the verification marker cannot follow the "
                "directory — refusing a rename that would orphan it",
            )
        dictReport["bDirectoryMoved"] = _fnMoveStepDirectory(
            connectionDocker, sContainerId, sRepo, dictPlan,
        )
        try:
            dictReport["bMarkerMoved"] = _fbMoveMarkerFile(
                filesRepo, dictPlan, sWorkflowPath,
            )
            dictReport["bManifestRewritten"] = _fbRewriteManifestPaths(
                filesRepo, dictPlan,
            )
        except Exception as errorCascade:
            _fnUndoOrRecordSplit(
                connectionDocker, sContainerId, sRepo, dictWorkflow,
                iStepIndex, dictPlan, dictReport, errorCascade,
            )
            raise
    _fnApplyWorkflowRewrites(dictWorkflow, iStepIndex, dictPlan)
    return dictReport
