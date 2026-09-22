"""Cancelling dragover alone makes a drop target only in some browsers.

An element becomes a drop target when BOTH ``dragenter`` and
``dragover`` are cancelled. Chromium and WebKit accept a cancelled
``dragover`` on its own, so a target missing ``dragenter`` looks
perfectly correct in two of the three engines this lane drives -- and
does nothing whatsoever in Firefox: no drag-over highlight, no drop,
no error. That was the standing report (2026-09-22), on a researcher's
Firefox, against code every existing test called bound.

The existing coverage could not see it, and the reason is worth
keeping: ``testTheLabelledZoneIsAnActualDropTarget`` builds a
``DragEvent`` and calls ``el.dispatchEvent(event)`` on the zone. A
synthetic dispatch runs the handler directly and never consults the
browser's drag machinery, so it reports a bound ``dragover`` whether
or not the element is a target the machinery would ever offer a drop
to. It was green on Firefox throughout.

**What these tests prove, and what they do not.** They assert the
specification property that was missing -- that a file-shaped
``dragenter`` is CANCELLED on each target -- which is the thing whose
absence Firefox punishes. They do not simulate an operating-system
file drag, which Playwright cannot synthesise; the end-to-end drop
remains covered only by the manual walkthrough.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_CONNECT_ROUTE_GLOB = "**/api/connect/*"
S_FILES_ROUTE_GLOB = "**/api/files/**"


def _fnOpenFilesPanel(page, serverHub):
    """Enter the dashboard and show the Files sub-panel."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
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


def _fbDragEnterIsCancelled(page, sSelector):
    """Report whether a file-shaped dragenter is cancelled on a target.

    ``dispatchEvent`` returns false exactly when a listener called
    ``preventDefault``, which is the signal the drag machinery reads
    to decide the element accepts a drop.
    """
    return page.evaluate(
        """(sSelector) => {
            const el = document.querySelector(sSelector);
            if (!el) return null;
            const event = new DragEvent('dragenter', {
                bubbles: true,
                cancelable: true,
                dataTransfer: new DataTransfer(),
            });
            Object.defineProperty(event.dataTransfer, 'types', {
                value: ['Files'],
            });
            return !el.dispatchEvent(event);
        }""",
        sSelector,
    )


@pytest.mark.falsification
def testTheUploadZoneCancelsDragEnterNotOnlyDragOver(
    pageDashboard, serverHub,
):
    """The labelled zone is a target in every engine, not two of three.

    Kills: binding only dragover on the upload zone, which leaves the
    zone inert in a browser that holds to the specification while
    every Chromium-driven test still passes.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    bCancelled = _fbDragEnterIsCancelled(pageDashboard, "#fileUploadDropZone")
    assert bCancelled is not None, "the labelled upload zone is not in the page"
    assert bCancelled, (
        "dragenter was not cancelled on the upload zone, so the zone "
        "never becomes a drop target in a browser that requires it"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheFileListCancelsDragEnterToo(pageDashboard, serverHub):
    """The list has always been a drop target and must stay one.

    Kills: fixing the labelled zone alone and leaving the file list --
    the target researchers learned first -- inert.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    bCancelled = _fbDragEnterIsCancelled(pageDashboard, "#listFiles")
    assert bCancelled is not None, "the file list is not in the page"
    assert bCancelled, (
        "dragenter was not cancelled on the file list, so dropping "
        "onto it does nothing in a browser that requires it"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADragThatCarriesNoFilesIsStillIgnored(pageDashboard, serverHub):
    """The guard survives the fix: a non-file drag is not accepted.

    Cancelling dragenter unconditionally would make the zone claim
    every drag on the page, including a step being reordered, and the
    researcher would be offered a drop that uploads nothing.

    Kills: cancelling dragenter without asking whether the drag
    carries host files.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    bCancelled = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('fileUploadDropZone');
            const event = new DragEvent('dragenter', {
                bubbles: true,
                cancelable: true,
                dataTransfer: new DataTransfer(),
            });
            Object.defineProperty(event.dataTransfer, 'types', {
                value: ['vaibify/step'],
            });
            return !el.dispatchEvent(event);
        }"""
    )
    assert bCancelled is False, (
        "a drag carrying no host files was accepted by the upload zone"
    )
    assert pageDashboard.listPageErrors == []
