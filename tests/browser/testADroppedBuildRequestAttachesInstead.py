"""A dropped connection is not a failed build.

``POST /api/containers/{name}/build`` is held open for the WHOLE
build -- minutes for a first image -- and a browser abandons a request
that long on its own. When it did, the dashboard reported the build as
failed and told the researcher the server could not be reached, while
the server was healthy and the build was still printing to the hub's
terminal (researcher-reported, 2026-09-05, on a first build after a
Docker context fix).

Nothing was wrong except the report. The docker build outlives the
request that started it -- that is why ``/build/progress`` exists, and
why the 409 path already attaches to it rather than declaring failure.
A network drop now takes the same path.

``route.abort()`` is a faithful reproduction rather than a stand-in:
it fails the fetch at the transport, which is exactly what a browser's
own response timeout does, and it is the layer the production code
classifies as ``sKind: "network"``.
"""

import pytest

from .fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser


S_PROGRESS_FAILED = (
    '{"bKnown": true, "bLive": false, "saTailLines": [], '
    '"iLineCount": 0, "sOutcome": "failed"}'
)


def _fnStubBuildAsDroppedButRunning(pageDashboard):
    """Drop the build POST in flight; answer progress as a real build."""
    pageDashboard.route(
        "**/api/containers/*/build",
        lambda route: route.abort(),
    )
    pageDashboard.route(
        "**/api/containers/*/build/progress",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=S_PROGRESS_FAILED,
        ),
    )


def _flistToastTexts(pageDashboard):
    """Every toast currently on screen, in order."""
    return pageDashboard.eval_on_selector_all(
        "#toastContainer .toast", "els => els.map(el => el.textContent)",
    )


def test_a_dropped_build_request_attaches_rather_than_reporting_failure(
    pageDashboard, serverHub,
):
    """The researcher is told the build survived, not that it died.

    Kills: removing the ``sKind === "network"`` branch from
    ``fnBuildContainer``, which sends the drop to
    ``_fnReportBuildFailure`` and puts "Cannot reach Vaibify server"
    on screen over a healthy server and a running build.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    _fnStubBuildAsDroppedButRunning(pageDashboard)
    pageDashboard.evaluate(
        """(sName) => {
            document.getElementById("toastContainer").innerHTML = "";
            return VaibifyContainerManager.fnBuildContainer(sName, false);
        }""",
        S_CONTAINER_NAME,
    )
    listToasts = _flistToastTexts(pageDashboard)
    sJoined = " | ".join(listToasts)
    assert "still running" in sJoined, (
        "a dropped request must say the build survived it: " + sJoined
    )
    assert "Cannot reach Vaibify server" not in sJoined, (
        "a healthy server was reported unreachable: " + sJoined
    )


def test_the_build_progress_endpoint_is_actually_consulted(
    pageDashboard, serverHub,
):
    """Attaching means reading the build's state, not just re-wording.

    A branch that showed a friendlier toast and then gave up would
    pass the assertions above while leaving the researcher exactly as
    uninformed. This one fails unless the progress endpoint is
    requested after the POST is dropped.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    listProgressCalls = []
    pageDashboard.route(
        "**/api/containers/*/build",
        lambda route: route.abort(),
    )

    def _fnRecordProgress(route):
        listProgressCalls.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=S_PROGRESS_FAILED,
        )

    pageDashboard.route(
        "**/api/containers/*/build/progress", _fnRecordProgress,
    )
    pageDashboard.evaluate(
        """(sName) => VaibifyContainerManager.fnBuildContainer(
            sName, false)""",
        S_CONTAINER_NAME,
    )
    assert listProgressCalls, (
        "the dropped build was never followed up on the progress "
        "endpoint, so nothing was attached to"
    )
