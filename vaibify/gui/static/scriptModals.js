/* Vaibify — Modal/dialog utilities */

var PipeleyenModals = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var fsSanitizeErrorForUser = VaibifyUtilities.fsSanitizeErrorForUser;

    function fnShowConfirmModal(sTitle, sMessage, fnOnConfirm) {
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

    function fnShowErrorModal(sMessage) {
        var elModal = document.getElementById("modalError");
        var elContent = document.getElementById("modalErrorContent");
        elContent.textContent = fsSanitizeErrorForUser(sMessage);
        elModal.style.display = "flex";
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
        fnShowErrorModal: fnShowErrorModal,
        fnShowInlineInput: fnShowInlineInput,
    };
})();
