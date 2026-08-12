"""Sharding the standing negative control without losing what it proves.

The re-confirmation harness re-applies every recorded mutation and
confirms the guarding test still fails. Splitting it across machines is
a throughput change; the risk is that it quietly becomes a weaker
claim, in one of three ways:

- **A shard could stop being a partition.** If the slices overlap, work
  is repeated; if they miss entries, those entries are never judged and
  nothing says so.
- **A shard could promote a deferred entry.** The facility partition
  defers entries needing a Docker daemon or a browser; a selector
  applied before it hands them to a host that lacks the facility, whose
  child turns the skip into a failure, and the report then names a
  broken guard that is not broken. ``--only`` already had to learn this.
- **The union could go unchecked.** No shard can say "the whole
  registry was re-confirmed". If nothing else says it either, the
  guarantee is gone while every job stays green -- and a shard that
  never ran uploads nothing, so a summary that adds up what arrived
  reports a clean bill for absent work.

The last one is the reason this file exists. It is the same shape as
the ``docker info || exit 0`` step in this repository's Lessons list,
which reported success for having run nothing.
"""

import importlib.util
import json
import pathlib

import pytest


def _fmoduleLoadTool(sFileName):
    """Load a module from tools/ under a private name."""
    pathTool = (
        pathlib.Path(__file__).resolve().parent.parent
        / "tools" / sFileName
    )
    spec = importlib.util.spec_from_file_location(
        "tool_" + sFileName.replace(".py", ""), pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


def _flistFakeEntries(iCount):
    """Return placeholder entries; only identity and count matter here."""
    return [f"entry-{i:03d}" for i in range(iCount)]


# ── The split itself ─────────────────────────────────────────────

@pytest.mark.falsification
def testEveryEntryLandsInExactlyOneShard():
    """The shards partition the registry: no gaps, no overlaps.

    A gap is the dangerous half. An entry in no shard is never
    re-confirmed, every job still reports success for the slice it did
    run, and the guard it defends is undefended with nothing red
    anywhere.

    Kills: a block split with an off-by-one, or any stride that does
    not cover the list.
    """
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")
    listEntries = _flistFakeEntries(101)
    for iShards in (1, 2, 3, 8, 16):
        listSeen = []
        for iShard in range(1, iShards + 1):
            listSlice, _listDeferred = moduleTool._tSelectShard(
                listEntries, [], (iShard, iShards),
            )
            listSeen.extend(listSlice)
        assert sorted(listSeen) == sorted(listEntries), (
            f"K={iShards} is not a partition: "
            f"{len(listSeen)} slots for {len(listEntries)} entries"
        )


def testTheShardsAreBalancedToWithinOneEntry():
    """A stride split, so no shard carries the slow half alone.

    Entries sit in the registry grouped by the feature they defend, so
    a block split would hand one shard the browser entries and another
    the cheap unit ones -- and the lane is as slow as its slowest
    shard.
    """
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")
    listEntries = _flistFakeEntries(752)
    listSizes = [
        len(moduleTool._tSelectShard(listEntries, [], (i, 8))[0])
        for i in range(1, 9)
    ]
    assert max(listSizes) - min(listSizes) <= 1, listSizes


def testAShardOutsideItsRangeIsRefusedRatherThanEmptied():
    """Shard 9 of 8 must not run zero entries and call it success.

    An out-of-range index is a workflow typo, and the silent reading
    of it is the worst one available: no entries, "0/0
    kill-confirmed", exit zero. A green lane that checked nothing.
    """
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")
    for sBad in ("9/8", "0/8", "1/0", "one/eight", "8"):
        with pytest.raises(SystemExit):
            moduleTool._tParseShardArgument(sBad)
    assert moduleTool._tParseShardArgument("3/8") == (3, 8)
    assert moduleTool._tParseShardArgument("") is None


@pytest.mark.falsification
def testShardingNeverPromotesADeferredEntryIntoTheJudgedSet():
    """The shard split runs after the facility partition, never before.

    Same property ``--only`` needed, and it is worth pinning again
    because the tempting implementation is different: a shard that
    slices the raw registry looks obviously correct, and it hands a
    ``docker_live`` entry to a daemon-less macOS runner, where the
    child sets the requirement variable and the skip is reported as
    "does not pass on clean code" -- a deferral wearing a defect's
    clothes.

    Kills: slicing the registry before the partition, which the
    deferred list can detect because its entries would appear in the
    evaluable slice.
    """
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")
    listEvaluable = _flistFakeEntries(20)
    listDeferred = [(f"deferred-{i}", "browser") for i in range(6)]
    setDeferred = {t[0] for t in listDeferred}
    for iShard in range(1, 4):
        listSlice, listSliceDeferred = moduleTool._tSelectShard(
            listEvaluable, listDeferred, (iShard, 3),
        )
        assert not (set(listSlice) & setDeferred), (
            "a deferred entry reached the judged slice"
        )
        assert all(t in listDeferred for t in listSliceDeferred)


def testAShardSaysItIsNotTheStandingNegativeControl():
    """A slice must not read like the whole thing.

    The report a shard prints is the only thing most readers see, and
    "744/744 kill-confirmed" from one of eight machines is true of the
    slice and false of the registry.
    """
    sSource = (
        pathlib.Path(__file__).resolve().parent.parent
        / "tools" / "reconfirmFalsification.py"
    ).read_text()
    assert "SHARD {tShard[0]} of {tShard[1]}" in sSource
    assert (
        "[] if listOnly or tShard else _flistMarkedTestsWithoutEntry()"
        in sSource
    ), (
        "a shard must decline the whole-registry completeness check, "
        "the way a narrowed run does"
    )


# ── The union, which is the part that can silently disappear ─────

def _fnWriteShardArtifact(
    pathDirectory, sLeg, iShard, iShards, iRan, iKilled,
    listSurvivors=(),
):
    """Write one shard artifact the way the upload step lays it out."""
    pathArtifact = (
        pathDirectory / f"falsificationSummary-{sLeg}-{iShard}"
    )
    pathArtifact.mkdir(parents=True, exist_ok=True)
    (pathArtifact / "shardSummary.json").write_text(json.dumps({
        "iShard": iShard, "iShards": iShards,
        "iRan": iRan, "iKilled": iKilled, "iDeferred": 0,
        "listSurvivors": list(listSurvivors),
    }))


@pytest.mark.falsification
def testAMissingShardFailsTheSummaryInsteadOfBeingAddedAround(tmp_path):
    """The fail-closed property, and the whole reason this job exists.

    A shard that was cancelled, timed out, or never started uploads
    nothing. Summing the seven that did arrive and printing a total
    reports a green lane for work nobody ran -- which this repository
    has shipped once already, as a CI step guarded by ``docker info ||
    exit 0``.

    Kills: discovering the shard count from whatever arrived instead
    of requiring the declared one.
    """
    moduleSummary = _fmoduleLoadTool("summarizeFalsificationShards.py")
    for iShard in (1, 2, 3):
        _fnWriteShardArtifact(
            tmp_path, "ubuntu-24.04-3.14", iShard, 4, 100, 100,
        )
    iExit = moduleSummary.fiSummarizeShards(
        str(tmp_path), [("ubuntu-24.04-3.14", 4)],
    )
    assert iExit == 1, (
        "three shards of four reported and the summary passed anyway"
    )


def testAllShardsPresentAndKillingPassesTheSummary(tmp_path):
    """The other direction: a complete union is allowed to say so."""
    moduleSummary = _fmoduleLoadTool("summarizeFalsificationShards.py")
    for iShard in (1, 2):
        _fnWriteShardArtifact(
            tmp_path, "macos-26-3.9", iShard, 2, 350, 350,
        )
    assert moduleSummary.fiSummarizeShards(
        str(tmp_path), [("macos-26-3.9", 2)],
    ) == 0


@pytest.mark.falsification
def testAShardRunningADifferentSplitIsRefused(tmp_path):
    """Eight artifacts of a four-way split are not a four-way split.

    The workflow and the summary can drift: someone changes the matrix
    to 8 and forgets the ``--expect``, or the reverse. Each shard
    records the denominator it actually ran, so the mismatch is
    visible -- and it must be fatal, because "shard 1 of 8" covering
    an eighth while the summary believes it covered a quarter leaves
    most of the registry unjudged and the lane green.

    Kills: trusting the artifact count and ignoring the denominator
    each shard reports.
    """
    moduleSummary = _fmoduleLoadTool("summarizeFalsificationShards.py")
    for iShard in (1, 2, 3, 4):
        _fnWriteShardArtifact(
            tmp_path, "ubuntu-24.04-3.9", iShard, 8, 90, 90,
        )
    assert moduleSummary.fiSummarizeShards(
        str(tmp_path), [("ubuntu-24.04-3.9", 4)],
    ) == 1


def testASurvivingMutationIsNamedByLegAndShard(tmp_path, capsys):
    """A red summary must say where to look, across thirty-two jobs."""
    moduleSummary = _fmoduleLoadTool("summarizeFalsificationShards.py")
    _fnWriteShardArtifact(
        tmp_path, "ubuntu-24.04-3.14", 1, 2, 90, 89,
        listSurvivors=[{
            "sNodeId": "tests/testThing.py::testGuard",
            "sStatus": "SURVIVED: test did NOT catch the mutation",
        }],
    )
    _fnWriteShardArtifact(
        tmp_path, "ubuntu-24.04-3.14", 2, 2, 90, 90,
    )
    assert moduleSummary.fiSummarizeShards(
        str(tmp_path), [("ubuntu-24.04-3.14", 2)],
    ) == 1
    sOutput = capsys.readouterr().out
    assert "tests/testThing.py::testGuard" in sOutput
    assert "shard 1" in sOutput


def testTheWorkflowAndTheSummaryAgreeOnEveryLeg():
    """The `--expect` arguments must match the matrix that produces them.

    Drift here is invisible at runtime in the safe direction and fatal
    in the other: a leg the summary never expects can fail while the
    summary passes, because nothing ever looks for it.
    """
    import re
    sWorkflow = (
        pathlib.Path(__file__).resolve().parent.parent
        / ".github" / "workflows" / "falsification.yml"
    ).read_text()
    listExpected = re.findall(r'--expect "([^"]+)"', sWorkflow)
    assert listExpected, "the summary job declares no legs at all"
    for sExpectation in listExpected:
        sOperatingSystem, sPython, sCount = sExpectation.split(":")
        assert f"runs-on: {sOperatingSystem}" in sWorkflow, sExpectation
        assert f'"{sPython}"' in sWorkflow, sExpectation
        # The shard list must actually contain the highest index the
        # summary demands, or that shard is expected and never built.
        assert f"{sCount}]" in sWorkflow or f"{sCount},", sExpectation
