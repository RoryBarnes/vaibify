"""Tests for the AICS Level 3 (Reproducibility) gate.

The L3 gate is the conjunction of L2 + ``fbL3ReadinessOK`` +
``fbL3AttestationCurrent``. Tests cover each of the six readiness
verifiers individually plus the composition; the attestation
half of the gate is also exercised here so the conjunction is
checked end-to-end on a synthetic project repo.
"""

import json

import pytest

from vaibify.reproducibility.dockerfileLint import (
    S_DOCKERFILE_FILENAME,
)
from vaibify.reproducibility.l3Attestation import (
    S_ATTESTATION_FILENAME,
    S_STATUS_PASSED,
    fdictBuildAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)
from vaibify.reproducibility.levelGates import (
    fbAtLeastLevel3,
    fbL3ReadinessOK,
    fbVerifyDependencyLock,
    fbVerifyDeterminismDeclared,
    fbVerifyDockerfilePinned,
    fbVerifyEnvironmentSnapshot,
    fbVerifyManifestComplete,
    fbVerifyReproduceScript,
    fdictL3ReadinessGaps,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
)


def _fnWriteRequirementsLock(tmp_path):
    sBody = (
        "click==8.1.7 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    (tmp_path / "requirements.lock").write_text(sBody)


def _fnWriteEnvironment(tmp_path, sDigest="img@sha256:" + "a" * 64):
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    dictPayload = {
        "dictContainer": {"sImageDigest": sDigest},
        "sSchemaVersion": "1",
    }
    (pathDir / "environment.json").write_text(
        json.dumps(dictPayload)
    )


def _fnWriteDockerfile(tmp_path):
    (tmp_path / S_DOCKERFILE_FILENAME).write_text(
        "FROM python@sha256:" + "b" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )


def _fnWriteReproduceScript(tmp_path):
    pathScript = tmp_path / S_REPRODUCE_SCRIPT_FILENAME
    pathScript.write_text("#!/usr/bin/env bash\nset -e\n")
    pathScript.chmod(0o755)
    return pathScript


def _fnWriteManifestCovering(tmp_path, listExtraPaths=()):
    """Write a manifest pinning the supplied paths (relative to tmp_path)."""
    import hashlib
    listLines = ["# manifest\n"]
    for sRel in listExtraPaths:
        pathFile = tmp_path / sRel
        sHash = hashlib.sha256(pathFile.read_bytes()).hexdigest()
        listLines.append(f"{sHash}  {sRel}\n")
    (tmp_path / "MANIFEST.sha256").write_text("".join(listLines))


def _fdictBuildL3ReadyWorkflow():
    return {
        "listSteps": [],
        "dictDeterminism": {"bAcceptBlasVariance": True},
    }


@pytest.fixture
def fixtureL3Repo(tmp_path):
    """Build a project repo that passes every L3 readiness verifier."""
    _fnWriteRequirementsLock(tmp_path)
    _fnWriteEnvironment(tmp_path)
    _fnWriteDockerfile(tmp_path)
    pathScript = _fnWriteReproduceScript(tmp_path)
    _fnWriteManifestCovering(
        tmp_path,
        [S_REPRODUCE_SCRIPT_FILENAME, S_DOCKERFILE_FILENAME],
    )
    return tmp_path


def test_readiness_passes_with_full_envelope(fixtureL3Repo):
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_fails_without_dockerfile(fixtureL3Repo):
    (fixtureL3Repo / S_DOCKERFILE_FILENAME).unlink()
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_fails_without_reproduce_script(fixtureL3Repo):
    (fixtureL3Repo / S_REPRODUCE_SCRIPT_FILENAME).unlink()
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_fails_without_determinism_block(fixtureL3Repo):
    dictWorkflow = {"listSteps": []}
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_fails_without_lockfile(fixtureL3Repo):
    (fixtureL3Repo / "requirements.lock").unlink()
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_fails_without_environment_digest(fixtureL3Repo):
    _fnWriteEnvironment(
        fixtureL3Repo, sDigest="python:3.11",
    )
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_fails_without_manifest_coverage(fixtureL3Repo):
    _fnWriteManifestCovering(fixtureL3Repo, [])
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert not fbL3ReadinessOK(dictWorkflow, str(fixtureL3Repo))


def test_readiness_gaps_dict_shape(fixtureL3Repo):
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    dictGaps = fdictL3ReadinessGaps(
        dictWorkflow, str(fixtureL3Repo),
    )
    for sKey in (
        "bManifestComplete", "bDependencyLockHashed",
        "bEnvironmentDigestPinned", "bDockerfilePinned",
        "bReproduceScriptPinned", "bDeterminismDeclared",
        "bL3ReadinessOK", "bL3AttestationCurrent",
        "sManifestDigest",
    ):
        assert sKey in dictGaps


def test_at_least_level3_requires_attestation_and_l2(fixtureL3Repo):
    # Even with full readiness, no attestation means L3 is unmet.
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    assert not fbAtLeastLevel3(dictWorkflow, str(fixtureL3Repo))
    # And without L2 (synthetic — we don't set up github/zenodo here)
    # the gate still fails even after attesting.
    sDigest = fsCurrentManifestDigest(str(fixtureL3Repo))
    fnWriteAttestation(
        str(fixtureL3Repo),
        fdictBuildAttestation(
            S_STATUS_PASSED, sDigest, "", 1.0, 1, 1, [], "",
        ),
    )
    assert not fbAtLeastLevel3(dictWorkflow, str(fixtureL3Repo))


def test_individual_verifier_helpers_compose_with_readiness(
    fixtureL3Repo,
):
    dictWorkflow = _fdictBuildL3ReadyWorkflow()
    sRepo = str(fixtureL3Repo)
    assert fbVerifyManifestComplete(sRepo, dictWorkflow)
    assert fbVerifyDependencyLock(sRepo)
    assert fbVerifyEnvironmentSnapshot(sRepo)
    assert fbVerifyDockerfilePinned(sRepo)
    assert fbVerifyReproduceScript(sRepo, dictWorkflow)
    assert fbVerifyDeterminismDeclared(sRepo, dictWorkflow)
