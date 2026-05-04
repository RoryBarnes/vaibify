"""Tests for uncovered branches in fileStatusManager."""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui.fileStatusManager import (
    _fbAnyMtimeNewerThan,
    _fbPlotNewerThanUserVerification,
    _fdictBuildScriptStatus,
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


# ---------------------------------------------------------------
# Wire-format: listModifiedFiles is always repo-relative.
# ---------------------------------------------------------------


def test_fnInvalidateStepFiles_writes_repo_relative_paths():
    """The persisted listModifiedFiles must use repo-relative keys."""
    dictStep = {
        "saDataFiles": [],
        "saPlotFiles": [],
        "dictVerification": {},
    }
    _fnInvalidateStepFiles(
        dictStep,
        ["/workspace/proj/sub/a.dat"],
        dictModTimes={"/workspace/proj/sub/a.dat": "100"},
        sRepoRoot="/workspace/proj",
    )
    assert dictStep["dictVerification"]["listModifiedFiles"] == [
        "sub/a.dat",
    ]


def test_fnInvalidateStepFiles_two_files_same_step_both_match():
    """A09 repro: two changed files in sibling subdirs both stored."""
    dictStep = {
        "saDataFiles": [
            "EngleBarnes/output/Converged_Param_Dictionary.json",
            "RibasBarnes/output/Converged_Param_Dictionary.json",
        ],
        "saPlotFiles": [],
        "dictVerification": {},
    }
    _fnInvalidateStepFiles(
        dictStep,
        [
            "/workspace/proj/EngleBarnes/output/"
            "Converged_Param_Dictionary.json",
            "/workspace/proj/RibasBarnes/output/"
            "Converged_Param_Dictionary.json",
        ],
        dictModTimes={},
        sRepoRoot="/workspace/proj",
    )
    listMod = dictStep["dictVerification"]["listModifiedFiles"]
    assert listMod == [
        "EngleBarnes/output/Converged_Param_Dictionary.json",
        "RibasBarnes/output/Converged_Param_Dictionary.json",
    ]


def test_fnInvalidateStepFiles_dedupes_existing_abs_with_new_rel():
    """If an old abs entry survives, it merges with the new rel form."""
    dictStep = {
        "saDataFiles": [],
        "saPlotFiles": [],
        "dictVerification": {
            "listModifiedFiles": [
                "/workspace/proj/sub/a.dat",  # legacy abs.
            ],
        },
    }
    _fnInvalidateStepFiles(
        dictStep,
        ["/workspace/proj/sub/a.dat"],
        dictModTimes={},
        sRepoRoot="/workspace/proj",
    )
    assert dictStep["dictVerification"]["listModifiedFiles"] == [
        "sub/a.dat",
    ]


def test_fnInvalidateStepFiles_no_repo_root_keeps_paths():
    """Pre-repo workflows pass empty sRepoRoot; behavior unchanged."""
    dictStep = {
        "saDataFiles": [],
        "saPlotFiles": [],
        "dictVerification": {},
    }
    _fnInvalidateStepFiles(
        dictStep,
        ["/ws/sub/a.dat"],
        dictModTimes={},
        sRepoRoot="",
    )
    assert dictStep["dictVerification"]["listModifiedFiles"] == [
        "/ws/sub/a.dat",
    ]


def test_flistDetectAndInvalidate_threads_repo_root_into_persisted_list():
    """End-to-end: detect a change and persist a repo-relative entry."""
    mockSave = MagicMock()
    dictCtx = {
        "save": mockSave,
        "dictPreviousModTimes": {
            "cid": {"/workspace/proj/step0/out.dat": "100"},
        },
    }
    dictStep = {
        "sDirectory": "step0",
        "saDataFiles": ["out.dat"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
        "dictVerification": {"sUnitTest": "passed"},
    }
    dictWorkflow = {
        "listSteps": [dictStep],
        "sProjectRepoPath": "/workspace/proj",
    }
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        _flistDetectAndInvalidate(
            dictCtx, "cid", dictWorkflow,
            {"/workspace/proj/step0/out.dat": "200"},
            dictVars={
                "sPlotDirectory": "Plot",
                "sFigureType": "pdf",
                "sRepoRoot": "/workspace/proj",
            },
        )
    listMod = dictStep["dictVerification"]["listModifiedFiles"]
    assert listMod == ["step0/out.dat"]
    mockSave.assert_called_once_with("cid", dictWorkflow)


# ---------------------------------------------------------------
# Workflow loader migration of legacy listModifiedFiles
# ---------------------------------------------------------------


def test_fbMigrateModifiedFilesToRepoRelative_converts_abs_to_rel():
    from vaibify.gui.workflowManager import (
        fbMigrateModifiedFilesToRepoRelative,
    )
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/proj",
        "listSteps": [
            {
                "dictVerification": {
                    "listModifiedFiles": [
                        "/workspace/proj/dir/a.dat",
                        "/workspace/proj/dir/b.dat",
                    ],
                },
            },
        ],
    }
    bChanged = fbMigrateModifiedFilesToRepoRelative(dictWorkflow)
    assert bChanged is True
    assert dictWorkflow["listSteps"][0][
        "dictVerification"]["listModifiedFiles"] == [
        "dir/a.dat", "dir/b.dat",
    ]


def test_fbMigrateModifiedFilesToRepoRelative_idempotent():
    from vaibify.gui.workflowManager import (
        fbMigrateModifiedFilesToRepoRelative,
    )
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/proj",
        "listSteps": [
            {
                "dictVerification": {
                    "listModifiedFiles": ["dir/a.dat"],
                },
            },
        ],
    }
    bChanged = fbMigrateModifiedFilesToRepoRelative(dictWorkflow)
    assert bChanged is False


def test_fbMigrateModifiedFilesToRepoRelative_skips_steps_without_list():
    from vaibify.gui.workflowManager import (
        fbMigrateModifiedFilesToRepoRelative,
    )
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/proj",
        "listSteps": [
            {"dictVerification": {}},
            {},
        ],
    }
    bChanged = fbMigrateModifiedFilesToRepoRelative(dictWorkflow)
    assert bChanged is False


def test_fbMigrateModifiedFilesToRepoRelative_no_repo_root_keeps_paths():
    from vaibify.gui.workflowManager import (
        fbMigrateModifiedFilesToRepoRelative,
    )
    dictWorkflow = {
        "listSteps": [
            {
                "dictVerification": {
                    "listModifiedFiles": ["/abs/a.dat"],
                },
            },
        ],
    }
    bChanged = fbMigrateModifiedFilesToRepoRelative(dictWorkflow)
    # No root, no normalization possible -> unchanged.
    assert bChanged is False
    assert dictWorkflow["listSteps"][0][
        "dictVerification"]["listModifiedFiles"] == ["/abs/a.dat"]


# ---------------------------------------------------------------
# Edge-case geometry for the path-contract wire format.
# ---------------------------------------------------------------


def test_fnInvalidateStepFiles_step_directory_dot_normalizes_to_basename():
    """A step at the repo root (sDirectory='.') stores bare filenames."""
    dictStep = {
        "sDirectory": ".",
        "saDataFiles": ["summary.csv"],
        "saPlotFiles": [],
        "dictVerification": {},
    }
    _fnInvalidateStepFiles(
        dictStep,
        ["/workspace/proj/summary.csv"],
        dictModTimes={"/workspace/proj/summary.csv": "1"},
        sRepoRoot="/workspace/proj",
    )
    assert dictStep["dictVerification"]["listModifiedFiles"] == [
        "summary.csv",
    ]


def test_fnInvalidateStepFiles_sibling_directory_plot_path():
    """A plot file living in a sibling directory under the repo stores
    the full repo-relative path, not just the basename."""
    dictStep = {
        "sDirectory": "stepOne",
        "saDataFiles": [],
        "saPlotFiles": ["../shared/figure.pdf"],
        "dictVerification": {},
    }
    _fnInvalidateStepFiles(
        dictStep,
        ["/workspace/proj/shared/figure.pdf"],
        dictModTimes={"/workspace/proj/shared/figure.pdf": "5"},
        sRepoRoot="/workspace/proj",
    )
    assert dictStep["dictVerification"]["listModifiedFiles"] == [
        "shared/figure.pdf",
    ]


def test_fnInvalidateStepFiles_wire_format_has_no_absolute_paths():
    """No entry written by the invalidator may start with '/' when a
    repo root is supplied — that is the wire-format contract."""
    dictStep = {
        "sDirectory": "stepOne",
        "saDataFiles": ["out.dat"],
        "saPlotFiles": ["plot.pdf"],
        "dictVerification": {},
    }
    _fnInvalidateStepFiles(
        dictStep,
        [
            "/workspace/proj/stepOne/out.dat",
            "/workspace/proj/stepOne/plot.pdf",
        ],
        dictModTimes={},
        sRepoRoot="/workspace/proj",
    )
    for sPath in dictStep["dictVerification"]["listModifiedFiles"]:
        assert not sPath.startswith("/"), (
            f"wire format must be repo-relative, got abs: {sPath}"
        )


# ---------------------------------------------------------------
# Manifest short-circuit: hash-clean steps suppress mtime stale
# ---------------------------------------------------------------


_S_MANIFEST_HEADER = "# SHA-256 manifest of workflow outputs\n"
_S_MANIFEST_FILENAME = "MANIFEST.sha256"


def _fsSha256(sContent):
    """Return SHA-256 hex digest of ``sContent`` encoded UTF-8."""
    return hashlib.sha256(sContent.encode("utf-8")).hexdigest()


def test_step_status_unchanged_when_mtime_stale_but_hash_clean(tmp_path):
    """A fresh-clone-like state (new mtimes, matching content) is unchanged."""
    sContent = "alpha,beta\n1,2\n"
    sRepoRoot = str(tmp_path)
    sStepDir = posixJoin(sRepoRoot, "stepOne")
    pathFile = tmp_path / "stepOne" / "out.csv"
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_text(sContent)
    pathManifest = tmp_path / _S_MANIFEST_FILENAME
    pathManifest.write_text(
        _S_MANIFEST_HEADER + f"{_fsSha256(sContent)}  stepOne/out.csv\n"
    )
    sAbsPath = str(pathFile)
    dictStep = {
        "sName": "OnlyStep",
        "sDirectory": sStepDir,
        "saDataFiles": ["out.csv"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
        "dictVerification": {
            "sUser": "passed",
            "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
        },
    }
    dictWorkflow = {
        "listSteps": [dictStep],
        "sProjectRepoPath": sRepoRoot,
    }
    dictModTimes = {sAbsPath: "1900000000"}
    dictVars = {
        "sPlotDirectory": "Plot", "sFigureType": "pdf",
        "sRepoRoot": sRepoRoot,
    }
    dictResult = _fdictBuildScriptStatus(
        dictWorkflow, dictModTimes, dictVars,
    )
    assert dictResult[0]["sStatus"] == "unchanged"


def test_step_status_modified_when_mtime_stale_and_hash_drifted(tmp_path):
    """When current content drifted from the manifest, the step stays stale."""
    sOriginal = "alpha,beta\n1,2\n"
    sCurrent = "alpha,beta\n9,9\n"
    sRepoRoot = str(tmp_path)
    sStepDir = posixJoin(sRepoRoot, "stepOne")
    pathFile = tmp_path / "stepOne" / "out.csv"
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_text(sCurrent)
    # Manifest pins the original content; current content drifted.
    pathManifest = tmp_path / _S_MANIFEST_FILENAME
    pathManifest.write_text(
        _S_MANIFEST_HEADER + f"{_fsSha256(sOriginal)}  stepOne/out.csv\n"
    )
    sAbsPath = str(pathFile)
    dictStep = {
        "sName": "OnlyStep",
        "sDirectory": sStepDir,
        "saDataFiles": ["out.csv"],
        "saPlotFiles": [],
        "saDataCommands": [],
        "saPlotCommands": [],
        "dictVerification": {
            "sUser": "passed",
            "sLastUserUpdate": "2020-01-01 00:00:00 UTC",
        },
    }
    dictWorkflow = {
        "listSteps": [dictStep],
        "sProjectRepoPath": sRepoRoot,
    }
    dictModTimes = {sAbsPath: "1900000000"}
    dictVars = {
        "sPlotDirectory": "Plot", "sFigureType": "pdf",
        "sRepoRoot": sRepoRoot,
    }
    dictResult = _fdictBuildScriptStatus(
        dictWorkflow, dictModTimes, dictVars,
    )
    assert dictResult[0]["sStatus"] == "modified"


def posixJoin(*saParts):
    """Join path parts using forward slashes (manifest paths are POSIX)."""
    return "/".join(part.rstrip("/") for part in saParts if part)


# ---------------------------------------------------------------
# Corrupt workflow.json: defensive handling of None / non-dict steps
# ---------------------------------------------------------------


def test_fbAllStepsFullyVerified_returns_false_on_none_step():
    """A workflow with a None step must not crash with AttributeError."""
    from vaibify.gui.fileStatusManager import fbAllStepsFullyVerified
    dictWorkflow = {"listSteps": [None]}
    assert fbAllStepsFullyVerified(dictWorkflow) is False


def test_fbAllStepsFullyVerified_returns_false_on_string_step():
    """A workflow with a non-dict step must not crash."""
    from vaibify.gui.fileStatusManager import fbAllStepsFullyVerified
    dictWorkflow = {"listSteps": ["corrupt"]}
    assert fbAllStepsFullyVerified(dictWorkflow) is False


def test_fbAllStepsFullyVerified_mixed_none_and_valid_returns_false():
    """Even one corrupt entry blocks the all-green claim."""
    from vaibify.gui.fileStatusManager import fbAllStepsFullyVerified
    dictGoodStep = {
        "dictVerification": {"sUser": "passed"},
    }
    dictWorkflow = {"listSteps": [dictGoodStep, None]}
    assert fbAllStepsFullyVerified(dictWorkflow) is False


def test_fbIsStepFullyVerified_returns_false_on_none_dict_verification():
    """A non-dict dictVerification on a step must not crash."""
    from vaibify.gui.fileStatusManager import fbIsStepFullyVerified
    dictStep = {"dictVerification": None}
    assert fbIsStepFullyVerified(dictStep) is False


def test_fbIsStepFullyVerified_returns_false_on_non_dict_step():
    """A non-dict step must not crash with AttributeError."""
    from vaibify.gui.fileStatusManager import fbIsStepFullyVerified
    assert fbIsStepFullyVerified(None) is False
    assert fbIsStepFullyVerified("oops") is False
    assert fbIsStepFullyVerified(42) is False
