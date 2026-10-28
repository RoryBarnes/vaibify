"""A push re-verifies every remote the project can be verified against.

The post-push verify used to skip any service with no ``dictRemotes``
entry. GitHub is derived from the checkout's ``origin`` when no entry
records it, so that key test skipped the post-push verify on exactly the
projects whose GitHub came from ``origin`` -- three successful pushes,
and a GitHub row that stayed unconfirmed (researcher-reported,
2026-10-27). These tests drive the real predicate against a real
checkout on disk; only the verify itself is replaced by a recorder.
"""

import asyncio
import os

import pytest

from vaibify.gui import routeContext
from vaibify.reproducibility.repoFiles import HostRepoFiles
from vaibify.reproducibility.scheduledReverify import (
    S_HOST_GITHUB_BINDING_KEY,
)


_S_PROJECT_REPO = "/workspace/project"
_S_ORIGIN_CONFIG = (
    '[core]\n\trepositoryformatversion = 0\n'
    '[remote "origin"]\n'
    '\turl = https://github.com/example-owner/example-repo.git\n'
    '\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
)


def _fnWriteCheckout(sDirectory, sGitConfig):
    os.makedirs(os.path.join(sDirectory, ".git"))
    with open(os.path.join(sDirectory, ".git", "config"), "w") as fileHandle:
        fileHandle.write(sGitConfig)


def _fdictWorkflowPushedFromThisHub():
    """A project with no dictRemotes whose push this hub recorded."""
    return {
        "sProjectRepoPath": _S_PROJECT_REPO,
        "dictRemotes": None,
        S_HOST_GITHUB_BINDING_KEY: {_S_PROJECT_REPO: {
            "sOwner": "example-owner", "sRepo": "example-repo",
            "sBranch": "main"}},
    }


def _flistRunRefresh(monkeypatch, sDirectory, dictWorkflow):
    """Run the post-push refresh; return the services it verified."""
    listVerified = []

    async def fnRecordVerify(
        sContainerId, dictWorkflowSeen, sService, filesRepo, requestHttp,
    ):
        listVerified.append(sService)

    monkeypatch.setattr(
        routeContext, "ffilesForWorkflow",
        lambda dictCtx, sContainerId, dictWorkflowArg: HostRepoFiles(
            sDirectory))
    monkeypatch.setattr(
        routeContext, "_fnRunPostPushVerifyBlocking", fnRecordVerify)
    sWarning = asyncio.run(routeContext.fsRefreshVerifyCacheAfterPush(
        {}, "container-id", dictWorkflow, "github"))
    assert sWarning == ""
    return listVerified


@pytest.mark.falsification
def testAPushReverifiesGithubDerivedFromOrigin(tmp_path, monkeypatch):
    """No dictRemotes entry, an origin remote, a recorded push: GitHub is verified.

    Kills: restoring the ``dictRemotes`` key test, which skips the
    post-push verify for every project whose GitHub comes from origin.
    """
    _fnWriteCheckout(str(tmp_path), _S_ORIGIN_CONFIG)
    assert _flistRunRefresh(
        monkeypatch, str(tmp_path), _fdictWorkflowPushedFromThisHub(),
    ) == ["github"]


@pytest.mark.falsification
def testAnUnboundGithubRemoteIsStillVerifiedSoItsRefusalIsReported(
    tmp_path, monkeypatch,
):
    """A remote the hub has no push record for is verified, not dropped.

    The verify then refuses with "has not pushed from this hub" and its
    remedy, which the push returns as its warning. Dropping the service
    instead turns a successful push into silence about the comparison.

    Kills: asking the open-time refresh's question (verifiable NOW,
    binding included) after a push, which skips exactly this remote.
    """
    _fnWriteCheckout(str(tmp_path), _S_ORIGIN_CONFIG)
    dictWorkflow = dict(_fdictWorkflowPushedFromThisHub())
    dictWorkflow[S_HOST_GITHUB_BINDING_KEY] = {}
    assert _flistRunRefresh(
        monkeypatch, str(tmp_path), dictWorkflow) == ["github"]


@pytest.mark.falsification
def testAnUnboundRemoteIsRefusedBeforeAnyFetchAndTheRefusalIsRelayed(
    tmp_path, monkeypatch,
):
    """Through the REAL verify: refused at the binding, and the reason relayed.

    Drives ``fdictVerifyRemoteService`` -- the one call site every verify
    lane shares -- rather than the config helper, so the binding check is
    asserted where it protects the researcher's token: the GitHub fetch
    is rigged to fail the test if it is ever reached. And the push's
    warning carries the refusal's own sentence, because an agent cannot
    read the hub log it used to point at (found in review, 2026-10-27).

    Kills: resolving the verify's config without the binding check at
    ``fdictVerifyRemoteService``, which then queries the remote the
    container's ``origin`` names with the researcher's token.
    """
    from vaibify.reproducibility import scheduledReverify
    _fnWriteCheckout(str(tmp_path), _S_ORIGIN_CONFIG)

    def fnFailIfFetched(*args, **kwargs):
        raise AssertionError("the remote was queried without a binding")

    monkeypatch.setattr(
        scheduledReverify, "_fdictFetchGithubHashes", fnFailIfFetched)
    monkeypatch.setattr(
        routeContext, "ffilesForWorkflow",
        lambda dictCtx, sContainerId, dictWorkflowArg: HostRepoFiles(
            str(tmp_path)))
    dictWorkflow = dict(_fdictWorkflowPushedFromThisHub())
    dictWorkflow[S_HOST_GITHUB_BINDING_KEY] = {}
    sWarning = asyncio.run(routeContext.fsRefreshVerifyCacheAfterPush(
        {}, "container-id", dictWorkflow, "github"))
    assert scheduledReverify.S_GITHUB_NOT_BOUND in sWarning, sWarning


def testAProjectWithNoRemoteIsSkippedSilently(tmp_path, monkeypatch):
    """With nothing to verify against, a push verifies nothing and warns of nothing."""
    _fnWriteCheckout(str(tmp_path), "[core]\n\tbare = false\n")
    dictWorkflow = {"sProjectRepoPath": _S_PROJECT_REPO, "dictRemotes": None}
    assert _flistRunRefresh(monkeypatch, str(tmp_path), dictWorkflow) == []
