"""Static Dockerfile checks that enforce container-isolation invariants.

These tests parse the on-disk
``vaibify/containerImage/Dockerfile`` (and overlays) and
assert hardening properties that audit findings C1, C2, H1, and M5
require to hold for every build.
"""

import json
import os
import re

import pytest


_S_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_S_DOCKERFILE = os.path.join(
    _S_REPO_ROOT, "vaibify", "containerImage", "Dockerfile",
)
_S_DOCKERFILE_JUPYTER = os.path.join(
    _S_REPO_ROOT, "vaibify", "containerImage", "Dockerfile.jupyter",
)
_S_ENTRYPOINT = os.path.join(
    _S_REPO_ROOT, "vaibify", "containerImage", "entrypoint.sh",
)


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


def _fsJupyterStartScript():
    """Return the shell script the overlay's CMD hands to ``bash -c``.

    Parsed the way Docker parses it: backslash-newline continuations
    joined, then the exec-form array decoded as JSON.
    """
    sJoined = re.sub(r"\\\n", "", _fsReadJupyterDockerfile())
    sCmdLine = next(sLine for sLine in sJoined.splitlines()
                    if sLine.startswith("CMD "))
    listArguments = json.loads(sCmdLine[len("CMD "):])
    assert listArguments[:2] == ["/bin/bash", "-c"]
    return listArguments[2]


def _fsJupyterExecCommand():
    """Return the ``exec jupyter lab ...`` command, the one ps shows."""
    sScript = _fsJupyterStartScript()
    return sScript[sScript.index("exec jupyter lab"):]


def test_jupyter_overlay_generates_per_session_token():
    """A fresh token is generated and persisted to a mode-600 file."""
    sScript = _fsJupyterStartScript()
    assert "secrets.token_urlsafe" in sScript
    assert "install -m 0600 /dev/null /run/vaibify/jupyter-token" in sScript


def test_jupyter_reads_its_token_from_a_mode_600_config_file():
    """The config file is created 0600 before the token line is written."""
    sScript = _fsJupyterStartScript()
    sConfig = "/run/vaibify/jupyter_server_config.py"
    iCreate = sScript.index("install -m 0600 /dev/null " + sConfig)
    iWrite = sScript.index("> " + sConfig)
    assert iCreate < iWrite
    assert ('c.IdentityProvider.token = '
            'open("/run/vaibify/jupyter-token").read().strip()') in sScript
    assert "--config=" + sConfig in _fsJupyterExecCommand()


@pytest.mark.falsification
def test_jupyter_token_never_reaches_a_command_line():
    """Kills: passing the token to jupyter lab as an argument again.

    Arguments are visible to every process in the container (ps).
    """
    assert "token" not in _fsJupyterExecCommand().lower()


def test_jupyter_token_never_reaches_an_environment_variable():
    """No shell or ENV variable carries the token, exported or not."""
    sContent = _fsReadJupyterDockerfile()
    assert "JUPYTER_TOKEN" not in sContent
    assert "$(cat /run/vaibify/jupyter-token)" not in sContent
    assert not re.search(r"^\s*ENV\b.*token", sContent,
                         re.IGNORECASE | re.MULTILINE)


# -----------------------------------------------------------------------
# Entrypoint chown safety (M5)
# -----------------------------------------------------------------------


def test_entrypoint_chown_does_not_follow_symlinks():
    """Audit M5: the workspace chown must not dereference symlinks.

    The workspace-ownership migration is now mount-aware — it chowns the
    paths ``find`` reports (bind subtrees pruned) via ``xargs chown``
    rather than a blanket ``chown -R`` — but the symlink-safety property
    is unchanged: every chown carries ``--no-dereference``.
    """
    sContent = _fsReadEntrypoint()
    iIdx = sContent.find("chown --no-dereference")
    assert iIdx >= 0, (
        "expected the workspace migration to chown with --no-dereference"
    )
    # No actual command may reintroduce a blanket recursive chown (which
    # would traverse into a bind mount nested under the volume). Comments
    # that MENTION ``chown -R`` to explain why it was removed are fine, so
    # this checks command lines, not prose.
    listChownRCommands = [
        sLine for sLine in sContent.splitlines()
        if sLine.split("#", 1)[0].strip().startswith("chown -R")
    ]
    assert listChownRCommands == [], (
        "a blanket 'chown -R' would recurse into bind mounts; the "
        f"migration prunes nested mounts instead: {listChownRCommands}"
    )
