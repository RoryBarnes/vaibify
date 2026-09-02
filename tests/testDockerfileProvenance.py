"""The exported Dockerfile is PROVEN to describe the pinned image.

The repo Dockerfile is composed from vaibify's packaged build chain,
and nothing tied it to the image the envelope pins: rebuild with a
changed overlay set (or upgrade vaibify) and the file keeps looking
like provenance while describing a recipe that never built the pinned
image. The 2026-09-01 ruling adds build-time evidence on both ends —
the builder stamps a fingerprint of the exact texts it built with
onto the image as a label, the export stamps the fingerprint of the
texts it composed from into the header — so equality is a proof.
Recompose-and-compare could not be: it flags every vaibify upgrade as
staleness the envelope does not have.
"""

from unittest.mock import patch

import pytest

from vaibify.docker import imageBuilder
from vaibify.reproducibility import dockerfileComposer
from vaibify.reproducibility.dockerfileComposer import (
    S_RECIPE_HEADER_PREFIX,
    S_RECIPE_IMAGE_LABEL,
    fsComposeImageDockerfile,
    fsComputeRecipeFingerprint,
    fsExtractRecipeFingerprint,
)


S_BASE = "FROM python:3.12\nRUN echo base\n"
LIST_OVERLAYS = [("jupyter", "ARG BASE_IMAGE=x\nFROM ${BASE_IMAGE}\n")]


def test_the_fingerprint_is_stable_and_order_sensitive():
    sFingerprint = fsComputeRecipeFingerprint(S_BASE, LIST_OVERLAYS)
    assert sFingerprint == fsComputeRecipeFingerprint(
        S_BASE, LIST_OVERLAYS,
    )
    assert sFingerprint != fsComputeRecipeFingerprint(
        S_BASE + "RUN echo changed\n", LIST_OVERLAYS,
    )
    listReordered = [
        ("latex", "ARG BASE_IMAGE=x\nFROM ${BASE_IMAGE}\n"),
    ] + LIST_OVERLAYS
    assert fsComputeRecipeFingerprint(
        S_BASE, listReordered,
    ) != fsComputeRecipeFingerprint(
        S_BASE, list(reversed(listReordered)),
    ), "overlay order IS the semantics and must move the fingerprint"


def test_the_separators_keep_the_digest_injective():
    """Two overlay splits of the same bytes must not collide."""
    assert fsComputeRecipeFingerprint(
        "AB", [("x", "CD")],
    ) != fsComputeRecipeFingerprint("A", [("Bx", "CD")])


def test_the_header_carries_the_fingerprint_and_extract_reads_it():
    sFingerprint = fsComputeRecipeFingerprint(S_BASE, LIST_OVERLAYS)
    sText = fsComposeImageDockerfile(
        S_BASE, LIST_OVERLAYS, sImageDigest="img@sha256:" + "ab" * 32,
        sRecipeFingerprint=sFingerprint,
    )
    assert fsExtractRecipeFingerprint(sText) == sFingerprint


def test_a_pre_fingerprint_export_extracts_empty_never_wrong():
    sText = fsComposeImageDockerfile(S_BASE, LIST_OVERLAYS)
    assert fsExtractRecipeFingerprint(sText) == ""


def test_the_export_stamps_the_fingerprint_of_what_it_composed(
    tmp_path, monkeypatch,
):
    from vaibify.reproducibility import imageDockerfileExport
    (tmp_path / "Dockerfile").write_text(S_BASE)
    monkeypatch.setattr(
        imageDockerfileExport,
        "flistResolveOverlayNamesForContainer", lambda sName: [],
    )
    monkeypatch.setattr(
        imageDockerfileExport.resources, "fpathContainerImageRoot",
        lambda: tmp_path,
    )
    sText = imageDockerfileExport.fsBuildImageDockerfileText("proj")
    assert fsExtractRecipeFingerprint(sText) == (
        fsComputeRecipeFingerprint(S_BASE, [])
    )


@pytest.mark.falsification
def test_the_builder_labels_every_build_with_the_chain_fingerprint(
    tmp_path,
):
    """The image-side half of the proof.

    Without the label the check can never determine anything, every
    answer is None, and Dockerfile staleness goes back to being
    invisible — while every rendering surface still works, because
    None is the honest 'undetermined' they all accept.

    Kills: In imageBuilder.fnBuildImage, pass sRecipeFingerprint=""
    to fnBuildBase and fnApplyOverlay instead of the computed chain
    fingerprint.
    """
    from types import SimpleNamespace
    (tmp_path / "Dockerfile").write_text(S_BASE)
    (tmp_path / "dockerfiles").mkdir(exist_ok=True)
    config = SimpleNamespace(
        sProjectName="proj", sBaseImage="python:3.12",
        sPythonVersion="3.12", sContainerUser="researcher",
        sWorkspaceRoot="/workspace", sPackageManager="pip",
        features=SimpleNamespace(bGpu=False, bLatex=False),
    )
    listCommands = []
    with patch.object(
        imageBuilder, "_fnRunDockerBuild",
        side_effect=lambda saCommand: listCommands.append(saCommand),
    ), patch.object(
        imageBuilder, "flistDetermineOverlays", return_value=[],
    ), patch.object(
        imageBuilder, "_fnPruneDanglingImages",
    ):
        imageBuilder.fnBuildImage(config, str(tmp_path))
    sExpected = fsComputeRecipeFingerprint(S_BASE, [])
    listBuildCommands = [
        saCommand for saCommand in listCommands
        if "--label" in saCommand
    ]
    assert listBuildCommands, "no build carried the recipe label"
    for saCommand in listBuildCommands:
        sLabel = saCommand[saCommand.index("--label") + 1]
        assert sLabel == f"{S_RECIPE_IMAGE_LABEL}={sExpected}"
        assert saCommand[-1] == str(tmp_path), (
            "the label displaced the build-context path from the "
            "end of the argv"
        )


@pytest.mark.falsification
def test_a_mismatched_fingerprint_is_reported_not_absorbed(tmp_path):
    """The comparison itself: unequal fingerprints answer False.

    Kills: In fdictAssessDockerfileProvenance, answer
    bDockerfileDescribesPinnedImage True instead of comparing the
    header fingerprint against the image label.
    """
    from vaibify.gui.routes.reproducibilityRoutes import (
        fdictAssessDockerfileProvenance,
    )
    sHeaderPrint = "ab" * 32
    _fnWriteRepoDockerfile(tmp_path, sHeaderPrint)
    _fnWritePin(tmp_path)
    with patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fsReadImageRecipeLabel", return_value="cd" * 32,
    ):
        dictAnswer = fdictAssessDockerfileProvenance(str(tmp_path))
    assert dictAnswer["bDockerfileDescribesPinnedImage"] is False


def test_matching_fingerprints_answer_true(tmp_path):
    from vaibify.gui.routes.reproducibilityRoutes import (
        fdictAssessDockerfileProvenance,
    )
    sPrint = "ab" * 32
    _fnWriteRepoDockerfile(tmp_path, sPrint)
    _fnWritePin(tmp_path)
    with patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fsReadImageRecipeLabel", return_value=sPrint,
    ):
        dictAnswer = fdictAssessDockerfileProvenance(str(tmp_path))
    assert dictAnswer["bDockerfileDescribesPinnedImage"] is True


def test_an_unlabelled_image_answers_none_never_false(tmp_path):
    """Pre-label images must not light warnings they cannot earn."""
    from vaibify.gui.routes.reproducibilityRoutes import (
        fdictAssessDockerfileProvenance,
    )
    _fnWriteRepoDockerfile(tmp_path, "ab" * 32)
    _fnWritePin(tmp_path)
    with patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fsReadImageRecipeLabel", return_value="",
    ):
        dictAnswer = fdictAssessDockerfileProvenance(str(tmp_path))
    assert dictAnswer["bDockerfileDescribesPinnedImage"] is None


def test_a_hand_written_dockerfile_is_not_applicable(tmp_path):
    from vaibify.gui.routes.reproducibilityRoutes import (
        fdictAssessDockerfileProvenance,
    )
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    _fnWritePin(tmp_path)
    dictAnswer = fdictAssessDockerfileProvenance(str(tmp_path))
    assert dictAnswer["bDockerfileDescribesPinnedImage"] is None


def _fnWriteRepoDockerfile(tmp_path, sFingerprint):
    (tmp_path / "Dockerfile").write_text(
        dockerfileComposer.S_GENERATED_MARKER + "\n"
        + S_RECIPE_HEADER_PREFIX + sFingerprint + "\n"
        + S_BASE,
    )


def _fnWritePin(tmp_path):
    import json
    (tmp_path / ".vaibify").mkdir(exist_ok=True)
    (tmp_path / ".vaibify" / "environment.json").write_text(json.dumps(
        {"dictContainer": {
            "sImageDigest": "img@sha256:" + "ee" * 32}},
    ))
