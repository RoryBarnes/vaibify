"""The sync epoch must be observable when no run is in flight.

A researcher clicked Verify now, the server compared every published
file, rewrote the cache, and bumped the sync epoch. The badges never
moved. Reading the API by hand showed ``synced``; the page showed
``unknown`` — for twenty minutes, and across a reload.

``iSyncEpoch`` was carried on ONE payload,
``/api/pipeline/{id}/state``, and the poll that reads it
(``fnStartPipelinePolling``) is started only when a run is recovered
live and stopped the moment the run ends. So the dashboard's only
poll-free invalidation signal was unobservable in precisely the
situation it exists for: a researcher acting on an idle project.

This was a latent hole, not a regression. The badge refresh used to
happen unconditionally on every file-status tick; that was removed
because "badge refresh is owned by the sync-epoch-bump path" and the
unconditional call doubled the per-tick container exec load for no
observable benefit. The reasoning was sound and the handoff was
incomplete — the new owner does not run outside a run, so outside a
run nothing refreshed at all.

The fix is to carry the epoch on the file-status payload too, which
polls continuously. The exec saving is kept: the tick still refreshes
only when the epoch actually moved.

Kills (confirmed, not assumed): removing the ``iSyncEpoch`` stamp from
the file-status response fails ``test_the_file_status_payload_carries_
the_sync_epoch``; the frontend half is driven in
``tests/browser/testBadgesRepaintAfterAVerify.py``.
"""

import inspect

import pytest

from vaibify.gui.routes import pipelineRoutes


def test_the_file_status_payload_carries_the_sync_epoch():
    """The bug, at the seam where the signal was unreachable.

    Asserted against the handler source rather than a live response
    because the route needs a full container context; the point is
    that the stamp exists on this payload at all, which is exactly
    what was missing.
    """
    sSource = inspect.getsource(pipelineRoutes)
    iStart = sSource.index("def fresponseHandleGetFileStatus")
    sHandler = sSource[iStart:iStart + 2500]
    assert 'dictResponse["iSyncEpoch"]' in sHandler, (
        "the file-status payload no longer carries iSyncEpoch, so the "
        "only poll that runs outside a live run cannot observe a sync "
        "bump and the badges stop repainting"
    )


def test_the_stamp_lands_after_the_etag_is_computed():
    """Ordering is load-bearing, so it is pinned.

    The ETag already covers the epoch separately. Stamping the body
    BEFORE the ETag call would fold the value into the payload hash as
    well, changing what a 304 means for every client. A bump alters
    the ETag regardless, so the full body that follows always carries
    the fresh value.
    """
    sSource = inspect.getsource(pipelineRoutes)
    iStart = sSource.index("def fresponseHandleGetFileStatus")
    sHandler = sSource[iStart:iStart + 2500]
    iEtag = sHandler.index("sEtag = _fsBuildFileStatusEtag")
    iStamp = sHandler.index('dictResponse["iSyncEpoch"]')
    assert iEtag < iStamp, (
        "iSyncEpoch is stamped onto the body before the ETag is "
        "computed, which folds it into the payload hash and changes "
        "304 semantics for every polling client"
    )


def test_the_pipeline_state_payload_still_carries_it():
    """The original producer must not be traded away for the new one.

    Both polls read the same field; during a live run the pipeline
    poll is the more frequent one, and a researcher who verifies
    mid-run should not be worse off than one who verifies idle.
    """
    sSource = inspect.getsource(pipelineRoutes)
    assert 'dictState["iSyncEpoch"] = iSyncEpoch' in sSource
    assert '{"bRunning": False, "iSyncEpoch": iSyncEpoch}' in sSource
