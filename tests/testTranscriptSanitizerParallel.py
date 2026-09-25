"""The parallel transcript sanitizer is exact, and uses workers only when it pays.

``flistSanitizeTextsInParallel`` splits texts at line boundaries and
scans the pieces in spawned worker processes. The claim worth defending
is that this changes nothing but the speed, so every exactness test
compares against the single in-process call over each WHOLE text -- the
behavior it replaced -- with the piece size forced small so the split
and the workers are really exercised.
"""

import json

import pytest

from vaibify.config import workerProcessPool
from vaibify.gui import transcriptSanitizer
from vaibify.gui.transcriptSanitizer import (
    fbSanitizerAvailable,
    flistSanitizeTextsInParallel,
    ftResultSanitizeText,
)


S_EXACT_SECRET = "secret-token-abcdef123456"
S_MULTILINE_SECRET = "first-half-of-a-secret\nsecond-half-of-a-secret"


def _fnRequireSanitizer():
    if not fbSanitizerAvailable():
        pytest.skip("detect-secrets not installed (vaibify[replay])")


def _fsTranscript(iLines, sExtra=""):
    """Return JSONL text whose lines trip every redaction layer."""
    listLines = []
    for iLine in range(iLines):
        listLines.append(json.dumps({
            "iLine": iLine,
            "sText": (
                "token " + S_EXACT_SECRET + " ghp_"
                + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 key "
                + "AKIAIOSFODNN7EXAMPLE entropy "
                + "q8Zr4Lm2Xv7Tn1Kp9Wd3Hs6Jf0Gb5Yc8Ue2Ra4"
            ),
        }) + "\n")
    return "".join(listLines) + sExtra


class _ProcessPoolSpy(workerProcessPool.ProcessPoolExecutor):
    """A real process pool that records that it was started."""

    listStarted = []

    def __init__(self, *args, **kwargs):
        _ProcessPoolSpy.listStarted.append(kwargs.get("max_workers"))
        super().__init__(*args, **kwargs)


@pytest.fixture
def listPoolsStarted(monkeypatch):
    """Record every worker pool the sanitizer starts."""
    _ProcessPoolSpy.listStarted = []
    monkeypatch.setattr(
        workerProcessPool, "ProcessPoolExecutor", _ProcessPoolSpy,
    )
    return _ProcessPoolSpy.listStarted


@pytest.mark.falsification
def test_split_pieces_in_workers_match_one_whole_call(
    monkeypatch, listPoolsStarted,
):
    """Pieces scanned in worker processes join to the whole-text result.

    Kills: cutting a piece BEFORE its newline instead of after it.
    """
    _fnRequireSanitizer()
    monkeypatch.setattr(transcriptSanitizer, "I_PIECE_CHARACTERS", 700)
    monkeypatch.setattr(
        transcriptSanitizer, "I_WORKER_PROCESS_MINIMUM_CHARACTERS", 0,
    )
    listTexts = [_fsTranscript(12), _fsTranscript(3), "", "no newline"]
    listExpected = [
        ftResultSanitizeText(sText, [S_EXACT_SECRET]) for sText in listTexts
    ]
    listActual = flistSanitizeTextsInParallel(listTexts, [S_EXACT_SECRET])
    assert listPoolsStarted, "the batch never reached a worker process"
    assert listActual == listExpected
    assert S_EXACT_SECRET not in listActual[0][0]


@pytest.mark.falsification
def test_a_secret_spanning_lines_is_never_split_across_pieces(monkeypatch):
    """A multi-line exact secret disables the split, so it stays redacted.

    Split at a newline inside the secret, each half would be scanned
    alone and neither half is the secret: it would land in the record.

    Kills: splitting whatever the secrets contain.
    """
    _fnRequireSanitizer()
    monkeypatch.setattr(transcriptSanitizer, "I_PIECE_CHARACTERS", 10)
    sText = "before\n" + S_MULTILINE_SECRET + "\nafter\n"
    [(sSanitized, dictCounts)] = flistSanitizeTextsInParallel(
        [sText], [S_MULTILINE_SECRET],
    )
    assert (sSanitized, dictCounts) == ftResultSanitizeText(
        sText, [S_MULTILINE_SECRET],
    )
    assert "first-half-of-a-secret" not in sSanitized


@pytest.mark.falsification
def test_a_large_batch_runs_in_worker_processes(listPoolsStarted):
    """A first-pass-sized batch leaves the hub's interpreter.

    In a hub thread the scan held the interpreter lock for the whole
    pass and starved the event loop.

    Kills: always sanitizing in-process.
    """
    _fnRequireSanitizer()
    iLines = (
        transcriptSanitizer.I_WORKER_PROCESS_MINIMUM_CHARACTERS
        // len(_fsTranscript(1)) + 1
    )
    sText = _fsTranscript(iLines)
    assert len(sText) >= transcriptSanitizer.I_WORKER_PROCESS_MINIMUM_CHARACTERS
    [(sSanitized, _)] = flistSanitizeTextsInParallel([sText], [])
    assert len(listPoolsStarted) == 1
    assert listPoolsStarted[0] >= 1
    assert "AKIAIOSFODNN7EXAMPLE" not in sSanitized


@pytest.mark.falsification
def test_a_small_batch_stays_in_process(listPoolsStarted):
    """The few-kilobyte passes that follow a first capture spawn nothing.

    Starting a worker costs about as much as scanning 100 KB, and those
    passes arrive every 30 seconds while an agent works.

    Kills: always sanitizing in worker processes.
    """
    _fnRequireSanitizer()
    [(sSanitized, _)] = flistSanitizeTextsInParallel(
        [_fsTranscript(5)], [S_EXACT_SECRET],
    )
    assert listPoolsStarted == []
    assert S_EXACT_SECRET not in sSanitized
