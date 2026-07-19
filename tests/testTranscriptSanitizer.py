"""Tests for capture-time transcript sanitization.

These are falsification tests in the repo's sense: the load-bearing
assertion is that the planted secret's plaintext is ABSENT from the
output, not merely that a marker appeared. Skips cleanly when the
optional detect-secrets dependency (vaibify[replay]) is missing.
"""

import pytest

from vaibify.gui.transcriptSanitizer import (
    S_SESSION_SECRET_CATEGORY,
    fbSanitizerAvailable,
    ftResultSanitizeText,
)


S_FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
S_FAKE_SESSION_TOKEN = "vaibify-token-1234567890abcdef"


def _fnRequireSanitizer():
    if not fbSanitizerAvailable():
        pytest.skip("detect-secrets not installed (vaibify[replay])")


def test_planted_aws_key_plaintext_is_absent():
    _fnRequireSanitizer()
    sText = 'config = {"aws_key": "' + S_FAKE_AWS_KEY + '"}\n'
    sSanitized, dictCounts = ftResultSanitizeText(sText)
    assert S_FAKE_AWS_KEY not in sSanitized
    assert "[REDACTED: " in sSanitized
    assert sum(dictCounts.values()) >= 1


def test_planted_session_token_plaintext_is_absent():
    _fnRequireSanitizer()
    sText = (
        "curl -H 'X-Vaibify-Session: " + S_FAKE_SESSION_TOKEN + "'\n"
        "echo " + S_FAKE_SESSION_TOKEN + "\n"
    )
    sSanitized, dictCounts = ftResultSanitizeText(
        sText, [S_FAKE_SESSION_TOKEN],
    )
    assert S_FAKE_SESSION_TOKEN not in sSanitized
    assert dictCounts[S_SESSION_SECRET_CATEGORY] == 2
    assert sSanitized.count(
        "[REDACTED: " + S_SESSION_SECRET_CATEGORY + "]",
    ) == 2


def test_clean_text_passes_through_unchanged():
    _fnRequireSanitizer()
    sText = "just an ordinary line about stellar photometry\n"
    sSanitized, dictCounts = ftResultSanitizeText(sText)
    assert sSanitized == sText
    assert dictCounts == {}


def test_empty_and_short_exact_secrets_are_ignored():
    _fnRequireSanitizer()
    sText = "the word cat appears here\n"
    sSanitized, dictCounts = ftResultSanitizeText(
        sText, ["", "cat"],
    )
    assert sSanitized == sText
    assert dictCounts == {}


def test_unavailable_sanitizer_refuses_instead_of_passing_raw(
    monkeypatch,
):
    monkeypatch.setattr(
        "vaibify.gui.transcriptSanitizer.fbSanitizerAvailable",
        lambda: False,
    )
    with pytest.raises(RuntimeError):
        ftResultSanitizeText("anything", [])
