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
    fnStoreSecret,
    fnDeleteSecret,
    fbSecretExists,
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


# -----------------------------------------------------------------------
# fnStoreSecret
# -----------------------------------------------------------------------


def test_fnStoreSecret_keyring_calls_set_password():
    mockKeyring = MagicMock()
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        return_value=mockKeyring,
    ):
        fnStoreSecret("mytoken", "s3cret", "keyring")
    mockKeyring.set_password.assert_called_once_with(
        "vaibify", "mytoken", "s3cret",
    )


def test_fnStoreSecret_gh_auth_not_implemented():
    with pytest.raises(NotImplementedError):
        fnStoreSecret("gh_token", "val", "gh_auth")


def test_fnStoreSecret_docker_secret_not_implemented():
    with pytest.raises(NotImplementedError):
        fnStoreSecret("gh_token", "val", "docker_secret")


def test_fnStoreSecret_rejects_unknown_method():
    with pytest.raises(ValueError):
        fnStoreSecret("gh_token", "val", "bogus")


# -----------------------------------------------------------------------
# fnDeleteSecret
# -----------------------------------------------------------------------


def test_fnDeleteSecret_keyring_calls_delete_password():
    mockKeyring = MagicMock()
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        return_value=mockKeyring,
    ):
        fnDeleteSecret("mytoken", "keyring")
    mockKeyring.delete_password.assert_called_once_with(
        "vaibify", "mytoken",
    )


def test_fnDeleteSecret_suppresses_password_delete_error():
    from keyring.errors import PasswordDeleteError
    mockKeyring = MagicMock()
    mockKeyring.delete_password.side_effect = PasswordDeleteError("gone")
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        return_value=mockKeyring,
    ):
        fnDeleteSecret("mytoken", "keyring")


def test_fnDeleteSecret_reraises_other_exceptions():
    mockKeyring = MagicMock()
    mockKeyring.delete_password.side_effect = RuntimeError("kaboom")
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        return_value=mockKeyring,
    ):
        with pytest.raises(RuntimeError):
            fnDeleteSecret("mytoken", "keyring")


def test_fnDeleteSecret_gh_auth_not_implemented():
    with pytest.raises(NotImplementedError):
        fnDeleteSecret("gh_token", "gh_auth")


# -----------------------------------------------------------------------
# fbSecretExists
# -----------------------------------------------------------------------


def test_fbSecretExists_keyring_true_when_present():
    mockKeyring = MagicMock()
    mockKeyring.get_password.return_value = "a_real_token"
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        return_value=mockKeyring,
    ):
        assert fbSecretExists("mytoken", "keyring") is True


def test_fbSecretExists_keyring_false_when_absent():
    mockKeyring = MagicMock()
    mockKeyring.get_password.return_value = None
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        return_value=mockKeyring,
    ):
        assert fbSecretExists("mytoken", "keyring") is False


def test_fbSecretExists_keyring_false_when_backend_raises():
    with patch(
        "vaibify.config.secretManager._fnLoadKeyringModule",
        side_effect=ImportError("no keyring"),
    ):
        assert fbSecretExists("mytoken", "keyring") is False


def test_fbSecretExists_gh_auth_true_when_token_available():
    with patch(
        "vaibify.config.secretManager._fsRetrieveViaGhAuth",
        return_value="gho_xyz",
    ):
        assert fbSecretExists("ignored", "gh_auth") is True


def test_fbSecretExists_gh_auth_false_when_retrieval_fails():
    with patch(
        "vaibify.config.secretManager._fsRetrieveViaGhAuth",
        side_effect=RuntimeError("not logged in"),
    ):
        assert fbSecretExists("ignored", "gh_auth") is False


def test_fbSecretExists_docker_secret_true_when_file_present(tmp_path):
    import pathlib
    sName = "test_docker_secret_x"
    pathFake = pathlib.Path("/run/secrets") / sName

    class _FakePath:
        def __init__(self, sPath):
            self.sPath = sPath

        def exists(self):
            return self.sPath == str(pathFake)

    with patch(
        "vaibify.config.secretManager.Path",
        lambda sPath: _FakePath(sPath),
    ):
        assert fbSecretExists(sName, "docker_secret") is True


def test_fbSecretExists_docker_secret_false_when_missing():
    assert fbSecretExists("does_not_exist_xyz", "docker_secret") is False
