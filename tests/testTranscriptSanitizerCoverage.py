"""Behaviour tests for the agent-transcript secret sanitizer.

A transcript is captured into a public repo, so a leaked credential is
permanent. These tests exercise the redaction layers — exact known
secrets, detect-secrets pattern detectors, vendor-prefixed tokens, and
the guarded Shannon-entropy fallback — asserting that secrets are
redacted and that benign identifiers, hashes, and prose survive.
"""

import pytest

from vaibify.gui import transcriptSanitizer as ts


def test_sanitizer_reports_available():
    assert ts.fbSanitizerAvailable() is True


def test_exact_session_secret_is_redacted_everywhere():
    sSecret = "s3ss10n-Abc123Def456"
    sText = f"token={sSecret} and again {sSecret}"
    sOut, dictCounts = ts.ftResultSanitizeText(sText, [sSecret])
    assert sSecret not in sOut
    assert dictCounts[ts.S_SESSION_SECRET_CATEGORY] == 2


def test_short_exact_secret_is_ignored():
    """A <4-char 'secret' is not redacted — too short to be meaningful."""
    sOut, dictCounts = ts.ftResultSanitizeText("value ab here", ["ab"])
    assert "ab" in sOut
    assert ts.S_SESSION_SECRET_CATEGORY not in dictCounts


def test_high_entropy_token_is_redacted():
    sToken = "aZ9qK3mX7pR2wL8vT5nB4jH6dF1cS0yG"
    sOut, dictCounts = ts.ftResultSanitizeText(f"key: {sToken}")
    assert sToken not in sOut
    assert dictCounts.get(ts.S_ENTROPY_CATEGORY, 0) >= 1


def test_plain_english_and_identifiers_survive():
    sText = "the quick brown fox jumps; fnRenderStepList called twice"
    sOut, dictCounts = ts.ftResultSanitizeText(sText)
    assert "quick brown fox" in sOut
    assert "fnRenderStepList" in sOut


def test_low_entropy_token_is_not_flagged():
    # 32+ chars with a digit and letters, but far too repetitive.
    assert ts._fbTokenLooksSecret("a1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") is False


def test_token_without_digit_is_not_secret():
    # 32+ letters, no digit — passes the length guard, fails the digit one.
    assert ts._fbTokenLooksSecret("abcdefghijklmnopqrstuvwxyzABCDEFG") is False


def test_token_without_letter_is_not_secret():
    # 32+ digits, no letter.
    assert ts._fbTokenLooksSecret("01234567890123456789012345678901") is False


def test_short_token_is_not_secret():
    assert ts._fbTokenLooksSecret("aZ9") is False


def test_already_redacted_token_is_left_alone():
    sToken = "[REDACTED: entropy]aZ9qK3mX7pR2wL8vT5nB4jH"
    assert ts._fbTokenLooksSecret(sToken) is False


def test_entropy_of_uniform_string_is_zero():
    assert ts._fFractionalShannonEntropy("aaaa") == 0.0


def test_entropy_of_mixed_string_is_positive():
    assert ts._fFractionalShannonEntropy("abcd") > 0.0


def test_vendor_prefixed_token_is_redacted():
    sToken = "sk-ant-api03-abcdefghijklmno"
    dictCounts = {}
    sOut = ts._fsRedactSupplementalPatterns(f"using {sToken}", dictCounts)
    assert sToken not in sOut
    assert dictCounts.get("vendor-token", 0) == 1


def test_marker_format_names_the_category():
    assert ts._fsMarker("entropy") == "[REDACTED: entropy]"


def test_multiline_text_is_sanitized_per_line():
    sToken = "aZ9qK3mX7pR2wL8vT5nB4jH6dF1cS0yG"  # 32 chars, entropy 5.0
    sText = f"line one is fine\nsecret {sToken} here"
    sOut, _ = ts.ftResultSanitizeText(sText)
    assert "line one is fine" in sOut
    assert sToken not in sOut


def test_unavailable_scanner_refuses_rather_than_leaks(monkeypatch):
    """With detect-secrets absent, capture must raise, not pass text."""
    monkeypatch.setattr(ts, "fbSanitizerAvailable", lambda: False)
    with pytest.raises(RuntimeError):
        ts.ftResultSanitizeText("anything")
