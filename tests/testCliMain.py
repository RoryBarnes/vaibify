"""Tests for vaibify.cli.main — main group, stop, connect, verify, etc."""

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.main import main

# The fnLaunchHub / setup / gui tests below patch ``uvicorn.run`` and
# ``webbrowser.open`` directly rather than swapping the modules out of
# ``sys.modules``. Earlier revisions of this file used
# ``patch.dict(sys.modules, {"uvicorn": mock, "webbrowser": mock})``,
# but that snapshots and restores ``sys.modules`` on context exit —
# evicting every module imported transitively *during* the context,
# including ``starlette.requests``. The next test that builds a
# FastAPI app then sees a re-imported ``Request`` class identity that
# no longer matches the one cached inside
# ``fastapi.dependencies.utils``, triggering a spurious
# ``FastAPIError`` on ``lenient_issubclass(annotation, Request)`` —
# the symptom was ``test_gui_launches_pipeline_viewer`` passing in
# isolation but failing after ``test_fnLaunchHub_starts_server``.
# Patching ``uvicorn.run`` and ``webbrowser.open`` at attribute level
# leaves ``sys.modules`` undisturbed and avoids the cascade.


# -----------------------------------------------------------------------
# main group
# -----------------------------------------------------------------------


def test_main_group_help_lists_all_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for sCommand in (
        "init", "build", "start", "status",
        "destroy", "config", "stop",
        "connect", "verify", "setup", "gui",
        "push", "pull",
    ):
        assert sCommand in result.output


def test_main_group_hides_unimplemented_publish():
    """`publish` stays unregistered while both subcommands are stubs.

    Advertising a command whose every path prints "Not yet
    implemented." misrepresents what the tool can do; the stub module
    lives on in vaibify/cli/commandPublish.py until it is real.
    """
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "publish" not in result.output
    resultInvoke = runner.invoke(main, ["publish"])
    assert resultInvoke.exit_code != 0


def test_main_version_option():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0


# -----------------------------------------------------------------------
# stop
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
def test_stop_success(mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
    )
    with patch(
        "vaibify.docker.containerManager.fnStopContainer",
    ) as mockStop:
        runner = CliRunner()
        result = runner.invoke(main, ["stop"])
        assert result.exit_code == 0
        assert "Stopped" in result.output


@patch("vaibify.cli.main.fconfigResolveProject")
def test_stop_not_running_exits(mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="testproj",
    )
    with patch(
        "vaibify.docker.containerManager.fnStopContainer",
        side_effect=RuntimeError("not running"),
    ):
        runner = CliRunner()
        result = runner.invoke(main, ["stop"])
        assert result.exit_code != 0
        assert "not active" in result.output.lower()


# -----------------------------------------------------------------------
# connect
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_connect_calls_docker_exec(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["connect"])
    assert mockRun.called
    listArgs = mockRun.call_args[0][0]
    assert "docker" in listArgs
    assert "exec" in listArgs
    assert "researcher" in listArgs


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_connect_with_project_option(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="myproj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["connect", "-p", "myproj"])
    mockConfig.assert_called_once_with("myproj")
    assert mockRun.called


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPushToContainer")
def test_push_with_project_option(mockPush, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="myproj",
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["push", "-p", "myproj", "/src", "/dst"],
    )
    assert result.exit_code == 0
    mockConfig.assert_called_once_with("myproj")
    mockPush.assert_called_once_with("myproj", "/src", "/dst")


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPullFromContainer")
def test_pull_with_project_option(mockPull, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="myproj",
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["pull", "-p", "myproj", "/src", "/dst"],
    )
    assert result.exit_code == 0
    mockConfig.assert_called_once_with("myproj")
    mockPull.assert_called_once_with("myproj", "/src", "/dst")


# -----------------------------------------------------------------------
# verify
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("subprocess.run")
def test_verify_calls_check_isolation(mockRun, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
        sContainerUser="researcher",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["verify"])
    assert mockRun.called
    listArgs = mockRun.call_args[0][0]
    assert "checkIsolation" in listArgs[-1]


# -----------------------------------------------------------------------
# setup help
# -----------------------------------------------------------------------


def test_setup_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--help"])
    assert result.exit_code == 0
    assert "setup" in result.output.lower()


# -----------------------------------------------------------------------
# gui help
# -----------------------------------------------------------------------


def test_gui_help_text():
    """Updated 2026-08-10 with the command's wording; see testCliCommands."""
    runner = CliRunner()
    result = runner.invoke(main, ["gui", "--help"])
    assert result.exit_code == 0
    assert "dashboard" in result.output.lower()


def test_gui_help_no_user_option():
    runner = CliRunner()
    result = runner.invoke(main, ["gui", "--help"])
    assert "--user" not in result.output


# -----------------------------------------------------------------------
# push
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPushToContainer")
def test_push_calls_transfer(mockPush, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["push", "/src", "/dst"])
    assert result.exit_code == 0
    assert "Pushed" in result.output
    mockPush.assert_called_once_with("proj", "/src", "/dst")


# -----------------------------------------------------------------------
# pull
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
@patch("vaibify.docker.fileTransfer.fnPullFromContainer")
def test_pull_calls_transfer(mockPull, mockConfig):
    mockConfig.return_value = SimpleNamespace(
        sProjectName="proj",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["pull", "/src", "/dst"])
    assert result.exit_code == 0
    assert "Pulled" in result.output
    mockPull.assert_called_once_with("proj", "/src", "/dst")


# -----------------------------------------------------------------------
# push / pull help
# -----------------------------------------------------------------------


def test_push_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["push", "--help"])
    assert result.exit_code == 0
    assert "Push" in result.output
    assert "--project" in result.output


def test_pull_help_text():
    runner = CliRunner()
    result = runner.invoke(main, ["pull", "--help"])
    assert result.exit_code == 0
    assert "Pull" in result.output
    assert "--project" in result.output


def test_connect_help_shows_project_option():
    runner = CliRunner()
    result = runner.invoke(main, ["connect", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.output


# -----------------------------------------------------------------------
# --config option
# -----------------------------------------------------------------------


def test_main_config_option_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "--config" in result.output


# -----------------------------------------------------------------------
# --config option sets path (lines 51-52)
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fnLaunchHub")
def test_main_config_option_calls_set_path(mockLaunch, tmp_path):
    """Lines 51-52: --config option invokes fnSetConfigPath."""
    sConfigPath = str(tmp_path / "vaibify.yml")
    with open(sConfigPath, "w") as fh:
        fh.write("projectName: test\n")
    with patch(
        "vaibify.cli.main.fnSetConfigPath",
        create=True,
    ) as mockSetPath:
        with patch(
            "vaibify.cli.configLoader.fnSetConfigPath",
        ) as mockSetPathReal:
            runner = CliRunner()
            result = runner.invoke(
                main, ["--config", sConfigPath],
            )
            mockSetPathReal.assert_called_once_with(sConfigPath)


# -----------------------------------------------------------------------
# main invoked without subcommand (line 54)
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fnLaunchHub")
def test_main_no_subcommand_calls_launch_hub(mockLaunch):
    """Line 54: no subcommand invokes fnLaunchHub with no explicit port."""
    runner = CliRunner()
    result = runner.invoke(main, [])
    mockLaunch.assert_called_once_with(None)


@patch("vaibify.cli.main.fnLaunchHub")
def test_main_custom_port_passed_to_launch_hub(mockLaunch):
    """Lines 54: --port forwarded to fnLaunchHub."""
    runner = CliRunner()
    result = runner.invoke(main, ["--port", "9999"])
    mockLaunch.assert_called_once_with(9999)


# -----------------------------------------------------------------------
# fnLaunchHub (lines 59-72)
# -----------------------------------------------------------------------


def _fnPatchSessionSlot():
    """Return (acquirePatch, releasePatch) that no-op the session registry."""
    return (
        patch(
            "vaibify.config.sessionRegistry.ffileAcquireSessionSlot",
            return_value=MagicMock(),
        ),
        patch(
            "vaibify.config.sessionRegistry.fnReleaseSessionSlot",
        ),
    )


def test_fnLaunchHub_starts_server():
    """Lines 59-72: fnLaunchHub creates app and runs uvicorn.

    Sets ``VAIBIFY_SUPPRESS_BROWSER=1`` to prevent the daemon thread
    that schedules ``webbrowser.open`` from firing a *real* browser
    tab after the patch context exits — ``patch("webbrowser.open")``
    only holds for the synchronous test body, not the 1-second
    sleeper inside the thread.
    """
    import os
    patchAcquireSlot, patchReleaseSlot = _fnPatchSessionSlot()
    with patch("uvicorn.run") as mockRun, \
            patch("webbrowser.open"), \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ), patchAcquireSlot, patchReleaseSlot:
        with patch(
            "vaibify.gui.pipelineServer.fappCreateHubApplication",
        ) as mockApp:
            mockApp.return_value = MagicMock()
            from vaibify.cli.main import fnLaunchHub
            fnLaunchHub(8050)
            mockApp.assert_called_once()
            mockRun.assert_called_once()
            args = mockRun.call_args
            assert args[1]["port"] == 8050


def test_fnLaunchHub_suppresses_browser_when_env_set():
    """VAIBIFY_SUPPRESS_BROWSER=1 means no webbrowser.open thread fires."""
    import os
    import time
    patchAcquireSlot, patchReleaseSlot = _fnPatchSessionSlot()
    with patch("uvicorn.run"), \
            patch("webbrowser.open") as mockOpen, \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ), patch(
                "vaibify.gui.pipelineServer.fappCreateHubApplication",
                return_value=MagicMock(),
            ), patchAcquireSlot, patchReleaseSlot:
        from vaibify.cli.main import fnLaunchHub
        fnLaunchHub(8050)
    time.sleep(1.2)
    mockOpen.assert_not_called()


def test_setup_suppresses_browser_when_env_set():
    """The setup command must honour VAIBIFY_SUPPRESS_BROWSER too.

    Without this gate the daemon thread fires after every test that
    invokes the setup command, popping a real browser tab at
    http://127.0.0.1:8051 long after the test's webbrowser.open patch
    has been restored.
    """
    import os
    import time
    with patch("uvicorn.run"), \
            patch("webbrowser.open") as mockOpen, \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ), patch(
                "vaibify.install.setupServer.fappCreateSetupWizard",
                return_value=MagicMock(),
            ):
        runner = CliRunner()
        runner.invoke(main, ["setup"])
    time.sleep(1.2)
    mockOpen.assert_not_called()


@patch("vaibify.cli.main.fconfigResolveProject")
def test_gui_suppresses_browser_when_env_set(mockConfig):
    """The gui command must honour VAIBIFY_SUPPRESS_BROWSER too.

    Same hazard as ``test_setup_suppresses_browser_when_env_set``;
    the gui command's daemon thread otherwise pops a real tab at
    http://127.0.0.1:8050.
    """
    import os
    import time
    mockConfig.return_value = SimpleNamespace(
        sWorkspaceRoot="/workspace",
        sContainerUser="researcher",
    )
    with patch("uvicorn.run"), \
            patch("webbrowser.open") as mockOpen, \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ), patch(
                "vaibify.gui.pipelineServer.fappCreateApplication",
                return_value=MagicMock(),
            ):
        runner = CliRunner()
        runner.invoke(main, ["gui"])
    time.sleep(1.2)
    mockOpen.assert_not_called()


def test_fnLaunchHub_exits_when_session_limit_reached():
    """fnLaunchHub exits nonzero when the 99-session cap is hit."""
    import os
    from vaibify.config.sessionRegistry import SessionLimitExceededError
    with patch("uvicorn.run") as mockRun, \
            patch("webbrowser.open"), \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ), patch(
                "vaibify.config.sessionRegistry.ffileAcquireSessionSlot",
                side_effect=SessionLimitExceededError(99, 99),
            ):
        import pytest
        from vaibify.cli.main import fnLaunchHub
        with pytest.raises(SystemExit) as exitInfo:
            fnLaunchHub(8050)
    assert exitInfo.value.code == 1
    mockRun.assert_not_called()


# -----------------------------------------------------------------------
# setup command (lines 126-138)
# -----------------------------------------------------------------------


def test_setup_launches_wizard():
    """Lines 126-138: setup command starts wizard server.

    See note in ``test_fnLaunchHub_starts_server`` — env-var
    suppression is the only reliable way to keep the daemon thread
    from firing a real browser tab after the patch context exits.
    """
    import os
    with patch("uvicorn.run") as mockRun, \
            patch("webbrowser.open"), \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ):
        with patch(
            "vaibify.install.setupServer.fappCreateSetupWizard",
        ) as mockWizard:
            mockWizard.return_value = MagicMock()
            runner = CliRunner()
            result = runner.invoke(main, ["setup"])
            assert "setup wizard" in result.output.lower()
            mockRun.assert_called_once()


# -----------------------------------------------------------------------
# gui command (lines 144-163)
# -----------------------------------------------------------------------


@patch("vaibify.cli.main.fconfigResolveProject")
def test_gui_with_a_project_launches_that_projects_viewer(mockConfig):
    """Rewritten 2026-08-10: this invoked ``gui`` with NO project.

    It predated the fix and pinned the defect. A bare ``gui`` built the
    single-project viewer with a ``/workspace`` workspace root — a
    container path, on a laptop — while its own help promised the
    landing page; the fixture above even supplied that root, so the
    test agreed with the bug. A bare ``gui`` now launches the hub
    (``testGuiLaunchHonesty``), and what belongs here is the branch
    this command still owns: opening a NAMED project.

    See ``test_fnLaunchHub_starts_server`` — env-var suppression is the
    only reliable way to keep the daemon thread from firing a real
    browser tab after the patch context exits.
    """
    import os
    mockConfig.return_value = SimpleNamespace(
        sWorkspaceRoot="/workspace",
        sContainerUser="researcher",
    )
    mockCreateApp = MagicMock(return_value=MagicMock())
    with patch("uvicorn.run") as mockRun, \
            patch("webbrowser.open"), \
            patch.dict(
                os.environ, {"VAIBIFY_SUPPRESS_BROWSER": "1"},
            ):
        with patch(
            "vaibify.gui.pipelineServer.fappCreateApplication",
            mockCreateApp,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["gui", "--project", "someProject"])
            assert result.exit_code == 0, result.output
            assert "starting vaibify" in result.output.lower()
            mockRun.assert_called_once()


# -----------------------------------------------------------------------
# headless launch contract
# -----------------------------------------------------------------------


def _fnPatchedHeadlessLaunch(dictEnvironment):
    """Return the context managers a hub launch needs, plus the mint mock.

    The app is a MagicMock, so ``app.state.dictBrowserSessions`` is
    truthy and the launch takes the branch that WOULD mint. A launch
    against an app with no session store proves nothing about ordering,
    because it never reaches the mint under any ordering.
    """
    import os
    patchAcquireSlot, patchReleaseSlot = _fnPatchSessionSlot()
    return (
        patch("uvicorn.run"),
        patch("webbrowser.open"),
        patch("vaibify.gui.browserSession.fsMintBootstrapCapability"),
        patch.dict(os.environ, dictEnvironment),
        patch(
            "vaibify.gui.pipelineServer.fappCreateHubApplication",
            return_value=MagicMock(),
        ),
        patchAcquireSlot,
        patchReleaseSlot,
    )


def test_no_browser_flag_suppresses_the_launch():
    """``--no-browser`` is the public spelling of the same switch.

    The flag and ``VAIBIFY_SUPPRESS_BROWSER`` are one mechanism: the
    flag sets the variable, so a reader that honours the variable
    honours the flag and neither can be half-applied.
    """
    import os
    import time
    patchAcquireSlot, patchReleaseSlot = _fnPatchSessionSlot()
    with patch("uvicorn.run"), \
            patch("webbrowser.open") as mockOpen, \
            patch.dict(os.environ, {}), \
            patch(
                "vaibify.gui.pipelineServer.fappCreateHubApplication",
                return_value=MagicMock(),
            ), patchAcquireSlot, patchReleaseSlot:
        os.environ.pop("VAIBIFY_SUPPRESS_BROWSER", None)
        runner = CliRunner()
        result = runner.invoke(main, ["--no-browser", "--port", "8050"])
        assert result.exit_code == 0, result.output
        assert "not opening a browser" in result.output.lower(), result.output
        assert "not a daemon" in result.output.lower(), (
            "a headless launch must say it still holds the terminal; "
            f"researchers background it otherwise: {result.output}"
        )
    time.sleep(1.2)
    mockOpen.assert_not_called()


@pytest.mark.falsification
def test_suppressed_launch_mints_no_capability():
    """A launch that opens no browser must arm no credential.

    Kills: disabling the suppression guard in ``_fnAnnounceAndOpen``,
    which restores the historical ordering where the mint was the
    ARGUMENT to the suppression-checking call and therefore ran before
    anything was checked. Each wasted mint arms a one-time credential
    nobody can redeem and holds one of 64 slots for 300 seconds.
    """
    (
        patchRun, patchOpen, patchMint, patchEnvironment,
        patchApp, patchAcquireSlot, patchReleaseSlot,
    ) = _fnPatchedHeadlessLaunch({"VAIBIFY_SUPPRESS_BROWSER": "1"})
    with patchRun, patchOpen, patchApp, patchAcquireSlot, \
            patchReleaseSlot, patchEnvironment, patchMint as mockMint:
        from vaibify.cli.main import fnLaunchHub
        fnLaunchHub(8050)
    mockMint.assert_not_called()


@pytest.mark.falsification
def test_ordinary_launch_still_mints_a_capability():
    """The symmetric half: the suppressed assertion must not go vacuous.

    ``assert_not_called`` is equally true of a mint that was deleted
    outright, so the pair only means something with this half beside
    it.

    Kills: dropping the mint from ``_fsLaunchUrlWithCapability`` and
    returning the bare address, which is the defect that sent a
    researcher to a dashboard refusing every call.
    """
    import os
    (
        patchRun, patchOpen, patchMint, patchEnvironment,
        patchApp, patchAcquireSlot, patchReleaseSlot,
    ) = _fnPatchedHeadlessLaunch({})
    with patchRun, patchOpen, patchApp, patchAcquireSlot, \
            patchReleaseSlot, patchEnvironment, patchMint as mockMint:
        os.environ.pop("VAIBIFY_SUPPRESS_BROWSER", None)
        from vaibify.cli.main import fnLaunchHub
        fnLaunchHub(8050)
    mockMint.assert_called_once()
