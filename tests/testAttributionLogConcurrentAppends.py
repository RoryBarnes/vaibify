"""Concurrent writers cannot fork or shorten the attribution hash chains.

Each record names its predecessor's hash, and an append read the file,
added a record and rewrote it. Two threads that both read the same last
record each wrote a successor to it: one record was lost, and where the
losing write landed last the chain still verified, so nothing said so.
The fake adapter pauses between its read and its write to make the
interleaving certain rather than lucky.
"""

import threading
import time

import pytest

from vaibify.gui import attributionLog

I_WRITERS = 12
DICT_SUPERVISED = {"bSupervisionEnabled": True}


class SlowRepoFiles:
    """Dict-backed repo files whose atomic write pauses first."""

    def __init__(self):
        self.dictFiles = {}

    def fbIsFile(self, sRelPath):
        return sRelPath in self.dictFiles

    def fsReadText(self, sRelPath):
        return self.dictFiles[sRelPath]

    def fnWriteTextAtomic(self, sRelPath, sContent):
        time.sleep(0.01)
        self.dictFiles[sRelPath] = sContent


def _fnRunWriters(fnWrite):
    listThreads = [threading.Thread(target=fnWrite, args=(iIndex,))
                   for iIndex in range(I_WRITERS)]
    for threadWriter in listThreads:
        threadWriter.start()
    for threadWriter in listThreads:
        threadWriter.join()


@pytest.fixture
def filesSupervised(monkeypatch):
    monkeypatch.setattr(attributionLog, "fbSupervisionEnabled",
                        lambda dictWorkflow: True)
    return SlowRepoFiles()


@pytest.mark.falsification
def testConcurrentEventAppendsKeepEveryRecordInOneChain(filesSupervised):
    """Kills: appending events without holding the chain's lock."""
    _fnRunWriters(lambda iIndex: attributionLog.fnAppendAttributionEvent(
        filesSupervised, DICT_SUPERVISED, "editor", "researcher",
        f"save-{iIndex}"))
    listEvents = attributionLog.flistLoadAttributionEvents(filesSupervised)
    assert len(listEvents) == I_WRITERS
    assert len({dictEvent["sDetail"] for dictEvent in listEvents}) == (
        I_WRITERS)
    assert attributionLog.fbVerifyEventChain(listEvents)


@pytest.mark.falsification
def testConcurrentFlagAppendsKeepEveryFlagInOneChain(filesSupervised):
    """Kills: appending flags without holding the chain's lock."""
    _fnRunWriters(lambda iIndex: attributionLog.fdictAppendFlag(
        filesSupervised, "unattributed-modification", f"file-{iIndex}"))
    listFlags = attributionLog.flistLoadFlags(filesSupervised)
    assert len(listFlags) == I_WRITERS
    assert attributionLog.fbVerifyFlagChain(listFlags)
