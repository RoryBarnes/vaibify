"""Tests for the stale-output detector (Part D).

Covers the per-file advisory shape, declared-edge suppression,
sibling-step suppression, margin filter, clustering, and the
wire-through helper that surfaces advisories in the poll response.
"""

from vaibify.gui.routes.pipelineRoutes import (
    _flistBuildStaleOutputAdvisories,
)
from vaibify.gui.staleOutputDetector import flistStaleOutputAdvisories


def _fdictBuildSimpleWorkflow():
    """Two-step workflow: A03 (producer) and A10 (consumer)."""
    return {
        "sProjectRepoPath": "/repo",
        "listSteps": [
            {
                "sName": "Producer",
                "sDirectory": "Producer",
                "saDataFiles": ["output.npy"],
                "saPlotFiles": [],
            },
            {
                "sName": "Consumer",
                "sDirectory": "Consumer",
                "saDataFiles": ["consumed.json"],
                "saPlotFiles": ["plot.pdf"],
            },
        ],
    }


def testConsumerOlderThanUndeclaredProducerEmitsAdvisory():
    """A non-declared producer newer than the consumer should advise."""
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 1000.0,
        "Consumer/consumed.json": 500.0,
        "Consumer/plot.pdf": 500.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert len(listAdvisories) == 1
    dictEntry = listAdvisories[0]
    assert dictEntry["iConsumerStepIndex"] == 1
    assert dictEntry["iLikelyProducerStepIndex"] == 0
    assert dictEntry["listOffendingFiles"] == [
        "consumed.json", "plot.pdf",
    ]
    assert dictEntry["fAgeDeltaSeconds"] == 500.0


def testDirectionFollowsWhichStepIsOlder():
    """The advisory's iConsumer is always the older-output step.

    The detector doesn't know which way a dependency points; it flags
    any asymmetry. When the "Producer"-named step is actually OLDER
    than the "Consumer"-named step, the advisory still fires but with
    iConsumer = the older one and iLikelyProducer = the newer one.
    The naming in the test fixture is incidental.
    """
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 500.0,
        "Consumer/consumed.json": 1000.0,
        "Consumer/plot.pdf": 1000.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert len(listAdvisories) == 1
    dictEntry = listAdvisories[0]
    assert dictEntry["iConsumerStepIndex"] == 0
    assert dictEntry["iLikelyProducerStepIndex"] == 1


def testDeclaredEdgeSuppressesAdvisory():
    """A declared upstream edge silences the cross-mtime advisory."""
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 1000.0,
        "Consumer/consumed.json": 500.0,
        "Consumer/plot.pdf": 500.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={1: {0}},
    )
    assert listAdvisories == []


def testSiblingHeuristicSuppresses():
    """Sibling steps sharing parent + output basenames don't advise each other."""
    dictWorkflow = {
        "sProjectRepoPath": "/repo",
        "listSteps": [
            {
                "sName": "A",
                "sDirectory": "Suite/A",
                "saDataFiles": ["result.json"],
                "saPlotFiles": [],
            },
            {
                "sName": "B",
                "sDirectory": "Suite/B",
                "saDataFiles": ["result.json"],
                "saPlotFiles": [],
            },
        ],
    }
    dictMtimes = {
        "Suite/A/result.json": 1000.0,
        "Suite/B/result.json": 500.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert listAdvisories == []


def testMarginFilterRejectsSmallDelta():
    """A delta below the 60-second default margin should not advise."""
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 1030.0,
        "Consumer/consumed.json": 1000.0,
        "Consumer/plot.pdf": 1000.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert listAdvisories == []


def testMarginFilterAcceptsLargerDelta():
    """A delta well above the margin should advise."""
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 1500.0,
        "Consumer/consumed.json": 1000.0,
        "Consumer/plot.pdf": 1000.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert len(listAdvisories) == 1
    assert listAdvisories[0]["fAgeDeltaSeconds"] == 500.0


def testClusterIntoOneEntryPerPair():
    """Multiple offending consumer files from one producer cluster into one advisory."""
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictWorkflow["listSteps"][1]["saDataFiles"] = ["a.json", "b.json", "c.json"]
    dictWorkflow["listSteps"][1]["saPlotFiles"] = ["d.pdf", "e.pdf"]
    dictMtimes = {
        "Producer/output.npy": 5000.0,
        "Consumer/a.json": 1000.0,
        "Consumer/b.json": 1100.0,
        "Consumer/c.json": 1200.0,
        "Consumer/d.pdf": 1300.0,
        "Consumer/e.pdf": 1400.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert len(listAdvisories) == 1
    assert len(listAdvisories[0]["listOffendingFiles"]) == 5


def testNeverProducedConsumerSkipped():
    """Consumer with no observed mtimes shouldn't advise (poll hasn't seen outputs)."""
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 1000.0,
    }
    listAdvisories = flistStaleOutputAdvisories(
        dictWorkflow, dictMtimes, dictDeclaredUpstream={},
    )
    assert listAdvisories == []


def testWireThroughReturnsAdvisoriesFromPollHelper():
    """The poll-side helper builds the declared-upstream map from the workflow.

    Exercises the path the live poll uses: `_flistBuildStaleOutputAdvisories`
    derives `dictDeclaredUpstream` from `fdictBuildDirectDependencies`
    and forwards to the detector. With an undeclared edge, the
    advisory surfaces in the helper's output.
    """
    dictWorkflow = _fdictBuildSimpleWorkflow()
    dictMtimes = {
        "Producer/output.npy": 1000.0,
        "Consumer/consumed.json": 500.0,
        "Consumer/plot.pdf": 500.0,
    }
    listAdvisories = _flistBuildStaleOutputAdvisories(
        dictWorkflow, dictMtimes,
    )
    assert len(listAdvisories) == 1
    assert listAdvisories[0]["iConsumerStepIndex"] == 1
    assert listAdvisories[0]["iLikelyProducerStepIndex"] == 0
