"""Level 3 requires the envelope in the Zenodo ARCHIVE, not only GitHub.

The 2026-08-26 ruling (superseding a same-day GitHub-only one): Level 3
claims a third party can re-fetch and re-execute, and GitHub is not an
archive — repositories are renamed, made private, force-pushed,
deleted. Within v1.0's closed world of exactly two remotes, "the
envelope is in the permanent archive" reduces to "the envelope is in
Zenodo".

Two constraints shipped WITH the criterion, and this module defends
both. Zenodo's own GitHub integration archives a code release as a
SEPARATE record with its own DOI, so the verify consults every
DECLARED record and a file agrees with Zenodo when ANY of them serves
its bytes — a single-record check would fire falsely on the
arrangement Zenodo itself promotes. And Zenodo deposits are immutable,
so the criterion makes Level 3 a release-time property; the gate tests
here pin that the criterion actually GATES ``fbAtLeastLevel3`` rather
than merely decorating the screen (the GitHub twin shipped
visibility-only, with a comment claiming it blocked attainment).
"""

import pytest

from tests.syncStatusFixtures import fsRecentVerifyIso
from vaibify.gui import workflowManager
from vaibify.reproducibility import (
    levelGates,
    publicationScope,
    scheduledReverify,
)


S_DATA = "MakeData/output.json"
S_ENVELOPE = "reproduce.sh"


class _FakeRepoFiles:
    """A repo whose files are whatever the test says exist."""

    def __init__(self, setPresent=()):
        self.sRootPath = "/repo"
        self._setPresent = set(setPresent)

    def fbIsFile(self, sRelPath):
        return sRelPath in self._setPresent

    def flistListJsonFilenames(self, sRelDir):
        return []


def _fdictStatus(listCompared, listDivergedPaths=(),
                 sVerified=fsRecentVerifyIso()):
    return {
        "sService": "zenodo",
        "sLastVerified": sVerified,
        "iTotalFiles": len(listCompared),
        "iMatching": len(listCompared) - len(listDivergedPaths),
        "listComparedPaths": list(listCompared),
        "listDiverged": [
            {"sPath": s, "sExpected": "aaa", "sActual": "bbb"}
            for s in listDivergedPaths
        ],
        "iScopeVersion": publicationScope.I_PUBLICATION_SCOPE_VERSION,
        "sZenodoDoi": "10.5281/zenodo.1234",
        "sEndpointVerified": "sandbox",
    }


def _fbArchiveMatches(monkeypatch, dictStatus, setPresent):
    monkeypatch.setattr(
        scheduledReverify, "fdictReadCachedSyncStatus",
        lambda filesRepo, sService: (
            dictStatus if sService == "zenodo" else {}
        ),
    )
    return levelGates.fbEnvelopeMatchesZenodoArchive(
        _FakeRepoFiles(setPresent),
    )


# ---------------------------------------------------------------------
# The criterion itself, symmetric with its GitHub twin.
# ---------------------------------------------------------------------


def test_a_diverged_envelope_blocks_the_archive_criterion(monkeypatch):
    assert _fbArchiveMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_ENVELOPE], [S_ENVELOPE]),
        {S_ENVELOPE},
    ) is False


def test_a_matching_envelope_passes_the_archive_criterion(monkeypatch):
    """A diverged DATA file must not block the Level 3 criterion."""
    assert _fbArchiveMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_ENVELOPE], [S_DATA]),
        {S_ENVELOPE},
    ) is True


def test_an_envelope_never_compared_against_zenodo_does_not_pass(
    monkeypatch,
):
    """A cache whose compared set omits the envelope proves nothing."""
    assert _fbArchiveMatches(
        monkeypatch,
        _fdictStatus([S_DATA]),
        {S_ENVELOPE},
    ) is False


def test_a_project_with_no_envelope_is_not_double_reported(monkeypatch):
    """Absence is already reported by the artifact criteria."""
    assert _fbArchiveMatches(
        monkeypatch, _fdictStatus([S_DATA]), set(),
    ) is True


def test_an_unverified_project_does_not_claim_an_archived_envelope(
    monkeypatch,
):
    assert _fbArchiveMatches(
        monkeypatch,
        _fdictStatus([S_DATA, S_ENVELOPE], sVerified=None),
        {S_ENVELOPE},
    ) is False


def test_the_criterion_is_registered_with_a_usable_hint():
    """The remediation is a deposit VERSION, never a push."""
    sHint = levelGates._DICT_L3_REMEDIATION_HINTS[
        "envelope-not-in-zenodo-archive"
    ]
    assert "Verify now" in sHint, sHint
    assert "deposit" in sHint, sHint
    assert "push" not in sHint.lower(), (
        "the Zenodo remediation must not tell the researcher to "
        f"push: {sHint}"
    )


def test_the_zenodo_criterion_reaches_the_proof_tab_payload(tmp_path):
    """A blocker with no row is an unexplained dash to the researcher."""
    from vaibify.reproducibility.repoFiles import HostRepoFiles

    dictWorkflow = {"sProjectRepoPath": "/repo", "listSteps": []}
    dictGaps = levelGates.fdictL3ReadinessGaps(
        dictWorkflow, HostRepoFiles(str(tmp_path)),
    )
    assert "bEnvelopeInZenodoArchive" in dictGaps, sorted(dictGaps)


def test_publishing_to_zenodo_is_not_a_precondition_for_attesting():
    """Readiness asks about the LOCAL envelope; publication is separate."""
    dictFlags = levelGates._fdictCollectL3ReadinessFlags(
        {"sProjectRepoPath": "/repo", "listSteps": []},
        _FakeRepoFiles(setPresent={S_ENVELOPE}),
        True,
    )
    assert "bEnvelopeInZenodoArchive" not in dictFlags, sorted(dictFlags)


# ---------------------------------------------------------------------
# The criterion GATES the level. Its GitHub twin shipped as
# visibility-only while a comment claimed it blocked attainment — the
# accepted-and-dropped-parameter class of defect. These tests pin the
# wire, not the intent.
# ---------------------------------------------------------------------


def _fnMakeEveryOtherConjunctPass(monkeypatch):
    for sName in (
        "fbAtLeastLevel2", "fbL3ReadinessOK",
    ):
        monkeypatch.setattr(
            levelGates, sName, lambda dictWorkflow, filesRepo: True,
        )
    monkeypatch.setattr(
        levelGates, "fbL3AttestationCurrent", lambda filesRepo: True,
    )


@pytest.mark.falsification
def test_a_missing_zenodo_archive_refuses_level_three(monkeypatch):
    """The release-time property has teeth only if this refuses.

    Kills: deleting the ``fbEnvelopeMatchesZenodoArchive`` conjunct
    from ``fbAtLeastLevel3``, which returns the gate to reporting
    Level 3 for an envelope no archive holds.
    """
    _fnMakeEveryOtherConjunctPass(monkeypatch)
    monkeypatch.setattr(
        levelGates, "fbEnvelopeMatchesGithubMirror",
        lambda filesRepo: True,
    )
    monkeypatch.setattr(
        levelGates, "fbEnvelopeMatchesZenodoArchive",
        lambda filesRepo: False,
    )
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _FakeRepoFiles(),
    ) is False


@pytest.mark.falsification
def test_a_drifted_github_envelope_refuses_level_three(monkeypatch):
    """The GitHub twin gates too — it used to be visibility-only.

    Kills: deleting the ``fbEnvelopeMatchesGithubMirror`` conjunct
    from ``fbAtLeastLevel3``.
    """
    _fnMakeEveryOtherConjunctPass(monkeypatch)
    monkeypatch.setattr(
        levelGates, "fbEnvelopeMatchesGithubMirror",
        lambda filesRepo: False,
    )
    monkeypatch.setattr(
        levelGates, "fbEnvelopeMatchesZenodoArchive",
        lambda filesRepo: True,
    )
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _FakeRepoFiles(),
    ) is False


def test_level_three_still_attainable_with_both_envelopes_published(
    monkeypatch,
):
    """The complement: the pair must not refuse everything."""
    _fnMakeEveryOtherConjunctPass(monkeypatch)
    for sName in (
        "fbEnvelopeMatchesGithubMirror",
        "fbEnvelopeMatchesZenodoArchive",
    ):
        monkeypatch.setattr(levelGates, sName, lambda filesRepo: True)
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _FakeRepoFiles(),
    ) is True


@pytest.mark.falsification
def test_the_refusal_names_itself_in_the_blocker_list(tmp_path):
    """A gate that refuses while the blocker list stays silent is a
    refusal with no reason — that bug shipped once already.

    Kills: removing the ``envelope-not-in-zenodo-archive`` entry from
    ``_fdictL3WorkflowChecks``.
    """
    (tmp_path / S_ENVELOPE).write_text("#!/bin/sh\n")
    dictWorkflow = {
        "sProjectRepoPath": str(tmp_path), "listSteps": [],
    }
    levelGates.fnClearLevelBlockerCache()
    listCriteria = [
        dictEntry.get("sCriterion")
        for dictEntry in levelGates.flistLevel3Blockers(
            dictWorkflow, str(tmp_path), False,
        )
    ]
    assert "envelope-not-in-zenodo-archive" in listCriteria, listCriteria


def test_the_workflow_header_cell_counts_the_envelope_pair():
    """Both criteria are applicable workflow-scope L3 requirements."""
    for sCriterion in (
        "envelope-not-in-github-mirror",
        "envelope-not-in-zenodo-archive",
    ):
        assert sCriterion in levelGates._T_WORKFLOW_LEVEL3_CRITERIA


# ---------------------------------------------------------------------
# Declared records: the archive is a SET of deposits.
# ---------------------------------------------------------------------


def test_declared_records_lead_with_the_primary_and_deduplicate():
    dictConfig = {
        "sRecordId": "111", "sDoi": "10.5281/zenodo.111",
        "listRecords": [
            {"sRecordId": "222", "sDoi": "10.5281/zenodo.222"},
            {"sRecordId": "111"},
            "garbage",
            {"sDoi": "10.5281/zenodo.333"},
            {"sRecordId": "  "},
        ],
    }
    listRecords = scheduledReverify.flistZenodoDeclaredRecords(
        dictConfig,
    )
    assert listRecords == [
        {"sRecordId": "111", "sDoi": "10.5281/zenodo.111"},
        {"sRecordId": "222", "sDoi": "10.5281/zenodo.222"},
    ]


def test_no_declared_records_still_refuses_the_verify():
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify._fdictFetchZenodoHashes(
            {"sService": "sandbox"}, [S_ENVELOPE], {},
        )


@pytest.mark.falsification
def test_a_file_agrees_with_zenodo_when_any_declared_record_serves_it(
    monkeypatch,
):
    """The arrangement Zenodo promotes: data deposit + software deposit.

    The records DISAGREE about ``reproduce.sh`` — the data deposit
    carries a stale copy, the declared software deposit the current
    one — because with agreeing records a first-record merge gives the
    same answer and the mutation survives unobserved.

    Kills: disabling the expected-hash preference in
    ``_fdictMergeRecordHashes``, which hands back the stale first
    record's hash and reports a divergence the archive does not have.
    """
    # Keyed by DEPOSIT KEY (the basename): a Zenodo deposit is flat,
    # so the fetch requests basenames and maps them back onto the
    # compared repo-relative paths.
    dictByRecord = {
        "111": {"output.json": "d" * 64,
                S_ENVELOPE: "stale" + "0" * 59},
        "222": {S_ENVELOPE: "e" * 64},
    }
    monkeypatch.setattr(
        scheduledReverify.zenodoClient, "fdictFetchRemoteHashes",
        lambda sRecordId, listRelPaths=None, sService="sandbox":
            {sPath: dictByRecord[sRecordId].get(sPath)
             for sPath in listRelPaths},
    )
    dictConfig = {
        "sRecordId": "111", "sService": "sandbox",
        "listRecords": [{"sRecordId": "222"}],
    }
    dictExpected = {S_DATA: "d" * 64, S_ENVELOPE: "e" * 64}
    dictActual = scheduledReverify._fdictFetchZenodoHashes(
        dictConfig, [S_DATA, S_ENVELOPE], dictExpected,
    )
    assert dictActual[S_ENVELOPE] == "e" * 64, (
        "the envelope is in the declared software deposit, but the "
        "merge reported the data deposit's stale copy: "
        f"{dictActual[S_ENVELOPE]!r}"
    )
    assert dictActual[S_DATA] == "d" * 64


def test_a_file_in_no_declared_record_keeps_a_real_remote_hash(
    monkeypatch,
):
    """A divergence entry must show a hash some record actually serves,
    and a file in no record at all must come back None."""
    dictByRecord = {
        "111": {S_ENVELOPE: "1" * 64},
        "222": {S_ENVELOPE: "2" * 64},
    }  # reproduce.sh is its own basename; output.json in no record
    monkeypatch.setattr(
        scheduledReverify.zenodoClient, "fdictFetchRemoteHashes",
        lambda sRecordId, listRelPaths=None, sService="sandbox":
            {sPath: dictByRecord[sRecordId].get(sPath)
             for sPath in listRelPaths},
    )
    dictConfig = {
        "sRecordId": "111", "sService": "sandbox",
        "listRecords": [{"sRecordId": "222"}],
    }
    dictActual = scheduledReverify._fdictFetchZenodoHashes(
        dictConfig, [S_ENVELOPE, S_DATA],
        {S_ENVELOPE: "e" * 64, S_DATA: "d" * 64},
    )
    assert dictActual[S_ENVELOPE] == "1" * 64
    assert dictActual[S_DATA] is None


# ---------------------------------------------------------------------
# Declaring and removing records on the workflow.
# ---------------------------------------------------------------------


def test_declaring_a_record_is_idempotent_against_the_primary():
    dictWorkflow = {
        "dictRemotes": {"zenodo": {"sRecordId": "111"}},
    }
    assert workflowManager.fbDeclareZenodoRecord(
        dictWorkflow, "111",
    ) is False
    assert workflowManager.fbDeclareZenodoRecord(
        dictWorkflow, "222", "10.5281/zenodo.222",
    ) is True
    assert workflowManager.fbDeclareZenodoRecord(
        dictWorkflow, "222",
    ) is False
    assert dictWorkflow["dictRemotes"]["zenodo"]["listRecords"] == [
        {"sRecordId": "222", "sDoi": "10.5281/zenodo.222"},
    ]


def test_declaring_works_on_a_workflow_with_no_remotes_yet():
    dictWorkflow = {}
    assert workflowManager.fbDeclareZenodoRecord(
        dictWorkflow, "333",
    ) is True
    assert dictWorkflow["dictRemotes"]["zenodo"]["listRecords"] == [
        {"sRecordId": "333"},
    ]


def test_removing_touches_only_declared_records_never_the_primary():
    dictWorkflow = {
        "dictRemotes": {"zenodo": {
            "sRecordId": "111",
            "listRecords": [{"sRecordId": "222"}],
        }},
    }
    assert workflowManager.fbRemoveZenodoRecord(
        dictWorkflow, "111",
    ) is False, "the primary record must not be removable here"
    assert workflowManager.fbRemoveZenodoRecord(
        dictWorkflow, "222",
    ) is True
    assert dictWorkflow["dictRemotes"]["zenodo"]["listRecords"] == []


def test_the_doi_extractor_refuses_foreign_dois():
    assert workflowManager.fsZenodoRecordIdFromDoi(
        "10.5281/zenodo.123456",
    ) == "123456"
    assert workflowManager.fsZenodoRecordIdFromDoi(
        "10.9999/notzenodo.123",
    ) == ""
    assert workflowManager.fsZenodoRecordIdFromDoi("") == ""


# ---------------------------------------------------------------------
# The scope bump: a version-2 cache cannot answer the multi-record
# question.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_version_two_cache_is_no_longer_scope_current():
    """Every superseded scope version reads stale; only the current passes.

    A v2 cache compared one deposit where v3 asks about the declared
    SET; a v3 cache compared a project.json that still carried the
    sync bookkeeping the sidecar migration removed. The literal
    versions are deliberate — these are the assertions that fail if a
    bump is ever reverted.

    Kills: reverting ``I_PUBLICATION_SCOPE_VERSION`` to 3 (or any
    earlier value), which makes every pre-sidecar cache claim to
    answer a question it never asked.
    """
    for iStaleVersion in (2, 3):
        assert publicationScope.fbCachedScopeIsCurrent(
            {"iScopeVersion": iStaleVersion},
        ) is False
    assert publicationScope.fbCachedScopeIsCurrent(
        {"iScopeVersion":
            publicationScope.I_PUBLICATION_SCOPE_VERSION},
    ) is True


@pytest.mark.falsification
def test_colliding_basenames_read_not_in_deposit(monkeypatch):
    """A basename two compared paths share identifies NEITHER file.

    The deposit is flat, so the record holds ONE file under the shared
    name — whichever survived the upload's silent overwrite. Matching
    either compared path to it would certify a file the archive may
    have destroyed.

    Kills: dropping the uniqueness exclusion in
    ``_fdictUniqueBasenameByPath``, which matches the surviving upload
    to one of the paths and reports it published.
    """
    sPathAlpha = "StepAlpha/tests/test_qualitative.py"
    sPathBeta = "StepBeta/tests/test_qualitative.py"
    monkeypatch.setattr(
        scheduledReverify.zenodoClient, "fdictFetchRemoteHashes",
        lambda sRecordId, listRelPaths=None, sService="sandbox":
            {sKey: {"test_qualitative.py": "a" * 64}.get(sKey)
             for sKey in listRelPaths},
    )
    dictActual = scheduledReverify._fdictFetchZenodoHashes(
        {"sRecordId": "111", "sService": "sandbox"},
        [sPathAlpha, sPathBeta],
        {sPathAlpha: "a" * 64, sPathBeta: "b" * 64},
    )
    assert dictActual[sPathAlpha] is None, dictActual
    assert dictActual[sPathBeta] is None, dictActual


def test_unique_basenames_map_onto_the_flat_deposit(monkeypatch):
    """The complement: a unique basename finds its deposit key."""
    listRequested = []

    def _fdictFetch(sRecordId, listRelPaths=None, sService="sandbox"):
        listRequested.extend(listRelPaths)
        return {"output.json": "d" * 64, "reproduce.sh": "e" * 64}

    monkeypatch.setattr(
        scheduledReverify.zenodoClient, "fdictFetchRemoteHashes",
        _fdictFetch,
    )
    dictActual = scheduledReverify._fdictFetchZenodoHashes(
        {"sRecordId": "111", "sService": "sandbox"},
        [S_DATA, S_ENVELOPE],
        {S_DATA: "d" * 64, S_ENVELOPE: "e" * 64},
    )
    assert dictActual == {S_DATA: "d" * 64, S_ENVELOPE: "e" * 64}
    assert sorted(listRequested) == ["output.json", "reproduce.sh"], (
        "the fetch must request DEPOSIT keys (basenames), because "
        f"that is what a flat deposit is keyed by: {listRequested}"
    )
