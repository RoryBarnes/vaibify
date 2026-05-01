"""Pre-flight check tests for `vaibify start` (F-S-01..F-S-05)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner


def _fConfigForPreflight(
    listPorts=None, listBindMounts=None, sProjectName="proj",
):
    """Return a minimal config namespace usable by the pre-flight helpers."""
    return SimpleNamespace(
        sProjectName=sProjectName,
        sWorkspaceRoot="/workspace",
        listPorts=listPorts or [],
        listBindMounts=listBindMounts or [],
    )


def _fdictContainerStatus(bExists=False, bRunning=False, sStatus="not found"):
    """Return a status dict shaped like fdictGetContainerStatus."""
    return {"bExists": bExists, "bRunning": bRunning, "sStatus": sStatus}


# -----------------------------------------------------------------------
# F-S-01: docker daemon down
# -----------------------------------------------------------------------


def test_preflight_fails_when_daemon_unreachable_colima():
    """Daemon-down with colima active suggests `colima start`."""
    from vaibify.cli.commandStart import flistRunStartPreflight
    config = _fConfigForPreflight()
    with patch(
        "vaibify.docker.fbDockerDaemonReachable", return_value=False,
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=True,
    ):
        listResults = flistRunStartPreflight(config)
    assert listResults[0].sLevel == "fail"
    assert listResults[0].sName == "docker-daemon"
    assert "colima start" in listResults[0].sRemediation
    assert len(listResults) == 1


def test_preflight_fails_when_daemon_unreachable_no_colima():
    """Daemon-down without colima suggests Docker Desktop."""
    from vaibify.cli.commandStart import flistRunStartPreflight
    config = _fConfigForPreflight()
    with patch(
        "vaibify.docker.fbDockerDaemonReachable", return_value=False,
    ), patch(
        "vaibify.docker.dockerContext.fbColimaActive", return_value=False,
    ):
        listResults = flistRunStartPreflight(config)
    assert listResults[0].sLevel == "fail"
    assert "Docker Desktop" in listResults[0].sRemediation


def test_start_command_exits_one_when_daemon_unreachable():
    """`vaibify start` exits 1 and prints the daemon-down report."""
    from vaibify.cli.commandStart import start
    from vaibify.cli.preflightResult import PreflightResult
    listFail = [PreflightResult(
        sName="docker-daemon", sLevel="fail",
        sMessage="Docker daemon not reachable",
        sRemediation="Run `colima start`.",
    )]
    with patch(
        "vaibify.cli.commandStart.fconfigResolveProject",
        return_value=_fConfigForPreflight(),
    ), patch(
        "vaibify.cli.commandStart.flistRunStartPreflight",
        return_value=listFail,
    ), patch(
        "vaibify.cli.commandStart._fnStartContainer",
    ) as mockStart:
        result = CliRunner().invoke(start, [])
    assert result.exit_code == 1
    assert "Docker daemon not reachable" in result.output
    mockStart.assert_not_called()


# -----------------------------------------------------------------------
# F-S-02: image not built
# -----------------------------------------------------------------------


def test_preflight_fails_when_image_missing():
    """Image-missing emits a fail with `vaibify build` remediation."""
    from vaibify.cli.commandStart import flistRunStartPreflight
    config = _fConfigForPreflight(sProjectName="missingproj")
    with patch(
        "vaibify.docker.fbDockerDaemonReachable", return_value=True,
    ), patch(
        "vaibify.docker.fbImageExists", return_value=False,
    ), patch(
        "vaibify.cli.commandStart._flistpreflightPorts", return_value=[],
    ), patch(
        "vaibify.cli.commandStart._fpreflightContainerName",
    ) as mockName, patch(
        "vaibify.cli.commandStart._flistpreflightBindMounts",
        return_value=[],
    ):
        from vaibify.cli.preflightResult import PreflightResult
        mockName.return_value = PreflightResult(
            sName="container-name", sLevel="ok", sMessage="ok",
        )
        listResults = flistRunStartPreflight(config)
    listFails = [r for r in listResults if r.sLevel == "fail"]
    assert any(r.sName == "image" for r in listFails)
    sFail = next(r for r in listFails if r.sName == "image")
    assert "missingproj:latest" in sFail.sMessage
    assert "vaibify build" in sFail.sRemediation


# -----------------------------------------------------------------------
# F-S-03: host port collision
# -----------------------------------------------------------------------


def test_preflight_fails_when_host_port_in_use():
    """A listPorts entry whose host port is bound emits a fail."""
    from vaibify.cli.commandStart import _flistpreflightPorts
    config = _fConfigForPreflight(
        listPorts=[{"host": 8888, "container": 8888}],
    )
    with patch(
        "vaibify.docker.fbForwardedHostPortFree", return_value=False,
    ):
        listResults = _flistpreflightPorts(config)
    assert len(listResults) == 1
    assert listResults[0].sLevel == "fail"
    assert "8888" in listResults[0].sMessage
    assert "vaibify.yml" in listResults[0].sRemediation
    assert "lsof -i :8888" in listResults[0].sRemediation


def test_preflight_passes_when_host_port_free():
    """A free host port emits an ok-level result."""
    from vaibify.cli.commandStart import _flistpreflightPorts
    config = _fConfigForPreflight(
        listPorts=[{"host": 9000, "container": 9000}],
    )
    with patch(
        "vaibify.docker.fbForwardedHostPortFree", return_value=True,
    ):
        listResults = _flistpreflightPorts(config)
    assert listResults[0].sLevel == "ok"


def test_preflight_falls_back_to_container_when_host_omitted():
    """When `host` is omitted, the `container` port is checked instead."""
    from vaibify.cli.commandStart import _flistpreflightPorts
    config = _fConfigForPreflight(listPorts=[{"container": 7777}])
    listObserved = []
    with patch(
        "vaibify.docker.fbForwardedHostPortFree",
        side_effect=lambda iPort: listObserved.append(iPort) or True,
    ):
        _flistpreflightPorts(config)
    assert listObserved == [7777]


# -----------------------------------------------------------------------
# F-S-04: container name already exists
# -----------------------------------------------------------------------


def test_preflight_fails_when_container_already_running():
    """Running container of the same name emits a fail with stop hint."""
    from vaibify.cli.commandStart import _fpreflightContainerName
    config = _fConfigForPreflight(sProjectName="busy")
    with patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value=_fdictContainerStatus(
            bExists=True, bRunning=True, sStatus="running",
        ),
    ), patch(
        "vaibify.docker.containerManager.fnRemoveStopped",
    ) as mockRemove:
        result = _fpreflightContainerName(config)
    assert result.sLevel == "fail"
    assert "already running" in result.sMessage
    assert "vaibify stop" in result.sRemediation
    mockRemove.assert_not_called()


def test_preflight_warn_and_remove_when_container_stopped():
    """A stopped container is auto-removed and a warn is emitted."""
    from vaibify.cli.commandStart import _fpreflightContainerName
    config = _fConfigForPreflight(sProjectName="stale")
    with patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value=_fdictContainerStatus(
            bExists=True, bRunning=False, sStatus="exited",
        ),
    ), patch(
        "vaibify.docker.containerManager.fnRemoveStopped",
    ) as mockRemove:
        result = _fpreflightContainerName(config)
    assert result.sLevel == "warn"
    assert "stale" in result.sMessage
    mockRemove.assert_called_once_with("stale")


def test_preflight_ok_when_container_not_present():
    """No existing container yields an ok result."""
    from vaibify.cli.commandStart import _fpreflightContainerName
    config = _fConfigForPreflight(sProjectName="fresh")
    with patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value=_fdictContainerStatus(),
    ):
        result = _fpreflightContainerName(config)
    assert result.sLevel == "ok"


# -----------------------------------------------------------------------
# F-S-05: bind-mount source path missing
# -----------------------------------------------------------------------


def test_preflight_fails_when_bind_mount_source_missing(tmp_path):
    """A nonexistent bind-mount host path emits a fail."""
    from vaibify.cli.commandStart import _flistpreflightBindMounts
    sMissing = str(tmp_path / "does-not-exist")
    config = _fConfigForPreflight(
        listBindMounts=[{"host": sMissing, "container": "/data"}],
    )
    listResults = _flistpreflightBindMounts(config)
    assert len(listResults) == 1
    assert listResults[0].sLevel == "fail"
    assert sMissing in listResults[0].sMessage
    assert "vaibify.yml" in listResults[0].sRemediation


def test_preflight_passes_when_bind_mount_source_exists(tmp_path):
    """An existing bind-mount host path emits an ok."""
    from vaibify.cli.commandStart import _flistpreflightBindMounts
    sExisting = str(tmp_path)
    config = _fConfigForPreflight(
        listBindMounts=[{"host": sExisting, "container": "/data"}],
    )
    listResults = _flistpreflightBindMounts(config)
    assert listResults[0].sLevel == "ok"


def test_preflight_reports_all_missing_bind_mounts(tmp_path):
    """Every missing bind-mount path appears in the report."""
    from vaibify.cli.commandStart import _flistpreflightBindMounts
    sMissingOne = str(tmp_path / "a")
    sMissingTwo = str(tmp_path / "b")
    config = _fConfigForPreflight(listBindMounts=[
        {"host": sMissingOne, "container": "/x"},
        {"host": sMissingTwo, "container": "/y"},
    ])
    listResults = _flistpreflightBindMounts(config)
    listFails = [r for r in listResults if r.sLevel == "fail"]
    assert len(listFails) == 2
    saMessages = " ".join(r.sMessage for r in listFails)
    assert sMissingOne in saMessages
    assert sMissingTwo in saMessages


# -----------------------------------------------------------------------
# Happy path & combined fail
# -----------------------------------------------------------------------


def test_start_command_proceeds_when_all_preflight_passes():
    """All-ok pre-flight results invoke _fnStartContainer."""
    from vaibify.cli.commandStart import start
    from vaibify.cli.preflightResult import PreflightResult
    listOk = [
        PreflightResult(
            sName="docker-daemon", sLevel="ok", sMessage="ok",
        ),
        PreflightResult(sName="image", sLevel="ok", sMessage="ok"),
    ]
    mockStart = MagicMock()
    with patch(
        "vaibify.cli.commandStart.fconfigResolveProject",
        return_value=_fConfigForPreflight(),
    ), patch(
        "vaibify.cli.commandStart.fsDockerDir",
        return_value="/dk",
    ), patch(
        "vaibify.cli.commandStart.flistRunStartPreflight",
        return_value=listOk,
    ), patch(
        "vaibify.cli.commandStart._fnStartContainer", mockStart,
    ):
        result = CliRunner().invoke(start, [])
    assert result.exit_code == 0
    mockStart.assert_called_once()


def test_start_command_prints_warn_results_then_proceeds():
    """A warn-level result is printed and start continues."""
    from vaibify.cli.commandStart import start
    from vaibify.cli.preflightResult import PreflightResult
    listMixed = [
        PreflightResult(
            sName="container-name", sLevel="warn",
            sMessage="Removed stopped container 'foo' from prior session.",
        ),
    ]
    mockStart = MagicMock()
    with patch(
        "vaibify.cli.commandStart.fconfigResolveProject",
        return_value=_fConfigForPreflight(sProjectName="foo"),
    ), patch(
        "vaibify.cli.commandStart.fsDockerDir",
        return_value="/dk",
    ), patch(
        "vaibify.cli.commandStart.flistRunStartPreflight",
        return_value=listMixed,
    ), patch(
        "vaibify.cli.commandStart._fnStartContainer", mockStart,
    ):
        result = CliRunner().invoke(start, [])
    assert result.exit_code == 0
    assert "Removed stopped container" in result.output
    mockStart.assert_called_once()


def test_start_command_combined_fail_image_and_port():
    """Image-missing and port-conflict both surface in one report."""
    from vaibify.cli.commandStart import start
    from vaibify.cli.preflightResult import PreflightResult
    listFails = [
        PreflightResult(
            sName="image", sLevel="fail",
            sMessage="Image proj:latest not found",
            sRemediation="Run 'vaibify build' first.",
        ),
        PreflightResult(
            sName="port-8050", sLevel="fail",
            sMessage="Host port 8050 already in use",
            sRemediation="Edit vaibify.yml's `ports:` section.",
        ),
    ]
    with patch(
        "vaibify.cli.commandStart.fconfigResolveProject",
        return_value=_fConfigForPreflight(),
    ), patch(
        "vaibify.cli.commandStart.flistRunStartPreflight",
        return_value=listFails,
    ), patch(
        "vaibify.cli.commandStart._fnStartContainer",
    ) as mockStart:
        result = CliRunner().invoke(start, [])
    assert result.exit_code == 1
    assert "Image proj:latest not found" in result.output
    assert "Host port 8050 already in use" in result.output
    mockStart.assert_not_called()
