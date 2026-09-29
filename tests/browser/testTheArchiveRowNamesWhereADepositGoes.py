"""The Environment archive row says where a deposit goes before it goes.

The row used to offer "Deposit this image in Zenodo" and nothing else:
not sandbox or permanent, not whether the upload would continue the
record this project had already deposited. A researcher whose earlier
deposit was permanent, and whose project setting said sandbox, was one
click from an unrelated sandbox record -- and could not tell
(researcher-reported, 2026-09-28).

These are claims about the SCREEN and the REQUEST, so they are driven
in a real page: the options and the recommendation the row renders, the
button following a real click on an option, and the body and the
confirmation of the deposit request that click leads to.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_PREVIOUS_VERSION_DOI = "10.5281/zenodo.8100002"
S_PREVIOUS_CONCEPT_DOI = "10.5281/zenodo.8100001"


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test."""
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


def _fdictPlanContinuingAPermanentRecord():
    """The plan the backend builds for a permanent record and a new image.

    Built by the real ``fdictBuildDepositPlan`` rather than written by
    hand, so the row is driven with the shape the poll actually ships.
    """
    from vaibify.reproducibility.archiveDepositPlan import (
        fdictBuildDepositPlan,
    )
    return fdictBuildDepositPlan({
        "sImageDigest": "registry.example/p@sha256:" + "a" * 64,
        "dictImageArchiveLineage": {
            "sVersionDoi": S_PREVIOUS_VERSION_DOI,
            "sConceptDoi": S_PREVIOUS_CONCEPT_DOI,
            "sZenodoService": "zenodo",
        },
    }, bCovered=False)


def _fdictArchivePayload():
    return {
        "sState": "none", "dictRecord": None, "dictDeposit": None,
        "listIssues": [], "bAnswered": False, "sAnswer": "",
        "dictDepositPlan": _fdictPlanContinuingAPermanentRecord(),
    }


_S_MOUNT_ROW = """(dictArchive) => {
    /* Mounted inside #panelSteps, where the click delegation lives,
       but beside #projectBlock rather than in it: the poll rewrites
       #projectBlock on its own cadence and would replace the row
       under the test. */
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictImageArchive: dictArchive},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(['environmentArchive']),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.id = 'archiveRowUnderTest';
    elHost.innerHTML = sHtml;
    document.getElementById('panelSteps').appendChild(elHost);
}"""

_S_READ_ROW = """() => {
    const elHost = document.getElementById('archiveRowUnderTest');
    const elRow = Array.from(elHost.querySelectorAll(
        '.requirement-row-header')).find(
            el => (el.dataset.req || '') === 'environmentArchive')
        .closest('.requirement-row');
    const elButton = elRow.querySelector('.environment-archive-continue');
    const dictChecked = {};
    const listRecommended = [];
    elRow.querySelectorAll('.environment-archive-answer').forEach(el => {
        dictChecked[el.value] = el.checked;
        if (el.closest('label').querySelector(
                '.environment-archive-recommended')) {
            listRecommended.push(el.value);
        }
    });
    return {
        sText: elRow.textContent,
        dictChecked: dictChecked,
        listRecommended: listRecommended,
        sButton: elButton.textContent,
        sAction: elButton.dataset.wfAction,
        bDisabled: elButton.disabled,
    };
}"""


def _fnMountTheRow(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    pageDashboard.evaluate(_S_MOUNT_ROW, _fdictArchivePayload())


def _fnChoose(pageDashboard, sValue):
    pageDashboard.click(
        "#archiveRowUnderTest .environment-archive-answer"
        f"[value='{sValue}']")


@pytest.mark.falsification
def test_the_row_names_the_earlier_deposit_and_marks_the_recommendation(
    pageDashboard, serverHub,
):
    """A researcher must see they have been through this before.

    And the recommendation is MARKED, never pre-selected: the choice
    mints a public DOI, so the click that makes it is the researcher's.

    Kills: dropping the guidance (the earlier deposit and the
    recommendation) from the form.
    """
    _fnMountTheRow(pageDashboard, serverHub)
    dictSeen = pageDashboard.evaluate(_S_READ_ROW)

    assert S_PREVIOUS_VERSION_DOI in dictSeen["sText"], dictSeen["sText"]
    assert "deposited its environment before" in dictSeen["sText"]
    assert "Recommended: deposit a new version of the record " + (
        S_PREVIOUS_CONCEPT_DOI) in dictSeen["sText"], dictSeen["sText"]
    assert dictSeen["listRecommended"] == ["new-version"], dictSeen
    assert not any(dictSeen["dictChecked"].values()), (
        "an option arrives pre-selected: " + str(dictSeen["dictChecked"])
    )
    assert dictSeen["bDisabled"] is True, (
        "the button offers an action before anything was chosen"
    )


@pytest.mark.falsification
def test_the_button_names_the_option_the_researcher_clicked(
    pageDashboard, serverHub,
):
    """Clicking an option makes the button say what it will do.

    Kills: the click delegation no longer reaching
    ``fnFollowArchiveChoice``, which leaves the button disabled and
    naming nothing whatever is chosen.
    """
    _fnMountTheRow(pageDashboard, serverHub)

    _fnChoose(pageDashboard, "new-record-sandbox")
    dictSandbox = pageDashboard.evaluate(_S_READ_ROW)
    assert dictSandbox["bDisabled"] is False
    assert dictSandbox["sAction"] == "deposit-environment-archive"
    assert "sandbox" in dictSandbox["sButton"], dictSandbox["sButton"]
    assert "testing" in dictSandbox["sButton"], dictSandbox["sButton"]

    _fnChoose(pageDashboard, "declined")
    dictDeclined = pageDashboard.evaluate(_S_READ_ROW)
    assert dictDeclined["sAction"] == "answer-environment-archive"
    assert dictDeclined["sButton"] == "Save this answer"


@pytest.mark.falsification
def test_the_deposit_sends_the_choice_only_after_a_confirmation_naming_it(
    pageDashboard, serverHub,
):
    """The confirmation names the destination, and nothing leaves first.

    Then the refusal a missing token earns opens the connection dialog
    on the instance the researcher CHOSE -- a deposit can now go to
    either Zenodo, so a fixed prompt would ask for the wrong token.

    Kills: the confirmation falling back to a fixed message that names
    no destination.
    """
    _fnMountTheRow(pageDashboard, serverHub)
    listBodies = []

    def fnAnswerTheDeposit(route):
        listBodies.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {
                "sError": "ZENODO-TOKEN-MISSING",
                "sInstance": "production",
                "sMessage": "No production Zenodo token is stored.",
            }}),
        )

    pageDashboard.route(
        "**/api/workflow/**/environment-archive/deposit",
        fnAnswerTheDeposit,
    )
    _fnChoose(pageDashboard, "new-version")
    pageDashboard.click("#archiveRowUnderTest .environment-archive-continue")
    pageDashboard.wait_for_selector(
        "#modalConfirm", state="visible", timeout=5000)
    sConfirm = pageDashboard.inner_text("#modalConfirm")
    assert "NEW VERSION of the record " + S_PREVIOUS_CONCEPT_DOI + (
        " on zenodo.org") in sConfirm, sConfirm
    assert listBodies == [], "the deposit was sent before the confirmation"

    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "#modalConnectionSetup", state="visible", timeout=5000)
    assert listBodies == [{"sChoice": "new-version"}], listBodies
    assert pageDashboard.is_checked(
        "input[name='zenodoInstance'][value='production']"), (
        "the token prompt did not ask for the instance that was chosen"
    )
