"""Which overlays built a container, and when its Dockerfile may be written.

The project registry lives under the researcher's home directory, so
it is the one boundary replaced: ``flistGetAllProjects`` answers with
fixture projects whose container NAMES differ from their project
names, so a lookup keyed on the wrong field finds nothing. The
``vaibify.yml`` they point at is real and parsed by the real loader.
"""

import pytest

from vaibify.config import registryManager
from vaibify.reproducibility import imageDockerfileExport


S_CONTAINER_ALPHA = "containerAlpha"
S_CONTAINER_BETA = "containerBeta"


def fnWriteConfig(pathConfig, listEnabledFeatures):
    """Write a minimal vaibify.yml enabling the named features."""
    sFeatures = "".join(
        f"  {sFeature}: true\n" for sFeature in listEnabledFeatures
    )
    pathConfig.write_text(
        "projectName: projectAlpha\nfeatures:\n" + sFeatures,
        encoding="utf-8",
    )


def fnRegisterProjects(monkeypatch, listProjects):
    """Answer the host registry with ``listProjects``."""
    monkeypatch.setattr(
        registryManager, "flistGetAllProjects", lambda: listProjects,
    )


def testOverlaysComeFromTheConfigOfTheMatchingContainer(
    monkeypatch, tmp_path,
):
    """The container's own vaibify.yml decides, not a neighbor's."""
    pathAlpha = tmp_path / "alpha.yml"
    pathBeta = tmp_path / "beta.yml"
    fnWriteConfig(pathAlpha, ["jupyter", "julia"])
    fnWriteConfig(pathBeta, ["rLanguage"])
    fnRegisterProjects(monkeypatch, [
        None,
        {"sName": S_CONTAINER_ALPHA, "sContainerName": S_CONTAINER_BETA,
         "sConfigPath": str(pathBeta)},
        {"sName": "projectAlpha", "sContainerName": S_CONTAINER_ALPHA,
         "sConfigPath": str(pathAlpha)},
    ])
    assert imageDockerfileExport.flistResolveOverlayNamesForContainer(
        S_CONTAINER_ALPHA,
    ) == ["jupyter", "julia"]
    assert imageDockerfileExport.flistResolveOverlayNamesForContainer(
        S_CONTAINER_BETA,
    ) == ["rlang"]


@pytest.mark.parametrize("sConfigPath", ["", "missing.yml"])
def testAnUnreadableConfigComposesTheBaseOnly(
    monkeypatch, tmp_path, sConfigPath,
):
    """No config path, or one that is gone, yields no overlays."""
    fnRegisterProjects(monkeypatch, [{
        "sContainerName": S_CONTAINER_ALPHA,
        "sConfigPath": str(tmp_path / sConfigPath) if sConfigPath else "",
    }])
    assert imageDockerfileExport.flistResolveOverlayNamesForContainer(
        S_CONTAINER_ALPHA,
    ) == []


def testAnUnregisteredContainerComposesTheBaseOnly(monkeypatch):
    """A container the registry does not know has no overlays."""
    fnRegisterProjects(monkeypatch, [])
    assert imageDockerfileExport.flistResolveOverlayNamesForContainer(
        S_CONTAINER_ALPHA,
    ) == []


def testOverlaysWithoutAPackagedFileAreSkippedNotFaked(tmp_path):
    """Unknown names and missing files drop out; order is preserved."""
    (tmp_path / "Dockerfile.julia").write_text("FROM julia\n", "utf-8")
    (tmp_path / "Dockerfile.claude").write_text("FROM claude\n", "utf-8")
    listPairs = imageDockerfileExport._flistTReadOverlays(
        tmp_path, ["julia", "notAnOverlay", "jupyter", "claude"],
    )
    assert listPairs == [
        ("julia", "FROM julia\n"), ("claude", "FROM claude\n"),
    ]


def testComposedDockerfileNamesOnlyTheOverlaysItRead(monkeypatch, tmp_path):
    """The exported text carries the digest and omits a skipped overlay."""
    pathConfig = tmp_path / "vaibify.yml"
    fnWriteConfig(pathConfig, ["jupyter", "julia"])
    fnRegisterProjects(monkeypatch, [{
        "sContainerName": S_CONTAINER_ALPHA, "sConfigPath": str(pathConfig),
    }])
    pathImageRoot = tmp_path / "containerImage"
    pathImageRoot.mkdir()
    (pathImageRoot / "Dockerfile").write_text(
        "FROM ubuntu:24.04\nRUN echo base\n", "utf-8",
    )
    (pathImageRoot / "Dockerfile.julia").write_text(
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nRUN echo julia-layer\n", "utf-8",
    )
    monkeypatch.setattr(
        imageDockerfileExport.resources, "fpathContainerImageRoot",
        lambda: pathImageRoot,
    )
    sDigest = "registry.example/projectAlpha@sha256:" + "c" * 64
    sText = imageDockerfileExport.fsBuildImageDockerfileText(
        S_CONTAINER_ALPHA, sImageDigest=sDigest,
    )
    assert "julia-layer" in sText
    assert "echo base" in sText
    assert sDigest in sText
    assert "jupyter" not in sText


class _UnreadableDockerfileRepo:
    """A repo whose Dockerfile exists but cannot be read."""

    def fbIsFile(self, sRelPath):
        return sRelPath == "Dockerfile"

    def fsReadText(self, sRelPath):
        raise OSError("permission denied")


def testAnUnreadableExistingDockerfileIsNeverOverwritten():
    """Could-not-check refuses: the file may be the researcher's own."""
    sRefusal = imageDockerfileExport.fsRefusalIfDockerfileNotReplaceable(
        _UnreadableDockerfileRepo(),
    )
    assert "could not be read" in sRefusal
    assert "Refusing to overwrite" in sRefusal


def testAResearchersOwnDockerfileIsRefusedAndAbsenceIsAllowed(tmp_path):
    """A hand-written Dockerfile refuses; no Dockerfile at all is writable."""
    assert imageDockerfileExport.fsRefusalIfDockerfileNotReplaceable(
        str(tmp_path),
    ) == ""
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n", "utf-8")
    assert "did not generate" in (
        imageDockerfileExport.fsRefusalIfDockerfileNotReplaceable(
            str(tmp_path),
        )
    )
