"""The ladder is mostly order-free, and the exceptions were never said.

Reported by the researcher on 2026-09-14, after a session spent
working out by hand that regenerating the manifest before the reproduce
script pins the script that is about to be replaced, and that archiving
to Zenodo before attesting costs a whole immutable version to correct.
Vaibify knew both facts -- they fall out of ``flistManifestPathsToPin``
and ``fbAttestationIsPubliclyArchived`` -- and stated them only as
prose inside remediation hints.

The feature answers ONE question ("which blocked requirement has to be
fixed first?") rather than rendering a plan, because a wrong plan reads
authoritatively and a wrong single answer is found out in seconds. The
tests below therefore spend most of their effort on the two ways the
answer must be SILENT: no live edge, and more than one root.
"""

import os
import subprocess

import pytest

from vaibify.reproducibility import levelOrdering


_T_ROWS = (
    # dependencyLock joined on 2026-09-15: a lock the pinned image
    # does not satisfy makes the rerun refuse, and until it was a row
    # here the arrow sent researchers at the attestation instead.
    # environmentArchive joined the same day: its deposit re-pins
    # MANIFEST.sha256, so it orders everything the manifest does.
    "dependencyLock", "environmentArchive", "manifest",
    "reproduceScript", "rebuildAttestation", "envelopeMirror",
    "envelopeArchive",
)


@pytest.fixture
def sProjectRepo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    os.makedirs(os.path.join(str(tmp_path), ".vaibify"), exist_ok=True)
    return str(tmp_path)


def _fdictNextFrom(dictSatisfied, monkeypatch):
    """Drive the ordering with a hand-set verdict for each row.

    The judgement and the ordering are separate concerns and are
    tested separately: this exercises the ORDER, and
    ``test_the_arrow_judges_rows_exactly_as_the_rows_do`` exercises
    the judgement against the payload the dashboard renders.
    """
    monkeypatch.setattr(
        levelOrdering, "fdictJudgeOrderedRequirements",
        lambda dictWorkflow, filesRepo, dictLock=None,
        dictCurrency=None: dictSatisfied,
    )
    return levelOrdering.fdictDescribeNextOrderedStep({}, "/nowhere")


def _fdictAllSatisfiedExcept(*saUnsatisfied):
    return {
        sRow: sRow not in saUnsatisfied for sRow in _T_ROWS
    }


def test_the_script_comes_before_the_manifest_that_pins_it(monkeypatch):
    """The live case that prompted this, asserted rather than narrated."""
    dictNext = _fdictNextFrom(
        _fdictAllSatisfiedExcept(
            "reproduceScript", "manifest", "envelopeMirror",
            "envelopeArchive",
        ),
        monkeypatch,
    )
    assert dictNext["sRowKey"] == "reproduceScript"
    # The reason must not claim an UNDO here. Generating the script
    # re-pins the manifest in the same action, so a manifest done
    # first is repeated rather than lost, and the first wording said
    # otherwise (corrected 2026-09-15 after a live run).
    assert "re-pins MANIFEST.sha256" in dictNext["sReason"]
    assert "doing it twice" in dictNext["sReason"]


def test_the_manifest_comes_before_the_attestation_keyed_to_it(
    monkeypatch,
):
    dictNext = _fdictNextFrom(
        _fdictAllSatisfiedExcept(
            "manifest", "rebuildAttestation", "envelopeArchive",
        ),
        monkeypatch,
    )
    assert dictNext["sRowKey"] == "manifest"


def test_the_attestation_comes_before_the_immutable_archive(monkeypatch):
    """Publishing first costs a new Zenodo version, not a re-push."""
    dictNext = _fdictNextFrom(
        _fdictAllSatisfiedExcept("rebuildAttestation", "envelopeArchive"),
        monkeypatch,
    )
    assert dictNext["sRowKey"] == "rebuildAttestation"
    assert "immutable" in dictNext["sReason"]


@pytest.mark.falsification
def test_independent_blockers_are_named_in_no_order_at_all(monkeypatch):
    """Silence is the answer, and it is INFORMATION.

    Once the attestation is current, the GitHub mirror and the Zenodo
    archive are genuinely independent: neither undoes the other, so
    there is no first one to do. An arrow here would invent a sequence
    the code does not support, and it would carry more authority than
    the rows it points at.

    Kills: answering with the first unsatisfied row, or with any root
    of the full edge set rather than of the LIVE edges -- both of
    which would point at one of these two.
    """
    assert _fdictNextFrom(
        _fdictAllSatisfiedExcept("envelopeMirror", "envelopeArchive"),
        monkeypatch,
    ) is None


@pytest.mark.falsification
def test_two_independent_chains_are_answered_with_silence(monkeypatch):
    """More than one root means there is no single next step.

    The shipped edge set is a tree rooted at the reproduce script, so
    it cannot currently produce two roots -- which is exactly why this
    guard is tested against a SYNTHETIC pair of chains. The day an
    edge is added that forks the graph, the guard has to already be
    there; discovering it then means discovering it from an arrow
    pointing at one of two equally-ready rows.

    Kills: returning ``listRoots[0]`` instead of requiring exactly
    one. That mutation names a real root, which is what makes it
    dangerous: the answer looks right while quietly asserting that the
    other chain is not ready to start.
    """
    monkeypatch.setattr(
        levelOrdering, "T_LEVEL3_ORDERING_EDGES",
        (
            ("manifest", "envelopeMirror", "one chain"),
            ("rebuildAttestation", "envelopeArchive", "another chain"),
        ),
    )
    assert _fdictNextFrom(
        {
            "manifest": False, "envelopeMirror": False,
            "rebuildAttestation": False, "envelopeArchive": False,
            "reproduceScript": True, "dependencyLock": True,
            "environmentArchive": True,
        },
        monkeypatch,
    ) is None


def test_a_fully_satisfied_project_is_answered_with_silence(monkeypatch):
    assert _fdictNextFrom(_fdictAllSatisfiedExcept(), monkeypatch) is None


def test_every_edge_names_rows_the_judgement_covers():
    """An edge naming a row nobody judges would silently never fire."""
    setJudged = set(_T_ROWS)
    for sEarlier, sLater, sReason in (
        levelOrdering.T_LEVEL3_ORDERING_EDGES
    ):
        assert sEarlier in setJudged, sEarlier
        assert sLater in setJudged, sLater
        assert sReason.strip(), (sEarlier, sLater)


@pytest.mark.falsification
def test_the_arrow_judges_rows_exactly_as_the_rows_do(sProjectRepo):
    """The arrow must never point at a row the researcher sees as green.

    The two verdicts are computed in different modules -- this one
    cannot import the route module that assembles the poll payload --
    so nothing but this test stops them drifting. The rows it can
    compare are the envelope artifacts, which is where the drift
    actually happened once: ``reproduce-script-stale`` was registered
    in five places and not in the row's own map, and the row stayed
    green while the level blocked.

    Every shared row is compared, not a named pair, so a row added to
    either side joins this check by existing. ``dependencyLock`` is
    excluded because a ruling made it the one permitted divergence;
    that carve-out has its own test asserting it is the only one, and
    excluding it here without that test would be indistinguishable
    from weakening this one.

    Kills: dropping either conjunct from the reproduce-script entry in
    ``fdictJudgeOrderedRequirements`` -- the arrow would then judge a
    stale script satisfied and never name it.
    """
    from vaibify.gui.routes.pipelineRoutes import (
        _fdictEnvelopeArtifactSatisfaction,
    )
    from vaibify.reproducibility.reproduceScriptGenerator import (
        S_REPRODUCE_SCRIPT_FILENAME,
    )
    dictWorkflow = {
        "sWorkflowName": "project", "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Make Data", "sDirectory": "MakeData",
            "saDataCommands": ["python makeData.py"],
            "saOutputDataFiles": ["out.json"],
        }],
    }
    os.makedirs(os.path.join(sProjectRepo, "MakeData"), exist_ok=True)
    for sRelPath in ("MakeData/makeData.py", "MakeData/out.json"):
        with open(os.path.join(sProjectRepo, sRelPath), "w") as fileOut:
            fileOut.write("# fixture\\n")
    # A script that is pinned and token-free but predates its
    # generator: green under the older gate, red under the newer one.
    with open(
        os.path.join(sProjectRepo, S_REPRODUCE_SCRIPT_FILENAME), "w",
    ) as fileOut:
        fileOut.write("#!/usr/bin/env bash\\nset -euo pipefail\\n")
    from vaibify.reproducibility.manifestWriter import fnWriteManifest
    fnWriteManifest(sProjectRepo, dictWorkflow)
    dictRows = _fdictEnvelopeArtifactSatisfaction(
        dictWorkflow, sProjectRepo,
    )
    dictArrow = levelOrdering.fdictJudgeOrderedRequirements(
        dictWorkflow, sProjectRepo,
    )
    listShared = sorted(
        set(dictRows) & set(dictArrow) - {"dependencyLock"}
    )
    assert "manifest" in listShared and "reproduceScript" in listShared, (
        "the overlap this test compares has shrunk to "
        f"{listShared}, so it may be asserting nothing"
    )
    for sRow in listShared:
        assert bool(dictArrow[sRow]) == bool(dictRows[sRow]), (
            f"the arrow and the {sRow} row disagree: arrow says "
            f"{dictArrow[sRow]}, the row renders {dictRows[sRow]}"
        )
    assert dictArrow["reproduceScript"] is False, (
        "the fixture is meant to hold a stale script"
    )


def test_the_poll_payload_carries_the_verdict(sProjectRepo):
    """Computed on the backend, rendered by the dashboard, never re-derived."""
    from vaibify.gui.routes.pipelineRoutes import (
        _fdictBuildWorkflowEnvelopeDetail,
    )
    import inspect
    sSource = inspect.getsource(_fdictBuildWorkflowEnvelopeDetail)
    assert "dictNextOrderedStep" in sSource
    assert "levelOrdering.fdictDescribeNextOrderedStep" in sSource


def test_the_arrow_precedes_the_banner_it_sits_inside():
    """First-match dispatch walks ancestors, so order is load-bearing.

    Registered after ``.project-block-header``, a click on "Do this
    first" collapses the Project block and hides the row the arrow
    just named. This is the Align-button trap, which the registry
    already carries a comment about.
    """
    import vaibify.gui as guiPackage
    sPath = os.path.join(
        os.path.dirname(guiPackage.__file__), "static",
        "scriptEventBindings.js",
    )
    with open(sPath) as fileIn:
        sText = fileIn.read()
    iArrow = sText.index('".ordering-arrow":')
    iBanner = sText.index('".project-block-header":')
    assert iArrow < iBanner, (
        "the arrow must be registered before the banner it sits inside"
    )


def test_a_lock_the_image_does_not_satisfy_is_named_first(monkeypatch):
    """The live gap: the rerun refuses, so everything after it is wasted.

    Found on 2026-09-15 by a researcher who spent a verification to be
    told their lock was five days older than their image. The
    Dependency-lock row was green throughout -- it asked only whether
    every entry carried a hash -- and no arrow pointed there.
    """
    from vaibify.reproducibility import lockSatisfaction
    monkeypatch.setattr(
        levelOrdering, "fdictJudgeOrderedRequirements",
        lambda dictWorkflow, filesRepo, dictLock=None,
        dictCurrency=None: (
            _fdictAllSatisfiedExcept(
                "dependencyLock", "rebuildAttestation",
                "envelopeMirror", "envelopeArchive",
            )
        ),
    )
    dictNext = levelOrdering.fdictDescribeNextOrderedStep({}, "/nowhere")
    assert dictNext["sRowKey"] == "dependencyLock"
    assert "refuse before it starts" in dictNext["sReason"]
    assert sorted(dictNext["listBlockedRowKeys"]) == [
        "envelopeArchive", "envelopeMirror", "rebuildAttestation",
    ]
    assert lockSatisfaction.S_LOCK_MISMATCH == "mismatch"


def test_an_unchecked_lock_points_no_arrow_at_it(sProjectRepo):
    """Unknown is not a mismatch.

    The poll may not exec, so on most ticks nobody has asked whether
    the image satisfies the lock. Treating that silence as a fault
    would put an arrow on the Dependency-lock row of every project
    between restarts.
    """
    dictWorkflow = {"listSteps": [], "sWorkflowName": "p"}
    dictJudged = levelOrdering.fdictJudgeOrderedRequirements(
        dictWorkflow, sProjectRepo, None,
    )
    dictJudgedUnknown = levelOrdering.fdictJudgeOrderedRequirements(
        dictWorkflow, sProjectRepo, {"sState": "unknown"},
    )
    assert dictJudged["dependencyLock"] == (
        dictJudgedUnknown["dependencyLock"]
    ), "an unknown verdict must read exactly as an unasked one"


# ----------------------------------------------------------------------
# The two archives are separate nodes, and only an ASYMMETRIC pair
# can tell the decomposed design from the combined one
# ----------------------------------------------------------------------

_S_SANDBOX = "sandbox"
_S_PERMANENT = "permanent"


def _fnStubEveryGate(monkeypatch, dictPermanence, **dictOverrides):
    """Make every ordering gate pass, then apply the named overrides.

    The arrow only fires on an edge live at BOTH ends, so a fixture
    that left unrelated rows unsatisfied would produce an answer about
    whichever chain happened to be longest. Stubbing the lot and
    reaching in for one pair is what makes each assertion about the
    pair it names.
    """
    from vaibify.reproducibility import levelGates
    dictGates = {
        "fbVerifyDependencyLock": True,
        "fbVerifyManifestComplete": True,
        "fbVerifyReproduceScript": True,
        "fbVerifyReproduceScriptCurrent": True,
        "fbImageArchiveDeposited": True,
        "fbEnvelopeMatchesGithubMirror": True,
        "fbEnvelopeMatchesZenodoArchive": True,
        "fbAttestationIsPubliclyArchived": True,
        # Derived from the permanence above, NEVER forced True. The
        # combined gate is the thing these tests are about: a node
        # that read it instead of its own archive's verdict is the
        # defect, and stubbing it True neutralises that mutation --
        # measured, both asymmetric tests reported SURVIVED until
        # this line existed.
        "fbNoArchiveIsKnownSandbox": not any(
            sValue == _S_SANDBOX for sValue in dictPermanence.values()
            if isinstance(sValue, str)
        ),
    }
    dictGates.update(dictOverrides)
    for sName, bValue in dictGates.items():
        monkeypatch.setattr(
            levelGates, sName,
            lambda *args, bValue=bValue, **kwargs: bValue,
        )
    monkeypatch.setattr(
        levelGates, "fdictArchivePermanenceState",
        lambda *args, **kwargs: dictPermanence,
    )
    monkeypatch.setattr(
        levelOrdering, "fbL3AttestationCurrent",
        lambda *args, **kwargs: dictOverrides.get(
            "fbL3AttestationCurrent", True,
        ),
    )


def _fdictJudgeUnderStubs(monkeypatch, dictPermanence, **dictOverrides):
    _fnStubEveryGate(monkeypatch, dictPermanence, **dictOverrides)
    return levelOrdering.fdictJudgeOrderedRequirements({}, "/nowhere")


@pytest.mark.falsification
def test_a_sandbox_project_deposit_never_lights_the_environment_row(
    monkeypatch,
):
    """The image is permanent; only the PROJECT deposit is a sandbox one.

    ``fbNoArchiveIsKnownSandbox`` classifies BOTH archives, so a node
    judged from it goes unsatisfied here as well -- and if that node is
    the environment one, the arrow sends the researcher to a Make
    Permanent button that promotes the wrong archive. A fixture with
    both deposits in the same state passes against the broken design
    and proves nothing, which is why this pair is asymmetric.

    Kills: judging ``environmentArchive`` from the combined sandbox
    gate rather than from ``sImageArchivePermanence``.
    """
    dictJudged = _fdictJudgeUnderStubs(monkeypatch, {
        "sImageArchivePermanence": _S_PERMANENT,
        "sProjectArchivePermanence": _S_SANDBOX,
    })
    assert dictJudged["environmentArchive"] is True, (
        "a sandbox PROJECT deposit marked the ENVIRONMENT archive row "
        "unsatisfied; its button promotes the image deposit, which is "
        "not what is wrong"
    )
    assert dictJudged["envelopeArchive"] is False
    assert dictJudged["rebuildAttestation"] is True, (
        "the rerun happened and the attestation is current; permanence "
        "is not this row's question and it carries neither remedy"
    )


@pytest.mark.falsification
def test_a_sandbox_image_deposit_never_lights_the_zenodo_row(monkeypatch):
    """The reverse: a sandbox IMAGE deposit and a permanent project one.

    Kills: judging ``envelopeArchive`` from the combined sandbox gate,
    which would send a researcher to publish a new immutable Zenodo
    version over a project deposit that is already permanent.
    """
    dictJudged = _fdictJudgeUnderStubs(monkeypatch, {
        "sImageArchivePermanence": _S_SANDBOX,
        "sProjectArchivePermanence": _S_PERMANENT,
    })
    assert dictJudged["environmentArchive"] is False
    assert dictJudged["envelopeArchive"] is True
    assert dictJudged["rebuildAttestation"] is True


@pytest.mark.falsification
def test_an_unknown_permanence_keeps_both_archive_rows_satisfied(
    monkeypatch,
):
    """``unknown`` passes: the gate fails open in the researcher's favour.

    Spelling permanence as ``== permanent`` would block every deposit
    vaibify cannot classify -- a hand-edited envelope, a service from a
    future release -- and there is no harm this ordering prevents that
    an abstention causes.

    Kills: rewriting ``_fbIsSandbox`` as ``!= permanent``.
    """
    dictJudged = _fdictJudgeUnderStubs(monkeypatch, {
        "sImageArchivePermanence": "unknown",
        "sProjectArchivePermanence": "unknown",
    })
    assert dictJudged["environmentArchive"] is True
    assert dictJudged["envelopeArchive"] is True


@pytest.mark.falsification
def test_an_archive_holding_no_covering_attestation_is_named(monkeypatch):
    """The silence this widening fixed.

    Zenodo holds the envelope, so ``fbEnvelopeMatchesZenodoArchive``
    passes -- but the archived attestation does not cover the archived
    manifest, so Level 3 fails. Judged on the envelope alone, every
    publication node was satisfied, no edge was live at both ends, and
    the arrow said nothing at exactly the moment the researcher needed
    it. All three of this node's conjuncts have ONE remedy: publish a
    Zenodo version.

    Kills: dropping ``fbAttestationIsPubliclyArchived`` from the
    ``envelopeArchive`` node.
    """
    dictJudged = _fdictJudgeUnderStubs(
        monkeypatch,
        {"sImageArchivePermanence": _S_PERMANENT,
         "sProjectArchivePermanence": _S_PERMANENT},
        fbAttestationIsPubliclyArchived=False,
    )
    assert dictJudged["envelopeArchive"] is False


def test_two_real_roots_are_answered_with_silence(monkeypatch):
    """The fork the shipped graph can now produce, not a synthetic one.

    Before the environment archive became a node, the edge set was a
    tree and two roots could only be staged by replacing the table --
    which is a guard on the algorithm, not on this graph. The lock and
    the archive are genuinely independent (neither undoes the other)
    and both point at the same three later rows, so a project blocked
    on both has no single next step, and saying otherwise would tell
    the researcher the other chain is not ready to start.
    """
    assert _fdictNextFrom(
        _fdictAllSatisfiedExcept(
            "dependencyLock", "environmentArchive",
            "rebuildAttestation", "envelopeMirror", "envelopeArchive",
        ),
        monkeypatch,
    ) is None


def test_the_environment_archive_orders_everything_the_manifest_does(
    monkeypatch,
):
    """Its deposit re-pins MANIFEST.sha256, so a rerun made first is stale.

    The live consequence: depositing after the verification wastes the
    verification. Asserted as an ARROW answer rather than as an edge
    table entry, so it is the researcher-facing behaviour that is
    pinned.
    """
    dictNext = _fdictNextFrom(
        _fdictAllSatisfiedExcept(
            "environmentArchive", "rebuildAttestation",
            "envelopeMirror", "envelopeArchive",
        ),
        monkeypatch,
    )
    assert dictNext["sRowKey"] == "environmentArchive"
    assert sorted(dictNext["listBlockedRowKeys"]) == [
        "envelopeArchive", "envelopeMirror", "rebuildAttestation",
    ]


@pytest.mark.falsification
def test_the_dependency_lock_is_the_only_row_allowed_to_diverge(
    sProjectRepo,
):
    """The named exception, asserted as an exception rather than believed.

    The Dependency-lock ROW stays green over a lock the container does
    not satisfy (ruled 2026-09-15: the row's criterion is about the
    repository's envelope, and the container is a different question),
    while the ARROW must still name it, because the rerun refuses
    before it starts. That divergence is indistinguishable from the bug
    the agreement invariant exists to catch -- so it is pinned as the
    ONLY one, and the next agent to notice it finds this test rather
    than a reason to "fix" the ruling away.

    Kills: adding the lock-satisfaction conjunct back to the row (the
    divergence disappears), and adding a second row-vs-arrow
    divergence anywhere else.
    """
    from vaibify.gui.routes.pipelineRoutes import (
        _fdictEnvelopeArtifactSatisfaction,
    )
    from vaibify.reproducibility import lockSatisfaction
    dictWorkflow = {"sWorkflowName": "project", "listSteps": []}
    with open(
        os.path.join(sProjectRepo, "requirements.lock"), "w",
    ) as fileLock:
        fileLock.write("numpy==2.5.2 \\\n    --hash=sha256:00\n")
    dictRows = _fdictEnvelopeArtifactSatisfaction(
        dictWorkflow, sProjectRepo,
    )
    dictArrow = levelOrdering.fdictJudgeOrderedRequirements(
        dictWorkflow, sProjectRepo,
        {"sState": lockSatisfaction.S_LOCK_MISMATCH},
        {"bPinnedImageIsLive": True},
    )
    listDiverged = sorted(
        sRow for sRow in dictRows
        if sRow in dictArrow
        and bool(dictRows[sRow]) != bool(dictArrow[sRow])
    )
    assert listDiverged == ["dependencyLock"], (
        "the set of rows whose rendered state disagrees with the "
        f"arrow's verdict changed: {listDiverged}. Exactly one row is "
        "permitted to diverge, and only because a ruling put the "
        "warning in an amber note rather than in the row's colour"
    )
