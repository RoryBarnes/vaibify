"""The public Prompt Record carries no unkeyed digest of a raw transcript.

The index pins each captured raw prefix so the next pass can sanitize
only the lines appended since. It used to pin it with a bare SHA-256.
The index is public, and the sanitized session beside it reproduces
every raw byte except the redacted ones, so that hash let anyone test a
guessed secret offline: rebuild the raw prefix with the guess, hash it,
compare. The pin is now an HMAC under a key that never leaves the host,
and an index written by an earlier version loses its bare hashes at its
next write.

The capture runs for real against a temporary repository, a stub
transcript server and the real sanitizer; the oracle is attacked the
way an outsider would attack it, by hashing the raw prefixes and
searching the published index text for them.
"""

import hashlib
import hmac
import os
import stat

import pytest

from tests.testPromptRecordManager import (
    S_EXACT_SECRET,
    S_TRANSCRIPT_PATH,
    _StubDockerTranscripts,
    _fnRequireSanitizer,
    _fsLandedSessionText,
    fdictRunCapturePass,
)
from vaibify.gui import promptRecordManager
from vaibify.gui.promptRecordManager import (
    S_PROMPT_RECORD_INDEX_PATH,
    fbVerifyCaptureChain,
    fdictLoadIndex,
)
from vaibify.gui.transcriptSanitizer import ftResultSanitizeText
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


BA_FIRST_LINE = (
    b'{"role":"user","text":"token ' + S_EXACT_SECRET.encode() + b'"}\n')
BA_SECOND_LINE = b'{"role":"assistant","text":"noted"}\n'


def _ftCaptureTwice(tmp_path):
    """Capture one line, then the appended second: whole, then appended."""
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    stubDocker = _StubDockerTranscripts({S_TRANSCRIPT_PATH: BA_FIRST_LINE})
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [S_EXACT_SECRET])
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = (
        BA_FIRST_LINE + BA_SECOND_LINE)
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [S_EXACT_SECRET])
    return filesRepo, stubDocker


def _fbaDigestKey():
    with open(promptRecordManager.fsResolveDigestKeyPath(), "rb") as fileKey:
        return fileKey.read()


@pytest.mark.falsification
def testThePublishedIndexHoldsNoBareHashOfAnyRawPrefix(tmp_path):
    """Kills: pinning the raw prefix with a bare SHA-256 again."""
    _fnRequireSanitizer()
    filesRepo, _ = _ftCaptureTwice(tmp_path)
    sIndexText = filesRepo.fsReadText(S_PROMPT_RECORD_INDEX_PATH)
    baRaw = BA_FIRST_LINE + BA_SECOND_LINE
    for iLength in (len(BA_FIRST_LINE), len(baRaw)):
        assert hashlib.sha256(baRaw[:iLength]).hexdigest() not in sIndexText
    dictIndex = fdictLoadIndex(filesRepo)
    assert fdictLoadIndex(filesRepo)["listCaptures"][-1][
        "sCaptureKind"] == "appended"
    assert dictIndex["dictSessionRawDigests"][S_TRANSCRIPT_PATH] == (
        hmac.new(_fbaDigestKey(), baRaw, hashlib.sha256).hexdigest())


def testTheKeyIsPrivateAndStable(tmp_path):
    _fnRequireSanitizer()
    _ftCaptureTwice(tmp_path)
    sKeyPath = promptRecordManager.fsResolveDigestKeyPath()
    assert stat.S_IMODE(os.stat(sKeyPath).st_mode) == 0o600
    baKey = _fbaDigestKey()
    assert len(baKey) == 32
    assert promptRecordManager._fbaLoadOrCreateDigestKey() == baKey
    assert [sName for sName in os.listdir(os.path.dirname(sKeyPath))
            if sName.startswith("tmp")] == []


def testWithoutTheKeyTheSessionIsRecapturedWholeAndStaysCorrect(tmp_path):
    """Another machine, or a lost key: slower, never wrong."""
    _fnRequireSanitizer()
    filesRepo, stubDocker = _ftCaptureTwice(tmp_path)
    os.remove(promptRecordManager.fsResolveDigestKeyPath())
    baThird = b'{"role":"user","text":"and more"}\n'
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] += baThird
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [S_EXACT_SECRET])
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["listCaptures"][-1]["sCaptureKind"] == "whole"
    sWhole, _ = ftResultSanitizeText(
        stubDocker.dictTranscripts[S_TRANSCRIPT_PATH].decode("utf-8"),
        [S_EXACT_SECRET])
    assert _fsLandedSessionText(filesRepo) == sWhole
    assert fbVerifyCaptureChain(dictIndex)


@pytest.mark.falsification
def testAnIndexFromAnEarlierVersionLosesItsBareHashesAtItsNextWrite(tmp_path):
    """Kills: carrying the legacy unkeyed hashes forward into the index."""
    _fnRequireSanitizer()
    filesRepo, stubDocker = _ftCaptureTwice(tmp_path)
    dictIndex = fdictLoadIndex(filesRepo)
    dictIndex["dictSessionRawSha256"] = {S_TRANSCRIPT_PATH: "e" * 64}
    filesRepo.fnWriteJsonAtomic(S_PROMPT_RECORD_INDEX_PATH, dictIndex)
    stubDocker.dictTranscripts["/home/user/.claude/projects/"
                               "-workspace-project/session2.jsonl"] = (
        BA_SECOND_LINE)
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [S_EXACT_SECRET])
    sIndexText = filesRepo.fsReadText(S_PROMPT_RECORD_INDEX_PATH)
    assert "dictSessionRawSha256" not in sIndexText
    assert "e" * 64 not in sIndexText
