"""Rename a project: change one project file's ``sWorkflowName``, honestly.

A project's display name lives inside its project file, and that file's
bytes are hashed by the PROOF machinery: the GitHub and Zenodo checks
compare it with the published copy, and a reproduction record binds its
digest. So a rename is not cosmetic. Two rulings (2026-10-28) shape
what this module does about that:

- **D4.** The dry run lists every PROOF requirement and label the new
  bytes would invalidate, and every byte change besides the name (a
  save also stamps the current schema version), so the researcher
  confirms with that list in front of them. The verdicts come from the
  EXISTING predicates -- ``levelGates.fdictLevel2Gaps``,
  ``levelGates.fdictL3ReadinessGaps`` and
  ``reproductionLabel.fdictBuildReproductionLabel`` -- evaluated twice
  over one snapshot of the repository, once as it is and once with the
  project file's bytes replaced by the bytes the save would write.
  Nothing here re-derives a level.
- **D5.** A project whose declared paths or fields use the
  ``{sWorkflowName}`` token is refused, naming each step and field,
  because renaming it would move those outputs.

The file to rename is named by the request, so it is authorized by
EXACT equality with the container's own project listing; anything else
is a 404. The write goes through the canonical save with the project
repository as the confining root. Only when the file is the project
this container has loaded does the hub's cache follow the rename.
"""

import copy
import hashlib
import json
import posixpath

from fastapi import HTTPException

from . import workflowManager
from . import workflowMigrations

__all__ = [
    "I_MAXIMUM_PROJECT_NAME_LENGTH",
    "S_WORKFLOW_NAME_TOKEN",
    "fdictBuildRenamePreview",
    "fdictComputeInvalidations",
    "fdictFindListedProject",
    "fdictRenameProject",
    "flistDescribeOtherByteChanges",
    "flistDescribeWorkflowNameTokenUses",
    "fsValidateProjectDisplayName",
]

S_WORKFLOW_NAME_TOKEN = "{sWorkflowName}"
I_MAXIMUM_PROJECT_NAME_LENGTH = 200


def fsValidateProjectDisplayName(sNewName):
    """Return the stripped name, or raise 400 naming what is wrong.

    The create route applies no rule to a project's display name beyond
    uniqueness, so this states the narrow one a name needs to survive a
    JSON file and a dashboard card: present, bounded, and free of
    control characters.
    """
    sClean = (sNewName or "").strip()
    if not sClean or len(sClean) > I_MAXIMUM_PROJECT_NAME_LENGTH:
        raise HTTPException(
            400, "A project name must be 1 to "
            f"{I_MAXIMUM_PROJECT_NAME_LENGTH} characters.",
        )
    if any(ord(sCharacter) < 32 or ord(sCharacter) == 127
           for sCharacter in sClean):
        raise HTTPException(
            400, "A project name may not contain control characters.",
        )
    return sClean


def fdictFindListedProject(listProjects, sWorkflowPath):
    """Return the listing entry whose path EQUALS the request's, or 404.

    Exact equality with the server's own listing is the authorization:
    the request names a file, and the only files this route may rewrite
    are the project files the container's discovery found. A normalized
    or prefix comparison would admit a path the listing never offered.
    """
    for dictProject in listProjects or []:
        if dictProject.get("sPath") == sWorkflowPath:
            return dictProject
    raise HTTPException(404, "No such project in this environment.")


def _flistTokenFieldsIn(jsonValue, sFieldPath):
    """Return the field paths under ``jsonValue`` whose text uses the token."""
    if isinstance(jsonValue, str):
        return [sFieldPath] if S_WORKFLOW_NAME_TOKEN in jsonValue else []
    listFound = []
    if isinstance(jsonValue, dict):
        for sKey, jsonChild in jsonValue.items():
            listFound.extend(_flistTokenFieldsIn(
                jsonChild, f"{sFieldPath}.{sKey}" if sFieldPath else sKey,
            ))
    elif isinstance(jsonValue, list):
        for iIndex, jsonChild in enumerate(jsonValue):
            listFound.extend(_flistTokenFieldsIn(
                jsonChild, f"{sFieldPath}[{iIndex}]",
            ))
    return listFound


def flistDescribeWorkflowNameTokenUses(dictWorkflow):
    """Return one sentence per step or project field that uses the token.

    Every string is scanned, not only the declared output lists: the
    token resolves wherever a workflow-level template value does, so a
    directory, a command or a plot path that names it moves with the
    name just as an output does.
    """
    listUses = []
    dictTopLevel = {
        sKey: jsonValue for sKey, jsonValue in (dictWorkflow or {}).items()
        if sKey not in ("listSteps", "sWorkflowName")
    }
    for sField in _flistTokenFieldsIn(dictTopLevel, ""):
        listUses.append(f"the project's {sField} field")
    for iIndex, dictStep in enumerate(
            (dictWorkflow or {}).get("listSteps") or []):
        if not isinstance(dictStep, dict):
            continue
        sStep = _fsDescribeStep(dictStep, iIndex)
        for sField in _flistTokenFieldsIn(dictStep, ""):
            listUses.append(f"step {sStep}, field {sField}")
    return listUses


def _fsDescribeStep(dictStep, iIndex):
    """Return ``A01 (name)``, falling back to the step's position."""
    sLabel = dictStep.get("sLabel") or f"#{iIndex + 1}"
    sName = dictStep.get("sName") or ""
    return f"{sLabel} ({sName})" if sName else sLabel


def _fnRefuseTokenUses(dictWorkflow):
    """Raise 409 when the project names its own name in a path or field."""
    listUses = flistDescribeWorkflowNameTokenUses(dictWorkflow)
    if not listUses:
        return
    raise HTTPException(409, detail={
        "sMessage": (
            "This project cannot be renamed, because these use the "
            f"{S_WORKFLOW_NAME_TOKEN} token, and renaming the project "
            "would move what they name: " + "; ".join(listUses) + "."
        ),
        "listTokenUses": listUses,
    })


def _fdictLoadSelectedProject(connectionDocker, sContainerId, sWorkflowPath):
    """Load the selected file fresh, or refuse with the loader's own reason.

    A project file that does not load is a fact about the file, not a
    half-finished write, so it is a 409 the researcher can act on --
    never the 5xx that would poison the carrier's journal record.
    """
    try:
        return workflowManager.fdictLoadWorkflowFromContainer(
            connectionDocker, sContainerId, sWorkflowPath,
        )
    except ValueError as error:
        raise HTTPException(409, (
            "This project file cannot be renamed until it loads: "
            f"{error}"
        ))


def _ftLoadRenamedPair(connectionDocker, sContainerId, dictProject, sNewName):
    """Return ``(dictCurrent, dictRenamed, sNewJson)`` for the listed file."""
    sWorkflowPath = dictProject["sPath"]
    dictCurrent = _fdictLoadSelectedProject(
        connectionDocker, sContainerId, sWorkflowPath,
    )
    dictCurrent["sProjectRepoPath"] = dictProject.get("sProjectRepoPath", "")
    _fnRefuseTokenUses(dictCurrent)
    dictRenamed = copy.deepcopy(dictCurrent)
    dictRenamed["sWorkflowName"] = sNewName
    sNewJson, _dictState, _dictBookkeeping, _sRepo = (
        workflowManager.ftPrepareWorkflowForSave(
            connectionDocker, sContainerId, dictRenamed, sWorkflowPath,
        )
    )
    return dictCurrent, dictRenamed, sNewJson


def _flistDifferingFields(jsonBefore, jsonAfter, sFieldPath):
    """Return the field paths whose values differ between two JSON trees."""
    if isinstance(jsonBefore, dict) and isinstance(jsonAfter, dict):
        listFound = []
        for sKey in sorted(set(jsonBefore) | set(jsonAfter)):
            listFound.extend(_flistDifferingFields(
                jsonBefore.get(sKey), jsonAfter.get(sKey),
                f"{sFieldPath}.{sKey}" if sFieldPath else sKey,
            ))
        return listFound
    return [] if jsonBefore == jsonAfter else [sFieldPath]


def flistDescribeOtherByteChanges(sCurrentJson, sNewJson):
    """Return a sentence per change the save makes besides the name."""
    try:
        dictBefore = json.loads(sCurrentJson)
    except ValueError:
        return ["the file is rewritten from text that does not parse"]
    dictAfter = json.loads(sNewJson)
    listChanges = []
    sVersionKey = workflowMigrations.S_VERSION_KEY
    if dictBefore.get(sVersionKey) is None and dictAfter.get(sVersionKey):
        listChanges.append(
            "this also stamps the file's format version "
            f"({dictAfter.get(sVersionKey)}), which it does not carry yet"
        )
    elif dictBefore.get(sVersionKey) != dictAfter.get(sVersionKey):
        listChanges.append(
            "this also upgrades the file's format from version "
            f"{dictBefore.get(sVersionKey)} to {dictAfter.get(sVersionKey)}"
        )
    for sField in _flistDifferingFields(dictBefore, dictAfter, ""):
        if sField not in ("sWorkflowName", sVersionKey):
            listChanges.append(f"this also rewrites the {sField} field")
    dictRenamedBefore = dict(dictBefore)
    dictRenamedBefore["sWorkflowName"] = dictAfter.get("sWorkflowName")
    if not listChanges and (
            json.dumps(dictRenamedBefore, indent=2) + "\n" != sNewJson):
        listChanges.append(
            "this also rewrites the file's layout (its spacing or the "
            "order of its fields), though no value but the name changes"
        )
    return listChanges


def _fsGitBlobSha(baContent):
    """Return git's blob sha of ``baContent``, as the snapshot records it."""
    baHeader = f"blob {len(baContent)}\0".encode("utf-8")
    return hashlib.sha1(baHeader + baContent).hexdigest()


def _ffilesProposedSnapshot(filesSnapshot, sRelativePath, sNewText):
    """Return a copy of ``filesSnapshot`` holding the save's bytes for one file.

    Every other path answers exactly as the snapshot does, so the only
    thing the second evaluation can see differently is the project
    file -- the one input the rename changes.
    """
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    baNew = sNewText.encode("utf-8")
    dictFiles = copy.deepcopy(filesSnapshot._dictFiles)
    dictHashes = copy.deepcopy(filesSnapshot._dictHashes)
    dictEntry = dictFiles.setdefault(sRelativePath, {})
    dictEntry.update({"bIsFile": True, "sText": sNewText})
    dictHashes[sRelativePath] = {
        "sSha256": hashlib.sha256(baNew).hexdigest(),
        "sBlobSha": _fsGitBlobSha(baNew),
    }
    filesProposed = SnapshotRepoFiles(
        filesSnapshot.sRootPath, dictFiles, dictHashes,
        dictAbsHashes=copy.deepcopy(filesSnapshot._dictAbsHashes),
    )
    filesProposed.dictReproductionRecords = (
        filesSnapshot.dictReproductionRecords)
    filesProposed.sReproductionsError = filesSnapshot.sReproductionsError
    filesProposed.bManifestHasEscapedPaths = (
        filesSnapshot.bManifestHasEscapedPaths)
    filesProposed.dictOwnershipFacts = filesSnapshot.dictOwnershipFacts
    return filesProposed


def _ffilesFetchRenameSnapshot(dictCtx, sContainerId, dictWorkflow,
                               sRelativePath):
    """Return one snapshot of the project's repository, hydrated as the poll's."""
    from vaibify.reproducibility.declarationFreshness import (
        flistPathsToHashForFreshness,
    )
    from vaibify.reproducibility.levelGates import (
        _flistAllStepScriptPaths, flistWorkflowBinaryPaths,
    )
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    from .routeContext import flistAllOutputRepoPaths, fnHydrateManifestText
    sRepoRoot = dictWorkflow.get("sProjectRepoPath") or ""
    listHashPaths = flistAllOutputRepoPaths(dictWorkflow, sRepoRoot)
    listHashPaths.extend(flistPathsToHashForFreshness(dictWorkflow))
    listHashPaths.append(sRelativePath)
    filesSnapshot = SnapshotRepoFiles.ffilesFetch(
        dictCtx["docker"], sContainerId, sRepoRoot,
        listScriptRelPaths=_flistAllStepScriptPaths(dictWorkflow),
        listHashRelPaths=sorted(set(listHashPaths)),
        listAbsHashPaths=flistWorkflowBinaryPaths(dictWorkflow),
        bHashManifestEntries=True, bReadReproductions=True,
    )
    fnHydrateManifestText(dictCtx, sContainerId, sRepoRoot, filesSnapshot)
    return filesSnapshot


# The requirement each gate key names, in the researcher's words. A key
# not listed here is reported by its own name rather than dropped.
_DICT_REQUIREMENT_WORDS = {
    "bAtLeastLevel1": "Level 1 (Self-Consistent)",
    "bAtLeastLevel2": "Level 2 (Published)",
    "bGithubFullySynced": "the GitHub published copy matches",
    "bZenodoFullySynced": "the Zenodo published copy matches",
    "bOverleafFullySynced": "the Overleaf copy matches",
    "bArxivFullySynced": "the arXiv submission matches",
    "bL3AttestationCurrent": "the Level 3 rebuild attestation is current",
    "bEnvelopeInGithubMirror": "the envelope matches the GitHub mirror",
    "bEnvelopeInZenodoArchive": "the envelope matches the Zenodo archive",
}


def _flistNewlyFalseKeys(dictBefore, dictAfter):
    """Return the boolean keys that hold now and would not after the save."""
    return sorted(
        sKey for sKey, jsonValue in (dictBefore or {}).items()
        if jsonValue is True and (dictAfter or {}).get(sKey) is False
    )


def _flistGateInvalidations(
    dictBefore, dictAfter, filesNow, filesAfter, sRelativePath,
):
    """Return ``{sLevel, sKey, sRequirement}`` for every gate key the save breaks."""
    from vaibify.reproducibility.levelGates import (
        fdictL3ReadinessGaps, fdictLevel2Gaps, fnClearLevelBlockerCache,
    )
    listInvalidated = []
    for sLevel, fdictGaps in (("Level 2", fdictLevel2Gaps),
                              ("Level 3", fdictL3ReadinessGaps)):
        fnClearLevelBlockerCache()
        dictGapsNow = fdictGaps(dictBefore, filesNow)
        fnClearLevelBlockerCache()
        dictGapsAfter = fdictGaps(dictAfter, filesAfter)
        for sKey in _flistNewlyFalseKeys(dictGapsNow, dictGapsAfter):
            listInvalidated.append({
                "sLevel": sLevel, "sKey": sKey,
                "sRequirement": _DICT_REQUIREMENT_WORDS.get(sKey, sKey),
            })
        if sLevel == "Level 2":
            setFlagged = {dictEntry["sKey"] for dictEntry in listInvalidated}
            listInvalidated.extend(
                dictEntry for dictEntry in _flistPublishedCopyInvalidations(
                    dictGapsNow, filesNow, sRelativePath)
                if dictEntry["sKey"] not in setFlagged)
    fnClearLevelBlockerCache()
    return listInvalidated


# The Level 2 gate does not hash the project file between verifies (its
# canonical set cannot list directories, so a changed definition is left
# to the next verify). The verify DOES compare it, so a rename leaves a
# published copy that the next verify will find different. That is read
# from the cached verify's own compared scope, never assumed.
_T_PUBLISHED_COPY_SERVICES = (
    ("github", "bGithubFullySynced", "the GitHub published copy matches"),
    ("zenodo", "bZenodoFullySynced", "the Zenodo published copy matches"),
)


def _flistPublishedCopyInvalidations(dictGapsNow, filesNow, sRelativePath):
    """Return the services whose next verify will find this file differs."""
    from vaibify.reproducibility import publicationScope, scheduledReverify
    listInvalidated = []
    for sService, sKey, sWords in _T_PUBLISHED_COPY_SERVICES:
        if dictGapsNow.get(sKey) is not True:
            continue
        dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
            filesNow, sService)
        if sRelativePath in publicationScope.fsetSelectLevel2Paths(
                dictStatus.get("listComparedPaths") or []):
            listInvalidated.append({
                "sLevel": "Level 2", "sKey": sKey,
                "sRequirement": sWords + (
                    ": its next verify compares this file, and the "
                    "published copy will differ until the rename is "
                    "published"),
            })
    return listInvalidated


def _flistLabelInvalidation(dictBefore, dictAfter, filesNow, filesAfter,
                            sRelativePath):
    """Return the Reproduced label's entry when the save would clear it."""
    from vaibify.reproducibility.reproductionLabel import (
        fdictBuildReproductionLabel,
    )
    dictLabelNow = fdictBuildReproductionLabel(
        filesNow, dictBefore, sRelativePath)
    dictLabelAfter = fdictBuildReproductionLabel(
        filesAfter, dictAfter, sRelativePath)
    if dictLabelNow.get("bShow") and not dictLabelAfter.get("bShow"):
        return [{
            "sLevel": "Label", "sKey": "dictReproductionLabel",
            "sRequirement": "the Reproduced label",
        }]
    return []


def fdictComputeInvalidations(dictCtx, sContainerId, dictBefore, dictAfter,
                              sNewJson, sRelativePath):
    """Return what the new bytes would invalidate, from the existing predicates.

    ``bKnown`` is False, with the reason, when the repository could not
    be read: an empty list then would claim the rename is free, which
    nobody established.
    """
    try:
        filesNow = _ffilesFetchRenameSnapshot(
            dictCtx, sContainerId, dictBefore, sRelativePath)
        filesAfter = _ffilesProposedSnapshot(
            filesNow, sRelativePath, sNewJson)
        listInvalidated = _flistGateInvalidations(
            dictBefore, dictAfter, filesNow, filesAfter, sRelativePath)
        listInvalidated.extend(_flistLabelInvalidation(
            dictBefore, dictAfter, filesNow, filesAfter, sRelativePath))
    except (OSError, ValueError, KeyError, NotImplementedError) as error:
        return {"bKnown": False, "listInvalidated": [],
                "sUnknownReason": (
                    "vaibify could not read the repository to tell which "
                    f"PROOF requirements this would invalidate ({error})")}
    return {"bKnown": True, "listInvalidated": listInvalidated,
            "sUnknownReason": ""}


def _fsRelativeProjectPath(dictProject):
    """Return the project file's path relative to its repository."""
    from vaibify.reproducibility.reproductionLabel import (
        fsRelativeWorkflowPath,
    )
    return fsRelativeWorkflowPath(
        dictProject["sPath"], dictProject.get("sProjectRepoPath", ""))


def fdictBuildRenamePreview(dictCtx, sContainerId, dictProject, sNewName):
    """Return the dry run: the byte changes and the invalidations, writing nothing."""
    connectionDocker = dictCtx["docker"]
    baCurrent = connectionDocker.fbaFetchFile(
        sContainerId, dictProject["sPath"])
    dictCurrent, dictRenamed, sNewJson = _ftLoadRenamedPair(
        connectionDocker, sContainerId, dictProject, sNewName,
    )
    dictInvalidations = fdictComputeInvalidations(
        dictCtx, sContainerId, dictCurrent, dictRenamed, sNewJson,
        _fsRelativeProjectPath(dictProject),
    )
    return {
        "bDryRun": True,
        "sWorkflowPath": dictProject["sPath"],
        "sOldName": dictCurrent.get("sWorkflowName", ""),
        "sNewName": sNewName,
        "listOtherByteChanges": flistDescribeOtherByteChanges(
            baCurrent.decode("utf-8", errors="replace"), sNewJson),
        **dictInvalidations,
    }


def fdictRenameProject(dictCtx, sContainerId, dictProject, sNewName):
    """Write the new name through the canonical save, confined to the repo.

    The selected file is loaded fresh and saved to ITS path, never
    through ``routeContext.fnSaveWorkflow``, which saves whichever
    project the container has loaded. Only when the selected file IS
    that loaded project does the hub's cache follow the new name and
    the self-write fingerprint move with the file.
    """
    from .pipelineServer import fnRecordSelfWriteOfLoadedWorkflow
    connectionDocker = dictCtx["docker"]
    sWorkflowPath = dictProject["sPath"]
    dictWorkflow = _fdictLoadSelectedProject(
        connectionDocker, sContainerId, sWorkflowPath,
    )
    _fnRefuseTokenUses(dictWorkflow)
    sOldName = dictWorkflow.get("sWorkflowName", "")
    dictWorkflow["sWorkflowName"] = sNewName
    workflowManager.fnSaveWorkflowToContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        dictWriteKeywords={
            "sAuthorizedRoot": posixpath.normpath(
                dictProject.get("sProjectRepoPath") or ""),
            "tForbiddenNames": (".git",),
        },
    )
    dictCached = dictCtx["workflows"].get(sContainerId)
    if dictCached is not None and (
            dictCtx["paths"].get(sContainerId) == sWorkflowPath):
        dictCached["sWorkflowName"] = sNewName
        fnRecordSelfWriteOfLoadedWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictCached["_sSourceFingerprint"] = dictWorkflow["_sSourceFingerprint"]
    return {"bDryRun": False, "sWorkflowPath": sWorkflowPath,
            "sOldName": sOldName, "sNewName": sNewName}
