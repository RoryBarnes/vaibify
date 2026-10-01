"""``bArchiveTests: false`` must not leave rows no remedy can clear.

The opt-out stops the manifest from pinning test files and standards,
and the manifest writer folded the AI-declaration file into the same
block, so the declaration -- a publication artefact a human wrote --
fell out of the manifest too. The per-step Level 3 row kept demanding
both the standards and the declaration regardless, so the researcher
saw ``missing-from-manifest`` rows that Regenerate could never clear:
the writer would not write what the row required.

The row's expectation must be the writer's rule: the declaration is
always pinned, standards follow the opt-out.
"""

import os

import pytest

from vaibify.reproducibility.levelGates import (
    fdictComputeStepLevelStates,
    flistLevel3Blockers,
)
from vaibify.reproducibility.manifestWriter import (
    flistParseManifestLines,
    fnWriteManifest,
)

S_DECLARATION = "AIDeclaration/declaration.md"
S_STANDARDS = "analysis/tests/standards.json"


def _fnWrite(sRepo, sRelative, sText):
    sPath = os.path.join(sRepo, sRelative)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileHandle:
        fileHandle.write(sText)


def _fdictWorkflow(sRepo, bArchiveTests):
    return {
        "sProjectRepoPath": sRepo,
        "bArchiveTests": bArchiveTests,
        "bNoStandaloneBinaries": True,
        "listSteps": [
            {
                "sName": "Analysis", "sDirectory": "analysis",
                "saDataCommands": ["python make.py"],
                "saOutputDataFiles": ["out.csv"], "bNoInputData": True,
                "dictTests": {"dictQuantitative": {
                    "saCommands": ["pytest"],
                    "sFilePath": "analysis/tests/test_q.py",
                    "sStandardsPath": S_STANDARDS,
                }},
                "dictVerification": {"sUser": "passed"},
                "dictRunStats": {"fWallClock": 1.0},
            },
            {
                "sName": "AI Declaration", "sDirectory": "AIDeclaration",
                "sStepKind": "ai-declaration", "bInteractive": True,
                "sDeclarationFile": S_DECLARATION,
                "saOutputDataFiles": [],
                "dictVerification": {"sUser": "passed"},
                "dictRunStats": {"fWallClock": 1.0},
            },
        ],
    }


def _fsBuildRepo(tmp_path):
    sRepo = str(tmp_path)
    _fnWrite(sRepo, "analysis/out.csv", "1,2\n")
    _fnWrite(sRepo, "analysis/make.py", "print(1)\n")
    _fnWrite(sRepo, S_STANDARDS, "{}\n")
    _fnWrite(sRepo, "analysis/tests/test_q.py", "def test_q(): pass\n")
    _fnWrite(sRepo, S_DECLARATION, "No AI was used.\n")
    return sRepo


def _flistMissingFromManifestOffenders(dictWorkflow, sRepo):
    return [
        sFile
        for dictBlocker in flistLevel3Blockers(dictWorkflow, sRepo, False)
        if dictBlocker["sCriterion"] == "missing-from-manifest"
        for sFile in dictBlocker["listOffendingFiles"]
    ]


@pytest.mark.falsification
def testTheDeclarationIsPinnedWhateverTheTestsOptOutSays(tmp_path):
    """Kills: manifestWriter._flistCollectManifestPaths: the declaration
    paths put back inside the `if bArchiveTests:` block."""
    sRepo = _fsBuildRepo(tmp_path)
    dictWorkflow = _fdictWorkflow(sRepo, False)
    fnWriteManifest(sRepo, dictWorkflow)
    setPinned = {d["sPath"] for d in flistParseManifestLines(sRepo)}
    assert S_DECLARATION in setPinned
    assert S_STANDARDS not in setPinned


@pytest.mark.falsification
def testNoRowDemandsWhatTheWriterWillNotWriteWhenTestsAreNotArchived(
    tmp_path,
):
    """Kills: levelGates._flistStepDeclaredPaths: the standards demanded
    unconditionally again (the `if bArchiveTests` guard removed)."""
    sRepo = _fsBuildRepo(tmp_path)
    dictWorkflow = _fdictWorkflow(sRepo, False)
    fnWriteManifest(sRepo, dictWorkflow)
    assert _flistMissingFromManifestOffenders(dictWorkflow, sRepo) == []
    dictCells = fdictComputeStepLevelStates(
        dictWorkflow, [], [],
        flistLevel3Blockers(dictWorkflow, sRepo, False),
    )
    for dictStepCells in dictCells.values():
        assert dictStepCells["s3"]["sState"] != "partial"
        assert dictStepCells["s3"]["sState"] != "none"


def testTheDefaultStillPinsAndDemandsTheStandards(tmp_path):
    """The opt-out is the exception: archiving is the default and unchanged."""
    sRepo = _fsBuildRepo(tmp_path)
    dictWorkflow = _fdictWorkflow(sRepo, True)
    fnWriteManifest(sRepo, dictWorkflow)
    setPinned = {d["sPath"] for d in flistParseManifestLines(sRepo)}
    assert {S_DECLARATION, S_STANDARDS} <= setPinned
    assert _flistMissingFromManifestOffenders(dictWorkflow, sRepo) == []
    os.remove(os.path.join(sRepo, "MANIFEST.sha256"))
    _fnWrite(sRepo, "MANIFEST.sha256", "")
    assert S_STANDARDS in _flistMissingFromManifestOffenders(
        dictWorkflow, sRepo)
