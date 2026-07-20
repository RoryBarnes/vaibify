/* Vaibify — personal instruction layer declaration (Replay axis).

   Instruction-stack layer 4: the researcher's private host-side agent
   configuration (global instruction file, personal skills, memory,
   hooks). The Project block's AI section renders one requirement row
   for it; answering the question with any of the three statuses is
   what the Level 2 criterion requires — disclosure is never required:

     - none              no personal layer exists
     - declared-private  it exists; content withheld (optional hash
                         commitments prove, on later release, that
                         the released files governed the work)
     - included          the files were added to the project repo

   Two endpoints, both through VaibifyApi:
     POST .../personal-layer/declare  {sStatus, dictHashCommitment?}
     POST .../personal-layer/hash     {sHostPath, sLabel} → commitment
   The hash endpoint is researcher-only (the backend rejects the
   in-container agent lane); the host path is sent once, hashed, and
   never persisted anywhere.

   Exposes:
     - VaibifyPersonalLayer.fsRenderPersonalLayerDetail(dictDetail)
     - VaibifyPersonalLayer.fnDeclareStatus(sStatus)
     - VaibifyPersonalLayer.fnAddCommitment(elButton)
*/

var VaibifyPersonalLayer = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    var _DICT_STATUS_LABELS = {
        "none": "No personal layer exists",
        "declared-private": "Exists — content withheld",
        "included": "Included in the project repository",
    };

    function _fdictLayerOf(dictDetail) {
        return ((dictDetail || {}).dictAiProvenance || {})
            .dictPersonalLayer || {};
    }

    function _fsRenderStatusChoiceButton(sStatus, sCurrent) {
        var bActive = sStatus === sCurrent;
        return '<button type="button" class="btn btn-small ' +
            'wf-personal-layer-set' +
            (bActive ? ' personal-layer-active' : '') + '" ' +
            'data-personal-status="' + fnEscapeHtml(sStatus) + '">' +
            fnEscapeHtml(_DICT_STATUS_LABELS[sStatus]) +
            (bActive ? ' (current)' : '') + '</button>';
    }

    function _fsRenderStatusChoices(sCurrent) {
        return '<div class="requirement-row-actions">' +
            ['none', 'declared-private', 'included']
                .map(function (sStatus) {
                    return _fsRenderStatusChoiceButton(
                        sStatus, sCurrent);
                }).join(' ') + '</div>';
    }

    function _fsRenderCommitmentChip(dictCommitment) {
        var sHashHead = String(dictCommitment.sSha256 || "")
            .slice(0, 12);
        var sDate = String(dictCommitment.sDeclaredIso || "")
            .slice(0, 10);
        return '<span class="personal-layer-commitment-chip" ' +
            'title="SHA-256 ' +
            fnEscapeHtml(dictCommitment.sSha256 || "") + ' · ' +
            fnEscapeHtml(String(
                dictCommitment.iByteCount || 0)) + ' bytes">' +
            fnEscapeHtml(dictCommitment.sLabel || "?") + ' · ' +
            fnEscapeHtml(sHashHead) + '… · ' +
            fnEscapeHtml(sDate) + '</span>';
    }

    function _fsRenderCommitmentBlock(dictLayer) {
        var listCommitments = dictLayer.listHashCommitments || [];
        var sChips = listCommitments.length === 0
            ? '<div class="requirement-row-status">No hash ' +
              'commitments recorded — optional. The answer alone ' +
              'meets the requirement.</div>'
            : '<div class="personal-layer-commitments">' +
              listCommitments.map(_fsRenderCommitmentChip).join(' ') +
              '</div>';
        return sChips +
            '<div class="personal-layer-add-form">' +
            '<label class="determinism-form-row">Label: ' +
            '<input type="text" class="personal-layer-label" ' +
            'placeholder="e.g. personal instruction file">' +
            '</label>' +
            '<label class="determinism-form-row">Path on this ' +
            'computer: <input type="text" ' +
            'class="personal-layer-host-path" ' +
            'placeholder="absolute path, inside your home">' +
            '</label>' +
            '<div class="requirement-row-actions">' +
            '<button type="button" class="btn btn-small ' +
            'wf-personal-layer-add">Add hash commitment</button>' +
            '</div>' +
            '<div class="requirement-row-howto">A commitment stores ' +
            'only a label, the file\'s SHA-256, its byte count, ' +
            'and the date — never the path or any content. It ' +
            'reveals nothing, but proves — if you later choose ' +
            'to release the file — that the released version ' +
            'is the one that governed the work.</div>';
    }

    function _fsRenderIncludedPaths(dictLayer) {
        var listPaths = dictLayer.listIncludedPaths || [];
        if (listPaths.length === 0) return "";
        return '<div class="requirement-row-status">Included ' +
            'files: ' +
            fnEscapeHtml(listPaths.join(", ")) + '</div>';
    }

    function fsRenderPersonalLayerDetail(dictDetail) {
        var dictLayer = _fdictLayerOf(dictDetail);
        var sStatus = dictLayer.sStatus || "";
        var bAnswered = Object.prototype.hasOwnProperty.call(
            _DICT_STATUS_LABELS, sStatus);
        var sStatusLine = bAnswered
            ? '<div class="requirement-row-status">Declared: ' +
              fnEscapeHtml(_DICT_STATUS_LABELS[sStatus]) +
              (dictLayer.sDeclaredIso
                  ? ' (' + fnEscapeHtml(String(
                      dictLayer.sDeclaredIso).slice(0, 10)) + ')'
                  : '') + '</div>'
            : '<div class="requirement-row-status">Not yet ' +
              'answered. Your personal agent configuration on this ' +
              'computer (global instruction file, personal skills, ' +
              'memory, hooks) is part of the instruction stack that ' +
              'governed the AI\'s work. State its status — ' +
              'any answer meets the requirement; sharing the ' +
              'content is never required.</div>';
        return '<div class="requirement-row-detail">' + sStatusLine +
            _fsRenderStatusChoices(sStatus) +
            (sStatus === "declared-private"
                ? _fsRenderCommitmentBlock(dictLayer) : "") +
            (sStatus === "included"
                ? _fsRenderIncludedPaths(dictLayer) : "") +
            '<div class="requirement-row-howto">The declaration is ' +
            'stored in project.json with the other AI provenance ' +
            'declarations; once answered, this requirement will ' +
            'pass on the next status poll.</div></div>';
    }

    async function fnDeclareStatus(sStatus) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var dictResult = await _fdictPostDeclare(
                sContainerId, {sStatus: sStatus});
            _fnApplyResultToWorkflow(dictResult);
            PipeleyenApp.fnShowToast(
                "Personal layer declared: " +
                    (_DICT_STATUS_LABELS[sStatus] || sStatus) + ".",
                "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Declaration failed: " + _fsDetail(error), "error");
        }
    }

    async function fnAddCommitment(elButton) {
        var elForm = elButton.closest(".personal-layer-add-form");
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!elForm || !sContainerId) return;
        var sLabel = (elForm.querySelector(".personal-layer-label")
            .value || "").trim();
        var sHostPath = (elForm
            .querySelector(".personal-layer-host-path")
            .value || "").trim();
        if (!sLabel || !sHostPath) {
            PipeleyenApp.fnShowToast(
                "Both fields are needed: a label and the file's " +
                    "path on this computer.", "warning");
            return;
        }
        try {
            var dictHashResult = await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                    "/personal-layer/hash",
                {sHostPath: sHostPath, sLabel: sLabel});
            var dictResult = await _fdictPostDeclare(sContainerId, {
                sStatus: "declared-private",
                dictHashCommitment:
                    (dictHashResult || {}).dictHashCommitment,
            });
            _fnApplyResultToWorkflow(dictResult);
            PipeleyenApp.fnShowToast(
                "Hash commitment recorded for '" + sLabel +
                    "' — only the digest, byte count, and " +
                    "date were stored.", "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Commitment failed: " + _fsDetail(error), "error");
        }
    }

    async function _fdictPostDeclare(sContainerId, dictBody) {
        return VaibifyApi.fdictPost(
            "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/personal-layer/declare",
            dictBody);
    }

    function _fnApplyResultToWorkflow(dictResult) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow) return;
        var dictProvenance = dictWorkflow.dictAiProvenance || {};
        dictProvenance.dictPersonalLayer =
            (dictResult || {}).dictPersonalLayer || {};
        dictWorkflow.dictAiProvenance = dictProvenance;
    }

    function _fsDetail(error) {
        if (!error) return "unknown error";
        if (typeof error === "string") return error;
        return error.message || "unknown error";
    }

    return {
        fsRenderPersonalLayerDetail: fsRenderPersonalLayerDetail,
        fnDeclareStatus: fnDeclareStatus,
        fnAddCommitment: fnAddCommitment,
    };
})();
