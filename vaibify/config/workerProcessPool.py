"""Run CPU-bound work in spawned worker processes, off the hub.

The hub is one interpreter, and pure-Python work holds its interpreter
lock: run in a hub thread, a long computation starves the event loop
that answers every dashboard request. This module is the one place
under vaibify that starts worker processes for such work, so the
capability lives outside ``vaibify/gui/`` and is named once
(``tests/testCapabilityAuthorities.py``).

It runs a caller's module-level function over a list of argument
tuples and nothing else: no shell, no command text, no path. A worker
is a copy of vaibify's own code in a fresh interpreter, running as the
same user as the hub.
"""

__all__ = ["flistMapInWorkerProcesses"]

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor


def flistMapInWorkerProcesses(fnWorker, listArgumentTuples):
    """Return ``[fnWorker(*tArguments) ...]`` in order, computed in workers.

    ``fnWorker`` must be a module-level function, because a spawned
    worker imports it by name. The pool uses every core but one, never
    more workers than there are argument tuples, and is shut down
    before this returns.

    ``spawn`` rather than the platform default because the hub is
    multi-threaded, and forking a threaded process can copy a lock some
    other thread holds into a child that will never release it.
    """
    if not listArgumentTuples:
        return []
    iWorkers = min(
        len(listArgumentTuples), max(1, (os.cpu_count() or 2) - 1),
    )
    with ProcessPoolExecutor(
        max_workers=iWorkers,
        mp_context=multiprocessing.get_context("spawn"),
    ) as executorPool:
        return list(executorPool.map(fnWorker, *zip(*listArgumentTuples)))
