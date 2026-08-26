"""Frontend contract checks for the Agent Council module (section 15.4).

JavaScript is not executed by the repository test suite, so these
string/DOM-presence tests pin the contracts the browser lane and manual
walkthrough rely on: where the toolbar button lives, that the console is
read-only and never opens a terminal, that the chairbot defaults to the
first participant, that the composer states how a message is handled,
that plan acceptance controls exist, that sequence gaps and stale
baselines are surfaced, and that no state is transitioned optimistically.
This mirrors tests/testReposPanelFrontendContract.py.
"""

import os
import re

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsCouncilSource():
    return _fsReadStaticFile("scriptAgentCouncil.js")


def _fsExtractReturnBlock(sSource):
    iStart = sSource.rfind("return {")
    assert iStart != -1, "IIFE return block not found"
    iEnd = sSource.find("};", iStart)
    assert iEnd != -1, "IIFE return block not terminated"
    return sSource[iStart:iEnd]


def test_council_module_exists_and_is_an_iife():
    sSource = _fsCouncilSource()
    assert "var VaibifyAgentCouncil = (function ()" in sSource


def test_council_exports_lifecycle_api():
    sReturnBlock = _fsExtractReturnBlock(_fsCouncilSource())
    for sName in (
        "fnInitialize", "fnActivate", "fnTeardown",
        "fnHandleToolbarClick", "fnRefreshCapabilities",
    ):
        assert sName in sReturnBlock, sName + " missing from return block"


def test_index_html_has_toolbar_button_after_identity_before_run_menu():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="btnAgentCouncil"' in sSource
    iIdentity = sSource.find('id="workflowSwitcher"')
    iButton = sSource.find('id="btnAgentCouncil"')
    iRunMenu = sSource.find('id="toolbarMenuRun"')
    assert -1 not in (iIdentity, iButton, iRunMenu)
    assert iIdentity < iButton < iRunMenu, (
        "the Agent Council button must sit after the project identity "
        "and before the Run menu (section 6.1)"
    )


def test_index_html_has_workspace_and_modal_scaffolding():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="agentCouncilModal"' in sSource
    assert 'id="agentCouncilModalBody"' in sSource
    assert 'id="agentCouncilWorkspace"' in sSource
    assert 'id="agentCouncilWorkspaceBody"' in sSource


def test_index_html_loads_council_after_application():
    sSource = _fsReadStaticFile("index.html")
    iApp = sSource.find("/static/scriptApplication.js")
    iCouncil = sSource.find("/static/scriptAgentCouncil.js")
    assert -1 not in (iApp, iCouncil)
    assert iApp < iCouncil, "council must load after VaibifyApp is defined"


def test_application_wires_council_lifecycle():
    sSource = _fsReadStaticFile("scriptApplication.js")
    assert "VaibifyAgentCouncil.fnActivate" in sSource
    assert "VaibifyAgentCouncil.fnTeardown" in sSource


def test_toolbar_button_states_are_disabled_when_unavailable():
    sSource = _fsCouncilSource()
    # Disabled without a project and when fewer than two participants.
    assert "_fdictToolbarState" in sSource
    assert "two supported participants" in sSource
    assert "council-attention" in sSource
    assert "council-running" in sSource


def test_host_project_on_ramp_says_convert_not_promote():
    sSource = _fsCouncilSource()
    assert "Convert this project to a container to convene a council" \
        in sSource
    # The neighbouring promote action leaves it host mode and would
    # refuse again, so the word must never appear as the way forward.
    assert "promote" not in sSource.lower()
    assert "graduate" not in sSource.lower()


def test_host_on_ramp_keys_on_the_marker_not_prose():
    sSource = _fsCouncilSource()
    assert 'sUnavailableIn === "host-mode"' in sSource, (
        "the on-ramp must branch on the machine-readable marker, not by "
        "parsing the reason prose"
    )


def test_creation_chooser_offers_plan_and_open_existing():
    sSource = _fsCouncilSource()
    assert "Plan a change" in sSource
    assert "Open an existing campaign" in sSource
    assert "btnCouncilPlanChange" in sSource
    assert "btnCouncilOpenExisting" in sSource


def test_provider_availability_is_rendered_from_capabilities():
    sSource = _fsCouncilSource()
    assert "_fsProviderAvailability" in sSource
    assert "listProviders" in sSource
    assert "No reviewed" in sSource and "unavailable" in sSource


def test_models_come_from_capabilities_never_a_hardcoded_table():
    sSource = _fsCouncilSource()
    # The NESTED shape the backend actually sends. The picker used to
    # read dictProvider.listModels — a key no payload ever carried — so
    # it silently fell through to free text while the discovery result
    # rode over the wire unread.
    assert "_fdictProviderDiscovery" in sSource
    assert "dictModelDiscovery" in sSource
    assert "listModelIds" in sSource
    assert "_flistProviderModels" not in sSource, (
        "the old flat-key lookup is back; it reads a key the backend "
        "does not send")
    # Provenance is SHOWN, not merely carried: an un-verified alias set
    # presented without saying so reads as a discovered list.
    assert "_fsDiscoveryProvenance" in sSource
    assert "un-verified aliases" in sSource
    assert "bVerified" in sSource
    # No stale alias/model table lives in this source (sections 6.3.1,
    # 8.2). Guard against the known provider alias vocabulary leaking in.
    sLower = sSource.lower()
    for sForbidden in ("claude-sonnet", "claude-opus", "gpt-4", "gpt-5",
                       "\"sonnet\"", "\"opus\"", "\"haiku\""):
        assert sForbidden not in sLower, (
            "a hardcoded model id leaked into the picker: " + sForbidden
        )


def test_chairbot_selector_defaults_to_first_participant():
    sSource = _fsCouncilSource()
    assert "councilChairbot" in sSource
    assert "iChairbotIndex" in sSource
    assert "_dictState.iChairbotIndex = 0" in sSource, (
        "the chairbot default must be the first configured participant "
        "(section 6.3.1)"
    )


def test_council_settings_show_each_default():
    sSource = _fsCouncilSource()
    assert "bPeerAnonymity: true" in sSource
    assert 'sExecutionPermission: "fullSandbox"' in sSource
    assert "iMinimumRounds: 1" in sSource
    assert "councilPeerAnonymity" in sSource
    assert "councilMinimumRounds" in sSource


def test_disclosure_states_the_credential_risk_and_boundary():
    sSource = _fsCouncilSource()
    assert "Credential exposure" in sSource
    assert "revoke at the provider" in sSource
    assert "Execution boundary" in sSource
    assert "discarded" in sSource
    assert "receives your" in sSource  # provider content
    assert "billing" in sSource.lower()


def test_participant_console_is_read_only_and_disclaims_reasoning():
    sSource = _fsCouncilSource()
    assert "Read-only console" in sSource
    assert "not the model's private reasoning" in sSource


def test_council_never_constructs_a_terminal():
    sSource = _fsCouncilSource()
    assert "/ws/terminal" not in sSource
    assert "TerminalSession" not in sSource
    assert "VaibifyTerminal" not in sSource


def test_sequence_gap_and_eviction_are_surfaced():
    """Lost console output is still admitted — in the console.

    It used to be a banner above the tab bar, so a notice about missing
    console lines rendered on the Council, Plan and chat tabs, none of
    which show an event. Moving it into the log removes the noise
    WITHOUT removing the statement; deleting it outright would make the
    console skip events silently, which is the one thing the dashboard
    rules forbid.
    """
    sSource = _fsCouncilSource()
    assert "_fsRetentionBoundaryRow" in sSource
    assert "no longer retained" in sSource
    assert "iLowestRetainedSequence" in sSource
    # In the log, not in the workspace header.
    iRender = sSource.find("function _fnRenderWorkspace")
    iEnd = sSource.find("function _fsTabBar", iRender)
    assert "_fsRetentionBoundaryRow" not in sSource[iRender:iEnd], (
        "the retention notice is back above the tab bar, where it shows "
        "on tabs that display no events at all")
    iLog = sSource.find("function _fsEventLog")
    iLogEnd = sSource.find("function _fsOneEventRow", iLog)
    assert "_fsRetentionBoundaryRow" in sSource[iLog:iLogEnd]


def test_needs_human_blocking_question_card_renders_engine_questions():
    """The gate card reads the ENGINE'S shape (remediation R6)."""
    sSource = _fsCouncilSource()
    assert "_fsBlockingQuestionCard" in sSource
    assert "needs your decision" in sSource
    assert "listQuestions" in sSource
    assert "sQuestionText" in sSource
    assert "sRaisedByParticipantId" in sSource
    # The invented gate fields no engine ever wrote must be gone.
    assert "sDecisionRequired" not in sSource
    assert "sWhyEvidenceInsufficient" not in sSource


def test_exhausted_round_posts_the_three_engine_exit_routes():
    """Each exit control posts its own route (remediation R6), never a
    respond message the backend would have to parse back into intent."""
    sSource = _fsCouncilSource()
    assert "_fsExhaustedRoundCard" in sSource
    for sRouteSuffix in (
        "/grant-resolution-round",
        "/resolve-objections",
        "/reject-candidate",
    ):
        assert sRouteSuffix in sSource, (
            "missing exhausted-round exit route " + sRouteSuffix)
    assert '"[exit] "' not in sSource, (
        "the fake exit-as-respond message channel must stay dead")
    # Every unresolved objection gets a decision control, and the
    # resolve exit refuses to submit with one undecided.
    assert "listUnresolvedObjections" in sSource
    assert "council-objection-row" in sSource
    assert "Every objection needs a resolve or override" in sSource
    # The exhausted card must not carry a plain respond textarea that
    # would silently relaunch the spent budget (section 6.5): its only
    # textarea id (councilAnswer) belongs to the blocking-question card.
    iCard = sSource.find("function _fsExhaustedRoundCard")
    iEnd = sSource.find("    /* ---", iCard)
    assert iCard != -1 and iEnd != -1
    sCardBody = sSource[iCard:iEnd]
    assert "textarea" not in sCardBody, (
        "the exhausted-round card must offer three exits, not a respond "
        "field"
    )


def test_composer_matches_the_real_continuation_semantics():
    """The protocol has no mid-deliberation message channel (R6): the
    surface offers watching and stopping, never a Send box whose POST
    the backend rightly refuses."""
    sSource = _fsCouncilSource()
    assert "The council is deliberating" in sSource
    assert "stop after the current" in sSource
    assert "btnCouncilSend" not in sSource
    assert "Message the council" not in sSource
    assert "councilMessage" not in sSource


def test_plan_acceptance_controls_present():
    sSource = _fsCouncilSource()
    for sId in (
        "btnCouncilAcceptPlan",
        "btnCouncilCopyBrief", "btnCouncilDownloadPlan",
        "btnCouncilRejectPlan",
    ):
        assert sId in sSource, "missing plan action " + sId
    # "Request another pass" posted a transition the engine does not
    # offer at planReady; the control is gone (remediation R6).
    assert "btnCouncilAnotherPass" not in sSource


def test_candidate_plan_renders_the_engine_result_shape():
    """The plan tab reads dictCandidatePlan.dictResult (R6), never the
    fabricated top-level sPlanText no engine ever wrote."""
    sSource = _fsCouncilSource()
    assert "_fsCandidatePlanBody" in sSource
    assert "dictResult" in sSource
    assert "listPlanItems" in sSource
    assert "listResearcherOverriddenObjections" in sSource
    assert "sPlanText" not in sSource
    assert "councilPlanText" not in sSource


def test_accept_posts_no_body_and_events_read_the_engine_field():
    """Acceptance is the server-held candidate (R3): no caller text.
    The event log reads the engine's sEventKind (R6)."""
    sSource = _fsCouncilSource()
    iAccept = sSource.find("async function _fnAcceptPlan")
    iEnd = sSource.find("function _fnReportPlanSaved", iAccept)
    assert iAccept != -1 and iEnd != -1
    sAcceptBody = sSource[iAccept:iEnd]
    assert "fdictPostRaw" in sAcceptBody
    assert "sPlanText" not in sAcceptBody
    assert "dictEvent.sEventKind" in sSource
    assert "dictEvent.sKind" not in sSource


def test_convene_sends_the_settings_form():
    """The start payload carries dictSettings from the form (R6)."""
    sSource = _fsCouncilSource()
    assert "_fdictReadSettingsForm" in sSource
    assert "dictSettings: _fdictReadSettingsForm()" in sSource
    for sFormId in ("councilPeerAnonymity", "councilEffort",
                    "councilExecution", "councilMinimumRounds"):
        assert sFormId in sSource, "settings form lost " + sFormId


def test_stale_planning_baseline_warning():
    sSource = _fsCouncilSource()
    assert "_fsBaselineWarning" in sSource
    assert "bPlanningBaselineStale" in sSource
    assert "earlier" in sSource.lower() and "baseline" in sSource.lower()


def test_no_optimistic_state_transition():
    """Every human action refetches the backend record before render.

    The frontend renders the registry's truth; it never sets a campaign
    state locally. Guard against a reassignment of the campaign state on
    the client and confirm each action reloads.
    """
    sSource = _fsCouncilSource()
    assert not re.search(r"dictCampaign\.sState\s*=(?!=)", sSource), (
        "the frontend must not set campaign state locally — it renders "
        "backend truth (section 6.4)"
    )
    assert "_fnReloadActiveCampaign" in sSource
    # The action helper refetches the record after every POST rather
    # than mutating a local copy.
    iAction = sSource.find("async function _fnPostAction")
    iEnd = sSource.find("function _fnCopyBrief", iAction)
    sBody = sSource[iAction:iEnd]
    assert "_fnReloadActiveCampaign" in sBody, (
        "an action that does not refetch would show an optimistic state"
    )


def test_all_council_urls_are_same_origin_relative():
    """No absolute address, loopback port, or window.open (section 21)."""
    sSource = _fsCouncilSource()
    assert "window.open(" not in sSource
    assert "http://127.0.0.1" not in sSource
    assert "https://" not in sSource
    assert "/api/agent-councils/" in sSource


# ── ask the chairbot ─────────────────────────────────────────────

def test_chat_tab_exists_and_states_its_cost_before_the_button():
    """A researcher who has not been told the cost has not agreed to it.

    Opening a conversation builds a real container and every message
    spends the project's provider subscription, so the disclosure has to
    be beside the Open button, not discovered after the click.
    """
    sSource = _fsCouncilSource()
    assert 'data-tab=\\"chat\\"' in sSource or 'data-tab="chat"' in sSource
    iClosed = sSource.find("function _fsChatClosedTab")
    iOpenButton = sSource.find("btnCouncilChatOpen", iClosed)
    assert iClosed != -1 and iOpenButton != -1
    sBefore = sSource[iClosed:iOpenButton]
    assert "spends this project's provider" in sBefore
    assert "disposable runner" in sBefore


def test_chat_says_what_the_chairbot_cannot_do():
    """The panel must not let a conversation read as an action.

    A chairbot cannot accept a plan, clear an objection or start a
    round; a UI that did not say so invites a researcher to ask it to.
    """
    sSource = _fsCouncilSource()
    iClosed = sSource.find("function _fsChatClosedTab")
    iEnd = sSource.find("function _fsChatStatusLine", iClosed)
    sTab = sSource[iClosed:iEnd]
    assert "cannot accept a plan" in sTab
    assert "closes itself" in sTab


def test_chat_transcript_is_backend_truth_never_composed_locally():
    """An answer on screen must be one the server recorded.

    Appending the researcher's own message locally would make a refused
    message indistinguishable from an accepted one.
    """
    sSource = _fsCouncilSource()
    iAction = sSource.find("async function _fnPostChatAction")
    iEnd = sSource.find("async function _fnLoadChatQuietly", iAction)
    sBody = sSource[iAction:iEnd]
    assert "_fnLoadChatQuietly" in sBody, (
        "a chat action that does not refetch would show an optimistic "
        "transcript")
    assert "listMessages.push" not in sSource
    assert "_dictState.dictChat = await VaibifyApi.fdictGet(" in sSource


def test_chat_poll_failures_are_shown_not_swallowed():
    """A conversation whose poll broke must not look current."""
    sSource = _fsCouncilSource()
    assert "sLastChatError" in sSource
    assert "_fsChatStaleNotice" in sSource
    assert "may be out of date" in sSource


def test_the_idle_countdown_is_absent_from_the_render_signature():
    """A per-tick value in the signature would wipe a half-typed question.

    The panel re-renders only when its signature changes, so anything
    that changes on every poll must stay out of it.
    """
    sSource = _fsCouncilSource()
    iSignature = sSource.find("function _fsChatSignature")
    iEnd = sSource.find("function _fiPollInterval", iSignature)
    sBody = sSource[iSignature:iEnd]
    assert "iIdleSecondsRemaining" not in sBody
    assert "listMessages" in sBody, (
        "a landed answer must change the signature or it never appears")
