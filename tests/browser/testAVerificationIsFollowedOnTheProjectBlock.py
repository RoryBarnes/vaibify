"""A Level 3 verification is visible until it ends, and its end is shown.

A verification runs for minutes to hours. Its only in-progress display
was the PROOF tab's card, so from any other view the researcher saw
one toast at the start and then nothing (researcher-reported). Now:

- the top bar carries a badge reading "Reproducing", right of the
  project name's PROOF checks (it lived on the Project block's header
  until the researcher's 2026-10-12 placement ruling), orange and
  pulsing, from the moment the server accepts the start and
  for as long as the file-status poll -- the server's recorded phase,
  never a client timer -- says the run is live, including after a
  reload;
- when the run ends, the dashboard reads the server's record of how it
  ended once: a failure is told in a toast carrying the server's own
  reasons and the diagnosis offer, a success leaves the badge reading
  "Reproduced" in the theme color when the server's label says so,
  and either way MANIFEST.sha256 opens in the left viewer and the
  attempt's own reproduced manifest in the right -- or, when the
  attempt wrote none, a plain statement saying so, never an earlier
  attempt's file.

Driven through the real file-status poll: the hub's own response is
fetched and only the running flag (and, where named, the reproduction
label) is rewritten.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_BADGE = "#workflowSwitcher #projectReproductionBadge .project-reproduction-badge"
S_MATCHED_PATH = "MakeNumbers/numbers.json"
S_DIVERGED_PATH = "SecondStage/table.csv"
S_MANIFEST_TEXT = "\n".join([
    "# vaibify manifest v1",
    "a" * 64 + "  " + S_MATCHED_PATH,
    "b" * 64 + "  " + S_DIVERGED_PATH,
]) + "\n"
S_REPRODUCED_TEXT = "\n".join([
    "# vaibify reproduced manifest v1",
    "a" * 64 + "  " + S_MATCHED_PATH,
    "e" * 64 + "  " + S_DIVERGED_PATH,
]) + "\n"
# The root file holds a DIFFERENT text: on a reader's clone it is the
# author's, and in any repository it is whichever attempt wrote last.
# The settled report must open the attempt's own dated file instead.
S_ROOT_REPRODUCED_TEXT = "# vaibify reproduced manifest v1 (root)\n"
S_FAILED_ATTEMPT_PATH = (
    ".vaibify/reproducedManifests/2026-10-12T10-00-00Z_failed.sha256")
S_PASSED_ATTEMPT_PATH = (
    ".vaibify/reproducedManifests/2026-10-12T10-00-00Z_passed.sha256")
LIST_DIVERGED_OUTCOMES = [
    {"sPath": S_MATCHED_PATH, "sExpected": "a" * 64,
     "sObserved": "a" * 64, "sStatus": "matched"},
    {"sPath": S_DIVERGED_PATH, "sExpected": "b" * 64,
     "sObserved": "e" * 64, "sStatus": "diverged"},
]


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (see the sibling files)."""
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


def _fdictAttestation(sStatus, listOutcomes):
    return {
        "iSchemaVersion": 5, "sStatus": sStatus,
        "sAttestedAtUtc": "2026-10-12T10:00:00Z",
        "sManifestDigestAtAttestation": "f" * 64,
        "iOutputHashesMatched": 1, "iOutputHashesTotal": 2,
        "listCarriedPaths": [], "listDivergedHashes": [S_DIVERGED_PATH],
        "listFileOutcomes": listOutcomes,
        "sReproducedManifestPath": "REPRODUCED.sha256",
    }


def _fdictPayload(dictLastSettled, dictCurrent=None, dictReproduction=None):
    return {
        "dictCurrentAttestation": dictCurrent, "listHistory": [],
        "dictLatestReproduction": dictReproduction,
        "listReproductionHistory": [],
        "dictInFlight": None, "dictLastNoVerdict": None,
        "dictLastSettled": dictLastSettled,
        "dictUnsettledTeardown": None, "sLiveManifestDigest": "f" * 64,
    }


def _fdictInstallTheFakeHub(pageDashboard, dictPayload, bRunning,
                            dictLabel=None):
    """Rewrite the poll's running flag and serve the settled answers."""
    dictState = {"bRunning": bRunning, "dictLabel": dictLabel,
                 "iPolls": 0}

    def fnFileStatus(route):
        dictStatus = json.loads(route.fetch().text())
        dictDetail = dictStatus.get("dictWorkflowEnvelopeDetail") or {}
        dictDetail["bRebuildAttestationRunning"] = dictState["bRunning"]
        dictStatus["dictWorkflowEnvelopeDetail"] = dictDetail
        if dictState["dictLabel"] is not None:
            dictStatus["dictReproductionLabel"] = dictState["dictLabel"]
        dictState["iPolls"] += 1
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(dictStatus))

    def fnServeText(sText):
        return lambda route: route.fulfill(
            status=200, content_type="text/plain", body=sText)

    pageDashboard.route("**/api/pipeline/*/file-status*", fnFileStatus)
    pageDashboard.route(
        "**/api/workflow/**/level3/attestation",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictPayload)))
    pageDashboard.route("**/api/figure/**/MANIFEST.sha256",
                        fnServeText(S_MANIFEST_TEXT))
    pageDashboard.route("**/api/figure/**/REPRODUCED.sha256",
                        fnServeText(S_ROOT_REPRODUCED_TEXT))
    pageDashboard.route("**/api/figure/**/2026-10-12T10-00-00Z_*.sha256",
                        fnServeText(S_REPRODUCED_TEXT))
    return dictState


def _fnWaitUntil(pageDashboard, fbCondition, iTimeoutSeconds=30):
    for _ in range(iTimeoutSeconds * 4):
        if fbCondition():
            return
        pageDashboard.wait_for_timeout(250)


_S_READ_BADGE = """(sBadgeSelector) => {
    const elBadge = document.querySelector(sBadgeSelector);
    if (!elBadge) return null;
    // What the badge must sit right of: the last LIT PROOF check beside
    // the project name, or the name itself before any level is reached.
    const listLit = Array.from(document.querySelectorAll(
        "#workflowSwitcher .vaibify-checkmark")).filter(
            (el) => getComputedStyle(el).display !== "none");
    const elStrip = listLit.length ? listLit[listLit.length - 1]
        : document.getElementById("activeWorkflowName");
    const elProbe = document.createElement("span");
    elProbe.style.background = "var(--highlight-color)";
    document.body.appendChild(elProbe);
    const sTheme = getComputedStyle(elProbe).backgroundColor;
    elProbe.remove();
    const style = getComputedStyle(elBadge);
    return {
        sText: elBadge.textContent,
        sAnimation: style.animationName,
        sBackground: style.backgroundColor,
        sTheme: sTheme,
        fBadgeLeft: elBadge.getBoundingClientRect().left,
        fStripRight: elStrip ? elStrip.getBoundingClientRect().right : -1,
        bProofTabActive: document.getElementById("panelProof")
            .classList.contains("active"),
    };
}"""

_S_READ_VIEWERS = """() => {
    const fsRead = (sId) => {
        // The manifest's own text when one is open (the viewport also
        // carries a read-only label), otherwise whatever it says.
        const el = document.getElementById(sId);
        if (!el) return "";
        const elPre = el.querySelector("pre");
        return elPre ? elPre.textContent : el.textContent;
    };
    return {sA: fsRead("viewportA"), sB: fsRead("viewportB")};
}"""


def _fdictReadBadge(pageDashboard):
    return pageDashboard.evaluate(_S_READ_BADGE, S_BADGE)


def _fsBadgeText(pageDashboard):
    return (_fdictReadBadge(pageDashboard) or {}).get("sText", "")


def _flistToastTexts(pageDashboard):
    return pageDashboard.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#toastContainer .toast')).map(el => el.textContent)""")


def _fsToastContaining(pageDashboard, sText):
    for sToast in _flistToastTexts(pageDashboard):
        if sText in sToast:
            return sToast
    return ""


def _fdictWaitForViewers(pageDashboard, fbReady):
    dictViewers = {}

    def fbLoaded():
        dictViewers.update(pageDashboard.evaluate(_S_READ_VIEWERS))
        return fbReady(dictViewers)
    _fnWaitUntil(pageDashboard, fbLoaded)
    return dictViewers


def _fnAnswerReadinessReady(pageDashboard):
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps
    dictGaps = {
        sKey: (True if isinstance(objValue, bool) else objValue)
        for sKey, objValue in fdictL3ReadinessGaps(
            {}, "/nonexistent-repo-for-shape",
        ).items()
    }
    pageDashboard.route(
        "**/api/workflow/**/level3/readiness",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"iProofLevel": 3,
                             "dictL3ReadinessGaps": dictGaps})))


@pytest.mark.falsification
def test_choosing_verify_shows_a_pulsing_reproducing_badge_until_it_ends(
    pageDashboard, serverHub,
):
    """Accepted start -> orange pulsing "Reproducing" -> gone at the end.

    Kills: removing the running badge's pulse -- a still badge over a
    run that will take hours reads exactly like one left behind.
    """
    dictState = _fdictInstallTheFakeHub(
        pageDashboard, _fdictPayload(
            {"sPhase": "passed", "listReasons": [],
             "sReproducedManifestPath": S_PASSED_ATTEMPT_PATH,
             "sRecordKind": "attestation"},
            dictCurrent=_fdictAttestation("passed", [])),
        bRunning=False)
    _fnAnswerReadinessReady(pageDashboard)

    def fnAcceptTheStart(route):
        dictState["bRunning"] = True
        route.fulfill(status=200, content_type="application/json",
                      body='{"bAccepted": true, "sPhase": "starting"}')

    pageDashboard.route("**/api/workflow/**/level3/verify",
                        fnAcceptTheStart)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    assert _fdictReadBadge(pageDashboard) is None, (
        "a badge is drawn before any verification was started")
    pageDashboard.click("#toolbarMenuRun .toolbar-menu-trigger")
    pageDashboard.click("#btnVerifyLevel3")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproducing",
                 iTimeoutSeconds=10)

    dictBadge = _fdictReadBadge(pageDashboard)
    assert dictBadge and dictBadge["sText"] == "Reproducing", (
        f"no Reproducing badge after the server accepted: {dictBadge!r}")
    assert dictBadge["bProofTabActive"] is False
    assert dictBadge["sAnimation"] == "pulse", (
        f"the running badge does not pulse: {dictBadge['sAnimation']!r}")
    assert dictBadge["sBackground"] == "rgb(224, 148, 1)", (
        f"the running badge is not orange: {dictBadge['sBackground']!r}")
    assert dictBadge["fBadgeLeft"] >= dictBadge["fStripRight"] >= 0, (
        "the badge is not to the right of the level checks")

    dictState["bRunning"] = False
    _fnWaitUntil(pageDashboard,
                 lambda: _fdictReadBadge(pageDashboard) is None)
    assert _fdictReadBadge(pageDashboard) is None, (
        "the Reproducing badge outlived the run")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_live_verification_is_shown_again_after_a_reload(
    pageDashboard, serverHub,
):
    """Nothing the page remembered is needed: the poll says it is live.

    Kills: never rendering the running badge from the poll's flag.
    """
    _fdictInstallTheFakeHub(
        pageDashboard, _fdictPayload(None), bRunning=True)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproducing")
    assert _fsBadgeText(pageDashboard) == "Reproducing"
    pageDashboard.reload(wait_until="load")
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproducing")
    dictBadge = _fdictReadBadge(pageDashboard)
    assert dictBadge and dictBadge["sText"] == "Reproducing", (
        f"a live verification vanished on reload: {dictBadge!r}")
    assert dictBadge["sAnimation"] == "pulse"


@pytest.mark.falsification
def test_a_diverged_run_is_told_with_its_reasons_and_both_manifests_open(
    pageDashboard, serverHub,
):
    """Failure: badge gone, a toast in the server's words, both files.

    Kills: dropping the failure report -- the badge stops and nothing
    says the run failed.
    """
    dictState = _fdictInstallTheFakeHub(
        pageDashboard, _fdictPayload(
            {"sPhase": "failed", "listReasons": [S_DIVERGED_PATH],
             "sReproducedManifestPath": S_FAILED_ATTEMPT_PATH,
             "sRecordKind": "attestation"},
            dictCurrent=_fdictAttestation("failed", LIST_DIVERGED_OUTCOMES)),
        bRunning=True)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproducing")
    dictState["bRunning"] = False
    _fnWaitUntil(pageDashboard, lambda: _fsToastContaining(
        pageDashboard, "Level 3 verification failed"))

    sToast = _fsToastContaining(pageDashboard, "Level 3 verification failed")
    assert sToast, f"no failure toast: {_flistToastTexts(pageDashboard)!r}"
    assert S_DIVERGED_PATH in sToast, (
        f"the server's reason is not in the toast: {sToast!r}")
    assert "Click to run a diagnosis" in sToast, sToast
    assert _fdictReadBadge(pageDashboard) is None, (
        "a badge remains over a failed verification")
    dictViewers = _fdictWaitForViewers(
        pageDashboard, lambda d: "reproduced manifest v1" in d.get("sB", "")
        and "# vaibify manifest v1" in d.get("sA", ""))
    assert dictViewers["sA"].strip() == S_MANIFEST_TEXT.strip(), dictViewers
    assert dictViewers["sB"].strip() == S_REPRODUCED_TEXT.strip(), (
        "the right viewer does not hold this attempt's own manifest: "
        f"{dictViewers!r}")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_an_attempt_with_no_verdict_says_it_wrote_no_reproduced_manifest(
    pageDashboard, serverHub,
):
    """The right viewer states the absence; an older file is not shown.

    Kills: guessing the root REPRODUCED.sha256 when the attempt wrote
    nothing -- an EARLIER attempt's file would read as this result.
    """
    sReason = ("refused before any step ran: git could not say whose "
               "manifest this is")
    dictState = _fdictInstallTheFakeHub(
        pageDashboard, _fdictPayload(
            {"sPhase": "no-verdict", "listReasons": [sReason],
             "sReproducedManifestPath": "", "sRecordKind": ""},
            dictCurrent=_fdictAttestation("failed", LIST_DIVERGED_OUTCOMES)),
        bRunning=True)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproducing")
    # The earlier attempt's file is already open in the right viewer.
    pageDashboard.evaluate("""() => VaibifyFigureViewer
        .fnDisplayRecordInViewer("B", "REPRODUCED.sha256", null)""")
    _fdictWaitForViewers(
        pageDashboard, lambda d: "(root)" in d.get("sB", ""))
    dictState["bRunning"] = False
    dictViewers = _fdictWaitForViewers(
        pageDashboard, lambda d: "produced no reproduced manifest" in
        d.get("sB", "") and "# vaibify manifest v1" in d.get("sA", ""))

    assert ("This attempt produced no reproduced manifest: " + sReason
            in dictViewers["sB"]), dictViewers
    assert "reproduced manifest v1" not in dictViewers["sB"], (
        "an earlier attempt's reproduced manifest is still shown")
    assert dictViewers["sA"].strip() == S_MANIFEST_TEXT.strip(), dictViewers
    sToast = _fsToastContaining(pageDashboard, "without reaching a verdict")
    assert sReason in sToast, _flistToastTexts(pageDashboard)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_reproduced_run_turns_the_badge_and_opens_both_manifests(
    pageDashboard, serverHub,
):
    """Success: "Reproduced" in the theme color, still, and both files.

    Kills: not opening the manifests when the run ends.
    """
    dictLabel = {
        "bShow": True, "sState": "reproduced", "sReason": "",
        "bEmulated": False, "sPlatform": "linux/amd64",
        "sRecordedIso": "2026-10-12T10:00:00+00:00",
        "sLatestAttemptIso": "", "sLatestAttemptVerdict": "",
    }
    listMatched = [dict(d, sObserved=d["sExpected"], sStatus="matched")
                   for d in LIST_DIVERGED_OUTCOMES]
    dictReproduction = {
        "sVerdict": "reproduced", "listFileOutcomes": listMatched,
        "sReproducedManifestPath": S_PASSED_ATTEMPT_PATH,
        "sCreatedAtIso": "2026-10-12T10:00:00+00:00",
    }
    dictState = _fdictInstallTheFakeHub(
        pageDashboard, _fdictPayload(
            {"sPhase": "passed", "listReasons": [],
             "sReproducedManifestPath": S_PASSED_ATTEMPT_PATH,
             "sRecordKind": "reproduction"},
            dictReproduction=dictReproduction),
        bRunning=True, dictLabel=dictLabel)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproducing")
    dictState["bRunning"] = False
    _fnWaitUntil(pageDashboard,
                 lambda: _fsBadgeText(pageDashboard) == "Reproduced")
    dictViewers = _fdictWaitForViewers(
        pageDashboard, lambda d: "reproduced manifest v1" in d.get("sB", "")
        and "# vaibify manifest v1" in d.get("sA", ""))

    dictBadge = _fdictReadBadge(pageDashboard)
    assert dictBadge and dictBadge["sText"] == "Reproduced", dictBadge
    assert dictBadge["sAnimation"] in ("none", ""), dictBadge
    assert dictBadge["sBackground"] == dictBadge["sTheme"], (
        f"the Reproduced badge is not the theme color: {dictBadge!r}")
    assert dictViewers["sA"].strip() == S_MANIFEST_TEXT.strip(), dictViewers
    assert dictViewers["sB"].strip() == S_REPRODUCED_TEXT.strip(), dictViewers
    # The viewer's file list names the attempt by its dated file.
    sChosen = pageDashboard.evaluate("""() => {
        const el = document.getElementById("selectFigureB");
        const elOption = el ? el.options[el.selectedIndex] : null;
        return elOption ? elOption.textContent : "";
    }""")
    assert sChosen == "2026-10-12T10-00-00Z_passed.sha256", sChosen
    assert not _fsToastContaining(pageDashboard, "Level 3 verification"), (
        f"a success was reported as a failure: "
        f"{_flistToastTexts(pageDashboard)!r}")
    assert pageDashboard.listPageErrors == []
