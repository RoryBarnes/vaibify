"""A zoomed figure can be scrolled to its left edge.

Zoomed in on a wide figure, the left side was cut off and scrolling
reached only the right edge: the viewer centred its scrolling box with
``justify-content: center``, and a centred flex row whose child
overflows pushes the overflow past the LEFT edge, where no scroll
position reaches. ``styleMain.css`` records the same trap, and the same
cure (auto margins), for the hub lists.

Driven in a real browser engine with a real image and a real PDF page
rendered by PDF.js, served to the viewer from the test; the measurement
is the browser's own layout.
"""

import struct
import zlib

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testALateAnswerNeverRepaintsAViewer import (
    _fbaBuildOneSquarePdf,
)


pytestmark = pytest.mark.browser

I_WIDE_PIXELS = 1200
S_TOP_ZOOM = "400%"

S_MEASURE_THE_SCROLL_BOX = """(sSelector) => {
    const elFigure = document.querySelector(sSelector);
    const elScroll = elFigure.parentElement;
    elScroll.scrollLeft = 0;
    const dictFigure = elFigure.getBoundingClientRect();
    const dictScroll = elScroll.getBoundingClientRect();
    return {
        fFigureLeft: dictFigure.left, fScrollLeft: dictScroll.left,
        fFigureWidth: dictFigure.width, iScrollWidth: elScroll.scrollWidth,
        iClientWidth: elScroll.clientWidth,
    };
}"""


def _fbaBuildWidePng(iWidth, iHeight):
    """Return a solid, valid RGB PNG, built from the format itself."""
    def fbaChunk(baKind, baData):
        return (struct.pack(">I", len(baData)) + baKind + baData
                + struct.pack(">I", zlib.crc32(baKind + baData) & 0xffffffff))
    baRows = b"".join(b"\x00" + b"\x20\x80\xc0" * iWidth for _ in range(iHeight))
    return (b"\x89PNG\r\n\x1a\n"
            + fbaChunk(b"IHDR", struct.pack(">IIBBBBB", iWidth, iHeight, 8, 2, 0, 0, 0))
            + fbaChunk(b"IDAT", zlib.compress(baRows))
            + fbaChunk(b"IEND", b""))


def _fnZoomToTheTop(pageDashboard):
    """Press Zoom in until the top level; the toolbar shows on hover, as a person sees it."""
    for _ in range(12):
        if pageDashboard.inner_text("#viewportA .zoom-level") == S_TOP_ZOOM:
            return
        pageDashboard.hover("#viewportA")
        pageDashboard.click("#viewportA button[title='Zoom in']")
        pageDashboard.wait_for_timeout(150)
    raise AssertionError("the viewer never reached its top zoom level")


def _fnAssertTheLeftEdgeIsInReach(pageDashboard, sSelector):
    dictMeasured = pageDashboard.evaluate(S_MEASURE_THE_SCROLL_BOX, sSelector)
    assert dictMeasured["fFigureWidth"] > dictMeasured["iClientWidth"], (
        "the figure must overflow for this to test anything", dictMeasured)
    assert dictMeasured["fFigureLeft"] >= dictMeasured["fScrollLeft"] - 0.5, (
        dictMeasured)
    assert dictMeasured["iScrollWidth"] >= dictMeasured["fFigureWidth"], (
        dictMeasured)


@pytest.mark.falsification
def testAZoomedImageKeepsItsLeftEdgeInReach(pageDashboard, serverHub):
    """Kills: centring the scrolling box with justify-content again."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route("**/api/figure/**/wide.png", lambda route: route.fulfill(
        status=200, content_type="image/png",
        body=_fbaBuildWidePng(I_WIDE_PIXELS, 40)))
    pageDashboard.evaluate(
        "() => VaibifyFigureViewer.fnDisplayFileInViewer('A', 'wide.png', '')")
    pageDashboard.wait_for_selector("#viewportA .zoom-level", timeout=10000)
    _fnZoomToTheTop(pageDashboard)
    _fnAssertTheLeftEdgeIsInReach(pageDashboard, "#viewportA img")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAZoomedPdfPageKeepsItsLeftEdgeInReach(pageDashboard, serverHub):
    """Kills: the same centring, on the PDF page's scrolling box."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route("**/api/figure/**/wide.pdf", lambda route: route.fulfill(
        status=200, content_type="application/pdf",
        body=_fbaBuildOneSquarePdf(iPageWidth=I_WIDE_PIXELS)))
    pageDashboard.evaluate(
        "() => VaibifyFigureViewer.fnDisplayFileInViewer('A', 'wide.pdf', '')")
    pageDashboard.wait_for_selector("#viewportA canvas", timeout=15000)
    _fnZoomToTheTop(pageDashboard)
    pageDashboard.wait_for_selector("#viewportA canvas", timeout=15000)
    _fnAssertTheLeftEdgeIsInReach(pageDashboard, "#viewportA canvas")
    assert pageDashboard.listPageErrors == []
