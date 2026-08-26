"""A successful push runs the verify that can actually change a badge.

Since the octocat started meaning "verified identical to the published
copy", a push can never turn one blue on its own -- it changes the
remote, not what vaibify KNOWS about the remote. That was honest and it
was a dead end: a researcher who pushed a file saw a success toast,
watched every badge stay exactly as it was, and had no way to tell
whether anything had happened.

So the push ends by running the same verify the Verify-now button runs.
This drives that chain in a real browser with the two network calls
stubbed at the API seam, which is the only layer the browser lane can
honestly reach: the fake Docker adapter cannot run `git push`, and a
real verify would need the GitHub API. What IS exercised is the whole
JavaScript control flow -- outcome shaping, the success test, and the
dispatch to the verify.

What this does NOT prove, and must not be read as proving: that the
push route works, that the verify route works, or that a badge ends up
blue against a real mirror. It proves that a successful push reaches
the verify instead of stopping.

Kills (confirmed, not assumed): removing the
_fnVerifyAfterSuccessfulPush call from _fnRunSyncOnce fails the
verify-was-called assertion; making the push outcome unconditionally
truthy fails the failed-push assertion.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

# Replaces VaibifyApi.fdictPost so no route is really called. Records
# every path, and answers the add-file route with the shape the real
# one returns on success.
_S_INSTALL_RECORDER = """(bPushSucceeds) => {
    window.__listPosts = [];
    VaibifyApi.fdictPost = function (sPath, dictBody) {
        window.__listPosts.push(sPath);
        if (sPath.indexOf('/add-file') !== -1) {
            return Promise.resolve({bSuccess: bPushSucceeds,
                                    sOutput: 'stub'});
        }
        if (sPath.indexOf('/verify') !== -1) {
            return Promise.resolve({
                sService: 'github', sLastVerified: '2026-08-26T00:00:00Z',
                iTotalFiles: 1, iMatching: 1, listDiverged: [],
                listComparedPaths: ['a.txt'],
            });
        }
        return Promise.resolve({});
    };
}"""


def _fnOpenTheHostWorkflow(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )
    pageDashboard.wait_for_selector(
        ".project-block-header", timeout=20000,
    )


def _flistDrivePush(pageDashboard, bPushSucceeds):
    """Push one file through the real handler; return the paths posted."""
    pageDashboard.evaluate(_S_INSTALL_RECORDER, bPushSucceeds)
    pageDashboard.evaluate(
        """async () => {
            await VaibifySyncManager.fnSyncFileToRemote(
                'sGithub', 'reproduce.sh', '');
        }"""
    )
    return pageDashboard.evaluate("() => window.__listPosts")


def test_only_a_successful_push_is_followed_by_a_verify(
    pageDashboard, serverHub,
):
    """Both directions in ONE test, because one session owns a project.

    A container admits exactly one browser session, so a second test
    function reopening the same project is refused at the gate -- the
    failure looks like a Playwright timeout and has nothing to do with
    what is under test. The complement is not optional though: without
    it, an unconditional verify passes the first half.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    listAfterSuccess = _flistDrivePush(pageDashboard, True)
    assert any("/add-file" in s for s in listAfterSuccess), (
        f"the push never reached the add-file route: {listAfterSuccess}"
    )
    assert any("/verify" in s for s in listAfterSuccess), (
        "the push succeeded and stopped there, so every badge stays "
        "exactly as it was and the researcher has no way to see that "
        f"their file is now published: {listAfterSuccess}"
    )

    listAfterFailure = _flistDrivePush(pageDashboard, False)
    assert any("/add-file" in s for s in listAfterFailure), (
        listAfterFailure
    )
    assert not any("/verify" in s for s in listAfterFailure), (
        "a failed push still ran a verify, spending ten seconds of the "
        "researcher's time to re-confirm what the failure toast "
        f"already said: {listAfterFailure}"
    )

    assert pageDashboard.listPageErrors == []
