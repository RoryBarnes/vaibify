"""Every undecided repository gets its prompt, one at a time.

The shipped bug: the prompt loop called the choice modal once per
undecided repository, and the modal is a singleton — each call removed
the previous one, so with N undecided repositories the researcher saw
exactly one prompt while all N were marked as already-prompted. The
other N-1 stayed silently undecided for the rest of the page load.

The queue is a frontend behaviour, so the status payload is delivered
over the wire by route interception and the assertion is on the real
modals a real Chromium shows.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_REPOS_STATUS_GLOB = "**/api/repos/*/status"
S_REPOS_DECISION_GLOB = "**/api/repos/*/*/track"
S_REPOS_IGNORE_GLOB = "**/api/repos/*/*/ignore"


def _fnServeReposStatus(page, listUndecidedNames):
    """Answer the panel's status poll with the given undecided repos."""
    page.route(
        S_REPOS_STATUS_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "listTracked": [],
                "listIgnored": [],
                "listUndecided": [
                    {"sName": sName} for sName in listUndecidedNames
                ],
                "listNonRepoDirs": [],
            }),
        ),
    )


def _fnAcceptDecisionPosts(page):
    """Answer Track and Ignore posts with success."""
    for sGlob in (S_REPOS_DECISION_GLOB, S_REPOS_IGNORE_GLOB):
        page.route(
            sGlob,
            lambda routeIntercepted: routeIntercepted.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"bSuccess": True}),
            ),
        )


def testEachUndecidedRepositoryIsPromptedInTurn(pageDashboard, serverHub):
    """Answering one prompt surfaces the next; the last answer ends it.

    Kills: the one-modal-per-loop-iteration call pattern, where the
    second repository's prompt destroyed the first's before a human
    could see it.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    _fnServeReposStatus(pageDashboard, ["handAlpha", "handBeta"])
    _fnAcceptDecisionPosts(pageDashboard)
    pageDashboard.evaluate(
        "async () => { await VaibifyReposPanel.fnInit('fake-resource'); }",
    )
    pageDashboard.wait_for_selector("#modalChoice", timeout=5000)
    assert "handAlpha" in pageDashboard.inner_text("#modalChoice")
    pageDashboard.click("#modalChoice button:has-text('Track')")
    pageDashboard.wait_for_selector("#modalChoice", timeout=5000)
    assert "handBeta" in pageDashboard.inner_text("#modalChoice")
    pageDashboard.click("#modalChoice button:has-text('Later')")
    pageDashboard.wait_for_selector(
        "#modalChoice", state="detached", timeout=5000,
    )
    assert pageDashboard.listPageErrors == []


def testAnAnsweredPromptDoesNotReturnOnTheNextPoll(
    pageDashboard, serverHub,
):
    """The prompted-names set still suppresses re-prompting.

    Kills: a queue that enqueues every undecided name on every status
    update, which would re-ask about a repository the researcher just
    deferred with Later on the very next five-second poll.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(".container-tile", timeout=10000)
    _fnServeReposStatus(pageDashboard, ["handAlpha"])
    _fnAcceptDecisionPosts(pageDashboard)
    pageDashboard.evaluate(
        "async () => { await VaibifyReposPanel.fnInit('fake-resource'); }",
    )
    pageDashboard.wait_for_selector("#modalChoice", timeout=5000)
    pageDashboard.click("#modalChoice button:has-text('Later')")
    pageDashboard.wait_for_selector(
        "#modalChoice", state="detached", timeout=5000,
    )
    pageDashboard.evaluate(
        "() => { VaibifyReposPanel.fnHandleStatusUpdate({"
        "listTracked: [], listIgnored: [],"
        "listUndecided: [{sName: 'handAlpha'}], listNonRepoDirs: []"
        "}); }",
    )
    assert pageDashboard.query_selector("#modalChoice") is None
    assert pageDashboard.listPageErrors == []
