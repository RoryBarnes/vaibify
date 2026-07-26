"""A researcher's attestation must survive moving to another machine.

Git stamps every file with the checkout time, so on a fresh clone every
plot is "newer than" ``sLastUserUpdate`` and the mtime comparison in
``_ftEvaluateFreshness`` marks every attestation stale — even though the
bytes are byte-identical and committed. Pull on a second machine and
every "yes, I looked at this plot" silently disappears.

The test-marker lane already solved this class with committed content
hashes ("a fresh clone with identical content stays green"). These
tests assert the researcher-verification lane now behaves the same way:
mtime detects a change, content decides whether there was one.
"""

import pytest

from vaibify.gui.fileStatusManager import (
    fbReconcileUserVerificationByContentHash,
)


S_REPO_ROOT = "/workspace/SomeRepo"
S_PLOT_SHA = "a" * 64


class _StubRepoFiles:
    """Snapshot stub returning fixed hashes for repo-relative paths."""

    def __init__(self, dictHashes):
        self._dictHashes = dictHashes

    def fdictHashFiles(self, listRelPaths):
        return {
            sPath: {"sSha256": self._dictHashes.get(sPath)}
            for sPath in listRelPaths
        }


def _fdictStep(sUser, dictRecordedHashes=None):
    """Return a step declaring one plot, with the given attestation."""
    dictVerification = {
        "sUser": sUser,
        "sLastUserUpdate": "2026-07-01 12:00:00 UTC",
    }
    if dictRecordedHashes is not None:
        dictVerification["dictUserVerifiedHashes"] = dictRecordedHashes
    return {
        "sName": "Plot Step",
        "sDirectory": "PlotStep",
        "saPlotFiles": ["figure.pdf"],
        "saOutputDataFiles": [],
        "dictVerification": dictVerification,
    }


def _fdictWorkflow(dictStep):
    return {"sProjectRepoPath": S_REPO_ROOT, "listSteps": [dictStep]}


def test_passing_attestation_records_its_plot_hashes():
    """The hashes must be recorded while the step still reads passed."""
    dictStep = _fdictStep("passed")
    filesRepo = _StubRepoFiles({"PlotStep/figure.pdf": S_PLOT_SHA})

    bChanged = fbReconcileUserVerificationByContentHash(
        _fdictWorkflow(dictStep), filesRepo, S_REPO_ROOT,
    )

    assert bChanged is True
    assert dictStep["dictVerification"]["dictUserVerifiedHashes"] == {
        "PlotStep/figure.pdf": S_PLOT_SHA,
    }


@pytest.mark.falsification
def test_fresh_clone_does_not_discard_the_researchers_attestation():
    """Identical bytes under a new mtime must stay verified.

    This is the machine-A-to-machine-B case: the plot was attested on
    one machine, committed, and cloned onto another, where git gave it
    a checkout-time mtime. The mtime pass marks it stale; the content
    is unchanged, so it must be restored.

    Kills: in fileStatusManager.fbReconcileUserVerificationByContentHash,
    remove the restore branch (the ``dictRecorded == dictCurrent``
    assignment of sUser back to "passed").
    """
    dictStep = _fdictStep(
        "stale", {"PlotStep/figure.pdf": S_PLOT_SHA},
    )
    filesRepo = _StubRepoFiles({"PlotStep/figure.pdf": S_PLOT_SHA})

    bChanged = fbReconcileUserVerificationByContentHash(
        _fdictWorkflow(dictStep), filesRepo, S_REPO_ROOT,
    )

    assert bChanged is True
    assert dictStep["dictVerification"]["sUser"] == "passed"


@pytest.mark.falsification
def test_a_genuinely_changed_plot_stays_stale():
    """Different bytes must NOT be restored — the honesty direction.

    The restore must key on content, never on the mere presence of a
    recorded hash, or a real change would be laundered into a verified
    state and the dashboard would vouch for a plot nobody looked at.

    Kills: in fileStatusManager.fbReconcileUserVerificationByContentHash,
    weaken the restore condition from ``dictRecorded == dictCurrent``
    to ``dictRecorded`` alone.
    """
    dictStep = _fdictStep(
        "stale", {"PlotStep/figure.pdf": S_PLOT_SHA},
    )
    filesRepo = _StubRepoFiles({"PlotStep/figure.pdf": "b" * 64})

    fbReconcileUserVerificationByContentHash(
        _fdictWorkflow(dictStep), filesRepo, S_REPO_ROOT,
    )

    assert dictStep["dictVerification"]["sUser"] == "stale"


@pytest.mark.falsification
def test_a_stale_step_never_adopts_the_current_hash_as_verified():
    """Recording must not happen while the step reads stale.

    Otherwise the poll after a real change would record the NEW hash
    and the following poll would restore the attestation — the change
    would verify itself.

    Kills: in fileStatusManager.fbReconcileUserVerificationByContentHash,
    move the recording branch so it also runs for a stale step (drop
    the ``if sUser == "passed":`` guard around it).
    """
    dictStep = _fdictStep("stale")
    filesRepo = _StubRepoFiles({"PlotStep/figure.pdf": S_PLOT_SHA})

    fbReconcileUserVerificationByContentHash(
        _fdictWorkflow(dictStep), filesRepo, S_REPO_ROOT,
    )

    assert "dictUserVerifiedHashes" not in dictStep["dictVerification"]
    assert dictStep["dictVerification"]["sUser"] == "stale"


def test_an_unsampled_plot_hash_is_never_recorded():
    """A hash the poll did not sample must not vouch for anything.

    Two missing hashes would otherwise compare equal and restore an
    attestation on the strength of two files nobody read.
    """
    dictStep = _fdictStep("passed")
    filesRepo = _StubRepoFiles({})

    bChanged = fbReconcileUserVerificationByContentHash(
        _fdictWorkflow(dictStep), filesRepo, S_REPO_ROOT,
    )

    assert bChanged is False
    assert "dictUserVerifiedHashes" not in dictStep["dictVerification"]
