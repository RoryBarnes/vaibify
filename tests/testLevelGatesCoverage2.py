"""Direct-function coverage for uncovered branches in levelGates.

Every test here calls a gate/helper function directly with a crafted
workflow dict and either a repo-path string, a real ``tmp_path`` repo
root, or a small fake repo adapter, and asserts the concrete return
value (a blocker list, a boolean, a fingerprint string, a requirement
breakdown). The fake adapter mocks only the true external — repo file
IO — never the unit under test. These exercise the defensive and
error branches the happy-path suites never reach: malformed step
entries, missing repo roots, hash-adapter failures, and the arXiv
client raising.
"""

import pytest

from vaibify.reproducibility import levelGates as lg


class FakeRepo:
    """Minimal repoFiles adapter double.

    Only ``sRootPath`` plus the two hash methods the gate helpers
    touch are modelled; a truthy ``bRaiseHash`` makes both hash
    methods raise so the conservative error branches run.
    """

    def __init__(self, sRootPath="/repo", dictHashes=None, bRaiseHash=False):
        self.sRootPath = sRootPath
        self._dictHashes = dictHashes or {}
        self._bRaiseHash = bRaiseHash

    def fdictHashFiles(self, listRelPaths):
        if self._bRaiseHash:
            raise RuntimeError("hash adapter unavailable")
        return self._dictHashes

    def fdictHashAbsolutePaths(self, listAbsolutePaths):
        if self._bRaiseHash:
            raise RuntimeError("hash adapter unavailable")
        return self._dictHashes


# ------------------------------------------------------------------
# Blocker-list cache
# ------------------------------------------------------------------


def test_blocker_cache_store_updates_existing_key_value():
    lg.fnClearLevelBlockerCache()
    tKey = ("L1", "fp", "mt", "root", "scripts")
    lg._fnBlockerCacheStore(tKey, ["first"])
    lg._fnBlockerCacheStore(tKey, ["second"])
    assert lg._flistBlockerCacheLookup(tKey) == ["second"]
    lg.fnClearLevelBlockerCache()


def test_blocker_cache_evicts_least_recently_used():
    lg.fnClearLevelBlockerCache()
    for iIndex in range(lg._I_BLOCKER_CACHE_MAX_ENTRIES + 3):
        lg._fnBlockerCacheStore((f"k{iIndex}",), [iIndex])
    assert len(lg._DICT_BLOCKER_CACHE) == lg._I_BLOCKER_CACHE_MAX_ENTRIES
    assert lg._flistBlockerCacheLookup(("k0",)) is None
    lg.fnClearLevelBlockerCache()


# ------------------------------------------------------------------
# Sync-status fingerprint
# ------------------------------------------------------------------


def test_sync_fingerprint_is_deterministic_for_the_same_state(tmp_path):
    # With no sync cache written, the fingerprint is a stable hex digest
    # (not a literal "none"): the same repo state hashes identically, so
    # the gate can detect when sync status actually changes.
    sFirst = lg._fsSyncStatusFingerprint(str(tmp_path))
    sSecond = lg._fsSyncStatusFingerprint(str(tmp_path))
    assert sFirst == sSecond
    assert isinstance(sFirst, str) and len(sFirst) >= 16


def test_sync_fingerprint_tolerates_read_error(monkeypatch, tmp_path):
    def fnRaise(filesRepo, sService):
        raise RuntimeError("cache read blew up")

    monkeypatch.setattr(
        lg.scheduledReverify, "fdictReadCachedSyncStatus", fnRaise,
    )
    # Each service read raises -> caught -> no data -> "none".
    assert lg._fsSyncStatusFingerprint(str(tmp_path)) == "none"


def test_sync_fingerprint_hashes_present_status(monkeypatch, tmp_path):
    def fnStatus(filesRepo, sService):
        return {"sLastVerified": "2026-01-01T00:00:00+00:00"} \
            if sService == "github" else {}

    monkeypatch.setattr(
        lg.scheduledReverify, "fdictReadCachedSyncStatus", fnStatus,
    )
    sFingerprint = lg._fsSyncStatusFingerprint(str(tmp_path))
    assert sFingerprint != "none" and len(sFingerprint) == 64


# ------------------------------------------------------------------
# L1 blockers — empty and degenerate workflows
# ------------------------------------------------------------------


def test_level1_blockers_empty_when_no_steps(tmp_path):
    listBlockers = lg.flistLevel1Blockers(
        {"listSteps": []}, {}, str(tmp_path),
    )
    assert listBlockers == []


def test_level1_blockers_empty_when_no_repo():
    listBlockers = lg.flistLevel1Blockers(
        {"listSteps": [{"sName": "A"}]}, {}, "",
    )
    assert listBlockers == []


# ------------------------------------------------------------------
# axis-not-green hint fallback
# ------------------------------------------------------------------


def test_axis_not_green_hint_falls_back_for_uncategorized_value():
    dictStep = {"dictVerification": {"sUnitTest": "pending"}}
    assert lg._fsAxisNotGreenSubState(dictStep) == "untested"
    assert lg._fsAxisNotGreenHint(dictStep) == (
        "Re-run failing tests, then verify"
    )


def test_axis_not_green_hint_names_untested_category():
    dictStep = {"dictVerification": {"sQualitative": "untested"}}
    assert "qualitative" in lg._fsAxisNotGreenHint(dictStep)


# ------------------------------------------------------------------
# repo-relative mapping / output resolution
# ------------------------------------------------------------------


def test_repo_relative_by_raw_path_empty_without_root():
    assert lg._fdictRepoRelativeByRawPath({}, "", ["x.dat"]) == {}


def test_step_outputs_repo_relative_skips_empty_entries(tmp_path):
    dictStep = {
        "sDirectory": "StepA",
        "saOutputDataFiles": ["", "result.dat"],
        "saPlotFiles": [],
    }
    listRelative = lg._flistStepOutputsRepoRelative(dictStep, str(tmp_path))
    assert len(listRelative) == 1
    assert listRelative[0].endswith("result.dat")


# ------------------------------------------------------------------
# script-stale / manifest-hash predicates
# ------------------------------------------------------------------


def test_step_script_stale_false_when_status_not_modified():
    dictStep = {"saOutputDataFiles": ["x.dat"]}
    assert lg._fbStepScriptStale(
        0, dictStep, {0: {"sStatus": "clean"}}, "/repo",
    ) is False


def test_step_script_stale_false_without_script_status():
    assert lg._fbStepScriptStale(0, {}, None, "/repo") is False


def test_hashes_match_manifest_false_without_repo_root():
    assert lg._fbStepHashesMatchManifest({"saOutputDataFiles": ["x"]}, "") \
        is False


def test_hashes_match_manifest_false_without_declared_outputs(tmp_path):
    assert lg._fbStepHashesMatchManifest({}, str(tmp_path)) is False


# ------------------------------------------------------------------
# small numeric / label helpers
# ------------------------------------------------------------------


def test_step_max_mtime_zero_for_unparseable_value():
    assert lg._fiStepMaxMtime(0, {"0": "not-an-int"}) == 0


def test_step_max_mtime_zero_for_missing_step():
    assert lg._fiStepMaxMtime(5, {"0": "10"}) == 0


def test_label_for_step_falls_back_when_generator_raises(monkeypatch):
    def fnRaise(dictWorkflow, iStepIndex):
        raise TypeError("corrupt step")

    monkeypatch.setattr(
        "vaibify.gui.pipelineUtils.fsLabelFromStepIndex", fnRaise,
    )
    assert lg._fsLabelForStep({"listSteps": [None]}, 0) == "01"


# ------------------------------------------------------------------
# binary declaration coherence
# ------------------------------------------------------------------


def test_declares_binaries_rejects_non_dict_workflow():
    assert lg.fbWorkflowDeclaresBinaries(None) is False


def test_declares_binaries_rejects_non_list_declaration():
    assert lg.fbWorkflowDeclaresBinaries(
        {"bNoStandaloneBinaries": False, "listDeclaredBinaries": "nope"},
    ) is False


def test_declares_binaries_rejects_malformed_entry():
    assert lg.fbWorkflowDeclaresBinaries(
        {"bNoStandaloneBinaries": False, "listDeclaredBinaries": ["x"]},
    ) is False


def test_declares_binaries_accepts_full_entry():
    assert lg.fbWorkflowDeclaresBinaries({
        "bNoStandaloneBinaries": False,
        "listDeclaredBinaries": [{
            "sBinaryPath": "/usr/bin/tool",
            "sPurpose": "modelling",
            "sExpectedVersion": "1.0",
        }],
    }) is True


def test_declared_binaries_normalized_non_list_is_empty():
    assert lg._flistDeclaredBinariesNormalized(
        {"listDeclaredBinaries": "not-a-list"},
    ) == []


# ------------------------------------------------------------------
# arXiv helpers (client mocked)
# ------------------------------------------------------------------


def test_live_hashes_none_when_adapter_raises():
    assert lg._fdictLiveHashesOrNone(
        FakeRepo(bRaiseHash=True), ["fig.pdf"],
    ) is None


def test_live_hashes_extracts_sha_entries():
    dictResult = lg._fdictLiveHashesOrNone(
        FakeRepo(dictHashes={"fig.pdf": {"sSha256": "abc"}}),
        ["fig.pdf"],
    )
    assert dictResult == {"fig.pdf": "abc"}


def test_arxiv_version_current_false_without_recorded_version():
    assert lg._fbArxivVersionCurrent({"sArxivId": "1234.5678"}) is False


def test_arxiv_version_current_false_on_client_error(monkeypatch):
    from vaibify.reproducibility import arxivClient

    def fnRaise(sArxivId):
        raise arxivClient.ArxivError("network")

    monkeypatch.setattr(arxivClient, "fsResolveLatestVersion", fnRaise)
    assert lg._fbArxivVersionCurrent(
        {"sArxivId": "1234.5678", "sArxivVersion": "v2"},
    ) is False


def test_arxiv_hashes_cover_false_when_live_hash_unavailable():
    assert lg._fbArxivHashesCoverPushList(
        {"dictRemotes": {"arxiv": {"sArxivId": "1234.5678"}}},
        FakeRepo(bRaiseHash=True), ["fig.pdf"],
    ) is False


def test_arxiv_hashes_cover_false_on_client_error(monkeypatch):
    from vaibify.reproducibility import arxivClient

    def fnRaise(sArxivId, listPushed, dictPathMap=None, sCacheDir=None):
        raise arxivClient.ArxivError("throttled")

    monkeypatch.setattr(arxivClient, "fdictFetchRemoteHashes", fnRaise)
    monkeypatch.setattr(
        lg.scheduledReverify, "fsArxivCacheDir", lambda filesRepo: "/tmp",
    )
    assert lg._fbArxivHashesCoverPushList(
        {"dictRemotes": {"arxiv": {"sArxivId": "1234.5678"}}},
        FakeRepo(dictHashes={"fig.pdf": {"sSha256": "abc"}}),
        ["fig.pdf"],
    ) is False


# ------------------------------------------------------------------
# supervision evidence / binary-state fingerprint error paths
# ------------------------------------------------------------------


def test_recompute_supervision_evidence_none_on_error(monkeypatch):
    def fnRaise(filesRepo, dictWorkflow):
        raise OSError("attribution log unreadable")

    monkeypatch.setattr(
        "vaibify.gui.attributionLog.fdictSummarizeSupervisionEvidence",
        fnRaise,
    )
    assert lg._fdictRecomputeSupervisionEvidence({}, "/repo") is None


def test_binary_state_fingerprint_tolerates_hash_error():
    dictWorkflow = {
        "bNoStandaloneBinaries": False,
        "listDeclaredBinaries": [{
            "sBinaryPath": "/usr/bin/tool",
            "sPurpose": "p", "sExpectedVersion": "1",
        }],
    }
    sFingerprint = lg._fsBinaryStateFingerprint(
        dictWorkflow, FakeRepo(bRaiseHash=True),
    )
    assert len(sFingerprint) == 64


def test_binary_state_fingerprint_none_without_binaries():
    assert lg._fsBinaryStateFingerprint(
        {"listDeclaredBinaries": []}, FakeRepo(),
    ) == "none"


# ------------------------------------------------------------------
# L3 manifest / binary command-scan helpers
# ------------------------------------------------------------------


def test_read_manifest_paths_empty_without_manifest(tmp_path):
    assert lg._fsetReadManifestPaths(str(tmp_path)) == set()


def test_nondeterministic_steps_skips_non_dict_entries():
    dictWorkflow = {"listSteps": [
        None, {"bUnseededRandomnessWarning": True},
    ]}
    assert lg._fsetNondeterministicSteps(dictWorkflow) == {1}


def test_build_l3_step_blocker_none_for_non_dict_step():
    assert lg._fdictBuildL3StepBlocker({}, 0, None, {}) is None


def test_script_hash_matches_true_and_false():
    dictOnDisk = {"a.py": {"sSha256": "abc"}}
    assert lg._fbScriptHashMatches(dictOnDisk, "a.py", "abc") is True
    assert lg._fbScriptHashMatches(dictOnDisk, "a.py", "xyz") is False
    assert lg._fbScriptHashMatches({}, "a.py", "abc") is False


def test_declared_basenames_skips_empty_path():
    assert lg._fsetDeclaredBasenames([{"sBinaryPath": ""}]) == set()


def test_declared_binaries_not_captured_empty_without_commands():
    dictContext = {
        "listDeclaredBinaries": [{
            "sBinaryPath": "/usr/bin/tool",
            "sPurpose": "p", "sExpectedVersion": "1",
        }],
        "dictEnvironment": {},
    }
    assert lg._flistDeclaredBinariesNotCaptured(
        {"saDataCommands": []}, dictContext,
    ) == []


def test_step_references_declared_binary_false_for_empty_path():
    assert lg._fbStepReferencesDeclaredBinary(["run tool"], "") is False


def test_step_depended_binary_paths_non_dict_step():
    assert lg.flistStepDependedBinaryPaths(None, []) == []


def test_step_depended_binary_paths_skips_empty_declared_path():
    dictStep = {"saDataCommands": ["vplanet vpl.in"]}
    listDeclared = [{"sBinaryPath": ""}]
    assert lg.flistStepDependedBinaryPaths(dictStep, listDeclared) == []


# ------------------------------------------------------------------
# per-step projection helpers — defensive branches
# ------------------------------------------------------------------


def test_get_step_level_high_water_empty_for_non_dict():
    assert lg._fdictGetStepLevelHighWater(None) == {}


def test_step_has_no_activity_true_for_non_dict():
    assert lg._fbStepHasNoActivity(None) is True


def test_count_green_axes_over_present_axes():
    dictStep = {"dictVerification": {
        "sUnitTest": "passed", "sIntegrity": "failed",
    }}
    assert lg._ftCountGreenAxes(dictStep) == (1, 2)


def test_count_green_axes_non_dict_verification():
    assert lg._ftCountGreenAxes({"dictVerification": []}) == (0, 0)


def test_level1_requirements_non_dict_verification():
    listReq = lg._flistStepLevel1Requirements(
        {"dictVerification": [], "bNoInputData": True}, set(),
    )
    sNames = {sName for sName, _ in listReq}
    assert "user-attestation" in sNames
    assert "input-data-declared" in sNames


def test_attestation_stale_false_for_non_dict_step():
    assert lg._fbAttestationStaleOnStep(None) is False


def test_attestation_stale_false_for_non_dict_verification():
    assert lg._fbAttestationStaleOnStep({"dictVerification": []}) is False


def test_figure_freeze_not_applicable_for_non_dict_step():
    assert lg._fbFigureFreezeApplicable(None, {"bOverleafBound": True}) \
        is False


def test_applicable_l3_criteria_empty_for_non_dict_step():
    assert lg._fsetStepApplicableLevel3Criteria(None, []) == set()


def test_step_binary_newer_than_outputs_false_on_bad_mtime():
    assert lg._fbStepBinaryNewerThanOutputs(
        {"saDataCommands": ["tool"]}, [], {}, "not-an-int",
    ) is False


def test_step_has_failed_axis_false_for_non_dict_step():
    assert lg._fbStepHasFailedAxis(None) is False


def test_step_has_failed_axis_false_for_non_dict_verification():
    assert lg._fbStepHasFailedAxis({"dictVerification": []}) is False


def test_step_has_failed_axis_true_when_axis_failed():
    assert lg._fbStepHasFailedAxis(
        {"dictVerification": {"sUnitTest": "failed"}},
    ) is True
