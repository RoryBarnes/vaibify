"""The poll holds every sign-off to the plot bytes it attested.

One test per row of the reconciliation table
(``fileStatusManager.fbReconcileUserVerificationByContentHash``). The
step declares a templated and a literal plot, so every comparison runs
against the step's FULL declared list as ``fsStepFileRepoRelative``
resolves it: a plot omitted from the current hashes is a difference,
never "nothing to compare".
"""

import pytest

from vaibify.gui.fileStatusManager import (
    fbReconcileUserVerificationByContentHash,
)

S_REPO_ROOT = "/workspace/Project"
S_TEMPLATED_KEY = "Plot/fig.pdf"
S_LITERAL_KEY = "Step/figure.pdf"
S_KEY = "dictUserVerifiedHashes"
DICT_ATTESTED = {S_TEMPLATED_KEY: "1" * 64, S_LITERAL_KEY: "2" * 64}


class _StubRepoFiles:
    """Answer each path with the hash given, ``None`` when not given."""

    def __init__(self, dictHashes):
        self._dictHashes = dictHashes

    def fdictHashFiles(self, listRelPaths):
        return {
            sPath: {"sSha256": self._dictHashes.get(sPath)}
            for sPath in listRelPaths
        }


def _fdictWorkflow(sUser, dictRecorded=None, bWithPlots=True):
    dictVerification = {"sUser": sUser, "sLastUserUpdate": "2026-10-15 12:00:00 UTC"}
    if dictRecorded is not None:
        dictVerification[S_KEY] = dict(dictRecorded)
    return {
        "sProjectRepoPath": S_REPO_ROOT, "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "listSteps": [{
            "sName": "Step", "sDirectory": "Step",
            "saOutputDataFiles": [],
            "saPlotFiles": (
                ["{sPlotDirectory}/fig.{sFigureType}", "figure.pdf"]
                if bWithPlots else []),
            "dictVerification": dictVerification,
        }],
    }


def _ftReconcile(dictWorkflow, dictOnDisk):
    bChanged = fbReconcileUserVerificationByContentHash(
        dictWorkflow, _StubRepoFiles(dictOnDisk), S_REPO_ROOT)
    return bChanged, dictWorkflow["listSteps"][0]["dictVerification"]


def test_a_legacy_sign_off_records_a_complete_capture_once():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed"), DICT_ATTESTED)
    assert bChanged is True
    assert dictVerification["sUser"] == "passed"
    assert dictVerification[S_KEY] == DICT_ATTESTED


def test_a_legacy_sign_off_with_one_unreadable_plot_goes_stale_and_records_nothing():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed"), {S_TEMPLATED_KEY: "1" * 64})
    assert bChanged is True
    assert dictVerification["sUser"] == "stale"
    assert S_KEY not in dictVerification


def test_a_legacy_sign_off_with_every_plot_missing_goes_stale_and_records_nothing():
    bChanged, dictVerification = _ftReconcile(_fdictWorkflow("passed"), {})
    assert bChanged is True
    assert dictVerification["sUser"] == "stale"
    assert S_KEY not in dictVerification


def test_a_sign_off_whose_plots_still_match_is_left_alone():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed", DICT_ATTESTED), DICT_ATTESTED)
    assert bChanged is False
    assert dictVerification["sUser"] == "passed"


@pytest.mark.falsification
def test_a_changed_plot_voids_the_sign_off_and_keeps_the_attested_bytes():
    """Kills: adopting the bytes on disk as attested when they differ."""
    dictOnDisk = dict(DICT_ATTESTED, **{S_LITERAL_KEY: "3" * 64})
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed", DICT_ATTESTED), dictOnDisk)
    assert bChanged is True
    assert dictVerification["sUser"] == "stale"
    assert dictVerification[S_KEY] == DICT_ATTESTED


def test_a_deleted_plot_voids_the_sign_off_and_keeps_the_attested_bytes():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed", DICT_ATTESTED), {S_TEMPLATED_KEY: "1" * 64})
    assert bChanged is True
    assert dictVerification["sUser"] == "stale"
    assert dictVerification[S_KEY] == DICT_ATTESTED


def test_every_declared_plot_gone_voids_the_sign_off():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed", DICT_ATTESTED), {})
    assert bChanged is True
    assert dictVerification["sUser"] == "stale"
    assert dictVerification[S_KEY] == DICT_ATTESTED


def test_a_step_with_no_plots_and_an_empty_baseline_is_left_alone():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("passed", {}, bWithPlots=False), {})
    assert bChanged is False
    assert dictVerification["sUser"] == "passed"


def test_a_stale_sign_off_whose_bytes_match_again_is_restored():
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("stale", DICT_ATTESTED), DICT_ATTESTED)
    assert bChanged is True
    assert dictVerification["sUser"] == "passed"


@pytest.mark.parametrize("dictRecorded, dictOnDisk, bWithPlots", [
    (DICT_ATTESTED, {S_TEMPLATED_KEY: "1" * 64, S_LITERAL_KEY: "9" * 64}, True),
    (DICT_ATTESTED, {S_TEMPLATED_KEY: "1" * 64}, True),
    (None, DICT_ATTESTED, True),
    ({}, {}, False),
])
def test_a_stale_sign_off_is_otherwise_left_stale(
    dictRecorded, dictOnDisk, bWithPlots,
):
    bChanged, dictVerification = _ftReconcile(
        _fdictWorkflow("stale", dictRecorded, bWithPlots), dictOnDisk)
    assert bChanged is False
    assert dictVerification["sUser"] == "stale"
    assert dictVerification.get(S_KEY) == dictRecorded


def _ftTimestampCheckWithAPlotNewerThanTheSignOff(dictRecorded):
    """Run the poll's timestamp check; return (bChanged, sUser)."""
    import calendar
    import time
    from vaibify.gui.fileStatusManager import _fbCheckStaleUserVerification
    dictWorkflow = _fdictWorkflow("passed", dictRecorded)
    iSignedOff = calendar.timegm(time.strptime(
        "2026-10-15 12:00:00 UTC", "%Y-%m-%d %H:%M:%S UTC"))
    dictModTimes = {
        S_REPO_ROOT + "/Plot/fig.pdf": str(iSignedOff + 600),
        S_REPO_ROOT + "/Step/figure.pdf": str(iSignedOff + 600),
    }
    bChanged = _fbCheckStaleUserVerification(dictWorkflow, dictModTimes, {
        "sRepoRoot": S_REPO_ROOT, "sPlotDirectory": S_REPO_ROOT + "/Plot",
        "sFigureType": "pdf"})
    return bChanged, dictWorkflow["listSteps"][0]["dictVerification"]["sUser"]


@pytest.mark.falsification
def test_a_sign_off_with_a_recorded_baseline_is_left_to_the_content_pass():
    """Kills: the timestamp check judging a sign-off whose bytes are
    recorded, which the content pass then reverses on every poll."""
    assert _ftTimestampCheckWithAPlotNewerThanTheSignOff(DICT_ATTESTED) == (
        False, "passed")


def test_a_sign_off_from_before_baselines_still_goes_stale_by_the_clock():
    assert _ftTimestampCheckWithAPlotNewerThanTheSignOff(None) == (
        True, "stale")
