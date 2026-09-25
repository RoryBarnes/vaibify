"""A transcript record is redacted as JSON, not as raw text.

Agent transcripts are JSONL. Scanned as raw text, a record's escapes
hid secrets: the ``n`` of a ``\\n`` escape joined the token after it, so
the vendor-prefix rule never saw ``ghp_`` at a word boundary, and an
escaped quote (``\\"``) defeated detect-secrets' keyword rule, so
``password = "..."`` inside a command passed through untouched. A
replacement that swallowed an escape's letter also left a lone
backslash, and the record stopped being valid JSON. Every test here
builds its record with ``json.dumps`` from the value a researcher would
actually have typed, so the escapes are the real ones.
"""

import json

import pytest

from vaibify.gui.transcriptSanitizer import (
    fbSanitizerAvailable,
    ftResultSanitizeText,
)


S_LOW_ENTROPY_TOKEN = "ghp_" + "aaaaaaaaaaaaaaaaaaaa1111111111111111"
S_PASSWORD = "hunter2hunter2xy"


def _fnRequireSanitizer():
    if not fbSanitizerAvailable():
        pytest.skip("detect-secrets not installed (vaibify[replay])")


def _fsSanitizedRecord(dictRecord):
    sSanitized, _ = ftResultSanitizeText(json.dumps(dictRecord), [])
    return sSanitized


@pytest.mark.falsification
def test_a_low_entropy_vendor_token_after_an_escape_is_redacted():
    """A prefixed token straight after a newline escape is still caught.

    Its suffix is too regular for the entropy rule, so the vendor
    prefix is the only thing that can identify it.

    Kills: scanning every record as raw text.
    """
    _fnRequireSanitizer()
    sSanitized = _fsSanitizedRecord(
        {"sText": "output:\n" + S_LOW_ENTROPY_TOKEN},
    )
    assert S_LOW_ENTROPY_TOKEN not in sSanitized
    assert "[REDACTED: vendor-token]" in sSanitized


@pytest.mark.falsification
def test_a_quoted_secret_inside_a_record_is_redacted():
    """``password = "..."`` inside a command is caught.

    That shape is ordinary in an agent's shell commands, and in JSON
    its quotes are escaped, which is what hid it from the raw scan.

    Kills: skipping the full scan of strings that needed escapes.
    """
    _fnRequireSanitizer()
    for sCommand in (
        'export password = "' + S_PASSWORD + '"',
        'x\npassword = "' + S_PASSWORD + '"',
    ):
        assert S_PASSWORD not in _fsSanitizedRecord({"sText": sCommand}), (
            sCommand
        )


@pytest.mark.falsification
def test_a_secret_named_by_its_key_is_redacted():
    """A value whose only tell is the key beside it is still caught.

    A decoded string on its own has lost its key, so the raw record's
    scan is what supplies the context.

    Kills: dropping the raw record's scan.
    """
    _fnRequireSanitizer()
    assert S_PASSWORD not in _fsSanitizedRecord({"password": S_PASSWORD})


@pytest.mark.falsification
def test_a_redacted_record_stays_valid_json_with_its_escapes():
    """Redaction leaves a parseable record whose escapes still decode.

    The raw path left ``\\[REDACTED`` behind: an invalid escape, and the
    newline it had swallowed was gone.

    Kills: redacting a changed record as raw text.
    """
    _fnRequireSanitizer()
    sToken = "q8Zr4Lm2Xv7Tn1Kp9Wd3Hs6Jf0Gb5Yc8Ue2Ra4"
    dictRecord = {"sText": "/tmp/paper.eps\n" + sToken + "\tdone"}
    dictBack = json.loads(_fsSanitizedRecord(dictRecord))
    assert sToken not in dictBack["sText"]
    assert dictBack["sText"].startswith("/tmp/paper.eps\n[REDACTED: ")
    assert dictBack["sText"].endswith("\tdone")


@pytest.mark.falsification
def test_a_record_with_nothing_to_redact_keeps_its_bytes():
    """An untouched record is returned byte for byte, not re-encoded.

    Kills: always re-encoding a parsed record.
    """
    _fnRequireSanitizer()
    sRecord = '{"sText": "plain words", "iCount": 3, "listTags": ["a"]}'
    sSanitized, dictCounts = ftResultSanitizeText(sRecord, [])
    assert (sSanitized, dictCounts) == (sRecord, {})
