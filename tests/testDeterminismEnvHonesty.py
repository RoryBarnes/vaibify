"""Determinism-injection honesty and SVG salt coverage.

Two guarantees are asserted here.

**Degradation is recorded, never swallowed.** ``_fsBuildDeterminismEnvPrefix``
returns an empty prefix when the project repo has no reachable HEAD
commit, and by contract it must not block the run. A step that ran
without the prefix therefore produces nondeterministic output with no
diagnostic unless the skip is written down. The run records
``bDeterminismEnvApplied`` in the step's ``dictRunStats`` (the existing
per-step state channel, persisted into ``project.json``) and emits a
warning line into the run log. Absence of the key means *unknown*, not
*clean* — legacy stats predate the flag and must not be graded as
deterministic.

**The matplotlib SVG salt is pinned.** ``SOURCE_DATE_EPOCH`` alone
fixes the SVG ``<dc:date>`` but not the element ids, which matplotlib
derives from ``rcParams["svg.hashsalt"]`` and defaults to a fresh
``uuid4`` per process. The prefix pins the salt to the same HEAD commit
epoch, so SVG joins PNG/PDF/EPS/PS as byte-reproducible.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui.pipelineRunner import (
    S_DETERMINISM_APPLIED_KEY,
    S_ENV_PREFIX_KEY,
    _fiExecuteAndRecord,
    _fnInjectDeterminismEnvPrefix,
    _fsBuildDeterminismEnvPrefix,
    _ftPrepareLogAndVariables,
)
from vaibify.gui.pipelineUtils import _fnRecordRunStats


I_EPOCH = 1745798400
I_OTHER_EPOCH = 1745798401


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a mock Docker connection whose git query returns sOutput."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (iExitCode, sOutput)
    mockDocker.fnWriteFile = MagicMock()
    return mockDocker


def _fMockCallback():
    """Return an async callback that captures events."""
    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    return fnCallback, listCaptured


# -----------------------------------------------------------------------
# Gap 1: a skipped determinism prefix must leave a trace
# -----------------------------------------------------------------------


def test_inject_marks_determinism_applied_when_epoch_available():
    """A usable HEAD epoch records the guarantee as honoured."""
    dictVariables = {}
    _fnRunAsync(_fnInjectDeterminismEnvPrefix(
        _fMockDocker(0, f"{I_EPOCH}\n"), "cid",
        {"sProjectRepoPath": "/workspace/repo"}, dictVariables,
    ))
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is True


def test_inject_marks_determinism_skipped_when_git_query_fails():
    """A failed git query records the guarantee as NOT honoured."""
    dictVariables = {}
    _fnRunAsync(_fnInjectDeterminismEnvPrefix(
        _fMockDocker(128, ""), "cid",
        {"sProjectRepoPath": "/workspace/repo"}, dictVariables,
    ))
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is False


def test_inject_marks_skipped_even_when_slug_export_is_present():
    """The slug export must not be mistaken for the determinism prefix.

    A non-empty ``S_ENV_PREFIX_KEY`` is not evidence the epoch was
    exported: the workflow-slug export is appended unconditionally.
    """
    dictVariables = {}
    _fnRunAsync(_fnInjectDeterminismEnvPrefix(
        _fMockDocker(128, ""), "cid",
        {
            "sProjectRepoPath": "/workspace/repo",
            "sPath": "/workspace/repo/.vaibify/workflows/wfa.json",
        },
        dictVariables,
    ))
    assert dictVariables[S_ENV_PREFIX_KEY] != ""
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is False


def _ftRunStepWithVariables(dictVariables):
    """Drive ``_fiExecuteAndRecord`` once, returning the step's run stats."""
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {"sDirectory": "/ws/step"}
    with patch(
        "vaibify.gui.pipelineRunner.ftRunStepCommands",
        new=AsyncMock(return_value=(0, 1.0)),
    ), patch(
        "vaibify.gui.pipelineRunner._fsetSnapshotDirectory",
        new=AsyncMock(return_value=set()),
    ), patch(
        "vaibify.gui.pipelineRunner._fnEmitDiscoveredOutputs",
        new=AsyncMock(),
    ), patch(
        "vaibify.gui.pipelineRunner._fnRecordRemoteDataProvenance",
        new=AsyncMock(),
    ), patch(
        "vaibify.gui.workflowManager.fnCleanStepScratchDirs",
        new=MagicMock(),
    ):
        _fnRunAsync(_fiExecuteAndRecord(
            _fMockDocker(), "cid", dictStep,
            1, "/ws", dictVariables, fnCallback,
        ))
    return dictStep["dictRunStats"], listCaptured


def test_run_stats_record_determinism_skip():
    """A step run without the epoch records the skip in its run stats."""
    dictRunStats, _listCaptured = _ftRunStepWithVariables({
        S_ENV_PREFIX_KEY: "",
        S_DETERMINISM_APPLIED_KEY: False,
    })
    assert dictRunStats["bDeterminismEnvApplied"] is False


def test_run_stats_record_determinism_applied():
    """A step run with the epoch records the guarantee as honoured."""
    dictRunStats, _listCaptured = _ftRunStepWithVariables({
        S_ENV_PREFIX_KEY: f"export SOURCE_DATE_EPOCH={I_EPOCH} && ",
        S_DETERMINISM_APPLIED_KEY: True,
    })
    assert dictRunStats["bDeterminismEnvApplied"] is True


def test_run_stats_flag_rides_the_step_stats_event():
    """The dashboard's stepStats event carries the flag, not just disk."""
    _dictRunStats, listCaptured = _ftRunStepWithVariables({
        S_DETERMINISM_APPLIED_KEY: False,
    })
    listStats = [
        d for d in listCaptured if d.get("sType") == "stepStats"
    ]
    assert listStats
    assert listStats[0]["dictRunStats"]["bDeterminismEnvApplied"] is False


def test_unknown_determinism_is_not_recorded_as_applied():
    """No information means the key is absent — never a silent True.

    Run stats written before this flag existed, and paths where the
    prefix was never computed, must read as *unknown*. Defaulting an
    unknown to ``True`` would grade an unverified run clean.
    """
    dictStep = {}
    _fnRecordRunStats(dictStep, 0.0, 0.0, iExitCode=0)
    assert "bDeterminismEnvApplied" not in dictStep["dictRunStats"]


def test_missing_variable_key_leaves_determinism_unknown():
    """A caller that never injected the prefix records no verdict."""
    dictRunStats, _listCaptured = _ftRunStepWithVariables({})
    assert "bDeterminismEnvApplied" not in dictRunStats


def _ftPrepareWithGitExit(iGitExit):
    """Run ``_ftPrepareLogAndVariables`` with a given git exit code."""
    fnCallback, listCaptured = _fMockCallback()
    mockDocker = _fMockDocker(iGitExit, f"{I_EPOCH}\n" if not iGitExit else "")
    with patch(
        "vaibify.gui.pipelineRunner._fnEnsureLogsDirectory",
        new=AsyncMock(return_value="/ws/.vaibify/logs"),
    ), patch(
        "vaibify.gui.pipelineLogger.fnPruneOldLogs",
        new=AsyncMock(),
    ):
        _sLogPath, listLogLines, _fnLogging, dictVariables = _fnRunAsync(
            _ftPrepareLogAndVariables(
                mockDocker, "cid",
                {
                    "sProjectRepoPath": "/workspace/repo",
                    "sWorkflowName": "wf",
                    "listSteps": [],
                },
                "/workspace/repo/.vaibify/workflows/wf.json",
                fnCallback,
            )
        )
    return listLogLines, listCaptured, dictVariables


def test_degraded_determinism_is_announced_in_the_run_log():
    """The skip reaches the persisted run log, not just the step record."""
    listLogLines, _listCaptured, dictVariables = _ftPrepareWithGitExit(128)
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is False
    assert any(
        "SOURCE_DATE_EPOCH" in sLine for sLine in listLogLines
    ), listLogLines


def test_no_degradation_notice_when_determinism_holds():
    """A healthy run must not cry wolf about determinism."""
    listLogLines, _listCaptured, dictVariables = _ftPrepareWithGitExit(0)
    assert dictVariables[S_DETERMINISM_APPLIED_KEY] is True
    assert not any(
        "SOURCE_DATE_EPOCH" in sLine for sLine in listLogLines
    ), listLogLines


def test_degraded_determinism_does_not_block_the_run():
    """Recording the skip must not raise or abort preparation."""
    listLogLines, _listCaptured, dictVariables = _ftPrepareWithGitExit(128)
    assert dictVariables[S_ENV_PREFIX_KEY] is not None
    assert listLogLines


# -----------------------------------------------------------------------
# Gap 2: SVG element ids need a pinned matplotlib hash salt
# -----------------------------------------------------------------------


def _fsPrefixForEpoch(iEpoch):
    """Return the determinism prefix built from a given HEAD epoch."""
    return _fnRunAsync(_fsBuildDeterminismEnvPrefix(
        _fMockDocker(0, f"{iEpoch}\n"), "cid", "/workspace/repo",
    ))


def test_determinism_prefix_pins_the_svg_hash_salt():
    """Without a pinned salt, matplotlib randomises SVG element ids."""
    sPrefix = _fsPrefixForEpoch(I_EPOCH)
    assert "svg.hashsalt" in sPrefix
    assert str(I_EPOCH) in sPrefix


def test_svg_salt_is_reachable_by_matplotlib_via_its_config_dir():
    """A salt matplotlib never reads is not a fix.

    The salt must be written where matplotlib looks for it, which
    means exporting ``MPLCONFIGDIR`` and creating the directory
    before the step command runs.
    """
    sPrefix = _fsPrefixForEpoch(I_EPOCH)
    assert "MPLCONFIGDIR" in sPrefix
    assert "mkdir -p" in sPrefix


def test_svg_salt_is_stable_across_runs_of_the_same_source():
    """Identical source must yield an identical salt, run after run."""
    assert _fsPrefixForEpoch(I_EPOCH) == _fsPrefixForEpoch(I_EPOCH)


def test_svg_salt_tracks_the_source_it_came_from():
    """A different commit yields a different salt — the salt is derived."""
    assert _fsPrefixForEpoch(I_EPOCH) != _fsPrefixForEpoch(I_OTHER_EPOCH)


def test_no_salt_when_the_epoch_is_unavailable():
    """No epoch means no fabricated salt; the prefix stays empty."""
    sPrefix = _fnRunAsync(_fsBuildDeterminismEnvPrefix(
        _fMockDocker(128, ""), "cid", "/workspace/repo",
    ))
    assert sPrefix == ""


def test_salt_does_not_claim_the_researcher_s_override_slot():
    """The salt is a default, not a clobber.

    Measured against matplotlib 3.5.0: ``MPLCONFIGDIR/matplotlibrc``
    is the lowest-precedence of the three, losing to both a
    working-directory ``matplotlibrc`` and an explicitly exported
    ``MATPLOTLIBRC``. Exporting ``MATPLOTLIBRC`` instead would
    silently discard a researcher's own environment setting, so the
    prefix must not touch it.
    """
    sPrefix = _fsPrefixForEpoch(I_EPOCH)
    assert "MATPLOTLIBRC" not in sPrefix


def test_salt_failure_does_not_break_the_command_chain():
    """A container that cannot host the rc file still runs the step.

    Determinism is best-effort by contract; an unwritable config
    directory must report on stderr, not abort the ``&&`` chain
    before the researcher's command.
    """
    sPrefix = _fsPrefixForEpoch(I_EPOCH)
    assert "|| echo" in sPrefix
    assert ">&2" in sPrefix


def test_env_prefix_is_applied_outside_the_time_wrapper():
    """``/usr/bin/time`` must never be handed ``export`` as its program.

    ``_fsWrapWithTime`` emits ``/usr/bin/time -f FMT <command>``. With
    the env prefix inside, that becomes ``/usr/bin/time -f FMT export
    SOURCE_DATE_EPOCH=... && cmd``: GNU time execs ``export``, exits
    127, and the real command never runs. Measured with a stand-in
    time binary. The base image ships no ``/usr/bin/time``, so the
    bug is dormant — a project adding it via ``systemPackages``
    would wake it.
    """
    from vaibify.gui.pipelineRunner import _ftRunSingleCommand
    mockDocker = _fMockDocker()
    mockDocker.texecRunInContainerStreamedWithChunks.return_value = (
        MagicMock(iExitCode=0, sStdout="", sStderr="")
    )
    fnCallback, _listCaptured = _fMockCallback()
    _fnRunAsync(_ftRunSingleCommand(
        mockDocker, "cid", "python plot.py", "python plot.py",
        "/work", fnCallback,
        sEnvPrefix=f"export SOURCE_DATE_EPOCH={I_EPOCH} && ",
    ))
    sExecuted = mockDocker.texecRunInContainerStreamedWithChunks \
        .call_args[0][1]
    assert sExecuted.index("export SOURCE_DATE_EPOCH") < (
        sExecuted.index("/usr/bin/time")
    ), sExecuted


@pytest.mark.parametrize("iEpoch", [I_EPOCH, I_OTHER_EPOCH])
def test_epoch_and_salt_agree(iEpoch):
    """One derivation, two consumers: the salt IS the exported epoch."""
    sPrefix = _fsPrefixForEpoch(iEpoch)
    assert f"export SOURCE_DATE_EPOCH={iEpoch} " in sPrefix
    assert f"svg.hashsalt: {iEpoch}" in sPrefix
