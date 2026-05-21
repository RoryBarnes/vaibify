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


# -----------------------------------------------------------------------
# Jupyter overlay (H1)
# -----------------------------------------------------------------------


def test_jupyter_overlay_binds_loopback_only():
    """JupyterLab must not listen on 0.0.0.0; loopback only."""
    sContent = _fsReadJupyterDockerfile()
    assert "--ip=0.0.0.0" not in sContent
    assert "--ip=127.0.0.1" in sContent


def test_jupyter_overlay_does_not_allow_root():
    """``--allow-root`` must not be passed; the image USER is now unpriv."""
    sContent = _fsReadJupyterDockerfile()
    assert "--allow-root" not in sContent


def test_jupyter_overlay_generates_per_session_token():
    """A fresh token is generated and persisted to a mode-600 file."""
    sContent = _fsReadJupyterDockerfile()
    assert "secrets.token_urlsafe" in sContent
    assert "0600" in sContent
    assert "ServerApp.token" in sContent


# -----------------------------------------------------------------------
# Entrypoint chown safety (M5)
# -----------------------------------------------------------------------


def test_entrypoint_chown_does_not_follow_symlinks():
    """Audit M5: recursive chown must not dereference symlinks."""
    sContent = _fsReadEntrypoint()
    iIdx = sContent.find("chown -R")
    assert iIdx >= 0, "expected recursive chown in entrypoint"
    sChownLine = sContent[iIdx:iIdx + 200]
    assert "--no-dereference" in sChownLine
