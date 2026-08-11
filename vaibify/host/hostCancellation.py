"""Terminating the process groups vaibify journaled for a host project.

A container run is cancelled by asking the container what is running:
the process table inside it belongs to vaibify, so pattern-matching it
is safe there. A host run has no such table of its own — the process
table is the researcher's whole machine — so the only thing vaibify may
signal is a process group it recorded when it started one, and only
while that record's identity is still provable. Pattern-matching the
host process table would kill an unrelated editor that happened to be
running the same script name.

That is the whole content of this module, and it is deliberately its
own: two surfaces need it (the Cancel button on the pipeline, and the
quarantine view that offers to terminate the run it has just named for
the researcher), and both must make the SAME identity judgement. The
judgement is the recycle-proof one the journal's own probe uses —
holder PID still alive, with a start clock no later than the moment the
record went in flight.

**A record whose identity cannot be proven is never signalled.** A PID
that has vanished may have been handed to something else, and a process
group leader is exactly the shape of process that can inherit a
recycled group id (every gated launch here makes one). So an
unprovable record is REPORTED — routed to reconciliation, which can
settle it with the human in the loop — never guessed at. The cost of
being wrong in the other direction is killing a stranger's process with
the researcher's own authority.

What a successful termination proves is host mode's weaker claim
(host-mode plan decision 7): *every process vaibify started has
exited* — never "nothing is running". A command that called ``setsid``
left the recorded group and nothing here can see it, which is why
``bGroupProvedEmpty`` is reported rather than assumed.
"""

__all__ = [
    "fdictCancelJournaledHostRun",
    "fnTerminateProcessGroup",
    "fbProcessGroupProvedEmpty",
    "S_CANCEL_OUTCOME_TERMINATED",
    "S_CANCEL_OUTCOME_ALREADY_EXITED",
    "S_CANCEL_OUTCOME_REFUSED",
    "F_TERMINATE_GRACE_SECONDS",
]

import os
import signal
import time

from vaibify.config import operationJournal
from vaibify.config.processLiveness import fbIsUsablePid

F_TERMINATE_GRACE_SECONDS = 2.0

S_CANCEL_OUTCOME_TERMINATED = "terminated"
S_CANCEL_OUTCOME_ALREADY_EXITED = "already-exited"
S_CANCEL_OUTCOME_REFUSED = "refused"

_S_REASON_IDENTITY_UNPROVABLE = (
    "the recorded process no longer matches its journaled identity, so "
    "its process group cannot be signalled without risking an unrelated "
    "process; run 'vaibify reconcile' to settle this record"
)


def fdictCancelJournaledHostRun(sResourceName):
    """Terminate every provable host-exec group this project recorded.

    Returns the three outcomes separately because they mean different
    things to a researcher: work that was stopped, work that had
    already finished, and work vaibify refuses to touch. A refusal is
    never collapsed into the count — a Cancel that silently declined to
    signal, and reported "0 processes", is the dashboard lying about
    the state of the machine.

    Records are left in the journal. The launcher settles its own
    record when its wait returns, and a record nobody is waiting on
    belongs to reconciliation; settling one from here would race the
    thread still holding the process.
    """
    dictOutcome = {
        "iGroupsTerminated": 0,
        "listTerminated": [],
        "listAlreadyExited": [],
        "listRefused": [],
    }
    for dictHolder in operationJournal.flistDescribeHostExecHolders(
        sResourceName,
    ):
        _fnApplyCancelToOneHolder(dictHolder, dictOutcome)
    dictOutcome["iGroupsTerminated"] = len(dictOutcome["listTerminated"])
    return dictOutcome


def _fnApplyCancelToOneHolder(dictHolder, dictOutcome):
    """Signal, skip, or refuse one journaled holder, recording which."""
    iProcessGroup = dictHolder["iHolderProcessGroup"]
    if dictHolder["bHolderProven"]:
        fnTerminateProcessGroup(iProcessGroup)
        dictOutcome["listTerminated"].append({
            **dictHolder,
            "sOutcome": S_CANCEL_OUTCOME_TERMINATED,
            "bGroupProvedEmpty": fbProcessGroupProvedEmpty(iProcessGroup),
        })
        return
    if fbProcessGroupProvedEmpty(iProcessGroup):
        dictOutcome["listAlreadyExited"].append({
            **dictHolder,
            "sOutcome": S_CANCEL_OUTCOME_ALREADY_EXITED,
        })
        return
    dictOutcome["listRefused"].append({
        **dictHolder,
        "sOutcome": S_CANCEL_OUTCOME_REFUSED,
        "sReason": _S_REASON_IDENTITY_UNPROVABLE,
    })


def fnTerminateProcessGroup(iProcessGroup):
    """TERM then KILL the recorded group; tolerate an already-empty one.

    ``PermissionError`` returns rather than escalating: a group vaibify
    may not signal is one it did not start as this user, and the honest
    answer is to leave it alone.

    The usable-pid guard is load-bearing HERE in a way it was not when
    this lived beside the launch that produced the number: a group id
    read back out of a journal file can be ``0`` or ``None``, and
    ``os.killpg(0, SIGKILL)`` signals the CALLER's own process group —
    the hub would kill itself.
    """
    if not fbIsUsablePid(iProcessGroup):
        return
    for iSignalNumber, fGraceSeconds in (
        (signal.SIGTERM, F_TERMINATE_GRACE_SECONDS),
        (signal.SIGKILL, 0.0),
    ):
        try:
            os.killpg(iProcessGroup, iSignalNumber)
        except (ProcessLookupError, PermissionError):
            return
        fDeadline = time.monotonic() + fGraceSeconds
        while time.monotonic() < fDeadline:
            if fbProcessGroupProvedEmpty(iProcessGroup):
                return
            time.sleep(0.05)


def fbProcessGroupProvedEmpty(iProcessGroup):
    """Return True only when no process remains in the group.

    ``PermissionError`` is False, not True: a group that answers "not
    yours to signal" is a group that exists. An unusable group id is
    also False — "no proof" is not "proved empty".
    """
    if not fbIsUsablePid(iProcessGroup):
        return False
    try:
        os.killpg(iProcessGroup, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False
