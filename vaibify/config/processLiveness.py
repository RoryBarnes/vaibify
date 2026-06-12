"""Safe PID-liveness probe shared by the lock and slot registries.

``os.kill(iPid, 0)`` delivers no signal but reports whether the
target process exists. ``PermissionError`` means the process exists
under another user, so it counts as alive. This probe is the
fallback that breaks claims whose flock outlived the recorded
holder (for example, a lock file descriptor leaked into a
surviving descendant of a killed vaibify server).
"""

__all__ = ["fbIsProcessAlive"]

import os


def fbIsProcessAlive(iPid):
    """Return True when a process with the given PID currently exists."""
    if not isinstance(iPid, int) or isinstance(iPid, bool) or iPid <= 0:
        return False
    try:
        os.kill(iPid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
