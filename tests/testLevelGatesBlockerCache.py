"""Cross-poll memoization tests for the level-gate blocker collectors.

``flistLevel1Blockers`` / ``flistLevel2Blockers`` / ``flistLevel3Blockers``
each walk every step on every poll. When the workflow content,
mod-time vector, and project repo are unchanged between two polls,
their output is identical — the cache here short-circuits the second
walk so the dashboard's per-poll cost stays bounded as the workflow
grows.
"""

from vaibify.reproducibility import levelGates
from vaibify.reproducibility.levelGates import (
    flistLevel1Blockers,
    flistLevel2Blockers,
    flistLevel3Blockers,
    fnClearLevelBlockerCache,
)


def _fdictMinimalStep(sName):
    """Step satisfying every L1 axis so the blocker list stays empty."""
    return {
        "sName": sName, "sDirectory": sName, "bInteractive": False,
        "saOutputDataFiles": [sName + "/data.csv"],
        "saPlotFiles": [sName + "/plot.pdf"],
        "saDataCommands": [], "saPlotCommands": [],
        "saTestCommands": [], "saDependencies": [],
        "dictVerification": {
            "sUser": "passed", "sUnitTest": "passed",
            "sIntegrity": "passed", "sQualitative": "passed",
            "sQuantitative": "passed",
        },
    }


def _fdictWorkflow(iSteps=3):
    """Return a workflow with iSteps all-green steps."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            _fdictMinimalStep(f"Step{iIndex:02d}")
            for iIndex in range(iSteps)
        ],
    }


class _ComputeCounter:
    """Wrap the level-N compute function to count invocations."""

    def __init__(self, sAttribute):
        self.sAttribute = sAttribute
        self.fnOriginal = getattr(levelGates, sAttribute)
        self.iCalls = 0

    def __enter__(self):
        def fnWrapped(*aArgs, **dictKwargs):
            self.iCalls += 1
            return self.fnOriginal(*aArgs, **dictKwargs)
        setattr(levelGates, self.sAttribute, fnWrapped)
        return self

    def __exit__(self, *aIgnored):
        setattr(levelGates, self.sAttribute, self.fnOriginal)


class TestLevelBlockerCacheLevel1:

    def setup_method(self):
        fnClearLevelBlockerCache()

    def test_second_call_returns_cached_object(self):
        dictWorkflow = _fdictWorkflow(3)
        listFirst = flistLevel1Blockers(dictWorkflow, {}, "/repo")
        listSecond = flistLevel1Blockers(dictWorkflow, {}, "/repo")
        assert listFirst is listSecond

    def test_second_call_does_not_re_walk(self):
        dictWorkflow = _fdictWorkflow(3)
        flistLevel1Blockers(dictWorkflow, {}, "/repo")
        with _ComputeCounter("_flistComputeLevel1Blockers") as counter:
            flistLevel1Blockers(dictWorkflow, {}, "/repo")
            assert counter.iCalls == 0

    def test_workflow_mutation_busts_cache(self):
        dictWorkflow = _fdictWorkflow(3)
        flistLevel1Blockers(dictWorkflow, {}, "/repo")
        dictWorkflow["listSteps"][0]["dictVerification"][
            "sUser"] = "untested"
        with _ComputeCounter("_flistComputeLevel1Blockers") as counter:
            listSecond = flistLevel1Blockers(
                dictWorkflow, {}, "/repo",
            )
            assert counter.iCalls == 1
        assert listSecond  # Now non-empty because user not approved.

    def test_mod_time_vector_mutation_busts_cache(self):
        dictWorkflow = _fdictWorkflow(3)
        flistLevel1Blockers(dictWorkflow, {1: 100}, "/repo")
        with _ComputeCounter("_flistComputeLevel1Blockers") as counter:
            flistLevel1Blockers(dictWorkflow, {1: 200}, "/repo")
            assert counter.iCalls == 1

    def test_clear_drops_cached_entries(self):
        dictWorkflow = _fdictWorkflow(3)
        flistLevel1Blockers(dictWorkflow, {}, "/repo")
        fnClearLevelBlockerCache()
        with _ComputeCounter("_flistComputeLevel1Blockers") as counter:
            flistLevel1Blockers(dictWorkflow, {}, "/repo")
            assert counter.iCalls == 1


class TestLevelBlockerCacheLevel2:

    def setup_method(self):
        fnClearLevelBlockerCache()

    def test_second_call_returns_cached_object(self):
        dictWorkflow = _fdictWorkflow(2)
        listFirst = flistLevel2Blockers(dictWorkflow, "/repo")
        listSecond = flistLevel2Blockers(dictWorkflow, "/repo")
        assert listFirst is listSecond

    def test_second_call_does_not_re_walk(self):
        dictWorkflow = _fdictWorkflow(2)
        flistLevel2Blockers(dictWorkflow, "/repo")
        with _ComputeCounter("_flistComputeLevel2Blockers") as counter:
            flistLevel2Blockers(dictWorkflow, "/repo")
            assert counter.iCalls == 0


class TestLevelBlockerCacheLevel3:

    def setup_method(self):
        fnClearLevelBlockerCache()

    def test_second_call_returns_cached_object(self):
        dictWorkflow = _fdictWorkflow(2)
        listFirst = flistLevel3Blockers(dictWorkflow, "/repo")
        listSecond = flistLevel3Blockers(dictWorkflow, "/repo")
        assert listFirst is listSecond

    def test_second_call_does_not_re_walk(self):
        dictWorkflow = _fdictWorkflow(2)
        flistLevel3Blockers(dictWorkflow, "/repo")
        with _ComputeCounter("_flistComputeLevel3Blockers") as counter:
            flistLevel3Blockers(dictWorkflow, "/repo")
            assert counter.iCalls == 0


class TestLevelBlockerCacheLruBound:

    def setup_method(self):
        fnClearLevelBlockerCache()

    def test_lru_bound_keeps_cache_small(self):
        for iVariant in range(20):
            dictWorkflow = _fdictWorkflow(2)
            dictWorkflow["listSteps"][0]["sName"] = f"Variant{iVariant}"
            flistLevel1Blockers(dictWorkflow, {}, "/repo")
        assert len(levelGates._DICT_BLOCKER_CACHE) <= 8
