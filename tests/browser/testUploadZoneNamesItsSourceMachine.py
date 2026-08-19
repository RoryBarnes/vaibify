"""The upload affordance says which machine the bytes come from.

Every other file action in the dashboard names its machine --
"Download to this computer", "Copy to execution-host path" -- because
once a browser can be somewhere else, "this machine" needs a subject.
Browser upload was the one left unnamed, and it is the one whose
direction is least guessable: the file list above it shows the
EXECUTION host's files, while a dropped file is read from the
BROWSER's computer. Through a tunnel those are different machines.

Two properties, and the second is the one worth the test.

The label must be SHOWN, not merely present in the markup. And the
labelled zone must actually ACCEPT a drop, because it says "drop files
here": a region that reads as a drop target without being one is worse
than no label at all -- the researcher drags a file onto exactly the
words inviting it, nothing happens, and there is no error to read.

Binding is asserted through the real listener's own visible effect
(the drag-over class the CSS keys off), not by inspecting handlers.
Playwright cannot synthesise a genuine OS file drag, so this proves
the zone is bound and responsive to a file drag; the upload REQUEST
that follows is covered by the file-endpoint tests, which drive the
route directly.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_ZONE_SELECTOR = "#fileUploadDropZone"
S_CONNECT_ROUTE_GLOB = "**/api/connect/*"
S_FILES_ROUTE_GLOB = "**/api/files/**"


def _fnOpenFilesPanel(page, serverHub):
    """Load the dashboard and open the Files sub-panel.

    Panels are shown by the ``active`` class the tab handler toggles,
    not by inline display, so the panel is opened the way the tab does
    it. Poking ``style.display`` instead reports a visible element that
    the real dashboard would still have hidden.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    # `#mainLayout` is display:none until the dashboard is entered, so
    # a zone inside it reports invisible however its own panel is
    # styled -- which would say nothing about the label. Entry runs
    # through the product's own function; the connect handshake is
    # stubbed because that function reveals the layout only after it
    # succeeds, and there is no real container here.
    page.route(
        S_CONNECT_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "sContainerId": "fake-resource",
                "sWorkflowPath": None,
                "dictWorkflow": None,
                "sLeaseId": "",
                "sProjectMode": "container",
                "sWorkspaceRoot": "",
            }),
        ),
    )
    page.route(
        S_FILES_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json", body="[]",
        ),
    )
    page.evaluate(
        "async () => { await VaibifyApp.fnEnterNoWorkflow('fake-resource'); }",
    )
    page.evaluate(
        """() => {
            ['panelSteps', 'panelLogs'].forEach((sId) => {
                const el = document.getElementById(sId);
                if (el) el.classList.remove('active');
            });
            const elFiles = document.getElementById('panelFiles');
            if (elFiles) elFiles.classList.add('active');
        }"""
    )


def testTheUploadZoneNamesTheComputerTheFilesComeFrom(
    pageDashboard, serverHub,
):
    """The researcher is told the source machine, in the panel.

    Kills: shipping the drop zone unlabelled, or labelling it with the
    execution host, which is the machine whose files are listed
    directly above and the one the bytes do NOT come from.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    elZone = pageDashboard.locator(S_ZONE_SELECTOR)
    assert elZone.count() == 1, "the labelled upload zone is not in the page"
    assert elZone.is_visible(), (
        "the upload zone exists but is not shown, so the researcher is "
        "told nothing about where a dropped file is read from"
    )
    sText = elZone.inner_text().lower()
    assert "upload from this computer" in sText, (
        f"the upload zone does not name its source machine: {sText!r}"
    )
    assert pageDashboard.listPageErrors == []


def testTheLabelledZoneIsAnActualDropTarget(pageDashboard, serverHub):
    """The words "drop files here" are backed by a bound listener.

    Kills: adding the label to index.html and never binding it, which
    is the silent half of this change -- the page looks right, the
    invitation is false, and a drop produces no request and no error.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    bReacted = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('fileUploadDropZone');
            if (!el) return null;
            const event = new DragEvent('dragover', {
                bubbles: true,
                cancelable: true,
                dataTransfer: new DataTransfer(),
            });
            /* The handlers ignore anything that is not a file drag,
               so the probe must look like one. */
            Object.defineProperty(event.dataTransfer, 'types', {
                value: ['Files'],
            });
            el.dispatchEvent(event);
            return el.classList.contains('drag-over');
        }"""
    )
    assert bReacted is not None, "the labelled upload zone is not in the page"
    assert bReacted, (
        "dragging a file over the zone labelled 'drop files here' did "
        "not activate it, so no listener is bound: the label invites a "
        "drop the page will silently ignore"
    )
    assert pageDashboard.listPageErrors == []


def testTheFileListRemainsADropTarget(pageDashboard, serverHub):
    """The affordance that already existed keeps working.

    Kills: moving the binding onto the new zone instead of adding it,
    which would break the muscle memory of anyone who learned to drop
    onto the listing itself.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    bReacted = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('listFiles');
            if (!el) return null;
            const event = new DragEvent('dragover', {
                bubbles: true,
                cancelable: true,
                dataTransfer: new DataTransfer(),
            });
            Object.defineProperty(event.dataTransfer, 'types', {
                value: ['Files'],
            });
            el.dispatchEvent(event);
            return el.classList.contains('drag-over');
        }"""
    )
    assert bReacted is not None, "the file list is not in the page"
    assert bReacted, (
        "the file listing stopped accepting drops, so an existing "
        "affordance was replaced rather than labelled"
    )
    assert pageDashboard.listPageErrors == []
