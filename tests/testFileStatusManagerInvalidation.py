"""Tests for uncovered branches in fileStatusManager."""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.fileStatusManager import (
    _fbAnyMtimeNewerThan,
    _fbPlotNewerThanUserVerification,
    _fdictDetectChangedFiles,
    _fdictStatBatch,
    _fiMarkerMtime,
    _flistDetectAndInvalidate,
    _flistNewerPaths,
    _fnInvalidateStepFiles,
)


# ---------------------------------------------------------------
# _flistNewerPaths: non-integer mtime handling (lines 471, 474-475)
# ---------------------------------------------------------------


def test_flistNewerPaths_skips_missing_mtime():
    listResult = _flistNewerPaths(
        ["/a/missing.dat"], {}, iThreshold=100,
    )
    assert listResult == []


def test_flistNewerPaths_skips_non_integer_mtime():
    listResult = _flistNewerPaths(
        ["/a/bad.dat"],
        {"/a/bad.dat": "not-a-number"},
        iThreshold=100,
    )
    assert listResult == []


def test_flistNewerPaths_skips_none_mtime():
    listResult = _flistNewerPaths(
        ["/a/null.dat"],
        {"/a/null.dat": None},
        iThreshold=100,
    )
    assert listResult == []


def test_flistNewerPaths_returns_newer_paths():
    listResult = _flistNewerPaths(
        ["/a/new.dat", "/a/old.dat"],
        {"/a/new.dat": "200", "/a/old.dat": "50"},
        iThreshold=100,
    )
    assert listResult == ["/a/new.dat"]


# ---------------------------------------------------------------
# _fbPlotNewerThanUserVerification: user-update edge cases
# ---------------------------------------------------------------


def test_fbPlotNewer_returns_false_if_no_plot_changed():
    dictStep = {
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {},
    }
    assert not _fbPlotNewerThanUserVerification(
        dictStep, ["/ws/data.out"], {},
    )


def test_fbPlotNewer_returns_true_if_no_user_timestamp():
    dictStep = {
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {},
    }
    assert _fbPlotNewerThanUserVerification(
        dictStep, ["/ws/figure.pdf"], {},
    )


def test_fbPlotNewer_returns_true_if_unparseable_timestamp():
    dictStep = {
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {
            "sLastUserUpdate": "not-a-timestamp",
        },
    }
    assert _fbPlotNewerThanUserVerification(
        dictStep, ["/ws/figure.pdf"], {},
    )


def test_fbPlotNewer_returns_false_when_plot_older():
    dictStep = {
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {
            "sLastUserUpdate": "2026-04-20 00:00:00 UTC",
        },
    }
    # Plot mtime is long before 2026-04-20.
    assert not _fbPlotNewerThanUserVerification(
        dictStep, ["/ws/figure.pdf"],
        {"/ws/figure.pdf": "1500000000"},
    )


def test_fbPlotNewer_returns_true_when_plot_newer():
    dictStep = {
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {
            "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
        },
    }
    assert _fbPlotNewerThanUserVerification(
        dictStep, ["/ws/figure.pdf"],
        {"/ws/figure.pdf": "1900000000"},
    )


# ---------------------------------------------------------------
# _fnInvalidateStepFiles sUser=passed + plot newer (lines 430-441)
# ---------------------------------------------------------------


def test_fnInvalidateStepFiles_user_passed_plot_newer_resets():
    dictStep = {
        "saDataFiles": [],
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {
            "sUser": "passed",
            "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
        },
    }
    _fnInvalidateStepFiles(
        dictStep, ["/ws/figure.pdf"],
        dictModTimes={"/ws/figure.pdf": "1900000000"},
    )
    assert dictStep["dictVerification"]["sUser"] == "untested"


def test_fnInvalidateStepFiles_user_passed_plot_older_preserved():
    dictStep = {
        "saDataFiles": [],
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {
            "sUser": "passed",
            "sLastUserUpdate": "2026-04-20 00:00:00 UTC",
        },
    }
    _fnInvalidateStepFiles(
        dictStep, ["/ws/figure.pdf"],
        dictModTimes={"/ws/figure.pdf": "1500000000"},
    )
    assert dictStep["dictVerification"]["sUser"] == "passed"


def test_fnInvalidateStepFiles_user_passed_no_plot_changed_preserved():
    dictStep = {
        "saDataFiles": ["data.out"],
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {
            "sUser": "passed",
            "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
        },
    }
    # Data file changed, plot file unchanged => sUser preserved
    _fnInvalidateStepFiles(
        dictStep, ["/ws/data.out"],
        dictModTimes={"/ws/data.out": "1900000000"},
    )
    assert dictStep["dictVerification"]["sUser"] == "passed"


# ---------------------------------------------------------------
# _fiMarkerMtime: TypeError/ValueError branches (lines 637-638)
# ---------------------------------------------------------------


def test_fiMarkerMtime_returns_none_when_absent():
    assert _fiMarkerMtime({}, 0) is None


def test_fiMarkerMtime_returns_none_on_non_integer():
    assert _fiMarkerMtime({"0": "bogus"}, 0) is None


def test_fiMarkerMtime_returns_int_on_valid():
    assert _fiMarkerMtime({"0": "1234"}, 0) == 1234


# ---------------------------------------------------------------
# _fdictDetectChangedFiles: running pipeline short-circuit (line 653)
# ---------------------------------------------------------------


def test_fdictDetectChangedFiles_returns_empty_while_running():
    dictCtx = {
        "dictPreviousModTimes": {"cid": {"/a/x": "100"}},
    }
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        dictResult = _fdictDetectChangedFiles(
            dictCtx, "cid",
            {"listSteps": []},
            {"/a/x": "200"},
        )
    assert dictResult == {}


def test_fdictDetectChangedFiles_returns_empty_on_first_poll():
    # No previous snapshot -> no detection yet.
    dictCtx = {}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        dictResult = _fdictDetectChangedFiles(
            dictCtx, "cid",
            {"listSteps": []},
            {"/a/x": "100"},
        )
    assert dictResult == {}
    # Baseline should be stored.
    assert dictCtx["dictPreviousModTimes"]["cid"] == {"/a/x": "100"}


# ---------------------------------------------------------------
# _flistDetectAndInvalidate: saves after invalidation (lines 698-701)
# ---------------------------------------------------------------


def test_flistDetectAndInvalidate_saves_when_changes_found():
    # Prime with an old snapshot so the next poll sees a change.
    mockSave = MagicMock()
    dictCtx = {
        "save": mockSave,
        "dictPreviousModTimes": {
            "cid": {"/ws/step0/out.dat": "100"},
        },
    }
    dictStep = {
        "sDirectory": "/ws/step0",
        "saDataFiles": ["out.dat"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
        "dictVerification": {"sUnitTest": "passed"},
    }
    dictWorkflow = {"listSteps": [dictStep]}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        dictResult = _flistDetectAndInvalidate(
            dictCtx, "cid", dictWorkflow,
            {"/ws/step0/out.dat": "200"},
            dictVars={"sPlotDirectory": "Plot", "sFigureType": "pdf"},
        )
    # Step 0 was invalidated because its output file changed.
    assert 0 in dictResult
    mockSave.assert_called_once_with("cid", dictWorkflow)


def test_flistDetectAndInvalidate_no_save_when_no_changes():
    mockSave = MagicMock()
    dictCtx = {
        "save": mockSave,
        "dictPreviousModTimes": {
            "cid": {"/ws/step0/out.dat": "100"},
        },
    }
    dictStep = {
        "sDirectory": "/ws/step0",
        "saDataFiles": ["out.dat"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
        "dictVerification": {},
    }
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        dictResult = _flistDetectAndInvalidate(
            dictCtx, "cid",
            {"listSteps": [dictStep]},
            {"/ws/step0/out.dat": "100"},  # Same mtime.
            dictVars={"sPlotDirectory": "Plot", "sFigureType": "pdf"},
        )
    assert dictResult == {}
    mockSave.assert_not_called()


# ---------------------------------------------------------------
# _fdictStatBatch: empty listPaths early return (line 721)
# ---------------------------------------------------------------


def test_fdictStatBatch_empty_paths_returns_empty():
    mockDocker = MagicMock()
    dictResult = _fdictStatBatch(mockDocker, "cid", [])
    assert dictResult == {}
    mockDocker.ftResultExecuteCommand.assert_not_called()


def test_fdictStatBatch_parses_stat_output():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0, "/ws/a.dat 123\n/ws/b.dat 456\n",
    )
    dictResult = _fdictStatBatch(
        mockDocker, "cid", ["/ws/a.dat", "/ws/b.dat"],
    )
    assert dictResult == {
        "/ws/a.dat": "123",
        "/ws/b.dat": "456",
    }


def test_fdictStatBatch_skips_malformed_lines():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0, "malformed_line_no_space\n/ws/good.dat 999\n\n",
    )
    dictResult = _fdictStatBatch(
        mockDocker, "cid", ["/ws/good.dat"],
    )
    assert dictResult == {"/ws/good.dat": "999"}


# ---------------------------------------------------------------
# _fbAnyMtimeNewerThan: sanity
# ---------------------------------------------------------------


def test_fbAnyMtimeNewerThan_picks_up_newer():
    assert _fbAnyMtimeNewerThan(
        ["/a", "/b"], {"/a": "1", "/b": "100"}, iThreshold=50,
    )


def test_fbAnyMtimeNewerThan_missing_entries_ignored():
    assert not _fbAnyMtimeNewerThan(
        ["/missing"], {}, iThreshold=50,
    )


def test_fbAnyMtimeNewerThan_all_below_threshold():
    assert not _fbAnyMtimeNewerThan(
        ["/a"], {"/a": "5"}, iThreshold=50,
    )
