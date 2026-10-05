"""The image builder's argv, its image-store reads, and its output routing.

The ``docker`` binary is the boundary: every test here replaces the
module's ``subprocess`` (or its one ``_fnRunDockerBuild`` primitive)
with a recorder, and asserts on the exact commands vaibify would have
run and on what it concludes from the answers. The shipped
``containerImage`` directory is used as the real build context, so the
recipe fingerprint stamped on the chain is computed over real texts.
"""

import io
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.cli.configLoader import fsDockerDir
from vaibify.config.projectConfig import fconfigFromYamlDict
from vaibify.docker import imageBuilder
from vaibify.reproducibility.dockerfileComposer import (
    S_ENVIRONMENT_IMAGE_LABEL,
    S_OVERLAYS_IMAGE_LABEL,
    S_RECIPE_IMAGE_LABEL,
)


S_PROJECT_NAME = "builderProject"

# conftest answers "no such image" for every unit test; the removal
# test below needs the REAL probe driven by its own fake subprocess.
fbRealImageExists = imageBuilder.fbImageExists


class _FakeCompletedProcess:
    """What ``subprocess.run`` returns, with only the fields read."""

    def __init__(self, iReturnCode=0, sStdout="", sStderr=""):
        self.returncode = iReturnCode
        self.stdout = sStdout
        self.stderr = sStderr


def fnReplaceSubprocess(monkeypatch, fnRun, fnPopen=None):
    """Swap the module's subprocess for one whose run/Popen are scripted."""
    moduleFake = SimpleNamespace(
        run=fnRun,
        Popen=fnPopen or subprocess.Popen,
        PIPE=subprocess.PIPE,
        STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    monkeypatch.setattr(imageBuilder, "subprocess", moduleFake)


def flistRecordDockerBuilds(monkeypatch):
    """Capture every build primitive call instead of running docker."""
    listBuilds = []
    monkeypatch.setattr(
        imageBuilder, "_fnRunDockerBuild",
        lambda saCommand: listBuilds.append(list(saCommand)),
    )
    return listBuilds


def fsLabelValue(saCommand, sLabelKey):
    """Return the value of one ``--label key=value`` pair in an argv."""
    for iIndex, sArgument in enumerate(saCommand[:-1]):
        if sArgument == "--label" and saCommand[iIndex + 1].startswith(
            sLabelKey + "=",
        ):
            return saCommand[iIndex + 1].split("=", 1)[1]
    return None


def testAFullBuildChainsEveryOverlayOnThePreviousTag(monkeypatch, capsys):
    """Base, then each overlay FROM the one before, then :latest, then prune.

    buildx is absent here, so the legacy builder is used AND the
    researcher is told why Docker is about to print a deprecation.
    """
    listRunCalls = []

    sEnvironmentImageId = "sha256:" + "e" * 64

    def fnRun(saCommand, **kwargs):
        listRunCalls.append(list(saCommand))
        if saCommand[:3] == ["docker", "image", "inspect"]:
            return _FakeCompletedProcess(0, sEnvironmentImageId + "\n")
        raise FileNotFoundError("docker")

    fnReplaceSubprocess(monkeypatch, fnRun)
    listBuilds = flistRecordDockerBuilds(monkeypatch)
    configProject = fconfigFromYamlDict({
        "projectName": S_PROJECT_NAME,
        "features": {"claude": True, "gemini": True},
    })
    imageBuilder.fnBuildImage(configProject, fsDockerDir(), bNoCache=True)
    listTags = [saBuild[saBuild.index("-t") + 1] for saBuild in listBuilds[:-1]]
    assert listTags == [
        f"{S_PROJECT_NAME}:base", f"{S_PROJECT_NAME}:node",
        f"{S_PROJECT_NAME}:claude", f"{S_PROJECT_NAME}:gemini",
    ]
    for iIndex, saOverlay in enumerate(listBuilds[1:-1], start=1):
        assert saOverlay[:2] == ["docker", "build"]
        assert f"BASE_IMAGE={listTags[iIndex - 1]}" in saOverlay
        assert "--no-cache" in saOverlay
    listFingerprints = [
        fsLabelValue(saBuild, S_RECIPE_IMAGE_LABEL)
        for saBuild in listBuilds[:-1]
    ]
    assert len(set(listFingerprints)) == len(listFingerprints)
    assert None not in listFingerprints
    assert [
        fsLabelValue(saBuild, S_OVERLAYS_IMAGE_LABEL)
        for saBuild in listBuilds[:-1]
    ] == ["", "node", "node,claude", "node,claude,gemini"]
    assert [
        fsLabelValue(saBuild, S_ENVIRONMENT_IMAGE_LABEL)
        for saBuild in listBuilds[:-1]
    ] == ["", sEnvironmentImageId, sEnvironmentImageId, sEnvironmentImageId]
    assert listBuilds[-1] == [
        "docker", "tag", f"{S_PROJECT_NAME}:gemini", f"{S_PROJECT_NAME}:latest",
    ]
    assert ["docker", "image", "prune", "-f"] in listRunCalls
    assert "buildx is not installed" in capsys.readouterr().err


def testAStackOnAnObtainedBaseWithNoChainBuildsNothing(monkeypatch):
    """No overlay to stack means the base ID itself runs; docker is idle."""
    listBuilds = flistRecordDockerBuilds(monkeypatch)
    sBaseId = "sha256:" + "b" * 64
    assert imageBuilder.fsStackOverlaysOnObtainedBase(
        S_PROJECT_NAME, sBaseId, [], fsDockerDir(), "linux/amd64", [],
    ) == sBaseId
    assert listBuilds == []


def testAStackChainsItsStagesAndOmitsAnAbsentPlatform(monkeypatch):
    """Stage two builds FROM stage one; no --platform when none is pinned."""
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: True)
    listBuilds = flistRecordDockerBuilds(monkeypatch)
    sBaseId = "sha256:" + "c" * 64
    sLast = imageBuilder.fsStackOverlaysOnObtainedBase(
        S_PROJECT_NAME, sBaseId, ["node", "gemini"], fsDockerDir(), "",
        ["node", "claude", "gemini"], bNoCache=True,
    )
    assert sLast == f"{S_PROJECT_NAME}:gemini"
    assert f"BASE_IMAGE={sBaseId}" in listBuilds[0]
    assert f"BASE_IMAGE={S_PROJECT_NAME}:node" in listBuilds[1]
    for saBuild in listBuilds:
        assert "--platform" not in saBuild
        assert "--no-cache" in saBuild
        assert saBuild.count(fsDockerDir()) == 1
        assert fsLabelValue(saBuild, S_OVERLAYS_IMAGE_LABEL) == (
            "node,claude,gemini"
        )


def testASingleOverlayCarriesNoCacheBeforeItsLabels(monkeypatch):
    """--no-cache and the recipe label reach a single overlay's build."""
    monkeypatch.setattr(imageBuilder, "fbBuildxAvailable", lambda: True)
    listBuilds = flistRecordDockerBuilds(monkeypatch)
    sDockerDirectory = fsDockerDir()
    imageBuilder.fnApplyOverlay(
        S_PROJECT_NAME, "claude", sDockerDirectory, "base", bNoCache=True,
        sRecipeFingerprint="abc123", listOverlays=["claude"],
    )
    saBuild = listBuilds[0]
    assert "--no-cache" in saBuild
    assert fsLabelValue(saBuild, S_RECIPE_IMAGE_LABEL) == "abc123"
    assert saBuild[saBuild.index("-f") + 1].endswith("Dockerfile.claude")
    assert f"BASE_IMAGE={S_PROJECT_NAME}:base" in saBuild


def testAnOverlayWithNoFeatureFieldAnswersEmpty():
    """Prerequisites like node are enabled by agents, never by a field."""
    assert imageBuilder.fsFeatureFieldForOverlay("node") == ""
    assert imageBuilder.fsFeatureFieldForOverlay("claude") == "bClaude"


def testProjectImageReferencesListEveryTagButUntaggedLayers(monkeypatch):
    """A deletion must see :base and each overlay, never '<none>' entries."""
    listRunCalls = []

    def fnRun(saCommand, **kwargs):
        listRunCalls.append(list(saCommand))
        return _FakeCompletedProcess(0, (
            f"{S_PROJECT_NAME}:latest\n{S_PROJECT_NAME}:claude\n"
            f"{S_PROJECT_NAME}:<none>\n{S_PROJECT_NAME}:base\n"
        ))

    fnReplaceSubprocess(monkeypatch, fnRun)
    assert imageBuilder.flistProjectImageReferences(S_PROJECT_NAME) == [
        f"{S_PROJECT_NAME}:latest", f"{S_PROJECT_NAME}:claude",
        f"{S_PROJECT_NAME}:base",
    ]
    assert listRunCalls[0][-1] == S_PROJECT_NAME


def testAFailedImageListingRaisesRatherThanReportingNone(monkeypatch):
    """An unanswered listing must not read as 'nothing to delete'."""
    fnReplaceSubprocess(monkeypatch, lambda saCommand, **kwargs: (
        _FakeCompletedProcess(1, "", "  Cannot connect to the daemon \n")
    ))
    with pytest.raises(RuntimeError) as errorRaised:
        imageBuilder.flistProjectImageReferences(S_PROJECT_NAME)
    assert str(errorRaised.value) == (
        "docker images failed: Cannot connect to the daemon"
    )


@pytest.mark.parametrize("iRemoveCode,iInspectCode,bExpectedGone", [
    (0, 0, True),
    (1, 1, True),
    (1, 0, False),
])
def testRemovingAnImageReportsWhetherItIsGoneAfterwards(
    monkeypatch, iRemoveCode, iInspectCode, bExpectedGone,
):
    """A failed rmi is still success when the image is already absent."""
    listRunCalls = []

    def fnRun(saCommand, **kwargs):
        listRunCalls.append(list(saCommand))
        if saCommand[1] == "rmi":
            return _FakeCompletedProcess(iRemoveCode)
        return _FakeCompletedProcess(iInspectCode)

    fnReplaceSubprocess(monkeypatch, fnRun)
    monkeypatch.setattr(imageBuilder, "fbImageExists", fbRealImageExists)
    sReference = f"{S_PROJECT_NAME}:claude"
    assert imageBuilder.fbRemoveImage(sReference) is bExpectedGone
    assert listRunCalls[0] == ["docker", "rmi", "-f", sReference]
    if iRemoveCode != 0:
        assert listRunCalls[1] == ["docker", "image", "inspect", sReference]


def testAnEmittedBuildLineReachesTheSinkRedacted(capsys):
    """The dashboard pane gets vaibify's own lines, with credentials stripped."""
    listSunk = []
    imageBuilder.fnSetThreadBuildLineSink(listSunk.append)
    try:
        imageBuilder.fnEmitBuildLine(
            "[vaib] cloning https://someone:secretValue@example.test/r.git\n",
        )
    finally:
        imageBuilder.fnSetThreadBuildLineSink(None)
    assert listSunk == [
        "[vaib] cloning https://REDACTED@example.test/r.git\n",
    ]
    assert "[vaib] cloning" in capsys.readouterr().err


def testABuildWhoseStderrIsUnavailableStillFailsWithItsExitCode(monkeypatch):
    """No stderr stream means no tail, never a swallowed failure."""

    class _FakeBuildProcess:
        stderr = None

        def wait(self):
            return 17

    fnReplaceSubprocess(
        monkeypatch, lambda *aArgs, **kwargs: None,
        fnPopen=lambda *aArgs, **kwargs: _FakeBuildProcess(),
    )
    with pytest.raises(RuntimeError) as errorRaised:
        imageBuilder._fnRunDockerBuildCapturing(["docker", "build", "."])
    assert "exit 17" in str(errorRaised.value)
    assert errorRaised.value.sStderrTail == ""


def testAStreamedStderrIsTeedAndRedactedInTheTail(monkeypatch, capsys):
    """The captured tail never carries a token a build step printed."""

    class _FakeBuildProcess:
        stderr = io.StringIO(
            "step 1\nfetch https://bot:ghp_secretToken@example.test/x\n"
        )

        def wait(self):
            return 2

    fnReplaceSubprocess(
        monkeypatch, lambda *aArgs, **kwargs: None,
        fnPopen=lambda *aArgs, **kwargs: _FakeBuildProcess(),
    )
    with pytest.raises(RuntimeError) as errorRaised:
        imageBuilder._fnRunDockerBuildCapturing(["docker", "build", "."])
    assert "ghp_secretToken" not in errorRaised.value.sStderrTail
    assert "https://REDACTED@example.test/x" in errorRaised.value.sStderrTail
    assert "step 1" in capsys.readouterr().err
