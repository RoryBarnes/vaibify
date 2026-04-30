"""Fixture-based tests locking in Overleaf git-bridge behavior assumptions.

These tests exist to fail loudly when Overleaf changes any of the
documented behaviors encoded in ``vaibify.reproducibility.overleafMirror``
(see that module's "Overleaf behavior adapter" docstring section).

No network is involved: each test supplies a static ``git ls-tree``
output fragment that simulates an observed Overleaf quirk and asserts
the adapter still interprets the fragment as we expect. If any of
these fail, Overleaf has likely changed its bridge semantics and the
adapter module is the only place that needs updating.
"""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import overleafMirror
from vaibify.reproducibility.overleafMirror import (
    S_OVERLEAF_WEB_UI_COMMIT_MESSAGE,
    flistDetectCaseCollisions,
    flistListMirrorTree,
)


__all__ = [
    "test_overleaf_surfaces_case_variants_as_distinct_entries",
    "test_overleaf_collision_detection_against_case_variant_fixture",
    "test_overleaf_same_case_fixture_has_no_collisions",
    "test_overleaf_web_ui_commit_message_pattern_is_recognized",
]


_S_FIXTURE_WITH_CASE_VARIANTS = (
    "100644 blob deadbeefdeadbeefdeadbeefdeadbeefdeadbeef    42"
    "\tFigures/x.pdf\n"
    "100644 blob deadbeefdeadbeefdeadbeefdeadbeefdeadbeef    42"
    "\tfigures/x.pdf\n"
)


_S_FIXTURE_SINGLE_CASE = (
    "100644 blob cafecafecafecafecafecafecafecafecafecafe    99"
    "\tfigures/x.pdf\n"
)


def _fnMakeFakeMirror(tmp_path, sProjectId):
    """Create a fake ``.git`` subdir so ``_fbMirrorExists`` returns True."""
    import os
    sMirror = os.path.join(
        str(tmp_path), ".vaibify", "overleaf-mirrors", sProjectId,
    )
    os.makedirs(os.path.join(sMirror, ".git"))
    return sMirror


def _fnPatchLsTreeOutput(sOutput):
    """Patch subprocess.run so git ls-tree yields ``sOutput``."""
    mockResult = MagicMock(returncode=0, stdout=sOutput, stderr="")
    return patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        return_value=mockResult,
    )


# ── Case variants surfaced as distinct ls-tree rows ──────────────


def test_overleaf_surfaces_case_variants_as_distinct_entries(
    tmp_path, monkeypatch,
):
    """Overleaf exposes both ``Figures/x.pdf`` and ``figures/x.pdf``.

    If this ever stops being true, the case-collision adapter can be
    simplified (or removed), but we need to know.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _fnMakeFakeMirror(tmp_path, "overleafBehaviorA")
    with _fnPatchLsTreeOutput(_S_FIXTURE_WITH_CASE_VARIANTS):
        listEntries = flistListMirrorTree("overleafBehaviorA")
    listPaths = sorted(d["sPath"] for d in listEntries)
    assert listPaths == ["Figures/x.pdf", "figures/x.pdf"]


def test_overleaf_collision_detection_against_case_variant_fixture(
    tmp_path, monkeypatch,
):
    """First-seen wins when Overleaf surfaces duplicate case variants.

    The mirror contains both ``Figures/x.pdf`` and ``figures/x.pdf``
    at identical blob SHAs (Overleaf storage treats them as one
    file; the git bridge surfaces both). The adapter's
    ``_fdictLowercaseRemoteIndex`` helper records the first ls-tree
    row per lowercased key as the canonical path. Because
    ``Figures/x.pdf`` sorts before ``figures/x.pdf`` in the fixture,
    the adapter treats ``Figures/x.pdf`` as canonical — so a push
    targeting ``figures/`` is reported as a case-collision against
    that canonical. This is the documented behavior; the test fails
    if Overleaf (or git's ordering) changes in a way that flips the
    canonical choice.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _fnMakeFakeMirror(tmp_path, "overleafBehaviorB")
    with _fnPatchLsTreeOutput(_S_FIXTURE_WITH_CASE_VARIANTS):
        listCollisions = flistDetectCaseCollisions(
            "overleafBehaviorB", ["/local/x.pdf"], "figures",
        )
    assert len(listCollisions) == 1
    assert listCollisions[0]["sTypedRemotePath"] == "figures/x.pdf"
    assert listCollisions[0]["sCanonicalRemotePath"] == "Figures/x.pdf"


def test_overleaf_same_case_fixture_has_no_collisions(
    tmp_path, monkeypatch,
):
    """Single ``figures/x.pdf`` entry: pushing to ``figures`` is safe."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _fnMakeFakeMirror(tmp_path, "overleafBehaviorC")
    with _fnPatchLsTreeOutput(_S_FIXTURE_SINGLE_CASE):
        listCollisions = flistDetectCaseCollisions(
            "overleafBehaviorC", ["/local/x.pdf"], "figures",
        )
    assert listCollisions == []


def test_overleaf_same_case_fixture_detects_wrong_case_push(
    tmp_path, monkeypatch,
):
    """Fixture has ``figures/x.pdf``; a ``Figures/`` push is a collision."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _fnMakeFakeMirror(tmp_path, "overleafBehaviorD")
    with _fnPatchLsTreeOutput(_S_FIXTURE_SINGLE_CASE):
        listCollisions = flistDetectCaseCollisions(
            "overleafBehaviorD", ["/local/x.pdf"], "Figures",
        )
    assert len(listCollisions) == 1
    assert listCollisions[0]["sCanonicalRemotePath"] == "figures/x.pdf"
    assert listCollisions[0]["sTypedRemotePath"] == "Figures/x.pdf"


# ── Web-UI commit message pattern ───────────────────────────────


def test_overleaf_web_ui_commit_message_pattern_is_recognized():
    """The adapter exposes the Overleaf web-UI commit message verbatim.

    The string is documented as a stable Overleaf signal; the adapter
    is the only place that should encode it. A future "was last edited
    in the web UI" hint can rely on comparing commit messages against
    this constant.
    """
    assert S_OVERLEAF_WEB_UI_COMMIT_MESSAGE == "Update on Overleaf."


def test_overleaf_web_ui_commit_message_is_exported():
    """The constant must be on the adapter's public surface."""
    assert (
        "S_OVERLEAF_WEB_UI_COMMIT_MESSAGE"
        in overleafMirror.__all__
    )


# ── Partial-clone contract: blobs are metadata-only ─────────────


def test_partial_clone_filter_flag_is_used(tmp_path, monkeypatch):
    """Refreshing the mirror must pass ``--filter=blob:none``.

    This is the load-bearing assumption for the diff logic: blob
    contents are never fetched, only tree metadata. If Overleaf ever
    stops honoring the filter, we need to reassess the adapter.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    sAskpass = str(tmp_path / "askpass.py")
    with open(sAskpass, "w") as handle:
        handle.write("#!/usr/bin/env python3\n")
    _fnMakeFakeMirror(tmp_path, "overleafBehaviorE")
    listCapturedArgs = []

    def fnFakeRun(listArgv, **kwargs):
        listCapturedArgs.append(listArgv)
        mock = MagicMock(returncode=0, stdout="sha\n", stderr="")
        return mock

    with patch(
        "vaibify.reproducibility.overleafMirror.subprocess.run",
        side_effect=fnFakeRun,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsWriteAskpassScript",
        return_value=sAskpass,
    ):
        overleafMirror.fbRefreshMirror("overleafBehaviorE", "tok")

    listFetchCalls = [c for c in listCapturedArgs if "fetch" in c]
    assert listFetchCalls
    for listCall in listFetchCalls:
        assert "--filter=blob:none" in listCall


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
