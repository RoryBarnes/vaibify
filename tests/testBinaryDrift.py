"""Tests for L3 binary-drift detection and step-binary attribution.

'Reproducible' (L3) requires the binary on disk to match the hash
captured in environment.json. A rebuilt/replaced binary drifts and
must fail L3 for every step that depends on it — including a step
that only depends on the binary IMPLICITLY (declared via
saBinaryDependencies because a command-scan cannot see it, e.g.
maxlev invoking vplanet internally).
"""

import hashlib
import json
import os

from vaibify.reproducibility.levelGates import (
    flistLevel3Blockers,
    flistStepDependedBinaryPaths,
    flistWorkflowBinaryPaths,
)


def _fnWriteBinary(sDir, sName, baContent):
    """Create a fake binary file; return (abs path, sha256)."""
    os.makedirs(sDir, exist_ok=True)
    sPath = os.path.join(sDir, sName)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(baContent)
    return sPath, hashlib.sha256(baContent).hexdigest()


def _fnWriteEnvironmentJson(sRepo, listBinaryEntries):
    """Write .vaibify/environment.json capturing binary hashes."""
    sDir = os.path.join(sRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    with open(
        os.path.join(sDir, "environment.json"), "w", encoding="utf-8",
    ) as fileHandle:
        json.dump(
            {"dictHostBinaries": {"listBinaries": listBinaryEntries}},
            fileHandle,
        )


def _fnWriteManifest(sRepo, dictEntries):
    with open(
        os.path.join(sRepo, "MANIFEST.sha256"), "w", encoding="utf-8",
    ) as fileHandle:
        for sPath, sHash in dictEntries.items():
            fileHandle.write(f"{sHash}  {sPath}\n")


def _fdictWorkflow(sRepo, sBinaryPath, listSteps):
    return {
        "sProjectRepoPath": sRepo,
        "listDeclaredBinaries": [{
            "sBinaryPath": sBinaryPath,
            "sPurpose": "forward model",
            "sExpectedVersion": "3.0",
        }],
        "bNoStandaloneBinaries": False,
        "listSteps": listSteps,
    }


def _flistDriftBlockers(sRepo, dictWorkflow):
    return [
        d for d in flistLevel3Blockers(dictWorkflow, sRepo)
        if d["sCriterion"] == "binary-drifted"
    ]


# --------- attribution helpers ---------


def test_workflow_binary_paths_lists_declared_binaries(tmp_path):
    dictWorkflow = _fdictWorkflow(
        str(tmp_path), "/opt/bin/vplanet", [],
    )
    assert flistWorkflowBinaryPaths(dictWorkflow) == ["/opt/bin/vplanet"]


def test_explicit_dependency_attributes_an_implicit_binary():
    """A step that only names the binary in saBinaryDependencies (its
    command runs maxlev, which calls vplanet internally) still depends
    on vplanet — the command scan alone would miss it."""
    listDeclared = [{"sBinaryPath": "/opt/bin/vplanet"}]
    dictStep = {
        "sName": "MaxLikelihood", "sDirectory": "MaxLev",
        "saDataCommands": ["maxlev config.in"],  # no 'vplanet' token
        "saBinaryDependencies": ["vplanet"],
    }
    assert flistStepDependedBinaryPaths(dictStep, listDeclared) == [
        "/opt/bin/vplanet",
    ]


def test_command_scan_still_attributes_direct_invocation():
    listDeclared = [{"sBinaryPath": "/opt/bin/vplanet"}]
    dictStep = {
        "sDirectory": "Sim", "saDataCommands": ["vplanet vpl.in"],
    }
    assert flistStepDependedBinaryPaths(dictStep, listDeclared) == [
        "/opt/bin/vplanet",
    ]


# --------- L3 drift blocker (end-to-end with real files) ---------


def test_drifted_binary_fails_l3_for_the_implicit_dependent(tmp_path):
    """The reported scenario: vplanet rebuilt after outputs produced,
    the Max Likelihood step depends on it implicitly, so L3 fails."""
    sRepo = str(tmp_path)
    sBin, sOrigHash = _fnWriteBinary(
        str(tmp_path / "bin"), "vplanet", b"OLD vplanet build",
    )
    os.makedirs(os.path.join(sRepo, "MaxLev"), exist_ok=True)
    baOutput = b"{}"
    with open(os.path.join(sRepo, "MaxLev", "out.json"), "wb") as f:
        f.write(baOutput)
    # The output matches the manifest — only the binary drifts, so
    # binary-drifted is the sole (dominant) L3 failure on this step.
    _fnWriteManifest(sRepo, {
        "MaxLev/out.json": hashlib.sha256(baOutput).hexdigest(),
    })
    _fnWriteEnvironmentJson(sRepo, [
        {"sBinaryPath": sBin, "sSha256": sOrigHash, "sVersion": "3.0"},
    ])
    dictWorkflow = _fdictWorkflow(sRepo, sBin, [{
        "sName": "MaxLikelihood", "sDirectory": "MaxLev",
        "saDataCommands": ["maxlev config.in"],
        "saOutputDataFiles": ["MaxLev/out.json"],  # repo-relative
        "saBinaryDependencies": ["vplanet"],
    }])

    # Matching binary → no drift.
    assert _flistDriftBlockers(sRepo, dictWorkflow) == []

    # Rebuild the binary (different bytes) → drift fails L3.
    from vaibify.reproducibility.levelGates import fnClearLevelBlockerCache
    fnClearLevelBlockerCache()
    with open(sBin, "wb") as f:
        f.write(b"NEW vplanet build")
    listDrift = _flistDriftBlockers(sRepo, dictWorkflow)
    assert len(listDrift) == 1
    assert listDrift[0]["iStepIndex"] == 0
    assert listDrift[0]["listOffendingFiles"] == [sBin]


def test_no_drift_when_binary_has_no_captured_hash(tmp_path):
    """binary-not-captured owns the 'never captured' gap; drift is only
    meaningful against a real captured hash."""
    sRepo = str(tmp_path)
    sBin, _sHash = _fnWriteBinary(
        str(tmp_path / "bin"), "vplanet", b"a binary",
    )
    os.makedirs(os.path.join(sRepo, "MaxLev"), exist_ok=True)
    _fnWriteEnvironmentJson(sRepo, [
        {"sBinaryPath": sBin, "sSha256": None, "sVersion": None},
    ])
    dictWorkflow = _fdictWorkflow(sRepo, sBin, [{
        "sDirectory": "MaxLev", "saDataCommands": ["maxlev x"],
        "saBinaryDependencies": ["vplanet"],
    }])
    assert _flistDriftBlockers(sRepo, dictWorkflow) == []
