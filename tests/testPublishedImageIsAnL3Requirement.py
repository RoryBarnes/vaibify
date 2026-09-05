"""Publishing the container image is part of Level 3 (ruled 2026-09-05).

``reproduce.sh``'s first act is ``docker pull`` of the reference the
envelope pins. A locally built image records its bare image ID
(``sha256:<hex>``) — an honest content pin that exists in no registry,
so a stranger's reproduction dies at step one. Until this ruling that
was reported as a blocker ROW and consulted by neither the scalar L3
gate nor the header cell, so a project could attain Level 3 — the rung
that claims a third party can re-execute the work — with an image only
its author could obtain.

Two halves, and each fails differently, which is why both are asserted
here:

- ``fbAtLeastLevel3`` now refuses. That is the claim itself.
- ``_T_WORKFLOW_LEVEL3_CRITERIA`` now carries the criterion. A criterion
  the gates EMIT but that tuple OMITS is silently dropped from the
  header count rather than merely uncounted, so the cell paints a check
  above an orange row — the exact shape this repository already shipped
  once with the L2 tuple.

It stays OUT of the readiness composition, beside the GitHub and Zenodo
conjuncts and for their reason: readiness asks whether the local
envelope is coherent enough to attempt a rerun, and the rerun builds
its shadow from whatever the daemon already holds. Blocking readiness
would stop a researcher attesting locally before they publish, which
the gate's own docstring exists to protect.
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


def test_image_not_published_is_one_of_them():
    """The instance the ruling was about, pinned by name.

    The subset test above would also pass if BOTH sides dropped the
    criterion, which is the tidy-looking way to make it green.
    """
    assert "image-not-published" in _T_WORKFLOW_LEVEL3_CRITERIA


def test_a_local_only_image_cannot_attain_level_three(monkeypatch):
    """Every other conjunct green, and the level still refuses.

    Driven with the rest of the gate stubbed to True so the assertion
    can only be about this one conjunct. Without that, a gate that
    ignored the image would still fail for an unrelated reason and the
    test would pass while proving nothing.
    """
    for sName in (
        "fbAtLeastLevel2", "fbL3ReadinessOK", "fbL3AttestationCurrent",
        "fbEnvelopeMatchesGithubMirror", "fbEnvelopeMatchesZenodoArchive",
    ):
        monkeypatch.setattr(
            levelGates, sName, lambda *args, **kwargs: True,
        )
    monkeypatch.setattr(
        levelGates, "fbVerifyImagePublished",
        lambda *args, **kwargs: False,
    )
    assert levelGates.fbAtLeastLevel3({}, _ffilesStubRepo()) is False
    monkeypatch.setattr(
        levelGates, "fbVerifyImagePublished",
        lambda *args, **kwargs: True,
    )
    assert levelGates.fbAtLeastLevel3({}, _ffilesStubRepo()) is True


def _ffilesStubRepo():
    """Return a repo adapter the stubbed gate never actually reads."""
    from vaibify.reproducibility.repoFiles import HostRepoFiles
    return HostRepoFiles("/nonexistent-for-shape")


def test_readiness_still_passes_for_a_local_only_image(monkeypatch):
    """A researcher may attest locally before they publish.

    The readiness composition answers "is the envelope coherent enough
    to attempt a rebuild?", and the rebuild builds its shadow from the
    image already on this daemon. Folding the publication question into
    readiness would refuse the local verification a researcher runs
    BEFORE deciding the work is ready to publish.
    """
    for sName in (
        "fbWorkflowHasProjectRepo", "fbVerifyManifestComplete",
        "fbVerifyDependencyLock", "fbVerifyEnvironmentSnapshot",
        "fbVerifyDockerfilePinned", "fbVerifyReproduceScript",
        "fbVerifyDeterminismDeclared", "fbWorkflowDeclaresBinaries",
    ):
        monkeypatch.setattr(
            levelGates, sName, lambda *args, **kwargs: True,
        )
    monkeypatch.setattr(
        levelGates, "fbVerifyImagePublished",
        lambda *args, **kwargs: False,
    )
    assert levelGates.fbL3ReadinessOK({}, _ffilesStubRepo()) is True


def test_the_readiness_payload_reports_the_image_anyway():
    """Reported without being counted, like the published-envelope pair.

    A blocker with no row is one a researcher meets as an unexplained
    dash, so the flag rides the same payload the PROOF tab binds
    against even though it is outside the readiness ``all()``.
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
    entirely different cause -- and telling a researcher to publish an
    image that is sitting right there would be worse than the bare
    error the translation replaces.
    """


def test_a_missing_local_only_image_names_the_publish_remedy(monkeypatch):
    """The researcher got "404 ... No such image" and nothing else.

    It arrived from inside a background task, about a digest, after a
    rebuild had moved the image out from under the envelope. The
    refusal now says which kind of reference it is and what to do.
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
    assert "registry" in sMessage


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
    mid-call: dressing those up as "publish your image" would send the
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

    Both halves matter, and they are driven against each other here: an
    envelope written before the flag existed is judged by its digest
    shape alone, and one that carries the flag is judged by the flag
    even though its digest is the registry form. Asserting only the
    shape case would pass against a verifier that ignored the stamp
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
