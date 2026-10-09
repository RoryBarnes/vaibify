"""Tests for the Prompt Record capture manager.

Drives a real capture pass against a temp project repo with a stub
docker connection, then attacks the record: a tampered capture entry
must break the hash chain, an edited session file must land in the
tamper list, and a simulated silence must open a coverage gap —
falsification-style assertions, not just happy paths.

The incremental-capture tests compare against the sanitizer run on the
WHOLE transcript, because the claim they defend is byte identity with
the whole-file recapture the incremental path replaced — not merely
"the secret is gone".
"""

import json
import os
import subprocess
import sys
import zlib
from collections import namedtuple

import pytest

from vaibify.gui import promptRecordManager
from vaibify.gui.promptRecordManager import (
    S_PROMPT_RECORD_INDEX_PATH,
    S_PROMPT_RECORD_SESSIONS_DIRECTORY,
    fbSessionBelongsToProject,
    fbVerifyCaptureChain,
    fdictLandSanitizedSessions,
    fdictLoadIndex,
    fdictSanitizeNewTranscriptLines,
    flistSummarizeSessions,
    flistVerifyCapturedFiles,
)
from vaibify.gui.transcriptSanitizer import (
    fbSanitizerAvailable,
    ftResultSanitizeText,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


_ResultExec = namedtuple(
    "_ResultExec", ["iExitCode", "sStdout", "sStderr"],
)

S_PROJECT_REPO_PATH = "/workspace/project"
S_TRANSCRIPT_PATH = (
    "/home/user/.claude/projects/-workspace-project/session1.jsonl"
)
S_EXACT_SECRET = "secret-token-abcdef123456"


class _StubDockerTranscripts:
    """Serve a fixed transcript listing + contents to the manager.

    ``dictLaunchDirectories`` gives each transcript the directory its
    session was launched in; unlisted transcripts were launched in the
    project repository.
    """

    def __init__(self, dictTranscripts, dictLaunchDirectories=None):
        self.dictTranscripts = dictTranscripts
        self.dictLaunchDirectories = dictLaunchDirectories or {}
        self.dictProviders = {}

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        dictListing = {
            sPath: {
                "iSizeBytes": len(baContent),
                "sLaunchDirectory": self.dictLaunchDirectories.get(
                    sPath, S_PROJECT_REPO_PATH,
                ),
                # A real file's mtime and ctime move on every write; a
                # content-derived key stands in for them here, so a
                # same-size rewrite moves the key exactly as it would.
                "listStatKey": [zlib.crc32(baContent), len(baContent)],
                "sProvider": self.dictProviders.get(sPath, "claude"),
            }
            for sPath, baContent in self.dictTranscripts.items()
        }
        return _ResultExec(0, json.dumps(dictListing), "")

    def fbaFetchFile(self, sContainerId, sFilePath):
        return self.dictTranscripts[sFilePath]


def fdictRunCapturePass(stubDocker, sContainerId, filesRepo, listSecrets):
    """Run the three capture phases in order, as the route does."""
    dictListing = promptRecordManager.fdictListContainerTranscripts(
        stubDocker, sContainerId,
    )
    dictSanitized = fdictSanitizeNewTranscriptLines(
        stubDocker, sContainerId, filesRepo, dictListing,
        S_PROJECT_REPO_PATH, listSecrets,
    )
    return fdictLandSanitizedSessions(filesRepo, dictSanitized)


def _fsLandedSessionText(filesRepo):
    dictIndex = fdictLoadIndex(filesRepo)
    return filesRepo.fsReadText(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/"
        + dictIndex["listCaptures"][-1]["sSessionFileName"],
    )


def _fnRequireSanitizer():
    if not fbSanitizerAvailable():
        pytest.skip("detect-secrets not installed (vaibify[replay])")


def _ftCaptureOnce(tmp_path, baContent):
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    stubDocker = _StubDockerTranscripts({S_TRANSCRIPT_PATH: baContent})
    dictSummary = fdictRunCapturePass(
        stubDocker, "cid", filesRepo, [S_EXACT_SECRET],
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


def test_grown_transcript_appends_new_lines_and_chain_extends(
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
    assert dictIndex["listCaptures"][-1]["sCaptureKind"] == "appended"
    assert fbVerifyCaptureChain(dictIndex)
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == []
    sSessionText = _fsLandedSessionText(filesRepo)
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


def _fbaSecretBearingLines(iStart, iCount):
    """Return JSONL lines that trip every redaction layer."""
    listLines = []
    for iLine in range(iStart, iStart + iCount):
        listLines.append(json.dumps({
            "iLine": iLine,
            "sText": (
                "token " + S_EXACT_SECRET + " and ghp_"
                + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + " key "
                + "AKIAIOSFODNN7EXAMPLE and entropy "
                + "q8Zr4Lm2Xv7Tn1Kp9Wd3Hs6Jf0Gb5Yc8Ue2Ra4"
            ),
        }).encode("utf-8") + b"\n")
    return b"".join(listLines)


@pytest.mark.falsification
def test_appended_capture_is_byte_identical_to_whole_recapture(tmp_path):
    """Three appends land exactly what one whole-file sanitize produces.

    The incremental path is only honest if it is EXACT: sanitizing the
    new lines and concatenating must equal sanitizing the whole file.
    Every redaction layer is exercised in every chunk, so a layer that
    was not line-local would diverge here. The oracle is the sanitizer
    run once over the whole transcript -- the behavior this path
    replaced -- not the incremental code under test.

    Kills: landing only the new chunk's sanitized text instead of the
    prior session text plus the chunk.
    """
    _fnRequireSanitizer()
    baTranscript = _fbaSecretBearingLines(0, 3)
    filesRepo, stubDocker, _ = _ftCaptureOnce(tmp_path, baTranscript)
    for iChunk in range(1, 4):
        baTranscript += _fbaSecretBearingLines(iChunk * 10, 2)
        stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = baTranscript
        fdictRunCapturePass(stubDocker, "cid", filesRepo, [S_EXACT_SECRET])
    sExpected, _ = ftResultSanitizeText(
        baTranscript.decode("utf-8"), [S_EXACT_SECRET],
    )
    dictIndex = fdictLoadIndex(filesRepo)
    assert [
        dictRecord["sCaptureKind"] for dictRecord in dictIndex["listCaptures"]
    ] == ["whole", "appended", "appended", "appended"]
    assert _fsLandedSessionText(filesRepo) == sExpected
    assert S_EXACT_SECRET not in sExpected
    assert fbVerifyCaptureChain(dictIndex)
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == []


@pytest.mark.falsification
def test_partial_trailing_line_waits_for_its_newline(tmp_path):
    """A line still being written is not captured, then lands whole.

    A secret can span a mid-line boundary, so cutting there would
    sanitize two halves that each look innocent.

    Kills: cutting the capture at the end of the fetched bytes instead
    of after the last newline.
    """
    _fnRequireSanitizer()
    baFirst = b'{"text":"complete"}\n'
    baSecretLine = (
        '{"text":"' + S_EXACT_SECRET + '"}\n'
    ).encode("utf-8")
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, baFirst + baSecretLine[:20],
    )
    assert S_EXACT_SECRET[:8] not in _fsLandedSessionText(filesRepo)
    assert fdictLoadIndex(filesRepo)["dictSessionBytes"][
        S_TRANSCRIPT_PATH
    ] == len(baFirst)
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = baFirst + baSecretLine
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [S_EXACT_SECRET])
    sLanded = _fsLandedSessionText(filesRepo)
    assert S_EXACT_SECRET not in sLanded
    assert sLanded.count("[REDACTED: ") == 1


@pytest.mark.falsification
def test_rewritten_transcript_is_recaptured_whole(tmp_path):
    """A transcript whose captured prefix changed is not appended to.

    Kills: treating the captured raw prefix as unchanged without
    comparing its hash.
    """
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"original"}\n',
    )
    baRewritten = b'{"text":"replaced"}\n{"text":"more"}\n'
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = baRewritten
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["listCaptures"][-1]["sCaptureKind"] == "whole"
    sLanded = _fsLandedSessionText(filesRepo)
    assert "original" not in sLanded
    assert sLanded == baRewritten.decode("utf-8")


@pytest.mark.falsification
def test_a_same_size_rewrite_is_recaptured_whole(tmp_path):
    """A rewrite that leaves the size unchanged is still a rewrite.

    The capture loop skipped any transcript no larger than what it had
    captured, before ever comparing the captured prefix, so a same-size
    rewrite was never refetched and the record kept text the transcript
    no longer held.

    Kills: skipping a transcript on its size alone.
    """
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"aaaa"}\n',
    )
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = b'{"text":"bbbb"}\n'
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["listCaptures"][-1]["sCaptureKind"] == "whole"
    assert _fsLandedSessionText(filesRepo) == '{"text":"bbbb"}\n'
    assert fbVerifyCaptureChain(dictIndex)


@pytest.mark.falsification
def test_a_truncated_transcript_is_recaptured_whole(tmp_path):
    """A transcript cut back to fewer lines is recaptured as it now is.

    Kills: skipping a transcript that is smaller than what was captured.
    """
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"kept"}\n{"text":"dropped"}\n',
    )
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = b'{"text":"kept"}\n'
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["listCaptures"][-1]["sCaptureKind"] == "whole"
    assert _fsLandedSessionText(filesRepo) == '{"text":"kept"}\n'
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == []


def test_an_unchanged_transcript_is_not_fetched_again(tmp_path):
    """The stat key spares an unchanged transcript the fetch entirely."""
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"once"}\n',
    )
    listFetched = []
    fnOriginalFetch = stubDocker.fbaFetchFile

    def fbaRecordingFetch(sContainerId, sFilePath):
        listFetched.append(sFilePath)
        return fnOriginalFetch(sContainerId, sFilePath)

    stubDocker.fbaFetchFile = fbaRecordingFetch
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    assert listFetched == []


@pytest.mark.falsification
def test_edited_session_file_is_recaptured_not_appended_to(tmp_path):
    """New lines are never appended onto a session edited after capture.

    Kills: accepting the landed session text without checking it still
    matches the hash its newest capture record pins.
    """
    _fnRequireSanitizer()
    baFirst = b'{"text":"a"}\n'
    filesRepo, stubDocker, _ = _ftCaptureOnce(tmp_path, baFirst)
    sFileName = fdictLoadIndex(filesRepo)["listCaptures"][0][
        "sSessionFileName"
    ]
    filesRepo.fnWriteTextAtomic(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/" + sFileName,
        "planted after capture\n",
    )
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = (
        baFirst + b'{"text":"b"}\n'
    )
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    assert "planted" not in _fsLandedSessionText(filesRepo)
    assert fdictLoadIndex(filesRepo)["listCaptures"][-1][
        "sCaptureKind"
    ] == "whole"


@pytest.mark.falsification
def test_sessions_launched_outside_the_project_are_not_captured(tmp_path):
    """Another project's session, and the workspace root's, stay out.

    The count of left-out sessions is kept in the index so the dialog
    can show the gap; their paths are not, because a path names the
    project it was launched in.

    Kills: skipping the launch-directory scope check.
    """
    _fnRequireSanitizer()
    sSiblingPath = "/home/user/.claude/projects/-workspace-other/s2.jsonl"
    sRootPath = "/home/user/.claude/projects/-workspace/s3.jsonl"
    sSubdirectoryPath = (
        "/home/user/.claude/projects/-workspace-project-sub/s4.jsonl"
    )
    stubDocker = _StubDockerTranscripts(
        {
            S_TRANSCRIPT_PATH: b'{"text":"mine"}\n',
            sSiblingPath: b'{"text":"other project"}\n',
            sRootPath: b'{"text":"workspace root"}\n',
            sSubdirectoryPath: b'{"text":"subdirectory"}\n',
        },
        {
            sSiblingPath: "/workspace/project-old",
            sRootPath: "/workspace",
            sSubdirectoryPath: S_PROJECT_REPO_PATH + "/sub",
        },
    )
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    dictSummary = fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    assert dictSummary["iSessionsOutsideProject"] == 2
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["iSessionsOutsideProject"] == 2
    assert "other" not in json.dumps(dictIndex)
    assert sorted(dictSummary["listCapturedSessions"]) == [
        "-workspace-project-sub__s4.jsonl",
        "-workspace-project__session1.jsonl",
    ]


@pytest.mark.parametrize("sLaunchDirectory, bExpected", [
    ("/workspace/project", True),
    ("/workspace/project/", True),
    ("/workspace/project/analysis", True),
    ("/workspace/project-old", False),
    ("/workspace/projectile", False),
    ("/workspace", False),
    ("/workspace/project/../other", False),
    ("", False),
])
def test_session_scope_is_decided_by_path_components(
    sLaunchDirectory, bExpected,
):
    assert fbSessionBelongsToProject(
        sLaunchDirectory, "/workspace/project",
    ) is bExpected


@pytest.mark.falsification
def test_landing_defers_a_session_another_pass_already_moved(tmp_path):
    """Two overlapping passes cannot both chain the same new lines.

    Kills: landing a pending session without re-checking that the index
    still holds the state it was sanitized against.
    """
    _fnRequireSanitizer()
    baFirst = b'{"text":"a"}\n'
    filesRepo, stubDocker, _ = _ftCaptureOnce(tmp_path, baFirst)
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = (
        baFirst + b'{"text":"b"}\n'
    )
    dictListing = promptRecordManager.fdictListContainerTranscripts(
        stubDocker, "cid",
    )
    dictStale = fdictSanitizeNewTranscriptLines(
        stubDocker, "cid", filesRepo, dictListing, S_PROJECT_REPO_PATH, [],
    )
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictSummary = fdictLandSanitizedSessions(filesRepo, dictStale)
    assert dictSummary["listCapturedSessions"] == []
    assert dictSummary["listDeferredSessions"] == [
        "-workspace-project__session1.jsonl",
    ]
    dictIndex = fdictLoadIndex(filesRepo)
    assert len(dictIndex["listCaptures"]) == 2
    assert fbVerifyCaptureChain(dictIndex)
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == []


def _fnWriteTranscript(sPath, listLines):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileTranscript:
        for dictLine in listLines:
            fileTranscript.write(json.dumps(dictLine) + "\n")


@pytest.mark.falsification
def test_the_container_listing_program_reads_each_launch_directory(
    tmp_path,
):
    """Run the REAL listing program the container runs, against files.

    The stub above answers the listing itself, so without this the
    program that decides scope in production would never execute. The
    FIRST recorded working directory is the launch directory; a later
    ``cwd`` is where the session wandered, not where it started.

    Kills: taking the last recorded ``cwd`` instead of the first.
    """
    sRoot = str(tmp_path / ".claude" / "projects")
    sInProject = os.path.join(sRoot, "-workspace-project", "a.jsonl")
    sWithoutCwd = os.path.join(sRoot, "-workspace-project", "b.jsonl")
    _fnWriteTranscript(sInProject, [
        {"type": "summary", "summary": "no cwd on this line"},
        {"type": "user", "cwd": "/workspace/project", "text": "x"},
        {"type": "user", "cwd": "/workspace/elsewhere", "text": "y"},
    ])
    _fnWriteTranscript(sWithoutCwd, [{"type": "summary"}])
    sProgram = promptRecordManager._S_LIST_PROGRAM
    dictEnvironment = dict(os.environ, HOME=str(tmp_path))
    resultRun = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True,
        text=True, env=dictEnvironment, check=True,
    )
    dictListing = json.loads(resultRun.stdout)
    assert dictListing[sInProject]["sLaunchDirectory"] == (
        "/workspace/project"
    )
    assert dictListing[sInProject]["iSizeBytes"] == os.path.getsize(
        sInProject,
    )
    assert dictListing[sWithoutCwd]["sLaunchDirectory"] == ""


@pytest.mark.falsification
def test_a_whole_recapture_resets_the_session_redaction_tally():
    """A session's redactions are its current total, not a sum of records.

    A whole recapture re-counts everything it lands, so summing every
    record counted a recaptured session twice; an appended capture adds
    only its new lines' redactions.

    Kills: adding a whole recapture's count instead of resetting to it.
    """
    dictIndex = {"listCaptures": [
        {"sSessionFileName": "a.jsonl", "sCaptureKind": "whole",
         "iRedactionCount": 5},
        {"sSessionFileName": "a.jsonl", "sCaptureKind": "appended",
         "iRedactionCount": 2},
        {"sSessionFileName": "b.jsonl", "sCaptureKind": "whole",
         "iRedactionCount": 1},
        {"sSessionFileName": "a.jsonl", "sCaptureKind": "whole",
         "iRedactionCount": 7},
    ]}
    assert [
        (dictSession["sSessionFileName"], dictSession["iRedactionCount"])
        for dictSession in flistSummarizeSessions(dictIndex)
    ] == [("a.jsonl", 7), ("b.jsonl", 1)]


# ---------------------------------------------------------------------
# Codex and Gemini transcripts, and records written before providers.
# The sample transcripts are synthetic and secret-free, shaped like the
# files each CLI writes: Codex's first line is ``session_meta`` with the
# launch directory in ``payload.cwd``; Gemini files its chats under a
# per-project folder whose ``.project_root`` names the directory.
# ---------------------------------------------------------------------

def _fdictRunTheListingProgram(sHome):
    dictEnvironment = dict(os.environ, HOME=sHome)
    resultRun = subprocess.run(
        [sys.executable, "-c", promptRecordManager._S_LIST_PROGRAM],
        capture_output=True, text=True, env=dictEnvironment, check=True,
    )
    return json.loads(resultRun.stdout)


@pytest.mark.falsification
def test_the_listing_program_reads_codex_and_gemini_launch_directories(
    tmp_path,
):
    """Kills: the Codex rule reading the launch directory from any
    record rather than the session's own ``session_meta``.
    """
    sHome = str(tmp_path)
    sCodex = os.path.join(
        sHome, ".codex", "sessions", "2026", "10", "08",
        "rollout-2026-10-08T10-00-00-0199aaaa.jsonl")
    _fnWriteTranscript(sCodex, [
        {"type": "turn_context", "payload": {"cwd": "/workspace/elsewhere"}},
        {"type": "session_meta",
         "payload": {"id": "0199aaaa", "cwd": "/workspace/project"}},
    ])
    sGeminiMarked = os.path.join(
        sHome, ".gemini", "tmp", "project", "chats",
        "session-2026-10-08T10-00-aaaa.jsonl")
    _fnWriteTranscript(sGeminiMarked, [{"sessionId": "aaaa"}])
    with open(os.path.join(sHome, ".gemini", "tmp", "project",
                           ".project_root"), "w") as fileRoot:
        fileRoot.write("/workspace/project\n")
    sGeminiMapped = os.path.join(
        sHome, ".gemini", "tmp", "other-1", "chats",
        "session-2026-10-08T11-00-bbbb.jsonl")
    _fnWriteTranscript(sGeminiMapped, [{"sessionId": "bbbb"}])
    with open(os.path.join(sHome, ".gemini", "projects.json"),
              "w") as fileMap:
        json.dump({"projects": {"/workspace/other": "other-1"}}, fileMap)
    dictListing = _fdictRunTheListingProgram(sHome)
    assert dictListing[sCodex]["sProvider"] == "codex"
    assert dictListing[sCodex]["sLaunchDirectory"] == "/workspace/project"
    assert dictListing[sGeminiMarked]["sProvider"] == "gemini"
    assert dictListing[sGeminiMarked]["sLaunchDirectory"] == (
        "/workspace/project")
    assert dictListing[sGeminiMapped]["sLaunchDirectory"] == (
        "/workspace/other")
    assert len(dictListing[sCodex]["listStatKey"]) == 4


def test_the_listing_program_ignores_gemini_backups_and_temp_files(
    tmp_path,
):
    """Gemini preserves an unreadable session beside a rewritten one and
    writes through a temporary file; neither is a transcript."""
    sHome = str(tmp_path)
    sChats = os.path.join(sHome, ".gemini", "tmp", "project", "chats")
    sSession = os.path.join(sChats, "session-2026-10-08T10-00-aaaa.jsonl")
    _fnWriteTranscript(sSession, [{"sessionId": "aaaa"}])
    _fnWriteTranscript(sSession + ".unreadable-1760000000", [{}])
    _fnWriteTranscript(sSession + ".tmp-4242", [{}])
    assert list(_fdictRunTheListingProgram(sHome)) == [sSession]


def test_codex_and_gemini_sessions_land_under_provider_names(tmp_path):
    _fnRequireSanitizer()
    sCodex = (
        "/home/user/.codex/sessions/2026/10/08/rollout-2026-10-08-aaaa.jsonl")
    sGemini = (
        "/home/user/.gemini/tmp/project/chats/session-2026-10-08-bbbb.jsonl")
    sElsewhere = (
        "/home/user/.codex/sessions/2026/10/08/rollout-2026-10-08-cccc.jsonl")
    stubDocker = _StubDockerTranscripts({
        sCodex: b'{"type":"session_meta","payload":{}}\n',
        sGemini: b'{"sessionId":"bbbb"}\n',
        sElsewhere: b'{"type":"session_meta","payload":{}}\n',
    }, dictLaunchDirectories={sElsewhere: "/workspace/other"})
    stubDocker.dictProviders = {sCodex: "codex", sGemini: "gemini",
                                sElsewhere: "codex"}
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    dictSummary = fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    assert dictSummary["iSessionsOutsideProject"] == 1
    dictIndex = fdictLoadIndex(filesRepo)
    dictByName = {
        dictRecord["sSessionFileName"]: dictRecord["sProvider"]
        for dictRecord in dictIndex["listCaptures"]}
    assert dictByName == {
        "codex__rollout-2026-10-08-aaaa.jsonl": "codex",
        "gemini__project__session-2026-10-08-bbbb.jsonl": "gemini",
    }
    assert fbVerifyCaptureChain(dictIndex)


@pytest.mark.falsification
def test_an_index_written_before_providers_still_verifies_and_extends(
    tmp_path,
):
    """Provider metadata must not change the bytes the old chain hashed.

    The legacy record carries no ``sProvider`` and the index no stat
    keys. After a new capture the old record is byte-for-byte what it
    was, the chain still verifies, and the new record chains onto it.

    Kills: backfilling ``sProvider`` into records already in the index.
    """
    _fnRequireSanitizer()
    filesRepo = ffilesEnsureRepoFiles(str(tmp_path))
    sLegacyName = "-workspace-project__legacy.jsonl"
    sLegacyText = '{"type":"user","message":{"content":"old"}}\n'
    filesRepo.fnWriteTextAtomic(
        S_PROMPT_RECORD_SESSIONS_DIRECTORY + "/" + sLegacyName, sLegacyText)
    import hashlib
    dictLegacyRecord = {
        "sSessionFileName": sLegacyName, "sCaptureKind": "whole",
        "iBytesCaptured": len(sLegacyText),
        "sSha256": hashlib.sha256(sLegacyText.encode()).hexdigest(),
        "sPreviousRecordSha256": "",
        "sCapturedAtUtc": "2026-09-01T00:00:00+00:00",
        "iRedactionCount": 0, "dictRedactionsByCategory": {},
    }
    filesRepo.fnWriteJsonAtomic(S_PROMPT_RECORD_INDEX_PATH, {
        "listCaptures": [dictLegacyRecord], "listCoverageIntervals": [],
        "dictSessionBytes": {}, "dictSessionRawSha256": {},
        "iSessionsOutsideProject": 0,
    })
    stubDocker = _StubDockerTranscripts(
        {S_TRANSCRIPT_PATH: b'{"text":"new"}\n'})
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["listCaptures"][0] == dictLegacyRecord
    assert fbVerifyCaptureChain(dictIndex)
    assert dictIndex["listCaptures"][1]["sPreviousRecordSha256"] == (
        promptRecordManager._fsHashRecord(dictLegacyRecord))
    assert dictIndex["listCaptures"][1]["sProvider"] == "claude"
    dictSummaries = {
        d["sSessionFileName"]: d["sProvider"]
        for d in flistSummarizeSessions(dictIndex)}
    assert dictSummaries[sLegacyName] == "claude"


@pytest.mark.falsification
def test_a_transcript_truncated_to_nothing_replaces_its_landed_text(tmp_path):
    """A captured transcript emptied in place is recaptured as empty, and
    the empty file's stat key does not freeze the old text in place.

    Kills: returning no pending capture when a previously captured
    transcript now holds no complete line.
    """
    _fnRequireSanitizer()
    filesRepo, stubDocker, _ = _ftCaptureOnce(
        tmp_path, b'{"text":"said earlier"}\n',
    )
    stubDocker.dictTranscripts[S_TRANSCRIPT_PATH] = b""
    fdictRunCapturePass(stubDocker, "cid", filesRepo, [])
    dictIndex = fdictLoadIndex(filesRepo)
    assert dictIndex["listCaptures"][-1]["sCaptureKind"] == "whole"
    assert dictIndex["listCaptures"][-1]["iBytesCaptured"] == 0
    assert _fsLandedSessionText(filesRepo) == ""
    assert fbVerifyCaptureChain(dictIndex)
    assert flistVerifyCapturedFiles(filesRepo, dictIndex) == []
