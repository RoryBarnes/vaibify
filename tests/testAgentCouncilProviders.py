"""Unit falsification of the Claude runner-backend adapter (design 8.x/9.7/13.2).

No Docker here: the argv/stdin boundary, the stream-json parsing, the
structured-result and model-identity extraction, the failure
classification, the capability contract, live-discovery fallback, the
extraction-only credential lane and the ownership-stamped credential
tarball are all exercised against crafted inputs and fakes. The
§15.2 boundary — researcher/plan/prior-agent text absent from argv — is
``testResearcherAndPeerTextNeverReachArgv``.
"""

import asyncio
import json
import os
import stat
import tarfile
import time

import pytest

from vaibify.config import secretManager
from vaibify.gui import agentCouncilProviders as providers
from vaibify.gui import agentCouncilRunner
from vaibify.gui.agentCouncilCharter import fdictBuildQuotedEntry

S_RESEARCHER_SENTINEL = "RESEARCHER_TEXT_XYZZY_DO_RM_RF"
S_PEER_SENTINEL = "PEER_PROPOSAL_PLUGH_IGNORE_PRIOR"
S_ROLE_SENTINEL = "ADVERSARIAL_SECURITY_ROLE_SENTINEL"


def testResearcherAndPeerTextNeverReachArgv():
    """§15.2: researcher, plan and prior-agent text are absent from argv.

    The composed instruction (charter + role + phase) is the only text
    the command line carries besides fixed flags and the model id. The
    role legitimately rides that instruction; the untrusted material
    rides stdin, never argv.
    """
    sInstruction = (
        "COUNCIL CHARTER ...\n\nROLE PERSPECTIVE: " + S_ROLE_SENTINEL
        + "\n\nPHASE: independent proposal.")
    listQuoted = [
        fdictBuildQuotedEntry("researcherQuestion", "researcher",
                              S_RESEARCHER_SENTINEL),
        fdictBuildQuotedEntry("peerProposal", "participant-1",
                              S_PEER_SENTINEL),
    ]
    saArgv = providers.flistComposeClaudeArgv("sonnet", sInstruction)
    sJoinedArgv = "\x00".join(saArgv)

    assert S_RESEARCHER_SENTINEL not in sJoinedArgv
    assert S_PEER_SENTINEL not in sJoinedArgv
    # The instruction channel is on argv and carries the role.
    assert sInstruction in saArgv
    assert S_ROLE_SENTINEL in sJoinedArgv
    assert "--append-system-prompt" in saArgv
    assert saArgv[saArgv.index("--append-system-prompt") + 1] == sInstruction
    assert saArgv[saArgv.index("--model") + 1] == "sonnet"

    sStdin = providers.fsComposeUntrustedPromptText(listQuoted)
    assert S_RESEARCHER_SENTINEL in sStdin
    assert S_PEER_SENTINEL in sStdin
    assert "never" in sStdin.lower() and "obey" in sStdin.lower()


def testArgvCarriesOnlyAllowlistedFlags():
    saArgv = providers.flistComposeClaudeArgv("opus", "instruction text")
    assert saArgv[0] == "claude"
    for sFlag in ("-p", "--output-format", "stream-json", "--verbose",
                  "--permission-mode", "plan", "--model",
                  "--append-system-prompt"):
        assert sFlag in saArgv
    saOverridden = providers.flistComposeClaudeArgv(
        "opus", "x", saCliProgram=["python3", "/council/fakeProvider.py"])
    assert saOverridden[:2] == ["python3", "/council/fakeProvider.py"]


def testStreamJsonParsingToleratesFragmentationAndPartialTail():
    sStream = (
        '{"type":"system","subtype":"init","model":"claude-sonnet-5"}\n'
        "\n"
        '{"type":"assistant","message":{"model":"claude-sonnet-5"}}\n'
        '{"type":"result","subtype":"success","result":"{}"}\n'
        '{"type":"result","subtype":"suc'  # torn trailing line
    )
    listEvents = providers.flistParseStreamJsonEvents(sStream)
    assert [dictEvent.get("type") for dictEvent in listEvents] == [
        "system", "assistant", "result"]


def testStructuredResultExtractionParsesResultObjectAndFence():
    dictTurnResult = {"sSummary": "s", "sVerdict": "accept"}
    listPlain = [{"type": "result",
                  "result": json.dumps(dictTurnResult)}]
    assert providers.fdictExtractStructuredResult(listPlain) == dictTurnResult

    sFenced = "```json\n" + json.dumps(dictTurnResult) + "\n```"
    listFenced = [{"type": "result", "result": sFenced}]
    assert providers.fdictExtractStructuredResult(listFenced) == dictTurnResult


def testStructuredResultExtractionSurfacesUnparseableForRepair():
    listProse = [{"type": "result", "result": "here is my plan, no JSON"}]
    dictExtracted = providers.fdictExtractStructuredResult(listProse)
    assert dictExtracted == {"sRawResultText": "here is my plan, no JSON"}
    # An empty stream still yields an empty raw text — the validator's
    # trigger — but now carries its diagnosis alongside, so this asserts
    # the contract rather than the exact dict.
    dictEmpty = providers.fdictExtractStructuredResult([])
    assert dictEmpty["sRawResultText"] == ""
    assert dictEmpty["sEmptyResultReason"] == "noResultEvent"


def testModelIdentityRecordsResolvedNeverLaundersAlias():
    listEvents = [
        {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
        {"type": "result", "result": "{}",
         "modelUsage": {"claude-sonnet-5": {"inputTokens": 10}},
         "usage": {"input_tokens": 10, "output_tokens": 4}},
    ]
    dictIdentity = providers.fdictExtractModelIdentity(listEvents, "sonnet")
    assert dictIdentity["sRequestedModel"] == "sonnet"
    assert dictIdentity["sResolvedModel"] == "claude-sonnet-5"
    assert dictIdentity["dictUsage"]["input_tokens"] == 10
    assert "claude-sonnet-5" in dictIdentity["dictModelUsage"]

    # No resolved id anywhere: the alias is NOT laundered into a
    # declaration (section 13.2) — the field stays empty.
    dictNoResolved = providers.fdictExtractModelIdentity(
        [{"type": "result", "result": "{}"}], "sonnet")
    assert dictNoResolved["sResolvedModel"] == ""


def testFailureClassification():
    assert providers.fsClassifyTurnFailure(None, []) == (
        providers.S_FAILURE_KILLED_NO_EXIT_CODE)
    assert providers.fsClassifyTurnFailure(0, []) == (
        providers.S_FAILURE_NO_RESULT_EVENT)
    assert providers.fsClassifyTurnFailure(
        0, [{"type": "result", "is_error": False, "result": "{}"}]) == (
        providers.S_FAILURE_CLEAN_EXIT)
    assert providers.fsClassifyTurnFailure(
        1, [{"type": "result", "is_error": False, "result": ""}]) == (
        providers.S_FAILURE_NON_ZERO_EXIT)
    assert providers.fsClassifyTurnFailure(
        1, [{"type": "result", "is_error": True,
             "result": "authentication error: invalid credential"}]) == (
        providers.S_FAILURE_AUTHENTICATION)
    assert providers.fsClassifyTurnFailure(
        1, [{"type": "result", "is_error": True,
             "result": "rate limit exceeded"}]) == (
        providers.S_FAILURE_RATE_LIMIT)


def testModelDiscoveryFallsBackToLabelledAliasSetWithoutKey():
    dictDiscovery = providers.fdictDiscoverClaudeModels(sApiKey=None)
    assert dictDiscovery["bVerified"] is False
    assert dictDiscovery["sSource"] == "cliAliasFallback"
    assert "sonnet" in dictDiscovery["listModelIds"]


def testCapabilityContractDeclaresSeparableInstructionChannel():
    dictContract = providers.fdictClaudeCapabilityContract(sApiKey=None)
    assert dictContract["sProvider"] == "claude"
    assert dictContract["sBackend"] == "runner"
    assert dictContract["bHasSeparableInstructionChannel"] is True
    assert dictContract["sInstructionChannelFlag"] == "--append-system-prompt"
    assert dictContract["bRequiresCredentialDelivery"] is True
    assert dictContract["sCredentialField"] == "accessToken"
    assert providers.S_ANTHROPIC_API_HOSTNAME in (
        dictContract["saEgressAllowlist"])
    assert dictContract["bExtractsModelIdentity"] is True
    assert dictContract["bExtractsUsage"] is True


def _fiEpochMillisecondsFromNow(fSecondsAhead):
    """Return an epoch-millisecond stamp offset from now."""
    return int((time.time() + fSecondsAhead) * 1000)


class _FakeCredentialConnection:
    """A minimal connection exposing only the typed named-file read."""

    def __init__(self, baContentOrError):
        self._baContentOrError = baContentOrError

    def fbaFetchCredentialFile(self, sContainerId, sFilePath):
        return self.fbaFetchFile(sContainerId, sFilePath)

    def fbaFetchFile(self, sContainerId, sFilePath, iMaxBytes=None):
        if isinstance(self._baContentOrError, Exception):
            raise self._baContentOrError
        return self._baContentOrError


def testCredentialExtractionCopiesAccessTokenAndScopesNeverRefresh():
    """Both halves of the section 9.7 contract, in both directions.

    Scopes MUST come across — without them the staged document is one
    the CLI refuses outright, which is the defect the 2026-08-22
    ceremony found. The refresh token MUST NOT, because it can mint new
    sessions. Asserting the exact dict pins both: dropping scopes or
    admitting the refresh token each fails here.
    """
    dictLogin = {"claudeAiOauth": {
        "accessToken": "ACCESS-TOKEN-KEEP",
        "refreshToken": "REFRESH-TOKEN-NEVER-COPY",
        # A LIVE expiry. This read 123 (epoch 1970) until the expiry
        # guard landed, which refused it correctly — this test is about
        # scopes and the refresh token, so it needs a token that is not
        # independently rejected.
        "expiresAt": _fiEpochMillisecondsFromNow(3600),
        "scopes": ["user:inference"]}}
    connectionFake = _FakeCredentialConnection(
        json.dumps(dictLogin).encode("utf-8"))
    dictCredential = providers.fdictExtractRunnerCredential(
        connectionFake, "container-abc", "/root/.claude/.credentials.json")
    assert dictCredential == {
        "sAccessToken": "ACCESS-TOKEN-KEEP",
        "listScopes": ["user:inference"],
    }
    assert "REFRESH-TOKEN-NEVER-COPY" not in json.dumps(dictCredential)


def testCredentialExtractionRefusesMissingFileAndMissingToken():
    connectionMissing = _FakeCredentialConnection(
        FileNotFoundError("no file"))
    with pytest.raises(providers.RunnerCredentialError):
        providers.fdictExtractRunnerCredential(
            connectionMissing, "c", "/root/.claude/.credentials.json")
    connectionNoToken = _FakeCredentialConnection(
        json.dumps({"claudeAiOauth": {"refreshToken": "r"}}).encode())
    with pytest.raises(providers.RunnerCredentialError):
        providers.fdictExtractRunnerCredential(
            connectionNoToken, "c", "/root/.claude/.credentials.json")


def testStageCredentialFileIsNarrowModeSixHundredAndCleansUp():
    """Narrow, mode 600, cleaned up — and CARRYING THE SCOPES.

    The scopes assertion is the one with history. This test previously
    demanded ``{"accessToken": ...}`` and nothing else, so it PASSED
    against a document the CLI refuses outright ("Not logged in ·
    Please run /login"), and would have gone on passing forever. The
    defect was invisible to it because it checked the shape the code
    produced rather than the shape the CLI accepts — which no unit test
    can know, and which the 2026-08-22 credential ceremony measured.

    So the direction matters: scopes MUST be present (or the login does
    not work at all) and refreshToken MUST be absent (section 9.7's
    blast-radius rule). Asserting the whole document pins both, and
    fails if either half is quietly changed.
    """
    sPath = providers.fsStageRunnerCredentialFile(
        "ACCESS-ONLY", ["user:inference", "user:profile"])
    try:
        assert os.path.exists(sPath)
        assert stat.S_IMODE(os.stat(sPath).st_mode) == 0o600
        dictWritten = json.loads(open(sPath).read())
        assert dictWritten == {"claudeAiOauth": {
            "accessToken": "ACCESS-ONLY",
            "scopes": ["user:inference", "user:profile"],
        }}
        assert "refreshToken" not in dictWritten["claudeAiOauth"]
    finally:
        secretManager.fnCleanupSecretFiles([sPath])
    assert not os.path.exists(sPath)


def testCredentialContainerPathResolverRefusesRelativeRoot():
    assert providers.fsComposeCredentialContainerPath("/workspace/repo") == (
        "/workspace/repo/.claude/.credentials.json")
    with pytest.raises(providers.RunnerCredentialError):
        providers.fsComposeCredentialContainerPath("relative/root")


def testStampedFileTarballCarriesCouncilUserOwnership():
    """The ownership invariant the architectural test defers to here.

    ``fbaBuildStampedFileTarball`` stamps both entries to 1000:1000 with
    empty owner names, so a copied credential can never land root-owned
    (design section 9.7; the file-ownership trap).
    """
    baTarball = agentCouncilRunner.fbaBuildStampedFileTarball(
        "configDir", ".credentials.json", b'{"token":"x"}')
    import io
    with tarfile.open(fileobj=io.BytesIO(baTarball), mode="r") as fileTar:
        listMembers = fileTar.getmembers()
    assert {member.name for member in listMembers} == {
        "configDir", "configDir/.credentials.json"}
    for infoMember in listMembers:
        assert infoMember.uid == 1000
        assert infoMember.gid == 1000
        assert infoMember.uname == ""
        assert infoMember.gname == ""
    dictByName = {member.name: member for member in listMembers}
    assert dictByName["configDir/.credentials.json"].mode == 0o600
    assert dictByName["configDir"].mode == 0o700


def test_charter_rides_the_instruction_flag_never_a_snapshot_file():
    """R11 belt two: the charter is delivered as ``--append-system-prompt``.

    The composed argv carries the server-owned instruction as a FLAG
    value; nothing in the adapter writes an instruction file into the
    snapshot tree (which a hostile repo could shadow or a provider
    could rank below its own repo-doc conventions). Whether a real
    model obeys a hostile surviving doc over this flag is the
    maintainer's paid-model empiric — recorded, never assumed here.
    """
    from vaibify.gui.agentCouncilProviders import flistComposeClaudeArgv
    saArgv = flistComposeClaudeArgv("modelOne", "THE COUNCIL CHARTER")
    iFlag = saArgv.index("--append-system-prompt")
    assert saArgv[iFlag + 1] == "THE COUNCIL CHARTER"
    import inspect
    from vaibify.gui import agentCouncilProviders
    sSource = inspect.getsource(agentCouncilProviders)
    assert ".md" not in sSource.replace("agentCouncil.md", ""), (
        "the adapter must never write or reference an agent-doc file")


def _fconnectionLoginExpiringIn(fSecondsAhead, bIncludeExpiry=True):
    """A fake connection serving a login with the given expiry."""
    dictOauth = {"accessToken": "ACCESS-TOKEN",
                 "scopes": ["user:inference"]}
    if bIncludeExpiry:
        dictOauth["expiresAt"] = _fiEpochMillisecondsFromNow(fSecondsAhead)
    return _FakeCredentialConnection(
        json.dumps({"claudeAiOauth": dictOauth}).encode("utf-8"))


def testAnExpiredLoginIsRefusedBeforeAnyRunnerIsBuilt():
    """The defect that cost a live council two runners and said nothing.

    Kills: extracting a credential without checking its expiry.

    Section 9.7 stages the access token WITHOUT the refresh token, so a
    runner handed an expired token cannot renew it: the CLI reports
    itself logged out and exits without calling the API. Nothing in the
    obvious places shows it — the model resolves, the exit is clean,
    the usage block is all zeroes — and the recorded failure was a
    schema complaint listing every absent field (2026-08-24). Expiry is
    a timestamp in the document, so it is knowable before a runner
    exists.
    """
    with pytest.raises(providers.RunnerCredentialError) as errorInfo:
        providers.fdictExtractRunnerCredential(
            _fconnectionLoginExpiringIn(-3600), "container-abc",
            "/root/.claude/.credentials.json")
    sMessage = str(errorInfo.value)
    assert "expired" in sMessage
    assert "refresh" in sMessage, (
        "the message does not say WHY the runner cannot recover, so it "
        "reads as a vaibify bug rather than a login to refresh")
    assert "claude" in sMessage, (
        f"the message names no remedy: {sMessage!r}")


def testALiveLoginIsAcceptedSoTheGuardIsNotJustRefusingEverything():
    """The other half of the pair.

    Kills: refusing every credential regardless of expiry.

    A guard that always refused would satisfy the assertion above and
    ground every council permanently.
    """
    dictCredential = providers.fdictExtractRunnerCredential(
        _fconnectionLoginExpiringIn(3600), "container-abc",
        "/root/.claude/.credentials.json")
    assert dictCredential["sAccessToken"] == "ACCESS-TOKEN"


def testALoginWithoutAnExpiryFieldIsAcceptedRatherThanGuessedAt():
    """Absence of the field is not evidence of expiry.

    Refusing on a field we cannot read would ground every council on a
    guess if the provider's document shape ever changed. Spending one
    turn to learn the truth is the better failure.
    """
    dictCredential = providers.fdictExtractRunnerCredential(
        _fconnectionLoginExpiringIn(0, bIncludeExpiry=False),
        "container-abc", "/root/.claude/.credentials.json")
    assert dictCredential["sAccessToken"] == "ACCESS-TOKEN"


def testTheExpiryRefusalReachesTheLaunchProbeAsProse():
    """A boolean probe would report the wrong remedy.

    Kills: collapsing the credential refusal back to a boolean.

    The launch refusal used to say "this project has no Claude login",
    which is the wrong instruction for a login that is present, parses,
    and has merely expired: the researcher is told to log in when the
    remedy is to refresh.
    """
    sExplanation = providers.fsExplainUnusableRunnerCredential(
        _fconnectionLoginExpiringIn(-3600), "container-abc",
        "/root/.claude/.credentials.json")
    assert "expired" in sExplanation
    assert providers.fsExplainUnusableRunnerCredential(
        _fconnectionLoginExpiringIn(3600), "container-abc",
        "/root/.claude/.credentials.json") == "", (
        "a usable login produced an explanation, so the launch probe "
        "would refuse a project that can convene")


def testAnEmptyResultSaysWhichKindOfEmptyItIs():
    """Two causes, two diagnoses — they used to be one blank record.

    Kills: returning a bare empty result without its reason.

    A stream that ended with no result event and a result event
    carrying no text both produced {"sRawResultText": ""}, so the
    engine recorded "every schema field is missing" and the two — a CLI
    that died before answering, and one that answered with nothing —
    were indistinguishable. A live opus turn hit the first (2026-08-24).
    """
    dictNoEvent = providers.fdictExtractStructuredResult([
        {"type": "system"}, {"type": "assistant"}])
    assert dictNoEvent["sEmptyResultReason"] == "noResultEvent"
    assert dictNoEvent["dictEventTypeCounts"] == {
        "system": 1, "assistant": 1}

    dictNoText = providers.fdictExtractStructuredResult([
        {"type": "result", "is_error": True, "subtype": "error_max_turns"}])
    assert dictNoText["sEmptyResultReason"] == "resultEventCarriedNoText"
    assert dictNoText["bResultEventReportedError"] is True
    assert dictNoText["sResultEventSubtype"] == "error_max_turns"


def testTheDiagnosisNeverCopiesModelOutputIntoTheRecord():
    """The diagnostic must stay metadata about the stream.

    It rides into the campaign record, which is rewritten on every
    checkpoint, so a field that grew to carry participant text would be
    paid for repeatedly — and an empty result has no output worth
    carrying anyway.
    """
    dictEmpty = providers.fdictExtractStructuredResult([
        {"type": "assistant",
         "message": {"content": "SENSITIVE-MODEL-PROSE"}},
        {"type": "result", "result": None}])
    assert "SENSITIVE-MODEL-PROSE" not in json.dumps(dictEmpty)


def testAUsableResultIsUnaffectedByTheDiagnosis():
    """The other half: a good turn must not grow diagnostic fields.

    Kills: attaching the diagnosis unconditionally.
    """
    dictGood = providers.fdictExtractStructuredResult([
        {"type": "result", "result": json.dumps({"sSummary": "ok"})}])
    assert dictGood == {"sSummary": "ok"}


def testAWallClockKillIsNamedRatherThanGuessedAt():
    """The opus failure, correctly attributed at last.

    Kills: diagnosing an empty result from the event stream alone.

    A turn killed at its wall-clock budget has its container destroyed
    mid-stream: no result event, no error, nothing in the events to
    distinguish it from a model that simply stopped. The gateway
    recorded bWallClockExceeded and the elapsed seconds all along and
    the diagnosis never read them — so I twice told the researcher
    their council had been rate limited, inferring it from a
    `rate_limit_event` that the CLI emits as routine telemetry
    (2026-08-24).
    """
    dictEmpty = providers.fdictExtractStructuredResult(
        [{"type": "system"}, {"type": "assistant"},
         {"type": providers.S_RATE_LIMIT_EVENT_TYPE}],
        {"bWallClockExceeded": True, "fElapsedSeconds": 300.4,
         "iExitCode": None})
    assert dictEmpty["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_WALL_CLOCK)
    assert dictEmpty["fElapsedSeconds"] == 300.4


def testARateLimitEventIsRecordedButNeverTreatedAsTheCause():
    """Co-occurrence is not causation, and this one cost real trust.

    Kills: concluding "rate limited" from the event's presence.

    The same stream that carries a rate_limit_event carries it whether
    or not a limit was hit. It stays in the tally — a reader may weigh
    it — but it must not become the reason.
    """
    dictEmpty = providers.fdictExtractStructuredResult(
        [{"type": "assistant"},
         {"type": providers.S_RATE_LIMIT_EVENT_TYPE}],
        {"bWallClockExceeded": False, "fElapsedSeconds": 9.0,
         "iExitCode": 1})
    assert dictEmpty["sEmptyResultReason"] == "noResultEvent"
    assert dictEmpty["dictEventTypeCounts"][
        providers.S_RATE_LIMIT_EVENT_TYPE] == 1


def testAnOrdinaryTruncationIsNotBlamedOnARateLimit():
    """The other half: only a real rate-limit event says rate limit.

    Kills: reporting every empty result as rate limited.

    A stream that simply died has a different remedy from one the
    provider throttled, and conflating them would send the researcher
    to wait out a limit that was never hit.
    """
    dictEmpty = providers.fdictExtractStructuredResult([
        {"type": "system"}, {"type": "assistant"}])
    assert dictEmpty["sEmptyResultReason"] == "noResultEvent"


def testTheConnectionHandsItsExecutionRecordToTheDiagnosis():
    """The WIRING, not the diagnosis — they fail independently.

    Kills: calling fdictExtractStructuredResult without the execution
    record.

    A correct diagnosis that is never given the facts it needs is the
    shape this bug had for three sessions: the gateway recorded
    bWallClockExceeded, the extractor could read it, and nothing
    connected the two — so every wall-clock kill was reported as an
    unexplained empty result. Asserting the pure function alone leaves
    that gap wide open, and a mutation removing the argument survived a
    suite that only did that.
    """
    connection = providers.ClaudeRunnerConnection.__new__(
        providers.ClaudeRunnerConnection)
    connection._listEvents = [{"type": "assistant"}]
    connection._dictTurnExecution = {
        "bWallClockExceeded": True, "fElapsedSeconds": 300.2,
        "iExitCode": None}
    dictResult = asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(connection.fdictCollectStructuredResult())
    assert dictResult["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_WALL_CLOCK), (
        "the connection did not pass its execution record on, so the "
        "wall-clock kill is invisible to the diagnosis")
    assert dictResult["fElapsedSeconds"] == 300.2


def testAnOutputCapKillIsDistinguishedFromATimeoutAndFromNeither():
    """Three outcomes the record must not conflate.

    Kills: recording only the wall-clock flag.

    The gateway kills on the output cap OR the deadline. Only the
    deadline was recorded, so a turn killed by the cap reported "no
    result event" with every flag false and an exit code of 137 that
    nothing read — and I argued two wrong causes from that record,
    rate limiting and then a timeout, before the missing field made
    the difference visible (2026-08-25).
    """
    dictCap = providers.fdictExtractStructuredResult(
        [{"type": "assistant"}],
        {"bOutputCapExceeded": True, "bWallClockExceeded": False,
         "iExitCode": 137, "fElapsedSeconds": 473.0})
    assert dictCap["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_OUTPUT_CAP)

    dictClock = providers.fdictExtractStructuredResult(
        [{"type": "assistant"}],
        {"bOutputCapExceeded": False, "bWallClockExceeded": True,
         "iExitCode": 137, "fElapsedSeconds": 3600.0})
    assert dictClock["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_WALL_CLOCK)

    dictNeither = providers.fdictExtractStructuredResult(
        [{"type": "assistant"}],
        {"bOutputCapExceeded": False, "bWallClockExceeded": False,
         "iExitCode": 1, "fElapsedSeconds": 9.0})
    assert dictNeither["sEmptyResultReason"] == "noResultEvent", (
        "a turn that hit neither bound was blamed on one of them")
    assert dictNeither["jsonExitCode"] == 1


def testAnOutOfMemoryKillIsDistinguishedFromOurOwnKills():
    """Exit 137 says SIGKILL, never who sent it.

    Kills: omitting the container's OOMKilled state.

    This gateway kills on a breached bound; the kernel kills on memory
    pressure. Both surface as 137, and without the container's own
    verdict an opus failure stayed ambiguous for a whole session. Our
    own breach wins when both are true — a bound we chose to enforce is
    the better explanation than pressure we merely observed.
    """
    dictOom = providers.fdictExtractStructuredResult(
        [{"type": "assistant"}],
        {"bOomKilled": True, "iExitCode": 137, "iOutputBytes": 900})
    assert dictOom["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_OUT_OF_MEMORY)

    dictBoth = providers.fdictExtractStructuredResult(
        [{"type": "assistant"}],
        {"bOutputCapExceeded": True, "bOomKilled": True,
         "iExitCode": 137, "iOutputBytes": 1048576})
    assert dictBoth["sEmptyResultReason"] == (
        providers.S_EMPTY_BECAUSE_OUTPUT_CAP)


def testTheObservedStreamSizeIsRealRatherThanAConstantZero():
    """A field that always reports the same number is worse than none.

    Kills: reading iOutputBytes from a gateway that never returns it.

    The diagnosis read this key the day before anything produced it, so
    every record said zero bytes while looking authoritative — and the
    output-cap theory it existed to test could not be checked against
    it (external review, 2026-08-25).
    """
    dictSized = providers.fdictExtractStructuredResult(
        [{"type": "assistant"}],
        {"iOutputBytes": 524288, "iExitCode": 1})
    assert dictSized["iOutputBytes"] == 524288
