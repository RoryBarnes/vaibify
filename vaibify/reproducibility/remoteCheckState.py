"""Which remote checks are in flight right now, per project.

A remote check — the Level 2 verify of the published files against
GitHub, Zenodo, Overleaf or arXiv — reaches the network, so the
dashboard cannot wait for one before it renders. This module is the one
place that knows, for a given resource, whether a service's check is
CHECKING, has SETTLED, or came back UNCHECKABLE. Those three states are
what the poll reports and what the badge renders: a configured remote
pulses until its own check answers, and settles independently of the
others.

The VERDICT is never here. It lives in the project's
``syncStatus.json``, which a failed check must never overwrite —
"vaibify could not reach GitHub" is not a claim about whether the
published copies match, and rendering it as one would be a divergence
alarm over evidence nobody gathered.

State is per hub PROCESS and deliberately not persisted: it answers "is
vaibify asking right now", which stops being true the moment the
process ends.

A CHECKING entry that outlives :data:`F_CHECK_TIMEOUT_SECONDS` reads
back as uncheckable. The expiry is evaluated when the state is READ,
never on a timer, because the failure it guards against is a check that
never returns — and a worker that hung cannot be relied on to clear its
own flag. A late answer still settles the entry, which is honest in
both directions: the badge stopped pulsing because vaibify could not
say, and then it could.
"""

import threading
import time


__all__ = [
    "F_CHECK_TIMEOUT_SECONDS",
    "S_STATE_CHECKING",
    "S_STATE_SETTLED",
    "S_STATE_UNCHECKABLE",
    "S_TIMEOUT_REASON",
    "fbIsCheckInFlight",
    "fdictDescribeChecks",
    "fnForgetResource",
    "fnMarkChecking",
    "fnMarkSettled",
    "fnMarkUncheckable",
]


S_STATE_CHECKING = "checking"
S_STATE_SETTLED = "settled"
S_STATE_UNCHECKABLE = "uncheckable"

# Long enough for a slow remote to answer over a large published set,
# short enough that a badge cannot pulse for the length of a session.
F_CHECK_TIMEOUT_SECONDS = 180.0

S_TIMEOUT_REASON = (
    "the remote did not answer within "
    + str(int(F_CHECK_TIMEOUT_SECONDS)) + " seconds"
)

_DICT_CHECKS_BY_RESOURCE = {}
_LOCK_CHECKS = threading.Lock()


def _fnRecordCheckState(sResourceId, sService, sState, sReason):
    """Store one service's check state for one resource."""
    with _LOCK_CHECKS:
        dictServices = _DICT_CHECKS_BY_RESOURCE.setdefault(
            sResourceId, {},
        )
        dictServices[sService] = {
            "sState": sState,
            "sReason": sReason,
            "fRecordedMonotonic": time.monotonic(),
        }


def fnMarkChecking(sResourceId, sService):
    """Record that a check of sService has started."""
    _fnRecordCheckState(sResourceId, sService, S_STATE_CHECKING, "")


def fnMarkSettled(sResourceId, sService):
    """Record that a check of sService answered.

    The answer itself is the rewritten ``syncStatus.json`` entry; this
    says only that the question was asked and came back, which is what
    stops the badge pulsing.
    """
    _fnRecordCheckState(sResourceId, sService, S_STATE_SETTLED, "")


def fnMarkUncheckable(sResourceId, sService, sReason):
    """Record that a check of sService could not be completed.

    Never a divergence claim: the remote was not compared, so the
    cached record stands untouched and the badge must not go red.
    """
    _fnRecordCheckState(
        sResourceId, sService, S_STATE_UNCHECKABLE,
        sReason or "the reason was not reported",
    )


def fbIsCheckInFlight(sResourceId, sService):
    """Return True when sService's check is running and not timed out."""
    dictCheck = fdictDescribeChecks(sResourceId).get(sService) or {}
    return dictCheck.get("sState") == S_STATE_CHECKING


def fdictDescribeChecks(sResourceId):
    """Return ``{sService: {sState, sReason}}`` for one resource.

    Services this process never started a check for are absent, which
    is how an unconfigured remote avoids pulsing: the poll reports
    nothing about it and the badge renders from the cache alone.
    """
    fNow = time.monotonic()
    with _LOCK_CHECKS:
        dictServices = dict(
            _DICT_CHECKS_BY_RESOURCE.get(sResourceId) or {},
        )
    return {
        sService: _fdictProjectCheck(dictRecord, fNow)
        for sService, dictRecord in dictServices.items()
    }


def _fdictProjectCheck(dictRecord, fNow):
    """Project one stored record onto the wire shape, ageing it out."""
    sState = dictRecord.get("sState") or S_STATE_SETTLED
    fRecorded = dictRecord.get("fRecordedMonotonic")
    fElapsed = fNow - (fNow if fRecorded is None else fRecorded)
    if (sState == S_STATE_CHECKING
            and fElapsed > F_CHECK_TIMEOUT_SECONDS):
        return {
            "sState": S_STATE_UNCHECKABLE,
            "sReason": S_TIMEOUT_REASON,
        }
    return {"sState": sState, "sReason": dictRecord.get("sReason") or ""}


def fnForgetResource(sResourceId):
    """Drop every recorded check for one resource."""
    with _LOCK_CHECKS:
        _DICT_CHECKS_BY_RESOURCE.pop(sResourceId, None)
