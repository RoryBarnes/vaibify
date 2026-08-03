"""Tests for the bind-mount allowlist validator (audit finding H2)."""

import json
import os
import socket

import pytest

from vaibify.config.bindMountValidator import (
    BindMountValidationError,
    flistConfiguredDockerEndpoints,
    fnValidateBindMount,
    fnValidateBindMountList,
)


def _ftConfigureHome(monkeypatch, tmp_path):
    """Point $HOME at a fresh tmp_path and return it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_valid_path_under_home_is_accepted(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sHostPath = str(sHome / "datasets")
    os.makedirs(sHostPath)
    fnValidateBindMount({"host": sHostPath, "container": "/data"})


def test_path_outside_home_is_rejected(monkeypatch, tmp_path):
    _ftConfigureHome(monkeypatch, tmp_path)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": "/data", "container": "/data"})


def test_docker_socket_is_rejected(monkeypatch, tmp_path):
    _ftConfigureHome(monkeypatch, tmp_path)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": "/var/run/docker.sock", "container": "/sock"},
        )


def test_etc_prefix_is_rejected(monkeypatch, tmp_path):
    _ftConfigureHome(monkeypatch, tmp_path)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": "/etc/passwd", "container": "/p"})


def test_root_home_is_rejected(monkeypatch, tmp_path):
    _ftConfigureHome(monkeypatch, tmp_path)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": "/root", "container": "/r"})


def test_ssh_directory_is_rejected(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sSshDir = str(sHome / ".ssh")
    os.makedirs(sSshDir)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": sSshDir, "container": "/sshconfig"},
        )


def test_aws_directory_is_rejected(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sAwsDir = str(sHome / ".aws")
    os.makedirs(sAwsDir)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": sAwsDir, "container": "/awsconfig"},
        )


def test_gh_config_directory_is_rejected(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sGhDir = str(sHome / ".config" / "gh")
    os.makedirs(sGhDir)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": sGhDir, "container": "/ghconfig"},
        )


def test_double_dot_segments_are_rejected(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sUnsafe = str(sHome) + "/projects/../.ssh"
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": sUnsafe, "container": "/x"})


def test_symlink_into_etc_is_rejected(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sBadLink = str(sHome / "etc_link")
    os.symlink("/etc", sBadLink)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": sBadLink, "container": "/x"})


def test_missing_host_field_is_rejected(monkeypatch, tmp_path):
    _ftConfigureHome(monkeypatch, tmp_path)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"container": "/x"})


def test_list_validator_accepts_empty_list():
    fnValidateBindMountList([])


def test_list_validator_aborts_on_first_violation(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sGoodPath = str(sHome / "ok")
    os.makedirs(sGoodPath)
    listMounts = [
        {"host": sGoodPath, "container": "/ok"},
        {"host": "/etc", "container": "/etc"},
    ]
    with pytest.raises(BindMountValidationError):
        fnValidateBindMountList(listMounts)


def test_project_repo_root_is_accepted(monkeypatch, tmp_path):
    """A path under the explicit project-repo root is allowed even
    if it sits outside the user's home directory."""
    sOtherRoot = tmp_path / "elsewhere"
    sOtherRoot.mkdir()
    sRepoPath = sOtherRoot / "repo"
    sRepoPath.mkdir()
    sChild = sRepoPath / "data"
    sChild.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"))
    fnValidateBindMount(
        {"host": str(sChild), "container": "/data"},
        sProjectRepoPath=str(sRepoPath),
    )


def test_ssh_directory_via_symlinked_home_is_rejected(
    monkeypatch, tmp_path,
):
    """Audit H2: a system whose $HOME resolves through a symlink must
    still trip the home-relative denylist (e.g. macOS ``/Users/foo``
    backed by ``/private/...``)."""
    sRealHome = tmp_path / "real_home"
    sRealHome.mkdir()
    sSymHome = tmp_path / "sym_home"
    os.symlink(str(sRealHome), str(sSymHome))
    sSshDir = sRealHome / ".ssh"
    sSshDir.mkdir()
    monkeypatch.setenv("HOME", str(sSymHome))
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": str(sSshDir), "container": "/sshconfig"},
        )


@pytest.mark.falsification
def test_mounting_home_itself_is_rejected(monkeypatch, tmp_path):
    """Mounting $HOME exposes ~/.ssh et al. and must be denied.

    The denylist blocked ~/.ssh directly but allowed $HOME, whose
    read-write mount hands the container every credential beneath it.
    A denylist that only checks the descendant direction misses this
    entirely.

    Kills: In bindMountValidator._fbPathsOverlap, drop the
    ``sSecond.startswith(sFirst + os.sep)`` (ancestor) direction so
    only descendant overlaps are caught.
    """
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    (sHome / ".ssh").mkdir()
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": str(sHome), "container": "/host"})


def test_mounting_config_parent_of_gh_is_rejected(monkeypatch, tmp_path):
    """~/.config is an ancestor of the denied ~/.config/gh, so denied."""
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sConfig = sHome / ".config"
    sConfig.mkdir()
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": str(sConfig), "container": "/config"},
        )


def test_granular_config_sibling_is_still_allowed(monkeypatch, tmp_path):
    """A specific ~/.config/myapp mount is fine — only the parent is denied."""
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sApp = sHome / ".config" / "myapp"
    sApp.mkdir(parents=True)
    fnValidateBindMount({"host": str(sApp), "container": "/app"})


@pytest.mark.parametrize("sDenied", [".gnupg", ".docker", ".kube"])
def test_newly_denied_credential_dirs_are_rejected(
    monkeypatch, tmp_path, sDenied,
):
    """~/.gnupg, ~/.docker (registry creds), ~/.kube were never listed."""
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sDir = sHome / sDenied
    sDir.mkdir()
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": str(sDir), "container": "/x"})


def test_mounting_var_run_parent_of_docker_sock_is_rejected(
    monkeypatch, tmp_path,
):
    """Mounting /var/run exposes the docker socket beneath it."""
    _ftConfigureHome(monkeypatch, tmp_path)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": "/var/run", "container": "/vr"})


def test_container_target_must_be_absolute(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sHostPath = str(sHome / "data")
    os.makedirs(sHostPath)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": sHostPath, "container": "relative/path"},
        )


def test_container_target_rejects_traversal(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sHostPath = str(sHome / "data")
    os.makedirs(sHostPath)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount(
            {"host": sHostPath, "container": "/workspace/../etc"},
        )


def test_container_target_missing_is_rejected(monkeypatch, tmp_path):
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sHostPath = str(sHome / "data")
    os.makedirs(sHostPath)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": sHostPath})


@pytest.mark.falsification
def test_journal_directory_mount_is_rejected_in_every_direction(
    monkeypatch, tmp_path,
):
    """Design §13 case 39: the quarantine markers are bind-mount-denied.

    Every agent inside a container runs as the same UID that owns
    ``~/.vaibify/journal`` on the host, so any mount that exposes the
    journal — the directory itself, an ancestor, a descendant, or a
    symlink resolving into it — would let a compromised agent delete a
    quarantine marker and un-quarantine a container whose past
    operations were never proven settled.

    Kills: in bindMountValidator._LIST_HOME_RELATIVE_DENY_PREFIXES,
    drop the ".vaibify/journal" entry (with its comment block), so a
    mount of ~/.vaibify passes the home-allowlist untouched.
    """
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sJournalDirectory = str(sHome / ".vaibify" / "journal")
    os.makedirs(sJournalDirectory)
    listHostPaths = [
        sJournalDirectory,
        str(sHome / ".vaibify"),
        os.path.join(sJournalDirectory, "demo.operationJournal"),
    ]
    for sHostPath in listHostPaths:
        with pytest.raises(BindMountValidationError):
            fnValidateBindMount({"host": sHostPath, "container": "/mnt"})
    sSymlinkPath = str(sHome / "innocuousData")
    os.symlink(sJournalDirectory, sSymlinkPath)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": sSymlinkPath, "container": "/mnt"})


def test_control_socket_directory_mount_is_rejected_in_every_direction(
    monkeypatch, tmp_path,
):
    """Design §6b/§14: the host control sockets are bind-mount-denied.

    The control plane is host-only by construction: every agent inside
    a container runs as the same UID that owns ``~/.vaibify/control``
    on the host, so any mount exposing it — the directory itself, an
    ancestor, a descendant, or a symlink resolving into it — would let
    a compromised agent connect to the peer-authenticated socket and
    drive reconcile/force-abandon/break-glass from inside.

    Kills: in bindMountValidator._LIST_HOME_RELATIVE_DENY_PREFIXES,
    drop the ".vaibify/control" entry (with its comment block), so a
    mount of the socket directory passes the home-allowlist untouched.
    """
    sHome = _ftConfigureHome(monkeypatch, tmp_path)
    sControlDirectory = str(sHome / ".vaibify" / "control")
    os.makedirs(sControlDirectory)
    listHostPaths = [
        sControlDirectory,
        str(sHome / ".vaibify"),
        os.path.join(sControlDirectory, "hub-8123.controlSocket"),
    ]
    for sHostPath in listHostPaths:
        with pytest.raises(BindMountValidationError):
            fnValidateBindMount({"host": sHostPath, "container": "/mnt"})
    sSymlinkPath = str(sHome / "innocuousSockets")
    os.symlink(sControlDirectory, sSymlinkPath)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": sSymlinkPath, "container": "/mnt"})


# ---------------------------------------------------------------------
# The daemon endpoint. Denying the string "/var/run/docker.sock" was
# never enough: on Colima, Rancher and rootless installs the live
# endpoint sits INSIDE $HOME, where the general home allow-list admitted
# it. Verified against a live socket before the fix --
# ~/.colima/default/docker.sock -> /var/run/docker.sock was ACCEPTED,
# which hands the container the host daemon and, through it, the host.
# ---------------------------------------------------------------------

def _fsWriteDockerContext(sHome, sContextName, sEndpoint):
    """Write a Docker context meta file naming an endpoint, as Docker does."""
    sMetaDirectory = os.path.join(
        sHome, ".docker", "contexts", "meta", sContextName,
    )
    os.makedirs(sMetaDirectory, exist_ok=True)
    sMetaPath = os.path.join(sMetaDirectory, "meta.json")
    with open(sMetaPath, "w", encoding="utf-8") as fileMeta:
        json.dump(
            {
                "Name": sContextName,
                "Endpoints": {"docker": {"Host": sEndpoint}},
            },
            fileMeta,
        )
    return sMetaPath


@pytest.mark.falsification
def test_configured_daemon_endpoint_in_home_is_rejected(
    monkeypatch, tmp_path,
):
    """The endpoint Docker names is denied, and so is every ancestor.

    The exact shape that was exploitable: a socket under $HOME, reached
    through a context file rather than through a hard-coded name. The
    ancestor cases matter as much as the socket -- mounting the parent
    directory grants the socket just as completely, which is why the
    overlap check runs in both directions.

    Kills: dropping the _fnRejectDaemonSocket call from
    fnValidateBindMount.
    """
    sHome = str(_ftConfigureHome(monkeypatch, tmp_path))
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    sSocketDirectory = os.path.join(sHome, ".someruntime", "default")
    os.makedirs(sSocketDirectory)
    sSocketPath = os.path.join(sSocketDirectory, "docker.sock")
    _fsWriteDockerContext(sHome, "someruntime", "unix://" + sSocketPath)

    for sHostPath in (
        sSocketPath,
        sSocketDirectory,
        os.path.join(sHome, ".someruntime"),
    ):
        with pytest.raises(BindMountValidationError):
            fnValidateBindMount({"host": sHostPath, "container": "/mnt"})


@pytest.mark.falsification
def test_docker_host_environment_endpoint_is_rejected(monkeypatch, tmp_path):
    """DOCKER_HOST names the live daemon as authoritatively as a context.

    A separate source, so a separate test: an install can point at its
    daemon through the environment with no context file written at all,
    and reading only the on-disk contexts would leave that endpoint
    mountable.

    Kills: dropping the DOCKER_HOST branch from
    _flistConfiguredDockerEndpoints.
    """
    sHome = str(_ftConfigureHome(monkeypatch, tmp_path))
    sSocketPath = os.path.join(sHome, "runtime", "engine.sock")
    os.makedirs(os.path.dirname(sSocketPath))
    monkeypatch.setenv("DOCKER_HOST", "unix://" + sSocketPath)
    with pytest.raises(BindMountValidationError):
        fnValidateBindMount({"host": sSocketPath, "container": "/mnt"})


@pytest.mark.falsification
def test_any_unix_socket_is_rejected_even_when_unconfigured(
    monkeypatch, tmp_path,
):
    """The fail-closed layer: a socket is refused without being named.

    Layer one can only deny endpoints the configuration mentions. A
    runtime nobody anticipated, or a daemon whose config was never
    written, would slip past it -- and the whole reason this fix exists
    is that a name-matched list did not keep up. Refusing by FILE TYPE
    needs no list and no foresight.

    Kills: removing the _fbIsUnixSocket branch from
    _fnRejectDaemonSocket.
    """
    sHome = str(_ftConfigureHome(monkeypatch, tmp_path))
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    sSocketPath = os.path.join(sHome, "unnamed.sock")
    socketProbe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sOriginalDirectory = os.getcwd()
    try:
        # Bound through a relative name: AF_UNIX paths are capped near
        # 104 bytes and a pytest tmp_path alone can exceed it.
        os.chdir(sHome)
        socketProbe.bind("unnamed.sock")
        with pytest.raises(BindMountValidationError):
            fnValidateBindMount({"host": sSocketPath, "container": "/mnt"})
    finally:
        os.chdir(sOriginalDirectory)
        socketProbe.close()


def test_ordinary_directories_are_still_accepted(monkeypatch, tmp_path):
    """The negative control for all three tests above.

    A validator that refused everything would pass each of them while
    making the product unusable. A data directory under $HOME is the
    ordinary case and must survive.
    """
    sHome = str(_ftConfigureHome(monkeypatch, tmp_path))
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    _fsWriteDockerContext(
        sHome, "someruntime",
        "unix://" + os.path.join(sHome, ".someruntime", "docker.sock"),
    )
    sHostPath = os.path.join(sHome, "datasets", "survey")
    os.makedirs(sHostPath)
    fnValidateBindMount({"host": sHostPath, "container": "/data"})


def test_a_tcp_endpoint_yields_no_path_to_deny(monkeypatch, tmp_path):
    """A TCP daemon is out of this validator's reach, and says so.

    Recorded as a fact rather than left to be rediscovered: there is no
    path to refuse for `tcp://`, so bind-mount validation contributes
    nothing against it and the container's network isolation is what
    answers. A reader who assumes "no socket mounted" means "no daemon
    reachable" is wrong, and this is where that is written down.
    """
    _ftConfigureHome(monkeypatch, tmp_path)
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:2375")
    assert flistConfiguredDockerEndpoints() == []
