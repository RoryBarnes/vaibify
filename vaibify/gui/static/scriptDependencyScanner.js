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
                PipeleyenApp.fsFormatUtcTimestamp();
            PipeleyenApp.fnSaveStepUpdate(iStep, {
                dictVerification: dictStep.dictVerification,
            });
            fnShowDependencyModal(iStep, dictResult);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Dependency scan failed", "error");
        }
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
                PipeleyenApp.fnEscapeHtml(dictSugg.sSourceStepName) +
                '</span> ' +
                '<span>' + PipeleyenApp.fnEscapeHtml(dictSugg.sFileName) +
                '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                PipeleyenApp.fnEscapeHtml(dictSugg.sLoadFunction) +
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
                '<span>' + PipeleyenApp.fnEscapeHtml(dictFile.sFileName) +
                '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                PipeleyenApp.fnEscapeHtml(dictFile.sLoadFunction) +
                ' (line ' + dictFile.iLineNumber + ')' +
                '</span></div>';
        }
        return sHtml;
    }

    function fsRenderManualSection() {
        return '<div class="dependency-section-title">' +
            'Manual Dependencies</div>' +
            '<div id="listManualDeps"></div>' +
            '<div class="dependency-browser-row">' +
            '<button class="btn btn-small" id="btnBrowseDep">' +
            'Browse</button>' +
            '</div>' +
            '<div id="depFileBrowser" class="dep-file-browser" ' +
            'style="display:none"></div>';
    }

    function fsRenderSelectionStep(dictResult) {
        var sHtml = '<h2>Dependency Detection' +
            '<span class="dep-step-indicator">Step 1 of 2</span></h2>';
        var listSuggestions = dictResult.listSuggestions || [];
        var listUnmatched = dictResult.listUnmatchedFiles || [];
        sHtml += fsRenderDetectedSection(listSuggestions);
        sHtml += fsRenderPossibleSection(listUnmatched);
        sHtml += fsRenderManualSection();
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnDepSkip">Skip</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnDepNext">Next</button></div>';
        return sHtml;
    }

    function flistCollectCheckedDeps(elModal, dictResult) {
        var listChecked = [];
        var listBoxes = elModal.querySelectorAll(
            ".dependency-checkbox:checked");
        for (var k = 0; k < listBoxes.length; k++) {
            var elBox = listBoxes[k];
            var sSource = elBox.getAttribute("data-source");
            if (sSource === "detected") {
                var iIdx = parseInt(
                    elBox.getAttribute("data-dep-index"), 10);
                listChecked.push(dictResult.listSuggestions[iIdx]);
            } else if (sSource === "possible") {
                var iUIdx = parseInt(
                    elBox.getAttribute("data-unmatched-idx"), 10);
                var dictFile = dictResult.listUnmatchedFiles[iUIdx];
                var sResolved = fsResolvePathToTemplate(
                    dictFile.sFileName);
                listChecked.push({
                    sFileName: dictFile.sFileName,
                    sSourceStepName: "Manual",
                    sTemplateVariable: sResolved,
                });
            } else if (sSource === "manual") {
                var sPath = elBox.getAttribute("data-file-path");
                var sTemplate = fsResolvePathToTemplate(sPath);
                listChecked.push({
                    sFileName: sPath.split("/").pop(),
                    sSourceStepName: "Manual",
                    sTemplateVariable: sTemplate,
                });
            }
        }
        return listChecked;
    }

    function fsSourceLabel(sSource) {
        if (sSource === "detected") return "Detected";
        if (sSource === "possible") return "Possible";
        return "Manual";
    }

    function fsRenderConfirmStep(listChecked) {
        var sHtml = '<h2>Confirm Dependencies' +
            '<span class="dep-step-indicator">Step 2 of 2</span></h2>';
        if (listChecked.length === 0) {
            sHtml += '<p style="color:var(--text-secondary)">' +
                'No dependencies selected.</p>';
        }
        for (var i = 0; i < listChecked.length; i++) {
            var dictDep = listChecked[i];
            sHtml += '<div class="dependency-suggestion">' +
                '<span style="color:var(--color-pale-blue);">' +
                '&#10003;</span> ' +
                '<span class="dependency-step-badge">' +
                PipeleyenApp.fnEscapeHtml(dictDep.sSourceStepName) +
                '</span> ' +
                '<span>' +
                PipeleyenApp.fnEscapeHtml(dictDep.sFileName) + '</span> ' +
                '<span style="color:var(--text-secondary)">' +
                PipeleyenApp.fnEscapeHtml(fsSourceLabel(
                    dictDep._sSource || "detected")) +
                '</span></div>';
        }
        sHtml += '<div class="modal-actions">' +
            '<button class="btn" id="btnDepBack">Go Back</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnDepConfirm">Confirm</button></div>';
        return sHtml;
    }

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
            'data-file-path="' + PipeleyenApp.fnEscapeHtml(sFilePath) +
            '" class="dependency-checkbox">' +
            '<span>' + PipeleyenApp.fnEscapeHtml(sBasename) + '</span> ' +
            '<span style="color:var(--text-secondary)">' +
            PipeleyenApp.fnEscapeHtml(sFilePath) + '</span>' +
            '<button class="btn btn-small btn-remove-manual" ' +
            'style="margin-left:auto;padding:2px 8px;' +
            'font-size:12px">Remove</button>';
        elList.appendChild(elRow);
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
                '" data-path="' + PipeleyenApp.fnEscapeHtml(dictEntry.sPath) +
                '" data-is-dir="' + dictEntry.bIsDirectory + '">' +
                sIcon + ' ' +
                PipeleyenApp.fnEscapeHtml(dictEntry.sName) + '</div>';
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
                PipeleyenApp.fnEscapeHtml(sBuilt) + '">' +
                PipeleyenApp.fnEscapeHtml("/" + listParts[i]) + '</span>';
        }
        if (!sHtml) {
            sHtml = '<span class="dep-breadcrumb-part" data-path="/">/</span>';
        }
        return '<div class="dep-breadcrumb">' + sHtml + '</div>';
    }

    async function fnLoadBrowserDirectory(elBrowser, sPath) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        elBrowser.style.display = "block";
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

    function fnShowDependencyModal(iStep, dictResult) {
        var elExisting = document.getElementById("modalDependency");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalDependency";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        fnRenderDepModalStep1(elModal, dictResult);
        document.body.appendChild(elModal);
        fnAttachDepModalEvents(elModal, iStep, dictResult);
    }

    function fnRenderDepModalStep1(elModal, dictResult) {
        elModal.innerHTML = '<div class="modal">' +
            fsRenderSelectionStep(dictResult) + '</div>';
    }

    function fnAttachDepModalEvents(elModal, iStep, dictResult) {
        elModal.addEventListener("click", function (event) {
            var elTarget = event.target;
            if (elTarget.id === "btnDepSkip") {
                elModal.remove();
                return;
            }
            if (elTarget.id === "btnDepNext") {
                fnHandleDepNext(elModal, dictResult);
                return;
            }
            if (elTarget.id === "btnDepBack") {
                fnRenderDepModalStep1(elModal, dictResult);
                return;
            }
            if (elTarget.id === "btnDepConfirm") {
                fnHandleDepConfirm(elModal, iStep);
                return;
            }
            if (elTarget.id === "btnBrowseDep") {
                fnHandleDepBrowse(elModal);
                return;
            }
            if (elTarget.classList.contains("btn-remove-manual")) {
                elTarget.closest(".dependency-suggestion").remove();
                return;
            }
            fnHandleBrowserClick(elTarget, elModal, dictResult);
        });
    }

    function fnHandleDepBrowse(elModal) {
        var elBrowser = elModal.querySelector("#depFileBrowser");
        if (!elBrowser) return;
        if (elBrowser.style.display === "block") {
            elBrowser.style.display = "none";
            return;
        }
        fnLoadBrowserDirectory(elBrowser, "/workspace");
    }

    function fnHandleBrowserClick(elTarget, elModal, dictResult) {
        var elEntry = elTarget.closest(
            ".dep-browser-dir, .dep-browser-file");
        if (elEntry) {
            var sPath = elEntry.getAttribute("data-path");
            var bIsDir = elEntry.getAttribute("data-is-dir") ===
                "true";
            var elBrowser = elModal.querySelector("#depFileBrowser");
            if (bIsDir) {
                fnLoadBrowserDirectory(elBrowser, sPath);
            } else {
                fnHandleFileSelection(elModal, sPath, dictResult);
            }
            return;
        }
        var elCrumb = elTarget.closest(".dep-breadcrumb-part");
        if (elCrumb) {
            var sCrumbPath = elCrumb.getAttribute("data-path");
            var elBr = elModal.querySelector("#depFileBrowser");
            fnLoadBrowserDirectory(elBr, sCrumbPath);
        }
    }

    function fnHandleFileSelection(elModal, sPath, dictResult) {
        if (fbDepAlreadyListed(sPath, dictResult, elModal)) {
            PipeleyenApp.fnShowToast(
                "Already listed as a dependency", "info");
            return;
        }
        var elList = elModal.querySelector("#listManualDeps");
        if (elList) fnAddManualDepRow(elList, sPath);
    }

    function fnHandleDepNext(elModal, dictResult) {
        var listChecked = flistCollectCheckedDeps(
            elModal, dictResult);
        for (var i = 0; i < listChecked.length; i++) {
            if (!listChecked[i]._sSource) {
                listChecked[i]._sSource = "detected";
            }
        }
        var elInner = elModal.querySelector(".modal");
        elInner.innerHTML = fsRenderConfirmStep(listChecked);
        elModal._listPendingDeps = listChecked;
    }

    function fnHandleDepConfirm(elModal, iStep) {
        var listPending = elModal._listPendingDeps || [];
        elModal.remove();
        if (listPending.length > 0) {
            fnApplyDependencies(iStep, listPending);
        }
    }

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
                    var iStepNumber = i + 1;
                    var sStepLabel = "Step" +
                        String(iStepNumber).padStart(2, "0");
                    return "{" + sStepLabel + "." + sStem + "}";
                }
            }
        }
        return sPath;
    }

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

    async function fnApplyDependencies(iStep, listChecked) {
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
        if (listNew.length === 0) {
            PipeleyenApp.fnShowToast(
                "No new dependencies to add", "info");
            return;
        }
        var saUpdated = saDependencies.concat(listNew);
        dictStep.saDependencies = saUpdated;
        await fnSaveDependencies(iStep, saUpdated);
        PipeleyenApp.fnShowToast(
            listNew.length + " dependencies added", "success");
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
