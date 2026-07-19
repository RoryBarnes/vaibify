/* Vaibify — AI model declaration modal (Replay axis).

   Opened from the Project block's "AI Model / Prompts" row. POSTs
   /api/workflow/{id}/ai-models/declare with:
     {sVendor, sModelId, sUseStartDate, sUseEndDate}          — closed
     {…, bOpenWeights: true, sWeightsSource,
      sWeightsRevisionHash}                                   — open

   Declarations upsert on (vendor, model id); the row's light flips
   on the next status poll, which reads the saved workflow.

   Exposes:
     - VaibifyAiModelConfig.fnOpen()    open the modal (blank form)
     - VaibifyAiModelConfig.fnClose()   hide the modal
     - VaibifyAiModelConfig.fnSave()    submit the declaration
     - VaibifyAiModelConfig.fnToggleWeightsFields()
*/

var VaibifyAiModelConfig = (function () {
    "use strict";

    var _RE_ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function _fnShowError(sMessage) {
        var elError = _felGet("aiModelConfigError");
        elError.textContent = sMessage;
        elError.hidden = false;
    }

    function _fnClearError() {
        var elError = _felGet("aiModelConfigError");
        elError.textContent = "";
        elError.hidden = true;
    }

    function _fnClearForm() {
        _felGet("inputAiModelVendor").value = "";
        _felGet("inputAiModelId").value = "";
        _felGet("inputAiModelStartDate").value = "";
        _felGet("inputAiModelEndDate").value = "";
        _felGet("checkAiModelOpenWeights").checked = false;
        _felGet("inputAiModelWeightsSource").value = "";
        _felGet("inputAiModelWeightsHash").value = "";
        fnToggleWeightsFields();
    }

    function fnToggleWeightsFields() {
        var bOpenWeights = _felGet("checkAiModelOpenWeights").checked;
        _felGet("groupAiModelWeightsSource").hidden = !bOpenWeights;
        _felGet("groupAiModelWeightsHash").hidden = !bOpenWeights;
    }

    var _fnEscapeKeyHandler = null;

    function _fnAttachEscapeHandler() {
        _fnEscapeKeyHandler = function (event) {
            if (event.key === "Escape") {
                event.stopPropagation();
                fnClose();
            }
        };
        document.addEventListener("keydown", _fnEscapeKeyHandler);
    }

    function _fnDetachEscapeHandler() {
        if (!_fnEscapeKeyHandler) return;
        document.removeEventListener("keydown", _fnEscapeKeyHandler);
        _fnEscapeKeyHandler = null;
    }

    function fnOpen() {
        _fnClearForm();
        _fnClearError();
        _felGet("modalAiModelConfig").style.display = "flex";
        _fnAttachEscapeHandler();
        _felGet("inputAiModelVendor").focus();
    }

    function fnClose() {
        _felGet("modalAiModelConfig").style.display = "none";
        _fnDetachEscapeHandler();
    }

    function _fdictReadForm() {
        var dictModel = {
            sVendor: _felGet("inputAiModelVendor").value.trim(),
            sModelId: _felGet("inputAiModelId").value.trim(),
            sUseStartDate: _felGet("inputAiModelStartDate").value.trim(),
            sUseEndDate: _felGet("inputAiModelEndDate").value.trim(),
        };
        if (_felGet("checkAiModelOpenWeights").checked) {
            dictModel.bOpenWeights = true;
            dictModel.sWeightsSource =
                _felGet("inputAiModelWeightsSource").value.trim();
            dictModel.sWeightsRevisionHash =
                _felGet("inputAiModelWeightsHash").value.trim();
        }
        return dictModel;
    }

    function _fbValidateBeforeSubmit(dictModel) {
        if (!dictModel.sVendor || !dictModel.sModelId) {
            _fnShowError("Vendor and model ID are both required.");
            return false;
        }
        if (!_RE_ISO_DATE.test(dictModel.sUseStartDate) ||
                !_RE_ISO_DATE.test(dictModel.sUseEndDate)) {
            _fnShowError("Both use dates are required (YYYY-MM-DD).");
            return false;
        }
        if (dictModel.bOpenWeights === true &&
                (!dictModel.sWeightsSource ||
                 !dictModel.sWeightsRevisionHash)) {
            _fnShowError("Open-weights declarations require the " +
                "weights source and revision hash.");
            return false;
        }
        return true;
    }

    async function fnSave() {
        _fnClearError();
        var dictModel = _fdictReadForm();
        if (!_fbValidateBeforeSubmit(dictModel)) return;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                    "/ai-models/declare",
                dictModel,
            );
            _fnApplyResultToWorkflow(dictResult);
            PipeleyenApp.fnShowToast(
                "Declared " + dictModel.sVendor + " / " +
                    dictModel.sModelId + ".",
                "success",
            );
            fnClose();
        } catch (error) {
            _fnShowError(_fsExtractErrorDetail(error));
        }
    }

    function _fnApplyResultToWorkflow(dictResult) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return;
        var dictProvenance = dictWorkflow.dictAiProvenance || {};
        dictProvenance.listDeclaredModels =
            (dictResult || {}).listDeclaredModels || [];
        dictWorkflow.dictAiProvenance = dictProvenance;
    }

    function _fsExtractErrorDetail(error) {
        if (!error) return "Declaration failed.";
        if (typeof error === "string") return error;
        return error.message || "Declaration failed.";
    }

    return {
        fnOpen: fnOpen,
        fnClose: fnClose,
        fnSave: fnSave,
        fnToggleWeightsFields: fnToggleWeightsFields,
    };
})();
