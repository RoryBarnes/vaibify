"""Capture-time sanitization for Development Transcript records.

Captured agent transcripts land inside the project repository, and
the repository is public or will be — so secrets must be scrubbed
BEFORE the record ever touches it, not at publish time. Three layers:

1. **Exact-value redaction** of every vaibify session secret the hub
   knows (per-container agent token, hub session token, everything in
   the container's session env file). These are replaced wherever
   they appear.
2. **detect-secrets pattern detectors** (the battle-tested rules,
   installed as ``vaibify[replay]``): AWS keys, GitHub/GitLab/Slack/
   Stripe tokens, private-key blocks, JWTs, and the rest of the
   upstream-maintained catalog. The two *entropy* plugins are
   deliberately excluded: via ``scan.scan_line`` they apply no
   usable threshold and flag ordinary English words (verified
   empirically — 'the', 'dog', and 'photometry' all matched), which
   would shred a transcript.
3. **A conservative entropy supplement** replacing them: a token of
   32+ characters containing both letters and digits whose Shannon
   entropy exceeds 4.5 bits/character is redacted. The guards keep
   code identifiers, git hashes, and prose intact while catching the
   long random strings real credentials are made of.

Every redaction is an explicit ``[REDACTED: <category>]`` marker and
is counted per category; the UI must present the result as a
*redacted transcript*, never as raw tokens. Matches shorter than 8
characters are ignored — replacing tiny substrings globally would
mangle unrelated text.
"""

__all__ = [
    "S_SESSION_SECRET_CATEGORY",
    "S_ENTROPY_CATEGORY",
    "fbSanitizerAvailable",
    "flistSanitizeTextsInParallel",
    "ftResultSanitizeText",
]

import itertools
import math
import multiprocessing
import os
import re
from concurrent.futures import ProcessPoolExecutor

S_SESSION_SECRET_CATEGORY = "vaibify-session-secret"
S_ENTROPY_CATEGORY = "high-entropy-string"
_I_MINIMUM_PATTERN_SECRET_LENGTH = 8
_I_MINIMUM_ENTROPY_TOKEN_LENGTH = 32
_F_ENTROPY_LIMIT_BITS = 4.5
# Measured on the hub's host: starting one spawned worker costs about
# 0.5 s, the time the sanitizer takes over roughly 100 KB. Below a
# megabyte the work stays in-process; the passes that follow a first
# capture carry a few kilobytes each, every 30 seconds.
I_WORKER_PROCESS_MINIMUM_CHARACTERS = 1_000_000
I_PIECE_CHARACTERS = 2_000_000

_SET_EXCLUDED_PLUGIN_TYPES = frozenset({
    "Base64 High Entropy String",
    "Hex High Entropy String",
})

_REGEX_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")

# Vendor token prefixes whose suffix is random: the prefix alone
# identifies the credential class even when the suffix's sampled
# entropy dips below the general limit.
_REGEX_PREFIXED_TOKEN = re.compile(
    r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|"
    r"sk-(?:proj-|ant-)?|xox[baprs]-)[A-Za-z0-9_\-]{10,}"
)


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


def _ffFractionalShannonEntropy(sToken):
    """Return the Shannon entropy of a token in bits per character."""
    dictFrequency = {}
    for sCharacter in sToken:
        dictFrequency[sCharacter] = dictFrequency.get(sCharacter, 0) + 1
    fEntropy = 0.0
    for iCount in dictFrequency.values():
        fProbability = iCount / len(sToken)
        fEntropy -= fProbability * math.log2(fProbability)
    return fEntropy


def _fbTokenLooksSecret(sToken):
    """Apply the guards that keep identifiers and hashes intact."""
    if len(sToken) < _I_MINIMUM_ENTROPY_TOKEN_LENGTH:
        return False
    if not re.search(r"[0-9]", sToken):
        return False
    if not re.search(r"[A-Za-z]", sToken):
        return False
    if "[REDACTED" in sToken:
        return False
    return _ffFractionalShannonEntropy(sToken) >= _F_ENTROPY_LIMIT_BITS


def _fsRedactSupplementalPatterns(sLine, dictCounts):
    """Redact prefixed vendor tokens and high-entropy strings."""
    def _fsReplacePrefixed(match):
        dictCounts["vendor-token"] = (
            dictCounts.get("vendor-token", 0) + 1
        )
        return _fsMarker("vendor-token")

    sLine = _REGEX_PREFIXED_TOKEN.sub(_fsReplacePrefixed, sLine)

    def _fsReplaceEntropy(match):
        if not _fbTokenLooksSecret(match.group(0)):
            return match.group(0)
        dictCounts[S_ENTROPY_CATEGORY] = (
            dictCounts.get(S_ENTROPY_CATEGORY, 0) + 1
        )
        return _fsMarker(S_ENTROPY_CATEGORY)

    return _REGEX_ENTROPY_CANDIDATE.sub(_fsReplaceEntropy, sLine)


def _fsRedactLinePatterns(sLine, dictCounts):
    """Scan one line with detect-secrets' pattern detectors."""
    from detect_secrets.core import scan
    for secretFound in scan.scan_line(sLine):
        if secretFound.type in _SET_EXCLUDED_PLUGIN_TYPES:
            continue
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
    return _fsRedactSupplementalPatterns(sLine, dictCounts)


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


def flistSanitizeTextsInParallel(listTexts, listExactSecrets=None):
    """Return :func:`ftResultSanitizeText` of each text, in order.

    The scan is pure-Python regex work that holds the interpreter lock,
    so run in a hub thread it starves the event loop: a 34 MB first
    capture took about 14 minutes inside the hub against 6.6 standalone,
    and the dashboard went sluggish for all of it. Large batches
    therefore run in worker processes, on every core but one.

    Each text is split into pieces at line boundaries so a single long
    transcript spreads across workers too. That is exact, not an
    approximation: the scan is line-local and no redacted secret spans
    a newline, so sanitizing the pieces and joining them yields the
    bytes one call over the whole text would. An exact secret that DOES
    contain a newline disables the split, as it disables incremental
    capture.
    """
    bSplittable = not any(
        "\n" in sSecret for sSecret in listExactSecrets or []
    )
    listPieces = []
    listOwners = []
    for iText, sText in enumerate(listTexts):
        listSplit = (
            _flistSplitAtLineBoundaries(sText, I_PIECE_CHARACTERS)
            if bSplittable else [sText]
        )
        listPieces.extend(listSplit)
        listOwners.extend([iText] * len(listSplit))
    listResults = _flistSanitizePieces(listPieces, listExactSecrets)
    return _flistJoinPieceResults(len(listTexts), listOwners, listResults)


def _flistSplitAtLineBoundaries(sText, iPieceCharacters):
    """Split text into pieces of about ``iPieceCharacters`` at line ends."""
    listPieces = []
    iStart = 0
    while len(sText) - iStart > iPieceCharacters:
        iNewline = sText.find("\n", iStart + iPieceCharacters)
        if iNewline < 0:
            break
        listPieces.append(sText[iStart:iNewline + 1])
        iStart = iNewline + 1
    if iStart < len(sText) or not listPieces:
        listPieces.append(sText[iStart:])
    return listPieces


def _flistSanitizePieces(listPieces, listExactSecrets):
    """Sanitize pieces in-process when small, in spawned workers when not.

    ``spawn`` rather than the platform default because the hub is
    multi-threaded, and forking a threaded process can copy a lock some
    other thread holds into a child that will never release it.
    """
    iCharacters = sum(len(sPiece) for sPiece in listPieces)
    if iCharacters < I_WORKER_PROCESS_MINIMUM_CHARACTERS:
        return [
            ftResultSanitizeText(sPiece, listExactSecrets)
            for sPiece in listPieces
        ]
    iWorkers = min(len(listPieces), max(1, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(
        max_workers=iWorkers,
        mp_context=multiprocessing.get_context("spawn"),
    ) as executorPool:
        return list(executorPool.map(
            ftResultSanitizeText, listPieces,
            itertools.repeat(listExactSecrets),
        ))


def _flistJoinPieceResults(iTexts, listOwners, listResults):
    """Reassemble per-piece results into one result per original text."""
    listSanitized = [[] for _ in range(iTexts)]
    listCounts = [{} for _ in range(iTexts)]
    for iOwner, (sSanitized, dictCounts) in zip(listOwners, listResults):
        listSanitized[iOwner].append(sSanitized)
        for sCategory, iCount in dictCounts.items():
            listCounts[iOwner][sCategory] = (
                listCounts[iOwner].get(sCategory, 0) + iCount
            )
    return [
        ("".join(listText), dictCounts)
        for listText, dictCounts in zip(listSanitized, listCounts)
    ]
