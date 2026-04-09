/* Vaibify — Dependency detection modal (extracted from scriptApplication.js) */

var PipeleyenDependencyScanner = (function () {
    "use strict";

    async function fnScanDependencies(iStep) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictStep = dictWorkflow.listSteps[iStep];
        var saCommands = dictStep.saDataCommands || [];
        if (saCommands.length === 0) {
            return;
        }
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iStep +
                "/scan-dependencies",
                {saDataCommands: saCommands}
            );
            dictStep.dictVerification = dictStep.dictVerification ||
                {sUnitTest: "untested", sUser: "untested"};
            dictStep.dictVerification.sLastDepsCheck =
                VaibifyUtilities.fsFormatUtcTimestamp();
            PipeleyenApp.fnSaveStepUpdate(iStep, {
                dictVerification: dictStep.dictVerification,
            });
            fnShowDependencyModal(iStep, dictResult);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Dependency scan failed", "error");
        }
    }

    /* ── Main modal (page 1) ─────────────────────────────────── */

    function fsRenderMainPage(dictResult, iCurrentStep, listRemovals) {
        var sHtml = '<h2>Dependency Detection</h2>';
        sHtml += fsRenderDetectedSection(dictResult.listSuggestions || []);
        sHtml += fsRenderPossibleSection(dictResult.listUnmatchedFiles || []);
        sHtml += fsRenderManualSection(iCurrentStep, listRemovals);
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnDepSkip">Skip</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnDepNext">Next</button></div>';
        return sHtml;
    }

    function fsRenderDetectedSection(listSuggestions) {
        if (listSuggestions.length === 0) return "";
        var sHtml = '<div class="dependency-section-title">' +
            'Detected Dependencies</div>';
        for (var i = 0; i < listSuggestions.length; i++) {
            var dictSugg = listSuggestions[i];
            sHtml += '<div class="dependency-suggestion">' +
                '<input type="checkbox" checked ' +
                'data-source="detected" data-dep-index="' + i +
                '" class="dependency-checkbox">' +
                '<span class="dependency-step-badge">' +
                VaibifyUtilities.fnEscapeHtml(dictSugg.sSourceStepName) +
                '</span> ' +
                '<span>' + VaibifyUtilities.fnEscapeHtml(dictSugg.sFileName) +
                '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                VaibifyUtilities.fnEscapeHtml(dictSugg.sLoadFunction) +
                ' (line ' + dictSugg.iLineNumber + ')' +
                '</span></div>';
        }
        return sHtml;
    }

    function fsRenderPossibleSection(listUnmatched) {
        if (listUnmatched.length === 0) return "";
        var sHtml = '<div class="dependency-section-title">' +
            'Possible Dependencies</div>';
        for (var j = 0; j < listUnmatched.length; j++) {
            var dictFile = listUnmatched[j];
            sHtml += '<div class="dependency-unmatched">' +
                '<input type="checkbox" data-source="possible" ' +
                'data-unmatched-idx="' + j +
                '" class="dependency-checkbox">' +
                '<span>' + VaibifyUtilities.fnEscapeHtml(dictFile.sFileName) +
                '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                VaibifyUtilities.fnEscapeHtml(dictFile.sLoadFunction) +
                ' (line ' + dictFile.iLineNumber + ')' +
                '</span></div>';
        }
        return sHtml;
    }

    function fsRenderManualSection(iCurrentStep, listRemovals) {
        return '<div class="dependency-section-title">' +
            'Manual Dependencies</div>' +
            '<div id="listManualDeps">' +
            _fsRenderExistingManualDeps(iCurrentStep, listRemovals) +
            '</div>' +
            '<div class="dependency-browser-row">' +
            '<button class="btn btn-small" id="btnSelectFile">' +
            'Select File</button> ' +
            '<button class="btn btn-small" id="btnSelectStep">' +
            'Select Step</button></div>';
    }

    function _fsRenderExistingManualDeps(iCurrentStep, listRemovals) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = (dictWorkflow.listSteps || [])[iCurrentStep];
        if (!dictStep) return "";
        var saDeps = _flistFilterOutRemovals(
            dictStep.saDependencies || [], listRemovals);
        var sHtml = "";
        for (var i = 0; i < saDeps.length; i++) {
            sHtml += _fsRenderOneExistingDep(
                saDeps[i], dictWorkflow);
        }
        return sHtml;
    }

    function _fsRenderOneExistingDep(sToken, dictWorkflow) {
        var match = sToken.match(/^\{Step(\d+)\.manual\}$/);
        if (!match) return "";
        var iDepIndex = parseInt(match[1], 10) - 1;
        var listSteps = dictWorkflow.listSteps || [];
        if (iDepIndex < 0 || iDepIndex >= listSteps.length) return "";
        var sName = listSteps[iDepIndex].sName ||
            "Step " + (iDepIndex + 1);
        return '<div class="dependency-suggestion">' +
            '<input type="checkbox" checked data-source="step" ' +
            'data-dep-step="' + iDepIndex +
            '" class="dependency-checkbox">' +
            '<span class="dependency-step-badge">' +
            VaibifyUtilities.fnEscapeHtml(sName) + '</span>' +
            '<button class="btn btn-small btn-remove-manual" ' +
            'style="margin-left:auto;padding:2px 8px;' +
            'font-size:12px">Remove</button></div>';
    }

    /* ── Select Step sub-page ────────────────────────────────── */

    function fsRenderStepSelectPage(iCurrentStep, listRemovals) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var listSteps = dictWorkflow.listSteps || [];
        var dictStep = listSteps[iCurrentStep];
        var saDeps = _flistFilterOutRemovals(
            dictStep.saDependencies || [], listRemovals);
        var saDepsJoined = saDeps.join(" ");
        var setAutoDeps = _fsetComputeAutoDeps(
            iCurrentStep, listRemovals);
        var sHtml = '<h2>Select Step Dependencies</h2>';
        for (var j = 0; j < iCurrentStep; j++) {
            sHtml += _fsRenderStepCheckbox(
                j, listSteps[j], setAutoDeps, saDepsJoined);
        }
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnStepSelectBack">' +
            'Back</button></div>';
        return sHtml;
    }

    function _flistFilterOutRemovals(saDeps, listRemovals) {
        if (!listRemovals || listRemovals.length === 0) {
            return saDeps;
        }
        var setRemove = {};
        for (var i = 0; i < listRemovals.length; i++) {
            setRemove[listRemovals[i]] = true;
        }
        return saDeps.filter(function (s) {
            return !setRemove[s];
        });
    }

    function _fsetComputeAutoDeps(iCurrentStep, listRemovals) {
        var listDeps = PipeleyenApp.flistGetStepDependencies(
            iCurrentStep);
        var setRemovedIndices = _fsetRemovedStepIndices(
            listRemovals);
        var setAuto = {};
        for (var i = 0; i < listDeps.length; i++) {
            if (!setRemovedIndices[listDeps[i]]) {
                setAuto[listDeps[i]] = true;
            }
        }
        return setAuto;
    }

    function _fsetRemovedStepIndices(listRemovals) {
        var setIndices = {};
        if (!listRemovals) return setIndices;
        for (var i = 0; i < listRemovals.length; i++) {
            var match = listRemovals[i].match(/\{Step(\d+)\./);
            if (match) {
                setIndices[parseInt(match[1], 10) - 1] = true;
            }
        }
        return setIndices;
    }

    function _fsRenderStepCheckbox(
        iDepIndex, dictDepStep, setAutoDeps, saDepsJoined
    ) {
        var sStepLabel = "Step" +
            String(iDepIndex + 1).padStart(2, "0");
        var sName = dictDepStep.sName || sStepLabel;
        var bIsAuto = setAutoDeps[iDepIndex] === true;
        var sManualToken = "{" + sStepLabel + ".manual}";
        var bIsManual = saDepsJoined.indexOf(sManualToken) !== -1;
        var bChecked = bIsAuto || bIsManual;
        var sDisabled = (bIsAuto && !bIsManual) ? " disabled" : "";
        var sLabel = bIsAuto && !bIsManual ?
            ' <span style="color:var(--text-secondary)">(auto)</span>' : "";
        return '<div class="dependency-suggestion">' +
            '<input type="checkbox"' +
            (bChecked ? " checked" : "") + sDisabled +
            ' data-source="step" data-dep-step="' + iDepIndex +
            '" class="dependency-checkbox">' +
            '<span class="dependency-step-badge">' +
            VaibifyUtilities.fnEscapeHtml(sName) +
            '</span>' + sLabel + '</div>';
    }

    function _flistCollectStepSelections(elModal) {
        var listAdded = [];
        var listBoxes = elModal.querySelectorAll(
            '[data-source="step"]:checked:not(:disabled)');
        for (var i = 0; i < listBoxes.length; i++) {
            var iDep = parseInt(
                listBoxes[i].getAttribute("data-dep-step"), 10);
            listAdded.push(iDep);
        }
        return listAdded;
    }

    function _flistCollectStepRemovals(elModal, iCurrentStep) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iCurrentStep];
        var saDepsJoined = (dictStep.saDependencies || []).join(" ");
        var listBoxes = elModal.querySelectorAll(
            '[data-source="step"]:not(:checked):not(:disabled)');
        var listRemoved = [];
        for (var i = 0; i < listBoxes.length; i++) {
            var iDep = parseInt(
                listBoxes[i].getAttribute("data-dep-step"), 10);
            var sToken = "{Step" +
                String(iDep + 1).padStart(2, "0") + ".manual}";
            if (saDepsJoined.indexOf(sToken) !== -1) {
                listRemoved.push(sToken);
            }
        }
        return listRemoved;
    }

    function _fnApplyStepSelectionsToMain(
        elModal, listAdded, listRemoved
    ) {
        var elList = elModal.querySelector("#listManualDeps");
        if (!elList) return;
        _fnRemoveExistingStepRows(elList);
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var listSteps = dictWorkflow.listSteps || [];
        for (var i = 0; i < listAdded.length; i++) {
            var iDep = listAdded[i];
            var sName = (listSteps[iDep] || {}).sName ||
                "Step " + (iDep + 1);
            fnAddManualStepRow(elList, iDep, sName);
        }
        elModal._listStepRemovals = listRemoved;
    }

    function _fnRemoveExistingStepRows(elList) {
        var listExisting = elList.querySelectorAll(
            '[data-source="step"]');
        for (var i = 0; i < listExisting.length; i++) {
            listExisting[i].closest(
                ".dependency-suggestion").remove();
        }
    }

    function fnAddManualStepRow(elList, iDepIndex, sName) {
        var sStepLabel = "Step" +
            String(iDepIndex + 1).padStart(2, "0");
        var elRow = document.createElement("div");
        elRow.className = "dependency-suggestion";
        elRow.innerHTML =
            '<input type="checkbox" checked data-source="step" ' +
            'data-dep-step="' + iDepIndex +
            '" class="dependency-checkbox">' +
            '<span class="dependency-step-badge">' +
            VaibifyUtilities.fnEscapeHtml(sName) + '</span>' +
            '<button class="btn btn-small btn-remove-manual" ' +
            'style="margin-left:auto;padding:2px 8px;' +
            'font-size:12px">Remove</button>';
        elList.appendChild(elRow);
    }

    /* ── Select File sub-page ────────────────────────────────── */

    function fsRenderFileSelectPage() {
        return '<h2>Select File Dependency</h2>' +
            '<div id="depFileBrowser" class="dep-file-browser" ' +
            'style="display:block"></div>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnFileSelectBack">' +
            'Back</button></div>';
    }

    function fsRenderBrowserEntries(listEntries) {
        var sHtml = '';
        for (var i = 0; i < listEntries.length; i++) {
            var dictEntry = listEntries[i];
            var sIcon = dictEntry.bIsDirectory ? "\uD83D\uDCC1" :
                "\uD83D\uDCC4";
            var sClass = dictEntry.bIsDirectory ?
                "dep-browser-dir" : "dep-browser-file";
            sHtml += '<div class="' + sClass +
                '" data-path="' + VaibifyUtilities.fnEscapeHtml(dictEntry.sPath) +
                '" data-is-dir="' + dictEntry.bIsDirectory + '">' +
                sIcon + ' ' +
                VaibifyUtilities.fnEscapeHtml(dictEntry.sName) + '</div>';
        }
        return sHtml;
    }

    function fsRenderBreadcrumb(sCurrentPath) {
        var listParts = sCurrentPath.split("/").filter(
            function (s) { return s; });
        var sBuilt = "";
        var sHtml = "";
        for (var i = 0; i < listParts.length; i++) {
            sBuilt += "/" + listParts[i];
            if (i > 0) {
                sHtml += '<span class="dep-breadcrumb-sep">/</span>';
            }
            sHtml += '<span class="dep-breadcrumb-part" data-path="' +
                VaibifyUtilities.fnEscapeHtml(sBuilt) + '">' +
                VaibifyUtilities.fnEscapeHtml("/" + listParts[i]) + '</span>';
        }
        if (!sHtml) {
            sHtml = '<span class="dep-breadcrumb-part" data-path="/">/</span>';
        }
        return '<div class="dep-breadcrumb">' + sHtml + '</div>';
    }

    async function fnLoadBrowserDirectory(elBrowser, sPath) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        elBrowser.innerHTML = '<div class="dep-browser-loading">' +
            'Loading...</div>';
        try {
            var sUrl = "/api/files/" + sContainerId + sPath;
            var listEntries = await VaibifyApi.fdictGet(sUrl);
            elBrowser.innerHTML = fsRenderBreadcrumb(sPath) +
                '<div class="dep-browser-list">' +
                fsRenderBrowserEntries(listEntries) + '</div>';
            elBrowser.setAttribute("data-current-path", sPath);
        } catch (error) {
            elBrowser.innerHTML = '<div class="dep-browser-loading">' +
                'Failed to load directory</div>';
        }
    }

    /* ── Manual file dep helpers ──────────────────────────────── */

    function fbDepAlreadyListed(sFilePath, dictResult, elModal) {
        var sBasename = sFilePath.split("/").pop();
        var listSugg = dictResult.listSuggestions || [];
        for (var i = 0; i < listSugg.length; i++) {
            if (listSugg[i].sFileName === sBasename) return true;
        }
        var listUnm = dictResult.listUnmatchedFiles || [];
        for (var j = 0; j < listUnm.length; j++) {
            if (listUnm[j].sFileName === sBasename) return true;
        }
        var listManual = elModal.querySelectorAll(
            '[data-source="manual"]');
        for (var m = 0; m < listManual.length; m++) {
            if (listManual[m].getAttribute("data-file-path") ===
                sFilePath) return true;
        }
        return false;
    }

    function fnAddManualDepRow(elList, sFilePath) {
        var sBasename = sFilePath.split("/").pop();
        var elRow = document.createElement("div");
        elRow.className = "dependency-suggestion";
        elRow.innerHTML =
            '<input type="checkbox" checked data-source="manual" ' +
            'data-file-path="' + VaibifyUtilities.fnEscapeHtml(sFilePath) +
            '" class="dependency-checkbox">' +
            '<span>' + VaibifyUtilities.fnEscapeHtml(sBasename) + '</span> ' +
            '<span style="color:var(--text-secondary)">' +
            VaibifyUtilities.fnEscapeHtml(sFilePath) + '</span>' +
            '<button class="btn btn-small btn-remove-manual" ' +
            'style="margin-left:auto;padding:2px 8px;' +
            'font-size:12px">Remove</button>';
        elList.appendChild(elRow);
    }

    /* ── Confirmation page ────────────────────────────────────── */

    function fsRenderConfirmPage(listChecked, listRemoved) {
        var sHtml = '<h2>Confirm Dependencies</h2>';
        if (listChecked.length === 0 &&
            (!listRemoved || listRemoved.length === 0)) {
            sHtml += '<p style="color:var(--text-secondary)">' +
                'No changes to apply.</p>';
        }
        sHtml += _fsRenderConfirmAdded(listChecked);
        sHtml += _fsRenderConfirmRemoved(listRemoved);
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnDepBack">Go Back</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnDepConfirm">Confirm</button></div>';
        return sHtml;
    }

    function _fsRenderConfirmAdded(listChecked) {
        var sHtml = "";
        for (var i = 0; i < listChecked.length; i++) {
            var dictDep = listChecked[i];
            sHtml += '<div class="dependency-suggestion">' +
                '<span style="color:var(--color-pale-blue);">' +
                '&#10003;</span> ' +
                '<span class="dependency-step-badge">' +
                VaibifyUtilities.fnEscapeHtml(dictDep.sSourceStepName) +
                '</span> ' +
                VaibifyUtilities.fnEscapeHtml(dictDep.sFileName) +
                '</div>';
        }
        return sHtml;
    }

    function _fsRenderConfirmRemoved(listRemoved) {
        if (!listRemoved || listRemoved.length === 0) return "";
        var sHtml = '<div class="dependency-section-title">' +
            'Removing</div>';
        for (var i = 0; i < listRemoved.length; i++) {
            sHtml += '<div class="dependency-suggestion">' +
                '<span style="color:var(--color-error);">' +
                '&#10007;</span> ' +
                VaibifyUtilities.fnEscapeHtml(
                    _fsTokenToStepName(listRemoved[i])) +
                '</div>';
        }
        return sHtml;
    }

    function _fsTokenToStepName(sToken) {
        var match = sToken.match(/\{Step(\d+)\./);
        if (!match) return sToken;
        var iIndex = parseInt(match[1], 10) - 1;
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var listSteps = (dictWorkflow || {}).listSteps || [];
        if (iIndex >= 0 && iIndex < listSteps.length) {
            return listSteps[iIndex].sName || sToken;
        }
        return sToken;
    }

    /* ── Collection helpers ───────────────────────────────────── */

    function flistCollectCheckedDeps(elModal, dictResult) {
        var listChecked = [];
        var listBoxes = elModal.querySelectorAll(
            ".dependency-checkbox:checked");
        for (var k = 0; k < listBoxes.length; k++) {
            var elBox = listBoxes[k];
            var sSource = elBox.getAttribute("data-source");
            if (sSource === "detected") {
                _fnCollectDetected(elBox, dictResult, listChecked);
            } else if (sSource === "possible") {
                _fnCollectPossible(elBox, dictResult, listChecked);
            } else if (sSource === "manual") {
                _fnCollectManualFile(elBox, listChecked);
            } else if (sSource === "step") {
                _fnCollectManualStep(elBox, listChecked);
            }
        }
        return listChecked;
    }

    function _fnCollectDetected(elBox, dictResult, listChecked) {
        var iIdx = parseInt(
            elBox.getAttribute("data-dep-index"), 10);
        listChecked.push(dictResult.listSuggestions[iIdx]);
    }

    function _fnCollectPossible(elBox, dictResult, listChecked) {
        var iUIdx = parseInt(
            elBox.getAttribute("data-unmatched-idx"), 10);
        var dictFile = dictResult.listUnmatchedFiles[iUIdx];
        listChecked.push({
            sFileName: dictFile.sFileName,
            sSourceStepName: "Manual",
            sTemplateVariable: fsResolvePathToTemplate(
                dictFile.sFileName),
        });
    }

    function _fnCollectManualFile(elBox, listChecked) {
        var sPath = elBox.getAttribute("data-file-path");
        listChecked.push({
            sFileName: sPath.split("/").pop(),
            sSourceStepName: "Manual",
            sTemplateVariable: fsResolvePathToTemplate(sPath),
        });
    }

    function _fnCollectManualStep(elBox, listChecked) {
        var iDepStep = parseInt(
            elBox.getAttribute("data-dep-step"), 10);
        var sStepId = "Step" +
            String(iDepStep + 1).padStart(2, "0");
        var dictWf = PipeleyenApp.fdictGetWorkflow();
        var sDepName = (dictWf.listSteps[iDepStep] || {})
            .sName || sStepId;
        listChecked.push({
            sFileName: "",
            sSourceStepName: sDepName,
            sTemplateVariable: "{" + sStepId + ".manual}",
            _sSource: "step",
        });
    }

    /* ── Modal lifecycle ──────────────────────────────────────── */

    function fnShowDependencyModal(iStep, dictResult) {
        var elExisting = document.getElementById("modalDependency");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalDependency";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal._listStepRemovals = [];
        _fnRenderMainPage(elModal, dictResult, iStep);
        document.body.appendChild(elModal);
        fnAttachDepModalEvents(elModal, iStep, dictResult);
    }

    function _fnRenderMainPage(elModal, dictResult, iStep) {
        var listRemovals = elModal._listStepRemovals || [];
        elModal.innerHTML = '<div class="modal">' +
            fsRenderMainPage(dictResult, iStep, listRemovals) +
            '</div>';
    }

    function fnAttachDepModalEvents(elModal, iStep, dictResult) {
        elModal.addEventListener("click", function (event) {
            var elTarget = event.target;
            _fnRouteModalClick(
                elTarget, elModal, iStep, dictResult);
        });
    }

    function _fnRouteModalClick(
        elTarget, elModal, iStep, dictResult
    ) {
        var sId = elTarget.id;
        if (sId === "btnDepSkip") {
            elModal.remove();
            return;
        }
        if (sId === "btnSelectStep") {
            _fnShowStepSelectPage(elModal, iStep);
            return;
        }
        if (sId === "btnSelectFile") {
            _fnShowFileSelectPage(elModal);
            return;
        }
        if (sId === "btnStepSelectBack") {
            _fnReturnFromStepSelect(elModal, iStep, dictResult);
            return;
        }
        if (sId === "btnFileSelectBack") {
            _fnRenderMainPage(elModal, dictResult, iStep);
            return;
        }
        if (sId === "btnDepNext") {
            _fnShowConfirmPage(elModal, dictResult);
            return;
        }
        if (sId === "btnDepBack") {
            _fnRenderMainPage(elModal, dictResult, iStep);
            return;
        }
        if (sId === "btnDepConfirm") {
            _fnHandleConfirm(elModal, iStep);
            return;
        }
        if (elTarget.classList.contains("btn-remove-manual")) {
            _fnConfirmRemoveManual(elTarget, elModal);
            return;
        }
        _fnHandleBrowserClick(elTarget, elModal, dictResult);
    }

    function _fnConfirmRemoveManual(elTarget, elModal) {
        var elRow = elTarget.closest(".dependency-suggestion");
        var elCheckbox = elRow.querySelector(".dependency-checkbox");
        var sSource = elCheckbox.getAttribute("data-source");
        var sLabel = elRow.querySelector(
            ".dependency-step-badge");
        var sName = sLabel ? sLabel.textContent : "this dependency";
        PipeleyenApp.fnShowConfirmModal(
            "Remove Dependency",
            "Remove dependency on " + sName + "?",
            function () {
                _fnExecuteRemoveManual(
                    elRow, elCheckbox, sSource, elModal);
            }
        );
    }

    function _fnExecuteRemoveManual(
        elRow, elCheckbox, sSource, elModal
    ) {
        if (sSource === "step") {
            var iDep = parseInt(
                elCheckbox.getAttribute("data-dep-step"), 10);
            var sToken = "{Step" +
                String(iDep + 1).padStart(2, "0") + ".manual}";
            elModal._listStepRemovals =
                elModal._listStepRemovals || [];
            if (elModal._listStepRemovals.indexOf(sToken) === -1) {
                elModal._listStepRemovals.push(sToken);
            }
        }
        elRow.remove();
    }

    function _fnShowStepSelectPage(elModal, iStep) {
        var listRemovals = elModal._listStepRemovals || [];
        var elInner = elModal.querySelector(".modal");
        elInner.innerHTML = fsRenderStepSelectPage(
            iStep, listRemovals);
    }

    function _fnReturnFromStepSelect(elModal, iStep, dictResult) {
        var listAdded = _flistCollectStepSelections(elModal);
        var listRemoved = _flistCollectStepRemovals(elModal, iStep);
        _fnRenderMainPage(elModal, dictResult, iStep);
        _fnApplyStepSelectionsToMain(
            elModal, listAdded, listRemoved);
    }

    function _fnShowFileSelectPage(elModal) {
        var elInner = elModal.querySelector(".modal");
        elInner.innerHTML = fsRenderFileSelectPage();
        var elBrowser = elModal.querySelector("#depFileBrowser");
        fnLoadBrowserDirectory(elBrowser, "/workspace");
    }

    function _fnHandleBrowserClick(
        elTarget, elModal, dictResult
    ) {
        var elEntry = elTarget.closest(
            ".dep-browser-dir, .dep-browser-file");
        if (elEntry) {
            var sPath = elEntry.getAttribute("data-path");
            var bIsDir = elEntry.getAttribute("data-is-dir") ===
                "true";
            if (bIsDir) {
                var elBrowser = elModal.querySelector(
                    "#depFileBrowser");
                fnLoadBrowserDirectory(elBrowser, sPath);
            } else {
                _fnHandleFileSelection(
                    elModal, sPath, dictResult);
            }
            return;
        }
        var elCrumb = elTarget.closest(".dep-breadcrumb-part");
        if (elCrumb) {
            var elBr = elModal.querySelector("#depFileBrowser");
            fnLoadBrowserDirectory(
                elBr, elCrumb.getAttribute("data-path"));
        }
    }

    function _fnHandleFileSelection(elModal, sPath, dictResult) {
        if (fbDepAlreadyListed(sPath, dictResult, elModal)) {
            PipeleyenApp.fnShowToast(
                "Already listed as a dependency", "info");
            return;
        }
        var elList = elModal.querySelector("#listManualDeps");
        if (elList) fnAddManualDepRow(elList, sPath);
    }

    function _fnShowConfirmPage(elModal, dictResult) {
        var listChecked = flistCollectCheckedDeps(
            elModal, dictResult);
        var listRemoved = elModal._listStepRemovals || [];
        var elInner = elModal.querySelector(".modal");
        elInner.innerHTML = fsRenderConfirmPage(
            listChecked, listRemoved);
        elModal._listPendingDeps = listChecked;
    }

    function _fnHandleConfirm(elModal, iStep) {
        var listPending = elModal._listPendingDeps || [];
        var listRemoved = elModal._listStepRemovals || [];
        elModal.remove();
        fnApplyDependencies(iStep, listPending, listRemoved);
    }

    /* ── Template resolution ──────────────────────────────────── */

    function fsResolvePathToTemplate(sPath) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var sBasename = sPath.split("/").pop();
        var sStem = sBasename.replace(/\.[^.]+$/, "");
        if (!sStem) return sPath;
        var listSteps = dictWorkflow.listSteps || [];
        for (var i = 0; i < listSteps.length; i++) {
            var saFiles = (listSteps[i].saDataFiles || []).concat(
                listSteps[i].saPlotFiles || []
            );
            for (var j = 0; j < saFiles.length; j++) {
                var sUpBase = saFiles[j].split("/").pop();
                var sUpStem = sUpBase.replace(/\.[^.]+$/, "");
                if (sUpStem === sStem) {
                    var sLabel = "Step" +
                        String(i + 1).padStart(2, "0");
                    return "{" + sLabel + "." + sStem + "}";
                }
            }
        }
        return sPath;
    }

    /* ── Apply and save ───────────────────────────────────────── */

    function flistFilterNewTokens(
        listTokens, saCommands, saDependencies
    ) {
        var sJoinedCommands = saCommands.join(" ");
        var sJoinedDeps = (saDependencies || []).join(" ");
        var listNew = [];
        for (var i = 0; i < listTokens.length; i++) {
            var sToken = listTokens[i];
            if (!/^\{Step\d+\./.test(sToken)) continue;
            if (sJoinedCommands.indexOf(sToken) === -1 &&
                sJoinedDeps.indexOf(sToken) === -1) {
                listNew.push(sToken);
            }
        }
        return listNew;
    }

    async function fnApplyDependencies(
        iStep, listChecked, listRemoved
    ) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var dictStep = dictWorkflow.listSteps[iStep];
        var saCommands = dictStep.saDataCommands || [];
        var saDependencies = dictStep.saDependencies || [];
        var listDepTokens = [];
        for (var i = 0; i < listChecked.length; i++) {
            listDepTokens.push(listChecked[i].sTemplateVariable);
        }
        var listNew = flistFilterNewTokens(
            listDepTokens, saCommands, saDependencies
        );
        var saUpdated = _flistApplyRemovals(
            saDependencies.concat(listNew), listRemoved
        );
        if (listNew.length === 0 &&
            saUpdated.length === saDependencies.length) {
            PipeleyenApp.fnShowToast(
                "No dependency changes", "info");
            return;
        }
        dictStep.saDependencies = saUpdated;
        await fnSaveDependencies(iStep, saUpdated);
        var iAdded = listNew.length;
        var iRemoved = saDependencies.length +
            listNew.length - saUpdated.length;
        PipeleyenApp.fnShowToast(
            _fsFormatChangeMessage(iAdded, iRemoved), "success");
    }

    function _flistApplyRemovals(saDependencies, listRemoved) {
        if (!listRemoved || listRemoved.length === 0) {
            return saDependencies;
        }
        var setRemove = {};
        for (var i = 0; i < listRemoved.length; i++) {
            setRemove[listRemoved[i]] = true;
        }
        return saDependencies.filter(function (sToken) {
            return !setRemove[sToken];
        });
    }

    function _fsFormatChangeMessage(iAdded, iRemoved) {
        var listParts = [];
        if (iAdded > 0) listParts.push(iAdded + " added");
        if (iRemoved > 0) listParts.push(iRemoved + " removed");
        return listParts.join(", ");
    }

    async function fnSaveDependencies(iStep, saDependencies) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        try {
            await VaibifyApi.fdictPut(
                "/api/steps/" + sContainerId + "/" + iStep,
                {saDependencies: saDependencies});
            PipeleyenApp.fnRenderStepList();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Failed to save dependencies", "error");
        }
    }

    return {
        fnScanDependencies: fnScanDependencies,
        fnShowDependencyModal: fnShowDependencyModal,
    };
})();
