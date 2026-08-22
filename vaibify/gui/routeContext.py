"""Route handler context: the typed dict wrapper and shared route helpers.

Provides attribute access with clear types so that route handlers
can use ``dictCtx.docker`` instead of ``dictCtx["docker"]``, making
dependencies explicit and enabling IDE auto-completion. The class
also acts as a dict for backward compatibility — existing code using
``dictCtx["key"]`` continues to work unchanged.

This module is also the home for helpers shared by MULTIPLE route
modules (``ffilesForWorkflow``, the post-push verify refresh): route
modules must not import siblings
(``testRouteModulesDoNotImportSiblings``), so cross-route logic
lives here, beneath them.
"""

__all__ = [
    "I_REJECT_CONTAINER_ONLY",
    "S_UNAVAILABLE_IN_HOST_MODE",
    "S_UNAVAILABLE_IN_CONTAINER_MODE",
    "S_UNAVAILABLE_UNTIL_CREDENTIAL_EVIDENCE",
    "S_UNAVAILABLE_SNAPSHOT_TOO_LARGE",
    "S_UNAVAILABLE_NO_DOMINANT_DIRECTORY",
    "RouteContext",
    "fdictCarryARefusalBackInsteadOfRaising",
    "fnRefuseContainerOnlyForHostProject",
    "fnRefuseHostOnlyForContainerProject",
    "fdictRequireLaneTupleForCommit",
    "fdictRunAutomaticReadUnderTheDrain",
    "fdictRunRemoteVerifyBlocking",
    "ffilesForWorkflow",
    "fdictCommitWorkflowSave",
    "fnRecordAttributionEvent",
    "fnRejectAgentTokenLane",
    "fgenericRunWorkerUnderTheDrain",
    "fsHashContainerFileOrEmpty",
    "fsRefreshVerifyCacheAfterPush",
]

import asyncio
import hashlib
import logging

from fastapi import HTTPException

from .actionCatalog import S_SESSION_HEADER_NAME

logger = logging.getLogger("vaibify")


def fnRejectAgentTokenLane(requestHttp):
    """Raise HTTP 403 when the request rides the in-container agent lane.

    The agent authenticates REST calls with the per-container
    ``X-Vaibify-Session`` header; a browser session never sends that
    header (it presents the hub credential separately). Its presence
    therefore marks the agent lane — the same discriminator the
    ``/api/session-token`` guard uses — and a route that reads HOST
    state must fail it closed.

    Header PRESENCE is the trigger, not whether the token authorizes
    this container: a route reading the researcher's own machine has no
    honest answer for an agent, so there is nothing for a valid token to
    unlock. Shared by every such route, because a second copy is how one
    of them would come to differ from the others.
    """
    if requestHttp.headers.get(S_SESSION_HEADER_NAME.lower(), ""):
        raise HTTPException(
            403, "The in-container agent must not read host state.",
        )


# A container-only capability aimed at a host project. NOT 403: this
# is the terminal's close-code lesson on the HTTP boundary. A caller
# that cannot tell "this feature does not exist for this project" from
# "your credential was rejected" tells the researcher to re-claim a
# project that is already theirs, and they re-claim it forever. 409
# says the request conflicts with the resource's state, which is
# exactly true -- the project runs on the host, so there is no
# container for the capability to act on.
I_REJECT_CONTAINER_ONLY = 409
# The machine-readable half. The prose is what a researcher reads; a
# panel deciding whether to hide a control must not have to parse it.
S_UNAVAILABLE_IN_HOST_MODE = "host-mode"
# The mirror marker: a HOST-only capability aimed at a container
# project. Its own value so a panel can tell the two refusals apart --
# "this needs a container" is the opposite diagnosis from "this project
# already IS one".
S_UNAVAILABLE_IN_CONTAINER_MODE = "container-mode"
# Not a mode at all: the capability fits this project, but a gate the
# researcher can OPEN is currently shut. The two markers above say "this
# will never work here"; this one says "here is the thing to go and do",
# and a panel must be able to tell those apart, because only the third
# deserves instructions.
S_UNAVAILABLE_UNTIL_CREDENTIAL_EVIDENCE = "credential-evidence"
# Also not a mode, and also openable — but by changing the PROJECT
# rather than the machine. Its own marker so the panel can offer the
# repository advice instead of the credential ceremony.
S_UNAVAILABLE_SNAPSHOT_TOO_LARGE = "snapshot-too-large"
# The third openable one: the capability fits and the machine is
# ready, but the PROJECT has not said which directory it is about.
# Distinct from the size marker because the action differs — one
# shrinks a repository, the other names one.
S_UNAVAILABLE_NO_DOMINANT_DIRECTORY = "no-dominant-directory"


def fnRefuseContainerOnlyForHostProject(sName, sCapability):
    """Raise 409 when a container-only capability names a host project.

    Server-side is where this has to live. The dashboard hides
    start/stop/build from a host tile, but hiding is courtesy: the
    routes are reachable by ``curl``, by the CLI, and by any panel that
    forgets, and every one of them would otherwise drive Docker
    machinery at a project that has no container.

    ``sCapability`` names the capability in the researcher's words
    ("Starting a container"), because the refusal is read by a person
    who is trying to do something, not by whoever wrote the route.
    """
    from vaibify.config.registryManager import fbIsHostProject
    if not fbIsHostProject(sName):
        return
    raise HTTPException(
        I_REJECT_CONTAINER_ONLY,
        detail={
            "sMessage": (
                f"{sCapability} applies only to containerized "
                f"projects. '{sName}' runs directly on this machine "
                "and has no container."
            ),
            "sUnavailableIn": S_UNAVAILABLE_IN_HOST_MODE,
        },
    )


def fnRefuseHostOnlyForContainerProject(sName, sCapability):
    """Raise 409 when a host-only capability names a container project.

    The exact mirror of :func:`fnRefuseContainerOnlyForHostProject`, and
    it must live on the server for the same reason: the dashboard shows
    "Convert to Project" only on a host tile, but hiding is courtesy --
    the route is reachable by ``curl`` and by any panel that forgets, and
    a container project has nothing to convert.

    It is also how the convert route stays idempotent: a repeat call
    after a successful conversion sees a container project and is refused
    here rather than re-registering it a second time. Not 403, for the
    withdrawn terminal's reason -- a refusal a client reads as "your
    credential was rejected" sends the researcher to re-claim a project
    that is already theirs.
    """
    from vaibify.config.registryManager import fbIsHostProject
    if fbIsHostProject(sName):
        return
    raise HTTPException(
        I_REJECT_CONTAINER_ONLY,
        detail={
            "sMessage": (
                f"{sCapability} applies only to host projects. "
                f"'{sName}' is already a containerized project."
            ),
            "sUnavailableIn": S_UNAVAILABLE_IN_CONTAINER_MODE,
        },
    )


def fdictRequireLaneTupleForCommit(
    requestHttp, sContainerId, sOperationName,
):
    """Return the lane tuple binding a request to its container's owner.

    Every route migrated onto the commit-guard carrier needs the same
    refusal first: the carrier will not mint an admission for a request
    it cannot bind to the owner record, so a route that called it with
    ``None`` would raise a ``KeyError`` deep inside the carrier instead
    of telling the caller what to do. Answering 403 here keeps the
    diagnosis actionable — the request does not hold the container, so
    claim or connect.
    """
    from . import commitCarrier
    dictLaneTuple = commitCarrier.fdictBuildLaneTupleFromRequest(
        requestHttp.app.state, sContainerId, requestHttp,
    )
    if dictLaneTuple is None:
        raise HTTPException(
            403,
            f"{sOperationName} cannot be bound to this container's owner "
            "record; claim or connect first.",
        )
    return dictLaneTuple


def fdictCarryARefusalBackInsteadOfRaising(
    fnEffect, setAlsoCarriedStatusCodes=frozenset(),
):
    """Run a carrier worker's effect, carrying a refusal back as a value.

    A carrier worker that RAISES is settled through the failure path,
    which marks its journal record NEEDS RECONCILIATION and QUARANTINES
    the container until the researcher runs ``vaibify reconcile``. That
    is exactly right for an effect whose state nobody knows, and badly
    wrong for "no such repository" or "the remote is unreachable": an
    ordinary refusal would take a working container out of service.

    The default split is by STATUS, and the line is 4xx/5xx because that
    is the line between the two meanings. A 4xx is a refusal decided
    BEFORE anything was written -- the directory already exists, the
    repository is not tracked, the name is invalid -- so the container is
    untouched and there is nothing to reconcile. A 5xx is raised
    MID-EFFECT (``mkdir`` failed, ``git init`` failed halfway), which is
    precisely the unknown state the quarantine exists for, so it is left
    to propagate and poison.

    ``setAlsoCarriedStatusCodes`` names the 5xx codes a particular caller
    has judged to be decided rather than unknown, and every caller that
    passes one must say why at its call site. The git panel is the case
    that needed it: a failed ``git fetch`` answers 502, but the git
    process RAN TO COMPLETION and reported a non-zero exit, so the
    container's state is known and a researcher whose network blipped
    must not be told to reconcile.

    Returns ``{"errorRefused": HTTPException|None, "objResult": ...}``.
    """
    try:
        return {"errorRefused": None, "objResult": fnEffect()}
    except HTTPException as errorHttp:
        if (
            errorHttp.status_code >= 500
            and errorHttp.status_code not in setAlsoCarriedStatusCodes
        ):
            raise
        return {"errorRefused": errorHttp, "objResult": None}


async def fgenericRunWorkerUnderTheDrain(
    sContainerId, fnCarryingWorker, sOperationTarget, requestHttp,
):
    """Run a carrying worker under the drain; re-raise its refusal here.

    Mode (b) rather than mode (a) whenever the effect is a sequence of
    container commands that can run for as long as a network round-trip
    or a subprocess takes: mode (a) runs its effect on the event loop,
    and — more importantly — the drain has to be held for the WORKER's
    life, so an ownership hand-over or a Run Step arriving mid-effect is
    refused and told what is running rather than landing underneath a
    process that keeps writing.

    ``fnCarryingWorker(supervisor=None)`` must return the
    ``{"errorRefused", "objResult"}`` shape
    :func:`fdictCarryARefusalBackInsteadOfRaising` produces. The refusal
    travels back as a VALUE and is re-raised OUT HERE deliberately: by
    then the supervisor has settled its journal record normally, so the
    researcher gets their 4xx and their container stays usable, where a
    raise from inside the worker would poison the record and quarantine
    the container over "that name is already taken".

    Lifted here on the fourth copy — the git panel, the Repos panel and
    the step-rename cascade all wanted the identical five statements —
    so the "settle, then raise" ordering has one implementation to get
    right. What deliberately did NOT move is the caller's own call to
    the carry-back: which statuses a panel treats as decided is ITS
    judgement, stated at its own call site, and folding those calls in
    here would leave one mutant able to break every caller's guarantee
    at once, isolating none of them.

    ``sOperationTarget`` is written into the journal, so a caller must
    pass a compile-time constant or an already-sanitized identifier —
    never a remote URL or anything else derived from the request.
    """
    from . import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sOperationTarget,
    )
    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", sOperationTarget,
        fnCarryingWorker,
    )
    dictCarried = dictOutcome["result"]
    if dictCarried["errorRefused"] is not None:
        raise dictCarried["errorRefused"]
    return dictCarried["objResult"]


async def fdictRunAutomaticReadUnderTheDrain(
    sContainerId, fnWorker, sOperationTarget, requestHttp,
):
    """Run an automatic read's worker under the drain, or report paused.

    An AUTOMATIC read is one the dashboard issues on its own — the
    badge refresh a workflow fires on open, the repository panel's
    poll — as opposed to one a researcher asked for by clicking.

    Its commands reach the general exec primitive, which the gate must
    treat as mutating (command text cannot be told apart from a
    delete), so on the enforced branch it needs a carrier like any
    mutation. What it must NOT do is queue: mode (b) waits for the
    drain, and waiting spends an unpredictable amount of a request
    nobody made — a ``git fetch`` or a step run can hold it for
    minutes. So this asks for the drain and takes ``""`` for an
    answer, returning ``{"bPaused", "sPausedBy", "objResult"}``.

    Paused is a RESULT, not an error. The caller answers 200 with a
    typed paused payload and the panel renders a paused state until the
    next refresh finds the container quiet: a failed request would
    teach the researcher to distrust a panel that is working
    correctly, and a silent empty answer would report "no remotes" as
    a fact about their repository.
    """
    from . import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sOperationTarget,
    )
    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", sOperationTarget,
        fnWorker, bPauseWhenBusy=True,
    )
    if dictOutcome.get("bPausedByLiveWork"):
        return {
            "bPaused": True,
            "sPausedBy": dictOutcome.get("sLiveWork", ""),
            "objResult": None,
        }
    return {
        "bPaused": False,
        "sPausedBy": "",
        "objResult": dictOutcome["result"],
    }


def fsHashContainerFileOrEmpty(dictCtx, sContainerId, sPath):
    """Return a container file's sha256 hex, or ``''`` when it is absent.

    The prior-content half of a ``file-write`` journal record: an atomic
    write leaves its target matching either the intended bytes or the
    bytes that were there before, and the journal spells "the file did
    not exist" as the empty string. Anything this cannot read also
    answers ``''``, which is the fail-safe direction — a wrongly-empty
    prior can only make the probe QUARANTINE a record it might have
    settled, never settle one it should have quarantined.
    """
    try:
        baCurrent = dictCtx["docker"].fbaFetchFile(sContainerId, sPath)
    except FileNotFoundError:
        return ""
    except Exception as error:
        logger.warning(
            "Could not read %s in container %s for the write-ahead "
            "record's prior hash; recording it as absent, which reads "
            "as unproven rather than settled: %s",
            sPath, sContainerId, error,
        )
        return ""
    return hashlib.sha256(baCurrent).hexdigest()


def fdictStampDockerIdForJournal(sContainerId):
    """Return a file-write payload's Docker-id stamp, or {} for host.

    The journal's file-write probe selects its in-container hash branch
    exactly when ``sDockerContainerId`` is present on the record
    (``operationJournal._fdictProbeFileWriteOperation``). A host
    project's write must leave the key absent so the host sha256 branch
    verifies it — a stamped host record would journal a probe that
    tries ``docker exec`` against a container that does not exist.
    """
    from vaibify.config.registryManager import fbIsHostProject
    if fbIsHostProject(sContainerId):
        return {}
    return {"sDockerContainerId": sContainerId}


def fdictCommitWorkflowSave(
    dictCtx, sContainerId, dictWorkflow, requestHttp, sOperationName,
):
    """Commit a ``project.json`` save through carrier mode (a) (design §8).

    The hub's most repeated container mutation — roughly fifty route
    call sites write the workflow back — and the same ``file-write``
    record every time, so migrating a route that saves means routing its
    save through here rather than re-deriving the record. The expected
    hash is the workflow's own serialization fingerprint, which IS the
    sha256 of the bytes the save path writes
    (``fsComputeWorkflowFingerprint``), so the journal's probe can
    compare it against ``sha256sum`` of the file itself.

    The prior hash is read from DISK rather than captured from the dict,
    because by the time a route saves it has usually already mutated the
    workflow in memory: hashing it then would record the intended state
    as the prior one, and the probe would read a write that never landed
    as a write that did. ``settingsRoutes`` captures its own prior in
    memory instead, and correctly — it still holds the pre-update dict
    at that point, and does not need the round-trip.
    """
    from . import commitCarrier, workflowManager
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sOperationName,
    )
    sWorkflowPath = (dictCtx.get("paths") or {}).get(sContainerId, "")
    return commitCarrier.fdictCommitSynchronousMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "file-write",
        sWorkflowPath or "project.json",
        lambda: dictCtx["save"](sContainerId, dictWorkflow),
        {
            **fdictStampDockerIdForJournal(sContainerId),
            "sExpectedSha256": (
                workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
            ),
            "sPriorSha256": fsHashContainerFileOrEmpty(
                dictCtx, sContainerId, sWorkflowPath,
            ) if sWorkflowPath else "",
        },
    )


def ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow):
    """Return the repo-file adapter for a workflow's project repo.

    Production contexts carry a ``files`` callable that builds a
    ``ContainerRepoFiles`` rooted at the workflow's project repo —
    ``sProjectRepoPath`` is a container path, so container IO is the
    only honest reader. Legacy and test contexts without the callable
    fall back to a host adapter over the raw path string, preserving
    host-clone semantics.
    """
    fnFiles = dictCtx.get("files")
    if fnFiles is not None:
        return fnFiles(sContainerId)
    from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
    return ffilesEnsureRepoFiles(
        (dictWorkflow or {}).get("sProjectRepoPath") or "",
    )


def fnRecordAttributionEvent(
    dictCtx, sContainerId, dictWorkflow, sChannel, sDetail,
):
    """Append a Supervised-mode attribution event from a route.

    Cheap no-op when supervision is off; failures are logged and
    swallowed — attribution must never break the action it records.
    """
    from . import attributionLog
    if not attributionLog.fbSupervisionEnabled(dictWorkflow):
        return
    try:
        attributionLog.fnAppendAttributionEvent(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            dictWorkflow, sChannel, "hub", sDetail,
        )
    except Exception as errorCaught:  # noqa: BLE001 — never break the route
        logger.warning("Attribution event append failed: %s", errorCaught)


def fdictRunRemoteVerifyBlocking(dictWorkflow, sService, filesRepo):
    """Run the synchronous verify call against the remote and return status."""
    from vaibify.reproducibility import scheduledReverify
    dictStatus = scheduledReverify.fdictVerifyRemoteService(
        filesRepo, dictWorkflow, sService,
    )
    scheduledReverify.fnWriteSyncStatus(filesRepo, dictStatus)
    return dictStatus


async def _fnRunPostPushVerifyBlocking(
    sContainerId, dictWorkflow, sService, filesRepo, requestHttp,
):
    """Run the post-push verify, under the drain on a migrated route.

    The verify hashes the project repo and REWRITES ``syncStatus.json``
    inside the container, so it is a second mutation arriving after the
    push's own carrier has already settled — and on a route served by
    the enforced branch an uncarried write is refused at the primitive.
    It gets its own mode-(b) invocation rather than sharing the push's,
    because the push's supervisor released the drain when its worker
    terminated, which is the property that makes mode (b) worth having.

    A caller that passes no request is a route still awaiting migration
    or the direct library lane; those keep the legacy ``to_thread`` —
    an unenforced lane named in the carrier's documented remainder,
    never a pretend-guarded one. The two lanes are not a divergence to
    tidy away: this helper is shared by three push routes that migrate
    one at a time.
    """
    def fdictVerifyWorker(supervisor=None):
        del supervisor
        return fdictRunRemoteVerifyBlocking(
            dictWorkflow, sService, filesRepo,
        )

    if requestHttp is None:
        await asyncio.to_thread(fdictVerifyWorker)
        return
    from . import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, f"The post-push {sService} verify",
    )
    await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper",
        "post-push-verify " + sService, fdictVerifyWorker,
    )


async def fsRefreshVerifyCacheAfterPush(
    dictCtx, sContainerId, dictWorkflow, sService, requestHttp=None,
):
    """Re-verify one service's remote right after a successful push.

    Shared by every push route (GitHub sync, Repos-panel staged and
    per-file pushes). The push already proved the network path to
    the service, so one verify round-trip is cheap, and it spares
    the researcher a manual "refresh remote status" click: the L2
    cells read the verify cache, which would otherwise stay stale
    for up to 24 hours after the very push that satisfied them.
    Best-effort by design — a failed verify leaves the cache stale
    (the cells keep reading unknown, never a fake green) and must
    not fail the push response. Returns "" on success and a short
    redaction-safe warning string on failure, so the push toast can
    tell the researcher the L2 check did not run instead of leaving
    a silent gap between "pushed" and "still unknown". A service the
    workflow never configured in ``dictRemotes`` is skipped without
    a warning — there is nothing to verify against, and nagging a
    researcher who only uses plain git pushes would teach them to
    ignore real warnings.
    """
    if not ((dictWorkflow or {}).get("dictRemotes") or {}).get(sService):
        return ""
    try:
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        await _fnRunPostPushVerifyBlocking(
            sContainerId, dictWorkflow, sService, filesRepo, requestHttp,
        )
    except Exception as error:
        logger.warning(
            "Post-push %s verify failed; cached remote status stays "
            "stale until the next refresh.", sService, exc_info=True,
        )
        return _fsPostPushVerifyWarning(sService, error)
    return ""


def _fsPostPushVerifyWarning(sService, error):
    """Actionable, redaction-safe summary of a failed post-push verify.

    Never embeds the raw exception text: verify errors can carry
    remote URLs, and URLs can carry credentials. Known causes get a
    specific remedy; everything else points at the hub log.
    """
    if (isinstance(error, FileNotFoundError)
            and "manifest" in str(error).lower()):
        return (
            "Pushed, but the " + sService + " status check needs "
            "MANIFEST.sha256, which does not exist yet — vaibify "
            "generates it when the workflow reaches Level 1."
        )
    return (
        "Pushed, but the " + sService + " status check failed — "
        "the Published (L2) cells stay unknown. See the hub log."
    )


class RouteContext:
    """Typed context object passed to all route handlers.

    Wraps the underlying dict so both attribute access and dict
    access work identically.  New code should prefer attributes;
    old code using bracket notation keeps working.
    """

    def __init__(self, dictRaw):
        object.__setattr__(self, "_dictRaw", dictRaw)

    # --- typed attribute access ---

    @property
    def connectionDocker(self):
        """Docker connection for executing container commands."""
        return self._dictRaw["docker"]

    @property
    def dictWorkflows(self):
        """Dict of {sContainerId: dictWorkflow} cache."""
        return self._dictRaw["workflows"]

    @property
    def dictPaths(self):
        """Dict of {sContainerId: sWorkflowPath} cache."""
        return self._dictRaw["paths"]

    @property
    def dictTerminals(self):
        """Dict of {sSessionId: TerminalSession} cache."""
        return self._dictRaw["terminals"]

    @property
    def dictContainerUsers(self):
        """Dict of {sContainerId: sUsername} cache."""
        return self._dictRaw["containerUsers"]

    @property
    def dictPipelineTasks(self):
        """Dict of {sContainerId: asyncio.Task} for running pipelines."""
        return self._dictRaw["pipelineTasks"]

    @property
    def sSessionToken(self):
        """Session token for WebSocket origin validation."""
        return self._dictRaw.get("sSessionToken", "")

    def fnRequireDocker(self):
        """Raise if Docker is not available."""
        self._dictRaw["require"]()

    def fnSaveWorkflow(self, sContainerId, dictWorkflow):
        """Persist workflow to container."""
        self._dictRaw["save"](sContainerId, dictWorkflow)

    def fdictGetVariables(self, sContainerId):
        """Build variable substitution dict for a container."""
        return self._dictRaw["variables"](sContainerId)

    def fsGetWorkflowDirectory(self, sContainerId):
        """Return the workflow directory path for a container."""
        return self._dictRaw["workflowDir"](sContainerId)

    def ffilesGetRepoFiles(self, sContainerId):
        """Return a ContainerRepoFiles rooted at the workflow's project repo."""
        return self._dictRaw["files"](sContainerId)

    # --- dict-compatible access for backward compatibility ---

    def __getitem__(self, sKey):
        return self._dictRaw[sKey]

    def __setitem__(self, sKey, value):
        self._dictRaw[sKey] = value

    def __contains__(self, sKey):
        return sKey in self._dictRaw

    def __delitem__(self, sKey):
        del self._dictRaw[sKey]

    def get(self, sKey, default=None):
        """Dict-compatible get with default."""
        return self._dictRaw.get(sKey, default)

    def setdefault(self, sKey, default=None):
        """Dict-compatible setdefault."""
        return self._dictRaw.setdefault(sKey, default)

    def pop(self, sKey, *args):
        """Dict-compatible pop."""
        return self._dictRaw.pop(sKey, *args)
