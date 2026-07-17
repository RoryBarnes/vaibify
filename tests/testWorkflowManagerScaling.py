"""Scaling regression tests for workflowManager hot paths.

Item 2 — ``fdictBuildImplicitDependencies`` was O(N**2 * M) because
of nested outer-step / inner-step / per-output loops; the optimized
version pre-indexes step directories and probes each output path's
ancestor directories.

Item 3 — ``fdictBuildDownstreamMap`` historically ran a BFS per
step, costing O(N * (N + E)). The replacement walks one reverse-
topological closure in O(N + E).

Item 4 — ``_fdictStripComputedFields`` deep-copied the whole
workflow even though only the transient step keys ever change; the
replacement copies the spine shallowly and the steps shallowly.

Items 2 and 3 share a workflow-content-hash-keyed LRU cache so a
repeat call on identical input never reruns the graph build.
"""

import time

from vaibify.gui import workflowManager
from vaibify.gui.workflowManager import (
    _fdictStripComputedFields,
    fdictBuildDirectDependencies,
    fdictBuildDownstreamMap,
    fdictBuildImplicitDependencies,
    fnClearDepGraphCache,
)


def _fdictLinearChainWorkflow(iSteps):
    """Build an N-step chain: each step depends on the previous via a token."""
    listSteps = []
    for iIndex in range(iSteps):
        iNumber = iIndex + 1
        dictStep = {
            "sName": f"Step{iNumber:03d}",
            "sDirectory": f"step{iNumber:03d}",
            "saDataCommands": [],
            "saPlotCommands": [],
            "saOutputDataFiles": [f"output{iNumber:03d}.csv"],
            "saPlotFiles": [],
            "saDependencies": [],
        }
        if iIndex > 0:
            dictStep["saDataCommands"].append(
                f"compute --in {{Step{iIndex:02d}.output{iIndex:03d}}}",
            )
        listSteps.append(dictStep)
    return {"listSteps": listSteps, "sPlotDirectory": "Plot"}


def _fdictNestedDirectoryWorkflow(iSteps):
    """Build a workflow where each step's outputs land in step0's dir.

    The original implicit-deps algorithm degrades to its worst case
    here because every later step sees step0 as a potential consumer
    of every other step's outputs.
    """
    listSteps = []
    for iIndex in range(iSteps):
        listSteps.append({
            "sName": f"Step{iIndex + 1:03d}",
            "sDirectory": "shared"
            if iIndex == iSteps - 1 else f"shared/sub{iIndex:03d}",
            "saOutputDataFiles": [f"output{iIndex:03d}.csv"],
            "saPlotFiles": [],
            "saDataCommands": [],
            "saPlotCommands": [],
        })
    return {"listSteps": listSteps, "sPlotDirectory": "Plot"}


class TestFdictBuildImplicitDependenciesScaling:

    def setup_method(self):
        fnClearDepGraphCache()

    def test_500_step_nested_completes_quickly(self):
        dictWorkflow = _fdictNestedDirectoryWorkflow(500)
        fStart = time.perf_counter()
        fdictBuildImplicitDependencies(dictWorkflow)
        fElapsed = time.perf_counter() - fStart
        assert fElapsed < 1.0

    def test_correctness_small_nested(self):
        dictWorkflow = _fdictNestedDirectoryWorkflow(5)
        dictImplicit = fdictBuildImplicitDependencies(dictWorkflow)
        for iProducer in range(4):
            assert 4 in dictImplicit.get(iProducer, set())


class TestDepGraphCache:

    def setup_method(self):
        fnClearDepGraphCache()

    def test_repeat_call_hits_cache(self):
        dictWorkflow = _fdictLinearChainWorkflow(50)
        dictFirst = fdictBuildDirectDependencies(dictWorkflow)
        dictSecond = fdictBuildDirectDependencies(dictWorkflow)
        assert dictFirst is dictSecond

    def test_cache_miss_after_workflow_mutation(self):
        dictWorkflow = _fdictLinearChainWorkflow(20)
        dictFirst = fdictBuildDirectDependencies(dictWorkflow)
        dictWorkflow["listSteps"].append({
            "sName": "Tail", "sDirectory": "tail",
            "saOutputDataFiles": ["tail.csv"], "saPlotFiles": [],
            "saDataCommands": [], "saPlotCommands": [],
            "saDependencies": [],
        })
        dictSecond = fdictBuildDirectDependencies(dictWorkflow)
        assert dictFirst is not dictSecond
        assert len(dictSecond) >= len(dictFirst)

    def test_cache_lru_eviction(self):
        dictBaseline = _fdictLinearChainWorkflow(5)
        fdictBuildDirectDependencies(dictBaseline)
        for iVariant in range(20):
            dictVariant = _fdictLinearChainWorkflow(5)
            dictVariant["listSteps"][0]["sName"] = f"Variant{iVariant}"
            fdictBuildDirectDependencies(dictVariant)
        assert len(workflowManager._DICT_DEP_CACHE) <= 16


class TestFdictBuildDownstreamMapScaling:

    def setup_method(self):
        fnClearDepGraphCache()

    def test_chain_correctness_500_steps(self):
        dictWorkflow = _fdictLinearChainWorkflow(500)
        dictDownstream = fdictBuildDownstreamMap(dictWorkflow)
        assert dictDownstream[0] == set(range(1, 500))
        assert dictDownstream[250] == set(range(251, 500))
        assert dictDownstream[499] == set()

    def test_chain_completes_quickly_500_steps(self):
        dictWorkflow = _fdictLinearChainWorkflow(500)
        fStart = time.perf_counter()
        fdictBuildDownstreamMap(dictWorkflow)
        fElapsed = time.perf_counter() - fStart
        assert fElapsed < 1.0

    def test_matches_legacy_bfs_for_50_step_dag(self):
        dictWorkflow = _fdictLinearChainWorkflow(50)
        dictDirect = fdictBuildDirectDependencies(dictWorkflow)
        dictExpected = _fdictLegacyBfsClosure(
            len(dictWorkflow["listSteps"]), dictDirect,
        )
        fnClearDepGraphCache()
        dictActual = fdictBuildDownstreamMap(dictWorkflow)
        assert dictActual == dictExpected

    def test_diamond_dag_matches_legacy_bfs(self):
        dictWorkflow = _fdictDiamondWorkflow()
        dictDirect = fdictBuildDirectDependencies(dictWorkflow)
        dictExpected = _fdictLegacyBfsClosure(
            len(dictWorkflow["listSteps"]), dictDirect,
        )
        fnClearDepGraphCache()
        dictActual = fdictBuildDownstreamMap(dictWorkflow)
        assert dictActual == dictExpected


def _fdictDiamondWorkflow():
    """Root branches into two children which both feed one tail."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [
            {
                "sName": "Root", "sDirectory": "root",
                "saOutputDataFiles": ["root.csv"],
                "saPlotFiles": [], "saDataCommands": [],
                "saPlotCommands": [],
            },
            {
                "sName": "Left", "sDirectory": "left",
                "saOutputDataFiles": ["left.csv"], "saPlotFiles": [],
                "saDataCommands": ["use {Step01.root}"],
                "saPlotCommands": [],
            },
            {
                "sName": "Right", "sDirectory": "right",
                "saOutputDataFiles": ["right.csv"], "saPlotFiles": [],
                "saDataCommands": ["use {Step01.root}"],
                "saPlotCommands": [],
            },
            {
                "sName": "Tail", "sDirectory": "tail",
                "saOutputDataFiles": ["tail.csv"], "saPlotFiles": [],
                "saDataCommands": [
                    "merge {Step02.left} {Step03.right}",
                ],
                "saPlotCommands": [],
            },
        ],
    }


def _fdictLegacyBfsClosure(iStepCount, dictDirect):
    """Reference implementation of the old per-step BFS closure."""
    from collections import deque
    dictDownstream = {}
    for iIndex in range(iStepCount):
        setVisited = set()
        dequeQueue = deque(dictDirect.get(iIndex, set()))
        while dequeQueue:
            iCurrent = dequeQueue.popleft()
            if iCurrent in setVisited:
                continue
            setVisited.add(iCurrent)
            dequeQueue.extend(dictDirect.get(iCurrent, set()))
        dictDownstream[iIndex] = setVisited
    return dictDownstream


class TestFdictStripComputedFieldsShallow:

    def test_strips_transient_step_keys(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "dictStateLoadNotice": {"sStatus": "ok"},
            "listSteps": [{
                "sName": "S", "saStepScripts": ["a.py"],
                "saTestStandards": ["b"], "saSourceCodeDeps": ["c"],
            }],
        }
        dictClean = _fdictStripComputedFields(dictWorkflow)
        assert "dictStateLoadNotice" not in dictClean
        assert "saStepScripts" not in dictClean["listSteps"][0]
        assert "saTestStandards" not in dictClean["listSteps"][0]
        assert "saSourceCodeDeps" not in dictClean["listSteps"][0]

    def test_does_not_mutate_source_workflow(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "dictStateLoadNotice": {"sStatus": "ok"},
            "listSteps": [{
                "sName": "S", "saStepScripts": ["a.py"],
            }],
        }
        _fdictStripComputedFields(dictWorkflow)
        assert "dictStateLoadNotice" in dictWorkflow
        assert "saStepScripts" in dictWorkflow["listSteps"][0]

    def test_returned_steps_are_independent_from_source(self):
        dictWorkflow = {
            "sPlotDirectory": "Plot",
            "listSteps": [{
                "sName": "S",
                "saStepScripts": ["a.py"],
            }],
        }
        dictClean = _fdictStripComputedFields(dictWorkflow)
        dictClean["listSteps"][0]["sName"] = "Mutated"
        assert dictWorkflow["listSteps"][0]["sName"] == "S"

    def test_500_step_strip_completes_quickly(self):
        dictWorkflow = _fdictLinearChainWorkflow(500)
        for dictStep in dictWorkflow["listSteps"]:
            dictStep["saStepScripts"] = ["a.py", "b.py"]
            dictStep["saTestStandards"] = ["s1", "s2"]
        fStart = time.perf_counter()
        _fdictStripComputedFields(dictWorkflow)
        fElapsed = time.perf_counter() - fStart
        assert fElapsed < 0.5
