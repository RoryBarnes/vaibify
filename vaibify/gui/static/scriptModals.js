/* Vaibify — Modal/dialog utilities */

var PipeleyenModals = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var fsSanitizeErrorForUser = VaibifyUtilities.fsSanitizeErrorForUser;

    function fnShowConfirmModal(sTitle, sMessage, fnOnConfirm, dictDetails) {
        var elExisting = document.getElementById("modalConfirm");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalConfirm";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sTitle) + '</h2>' +
            '<p style="white-space:pre-wrap;margin-bottom:16px">' +
            fnEscapeHtml(sMessage) + '</p>' +
            _fsBuildConfirmDetails(dictDetails) +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnConfirmCancel">Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnConfirmOk">Confirm</button>' +
            '</div></div>';
        document.body.appendChild(elModal);
        document.getElementById("btnConfirmCancel").addEventListener(
            "click", function () { elModal.remove(); }
        );
        document.getElementById("btnConfirmOk").addEventListener(
            "click", function () {
                elModal.remove();
                fnOnConfirm();
            }
        );
    }

    function _fsBuildConfirmDetails(dictDetails) {
        if (!dictDetails) return "";
        var sBody = "";
        if (dictDetails.sDetails) {
            sBody += '<p class="confirm-details-text">' +
                fnEscapeHtml(dictDetails.sDetails) + '</p>';
        }
        if (dictDetails.sCommand) {
            sBody += '<p class="confirm-details-label">' +
                'Equivalent command:</p>' +
                '<pre class="confirm-details-command">' +
                fnEscapeHtml(dictDetails.sCommand) + '</pre>';
        } else if (dictDetails.bNoCommand) {
            sBody += '<p class="confirm-details-label">' +
                'Equivalent command:</p>' +
                '<p class="confirm-details-text">' +
                'No direct command &mdash; this action only ' +
                'affects the dashboard.</p>';
        }
        if (!sBody) return "";
        return (
            '<details class="confirm-details">' +
            '<summary>Learn more</summary>' +
            sBody +
            '</details>'
        );
    }

    function fnShowInputModal(sLabel, sPlaceholder, fnCallback) {
        var elExisting = document.getElementById("modalInput");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalInput";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sLabel) + '</h2>' +
            '<input type="text" class="input-modal-field" ' +
            'placeholder="' + fnEscapeHtml(sPlaceholder) + '">' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnInputCancel">Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnInputConfirm">Add</button>' +
            '</div></div>';
        document.body.appendChild(elModal);
        var elInput = elModal.querySelector(".input-modal-field");
        elInput.focus();
        elInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") fnConfirmInput();
            if (e.key === "Escape") elModal.remove();
        });
        document.getElementById("btnInputCancel").addEventListener(
            "click", function () { elModal.remove(); }
        );
        document.getElementById("btnInputConfirm").addEventListener(
            "click", fnConfirmInput
        );
        function fnConfirmInput() {
            var sValue = elInput.value.trim();
            elModal.remove();
            if (sValue) fnCallback(sValue);
        }
    }

    function _felBuildChoiceButton(dictChoice, elModal) {
        var elButton = document.createElement("button");
        elButton.className = "btn " + (dictChoice.sStyleClass || "");
        elButton.textContent = dictChoice.sLabel;
        elButton.addEventListener("click", function () {
            elModal.remove();
            if (dictChoice.fnCallback) dictChoice.fnCallback();
        });
        return elButton;
    }

    function fnShowChoiceModal(sTitle, sMessage, listChoices) {
        var elExisting = document.getElementById("modalChoice");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalChoice";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sTitle) + '</h2>' +
            '<p style="white-space:pre-wrap;margin-bottom:16px">' +
            fnEscapeHtml(sMessage) + '</p>' +
            '<div class="modal-actions" id="choiceModalActions"></div>' +
            '</div>';
        document.body.appendChild(elModal);
        var elActions = document.getElementById("choiceModalActions");
        listChoices.forEach(function (dictChoice) {
            elActions.appendChild(_felBuildChoiceButton(dictChoice, elModal));
        });
    }

    function fnShowErrorModal(sMessage) {
        var elModal = document.getElementById("modalError");
        var elContent = document.getElementById("modalErrorContent");
        elContent.textContent = fsSanitizeErrorForUser(sMessage);
        elModal.style.display = "flex";
    }

    function fnShowInfoModal(sTitle, sMessageHtml) {
        var elExisting = document.getElementById("modalInfo");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalInfo";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML =
            '<div class="modal modal-info">' +
            '<h2>' + fnEscapeHtml(sTitle) + '</h2>' +
            '<div class="modal-info-body">' + sMessageHtml + '</div>' +
            '<div class="modal-actions">' +
            '<button class="btn btn-primary" id="btnInfoClose">' +
            'Got it</button></div></div>';
        document.body.appendChild(elModal);
        document.getElementById("btnInfoClose").addEventListener(
            "click", function () { elModal.remove(); });
    }

    function fnShowInlineInput(iStep, sArrayKey, sPlaceholder) {
        var elSection = document.querySelector(
            '.section-add[data-step="' + iStep +
            '"][data-array="' + sArrayKey + '"]'
        );
        if (!elSection) return;
        var elLabel = elSection.parentElement;
        var elExisting = elLabel.nextElementSibling;
        if (elExisting &&
            elExisting.classList.contains("inline-add-row")) {
            return;
        }

        var elRow = document.createElement("div");
        elRow.className = "inline-add-row";
        elRow.innerHTML =
            '<input class="detail-edit-input" type="text" ' +
            'placeholder="' + sPlaceholder + '">' +
            '<button class="inline-add-confirm" title="Add">' +
            '&#10003;</button>' +
            '<button class="inline-add-cancel" title="Cancel">' +
            '&#10005;</button>';
        elLabel.parentElement.insertBefore(
            elRow, elLabel.nextSibling);

        var elInput = elRow.querySelector("input");
        elInput.focus();

        function fnConfirm() {
            var sValue = elInput.value.trim();
            if (sValue) {
                PipeleyenApp.fnCommitNewItem(iStep, sArrayKey, sValue);
            }
            elRow.remove();
        }
        function fnCancel() {
            elRow.remove();
        }

        elRow.querySelector(".inline-add-confirm").addEventListener(
            "click", fnConfirm
        );
        elRow.querySelector(".inline-add-cancel").addEventListener(
            "click", fnCancel
        );
        elInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") fnConfirm();
            if (event.key === "Escape") fnCancel();
        });
    }

    /* --- Tree picker (Overleaf mirror target-directory) --- */

    function _fbIsValidOverleafPath(sPath) {
        if (!sPath) return false;
        var sFirst = sPath.charAt(0);
        if (sFirst === "/" || sFirst === "\\") return false;
        for (var iChar = 0; iChar < sPath.length; iChar += 1) {
            var iCode = sPath.charCodeAt(iChar);
            if (iCode < 32 || iCode === 127) return false;
        }
        var sTrimmed = sPath;
        while (
            sTrimmed.length > 0 &&
            sTrimmed.charAt(sTrimmed.length - 1) === "/"
        ) {
            sTrimmed = sTrimmed.substring(0, sTrimmed.length - 1);
        }
        if (sTrimmed.length === 0) return false;
        var listSegments = sTrimmed.split("/");
        for (var iIndex = 0; iIndex < listSegments.length; iIndex += 1) {
            if (listSegments[iIndex] === "..") return false;
            if (listSegments[iIndex] === "") return false;
        }
        return true;
    }

    function _fsBuildTreePickerShell(sTitle, sCurrentPath) {
        return (
            '<div class="modal">' +
            '<h2>' + fnEscapeHtml(sTitle) + '</h2>' +
            '<label class="tree-picker-label" ' +
            'for="inputTreePickerPath">Target directory</label>' +
            '<input type="text" id="inputTreePickerPath" ' +
            'class="tree-picker-input" ' +
            'value="' + fnEscapeHtml(sCurrentPath) + '" ' +
            'placeholder="figures">' +
            '<p class="tree-picker-error" ' +
            'id="treePickerError" aria-live="polite"></p>' +
            '<div class="tree-picker-body" ' +
            'id="treePickerBody" tabindex="0"></div>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnTreePickerCancel">' +
            'Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnTreePickerSelect">Select</button>' +
            '</div></div>'
        );
    }

    function _felBuildTreeRow(sDirPath, setExpandedDirs, dictTreeIndex) {
        var sLabel = sDirPath.split("/").pop() || sDirPath;
        var bExpanded = setExpandedDirs.has(sDirPath);
        var listChildren = dictTreeIndex[sDirPath] || [];
        var bHasChildren = listChildren.length > 0;
        var sTriangle = bHasChildren
            ? (bExpanded ? "&#9660;" : "&#9654;") : "&nbsp;";
        var elRow = document.createElement("button");
        elRow.type = "button";
        elRow.className = "tree-picker-row";
        if (bExpanded) elRow.classList.add("expanded");
        elRow.dataset.path = sDirPath;
        elRow.dataset.hasChildren = bHasChildren ? "1" : "0";
        var iDepth = sDirPath.split("/").length - 1;
        elRow.style.paddingLeft = (8 + iDepth * 16) + "px";
        elRow.innerHTML =
            '<span class="tree-picker-toggle" data-toggle="' +
            (bHasChildren ? "1" : "0") + '">' + sTriangle + '</span>' +
            '<span class="tree-picker-name">' +
            fnEscapeHtml(sLabel) + '</span>';
        return elRow;
    }

    function _fnAppendChildren(
        elContainer, sDirPath, setExpandedDirs,
        dictTreeIndex, fnOnRowClick
    ) {
        if (!setExpandedDirs.has(sDirPath)) return;
        var listChildren = dictTreeIndex[sDirPath] || [];
        listChildren.forEach(function (sChild) {
            var elChildRow = _felBuildTreeRow(
                sChild, setExpandedDirs, dictTreeIndex);
            elChildRow.addEventListener("click", fnOnRowClick);
            elContainer.appendChild(elChildRow);
            _fnAppendChildren(
                elContainer, sChild, setExpandedDirs,
                dictTreeIndex, fnOnRowClick);
        });
    }

    function _fnRenderTreeBody(
        elBody, dictTreeIndex, setExpandedDirs, fnOnRowClick
    ) {
        elBody.innerHTML = "";
        var listRoots = dictTreeIndex[""] || [];
        if (listRoots.length === 0) {
            elBody.innerHTML =
                '<p class="tree-picker-empty">' +
                'No directories in the mirror yet. ' +
                'Type a path above to create one on push.</p>';
            return;
        }
        listRoots.forEach(function (sRoot) {
            var elRow = _felBuildTreeRow(
                sRoot, setExpandedDirs, dictTreeIndex);
            elRow.addEventListener("click", fnOnRowClick);
            elBody.appendChild(elRow);
            _fnAppendChildren(
                elBody, sRoot, setExpandedDirs,
                dictTreeIndex, fnOnRowClick);
        });
    }

    function _fnBindTreePickerToggle(event, dictPicker) {
        event.stopPropagation();
        var elRow = event.currentTarget;
        var sPath = elRow.dataset.path;
        if (elRow.dataset.hasChildren !== "1") {
            dictPicker.elInput.value = sPath;
            return;
        }
        if (dictPicker.setExpandedDirs.has(sPath)) {
            dictPicker.setExpandedDirs.delete(sPath);
        } else {
            dictPicker.setExpandedDirs.add(sPath);
        }
        dictPicker.elInput.value = sPath;
        _fnRenderTreeBody(
            dictPicker.elBody, dictPicker.dictTreeIndex,
            dictPicker.setExpandedDirs, dictPicker.fnRowClick);
    }

    function _fnHandleTreeRowClick(event, dictPicker) {
        var elToggle = event.target.closest(".tree-picker-toggle");
        if (elToggle) {
            _fnBindTreePickerToggle(event, dictPicker);
            return;
        }
        var elRow = event.currentTarget;
        dictPicker.elInput.value = elRow.dataset.path;
        if (elRow.dataset.hasChildren === "1") {
            _fnBindTreePickerToggle(event, dictPicker);
        }
    }

    function _fnCommitTreePickerSelection(dictPicker, dictOptions) {
        var sValue = dictPicker.elInput.value.trim();
        if (sValue.length === 0) {
            dictPicker.elError.textContent =
                "Target directory cannot be empty.";
            return;
        }
        if (!_fbIsValidOverleafPath(sValue)) {
            dictPicker.elError.textContent =
                "Path must not start with '/' or contain '..' " +
                "or empty segments.";
            return;
        }
        dictPicker.elError.textContent = "";
        dictPicker.elModal.remove();
        if (dictOptions.fnOnSelect) dictOptions.fnOnSelect(sValue);
    }

    function fnShowTreePicker(dictOptions) {
        var elExisting = document.getElementById("modalTreePicker");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalTreePicker";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML = _fsBuildTreePickerShell(
            dictOptions.sTitle || "Select directory",
            dictOptions.sCurrentPath || "");
        document.body.appendChild(elModal);
        var dictPicker = {
            elModal: elModal,
            elInput: document.getElementById("inputTreePickerPath"),
            elError: document.getElementById("treePickerError"),
            elBody: document.getElementById("treePickerBody"),
            dictTreeIndex: dictOptions.dictTreeIndex || {},
            setExpandedDirs: dictOptions.setExpandedDirs || new Set(),
            fnRowClick: null,
        };
        dictPicker.fnRowClick = function (event) {
            _fnHandleTreeRowClick(event, dictPicker);
        };
        _fnRenderTreeBody(
            dictPicker.elBody, dictPicker.dictTreeIndex,
            dictPicker.setExpandedDirs, dictPicker.fnRowClick);
        _fnWireTreePickerButtons(elModal, dictPicker, dictOptions);
        dictPicker.elInput.focus();
    }

    function _fnWireTreePickerButtons(elModal, dictPicker, dictOptions) {
        document.getElementById("btnTreePickerCancel")
            .addEventListener("click", function () {
                elModal.remove();
                if (dictOptions.fnOnCancel) dictOptions.fnOnCancel();
            });
        document.getElementById("btnTreePickerSelect")
            .addEventListener("click", function () {
                _fnCommitTreePickerSelection(dictPicker, dictOptions);
            });
        dictPicker.elInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                _fnCommitTreePickerSelection(dictPicker, dictOptions);
            } else if (e.key === "Escape") {
                elModal.remove();
                if (dictOptions.fnOnCancel) dictOptions.fnOnCancel();
            }
        });
    }

    return {
        fnShowConfirmModal: fnShowConfirmModal,
        fnShowInputModal: fnShowInputModal,
        fnShowChoiceModal: fnShowChoiceModal,
        fnShowErrorModal: fnShowErrorModal,
        fnShowInfoModal: fnShowInfoModal,
        fnShowInlineInput: fnShowInlineInput,
        fnShowTreePicker: fnShowTreePicker,
    };
})();
