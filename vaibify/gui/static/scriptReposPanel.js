/* Vaibify — Repos panel (tracked repos status, push, track/ignore) */

var PipeleyenReposPanel = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    var _sContainerId = null;
    var _dictCachedStatus = {
        listTracked: [],
        listIgnored: [],
        listUndecided: [],
    };
    var _setPromptedNames = null;

    function _felGetContainer() {
        return document.getElementById("reposPanelContainer");
    }

    function _fsApiBase() {
        return "/api/repos/" + _sContainerId;
    }

    async function _fnRefreshNow() {
        if (!_sContainerId) return;
        try {
            var dictStatus = await VaibifyApi.fdictGet(
                _fsApiBase() + "/status"
            );
            fnHandleStatusUpdate(dictStatus);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Failed to refresh repos: " + error.message, "error"
            );
        }
    }

    function _fsStatusDotClass(dictRepo) {
        if (dictRepo.bMissing) return "status-not-built";
        if (dictRepo.bDirty) return "status-pending";
        return "status-running";
    }

    function _fsStatusLabel(dictRepo) {
        if (dictRepo.bMissing) return "missing";
        if (dictRepo.bDirty) return "dirty";
        return "clean";
    }

    function _fsRenderRepoRow(dictRepo) {
        var sDotClass = _fsStatusDotClass(dictRepo);
        var sMissing = dictRepo.bMissing ? " missing" : "";
        var sBranch = dictRepo.sBranch || "";
        var sSubtitle = fnEscapeHtml(sBranch) +
            " &middot; " + _fsStatusLabel(dictRepo);
        return '<div class="repo-tile' + sMissing +
            '" data-name="' + fnEscapeHtml(dictRepo.sName) + '">' +
            '<span class="status-dot ' + sDotClass + '"></span>' +
            '<div class="repo-tile-main">' +
            '<div class="repo-tile-name">' +
            fnEscapeHtml(dictRepo.sName) + '</div>' +
            '<div class="repo-tile-sub">' + sSubtitle + '</div>' +
            '</div>' +
            _fsRenderRowActions(dictRepo) +
            '</div>';
    }

    function _fsRenderRowActions(dictRepo) {
        var bCanPush = !dictRepo.bMissing && !!dictRepo.sUrl;
        var sTitle = "Push staged changes";
        if (dictRepo.bMissing) {
            sTitle = "Missing — re-clone with terminal";
        } else if (!dictRepo.sUrl) {
            sTitle = "No remote configured.";
        }
        var sDisabled = bCanPush ? "" : " disabled";
        return '<div class="repo-tile-actions">' +
            '<button class="btn repo-push-btn" title="' +
            fnEscapeHtml(sTitle) + '"' + sDisabled + '>Push</button>' +
            '<button class="btn-icon repo-gear-btn" ' +
            'title="More actions">&#9881;</button>' +
            '</div>';
    }

    function _felBuildPanelHeader() {
        var elHeader = document.createElement("div");
        elHeader.className = "panel-header";
        var iCount = _dictCachedStatus.listTracked.length;
        elHeader.innerHTML =
            '<span class="repos-panel-title">Repositories</span>' +
            '<span class="repos-badge">' + iCount + '</span>' +
            '<button class="btn-icon" id="btnReposRefresh" ' +
            'title="Refresh now">&#8635;</button>';
        elHeader.querySelector("#btnReposRefresh")
            .addEventListener("click", _fnRefreshNow);
        return elHeader;
    }

    function _felBuildRowsContainer() {
        var elRows = document.createElement("div");
        elRows.className = "repos-rows";
        if (_dictCachedStatus.listTracked.length === 0) {
            elRows.innerHTML =
                '<p class="repos-empty">' +
                'No tracked repositories.</p>';
            return elRows;
        }
        var sHtml = "";
        _dictCachedStatus.listTracked.forEach(function (dictRepo) {
            sHtml += _fsRenderRepoRow(dictRepo);
        });
        elRows.innerHTML = sHtml;
        _fnBindRowEvents(elRows);
        return elRows;
    }

    function fnRender() {
        var elContainer = _felGetContainer();
        if (!elContainer) return;
        elContainer.innerHTML = "";
        elContainer.appendChild(_felBuildPanelHeader());
        elContainer.appendChild(_felBuildRowsContainer());
    }

    function _fdictFindRepo(sName) {
        var listRepos = _dictCachedStatus.listTracked;
        for (var i = 0; i < listRepos.length; i++) {
            if (listRepos[i].sName === sName) return listRepos[i];
        }
        return null;
    }

    function _fnBindRowEvents(elRows) {
        elRows.addEventListener("click", function (event) {
            var elTile = event.target.closest(".repo-tile");
            if (!elTile) return;
            var sName = elTile.dataset.name;
            if (event.target.closest(".repo-push-btn")) {
                _fnHandlePushClick(sName);
            } else if (event.target.closest(".repo-gear-btn")) {
                _fnToggleGearMenu(elTile, sName);
            }
        });
    }

    function _fnToggleGearMenu(elTile, sName) {
        var elExisting = elTile.querySelector(".repo-gear-menu");
        if (elExisting) {
            elExisting.remove();
            return;
        }
        _fnCloseAllGearMenus();
        var dictRepo = _fdictFindRepo(sName);
        if (!dictRepo) return;
        elTile.appendChild(_felBuildGearMenu(dictRepo));
    }

    function _fnCloseAllGearMenus() {
        var listMenus = document.querySelectorAll(".repo-gear-menu");
        listMenus.forEach(function (el) { el.remove(); });
    }

    function _felBuildGearMenu(dictRepo) {
        var elMenu = document.createElement("div");
        elMenu.className = "repo-gear-menu container-tile-menu";
        if (!dictRepo.bMissing) {
            elMenu.appendChild(_felMenuItem(
                "Push files...", "pushFiles", dictRepo.sName));
            elMenu.appendChild(_felMenuItem(
                "Copy URL", "copyUrl", dictRepo.sName));
        }
        elMenu.appendChild(_felMenuItem(
            "Untrack", "untrack", dictRepo.sName, true));
        return elMenu;
    }

    function _felMenuItem(sLabel, sAction, sName, bDanger) {
        var elItem = document.createElement("div");
        elItem.className = "container-menu-item" +
            (bDanger ? " danger" : "");
        elItem.textContent = sLabel;
        elItem.addEventListener("click", function (event) {
            event.stopPropagation();
            _fnCloseAllGearMenus();
            _fnDispatchMenuAction(sAction, sName);
        });
        return elItem;
    }

    function _fnDispatchMenuAction(sAction, sName) {
        if (sAction === "pushFiles") _fnHandlePushFilesClick(sName);
        else if (sAction === "copyUrl") _fnHandleCopyUrlClick(sName);
        else if (sAction === "untrack") _fnHandleUntrackClick(sName);
    }

    function _fnHandlePushClick(sName) {
        var dictRepo = _fdictFindRepo(sName);
        if (!dictRepo || dictRepo.bMissing || !dictRepo.sUrl) return;
        _fnShowCommitMessageModal(
            "Push " + sName,
            function (sMessage) {
                _fnPostPushStaged(sName, sMessage);
            }
        );
    }

    async function _fnPostPushStaged(sName, sMessage) {
        try {
            await VaibifyApi.fdictPost(
                _fsApiBase() + "/" + encodeURIComponent(sName) +
                "/push-staged",
                {sCommitMessage: sMessage}
            );
            PipeleyenApp.fnShowToast("Pushed to remote.", "success");
            _fnRefreshNow();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Push failed: " + error.message, "error"
            );
        }
    }

    async function _fnHandlePushFilesClick(sName) {
        try {
            var dictResult = await VaibifyApi.fdictGet(
                _fsApiBase() + "/" + encodeURIComponent(sName) +
                "/dirty-files"
            );
            var listFiles = dictResult.listDirtyFiles || [];
            if (listFiles.length === 0) {
                PipeleyenApp.fnShowToast(
                    "Nothing to commit.", "info");
                return;
            }
            _fnShowFilePickerModal(sName, listFiles);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Failed to load dirty files: " +
                error.message, "error");
        }
    }

    function _fnHandleUntrackClick(sName) {
        PipeleyenModals.fnShowConfirmModal(
            "Untrack repository",
            "Stop tracking '" + sName + "'?",
            function () { _fnPostUntrack(sName); }
        );
    }

    async function _fnPostUntrack(sName) {
        try {
            await VaibifyApi.fdictPost(
                _fsApiBase() + "/" + encodeURIComponent(sName) +
                "/untrack"
            );
            PipeleyenApp.fnShowToast("Untracked " + sName, "success");
            _fnRefreshNow();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Untrack failed: " + error.message, "error"
            );
        }
    }

    function _fnHandleCopyUrlClick(sName) {
        var dictRepo = _fdictFindRepo(sName);
        if (!dictRepo || !dictRepo.sUrl) {
            PipeleyenApp.fnShowToast(
                "No remote URL configured.", "error"
            );
            return;
        }
        _fnWriteClipboard(dictRepo.sUrl);
    }

    function _fnWriteClipboard(sText) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(sText).then(function () {
                PipeleyenApp.fnShowToast(
                    "URL copied to clipboard.", "success"
                );
            }, function () {
                PipeleyenApp.fnShowToast(
                    "Clipboard unavailable: " + sText, "info"
                );
            });
            return;
        }
        PipeleyenApp.fnShowToast(
            "Clipboard unavailable: " + sText, "info"
        );
    }

    function _felBuildCommitModalBody(sTitle) {
        var elModal = document.createElement("div");
        elModal.id = "modalRepoCommit";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sTitle) + '</h2>' +
            '<input type="text" class="input-modal-field" ' +
            'id="repoCommitMessage" placeholder="Commit message">' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnRepoCommitCancel">' +
            'Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnRepoCommitConfirm">Push</button>' +
            '</div></div>';
        return elModal;
    }

    function _fnShowCommitMessageModal(sTitle, fnCallback) {
        var elExisting = document.getElementById("modalRepoCommit");
        if (elExisting) elExisting.remove();
        var elModal = _felBuildCommitModalBody(sTitle);
        document.body.appendChild(elModal);
        var elInput = elModal.querySelector("#repoCommitMessage");
        elInput.focus();
        _fnBindCommitModalActions(elModal, elInput, fnCallback);
    }

    function _fnBindCommitModalActions(elModal, elInput, fnCallback) {
        document.getElementById("btnRepoCommitCancel")
            .addEventListener("click", function () {
                elModal.remove();
            });
        document.getElementById("btnRepoCommitConfirm")
            .addEventListener("click", function () {
                _fnSubmitCommitModal(elModal, elInput, fnCallback);
            });
        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                _fnSubmitCommitModal(elModal, elInput, fnCallback);
            } else if (event.key === "Escape") {
                elModal.remove();
            }
        });
    }

    function _fnSubmitCommitModal(elModal, elInput, fnCallback) {
        var sValue = elInput.value.trim();
        if (!sValue) {
            PipeleyenApp.fnShowToast(
                "Commit message is required.", "error"
            );
            return;
        }
        elModal.remove();
        fnCallback(sValue);
    }

    function _fsRenderDirtyFileRow(dictFile) {
        var sPath = fnEscapeHtml(dictFile.sPath || "");
        var sStatus = fnEscapeHtml(dictFile.sStatus || "");
        return '<label class="dirty-file-row">' +
            '<input type="checkbox" class="dirty-file-cb" ' +
            'data-path="' + sPath + '" checked>' +
            '<span class="dirty-file-status">' + sStatus + '</span>' +
            '<span class="dirty-file-path">' + sPath + '</span>' +
            '</label>';
    }

    function _fsBuildFilePickerHtml(sName, listFiles) {
        var sRows = listFiles.map(_fsRenderDirtyFileRow).join("");
        return '<div class="modal">' +
            '<h2>Push files — ' + fnEscapeHtml(sName) + '</h2>' +
            '<div class="dirty-file-list">' + sRows + '</div>' +
            '<input type="text" class="input-modal-field" ' +
            'id="repoFilePickerMessage" ' +
            'placeholder="Commit message">' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnRepoPickerCancel">' +
            'Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnRepoPickerConfirm">Push</button>' +
            '</div></div>';
    }

    function _felBuildFilePickerBody(sName, listFiles) {
        var elModal = document.createElement("div");
        elModal.id = "modalRepoFilePicker";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML = _fsBuildFilePickerHtml(sName, listFiles);
        return elModal;
    }

    function _fnShowFilePickerModal(sName, listFiles) {
        var elExisting = document.getElementById("modalRepoFilePicker");
        if (elExisting) elExisting.remove();
        var elModal = _felBuildFilePickerBody(sName, listFiles);
        document.body.appendChild(elModal);
        document.getElementById("btnRepoPickerCancel")
            .addEventListener("click", function () {
                elModal.remove();
            });
        document.getElementById("btnRepoPickerConfirm")
            .addEventListener("click", function () {
                _fnSubmitFilePicker(elModal, sName);
            });
    }

    function _flistCollectCheckedPaths(elModal) {
        var listChecked = elModal.querySelectorAll(
            ".dirty-file-cb:checked"
        );
        var listPaths = [];
        listChecked.forEach(function (elCb) {
            listPaths.push(elCb.dataset.path);
        });
        return listPaths;
    }

    function _fnSubmitFilePicker(elModal, sName) {
        var listPaths = _flistCollectCheckedPaths(elModal);
        if (listPaths.length === 0) {
            PipeleyenApp.fnShowToast(
                "Select at least one file.", "error"
            );
            return;
        }
        var sMessage = elModal.querySelector(
            "#repoFilePickerMessage"
        ).value.trim();
        if (!sMessage) {
            PipeleyenApp.fnShowToast(
                "Commit message is required.", "error"
            );
            return;
        }
        elModal.remove();
        _fnPostPushFiles(sName, sMessage, listPaths);
    }

    async function _fnPostPushFiles(sName, sMessage, listPaths) {
        try {
            await VaibifyApi.fdictPost(
                _fsApiBase() + "/" + encodeURIComponent(sName) +
                "/push-files",
                {sCommitMessage: sMessage, listFilePaths: listPaths}
            );
            PipeleyenApp.fnShowToast(
                "Pushed files to remote.", "success"
            );
            _fnRefreshNow();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Push failed: " + error.message, "error"
            );
        }
    }

    function _fnHandleTrackChoice(sName) {
        _fnPostTrackOrIgnore(sName, "track");
    }

    function _fnHandleIgnoreChoice(sName) {
        _fnPostTrackOrIgnore(sName, "ignore");
    }

    async function _fnPostTrackOrIgnore(sName, sAction) {
        try {
            await VaibifyApi.fdictPost(
                _fsApiBase() + "/" + encodeURIComponent(sName) +
                "/" + sAction
            );
            _fnRefreshNow();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                sAction + " failed: " + error.message, "error"
            );
        }
    }

    function _flistBuildPromptChoices(sName) {
        return [
            {
                sLabel: "Track",
                sStyleClass: "btn-primary",
                fnCallback: function () {
                    _fnHandleTrackChoice(sName);
                },
            },
            {
                sLabel: "Ignore",
                fnCallback: function () {
                    _fnHandleIgnoreChoice(sName);
                },
            },
            {sLabel: "Later", fnCallback: function () { }},
        ];
    }

    function _fnPromptOne(sName) {
        PipeleyenModals.fnShowChoiceModal(
            "New repository detected",
            "Track or ignore '" + sName + "'?",
            _flistBuildPromptChoices(sName)
        );
    }

    function _fnPromptForUndecided(listUndecided) {
        if (!_setPromptedNames) return;
        (listUndecided || []).forEach(function (sName) {
            if (_setPromptedNames.has(sName)) return;
            _setPromptedNames.add(sName);
            _fnPromptOne(sName);
        });
    }

    function fnHandleStatusUpdate(dictStatus) {
        if (!dictStatus) return;
        _dictCachedStatus = {
            listTracked: dictStatus.listTracked || [],
            listIgnored: dictStatus.listIgnored || [],
            listUndecided: dictStatus.listUndecided || [],
        };
        fnRender();
        _fnPromptForUndecided(_dictCachedStatus.listUndecided);
    }

    async function fnInit(sContainerId) {
        _sContainerId = sContainerId;
        _setPromptedNames = new Set();
        _dictCachedStatus = {
            listTracked: [], listIgnored: [], listUndecided: [],
        };
        _fnShowLoadingIndicator();
        await _fnRefreshNow();
        VaibifyPolling.fnSetReposHandler(fnHandleStatusUpdate);
        VaibifyPolling.fnStartReposPolling(sContainerId);
    }

    function _fnShowLoadingIndicator() {
        var elContainer = _felGetContainer();
        if (elContainer) {
            elContainer.innerHTML =
                '<p class="muted-text" style="text-align:center;' +
                'padding:24px 0;">Loading repositories...</p>';
        }
    }

    function fnTeardown() {
        VaibifyPolling.fnStopReposPolling();
        VaibifyPolling.fnSetReposHandler(null);
        _sContainerId = null;
        _setPromptedNames = null;
        _dictCachedStatus = {
            listTracked: [], listIgnored: [], listUndecided: [],
        };
        var elContainer = _felGetContainer();
        if (elContainer) elContainer.innerHTML = "";
    }

    return {
        fnInit: fnInit,
        fnTeardown: fnTeardown,
        fnHandleStatusUpdate: fnHandleStatusUpdate,
        fnRender: fnRender,
    };
})();
