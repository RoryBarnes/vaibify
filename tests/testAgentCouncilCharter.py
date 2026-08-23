"""Falsification tests for the council instruction contract and schema.

Phase 1 of the Agent Council (design/agentCouncil.md sections 5.5-5.6,
8.5, and the section-15.1 checklist). These tests target the charter's
*construction and placement* — that the server-owned text is built,
recorded immutably in the campaign, kept structurally separate from
quoted peer/researcher material, and delivered on the instruction
channel — never the behavioral, unprovable claim that a model cannot be
made to obey an injection. They also pin the structured turn schema,
peer-anonymity blinding, independent-proposal isolation, and the
untrusted labeling of quoted material.

Each test is written to FAIL if the property it defends is broken: a
charter that stopped recording its version, a peer author that leaked
into a blinded prompt, or a proposal that began seeing peer output would
each flip an assertion here.
"""

from vaibify.gui import agentCouncilCharter
from vaibify.gui.agentCouncilCampaign import (
    fdictCreateCampaign,
    fdictCreateParticipant,
)
from vaibify.gui.agentCouncilCharter import (
    DICT_PHASE_INSTRUCTIONS,
    S_CHARTER_TEXT,
    S_CHARTER_VERSION,
    S_PHASE_CROSS_REVIEW,
    S_PHASE_PROPOSAL,
    S_QUOTED_MATERIAL_LABEL,
    fdictBuildQuotedEntry,
    fdictComposeTurnRequest,
    fdictValidateTurnResult,
    flistBlindQuotedMaterial,
    fsComposeTurnInstruction,
)

from tests.agentCouncilHarness import (
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)

LIST_TWO_SPECS = [
    {"sHandle": "A", "sProvider": "prov-a", "sRequestedModel": "model-a"},
    {"sHandle": "B", "sProvider": "prov-b", "sRequestedModel": "model-b"},
]
LIST_THREE_SPECS = LIST_TWO_SPECS + [
    {"sHandle": "C", "sProvider": "prov-c", "sRequestedModel": "model-c"}]


def _fdictMakeCampaign(dictSettings=None):
    listParticipants = [
        fdictCreateParticipant("prov-a", "model-a"),
        fdictCreateParticipant("prov-b", "model-b"),
    ]
    return fdictCreateCampaign("the question", listParticipants,
                              dictSettings=dictSettings)


# ----- charter construction and placement -----------------------------

def testCharterTextCarriesTheVersionAndEveryChartedClause():
    """The server-owned charter states each section-5.5 clause."""
    assert S_CHARTER_VERSION in S_CHARTER_TEXT
    for sExpectedFragment in ("not the sole author", "Consensus is not proof",
                              "Evidence discipline", "falsify",
                              "Independence before convergence",
                              "blocking question", "turn schema"):
        assert sExpectedFragment in S_CHARTER_TEXT, sExpectedFragment


def testCampaignRecordsTheEffectiveCharterVersionAndTextImmutably():
    """Every campaign carries the charter it was bound by (section 5.5)."""
    dictCampaign = _fdictMakeCampaign()
    assert dictCampaign["sCharterVersion"] == S_CHARTER_VERSION
    assert dictCampaign["sCharterText"] == S_CHARTER_TEXT
    assert S_CHARTER_VERSION in dictCampaign["sCharterText"]


def testCharterRidesTheInstructionChannelNotTheQuotedMaterial():
    """Construction/placement: the charter is in the instruction channel
    and the researcher/peer text is quoted, labeled untrusted, never in
    the instruction channel (section 5.5)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    dictRequest = fixture.flistRequestsFor("A", S_PHASE_CROSS_REVIEW)[0]
    assert S_CHARTER_VERSION in dictRequest["sInstructionChannel"]
    assert "COUNCIL CHARTER" in dictRequest["sInstructionChannel"]
    for dictQuoted in dictRequest["listQuotedMaterial"]:
        assert "COUNCIL CHARTER" not in dictQuoted["sContent"]


def testRoleOverlayComposesIntoInstructionWithoutRelaxingCharter():
    """A role narrows scrutiny; the charter still rides the same channel
    (section 5.6)."""
    dictCampaign = _fdictMakeCampaign()
    dictParticipant = dict(dictCampaign["listParticipants"][0])
    dictParticipant["sRole"] = "security-audit perspective"
    sInstruction = fsComposeTurnInstruction(
        dictCampaign, dictParticipant, S_PHASE_CROSS_REVIEW)
    assert "security-audit perspective" in sInstruction
    assert "never relaxes the charter" in sInstruction
    assert "COUNCIL CHARTER" in sInstruction
    assert DICT_PHASE_INSTRUCTIONS[S_PHASE_CROSS_REVIEW] in sInstruction


# ----- structured turn schema -----------------------------------------

def testValidTurnResultPassesTheSchema():
    assert fdictValidateTurnResult(fdictMakeTurnResult())["bValid"] is True


def testSchemaRejectsMissingAndMistypedFields():
    """A result missing its verdict, or with a non-array evidence field,
    is invalid (section 8.5)."""
    dictMissingVerdict = fdictMakeTurnResult()
    del dictMissingVerdict["sVerdict"]
    dictOutcome = fdictValidateTurnResult(dictMissingVerdict)
    assert dictOutcome["bValid"] is False
    assert any("sVerdict" in sProblem for sProblem in
               dictOutcome["listProblems"])
    dictBadEvidence = fdictMakeTurnResult()
    dictBadEvidence["listEvidence"] = "not-a-list"
    assert fdictValidateTurnResult(dictBadEvidence)["bValid"] is False
    assert fdictValidateTurnResult("not-a-mapping")["bValid"] is False


# ----- quoted material: labeling, independence, anonymity -------------

def testEveryQuotedEntryIsLabeledUntrusted():
    """Cross-review input is presented as untrusted material (section
    5.5)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    dictRequest = fixture.flistRequestsFor("A", S_PHASE_CROSS_REVIEW)[0]
    assert dictRequest["listQuotedMaterial"]
    for dictQuoted in dictRequest["listQuotedMaterial"]:
        assert dictQuoted["sLabel"] == S_QUOTED_MATERIAL_LABEL


def testIndependentProposalsSeeNoPeerOutput():
    """A proposal turn is handed only the researcher's material, never a
    peer's (section 5.1 barrier)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    dictRequest = fixture.flistRequestsFor("A", S_PHASE_PROPOSAL)[0]
    listSourceKinds = {dictQuoted["sSourceKind"]
                       for dictQuoted in dictRequest["listQuotedMaterial"]}
    assert "peerProposal" not in listSourceKinds
    assert "peerCritique" not in listSourceKinds
    assert listSourceKinds <= {"researcherQuestion", "researcherResponse"}


def testPeerAnonymityBlindsThePromptButTheRecordRetainsIdentities():
    """With anonymity on (the default), cross-review authors are aliased
    in the prompt while the campaign record keeps the real ids (section
    6.3.2)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    setRealIds = set(fixture.dictHandleToId.values())
    dictRequest = fixture.flistRequestsFor("A", S_PHASE_CROSS_REVIEW)[0]
    listPeerAuthors = [dictQuoted["sAuthorIdentity"]
                       for dictQuoted in dictRequest["listQuotedMaterial"]
                       if dictQuoted["sSourceKind"] == "peerProposal"]
    assert listPeerAuthors
    for sAuthor in listPeerAuthors:
        assert sAuthor.startswith("anonymousPeer-")
        assert sAuthor not in setRealIds
    listProposalRecords = (
        dictOut["listRounds"][0]["dictTurnsByPhase"][S_PHASE_PROPOSAL])
    for dictRecord in listProposalRecords:
        assert dictRecord["sParticipantId"] in setRealIds


def testAnonymityOffPresentsRealPeerIdentitiesInThePrompt():
    """Turning anonymity off is a real toggle: peer authorship is shown."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS, ffnDecideAllAccept,
        dictSettings={"bPeerAnonymity": False}, sChairbotHandle="A")
    fixture.fdictDrive()
    setRealIds = set(fixture.dictHandleToId.values())
    dictRequest = fixture.flistRequestsFor("A", S_PHASE_CROSS_REVIEW)[0]
    listPeerAuthors = [dictQuoted["sAuthorIdentity"]
                       for dictQuoted in dictRequest["listQuotedMaterial"]
                       if dictQuoted["sSourceKind"] == "peerProposal"]
    assert listPeerAuthors
    assert all(sAuthor in setRealIds for sAuthor in listPeerAuthors)


def testBlindingAliasesAreStablePerAuthor():
    """One author keeps one alias so an argument keeps its thread."""
    listQuoted = [
        fdictBuildQuotedEntry("peerProposal", "author-x", "first"),
        fdictBuildQuotedEntry("peerCritique", "author-x", "second"),
        fdictBuildQuotedEntry("peerProposal", "author-y", "third"),
        fdictBuildQuotedEntry("researcherQuestion", "researcher", "q"),
    ]
    listBlinded = flistBlindQuotedMaterial(listQuoted)
    assert listBlinded[0]["sAuthorIdentity"] == listBlinded[1][
        "sAuthorIdentity"]
    assert listBlinded[0]["sAuthorIdentity"] != listBlinded[2][
        "sAuthorIdentity"]
    assert listBlinded[3]["sAuthorIdentity"] == "researcher"


def testComposeTurnRequestSeparatesInstructionFromQuotedChannels():
    """The composed request keeps the trusted and untrusted channels
    apart, and quotes are deep-copied (section 5.5)."""
    dictCampaign = _fdictMakeCampaign()
    dictParticipant = dictCampaign["listParticipants"][0]
    listQuoted = [fdictBuildQuotedEntry("peerProposal", "peer", "content")]
    dictRequest = fdictComposeTurnRequest(
        dictCampaign, dictParticipant, S_PHASE_CROSS_REVIEW, 1, listQuoted)
    assert "COUNCIL CHARTER" in dictRequest["sInstructionChannel"]
    assert dictRequest["listQuotedMaterial"] is not listQuoted
    assert dictRequest["listQuotedMaterial"][0]["sContent"] == "content"
    assert dictRequest["sParticipantId"] == dictParticipant["sParticipantId"]


def testAPartialSnapshotIsDeclaredToEveryParticipant():
    """A participant must be told what it was not shown, by name.

    Kills: composing a turn instruction without the scope note.

    The excluded file exists in the researcher's repository; only the
    council's copy lacks it. A participant that is not told will read
    the absence as evidence — concluding the repository is broken, or
    asserting what the missing file must contain — and the charter's
    own evidence discipline gives it no way to notice, because nothing
    in the snapshot contradicts either conclusion.
    """
    sNote = agentCouncilCharter.fsDescribeSnapshotScope(["data/huge.bin"])
    assert "data/huge.bin" in sNote
    dictCampaign = {
        "sCharterText": agentCouncilCharter.S_CHARTER_TEXT,
        "dictProjectIdentity": {"sSnapshotScopeNote": sNote},
    }
    sInstruction = agentCouncilCharter.fsComposeTurnInstruction(
        dictCampaign, {"sRole": ""}, agentCouncilCharter.S_PHASE_PROPOSAL)
    assert "data/huge.bin" in sInstruction
    assert "PARTIAL" in sInstruction
    assert "never as absent" in sInstruction


def testAWholeSnapshotAddsNothingToTheTurnInstruction():
    """The ordinary case must not grow a paragraph about nothing.

    The other half of the pair: a scope note that always fired would
    make the assertion above pass while telling every council its
    snapshot was partial, which is a false statement in the common
    case and trains participants to ignore the notice in the rare one.
    """
    assert agentCouncilCharter.fsDescribeSnapshotScope([]) == ""
    dictCampaign = {
        "sCharterText": agentCouncilCharter.S_CHARTER_TEXT,
        "dictProjectIdentity": {"sSnapshotScopeNote": ""},
    }
    sInstruction = agentCouncilCharter.fsComposeTurnInstruction(
        dictCampaign, {"sRole": ""}, agentCouncilCharter.S_PHASE_PROPOSAL)
    assert "PARTIAL" not in sInstruction
