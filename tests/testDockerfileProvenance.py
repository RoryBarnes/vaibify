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
        "flistResolveOverlayNamesForContainer",
        lambda sName, sImageDigest="": [],
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
def test_the_export_describes_the_pinned_image_not_the_config(
    tmp_path, monkeypatch,
):
    """The exported Dockerfile composes what the PINNED image holds.

    The envelope pins the agent-free environment while ``vaibify.yml``
    still names the coding agents stacked on it. Composed from the
    config, the header would claim agents the pinned image does not
    hold, the recipe fingerprint would match no image, and a reader
    obtaining the image would be refused for a disagreement the author
    never made.

    Kills: resolving the overlays from ``vaibify.yml`` when the pinned
    image carries its own label.
    """
    from vaibify.config import registryManager
    from vaibify.reproducibility import environmentSnapshot, imageDockerfileExport
    from vaibify.reproducibility.dockerfileComposer import (
        flistExtractOverlayOrder,
    )
    (tmp_path / "Dockerfile").write_text(S_BASE)
    for sOverlay in ("jupyter", "claude"):
        pathOverlay = tmp_path / imageBuilder._DICT_OVERLAY_DOCKERFILE_MAP[sOverlay]
        pathOverlay.parent.mkdir(parents=True, exist_ok=True)
        pathOverlay.write_text(f"FROM ${{BASE_IMAGE}}\nRUN echo {sOverlay}\n")
    pathConfig = tmp_path / "vaibify.yml"
    pathConfig.write_text("projectName: proj\n")
    monkeypatch.setattr(
        imageDockerfileExport.resources, "fpathContainerImageRoot",
        lambda: tmp_path,
    )
    monkeypatch.setattr(registryManager, "flistGetAllProjects", lambda: [
        {"sContainerName": "proj", "sConfigPath": str(pathConfig)},
    ])
    monkeypatch.setattr(
        "vaibify.config.projectConfig.fconfigLoadFromFile", lambda sPath: None,
    )
    monkeypatch.setattr(
        imageBuilder, "flistDetermineOverlays",
        lambda config: ["jupyter", "claude"],
    )
    monkeypatch.setattr(
        environmentSnapshot, "_fsInspectFormatValue",
        lambda sTarget, sFormat: "jupyter\n",
    )
    sText = imageDockerfileExport.fsBuildImageDockerfileText(
        "proj", "sha256:" + "e" * 64,
    )
    assert flistExtractOverlayOrder(sText) == ["jupyter"]
    assert "echo claude" not in sText
    monkeypatch.setattr(
        environmentSnapshot, "_fsInspectFormatValue",
        lambda sTarget, sFormat: "<no value>\n",
    )
    assert flistExtractOverlayOrder(
        imageDockerfileExport.fsBuildImageDockerfileText("proj", "sha256:x"),
    ) == ["jupyter", "claude"], "an unlabelled image falls back to its config"


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


def _fdictLabelsOfBuild(saCommand):
    """Return the ``--label`` pairs one docker build argv stamps."""
    dictLabels = {}
    for iIndex, sArgument in enumerate(saCommand):
        if sArgument == "--label":
            sKey, _, sValue = saCommand[iIndex + 1].partition("=")
            dictLabels[sKey] = sValue
    return dictLabels


@pytest.mark.falsification
def test_each_stage_is_labelled_with_what_it_holds(tmp_path):
    """A stage's labels describe THAT stage, never the finished chain.

    The stage below the first coding agent is the environment a
    researcher publishes, and a reproducer proves which overlays it
    holds by these very labels. When every stage carried the whole
    chain's list, the agent-free stage claimed agents it did not hold
    and could never be published as agent-free. Agent stages name the
    agent-free stage's image ID; environment stages name nothing, even
    when their FROM image named something.

    Kills: stamping the whole chain's overlay list on every stage.
    """
    from types import SimpleNamespace
    from vaibify.reproducibility.dockerfileComposer import (
        S_ENVIRONMENT_IMAGE_LABEL,
        S_OVERLAYS_IMAGE_LABEL,
    )
    (tmp_path / "Dockerfile").write_text(S_BASE)
    for sOverlay in ("jupyter", "claude", "codex"):
        pathOverlay = tmp_path / imageBuilder._DICT_OVERLAY_DOCKERFILE_MAP[sOverlay]
        pathOverlay.parent.mkdir(parents=True, exist_ok=True)
        pathOverlay.write_text(f"FROM ${{BASE_IMAGE}}\nRUN echo {sOverlay}\n")
    config = SimpleNamespace(
        sProjectName="proj", sBaseImage="python:3.12",
        sPythonVersion="3.12", sContainerUser="researcher",
        sWorkspaceRoot="/workspace", sPackageManager="pip",
        features=SimpleNamespace(bGpu=False, bLatex=False),
    )
    listCommands = []
    listInspected = []
    with patch.object(
        imageBuilder, "_fnRunDockerBuild",
        side_effect=lambda saCommand: listCommands.append(saCommand),
    ), patch.object(
        imageBuilder, "flistDetermineOverlays",
        return_value=["jupyter", "claude", "codex"],
    ), patch.object(
        imageBuilder, "fsReadImageId",
        side_effect=lambda sRef: listInspected.append(sRef) or "sha256:env",
    ), patch.object(imageBuilder, "_fnPruneDanglingImages"):
        imageBuilder.fnBuildImage(config, str(tmp_path))
    listStages = [
        _fdictLabelsOfBuild(saCommand) for saCommand in listCommands
        if "--label" in saCommand
    ]
    assert [d[S_OVERLAYS_IMAGE_LABEL] for d in listStages] == [
        "", "jupyter", "jupyter,claude", "jupyter,claude,codex",
    ]
    assert [d[S_ENVIRONMENT_IMAGE_LABEL] for d in listStages] == [
        "", "", "sha256:env", "sha256:env",
    ]
    assert listInspected == ["proj:jupyter"], (
        "the environment is the stage BELOW the first agent"
    )
    listTexts = [
        (s, (tmp_path / imageBuilder._DICT_OVERLAY_DOCKERFILE_MAP[s]).read_text())
        for s in ("jupyter", "claude", "codex")
    ]
    assert [d[S_RECIPE_IMAGE_LABEL] for d in listStages] == [
        fsComputeRecipeFingerprint(S_BASE, listTexts[:iCount])
        for iCount in range(4)
    ]


def test_every_environment_overlay_precedes_every_agent_side_overlay():
    """The published environment is a PREFIX of the chain, by construction.

    ``fnBuildImage`` names the environment as the stage below the first
    agent-side overlay. Were an environment overlay ever ordered after
    one, it would be built above an agent and silently left out of the
    environment.
    """
    listOrder = imageBuilder.flistCanonicalOverlayOrder()
    listEnvironment = [
        s for s in listOrder if imageBuilder.fbOverlayBelongsToEnvironment(s)
    ]
    assert listOrder[:len(listEnvironment)] == listEnvironment


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
