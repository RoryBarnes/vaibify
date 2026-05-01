"""`vaibify status` Docker-context line tests (F-E-02)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner


def _fconfigForStatus():
    """Return a stub config for `vaibify status`."""
    return SimpleNamespace(sProjectName="ctxproj")


def _fdockerClient():
    """Return a docker SDK client stub with no images/containers/volumes."""
    dockerClient = MagicMock()
    dockerClient.ping.return_value = True
    dockerClient.images.get.side_effect = Exception("not found")
    dockerClient.volumes.get.side_effect = Exception("not found")
    dockerClient.containers.list.return_value = []
    return dockerClient


def test_status_prints_active_docker_context_when_known():
    """`vaibify status` prints the active context when one is reported."""
    from vaibify.cli.commandStatus import status
    with patch(
        "vaibify.cli.commandStatus.fbDockerAvailable", return_value=True,
    ), patch(
        "vaibify.cli.commandStatus.fconfigResolveProject",
        return_value=_fconfigForStatus(),
    ), patch(
        "docker.from_env",
        return_value=_fdockerClient(),
    ), patch(
        "vaibify.docker.dockerContext.fsActiveDockerContext",
        return_value="colima",
    ):
        result = CliRunner().invoke(status, [])
    assert result.exit_code == 0
    assert "Docker context: colima" in result.output


def test_status_omits_context_line_when_unknown():
    """No context line is printed when the helper returns ''."""
    from vaibify.cli.commandStatus import status
    with patch(
        "vaibify.cli.commandStatus.fbDockerAvailable", return_value=True,
    ), patch(
        "vaibify.cli.commandStatus.fconfigResolveProject",
        return_value=_fconfigForStatus(),
    ), patch(
        "docker.from_env",
        return_value=_fdockerClient(),
    ), patch(
        "vaibify.docker.dockerContext.fsActiveDockerContext",
        return_value="",
    ):
        result = CliRunner().invoke(status, [])
    assert result.exit_code == 0
    assert "Docker context" not in result.output
