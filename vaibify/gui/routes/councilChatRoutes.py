"""Ask-the-chairbot HTTP routes: the council's conversation lane.

A conversation is not a protocol turn — it resolves no round and writes
no campaign state — but it IS paid provider work over the project's
copied login inside a council runner, so it passes every gate a turn
passes: the browser-only refusal, the container-only refusal, the
identity match, the credential gate against the project's resolved
immutable image, the live login probe, and the release authority's
admission close. Those gates are the shared
``vaibify.gui.councilRouteGuards`` module — one copy, two route skins —
because this module and ``councilRoutes.py`` may not import each other
(sibling route imports are banned).

Split out of ``councilRoutes.py`` on 2026-08-26: the conversation lane
registers its own commands, owns its own session lifecycle module
(``agentCouncilChat``), and changes for its own reasons (clocks,
transcript bounds, reaper) — a genuine fault line, once the guards it
shared stopped being another route module's privates.

Ask returns as soon as the message is recorded and the answer lands on
a background task; the panel polls the read route for it. An answer can
take minutes, and a request held open that long is at the mercy of
every proxy between the browser and the hub.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .. import agentCouncilController
from .. import agentCouncilDockerGateway
from .. import councilRouteGuards
from ..routeScope import (
    ffnDeclareCarrierMode,
    S_CARRIER_SEPARATE_AUTHORITY,
)


class CouncilChatAskRequest(BaseModel):
    """Body for one ask-the-chairbot message."""

    sQuestionText: str = Field(
        min_length=1, max_length=councilRouteGuards.I_MAX_RESPONSE_LENGTH)


def _fnRegisterChairbotChat(app, dictCtx):
    """Register the four ask-the-chairbot routes (the conversation lane)."""

    @app.get("/api/agent-councils/{sContainerId}/{sCampaignId}/chat")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReadChairbotChat(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        from .. import agentCouncilChat
        sName, sProjectRepoPath = councilRouteGuards.ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory, sCampaignId)
        councilRouteGuards.fjsonRequireCampaign(
            councilRouteGuards.fdictCampaignStore(requestHttp), sCampaignId,
            sName, sProjectRepoPath)
        return agentCouncilChat.fdictDescribeChatSession(
            councilRouteGuards.fdictControllerState(requestHttp), sCampaignId)

    @app.post("/api/agent-councils/{sContainerId}/{sCampaignId}/chat/open")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictOpenChairbotChat(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = councilRouteGuards.ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory, sCampaignId)
        dictStore = councilRouteGuards.fdictCampaignStore(requestHttp)
        dictControllerState = councilRouteGuards.fdictControllerState(
            requestHttp)

        async def _fdictExecuteOpenChat():
            jsonCampaign = councilRouteGuards.fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            _fnRefuseChatWhenAdmissionClosed(dictControllerState, sName)
            # The same two gates start passes, in the same order and for
            # the same reasons: the image resolves first so the evidence
            # record's pin is always compared, and the login presence
            # probe runs before a runner exists so an absent login reads
            # as "log in" rather than as a failed conversation.
            sImageReference = await councilRouteGuards.ffnBuildImageResolver(
                dictCtx, sContainerId)()
            councilRouteGuards.fnRefuseRunnerBackendUnlessEnabled(
                sImageReference)
            await asyncio.to_thread(
                councilRouteGuards.fnRefuseStartWithoutAProjectLogin,
                dictCtx, sContainerId)
            return await _fdictOpenChatMapped(dictControllerState, {
                "sCampaignId": sCampaignId,
                "sResourceName": sName,
                "dictCampaign": jsonCampaign,
                "dictStore": dictStore,
                "dictRegistry": councilRouteGuards.fdictCouncilRegistry(
                    requestHttp),
                "sImageReference": sImageReference,
                "ftStageRunnerCredential":
                    councilRouteGuards.ffnBuildCredentialStager(
                        dictCtx, sContainerId),
            })

        return await councilRouteGuards.fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_OPEN_CHAT,
            _fdictExecuteOpenChat)

    @app.post("/api/agent-councils/{sContainerId}/{sCampaignId}/chat/ask")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictAskChairbot(
        sContainerId: str, sCampaignId: str,
        request: CouncilChatAskRequest, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        from .. import agentCouncilChat
        sName, sProjectRepoPath = councilRouteGuards.ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory, sCampaignId)
        dictStore = councilRouteGuards.fdictCampaignStore(requestHttp)
        dictControllerState = councilRouteGuards.fdictControllerState(
            requestHttp)

        async def _fdictExecuteAsk():
            councilRouteGuards.fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            _fnRefuseChatWhenAdmissionClosed(dictControllerState, sName)
            try:
                return await agentCouncilChat.fdictAskChatQuestion(
                    dictControllerState, sCampaignId, request.sQuestionText)
            except agentCouncilChat.CouncilChatError as error:
                raise HTTPException(409, str(error))

        return await councilRouteGuards.fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_ASK_CHAIRBOT,
            _fdictExecuteAsk)

    @app.post("/api/agent-councils/{sContainerId}/{sCampaignId}/chat/close")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictCloseChairbotChat(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        from .. import agentCouncilChat
        sName, sProjectRepoPath = councilRouteGuards.ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory, sCampaignId)
        dictStore = councilRouteGuards.fdictCampaignStore(requestHttp)
        dictControllerState = councilRouteGuards.fdictControllerState(
            requestHttp)

        async def _fdictExecuteCloseChat():
            councilRouteGuards.fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            # NOT gated on admission: closing is how a researcher makes
            # a released project releasable, so refusing it there would
            # leave the only exit behind the gate it opens.
            dictSettled = await agentCouncilChat.fdictCloseChatSession(
                dictControllerState, sCampaignId)
            if not dictSettled["bSettled"]:
                raise HTTPException(
                    409,
                    "the conversation's runner could not be proven gone "
                    f"({dictSettled['sReason']}); it stays visible as a "
                    "quarantined runner until vaibify reconcile settles "
                    "it. Retry the close.")
            return dictSettled

        return await councilRouteGuards.fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_CLOSE_CHAT,
            _fdictExecuteCloseChat)


def _fnRefuseChatWhenAdmissionClosed(dictControllerState, sName):
    """Refuse a conversation for a container whose lease was released."""
    if agentCouncilController.fbResourceAdmissionIsClosed(
            dictControllerState, sName):
        raise HTTPException(
            409, "the project lease was released; claim the project "
            "again before talking to its council")


async def _fdictOpenChatMapped(dictControllerState, dictOpenRequest):
    """Open a conversation, mapping its refusals onto HTTP.

    A ``CouncilChatError`` is a refusal decided before anything was
    built (no admission, a corrupt chairbot record) and answers 409 in
    the chat module's own words; a gateway or daemon fault is a 502,
    because it is the machine that failed, not the request.
    """
    from .. import agentCouncilChat
    try:
        return await agentCouncilChat.fdictOpenChatSession(
            dictControllerState, dictOpenRequest)
    except agentCouncilChat.CouncilChatError as error:
        raise HTTPException(409, str(error))
    except agentCouncilDockerGateway.CouncilGatewayError as error:
        raise HTTPException(
            502, f"the chairbot's runner could not be built: {error}")


def fnRegisterAll(app, dictCtx):
    """Register the ask-the-chairbot conversation routes."""
    _fnRegisterChairbotChat(app, dictCtx)
