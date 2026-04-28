"""Tests for the workflow-level unseeded-randomness lint."""

from vaibify.gui.randomnessLint import (
    fbStepHasUnseededRandomness,
    flistConfigFilesForStep,
    fnApplyRandomnessLintToWorkflow,
)


def _fdictMakeStep(sCmd):
    return {
        "sName": "Sweep", "sDirectory": "/repo/sweep",
        "saSetupCommands": [sCmd], "saDataCommands": [], "saCommands": [],
    }


def _fnReadFromMap(dictMap):
    def fnReadFile(sPath):
        return dictMap.get(sPath, "")
    return fnReadFile


def test_lint_flags_step_when_seed_missing():
    dictStep = _fdictMakeStep("vspace vspace.in")
    dictLint = {"sConfigGlob": "*.in", "sSeedRegex": r"^seed\s+\d+"}
    fnReadFile = _fnReadFromMap({
        "/repo/sweep/vspace.in": "name foo\nbody bar\n",
    })
    bWarn = fbStepHasUnseededRandomness(
        dictStep, "/repo/sweep", dictLint, fnReadFile,
    )
    assert bWarn is True


def test_lint_passes_step_when_seed_present():
    dictStep = _fdictMakeStep("vspace vspace.in")
    dictLint = {"sConfigGlob": "*.in", "sSeedRegex": r"^seed\s+\d+"}
    fnReadFile = _fnReadFromMap({
        "/repo/sweep/vspace.in": "seed 12345\nname foo\n",
    })
    bWarn = fbStepHasUnseededRandomness(
        dictStep, "/repo/sweep", dictLint, fnReadFile,
    )
    assert bWarn is False


def test_lint_no_op_without_dictRandomnessLint():
    dictStep = _fdictMakeStep("vspace vspace.in")
    bWarn = fbStepHasUnseededRandomness(
        dictStep, "/repo/sweep", None, _fnReadFromMap({}),
    )
    assert bWarn is False


def test_lint_no_op_when_glob_missing():
    dictStep = _fdictMakeStep("vspace vspace.in")
    bWarn = fbStepHasUnseededRandomness(
        dictStep, "/repo/sweep",
        {"sSeedRegex": r"^seed\s+\d+"},
        _fnReadFromMap({}),
    )
    assert bWarn is False


def test_flistConfigFilesForStep_extracts_relative_path():
    dictStep = _fdictMakeStep("vspace vspace.in --quiet")
    listFiles = flistConfigFilesForStep(
        dictStep, "/repo/sweep", "*.in",
    )
    assert listFiles == ["/repo/sweep/vspace.in"]


def test_flistConfigFilesForStep_empty_when_no_match():
    dictStep = _fdictMakeStep("python run.py output.csv")
    listFiles = flistConfigFilesForStep(
        dictStep, "/repo/sweep", "*.in",
    )
    assert listFiles == []


def test_flistConfigFilesForStep_absolute_path_preserved():
    dictStep = _fdictMakeStep("vspace /etc/vspace.in")
    listFiles = flistConfigFilesForStep(
        dictStep, "/repo/sweep", "*.in",
    )
    assert listFiles == ["/etc/vspace.in"]


def test_apply_lint_sets_flag_on_unseeded_step():
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "dictRandomnessLint": {
            "sConfigGlob": "*.in", "sSeedRegex": r"^seed\s+\d+",
        },
        "listSteps": [_fdictMakeStep("vspace vspace.in")],
    }
    dictWorkflow["listSteps"][0]["sDirectory"] = "sweep"
    fnReadFile = _fnReadFromMap({
        "/repo/sweep/vspace.in": "name foo\n",
    })
    fnApplyRandomnessLintToWorkflow(dictWorkflow, fnReadFile)
    assert (
        dictWorkflow["listSteps"][0]["dictVerification"][
            "bUnseededRandomnessWarning"]
        is True
    )


def test_lint_invalid_regex_triggers_warning_not_silent_pass(caplog):
    """A malformed sSeedRegex must not silently mark every file as seeded.

    Regression: previously returned True on re.error, which hid both
    the config typo and any real unseeded randomness. Now returns
    False so the yellow badge appears.
    """
    import logging

    dictStep = _fdictMakeStep("vspace vspace.in")
    dictLint = {"sConfigGlob": "*.in", "sSeedRegex": r"^seed[\s+\d+"}
    fnReadFile = _fnReadFromMap({
        "/repo/sweep/vspace.in": "seed 42\nname foo\n",
    })
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        bWarn = fbStepHasUnseededRandomness(
            dictStep, "/repo/sweep", dictLint, fnReadFile,
        )
    assert bWarn is True
    assert any(
        "Invalid sSeedRegex" in rec.message for rec in caplog.records
    )


def test_apply_lint_clears_flag_when_seed_added():
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "dictRandomnessLint": {
            "sConfigGlob": "*.in", "sSeedRegex": r"^seed\s+\d+",
        },
        "listSteps": [_fdictMakeStep("vspace vspace.in")],
    }
    dictWorkflow["listSteps"][0]["sDirectory"] = "sweep"
    dictWorkflow["listSteps"][0]["dictVerification"] = {
        "bUnseededRandomnessWarning": True,
    }
    fnReadFile = _fnReadFromMap({
        "/repo/sweep/vspace.in": "seed 42\n",
    })
    fnApplyRandomnessLintToWorkflow(dictWorkflow, fnReadFile)
    assert (
        "bUnseededRandomnessWarning"
        not in dictWorkflow["listSteps"][0]["dictVerification"]
    )
