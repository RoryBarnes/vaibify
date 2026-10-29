"""Hold a name's lifecycle lock in a separate, real process.

The lifecycle lock is a cross-process ``flock``, so the only honest way
to prove an entry point respects it is to have ANOTHER process hold it.
The child takes the lock through the production module, with the lock
directory the parent's suite redirected, prints ``held`` and blocks on
its standard input; closing that input releases the lock by ending the
process.
"""

import contextlib
import os
import subprocess
import sys


S_HOLDER_PROGRAM = """
import sys
sys.path.insert(0, sys.argv[1])
from vaibify.config import containerLock
containerLock._S_LIFECYCLE_LOCK_DIRECTORY = sys.argv[2]
fileHandle = containerLock.ffileTakeLifecycleLock(sys.argv[3], sys.argv[4])
print("held", flush=True)
sys.stdin.read()
"""


def fsRepositoryRoot():
    """Return the checkout the suite is running, for the child's path."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.contextmanager
def fcontextHoldLifecycleLockInAnotherProcess(sName, sOperation):
    """Yield while a child process holds ``sName``'s lifecycle lock."""
    from vaibify.config import containerLock
    processHolder = subprocess.Popen(
        [sys.executable, "-c", S_HOLDER_PROGRAM, fsRepositoryRoot(),
         containerLock.fsGetLifecycleLockDirectory(), sName, sOperation],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    try:
        sFirstLine = processHolder.stdout.readline().strip()
        assert sFirstLine == "held", processHolder.stderr.read()
        yield processHolder
    finally:
        processHolder.stdin.close()
        processHolder.wait(timeout=10)
