"""The open-time remote refresh, and what it must never claim.

Reopening a project after a day showed orange Published-copies badges
purely because the cached verify had aged past ``F_MAX_STALE_HOURS``.
The fix is to ask again on entry and pulse each configured badge until
its own answer arrives. These tests defend the three properties that
make the pulse honest rather than merely friendlier:

* A check that cannot complete settles to UNCHECKABLE with a reason and
  leaves the last good cached record byte-for-byte where it was. "I
  could not reach GitHub" is not a claim about whether the published
  copies match.
* A remote the workflow has not configured is never marked, so it never
  pulses for an answer that is not coming.
* A pulse cannot outlive the thing it waits for: a launch the carrier
  refuses settles every badge at once, and a check that never returns
  ages out when the state is READ.
"""

import asyncio
import json
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.gui import pipelineServer
from vaibify.gui.routes import remoteRefreshRoutes
from vaibify.reproducibility import remoteCheckState, scheduledReverify


S_CONTAINER_ID = "remoterefresh_cid"
S_DECOY_CONTAINER_ID = "remoterefresh_decoy"
S_REFRESH_PATH = "/api/workflow/{0}/remotes/refresh"


@pytest.fixture(autouse=True)
def fixtureForgottenChecks():
    """Clear this process's check registry around every test."""
    remoteCheckState.fnForgetResource(S_CONTAINER_ID)
    remoteCheckState.fnForgetResource(S_DECOY_CONTAINER_ID)
    yield
    remoteCheckState.fnForgetResource(S_CONTAINER_ID)
    remoteCheckState.fnForgetResource(S_DECOY_CONTAINER_ID)


def _fdictBuildWorkflow(sProjectRepo):
    """Return a workflow configuring GitHub and Zenodo, nothing else."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {
            "github": {"sOwner": "someone", "sRepo": "something"},
            "zenodo": {"sDoi": "10.5281/zenodo.1"},
        },
        "listSteps": [],
    }


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    return sRepo


@pytest.fixture
def fixtureWorkflow(fixtureProjectRepo):
    return _fdictBuildWorkflow(fixtureProjectRepo)


@pytest.fixture
def fixtureClient(fixtureWorkflow, monkeypatch):
    """A bare app carrying only the refresh route.

    The carrier is stood down (see ``tests/carrierStandDown``): this
    module proves what the route DOES, never the admission it runs
    under. The stand-down still STARTS the durable task, so the
    marking these tests assert is the marking production performs.
    """
    fnStandCarrierDown(monkeypatch, remoteRefreshRoutes)
    monkeypatch.setattr(
        remoteRefreshRoutes, "_fbContainerSealedOffTheNetwork",
        lambda sContainerId: False,
    )
    app = FastAPI()
    dictCtx = {
        "docker": None,
        "workflows": {S_CONTAINER_ID: fixtureWorkflow},
        "paths": {},
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
    }
    remoteRefreshRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    """Stand the carrier down for the tests that drive the worker.

    The per-service WRITE opens a real mode-(b) carrier now, so a test
    calling the worker directly needs the stand-down that the client
    fixture already applies. See ``tests/carrierStandDown`` for what
    it costs; the admission itself is proven in
    ``tests/testCarrierMigratedRoutes.py``.
    """
    fnStandCarrierDown(monkeypatch, remoteRefreshRoutes)


def _fdictCarrierFor(sContainerId=S_CONTAINER_ID):
    """The carrier bundle the route hands the background worker.

    Shaped like the real one; with the carrier stood down the mode-(b)
    write runs inline, so these tests observe what the worker DOES
    without needing an owner record.
    """
    return {
        "appState": None,
        "sContainerName": "fake-container",
        "sContainerId": sContainerId,
        "dictLaneTuple": {"sContainerName": "fake-container"},
    }


def _fnRefuseEveryVerify(monkeypatch, sMessage):
    """Make the real verify chain fail the way an unreachable remote does."""
    def fdictRaise(filesRepo, dictWorkflow, sService, sNowIso=None):
        raise RuntimeError(sMessage)

    monkeypatch.setattr(
        scheduledReverify, "fdictVerifyRemoteService", fdictRaise,
    )


def _fnAnswerEveryVerify(monkeypatch, listCalls):
    """Make the verify chain answer with a minimal matching status."""
    def fdictAnswer(filesRepo, dictWorkflow, sService, sNowIso=None):
        listCalls.append(sService)
        return {
            "sService": sService,
            "sLastVerified": "2026-08-30T00:00:00Z",
            "iTotalFiles": 1,
            "iMatching": 1,
            "listDiverged": [],
            "listComparedPaths": ["a.txt"],
            "iScopeVersion": 3,
        }

    monkeypatch.setattr(
        scheduledReverify, "fdictVerifyRemoteService", fdictAnswer,
    )


# -----------------------------------------------------------------------
# Only configured remotes are ever marked
# -----------------------------------------------------------------------


def test_only_configured_remotes_are_ever_marked(
    fixtureClient, monkeypatch,
):
    """Overleaf and arXiv are unconfigured, so neither may pulse.

    An unconfigured remote that appeared in the map would pulse until
    the read-time timeout aged it out — three minutes of a badge
    promising an answer nobody asked for.
    """
    _fnAnswerEveryVerify(monkeypatch, [])
    responseHttp = fixtureClient.post(
        S_REFRESH_PATH.format(S_CONTAINER_ID),
    )
    assert responseHttp.status_code == 200
    dictChecks = remoteCheckState.fdictDescribeChecks(S_CONTAINER_ID)
    assert sorted(dictChecks.keys()) == ["github", "zenodo"]


def test_a_project_with_no_remotes_starts_nothing(
    fixtureClient, fixtureWorkflow, monkeypatch,
):
    """A project configuring nothing must leave every badge alone."""
    _fnAnswerEveryVerify(monkeypatch, [])
    fixtureWorkflow["dictRemotes"] = {}
    responseHttp = fixtureClient.post(
        S_REFRESH_PATH.format(S_CONTAINER_ID),
    )
    assert responseHttp.json() == {
        "listChecking": [], "listUncheckable": [],
    }
    assert remoteCheckState.fdictDescribeChecks(S_CONTAINER_ID) == {}


def test_a_sealed_container_says_so_instead_of_pulsing(
    fixtureWorkflow, monkeypatch,
):
    """A network-isolated container settles every badge with the reason.

    Nothing can be reached from it, so a pulse would be a promise the
    container cannot keep.
    """
    fnStandCarrierDown(monkeypatch, remoteRefreshRoutes)
    monkeypatch.setattr(
        remoteRefreshRoutes, "_fbContainerSealedOffTheNetwork",
        lambda sContainerId: True,
    )
    app = FastAPI()
    remoteRefreshRoutes.fnRegisterAll(app, {
        "workflows": {S_CONTAINER_ID: fixtureWorkflow},
        "require": lambda *aArgs: None,
    })
    dictBody = TestClient(app).post(
        S_REFRESH_PATH.format(S_CONTAINER_ID),
    ).json()
    assert dictBody["listChecking"] == []
    dictChecks = remoteCheckState.fdictDescribeChecks(S_CONTAINER_ID)
    assert dictChecks["github"]["sState"] == (
        remoteCheckState.S_STATE_UNCHECKABLE
    )
    assert "networking disabled" in dictChecks["github"]["sReason"]


@pytest.mark.falsification
def test_github_is_checked_when_only_the_checkout_configures_it(
    fixtureProjectRepo, monkeypatch,
):
    """A repo with an origin remote and no dictRemotes entry still counts.

    This is how real projects are shaped: nothing records
    ``dictRemotes.github``, and the verify derives owner and repo from
    the checkout's ``origin``. A key-presence predicate skipped GitHub
    on exactly those projects — the manual "Verify now" worked while
    the open-time refresh never re-checked it, so the octocat sat on
    whatever the last verify had said and its badge never pulsed.
    Observed on a real project, 2026-08-30.

    Kills: reverting flistSelectConfiguredServices to a
    ``sService in dictRemotes`` membership test, which drops GitHub
    here.
    """
    os.makedirs(os.path.join(fixtureProjectRepo, ".git"), exist_ok=True)
    with open(
        os.path.join(fixtureProjectRepo, ".git", "config"), "w",
        encoding="utf-8",
    ) as fileConfig:
        fileConfig.write(
            '[remote "origin"]\n'
            "\turl = https://github.com/someone/something.git\n"
        )
    dictWorkflow = {
        "sProjectRepoPath": fixtureProjectRepo,
        "dictRemotes": {},
        "listSteps": [],
    }
    listServices = scheduledReverify.flistSelectConfiguredServices(
        dictWorkflow, fixtureProjectRepo,
    )
    assert "github" in listServices, (
        "a project whose repository has an origin remote was not "
        "counted as having GitHub configured, so its badge will never "
        f"be re-checked on open: {listServices}"
    )


def test_a_project_with_neither_a_remote_nor_an_origin_selects_nothing(
    fixtureProjectRepo,
):
    """The predicate must not widen into 'always GitHub'.

    The falsification above would also pass for a predicate that
    returned GitHub unconditionally, which would pulse a badge on
    every project that has no mirror at all.
    """
    listServices = scheduledReverify.flistSelectConfiguredServices(
        {"sProjectRepoPath": fixtureProjectRepo,
         "dictRemotes": {}, "listSteps": []},
        fixtureProjectRepo,
    )
    assert listServices == [], (
        f"a project with no remotes selected {listServices}"
    )


# -----------------------------------------------------------------------
# A failed check never overwrites the last good record
# -----------------------------------------------------------------------


@pytest.mark.falsification
def test_an_unreachable_remote_leaves_the_cached_record_untouched(
    fixtureProjectRepo, fixtureWorkflow, monkeypatch,
):
    """The one property that makes "never red" honest.

    The badge keeps whatever colour the last completed verify earned,
    because that verify's record is still on disk exactly as it was.
    Drives the real ``fdictAttemptOneVerify``, not a stand-in, since
    the no-write-on-failure rule lives inside it.

    Kills: making the failed check settle as SETTLED instead of
    uncheckable, which tells the researcher a remote was compared
    when the comparison never ran.
    """
    sCachePath = os.path.join(
        fixtureProjectRepo, ".vaibify", "syncStatus.json",
    )
    dictCached = {"github": {
        "sService": "github", "sLastVerified": "2026-08-01T00:00:00Z",
        "iTotalFiles": 4, "iMatching": 4, "listDiverged": [],
    }}
    with open(sCachePath, "w", encoding="utf-8") as fileCache:
        json.dump(dictCached, fileCache)
    baBefore = open(sCachePath, "rb").read()

    _fnRefuseEveryVerify(monkeypatch, "could not resolve api.github.com")
    asyncio.run(remoteRefreshRoutes._fnCheckOneRemote(
        _fdictCarrierFor(), fixtureWorkflow, fixtureProjectRepo,
        "github",
    ))

    dictCheck = remoteCheckState.fdictDescribeChecks(
        S_CONTAINER_ID,
    )["github"]
    assert dictCheck["sState"] == remoteCheckState.S_STATE_UNCHECKABLE
    assert "api.github.com" in dictCheck["sReason"]
    assert open(sCachePath, "rb").read() == baBefore


def test_a_completed_check_settles_and_stops_the_pulse(
    fixtureProjectRepo, fixtureWorkflow, monkeypatch, fixtureCarrierStoodDown,
):
    """A check that answered must not keep its badge pulsing."""
    listCalls = []
    _fnAnswerEveryVerify(monkeypatch, listCalls)
    remoteCheckState.fnMarkChecking(S_CONTAINER_ID, "github")
    asyncio.run(remoteRefreshRoutes._fnCheckOneRemote(
        _fdictCarrierFor(), fixtureWorkflow, fixtureProjectRepo,
        "github",
    ))
    assert listCalls == ["github"]
    assert not remoteCheckState.fbIsCheckInFlight(
        S_CONTAINER_ID, "github",
    )
    assert remoteCheckState.fdictDescribeChecks(
        S_CONTAINER_ID,
    )["github"]["sState"] == remoteCheckState.S_STATE_SETTLED


def test_a_worker_failure_lands_on_the_badge_not_on_the_task(
    fixtureProjectRepo, fixtureWorkflow, monkeypatch,
):
    """A raise here would quarantine the container over a down remote.

    ``_fnRunRefreshWorker`` runs inside a durable carrier task, whose
    failure path marks the journal record NEEDS RECONCILIATION. So the
    worker swallows and records instead — the researcher loses a badge
    reading, never their project.
    """
    async def fnExplode(dictCarrier, dictWorkflow, filesRepo, sService):
        raise ValueError("the adapter itself fell over")

    monkeypatch.setattr(
        remoteRefreshRoutes, "_fnCheckOneRemote", fnExplode,
    )
    asyncio.run(remoteRefreshRoutes._fnRunRefreshWorker(
        {}, _fdictCarrierFor(), fixtureWorkflow, fixtureProjectRepo,
        ["github", "zenodo"],
    ))
    dictChecks = remoteCheckState.fdictDescribeChecks(S_CONTAINER_ID)
    for sService in ("github", "zenodo"):
        assert dictChecks[sService]["sState"] == (
            remoteCheckState.S_STATE_UNCHECKABLE
        )
        assert "fell over" in dictChecks[sService]["sReason"]


@pytest.mark.falsification
def test_a_finished_refresh_bumps_the_epoch_so_badges_repaint(
    fixtureProjectRepo, fixtureWorkflow, monkeypatch, fixtureCarrierStoodDown,
):
    """The per-file octocats read the cache these checks rewrite.

    The sync epoch is the dashboard's only poll-free invalidation
    signal, and the badge map refreshes on nothing else. Without the
    bump the refresh silently improves a record that nothing on screen
    repaints — which is how it shipped the first time.

    Kills: deleting the _fnBumpSoTheBadgesRepaint call from
    _fnRunRefreshWorker, which leaves the epoch where it was.
    """
    _fnAnswerEveryVerify(monkeypatch, [])
    dictCtx = {"dictSyncEpochs": {S_CONTAINER_ID: 7}}
    asyncio.run(remoteRefreshRoutes._fnRunRefreshWorker(
        dictCtx, _fdictCarrierFor(), fixtureWorkflow,
        fixtureProjectRepo, ["github", "zenodo"],
    ))
    assert dictCtx["dictSyncEpochs"][S_CONTAINER_ID] > 7, (
        "the refresh rewrote syncStatus.json and left the sync epoch "
        "untouched, so the per-file badges keep showing the previous "
        "verify's result until the researcher acts"
    )


# -----------------------------------------------------------------------
# A pulse cannot outlive what it waits for
# -----------------------------------------------------------------------


@pytest.mark.falsification
def test_the_refresh_leaves_the_durable_slot_free(
    fixtureWorkflow, monkeypatch,
):
    """A researcher's own action must not be blocked by an automatic one.

    The first version registered the whole sweep as ONE durable task.
    A container has a single durable slot, so for the entire network
    round-trip — measured at ~7s for 29 published paths, and linear in
    the file count — the researcher's Level 3 verification was refused
    with "the container is busy: the remote-status refresh is already
    running". Reported 2026-08-30, by a researcher whose own act of
    opening the project is what started it.

    The correction is that mode (c) was the wrong mode: this is
    NETWORK work with milliseconds of container writing per service,
    so only the write takes a carrier.

    Kills: registering the sweep as a durable task again, which
    re-occupies the slot for the whole round-trip.
    """
    import inspect
    assert "fdictLaunchDurableTask" not in inspect.getsource(
        remoteRefreshRoutes,
    ), (
        "the refresh registers a durable task again, so it holds the "
        "container's only durable slot across a network round-trip "
        "and refuses the researcher's own verification"
    )
    # And the route still answers immediately with what it started —
    # the property the durable launch was there to provide.
    fnStandCarrierDown(monkeypatch, remoteRefreshRoutes)
    monkeypatch.setattr(
        remoteRefreshRoutes, "_fbContainerSealedOffTheNetwork",
        lambda sContainerId: False,
    )
    _fnAnswerEveryVerify(monkeypatch, [])
    app = FastAPI()
    remoteRefreshRoutes.fnRegisterAll(app, {
        "workflows": {S_CONTAINER_ID: fixtureWorkflow},
        "require": lambda *aArgs: None,
    })
    dictBody = TestClient(app).post(
        S_REFRESH_PATH.format(S_CONTAINER_ID),
    ).json()
    assert sorted(dictBody["listChecking"]) == ["github", "zenodo"]


@pytest.mark.falsification
def test_a_check_that_never_returns_stops_pulsing(monkeypatch):
    """The timeout is evaluated on READ, so a hung worker cannot hide it.

    A worker that never comes back cannot clear its own flag, so a
    timer-based expiry would never fire for the one failure it exists
    to cover.

    Kills: disabling the age-out branch in _fdictProjectCheck, which
    leaves the badge pulsing for the rest of the session.
    """
    remoteCheckState.fnMarkChecking(S_CONTAINER_ID, "github")
    assert remoteCheckState.fbIsCheckInFlight(S_CONTAINER_ID, "github")
    fFrozen = [0.0]
    monkeypatch.setattr(
        remoteCheckState.time, "monotonic", lambda: fFrozen[0],
    )
    fFrozen[0] = remoteCheckState.F_CHECK_TIMEOUT_SECONDS * 1000
    dictCheck = remoteCheckState.fdictDescribeChecks(
        S_CONTAINER_ID,
    )["github"]
    assert dictCheck["sState"] == remoteCheckState.S_STATE_UNCHECKABLE
    assert dictCheck["sReason"] == remoteCheckState.S_TIMEOUT_REASON


def test_a_late_answer_still_settles_a_timed_out_check(monkeypatch):
    """Honest in both directions: could not say, then could."""
    remoteCheckState.fnMarkChecking(S_CONTAINER_ID, "github")
    monkeypatch.setattr(
        remoteCheckState.time, "monotonic",
        lambda: remoteCheckState.F_CHECK_TIMEOUT_SECONDS * 1000,
    )
    remoteCheckState.fnMarkSettled(S_CONTAINER_ID, "github")
    assert remoteCheckState.fdictDescribeChecks(
        S_CONTAINER_ID,
    )["github"]["sState"] == remoteCheckState.S_STATE_SETTLED


# -----------------------------------------------------------------------
# The poll reports it, for the container it was asked about
# -----------------------------------------------------------------------


class _MockDockerForPoll:
    """The narrowest Docker double one file-status poll needs."""

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID, "sShortId": "remref",
            "sName": "remote-refresh-container", "sImage": "ubuntu",
        }]

    def fdictStatPathMtimes(self, sContainerId, listPaths):
        return {}

    def fsHashContainerFileSha256(self, sContainerId, sPath):
        return ""

    def ftResultExecuteCommand(self, sContainerId, sCommand,
                               sWorkdir=None):
        if "find" in sCommand and "workflows" in sCommand:
            return (0, "/workspace/.vaibify/workflows/test.json\n")
        return (0, "")

    def ftRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        from vaibify.docker.dockerConnection import ExecResult
        iExitCode, sOutput = self.ftResultExecuteCommand(
            sContainerId, sCommand, sWorkdir=sWorkdir,
        )
        return ExecResult(
            iExitCode=iExitCode, sStdout=sOutput, sStderr="",
        )

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath.endswith(".json"):
            return json.dumps({
                "sWorkflowName": "Poll",
                "sPlotDirectory": "Plot",
                "sFigureType": "pdf",
                "iNumberOfCores": 1,
                "listSteps": [],
            }).encode("utf-8")
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, sContainerId, sPath, baContent, **dictKwargs):
        return None

    def fnWriteFileViaTar(
        self, sContainerId, sPath, baContent, **dictKwargs,
    ):
        return None


def test_the_poll_reports_the_checks_of_the_container_it_was_asked_about(
    monkeypatch,
):
    """The wire key exists, and it is keyed by container.

    A registry read with the wrong key is invisible in a fixture where
    only one project has ever been opened, so a decoy container is
    given a DIFFERENT service and the poll must not report it.
    """
    monkeypatch.setattr(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: _MockDockerForPoll(),
    )
    app = pipelineServer.fappCreateApplication(
        sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
    )
    clientHttp = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
    )
    responseConnect = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": "/workspace/.vaibify/workflows/t.json"},
    )
    assert responseConnect.status_code == 200
    if responseConnect.json().get("sLeaseId"):
        clientHttp.headers["X-Vaibify-Lease"] = (
            responseConnect.json()["sLeaseId"]
        )
    remoteCheckState.fnMarkChecking(S_CONTAINER_ID, "github")
    remoteCheckState.fnMarkChecking(S_DECOY_CONTAINER_ID, "zenodo")

    dictPoll = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/file-status",
    ).json()
    assert dictPoll["dictRemoteChecks"] == {
        "github": {
            "sState": remoteCheckState.S_STATE_CHECKING, "sReason": "",
        },
    }


@pytest.mark.falsification
def test_the_compare_runs_outside_the_enforced_lane():
    """The compare thread must not inherit the request's lane flag.

    ``asyncio.to_thread`` copies contextvars, so a compare launched
    from a request-context task inherited the route class's
    enforced-lane flag — and its container hashing (an embedded
    script through the GENERAL exec primitive, not a typed read) was
    refused by the mutation gate on every open-triggered refresh.
    Both remotes then reported the raw MutationNotAdmittedError as
    "Could not check", container id and design citation included
    (researcher-reported, 2026-09-02). The scheduled loop runs the
    SAME compare from a plain thread, where the gate's documented
    background remainder applies; the refresh must run it in the same
    lane-free condition, which run_in_executor's no-copy behaviour
    provides. Nothing is dodged: the compare writes nothing, and the
    write half takes its own mode-(b) carrier.

    Kills: In _fdictCompareOutsideTheLane, run the compare through
    asyncio.to_thread instead of loop.run_in_executor, re-inheriting
    the caller's enforced-lane flag.
    """
    from vaibify.config import mutationAdmission

    listLaneSeen = []

    def fdictProbeLane(filesRepo, dictWorkflow, sService):
        del filesRepo, dictWorkflow, sService
        listLaneSeen.append(mutationAdmission.fbLaneEnforced())
        return {"dictStatus": None, "sError": "probe"}

    async def fnDriveFromAnEnforcedLane():
        tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
        try:
            with patch.object(
                scheduledReverify, "fdictVerifyRemoteService",
                side_effect=fdictProbeLane,
            ):
                await remoteRefreshRoutes._fdictCompareOutsideTheLane(
                    {"listSteps": []}, "/tmp/nowhere", "github",
                )
        finally:
            mutationAdmission.fnResetEnforcedLane(tokenLane)

    asyncio.run(fnDriveFromAnEnforcedLane())
    assert listLaneSeen == [False], (
        "the compare thread inherited the request's enforced lane; "
        "every container read it makes will be refused"
    )


def test_a_control_plane_refusal_is_translated_for_the_researcher():
    """No container ids or design citations on the dashboard."""
    from vaibify.config.mutationAdmission import (
        MutationNotAdmittedError,
    )

    sReason = remoteRefreshRoutes._fsDescribeCheckFailure(
        MutationNotAdmittedError(
            "ftRunInContainerStreamed on container 'abc123' was "
            "attempted from a request lane without a commit-guard "
            "admission (design §8)."
        ),
    )
    assert "abc123" not in sReason
    assert "§" not in sReason
    assert "defect in vaibify" in sReason


def test_an_ordinary_network_error_keeps_its_message():
    """The error text is the actionable part of a failed network check."""
    sReason = remoteRefreshRoutes._fsDescribeCheckFailure(
        OSError("zenodo.org: Name or service not known"),
    )
    assert "Name or service not known" in sReason
