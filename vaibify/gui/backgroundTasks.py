"""A strong reference for every task nobody awaits.

The event loop holds only a weak reference to a task, so a task created
and dropped can be garbage collected mid-flight, taking its work with it
and raising nothing. Anything scheduled to run on its own (a refresh, a
dependency scan, a fenced socket's close) is handed to
``fnKeepTaskReferenced`` instead of being left to its creator's luck.
"""

__all__ = ["fnKeepTaskReferenced"]

_SET_LIVE_TASKS = set()


def fnKeepTaskReferenced(taskBackground):
    """Hold a strong reference to a task until it ends, as asyncio advises."""
    _SET_LIVE_TASKS.add(taskBackground)
    taskBackground.add_done_callback(_SET_LIVE_TASKS.discard)
