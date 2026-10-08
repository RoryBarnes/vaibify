"""The dashboard tells the marker plugin which category it is running.

A pytest session that fails during collection never reaches a test, so
the plugin has no file to charge the failure to. The dashboard knows
which category's command it launched, and exports it as
``VAIBIFY_TEST_CATEGORY`` so the failure lands on that category's files
rather than on nobody. The value is looked up from a fixed table, never
composed from the caller's string: this is text that reaches a shell.
"""

import pytest

from vaibify.gui.routes import testRoutes


def _fdictWorkflow():
    return {
        "sProjectRepoPath": "/workspace/proj",
        "_sLoadedFromPath": "/workspace/proj/.vaibify/projects/demo.json",
    }


def _fdictStep():
    return {"sDirectory": "StepA"}


@pytest.mark.falsification
def test_a_category_command_exports_its_category_and_the_workflow():
    """Both environment prefixes ride the same command.

    Kills: building the category command without the category export,
    which charges a collection failure to no category at all.
    """
    sCommand = testRoutes._fsBuildCategoryCommand(
        _fdictStep(), _fdictWorkflow(), ["python -m pytest tests"],
        "integrity")
    assert "export VAIBIFY_TEST_CATEGORY=integrity" in sCommand
    assert "export VAIBIFY_ACTIVE_WORKFLOW_SLUG='demo'" in sCommand
    assert sCommand.index("VAIBIFY_ACTIVE_WORKFLOW_SLUG") < sCommand.index(
        "cd ")


@pytest.mark.falsification
def test_only_the_fixed_category_names_ever_reach_the_shell():
    """A name outside the table exports nothing, hostile or merely legacy.

    Kills: falling back to the caller's own string when the name is not
    in the table, which lets request text become shell text.
    """
    for sName in ("legacy", "", "other", "integrity; touch /tmp/x",
                  "$(id)", "Integrity"):
        sCommand = testRoutes._fsPrefixWithTestCategoryEnv("pytest", sName)
        assert sCommand == "pytest", sName
    for sName, sExpected in (
        ("dictQualitative", "qualitative"), ("quantitative", "quantitative"),
    ):
        assert testRoutes._fsPrefixWithTestCategoryEnv(
            "pytest", sName) == (
            "export VAIBIFY_TEST_CATEGORY=" + sExpected + " && pytest")


def test_the_run_all_lane_signals_each_group_and_not_the_legacy_one():
    listCommands = []

    class DockerRecorder:
        def ftRunInContainerStreamed(self, sContainerId, sCommand):
            listCommands.append(sCommand)
            from types import SimpleNamespace
            return SimpleNamespace(iExitCode=0, sStdout="", sStderr="")

    dictCtx = {"docker": DockerRecorder()}
    for sGroup in ("dictIntegrity", "dictQuantitative", "legacy"):
        testRoutes._fdictRunOneTestCategory(
            dictCtx, "cid", "/workspace/proj/StepA", ["pytest tests"],
            "demo", sGroup)
    assert "VAIBIFY_TEST_CATEGORY=integrity" in listCommands[0]
    assert "VAIBIFY_TEST_CATEGORY=quantitative" in listCommands[1]
    assert "VAIBIFY_TEST_CATEGORY" not in listCommands[2]
