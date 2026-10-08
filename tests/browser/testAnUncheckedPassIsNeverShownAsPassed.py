"""A pass the poll could not check against the files never reads as Passed.

When the poll cannot compare a step's recorded test digests with its
files (the snapshot failed, a read tore, the container omitted a path)
it invalidates nothing -- silence is not a deletion -- but it must not
show the step as green either. The server says so in three places that
are one verdict: the Level 1 cell is not attained, the Level 1
requirement rows for the test axes are unknown, and the step carries a
``test-freshness-unchecked`` blocker.

The screen has to say the same thing in every place it shows the pass.
These are claims about the SCREEN, so a Python test of the gate proves
none of them. The real page loads the real poll, whose answer is
altered in flight to the shape a failed snapshot produces, and the
assertions read what a researcher would see:

* the badge is neither green nor the plain word "Passed", and says the
  freshness could not be checked;
* the requirement row (the info modal's list) marks the axis unknown
  and says why;
* the Level 1 cell is not the attained check;
* clicking the badge runs the diagnosis it promises, rather than
  toggling the row it sits in.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

LIST_TEST_AXES = ("sUnitTest", "sIntegrity", "sQualitative", "sQuantitative")


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds; the symptom is a locked tile intercepting the next click.
    """
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


@pytest.fixture(autouse=True)
def fixtureStopInterceptingThePoll(pageDashboard):
    """Drop the poll route before the page closes.

    A route callback still awaiting the real answer when its page is
    torn down raises ``TargetClosedError`` from inside Playwright's
    event loop, which reads like a product failure and is not one.
    """
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _flistUncheckedRequirements():
    """Level 1 rows as the server writes them for an unchecked pass."""
    listRows = [
        {"sName": sAxis, "bMet": None} for sAxis in LIST_TEST_AXES]
    listRows += [
        {"sName": "user-attestation", "bMet": True},
        {"sName": "timing-clean", "bMet": True},
        {"sName": "input-data-declared", "bMet": True},
    ]
    return listRows


def _fnAlterThePollToAnUncheckedPass(dictBody):
    """Rewrite the poll to what a failed snapshot answers for step 0."""
    dictStepLevels = dictBody.setdefault("dictStepLevels", {})
    dictLevels = dictStepLevels.setdefault("0", {})
    dictLevels["s1"] = {
        "sState": "partial", "iSatisfied": 3, "iTotal": 7,
        "bRegression": False,
        "listRequirements": _flistUncheckedRequirements(),
    }
    dictBody["listBlockers"] = [{
        "iLevel": 1, "iStepIndex": 0, "sStepLabel": "A01",
        "sScope": "step", "sCriterion": "test-freshness-unchecked",
        "listOffendingFiles": ["MakeNumbers/numbers.json"],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": (
            "Could not check whether the files still match the last "
            "test run — not a failure. Click to run a diagnosis"),
    }]
    dictBody["iProofLevel"] = 0
    return dictBody


def _fnInterceptThePoll(pageDashboard):
    def fnHandle(routePoll):
        responsePoll = routePoll.fetch()
        dictBody = _fnAlterThePollToAnUncheckedPass(responsePoll.json())
        routePoll.fulfill(
            response=responsePoll, body=json.dumps(dictBody),
            headers={"content-type": "application/json"})
    pageDashboard.route("**/file-status*", fnHandle)


_S_MARK_THE_STEP_PASSED = """() => {
    const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
    dictStep.dictVerification = {
        sUnitTest: "passed", sIntegrity: "passed",
        sQualitative: "passed", sQuantitative: "passed",
        sUser: "passed",
    };
    VaibifyApp.fnRenderStepList();
}"""


def _fnOpenTheStepAndPassItsTests(pageDashboard, serverHub):
    _fnInterceptThePoll(pageDashboard)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    # The altered poll has been consumed once the warning column carries
    # its diagnosable mark: that class exists only because the blocker
    # the page received names the unchecked criterion.
    pageDashboard.wait_for_selector(
        '.step-item[data-index="0"] '
        '.step-regression-cell.freshness-unchecked-diagnose',
        timeout=20000,
    )
    pageDashboard.evaluate(_S_MARK_THE_STEP_PASSED)
    pageDashboard.click('.step-item[data-index="0"] .step-name')
    pageDashboard.wait_for_selector(
        '.step-detail[data-index="0"] '
        '.verification-row[data-approver="unitTest"]', timeout=10000)


@pytest.mark.falsification
def test_an_unchecked_pass_is_not_shown_as_passed(
    pageDashboard, serverHub,
):
    """The badge, the requirement row and the cell tell one story.

    Kills: rendering the test badge from the step's own state alone
    (a plain green Passed beside a cell that is not attained).
    """
    _fnOpenTheStepAndPassItsTests(pageDashboard, serverHub)
    elBadge = pageDashboard.locator(
        '.step-detail[data-index="0"] '
        '.verification-row[data-approver="unitTest"] '
        '.verification-badge').first
    sClasses = elBadge.get_attribute("class")
    sText = elBadge.inner_text()
    assert "state-unchecked" in sClasses, sClasses
    assert "state-passed" not in sClasses, sClasses
    assert "couldn't check freshness" in sText, sText
    assert "Click to run a diagnosis" in elBadge.get_attribute("title")
    sCellClasses = pageDashboard.locator(
        '.step-item[data-index="0"] .step-level-strip '
        '.step-level-cell[data-level="1"]').first.get_attribute("class")
    assert "level-cell-attained" not in sCellClasses, sCellClasses
    assert "level-cell-partial" in sCellClasses, sCellClasses


@pytest.mark.falsification
def test_the_requirement_rows_read_unknown_and_say_why(
    pageDashboard, serverHub,
):
    """The info modal lists each test axis as unknown, never as met.

    Kills: rendering an unknown requirement with the stale-remote
    wording (a remedy for a different problem), and rendering it with
    the met check.
    """
    _fnOpenTheStepAndPassItsTests(pageDashboard, serverHub)
    pageDashboard.click(
        '.step-detail[data-index="0"] .step-level-info[data-level="1"]')
    pageDashboard.wait_for_selector(
        "#modalInfo .step-level-requirement-row", timeout=10000)
    listRows = pageDashboard.locator(
        "#modalInfo .step-level-requirement-row")
    dictByLabel = {}
    for iRow in range(listRows.count()):
        elRow = listRows.nth(iRow)
        dictByLabel[elRow.locator(
            ".step-level-requirement-label").inner_text()] = elRow
    elUnit = dictByLabel["Unit tests pass"]
    assert elUnit.locator(".envelope-light-unknown").count() == 1
    sMeaning = elUnit.locator(
        ".step-level-requirement-meaning").inner_text()
    assert "could not be checked against the files" in sMeaning, sMeaning
    assert "remote verify" not in sMeaning, sMeaning
    elSignoff = dictByLabel["Your sign-off recorded"]
    assert elSignoff.locator(".envelope-light-unknown").count() == 0


@pytest.mark.falsification
def test_clicking_the_unchecked_badge_runs_the_diagnosis_it_promises(
    pageDashboard, serverHub,
):
    """The tooltip says "Click to run a diagnosis", so the click does.

    The badge sits inside a row whose own click expands the unit-test
    detail; the diagnosis must win, and the row must stay as it was.

    Kills: letting the row's handler take the click first, and
    promising a diagnosis the click never runs.
    """
    _fnOpenTheStepAndPassItsTests(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/system/doctor",
        lambda routeDoctor: routeDoctor.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"listFindings": [{
                "sName": "docker-daemon", "sLevel": "fail",
                "sScope": "host", "sMessage": "Docker daemon not reachable.",
                "sRemediation": "Start the Docker daemon.",
                "sCommand": "colima start"}]})))
    iExpandedBefore = pageDashboard.locator(
        ".step-detail[data-index=\"0\"] .unit-tests-expanded").count()
    pageDashboard.click(
        '.step-detail[data-index="0"] '
        '.verification-row[data-approver="unitTest"] '
        '.freshness-unchecked-diagnose')
    pageDashboard.wait_for_selector(
        "#modalInfo .diagnosis-finding--fail", timeout=10000)
    sReport = pageDashboard.locator("#modalInfo").inner_text()
    assert "the tests passed, but vaibify could not check" in (
        sReport.lower().replace("\n", " ")), sReport
    assert "docker-daemon" in sReport
    assert pageDashboard.locator(
        ".step-detail[data-index=\"0\"] .unit-tests-expanded").count() == (
        iExpandedBefore)
