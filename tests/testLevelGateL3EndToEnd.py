"""End-to-end test: a synthetic L3-ready workflow reaches iAICSLevel == 3.

Builds a project repo that satisfies every L1, L2, and L3 criterion
(L2 GitHub/Zenodo cached as fully-synced, AI declaration step, L3
envelope artefacts, plus a passing attestation) and confirms that
``fiAICSLevel`` returns 3. Then mutates the manifest to confirm the
gate falls back to 2 once the attestation goes stale.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.reproducibility.aiDeclarationStep import (
    S_AI_DECLARATION_STEP_KIND,
)
from vaibify.reproducibility.dockerfileLint import S_DOCKERFILE_FILENAME
from vaibify.reproducibility.l3Attestation import (
    S_STATUS_PASSED,
    fdictBuildAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)
from vaibify.reproducibility.levelGates import (
    fbAtLeastLevel3,
    fiAICSLevel,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
)


def _fsIsoNow(fHoursAgo=0.0):
    dtNow = datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    return dtNow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fnWriteSyncStatus(tmp_path):
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    dictPayload = {
        "github": {
            "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
            "sLastVerified": _fsIsoNow(0.5),
            "sCommittedShaVerified": "abc123",
        },
        "zenodo": {
            "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
            "sLastVerified": _fsIsoNow(0.5),
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    }
    (pathDir / "syncStatus.json").write_text(
        json.dumps(dictPayload)
    )


def _fnWriteL3EnvelopeFiles(tmp_path):
    (tmp_path / S_DOCKERFILE_FILENAME).write_text(
        "FROM python@sha256:" + "a" * 64 + "\n"
        "ENV SOURCE_DATE_EPOCH=1700000000\n"
    )
    pathScript = tmp_path / S_REPRODUCE_SCRIPT_FILENAME
    pathScript.write_text("#!/usr/bin/env bash\nset -e\n")
    pathScript.chmod(0o755)
    (tmp_path / "requirements.lock").write_text(
        "click==8.1.7 \\\n    --hash=sha256:" + "b" * 64 + "\n"
    )
    pathEnv = tmp_path / ".vaibify"
    pathEnv.mkdir(parents=True, exist_ok=True)
    (pathEnv / "environment.json").write_text(json.dumps({
        "dictContainer": {
            "sImageDigest": "img@sha256:" + "c" * 64,
        },
        "sSchemaVersion": "1",
    }))


def _fnWriteManifest(tmp_path, listRelPaths):
    sBody = "# manifest\n"
    for sRel in listRelPaths:
        sHash = hashlib.sha256(
            (tmp_path / sRel).read_bytes()
        ).hexdigest()
        sBody += f"{sHash}  {sRel}\n"
    (tmp_path / "MANIFEST.sha256").write_text(sBody)


def _fdictBuildLevel3Workflow():
    return {
        "listSteps": [
            {
                "sName": "A", "sDirectory": "A",
                "bNoInputData": True,
                "dictVerification": {
                    "sUser": "passed", "sUnitTest": "passed",
                    "sIntegrity": "passed",
                    "sQualitative": "passed",
                    "sQuantitative": "passed",
                },
            },
            {
                "sName": "AI", "sDirectory": "AI",
                "sStepKind": S_AI_DECLARATION_STEP_KIND,
                "dictVerification": {
                    "sUser": "passed", "sUnitTest": "passed",
                    "sIntegrity": "passed",
                    "sQualitative": "passed",
                    "sQuantitative": "passed",
                },
            },
        ],
        "dictRemotes": {
            "github": {
                "sOwner": "u", "sRepo": "r", "sBranch": "main",
                "sCommittedSha": "abc123",
            },
            "zenodo": {
                "sRecordId": "1234", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
        },
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "bNoStandaloneBinaries": True,
        "listDeclaredBinaries": [],
    }


@pytest.fixture
def fixtureLevel3Repo(tmp_path):
    """Seed a project repo that satisfies every L1, L2, L3 criterion."""
    _fnWriteSyncStatus(tmp_path)
    _fnWriteL3EnvelopeFiles(tmp_path)
    _fnWriteManifest(
        tmp_path, [S_REPRODUCE_SCRIPT_FILENAME, S_DOCKERFILE_FILENAME],
    )
    return tmp_path


def test_end_to_end_l3_with_attestation(fixtureLevel3Repo):
    dictWorkflow = _fdictBuildLevel3Workflow()
    sRepo = str(fixtureLevel3Repo)
    # Without attestation we cap at L2 even with full readiness.
    assert fiAICSLevel(dictWorkflow, sRepo) == 2
    # Write a passing attestation.
    sDigest = fsCurrentManifestDigest(sRepo)
    fnWriteAttestation(sRepo, fdictBuildAttestation(
        S_STATUS_PASSED, sDigest, "img@sha256:" + "c" * 64,
        12.0, 2, 2, [], "",
    ))
    assert fbAtLeastLevel3(dictWorkflow, sRepo)
    assert fiAICSLevel(dictWorkflow, sRepo) == 3
    # Mutate the manifest — attestation goes stale, gate falls to L2.
    (fixtureLevel3Repo / "MANIFEST.sha256").write_text(
        "# changed\n"
    )
    assert not fbAtLeastLevel3(dictWorkflow, sRepo)
    # With L3 readiness now also failing (no entries), level drops to L2
    assert fiAICSLevel(dictWorkflow, sRepo) == 2
