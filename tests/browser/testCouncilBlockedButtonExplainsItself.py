"""A shut credential gate must SAY so when the researcher clicks.

The live report that produced this file: the button did nothing at all.
The explanation code existed — ``fnHandleToolbarClick`` has always
toasted the refusal — but ``_fnRenderToolbarButton`` set
``elButton.disabled`` for the same condition, and a disabled button
swallows its own click, so the handler was unreachable. The reason was
reachable only as a hover title, which is not where anyone looks after
clicking.

These tests drive the REAL frontend against the REAL capabilities route
with the gate genuinely shut (no evidence record patched in), because
the defect was precisely that a code path existed and could not run.
Asserting the toast TEXT is what makes them fail if the click goes
silent again.
"""

import pytest

from .fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_WORKFLOW_PATH,
)
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def _fnDropTheClaimBetweenTests(serverHub):
    """Release the container lease each test takes.

    Every test here claims the same container, and a lease left behind
    makes the NEXT claim fail — which showed up as the click producing
    no toast, a symptom indistinguishable from the bug under test. Two
    of these tests passed alone and failed in sequence before this
    existed.
    """
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)


@pytest.fixture(autouse=True)
def _fnShutTheCredentialGate(monkeypatch):
    """Leave the runner backend disabled, the way a fresh machine is.

    Patched explicitly rather than relying on the absence of a record,
    so the test states its own premise and cannot be quietly enabled by
    a developer's real ~/.vaibify evidence file.
    """
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": False,
            "sReason": ("the runner backend is disabled: no "
                        "credential-verification evidence record exists "
                        "on this machine."),
            "dictRecord": None,
        })


def _fdictActivateCouncilToolbar(page, serverHub):
    """Claim, open the workflow, and report the toolbar button's state."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    return page.evaluate(
        """async ([sContainerId, sName, sWorkflowPath]) => {
            const dictClaim = await VaibifyApi.fdictPost(
                '/api/registry/' + encodeURIComponent(sName) + '/claim', {});
            VaibifyApp.fnRecordClaimedLease(sName, dictClaim.sLeaseId);
            await VaibifyApp.fnEnterNoWorkflow(sContainerId);
            await VaibifyApi.fdictPostRaw(
                '/api/connect/' + sContainerId +
                '?sWorkflowPath=' + encodeURIComponent(sWorkflowPath));
            VaibifyAgentCouncil.fnActivate(sContainerId);
            await VaibifyAgentCouncil.fnRefreshCapabilities();
            const elButton = document.getElementById('btnAgentCouncil');
            return {
                bDisabled: elButton.disabled,
                bBlockedClass: elButton.classList.contains(
                    'council-blocked'),
                sTitle: elButton.title,
            };
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME, S_WORKFLOW_PATH],
    )


def testAShutGateLeavesTheButtonClickableNotDead(pageDashboard, serverHub):
    """The regression itself: disabled would swallow the click again."""
    dictState = _fdictActivateCouncilToolbar(pageDashboard, serverHub)

    assert dictState["bDisabled"] is False, (
        "the council button is disabled while the credential gate is "
        "shut, so clicking it does nothing and the researcher is told "
        "nothing — this is the reported defect"
    )
    assert dictState["bBlockedClass"] is True, (
        "a clickable-but-unusable button must still LOOK unusable"
    )
    assert "credential" in dictState["sTitle"].lower()


def testClickingTheBlockedButtonNamesTheFixOnScreen(
    pageDashboard, serverHub,
):
    """Not merely 'a toast appeared' — the remediation must be IN it.

    The whole point of the report was that the researcher could not
    tell what to do next, so asserting the presence of a toast would
    re-pass while saying nothing useful.
    """
    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)
    sToast = pageDashboard.inner_text(".toast")

    assert "credentialEvidence.json" in sToast, (
        f"the toast never names where to record the result: {sToast!r}")
    assert "sha256" in sToast, (
        f"the toast never says a tag is refused: {sToast!r}")
    assert "paid account" in sToast, (
        f"the toast never names the check that opens the gate: {sToast!r}")


def testNoWorkflowOpenAlsoExplainsItselfRatherThanDyingSilently(
    pageDashboard, serverHub,
):
    """The SECOND silent dead click, from the same button.

    ``fnActivate`` runs from ``_fnActivateWorkflow`` — opening a
    WORKFLOW, not merely a container — so a researcher sitting in a
    project with no workflow open has a live-looking button and no
    container id behind it. The first fix left this case disabled, on
    the reasoning that there was "nothing to say yet"; there is, and it
    is the most actionable thing on the page.

    Enters the container WITHOUT opening a workflow — the state the
    live report was made from, and the one where the button first
    becomes visible. (On the landing page it is hidden, so no click is
    possible there; verified before writing this.)
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    pageDashboard.evaluate(
        """async ([sContainerId, sName]) => {
            const dictClaim = await VaibifyApi.fdictPost(
                '/api/registry/' + encodeURIComponent(sName) + '/claim', {});
            VaibifyApp.fnRecordClaimedLease(sName, dictClaim.sLeaseId);
            await VaibifyApp.fnEnterNoWorkflow(sContainerId);
        }""",
        [S_CONTAINER_ID, S_CONTAINER_NAME],
    )
    pageDashboard.wait_for_selector("#btnAgentCouncil:visible", timeout=8000)

    assert pageDashboard.locator("#btnAgentCouncil").is_disabled() is False, (
        "in a container with no workflow open the button is visible but "
        "disabled, so the click is swallowed and the researcher is told "
        "nothing — this is the second report of the same silent click"
    )

    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)
    sToast = pageDashboard.inner_text(".toast")

    assert "workflow" in sToast.lower(), (
        f"the toast never says to open a workflow: {sToast!r}")


def testTheExplanationDoesNotSelfDestructBeforeItCanBeRead(
    pageDashboard, serverHub,
):
    """An info toast disappears after 4s; this one must not.

    A refusal carrying a filesystem path and an image-id rule is not
    readable, let alone actionable, in four seconds.
    """
    _fdictActivateCouncilToolbar(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector(".toast", timeout=8000)

    pageDashboard.wait_for_timeout(5000)

    assert pageDashboard.locator(".toast").count() == 1, (
        "the refusal vanished before it could be read or acted on"
    )
