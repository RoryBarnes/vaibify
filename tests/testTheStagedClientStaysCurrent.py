"""The container-side Zenodo client is restaged when the host's differs.

The image COPYs ``zenodoClient.py`` and ``credentialRedactor.py``
into ``/usr/share/vaibify`` at BUILD time, and the dispatcher's
generated archive script imports symbols from the HOST's version of
that module. A 14-day-old container met a script naming
``fdictDescribeLocalFileForDeposit`` -- added since its build -- and
Make Permanent died with an ImportError from inside the container
(live, 2026-09-16). Two copies synchronized only at image build is
the divergence-bug shape this repo keeps refinding; the archive run
now hash-compares and restages before executing.
"""

import hashlib
from pathlib import Path

import pytest

import vaibify.reproducibility as moduleReproducibility
from vaibify.gui import syncDispatcher


def _fbaHostCopy(sName):
    return (
        Path(moduleReproducibility.__file__).parent / sName
    ).read_bytes()


class _StubStagingConnection:
    """Records hash probes, writes, and the exec, in call order."""

    def __init__(self, dictShaByPath):
        self._dictShaByPath = dictShaByPath
        self.listCalls = []

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        self.listCalls.append(("hash", sPath))
        return self._dictShaByPath.get(sPath, "")

    def fnWriteFile(self, sContainerId, sPath, baContent,
                    iMode=None, iUid=None, iGid=None):
        self.listCalls.append(("write", sPath, baContent))

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCalls.append(("execute", sCommand))
        return (0, "Published: 1")


@pytest.mark.falsification
def test_a_stale_staged_client_is_rewritten_before_the_upload():
    """Drifted container copies are replaced with the host's bytes.

    Kills: dropping ``fnEnsureStagedClientCurrent`` from
    ``ftResultArchiveToZenodo`` -- the shipped shape, in which the
    archive script ran against whatever the image build froze and a
    researcher's publish died on an ImportError the dispatcher
    created.
    """
    connectionStub = _StubStagingConnection({
        "/usr/share/vaibify/zenodoClient.py": "0" * 64,
        "/usr/share/vaibify/credentialRedactor.py": "0" * 64,
    })
    iExit, sOut = syncDispatcher.ftResultArchiveToZenodo(
        connectionStub, "cid-1", "sandbox", ["/workspace/repo/a.csv"],
        {"sTitle": "T", "listCreators": [{"sName": "N"}]},
    )
    assert iExit == 0
    listWrites = [t for t in connectionStub.listCalls
                  if t[0] == "write"]
    assert sorted(t[1] for t in listWrites) == [
        "/usr/share/vaibify/credentialRedactor.py",
        "/usr/share/vaibify/zenodoClient.py",
    ], connectionStub.listCalls
    for tWrite in listWrites:
        sName = tWrite[1].rsplit("/", 1)[1]
        assert tWrite[2] == _fbaHostCopy(sName), (
            f"{sName} was staged with bytes that are not the host's"
        )
    # The writes land BEFORE the exec that imports them.
    listKinds = [t[0] for t in connectionStub.listCalls]
    assert listKinds.index("execute") > max(
        iIndex for iIndex, sKind in enumerate(listKinds)
        if sKind == "write"
    )


def test_a_current_staged_client_is_left_alone():
    """Matching hashes mean two typed reads and zero writes."""
    connectionStub = _StubStagingConnection({
        "/usr/share/vaibify/" + sName:
            hashlib.sha256(_fbaHostCopy(sName)).hexdigest()
        for sName in ("zenodoClient.py", "credentialRedactor.py")
    })
    syncDispatcher.ftResultArchiveToZenodo(
        connectionStub, "cid-1", "sandbox", ["/workspace/repo/a.csv"],
        {"sTitle": "T", "listCreators": [{"sName": "N"}]},
    )
    assert [t for t in connectionStub.listCalls
            if t[0] == "write"] == [], (
        "a current staged client was rewritten anyway; every archive "
        "run would pay two writes for nothing"
    )
