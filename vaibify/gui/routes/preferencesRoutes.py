"""API routes for host-global user preferences."""

__all__ = [
    "HostWarningAcknowledgedRequest",
    "fnRegisterAll",
]

import os

from fastapi import HTTPException
from pydantic import BaseModel

from vaibify.config import preferencesStore
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY,
    ffnDeclareCarrierMode,
)


class HostWarningAcknowledgedRequest(BaseModel):
    sProjectDirectory: str


def _fnRegisterGetPreferences(app, dictCtx):
    """Register GET /api/preferences."""

    @app.get("/api/preferences")
    async def fdictGetPreferences():
        return preferencesStore.fdictLoadPreferences()


def _fnRegisterHostWarningAcknowledged(app, dictCtx):
    """Register PUT /api/preferences/host-warning-acknowledged."""

    # separate-authority: the write lands in ~/.vaibify/preferences.json
    # on the researcher's machine — host state outside any container, so
    # the commit-guard carrier does not govern it. What governs it is
    # preferencesStore's exclusive-lock read-modify-write plus the
    # absolute-path refusal below; the directory is deliberately NOT
    # validated against any container root, because a host project
    # lives wherever the researcher put it. Ruling 2026-08-08,
    # mirroring the container-settings YAML write in registryRoutes.
    @app.put("/api/preferences/host-warning-acknowledged")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictPutHostWarningAcknowledged(
        request: HostWarningAcknowledgedRequest,
    ):
        sProjectDirectory = (request.sProjectDirectory or "").strip()
        if not sProjectDirectory:
            raise HTTPException(400, "Project directory is required")
        if not os.path.isabs(sProjectDirectory):
            raise HTTPException(
                400, "Project directory must be an absolute path",
            )
        preferencesStore.fnRecordHostWarningAcknowledged(
            sProjectDirectory,
        )
        return {"bAcknowledged": True}


def fnRegisterAll(app, dictCtx):
    """Register all preferences routes."""
    _fnRegisterGetPreferences(app, dictCtx)
    _fnRegisterHostWarningAcknowledged(app, dictCtx)
