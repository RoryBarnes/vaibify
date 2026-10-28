"""Renaming a project changes one project file's name, and nothing else.

Driven against REAL files: two git repositories under one root, each
holding one project file, read and written through a real
``HostConnection``. The projects have distinct names AND distinct
paths, so a save that lands in the wrong file, or a cache keyed to the
wrong project, cannot pass by coincidence.

What is pinned (D4, D5, D11 of the 2026-10-28 rulings):

- the request names a file, so it is authorized by EXACT equality with
  the container's own project listing, and an unlisted file is a 404
  that writes nothing;
- the write goes to the selected file through the canonical save,
  never through ``routeContext.fnSaveWorkflow``, which saves whatever
  project the container has loaded; with another project loaded, that
  project's bytes, cache and self-write fingerprint do not move, and
  with the renamed project loaded, the cache follows;
- a project naming ``{sWorkflowName}`` in a field is refused, and the
  refusal names the step and the field;
- the dry run reports the extra byte changes and every requirement the
  new bytes would invalidate, by evaluating the existing predicates over
  the bytes the save would write;
- the project file write is confined to the project repository.
"""

import hashlib
import json
import os
import subprocess
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from vaibify.gui import pipelineServer, projectRename, projectRoots
from vaibify.gui import workflowManager
from vaibify.gui.routes import workflowRoutes
from vaibify.gui.workflowReloadDetector import _fdictGetSelfWriteMap
from vaibify.host.hostConnection import HostConnection


S_CONTAINER_ID = "renameContainerId"


def _fdictProjectContent(sName, listSteps=None):
    return {
        "sWorkflowName": sName, "sPlotDirectory": "Plot",
        "sFigureType": "pdf", "iNumberOfCores": -1,
        "listSteps": listSteps or [],
        workflowManager.workflowMigrations.S_VERSION_KEY:
            workflowManager.workflowMigrations.I_CURRENT_WORKFLOW_VERSION,
    }


def _fnWriteProject(sPath, dictContent):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileProject:
        fileProject.write(json.dumps(dictContent, indent=2) + "\n")


def _fbaRead(sPath):
    with open(sPath, "rb") as fileProject:
        return fileProject.read()


@pytest.fixture
def dictWorld(fnIsolateVaibifyStateDirectories):
    """Two repositories, one project each, under one container root."""
    sRoot = os.path.realpath(
        os.path.join(fnIsolateVaibifyStateDirectories, "workspace"))
    dictPaths = {}
    for sRepo, sFile, sName in (("repoAlpha", "alpha.json", "Alpha"),
                                ("repoBeta", "beta.json", "Beta")):
        sRepoPath = os.path.join(sRoot, sRepo)
        os.makedirs(sRepoPath)
        subprocess.run(["git", "init", "-q", sRepoPath], check=True)
        sPath = os.path.join(sRepoPath, ".vaibify", "projects", sFile)
        _fnWriteProject(sPath, _fdictProjectContent(sName))
        dictPaths[sName] = sPath
    connection = HostConnection(fnResolveProjectRoot=lambda _sId: sRoot)
    dictCtx = {
        "docker": connection, "workflows": {}, "paths": {},
        "pipelineTasks": {},
    }
    with patch.object(
        projectRoots, "fsResolveProjectRoot", lambda _sId, _sDefault: sRoot,
    ):
        yield {"sRoot": sRoot, "dictCtx": dictCtx, "dictPaths": dictPaths}


def _fnLoadIntoCache(dictWorld, sName):
    """Open a project the way connect does: cache, path and fingerprint."""
    dictCtx = dictWorld["dictCtx"]
    sPath = dictWorld["dictPaths"][sName]
    dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
        dictCtx["docker"], S_CONTAINER_ID, sPath,
    )
    dictCtx["workflows"][S_CONTAINER_ID] = dictWorkflow
    dictCtx["paths"][S_CONTAINER_ID] = sPath
    _fdictGetSelfWriteMap(dictCtx)[S_CONTAINER_ID] = (
        dictWorkflow["_sSourceFingerprint"])
    return dictWorkflow


def _fdictRename(dictWorld, sPath, sNewName, bDryRun=False):
    return workflowRoutes._fdictRenameListedProject(
        dictWorld["dictCtx"], S_CONTAINER_ID, sPath, sNewName, bDryRun,
    )


def _fsNameOnDisk(sPath):
    return json.loads(_fbaRead(sPath))["sWorkflowName"]


# ---------------------------------------------------------------------------
# The selected file is the one renamed
# ---------------------------------------------------------------------------


def testRenamingWithNothingLoadedChangesOnlyTheSelectedFile(dictWorld):
    sAlpha = dictWorld["dictPaths"]["Alpha"]
    sBeta = dictWorld["dictPaths"]["Beta"]
    baAlphaBefore = _fbaRead(sAlpha)
    _fdictRename(dictWorld, sBeta, "Gamma")
    assert _fsNameOnDisk(sBeta) == "Gamma"
    assert _fbaRead(sAlpha) == baAlphaBefore
    assert dictWorld["dictCtx"]["workflows"] == {}


@pytest.mark.falsification
def testRenamingAnotherProjectLeavesTheLoadedOneUntouched(dictWorld):
    """With Alpha open, renaming Beta moves nothing of Alpha's.

    Kills: the rename saving through ``dictCtx["save"]`` (the loaded
    project's save) instead of the canonical save to the selected path.
    """
    sAlpha = dictWorld["dictPaths"]["Alpha"]
    sBeta = dictWorld["dictPaths"]["Beta"]
    dictCtx = dictWorld["dictCtx"]
    dictCtx["save"] = pipelineServer._ftBuildHelpers(
        dictCtx, dictCtx["workflows"], dictCtx["paths"])[1]
    dictAlphaCached = _fnLoadIntoCache(dictWorld, "Alpha")
    baAlphaBefore = _fbaRead(sAlpha)
    sFingerprintBefore = _fdictGetSelfWriteMap(dictCtx)[S_CONTAINER_ID]
    _fdictRename(dictWorld, sBeta, "Gamma")
    assert _fsNameOnDisk(sBeta) == "Gamma"
    assert _fbaRead(sAlpha) == baAlphaBefore
    assert dictCtx["workflows"][S_CONTAINER_ID] is dictAlphaCached
    assert dictAlphaCached["sWorkflowName"] == "Alpha"
    assert _fdictGetSelfWriteMap(dictCtx)[S_CONTAINER_ID] == (
        sFingerprintBefore)


def testRenamingTheLoadedProjectMovesItsCacheWithTheFile(dictWorld):
    sAlpha = dictWorld["dictPaths"]["Alpha"]
    dictCtx = dictWorld["dictCtx"]
    dictAlphaCached = _fnLoadIntoCache(dictWorld, "Alpha")
    _fdictRename(dictWorld, sAlpha, "Delta")
    assert _fsNameOnDisk(sAlpha) == "Delta"
    assert dictAlphaCached["sWorkflowName"] == "Delta"
    sOnDisk = hashlib.sha256(_fbaRead(sAlpha)).hexdigest()
    assert _fdictGetSelfWriteMap(dictCtx)[S_CONTAINER_ID] == sOnDisk
    assert dictAlphaCached["_sSourceFingerprint"] == sOnDisk


# ---------------------------------------------------------------------------
# Authorization by the listing, and confinement of the write
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAnUnlistedWritableFileIsNotFoundAndKeepsItsBytes(dictWorld):
    """A regular file that is not a listed project is a 404.

    Kills: removing the listing-equality check in
    ``projectRename.fdictFindListedProject``.
    """
    sNotes = os.path.join(dictWorld["sRoot"], "repoBeta", "notes.json")
    _fnWriteProject(sNotes, _fdictProjectContent("Notes"))
    baBefore = _fbaRead(sNotes)
    with pytest.raises(HTTPException) as excInfo:
        _fdictRename(dictWorld, sNotes, "Hijacked")
    assert excInfo.value.status_code == 404
    assert _fbaRead(sNotes) == baBefore


def testAListedPathSpelledDifferentlyIsNotTheListedFile(dictWorld):
    sBeta = dictWorld["dictPaths"]["Beta"]
    sRespelled = sBeta.replace("/.vaibify/", "/./.vaibify/")
    with pytest.raises(HTTPException) as excInfo:
        _fdictRename(dictWorld, sRespelled, "Gamma")
    assert excInfo.value.status_code == 404
    assert _fsNameOnDisk(sBeta) == "Beta"


def testAListedProjectThatIsASymlinkOutOfTheRepoIsRefused(dictWorld):
    """A listed project file that resolves outside the repo is not written.

    The host leg refuses the path itself; the container leg's write walks
    each component with ``O_NOFOLLOW`` (``confinedWrite``, whose kill is
    registered there). Either way the target outside keeps its bytes.
    """
    sOutside = os.path.join(dictWorld["sRoot"], "..", "outside.json")
    _fnWriteProject(sOutside, _fdictProjectContent("Outside"))
    baOutside = _fbaRead(sOutside)
    sLink = os.path.join(
        dictWorld["sRoot"], "repoBeta", ".vaibify", "projects", "link.json")
    os.symlink(sOutside, sLink)
    listListed = [{"sPath": sLink, "sName": "Outside",
                   "sProjectRepoPath": os.path.join(
                       dictWorld["sRoot"], "repoBeta")}]
    with patch.object(
        workflowManager, "flistFindWorkflowsInContainer",
        return_value=listListed,
    ), pytest.raises(Exception):
        _fdictRename(dictWorld, sLink, "Gamma")
    assert _fbaRead(sOutside) == baOutside


def testTheProjectFileWriteIsConfinedToItsRepository(dictWorld):
    """The container leg confines the write; the save hands it the root."""
    sBeta = dictWorld["dictPaths"]["Beta"]
    connection = dictWorld["dictCtx"]["docker"]
    listWrites = []
    fnWriteReal = connection.fnWriteFile

    def fnRecordWrite(sContainerId, sPath, baContent, **dictKeywords):
        listWrites.append((sPath, dictKeywords))
        return fnWriteReal(sContainerId, sPath, baContent, **dictKeywords)

    with patch.object(connection, "fnWriteFile", fnRecordWrite):
        _fdictRename(dictWorld, sBeta, "Gamma")
    dictKeywords = dict(listWrites)[sBeta]
    assert dictKeywords["sAuthorizedRoot"] == os.path.join(
        dictWorld["sRoot"], "repoBeta")
    assert dictKeywords["tForbiddenNames"] == (".git",)


# ---------------------------------------------------------------------------
# D5: a project that names its own name is refused, by step and field
# ---------------------------------------------------------------------------


@pytest.mark.falsification
def testAProjectUsingTheNameTokenIsRefusedNamingStepAndField(dictWorld):
    """Kills: ``_fnRefuseTokenUses`` returning without refusing."""
    sBeta = dictWorld["dictPaths"]["Beta"]
    _fnWriteProject(sBeta, _fdictProjectContent("Beta", listSteps=[{
        "sName": "Make figures", "sDirectory": "figures",
        "bRunEnabled": True, "bInteractive": False,
        "saDataCommands": [], "saOutputDataFiles": [],
        "saPlotCommands": [],
        "saPlotFiles": ["{sWorkflowName}_summary.pdf"],
        "saTestCommands": [],
    }]))
    baBefore = _fbaRead(sBeta)
    for bDryRun in (True, False):
        with pytest.raises(HTTPException) as excInfo:
            _fdictRename(dictWorld, sBeta, "Gamma", bDryRun=bDryRun)
        assert excInfo.value.status_code == 409
        sMessage = excInfo.value.detail["sMessage"]
        assert "A01 (Make figures)" in sMessage
        assert "saPlotFiles[0]" in sMessage
    assert _fbaRead(sBeta) == baBefore


def testTheTokenScanReachesProjectLevelFields():
    listUses = projectRename.flistDescribeWorkflowNameTokenUses({
        "sWorkflowName": "Beta", "sPlotDirectory": "{sWorkflowName}/Plot",
        "listSteps": [],
    })
    assert listUses == ["the project's sPlotDirectory field"]


# ---------------------------------------------------------------------------
# D4: the dry run says what else changes and what it invalidates
# ---------------------------------------------------------------------------


def testTheDryRunWritesNothingAndNamesAFormatUpgrade(dictWorld):
    sBeta = dictWorld["dictPaths"]["Beta"]
    dictOld = _fdictProjectContent("Beta")
    dictOld.pop(workflowManager.workflowMigrations.S_VERSION_KEY)
    _fnWriteProject(sBeta, dictOld)
    baBefore = _fbaRead(sBeta)
    dictPreview = _fdictRename(dictWorld, sBeta, "Gamma", bDryRun=True)
    assert _fbaRead(sBeta) == baBefore
    assert dictPreview["sOldName"] == "Beta"
    assert dictPreview["sNewName"] == "Gamma"
    assert any("format" in sChange
               for sChange in dictPreview["listOtherByteChanges"])


def testAnAlreadyCurrentFileReportsNoOtherByteChange(dictWorld):
    dictPreview = _fdictRename(
        dictWorld, dictWorld["dictPaths"]["Beta"], "Gamma", bDryRun=True)
    assert dictPreview["listOtherByteChanges"] == []
    assert dictPreview["bKnown"] is True


@pytest.mark.falsification
def testTheDryRunNamesAManifestThatWouldNoLongerMatch(dictWorld):
    """A manifest pinning the project file is reported as invalidated.

    The verdict comes from ``levelGates.fdictL3ReadinessGaps`` evaluated
    over the bytes the save would write; its expected value is derived
    here independently: the manifest pins today's sha256, and the new
    bytes hash differently.

    Kills: ``_ffilesProposedSnapshot`` returning the unchanged snapshot,
    so the second evaluation sees today's bytes.
    """
    sBeta = dictWorld["dictPaths"]["Beta"]
    sRepo = os.path.join(dictWorld["sRoot"], "repoBeta")
    sRelative = os.path.relpath(sBeta, sRepo)
    sSha = hashlib.sha256(_fbaRead(sBeta)).hexdigest()
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "w") as fileManifest:
        fileManifest.write(f"{sSha}  {sRelative}\n")
    dictPreview = _fdictRename(dictWorld, sBeta, "Gamma", bDryRun=True)
    assert dictPreview["bKnown"] is True, dictPreview["sUnknownReason"]
    listKeys = [(dictEntry["sLevel"], dictEntry["sKey"])
                for dictEntry in dictPreview["listInvalidated"]]
    assert ("Level 3", "bManifestMatchesTheFiles") in listKeys
    assert _fbaRead(sBeta).startswith(b"{")
    assert _fsNameOnDisk(sBeta) == "Beta"


def testTheProposedSnapshotChangesOnlyTheProjectFile():
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    filesNow = SnapshotRepoFiles(
        "/repo",
        {"a.json": {"bIsFile": True, "sText": "old"},
         "other.txt": {"bIsFile": True, "sText": "keep"}},
        {"a.json": {"sSha256": "0" * 64}, "other.txt": {"sSha256": "1" * 64}},
    )
    filesAfter = projectRename._ffilesProposedSnapshot(
        filesNow, "a.json", "new\n")
    dictHashes = filesAfter.fdictHashFiles(["a.json", "other.txt"])
    assert dictHashes["a.json"]["sSha256"] == hashlib.sha256(
        b"new\n").hexdigest()
    assert dictHashes["other.txt"]["sSha256"] == "1" * 64
    assert filesAfter.fsReadText("a.json") == "new\n"
    assert filesNow.fsReadText("a.json") == "old"


# ---------------------------------------------------------------------------
# What the route refuses before it writes
# ---------------------------------------------------------------------------


def testRenamingToTheCurrentNameIsRefused(dictWorld):
    with pytest.raises(HTTPException) as excInfo:
        _fdictRename(dictWorld, dictWorld["dictPaths"]["Beta"], "Beta")
    assert excInfo.value.status_code == 400


def testRenamingToAnotherProjectsNameIsRefused(dictWorld):
    """The duplicate check searches the root the listing searched."""
    sBeta = dictWorld["dictPaths"]["Beta"]
    with pytest.raises(HTTPException) as excInfo:
        _fdictRename(dictWorld, sBeta, "Alpha")
    assert excInfo.value.status_code == 409
    assert _fsNameOnDisk(sBeta) == "Beta"


def testARenameWaitsForALiveRunInTheRepository(dictWorld):
    class _LiveTask:
        def done(self):
            return False

    sBeta = dictWorld["dictPaths"]["Beta"]
    dictWorld["dictCtx"]["pipelineTasks"] = {S_CONTAINER_ID: {
        os.path.join(dictWorld["sRoot"], "repoBeta"): _LiveTask()}}
    with pytest.raises(HTTPException) as excInfo:
        _fdictRename(dictWorld, sBeta, "Gamma")
    assert excInfo.value.status_code == 409
    assert _fsNameOnDisk(sBeta) == "Beta"


@pytest.mark.parametrize("sBadName", ["", "   ", "a\nb", "x" * 201])
def testANameMustBePrintableAndBounded(sBadName):
    with pytest.raises(HTTPException) as excInfo:
        projectRename.fsValidateProjectDisplayName(sBadName)
    assert excInfo.value.status_code == 400


@pytest.mark.falsification
def testANameWithAControlCharacterIsRefused():
    """A newline in a name would break the card and the file alike.

    Kills: the control-character refusal in
    ``projectRename.fsValidateProjectDisplayName`` replaced by ``pass``.
    """
    with pytest.raises(HTTPException) as excInfo:
        projectRename.fsValidateProjectDisplayName("Alpha\tBeta")
    assert excInfo.value.status_code == 400
    assert projectRename.fsValidateProjectDisplayName("  Alpha Beta ") == (
        "Alpha Beta")
