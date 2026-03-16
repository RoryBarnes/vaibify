"""Tests for vaibify.config.secretManager credential abstraction."""

import os
import stat
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from vaibify.config.secretManager import (
    fsRetrieveSecret,
    fsMountSecret,
    fnCleanupSecretFiles,
    flistPrepareDockerSecretArgs,
)


def test_fsRetrieveSecret_docker_secret_method(tmp_path):
    sSecretName = "test_token"
    sSecretValue = "ghp_faketoken123"

    pathFakeSecret = tmp_path / sSecretName
    pathFakeSecret.write_text(f"  {sSecretValue}  \n")

    with patch(
        "vaibify.config.secretManager._fsRetrieveViaDockerSecret"
    ) as mockRetrieve:
        mockRetrieve.return_value = sSecretValue

        sResult = fsRetrieveSecret(sSecretName, "docker_secret")

    assert sResult == sSecretValue
    mockRetrieve.assert_called_once_with(sSecretName)


def test_fsRetrieveSecret_gh_auth_method():
    sExpectedToken = "gho_abc123xyz"

    with patch(
        "vaibify.config.secretManager._fsRetrieveViaGhAuth"
    ) as mockGhAuth:
        mockGhAuth.return_value = sExpectedToken

        sResult = fsRetrieveSecret("github_token", "gh_auth")

    assert sResult == sExpectedToken
    mockGhAuth.assert_called_once()


def test_fsMountSecret_creates_file_with_correct_permissions():
    sSecretValue = "supersecretvalue"

    with patch(
        "vaibify.config.secretManager.fsRetrieveSecret"
    ) as mockRetrieve:
        mockRetrieve.return_value = sSecretValue

        sFilePath = fsMountSecret("test_secret", "gh_auth")

    try:
        assert os.path.isfile(sFilePath)
        iFileMode = os.stat(sFilePath).st_mode
        iExpectedPermissions = stat.S_IRUSR | stat.S_IWUSR
        iActualPermissions = stat.S_IMODE(iFileMode)
        assert iActualPermissions == iExpectedPermissions

        with open(sFilePath, "r") as fileHandle:
            sContents = fileHandle.read()
        assert sContents == sSecretValue
    finally:
        os.remove(sFilePath)


def test_fnCleanupSecretFiles_removes_files(tmp_path):
    pathFileA = tmp_path / "secret_a.tmp"
    pathFileB = tmp_path / "secret_b.tmp"
    pathFileA.write_text("aaa")
    pathFileB.write_text("bbb")

    listPaths = [str(pathFileA), str(pathFileB)]
    assert pathFileA.exists()
    assert pathFileB.exists()

    fnCleanupSecretFiles(listPaths)

    assert not pathFileA.exists()
    assert not pathFileB.exists()


def test_fnCleanupSecretFiles_ignores_missing_files(tmp_path):
    sNonexistent = str(tmp_path / "does_not_exist.tmp")
    fnCleanupSecretFiles([sNonexistent])


def test_flistPrepareDockerSecretArgs():
    listSecrets = [
        {"name": "github_token", "method": "gh_auth"},
        {"name": "zenodo_token", "method": "keyring"},
    ]

    with patch(
        "vaibify.config.secretManager.fsMountSecret"
    ) as mockMount:
        mockMount.side_effect = [
            "/tmp/vc_secret_github_token_abc.tmp",
            "/tmp/vc_secret_zenodo_token_def.tmp",
        ]

        listArgs = flistPrepareDockerSecretArgs(listSecrets)

    assert len(listArgs) == 4
    assert listArgs[0] == "-v"
    assert "github_token" in listArgs[1]
    assert ":ro" in listArgs[1]
    assert listArgs[2] == "-v"
    assert "zenodo_token" in listArgs[3]
    assert ":ro" in listArgs[3]


def test_fsRetrieveSecret_rejects_unknown_method():
    with pytest.raises(ValueError, match="Unknown secret method"):
        fsRetrieveSecret("any_name", "env_var")
