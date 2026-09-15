"""The Dependency-lock row stays green, warns in amber, and gets the arrow.

Three claims that only a rendered page can hold together, and each one
would look like a bug on its own.

The ROW keeps its state on a mismatch (ruled 2026-09-15). Its criterion
is about the repository's envelope -- the lock is present and hashed,
which stays true -- while a lock the container does not satisfy is a
fact about the CONTAINER. The precedent is the image-currency warning,
which has always warned on the Environment snapshot row without moving
it.

The NOTE says so in amber, and says it about *the container you are
working in*, never about the pinned image: the measurement is taken
against the running container, because asking the pin means launching
it, which is the cost the check exists to avoid.

The ARROW still names the row, because the rerun refuses before it
starts. That divergence between a green row and an arrow pointing at it
is the ONE permitted one, pinned on the Python side by
``test_the_dependency_lock_is_the_only_row_allowed_to_diverge``; here it
is pinned as a thing a researcher actually sees.

And the arrow appears only when the running container is PROVEN to be
the pinned image. The backend decides that (one truth table, shared with
the verification pre-flight) and ships the answer; what this file holds
is that the page renders the three states apart rather than deriving a
fourth of its own.
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
                dependencyLock: {bPresent: true, bSatisfied: true},
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
def test_a_lock_the_container_fails_keeps_its_row_and_gains_a_note(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    Kills: silencing ``_fsRenderLockMismatchNote``. The row is then
    green, the arrow still points at it, and nothing on the page says
    why -- which is the state the researcher was in before any of
    this existed, reached from the opposite direction.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- mismatch, and the running container IS the pin. The row is
    # green, the note is there, and the arrow names the row.
    sHtml = pageDashboard.evaluate(_S_RENDER_LOCK_ROW, {
        "dictImageCurrency": {"bPinnedImageIsLive": True},
        "dictLockSatisfaction": _DICT_MISMATCH,
        "dictNextOrderedStep": _DICT_ARROW,
    })
    sRow = _fsSelectRow(sHtml, "dependencyLock")
    assert "Level 3: met" in sRow, sRow[:1500]
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
    })
    assert "lock-mismatch-warning" in _fsSelectRow(
        sUnproven, "dependencyLock",
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
            }),
            "dependencyLock",
        )
        assert "lock-mismatch-warning" not in sQuiet, objVerdict
        assert "Level 3: met" in sQuiet, objVerdict

    assert pageDashboard.listPageErrors == []
