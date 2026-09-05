/* Vaibify — Container landing page (extracted from scriptApplication.js) */

var VaibifyContainerManager = (function () {
    "use strict";

    var _sSelectedContainerId = null;
    var _sSelectedContainerName = null;
    var _sSelectedContainerDirectory = "";
    var _bSelectedContainerIsProject = false;

    async function fnLoadContainers() {
        try {
            await fnRefreshContainerHub();
        } catch (error) {
            _fnShowContainerListLoadError();
        }
    }

    /* Throwing variant for the picker poller: a registry fetch failure
       must propagate so VaibifyPolling can route it to the connection
       monitor instead of leaving a stale list and hammering on. */
    async function fnRefreshContainerHub() {
        await _fnRefreshDockerStatusBanner();
        var dictResult = await VaibifyApi.fdictGet(_fsRegistryUrl());
        fnRenderContainerList(dictResult.listContainers || []);
        fnRenderUnrecognizedList(dictResult.listUnrecognized || []);
    }

    function _fnShowContainerListLoadError() {
        var elList = document.getElementById("listContainers");
        elList.innerHTML =
            '<p style="color: var(--color-red-text);">' +
            "Cannot load containers</p>";
    }

    async function _fnRefreshDockerStatusBanner() {
        var elBanner = document.getElementById("dockerStatusBanner");
        if (!elBanner) return;
        try {
            var dictStatus = await VaibifyApi.fdictGet(
                "/api/system/docker-status"
            );
            if (dictStatus.bAvailable) {
                elBanner.style.display = "none";
                elBanner.innerHTML = "";
                return;
            }
            _fnRenderDockerStatusBanner(elBanner, dictStatus);
        } catch (error) {
            elBanner.style.display = "none";
        }
    }

    function _fnRenderDockerStatusBanner(elBanner, dictStatus) {
        var sHint = VaibifyUtilities.fnEscapeHtml(dictStatus.sHint || "");
        var sCommand = VaibifyUtilities.fnEscapeHtml(
            dictStatus.sCommand || ""
        );
        var sError = VaibifyUtilities.fnEscapeHtml(dictStatus.sError || "");
        var sEndpoint = VaibifyUtilities.fnEscapeHtml(
            dictStatus.sEndpoint || ""
        );
        elBanner.innerHTML =
            '<div class="docker-status-banner-message">' +
            '<strong>Docker is unavailable.</strong> ' + sHint +
            (sEndpoint
                ? ' Endpoint vaibify used: <code>' + sEndpoint
                  + '</code>.'
                : '') +
            (sCommand
                ? ' <code>' + sCommand + '</code>'
                : '') +
            (sError
                ? '<div class="docker-status-banner-detail">'
                  + sError + '</div>'
                : '') +
            '</div>' +
            '<div class="docker-status-banner-actions">' +
            '<button id="btnRetryDockerStatus" type="button">'
            + 'Retry</button></div>';
        elBanner.style.display = "flex";
        var elRetry = document.getElementById("btnRetryDockerStatus");
        if (elRetry) {
            elRetry.addEventListener("click", _fnRetryDockerStatus);
        }
    }

    async function _fnRetryDockerStatus() {
        var elRetry = document.getElementById("btnRetryDockerStatus");
        if (elRetry) elRetry.disabled = true;
        try {
            var dictStatus = await VaibifyApi.fdictPostRaw(
                "/api/system/docker-status/retry"
            );
            if (dictStatus.bAvailable) {
                VaibifyApp.fnShowToast(
                    "Docker is available", "success"
                );
                await fnLoadContainers();
                return;
            }
            VaibifyApp.fnShowToast(
                "Docker still unavailable. " + (dictStatus.sHint || ""),
                "error"
            );
            await _fnRefreshDockerStatusBanner();
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error"
            );
        } finally {
            var elRetryAfter = document.getElementById(
                "btnRetryDockerStatus"
            );
            if (elRetryAfter) elRetryAfter.disabled = false;
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
                VaibifyUtilities.fnEscapeHtml(c.sContainerId) +
                '" data-name="' +
                VaibifyUtilities.fnEscapeHtml(c.sName) + '">' +
                '<span class="name">' +
                VaibifyUtilities.fnEscapeHtml(c.sName) + "</span>" +
                '<span class="image">' +
                VaibifyUtilities.fnEscapeHtml(c.sImage) + "</span></div>"
            );
        }).join("");
        elList.querySelectorAll(".container-card").forEach(function (el) {
            el.addEventListener("click", function () {
                _fnClaimAndConnectUnrecognized(
                    el.dataset.id, el.dataset.name);
            });
        });
    }

    async function _fnClaimAndConnectUnrecognized(sId, sName) {
        /* An unrecognized container has no registry entry, but its docker
           NAME is still the canonical claim key. Claim it before
           connecting so the session holds a lease the pipeline and
           terminal WebSockets can present; without the claim every WS
           closes 4403. The claim is keyed by name, the connect by id. */
        if (sName) {
            var bClaimed = await fbClaimContainer(sName);
            if (!bClaimed) return;
        }
        fnConnectToContainer(sId);
    }

    function _fbHostProject(dictContainer) {
        /* Absent sMode means container: that is what every registry
           entry written before host mode existed meant. */
        return dictContainer.sMode === "host";
    }

    function _fbIsProject(dictContainer) {
        /* A container is a Project by definition; a host entry is a
           Project only when it has been promoted (bIsProject true). The
           backend enrichment carries the flag, so the tile never
           re-derives the sandbox/Project distinction. */
        if (!_fbHostProject(dictContainer)) return true;
        return dictContainer.bIsProject === true;
    }

    function fsRenderContainerTile(dictContainer) {
        var sStatusClass = _fsStatusDotClass(dictContainer.sStatus);
        var bHost = _fbHostProject(dictContainer);
        var bIsProject = _fbIsProject(dictContainer);
        /* A host project has no container, so the registry sends no
           sContainerId. Its resource id IS its registry name -- the
           same substitution the backend claim path makes -- and the
           tile has to carry it, or the click path resolves nothing
           and returns silently. */
        var sId = bHost
            ? (dictContainer.sName || "")
            : (dictContainer.sContainerId || "");
        var bQuarantined = dictContainer.bQuarantined === true;
        /* A quarantined container is refused for a reason the researcher
           can act on, so it must NOT wear the generic "locked" grey and
           its "in use by another session" message -- that mislabels a
           zombie-process problem as somebody else's tab. It gets its own
           attention state and a clickable chip instead. */
        var bUnavailable = !bQuarantined && _fbContainerUnavailable(
            dictContainer);
        var sStateClass = bQuarantined
            ? " container-tile--quarantined"
            : (bUnavailable ? " container-tile--locked" : "");
        var sLockedMessage = _fsUnavailableMessage(dictContainer);
        var sLockedTitle = bUnavailable
            ? ' title="' + sLockedMessage + '"' : "";
        var sLockedAttr = bUnavailable
            ? ' data-locked="true" data-locked-message="' +
              sLockedMessage + '"'
            : "";
        return (
            '<div class="container-tile' + sStateClass +
            '" data-name="' +
            VaibifyUtilities.fnEscapeHtml(dictContainer.sName) +
            '" data-container-id="' + VaibifyUtilities.fnEscapeHtml(sId) +
            '" data-mode="' + (bHost ? "host" : "container") +
            '" data-is-project="' + (bIsProject ? "true" : "false") +
            '" data-quarantined="' + (bQuarantined ? "true" : "false") +
            '"' + _fsRenderHostTileData(dictContainer, bHost) +
            sLockedAttr + sLockedTitle + '>' +
            '<div class="container-tile-main">' +
            '<span class="status-dot ' + sStatusClass + '"></span>' +
            '<span class="container-tile-name">' +
            VaibifyUtilities.fnEscapeHtml(dictContainer.sName) + "</span>" +
            _fsRenderContainmentChip(dictContainer, bHost) +
            _fsRenderHostTileNote(dictContainer, bHost) +
            "</div>" +
            '<button class="btn-icon container-tile-actions" ' +
            'title="Actions">&#8942;</button>' +
            _fsRenderTileGear(bHost) +
            '<div class="container-tile-menu" style="display:none;">' +
            _fsRenderContainerOnlyMenuItems(bHost) +
            _fsRenderHostConvertMenuItem(bHost) +
            '<div class="container-menu-item danger" ' +
            'data-action="remove">Remove from list</div>' +
            "</div></div>"
        );
    }

    function _fsRenderContainmentChip(dictContainer, bHost) {
        /* The list is grouped by MACHINE, so the tile has to say the
           other thing: whether the work is contained. It used to be
           said by the status dot alone, in the brand colour, which
           spent vaibify's own blue on "this one is the odd one out"
           and told a researcher nothing about why. The uncontained
           chip deliberately matches the in-workflow badge, because it
           is the same claim about the same project.

           A quarantined container overrides that label entirely: the
           chip becomes a clickable "quarantined" button that opens the
           explanation, because the researcher's first question is not
           "is this contained" but "why can't I get in". */
        if (dictContainer.bQuarantined === true) {
            return (
                '<button type="button" class="containment-chip ' +
                'containment-chip--quarantined" ' +
                'data-quarantine-name="' +
                VaibifyUtilities.fnEscapeHtml(dictContainer.sName) +
                '" title="Why is this container blocked?">' +
                "quarantined</button>"
            );
        }
        return (
            '<span class="containment-chip containment-chip--' +
            (bHost ? "direct" : "contained") + '">' +
            (bHost ? "uncontained" : "contained") + "</span>"
        );
    }

    function _fsRenderHostTileData(dictContainer, bHost) {
        /* The directory is what the acknowledgement is keyed by, and
           whether it HAS been acknowledged is the backend's answer --
           it canonicalises the path, so a symlinked alias of an
           accepted project is not warned about a second time. */
        if (!bHost) return "";
        return (
            ' data-directory="' +
            VaibifyUtilities.fnEscapeHtml(dictContainer.sDirectory || "") +
            '" data-warning-acknowledged="' +
            (dictContainer.bHostWarningAcknowledged ? "true" : "false") +
            '"'
        );
    }

    function _fsRenderTileGear(bHost) {
        /* The settings modal is entirely container fields -- keep the
           host awake while the CONTAINER runs, CPU and memory limits
           on a container that does not exist. Hiding it is courtesy;
           the routes refuse a host project on their own. */
        if (bHost) return "";
        return (
            '<button class="btn-icon container-tile-gear" ' +
            'title="Settings">&#9881;</button>'
        );
    }

    function _fsRenderContainerOnlyMenuItems(bHost) {
        /* Start, stop, restart and the two rebuilds all drive Docker
           machinery a host project has none of. The server refuses
           them with a 409 naming host mode; this only keeps the
           researcher from being offered them. */
        if (bHost) return "";
        return (
            '<div class="container-menu-item" data-action="start">' +
            "Start</div>" +
            '<div class="container-menu-item" data-action="cancel-start">' +
            "Cancel Start</div>" +
            '<div class="container-menu-item" data-action="stop">' +
            "Stop</div>" +
            '<div class="container-menu-item" data-action="restart">' +
            "Restart</div>" +
            '<div class="container-menu-item" data-action="rebuild">' +
            "Rebuild</div>" +
            '<div class="container-menu-item" data-action="force-rebuild">' +
            "Force Rebuild</div>" +
            '<div class="container-menu-separator"></div>'
        );
    }

    function _fsRenderHostConvertMenuItem(bHost) {
        /* Only a host tile carries this action; a container tile has
           nothing here.

           ONE label for both host states (2026-09-04). It used to read
           "Make a Project…" for a sandbox and "Containerize…" for a
           promoted host Project, which asked a researcher to create
           the thing they had spent the whole walkthrough working
           inside: the dashboard says Project everywhere, the file is
           named project.json, and the PROOF tab grades it -- so an
           action offering to MAKE one reads as though the project
           does not exist (researcher-reported). The sandbox/Project
           distinction is a registry flag, not something the reader can
           see, and containerizing is what either state is here to do.

           The noun is ENVIRONMENT, not Project (researcher-reported
           2026-09-04, second pass). Nothing here happens to the
           project: the directory, the git history and the outputs are
           untouched, and the tile sits in the Environments hub. What
           the action replaces is the interpreter, the libraries and
           the OS the steps run against -- which is also the only
           thing PROOF Level 3 is asking for. "Containerize Project"
           named the one part that does not change.

           The two flows still differ underneath: a sandbox reaches the
           destination step (host Project or container), a promoted
           Project skips it because there is no destination left to
           choose. */
        if (!bHost) return "";
        var sLabel = "Containerize Environment";
        return (
            '<div class="container-menu-item" data-action="convert">' +
            sLabel + "</div>" +
            '<div class="container-menu-separator"></div>'
        );
    }

    function _fsRenderHostTileNote(dictContainer, bHost) {
        /* A missing host project is a directory that moved or a
           config that was deleted. Naming the path is the whole
           remedy: the researcher can see at a glance whether they
           renamed a folder or are looking at a stale entry. */
        if (!bHost || dictContainer.sStatus !== "missing") return "";
        return (
            '<span class="container-tile-note">' +
            VaibifyUtilities.fnEscapeHtml(
                dictContainer.sDirectory || "directory unknown") +
            "</span>"
        );
    }

    function _fbContainerUnavailable(dictContainer) {
        return dictContainer.bLocked === true
            || dictContainer.bOwnedByOtherSession === true;
    }

    function _fsUnavailableMessage(dictContainer) {
        if (dictContainer.bOwnedByOtherSession === true) {
            return "In use in another browser session.";
        }
        if (dictContainer.bLocked === true) {
            return _fsLockedMessage(dictContainer.iLockedByPort);
        }
        return "";
    }

    function _fsLockedMessage(iLockedByPort) {
        var sSuffix = iLockedByPort
            ? " on port " + iLockedByPort : "";
        return "In use by another vaibify session" + sSuffix + ".";
    }

    async function _fnShowQuarantineExplanation(sName) {
        /* The "why" a researcher would otherwise reach an agent for. The
           detail route is read-only and returns only the journal's
           allowlisted fields, so nothing here needs the container's
           lease. */
        try {
            var dictDetail = await VaibifyApi.fdictGet(
                "/api/registry/" + encodeURIComponent(sName) +
                "/quarantine");
            VaibifyModals.fnShowInfoModal(
                "Container '" + sName + "' is blocked (quarantined)",
                _fsRenderQuarantineExplanation(dictDetail));
            _fnBindQuarantineRemedyCopy(dictDetail.sRemedy || "");
            _fnBindQuarantineReconcile(sName, dictDetail);
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    function _fsRenderQuarantineExplanation(dictDetail) {
        var listRecords = dictDetail.listRecords || [];
        var sIntro =
            "<p>Vaibify blocked this container because it could not " +
            "prove that every operation on it finished cleanly — most " +
            "often a terminal or command left a process running inside " +
            "the container. This is a safety check to keep the " +
            "dashboard honest, <strong>not</strong> data loss: your " +
            "workspace is untouched.</p>";
        var sBody = listRecords.length
            ? _fsRenderQuarantineRecords(listRecords)
            : ("<p>The container's operation journal could not be read " +
               "(state: " +
               VaibifyUtilities.fnEscapeHtml(dictDetail.sReadState || "") +
               ").</p>");
        var sActions = dictDetail.bReconcilableHere
            ? ('<p><strong>To clear it,</strong> reconcile: vaibify ' +
               "re-checks every operation above and unblocks the " +
               "container once all of them are proven finished.</p>" +
               '<button class="btn btn-primary ' +
               'quarantine-reconcile-button" type="button">' +
               "Reconcile now</button>" +
               '<p class="quarantine-reconcile-outcome" ' +
               'style="display:none"></p>')
            : ("<p>This journal cannot be cleared from the dashboard " +
               "— run the command below on the host machine, which " +
               "can inspect it and offer the recovery options.</p>");
        var sRemedy =
            "<p>The command-line equivalent, on the host machine (not " +
            "inside the container):</p>" +
            '<pre class="quarantine-remedy">' +
            VaibifyUtilities.fnEscapeHtml(dictDetail.sRemedy || "") +
            "</pre>" +
            '<button class="btn quarantine-copy-button" type="button">' +
            "Copy command</button>";
        return sIntro + sBody + sActions + sRemedy;
    }

    function _fnBindQuarantineReconcile(sName, dictDetail) {
        var elButton = document.querySelector(
            "#modalInfo .quarantine-reconcile-button");
        if (!elButton) return;
        var listExpectedIds = (dictDetail.listRecords || []).map(
            function (dictRecord) { return dictRecord.sOperationId; });
        elButton.addEventListener("click", function () {
            _fnReconcileQuarantine(sName, listExpectedIds, dictDetail);
        });
    }

    async function _fnReconcileQuarantine(sName, listExpectedIds, dictDetail) {
        /* The same proving transaction the CLI runs: it clears the
           quarantine only when every shown record proves settled, and
           otherwise reports the refusal verbatim. On a refusal a
           container project is offered the kernel-proven escalation —
           stop the container (nothing survives that), reconcile again. */
        var elButton = document.querySelector(
            "#modalInfo .quarantine-reconcile-button");
        if (elButton) {
            elButton.disabled = true;
            elButton.textContent = "Reconciling…";
        }
        try {
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) +
                "/reconcile",
                { listExpectedOperationIds: listExpectedIds });
        } catch (error) {
            _fnOfferQuarantineEscalation(
                sName, listExpectedIds, dictDetail, error);
            return;
        }
        _fnFinishQuarantineReconcile(sName);
    }

    function _fnFinishQuarantineReconcile(sName) {
        var elModal = document.getElementById("modalInfo");
        if (elModal) elModal.remove();
        VaibifyApp.fnShowToast(
            "Container '" + sName + "' reconciled — it is claimable " +
            "again.", "success");
        fnLoadContainers();
    }

    function _fnOfferQuarantineEscalation(
        sName, listExpectedIds, dictDetail, error
    ) {
        var elOutcome = document.querySelector(
            "#modalInfo .quarantine-reconcile-outcome");
        var elButton = document.querySelector(
            "#modalInfo .quarantine-reconcile-button");
        if (elButton) {
            elButton.disabled = false;
            elButton.textContent = "Reconcile now";
        }
        if (!elOutcome) return;
        elOutcome.style.display = "";
        elOutcome.textContent = "Refused: " +
            VaibifyUtilities.fsSanitizeErrorForUser(error.message);
        if (dictDetail.bHostProject) return;
        if (elOutcome.dataset.bEscalationOffered === "1") return;
        elOutcome.dataset.bEscalationOffered = "1";
        var elStop = document.createElement("button");
        elStop.className = "btn quarantine-stop-certify-button";
        elStop.type = "button";
        elStop.textContent = "Stop container & certify";
        elStop.title = "Stops the container — nothing survives a " +
            "stop, so the check can then prove it settled — and " +
            "reconciles again. Start the container afterwards as usual.";
        elOutcome.insertAdjacentElement("afterend", elStop);
        elStop.addEventListener("click", function () {
            _fnStopAndCertifyQuarantine(sName, listExpectedIds, elStop);
        });
    }

    async function _fnStopAndCertifyQuarantine(sName, listExpectedIds, elStop) {
        elStop.disabled = true;
        elStop.textContent = "Stopping…";
        try {
            await VaibifyApi.fdictPost(
                "/api/containers/" + encodeURIComponent(sName) + "/stop");
            elStop.textContent = "Certifying…";
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) +
                "/reconcile",
                { listExpectedOperationIds: listExpectedIds });
        } catch (error) {
            elStop.disabled = false;
            elStop.textContent = "Stop container & certify";
            var elOutcome = document.querySelector(
                "#modalInfo .quarantine-reconcile-outcome");
            if (elOutcome) {
                elOutcome.textContent = "Refused: " +
                    VaibifyUtilities.fsSanitizeErrorForUser(error.message);
            }
            return;
        }
        _fnFinishQuarantineReconcile(sName);
    }

    function _fnBindQuarantineRemedyCopy(sRemedy) {
        /* The info modal renders plain HTML, so the button is wired
           here, after it exists in the DOM. */
        var elButton = document.querySelector(
            "#modalInfo .quarantine-copy-button");
        if (!elButton || !sRemedy) return;
        elButton.addEventListener("click", function () {
            VaibifyFileOps.fnCopyToClipboard(sRemedy);
            elButton.textContent = "Copied";
            window.setTimeout(function () {
                elButton.textContent = "Copy command";
            }, 1500);
        });
    }

    function _fsRenderQuarantineRecords(listRecords) {
        var sRows = listRecords.map(function (dictRecord) {
            var sReason = dictRecord.sNote || dictRecord.sState || "";
            var sWhen = dictRecord.sPreparedIso
                ? ' <span class="quarantine-when">(since ' +
                  VaibifyUtilities.fnEscapeHtml(dictRecord.sPreparedIso) +
                  ")</span>"
                : "";
            return (
                "<li><strong>" +
                VaibifyUtilities.fnEscapeHtml(
                    dictRecord.sKind || "operation") +
                "</strong>: " +
                VaibifyUtilities.fnEscapeHtml(sReason) + sWhen + "</li>");
        }).join("");
        return (
            "<p>Unsettled operations still holding this container:</p>" +
            "<ul class=\"quarantine-records\">" + sRows + "</ul>");
    }

    function _fsStatusDotClass(sStatus) {
        if (sStatus === "running") return "status-running";
        if (sStatus === "stopped") return "status-stopped";
        /* Host vocabulary. A host project is ready or its directory is
           gone; it is never built, started or stopped, and reusing
           "not built" for it would offer a build that cannot happen. */
        if (sStatus === "ready") return "status-host-ready";
        if (sStatus === "missing") return "status-missing";
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
        var elQuarantine = elTile.querySelector(
            ".containment-chip--quarantined");
        if (elQuarantine) {
            elQuarantine.addEventListener("click", function (event) {
                /* Stop the bubble to the tile-main handler so the modal
                   opens exactly once, from the affordance the chip is. */
                event.stopPropagation();
                _fnCloseAllActionsMenus();
                _fnShowQuarantineExplanation(sName);
            });
        }
        elActions.addEventListener("click", function (event) {
            event.stopPropagation();
            _fnToggleActionsMenu(elMenu);
        });
        /* A host tile renders no gear -- its settings are all
           container settings -- so binding one is conditional. */
        if (elGear) {
            elGear.addEventListener("click", function (event) {
                event.stopPropagation();
                _fnCloseAllActionsMenus();
                fnShowContainerSettings(sName);
            });
        }
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
        if (elTile && elTile.dataset.quarantined === "true") {
            await _fnShowQuarantineExplanation(sName);
            return;
        }
        if (elTile && elTile.dataset.locked === "true") {
            VaibifyApp.fnShowToast(
                "Container '" + sName + "': " +
                (elTile.dataset.lockedMessage ||
                 _fsLockedMessage(0)), "warning");
            return;
        }
        if (elTile && elTile.dataset.mode === "host") {
            await _fnOpenHostProject(sName, elTile);
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
        var bClaimed = await fbClaimContainer(sName);
        if (!bClaimed) {
            await fnLoadContainers();
            return;
        }
        var sStoredId = elTile ? elTile.dataset.containerId : "";
        var sTargetId = sStoredId ||
            await fsResolveContainerId(sName);
        if (!sTargetId) return;
        _fnShowInitializingOverlay();
        var dictReadiness = await _fdictWaitForContainerReady(sTargetId);
        _fnHideInitializingOverlay();
        _fnSurfaceReadinessOutcome(dictReadiness);
        if (!dictReadiness || !dictReadiness.bReady) {
            var sStatus = dictReadiness ? dictReadiness.sStatus : "";
            if (sStatus !== "failed" && sStatus !== "stalled") {
                VaibifyApp.fnShowToast(
                    "Container took too long to initialize. "
                    + "Connecting anyway — some data may be "
                    + "incomplete.", "warning");
            }
        }
        fnConnectToContainer(sTargetId);
    }

    async function _fnOpenHostProject(sName, elTile) {
        /* A host project skips every step the container path takes
           before connecting: there is no image to build, nothing to
           start, and no entrypoint to become ready. It is ready when
           the directory and its config are there, and the claim is
           the only arbitration left. Falling into the container path
           would offer a build for a project that can never have one
           -- the `not built -> click -> build` trap. */
        var elDot = elTile.querySelector(".status-dot");
        if (elDot && elDot.classList.contains("status-missing")) {
            var elNote = elTile.querySelector(".container-tile-note");
            VaibifyApp.fnShowToast(
                "Project '" + sName + "' is not on disk any more" +
                (elNote ? ": " + elNote.textContent : "") +
                ". Restore the directory and its vaibify.yml, or " +
                "remove the project from the list.", "error");
            return;
        }
        var bClaimed = await fbClaimContainer(sName);
        if (!bClaimed) {
            await fnLoadContainers();
            return;
        }
        /* The warning comes AFTER the claim: arbitration has to run
           first, or a researcher reads and accepts a disclosure about
           a project another session is already holding. */
        if (elTile.dataset.warningAcknowledged !== "true") {
            _fnWarnBeforeEnteringHostProject(sName, elTile);
            return;
        }
        /* The resource id of a host project is its registry name. */
        fnConnectToContainer(elTile.dataset.containerId || sName);
    }

    var _S_HOST_WARNING_TITLE =
        "⚠ You are working directly on your host machine.";

    var _S_HOST_WARNING_BODY =
        "Changes are not contained in a Docker environment. Pipeline " +
        "commands and any AI agent you run here execute with your " +
        "full user authority: your files, your network, your stored " +
        "credentials, and vaibify's own state on this machine.\n\n" +
        "Vaibify cannot prove that finished runs left nothing behind, " +
        "cannot reach reproducibility Level 3, and cannot provide " +
        "Supervised attribution for this project.\n\n" +
        "For contained, attestable work, create a containerized " +
        "project.";

    function _fnWarnBeforeEnteringHostProject(sName, elTile) {
        VaibifyModals.fnShowConfirmModal(
            _S_HOST_WARNING_TITLE, _S_HOST_WARNING_BODY,
            function (bDoNotWarnAgain) {
                _fnEnterHostProject(sName, elTile, bDoNotWarnAgain);
            },
            {
                sCheckboxLabel:
                    "Don't warn me again for this project",
                sCancelLabel: "Go back",
                sConfirmLabel: "I understand, continue",
                /* Going back must give the project up. The claim
                   already succeeded, so without this the project
                   stays held by a tab that never opened it and the
                   picker renders it as somebody else's. */
                fnOnCancel: async function () {
                    await fnReleaseClaim(sName);
                    await fnLoadContainers();
                },
            }
        );
    }

    async function _fnEnterHostProject(sName, elTile, bDoNotWarnAgain) {
        if (bDoNotWarnAgain) {
            await _fnRecordHostWarningAcknowledged(elTile);
        }
        fnConnectToContainer(elTile.dataset.containerId || sName);
    }

    async function _fnRecordHostWarningAcknowledged(elTile) {
        /* Keyed by the project DIRECTORY, never the display name: a
           reused name must not suppress the warning for a different
           directory. The backend canonicalises it. */
        var sDirectory = elTile.dataset.directory || "";
        if (!sDirectory) return;
        try {
            await VaibifyApi.fdictPut(
                "/api/preferences/host-warning-acknowledged",
                { sProjectDirectory: sDirectory }
            );
        } catch (error) {
            /* A preference that failed to save is a nuisance, not a
               reason to refuse entry -- the warning simply shows
               again next time, which is the safe direction. */
            VaibifyApp.fnShowToast(
                "Could not save the preference; the warning will " +
                "show again next time.", "warning");
        }
    }

    async function fbClaimContainer(sName) {
        /* Any re-claim lease rides the X-Vaibify-Lease header the
           authenticated-fetch wrapper attaches, never a query param. */
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) +
                "/claim", {});
            VaibifyApp.fnRecordClaimedLease(sName, dictResult.sLeaseId);
            return true;
        } catch (error) {
            _fnReportClaimRefusal(sName, error);
            return false;
        }
    }

    function _fnReportClaimRefusal(sName, error) {
        var dictDetail = error.dictDetail || {};
        var sReason = dictDetail.sMessage
            || (dictDetail.iLockedByPort
                ? _fsLockedMessage(dictDetail.iLockedByPort)
                : _fsLockedMessage(0));
        VaibifyApp.fnShowToast(
            "Container '" + sName + "': " + sReason, "warning");
    }

    async function fnReleaseClaim(sName) {
        if (!sName) return;
        /* No lease for this name means this tab cannot release it --
           the server refuses a lease-less release -- and firing the
           doomed request anyway races its fnForgetLease below against
           a claim this tab may be making on ANOTHER name (the
           promotion re-entry hit exactly that: the stray release's
           completion wiped the freshly claimed lease). */
        if (!VaibifyApp.fsGetLeaseForContainer(sName)) return;
        /* The owning lease rides the X-Vaibify-Lease header the
           authenticated-fetch wrapper attaches, never a query param. */
        try {
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sName) +
                "/release", {});
        } catch (error) {
            /* A 409 is a RETAINED refusal: the container is still
               ours (a run is live, or an agent is working in it), so
               dropping the lease here would leave this tab unable to
               act on a container it still owns, and the picker would
               render it as somebody else's. Say so and keep the
               lease. Any other failure stays best-effort -- the grace
               reaper cleans up. */
            var iStatus = (error && error.iStatus) || 0;
            if (iStatus === 409) {
                VaibifyApp.fnShowToast(
                    (error.dictDetail && error.dictDetail.sMessage) ||
                    error.message, "warning");
                return;
            }
            /* An AMBIGUOUS failure -- no status at all (timeout,
               dropped connection) or a server error -- does not say
               whether the release committed. Forgetting the lease on
               a maybe stranded this tab exactly as a 409 would: it
               could no longer act on a container it may still own.
               Only a CONFIRMED outcome may drop the lease, so keep it
               and let the grace reaper be the backstop. A definite
               4xx below means the lease is already worthless. */
            if (!iStatus || iStatus >= 500) {
                VaibifyApp.fnShowToast(
                    "Could not confirm the release of '" + sName +
                    "'. Keeping this session's claim -- retry, or let " +
                    "it time out.", "warning");
                return;
            }
        }
        VaibifyApp.fnForgetLease();
    }

    async function fnHandleContainerAction(sName, sAction) {
        if (sAction === "start") await fnStartContainer(sName);
        else if (sAction === "cancel-start")
            await fnCancelStartContainer(sName);
        else if (sAction === "stop") await fnStopContainer(sName);
        else if (sAction === "restart") await fnRestartContainer(sName);
        else if (sAction === "rebuild") await fnRebuildContainer(sName);
        else if (sAction === "force-rebuild")
            await fnForceRebuildContainer(sName);
        else if (sAction === "convert") _fnStartConversion(sName);
        else if (sAction === "remove") await fnRemoveContainer(sName);
    }

    function _fnStartConversion(sName) {
        /* The convert wizard needs the project's directory too. It is
           read from the tile by name-equality rather than an attribute
           selector, so a host name carrying a space cannot break the
           lookup.

           This door containerizes, in BOTH host states (2026-09-04
           ruling). It used to hand a sandbox the destination choice --
           host Project or container -- so a researcher who had just
           clicked "Containerize Environment" was asked whether they meant
           it, with one card offering to keep the project exactly where
           it already was. Promotion to a host Project did not go away:
           it lives on the Files panel's own "Convert to Project" bar,
           which is shown for a sandbox and calls this same wizard
           WITHOUT this argument, so it still opens the choice. One
           door, one outcome. */
        var elTile = _felTileByName(sName);
        var sDirectory = elTile ? (elTile.dataset.directory || "") : "";
        VaibifyWorkflowManager.fnOpenConvertWizard(
            sName, sDirectory, false);
    }

    function _felTileByName(sName) {
        var listTiles = document.querySelectorAll(".container-tile");
        for (var i = 0; i < listTiles.length; i++) {
            if (listTiles[i].dataset.name === sName) return listTiles[i];
        }
        return null;
    }

    async function fnShowContainerSettings(sName) {
        try {
            var dictSettings = await VaibifyApi.fdictGet(
                "/api/containers/" + encodeURIComponent(sName)
                + "/settings"
            );
            fnShowContainerSettingsModal(sName, dictSettings);
        } catch (error) {
            VaibifyApp.fnShowToast(
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
            _fsRenderResourceLimitSettings(dictSettings) +
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
                var iCpuLimit = _fiParsePositiveNumber(
                    "settingCpuLimit", true);
                var fMemoryLimitGigabytes = _fiParsePositiveNumber(
                    "settingMemoryLimit", false);
                elModal.remove();
                await fnSaveContainerSettings(sName, {
                    bNeverSleep: bNeverSleep,
                    iCpuLimit: iCpuLimit,
                    fMemoryLimitGigabytes: fMemoryLimitGigabytes,
                });
            });
    }

    function _fsRenderResourceLimitSettings(dictSettings) {
        var sCpuValue = dictSettings.iCpuLimit > 0
            ? String(dictSettings.iCpuLimit) : "";
        var sMemoryValue = dictSettings.fMemoryLimitGigabytes > 0
            ? String(dictSettings.fMemoryLimitGigabytes) : "";
        return '<div class="settings-option">' +
            '<div class="settings-option-row">' +
            '<input type="number" id="settingCpuLimit" min="1" ' +
            'step="1" placeholder="all cores − 1" value="' +
            sCpuValue + '" class="settings-number-input">' +
            '<span class="settings-option-label">' +
            'CPU core limit</span></div>' +
            '<div class="settings-option-row">' +
            '<input type="number" id="settingMemoryLimit" ' +
            'min="0.25" step="0.25" placeholder="unlimited" ' +
            'value="' + sMemoryValue +
            '" class="settings-number-input">' +
            '<span class="settings-option-label">' +
            'Memory limit (GB)</span></div>' +
            '<p class="settings-option-help">' +
            'Blank means no limit. Applied via docker run the ' +
            'next time the container starts.</p>' +
            '</div>';
    }

    function _fiParsePositiveNumber(sId, bInteger) {
        /* Blank, zero, or unparsable all mean "no limit" (0). */
        var elInput = document.getElementById(sId);
        if (!elInput) return 0;
        var fParsed = bInteger
            ? parseInt(elInput.value, 10)
            : parseFloat(elInput.value);
        return (isNaN(fParsed) || fParsed <= 0) ? 0 : fParsed;
    }

    async function fnSaveContainerSettings(sName, dictSettings) {
        try {
            await VaibifyApi.fdictPost(
                "/api/containers/" + encodeURIComponent(sName)
                + "/settings",
                dictSettings
            );
            VaibifyApp.fnShowToast(
                "Settings saved. Use Restart to apply.",
                "success");
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        }
    }

    var _iBuildProgressTimer = null;

    function _fnRenderBuildProgress(dictProgress) {
        var elTail = document.getElementById("buildProgressTail");
        if (!elTail) return;
        var saLines = dictProgress.saTailLines || [];
        if (!saLines.length) return;
        elTail.style.display = "block";
        elTail.textContent = saLines.join("\n");
        elTail.scrollTop = elTail.scrollHeight;
    }

    function _fnStartBuildProgressPoll(sName) {
        _fnStopBuildProgressPoll();
        _iBuildProgressTimer = setInterval(async function () {
            try {
                _fnRenderBuildProgress(await _fdictFetchBuildProgress(
                    sName));
            } catch (error) {
                // A transient poll failure must not disturb the build;
                // the blocking POST owns success/failure reporting.
            }
        }, 1500);
    }

    function _fnStopBuildProgressPoll() {
        if (_iBuildProgressTimer !== null) {
            clearInterval(_iBuildProgressTimer);
            _iBuildProgressTimer = null;
        }
    }

    function _fdictFetchBuildProgress(sName) {
        return VaibifyApi.fdictGet(
            "/api/containers/" + encodeURIComponent(sName)
            + "/build/progress");
    }

    function _fnResetBuildProgressTail() {
        var elTail = document.getElementById("buildProgressTail");
        if (!elTail) return;
        elTail.textContent = "";
        elTail.style.display = "none";
    }

    async function fnBuildContainer(sName, bNoCache) {
        /* Returns whether the container is now BUILT AND RUNNING. It
           reports its own failures, so a caller has nothing to add --
           but a caller with follow-on work needs to know not to
           attempt it. Without this, a failed build was followed by an
           attempt to copy files into a container that did not exist,
           and the researcher was told their files were not copied
           because it was "not reporting as running yet" -- true, and
           useless, when the real answer was three lines up in the
           build log (live report, 2026-08-21). */
        var elOverlay = document.getElementById("modalBuildProgress");
        var bBuiltAndRunning = false;
        _fnResetBuildProgressTail();
        elOverlay.style.display = "flex";
        _fnStartBuildProgressPoll(sName);
        try {
            var sUrl = "/api/containers/" +
                encodeURIComponent(sName) + "/build";
            if (bNoCache) sUrl += "?bNoCache=true";
            await VaibifyApi.fdictPostRaw(sUrl);
            VaibifyApp.fnShowToast("Build complete", "success");
            await fnStartContainer(sName);
            bBuiltAndRunning = true;
        } catch (error) {
            if (error.iStatus === 409) {
                bBuiltAndRunning = await _fnWatchRunningBuild(sName);
            } else {
                _fnReportBuildFailure(error);
            }
        } finally {
            _fnStopBuildProgressPoll();
            elOverlay.style.display = "none";
            fnLoadContainers();
        }
        return bBuiltAndRunning;
    }

    async function _fnWatchRunningBuild(sName) {
        // The 409 means a build for this project is already running —
        // typically started by a tab that has since closed. The docker
        // build outlives the request that started it, so watch that
        // build to completion instead of reporting a failure.
        VaibifyApp.fnShowToast(
            "A build for this project is already running; " +
            "attaching to its progress.", "info");
        var dictProgress;
        while (true) {
            try {
                dictProgress = await _fdictFetchBuildProgress(sName);
            } catch (error) {
                VaibifyApp.fnShowToast(
                    "Lost contact with the running build; refresh " +
                    "to check its status.", "error");
                return false;
            }
            _fnRenderBuildProgress(dictProgress);
            if (!dictProgress.bLive) break;
            await new Promise(function (fnResolve) {
                setTimeout(fnResolve, 1500);
            });
        }
        if (dictProgress.sOutcome === "succeeded") {
            VaibifyApp.fnShowToast("Build complete", "success");
            await fnStartContainer(sName);
            return true;
        }
        VaibifyApp.fnShowToast(
            "The running build failed; see the hub log for the " +
            "full output.", "error");
        return false;
    }

    function _fnReportBuildFailure(error) {
        var sTail = (error.dictDetail && error.dictDetail.sStderrTail)
            || "";
        if (!sTail) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
            return;
        }
        _fnShowBuildFailureModal(error.message, sTail);
    }

    function _fnShowBuildFailureModal(sMessage, sTail) {
        var elModal = document.getElementById("modalBuildFailure");
        var elMessage = document.getElementById("buildFailureMessage");
        var elTail = document.getElementById("buildFailureTail");
        var elClose = document.getElementById("buttonBuildFailureClose");
        elMessage.textContent =
            VaibifyUtilities.fsSanitizeErrorForUser(sMessage);
        elTail.textContent = sTail;
        elClose.onclick = function () {
            elModal.style.display = "none";
        };
        elModal.style.display = "flex";
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

    /* Reservation ids of failed starts this tab has already SHOWN the
       researcher, keyed by container name. Naming one is how the next
       start says "I read that failure and mean to try again" -- the
       server refuses a start that would silently relaunch after an
       unacknowledged failure (design 10b). */
    var _dictAcknowledgedStartFailure = {};

    var _I_START_POLL_INTERVAL_MILLISECONDS = 1000;
    var _I_START_POLL_LIMIT = 900;
    /* A start survives the page that asked for it, so the RESUMED poll
       runs while a just-reloaded page is still settling -- the first
       /start-status can transiently fail (the session token not yet
       re-established, a network blip). Abandoning the start on that one
       failure stranded the researcher's running container and flaked
       the reload-resume browser test. Tolerate a bounded run of
       consecutive poll errors, retrying within the poll budget; a
       PERSISTENT failure still propagates. */
    var _I_START_POLL_ERROR_TOLERANCE = 5;

    /* The name of a start this tab is following, remembered across a
       reload. A start outlives the request that asked for it, so a
       researcher who reloads mid-start used to be stranded: the server
       was still pulling, the outcome and the lease were waiting on the
       poll, and nothing in the reloaded page was polling. sessionStorage
       is per-tab and survives a reload, which is exactly the scope of
       "this tab is following that start". */
    var S_PENDING_START_KEY = "vaibifyPendingStartName";

    function _fnRememberPendingStart(sName) {
        try {
            window.sessionStorage.setItem(S_PENDING_START_KEY, sName);
        } catch (error) {
            /* A tab with storage disabled simply loses reload recovery;
               it must not lose the ability to start a container. */
        }
    }

    function _fnForgetPendingStart() {
        try {
            window.sessionStorage.removeItem(S_PENDING_START_KEY);
        } catch (error) {
            /* See above. */
        }
    }

    async function fnResumeInterruptedStart() {
        var sName = null;
        try {
            sName = window.sessionStorage.getItem(S_PENDING_START_KEY);
        } catch (error) {
            return;
        }
        if (!sName) return;
        fnSetTilePending(sName);
        try {
            await _fnFollowStartToItsOutcome(sName, {});
        } catch (error) {
            _fnForgetPendingStart();
        }
    }

    async function fnStartContainer(sName) {
        fnSetTilePending(sName);
        _fnRememberPendingStart(sName);
        try {
            var dictStart = await VaibifyApi.fdictPost(
                _fsContainerUrl(sName, "/start"),
                _fdictStartBody(sName)
            );
            await _fnFollowStartToItsOutcome(sName, dictStart);
        } catch (error) {
            _fnForgetPendingStart();
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        } finally {
            fnLoadContainers();
        }
    }

    function _fsContainerUrl(sName, sSuffix) {
        return "/api/containers/" + encodeURIComponent(sName) + sSuffix;
    }

    function _fdictStartBody(sName) {
        var sAcknowledged = _dictAcknowledgedStartFailure[sName];
        delete _dictAcknowledgedStartFailure[sName];
        return sAcknowledged
            ? {sAcknowledgeReservationId: sAcknowledged} : {};
    }

    /* The start is a server-owned reservation: the POST only reserves
       it, so the outcome -- and the container's lease -- arrive from
       the status poll. Reporting "started" on the 202 would tell the
       researcher a container is running when it may still be pulling,
       or may have already failed. */
    async function _fnFollowStartToItsOutcome(sName, dictStart) {
        VaibifyApp.fnShowToast("Starting container...", "info");
        var iAttempt = 0;
        var iConsecutiveErrors = 0;
        while (iAttempt < _I_START_POLL_LIMIT) {
            var dictStatus;
            try {
                dictStatus = await VaibifyApi.fdictGet(
                    _fsContainerUrl(sName, "/start-status")
                );
                iConsecutiveErrors = 0;
            } catch (error) {
                /* A transient poll failure must not abandon a start that
                   is still running on the server; retry within the poll
                   budget. Only a PERSISTENT failure propagates. */
                iConsecutiveErrors += 1;
                if (iConsecutiveErrors > _I_START_POLL_ERROR_TOLERANCE) {
                    throw error;
                }
                await _fnSleepMilliseconds(
                    _I_START_POLL_INTERVAL_MILLISECONDS);
                iAttempt += 1;
                continue;
            }
            if (dictStatus.sState !== "PENDING") {
                _fnReportStartOutcome(sName, dictStatus);
                return;
            }
            _fnWarnOnStalledStart(sName, dictStatus, iAttempt);
            await _fnSleepMilliseconds(_I_START_POLL_INTERVAL_MILLISECONDS);
            iAttempt += 1;
        }
        VaibifyApp.fnShowToast(
            "Container '" + sName + "' is still starting. Its status "
            + "stays available; use Cancel Start if it is stuck.",
            "warning");
    }

    function _fnReportStartOutcome(sName, dictStatus) {
        _fnForgetPendingStart();
        if (dictStatus.sState === "SUCCEEDED") {
            if (dictStatus.sLeaseId) {
                VaibifyApp.fnRecordClaimedLease(sName, dictStatus.sLeaseId);
            }
            VaibifyApp.fnShowToast("Container started", "success");
            return;
        }
        /* OWNED is not an outcome: it means no start result is on
           record and this session still owns the container, which is
           what a reload after a long start now recovers. It carries the
           live lease, so the tab can act again; claiming it "started"
           would invent a start that did not happen in this window. */
        if (dictStatus.sState === "OWNED") {
            if (dictStatus.sLeaseId) {
                VaibifyApp.fnRecordClaimedLease(sName, dictStatus.sLeaseId);
            }
            VaibifyApp.fnShowToast(
                "Container '" + sName + "' is running and still yours.",
                "success");
            return;
        }
        _dictAcknowledgedStartFailure[sName] = dictStatus.sReservationId;
        VaibifyApp.fnShowToast(
            VaibifyUtilities.fsSanitizeErrorForUser(
                "Start failed: " + (dictStatus.sError || "unknown error")
            ),
            "error");
    }

    function _fnWarnOnStalledStart(sName, dictStatus, iAttempt) {
        if (!dictStatus.bHeartbeatStale || iAttempt % 30 !== 0) return;
        VaibifyApp.fnShowToast(
            "Container '" + sName + "' has made no start progress for a "
            + "while. Use Cancel Start to stop it.", "warning");
    }

    function _fnSleepMilliseconds(iMilliseconds) {
        return new Promise(function (fnResolve) {
            setTimeout(fnResolve, iMilliseconds);
        });
    }

    async function fnCancelStartContainer(sName) {
        try {
            var dictCancel = await VaibifyApi.fdictPostRaw(
                _fsContainerUrl(sName, "/start/cancel")
            );
            _fnReportCancelOutcome(sName, dictCancel);
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        } finally {
            fnLoadContainers();
        }
    }

    function _fnReportCancelOutcome(sName, dictCancel) {
        if (dictCancel.sReservationId) {
            _dictAcknowledgedStartFailure[sName] = dictCancel.sReservationId;
        }
        if (dictCancel.bQuarantined) {
            VaibifyApp.fnShowToast(
                "The start was stopped, but the container could not be "
                + "proven clean, so it is quarantined. Run 'vaibify "
                + "reconcile " + sName + "' before starting it again.",
                "warning");
            return;
        }
        VaibifyApp.fnShowToast(
            dictCancel.bCancelled
                ? "Start cancelled"
                : (dictCancel.sMessage || "Start is still terminating"),
            dictCancel.bCancelled ? "success" : "warning");
    }

    async function fnStopContainer(sName) {
        fnSetTilePending(sName);
        VaibifyTerminal.fnCloseAll();
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/containers/" + encodeURIComponent(sName)
                + "/stop"
            );
            VaibifyApp.fnShowToast("Container stopped", "success");
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(error.message),
                "error");
        } finally {
            fnLoadContainers();
        }
    }

    async function fnRestartContainer(sName) {
        VaibifyApp.fnShowConfirmModal(
            "Restart Container",
            "Stop the container and start it again using the " +
            "current image. Open terminal sessions will close. " +
            "Workspace files are preserved.",
            async function () {
                VaibifyTerminal.fnCloseAll();
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
        VaibifyApp.fnShowConfirmModal(
            "Rebuild Container",
            "Stop the container, rebuild the image with your " +
            "current vaibify.yml settings, then start a fresh " +
            "container. Open terminal sessions will close. " +
            "Workspace files are preserved.",
            async function () {
                VaibifyTerminal.fnCloseAll();
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
        VaibifyApp.fnShowConfirmModal(
            "Force Rebuild (Slow)",
            "Rebuild every layer of the image from scratch, " +
            "ignoring the build cache. This can take several " +
            "minutes. Workspace files are preserved.",
            async function () {
                VaibifyTerminal.fnCloseAll();
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
        VaibifyApp.fnShowConfirmModal(
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
                    VaibifyApp.fnShowToast(
                        "Container removed", "success");
                } catch (error) {
                    VaibifyApp.fnShowToast(
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

    function _fsRegistryUrl() {
        /* The caller's lease rides the X-Vaibify-Lease header the
           authenticated-fetch wrapper attaches; the registry endpoint reads
           it only to grey tiles another session holds. */
        return "/api/registry";
    }

    async function fsResolveContainerId(sName) {
        try {
            var dictResult = await VaibifyApi.fdictGet(_fsRegistryUrl());
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
        _fnRenderBuildWarningsBanner(
            (dictReadiness && dictReadiness.saWarnings) || []
        );
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
        if (sStatus === "stale-image"
                || sStatus === "stale-version") {
            _fnShowStaleImageBanner(dictReadiness);
            return;
        }
        var listWarnings = dictReadiness.saWarnings || [];
        if (listWarnings.length > 0) {
            _fnShowReadinessWarningBanner(listWarnings);
        }
    }

    function _fnRenderBuildWarningsBanner(listWarnings) {
        var elBanner = document.getElementById("buildWarningsBanner");
        if (!elBanner) return;
        if (!listWarnings || listWarnings.length === 0) {
            elBanner.style.display = "none";
            elBanner.innerHTML = "";
            return;
        }
        var sLabel = listWarnings.length === 1
            ? "1 build warning"
            : listWarnings.length + " build warnings";
        var sItems = listWarnings.map(function (sLine) {
            return "<li>"
                + VaibifyUtilities.fnEscapeHtml(sLine)
                + "</li>";
        }).join("");
        elBanner.innerHTML =
            '<div class="build-warnings-banner-header">' +
            '<span>' + VaibifyUtilities.fnEscapeHtml(sLabel)
            + ' from the most recent container start</span>' +
            '<button type="button" '
            + 'class="build-warnings-banner-dismiss" '
            + 'id="btnDismissBuildWarnings" '
            + 'aria-label="Dismiss build warnings">×</button>' +
            '</div>' +
            '<ul class="build-warnings-banner-list">'
            + sItems + '</ul>';
        elBanner.style.display = "block";
        var elDismiss = document.getElementById(
            "btnDismissBuildWarnings"
        );
        if (elDismiss) {
            elDismiss.addEventListener("click", function () {
                elBanner.style.display = "none";
                elBanner.innerHTML = "";
            });
        }
    }

    function _fnShowStaleImageBanner(dictReadiness) {
        var sReason = dictReadiness.sReason
            || "Container image is out of date. Rebuild via kebab menu.";
        VaibifyApp.fnShowToast(sReason, "warning");
    }

    function _fnShowReadinessFailureBanner(dictReadiness) {
        var sReason = dictReadiness.sReason || "Unknown failure.";
        var sMessage =
            "Container start failed: " + sReason +
            " Run `vaibify stop && vaibify build && vaibify start`.";
        VaibifyApp.fnShowToast(sMessage, "error");
    }

    function _fnShowReadinessStalledBanner() {
        VaibifyApp.fnShowToast(
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
        VaibifyApp.fnShowToast(
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
        var sContainerId = await fsResolveContainerId(sName);
        if (!sContainerId) {
            VaibifyApp.fnShowToast(
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

    function _fsContainerModeById(sId) {
        /* Read back off the tile the registry listing rendered, so the
           project-list screen shows the mode the SERVER reported.
           Unknown ids (an unrecognized container) read as container,
           which is what they are. */
        var el = document.querySelector(
            '.container-tile[data-container-id="' + sId + '"]'
        );
        return (el && el.dataset.mode) || "container";
    }

    function _fsContainerDirectoryById(sId) {
        /* The host tile carries its directory in data-directory; the
           toolbar shows it because a host sandbox IS its directory. A
           container tile has none, and the empty string is correct
           there -- its toolbar shows the container name instead. */
        var el = document.querySelector(
            '.container-tile[data-container-id="' + sId + '"]'
        );
        return (el && el.dataset.directory) || "";
    }

    function _fbIsProjectById(sId) {
        /* The tile carries data-is-project from the registry truth. It
           gates the Files-panel "Convert to Project" affordance: a
           project that IS one already must not be offered the
           conversion. A container tile has no such attribute and reads
           false, which is harmless -- the Files button is host-only. */
        var el = document.querySelector(
            '.container-tile[data-container-id="' + sId + '"]'
        );
        return Boolean(el && el.dataset.isProject === "true");
    }

    async function fnConnectToContainer(sId) {
        try {
            var listWorkflows = await VaibifyApi.fdictGet(
                "/api/workflows/" + sId);
            _sSelectedContainerId = sId;
            _sSelectedContainerName = _fsContainerNameById(sId);
            _sSelectedContainerDirectory = _fsContainerDirectoryById(sId);
            _bSelectedContainerIsProject = _fbIsProjectById(sId);
            VaibifyApp.fnApplyProjectMode(_fsContainerModeById(sId));
            VaibifyApp.fnShowWorkflowPicker(_sSelectedContainerName);
            fnRenderWorkflowList(listWorkflows, sId);
        } catch (error) {
            VaibifyApp.fnShowToast(
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
            "click", VaibifyDirectoryBrowser.fnBrowserNavigateBack
        );
        document.getElementById("btnBrowserForward").addEventListener(
            "click", VaibifyDirectoryBrowser.fnBrowserNavigateForward
        );
    }

    function fnBindAddContainerModal() {
        document.getElementById("btnAddContainerCancel").addEventListener(
            "click", VaibifyDirectoryBrowser.fnHandleModalClose
        );
        document.getElementById("btnAddContainerConfirm").addEventListener(
            "click", VaibifyDirectoryBrowser.fnSelectDirectory
        );
        var elNewFolder = document.getElementById("btnDirectoryNewFolder");
        if (elNewFolder) {
            elNewFolder.addEventListener(
                "click", VaibifyDirectoryBrowser.fnPromptCreateFolder
            );
        }
        fnBindAddChoiceModal();
        VaibifyWorkflowManager.fnBindCreateWizardModal();
        VaibifyNewWorkflowWizard.fnBindEventHandlers();
    }

    /* The environment kind chosen on stage 1, carried into stage 2.
       "" means stage 1 is still on screen. */
    var _sChosenEnvironmentKind = "";

    function fnOpenAddChoice() {
        _fnShowAddChoiceStage("");
        document.getElementById("modalAddChoice").style.display = "flex";
    }

    function _fnShowAddChoiceStage(sKind) {
        /* One dialog, two stages: stage 1 asks WHERE the work runs,
           stage 2 asks how the project gets here. The host tier used to
           be a third card on stage 1, which put "Add Container" over a
           choice between a container and not-a-container. Both kinds
           now take the same second stage, and the disclosure appears
           with the host kind -- before any directory is chosen. */
        _sChosenEnvironmentKind = sKind;
        document.getElementById("addChoiceCards").style.display =
            sKind ? "none" : "";
        document.getElementById("addChoiceHowStage").style.display =
            sKind ? "" : "none";
        document.getElementById("addChoiceHostNote").style.display =
            sKind === "host" ? "" : "none";
        _fnSetAddChoiceTitle(sKind);
    }

    function _fnSetAddChoiceTitle(sKind) {
        /* The title carries the chosen kind, so the second stage never
           asks a researcher to remember which branch they took. */
        var elTitle = document.getElementById("addChoiceTitle");
        if (!elTitle) return;
        if (!sKind) {
            elTitle.textContent = "Add Environment";
            return;
        }
        elTitle.textContent = "Add Environment — " + (
            sKind === "host" ? "This machine" : "Container"
        );
    }

    function _fnCloseAddChoice() {
        document.getElementById("modalAddChoice").style.display = "none";
        _fnShowAddChoiceStage("");
    }

    function fnBindAddChoiceModal() {
        document.getElementById("btnAddChoiceCancel").addEventListener(
            "click", _fnCloseAddChoice
        );
        _fnBindAddChoiceCard("btnChoiceAddExisting", "existing");
        _fnBindAddChoiceCard("btnChoiceCreateNew", "create");
        _fnBindEnvironmentKindCard("btnChoiceKindContainer", "container");
        _fnBindEnvironmentKindCard("btnChoiceKindHost", "host");
        var elHelp = document.getElementById("btnAddChoiceHelp");
        if (elHelp) {
            elHelp.addEventListener("click", _fnShowAddChoiceHelp);
        }
    }

    function _fnBindEnvironmentKindCard(sElementId, sKind) {
        document.getElementById(sElementId).addEventListener(
            "click", function () { _fnShowAddChoiceStage(sKind); }
        );
    }

    function _fnBindAddChoiceCard(sElementId, sPath) {
        /* The kind is read at CLICK time, not bound at wiring time:
           one pair of cards now serves both kinds, so the mode is
           whatever stage 1 last chose. */
        document.getElementById(sElementId).addEventListener(
            "click", function () {
                var sMode = _sChosenEnvironmentKind || "container";
                _fnCloseAddChoice();
                if (sPath === "existing") {
                    VaibifyDirectoryBrowser.fnOpenDirectoryBrowser(sMode);
                    return;
                }
                VaibifyWorkflowManager.fnOpenCreateWizard(sMode);
            }
        );
    }

    function _fnShowAddChoiceHelp() {
        VaibifyModals.fnShowInfoModal(
            "Add Environment — Help", _S_ADD_CHOICE_HELP);
        var elInfo = document.getElementById("modalInfo");
        if (elInfo) elInfo.style.zIndex = "1200";
    }

    var _S_ADD_CHOICE_HELP =
        '<p>An <strong>environment</strong> is a place your projects ' +
        'run. Adding one takes two steps: first where it runs, then ' +
        'whether the project already exists.</p>' +
        '<p><strong>Container</strong> &mdash; vaibify builds a Docker ' +
        'image from your <code>vaibify.yml</code> and runs every step ' +
        'inside it. The environment is pinned and rebuildable, which ' +
        'is what lets a result reach reproducibility Level 3 and what ' +
        'lets vaibify attest that an AI agent changed only what it ' +
        'says it changed. Requires Docker, and the first build takes ' +
        'minutes to hours.</p>' +
        '<p><strong>This machine</strong> &mdash; vaibify runs your ' +
        'steps directly, with no container. There is nothing to build, ' +
        'so you can start immediately, and it is the right choice for ' +
        'experimentation. In exchange the commands run with your full ' +
        'user authority &mdash; your files, your network, your stored ' +
        'credentials &mdash; and vaibify cannot reach Level 3 or ' +
        'provide Supervised attribution for that project. You are ' +
        'shown this again, in full, before you open one.</p>' +
        '<p><strong>Add Existing</strong> &mdash; point vaibify at a ' +
        'directory that already contains a <code>vaibify.yml</code>: ' +
        'one a collaborator shared, one you cloned from GitHub, or one ' +
        'you registered before and removed. The existing config is ' +
        'read, never overwritten.</p>' +
        '<p><strong>Create New</strong> &mdash; the wizard writes a ' +
        'fresh <code>vaibify.yml</code>. You pick a directory, a ' +
        'starter template and a name; a container environment also ' +
        'asks about features and packages, which a host one has no ' +
        'use for.</p>' +
        '<p>The choice is not permanent in the sense that matters: ' +
        'the same directory can be registered as a container ' +
        'environment later, and starting on this machine is a ' +
        'reasonable way to begin.</p>';

    function fsGetSelectedContainerId() {
        return _sSelectedContainerId;
    }

    function fsGetSelectedContainerName() {
        return _sSelectedContainerName;
    }

    function fsGetSelectedContainerDirectory() {
        return _sSelectedContainerDirectory;
    }

    function fbGetSelectedContainerIsProject() {
        return _bSelectedContainerIsProject;
    }

    return {
        fnLoadContainers: fnLoadContainers,
        fnRefreshContainerHub: fnRefreshContainerHub,
        fnConnectToContainer: fnConnectToContainer,
        fbClaimContainer: fbClaimContainer,
        fsResolveContainerId: fsResolveContainerId,
        fnBindContainerLandingEvents: fnBindContainerLandingEvents,
        fnBindAddContainerModal: fnBindAddContainerModal,
        fnOpenAddChoice: fnOpenAddChoice,
        fnBindAddChoiceModal: fnBindAddChoiceModal,
        fnCreateNewWorkflow: fnCreateNewWorkflow,
        fsGetSelectedContainerId: fsGetSelectedContainerId,
        fsGetSelectedContainerName: fsGetSelectedContainerName,
        fsGetSelectedContainerDirectory: fsGetSelectedContainerDirectory,
        fbGetSelectedContainerIsProject: fbGetSelectedContainerIsProject,
        fnReleaseClaim: fnReleaseClaim,
        fnStartContainer: fnStartContainer,
        fnCancelStartContainer: fnCancelStartContainer,
        fnResumeInterruptedStart: fnResumeInterruptedStart,
        fnBuildContainer: fnBuildContainer,
    };
})();
