"""Static Dockerfile checks that enforce container-isolation invariants.

These tests parse the on-disk ``docker/Dockerfile`` (and overlays) and
assert hardening properties that audit findings C1, C2, H1, and M5
require to hold for every build.
"""

import os


_S_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S_DOCKERFILE = os.path.join(_S_REPO_ROOT, "docker", "Dockerfile")
_S_DOCKERFILE_JUPYTER = os.path.join(
    _S_REPO_ROOT, "docker", "Dockerfile.jupyter",
)
_S_ENTRYPOINT = os.path.join(_S_REPO_ROOT, "docker", "entrypoint.sh")


def _fsReadDockerfile():
    """Return the base Dockerfile contents as text."""
    with open(_S_DOCKERFILE, "r") as fileHandle:
        return fileHandle.read()


def _fsReadJupyterDockerfile():
    """Return the Jupyter overlay Dockerfile contents as text."""
    with open(_S_DOCKERFILE_JUPYTER, "r") as fileHandle:
        return fileHandle.read()


def _fsReadEntrypoint():
    """Return the entrypoint shell script contents as text."""
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        return fileHandle.read()


def test_dockerfile_does_not_install_sudo():
    """The container image must not ship the sudo binary at all."""
    sContent = _fsReadDockerfile()
    assert "sudo \\" not in sContent
    assert " sudo\n" not in sContent
    assert "install sudo" not in sContent


def test_dockerfile_has_no_nopasswd_sudoers_dropin():
    """No passwordless sudoers entry may be written for any user."""
    sContent = _fsReadDockerfile()
    assert "NOPASSWD" not in sContent
    assert "/etc/sudoers.d/" not in sContent


def test_dockerfile_pins_default_user_to_container_user():
    """``USER ${CONTAINER_USER}`` must appear so docker exec is unpriv."""
    sContent = _fsReadDockerfile()
    assert "USER ${CONTAINER_USER}" in sContent
