"""The dashboard's Prompt Record poll declares itself automatic.

The backend stands an automatic capture down behind one already in
flight instead of queuing a second full pass; a poll that forgot the
flag would be served as a researcher's request and queue. The browser
lane EXECUTES this poll
(``tests/browser/testThePromptRecordNamesWhatItLeftOut.py``); this
string-presence check exists so the mutation has a registry entry of
its own, because an entry names exactly one test.
"""

import os

import pytest

S_POLLING_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static", "scriptPolling.js",
)


@pytest.mark.falsification
def test_the_capture_poll_url_carries_the_automatic_flag():
    """Every capture URL in the poller carries ``bAutomatic=true``.

    Kills: dropping ``?bAutomatic=true`` from the poller's URL.
    """
    with open(S_POLLING_SCRIPT_PATH, encoding="utf-8") as filePolling:
        sSource = filePolling.read()
    iCaptureUrls = sSource.count('"/prompt-record/capture')
    assert iCaptureUrls == 1, iCaptureUrls
    assert '"/prompt-record/capture?bAutomatic=true"' in sSource
