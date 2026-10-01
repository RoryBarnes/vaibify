"""The in-app council credential test: its job, its checks, its admitter.

A credential test is what turns a researcher's consent into an
authorization. It runs against the project image BY ITS sha256 ID — the
id every council runner launches from — in a disposable runner built
by the same gateway and staging machinery a council uses, and it ends
in exactly one durable outcome: ``passed``, ``failed`` (naming the
check), or ``incomplete`` (timeout, cancellation, hub interruption).

The test cannot use the per-turn admission a council turn uses
(``councilRouteGuards.ftAdmitCouncilTurnCredential``): a first test has
no ``passed`` outcome yet, and starting a re-test deliberately suspends
the old one. So it gets its own NARROW authority,
:func:`ftAdmitCredentialTestCredential`, never a "skip verification"
flag on the per-turn one. It admits a token only for THIS job's own
runner, only while this job is the key's recorded test in flight, and
only while consent is active at the generation the job started under —
so a withdrawal mid-test stops the test's next turn. Those two are the
only functions in vaibify that stage a council token, and
``tests/testCouncilCredentialAdmission.py`` pins that structurally.
"""

import uuid

from . import agentCouncilCredentialGate
from . import agentCouncilCredentialStore
from . import agentCouncilProviderRegistry
from . import agentCouncilRunner
from . import agentCouncilStagedCopies
from .agentCouncilCredentialTestRecords import (
    F_TURN_TIMEOUT_SECONDS,
    SET_CHECK_IDS,
    fdictCreateJobRecord,
    fnWriteJobRecord,
)

__all__ = [
    "S_TEST_CAMPAIGN_PREFIX",
    "fsComposeTestJobId",
    "fsComposeTestCampaignId",
    "fbRunnerLabelBelongsToJob",
    "ftAdmitCredentialTestCredential",
    "fsReadRunnerCouncilLabel",
    "F_KILL_AFTER_SECONDS",
    "S_INVALID_MODEL_ID",
    "DICT_DEFAULT_TEST_MODELS",
    "CredentialCheckFailedError",
    "CredentialTestIncompleteError",
    "fbaBuildEmptySnapshotArchive",
    "fsReadCliVersionFromImage",
    "fdictProvisionTestEgress",
    "fdictBuildJobRuntime",
    "fdictStartCredentialTest",
    "fnRunCredentialTestJob",
    "fbRequestCredentialTestCancel",
    "flistRemoveTestEgress",
    "fsDescribeUnsettled",
    "fnPublishJobOutcome",
]

# A credential test's runners are reserved under this pseudo-campaign
# id, so the reservation id — and therefore the ``vaibify-council``
# label every runner wears — names the job it serves. No campaign id
# can take this shape: campaign ids are minted ``campaign-<12 hex>``.
S_TEST_CAMPAIGN_PREFIX = "credentialTest-"


def fsComposeTestJobId():
    """Mint a fresh, unguessable credential-test job id."""
    return uuid.uuid4().hex


def fsComposeTestCampaignId(sJobId):
    """Return the pseudo-campaign id a job's runners are reserved under."""
    return f"{S_TEST_CAMPAIGN_PREFIX}{sJobId}"


def fbRunnerLabelBelongsToJob(sCouncilLabel, sJobId):
    """Return True when a runner's council label was minted for this job.

    The gateway mints ``council-<campaign id>-<random>`` for every
    reservation, so a runner this job reserved carries its
    pseudo-campaign id as the label's middle segment; a campaign
    runner, a chat runner, or another job's runner never does.
    """
    if not sJobId or not sCouncilLabel:
        return False
    return sCouncilLabel.startswith(
        f"council-{fsComposeTestCampaignId(sJobId)}-")


def _fsExplainTestAdmissionRefusal(dictDocument, sJobId, sProvider,
                                   sImageIdentity, sRunnerCouncilLabel):
    """Return why a test admission is refused, or "" when it may proceed.

    Every condition is checked, in the order that names the most useful
    reason first; each one is independently pinned by a test.
    """
    sKey = agentCouncilCredentialStore.fsComposeCredentialKey(
        sProvider, sImageIdentity)
    dictMarker = dictDocument["dictInFlight"].get(sKey)
    if dictMarker is None or dictMarker.get("sJobId") != sJobId:
        return "this job is not the credential test in flight for this key"
    if dictMarker.get("sProvider") != sProvider or dictMarker.get(
            "sImageIdentity") != sImageIdentity:
        return "the job's recorded provider or image does not match"
    dictConsent = dictDocument["dictConsents"].get(sKey) or {}
    if dictConsent.get("sState") != (
            agentCouncilCredentialStore.S_CONSENT_ACTIVE):
        return "consent for this key is no longer active"
    if dictConsent.get("iConsentGeneration") != dictMarker.get(
            "iConsentGeneration"):
        return "consent changed after this test started"
    if not fbRunnerLabelBelongsToJob(sRunnerCouncilLabel, sJobId):
        return "the runner being staged for was not created by this test"
    return ""


def ftAdmitCredentialTestCredential(sJobId, sProvider, sImageIdentity,
                                    sRunnerCouncilLabel, dictCredential):
    """Stage a credential for ONE credential-test runner, or refuse.

    Contract A7. ``sRunnerCouncilLabel`` is the runner's
    ``vaibify-council`` label, read from the daemon by the caller
    BEFORE this runs — nothing slow happens under the store lock. The
    token is staged from memory only when every condition holds, and
    the admission is recorded beside the per-turn ones. Returns
    ``(sStagedPath, iExpiresAt)``; raises
    ``CredentialAdmissionRefusedError`` otherwise, having written no
    token anywhere.
    """
    listStagedPaths = []
    listRefusals = []

    def _fnAdmitUnderStoreLock(dictDocument):
        sRefusal = _fsExplainTestAdmissionRefusal(
            dictDocument, sJobId, sProvider, sImageIdentity,
            sRunnerCouncilLabel)
        if sRefusal:
            listRefusals.append(sRefusal)
            return
        sKey = agentCouncilCredentialStore.fsComposeCredentialKey(
            sProvider, sImageIdentity)
        listStagedPaths.append(
            agentCouncilProviderRegistry.fsStageProviderCredential(
                sProvider, dictCredential))
        agentCouncilStagedCopies.fnHoldStagedCopy(listStagedPaths[-1])
        agentCouncilCredentialStore.fnRecordAdmission(dictDocument, sKey, {
            "sAdmissionKind": "credentialTest", "sJobId": sJobId,
            "iConsentGeneration":
                dictDocument["dictInFlight"][sKey]["iConsentGeneration"]})

    try:
        agentCouncilCredentialStore.fdictMutateCredentialDocument(
            agentCouncilCredentialGate.fsResolveCredentialEvidencePath(),
            _fnAdmitUnderStoreLock)
    except BaseException:
        _fnDiscardStagedPaths(listStagedPaths)
        raise
    if listRefusals:
        raise agentCouncilCredentialStore.CredentialAdmissionRefusedError(
            f"{sProvider}: the credential test's turn was not admitted — "
            f"{listRefusals[0]}.")
    return (listStagedPaths[0],
            dictCredential.get("iExpiresAtEpochMilliseconds", 0))


def _fnDiscardStagedPaths(listStagedPaths):
    """Delete staged files an admission wrote but could not complete."""
    from ..config import secretManager
    secretManager.fnCleanupSecretFiles(listStagedPaths)


def fsReadRunnerCouncilLabel(dictLabels):
    """Return the ``vaibify-council`` label from a daemon label map."""
    return (dictLabels or {}).get(agentCouncilRunner.S_COUNCIL_LABEL, "")


# ----- the job -----------------------------------------------------------------


# Defaults (plan section 8) for the two turns the records module does not
# own: how long the interrupted turn runs before its deliberate kill,
# and the model id no provider serves.
F_KILL_AFTER_SECONDS = 8.0
S_INVALID_MODEL_ID = "vaibify-credential-test-no-such-model"
DICT_DEFAULT_TEST_MODELS = {"claude": "haiku"}

S_TRIVIAL_INSTRUCTION = (
    "This is vaibify's council credential test. Reply with the single "
    "word OK and nothing else.")
S_INTERRUPTED_INSTRUCTION = (
    "This is vaibify's council credential test of an interrupted turn. "
    "Count from 1 to 3000, one number per line, and nothing else.")


class CredentialCheckFailedError(Exception):
    """A check ran to completion and did not hold."""

    def __init__(self, sCheckId, sDetail):
        super().__init__(f"{sCheckId}: {sDetail}")
        self.sCheckId = sCheckId
        self.sDetail = sDetail


class CredentialTestIncompleteError(Exception):
    """The test stopped before a check could reach a verdict."""

    def __init__(self, sCheckId, sDetail):
        super().__init__(f"{sCheckId}: {sDetail}")
        self.sCheckId = sCheckId
        self.sDetail = sDetail


def _fnMarkCheck(dictJob, sCheckId, sStatus, sDetail=""):
    """Record one check's status in the job record and persist it."""
    for dictCheck in dictJob["listChecks"]:
        if dictCheck["sCheckId"] == sCheckId:
            dictCheck["sStatus"] = sStatus
            dictCheck["sDetail"] = sDetail
    dictJob["sCurrentCheck"] = sCheckId if sStatus == "running" else ""
    fnWriteJobRecord(dictJob)


def _fsDigestCredential(dictCredential):
    """Return a digest of the extracted login, compared in memory only."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        dictCredential, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _fsComposeProjectCredentialPath(dictRuntime, sProvider):
    """Return the project login's container path for one provider."""
    from . import projectRoots
    from .pipelineServer import WORKSPACE_ROOT
    return agentCouncilProviderRegistry.fsComposeProviderCredentialPath(
        sProvider, projectRoots.fsResolveProjectRoot(
            dictRuntime["sContainerId"], WORKSPACE_ROOT))


def _fdictExtractProjectCredential(dictRuntime, sProvider):
    """Read the project login into memory (a Docker file fetch)."""
    return agentCouncilProviderRegistry.fdictExtractProviderCredential(
        sProvider, dictRuntime["connectionDocker"],
        dictRuntime["sContainerId"],
        _fsComposeProjectCredentialPath(dictRuntime, sProvider))


def _fsReadProjectLoginDigest(dictRuntime, sProvider, sCheckId):
    """Return the login's digest; a missing login fails ``sCheckId``."""
    from . import agentCouncilProviders
    try:
        dictCredential = _fdictExtractProjectCredential(dictRuntime, sProvider)
    except agentCouncilProviders.RunnerCredentialError as error:
        raise CredentialCheckFailedError(sCheckId, str(error))
    try:
        return _fsDigestCredential(dictCredential)
    finally:
        dictCredential.clear()


# ----- one test turn ---------------------------------------------------------


def _fdictSingleLiveHandle(dictGateway, sJobId):
    """Return the job's one live runner handle, or refuse ambiguity."""
    sCampaignId = fsComposeTestCampaignId(sJobId)
    listHandles = [dictHandle for dictHandle
                   in dictGateway["dictHandlesById"].values()
                   if dictHandle.get("sCampaignId") == sCampaignId]
    if len(listHandles) != 1:
        raise agentCouncilCredentialStore.CredentialAdmissionRefusedError(
            f"expected one live test runner, found {len(listHandles)}")
    return listHandles[0]


def _ffnBuildTestStager(dictJob, dictRuntime):
    """Build the connection's stager: label read, fetch, then A7 admit.

    Both slow steps — the runner's label inspect and the login fetch —
    happen BEFORE :func:`ftAdmitCredentialTestCredential` takes the
    store lock. The staged path is remembered IN MEMORY only, for check
    5; no record ever names a credential path. The admitter holds a lock
    on the copy, so a hub that dies in the milliseconds it exists leaves
    a copy the sweep can prove orphaned
    (``agentCouncilStagedCopies.fiSweepOrphanedStagedCopies``).
    """
    from . import agentCouncilDockerGateway

    def _ftStageTestCredential():
        dictGateway = dictRuntime["dictGateway"]
        dictHandle = _fdictSingleLiveHandle(dictGateway, dictJob["sJobId"])
        dictProbe = agentCouncilDockerGateway.fdictProbeRunnerAbsence(
            dictGateway["dockerCouncil"], dictHandle["sContainerId"])
        dictCredential = _fdictExtractProjectCredential(
            dictRuntime, dictJob["sProvider"])
        try:
            tStaged = ftAdmitCredentialTestCredential(
                dictJob["sJobId"], dictJob["sProvider"],
                dictJob["sImageIdentity"],
                fsReadRunnerCouncilLabel(dictProbe["dictLabels"]),
                dictCredential)
        finally:
            dictCredential.clear()
        dictRuntime.setdefault("listStagedPaths", []).append(tStaged[0])
        return tStaged

    return _ftStageTestCredential


def fbaBuildEmptySnapshotArchive():
    """Return a valid tar archive with no members (the test copies nothing)."""
    import io
    import tarfile
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w"):
        pass
    return bufferArchive.getvalue()


async def _fdictDriveTestTurn(dictJob, dictRuntime, sModelId, sInstruction,
                              fWallClockSeconds):
    """Run one headless turn in a fresh runner; return its evidence.

    The same connection class, gateway and egress a council turn uses,
    reserved under the job's pseudo-campaign id. The connection destroys
    its runner on every exit path; ``sCompletion`` says whether that
    destruction was PROVEN.
    """
    connectionTurn = dictRuntime["fconnectionBuild"](
        dictJob["sProvider"], dictRuntime["dictGateway"],
        fsComposeTestCampaignId(dictJob["sJobId"]), dictJob["sImageIdentity"],
        dictRuntime.get("baSnapshotTar") or fbaBuildEmptySnapshotArchive(),
        sModelId,
        dictEgress=dictRuntime["dictEgress"],
        ftStageRunnerCredential=_ffnBuildTestStager(dictJob, dictRuntime),
        fWallClockSeconds=fWallClockSeconds,
        saCliProgram=dictRuntime.get("saCliProgram"))
    dictRequest = {"sTurnId": uuid.uuid4().hex, "sPhase": "credentialTest",
                   "sInstructionChannel": sInstruction,
                   "listQuotedMaterial": []}
    await connectionTurn.fdictPrepareImmutableContext(dictRequest)
    await connectionTurn.fnStartTurn(dictRequest)
    dictResult = await connectionTurn.fdictCollectStructuredResult()
    sCompletion = await connectionTurn.fsReportCompletion()
    return {"dictResult": dictResult or {}, "sCompletion": sCompletion}


# ----- the checks --------------------------------------------------------------


def _fnRaiseIfCancelled(dictRuntime, sCheckId):
    """End the job as incomplete when the researcher cancelled it."""
    if dictRuntime["eventCancel"].is_set():
        raise CredentialTestIncompleteError(
            sCheckId, "the test was cancelled")


def _fnRequireProvenDestruction(sCheckId, dictTurn):
    """Fail ``sCheckId`` unless the turn's runner is proven destroyed."""
    from . import agentCouncilProviders
    if dictTurn["sCompletion"] != agentCouncilProviders.S_COMPLETION_TERMINAL:
        raise CredentialCheckFailedError(
            sCheckId, "the runner could not be proven destroyed; it is "
            "held in quarantine until the daemon answers")


def _fnVerifyTokenAndStaging(dictJob, dictRuntime, sDigestBefore, dictTurn):
    """Checks 3, 4 and 5 after a turn: login intact, unrotated, cleaned.

    A turn's own failure does not excuse any of these: whatever the turn
    did, the project's login must be untouched and no copy may remain.
    """
    import os
    sDigestAfter = _fsReadProjectLoginDigest(
        dictRuntime, dictJob["sProvider"], "originalLogin")
    if sDigestAfter != sDigestBefore:
        raise CredentialCheckFailedError(
            "tokenNotRotated", "the project login changed during the test")
    listLeft = [sPath for sPath in dictRuntime.get("listStagedPaths", [])
                if os.path.exists(sPath)]
    if listLeft:
        raise CredentialCheckFailedError(
            "stagingCleaned", f"{len(listLeft)} staged copy(ies) remain")
    _fnRequireProvenDestruction("stagingCleaned", dictTurn)


def _fnClassifyTrivialTurn(dictTurn):
    """Check 2: the turn answered, and was not killed at its deadline."""
    from . import agentCouncilProviders
    sEmpty = dictTurn["dictResult"].get("sEmptyResultReason", "")
    if sEmpty == agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK:
        raise CredentialTestIncompleteError(
            "trivialTurn", "the turn did not answer within the timeout")
    if sEmpty:
        raise CredentialCheckFailedError(
            "trivialTurn", f"the turn did not answer ({sEmpty})")


def _fnClassifyFailurePathTurn(dictTurn):
    """Check 6: an invalid model id must end as a CLASSIFIED failure."""
    from . import agentCouncilProviders
    sEmpty = dictTurn["dictResult"].get("sEmptyResultReason", "")
    if sEmpty == agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK:
        raise CredentialTestIncompleteError(
            "failurePath", "the failing turn did not end within the timeout")
    if not sEmpty:
        raise CredentialCheckFailedError(
            "failurePath", "a turn with an invalid model id was reported "
            "as an answer instead of a failure")


def _fnClassifyKilledTurn(dictTurn):
    """Check 7: the turn really was killed at its (short) deadline."""
    from . import agentCouncilProviders
    sEmpty = dictTurn["dictResult"].get("sEmptyResultReason", "")
    if sEmpty == agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK:
        return
    if not sEmpty:
        raise CredentialTestIncompleteError(
            "runnerKilledMidTurn", "the turn finished before it could be "
            "killed, so the kill path was not exercised; re-run the test")
    raise CredentialCheckFailedError(
        "runnerKilledMidTurn", f"the turn ended as {sEmpty!r} instead of "
        "being killed at its deadline")


async def _fnRunTurnCheck(dictJob, dictRuntime, sCheckId, sModelId,
                          sInstruction, fWallClockSeconds, fnClassify,
                          sDigestBefore):
    """Run one turn-shaped check, then re-verify 3, 4 and 5 after it."""
    _fnRaiseIfCancelled(dictRuntime, sCheckId)
    _fnMarkCheck(dictJob, sCheckId, "running")
    dictTurn = await _fdictDriveTestTurn(
        dictJob, dictRuntime, sModelId, sInstruction, fWallClockSeconds)
    _fnRaiseIfCancelled(dictRuntime, sCheckId)
    fnClassify(dictTurn)
    _fnVerifyTokenAndStaging(dictJob, dictRuntime, sDigestBefore, dictTurn)
    _fnMarkCheck(dictJob, sCheckId, "passed")


async def _fnExecuteChecks(dictJob, dictRuntime):
    """Run checks 1-7 in order; raise at the first that does not hold."""
    sProvider = dictJob["sProvider"]
    _fnMarkCheck(dictJob, "loginPresent", "running")
    sDigestBefore = _fsReadProjectLoginDigest(
        dictRuntime, sProvider, "loginPresent")
    _fnMarkCheck(dictJob, "loginPresent", "passed")
    dictJob["sCliVersion"] = dictRuntime["fsReadCliVersion"](
        dictJob, dictRuntime)
    dictRuntime["dictEgress"] = dictRuntime["fdictProvisionEgress"](
        dictJob, dictRuntime)
    fTimeout = dictRuntime.get("fTurnTimeoutSeconds", F_TURN_TIMEOUT_SECONDS)
    await _fnRunTurnCheck(
        dictJob, dictRuntime, "trivialTurn", dictJob["sRequestedModel"],
        S_TRIVIAL_INSTRUCTION, fTimeout, _fnClassifyTrivialTurn,
        sDigestBefore)
    for sCheckId in ("originalLogin", "tokenNotRotated", "stagingCleaned"):
        _fnMarkCheck(dictJob, sCheckId, "passed")
    await _fnRunTurnCheck(
        dictJob, dictRuntime, "failurePath", S_INVALID_MODEL_ID,
        S_TRIVIAL_INSTRUCTION, fTimeout, _fnClassifyFailurePathTurn,
        sDigestBefore)
    await _fnRunTurnCheck(
        dictJob, dictRuntime, "runnerKilledMidTurn",
        dictJob["sRequestedModel"], S_INTERRUPTED_INSTRUCTION,
        dictRuntime.get("fKillAfterSeconds", F_KILL_AFTER_SECONDS),
        _fnClassifyKilledTurn, sDigestBefore)


# ----- runtime defaults (each a seam the offline tests replace) -------------------


def fsReadCliVersionFromImage(dictJob, dictRuntime):
    """Read ``<cli> --version`` in a no-network, no-credential sandbox.

    The recorded version documents what the test exercised; it is read
    from the image, never typed. A sandbox that cannot answer records
    "unreadable" rather than failing the test, because the image id —
    not this string — is what pins the CLI.
    """
    from . import agentCouncilDockerGateway
    from . import agentCouncilProviders
    dictGateway = dictRuntime["dictGateway"]
    dictLimits = agentCouncilRunner.fdictBuildDefaultRunnerLimits()
    dictCreated = agentCouncilDockerGateway.fdictReserveAndCreateRunner(
        dictGateway, fsComposeTestCampaignId(dictJob["sJobId"]),
        agentCouncilProviders.S_BASELINE_SANDBOX_PROVIDER,
        {"iMemoryBytes": dictLimits["iMemoryBytes"],
         "fCpuCount": dictLimits["fCpuCount"]},
        dictJob["sImageIdentity"], bSandbox=True)
    if not dictCreated["bCreated"]:
        return "unreadable"
    try:
        dictExecuted = agentCouncilDockerGateway.fdictExecuteBoundedTurn(
            dictGateway, dictCreated["sHandle"],
            _flistCliProgram(dictJob["sProvider"]) + ["--version"],
            fWallClockSeconds=30.0)
    finally:
        agentCouncilDockerGateway.fdictDestroyAndSettle(
            dictGateway, dictCreated["sHandle"])
    listLines = (dictExecuted.get("sOutput") or "").strip().splitlines()
    if dictExecuted.get("iExitCode") != 0 or not listLines:
        return "unreadable"
    return listLines[0][:200]


def _flistCliProgram(sProvider):
    """Return the provider CLI's program vector."""
    from . import agentCouncilAntigravityProvider
    from . import agentCouncilCodexProvider
    from . import agentCouncilProviders
    return list({
        "claude": agentCouncilProviders.LIST_CLAUDE_CLI_PROGRAM,
        "codex": agentCouncilCodexProvider.LIST_CODEX_CLI_PROGRAM,
        "gemini": agentCouncilAntigravityProvider.LIST_ANTIGRAVITY_CLI_PROGRAM,
    }[sProvider])


def fdictProvisionTestEgress(dictJob, dictRuntime):
    """Create the job's own internal network and allowlisting proxy."""
    from . import agentCouncilDockerGateway
    from . import agentCouncilEgress
    sScope = fsComposeTestCampaignId(dictJob["sJobId"])
    dictGateway = dictRuntime["dictGateway"]
    dictRuntime["bEgressProvisioned"] = True
    sNetworkName = agentCouncilDockerGateway.fsCreateCampaignInternalNetwork(
        dictGateway, sScope)
    sProxyAddress = agentCouncilDockerGateway.fsLaunchAllowlistProxy(
        dictGateway, sScope,
        agentCouncilProviderRegistry.flistCollectProviderEgressHostnames(
            {dictJob["sProvider"]}))
    return {"sNetworkName": sNetworkName,
            "sProxyInternalAddress": sProxyAddress,
            "iProxyPort": agentCouncilEgress.I_PROXY_LISTEN_PORT}


def fdictBuildJobRuntime(connectionDocker, dockerCouncil, dictRegistry,
                         sResourceName, sContainerId):
    """Compose the production runtime for one job."""
    from . import agentCouncilDockerGateway
    import threading
    return {
        "connectionDocker": connectionDocker, "sContainerId": sContainerId,
        "dictGateway": agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
            dockerCouncil, dictRegistry, sResourceName),
        "eventCancel": threading.Event(),
        "fconnectionBuild":
            agentCouncilProviderRegistry.fconnectionBuildProviderConnection,
        "fsReadCliVersion": fsReadCliVersionFromImage,
        "fdictProvisionEgress": fdictProvisionTestEgress,
        "dictEgress": None, "bEgressProvisioned": False,
    }


# ----- lifecycle -------------------------------------------------------------------


def fdictStartCredentialTest(dictJobsInProcess, sProvider, sImageIdentity,
                             sResourceName, sRequestedModel, dictRuntime):
    """Record consent, claim the key's test lock, and start the job thread.

    Returns ``{"sJobId", "bAlreadyRunning"}``. A second request for a
    key whose test is running gets the running job's id, never a second
    job — the test lock is the duplicate guard. The job lock is handed
    to the job thread, which holds it until the outcome is published.
    """
    import threading
    sDirectory = agentCouncilCredentialGate.fsResolveCredentialStoreDirectory()
    fileJobLock = agentCouncilCredentialStore.ffileTryAcquireTestLock(
        sDirectory, sProvider, sImageIdentity)
    if fileJobLock is None:
        return {"sJobId": _fsRunningJobIdFor(sProvider, sImageIdentity),
                "bAlreadyRunning": True}
    try:
        dictJob = _fdictBeginJob(sProvider, sImageIdentity, sResourceName,
                                 sRequestedModel, dictRuntime)
    except BaseException:
        fileJobLock.close()
        raise
    dictRuntime["fileJobLock"] = fileJobLock
    threadJob = threading.Thread(
        target=fnRunCredentialTestJob, args=(dictJob, dictRuntime),
        name=f"vaibify-credential-test-{dictJob['sJobId'][:8]}", daemon=True)
    dictJobsInProcess[dictJob["sJobId"]] = {
        "dictRuntime": dictRuntime, "threadJob": threadJob}
    threadJob.start()
    return {"sJobId": dictJob["sJobId"], "bAlreadyRunning": False}


def _fdictBeginJob(sProvider, sImageIdentity, sResourceName, sRequestedModel,
                   dictRuntime):
    """Consent, mint the job, record its marker and its durable record."""
    sEvidencePath = agentCouncilCredentialGate.fsResolveCredentialEvidencePath()
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, sProvider, sImageIdentity)
    sJobId = fsComposeTestJobId()
    dictJob = fdictCreateJobRecord(
        sJobId, sProvider, sImageIdentity, sResourceName,
        dictRuntime["sContainerId"], sRequestedModel)
    fnWriteJobRecord(dictJob)
    dictMarker = agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, sProvider, sImageIdentity, sJobId,
        {"sResourceName": sResourceName})
    dictJob["iConsentGeneration"] = dictMarker["iConsentGeneration"]
    fnWriteJobRecord(dictJob)
    return dictJob


def _fsRunningJobIdFor(sProvider, sImageIdentity):
    """Return the job id the store records as in flight for a key."""
    dictDocument = agentCouncilCredentialStore.fdictReadCredentialDocument(
        agentCouncilCredentialGate.fsResolveCredentialEvidencePath()
    )["dictDocument"]
    return (dictDocument["dictInFlight"].get(
        agentCouncilCredentialStore.fsComposeCredentialKey(
            sProvider, sImageIdentity)) or {}).get("sJobId", "")


def fnRunCredentialTestJob(dictJob, dictRuntime):
    """Run every check, then publish exactly one durable outcome.

    Blocking; runs on the job's own thread. A check that does not hold
    ends the job ``failed`` naming it; a timeout, a cancellation, a
    withdrawal mid-test, or a fault in vaibify's own machinery ends it
    ``incomplete``. Resources are released and the outcome published on
    every path, and the job lock is released last.
    """
    import asyncio
    sOutcome, sCheckId, sDetail = (
        agentCouncilCredentialStore.S_OUTCOME_PASSED, "", "")
    try:
        asyncio.run(_fnExecuteChecks(dictJob, dictRuntime))
    except CredentialCheckFailedError as error:
        sOutcome, sCheckId, sDetail = (
            agentCouncilCredentialStore.S_OUTCOME_FAILED, error.sCheckId,
            error.sDetail)
    except CredentialTestIncompleteError as error:
        sOutcome, sCheckId, sDetail = (
            agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE,
            error.sCheckId, error.sDetail)
    except Exception as error:
        sOutcome, sCheckId, sDetail = (
            agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE,
            dictJob.get("sCurrentCheck", ""),
            f"{type(error).__name__}: {error}")
    finally:
        _fnFinishJob(dictJob, dictRuntime, sOutcome, sCheckId, sDetail)


def _fnFinishJob(dictJob, dictRuntime, sOutcome, sCheckId, sDetail):
    """Release resources, publish the outcome, persist, drop the lock.

    Cleanup that cannot be PROVEN is never a pass: a runner or network
    left unproven turns a passing test into ``incomplete`` at the
    clean-up check, the leftovers are named in the job record, and the
    restart sweep keeps retrying them until they are proven gone.
    """
    try:
        listUnsettled = _flistReleaseTestResources(dictJob, dictRuntime)
        dictJob["listUnsettledResources"] = listUnsettled
        if listUnsettled:
            sDetail = fsDescribeUnsettled(listUnsettled, sDetail)
            if sOutcome == agentCouncilCredentialStore.S_OUTCOME_PASSED:
                sOutcome, sCheckId = (
                    agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE,
                    "stagingCleaned")
        if sCheckId in SET_CHECK_IDS:
            _fnMarkCheck(dictJob, sCheckId, sOutcome, sDetail)
        fnPublishJobOutcome(dictJob, sOutcome, sCheckId, sDetail)
    finally:
        fileJobLock = dictRuntime.pop("fileJobLock", None)
        if fileJobLock is not None:
            fileJobLock.close()


def fsDescribeUnsettled(listUnsettled, sDetail):
    """Name what could not be proven removed, keeping any earlier detail."""
    sUnsettled = (
        "the test's own resources could not be proven removed ("
        + ", ".join(listUnsettled) + "); they are kept for the next start "
        "to reconcile, and the provider stays off until a test passes")
    return f"{sDetail}; {sUnsettled}" if sDetail else sUnsettled


def _flistReleaseTestResources(dictJob, dictRuntime):
    """Destroy live runners, remove egress, delete staged copies.

    Returns the resources whose removal was NOT proven — empty only
    when every runner was proven destroyed and the egress proven gone.
    """
    import os
    from . import agentCouncilDockerGateway
    from ..config import secretManager
    secretManager.fnCleanupSecretFiles(
        [sPath for sPath in dictRuntime.get("listStagedPaths", [])
         if os.path.exists(sPath)])
    dictGateway = dictRuntime.get("dictGateway") or {}
    listUnsettled = []
    for sHandle, dictHandle in list(
            (dictGateway.get("dictHandlesById") or {}).items()):
        try:
            sOutcome = agentCouncilDockerGateway.fdictDestroyAndSettle(
                dictGateway, sHandle).get("sOutcome")
        except Exception:
            sOutcome = ""
        if sOutcome != agentCouncilRunner.S_OUTCOME_DESTROYED:
            listUnsettled.append(
                "runner " + dictHandle.get("sContainerName", sHandle))
    if dictRuntime.get("bEgressProvisioned"):
        listUnsettled.extend(flistRemoveTestEgress(dictJob, dictGateway))
    return listUnsettled


def flistRemoveTestEgress(dictJob, dictGateway):
    """Remove the job's proxy and network; return what stayed unproven."""
    from . import agentCouncilDockerGateway
    try:
        dictRemoved = (
            agentCouncilDockerGateway.fdictRemoveCampaignEgressResources(
                dictGateway, fsComposeTestCampaignId(dictJob["sJobId"])))
    except Exception:
        return ["the test's network and proxy"]
    return list(dictRemoved["saIndeterminateResources"])


def fnPublishJobOutcome(dictJob, sOutcome, sCheckId, sDetail):
    """Publish to the store and persist the job's terminal record."""
    dictDetails = {"sFailedCheck": sCheckId if sOutcome != (
        agentCouncilCredentialStore.S_OUTCOME_PASSED) else "",
        "sDetail": sDetail, "sCliVersion": dictJob.get("sCliVersion", ""),
        "listPassedChecks": [dictCheck["sCheckId"]
                             for dictCheck in dictJob["listChecks"]
                             if dictCheck["sStatus"] == "passed"],
        "listModelIds": [dictJob["sRequestedModel"]]}
    try:
        agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
            agentCouncilCredentialGate.fsResolveCredentialEvidencePath(),
            dictJob["sProvider"], dictJob["sImageIdentity"],
            dictJob["sJobId"], sOutcome, dictDetails)
    except agentCouncilCredentialStore.CredentialStoreError as error:
        sOutcome, sDetail = (
            agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE, str(error))
    dictJob.update({"sStatus": sOutcome, "sFailedCheck":
                    dictDetails["sFailedCheck"], "sDetail": sDetail,
                    "sCurrentCheck": "",
                    "sFinishedIso": agentCouncilCredentialStore.fsNowIso()})
    fnWriteJobRecord(dictJob)


def fbRequestCredentialTestCancel(dictJobsInProcess, sJobId):
    """Cancel a job this hub runs: flag it, then kill its live runners.

    Killing the runner is what ends a turn that is mid-stream; the job
    thread then settles the handle, deletes staging and records
    ``incomplete``. Returns False for a job this hub is not running.
    """
    from . import agentCouncilDockerGateway
    dictEntry = dictJobsInProcess.get(sJobId)
    if dictEntry is None or not dictEntry["threadJob"].is_alive():
        return False
    dictRuntime = dictEntry["dictRuntime"]
    dictRuntime["eventCancel"].set()
    dictGateway = dictRuntime.get("dictGateway") or {}
    for dictHandle in list((dictGateway.get("dictHandlesById") or {})
                           .values()):
        agentCouncilDockerGateway.fdictDestroyRunnerAndProveAbsence(
            dictGateway["dockerCouncil"], dictHandle["sContainerId"])
    return True
