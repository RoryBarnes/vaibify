"""Frontend contract checks for the large-workflow loading banner.

The workflow picker surfaces a banner when the researcher selects a
workflow whose serialized JSON exceeds a size threshold. Without it,
a multi-megabyte workflow.json silently took ~30s to round-trip with
no UI feedback. JavaScript is not executed by the repository test
suite; these are string-presence + structural tests.
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


def test_index_html_has_loading_banner_dom_node():
    """The banner element must exist in the picker DOM so the JS has
    something to reveal."""
    sSource = _fsReadStaticFile("index.html")
    assert 'id="workflowLoadingBanner"' in sSource
    assert 'id="workflowLoadingText"' in sSource
    assert "workflow-loading-banner" in sSource


def test_loading_banner_has_aria_live_role():
    """Researchers using a screen reader should be told the load is
    in flight — role=status + aria-live=polite is the standard."""
    sSource = _fsReadStaticFile("index.html")
    iStart = sSource.find('id="workflowLoadingBanner"')
    iEnd = sSource.find(">", iStart)
    sTag = sSource[iStart:iEnd]
    assert 'role="status"' in sTag
    assert 'aria-live="polite"' in sTag


def test_loading_banner_hidden_by_default():
    """The banner must start hidden so it doesn't flash on every load."""
    sSource = _fsReadStaticFile("index.html")
    iStart = sSource.find('id="workflowLoadingBanner"')
    iEnd = sSource.find(">", iStart)
    sTag = sSource[iStart:iEnd]
    assert "hidden" in sTag


def test_loading_banner_css_defined():
    """Both the banner class and its spinner sibling must have CSS."""
    sSource = _fsReadStaticFile("styleMain.css")
    assert ".workflow-loading-banner" in sSource
    assert ".workflow-loading-spinner" in sSource


def test_workflow_card_carries_size_bytes_attribute():
    """Every rendered card must expose data-size-bytes so the click
    handler can decide whether to show the banner without re-querying
    the backend."""
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iStart = sSource.find("function _fsRenderWorkflowCard(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "data-size-bytes" in sBlock, (
        "Workflow card must render data-size-bytes for the picker handler"
    )


def test_card_click_handler_reads_size_bytes():
    """The bound click handler must read dataset.sizeBytes and forward
    it to fnSelectWorkflow."""
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iStart = sSource.find("function _fnBindWorkflowCards(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "dataset.sizeBytes" in sBlock, (
        "Click handler must read data-size-bytes off the card"
    )
    assert "fnSelectWorkflow(sId, sPath, sName" in sBlock


def test_select_workflow_shows_and_hides_banner():
    """The selection path must reveal the banner above threshold AND
    hide it in finally — otherwise a failed load leaves stale UI."""
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iStart = sSource.find("async function fnSelectWorkflow(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fnShowLargeWorkflowLoadingBanner" in sBlock, (
        "fnSelectWorkflow must call the show-banner helper"
    )
    assert "_fnHideLargeWorkflowLoadingBanner" in sBlock, (
        "fnSelectWorkflow must call the hide-banner helper"
    )
    assert "finally" in sBlock, (
        "Hide must run in finally so failed loads still clear the banner"
    )


def test_banner_threshold_is_declared_and_compared():
    """The size threshold must be a real ``var`` (greppable, one place
    to tune) and ``fnSelectWorkflow`` must actually compare the
    incoming size against it — not merely mention the symbol name in a
    comment."""
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert "var _I_LARGE_WORKFLOW_BYTES" in sSource, (
        "_I_LARGE_WORKFLOW_BYTES must be declared as a real module-level "
        "binding so future tuning happens in one place."
    )
    iStart = sSource.find("async function fnSelectWorkflow(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_I_LARGE_WORKFLOW_BYTES" in sBlock, (
        "fnSelectWorkflow must compare against the threshold; "
        "otherwise the banner never gates on size."
    )


def test_banner_shows_workflow_name_and_size():
    """The banner text must include the workflow name and human-readable
    KB so the researcher knows which load is in flight."""
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iStart = sSource.find("_fnShowLargeWorkflowLoadingBanner")
    iEnd = sSource.find("\n    }\n", iStart + 1)
    sBlock = sSource[iStart:iEnd]
    assert "sWorkflowName" in sBlock
    assert "KB" in sBlock
