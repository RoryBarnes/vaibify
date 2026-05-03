"""Tests for vaibify.reproducibility.scheduledReverify."""

import os
from unittest.mock import MagicMock, patch

import pytest

from vaibify.reproducibility import scheduledReverify


def _fdictBuildWorkflow(sProjectRepo, sId="wf01"):
    """Return a minimal workflow dict with three remotes configured."""
    return {
        "sWorkflowId": sId,
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {
            "github": {
                "sOwner": "owner",
                "sRepo": "repo",
                "sBranch": "main",
            },
            "overleaf": {"sProjectId": "project1234"},
            "zenodo": {"sRecordId": "98765", "sService": "sandbox"},
        },
        "listSteps": [
            {
                "sDirectory": "step01",
                "saDataFiles": ["step01/data.csv"],
                "saPlotFiles": [],
                "saOutputFiles": [],
            },
        ],
    }


def _fnWriteManifestForOneFile(sProjectRepo, sExpectedHash):
    """Write a single-entry MANIFEST.sha256 for the test repo."""
    sManifest = os.path.join(sProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(
            "# SHA-256 manifest of workflow outputs\n"
            f"{sExpectedHash}  step01/data.csv\n"
        )


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Return a temp project repo with a one-line manifest."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
    _fnWriteManifestForOneFile(sRepo, "a" * 64)
    return sRepo


# --------- fnRunReverifyOnce: happy path ---------


def testRunReverifyOnceCoversAllConfiguredServices(tmp_path):
    """Two workflows × three remotes → six results, all sStatus=ok."""
    sRepoOne = str(tmp_path / "one")
    sRepoTwo = str(tmp_path / "two")
    for sRepo in (sRepoOne, sRepoTwo):
        os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
        _fnWriteManifestForOneFile(sRepo, "a" * 64)
    listWorkflows = [
        _fdictBuildWorkflow(sRepoOne, "wfA"),
        _fdictBuildWorkflow(sRepoTwo, "wfB"),
    ]
    dictMatch = {"step01/data.csv": "a" * 64}
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ):
        dictReport = scheduledReverify.fnRunReverifyOnce(
            {"workflows": {}}, listWorkflows,
        )
    assert len(dictReport["listResults"]) == 6
    listStatuses = [d["sStatus"] for d in dictReport["listResults"]]
    assert listStatuses == ["ok"] * 6
    assert dictReport["sNowIso"]


# --------- fnRunReverifyOnce: one bad remote isolated ---------


def testRunReverifyOnceCapturesPerServiceFailures(tmp_path):
    """One service raising → only that service is sStatus=error."""
    sRepoOne = str(tmp_path / "one")
    sRepoTwo = str(tmp_path / "two")
    for sRepo in (sRepoOne, sRepoTwo):
        os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
        _fnWriteManifestForOneFile(sRepo, "a" * 64)
    listWorkflows = [
        _fdictBuildWorkflow(sRepoOne, "wfA"),
        _fdictBuildWorkflow(sRepoTwo, "wfB"),
    ]
    dictMatch = {"step01/data.csv": "a" * 64}
    from vaibify.reproducibility.githubMirror import GithubMirrorError
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        side_effect=GithubMirrorError(
            "GitHub authentication failed: token=abc123",
        ),
    ), patch(
        "vaibify.reproducibility.overleafMirror.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ), patch(
        "vaibify.reproducibility.zenodoClient.fdictFetchRemoteHashes",
        return_value=dictMatch,
    ):
        dictReport = scheduledReverify.fnRunReverifyOnce(
            {"workflows": {}}, listWorkflows,
        )
    listResults = dictReport["listResults"]
    assert len(listResults) == 6
    listGithubResults = [
        d for d in listResults if d["sService"] == "github"
    ]
    listOtherResults = [
        d for d in listResults if d["sService"] != "github"
    ]
    assert all(d["sStatus"] == "error" for d in listGithubResults)
    assert all(d["sStatus"] == "ok" for d in listOtherResults)
    for dictGithub in listGithubResults:
        assert "abc123" not in dictGithub["sError"]


# --------- fnScheduleReverify: registration ---------


def testScheduleReverifyRegistersStartupAndShutdown():
    """fnScheduleReverify wires startup + shutdown handlers on the app."""
    appMock = MagicMock()
    appMock.on_event.return_value = lambda fn: fn
    scheduledReverify.fnScheduleReverify(
        appMock, {"workflows": {}}, fHoursCadence=0.1,
    )
    listEventNames = [
        callOne.args[0] for callOne in appMock.on_event.call_args_list
    ]
    assert "startup" in listEventNames
    assert "shutdown" in listEventNames


# --------- Manifest loading helper ---------


def testLoadManifestExpectedHashesParsesEntries(fixtureProjectRepo):
    """Manifest entries are parsed into a {relpath: hash} dict."""
    dictExpected = scheduledReverify.fdictLoadManifestExpectedHashes(
        fixtureProjectRepo,
    )
    assert dictExpected == {"step01/data.csv": "a" * 64}


def testLoadManifestRaisesWhenAbsent(tmp_path):
    """Missing manifest raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        scheduledReverify.fdictLoadManifestExpectedHashes(str(tmp_path))


# --------- Sync status read/write round trip ---------


def testWriteAndReadSyncStatusRoundTrip(fixtureProjectRepo):
    """A status written for one service is recovered by the read helper."""
    dictPersisted = {
        "sService": "overleaf",
        "sLastVerified": "2026-05-03T12:00:00Z",
        "iTotalFiles": 5,
        "iMatching": 4,
        "listDiverged": [
            {"sPath": "x.txt", "sExpected": "h1", "sActual": "h2"},
        ],
    }
    scheduledReverify.fnWriteSyncStatus(
        fixtureProjectRepo, dictPersisted,
    )
    dictReadBack = scheduledReverify.fdictReadCachedSyncStatus(
        fixtureProjectRepo, "overleaf",
    )
    assert dictReadBack == dictPersisted


def testReadCachedReturnsEmptyDefaultWhenAbsent(tmp_path):
    """Missing syncStatus.json yields a clean empty default per service."""
    sRepo = str(tmp_path)
    dictResult = scheduledReverify.fdictReadCachedSyncStatus(
        sRepo, "github",
    )
    assert dictResult["sService"] == "github"
    assert dictResult["sLastVerified"] is None
    assert dictResult["listDiverged"] == []


# --------- Config error path ---------


def testVerifyRemoteRaisesConfigErrorOnUnconfiguredService(
    fixtureProjectRepo,
):
    """A workflow without a remote configured for sService raises."""
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflow["dictRemotes"].pop("github")
    with pytest.raises(scheduledReverify.ReverifyConfigError):
        scheduledReverify.fdictVerifyRemoteService(
            fixtureProjectRepo, dictWorkflow, "github",
        )
