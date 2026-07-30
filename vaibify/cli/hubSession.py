"""Researcher-lane client for a live vaibify hub.

The host CLI acts as the RESEARCHER, exactly as the browser does. It
authenticates with the hub's shared session token in the
``X-Session-Token`` header — fetched from the loopback
``/api/session-token`` endpoint — and it holds the per-claim lease that
the one-session-per-container model makes the exclusivity principal. It
never presents the per-container agent token and never rides the agent
lane, so ``bAgentSafe`` (which governs what a compromised in-container
agent may do) places no restriction on it.

The pipeline WebSocket discriminates lanes by ``Origin``: a loopback
origin plus the shared token plus the owning lease is the browser gate;
a non-loopback origin can only be the in-container agent. The CLI is a
loopback client acting for the researcher, so it presents all three and
never takes the agent's lease-exempt path.

Exclusivity is honest in both directions: a container already claimed by
an open dashboard tab answers this client's claim with 409, and this
client releases its lease when the command finishes.
"""

import json

import click


S_BROWSER_TOKEN_HEADER = "X-Session-Token"
S_LEASE_HEADER = "X-Vaibify-Lease"
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


def fsResolveHubBaseUrl(iPort=None):
    """Return the loopback base URL of the hub to drive.

    An explicit port wins. Otherwise the single live hub is used; zero
    or several are an error rather than a guess, because picking the
    wrong hub would drive the wrong researcher's session.
    """
    if iPort:
        return "http://127.0.0.1:%d" % int(iPort)
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
    return "http://127.0.0.1:%d" % int(listSessions[0]["iPort"])


def _fobjParseResponseBody(response):
    """Return the parsed JSON body, or the raw text when not JSON."""
    try:
        return response.json()
    except ValueError:
        return response.text


def ftSendHttpRequest(
    sBaseUrl, sSessionToken, sMethod, sPath, dictFields=None,
    fTimeoutSeconds=F_DEFAULT_TIMEOUT_SECONDS, dictQuery=None,
    sLeaseId="",
):
    """Return ``(iStatusCode, objBody)`` for one researcher-lane call.

    The session token is set in exactly one place. A caller that merges
    it in twice sends ``"<token>, <token>"``, which is not the hub's
    token and is correctly refused 401. GET fields ride the query string
    because a GET body is non-portable and FastAPI binds primitives from
    the query.
    ``dictQuery`` is for the control-plane routes that declare their
    parameters as query values on a POST — connect's ``sWorkflowPath``.
    ``sLeaseId``, when set, rides the ``X-Vaibify-Lease`` header (never a
    query param, which would leak into logs): claim's re-claim lease,
    release's owning lease, connect's owning lease.
    """
    import requests
    dictHeaders = (
        {S_BROWSER_TOKEN_HEADER: sSessionToken} if sSessionToken else {}
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
    return response.status_code, _fobjParseResponseBody(response)


def _fobjRequireOkResponse(tResponse, sWhat):
    """Return the body of a 2xx response, or raise with the hub's detail."""
    iStatusCode, objBody = tResponse
    if 200 <= iStatusCode < 300:
        return objBody
    sDetail = objBody
    if isinstance(objBody, dict):
        sDetail = objBody.get("detail", objBody)
    raise HubSessionError(
        "%s failed (HTTP %d): %s" % (sWhat, iStatusCode, sDetail)
    )


def fsFetchSessionToken(sBaseUrl):
    """Return the hub's shared session token from the loopback endpoint."""
    objBody = _fobjRequireOkResponse(
        ftSendHttpRequest(
            sBaseUrl, "", "GET", "/api/session-token", None,
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Session-token fetch",
    )
    sToken = (objBody or {}).get("sToken", "")
    if not sToken:
        raise HubSessionError(
            "Hub at %s returned no session token." % sBaseUrl
        )
    return sToken


def fsResolveContainerId(sBaseUrl, sSessionToken, sContainerName):
    """Return the running Docker id the hub knows for a container name."""
    objBody = _fobjRequireOkResponse(
        ftSendHttpRequest(
            sBaseUrl, sSessionToken, "GET", "/api/registry", None,
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Container listing",
    )
    for dictContainer in (objBody or {}).get("listContainers", []):
        if dictContainer.get("sName") != sContainerName:
            continue
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


def fsClaimContainer(sBaseUrl, sSessionToken, sContainerName):
    """Claim the container and return the lease the hub minted.

    A foreign claim is refused by the hub with 409; that refusal is
    surfaced verbatim, because a dashboard tab holding the container is
    a fact the researcher needs to see rather than a condition to work
    around.
    """
    objBody = _fobjRequireOkResponse(
        ftSendHttpRequest(
            sBaseUrl, sSessionToken, "POST",
            "/api/registry/%s/claim" % sContainerName, None,
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Container claim",
    )
    sLeaseId = (objBody or {}).get("sLeaseId", "")
    if not sLeaseId:
        raise HubSessionError(
            "Claim of '%s' returned no lease." % sContainerName
        )
    return sLeaseId


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
        iStatusCode, objBody = ftSendHttpRequest(
            dictSession["sBaseUrl"], dictSession["sSessionToken"],
            "POST",
            "/api/registry/%s/release" % dictSession["sContainerName"],
            None, F_BOOTSTRAP_TIMEOUT_SECONDS,
            sLeaseId=dictSession["sLeaseId"],
        )
    except HubSessionError as error:
        click.echo("Warning: lease release failed: %s" % error, err=True)
        return
    if iStatusCode != 200 or not (objBody or {}).get("bReleased"):
        click.echo(
            "Warning: the hub still holds '%s'; the next command may "
            "be refused as in use."
            % dictSession["sContainerName"],
            err=True,
        )


def fsSelectWorkflowPath(
    sBaseUrl, sSessionToken, sContainerId, sWorkflowPath=None,
):
    """Return the project.json path to connect, discovering it if unset."""
    if sWorkflowPath:
        return sWorkflowPath
    objBody = _fobjRequireOkResponse(
        ftSendHttpRequest(
            sBaseUrl, sSessionToken, "GET",
            "/api/workflows/%s" % sContainerId, None,
            F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Project discovery",
    )
    if not objBody:
        raise HubSessionError(
            "No vaibify project found in the container."
        )
    return objBody[0]["sPath"]


def fnConnectWorkflow(dictSession, sWorkflowPath):
    """Load the container's project into the hub's workflow cache."""
    sPath = fsSelectWorkflowPath(
        dictSession["sBaseUrl"], dictSession["sSessionToken"],
        dictSession["sContainerId"], sWorkflowPath,
    )
    _fobjRequireOkResponse(
        ftSendHttpRequest(
            dictSession["sBaseUrl"], dictSession["sSessionToken"],
            "POST", "/api/connect/%s" % dictSession["sContainerId"],
            None, F_DEFAULT_TIMEOUT_SECONDS,
            dictQuery={"sWorkflowPath": sPath},
            sLeaseId=dictSession["sLeaseId"],
        ),
        "Project connect",
    )
    dictSession["sWorkflowPath"] = sPath


def fdictInspectHubSession(sContainerName, iPort=None):
    """Return a read-only session: hub, token, container id, no lease.

    Nothing here takes the container away from a dashboard tab, which is
    what ``--dry-run`` needs: enough to name the exact call, none of the
    exclusivity that making it would require.
    """
    sBaseUrl = fsResolveHubBaseUrl(iPort)
    sSessionToken = fsFetchSessionToken(sBaseUrl)
    return {
        "sBaseUrl": sBaseUrl,
        "sSessionToken": sSessionToken,
        "sContainerName": sContainerName,
        "sContainerId": fsResolveContainerId(
            sBaseUrl, sSessionToken, sContainerName,
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
        dictSession["sBaseUrl"], dictSession["sSessionToken"],
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
    objBody = _fobjRequireOkResponse(
        ftSendHttpRequest(
            dictSession["sBaseUrl"], dictSession["sSessionToken"],
            "GET",
            "/api/steps/%s/by-label/%s" % (
                dictSession["sContainerId"], sLabel,
            ),
            None, F_BOOTSTRAP_TIMEOUT_SECONDS,
        ),
        "Step-label lookup for '%s'" % sLabel,
    )
    return int(objBody["iStepIndex"])


def _fnPrintPayload(objBody, bJson):
    """Print a response body as one JSON line or an indented block."""
    if not isinstance(objBody, (dict, list)):
        click.echo(str(objBody))
        return
    if bJson:
        click.echo(json.dumps(objBody))
    else:
        click.echo(json.dumps(objBody, indent=2, sort_keys=True))


def fiSendHttpAction(
    dictSession, sMethod, sPath, dictFields, bJson, fTimeoutSeconds,
):
    """Perform one HTTP action, print the body, return the exit code."""
    iStatusCode, objBody = ftSendHttpRequest(
        dictSession["sBaseUrl"], dictSession["sSessionToken"],
        sMethod, sPath, dictFields, fTimeoutSeconds,
    )
    _fnPrintPayload(objBody, bJson)
    if 200 <= iStatusCode < 300:
        return 0
    if 400 <= iStatusCode < 500:
        return 1
    return 2


def _fsBuildPipelineSocketUrl(dictSession):
    """Return the pipeline WebSocket URL carrying the token and lease."""
    from urllib.parse import quote
    sWebSocketBase = dictSession["sBaseUrl"].replace("http://", "ws://", 1)
    return "%s/ws/pipeline/%s?sToken=%s&sLeaseId=%s" % (
        sWebSocketBase,
        quote(dictSession["sContainerId"], safe=""),
        quote(dictSession["sSessionToken"], safe=""),
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

    Presents the loopback origin, the shared session token, and the
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
