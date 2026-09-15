"""The project deposit, too, is finished when the ARCHIVE agrees.

The environment archive gained this check first; the project deposit
is the lane a researcher actually reaches every time they archive
their outputs, and it had none. The asymmetry was the defect: a
truncated upload, a dropped byte range, or a stored object that is
not the one sent would leave the deposit record saying exactly what
it says now, and the L2/L3 cells would go green over an archive that
cannot satisfy them.

This lane runs INSIDE the container, so the check cannot import the
host package. It shares one implementation with the host lane via
``zenodoClient``, the only module staged on both sides of that wall.

The tests execute the real generated script — the one that is
base64-encoded and exec'd in the container — against a fake Zenodo.
Asserting on substrings of the template would prove the text and not
the behavior, and this file exists because a check that is merely
written down is the failure mode under review.
"""

import base64
import io
import re
import sys
import types
from contextlib import redirect_stdout

import pytest

from vaibify.gui import syncDispatcher
from vaibify.reproducibility import zenodoClient


_S_NAME = "results.csv"
_BA_CONTENT = b"a,b\n1,2\n"
# md5 of _BA_CONTENT, computed by the code under test's own helper at
# call time rather than pinned here — a pinned constant would agree
# with a hasher that had stopped reading the file.


def _fsBuildScript(listPaths, iParent=0):
    """Decode the script the dispatcher would actually run."""
    sCommand = syncDispatcher._fsBuildZenodoArchiveCommand(
        "https://example.invalid/api", "zenodo_token_sandbox",
        listPaths, {"title": "A project", "creators": [{"name": "X"}]},
        "cid", iParent,
    )
    sEncoded = re.search(r"b64decode\('([^']+)'\)", sCommand).group(1)
    return base64.b64decode(sEncoded).decode()


class _FakeZenodo:
    """A Zenodo that answers what the test tells it to."""

    def __init__(self, dictServed=None, **kwargs):
        self.listPhases = []
        self.dictServed = dictServed
        self.dictUploaded = {}

    def fdictCreateDraft(self, dictMetadata):
        self.listPhases.append("created")
        return {"id": 42, "links": {"bucket": "https://example/b"}}

    def fdictGetNewVersionDraft(self, iParent):
        self.listPhases.append("newversion")
        return {"id": 42, "links": {"bucket": "https://example/b"}}

    def fnClearDraftFiles(self, iDepositId):
        self.listPhases.append("cleared")

    def fnSetMetadata(self, iDepositId, dictMetadata):
        self.listPhases.append("metadata")

    def fnUploadToBucket(self, sBucketUrl, sPath):
        self.listPhases.append("upload")
        baContent = open(sPath, "rb").read()
        self.dictUploaded[sPath.rsplit("/", 1)[-1]] = baContent

    def fdictGetDeposit(self, iDepositId):
        self.listPhases.append("asked")
        if self.dictServed is not None:
            return self.dictServed
        return {"files": [
            {"key": sKey, "filesize": len(ba),
             "checksum": "md5:" + _fsMd5(ba)}
            for sKey, ba in self.dictUploaded.items()
        ]}

    def fdictPublishDraft(self, iDepositId):
        self.listPhases.append("published")
        return {"doi": "10.5281/zenodo.1", "conceptdoi": "",
                "links": {"html": "https://example/r/1"}}

    def fnDeleteDraft(self, iDepositId):
        self.listPhases.append("discarded")


def _fsMd5(baContent):
    import hashlib
    return hashlib.md5(  # noqa: S324 -- Zenodo's vocabulary, not a claim
        baContent, usedforsecurity=False,
    ).hexdigest()


def _ftRunScript(monkeypatch, listPaths, clientFake, iParent=0):
    """Exec the real script against a fake Zenodo; return (code, stdout).

    ``sys.modules['zenodoClient']`` is pointed at the real module so
    the script's flat import finds the genuine comparison functions,
    and only the client class is replaced. A fake comparison would
    make this test agree with itself.
    """
    moduleKeyring = types.ModuleType("keyring")
    moduleKeyring.get_password = lambda sService, sSlot: "a-token"
    monkeypatch.setitem(sys.modules, "keyring", moduleKeyring)
    monkeypatch.setitem(sys.modules, "zenodoClient", zenodoClient)
    monkeypatch.setattr(
        zenodoClient, "ZenodoClient",
        lambda **kwargs: clientFake, raising=True,
    )
    sScript = _fsBuildScript(listPaths, iParent)
    streamOut = io.StringIO()
    try:
        with redirect_stdout(streamOut):
            exec(compile(sScript, "<archive>", "exec"), {"__name__": "__m__"})
    except SystemExit as errorExit:
        return str(errorExit.code), streamOut.getvalue()
    return "", streamOut.getvalue()


@pytest.fixture
def pathFile(tmp_path):
    pathWritten = tmp_path / _S_NAME
    pathWritten.write_bytes(_BA_CONTENT)
    return pathWritten


def test_an_agreeing_draft_is_published(monkeypatch, pathFile):
    clientFake = _FakeZenodo()
    sCode, sOut = _ftRunScript(monkeypatch, [str(pathFile)], clientFake)
    assert sCode == "", sCode
    assert "ZENODO_RESULT=" in sOut
    assert clientFake.listPhases == [
        "created", "upload", "asked", "published",
    ]


@pytest.mark.falsification
def test_a_disagreeing_draft_costs_a_draft_and_never_a_doi(
    monkeypatch, pathFile,
):
    """The check runs BEFORE the publish, and the ordering is the point.

    Verifying afterwards would mean failing with a DOI already minted:
    an orphan the researcher owns, that vaibify refused to record, and
    that nothing on this path could clean up, because a published
    record cannot be discarded.

    Kills: dropping the verification from the container script, or
    moving it after ``fdictPublishDraft``.
    """
    clientFake = _FakeZenodo(dictServed={"files": [
        {"key": _S_NAME, "filesize": len(_BA_CONTENT),
         "checksum": "md5:" + "e" * 32},
    ]})
    sCode, sOut = _ftRunScript(monkeypatch, [str(pathFile)], clientFake)
    assert "published" not in clientFake.listPhases, "a DOI was minted"
    assert "discarded" in clientFake.listPhases
    assert "ARCHIVE-MISMATCH" in sCode
    assert "nothing was published" in sCode
    assert "ZENODO_RESULT=" not in sOut


def test_a_truncated_upload_is_caught(monkeypatch, pathFile):
    clientFake = _FakeZenodo(dictServed={"files": [
        {"key": _S_NAME, "filesize": 1,
         "checksum": "md5:" + _fsMd5(_BA_CONTENT)},
    ]})
    sCode, _ = _ftRunScript(monkeypatch, [str(pathFile)], clientFake)
    assert "bytes" in sCode
    assert "published" not in clientFake.listPhases


def test_a_draft_serving_no_such_file_is_caught(monkeypatch, pathFile):
    clientFake = _FakeZenodo(dictServed={"files": []})
    sCode, _ = _ftRunScript(monkeypatch, [str(pathFile)], clientFake)
    assert "serves no file named" in sCode


@pytest.mark.falsification
def test_a_file_the_clear_missed_is_caught(monkeypatch, pathFile):
    """A newversion draft INHERITS the parent's files.

    vaibify clears them before uploading. A delete that quietly did
    not happen publishes a record mixing this version's files with the
    last one's, and every per-file comparison still passes — the
    researcher's record names N files and the archive serves N+1.
    This is the one disagreement only the versioning lane can produce,
    which is why the environment archive does not ask for it.

    Kills: dropping ``flistDescribeUnexpectedDepositFiles`` from the
    container script's problem list.
    """
    clientFake = _FakeZenodo(dictServed={"files": [
        {"key": _S_NAME, "filesize": len(_BA_CONTENT),
         "checksum": "md5:" + _fsMd5(_BA_CONTENT)},
        {"key": "stale-from-v1.csv", "filesize": 9,
         "checksum": "md5:" + "f" * 32},
    ]})
    sCode, _ = _ftRunScript(
        monkeypatch, [str(pathFile)], clientFake, iParent=99,
    )
    assert "stale-from-v1.csv" in sCode
    assert "did not upload" in sCode
    assert "published" not in clientFake.listPhases


def test_a_missing_local_file_is_still_named_a_local_problem(
    monkeypatch, tmp_path,
):
    """The pre-upload hash must not relabel a local fault as a Zenodo one.

    Hashing now happens before the upload, so a missing file is first
    touched by the hasher. Its FileNotFoundError has to keep reaching
    the same handler, or the researcher is sent to check their DOI
    over a file that was never on disk.
    """
    clientFake = _FakeZenodo()
    sCode, _ = _ftRunScript(
        monkeypatch, [str(tmp_path / "absent.csv")], clientFake,
    )
    assert "LOCAL-FILE-ERROR" in sCode
    assert "upload" not in clientFake.listPhases


def test_the_two_lanes_share_one_comparison():
    """The container cannot import the host, so drift is the default.

    Two copies of this check would announce their divergence as a
    deposit that passed on one lane and failed on the other.
    """
    import inspect
    from vaibify.reproducibility import imageDeposit
    sImageSource = inspect.getsource(
        imageDeposit.flistDescribeArchiveDisagreement,
    )
    assert "zenodoClient.flistDescribeDepositDisagreement" in sImageSource
    assert "flistDescribeDepositDisagreement" in (
        syncDispatcher._S_ARCHIVE_SCRIPT_TEMPLATE
    )


def test_the_digest_describes_the_file_on_disk(tmp_path):
    pathWritten = tmp_path / "some.bin"
    pathWritten.write_bytes(_BA_CONTENT)
    dictDescribed = zenodoClient.fdictDescribeLocalFileForDeposit(
        str(pathWritten),
    )
    assert dictDescribed == {
        "sKey": "some.bin", "sMd5": _fsMd5(_BA_CONTENT),
        "iBytes": len(_BA_CONTENT),
    }
