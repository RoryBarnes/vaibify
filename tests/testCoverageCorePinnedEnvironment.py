"""Refusals and read paths of vaibify.gui.pinnedEnvironmentConversion.

Clones are real directories in tmp_path, committed with real git where
the code reads HEAD, and carry a real envelope from the shared
reproduction-source fixtures. No Docker daemon is asked anything: the
wizard description is handed ``None`` for its connection, which is the
documented "no daemon" value.
"""

import os
import shutil
import subprocess

import pytest
import yaml
from fastapi import HTTPException

from tests.reproductionSourceFixtures import fdictBuildEnvelope, fnWriteJson
from vaibify.gui import pinnedEnvironmentConversion
from vaibify.gui.registryRoutes import ConvertToContainerRequest


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git executable not installed",
)

S_AUTHOR_YAML = (
    "projectName: authorProject\npythonVersion: '3.11'\n"
    "baseImage: debian:12\ncpuLimit: 4\ndashboardPort: 8123\n"
    "features:\n  claude: true\n  latex: true\n"
)


def fnCommitFile(sDirectory, sRelativePath, sText):
    """Write sText at sRelativePath and commit it with a fixed identity."""
    with open(os.path.join(sDirectory, sRelativePath), "w") as fileHandle:
        fileHandle.write(sText)
    listIdentity = ["-c", "user.email=a@example.com", "-c", "user.name=a",
                    "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "add", sRelativePath], cwd=sDirectory, check=True)
    subprocess.run(
        ["git", *listIdentity, "commit", "-q", "-m", "author"],
        cwd=sDirectory, check=True,
    )


def fdictBuildClone(tmp_path, sCommittedYaml, sWorkingYaml, bEnvelope=True):
    """Return a project dict for a git clone with the given config copies."""
    sDirectory = str(tmp_path / "cloneAlpha")
    os.makedirs(sDirectory)
    subprocess.run(["git", "init", "-q"], cwd=sDirectory, check=True)
    sConfigPath = os.path.join(sDirectory, "vaibify.yml")
    if sCommittedYaml is not None:
        fnCommitFile(sDirectory, "vaibify.yml", sCommittedYaml)
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(sWorkingYaml)
    if bEnvelope:
        fnWriteJson(sDirectory, ".vaibify/environment.json", fdictBuildEnvelope())
    return {
        "sName": "projectAlpha", "sMode": "container",
        "sDirectory": sDirectory, "sConfigPath": sConfigPath,
    }


def fdictReadYaml(sPath):
    """Return the parsed YAML mapping at sPath."""
    with open(sPath) as fileHandle:
        return yaml.safe_load(fileHandle)


def testRewriteForObtainedImageKeepsAuthorBaseAndAppliesRuntime(tmp_path):
    """Base keys survive, runtime keys come from the request, unset ones go."""
    dictProject = fdictBuildClone(tmp_path, None, S_AUTHOR_YAML)
    request = ConvertToContainerRequest(
        sProjectName="projectAlpha", bNeverSleep=True,
        bClaudeAutoUpdate=False, sBaseImage="ubuntu:24.04",
    )
    pinnedEnvironmentConversion.fnRewriteConfigForObtainedImage(
        dictProject["sConfigPath"], request,
    )
    dictWritten = fdictReadYaml(dictProject["sConfigPath"])
    assert dictWritten["projectName"] == "projectAlpha"
    assert dictWritten["baseImage"] == "debian:12"
    assert dictWritten["pythonVersion"] == "3.11"
    assert dictWritten["neverSleep"] is True
    assert "cpuLimit" not in dictWritten or not dictWritten["cpuLimit"]
    assert dictWritten["features"]["claude"] is True
    assert dictWritten["features"]["claudeAutoUpdate"] is False


def testRewriteForObtainedImageRefusesAnInvalidResult(tmp_path):
    """An author file the validator rejects is a 400, and is not rewritten."""
    sInvalid = "projectName: x\npackageManager: notAManager\n"
    dictProject = fdictBuildClone(tmp_path, None, sInvalid)
    request = ConvertToContainerRequest(sProjectName="projectAlpha")
    with pytest.raises(HTTPException) as excinfo:
        pinnedEnvironmentConversion.fnRewriteConfigForObtainedImage(
            dictProject["sConfigPath"], request,
        )
    assert excinfo.value.status_code == 400
    assert "invalid after the runtime settings" in excinfo.value.detail
    with open(dictProject["sConfigPath"]) as fileHandle:
        assert fileHandle.read() == sInvalid


def testWizardDescriptionCombinesPinDaemonAndAuthorFeatures(tmp_path):
    """The page gets the pin, an unreachable daemon, and the author's features."""
    dictProject = fdictBuildClone(tmp_path, None, S_AUTHOR_YAML)
    dictDescription = (
        pinnedEnvironmentConversion.fdictDescribePinnedEnvironmentForWizard(
            dictProject, None,
        )
    )
    assert dictDescription["bObtainable"] is True
    assert dictDescription["sPinnedImageReference"]
    assert dictDescription["dictDaemon"]["bReachable"] is False
    assert dictDescription["dictDaemon"]["bArchitectureMatches"] is False
    assert dictDescription["dictAuthorFeatures"] == {
        "claude": True, "latex": True,
    }
    assert "claude" in dictDescription["listAgentOverlays"]
    assert dictDescription["listBaseFeatureKeys"] == list(
        pinnedEnvironmentConversion.T_BASE_FEATURE_KEYS,
    )


@pytest.mark.parametrize(
    "sWorkingYaml", ["features: [claude]\n", "features: {unclosed\n"],
)
def testWizardAuthorFeaturesAreEmptyForMalformedConfig(tmp_path, sWorkingYaml):
    """A non-mapping or unparseable features block reads as no features."""
    dictProject = fdictBuildClone(tmp_path, None, sWorkingYaml)
    dictDescription = (
        pinnedEnvironmentConversion.fdictDescribePinnedEnvironmentForWizard(
            dictProject, None,
        )
    )
    assert dictDescription["dictAuthorFeatures"] == {}


def testArchiveSourceIsRefusedWhenTheEnvelopePinsNothing(tmp_path):
    """No envelope means nothing obtainable: a 409 naming the refusal."""
    dictProject = fdictBuildClone(tmp_path, None, S_AUTHOR_YAML, bEnvelope=False)
    request = ConvertToContainerRequest(sProjectName="projectAlpha")
    with pytest.raises(HTTPException) as excinfo:
        pinnedEnvironmentConversion.fdictBuildArchiveImageSource(
            dictProject, request,
        )
    assert excinfo.value.status_code == 409
    assert "does not pin an image" in excinfo.value.detail["sMessage"]


def testArchiveSourceTakesBaselineFromDockerfileHeader(tmp_path):
    """The committed Dockerfile's header names the candidate and fingerprint."""
    dictProject = fdictBuildClone(tmp_path, None, S_AUTHOR_YAML)
    with open(os.path.join(dictProject["sDirectory"], "Dockerfile"), "w") as fileHandle:
        fileHandle.write(
            "# vaibify:recipe-sha256=abc123\n"
            "#   base + overlays in order: claude, gemini\nFROM scratch\n",
        )
    request = ConvertToContainerRequest(
        sProjectName="projectAlpha", listFeatures=["gemini", "codex", "latex"],
        bAllowEmulation=True,
    )
    dictSource = pinnedEnvironmentConversion.fdictBuildArchiveImageSource(
        dictProject, request,
    )
    assert dictSource["listAuthorOverlays"] == ["claude", "gemini"]
    assert dictSource["sAuthorRecipeFingerprint"] == "abc123"
    assert dictSource["listAdditionalAgents"] == ["codex"]
    assert dictSource["bAllowEmulation"] is True
    assert dictSource["listResolvedOverlays"] is None


def testArchiveSourceWithUnloadableConfigIsRefusedByName(tmp_path):
    """Without a header, an unloadable vaibify.yml is a 409, not a guess."""
    dictProject = fdictBuildClone(
        tmp_path, None, "projectName: x\npackageManager: notAManager\n",
    )
    request = ConvertToContainerRequest(sProjectName="projectAlpha")
    with pytest.raises(HTTPException) as excinfo:
        pinnedEnvironmentConversion.fdictBuildArchiveImageSource(
            dictProject, request,
        )
    assert excinfo.value.status_code == 409
    assert "cannot be loaded" in excinfo.value.detail["sMessage"]


def testImageOriginOfBuiltProjectWithoutDirectoryIsNotObtainable():
    """A built container entry with no directory cannot name the switch."""
    dictOrigin = pinnedEnvironmentConversion.fdictDescribeImageOriginForProject(
        {"sMode": "container", "sDirectory": ""},
    )
    assert dictOrigin == {"bImageWasBuilt": True, "bPinnedImageObtainable": False}
    assert not pinnedEnvironmentConversion.fbSwitchToPinnedImageIsTheRemedy(
        dictOrigin,
    )


def testImageOriginTreatsUnreadableCloneAsNotObtainable(tmp_path, monkeypatch):
    """An OSError reading the clone never becomes an obtainable claim."""
    from vaibify.reproducibility import reproductionSource

    def fdictRaiseOsError(sRepoPath):
        raise OSError("permission denied")

    monkeypatch.setattr(
        reproductionSource, "fdictDescribePinnedEnvironment", fdictRaiseOsError,
    )
    dictOrigin = pinnedEnvironmentConversion.fdictDescribeImageOriginForProject(
        {"sMode": "container", "sDirectory": str(tmp_path)},
    )
    assert dictOrigin == {"bImageWasBuilt": True, "bPinnedImageObtainable": False}


@pytest.mark.parametrize(
    "sWorkingYaml", ["features: [claude]\n", "features: {unclosed\n"],
)
def testEnabledAgentsOfMalformedConfigIsEmpty(tmp_path, sWorkingYaml):
    """Neither a list of features nor broken YAML enables any agent."""
    pathConfig = tmp_path / "vaibify.yml"
    pathConfig.write_text(sWorkingYaml)
    assert pinnedEnvironmentConversion._flistEnabledAgentsInConfig(
        str(pathConfig),
    ) == []
    assert pinnedEnvironmentConversion._flistEnabledAgentsInConfig(
        str(tmp_path / "absent.yml"),
    ) == []


def testRestoreRefusesConfigOutsideTheClone(tmp_path):
    """A config path outside the clone has no committed author copy."""
    dictProject = fdictBuildClone(tmp_path, S_AUTHOR_YAML, S_AUTHOR_YAML)
    pathOutside = tmp_path / "elsewhere.yml"
    pathOutside.write_text(S_AUTHOR_YAML)
    dictProject["sConfigPath"] = str(pathOutside)
    with pytest.raises(HTTPException) as excinfo:
        pinnedEnvironmentConversion.fnRestoreAuthorBaseFieldsFromGit(dictProject)
    assert excinfo.value.status_code == 409
    assert "not committed" in excinfo.value.detail["sMessage"]


@pytest.mark.parametrize(
    "sCommittedYaml,sExpectedFragment",
    [
        ("features: {unclosed\n", "could not be read"),
        ("- a\n- b\n", "not a mapping"),
        ("projectName: x\npackageManager: notAManager\n", "invalid once"),
    ],
)
def testRestoreRefusesUnusableCommittedConfig(
    tmp_path, sCommittedYaml, sExpectedFragment,
):
    """Each unusable author copy is refused and the working copy is kept."""
    dictProject = fdictBuildClone(tmp_path, sCommittedYaml, S_AUTHOR_YAML)
    with pytest.raises(HTTPException) as excinfo:
        pinnedEnvironmentConversion.fnRestoreAuthorBaseFieldsFromGit(dictProject)
    assert excinfo.value.status_code == 409
    assert sExpectedFragment in excinfo.value.detail["sMessage"]
    with open(dictProject["sConfigPath"]) as fileHandle:
        assert fileHandle.read() == S_AUTHOR_YAML
