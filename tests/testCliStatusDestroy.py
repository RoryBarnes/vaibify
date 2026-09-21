"""Tests for commandStatus and commandDestroy uncovered paths."""

import re

import sys

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.commandStatus import (
    fnStatusCommand,
    fnShowDaemonStatus,
    fnShowImageStatus,
    fnShowVolumeStatus,
    fnShowContainerStatus,
)
from vaibify.cli.commandDestroy import (
    fnDestroyCommand,
    fnRemoveVolume,
    flistRemoveProjectImages,
)


def _fMockDockerModule():
    """Return a mock docker module with stable from_env."""
    mockModule = MagicMock()
    mockClient = MagicMock()
    mockModule.from_env.return_value = mockClient
    mockModule.errors.NotFound = type(
        "NotFound", (Exception,), {}
    )
    mockModule.errors.ImageNotFound = type(
        "ImageNotFound", (Exception,), {}
    )
    mockModule.errors.APIError = type(
        "APIError", (Exception,), {}
    )
    mockModule._mockClient = mockClient
    return mockModule


# -----------------------------------------------------------------------
# fnShowDaemonStatus
# -----------------------------------------------------------------------


def test_fnShowDaemonStatus_reachable(capsys):
    mockDocker = _fMockDockerModule()
    mockClient = mockDocker._mockClient
    fnShowDaemonStatus(mockClient)
    sCaptured = capsys.readouterr().out
    assert "running" in sCaptured


def test_fnShowDaemonStatus_unreachable(capsys):
    mockClient = MagicMock()
    mockClient.ping.side_effect = Exception("fail")
    fnShowDaemonStatus(mockClient)
    sCaptured = capsys.readouterr().out
    assert "unavailable" in sCaptured


# -----------------------------------------------------------------------
# fnShowImageStatus
# -----------------------------------------------------------------------


def test_fnShowImageStatus_found(capsys):
    mockClient = MagicMock()
    mockImage = MagicMock()
    mockImage.attrs = {"Created": "2024-01-01"}
    mockClient.images.get.return_value = mockImage
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowImageStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "testproj:latest" in sCaptured
    assert "built" in sCaptured


def test_fnShowImageStatus_not_found(capsys):
    mockClient = MagicMock()
    mockClient.images.get.side_effect = Exception("nope")
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowImageStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "not found" in sCaptured


# -----------------------------------------------------------------------
# fnShowVolumeStatus
# -----------------------------------------------------------------------


def test_fnShowVolumeStatus_exists(capsys):
    mockClient = MagicMock()
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowVolumeStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "exists" in sCaptured


def test_fnShowVolumeStatus_not_found(capsys):
    mockClient = MagicMock()
    mockClient.volumes.get.side_effect = Exception("nope")
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowVolumeStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "not found" in sCaptured


# -----------------------------------------------------------------------
# fnShowContainerStatus
# -----------------------------------------------------------------------


def test_fnShowContainerStatus_running(capsys):
    mockClient = MagicMock()
    mockContainer = MagicMock()
    mockContainer.name = "testproj"
    mockContainer.status = "running"
    mockClient.containers.list.return_value = [mockContainer]
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowContainerStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "running" in sCaptured


def test_fnShowContainerStatus_none(capsys):
    mockClient = MagicMock()
    mockClient.containers.list.return_value = []
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowContainerStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "none" in sCaptured


def test_fnShowContainerStatus_error(capsys):
    mockClient = MagicMock()
    mockClient.containers.list.side_effect = Exception("fail")
    mockConfig = SimpleNamespace(sProjectName="testproj")
    fnShowContainerStatus(mockClient, mockConfig)
    sCaptured = capsys.readouterr().out
    assert "unable" in sCaptured


# -----------------------------------------------------------------------
# fnStatusCommand CLI — full invocation
# -----------------------------------------------------------------------


@patch("vaibify.cli.commandStatus.fbDockerAvailable",
       return_value=False)
def test_status_no_docker(mockAvail):
    runner = CliRunner()
    result = runner.invoke(fnStatusCommand, [])
    assert result.exit_code != 0
    assert "docker Python package is missing" in result.output
    assert "pip install --force-reinstall vaibify" in result.output


@patch("vaibify.cli.commandStatus.fnShowContainerStatus")
@patch("vaibify.cli.commandStatus.fnShowVolumeStatus")
@patch("vaibify.cli.commandStatus.fnShowImageStatus")
@patch("vaibify.cli.commandStatus.fnShowDaemonStatus")
@patch("vaibify.cli.commandStatus.fconfigResolveProject")
@patch("vaibify.cli.commandStatus.fbDockerAvailable",
       return_value=True)
@patch("docker.from_env")
def test_status_full_run(
    mockDockerEnv, mockAvail, mockLoad, mockDaemon,
    mockImage, mockVolume, mockContainer,
):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(fnStatusCommand, [])
    assert result.exit_code == 0
    mockDaemon.assert_called_once()
    mockImage.assert_called_once()
    mockVolume.assert_called_once()
    mockContainer.assert_called_once()


# -----------------------------------------------------------------------
# fnRemoveVolume
# -----------------------------------------------------------------------


def test_fnRemoveVolume_success(capsys):
    mockDocker = _fMockDockerModule()
    with patch.dict("sys.modules", {"docker": mockDocker}):
        fnRemoveVolume("test-workspace")
    sCaptured = capsys.readouterr().out
    assert "Removed" in sCaptured


def test_fnRemoveVolume_not_found(capsys):
    mockDocker = _fMockDockerModule()
    mockDocker.from_env().volumes.get.side_effect = (
        mockDocker.errors.NotFound("nope")
    )
    with patch.dict("sys.modules", {"docker": mockDocker}):
        fnRemoveVolume("test-workspace")
    sCaptured = capsys.readouterr().out
    assert "does not exist" in sCaptured


def test_fnRemoveVolume_api_error():
    mockDocker = _fMockDockerModule()
    mockVolume = MagicMock()
    mockVolume.remove.side_effect = (
        mockDocker.errors.APIError("busy")
    )
    mockDocker.from_env().volumes.get.return_value = mockVolume
    with patch.dict("sys.modules", {"docker": mockDocker}):
        with pytest.raises(SystemExit):
            fnRemoveVolume("test-workspace")


# -----------------------------------------------------------------------
# flistRemoveProjectImages
# -----------------------------------------------------------------------


@pytest.mark.falsification
def testDestroyRemovesEveryTagAProjectBuildLeft():
    """A build leaves a chain, so destroying one tag destroys nothing much.

    The independent oracle is the builder's own tagging, stated in
    ``imageBuilder.fnBuildImage``: it tags ``:base``, then one tag per
    overlay in order, then ``:latest``. Removing only ``:latest``
    untags the tip and leaves every layer beneath it held by its own
    tag -- on a real six-tag project that is most of the bytes, with
    the command reporting success.

    Kills: narrowing the CLI's removal back to ``<project>:latest``.
    """
    listReferences = [
        "proj:base", "proj:claude", "proj:codex", "proj:latest",
    ]
    listAsked = []
    with patch(
        "vaibify.docker.imageBuilder.flistProjectImageReferences",
        return_value=listReferences,
    ), patch(
        "vaibify.docker.imageBuilder.fbRemoveImage",
        side_effect=lambda sReference: (
            listAsked.append(sReference) or True
        ),
    ):
        listRemoved = flistRemoveProjectImages("proj")
    assert listAsked == listReferences, (
        "every tag the build left must be removed, not only the tip"
    )
    assert listRemoved == listReferences


def test_flistRemoveProjectImages_reports_each_removal(capsys):
    with patch(
        "vaibify.docker.imageBuilder.flistProjectImageReferences",
        return_value=["proj:base", "proj:latest"],
    ), patch(
        "vaibify.docker.imageBuilder.fbRemoveImage", return_value=True,
    ):
        flistRemoveProjectImages("proj")
    sCaptured = capsys.readouterr().out
    assert "Removed image: proj:base" in sCaptured
    assert "Removed image: proj:latest" in sCaptured


def test_flistRemoveProjectImages_says_when_there_are_none(capsys):
    with patch(
        "vaibify.docker.imageBuilder.flistProjectImageReferences",
        return_value=[],
    ):
        assert flistRemoveProjectImages("proj") == []
    assert "No images found" in capsys.readouterr().out


def test_flistRemoveProjectImages_names_a_tag_it_could_not_remove(capsys):
    with patch(
        "vaibify.docker.imageBuilder.flistProjectImageReferences",
        return_value=["proj:base", "proj:latest"],
    ), patch(
        "vaibify.docker.imageBuilder.fbRemoveImage",
        side_effect=[False, True],
    ):
        assert flistRemoveProjectImages("proj") == ["proj:latest"]
    sCaptured = capsys.readouterr().out
    assert "Could not remove the image proj:base" in sCaptured


def test_flistRemoveProjectImages_exits_when_the_listing_fails():
    with patch(
        "vaibify.docker.imageBuilder.flistProjectImageReferences",
        side_effect=RuntimeError("docker images failed"),
    ):
        with pytest.raises(SystemExit):
            flistRemoveProjectImages("proj")


# -----------------------------------------------------------------------
# fnDestroyCommand CLI — full invocation
# -----------------------------------------------------------------------


@patch("vaibify.cli.commandDestroy.fbDockerAvailable",
       return_value=True)
@patch("vaibify.cli.commandDestroy.fconfigResolveProject")
@patch("vaibify.cli.commandDestroy.fnRemoveVolume")
def test_destroy_confirm_yes(
    mockRemove, mockLoad, mockAvail,
):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(fnDestroyCommand, [], input="y\nn\n")
    assert result.exit_code == 0
    mockRemove.assert_called_once()
    assert "Destroy complete" in result.output


@patch("vaibify.cli.commandDestroy.fbDockerAvailable",
       return_value=True)
@patch("vaibify.cli.commandDestroy.fconfigResolveProject")
def test_destroy_confirm_no(mockLoad, mockAvail):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(fnDestroyCommand, [], input="n\n")
    assert "Aborted" in result.output


@patch("vaibify.cli.commandDestroy.fbDockerAvailable",
       return_value=True)
@patch("vaibify.cli.commandDestroy.fconfigResolveProject")
@patch("vaibify.cli.commandDestroy.fnRemoveVolume")
@patch("vaibify.cli.commandDestroy.flistRemoveProjectImages")
def test_destroy_also_image(
    mockRemoveImg, mockRemoveVol, mockLoad, mockAvail,
):
    mockLoad.return_value = SimpleNamespace(
        sProjectName="proj"
    )
    runner = CliRunner()
    result = runner.invoke(fnDestroyCommand, [], input="y\ny\n")
    assert result.exit_code == 0
    mockRemoveVol.assert_called_once()
    mockRemoveImg.assert_called_once()


# --- PROOF level section (fdictBuildLevelSection / _fnEmitProofSection) ---

def _fconfig(sName="proj"):
    return SimpleNamespace(sProjectName=sName)


def test_level_section_unavailable_when_no_project():
    """A LookupError from the loader becomes an honest unavailable dict."""
    from vaibify.cli import commandStatus
    with patch(
        "vaibify.cli.commandStatus.fconnectionRequireDocker"
        if hasattr(commandStatus, "fconnectionRequireDocker")
        else "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
        return_value=MagicMock(),
    ), patch(
        "vaibify.cli.levelReport.ftLoadContainerLevelInputs",
        side_effect=LookupError("No vaibify project found"),
    ):
        dictSection = commandStatus.fdictBuildLevelSection(_fconfig())
    assert dictSection["bAvailable"] is False
    assert "No vaibify project" in dictSection["sReason"]


def test_level_section_reports_read_failure_reason():
    from vaibify.cli import commandStatus
    with patch(
        "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
        return_value=MagicMock(),
    ), patch(
        "vaibify.cli.levelReport.ftLoadContainerLevelInputs",
        side_effect=RuntimeError("exec boom"),
    ):
        dictSection = commandStatus.fdictBuildLevelSection(_fconfig())
    assert dictSection["bAvailable"] is False
    assert "could not read the container" in dictSection["sReason"]


def test_level_section_available_marks_the_report():
    from vaibify.cli import commandStatus
    with patch(
        "vaibify.cli.commandUtilsDocker.fconnectionRequireDocker",
        return_value=MagicMock(),
    ), patch(
        "vaibify.cli.levelReport.ftLoadContainerLevelInputs",
        return_value=({"listSteps": []}, MagicMock()),
    ), patch(
        "vaibify.cli.levelReport.fdictBuildLevelReport",
        return_value={"iProofLevel": 1, "sProofLevelName": "x"},
    ):
        dictSection = commandStatus.fdictBuildLevelSection(_fconfig())
    assert dictSection["bAvailable"] is True
    assert dictSection["iProofLevel"] == 1


def test_status_proof_flag_emits_level_or_reason(capsys):
    from vaibify.cli import commandStatus
    with patch("vaibify.cli.commandStatus.fbDockerAvailable",
               return_value=True), \
         patch("vaibify.cli.commandStatus.fdockerBuildClientOrNone",
               return_value=None), \
         patch("vaibify.cli.commandStatus.fconfigResolveProject",
               return_value=_fconfig()), \
         patch("vaibify.cli.commandStatus.fnShowDaemonStatus"), \
         patch("vaibify.cli.commandStatus.fnShowDockerContext"), \
         patch("vaibify.cli.commandStatus.fnShowImageStatus"), \
         patch("vaibify.cli.commandStatus.fnShowVolumeStatus"), \
         patch("vaibify.cli.commandStatus.fnShowContainerStatus"), \
         patch("vaibify.cli.commandStatus.fdictBuildLevelSection",
               return_value={"bAvailable": False,
                             "sReason": "no project"}):
        result = CliRunner().invoke(fnStatusCommand, ["--proof"])
    assert result.exit_code == 0, result.output
    assert "PROOF level: unavailable (no project)" in result.output


def test_status_json_flag_emits_one_object():
    import json as jsonlib
    from vaibify.cli import commandStatus
    with patch("vaibify.cli.commandStatus.fbDockerAvailable",
               return_value=True), \
         patch("vaibify.cli.commandStatus.fdockerBuildClientOrNone",
               return_value=None), \
         patch("vaibify.cli.commandStatus.fconfigResolveProject",
               return_value=_fconfig()), \
         patch("vaibify.cli.commandStatus.fdictBuildEnvironmentStatus",
               return_value={"sProjectName": "proj"}), \
         patch("vaibify.cli.commandStatus.fdictBuildLevelSection",
               return_value={"bAvailable": True, "iProofLevel": 2}):
        result = CliRunner().invoke(fnStatusCommand, ["--json"])
    assert result.exit_code == 0, result.output
    dictOut = jsonlib.loads(result.output)
    assert dictOut["dictEnvironment"]["sProjectName"] == "proj"
    assert dictOut["dictProof"]["iProofLevel"] == 2


@pytest.mark.falsification
def testTheCliAndTheDashboardAskOneAuthorityWhichImagesAreTheProjects():
    """Two callers, one answer, and no private copy on either side.

    This is the divergence bug that had already happened: the
    dashboard's Delete asked ``imageBuilder`` which references a
    project owns and removed them all, while the CLI formatted
    ``f"{name}:latest"`` for itself and removed one. Both were
    internally consistent; only one was right, and the difference was
    invisible until a researcher's disk filled.

    The oracle is the rule this repository states for exactly this
    case: when two things that must agree have drifted, the fix is one
    authority, not a second correction. So what is asserted is not
    "the CLI removes several tags" -- a private loop could satisfy
    that -- but that neither caller derives the set at all.

    Kills: reintroducing a locally-formatted ``:latest`` reference in
    the destroy command.
    """
    import pathlib
    for sPath in (
        "vaibify/cli/commandDestroy.py",
        "vaibify/gui/environmentDeletion.py",
    ):
        sSource = pathlib.Path(sPath).read_text(encoding="utf-8")
        # Prose may name the tags to explain the chain; CODE may not
        # build one.
        sCode = re.sub(r'"""[\s\S]*?"""', "", sSource)
        sCode = re.sub(r"^\s*#.*$", "", sCode, flags=re.M)
        assert "flistProjectImageReferences" in sSource, (
            f"{sPath} must ask imageBuilder which images are the "
            "project's rather than deriving them"
        )
        assert ":latest" not in sCode, (
            f"{sPath} formats an image tag of its own; a build leaves a "
            "chain, and a second derivation of that set is how the CLI "
            "and the dashboard came to disagree"
        )
