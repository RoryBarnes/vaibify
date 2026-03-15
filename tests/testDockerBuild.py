"""Integration tests for imageBuilder that require a running Docker daemon.

All tests in this file are marked with @pytest.mark.docker and are
skipped by default. Run with: pytest -m docker
"""

import subprocess

import pytest

from vaibcask.docker.imageBuilder import (
    fnBuildBase,
    fnApplyOverlay,
    fbImageExists,
)
from vaibcask.config.projectConfig import (
    ProjectConfig,
    FeaturesConfig,
)


def _fbDockerAvailable():
    """Return True if the Docker daemon is reachable."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return resultProcess.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


bDockerRunning = _fbDockerAvailable()
sSkipReason = "Docker daemon is not available"


@pytest.mark.docker
@pytest.mark.skipif(not bDockerRunning, reason=sSkipReason)
def test_fnBuildBase_creates_image(tmp_path):
    sDockerDir = str(tmp_path / "docker")
    (tmp_path / "docker").mkdir()

    sDockerfile = (
        "ARG BASE_IMAGE=ubuntu:24.04\n"
        "FROM ${BASE_IMAGE}\n"
        "ARG PYTHON_VERSION=3.12\n"
        "ARG CONTAINER_USER=researcher\n"
        "ARG WORKSPACE_ROOT=/workspace\n"
        "ARG INSTALL_LATEX=false\n"
        "ARG INSTALL_X11=true\n"
        "ARG PACKAGE_MANAGER=pip\n"
        "ARG VC_PROJECT_NAME=test_docker_build\n"
        "RUN echo 'base image built'\n"
    )
    (tmp_path / "docker" / "Dockerfile").write_text(sDockerfile)

    config = ProjectConfig(
        sProjectName="dctest_base",
        features=FeaturesConfig(bGpu=False),
    )

    fnBuildBase(config, sDockerDir, bNoCache=True)

    assert fbImageExists("dctest_base:base")

    subprocess.run(
        ["docker", "rmi", "dctest_base:base"],
        capture_output=True,
    )


@pytest.mark.docker
@pytest.mark.skipif(not bDockerRunning, reason=sSkipReason)
def test_fnApplyOverlay_creates_tagged_image(tmp_path):
    sDockerDir = str(tmp_path / "docker")
    (tmp_path / "docker").mkdir()

    sBaseDockerfile = (
        "FROM ubuntu:24.04\n"
        "RUN echo 'base'\n"
    )
    (tmp_path / "docker" / "Dockerfile").write_text(sBaseDockerfile)

    subprocess.run(
        [
            "docker", "build",
            "-f", str(tmp_path / "docker" / "Dockerfile"),
            "-t", "dctest_overlay:base",
            str(tmp_path / "docker"),
        ],
        capture_output=True,
        check=True,
    )

    sJupyterDockerfile = (
        "ARG BASE_IMAGE=dctest_overlay:base\n"
        "FROM ${BASE_IMAGE}\n"
        "RUN echo 'jupyter overlay'\n"
    )
    (tmp_path / "docker" / "Dockerfile.jupyter").write_text(
        sJupyterDockerfile
    )

    fnApplyOverlay(
        "dctest_overlay", "jupyter", sDockerDir, "base",
    )

    assert fbImageExists("dctest_overlay:jupyter")

    subprocess.run(
        ["docker", "rmi", "dctest_overlay:jupyter", "dctest_overlay:base"],
        capture_output=True,
    )
