"""Whether the image satisfies the lock, asked before a rerun is spent.

A researcher reached Level 3's last step, clicked Verify, and was told
the pinned image did not satisfy requirements.lock -- a lock five days
older than the image, fixable in seconds. Three surfaces had been
silent (2026-09-15):

* the Dependency-lock row was GREEN, because ``fbVerifyDependencyLock``
  asks whether every entry carries a hash and nothing about whether
  the entries are true;
* the readiness pre-flight asked a DIFFERENT question entirely --
  vaibify.yml's declared packages against the repository mirror -- so
  it could not see this one;
* the shadow's refusal was the only place the question was asked, and
  reaching it costs a container launch.

The refusal also named the expensive remedy first: rebuilding the
image to satisfy a stale lock DOWNGRADES it.
"""

import pytest

from vaibify.reproducibility import lockSatisfaction


def test_the_lock_is_the_claim_and_the_image_is_the_evidence():
    listLines = lockSatisfaction.flistDescribeLockMismatch(
        {"numpy": "2.4.6", "scipy": "1.17.1"},
        {"numpy": "2.5.2", "scipy": "1.17.1"},
    )
    assert listLines == ["numpy==2.4.6 (image has 2.5.2)"]


def test_a_package_the_image_lacks_says_so_in_words():
    """An empty version read as a formatting fault the first time."""
    listLines = lockSatisfaction.flistDescribeLockMismatch(
        {"argparse": "1.4.0"}, {},
    )
    assert listLines == ["argparse==1.4.0 (image has nothing)"]


def test_extra_packages_in_the_image_satisfy_the_lock():
    """The lock is a floor for the rerun, not an inventory."""
    assert lockSatisfaction.flistDescribeLockMismatch(
        {"numpy": "2.5.2"}, {"numpy": "2.5.2", "rich": "13.0"},
    ) == []


@pytest.mark.falsification
def test_an_unreadable_image_is_unknown_not_an_empty_one():
    """"Could not look" must not arrive as "the image has nothing".

    The two produce opposite screens: unknown renders as it always
    has, while an empty image mismatches every line of the lock. A
    failed exec reported as the second is the loudest possible wrong
    answer, and it would redden the row of every project whose
    container was briefly unreachable.

    Kills: treating a missing installed list as an empty mapping.
    """
    dictUnknown = lockSatisfaction.fdictDescribeLockSatisfaction(
        {"numpy": "2.5.2"}, None, "pip list exited 127",
    )
    assert dictUnknown["sState"] == lockSatisfaction.S_LOCK_UNKNOWN
    assert dictUnknown["listMismatches"] == []
    assert "127" in dictUnknown["sReason"]

    dictEmpty = lockSatisfaction.fdictDescribeLockSatisfaction(
        {"numpy": "2.5.2"}, {},
    )
    assert dictEmpty["sState"] == lockSatisfaction.S_LOCK_MISMATCH


@pytest.mark.falsification
def test_the_shadow_and_the_preflight_share_one_diff():
    """Two derivations of one question are two authorities on it.

    The shadow had its own copy of this comparison, and the only other
    surface that looked asked something else — which is exactly how a
    lock that satisfied nothing reached a researcher's verification.

    Kills: reinstating a private diff inside shadowRerun instead of
    calling the shared one.
    """
    import inspect
    from vaibify.reproducibility import shadowRerun
    sSource = inspect.getsource(shadowRerun._fdictRefusalFromLockDiff)
    assert "flistDescribeLockMismatch" in sSource
    assert "image has " not in sSource, (
        "the diff is being formatted here again rather than shared"
    )


def test_the_refusal_names_the_cheap_remedy_first():
    """Rebuilding the image to satisfy a stale lock DOWNGRADES it.

    Asserted on the MESSAGE the researcher reads, not on the source
    that composes it: the sentence is split across source lines, so a
    source-text assertion would be checking the formatting rather than
    the advice.
    """
    from vaibify.reproducibility import shadowRerun
    dictOutcome = shadowRerun._fdictRefusalFromLockDiff(
        {"numpy": "2.4.6"}, {"numpy": "2.5.2"},
    )
    sMessage = " ".join(dictOutcome["listDivergedHashes"])
    iRegenerate = sMessage.index("regenerate the envelope")
    iRebuild = sMessage.index("Rebuild the image instead")
    assert iRegenerate < iRebuild, (
        "the expensive remedy is named before the cheap one"
    )
    assert "downgrades" in sMessage.lower()


def test_the_readiness_route_asks_the_shadows_question():
    """The pre-flight must ask what the rerun will ask, not something else."""
    import inspect
    from vaibify.gui.routes import reproducibilityRoutes
    sSource = inspect.getsource(
        reproducibilityRoutes.fdictCheckLockSatisfiedByContainer,
    )
    assert "requirements.lock" in sSource
    assert "S_SHADOW_PIP_ENUMERATE_COMMAND" in sSource, (
        "the pre-flight must enumerate packages the same way the "
        "shadow does, or the two can disagree again"
    )
