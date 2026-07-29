"""Tests for untested functions in vaibify.config.projectConfig."""

import os
import tempfile

import pytest

from vaibify.config.projectConfig import (
    fdictLoadDefaults,
    fbValidateConfig,
    fconfigLoadFromFile,
    fnSaveToFile,
    ProjectConfig,
    FeaturesConfig,
)


def test_fdictLoadDefaults_has_keys():
    dictDefaults = fdictLoadDefaults()
    assert "projectName" in dictDefaults
    assert "features" in dictDefaults
    assert "pythonVersion" in dictDefaults


def test_fdictLoadDefaults_package_manager():
    dictDefaults = fdictLoadDefaults()
    assert dictDefaults["packageManager"] == "pip"


def test_fbValidateConfig_valid():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "testproj"
    assert fbValidateConfig(dictConfig) is True


def test_fbValidateConfig_rejects_conda_packages():
    """A hand-edited vaibify.yml must not smuggle the field back in.

    The create route refuses it, but the route is not the only way a
    config reaches the build; editing the file directly bypasses it.
    Nothing installs these packages, so accepting the file would put
    the container in a state the config does not describe.
    """
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "testproj"
    dictConfig["condaPackages"] = ["scipy"]
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_missing_name():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = ""
    assert fbValidateConfig(dictConfig) is False


def _fdictConfigWithRepos(listRepos, listMounts=None):
    """Return a minimal valid config carrying the given repos/mounts."""
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "testproj"
    dictConfig["repositories"] = listRepos
    if listMounts is not None:
        dictConfig["bindMounts"] = listMounts
    return dictConfig


@pytest.mark.falsification
def test_repo_destination_traversal_is_rejected():
    """A '../' destination becomes rm -rf outside the workspace.

    The entrypoint runs rm -rf "${WORKSPACE}/${destination}" before
    relocating a clone, so an unvalidated '../' destination deletes
    host data outside the workspace root. The host validator must
    reject it before it reaches container.conf.

    Kills: In projectConfig._fbValidateRepositoryDestinations, drop the
    ``".." in sDestination.split("/")`` guard from the isabs/.. check.
    """
    dictConfig = _fdictConfigWithRepos([
        {"name": "r", "url": "https://x/r.git", "destination": "../escape"},
    ])
    assert fbValidateConfig(dictConfig) is False


def test_repo_absolute_destination_is_rejected():
    dictConfig = _fdictConfigWithRepos([
        {"name": "r", "url": "https://x/r.git", "destination": "/etc"},
    ])
    assert fbValidateConfig(dictConfig) is False


@pytest.mark.falsification
def test_repo_destination_colliding_with_bind_mount_is_rejected():
    """A destination that lands on a bind mount would rm -rf host data.

    Bind mount /workspace/data and repo destination 'data' both resolve
    to ${WORKSPACE}/data, whose rm -rf deletes the mounted host
    directory's contents through the mount.

    Kills: In projectConfig._fbValidateRepositoryDestinations, replace
    the collision check with a constant ``if any([]):`` so no bind
    target is ever consulted.
    """
    dictConfig = _fdictConfigWithRepos(
        [{"name": "r", "url": "https://x/r.git", "destination": "data"}],
        [{"host": "~/Documents", "container": "/workspace/data"}],
    )
    assert fbValidateConfig(dictConfig) is False


@pytest.mark.falsification
def test_repo_destination_under_a_workspace_root_mount_is_rejected():
    """A mount at the workspace ROOT is an ancestor of every destination.

    The earlier collector expressed bind targets workspace-relative and
    dropped the root mount (empty relative string), so /workspace mounted
    + destination 'data' validated True and rm -rf /workspace/data hit
    the mount. The absolute-path overlap check must catch it — the
    destination is a DESCENDANT of the mount, so the descendant
    direction of the overlap check is what catches this case (the plain
    collision test exercises the equal case instead).

    Kills: In projectConfig._fbContainerPathsOverlap, drop the
    ``sFirst.startswith(sSecond + "/")`` (descendant) direction, so a
    destination nested under a mount is no longer detected.
    """
    dictConfig = _fdictConfigWithRepos(
        [{"name": "r", "url": "https://x/r.git", "destination": "data"}],
        [{"host": "~/Documents", "container": "/workspace"}],
    )
    assert fbValidateConfig(dictConfig) is False


def test_repo_destination_under_ancestor_of_custom_workspace_is_rejected():
    """A mount that is an ancestor of a customized workspace still collides."""
    dictConfig = _fdictConfigWithRepos(
        [{"name": "r", "url": "https://x/r.git", "destination": "out"}],
        [{"host": "~/Documents", "container": "/data"}],
    )
    dictConfig["workspaceRoot"] = "/data/workspace"
    # rm -rf /data/workspace/out lives under the /data mount.
    assert fbValidateConfig(dictConfig) is False


def test_repo_destination_nested_under_bind_mount_is_rejected():
    """A destination inside a mounted directory still deletes into it."""
    dictConfig = _fdictConfigWithRepos(
        [{"name": "r", "url": "https://x/r.git",
          "destination": "data/repo"}],
        [{"host": "~/Documents", "container": "/workspace/data"}],
    )
    assert fbValidateConfig(dictConfig) is False


def test_repo_destination_beside_bind_mount_is_allowed():
    """A sibling destination that does not touch the mount is fine."""
    dictConfig = _fdictConfigWithRepos(
        [{"name": "r", "url": "https://x/r.git", "destination": "code"}],
        [{"host": "~/Documents", "container": "/workspace/data"}],
    )
    assert fbValidateConfig(dictConfig) is True


def test_repo_plain_relative_destination_is_allowed():
    dictConfig = _fdictConfigWithRepos([
        {"name": "r", "url": "https://x/r.git", "destination": "libs/r"},
    ])
    assert fbValidateConfig(dictConfig) is True


def test_repo_without_destination_is_allowed():
    dictConfig = _fdictConfigWithRepos([
        {"name": "r", "url": "https://x/r.git"},
    ])
    assert fbValidateConfig(dictConfig) is True


def test_fbValidateConfig_rejects_metacharacter_names():
    dictConfig = fdictLoadDefaults()
    for sBadName in [
        "proj;rm -rf /",
        "../escape",
        "name with spaces",
        "name$(whoami)",
        "-leadingdash",
        ".leadingdot",
        "_leadingunder",
        "x" * 64,
    ]:
        dictConfig["projectName"] = sBadName
        assert fbValidateConfig(dictConfig) is False, sBadName


def test_fbValidateConfig_accepts_well_formed_names():
    dictConfig = fdictLoadDefaults()
    for sGoodName in [
        "myproj",
        "MyProj",
        "proj-1",
        "proj_1",
        "proj.1",
        "Project123",
        "x",
        "x" * 63,
    ]:
        dictConfig["projectName"] = sGoodName
        assert fbValidateConfig(dictConfig) is True, sGoodName


def test_fbValidateConfig_bad_manager():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["packageManager"] = "yarn"
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_not_dict():
    assert fbValidateConfig("not a dict") is False


def test_fbValidateConfig_bad_list():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["repositories"] = "not a list"
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_bad_features():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["features"] = {"jupyter": "yes"}
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_resource_limits():
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["cpuLimit"] = 1
    dictConfig["memoryLimitGigabytes"] = 1.0
    assert fbValidateConfig(dictConfig) is True
    dictConfig["cpuLimit"] = -1
    assert fbValidateConfig(dictConfig) is False
    dictConfig["cpuLimit"] = 1
    dictConfig["memoryLimitGigabytes"] = 0.1
    assert fbValidateConfig(dictConfig) is False
    dictConfig["memoryLimitGigabytes"] = 0
    assert fbValidateConfig(dictConfig) is True


def test_fbValidateConfig_resource_limit_types():
    """Non-numeric and bool-typed limits must be rejected, not
    coerced: bool is an int subclass, so the isinstance guards are
    load-bearing on both fields."""
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["cpuLimit"] = "2"
    assert fbValidateConfig(dictConfig) is False
    dictConfig["cpuLimit"] = True
    assert fbValidateConfig(dictConfig) is False
    dictConfig["cpuLimit"] = 0
    dictConfig["memoryLimitGigabytes"] = True
    assert fbValidateConfig(dictConfig) is False
    dictConfig["memoryLimitGigabytes"] = "1"
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_resource_limit_boundaries():
    """0.25 GB is the smallest allowed memory cap; negatives are
    invalid on both sides of the == 0 escape."""
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    dictConfig["memoryLimitGigabytes"] = 0.25
    assert fbValidateConfig(dictConfig) is True
    dictConfig["memoryLimitGigabytes"] = -1.0
    assert fbValidateConfig(dictConfig) is False


def test_fbValidateConfig_absent_resource_keys_are_valid():
    """A vaibify.yml predating the feature has neither key; the
    validator's fallback defaults must read as unlimited."""
    dictConfig = fdictLoadDefaults()
    dictConfig["projectName"] = "test"
    del dictConfig["cpuLimit"]
    del dictConfig["memoryLimitGigabytes"]
    assert fbValidateConfig(dictConfig) is True


def test_project_config_defaults_are_unlimited():
    configDefault = ProjectConfig()
    assert configDefault.iCpuLimit == 0
    assert configDefault.fMemoryLimitGigabytes == 0.0


def test_fnSaveToFile_roundtrip_resource_limits():
    config = ProjectConfig(
        sProjectName="limited",
        iCpuLimit=1,
        fMemoryLimitGigabytes=1.5,
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.iCpuLimit == 1
    assert configLoaded.fMemoryLimitGigabytes == 1.5


def test_fnSaveToFile_roundtrip():
    config = ProjectConfig(sProjectName="roundtrip")
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        assert os.path.isfile(sPath)
        configLoaded = fconfigLoadFromFile(sPath)
        assert configLoaded.sProjectName == "roundtrip"


def test_fnSaveToFile_roundtrip_full():
    listRepositories = [{
        "name": "foo",
        "url": "https://github.com/example/foo.git",
        "branch": "main",
        "installMethod": "pip_editable",
    }]
    config = ProjectConfig(
        sProjectName="fullproj",
        listRepositories=listRepositories,
        bNeverSleep=True,
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.listRepositories == listRepositories
    assert configLoaded.bNeverSleep is True


def test_fconfigLoadFromFile_missing():
    with pytest.raises(FileNotFoundError):
        fconfigLoadFromFile("/nonexistent/vaibify.yml")


def test_fconfigLoadFromFile_features():
    config = ProjectConfig(
        sProjectName="feat",
        features=FeaturesConfig(bJupyter=True),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
        assert configLoaded.features.bJupyter is True
        assert configLoaded.features.bGpu is False


def test_claude_auto_update_default_true():
    config = ProjectConfig(sProjectName="claudedefault")
    assert config.features.bClaudeAutoUpdate is True


def test_claude_auto_update_yaml_roundtrip_true():
    config = ProjectConfig(
        sProjectName="claudeon",
        features=FeaturesConfig(
            bClaude=True, bClaudeAutoUpdate=True,
        ),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.features.bClaude is True
    assert configLoaded.features.bClaudeAutoUpdate is True


def test_claude_auto_update_yaml_roundtrip_false():
    config = ProjectConfig(
        sProjectName="claudeoff",
        features=FeaturesConfig(
            bClaude=True, bClaudeAutoUpdate=False,
        ),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.features.bClaudeAutoUpdate is False


def test_claude_auto_update_missing_key_defaults_true():
    import yaml
    dictConfig = {
        "projectName": "legacy",
        "features": {"claude": True},
    }
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        with open(sPath, "w") as fileHandle:
            yaml.safe_dump(dictConfig, fileHandle)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.features.bClaudeAutoUpdate is True


@pytest.mark.parametrize(
    "sAgent,sEnabled,sAutoUpdate",
    [
        ("codex", "bCodex", "bCodexAutoUpdate"),
        ("gemini", "bGemini", "bGeminiAutoUpdate"),
        ("opencode", "bOpenCode", "bOpenCodeAutoUpdate"),
        ("cline", "bCline", "bClineAutoUpdate"),
        ("openhands", "bOpenHands", "bOpenHandsAutoUpdate"),
        ("pi", "bPi", "bPiAutoUpdate"),
    ],
)
def test_new_agent_auto_update_roundtrip_and_legacy_default(
    sAgent, sEnabled, sAutoUpdate,
):
    """Each provider persists its own setting and old files default safely."""
    import yaml
    config = ProjectConfig(
        sProjectName=sAgent,
        features=FeaturesConfig(**{sEnabled: True, sAutoUpdate: False}),
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        assert getattr(fconfigLoadFromFile(sPath).features, sAutoUpdate) is False
        with open(sPath, "w") as fileHandle:
            yaml.safe_dump({"projectName": "legacy", "features": {
                sAgent: True,
            }}, fileHandle)
        assert getattr(fconfigLoadFromFile(sPath).features, sAutoUpdate) is True


@pytest.mark.falsification
def test_pi_auto_update_yaml_mapping_cannot_be_dropped():
    """A disabled Pi update preference must survive configuration parsing.

    Kills: delete the ``piAutoUpdate`` mapping from
    ``_FEATURES_YAML_TO_HUNGARIAN`` in ``projectConfig.py``.
    """
    from vaibify.config.projectConfig import fconfigFromYamlDict

    config = fconfigFromYamlDict({
        "projectName": "pi-preference",
        "features": {"pi": True, "piAutoUpdate": False},
    })
    assert config.features.bPi is True
    assert config.features.bPiAutoUpdate is False


# ---------------------------------------------------------------------------
# iDashboardPort — stable per-project port persistence
# ---------------------------------------------------------------------------


def test_dashboard_port_defaults_to_zero():
    config = ProjectConfig(sProjectName="demo")
    assert config.iDashboardPort == 0


def test_dashboard_port_roundtrips_through_yaml():
    config = ProjectConfig(
        sProjectName="demo", iDashboardPort=8077,
    )
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        fnSaveToFile(config, sPath)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.iDashboardPort == 8077


def test_dashboard_port_missing_key_defaults_to_zero():
    import yaml
    dictConfig = {"projectName": "legacy"}
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "vaibify.yml")
        with open(sPath, "w") as fileHandle:
            yaml.safe_dump(dictConfig, fileHandle)
        configLoaded = fconfigLoadFromFile(sPath)
    assert configLoaded.iDashboardPort == 0


def test_dashboard_port_invalid_value_rejected_by_validator():
    dictConfig = {
        "projectName": "demo",
        "dashboardPort": 22,
    }
    assert fbValidateConfig(dictConfig) is False


def test_dashboard_port_zero_is_valid_sentinel():
    dictConfig = {
        "projectName": "demo",
        "dashboardPort": 0,
    }
    assert fbValidateConfig(dictConfig) is True


def test_dashboard_port_in_range_is_valid():
    dictConfig = {
        "projectName": "demo",
        "dashboardPort": 8050,
    }
    assert fbValidateConfig(dictConfig) is True
