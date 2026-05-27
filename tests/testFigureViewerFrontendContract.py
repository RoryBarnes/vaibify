"""Frontend contract checks for the figure viewer zoom toolbar.

Asserts the regression-fix shape for PDF state (per-viewport, not
module-level) and verifies each of the three zoom-toolbar buttons is
wired with the expected behavior. JavaScript is not executed by the
repository test suite; these are string-presence + structural tests
in the established frontend-contract pattern.

Background
----------
The original implementation stored ``_activePdfDocument`` and
``_activePdfRenderTask`` as module-level singletons. Rendering a PDF
in one viewer destroyed the other viewer's PDF document out from
under its zoom-toolbar closures, leaving the first viewer's ``-``,
``+``, and fit buttons pointed at a dead page handle. Symptom: the
first-rendered viewer's zoom buttons silently stopped working as
soon as the second viewer rendered a PDF. Tests here lock in the
fix (state lives on the viewport element) and the per-button
behavior contract so the bug can't recur.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


# -----------------------------------------------------------------------
# Regression guard: PDF state must be per-viewport, never module-level
# -----------------------------------------------------------------------


def test_no_module_level_active_pdf_document_singleton():
    """A bare ``var _activePdfDocument =`` would re-introduce the
    cross-viewer destruction bug."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    assert "var _activePdfDocument" not in sSource, (
        "Module-level _activePdfDocument singleton would re-introduce "
        "the cross-viewer PDF destruction bug. Store PDF state on the "
        "viewport element instead."
    )
    assert "var _activePdfRenderTask" not in sSource, (
        "Module-level _activePdfRenderTask singleton would let one "
        "viewer cancel the other viewer's render. Store on the "
        "viewport element instead."
    )


def test_pdf_state_lives_on_viewport_element():
    """PDF document and render-task references must hang off elViewport."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    assert "elViewport._activePdfDocument" in sSource, (
        "PDF document handle must be stored as elViewport._activePdfDocument"
    )
    assert "elViewport._activePdfRenderTask" in sSource, (
        "PDF render task must be stored as elViewport._activePdfRenderTask"
    )


def test_destroy_active_pdf_takes_viewport_argument():
    """fnDestroyActivePdf and fnCancelActivePdfRender must accept the
    viewport so they can scope to a single viewer."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    assert "function fnDestroyActivePdf(elViewport)" in sSource
    assert "function fnCancelActivePdfRender(elViewport)" in sSource


def test_release_all_resources_iterates_both_viewports():
    """The session-wide cleanup must destroy each viewer's PDF
    independently — otherwise a hot reload could leak one of them."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    iStart = sSource.find("function fnReleaseAllResources")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert '"viewportA"' in sBlock and '"viewportB"' in sBlock, (
        "fnReleaseAllResources must destroy PDF state on both viewports"
    )


# -----------------------------------------------------------------------
# Per-button behavior contract — one test per zoom-toolbar button
# -----------------------------------------------------------------------


def _fsExtractZoomToolbarBody():
    """Return the body of fnCreateZoomToolbar for structural assertions."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    iStart = sSource.find("function fnCreateZoomToolbar(")
    assert iStart != -1, "fnCreateZoomToolbar must exist"
    iEnd = sSource.find("\n    }\n", iStart)
    assert iEnd != -1, "fnCreateZoomToolbar must terminate"
    return sSource[iStart:iEnd]


def test_zoom_toolbar_creates_three_buttons():
    """The toolbar must wire exactly three zoom buttons (out, in, fit)."""
    sBody = _fsExtractZoomToolbarBody()
    iCount = sBody.count("felCreateZoomButton(")
    assert iCount == 3, (
        "fnCreateZoomToolbar should create three buttons "
        "(zoom-out, zoom-in, fit-to-window); found {0}".format(iCount)
    )


def test_zoom_out_button_steps_down_through_zoom_levels():
    """The zoom-out button must walk the discrete zoom levels via
    fiNextZoomIndex with direction -1, not multiply scale directly."""
    sBody = _fsExtractZoomToolbarBody()
    assert '"Zoom out"' in sBody, "Zoom-out button title missing"
    assert "fiNextZoomIndex(dNumeric, -1)" in sBody, (
        "Zoom-out button must call fiNextZoomIndex with -1 direction"
    )


def test_zoom_in_button_steps_up_through_zoom_levels():
    """The zoom-in button must walk the discrete zoom levels via
    fiNextZoomIndex with direction +1."""
    sBody = _fsExtractZoomToolbarBody()
    assert '"Zoom in"' in sBody, "Zoom-in button title missing"
    assert "fiNextZoomIndex(dNumeric, 1)" in sBody, (
        "Zoom-in button must call fiNextZoomIndex with +1 direction"
    )


def test_fit_to_window_button_emits_literal_fit_scale():
    """The fit-to-window button must pass the literal "fit" sentinel
    so downstream renderers compute the scale from viewport width."""
    sBody = _fsExtractZoomToolbarBody()
    assert '"Fit to window"' in sBody, "Fit button title missing"
    assert 'fnOnZoom("fit")' in sBody, (
        "Fit button must invoke fnOnZoom with the 'fit' sentinel"
    )


# -----------------------------------------------------------------------
# Each toolbar button must be wired with a click handler
# -----------------------------------------------------------------------


def test_zoom_button_factory_attaches_click_listener():
    """felCreateZoomButton must register an onClick listener for every
    button it returns; otherwise the buttons render but don't respond."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    iStart = sSource.find("function felCreateZoomButton(")
    assert iStart != -1, "felCreateZoomButton must exist"
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert 'addEventListener("click"' in sBlock, (
        "felCreateZoomButton must wire a click listener on each button"
    )


# -----------------------------------------------------------------------
# Both viewers share the same toolbar code (no per-viewer divergence)
# -----------------------------------------------------------------------


def test_image_renderer_uses_shared_zoom_toolbar():
    """fnRenderImageWithZoom must use fnCreateZoomToolbar, not a
    private duplicate — otherwise viewer A and viewer B could drift."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    iStart = sSource.find("function fnRenderImageWithZoom(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "fnCreateZoomToolbar(" in sBlock, (
        "Image renderer must reuse fnCreateZoomToolbar"
    )


def test_pdf_renderer_uses_shared_zoom_toolbar():
    """fnSwapPdfContent must use fnCreateZoomToolbar so PDF zoom
    behaves identically to image zoom on both viewers."""
    sSource = _fsReadStaticFile("scriptFigureViewer.js")
    iStart = sSource.find("function fnSwapPdfContent(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "fnCreateZoomToolbar(" in sBlock, (
        "PDF renderer must reuse fnCreateZoomToolbar"
    )
