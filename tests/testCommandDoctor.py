"""Tests for `vaibify doctor` and the shared preflightChecks helpers."""

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from vaibify.cli.commandDoctor import doctor, flistRunDoctorChecks
from vaibify.cli.preflightChecks import (
    fpreflightColimaVersion,
    fpreflightDaemon,
    fpreflightDockerContextActive,
)
from vaibify.cli.preflightResult import PreflightResult


def _fconfigForDoctor(sProjectName="proj"):
    """Return a minimal config namespace suitable for the doctor subcommand."""
    return SimpleNamespace(
        sProjectName=sProjectName,
        sWorkspaceRoot="/workspace",
        listPorts=[],
        listBindMounts=[],
        features=SimpleNamespace(bGpu=False),
    )


def _fresultOk(sName="check"):
    """Return a stock ok-level PreflightResult."""
    return PreflightResult(sName=sName, sLevel="ok", sMessage="all good")


def _fresultFail(sName="check"):
    """Return a stock fail-level PreflightResult."""
    return PreflightResult(
        sName=sName, sLevel="fail",
        sMessage="something broke",
        sRemediation="fix it",
    )


# -----------------------------------------------------------------------
# preflightChecks.fpreflightDaemon
# -----------------------------------------------------------------------


_S_COLIMA_DAEMON_STDERR = (
    "Cannot connect to the Docker daemon at "
    "unix:///Users/x/.colima/default/docker.sock. "
    "Is the docker daemon running?\n"
)


@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(0, ""),
)
def test_fpreflightDaemon_reachable_returns_ok(mockProbe):
    """A reachable daemon yields an ok-level result."""
    resultPreflight = fpreflightDaemon()
    assert resultPreflight.sLevel == "ok"
    assert resultPreflight.sName == "docker-daemon"


@patch(
    "vaibify.docker.dockerContext.fsActiveDockerContext",
    return_value="colima",
)
@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(1, _S_COLIMA_DAEMON_STDERR),
)
def test_fpreflightDaemon_colima_remediation(mockProbe, mockContext):
    """Colima-active failure points the user at `colima start`."""
    resultPreflight = fpreflightDaemon("build")
    assert resultPreflight.sLevel == "fail"
    assert resultPreflight.sCommand == "colima start"
    assert "colima" in resultPreflight.sRemediation.lower()
    assert "vaibify build" in resultPreflight.sRemediation


@patch("vaibify.cli.preflightChecks.sys.platform", "darwin")
@patch(
    "vaibify.docker.dockerContext.fsActiveDockerContext",
    return_value="desktop-linux",
)
@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(1, _S_COLIMA_DAEMON_STDERR),
)
def test_fpreflightDaemon_no_colima_remediation(mockProbe, mockContext):
    """Non-Colima failure on macOS points the user at Docker Desktop."""
    resultPreflight = fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "Docker Desktop" in resultPreflight.sRemediation


@patch("vaibify.cli.preflightChecks.sys.platform", "linux")
@patch(
    "vaibify.docker.dockerContext.fsActiveDockerContext",
    return_value="default",
)
@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(1, _S_COLIMA_DAEMON_STDERR),
)
def test_fpreflightDaemon_linux_remediation(mockProbe, mockContext):
    """Non-Colima failure on Linux points the user at systemctl."""
    resultPreflight = fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert resultPreflight.sCommand == "sudo systemctl start docker"
    assert "docker.service" in resultPreflight.sRemediation


@patch(
    "vaibify.docker.dockerContext.fsActiveDockerContext",
    return_value="colima",
)
@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(
        1,
        "failed to run attach disk \"colima\", in use by instance \"colima\"",
    ),
)
def test_fpreflightDaemon_surfaces_colima_stale_lock(mockProbe, mockContext):
    """A stale-lock stderr produces the specific catalog hint and command."""
    resultPreflight = fpreflightDaemon("start")
    assert resultPreflight.sLevel == "fail"
    assert resultPreflight.sCommand == "colima stop --force && colima start"
    assert "stale" in resultPreflight.sRemediation.lower()


@patch(
    "vaibify.docker.dockerContext.fsActiveDockerContext",
    return_value="colima",
)
@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(1, _S_COLIMA_DAEMON_STDERR),
)
def test_fpreflightDaemon_carries_raw_error(mockProbe, mockContext):
    """The verbatim daemon stderr is appended as a `Raw error:` line."""
    resultPreflight = fpreflightDaemon()
    assert "Raw error:" in resultPreflight.sRemediation
    assert "Cannot connect to the Docker daemon" in resultPreflight.sRemediation


@patch(
    "vaibify.cli.preflightChecks._ftDockerInfoProbe",
    return_value=(
        1,
        "Permission denied while trying to connect to the Docker "
        "daemon socket at unix:///var/run/docker.sock",
    ),
)
def test_fpreflightDaemon_socket_permission_branch(mockProbe):
    """A permission-denied stderr uses the dedicated socket-perm hint."""
    resultPreflight = fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "Docker socket unreadable" in resultPreflight.sRemediation


# -----------------------------------------------------------------------
# preflightChecks.fpreflightColimaVersion
# -----------------------------------------------------------------------


def test_fpreflightColimaVersion_silent_when_inactive():
    """Non-Colima context returns None."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
    ):
        assert fpreflightColimaVersion() is None


def test_fpreflightColimaVersion_warns_below_floor():
    """A version below 0.5.0 yields a warn-level result."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion",
        return_value=(0, 4, 0),
    ):
        resultPreflight = fpreflightColimaVersion()
    assert resultPreflight is not None
    assert resultPreflight.sLevel == "warn"


def test_fpreflightColimaVersion_silent_at_floor():
    """A version at the floor yields no result."""
    with patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ), patch(
        "vaibify.docker.dockerContext.ftColimaVersion",
        return_value=(0, 5, 0),
    ):
        assert fpreflightColimaVersion() is None


# -----------------------------------------------------------------------
# preflightChecks.fpreflightDockerContextActive
# -----------------------------------------------------------------------


def test_fpreflightDockerContextActive_reports_context():
    """An active context name is reported at info/ok level."""
    with patch(
        "vaibify.docker.dockerContext.fsActiveDockerContext",
        return_value="colima",
    ):
        resultPreflight = fpreflightDockerContextActive()
    assert resultPreflight.sLevel == "ok"
    assert "colima" in resultPreflight.sMessage


def test_fpreflightDockerContextActive_handles_empty_context():
    """An empty context lookup yields an info-level result."""
    with patch(
        "vaibify.docker.dockerContext.fsActiveDockerContext",
        return_value="",
    ):
        resultPreflight = fpreflightDockerContextActive()
    assert resultPreflight.sLevel == "info"


# -----------------------------------------------------------------------
# doctor — happy paths
# -----------------------------------------------------------------------


def _patchScopeHelpers(listBuild=None, listStart=None):
    """Return the patch context manager set used to stub doctor scope helpers."""
    listBuild = listBuild or []
    listStart = listStart or []
    return [
        patch(
            "vaibify.cli.commandDoctor._flistBuildOnlyChecks",
            return_value=list(listBuild),
        ),
        patch(
            "vaibify.cli.commandDoctor._flistStartOnlyChecks",
            return_value=list(listStart),
        ),
    ]


def _runDoctor(saArgs, listShared=None, listBuild=None, listStart=None):
    """Invoke the doctor CLI command with all helpers patched."""
    listShared = listShared or [_fresultOk("docker-context"),
                                _fresultOk("docker-daemon")]
    contextScope = _patchScopeHelpers(listBuild, listStart)
    with patch(
        "vaibify.cli.commandDoctor.fconfigResolveProject",
        return_value=_fconfigForDoctor(),
    ), patch(
        "vaibify.cli.commandDoctor._flistSharedChecks",
        return_value=list(listShared),
    ), contextScope[0] as mockBuild, contextScope[1] as mockStart:
        result = CliRunner().invoke(doctor, saArgs)
    return result, mockBuild, mockStart


def test_doctor_happy_path_exits_zero():
    """All-ok results yield exit 0 and a `N ok / 0 warn / 0 fail` summary."""
    listShared = [_fresultOk("docker-context"), _fresultOk("docker-daemon")]
    listBuild = [_fresultOk("docker-disk")]
    listStart = [_fresultOk("image")]
    result, _, _ = _runDoctor(
        [], listShared=listShared,
        listBuild=listBuild, listStart=listStart,
    )
    assert result.exit_code == 0
    assert "[ok] docker-daemon" in result.output
    assert "4 ok / 0 warn / 0 fail" in result.output


def test_doctor_exits_one_on_fail():
    """Any fail-level result drives a non-zero exit and prints the line."""
    listShared = [_fresultOk("docker-context"), _fresultOk("docker-daemon")]
    listStart = [_fresultFail("image")]
    result, _, _ = _runDoctor(
        [], listShared=listShared, listStart=listStart,
    )
    assert result.exit_code == 1
    assert "[fail] image: something broke" in result.output
    assert "fix it" in result.output
    assert "0 warn / 1 fail" in result.output


def test_doctor_quiet_suppresses_ok_lines():
    """`--quiet` hides ok-level entries but keeps the summary."""
    listShared = [_fresultOk("docker-context"), _fresultOk("docker-daemon")]
    listBuild = [PreflightResult(
        sName="docker-disk", sLevel="warn",
        sMessage="disk getting tight",
    )]
    result, _, _ = _runDoctor(
        ["--quiet"], listShared=listShared, listBuild=listBuild,
    )
    assert result.exit_code == 0
    assert "[ok]" not in result.output
    assert "[warn] docker-disk" in result.output
    assert "ok / 1 warn / 0 fail" in result.output


def test_doctor_build_scope_runs_only_build_helpers():
    """`--build` invokes the build subset and skips start helpers."""
    result, mockBuild, mockStart = _runDoctor(["--build"])
    assert result.exit_code == 0
    mockBuild.assert_called_once()
    mockStart.assert_not_called()


def test_doctor_start_scope_runs_only_start_helpers():
    """`--start` invokes the start subset and skips build helpers."""
    result, mockBuild, mockStart = _runDoctor(["--start"])
    assert result.exit_code == 0
    mockStart.assert_called_once()
    mockBuild.assert_not_called()


def test_doctor_default_runs_both_scopes():
    """No scope flag runs both subsets."""
    result, mockBuild, mockStart = _runDoctor([])
    assert result.exit_code == 0
    mockBuild.assert_called_once()
    mockStart.assert_called_once()


# -----------------------------------------------------------------------
# doctor — daemon short-circuit
# -----------------------------------------------------------------------


def test_doctor_short_circuits_when_daemon_fails():
    """A failing daemon check skips every scope-specific helper."""
    listShared = [
        _fresultOk("docker-context"),
        PreflightResult(
            sName="docker-daemon", sLevel="fail",
            sMessage="not running",
            sRemediation="run colima",
        ),
    ]
    config = _fconfigForDoctor()
    with patch(
        "vaibify.cli.commandDoctor._flistSharedChecks",
        return_value=listShared,
    ), patch(
        "vaibify.cli.commandDoctor._flistBuildOnlyChecks",
    ) as mockBuild, patch(
        "vaibify.cli.commandDoctor._flistStartOnlyChecks",
    ) as mockStart:
        listResults = flistRunDoctorChecks(config, False, False)
    assert any(r.sLevel == "fail" for r in listResults)
    mockBuild.assert_not_called()
    mockStart.assert_not_called()


# -----------------------------------------------------------------------
# doctor — registered with the main CLI
# -----------------------------------------------------------------------


def test_doctor_command_registered_on_main_cli():
    """`vaibify doctor --help` succeeds via the top-level Click group."""
    from vaibify.cli.main import main
    result = CliRunner().invoke(main, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "Run pre-flight checks" in result.output


# -----------------------------------------------------------------------
# doctor — optional shared probes wired into _flistSharedChecks
# -----------------------------------------------------------------------


def test_doctor_shared_checks_include_optional_probes():
    """`_flistSharedChecks` invokes the hostagent-log and systemd probes."""
    from vaibify.cli.commandDoctor import _flistSharedChecks
    with patch(
        "vaibify.cli.commandDoctor.fpreflightDockerContextActive",
        return_value=_fresultOk("docker-context"),
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightDaemon",
        return_value=_fresultOk("docker-daemon"),
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightColimaVersion",
        return_value=None,
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightColimaHostagentLog",
        return_value=None,
    ) as mockHostagent, patch(
        "vaibify.cli.commandDoctor.fpreflightLinuxDockerService",
        return_value=None,
    ) as mockSystemd:
        _flistSharedChecks()
    mockHostagent.assert_called_once()
    mockSystemd.assert_called_once()


def test_doctor_shared_checks_includes_hostagent_warn():
    """A warn from the hostagent probe is included in shared results."""
    from vaibify.cli.commandDoctor import _flistSharedChecks
    resultWarn = PreflightResult(
        sName="colima-hostagent-log", sLevel="warn",
        sMessage="recent error",
        sRemediation="Restart Colima.",
        sCommand="colima stop --force && colima start",
    )
    with patch(
        "vaibify.cli.commandDoctor.fpreflightDockerContextActive",
        return_value=_fresultOk("docker-context"),
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightDaemon",
        return_value=_fresultOk("docker-daemon"),
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightColimaVersion",
        return_value=None,
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightColimaHostagentLog",
        return_value=resultWarn,
    ), patch(
        "vaibify.cli.commandDoctor.fpreflightLinuxDockerService",
        return_value=None,
    ):
        listResults = _flistSharedChecks()
    assert any(r.sName == "colima-hostagent-log" for r in listResults)
