"""The randomness lint's flag reaches every Level 3 check that reads it.

The lint writes ``dictStep["dictVerification"]["bUnseededRandomnessWarning"]``.
Three Level 3 checks read the flag from the step's TOP level, where
nothing writes it, so the Level 3 randomness criterion never fired: a
reviewer ran the real lint, saw the nested flag set, and found the
gate's flagged set empty. Every fixture that tested those checks set
the flag at the top level by hand, which is how the suite stayed green.

These tests write no flag. They run the REAL lint over a real
configuration file on disk and ask the real gates what they see.
"""

import pytest

from tests.testLevelGateL3Blockers import (  # noqa: F401 (fixture)
    _fdictWaivedWorkflow,
    fixtureL3Repo,
)
from vaibify.gui.randomnessLint import fnApplyRandomnessLintToWorkflow
from vaibify.reproducibility import levelGates
from vaibify.reproducibility.determinismGate import flistAuditWorkflow


def _fsReadFromDisk(sPath):
    with open(sPath, encoding="utf-8") as fileIn:
        return fileIn.read()


def _fdictLintedWorkflow(pathRepo, sConfigText):
    """Write a sweep config, run the real lint, return the workflow."""
    (pathRepo / "sweep").mkdir(exist_ok=True)
    (pathRepo / "sweep" / "sweep.in").write_text(sConfigText)
    dictWorkflow = _fdictWaivedWorkflow()
    dictWorkflow["sProjectRepoPath"] = str(pathRepo)
    dictWorkflow["dictRandomnessLint"] = {
        "sConfigGlob": "*.in", "sSeedRegex": r"^seed\s+\d+"}
    dictWorkflow["listSteps"] = [{
        "sName": "Sweep", "sDirectory": "sweep",
        "saDataCommands": ["python run.py sweep.in"],
        "saOutputDataFiles": [], "saPlotFiles": [],
        "saPlotCommands": [],
    }]
    fnApplyRandomnessLintToWorkflow(dictWorkflow, _fsReadFromDisk)
    return dictWorkflow


def _flistFailingCriteria(dictWorkflow, pathRepo):
    return [sCriterion
            for dictBlocker in levelGates.flistLevel3Blockers(
                dictWorkflow, str(pathRepo), False)
            if dictBlocker.get("iStepIndex") == 0
            for sCriterion in dictBlocker.get("listFailingCriteria", [])]


@pytest.mark.falsification
def testAnUnseededConfigFailsTheLevelThreeRandomnessCriterion(fixtureL3Repo):
    """Kills: reading the flag from the step's top level again."""
    dictWorkflow = _fdictLintedWorkflow(fixtureL3Repo, "nruns 10\n")
    dictStep = dictWorkflow["listSteps"][0]
    assert dictStep["dictVerification"]["bUnseededRandomnessWarning"] is True
    assert "bUnseededRandomnessWarning" not in dictStep
    assert "nondeterminism-undeclared" in _flistFailingCriteria(
        dictWorkflow, fixtureL3Repo)


def testEveryReaderSeesTheLintsFlag(fixtureL3Repo):
    dictWorkflow = _fdictLintedWorkflow(fixtureL3Repo, "nruns 10\n")
    assert levelGates._fsetNondeterministicSteps(dictWorkflow) == {0}
    assert any("Sweep" in sIssue for sIssue in flistAuditWorkflow(
        dictWorkflow))
    assert levelGates.fbVerifyDeterminismDeclared(
        str(fixtureL3Repo), dictWorkflow) is False
    assert levelGates._fdictBlockerRelevantStep(
        dictWorkflow["listSteps"][0])["bUnseededRandomnessWarning"] is True


def testASeededConfigPassesEveryReader(fixtureL3Repo):
    dictWorkflow = _fdictLintedWorkflow(
        fixtureL3Repo, "seed 42\nnruns 10\n")
    assert "nondeterminism-undeclared" not in _flistFailingCriteria(
        dictWorkflow, fixtureL3Repo)
    assert levelGates._fsetNondeterministicSteps(dictWorkflow) == set()
    assert flistAuditWorkflow(dictWorkflow) == []
    assert levelGates._fdictBlockerRelevantStep(
        dictWorkflow["listSteps"][0])["bUnseededRandomnessWarning"] is False
