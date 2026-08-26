"""Ask-the-chairbot conversations over a council campaign's sealed snapshot.

The researcher reads a candidate plan, a held question, or a round of
objections and wants to ask the pen-holder about it. That question is
not a protocol turn: it resolves no round, casts no veto, adopts no
plan and answers no gate. So it does not go through the engine at all —
it goes through here, and the whole of what it shares with a turn is the
containment: a disposable council runner, seeded from the campaign's
SEALED snapshot, reaching the provider only through the campaign's own
allowlisting egress boundary, destroyed with absence proven.

**One runner serves the whole conversation.** A protocol turn creates
and destroys a runner around every single turn; a chat creates one when
the researcher opens the conversation, serves N messages from it, and
destroys it at close. That is affordable here for the reason it is not
affordable there: the completion proof the council depends on
classifies PROTOCOL turns — an indeterminate one interrupts the
campaign — and a chat message classifies nothing. The proof that
matters attaches to the CONTAINER's disposal, and that still happens
exactly once per session, through the same
``fdictDestroyAndSettle`` (quarantine on an unproven absence).

**A persistent runner buys latency, never memory.** Each message is a
fresh headless CLI run inside the container; nothing survives between
them but the files. The conversation's memory is therefore the SERVER'S
transcript, quoted back in full on every message
(``agentCouncilCharter.flistBuildChatQuotedMaterial``). A reply the
server does not quote is a reply the chairbot never said, and the chat
clause tells the model exactly that rather than letting it act as
though it remembers.

**Two clocks bound the credential window.** A turn's login copy is
staged and the host file deleted the instant its tarball is built, so
no token sits at rest on the researcher's disk; that is unchanged here.
What a session-scoped runner widens is the window the copy lives INSIDE
the container — a turn's is one turn long, a conversation's is the
conversation. So the session is bounded twice: idle
(:data:`F_CHAT_IDLE_TIMEOUT_SECONDS`) so a closed browser tab cannot
strand a runner holding a token, and absolute
(:data:`F_CHAT_SESSION_CEILING_SECONDS`) so an open tab left overnight
cannot either. Both are enforced by the reaper on the hub's own clock,
never by the browser: a rule the client enforces is not a bound.

This module touches no Docker SDK — every daemon operation goes through
``agentCouncilDockerGateway``, the single council SDK authority — and
it writes no campaign state. It appends lifecycle EVENTS to the
campaign's ring (opened, asked, answered, closed) and deliberately not
the message text: a chat answer is not part of the plan's provenance,
and dumping prose into the deliberation console would make it look as
though it were.
"""

import asyncio
import logging
import time
import uuid

from . import agentCouncilCharter
from . import agentCouncilContext
from . import agentCouncilDockerGateway
from . import agentCouncilEgress
from . import agentCouncilRunner
from . import agentCouncilStore

logger = logging.getLogger("vaibify")

__all__ = [
    "CouncilChatError",
    "F_CHAT_IDLE_TIMEOUT_SECONDS",
    "F_CHAT_SESSION_CEILING_SECONDS",
    "F_CHAT_TURN_WALL_CLOCK_SECONDS",
    "I_MAX_CHAT_MESSAGES",
    "S_CHAT_SESSIONS_KEY",
    "S_CHAT_STATE_ANSWERING",
    "S_CHAT_STATE_FAILED",
    "S_CHAT_STATE_READY",
    "fbResourceHasChatMessageInFlight",
    "fdictAskChatQuestion",
    "fdictCloseChatSession",
    "fdictDescribeChatSession",
    "fdictOpenChatSession",
    "flistCloseChatSessionsForResource",
    "fiReapExpiredChatSessions",
    "fsComposeChatEgressScope",
]

# The controller-state key the sessions live under. They share that dict
# with the campaign runtimes because they share its LIFETIME (the hub
# process) and its release semantics: whatever settles a campaign's live
# state for a container has to settle its conversations too, and a
# second app-state key would be a second thing to forget.
S_CHAT_SESSIONS_KEY = "dictChatSessionsByCampaign"

# The egress scope a chat's network and proxy are named for. DISTINCT
# from the campaign's own, so a conversation and a deliberation never
# contend for one network name — and derived from the campaign id, so
# the startup sweep can compose it from the durable store exactly as it
# composes the campaign's.
S_CHAT_EGRESS_SCOPE_SUFFIX = "-chat"

S_CHAT_STATE_READY = "ready"
S_CHAT_STATE_ANSWERING = "answering"
S_CHAT_STATE_FAILED = "failed"

# One chat message's wall clock. Far shorter than a deliberation turn's
# hour: a turn explores a repository with tool calls, an answer about
# work already done should not. A model that runs past this is killed
# with its container and the researcher is told which bound it hit.
F_CHAT_TURN_WALL_CLOCK_SECONDS = 600.0

# Idle: no message asked or answered for this long and the runner is
# destroyed. Deliberately shorter than a single message's ceiling would
# suggest, because the thing being bounded is not the model's time but
# the credential copy's residency.
F_CHAT_IDLE_TIMEOUT_SECONDS = 900.0

# Absolute: a conversation somebody keeps warm cannot outlive this,
# however active it is. Without it the idle bound is defeated by a
# browser that polls.
F_CHAT_SESSION_CEILING_SECONDS = 7200.0

# The transcript is quoted IN FULL on every message, so its length is a
# per-message cost, not a one-off. At the bound the session refuses
# further messages and says to close and reopen — truncating the middle
# would leave the chairbot answering from a conversation neither party
# had.
I_MAX_CHAT_MESSAGES = 60

I_MAX_CHAT_QUESTION_LENGTH = 20000
I_CHAT_OUTPUT_CAP_BYTES = 1_048_576

# How long a close waits for an in-flight answer's worker thread after
# the runner has been destroyed. The destruction is what ends the wait:
# killing the container closes the exec stream the worker is pumping,
# so the thread returns on its own within a poll interval rather than
# running to its wall clock.
F_CHAT_WORKER_SETTLE_SECONDS = 15.0


class CouncilChatError(Exception):
    """A chat request was refused; the message is the researcher's."""


def fsComposeChatEgressScope(sCampaignId):
    """Return the egress-resource scope name for a campaign's chat."""
    return f"{sCampaignId}{S_CHAT_EGRESS_SCOPE_SUFFIX}"


def _fdictSessionsByCampaign(dictControllerState):
    """Return (seeding on first use) the live chat sessions map."""
    return dictControllerState.setdefault(S_CHAT_SESSIONS_KEY, {})


def _fdictRequireSession(dictControllerState, sCampaignId):
    """Return the campaign's open session, or refuse with the remedy."""
    dictSession = _fdictSessionsByCampaign(dictControllerState).get(
        sCampaignId)
    if dictSession is None:
        raise CouncilChatError(
            "there is no open conversation with this council's chairbot; "
            "open one first")
    return dictSession


def _fsMintChatIdentifier(sKindPrefix):
    """Mint a server-owned chat identifier."""
    return f"{sKindPrefix}-{uuid.uuid4().hex[:12]}"


# ----- the observable view ---------------------------------------------


def fdictDescribeChatSession(dictControllerState, sCampaignId):
    """Describe the campaign's conversation for the panel.

    Always answers — a campaign with no session reports ``bOpen`` False
    rather than 404 — because the panel polls this to learn when an
    answer landed, and a poll that has to distinguish "no conversation"
    from "the request failed" reports the second as the first.
    """
    dictSession = _fdictSessionsByCampaign(dictControllerState).get(
        sCampaignId)
    if dictSession is None:
        return {"bOpen": False, "sState": "", "listMessages": [],
                "sPendingMessageId": "", "sFailureReason": "",
                "sChairbotParticipantId": "", "sResolvedModel": "",
                "iMessagesRemaining": I_MAX_CHAT_MESSAGES,
                "iIdleSecondsRemaining": 0}
    return {
        "bOpen": True,
        "sState": dictSession["sState"],
        "listMessages": [dict(dictMessage)
                         for dictMessage in dictSession["listMessages"]],
        "sPendingMessageId": dictSession["sPendingMessageId"],
        "sFailureReason": dictSession["sFailureReason"],
        "sChairbotParticipantId": dictSession["sParticipantId"],
        # Mechanically recorded from the stream, exactly as a turn's is:
        # empty until a message has actually run, never the requested
        # alias laundered into a resolved identity.
        "sResolvedModel": dictSession["dictModelIdentity"].get(
            "sResolvedModel", ""),
        "iMessagesRemaining": max(
            0, I_MAX_CHAT_MESSAGES - len(dictSession["listMessages"])),
        "iIdleSecondsRemaining": int(max(0, _ffFindSecondsUntilDeadline(
            dictSession))),
    }


def _ffFindSecondsUntilDeadline(dictSession):
    """Return seconds until whichever bound closes this session first."""
    fNow = time.monotonic()
    return min(
        dictSession["fLastActivityMonotonic"] + F_CHAT_IDLE_TIMEOUT_SECONDS
        - fNow,
        dictSession["fOpenedMonotonic"] + F_CHAT_SESSION_CEILING_SECONDS
        - fNow,
    )


def fbResourceHasChatMessageInFlight(dictControllerState, sResourceName):
    """Report whether a container has a chat message being answered.

    The release path's chat half. An idle open conversation is NOT
    busy — the drain closes it — but a message in flight is paid
    provider work, and a release that dropped the lease under one would
    abandon a turn the researcher is waiting on.
    """
    return any(
        dictSession["sResourceName"] == sResourceName
        and dictSession["sState"] == S_CHAT_STATE_ANSWERING
        for dictSession in _fdictSessionsByCampaign(
            dictControllerState).values())


# ----- opening ----------------------------------------------------------


async def fdictOpenChatSession(dictControllerState, dictOpenRequest):
    """Build the conversation's runner and return the opened session.

    Synchronous from the caller's point of view — the HTTP request
    waits — because opening is bounded work the researcher has just
    asked for and a background "opening" state would need its own
    cancellation story for no gain. The build itself runs off the event
    loop.

    Re-opening an already-open conversation is idempotent: the existing
    session is returned untouched, so a double-click cannot leave a
    second runner nobody holds a reference to.
    """
    sCampaignId = dictOpenRequest["sCampaignId"]
    dictSessions = _fdictSessionsByCampaign(dictControllerState)
    if sCampaignId in dictSessions:
        return fdictDescribeChatSession(dictControllerState, sCampaignId)
    dictSession = _fdictCreateSessionRecord(dictOpenRequest)
    # Registered BEFORE the build so a fault midway leaves a record the
    # teardown below can find; the same discipline the campaign runtime
    # uses, for the same reason.
    dictSessions[sCampaignId] = dictSession
    try:
        await asyncio.to_thread(_fnBuildChatRunner, dictSession)
    except BaseException:
        await asyncio.to_thread(_fdictTearDownChatResources, dictSession)
        dictSessions.pop(sCampaignId, None)
        raise
    agentCouncilStore.fdictAppendCampaignEvent(
        dictSession["dictStore"], sCampaignId,
        {"sEventKind": "chairbotChatOpened", "sTurnId": "",
         "sDetail": dictSession["sSessionId"]})
    return fdictDescribeChatSession(dictControllerState, sCampaignId)


def _fdictCreateSessionRecord(dictOpenRequest):
    """Assemble the in-process record one conversation is driven from."""
    dictCampaign = dictOpenRequest["dictCampaign"]
    dictParticipant = _fdictResolveChairbot(dictCampaign)
    fNow = time.monotonic()
    return {
        "sSessionId": _fsMintChatIdentifier("chat"),
        "sCampaignId": dictCampaign["sCampaignId"],
        "sResourceName": dictOpenRequest["sResourceName"],
        "dictStore": dictOpenRequest["dictStore"],
        "dictRegistry": dictOpenRequest["dictRegistry"],
        "sImageReference": dictOpenRequest["sImageReference"],
        "fsStageRunnerCredential": dictOpenRequest["fsStageRunnerCredential"],
        "sParticipantId": dictParticipant["sParticipantId"],
        "sRequestedModel": dictParticipant["sRequestedModel"],
        "dictParticipant": dictParticipant,
        "sState": S_CHAT_STATE_READY,
        "sFailureReason": "",
        "sPendingMessageId": "",
        "listMessages": [],
        "dictModelIdentity": {},
        "dictGateway": None,
        "sHandle": "",
        "bEgressProvisioned": False,
        "taskAnswer": None,
        "bClosing": False,
        "fOpenedMonotonic": fNow,
        "fLastActivityMonotonic": fNow,
    }


def _fjsonReadCampaignNow(dictSession):
    """Re-read the campaign record the next message will be composed from.

    Read per message, never cached at open: a conversation held open
    across a round would otherwise quote the candidate plan as it stood
    when the researcher opened the panel, and the chairbot would answer
    confidently about a plan its own council had already revised. The
    store returns a deep copy, so caching one is caching a stale
    campaign, not observing a live one.
    """
    jsonCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictSession["dictStore"], dictSession["sCampaignId"])
    if jsonCampaign is None:
        raise CouncilChatError(
            "this campaign's record is gone; the conversation cannot "
            "continue")
    return jsonCampaign


def _fdictResolveChairbot(dictCampaign):
    """Return the campaign's pen-holder participant record.

    The chairbot is who the researcher is asking, because the chairbot
    is who wrote the plan they are asking about. A campaign whose
    recorded chairbot id matches no participant is a corrupt record,
    not a reason to substitute somebody else's voice.
    """
    for dictParticipant in dictCampaign.get("listParticipants") or []:
        if dictParticipant["sParticipantId"] == dictCampaign.get(
                "sChairbotParticipantId"):
            return dictParticipant
    raise CouncilChatError(
        "this campaign's recorded chairbot is not one of its "
        "participants, so there is nobody to ask")


def _fnBuildChatRunner(dictSession):
    """Provision egress and create the conversation's runner (blocking).

    The order is the campaign's order for the same reasons: the egress
    boundary first (a runner with no proxy to reach is useless), then
    the reserve-before-create runner, then the snapshot, then the
    login. Every step writes what it built onto the session record as
    it goes, so a fault at any point leaves the teardown something to
    find.
    """
    from . import agentCouncilProviders
    dictGateway = agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
        agentCouncilDockerGateway.fdockerCreateCouncilClient(),
        dictSession["dictRegistry"], dictSession["sResourceName"])
    dictSession["dictGateway"] = dictGateway
    dictEgress = _fdictProvisionChatEgress(dictSession, dictGateway)
    dictCreated = agentCouncilDockerGateway.fdictReserveAndCreateRunner(
        dictGateway, dictSession["sCampaignId"],
        agentCouncilProviders.S_PROVIDER_CLAUDE,
        _fdictComposeChatRunnerCost(), dictSession["sImageReference"],
        None, dictEgress["sNetworkName"], False,
        _fdictComposeChatRunnerEnvironment(dictEgress),
        *agentCouncilProviders.ftBuildDnsWiring(dictEgress))
    if not dictCreated["bCreated"]:
        raise CouncilChatError(
            "the chairbot's runner was refused admission: "
            + dictCreated["sRefusalReason"])
    dictSession["sHandle"] = dictCreated["sHandle"]
    agentCouncilDockerGateway.fnCopySnapshotIntoRunner(
        dictGateway, dictCreated["sHandle"],
        agentCouncilContext.fbaReadSealedSnapshotArchive(
            dictSession["dictStore"]["sDurableStoreRoot"],
            dictSession["sCampaignId"]))
    _fnDeliverChatCredential(dictSession)


def _fdictProvisionChatEgress(dictSession, dictGateway):
    """Create the conversation's own internal network and CONNECT proxy."""
    from . import agentCouncilProviders
    sScope = fsComposeChatEgressScope(dictSession["sCampaignId"])
    dictSession["bEgressProvisioned"] = True
    sNetworkName = agentCouncilDockerGateway.fsCreateCampaignInternalNetwork(
        dictGateway, sScope)
    sProxyInternalAddress = (
        agentCouncilDockerGateway.fsLaunchAllowlistProxy(
            dictGateway, sScope,
            [agentCouncilProviders.S_ANTHROPIC_API_HOSTNAME]))
    return {"sNetworkName": sNetworkName,
            "sProxyInternalAddress": sProxyInternalAddress,
            "iProxyPort": agentCouncilEgress.I_PROXY_LISTEN_PORT}


def _fdictComposeChatRunnerEnvironment(dictEgress):
    """Compose the runner's proxy and config-directory environment."""
    from . import agentCouncilProviders
    dictEnvironment = agentCouncilEgress.fdictBuildRunnerProxyEnvironment(
        dictEgress["sProxyInternalAddress"], dictEgress["iProxyPort"])
    dictEnvironment[
        agentCouncilProviders.S_CLAUDE_CONFIG_DIRECTORY_ENV] = (
        agentCouncilProviders.S_RUNNER_CLAUDE_CONFIG_DIRECTORY)
    return dictEnvironment


def _fdictComposeChatRunnerCost():
    """Declare the admission cost of one conversation's runner.

    The same cost a deliberation runner declares, because it is the
    same container. A conversation is admitted through the same
    registry ceilings as a turn, so a hub already at its concurrency
    bound refuses the chat rather than overcommitting the daemon.
    """
    dictLimits = agentCouncilRunner.fdictBuildDefaultRunnerLimits()
    return {"iMemoryBytes": dictLimits["iMemoryBytes"],
            "fCpuCount": dictLimits["fCpuCount"]}


def _fnDeliverChatCredential(dictSession):
    """Stage, deliver and immediately delete this session's login copy.

    The host file's life is the milliseconds between materialization
    and the tarball build, exactly as a turn's is — the difference a
    session-scoped runner makes is in the CONTAINER, and that is what
    the two session clocks bound.
    """
    from . import agentCouncilProviders
    from ..config import secretManager
    sStagedPath = dictSession["fsStageRunnerCredential"]()
    try:
        baCredentialTar = agentCouncilProviders.fbaBuildCredentialTarball(
            sStagedPath)
    finally:
        secretManager.fnCleanupSecretFiles([sStagedPath])
    agentCouncilProviders.fnDeliverCredentialIntoRunner(
        dictSession["dictGateway"], dictSession["sHandle"], baCredentialTar)


# ----- asking -----------------------------------------------------------


async def fdictAskChatQuestion(dictControllerState, sCampaignId,
                               sQuestionText):
    """Record the researcher's message and start answering it.

    Returns as soon as the message is recorded; the answer lands on a
    background task and the panel polls
    :func:`fdictDescribeChatSession` for it. A model answer can take
    minutes, and an HTTP request held open that long is at the mercy of
    every proxy between the browser and the hub.
    """
    dictSession = _fdictRequireSession(dictControllerState, sCampaignId)
    _fnRefuseUnaskableQuestion(dictSession, sQuestionText)
    dictMessage = _fdictAppendMessage(
        dictSession, agentCouncilCharter.S_CHAT_AUTHOR_RESEARCHER,
        sQuestionText)
    # Set BEFORE any await: the release path's busy predicate reads this
    # state, and a check that could run between the refusal above and
    # this assignment would see an idle session and drain it out from
    # under a message the researcher just sent.
    dictSession["sState"] = S_CHAT_STATE_ANSWERING
    dictSession["sPendingMessageId"] = dictMessage["sMessageId"]
    dictSession["taskAnswer"] = asyncio.create_task(
        _fnAnswerChatQuestion(dictSession))
    agentCouncilStore.fdictAppendCampaignEvent(
        dictSession["dictStore"], sCampaignId,
        {"sEventKind": "chairbotAsked", "sTurnId": "",
         "sDetail": dictMessage["sMessageId"]})
    return fdictDescribeChatSession(dictControllerState, sCampaignId)


def _fnRefuseUnaskableQuestion(dictSession, sQuestionText):
    """Refuse a message this session cannot honestly serve."""
    if dictSession["bClosing"]:
        raise CouncilChatError("this conversation is closing")
    if dictSession["sState"] == S_CHAT_STATE_ANSWERING:
        raise CouncilChatError(
            "the chairbot is still answering the previous message")
    if dictSession["sState"] == S_CHAT_STATE_FAILED:
        raise CouncilChatError(
            "this conversation's runner is no longer usable: "
            + dictSession["sFailureReason"]
            + " — close the conversation and open a fresh one")
    if len(dictSession["listMessages"]) >= I_MAX_CHAT_MESSAGES:
        raise CouncilChatError(
            f"this conversation has reached its {I_MAX_CHAT_MESSAGES}-"
            "message bound; the whole transcript is re-sent on every "
            "message, so it is closed rather than silently truncated. "
            "Close it and open a fresh one.")
    if not sQuestionText.strip():
        raise CouncilChatError("a question must not be empty")


def _fdictAppendMessage(dictSession, sAuthor, sText):
    """Append one transcript message and mark the session active."""
    dictMessage = {
        "sMessageId": _fsMintChatIdentifier("message"),
        "sAuthor": sAuthor,
        "sText": sText,
        "fRecordedEpoch": time.time(),
    }
    dictSession["listMessages"].append(dictMessage)
    dictSession["fLastActivityMonotonic"] = time.monotonic()
    return dictMessage


async def _fnAnswerChatQuestion(dictSession):
    """Run one message in the session's runner and record the answer.

    The answer is recorded whatever happens: a fault becomes a failed
    session with the reason the researcher reads, never a conversation
    that silently stops responding. The session is NOT torn down on a
    failure — the researcher decides whether to close it — but it
    refuses further messages, because a runner that just faulted has
    not been shown to be able to serve another.
    """
    try:
        sAnswerText = await asyncio.to_thread(
            _fsRunChatMessageInRunner, dictSession)
        if dictSession["bClosing"]:
            return
        _fdictAppendMessage(
            dictSession, agentCouncilCharter.S_CHAT_AUTHOR_CHAIRBOT,
            sAnswerText)
        dictSession["sState"] = S_CHAT_STATE_READY
        agentCouncilStore.fdictAppendCampaignEvent(
            dictSession["dictStore"], dictSession["sCampaignId"],
            {"sEventKind": "chairbotAnswered", "sTurnId": "",
             "sDetail": dictSession["sPendingMessageId"]})
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.exception(
            "Council chat message failed for campaign %s",
            dictSession["sCampaignId"])
        dictSession["sState"] = S_CHAT_STATE_FAILED
        dictSession["sFailureReason"] = (
            f"{type(error).__name__}: {error}")
    finally:
        dictSession["sPendingMessageId"] = ""
        dictSession["fLastActivityMonotonic"] = time.monotonic()


def _fsRunChatMessageInRunner(dictSession):
    """Execute one message's CLI run and return the chairbot's prose.

    The instruction channel is server-composed (charter + role + the
    chat clause) and carries no researcher text; the question and the
    transcript ride the quoted-untrusted-material channel on stdin,
    exactly as peer material does in a turn. A crafted question cannot
    become a flag, a model id or a tool name.
    """
    from . import agentCouncilProviders
    dictCampaign = _fjsonReadCampaignNow(dictSession)
    saArgv = agentCouncilProviders.flistComposeClaudeArgv(
        dictSession["sRequestedModel"],
        agentCouncilCharter.fsComposeChatInstruction(
            dictCampaign, dictSession["dictParticipant"]))
    baStdin = agentCouncilProviders.fsComposeUntrustedPromptText(
        agentCouncilCharter.flistBuildChatQuotedMaterial(
            dictCampaign, dictSession["listMessages"])).encode("utf-8")
    dictExecuted = agentCouncilDockerGateway.fdictExecuteBoundedTurn(
        dictSession["dictGateway"], dictSession["sHandle"], saArgv,
        I_CHAT_OUTPUT_CAP_BYTES, F_CHAT_TURN_WALL_CLOCK_SECONDS,
        agentCouncilRunner.S_RUNNER_SNAPSHOT_ROOT, baStdin)
    listEvents = agentCouncilProviders.flistParseStreamJsonEvents(
        dictExecuted["sOutput"])
    dictSession["dictModelIdentity"] = (
        agentCouncilProviders.fdictExtractModelIdentity(
            listEvents, dictSession["sRequestedModel"]))
    sAnswerText = agentCouncilProviders.fsExtractResultText(listEvents)
    if sAnswerText:
        return sAnswerText
    raise CouncilChatError(agentCouncilProviders.fsExplainEmptyResult(
        listEvents, dictExecuted))


# ----- closing ----------------------------------------------------------


async def fdictCloseChatSession(dictControllerState, sCampaignId):
    """Close a conversation: destroy the runner, prove it gone, report.

    The destruction is what ends an in-flight message — killing the
    container closes the exec stream its worker thread is pumping — so
    the order is destroy first, then wait the worker out, never the
    reverse. Waiting first would hold the close for the message's whole
    wall clock.

    Returns ``{bSettled, sOutcome, sReason}``. ``bSettled`` False means
    the daemon could not PROVE the runner or its egress gone: the
    reservation stays visibly quarantined and holding budget, and every
    caller that treats settlement as permission — the release drain,
    the campaign delete — must refuse rather than proceed.
    """
    dictSession = _fdictSessionsByCampaign(dictControllerState).get(
        sCampaignId)
    if dictSession is None:
        return {"bSettled": True, "sOutcome": "", "sReason": ""}
    dictSession["bClosing"] = True
    dictSettled = await asyncio.to_thread(
        _fdictTearDownChatResources, dictSession)
    await _fnAwaitAnswerWorker(dictSession)
    if dictSettled["bSettled"]:
        _fdictSessionsByCampaign(dictControllerState).pop(sCampaignId, None)
        agentCouncilStore.fdictAppendCampaignEvent(
            dictSession["dictStore"], sCampaignId,
            {"sEventKind": "chairbotChatClosed", "sTurnId": "",
             "sDetail": dictSession["sSessionId"]})
    else:
        # KEPT deliberately: the record is the in-process retry state,
        # and dropping it would leave a container nobody proved gone
        # with nothing naming it but a label.
        dictSession["sState"] = S_CHAT_STATE_FAILED
        dictSession["sFailureReason"] = dictSettled["sReason"]
    return dictSettled


async def _fnAwaitAnswerWorker(dictSession):
    """Wait a cancelled-in-spirit answer worker out, absorbing outcomes.

    A worker thread cannot be interrupted, so the close must not report
    itself done while one is still holding the session record. The wait
    is bounded because the destruction above is what actually ends it;
    if the daemon did not answer, the wait must not become the place
    the hub hangs.
    """
    taskAnswer = dictSession.get("taskAnswer")
    if taskAnswer is None or taskAnswer.done():
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(taskAnswer), F_CHAT_WORKER_SETTLE_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.warning(
            "Council chat worker for campaign %s did not settle within "
            "%.0fs of its runner's destruction",
            dictSession["sCampaignId"], F_CHAT_WORKER_SETTLE_SECONDS)
    except Exception:
        pass


def _fdictTearDownChatResources(dictSession):
    """Destroy the runner and remove the egress; report SETTLED or not.

    Idempotent: a session whose build failed before either existed
    settles at once. Both halves are attempted even when the first is
    unproven, because leaving a network behind helps nobody.
    """
    dictGateway = dictSession.get("dictGateway")
    if dictGateway is None:
        return {"bSettled": True, "sOutcome": "", "sReason": ""}
    listUnproven = []
    sOutcome = ""
    if dictSession["sHandle"]:
        dictDestroyed = _fdictDestroyChatRunner(dictGateway, dictSession)
        sOutcome = dictDestroyed["sOutcome"]
        if sOutcome != agentCouncilRunner.S_OUTCOME_DESTROYED:
            listUnproven.append(dictDestroyed["sReason"])
    if dictSession["bEgressProvisioned"]:
        dictRemoved = (
            agentCouncilDockerGateway.fdictRemoveCampaignEgressResources(
                dictGateway,
                fsComposeChatEgressScope(dictSession["sCampaignId"])))
        if dictRemoved["saIndeterminateResources"]:
            listUnproven.append(
                "the daemon could not prove these gone: "
                + ", ".join(dictRemoved["saIndeterminateResources"]))
        else:
            dictSession["bEgressProvisioned"] = False
    return {"bSettled": not listUnproven, "sOutcome": sOutcome,
            "sReason": "; ".join(listUnproven)}


def _fdictDestroyChatRunner(dictGateway, dictSession):
    """Destroy the session's runner, translating a refusal into a report.

    A gateway refusal — the target no longer carries this handle's
    council label — is not a settled destruction and must not be
    swallowed into one: the container it names is somebody else's, and
    the honest answer is that this session's runner is unaccounted for.
    """
    try:
        dictDestroyed = agentCouncilDockerGateway.fdictDestroyAndSettle(
            dictGateway, dictSession["sHandle"])
    except agentCouncilDockerGateway.CouncilGatewayError as error:
        return {"sOutcome": agentCouncilRunner.S_OUTCOME_QUARANTINED,
                "sReason": str(error)}
    if dictDestroyed["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED:
        dictSession["sHandle"] = ""
    return dictDestroyed


async def flistCloseChatSessionsForResource(dictControllerState,
                                            sResourceName):
    """Close every conversation bound to one container; list the unsettled.

    The release path's chat drain. A conversation cannot continue
    against a project whose lease is gone, and its runner and proxy
    must not outlive that lease — so every session for the resource is
    closed, and any whose resources could not be proven gone is
    returned so the release refuses rather than handing the container
    on.
    """
    listUnsettledCampaignIds = []
    for sCampaignId, dictSession in list(
            _fdictSessionsByCampaign(dictControllerState).items()):
        if dictSession["sResourceName"] != sResourceName:
            continue
        dictSettled = await fdictCloseChatSession(
            dictControllerState, sCampaignId)
        if not dictSettled["bSettled"]:
            listUnsettledCampaignIds.append(sCampaignId)
    return listUnsettledCampaignIds


# ----- the reaper -------------------------------------------------------


async def fiReapExpiredChatSessions(dictControllerState):
    """Close every conversation past its idle or absolute bound.

    Runs on the hub's own clock so a browser that was closed, crashed,
    or simply navigated away cannot strand a runner holding a copied
    login. A session with a message IN FLIGHT is spared: the message
    has its own wall clock, the researcher is waiting on it, and
    killing it here would report a fault the model never had.

    Returns how many were closed.
    """
    iClosed = 0
    for sCampaignId, dictSession in list(
            _fdictSessionsByCampaign(dictControllerState).items()):
        if dictSession["sState"] == S_CHAT_STATE_ANSWERING:
            continue
        if _ffFindSecondsUntilDeadline(dictSession) > 0:
            continue
        logger.info(
            "Closing idle chairbot conversation for campaign %s",
            sCampaignId)
        await fdictCloseChatSession(dictControllerState, sCampaignId)
        iClosed += 1
    return iClosed
