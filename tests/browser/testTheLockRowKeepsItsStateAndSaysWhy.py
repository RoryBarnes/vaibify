"""A row nothing can be done about is not green.

The Dependency-lock row used to keep its state on a mismatch and warn
only in an amber note, on the reasoning that the row speaks for the
repository's envelope -- the lock is present and hashed, which stays
true -- while a lock the container does not satisfy is a fact about the
CONTAINER. It had a precedent: the image-currency warning has always
warned on the Environment snapshot row without moving it.

Seen on a live project, the combination read as nonsense. Every
applicable level showed a check, the "Do this next" arrow pointed at
that very row, and a note underneath explained that a rerun would
refuse before it started. The researcher reversed the ruling on sight
(2026-09-15): "if I ruled this was OK before it was because I didn't
see it."

So the row now carries the same conjunct the arrow does, and resolves
to PARTIAL rather than to red -- the artifact vocabulary already had
that state, and it is the honest one: the file IS present and IS
hashed, and what disagrees is the image. F stays green, R goes red,
the row's level cell and the Artifacts banner go amber.

What survives the reversal, and is asserted here because only a
rendered page can hold it together: the note still says *the container
you are working in* rather than the pinned image, because that is what
was measured; and NONE of it fires unless the running container is
PROVEN to be the image the envelope pins. The backend decides that from
one truth table shared with the arrow and the verification route, so
the three cannot disagree.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_LOCK_ROW = """(dictArgs) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            listBinaries: [],
            dictImageCurrency: dictArgs.dictImageCurrency,
            dictLockSatisfaction: dictArgs.dictLockSatisfaction,
            dictNextOrderedStep: dictArgs.dictNextOrderedStep,
            dictArtifacts: {
                manifest: {bPresent: true, bSatisfied: true},
                dependencyLock: {
                    bPresent: true,
                    bSatisfied: dictArgs.bLockSatisfied,
                },
                environmentSnapshot: {bPresent: true, bSatisfied: true},
                dockerfile: {bPresent: true, bSatisfied: true},
                reproduceScript: {bPresent: true, bSatisfied: true},
            },
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(['dependencyLock']),
    });
}"""

_DICT_MISMATCH = {
    "sState": "mismatch",
    "listMismatches": ["numpy==2.4.6 (image has 2.5.2)"],
    "sReason": "",
}

_DICT_ARROW = {
    "sRowKey": "dependencyLock",
    "sReason": "The container you are working in does not satisfy "
               "requirements.lock",
    "listBlockedRowKeys": ["rebuildAttestation"],
}


def _fsSelectRow(sHtml, sKey):
    """Return the markup of ONE requirement row, bounded at the next."""
    import re
    sTail = sHtml.split('data-req="' + sKey + '"')[1]
    return re.split(r'data-(?:req|group)="', sTail)[0]


@pytest.mark.falsification
def test_a_lock_the_container_fails_turns_its_row_amber_and_says_why(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    Kills: silencing ``_fsRenderLockMismatchNote``. The row is then
    amber with an arrow on it and nothing on the page saying which
    packages disagree or what to click -- a researcher told they are
    blocked and not told by what, which is the state this whole
    feature exists to end.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- mismatch, and the running container IS the pin. The row is
    # green, the note is there, and the arrow names the row.
    # bSatisfied is what the BACKEND computes -- false here, because
    # the policy blocks. That the backend computes it that way is
    # pinned in Python by test_no_row_diverges_from_the_arrow_at_all;
    # what this file pins is what the page does with it.
    sHtml = pageDashboard.evaluate(_S_RENDER_LOCK_ROW, {
        "dictImageCurrency": {"bPinnedImageIsLive": True},
        "dictLockSatisfaction": _DICT_MISMATCH,
        "dictNextOrderedStep": _DICT_ARROW,
        "bLockSatisfied": False,
    })
    sRow = _fsSelectRow(sHtml, "dependencyLock")
    # PARTIAL, not met and not failed. The envelope is coherent --
    # the file is there and it is hashed -- and the image is what
    # disagrees, so red would name the wrong thing as broken.
    assert "Level 3: partially met" in sRow, sRow[:1500]
    assert "Level 3: met" not in sRow, (
        "a row the arrow is pointing at, that a rerun would refuse "
        "on, still shows a check"
    )
    assert "lock-mismatch-warning" in sRow
    assert "numpy==2.4.6 (image has 2.5.2)" in sRow
    assert "container you are working in" in sRow, (
        "the note states a fact about the PINNED IMAGE from a "
        "measurement of the running container"
    )
    assert "The pinned image does not satisfy this lock" not in sRow
    # The cheap remedy first, the expensive one with its cost. One
    # cause must not produce two sets of instructions, so this is the
    # same order the shadow's refusal uses.
    assert sRow.index("Regenerate now") < sRow.index("rebuilding"), sRow
    assert "downgrading the image" in sRow
    assert 'data-ordering-row="dependencyLock"' in sHtml, (
        "the arrow did not name the row a rerun would refuse on"
    )

    # --- the same mismatch, measured against an image nobody has
    # shown to be the pin. The note stays -- it is still true about
    # the container -- and the ARROW is gone, because the verdict is
    # no evidence about the image a rerun would grade.
    sUnproven = pageDashboard.evaluate(_S_RENDER_LOCK_ROW, {
        "dictImageCurrency": {"bPinnedImageIsLive": None},
        "dictLockSatisfaction": _DICT_MISMATCH,
        "dictNextOrderedStep": None,
        "bLockSatisfied": True,
    })
    sUnprovenRow = _fsSelectRow(sUnproven, "dependencyLock")
    assert "lock-mismatch-warning" in sUnprovenRow
    assert "Level 3: met" in sUnprovenRow, (
        "a rerun of the PINNED image was marked down over a "
        "measurement of a container nobody has shown to be it"
    )
    assert "ordering-arrow" not in sUnproven, (
        "an arrow pointed at a row on the strength of a measurement "
        "of a container the envelope does not pin"
    )

    # --- UNKNOWN paints nothing at all. The poll may not exec, so on
    # most ticks nobody has asked; a note built from an unasked
    # question would appear on every project between restarts.
    for objVerdict in (None, {"sState": "unknown", "listMismatches": []}):
        sQuiet = _fsSelectRow(
            pageDashboard.evaluate(_S_RENDER_LOCK_ROW, {
                "dictImageCurrency": {"bPinnedImageIsLive": True},
                "dictLockSatisfaction": objVerdict,
                "dictNextOrderedStep": None,
                "bLockSatisfied": True,
            }),
            "dependencyLock",
        )
        assert "lock-mismatch-warning" not in sQuiet, objVerdict
        assert "Level 3: met" in sQuiet, objVerdict

    assert pageDashboard.listPageErrors == []
