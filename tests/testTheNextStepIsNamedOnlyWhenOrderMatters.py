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
    "manifest", "reproduceScript", "rebuildAttestation",
    "envelopeMirror", "envelopeArchive",
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
        lambda dictWorkflow, filesRepo: dictSatisfied,
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
    assert "MANIFEST.sha256 pins reproduce.sh" in dictNext["sReason"]


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
            "reproduceScript": True,
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
    for sRow in ("manifest", "reproduceScript"):
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
