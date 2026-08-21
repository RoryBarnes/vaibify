"""Discovery of a legacy root-level project.json, over a REAL find.

Early scaffolds left the Project file at the repo ROOT, and discovery
scanned only ``.vaibify/projects`` and ``.vaibify/workflows`` — so a
researcher who registered such a repo saw an empty Project list with no
error anywhere, and the dashboard read as "this project has no
workflows" about a repo that had one (live incident, 2026-08-20: a
freshly promoted host Project whose hub listed nothing).

Everything here runs the REAL pipeline: the actual ``find`` composed by
``_flistDiscoverCandidatePaths``, executed by the actual
``HostConnection`` gated launch against real git repositories on real
disk — because the claim under test crosses the shell boundary (BSD
versus GNU find has burned this repo before) and a stub answering the
find would pass whether or not the pattern matches anything.
"""

import json
import os
import subprocess

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import workflowManager
from vaibify.host import hostScratch
from vaibify.host.hostConnection import HostConnection


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndScratch(tmp_path, monkeypatch):
    """Redirect the journal, locks, and scratch roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        hostScratch, "_S_HOST_DIAGNOSTICS_ROOT",
        str(tmp_path / "host-diagnostics"),
    )


def _fsCreateGitRepo(tmp_path, sRepoName):
    """Create a real git repository under tmp_path and return its path."""
    sRepoPath = str(tmp_path / sRepoName)
    os.makedirs(sRepoPath)
    subprocess.run(
        ["git", "init", "-q"], cwd=sRepoPath, check=True,
        capture_output=True,
    )
    return sRepoPath


def _fnWriteJson(sDirectory, sFileName, dictContent):
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, sFileName), "w") as fileHandle:
        json.dump(dictContent, fileHandle)


def _flistDiscover(sSearchRoot):
    """Run the real discovery over the real host connection."""
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sSearchRoot,
    )
    return workflowManager.flistFindWorkflowsInContainer(
        connection, "resource-id", sSearchRoot,
    )


@pytest.mark.falsification
def testARootLevelProjectFileIsDiscovered(tmp_path):
    """A repo-root project.json declaring steps is listed, named by repo.

    The file carries no ``sWorkflowName`` — exactly the legacy shape —
    so the display name must come from the repo directory, never the
    meaningless literal "project.json".

    Kills: dropping ``-o -name 'project.json'`` from the discovery
    find, which regresses to the empty Project hub the live incident
    reported.
    """
    sRepoPath = _fsCreateGitRepo(tmp_path, "legacyGreenhouse")
    _fnWriteJson(sRepoPath, "project.json", {
        "sPlotDirectory": "Plot",
        "listSteps": [],
    })
    listFound = _flistDiscover(sRepoPath)
    assert len(listFound) == 1, (
        f"the root-level project.json was not discovered: {listFound}"
    )
    assert listFound[0]["sPath"] == os.path.join(
        sRepoPath, "project.json",
    )
    assert listFound[0]["sName"] == "legacyGreenhouse"
    assert listFound[0]["sProjectRepoPath"] == os.path.realpath(sRepoPath)


@pytest.mark.falsification
def testAForeignProjectJsonIsNotListed(tmp_path):
    """A root project.json from another ecosystem stays invisible.

    The name is shared — .NET Core, among others, used it — so a
    candidate matched by NAME alone must prove itself by declaring a
    ``listSteps`` list before the hub offers to open it as a vaibify
    workflow.

    Kills: admitting every name-matched candidate without the
    content gate in ``flistFindWorkflowsInContainer``.
    """
    sRepoPath = _fsCreateGitRepo(tmp_path, "dotnetRepo")
    _fnWriteJson(sRepoPath, "project.json", {
        "frameworks": {"netcoreapp1.0": {}},
        "dependencies": {},
    })
    assert _flistDiscover(sRepoPath) == [], (
        "a foreign project.json was offered as a vaibify workflow"
    )


def testCanonicalAndLegacyRootFilesAreListedTogether(tmp_path):
    """The canonical directory and the legacy root coexist in one repo.

    The canonical candidate keeps its own file name and its
    researcher-given ``sWorkflowName``; the legacy one takes the repo's
    name. The content gate applies ONLY to the name-matched candidate —
    a malformed file under ``.vaibify/projects`` still lists, because
    hiding a broken workflow the researcher placed there deliberately
    would bury the problem instead of surfacing it.
    """
    sRepoPath = _fsCreateGitRepo(tmp_path, "mixedRepo")
    _fnWriteJson(sRepoPath, "project.json", {"listSteps": []})
    sCanonicalDirectory = os.path.join(sRepoPath, ".vaibify", "projects")
    _fnWriteJson(sCanonicalDirectory, "analysis.json", {
        "sWorkflowName": "Named Analysis",
        "listSteps": [],
    })
    with open(
        os.path.join(sCanonicalDirectory, "broken.json"), "w",
    ) as fileHandle:
        fileHandle.write("{ not json")
    listNames = sorted(
        dictFound["sName"] for dictFound in _flistDiscover(sRepoPath)
    )
    assert listNames == ["Named Analysis", "broken.json", "mixedRepo"]


def testARootLevelWorkflowLoadsWithItsRepoDerived(tmp_path):
    """A discovered root workflow OPENS, and knows which repo it is in.

    Listing without loading is the "template nobody executes" trap, so
    the discovered path is pushed through the real load. The repo
    derivation is the half with teeth: with ``""`` derived (the
    pre-fix answer for a root path), the workflow loaded but silently
    recorded nothing — no state.json home, no markers, no proof
    level — which is a misrepresentation the dashboard forbids.
    """
    sRepoPath = _fsCreateGitRepo(tmp_path, "loadableRepo")
    _fnWriteJson(sRepoPath, "project.json", {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": -1,
        "listSteps": [],
    })
    connection = HostConnection(
        fnResolveProjectRoot=lambda sResourceId: sRepoPath,
    )
    listFound = workflowManager.flistFindWorkflowsInContainer(
        connection, "resource-id", sRepoPath,
    )
    assert len(listFound) == 1
    sWorkflowPath = listFound[0]["sPath"]
    dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
        connection, "resource-id", sWorkflowPath,
    )
    assert dictWorkflow["listSteps"] == []
    assert workflowManager.fsDeriveProjectRepoPathFromWorkflow(
        sWorkflowPath,
    ) == os.path.dirname(sWorkflowPath)


def testARootProjectFileOutsideAGitRepoIsDropped(tmp_path):
    """The git gate applies to legacy candidates exactly as to canonical.

    Every vaibify workflow lives inside its project repo; a bare
    directory carrying a step-declaring project.json is still not one
    the dashboard may offer, because nothing downstream (badges,
    markers, sync) can function without the repo.
    """
    sBarePath = str(tmp_path / "noRepo")
    _fnWriteJson(sBarePath, "project.json", {"listSteps": []})
    assert _flistDiscover(sBarePath) == []
