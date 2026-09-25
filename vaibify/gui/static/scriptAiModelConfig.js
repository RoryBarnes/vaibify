/* Vaibify — AI model declaration (Replay axis), view and edit.

   Opened from the Project block's "AI models" row:

     - View Declaration  a read-only list of every declared model
     - Edit Declaration  one card per model. A card saves itself:
                         POST .../ai-models/update for a model already
                         declared (found by its ORIGINAL vendor and model
                         id, so correcting either edits it in place),
                         POST .../ai-models/declare for a new card.
                         Delete goes through the shared "remove-ai-model"
                         project action, which confirms first.

   One model per card, because the provenance record names models
   individually: "two models in one entry" is a declaration of neither.
   The Project block's light flips on the next status poll, which reads
   the saved workflow; a save asks for that poll at once.

   Exposes:
     - VaibifyAiModelConfig.fnOpenView()
     - VaibifyAiModelConfig.fnOpenEdit()
     - VaibifyAiModelConfig.fnClose()
     - VaibifyAiModelConfig.fnAddCard()
     - VaibifyAiModelConfig.fnApplyDeclaredModels(listModels)
     - VaibifyAiModelConfig.fnBindEditor()   once, at startup
*/

var VaibifyAiModelConfig = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var _RE_ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

    var _LIST_BASE_FIELDS = [
        ["sVendor", "Vendor", "text", "e.g. Anthropic"],
        ["sModelId", "Model ID", "text",
            "one exact model identifier per entry"],
        ["sUseStartDate", "Used from", "date", ""],
        ["sUseEndDate", "Used until", "date", ""],
    ];
    var _LIST_WEIGHTS_FIELDS = [
        ["sWeightsSource", "Weights source", "text",
            "where the weights are published"],
        ["sWeightsRevisionHash", "Weights revision hash", "text",
            "revision / commit hash of the weights"],
    ];

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function _flistDeclaredModels() {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow() || {};
        return ((dictWorkflow.dictAiProvenance || {})
            .listDeclaredModels || []).slice();
    }

    /* --- View --- */

    function fnOpenView() {
        var listModels = _flistDeclaredModels();
        _felGet("aiModelViewBody").innerHTML = listModels.length === 0
            ? '<p class="muted-text">No AI model is declared.</p>'
            : listModels.map(_fsRenderModelSummary).join("");
        _fnShow("modalAiModelView");
    }

    function _fsRenderModelSummary(dictModel) {
        var listRows = [
            ["Vendor", dictModel.sVendor],
            ["Model ID", dictModel.sModelId],
            ["Used", (dictModel.sUseStartDate || "?") + " to " +
                (dictModel.sUseEndDate || "?")],
            ["Weights", dictModel.bOpenWeights === true
                ? "open — " + (dictModel.sWeightsSource || "?") + " @ " +
                  (dictModel.sWeightsRevisionHash || "?")
                : "closed"],
        ];
        return '<dl class="ai-model-summary">' + listRows.map(
            function (tRow) {
                return '<dt>' + fnEscapeHtml(tRow[0]) + '</dt><dd>' +
                    fnEscapeHtml(tRow[1] || "?") + '</dd>';
            }).join("") + '</dl>';
    }

    /* --- Edit --- */

    function fnOpenEdit() {
        fnClose();
        _fnRenderCards(_flistDeclaredModels());
        _fnShow("modalAiModelConfig");
    }

    function _fnRenderCards(listModels) {
        var elCards = _felGet("aiModelEditorCards");
        elCards.innerHTML = listModels.map(function (dictModel) {
            return _fsRenderCard(dictModel, false);
        }).join("");
        if (listModels.length === 0) fnAddCard();
    }

    function fnAddCard() {
        _felGet("aiModelEditorCards").insertAdjacentHTML(
            "beforeend", _fsRenderCard({}, true));
    }

    function _fsRenderCard(dictModel, bNew) {
        var bOpenWeights = dictModel.bOpenWeights === true;
        return '<div class="ai-model-card"' +
            (bNew ? ' data-new="1"' : '') +
            ' data-original-vendor="' +
            fnEscapeHtml(dictModel.sVendor || "") + '"' +
            ' data-original-model="' +
            fnEscapeHtml(dictModel.sModelId || "") + '">' +
            _LIST_BASE_FIELDS.map(function (tField) {
                return _fsRenderField(tField, dictModel, false);
            }).join("") +
            '<div class="form-group"><label><input type="checkbox" ' +
            'data-field="bOpenWeights"' + (bOpenWeights ? ' checked' : '') +
            '> Open weights</label></div>' +
            _LIST_WEIGHTS_FIELDS.map(function (tField) {
                return _fsRenderField(tField, dictModel, !bOpenWeights);
            }).join("") +
            '<p class="form-error" data-card-error hidden></p>' +
            '<div class="modal-inline-actions">' +
            '<button type="button" class="btn btn-primary" ' +
            'data-card-action="save">' + (bNew ? 'Declare' : 'Save') +
            '</button><button type="button" class="btn' +
            (bNew ? '' : ' btn-danger') + '" data-card-action="' +
            (bNew ? 'discard' : 'delete') + '">' +
            (bNew ? 'Discard' : 'Delete') + '</button></div></div>';
    }

    function _fsRenderField(tField, dictModel, bHidden) {
        // Label and input as siblings, the shape the dashboard's form
        // styles expect -- nested, the date inputs lost their theme.
        return '<div class="form-group" data-group="' + tField[0] + '"' +
            (bHidden ? ' hidden' : '') + '><label>' +
            fnEscapeHtml(tField[1]) + '</label><input type="' +
            tField[2] + '" data-field="' + tField[0] + '" value="' +
            fnEscapeHtml(dictModel[tField[0]] || "") + '" placeholder="' +
            fnEscapeHtml(tField[3]) + '"></div>';
    }

    function _fnHandleCardClick(event) {
        var elButton = event.target.closest("[data-card-action]");
        if (!elButton) return;
        var elCard = elButton.closest(".ai-model-card");
        var sAction = elButton.dataset.cardAction;
        if (sAction === "save") _fnSaveCard(elCard);
        else if (sAction === "discard") elCard.remove();
        else if (sAction === "delete") _fnDeleteCard(elCard, elButton);
    }

    function _fnHandleCardChange(event) {
        if (event.target.dataset.field !== "bOpenWeights") return;
        var elCard = event.target.closest(".ai-model-card");
        _LIST_WEIGHTS_FIELDS.forEach(function (tField) {
            elCard.querySelector('[data-group="' + tField[0] + '"]')
                .hidden = !event.target.checked;
        });
    }

    function _fdictReadCard(elCard) {
        var dictModel = {};
        _LIST_BASE_FIELDS.forEach(function (tField) {
            dictModel[tField[0]] = elCard.querySelector(
                '[data-field="' + tField[0] + '"]').value.trim();
        });
        if (elCard.querySelector('[data-field="bOpenWeights"]').checked) {
            dictModel.bOpenWeights = true;
            _LIST_WEIGHTS_FIELDS.forEach(function (tField) {
                dictModel[tField[0]] = elCard.querySelector(
                    '[data-field="' + tField[0] + '"]').value.trim();
            });
        }
        return dictModel;
    }

    function _fsValidationProblem(dictModel) {
        if (!dictModel.sVendor || !dictModel.sModelId) {
            return "Vendor and model ID are both required.";
        }
        if (!_RE_ISO_DATE.test(dictModel.sUseStartDate) ||
                !_RE_ISO_DATE.test(dictModel.sUseEndDate)) {
            return "Both use dates are required (YYYY-MM-DD).";
        }
        if (dictModel.bOpenWeights === true &&
                (!dictModel.sWeightsSource ||
                 !dictModel.sWeightsRevisionHash)) {
            return "Open-weights declarations require the weights " +
                "source and revision hash.";
        }
        return "";
    }

    async function _fnSaveCard(elCard) {
        var elError = elCard.querySelector("[data-card-error]");
        elError.hidden = true;
        var dictModel = _fdictReadCard(elCard);
        var sProblem = _fsValidationProblem(dictModel);
        if (sProblem) {
            elError.textContent = sProblem;
            elError.hidden = false;
            return;
        }
        var bNew = elCard.dataset.new === "1";
        if (!bNew) {
            dictModel.sOriginalVendor = elCard.dataset.originalVendor;
            dictModel.sOriginalModelId = elCard.dataset.originalModel;
        }
        // Every button on the card waits for the save: until the saved
        // list re-renders it, the card still carries the ORIGINAL key,
        // and a Delete clicked in that gap would name a model the save
        // just renamed.
        _fnSetCardButtonsDisabled(elCard, true);
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" +
                    encodeURIComponent(VaibifyApp.fsGetContainerId()) +
                    (bNew ? "/ai-models/declare" : "/ai-models/update"),
                dictModel);
            fnApplyDeclaredModels((dictResult || {}).listDeclaredModels);
            VaibifyApp.fnShowToast((bNew ? "Declared " : "Saved ") +
                dictModel.sVendor + " / " + dictModel.sModelId + ".",
                "success");
        } catch (error) {
            elError.textContent = VaibifyDiagnosis.fsExplainError(error);
            elError.hidden = false;
        } finally {
            _fnSetCardButtonsDisabled(elCard, false);
        }
    }

    function _fnSetCardButtonsDisabled(elCard, bDisabled) {
        elCard.querySelectorAll("[data-card-action]").forEach(
            function (elCardButton) {
                elCardButton.disabled = bDisabled;
            });
    }

    function _fnDeleteCard(elCard, elButton) {
        VaibifyApp.fnRunProjectAction("remove-ai-model", JSON.stringify({
            sVendor: elCard.dataset.originalVendor,
            sModelId: elCard.dataset.originalModel,
        }), elButton);
    }

    function fnApplyDeclaredModels(listModels) {
        /* The one place a saved list reaches the page: the workflow the
           rows and dialogs read, the open editor, and an immediate poll
           so the row's light follows. The remove action calls it too. */
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        if (dictWorkflow) {
            var dictProvenance = dictWorkflow.dictAiProvenance || {};
            dictProvenance.listDeclaredModels = listModels || [];
            dictWorkflow.dictAiProvenance = dictProvenance;
        }
        if (_felGet("modalAiModelConfig").style.display === "flex") {
            _fnRenderCards(listModels || []);
        }
        VaibifyPolling.fnStartFilePolling(VaibifyApp.fsGetContainerId());
    }

    /* --- Shared --- */

    var _fnEscapeKeyHandler = null;

    function _fnShow(sModalId) {
        _felGet(sModalId).style.display = "flex";
        _fnEscapeKeyHandler = function (event) {
            if (event.key === "Escape") {
                event.stopPropagation();
                fnClose();
            }
        };
        document.addEventListener("keydown", _fnEscapeKeyHandler);
    }

    function fnClose() {
        _felGet("modalAiModelView").style.display = "none";
        _felGet("modalAiModelConfig").style.display = "none";
        if (_fnEscapeKeyHandler) {
            document.removeEventListener("keydown", _fnEscapeKeyHandler);
            _fnEscapeKeyHandler = null;
        }
    }

    function fnBindEditor() {
        var elCards = _felGet("aiModelEditorCards");
        if (!elCards) return;
        elCards.addEventListener("click", _fnHandleCardClick);
        elCards.addEventListener("change", _fnHandleCardChange);
    }

    return {
        fnOpenView: fnOpenView,
        fnOpenEdit: fnOpenEdit,
        fnClose: fnClose,
        fnAddCard: fnAddCard,
        fnApplyDeclaredModels: fnApplyDeclaredModels,
        fnBindEditor: fnBindEditor,
    };
})();
