"""Add the falsification shards up, and refuse to guess about a missing one.

The re-confirmation harness is the standing negative control: it
re-applies every recorded mutation and confirms the guarding test still
fails. Splitting it across machines makes it fast, and takes away the
one thing a single run used to have -- the ability to say "the whole
registry was re-confirmed". No shard can say that. This does.

**It fails closed, and that is the entire point.** A shard that was
cancelled, timed out, or never started uploads no summary. Adding up
the summaries that did arrive and printing a total would report a green
lane for work nobody ran, which is the failure this repository already
has a Lessons entry about (a CI step guarded by ``docker info || exit
0`` reported success for having run nothing). So the expected shards
are declared on the command line, and every one of them must be
present, exactly once, with the shard count it claims matching what was
asked for.

The output line is deliberately the same shape a single unsharded run
printed -- ``752/752 kill-confirmed`` -- because that is what a reader
already knows how to read, and because the badge on the landing page
counts registry entries rather than parsing this.
"""

__all__ = ["fiSummarizeShards"]

import argparse
import json
import pathlib
import sys


def _tParseExpectation(sExpectation):
    """Return ``(sLeg, iShards)`` from an ``os:python:class:count`` argument.

    The CLASS is part of the leg's identity, not decoration: the
    exclusive entries and the shareable ones are re-confirmed by
    different jobs, and a summary that could not tell them apart would
    accept the shareable ones twice and never notice the exclusive lane
    was missing.
    """
    listParts = sExpectation.split(":")
    if len(listParts) != 4:
        raise SystemExit(
            f"--expect wants os:python:class:count, not {sExpectation!r}"
        )
    sOperatingSystem, sPython, sClass, sCount = listParts
    try:
        iShards = int(sCount)
    except ValueError:
        raise SystemExit(
            f"--expect count is not a number in {sExpectation!r}"
        )
    return (f"{sOperatingSystem}-{sPython}-{sClass}", iShards)


def _fdictReadShardSummaries(sDirectory):
    """Return ``{sLeg: {iShard: dictSummary}}`` from the artifact tree.

    The artifact directory name carries the leg and shard, because the
    JSON inside knows which shard it is but not which machine ran it --
    and "shard 3 reported" is only half an answer when four legs each
    have a shard 3.
    """
    dictByLeg = {}
    for pathSummary in sorted(
        pathlib.Path(sDirectory).glob("*/shardSummary.json"),
    ):
        sArtifact = pathSummary.parent.name
        sLeg, _sSeparator, sShard = (
            sArtifact[len("falsificationSummary-"):].rpartition("-")
        )
        dictSummary = json.loads(pathSummary.read_text(encoding="utf-8"))
        dictByLeg.setdefault(sLeg, {})[int(sShard)] = dictSummary
    return dictByLeg


def _flistDescribeMissingShards(dictByLeg, listExpectations):
    """Return one sentence per leg whose shards did not all report."""
    listProblems = []
    for sLeg, iShards in listExpectations:
        dictShards = dictByLeg.get(sLeg, {})
        setMissing = set(range(1, iShards + 1)) - set(dictShards)
        if setMissing:
            listProblems.append(
                f"{sLeg}: shard(s) {sorted(setMissing)} reported "
                f"nothing. A shard that did not report did not run, "
                f"and its slice of the registry is unjudged."
            )
        for iShard, dictSummary in sorted(dictShards.items()):
            if dictSummary.get('iShards') != iShards:
                listProblems.append(
                    f"{sLeg} shard {iShard} says it was 1 of "
                    f"{dictSummary.get('iShards')}, but this job "
                    f"expected 1 of {iShards}. The split the shards "
                    f"ran is not the split being summarized."
                )
    setUnexpected = set(dictByLeg) - {sLeg for sLeg, _i in listExpectations}
    for sLeg in sorted(setUnexpected):
        listProblems.append(
            f"{sLeg} reported but was not expected; the workflow and "
            f"this summary disagree about which legs exist."
        )
    return listProblems


def _flistDescribeSurvivors(dictByLeg):
    """Return one line per entry a shard failed to kill, named by leg."""
    listSurvivors = []
    for sLeg, dictShards in sorted(dictByLeg.items()):
        for iShard, dictSummary in sorted(dictShards.items()):
            for dictSurvivor in dictSummary.get("listSurvivors", []):
                listSurvivors.append(
                    f"  {sLeg} shard {iShard}: "
                    f"{dictSurvivor.get('sStatus', '?')}  "
                    f"{dictSurvivor.get('sNodeId', '?')}"
                )
    return listSurvivors


def fiSummarizeShards(sDirectory, listExpectations):
    """Print the union's verdict; return the process exit code."""
    dictByLeg = _fdictReadShardSummaries(sDirectory)
    listProblems = _flistDescribeMissingShards(dictByLeg, listExpectations)
    listSurvivors = _flistDescribeSurvivors(dictByLeg)
    for sLeg, _iShards in listExpectations:
        dictShards = dictByLeg.get(sLeg, {})
        iRan = sum(d.get("iRan", 0) for d in dictShards.values())
        iKilled = sum(d.get("iKilled", 0) for d in dictShards.values())
        iDeferred = sum(d.get("iDeferred", 0) for d in dictShards.values())
        print(
            f"{sLeg}: {iKilled}/{iRan} kill-confirmed across "
            f"{len(dictShards)} shard(s)"
            + (f", {iDeferred} not evaluated" if iDeferred else "")
        )
    if listSurvivors:
        print("\nEntries that did NOT kill their mutation:")
        print("\n".join(listSurvivors))
    if listProblems:
        print("\nThe union is incomplete, so it proves nothing:")
        for sProblem in listProblems:
            print("  " + sProblem)
    if listProblems or listSurvivors:
        return 1
    print(
        "\nEvery expected shard reported and every mutation was killed: "
        "the union of the shards IS the standing negative control."
    )
    return 0


def main():
    """Parse the arguments and exit with the union's verdict."""
    parser = argparse.ArgumentParser(
        description=(
            "Add the falsification shard summaries up and refuse to "
            "report a total when a shard is missing."
        ),
    )
    parser.add_argument(
        "--directory", required=True,
        help="Directory holding the downloaded shard artifacts.",
    )
    parser.add_argument(
        "--expect", dest="listExpect", action="append", default=[],
        metavar="OS:PYTHON:COUNT", required=True,
        help=(
            "A leg that must report, and how many shards it must "
            "report (repeatable). Declared rather than discovered: a "
            "count read from whatever arrived can never notice that "
            "nothing did."
        ),
    )
    args = parser.parse_args()
    sys.exit(fiSummarizeShards(
        args.directory,
        [_tParseExpectation(s) for s in args.listExpect],
    ))


if __name__ == "__main__":
    main()
