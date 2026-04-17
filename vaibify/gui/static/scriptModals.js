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

    return {
        fnShowConfirmModal: fnShowConfirmModal,
        fnShowInputModal: fnShowInputModal,
        fnShowChoiceModal: fnShowChoiceModal,
        fnShowErrorModal: fnShowErrorModal,
        fnShowInfoModal: fnShowInfoModal,
        fnShowInlineInput: fnShowInlineInput,
    };
})();
