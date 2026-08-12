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
        "[] if listOnly or tShard or sClass" in sSource
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
            tmp_path, "ubuntu-24.04-3.14-shareable", iShard, 4, 100, 100,
        )
    iExit = moduleSummary.fiSummarizeShards(
        str(tmp_path), [("ubuntu-24.04-3.14-shareable", 4)],
    )
    assert iExit == 1, (
        "three shards of four reported and the summary passed anyway"
    )


def testAllShardsPresentAndKillingPassesTheSummary(tmp_path):
    """The other direction: a complete union is allowed to say so."""
    moduleSummary = _fmoduleLoadTool("summarizeFalsificationShards.py")
    for iShard in (1, 2):
        _fnWriteShardArtifact(
            tmp_path, "macos-26-3.9-shareable", iShard, 2, 350, 350,
        )
    assert moduleSummary.fiSummarizeShards(
        str(tmp_path), [("macos-26-3.9-shareable", 2)],
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
            tmp_path, "ubuntu-24.04-3.9-shareable", iShard, 8, 90, 90,
        )
    assert moduleSummary.fiSummarizeShards(
        str(tmp_path), [("ubuntu-24.04-3.9-shareable", 4)],
    ) == 1


def testASurvivingMutationIsNamedByLegAndShard(tmp_path, capsys):
    """A red summary must say where to look, across thirty-two jobs."""
    moduleSummary = _fmoduleLoadTool("summarizeFalsificationShards.py")
    _fnWriteShardArtifact(
        tmp_path, "ubuntu-24.04-3.14-shareable", 1, 2, 90, 89,
        listSurvivors=[{
            "sNodeId": "tests/testThing.py::testGuard",
            "sStatus": "SURVIVED: test did NOT catch the mutation",
        }],
    )
    _fnWriteShardArtifact(
        tmp_path, "ubuntu-24.04-3.14-shareable", 2, 2, 90, 90,
    )
    assert moduleSummary.fiSummarizeShards(
        str(tmp_path), [("ubuntu-24.04-3.14-shareable", 2)],
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
        sOperatingSystem, sPython, _sClass, sCount = sExpectation.split(":")
        assert f"runs-on: {sOperatingSystem}" in sWorkflow, sExpectation
        assert f'"{sPython}"' in sWorkflow, sExpectation
        # The shard list must actually contain the highest index the
        # summary demands, or that shard is expected and never built.
        assert f"{sCount}]" in sWorkflow or f"{sCount},", sExpectation


# ── The classification, which is a grep unless something enforces it ──

_T_MACHINE_RESOURCE_IDIOMS = (
    "uvicorn.Server", "_fiFreePort", "AF_UNIX",
)


@pytest.mark.falsification
def testAFileThatBindsAPortCarriesTheExclusiveMarker():
    """The class split is only as good as the thing that assigns it.

    ``browser`` and ``docker_live`` are markers the harness already
    partitions on, so those two classes look after themselves. "Binds
    a port" is marked nowhere by nature: it is a property of a fixture
    that happens to start a real server, and the only reason the 19
    entries are classified today is that somebody grepped for the
    idioms once. A grep run once rots.

    So the grep runs here instead, every build. A file using any of
    these idioms must carry ``pytestmark = pytest.mark.exclusive``, or
    its entries land in the shareable lane and two of them meet on one
    machine under workers -- which fails as a port collision in an
    unrelated test, weeks later, and reads as flakiness.

    Kills: adding a real-server fixture to a test file and not marking
    it, which is what a future contributor will do.
    """
    pathTests = pathlib.Path(__file__).resolve().parent
    listUnmarked = []
    for pathFile in sorted(pathTests.glob("test*.py")):
        sSource = pathFile.read_text(encoding="utf-8")
        if not any(s in sSource for s in _T_MACHINE_RESOURCE_IDIOMS):
            continue
        if "pytest.mark.exclusive" in sSource:
            continue
        listUnmarked.append(pathFile.name)
    assert listUnmarked == [], (
        "these files bind a machine-global resource but are not marked "
        f"exclusive, so the harness would run them under parallel "
        f"workers: {listUnmarked}"
    )


def testTheExclusiveClassIsWhatTheHarnessActuallySelects():
    """The marker names it and the harness partitions on it.

    Regression cover for the wiring rather than a separate property:
    a marker nothing reads classifies nothing.
    """
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")
    assert "exclusive" in moduleTool.T_EXCLUSIVE_MARKERS
    assert "browser" in moduleTool.T_EXCLUSIVE_MARKERS
    assert "docker_live" in moduleTool.T_EXCLUSIVE_MARKERS


@pytest.mark.falsification
def testAWorkerSliceIsStillAPartitionOfItsParentShard():
    """Workers split a shard, and the arithmetic must not lose entries.

    A worker takes a slice of a slice. If the composition is wrong the
    lost entries are re-confirmed by nobody, every job still reports
    success for what it did run, and the registry quietly stops being
    covered.

    Kills: any sub-shard arithmetic whose union is not the parent's
    slice -- an off-by-one in the worker index or the wrong
    denominator.
    """
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")
    listEntries = _flistFakeEntries(200)
    for tParent in ((1, 4), (3, 4), (1, 1)):
        listParent, _listDeferred = moduleTool._tSelectShard(
            listEntries, [], tParent,
        )
        listSeen = []
        for iWorker in range(1, 5):
            tSub = moduleTool._tSubShardForWorker(tParent, iWorker, 4)
            listSlice, _listUnused = moduleTool._tSelectShard(
                listEntries, [], tSub,
            )
            listSeen.extend(listSlice)
        assert sorted(listSeen) == sorted(listParent), (
            f"workers of shard {tParent} cover {len(listSeen)} of "
            f"{len(listParent)} entries"
        )


@pytest.mark.falsification
def testALeftoverGrandchildDoesNotFailTheRun(monkeypatch, capsys):
    """A bytecode cache that will not delete is not a verdict.

    The first CI run that ever re-confirmed the browser entries died
    here, with ``OSError: Directory not empty``. The entry being judged
    had already passed; what failed was removing the throwaway
    ``PYTHONPYCACHEPREFIX`` tree, because a test's uvicorn hub or its
    Chromium outlived the pytest process and kept writing bytecode into
    it. Failing a whole lane over that reports a guard as undefended
    when nothing of the sort happened.

    It must still SAY so, because a process outliving its test is worth
    knowing about even though it is not this tool's to fix.

    Kills: letting the cleanup raise -- which is what
    ``TemporaryDirectory`` does, and what shipped.
    """
    import shutil as moduleShutil
    moduleTool = _fmoduleLoadTool("reconfirmFalsification.py")

    def fnRefuseToRemove(sPath, *tArguments, **dictKeywords):
        raise OSError(39, "Directory not empty", "python3.9")

    monkeypatch.setattr(moduleShutil, "rmtree", fnRefuseToRemove)
    monkeypatch.setattr(moduleTool, "shutil", moduleShutil)
    moduleTool._fnDiscardPycachePrefix("/tmp/somePycachePrefix")
    sOutput = capsys.readouterr().out
    assert "left the bytecode cache" in sOutput, sOutput
    assert "after pytest exited" in sOutput, sOutput


@pytest.mark.falsification
def testNoSummaryIsWrittenIntoTheCheckout():
    """A job's own output must not make its next step refuse to run.

    The harness refuses a dirty working tree, because it checks out
    HEAD and would otherwise report on code the caller does not have.
    That guard is right, and it fired on the first macOS job to run two
    invocations in one checkout: the exclusive step wrote
    ``exclusiveSummary.json`` into the repo root, and the shareable
    step that followed saw ``?? exclusiveSummary.json`` and stopped.

    The single-invocation jobs never notice, which is what makes this
    worth pinning: it appears only when a job grows a second step, and
    the failure names the guard rather than the cause.

    Kills: writing a summary to a bare filename, which resolves inside
    the checkout.
    """
    sWorkflow = (
        pathlib.Path(__file__).resolve().parent.parent
        / ".github" / "workflows" / "falsification.yml"
    ).read_text()
    import re
    listTargets = re.findall(r"--summary-json (\S+)", sWorkflow)
    assert listTargets, "no summary is written at all"
    listInsideCheckout = [
        s for s in listTargets if "RUNNER_TEMP" not in s
    ]
    assert listInsideCheckout == [], (
        "these summaries land in the checkout, so a following step "
        f"sees a dirty tree and refuses: {listInsideCheckout}"
    )
