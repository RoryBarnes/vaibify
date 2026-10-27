"""A program's request to set the clipboard waits for the researcher.

Inside a container there is no clipboard program, so Claude Code copies
-- /copy, its login URL -- by printing OSC 52, an escape sequence asking
the terminal to set the clipboard. The vendored xterm had no handler, so
every such copy was silently dropped. The pane now shows each request
with a preview and writes it only when the researcher clicks Copy.

The program here is real: a host project's pane is a real PTY on this
machine, and each request is printed into it by ``cat`` from a file the
test prepared, byte for byte as a program would print it. The clipboard
is observed by wrapping ``navigator.clipboard.writeText`` in an init
script rather than by reading it back, which needs no permission and so
runs on every engine; what the bytes reach is the wrapped call, which is
exactly the call the page makes.

Every test shares ONE page and ONE shell (``pageDashboard``): a project is
held by one browser session at a time, and a fresh page per test waited
on the previous page's claim. Each test is its own node, so each kills
its own recorded mutation, and each leaves the shell with no bar up.
"""

import base64
import itertools
import shlex
import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

F_SHELL_DEADLINE_SECONDS = 45.0
F_COPY_ARMING_SECONDS = 1.0
I_DECODED_LIMIT_BYTES = 1000000
I_ENCODED_LIMIT_CHARACTERS = 1400000

S_BAR = ".terminal-clipboard-request"
S_COPY = ".terminal-clipboard-request-copy"
S_PREVIEW = ".terminal-clipboard-request-preview"
S_HELD_NOTE = ".terminal-clipboard-request-held"

S_RECORDERS = """
window.listClipboardWrites = [];
window.listTerminalSends = [];
window.iLongestAtobInput = 0;
(function () {
    const fnRecordWrite = (sText) => {
        window.listClipboardWrites.push(String(sText));
        return Promise.resolve();
    };
    if (navigator.clipboard) {
        Object.defineProperty(navigator.clipboard, 'writeText', {
            configurable: true, value: fnRecordWrite,
        });
    } else {
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true, value: {writeText: fnRecordWrite},
        });
    }
    const fnOriginalAtob = window.atob.bind(window);
    window.atob = (sEncoded) => {
        window.iLongestAtobInput = Math.max(
            window.iLongestAtobInput, String(sEncoded).length);
        return fnOriginalAtob(sEncoded);
    };
    const fnOriginalSend = WebSocket.prototype.send;
    WebSocket.prototype.send = function (data) {
        if (String(this.url).indexOf('/ws/terminal/') !== -1) {
            if (typeof data === 'string') {
                window.listTerminalSends.push({sKind: 'text', sData: data});
            } else {
                const baBytes = data instanceof ArrayBuffer
                    ? new Uint8Array(data)
                    : new Uint8Array(
                        data.buffer, data.byteOffset, data.byteLength);
                window.listTerminalSends.push({
                    sKind: 'binary',
                    sData: Array.from(baBytes, (i) =>
                        String.fromCharCode(i)).join(''),
                });
            }
        }
        return fnOriginalSend.call(this, data);
    };
})();
"""

_iterMarkers = itertools.count(100)


def _fbaBuildClipboardWrite(sText, sSelection="c"):
    """Return the OSC 52 bytes a program prints to copy sText."""
    return (
        b"\x1b]52;" + sSelection.encode("ascii") + b";"
        + base64.b64encode(sText.encode("utf-8")) + b"\x07"
    )


def _fsVisiblePaneText(pageDashboard):
    """Return the visible tab's rows, whitespace collapsed."""
    return pageDashboard.evaluate(
        """() => {
            const el = Array.from(document.querySelectorAll('.xterm'))
                .find((e) => e.style.display !== 'none');
            const elRows = el && el.querySelector('.xterm-rows');
            return elRows ? elRows.textContent : '';
        }""",
    )


def _fnWaitForPaneText(pageDashboard, sExpected, sWhat):
    """Block until sExpected is visible in the pane, or fail saying why."""
    fStarted = time.monotonic()
    sPane = _fsVisiblePaneText(pageDashboard)
    while sExpected not in " ".join(sPane.split()):
        assert time.monotonic() - fStarted < F_SHELL_DEADLINE_SECONDS, (
            f"{sWhat} never appeared in the terminal pane. "
            f"Pane read: {sPane[-400:]}"
        )
        pageDashboard.wait_for_timeout(250)
        sPane = _fsVisiblePaneText(pageDashboard)


def _fnDialTheVisibleShell(pageDashboard):
    """Click into the visible pane and wait for the host shell's banner."""
    pageDashboard.click(".xterm:not([style*='display: none'])")
    _fnWaitForPaneText(
        pageDashboard, "YOUR OWN machine", "the host shell banner",
    )


def _fnOpenAHostShell(pageDashboard, serverHub):
    """Install the recorders, enter the host project, dial a shell."""
    pageDashboard.add_init_script(S_RECORDERS)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(".xterm", timeout=20000)
    _fnDialTheVisibleShell(pageDashboard)


def _fnTypeInTheShell(pageDashboard, sCommand):
    pageDashboard.click(".xterm:not([style*='display: none'])")
    pageDashboard.keyboard.type(sCommand)
    pageDashboard.keyboard.press("Enter")


def _fsCommandPrintingTheFile(pathDirectory, baBytes):
    """Write baBytes to a file; return (command, marker it prints)."""
    iMarker = next(_iterMarkers)
    pathFile = pathDirectory / f"request{iMarker}.bin"
    pathFile.write_bytes(baBytes)
    # The command line names the sum, the output prints it, so the
    # marker appears on screen only once the bytes have been printed.
    sCommand = (
        f"cat {shlex.quote(str(pathFile))}; echo; "
        f"echo PRINTED-$(({iMarker}+1))"
    )
    return sCommand, f"PRINTED-{iMarker + 1}"


def _fnPrintFromTheShell(pageDashboard, pathDirectory, baBytes):
    """Have the shell print baBytes into the pane, and wait for it."""
    sCommand, sMarker = _fsCommandPrintingTheFile(pathDirectory, baBytes)
    _fnTypeInTheShell(pageDashboard, sCommand)
    _fnWaitForPaneText(pageDashboard, sMarker, "the printed request")


def _flistClipboardWrites(pageDashboard):
    return pageDashboard.evaluate("() => window.listClipboardWrites")


def _fnClickCopyOnceArmed(pageDashboard):
    """Wait out the arming delay, then click Copy as a researcher would."""
    pageDashboard.wait_for_selector(f"{S_COPY}:not([disabled])",
                                    timeout=5000)
    pageDashboard.click(S_COPY)


def _fsPreviewText(pageDashboard):
    return pageDashboard.text_content(S_PREVIEW)


def _fnForgetRecordedWrites(pageDashboard):
    pageDashboard.evaluate("() => { window.listClipboardWrites = []; }")


def _fnDismissTheBar(pageDashboard):
    pageDashboard.click(".terminal-clipboard-request-button >> text=Dismiss")
    pageDashboard.wait_for_selector(S_BAR, state="detached", timeout=5000)


@pytest.fixture(autouse=True)
def fnReleaseTheProjectAfterwards(pageDashboard):
    """Release the host project before the page closes.

    A project is held by one browser session at a time, and a page that
    merely closes leaves its claim to the hub's reaper -- measured here
    at over ninety seconds before the next test could claim it. The
    release route ends the page's shell with proof first, as it does
    for a researcher who chooses Release.
    """
    yield
    pageDashboard.evaluate(
        """async (sName) => {
            try {
                await VaibifyApi.fdictPost('/api/registry/'
                    + encodeURIComponent(sName) + '/release', {});
            } catch (error) {}
        }""", S_HOST_PROJECT_READY)


@pytest.mark.falsification
def test_a_request_is_not_copied_when_it_arrives(
    pageDashboard, serverHub, tmp_path,
):
    """Arrival shows the bar; the clipboard is untouched until a click.

    Kills: writing the clipboard the moment a request arrives.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite("not yet copied"))
    pageDashboard.wait_for_selector(S_COPY, timeout=10000)
    pageDashboard.wait_for_timeout(1500)
    assert _flistClipboardWrites(pageDashboard) == [], (
        "the clipboard was written before any click: a program's "
        "request reached it on the program's word alone"
    )
    assert _fsPreviewText(pageDashboard) == "not yet copied"


@pytest.mark.falsification
def test_a_click_in_the_bars_first_second_does_nothing(
    pageDashboard, serverHub, tmp_path,
):
    """Copy is disabled for its first second, then works.

    A bar a program times to appear under the pointer must not take a
    click meant for whatever was there a moment before.

    Kills: arming Copy the moment its bar appears.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite("armed later"))
    pageDashboard.wait_for_selector(S_COPY, timeout=10000)
    dictBox = pageDashboard.query_selector(S_COPY).bounding_box()
    pageDashboard.mouse.click(
        dictBox["x"] + dictBox["width"] / 2,
        dictBox["y"] + dictBox["height"] / 2,
    )
    pageDashboard.wait_for_timeout(300)
    assert _flistClipboardWrites(pageDashboard) == [], (
        "a click in the bar's first second wrote the clipboard"
    )
    _fnClickCopyOnceArmed(pageDashboard)
    pageDashboard.wait_for_selector(S_BAR, state="detached", timeout=5000)
    assert _flistClipboardWrites(pageDashboard) == ["armed later"]


@pytest.mark.falsification
def test_copy_writes_exactly_the_decoded_text(
    pageDashboard, serverHub, tmp_path,
):
    """Non-ASCII text survives the trip, and the bar counts characters.

    Kills: decoding the payload as Latin-1 instead of UTF-8.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    sText = "na\xefve caf\xe9 \u2014 \u65e5\u672c\u8a9e \U0001f680\nsecond line"
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite(sText))
    pageDashboard.wait_for_selector(S_COPY, timeout=10000)
    sBar = pageDashboard.text_content(S_BAR)
    # Python counts code points, as the bar does: the rocket is one.
    assert f"wants to copy {len(sText)} characters" in sBar, sBar
    assert _fsPreviewText(pageDashboard) == (
        "na\xefve caf\xe9 \u2014 \u65e5\u672c\u8a9e \U0001f680\u21b5second line")
    _fnClickCopyOnceArmed(pageDashboard)
    pageDashboard.wait_for_selector(S_BAR, state="detached", timeout=5000)
    assert _flistClipboardWrites(pageDashboard) == [sText], (
        "Copy did not write exactly the text the program sent"
    )


@pytest.mark.falsification
def test_a_read_of_the_clipboard_is_never_answered(
    pageDashboard, serverHub, tmp_path,
):
    """A "?" read shows no bar and sends nothing back to the program.

    Everything the pane sends a program travels on the terminal socket,
    so its recorded frames observe every possible reply; the typed
    command itself carries no escape character.

    Kills: answering a read with the clipboard's contents.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    iSendsBefore = pageDashboard.evaluate(
        "() => window.listTerminalSends.length")
    _fnPrintFromTheShell(pageDashboard, tmp_path, b"\x1b]52;c;?\x07")
    pageDashboard.wait_for_timeout(1000)
    listSends = pageDashboard.evaluate(
        "() => window.listTerminalSends")[iSendsBefore:]
    listReplies = [
        dictSend for dictSend in listSends
        if dictSend["sKind"] == "binary" and "\x1b" in dictSend["sData"]
    ]
    assert listReplies == [], (
        "the pane answered a clipboard READ, handing the researcher's "
        f"clipboard to the program that asked: {listReplies!r}"
    )
    assert pageDashboard.query_selector(S_BAR) is None, (
        "a read is not a request to copy anything, but it showed a bar"
    )


@pytest.mark.falsification
def test_a_later_request_never_changes_the_bar_on_screen(
    pageDashboard, serverHub, tmp_path,
):
    """A request arriving while a bar shows is held, not swapped in.

    Kills: replacing the request on screen with the newer one.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite("first payload"))
    pageDashboard.wait_for_selector(S_BAR, timeout=10000)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite("second payload"))
    pageDashboard.wait_for_selector(
        f"{S_HELD_NOTE}:has-text('1 newer request is waiting')",
        timeout=10000)
    assert _fsPreviewText(pageDashboard) == "first payload", (
        "a newer request changed the bar already on screen"
    )
    _fnClickCopyOnceArmed(pageDashboard)
    pageDashboard.wait_for_function(
        "() => window.listClipboardWrites.length === 1", timeout=5000)
    assert _flistClipboardWrites(pageDashboard) == ["first payload"], (
        "the bar's Copy wrote a request that arrived after it was shown"
    )
    pageDashboard.wait_for_selector(
        f"{S_PREVIEW}:has-text('second payload')", timeout=5000)


@pytest.mark.falsification
def test_a_burst_leaves_one_bar_and_holds_the_newest(
    pageDashboard, serverHub, tmp_path,
):
    """Fifty requests at once: one bar, and the newest one waits.

    Kills: holding the earliest of the waiting requests instead.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    baBurst = b"".join(
        _fbaBuildClipboardWrite(f"burst-{iIndex:02d}")
        for iIndex in range(1, 51))
    _fnPrintFromTheShell(pageDashboard, tmp_path, baBurst)
    pageDashboard.wait_for_selector(
        f"{S_HELD_NOTE}:has-text('1 newer request is waiting')",
        timeout=10000)
    assert len(pageDashboard.query_selector_all(S_BAR)) == 1
    assert _fsPreviewText(pageDashboard) == "burst-01"
    _fnDismissTheBarFor(pageDashboard, "burst-01")
    assert _fsPreviewText(pageDashboard) == "burst-50", (
        "the request held behind the bar was not the newest one"
    )
    assert pageDashboard.text_content(S_HELD_NOTE) == ""


def _fnDismissTheBarFor(pageDashboard, sPreview):
    """Dismiss the bar showing sPreview and wait for its successor."""
    pageDashboard.click(".terminal-clipboard-request-button >> text=Dismiss")
    pageDashboard.wait_for_function(
        """(sPreview) => {
            const el = document.querySelector(
                '.terminal-clipboard-request-preview');
            return !el || el.textContent !== sPreview;
        }""", arg=sPreview, timeout=5000)


@pytest.mark.falsification
def test_an_encoded_request_over_the_cap_is_never_decoded(
    pageDashboard, serverHub, tmp_path,
):
    """Refused on its encoded length, before anything decodes it.

    The longest string any code hands to ``atob`` is recorded.

    Kills: dropping the encoded-length check, so the payload is
    decoded first.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    pageDashboard.evaluate("() => { window.iLongestAtobInput = 0; }")
    _fnPrintFromTheShell(
        pageDashboard, tmp_path,
        _fbaBuildClipboardWrite("a" * (I_DECODED_LIMIT_BYTES + 60000)))
    pageDashboard.wait_for_selector(
        f"{S_BAR}:has-text('more than 1 MB')", timeout=20000)
    iLongestAtobInput = pageDashboard.evaluate("() => window.iLongestAtobInput")
    assert iLongestAtobInput < I_ENCODED_LIMIT_CHARACTERS, (
        "an encoded payload over the cap was decoded before it was "
        f"refused ({iLongestAtobInput} characters reached atob)"
    )
    assert pageDashboard.query_selector(S_COPY) is None


@pytest.mark.falsification
def test_decoded_text_over_the_cap_is_refused(
    pageDashboard, serverHub, tmp_path,
):
    """Within the encoded cap, but past the text cap: refused, said so.

    Kills: dropping the decoded-size check.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path,
        _fbaBuildClipboardWrite("b" * (I_DECODED_LIMIT_BYTES + 20000)))
    pageDashboard.wait_for_selector(S_BAR, timeout=20000)
    sBar = pageDashboard.text_content(S_BAR)
    assert "more than 1 MB" in sBar and "nothing was copied" in sBar, (
        f"decoded text over the cap was offered for copying: {sBar[:200]}"
    )
    assert pageDashboard.query_selector(S_COPY) is None
    assert _flistClipboardWrites(pageDashboard) == []


S_HOSTILE_PREVIEW_TEXT = "<b>bold</b>\x01\x1b[31m\u202eevil\tend"


@pytest.mark.falsification
def test_the_preview_never_renders_markup(
    pageDashboard, serverHub, tmp_path,
):
    """A program's text in the preview stays text.

    Kills: building the preview with innerHTML.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite(S_HOSTILE_PREVIEW_TEXT))
    pageDashboard.wait_for_selector(S_PREVIEW, timeout=10000)
    assert pageDashboard.query_selector(f"{S_PREVIEW} b") is None, (
        "the preview rendered the program's text as markup"
    )
    assert _fsPreviewText(pageDashboard).startswith("<b>bold</b>")


@pytest.mark.falsification
def test_the_preview_shows_its_control_characters(
    pageDashboard, serverHub, tmp_path,
):
    """Control and reordering characters are drawn, never obeyed.

    A preview that let them act would show the researcher something
    other than what Copy writes.

    Kills: passing the preview through without making them visible.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite(S_HOSTILE_PREVIEW_TEXT))
    pageDashboard.wait_for_selector(S_PREVIEW, timeout=10000)
    assert _fsPreviewText(pageDashboard) == (
        "<b>bold</b>\u2401\u241b[31m<U+202E>evil\u2192end"
    )


def _fdictMeasureTheTerminal(pageDashboard):
    """Return the visible terminal's rows, screen box and resize count."""
    return pageDashboard.evaluate(
        """() => {
            const el = Array.from(document.querySelectorAll('.xterm'))
                .find((e) => e.style.display !== 'none');
            const dictBox = el.querySelector('.xterm-screen')
                .getBoundingClientRect();
            return {
                iRows: el.querySelectorAll('.xterm-rows > div').length,
                fWidth: dictBox.width,
                fHeight: dictBox.height,
                iResizeSends: window.listTerminalSends.filter(
                    (d) => d.sKind === 'text'
                        && d.sData.indexOf('"resize"') !== -1).length,
            };
        }""",
    )


@pytest.mark.falsification
def test_showing_and_removing_the_bar_never_resizes_the_program(
    pageDashboard, serverHub, tmp_path,
):
    """The bar overlays the text; the terminal's size never moves.

    A bar that took height would fire the pane's ResizeObserver, refit
    the terminal and send the program a resize -- the resize path this
    work deliberately leaves alone, triggered by a notice.

    Kills: placing the bar in the layout flow above the terminal.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    pageDashboard.wait_for_timeout(1500)
    dictBefore = _fdictMeasureTheTerminal(pageDashboard)
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite("overlay"))
    pageDashboard.wait_for_selector(S_BAR, timeout=10000)
    pageDashboard.wait_for_timeout(1500)
    dictWhileShown = _fdictMeasureTheTerminal(pageDashboard)
    _fnDismissTheBar(pageDashboard)
    pageDashboard.wait_for_timeout(1500)
    dictAfter = _fdictMeasureTheTerminal(pageDashboard)
    assert dictWhileShown == dictBefore, (
        f"showing the bar changed the terminal: {dictBefore} -> "
        f"{dictWhileShown}"
    )
    assert dictAfter == dictBefore, (
        f"removing the bar changed the terminal: {dictBefore} -> "
        f"{dictAfter}"
    )


def _fnClickTabLabel(pageDashboard, iTabIndex):
    pageDashboard.locator(".terminal-tab").nth(iTabIndex).click(
        position={"x": 12, "y": 8})


def _fiCountTabs(pageDashboard):
    return pageDashboard.locator(".terminal-tab").count()


def _fnOpenANewTabWithAShell(pageDashboard):
    pageDashboard.click(".terminal-pane-add")
    _fnDialTheVisibleShell(pageDashboard)


@pytest.mark.falsification
def test_a_background_tabs_request_waits_on_its_own_tab(
    pageDashboard, serverHub, tmp_path,
):
    """Shown only on the tab whose program sent it; marked meanwhile.

    Kills: rendering a request on the pane rather than on its tab.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnOpenANewTabWithAShell(pageDashboard)
    iSender = _fiCountTabs(pageDashboard) - 1
    sCommand, _ = _fsCommandPrintingTheFile(
        tmp_path, _fbaBuildClipboardWrite("from a background tab"))
    _fnTypeInTheShell(pageDashboard, "sleep 2; " + sCommand)
    _fnClickTabLabel(pageDashboard, iSender - 1)
    pageDashboard.locator(".terminal-tab").nth(iSender).locator(
        ".terminal-tab-clipboard-marker").wait_for(timeout=15000)
    assert pageDashboard.query_selector(S_BAR) is None, (
        "a background tab's request was shown over another tab's program"
    )
    _fnClickTabLabel(pageDashboard, iSender)
    pageDashboard.wait_for_selector(
        f"{S_PREVIEW}:has-text('from a background tab')", timeout=5000)
    assert pageDashboard.query_selector(
        ".terminal-tab-clipboard-marker") is None


@pytest.mark.falsification
def test_closing_a_tab_to_the_left_keeps_the_visible_tabs_request(
    pageDashboard, serverHub, tmp_path,
):
    """The active-tab index follows the tab on screen after a close.

    Closing a tab to the LEFT of the active one used to leave the index
    one place too far, on a hidden tab: the bar answered for the wrong
    program, and the tab bar highlighted a tab that was not showing.

    Kills: dropping the index adjustment for a close to the left.
    """
    _fnOpenAHostShell(pageDashboard, serverHub)
    _fnOpenANewTabWithAShell(pageDashboard)
    iHolder = _fiCountTabs(pageDashboard) - 1
    _fnPrintFromTheShell(
        pageDashboard, tmp_path, _fbaBuildClipboardWrite("still in view"))
    sHolderLabel = pageDashboard.locator(".terminal-tab").nth(
        iHolder).text_content()
    _fnOpenANewTabWithAShell(pageDashboard)
    _fnClickTabLabel(pageDashboard, iHolder)
    pageDashboard.wait_for_selector(
        f"{S_PREVIEW}:has-text('still in view')", timeout=5000)
    pageDashboard.locator(".terminal-tab").nth(iHolder - 1).locator(
        ".close-tab").click()
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "(i) => document.querySelectorAll('.terminal-tab').length === i",
        arg=iHolder + 1, timeout=5000)
    assert pageDashboard.text_content(".terminal-tab.active") == sHolderLabel, (
        "after closing the tab to its left, the highlighted tab is not "
        "the one on screen"
    )
    assert pageDashboard.query_selector(
        f"{S_PREVIEW}:has-text('still in view')") is not None, (
        "after closing the tab to its left, the visible tab's request "
        "vanished: the active index had moved onto a hidden tab"
    )
