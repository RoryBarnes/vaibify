"""Tests for the Prompt Record capture manager.

Drives a real capture pass against a temp project repo with a stub
docker connection, then attacks the record: a tampered capture entry
must break the hash chain, an edited session file must land in the
tamper list, and a simulated silence must open a coverage gap —
falsification-style assertions, not just happy paths.
"""

import json
from collections import namedtuple

import pytest

from vaibify.gui.promptRecordManager import (
    S_PROMPT_RECORD_INDEX_PATH,
    S_PROMPT_RECORD_SESSIONS_DIRECTORY,
    fbVerifyCaptureChain,
    fdictLoadIndex,
    fdictRunCapturePass,
    flistVerifyCapturedFiles,
)
from vaibify.gui.transcriptSanitizer import fbSanitizerAvailable
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


_ResultExec = namedtuple(
    "_ResultExec", ["iExitCode", "sStdout", "sStderr"],
)

S_TRANSCRIPT_PATH = "/home/user/.claude/projects/example/session1.jsonl"


class _StubDockerTranscripts:
    """Serve a fixed transcript listing + contents to the manager."""

    def __init__(self, dictTranscripts):
        self.dictTranscripts = dictTranscripts

    def texecRunInContainerStreamed(self, sContainerId, sCommand):
        dictSizes = {
            sPath: len(baContent)
            for sPath, baContent in self.dictTranscripts.items()
        }
        return _ResultExec(0, json.dumps(dictSizes), "")

    def fbaFetchFile(self, sContainerId, sFilePath):
        return self.dictTranscripts[sFilePath]


def _fnRequireSanitizer():
    if not fbSanitizerAvailable():
        pytest.skip("detect-secrets not installed (vaibify[replay])")


def _ftCaptureOnce(tmp_path, baContent):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    stubDocker = _StubDockerTranscripts({S_TRANSCRIPT_PATH: baContent})
    dictSummary = fdictRunCapturePass(
        stubDocker, "cid", filesRepo, ["secret-token-abcdef123456"],
    )
    return filesRepo, stubDocker, dictSummary


def test_capture_lands_sanitized_session_and_index(tmp_path):
    _fnRequireSanitizer()
    baContent = (
        b'{"role":"user","text":"token secret-token-abcdef123456"}\n'
    )
    filesRepo, _, dictSummary = _ftCaptureOnce(tmp_path, baContent)
    assert dictSummary["iSessionCount"] == 1
    assert dictSummary["iRedactionCount"] >= 1
    dictIndex = fdictLoadIndex(filesRepo)
    assert len(dictIndex["listCaptures"]) == 1
    sSessionText = filesRepo.fsReadText(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/"
        + dictIndex["listCaptures"][0]["sSessionFileName"],
    )
    assert "secret-token-abcdef123456" not in sSessionText
    assert "[REDACTED: " in sSessionText
    assert fbVerifyCaptureChain(dictIndex)
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == []


def test_unchanged_transcript_is_not_recaptured(tmp_path):
    _fnRequireSanitizer()
    baContent = b'{"text":"plain"}\n'
    filesRepo, stubDocker, _ = _ftCaptureOnce(tmp_path, baContent)
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert len(dictIndex["listCaptures"]) == 1


def test_grown_transcript_is_recaptured_whole_and_chain_extends(
    tmp_path,
):
    _fnRequireSanitizer()
    baContent = b'{"text":"first"}\n'
    filesRepo, stubDocker, _ = _ftCaptureOnce(tmp_path, baContent)
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = (
        baContent + b'{"text":"second"}\n'
    )
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert len(dictIndex["listCaptures"]) == 2
    assert fbVerifyCaptureChain(dictIndex)
    sSessionText = filesRepo.fsReadText(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/"
        + dictIndex["listCaptures"][-1]["sSessionFileName"],
    )
    assert '"first"' in sSessionText and '"second"' in sSessionText


def test_tampered_capture_record_breaks_the_chain(tmp_path):
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"a"}\n',
    )
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = (
        b'{"text":"a"}\n{"text":"b"}\n'
    )
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert fbVerifyCaptureChain(dictIndex)
    dictIndex["listCaptures"][0]["iRedactionCount"] = 999
    assert not fbVerifyCaptureChain(dictIndex)
    del dictIndex["listCaptures"][0]
    assert not fbVerifyCaptureChain(dictIndex)


def test_edited_session_file_is_reported_as_tampered(tmp_path):
    _fnRequireSanitizer()
    filesRepo, _, _ = _ftCaptureOnce(tmp_path, b'{"text":"a"}\n')
    dictIndex = fdictLoadIndex(filesRepo)
    sFileName = dictIndex["listCaptures"][0]["sSessionFileName"]
    filesRepo.fnWriteTextAtomic(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/" + sFileName,
        "rewritten after capture\n",
    )
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == [
        sFileName,
    ]


def test_silence_between_passes_opens_a_coverage_gap(tmp_path):
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"a"}\n',
    )
    dictIndex = fdictLoadIndex(filesRepo)
    assert len(dictIndex["listCoverageIntervals"]) == 1
    # Simulate a long hub-down silence by backdating the live
    # interval, then run another pass: a NEW interval must open —
    # the unmonitored time between them is the honest gap.
    for dictInterval in dictIndex["listCoverageIntervals"]:
        dictInterval["sEndUtc"] = "2020-01-01T00:00:00+00:00"
        dictInterval["sStartUtc"] = "2020-01-01T00:00:00+00:00"
    filesRepo.fnWriteJsonAtomic(S_PROMPT_RECORD_INDEX_PATH, dictIndex)
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert len(dictIndex["listCoverageIntervals"]) == 2
