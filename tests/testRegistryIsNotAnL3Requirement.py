"""A container registry is a convenience, never a PROOF rung (ruled 2026-09-05).

Earlier the same day, publishing the image to a registry had been made a
Level 3 requirement, on the reasoning that ``reproduce.sh``'s first act
is ``docker pull``. The ruling was reversed once the question "what is
the mission of Docker Hub and GHCR?" was asked: they are commercial
services with no retention policy, no DOI and no succession plan, and
grant reviewers have declined to accept even GitHub as a long-term
repository. A rung resting on one would be a claim vaibify could not
defend. So registries are treated like Overleaf and arXiv: integrated
because they are useful, gating nothing. The image's only rung is the
environment archive on Zenodo, which has a preservation commitment.

Three halves are asserted, because each could regress on its own:

- ``fbAtLeastLevel3`` does not consult the registry verdict.
- ``_fdictL3WorkflowChecks`` emits no ``image-not-published`` blocker
  and ``_T_WORKFLOW_LEVEL3_CRITERIA`` counts none -- the two must
  agree, since a criterion one side carries and the other omits is
  silently dropped from the header count.
- The readiness payload still REPORTS ``bImagePublished``, because the
  PROOF tab shows the registry copy as an optional row. Optional means
  visible and uncounted, not absent.

The shadow-lane translation of the SDK's bare ``404 ... No such image``
survives the reversal; only the remedy it names changed.
"""

import pytest

from vaibify.reproducibility import levelGates
from vaibify.reproducibility.levelGates import (
    _T_WORKFLOW_LEVEL3_CRITERIA,
    _fdictL3WorkflowChecks,
    fbVerifyImagePublished,
)


def test_the_header_tuple_carries_every_criterion_the_gates_emit():
    """The cell and the rows beneath it must fail on the same set.

    Asserted as a SUBSET relationship rather than by listing the
    criteria, because the failure mode is an addition to one side that
    nobody mirrors on the other -- and a hand-written list would have to
    be edited by the same person who forgot.
    """
    setEmitted = set(_fdictL3WorkflowChecks({}, "/nonexistent-for-shape"))
    setCounted = set(_T_WORKFLOW_LEVEL3_CRITERIA)
    assert setEmitted - setCounted == set(), (
        "these L3 criteria are emitted as blockers but not counted by "
        "the workflow-scope header cell, so the header can paint a "
        f"check above an orange row: {sorted(setEmitted - setCounted)}"
    )


def test_no_registry_criterion_is_emitted_or_counted():
    """The instance the ruling was about, pinned by name on BOTH sides.

    The subset test above is satisfied when both sides carry the
    criterion and when neither does; this is what says which.
    """
    assert "image-not-published" not in _T_WORKFLOW_LEVEL3_CRITERIA
    assert "image-not-published" not in _fdictL3WorkflowChecks(
        {}, "/nonexistent-for-shape",
    )


def test_a_local_only_image_can_still_attain_level_three(monkeypatch):
    """Every conjunct green and the registry verdict false: attained.

    Driven with the rest of the gate stubbed to True so the assertion
    can only be about the registry. A gate that still consulted
    ``fbVerifyImagePublished`` would refuse here.
    """
    for sName in (
        "fbAtLeastLevel2", "fbL3ReadinessOK", "fbL3AttestationCurrent",
        "fbEnvelopeMatchesGithubMirror", "fbEnvelopeMatchesZenodoArchive",
        "fbImageArchiveDeposited",
    ):
        monkeypatch.setattr(
            levelGates, sName, lambda *args, **kwargs: True,
        )
    monkeypatch.setattr(
        levelGates, "fbVerifyImagePublished",
        lambda *args, **kwargs: False,
    )
    assert levelGates.fbAtLeastLevel3({}, _ffilesStubRepo()) is True


def test_the_archive_is_the_image_criterion_that_gates(monkeypatch):
    """The reversal removed the registry, not the image, from Level 3.

    Without this, the test above would also pass against a gate that
    had dropped BOTH image conjuncts, which is the tidy-looking way to
    make it green.
    """
    for sName in (
        "fbAtLeastLevel2", "fbL3ReadinessOK", "fbL3AttestationCurrent",
        "fbEnvelopeMatchesGithubMirror", "fbEnvelopeMatchesZenodoArchive",
    ):
        monkeypatch.setattr(
            levelGates, sName, lambda *args, **kwargs: True,
        )
    monkeypatch.setattr(
        levelGates, "fbImageArchiveDeposited",
        lambda *args, **kwargs: False,
    )
    assert levelGates.fbAtLeastLevel3({}, _ffilesStubRepo()) is False
    assert "image-not-archived" in _T_WORKFLOW_LEVEL3_CRITERIA


def _ffilesStubRepo():
    """Return a repo adapter the stubbed gate never actually reads."""
    from vaibify.reproducibility.repoFiles import HostRepoFiles
    return HostRepoFiles("/nonexistent-for-shape")


def test_the_readiness_payload_reports_the_registry_anyway():
    """Reported without being counted: the PROOF tab's optional row.

    Optional means the researcher can SEE whether the fast path works,
    not that the question vanished. A payload without the flag would
    leave that row permanently blank.
    """
    dictGaps = levelGates.fdictL3ReadinessGaps(
        {}, "/nonexistent-for-shape",
    )
    assert "bImagePublished" in dictGaps


# ------------------------------------------------------------------
# The shadow lane: a named refusal, never a bare 404
# ------------------------------------------------------------------


class ImageNotFound(Exception):
    """Stands in for the SDK's own ImageNotFound.

    Named EXACTLY as the SDK names it, without the leading underscore a
    test-local class would otherwise carry: the translation matches on
    the class name, so a stand-in spelled differently would prove only
    that the test can construct an exception.

    Recognised by CLASS NAME rather than by matching "404" in a
    message, because an unreachable daemon can carry a 404 from an
    entirely different cause -- and telling a researcher to rebuild an
    image that is sitting right there would be worse than the bare
    error the translation replaces.
    """


def test_a_missing_local_only_image_names_the_rebuild_remedy(monkeypatch):
    """The researcher got "404 ... No such image" and nothing else.

    It arrived from inside a background task, about a digest, after a
    rebuild had moved the image out from under the envelope. The
    refusal now says which kind of reference it is and what to do --
    and what to do is rebuild or load the deposit, never "publish",
    because a registry is not a remedy for anything on the ladder.
    """
    from vaibify.reproducibility import shadowRerun

    def _fnRaiseImageNotFound(*args, **kwargs):
        raise ImageNotFound("404 Client Error: No such image")

    monkeypatch.setattr(
        shadowRerun.disposableContainer,
        "fdictReserveAndCreateContainer", _fnRaiseImageNotFound,
    )
    with pytest.raises(shadowRerun.ShadowRerunRefusedError) as errorInfo:
        shadowRerun._fdictCreateShadowOrExplainTheMissingImage(
            {}, "sha256:" + "a" * 64, {},
        )
    sMessage = str(errorInfo.value)
    assert "local-only" in sMessage
    assert "deposit" in sMessage
    assert "registry" not in sMessage


def test_a_missing_registry_image_names_the_pull_instead(monkeypatch):
    """Two references, two remedies.

    A registry digest the daemon does not hold is a pull away; a bare
    image ID is not, and cannot be. A single message covering both
    would send half the researchers who see it to the wrong place.
    """
    from vaibify.reproducibility import shadowRerun

    def _fnRaiseImageNotFound(*args, **kwargs):
        raise ImageNotFound("404 Client Error: No such image")

    monkeypatch.setattr(
        shadowRerun.disposableContainer,
        "fdictReserveAndCreateContainer", _fnRaiseImageNotFound,
    )
    sReference = "example.invalid/probe@sha256:" + "b" * 64
    with pytest.raises(shadowRerun.ShadowRerunRefusedError) as errorInfo:
        shadowRerun._fdictCreateShadowOrExplainTheMissingImage(
            {}, sReference, {},
        )
    sMessage = str(errorInfo.value)
    assert "docker pull " + sReference in sMessage
    assert "local-only" not in sMessage


def test_any_other_creation_failure_is_left_alone(monkeypatch):
    """Only an absent image is translated.

    A disk-full create, a refused reservation, a daemon that died
    mid-call: dressing those up as "rebuild your image" would send the
    researcher to fix something that is not broken, which is the defect
    being repaired, pointed the other way.
    """
    from vaibify.reproducibility import shadowRerun

    def _fnRaiseSomethingElse(*args, **kwargs):
        raise RuntimeError("the daemon ran out of disk")

    monkeypatch.setattr(
        shadowRerun.disposableContainer,
        "fdictReserveAndCreateContainer", _fnRaiseSomethingElse,
    )
    with pytest.raises(RuntimeError) as errorInfo:
        shadowRerun._fdictCreateShadowOrExplainTheMissingImage(
            {}, "sha256:" + "c" * 64, {},
        )
    assert "ran out of disk" in str(errorInfo.value)


def _ffilesWriteEnvelope(pathRepo, dictContainer):
    """Write an environment.json holding dictContainer; return the adapter."""
    import json

    from vaibify.reproducibility.repoFiles import HostRepoFiles
    (pathRepo / ".vaibify").mkdir(parents=True, exist_ok=True)
    (pathRepo / ".vaibify" / "environment.json").write_text(
        json.dumps({"dictContainer": dictContainer}), encoding="utf-8",
    )
    return HostRepoFiles(str(pathRepo))


def test_the_verifier_reads_the_local_only_stamp(tmp_path):
    """The stamp is authoritative; the shape is the fallback.

    The verdict feeds an optional row rather than a gate now, but a row
    that lies is still a lie. Both halves are driven against each
    other: an envelope written before the flag existed is judged by its
    digest shape alone, and one that carries the flag is judged by the
    flag even though its digest is the registry form. Asserting only
    the shape case would pass against a verifier that ignored the stamp
    entirely.
    """
    sRegistryDigest = "example.invalid/probe@sha256:" + "d" * 64
    filesShapeOnly = _ffilesWriteEnvelope(
        tmp_path / "legacy", {"sImageDigest": "sha256:" + "e" * 64},
    )
    assert fbVerifyImagePublished(filesShapeOnly) is False

    filesStamped = _ffilesWriteEnvelope(
        tmp_path / "stamped",
        {"sImageDigest": sRegistryDigest, "bLocalImageOnly": True},
    )
    assert fbVerifyImagePublished(filesStamped) is False

    filesPublished = _ffilesWriteEnvelope(
        tmp_path / "published",
        {"sImageDigest": sRegistryDigest, "bLocalImageOnly": False},
    )
    assert fbVerifyImagePublished(filesPublished) is True
