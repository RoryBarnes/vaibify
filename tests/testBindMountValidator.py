"""Tests for the bind-mount allowlist validator (audit finding H2)."""

import os

import pytest

from vaibify.config.bindMountValidator import (
    BindMountValidationError,
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
