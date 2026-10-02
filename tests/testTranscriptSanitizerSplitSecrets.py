"""A session secret split across a terminal line break is still redacted.

A terminal wraps a long token at the screen width, so a captured
transcript can carry the secret with a newline in the middle of it. The
exact-value layer matched only the unbroken text, so the two halves were
written into a public repository.
"""

import json

import pytest

from vaibify.gui.transcriptSanitizer import (
    S_SESSION_SECRET_CATEGORY, fbSanitizerAvailable, ftResultSanitizeText,
)

pytestmark = pytest.mark.skipif(
    not fbSanitizerAvailable(), reason="detect-secrets is not installed")

S_SECRET = "Zq7vK2mPxL9wRt4YbN8cHd3JfG6sUa5E"


@pytest.mark.falsification
@pytest.mark.parametrize("sBreak", ["\n", "\r\n"])
def testASecretSplitByALineBreakIsRedacted(sBreak):
    """Kills: matching only the secret's unbroken spelling."""
    sText = "token is " + S_SECRET[:13] + sBreak + S_SECRET[13:] + " ok"
    sSanitized, dictCounts = ftResultSanitizeText(sText, [S_SECRET])
    assert S_SECRET[:13] not in sSanitized
    assert S_SECRET[13:] not in sSanitized
    assert dictCounts[S_SESSION_SECRET_CATEGORY] == 1


def testASecretSplitInsideAJsonRecordIsRedactedAndStaysValidJson():
    jsonRecord = {"sText": "paste " + S_SECRET[:9] + "\n" + S_SECRET[9:]}
    sSanitized, _ = ftResultSanitizeText(json.dumps(jsonRecord), [S_SECRET])
    assert S_SECRET[:9] not in sSanitized
    assert S_SECRET[9:] not in sSanitized
    json.loads(sSanitized)


def testAnUnbrokenSecretStillCountsEachOccurrence():
    sText = S_SECRET + " and again " + S_SECRET
    sSanitized, dictCounts = ftResultSanitizeText(sText, [S_SECRET])
    assert S_SECRET not in sSanitized
    assert dictCounts[S_SESSION_SECRET_CATEGORY] == 2


def testProseWithoutTheSecretIsUntouched():
    sText = "an ordinary line\nand another"
    sSanitized, dictCounts = ftResultSanitizeText(sText, [S_SECRET])
    assert sSanitized == sText
    assert S_SESSION_SECRET_CATEGORY not in dictCounts
