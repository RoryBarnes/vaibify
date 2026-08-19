"""API routes for host-global user preferences."""

__all__ = [
    "HostWarningAcknowledgedRequest",
    "IdleTimeoutRequest",
    "fnRegisterAll",
]

import math
import os

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config import preferencesStore
from .. import serverLifespan
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY,
    ffnDeclareCarrierMode,
)


class HostWarningAcknowledgedRequest(BaseModel):
    sProjectDirectory: str


class IdleTimeoutRequest(BaseModel):
    sValue: str


def _fdictDescribeIdleTimeout(appState):
    """Describe the live idle timeout for the Settings control.

    The effective value is read from ``app.state`` (the same source the
    watchdog reads every tick), falling back to a fresh resolution when
    the watchdog has not published one yet. ``math.inf`` is reported as
    ``bNever`` with a null second-count so the response stays JSON-safe.
    """
    fEffective = getattr(appState, "fIdleTimeoutSeconds", None)
    if fEffective is None:
        fEffective = serverLifespan._ffResolveIdleTimeoutSeconds()
    bNever = fEffective is None or math.isinf(fEffective)
    return {
        "bNever": bNever,
        "fSeconds": None if bNever else fEffective,
        "sStoredPreference": (
            preferencesStore.fsIdleTimeoutPreference() or None
        ),
        "bEnvOverride": (
            serverLifespan._ffIdleTimeoutFromEnvironment() is not None
        ),
    }


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


def _fnRegisterGetIdleTimeout(app, dictCtx):
    """Register GET /api/preferences/idle-timeout (live effective value)."""

    @app.get("/api/preferences/idle-timeout")
    async def fdictGetIdleTimeout(requestHttp: Request):
        return _fdictDescribeIdleTimeout(requestHttp.app.state)


def _fnRegisterPutIdleTimeout(app, dictCtx):
    """Register PUT /api/preferences/idle-timeout (persist + apply live)."""

    # separate-authority: the write lands in ~/.vaibify/preferences.json
    # on the researcher's machine — host state outside any container, so
    # the commit-guard carrier does not govern it (mirrors the
    # host-warning write above). The value is validated by the shared
    # timeout parser before it is persisted, then the effective timeout is
    # re-resolved and published on app.state so the watchdog picks it up
    # on its next tick without a relaunch. Re-resolving (not writing the
    # posted value directly) preserves the env override's precedence: a
    # VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS in force still wins live.
    @app.put("/api/preferences/idle-timeout")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictPutIdleTimeout(
        request: IdleTimeoutRequest,
        requestHttp: Request,
    ):
        sValue = (request.sValue or "").strip()
        if serverLifespan._ffParseIdleTimeoutSeconds(sValue) is None:
            raise HTTPException(
                400,
                "Idle timeout must be a non-negative number of seconds "
                "or 'never'.",
            )
        preferencesStore.fnRecordIdleTimeoutPreference(sValue)
        requestHttp.app.state.fIdleTimeoutSeconds = (
            serverLifespan._ffResolveIdleTimeoutSeconds()
        )
        return _fdictDescribeIdleTimeout(requestHttp.app.state)


def fnRegisterAll(app, dictCtx):
    """Register all preferences routes."""
    _fnRegisterGetPreferences(app, dictCtx)
    _fnRegisterHostWarningAcknowledged(app, dictCtx)
    _fnRegisterGetIdleTimeout(app, dictCtx)
    _fnRegisterPutIdleTimeout(app, dictCtx)
