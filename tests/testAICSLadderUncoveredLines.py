"""Targeted unit tests for AICS-ladder lines uncovered by Phase 1-3 tests.

Each test exercises one or more lines flagged as missing by the
coverage run on commits 6f91f9c (Phase 1), eec96c4 (Phase 2),
and db9771f (Phase 3). The tests document a deliberate audit pass:
defensive guards, error branches, and shape-preserving fallbacks
are all reachable and tested here rather than implicitly defended.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import scheduledReverify
from vaibify.reproducibility.aiDeclarationStep import (
    S_DEFAULT_DECLARATION_FILENAME,
    fbDeclarationFileExists,
    fnWriteDeclarationTemplate,
)
from vaibify.reproducibility.determinismGate import (
    flistAuditScriptAntiPatterns,
    flistAuditWorkflow,
)
from vaibify.reproducibility.dockerfileLint import (
    flistCheckAptVersionPins,
)
from vaibify.reproducibility.environmentSnapshot import (
    _fsExtractImageDigest,
    fbEnvironmentDigestPinned,
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.l3Attestation import (
    S_STATUS_PASSED,
    fdictBuildAttestation,
    fdictReadAttestation,
    flistReadAttestationHistory,
    fnInvalidateAttestation,
    fnWriteAttestation,
)
from vaibify.reproducibility.levelGates import (
    F_MAX_STALE_HOURS,
    _fbCachedSyncStatusFresh,
    fbAtLeastLevel2,
    fbL3ReadinessOK,
    fbVerifyManifestComplete,
    fbVerifyReproduceScript,
    fbWorkflowFullySyncedWithGithub,
    fbWorkflowFullySyncedWithZenodo,
    fbWorkflowHasAiDeclarationStep,
)
from vaibify.reproducibility.reproduceScriptGenerator import (
    flistRenderStepCommands,
)
from vaibify.reproducibility.scheduledReverify import (
    ReverifyConfigError,
    _fdictEmptyServiceStatus,
    _fdictRequireServiceConfig,
    _fnBackfillServiceIdentityFields,
    fdictReadCachedSyncStatus,
)


# ============================================================================
# aiDeclarationStep.py
# ============================================================================


def test_fbDeclarationFileExists_empty_project_repo_returns_false():
    """Line 79: empty sProjectRepoPath must short-circuit to False."""
    assert fbDeclarationFileExists("", "AI_USAGE.md") is False


def test_fbDeclarationFileExists_empty_relative_path_returns_false():
    """Line 79: empty sRelativePath must short-circuit to False."""
    assert fbDeclarationFileExists("/tmp", "") is False


def test_fnWriteDeclarationTemplate_rejects_empty_project_repo():
    """Validation: empty project repo raises ValueError before any IO."""
    with pytest.raises(ValueError, match="sProjectRepoPath"):
        fnWriteDeclarationTemplate("", "AI_USAGE.md")


def test_fnWriteDeclarationTemplate_rejects_empty_relative_path(tmp_path):
    """Validation: empty relative path raises ValueError before any IO."""
    with pytest.raises(ValueError, match="sRelativePath"):
        fnWriteDeclarationTemplate(str(tmp_path), "")


def test_fnWriteDeclarationTemplate_refuses_to_overwrite(tmp_path):
    """An existing file must raise FileExistsError rather than truncate."""
    pathTarget = tmp_path / S_DEFAULT_DECLARATION_FILENAME
    pathTarget.write_text("# pre-existing content\n")
    with pytest.raises(FileExistsError):
        fnWriteDeclarationTemplate(
            str(tmp_path), S_DEFAULT_DECLARATION_FILENAME,
        )
    assert pathTarget.read_text() == "# pre-existing content\n"


# ============================================================================
# determinismGate.py
# ============================================================================


def test_clock_seed_with_syntax_error_returns_empty_list(tmp_path):
    """Lines 73-74: SyntaxError on parse must yield no clock-seed issues."""
    pathScript = tmp_path / "broken.py"
    pathScript.write_text("def broken( :\n")
    listIssues = flistAuditScriptAntiPatterns(str(pathScript))
    sJoined = "\n".join(listIssues)
    assert "clock" not in sJoined.lower()


def test_seed_call_with_bare_name_function(tmp_path):
    """Line 99: seed(...) called as a bare Name (not Attribute)."""
    pathScript = tmp_path / "barename.py"
    pathScript.write_text(
        "from random import seed\n"
        "import time\n"
        "seed(time.time())\n"
    )
    listIssues = flistAuditScriptAntiPatterns(str(pathScript))
    assert any("clock" in sIssue for sIssue in listIssues)


def test_clock_attribute_on_unimported_module_is_not_flagged(tmp_path):
    """Line 123: attribute on a non-clock module Name must not match."""
    pathScript = tmp_path / "notclock.py"
    pathScript.write_text(
        "import random\n"
        "import notTime\n"
        "random.seed(notTime.time())\n"
    )
    listIssues = flistAuditScriptAntiPatterns(str(pathScript))
    assert all("clock" not in sIssue.lower() for sIssue in listIssues)


def test_seed_call_via_indirect_function_returns_false(tmp_path):
    """Line 99: a seed-like call whose func is not Name or Attribute is skipped.

    Example: ``(get_seeder())(time.time())`` — the outer call's func
    is itself a Call. The helper must return False so the analyser
    does not misclassify it as a seed function.
    """
    pathScript = tmp_path / "indirectseed.py"
    pathScript.write_text(
        "import time\n"
        "def get_seeder():\n"
        "    def seed(arg):\n"
        "        pass\n"
        "    return seed\n"
        "(get_seeder())(time.time())\n"
    )
    listIssues = flistAuditScriptAntiPatterns(str(pathScript))
    # No "clock" issue since the outer call isn't recognised as seed.
    assert all("clock" not in sIssue.lower() for sIssue in listIssues)


def test_clock_attribute_with_non_name_root_returns_false(tmp_path):
    """Line 123: an attribute whose root is a Call (not Name) must not match."""
    pathScript = tmp_path / "nonameroot.py"
    pathScript.write_text(
        "import random\n"
        "import time\n"
        "random.seed(get_module().time())\n"  # root is a Call, not Name
    )
    listIssues = flistAuditScriptAntiPatterns(str(pathScript))
    # The .time attribute lookup has a Call as its base; not flagged.
    assert all("clock" not in sIssue.lower() for sIssue in listIssues)


def test_urandom_call_via_non_attribute_name_is_not_clock_match(tmp_path):
    """Line 130: bare urandom() (not os.urandom) must not match _fbCallIsOsUrandom."""
    pathScript = tmp_path / "bareurandom.py"
    pathScript.write_text(
        "from os import urandom\n"
        "import random\n"
        "random.seed(urandom(4))\n"
    )
    # We're verifying the AST helper does not falsely match a bare Call.
    listIssues = flistAuditScriptAntiPatterns(str(pathScript))
    # The os.urandom regex still fires on the literal text — not the AST guard.
    sJoined = "\n".join(listIssues)
    assert "os.urandom" not in sJoined  # bare urandom() doesn't match regex.


def test_flistAuditWorkflow_skips_corrupt_steps():
    """Line 213: a non-dict entry in listSteps must be skipped, not crashed on."""
    dictWorkflow = {
        "dictDeterminism": {"bAcceptBlasVariance": True},
        "listSteps": [None, "not a dict", {"sName": "S"}],
    }
    listIssues = flistAuditWorkflow(dictWorkflow)
    # Should not raise; should not flag corrupt entries as RNG-warned.
    assert all("None" not in sIssue for sIssue in listIssues)


# ============================================================================
# dockerfileLint.py
# ============================================================================


def test_apt_install_continuation_block_finalizes_at_eof():
    """Line 136: a continued apt-install line ending the file must still finalize."""
    listLines = [
        "RUN apt-get install -y \\",
        "    pkg=1.0 \\",
        "    other=2.0 \\",
    ]
    # No final non-continuation line; helper still emits one logical entry.
    listIssues = flistCheckAptVersionPins(listLines)
    # The pinned packages are clean — no issues should appear.
    assert listIssues == []


def test_apt_payload_with_no_install_keyword_returns_empty():
    """Line 164: a logical line missing 'install' returns no payload tokens.

    The function is internal but exercised through ``flistCheckAptVersionPins``
    when the regex's first-match flushes a non-install line.
    """
    listLines = ["RUN apt-get update"]
    # No install keyword → no logical apt block at all.
    listIssues = flistCheckAptVersionPins(listLines)
    assert listIssues == []


def test_apt_line_with_no_hash_is_kept_intact():
    """Line 174 vicinity: _fsStripLineComment with no '#' returns line unchanged."""
    listLines = ["RUN apt-get install -y curl=7.0"]
    listIssues = flistCheckAptVersionPins(listLines)
    assert listIssues == []  # curl=7.0 is pinned, no issues


def test_apt_line_with_trailing_comment_is_stripped():
    """Line 174: lines containing '#' are sliced before the marker."""
    # The marker `# allow-unpinned` must waive the unpinned `curl` package.
    listLines = ["RUN apt-get install -y curl  # allow-unpinned"]
    listIssues = flistCheckAptVersionPins(listLines)
    assert listIssues == []


def test_apt_payload_extraction_after_comment_strip_no_install():
    """Line 164: when comment-stripping removes the 'install' keyword, return ''.

    A line like ``# apt-get install foo`` is fully-commented; the
    payload extractor's _REGEX_APT_INSTALL.search must return None
    and the helper must return an empty string, which causes the
    outer linter to emit no per-package issues.
    """
    # The outer flistCheckAptVersionPins detects the keyword first
    # via _flistLogicalAptInstallLines, so to hit line 164 we exercise
    # the helper directly with a logical line whose stripped form
    # no longer contains the install keyword.
    from vaibify.reproducibility.dockerfileLint import _fsExtractAptPayload
    sLine = "# apt-get install foo bar"
    assert _fsExtractAptPayload(sLine) == ""


# ============================================================================
# l3Attestation.py
# ============================================================================


def test_fdictReadAttestation_malformed_json_returns_none(tmp_path):
    """Lines 77-78: an unparseable JSON file maps to None."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    pathFile = pathDir / "l3_attestation.json"
    pathFile.write_text("{ not valid json")
    assert fdictReadAttestation(str(tmp_path)) is None


def test_fdictReadAttestation_non_dict_payload_returns_none(tmp_path):
    """Line 80: a JSON list or scalar at top level must map to None."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    pathFile = pathDir / "l3_attestation.json"
    pathFile.write_text("[1, 2, 3]")
    assert fdictReadAttestation(str(tmp_path)) is None


def test_fbL3AttestationCurrent_missing_recorded_digest_is_false(tmp_path):
    """Line 98: attestation passed but empty manifest digest field is stale."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    dictAttestation = fdictBuildAttestation(
        S_STATUS_PASSED, "", "img@sha256:def",
        1.0, 1, 1, [], "",
    )
    fnWriteAttestation(str(tmp_path), dictAttestation)
    from vaibify.reproducibility.l3Attestation import fbL3AttestationCurrent
    assert fbL3AttestationCurrent(str(tmp_path)) is False


def test_fnInvalidateAttestation_returns_false_when_missing(tmp_path):
    """Line 158: an absent attestation file makes invalidate a no-op (False)."""
    assert fnInvalidateAttestation(str(tmp_path)) is False


def test_fnInvalidateAttestation_returns_false_when_os_remove_fails(tmp_path):
    """Lines 161-162: an OSError during os.remove returns False, not raise."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "l3_attestation.json").write_text("{}")
    with patch(
        "vaibify.reproducibility.l3Attestation.os.remove",
        side_effect=OSError("permission denied"),
    ):
        assert fnInvalidateAttestation(str(tmp_path)) is False


def test_flistReadAttestationHistory_missing_directory_is_empty(tmp_path):
    """Line 176: absent history directory returns []."""
    assert flistReadAttestationHistory(str(tmp_path)) == []


def test_flistReadAttestationHistory_skips_malformed_entries(tmp_path):
    """Lines 182-183: a malformed history file is skipped, not raised."""
    pathHistoryDir = tmp_path / ".vaibify" / "l3_attestations"
    pathHistoryDir.mkdir(parents=True, exist_ok=True)
    # Two files: one good, one malformed.
    (pathHistoryDir / "20260101T000000Z_passed.json").write_text(
        json.dumps({"sStatus": "passed", "sAttestedAtUtc": "2026-01-01T00:00:00Z"})
    )
    (pathHistoryDir / "20260102T000000Z_passed.json").write_text(
        "{ malformed"
    )
    listHistory = flistReadAttestationHistory(str(tmp_path))
    assert len(listHistory) == 1


def test_atomic_write_cleans_up_temp_on_failure(tmp_path):
    """Lines 196-201: an OSError during write must remove the .tmp file."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    dictAttestation = fdictBuildAttestation(
        S_STATUS_PASSED, "sha256:abc", "img@sha256:def",
        1.0, 1, 1, [], "",
    )
    with patch(
        "vaibify.reproducibility.l3Attestation.os.replace",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError):
            fnWriteAttestation(str(tmp_path), dictAttestation)
    listLeftovers = [
        sName for sName in os.listdir(str(pathDir))
        if sName.endswith(".tmp")
    ]
    # The exception cleanup path removes the temp file.
    assert listLeftovers == []


def test_atomic_write_cleanup_tolerates_missing_temp(tmp_path):
    """Lines 196-201: the cleanup branch must also handle a missing .tmp file.

    When os.replace fails AND the temp file is also already gone, the
    nested ``os.remove`` would raise OSError; that secondary failure is
    deliberately swallowed via the try/except OSError: pass branch so
    the original write error is what surfaces.
    """
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    dictAttestation = fdictBuildAttestation(
        S_STATUS_PASSED, "sha256:abc", "img@sha256:def",
        1.0, 1, 1, [], "",
    )
    with patch(
        "vaibify.reproducibility.l3Attestation.os.replace",
        side_effect=OSError("disk full"),
    ), patch(
        "vaibify.reproducibility.l3Attestation.os.remove",
        side_effect=OSError("temp missing"),
    ):
        with pytest.raises(OSError, match="disk full"):
            fnWriteAttestation(str(tmp_path), dictAttestation)


# ============================================================================
# environmentSnapshot.py
# ============================================================================


def test_parse_repo_digests_returns_none_when_no_at_sha256():
    """Line 98: a repo-digest output without '@sha256:' returns None."""
    from vaibify.reproducibility.environmentSnapshot import _fsParseRepoDigests
    assert _fsParseRepoDigests("[image:latest]") is None


def test_libc_version_returns_none_when_both_empty(monkeypatch):
    """Lines 205-206: platform.libc_ver returns ('','') maps to None."""
    from vaibify.reproducibility.environmentSnapshot import _fsCaptureLibcVersion
    monkeypatch.setattr(
        "vaibify.reproducibility.environmentSnapshot.platform.libc_ver",
        lambda: ("", ""),
    )
    assert _fsCaptureLibcVersion() is None


def test_libc_version_returns_none_on_exception(monkeypatch):
    """Lines 205-206: an OSError from libc_ver maps to None."""
    from vaibify.reproducibility.environmentSnapshot import _fsCaptureLibcVersion

    def _fnRaise():
        raise OSError("libc gone")

    monkeypatch.setattr(
        "vaibify.reproducibility.environmentSnapshot.platform.libc_ver",
        _fnRaise,
    )
    assert _fsCaptureLibcVersion() is None


def test_read_os_release_returns_none_when_missing(monkeypatch):
    """Line 220-221: a missing /etc/os-release maps to None."""
    from vaibify.reproducibility.environmentSnapshot import _fsReadOsRelease
    monkeypatch.setattr(
        "vaibify.reproducibility.environmentSnapshot._OS_RELEASE_PATH",
        "/this/path/does/not/exist/os-release-xyz",
    )
    assert _fsReadOsRelease() is None


def test_read_os_release_returns_none_on_os_error(monkeypatch, tmp_path):
    """Lines 220-221: an OSError reading os-release maps to None."""
    from vaibify.reproducibility.environmentSnapshot import _fsReadOsRelease
    # Make the file appear to exist but error on read.
    pathFake = tmp_path / "os-release"
    pathFake.write_text("PRETTY_NAME=Test\n")
    monkeypatch.setattr(
        "vaibify.reproducibility.environmentSnapshot._OS_RELEASE_PATH",
        str(pathFake),
    )
    with patch(
        "pathlib.Path.read_text",
        side_effect=OSError("io error"),
    ):
        assert _fsReadOsRelease() is None


def test_fdictReadEnvironmentJson_missing_returns_none(tmp_path):
    """Line 284: missing file returns None."""
    assert fdictReadEnvironmentJson(str(tmp_path)) is None


def test_fdictReadEnvironmentJson_malformed_returns_none(tmp_path):
    """Lines 288-289: invalid JSON returns None."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text("{ not json")
    assert fdictReadEnvironmentJson(str(tmp_path)) is None


def test_fdictReadEnvironmentJson_non_dict_returns_none(tmp_path):
    """Line 291: a top-level JSON list returns None."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text("[1, 2, 3]")
    assert fdictReadEnvironmentJson(str(tmp_path)) is None


def test_fbEnvironmentDigestPinned_floating_tag_fails(tmp_path):
    """Lines 307, 310: a value without '@sha256:' fails honestly."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(
        json.dumps({"sImageDigest": "python:3.11"})
    )
    assert fbEnvironmentDigestPinned(str(tmp_path)) is False


def test_fbEnvironmentDigestPinned_empty_digest_fails(tmp_path):
    """Lines 307, 310: empty sImageDigest fails."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(
        json.dumps({"sImageDigest": ""})
    )
    assert fbEnvironmentDigestPinned(str(tmp_path)) is False


def test_fsExtractImageDigest_picks_nested_first():
    """Nested dictContainer.sImageDigest takes precedence over the flat key."""
    dictPayload = {
        "dictContainer": {"sImageDigest": "nested@sha256:abc"},
        "sImageDigest": "flat@sha256:def",
    }
    assert _fsExtractImageDigest(dictPayload) == "nested@sha256:abc"


def test_fsExtractImageDigest_falls_back_to_flat():
    """When nested is empty, returns the flat key."""
    dictPayload = {
        "dictContainer": {"sImageDigest": ""},
        "sImageDigest": "flat@sha256:def",
    }
    assert _fsExtractImageDigest(dictPayload) == "flat@sha256:def"


def test_fsExtractImageDigest_no_dict_container_falls_back():
    """When dictContainer is not a dict, returns the flat key."""
    dictPayload = {
        "dictContainer": "not a dict",
        "sImageDigest": "flat@sha256:def",
    }
    assert _fsExtractImageDigest(dictPayload) == "flat@sha256:def"


# ============================================================================
# reproduceScriptGenerator.py
# ============================================================================


def test_render_step_commands_skips_corrupt_steps():
    """Line 107: a non-dict step is silently skipped."""
    dictWorkflow = {
        "listSteps": [
            None,
            "not a dict",
            {"sName": "S", "saDataCommands": ["python x.py"]},
        ],
    }
    listLines = flistRenderStepCommands(dictWorkflow)
    # Only the third (dict) step contributes; corrupt entries are skipped.
    assert any("python x.py" in sLine for sLine in listLines)
    # The corrupt entries did not raise.


def test_render_step_commands_handles_none_workflow():
    """Defensive: a None workflow returns no lines."""
    assert flistRenderStepCommands(None) == []


# ============================================================================
# scheduledReverify.py — Phase-2 service-identity-field additions
# ============================================================================


def test_fdictReadCachedSyncStatus_missing_file_returns_empty(tmp_path):
    """A never-created syncStatus.json returns the empty service shape."""
    dictStatus = fdictReadCachedSyncStatus(str(tmp_path), "github")
    assert dictStatus["sService"] == "github"
    assert dictStatus["sLastVerified"] is None
    assert dictStatus["sCommittedShaVerified"] is None


def test_fdictReadCachedSyncStatus_malformed_returns_empty(tmp_path):
    """Lines 264-265: malformed JSON falls back to empty service shape."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "syncStatus.json").write_text("{ not json")
    dictStatus = fdictReadCachedSyncStatus(str(tmp_path), "zenodo")
    assert dictStatus["sService"] == "zenodo"
    assert dictStatus["sZenodoDoi"] is None
    assert dictStatus["sEndpointVerified"] is None


def test_fdictReadCachedSyncStatus_non_dict_entry_returns_empty(tmp_path):
    """Line 268: a list-typed service entry returns the empty shape."""
    pathDir = tmp_path / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "syncStatus.json").write_text(
        json.dumps({"github": ["not", "a", "dict"]})
    )
    dictStatus = fdictReadCachedSyncStatus(str(tmp_path), "github")
    assert dictStatus["sLastVerified"] is None


def test_fnBackfillServiceIdentityFields_idempotent_for_github():
    """Existing keys must not be overwritten by setdefault."""
    dictEntry = {"sCommittedShaVerified": "preserved-sha"}
    _fnBackfillServiceIdentityFields(dictEntry, "github")
    assert dictEntry["sCommittedShaVerified"] == "preserved-sha"


def test_fnBackfillServiceIdentityFields_adds_missing_zenodo_fields():
    """Empty zenodo entry gets backfilled with the Phase 2 fields."""
    dictEntry = {}
    _fnBackfillServiceIdentityFields(dictEntry, "zenodo")
    assert "sZenodoDoi" in dictEntry
    assert "sEndpointVerified" in dictEntry


def test_fnBackfillServiceIdentityFields_unknown_service_no_op():
    """A service not in {github, zenodo} adds no identity fields."""
    dictEntry = {}
    _fnBackfillServiceIdentityFields(dictEntry, "overleaf")
    assert dictEntry == {}


def test_fdictEmptyServiceStatus_github_has_identity_field():
    """Phase 2 contract: github default carries sCommittedShaVerified."""
    dictEmpty = _fdictEmptyServiceStatus("github")
    assert "sCommittedShaVerified" in dictEmpty
    assert dictEmpty["sCommittedShaVerified"] is None


def test_fdictEmptyServiceStatus_zenodo_has_identity_fields():
    """Phase 2 contract: zenodo default carries doi + endpoint."""
    dictEmpty = _fdictEmptyServiceStatus("zenodo")
    assert dictEmpty["sZenodoDoi"] is None
    assert dictEmpty["sEndpointVerified"] is None


def test_fdictEmptyServiceStatus_other_service_no_identity_fields():
    """A non-github/zenodo service does not get Phase 2 fields."""
    dictEmpty = _fdictEmptyServiceStatus("overleaf")
    assert "sCommittedShaVerified" not in dictEmpty
    assert "sZenodoDoi" not in dictEmpty


def test_fdictRequireServiceConfig_unsupported_service_raises():
    """Line 90: an unknown service raises ReverifyConfigError."""
    with pytest.raises(ReverifyConfigError, match="Unsupported service"):
        _fdictRequireServiceConfig({}, "unknown_service_xyz")


# ============================================================================
# levelGates.py — uncovered branches
# ============================================================================


def _fdictAllGreenStep(sStepKind=None):
    """Return one L1-satisfying step, with optional sStepKind."""
    dictStep = {
        "sName": "A", "sDirectory": "A",
        "dictVerification": {
            "sUser": "passed",
            "sUnitTest": "passed",
            "sIntegrity": "passed",
            "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }
    if sStepKind:
        dictStep["sStepKind"] = sStepKind
    return dictStep


def _fsBuildIsoTimestamp(fHoursAgo=0.0):
    """Return an ISO-8601 UTC timestamp fHoursAgo before now."""
    dtNow = datetime.now(timezone.utc) - timedelta(hours=fHoursAgo)
    return dtNow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fnWriteSyncStatusFile(sProjectRepo, dictPerService):
    """Write a sample syncStatus.json under .vaibify/."""
    sDir = os.path.join(sProjectRepo, ".vaibify")
    os.makedirs(sDir, exist_ok=True)
    sPath = os.path.join(sDir, "syncStatus.json")
    with open(sPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictPerService, fileHandle)


def test_fbAtLeastLevel2_missing_zenodo_sync_blocks(tmp_path):
    """Line 150: GitHub-synced but Zenodo-unsynced workflow must fail L2."""
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
        # Zenodo deliberately absent.
    })
    dictWorkflow = {
        "listSteps": [
            _fdictAllGreenStep(),
            _fdictAllGreenStep(sStepKind="ai-declaration"),
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
    }
    assert fbAtLeastLevel2(dictWorkflow, sProjectRepo) is False


def test_fbAtLeastLevel2_missing_ai_declaration_blocks(tmp_path):
    """Line 150: an L1-clean workflow without an AI declaration step fails L2.

    The composition exits via the line-152 return False; we must hit it.
    """
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 3, "iMatching": 3, "listDiverged": [],
            "sCommittedShaVerified": "abc123",
        },
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    })
    dictWorkflow = {
        "listSteps": [_fdictAllGreenStep()],  # No ai-declaration kind here
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
    }
    assert fbAtLeastLevel2(dictWorkflow, sProjectRepo) is False


def test_fbL3ReadinessOK_empty_repo_returns_false():
    """Line 185: empty sProjectRepoPath short-circuits to False."""
    assert fbL3ReadinessOK({"listSteps": []}, "") is False


def test_fbVerifyManifestComplete_file_not_found_returns_false(tmp_path):
    """Lines 210-211 region (line 209 actually): missing manifest fails honestly."""
    assert fbVerifyManifestComplete(str(tmp_path), {"listSteps": []}) is False


def test_fbVerifyManifestComplete_corrupt_manifest_returns_false(tmp_path):
    """Lines 210-211: a corrupt manifest (ValueError) returns False."""
    pathManifest = tmp_path / "MANIFEST.sha256"
    # An entry with the wrong column count to trigger a parser ValueError.
    pathManifest.write_text("not-a-valid-hash-line\n")
    # The parser silently skips malformed lines, so the result is an empty
    # manifest, which is still "complete" w/r/t an empty workflow.
    # But coverage targets the except block — patch to force ValueError.
    with patch(
        "vaibify.reproducibility.levelGates.flistDeclaredButMissingFromManifest",
        side_effect=ValueError("corrupt"),
    ):
        assert fbVerifyManifestComplete(
            str(tmp_path), {"listSteps": []},
        ) is False


def test_fbVerifyManifestComplete_os_error_returns_false(tmp_path):
    """Lines 210-211: an OSError from the parser surfaces as False."""
    with patch(
        "vaibify.reproducibility.levelGates.flistDeclaredButMissingFromManifest",
        side_effect=OSError("io"),
    ):
        assert fbVerifyManifestComplete(
            str(tmp_path), {"listSteps": []},
        ) is False


def test_fbVerifyReproduceScript_manifest_value_error_returns_false(tmp_path):
    """Lines 245-246: ValueError from manifest parser is captured."""
    (tmp_path / "reproduce.sh").write_text("#!/usr/bin/env bash\n")
    with patch(
        "vaibify.reproducibility.levelGates.flistParseManifestLines",
        side_effect=ValueError("corrupt"),
    ):
        assert fbVerifyReproduceScript(
            str(tmp_path), {"listSteps": []},
        ) is False


def test_fbVerifyReproduceScript_manifest_os_error_returns_false(tmp_path):
    """Lines 245-246: OSError from manifest parser is captured."""
    (tmp_path / "reproduce.sh").write_text("#!/usr/bin/env bash\n")
    with patch(
        "vaibify.reproducibility.levelGates.flistParseManifestLines",
        side_effect=OSError("io"),
    ):
        assert fbVerifyReproduceScript(
            str(tmp_path), {"listSteps": []},
        ) is False


def test_fbWorkflowHasAiDeclarationStep_non_dict_workflow_is_false():
    """Line 316: a non-dict workflow input must return False."""
    assert fbWorkflowHasAiDeclarationStep(None) is False
    assert fbWorkflowHasAiDeclarationStep("string") is False


def test_cached_sync_status_fresh_empty_dict_returns_false():
    """Line 332: an empty dict has no timestamp → not fresh."""
    assert _fbCachedSyncStatusFresh({}, F_MAX_STALE_HOURS) is False


def test_cached_sync_status_fresh_malformed_timestamp_returns_false():
    """Lines 336-337: TypeError/ValueError from fromisoformat → not fresh."""
    dictStatus = {"sLastVerified": "not a real iso timestamp"}
    assert _fbCachedSyncStatusFresh(dictStatus, F_MAX_STALE_HOURS) is False


def test_cached_sync_status_fresh_naive_timestamp_treated_as_utc():
    """Line 339: a naive datetime gets tzinfo UTC attached before subtraction."""
    # A naive timestamp 1 hour ago must be considered fresh (within 24h budget).
    sIso = (
        datetime.now(timezone.utc) - timedelta(hours=1.0)
    ).strftime("%Y-%m-%dT%H:%M:%S")  # No timezone suffix → naive.
    dictStatus = {"sLastVerified": sIso}
    assert _fbCachedSyncStatusFresh(dictStatus, F_MAX_STALE_HOURS) is True


def test_cached_sync_status_full_match_with_empty_dict():
    """Line 349: an empty/falsy dictStatus returns False."""
    from vaibify.reproducibility.levelGates import _fbCachedSyncStatusFullMatch
    assert _fbCachedSyncStatusFullMatch({}) is False
    assert _fbCachedSyncStatusFullMatch(None) is False


def test_cached_sync_status_full_match_zero_total_returns_false():
    """The iTotal==0 branch (right before line 356) returns False."""
    from vaibify.reproducibility.levelGates import _fbCachedSyncStatusFullMatch
    dictStatus = {"iTotalFiles": 0, "iMatching": 0, "listDiverged": []}
    assert _fbCachedSyncStatusFullMatch(dictStatus) is False


def test_cached_sync_status_full_match_diverged_blocks():
    """Line 356: a non-empty listDiverged returns False."""
    from vaibify.reproducibility.levelGates import _fbCachedSyncStatusFullMatch
    dictStatus = {
        "iTotalFiles": 3, "iMatching": 3,
        "listDiverged": [{"sPath": "x"}],
    }
    assert _fbCachedSyncStatusFullMatch(dictStatus) is False


def test_fbGithubHeadMatchesVerifiedSha_both_empty_is_permissive(tmp_path):
    """Line 400: when both verified and live SHA are empty, treat as match."""
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "github": {
            "sService": "github",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 1, "iMatching": 1, "listDiverged": [],
            "sCommittedShaVerified": "",
        },
    })
    dictWorkflow = {
        "listSteps": [_fdictAllGreenStep()],
        "dictRemotes": {
            "github": {
                "sOwner": "u", "sRepo": "r", "sBranch": "main",
            },
        },
    }
    assert fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepo,
    ) is True


def test_fbWorkflowFullySyncedWithZenodo_stale_returns_false(tmp_path):
    """Line 422: a stale Zenodo verify returns False."""
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=72.0),  # > 24h
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "sandbox",
        },
    })
    dictWorkflow = {
        "listSteps": [_fdictAllGreenStep()],
        "dictRemotes": {
            "zenodo": {
                "sRecordId": "1234", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
        },
    }
    assert fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepo,
    ) is False


def test_fbZenodoEndpointMatches_empty_verified_endpoint_returns_false(tmp_path):
    """Line 439: an empty sEndpointVerified blocks the gate."""
    sProjectRepo = str(tmp_path)
    _fnWriteSyncStatusFile(sProjectRepo, {
        "zenodo": {
            "sService": "zenodo",
            "sLastVerified": _fsBuildIsoTimestamp(fHoursAgo=1.0),
            "iTotalFiles": 2, "iMatching": 2, "listDiverged": [],
            "sZenodoDoi": "10.1000/example",
            "sEndpointVerified": "",  # Empty
        },
    })
    dictWorkflow = {
        "listSteps": [_fdictAllGreenStep()],
        "dictRemotes": {
            "zenodo": {
                "sRecordId": "1234", "sService": "sandbox",
                "sDoi": "10.1000/example",
            },
        },
    }
    assert fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepo,
    ) is False
