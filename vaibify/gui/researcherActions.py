"""The researcher's command-line equivalent of every dashboard action.

Every state-mutating route the dashboard can invoke must also be
reachable from the command line, so a project can reach Level 3 with
no browser at all. A route is registered in exactly ONE of four places:

* ``actionCatalog.LIST_AGENT_ACTIONS`` -- callable by in-container
  agents and generated into ``vaibify do`` (ships into the container);
* :data:`LIST_RESEARCHER_ACTIONS` -- generated into ``vaibify do`` on
  the host only. This list is NEVER merged into the agent catalog:
  ``actionCatalog.fdictBuildCatalogJson`` ships only the agent list,
  and every route here stays refused on the agent lane (it is in
  ``SET_INTENTIONALLY_EXCLUDED_PATHS``, which remains the agent-lane
  authority);
* :data:`DICT_ROUTE_TO_HOST_COMMAND` -- an existing, hand-written
  ``vaibify`` command (credentials typed at a hidden prompt, container
  lifecycle, the transfer to a browser tab);
* :data:`SET_GUI_ONLY_ROUTES` -- genuine browser plumbing, each with
  the reason no command line needs it.

``testEveryGuiInvokedMutationHasCliEquivalent`` enforces that the four
cover every mutating route, that no route is in two, and that no entry
is stale.

An entry whose ``sLane`` is ``"hub"`` addresses the hub's control
plane (a project by name, not a running container's session): its
command authenticates as the researcher and claims no container, and
``{sName}`` / ``{sHostName}`` are the resolved project's name.
"""

__all__ = [
    "LIST_RESEARCHER_ACTIONS",
    "DICT_ROUTE_TO_HOST_COMMAND",
    "SET_GUI_ONLY_ROUTES",
    "DICT_WS_ACTION_TO_HOST_COMMAND",
    "S_LANE_HUB",
    "T_HUB_SESSION_PLACEHOLDERS",
    "fdictLookupResearcherAction",
]

S_LANE_HUB = "hub"
T_HUB_SESSION_PLACEHOLDERS = ("sName", "sHostName")
_T_PROJECT_DIRECTORY_QUERY = ("sProjectDirectory",)
_S_COUNCIL = "/api/agent-councils/{sContainerId}/{sCampaignId}"


def _fdictEntry(sName, sCategory, sMethod, sPath, sDescription, **dictExtra):
    dictEntry = {"sName": sName, "sCategory": sCategory, "sMethod": sMethod,
                 "sPath": sPath, "sDescription": sDescription}
    dictEntry.update(dictExtra)
    return dictEntry


def _fdictCouncilEntry(sName, sMethod, sSuffix, sDescription):
    return _fdictEntry(
        sName, "council", sMethod, _S_COUNCIL + sSuffix, sDescription,
        saQueryFields=list(_T_PROJECT_DIRECTORY_QUERY))


def _fdictHubEntry(sName, sCategory, sMethod, sPath, sDescription,
                   **dictExtra):
    return _fdictEntry(sName, sCategory, sMethod, sPath, sDescription,
                       sLane=S_LANE_HUB, **dictExtra)


LIST_RESEARCHER_ACTIONS = [
    _fdictEntry(
        "track-sync-file", "sync", "POST", "/api/sync/{sContainerId}/track",
        "Track or untrack one file for a remote. Args: {sPath, sService: "
        "github|zenodo|overleaf, bTrack}."),
    _fdictEntry(
        "update-workflow-settings", "workflow", "PUT",
        "/api/settings/{sContainerId}",
        "Change the project's settings. Args: any of {sPlotDirectory, "
        "sFigureType, iNumberOfCores, fTolerance, bAutoArchive, "
        "fDefaultWallClockBudgetSeconds}."),
    _fdictEntry(
        "hash-personal-layer-file", "verification", "POST",
        "/api/workflow/{sContainerId}/personal-layer/hash",
        "Record a hash commitment to a host file of your Personal AI "
        "Configuration (its SHA-256, size and date; never its content). "
        "Args: {sHostPath, sLabel}."),
    _fdictEntry(
        "import-project-context", "workflow", "POST",
        "/api/workflow/{sContainerId}/project-context/import",
        "Import a host file as the project context file "
        "(.vaibify/AGENTS.md). Args: {sHostPath, bOverwrite}."),
    _fdictEntry(
        "approve-first-prompt-capture", "verification", "POST",
        "/api/workflow/{sContainerId}/prompt-record/approve-first-capture",
        "Approve the Prompt Record's first sanitized capture after "
        "reviewing it."),
    _fdictEntry(
        "configure-supervision", "verification", "POST",
        "/api/workflow/{sContainerId}/supervision/configure",
        "Turn Supervised mode on or off. Args: {bEnabled}."),
    _fdictEntry(
        "end-supervision-on-host", "verification", "POST",
        "/api/workflow/{sContainerId}/supervision/end-on-host",
        "End supervision from the host."),
    _fdictEntry(
        "prepare-overleaf-diff", "sync", "POST",
        "/api/overleaf/{sContainerId}/diff",
        "Compare files with the bound Overleaf project before a push. "
        "Args: {listFilePaths, sTargetDirectory}."),
    _fdictEntry(
        "track-repo", "repos", "POST",
        "/api/repos/{sContainerId}/{sRepoName}/track",
        "Track a repository found in the workspace."),
    _fdictEntry(
        "ignore-repo", "repos", "POST",
        "/api/repos/{sContainerId}/{sRepoName}/ignore",
        "Ignore a repository found in the workspace."),
    _fdictEntry(
        "untrack-repo", "repos", "POST",
        "/api/repos/{sContainerId}/{sRepoName}/untrack",
        "Stop tracking a repository."),
    _fdictEntry(
        "push-staged-repo", "repos", "POST",
        "/api/repos/{sContainerId}/{sRepoName}/push-staged",
        "Commit what is staged in a repository and push it. Args: "
        "{sCommitMessage}."),
    _fdictEntry(
        "push-repo-files", "repos", "POST",
        "/api/repos/{sContainerId}/{sRepoName}/push-files",
        "Commit named files in a repository and push them. Args: "
        "{sCommitMessage, listFilePaths}."),
    _fdictEntry(
        "create-workflow", "workflow", "POST",
        "/api/workflows/{sContainerId}/create",
        "Create a new project (workflow) in a repository of the "
        "container. Args: {sWorkflowName, sFileName, sRepoDirectory}."),
    _fdictEntry(
        "start-council", "council", "POST",
        "/api/agent-councils/{sContainerId}/start",
        "Convene an Agent Council (spends your provider accounts). Args: "
        "{sQuestion, sCampaignName, listParticipants, iChairbotIndex, "
        "dictSettings, sProjectDirectory, listExcludedPaths, "
        "sCampaignKind, sSourceCampaignId, sSnapshotScope}; "
        "dictSettings may set iMaximumRounds (default 3)."),
    _fdictCouncilEntry(
        "respond-to-council", "POST", "/respond",
        "Answer a council's question. Args: {sResponseText, "
        "listDecisionAnswers, sResearcherComment}, sProjectDirectory."),
    _fdictCouncilEntry(
        "resume-council", "POST", "/resume",
        "Resume a paused council. Args: {bClearStopRequest}, "
        "sProjectDirectory."),
    _fdictCouncilEntry(
        "retry-council", "POST", "/retry",
        "Retry a council that stopped on an error. Args: "
        "{bClearStopRequest, iMaximumOutputBytesPerTurn}, "
        "sProjectDirectory."),
    _fdictCouncilEntry(
        "pause-council", "POST", "/pause",
        "Pause a running council. Args: sProjectDirectory."),
    _fdictCouncilEntry(
        "request-council-stop", "POST", "/request-stop",
        "Ask a running council to stop. Args: sProjectDirectory."),
    _fdictCouncilEntry(
        "grant-council-resolution-round", "POST", "/grant-resolution-round",
        "Grant a council further resolution rounds. Args: "
        "{iGrantedRounds}, sProjectDirectory."),
    _fdictCouncilEntry(
        "resolve-council-objections", "POST", "/resolve-objections",
        "Dispose of a council's open objections. Args: "
        "{dictDispositionByObjectionId}, sProjectDirectory."),
    _fdictCouncilEntry(
        "reject-council-candidate", "POST", "/reject-candidate",
        "Reject a council's candidate plan. Args: {sReasonText}, "
        "sProjectDirectory."),
    _fdictCouncilEntry(
        "accept-council-plan", "POST", "/accept-plan",
        "Accept a council's plan. Args: sProjectDirectory."),
    _fdictCouncilEntry(
        "delete-council", "DELETE", "",
        "Delete a retained council campaign. Args: sProjectDirectory."),
    _fdictCouncilEntry(
        "open-council-chat", "POST", "/chat/open",
        "Open a conversation with a council's chairbot. Args: "
        "sProjectDirectory."),
    _fdictCouncilEntry(
        "ask-council-chat", "POST", "/chat/ask",
        "Ask the chairbot a question. Args: {sQuestionText}, "
        "sProjectDirectory."),
    _fdictCouncilEntry(
        "close-council-chat", "POST", "/chat/close",
        "Close the chairbot conversation. Args: sProjectDirectory."),
    _fdictEntry(
        "test-council-credentials", "council", "POST",
        "/api/council-credentials/{sContainerId}/credential-test",
        "Consent to and start a paid test of provider logins for a "
        "council. Args: {listProviders}."),
    _fdictEntry(
        "cancel-council-credential-test", "council", "POST",
        "/api/council-credentials/{sContainerId}/credential-test/{sJobId}"
        "/cancel",
        "Cancel a running council credential test."),
    _fdictEntry(
        "withdraw-council-credential-consent", "council", "DELETE",
        "/api/council-credentials/{sContainerId}/credential-consent"
        "/{sProvider}",
        "Withdraw consent for a provider's login to be used by councils."),
    _fdictEntry(
        "set-council-snapshot-scope", "council", "POST",
        "/api/council-snapshots/{sContainerId}/scope",
        "Choose what a council snapshot copies. Args: {sScope, "
        "sProjectDirectory}."),
    _fdictHubEntry(
        "set-container-settings", "container", "POST",
        "/api/containers/{sName}/settings",
        "Change the container's settings, e.g. CPU and memory limits. "
        "Args: any of {iCpuLimit, fMemoryLimitGigabytes, bNeverSleep, "
        "bX11Forwarding, b<Agent>AutoUpdate}."),
    _fdictHubEntry(
        "cancel-container-start", "container", "POST",
        "/api/containers/{sName}/start/cancel",
        "Cancel a container start in progress. Args: sReservationId.",
        saQueryFields=["sReservationId"]),
    _fdictHubEntry(
        "acquire-pinned-image", "container", "POST",
        "/api/containers/{sName}/acquire-image",
        "Obtain the author's pinned image for this project. Args: "
        "bAllowEmulation, bWithoutAdditions.",
        saQueryFields=["bAllowEmulation", "bWithoutAdditions"]),
    _fdictHubEntry(
        "switch-to-building", "container", "POST",
        "/api/containers/{sName}/switch-to-building",
        "Build this project's image instead of using the pinned one."),
    _fdictHubEntry(
        "switch-to-pinned-image", "container", "POST",
        "/api/containers/{sName}/switch-to-pinned-image",
        "Use the author's pinned image instead of building. Args: "
        "bAllowEmulation.",
        saQueryFields=["bAllowEmulation"]),
    _fdictHubEntry(
        "unregister-project", "container", "DELETE",
        "/api/registry/{sName}",
        "Remove the project from vaibify's registry (files untouched)."),
    _fdictHubEntry(
        "delete-environment", "container", "POST",
        "/api/registry/{sName}/delete-environment",
        "Permanently delete the container, both volumes and every image "
        "tag the project owns. Args: {sConfirmation} -- the confirmation "
        "phrase the route asks for, typed out."),
    _fdictHubEntry(
        "scan-host-dependencies", "containerize", "POST",
        "/api/registry/{sName}/scan-dependencies",
        "Suggest packages from a host project's own imports before "
        "containerizing. Args: {saRelativePaths}."),
    _fdictHubEntry(
        "convert-to-container", "containerize", "POST",
        "/api/registry/{sName}/convert-to-container",
        "Containerize a host project. Args: the conversion form's fields "
        "(sProjectName, listRepositories, listFeatures, ...)."),
    _fdictHubEntry(
        "retry-containerization", "containerize", "POST",
        "/api/conversions/{sHostName}/retry",
        "Try a pending containerization again."),
    _fdictHubEntry(
        "commit-containerization", "containerize", "POST",
        "/api/conversions/{sHostName}/commit",
        "Finish switching a host project to its container."),
    _fdictHubEntry(
        "cancel-containerization", "containerize", "POST",
        "/api/conversions/{sHostName}/cancel",
        "Give up a pending containerization and delete the partial."),
    _fdictHubEntry(
        "promote-to-host-project", "containerize", "POST",
        "/api/registry/{sName}/promote-to-host-project",
        "Promote a host sandbox to a host-based Project. Args: "
        "{sProjectName}."),
    _fdictHubEntry(
        "return-to-host-mode", "containerize", "POST",
        "/api/registry/{sName}/return-to-host-mode",
        "Return a stuck container entry to host mode. Args: "
        "{bRestoreConfigAnyway}."),
    _fdictHubEntry(
        "rescan-remnants", "system", "POST", "/api/system/remnants/rescan",
        "Look again for sessions, keep-alives and containers vaibify "
        "left behind."),
    _fdictHubEntry(
        "remove-remnants", "system", "POST", "/api/system/remnants/remove",
        "Remove remnants by id. Args: {listItemIds}."),
    _fdictHubEntry(
        "set-idle-timeout", "system", "PUT", "/api/preferences/idle-timeout",
        "Set how long an idle hub waits before shutting down. Args: "
        "{sValue}."),
    _fdictHubEntry(
        "set-session-cap", "system", "PUT", "/api/preferences/session-cap",
        "Set how long a browser session may live. Args: {sValue}."),
]

DICT_ROUTE_TO_HOST_COMMAND = {
    ("POST", "/api/sync/{sContainerId}/setup"): "credential",
    ("POST", "/api/containers/{sName}/build"): "build",
    ("POST", "/api/containers/{sName}/start"): "start",
    ("POST", "/api/containers/{sName}/stop"): "stop",
    ("POST", "/api/registry"): "register",
    ("POST", "/api/registry/{sName}/reconcile"): "reconcile",
    # `vaibify start --image-trust` records the same trust grant.
    ("POST", "/api/registry/{sName}/image-trust"): "start",
    # `vaibify open` mints and redeems the transfer capability.
    ("POST", "/api/transfer"): "open",
    # The dashboard's new-project form; `vaibify init` scaffolds one.
    ("POST", "/api/projects/create"): "init",
}

SET_GUI_ONLY_ROUTES = {
    ("POST", "/api/bootstrap"):
        "redeems a browser launch capability; the CLI bootstraps its "
        "own session over the host control socket",
    ("POST", "/api/connect/{sContainerId}"):
        "opens the page's session; every `vaibify do` connects itself",
    ("POST", "/api/session/spawn"):
        "opens another browser tab's session",
    ("POST", "/api/session/renew"):
        "renews a browser tab's absolute session cap",
    ("POST", "/api/registry/{sName}/claim"):
        "a tab's lease; the CLI claims and releases per command",
    ("POST", "/api/registry/{sName}/release"):
        "a tab's lease; the CLI claims and releases per command",
    ("POST", "/api/system/docker-status/retry"):
        "the page's retry of its own Docker probe; `vaibify doctor` "
        "checks Docker from the command line",
    ("POST", "/api/workflow/{sContainerId}/remotes/refresh"):
        "the page's open-time refresh; `vaibify do verify-remote` checks "
        "each remote",
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/scan-dependencies"):
        "the dependency modal's per-step read; `vaibify do "
        "rescan-dependencies` scans every step",
    ("POST", "/api/steps/{sContainerId}/{iStepIndex}/scan-scripts"):
        "the step editor's script detection, a read the editor fills a "
        "form with",
    ("POST", "/api/host-directories/create"):
        "the folder picker's new-folder button; a shell's mkdir",
    ("PUT", "/api/preferences/host-warning-acknowledged"):
        "a browser warning's acknowledgement",
}

# Interactive steps answer inside a running pipeline: `vaibify do`'s
# run commands prompt at the terminal, or fail at once with
# --non-interactive.
DICT_WS_ACTION_TO_HOST_COMMAND = {
    "interactiveResume": "do",
    "interactiveSkip": "do",
    "interactiveComplete": "do",
}


def fdictLookupResearcherAction(sName):
    """Return the researcher action named sName, or None."""
    for dictEntry in LIST_RESEARCHER_ACTIONS:
        if dictEntry["sName"] == sName:
            return dictEntry
    return None
