"""Check the connection behind one deposit download, on request.

One browser-only GET. It names an acquisition -- a download the hub has
on disk, live or stopped -- by the id its directory carries, reads that
download's sidecar, and runs the bounded probes in
:mod:`vaibify.reproducibility.connectionCheck`. The route changes no
state and takes no URL from the caller: the file's address and the
service come from the sidecar vaibify wrote, the id is a bare directory
name validated as one, and the check reaches only the recorded Zenodo
service and the DOI resolver. It reads host state and makes network
requests from the host, so the in-container agent lane is refused.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import os

from fastapi import HTTPException, Request

from ...reproducibility import connectionCheck, downloadPartial
from ..routeContext import fnRejectAgentTokenLane

_S_ACQUISITION_PREFIX = "acquisition-"


def _fdictRequireSidecar(sAcquisitionId):
    """Return the sidecar of a download this host holds, or raise 404."""
    if (
        os.path.basename(sAcquisitionId) != sAcquisitionId
        or not sAcquisitionId.startswith(_S_ACQUISITION_PREFIX)
    ):
        raise HTTPException(404, "No download is held under that id.")
    for dictPartial in downloadPartial.flistDescribePartials():
        if dictPartial["sAcquisitionId"] != sAcquisitionId:
            continue
        if dictPartial.get("dictSidecar") is None:
            raise HTTPException(
                409, "This download's record could not be read, so there is "
                     "nothing to check its connection against.",
            )
        return dictPartial["dictSidecar"]
    raise HTTPException(404, "No download is held under that id.")


def _fnRegisterConnectionCheck(app, dictCtx):
    """Register GET /api/connection-check/{sAcquisitionId}."""
    del dictCtx

    @app.get("/api/connection-check/{sAcquisitionId}")
    async def fdictCheckTheConnection(sAcquisitionId: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        dictSidecar = _fdictRequireSidecar(sAcquisitionId)
        try:
            return await asyncio.to_thread(
                connectionCheck.fdictProbeArchiveConnection, dictSidecar,
            )
        except ValueError as error:
            raise HTTPException(
                409, f"This download names a service vaibify cannot check: {error}",
            ) from None


def fnRegisterAll(app, dictCtx):
    """Register the connection-check route."""
    _fnRegisterConnectionCheck(app, dictCtx)
