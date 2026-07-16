"""Unit tests for ``flistLevel3Blockers``.

The L3 blocker list pins both the per-step criteria
(``missing-from-manifest``, ``script-not-pinned``,
``nondeterminism-undeclared``, ``binary-not-declared``,
``binary-not-captured``) and the workflow-scope criteria
(``dockerfile-not-pinned``, ``dependency-lock-missing``,
``environment-snapshot-missing``, ``reproduce-script-missing``,
``l3-attestation-stale``, ``binaries-not-declared-or-waived``).
"""

import hashlib
import json

import pytest

from vaibify.reproducibility.dockerfileLint import S_DOCKERFILE_FILENAME
from vaibify.reproducibility.levelGates import (
    fbL3ReadinessOK,
    flistLevel3Blockers,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
)


def _fnWriteRequirementsLock(pathDir):
    sBody = (
        "click==8.1.7 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    (pathDir / "requirements.lock").write_text(sBody)


def _fnWriteEnvironment(pathDir, sDigest="img@sha256:" + "a" * 64):
    pathVaib = pathDir / ".vaibify"
    pathVaib.mkdir(parents=True, exist_ok=True)
    dictPayload = {
        "dictContainer": {"sImageDigest": sDigest},
        "sSchemaVersion": "1",
    }
    (pathVaib / "environment.json").write_text(json.dumps(dictPayload))


def _fnWriteDockerfile(pathDir):
    (pathDir / S_DOCKERFILE_FILENAME).write_text(
        "FROM python@sha256:" + "b" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )


def _fnWriteReproduceScript(pathDir):
    pathScript = pathDir / S_REPRODUCE_SCRIPT_FILENAME
    pathScript.write_text("#!/usr/bin/env bash\nset -e\n")
    pathScript.chmod(0o755)


def _fnWriteManifestCoveringPaths(pathDir, listRelativePaths):
    listLines = ["# manifest\n"]
    for sRel in listRelativePaths:
        pathFile = pathDir / sRel
        sHash = hashlib.sha256(pathFile.read_bytes()).hexdigest()
        listLines.append(f"{sHash}  {sRel}\n")
    (pathDir / "MANIFEST.sha256").write_text("".join(listLines))


@pytest.fixture
def fixtureL3Repo(tmp_path):
    """A project repo that satisfies every L3 readiness check.

    Includes the binary-declaration waiver so the binary criteria
    suppress by default; each test that exercises a different
    criterion overrides the relevant piece.
    """
    _fnWriteRequirementsLock(tmp_path)
    _fnWriteEnvironment(tmp_path)
    _fnWriteDockerfile(tmp_path)
    _fnWriteReproduceScript(tmp_path)
    _fnWriteManifestCoveringPaths(
        tmp_path,
        [S_REPRODUCE_SCRIPT_FILENAME, S_DOCKERFILE_FILENAME],
    )
    return tmp_path


def _fdictWaivedWorkflow():
    return {
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
    }


def _flistFindByCriterion(listBlockers, sCriterion):
    return [
        d for d in listBlockers if d.get("sCriterion") == sCriterion
    ]


# ---------------------------------------------------------------------
# Per-step criteria
# ---------------------------------------------------------------------


def testMissingFromManifestCriterionFires(fixtureL3Repo):
    """A step output absent from MANIFEST surfaces missing-from-manifest."""
    dictWorkflow = _fdictWaivedWorkflow()
    dictWorkflow["listSteps"] = [{
        "sName": "A", "sDirectory": "A",
        "saOutputDataFiles": ["A/missing.csv"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
    }]
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listMissing = _flistFindByCriterion(
        listBlockers, "missing-from-manifest",
    )
    assert len(listMissing) == 1
    assert listMissing[0]["iStepIndex"] == 0
    assert "A/missing.csv" in listMissing[0]["listOffendingFiles"]
    assert listMissing[0]["sScope"] == "step"
    assert listMissing[0]["iLevel"] == 3


def testDominantEntryCarriesEveryFailingCriterion(fixtureL3Repo):
    """One entry per step (single dashboard glyph), but the entry's
    ``listFailingCriteria`` reports the complete failure set so the
    level-cell projection can count every unmet requirement."""
    dictWorkflow = _fdictWaivedWorkflow()
    dictWorkflow["listSteps"] = [{
        "sName": "A", "sDirectory": "A",
        "saOutputDataFiles": ["A/missing.csv"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
        "bUnseededRandomnessWarning": True,
    }]
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listStepEntries = [
        d for d in listBlockers if d.get("iStepIndex") == 0
    ]
    assert len(listStepEntries) == 1
    assert listStepEntries[0]["sCriterion"] == "missing-from-manifest"
    assert listStepEntries[0]["listFailingCriteria"] == [
        "missing-from-manifest", "nondeterminism-undeclared",
    ]


def testBinaryNotDeclaredFiresOnVplanetInvocation(fixtureL3Repo):
    """A step invoking vplanet with no declarations triggers the criterion.

    The step's data file must be in MANIFEST so missing-from-manifest
    does not dominate; we pin a sample file and assert the heuristic
    fires.
    """
    sRel = "A/data.csv"
    pathStep = fixtureL3Repo / "A"
    pathStep.mkdir()
    (fixtureL3Repo / sRel).write_text("x\n")
    _fnWriteManifestCoveringPaths(
        fixtureL3Repo,
        [S_REPRODUCE_SCRIPT_FILENAME, S_DOCKERFILE_FILENAME, sRel],
    )
    dictWorkflow = _fdictWaivedWorkflow()
    dictWorkflow["bNoStandaloneBinaries"] = False
    dictWorkflow["listDeclaredBinaries"] = [
        {"sBinaryPath": "/usr/local/bin/multiplanet",
         "sPurpose": "x", "sExpectedVersion": "1.0"},
    ]
    dictWorkflow["listSteps"] = [{
        "sName": "A", "sDirectory": "A",
        "saOutputDataFiles": [sRel],
        "saPlotFiles": [],
        "saDataCommands": ["vplanet input.in"],
        "saPlotCommands": [],
    }]
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listUndeclared = _flistFindByCriterion(
        listBlockers, "binary-not-declared",
    )
    assert len(listUndeclared) == 1
    assert "vplanet" in listUndeclared[0]["listOffendingFiles"]


def _fnSeedStepDataFile(pathRepo, sRel):
    """Materialize a step data file and pin it in MANIFEST."""
    (pathRepo / "A").mkdir()
    (pathRepo / sRel).write_text("x\n")
    _fnWriteManifestCoveringPaths(
        pathRepo,
        [S_REPRODUCE_SCRIPT_FILENAME, S_DOCKERFILE_FILENAME, sRel],
    )


def _fdictWaivedWorkflowWithCommand(sCommand, sRel):
    """Build a falsely-waivered workflow whose step runs ``sCommand``."""
    dictWorkflow = _fdictWaivedWorkflow()
    dictWorkflow["listSteps"] = [{
        "sName": "A", "sDirectory": "A",
        "saOutputDataFiles": [sRel],
        "saPlotFiles": [],
        "saDataCommands": [sCommand],
        "saPlotCommands": [],
    }]
    return dictWorkflow


@pytest.mark.parametrize(
    "sCommand, bExpectFire",
    [
        ("vplanet input.in", True),
        ("/usr/local/bin/vplanet input.in", True),
        ("./bin/vplanet input.in", True),
        ("VPLANET input.in", False),
    ],
    ids=["basename", "absolute-path", "relative-path", "case-mismatch"],
)
def testFalseWaiverCheatingDefeatedByAllowlist(
    fixtureL3Repo, sCommand, bExpectFire,
):
    """The allowlist beats a false waiver across path forms.

    Basename, absolute, and relative invocations all trip the
    word-boundary regex, but POSIX case-sensitivity means an
    uppercase ``VPLANET`` is treated as a distinct binary and does
    not fire.
    """
    sRel = "A/data.csv"
    _fnSeedStepDataFile(fixtureL3Repo, sRel)
    dictWorkflow = _fdictWaivedWorkflowWithCommand(sCommand, sRel)
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listUndeclared = _flistFindByCriterion(
        listBlockers, "binary-not-declared",
    )
    iExpected = 1 if bExpectFire else 0
    assert len(listUndeclared) == iExpected


def testBinaryNotCapturedFiresWhenDeclaredButMissingFromEnv(
    fixtureL3Repo,
):
    """A declared binary referenced but not in environment.json fires."""
    sRel = "A/data.csv"
    (fixtureL3Repo / "A").mkdir()
    (fixtureL3Repo / sRel).write_text("x\n")
    _fnWriteManifestCoveringPaths(
        fixtureL3Repo,
        [S_REPRODUCE_SCRIPT_FILENAME, S_DOCKERFILE_FILENAME, sRel],
    )
    dictWorkflow = _fdictWaivedWorkflow()
    dictWorkflow["bNoStandaloneBinaries"] = False
    dictWorkflow["listDeclaredBinaries"] = [
        {"sBinaryPath": "/usr/local/bin/vplanet",
         "sPurpose": "fwd model", "sExpectedVersion": "v3.0.0"},
    ]
    dictWorkflow["listSteps"] = [{
        "sName": "A", "sDirectory": "A",
        "saOutputDataFiles": [sRel],
        "saPlotFiles": [],
        "saDataCommands": ["vplanet input.in"],
        "saPlotCommands": [],
    }]
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listNotCaptured = _flistFindByCriterion(
        listBlockers, "binary-not-captured",
    )
    assert len(listNotCaptured) == 1
    assert "/usr/local/bin/vplanet" in (
        listNotCaptured[0]["listOffendingFiles"]
    )


# ---------------------------------------------------------------------
# Workflow-scope criteria
# ---------------------------------------------------------------------


def testDockerfileNotPinnedFiresAsWorkflowScope(fixtureL3Repo):
    """An unpinned Dockerfile produces a workflow-scope blocker."""
    (fixtureL3Repo / S_DOCKERFILE_FILENAME).write_text(
        "FROM python:3.11\n"
    )
    dictWorkflow = _fdictWaivedWorkflow()
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listPinned = _flistFindByCriterion(
        listBlockers, "dockerfile-not-pinned",
    )
    assert len(listPinned) == 1
    assert listPinned[0]["iStepIndex"] == -1
    assert listPinned[0]["sStepLabel"] == "(workflow)"
    assert listPinned[0]["sScope"] == "workflow"
    assert listPinned[0]["sRemediationHint"]


def testDependencyLockMissingHintNamesInstallableTools(fixtureL3Repo):
    """The lock-missing hint must tell the user what to install.

    Without uv (or pip-tools) the lock silently never appears; the
    actionable remediation belongs in the L3 readiness payload, not
    only in a host log line.
    """
    (fixtureL3Repo / "requirements.lock").unlink()
    dictWorkflow = _fdictWaivedWorkflow()
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listLockBlockers = _flistFindByCriterion(
        listBlockers, "dependency-lock-missing",
    )
    assert len(listLockBlockers) == 1
    sHint = listLockBlockers[0]["sRemediationHint"]
    assert "uv" in sHint
    assert "pip-tools" in sHint
    assert "https://docs.astral.sh/uv/" in sHint


# ---------------------------------------------------------------------
# Boolean-gate truth-table preservation
# ---------------------------------------------------------------------


def testL3BooleanGateUnchanged(fixtureL3Repo):
    """A properly-waivered workflow still satisfies ``fbL3ReadinessOK``."""
    dictWorkflow = _fdictWaivedWorkflow()
    assert fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def testL3GateFailsWithoutBinaryAnswer(fixtureL3Repo):
    """A workflow missing both waiver and declaration fails L3 readiness."""
    dictWorkflow = {
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
    }
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def testL3BlockerListIncludesBinariesNotDeclaredOrWaived(fixtureL3Repo):
    """A workflow with no binary state emits the workflow-scope blocker."""
    dictWorkflow = {
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
    }
    listBlockers = flistLevel3Blockers(
        dictWorkflow, str(fixtureL3Repo),
    )
    listFound = _flistFindByCriterion(
        listBlockers, "binaries-not-declared-or-waived",
    )
    assert len(listFound) == 1
    assert listFound[0]["iStepIndex"] == -1
