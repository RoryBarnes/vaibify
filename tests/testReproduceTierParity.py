"""Tier 3 and Tier 4 must agree with the readers and gates they claim to share.

Both defects here are the same shape as the Tier 5 wrong-root bug: two
code paths that answer one question and quietly disagree. Tier 3 read
the image digest from a layout its two sibling readers do not use;
Tier 4 advertised parity with the dashboard's readiness gate while
skipping one of its seven verifiers.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner


def _fnWriteWorkflow(pathRepo, sName, dictExtra=None):
    """Write one project.json under .vaibify/workflows and return its path."""
    pathWorkflows = pathRepo / ".vaibify" / "workflows"
    pathWorkflows.mkdir(parents=True, exist_ok=True)
    dictWorkflow = {"sName": sName, "listSteps": []}
    dictWorkflow.update(dictExtra or {})
    pathFile = pathWorkflows / f"{sName}.json"
    pathFile.write_text(json.dumps(dictWorkflow), encoding="utf-8")
    return pathFile


@pytest.fixture
def fixtureRepo(tmp_path):
    """A project repo skeleton with a .vaibify directory."""
    pathRepo = tmp_path / "repo"
    (pathRepo / ".vaibify").mkdir(parents=True)
    return pathRepo


# ---------------------------------------------------------------------------
# Tier 3 — the nested image-digest layout
# ---------------------------------------------------------------------------


def testTier3AcceptsTheNestedImageDigestLayout(fixtureRepo):
    """A nested dictContainer.sImageDigest must satisfy Tier 3.

    ``environmentSnapshot._fsExtractImageDigest`` exists precisely to
    read "either supported layout", and ``_fsRecordedImageDigest``
    honours the nested form too. Tier 3's own loader read only the
    top-level key, so a snapshot written in the nested layout aborted
    the whole reproduce run with exit 2 while the digest sat right
    there.
    """
    from vaibify.cli.commandReproduce import _fsLoadImageDigest

    sDigest = "ghcr.io/example/image@sha256:" + "a" * 64
    pathEnvironment = fixtureRepo / "environment.json"
    pathEnvironment.write_text(
        json.dumps({"dictContainer": {"sImageDigest": sDigest}}),
        encoding="utf-8",
    )

    assert _fsLoadImageDigest(pathEnvironment, str(fixtureRepo)) == sDigest


def testTier3StillReadsTheFlatImageDigestLayout(fixtureRepo):
    """The flat layout keeps working — this is a widening, not a swap."""
    from vaibify.cli.commandReproduce import _fsLoadImageDigest

    sDigest = "ghcr.io/example/image@sha256:" + "b" * 64
    pathEnvironment = fixtureRepo / "environment.json"
    pathEnvironment.write_text(
        json.dumps({"sImageDigest": sDigest}), encoding="utf-8",
    )

    assert _fsLoadImageDigest(pathEnvironment, str(fixtureRepo)) == sDigest


def testTier3AgreesWithTheCanonicalReader(fixtureRepo):
    """Tier 3 and environmentSnapshot must never disagree about a payload.

    The bijection is the point: any payload one reader accepts, the
    other must accept, or the CLI and the dashboard grade differently.
    """
    from vaibify.cli.commandReproduce import _fsLoadImageDigest
    from vaibify.reproducibility.environmentSnapshot import (
        _fsExtractImageDigest,
    )

    sDigest = "ghcr.io/example/image@sha256:" + "c" * 64
    for dictPayload in (
        {"sImageDigest": sDigest},
        {"dictContainer": {"sImageDigest": sDigest}},
        {"dictContainer": {"sImageDigest": sDigest}, "sImageDigest": ""},
    ):
        pathEnvironment = fixtureRepo / "environment.json"
        pathEnvironment.write_text(json.dumps(dictPayload), encoding="utf-8")
        assert _fsExtractImageDigest(dictPayload) == sDigest
        assert _fsLoadImageDigest(
            pathEnvironment, str(fixtureRepo),
        ) == sDigest


# ---------------------------------------------------------------------------
# Tier 4 — the seventh readiness verifier
# ---------------------------------------------------------------------------


def testTier4RunsTheBinariesVerifier(fixtureRepo):
    """Tier 4 must evaluate all seven readiness verifiers, not six.

    ``fbWorkflowDeclaresBinaries`` was skipped because the aggregate
    carried no binary-declaration state, so a repo could clear the CLI
    gate and still be blocked by the dashboard's ``fbL3ReadinessOK``.
    The CLI claiming parity while being more permissive is the failure
    mode, not the missing check itself.
    """
    from vaibify.cli.commandReproduce import _flistRunReadinessVerifiers
    from vaibify.cli.commandReproduce import _fdictAggregateAllWorkflows

    _fnWriteWorkflow(
        fixtureRepo, "alpha",
        {"bNoStandaloneBinaries": True, "listDeclaredBinaries": []},
    )
    dictAggregate = _fdictAggregateAllWorkflows(str(fixtureRepo)) or {}
    listResults = _flistRunReadinessVerifiers(
        str(fixtureRepo), dictAggregate,
    )

    listLabels = [sLabel for sLabel, _ in listResults]
    assert len(listResults) == 7, (
        "Tier 4 must run seven verifiers to match fbL3ReadinessOK; "
        f"ran {len(listResults)}: {listLabels}"
    )
    assert any("binar" in sLabel.lower() for sLabel in listLabels), (
        f"no binary-declaration verifier among {listLabels}"
    )


def testTier4FailsAWorkflowWithNoBinaryDeclaration(fixtureRepo):
    """An unanswered binary question must block Tier 4, as it blocks L3."""
    from vaibify.cli.commandReproduce import _flistRunReadinessVerifiers
    from vaibify.cli.commandReproduce import _fdictAggregateAllWorkflows

    _fnWriteWorkflow(fixtureRepo, "alpha")  # neither waiver nor declaration
    dictAggregate = _fdictAggregateAllWorkflows(str(fixtureRepo)) or {}
    listResults = _flistRunReadinessVerifiers(
        str(fixtureRepo), dictAggregate,
    )

    listBinary = [
        bPassed for sLabel, bPassed in listResults
        if "binar" in sLabel.lower()
    ]
    assert listBinary and not listBinary[0], (
        "a workflow declaring neither a waiver nor any binary must fail"
    )


def testTier4RequiresEveryWorkflowToAnswer(fixtureRepo):
    """One coherent workflow must not carry an incoherent sibling.

    The project ladders L3 as a whole, so a union that lets a clean
    workflow mask an unanswered one would reintroduce the permissive
    gap in a subtler form.
    """
    from vaibify.cli.commandReproduce import _flistRunReadinessVerifiers
    from vaibify.cli.commandReproduce import _fdictAggregateAllWorkflows

    _fnWriteWorkflow(
        fixtureRepo, "alpha",
        {"bNoStandaloneBinaries": True, "listDeclaredBinaries": []},
    )
    _fnWriteWorkflow(fixtureRepo, "beta")  # unanswered
    dictAggregate = _fdictAggregateAllWorkflows(str(fixtureRepo)) or {}
    listResults = _flistRunReadinessVerifiers(
        str(fixtureRepo), dictAggregate,
    )

    listBinary = [
        bPassed for sLabel, bPassed in listResults
        if "binar" in sLabel.lower()
    ]
    assert listBinary and not listBinary[0], (
        "an unanswered sibling workflow must fail the project's gate"
    )


def testTier4DocstringNoLongerClaimsSixOfSeven(fixtureRepo):
    """The advertised count must track the implementation.

    A docstring that undercounts is how the gap stayed invisible; a
    docstring that overcounts is how it would return.
    """
    from vaibify.cli.commandReproduce import fbVerifyTier4

    sDoc = (fbVerifyTier4.__doc__ or "").lower()
    assert "six of the seven" not in sDoc, (
        "Tier 4 now runs all seven verifiers; its docstring still says six"
    )
