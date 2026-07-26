"""The host-side anchor closes the flag-log truncation hole.

Both attribution chains are prefix-valid, so deleting trailing flag
records leaves a chain that still verifies. The workflow's persisted
``iUnattributedFlagCount`` narrows that hole but cannot close it: it
lives in ``project.json``, inside the same container-writable
repository as ``flags.jsonl``, so an agent that edits both leaves two
artifacts in agreement.

These tests drive that exact attack — truncate the log AND zero the
count — and assert the gate still refuses. The anchor lives under the
researcher's home directory, outside the container the supervised
party is confined to, which is the whole reason it works.
"""

import json

import pytest

from vaibify.gui.attributionLog import (
    S_ATTRIBUTION_FLAGS_PATH,
    fdictSummarizeSupervisionEvidence,
    fnAppendFlag,
)
from vaibify.gui import supervisionAnchor
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
from vaibify.reproducibility.replayGate import fbSupervisionClean


@pytest.fixture
def fixtureIsolatedHome(tmp_path, monkeypatch):
    """Point the anchor store at a scratch home directory."""
    pathHome = tmp_path / "home"
    pathHome.mkdir()
    monkeypatch.setenv("HOME", str(pathHome))
    return pathHome


def _fdictSupervisedWorkflow(sRepoPath, iPersistedCount):
    """Return a workflow with supervision on and a persisted count."""
    return {
        "sProjectRepoPath": sRepoPath,
        "dictAiProvenance": {
            "dictSupervision": {
                "bEnabled": True,
                "iUnattributedFlagCount": iPersistedCount,
            },
        },
    }


def _fnTruncateFlagLog(pathRepo, iKeepRecords):
    """Rewrite flags.jsonl keeping only the first iKeepRecords lines."""
    pathFlags = pathRepo / S_ATTRIBUTION_FLAGS_PATH
    listLines = pathFlags.read_text().splitlines(keepends=True)
    pathFlags.write_text("".join(listLines[:iKeepRecords]))


def test_anchor_seeds_itself_and_does_not_flag_a_first_observation(
    tmp_path, fixtureIsolatedHome,
):
    """With no prior anchor there is nothing to contradict."""
    pathRepo = tmp_path / "repo"
    filesRepo = ffilesEnsureRepoFiles(str(pathRepo))
    fnAppendFlag(filesRepo, "unattributed-modification", "fileA")
    dictWorkflow = _fdictSupervisedWorkflow(str(pathRepo), 1)

    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, dictWorkflow,
    )
    assert dictEvidence["bHostAnchorConsistent"] is True

    sKey = supervisionAnchor.fsAnchorKeyFor(str(pathRepo))
    assert supervisionAnchor.fdictReadAnchor(sKey)["iFlagCount"] == 1


def test_growing_the_flag_log_advances_the_anchor(
    tmp_path, fixtureIsolatedHome,
):
    """Normal accumulation of new flags is never a contradiction."""
    pathRepo = tmp_path / "repo"
    filesRepo = ffilesEnsureRepoFiles(str(pathRepo))
    fnAppendFlag(filesRepo, "unattributed-modification", "fileA")
    fdictSummarizeSupervisionEvidence(
        filesRepo, _fdictSupervisedWorkflow(str(pathRepo), 1),
    )
    fnAppendFlag(filesRepo, "unattributed-modification", "fileB")

    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, _fdictSupervisedWorkflow(str(pathRepo), 2),
    )
    assert dictEvidence["bHostAnchorConsistent"] is True
    sKey = supervisionAnchor.fsAnchorKeyFor(str(pathRepo))
    assert supervisionAnchor.fdictReadAnchor(sKey)["iFlagCount"] == 2


@pytest.mark.falsification
def test_truncating_the_log_and_the_count_together_still_fails_the_gate(
    tmp_path, fixtureIsolatedHome,
):
    """The residual hole: edit both in-repo artifacts, still refused.

    flags.jsonl and project.json are both container-writable, so an
    agent can truncate the log and zero the persisted count, leaving a
    prefix-valid chain whose count matches. Every in-repo check then
    passes. Only the host anchor, which the container cannot reach,
    still remembers that three flags were once observed.

    Kills: in attributionLog._fbHostAnchorConsistent, return True
    unconditionally instead of consulting the anchor.
    """
    pathRepo = tmp_path / "repo"
    filesRepo = ffilesEnsureRepoFiles(str(pathRepo))
    for sDetail in ("fileA", "fileB", "fileC"):
        fnAppendFlag(filesRepo, "unattributed-modification", sDetail)

    # The hub observes three flags and anchors them host-side.
    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, _fdictSupervisedWorkflow(str(pathRepo), 3),
    )
    assert dictEvidence["iFlagCount"] == 3

    # The agent now erases every trace it can reach.
    _fnTruncateFlagLog(pathRepo, 0)
    dictWorkflow = _fdictSupervisedWorkflow(str(pathRepo), 0)

    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, dictWorkflow,
    )
    # Every in-repo signal now looks clean...
    assert dictEvidence["iFlagCount"] == 0
    assert dictEvidence["bFlagChainIntact"] is True
    assert dictEvidence["bPersistedFlagCountMatches"] is True
    # ...and the host anchor is the only thing that still objects.
    assert dictEvidence["bHostAnchorConsistent"] is False
    assert fbSupervisionClean(dictWorkflow, dictEvidence) is False


@pytest.mark.falsification
def test_rewriting_records_in_place_is_caught_by_the_head_digest(
    tmp_path, fixtureIsolatedHome,
):
    """Same count, different content, must still contradict the anchor.

    A count-only anchor would miss an agent that rewrites a flag's
    detail in place rather than deleting it, so the anchor pins the
    chain head digest too.

    Kills: in supervisionAnchor.fbAnchorContradictedBy, drop the
    equal-count head-digest comparison (return False on that branch).
    """
    pathRepo = tmp_path / "repo"
    filesRepo = ffilesEnsureRepoFiles(str(pathRepo))
    fnAppendFlag(filesRepo, "unattributed-modification", "the real finding")
    fdictSummarizeSupervisionEvidence(
        filesRepo, _fdictSupervisedWorkflow(str(pathRepo), 1),
    )

    pathFlags = pathRepo / S_ATTRIBUTION_FLAGS_PATH
    dictFlag = json.loads(pathFlags.read_text().splitlines()[0])
    dictFlag["sDetail"] = "something harmless"
    pathFlags.write_text(json.dumps(dictFlag) + "\n")

    dictEvidence = fdictSummarizeSupervisionEvidence(
        filesRepo, _fdictSupervisedWorkflow(str(pathRepo), 1),
    )
    assert dictEvidence["bHostAnchorConsistent"] is False


@pytest.mark.falsification
def test_anchor_never_lowers_itself(tmp_path, fixtureIsolatedHome):
    """A smaller count must not overwrite a larger recorded one.

    Monotonicity is what makes the anchor evidence: if a truncation
    could write its own smaller count back, it would launder itself on
    the very next observation.

    Kills: in supervisionAnchor.fnRecordAnchor, remove the guard that
    refuses to lower an existing count.
    """
    sKey = supervisionAnchor.fsAnchorKeyFor("/workspace/SomeRepo")
    supervisionAnchor.fnRecordAnchor(sKey, 5, "headFive")
    supervisionAnchor.fnRecordAnchor(sKey, 1, "headOne")
    assert supervisionAnchor.fdictReadAnchor(sKey)["iFlagCount"] == 5


def test_absent_anchor_is_unknown_not_a_contradiction():
    """A missing anchor must never be read as evidence of tampering."""
    assert supervisionAnchor.fbAnchorContradictedBy({}, [], "") is False
