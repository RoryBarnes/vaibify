"""The envelope's pinned image versus the container actually open.

The envelope pins the image a project's results claim to come from.
Nothing compared that pin against the running container, so a
researcher who rebuilt and forgot to regenerate the snapshot had every
verification silently grade the OLD image — discovered only when a
step failed deep inside the shadow over a package the new image had
carried for days (researcher-reported, 2026-09-01). These tests pin
the capture, the three-state comparison, and the honesty rule that an
absent capture warns about nothing.
"""

from unittest.mock import patch

import pytest

from vaibify.gui import pipelineServer
from vaibify.reproducibility import environmentSnapshot


S_REGISTRY_DIGEST = "registry.example/img@sha256:" + "ab" * 32
S_IMAGE_ID = "sha256:" + "cd" * 32
S_OTHER_DIGEST = "registry.example/img@sha256:" + "ef" * 32


def _fdictContext(dictIdentity):
    dictCtx = {}
    if dictIdentity is not None:
        dictCtx["dictLiveImageIdentities"] = {"cid1": dictIdentity}
    return dictCtx


def _ffilesRepoWithPin(tmp_path, sPinned):
    pathVaibify = tmp_path / ".vaibify"
    pathVaibify.mkdir(exist_ok=True)
    if sPinned is not None:
        (pathVaibify / "environment.json").write_text(
            '{"dictContainer": {"sImageDigest": "%s"}}' % sPinned,
        )
    return str(tmp_path)


def test_capture_returns_both_identity_forms(monkeypatch):
    """The preferred form AND the raw image ID both travel."""
    monkeypatch.setattr(
        environmentSnapshot, "_fnEnsureDockerAvailable", lambda: None,
    )
    monkeypatch.setattr(
        environmentSnapshot, "_fsInspectFormatValue",
        lambda sTarget, sFormat: (
            S_IMAGE_ID if sFormat == "{{.Image}}" else "[ignored]"
        ),
    )
    monkeypatch.setattr(
        environmentSnapshot, "_fsParseRepoDigests",
        lambda sRaw: S_REGISTRY_DIGEST,
    )
    dictIdentity = environmentSnapshot.fdictCaptureLiveImageIdentity(
        "someContainer",
    )
    assert dictIdentity == {
        "sImageDigest": S_REGISTRY_DIGEST,
        "sImageId": S_IMAGE_ID,
    }


def test_a_matching_pin_is_current(tmp_path):
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContext({"sImageDigest": S_REGISTRY_DIGEST,
                       "sImageId": S_IMAGE_ID}),
        "cid1", _ffilesRepoWithPin(tmp_path, S_REGISTRY_DIGEST),
    )
    assert dictAnswer["bPinnedImageIsLive"] is True


def test_a_pin_by_raw_image_id_is_also_current(tmp_path):
    """An image pushed to a registry after capture has not changed.

    The envelope may hold the image ID (locally built at capture
    time) while the live capture prefers the registry digest the
    image has since gained. Same bytes, two names — comparing only
    the preferred form would flag it as a rebuild.
    """
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContext({"sImageDigest": S_REGISTRY_DIGEST,
                       "sImageId": S_IMAGE_ID}),
        "cid1", _ffilesRepoWithPin(tmp_path, S_IMAGE_ID),
    )
    assert dictAnswer["bPinnedImageIsLive"] is True


@pytest.mark.falsification
def test_a_rebuilt_container_is_reported_as_not_pinned(tmp_path):
    """The researcher's incident: rebuilt, pin unchanged, nobody said.

    Kills: In fdictAssessEnvelopeImageCurrency, answer
    bPinnedImageIsLive True whenever both sides are known, instead of
    comparing the pin against the two identity forms.
    """
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContext({"sImageDigest": S_REGISTRY_DIGEST,
                       "sImageId": S_IMAGE_ID}),
        "cid1", _ffilesRepoWithPin(tmp_path, S_OTHER_DIGEST),
    )
    assert dictAnswer["bPinnedImageIsLive"] is False
    assert dictAnswer["sPinnedImageDigest"] == S_OTHER_DIGEST
    assert dictAnswer["sLiveImageDigest"] == S_REGISTRY_DIGEST


def test_an_absent_capture_answers_none_never_false(tmp_path):
    """No capture is no evidence; a warning built from it cries wolf."""
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContext(None), "cid1",
        _ffilesRepoWithPin(tmp_path, S_REGISTRY_DIGEST),
    )
    assert dictAnswer["bPinnedImageIsLive"] is None


def test_an_unpinned_envelope_answers_none_never_false(tmp_path):
    dictAnswer = pipelineServer.fdictAssessEnvelopeImageCurrency(
        _fdictContext({"sImageDigest": S_REGISTRY_DIGEST,
                       "sImageId": S_IMAGE_ID}),
        "cid1", _ffilesRepoWithPin(tmp_path, None),
    )
    assert dictAnswer["bPinnedImageIsLive"] is None


def test_connect_records_the_identity_for_the_session(monkeypatch):
    dictCtx = {}
    monkeypatch.setattr(
        "vaibify.config.registryManager.fbIsHostProject",
        lambda sResourceId: False,
    )
    with patch.object(
        environmentSnapshot, "fdictCaptureLiveImageIdentity",
        return_value={"sImageDigest": S_REGISTRY_DIGEST,
                      "sImageId": S_IMAGE_ID},
    ):
        pipelineServer.fnCaptureLiveImageIdentityAtConnect(
            dictCtx, "cid1",
        )
    assert dictCtx["dictLiveImageIdentities"]["cid1"] == {
        "sImageDigest": S_REGISTRY_DIGEST, "sImageId": S_IMAGE_ID,
    }


def test_a_failed_capture_records_nothing(monkeypatch):
    """Absent must stay absent: downstream reads it as 'unknown'."""
    dictCtx = {}
    monkeypatch.setattr(
        "vaibify.config.registryManager.fbIsHostProject",
        lambda sResourceId: False,
    )
    with patch.object(
        environmentSnapshot, "fdictCaptureLiveImageIdentity",
        side_effect=RuntimeError("docker unreachable"),
    ):
        pipelineServer.fnCaptureLiveImageIdentityAtConnect(
            dictCtx, "cid1",
        )
    assert "cid1" not in dictCtx.get("dictLiveImageIdentities", {})


def test_a_host_project_is_skipped(monkeypatch):
    dictCtx = {}
    monkeypatch.setattr(
        "vaibify.config.registryManager.fbIsHostProject",
        lambda sResourceId: True,
    )
    pipelineServer.fnCaptureLiveImageIdentityAtConnect(dictCtx, "hostA")
    assert dictCtx.get("dictLiveImageIdentities", {}) == {}
