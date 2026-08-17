"""The raw-error redactor, driven in a real browser.

The redaction rules have a false-positive history: the bearer-token
pattern's character class included "/", so any absolute path 40+
characters long — every host project path — was eaten whole and the
raw-error pane showed ``[redacted-token].json`` where the diagnostic
detail should have been (live, 2026-08-17). These tests pin both
directions: real token shapes are still redacted, and ordinary file
paths survive.
"""

import pytest

pytestmark = pytest.mark.browser

S_LONG_HOST_PATH = (
    "/Users/researcher/exampleHostProject/StepDirectory/output.json"
)


def _fsRedact(pageDashboard, serverHub, sRaw):
    """Run the production sanitizer on one string in the loaded page."""
    if "about:blank" in pageDashboard.url:
        pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    return pageDashboard.evaluate(
        "(sRaw) => VaibifySyncManager.fsSanitizeRawError(sRaw)", sRaw,
    )


@pytest.mark.falsification
def testAnOrdinaryFilePathSurvivesRedaction(pageDashboard, serverHub):
    """A pathspec error keeps its path — the pane's whole purpose.

    Kills: restoring "/" to the bearer-token character class — the
    path is then one 40+ character "token" and the raw-error pane
    hides exactly the detail it exists to show.
    """
    sMessage = (
        f"fatal: pathspec '{S_LONG_HOST_PATH}' did not match any files"
    )
    assert _fsRedact(pageDashboard, serverHub, sMessage) == sMessage


@pytest.mark.falsification
def testRealTokenShapesAreStillRedacted(pageDashboard, serverHub):
    """Loosening the class must not stop redacting actual secrets.

    Kills: deleting the bearer-token replacement line — a 40+
    character slash-free secret then reaches the pane verbatim.
    """
    sClassic = "gho_" + "a1B2" * 10
    sBare = "A" * 25 + "z9" * 10
    sRedacted = _fsRedact(
        pageDashboard, serverHub,
        f"auth failed for {sClassic} and {sBare}",
    )
    assert sClassic not in sRedacted
    assert sBare not in sRedacted
    assert "[redacted-token]" in sRedacted


def testUrlUserinfoIsStillRedacted(pageDashboard, serverHub):
    """A token embedded in a remote URL never reaches the pane."""
    sRedacted = _fsRedact(
        pageDashboard, serverHub,
        "fatal: unable to access "
        "'https://x-access-token:ghp_secret@github.com/o/r.git'",
    )
    assert "ghp_secret" not in sRedacted
    assert "[redacted-userinfo]@github.com" in sRedacted
