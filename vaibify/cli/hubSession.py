"""Researcher-lane client for a live vaibify hub.

The host CLI acts as the RESEARCHER, exactly as the browser does, and
authenticates the same way the browser does — through the capability
bootstrap, headlessly (design §6b, slice 8):

1. mint an ORDINARY launch capability over the hub's peer-authenticated
   host control socket (``mint-bootstrap``), which no container and no
   remote peer can reach, so possession proves a human at this host;
2. redeem it once at ``/api/bootstrap`` for a per-browser credential,
   presented thereafter in the ``X-Session-Token`` header;
3. claim the container for a LEASE, exactly as a browser tab does —
   never a transfer, so a dashboard owner is never displaced;
4. attach BOTH the credential and the lease to every owner-scoped
   request (:func:`ftSendSessionRequest`, the single place that does
   it, so no call site can forget again);
5. release the lease when the command finishes, on every path.

It never presents the per-container agent token and never rides the
agent lane, so ``bAgentSafe`` (which governs what a compromised
in-container agent may do) places no restriction on it.

The pipeline WebSocket discriminates lanes by ``Origin``: a loopback
origin plus the credential plus the owning lease is the browser gate; a
non-loopback origin can only be the in-container agent. The CLI is a
loopback client acting for the researcher, so it presents all three and
never takes the agent's lease-exempt path.

Exclusivity is honest in both directions: a container already claimed by
an open dashboard tab answers this client's claim with 409 — one session
per container, and the way through is the in-container agent lane, not a
takeover — and this client releases its lease when the command finishes.
"""

import json

import click


S_BROWSER_TOKEN_HEADER = "X-Session-Token"
S_LEASE_HEADER = "X-Vaibify-Lease"
# Where a launch capability becomes a per-browser credential. The same
# endpoint the dashboard posts its URL-fragment capability to; this
# client's capability arrives over the host control socket instead.
S_BOOTSTRAP_ENDPOINT = "/api/bootstrap"
F_DEFAULT_TIMEOUT_SECONDS = 300.0
F_BOOTSTRAP_TIMEOUT_SECONDS = 30.0
_T_TERMINAL_EVENT_TYPES = (
    "completed", "error", "pipelineError", "runRefused",
)


class HubSessionError(RuntimeError):
    """A live hub could not be reached, claimed, or connected."""


def flistFindLiveHubSessions():
    """Return the live HUB session slots that bound a port.

    Only a hub registers the claim / release / registry routes
    (``appFactory._fnRegisterHubLifecycle``), so a viewer session — one
    project, its own lease minted at connect — cannot serve this client
    and is not a candidate.
    """
    from vaibify.config.sessionRegistry import flistReadAllSlots
    return [
        dictSlot for dictSlot in flistReadAllSlots()
        if dictSlot.get("iPort") and dictSlot.get("sRole") == "hub"
    ]


def fiResolveHubPort(iPort=None):
    """Return the port of the hub to drive.

    An explicit port wins. Otherwise the single live hub is used; zero
    or several are an error rather than a guess, because picking the
    wrong hub would drive the wrong researcher's session. The port —
    not the URL — is the primitive, because the host control socket is
    keyed by hub port exactly as the session-registry slot is.
    """
    if iPort:
        return int(iPort)
    listSessions = flistFindLiveHubSessions()
    if not listSessions:
        raise HubSessionError(
            "No live vaibify hub found. Start one with 'vaibify' in "
            "another terminal, or pass --port."
        )
    if len(listSessions) > 1:
        sPorts = ", ".join(
            str(dictSlot.get("iPort")) for dictSlot in listSessions
        )
        raise HubSessionError(
            "Several vaibify hubs are live (ports %s); "
            "choose one with --port." % sPorts
        )
    return int(listSessions[0]["iPort"])


def fsResolveHubBaseUrl(iPort=None):
    """Return the loopback base URL of the hub to drive."""
    return "http://127.0.0.1:%d" % fiResolveHubPort(iPort)


def _fjsonParseResponseBody(response):
    """Return the parsed JSON body, or the raw text when not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text


def ftSendHttpRequest(
    sBaseUrl, sCredential, sMethod, sPath, dictFields=None,
    fTimeoutSeconds=F_DEFAULT_TIMEOUT_SECONDS, dictQuery=None,
    sLeaseId="",
):
    """Return ``(iStatusCode, jsonBody)`` for one researcher-lane call.

    The credential is set in exactly one place. A caller that merges it
    in twice sends ``"<credential>, <credential>"``, which is not a
    credential the hub knows and is correctly refused 401. GET fields
    ride the query string because a GET body is non-portable and
    FastAPI binds primitives from the query.
    ``dictQuery`` is for the control-plane routes that declare their
    parameters as query values on a POST — connect's ``sWorkflowPath``.
    ``sLeaseId``, when set, rides the ``X-Vaibify-Lease`` header (never a
    query param, which would leak into logs): claim's re-claim lease,
    release's owning lease, connect's owning lease.
    """
    import requests
    dictHeaders = (
        {S_BROWSER_TOKEN_HEADER: sCredential} if sCredential else {}
    )
    if sLeaseId:
        dictHeaders[S_LEASE_HEADER] = sLeaseId
    dictKeywords = {}
    if dictQuery:
        dictKeywords["params"] = dict(dictQuery)
    if dictFields:
        if sMethod == "GET":
            dictKeywords["params"] = dict(
                dictKeywords.get("params", {}), **dictFields
            )
        else:
            dictKeywords["json"] = dictFields
    try:
        response = requests.request(
            sMethod, sBaseUrl + sPath, headers=dictHeaders,
            timeout=(fTimeoutSeconds or None), **dictKeywords
        )
    except requests.RequestException as error:
        raise HubSessionError(
            "vaibify hub unreachable at %s: %s" % (sBaseUrl, error)
        )
    return response.status_code, _fjsonParseResponseBody(response)


def _fjsonRequireOkResponse(tResponse, sWhat):
    """Return the body of a 2xx response, or raise with the hub's detail."""
    iStatusCode, jsonBody = tResponse
    if 200 <= iStatusCode < 300:
        return jsonBody
    sDetail = jsonBody
    if isinstance(jsonBody, dict):
        sDetail = jsonBody.get("detail", jsonBody)
    raise HubSessionError(
        "%s failed (HTTP %d): %s" % (sWhat, iStatusCode, sDetail)
    )


def ftSendSessionRequest(
    dictSession, sMethod, sPath, dictFields=None,
    fTimeoutSeconds=F_DEFAULT_TIMEOUT_SECONDS, dictQuery=None,
):
    """Send one OWNER-SCOPED request: the credential AND the lease, always.

    Every container-scoped route — every mutation, and every read
    carrying a ``{sContainerId}`` segment — is authorized by the strong
    predicate: a live browser credential PLUS the lease bound to that
    session. Sending only the credential is a 403 that reads like a
    server fault, which is precisely how this lane shipped broken, so
    the attachment lives here rather than at each call site.
    """
    return ftSendHttpRequest(
        dictSession["sBaseUrl"], dictSession["sCredential"], sMethod,
        sPath, dictFields, fTimeoutSeconds, dictQuery=dictQuery,
        sLeaseId=dictSession["sLeaseId"],
    )


def fsRequestBootstrapCapability(iHubPort):
    """Return a launch capability minted over the hub's control socket.

    Host-lane only: the socket lives in a ``0700`` directory, is
    peer-authenticated to the hub's own uid, and is unreachable from
    any container, so a capability handed back here proves nothing more
    than that this process runs as the researcher who started the hub.
    """
    from vaibify.gui.hostControlChannel import (
        HostControlError,
        S_SOCKET_OPERATION_MINT_BOOTSTRAP,
        fdictSendHostControlRequest,
    )
    try:
        dictMinted = fdictSendHostControlRequest(
            iHubPort, {"sOperation": S_SOCKET_OPERATION_MINT_BOOTSTRAP},
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        )
    except HostControlError as error:
        raise HubSessionError(
            "Could not reach the host control socket of the vaibify hub "
            "on port %d: %s" % (iHubPort, error)
        )
    if not dictMinted.get("bAccepted") or not dictMinted.get("bMinted"):
        raise HubSessionError(
            "The hub refused to mint a host-lane credential: %s"
            % dictMinted.get("sError", "no reason given")
        )
    return dictMinted["sBootstrapCapability"]


def fsRedeemHostLaneCredential(iHubPort, sBaseUrl):
    """Mint a capability over the socket and redeem it for a credential.

    The headless equivalent of the browser's launch: the capability
    never touches a file, a log, or a query string — it travels over
    the Unix socket and then in the JSON body of one POST — and the
    credential it returns is an ordinary per-browser session, holding
    no container until this client claims one.
    """
    sCapability = fsRequestBootstrapCapability(iHubPort)
    jsonBody = _fjsonRequireOkResponse(
        ftSendHttpRequest(
            sBaseUrl, "", "POST", S_BOOTSTRAP_ENDPOINT,
            {"sCapability": sCapability}, F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Browser-credential bootstrap",
    )
    sCredential = (jsonBody or {}).get("sCredential", "")
    if not sCredential:
        raise HubSessionError(
            "Hub at %s redeemed the launch capability but returned no "
            "credential." % sBaseUrl
        )
    return sCredential


def fsResolveContainerId(sBaseUrl, sCredential, sContainerName):
    """Return the resource id the hub addresses this project by.

    A container project's id is its running Docker id, and asking for
    one that is not running earns the start hint. A HOST project's
    resource id IS its registry name (host-mode plan §9): nothing
    needs to be "running" first — there is no container, no readiness
    wait, and telling a researcher to ``vaibify start --detach`` a
    project that never wanted Docker was Phase D's found defect.
    """
    jsonBody = _fjsonRequireOkResponse(
        ftSendHttpRequest(
            sBaseUrl, sCredential, "GET", "/api/registry", None,
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Container listing",
    )
    for dictContainer in (jsonBody or {}).get("listContainers", []):
        if dictContainer.get("sName") != sContainerName:
            continue
        if dictContainer.get("sMode") == "host":
            return sContainerName
        sContainerId = dictContainer.get("sContainerId") or ""
        if not sContainerId:
            raise HubSessionError(
                "Container '%s' is not running. Start it with: "
                "vaibify start --detach" % sContainerName
            )
        return sContainerId
    raise HubSessionError(
        "The hub does not know a container named '%s'."
        % sContainerName
    )


def fsClaimContainer(sBaseUrl, sCredential, sContainerName):
    """Claim the container and return the lease the hub minted.

    A container someone else holds is refused by the hub with 409, and
    that refusal is surfaced with the way out rather than worked
    around: this client claims like any browser and must never transfer
    or revoke a dashboard owner to authenticate itself.
    """
    iStatusCode, jsonBody = ftSendHttpRequest(
        sBaseUrl, sCredential, "POST",
        "/api/registry/%s/claim" % sContainerName, None,
        F_BOOTSTRAP_TIMEOUT_SECONDS,
    )
    if iStatusCode == 409:
        raise HubSessionError(
            fsExplainClaimConflict(sContainerName, jsonBody),
        )
    jsonBody = _fjsonRequireOkResponse(
        (iStatusCode, jsonBody), "Container claim",
    )
    sLeaseId = (jsonBody or {}).get("sLeaseId", "")
    if not sLeaseId:
        raise HubSessionError(
            "Claim of '%s' returned no lease." % sContainerName
        )
    return sLeaseId


# The way through when a dashboard holds the container. One browser
# session per container is the model (design §9), so the CLI cannot
# simply take it — but the in-container agent lane exists precisely to
# act INSIDE a live dashboard session, which is why it is named here
# rather than leaving the researcher with a bare 409.
S_AGENT_LANE_POINTER = (
    "Vaibify allows one session per container, so this command cannot "
    "take it from a live dashboard. Either close the dashboard tab "
    "holding it (or release the container from the picker) and run "
    "this again, or run the same action from inside the container "
    "with 'vaibify-do', the in-container agent lane, which acts within "
    "the session that is already open."
)


def fsExplainClaimConflict(sContainerName, objDetail):
    """Return the message for a 409 claim, with the way out named.

    The hub's own reason is always surfaced first — it is the honest
    fact. The agent-lane pointer is appended only for the in-use
    family of refusals: a poisoned record names ``vaibify reconcile``
    as its recovery and a cardinality refusal names the container this
    session already holds, and for those the agent lane is not the
    answer.
    """
    jsonBody = objDetail.get("detail", objDetail) if isinstance(
        objDetail, dict,
    ) else objDetail
    dictBody = jsonBody if isinstance(jsonBody, dict) else {}
    sReason = dictBody.get("sMessage") or (
        jsonBody if isinstance(jsonBody, str) else "it is in use"
    )
    sExplanation = (
        "Container '%s' is held by another vaibify session: %s."
        % (sContainerName, sReason)
    )
    if dictBody.get("bPoisoned") or dictBody.get("sHeldContainerName"):
        return sExplanation
    return "%s %s" % (sExplanation, S_AGENT_LANE_POINTER)


def fnReleaseContainer(dictSession):
    """Release the lease this session claimed, and say so if it failed.

    A release that quietly does nothing leaves the container held until
    the ownership reaper notices, and the next command is refused 409
    with no explanation. The hub answers 200 with ``bReleased: false``
    when the lease did not match, so the body — not the status — is the
    thing to check.
    """
    if not dictSession.get("sLeaseId"):
        return
    try:
        iStatusCode, jsonBody = ftSendHttpRequest(
            dictSession["sBaseUrl"], dictSession["sCredential"],
            "POST",
            "/api/registry/%s/release" % dictSession["sContainerName"],
            None, F_BOOTSTRAP_TIMEOUT_SECONDS,
            sLeaseId=dictSession["sLeaseId"],
        )
    except HubSessionError as error:
        click.echo("Warning: lease release failed: %s" % error, err=True)
        return
    if iStatusCode != 200 or not (jsonBody or {}).get("bReleased"):
        click.echo(
            "Warning: the hub still holds '%s'; the next command may "
            "be refused as in use."
            % dictSession["sContainerName"],
            err=True,
        )


def fsSelectWorkflowPath(dictSession, sWorkflowPath=None):
    """Return the project.json path to connect, discovering it if unset.

    The discovery read is container-scoped, so it rides the owner-scoped
    sender: without the lease the hub answers 403 and the researcher
    would be told there is no project when there is.
    """
    if sWorkflowPath:
        return sWorkflowPath
    jsonBody = _fjsonRequireOkResponse(
        ftSendSessionRequest(
            dictSession, "GET",
            "/api/workflows/%s" % dictSession["sContainerId"], None,
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Project discovery",
    )
    if not jsonBody:
        raise HubSessionError(
            "No vaibify project found in the container."
        )
    return jsonBody[0]["sPath"]


def fnConnectWorkflow(dictSession, sWorkflowPath):
    """Load the container's project into the hub's workflow cache."""
    sPath = fsSelectWorkflowPath(dictSession, sWorkflowPath)
    _fjsonRequireOkResponse(
        ftSendSessionRequest(
            dictSession, "POST",
            "/api/connect/%s" % dictSession["sContainerId"],
            None, F_DEFAULT_TIMEOUT_SECONDS,
            dictQuery={"sWorkflowPath": sPath},
        ),
        "Project connect",
    )
    dictSession["sWorkflowPath"] = sPath


def fdictInspectHubSession(sContainerName, iPort=None):
    """Return a read-only session: hub, credential, container id, no lease.

    Nothing here takes the container away from a dashboard tab, which is
    what ``--dry-run`` needs: enough to name the exact call, none of the
    exclusivity that making it would require.
    """
    iHubPort = fiResolveHubPort(iPort)
    sBaseUrl = "http://127.0.0.1:%d" % iHubPort
    sCredential = fsRedeemHostLaneCredential(iHubPort, sBaseUrl)
    return {
        "sBaseUrl": sBaseUrl,
        "iHubPort": iHubPort,
        "sCredential": sCredential,
        "sContainerName": sContainerName,
        "sContainerId": fsResolveContainerId(
            sBaseUrl, sCredential, sContainerName,
        ),
        "sLeaseId": "",
        "sWorkflowPath": "",
    }


def fdictOpenResearcherSession(
    sContainerName, iPort=None, sWorkflowPath=None,
):
    """Discover a hub, authenticate, claim the container, and connect."""
    dictSession = fdictInspectHubSession(sContainerName, iPort)
    dictSession["sLeaseId"] = fsClaimContainer(
        dictSession["sBaseUrl"], dictSession["sCredential"],
        sContainerName,
    )
    try:
        fnConnectWorkflow(dictSession, sWorkflowPath)
    except HubSessionError:
        fnReleaseContainer(dictSession)
        raise
    return dictSession


def fiResolveStepLabel(dictSession, sLabel):
    """Return the 0-based step index the hub assigns to a step label."""
    jsonBody = _fjsonRequireOkResponse(
        ftSendSessionRequest(
            dictSession, "GET",
            "/api/steps/%s/by-label/%s" % (
                dictSession["sContainerId"], sLabel,
            ),
            None, F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Step-label lookup for '%s'" % sLabel,
    )
    return int(jsonBody["iStepIndex"])


def _fnPrintPayload(jsonBody, bJson):
    """Print a response body as one JSON line or an indented block."""
    if not isinstance(jsonBody, (dict, list)):
        click.echo(str(jsonBody))
        return
    if bJson:
        click.echo(json.dumps(jsonBody))
    else:
        click.echo(json.dumps(jsonBody, indent=2, sort_keys=True))


def fiSendHttpAction(
    dictSession, sMethod, sPath, dictFields, bJson, fTimeoutSeconds,
):
    """Perform one HTTP action, print the body, return the exit code.

    Owner-scoped by construction: every generated action addresses a
    container, so the lease rides along with the credential.
    """
    iStatusCode, jsonBody = ftSendSessionRequest(
        dictSession, sMethod, sPath, dictFields, fTimeoutSeconds,
    )
    _fnPrintPayload(jsonBody, bJson)
    if 200 <= iStatusCode < 300:
        return 0
    if 400 <= iStatusCode < 500:
        return 1
    return 2


def _fsBuildPipelineSocketUrl(dictSession):
    """Return the pipeline WebSocket URL carrying the credential and lease."""
    from urllib.parse import quote
    sWebSocketBase = dictSession["sBaseUrl"].replace("http://", "ws://", 1)
    return "%s/ws/pipeline/%s?sToken=%s&sLeaseId=%s" % (
        sWebSocketBase,
        quote(dictSession["sContainerId"], safe=""),
        quote(dictSession["sCredential"], safe=""),
        quote(dictSession["sLeaseId"], safe=""),
    )


def _fnPrintPipelineEvent(dictEvent, bJson):
    """Print one pipeline event as JSON or a short human line."""
    if bJson:
        click.echo(json.dumps(dictEvent))
        return
    listParts = ["[%s]" % dictEvent.get("sType", "event")]
    for sKey in ("iStep", "sStepName", "sLine", "sMessage", "iExitCode"):
        if sKey in dictEvent:
            listParts.append("%s=%s" % (sKey, dictEvent[sKey]))
    click.echo(" ".join(listParts))


def fiStreamPipelineAction(
    dictSession, dictPayload, bJson, fTimeoutSeconds,
):
    """Send one action on the pipeline socket and stream its events.

    Presents the loopback origin, the browser credential, and the
    owning lease — the browser gate, all three. Returns the process exit
    code: the run's own exit code on completion, 1 on refusal or error.
    """
    from websockets.sync.client import connect
    from websockets.exceptions import WebSocketException
    try:
        with connect(
            _fsBuildPipelineSocketUrl(dictSession),
            additional_headers={"Origin": dictSession["sBaseUrl"]},
            open_timeout=F_BOOTSTRAP_TIMEOUT_SECONDS,
            max_size=None,
        ) as connection:
            connection.send(json.dumps(dictPayload))
            return _fiStreamUntilTerminalEvent(
                connection, bJson, fTimeoutSeconds,
            )
    except (WebSocketException, OSError) as error:
        raise HubSessionError(
            "pipeline socket failed: %s" % error
        )


def _fiStreamUntilTerminalEvent(connection, bJson, fTimeoutSeconds):
    """Print events until a terminal one arrives; return the exit code."""
    while True:
        sMessage = connection.recv(timeout=(fTimeoutSeconds or None))
        try:
            dictEvent = json.loads(sMessage)
        except (ValueError, TypeError):
            continue
        if dictEvent.get("sType") == "wsHeartbeat":
            continue
        _fnPrintPipelineEvent(dictEvent, bJson)
        sType = dictEvent.get("sType", "")
        if sType == "completed":
            return int(dictEvent.get("iExitCode", 0) or 0)
        if sType in _T_TERMINAL_EVENT_TYPES:
            return 1
