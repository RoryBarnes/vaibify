"""A login shorter than the turn budget is said out loud, not refused.

Until 2026-08-30 this case refused the convene outright, and the
refusal's remedy — run `claude` in the project's container — was
measured to do nothing in exactly the state that fired it: the CLI
refreshes a login that has LAPSED and leaves a still-valid one alone.
The refusal became a clamp, and a clamp the researcher cannot see is a
turn that ends early for no stated reason.

Only a real browser proves this half. The notice is composed at render
from an absolute expiry the server sent when the project was activated,
minus the clock at the moment the form opens — a source-level test
cannot show that the arithmetic runs, that the form still opens, or
that the notice stays away when the login is healthy.
"""

import time

import pytest

from . import fakeDockerAdapter
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser


@pytest.fixture
def fiShortLivedProjectLogin():
    """Model a login with a quarter of an hour left, then put it back."""
    fakeDockerAdapter.I_LOGIN_EXPIRES_AT_EPOCH_MILLISECONDS = int(
        (time.time() + 900) * 1000)
    try:
        yield
    finally:
        fakeDockerAdapter.I_LOGIN_EXPIRES_AT_EPOCH_MILLISECONDS = 0


def _fsOpenConveneFormAndReadSettings(pageDashboard, serverHub):
    """Open the planning form and return its settings fieldset text."""
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#btnCouncilConvene", timeout=8000)
    return pageDashboard.inner_text(".council-settings")


def testAShortLoginIsAnnouncedInTheConveneFormRatherThanRefused(
        pageDashboard, serverHub, fiShortLivedProjectLogin):
    """The form opens, and says what the login will cost the turn.

    Kills: clamping the turn silently, and the older behaviour of
    refusing the convene outright — the form must still be usable.
    """
    sSettings = _fsOpenConveneFormAndReadSettings(pageDashboard, serverHub)

    assert "15 minutes" in sSettings, (
        "the form does not state the login's remaining life, so a turn "
        f"that ends early has no stated reason: {sSettings!r}")
    assert "capped" in sSettings, (
        "the notice does not say the turn will be shortened")
    # Still convenable: a clamp is not a refusal.
    assert pageDashboard.is_enabled("#btnCouncilConvene")

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testAHealthyLoginSaysNothingAtAll(pageDashboard, serverHub):
    """The falsification pair.

    Kills: a notice that renders every time, which is a notice nobody
    reads by the time it matters. The default modelled login states no
    expiry at all, so there is nothing to announce.
    """
    sSettings = _fsOpenConveneFormAndReadSettings(pageDashboard, serverHub)

    assert "capped" not in sSettings, (
        f"the cap notice appeared for a login with no expiry: {sSettings!r}")

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
