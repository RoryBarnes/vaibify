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


@patch("vaibify.docker.fbDockerDaemonReachable", return_value=True)
def test_fpreflightDaemon_reachable_returns_ok(mockDaemon):
    """A reachable daemon yields an ok-level result."""
    resultPreflight = fpreflightDaemon()
    assert resultPreflight.sLevel == "ok"
    assert resultPreflight.sName == "docker-daemon"


@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=True)
def test_fpreflightDaemon_colima_remediation(mockColima, mockDaemon):
    """Colima-active failure points the user at `colima start`."""
    resultPreflight = fpreflightDaemon("build")
    assert resultPreflight.sLevel == "fail"
    assert "colima start" in resultPreflight.sRemediation.lower()
    assert "vaibify build" in resultPreflight.sRemediation


@patch("vaibify.docker.fbDockerDaemonReachable", return_value=False)
@patch("vaibify.docker.dockerContext.fbColimaActive", return_value=False)
def test_fpreflightDaemon_no_colima_remediation(mockColima, mockDaemon):
    """Non-Colima failure points the user at Docker Desktop."""
    resultPreflight = fpreflightDaemon()
    assert resultPreflight.sLevel == "fail"
    assert "Docker Desktop" in resultPreflight.sRemediation


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
