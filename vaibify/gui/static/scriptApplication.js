/* Vaibify — Main application logic */

const VaibifyApp = (function () {
    "use strict";

    var fbStepIsInteractive = VaibifyUtilities.fbStepIsInteractive;

    function fbIsTerminalFocused() {
        var elActive = document.activeElement;
        if (!elActive) return false;
        return !!elActive.closest("#terminalStrip, .xterm");
    }

    var _dictSessionState = {
        sSessionToken: "",
        sContainerId: null,
        sUserName: "User",
        dictDashboardMode: null,
        sLeaseId: "",
        sLeaseContainerName: null,
        fSessionExpiryWarnedFraction: 0,
        /* The server's answer, stored so panels that must speak
           differently about a host project ask one place rather than
           each inferring the mode for themselves. Defaults to the
           container so an older server that sends no mode renders
           exactly what it always did. */
        sProjectMode: "container",
        /* Where this project's files live. A container's is
           /workspace; a host project's is the directory the
           researcher registered, and the frontend has no way to know
           it. Same default for the same reason. */
        sWorkspaceRoot: "/workspace",
    };

    var _S_LEASE_STORAGE_KEY = "vaibifyContainerLease";
    var _S_SESSION_CREDENTIAL_STORAGE_KEY = "vaibifySessionCredential";

    function _fdictDefaultWorkflowState() {
        return {
            dictWorkflow: null,
            sWorkflowPath: null,
            dictStepStatus: {},
            dictStepTaint: {},
            dictScriptModified: {},
            dictStaleArtifacts: {},
            dictDiscoveredOutputs: {},
            dictUserVerifiedAt: {},
            dictFileExistenceCache: {},
            dictFileModTimes: {},
            dictOutputMtimes: {},
            dictPlotMtimes: {},
            dictMaxDataMtimeByStep: {},
            dictMaxInputMtimeByStep: {},
            dictMarkerMtimeByStep: {},
            dictTestSourceMtimeByStep: {},
            dictTestCategoryMtimes: {},
            dictPlotStandardExists: {},
            dictBlockersByStep: {},
            dictBlockersByStepLevel2: {},
            dictBlockersByStepLevel3: {},
            dictStepLevels: {},
            dictStepLevelHighWater: {},
            dictStepLevelWarnings: {},
            dictWorkflowScopeLevels: null,
            dictWorkflowLevelHighWater: {},
            dictWorkflowEnvelopeDetail: null,
            dictRemoteChecks: {},
            /* The Project block renders a lock verdict that costs a
               container exec, and the poll is forbidden to make one.
               True means nobody has asked yet, so the block has
               nothing to render but a wait; the open-time check
               clears it once an answer -- including "unknown" -- has
               been measured and a poll has carried it back. The first
               paint must be correct: a provisional green is not a
               smaller error than a slow page, it is a worse one,
               because the researcher clicks it (2026-09-15). */
            bProjectBlockAwaitsFirstAnswer: true,
            iL1BlockerCount: 0,
            iL2BlockerCount: 0,
            iL3BlockerCount: 0,
            iCachedProofLevel: null,
            iWorkflowEpoch: -1,
            sWorkflowFingerprint: "",
            iFileCheckTimer: null,
            bFileCheckInProgress: false,
            iInflightRequests: 0,
            abortControllerFileCheck: null,
            bDelegatedEventsInitialized: false,
            iLastRenderedProofLevel: 0,
            listUndoStack: [],
            dictContainerSettings: null,
            bAgentRestartNeeded: false,
        };
    }

    var _dictWorkflowState = _fdictDefaultWorkflowState();

    var _dictUiState = {
        iSelectedStepIndex: -1,
        setExpandedSteps: new Set(),
        setExpandedDeps: new Set(),
        setExpandedQualitative: new Set(),
        setExpandedQuantitative: new Set(),
        setExpandedIntegrity: new Set(),
        setExpandedRequirementGroups: new Set(),
        setExpandedRequirementRows: new Set(),
        // Records a flip AWAY from each file group's default
        // open state, not the state itself, so the defaults
        // keep applying to groups the researcher never
        // touched — including ones that did not exist yet.
        setToggledFileGroups: new Set(),
        // Per-step level sections in the Step Viewer: keys are
        // "iStep:iLevel". A step is seeded once (its target rung
        // opens) and remembered in setLevelSeededSteps so the
        // researcher's own toggles are never overridden.
        setExpandedStepLevels: new Set(),
        setLevelSeededSteps: new Set(),
        // Description block: seeded open when the step already has
        // text, then the researcher's toggles win.
        setExpandedStepDescriptions: new Set(),
        setDescriptionSeededSteps: new Set(),
        bStepsCollapsed: false,
        bProjectBlockCollapsed: false,
        bBinaryAddFormOpen: false,
        bShowTimestamps: false,
        iContextStepIndex: -1,
    };

    var I_MAX_UNDO = 50;
    var fbIsBinaryFile = VaibifyUtilities.fbIsBinaryFile;

    var DICT_MODE_WORKFLOW = {
        sMode: "workflow",
        // "repos" joined this list on 2026-08-25. It was present only
        // in the no-workflow mode, so the panel was unreachable
        // exactly when a project was open -- while four
        // researcher-facing pointers that render ONLY with a project
        // open sent the reader to it: the PROOF tab's L2 GitHub and
        // Zenodo rows (whose fix button is literally labelled "Open
        // the Repos panel"), and the Project block's two
        // published-copies hints. The button half-worked by accident
        // -- a programmatic .click() fires on a display:none tab --
        // so the panel opened with no tab to return to.
        listLeftTabs: ["steps", "proof", "files", "repos", "logs"],
        sDefaultLeftTab: "steps",
        bShowRunMenu: true,
        bShowDagButton: true,
    };

    var DICT_MODE_NO_WORKFLOW = {
        sMode: "noWorkflow",
        listLeftTabs: ["files", "repos", "logs"],
        sDefaultLeftTab: "files",
        bShowRunMenu: false,
        bShowDagButton: false,
    };

    function _fsReadBootstrapCapabilityFromFragment() {
        var sHash = window.location.hash || "";
        var oMatch = sHash.match(/[#&]bootstrap=([^&]+)/);
        return oMatch ? decodeURIComponent(oMatch[1]) : "";
    }

    function _fsReadTransferCapabilityFromFragment() {
        var sHash = window.location.hash || "";
        var oMatch = sHash.match(/[#&]transfer=([^&]+)/);
        return oMatch ? decodeURIComponent(oMatch[1]) : "";
    }

    function _fnClearCapabilityFragment() {
        try {
            window.history.replaceState(
                null, "",
                window.location.pathname + window.location.search,
            );
        } catch (e) { /* history unavailable; leave the fragment */ }
    }

    function _fsRestoreStoredCredential() {
        try {
            return window.sessionStorage.getItem(
                _S_SESSION_CREDENTIAL_STORAGE_KEY) || "";
        } catch (e) { return ""; }
    }

    function _fnStoreCredential(sCredential) {
        try {
            window.sessionStorage.setItem(
                _S_SESSION_CREDENTIAL_STORAGE_KEY, sCredential);
        } catch (e) { /* sessionStorage unavailable; memory only */ }
    }

    async function _fsExchangeBootstrapCapability(sCapability) {
        /* Raw fetch: the authenticated wrapper is not installed yet, and
         * /api/bootstrap is exempt from the credential requirement. */
        try {
            var response = await window.fetch("/api/bootstrap", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sCapability: sCapability }),
            });
            if (!response.ok) return "";
            var data = await response.json();
            return data.sCredential || "";
        } catch (e) { return ""; }
    }

    async function _fsExchangeTransferCapability(sCapability) {
        /* The 'vaibify open' landing: the CLI minted this capability over
         * the host control socket, redeemed it (committing the ownership
         * transfer), and launched this tab with it in the URL fragment.
         * This exchange rides the server's bounded replay window, so it
         * returns the SAME credential-and-lease tuple the commit minted.
         * Raw fetch: /api/transfer is exempt, like /api/bootstrap. */
        try {
            var response = await window.fetch("/api/transfer", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sCapability: sCapability }),
            });
            var data = await response.json();
            if (!response.ok || data.sOutcome !== "transferred") {
                _fnReportTransferRefusal(data);
                return "";
            }
            if (data.sContainerName && data.sLeaseId) {
                fnRecordClaimedLease(data.sContainerName, data.sLeaseId);
            }
            return data.sCredential || "";
        } catch (e) {
            _fnReportTransferRefusal(null);
            return "";
        }
    }

    /* A refused transfer used to vanish: the exchange returned "" and
       the tab quietly fell back to a stored credential or to none at
       all, so the researcher who had just run 'vaibify open' saw a
       dashboard that was simply not attached to their container and no
       reason why. Every refusal the server sends names its own recovery
       -- retry, re-mint, claim normally, reconcile -- and that is the
       message to show. The generic line is used ONLY when there is no
       server answer to show (the hub could not be reached at all), and
       it says exactly that rather than inventing a cause. */
    function _fnReportTransferRefusal(dictOutcome) {
        var sMessage = (dictOutcome || {}).sMessage;
        if (!sMessage) {
            sMessage = "The hub could not be reached to complete the "
                + "hand-over. Reload this page to try again, or run "
                + "'vaibify open' once more.";
        }
        fnShowToast(sMessage, "error");
    }

    async function fnFetchSessionToken() {
        /* Prefer a fresh capability from the URL fragment -- a transferred
         * session ('vaibify open') or a launch bootstrap -- exchanged once
         * and then cleared, then a stored per-tab credential (so a reload
         * keeps working). With neither, the tab has no credential and is
         * correctly denied -- the retired shared-token oracle is gone. */
        var sCredential = "";
        var sTransferCapability = _fsReadTransferCapabilityFromFragment();
        if (sTransferCapability) {
            sCredential = await _fsExchangeTransferCapability(
                sTransferCapability);
        }
        var sCapability = _fsReadBootstrapCapabilityFromFragment();
        if (!sCredential && sCapability) {
            sCredential = await _fsExchangeBootstrapCapability(sCapability);
        }
        /* Clear the fragment only once an exchange has SUCCEEDED. A
         * transient failure (offline, hub restarting) must leave the
         * capability in the URL so a reload can re-mint -- the server
         * honours a bounded replay window -- rather than discarding the
         * one credential-minting token on the first hiccup. */
        if (sCredential && (sTransferCapability || sCapability)) {
            _fnClearCapabilityFragment();
            _fnStoreCredential(sCredential);
        }
        if (!sCredential) {
            sCredential = _fsRestoreStoredCredential();
        }
        _dictSessionState.sSessionToken = sCredential;
        fnInstallAuthenticatedFetch(_dictSessionState.sSessionToken);
    }

    function fnInstallAuthenticatedFetch(sToken) {
        var originalFetch = window.fetch;
        window.fetch = function (sUrl, dictOptions) {
            dictOptions = dictOptions || {};
            dictOptions.headers = dictOptions.headers || {};
            /* The lease is read INSIDE the wrapper on every call: the
               wrapper is installed before the container is claimed, so
               capturing fsGetLeaseId() at install time would send an empty
               lease forever. The lease travels as a header, never a query
               param, so it never lands in an access log or browser
               history. */
            var sLease = fsGetLeaseId();
            if (typeof dictOptions.headers.set === "function") {
                dictOptions.headers.set("X-Session-Token", sToken);
                if (sLease) dictOptions.headers.set("X-Vaibify-Lease", sLease);
            } else {
                dictOptions.headers["X-Session-Token"] = sToken;
                if (sLease) dictOptions.headers["X-Vaibify-Lease"] = sLease;
            }
            return originalFetch.call(window, sUrl, dictOptions);
        };
    }

    /* --- Per-tab claim lease --- */

    function _fnPersistLease() {
        try {
            if (!_dictSessionState.sLeaseId) {
                window.sessionStorage.removeItem(_S_LEASE_STORAGE_KEY);
                return;
            }
            window.sessionStorage.setItem(
                _S_LEASE_STORAGE_KEY,
                JSON.stringify({
                    sName: _dictSessionState.sLeaseContainerName,
                    sLeaseId: _dictSessionState.sLeaseId,
                }),
            );
        } catch (error) {
            /* sessionStorage unavailable; lease lives in memory only */
        }
    }

    function _fnRestoreLeaseFromStorage() {
        try {
            var sStored = window.sessionStorage.getItem(
                _S_LEASE_STORAGE_KEY);
            if (!sStored) return;
            var dictStored = JSON.parse(sStored);
            _dictSessionState.sLeaseContainerName =
                dictStored.sName || null;
            _dictSessionState.sLeaseId = dictStored.sLeaseId || "";
        } catch (error) {
            /* corrupt or unavailable storage; start with no lease */
        }
    }

    function fnRecordClaimedLease(sName, sLeaseId) {
        _dictSessionState.sLeaseContainerName = sName;
        _dictSessionState.sLeaseId = sLeaseId || "";
        _fnPersistLease();
    }

    function fnForgetLease() {
        _dictSessionState.sLeaseContainerName = null;
        _dictSessionState.sLeaseId = "";
        _fnPersistLease();
    }

    function fsGetLeaseId() {
        return _dictSessionState.sLeaseId || "";
    }

    function fsGetLeaseForContainer(sName) {
        if (_dictSessionState.sLeaseContainerName === sName) {
            return _dictSessionState.sLeaseId || "";
        }
        return "";
    }

    function _fnRecordViewerLeaseFromConnect(sId, dictConnect) {
        /* Viewer mode mints its lease server-side and returns it on the
           connect response (the viewer has no claim route). The served
           lease is AUTHORITATIVE: it must replace any lease left in
           sessionStorage by a previous hub process, or every
           WebSocket presents a foreign lease and fails closed as 1006
           after a hub restart (live incident 2026-07-03 — a reload
           preserves sessionStorage, so the stale lease survived every
           restart). Hub-mode connect responses carry no lease
           (sLeaseId ""), so the first guard leaves the claim-recorded
           lease untouched; the second skips a redundant re-record when
           the stored lease already matches. */
        if (!dictConnect || !dictConnect.sLeaseId) return;
        if (fsGetLeaseId() === dictConnect.sLeaseId) return;
        var sName = VaibifyContainerManager
            .fsGetSelectedContainerName() || sId;
        fnRecordClaimedLease(sName, dictConnect.sLeaseId);
    }

    /* --- WebSocket and Polling Registration --- */

    function fnRegisterWebSocketHandlers() {
        VaibifyWebSocket.fnOnEvent("*",
            VaibifyPipelineRunner.fnHandlePipelineEvent);
        VaibifyWebSocket.fnOnEvent("_wsClose", function (dictEvent) {
            fnClearRunningStatuses();
            fnRenderStepList();
            if (dictEvent.bActionsDropped) {
                // The socket died holding unsent actions: whatever
                // the researcher just clicked (typically a step run
                // that already painted its queued light) never
                // reached the server. Saying so beats letting the
                // queued light silently evaporate (live incident,
                // 2026-07-03).
                fnShowToast(
                    "Connection lost before your last request " +
                    "reached the server — it was NOT submitted. " +
                    "A step run that showed as queued never " +
                    "started. Reconnect and retry.",
                    "error");
            }
            _fnReportConnectionLossToMonitor(dictEvent);
        });
        VaibifyWebSocket.fnOnEvent("_wsError", function (dictEvent) {
            _fnReportConnectionLossToMonitor(dictEvent);
        });
        VaibifyWebSocket.fnOnEvent("_wsReconnect", function () {
            // The link was down for an unknown span, over which a
            // remote could have gained or lost a published file. The
            // badges are re-asked for the same reason they are on
            // project open: the alternative is presenting evidence
            // gathered before the gap as if it survived it.
            VaibifySyncManager.fnRefreshConfiguredRemotes(
                _dictSessionState.sContainerId);
        });
    }

    function _fnReportConnectionLossToMonitor(dictEvent) {
        if (typeof VaibifyConnectionMonitor === "undefined") return;
        VaibifyConnectionMonitor.fnReportWsLoss(dictEvent);
    }

    function fnRegisterPollingHandlers() {
        VaibifyPolling.fnSetPipelineStateHandler(
            VaibifyPipelineRunner.fnHandlePipelinePollResult);
        VaibifyPolling.fnSetFileStatusHandler(
            fnProcessFileStatusResponse);
        VaibifyPolling.fnSetWorkflowDiscoveryHandler(
            fnProcessWorkflowDiscovery);
        VaibifyPolling.fnSetSessionLifetimeHandler(
            _fnHandleSessionLifetime);
        VaibifyPolling.fnSetFileTreeHandler(
            VaibifyFiles.fnRefreshCurrentDirectory);
    }

    function _fnHandleSessionLifetime(dictLifetime) {
        /* The server says how far through its cap this session is and
         * WHICH warning band that puts it in; every number here comes
         * from that payload, so the warning cannot fire at a threshold
         * the server does not believe in. The bands themselves are
         * deliberately not mirrored here.
         *
         * Warned once per BAND, not once per crossing: the poll
         * repeats every minute, and the researcher who ignored the
         * first notice still deserves the louder one later. A session
         * that reports a lower band than the one already warned has
         * been renewed or replaced, so the latch falls back with it.
         */
        if (!dictLifetime || !dictLifetime.bSessionKnown ||
                !dictLifetime.bExpiringSoon) {
            _dictSessionState.fSessionExpiryWarnedFraction = 0;
            return;
        }
        var fBand = dictLifetime.fWarningFraction || 0;
        if (fBand <= _dictSessionState.fSessionExpiryWarnedFraction) {
            _dictSessionState.fSessionExpiryWarnedFraction = fBand;
            return;
        }
        _dictSessionState.fSessionExpiryWarnedFraction = fBand;
        fnShowToast(
            "This browser session reaches its maximum lifetime in " +
            _fsDescribeRemainingLifetime(
                dictLifetime.fSecondsUntilSessionCap) +
            " (" + Math.round(fBand * 100) + "% of the way through). " +
            "Click to renew it — your open panels and agent " +
            "conversations stay as they are. Otherwise run " +
            "'vaibify open' for a fresh tab; the container and any " +
            "running step keep going either way. Session lifetime is " +
            "under the toolbar gear.",
            dictLifetime.bFinalWarning ? "error" : "warning",
            _fnRenewSessionLifetime);
    }

    function _fsDescribeRemainingLifetime(fSeconds) {
        /* Days, hours or minutes — whichever the number actually is.
         * A seven-day cap warned at three quarters has forty-two hours
         * left, and "about 2520 minutes" is a number nobody reads. */
        var iMinutes = Math.max(1, Math.round(fSeconds / 60));
        if (iMinutes < 90) {
            return "about " + iMinutes + " minute" +
                (iMinutes === 1 ? "" : "s");
        }
        var iHours = Math.round(iMinutes / 60);
        if (iHours < 48) {
            return "about " + iHours + " hours";
        }
        return "about " + Math.round(iHours / 24) + " days";
    }

    async function _fnRenewSessionLifetime() {
        /* Only ever from the researcher's own click. Nothing in the
         * polling path may call this: a renewal on a timer would
         * delete the cap while the setting went on claiming one. */
        try {
            var dictRenewed = await VaibifyApi.fdictPost(
                "/api/session/renew", {});
            if (!dictRenewed || !dictRenewed.bRenewed) {
                fnShowToast(
                    "This session could not be renewed — it has " +
                    "already ended. Run 'vaibify open' for a fresh " +
                    "tab.", "error");
                return;
            }
            _dictSessionState.fSessionExpiryWarnedFraction = 0;
            fnShowToast(
                "Session renewed — " +
                _fsDescribeRemainingLifetime(
                    dictRenewed.fSecondsUntilSessionCap) +
                " from now.", "success");
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
        }
    }

    /* --- Initialization --- */

    function _fnReportNoCredentialAndStop() {
        /* The tab holds no credential, so every API call will answer
           401. Say so, in the place the researcher is already looking.

           Without this the page half-initialised: fnLoadUserName threw
           on the first 401, the rest of fnInitialize never ran, and
           what stayed on screen was the STATIC "Loading environments..."
           from index.html — so an unauthenticated dashboard was
           indistinguishable from a slow one, and the Add button was
           dead because its binding never happened. A spinner that
           means "refused" is the dashboard misreporting its own
           state. */
        var elList = document.getElementById("listContainers");
        if (elList) {
            elList.innerHTML =
                '<p style="color: var(--color-red-text);">' +
                "This tab is not signed in.</p>" +
                '<p class="muted-text">' +
                "The dashboard signs in with a one-time link, so a " +
                "bookmarked or retyped address cannot. Re-run " +
                "<code>vaibify</code> and use the tab it opens." +
                "</p>";
        }
    }

    async function fnInitialize() {
        _fnRestoreLeaseFromStorage();
        await fnFetchSessionToken();
        if (!_dictSessionState.sSessionToken) {
            _fnReportNoCredentialAndStop();
            return;
        }
        fnRegisterWebSocketHandlers();
        fnRegisterPollingHandlers();
        /* Container-independent: a session sitting on the picker is
         * subject to the same cap as one inside a workflow. */
        VaibifyPolling.fnStartSessionLifetimePolling();
        fnLoadUserName();
        fnLoadTimestampSetting();
        fnShowContainerLanding();
        VaibifyEventBindings.fnBindToolbarEvents();
        VaibifyEventBindings.fnBindWorkflowPickerEvents();
        VaibifyContainerManager.fnBindContainerLandingEvents();
        VaibifyContainerManager.fnBindAddContainerModal();
        VaibifyEventBindings.fnBindErrorModal();
        VaibifyTestManager.fnBindApiConfirmModal();
        VaibifyEventBindings.fnBindContextMenuEvents();
        VaibifyEventBindings.fnBindLeftPanelTabs();
        VaibifyEventBindings.fnBindResizeHandles();
        VaibifyEventBindings.fnBindGlobalSettingsToggle();
        VaibifyEventBindings.fnBindHostSettingsToggle();
        VaibifyEventBindings.fnBindRefreshRemoteStatus();
        /* A start outlives the request that asked for it, so a reload
           mid-start must pick the poll back up. Deliberately NOT
           awaited: the poll runs for as long as the start does, and
           initialization must not wait on a container pull. */
        VaibifyContainerManager.fnResumeInterruptedStart();
        document.addEventListener("click", function () {
            fnHideContextMenu();
        });
        /*
         * TERMINAL SAFETY: When the terminal pane is focused, ALL
         * keystrokes must pass through to the container PTY
         * unmodified. Before adding any new global keybinding,
         * check fbIsTerminalFocused() and skip if true.
         */
        document.addEventListener("keydown", function (event) {
            if ((event.ctrlKey || event.metaKey) && event.key === "z") {
                if (fbIsTerminalFocused()) return;
                event.preventDefault();
                fnUndo();
            }
        });
    }

    async function fnLoadUserName() {
        try {
            var dictUser = await VaibifyApi.fdictGet("/api/user");
            fnSetVerificationUserName(dictUser.sUserName);
        } catch (error) {
            fnSetVerificationUserName("User");
        }
    }

    function _fnResetWorkflowState() {
        var dictDefaults = _fdictDefaultWorkflowState();
        for (var sKey in dictDefaults) {
            _dictWorkflowState[sKey] = dictDefaults[sKey];
        }
        _fnResetUiState();
        _fnInvalidateAllRenderCaches();
        _bReflectedDispatchRun = false;
        _iReflectedActiveIndex = -1;
        _fnRenderOtherProjectRuns(null);
        VaibifyTestManager.fnResetState();
        VaibifyPipelineRunner.fnResetState();
        VaibifyOverleafMirror.fnResetState();
        VaibifySyncManager.fnResetState();
        VaibifyGitBadges.fnResetState();
        VaibifyPolling.fnStopPipelinePolling();
        VaibifyPolling.fnStopFilePolling();
        VaibifyPolling.fnStopFileTreePolling();
        VaibifyPolling.fnStopDiscoveryPolling();
        VaibifyPolling.fnStopPromptRecordPolling();
        VaibifyReposPanel.fnTeardown();
        if (typeof VaibifyAgentCouncil !== "undefined") {
            VaibifyAgentCouncil.fnTeardown();
        }
        VaibifyProofTab.fnSetContainerId(null);
    }

    function _fnResetUiState() {
        _dictUiState.iSelectedStepIndex = -1;
        _dictUiState.setExpandedSteps.clear();
        _dictUiState.setExpandedDeps.clear();
        _dictUiState.setExpandedQualitative.clear();
        _dictUiState.setExpandedQuantitative.clear();
        _dictUiState.setExpandedIntegrity.clear();
        _dictUiState.setExpandedRequirementGroups.clear();
        _dictUiState.setExpandedRequirementRows.clear();
        _dictUiState.setToggledFileGroups.clear();
        _dictUiState.setExpandedStepLevels.clear();
        _dictUiState.setLevelSeededSteps.clear();
        _dictUiState.setExpandedStepDescriptions.clear();
        _dictUiState.setDescriptionSeededSteps.clear();
        _dictUiState.bBinaryAddFormOpen = false;
    }

    function fnApplyWorkspaceRoot(sWorkspaceRoot) {
        /* Stored, never derived. Every file panel, directory browser
           and path-display in the dashboard used to write /workspace
           as a constant, which is true of a container and false of a
           host project. An empty or missing value keeps the container
           default so an older server behaves exactly as it did. */
        _dictSessionState.sWorkspaceRoot = sWorkspaceRoot || "/workspace";
    }

    function fnApplyRemoteSession(bRemote, sExecutionHostname) {
        /* A remote session is not a variant of host or container mode:
           either can be reached over a tunnel. So this is its own
           indicator, and it names the machine -- "somewhere else" is
           not an answer a researcher can act on. */
        _dictSessionState.bRemoteSession = Boolean(bRemote);
        _dictSessionState.sExecutionHostname = sExecutionHostname || "";
        var elBadge = document.getElementById("remoteSessionBadge");
        if (elBadge) {
            elBadge.textContent = bRemote
                ? ("REMOTE \u2014 " + (sExecutionHostname || "another machine"))
                : "";
            elBadge.style.display = bRemote ? "" : "none";
        }
        _fnHideAffordancesThatCannotWorkRemotely(Boolean(bRemote));
    }

    function _fnHideAffordancesThatCannotWorkRemotely(bRemote) {
        /* Both of these hand the BROWSER an address, and through a
           tunnel the browser's 127.0.0.1 is the laptop. A new hub is
           spawned on a port chosen after the tunnel was built, so
           nothing forwards it; a vscode:// deep link carries a
           container id that exists only on the remote daemon. Hiding
           beats letting the browser report a deliberate dead end as a
           server failure. */
        var listRemoteHostile = [
            "btnNewVaibifyWindow",
            "btnNewVaibifyWindowWorkflows",
            "btnVsCode",
        ];
        listRemoteHostile.forEach(function (sId) {
            var elButton = document.getElementById(sId);
            if (!elButton) return;
            elButton.style.display = bRemote ? "none" : "";
            if (bRemote) {
                elButton.title =
                    "Unavailable in a remote session: this would open " +
                    "an address on the computer you are sitting at, " +
                    "not on the machine running your work.";
            }
        });
    }

    function fnApplyExecutionTopology(dictTopology) {
        /* Stored as sent. The frontend must not re-derive which
           locations coincide from the mode string: that is a second
           implementation of a fact the server already knows, and it
           was the absence of ANY such check that let a host-mode
           "pull to host" ship as a self-copy presented as a transfer. */
        _dictSessionState.dictExecutionTopology = dictTopology || {};
    }

    function fbExecutionHostIsTheEnvironment() {
        return Boolean(
            (_dictSessionState.dictExecutionTopology || {})
                .bSameFilesystem
        );
    }

    function fbIsRemoteSession() {
        return Boolean(_dictSessionState.bRemoteSession);
    }

    function fnApplyProjectMode(sProjectMode) {
        /* The mode is always the SERVER's answer -- the registry
           listing that rendered the tile on the project-list screen,
           the connect handshake once a project is open -- never
           something the dashboard infers for itself. A permanent
           claim about whether commands are contained is not one to be
           wrong about. Anything other than "host" hides the badge, so
           an older server that sends no mode renders exactly the
           container toolbar it always did.

           Every ``.host-mode-badge`` is driven, not one by id: the
           badge appears on both the project-list screen and the
           workflow toolbar, and two elements with one rule cannot
           disagree the way two rules would. */
        var bHost = sProjectMode === "host";
        _dictSessionState.sProjectMode = bHost ? "host" : "container";
        document.querySelectorAll(".host-mode-badge").forEach(
            function (elBadge) {
                elBadge.style.display = bHost ? "" : "none";
            }
        );
        var elLabel = document.getElementById("activeResourceLabel");
        if (elLabel) {
            elLabel.textContent = bHost ? "Directory:" : "Container:";
        }
    }

    function _fnRenderActiveResource(sProjectMode) {
        /* A host project's toolbar names the DIRECTORY it works in, not
           a separate sandbox name -- the two are the same thing, and
           the field is labelled "Directory:". The basename keeps the
           bar compact; the full path rides the hover title. A container
           project still shows its container name. */
        var elValue = document.getElementById("activeContainerName");
        if (!elValue) return;
        if (sProjectMode === "host") {
            var sDir =
                VaibifyContainerManager.fsGetSelectedContainerDirectory();
            elValue.textContent = _fsBasename(sDir) || sDir || "";
            elValue.title = sDir || "";
            return;
        }
        elValue.textContent =
            VaibifyContainerManager.fsGetSelectedContainerName() || "";
        elValue.title = "";
    }

    function _fsBasename(sPath) {
        var sTrimmed = (sPath || "").replace(/\/+$/, "");
        var iLastSlash = sTrimmed.lastIndexOf("/");
        return iLastSlash >= 0
            ? sTrimmed.substring(iLastSlash + 1) : sTrimmed;
    }

    function _fnActivateWorkflow(sId, data, sWorkflowName) {
        /* A container hosts several projects, and the pipeline socket
           outlives a switch between them. Left open, it kept
           delivering the previous project's run events -- step
           numbers that index ANOTHER step list -- onto the new
           project's lights and saves. The run itself continues on
           the server; reopening its project reconnects to it. */
        if (_dictWorkflowState.sWorkflowPath &&
            _dictWorkflowState.sWorkflowPath !== data.sWorkflowPath) {
            VaibifyWebSocket.fnDisconnect();
        }
        _fnResetWorkflowState();
        VaibifyPolling.fnStopDiscoveryPolling();
        _fnRecordViewerLeaseFromConnect(sId, data);
        _dictSessionState.sContainerId = sId;
        _dictWorkflowState.dictWorkflow = data.dictWorkflow;
        _dictWorkflowState.sWorkflowPath = data.sWorkflowPath;
        _dictWorkflowState.iWorkflowEpoch =
            typeof data.iWorkflowEpoch === "number" ?
                data.iWorkflowEpoch : -1;
        _dictWorkflowState.sWorkflowFingerprint =
            data.sWorkflowFingerprint || "";
        /* The freshness acknowledgment: what this dashboard has
           APPLIED, advanced only at apply points and on this
           client's own save responses — never merely displayed. */
        _dictWorkflowState.sAcknowledgedSourceFingerprint =
            (data.dictWorkflow || {})._sSourceFingerprint || "";
        _fnLoadStepsCollapsed();
        _dictSessionState.dictDashboardMode = DICT_MODE_WORKFLOW;
        _fnSurfaceStateLoadNotice(data.dictWorkflow);
        if (data.dictFileStatus) {
            fnProcessFileStatusResponse(data.dictFileStatus);
        }
        var iStepCount = (_dictWorkflowState.dictWorkflow.listSteps || []).length;
        if (iStepCount > 500) {
            fnShowToast(
                "This project has " + iStepCount + " steps. " +
                "Large projects may use significant memory. " +
                "Avoid expanding many steps simultaneously.",
                "error"
            );
        }
        _fnRenderActiveResource(data.sProjectMode);
        fnApplyProjectMode(data.sProjectMode);
        fnApplyRemoteSession(
            data.bRemoteSession, data.sExecutionHostname);
        fnApplyExecutionTopology(data.dictExecutionTopology);
        fnApplyWorkspaceRoot(data.sWorkspaceRoot);
        VaibifyWebSocket.fnSetReconnectWindowSeconds(
            data.fReconnectWindowSeconds);
        document.getElementById("activeWorkflowName").textContent =
            sWorkflowName || "";
        document.title = (VaibifyContainerManager.fsGetSelectedContainerName() || "Vaibify") +
            (sWorkflowName ? ": " + sWorkflowName : "");
        fnShowMainLayout();
        fnRenderStepList();
        fnUpdateHighlightState();
        fnPollAllStepFiles();
        fnStartFileChangePolling();
        VaibifyPolling.fnStartFileTreePolling();
        try {
            VaibifyTerminal.fnEnsureTab();
        } catch (errorTerminal) {
            // A terminal failure must never abort the rest of
            // activation (PROOF tab, repos panel, badges, pipeline
            // recovery below) — that shipped as a raw "Terminal is
            // not defined" toast plus a half-initialized dashboard.
            fnShowToast(
                "Terminal setup failed: " +
                fsSanitizeErrorForUser(errorTerminal.message),
                "error");
        }
        // The PROOF and Repos tabs are container-scoped: without
        // these two calls they sit in their "connect first" empty
        // states for the entire workflow session, which is the mode
        // researchers are actually in.
        VaibifyProofTab.fnSetContainerId(sId);
        VaibifyReposPanel.fnInit(sId);
        if (typeof VaibifyAgentCouncil !== "undefined") {
            VaibifyAgentCouncil.fnActivate(sId);
        }
        // Badges otherwise stay empty until a sync action bumps the
        // epoch mid-session: the per-file remote icons render grey
        // and the declaration commit/remove buttons gate wrong on
        // every fresh load. One fetch here seeds them.
        // Every configured remote is re-checked on entry, and its
        // badge pulses until its own answer arrives. Painting a
        // day-old cached green on open presents stale evidence as
        // current fact; painting orange because the cache aged is a
        // colour change with nothing wrong behind it. Asking is the
        // only honest third option.
        //
        // CHAINED, not fired alongside: the badge read is an
        // AUTOMATIC read that PAUSES (returning no badge map at all)
        // when the container has live durable work, and the refresh
        // registers exactly that. Fired together the refresh won the
        // race, the badge read came back paused, and on a fresh hub
        // there is no previous map to fall back on -- so every
        // Published-copies row read "No files tracked for this remote
        // yet" until something bumped the sync epoch. Observed on a
        // real project, 2026-08-30.
        _fnSeedBadgesThenAskTheRemotes(sId);
        VaibifyPipelineRunner.fnRecoverPipelineState(sId);
        fnLoadContainerSettings();
    }

    function _fnSeedBadgesThenAskTheRemotes(sId) {
        // ONE chain, three links: badges, then readiness, then the
        // remote refresh. Badges first so the first paint is never an
        // empty map; the remote checks LAST, and their own completion
        // bumps the sync epoch, which repaints the badges with what
        // they found.
        //
        // The readiness request sits BETWEEN them because its lock
        // probe is an automatic read that PAUSES rather than queues
        // -- fired alongside the other open-time requests it lost
        // the carrier race on every open, came back "paused", and
        // the Dependency-lock verdict was never measured on a real
        // open at all (the "container probe 0.01s" line). Note the
        // remote refresh RETURNS before its background checks
        // finish, so "await the refresh, then ask readiness" would
        // not serialize anything; starting the refresh after
        // readiness answers is the order that does.
        if (typeof VaibifyGitBadges === "undefined") {
            fnResolveLockSatisfactionBeforeFirstPaint(sId)
                .then(function () {
                    return VaibifyWorkflowManager.fnCheckOriginDrift(
                        sId, false);
                })
                .then(function () {
                    VaibifySyncManager.fnRefreshConfiguredRemotes(sId);
                });
            return;
        }
        VaibifyGitBadges.fnRefresh(sId).then(function () {
            // The Project block holds its first paint until this
            // answers, because a row it renders is derived from the
            // verdict; the resolver never rejects.
            return fnResolveLockSatisfactionBeforeFirstPaint(sId);
        }).then(function () {
            // The origin-drift check opens a lock-held git-fetch
            // carrier. Fired from fnSelectWorkflow it raced this
            // chain and held the carrier at exactly the moment the
            // readiness probe asked for the drain -- the log's
            // "PAUSED (carrier held by helper on git-fetch)", first
            // seen the same evening the badge/remote race was fixed.
            // Ordering is this chain's ONE job, so the check moved
            // into it.
            return VaibifyWorkflowManager.fnCheckOriginDrift(
                sId, false);
        }).then(function () {
            VaibifySyncManager.fnRefreshConfiguredRemotes(sId);
        });
    }

    function _fnSurfaceStateLoadNotice(dictWorkflow) {
        if (!dictWorkflow) return;
        var dictNotice = dictWorkflow.dictStateLoadNotice;
        if (!dictNotice || !dictNotice.sMessage) return;
        var sLevel = dictNotice.sLevel || "warning";
        fnShowToast(dictNotice.sMessage, sLevel);
        delete dictWorkflow.dictStateLoadNotice;
    }

    function fnRefreshWorkflowData(dictData) {
        _dictWorkflowState.dictWorkflow = dictData.dictWorkflow;
        _dictWorkflowState.sWorkflowPath = dictData.sWorkflowPath;
        _dictWorkflowState.sAcknowledgedSourceFingerprint =
            (dictData.dictWorkflow || {})._sSourceFingerprint ||
            _dictWorkflowState.sAcknowledgedSourceFingerprint;
        if (typeof dictData.iWorkflowEpoch === "number") {
            _dictWorkflowState.iWorkflowEpoch =
                dictData.iWorkflowEpoch;
        }
        // Adopt the fresh compare-and-swap baseline immediately so the
        // very next edit is validated against current state, not the
        // stale fingerprint that provoked the reload.
        if (typeof dictData.sWorkflowFingerprint === "string") {
            _dictWorkflowState.sWorkflowFingerprint =
                dictData.sWorkflowFingerprint;
        }
        _fnClearFileCaches();
        _fnInvalidateAllRenderCaches();
        fnRenderStepList();
        fnPollAllStepFiles();
    }

    function _fnApplyOutOfBandWorkflowReload(
        dictWorkflowNew, iWorkflowEpoch
    ) {
        var iPriorSelected = _dictUiState.iSelectedStepIndex;
        var dictPriorExpanded = _fdictSnapshotExpansionSets();
        _dictWorkflowState.dictWorkflow = dictWorkflowNew;
        if (typeof iWorkflowEpoch === "number") {
            _dictWorkflowState.iWorkflowEpoch = iWorkflowEpoch;
        }
        _fnClearFileCaches();
        _fnInvalidateAllRenderCaches();
        fnRenderStepList();
        var iStepCount = (dictWorkflowNew.listSteps || []).length;
        _fnRestoreUiSelection(iPriorSelected, iStepCount);
        _fnRestoreExpansionSets(dictPriorExpanded, iStepCount);
        fnRenderStepList();
        /* Acknowledge only AFTER the replacement rendered: if the
           apply had thrown above, the next dispatch must stay
           refused rather than vouch for a workflow nobody saw. */
        _dictWorkflowState.sAcknowledgedSourceFingerprint =
            dictWorkflowNew._sSourceFingerprint ||
            _dictWorkflowState.sAcknowledgedSourceFingerprint;
        fnShowToast(
            "Project definition reloaded from disk", "info");
    }

    function _fdictSnapshotExpansionSets() {
        return {
            setSteps: new Set(_dictUiState.setExpandedSteps),
            setDeps: new Set(_dictUiState.setExpandedDeps),
            setQualitative: new Set(
                _dictUiState.setExpandedQualitative),
            setQuantitative: new Set(
                _dictUiState.setExpandedQuantitative),
            setIntegrity: new Set(_dictUiState.setExpandedIntegrity),
        };
    }

    function _fnRestoreUiSelection(iPriorSelected, iStepCount) {
        if (iPriorSelected >= 0 && iPriorSelected < iStepCount) {
            _dictUiState.iSelectedStepIndex = iPriorSelected;
        } else {
            _dictUiState.iSelectedStepIndex = -1;
        }
    }

    function _fnRestoreExpansionSets(dictPrior, iStepCount) {
        var listPairs = [
            [_dictUiState.setExpandedSteps, dictPrior.setSteps],
            [_dictUiState.setExpandedDeps, dictPrior.setDeps],
            [_dictUiState.setExpandedQualitative,
             dictPrior.setQualitative],
            [_dictUiState.setExpandedQuantitative,
             dictPrior.setQuantitative],
            [_dictUiState.setExpandedIntegrity,
             dictPrior.setIntegrity],
        ];
        for (var iPair = 0; iPair < listPairs.length; iPair++) {
            _fnCopyIndicesWithinRange(
                listPairs[iPair][0], listPairs[iPair][1], iStepCount);
        }
    }

    function _fnCopyIndicesWithinRange(setLive, setPrior, iStepCount) {
        setLive.clear();
        setPrior.forEach(function (iIndex) {
            if (iIndex >= 0 && iIndex < iStepCount) {
                setLive.add(iIndex);
            }
        });
    }

    function _fnClearFileCaches() {
        _dictWorkflowState.dictFileExistenceCache = {};
        _dictWorkflowState.dictFileModTimes = {};
        _dictWorkflowState.dictOutputMtimes = {};
        _dictWorkflowState.dictPlotMtimes = {};
        _dictWorkflowState.dictMaxDataMtimeByStep = {};
        _dictWorkflowState.dictMaxInputMtimeByStep = {};
        _dictWorkflowState.dictMarkerMtimeByStep = {};
        _dictWorkflowState.dictTestSourceMtimeByStep = {};
        _dictWorkflowState.dictTestCategoryMtimes = {};
        _dictWorkflowState.dictPlotStandardExists = {};
        _fnClearBlockerAndLevelState();
    }

    function _fnClearBlockerAndLevelState() {
        _dictWorkflowState.dictBlockersByStep = {};
        _dictWorkflowState.dictBlockersByStepLevel2 = {};
        _dictWorkflowState.dictBlockersByStepLevel3 = {};
        _dictWorkflowState.dictStepLevels = {};
        _dictWorkflowState.dictStepLevelHighWater = {};
        _dictWorkflowState.dictStepLevelWarnings = {};
        _dictWorkflowState.dictWorkflowScopeLevels = null;
        _dictWorkflowState.dictWorkflowLevelHighWater = {};
        _dictWorkflowState.dictWorkflowEnvelopeDetail = null;
        _dictWorkflowState.dictRemoteChecks = {};
        _dictWorkflowState.iL1BlockerCount = 0;
        _dictWorkflowState.iL2BlockerCount = 0;
        _dictWorkflowState.iL3BlockerCount = 0;
        _dictWorkflowState.iCachedProofLevel = null;
    }

    async function _fdictConnectReclaimingOnce(sId) {
        /* Opening a Blank Project meets the same reaped-claim refusal
           the workflow picker does, and its message names a control --
           the project tile -- that is one screen away. Recover the way
           the picker does rather than print an instruction the
           researcher cannot follow from here; a reclaim the server
           refuses rethrows, and the caller reports it. */
        try {
            return await VaibifyApi.fdictPostRaw("/api/connect/" + sId);
        } catch (error) {
            if (!await VaibifyContainerManager.fbReclaimAfterLostClaim(
                error
            )) throw error;
            return await VaibifyApi.fdictPostRaw("/api/connect/" + sId);
        }
    }

    async function fnEnterNoWorkflow(sId) {
        try {
            var dictConnect = await _fdictConnectReclaimingOnce(sId);
            _fnRecordViewerLeaseFromConnect(sId, dictConnect);
            _fnResetWorkflowState();
            _dictSessionState.sContainerId = sId;
            _dictSessionState.dictDashboardMode = DICT_MODE_NO_WORKFLOW;
            _fnRenderActiveResource(dictConnect.sProjectMode);
            fnApplyProjectMode(dictConnect.sProjectMode);
            fnApplyRemoteSession(
                dictConnect.bRemoteSession,
                dictConnect.sExecutionHostname);
            fnApplyExecutionTopology(
                dictConnect.dictExecutionTopology);
            fnApplyWorkspaceRoot(dictConnect.sWorkspaceRoot);
            VaibifyWebSocket.fnSetReconnectWindowSeconds(
                dictConnect.fReconnectWindowSeconds);
            _fnRenderToolkitBanner(0);
            document.title = VaibifyContainerManager.fsGetSelectedContainerName() || "Vaibify";
            fnShowMainLayout();
        } catch (error) {
            VaibifyDiagnosis.fnReportFailureFromError(error);
            return;
        }
        /* The layout is on screen from here: a failure below leaves
           the researcher INSIDE a Blank Project whose panels did not
           all come up, so it is reported as that rather than as the
           open having failed. */
        try {
            VaibifyTerminal.fnEnsureTab();
            await VaibifyReposPanel.fnInit(sId);
            VaibifyProofTab.fnSetContainerId(sId);
            // The Blank Project convenes too (2026-08-22). The backend
            // resolves its directory from the tracked-repos sidecar, so
            // a council needs no open workflow — but the panel is
            // activated from _fnActivateWorkflow, so without this line
            // it has no container id here and every click answers "open
            // this project", which is the state the researcher is
            // already in.
            if (typeof VaibifyAgentCouncil !== "undefined") {
                VaibifyAgentCouncil.fnActivate(sId);
            }
            VaibifyPolling.fnStartDiscoveryPolling(sId);
            VaibifyPolling.fnStartFileTreePolling();
        } catch (error) {
            VaibifyDiagnosis.fnReportFailure(
                "The Blank Project opened, but part of its dashboard " +
                "did not set up: " +
                fsSanitizeErrorForUser(error.message) +
                " Use Back and open it again.");
        }
    }

    function _fnRenderToolkitBanner(iAvailable) {
        /* TEXT, never markup. This banner used to write an <a> into
           the span, which took the browser's default link colour --
           dark blue on the dark toolbar, illegible -- because no
           stylesheet rule ever named its class. The span it sits in
           already carries the toolbar font, the "\u25BE" ::before
           chevron and the click binding that opens the switcher, so
           the anchor contributed a second chevron, a second toggle
           that fetched the project list twice, and the one thing the
           researcher could not read. Anything rendered here must
           inherit .toolbar-workflow rather than introduce an element
           with styling of its own. */
        var elName = document.getElementById("activeWorkflowName");
        if (!elName) return;
        elName.textContent = iAvailable > 0
            ? "None \u2014 " + iAvailable + " available"
            : "None";
    }

    function fnProcessWorkflowDiscovery(dictResponse) {
        if (!dictResponse) return;
        var listAvailable = dictResponse.listAvailableWorkflows || [];
        if (_dictSessionState.dictDashboardMode === DICT_MODE_NO_WORKFLOW) {
            _fnRenderToolkitBanner(listAvailable.length);
        }
        if (dictResponse.dictProjectCreationRequest) {
            _fnHandleAgentProjectCreationRequest(
                dictResponse.dictProjectCreationRequest);
        }
        if (!dictResponse.bWorkflowsChanged) return;
        var listNew = dictResponse.listNewWorkflowPaths || [];
        if (listNew.length === 0) return;
        _fnToastNewWorkflowsAppeared(listNew, listAvailable);
    }

    function _fnHandleAgentProjectCreationRequest(dictRequest) {
        fnShowToast(
            "Your agent asked to start a new Project. Creating one "
            + "is your call — review and confirm in the dialog.",
            "info");
        VaibifyNewWorkflowWizard.fnLaunch(
            _dictSessionState.sContainerId, dictRequest);
    }

    function _fnToastNewWorkflowsAppeared(listNewPaths, listAvailable) {
        var dictByPath = {};
        listAvailable.forEach(function (dictWf) {
            dictByPath[dictWf.sPath] = dictWf;
        });
        listNewPaths.forEach(function (sPath) {
            var dictWf = dictByPath[sPath];
            if (!dictWf) return;
            fnShowToast(
                "New project available: " + dictWf.sName
                + ". Click to load.",
                "info",
                function () {
                    VaibifyWorkflowManager.fnSelectWorkflow(
                        _dictSessionState.sContainerId,
                        dictWf.sPath, dictWf.sName,
                        dictWf.iSizeBytes || 0);
                }
            );
        });
    }

    function fnReorderLeftTabs(listVisibleTabs) {
        var elTabBar = document.getElementById("leftPanelTabs");
        if (!elTabBar) return;
        listVisibleTabs.forEach(function (sPanel) {
            var elTab = elTabBar.querySelector(
                '.left-tab[data-panel="' + sPanel + '"]'
            );
            if (elTab) elTabBar.appendChild(elTab);
        });
    }

    function fnReorderLeftPanels(listVisibleTabs) {
        var elResizeHandle = document.querySelector(
            "#panelLeft > .resize-handle-horizontal"
        );
        if (!elResizeHandle) return;
        var elPanelLeft = elResizeHandle.parentElement;
        listVisibleTabs.forEach(function (sPanel) {
            var sId = "panel" + sPanel.charAt(0).toUpperCase()
                + sPanel.slice(1);
            var elPanel = document.getElementById(sId);
            if (elPanel) {
                elPanelLeft.insertBefore(elPanel, elResizeHandle);
            }
        });
    }

    function fnApplyToolbarVisibility(dictMode) {
        var elRunMenu = document.getElementById("toolbarMenuRun");
        if (elRunMenu) elRunMenu.style.display =
            dictMode.bShowRunMenu ? "" : "none";
        var elDagButton = document.getElementById("btnShowDag");
        if (elDagButton) elDagButton.style.display =
            dictMode.bShowDagButton ? "" : "none";
    }

    function fnApplyDashboardMode() {
        if (!_dictSessionState.dictDashboardMode) return;
        var listLeftTabs = _dictSessionState.dictDashboardMode.listLeftTabs;
        var listAllTabs = document.querySelectorAll(".left-tab");
        listAllTabs.forEach(function (elTab) {
            var bVisible = listLeftTabs.includes(elTab.dataset.panel);
            elTab.style.display = bVisible ? "" : "none";
        });
        fnReorderLeftTabs(listLeftTabs);
        fnReorderLeftPanels(listLeftTabs);
        var elDefaultTab = document.querySelector(
            '.left-tab[data-panel="' +
            _dictSessionState.dictDashboardMode.sDefaultLeftTab + '"]'
        );
        if (elDefaultTab) elDefaultTab.click();
        fnApplyToolbarVisibility(_dictSessionState.dictDashboardMode);
    }

    function fnShowContainerLanding() {
        var sActiveName = VaibifyContainerManager
            .fsGetSelectedContainerName();
        if (sActiveName) {
            VaibifyContainerManager.fnReleaseClaim(sActiveName);
        }
        document.getElementById("containerLanding").style.display = "flex";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.remove("active");
        _dictSessionState.dictDashboardMode = null;
        document.getElementById("activeContainerName").textContent = "";
        fnApplyProjectMode("");
        document.getElementById("activeWorkflowName").textContent = "";
        document.title = "Vaibify";
        _fnStopWorkflowHubPolling();
        _fnStartContainerHubPolling();
    }

    function _fnApplyBlankProjectLocation() {
        /* "Work directly in the container" is the one line on this
           screen that names a place, and a host project does not have
           that place. Rewritten from the mode the server declared
           rather than from anything the picker infers. */
        var elLocation = document.getElementById("blankProjectLocation");
        if (!elLocation) return;
        elLocation.textContent =
            _dictSessionState.sProjectMode === "host"
                ? "Work directly in the project directory"
                : "Work directly in the container";
    }

    function fnShowWorkflowPicker(sContainerName) {
        _fnApplyBlankProjectLocation();
        document.getElementById("containerLanding").style.display = "none";
        document.getElementById("workflowPicker").style.display = "flex";
        document.getElementById("mainLayout").classList.remove("active");
        document.title = sContainerName || "Vaibify";
        _fnStopContainerHubPolling();
        var sContainerId = VaibifyContainerManager
            .fsGetSelectedContainerId();
        _fnStartWorkflowHubPolling(sContainerId);
    }

    function fnShowMainLayout() {
        document.getElementById("containerLanding").style.display = "none";
        document.getElementById("workflowPicker").style.display = "none";
        document.getElementById("mainLayout").classList.add("active");
        fnApplyDashboardMode();
        _fnStopContainerHubPolling();
        _fnStopWorkflowHubPolling();
    }

    function _fnStartContainerHubPolling() {
        VaibifyPolling.fnSetContainerHubHandler(
            _fnPollContainerHubIfIdle);
        VaibifyPolling.fnStartContainerHubPolling();
    }

    async function _fnPollContainerHubIfIdle() {
        if (_fbContainerHubHasOpenMenu()) return;
        await VaibifyContainerManager.fnRefreshContainerHub();
    }

    function _fbContainerHubHasOpenMenu() {
        var listMenus = document.querySelectorAll(
            ".container-tile-menu");
        for (var i = 0; i < listMenus.length; i++) {
            if (listMenus[i].style.display !== "none") return true;
        }
        return false;
    }

    function _fnStopContainerHubPolling() {
        VaibifyPolling.fnStopContainerHubPolling();
    }

    function _fnStartWorkflowHubPolling(sContainerId) {
        if (!sContainerId) {
            _fnStopWorkflowHubPolling();
            return;
        }
        VaibifyPolling.fnSetWorkflowHubHandler(function () {
            return _fnRefreshWorkflowHubList(sContainerId);
        });
        VaibifyPolling.fnStartWorkflowHubPolling();
    }

    function _fnStopWorkflowHubPolling() {
        VaibifyPolling.fnStopWorkflowHubPolling();
    }

    async function _fnRefreshWorkflowHubList(sContainerId) {
        var listWorkflows = await VaibifyApi.fdictGet(
            "/api/workflows/" + encodeURIComponent(sContainerId));
        VaibifyWorkflowManager.fnRenderWorkflowList(
            listWorkflows, sContainerId);
    }

    function fnStopAllHubPolling() {
        _fnStopContainerHubPolling();
        _fnStopWorkflowHubPolling();
    }

    function fnResumeHubPollingForCurrentView() {
        var elLanding = document.getElementById("containerLanding");
        var elPicker = document.getElementById("workflowPicker");
        if (elLanding && elLanding.style.display === "flex") {
            _fnStartContainerHubPolling();
            return;
        }
        if (elPicker && elPicker.style.display === "flex") {
            var sContainerId = VaibifyContainerManager
                .fsGetSelectedContainerId();
            _fnStartWorkflowHubPolling(sContainerId);
        }
    }

    function _fnCancelAllTimers() {
        VaibifyWebSocket.fnDisconnect();
        VaibifyPolling.fnStopPipelinePolling();
        VaibifyPolling.fnStopFilePolling();
        VaibifyPolling.fnStopFileTreePolling();
        VaibifyPolling.fnStopDiscoveryPolling();
        VaibifyPolling.fnStopPromptRecordPolling();
        VaibifyReposPanel.fnTeardown();
        if (_dictWorkflowState.iFileCheckTimer) {
            clearTimeout(_dictWorkflowState.iFileCheckTimer);
            _dictWorkflowState.iFileCheckTimer = null;
        }
        if (_dictWorkflowState.abortControllerFileCheck) {
            _dictWorkflowState.abortControllerFileCheck.abort();
            _dictWorkflowState.abortControllerFileCheck = null;
        }
        VaibifyPipelineRunner.fnCancelSentinelMonitor();
    }

    function fnDisconnect() {
        _dictSessionState.sContainerId = null;
        _dictWorkflowState.dictWorkflow = null;
        _dictWorkflowState.sWorkflowPath = null;
        _dictUiState.iSelectedStepIndex = -1;
        _dictUiState.setExpandedSteps.clear();
        _dictUiState.setExpandedDeps.clear();
        _dictUiState.setExpandedStepLevels.clear();
        _dictUiState.setLevelSeededSteps.clear();
        _dictUiState.setExpandedStepDescriptions.clear();
        _dictUiState.setDescriptionSeededSteps.clear();
        VaibifyTestManager.fnResetState();
        _dictWorkflowState.dictPlotStandardExists = {};
        _dictWorkflowState.dictStepStatus = {};
        _dictWorkflowState.dictStepTaint = {};
        document.body.classList.remove(
            "proof-level-1", "proof-level-2", "proof-level-3",
        );
        _fnCancelAllTimers();
        VaibifyFigureViewer.fnReleaseResources();
        VaibifyTerminal.fnCloseAll();
        fnShowContainerLanding();
        VaibifyContainerManager.fnLoadContainers();
    }

    /* --- Template Resolution --- */

    function fdictBuildClientVariables() {
        if (!_dictWorkflowState.dictWorkflow) return {};
        var sWorkflowDir = fsGetWorkflowDirectory();
        var sRepoRoot = sWorkflowDir;
        if (sRepoRoot.endsWith("/.vaibify/projects")) {
            sRepoRoot = sRepoRoot.replace(
                "/.vaibify/projects", "");
        } else if (sRepoRoot.endsWith("/.vaibify/workflows")) {
            sRepoRoot = sRepoRoot.replace(
                "/.vaibify/workflows", "");
        } else if (sRepoRoot.endsWith("/.vaibify")) {
            sRepoRoot = sRepoRoot.replace("/.vaibify", "");
        }
        var sPlotDir = _dictWorkflowState.dictWorkflow.sPlotDirectory || "Plot";
        if (sPlotDir.charAt(0) !== "/") {
            sPlotDir = sRepoRoot + "/" + sPlotDir;
        }
        var dictVars = {
            sPlotDirectory: sPlotDir,
            sRepoRoot: sRepoRoot,
            iNumberOfCores: _dictWorkflowState.dictWorkflow.iNumberOfCores || -1,
            sFigureType: (_dictWorkflowState.dictWorkflow.sFigureType || "pdf").toLowerCase(),
        };
        _dictWorkflowState.dictWorkflow.listSteps.forEach(function (step, iIdx) {
            var sStepDir = step.sDirectory || "";
            var iNum = iIdx + 1;
            var sPrefix = "Step" + String(iNum).padStart(2, "0");
            var listFiles = (step.saOutputDataFiles || []).concat(
                step.saPlotFiles || []);
            listFiles.forEach(function (sFile) {
                var sResolved = sFile.replace(
                    /\{([^}]+)\}/g, function (m, t) {
                        return dictVars[t] || m;
                    });
                if (sResolved.charAt(0) !== "/") {
                    sResolved = sStepDir + "/" + sResolved;
                }
                var sBase = sResolved.split("/").pop();
                var sStem = sBase.replace(/\.[^.]+$/, "");
                dictVars[sPrefix + "." + sStem] = sResolved;
            });
        });
        return dictVars;
    }

    var fsResolveTemplate = VaibifyUtilities.fsResolveTemplate;

    function fsJoinPath(sDirectory, sFilename) {
        if (!sDirectory || sDirectory === ".") {
            return sFilename;
        }
        if (sDirectory.endsWith("/")) {
            return sDirectory + sFilename;
        }
        return sDirectory + "/" + sFilename;
    }

    function fsShortenPath(sResolved, sWorkdir) {
        if (!sWorkdir || !sResolved) return sResolved;
        var sPrefix = sWorkdir.endsWith("/") ?
            sWorkdir : sWorkdir + "/";
        if (sResolved.startsWith(sPrefix)) {
            return sResolved.substring(sPrefix.length);
        }
        return sResolved;
    }

    function fsGetWorkflowDirectory() {
        if (!_dictWorkflowState.sWorkflowPath) {
            return _dictSessionState.sWorkspaceRoot;
        }
        var iLastSlash = _dictWorkflowState.sWorkflowPath.lastIndexOf("/");
        return iLastSlash > 0
            ? _dictWorkflowState.sWorkflowPath.substring(0, iLastSlash)
            : _dictSessionState.sWorkspaceRoot;
    }

    /* --- Global Settings --- */

    function fsSettingsRowHtml(sLabel, sInputHtml, sTooltip) {
        // Every settings row carries a hover tooltip on the whole
        // row (2026-07-18 ruling) so a researcher never has to
        // guess what an option controls.
        return '<div class="gs-row"' +
            (sTooltip
                ? ' title="' + fnEscapeHtml(sTooltip) + '"'
                : '') + '>' +
            '<span class="gs-label">' + sLabel + '</span>' +
            sInputHtml + '</div>';
    }

    function fsGlobalSettingsHtml() {
        var iToleranceExp = fsToleranceToExponent(
            _dictWorkflowState.dictWorkflow.fTolerance || 1e-6);
        return fsSettingsRowHtml("Plot Dir",
            '<input class="gs-input" id="gsPlotDirectory" value="' +
            fnEscapeHtml(_dictWorkflowState.dictWorkflow.sPlotDirectory || "Plot") + '">',
            "Subdirectory of each step where its figures are " +
            "written; commands reference it with the " +
            "{sPlotDirectory} token") +
            fsSettingsRowHtml("Figure Type",
            '<input class="gs-input" id="gsFigureType" value="' +
            fnEscapeHtml(_dictWorkflowState.dictWorkflow.sFigureType || "pdf") + '">',
            "File format for generated figures (for example pdf " +
            "or png); commands reference it with the " +
            "{sFigureType} token") +
            fsSettingsRowHtml("Cores",
            '<input class="gs-input" id="gsNumberOfCores" type="number" value="' +
            (_dictWorkflowState.dictWorkflow.iNumberOfCores || -1) + '">',
            "CPU cores available to parallel work inside the " +
            "container; -1 means all cores minus one") +
            fsSettingsRowHtml("Tolerance",
            '<input class="gs-input" id="gsTolerance" type="range"' +
            ' min="-16" max="0" step="1" value="' + iToleranceExp +
            '" title="10^' + iToleranceExp +
            ' = ' + (_dictWorkflowState.dictWorkflow.fTolerance || 1e-6) + '">',
            "How closely a quantitative test result must match its " +
            "standard to pass; the slider sets a power of ten — " +
            "hover the slider for the current value") +
            fsSettingsRowHtml("Poll Interval",
            '<input class="gs-input" id="gsPollInterval" type="range"' +
            ' min="1" max="60" value="' +
            (VaibifyPolling.fiGetPollIntervalMs() / 1000) +
            '" title="' +
            (VaibifyPolling.fiGetPollIntervalMs() / 1000) +
            ' seconds">',
            "How often the dashboard refreshes file and status " +
            "information from the container, in seconds — hover " +
            "the slider for the current value") +
            fsSettingsRowHtml("Show timestamps",
            '<input type="checkbox" id="gsShowTimestamps"' +
            (_dictUiState.bShowTimestamps ? " checked" : "") + '>',
            "Show file-modification timestamps in each step's " +
            "expanded detail") +
            fsSettingsRowHtml("Terminal lines",
            '<input id="gsTerminalScrollback" class="gs-input-local"' +
            ' type="number" min="100"' +
            ' value="' + VaibifyTerminal.fiGetScrollback() + '"' +
            (VaibifyTerminal.fbScrollbackIsUnlimited()
                ? " disabled" : "") +
            ' title="Lines of terminal scrollback to retain (min 100)">' +
            ' <label class="gs-inline-check" title="Retain up to' +
            ' 1,000,000 lines — effectively unlimited; protects' +
            ' browser memory"><input type="checkbox"' +
            ' id="gsTerminalScrollbackUnlimited"' +
            (VaibifyTerminal.fbScrollbackIsUnlimited()
                ? " checked" : "") + '> &#8734;</label>',
            "How many lines of output each terminal tab keeps; " +
            "the &#8734; checkbox retains effectively unlimited " +
            "scrollback") +
            fsSettingsRowHtml("Auto Archive",
            '<input type="checkbox" id="gsAutoArchive"' +
            (_dictWorkflowState.dictWorkflow.bAutoArchive
                ? " checked" : "") +
            ' title="Push verified files to Overleaf/Zenodo automatically">',
            "Push verified files to Overleaf and Zenodo " +
            "automatically after each successful verification") +
            fsSettingsRowHtml("Runtime limit (s)",
            '<input class="gs-input" id="gsDefaultWallClockBudget"' +
            ' type="number" min="0" step="1" value="' +
            (_dictWorkflowState.dictWorkflow
                .fDefaultWallClockBudgetSeconds || 0) + '"' +
            ' title="Default expected runtime in seconds applied to' +
            ' every step without its own value. A step that runs longer' +
            ' turns its run light red as a possibly-hung warning; the' +
            ' run is never stopped. 0 = no limit.">',
            "Advisory runtime limit in seconds inherited by every " +
            "step without its own (right-click a step to set one); " +
            "a longer-running step is flagged as possibly hung — " +
            "the run is never stopped. 0 = no limit") +
            fsAgentSettingsHtml();
    }

    /* The two host-global timeouts a researcher can set, and the whole
       difference between them, because setting the wrong one and
       waiting twelve hours to find out is the bug this pair exists to
       end. IDLE SHUTDOWN retires the hub PROCESS when nobody is
       looking; an open dashboard vetoes it, so a researcher sitting at
       the screen never meets it. SESSION LIFETIME is the browser
       credential's absolute cap, measured from when the tab was
       minted, and it has NO such veto by design -- a forgotten-open
       tab is exactly the case it exists to bound -- so it is the one
       that ends a session somebody is using. They share a response
       shape and a "never" vocabulary because the backend already
       resolves all three tiers through one parser; a second copy of
       this control would be where the two start to disagree about what
       never means.

       They live in the TOOLBAR gear, not the project gear. Both are
       properties of this computer rather than of a project, and the
       project gear sits in a panel that Blank Project mode does not
       render -- which left the researcher whose blank-project session
       had just timed out unable to reach the control that prevents
       it. */
    var _LIST_TIMEOUT_SETTINGS = [
        {
            sElementId: "gsIdleTimeout",
            sEndpoint: "/api/preferences/idle-timeout",
            sLabel: "Idle shutdown",
            sEnvironmentName: "VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS",
            listChoices: [
                ["never", "Never"], ["900", "15 minutes"],
                ["1800", "30 minutes"], ["3600", "1 hour"],
                ["7200", "2 hours"],
            ],
            sHelp: "When an idle hub with no connected dashboard and " +
                "no running pipeline retires itself. An open dashboard " +
                "always keeps it alive, so this is not what ends a " +
                "session you are using — that is Session lifetime " +
                "below. Never disables self-shutdown. Applies " +
                "immediately, no relaunch.",
        },
        {
            sElementId: "gsSessionCap",
            sEndpoint: "/api/preferences/session-cap",
            sLabel: "Session lifetime",
            sEnvironmentName: "VAIBIFY_ABSOLUTE_SESSION_CAP_SECONDS",
            listChoices: [
                ["never", "Never"], ["43200", "12 hours"],
                ["86400", "24 hours"], ["604800", "7 days"],
                ["1209600", "14 days"], ["2592000", "30 days"],
            ],
            sHelp: "How long one browser tab's credential lives, " +
                "counted from when the tab was opened and whether or " +
                "not you are using it. Reaching it ends the tab's " +
                "session — and with it the agent conversations that " +
                "session was holding; the container and any running " +
                "step keep going. You are warned at three quarters, " +
                "nine tenths and nineteen twentieths of the way " +
                "through, and each warning offers to restart the " +
                "clock. Never means the tab is never signed out. " +
                "Applies on the next check, no relaunch.",
        },
    ];

    function fsTimeoutSettingsRowsHtml() {
        // Host-global, each applied live through its own endpoint. The
        // gs-input-local class keeps these out of the .gs-input change
        // binding that PUTs container settings; each saves through the
        // handler bound in fnLoadTimeoutSetting.
        return _LIST_TIMEOUT_SETTINGS.map(fsTimeoutSettingRowHtml).join("");
    }

    function fsTimeoutSettingRowHtml(dictSetting) {
        var sOptions = dictSetting.listChoices.map(function (tChoice) {
            return '<option value="' + tChoice[0] + '">' +
                tChoice[1] + "</option>";
        }).join("");
        return fsSettingsRowHtml(dictSetting.sLabel,
            '<select class="gs-input-local" id="' +
            dictSetting.sElementId + '">' + sOptions + '</select>' +
            '<span id="' + dictSetting.sElementId +
            'Note" class="gs-idle-note"></span>',
            dictSetting.sHelp);
    }

    function fsAgentSettingsHtml() {
        var dictSettings = _dictWorkflowState.dictContainerSettings;
        if (!dictSettings) {
            return "";
        }
        var listAgents = [
            {sKey: "claude", sLabel: "Claude Code", sField: "bClaude"},
            {sKey: "codex", sLabel: "Codex", sField: "bCodex"},
            {sKey: "gemini", sLabel: "Gemini CLI", sField: "bGemini"},
            {sKey: "antigravity", sLabel: "Antigravity CLI",
             sField: "bAntigravity"},
            {sKey: "opencode", sLabel: "OpenCode", sField: "bOpenCode"},
            {sKey: "cline", sLabel: "Cline", sField: "bCline"},
            {sKey: "openhands", sLabel: "OpenHands", sField: "bOpenHands"},
            {sKey: "pi", sLabel: "Pi", sField: "bPi"},
        ].filter(function (dictAgent) {
            var sInstalled = dictAgent.sField + "Installed";
            return dictSettings[sInstalled];
        });
        if (listAgents.length === 0) return "";
        var sNotice = _dictWorkflowState.bAgentRestartNeeded
            ? '<div class="gs-notice">Restart the container to '
              + 'apply the new agent auto-update setting.</div>'
            : "";
        return '<div class="gs-section-heading">Container</div>' +
            listAgents.map(_fsAgentAutoUpdateRow).join("") +
            sNotice;
    }

    function _fsAgentAutoUpdateRow(dictAgent) {
        var sSetting = dictAgent.sField + "AutoUpdate";
        var sChecked = _dictWorkflowState.dictContainerSettings[sSetting]
            ? " checked" : "";
        return fsSettingsRowHtml(dictAgent.sLabel + " auto-update",
            '<input type="checkbox" class="gs-agent-auto-update" '
            + 'data-agent="' + dictAgent.sKey + '"' + sChecked + '>',
            "Allow " + dictAgent.sLabel + " inside the container to " +
            "update itself; a change takes effect after a container " +
            "restart");
    }

    function fnBindSettingsSliders() {
        var elPollSlider = document.getElementById("gsPollInterval");
        if (elPollSlider) {
            elPollSlider.addEventListener("input", function () {
                fnSetPollInterval(parseInt(elPollSlider.value, 10));
            });
        }
        var elToleranceSlider = document.getElementById("gsTolerance");
        if (elToleranceSlider) {
            elToleranceSlider.addEventListener("input", function () {
                var iExp = parseInt(elToleranceSlider.value, 10);
                var fVal = Math.pow(10, iExp);
                elToleranceSlider.title =
                    "10^" + iExp + " = " + fVal;
                _dictWorkflowState.dictWorkflow.fTolerance = fVal;
            });
        }
        var elTimestampCheckbox = document.getElementById(
            "gsShowTimestamps");
        if (elTimestampCheckbox) {
            elTimestampCheckbox.addEventListener(
                "change", function () {
                    fnToggleShowTimestamps(
                        elTimestampCheckbox.checked);
                });
        }
        var elAutoArchive = document.getElementById("gsAutoArchive");
        if (elAutoArchive) {
            elAutoArchive.addEventListener(
                "change", fnSaveGlobalSettings);
        }
        document.querySelectorAll(".gs-agent-auto-update")
            .forEach(function (elAgentAutoUpdate) {
                elAgentAutoUpdate.addEventListener("change", function () {
                    fnSaveAgentAutoUpdate(
                        elAgentAutoUpdate.dataset.agent,
                        elAgentAutoUpdate.checked);
                });
            });
        fnBindTerminalScrollbackControls();
    }

    function fnApplyTerminalScrollbackSetting(elNum, elUnlimited) {
        var bUnlimited = elUnlimited.checked;
        elNum.disabled = bUnlimited;
        VaibifyTerminal.fnSetScrollback(
            parseInt(elNum.value, 10), bUnlimited);
        elNum.value = VaibifyTerminal.fiGetScrollback();
    }

    function fnBindTerminalScrollbackControls() {
        var elNum = document.getElementById("gsTerminalScrollback");
        var elUnlimited = document.getElementById(
            "gsTerminalScrollbackUnlimited");
        if (!elNum || !elUnlimited) return;
        elNum.addEventListener("change", function () {
            fnApplyTerminalScrollbackSetting(elNum, elUnlimited);
        });
        elUnlimited.addEventListener("change", function () {
            fnApplyTerminalScrollbackSetting(elNum, elUnlimited);
        });
    }

    async function fnLoadContainerSettings() {
        var sId = _dictSessionState.sContainerId;
        if (!sId) return;
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/containers/"
                + encodeURIComponent(sId) + "/settings");
            _dictWorkflowState.dictContainerSettings = dictResult;
            fnRenderGlobalSettings();
        } catch (error) {
            _dictWorkflowState.dictContainerSettings = null;
        }
    }

    function fnApplyAgentSaveResult(sAgent, bValue, dictResult) {
        if (_dictWorkflowState.dictContainerSettings) {
            _dictWorkflowState.dictContainerSettings[
                _fsAgentSettingsField(sAgent) + "AutoUpdate"
            ] = bValue;
        }
        _dictWorkflowState.bAgentRestartNeeded =
            Boolean(dictResult && dictResult.bRestartRequired);
        fnRenderGlobalSettings();
        fnShowToast(sAgent + " setting saved", "success");
    }

    async function fnSaveAgentAutoUpdate(sAgent, bValue) {
        var sId = _dictSessionState.sContainerId;
        if (!sId) return;
        var dictPayload = {};
        dictPayload[_fsAgentSettingsField(sAgent) + "AutoUpdate"] = bValue;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/containers/"
                + encodeURIComponent(sId) + "/settings",
                dictPayload);
            fnApplyAgentSaveResult(sAgent, bValue, dictResult);
        } catch (error) {
            fnShowToast(
                "Failed to save " + sAgent + " setting", "error");
        }
    }

    function _fsAgentSettingsField(sAgent) {
        var dictFields = {
            claude: "bClaude", codex: "bCodex", gemini: "bGemini",
            antigravity: "bAntigravity",
            opencode: "bOpenCode", cline: "bCline",
            openhands: "bOpenHands", pi: "bPi",
        };
        return dictFields[sAgent];
    }

    function fnRenderGlobalSettings() {
        if (!_dictWorkflowState.dictWorkflow) return;
        var el = document.getElementById("globalSettingsPanel");
        el.innerHTML = fsGlobalSettingsHtml();
        el.querySelectorAll(".gs-input").forEach(function (inp) {
            inp.addEventListener("change", fnSaveGlobalSettings);
        });
        fnBindSettingsSliders();
    }

    function fnRenderHostSettings() {
        /* The host-global timeouts, rendered into the toolbar gear.
           They are NOT in the project settings panel: that panel
           returns early without an open project, and its whole tab is
           hidden in Blank Project mode, so a researcher working
           directly in a container could not reach the control that
           decides when their session ends. Rendered on each open so
           the selects show what the server currently resolves rather
           than what it resolved when the tab loaded. */
        var el = document.getElementById("hostSettingsPanel");
        if (!el) return;
        el.innerHTML =
            '<div class="gs-section-heading">This computer</div>' +
            fsTimeoutSettingsRowsHtml() +
            '<div class="gs-section-heading">This tab</div>' +
            fsSettingsRowHtml("Session",
                '<span id="gsSessionRemaining" class="gs-idle-note">' +
                "Reading\u2026</span>" +
                '<button type="button" class="btn" ' +
                'id="btnRenewSession">Renew</button>',
                "How much of this tab's session lifetime is left, and " +
                "a button to restart the clock without losing the " +
                "page. The expiry warnings offer the same thing; this " +
                "row is where to find it once a warning has been " +
                "dismissed.");
        fnLoadTimeoutSettings();
        _fnLoadSessionRemaining();
        var elRenew = document.getElementById("btnRenewSession");
        if (elRenew) {
            elRenew.addEventListener("click", async function () {
                await _fnRenewSessionLifetime();
                _fnLoadSessionRemaining();
            });
        }
    }

    async function _fnLoadSessionRemaining() {
        /* The server's own countdown, read fresh each time the panel
           opens. A page that counted down on its own clock would drift
           and would keep counting after a hub restart replaced the
           session entirely. */
        var elRemaining = document.getElementById("gsSessionRemaining");
        if (!elRemaining) return;
        try {
            var dictLifetime = await VaibifyApi.fdictGet(
                "/api/session/lifetime");
            if (!dictLifetime || !dictLifetime.bSessionKnown) {
                elRemaining.textContent = "Not known";
                return;
            }
            elRemaining.textContent = dictLifetime.bNeverExpires
                ? "Never expires"
                : _fsDescribeRemainingLifetime(
                    dictLifetime.fSecondsUntilSessionCap) + " left";
        } catch (error) {
            elRemaining.textContent = "Could not be read";
        }
    }

    function fnLoadTimeoutSettings() {
        _LIST_TIMEOUT_SETTINGS.forEach(fnLoadTimeoutSetting);
    }

    async function fnLoadTimeoutSetting(dictSetting) {
        var elSelect = document.getElementById(dictSetting.sElementId);
        if (!elSelect) return;
        try {
            var dictInfo = await VaibifyApi.fdictGet(dictSetting.sEndpoint);
            fnApplyTimeoutSettingInfo(dictSetting, elSelect, dictInfo);
        } catch (error) {
            // Best-effort: leave the default option selected.
        }
        elSelect.addEventListener("change", function () {
            fnSaveTimeoutSetting(dictSetting);
        });
    }

    function fnApplyTimeoutSettingInfo(dictSetting, elSelect, dictInfo) {
        var sValue = dictInfo.bNever
            ? "never"
            : String(Math.round(dictInfo.fSeconds));
        fnEnsureTimeoutSettingOption(elSelect, sValue, dictInfo);
        elSelect.value = sValue;
        elSelect.disabled = Boolean(dictInfo.bEnvOverride);
        var elNote = document.getElementById(
            dictSetting.sElementId + "Note");
        if (elNote) {
            elNote.textContent = dictInfo.bEnvOverride
                ? "Pinned by " + dictSetting.sEnvironmentName + "; the "
                  + "environment overrides this control."
                : "";
        }
    }

    function fnEnsureTimeoutSettingOption(elSelect, sValue, dictInfo) {
        // The effective value (e.g. an env-pinned 60s) may not match a
        // preset; add a one-off option so the select shows the truth.
        if (elSelect.querySelector('option[value="' + sValue + '"]')) {
            return;
        }
        var elOption = document.createElement("option");
        elOption.value = sValue;
        elOption.textContent = dictInfo.bNever
            ? "Never"
            : Math.round(dictInfo.fSeconds) + " seconds";
        elSelect.appendChild(elOption);
    }

    async function fnSaveTimeoutSetting(dictSetting) {
        var elSelect = document.getElementById(dictSetting.sElementId);
        if (!elSelect) return;
        try {
            var dictInfo = await VaibifyApi.fdictPut(
                dictSetting.sEndpoint, {sValue: elSelect.value});
            fnApplyTimeoutSettingInfo(dictSetting, elSelect, dictInfo);
            fnShowToast(dictSetting.sLabel + " updated", "success");
        } catch (error) {
            fnShowToast(
                "Failed to update " + dictSetting.sLabel.toLowerCase(),
                "error");
        }
    }

    function fnToggleShowTimestamps(bEnabled) {
        _dictUiState.bShowTimestamps = bEnabled;
        try {
            localStorage.setItem(
                "vaibifyShowTimestamps",
                bEnabled ? "true" : "false");
        } catch (e) { /* localStorage may be unavailable */ }
        fnApplyTimestampVisibility();
        fnRenderStepList();
    }

    function fnApplyTimestampVisibility() {
        var elList = document.getElementById("listSteps");
        if (!elList) return;
        if (_dictUiState.bShowTimestamps) {
            elList.classList.remove("hide-timestamps");
        } else {
            elList.classList.add("hide-timestamps");
        }
    }

    function fnLoadTimestampSetting() {
        try {
            var sStored = localStorage.getItem(
                "vaibifyShowTimestamps");
            _dictUiState.bShowTimestamps = sStored === "true";
        } catch (e) { /* localStorage may be unavailable */ }
    }

    function _fsStepsCollapsedKey() {
        return "vaibify.stepsCollapsed." +
            (_dictWorkflowState.sWorkflowPath || "");
    }

    function _fsStepsAutoCollapsedKey() {
        return "vaibify.stepsAutoCollapsed." +
            (_dictWorkflowState.sWorkflowPath || "");
    }

    function _fnPersistStepsCollapsed(bCollapsed) {
        try {
            localStorage.setItem(
                _fsStepsCollapsedKey(), bCollapsed ? "1" : "0");
        } catch (e) { /* localStorage may be unavailable */ }
    }

    function _fnLoadStepsCollapsed() {
        // Default expanded on a fresh workflow (no stored choice).
        try {
            _dictUiState.bStepsCollapsed =
                localStorage.getItem(_fsStepsCollapsedKey()) === "1";
        } catch (e) {
            _dictUiState.bStepsCollapsed = false;
        }
    }

    function _fnMaybeAutoCollapseStepsOnFirstL1(iProofLevel) {
        // Collapse the Steps block the first time this workflow
        // reaches L1; a one-shot guard means the user's manual choice
        // wins thereafter. Uses the authoritative server level. When
        // localStorage is unavailable the one-shot cannot be tracked,
        // so auto-collapse is skipped rather than fired every poll.
        if (typeof iProofLevel !== "number" || iProofLevel < 1) return;
        var bAlready;
        try {
            bAlready =
                localStorage.getItem(_fsStepsAutoCollapsedKey()) === "1";
        } catch (e) {
            return;
        }
        if (bAlready) return;
        try {
            localStorage.setItem(_fsStepsAutoCollapsedKey(), "1");
            localStorage.setItem(_fsStepsCollapsedKey(), "1");
        } catch (e) {
            return;
        }
        _dictUiState.bStepsCollapsed = true;
    }

    function fsToleranceToExponent(fTolerance) {
        return Math.round(Math.log10(fTolerance));
    }

    async function fnSaveGlobalSettings() {
        var iExp = parseInt(
            document.getElementById("gsTolerance").value, 10);
        var elAutoArchive = document.getElementById("gsAutoArchive");
        var elBudget = document.getElementById(
            "gsDefaultWallClockBudget");
        var fBudget = elBudget ? parseFloat(elBudget.value) : 0;
        var dictUpdates = {
            sPlotDirectory: document.getElementById("gsPlotDirectory").value,
            sFigureType: document.getElementById("gsFigureType").value,
            iNumberOfCores: parseInt(
                document.getElementById("gsNumberOfCores").value
            ),
            fTolerance: Math.pow(10, iExp),
            bAutoArchive: elAutoArchive
                ? elAutoArchive.checked : false,
            fDefaultWallClockBudgetSeconds:
                (isNaN(fBudget) || fBudget < 0) ? 0 : fBudget,
        };
        try {
            var result = await VaibifyApi.fdictPut(
                "/api/settings/" + _dictSessionState.sContainerId, dictUpdates);
            _dictWorkflowState.dictWorkflow.sPlotDirectory = result.sPlotDirectory;
            _dictWorkflowState.dictWorkflow.sFigureType = result.sFigureType;
            _dictWorkflowState.dictWorkflow.iNumberOfCores = result.iNumberOfCores;
            if (result.fTolerance !== undefined) {
                _dictWorkflowState.dictWorkflow.fTolerance = result.fTolerance;
            }
            if (result.bAutoArchive !== undefined) {
                _dictWorkflowState.dictWorkflow.bAutoArchive =
                    result.bAutoArchive;
            }
            if (result.fDefaultWallClockBudgetSeconds !== undefined) {
                _dictWorkflowState.dictWorkflow
                    .fDefaultWallClockBudgetSeconds =
                    result.fDefaultWallClockBudgetSeconds;
            }
            fnShowToast("Settings saved", "success");
            fnRenderStepList();
        } catch (error) {
            fnShowToast("Failed to save settings", "error");
        }
    }

    /* --- Step List --- */

    function fdictBuildRenderContext() {
        return {
            dictStepStatus: _dictWorkflowState.dictStepStatus,
            dictStepTaint: _dictWorkflowState.dictStepTaint,
            iSelectedStepIndex: _dictUiState.iSelectedStepIndex,
            setExpandedSteps: _dictUiState.setExpandedSteps,
            setExpandedDeps: _dictUiState.setExpandedDeps,
            setExpandedRequirementGroups:
                _dictUiState.setExpandedRequirementGroups,
            setExpandedRequirementRows:
                _dictUiState.setExpandedRequirementRows,
            setToggledFileGroups:
                _dictUiState.setToggledFileGroups,
            bProjectBlockCollapsed: _dictUiState.bProjectBlockCollapsed,
            bBinaryAddFormOpen: _dictUiState.bBinaryAddFormOpen,
            sProjectRepoPath: (_dictWorkflowState.dictWorkflow || {})
                .sProjectRepoPath || "",
            setExpandedUnitTests: VaibifyTestManager.fsetGetExpandedUnitTests(),
            fdictGetFalsificationState:
                VaibifyTestManager.fdictGetFalsificationState,
            setStepsWithData: VaibifyTestManager.fsetGetStepsWithData(),
            setGeneratingInFlight: VaibifyTestManager.fsetGetGeneratingInFlight(),
            dictPlotStandardExists: _dictWorkflowState.dictPlotStandardExists,
            dictScriptModified: _dictWorkflowState.dictScriptModified,
            dictStaleArtifacts: _dictWorkflowState.dictStaleArtifacts,
            dictOutputMtimes: _dictWorkflowState.dictOutputMtimes,
            dictMaxDataMtimeByStep:
                _dictWorkflowState.dictMaxDataMtimeByStep,
            dictMaxInputMtimeByStep:
                _dictWorkflowState.dictMaxInputMtimeByStep,
            dictMaxPlotMtimeByStep: _dictWorkflowState.dictPlotMtimes,
            dictMarkerMtimeByStep:
                _dictWorkflowState.dictMarkerMtimeByStep,
            dictTestCategoryMtimes:
                _dictWorkflowState.dictTestCategoryMtimes,
            dictDiscoveredOutputs: _dictWorkflowState.dictDiscoveredOutputs,
            dictWorkflow: _dictWorkflowState.dictWorkflow,
            sUserName: _dictSessionState.sUserName,
            fsComputeStepLabel: fsComputeStepLabel,
            fsResolveTemplate: fsResolveTemplate,
            fsJoinPath: fsJoinPath,
            fsShortenPath: fsShortenPath,
            fsInitialFileStatusClass: fsInitialFileStatusClass,
            fsGetFileCategory: fsGetFileCategory,
            fdictGetVerification: fdictGetVerification,
            fdictGetTests: fdictGetTests,
            fsEffectiveTestState: fsEffectiveTestState,
            fsComputeDepsState: fsComputeDepsState,
            fsGetCategoryState: fsGetCategoryState,
            fsTestCategoryLabel: fsTestCategoryLabel,
            fsVerificationStateLabel: fsVerificationStateLabel,
            fsVerificationStateIcon: fsVerificationStateIcon,
            flistGetStepDependencies: flistGetStepDependencies,
            fbStepFullyPassing: fbStepFullyPassing,
            fbAnyUpstreamModified: fbAnyUpstreamModified,
            ftComputeDepAxisStates: ftComputeDepAxisStates,
            fsetGetExpandedCategory: fsetGetExpandedCategory,
            fdictBuildClientVariables: fdictBuildClientVariables,
            dictBlockersByStep: _dictWorkflowState.dictBlockersByStep,
            dictBlockersByStepLevel2:
                _dictWorkflowState.dictBlockersByStepLevel2,
            dictBlockersByStepLevel3:
                _dictWorkflowState.dictBlockersByStepLevel3,
            dictStepLevels: _dictWorkflowState.dictStepLevels,
            dictStepLevelHighWater:
                _dictWorkflowState.dictStepLevelHighWater,
            dictStepLevelWarnings:
                _dictWorkflowState.dictStepLevelWarnings,
            dictWorkflowEnvelopeDetail:
                _dictWorkflowState.dictWorkflowEnvelopeDetail,
            dictRemoteChecks:
                _dictWorkflowState.dictRemoteChecks,
            bProjectBlockAwaitsFirstAnswer:
                _dictWorkflowState.bProjectBlockAwaitsFirstAnswer,
            fbFileIsL1Offending: fbFileIsL1Offending,
            fbUpstreamStepIsL1Offending: fbUpstreamStepIsL1Offending,
            fsBuildL1FailureGlyph: fsBuildL1FailureGlyph,
            fsBuildFileMarkGlyph: fsBuildFileMarkGlyph,
            fsBlockerHintForStep: fsBlockerHintForStep,
            fsBlockerHintForFile: fsBlockerHintForFile,
            fsLevelCellState: fsLevelCellState,
            fsLevelCellTooltip: fsLevelCellTooltip,
            fdictLevelCellForScope: fdictLevelCellForScope,
            fbIsStepLevelExpanded: fbIsStepLevelExpanded,
            fbIsStepDescriptionExpanded: fbIsStepDescriptionExpanded,
            fdictRegressionWarning: fdictRegressionWarning,
        };
    }

    var _bRenderScheduled = false;

    // Per-step render memoization. _dictRenderedStepHashes maps
    // iIndex -> sHash, where sHash captures every input
    // fsRenderStepItem reads. On each render the loop skips steps
    // whose hash is unchanged and replaces only the changed cards in
    // place, instead of blowing away all 5K-20K DOM nodes for 100
    // steps every poll tick. Structural changes (step count delta,
    // interactive-boundary shift) trigger a full innerHTML rebuild.
    var _dictRenderedStepHashes = {};
    var _sLastBoundarySignature = null;

    // Change 6: dependency-graph memo. flistGetStepDependencies is
    // O(N) per step (directory-overlap probe against every prior
    // step), so the naive call site is O(N^2) per render. The memo
    // is cleared by _fnInvalidateAllRenderCaches on workflow swap.
    var _dictStepDepsByIndex = {};
    // Reverse index used by the badge-driven partial-render entry:
    // sFilePath -> iStep, so a change set of affected files can be
    // mapped to step indices in O(F) instead of O(N x F).
    var _dictStepIndexByFilePath = {};

    // Change 7: step-label memo. The legacy fallback in
    // fsComputeStepLabel walks 0..iIndex to count interactive vs
    // automated steps, which is O(N) per call. Build the entire
    // index in one forward pass on workflow load.
    var _dictStepLabelByIndex = {};

    function _fnInvalidateAllRenderCaches() {
        _dictRenderedStepHashes = {};
        _sLastBoundarySignature = null;
        _dictStepDepsByIndex = {};
        _dictStepIndexByFilePath = {};
        _dictStepLabelByIndex = {};
    }

    function _fnInvalidateRenderCache(iIndex) {
        delete _dictRenderedStepHashes[iIndex];
    }

    function fnRenderStepList() {
        if (_bRenderScheduled) return;
        _bRenderScheduled = true;
        requestAnimationFrame(function () {
            _bRenderScheduled = false;
            _fnRenderStepListImmediate();
        });
    }

    function fnRenderStepListSync() {
        _bRenderScheduled = false;
        _fnRenderStepListImmediate();
    }

    function _fsBoundarySignature(listSteps) {
        // Compact key that changes when the step count or the
        // automated/interactive/declaration boundary pattern shifts.
        // Used to detect "structural change" so the renderer falls
        // back to the full innerHTML rebuild (which re-emits the
        // column header + step-type banners) instead of only swapping
        // individual step wrappers.
        //
        // The Project block is NOT part of this signature: it
        // lives in its own container and is rebuilt on every render,
        // so its expansion Sets can never cause a skip-repaint. A
        // future maintainer who memoizes that block MUST fold
        // setExpandedRequirementGroups / setExpandedRequirementRows
        // into a signature of its own.
        var sKey = String(listSteps.length);
        for (var i = 0; i < listSteps.length; i++) {
            if (listSteps[i].sStepKind === "ai-declaration") {
                sKey += "D";
            } else {
                sKey += fbStepIsInteractive(listSteps[i]) ? "I" : "A";
            }
        }
        return sKey;
    }

    function _fsExpansionSliceForStep(iIndex, dictContext) {
        // Captures every UI-expansion bit that fsRenderStepItem
        // reads for this step. Each toggle handler mutates one of
        // these Sets and calls fnRenderStepList — if the bit is not
        // in the hash, the incremental path silently skips the
        // re-render and the user's click appears to do nothing.
        return (
            (iIndex === dictContext.iSelectedStepIndex ? "1" : "0")
            + (dictContext.setExpandedSteps.has(iIndex) ? "1" : "0")
            + (dictContext.setExpandedDeps.has(iIndex) ? "1" : "0")
            + (dictContext.setExpandedUnitTests.has(iIndex) ? "1" : "0")
            + (dictContext.setGeneratingInFlight.has(iIndex) ? "1" : "0")
            + (dictContext.setStepsWithData.has(iIndex) ? "1" : "0")
            + (dictContext.fsetGetExpandedCategory("qualitative").has(iIndex) ? "1" : "0")
            + (dictContext.fsetGetExpandedCategory("quantitative").has(iIndex) ? "1" : "0")
            + (dictContext.fsetGetExpandedCategory("integrity").has(iIndex) ? "1" : "0")
            + (dictContext.fbIsStepLevelExpanded(iIndex, 1) ? "1" : "0")
            + (dictContext.fbIsStepLevelExpanded(iIndex, 2) ? "1" : "0")
            + (dictContext.fbIsStepLevelExpanded(iIndex, 3) ? "1" : "0")
            + (dictContext.fbIsStepDescriptionExpanded(iIndex) ? "1" : "0")
        );
    }

    function _fsContextSliceForStep(iIndex, dictContext) {
        // Per-step slice of the context dicts that vary independently
        // of the step object itself. Anything fsRenderStepItem reads
        // off dictContext keyed by iIndex must be represented here or
        // the incremental renderer will leave a stale card on screen.
        var sIdx = String(iIndex);
        return JSON.stringify([
            dictContext.dictScriptModified[iIndex] || "",
            dictContext.dictStaleArtifacts[iIndex] || null,
            dictContext.dictDiscoveredOutputs[iIndex] || null,
            dictContext.dictOutputMtimes[sIdx] || "",
            dictContext.dictMarkerMtimeByStep[sIdx] || "",
            dictContext.dictMaxDataMtimeByStep[sIdx] || "",
            (dictContext.dictMaxInputMtimeByStep || {})[sIdx] || "",
            dictContext.dictMaxPlotMtimeByStep[sIdx] || "",
            dictContext.dictTestCategoryMtimes[sIdx] || null,
            dictContext.fdictGetFalsificationState ?
                dictContext.fdictGetFalsificationState(iIndex) : null,
        ].concat(_flistBlockerAndLevelSlice(iIndex, dictContext)));
    }

    function _flistBlockerAndLevelSlice(iIndex, dictContext) {
        // Blocker entries and level-cell inputs the card renders.
        // Without these, a poll that flips a blocker or a level cell
        // leaves a stale card on screen under the incremental path.
        var sIdx = String(iIndex);
        return [
            (dictContext.dictBlockersByStep || {})[iIndex] || null,
            (dictContext.dictBlockersByStepLevel2 || {})[iIndex] ||
                null,
            (dictContext.dictBlockersByStepLevel3 || {})[iIndex] ||
                null,
            (dictContext.dictStepLevels || {})[sIdx] || null,
            (dictContext.dictStepLevelHighWater || {})[sIdx] || null,
            (dictContext.dictStepLevelWarnings || {})[sIdx] || null,
        ];
    }

    function _fsDeclarationBadgeSlice(step) {
        // The declaration commit/remove buttons gate on the file's
        // git badge at render time; the hash must move when the
        // badge does, or the incremental path leaves the stale
        // commit-only card on screen (the same signature-omission
        // class as _fsExpansionSliceForStep documents).
        if (!step || step.sStepKind !== "ai-declaration") return "";
        var sFile = (step.sDeclarationFile || "").trim();
        if (!sFile || typeof VaibifyGitBadges === "undefined") {
            return "";
        }
        var dictBadges = VaibifyGitBadges.fdictGetBadgesForFile(
            sFile, "");
        return (dictBadges && dictBadges.sGithub) || "";
    }

    function _fsComputeStepRenderHash(step, iIndex, dictContext, dictVars) {
        // The hash captures every render-affecting input
        // fsRenderStepItem reads: the step object itself plus the
        // per-step slices of dictContext and the resolved template
        // variables (so a global-setting save invalidates every
        // card's hash).
        return JSON.stringify(step)
            + "\x01" + (dictContext.dictStepStatus[iIndex] || "")
            + "\x01" + (dictContext.dictStepTaint[iIndex] ? "t" : "")
            + "\x01" + _fsExpansionSliceForStep(iIndex, dictContext)
            + "\x01" + _fsContextSliceForStep(iIndex, dictContext)
            + "\x01" + _fsDeclarationBadgeSlice(step)
            + "\x01" + JSON.stringify(dictVars || {});
    }

    function _fnRenderStepListImmediate() {
        if (typeof VaibifySyncManager !== "undefined"
            && typeof VaibifySyncManager.fnDismissAllPicklists
                === "function") {
            VaibifySyncManager.fnDismissAllPicklists();
        }
        var elList = document.getElementById("listSteps");
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) {
            elList.innerHTML = "";
            _fnInvalidateAllRenderCaches();
            return;
        }
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var sBoundary = _fsBoundarySignature(listSteps);
        var dictVars = fdictBuildClientVariables();
        var dictContext = fdictBuildRenderContext();
        if (sBoundary !== _sLastBoundarySignature) {
            _fnRenderStepListFull(
                elList, listSteps, dictVars, dictContext, sBoundary);
        } else {
            _fnRenderStepListIncremental(
                elList, listSteps, dictVars, dictContext);
        }
        _fnRenderProjectBlock(dictContext);
        _fnApplyStepsCollapsedClass();
        fnApplyTimestampVisibility();
        fnBindStepEvents();
        fnUpdateHighlightState();
        VaibifyStepRenderer.fnFillAiDeclarationPreviews();
        VaibifyFileOps.fnScheduleFileExistenceCheck(
            _dictWorkflowState);
    }

    function _fnRenderStepListFull(
        elList, listSteps, dictVars, dictContext, sBoundary
    ) {
        var sHtml = VaibifyStepRenderer.fsRenderStepColumnHeader();
        if (listSteps.length === 0) {
            sHtml += VaibifyStepRenderer.fsRenderEmptyStepListNotice();
        }
        var bPrior = null;
        _dictRenderedStepHashes = {};
        listSteps.forEach(function (step, iIndex) {
            var bInteractive = fbStepIsInteractive(step);
            if (bInteractive !== bPrior) {
                sHtml += fsRenderStepTypeBanner(bInteractive);
                bPrior = bInteractive;
            }
            sHtml += VaibifyStepRenderer.fsRenderStepItem(
                step, iIndex, dictVars, dictContext);
            _dictRenderedStepHashes[iIndex] = _fsComputeStepRenderHash(
                step, iIndex, dictContext, dictVars);
        });
        // The AI Declaration's only home is the step list; when the
        // step is missing, its ghost row carries the add action. The
        // boundary signature encodes the declaration step ("D"), so
        // adding one triggers this full rebuild and retires the ghost.
        var bHasDeclarationStep = listSteps.some(function (step) {
            return step.sStepKind === "ai-declaration";
        });
        if (!bHasDeclarationStep) {
            sHtml += VaibifyStepRenderer.fsRenderGhostAiDeclarationRow();
        }
        elList.innerHTML = sHtml;
        _sLastBoundarySignature = sBoundary;
    }

    var _sLastProjectBlockHtml = null;

    function fbProjectFormFieldFocused() {
        /* True while the researcher is editing a field inside the
           Project block, so a poll does not replace the form under
           their hands.

           The byte-identical memo below is the steady-state
           protection and is not sufficient on its own: it holds only
           while NOTHING else in the block moves, and the block also
           carries pulsing remote badges and live counts. When one of
           those changed mid-edit the whole block was rewritten and
           the researcher's selection went back to whatever the server
           last stored. That was survivable while the radios rendered
           blank; once they render the SAVED answer it becomes a trap
           -- a researcher who declined and then chose another option
           watched the form snap back to Declined and reported,
           correctly, that they could not undecline (2026-09-08).

           Buttons are deliberately NOT guarded. Focus lands on
           "Save this answer" at the moment the answer is submitted,
           and skipping the render then would suppress the very update
           that shows it was recorded. */
        var elActive = document.activeElement;
        if (!elActive || !elActive.closest) return false;
        if (!elActive.closest("#projectBlock")) return false;
        return ["INPUT", "SELECT", "TEXTAREA"].indexOf(
            elActive.tagName) !== -1;
    }

    function _fnRenderProjectBlock(dictContext) {
        // Rebuilt from data on every render — the block lives in its
        // own container, never in the incremental step-hash path, so
        // the requirement group/row expansion Sets need no render
        // signature (expansion state is IN the rendered output). The
        // DOM write is skipped when the output is byte-identical so a
        // steady-state poll never wipes in-progress form input (the
        // determinism declare form) or a text selection.
        var elBlock = document.getElementById("projectBlock");
        if (!elBlock) return;
        if (!_dictWorkflowState.dictWorkflow) {
            elBlock.innerHTML = "";
            _sLastProjectBlockHtml = null;
            return;
        }
        var sHtml =
            VaibifyWorkflowRequirements.fsRenderProjectBlock(
                dictContext);
        if (sHtml === _sLastProjectBlockHtml) return;
        // The memo above did not match, so this render WOULD replace
        // the block. Hold it while a field is being edited, and
        // deliberately do not update _sLastProjectBlockHtml: the next
        // render after the field is left still sees a difference and
        // writes, so held state is delayed, never dropped.
        if (fbProjectFormFieldFocused()) return;
        elBlock.innerHTML = sHtml;
        _sLastProjectBlockHtml = sHtml;
    }

    function _fnApplyStepsCollapsedClass() {
        var elBlock = document.getElementById("stepsBlock");
        if (!elBlock) return;
        elBlock.classList.toggle(
            "collapsed", _dictUiState.bStepsCollapsed === true);
        // The Steps header is static HTML; keep its aggregate status
        // light current here so the banner conveys the total step
        // state even while collapsed.
        var elStatus = elBlock.querySelector(".steps-block-status");
        if (elStatus) {
            elStatus.innerHTML = _fsRenderAlignDirectoriesButton() +
                _fsRenderStepsAggregateLight();
        }
    }

    function _fiCountNonconformingSteps() {
        var listSteps = (_dictWorkflowState.dictWorkflow || {})
            .listSteps || [];
        return listSteps.filter(function (dictStep) {
            return !VaibifyUtilities.fbStepDirectoryConforms(dictStep);
        }).length;
    }

    function _fsRenderAlignDirectoriesButton() {
        // The legacy-migration entry point: visible only while the
        // project has steps violating the name->directory contract.
        var iCount = _fiCountNonconformingSteps();
        if (iCount === 0) return "";
        return '<button class="btn wf-align-directories" ' +
            'title="' + iCount + ' step(s) have a directory that ' +
            'does not match their name. Align moves each directory ' +
            'to the name’s camel-case form (git mv; markers, ' +
            'manifest, and declared paths follow).">' +
            'Align directories (' + iCount + ')</button>';
    }

    async function fdictAlignStepDirectories() {
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + encodeURIComponent(sContainerId) +
                "/align-directories", {});
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error");
            return;
        }
        var listSkipped = dictResult.listSkipped || [];
        var sMessage = (dictResult.listAligned || []).length +
            " directory(ies) aligned";
        if (listSkipped.length > 0) {
            sMessage += "; " + listSkipped.length + " skipped: " +
                listSkipped.map(function (dictSkip) {
                    return dictSkip.sLabel + " (" +
                        dictSkip.sReason + ")";
                }).join("; ");
        }
        fnShowToast(sMessage,
            listSkipped.length > 0 ? "warning" : "success");
        await VaibifyWorkflowManager.fnRefreshWorkflow();
    }

    var _DICT_STEPS_AGGREGATE_PHRASES = {
        "attained": "Every step meets its {level} requirements",
        "partial": "Some steps have unmet {level} requirements",
        "none": "Every started step is failing {level}",
        "unknown": "Step {level} status is not yet known",
        "not-started": "No step has started yet",
        "unassessed": "Step outputs exist but none have been " +
            "assessed yet",
        "not-applicable": "No steps with {level} requirements",
    };

    function _fsAggregateStepsLevelState(iLevel) {
        // The total state of one level across every step, summarized
        // by the shared banner rule (see
        // VaibifyUtilities.fsSummarizeLevelStates): red only when
        // every started step is failing; any progress in the mix
        // reads orange.
        var listSteps = (_dictWorkflowState.dictWorkflow || {})
            .listSteps || [];
        if (listSteps.length === 0) return "not-started";
        var listStates = listSteps.map(function (dictStep, iIndex) {
            return fsLevelCellState(iIndex, iLevel);
        });
        return VaibifyUtilities.fsSummarizeLevelStates(listStates);
    }

    function _fsRenderStepsAggregateLight() {
        // A full ⚠ + L1|L2|L3 strip so the collapsed Steps banner
        // lines up with the Project banner strip. Each cell carries
        // the aggregate of that level across every step row.
        var sHtml = '<span class="step-level-strip">' +
            '<span class="step-regression-cell"></span>';
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            var sState = _fsAggregateStepsLevelState(iLevel);
            var sPhrase = (_DICT_STEPS_AGGREGATE_PHRASES[sState] ||
                "Step status").replace(
                "{level}", "Level " + iLevel);
            sHtml += VaibifyUtilities.fsBuildLevelCell(
                sState, sPhrase, "all steps passing");
        }
        return sHtml + '</span>';
    }

    function _fnRenderStepListIncremental(
        elList, listSteps, dictVars, dictContext
    ) {
        listSteps.forEach(function (step, iIndex) {
            var sHash = _fsComputeStepRenderHash(
                step, iIndex, dictContext, dictVars);
            if (_dictRenderedStepHashes[iIndex] === sHash) return;
            var sHtml = VaibifyStepRenderer.fsRenderStepItem(
                step, iIndex, dictVars, dictContext);
            var elExisting = elList.querySelector(
                ".step-wrapper[data-step-index=\"" + iIndex + "\"]");
            if (!elExisting) return;
            var elTemp = document.createElement("div");
            elTemp.innerHTML = sHtml;
            var elNew = elTemp.firstElementChild;
            if (!elNew) return;
            elExisting.replaceWith(elNew);
            _dictRenderedStepHashes[iIndex] = sHash;
        });
    }

    function fnRenderStepListPartial(listAffectedFiles) {
        // Maps changed files to step indices via the reverse map.
        // When no file matches (badge keys are workspace-relative;
        // raw step file values may not be), invalidate everything so
        // the next render rebuilds rather than leaving stale badges.
        if (!listAffectedFiles || !listAffectedFiles.length) {
            return fnRenderStepList();
        }
        var iMatched = 0;
        listAffectedFiles.forEach(function (sFile) {
            var iStep = _dictStepIndexByFilePath[sFile];
            if (iStep === undefined) return;
            _fnInvalidateRenderCache(iStep);
            iMatched++;
        });
        if (iMatched === 0) _dictRenderedStepHashes = {};
        fnRenderStepList();
    }

    function fsRenderStepTypeBanner(bInteractive) {
        var sLabel = bInteractive ?
            "Interactive Steps" : "Automatic Steps";
        return '<div class="step-type-banner">' + sLabel + '</div>';
    }

    function fnClearRunningStatuses() {
        for (var sKey in _dictWorkflowState.dictStepStatus) {
            var sVal = _dictWorkflowState.dictStepStatus[sKey];
            if (sVal === "running" || sVal === "queued"
                || sVal === "overBudget") {
                delete _dictWorkflowState.dictStepStatus[sKey];
            }
        }
    }

    var _bReflectedDispatchRun = false;
    var _iReflectedActiveIndex = -1;

    function _fnReflectDispatchedRunState(dictRunState) {
        // The continuously-polled /status payload surfaces any
        // dispatched run's active step and its per-step results —
        // including an in-container agent's run-step/runFrom — so the
        // lights follow even runs this browser did not initiate. The
        // pipeline-run poll additionally owns the queued vocabulary for
        // browser-initiated runs; this path never claims a step is
        // queued, because it does not know which steps a run covers.
        if (!dictRunState) return;
        if (dictRunState.bRunning && dictRunState.iActiveStep > 0) {
            var iActiveIndex = dictRunState.iActiveStep - 1;
            // Only the marker THIS path set is released when the run
            // moves on; a light set by the live run's own events is
            // not this path's to clear. Leaving it lit is how every
            // finished step of an agent's run kept pulsing
            // (researcher-reported, 2026-09-23).
            var sPrevious = _dictWorkflowState.dictStepStatus[
                _iReflectedActiveIndex];
            if (_iReflectedActiveIndex !== iActiveIndex &&
                (sPrevious === "running" || sPrevious === "overBudget")) {
                delete _dictWorkflowState.dictStepStatus[
                    _iReflectedActiveIndex];
            }
            VaibifyPipelineRunner.fnApplyStepResults(
                dictRunState.dictStepResults || {});
            // An active step that has outrun its declared wall-clock
            // budget still runs, but is flagged distinctly so a hung
            // step is no longer indistinguishable from a legitimately
            // long one. The backend computes this live each poll.
            _dictWorkflowState.dictStepStatus[iActiveIndex] =
                dictRunState.bActiveStepOverBudget
                    ? "overBudget" : "running";
            _iReflectedActiveIndex = iActiveIndex;
            _bReflectedDispatchRun = true;
            fnRenderStepList();
        } else if (_bReflectedDispatchRun) {
            // The finished run's verdicts, not merely the absence of a
            // running marker: clearing alone left every step of a
            // completed agent run looking as though it never ran.
            VaibifyPipelineRunner.fnApplyCompletedState(dictRunState);
            _iReflectedActiveIndex = -1;
            _bReflectedDispatchRun = false;
        }
    }

    function _fnRenderOtherProjectRuns(dictOtherRuns) {
        /* How many OTHER projects in this container are running. Their
           names are one hover away; the count is what says the
           container is busier than this project's lights show. */
        var elBadge = document.getElementById("otherProjectRunBadge");
        if (!elBadge) return;
        var iCount = (dictOtherRuns && dictOtherRuns.iRunningProjectCount) || 0;
        if (iCount < 1) {
            elBadge.style.display = "none";
            elBadge.textContent = "";
            return;
        }
        elBadge.textContent = iCount === 1 ?
            "1 other project running" : iCount + " other projects running";
        elBadge.title = "Running in this container: " +
            (dictOtherRuns.listRunningProjects || []).map(function (d) {
                return d.sWorkflowName || d.sProjectRepoPath;
            }).join(", ") +
            ". Open a project to follow or stop its run.";
        elBadge.style.display = "";
    }

    function fnResetQueuedSteps(listStepIndices) {
        /* A refused dispatch resets only the lights it optimistically
         * set to "queued"; a live run's "running" lights stay. With no
         * indices (runAll/runFrom refusals), clear every queued light. */
        if (listStepIndices && listStepIndices.length > 0) {
            listStepIndices.forEach(function (iIndex) {
                if (_dictWorkflowState.dictStepStatus[iIndex] === "queued") {
                    delete _dictWorkflowState.dictStepStatus[iIndex];
                }
            });
            return;
        }
        for (var sKey in _dictWorkflowState.dictStepStatus) {
            if (_dictWorkflowState.dictStepStatus[sKey] === "queued") {
                delete _dictWorkflowState.dictStepStatus[sKey];
            }
        }
    }

    function fnPruneStaleStatuses() {
        var iStepCount = (_dictWorkflowState.dictWorkflow && _dictWorkflowState.dictWorkflow.listSteps)
            ? _dictWorkflowState.dictWorkflow.listSteps.length : 0;
        for (var sKey in _dictWorkflowState.dictStepStatus) {
            if (parseInt(sKey, 10) >= iStepCount) {
                delete _dictWorkflowState.dictStepStatus[sKey];
            }
        }
    }

    function fnInvalidateStepFileCache(iStep) {
        var sPrefix = iStep + ":";
        Object.keys(_dictWorkflowState.dictFileExistenceCache).forEach(function (sKey) {
            if (sKey.indexOf(sPrefix) === 0) {
                delete _dictWorkflowState.dictFileExistenceCache[sKey];
            }
        });
        VaibifyTestManager.fsetGetStepsWithData().delete(iStep);
    }

    function fnPollAllStepFiles() {
        if (!_dictSessionState.sContainerId ||
            !_dictWorkflowState.dictWorkflow) return;
        // Skip the per-step existence check for any step whose output
        // mtime is already populated by the polling response — its
        // files demonstrably exist on disk, so re-issuing the check
        // would waste ~1000 file probes per poll on a 100-step
        // workflow. New / never-run steps still get the check.
        var dictMtimes = _dictWorkflowState.dictOutputMtimes || {};
        _dictWorkflowState.dictWorkflow.listSteps.forEach(
            function (step, iStep) {
                if (dictMtimes[String(iStep)]) return;
                VaibifyFileOps.fnCheckStepDataFiles(
                    step, iStep, _dictWorkflowState);
            });
    }

    function fbStepRequiresUnitTests(dictStep) {
        if (fbStepIsInteractive(dictStep)) return false;
        if ((dictStep.saDataCommands || []).length === 0) return false;
        return true;
    }

    function fbStepIsAtLeastLevel1(dictStep, iStep) {
        if (dictStep && dictStep.sStepKind === "ai-declaration") {
            /* Declaration steps are L1-not-applicable by the
             * 2026-07-02 ruling (their sign-off is an L2 criterion;
             * the L1 cell renders a dash, and the server emits no L1
             * blockers for them). Without this mirror of the server
             * rule the step has no data and no "passed" badge, so it
             * demoted the client-side level to 0 forever — every step
             * showed its check while the theme never left level 0. */
            return true;
        }
        /* Input contract must be declared — files listed or the
         * explicit "no input data" flag — mirroring the server's L1
         * rule. Without this the client chip and file colours showed
         * an undeclared step as L1 while the server cell showed it
         * blocked. */
        var bInputDeclared =
            (dictStep.saInputDataFiles || []).length > 0 ||
            dictStep.bNoInputData === true;
        if (!bInputDeclared) return false;
        var dictVerify = fdictGetVerification(dictStep);
        var listModified = dictVerify.listModifiedFiles || [];
        if (listModified.length > 0) return false;
        if (fbAnyUpstreamModified(iStep)) return false;
        if (_dictWorkflowState.dictScriptModified[iStep] === "modified") {
            return false;
        }
        var bHasData =
            VaibifyTestManager.fsetGetStepsWithData().has(iStep) ||
            !!_dictWorkflowState.dictOutputMtimes[String(iStep)];
        if (!bHasData) return false;
        var sUser = dictVerify.sUser;
        var sDeps = fsComputeDepsState(iStep);
        var bDepsFullyOk = sDeps === "none" || sDeps === "passed";
        if (sUser !== "passed" || !bDepsFullyOk) return false;
        if (fbStepRequiresUnitTests(dictStep)) {
            return fsEffectiveTestState(dictStep) === "passed";
        }
        return true;
    }

    function fbIsFileMissing(elText) {
        return VaibifyFileOps.fbIsFileMissing(
            elText, _dictWorkflowState.dictFileExistenceCache);
    }

    var fsInitialFileStatusClass =
        VaibifyFileOps.fsInitialFileStatusClass;

    function fsComputeStepLabel(iIndex) {
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var step = listSteps[iIndex];
        if (step && typeof step.sLabel === "string" && step.sLabel) {
            return step.sLabel;
        }
        // TODO(2026-07-01): drop this fallback once every response
        // path carries step.sLabel. Kept as a transition shim.
        if (_dictStepLabelByIndex[iIndex] !== undefined) {
            return _dictStepLabelByIndex[iIndex];
        }
        _fnPopulateStepLabelMemo(listSteps);
        return _dictStepLabelByIndex[iIndex];
    }

    function _fnPopulateStepLabelMemo(listSteps) {
        // Walk the list once, counting interactive vs automated steps
        // up to each index. Replaces the original O(N) per-step walk
        // with an O(N) one-time pass; subsequent lookups are O(1).
        var iAuto = 0;
        var iInter = 0;
        for (var i = 0; i < listSteps.length; i++) {
            var bInteractive = fbStepIsInteractive(listSteps[i]);
            if (bInteractive) {
                iInter++;
                _dictStepLabelByIndex[i] =
                    "I" + String(iInter).padStart(2, "0");
            } else {
                iAuto++;
                _dictStepLabelByIndex[i] =
                    "A" + String(iAuto).padStart(2, "0");
            }
        }
    }

    function _flistStepWarningReasons(iStepIndex) {
        // Every reason the ⚠ column should report for one step, one
        // plain-English line each. The backend level warning comes
        // first (it is still gated server-side to the lowest
        // non-attained level); the step staleness signals follow.
        // Signals already voiced by the step's dominant L1 blocker
        // hint are skipped so the tooltip never repeats itself.
        var listReasons = [];
        var dictBackend = (_dictWorkflowState.dictStepLevelWarnings ||
            {})[String(iStepIndex)] || null;
        if (dictBackend && dictBackend.iWarningLevel !== null &&
                dictBackend.sWarningHint &&
                dictBackend.sWarningHint.indexOf(
                    "timestamp order") === -1) {
            // The backend's generic timestamp-order hint is dropped:
            // the specific staleness lines below say the same thing
            // with the actual cause named.
            listReasons.push(dictBackend.sWarningHint);
        }
        var dictBlocker =
            _dictWorkflowState.dictBlockersByStep[iStepIndex];
        var sBlockerCriterion = dictBlocker
            ? dictBlocker.sCriterion : "";
        if (dictBlocker) {
            var dictMeta = _fdictBannerGlyphMeta(dictBlocker);
            if (dictMeta) {
                listReasons.push(
                    dictBlocker.sRemediationHint || dictMeta.sLabel);
            }
        }
        var dictStep = ((_dictWorkflowState.dictWorkflow || {})
            .listSteps || [])[iStepIndex] || {};
        var dictVerify = dictStep.dictVerification || {};
        if (_dictWorkflowState.dictScriptModified[iStepIndex] ===
                "modified" && sBlockerCriterion !== "script-stale") {
            listReasons.push("You edited this step's script after " +
                "its outputs were made — re-run the step");
        }
        var listModified = dictVerify.listModifiedFiles || [];
        if (listModified.length > 0) {
            listReasons.push("Output files changed after you " +
                "verified: " + listModified.map(function (sPath) {
                    return sPath.split("/").pop();
                }).join(", ") + " — re-run or re-verify");
        }
        if (fbAnyDepTimingStale(iStepIndex) &&
                sBlockerCriterion !== "upstream-modified") {
            listReasons.push("An earlier step's outputs changed — " +
                "re-run to stay consistent");
        }
        if (dictVerify.bUnseededRandomnessWarning === true) {
            listReasons.push("Unseeded randomness detected — add a " +
                "seed so the run is reproducible");
        }
        if (!VaibifyUtilities.fbStepDirectoryConforms(dictStep)) {
            listReasons.push("Step name and directory disagree — " +
                "the directory should be '" +
                VaibifyUtilities.fsSlugFromStepName(
                    dictStep.sName || "") +
                "'. Right-click → Rename, or use Align " +
                "directories on the Steps banner");
        }
        return listReasons;
    }

    function _fbStepWarningIsRed(iStepIndex) {
        // Red is reserved for genuine failures: the backend's
        // failed-tests warning, or an L1 blocker whose glyph meta is
        // the red axis warning (failed / outputs-missing).
        var dictBackend = (_dictWorkflowState.dictStepLevelWarnings ||
            {})[String(iStepIndex)] || null;
        if (dictBackend && dictBackend.sWarningSeverity === "red") {
            return true;
        }
        var dictStep = ((_dictWorkflowState.dictWorkflow || {})
            .listSteps || [])[iStepIndex];
        if (dictStep &&
                !VaibifyUtilities.fbStepDirectoryConforms(dictStep)) {
            // A broken name<->directory contract is an ERROR
            // (2026-07-18 ruling), not pending work.
            return true;
        }
        var dictBlocker =
            _dictWorkflowState.dictBlockersByStep[iStepIndex];
        if (!dictBlocker) return false;
        var dictMeta = _fdictBannerGlyphMeta(dictBlocker);
        return Boolean(dictMeta) &&
            dictMeta.sClass === "step-blocker-glyph-axis";
    }

    var _DICT_BLOCKER_CRITERION_GLYPHS = {
        "input-data-undeclared": {
            /* Orange "pending action" family (same glyph as awaiting
             * sign-off): undeclared input is an incomplete
             * declaration a single click resolves, not a breakage —
             * red stays reserved for failed/missing tests. */
            sIcon: "—",
            sLabel: "Input data undeclared — list the step's raw " +
                "inputs or check 'No input data needed'",
            sClass: "step-blocker-glyph-user",
        },
        "upstream-modified": {
            sIcon: "✎",
            sLabel: "Upstream changed; re-run to clear blocker",
            sClass: "step-blocker-glyph-upstream",
        },
        "script-stale": {
            sIcon: "✎",
            sLabel: "Script edited after output — re-run to clear blocker",
            sClass: "step-blocker-glyph-script-stale",
        },
        "axis-not-green": {
            sIcon: "⚠",
            sLabel: "Some tests are not verified — re-run to clear " +
                "the blocker",
            sClass: "step-blocker-glyph-axis",
        },
        "attestation-stale": {
            sIcon: "✎",
            sLabel: "Outputs changed since you verified — " +
                "re-verify or re-run",
            sClass: "step-blocker-glyph-attestation-stale",
        },
        "user-not-approved": {
            sIcon: "—",
            sLabel: "Awaiting your sign-off — review the step's " +
                "results and approve it",
            sClass: "step-blocker-glyph-user",
        },
    };

    // Severity refinement for ``axis-not-green``: the blocker's
    // ``sSubState`` (mirroring levelGates._fsAxisNotGreenSubState)
    // selects the banner glyph. ``untested`` is deliberately null —
    // no banner glyph; the orange status light already carries
    // "work not yet done". Keys must equal the Python sub-state
    // literals (pinned by tests/testStepRendererBlockerGlyphs.py).
    var _DICT_AXIS_SUBSTATE_GLYPHS = {
        "failed": {
            sIcon: "⚠",
            sLabel: "Unit tests failed — fix the step and re-run",
            sClass: "step-blocker-glyph-axis",
        },
        "outputs-missing": {
            sIcon: "⚠",
            sLabel: "Declared outputs missing — run the step",
            sClass: "step-blocker-glyph-axis",
        },
        "outputs-changed": {
            sIcon: "✎",
            sLabel: "Outputs changed since tests last passed — re-run",
            sClass: "step-blocker-glyph-outputs-changed",
        },
        "untested": null,
    };

    var _DICT_L2_BLOCKER_GLYPHS = {
        "ai-declaration-unattested": {
            sIcon: "—",
            sLabel: "AI declaration not yet attested — open the " +
                "step and verify it",
            sClass: "step-blocker-glyph-l2-declaration",
        },
        "not-in-github-mirror": {
            sIcon: "⚠",
            sLabel: "Published files differ from GitHub — commit " +
                "and push from the Repos panel",
            sClass: "step-blocker-glyph-l2-mirror",
        },
        "not-in-zenodo-deposit": {
            sIcon: "⚠",
            sLabel: "Published files differ from Zenodo — publish " +
                "a new deposit from the Repos panel",
            sClass: "step-blocker-glyph-l2-zenodo",
        },
        "github-verify-stale": {
            sIcon: "⚠",
            sLabel: "GitHub sync check is stale — re-verify from " +
                "the Repos panel to refresh status",
            sClass: "step-blocker-glyph-l2-github-stale",
        },
        "zenodo-verify-stale": {
            sIcon: "⚠",
            sLabel: "Zenodo sync check is stale — re-verify from " +
                "the Repos panel to refresh status",
            sClass: "step-blocker-glyph-l2-zenodo-stale",
        },
        "missing-ai-declaration-step": {
            sIcon: "—",
            sLabel: "Add an AI declaration step to " +
                "record agent involvement",
            sClass: "step-blocker-glyph-l2-ai-declaration",
        },
        "ai-models-undeclared": {
            sIcon: "⚠",
            sLabel: "No AI model declared — declare each model " +
                "used in the AI section of the Project block",
            sClass: "step-blocker-glyph-l2-ai-models",
        },
        "personal-layer-unanswered": {
            sIcon: "⚠",
            sLabel: "Personal AI Configuration unanswered — answer " +
                "in the AI section of the Project block",
            sClass: "step-blocker-glyph-l2-personal-layer",
        },
        "image-archive-unanswered": {
            sIcon: "⚠",
            sLabel: "Environment archive unanswered — deposit the " +
                "container image, point at a deposit that holds it, " +
                "or decline, in the Artifacts section of the " +
                "Project block",
            sClass: "step-blocker-glyph-l2-image-archive",
        },
        "figure-not-frozen": {
            sIcon: "⚠",
            sLabel: "Plot not pushed to Overleaf at recorded commit — " +
                "push manuscript figures",
            sClass: "step-blocker-glyph-l2-figure",
        },
        "arxiv-mismatch": {
            sIcon: "⚠",
            sLabel: "arXiv submission doesn't match the Overleaf " +
                "push — re-submit to arXiv or update the recorded " +
                "submission under Published copies",
            sClass: "step-blocker-glyph-l2-arxiv-mismatch",
        },
        "arxiv-version-stale": {
            sIcon: "⚠",
            sLabel: "arXiv has a newer version than recorded — " +
                "update the recorded version under Published copies",
            sClass: "step-blocker-glyph-l2-arxiv-version",
        },
    };

    var _DICT_L3_BLOCKER_GLYPHS = {
        "missing-from-manifest": {
            sIcon: "⚠",
            sLabel: "File missing from MANIFEST.sha256 — regenerate " +
                "the envelope from the Artifacts section of the " +
                "Project block",
            sClass: "step-blocker-glyph-l3-manifest",
        },
        "script-not-pinned": {
            sIcon: "⚠",
            sLabel: "Script changed since MANIFEST.sha256 was " +
                "written — re-run the step or regenerate the " +
                "envelope (Artifacts section)",
            sClass: "step-blocker-glyph-l3-pin",
        },
        "nondeterminism-undeclared": {
            sIcon: "⚠",
            sLabel: "Step uses randomness without a recorded seed — " +
                "seed it, or declare it in the Determinism section " +
                "of the Project block",
            sClass: "step-blocker-glyph-l3-determinism",
        },
        "binary-not-declared": {
            sIcon: "⚠",
            sLabel: "Step runs a program vaibify has no record of — " +
                "declare it in the Software section of the Project " +
                "block",
            sClass: "step-blocker-glyph-l3-binary-declared",
        },
        "binary-not-captured": {
            sIcon: "⚠",
            sLabel: "Declared program's version and hash not yet " +
                "recorded — use Capture in the Software section",
            sClass: "step-blocker-glyph-l3-binary-captured",
        },
        "binary-drifted": {
            sIcon: "⚠",
            sLabel: "The program on disk no longer matches the hash " +
                "recorded for reproducibility — it was rebuilt or " +
                "replaced after the outputs were produced. Re-run " +
                "with the current program and re-capture, or restore " +
                "the published binary.",
            sClass: "step-blocker-glyph-l3-binary-drifted",
        },
        "dockerfile-not-pinned": {
            sIcon: "⚠",
            sLabel: "Dockerfile base image not pinned to an exact " +
                "digest — pin the FROM line with @sha256:… or ask " +
                "the in-container agent to pin it",
            sClass: "step-blocker-glyph-l3-workflow-dockerfile",
        },
        "dependency-lock-missing": {
            sIcon: "⚠",
            sLabel: "requirements.lock missing or lacks hashes — " +
                "regenerate the envelope from the Artifacts section",
            sClass: "step-blocker-glyph-l3-workflow-lock",
        },
        "environment-snapshot-missing": {
            sIcon: "⚠",
            sLabel: "Environment snapshot missing or lacks the " +
                "container image digest — regenerate the envelope " +
                "from the Artifacts section",
            sClass: "step-blocker-glyph-l3-workflow-env",
        },
        "reproduce-script-missing": {
            sIcon: "⚠",
            sLabel: "reproduce.sh missing or not pinned in the " +
                "manifest — generate it from the Artifacts section " +
                "of the Project block",
            sClass: "step-blocker-glyph-l3-workflow-reproduce",
        },
        /* The Level 3 half of the published-copy question. Its Level 2
           twin is "not-in-github-mirror" above: same comparison, one
           pass, different files. Level 3 owns the envelope because
           the envelope is what a third party needs to re-execute —
           and a published reproduce.sh that differs from the local
           one means they would run something the researcher never
           did. */
        "envelope-not-in-github-mirror": {
            sIcon: "⚠",
            sLabel: "The reproduce script, manifest, dependency lock, " +
                "environment snapshot or Dockerfile differs from the " +
                "copy on GitHub, or has not been compared with it — " +
                "push the envelope, then Verify now",
            sClass: "step-blocker-glyph-l3-workflow-envelope",
        },
        /* The permanent-archive half of the same pair (2026-08-26).
           GitHub is not an archive — repos are renamed, made private,
           deleted — so Level 3's re-execute claim also needs the
           envelope in Zenodo. Deposits are immutable, so the
           remediation is a new deposit version, never a push. */
        "envelope-not-in-zenodo-archive": {
            sIcon: "⚠",
            sLabel: "The reproduce script, manifest, dependency lock, " +
                "environment snapshot or Dockerfile is not in the " +
                "Zenodo archive, or has not been compared with it — " +
                "publish a new deposit version with the envelope " +
                "(or declare the record that holds it), then " +
                "Verify now",
            sClass: "step-blocker-glyph-l3-workflow-envelope-zenodo",
        },
        /* The archive must carry the attestation itself, not just
           the envelope (2026-09-03). Level 3 claims a stranger can
           re-fetch and re-execute; until this criterion existed, the
           evidence that the author's OWN rebuild passed lived only on
           the author's disk, so the strongest rung on the ladder
           rested on a record nobody else could read. The GitHub copy
           is encouraged on the PROOF tab and gates nothing -- a repo
           can be renamed, made private or deleted, so it cannot carry
           a permanence claim. */
        "image-not-archived": {
            sIcon: "⚠",
            sLabel: "The container image these results were produced " +
                "in is not in a permanent archive, or the deposit on " +
                "record covers a different image or platform — " +
                "deposit it from the Artifacts section of the " +
                "Project block",
            sClass: "step-blocker-glyph-l3-workflow-image-archive",
        },
        /* A sandbox deposit really does hold matching bytes, so this
           blocker withholds the CREDIT rather than disputing the
           rebuild: the attestation row keeps saying the rerun
           happened, and says the attestation does not count while an
           archive is a test deposit. */
        /* The script exists, is hashed, and carries no tokens -- and
           still cannot run on a stranger's machine, because it
           predates the archive fallback it needs. Its own blocker,
           because "write a reproduce script" is the wrong remedy for
           a project that already has one. */
        "reproduce-script-stale": {
            sIcon: "⚠",
            sLabel: "reproduce.sh predates the machinery it needs — " +
                "it pulls the image and has no way to fall back to " +
                "your archived copy, so a stranger's reproduction " +
                "fails on its first line; regenerate it, then commit " +
                "and publish",
            sClass: "step-blocker-glyph-l3-workflow-reproduce-stale",
        },
        "an-archive-is-a-sandbox-deposit": {
            sIcon: "⚠",
            sLabel: "An archive backing this project is a Zenodo " +
                "SANDBOX deposit, which promises no preservation and " +
                "may be cleared at any time — use Make Permanent to " +
                "deposit again on zenodo.org",
            sClass: "step-blocker-glyph-l3-workflow-sandbox-archive",
        },
        "attestation-not-in-zenodo-archive": {
            sIcon: "⚠",
            sLabel: "The Zenodo archive carries no rebuild " +
                "attestation covering its own manifest, so a reader " +
                "cannot see this project was demonstrated to " +
                "rebuild — run the Level 3 verification, commit the " +
                "attestation, and publish a deposit version " +
                "containing it, then Verify now",
            sClass: "step-blocker-glyph-l3-workflow-attestation-archive",
        },
        "l3-attestation-stale": {
            sIcon: "⚠",
            sLabel: "Files changed since the last successful " +
                "rebuild — re-run rebuild verification on the PROOF " +
                "tab",
            sClass: "step-blocker-glyph-l3-workflow-attestation",
        },
        "binaries-not-declared-or-waived": {
            sIcon: "⚠",
            sLabel: "Programs found in step commands are neither " +
                "declared nor waived — resolve each one in the " +
                "Software section of the Project block",
            sClass: "step-blocker-glyph-l3-workflow-binaries",
        },
    };

    function _fdictBannerGlyphMeta(dictEntry) {
        // ``axis-not-green`` dispatches through the sub-state dict
        // when the backend supplies ``sSubState``; the static entry
        // is the fallback for older payloads that lack the field.
        if (dictEntry.sCriterion === "axis-not-green" &&
            _DICT_AXIS_SUBSTATE_GLYPHS.hasOwnProperty(
                dictEntry.sSubState)) {
            return _DICT_AXIS_SUBSTATE_GLYPHS[dictEntry.sSubState];
        }
        return _DICT_BLOCKER_CRITERION_GLYPHS[dictEntry.sCriterion];
    }

    var S_L1_FAILURE_GLYPH = "⚠";

    function fbFileIsL1Offending(iStepIndex, sRawPath) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[iStepIndex];
        if (!dictEntry) return false;
        var listOffending = dictEntry.listOffendingFiles || [];
        for (var i = 0; i < listOffending.length; i++) {
            if (listOffending[i] === sRawPath) return true;
        }
        return false;
    }

    function fbUpstreamStepIsL1Offending(iStepIndex, iUpstreamIndex) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[iStepIndex];
        if (!dictEntry) return false;
        var listOffending = dictEntry.listOffendingUpstreamSteps || [];
        return listOffending.indexOf(iUpstreamIndex) !== -1;
    }

    function fsBuildL1FailureGlyph(sTooltip) {
        return '<span class="l1-blocker-file-glyph" title="' +
            fnEscapeHtml(sTooltip || "Blocking L1 verification") +
            '">' + S_L1_FAILURE_GLYPH + '</span>';
    }

    // Per-file severity marks driven by the blocker's optional
    // ``dictOffendingFileMarks`` field ({sRawPath: "stale" | "failed"
    // | "missing"}). ``stale`` is recoverable by a re-run, so it
    // renders the orange pencil; ``failed`` / ``missing`` render the
    // red warning glyph (no X marks in the vaibify glyph language).
    var _DICT_FILE_MARK_GLYPHS = {
        "stale": {sIcon: "✎", sClass: "file-mark-stale"},
        "failed": {sIcon: "⚠", sClass: "l1-blocker-file-glyph"},
        "missing": {sIcon: "⚠", sClass: "l1-blocker-file-glyph"},
    };

    function _fsFileMarkForPath(iStepIndex, sRawPath) {
        var dictEntry = _dictWorkflowState.dictBlockersByStep[
            iStepIndex];
        if (!dictEntry) return "";
        return (dictEntry.dictOffendingFileMarks || {})[sRawPath] ||
            "";
    }

    function fsBuildFileMarkGlyph(iStepIndex, sRawPath, sTooltip) {
        var dictMeta = _DICT_FILE_MARK_GLYPHS[
            _fsFileMarkForPath(iStepIndex, sRawPath)];
        if (!dictMeta) return fsBuildL1FailureGlyph(sTooltip);
        return '<span class="' + dictMeta.sClass + '" title="' +
            fnEscapeHtml(sTooltip || "Blocking L1 verification") +
            '">' + dictMeta.sIcon + '</span>';
    }

    function _flistBlockerLevels() {
        return [
            _dictWorkflowState.dictBlockersByStep,
            _dictWorkflowState.dictBlockersByStepLevel2,
            _dictWorkflowState.dictBlockersByStepLevel3,
        ];
    }

    function _fiBlockerLevelWalkLimit(iStepIndex) {
        // Per-step gating: a step only surfaces work for its next
        // target rung. A step dirty at L1 shows only its L1 work;
        // L2 requirements appear once L1 is attained for that step.
        return Math.min(
            fiStepNextTargetLevel(iStepIndex),
            _flistBlockerLevels().length);
    }

    function fsBlockerHintForStep(iStepIndex) {
        // Section G: surface the dominant blocker's per-criterion
        // remediation hint for the step. Walks L1 upward, but only
        // to the step's next target rung, so the file-glyph tooltip
        // language matches the banner glyph's.
        var listLevels = _flistBlockerLevels();
        var iLimit = _fiBlockerLevelWalkLimit(iStepIndex);
        for (var i = 0; i < iLimit; i++) {
            var dictEntry = (listLevels[i] || {})[iStepIndex];
            if (dictEntry && dictEntry.sRemediationHint) {
                return dictEntry.sRemediationHint;
            }
        }
        return "";
    }

    function fsBlockerHintForFile(iStepIndex, sRawPath) {
        // Hook up file-list red glyphs to the per-file hint
        // (``dictOffendingFileHints``, when the backend supplies it)
        // of the first blocker whose ``listOffendingFiles`` contains
        // the file, then to that blocker's per-criterion hint.
        // Returns "" when the file is in no ``listOffendingFiles`` so
        // non-offending files never inherit the step-level hint.
        var listLevels = _flistBlockerLevels();
        var iLimit = _fiBlockerLevelWalkLimit(iStepIndex);
        for (var i = 0; i < iLimit; i++) {
            var dictEntry = (listLevels[i] || {})[iStepIndex];
            if (!dictEntry) continue;
            var listOffending = dictEntry.listOffendingFiles || [];
            if (listOffending.indexOf(sRawPath) === -1) continue;
            var dictFileHints = dictEntry.dictOffendingFileHints || {};
            if (dictFileHints[sRawPath]) return dictFileHints[sRawPath];
            if (dictEntry.sRemediationHint) {
                return dictEntry.sRemediationHint;
            }
        }
        return "";
    }

    /* --- Level cells (Scope F) ---
       Each step card and the workflow header row render a regression
       cell plus an always-visible L1|L2|L3 strip. Every cell comes
       from the poll's independent per-level projection
       (``dictStepLevels`` / ``dictWorkflowScopeLevels``), whose CELL
       dicts carry {sState, iSatisfied, iTotal, bRegression}.
       First-attainment dates come from the high-water marks.
       ``iStepIndex`` of -1 selects the workflow scope. */

    var _DICT_LEVEL_CELL_LABELS = {
        1: "L1 Self-Consistent",
        2: "L2 Published",
        3: "L3 Reproducible",
    };

    var _DICT_LEVEL_CELL_STATE_PHRASES = {
        "not-started": "not started — no outputs on disk and no " +
            "activity at this level yet",
        "unassessed": "unassessed — outputs exist on disk, but " +
            "no tests, checks, or sign-off have been recorded yet",
        "none": "no requirements met",
        "partial": "partially met",
        "attained": "attained",
        "unknown": "unknown — GitHub/Zenodo have not been checked " +
            "recently; refresh remote status to find out",
        "not-applicable":
            "not applicable — this step has no requirements at " +
            "this level",
    };

    function _fdictLevelStatesForScope(iStepIndex) {
        if (iStepIndex < 0) {
            return _dictWorkflowState.dictWorkflowScopeLevels || {};
        }
        return (_dictWorkflowState.dictStepLevels || {})[
            String(iStepIndex)] || {};
    }

    function _fdictLevelHighWaterForScope(iStepIndex) {
        if (iStepIndex < 0) {
            return _dictWorkflowState.dictWorkflowLevelHighWater ||
                {};
        }
        return (_dictWorkflowState.dictStepLevelHighWater || {})[
            String(iStepIndex)] || {};
    }

    function fdictLevelCellForScope(iStepIndex, iLevel) {
        var dictCell = _fdictLevelStatesForScope(iStepIndex)[
            "s" + iLevel];
        if (dictCell && typeof dictCell === "object") {
            return dictCell;
        }
        return null;
    }

    function fsLevelCellState(iStepIndex, iLevel) {
        // Rendered verbatim from the backend projection. An absent
        // cell renders the "?" "unknown" mark — never a fake
        // attained or not-started claim.
        var dictCell = fdictLevelCellForScope(iStepIndex, iLevel);
        return (dictCell && dictCell.sState) || "unknown";
    }

    function fsLevelCellTooltip(iStepIndex, iLevel) {
        var dictCell = fdictLevelCellForScope(iStepIndex, iLevel);
        var sState = fsLevelCellState(iStepIndex, iLevel);
        var listParts = [
            _DICT_LEVEL_CELL_LABELS[iLevel] + " — " +
                (_DICT_LEVEL_CELL_STATE_PHRASES[sState] || sState),
        ];
        if (iStepIndex < 0) {
            // The Project row covers project-scope requirements
            // only; it is NOT a roll-up of the step rows. The
            // all-steps aggregate renders as the header checkmarks
            // and the PROOF tab.
            listParts.push(
                "These requirements apply to the project as a " +
                "whole, not to any single step. Each step row " +
                "tracks its own. The overall level is shown by " +
                "the checkmarks next to the project name and in " +
                "the PROOF tab.");
        }
        if (dictCell && sState !== "not-applicable") {
            listParts.push(dictCell.iSatisfied + " of " +
                dictCell.iTotal + " requirements met");
        }
        return _flistAppendLevelTooltipContext(
            listParts, iStepIndex, iLevel, sState).join("\n");
    }

    function _flistAppendLevelTooltipContext(
        listParts, iStepIndex, iLevel, sState
    ) {
        if (sState === "not-applicable") {
            // Nothing to attain, regress from, or remediate; a stray
            // high-water stamp from the vacuous-attainment era must
            // not resurface here.
            return listParts;
        }
        var sFirstAttained = _fdictLevelHighWaterForScope(
            iStepIndex)[String(iLevel)] || "";
        if (sFirstAttained) {
            listParts.push("First attained " + sFirstAttained);
        }
        var sHint = _fsLevelBlockerHint(iStepIndex, iLevel);
        if (sHint && sState !== "attained") {
            listParts.push(sHint);
        }
        return listParts;
    }

    function _fsLevelBlockerHint(iStepIndex, iLevel) {
        // Dominant blocker hint for one rung of one scope. Workflow
        // scope (-1) hits the workflow-scope L2 entries that
        // _fdictBlockersByStepIndex keys under -1; L1 and L3 have no
        // workflow-scope entries in these dicts, so the hint is "".
        var dictEntry = (_flistBlockerLevels()[iLevel - 1] || {})[
            iStepIndex];
        return (dictEntry && dictEntry.sRemediationHint) || "";
    }

    function fiStepNextTargetLevel(iStepIndex) {
        // First rung whose cell is not attained — the rung the step
        // is currently working toward. A not-applicable rung has no
        // work to offer, so it never becomes the target. 4 when
        // every rung is attained or not applicable.
        for (var iLevel = 1; iLevel <= 3; iLevel++) {
            var sState = fsLevelCellState(iStepIndex, iLevel);
            if (sState !== "attained" && sState !== "not-applicable") {
                return iLevel;
            }
        }
        return 4;
    }

    function _fsStepLevelKey(iStepIndex, iLevel) {
        return iStepIndex + ":" + iLevel;
    }

    function fbIsStepLevelExpanded(iStepIndex, iLevel) {
        // First read for a step seeds its TARGET rung open (the
        // first non-attained level — fiStepNextTargetLevel), so the
        // detail opens onto the work the ladder asks for next; after
        // that the researcher's own toggles are authoritative.
        if (!_dictUiState.setLevelSeededSteps.has(iStepIndex)) {
            _dictUiState.setLevelSeededSteps.add(iStepIndex);
            var iTarget = fiStepNextTargetLevel(iStepIndex);
            if (iTarget >= 1 && iTarget <= 3) {
                _dictUiState.setExpandedStepLevels.add(
                    _fsStepLevelKey(iStepIndex, iTarget));
            }
        }
        return _dictUiState.setExpandedStepLevels.has(
            _fsStepLevelKey(iStepIndex, iLevel));
    }

    function fnToggleStepLevelSection(iStepIndex, iLevel) {
        fbIsStepLevelExpanded(iStepIndex, iLevel);
        var sKey = _fsStepLevelKey(iStepIndex, iLevel);
        if (_dictUiState.setExpandedStepLevels.has(sKey)) {
            _dictUiState.setExpandedStepLevels.delete(sKey);
        } else {
            _dictUiState.setExpandedStepLevels.add(sKey);
        }
        fnRenderStepList();
    }

    function fnExpandStepLevelSection(iStepIndex, iLevel) {
        // The banner's level cells double as a table of contents:
        // clicking one opens the step detail onto that section.
        fbIsStepLevelExpanded(iStepIndex, iLevel);
        _dictUiState.setExpandedSteps.add(iStepIndex);
        _dictUiState.setExpandedStepLevels.add(
            _fsStepLevelKey(iStepIndex, iLevel));
        fnRenderStepList();
    }

    function fbIsStepDescriptionExpanded(iStepIndex) {
        if (!_dictUiState.setDescriptionSeededSteps.has(iStepIndex)) {
            _dictUiState.setDescriptionSeededSteps.add(iStepIndex);
            var step = (_dictWorkflowState.dictWorkflow || {})
                .listSteps && _dictWorkflowState.dictWorkflow
                .listSteps[iStepIndex];
            if (step && (step.sDescription || "").trim()) {
                _dictUiState.setExpandedStepDescriptions.add(
                    iStepIndex);
            }
        }
        return _dictUiState.setExpandedStepDescriptions.has(
            iStepIndex);
    }

    function fnToggleStepDescription(iStepIndex) {
        fbIsStepDescriptionExpanded(iStepIndex);
        if (_dictUiState.setExpandedStepDescriptions.has(iStepIndex)) {
            _dictUiState.setExpandedStepDescriptions.delete(
                iStepIndex);
        } else {
            _dictUiState.setExpandedStepDescriptions.add(iStepIndex);
        }
        fnRenderStepList();
    }

    function fnBeginStepDescriptionEdit(iStepIndex) {
        // Swaps the description text for an inline textarea. This is
        // a DOM-only edit surface (no render-state change), so both
        // exits force the card's repaint by dropping its hash.
        var elBody = document.querySelector(
            '.step-description-body[data-step="' + iStepIndex + '"]');
        if (!elBody || elBody.querySelector("textarea")) return;
        var step = _dictWorkflowState.dictWorkflow
            .listSteps[iStepIndex];
        if (!step) return;
        elBody.innerHTML =
            '<textarea class="step-description-input" rows="4" ' +
            'placeholder="A few sentences on what this step ' +
            'does…"></textarea>' +
            '<div class="step-description-actions">' +
            '<button class="btn" id="btnDescriptionCancel">' +
            'Cancel</button> ' +
            '<button class="btn btn-primary" ' +
            'id="btnDescriptionSave">Save</button></div>';
        var elInput = elBody.querySelector("textarea");
        elInput.value = step.sDescription || "";
        elInput.focus();
        function fnRepaintCard() {
            delete _dictRenderedStepHashes[iStepIndex];
            fnRenderStepList();
        }
        document.getElementById("btnDescriptionCancel")
            .addEventListener("click", fnRepaintCard);
        document.getElementById("btnDescriptionSave")
            .addEventListener("click", async function () {
                // Success-only: commit the description to the step and
                // repaint only if the save landed, so a failed save
                // does not leave an un-persisted description on the card.
                var sText = elInput.value.trim();
                var dictResult = await fnPutStepEdit(
                    iStepIndex, {sDescription: sText});
                if (!dictResult) return;
                step.sDescription = sText;
                fnRepaintCard();
            });
    }

    function fnShowStepLevelRequirementsModal(iStepIndex, iLevel) {
        var dictCell = fdictLevelCellForScope(iStepIndex, iLevel);
        if (!dictCell) return;
        var sTitle = "Level " + iLevel + " requirements — " +
            fsComputeStepLabel(iStepIndex);
        VaibifyModals.fnShowInfoModal(
            sTitle,
            VaibifyStepRenderer.fsBuildLevelRequirementsListHtml(
                dictCell, iLevel));
    }

    function fdictRegressionWarning(iStepIndex) {
        // The single ⚠-column entry for one row — every warning a
        // step carries, consolidated. For steps it composes the
        // backend level warning (still gated server-side to the
        // lowest non-attained level) with each staleness signal, one
        // plain-English line per reason; red is reserved for genuine
        // failures. The workflow row keeps its backend entry, with
        // the cell-level regression flag as fallback when the poll
        // carries no "-1" entry.
        if (iStepIndex < 0) {
            var dictScope = (_dictWorkflowState.dictStepLevelWarnings
                || {})[String(iStepIndex)] ||
                _fdictDeriveWorkflowScopeWarning();
            if (!dictScope || !dictScope.sWarningSeverity) return null;
            return dictScope;
        }
        var listReasons = _flistStepWarningReasons(iStepIndex);
        if (listReasons.length === 0) return null;
        return {
            sWarningSeverity: _fbStepWarningIsRed(iStepIndex)
                ? "red" : "orange",
            sWarningHint: listReasons.join("\n"),
        };
    }

    function _fdictDeriveWorkflowScopeWarning() {
        // Mirrors the backend rule at workflow scope: warn only when
        // the lowest non-attained level was attained before
        // (bRegression). Severity is orange — red is reserved for
        // failed L1 test axes, which do not exist at workflow scope.
        var iLevel = fiStepNextTargetLevel(-1);
        if (iLevel > 3) return null;
        var dictCell = fdictLevelCellForScope(-1, iLevel);
        if (!dictCell || dictCell.bRegression !== true) return null;
        return {
            iWarningLevel: iLevel,
            sWarningSeverity: "orange",
            sWarningHint: _DICT_LEVEL_CELL_LABELS[iLevel] +
                " was attained before and has regressed",
        };
    }

    function _fnToggleInSet(setTarget, sKey) {
        // Mutate in place — the render context holds these Sets by
        // reference (the shared-Sets rule); never reassign.
        if (setTarget.has(sKey)) {
            setTarget.delete(sKey);
        } else {
            setTarget.add(sKey);
        }
        fnRenderStepList();
    }

    function fnToggleStepsBlockExpand() {
        _dictUiState.bStepsCollapsed = !_dictUiState.bStepsCollapsed;
        _fnPersistStepsCollapsed(_dictUiState.bStepsCollapsed);
        fnRenderStepList();
    }

    function fnToggleProjectBlockExpand() {
        _dictUiState.bProjectBlockCollapsed =
            !_dictUiState.bProjectBlockCollapsed;
        fnRenderStepList();
    }

    function fnToggleBinaryAddForm() {
        _dictUiState.bBinaryAddFormOpen =
            !_dictUiState.bBinaryAddFormOpen;
        fnRenderStepList();
    }

    function fnToggleRequirementGroup(sGroupKey) {
        _fnToggleInSet(
            _dictUiState.setExpandedRequirementGroups, sGroupKey);
    }

    function fnToggleRequirementRow(sReqKey) {
        _fnToggleInSet(
            _dictUiState.setExpandedRequirementRows, sReqKey);
    }

    function fnToggleFileGroup(sGroupKey) {
        _fnToggleInSet(
            _dictUiState.setToggledFileGroups, sGroupKey);
    }

    function fnExpandRequirementRow(sGroupKey, sReqKey) {
        // Idempotent open (never toggle) for PROOF-tab deep links:
        // .add() into the shared Sets in place — reassigning them
        // would detach the render context (the shared-Set trap).
        _dictUiState.setExpandedRequirementGroups.add(sGroupKey);
        _dictUiState.setExpandedRequirementRows.add(sReqKey);
        _dictUiState.bProjectBlockCollapsed = false;
        fnRenderStepList();
    }

    function _fdictDescribeDeterminismScan(dictSafe) {
        /* The scan answers TWO of the three determinism questions'
           worth of evidence, and the toast used to report only one.
           A researcher who ran it to settle the MKL question read
           "none of the known non-determinism patterns found" and
           reasonably concluded the scan had told them nothing —
           because about MKL it had (reported 2026-08-30). The maths
           library reading rides every branch now, in the short form
           the backend authors beside the long one. */
        var sMaths = (dictSafe.dictMathsLibrary || {}).sHeadline || "";
        var sSuffix = sMaths ? " " + sMaths : "";
        var listIssues = dictSafe.listIssues || [];
        var listUnreadable = dictSafe.listUnreadable || [];
        if (listIssues.length > 0) {
            return {sMessage: listIssues.length +
                " non-determinism finding(s): " +
                listIssues.slice(0, 3).join("; ") + sSuffix,
                sType: "warning"};
        }
        if (listUnreadable.length > 0) {
            return {sMessage: "No anti-patterns found, but " +
                listUnreadable.length + " script(s) could not be " +
                "read: " + listUnreadable.join(", ") +
                ". That is not the same as clean." + sSuffix,
                sType: "warning"};
        }
        // Deliberately not "your workflow is deterministic": the scan
        // finds known anti-patterns and cannot see seeded RNG or
        // iteration order.
        return {sMessage: "Scanned " +
            (dictSafe.listScanned || []).length +
            " script(s); none of the known non-determinism patterns " +
            "found. This does not prove determinism — the " +
            "declaration is still yours." + sSuffix,
            sType: "info"};
    }

    function _fsDescribeManifestDelta(dictDelta) {
        /* Regenerating rewrites MANIFEST.sha256 in place. The file is
           tracked, so the history is not lost — but a researcher who
           wanted to know which pinned hashes MOVED found out in a git
           diff or not at all, and silently replacing the record of a
           result is the wrong default for this product. Counts rather
           than paths: a manifest can hold hundreds of entries, and the
           git diff is right there for the detail. Empty string when
           nothing changed, so the ordinary regeneration says nothing
           extra. */
        var dictSafe = dictDelta || {};
        var listParts = [
            [(dictSafe.listChanged || []).length, "changed"],
            [(dictSafe.listAdded || []).length, "added"],
            [(dictSafe.listRemoved || []).length, "removed"],
        ].filter(function (tPart) {
            return tPart[0] > 0;
        }).map(function (tPart) {
            return tPart[0] + " " + tPart[1];
        });
        if (listParts.length === 0) return "";
        return " Manifest entries " + listParts.join(", ") + ".";
    }

    var _DICT_PROJECT_ACTIONS = {
        "capture-binary": {
            sPath: "/binaries/capture",
            fdictBody: function (sArg) {
                return {sBinaryPath: sArg};
            },
            fdictAfterResponse: function (dictResult, sArg) {
                var dictCaptured =
                    (dictResult || {}).dictCaptured || {};
                if (dictCaptured.sSha256) {
                    return {sMessage: "Captured " + sArg +
                        " (version " +
                        (dictCaptured.sVersion || "unknown") + ").",
                        sType: "info"};
                }
                return {sMessage: "No file found at " + sArg +
                    ". Fix the declared path with the Add / update " +
                    "package form at the bottom of the Software " +
                    "section.", sType: "error"};
            },
        },
        "declare-binary": {
            sPath: "/binaries/declare",
            fdictBodyFromElement: _fdictReadBinaryForm,
            /* The cost, named before the write. Both of these
               advance Level 3 by rewriting project.json -- the file
               Level 2 compares against GitHub and Zenodo -- so the
               project drops below Level 2 until it is pushed AND a
               new Zenodo version carries it. A Zenodo version is
               immutable, which is what earns this a modal where a
               step rename gets none. */
            dictConfirm: {
                sTitle: "Declare this package",
                sMessage: "Declaring a package to satisfy Level 3 " +
                    "rewrites project.json, which Level 2 compares " +
                    "against your GitHub mirror and your Zenodo " +
                    "archive. This project sits below Level 2 until " +
                    "you push the change and publish a new Zenodo " +
                    "version carrying it \u2014 Zenodo versions are " +
                    "immutable, so the archived copy cannot be " +
                    "corrected in place.",
            },
            sToast: "Package declared. Now capture its version and " +
                "hash from its row.",
        },
        "scan-determinism": {
            sPath: "/determinism/scan",
            sMethod: "GET",
            fdictAfterResponse: function (dictResult) {
                return _fdictDescribeDeterminismScan(dictResult || {});
            },
        },
        "declare-no-binaries": {
            // The waiver, reachable directly. It used to be settable
            // only as a side effect of removing the last declared
            // package, which no researcher would discover.
            sPath: "/binaries/declare",
            fdictBody: function () {
                return {
                    bNoStandaloneBinaries: true,
                    listDeclaredBinaries: [],
                };
            },
            dictConfirm: {
                sTitle: "Declare no standalone binaries",
                sMessage: "Confirm that this project calls no " +
                    "standalone scientific binaries — only Python " +
                    "packages pinned in requirements.lock. This is a " +
                    "claim Level 3 records; adding a package later " +
                    "retracts it automatically.",
            },
            sToast: "Declared: this project uses no standalone " +
                "binaries.",
        },
        "remove-binary": {
            sPath: "/binaries/declare",
            fdictBody: _fdictReadBinaryRemoval,
            dictConfirm: {
                sTitle: "Remove package",
                sMessage: "Remove this package from the declared " +
                    "software list? Its captured version and hash " +
                    "stay in the environment snapshot until the " +
                    "next regeneration.",
            },
            sToast: "Package removed from the declaration.",
        },
        "verify-dependency-lock": {
            sPath: "/dependencies/verify",
            fdictAfterResponse: function (dictResult) {
                var listProblems =
                    (dictResult || {}).listProblems || [];
                if (listProblems.length === 0) {
                    return {sMessage: "requirements.lock is clean: " +
                        "every dependency pinned by exact version " +
                        "with hashes.", sType: "info"};
                }
                return {sMessage: listProblems.length +
                    " problem(s) in requirements.lock (first: " +
                    listProblems[0] + "). Regenerate the envelope " +
                    "to rebuild it.", sType: "warning"};
            },
        },
        "regenerate-envelope": {
            sPath: "/level3/envelope",
            bOfferCommitAfterGenerate: true,
            sBusyLabel: "Regenerating\u2026",
            sStartNotice: "Regenerating the envelope: compiling the "
                + "dependency lock from the container, capturing the "
                + "environment snapshot, then rewriting the manifest "
                + "over both. This takes a few seconds.",
            /* The server refuses to replace a manifest another
               identity committed -- the author's claim on a clone --
               and names this action; the researcher is asked in the
               server's own words and the request is retried with
               consent. An undetermined owner is refused without an
               action and stays refused. */
            dictRetryOnRefusal: {
                sAction: "confirm-replace-foreign-manifest",
                sTitle: "Replace the author’s manifest?",
                sConfirmLabel: "Replace it",
                dictBody: {bReplaceForeignManifest: true},
            },
            // Asked BEFORE, because the cost lands afterwards and is
            // not recoverable by undoing anything. A fresh capture
            // can differ from the published one, and then the level
            // drops, the remotes no longer match, and a deposit
            // record whose image the new capture does not name is
            // discarded. A researcher met all of that as a silent
            // ten-second wait followed by a drop to Level 1 and a
            // modal about something else (researcher-reported,
            // 2026-09-09). An unchanged capture now rewrites nothing,
            // so this warns about the case that really can cost
            // something.
            dictConfirm: {
                sTitle: "Regenerate the reproducibility envelope",
                sMessage: "Re-capture the manifest, dependency lock " +
                    "and environment snapshot from this container? " +
                    "If the capture differs from what you have " +
                    "published, the project drops a level until you " +
                    "push again and publish a new Zenodo version, " +
                    "and a deposited image the new capture does not " +
                    "name stops counting. An identical capture " +
                    "changes nothing.",
            },
            fdictAfterResponse: function (dictResult) {
                var dictGaps =
                    (dictResult || {}).dictL3ReadinessGaps || {};
                var listStillFailing = [
                    ["bManifestComplete", "manifest"],
                    ["bManifestMatchesTheFiles", "manifest hashes"],
                    ["bDependencyLockHashed", "dependency lock"],
                    ["bEnvironmentDigestPinned", "environment"],
                ].filter(function (t) {
                    return dictGaps[t[0]] === false;
                }).map(function (t) { return t[1]; });
                var sDelta = _fsDescribeManifestDelta(
                    (dictResult || {}).dictManifestDelta);
                if (listStillFailing.length === 0) {
                    /* WHICH files were rewritten, from the tier
                       results, not a fixed list of three. A tier can
                       skip without failing -- a project with no
                       dependency input has no lock to compile -- and
                       naming it anyway told the researcher a file had
                       been refreshed that had not been touched. */
                    var listWritten = Object.keys(
                        (dictResult || {}).dictTierResults || {},
                    ).filter(function (sTier) {
                        return ((dictResult.dictTierResults[sTier] ||
                            {}).bWritten) === true;
                    });
                    return {sMessage: "Envelope regenerated" +
                        (listWritten.length
                            ? " — rewrote " + listWritten.join(", ")
                            : "") + "." + sDelta,
                        sType: "info"};
                }
                // The REASON, not a pointer to a log. Each tier
                // returns why it did not write, the route ships that
                // as dictTierResults -- and no JavaScript read it, so
                // a researcher whose environment snapshot silently
                // failed to rebuild was told to go and read a file
                // they have no path to, while the sentence explaining
                // it sat unused in the response
                // (researcher-reported, 2026-09-09). A regenerate
                // that did not regenerate must say what stopped it.
                var listReasons = [];
                var dictTiers = (dictResult || {}).dictTierResults || {};
                Object.keys(dictTiers).forEach(function (sTier) {
                    var dictTier = dictTiers[sTier] || {};
                    if (dictTier.bWritten === false &&
                            dictTier.sSkipReason) {
                        listReasons.push(
                            sTier + ": " + dictTier.sSkipReason);
                    }
                });
                if (listReasons.length) {
                    return {sMessage: "Envelope regenerated, but " +
                        listReasons.join(" · ") + sDelta,
                        sType: "warning"};
                }
                return {sMessage: "Envelope regenerated, but still " +
                    "failing: " + listStillFailing.join(", ") +
                    ". Nothing reported a reason \u2014 check the " +
                    "hub log." + sDelta,
                    sType: "warning"};
            },
        },
        "verify-manifest": {
            sPath: "/manifest/verify",
            fdictAfterResponse: function (dictResult) {
                var iTotal = (dictResult || {}).iTotal || 0;
                var listBad = (dictResult || {}).listMismatches || [];
                var sProvenance = _fsDescribeManifestProvenance(dictResult);
                if (listBad.length === 0 && sProvenance) {
                    /* A clean count against a manifest this machine
                       rewrote is a self-comparison, and the toast
                       must not read as "the author's bytes". */
                    return {sMessage: "All " + iTotal + " manifest " +
                        "files match their pinned hashes, but " +
                        sProvenance, sType: "warning"};
                }
                if (listBad.length === 0) {
                    return {sMessage: "All " + iTotal + " manifest " +
                        "files match their pinned hashes.",
                        sType: "info"};
                }
                return {sMessage: listBad.length + " of " + iTotal +
                    " files differ from the manifest (first: " +
                    (listBad[0].sPath || listBad[0]) + "). Re-run " +
                    "the project or regenerate the envelope.",
                    sType: "warning"};
            },
        },
        "copy-image-dockerfile": {
            sPath: "/level3/dockerfile",
            bOfferCommitAfterGenerate: true,
            fdictAfterResponse: function (dictResult) {
                // A refusal is a 200 carrying sRefusal, not an error:
                // declining to overwrite the researcher's own
                // Dockerfile is the route working correctly, and
                // surfacing it as a failure would read as a bug.
                var dictSafe = dictResult || {};
                if (dictSafe.sRefusal) {
                    return {sMessage: dictSafe.sRefusal,
                        sType: "warning"};
                }
                if (dictSafe.bManifestRefreshed === true) {
                    return {sMessage: "Dockerfile composed from the " +
                        "image's build chain and pinned in the " +
                        "manifest — the check will pass on the next " +
                        "status poll.", sType: "info"};
                }
                return {sMessage: "Dockerfile was written, but " +
                    "re-pinning the manifest failed — click " +
                    "'Regenerate now' on the Manifest row, then " +
                    "check the hub log.", sType: "warning"};
            },
        },
        "generate-reproduce-script": {
            sPath: "/level3/reproduce-script",
            bOfferCommitAfterGenerate: true,
            fdictAfterResponse: function (dictResult) {
                if ((dictResult || {}).bManifestRefreshed === true) {
                    return {sMessage: "reproduce.sh written and " +
                        "pinned in the manifest — the check will " +
                        "pass on the next status poll.",
                        sType: "info"};
                }
                return {sMessage: "reproduce.sh was written, but " +
                    "re-pinning the manifest failed — click " +
                    "'Regenerate now' on the Manifest row, then " +
                    "check the hub log.", sType: "warning"};
            },
        },
        "verify-l3": {
            sPath: "/level3/verify",
            fnConfirm: fnConfirmLevel3Verification,
            sToast: "Level 3 verification started. The workflow is " +
                "re-run in a throwaway copy of this project; the " +
                "result appears in the Attestation row when it " +
                "completes.",
        },
        "declare-determinism": {
            sPath: "/determinism/declare",
            fdictBodyFromElement: _fdictReadDeterminismForm,
            /* The cost, named before the write. Both of these
               advance Level 3 by rewriting project.json -- the file
               Level 2 compares against GitHub and Zenodo -- so the
               project drops below Level 2 until it is pushed AND a
               new Zenodo version carries it. A Zenodo version is
               immutable, which is what earns this a modal where a
               step rename gets none. */
            dictConfirm: {
                sTitle: "Declare the repeatability rules",
                sMessage: "Declaring the repeatability rules to " +
                    "satisfy Level 3 rewrites project.json, which " +
                    "Level 2 compares against your GitHub mirror " +
                    "and your Zenodo archive. This project sits " +
                    "below Level 2 until you push the change and " +
                    "publish a new Zenodo version carrying it " +
                    "\u2014 Zenodo versions are immutable, so the " +
                    "archived copy cannot be corrected in place.",
            },
            sToast: "Reproducibility rules declared.",
        },
        "delete-determinism": {
            sPath: "/determinism",
            sMethod: "DELETE",
            dictConfirm: {
                sTitle: "Delete reproducibility rules",
                sMessage: "Remove the declared repeatability rules " +
                    "from project.json? Level 3 requires a " +
                    "declaration, so you will need to declare again.",
            },
            sToast: "Reproducibility rules deleted.",
        },
        "answer-environment-archive": {
            sPath: "/environment-archive/answer",
            fdictBodyFromElement: _fdictReadEnvironmentArchiveForm,
            sToast: "Environment-archive answer recorded.",
        },
        "clear-environment-archive-answer": {
            sPath: "/environment-archive/answer",
            fdictBody: function () {
                return {sAnswer: "cleared"};
            },
            dictConfirm: {
                sTitle: "Clear the environment-archive answer",
                sMessage: "Return this question to unanswered? " +
                    "Answering it is a Level 2 requirement, so the " +
                    "project drops back below Level 2 until you " +
                    "answer again. Nothing already deposited is " +
                    "removed.",
            },
            sToast: "Environment-archive answer cleared.",
        },
        "deposit-environment-archive": {
            sPath: "/environment-archive/deposit",
            dictConfirm: {
                sTitle: "Deposit the container image",
                sMessage: "Vaibify will save this project's " +
                    "container image, compress it, and publish it to " +
                    "Zenodo under a new DOI. Deleting a Zenodo " +
                    "deposit later requires contacting Zenodo, and " +
                    "the DOI is tombstoned rather than removed. The " +
                    "image is " +
                    "usually several gigabytes, so this takes " +
                    "minutes and needs that much free disk space " +
                    "while it runs.",
            },
            sToast: "Depositing the container image. Progress " +
                "appears on the Environment archive row; the DOI is " +
                "recorded when it finishes.",
        },
        "reconcile-promotion": {
            sAbsolutePath: "/api/workflow/{sContainerId}/promotions/" +
                "{sPromotionId}/reconcile",
            fdictAfterResponse: function (dictResult, sArg) {
                /* Zenodo's own verdict, held for the row to render.
                   The row offers nothing until this lands: vaibify
                   does not know what a draft holds, and offering an
                   action on a guess is how a real DOI gets thrown
                   away. */
                VaibifyWorkflowRequirements.fnRecordPromotionOutcome(
                    sArg, dictResult);
                return {
                    sMessage: dictResult.sMessage || "",
                    sType: dictResult.listActions &&
                        dictResult.listActions.length
                        ? "info" : "warning",
                };
            },
        },
        "resume-promotion": {
            sAbsolutePath: "/api/workflow/{sContainerId}/promotions/" +
                "{sPromotionId}/resume",
            dictConfirm: {
                sTitle: "Finish publishing this deposit",
                sMessage: "Zenodo already holds every file this " +
                    "promotion intended; only the publish is left. " +
                    "Publishing MINTS the permanent DOI — deleting " +
                    "the record later requires contacting Zenodo, " +
                    "and the DOI is tombstoned rather than removed.",
            },
            sToast: "Published. The DOI is recorded on the row.",
        },
        "adopt-promotion": {
            sAbsolutePath: "/api/workflow/{sContainerId}/promotions/" +
                "{sPromotionId}/adopt",
            dictConfirm: {
                sTitle: "Record this published DOI",
                sMessage: "This promotion finished on Zenodo while " +
                    "vaibify was not watching: the record is " +
                    "published and holds the intended files. " +
                    "Recording it writes the new DOI into this " +
                    "project, replacing the deposit currently on " +
                    "file — which keeps resolving to exactly what it " +
                    "already holds.",
            },
            sToast: "The published DOI was recorded.",
        },
        "discard-promotion": {
            sAbsolutePath: "/api/workflow/{sContainerId}/promotions/" +
                "{sPromotionId}",
            sMethod: "DELETE",
            dictConfirm: {
                sTitle: "Discard this interrupted promotion",
                sMessage: "Vaibify will delete the unpublished draft " +
                    "on Zenodo and forget this record. Nothing " +
                    "published is touched and no DOI is lost — a " +
                    "draft has none. You can promote again " +
                    "afterwards.",
            },
            sToast: "The interrupted promotion was discarded.",
        },
        "promote-project-deposit": {
            /* Not under /api/workflow/{id}: the project deposit's
               routes live under /api/zenodo/{id}, beside the archive
               flow it reuses. */
            sAbsolutePath: "/api/zenodo/{sContainerId}/promote",
            // A promotion collects, uploads and verifies a whole
            // publication -- minutes of nothing on screen without
            // these. A researcher watched exactly that silence and
            // asked whether anything was running (2026-09-16).
            sBusyLabel: "Publishing\u2026",
            sStartNotice: "Publishing on production Zenodo: " +
                "collecting the publication files, uploading them " +
                "to a new record, and verifying the deposit. A " +
                "large project takes minutes; the verdict arrives " +
                "as a toast and the Zenodo row updates.",
            fdictBody: function () {
                return {listFilePaths: []};
            },
            dictConfirm: {
                sTitle: "Publish this project on production Zenodo",
                sMessage: "This is NOT a copy of your sandbox " +
                    "deposit. Zenodo's sandbox and production are " +
                    "separate systems and nothing transfers between " +
                    "them, so vaibify publishes your project's " +
                    "current files on zenodo.org as a NEW record " +
                    "with its own permanent DOI — the DOI will name " +
                    "your tree as it is now, not as it was when you " +
                    "deposited to the sandbox. This IS the " +
                    "publication: there is nothing to republish " +
                    "afterwards. Deleting a production deposit later " +
                    "requires contacting Zenodo, and the DOI is " +
                    "tombstoned rather than removed. Your sandbox " +
                    "record stays where it is.",
            },
            fdictAfterResponse: fdictDescribePromoteOutcome,
        },
        "start-new-zenodo-concept": {
            sAbsolutePath:
                "/api/zenodo/{sContainerId}/start-new-concept",
            dictConfirm: {
                sTitle: "Start a new Zenodo concept",
                sMessage: "Your next Zenodo publish will create a " +
                    "FRESH record rather than a new version of the " +
                    "one on file, so the two will not be linked as a " +
                    "version chain. The existing deposit is " +
                    "untouched and its DOI keeps resolving to " +
                    "exactly what it already holds; vaibify moves " +
                    "its identifiers into a superseded note so you " +
                    "can still see them. Use this when your recorded " +
                    "deposit and the instance you want to publish to " +
                    "are on different Zenodo sites.",
            },
            sToast: "The recorded deposit was retired. Your next " +
                "publish starts a new concept.",
        },
        "promote-environment-archive": {
            sPath: "/environment-archive/promote",
            dictConfirm: {
                sTitle: "Deposit this image on production Zenodo",
                sMessage: "Zenodo's sandbox and production are " +
                    "separate systems and nothing transfers between " +
                    "them, so this publishes the image AGAIN on " +
                    "zenodo.org under a NEW permanent DOI. The " +
                    "sandbox record stays where it is until Zenodo " +
                    "clears it. Deleting a production deposit later " +
                    "requires contacting Zenodo, and the DOI is " +
                    "tombstoned rather than removed. This is a step " +
                    "on the way, not the last one: it rewrites " +
                    ".vaibify/environment.json, so push the envelope " +
                    "and publish your project deposit afterwards. " +
                    "The image is usually several gigabytes, so this " +
                    "takes minutes and needs that much free disk " +
                    "space while it runs.",
            },
            dictCredentialPrompt: {
                sError: "PRODUCTION-TOKEN-MISSING",
                sInstance: "production",
            },
            sToast: "Depositing the image on production Zenodo. " +
                "Progress appears on the Environment archive row; " +
                "the new DOI is recorded when it finishes.",
        },
        "remove-ai-model": {
            sPath: "/ai-models/remove",
            fdictBody: function (sArg) {
                try {
                    return JSON.parse(sArg);
                } catch (error) {
                    return {};
                }
            },
            dictConfirm: {
                sTitle: "Remove model declaration",
                sMessage: "Remove this AI model from the provenance " +
                    "declaration? An undeclared model drops the " +
                    "project below Level 2 until re-declared.",
            },
            // The declaration editor holds a card per model; the saved
            // list returned here re-renders it, so a deleted model does
            // not linger on screen as if it were still declared.
            fdictAfterResponse: function (dictResult) {
                VaibifyAiModelConfig.fnApplyDeclaredModels(
                    (dictResult || {}).listDeclaredModels);
                return {sMessage: "Model declaration removed.",
                        sType: "info"};
            },
        },
    };

    function _fdictReadBinaryForm(elButton) {
        // Merge the form entry into the existing declarations —
        // an entry with the same path replaces the old one, so the
        // form both adds missed packages and fixes stale paths.
        var elForm = elButton.closest(".binary-add-form");
        if (!elForm) return null;
        var sPath = (elForm.querySelector(".binary-form-path")
            .value || "").trim();
        var sPurpose = (elForm.querySelector(".binary-form-purpose")
            .value || "").trim();
        var sVersion = (elForm.querySelector(".binary-form-version")
            .value || "").trim();
        if (!sPath || !sPurpose || !sVersion) {
            fnShowToast(
                "All three fields are needed: path, purpose, and " +
                "expected version.", "warning");
            return null;
        }
        var listExisting = ((_dictWorkflowState.dictWorkflow || {})
            .listDeclaredBinaries || []).filter(function (d) {
                return d && d.sBinaryPath !== sPath;
            });
        listExisting.push({
            sBinaryPath: sPath,
            sPurpose: sPurpose,
            sExpectedVersion: sVersion,
        });
        return {
            bNoStandaloneBinaries: false,
            listDeclaredBinaries: listExisting,
        };
    }

    function _fdictReadDeterminismForm(elButton) {
        /* Read ONE question's answer from the form the button sits
           in. Each question is now its own row with its own Save, so
           a body carries one answer and the endpoint's key merge
           leaves the other two alone.

           An unanswered form submits NOTHING rather than a default.
           The previous version sent `bAcceptBlasVariance: false` for
           an unticked box, which recorded a block that satisfied no
           gate while turning the row green — the defect this whole
           change came from. A researcher who saves without choosing
           now gets told to choose. */
        var elForm = elButton.closest(".determinism-form");
        if (!elForm) return null;
        var elChecked = elForm.querySelector(
            ".determinism-answer:checked");
        if (!elChecked) {
            fnShowToast(
                "Choose one of the answers before saving.", "error");
            return null;
        }
        return _fdictBuildDeterminismBody(elForm, elChecked.value);
    }

    function _fdictBuildDeterminismBody(elForm, sAnswer) {
        // The answer, plus the value that answer needs. A value is
        // sent as null when the chosen answer does not want one, so a
        // thread count survives no longer than the decision to pin.
        var dictBody = {};
        dictBody[elForm.dataset.answerKey] = sAnswer;
        var sValueKey = elForm.dataset.valueKey || "";
        if (!sValueKey) return dictBody;
        var elValue = elForm.querySelector(".determinism-value");
        var sRaw = elValue ? String(elValue.value).trim() : "";
        if (sRaw === "" || sAnswer !== "pinned") {
            dictBody[sValueKey] = null;
            return dictBody;
        }
        dictBody[sValueKey] = (sValueKey === "dOmpNumThreads")
            ? parseInt(sRaw, 10) : sRaw;
        return dictBody;
    }

    function _fdictReadEnvironmentArchiveForm(elButton) {
        // Two of the three answers come from here; the third
        // ("archived") is never sent, because it is not a claim a
        // caller may assert — it is what the backend writes once a
        // deposit has actually been published. The route refuses it.
        var elForm = elButton.closest(".environment-archive-form");
        if (!elForm) return null;
        var elChecked = elForm.querySelector(
            ".environment-archive-answer:checked");
        if (!elChecked) {
            fnShowToast(
                "Choose one of the answers before saving.", "error");
            return null;
        }
        var dictBody = {sAnswer: elChecked.value};
        if (elChecked.value !== "referenced") return dictBody;
        var elDoi = elForm.querySelector(".environment-archive-doi");
        dictBody.sVersionDoi = elDoi
            ? String(elDoi.value).trim() : "";
        if (!dictBody.sVersionDoi) {
            fnShowToast(
                "Enter the version DOI of the deposit that holds " +
                "this image.", "error");
            return null;
        }
        return dictBody;
    }

    function _fdictReadBinaryRemoval(sBinaryPath) {
        // Re-declare the list minus the removed entry.
        var listRemaining = ((_dictWorkflowState.dictWorkflow || {})
            .listDeclaredBinaries || []).filter(function (d) {
                return d && d.sBinaryPath !== sBinaryPath;
            });
        return {
            bNoStandaloneBinaries: listRemaining.length === 0,
            listDeclaredBinaries: listRemaining,
        };
    }

    function _fsDescribeManifestProvenance(dictResult) {
        /* Which manifest the check read, when that is not the one the
           author committed. Empty when the working copy IS the
           committed one (or nothing is tracked), so the ordinary
           "all match" stands. Undetermined is said, never assumed. */
        var dictPayload = dictResult || {};
        if (dictPayload.bManifestDiffersFromHead === true) {
            return "the manifest itself differs from the committed " +
                "one, so this compares your outputs with a manifest " +
                "this machine wrote. Run `git diff HEAD -- " +
                "MANIFEST.sha256` to see what moved, or `git checkout " +
                "-- MANIFEST.sha256` to check against the author's.";
        }
        if (dictPayload.bManifestDiffersFromHead === null &&
                dictPayload.sManifestOwnership === "undetermined") {
            return "git could not say whether the manifest is the " +
                "committed one.";
        }
        return "";
    }

    async function fnRunProjectAction(sAction, sArg, elButton) {
        // Runs a project action in place from the expanded
        // blocks (capture/declare binaries, regenerate the envelope,
        // verify the manifest, generate reproduce.sh, declare/delete
        // determinism, verify Level 3), then refreshes so the status
        // lights update. Destructive actions confirm first; actions
        // with a response-aware formatter report what actually
        // happened rather than a fixed message.
        var dictAction = _DICT_PROJECT_ACTIONS[sAction];
        var sContainerId = _dictSessionState.sContainerId;
        if (!dictAction || !sContainerId) return;
        if (dictAction.dictConfirm) {
            var dictNoConfirm = _fdictFreezeFormBody(dictAction, elButton);
            if (!dictNoConfirm) return;
            fnShowConfirmModal(
                dictAction.dictConfirm.sTitle,
                dictAction.dictConfirm.sMessage,
                function () {
                    _fnExecuteProjectAction(
                        dictNoConfirm, sContainerId, sArg, elButton);
                });
            return;
        }
        // An action whose warning is too specific for the generic
        // dictConfirm shape brings its own opener. It is a FUNCTION
        // rather than more fields so the same warning can be raised
        // from a second entry point -- the PROOF tab has its own Verify
        // button, and two hand-written copies of a safety warning are
        // two warnings that drift.
        if (dictAction.fnConfirm) {
            var dictUnconfirmed = Object.assign({}, dictAction);
            delete dictUnconfirmed.fnConfirm;
            dictAction.fnConfirm(function () {
                _fnExecuteProjectAction(
                    dictUnconfirmed, sContainerId, sArg, elButton);
            }, elButton);
            return;
        }
        await _fnExecuteProjectAction(
            dictAction, sContainerId, sArg, elButton);
    }

    function _fdictFreezeFormBody(dictAction, elButton) {
        /* Read a form-backed body at CLICK time, before the modal.
           The poll re-renders the Project block on its own cadence, so
           the row holding the form can be replaced while the
           researcher is reading the confirmation -- and a body read
           from a detached element comes back empty, which the
           executor treats as "nothing to send". The declaration would
           then vanish with no error and no toast, which is the
           silent-failure class this product exists to avoid. Returns
           the action to run, or null when the form itself refused. */
        var dictReady = Object.assign({}, dictAction);
        delete dictReady.dictConfirm;
        if (!dictAction.fdictBodyFromElement) return dictReady;
        var oBody = dictAction.fdictBodyFromElement(elButton);
        if (!oBody) return null;
        delete dictReady.fdictBodyFromElement;
        dictReady.fdictBody = function () { return oBody; };
        return dictReady;
    }

    async function _fnExecuteProjectAction(
        dictAction, sContainerId, sArg, elButton
    ) {
        var oBody = {};
        if (dictAction.fdictBodyFromElement) {
            oBody = dictAction.fdictBodyFromElement(elButton);
            if (!oBody) return;
        } else if (dictAction.fdictBody) {
            oBody = dictAction.fdictBody(sArg);
        }
        /* Most project actions hang off /api/workflow/{id}; a few
           live under the service prefix their flow already owns. An
           entry declares one or the other, never both. */
        var sUrl = dictAction.sAbsolutePath
            ? dictAction.sAbsolutePath
                .replace("{sContainerId}",
                    encodeURIComponent(sContainerId))
                .replace("{sPromotionId}",
                    encodeURIComponent(sArg || ""))
            : "/api/workflow/" + sContainerId + dictAction.sPath;
        /* A control that reaches the network before it can show
           anything must say so. Regenerating the envelope compiles a
           lock and captures a container, which is tens of seconds of
           nothing: the researcher clicked, watched an unchanged
           button, and learned it had worked when a row changed
           colour (researcher-reported, 2026-09-16). The busy hold and
           the notice are the same discipline as the Project block's
           first paint -- say what is happening, rather than leave a
           wait to be interpreted. */
        var fnReleaseButton = _ffnHoldButtonBusy(
            elButton, dictAction.sBusyLabel || "",
        );
        if (dictAction.sStartNotice) {
            fnShowToast(dictAction.sStartNotice, "info");
        }
        try {
            var dictResult;
            if (dictAction.sMethod === "DELETE") {
                dictResult = await VaibifyApi.fnDelete(sUrl);
            } else if (dictAction.sMethod === "GET") {
                // Read-only actions (the determinism scan) ask a
                // question rather than changing anything; posting to
                // a GET route answers 405.
                dictResult = await VaibifyApi.fdictGet(sUrl);
            } else {
                dictResult = await VaibifyApi.fdictPost(sUrl, oBody);
            }
            if (dictAction.fdictAfterResponse) {
                var dictOutcome = dictAction.fdictAfterResponse(
                    dictResult, sArg);
                fnShowToast(dictOutcome.sMessage, dictOutcome.sType);
            } else {
                fnShowToast(dictAction.sToast, "info");
            }
            if (dictAction.bOfferCommitAfterGenerate) {
                // The generated envelope/reproduce.sh files are not
                // auto-committed; offer to commit them now rather than
                // leaving them silently untracked (blocking L2).
                await VaibifyManifestCheck.fbOfferCommitAfterGenerate(
                    sContainerId);
            }
        } catch (error) {
            if (_fbRefusalNamesARetry(dictAction, error)) {
                fnReleaseButton();
                _fnOfferRetryWithConsent(
                    dictAction, error, sContainerId, sArg, elButton);
                return;
            }
            if (_fbRefusalNeedsACredential(dictAction, error)) {
                fnReleaseButton();
                await _fnAskForCredentialThenRetry(
                    dictAction, error, sContainerId, sArg, elButton);
                return;
            }
            fnShowToast(
                "Action failed: " +
                ((error && error.message) ? error.message : error),
                "error");
        } finally {
            // Released on EVERY path, including the two that hand off
            // to a retry above: a button left disabled by a refusal
            // the researcher then consents to is a dead control.
            fnReleaseButton();
        }
        // Fire an immediate file-status poll so the block's status
        // lights reflect the action right away instead of waiting for
        // the next scheduled tick. (fnRefreshWorkflowData requires a
        // connect payload — calling it bare threw and silently
        // skipped this refresh.)
        VaibifyPolling.fnStartFilePolling(sContainerId);
    }

    function _fsDistillZenodoReason(sReason) {
        /* A refusal's reason arrives as whatever the container
           script printed: a Python traceback whose last line holds
           the message, or an error body that is a whole HTML page
           (Zenodo's 504 is one). Neither is a sentence a researcher
           should have to parse -- distill to the claim. */
        var sDistilled = String(sReason || "");
        if (sDistilled.indexOf("Traceback") !== -1) {
            var listLines = sDistilled.trim().split(/\n+/);
            sDistilled = listLines[listLines.length - 1];
        }
        return sDistilled.replace(/<[^>]*>/g, " ")
            .replace(/\s+/g, " ").trim();
    }

    function fdictDescribePromoteOutcome(dictResult) {
        /* The promote route returns refusals as 200s with
           ``bSuccess: false`` and the reason in ``sMessage``. The
           old static toast said "Publishing on production Zenodo"
           over BOTH answers, so a refused publish looked like a
           silent success that never arrived -- a researcher asked
           "is it trying to archive?" over a refusal already parsed
           and thrown away (2026-09-16). */
        if (dictResult && dictResult.bSuccess === false) {
            var sReason = _fsDistillZenodoReason(
                dictResult.sMessage || dictResult.sError ||
                "no reason was given");
            /* Zenodo's overload answers -- a 5xx from their
               gateway, wrapped in an HTML page -- are about THEIR
               servers, and a researcher shown a raw gateway page
               reasonably reads it as their own failure. Say whose
               problem it is and that nothing is left behind: the
               orphan-draft cleanup runs before this answer, and a
               504 on publish that had actually landed would have
               made that cleanup refuse. */
            if (/\b(502|503|504)\b|gateway time.?out|service unavailable/i
                    .test(sReason)) {
                return {
                    sMessage: "Zenodo's servers are overloaded and " +
                        "timed out before the publish could start " +
                        "or finish. This is on Zenodo's side: " +
                        "nothing was published, no DOI was minted, " +
                        "and no draft was left behind, so trying " +
                        "again is safe. Check status.zenodo.org and " +
                        "retry when it is green.",
                    sType: "error",
                };
            }
            return {
                sMessage: "Zenodo refused the publish: " +
                    VaibifyUtilities.fsSanitizeErrorForUser(sReason),
                sType: "error",
            };
        }
        return {
            sMessage: "Published on production Zenodo \u2014 the " +
                "new DOI is recorded. Run Verify now on the Zenodo " +
                "row to light its cells.",
            sType: "success",
        };
    }

    function _fbRefusalNeedsACredential(dictAction, error) {
        /* A 409 naming a credential this host does not hold. Distinct
           from a retry-with-consent: nothing is being reconsidered,
           a token is simply missing, and the prompt must not record
           the instance as where this project publishes. */
        var dictPrompt = dictAction.dictCredentialPrompt;
        return Boolean(dictPrompt && error && error.iStatus === 409 &&
            error.dictDetail &&
            error.dictDetail.sError === dictPrompt.sError);
    }

    async function _fnAskForCredentialThenRetry(
        dictAction, error, sContainerId, sArg, elButton) {
        /* The server's own sentence says which credential is missing;
           the opener resolves once the researcher has stored one (or
           declined), and only then is the action retried. A second
           refusal is reported rather than asked again. */
        fnShowToast(
            error.dictDetail.sMessage || error.message || "", "warning");
        var bStored = await VaibifySyncManager
            .fpromiseConnectZenodoCredentialOnly(
                dictAction.dictCredentialPrompt.sInstance);
        if (!bStored) return;
        var dictOnce = Object.assign({}, dictAction);
        delete dictOnce.dictCredentialPrompt;
        await _fnExecuteProjectAction(
            dictOnce, sContainerId, sArg, elButton);
    }

    function _fbRefusalNamesARetry(dictAction, error) {
        /* A 409 whose detail carries the action this table entry
           declared it can retry on, with consent. Any other refusal,
           and any refusal on an entry that declared none, is reported
           as it is. */
        var dictRetry = dictAction.dictRetryOnRefusal;
        return Boolean(dictRetry && error && error.iStatus === 409 &&
            error.dictDetail &&
            error.dictDetail.sAction === dictRetry.sAction);
    }

    function _fnOfferRetryWithConsent(
        dictAction, error, sContainerId, sArg, elButton) {
        /* The server's own sentence is the question; consent retries
           the same action with the declared body merged in, and a
           second refusal is reported rather than asked again. */
        var dictRetry = dictAction.dictRetryOnRefusal;
        var sMessage = error.dictDetail.sMessage || error.message || "";
        fnShowConfirmModal(
            dictRetry.sTitle, sMessage,
            async function () {
                var dictConsented = Object.assign({}, dictAction, {
                    dictRetryOnRefusal: null,
                    fdictBody: function () {
                        var oBase = dictAction.fdictBody
                            ? dictAction.fdictBody(sArg) : {};
                        return Object.assign({}, oBase, dictRetry.dictBody);
                    },
                    fdictBodyFromElement: null,
                });
                await _fnExecuteProjectAction(
                    dictConsented, sContainerId, sArg, elButton);
            },
            {sConfirmLabel: dictRetry.sConfirmLabel || "Confirm"}
        );
    }

    function fnSetCachedProofLevel(iLevel) {
        _dictWorkflowState.iCachedProofLevel =
            typeof iLevel === "number" ? iLevel : null;
    }

    function fdictBlockerCountsByLevel() {
        // Section G legend panel: live counts of active blockers per
        // ladder rung. The panel renders one count per level so the
        // researcher can sanity-check the header progression.
        return {
            iLevel1: _dictWorkflowState.iL1BlockerCount || 0,
            iLevel2: _dictWorkflowState.iL2BlockerCount || 0,
            iLevel3: _dictWorkflowState.iL3BlockerCount || 0,
        };
    }

    function fdictBlockerGlyphCatalog() {
        // Section G legend panel: the authoritative glyph dicts per
        // ladder rung. The legend generates its criterion rows from
        // these so the panel cannot drift from the rendered glyphs.
        return {
            iLevel1: _DICT_BLOCKER_CRITERION_GLYPHS,
            iLevel2: _DICT_L2_BLOCKER_GLYPHS,
            iLevel3: _DICT_L3_BLOCKER_GLYPHS,
            dictAxisSubStates: _DICT_AXIS_SUBSTATE_GLYPHS,
        };
    }

    function fbAnyDepTimingStale(iStep) {
        var listDeps = flistGetStepDependencies(iStep);
        for (var i = 0; i < listDeps.length; i++) {
            var iDep = listDeps[i];
            if (iDep === iStep) continue;
            var tStates = ftComputeDepAxisStates(iStep, iDep);
            if (tStates.sTiming === "failed") return true;
        }
        return false;
    }

    function fdictGetVerification(step) {
        return step.dictVerification || {
            sUnitTest: "untested", sUser: "untested",
        };
    }

    function fdictGetTests(step) {
        if (step.dictTests) return step.dictTests;
        var listOldCommands = step.saTestCommands || [];
        return {
            dictQualitative: {saCommands: [], sFilePath: ""},
            dictQuantitative: {
                saCommands: [], sFilePath: "", sStandardsPath: "",
            },
            dictIntegrity: {
                saCommands: listOldCommands.slice(), sFilePath: "",
            },
            listUserTests: [],
        };
    }

    function fsGetCategoryState(step, sCategory) {
        var dictVerify = fdictGetVerification(step);
        var sKey = "s" + sCategory.charAt(0).toUpperCase() +
            sCategory.slice(1);
        return dictVerify[sKey] || "untested";
    }

    var fsTestCategoryLabel = VaibifyUtilities.fsTestCategoryLabel;

    /* "unnecessary" is the backend's value for a category that defines
       no commands. It is a GREEN value there \u2014 stepPredicates and
       truthDerivation both count it toward Level 1 \u2014 so rendering it
       through the `|| "Untested"` fallback stated the opposite of what
       the backend had derived, and a researcher reading "Untested" had
       no way to tell a real gap from a category that is legitimately
       N/A. It is shown grey rather than green: nothing was proven
       here, there was simply nothing to prove. */
    function fsVerificationStateLabel(sState) {
        var dictLabels = {
            passed: "Passed", failed: "Failed",
            untested: "Untested", error: "Error",
            stale: "Stale",
            unnecessary: "N/A",
            "passed-from-marker": "Passed",
            "outputs-changed": "Stale",
            "outputs-missing": "Missing",
        };
        return dictLabels[sState] || "Untested";
    }

    function fsVerificationStateIcon(sState) {
        var dictIcons = {
            passed: "\u2713", failed: "\u2717",
            untested: "\u2014", error: "\u2717",
            stale: "\u26A0",
            unnecessary: "\u25CB",
            "passed-from-marker": "\u2713",
            "outputs-changed": "\u26A0",
            "outputs-missing": "\u2717",
        };
        return dictIcons[sState] || "\u2014";
    }

    function fsetGetExpandedCategory(sCategory) {
        var dictSets = {
            qualitative: _dictUiState.setExpandedQualitative,
            quantitative: _dictUiState.setExpandedQuantitative,
            integrity: _dictUiState.setExpandedIntegrity,
        };
        return dictSets[sCategory] || new Set();
    }

    function fsEffectiveTestState(step) {
        var dictVerify = fdictGetVerification(step);
        var dictTests = fdictGetTests(step);
        var listCategories = [
            "qualitative", "quantitative", "integrity"];
        var bAnyCommands = false;
        var bAllPassed = true;
        var bAnyFailed = false;
        for (var i = 0; i < listCategories.length; i++) {
            var sCategory = listCategories[i];
            var sCatKey = "dict" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var dictCat = dictTests[sCatKey] || {};
            if ((dictCat.saCommands || []).length === 0) continue;
            bAnyCommands = true;
            var sKey = "s" +
                sCategory.charAt(0).toUpperCase() +
                sCategory.slice(1);
            var sState = dictVerify[sKey] || "untested";
            if (sState === "failed" || sState === "error") {
                bAnyFailed = true;
            }
            if (sState !== "passed") bAllPassed = false;
        }
        if (!bAnyCommands) return "untested";
        if (bAnyFailed) return "failed";
        if (bAllPassed) return "passed";
        return "untested";
    }

    function fbDirectoryOverlap(dictEarlierStep, sCurrentDirectory) {
        if (!dictEarlierStep.sDirectory) return false;
        var sPrefix = sCurrentDirectory + "/";
        var listFileKeys = ["saOutputDataFiles", "saPlotFiles"];
        for (var iKey = 0; iKey < listFileKeys.length; iKey++) {
            var listFiles = dictEarlierStep[listFileKeys[iKey]] || [];
            for (var iFile = 0; iFile < listFiles.length; iFile++) {
                if (listFiles[iFile].indexOf("{") !== -1) continue;
                var sJoined = dictEarlierStep.sDirectory + "/" + listFiles[iFile];
                if (sJoined.indexOf(sPrefix) === 0) return true;
            }
        }
        return false;
    }

    function flistGetStepDependencies(iStep) {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return [];
        if (_dictStepDepsByIndex[iStep] !== undefined) {
            return _dictStepDepsByIndex[iStep];
        }
        var listDeps = _flistComputeStepDependencies(iStep);
        _dictStepDepsByIndex[iStep] = listDeps;
        _fnIndexStepFilesIntoReverseMap(iStep);
        return listDeps;
    }

    function _flistComputeStepDependencies(iStep) {
        var listSteps = _dictWorkflowState.dictWorkflow.listSteps;
        var step = listSteps[iStep];
        var setDeps = {};
        var listArrays = ["saDataCommands", "saPlotCommands",
            "saTestCommands", "saOutputDataFiles", "saPlotFiles",
            "saDependencies", "saSetupCommands", "saCommands"];
        listArrays.forEach(function (sKey) {
            (step[sKey] || []).forEach(function (sVal) {
                var rRef = /\{Step(\d+)\.\w+\}/g;
                var match;
                while ((match = rRef.exec(sVal)) !== null) {
                    var iDep = parseInt(match[1]) - 1;
                    if (iDep !== iStep) setDeps[iDep] = true;
                }
            });
        });
        if (step.sDirectory) {
            for (var j = 0; j < iStep; j++) {
                if (fbDirectoryOverlap(listSteps[j], step.sDirectory))
                    setDeps[j] = true;
            }
        }
        (step.saSourceCodeDeps || []).forEach(function (iDepNumber) {
            var iDep = iDepNumber - 1;
            if (iDep >= 0 && iDep !== iStep) setDeps[iDep] = true;
        });
        return Object.keys(setDeps).map(Number).sort(
            function (a, b) { return a - b; }
        );
    }

    function _fnIndexStepFilesIntoReverseMap(iStep) {
        // Populate _dictStepIndexByFilePath so the badge-driven
        // partial render can map "this file's badge changed" to
        // "this step's card needs re-rendering" in O(1). Must cover
        // every file family that fsRenderStepItem reads badge state
        // for: data, plot, step scripts, test standards, and the AI
        // declaration file (its commit/remove buttons gate on the
        // git badge).
        var step = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (!step) return;
        var listFileKeys = ["saOutputDataFiles", "saPlotFiles",
            "saStepScripts", "saTestStandards"];
        listFileKeys.forEach(function (sKey) {
            (step[sKey] || []).forEach(function (sFile) {
                if (sFile) _dictStepIndexByFilePath[sFile] = iStep;
            });
        });
        var sDeclarationFile = (step.sDeclarationFile || "").trim();
        if (sDeclarationFile) {
            _dictStepIndexByFilePath[sDeclarationFile] = iStep;
        }
    }

    function fbStepFullyPassing(iStep, dictVisited) {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps[iStep]) {
            return false;
        }
        if (dictVisited[iStep]) return dictVisited[iStep] === "pass";
        dictVisited[iStep] = "checking";
        var step = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var dictVerify = fdictGetVerification(step);
        var bInteractive = fbStepIsInteractive(step);
        var bPlotOnly = (step.saDataCommands || []).length === 0;
        if (bInteractive) {
            if (dictVerify.sUser !== "passed") {
                dictVisited[iStep] = "fail";
                return false;
            }
        } else if (bPlotOnly) {
            if (dictVerify.sUser !== "passed") {
                dictVisited[iStep] = "fail";
                return false;
            }
        } else {
            var sTestState = fsEffectiveTestState(step);
            if (sTestState !== "passed" ||
                dictVerify.sUser !== "passed") {
                dictVisited[iStep] = "fail";
                return false;
            }
        }
        var listDeps = flistGetStepDependencies(iStep);
        for (var i = 0; i < listDeps.length; i++) {
            if (!fbStepFullyPassing(listDeps[i], dictVisited)) {
                dictVisited[iStep] = "fail";
                return false;
            }
        }
        dictVisited[iStep] = "pass";
        return true;
    }

    function fbAnyUpstreamModified(iStep) {
        var listDeps = flistGetStepDependencies(iStep);
        for (var i = 0; i < listDeps.length; i++) {
            var dictV = fdictGetVerification(
                _dictWorkflowState.dictWorkflow.listSteps[listDeps[i]]);
            var listMod = dictV.listModifiedFiles || [];
            if (listMod.length > 0) return true;
        }
        return false;
    }

    function fsComputeDepsState(iStep) {
        var listDeps = flistGetStepDependencies(iStep);
        if (listDeps.length === 0) return "none";
        var bAnyFailed = false;
        var bAnyUnknown = false;
        for (var i = 0; i < listDeps.length; i++) {
            var iDep = listDeps[i];
            if (iDep === iStep) continue;
            var tStates = ftComputeDepAxisStates(iStep, iDep);
            if (tStates.sStepStatus === "failed" ||
                    tStates.sTiming === "failed") {
                bAnyFailed = true;
            } else if (tStates.sTiming === "unknown") {
                bAnyUnknown = true;
            }
        }
        if (bAnyFailed) return "failed";
        if (bAnyUnknown) return "untested";
        return "passed";
    }

    function ftComputeDepAxisStates(iStep, iDep) {
        var dictVisited = {};
        var bStepStatus = fbStepFullyPassing(iDep, dictVisited);
        var dictMax = _dictWorkflowState.dictOutputMtimes || {};
        var iDepMtime = parseInt(dictMax[String(iDep)] || "0", 10);
        var iMyOutputMtime = parseInt(
            dictMax[String(iStep)] || "0", 10);
        var sTiming = _fsComputeDepLineageTiming(
            iStep, iDep, iDepMtime, iMyOutputMtime,
        );
        var dictTestSrc =
            _dictWorkflowState.dictTestSourceMtimeByStep || {};
        var sTestSrc = dictTestSrc[String(iDep)];
        var iDepTestSrcMtime = sTestSrc !== undefined
            ? parseInt(sTestSrc, 10) : null;
        return {
            sStepStatus: bStepStatus ? "passed" : "failed",
            sTiming: sTiming,
            iDepMtime: iDepMtime,
            iMyOutputMtime: iMyOutputMtime,
            iDepTestSrcMtime: iDepTestSrcMtime,
        };
    }

    function _fsComputeDepLineageTiming(
        iStep, iDep, iDepMtime, iMyOutputMtime,
    ) {
        /* The unit-test source mtime is the contract: when the
           upstream's correctness criteria were last written. If the
           contract was in force at the moment downstream was built
           (and is still currently met, captured by sStepStatus
           passing), the lineage is intact — even if the upstream
           data was rerun and produced bit-identical bytes with a
           fresher mtime. Falls back to upstream-output-mtime
           comparison for steps without a test contract (interactive
           and plot-only steps). Key-presence (not value > 0) marks
           "contract exists" so a legitimate mtime=0 (e.g. files
           placed by a sync that did not preserve mtimes) still
           satisfies the gate. */
        if (!iMyOutputMtime) return "unknown";
        var dictTestSrc =
            _dictWorkflowState.dictTestSourceMtimeByStep || {};
        var sTestSrc = dictTestSrc[String(iDep)];
        if (sTestSrc !== undefined) {
            var iDepTestSrcMtime = parseInt(sTestSrc, 10);
            return iDepTestSrcMtime <= iMyOutputMtime
                ? "passed" : "failed";
        }
        if (!iDepMtime) return "unknown";
        return iDepMtime <= iMyOutputMtime ? "passed" : "failed";
    }

    function fnSetVerificationUserName(sName) {
        _dictSessionState.sUserName = sName || "User";
    }

    function fiParseUtcTimestamp(sTimestamp) {
        if (!sTimestamp) return 0;
        var sClean = sTimestamp.replace(" UTC", "").trim();
        var dtParsed = new Date(sClean + "Z");
        if (isNaN(dtParsed.getTime())) return 0;
        return Math.floor(dtParsed.getTime() / 1000);
    }

    var fsFormatUtcTimestamp = VaibifyUtilities.fsFormatUtcTimestamp;

    function fsGetFileCategory(iStep, sFilePath, sArrayKey) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (sArrayKey === "saPlotFiles") {
            var dictPlot = dictStep.dictPlotFileCategories || {};
            return dictPlot[sFilePath] || "archive";
        }
        var dictData = dictStep.dictOutputDataFileCategories || {};
        return dictData[sFilePath] || "archive";
    }

    function fnBindStepEvents() {
        if (_dictWorkflowState.bDelegatedEventsInitialized) return;
        _dictWorkflowState.bDelegatedEventsInitialized = true;
        var elList = document.getElementById("listSteps");
        VaibifyEventBindings.fnSetupDelegatedEvents(elList);
    }

    function _fsDescribeSaveFailure(error) {
        /* One sentence for every failure is one sentence too few. The
           old message said "Save failed" and nothing else, so a
           refused lane, a server exception and an unreachable hub were
           indistinguishable on screen -- three different problems with
           three different fixes, and a researcher who reported any of
           them could only say "it didn't save". Diagnosing one cost a
           maintainer and a researcher an afternoon of guessing.

           The reason comes from the server's own detail where there is
           one, because that is the string that names the operation
           that failed. The status is included even when a detail
           exists: 403 and 500 send a reader to different places, and
           the sentence should not make them equivalent. */
        var dictDetail = (error && error.dictDetail) || {};
        var iStatus = (error && error.iStatus) || 0;
        var sTail =
            " The dashboard reloaded to match the server, so it is not "
            + "showing an unsaved change.";
        if (!iStatus) {
            return "Could not reach the server, so the edit was not "
                + "saved." + sTail;
        }
        var sReason = dictDetail.sMessage
            || (error && error.message)
            || "no reason given";
        return "Save failed (" + iStatus + "): "
            + VaibifyUtilities.fsSanitizeErrorForUser(sReason) + sTail;
    }

    async function fnPutStepEdit(iStep, dictUpdate) {
        // Single choke-point for every step edit. Attaches the
        // compare-and-swap fingerprint so a concurrent writer (the
        // in-container agent) is never silently overwritten, and keeps
        // the tracked fingerprint fresh for the next edit. On a 409 the
        // local optimistic edit is stale, so we re-sync from the server
        // rather than trust it. Returns the response dict on success,
        // null on any failure (the caller shows nothing extra).
        //
        // A caller-supplied sBaseFingerprint WINS over the current
        // tracked one: a long-lived form (the step-edit modal) captures
        // the fingerprint when it opens and must submit THAT, so an
        // out-of-band reload that advanced the tracked fingerprint
        // cannot let the modal's stale fields pass the CAS check. The
        // default (tracked) applies to the transient toggles that read
        // and write in the same tick.
        var dictBody = Object.assign(
            {sBaseFingerprint:
                _dictWorkflowState.sWorkflowFingerprint || null},
            dictUpdate);
        try {
            var dictResult = await VaibifyApi.fdictPut(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iStep,
                dictBody);
            if (dictResult && dictResult.sWorkflowFingerprint) {
                _dictWorkflowState.sWorkflowFingerprint =
                    dictResult.sWorkflowFingerprint;
            }
            return dictResult;
        } catch (error) {
            if (error && error.iStatus === 409) {
                fnShowToast(
                    "The project changed since you loaded it — "
                    + "reloaded to stay in sync so your edit didn't "
                    + "overwrite it. Re-apply it if you still want it.",
                    "warning");
            } else {
                fnShowToast(_fsDescribeSaveFailure(error), "error");
            }
            // ANY failed save leaves the caller's optimistic local edit
            // diverged from the server. Callers across every module
            // mutate local state before this PUT for UI stickiness and
            // do not roll back individually, so the single honest fix is
            // here: re-sync the CURRENT workflow in place (refreshes the
            // fingerprint baseline and re-renders) so the dashboard never
            // shows an un-persisted edit as if it were saved. This is the
            // dashboard-ground-truth contract. Reload in place, never via
            // fnConnectToContainer, which would eject the researcher to
            // the workflow picker on a routine failed edit.
            VaibifyWorkflowManager.fnRefreshWorkflow();
            return null;
        }
    }

    async function fnSetStepBudget(iStep, fBudget) {
        // Success-only: persist FIRST, mutate the workflow dict only if
        // the save landed. Mutating before the PUT (for input
        // stickiness) meant a failed save left the un-persisted value on
        // screen — and if the failure was a network outage, the re-sync
        // that would correct it also fails, so the stale value stayed
        // forever. The native input already reflects the typed value;
        // the workflow-dict change waits for the server.
        var dictResult = await fnPutStepEdit(
            iStep, {fWallClockBudgetSeconds: fBudget});
        if (!dictResult) return;
        _dictWorkflowState.dictWorkflow.listSteps[iStep]
            .fWallClockBudgetSeconds = fBudget;
    }

    async function fnTogglePlotOnly(iStep, bPlotOnly) {
        var dictResult = await fnPutStepEdit(iStep, {bPlotOnly: bPlotOnly});
        if (!dictResult) {
            fnRenderStepList();  // reset the checkbox to server truth
            return;
        }
        _dictWorkflowState.dictWorkflow.listSteps[iStep].bPlotOnly =
            bPlotOnly;
    }

    async function fnToggleNoInputData(iStep, bNoInputData) {
        var dictResult = await fnPutStepEdit(
            iStep, {bNoInputData: bNoInputData});
        if (dictResult) {
            _dictWorkflowState.dictWorkflow.listSteps[iStep].bNoInputData =
                bNoInputData;
        }
        fnRenderStepList();
    }

    async function fnBulkDeclareNoInputData() {
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId
                + "/declare-no-input-data", {});
            var listDeclared =
                dictResult.listDeclaredStepIndices || [];
            listDeclared.forEach(function (iStep) {
                var step =
                    _dictWorkflowState.dictWorkflow.listSteps[iStep];
                if (step) step.bNoInputData = true;
            });
            fnShowToast(
                listDeclared.length === 0
                    ? "Every step already declares its input data"
                    : listDeclared.length
                        + " step(s) declared as needing no input data",
                "success");
            fnRenderStepList();
            VaibifyPolling.fnStartFilePolling(sContainerId);
        } catch (error) {
            fnShowToast("Declaration failed", "error");
        }
    }

    function fnToggleDepsExpand(iStep) {
        if (_dictUiState.setExpandedDeps.has(iStep)) {
            _dictUiState.setExpandedDeps.delete(iStep);
        } else {
            _dictUiState.setExpandedDeps.add(iStep);
        }
        fnRenderStepList();
    }

    function fnToggleUnitTestExpand(iStep) {
        var setExpanded = VaibifyTestManager.fsetGetExpandedUnitTests();
        if (setExpanded.has(iStep)) {
            setExpanded.delete(iStep);
        } else {
            setExpanded.add(iStep);
        }
        fnRenderStepList();
    }

    function fnHandleDiscoveredOutputs(dictEvent) {
        var iStep = dictEvent.iStepNumber - 1;
        var iTotal = (typeof dictEvent.iTotalDiscovered === "number") ?
            dictEvent.iTotalDiscovered : dictEvent.listDiscovered.length;
        _dictWorkflowState.dictDiscoveredOutputs[iStep] = {
            listDiscovered: dictEvent.listDiscovered,
            iTotalDiscovered: iTotal,
        };
        fnRenderStepList();
        fnShowToast(
            "Step " + dictEvent.iStepNumber +
            ": " + iTotal +
            " new output(s) discovered", "success"
        );
    }

    async function fnAddDiscoveredOutput(
        iStep, sFile, sTargetArray
    ) {
        // Success-only: build the proposed array and persist it before
        // mutating local state, so a failed save leaves neither the
        // added output nor the removed discovery-chip behind.
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var listProposed = (dictStep[sTargetArray] || []).concat([sFile]);
        var dictUpdate = {};
        dictUpdate[sTargetArray] = listProposed;
        var dictResult = await fnSaveStepUpdate(iStep, dictUpdate);
        if (!dictResult) return;
        dictStep[sTargetArray] = listProposed;
        var dictDisc = _dictWorkflowState.dictDiscoveredOutputs[iStep] ||
            {listDiscovered: [], iTotalDiscovered: 0};
        var listFiltered = (dictDisc.listDiscovered || []).filter(
            function (d) { return d.sFilePath !== sFile; }
        );
        _dictWorkflowState.dictDiscoveredOutputs[iStep] = {
            listDiscovered: listFiltered,
            iTotalDiscovered: dictDisc.iTotalDiscovered || 0,
        };
        fnRenderStepList();
    }

    var fnShowConfirmModal = VaibifyModals.fnShowConfirmModal;


    // The one place the Level 3 warning is written. Both entry points
    // -- the Project block's verify-l3 action and the PROOF tab's own
    // Verify button -- call this, because a safety warning maintained
    // in two places is a warning that drifts, and the half that goes
    // stale is the half nobody reads.
    //
    // It exists at all because the reproduction is a black box to the
    // researcher: it copies their project and re-runs the whole
    // workflow somewhere they cannot watch. Vaibify's premise is that
    // a researcher always knows what agents are doing, so the one
    // moment that premise is hard to keep is the moment to say so
    // plainly -- BEFORE the copy, when acting on it is still cheap.
    // The backend refuses a copy taken while the repository moved, so
    // this is not the safety mechanism; it is what turns a refusal the
    // researcher would find baffling into one they were expecting.
    // The readiness labels, in the researcher's words. Kept beside
    // the modal that shows them rather than derived from the API's
    // boolean keys, because the point is to say the thing the row is
    // called, not the thing the flag is called.
    var _DICT_L3_READINESS_LABELS = {
        bManifestComplete:
            "Manifest — it does not yet cover every declared file",
        bManifestMatchesTheFiles:
            "Manifest — it pins hashes that are not the current " +
            "bytes of the files it names, so a rebuild would report " +
            "those files as diverged. Regenerate the envelope",
        bDependencyLockHashed:
            "Dependency lock — missing, or not hashed",
        bEnvironmentDigestPinned:
            "Environment snapshot — not pinned",
        bDockerfilePinned: "Dockerfile — not pinned",
        bReproduceScriptPinned:
            "reproduce.sh — missing, or not pinned",
        bDeterminismDeclared:
            "Repeatability rules — not declared",
        bBinariesDeclaredOrWaived:
            "Standalone packages — neither declared nor waived",
        /* Not one of the seven envelope gaps — a fact about the
           CONTAINER — but it refuses the verify just the same, and a
           precondition absent from this list reaches the researcher
           as a bare failure toast instead of this modal's checklist
           (reported 2026-09-01). */
        bImageMatchesDeclaredPackages:
            "Container image — disagrees with the dependency " +
            "declaration in vaibify.yml (see the error on Verify " +
            "for which packages, and which side to fix)",
        bDockerfileDescribesPinnedImage:
            "Dockerfile — exported from a different build chain " +
            "than the pinned image's; re-export it from the " +
            "Dockerfile row",
        /* Also a fact about the CONTAINER rather than one of the
           envelope gaps, and listed here for the same reason: the
           rerun refuses on it before it starts, so a researcher who
           met it as a bare failure toast would have spent the whole
           pre-flight learning nothing.

           The cheap remedy is named FIRST because it is almost
           always the right one -- the lock is usually simply older
           than the image, and Regenerate now rewrites it from what
           the image has. Rebuilding also settles it, and costs the
           image: it goes back to matching the older lock. This
           wording and the shadow's refusal must not give different
           instructions for one cause. */
        bLockDoesNotBlockVerification:
            "Dependency lock — the image your envelope pins does " +
            "not satisfy it, so the rerun refuses before it starts. " +
            "Click 'Regenerate now' on the Dependency lock row to " +
            "rewrite the lock from what the image actually has; " +
            "rebuilding the image instead also settles it, at the " +
            "cost of downgrading the image to match the older lock",
    };

    function _fnCarryRecordKindOntoGaps(dictResponse) {
        /* Which record a verification would WRITE is an envelope-level
           fact beside the gaps, like the image currency; it rides on
           the gaps dict the opener receives, because the modal must
           say it before the researcher consents. */
        var dictGaps = (dictResponse || {}).dictL3ReadinessGaps || null;
        if (dictGaps) {
            dictGaps.sRecordKind = (dictResponse || {}).sRecordKind || "";
        }
    }

    async function fnResolveLockSatisfactionBeforeFirstPaint(sId) {
        /* One readiness GET on project open, and the Project block
           waits for it. Reading the installed packages needs an exec
           and the poll may add none, so the Dependency-lock row and
           the "Do this next" arrow would otherwise stay UNKNOWN until
           the researcher opened the PROOF tab or clicked Verify --
           which is how a stale lock went unnoticed until it refused a
           rerun (researcher-reported, 2026-09-15).

           It is AWAITED rather than fired alongside, because the
           answer arrives five to ten seconds after the block would
           otherwise paint. Painting first meant a green Project block
           with the arrow on the Rebuild attestation row, then the
           real state: amber, arrow on Artifacts. The researcher acted
           on the interim twice. A provisional green is not a smaller
           error than a slow page -- it is a worse one, because it is
           the one that gets clicked. The pulse-while-asking pattern
           the remote badges use is deliberately NOT borrowed here: it
           trades a wrong first paint for a two-stage one and still
           leaves an interim to act on.

           The verdict is recorded SERVER-SIDE by the route, so the
           readiness answer alone is not enough -- the block renders
           from the poll payload, and only a poll sent after the route
           returned carries it. Hence the ordered single poll.

           Failure clears the wait exactly as success does. Every
           surface renders unknown as it always has, and a block held
           forever over a failed background fetch would be the same
           dishonesty pointed the other way. */
        try {
            await _fdictFetchL3Readiness();
            await VaibifyPolling.fnPollFileStatusOnce(sId);
        } catch (error) {
            console.warn("[l3] lock warm-up did not complete:",
                error && error.message);
        } finally {
            /* Only for the project this resolver was opened for. A
               researcher who switches projects mid-wait leaves this
               one in flight over a state that has since been reset,
               and clearing the new project's flag from here would
               release its block on the strength of the old one's
               answer -- the same wrong first paint, arrived at from
               the other direction. */
            if (_dictSessionState.sContainerId === sId) {
                _dictWorkflowState.bProjectBlockAwaitsFirstAnswer = false;
                fnRenderStepList();
            }
        }
    }

    async function _fdictFetchL3Readiness() {
        /* Returns the GAPS DICT, not the envelope around it. The
           route answers {iProofLevel, dictL3ReadinessGaps}, and
           reading the flags off the outer object gave `undefined` for
           every one -- so `!== true` held for all seven and the
           pre-flight declared a fully-ready project unready, making
           the verification unstartable from the dashboard. The
           browser test missed it because its fake answered a FLAT
           payload nobody sends (reported 2026-08-30). */
        try {
            var dictResponse = await VaibifyApi.fdictGet(
                "/api/workflow/" +
                encodeURIComponent(_dictSessionState.sContainerId) +
                "/level3/readiness");
            _fnCarryRecordKindOntoGaps(dictResponse);
            var dictGaps = (dictResponse || {}).dictL3ReadinessGaps
                || null;
            if (dictGaps) {
                dictGaps.dictImageCurrency =
                    (dictResponse || {}).dictImageCurrency || null;
            }
            return dictGaps;
        } catch (error) {
            // A readiness fetch that fails must not block the action:
            // the route re-checks and refuses on its own, naming the
            // gaps. Falling through means the researcher sees the
            // copy warning and then the server's answer, which is the
            // behaviour before this pre-flight existed.
            console.warn("[l3] readiness pre-flight failed:",
                error && error.message);
            return null;
        }
    }

    /* The remedy for EITHER container fact when the image was BUILT
       while the clone pins an obtainable one: on a published clone
       that is the one cause of both, and the labels above would send
       the researcher to rewrite the author's committed files so they
       describe a build the author never made. Named once, so the two
       facts cannot drift into two different instructions. */
    var _S_L3_SWITCH_TO_PINNED_IMAGE_REMEDY =
        "Container image — built from the Dockerfile, while the " +
        "envelope pins the author's image. On the Environments hub, " +
        "open the tile's menu and choose 'Switch to the author's " +
        "pinned image'";

    var _LIST_L3_CONTAINER_FACT_KEYS = [
        "bImageMatchesDeclaredPackages", "bDockerfileDescribesPinnedImage",
    ];

    function _fbSwitchToPinnedImageIsTheRemedy(dictReady) {
        return dictReady.bImageWasBuilt === true &&
            dictReady.bPinnedImageObtainable === true;
    }

    function _flistNamePendingReadiness(dictReady) {
        var listPending = [];
        var bSwitchIsTheRemedy = _fbSwitchToPinnedImageIsTheRemedy(dictReady);
        var bSwitchNamed = false;
        Object.keys(_DICT_L3_READINESS_LABELS).forEach(function (sKey) {
            if (dictReady[sKey] === true) return;
            var bContainerFact = _LIST_L3_CONTAINER_FACT_KEYS.indexOf(sKey) >= 0;
            if (bContainerFact && bSwitchIsTheRemedy) {
                if (!bSwitchNamed) {
                    listPending.push(_S_L3_SWITCH_TO_PINNED_IMAGE_REMEDY);
                }
                bSwitchNamed = true;
                return;
            }
            listPending.push(_DICT_L3_READINESS_LABELS[sKey]);
        });
        return listPending;
    }

    function _fsDescribeNonBlockingWarnings() {
        /* Named so a researcher looking at a red glyph learns it is
           NOT what stopped them. A step whose directory disagrees with
           its name still re-runs from the directory as recorded, so it
           cannot change whether the project reproduces — but it is the
           loudest red thing on the screen, and silence about it invites
           exactly the wrong conclusion. */
        var iNonconforming = _fiCountNonconformingSteps();
        if (iNonconforming === 0) return "";
        return "Not blocking this: " + iNonconforming +
            " step directory name" + (iNonconforming === 1 ? "" : "s") +
            " disagree with the step name. That is a naming contract, " +
            "not a reproducibility one — the rerun uses each directory " +
            "as recorded, so it cannot change whether your project " +
            "reproduces. Fix it whenever you like with Align step " +
            "directories.";
    }

    async function fnShowL3AttestationModal() {
        /* "On file" earns a way to open the file
           (researcher-requested, 2026-09-02). Fetched fresh rather
           than read off the poll: the poll carries a summary, and a
           researcher opening the record wants the record. Every field
           is escaped — the attestation is an agent-writable JSON
           file — and the full document rides below the summary,
           because a curated view of a scientific record must never
           be the only view of it. */
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId) return;
        var dictPayload;
        try {
            dictPayload = await VaibifyApi.fdictGet(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/level3/attestation");
        } catch (error) {
            fnShowToast("Could not load the attestation: " +
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
            return;
        }
        var dictCurrent = (dictPayload || {}).dictCurrentAttestation;
        if (!dictCurrent) {
            VaibifyModals.fnShowInfoModal("Rebuild attestation",
                "<p>No attestation is on file yet.</p>");
            return;
        }
        VaibifyModals.fnShowInfoModal(
            "Rebuild attestation",
            _fsRenderAttestationDocument(dictCurrent));
    }

    function _fsRenderAttestationDocument(dictCurrent) {
        var fnRow = function (sLabel, sValue) {
            return "<div><strong>" + fnEscapeHtml(sLabel) +
                ":</strong> " + fnEscapeHtml(String(sValue)) +
                "</div>";
        };
        var listCarried = dictCurrent.listCarriedPaths || [];
        var sHtml = '<div class="attestation-summary">' +
            fnRow("Status", dictCurrent.sStatus || "unknown") +
            fnRow("Attested", dictCurrent.sAttestedAtUtc || "?") +
            fnRow("Manifest digest",
                dictCurrent.sManifestDigestAtAttestation || "?") +
            fnRow("Image", dictCurrent.sImageDigest || "?") +
            fnRow("Re-derived files matched",
                (dictCurrent.iOutputHashesMatched || 0) + " of " +
                (dictCurrent.iOutputHashesTotal || 0)) +
            fnRow("Duration",
                (dictCurrent.fDurationSeconds || 0).toFixed(1) +
                " s") +
            (listCarried.length
                ? fnRow("Carried human-made files",
                    listCarried.join(", "))
                : "") +
            "</div>" +
            '<p class="modal-info-note">The full record, exactly as ' +
            "committed to the repository at " +
            ".vaibify/l3_attestation.json:</p>" +
            '<pre class="attestation-json-view">' +
            fnEscapeHtml(JSON.stringify(dictCurrent, null, 2)) +
            "</pre>";
        return sHtml;
    }

    function _fnShowLevel3NotReadyModal(dictReady) {
        /* An INFO modal, not the confirm one. The copy warning
           describes the risks of an operation that is not going to
           happen, and a modal offering "Copy and verify" over a list
           of reasons it cannot is a button that lies. fnShowInfoModal
           has one dismiss control and no proceed path, which is the
           shape of this message. */
        var sHtml = '<p>Level 3 verification re-runs your whole ' +
            'workflow and compares the result, so the envelope it ' +
            're-runs FROM has to be complete first. Still to do:</p>' +
            '<ul class="l3-pending-list">';
        _flistNamePendingReadiness(dictReady).forEach(function (sItem) {
            sHtml += '<li>' + fnEscapeHtml(sItem) + '</li>';
        });
        sHtml += '</ul><p class="modal-info-note">Each of these has a ' +
            'row in the Project block that explains what it wants.</p>';
        var sWarnings = _fsDescribeNonBlockingWarnings();
        if (sWarnings) {
            sHtml += '<p class="modal-info-note">' +
                fnEscapeHtml(sWarnings) + '</p>';
        }
        VaibifyModals.fnShowInfoModal("Not ready to verify yet", sHtml);
    }

    function _ffnHoldButtonBusy(elButton, sBusyLabel) {
        // Returns the restore. A control that reaches the network
        // before it can show anything must say so: this opener spends
        // five to ten seconds on the readiness round trip before the
        // modal appears, and an unchanged button in that window reads
        // as a click that did not register, so researchers click again
        // (reported 2026-08-31).
        if (!elButton) return function () {};
        var sOriginalText = elButton.textContent;
        var bOriginallyDisabled = elButton.disabled;
        elButton.disabled = true;
        elButton.classList.add("btn-busy");
        if (sBusyLabel) elButton.textContent = sBusyLabel;
        return function () {
            // The element may have been replaced by a re-render while
            // the request was in flight; restoring a detached node is
            // harmless, and checking would not make it less so.
            elButton.disabled = bOriginallyDisabled;
            elButton.classList.remove("btn-busy");
            elButton.textContent = sOriginalText;
        };
    }

    function _fsImageCurrencyModalWarning(dictReady) {
        /* One paragraph, only on a DETERMINED mismatch. This is the
           moment the warning pays for itself: a researcher who
           rebuilt and forgot to regenerate the snapshot is about to
           spend a workflow-length rerun grading the OLD image, and
           learned it from a failing step deep inside the shadow
           (reported 2026-09-01). null paints nothing — an absent
           capture is not evidence of a mismatch. */
        var dictCurrency = (dictReady || {}).dictImageCurrency || {};
        if (dictCurrency.bPinnedImageIsLive !== false) return "";
        return "NOTE: your envelope pins an image that is NOT the " +
            "one this container is running. The verification will " +
            "grade the pinned image — if you rebuilt and meant to " +
            "verify the new build, cancel and click Regenerate now " +
            "on the Environment snapshot row first.\n\n";
    }

    function _fsRecordKindModalSentence(dictReady) {
        /* Which record the run will WRITE, from the readiness
           answer. A researcher verifying a clone whose attestation
           was committed by somebody else gets a reproduction record
           of their own, and must know that before they consent --
           otherwise the run they expected to refresh the attestation
           leaves it untouched and they learn why afterwards. */
        var sRecordKind = (dictReady || {}).sRecordKind || "";
        if (sRecordKind === "reproduction") {
            return "This clone's attestation was committed by someone " +
                "else, so this run will be recorded as a reproduction " +
                "under .vaibify/reproductions/ and the attestation will " +
                "not be touched.\n\n";
        }
        if (sRecordKind === "undetermined") {
            /* Git could not say whose attestation the clone carries.
               The run will REFUSE rather than guess, and the
               researcher is told before spending the click. */
            return "Git could not say whose attestation this clone " +
                "carries, so the verification will refuse to run " +
                "rather than risk overwriting someone else's record. " +
                "Check that git works in the project repository.\n\n";
        }
        return "This run will update this project's Level 3 " +
            "attestation.\n\n";
    }

    function _fdictRecordWithReproducedManifest(dictPayload,
                                                sPreferredRecordKind) {
        /* The record whose reproduced manifest the comparison opens:
           the one the clicked card belongs to when it has one, else
           whichever of the attestation and the latest reproduction
           has one -- the newer when both do. */
        var dictAttestation = dictPayload.dictCurrentAttestation || null;
        var dictReproduction = dictPayload.dictLatestReproduction || null;
        function fbHasManifest(dictRecord) {
            return Boolean(dictRecord && dictRecord.sReproducedManifestPath);
        }
        if (sPreferredRecordKind === "attestation" &&
                fbHasManifest(dictAttestation)) {
            return dictAttestation;
        }
        if (sPreferredRecordKind === "reproduction" &&
                fbHasManifest(dictReproduction)) {
            return dictReproduction;
        }
        if (fbHasManifest(dictAttestation) && fbHasManifest(dictReproduction)) {
            var sAttestedAt = dictAttestation.sAttestedAtUtc || "";
            var sReproducedAt = dictReproduction.sCreatedAtIso || "";
            return sReproducedAt > sAttestedAt ?
                dictReproduction : dictAttestation;
        }
        if (fbHasManifest(dictAttestation)) return dictAttestation;
        if (fbHasManifest(dictReproduction)) return dictReproduction;
        return null;
    }

    async function fnCompareManifests(sPreferredRecordKind) {
        /* MANIFEST.sha256 in viewer A, the reproduced manifest in
           viewer B, both read-only, every line coloured by the
           outcome the backend graded for its path. The marks come
           from listFileOutcomes and nothing else: the viewer never
           compares the hashes it shows. */
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId) return;
        var dictPayload;
        try {
            dictPayload = await VaibifyApi.fdictGet(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/level3/attestation");
        } catch (error) {
            fnShowToast("Could not load the attestation record: " +
                error.message, "error");
            return;
        }
        var dictRecord = _fdictRecordWithReproducedManifest(
            dictPayload || {}, sPreferredRecordKind || "");
        if (!dictRecord) {
            fnShowToast("No reproduced manifest is on file for this " +
                "project yet. Run a Level 3 verification first.",
                "warning");
            return;
        }
        var dictLineMarks = VaibifyFileOutcomes.fdictLineMarksFromOutcomes(
            dictRecord.listFileOutcomes || []);
        var elFilesTab = document.querySelector(
            '.left-tab[data-panel="files"]');
        if (elFilesTab) elFilesTab.click();
        VaibifyFigureViewer.fnDisplayRecordInViewer(
            "A", "MANIFEST.sha256", dictLineMarks);
        VaibifyFigureViewer.fnDisplayRecordInViewer(
            "B", dictRecord.sReproducedManifestPath, dictLineMarks);
    }

    async function fnConfirmLevel3Verification(fnOnConfirm, elButton) {
        // Readiness FIRST. The copy warning is about a real risk, but
        // only of an operation that can actually start; asking a
        // researcher to accept it and then refusing them is how a
        // safety notice becomes noise (reported 2026-08-30).
        //
        // The busy hold covers the readiness fetch only. It is
        // released before either modal opens, because from there the
        // researcher is looking at a dialog and the button behind it
        // is no longer the thing they are waiting on.
        var fnRelease = _ffnHoldButtonBusy(elButton, "Checking\u2026");
        var dictReady;
        try {
            dictReady = await _fdictFetchL3Readiness();
        } finally {
            fnRelease();
        }
        /* BOTH refusal classes route to the checklist modal. The
           package mismatch is not one of the seven envelope gaps, so
           gating on bL3ReadinessOK alone let it slip past this
           pre-flight and reach the researcher as a bare failure toast
           (reported 2026-09-01). */
        if (dictReady && (dictReady.bL3ReadinessOK !== true ||
                dictReady.bImageMatchesDeclaredPackages === false ||
                dictReady.bLockDoesNotBlockVerification === false ||
                dictReady.bDockerfileDescribesPinnedImage === false)) {
            _fnShowLevel3NotReadyModal(dictReady);
            return;
        }
        fnShowConfirmModal(
            "Copy this project and verify it reproduces",
            "Vaibify will copy this project out of its container and " +
            "re-run the whole workflow in a fresh, throwaway " +
            "container built from the image your envelope pins. Your " +
            "own files are not touched, and the copy is deleted " +
            "afterwards.\n\n" +
            _fsRecordKindModalSentence(dictReady) +
            _fsImageCurrencyModalWarning(dictReady) +
            "Before you continue, make sure nothing is writing inside " +
            "the container \u2014 an agent part-way through a task, a " +
            "command running in the terminal, or a step still going. " +
            "The copy is taken while the container keeps running, so " +
            "a write landing during it means the copy would hold a " +
            "mixture of two moments; vaibify refuses that rather than " +
            "reporting a result you could not trust.",
            fnOnConfirm,
            {
                sDetails: _fsDescribeRerunCost(dictReady),
                sCommand: "vaibify reproduce --rerun",
                sConfirmLabel: "Copy and verify",
                sCancelLabel: "Not now",
            }
        );
    }

    function _fsDescribeRerunCost(dictReady) {
        /* Vaibify records fWallClock for every step it has run, so it
           can state THIS project's cost rather than warn about "hours"
           at a workflow that finishes in ten seconds -- noise in a
           safety notice is how researchers learn to click through
           safety notices (researcher-reported, 2026-09-15).

           Always a FLOOR, and it says so: untimed steps contribute
           nothing, and the rerun also exports the project, acquires
           the pinned image (which can mean loading a multi-gigabyte
           archive) and hashes every pinned file. */
        var dictCost = (dictReady || {}).dictRerunCost || {};
        var sTail = " The whole pipeline is re-run, and this does not " +
            "count exporting the project, fetching the pinned image, " +
            "or hashing the results \u2014 so allow more.";
        if (dictCost.bAnyStepTimed !== true) {
            return "\u26a0 The whole pipeline is re-run. None of " +
                "these steps has been timed yet, so vaibify cannot " +
                "say how long that will take.";
        }
        var sTotal = _fsHumanizeDuration(dictCost.fRecordedSeconds);
        if (dictCost.iStepsUntimed) {
            return "\u26a0 Last time, the timed steps took " + sTotal +
                " \u2014 but " + dictCost.iStepsUntimed + " step" +
                (dictCost.iStepsUntimed === 1 ? " has" : "s have") +
                " never been timed and are not in that figure." + sTail;
        }
        return "\u26a0 Last time, these steps took " + sTotal + "." +
            sTail;
    }

    function _fsHumanizeDuration(fSeconds) {
        // Coarse on purpose: a recorded wall-clock is evidence of an
        // order of magnitude, not a stopwatch for the next run.
        var f = Number(fSeconds) || 0;
        if (f < 90) return Math.round(f) + " seconds";
        if (f < 5400) return Math.round(f / 60) + " minutes";
        if (f < 172800) return (f / 3600).toFixed(1) + " hours";
        return (f / 86400).toFixed(1) + " days";
    }
    var fnShowInputModal = VaibifyModals.fnShowInputModal;

    async function fnSaveStepUpdate(iStep, dictUpdate) {
        // The canonical single-step save. Funnels through fnPutStepEdit
        // so every caller gets the compare-and-swap fingerprint and the
        // re-sync-on-failure. Returns the response dict (null on
        // failure) so callers can gate their own UI on the outcome.
        return await fnPutStepEdit(iStep, dictUpdate);
    }

    async function fnCycleUserVerification(iStep) {
        // Success-only: build the NEXT verification as a fresh dict,
        // persist it, and commit to the step only if the save landed.
        // The old code mutated the step's verification in place before
        // the PUT, so a failed save (a network outage, where the
        // re-sync also fails) left a "passed" badge on screen that the
        // server never recorded — the sharpest ground-truth breach.
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        var dictVerify = fdictGetVerification(dictStep);
        var listStates = [
            "untested", "passed", "failed", "error"
        ];
        var iNext = (listStates.indexOf(dictVerify.sUser || "untested")
            + 1) % listStates.length;
        var dictNext = Object.assign({}, dictVerify);
        dictNext.sUser = listStates[iNext];
        dictNext.sLastUserUpdate = fsFormatUtcTimestamp();
        if (listStates[iNext] === "passed") {
            delete dictNext.listModifiedFiles;
            delete dictNext.bOutputModified;
        }
        var dictResult = await fnPutStepEdit(
            iStep, {dictVerification: dictNext});
        if (!dictResult) return;
        dictStep.dictVerification = dictNext;
        _dictWorkflowState.dictUserVerifiedAt[iStep] = Date.now();
        fnRenderStepList();
        fnUpdateHighlightState();
    }

    function fiClientProofLevel() {
        /* Authoritative source is the server-derived ``iProofLevel`` on
           the workflow dict, refreshed on every file-status poll. The
           per-step client gate (``fbStepIsAtLeastLevel1``) reads
           live render state (modified-files, deps badges, in-flight
           script changes) that the backend cannot see between polls,
           so we conjoin it with the server's level: any client-visible
           regression demotes the displayed level immediately, but the
           client never invents a level above what the server granted. */
        var dictWorkflow = _dictWorkflowState.dictWorkflow;
        if (!dictWorkflow || !dictWorkflow.listSteps) return 0;
        var iServerLevel = dictWorkflow.iProofLevel || 0;
        if (iServerLevel === 0) return 0;
        var listSteps = dictWorkflow.listSteps;
        if (listSteps.length === 0) return 0;
        for (var i = 0; i < listSteps.length; i++) {
            if (!fbStepIsAtLeastLevel1(listSteps[i], i)) return 0;
        }
        return iServerLevel;
    }

    function fnUpdateHighlightState() {
        var iLevel = fiClientProofLevel();
        var listLevelClasses = [
            "proof-level-1", "proof-level-2", "proof-level-3",
        ];
        document.body.classList.remove.apply(
            document.body.classList, listLevelClasses,
        );
        if (iLevel >= 1) {
            document.body.classList.add("proof-level-" + iLevel);
            VaibifyTerminal.fnUpdateCursorColor("#b39ddb");
        } else {
            VaibifyTerminal.fnUpdateCursorColor("#13aed5");
        }
        fnTriggerLevelTransitionAnimation(
            iLevel, _dictWorkflowState.iLastRenderedProofLevel,
        );
        fnRecolorVisibleDagEdges();
        _dictWorkflowState.iLastRenderedProofLevel = iLevel;
        /* No attestation banner. It fetched
           /level3/attestation on every level render to say one thing
           -- "your attestation no longer covers the manifest" -- that
           the Attestation row already says, and it said it while
           sending the researcher to a different tab than the "Do this
           first" arrow was pointing at. Two destinations for one
           action, neither wrong, which is what made it confusing
           (researcher's ruling, 2026-09-15: the Main tab is where the
           researcher should stay). The row carries the state and the
           button; the arrow carries the order. */
    }

    function fnRecolorVisibleDagEdges() {
        document.querySelectorAll(".dag-container svg").forEach(
            function (elSvg) { fnRecolorDagEdges(elSvg); }
        );
    }

    function fnTriggerLevelTransitionAnimation(iNewLevel, iOldLevel) {
        /* Fires on any upward promotion across the ladder. The same
           DOM overlay is reused for every rung; the body's
           `--proof-bloom-color` CSS variable swaps the gradient color
           between purple (L1), green (L2), and pink (L3) without
           forking the element. */
        if (iNewLevel <= iOldLevel) return;
        if (iNewLevel === 1 || iNewLevel === 2 || iNewLevel === 3) {
            fnAnimateBloomOverlay();
            fnAnimatePanelBorderCascade();
        }
    }

    function fnAnimateBloomOverlay() {
        var elOverlay = document.createElement("div");
        elOverlay.className = "vaibify-bloom-overlay";
        document.body.appendChild(elOverlay);
        requestAnimationFrame(function () {
            elOverlay.classList.add("expanding");
        });
        setTimeout(function () {
            elOverlay.classList.add("fading");
        }, 2700);
        setTimeout(function () {
            if (elOverlay.parentNode) {
                elOverlay.parentNode.removeChild(elOverlay);
            }
        }, 3500);
    }

    function fnAnimatePanelBorderCascade() {
        var listSelectors = [
            "#panelLeft", "#viewerA", "#viewerB",
            "#terminalStrip"
        ];
        listSelectors.forEach(function (sSelector, iIndex) {
            var elPanel = document.querySelector(sSelector);
            if (!elPanel) return;
            setTimeout(function () {
                elPanel.classList.add("vaibify-glow-cascade");
                elPanel.addEventListener("animationend", function () {
                    elPanel.classList.remove("vaibify-glow-cascade");
                }, { once: true });
            }, iIndex * 200);
        });
    }

    function fnAnimateProjectBirth() {
        /* One-shot celebration for the sandbox-to-Project milestone:
           two strokes race from the left column's top-left corner
           along its edges and meet at the bottom-right, then the
           overlay fades and removes itself. pathLength="1" normalizes
           both paths so a single dash-offset keyframe draws them
           regardless of the panel's pixel size. The overlay attaches
           to whichever surface the researcher is actually looking at
           -- the step viewer's left column when the main layout is
           up, the picker's content column otherwise -- because a
           celebration drawn on a hidden panel celebrates nothing. */
        var bMainLayoutActive = document.getElementById("mainLayout")
            .classList.contains("active");
        var elPanel = bMainLayoutActive
            ? document.querySelector("#panelLeft")
            : document.querySelector("#workflowPicker .picker-content");
        if (!elPanel) return;
        var S_SVG_NAMESPACE = "http://www.w3.org/2000/svg";
        var elOutline = document.createElementNS(
            S_SVG_NAMESPACE, "svg");
        elOutline.setAttribute(
            "class", "vaibify-project-birth-outline");
        elOutline.setAttribute("viewBox", "0 0 100 100");
        elOutline.setAttribute("preserveAspectRatio", "none");
        ["M 0 0 H 100 V 100", "M 0 0 V 100 H 100"].forEach(
            function (sPathData) {
                var elPath = document.createElementNS(
                    S_SVG_NAMESPACE, "path");
                elPath.setAttribute("d", sPathData);
                elPath.setAttribute("pathLength", "1");
                elOutline.appendChild(elPath);
            }
        );
        elPanel.appendChild(elOutline);
        setTimeout(function () {
            if (elOutline.parentNode) {
                elOutline.parentNode.removeChild(elOutline);
            }
        }, 2400);
    }

    function fnShowPromotionCurtain(sProjectName) {
        /* An opaque cover for the promotion hand-off: the re-entry
           necessarily tears down to the Environment hub, reloads the
           tiles, and passes the Project hub before the step viewer is
           ready, and a researcher watching three screens flash by
           reads it as being kicked out (live report, 2026-08-20). The
           curtain is a transition, never a state: every exit path of
           the hand-off removes it, so a refusal mid-way still lands
           the researcher on the real screen underneath, and the
           toasts (z 2000) stay readable above it. */
        fnHidePromotionCurtain();
        var elCurtain = document.createElement("div");
        elCurtain.className = "vaibify-promotion-curtain";
        var elTitle = document.createElement("div");
        elTitle.className = "curtain-title";
        elTitle.textContent = sProjectName;
        var elSubtitle = document.createElement("div");
        elSubtitle.className = "curtain-subtitle";
        elSubtitle.textContent = "Opening your new Project...";
        elCurtain.appendChild(elTitle);
        elCurtain.appendChild(elSubtitle);
        document.body.appendChild(elCurtain);
    }

    function fnHidePromotionCurtain() {
        var elCurtain = document.querySelector(
            ".vaibify-promotion-curtain");
        if (!elCurtain) return;
        elCurtain.classList.add("fading");
        setTimeout(function () {
            if (elCurtain.parentNode) {
                elCurtain.parentNode.removeChild(elCurtain);
            }
        }, 450);
    }

    function fnDeleteDetailItem(iStep, sArray, iIdx) {
        var sValue = _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray][iIdx];
        var dictVars = fdictBuildClientVariables();
        var sDisplay = fsResolveTemplate(sValue, dictVars);
        fnShowConfirmModal("Delete Item", sDisplay, function () {
            _fnExecuteDeleteItem(iStep, sArray, iIdx, sValue);
        });
    }

    async function _fnExecuteDeleteItem(iStep, sArray, iIdx, sValue) {
        _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray].splice(iIdx, 1);
        fnPushUndo({
            sAction: "delete",
            iStep: iStep,
            sArray: sArray,
            iIdx: iIdx,
            sValue: sValue,
        });
        await fnSaveStepArray(iStep, sArray);
        fnRenderStepList();
    }

    function fnAddNewItem(iStep, sArrayKey) {
        if (sArrayKey === "saInputDataFiles") {
            VaibifyModals.fnShowFilePickerModal(
                "Add Input Data",
                "Pick the raw data file this step reads, or type "
                    + "its repo-relative path.",
                function (sPath) {
                    fnCommitNewItem(iStep, sArrayKey, sPath);
                });
            return;
        }
        var sPlaceholder = sArrayKey === "saPlotFiles" ?
            "File path..." : "Command...";
        VaibifyModals.fnShowInlineInput(
            iStep, sArrayKey, sPlaceholder);
    }

    async function fnCommitNewItem(iStep, sArrayKey, sValue) {
        if (!_dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey]) {
            _dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey] = [];
        }
        _dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey].push(sValue);
        // Persist BEFORE claiming success. On failure fnPutStepEdit has
        // re-synced the workflow (dropping this optimistic push) and
        // toasted the error; showing "Item added" too would contradict
        // it. Record undo only for a change that actually landed.
        var dictResult = await fnSaveStepArray(iStep, sArrayKey, true);
        if (!dictResult) return;
        fnPushUndo({
            sAction: "add",
            iStep: iStep,
            sArray: sArrayKey,
            iIdx: _dictWorkflowState.dictWorkflow.listSteps[iStep][sArrayKey].length - 1,
            sValue: sValue,
        });
        fnRenderStepList();
        fnShowToast("Item added", "success");
    }

    function fnPushUndo(dictAction) {
        _dictWorkflowState.listUndoStack.push(dictAction);
        if (_dictWorkflowState.listUndoStack.length > I_MAX_UNDO) {
            _dictWorkflowState.listUndoStack.shift();
        }
    }

    async function fnUndo() {
        if (_dictWorkflowState.listUndoStack.length === 0) {
            fnShowToast("Nothing to undo", "error");
            return;
        }
        var dictAction = _dictWorkflowState.listUndoStack.pop();
        if (dictAction.sAction === "add") {
            _dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 1);
        } else if (dictAction.sAction === "delete") {
            _dictWorkflowState.dictWorkflow.listSteps[dictAction.iStep][dictAction.sArray]
                .splice(dictAction.iIdx, 0, dictAction.sValue);
        }
        var dictResult = await fnSaveStepArray(
            dictAction.iStep, dictAction.sArray);
        if (!dictResult) {
            // The undo did not persist; fnPutStepEdit re-synced and
            // toasted. Put the action back so it can be retried, and do
            // not claim "Undone".
            _dictWorkflowState.listUndoStack.push(dictAction);
            return;
        }
        fnRenderStepList();
        fnShowToast("Undone", "success");
    }

    async function fnSaveStepArray(iStep, sArray, bScanDeps) {
        var dictUpdate = {};
        dictUpdate[sArray] = _dictWorkflowState.dictWorkflow.listSteps[iStep][sArray];
        var dictResult = await fnPutStepEdit(iStep, dictUpdate);
        if (dictResult && sArray === "saDataCommands" && bScanDeps) {
            VaibifyDependencyScanner.fnScanDependencies(iStep);
        }
        return dictResult;
    }

    /* --- Step Expand/Collapse --- */

    function fnToggleStepExpand(iIndex) {
        if (_dictUiState.setExpandedSteps.has(iIndex)) {
            _dictUiState.setExpandedSteps.delete(iIndex);
        } else {
            _dictUiState.setExpandedSteps.add(iIndex);
            VaibifyPlotStandards.fnLoadPlotStandardStatus(
                iIndex);
        }
        _dictUiState.iSelectedStepIndex = iIndex;
        fnRenderStepList();
    }

    async function fnToggleStepEnabled(iIndex, bRunEnabled) {
        var dictResult = await fnPutStepEdit(
            iIndex, {bRunEnabled: bRunEnabled});
        if (dictResult) {
            _dictWorkflowState.dictWorkflow
                .listSteps[iIndex].bRunEnabled = bRunEnabled;
        }
    }

    async function fnReorderStep(iFromIndex, iToIndex) {
        try {
            var result = await VaibifyApi.fdictPost(
                "/api/steps/" + _dictSessionState.sContainerId + "/reorder",
                {iFromIndex: iFromIndex, iToIndex: iToIndex});
            _dictWorkflowState.dictWorkflow.listSteps = result.listSteps;
            fnRenderStepList();
            fnShowToast(
                "Step reordered (references renumbered)",
                "success");
        } catch (error) {
            fnShowToast("Reorder failed", "error");
        }
    }

    function fnResetLayout() {
        document.getElementById("mainLayout")
            .style.gridTemplateColumns = "280px 1fr";
        document.getElementById("panelViewerDual")
            .style.flex = "1";
        document.getElementById("viewerA")
            .style.flex = "1";
    }

    function fnSetPollInterval(iSeconds) {
        VaibifyPolling.fnSetPollInterval(iSeconds);
        var elSlider = document.getElementById("gsPollInterval");
        if (elSlider) elSlider.title = iSeconds + " seconds";
        fnStartFileChangePolling();
    }

    async function fnShowDag() {
        if (!_dictSessionState.sContainerId) return;
        fnShowToast("Generating dependency graph...", "success");
        try {
            var sSvgText = await VaibifyApi.fsGetText(
                "/api/workflow/" + _dictSessionState.sContainerId + "/dag");
            _fnRenderDagInViewer(sSvgText);
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function _fnRenderDagInViewer(sSvgText) {
        var dScale = 1.0;
        _fnRenderDagWithZoom(sSvgText, dScale);
    }

    async function _fnExportDag() {
        var sFormat = "svg";
        if (_dictWorkflowState.dictWorkflow) {
            sFormat = (
                _dictWorkflowState.dictWorkflow.sFigureType || "pdf"
            ).toLowerCase();
        }
        var sUrl = "/api/workflow/" +
            _dictSessionState.sContainerId +
            "/dag/export?sFormat=" + encodeURIComponent(sFormat);
        try {
            var response = await fetch(sUrl);
            if (!response.ok) {
                throw new Error("Export failed (" +
                    response.status + ")");
            }
            var blob = await response.blob();
            var sBlobUrl = URL.createObjectURL(blob);
            var elLink = document.createElement("a");
            elLink.href = sBlobUrl;
            elLink.download = "dag." + sFormat;
            document.body.appendChild(elLink);
            elLink.click();
            document.body.removeChild(elLink);
            URL.revokeObjectURL(sBlobUrl);
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error"
            );
        }
    }

    var _elDagViewport = null;

    function _fnRenderDagWithZoom(sSvgText, dScale) {
        if (_elDagViewport &&
                document.body.contains(_elDagViewport)) {
            _fnPaintDagInViewport(_elDagViewport, sSvgText, dScale);
            return;
        }
        VaibifyFigureViewer.fnClaimNextViewerForReplacement(
            "pipeline DAG", function (sViewerLetter) {
                _elDagViewport = document.getElementById(
                    "viewport" + sViewerLetter);
                _fnPaintDagInViewport(
                    _elDagViewport, sSvgText, dScale);
            });
    }

    function _fnPaintDagInViewport(elViewport, sSvgText, dScale) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        if (dScale === "fit") {
            dScale = 1.0;
        }
        var elToolbar = VaibifyFigureViewer.fnCreateZoomToolbar(
            dScale, function (dNewScale) {
                _fnRenderDagWithZoom(sSvgText, dNewScale);
            }
        );
        var elExportButton = document.createElement("button");
        elExportButton.className = "btn btn-sm";
        elExportButton.textContent = "Export";
        elExportButton.title = "Export DAG in settings figure format";
        elExportButton.addEventListener("click", _fnExportDag);
        elToolbar.appendChild(elExportButton);
        elViewport.appendChild(elToolbar);
        var elContainer = document.createElement("div");
        elContainer.className = "dag-container";
        elContainer.style.overflow = "auto";
        elContainer.style.flex = "1";
        elContainer.style.display = "flex";
        elContainer.style.justifyContent = "center";
        elContainer.style.padding = "16px";
        elContainer.innerHTML = sSvgText;
        var elSvg = elContainer.querySelector("svg");
        if (elSvg) {
            elSvg.style.transform = "scale(" + dScale + ")";
            elSvg.style.transformOrigin = "top center";
            fnRecolorDagEdges(elSvg);
        }
        elViewport.appendChild(elContainer);
    }

    function fnRecolorDagEdges(elSvg) {
        var sColor = fsGetHighlightColor();
        elSvg.querySelectorAll(".edge path, .edge polygon")
            .forEach(function (el) {
                el.setAttribute("stroke", sColor);
                if (el.tagName === "polygon") {
                    el.setAttribute("fill", sColor);
                }
            });
    }

    function fsGetHighlightColor() {
        return getComputedStyle(document.body)
            .getPropertyValue("--highlight-color").trim();
    }

    async function fnLoadLogs() {
        if (!_dictSessionState.sContainerId) return;
        var elList = document.getElementById("listLogs");
        try {
            var listLogs = await VaibifyApi.fdictGet(
                "/api/logs/" + _dictSessionState.sContainerId);
            if (listLogs.length === 0) {
                elList.innerHTML =
                    '<p class="muted-text">No log files yet.</p>';
                return;
            }
            elList.innerHTML = listLogs.map(function (sFilename) {
                return (
                    '<div class="file-entry" data-log="' +
                    fnEscapeHtml(sFilename) + '">' +
                    fnEscapeHtml(sFilename) + '</div>'
                );
            }).join("");
            elList.querySelectorAll(".file-entry").forEach(function (el) {
                el.addEventListener("click", function () {
                    fnViewLogFile(el.dataset.log);
                });
            });
        } catch (error) {
            elList.innerHTML =
                '<p class="muted-text">Could not load logs.</p>';
        }
    }

    async function fnViewLogFile(sFilename) {
        if (!_dictSessionState.sContainerId) return;
        try {
            var sContent = await VaibifyApi.fsGetText(
                "/api/logs/" + _dictSessionState.sContainerId + "/" +
                encodeURIComponent(sFilename));
            VaibifyFigureViewer.fnClaimNextViewerForReplacement(
                sFilename, function (sViewerLetter) {
                    var elViewport = document.getElementById(
                        "viewport" + sViewerLetter);
                    elViewport.innerHTML =
                        '<pre class="pipeline-output">' +
                        fnEscapeHtml(sContent) + '</pre>';
                });
        } catch (error) {
            fnShowToast(fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnClearOutputModified(iStep) {
        var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
        if (dictStep && dictStep.dictVerification) {
            delete dictStep.dictVerification.bOutputModified;
            delete dictStep.dictVerification.listModifiedFiles;
        }
    }

    function fnStartFileChangePolling() {
        if (!_dictSessionState.sContainerId) return;
        VaibifyPolling.fnStartFilePolling(_dictSessionState.sContainerId);
        VaibifyPolling.fnSetPromptRecordPredicate(function () {
            var dictRecord = (_dictWorkflowState
                .dictWorkflowEnvelopeDetail || {}).dictPromptRecord;
            return Boolean(dictRecord && dictRecord.bEnabled === true);
        });
        VaibifyPolling.fnStartPromptRecordPolling(
            _dictSessionState.sContainerId);
    }

    function fnProcessFileStatusResponse(dictStatus) {
        /* An answer about another project of this container -- one
           computed before a switch landed on either side -- would
           overwrite this project's fingerprints, file times and
           lights with the other's. */
        if (dictStatus.sServedWorkflowPath &&
            dictStatus.sServedWorkflowPath !==
                _dictWorkflowState.sWorkflowPath) {
            return;
        }
        _fnRenderOtherProjectRuns(dictStatus.dictOtherProjectRuns);
        if (dictStatus.sWorkflowReloadError) {
            fnShowToast(
                "project.json error: " +
                dictStatus.sWorkflowReloadError +
                ". Showing last good state.",
                "warning");
        }
        if (dictStatus.bWorkflowReloaded && dictStatus.dictWorkflow) {
            _fnApplyOutOfBandWorkflowReload(
                dictStatus.dictWorkflow, dictStatus.iWorkflowEpoch);
        } else if (!dictStatus.bWorkflowReloaded &&
            typeof dictStatus.iWorkflowEpoch === "number") {
            _dictWorkflowState.iWorkflowEpoch =
                dictStatus.iWorkflowEpoch;
            /* Not reloaded means this client is on the server's
               current workflow lineage, so the poll's exact-source
               fingerprint describes what is already applied here —
               this is what keeps a self-save made from ANOTHER of
               this session's panels acknowledged. */
            if (typeof dictStatus.sExactSourceFingerprint ===
                "string" && dictStatus.sExactSourceFingerprint) {
                _dictWorkflowState.sAcknowledgedSourceFingerprint =
                    dictStatus.sExactSourceFingerprint;
            }
        }
        if (typeof dictStatus.sWorkflowFingerprint === "string") {
            _dictWorkflowState.sWorkflowFingerprint =
                dictStatus.sWorkflowFingerprint;
        }
        _fnReflectDispatchedRunState(dictStatus.dictRunState);
        VaibifyFileOps.fnDetectOutputFileChanges(
            dictStatus.dictModTimes || {}, _dictWorkflowState);
        if (dictStatus.dictMaxMtimeByStep) {
            _dictWorkflowState.dictOutputMtimes =
                dictStatus.dictMaxMtimeByStep;
        }
        if (dictStatus.dictMaxPlotMtimeByStep) {
            _dictWorkflowState.dictPlotMtimes =
                dictStatus.dictMaxPlotMtimeByStep;
        }
        if (dictStatus.dictMaxDataMtimeByStep) {
            _dictWorkflowState.dictMaxDataMtimeByStep =
                dictStatus.dictMaxDataMtimeByStep;
        }
        if (dictStatus.dictMaxInputMtimeByStep) {
            _dictWorkflowState.dictMaxInputMtimeByStep =
                dictStatus.dictMaxInputMtimeByStep;
        }
        if (dictStatus.dictMarkerMtimeByStep) {
            _dictWorkflowState.dictMarkerMtimeByStep =
                dictStatus.dictMarkerMtimeByStep;
        }
        if (dictStatus.dictTestSourceMtimeByStep) {
            _dictWorkflowState.dictTestSourceMtimeByStep =
                dictStatus.dictTestSourceMtimeByStep;
        }
        if (dictStatus.dictTestCategoryMtimes) {
            _dictWorkflowState.dictTestCategoryMtimes =
                dictStatus.dictTestCategoryMtimes;
        }
        _fnApplyBlockerAndLevelState(dictStatus);
        fnResetStaleUserVerifications();
        var dictInv = dictStatus.dictInvalidatedSteps;
        if (dictInv && Object.keys(dictInv).length > 0) {
            fnApplyInvalidatedSteps(dictInv);
        }
        fnUpdateDepsTimestamps();
        VaibifyFileOps.fnUpdateScriptStatus(
            dictStatus.dictScriptStatus, _dictWorkflowState);
        if (dictStatus.dictTestMarkers) {
            VaibifyTestManager.fnApplyTestMarkers(
                dictStatus.dictTestMarkers);
        }
        if (dictStatus.dictTestFileChanges) {
            VaibifyTestManager.fnNotifyTestFileChanges(
                dictStatus.dictTestFileChanges);
        }
    }

    function _fnApplyBlockersFromPoll(dictStatus) {
        var dictByStep = _fdictBlockersByStepIndex(
            dictStatus.listBlockers);
        _dictWorkflowState.dictBlockersByStep = dictByStep;
        _dictWorkflowState.iL1BlockerCount =
            (typeof dictStatus.iL1BlockerCount === "number")
                ? dictStatus.iL1BlockerCount
                : Object.keys(dictByStep).length;
        var dictByStepL2 = _fdictBlockersByStepIndex(
            dictStatus.listLevel2Blockers);
        _dictWorkflowState.dictBlockersByStepLevel2 = dictByStepL2;
        _dictWorkflowState.iL2BlockerCount =
            (typeof dictStatus.iL2BlockerCount === "number")
                ? dictStatus.iL2BlockerCount
                : Object.keys(dictByStepL2).length;
        _fnApplyL3BlockersFromPoll(dictStatus);
    }

    function _fnApplyBlockerAndLevelState(dictStatus) {
        // A poll can change blockers or level cells without touching
        // any other rendered input; re-render when (and only when)
        // they actually moved so the dashboard never shows a stale
        // ladder. The incremental renderer skips unchanged cards.
        var sPriorState = _fsBlockerAndLevelSnapshot();
        _fnApplyBlockersFromPoll(dictStatus);
        _fnApplyLevelStatesFromPoll(dictStatus);
        if (_fsBlockerAndLevelSnapshot() !== sPriorState) {
            fnRenderStepList();
        }
    }

    function _fsBlockerAndLevelSnapshot() {
        return JSON.stringify([
            _dictWorkflowState.dictWorkflow
                ? _dictWorkflowState.dictWorkflow.iProofLevel
                : null,
            _dictWorkflowState.dictBlockersByStep,
            _dictWorkflowState.dictBlockersByStepLevel2,
            _dictWorkflowState.dictBlockersByStepLevel3,
            _dictWorkflowState.dictStepLevels,
            _dictWorkflowState.dictStepLevelHighWater,
            _dictWorkflowState.dictStepLevelWarnings,
            _dictWorkflowState.dictWorkflowScopeLevels,
            _dictWorkflowState.dictWorkflowLevelHighWater,
            _dictWorkflowState.dictWorkflowEnvelopeDetail,
        ]);
    }

    function _fnApplyLevelStatesFromPoll(dictStatus) {
        // Level-cell wire keys (Scope B/P backend projection). Each
        // key is optional so older payloads degrade to the previous
        // state rather than blanking the cells.
        if (typeof dictStatus.iProofLevel === "number" &&
            _dictWorkflowState.dictWorkflow) {
            /* The theme (fiClientProofLevel) reads this integer off
             * the workflow dict. Without this copy the level cells
             * update live but the workflow-level promotion only
             * arrives on a full reload — every step showed its L1
             * check while the theme stayed at level 0. */
            _dictWorkflowState.dictWorkflow.iProofLevel =
                dictStatus.iProofLevel;
            _fnMaybeAutoCollapseStepsOnFirstL1(dictStatus.iProofLevel);
        }
        if (dictStatus.dictStepLevels) {
            _dictWorkflowState.dictStepLevels =
                dictStatus.dictStepLevels;
        }
        if (dictStatus.dictStepLevelHighWater) {
            _dictWorkflowState.dictStepLevelHighWater =
                dictStatus.dictStepLevelHighWater;
        }
        if (dictStatus.dictWorkflowScopeLevels) {
            _dictWorkflowState.dictWorkflowScopeLevels =
                dictStatus.dictWorkflowScopeLevels;
        }
        if (dictStatus.dictWorkflowLevelHighWater) {
            _dictWorkflowState.dictWorkflowLevelHighWater =
                dictStatus.dictWorkflowLevelHighWater;
        }
        _fnApplyWarningAndEnvelopeFromPoll(dictStatus);
    }

    function _fnApplyWarningAndEnvelopeFromPoll(dictStatus) {
        // Consolidated regression warnings and the workflow envelope
        // detail (software, artifacts, determinism, remote syncs).
        // Both keys are optional so older payloads degrade to the
        // previous state rather than blanking the cells.
        if (dictStatus.dictStepLevelWarnings) {
            _dictWorkflowState.dictStepLevelWarnings =
                dictStatus.dictStepLevelWarnings;
        }
        if (dictStatus.dictWorkflowEnvelopeDetail) {
            _dictWorkflowState.dictWorkflowEnvelopeDetail =
                dictStatus.dictWorkflowEnvelopeDetail;
        }
        // An empty map is a real answer here — "no check is running"
        // — and `{}` is truthy, so it is adopted. Only a payload from
        // a hub that predates the key is skipped, which leaves the
        // badges rendering from the cache exactly as they did before.
        if (dictStatus.dictRemoteChecks) {
            _dictWorkflowState.dictRemoteChecks =
                dictStatus.dictRemoteChecks;
        }
    }

    function _fdictBlockersByStepIndex(listBlockers) {
        var dictByStep = {};
        var listSafe = listBlockers || [];
        for (var i = 0; i < listSafe.length; i++) {
            var dictEntry = listSafe[i];
            if (dictEntry && typeof dictEntry.iStepIndex === "number") {
                dictByStep[dictEntry.iStepIndex] = dictEntry;
            }
        }
        return dictByStep;
    }

    function _fnApplyL3BlockersFromPoll(dictStatus) {
        var dictByStepL3 = {};
        var listL3 = dictStatus.listLevel3Blockers || [];
        for (var i = 0; i < listL3.length; i++) {
            var dictEntry = listL3[i];
            if (!dictEntry || typeof dictEntry.iStepIndex !== "number") {
                continue;
            }
            if (dictEntry.iStepIndex < 0) continue;
            dictByStepL3[dictEntry.iStepIndex] = dictEntry;
        }
        _dictWorkflowState.dictBlockersByStepLevel3 = dictByStepL3;
        _dictWorkflowState.iL3BlockerCount =
            (typeof dictStatus.iL3BlockerCount === "number")
                ? dictStatus.iL3BlockerCount
                : Object.keys(dictByStepL3).length;
    }

    function fnResetStaleUserVerifications() {
        if (!_dictWorkflowState.dictWorkflow || !_dictWorkflowState.dictWorkflow.listSteps) return;
        var bChanged = false;
        var iNow = Date.now();
        for (var i = 0; i < _dictWorkflowState.dictWorkflow.listSteps.length; i++) {
            if (_dictWorkflowState.dictUserVerifiedAt[i] &&
                (iNow - _dictWorkflowState.dictUserVerifiedAt[i]) < 15000) {
                continue;
            }
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[i];
            var dictVerify = (dictStep.dictVerification || {});
            if (dictVerify.sUser !== "passed") continue;
            if (_fbOutputNewerThanVerification(i, dictVerify)) {
                dictVerify.sUser = "untested";
                delete dictVerify.sLastUserUpdate;
                dictStep.dictVerification = dictVerify;
                bChanged = true;
            }
        }
        if (bChanged) fnRenderStepList();
    }

    function _fbOutputNewerThanVerification(iStep, dictVerify) {
        var sMaxMtime = _dictWorkflowState.dictPlotMtimes[String(iStep)];
        if (!sMaxMtime) return false;
        var iOutputEpoch = parseInt(sMaxMtime, 10);
        var iUserEpoch = fiParseUtcTimestamp(
            dictVerify.sLastUserUpdate);
        var bResult = iUserEpoch > 0 && iOutputEpoch > iUserEpoch;
        if (bResult) {
            console.log(
                "[STALE] step " + iStep + ": plotEpoch=" +
                iOutputEpoch + " userEpoch=" + iUserEpoch +
                " sLastUserUpdate=" + dictVerify.sLastUserUpdate
            );
        }
        return bResult;
    }

    function fnUpdateDepsTimestamps() {
        if (!_dictWorkflowState.dictWorkflow ||
                !_dictWorkflowState.dictWorkflow.listSteps) return;
        var sNow = fsFormatUtcTimestamp();
        for (var i = 0; i < _dictWorkflowState.dictWorkflow.listSteps.length; i++) {
            var listDeps = flistGetStepDependencies(i);
            if (listDeps.length === 0) continue;
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[i];
            var dictVerify = dictStep.dictVerification || {};
            dictVerify.sLastDepsCheck = sNow;
            dictStep.dictVerification = dictVerify;
        }
    }

    function fnApplyInvalidatedSteps(dictStepVerifications) {
        var bAnyChanged = false;
        var iNow = Date.now();
        var iGraceMs = 15000;
        for (var sIndex in dictStepVerifications) {
            var iStep = parseInt(sIndex, 10);
            if (_fbWithinGracePeriod(iStep, iNow, iGraceMs)) {
                continue;
            }
            var dictStep = _dictWorkflowState.dictWorkflow.listSteps[iStep];
            if (!dictStep) continue;
            var sOldUser = (dictStep.dictVerification || {}).sUser;
            var sNewUser = (dictStepVerifications[sIndex] || {}).sUser;
            if (sOldUser !== sNewUser) {
                console.log(
                    "[INVALIDATE] step " + iStep +
                    ": sUser " + sOldUser + " -> " + sNewUser
                );
            }
            dictStep.dictVerification =
                dictStepVerifications[sIndex];
            bAnyChanged = true;
        }
        if (bAnyChanged) fnRenderStepList();
    }

    function _fbWithinGracePeriod(iStep, iNow, iGraceMs) {
        var iAckedAt = VaibifyPipelineRunner.fiGetAcknowledgedAt(iStep);
        if (iAckedAt && (iNow - iAckedAt) < iGraceMs) {
            return true;
        }
        var iVerifiedAt = _dictWorkflowState.dictUserVerifiedAt[iStep];
        if (iVerifiedAt && (iNow - iVerifiedAt) < iGraceMs) {
            return true;
        }
        return false;
    }

    var fnShowErrorModal = VaibifyModals.fnShowErrorModal;

    async function fdictAddAiDeclarationStep() {
        var sContainerId = _dictSessionState.sContainerId;
        if (!sContainerId || !_dictWorkflowState.dictWorkflow) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/ai-declaration/add-step", {});
            _dictWorkflowState.dictWorkflow.listSteps.push(
                dictResult.dictStep);
            fnRenderStepList();
            fnShowToast("AI declaration step added", "success");
        } catch (error) {
            fnShowToast(
                fsSanitizeErrorForUser(error.message), "error");
        }
    }

    function fnOpenVsCode() {
        var sHexId = _dictSessionState.sContainerId.replace(/-/g, "");
        var sUri =
            "vscode://ms-vscode-remote.remote-containers/attach?containerId=" +
            sHexId;
        window.open(sUri, "_blank");
        fnShowToast("Opening VS Code...", "success");
    }

    function fnShowContextMenu(iX, iY, iIndex) {
        _dictUiState.iContextStepIndex = iIndex;
        var el = document.getElementById("contextMenu");
        el.style.left = iX + "px";
        el.style.top = iY + "px";
        el.classList.add("active");
    }

    function fnHideContextMenu() {
        document.getElementById("contextMenu")
            .classList.remove("active");
    }

    function fnHandleContextAction(sAction, iIndex) {
        if (sAction === "runStep") {
            VaibifyPipelineRunner.fnRunSingleStep(iIndex);
        } else if (sAction === "edit") {
            VaibifyStepEditor.fnOpenEditModal(iIndex);
        } else if (sAction === "runFrom") {
            VaibifyPipelineRunner.fnSendPipelineAction({
                sAction: "runFrom",
                iStartStep: iIndex + 1,
            });
        } else if (sAction === "insertBefore") {
            VaibifyStepEditor.fnOpenInsertModal(iIndex);
        } else if (sAction === "insertAfter") {
            VaibifyStepEditor.fnOpenInsertModal(iIndex + 1);
        } else if (sAction === "setRuntimeLimit") {
            fnOpenRuntimeLimitModal(iIndex);
        } else if (sAction === "rename") {
            VaibifyStepEditor.fnOpenRenameModal(iIndex);
        } else if (sAction === "delete") {
            fnDeleteStep(iIndex);
        }
    }

    function fnOpenRuntimeLimitModal(iStep) {
        var dictWorkflow = _dictWorkflowState.dictWorkflow;
        var step = dictWorkflow
            && dictWorkflow.listSteps[iStep];
        if (!step) return;
        var dictStats = step.dictRunStats || {};
        var bKnownSuccess = dictStats.iExitCode === 0
            && dictStats.fWallClock !== undefined;
        VaibifyModals.fnShowRuntimeLimitModal({
            sStepTitle: fsComputeStepLabel(iStep) + " " +
                (step.sName || ""),
            fCurrentBudget: step.fWallClockBudgetSeconds || 0,
            fProjectDefault:
                dictWorkflow.fDefaultWallClockBudgetSeconds || 0,
            fLastSuccessfulWallClock:
                bKnownSuccess ? dictStats.fWallClock : 0,
            fnOnSave: function (fSeconds) {
                fnSetStepBudget(iStep, fSeconds);
                fnShowToast(fSeconds > 0
                    ? "Runtime limit set to " + fSeconds + " s"
                    : "Runtime limit cleared — the step inherits "
                        + "the project default", "success");
            },
        });
    }

    function fnDeleteStep(iIndex) {
        var sName = _dictWorkflowState.dictWorkflow.listSteps[iIndex].sName;
        fnShowConfirmModal(
            "Delete Step",
            'Delete step "' + sName + '"?',
            function () { _fnExecuteDeleteStep(iIndex); }
        );
    }

    async function _fnExecuteDeleteStep(iIndex) {
        try {
            var result = await VaibifyApi.fnDelete(
                "/api/steps/" + _dictSessionState.sContainerId + "/" + iIndex);
            _dictWorkflowState.dictWorkflow.listSteps = result.listSteps;
            if (_dictUiState.iSelectedStepIndex === iIndex) _dictUiState.iSelectedStepIndex = -1;
            _dictUiState.setExpandedSteps.delete(iIndex);
            fnPruneStaleStatuses();
            fnRenderStepList();
            fnShowToast(
                "Step deleted (references renumbered)",
                "success");
        } catch (error) {
            fnShowToast("Delete failed", "error");
        }
    }

    function fnShowOutputNotAvailable() {
        VaibifyFigureViewer.fnShowPlaceholderInNextViewer(
            '<span class="placeholder output-missing-message">' +
            'Output not available. Run the step to generate.</span>',
            "missing output");
    }

    function fnShowBinaryNotViewable() {
        VaibifyFigureViewer.fnShowPlaceholderInNextViewer(
            '<span class="placeholder">' +
            'File cannot be viewed.</span>',
            "binary file");
    }

    var fsSanitizeErrorForUser = VaibifyUtilities.fsSanitizeErrorForUser;

    function fnShowToast(sMessage, sType, fnOnClick) {
        var el = document.createElement("div");
        el.className = "toast " + (sType || "");
        if (fnOnClick) el.classList.add("toast-clickable");
        el.innerHTML = fnEscapeHtml(sMessage) +
            '<button class="toast-close">&times;</button>';
        el.querySelector(".toast-close").addEventListener(
            "click", function (event) {
                event.stopPropagation();
                el.remove();
            }
        );
        if (fnOnClick) {
            el.addEventListener("click", function () {
                fnOnClick();
                el.remove();
            });
        }
        if (sType !== "error" && sType !== "warning") {
            setTimeout(function () { el.remove(); }, 4000);
        }
        document.getElementById("toastContainer").appendChild(el);
    }

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    /* --- Public API --- */

    return {
        fnInitialize: fnInitialize,
        fnShowToast: fnShowToast,
        fnRenderStepList: fnRenderStepList,
        fnRenderStepListPartial: fnRenderStepListPartial,
        _fnInvalidateAllRenderCaches: _fnInvalidateAllRenderCaches,
        fsGetContainerId: function () {
            return _dictSessionState.sContainerId;
        },
        fbIsRemoteSession: fbIsRemoteSession,
        fsGetExecutionHostname: function () {
            return _dictSessionState.sExecutionHostname || "";
        },
        fnApplyExecutionTopology: fnApplyExecutionTopology,
        fbExecutionHostIsTheEnvironment:
            fbExecutionHostIsTheEnvironment,
        fnApplyRemoteSession: fnApplyRemoteSession,
        fsGetProjectMode: function () {
            return _dictSessionState.sProjectMode;
        },
        fsGetWorkspaceRoot: function () {
            return _dictSessionState.sWorkspaceRoot;
        },
        fnApplyWorkspaceRoot: fnApplyWorkspaceRoot,
        fsGetSessionToken: function () {
            return _dictSessionState.sSessionToken;
        },
        fsGetLeaseId: fsGetLeaseId,
        fsGetLeaseForContainer: fsGetLeaseForContainer,
        fnRecordClaimedLease: fnRecordClaimedLease,
        fnForgetLease: fnForgetLease,
        fnAcknowledgeSourceFingerprint: function (sFingerprint) {
            if (sFingerprint) {
                _dictWorkflowState.sAcknowledgedSourceFingerprint =
                    sFingerprint;
            }
        },
        fsGetAcknowledgedSourceFingerprint: function () {
            return _dictWorkflowState
                .sAcknowledgedSourceFingerprint || "";
        },
        fsGetWorkflowPath: function () {
            return _dictWorkflowState.sWorkflowPath || "";
        },
        fdictGetWorkflow: function () {
            return _dictWorkflowState.dictWorkflow;
        },
        fsGetWorkflowPath: function () {
            return _dictWorkflowState.sWorkflowPath;
        },
        fsGetWorkflowFingerprint: function () {
            // The current tracked compare-and-swap baseline. A modal
            // captures this at open so it can submit the exact version
            // it was populated from, not whatever the poll advanced to.
            return _dictWorkflowState.sWorkflowFingerprint || null;
        },
        fiGetSelectedStepIndex: function () {
            return _dictUiState.iSelectedStepIndex;
        },
        fdictBuildClientVariables: fdictBuildClientVariables,
        fnShowConfirmModal: fnShowConfirmModal,
        fnConfirmLevel3Verification: fnConfirmLevel3Verification,
        fnCompareManifests: fnCompareManifests,
        fnShowL3AttestationModal: fnShowL3AttestationModal,
        fdictDescribePromoteOutcome: fdictDescribePromoteOutcome,
        fnShowInputModal: fnShowInputModal,
        fnClearOutputModified: fnClearOutputModified,
        fnActivateWorkflow: _fnActivateWorkflow,
        fnAnimateProjectBirth: fnAnimateProjectBirth,
        fnShowPromotionCurtain: fnShowPromotionCurtain,
        fnHidePromotionCurtain: fnHidePromotionCurtain,
        fnRefreshWorkflowData: fnRefreshWorkflowData,
        fiGetWorkflowEpoch: function () {
            return _dictWorkflowState.iWorkflowEpoch;
        },
        fnEnterNoWorkflow: fnEnterNoWorkflow,
        fnSaveStepUpdate: fnSaveStepUpdate,
        fnShowWorkflowPicker: fnShowWorkflowPicker,
        fnApplyProjectMode: fnApplyProjectMode,
        fnSetPlotStandardExists: function (sKey, bValue) {
            _dictWorkflowState.dictPlotStandardExists[sKey] =
                bValue;
        },
        fbGetPlotStandardExists: function (sKey) {
            return _dictWorkflowState
                .dictPlotStandardExists[sKey];
        },
        fnShowErrorModal: fnShowErrorModal,
        fnUpdateHighlightState: fnUpdateHighlightState,
        fsComputeStepLabel: fsComputeStepLabel,
        fdictGetVerification: fdictGetVerification,
        fdictGetTests: fdictGetTests,
        fnInvalidateStepFileCache: fnInvalidateStepFileCache,
        fnSetStepStatus: function (iIndex, sStatus) {
            _dictWorkflowState.dictStepStatus[iIndex] = sStatus;
        },
        /* Ruling R6: a step that executed after an earlier step's
           remote-data documentation degraded wears a warning glyph
           beside its light. The mark is per-run truth from the
           server's result events/records — never inferred here. */
        fnSetStepTaint: function (iIndex, bTainted) {
            if (bTainted) {
                _dictWorkflowState.dictStepTaint[iIndex] = true;
            } else {
                delete _dictWorkflowState.dictStepTaint[iIndex];
            }
        },
        fnClearStepTaints: function () {
            for (var sKey in _dictWorkflowState.dictStepTaint) {
                delete _dictWorkflowState.dictStepTaint[sKey];
            }
        },
        fnClearRunningStatuses: fnClearRunningStatuses,
        fnResetQueuedSteps: fnResetQueuedSteps,
        /* `fnClearAllStepStatuses` was removed with its only caller.
           Its whole effect there was the defect -- a stop erasing a
           completed step's result -- and a "forget every status"
           helper sitting on the public surface is an invitation to
           reintroduce it. Clearing across a workflow switch belongs
           to `_fnResetWorkflowState`, which rebuilds the whole state
           object rather than reaching into this one. */
        fnStartFileChangePolling: fnStartFileChangePolling,
        fnToggleStepEnabled: fnToggleStepEnabled,
        fnClearFileExistenceCache: function () {
            _dictWorkflowState.dictFileExistenceCache = {};
        },
        fiGetL1BlockerCount: function () {
            return _dictWorkflowState.iL1BlockerCount || 0;
        },
        fiGetL2BlockerCount: function () {
            return _dictWorkflowState.iL2BlockerCount || 0;
        },
        fiGetL3BlockerCount: function () {
            return _dictWorkflowState.iL3BlockerCount || 0;
        },
        fiGetCachedProofLevel: function () {
            var iCached = _dictWorkflowState.iCachedProofLevel;
            return typeof iCached === "number" ? iCached : null;
        },
        fnSetCachedProofLevel: fnSetCachedProofLevel,
        fdictBlockerCountsByLevel: fdictBlockerCountsByLevel,
        fdictBlockerGlyphCatalog: fdictBlockerGlyphCatalog,
        fsBlockerHintForStep: fsBlockerHintForStep,
        fsBlockerHintForFile: fsBlockerHintForFile,
        fdictAddAiDeclarationStep: fdictAddAiDeclarationStep,
        fnHandleDiscoveredOutputs: fnHandleDiscoveredOutputs,

        /* New public methods for extracted modules */
        fnDeleteDetailItem: fnDeleteDetailItem,
        fnAddDiscoveredOutput: fnAddDiscoveredOutput,
        fnAddNewItem: fnAddNewItem,
        fnCycleUserVerification: fnCycleUserVerification,
        fsetGetExpandedCategory: fsetGetExpandedCategory,
        fnToggleUnitTestExpand: fnToggleUnitTestExpand,
        fnToggleDepsExpand: fnToggleDepsExpand,
        fnToggleStepExpand: fnToggleStepExpand,
        fnToggleStepsBlockExpand: fnToggleStepsBlockExpand,
        fnToggleProjectBlockExpand:
            fnToggleProjectBlockExpand,
        fnToggleBinaryAddForm: fnToggleBinaryAddForm,
        fnToggleRequirementGroup: fnToggleRequirementGroup,
        fnToggleRequirementRow: fnToggleRequirementRow,
        fnToggleFileGroup: fnToggleFileGroup,
        fnExpandRequirementRow: fnExpandRequirementRow,
        fnRunProjectAction: fnRunProjectAction,
        fnTogglePlotOnly: fnTogglePlotOnly,
        fnToggleNoInputData: fnToggleNoInputData,
        fnBulkDeclareNoInputData: fnBulkDeclareNoInputData,
        fnSetStepBudget: fnSetStepBudget,
        fnToggleStepLevelSection: fnToggleStepLevelSection,
        fnExpandStepLevelSection: fnExpandStepLevelSection,
        fnToggleStepDescription: fnToggleStepDescription,
        fnBeginStepDescriptionEdit: fnBeginStepDescriptionEdit,
        fdictAlignStepDirectories: fdictAlignStepDirectories,
        fnShowStepLevelRequirementsModal:
            fnShowStepLevelRequirementsModal,
        fnShowContextMenu: fnShowContextMenu,
        fnHideContextMenu: fnHideContextMenu,
        fnReorderStep: fnReorderStep,
        fnHandleContextAction: fnHandleContextAction,
        fiGetContextStepIndex: function () {
            return _dictUiState.iContextStepIndex;
        },
        fnDisconnect: fnDisconnect,
        fnShowContainerLanding: fnShowContainerLanding,
        fnStopAllHubPolling: fnStopAllHubPolling,
        fnResumeHubPollingForCurrentView:
            fnResumeHubPollingForCurrentView,
        fbIsWorkflowMode: function () {
            return _dictSessionState.dictDashboardMode &&
                _dictSessionState.dictDashboardMode.sMode ===
                "workflow";
        },
        fnLoadLogs: fnLoadLogs,
        fnShowDag: fnShowDag,
        fnOpenVsCode: fnOpenVsCode,
        fnResetLayout: fnResetLayout,
        fnReconnectToCurrentContainer: function () {
            if (_dictSessionState.sContainerId) {
                VaibifyContainerManager.fnConnectToContainer(
                    _dictSessionState.sContainerId);
            }
        },
        fnRenderGlobalSettings: fnRenderGlobalSettings,
        fnRenderHostSettings: fnRenderHostSettings,
        fnCommitNewItem: fnCommitNewItem,
        fnShowOutputNotAvailable: fnShowOutputNotAvailable,
        fnShowBinaryNotViewable: fnShowBinaryNotViewable,
        fbIsFileMissing: fbIsFileMissing,
        fsGetFileCategory: fsGetFileCategory,
        fbStepIsAtLeastLevel1: fbStepIsAtLeastLevel1,
        fsetGetExpandedSteps: function () {
            return _dictUiState.setExpandedSteps;
        },
        fnSaveStepArray: fnSaveStepArray,
        flistGetStepDependencies: flistGetStepDependencies,
    };
})();

document.addEventListener("DOMContentLoaded", VaibifyApp.fnInitialize);

function fnBlockUnload(event) {
    event.preventDefault();
    event.returnValue = "";
}

window.addEventListener("beforeunload", fnBlockUnload);
window.addEventListener("pagehide", function (event) {
    /* pagehide fires on reload and navigation, not only a real close, so
     * it is NOT release intent: releasing the container here would drop a
     * running container on a mere reload. Abandonment is decided by the
     * WebSocket closing without a reconnect and the grace reaper freeing
     * the owner — never by an unload beacon. Just stop polling. */
    VaibifyApp.fnStopAllHubPolling();
});

document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
        VaibifyApp.fnStopAllHubPolling();
    } else {
        VaibifyApp.fnResumeHubPollingForCurrentView();
    }
});

window.addEventListener("keydown", function (event) {
    var bCloseShortcut = (event.metaKey || event.ctrlKey) &&
        event.key === "w";
    if (!bCloseShortcut) return;
    event.preventDefault();
    VaibifyApp.fnShowConfirmModal(
        "Close Vaibify",
        "Are you sure you want to close this window? " +
            "Unsaved changes may be lost.",
        function () {
            window.removeEventListener("beforeunload",
                fnBlockUnload);
            window.close();
        }
    );
});
