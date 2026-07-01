"""Mutation-coverage tests for credentialRedactor.

These tests close coverage holes surfaced by mutation testing. Each
asserts the correct, unmutated behaviour and is constructed to FAIL
when its specific surviving mutant is applied.

SECURITY NOTE: several of these tests deliberately use KEYWORD-FREE
carrier messages (no occurrence of the words ``token``, ``password``,
``bearer``, or ``authorization``) so that the whole-line keyword
scrubber is NOT what catches the secret. That isolates the GitHub-token
regex and the sensitive-keyword list from one another — the redundancy
between them is exactly what the mutants exploit to survive.
"""

import pytest

from vaibify.reproducibility.credentialRedactor import (
    fsRedactCredentials,
    fsRedactUrlCredentials,
)

pytestmark = pytest.mark.falsification


# ── Hole: 'token' dropped from _LIST_SENSITIVE_KEYWORDS ──────────


def test_token_keyword_redacts_whole_line_not_just_keyword_span():
    """The whole line must die, not just the ``token here`` span.

    With ``token`` in the sensitive-keyword list the entire line is
    replaced. Without it, the fallback Bearer regex only consumes
    ``token here`` and leaves the trailing secret exposed.

    Kills: Remove 'token' from _LIST_SENSITIVE_KEYWORDS (lines 57-59).
    """
    sScrubbed = fsRedactCredentials(
        "the token here is invalid: SECRET123"
    )
    assert "SECRET123" not in sScrubbed


# ── Hole: 'ghr' / 'github_pat' dropped from _REGEX_GITHUB_TOKEN ──


@pytest.mark.parametrize("sPrefix", [
    "ghp", "gho", "ghu", "ghs", "ghr", "github_pat",
])
def test_github_prefixes_scrubbed_in_keyword_free_message(sPrefix):
    """Each GitHub-token prefix must be scrubbed by the regex alone.

    The carrier message contains no sensitive keyword, so the
    whole-line scrubber cannot mask a dropped prefix. Only the
    GitHub-token regex can catch the secret here.

    Kills: Remove any prefix (e.g. 'ghr', 'github_pat') from the
    _REGEX_GITHUB_TOKEN prefix alternation (line 51).
    """
    sToken = f"{sPrefix}_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    sScrubbed = fsRedactCredentials(
        f"unexpected value {sToken} in output"
    )
    assert sToken not in sScrubbed


# ── Hole: 'or' -> 'and' in _fsScrubUrlQueryParts netloc guard ───


def test_scheme_present_empty_netloc_short_circuits_unchanged():
    """A scheme-only URL with empty netloc must pass through unchanged.

    ``not scheme or not netloc`` short-circuits to True (netloc empty)
    and returns the candidate verbatim. Mutating the ``or`` to ``and``
    would instead strip the query, deleting ``keepme``.

    Kills: Change 'not result.scheme or not result.netloc' to
    '... and ...' in _fsScrubUrlQueryParts (line 128).
    """
    sInput = "https:///path?token=keepme"
    assert fsRedactUrlCredentials(sInput) == sInput
