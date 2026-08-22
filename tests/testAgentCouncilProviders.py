"""Unit falsification of the Claude runner-backend adapter (design 8.x/9.7/13.2).

No Docker here: the argv/stdin boundary, the stream-json parsing, the
structured-result and model-identity extraction, the failure
classification, the capability contract, live-discovery fallback, the
extraction-only credential lane and the ownership-stamped credential
tarball are all exercised against crafted inputs and fakes. The
§15.2 boundary — researcher/plan/prior-agent text absent from argv — is
``testResearcherAndPeerTextNeverReachArgv``.
"""

import json
import os
import stat
import tarfile

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
    assert providers.fdictExtractStructuredResult([]) == {"sRawResultText": ""}


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
        "expiresAt": 123, "scopes": ["user:inference"]}}
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
