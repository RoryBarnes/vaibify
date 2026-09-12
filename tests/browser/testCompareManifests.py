"""The reproduced manifest reaches the screen, and only the backend colours it.

A Level 3 verification now keeps every per-file hash and writes
``REPRODUCED.sha256`` beside ``MANIFEST.sha256``. What the PROOF tab
adds on top -- a Compare button, a per-file table, a card for a
reproduction that is the researcher's own record rather than the
author's attestation, and a confirm dialog that names which of those
the run will write -- is all rendering, and rendering is what a Python
test cannot see.

Three properties here are worth a browser rather than a code review:

- The comparison opens the two manifests in the two file viewers with
  NO edit affordance. A record file that could be edited from the
  viewer would be a record a click could rewrite.
- A diverged line is visibly different from a matched one, read back
  as a COMPUTED style off a live line. Asserting the class alone
  passes against a stylesheet carrying no rule for it.
- The line colours come from the mark the backend gave, never from a
  hash comparison in JavaScript. The manifests served here are
  aligned line for line so that the ONLY way the diverged line can be
  coloured is by its path's mark -- and the matched line's hashes are
  made to DIFFER between the two files while its mark says matched,
  so a viewer that compared hashes would paint it wrong.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_MODAL = "#modalConfirm"
S_COMPARE_BUTTON = ".btn-compare-manifests"

# The matched path's hashes are deliberately UNEQUAL between the two
# served manifests: its mark says matched, and the viewer must colour
# by the mark. A viewer that compared the hashes itself would paint
# this line diverged, which is exactly the second authority the rule
# forbids.
S_MATCHED_PATH = "MakeNumbers/numbers.json"
S_DIVERGED_PATH = "SecondStage/table.csv"
S_CARRIED_PATH = "AIDeclaration/AI_USAGE.md"

S_MANIFEST_TEXT = "\n".join([
    "# vaibify manifest v1",
    "a" * 64 + "  " + S_MATCHED_PATH,
    "b" * 64 + "  " + S_DIVERGED_PATH,
    "c" * 64 + "  " + S_CARRIED_PATH,
]) + "\n"
S_REPRODUCED_TEXT = "\n".join([
    "# vaibify reproduced manifest v1",
    "d" * 64 + "  " + S_MATCHED_PATH,
    "e" * 64 + "  " + S_DIVERGED_PATH,
    "c" * 64 + "  " + S_CARRIED_PATH,
]) + "\n"

LIST_FILE_OUTCOMES = [
    {"sPath": S_MATCHED_PATH, "sExpected": "a" * 64,
     "sObserved": "a" * 64, "sStatus": "matched"},
    {"sPath": S_DIVERGED_PATH, "sExpected": "b" * 64,
     "sObserved": "e" * 64, "sStatus": "diverged"},
    {"sPath": S_CARRIED_PATH, "sExpected": "c" * 64,
     "sObserved": "c" * 64, "sStatus": "carried"},
]


def _fdictAttestation(sReproducedManifestPath):
    return {
        "iSchemaVersion": 5,
        "sStatus": "failed",
        "sAttestedAtUtc": "2026-09-11T10:00:00Z",
        "sManifestDigestAtAttestation": "f" * 64,
        "sImageDigest": "sha256:" + "1" * 64,
        "iOutputHashesMatched": 1,
        "iOutputHashesTotal": 2,
        "fDurationSeconds": 12.5,
        "listCarriedPaths": [S_CARRIED_PATH],
        "listDivergedHashes": [S_DIVERGED_PATH],
        "listFileOutcomes": LIST_FILE_OUTCOMES,
        "dictReproductionProvenance": None,
        "sReproducedManifestPath": sReproducedManifestPath,
    }


def _fdictReproduction():
    return {
        "sVerdict": "diverged",
        "iOutputHashesMatched": 1,
        "iOutputHashesTotal": 2,
        "listCarriedPaths": [S_CARRIED_PATH],
        "listDivergedHashes": [S_DIVERGED_PATH],
        "listFileOutcomes": LIST_FILE_OUTCOMES,
        "sReproducedManifestPath": "REPRODUCED.sha256",
        "dictRerunFailure": {},
        "dictPlatform": {"sRequiredPlatform": "linux/amd64",
                         "sObtainedPlatform": "linux/amd64",
                         "sDaemonArchitecture": "amd64",
                         "bEmulated": False},
        "sObtainedFrom": "registry",
        "sImageReferenceRun": "sha256:" + "2" * 64,
        "sCreatedAtIso": "2026-09-11T11:00:00Z",
        "sRecordedNote": "Recorded as a reproduction because the "
                         "attestation on file was committed by another "
                         "identity.",
        "dictImageRecheck": {"sVerdict": "passed", "bVacuous": False},
    }


def _fdictAttestationPayload(dictCurrent, dictLatestReproduction=None):
    return {
        "dictCurrentAttestation": dictCurrent,
        "listHistory": [],
        "dictLatestReproduction": dictLatestReproduction,
        "listReproductionHistory": (
            [dictLatestReproduction] if dictLatestReproduction else []
        ),
        "dictInFlight": None,
        "dictLastNoVerdict": None,
        "dictUnsettledTeardown": None,
        "sLiveManifestDigest": "f" * 64,
    }


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


def _fnAnswerReadiness(pageDashboard, sRecordKind):
    """Answer the readiness pre-flight as ready, naming the record kind.

    Built from the real ``fdictL3ReadinessGaps`` and wrapped in the
    route's real envelope key, as the sibling tests do; ``sRecordKind``
    sits at the envelope level exactly where the route puts it.
    """
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
            body=json.dumps({
                "iProofLevel": 3,
                "dictL3ReadinessGaps": dictGaps,
                "sRecordKind": sRecordKind,
            }),
        ),
    )


def _fdictAnswerAttestationAndManifests(pageDashboard, dictPayload):
    """Serve the attestation payload and both manifests; record fetches."""
    dictSeen = {"listManifestRequests": []}
    pageDashboard.route(
        "**/api/workflow/**/level3/attestation",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictPayload),
        ),
    )

    def fnServe(sText):
        def fnAnswer(route):
            dictSeen["listManifestRequests"].append(
                route.request.url.split("?")[0].rsplit("/", 1)[-1],
            )
            route.fulfill(status=200, content_type="text/plain",
                          body=sText)
        return fnAnswer

    pageDashboard.route(
        "**/api/figure/**/MANIFEST.sha256", fnServe(S_MANIFEST_TEXT),
    )
    pageDashboard.route(
        "**/api/figure/**/REPRODUCED.sha256", fnServe(S_REPRODUCED_TEXT),
    )
    return dictSeen


def _fnOpenTheProofTab(pageDashboard, serverHub, dictPayload,
                       sRecordKind="attestation"):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnAnswerReadiness(pageDashboard, sRecordKind)
    dictSeen = _fdictAnswerAttestationAndManifests(pageDashboard, dictPayload)
    pageDashboard.click('.left-tab[data-panel="proof"]')
    # The Level 3 section renders its body only when expanded, and the
    # seeded project is at Level 0, so the tab opens with Level 1
    # expanded; the attestation card lives inside Level 3.
    pageDashboard.wait_for_selector(
        '.proof-level-section-header[data-level="3"]', timeout=15000,
    )
    pageDashboard.click('.proof-level-section-header[data-level="3"]')
    pageDashboard.wait_for_selector(
        ".proof-attestation-card", state="visible", timeout=15000,
    )
    return dictSeen


# Reads the two viewers back off the live document: what each shows,
# whether either offers an edit control, and the computed colours of
# one diverged and one matched line.
_S_READ_THE_VIEWERS = """() => {
    const fdictReadViewer = (sId) => {
        const elViewport = document.getElementById(sId);
        const elPre = elViewport.querySelector("pre");
        const listToolbarButtons = Array.from(
            elViewport.querySelectorAll(".editor-toolbar button"));
        return {
            sText: elPre ? elPre.textContent : "",
            iToolbarButtons: listToolbarButtons.length,
            listButtonText: listToolbarButtons.map(el => el.textContent),
            iMarkedLines: elViewport.querySelectorAll(
                ".manifest-line").length,
        };
    };
    const fdictReadLine = (sId, sClass) => {
        const elLine = document.querySelector(
            "#" + sId + " .manifest-line-" + sClass);
        if (!elLine) return null;
        const style = window.getComputedStyle(elLine);
        return {
            sClassName: elLine.className,
            sText: elLine.textContent,
            sBackground: style.backgroundColor,
            sColor: style.color,
        };
    };
    return {
        dictA: fdictReadViewer("viewportA"),
        dictB: fdictReadViewer("viewportB"),
        dictDivergedB: fdictReadLine("viewportB", "diverged"),
        dictMatchedB: fdictReadLine("viewportB", "matched"),
        dictDivergedA: fdictReadLine("viewportA", "diverged"),
        dictCarriedA: fdictReadLine("viewportA", "carried"),
    };
}"""


def test_compare_opens_both_manifests_read_only_and_coloured_by_the_mark(
    pageDashboard, serverHub,
):
    dictSeen = _fnOpenTheProofTab(
        pageDashboard, serverHub,
        _fdictAttestationPayload(_fdictAttestation("REPRODUCED.sha256")),
    )
    pageDashboard.wait_for_selector(S_COMPARE_BUTTON, timeout=5000)
    pageDashboard.click(S_COMPARE_BUTTON)
    pageDashboard.wait_for_selector(
        "#viewportA .manifest-line", timeout=10000,
    )
    pageDashboard.wait_for_selector(
        "#viewportB .manifest-line", timeout=10000,
    )
    dictRead = pageDashboard.evaluate(_S_READ_THE_VIEWERS)

    assert sorted(dictSeen["listManifestRequests"]) == [
        "MANIFEST.sha256", "REPRODUCED.sha256",
    ], dictSeen
    assert dictRead["dictA"]["sText"] == S_MANIFEST_TEXT
    assert dictRead["dictB"]["sText"] == S_REPRODUCED_TEXT
    assert "# vaibify manifest v1" in dictRead["dictA"]["sText"]
    assert "# vaibify reproduced manifest v1" in dictRead["dictB"]["sText"]

    for sViewer in ("dictA", "dictB"):
        assert dictRead[sViewer]["iToolbarButtons"] == 0, (
            f"a record file offers an edit control in {sViewer}: "
            f"{dictRead[sViewer]['listButtonText']!r}"
        )
        # One span per line, the trailing newline's empty tail included,
        # so the text reads back identical to the unmarked rendering.
        assert dictRead[sViewer]["iMarkedLines"] == len(
            S_MANIFEST_TEXT.split("\n")), dictRead[sViewer]

    dictDiverged = dictRead["dictDivergedB"]
    dictMatched = dictRead["dictMatchedB"]
    assert dictDiverged is not None and dictMatched is not None, dictRead
    assert S_DIVERGED_PATH in dictDiverged["sText"]
    assert S_MATCHED_PATH in dictMatched["sText"]
    assert "manifest-line-diverged" in dictDiverged["sClassName"]
    assert "manifest-line-matched" in dictMatched["sClassName"]
    # Computed, not the class: the rule must actually paint.
    assert (
        dictDiverged["sBackground"] != dictMatched["sBackground"]
        or dictDiverged["sColor"] != dictMatched["sColor"]
    ), (
        "a diverged line is painted exactly like a matched one: "
        f"{dictDiverged!r} vs {dictMatched!r}"
    )
    # Viewer A holds the SAME marks: the matched line there carries a
    # hash unequal to viewer B's, and is still marked matched, so the
    # colour demonstrably came from the backend's verdict and not from
    # comparing the two files in the browser.
    assert dictRead["dictDivergedA"] is not None
    assert dictRead["dictCarriedA"] is not None
    assert S_CARRIED_PATH in dictRead["dictCarriedA"]["sText"]


def test_a_v4_record_with_no_reproduced_manifest_offers_no_compare(
    pageDashboard, serverHub,
):
    _fnOpenTheProofTab(
        pageDashboard, serverHub,
        _fdictAttestationPayload(_fdictAttestation(None)),
    )
    pageDashboard.wait_for_timeout(300)
    assert pageDashboard.query_selector(S_COMPARE_BUTTON) is None, (
        "a record that wrote no reproduced manifest still offers to "
        "compare one"
    )
    # The per-file table is there regardless: it comes from the
    # outcomes list, which this record carries.
    sCard = pageDashboard.inner_text(".proof-attestation-card")
    assert S_DIVERGED_PATH in sCard and "diverged" in sCard
    assert "re-derived, byte-identical" in sCard
    assert "carried in unchanged" in sCard


def test_a_reproduction_of_your_own_gets_its_own_card_and_compare(
    pageDashboard, serverHub,
):
    dictAttestationWithoutManifest = _fdictAttestation(None)
    _fnOpenTheProofTab(
        pageDashboard, serverHub,
        _fdictAttestationPayload(
            dictAttestationWithoutManifest, _fdictReproduction(),
        ),
    )
    pageDashboard.wait_for_selector(
        ".proof-reproduction-card", state="visible", timeout=5000,
    )
    sCard = pageDashboard.inner_text(".proof-reproduction-card")
    assert "Your reproduction" in sCard
    assert "diverged" in sCard
    assert "1 / 2" in sCard and "1 carried in unchanged" in sCard
    assert "committed by another identity" in sCard
    assert S_DIVERGED_PATH in sCard and S_MATCHED_PATH in sCard
    # The attestation card wrote no manifest, so the ONLY Compare
    # button on the tab is the reproduction card's own.
    listButtons = pageDashboard.query_selector_all(S_COMPARE_BUTTON)
    assert len(listButtons) == 1, len(listButtons)
    assert pageDashboard.query_selector(
        ".proof-reproduction-card " + S_COMPARE_BUTTON,
    ) is not None
    pageDashboard.click(".proof-reproduction-card " + S_COMPARE_BUTTON)
    pageDashboard.wait_for_selector(
        "#viewportB .manifest-line-diverged", timeout=10000,
    )


def _flistInterceptVerifyPosts(pageDashboard):
    listPosts = []
    pageDashboard.route(
        "**/api/workflow/**/level3/verify",
        lambda route: (
            listPosts.append(route.request.url),
            route.fulfill(
                status=200, content_type="application/json",
                body='{"bStarted": true, "sPhase": "starting"}',
            ),
        ),
    )
    return listPosts


def _fsOpenTheConfirmWithRecordKind(pageDashboard, serverHub, sRecordKind):
    """Drive the REAL verify-l3 action and return the modal's text.

    The order the sibling test pins -- no POST before the confirm --
    is re-asserted here rather than assumed, because a modal whose
    wording is right and whose timing is wrong protects nobody.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistInterceptVerifyPosts(pageDashboard)
    _fnAnswerReadiness(pageDashboard, sRecordKind)
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(S_MODAL, state="visible", timeout=5000)
    assert listPosts == [], "the verify POST left before the confirm"
    return pageDashboard.inner_text(S_MODAL)


def test_the_confirm_names_a_reproduction_when_that_is_what_it_will_write(
    pageDashboard, serverHub,
):
    sText = _fsOpenTheConfirmWithRecordKind(
        pageDashboard, serverHub, "reproduction",
    )
    assert "reproduction" in sText.lower(), sText
    assert ".vaibify/reproductions" in sText, sText
    assert "will not be touched" in sText, sText
    # Still the copy warning: naming the record kind is an addition,
    # never a replacement for what the modal already says.
    assert "copy" in sText.lower(), sText


def test_the_confirm_names_the_attestation_otherwise(
    pageDashboard, serverHub,
):
    sText = _fsOpenTheConfirmWithRecordKind(
        pageDashboard, serverHub, "attestation",
    )
    assert ".vaibify/reproductions" not in sText, sText
    assert "attestation" in sText.lower(), sText
    assert "copy" in sText.lower(), sText
