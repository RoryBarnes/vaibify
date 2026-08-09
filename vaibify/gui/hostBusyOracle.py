"""The host busy oracle: is a host project's pipeline running right now?

A host pipeline is running iff its durable-task record is live
in-process OR its journaled ``host-exec`` process group still has
members (host-mode plan §4). The container twin of this question is a
``docker exec`` process probe; a host project's truth is never in
Docker, so every busy veto — the claim arbiter's take-over veto, the
ownership reaper, the idle self-shutdown watchdog — must ask THIS
predicate for host resources. One function, three consumers, so the
vetoes cannot drift apart: the divergence where one veto sees a live
run and another evicts over it is the bug this module exists to
prevent.

The two halves fail safe independently: the durable-task read is an
in-process dict lookup, and the journal half treats an unreadable
journal or an unprobeable identity as live (see
``operationJournal.fbAnyHostExecHolderLive``).
"""

__all__ = ["fbHostProjectHasLiveRun"]

from vaibify.config import operationJournal


def fbHostProjectHasLiveRun(appState, sName):
    """Return True while a host project's run is provably or possibly live."""
    from . import commitCarrier
    if commitCarrier.fbContainerHasLiveMutationWork(appState, sName):
        return True
    return operationJournal.fbAnyHostExecHolderLive(sName)
