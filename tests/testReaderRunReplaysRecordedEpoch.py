"""A reader's container run replays the author's recorded date.

A run dated from HEAD can never reproduce a vector figure the author's
run dated otherwise: the commit that published the manifest moved HEAD,
and a reader's clone has its own. When a project runs in a container,
its envelope records ``iSourceDateEpoch`` and its manifest was committed
by ANOTHER identity, every run replays the recorded epoch. Otherwise a
run dates from HEAD, as it always did. The decision lives in one place,
so these tests drive that place from every lane that reaches it.

Every repository here is a real git repository. The author's identity
and the reader's are distinct strings, and the epochs are integers
written into the fixture, never read back from the code under test.
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)
from vaibify.gui import determinismEnvironment, pipelineRunner
from vaibify.gui.pipelineRunner import (
    S_ENV_PREFIX_KEY,
    _fnInjectDeterminismEnvPrefix,
    _ftPrepareLogAndVariables,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles

I_RECORDED_EPOCH = 1790631466
I_HEAD_EPOCH = 1111111111
S_AUTHOR_EMAIL = "author@example.invalid"
S_READER_EMAIL = "reader@example.invalid"


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    """Keep every git here from reading the developer's own config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", S_AUTHOR_EMAIL)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", S_AUTHOR_EMAIL)
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Author")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author")


@pytest.fixture
def fnSeamsOnTheLocalRepository(monkeypatch):
    """Read the project through a host adapter and date HEAD by a constant."""
    def fnArrange(sRepo, bHostProject=False):
        monkeypatch.setattr(
            determinismEnvironment, "_ffilesOpenProjectRepoFiles",
            lambda connectionDocker, sContainerId, sPath:
                ffilesEnsureRepoFiles(sRepo),
        )
        monkeypatch.setattr(
            "vaibify.config.registryManager.fbIsHostProject",
            lambda sContainerId: bHostProject,
        )
        monkeypatch.setattr(
            determinismEnvironment, "_fiQueryHeadCommitEpoch",
            AsyncMock(return_value=I_HEAD_EPOCH),
        )
    return fnArrange


def _fsPublishedClone(tmp_path, sReaderEmail, bRecordEpoch=True):
    """A repository whose manifest and envelope the author committed."""
    sRepo = str(tmp_path / "clone")
    os.makedirs(sRepo)
    fsRunGit(["init", "-q"], sRepo)
    fnWriteText(sRepo, "data.txt", "1 2 3\n")
    fnWriteManifest(sRepo, ["data.txt"])
    if bRecordEpoch:
        os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
        with open(os.path.join(sRepo, ".vaibify", "environment.json"), "w") as f:
            json.dump({"iSourceDateEpoch": I_RECORDED_EPOCH}, f)
    fnCommitEverything(sRepo, "author publishes")
    if sReaderEmail:
        fsRunGit(["config", "user.email", sReaderEmail], sRepo)
    return sRepo


def _fdictInject(sRepo, iOverride=0):
    dictVariables = {}
    dictWorkflow = {"sProjectRepoPath": sRepo, "listSteps": []}
    asyncio.run(_fnInjectDeterminismEnvPrefix(
        MagicMock(), "cid", dictWorkflow, dictVariables,
        iSourceDateEpochOverride=iOverride,
    ))
    return dictVariables


def _fiEpochOfPrefix(dictVariables):
    sPrefix = dictVariables[S_ENV_PREFIX_KEY]
    return int(sPrefix.split("export SOURCE_DATE_EPOCH=", 1)[1].split(" ", 1)[0])


@pytest.mark.falsification
def test_a_foreign_manifest_container_run_replays_the_recorded_epoch(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    """Kills: always dating from HEAD, which is the reader's defect."""
    sRepo = _fsPublishedClone(tmp_path, S_READER_EMAIL)
    fnSeamsOnTheLocalRepository(sRepo)
    assert _fiEpochOfPrefix(_fdictInject(sRepo)) == I_RECORDED_EPOCH


@pytest.mark.falsification
def test_the_authors_own_run_dates_from_head(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    """Kills: inverting the ownership condition.

    The author's committer is the configured identity, so the replay
    must not leak into the author's own flow.
    """
    sRepo = _fsPublishedClone(tmp_path, S_AUTHOR_EMAIL)
    fnSeamsOnTheLocalRepository(sRepo)
    assert _fiEpochOfPrefix(_fdictInject(sRepo)) == I_HEAD_EPOCH


@pytest.mark.falsification
def test_undetermined_ownership_dates_from_head_and_says_why(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    """Kills: treating an ownership git cannot settle as foreign."""
    sRepo = str(tmp_path / "notARepository")
    os.makedirs(os.path.join(sRepo, ".vaibify"))
    with open(os.path.join(sRepo, ".vaibify", "environment.json"), "w") as f:
        json.dump({"iSourceDateEpoch": I_RECORDED_EPOCH}, f)
    fnSeamsOnTheLocalRepository(sRepo)
    dictVariables = _fdictInject(sRepo)
    assert _fiEpochOfPrefix(dictVariables) == I_HEAD_EPOCH
    assert "could not be determined" in dictVariables[
        determinismEnvironment.S_DETERMINISM_EPOCH_SOURCE_KEY
    ]


def test_an_envelope_recording_no_epoch_dates_from_head(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    sRepo = _fsPublishedClone(tmp_path, S_READER_EMAIL, bRecordEpoch=False)
    fnSeamsOnTheLocalRepository(sRepo)
    assert _fiEpochOfPrefix(_fdictInject(sRepo)) == I_HEAD_EPOCH


@pytest.mark.falsification
def test_a_host_mode_run_never_replays(
    tmp_path, fnSeamsOnTheLocalRepository, monkeypatch,
):
    """Kills: replaying the recorded epoch for a project that runs on the host."""
    sRepo = _fsPublishedClone(tmp_path, S_READER_EMAIL)
    fnSeamsOnTheLocalRepository(sRepo, bHostProject=True)
    monkeypatch.setattr(
        determinismEnvironment, "_fsWriteHostMatplotlibSalt",
        AsyncMock(return_value=""),
    )
    dictVariables = _fdictInject(sRepo)
    sEpoch = dictVariables[determinismEnvironment.S_ENV_OVERLAY_KEY][
        "SOURCE_DATE_EPOCH"
    ]
    assert int(sEpoch) == I_HEAD_EPOCH


@pytest.mark.falsification
def test_an_explicit_override_beats_the_recorded_epoch(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    """Kills: letting the recorded replay outrank the rerun's override."""
    sRepo = _fsPublishedClone(tmp_path, S_READER_EMAIL)
    fnSeamsOnTheLocalRepository(sRepo)
    assert _fiEpochOfPrefix(_fdictInject(sRepo, iOverride=42)) == 42


def _fdictPrepareThroughTheRunLane(sRepo):
    async def fnCallback(dictEvent):
        return None
    with patch(
        "vaibify.gui.pipelineRunner._fsEnsureLogsDirectory",
        new=AsyncMock(return_value="/ws/.vaibify/logs"),
    ), patch("vaibify.gui.pipelineLogger.fnPruneOldLogs", new=AsyncMock()):
        _sLog, listLogLines, _fnLogging, dictVariables = asyncio.run(
            _ftPrepareLogAndVariables(
                MagicMock(), "cid",
                {"sProjectRepoPath": sRepo, "sWorkflowName": "wf",
                 "listSteps": []},
                sRepo + "/.vaibify/workflows/wf.json", fnCallback,
            )
        )
    return listLogLines, dictVariables


def _fdictPrepareThroughTheTestLane(sRepo):
    from vaibify.gui import pipelineTestRunner
    dictSeen = {}

    async def fiCapture(connectionDocker, sContainerId, dictWorkflow,
                        dictVars, fnStatusCallback):
        dictSeen.update(dictVars)
        return 0

    async def fnStatus(dictEvent):
        dictSeen.setdefault("listEvents", []).append(dictEvent)

    with patch.object(pipelineTestRunner, "_fiRunTestsForAllSteps", fiCapture):
        asyncio.run(pipelineTestRunner.fiRunAllTests(
            MagicMock(), "cid", {"sProjectRepoPath": sRepo, "listSteps": []},
            sRepo, fnStatus,
        ))
    return dictSeen


@pytest.mark.falsification
def test_the_run_lane_and_the_test_lane_get_the_same_epoch(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    """Kills: moving the decision into one dispatch (the other uses HEAD)."""
    sRepo = _fsPublishedClone(tmp_path, S_READER_EMAIL)
    fnSeamsOnTheLocalRepository(sRepo)
    _listLines, dictRunVariables = _fdictPrepareThroughTheRunLane(sRepo)
    dictTestVariables = _fdictPrepareThroughTheTestLane(sRepo)
    assert _fiEpochOfPrefix(dictRunVariables) == I_RECORDED_EPOCH
    assert _fiEpochOfPrefix(dictTestVariables) == I_RECORDED_EPOCH


def test_the_run_log_names_the_epoch_and_its_source(
    tmp_path, fnSeamsOnTheLocalRepository,
):
    sRepo = _fsPublishedClone(tmp_path, S_READER_EMAIL)
    fnSeamsOnTheLocalRepository(sRepo)
    listLines, _dictVariables = _fdictPrepareThroughTheRunLane(sRepo)
    assert any(
        str(I_RECORDED_EPOCH) in sLine
        and "replaying the author's recorded epoch" in sLine
        for sLine in listLines
    ), listLines
    sRepoOwn = _fsPublishedClone(tmp_path / "own", S_AUTHOR_EMAIL)
    fnSeamsOnTheLocalRepository(sRepoOwn)
    listOwnLines, _dictOwn = _fdictPrepareThroughTheRunLane(sRepoOwn)
    assert any(
        str(I_HEAD_EPOCH) in sLine and "dating from HEAD" in sLine
        for sLine in listOwnLines
    ), listOwnLines
