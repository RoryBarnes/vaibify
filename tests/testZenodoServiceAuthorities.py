"""Three questions about "which Zenodo", answered by three fields.

A project holds a *declaration* -- where the next publish should go,
in ``project.json``'s top-level ``sZenodoService`` -- and a *record* of
where the deposit it already made actually lives, in the sidecar's
``dictRemotes.zenodo.sService``. They agree for a project that has
never changed instance, which is why reading the wrong one is invisible
until something moves: a promotion to production advances the record,
and every consumer still reading the declaration then reports drift
against a deposit that is exactly where it says it is.

Every fixture here therefore makes the two keys DISAGREE. With them
equal each assertion below passes against the bug it exists to catch.
"""

import pytest

from vaibify.gui import badgeState
from vaibify.reproducibility import levelGates, syncBookkeeping


def _fdictPromotedWorkflow():
    """A workflow whose deposit was promoted: record ahead of declaration."""
    return {
        "sZenodoService": "sandbox",
        "sZenodoDepositionId": "991",
        "dictRemotes": {"zenodo": {
            "sRecordId": "991",
            "sDoi": "10.5281/zenodo.991",
            "sService": "zenodo",
        }},
    }


def test_the_recorded_service_answers_where_the_deposit_lives():
    assert syncBookkeeping.fsResolveRecordedZenodoService(
        _fdictPromotedWorkflow(),
    ) == "zenodo"


def test_the_declaration_answers_only_for_a_project_with_no_record():
    """It is the upgrade fallback, not an authority on a record."""
    assert syncBookkeeping.fsResolveRecordedZenodoService(
        {"sZenodoService": "zenodo"},
    ) == "zenodo"
    assert syncBookkeeping.fsResolveRecordedZenodoService({}) == "sandbox"


def test_a_badge_does_not_read_drifted_after_a_promotion():
    """The endpoint check compares against where the deposit IS."""
    dictWorkflow = _fdictPromotedWorkflow()
    dictSync = {"paper.tex": {
        "bZenodo": True,
        "sZenodoLastPushedDigest": "abc123",
        "sZenodoLastPushedEndpoint": "zenodo",
    }}
    dictBadges = badgeState.fdictBadgeStateFromHashes(
        ["paper.tex"], {}, dictSync, {"paper.tex": "abc123"}, set(),
        sZenodoService=syncBookkeeping.fsResolveRecordedZenodoService(
            dictWorkflow,
        ),
    )
    assert dictBadges["paper.tex"]["sZenodo"] != badgeState.S_BADGE_DRIFTED


def test_the_verify_endpoint_gate_follows_the_recorded_service():
    dictWorkflow = _fdictPromotedWorkflow()
    assert levelGates._fbZenodoEndpointMatches(
        dictWorkflow, {"sEndpointVerified": "zenodo"},
    ) is True
    assert levelGates._fbZenodoEndpointMatches(
        dictWorkflow, {"sEndpointVerified": "sandbox"},
    ) is False


def test_a_cross_instance_parent_is_refused_by_name():
    """Zenodo cannot version a record held on the other instance."""
    sRefusal = syncBookkeeping.fsDescribeCrossInstanceParent(
        _fdictPromotedWorkflow(), "sandbox",
    )
    assert "zenodo" in sRefusal and "sandbox" in sRefusal
    assert "new concept" in sRefusal


def test_an_ordinary_publish_to_its_own_instance_is_not_refused():
    """The declaration still decides where a publish goes."""
    assert syncBookkeeping.fsDescribeCrossInstanceParent(
        _fdictPromotedWorkflow(), "zenodo",
    ) == ""


def test_a_first_publish_is_never_cross_instance():
    """With no parent id there is no record to version."""
    assert syncBookkeeping.fsDescribeCrossInstanceParent(
        {"sZenodoService": "zenodo", "dictRemotes": {
            "zenodo": {"sService": "sandbox"},
        }},
        "zenodo",
    ) == ""


@pytest.mark.parametrize("sRaw,iExpected", [
    ("991", 991), ("0", 0), ("", 0), ("not-a-number", 0), (None, 0),
])
def test_the_parent_deposit_id_reader_never_raises(sRaw, iExpected):
    assert syncBookkeeping.fiResolveZenodoParentDepositId(
        {"sZenodoDepositionId": sRaw},
    ) == iExpected
