"""Host-side exception ring buffer keyed by container id.

A multi-day pipeline that dies on the host (browser WS crash, runner
exception, lifespan hook failure) leaves only a stack trace in
``~/.vaibify/vaibify.log`` — invisible to a Claude instance running
inside the container. This module exposes a process-wide ring buffer
that the reconciler consults so the cause-of-death can be stamped into
the container-readable ``pipeline_state.json``.

Capture path:

1. Every host log call that knows the container id should pass
   ``extra={"sContainerId": cid}`` so the handler tags the record.
2. :class:`HostIncidentHandler` is attached alongside the existing
   ``RotatingFileHandler`` in :mod:`vaibify.cli.main` and appends an
   incident dict to the per-container deque.
3. :func:`fdictLatestIncidentForContainer` returns the most recent
   incident, which the pipeline-state reconciler stamps into
   ``sFailureCauseHost``.

The ring is bounded per container (``I_MAX_INCIDENTS_PER_CONTAINER``)
so a noisy run cannot grow memory without bound.
"""

__all__ = [
    "I_MAX_INCIDENTS_PER_CONTAINER",
    "HostIncidentHandler",
    "fdictLatestIncidentForContainer",
    "flistIncidentsForContainer",
    "fnEvictHostIncidentsForContainer",
    "fnRecordHostIncident",
    "fnResetHostIncidents",
]

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional


I_MAX_INCIDENTS_PER_CONTAINER = 50


_dictHostIncidents: Dict[str, Deque[dict]] = {}
_lockHostIncidents = threading.Lock()


def fnResetHostIncidents():
    """Clear all recorded incidents (test-only helper)."""
    with _lockHostIncidents:
        _dictHostIncidents.clear()


def fnEvictHostIncidentsForContainer(sContainerId):
    """Drop the per-container deque for ``sContainerId``.

    Called by the periodic cache sweep so the outer dict does not grow
    one bucket per container forever across multi-week host uptimes.
    Safe to call when no bucket exists (no-op).
    """
    if not sContainerId:
        return
    with _lockHostIncidents:
        _dictHostIncidents.pop(sContainerId, None)


def _fdequeBucketForContainer(sContainerId):
    """Return (creating if needed) the bounded deque for sContainerId."""
    dequeBucket = _dictHostIncidents.get(sContainerId)
    if dequeBucket is None:
        dequeBucket = deque(maxlen=I_MAX_INCIDENTS_PER_CONTAINER)
        _dictHostIncidents[sContainerId] = dequeBucket
    return dequeBucket


def fnRecordHostIncident(sContainerId, dictIncident):
    """Append an incident dict to the per-container deque."""
    if not sContainerId or not dictIncident:
        return
    with _lockHostIncidents:
        _fdequeBucketForContainer(sContainerId).append(dictIncident)


def flistIncidentsForContainer(sContainerId):
    """Return a snapshot list of incidents for sContainerId (oldest first)."""
    with _lockHostIncidents:
        dequeBucket = _dictHostIncidents.get(sContainerId)
        if dequeBucket is None:
            return []
        return list(dequeBucket)


def fdictLatestIncidentForContainer(sContainerId) -> Optional[dict]:
    """Return the most recent incident dict for sContainerId, or None."""
    listIncidents = flistIncidentsForContainer(sContainerId)
    return listIncidents[-1] if listIncidents else None


def _fdictBuildIncidentFromRecord(record):
    """Convert a LogRecord into the incident dict the reconciler reads."""
    sExceptionRepr = ""
    if record.exc_info and record.exc_info[1] is not None:
        sExceptionRepr = repr(record.exc_info[1])
    return {
        "sIso": datetime.now(timezone.utc).isoformat(),
        "sLevel": record.levelname,
        "sLogger": record.name,
        "sMessage": record.getMessage(),
        "sExceptionRepr": sExceptionRepr,
    }


class HostIncidentHandler(logging.Handler):
    """Logging handler that captures records tagged with sContainerId.

    Mounted alongside the existing rotating file handler in
    :mod:`vaibify.cli.main`. Records that carry a non-empty
    ``sContainerId`` attribute (supplied by callers via
    ``extra={"sContainerId": cid}``) are appended to the per-container
    ring; untagged records are silently ignored so unrelated host
    chatter cannot pollute a container's incident history.
    """

    def emit(self, record):
        sContainerId = getattr(record, "sContainerId", "") or ""
        if not sContainerId:
            return
        try:
            dictIncident = _fdictBuildIncidentFromRecord(record)
            fnRecordHostIncident(sContainerId, dictIncident)
        except Exception:
            self.handleError(record)
