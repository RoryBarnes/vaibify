"""A container running the author's PINNED image is never told to rebuild.

The configuration-drift banner compares the fingerprint stamped on a
container's image with one computed from the project's configuration
as it is now, and its remedy is a rebuild. For an image OBTAINED from
the author's pin that remedy is the one thing a reproduction must not
do: a rebuild replaces the author's exact bytes with a fresh build. And
the comparison itself is skewed for such an image, because the stamp
hashes the AUTHOR's configuration while the reader's file carries the
agents vaibify stacked on the image and the auto-update switches the
conversion wrote -- vaibify's own edits, never a difference in the
environment (a reader met the rebuild banner on first open, having
changed nothing the image is built from).

Driven through the readiness assembly with real ``git`` holding the
author's committed file, and a container id that differs from its name.
"""

import subprocess
from unittest.mock import patch

import pytest
import yaml

from vaibify.cli.configLoader import fconfigLoadFromPath
from vaibify.config.configurationFingerprint import (
    fsComputeConfigurationFingerprint,
)
from vaibify.config.projectConfig import FeaturesConfig
from vaibify.docker.imageBuilder import T_AGENT_OVERLAY_NAMES
from vaibify.gui.routes import systemRoutes

S_CONTAINER_ID = "c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00"
S_CONTAINER_NAME = "pinnedProbe"
S_REBUILD_REMEDY = "choose Rebuild"

DICT_AUTHOR_CONFIGURATION = {
    "projectName": S_CONTAINER_NAME,
    "pythonVersion": "3.12",
    "pythonPackages": ["numpy"],
    "memoryLimitGigabytes": 1.0,
    "features": {"claude": False, "claudeAutoUpdate": True, "codex": False},
}


def _fnWriteYaml(pathFile, dictContent):
    pathFile.write_text(yaml.safe_dump(dictContent), encoding="utf-8")


def _fnRunGit(pathRepository, *saArguments):
    subprocess.run(
        ["git", "-C", str(pathRepository), "-c", "user.name=Author",
         "-c", "user.email=author@example.org", *saArguments],
        check=True, capture_output=True,
    )


def _fdictBuildProject(tmp_path, bCommitAuthorFile=True):
    """Return a registry entry for a clone holding the author's file.

    The working copy is what the conversion leaves: the author's
    image-defining fields untouched, a runtime field changed, the
    agents vaibify stacked switched on and an auto-update flipped.
    """
    pathProject = tmp_path / "clone"
    pathProject.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(pathProject)], check=True,
        capture_output=True,
    )
    pathConfig = pathProject / "vaibify.yml"
    _fnWriteYaml(pathConfig, DICT_AUTHOR_CONFIGURATION)
    if bCommitAuthorFile:
        _fnRunGit(pathProject, "add", "vaibify.yml")
        _fnRunGit(pathProject, "commit", "-q", "-m", "Author's environment")
    dictReader = dict(DICT_AUTHOR_CONFIGURATION)
    dictReader["memoryLimitGigabytes"] = 5.0
    dictReader["features"] = {
        "claude": True, "claudeAutoUpdate": False, "codex": True,
    }
    _fnWriteYaml(pathConfig, dictReader)
    return {
        "sName": S_CONTAINER_NAME, "sDirectory": str(pathProject),
        "sConfigPath": str(pathConfig), "sMode": "container",
        "dictImageSource": {"sSource": "archive"},
    }


def _fsStampForAuthor(tmp_path, listExtraPythonPackages=()):
    """Return the fingerprint the author's build stamped on the image."""
    dictAuthor = dict(DICT_AUTHOR_CONFIGURATION)
    dictAuthor["pythonPackages"] = (
        list(dictAuthor["pythonPackages"]) + list(listExtraPythonPackages)
    )
    pathAuthor = tmp_path / "authorAsBuilt.yml"
    _fnWriteYaml(pathAuthor, dictAuthor)
    return fsComputeConfigurationFingerprint(fconfigLoadFromPath(str(pathAuthor)))


def _fdictReadinessFor(dictProject, sStamped):
    """Return the settled readiness payload for the project's container."""
    def fsNameForId(_connectionDocker, sContainerId):
        return S_CONTAINER_NAME if sContainerId == S_CONTAINER_ID else ""

    def fdictProjectForName(sName):
        return dictProject if sName == S_CONTAINER_NAME else None

    with patch.object(
        systemRoutes, "_fdictProbeContainerReadiness",
        return_value={"bReady": True, "sStatus": "ok", "sReason": "",
                      "saWarnings": [], "iWarningCount": 0},
    ), patch.object(
        systemRoutes, "_flistDescribeUnresolvableSecrets", return_value=[],
    ), patch.object(
        systemRoutes, "_ftDescribeX11Findings", return_value=([], []),
    ), patch.object(
        systemRoutes, "_flistDescribeResourceLimitDrift", return_value=[],
    ), patch.object(
        systemRoutes, "_flistDescribeResourceAdvisories", return_value=[],
    ), patch(
        "vaibify.gui.pipelineServer.fsContainerNameForId", fsNameForId,
    ), patch(
        "vaibify.config.registryManager.fdictGetProject", fdictProjectForName,
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fsReadContainerConfigurationLabel",
        lambda sContainerId: sStamped if sContainerId == S_CONTAINER_ID else "",
    ):
        return systemRoutes._fdictReadinessWithSecretWarnings(
            None, S_CONTAINER_ID,
        )


@pytest.mark.falsification
def testAPinnedImageIsNeverToldToRebuild(tmp_path):
    """A real difference on a pinned image is reported without a rebuild.

    The oracle is the reproducibility lane's own ruling: an obtained
    image is the author's environment, and the only path back to a
    built image is the explicit switch, which leaves that environment.

    Kills: routing an obtained image through the built-image sentences,
    which tell the reader to replace the author's environment.
    """
    dictProject = _fdictBuildProject(tmp_path)
    dictReadiness = _fdictReadinessFor(
        dictProject, _fsStampForAuthor(tmp_path, ["scipy"]),
    )
    assert dictReadiness["listConfigurationDrift"] == []
    listPinnedLines = dictReadiness["listPinnedEnvironmentDrift"]
    assert listPinnedLines, "a real difference must still be reported"
    sAllText = " ".join(listPinnedLines)
    assert S_REBUILD_REMEDY not in sAllText
    assert "pinned" in sAllText
    assert "vaibify.yml" not in sAllText, (
        "the sentences must not assume the reader knows the file"
    )


@pytest.mark.falsification
def testTheAgentsVaibifyStackedAreNotADifference(tmp_path):
    """The reader's agent switches never make a pinned image read as drifted.

    The stamp is computed independently, from the author's file as
    built; the working copy differs from it only in what the conversion
    writes -- agents, auto-update switches and a runtime limit.

    Kills: comparing the reader's own agent switches with the stamp.
    """
    dictProject = _fdictBuildProject(tmp_path)
    dictReadiness = _fdictReadinessFor(dictProject, _fsStampForAuthor(tmp_path))
    assert dictReadiness["listConfigurationDrift"] == []
    assert dictReadiness["listPinnedEnvironmentDrift"] == []


@pytest.mark.falsification
def testWithoutTheAuthorsCommittedFileNothingIsDetermined(tmp_path):
    """No author's file means the agents cannot be set aside: say nothing.

    Kills: falling back to the reader's own agent switches when the
    author's committed file cannot be read, which reports vaibify's own
    edits as a difference in the environment.
    """
    dictProject = _fdictBuildProject(tmp_path, bCommitAuthorFile=False)
    dictReadiness = _fdictReadinessFor(dictProject, _fsStampForAuthor(tmp_path))
    assert dictReadiness["listConfigurationDrift"] == []
    assert dictReadiness["listPinnedEnvironmentDrift"] == []


@pytest.mark.falsification
def testABuiltImageStillNamesTheRebuild(tmp_path):
    """A project that BUILDS its image keeps the rebuild remedy.

    Kills: silencing the drift banner for every container rather than
    for the obtained ones.
    """
    dictProject = _fdictBuildProject(tmp_path)
    dictProject.pop("dictImageSource")
    dictReadiness = _fdictReadinessFor(
        dictProject, _fsStampForAuthor(tmp_path, ["scipy"]),
    )
    assert S_REBUILD_REMEDY in " ".join(dictReadiness["listConfigurationDrift"])
    assert dictReadiness["listPinnedEnvironmentDrift"] == []


def testEveryAgentSwitchIsAFeatureField():
    """Each agent overlay contributes its install and auto-update switch.

    A name that is not a field would be CREATED on the copy by the
    restoring assignment and change the hash for every pinned image.
    """
    from vaibify.config.configurationFingerprint import (
        flistAgentFeatureFields,
    )
    setFeatureFields = set(vars(FeaturesConfig()))
    listAgentFields = flistAgentFeatureFields()
    assert set(listAgentFields) <= setFeatureFields
    assert len(listAgentFields) == 2 * len(T_AGENT_OVERLAY_NAMES)
    assert "bLatex" not in listAgentFields
