"""Wall-clock guards that measure the work, not the runner's load.

A handful of tests assert that an operation finishes inside a time
budget. What they actually defend is algorithmic: an accidentally
quadratic traversal, a regex that backtracks catastrophically. Wall
clock is a proxy for that, and on a shared CI runner it is a noisy one
in exactly one direction — being descheduled adds time, nothing ever
gives it back — so a single sample measures the work plus whatever the
runner was doing instead of it.

That is not hypothetical. On 2026-07-28 a 500-step strip that takes
~0.3 ms locally reported 633 ms on one ubuntu-22.04 leg, three orders of
magnitude above the work it does, and failed a 0.5 s threshold on a
commit that changed one JavaScript comment and one test constant.
Eleven sibling legs passed the same assertion in the same run.

Taking the fastest of several samples drops that one-directional noise
without touching the threshold: a real regression still fails, because
no amount of repetition makes slow code fast, while a busy neighbour no
longer does.
"""

import time

I_DEFAULT_SAMPLE_COUNT = 5


def ffMeasureFastestRun(
    fnOperation, iSamples=I_DEFAULT_SAMPLE_COUNT,
    fnResetBetweenSamples=None,
):
    """Return the shortest wall-clock seconds across ``iSamples`` runs.

    ``fnResetBetweenSamples`` runs before each sample and is not itself
    timed.

    Anything memoized MUST reset here. A cached second call measures the
    cache rather than the operation, and the minimum across such samples
    approaches zero — which would leave the assertion passing for any
    regression whatsoever, a guard that is worse than no guard because
    it still reads as coverage.
    """
    listElapsed = []
    for _iSample in range(iSamples):
        if fnResetBetweenSamples is not None:
            fnResetBetweenSamples()
        fStart = time.perf_counter()
        fnOperation()
        listElapsed.append(time.perf_counter() - fStart)
    return min(listElapsed)
