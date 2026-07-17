"""Tests for the L1 binary-staleness WARNING (non-gating, mtime-based).

The reported scenario: a step is verified and reports L1-clean, then
the binary it depends on is rebuilt. Nothing re-ran, so the outputs now
predate the binary. Unlike the L3 ``binary-drifted`` FAILURE (hash vs
environment.json), this L1 signal is mtime-only and must NEVER drop the
L1 gate — it only raises an orange advisory in the regression column so
the researcher knows the verified state is older than the binary.

The mtime comparison is per-step (a binary newer than THIS step's
outputs), attributed through the same ``flistStepDependedBinaryPaths``
the L3 drift criterion uses, so an implicit dependency declared via
``saBinaryDependencies`` (maxlev invoking vplanet internally) is caught.
"""

from vaibify.reproducibility.levelGates import (
    fdictBinaryStaleByStep,
    fdictComputeStepLevelWarnings,
)


def _fdictWorkflow(listSteps, sBinaryPath="/opt/bin/vplanet"):
    return {
        "sProjectRepoPath": "/repo",
        "listDeclaredBinaries": [{
            "sBinaryPath": sBinaryPath,
            "sPurpose": "forward model",
            "sExpectedVersion": "3.0",
        }],
        "listSteps": listSteps,
    }


def _dictStep(saBinaryDependencies=None, saDataCommands=None):
    return {
        "sName": "MaxLikelihood", "sDirectory": "MaxLev",
        "saDataCommands": saDataCommands or ["maxlev config.in"],
        "saOutputDataFiles": ["MaxLev/out.json"],
        "saBinaryDependencies": saBinaryDependencies or ["vplanet"],
    }


# --------- fdictBinaryStaleByStep: the mtime comparison ---------


def test_binary_newer_than_outputs_is_stale():
    """A binary mtime past the step's newest output mtime → stale."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow,
        {"/opt/bin/vplanet": "200"},   # binary mtime
        {"0": "100"},                   # step 0 output mtime
    )
    assert dictStale == {0: True}


def test_binary_older_than_outputs_is_not_stale():
    """Kills a ``>=``-for-``>`` mutant at the equal boundary, and the
    plain older case: neither is stale."""
    dictWorkflow = _fdictWorkflow([_dictStep(), _dictStep()])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow,
        {"/opt/bin/vplanet": "100"},
        {"0": "100", "1": "200"},       # equal, then newer output
    )
    assert dictStale == {0: False, 1: False}


def test_step_without_outputs_is_never_stale():
    """No produced outputs means nothing to be out of date against."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow, {"/opt/bin/vplanet": "999"}, {},
    )
    assert dictStale == {0: False}


def test_binary_absent_from_mtimes_is_not_stale():
    """A binary the poll could not stat (never built) is skipped, not
    treated as infinitely new."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictStale = fdictBinaryStaleByStep(dictWorkflow, {}, {"0": "100"})
    assert dictStale == {0: False}


def _fdictWorkflowWithTwoBinaries(listSteps):
    """Two declared binaries so the scan must survive a bad first entry."""
    return {
        "sProjectRepoPath": "/repo",
        "listDeclaredBinaries": [
            {"sBinaryPath": "/opt/bin/maxlev", "sPurpose": "estimator",
             "sExpectedVersion": "1.0"},
            {"sBinaryPath": "/opt/bin/vplanet", "sPurpose": "forward model",
             "sExpectedVersion": "3.0"},
        ],
        "listSteps": listSteps,
    }


def test_unstatted_first_binary_does_not_mask_a_stale_second():
    """Skipping one binary must not stop the scan of the rest.

    The first depended binary is absent from the mtime map (the poll
    could not stat it); the second is genuinely newer than the step's
    outputs. The scan must SKIP the first and still flag the second —
    a skip that aborts the whole loop hides real staleness behind any
    one unstattable binary.
    """
    dictStep = _dictStep(saBinaryDependencies=["maxlev", "vplanet"])
    dictWorkflow = _fdictWorkflowWithTwoBinaries([dictStep])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow,
        {"/opt/bin/vplanet": "200"},   # maxlev missing from the map
        {"0": "100"},
    )
    assert dictStale == {0: True}


def test_unparseable_first_mtime_does_not_mask_a_stale_second():
    """A garbage mtime is skipped in place, without aborting the scan.

    The first binary's mtime is unparseable text; the second is newer
    than the outputs. The comparison must swallow the parse failure for
    that one entry (not crash, not stop) and still flag the second.
    """
    dictStep = _dictStep(saBinaryDependencies=["maxlev", "vplanet"])
    dictWorkflow = _fdictWorkflowWithTwoBinaries([dictStep])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow,
        {"/opt/bin/maxlev": "not-a-number", "/opt/bin/vplanet": "200"},
        {"0": "100"},
    )
    assert dictStale == {0: True}


def test_only_unparseable_mtimes_is_not_stale():
    """All-garbage mtimes resolve to not-stale, never to an exception."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow, {"/opt/bin/vplanet": "not-a-number"}, {"0": "100"},
    )
    assert dictStale == {0: False}


def test_step_not_depending_on_binary_is_not_stale():
    """Attribution gates the signal: a step that neither invokes nor
    declares the binary is unaffected by its mtime."""
    dictStep = {
        "sDirectory": "Plot", "saDataCommands": ["python plot.py"],
        "saOutputDataFiles": ["Plot/fig.pdf"], "saBinaryDependencies": [],
    }
    dictWorkflow = _fdictWorkflow([dictStep])
    dictStale = fdictBinaryStaleByStep(
        dictWorkflow, {"/opt/bin/vplanet": "999"}, {"0": "100"},
    )
    assert dictStale == {0: False}


# --------- warning projection: non-gating orange L1 ---------


def _dictStatesL1AttainedL2Partial():
    """The reported scenario's cell shape: L1 clean, climbing to L2."""
    return {
        "s1": {"sState": "attained", "bRegression": False},
        "s2": {"sState": "partial", "bRegression": False},
        "s3": {"sState": "partial", "bRegression": False},
    }


def test_stale_binary_raises_orange_l1_warning_on_clean_step():
    """The reported bug: an L1-clean step whose binary drifted gets an
    orange Level 1 advisory — without dropping the L1 gate (the cell
    stays attained; the warning level is 1, not the lowest-unattained
    2)."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, {0: _dictStatesL1AttainedL2Partial()}, [],
        {0: True},
    )
    dictWarning = dictWarnings[0]
    assert dictWarning["sWarningSeverity"] == "orange"
    assert dictWarning["iWarningLevel"] == 1
    assert "re-run" in dictWarning["sWarningHint"].lower()
    # Non-gating: the step is still at L1 (lowest non-attained is 2).
    assert dictWarning["iLowestNonAttainedLevel"] == 2


def test_no_warning_when_binary_not_stale():
    """Kill-confirm: the identical clean step with ``bBinaryStale``
    False emits no warning. If the assertion ever passes with the
    stale flag wired to a constant True, this fails."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, {0: _dictStatesL1AttainedL2Partial()}, [],
        {0: False},
    )
    assert dictWarnings[0]["iWarningLevel"] is None
    assert dictWarnings[0]["sWarningSeverity"] is None


def test_gate_warning_takes_precedence_over_stale_binary():
    """A genuine regression at the lowest non-attained level owns the
    single warning slot; the binary advisory does not mask it."""
    dictStates = {
        "s1": {"sState": "attained", "bRegression": False},
        "s2": {"sState": "partial", "bRegression": True},
        "s3": {"sState": "partial", "bRegression": False},
    }
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, {0: dictStates}, [], {0: True},
    )
    dictWarning = dictWarnings[0]
    assert dictWarning["iWarningLevel"] == 2   # the regression, not L1
    assert "regress" in dictWarning["sWarningHint"].lower()


def test_stale_binary_suppressed_when_all_levels_attained():
    """An attained L3 means the L3 hash check already confirmed the
    binary matches; a newer mtime is then a false alarm and must not
    warn."""
    dictStates = {
        "s1": {"sState": "attained", "bRegression": False},
        "s2": {"sState": "attained", "bRegression": False},
        "s3": {"sState": "attained", "bRegression": False},
    }
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, {0: dictStates}, [], {0: True},
    )
    assert dictWarnings[0]["iWarningLevel"] is None


def test_none_stale_map_defaults_to_no_binary_warning():
    """Omitting the stale map entirely leaves the legacy behavior
    unchanged — no warning appears out of nowhere."""
    dictWorkflow = _fdictWorkflow([_dictStep()])
    dictWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, {0: _dictStatesL1AttainedL2Partial()}, [],
    )
    assert dictWarnings[0]["iWarningLevel"] is None
