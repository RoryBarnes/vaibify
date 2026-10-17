"""The rerun procedure's guards each refuse what they exist to refuse.

Every function here guards a boundary the assessed rerun depends on:
the clearing never reaches a protected file, the hashing never follows
a path out of the repository, the capability reading never accepts a
floor it does not know, the plan never carries a job the shadow would
refuse, and the adapter writes outside its repository only into a
vaibify run directory beside it. The expected answers are those
boundaries, stated in the design and the module docstrings, not the
code under test.
"""

import os

import pytest

from vaibify.reproducibility import rerunAssessment, rerunProcedure
from vaibify.reproducibility.repoFiles import (
    HostRepoFiles,
    fnRequirePathBesideRepository,
)
from vaibify.reproducibility.rerunPreparation import (
    RerunPreparationRefusedError,
)


def _fnWrite(sPath, sText):
    """Write a file, creating its directory."""
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileOut:
        fileOut.write(sText)


@pytest.mark.falsification
def testClearingRefusesATargetThatContainsAProtectedFile(tmp_path):
    """A produced directory holding a pinned script is never deleted.

    Kills: the containment test answering only for identical paths.
    """
    sRoot = str(tmp_path / "repo")
    _fnWrite(os.path.join(sRoot, "Step/out/keep.py"), "pinned\n")
    dictResult = rerunProcedure.fdictClearRegeneratedPaths(sRoot, {
        "listProduced": ["Step/out"], "listProtected": ["Step/out/keep.py"],
        "listScratch": []})
    assert dictResult["listRefusals"]
    assert "Step/out/keep.py" in dictResult["listRefusals"][0]
    assert dictResult["listDeletedOutputs"] == []
    assert os.path.isfile(os.path.join(sRoot, "Step/out/keep.py"))


@pytest.mark.falsification
def testHashingNeverFollowsASymlinkOutOfTheRepository(tmp_path):
    """A pinned path that resolves outside the repository has no hash.

    Kills: hashing whatever a symlink inside the repository points at.
    """
    sRoot = str(tmp_path / "repo")
    sOutside = str(tmp_path / "outside.txt")
    _fnWrite(sOutside, "not the repository's\n")
    os.makedirs(sRoot)
    os.symlink(sOutside, os.path.join(sRoot, "link.txt"))
    assert rerunProcedure.fsHashRepositoryFileOrNone(sRoot, "link.txt") is None
    _fnWrite(os.path.join(sRoot, "inside.txt"), "inside\n")
    assert rerunProcedure.fsHashRepositoryFileOrNone(
        sRoot, "inside.txt") is not None


@pytest.mark.falsification
def testCapabilitiesRefuseAFloorKeyOutsideTheAllowlist(tmp_path, capsys):
    """A requested floor key the allowlist does not name is "Could not run".

    Kills: accepting any floor key a profile names.
    """
    iExit = rerunProcedure.fiRunMain([
        "capabilities", "--scratch", str(tmp_path),
        "--required-level", "x86-64-v3", "--floor-keys", "LD_PRELOAD"], {})
    assert iExit == 4
    assert "LD_PRELOAD" in capsys.readouterr().err
    assert rerunProcedure.fiRunMain([
        "capabilities", "--required-level", "x86-64-v9",
        "--floor-keys", "OPENBLAS_CORETYPE"], {}) == 4


@pytest.mark.falsification
def testThePlanRefusesWhatTheShadowWouldRefuse():
    """A reader's script never carries a clearing job the shadow refuses.

    Kills: building the plan without the shadow's host-side refusals.
    """
    dictWorkflow = {"listSteps": [
        {"sName": "Human", "sDirectory": "Shared", "bInteractive": True,
         "saOutputDataFiles": ["given.json"]},
        {"sName": "Machine", "sDirectory": "Shared", "bRunEnabled": True,
         "saDataCommands": ["python3 make.py"],
         "saOutputDataFiles": ["given.json"]},
    ]}
    with pytest.raises(RerunPreparationRefusedError) as errorRefused:
        rerunAssessment.fdictBuildAssessmentPlan(dictWorkflow)
    assert "Shared/given.json" in str(errorRefused.value)


@pytest.mark.falsification
def testTheAdapterWritesOutsideItsRepositoryOnlyIntoARunDirectory(tmp_path):
    """Beside the repository, in a ``vaibifyRerun`` directory, and nowhere else.

    Kills: letting the beside-repository write reach any path.
    """
    sRoot = str(tmp_path / "repo")
    os.makedirs(sRoot)
    os.makedirs(str(tmp_path / "vaibifyRerunScratch.abc"))
    filesHost = HostRepoFiles(sRoot)
    filesHost.fnWriteBesideRepository(
        str(tmp_path / "vaibifyRerunScratch.abc" / "a.txt"), "ok")
    for sTarget in (str(tmp_path / "elsewhere.txt"),
                    os.path.join(sRoot, "inside.txt"),
                    str(tmp_path / "other" / "a.txt"),
                    str(tmp_path / "deeper" / "vaibifyRerunScratch.x" / "a.txt"),
                    str(tmp_path / "vaibifyRerunScratch.abc" / "d" / "a.txt")):
        with pytest.raises(ValueError):
            fnRequirePathBesideRepository(sRoot, sTarget)
    assert not os.path.exists(os.path.join(sRoot, "inside.txt"))
