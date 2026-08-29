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

import pytest

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
    assert "Continue a council" in sSource
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
    surface offers watching, pausing and stopping, never a Send box
    whose POST the backend rightly refuses.

    Asserted against the composer's BODY, not the file: the phrase this
    used to look for survived only in a comment after the wording
    changed, which is a green assertion over prose nobody renders.
    """
    sSource = _fsCouncilSource()
    sComposer = _fsFunctionBody(sSource, "_fsComposer")
    assert "The council is deliberating" in sComposer
    assert "btnCouncilPause" in sComposer
    assert "btnCouncilStop" in sComposer
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


@pytest.mark.falsification
def test_copy_and_download_serve_the_servers_plan_bytes():
    """One composer: the backend's plan.md is the only plan text.

    A display-side brief composer lived here and diverged from the
    server's artifact by construction — two renderers over one record.
    Copy and Download now fetch the server's bytes, which also carry
    the DRAFT watermark and staleness statement only the server can
    compose.

    Kills: reintroducing a client-side plan composer, and the fetch
    helper losing the plan.md route.
    """
    sSource = _fsCouncilSource()
    assert "_fsComposePlanBriefText" not in sSource, (
        "a second plan composer diverges from the accepted artifact")
    sFetch = _fsFunctionBody(sSource, "_fsFetchPlanMarkdown")
    assert "/plan.md" in sFetch
    assert "fsGetText" in sFetch
    for sHelper in ("_fnCopyBrief", "_fnDownloadPlan"):
        assert "_fsFetchPlanMarkdown" in _fsFunctionBody(
            sSource, sHelper), sHelper


@pytest.mark.falsification
def test_a_dead_deliberation_offers_resume_from_backend_truth():
    """The composer must not claim "deliberating" over a dead hub.

    A crashed planning campaign used to render "The council is
    deliberating" forever. The panel now branches on the backend's own
    liveness statement and renders the resume surface from the durable
    stopping point — never a guess derived from staleness.

    Kills: the composer ignoring bDeliberationLive and rendering the
    deliberating text for a campaign nothing is driving.
    """
    sSource = _fsCouncilSource()
    sComposer = _fsFunctionBody(sSource, "_fsComposer")
    assert "bDeliberationLive" in sComposer
    assert "_fsResumeSurface" in sComposer
    sSurface = _fsFunctionBody(sSource, "_fsResumeSurface")
    assert "dictStoppingPoint" in sSurface
    # Keyed on the record-derived ACTION, not the conflating flag: the
    # button renders only where the route would admit a resume.
    assert 'sAction !== "resume"' in sSurface
    # The stop-clear choice is surfaced ON the control, and the action
    # posts the explicit flag.
    assert "clears the requested stop" in sSurface
    sAction = _fsFunctionBody(sSource, "_fnResumeCouncil")
    assert "/resume" in sAction
    assert "bClearStopRequest" in sAction
    # The retry lane renders from the same record-derived action and
    # posts the retry route.
    sRetry = _fsFunctionBody(sSource, "_fsRetrySurface")
    assert 'sAction !== "retry"' in sRetry
    assert "/retry" in _fsFunctionBody(sSource, "_fnRetryCouncil")


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
    iAction = sSource.find("async function _fbPostAction")
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
    # The lifecycle sentence follows the resting semantics (2026-08-27):
    # an idle conversation's RUNNER rests and the next question wakes
    # it — the researcher must be told the conversation itself stays.
    assert "rest" in sTab
    assert "wakes it" in sTab
    assert "conversation itself stays" in sTab


def test_chat_transcript_is_backend_truth_never_composed_locally():
    """An answer on screen must be one the server recorded.

    Appending the researcher's own message locally would make a refused
    message indistinguishable from an accepted one.
    """
    sSource = _fsCouncilSource()
    iAction = sSource.find("async function _fbPostChatAction")
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


# ── the gate reads as prose, not as a record dump ────────────────

def _fsFunctionBody(sSource, sName):
    """Return one module-level function's body, bounded at the next one.

    Searching for "the next `async function`" is not a bound: when the
    named helper happens to be the last of its kind, `find` returns -1
    and the slice runs to the end of the file — so an assertion about
    THIS function silently becomes an assertion about the whole module,
    and passes for a helper that no longer contains the thing at all.
    Caught by mutation testing, 2026-08-25.
    """
    iStart = sSource.find("function " + sName)
    assert iStart != -1, sName
    listEnds = [iEnd for iEnd in
                (sSource.find("\n    function ", iStart + 10),
                 sSource.find("\n    async function ", iStart + 10),
                 sSource.find("\n    var ", iStart + 10))
                if iEnd != -1]
    return sSource[iStart:min(listEnds)] if listEnds else sSource[iStart:]


@pytest.mark.falsification
def test_server_identifiers_are_hidden_from_model_written_text():
    """A model repeats the ids it was handed; a reader must not see them.

    The chairbot is given question ids so it can say which decision a
    plan item waits on, and it puts them into prose the researcher then
    reads. A participant id is REPLACED with the agent label rather than
    deleted, because "as participant-x noted" would otherwise lose its
    subject.

    Kills: internal identifiers leaking into the decision list.
    """
    sSource = _fsCouncilSource()
    assert "_fsHideInternalIdentifiers" in sSource
    assert "_RE_QUESTION_IDENTIFIER" in sSource
    assert "_RE_PARTICIPANT_IDENTIFIER" in sSource
    # Applied to every place model-written question text is rendered.
    for sRenderer in ("_fsDecisionBlock", "_fsBlockingQuestionCard"):
        assert "_fsHideInternalIdentifiers" in _fsFunctionBody(
            sSource, sRenderer), sRenderer


@pytest.mark.falsification
def test_decision_context_is_reachable_in_full():
    """A researcher must not be asked to decide against a cut sentence.

    The context was truncated at 240 characters with an ellipsis and no
    way to read the rest. The whole text is now in the DOM behind a
    native disclosure, so it costs a click and no round trip.

    Kills: the context collapsing to a dead 240-character ellipsis.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsDecisionContext")
    assert "<details" in sBody and "<summary>" in sBody
    assert "show all" in sBody
    # The FULL text, not the summary, is what the disclosure holds.
    assert "_fsEscape(sText) + \"</p></details>\"" in sBody
    assert "I_CONTEXT_SUMMARY_CHARACTERS" in sBody


@pytest.mark.falsification
def test_every_council_action_sends_the_chosen_directory():
    """An action that omits it is refused on a multi-directory project.

    The reads carried it and the actions did not, so the panel polled
    happily while every button failed.

    Kills: actions posting without the directory query.
    """
    sSource = _fsCouncilSource()
    for sHelper in ("_fbPostAction", "_fbPostChatAction"):
        assert "_fsDirectoryQuery" in _fsFunctionBody(
            sSource, sHelper), sHelper


def test_a_refusal_never_renders_as_zero_campaigns():
    """"(0)" and "never convened" must not look identical.

    A Blank Project has no workflow to open, so on a project tracking
    several directories the server cannot resolve which repository a
    bare listing means and rightly refuses. That refusal was swallowed
    into an empty list, so the chooser read "Open an existing campaign
    (0)" for a project holding a live council waiting at its gate.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fnRefreshSummaries")
    # The CATCH must record it. Matching the name alone also matched
    # the reset at the top of the function, so a mutation that put the
    # swallow straight back passed (2026-08-25).
    assert "sLastListError = error.message" in sBody, (
        "the listing refusal is discarded, so the count cannot be "
        "distinguished from an empty project")
    assert "listSummaries = []" not in sBody, (
        "the bare-call failure path empties the list instead of "
        "falling back to the candidate directories")
    assert "_fsListRefusalNotice" in sSource
    assert "not a count of what" in sSource


def test_the_listing_falls_back_to_the_candidate_directories():
    """A Blank Project must still be able to list its own councils.

    The bare call is tried first so the ordinary project still costs one
    request; only a refusal fans out across the directories the
    capabilities poll already named, and each summary remembers which
    one answered for it — a summary carries no repository of its own,
    and on a fresh load no campaign is open to supply it.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fnListAcrossCandidateDirectories")
    assert "listCandidateDirectories" in sBody
    assert "sProjectDirectory=" in sBody
    # The directory for the first per-campaign fetch comes from the
    # SUMMARY the server sent, not from bookkeeping the panel keeps.
    sQuery = _fsFunctionBody(sSource, "_fsDirectoryQuery")
    assert "_fsDirectoryForListedCampaign" in sQuery, (
        "picking a campaign out of the list would fetch it with no "
        "directory and be refused all over again")
    sLookup = _fsFunctionBody(sSource, "_fsDirectoryForListedCampaign")
    assert "sProjectRepoPath" in sLookup


def test_the_panel_keeps_no_directory_bookkeeping_of_its_own():
    """The record is the authority on which repository a campaign is in.

    The panel used to keep its own campaign-id-to-directory map because
    the listing summary carried no repository. It carries one now, and
    a second copy of that fact is a second thing that can be wrong.
    """
    sSource = _fsCouncilSource()
    assert "_dictDirectoryByCampaignId" not in sSource


def test_opening_a_listed_campaign_carries_its_directory():
    """The first fetch is keyed on the id being FETCHED, not the active one.

    _fnAdoptCampaign sets the active id AFTER the fetch returns, so a
    lookup against it is always a tick too late and the request goes out
    bare — which is why picking a campaign out of the list still failed
    with the directory refusal after the listing had already resolved it
    (2026-08-25).
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fnLoadCampaign")
    assert "_fsDirectoryQuery(\"?\", sCampaignId)" in sBody, (
        "the campaign fetch sends no directory, or looks it up under "
        "the wrong id")


def test_the_chooser_distinguishes_identical_questions():
    """One prompt iterated on gives a list where every row reads alike.

    The directory each campaign belongs to and its state are what tell
    them apart, so the rows are grouped under the directory that
    answered — and the ordering claim is limited to what a summary can
    actually support.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsSummariesList")
    # Ordered by the record's own clock, not by a position in a list.
    assert "fLastActivityEpoch" in sBody
    assert "sort(" in sBody
    # Split by whether the council can actually be continued, so a dead
    # campaign is not offered beside a live gate.
    assert "_fbSummaryIsResumable" in sBody
    sRow = _fsFunctionBody(sSource, "_fsOneSummaryRow")
    assert "sCampaignName" in sRow, (
        "rows are identified by the question alone, which is identical "
        "for a researcher iterating on one prompt")
    assert "_fsDescribeStoppingPoint" in sRow


def test_researcher_decisions_render_prose_never_raw_json():
    """Every recorded decision lands on a sentence, never serialized JSON.

    Three phaseRetried records rendered as raw JSON under "Your
    decisions" in a live gate (2026-08-28). The renderer prefers the
    record's own prose fields and falls back to a kind translation —
    a JSON.stringify fallback is the defect, not a safety net.

    The prose fallback now lives in ``_fsNonResponseDecisions``: the
    researcherResponse records that used to share it render as
    structured exchanges instead (2026-08-29), so the kinds that still
    have no Q&A shape are the ones this guarantee is about.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsNonResponseDecisions")
    assert "sText" in sBody
    assert "sDecisionKind" in sBody
    assert "JSON.stringify" not in sBody
    # And the entry point must still reach it, or the guarantee is
    # preserved in a function nothing calls.
    sEntry = _fsFunctionBody(sSource, "_fsResearcherDecisions")
    assert "_fsNonResponseDecisions" in sEntry


def test_answered_questions_render_as_structured_exchanges():
    """A long council's Q&A history must be scannable, not one blob.

    ``sText`` on a researcherResponse is a pre-rendered "ASKED: …
    ANSWERED: …" string, and every exchange printed as a single list
    item — read live as "a giant mass of unformatted text"
    (2026-08-29). The structure was already on the record; this asserts
    the renderer uses it rather than the blob.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsAnsweredExchanges")
    assert "listResearcherResponses" in sBody
    assert "listAnsweredQuestions" in sBody
    assert "<details" in sBody, (
        "each exchange collapses; the summary is the affordance")
    assert "sText" not in sBody, (
        "the pre-rendered blob is the thing being replaced")
    # An answer may cover several questions, so the mapping is by
    # membership. Pairing by position would misattribute answers.
    sMap = _fsFunctionBody(sSource, "_fsAnswerForQuestion")
    assert "listQuestionIds" in sMap
    assert "indexOf" in sMap


def test_the_models_own_decision_numbering_is_stripped_for_display():
    """Vaibify numbers the decisions; the chairbot must not also.

    A live gate rendered the heading "Decision 4" over a body opening
    "1. DECISION 2" — three numbering authorities on one item
    (2026-08-29). Only a LEADING self-label goes: a mid-sentence "as
    DECISION 2 established" is a reference the reader needs.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsHideInternalIdentifiers")
    assert "DECISION" in sBody
    assert "^" in sBody, "the strip must be anchored at the start"


def test_excised_identifier_debris_is_swept_with_its_connectives():
    """Stripping question ids must also sweep their orphaned wrapper.

    The charter tells the chairbot to write "(waits on question-x and
    question-y)"; excising the ids alone left "(waits on and )"
    rendered verbatim in a live gate (2026-08-28).
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsHideInternalIdentifiers")
    assert "waits on" in sBody, (
        "the connective sweep is gone; excised ids leave their "
        "empty '(waits on and )' wrapper on screen")


def test_the_implementation_button_sends_a_source_id_never_plan_text():
    """The client names the source council; the server loads the plan.

    Posting plan TEXT would let a caller hand implementers a plan no
    council accepted — the seed is loaded server-side from the source
    campaign's sealed artifact, so the body carries only the kind and
    the id.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fnConveneCouncil")
    assert "sCampaignKind" in sBody
    assert "sSourceCampaignId" in sBody
    assert "sSeedPlanDocument" not in sBody, (
        "the convene body must never carry plan text; the server loads "
        "the sealed artifact from the source campaign")


def test_implementing_a_plan_opens_the_consent_form_not_a_launch():
    """The button opens the convene form; it never starts a council.

    An implementation council spends the same paid provider work as
    any other, so it passes the same disclosure, participant and
    settings screen.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fnOpenImplementationForm")
    assert "_fnOpenPlanningForm" in sBody
    assert "/start" not in sBody, (
        "the button must not convene directly — the consent form is "
        "the gate every council passes")


def test_an_implementation_council_offers_no_further_implementation():
    """A patch implements nothing further — no council of councils."""
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fsAcceptedPlanActions")
    assert 'sCampaignKind === "implementation"' in sBody
    assert "git apply" in sBody, (
        "an accepted patch must say the researcher applies it; vaibify "
        "never writes it into the project")


def test_a_displayed_failure_drops_its_leading_machine_class():
    """"failed: emptyTurn: ..." is internal vocabulary on a screen.

    A researcher read exactly that (2026-08-28). The class stays in the
    durable record — the retry whitelist reads it — and is stripped
    only where the reason is rendered.
    """
    sSource = _fsCouncilSource()
    assert "_fsReadableFailureReason" in sSource
    sBody = _fsFunctionBody(sSource, "_fsReadableFailureReason")
    assert "_RE_LEADING_MACHINE_CLASS" in sBody
    # Both failure surfaces go through it: the per-turn chip's tooltip
    # and the participant's failure line.
    sParticipant = _fsFunctionBody(sSource, "_fsParticipantStatusChip")
    assert "_fsReadableFailureReason" in sParticipant


def test_convene_names_the_agent_whose_model_is_unchosen():
    """The model lists stay unchosen; the REFUSAL carries the meaning.

    Researcher ruling (2026-08-28): no model is pre-selected, because
    a default would tell every researcher which model vaibify thinks
    is best. The cost is that the form can be submitted incomplete, so
    the refusal must name the agent rather than let the server answer
    with a field-path 422 the dashboard rendered as a bare number.
    """
    sSource = _fsCouncilSource()
    sBody = _fsFunctionBody(sSource, "_fnConveneCouncil")
    assert "_fsDescribeParticipantsMissingAModel" in sBody, (
        "convene must refuse an incomplete participant before posting")
    sRefusal = _fsFunctionBody(
        sSource, "_fsDescribeParticipantsMissingAModel")
    assert "Agent " in sRefusal, (
        "name the participant in the vocabulary the workspace uses")
    assert "sRequestedModel" in sRefusal
    # And no default may creep into the seeding: an unchosen list is
    # the ruling, not an oversight.
    sSeed = _fsFunctionBody(sSource, "_fnSeedDraftParticipants")
    assert 'sRequestedModel: ""' in sSeed


def test_notes_render_beside_the_gate_not_inside_it():
    """Charter 1.7.0's notes channel has a rendering, and a distinct one.

    The gate is a numbered question list with answer boxes; a note is a
    finding that needs no answer. Rendered inside the gate it would read
    as one more thing the researcher must decide, which is the whole
    defect the field exists to remove — four items opening "Emphasis,
    not a decision: ..." arrived as questions in a live gate because
    there was nowhere else to put them.
    """
    sSource = _fsCouncilSource()
    assert "_fsNotedFindingsPanel" in sSource
    assert "listGateNotes" in sSource
    assert "sNoteText" in sSource
    # A SIBLING of the gate card, appended after it — the panel is
    # concatenated onto the card rather than composed into it.
    sNeedsHuman = _fsFunctionBody(sSource, "_fsNeedsHumanCard")
    assert "_fsNotedFindingsPanel(dictCampaign)" in sNeedsHuman
    sPanel = _fsFunctionBody(sSource, "_fsNotedFindingsPanel")
    # Nothing to answer with, and told so in as many words.
    assert "textarea" not in sPanel
    assert "council-decision-answer" not in sPanel
    assert "needs an answer" in sPanel
    # Distinguishable at a glance: its own container class and an
    # unnumbered list, where the gate's questions are <ol> numbered.
    assert "council-notes" in sPanel
    assert "<ul class=\\\"council-notes-list\\\">" in sPanel
    sCss = _fsReadStaticFile("styleMain.css")
    assert ".council-notes {" in sCss
    assert ".council-notes-list {" in sCss


def test_the_exhausted_gate_shows_a_summary_never_called_a_plan():
    """A council that never converged produced no plan, and must not
    appear to have produced one (researcher direction 2026-08-29)."""
    sSource = _fsCouncilSource()
    assert "_fsDeliberationSummarySection" in sSource
    sSection = _fsFunctionBody(sSource, "_fsDeliberationSummarySection")
    assert "dictDeliberationSummary" in sSection
    assert "Deliberation summary" in sSection
    assert "not a plan" in sSection
    for sSummaryKey in ("listPositionsProposed", "listPointsOfDisagreement",
                        "listEvidenceBehindEachPosition"):
        assert sSummaryKey in sSection, sSummaryKey
    # The heading a summary is rendered under must never be "Plan": the
    # candidate plan's own renderer owns that word.
    assert "<h5>Plan</h5>" not in sSection


def test_the_exhausted_gate_offers_two_ways_forward_and_an_abandon():
    """Exits are two; rejecting is abandoning, not a third way forward."""
    sSource = _fsCouncilSource()
    sCard = _fsFunctionBody(sSource, "_fsExhaustedRoundCard")
    iExits = sCard.find("council-exits")
    iAbandon = sCard.find("council-abandon")
    assert 0 < iExits < iAbandon, sCard
    sForward = sCard[iExits:iAbandon]
    assert "btnCouncilGrantRound" in sForward
    assert "btnCouncilResolveOverride" in sForward
    assert "Implement as-is" in sForward
    assert "btnCouncilReject" not in sForward, (
        "abandoning must not sit among the ways forward")
    assert "btnCouncilReject" in sCard[iAbandon:]
