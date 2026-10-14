"""The durable writer installs every byte or nothing, and says when a sync fails.

Fault injection at the OS-call boundary, not a power cut: a short
``os.write`` and a directory ``fsync`` that fails are the two answers
the writer used to accept as success.
"""

import logging
import os

import pytest

from vaibify.config import durableFile


__all__ = [
    "testAShortWriteIsContinuedUntilEveryByteIsDown",
    "testAWriteThatStopsInstallsNothing",
    "testADirectorySyncFailureIsReportedAtWarning",
]


BA_RECORD = b"{\"iDurableBytes\": 1234567, \"bComplete\": false}\n"


def _ffnWriteAtMost(iBytesPerCall):
    fnRealWrite = os.write

    def fiWriteShort(iDescriptor, baContent):
        return fnRealWrite(iDescriptor, bytes(baContent)[:iBytesPerCall])
    return fiWriteShort


@pytest.mark.falsification
def testAShortWriteIsContinuedUntilEveryByteIsDown(tmp_path, monkeypatch):
    """Kills: trusting one ``os.write`` to have written the whole record."""
    monkeypatch.setattr(os, "write", _ffnWriteAtMost(16))
    sPath = str(tmp_path / "record.json")
    durableFile.fnWriteFileDurably(sPath, BA_RECORD)
    with open(sPath, "rb") as fileRecord:
        assert fileRecord.read() == BA_RECORD


def testAWriteThatStopsInstallsNothing(tmp_path, monkeypatch):
    sPath = str(tmp_path / "record.json")
    with open(sPath, "wb") as fileRecord:
        fileRecord.write(b"previous")
    monkeypatch.setattr(os, "write", lambda iDescriptor, baContent: 0)
    with pytest.raises(OSError):
        durableFile.fnWriteFileDurably(sPath, BA_RECORD)
    with open(sPath, "rb") as fileRecord:
        assert fileRecord.read() == b"previous"
    assert sorted(os.listdir(tmp_path)) == ["record.json"], "a temporary file was left"


def testADirectorySyncFailureIsReportedAtWarning(tmp_path, monkeypatch, caplog):
    fnRealSync = os.fsync

    def fnRefuseDirectories(iDescriptor):
        if os.fstat(iDescriptor).st_mode & 0o040000:
            raise OSError(5, "Input/output error")
        fnRealSync(iDescriptor)

    monkeypatch.setattr(os, "fsync", fnRefuseDirectories)
    sPath = str(tmp_path / "record.json")
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        durableFile.fnWriteFileDurably(sPath, BA_RECORD)
    with open(sPath, "rb") as fileRecord:
        assert fileRecord.read() == BA_RECORD
    listWarnings = [
        recordLog for recordLog in caplog.records
        if recordLog.levelno == logging.WARNING and str(tmp_path) in recordLog.getMessage()
    ]
    assert listWarnings, "the failed directory sync was not reported"
    assert "power loss" in listWarnings[0].getMessage()
