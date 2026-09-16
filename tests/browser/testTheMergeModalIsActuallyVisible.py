"""A modal that is built but never displayed reads as a dead button.

Shipped to the researcher on 2026-09-15: the diverged banner rendered,
"Merge origin/main…" was clicked, and nothing happened. The modal WAS
being constructed and appended — with ``.modal`` on the outer element
instead of the house's ``.modal-overlay``, and without ``.active``, so
it landed at the end of <body> as an unstyled block below the fold.
Every unit assertion one could write about the markup would have
passed.

Nothing in the Python suite executes this file, and no test covered the
drift banner at all — the same hole that let the banner's prose drift
out of step with TUPLE_ROOT_CONFIG_FILES. These assertions are about
what the browser actually lays out: computed display, and a dialog
inside the viewport.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_DIVERGED = {
    "sRefusal": "diverged-branches",
    "sBranch": "main",
    "iAhead": 1,
    "iBehind": 1,
    "dictMergePreview": {"sState": "clean", "listConflictPaths": []},
}


@pytest.mark.falsification
def test_the_merge_modal_is_visible_when_the_button_is_clicked(
    pageDashboard, serverHub,
):
    """ONE open, every assertion: the seeded project is leased.

    Kills: building the dialog with ``.modal`` on the outer element,
    or omitting ``.active`` — either leaves a node in the DOM whose
    computed display is not ``flex`` and whose box sits outside the
    viewport, which is precisely what "nothing happens" looked like.
    It also kills enabling the confirm button regardless of the
    preview, and rendering the unknown state in the clean state's
    words.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    dictSeen = pageDashboard.evaluate(
        """(dictRefusal) => {
            document.querySelectorAll('.merge-modal-overlay')
                .forEach((el) => el.remove());
            const elBanner = document.getElementById('driftBanner');
            elBanner.innerHTML = '';
            elBanner.hidden = true;
            VaibifyWorkflowManager._fnRenderDriftBannerForTest(
                dictRefusal);
            const elButton = elBanner.querySelector(
                '.drift-banner-merge');
            if (!elButton) return {sError: 'no merge button rendered'};
            elButton.click();
            const elOverlay = document.querySelector(
                '.merge-modal-overlay');
            if (!elOverlay) return {sError: 'no overlay was created'};
            const elDialog = elOverlay.querySelector('.modal');
            const dictBox = elDialog
                ? elDialog.getBoundingClientRect() : null;
            return {
                sOverlayDisplay:
                    getComputedStyle(elOverlay).display,
                bDialogPresent: Boolean(elDialog),
                iDialogWidth: dictBox ? Math.round(dictBox.width) : 0,
                iDialogHeight: dictBox ? Math.round(dictBox.height) : 0,
                bInsideViewport: Boolean(dictBox) &&
                    dictBox.top >= 0 &&
                    dictBox.bottom <= window.innerHeight + 1,
                sText: elOverlay.textContent,
                bConfirmEnabled: !elOverlay.querySelector(
                    '.merge-modal-confirm').disabled,
            };
        }""",
        _S_DIVERGED,
    )
    assert "sError" not in dictSeen, dictSeen.get("sError")
    # The overlay must be LAID OUT, not merely present: display:none
    # is exactly the state that made the button look dead.
    assert dictSeen["sOverlayDisplay"] == "flex", dictSeen
    assert dictSeen["bDialogPresent"], "the inner .modal box is missing"
    assert dictSeen["iDialogWidth"] > 100
    assert dictSeen["iDialogHeight"] > 100
    assert dictSeen["bInsideViewport"], (
        "the dialog is off-screen, which is what the researcher saw"
    )
    # A clean preview must SAY it was checked, and offer the merge.
    assert "Checked: this merges cleanly" in dictSeen["sText"]
    assert dictSeen["bConfirmEnabled"]
    # And the rebase absence is stated rather than silent.
    assert "rebase" in dictSeen["sText"].lower()

    # ---- the other two preview states, same page, same lease ----
    # Unchecked and conflicting are not the same as clean: the first
    # would offer a merge vaibify has already said it will refuse, the
    # second would promise a cleanliness nothing established.
    dictConflict = dict(_S_DIVERGED)
    dictConflict["dictMergePreview"] = {
        "sState": "conflicts",
        "listConflictPaths": ["MANIFEST.sha256"],
    }
    dictUnknown = dict(_S_DIVERGED)
    dictUnknown["dictMergePreview"] = {
        "sState": "unknown", "listConflictPaths": [],
    }
    dictOther = pageDashboard.evaluate(
        """(dictArgs) => {
            const fnOpen = (dictRefusal) => {
                document.querySelectorAll('.merge-modal-overlay')
                    .forEach((el) => el.remove());
                const elBanner = document.getElementById('driftBanner');
                elBanner.innerHTML = '';
                VaibifyWorkflowManager._fnRenderDriftBannerForTest(
                    dictRefusal);
                elBanner.querySelector('.drift-banner-merge').click();
                const elOverlay = document.querySelector(
                    '.merge-modal-overlay');
                const dictOut = {
                    sText: elOverlay.textContent,
                    bConfirmEnabled: !elOverlay.querySelector(
                        '.merge-modal-confirm').disabled,
                };
                elOverlay.remove();
                return dictOut;
            };
            return {
                dictConflict: fnOpen(dictArgs.dictConflict),
                dictUnknown: fnOpen(dictArgs.dictUnknown),
            };
        }""",
        {"dictConflict": dictConflict, "dictUnknown": dictUnknown},
    )
    assert dictOther["dictConflict"]["bConfirmEnabled"] is False
    assert "MANIFEST.sha256" in dictOther["dictConflict"]["sText"]
    # Unknown says it could not check, and says so in its own words.
    # Matched on the CLEAN state's claim rather than the bare phrase:
    # "could not check whether this merges cleanly" contains it, and a
    # test that forbade the substring would be forbidding the honest
    # sentence instead of the false one.
    assert "could not check" in dictOther["dictUnknown"]["sText"]
    assert "Checked: this merges cleanly" not in (
        dictOther["dictUnknown"]["sText"]
    )
