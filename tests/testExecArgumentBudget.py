"""A batched container probe must not outgrow one exec argument.

Both badge probes hand their whole path list to the daemon inside ONE
argument — the existence probe embeds it as a Python literal, the
blob-sha probe appends it as a here-string — and Linux caps a single
argument at 128 KB. Measured against a real daemon on 2026-08-30:

* ``flistContainerPathsExist`` RAISED at 1,845 paths of 59 bytes. That
  raise happens inside a carrier worker, which poisons the journal
  record, so opening such a project would QUARANTINE the container.
* ``fdictComputeBlobShasInContainer`` failed SILENTLY at 2,562 paths of
  47 bytes, answering ``{}`` — so every badge downstream was computed
  from an empty hash map and rendered as fact.

These tests pin the batching that removed both. They drive fakes,
because what needs guarding is the SPLIT — that the adapters stop
handing an unbounded list to one exec — and a fake is the only way to
observe the per-exec payload. The live confirmation that a real daemon
now answers past the old wall is a scratchpad script, deliberately not
a suite test: it needs a daemon and six thousand files.
"""

import json

import pytest

from vaibify.docker.execArgumentBudget import (
    I_EXEC_ARGUMENT_BUDGET_BYTES,
    flistBatchPathsForOneExec,
)


S_CONTAINER_ID = "argbudget_cid"


def _flistBuildPaths(iCount, iBytes=59):
    """Return iCount distinct paths of roughly iBytes each."""
    listPaths = []
    for iIndex in range(iCount):
        sSuffix = str(iIndex).rjust(6, "0")
        sPath = "step/results/run" + sSuffix + "/"
        listPaths.append(sPath + "x" * max(1, iBytes - len(sPath)))
    return listPaths


# -----------------------------------------------------------------------
# The splitter itself
# -----------------------------------------------------------------------


def testEveryBatchFitsTheBudget():
    """No batch may exceed the budget, or the split bought nothing."""
    listPaths = _flistBuildPaths(6000)
    for listBatch in flistBatchPathsForOneExec(listPaths):
        iBytes = sum(len(sPath) + 4 for sPath in listBatch)
        assert iBytes <= I_EXEC_ARGUMENT_BUDGET_BYTES, (
            f"a batch of {len(listBatch)} paths renders {iBytes} bytes, "
            f"over the {I_EXEC_ARGUMENT_BUDGET_BYTES} budget"
        )


def testTheSplitLosesAndReordersNothing():
    """Concatenating the batches must reproduce the input exactly.

    Order is load-bearing: the existence probe zips its answers back
    onto the paths that produced them, so a reordering silently
    reports one file's state under another file's name.
    """
    listPaths = _flistBuildPaths(6000)
    listRejoined = [
        sPath for listBatch in flistBatchPathsForOneExec(listPaths)
        for sPath in listBatch
    ]
    assert listRejoined == listPaths


def testAnOversizedPathIsCarriedNotDropped():
    """The splitter decides where to split, never what to omit."""
    sHuge = "x" * (I_EXEC_ARGUMENT_BUDGET_BYTES * 2)
    listBatches = flistBatchPathsForOneExec(["a.txt", sHuge, "b.txt"])
    listRejoined = [s for listBatch in listBatches for s in listBatch]
    assert listRejoined == ["a.txt", sHuge, "b.txt"]


def testAShortListStaysOneBatch():
    """The common case must not pay for the pathological one."""
    listBatches = flistBatchPathsForOneExec(_flistBuildPaths(29))
    assert len(listBatches) == 1


# -----------------------------------------------------------------------
# The existence probe
# -----------------------------------------------------------------------


class _ConnectionRecordingTypedReads:
    """A DockerConnection whose typed reads record their payload size."""

    def __init__(self):
        self.listBatchSizes = []

    def _ftRunTypedRead(self, sContainerId, sOperation, objPaths):
        from vaibify.docker.dockerConnection import ExecResult
        listPaths = list(objPaths)
        self.listBatchSizes.append(
            sum(len(sPath) + 4 for sPath in listPaths)
        )
        return ExecResult(
            iExitCode=0,
            sStdout=json.dumps([True] * len(listPaths)),
            sStderr="",
        )


@pytest.mark.falsification
def testTheExistenceProbeSplitsInsteadOfOverflowingOneExec():
    """Every exec the probe issues must stay inside the budget.

    Kills: reverting flistContainerPathsExist to one _ftRunTypedRead
    over the whole list, which sends ~350 KB in a single argument and
    raised "argument list too long" against a real daemon.
    """
    from vaibify.docker.dockerConnection import DockerConnection
    connectionFake = _ConnectionRecordingTypedReads()
    listPaths = _flistBuildPaths(6000)
    listAnswers = DockerConnection.flistContainerPathsExist(
        connectionFake, S_CONTAINER_ID, listPaths,
    )
    assert len(listAnswers) == len(listPaths), (
        "the probe lost answers across the batch boundary"
    )
    assert len(connectionFake.listBatchSizes) > 1, (
        "6,000 paths went out in one exec, which is the payload that "
        "raised against a real daemon"
    )
    assert max(connectionFake.listBatchSizes) <= (
        I_EXEC_ARGUMENT_BUDGET_BYTES
    ), (
        "one exec carried "
        f"{max(connectionFake.listBatchSizes)} bytes of path text"
    )


# -----------------------------------------------------------------------
# The blob-sha probe
# -----------------------------------------------------------------------


class _ConnectionRecordingCommands:
    """A connection recording each exec command, with a canned answer."""

    def __init__(self, iFailOnCall=-1):
        self.listCommandLengths = []
        self.iFailOnCall = iFailOnCall

    def ftResultExecuteCommand(self, sContainerId, sCommand,
                               sWorkdir=None):
        self.listCommandLengths.append(len(sCommand))
        if len(self.listCommandLengths) == self.iFailOnCall:
            return (1, "")
        # Recover the paths from the here-string so the answer matches
        # the batch actually asked about.
        sJson = sCommand.rsplit("<<< ", 1)[1].strip()
        if sJson.startswith("'") and sJson.endswith("'"):
            sJson = sJson[1:-1].replace("'\\''", "'")
        listPaths = json.loads(sJson)
        return (0, json.dumps({s: "a" * 40 for s in listPaths}))


@pytest.mark.falsification
def testTheBlobShaProbeSplitsInsteadOfOverflowingOneExec():
    """The here-string is part of argv, so it needs the same budget.

    Kills: reverting fdictComputeBlobShasInContainer to a single exec
    over the whole list, which stopped answering — silently — at ~2,562
    paths against a real daemon.
    """
    from vaibify.gui import containerGit
    connectionFake = _ConnectionRecordingCommands()
    listPaths = _flistBuildPaths(6000, iBytes=47)
    dictHashes = containerGit.fdictComputeBlobShasInContainer(
        connectionFake, S_CONTAINER_ID, listPaths, sWorkspace="/repo",
    )
    assert len(dictHashes) == len(listPaths), (
        "the probe lost hashes across the batch boundary"
    )
    assert len(connectionFake.listCommandLengths) > 1, (
        "6,000 paths went out in one exec, which is the payload that "
        "silently emptied against a real daemon"
    )


@pytest.mark.falsification
def testAFailedBatchEmptiesTheWholeAnswerRatherThanHalfOfIt():
    """A partial hash map is the shape that put wrong badges on screen.

    A caller cannot tell a half-filled map from "those files could not
    be read", and the badge renderer treats a missing hash as a fact
    about the file. So one failed batch must collapse the whole call.

    Kills: having fdictComputeBlobShasInContainer skip a failed batch
    and return the batches that worked.
    """
    from vaibify.gui import containerGit
    connectionFake = _ConnectionRecordingCommands(iFailOnCall=2)
    dictHashes = containerGit.fdictComputeBlobShasInContainer(
        connectionFake, S_CONTAINER_ID, _flistBuildPaths(6000, 47),
        sWorkspace="/repo",
    )
    assert dictHashes == {}, (
        "a failed batch left a partial map, which reads downstream as "
        f"a claim about every file it omits: {len(dictHashes)} entries"
    )
