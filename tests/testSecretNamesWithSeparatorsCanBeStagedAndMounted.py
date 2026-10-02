"""A per-remote secret slot name can be staged to a file and mounted.

The validator admits ``service:owner/repo`` slot names, but the staging
step put the raw name into a temporary file's name (a "/" there is a
directory that does not exist) and the mount used ``-v host:target:ro``,
whose colon-separated fields a colon in the target corrupts. Both failed
only for the names the validator exists to allow.
"""

import csv
import io
import os
import stat

import pytest

from vaibify.config import secretManager

S_SLOT_NAME = "github_token:octo-org/data.repo"


@pytest.fixture
def pathStagingDirectory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        secretManager, "_fsGetTempDirectory", lambda: str(tmp_path))
    return tmp_path


@pytest.mark.falsification
def testASlotNameWithSeparatorsIsStagedToAPrivateFile(pathStagingDirectory):
    """Kills: using the raw slot name as part of a temporary file name."""
    sPath = secretManager.fsMaterializeSecretValue(S_SLOT_NAME, "value")
    assert os.path.dirname(sPath) == str(pathStagingDirectory)
    assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
    with open(sPath, encoding="utf-8") as fileStaged:
        assert fileStaged.read() == "value"


@pytest.mark.falsification
def testASlotNameWithAColonMountsAsOneTargetField():
    """Kills: building the mount as a colon-separated -v argument."""
    listArguments = secretManager.flistBuildSecretMountArguments(
        "/home/user/.vaibify/tmp/vc_secret_x.tmp", S_SLOT_NAME)
    assert listArguments[0] == "--mount"
    listFields = next(csv.reader(io.StringIO(listArguments[1])))
    assert listFields == [
        "type=bind", "source=/home/user/.vaibify/tmp/vc_secret_x.tmp",
        "target=/run/secrets/" + S_SLOT_NAME, "readonly",
    ]


def testAHostPathWithACommaStaysOneField():
    listArguments = secretManager.flistBuildSecretMountArguments(
        "/home/a,b/vc_secret_x.tmp", "gh_token")
    listFields = next(csv.reader(io.StringIO(listArguments[1])))
    assert listFields[1] == "source=/home/a,b/vc_secret_x.tmp"
    assert len(listFields) == 4
