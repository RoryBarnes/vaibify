/* Vaibify — Sync operations (Overleaf, GitHub, Zenodo) */

var VaibifySyncManager = (function () {
    "use strict";

    var _sPushService = "";

    var _DICT_SYNC_ERROR_MESSAGES = {
        auth: "Authentication failed. Check your credentials " +
            "in Sync > Setup.",
        rateLimit: "Rate limited. Try again in a few minutes.",
        notFound: "Resource not found. Check your project ID " +
            "or DOI.",
        network: "Network error. Check your container's " +
            "internet connection.",
    };

    async function fnOpenPushModal(sService) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var dictResult = await VaibifyApi.fdictGet(
            "/api/sync/" + sContainerId + "/check/" + sService
        );
        if (!dictResult.bConnected) {
            fnShowConnectionSetup(sService);
            return;
        }
        _sPushService = sService;
        fnPopulatePushModal(sService);
    }

    async function fnPopulatePushModal(sService) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var listFiles = await VaibifyApi.fdictGet(
            "/api/sync/" + sContainerId + "/files"
        );
        var dictNames = {
            overleaf: "Overleaf", zenodo: "Zenodo",
            github: "GitHub",
        };
        document.getElementById("modalPushTitle").textContent =
            "Push to " + dictNames[sService];
        _fnRenderPushFileList(listFiles);
        document.getElementById("modalPush").style.display = "flex";
    }

    function _fnRenderPushFileList(listFiles) {
        var elList = document.getElementById("modalPushFileList");
        var bOverleaf = _sPushService === "overleaf";
        elList.innerHTML = listFiles.map(function (dictFile) {
            var bSupporting = bOverleaf &&
                dictFile.sCategory === "supporting";
            return '<div class="push-file-row' +
                (bSupporting ? " push-file-supporting" : "") +
                '">' +
                '<input type="checkbox" class="push-file-checkbox" ' +
                'data-path="' +
                PipeleyenApp.fnEscapeHtml(dictFile.sPath) +
                '"' + (bSupporting ? "" : " checked") +
                (bSupporting ? " disabled" : "") + '>' +
                '<span class="push-file-name">' +
                PipeleyenApp.fnEscapeHtml(dictFile.sPath) +
                (bSupporting ? " (supporting)" : "") +
                '</span></div>';
        }).join("");
    }

    async function fnHandlePushConfirm() {
        var listPaths = [];
        document.querySelectorAll(
            ".push-file-checkbox:checked"
        ).forEach(function (el) {
            listPaths.push(el.dataset.path);
        });
        if (listPaths.length === 0) {
            PipeleyenApp.fnShowToast("No files selected", "error");
            return;
        }
        document.getElementById("modalPush").style.display = "none";
        PipeleyenApp.fnShowToast(
            "Pushing " + listPaths.length + " files...", "success");
        var sEndpoint = _fsServiceEndpoint(_sPushService);
        var sAction = _fsServiceAction(_sPushService);
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                sEndpoint + sContainerId + "/" + sAction,
                {listFilePaths: listPaths}
            );
            if (!dictResult.bSuccess) {
                fnShowSyncError(dictResult, _sPushService);
                return;
            }
            PipeleyenApp.fnShowToast("Push complete!", "success");
            PipeleyenApp.fnRenderStepList();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    function fnShowSyncError(dictResult, sService) {
        var sErrorType = dictResult.sErrorType || "unknown";
        var sMessage = _DICT_SYNC_ERROR_MESSAGES[sErrorType] ||
            dictResult.sMessage || "Unknown error";
        var sTitle = (sService || "Sync") + " failed: " +
            sErrorType;
        _fnShowErrorModal(sTitle + "\n\n" + sMessage);
    }

    function _fsServiceEndpoint(sService) {
        if (sService === "overleaf") return "/api/overleaf/";
        if (sService === "zenodo") return "/api/zenodo/";
        return "/api/github/";
    }

    function _fsServiceAction(sService) {
        if (sService === "zenodo") return "archive";
        return "push";
    }

    function fnBindPushModalEvents() {
        document.getElementById("btnPushCancel").addEventListener(
            "click", function () {
                document.getElementById("modalPush")
                    .style.display = "none";
            }
        );
        document.getElementById("btnPushConfirm").addEventListener(
            "click", fnHandlePushConfirm
        );
        document.getElementById("btnPushSelectAll").addEventListener(
            "click", function () {
                document.querySelectorAll(".push-file-checkbox")
                    .forEach(function (el) { el.checked = true; });
            }
        );
        fnBindConnectionSetupEvents();
    }

    function fnShowConnectionSetup(sService) {
        var elModal = document.getElementById("modalConnectionSetup");
        elModal.dataset.service = sService;
        var elProjectId = document.getElementById(
            "groupSetupProjectId");
        var elToken = document.getElementById("groupSetupToken");
        elProjectId.style.display = "none";
        elToken.style.display = "none";
        if (sService === "overleaf") {
            _fnSetupOverleafFields(elProjectId, elToken, elModal);
        } else if (sService === "zenodo") {
            _fnSetupZenodoFields(elToken, elModal);
        } else {
            PipeleyenApp.fnShowToast(
                "GitHub uses gh auth. Run 'gh auth login' " +
                "on your host machine.", "error"
            );
            return;
        }
        elModal.style.display = "flex";
    }

    function _fnSetupOverleafFields(elProjectId, elToken, elModal) {
        elProjectId.style.display = "";
        elToken.style.display = "";
        var elLabel = document.getElementById("labelSetupToken");
        var elHelp = document.getElementById("helpSetupToken");
        elLabel.textContent = "Overleaf Password ";
        if (elHelp) {
            elHelp.setAttribute("title",
                "Enter your Overleaf account password. " +
                "Overleaf uses this as the git password " +
                "for its git bridge. Go to Account > " +
                "Password to set or reset it.");
            elLabel.appendChild(elHelp);
        }
        document.getElementById("modalConnectionTitle")
            .textContent = "Connect to Overleaf";
    }

    function _fnSetupZenodoFields(elToken, elModal) {
        elToken.style.display = "";
        var elLabel = document.getElementById("labelSetupToken");
        var elHelp = document.getElementById("helpSetupToken");
        elLabel.textContent = "Zenodo API Token ";
        if (elHelp) elLabel.appendChild(elHelp);
        document.getElementById("modalConnectionTitle")
            .textContent = "Connect to Zenodo";
    }

    function fnBindConnectionSetupEvents() {
        document.getElementById("btnSetupCancel").addEventListener(
            "click", function () {
                document.getElementById("modalConnectionSetup")
                    .style.display = "none";
            }
        );
        document.getElementById("btnSetupSave").addEventListener(
            "click", _fnHandleSetupSave
        );
        document.addEventListener("click", function (event) {
            var elHelp = event.target.closest(".help-icon");
            if (!elHelp) return;
            var sText = elHelp.getAttribute("title");
            if (!sText) return;
            event.preventDefault();
            event.stopPropagation();
            fnShowHelpPopup(sText);
        });
    }

    function fnShowHelpPopup(sText) {
        var elExisting = document.getElementById("popupHelp");
        if (elExisting) elExisting.remove();
        var elPopup = document.createElement("div");
        elPopup.id = "popupHelp";
        elPopup.className = "help-popup";
        elPopup.innerHTML =
            '<div class="help-popup-content">' +
            '<span class="help-popup-close">&times;</span>' +
            '<p>' + PipeleyenApp.fnEscapeHtml(sText) + '</p></div>';
        document.body.appendChild(elPopup);
        elPopup.querySelector(".help-popup-close").addEventListener(
            "click", function () { elPopup.remove(); }
        );
    }

    async function _fnHandleSetupSave() {
        var elModal = document.getElementById("modalConnectionSetup");
        var sService = elModal.dataset.service;
        var dictBody = { sService: sService };
        var sProjectId = document.getElementById(
            "inputSetupProjectId").value.trim();
        var sToken = document.getElementById(
            "inputSetupToken").value.trim();
        if (sProjectId) dictBody.sProjectId = sProjectId;
        if (sToken) dictBody.sToken = sToken;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/sync/" + sContainerId + "/setup",
                dictBody
            );
            elModal.style.display = "none";
            if (dictResult.bConnected) {
                PipeleyenApp.fnShowToast("Connected!", "success");
                fnOpenPushModal(sService);
            } else {
                PipeleyenApp.fnShowToast(
                    dictResult.sMessage || "Connection failed",
                    "error"
                );
            }
        } catch (error) {
            PipeleyenApp.fnShowToast(
                _fsSanitizeError(error.message), "error");
        }
    }

    function _fsSanitizeError(sMessage) {
        if (typeof PipeleyenApp.fsSanitizeErrorForUser === "function") {
            return PipeleyenApp.fsSanitizeErrorForUser(sMessage);
        }
        return sMessage || "An error occurred.";
    }

    function _fnShowErrorModal(sMessage) {
        var elModal = document.getElementById("modalError");
        var elContent = document.getElementById("modalErrorContent");
        elContent.textContent = _fsSanitizeError(sMessage);
        elModal.style.display = "flex";
    }

    return {
        fnOpenPushModal: fnOpenPushModal,
        fnBindPushModalEvents: fnBindPushModalEvents,
        fnShowSyncError: fnShowSyncError,
        fnShowHelpPopup: fnShowHelpPopup,
    };
})();
