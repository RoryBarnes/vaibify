"""Four small edges where a container-chosen string reaches the host.

* ``git add`` takes the file list after ``--``, so a name that begins
  with a dash is a path and never an option.
* A failed pull answers without the researcher's host path.
* A pull on the agent lane stops at a size cap and leaves no partial file.
* A download filename of any script, or one holding a quote, yields a
  well-formed ``Content-Disposition`` instead of a 500 or a split header.
"""

import os
from unittest.mock import patch
from urllib.parse import unquote

import pytest

from tests.testFileEndpointsAndMiddleware import (  # noqa: F401
    MockDockerTransfer,
    S_CONTAINER_ID,
    _fnConnectToContainer,
    clientHttp,
)
from vaibify.gui import syncDispatcher
from vaibify.gui.routes import downloadRoutes, fileRoutes


class _RecordingConnection:
    def __init__(self):
        self.listCommands = []

    def ftResultExecuteCommand(self, sContainerId, sCommand, **dictKeywords):
        self.listCommands.append(sCommand)
        return (0, "")


@pytest.mark.falsification
def testAFileNamedLikeAnOptionIsStagedAsAPath():
    """Kills: dropping the ``--`` that ends option parsing before the paths."""
    connection = _RecordingConnection()
    syncDispatcher.ftResultPushToGithub(
        connection, "cid", ["--force", "-A"], "msg", "/workspace/repo")
    syncDispatcher.ftResultAddFileToGithub(
        connection, "cid", "--force", "msg", "/workspace/repo")
    for sCommand in connection.listCommands:
        assert " add -- " in sCommand
        assert sCommand.index(" add -- ") < sCommand.index("--force")


@pytest.mark.falsification
def testAFailedPullDoesNotNameTheHostPath(
    clientHttp, tmp_path, monkeypatch,
):
    """Kills: answering a host write failure with ``str(error)``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _fnConnectToContainer(clientHttp)
    sMissingParent = os.path.join(str(tmp_path), "no-such-directory")
    with patch.object(
        MockDockerTransfer, "fbaFetchFile", return_value=b"bytes",
    ):
        responseHttp = clientHttp.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json={"sContainerPath": "/workspace/data.bin",
                  "sHostDestination": os.path.join(
                      sMissingParent, "data.bin")},
        )
    assert responseHttp.status_code == 500
    assert str(tmp_path) not in responseHttp.text
    assert "no-such-directory" not in responseHttp.text


@pytest.mark.falsification
def testACappedPullStopsAndLeavesNoPartialFile(tmp_path):
    """Kills: ignoring ``iMaxBytes`` while streaming the pulled file."""
    connection = MockDockerTransfer()
    connection._dictFiles["/workspace/big.bin"] = b"x" * 5000
    sDestination = str(tmp_path / "big.bin")
    with pytest.raises(fileRoutes.PullTooLargeError):
        fileRoutes._fsPullContainerFileToHost(
            connection, "cid", "/workspace/big.bin", sDestination,
            iMaxBytes=4000)
    assert not os.path.exists(sDestination)
    sLanded = fileRoutes._fsPullContainerFileToHost(
        connection, "cid", "/workspace/big.bin", sDestination,
        iMaxBytes=5000)
    assert os.path.getsize(sLanded) == 5000


@pytest.mark.falsification
def testANonLatinOneFilenameDownloadsWithItsExactName(clientHttp):
    """Kills: putting the raw filename into the header (500 on non-Latin-1)."""
    _fnConnectToContainer(clientHttp)
    with patch.object(
        MockDockerTransfer, "fbaFetchFile", return_value=b"data",
    ):
        responseHttp = clientHttp.get(
            f"/api/files/{S_CONTAINER_ID}/download/"
            "workspace/stepA/spectrum%E2%82%AC.dat")
    assert responseHttp.status_code == 200
    sHeader = responseHttp.headers["content-disposition"]
    sEncodedName = sHeader.split("filename*=UTF-8''")[1]
    assert unquote(sEncodedName) == "spectrum€.dat"


@pytest.mark.falsification
def testAQuoteInAFilenameCannotEndTheQuotedString():
    """Kills: interpolating the filename into the quoted form unescaped."""
    sHeader = downloadRoutes.fsBuildContentDisposition('a".txt; x="y')
    assert sHeader.startswith('attachment; filename="a\\".txt; x=\\"y"')
