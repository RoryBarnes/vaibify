"""A viewer shows its latest display, whatever order the answers arrive in.

Every viewer display that fetches -- text, image, PDF -- painted its
answer whenever it arrived, so a slow fetch for an EARLIER display
repainted over whatever had replaced it. Found by external review on
the Level 3 viewer: the "this attempt produced no reproduced manifest"
statement was overwritten by a previous attempt's manifest, still in
flight, which presents an old result as the new one. Each display now
claims its viewport, and an answer paints only while its claim is the
latest.

Driven by holding the earlier fetch open in a real browser and
releasing it after the newer display has painted.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests,
)


pytestmark = pytest.mark.browser

S_NO_MANIFEST = "This attempt produced no reproduced manifest"


def _flistHoldFetches(pageDashboard, sPattern):
    listHeld = []
    pageDashboard.route(sPattern, lambda route: listHeld.append(route))
    return listHeld


def _fnWaitForHeld(pageDashboard, listHeld):
    for _ in range(40):
        if listHeld:
            return
        pageDashboard.wait_for_timeout(100)
    assert listHeld, "the earlier display never fetched"


def _fsViewportText(pageDashboard, sLetter):
    return pageDashboard.locator("#viewport" + sLetter).inner_text()


@pytest.mark.falsification
def test_an_earlier_manifest_never_replaces_the_no_manifest_statement(
    pageDashboard, serverHub,
):
    """Kills: painting a text answer without checking its claim."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    listHeld = _flistHoldFetches(pageDashboard, "**/api/figure/**/prior.sha256")
    pageDashboard.evaluate("""() => VaibifyFigureViewer
        .fnDisplayRecordInViewer('B', 'prior.sha256', null)""")
    _fnWaitForHeld(pageDashboard, listHeld)
    pageDashboard.evaluate("""(sText) => VaibifyFigureViewer
        .fnShowPlaceholderInViewer('B',
            '<span class="placeholder">' + sText + '</span>',
            'no reproduced manifest')""", S_NO_MANIFEST)
    assert S_NO_MANIFEST in _fsViewportText(pageDashboard, "B")
    listHeld[0].fulfill(status=200, content_type="text/plain",
                        body="AN EARLIER ATTEMPT'S MANIFEST")
    pageDashboard.wait_for_timeout(700)
    sShown = _fsViewportText(pageDashboard, "B")
    assert S_NO_MANIFEST in sShown and "EARLIER" not in sShown, (
        f"a late answer repainted the viewer: {sShown!r}")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_late_missing_file_never_replaces_a_newer_file(
    pageDashboard, serverHub,
):
    """Kills: painting a failure without checking its claim."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    listHeld = _flistHoldFetches(pageDashboard, "**/api/figure/**/older.txt")
    pageDashboard.route(
        "**/api/figure/**/newer.txt",
        lambda route: route.fulfill(status=200, content_type="text/plain",
                                    body="THE NEWER FILE"))
    pageDashboard.evaluate("""() => VaibifyFigureViewer
        .fnDisplayFileInViewer('A', 'older.txt', '')""")
    _fnWaitForHeld(pageDashboard, listHeld)
    pageDashboard.evaluate("""() => VaibifyFigureViewer
        .fnDisplayFileInViewer('A', 'newer.txt', '')""")
    pageDashboard.wait_for_function(
        "() => document.getElementById('viewportA')"
        ".textContent.includes('THE NEWER FILE')", timeout=10000)
    listHeld[0].fulfill(status=404, body="")
    pageDashboard.wait_for_timeout(700)
    sShown = _fsViewportText(pageDashboard, "A")
    assert "THE NEWER FILE" in sShown, (
        f"a late failure for an earlier file repainted the viewer: {sShown!r}")
    assert pageDashboard.listPageErrors == []


def _fbaBuildOneSquarePdf():
    """Return a minimal one-page PDF that PDF.js renders for real."""
    baStream = b"0 0 1 rg 10 10 80 80 re f\n"
    listObjects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(baStream)).encode() + b" >>\nstream\n"
        + baStream + b"endstream",
    ]
    baPdf = b"%PDF-1.4\n"
    listOffsets = []
    for iIndex, baObject in enumerate(listObjects, 1):
        listOffsets.append(len(baPdf))
        baPdf += (str(iIndex).encode() + b" 0 obj\n" + baObject
                  + b"\nendobj\n")
    iCrossReference = len(baPdf)
    baPdf += b"xref\n0 5\n0000000000 65535 f \n"
    for iOffset in listOffsets:
        baPdf += f"{iOffset:010d} 00000 n \n".encode()
    return (baPdf + b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n"
            + str(iCrossReference).encode() + b"\n%%EOF\n")


_S_HOLD_THE_FIRST_PDF_PAGE = """() => {
    const fnGetDocument = pdfjsLib.getDocument;
    window.pdfjsLib = Object.assign({}, pdfjsLib, {
        getDocument: function (dictOptions) {
            const taskReal = fnGetDocument(dictOptions);
            return {promise: taskReal.promise.then(pdfDoc => {
                const fnGetPage = pdfDoc.getPage.bind(pdfDoc);
                pdfDoc.getPage = (iPage) => fnGetPage(iPage).then(
                    page => new Promise(fnResolve => {
                        window.fnReleaseHeldPdfPage = () => fnResolve(page);
                    }));
                return pdfDoc;
            })};
        },
    });
    VaibifyFigureViewer.fnDisplayFileInViewer('B', 'prior.pdf', '');
}"""


@pytest.mark.falsification
def test_a_late_pdf_page_never_replaces_the_no_manifest_statement(
    pageDashboard, serverHub,
):
    """Kills: rendering a PDF page without checking its claim.

    The page render takes its claim when it STARTS, so a page that
    arrives after the viewer was replaced would start under the NEW
    claim and paint over it. Rendered by the real PDF.js.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    pageDashboard.route(
        "**/api/figure/**/prior.pdf",
        lambda route: route.fulfill(status=200,
                                    content_type="application/pdf",
                                    body=_fbaBuildOneSquarePdf()))
    pageDashboard.evaluate(_S_HOLD_THE_FIRST_PDF_PAGE)
    pageDashboard.wait_for_function(
        "() => !!window.fnReleaseHeldPdfPage", timeout=15000)
    pageDashboard.evaluate("""(sText) => {
        VaibifyFigureViewer.fnShowPlaceholderInViewer('B',
            '<span class="placeholder">' + sText + '</span>',
            'no reproduced manifest');
        window.fnReleaseHeldPdfPage();
    }""", S_NO_MANIFEST)
    pageDashboard.wait_for_timeout(1200)
    dictShown = pageDashboard.evaluate("""() => ({
        sText: document.getElementById('viewportB').textContent,
        iCanvases: document.querySelectorAll('#viewportB canvas').length,
    })""")
    assert S_NO_MANIFEST in dictShown["sText"], dictShown
    assert dictShown["iCanvases"] == 0, dictShown
    assert pageDashboard.listPageErrors == []
