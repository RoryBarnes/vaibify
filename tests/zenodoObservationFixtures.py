"""Serve a Zenodo archive's files to the verify without the network.

The verify observes every declared record once, anonymously, and then
hashes each record's files from that observation
(``zenodoClient.fdictHashRecordFiles``). The conftest guard answers
every observation "open, no files"; a test about the comparison serves
the hashes through here, keyed by record id, the way the tests that
predate the observation step served them through
``zenodoClient.fdictFetchRemoteHashes``.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


__all__ = [
    "fcontextServeZenodoHashes",
    "fmockServeZenodoHashes",
]


def _fmockHashRecordFiles(fnHashesForRecord):
    """Return a mock of ``fdictHashRecordFiles`` that asks fnHashesForRecord."""
    def fdictHash(dictObservation, listRelPaths=None, sService="sandbox"):
        return fnHashesForRecord(
            dictObservation["sRecordId"], listRelPaths=listRelPaths,
            sService=sService,
        )

    return MagicMock(side_effect=fdictHash)


def fmockServeZenodoHashes(monkeypatch, fnHashesForRecord):
    """Patch the record-hash seam; return the mock so calls can be read.

    ``fnHashesForRecord(sRecordId, listRelPaths=..., sService=...)``
    returns ``{depositKey: sha256}`` for one record, or is a MagicMock.
    """
    from vaibify.reproducibility import zenodoClient
    mockHash = _fmockHashRecordFiles(fnHashesForRecord)
    monkeypatch.setattr(zenodoClient, "fdictHashRecordFiles", mockHash)
    return mockHash


@contextmanager
def fcontextServeZenodoHashes(fnHashesForRecord):
    """The context-manager form, for tests written with ``patch``."""
    mockHash = _fmockHashRecordFiles(fnHashesForRecord)
    with patch(
        "vaibify.reproducibility.zenodoClient.fdictHashRecordFiles",
        mockHash,
    ):
        yield mockHash
