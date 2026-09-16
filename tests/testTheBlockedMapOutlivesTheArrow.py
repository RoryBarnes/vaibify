"""The blocked map keeps answering after the arrow goes silent.

The arrow names ONE next step and deliberately says nothing when two
independent chains remain -- no single root, so no single answer. But
the circle-slash on a premature row's buttons must not vanish with
it: a row downstream of an unsatisfied prerequisite is premature
regardless of how many chains remain. Deriving the blocking from the
arrow payload -- which is what the frontend did first -- makes every
multi-chain endgame render every button live.

The second test is the live shape this was found on (AI Greenhouse,
2026-09-16): a project at Level 2 with exactly ``reproduceScript``
and ``manifest`` open. A readiness condition silenced the arrow there
for part of a day, precisely because readiness is false when the
ordered rows are unmet; ``fdictDescribeOrderedEndgame`` must answer
that shape with the cheap order.
"""

import pytest

from vaibify.reproducibility import levelOrdering


_T_ROWS = (
    "dependencyLock", "environmentArchive", "manifest",
    "reproduceScript", "rebuildAttestation", "envelopeMirror",
    "envelopeArchive",
)


def _fdictEndgameWith(monkeypatch, *saUnsatisfied):
    """Drive the ordered endgame with a hand-set verdict per row."""
    monkeypatch.setattr(
        levelOrdering.levelGates, "flistLevel2Blockers",
        lambda dictWorkflow, filesRepo, **kwargs: [],
    )
    dictSatisfied = {
        sRow: sRow not in saUnsatisfied for sRow in _T_ROWS
    }
    monkeypatch.setattr(
        levelOrdering, "fdictJudgeOrderedRequirements",
        lambda dictWorkflow, filesRepo, dictLock=None,
        dictCurrency=None: dictSatisfied,
    )
    return levelOrdering.fdictDescribeOrderedEndgame({}, "/nowhere")


@pytest.mark.falsification
def test_two_chains_silence_the_arrow_but_not_the_blocked_map(
    monkeypatch,
):
    """No single root, yet both downstream rows are premature.

    Two independent chains: the lock before the attestation, the
    script before the manifest (the manifest is unmet too, so it
    chains onward to the attestation). The arrow correctly refuses
    to pick a chain; the blocked map correctly refuses to free the
    downstream rows.

    Kills: deriving the blocked map from the arrow payload's
    ``listBlockedRowKeys`` -- the map goes empty whenever the arrow
    goes silent, which is exactly when two prerequisites are open at
    once and acting early is easiest.
    """
    dictEndgame = _fdictEndgameWith(
        monkeypatch, "dependencyLock", "rebuildAttestation",
        "reproduceScript", "manifest",
    )
    assert dictEndgame["dictNextStep"] is None, (
        "two independent chains produced an arrow, so this fixture "
        "no longer exercises the silence the map must outlive"
    )
    dictBlocked = dictEndgame["dictBlockedRows"]
    assert set(dictBlocked) == {"manifest", "rebuildAttestation"}, (
        dictBlocked
    )
    assert "reproduce.sh" in dictBlocked["manifest"], (
        "the reason shown is not the edge's own text: %r"
        % dictBlocked["manifest"]
    )


def test_the_level_two_project_with_two_rows_left_gets_the_cheap_order(
    monkeypatch,
):
    """The live shape: script and manifest open, everything else met.

    One live edge, one root; the arrow names the script, the map
    marks the manifest, and the reason is the doubled-work claim the
    researcher acts on.
    """
    dictEndgame = _fdictEndgameWith(
        monkeypatch, "reproduceScript", "manifest",
    )
    dictNextStep = dictEndgame["dictNextStep"]
    assert dictNextStep and dictNextStep["sRowKey"] == "reproduceScript"
    assert dictNextStep["listBlockedRowKeys"] == ["manifest"]
    assert dictEndgame["dictBlockedRows"] == {
        "manifest": dictNextStep["sReason"],
    }, dictEndgame["dictBlockedRows"]
