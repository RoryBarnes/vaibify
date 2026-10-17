"""A step produced under another context says so, in words, and not as a failure.

The note is the server's: its message and the facts that differ come
from ``outputProvenance``, and the step card renders them verbatim. Two
properties only a browser shows: the exact phrase reaches the page --
never "a different computer" -- and the note is not painted in the
colours of a failure, because a recorded difference of hardware or
settings is not one (design, "Hardware and settings provenance").

Not a falsification entry; the wording and the neutral rule are pinned
here as a contract of the rendered page.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_THE_NOTE = """(dictNote) => {
    const elHost = document.createElement('div');
    elHost.innerHTML = VaibifyStepRenderer.fsRenderOutputContextNote(
        {dictOutputContextNotes: {"0": dictNote}}, 0);
    document.body.appendChild(elHost);
    const elNote = elHost.querySelector('.output-context-note');
    const elProbe = document.createElement('span');
    elProbe.style.color = 'var(--color-red)';
    document.body.appendChild(elProbe);
    const dictSeen = {
        bFound: Boolean(elNote),
        sText: elNote ? elNote.textContent : '',
        sClass: elNote ? elNote.className : '',
        sColor: elNote ? getComputedStyle(elNote).color : '',
        sFailureColor: getComputedStyle(elProbe).color,
    };
    elHost.remove();
    elProbe.remove();
    return dictSeen;
}"""


def test_a_note_names_the_differing_facts_neutrally(pageDashboard, serverHub):
    """The server's phrase and facts, in a muted note, never red."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictSeen = pageDashboard.evaluate(_S_RENDER_THE_NOTE, {
        "sMessage": "Produced under different execution settings or hardware",
        "listPaths": ["Step/values.json"],
        "listDifferences": [{"sFact": "x86-64 level", "sThere": "x86-64-v4",
                             "sHere": "x86-64-v3"}],
    })
    assert dictSeen["bFound"], dictSeen
    assert dictSeen["sText"] == (
        "Produced under different execution settings or hardware "
        "(x86-64 level: was x86-64-v4, now x86-64-v3).")
    assert "computer" not in dictSeen["sText"]
    for sWord in ("fail", "error", "diverged", "danger"):
        assert sWord not in dictSeen["sClass"]
    assert dictSeen["sColor"] != dictSeen["sFailureColor"]
    assert pageDashboard.listPageErrors == []


def test_no_note_is_drawn_for_a_step_without_one(pageDashboard, serverHub):
    """A step the server has nothing to say about renders nothing."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sHtml = pageDashboard.evaluate(
        "() => VaibifyStepRenderer.fsRenderOutputContextNote("
        "{dictOutputContextNotes: {}}, 0)")
    assert sHtml == ""
    assert pageDashboard.listPageErrors == []
