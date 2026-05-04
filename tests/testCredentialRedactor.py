"""Tests for vaibify.reproducibility.credentialRedactor.

Covers the consolidated credential-scrubbing behaviour used by
:mod:`githubMirror`, :mod:`overleafMirror`, :mod:`zenodoClient`, and
:mod:`dependencyPinning`. Each predecessor's redactor had a slightly
different rule set; the union is exercised here so a regression in
any one caller still surfaces a failing test.
"""

import time

import pytest

from vaibify.reproducibility.credentialRedactor import (
    fsRedactCredentials,
    fsRedactUrlCredentials,
)


# ── URL-embedded credentials ────────────────────────────────────


def test_url_credentials_in_text_are_redacted():
    sLeaky = (
        "fatal: unable to access "
        "'https://user:secrettoken@git.overleaf.com/abc'"
    )
    sScrubbed = fsRedactCredentials(sLeaky)
    assert "secrettoken" not in sScrubbed
    assert "user:" not in sScrubbed
    assert "<redacted>@" in sScrubbed


def test_url_credentials_http_scheme_redacted():
    sLeaky = "could not connect to http://u:p@host.example/path"
    sScrubbed = fsRedactCredentials(sLeaky)
    assert "u:p@" not in sScrubbed
    assert "<redacted>@" in sScrubbed


# ── GitHub PAT prefixes ─────────────────────────────────────────


@pytest.mark.parametrize("sPrefix", [
    "ghp", "gho", "ghu", "ghs", "ghr", "github_pat",
])
def test_all_github_pat_prefixes_scrubbed(sPrefix):
    sToken = f"{sPrefix}_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    sScrubbed = fsRedactCredentials(f"oops the token was {sToken}")
    assert sToken not in sScrubbed


# ── Bearer / Authorization / Token / Password keyword lines ─────


def test_bearer_keyword_line_redacted():
    sScrubbed = fsRedactCredentials("Bearer abcdefghij1234567890")
    assert "abcdefghij1234567890" not in sScrubbed


def test_authorization_keyword_line_redacted():
    sScrubbed = fsRedactCredentials(
        "Authorization: Bearer secret-secret-secret"
    )
    assert "secret-secret-secret" not in sScrubbed


def test_token_keyword_line_redacted():
    sScrubbed = fsRedactCredentials("token = abc123xyz789")
    assert "abc123xyz789" not in sScrubbed


def test_password_keyword_line_redacted():
    sScrubbed = fsRedactCredentials("password: hunter2")
    assert "hunter2" not in sScrubbed
    assert "<redacted>" in sScrubbed


# ── Generic token-shape parameters ──────────────────────────────


def test_access_token_param_redacted():
    sScrubbed = fsRedactCredentials(
        "GET /api/foo?access_token=SECRETLEAKED HTTP/1.1"
    )
    assert "SECRETLEAKED" not in sScrubbed


def test_api_key_param_redacted():
    sScrubbed = fsRedactCredentials(
        "request failed: api_key=abc-123-def"
    )
    assert "abc-123-def" not in sScrubbed


# ── Empty input ─────────────────────────────────────────────────


def test_empty_string_returns_empty():
    assert fsRedactCredentials("") == ""


def test_none_returns_empty():
    assert fsRedactCredentials(None) == ""


def test_url_only_empty_returns_empty():
    assert fsRedactUrlCredentials("") == ""


# ── Multi-line input mixing benign and sensitive ────────────────


def test_multi_line_mixed_keeps_benign_redacts_sensitive():
    sInput = (
        "harmless line\n"
        "password: hunter2\n"
        "another harmless line\n"
        "Authorization: Bearer leaky-secret\n"
        "yet another harmless line"
    )
    sScrubbed = fsRedactCredentials(sInput)
    assert "harmless line" in sScrubbed
    assert "another harmless line" in sScrubbed
    assert "yet another harmless line" in sScrubbed
    assert "hunter2" not in sScrubbed
    assert "leaky-secret" not in sScrubbed


# ── Adversarial: benign string that LOOKS like a credential URL ─


def test_benign_url_with_user_thing_in_query_not_over_redacted():
    """A query value containing ``:`` and ``@`` must not look like creds.

    The redactor's ``user:token@`` regex requires the segment to be
    immediately after ``://`` (no slashes between). This adversarial
    input embeds the colon-at pattern inside a query value where the
    netloc contains no user-info; the URL must come through unchanged.
    """
    sInput = "see https://example.com/?path=user:thing@x for details"
    sScrubbed = fsRedactCredentials(sInput)
    assert "https://example.com/?path=user:thing@x" in sScrubbed
    assert "<redacted>@" not in sScrubbed


def test_email_address_in_text_not_redacted():
    sInput = "contact alice@example.com for bug reports"
    sScrubbed = fsRedactCredentials(sInput)
    assert "alice@example.com" in sScrubbed


# ── ReDoS: pathological inputs run in linear time ───────────────


def test_pathological_colon_input_is_linear_time():
    """Confirm the regexes terminate quickly on adversarial input.

    None of the redaction patterns use nested quantifiers, so they
    cannot exhibit catastrophic backtracking. This test is a guardrail
    against an accidental future change introducing one.
    """
    sLong = ":" * 50_000 + "X"
    sInput = "Authorization: " + sLong
    fStart = time.perf_counter()
    sResult = fsRedactCredentials(sInput)
    fElapsed = time.perf_counter() - fStart
    # 1.0s is generous; linear regex runs in milliseconds even at 50k.
    assert fElapsed < 1.0, (
        f"redactor took {fElapsed:.3f}s on a 50k-char input — "
        "possible ReDoS regression"
    )
    # And the line was redacted.
    assert sLong not in sResult


def test_pathological_at_sign_input_is_linear_time():
    """Repeating ``@``s in URL position must not blow up the URL regex."""
    sInput = "https://" + "a:b@" * 5_000 + "host.example/"
    fStart = time.perf_counter()
    fsRedactCredentials(sInput)
    fElapsed = time.perf_counter() - fStart
    assert fElapsed < 1.0


# ── fsRedactUrlCredentials specifics ────────────────────────────


def test_url_only_strips_user_info():
    sUrl = "https://user:tok@host/path?x=1"
    sScrubbed = fsRedactUrlCredentials(sUrl)
    assert "user:" not in sScrubbed
    assert "tok" not in sScrubbed
    assert "<redacted>@" in sScrubbed
    assert "host/path" in sScrubbed


def test_url_only_strips_access_token_query_param():
    sUrl = "https://api.example/v1/foo?access_token=SECRET&x=1"
    sScrubbed = fsRedactUrlCredentials(sUrl)
    assert "SECRET" not in sScrubbed
    assert "x=1" in sScrubbed


def test_url_only_strips_token_query_param():
    sUrl = "https://api.example/v1/foo?token=SECRET2"
    sScrubbed = fsRedactUrlCredentials(sUrl)
    assert "SECRET2" not in sScrubbed


def test_url_only_passes_through_non_url_input():
    sNotUrl = "this is just an error message"
    assert fsRedactUrlCredentials(sNotUrl) == sNotUrl


# ── Integration: legacy aliases still work ──────────────────────


def test_legacy_github_alias_still_routes_through_canonical():
    """``githubMirror.fsRedactStderr`` must remain a working alias."""
    from vaibify.reproducibility import githubMirror

    sLeaky = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    sScrubbed = githubMirror.fsRedactStderr(sLeaky)
    assert sLeaky not in sScrubbed


def test_legacy_overleaf_alias_still_routes_through_canonical():
    """``overleafMirror.fsRedactStderr`` must remain a working alias."""
    from vaibify.reproducibility import overleafMirror

    sLeaky = "https://user:secret@git.overleaf.com/abc"
    sScrubbed = overleafMirror.fsRedactStderr(sLeaky)
    assert "user:secret" not in sScrubbed
