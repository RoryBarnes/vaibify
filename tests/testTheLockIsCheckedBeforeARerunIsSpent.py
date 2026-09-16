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
        {"numpy": "2.5.2"}, None, "the package inventory exited 127",
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


def test_both_callers_enumerate_packages_by_the_same_constant():
    """The one property source text is the right tool for.

    Whether the two lanes SPELL the same constant is a claim about the
    text, and no request can observe it: a pre-flight that assembled
    its own inventory variant would answer plausibly and disagree
    with the shadow on any image where the two spellings differ --
    which is the drift that let a lock satisfying nothing reach a
    researcher's verification in the first place.

    Everything else about that route is asserted by driving it, in
    ``tests/testTheReadinessRouteAnswersARequest.py``. A source-text
    guard on the route's BEHAVIOUR is what let the ``ImportError``
    ship: ``inspect.getsource`` cannot observe whether a function runs.
    """
    import inspect
    from vaibify.gui.routes import reproducibilityRoutes
    from vaibify.reproducibility import shadowRerun
    for fnCaller in (
        reproducibilityRoutes.fdictCheckLockSatisfiedByContainer,
        shadowRerun._fdictRefusalIfImageLacksLockedPackages,
    ):
        assert "S_ENUMERATE_PACKAGES_COMMAND" in inspect.getsource(
            fnCaller,
        ), (
            f"{fnCaller.__name__} enumerates packages its own way; the "
            "two lanes must ask the image one question, or they can "
            "disagree again"
        )


# ----------------------------------------------------------------------
# The pinned image is what a rerun grades, and the measurement is not
# ----------------------------------------------------------------------

def _fdictJudgeWith(dictVerdict, dictImageCurrency, sProjectRepo):
    """Return the arrow's verdicts for one (measurement, currency) pair."""
    from vaibify.reproducibility import levelOrdering
    return levelOrdering.fdictJudgeOrderedRequirements(
        {"listSteps": [], "sWorkflowName": "p"}, sProjectRepo,
        dictVerdict, dictImageCurrency,
    )


_T_LOCK_CASES = (
    # (label, verdict, image currency, blocks?)
    ("clean", {"sState": "clean"}, {"bPinnedImageIsLive": True}, False),
    (
        "mismatch against the proven pin",
        {"sState": "mismatch"}, {"bPinnedImageIsLive": True}, True,
    ),
    (
        "mismatch against a different image",
        {"sState": "mismatch"}, {"bPinnedImageIsLive": False}, False,
    ),
    (
        "mismatch against an undetermined image",
        {"sState": "mismatch"}, {"bPinnedImageIsLive": None}, False,
    ),
    ("unknown", {"sState": "unknown"}, {"bPinnedImageIsLive": True}, False),
    ("never asked", None, None, False),
)


@pytest.mark.parametrize(
    "sLabel,dictVerdict,dictCurrency,bBlocks", _T_LOCK_CASES,
)
@pytest.mark.falsification
def test_only_a_mismatch_against_the_proven_pin_blocks_a_rerun(
    sLabel, dictVerdict, dictCurrency, bBlocks,
):
    """The shadow runs the PIN; the measurement is of the running container.

    A verdict about one is evidence about the other only when the two
    are known to be the same image. Three of these five mismatch-ish
    cases must NOT block: blocking on any running-container mismatch
    falsely refuses a shadow whose pinned image is fine, and treating
    a clean running container as clearance falsely clears one the
    shadow will reject.

    Kills: gating on ``sState == "mismatch"`` alone, and reading
    ``bPinnedImageIsLive`` as truthy-or-absent rather than exactly
    ``True``.
    """
    from vaibify.reproducibility import lockSatisfaction
    assert lockSatisfaction.fbLockBlocksVerification(
        dictVerdict, dictCurrency,
    ) is bBlocks, sLabel


@pytest.mark.parametrize(
    "sLabel,dictVerdict,dictCurrency,bBlocks", _T_LOCK_CASES,
)
def test_the_arrow_reads_the_same_truth_table(
    sLabel, dictVerdict, dictCurrency, bBlocks, tmp_path,
):
    """BOTH surfaces, one table. Two derivations is what was fixed.

    The pre-flight and the "Do this next" arrow answer one question,
    and the arrow used to answer it from the measurement alone -- so a
    project whose running image was not the pin got an arrow pointing
    at a row no rerun would refuse on.
    """
    import os
    import subprocess
    sRepo = str(tmp_path)
    subprocess.run(["git", "init", "-q", sRepo], check=True)
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    with open(os.path.join(sRepo, "requirements.lock"), "w") as fileLock:
        fileLock.write("numpy==2.5.2 \\\n    --hash=sha256:00\n")
    dictJudged = _fdictJudgeWith(dictVerdict, dictCurrency, sRepo)
    assert dictJudged["dependencyLock"] is not bBlocks, sLabel


def test_the_edge_reason_names_the_cheap_remedy_first():
    """One cause must not produce two sets of instructions.

    The arrow's reason and the shadow's refusal describe the same
    mismatch, so both name regenerating the envelope before rebuilding
    the image, and both say what rebuilding costs. Asserted on the
    ORDER rather than the sentence: the wording will change and the
    advice must not.
    """
    from vaibify.reproducibility import levelOrdering
    listReasons = [
        sReason
        for sEarlier, sLater, sReason in (
            levelOrdering.T_LEVEL3_ORDERING_EDGES
        )
        if sEarlier == "dependencyLock" and sLater == "rebuildAttestation"
    ]
    assert len(listReasons) == 1
    sReason = listReasons[0]
    assert sReason.index("Regenerate") < sReason.index("Rebuilding"), (
        "the expensive remedy is named before the cheap one"
    )
    assert "downgrading" in sReason
    assert "pinned image does not satisfy" not in sReason, (
        "the reason states a fact about the PIN from a measurement of "
        "the running container"
    )
    assert "container you are working in" in sReason


# ----------------------------------------------------------------------
# The inventory must not inherit pip's blind spot
# ----------------------------------------------------------------------

def _fsWriteShadowingDistribution(pathSite, sName, sVersion):
    """Install a distribution whose name shadows a stdlib module.

    Nothing but a ``.dist-info`` directory with a METADATA file is
    needed for a distribution to be installed as far as the packaging
    metadata standard -- and as far as pip -- is concerned, which is
    what lets this drive the real command instead of a stub.
    """
    pathDistInfo = pathSite / f"{sName}-{sVersion}.dist-info"
    pathDistInfo.mkdir(parents=True)
    (pathDistInfo / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        f"Name: {sName}\n"
        f"Version: {sVersion}\n",
        encoding="utf-8",
    )
    (pathDistInfo / "RECORD").write_text("", encoding="utf-8")
    return str(pathSite)


def _fsRunInventoryCommand(sCommand, sSitePath):
    """Run one of the two spellings through a real shell and interpreter."""
    import os
    import subprocess
    dictEnvironment = dict(os.environ)
    dictEnvironment["PYTHONPATH"] = sSitePath
    return subprocess.run(
        ["bash", "-c", sCommand], capture_output=True, text=True,
        env=dictEnvironment, timeout=120,
    ).stdout


@pytest.mark.falsification
def test_the_inventory_reports_what_pip_refuses_to_show(tmp_path):
    """A stdlib-shadowing distribution is installed, and must be seen.

    ``pip list`` and ``pip freeze`` hardcode a refusal to report
    ``argparse``, ``python`` and ``wsgiref``
    (``pip._internal.utils.compat.stdlib_pkgs``). Any of the three,
    genuinely installed and genuinely locked, read back as "the image
    has nothing" -- a mismatch that is FALSE and that no button can
    clear, because Regenerate keeps re-pinning the version the image
    already has. Met live, where it blocked Level 3 permanently
    (2026-09-15).

    Driven through a real ``bash`` and a real interpreter over a
    synthesized distribution, because the claim is about what the
    command SEES. A stub returning canned lines would agree with any
    spelling, and the spelling is the entire defect.

    Kills: restoring the ``pip list --format=freeze`` spelling.
    """
    from vaibify.reproducibility.declaredPackages import (
        fdictParsePinnedVersions,
    )
    from vaibify.reproducibility.shadowRerun import (
        S_ENUMERATE_PACKAGES_COMMAND,
    )
    sSitePath = _fsWriteShadowingDistribution(tmp_path, "wsgiref", "0.1.2")
    dictSeenByPip = fdictParsePinnedVersions(_fsRunInventoryCommand(
        "python3 -m pip list --format=freeze --disable-pip-version-check",
        sSitePath,
    ))
    if dictSeenByPip.get("wsgiref"):
        pytest.skip(
            "this pip no longer hides stdlib-shadowing distributions, "
            "so the two spellings cannot be told apart here and this "
            "test can kill nothing"
        )
    dictSeen = fdictParsePinnedVersions(_fsRunInventoryCommand(
        S_ENUMERATE_PACKAGES_COMMAND, sSitePath,
    ))
    assert dictSeen.get("wsgiref") == "0.1.2", (
        "the image inventory inherited pip's blind spot, so a locked "
        "package the image HAS reads as 'the image has nothing' and "
        "no button can clear the mismatch"
    )
    assert len(dictSeen) > 1, (
        "the inventory reported only the synthesized distribution; it "
        "must enumerate the whole environment, or every other locked "
        "package reads as missing"
    )
