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
