"""The hub's "Reproduce a published project" card, driven in a browser.

What a browser test can see that a Python one cannot: the ORDER. The
run request is the one that spends the daemon and opens a shadow, so
it must not leave the page until the researcher has read the
confirmation card and clicked Run -- a modal shown while the request is
already in flight protects nobody. The assertions are therefore about
requests, not words: no run POST before Run is clicked, exactly one
after it, and the progress poll stops the moment the hub reports the
job settled.

The hub answers the routes for real up to the point the fake Docker
adapter cannot: staging a real repository needs a clone under the
researcher's home, so the three routes are intercepted at the page and
answered with the shapes the real routes return. Nothing here proves
the backend; ``tests/testReproductionRoutes.py`` does that.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_JOB_ID = "browserlanejob01"

DICT_STAGED_RESPONSE = {
    "sJobId": S_JOB_ID,
    "dictStaged": {
        "sKind": "git-url",
        "sRepositoryName": "project",
        "sResolvedCommit": "f" * 40,
        "sRemoteUrl": "https://host.example/group/project.git",
        "sWorkflowName": "Demo",
        "sWorkflowPath": ".vaibify/projects/project.json",
        "sPinnedImageReference": "registry.example/demo@sha256:" + "a" * 64,
        "sRequiredPlatform": "linux/amd64",
        "bDepositOnRecord": True,
        "sDepositVersionDoi": "10.5281/zenodo.1234567",
    },
    "dictDaemon": {
        "bReachable": True, "sArchitecture": "arm64",
        "sRequiredPlatform": "linux/amd64", "bArchitectureMatches": False,
    },
    "listChainLinks": ["registry pull", "archived deposit", "local copy"],
}


def _fdictJobView(bLive, sPhase, dictReport=None):
    return {
        "sJobId": S_JOB_ID, "sPhase": sPhase, "bLive": bLive,
        "sStepLabel": "A01", "sStepName": "Make Numbers",
        "iBytes": 0, "iTotalBytes": 0, "sCurrentLink": "",
        "listAttempts": [{"sLink": "registry pull", "bSucceeded": True,
                          "sDetail": ""}],
        "dictStaged": DICT_STAGED_RESPONSE["dictStaged"],
        "dictDaemon": DICT_STAGED_RESPONSE["dictDaemon"],
        "dictAcquired": None, "sReportId": "report01",
        "dictReport": dictReport, "sFailure": "", "bConsumed": True,
        "bAllowEmulation": True,
    }


DICT_REPORT = {
    "sReportId": "report01", "sVerdict": "reproduced",
    "sVerdictRendered": "reproduced under emulation (linux/amd64 image on "
                        "a arm64 host)",
    "iOutputHashesMatched": 2, "iOutputHashesTotal": 2,
    "listDivergedHashes": [], "listCarriedPaths": ["Notes/declaration.md"],
    "listMatchedPaths": ["MakeNumbers/numbers.txt", "Figures/figure.pdf"],
    "dictPlatform": {"sRequiredPlatform": "linux/amd64",
                     "sObtainedPlatform": "linux/amd64",
                     "sDaemonArchitecture": "arm64", "bEmulated": True},
    "sObtainedFrom": "archive", "sImageReferenceRun": "sha256:" + "b" * 64,
    "dictImageRecheck": {"sVerdict": "vacuous", "sReason": "",
                         "bVacuous": True},
    "dictRerunFailure": {}, "sShadowTeardown": "destroyed",
}


def _fdictInterceptTheJob(pageDashboard, iLivePolls=2):
    """Answer the three routes at the page; record every request."""
    dictSeen = {"listStagePosts": [], "listRunPosts": [],
                "listDiscardPosts": [], "iPolls": 0}

    def fnAnswerStage(route):
        dictSeen["listStagePosts"].append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(DICT_STAGED_RESPONSE))

    def fnAnswerRun(route):
        dictSeen["listRunPosts"].append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"bAccepted": True, "sPhase": "pulling"}))

    def fnAnswerPoll(route):
        dictSeen["iPolls"] += 1
        bLive = dictSeen["iPolls"] <= iLivePolls
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(_fdictJobView(
                          bLive, "running" if bLive else "settled",
                          None if bLive else DICT_REPORT)))

    def fnAnswerDiscard(route):
        dictSeen["listDiscardPosts"].append(route.request.url)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"bDiscarded": True}))

    pageDashboard.route("**/api/reproductions/stage", fnAnswerStage)
    pageDashboard.route(f"**/api/reproductions/{S_JOB_ID}/run", fnAnswerRun)
    pageDashboard.route(
        f"**/api/reproductions/{S_JOB_ID}/discard", fnAnswerDiscard,
    )
    pageDashboard.route(f"**/api/reproductions/{S_JOB_ID}", fnAnswerPoll)
    return dictSeen


def _fnOpenTheCard(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector("#btnAddContainer", timeout=15000)
    pageDashboard.click("#btnAddContainer")
    pageDashboard.wait_for_selector("#modalAddChoice", timeout=5000)


def test_the_card_carries_the_ruled_name_and_opens_the_source_form(
    pageDashboard, serverHub,
):
    _fnOpenTheCard(pageDashboard, serverHub)
    assert "Reproduce a published project" in pageDashboard.text_content(
        "#btnChoiceKindReproduce",
    )
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.wait_for_selector(
        "#modalReproducePublished", state="visible", timeout=5000,
    )
    assert pageDashboard.is_hidden("#modalAddChoice")
    assert pageDashboard.is_visible("#reproduceSourceInput")
    assert pageDashboard.is_hidden("#reproduceStageConfirm")


@pytest.mark.falsification
def test_no_run_request_leaves_before_the_researcher_confirms(
    pageDashboard, serverHub,
):
    """The confirmation precedes the run request, not accompanies it.

    Kills: sending the run request from the stage handler as soon as
    the snapshot is staged.
    """
    _fnOpenTheCard(pageDashboard, serverHub)
    dictSeen = _fdictInterceptTheJob(pageDashboard)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill(
        "#reproduceSourceInput", "https://host.example/group/project.git",
    )
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceStageConfirm", state="visible", timeout=5000,
    )
    assert dictSeen["listStagePosts"] == [{
        "sSource": "https://host.example/group/project.git",
        "sWorkflowName": "",
    }]
    sFacts = pageDashboard.text_content("#reproduceConfirmFacts")
    for sFact in ("Demo", "f" * 40, "linux/amd64", "arm64",
                  "does not match", "10.5281/zenodo.1234567"):
        assert sFact in sFacts, sFacts
    assert "registry pull" in pageDashboard.text_content("#reproduceChainLinks")
    assert pageDashboard.is_visible("#reproduceEmulationRow")
    assert not pageDashboard.is_checked("#reproduceEmulationCheckbox")
    pageDashboard.wait_for_timeout(300)
    assert dictSeen["listRunPosts"] == [], (
        "the run request was sent while the confirmation was still on screen"
    )
    pageDashboard.check("#reproduceEmulationCheckbox")
    pageDashboard.click("#btnReproduceRun")
    pageDashboard.wait_for_selector(
        "#reproduceStageProgress", state="visible", timeout=5000,
    )
    assert dictSeen["listRunPosts"] == [{"bAllowEmulation": True}]


def test_declining_the_confirmation_sends_nothing(pageDashboard, serverHub):
    _fnOpenTheCard(pageDashboard, serverHub)
    dictSeen = _fdictInterceptTheJob(pageDashboard)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/p.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceStageConfirm", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnReproduceCancelConfirm")
    pageDashboard.wait_for_selector(
        "#modalReproducePublished", state="hidden", timeout=5000,
    )
    pageDashboard.wait_for_timeout(300)
    assert dictSeen["listRunPosts"] == []


@pytest.mark.falsification
def test_the_poll_stops_when_the_job_settles_and_the_result_is_shown(
    pageDashboard, serverHub,
):
    """A finished job never pulses.

    Kills: removing the disarm from the settled branch of the poll.
    """
    _fnOpenTheCard(pageDashboard, serverHub)
    dictSeen = _fdictInterceptTheJob(pageDashboard, iLivePolls=1)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/p.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceStageConfirm", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnReproduceRun")
    pageDashboard.wait_for_selector(
        "#reproduceStageResult", state="visible", timeout=15000,
    )
    sVerdict = pageDashboard.text_content("#reproduceResultVerdict")
    assert "reproduced under emulation" in sVerdict
    sBody = pageDashboard.text_content("#reproduceResultBody")
    assert "2 of 2" in sBody and "1 carried in unchanged" in sBody
    assert "Notes/declaration.md" in sBody
    assert "archive" in sBody and "vacuous" in sBody
    assert "report01" in sBody
    iPollsAtSettle = dictSeen["iPolls"]
    pageDashboard.wait_for_timeout(5000)
    assert dictSeen["iPolls"] == iPollsAtSettle, (
        "the card kept polling after the hub reported the job settled"
    )


# ---------------------------------------------------------------------
# What a dismissed card and a lost job do (review, 2026-09-07)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_declining_the_confirmation_discards_the_staged_clone(
    pageDashboard, serverHub,
):
    """Hiding the modal used to leave a whole repository on disk.

    Kills: closing the modal without the discard request.
    """
    _fnOpenTheCard(pageDashboard, serverHub)
    dictSeen = _fdictInterceptTheJob(pageDashboard)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/p.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceStageConfirm", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnReproduceCancelConfirm")
    pageDashboard.wait_for_selector(
        "#modalReproducePublished", state="hidden", timeout=5000,
    )
    pageDashboard.wait_for_timeout(400)
    assert len(dictSeen["listDiscardPosts"]) == 1, dictSeen
    assert dictSeen["listRunPosts"] == []


@pytest.mark.falsification
def test_a_job_this_hub_no_longer_holds_stops_the_poll(
    pageDashboard, serverHub,
):
    """A 404 means there is nothing left to wait for.

    Jobs live only as long as the hub that started them, so a restart
    loses one -- and the card used to poll a hub that would never
    answer, forever.

    Kills: returning from the poll's failure branch without disarming.
    """
    _fnOpenTheCard(pageDashboard, serverHub)
    dictSeen = _fdictInterceptTheJob(pageDashboard)

    def fnAnswerGone(route):
        dictSeen["iPolls"] += 1
        route.fulfill(status=404, content_type="application/json",
                      body=json.dumps({"detail": "No reproduction job."}))

    pageDashboard.route(f"**/api/reproductions/{S_JOB_ID}", fnAnswerGone)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/p.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceStageConfirm", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnReproduceRun")
    pageDashboard.wait_for_selector(
        "#reproduceStageResult", state="visible", timeout=15000,
    )
    assert "no longer holding" in pageDashboard.text_content(
        "#reproduceResultBody",
    )
    iAfterSettle = dictSeen["iPolls"]
    pageDashboard.wait_for_timeout(3 * 2000)
    assert dictSeen["iPolls"] == iAfterSettle, (
        "the card kept polling a hub that answered 404"
    )


def test_the_result_names_every_file_and_links_its_report(
    pageDashboard, serverHub,
):
    _fnOpenTheCard(pageDashboard, serverHub)
    _fdictInterceptTheJob(pageDashboard, iLivePolls=1)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/p.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.wait_for_selector(
        "#reproduceStageConfirm", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnReproduceRun")
    pageDashboard.wait_for_selector(
        "#reproduceStageResult", state="visible", timeout=15000,
    )
    sBody = pageDashboard.text_content("#reproduceResultBody")
    for sPath in DICT_REPORT["listMatchedPaths"]:
        assert sPath in sBody, sPath
    assert "re-derived, byte-identical" in sBody
    sHref = pageDashboard.get_attribute(
        "#reproduceResultBody a[href*='reproductions/reports']", "href",
    )
    assert sHref.endswith("/api/reproductions/reports/report01"), sHref


@pytest.mark.falsification
def test_closing_the_card_mid_stage_discards_the_snapshot_that_arrives(
    pageDashboard, serverHub,
):
    """The card can be closed before the job id exists.

    Staging clones a repository, which takes time; a researcher who
    closes the card during it has no job id to discard yet, and the
    snapshot the response eventually names is one nobody asked for.

    Kills: forgetting the close that happened while staging was in
    flight.
    """
    _fnOpenTheCard(pageDashboard, serverHub)
    dictSeen = _fdictInterceptTheJob(pageDashboard)

    def fnAnswerSlowly(route):
        dictSeen["listStagePosts"].append(json.loads(route.request.post_data))
        pageDashboard.wait_for_timeout(700)
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(DICT_STAGED_RESPONSE))

    pageDashboard.route("**/api/reproductions/stage", fnAnswerSlowly)
    pageDashboard.click("#btnChoiceKindReproduce")
    pageDashboard.fill("#reproduceSourceInput", "https://host.example/p.git")
    pageDashboard.click("#btnReproduceStage")
    pageDashboard.click("#btnReproduceCancelSource")
    pageDashboard.wait_for_timeout(2000)
    assert len(dictSeen["listDiscardPosts"]) == 1, dictSeen
    assert pageDashboard.is_hidden("#modalReproducePublished")
