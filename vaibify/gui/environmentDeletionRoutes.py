"""The hub route that permanently deletes one container environment.

Split from ``registryRoutes`` along the same seam ``buildRoutes`` was:
the registry module answers "what projects exist and what containers do
they own", while this module owns the one action that ends a project's
existence on this machine. They change for different reasons -- registry
CRUD versus what a deletion has to destroy and what it must refuse --
and the refusals here (a typed confirmation, the agent lane, a host
project, a busy project) are the module's substance rather than
plumbing around a CRUD write.

The mechanics live one layer down in ``environmentDeletion``; this file
is the HTTP surface and the gate in front of it.
"""

__all__ = ["DeleteEnvironmentRequest", "fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.gui.routeScope import (
    S_CARRIER_LIFECYCLE_TRANSACTION,
    ffnDeclareCarrierMode,
)


class DeleteEnvironmentRequest(BaseModel):
    """Body for ``POST /api/registry/{sName}/delete-environment``.

    ``sConfirmation`` must be the exact phrase
    :func:`environmentDeletion.fsConfirmationPhraseFor` composes for
    this project. It is validated server-side because a confirmation
    only enforced in the dialog is not a confirmation: the route is
    reachable by anything that can reach the hub, and what it does
    cannot be undone.
    """

    sConfirmation: str = ""


def fnRegisterAll(app, dictCtx):
    """Register the environment-deletion route."""
    _fnRegisterDeleteEnvironment(app, dictCtx)


def _fnRegisterDeleteEnvironment(app, dictCtx):
    """Register POST /api/registry/{sName}/delete-environment.

    The irreversible half of the kebab's two removals. "Remove from
    list" (DELETE /api/registry/{sName}) un-registers and keeps every
    byte; this destroys the container, both volumes, every image tag in
    the project's repository, and the entry -- the work the modal used
    to send the researcher to a terminal for.

    lifecycle-transaction, on the same finding as the stop in
    ``registryRoutes``: the authority is ``containerManager``, the
    lifecycle gateway, which holds no ``mutationAdmission`` call, so
    there is no gated primitive here for a carrier to admit and
    declaring one would name an authority that never engages. What
    governs this route is the typed confirmation, the host-project
    refusal, the three-axis busy refusal, and the container-lifecycle
    scope (lease-enforced whenever the container is owned, and
    answerable for an unowned one -- a container nobody can claim is a
    common reason to want the environment gone, so a lease-enforced
    scope would lock the researcher out of the remedy).

    Researcher-only: excluded from the agent catalog AND refused at the
    handler for the agent token lane. An in-container agent asking to
    delete the environment it is running inside is never a request
    vaibify should carry out on the researcher's behalf.
    """

    @app.post("/api/registry/{sName}/delete-environment")
    @ffnDeclareCarrierMode(S_CARRIER_LIFECYCLE_TRANSACTION)
    async def fdictDeleteEnvironment(
        requestHttp: Request, sName: str,
        request: DeleteEnvironmentRequest,
    ):
        from vaibify.gui import environmentDeletion
        from vaibify.gui.registryRoutes import (
            _fdictRequireProject,
            _fnRefuseBusyProject,
            _fnRejectInvalidProjectName,
            _fnReleaseCallerOwnedSession,
        )
        from vaibify.gui.routeContext import (
            fnRefuseContainerOnlyForHostProject,
            fnRejectAgentTokenLane,
        )
        fnRejectAgentTokenLane(requestHttp)
        _fnRejectInvalidProjectName(sName)
        dictProject = _fdictRequireProject(sName)
        fnRefuseContainerOnlyForHostProject(
            sName, "Deleting an environment",
        )
        # Every validator runs BEFORE the caller's own session is
        # released, exactly as the conversion route orders it: a
        # mistyped confirmation must never cost the researcher the
        # project view they are deleting from.
        _fnRequireDeletionConfirmation(sName, request.sConfirmation)
        # The researcher who just looked inside an environment is its
        # owner, so without this their own tab is what refuses the
        # deletion -- a dead end only the command line could finish.
        # A lease a DIFFERENT session bound releases nothing and falls
        # through to the busy refusal below.
        await _fnReleaseCallerOwnedSession(app, sName, requestHttp)
        await _fnRefuseBusyProject(
            app, sName, dictCtx, sVerb="delete",
        )
        dictReport = await asyncio.to_thread(
            environmentDeletion.fdictDeleteEnvironment, dictProject,
        )
        if dictReport["listFailures"]:
            # 500, and the registry entry is still there. The tile
            # stays on screen because the environment is still partly
            # on the daemon -- a tile removed over a failed delete
            # would leave orphaned bytes nothing points at.
            raise HTTPException(500, detail={
                "sMessage": _fsDescribeDeletionFailure(dictReport),
                "dictReport": dictReport,
            })
        return dictReport


def _fnRequireDeletionConfirmation(sName, sConfirmation):
    """400 unless the caller typed this project's exact delete phrase."""
    from vaibify.gui import environmentDeletion
    sExpected = environmentDeletion.fsConfirmationPhraseFor(sName)
    if (sConfirmation or "").strip() != sExpected:
        raise HTTPException(400, detail={"sMessage": (
            f"Deleting '{sName}' requires the exact confirmation "
            f"{sExpected!r}."
        )})


def _fsDescribeDeletionFailure(dictReport):
    """Compose what the dashboard tells the researcher about a part-delete.

    Names what DID go as well as what did not: "delete failed" over a
    removed container and volumes would send the researcher looking for
    an environment that is half gone.
    """
    sFailures = "; ".join(dictReport["listFailures"])
    listRemoved = (
        dictReport["listVolumesRemoved"] + dictReport["listImagesRemoved"]
    )
    sRemoved = (
        " Already removed: "
        + ("the container, " if dictReport["bContainerRemoved"] else "")
        + ", ".join(listRemoved) + "."
    ) if (listRemoved or dictReport["bContainerRemoved"]) else ""
    return (
        f"'{dictReport['sName']}' was not fully deleted: {sFailures}."
        f"{sRemoved} It is still listed, so you can try again."
    )
