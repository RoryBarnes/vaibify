"""How many paths fit in one container exec, and where to split.

A batched container probe hands its whole path list to the daemon as
part of ONE argument — the ``python3 -c <program>`` string, whether
the paths are embedded in the program as a literal or appended as a
here-string. Linux caps a single argument at ``MAX_ARG_STRLEN``
(128 KB), so a batch large enough stops working.

Measured on a real daemon, 2026-08-30, with the paths a parameter
sweep actually produces:

* ``flistContainerPathsExist`` **raised** ``argument list too long`` at
  1,845 paths of 59 bytes (~106 KB of path text). Inside a carrier
  worker that raise poisons the journal record, so opening such a
  project would QUARANTINE the container.
* ``fdictComputeBlobShasInContainer`` **failed silently** at 2,562
  paths of 47 bytes (~117 KB), because it answers ``{}`` on a non-zero
  exit — so every badge would be computed from an empty hash map and
  the screen would report the result as fact.

Both are the same wall. This module is the one place that decides
where to split, so a caller batching paths cannot re-derive a
different budget and rediscover the wall on a slightly different
project.

The budget is deliberately well under the kernel's: the rendered
program adds quoting and a template around the paths, and the point of
a margin is to survive the parts this module cannot see. The cost of a
smaller budget is one extra exec per chunk (~0.1s measured), which is
the right trade against a probe that stops working.
"""

__all__ = [
    "I_EXEC_ARGUMENT_BUDGET_BYTES",
    "flistBatchPathsForOneExec",
]


# 64 KB against a measured failure at ~106 KB, i.e. a margin of about
# 1.7x. Not the kernel's 128 KB: the paths are rendered into a program
# whose template, quoting and separators this module does not measure,
# and a budget that only just fits is one that fails on the first
# project with longer paths.
I_EXEC_ARGUMENT_BUDGET_BYTES = 64 * 1024

# Each path costs its own length plus the quotes and separator the
# rendered literal puts around it. Counted rather than ignored so a
# batch of many SHORT paths is bounded too — 10,000 two-character
# paths are 20 KB of text and 60 KB of punctuation.
_I_PER_PATH_OVERHEAD_BYTES = 4


def flistBatchPathsForOneExec(
    listPaths, iBudgetBytes=I_EXEC_ARGUMENT_BUDGET_BYTES,
):
    """Split listPaths into batches that each fit one exec argument.

    Returns a list of lists whose concatenation is ``listPaths`` in the
    original order — order is load-bearing, because the existence
    probe zips its answers back onto the paths that produced them.

    A single path over the budget still gets its own batch rather than
    being dropped: this module decides where to SPLIT, never what to
    omit, and a caller silently missing a path is the failure mode the
    whole exercise exists to remove. Such a batch will fail in the
    container, loudly, which is the honest outcome for a path no exec
    can carry.
    """
    listBatches = []
    listCurrent = []
    iCurrentBytes = 0
    for sPath in listPaths:
        iCost = len(sPath) + _I_PER_PATH_OVERHEAD_BYTES
        if listCurrent and iCurrentBytes + iCost > iBudgetBytes:
            listBatches.append(listCurrent)
            listCurrent = []
            iCurrentBytes = 0
        listCurrent.append(sPath)
        iCurrentBytes += iCost
    if listCurrent:
        listBatches.append(listCurrent)
    return listBatches
