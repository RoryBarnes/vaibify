"""Tests for timestamp-based pencil staleness in fileStatusManager."""

from vaibify.gui.fileStatusManager import (
    _fbStepIsPencilStale,
    _fdictBuildScriptStatus,
    _fdictComputeMarkerMtimeByStep,
    _fdictComputeMaxDataMtimeByStep,
    fnCollectMarkerPathsByStep,
    fnCollectScriptPathsByStep,
    fsMarkerNameFromStepDirectory,
)


_S_FRESH = "2026-04-20 00:00:00 UTC"  # epoch ~1.77e9 (later)
_S_STALE = "2020-01-01 00:00:00 UTC"  # epoch ~1.58e9 (earlier)
_I_NEWER = 1900000000                  # mtime after both timestamps
_I_OLDER = 1500000000                  # mtime before both timestamps


def _fdictBuildStep(dictOverrides=None):
    dictStep = {
        "sDirectory": "/ws/stepA",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "saSetupCommands": [],
        "saCommands": [],
        "saDataFiles": ["data.out"],
        "saPlotFiles": ["figure.pdf"],
        "dictVerification": {},
    }
    if dictOverrides:
        dictStep.update(dictOverrides)
    return dictStep


def _tBuildScriptPaths(dictStep):
    dictWorkflow = {"listSteps": [dictStep]}
    return fnCollectScriptPathsByStep(dictWorkflow)[0]


def test_unset_validators_never_stale():
    dictStep = _fdictBuildStep()
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {"/ws/stepA/data.py": _I_NEWER},
    )
    assert bStale is False
    assert listStale == []


def test_marker_newer_than_artifacts_not_stale():
    dictStep = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_FRESH},
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/data.py": _I_OLDER,
            "/ws/stepA/plot.py": _I_OLDER,
            "/ws/stepA/data.out": _I_OLDER,
            "/ws/stepA/figure.pdf": _I_OLDER,
        },
        iMarkerMtime=_I_NEWER,
    )
    assert bStale is False
    assert listStale == []


def test_marker_older_than_data_script_is_stale():
    dictStep = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_FRESH},
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/data.py": _I_NEWER,
            "/ws/stepA/plot.py": _I_OLDER,
            "/ws/stepA/data.out": _I_OLDER,
            "/ws/stepA/figure.pdf": _I_OLDER,
        },
        iMarkerMtime=_I_OLDER,
    )
    assert bStale is True
    assert any(
        d["sValidator"] == "test"
        and d["sCategory"] == "dataScript"
        and d["sPath"] == "/ws/stepA/data.py"
        for d in listStale
    )


def test_marker_older_than_data_file_is_stale():
    dictStep = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_FRESH},
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/data.py": _I_OLDER,
            "/ws/stepA/plot.py": _I_OLDER,
            "/ws/stepA/data.out": _I_NEWER,
            "/ws/stepA/figure.pdf": _I_OLDER,
        },
        iMarkerMtime=_I_OLDER,
    )
    assert bStale is True
    assert any(
        d["sValidator"] == "test"
        and d["sCategory"] == "dataFile"
        and d["sPath"] == "/ws/stepA/data.out"
        for d in listStale
    )


def test_marker_none_short_circuits_test_branch():
    dictStep = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_FRESH},
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/data.py": _I_OLDER,
            "/ws/stepA/data.out": _I_OLDER,
            "/ws/stepA/figure.pdf": _I_OLDER,
            "/ws/stepA/plot.py": _I_OLDER,
        },
        iMarkerMtime=None,
    )
    assert bStale is False
    assert listStale == []


def test_marker_none_but_user_stale_still_fires():
    dictStep = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_STALE},
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/figure.pdf": _I_NEWER,
            "/ws/stepA/data.out": _I_OLDER,
            "/ws/stepA/data.py": _I_OLDER,
            "/ws/stepA/plot.py": _I_OLDER,
        },
        iMarkerMtime=None,
    )
    assert bStale is True
    assert any(
        d["sValidator"] == "user" and d["sCategory"] == "plotFile"
        for d in listStale
    )


def test_stale_user_vs_plot_file_reports_plotfile():
    dictStep = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_STALE},
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/data.out", "/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/data.py": _I_OLDER,
            "/ws/stepA/plot.py": _I_OLDER,
            "/ws/stepA/data.out": _I_OLDER,
            "/ws/stepA/figure.pdf": _I_NEWER,
        },
        iMarkerMtime=_I_NEWER,
    )
    assert bStale is True
    assert any(
        d["sValidator"] == "user"
        and d["sCategory"] == "plotFile"
        for d in listStale
    )


def test_plot_only_step_not_stale_by_test_branch():
    dictStep = _fdictBuildStep({
        "saDataCommands": [],
        "saDataFiles": [],
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, _listStale = _fbStepIsPencilStale(
        dictStep, dictScripts,
        ["/ws/stepA/figure.pdf"],
        {
            "/ws/stepA/plot.py": _I_NEWER,
            "/ws/stepA/figure.pdf": _I_NEWER,
        },
        iMarkerMtime=_I_OLDER,
    )
    assert bStale is False


def test_plot_only_step_unset_user_not_stale():
    dictStep = _fdictBuildStep({
        "saDataCommands": [],
        "saDataFiles": [],
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, _listStale = _fbStepIsPencilStale(
        dictStep, dictScripts, ["/ws/stepA/figure.pdf"],
        {"/ws/stepA/figure.pdf": _I_NEWER},
    )
    assert bStale is False


def test_setup_and_generic_commands_counted_as_data_scripts():
    dictStep = _fdictBuildStep({
        "saDataCommands": [],
        "saPlotCommands": [],
        "saSetupCommands": ["python setup.py"],
        "saCommands": ["python misc.py"],
        "saDataFiles": [],
        "saPlotFiles": [],
    })
    dictScripts = _tBuildScriptPaths(dictStep)
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictScripts, [],
        {
            "/ws/stepA/setup.py": _I_NEWER,
            "/ws/stepA/misc.py": _I_NEWER,
        },
        iMarkerMtime=_I_OLDER,
    )
    assert bStale is True
    setStalePaths = {d["sPath"] for d in listStale}
    assert "/ws/stepA/setup.py" in setStalePaths
    assert "/ws/stepA/misc.py" in setStalePaths
    for d in listStale:
        assert d["sCategory"] == "dataScript"


def test_fdictBuildScriptStatus_empty_workflow():
    dictResult = _fdictBuildScriptStatus({"listSteps": []}, {})
    assert dictResult == {}


def test_fdictBuildScriptStatus_reports_per_step():
    dictStepFresh = _fdictBuildStep({
        "dictVerification": {"sLastUserUpdate": _S_FRESH},
    })
    dictStepStale = _fdictBuildStep({
        "sDirectory": "/ws/stepB",
        "saDataCommands": ["python proc.py"],
        "saPlotCommands": [],
        "saSetupCommands": [],
        "saCommands": [],
        "saDataFiles": [],
        "saPlotFiles": [],
    })
    dictWorkflow = {"listSteps": [dictStepFresh, dictStepStale]}
    dictModTimes = {
        "/ws/stepA/data.py": _I_OLDER,
        "/ws/stepA/plot.py": _I_OLDER,
        "/ws/stepA/data.out": _I_OLDER,
        "/ws/stepA/figure.pdf": _I_OLDER,
        "/ws/stepB/proc.py": _I_NEWER,
    }
    dictResult = _fdictBuildScriptStatus(
        dictWorkflow, dictModTimes,
        dictMarkerMtimeByStep={
            "0": str(_I_NEWER),
            "1": str(_I_OLDER),
        },
    )
    assert dictResult[0]["sStatus"] == "unchanged"
    assert dictResult[0]["listStaleArtifacts"] == []
    assert dictResult[1]["sStatus"] == "modified"
    assert len(dictResult[1]["listStaleArtifacts"]) >= 1


# ---------------------------------------------------------------
# _fdictComputeMaxDataMtimeByStep
# ---------------------------------------------------------------


def test_compute_max_data_mtime_single_step():
    dictStep = _fdictBuildStep()
    dictWorkflow = {"listSteps": [dictStep]}
    dictResult = _fdictComputeMaxDataMtimeByStep(
        dictWorkflow, {"/ws/stepA/data.out": 1200},
    )
    assert dictResult == {"0": "1200"}


def test_compute_max_data_mtime_ignores_plot_files():
    dictStep = _fdictBuildStep()
    dictWorkflow = {"listSteps": [dictStep]}
    dictResult = _fdictComputeMaxDataMtimeByStep(
        dictWorkflow,
        {
            "/ws/stepA/data.out": 1000,
            "/ws/stepA/figure.pdf": 9999,
        },
    )
    assert dictResult == {"0": "1000"}


def test_compute_max_data_mtime_picks_max_of_multiple():
    dictStep = _fdictBuildStep({
        "saDataFiles": ["a.dat", "b.dat", "c.dat"],
    })
    dictWorkflow = {"listSteps": [dictStep]}
    dictResult = _fdictComputeMaxDataMtimeByStep(
        dictWorkflow,
        {
            "/ws/stepA/a.dat": 100,
            "/ws/stepA/b.dat": 500,
            "/ws/stepA/c.dat": 300,
        },
    )
    assert dictResult == {"0": "500"}


def test_compute_max_data_mtime_omits_step_without_files():
    dictStep = _fdictBuildStep({"saDataFiles": []})
    dictWorkflow = {"listSteps": [dictStep]}
    dictResult = _fdictComputeMaxDataMtimeByStep(dictWorkflow, {})
    assert dictResult == {}


# ---------------------------------------------------------------
# _fdictComputeMarkerMtimeByStep
# ---------------------------------------------------------------


def test_compute_marker_mtime_present():
    sStepDir = "/workspace/stepA"
    sMarkerPath = (
        "/workspace/.vaibify/test_markers/"
        + fsMarkerNameFromStepDirectory(sStepDir)
    )
    dictPathsByStep = {0: sMarkerPath}
    dictResult = _fdictComputeMarkerMtimeByStep(
        dictPathsByStep, {sMarkerPath: "1750000000"},
    )
    assert dictResult == {"0": "1750000000"}


def test_compute_marker_mtime_absent_omits_step():
    dictPathsByStep = {
        0: "/workspace/.vaibify/test_markers/missing.json",
    }
    assert _fdictComputeMarkerMtimeByStep(dictPathsByStep, {}) == {}


def test_collect_marker_paths_uses_helper():
    dictWorkflow = {
        "listSteps": [
            {"sDirectory": "stepA"},
            {"sDirectory": ""},
            {"sDirectory": "stepB"},
        ],
    }
    dictResult = fnCollectMarkerPathsByStep(
        dictWorkflow, "/workspace/DemoRepo",
    )
    assert dictResult[0] == (
        "/workspace/DemoRepo/.vaibify/test_markers/stepA.json"
    )
    assert 1 not in dictResult
    assert dictResult[2] == (
        "/workspace/DemoRepo/.vaibify/test_markers/stepB.json"
    )


def test_collect_marker_paths_empty_repo_returns_empty():
    dictWorkflow = {"listSteps": [{"sDirectory": "stepA"}]}
    assert fnCollectMarkerPathsByStep(dictWorkflow, "") == {}


def test_marker_name_sanitizes_paths():
    assert fsMarkerNameFromStepDirectory("/a/b/c") == "a_b_c.json"
    assert fsMarkerNameFromStepDirectory(
        "step01/") == "step01.json"
