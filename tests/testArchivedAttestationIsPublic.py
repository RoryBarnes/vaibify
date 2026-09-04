"""Level 3 requires the rebuild attestation to be readable by a stranger.

Level 3 asserts that a third party can re-fetch and re-execute this
work. Until this criterion existed, the evidence that the author's OWN
re-execution passed lived only in `.vaibify/l3_attestation.json` on the
author's disk — so the strongest claim on the ladder rested on a record
nobody else could read, and a reader could not tell a project whose
rebuild had been demonstrated from one where it never had.

The check is deliberately SEMANTIC rather than a byte comparison
against the local file, and the tests below pin both directions of that
choice, because both are ways it could be quietly undone:

* Byte-comparing would make re-running an unchanged project drop Level
  3 until a new immutable Zenodo version was minted — taxing the exact
  behaviour the ladder exists to encourage.
* Reading the LOCAL manifest digest instead of the archived one would
  let an ordinary local edit invalidate a sound archive, and would let
  a local attestation vouch for an archive it never described.
"""

import pytest

from vaibify.reproducibility import l3Attestation
from vaibify.reproducibility.l3Attestation import (
    S_ARCHIVED_ATTESTATION_ABSENT,
    S_ARCHIVED_ATTESTATION_NOT_PASSED,
    S_ARCHIVED_ATTESTATION_OTHER_MANIFEST,
    S_ARCHIVED_ATTESTATION_UNREADABLE,
    ftJudgeArchivedAttestation,
)


S_ARCHIVED_MANIFEST_SHA = "a" * 64
S_OTHER_MANIFEST_SHA = "b" * 64


def _fjsonAttestationFor(sManifestSha, sStatus="passed"):
    """Build the archived-attestation shape the judge reads."""
    return {
        "iSchemaVersion": l3Attestation.I_SCHEMA_VERSION,
        "sStatus": sStatus,
        "sManifestDigestAtAttestation": "sha256:" + sManifestSha,
        "sAttestedAtUtc": "2026-09-03T00:00:00Z",
    }


def test_an_attestation_covering_the_archived_manifest_passes():
    bCovers, sReason = ftJudgeArchivedAttestation(
        _fjsonAttestationFor(S_ARCHIVED_MANIFEST_SHA),
        S_ARCHIVED_MANIFEST_SHA,
    )
    assert bCovers is True
    assert sReason == l3Attestation.S_ARCHIVED_ATTESTATION_COVERS


def test_an_archive_with_no_attestation_is_refused():
    bCovers, sReason = ftJudgeArchivedAttestation(
        None, S_ARCHIVED_MANIFEST_SHA,
    )
    assert bCovers is False
    assert sReason == S_ARCHIVED_ATTESTATION_ABSENT


def test_an_attestation_for_a_different_manifest_is_refused():
    """The archived pair must be internally consistent.

    An attestation describing some other manifest is a true statement
    about work that is not what the archive serves. Accepting it would
    let a reader believe the archived artefacts had been rebuilt when
    a different set had.
    """
    bCovers, sReason = ftJudgeArchivedAttestation(
        _fjsonAttestationFor(S_OTHER_MANIFEST_SHA),
        S_ARCHIVED_MANIFEST_SHA,
    )
    assert bCovers is False
    assert sReason == S_ARCHIVED_ATTESTATION_OTHER_MANIFEST


def test_an_archived_failure_is_not_evidence_of_a_rebuild():
    """A recorded failure is an attestation, and it does not pass.

    Checking only for the file's presence would let a project that
    demonstrably did NOT rebuild satisfy the criterion by archiving
    the record saying so.
    """
    bCovers, sReason = ftJudgeArchivedAttestation(
        _fjsonAttestationFor(S_ARCHIVED_MANIFEST_SHA, sStatus="failed"),
        S_ARCHIVED_MANIFEST_SHA,
    )
    assert bCovers is False
    assert sReason == S_ARCHIVED_ATTESTATION_NOT_PASSED


@pytest.mark.parametrize("valueJunk", ["not a dict", 17, [], True])
def test_an_unreadable_attestation_is_refused(valueJunk):
    bCovers, sReason = ftJudgeArchivedAttestation(
        valueJunk, S_ARCHIVED_MANIFEST_SHA,
    )
    assert bCovers is False
    assert sReason == S_ARCHIVED_ATTESTATION_UNREADABLE


def test_a_missing_archived_manifest_cannot_be_matched_by_an_empty_digest():
    """The empty-hash case must fail closed, not string-match.

    When the archive serves no ``MANIFEST.sha256`` the fetched hash is
    the empty string, and ``"sha256:" + ""`` is a real string an
    attestation could carry. Building the comparison without the
    emptiness guard would let an attestation recording the literal
    ``"sha256:"`` vouch for an archive holding no manifest at all.
    """
    bCovers, sReason = ftJudgeArchivedAttestation(
        _fjsonAttestationFor(""), "",
    )
    assert bCovers is False
    assert sReason == S_ARCHIVED_ATTESTATION_OTHER_MANIFEST


def test_rerunning_locally_does_not_disturb_the_archived_verdict():
    """The whole point of the semantic check, stated as a test.

    A rerun rewrites the local attestation's timestamp and duration
    while changing no verdict. The ARCHIVED attestation still
    truthfully describes the ARCHIVED manifest, so the criterion must
    still hold — otherwise re-checking a years-old result would cost a
    new immutable Zenodo version and a new DOI.

    Kills the byte-comparison design: two attestations that differ in
    every field a rerun rewrites, both covering the same archived
    manifest, must both pass.
    """
    jsonArchived = _fjsonAttestationFor(S_ARCHIVED_MANIFEST_SHA)
    jsonLocalAfterRerun = dict(jsonArchived)
    jsonLocalAfterRerun["sAttestedAtUtc"] = "2028-01-01T00:00:00Z"
    jsonLocalAfterRerun["fDurationSeconds"] = 9999.0

    assert jsonLocalAfterRerun != jsonArchived
    for jsonEither in (jsonArchived, jsonLocalAfterRerun):
        bCovers, _ = ftJudgeArchivedAttestation(
            jsonEither, S_ARCHIVED_MANIFEST_SHA,
        )
        assert bCovers is True


# ---------------------------------------------------------------------
# The gate: the criterion must actually reach the L3 blocker list from a
# cached Zenodo verify. The predicate above can be perfectly correct
# while nothing consults it.
# ---------------------------------------------------------------------

import json
import os

from vaibify.reproducibility import levelGates
from vaibify.reproducibility.levelGates import (
    fbAttestationIsPubliclyArchived,
    flistLevel3Blockers,
)


def _fnSeedZenodoSyncStatus(sRepo, valueArchivedVerdict):
    """Write .vaibify/syncStatus.json carrying a zenodo entry."""
    sDirectory = os.path.join(sRepo, ".vaibify")
    os.makedirs(sDirectory, exist_ok=True)
    dictEntry = {
        "sService": "zenodo",
        "sLastVerified": "2026-09-03T00:00:00Z",
        "iTotalFiles": 1,
        "iMatching": 1,
        "listDiverged": [],
    }
    if valueArchivedVerdict is not None:
        dictEntry["dictArchivedAttestation"] = valueArchivedVerdict
    with open(
        os.path.join(sDirectory, "syncStatus.json"), "w",
        encoding="utf-8",
    ) as fileHandle:
        json.dump({"zenodo": dictEntry}, fileHandle)


def _flistAttestationBlockers(sRepo):
    levelGates.fnClearLevelBlockerCache()
    return [
        dictBlocker
        for dictBlocker in flistLevel3Blockers(
            {"sProjectRepoPath": sRepo, "listSteps": []}, sRepo, False,
        )
        if dictBlocker["sCriterion"] == "attestation-not-in-zenodo-archive"
    ]


def test_a_covering_archived_attestation_clears_the_criterion(tmp_path):
    _fnSeedZenodoSyncStatus(
        str(tmp_path),
        {"bCoversArchivedManifest": True, "sReason": "covers"},
    )
    assert fbAttestationIsPubliclyArchived(str(tmp_path)) is True
    assert _flistAttestationBlockers(str(tmp_path)) == []


def test_an_archive_without_the_attestation_blocks_level_three(tmp_path):
    """The criterion must REACH the blocker list, not merely exist.

    Kills adding the predicate without wiring it into
    ``_fdictL3WorkflowChecks``: the function would be correct, every
    unit test above would pass, and Level 3 would go on being granted
    to projects whose rebuild nobody outside can see.
    """
    _fnSeedZenodoSyncStatus(
        str(tmp_path),
        {"bCoversArchivedManifest": False,
         "sReason": "no-attestation-in-archive"},
    )
    assert fbAttestationIsPubliclyArchived(str(tmp_path)) is False
    listBlockers = _flistAttestationBlockers(str(tmp_path))
    assert len(listBlockers) == 1, listBlockers
    assert "attestation" in listBlockers[0]["sRemediationHint"].lower()


def test_a_cache_predating_the_check_does_not_grant_the_criterion(tmp_path):
    """Unproven is not passed.

    A syncStatus.json written before this criterion existed carries no
    answer at all. Reading a missing answer as satisfied is how an
    unasked question comes to look like a checked one -- the failure
    the scope-version bump exists to prevent.
    """
    _fnSeedZenodoSyncStatus(str(tmp_path), None)
    assert fbAttestationIsPubliclyArchived(str(tmp_path)) is False
    assert len(_flistAttestationBlockers(str(tmp_path))) == 1


def test_a_host_project_reports_only_host_mode(tmp_path):
    """The host carve-out still short-circuits ahead of this criterion."""
    _fnSeedZenodoSyncStatus(str(tmp_path), None)
    levelGates.fnClearLevelBlockerCache()
    listBlockers = flistLevel3Blockers(
        {"sProjectRepoPath": str(tmp_path), "listSteps": []},
        str(tmp_path), True,
    )
    assert [d["sCriterion"] for d in listBlockers] == ["host-mode"]


# ---------------------------------------------------------------------
# The GitHub copy is ENCOURAGED, never required. It is compared so the
# dashboard has a truthful badge to show, and it is kept out of the
# envelope tuple so that badge cannot gate a level.
# ---------------------------------------------------------------------

from vaibify.reproducibility import publicationScope
from vaibify.reproducibility.levelGates import (
    fdictAttestationPublicationState,
)


S_ATTESTATION_REPO_PATH = ".vaibify/l3_attestation.json"


def _fnSeedServiceStatus(
    sRepo, sService, listComparedPaths, listDivergedPaths=(),
):
    """Write a syncStatus.json entry for one service."""
    sDirectory = os.path.join(sRepo, ".vaibify")
    os.makedirs(sDirectory, exist_ok=True)
    sPathStatus = os.path.join(sDirectory, "syncStatus.json")
    dictAll = {}
    if os.path.isfile(sPathStatus):
        with open(sPathStatus, encoding="utf-8") as fileHandle:
            dictAll = json.load(fileHandle)
    dictAll[sService] = {
        "sService": sService,
        "sLastVerified": "2026-09-03T00:00:00Z",
        "iTotalFiles": len(listComparedPaths),
        "iMatching": len(listComparedPaths) - len(listDivergedPaths),
        "listComparedPaths": list(listComparedPaths),
        "listDiverged": [
            {"sPath": sPath} for sPath in listDivergedPaths
        ],
    }
    with open(sPathStatus, "w", encoding="utf-8") as fileHandle:
        json.dump(dictAll, fileHandle)


def test_the_attestation_is_compared_but_gates_nothing():
    """Both halves, pinned together, because each is a way to break it.

    Dropping it from the compared set leaves the GitHub badge with no
    data, so the nudge would either vanish or be invented in the
    frontend. Adding it to the envelope tuple would silently turn an
    encouragement into a Level 3 criterion -- and GitHub cannot
    support a permanence claim, which is the whole reason the archive
    copy is the one that counts.
    """
    assert S_ATTESTATION_REPO_PATH in (
        publicationScope.TUPLE_COMPARED_NOT_REQUIRED_PATHS)
    assert S_ATTESTATION_REPO_PATH not in (
        publicationScope.TUPLE_LEVEL3_ENVELOPE_PATHS)
    listGathered = publicationScope.flistCollectComparisonPaths(
        {"listSteps": []}, "/nonexistent-repo",
    )
    assert S_ATTESTATION_REPO_PATH in listGathered


def test_a_published_attestation_reads_true(tmp_path):
    _fnSeedServiceStatus(
        str(tmp_path), "github", [S_ATTESTATION_REPO_PATH],
    )
    dictWhere = fdictAttestationPublicationState(str(tmp_path))
    assert dictWhere["github"] is True


def test_a_diverged_attestation_reads_false(tmp_path):
    _fnSeedServiceStatus(
        str(tmp_path), "github", [S_ATTESTATION_REPO_PATH],
        [S_ATTESTATION_REPO_PATH],
    )
    dictWhere = fdictAttestationPublicationState(str(tmp_path))
    assert dictWhere["github"] is False


def test_a_verify_that_never_looked_reads_none_not_false(tmp_path):
    """The third state, which is the one that must not collapse.

    A verify predating this path joining the compared set looked for
    nothing. Reading that as ``False`` nags a researcher about a file
    the hub never searched for; reading it as ``True`` claims a
    publication nobody checked. Both are lies of a different sign,
    which is why the caller gets ``None`` and renders it differently.

    Kills computing the verdict as "not in listDiverged", the obvious
    implementation: an unlooked-for path is absent from the divergence
    list in exactly the same way a matching one is.
    """
    _fnSeedServiceStatus(
        str(tmp_path), "github", ["MANIFEST.sha256"],
    )
    dictWhere = fdictAttestationPublicationState(str(tmp_path))
    assert dictWhere["github"] is None


def test_no_verify_at_all_reads_none(tmp_path):
    dictWhere = fdictAttestationPublicationState(str(tmp_path))
    assert dictWhere == {"github": None, "zenodo": None}


def test_the_github_copy_does_not_move_any_level_three_criterion(tmp_path):
    """An attestation absent from GitHub blocks nothing.

    The encouragement must stay an encouragement. If this ever fails,
    a nudge has become a gate.
    """
    _fnSeedZenodoSyncStatus(
        str(tmp_path),
        {"bCoversArchivedManifest": True, "sReason": "covers"},
    )
    _fnSeedServiceStatus(
        str(tmp_path), "github", [S_ATTESTATION_REPO_PATH],
        [S_ATTESTATION_REPO_PATH],
    )
    assert fdictAttestationPublicationState(
        str(tmp_path))["github"] is False
    levelGates.fnClearLevelBlockerCache()
    listCriteria = [
        dictBlocker["sCriterion"]
        for dictBlocker in flistLevel3Blockers(
            {"sProjectRepoPath": str(tmp_path), "listSteps": []},
            str(tmp_path), False,
        )
    ]
    assert "attestation-not-in-zenodo-archive" not in listCriteria
    assert not any("attestation" in s and "github" in s
                   for s in listCriteria), listCriteria
