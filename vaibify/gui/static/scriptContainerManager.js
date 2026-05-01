/* Vaibify — Container landing page (extracted from scriptApplication.js) */

var PipeleyenContainerManager = (function () {
    "use strict";

    var _sSelectedContainerId = null;
    var _sSelectedContainerName = null;

    async function fnLoadContainers() {
        var elList = document.getElementById("listContainers");
        try {
            var dictResult = await VaibifyApi.fdictGet("/api/registry");
            fnRenderContainerList(dictResult.listContainers || []);
            fnRenderUnrecognizedList(dictResult.listUnrecognized || []);
        } catch (error) {
            elList.innerHTML =
                '<p style="color: var(--color-red);">' +
                "Cannot load containers</p>";
        }
    }

    function fnRenderContainerList(listContainers) {
        var elList = document.getElementById("listContainers");
        if (listContainers.length === 0) {
            elList.innerHTML =
                '<p class="muted-text" style="text-align: center;">' +
                "No containers registered. Click + to add one.</p>";
            return;
        }
        elList.innerHTML = listContainers.map(function (dictContainer) {
            return fsRenderContainerTile(dictContainer);
        }).join("");
        fnBindContainerTiles(elList);
    }

    function fnRenderUnrecognizedList(listUnrecognized) {
        var elSection = document.getElementById("unrecognizedSection");
        var elList = document.getElementById("listUnrecognized");
        if (listUnrecognized.length === 0) {
            elSection.style.display = "none";
            return;
        }
        elSection.style.display = "";
        elList.innerHTML = listUnrecognized.map(function (c) {
            return (
                '<div class="container-card unrecognized" data-id="' +
                VaibifyUtilities.fnEscapeHtml(c.sContainerId) + '">' +
                '<span class="name">' +
                VaibifyUtilities.fnEscapeHtml(c.sName) + "</span>" +
                '<span class="image">' +
                VaibifyUtilities.fnEscapeHtml(c.sImage) + "</span></div>"
            );
        }).join("");
        elList.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                fnConnectToContainer(el.dataset.id);
            });
        });
    }

    function fsRenderContainerTile(dictContainer) {
        var sStatusClass = _fsStatusDotClass(dictContainer.sStatus);
        var sId = dictContainer.sContainerId || "";
        var bLocked = dictContainer.bLocked === true;
        var sLockedClass = bLocked ? " container-tile--locked" : "";
        var sLockedTitle = bLocked
            ? ' title="Already accessed by another vaibify session"'
            : "";
        var sLockedAttr = bLocked ? ' data-locked="true"' : "";
        return (
            '<div class="container-tile' + sLockedClass +
            '" data-name="' +
            VaibifyUtilities.fnEscapeHtml(dictContainer.sName) +
            '" data-container-id="' + VaibifyUtilities.fnEscapeHtml(sId) +
            '"' + sLockedAttr + sLockedTitle + '>' +
            '<div class="container-tile-main">' +
            '<span class="status-dot ' + sStatusClass + '"></span>' +
            '<span class="container-tile-name">' +
            VaibifyUtilities.fnEscapeHtml(dictContainer.sName) + "</span>" +
            "</div>" +
            '<button class="btn-icon container-tile-actions" ' +
            'title="Actions">&#8942;</button>' +
            '<button class="btn-icon container-tile-gear" ' +
            'title="Settings">&#9881;</button>' +
            '<div class="container-tile-menu" style="display:none;">' +
            '<div class="container-menu-item" data-action="start">' +
            "Start</div>" +
            '<div class="container-menu-item" data-action="stop">' +
            "Stop</div>" +
            '<div class="container-menu-item" data-action="restart">' +
            "Restart</div>" +
            '<div class="container-menu-item" data-action="rebuild">' +
            "Rebuild</div>" +
            '<div class="container-menu-item" data-action="force-rebuild">' +
            "Force Rebuild</div>" +
            '<div class="container-menu-separator"></div>' +
            '<div class="container-menu-item danger" ' +
            'data-action="remove">Remove from list</div>' +
            "</div></div>"
        );
    }

    function _fsStatusDotClass(sStatus) {
        if (sStatus === "running") return "status-running";
        if (sStatus === "stopped") return "status-stopped";
        return "status-not-built";
    }

    function fnBindContainerTiles(elParent) {
        elParent.querySelectorAll(".container-tile").forEach(function (el) {
            var sName = el.dataset.name;
            el.querySelector(".container-tile-main").addEventListener(
                "click", function () {
                    fnHandleContainerClick(sName);
                }
            );
            _fnBindTileControls(el, sName);
        });
    }

    function _fnBindTileControls(elTile, sName) {
        var elActions = elTile.querySelector(".container-tile-actions");
        var elGear = elTile.querySelector(".container-tile-gear");
        var elMenu = elTile.querySelector(".container-tile-menu");
        elActions.addEventListener("click", function (event) {
            event.stopPropagation();
            _fnToggleActionsMenu(elMenu);
        });
        elGear.addEventListener("click", function (event) {
            event.stopPropagation();
            _fnCloseAllActionsMenus();
            fnShowContainerSettings(sName);
        });
        elMenu.querySelectorAll(".container-menu-item").forEach(
            function (elItem) {
                elItem.addEventListener("click", function (event) {
                    event.stopPropagation();
                    elMenu.style.display = "none";
                    fnHandleContainerAction(sName, elItem.dataset.action);
                });
            }
        );
    }

    function _fnCloseAllActionsMenus() {
        document.querySelectorAll(".container-tile-menu").forEach(
            function (el) { el.style.display = "none"; }
        );
    }

    function _fnToggleActionsMenu(elMenu) {
        var bVisible = elMenu.style.display !== "none";
        _fnCloseAllActionsMenus();
        elMenu.style.display = bVisible ? "none" : "";
    }

    async function fnHandleContainerClick(sName) {
        var elTile = document.querySelector(
            '.container-tile[data-name="' + sName + '"]'
        );
        if (elTile && elTile.dataset.locked === "true") {
            PipeleyenApp.fnShowToast(
                "Container '" + sName + "' is already accessed by "
                + "another vaibify session.", "warning");
            return;
        }
        var elDot = elTile ? elTile.querySelector(".status-dot") : null;
        var bRunning = elDot && elDot.classList.contains("status-running");
        var bNotBuilt = elDot &&
            elDot.classList.contains("status-not-built");
        if (bNotBuilt) {
            await fnBuildContainer(sName);
            return;
        }
        if (!bRunning) {
            await fnStartContainer(sName);
        }
        var bClaimed = await _fbClaimContainer(sName);
        if (!bClaimed) {
            await fnLoadContainers();
            return;
        }
        var sStoredId = elTile ? elTile.dataset.containerId : "";
        var sTargetId = sStoredId ||
            await _fsResolveContainerId(sName);
        if (!sTargetId) return;
        _fnShowInitializingOverlay();
        var dictReadiness = await _fdictWaitForContainerReady(sTargetId);
        _fnHideInitializingOverlay();
        _fnSurfaceReadinessOutcome(dictReadiness);
        if (!dictReadiness || !dictReadiness.bReady) {
            var sStatus = dictReadiness ? dictReadiness.sStatus : "";
            if (sStatus !== "failed" && sStatus !== "stalled") {
                PipeleyenApp.fnShowToast(
                    "Container took too long to initialize. "
                    + "Connecting anyway — some data may be "
                    + "incomplete.", "warning");
            }
        }
        fnConnectToContainer(sTargetId);
    }

    async function _fbClaimContainer(sName) {
        try {
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) + "/claim",
                {});
            return true;
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Container '" + sName + "' is already accessed by "
                + "another vaibify session.", "warning");
            return false;
        }
    }

    async function fnReleaseClaim(sName) {
        if (!sName) return;
        try {
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) +
                "/release", {});
        } catch (error) {
            /* release is best-effort; shutdown will clean up */
        }
    }

    async function fnHandleContainerAction(sName, sAction) {
        if (sAction === "start") await fnStartContainer(sName);
        else if (sAction === "stop") await fnStopContainer(sName);
        else if (sAction === "restart") await fnRestartContainer(sName);
        else if (sAction === "rebuild") await fnRebuildContainer(sName);
        else if (sAction === "force-rebuild")
            await fnForceRebuildContainer(sName);
        else if (sAction === "remove") await fnRemoveContainer(sName);
    }

    async function fnShowContainerSettings(sName) {
        try {
            var dictSettings = await VaibifyApi.fdictGet(
                "/api/containers/" + encodeURIComponent(sName)
                + "/settings"
            );
            fnShowContainerSettingsModal(sName, dictSettings);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function fnShowContainerSettingsModal(sName, dictSettings) {
        var elExisting = document.getElementById("modalSettings");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalSettings";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>Settings for ' +
            VaibifyUtilities.fnEscapeHtml(sName) + '</h2>' +
            '<p class="settings-intro">Configure how this ' +
            'container behaves while running. Changes take ' +
            'effect the next time the container starts.</p>' +
            '<div class="settings-option">' +
            '<label class="settings-option-row">' +
            '<input type="checkbox" id="settingNeverSleep"' +
            (dictSettings.bNeverSleep ? " checked" : "") + '>' +
            '<span class="settings-option-label">' +
            'Keep host awake while running</span></label>' +
            '<p class="settings-option-help">' +
            'On macOS, long simulations can be interrupted ' +
            'when the laptop sleeps. Enabling this runs ' +
            '<code>caffeinate</code> on the host for as long ' +
            'as this container is running, preventing sleep. ' +
            'Has no effect on Linux.</p>' +
            '</div>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnSettingsCancel">Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnSettingsSave">Save</button>' +
            '</div></div>';
        document.body.appendChild(elModal);
        document.getElementById("btnSettingsCancel").addEventListener(
            "click", function () { elModal.remove(); });
        document.getElementById("btnSettingsSave").addEventListener(
            "click", async function () {
                var bNeverSleep = document.getElementById(
                    "settingNeverSleep").checked;
                elModal.remove();
                await fnSaveContainerSettings(sName, {
                    bNeverSleep: bNeverSleep,
                });
            });
    }

    async function fnSaveContainerSettings(sName, dictSettings) {
        try {
            await VaibifyApi.fdictPost(
                "/api/containers/" + encodeURIComponent(sName)
                + "/settings",
                dictSettings
            );
            PipeleyenApp.fnShowToast(
                "Settings saved. Use Restart to apply.",
                "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    async function fnBuildContainer(sName, bNoCache) {
        var elOverlay = document.getElementById("modalBuildProgress");
        elOverlay.style.display = "flex";
        try {
            var sUrl = "/api/containers/" +
                encodeURIComponent(sName) + "/build";
            if (bNoCache) sUrl += "?bNoCache=true";
            await VaibifyApi.fdictPostRaw(sUrl);
            PipeleyenApp.fnShowToast("Build complete", "success");
            await fnStartContainer(sName);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        } finally {
            elOverlay.style.display = "none";
            fnLoadContainers();
        }
    }

    function fnSetTilePending(sName) {
        var elTile = document.querySelector(
            '.container-tile[data-name="' + CSS.escape(sName) + '"]'
        );
        if (!elTile) return;
        var elDot = elTile.querySelector(".status-dot");
        if (!elDot) return;
        elDot.className = "status-dot status-pending";
    }

    async function fnStartContainer(sName) {
        fnSetTilePending(sName);
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/containers/" + encodeURIComponent(sName)
                + "/start"
            );
            PipeleyenApp.fnShowToast("Container started", "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        } finally {
            fnLoadContainers();
        }
    }

    async function fnStopContainer(sName) {
        fnSetTilePending(sName);
        PipeleyenTerminal.fnCloseAll();
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/containers/" + encodeURIComponent(sName)
                + "/stop"
            );
            PipeleyenApp.fnShowToast("Container stopped", "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        } finally {
            fnLoadContainers();
        }
    }

    async function fnRestartContainer(sName) {
        PipeleyenApp.fnShowConfirmModal(
            "Restart Container",
            "Stop the container and start it again using the " +
            "current image. Open terminal sessions will close. " +
            "Workspace files are preserved.",
            async function () {
                PipeleyenTerminal.fnCloseAll();
                await fnStopContainer(sName);
                await fnStartContainer(sName);
            },
            {
                sDetails:
                    "Use Restart when you've rebuilt the image from " +
                    "the command line (vaibify build) and want the " +
                    "container to switch to the new image, or when " +
                    "a running container has gotten into a bad state " +
                    "and needs a fresh process. No image rebuild " +
                    "happens, so this is fast.",
                sCommand: "vaibify stop && vaibify start",
            }
        );
    }

    async function fnRebuildContainer(sName) {
        PipeleyenApp.fnShowConfirmModal(
            "Rebuild Container",
            "Stop the container, rebuild the image with your " +
            "current vaibify.yml settings, then start a fresh " +
            "container. Open terminal sessions will close. " +
            "Workspace files are preserved.",
            async function () {
                PipeleyenTerminal.fnCloseAll();
                await fnStopContainer(sName);
                await fnBuildContainer(sName, false);
            },
            {
                sDetails:
                    "Use Rebuild after editing vaibify.yml to change " +
                    "Python packages, system packages, repositories, " +
                    "or other project settings. Docker reuses cached " +
                    "layers where possible, so only the parts that " +
                    "changed are rebuilt \u2014 usually seconds.",
                sCommand: "vaibify stop && vaibify build && vaibify start",
            }
        );
    }

    async function fnForceRebuildContainer(sName) {
        PipeleyenApp.fnShowConfirmModal(
            "Force Rebuild (Slow)",
            "Rebuild every layer of the image from scratch, " +
            "ignoring the build cache. This can take several " +
            "minutes. Workspace files are preserved.",
            async function () {
                PipeleyenTerminal.fnCloseAll();
                await fnStopContainer(sName);
                await fnBuildContainer(sName, true);
            },
            {
                sDetails:
                    "Use Force Rebuild only when the image seems " +
                    "corrupted, or when a layer needs to re-fetch " +
                    "from the network \u2014 for example, a " +
                    "repository pinned to a moving branch like " +
                    "main. For routine changes, use Rebuild " +
                    "instead; it is much faster and produces the " +
                    "same result.",
                sCommand:
                    "vaibify stop && vaibify build --no-cache && "
                    + "vaibify start",
            }
        );
    }

    async function fnRemoveContainer(sName) {
        PipeleyenApp.fnShowConfirmModal(
            "Remove from List",
            "Remove '" + sName + "' from the dashboard. The " +
            "Docker image and workspace files are not deleted " +
            "and can be re-registered later.",
            async function () {
                try {
                    await VaibifyOverleafMirror.fnForgetContainer(sName);
                } catch (error) {
                    /* mirror deletion is best-effort */
                }
                try {
                    await VaibifyApi.fnDelete(
                        "/api/registry/"
                        + encodeURIComponent(sName)
                    );
                    PipeleyenApp.fnShowToast(
                        "Container removed", "success");
                } catch (error) {
                    PipeleyenApp.fnShowToast(
                        VaibifyUtilities.fsSanitizeErrorForUser(
                            error.message), "error");
                }
                fnLoadContainers();
            },
            {
                sDetails:
                    "This removes the container from vaibify's " +
                    "dashboard list only. It does not run `docker " +
                    "rm`, does not delete the image, and does not " +
                    "touch any files in your workspace. To fully " +
                    "delete the environment, use `vaibify destroy` " +
                    "from a terminal.",
                bNoCommand: true,
            }
        );
    }

    async function _fsResolveContainerId(sName) {
        try {
            var dictResult = await VaibifyApi.fdictGet("/api/registry");
            var listAll = dictResult.listContainers || [];
            var dictMatch = listAll.find(function (c) {
                return c.sName === sName && c.sContainerId;
            });
            return dictMatch ? dictMatch.sContainerId : "";
        } catch (error) {
            return "";
        }
    }

    async function _fdictWaitForContainerReady(sContainerId) {
        var iMaxAttempts = 60;
        var iIntervalMs = 2000;
        var dictLast = null;
        for (var iAttempt = 0; iAttempt < iMaxAttempts; iAttempt++) {
            try {
                var dictResult = await VaibifyApi.fdictGet(
                    "/api/containers/"
                    + encodeURIComponent(sContainerId) + "/ready"
                );
                dictLast = dictResult;
                var sStatus = dictResult.sStatus || "";
                if (sStatus === "stalled") return dictResult;
                if (sStatus === "failed") return dictResult;
                if (dictResult.bReady) return dictResult;
            } catch (error) {
                /* container may not be fully started yet */
            }
            await new Promise(function (fnResolve) {
                setTimeout(fnResolve, iIntervalMs);
            });
        }
        return dictLast || {
            bReady: false, sStatus: "timeout",
            sReason: "Container did not become ready in time.",
            saWarnings: [], iWarningCount: 0,
        };
    }

    function _fnSurfaceReadinessOutcome(dictReadiness) {
        if (!dictReadiness) return;
        var sStatus = dictReadiness.sStatus || "";
        if (sStatus === "failed") {
            _fnShowReadinessFailureBanner(dictReadiness);
            return;
        }
        if (sStatus === "stalled") {
            _fnShowReadinessStalledBanner();
            return;
        }
        var listWarnings = dictReadiness.saWarnings || [];
        if (listWarnings.length > 0) {
            _fnShowReadinessWarningBanner(listWarnings);
        }
    }

    function _fnShowReadinessFailureBanner(dictReadiness) {
        var sReason = dictReadiness.sReason || "Unknown failure.";
        var sMessage =
            "Container start failed: " + sReason +
            " Run `vaibify stop && vaibify build && vaibify start`.";
        PipeleyenApp.fnShowToast(sMessage, "error");
    }

    function _fnShowReadinessStalledBanner() {
        PipeleyenApp.fnShowToast(
            "Container is running but not responding to exec. " +
            "Try `vaibify stop && vaibify start`.",
            "error",
        );
    }

    function _fnShowReadinessWarningBanner(listWarnings) {
        var iCount = listWarnings.length;
        var sLabel = iCount === 1 ? "1 warning" : iCount + " warnings";
        var sJoined = listWarnings.map(function (sLine) {
            return "- " + sLine;
        }).join("\n");
        PipeleyenApp.fnShowToast(
            "Container started with " + sLabel + ":\n" + sJoined,
            "warning",
        );
    }

    function _fnShowInitializingOverlay() {
        var elOverlay = document.getElementById("modalInitializing");
        if (elOverlay) elOverlay.style.display = "flex";
    }

    function _fnHideInitializingOverlay() {
        var elOverlay = document.getElementById("modalInitializing");
        if (elOverlay) elOverlay.style.display = "none";
    }

    async function fnConnectToContainerByName(sName) {
        var sContainerId = await _fsResolveContainerId(sName);
        if (!sContainerId) {
            PipeleyenApp.fnShowToast(
                "Container not found for " + sName, "error");
            return;
        }
        fnConnectToContainer(sContainerId);
    }

    function _fsContainerNameById(sId) {
        var el = document.querySelector(
            '.container-tile[data-container-id="' + sId + '"]' +
            ' .container-tile-name'
        );
        return el ? el.textContent : sId.substring(0, 12);
    }

    async function fnConnectToContainer(sId) {
        try {
            var listWorkflows = await VaibifyApi.fdictGet(
                "/api/workflows/" + sId);
            _sSelectedContainerId = sId;
            _sSelectedContainerName = _fsContainerNameById(sId);
            PipeleyenApp.fnShowWorkflowPicker(_sSelectedContainerName);
            fnRenderWorkflowList(listWorkflows, sId);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function fnRenderWorkflowList(listWorkflows, sId) {
        VaibifyWorkflowManager.fnRenderWorkflowList(
            listWorkflows, sId);
    }

    function fnCreateNewWorkflow() {
        VaibifyWorkflowManager.fnCreateNewWorkflow(
            _sSelectedContainerId);
    }

    function fnBindContainerLandingEvents() {
        document.getElementById("btnRefreshContainers").addEventListener(
            "click", function () {
                fnLoadContainers();
            }
        );
        document.getElementById("btnAddContainer").addEventListener(
            "click", fnOpenAddChoice
        );
        var elNewWindow = document.getElementById("btnNewVaibifyWindow");
        if (elNewWindow) {
            elNewWindow.addEventListener(
                "click", VaibifyUtilities.fnSpawnNewSession,
            );
        }
        document.getElementById("btnShowUnrecognized").addEventListener(
            "click", function () {
                var elList = document.getElementById("listUnrecognized");
                var bVisible = elList.style.display !== "none";
                elList.style.display = bVisible ? "none" : "";
                this.textContent = bVisible
                    ? "Show unrecognized containers"
                    : "Hide unrecognized containers";
            }
        );
        document.addEventListener("click", function () {
            document.querySelectorAll(".container-tile-menu").forEach(
                function (el) { el.style.display = "none"; }
            );
        });
        document.getElementById("btnBrowserBack").addEventListener(
            "click", PipeleyenDirectoryBrowser.fnBrowserNavigateBack
        );
        document.getElementById("btnBrowserForward").addEventListener(
            "click", PipeleyenDirectoryBrowser.fnBrowserNavigateForward
        );
    }

    function fnBindAddContainerModal() {
        document.getElementById("btnAddContainerCancel").addEventListener(
            "click", PipeleyenDirectoryBrowser.fnHandleModalClose
        );
        document.getElementById("btnAddContainerConfirm").addEventListener(
            "click", PipeleyenDirectoryBrowser.fnSelectDirectory
        );
        var elNewFolder = document.getElementById("btnDirectoryNewFolder");
        if (elNewFolder) {
            elNewFolder.addEventListener(
                "click", PipeleyenDirectoryBrowser.fnPromptCreateFolder
            );
        }
        fnBindAddChoiceModal();
        VaibifyWorkflowManager.fnBindCreateWizardModal();
    }

    function fnOpenAddChoice() {
        document.getElementById("modalAddChoice").style.display = "flex";
    }

    function fnBindAddChoiceModal() {
        document.getElementById("btnAddChoiceCancel").addEventListener(
            "click", function () {
                document.getElementById("modalAddChoice")
                    .style.display = "none";
            }
        );
        document.getElementById("btnChoiceAddExisting").addEventListener(
            "click", function () {
                document.getElementById("modalAddChoice")
                    .style.display = "none";
                PipeleyenDirectoryBrowser.fnOpenDirectoryBrowser();
            }
        );
        document.getElementById("btnChoiceCreateNew").addEventListener(
            "click", function () {
                document.getElementById("modalAddChoice")
                    .style.display = "none";
                VaibifyWorkflowManager.fnOpenCreateWizard();
            }
        );
        var elHelp = document.getElementById("btnAddChoiceHelp");
        if (elHelp) {
            elHelp.addEventListener("click", _fnShowAddChoiceHelp);
        }
    }

    function _fnShowAddChoiceHelp() {
        PipeleyenModals.fnShowInfoModal(
            "Add Container — Help", _S_ADD_CHOICE_HELP);
        var elInfo = document.getElementById("modalInfo");
        if (elInfo) elInfo.style.zIndex = "1200";
    }

    var _S_ADD_CHOICE_HELP =
        '<p><strong>Add Existing</strong> &mdash; point vaibify at a ' +
        'directory on your host that already contains a ' +
        '<code>vaibify.yml</code> file. The directory might be a ' +
        'project a collaborator shared with you, a project you cloned ' +
        'from GitHub, or one you created previously and removed from ' +
        'the registry. Vaibify reads the existing config and registers ' +
        'the project &mdash; nothing is overwritten.</p>' +
        '<p><strong>Create New</strong> &mdash; launch the wizard that ' +
        'walks you through creating a brand new project from scratch. ' +
        'You pick a directory (existing or new), choose a starter ' +
        'template, configure features and packages, and vaibify writes ' +
        'a fresh <code>vaibify.yml</code> for you. Use this when you ' +
        'are starting a project, not when you already have one.</p>' +
        '<p>Both paths produce the same kind of registered project ' +
        'afterward; the only difference is whether the configuration ' +
        'file already exists.</p>';

    function fsGetSelectedContainerId() {
        return _sSelectedContainerId;
    }

    function fsGetSelectedContainerName() {
        return _sSelectedContainerName;
    }

    return {
        fnLoadContainers: fnLoadContainers,
        fnConnectToContainer: fnConnectToContainer,
        fnBindContainerLandingEvents: fnBindContainerLandingEvents,
        fnBindAddContainerModal: fnBindAddContainerModal,
        fnOpenAddChoice: fnOpenAddChoice,
        fnBindAddChoiceModal: fnBindAddChoiceModal,
        fnCreateNewWorkflow: fnCreateNewWorkflow,
        fsGetSelectedContainerId: fsGetSelectedContainerId,
        fsGetSelectedContainerName: fsGetSelectedContainerName,
        fnReleaseClaim: fnReleaseClaim,
    };
})();
