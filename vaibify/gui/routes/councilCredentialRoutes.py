"""Council credential consent and test routes (contracts A3 and A5).

The researcher's consent is what lets a council copy their provider
login into a runner, so every route here — the mutations AND the reads
— carries four independent layers:

1. the browser credential, checked by the session middleware;
2. the container's lease: ``{sContainerId}`` is in every path, so
   ``ContainerAwareRoute`` applies the bound-lease authority
   (``container-owner`` for mutations, ``container-read`` for reads);
3. the mutations and the cancel are in
   ``actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS``, so the agent lane
   is refused by the middleware before any handler runs; and
4. every handler refuses the agent-token lane itself.

What that guarantees is exactly this and no more: records are created,
changed or withdrawn only by a request from an authenticated vaibify
browser session holding the container's lease. It is NOT proof that a
human clicked, and nothing here may claim it is. It excludes agents in
containers and local processes without a browser credential.

The image id is resolved server-side from the project container, never
taken from a request; bodies carry only providers (and, for providers
whose models cannot be listed, the model id to test with). No token, no
token digest and no credential path appears in any response.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .. import agentCouncilCredentialGate
from .. import agentCouncilCredentialStore
from .. import agentCouncilCredentialTest
from .. import agentCouncilCredentialTestRecords
from .. import agentCouncilProviderRegistry
from .. import councilRouteGuards
from ..routeScope import (
    ffnDeclareCarrierMode,
    S_CARRIER_SEPARATE_AUTHORITY,
)

S_CREDENTIAL_TESTS_CONTEXT_KEY = "dictCouncilCredentialTests"
I_MAX_MODEL_LENGTH = 200


class CredentialTestProviderRequest(BaseModel):
    """One provider to consent to and test."""

    sProvider: str = Field(min_length=1, max_length=64)
    sRequestedModel: str = Field(default="", max_length=I_MAX_MODEL_LENGTH)


class CredentialTestRequest(BaseModel):
    """Body for the consent action: the providers the researcher ticked."""

    listProviders: list[CredentialTestProviderRequest] = Field(
        min_length=1, max_length=len(
            agentCouncilProviderRegistry.SET_COUNCIL_PROVIDERS))


def _fdictJobsInProcess(dictCtx):
    """Return this hub's in-process job table, creating it once.

    Kept in the route context, which lives exactly as long as the app,
    so a job started by one request can be cancelled by another.
    """
    return dictCtx.setdefault(S_CREDENTIAL_TESTS_CONTEXT_KEY, {})


def _fsRequireKnownProvider(sProvider):
    """Refuse a provider outside the closed council vocabulary."""
    if sProvider not in agentCouncilProviderRegistry.SET_COUNCIL_PROVIDERS:
        raise HTTPException(
            400, f"{sProvider!r} is not a council provider")
    return sProvider


def _fsResolveTestModel(requestProvider):
    """Return the model a test runs with, or refuse when none is known."""
    sModel = requestProvider.sRequestedModel.strip() or (
        agentCouncilCredentialTest.DICT_DEFAULT_TEST_MODELS.get(
            requestProvider.sProvider, ""))
    if not sModel:
        raise HTTPException(
            400, f"{requestProvider.sProvider}: name the model id to test "
            "with; vaibify cannot list this provider's models")
    return sModel


def _fdictRequireJobOfContainer(sJobId, sContainerId):
    """Return a job record that belongs to this container, else 404.

    A job belonging to another container answers exactly like an
    unknown id, so a lease on one project learns nothing about another.
    """
    try:
        dictJob = agentCouncilCredentialTestRecords.fdictReadJobRecord(sJobId)
    except ValueError:
        dictJob = None
    if dictJob is None or dictJob.get("sContainerId") != sContainerId:
        raise HTTPException(404, f"no credential test '{sJobId}'")
    return dictJob


def _fdictDescribeJob(dictJob):
    """Return the job fields a browser may read (no staged paths)."""
    return {sKey: dictJob.get(sKey) for sKey in (
        "sJobId", "sProvider", "sStatus", "sCurrentCheck", "sFailedCheck",
        "sDetail", "listChecks", "sStartedIso", "sFinishedIso",
        "sCliVersion", "fTurnTimeoutSeconds", "sRequestedModel")}


def _fnRegisterStartTest(app, dictCtx):
    """Register POST .../credential-test (the consent action)."""

    @app.post("/api/council-credentials/{sContainerId}/credential-test")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictConsentAndStartCredentialTest(
        sContainerId: str, request: CredentialTestRequest,
        requestHttp: Request,
    ):
        sName = councilRouteGuards.fsGuardCouncilRoute(
            dictCtx, requestHttp, sContainerId)
        listRequested = [
            (_fsRequireKnownProvider(requestProvider.sProvider),
             _fsResolveTestModel(requestProvider))
            for requestProvider in request.listProviders]
        sImageIdentity = await councilRouteGuards.ffnBuildImageResolver(
            dictCtx, sContainerId)()
        await asyncio.to_thread(
            councilRouteGuards.fnRefuseStartWithoutAProjectLogin, dictCtx,
            sContainerId, {sProvider for sProvider, _ in listRequested})
        listJobs = []
        for sProvider, sModel in listRequested:
            dictStarted = await asyncio.to_thread(
                _fdictStartOneTest, dictCtx, requestHttp, sContainerId,
                sName, sProvider, sImageIdentity, sModel)
            listJobs.append({"sProvider": sProvider, **dictStarted})
        return {"listJobs": listJobs}


def _fdictStartOneTest(dictCtx, requestHttp, sContainerId, sName, sProvider,
                       sImageIdentity, sModel):
    """Build the production runtime and start one provider's job."""
    from .. import agentCouncilDockerGateway
    dictRuntime = agentCouncilCredentialTest.fdictBuildJobRuntime(
        dictCtx["docker"],
        agentCouncilDockerGateway.fdockerCreateCouncilClient(),
        councilRouteGuards.fdictCouncilRegistry(requestHttp), sName,
        sContainerId)
    return agentCouncilCredentialTest.fdictStartCredentialTest(
        _fdictJobsInProcess(dictCtx), sProvider, sImageIdentity, sName,
        sModel, dictRuntime)


def _fnRegisterReadTest(app, dictCtx):
    """Register GET .../credential-test/{sJobId} (progress)."""

    @app.get("/api/council-credentials/{sContainerId}/credential-test/"
             "{sJobId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReadCredentialTest(
        sContainerId: str, sJobId: str, requestHttp: Request,
    ):
        councilRouteGuards.fsGuardCouncilRoute(
            dictCtx, requestHttp, sContainerId)
        return _fdictDescribeJob(
            _fdictRequireJobOfContainer(sJobId, sContainerId))


def _fnRegisterCancelTest(app, dictCtx):
    """Register POST .../credential-test/{sJobId}/cancel."""

    @app.post("/api/council-credentials/{sContainerId}/credential-test/"
              "{sJobId}/cancel")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictCancelCredentialTest(
        sContainerId: str, sJobId: str, requestHttp: Request,
    ):
        councilRouteGuards.fsGuardCouncilRoute(
            dictCtx, requestHttp, sContainerId)
        _fdictRequireJobOfContainer(sJobId, sContainerId)
        bCancelled = await asyncio.to_thread(
            agentCouncilCredentialTest.fbRequestCredentialTestCancel,
            _fdictJobsInProcess(dictCtx), sJobId)
        if not bCancelled:
            raise HTTPException(
                409, "this credential test is not running on this hub")
        return {"bCancelRequested": True}


def _fnRegisterWithdraw(app, dictCtx):
    """Register DELETE .../credential-consent/{sProvider}."""

    @app.delete("/api/council-credentials/{sContainerId}/"
                "credential-consent/{sProvider}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictWithdrawCredentialConsent(
        sContainerId: str, sProvider: str, requestHttp: Request,
    ):
        councilRouteGuards.fsGuardCouncilRoute(
            dictCtx, requestHttp, sContainerId)
        _fsRequireKnownProvider(sProvider)
        sImageIdentity = await councilRouteGuards.ffnBuildImageResolver(
            dictCtx, sContainerId)()
        dictWithdrawn = await asyncio.to_thread(
            agentCouncilCredentialStore.fdictWithdrawConsent,
            agentCouncilCredentialGate.fsResolveCredentialEvidencePath(),
            sProvider, sImageIdentity)
        return {"bWithdrawn": dictWithdrawn is not None,
                "dictProvider": await asyncio.to_thread(
                    _fdictDescribeProvider, dictCtx, sContainerId,
                    sProvider, sImageIdentity)}


def _fnRegisterPanel(app, dictCtx):
    """Register GET .../panel (the Credential tests panel)."""

    @app.get("/api/council-credentials/{sContainerId}/panel")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReadCredentialPanel(
        sContainerId: str, requestHttp: Request,
    ):
        councilRouteGuards.fsGuardCouncilRoute(
            dictCtx, requestHttp, sContainerId)
        sImageIdentity = await councilRouteGuards.ffnBuildImageResolver(
            dictCtx, sContainerId)()
        return await asyncio.to_thread(
            _fdictComposePanel, dictCtx, sContainerId, sImageIdentity)


def _fdictComposePanel(dictCtx, sContainerId, sImageIdentity):
    """Return the panel: each provider's state, and who shares the image."""
    return {
        "sImageIdentity": sImageIdentity,
        "listProjectsSharingImage": _flistProjectsSharingImage(
            dictCtx, sImageIdentity),
        "listProviders": [
            _fdictDescribeProvider(dictCtx, sContainerId, sProvider,
                                   sImageIdentity)
            for sProvider in sorted(
                agentCouncilProviderRegistry.SET_COUNCIL_PROVIDERS)],
        "listChecks": [{"sCheckId": sCheckId, "sLabel": sLabel}
                       for sCheckId, sLabel
                       in agentCouncilCredentialTestRecords.LIST_CHECKS],
    }


def _flistProjectsSharingImage(dictCtx, sImageIdentity):
    """Return the running projects on this computer using this image."""
    return sorted(
        dictContainer["sName"]
        for dictContainer in dictCtx["docker"].flistGetRunningContainers()
        if dictContainer.get("sImageIdentity") == sImageIdentity)


def _fdictDescribeProvider(dictCtx, sContainerId, sProvider, sImageIdentity):
    """Return one provider's consent, latest outcome and login presence."""
    dictEvaluation = agentCouncilCredentialStore.fdictEvaluateCredentialKey(
        agentCouncilCredentialStore.fdictReadCredentialDocument(
            agentCouncilCredentialGate.fsResolveCredentialEvidencePath()
        )["dictDocument"], sProvider, sImageIdentity)
    dictConsent = dictEvaluation["dictConsent"] or {}
    dictOutcome = dictEvaluation["dictOutcome"] or {}
    return {
        "sProvider": sProvider,
        "sState": dictEvaluation["sState"],
        "bAuthorized": dictEvaluation["bAuthorized"],
        "sReason": ("" if dictEvaluation["bAuthorized"] else
                    agentCouncilCredentialGate.fsExplainCredentialState(
                        dictEvaluation)),
        "sConsentState": dictConsent.get("sState", ""),
        "bConsentImplied": bool(dictConsent.get("bImplied")),
        "dictLatestOutcome": {sKey: dictOutcome.get(sKey, "") for sKey in (
            "sOutcome", "sFinishedIso", "sFailedCheck", "sDetail",
            "sVerificationMethod", "sCliVersion")},
        "sRunningJobId": (dictEvaluation["dictInFlight"] or {}).get(
            "sJobId", ""),
        "sLoginProblem": _fsExplainLoginProblem(
            dictCtx, sContainerId, sProvider),
        "sDefaultTestModel":
            agentCouncilCredentialTest.DICT_DEFAULT_TEST_MODELS.get(
                sProvider, ""),
        "sLastTestedModel": (list(dictOutcome.get("listModelIds") or [])
                             or [""])[0],
    }


def _fsExplainLoginProblem(dictCtx, sContainerId, sProvider):
    """Return why the project's login cannot be copied, or ""."""
    try:
        councilRouteGuards.fnRefuseStartWithoutAProjectLogin(
            dictCtx, sContainerId, {sProvider})
    except HTTPException as error:
        return str(error.detail)
    return ""


def fnRegisterAll(app, dictCtx):
    """Register every council credential route."""
    _fnRegisterStartTest(app, dictCtx)
    _fnRegisterReadTest(app, dictCtx)
    _fnRegisterCancelTest(app, dictCtx)
    _fnRegisterWithdraw(app, dictCtx)
    _fnRegisterPanel(app, dictCtx)
