"""Capture-time sanitization for Development Transcript records.

Captured agent transcripts land inside the project repository, and
the repository is public or will be — so secrets must be scrubbed
BEFORE the record ever touches it, not at publish time. Two layers:

1. **Exact-value redaction** of every vaibify session secret the hub
   knows (per-container agent token, hub session token, everything in
   the container's session env file). These are replaced wherever
   they appear, at any length.
2. **detect-secrets** (the battle-tested scanner, an optional
   dependency installed as ``vaibify[replay]``) line-scans for
   credential patterns and high-entropy strings. Matches shorter than
   8 characters are ignored — replacing tiny substrings globally
   would mangle unrelated text, and a sub-8-character credential is
   noise for this scanner.

Every redaction is an explicit ``[REDACTED: <category>]`` marker and
is counted per category; the UI must present the result as a
*redacted transcript*, never as raw tokens.
"""

__all__ = [
    "S_SESSION_SECRET_CATEGORY",
    "fbSanitizerAvailable",
    "ftResultSanitizeText",
]

S_SESSION_SECRET_CATEGORY = "vaibify-session-secret"
_I_MINIMUM_PATTERN_SECRET_LENGTH = 8


def fbSanitizerAvailable():
    """Return True iff the detect-secrets scanner can be imported."""
    try:
        import detect_secrets  # noqa: F401 — availability probe
    except ImportError:
        return False
    return True


def _fsMarker(sCategory):
    return "[REDACTED: " + sCategory + "]"


def _ftRedactExactSecrets(sText, listExactSecrets, dictCounts):
    """Replace every occurrence of each known session secret."""
    for sSecret in listExactSecrets or []:
        if not sSecret or len(sSecret) < 4:
            continue
        iOccurrences = sText.count(sSecret)
        if iOccurrences == 0:
            continue
        sText = sText.replace(
            sSecret, _fsMarker(S_SESSION_SECRET_CATEGORY),
        )
        dictCounts[S_SESSION_SECRET_CATEGORY] = (
            dictCounts.get(S_SESSION_SECRET_CATEGORY, 0) + iOccurrences
        )
    return sText


def _fsRedactLinePatterns(sLine, dictCounts):
    """Scan one line with detect-secrets and redact each match."""
    from detect_secrets.core import scan
    for secretFound in scan.scan_line(sLine):
        sValue = secretFound.secret_value or ""
        if len(sValue) < _I_MINIMUM_PATTERN_SECRET_LENGTH:
            continue
        if sValue not in sLine:
            continue
        iOccurrences = sLine.count(sValue)
        sLine = sLine.replace(sValue, _fsMarker(secretFound.type))
        dictCounts[secretFound.type] = (
            dictCounts.get(secretFound.type, 0) + iOccurrences
        )
    return sLine


def ftResultSanitizeText(sText, listExactSecrets=None):
    """Return ``(sSanitized, dictCountsByCategory)`` for one text.

    Raises ``RuntimeError`` when detect-secrets is unavailable — the
    caller must refuse to capture rather than silently landing an
    unscanned transcript in a public repository.
    """
    if not fbSanitizerAvailable():
        raise RuntimeError(
            "detect-secrets is not installed; install vaibify[replay] "
            "to enable transcript capture."
        )
    from detect_secrets.settings import default_settings
    dictCounts = {}
    sText = _ftRedactExactSecrets(sText, listExactSecrets, dictCounts)
    listSanitized = []
    with default_settings():
        for sLine in sText.split("\n"):
            listSanitized.append(_fsRedactLinePatterns(sLine, dictCounts))
    return "\n".join(listSanitized), dictCounts
