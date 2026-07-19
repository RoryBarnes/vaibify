/* Vaibify — Dual figure viewer with unified shared history */

const PipeleyenFigureViewer = (function () {
    "use strict";

    var fbIsFigureFile = VaibifyUtilities.fbIsFigureFile;
    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var fbIsBinaryFile = VaibifyUtilities.fbIsBinaryFile;
    var S_OUTPUT_MISSING = '<span class="placeholder output-missing-message">' +
        'Output not available. Run the step to generate.</span>';

    /* Unified shared history: list of {sPath, sWorkdir, iViewCount, iLastViewed} */
    var listHistory = [];
    var iHistoryCounter = 0;

    /* Two viewer states: A and B
     *
     * The edit-mode block (elTextarea, sBaseText, sBaseHash, sFilePath,
     * sWorkdir, sSaveUrl, elIndicator) is populated by fnEnterEditMode
     * and cleared by fnRenderTextWithToolbar / save success. A viewer
     * is "dirty" iff its textarea value differs from sBaseText. The
     * blocking guard in _fdictResolveTargetViewer reads bDirty to
     * decide whether to redirect an incoming display to the other
     * viewer or to surface the both-dirty modal. */
    var dictViewerA = {
        sId: "A",
        listNavHistory: [],
        iNavIndex: -1,
        elTextarea: null,
        sBaseText: null,
        sBaseHash: null,
        sFilePath: null,
        sWorkdir: null,
        sSaveUrl: null,
        elIndicator: null,
    };
    var dictViewerB = {
        sId: "B",
        listNavHistory: [],
        iNavIndex: -1,
        elTextarea: null,
        sBaseText: null,
        sBaseHash: null,
        sFilePath: null,
        sWorkdir: null,
        sSaveUrl: null,
        elIndicator: null,
    };
    var sNextViewer = "A";

    /* --- Shared History --- */

    function fnAddToHistory(sPath, sWorkdir) {
        iHistoryCounter++;
        var dictExisting = null;
        for (var i = 0; i < listHistory.length; i++) {
            if (listHistory[i].sPath === sPath) {
                dictExisting = listHistory[i];
                break;
            }
        }
        if (dictExisting) {
            dictExisting.iViewCount++;
            dictExisting.iLastViewed = iHistoryCounter;
        } else {
            listHistory.push({
                sPath: sPath,
                sWorkdir: sWorkdir || "",
                iViewCount: 1,
                iLastViewed: iHistoryCounter,
            });
        }
        fnTrimHistory();
        fnRefreshHistoryDropdowns();
    }

    var I_MAX_HISTORY_ENTRIES = 200;

    function fnTrimHistory() {
        if (listHistory.length <= I_MAX_HISTORY_ENTRIES) return;
        listHistory.sort(fdCompareHistoryScore);
        listHistory.length = I_MAX_HISTORY_ENTRIES;
    }

    function fdCompareHistoryScore(a, b) {
        return fdHistoryScore(b) - fdHistoryScore(a);
    }

    function fdHistoryScore(dictItem) {
        var dRecency = dictItem.iLastViewed / Math.max(iHistoryCounter, 1);
        var dFrequency = Math.log(1 + dictItem.iViewCount) / Math.log(101);
        return 0.6 * dRecency + 0.4 * dFrequency;
    }

    function flistGetSortedHistory() {
        var listSorted = listHistory.slice();
        listSorted.sort(fdCompareHistoryScore);
        return listSorted;
    }

    function fnRefreshHistoryDropdowns() {
        fnPopulateHistorySelect("selectFigureA", dictViewerA);
        fnPopulateHistorySelect("selectFigureB", dictViewerB);
    }

    function fnPopulateHistorySelect(sSelectId, dictViewer) {
        var elSelect = document.getElementById(sSelectId);
        if (!elSelect) return;
        var dictCurrent = fdictGetCurrentEntry(dictViewer);
        var sCurrentPath = dictCurrent ? dictCurrent.sPath : null;
        elSelect.innerHTML = '<option value="">Select a file...</option>';
        var listSorted = flistGetSortedHistory();
        listSorted.forEach(function (dictItem) {
            var elOption = document.createElement("option");
            elOption.value = dictItem.sPath;
            elOption.textContent = dictItem.sPath.split("/").pop();
            elOption.title = dictItem.sPath;
            if (dictItem.sPath === sCurrentPath) {
                elOption.selected = true;
            }
            elSelect.appendChild(elOption);
        });
        elSelect.onchange = function () {
            if (!elSelect.value) return;
            var sWorkdir = fsGetWorkdirForPath(elSelect.value);
            var sChosenPath = elSelect.value;
            _fnConfirmIfDirty(
                dictViewer, "Switching files",
                function () {
                    fnNavigateToPath(dictViewer, sChosenPath, sWorkdir);
                });
        };
    }

    function fsGetWorkdirForPath(sPath) {
        for (var i = 0; i < listHistory.length; i++) {
            if (listHistory[i].sPath === sPath) {
                return listHistory[i].sWorkdir;
            }
        }
        return "";
    }

    function fdictGetCurrentEntry(dictViewer) {
        if (dictViewer.iNavIndex >= 0 &&
            dictViewer.iNavIndex < dictViewer.listNavHistory.length) {
            return dictViewer.listNavHistory[dictViewer.iNavIndex];
        }
        return null;
    }

    /* --- Public entry points --- */

    function fnLoadStepFigures(iStepIndex) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId || iStepIndex < 0) return;

        fnFetchResolvedStep(iStepIndex, function (dictStep) {
            var listOutputFiles =
                dictStep.saResolvedOutputFiles ||
                dictStep.saPlotFiles || [];
            var listFigures = listOutputFiles.filter(fbIsFigureFile);

            if (listFigures.length > 0) {
                _fdictResolveTargetViewer(
                    "A", listFigures[0], function (dictViewer) {
                        fnNavigateToPath(
                            dictViewer, listFigures[0],
                            dictStep.sDirectory);
                    });
            }
        });
    }

    function fsClaimNextViewer() {
        var sClaimed = sNextViewer;
        sNextViewer = sNextViewer === "A" ? "B" : "A";
        return sClaimed;
    }

    /* --- Dirty-state tracking & blocking guard --- */

    function _fdictViewerByLetter(sLetter) {
        return sLetter === "A" ? dictViewerA : dictViewerB;
    }

    function fbViewerHasOpenEditor(dictViewer) {
        if (!dictViewer || !dictViewer.elTextarea) return false;
        return document.body.contains(dictViewer.elTextarea);
    }

    function fbViewerIsDirty(dictViewer) {
        if (!fbViewerHasOpenEditor(dictViewer)) return false;
        return dictViewer.elTextarea.value !== dictViewer.sBaseText;
    }

    function fbAnyViewerDirty() {
        return fbViewerIsDirty(dictViewerA) ||
            fbViewerIsDirty(dictViewerB);
    }

    function _fdictResolveTargetViewer(
        sPreferredLetter, sIncomingPath, fnOnReady,
    ) {
        var dictPreferred = _fdictViewerByLetter(sPreferredLetter);
        var dictOther = _fdictViewerByLetter(
            sPreferredLetter === "A" ? "B" : "A");
        if (!fbViewerHasOpenEditor(dictPreferred)) {
            fnOnReady(dictPreferred);
            return;
        }
        if (!fbViewerHasOpenEditor(dictOther)) {
            PipeleyenApp.fnShowToast(
                "Open editor in viewer " + dictPreferred.sId +
                " protected — opened in viewer " + dictOther.sId,
                "info");
            fnOnReady(dictOther);
            return;
        }
        _fnShowBothEditorsOpenModal(sIncomingPath, function () {
            _fdictResolveTargetViewer(
                sPreferredLetter, sIncomingPath, fnOnReady);
        });
    }

    function _fsDirtyFileLabel(dictViewer) {
        if (!dictViewer.sFilePath) return "viewer " + dictViewer.sId;
        var sBase = dictViewer.sFilePath.split("/").pop();
        return sBase + " (viewer " + dictViewer.sId + ")";
    }

    function _fnShowBothEditorsOpenModal(sIncomingPath, fnRetry) {
        var bAnyDirty = fbViewerIsDirty(dictViewerA) ||
            fbViewerIsDirty(dictViewerB);
        var sTitle = bAnyDirty
            ? "Unsaved edits in both viewers"
            : "Both viewers have open editors";
        var sMessage = _fsBothOpenModalMessage(
            sIncomingPath, bAnyDirty);
        var listChoices = _flistBuildBothOpenChoices(fnRetry);
        listChoices.push({sLabel: "Cancel"});
        PipeleyenModals.fnShowChoiceModal(
            sTitle, sMessage, listChoices);
    }

    function _fsBothOpenModalMessage(sIncomingPath, bAnyDirty) {
        var sIncomingLabel = sIncomingPath.split("/").pop();
        var sLead = bAnyDirty
            ? "Both viewers have open editors and at least one " +
              "has unsaved edits. Save or discard the open editors " +
              "you want to clear, then the new file can replace one."
            : "Both viewers have open editors. Close one of the " +
              "editors to view the new file.";
        return sLead + "\n\n" +
            "Pending file: " + sIncomingLabel + "\n" +
            "Viewer A: " + _fsDirtyFileLabel(dictViewerA) + "\n" +
            "Viewer B: " + _fsDirtyFileLabel(dictViewerB);
    }

    function _flistBuildBothOpenChoices(fnRetry) {
        return [].concat(
            _flistChoicesForViewer(dictViewerA, fnRetry),
            _flistChoicesForViewer(dictViewerB, fnRetry));
    }

    function _flistChoicesForViewer(dictViewer, fnRetry) {
        if (!fbViewerIsDirty(dictViewer)) {
            return [{
                sLabel: "Close " + dictViewer.sId,
                fnCallback: function () {
                    _fnFlushSaveOrDiscard(dictViewer, false, fnRetry);
                },
            }];
        }
        return [
            {sLabel: "Save " + dictViewer.sId,
             sStyleClass: "btn-primary",
             fnCallback: function () {
                 _fnFlushSaveOrDiscard(dictViewer, true, fnRetry);
             }},
            {sLabel: "Discard " + dictViewer.sId,
             fnCallback: function () {
                 _fnFlushSaveOrDiscard(dictViewer, false, fnRetry);
             }},
        ];
    }

    function _fnFlushSaveOrDiscard(dictViewer, bSave, fnRetry) {
        if (bSave) {
            fnSaveEditedFile(
                dictViewer.sSaveUrl,
                dictViewer.elTextarea.value,
                fnGetViewport(dictViewer),
                function () { fnRetry(); });
        } else {
            _fnDiscardEditingViewer(dictViewer);
            fnRetry();
        }
    }

    function _fnDiscardEditingViewer(dictViewer) {
        var sBaseText = dictViewer.sBaseText || "";
        var sSaveUrl = dictViewer.sSaveUrl || "";
        if (dictViewer.sFilePath) {
            _fnDiscardDraftFor(dictViewer);
        }
        _fnClearEditingState(dictViewer);
        fnRenderTextWithToolbar(
            sBaseText, sSaveUrl, fnGetViewport(dictViewer));
    }

    function _fnConfirmIfDirty(dictViewer, sActionLabel, fnOnConfirm) {
        if (!fbViewerIsDirty(dictViewer)) {
            fnOnConfirm();
            return;
        }
        PipeleyenApp.fnShowConfirmModal(
            "Discard unsaved edits?",
            "Viewer " + dictViewer.sId + " has unsaved edits in " +
            _fsDirtyFileLabel(dictViewer) + ". " + sActionLabel +
            " will lose those edits. The autosaved draft will " +
            "remain available to restore.",
            function () {
                _fnDiscardEditingViewer(dictViewer);
                fnOnConfirm();
            });
    }

    function fnDisplayFileInViewer(sViewerLetter, sPath, sWorkdir) {
        _fdictResolveTargetViewer(
            sViewerLetter, sPath, function (dictViewer) {
                fnNavigateToPath(dictViewer, sPath, sWorkdir || "");
            });
    }

    function fnDisplayInNextViewer(sPath, sWorkdir) {
        _fdictResolveTargetViewer(
            sNextViewer, sPath, function (dictViewer) {
                sNextViewer = dictViewer.sId === "A" ? "B" : "A";
                fnNavigateToPath(dictViewer, sPath, sWorkdir || "");
            });
    }

    function fnDisplayFileFromContainer(sPath) {
        fnDisplayInNextViewer(sPath, "");
    }

    function fnDisplayFigureByTemplate(sTemplatePath) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var iStepIndex = PipeleyenApp.fiGetSelectedStepIndex();
        if (!sContainerId || iStepIndex < 0) return;

        fnFetchResolvedStep(iStepIndex, function (dictStep) {
            var listRaw = dictStep.saPlotFiles || [];
            var listResolved =
                dictStep.saResolvedOutputFiles || listRaw;
            var sResolvedPath = sTemplatePath;
            var iMatch = listRaw.indexOf(sTemplatePath);
            if (iMatch >= 0 && iMatch < listResolved.length) {
                sResolvedPath = listResolved[iMatch];
            }
            fnDisplayInNextViewer(sResolvedPath, dictStep.sDirectory);
        });
    }

    /* --- Internal --- */

    function fnFetchResolvedStep(iStepIndex, fnCallback) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        fetch("/api/steps/" + sContainerId + "/" + iStepIndex)
            .then(function (r) { return r.json(); })
            .then(fnCallback)
            .catch(function () {
                var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
                if (dictWorkflow && dictWorkflow.listSteps[iStepIndex]) {
                    fnCallback(dictWorkflow.listSteps[iStepIndex]);
                }
            });
    }

    function fnGetViewport(dictViewer) {
        return document.getElementById("viewport" + dictViewer.sId);
    }

    function fnNavigateToPath(dictViewer, sPath, sWorkdir) {
        var dictEntry = { sPath: sPath, sWorkdir: sWorkdir || "" };
        /* Trim forward nav history */
        if (dictViewer.iNavIndex < dictViewer.listNavHistory.length - 1) {
            dictViewer.listNavHistory = dictViewer.listNavHistory.slice(
                0, dictViewer.iNavIndex + 1
            );
        }
        dictViewer.listNavHistory.push(dictEntry);
        dictViewer.iNavIndex = dictViewer.listNavHistory.length - 1;
        fnAddToHistory(sPath, sWorkdir);
        fnDisplayInViewport(dictViewer, dictEntry);
        fnUpdateNavButtons(dictViewer);
    }

    function fnNavigateBack(dictViewer) {
        if (dictViewer.iNavIndex <= 0) return;
        dictViewer.iNavIndex--;
        var dictEntry = dictViewer.listNavHistory[dictViewer.iNavIndex];
        fnAddToHistory(dictEntry.sPath, dictEntry.sWorkdir);
        fnDisplayInViewport(dictViewer, dictEntry);
        fnUpdateNavButtons(dictViewer);
    }

    function fnNavigateForward(dictViewer) {
        if (dictViewer.iNavIndex >= dictViewer.listNavHistory.length - 1) {
            return;
        }
        dictViewer.iNavIndex++;
        var dictEntry = dictViewer.listNavHistory[dictViewer.iNavIndex];
        fnAddToHistory(dictEntry.sPath, dictEntry.sWorkdir);
        fnDisplayInViewport(dictViewer, dictEntry);
        fnUpdateNavButtons(dictViewer);
    }

    function fnUpdateNavButtons(dictViewer) {
        var sId = dictViewer.sId;
        document.getElementById("btnBack" + sId).disabled =
            dictViewer.iNavIndex <= 0;
        document.getElementById("btnForward" + sId).disabled =
            dictViewer.iNavIndex >= dictViewer.listNavHistory.length - 1;
    }

    function fnDisplayInViewport(dictViewer, dictEntry) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sPath = dictEntry.sPath;
        var sWorkdir = dictEntry.sWorkdir || "";
        var sCleanPath = sPath.replace(/^\/+/, "");
        var sUrl = "/api/figure/" + sContainerId + "/" + sCleanPath;
        if (sWorkdir) {
            sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
        }
        var elViewport = fnGetViewport(dictViewer);
        var iDot = sPath.lastIndexOf(".");
        var sExtension = iDot >= 0 ?
            sPath.substring(iDot).toLowerCase() : "";

        fnApplyTestFailureOutline(elViewport, sPath);

        if (fbIsBinaryFile(sPath)) {
            elViewport.innerHTML =
                '<span class="placeholder" style="color:var(--color-red-text)">' +
                'File cannot be viewed.</span>';
        } else if (sExtension === ".pdf") {
            fnRenderPdf(sUrl, elViewport);
        } else if (fbIsFigureFile(sPath)) {
            fnRenderImage(sUrl, elViewport);
        } else {
            fnRenderText(sUrl, elViewport);
        }
    }

    function fnRenderImage(sUrl, elViewport) {
        fnDestroyActivePdf(elViewport);
        fnCancelPendingImage(elViewport);
        elViewport.innerHTML = "";
        fetch(sUrl).then(function (response) {
            if (!response.ok) throw new Error("Not found");
            return response.blob();
        }).then(function (blob) {
            var sBlobUrl = URL.createObjectURL(blob);
            fnDisplayImageBlob(sBlobUrl, sUrl, elViewport);
        }).catch(function () {
            elViewport.innerHTML = S_OUTPUT_MISSING;
        });
    }

    function fnDisplayImageBlob(sBlobUrl, sUrl, elViewport) {
        var elImg = document.createElement("img");
        elImg.src = sBlobUrl;
        elImg.alt = "Figure";
        var dScale = 1.0;
        elImg.onload = function () {
            fnRenderImageWithZoom(elImg, sUrl, elViewport, dScale);
        };
        elImg.onerror = function () {
            URL.revokeObjectURL(sBlobUrl);
            elViewport.innerHTML = S_OUTPUT_MISSING;
        };
        elViewport._activeImage = elImg;
        elViewport._activeBlobUrl = sBlobUrl;
        elViewport.appendChild(elImg);
    }

    function fnCancelPendingImage(elViewport) {
        if (elViewport._activeBlobUrl) {
            URL.revokeObjectURL(elViewport._activeBlobUrl);
            elViewport._activeBlobUrl = null;
        }
        if (elViewport._activeImage) {
            elViewport._activeImage.onload = null;
            elViewport._activeImage.onerror = null;
            elViewport._activeImage.src = "";
            elViewport._activeImage = null;
        }
    }

    function fnRenderImageWithZoom(
        elImg, sUrl, elViewport, dScale
    ) {
        var iNativeWidth = elImg.naturalWidth || 800;
        var iNativeHeight = elImg.naturalHeight || 800;
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        if (dScale === "fit") {
            dScale = (elViewport.clientWidth - 32) / iNativeWidth;
        }
        var iDisplayWidth = Math.round(iNativeWidth * dScale);
        var iDisplayHeight = Math.round(iNativeHeight * dScale);
        var elToolbar = fnCreateZoomToolbar(
            dScale, function (dNewScale) {
                fnRenderImageWithZoom(
                    elImg, sUrl, elViewport, dNewScale
                );
            }
        );
        elImg.style.width = iDisplayWidth + "px";
        elImg.style.height = iDisplayHeight + "px";
        elViewport.appendChild(elToolbar);
        elViewport.appendChild(fnCreateScrollableContent(elImg));
    }

    function fnRenderPdf(sUrl, elViewport) {
        elViewport.innerHTML =
            '<span class="placeholder">Loading PDF...</span>';
        fetch(sUrl, { method: "HEAD" }).then(function (r) {
            if (!r.ok) {
                elViewport.innerHTML =
                    '<span class="placeholder output-missing-message">' +
                    'Output not available. Run the step to generate.' +
                    '</span>';
                return;
            }
            fnRenderPdfDocument(sUrl, elViewport);
        }).catch(function () {
            elViewport.innerHTML =
                S_OUTPUT_MISSING;
        });
    }

    function felCreateZoomButton(sClass, sTitle, sText, fnClick) {
        var elButton = document.createElement("button");
        elButton.className = sClass;
        elButton.title = sTitle;
        elButton.textContent = sText;
        elButton.addEventListener("click", fnClick);
        return elButton;
    }

    var _LIST_ZOOM_LEVELS = [
        0.10, 0.25, 0.50, 0.75, 1.0,
        1.25, 1.50, 2.0, 3.0, 4.0,
    ];

    function fiNextZoomIndex(dCurrentScale, iDirection) {
        for (var i = 0; i < _LIST_ZOOM_LEVELS.length; i++) {
            if (_LIST_ZOOM_LEVELS[i] >= dCurrentScale - 0.001) {
                return Math.max(0, Math.min(
                    _LIST_ZOOM_LEVELS.length - 1, i + iDirection));
            }
        }
        return _LIST_ZOOM_LEVELS.length - 1;
    }

    function fnCreateZoomToolbar(dCurrentScale, fnOnZoom) {
        var elToolbar = document.createElement("div");
        elToolbar.className = "editor-toolbar zoom-toolbar";
        var sLabel = dCurrentScale === "fit" ? "Fit" :
            Math.round(dCurrentScale * 100) + "%";
        var elZoomLevel = document.createElement("span");
        elZoomLevel.className = "zoom-level";
        elZoomLevel.textContent = sLabel;
        elToolbar.appendChild(felCreateZoomButton(
            "btn-icon", "Zoom out", "\u2212", function () {
                var dNumeric = dCurrentScale === "fit" ?
                    1.0 : dCurrentScale;
                var iIdx = fiNextZoomIndex(dNumeric, -1);
                fnOnZoom(_LIST_ZOOM_LEVELS[iIdx]);
            }));
        elToolbar.appendChild(elZoomLevel);
        elToolbar.appendChild(felCreateZoomButton(
            "btn-icon", "Zoom in", "+", function () {
                var dNumeric = dCurrentScale === "fit" ?
                    1.0 : dCurrentScale;
                var iIdx = fiNextZoomIndex(dNumeric, 1);
                fnOnZoom(_LIST_ZOOM_LEVELS[iIdx]);
            }));
        elToolbar.appendChild(felCreateZoomButton(
            "btn-icon", "Fit to window", "\u2922", function () {
                fnOnZoom("fit");
            }));
        return elToolbar;
    }

    /* PDF state is per-viewport. A module-level singleton would let
     * a render in one viewer destroy the other viewer's PDF document
     * out from under its zoom-toolbar callbacks, leaving the first
     * viewer's "+", "-", and fit buttons holding a dead page handle
     * (the regression this layout prevents). Callers pass the
     * viewport in so each viewer gets independent lifecycle. */

    function fnCancelActivePdfRender(elViewport) {
        if (elViewport && elViewport._activePdfRenderTask) {
            elViewport._activePdfRenderTask.cancel();
            elViewport._activePdfRenderTask = null;
        }
    }

    function fnDestroyActivePdf(elViewport) {
        if (!elViewport) return;
        fnCancelActivePdfRender(elViewport);
        if (elViewport._activePdfDocument) {
            elViewport._activePdfDocument.destroy();
            elViewport._activePdfDocument = null;
        }
    }

    function fnReleaseAllResources() {
        ["viewportA", "viewportB"].forEach(function (sId) {
            var elViewport = document.getElementById(sId);
            if (elViewport) fnDestroyActivePdf(elViewport);
        });
        listHistory = [];
        iHistoryCounter = 0;
        dictViewerA.listNavHistory = [];
        dictViewerA.iNavIndex = -1;
        dictViewerB.listNavHistory = [];
        dictViewerB.iNavIndex = -1;
    }

    function fnRenderPdfDocument(sUrl, elViewport) {
        fnDestroyActivePdf(elViewport);
        if (typeof pdfjsLib === "undefined") {
            elViewport.innerHTML =
                '<span class="placeholder">PDF.js not loaded</span>';
            return;
        }
        pdfjsLib.GlobalWorkerOptions.workerSrc =
            "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
        pdfjsLib.getDocument({
            url: sUrl, isEvalSupported: false,
        }).promise.then(function (pdfDoc) {
            elViewport._activePdfDocument = pdfDoc;
            pdfDoc.getPage(1).then(function (page) {
                fnRenderPdfPage(page, elViewport, 1.0);
            });
        }).catch(function () {
            elViewport.innerHTML =
                S_OUTPUT_MISSING;
        });
    }

    function fnCreateScrollableContent(elChild) {
        var elContent = document.createElement("div");
        elContent.style.overflow = "auto";
        elContent.style.flex = "1";
        elContent.style.display = "flex";
        elContent.style.justifyContent = "center";
        elContent.style.padding = "16px";
        elContent.appendChild(elChild);
        return elContent;
    }

    function felCreatePdfCanvas(viewport) {
        var elCanvas = document.createElement("canvas");
        elCanvas.width = viewport.width;
        elCanvas.height = viewport.height;
        elCanvas.style.width = viewport.width / 2 + "px";
        elCanvas.style.height = viewport.height / 2 + "px";
        return elCanvas;
    }

    function fnRenderPdfPage(page, elViewport, dDisplayScale) {
        var iContainerWidth = elViewport.clientWidth;
        var dNativeViewport = page.getViewport({ scale: 1.0 });
        if (dDisplayScale === "fit") {
            dDisplayScale = (iContainerWidth - 32) /
                dNativeViewport.width;
        }
        dDisplayScale = Math.max(dDisplayScale, 0.10);
        fnCancelActivePdfRender(elViewport);
        var viewport = page.getViewport({ scale: dDisplayScale * 2 });
        var elCanvas = felCreatePdfCanvas(viewport);
        var renderTask = page.render({
            canvasContext: elCanvas.getContext("2d"),
            viewport: viewport,
        });
        elViewport._activePdfRenderTask = renderTask;
        renderTask.promise.then(function () {
            elViewport._activePdfRenderTask = null;
            fnSwapPdfContent(page, elViewport, elCanvas, dDisplayScale);
        }).catch(function (reason) {
            if (reason && reason.name === "RenderCancelled") return;
            elViewport._activePdfRenderTask = null;
        });
    }

    function fnSwapPdfContent(
        page, elViewport, elCanvas, dDisplayScale
    ) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        var elToolbar = fnCreateZoomToolbar(
            dDisplayScale, function (dNewScale) {
                fnRenderPdfPage(page, elViewport, dNewScale);
            }
        );
        elViewport.appendChild(elToolbar);
        elViewport.appendChild(fnCreateScrollableContent(elCanvas));
    }

    function fnRenderText(sUrl, elViewport) {
        fnDestroyActivePdf(elViewport);
        fnCancelPendingImage(elViewport);
        elViewport.innerHTML =
            '<span class="placeholder">Loading...</span>';
        fetch(sUrl)
            .then(function (r) {
                if (!r.ok) throw new Error("Not found");
                return r.text();
            })
            .then(function (sText) {
                fnRenderTextWithToolbar(sText, sUrl, elViewport);
            })
            .catch(function () {
                elViewport.innerHTML =
                    '<span class="placeholder output-missing-message">' +
                    'Output not available. Run the step to generate.' +
                    '</span>';
            });
    }

    function fnHandleEditClick(sText, sUrl, elViewport) {
        if (fbIsOutputFile(sUrl)) {
            PipeleyenApp.fnShowConfirmModal(
                "Edit Pipeline Output",
                "This file is pipeline output that may be " +
                "used for verification. Edit anyway?",
                function () {
                    fnEnterEditMode(sText, sUrl, elViewport);
                }
            );
            return;
        }
        fnEnterEditMode(sText, sUrl, elViewport);
    }

    function fnRenderTextWithToolbar(sText, sUrl, elViewport) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        var elToolbar = document.createElement("div");
        elToolbar.className = "editor-toolbar";
        var elEditBtn = felCreateZoomButton(
            "btn-icon", "Edit", "", function () {
                fnHandleEditClick(sText, sUrl, elViewport);
            });
        elEditBtn.innerHTML = "&#9998;";
        elToolbar.appendChild(elEditBtn);
        var elPre = document.createElement("pre");
        elPre.textContent = sText;
        elViewport.appendChild(elToolbar);
        elViewport.appendChild(elPre);
    }

    function fnCreateEditorToolbar() {
        var elToolbar = document.createElement("div");
        elToolbar.className = "editor-toolbar";
        var elFind = document.createElement("input");
        elFind.type = "text";
        elFind.placeholder = "Find...";
        elFind.className = "editor-find-input";
        var elSave = document.createElement("button");
        elSave.className = "btn btn-primary";
        elSave.textContent = "Save";
        var elCancel = document.createElement("button");
        elCancel.className = "btn";
        elCancel.textContent = "Cancel";
        var elIndicator = document.createElement("span");
        elIndicator.className = "editor-draft-indicator";
        elIndicator.style.marginLeft = "8px";
        elIndicator.style.fontSize = "0.85em";
        elIndicator.style.opacity = "0.8";
        elToolbar.appendChild(elFind);
        elToolbar.appendChild(elSave);
        elToolbar.appendChild(elCancel);
        elToolbar.appendChild(elIndicator);
        return { elToolbar: elToolbar, elFind: elFind,
                 elSave: elSave, elCancel: elCancel,
                 elIndicator: elIndicator };
    }

    function _fdictViewerForViewport(elViewport) {
        if (elViewport === fnGetViewport(dictViewerA)) return dictViewerA;
        if (elViewport === fnGetViewport(dictViewerB)) return dictViewerB;
        return null;
    }

    function _fdictParseFigureUrl(sUrl) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sPrefix = "/api/figure/" + sContainerId + "/";
        var sRest = sUrl.split(sPrefix)[1] || "";
        var sWorkdir = "";
        var sFilePath = sRest;
        if (sRest.includes("?")) {
            sFilePath = sRest.split("?")[0];
            var dictParams = new URLSearchParams(
                sRest.split("?")[1] || "");
            sWorkdir = dictParams.get("sWorkdir") || "";
        }
        return {
            sContainerId: sContainerId,
            sFilePath: sFilePath,
            sWorkdir: sWorkdir,
        };
    }

    function fiOffsetOfLine(sText, iLine) {
        if (iLine <= 0) return 0;
        var iOffset = 0;
        var iFound = 0;
        while (iFound < iLine) {
            var iNext = sText.indexOf("\n", iOffset);
            if (iNext === -1) return sText.length;
            iOffset = iNext + 1;
            iFound++;
        }
        return iOffset;
    }

    function fiFirstVisibleLineInViewer(elViewport) {
        var elPre = elViewport.querySelector("pre");
        if (!elPre) return 0;
        var elScroll = (elPre.scrollTop > 0) ? elPre : elViewport;
        var iScrollTop = elScroll.scrollTop;
        if (iScrollTop <= 0) return 0;
        var iScrollHeight = elScroll.scrollHeight;
        if (iScrollHeight <= 0) return 0;
        var sText = elPre.textContent || "";
        var iTotalLines = sText.split("\n").length;
        var fRatio = iScrollTop / iScrollHeight;
        return Math.floor(fRatio * iTotalLines);
    }

    function felBuildEditorTextarea(sText) {
        var elTextarea = document.createElement("textarea");
        elTextarea.className = "editor-textarea";
        elTextarea.value = sText;
        elTextarea.spellcheck = false;
        elTextarea.setAttribute("autocomplete", "off");
        elTextarea.setAttribute("autocorrect", "off");
        elTextarea.setAttribute("autocapitalize", "off");
        elTextarea.setAttribute("inputmode", "text");
        elTextarea.setAttribute("data-gramm", "false");
        elTextarea.setAttribute("data-gramm_editor", "false");
        return elTextarea;
    }

    function fnPlaceCursorAtLine(elTextarea, sText, iLine) {
        var iOffset = fiOffsetOfLine(sText, iLine);
        elTextarea.focus();
        elTextarea.setSelectionRange(iOffset, iOffset);
        var iTotalLines = sText.split("\n").length;
        var iScrollHeight = elTextarea.scrollHeight;
        if (iTotalLines > 0 && iScrollHeight > 0) {
            elTextarea.scrollTop =
                (iLine / iTotalLines) * iScrollHeight;
        }
    }

    function fnEnterEditMode(sText, sUrl, elViewport) {
        var iLine = fiFirstVisibleLineInViewer(elViewport);
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        var dictToolbar = fnCreateEditorToolbar();
        var elTextarea = felBuildEditorTextarea(sText);
        elViewport.appendChild(dictToolbar.elToolbar);
        var elRecoveryHolder = document.createElement("div");
        elRecoveryHolder.className = "editor-recovery-holder";
        elViewport.appendChild(elRecoveryHolder);
        elViewport.appendChild(elTextarea);
        fnPlaceCursorAtLine(elTextarea, sText, iLine);
        fnBindEditorFind(dictToolbar.elFind, elTextarea);
        _fnInitEditingState(
            sText, sUrl, elViewport, elTextarea,
            dictToolbar.elIndicator);
        dictToolbar.elSave.addEventListener("click", function () {
            fnSaveEditedFile(
                sUrl, elTextarea.value, elViewport, null);
        });
        dictToolbar.elCancel.addEventListener("click", function () {
            var dictViewer = _fdictViewerForViewport(elViewport);
            if (dictViewer && fbViewerIsDirty(dictViewer)) {
                _fnConfirmIfDirty(
                    dictViewer, "Cancel",
                    function () { /* discarded in helper */ });
                return;
            }
            _fnClearEditingState(_fdictViewerForViewport(elViewport));
            fnRenderTextWithToolbar(sText, sUrl, elViewport);
        });
        _fnAttachAutosave(elTextarea, elViewport);
        _fnOfferDraftRecovery(elRecoveryHolder, elTextarea, elViewport);
    }

    function _fnInitEditingState(
        sText, sUrl, elViewport, elTextarea, elIndicator,
    ) {
        var dictViewer = _fdictViewerForViewport(elViewport);
        if (!dictViewer) return;
        var dictUrl = _fdictParseFigureUrl(sUrl);
        dictViewer.elTextarea = elTextarea;
        dictViewer.sBaseText = sText;
        dictViewer.sBaseHash = null;
        dictViewer.sFilePath = dictUrl.sFilePath;
        dictViewer.sWorkdir = dictUrl.sWorkdir;
        dictViewer.sSaveUrl = fsBuildSaveUrl(sUrl);
        dictViewer.elIndicator = elIndicator;
        PipeleyenDraftManager.fsHashContent(sText).then(
            function (sHash) {
                if (dictViewer.elTextarea === elTextarea) {
                    dictViewer.sBaseHash = sHash;
                }
            });
        _fnUpdateIndicator(dictViewer, "clean");
    }

    function _fnClearEditingState(dictViewer) {
        if (!dictViewer) return;
        dictViewer.elTextarea = null;
        dictViewer.sBaseText = null;
        dictViewer.sBaseHash = null;
        dictViewer.sFilePath = null;
        dictViewer.sWorkdir = null;
        dictViewer.sSaveUrl = null;
        dictViewer.elIndicator = null;
    }

    function _fnUpdateIndicator(dictViewer, sState) {
        if (!dictViewer || !dictViewer.elIndicator) return;
        var elIndicator = dictViewer.elIndicator;
        if (sState === "clean") {
            elIndicator.textContent = "";
            elIndicator.title = "";
        } else if (sState === "dirty") {
            elIndicator.textContent = "● unsaved";
            elIndicator.style.color = "var(--color-red, #c0392b)";
            elIndicator.title =
                "Edits not yet saved to disk; draft autosave in progress";
        } else if (sState === "draft-local") {
            elIndicator.textContent = "● draft (local)";
            elIndicator.style.color = "var(--color-amber, #d97706)";
            elIndicator.title = "Draft saved to this browser";
        } else if (sState === "draft-remote") {
            elIndicator.textContent = "● draft (saved)";
            elIndicator.style.color = "var(--color-amber, #d97706)";
            elIndicator.title = "Draft mirrored to the container";
        } else if (sState === "saved") {
            elIndicator.textContent = "✓ saved";
            elIndicator.style.color = "var(--color-green, #16a34a)";
            elIndicator.title = "File saved to disk";
            setTimeout(function () {
                if (elIndicator.textContent === "✓ saved") {
                    elIndicator.textContent = "";
                }
            }, 2000);
        }
    }

    function _fnAttachAutosave(elTextarea, elViewport) {
        var dictViewer = _fdictViewerForViewport(elViewport);
        if (!dictViewer) return;
        elTextarea.addEventListener("input", function () {
            _fnOnEditorInput(dictViewer, elTextarea);
        });
        elTextarea.addEventListener("blur", function () {
            var sDraftKey = _fsCurrentDraftKey(dictViewer);
            if (sDraftKey) {
                PipeleyenDraftManager.fnFlushPendingSaves(sDraftKey);
            }
        });
    }

    function _fsCurrentDraftKey(dictViewer) {
        if (!dictViewer || !dictViewer.sFilePath) return "";
        return PipeleyenDraftManager.fsBuildDraftKey(
            PipeleyenApp.fsGetContainerId(),
            dictViewer.sFilePath, dictViewer.sWorkdir);
    }

    function _fnOnEditorInput(dictViewer, elTextarea) {
        if (!dictViewer || dictViewer.elTextarea !== elTextarea) return;
        if (elTextarea.value === dictViewer.sBaseText) {
            _fnUpdateIndicator(dictViewer, "clean");
            return;
        }
        _fnUpdateIndicator(dictViewer, "dirty");
        var sDraftKey = _fsCurrentDraftKey(dictViewer);
        if (!sDraftKey) return;
        var dictDraft = {
            sContent: elTextarea.value,
            sBaseHash: dictViewer.sBaseHash || "",
            iTimestampMs: Date.now(),
        };
        PipeleyenDraftManager.fnSaveLocalDraft(sDraftKey, dictDraft);
        PipeleyenDraftManager.fnSaveRemoteDraft(
            sDraftKey, dictDraft,
            PipeleyenApp.fsGetContainerId(),
            dictViewer.sFilePath, dictViewer.sWorkdir);
        _fnScheduleIndicatorPromotion(dictViewer);
    }

    function _fnScheduleIndicatorPromotion(dictViewer) {
        setTimeout(function () {
            if (!dictViewer.elTextarea ||
                    dictViewer.elTextarea.value === dictViewer.sBaseText) {
                return;
            }
            _fnUpdateIndicator(dictViewer, "draft-local");
        }, 700);
        setTimeout(function () {
            if (!dictViewer.elTextarea ||
                    dictViewer.elTextarea.value === dictViewer.sBaseText) {
                return;
            }
            _fnUpdateIndicator(dictViewer, "draft-remote");
        }, 5500);
    }

    function _fnDiscardDraftFor(dictViewer) {
        var sDraftKey = _fsCurrentDraftKey(dictViewer);
        if (!sDraftKey) return;
        PipeleyenDraftManager.fnDeleteDraft(
            sDraftKey, PipeleyenApp.fsGetContainerId(),
            dictViewer.sFilePath, dictViewer.sWorkdir);
    }

    async function _fnOfferDraftRecovery(
        elBanner, elTextarea, elViewport,
    ) {
        var dictViewer = _fdictViewerForViewport(elViewport);
        if (!dictViewer) return;
        var dictNewest = await _fdictPickNewestDraft(dictViewer);
        if (!dictNewest) return;
        if (dictNewest.sContent === dictViewer.sBaseText) {
            _fnDiscardDraftFor(dictViewer);
            return;
        }
        var sCurrentHash = await PipeleyenDraftManager.fsHashContent(
            dictViewer.sBaseText || "");
        var bStaleBase = Boolean(
            dictNewest.sBaseHash && sCurrentHash &&
            dictNewest.sBaseHash !== sCurrentHash);
        _fnRenderRecoveryBanner(
            elBanner, elTextarea, dictViewer, dictNewest, bStaleBase);
    }

    async function _fdictPickNewestDraft(dictViewer) {
        var sDraftKey = _fsCurrentDraftKey(dictViewer);
        if (!sDraftKey) return null;
        var dictLocal = PipeleyenDraftManager.fdictGetLocalDraft(
            sDraftKey);
        var dictRemote = await PipeleyenDraftManager.fdictGetRemoteDraft(
            PipeleyenApp.fsGetContainerId(),
            dictViewer.sFilePath, dictViewer.sWorkdir);
        if (!dictLocal && !dictRemote) return null;
        if (!dictLocal) return dictRemote;
        if (!dictRemote) return dictLocal;
        var iLocal = dictLocal.iTimestampMs || 0;
        var iRemote = dictRemote.iTimestampMs || 0;
        return iRemote > iLocal ? dictRemote : dictLocal;
    }

    function _fnRenderRecoveryBanner(
        elBanner, elTextarea, dictViewer, dictDraft, bStaleBase,
    ) {
        elBanner.innerHTML = "";
        elBanner.className = "editor-recovery-banner";
        elBanner.style.padding = "8px 12px";
        elBanner.style.margin = "0 0 4px 0";
        elBanner.style.background =
            "var(--warning-bg, #fff7e6)";
        elBanner.style.border =
            "1px solid var(--warning-border, #f5c46d)";
        elBanner.style.fontSize = "0.9em";
        elBanner.appendChild(_felBuildRecoveryMessage(
            dictDraft, bStaleBase));
        elBanner.appendChild(_felBuildRecoveryActions(
            elBanner, elTextarea, dictViewer, dictDraft));
    }

    function _felBuildRecoveryMessage(dictDraft, bStaleBase) {
        var elMessage = document.createElement("span");
        var sStamp = _fsFormatTimestamp(dictDraft.iTimestampMs || 0);
        var sText = "⚠ Unsaved draft from " + sStamp + " is available.";
        if (bStaleBase) {
            sText = "⚠ File changed on disk since draft was saved. " +
                sText;
        }
        elMessage.textContent = sText;
        elMessage.style.marginRight = "12px";
        return elMessage;
    }

    function _felBuildRecoveryActions(
        elBanner, elTextarea, dictViewer, dictDraft,
    ) {
        var elActions = document.createElement("span");
        elActions.appendChild(_felRecoveryButton(
            "Restore", "btn btn-primary btn-sm",
            function () {
                _fnRestoreDraft(elTextarea, dictViewer, dictDraft);
                elBanner.remove();
            }));
        elActions.appendChild(_felRecoveryButton(
            "View disk", "btn btn-sm",
            function () {
                _fnShowDiskContent(
                    dictViewer.sBaseText || "",
                    dictViewer.sBaseHash || "");
            }));
        elActions.appendChild(_felRecoveryButton(
            "Discard draft", "btn btn-sm",
            function () {
                _fnDiscardDraftFor(dictViewer);
                elBanner.remove();
            }));
        return elActions;
    }

    function _felRecoveryButton(sLabel, sClass, fnClick) {
        var elButton = document.createElement("button");
        elButton.className = sClass;
        elButton.textContent = sLabel;
        elButton.style.marginRight = "6px";
        elButton.addEventListener("click", fnClick);
        return elButton;
    }

    function _fnRestoreDraft(elTextarea, dictViewer, dictDraft) {
        elTextarea.value = dictDraft.sContent;
        _fnOnEditorInput(dictViewer, elTextarea);
        elTextarea.focus();
    }

    function _fsFormatTimestamp(iMs) {
        if (!iMs) return "earlier";
        var iAgeMs = Date.now() - iMs;
        if (iAgeMs < 60000) return "just now";
        if (iAgeMs < 3600000) {
            return Math.round(iAgeMs / 60000) + " min ago";
        }
        if (iAgeMs < 86400000) {
            return Math.round(iAgeMs / 3600000) + " h ago";
        }
        var dictDate = new Date(iMs);
        return dictDate.toLocaleString();
    }

    function fsBuildSaveUrl(sUrl) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sPrefix = "/api/figure/" + sContainerId + "/";
        var sFilePath = sUrl.split(sPrefix)[1] || "";
        var sWorkdir = "";
        if (sFilePath.includes("?")) {
            var sQuery = sFilePath.split("?")[1] || "";
            sFilePath = sFilePath.split("?")[0];
            var dictParams = new URLSearchParams(sQuery);
            sWorkdir = dictParams.get("sWorkdir") || "";
        }
        // The project context file saves through its dedicated route
        // (the generic file route denylists .vaibify/ writes).
        if (decodeURIComponent(sFilePath) === ".vaibify/AGENTS.md") {
            return "/api/workflow/" + sContainerId + "/project-context";
        }
        var sSaveUrl = "/api/file/" + sContainerId + "/" + sFilePath;
        if (sWorkdir) {
            sSaveUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
        }
        return sSaveUrl;
    }

    function fnSaveEditedFile(sUrl, sContent, elViewport, fnOnSuccess) {
        var sSaveUrl = fsBuildSaveUrl(sUrl);
        var dictViewer = _fdictViewerForViewport(elViewport);
        var sBaseHash = dictViewer ? (dictViewer.sBaseHash || "") : "";
        fetch(sSaveUrl, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                sContent: sContent, sBaseHash: sBaseHash,
            }),
        }).then(function (response) {
            if (response.status === 409) {
                return response.json().then(function (dictBody) {
                    _fnHandleSaveConflict(
                        sUrl, sContent, elViewport, dictBody,
                        fnOnSuccess);
                });
            }
            if (!response.ok) {
                return response.text().then(function (sBody) {
                    var sDetail = _fsExtractErrorDetail(sBody);
                    throw new Error(sDetail);
                });
            }
            _fnHandleSaveSuccess(
                sUrl, sContent, elViewport, dictViewer, fnOnSuccess);
        }).catch(function (error) {
            PipeleyenApp.fnShowToast(
                "Save failed: " + error.message, "error");
        });
    }

    function _fsExtractErrorDetail(sBody) {
        try {
            var dictParsed = JSON.parse(sBody);
            if (typeof dictParsed.detail === "string") {
                return dictParsed.detail;
            }
            if (dictParsed.detail && dictParsed.detail.sMessage) {
                return dictParsed.detail.sMessage;
            }
        } catch (error) { /* non-JSON body */ }
        return "unknown error";
    }

    function _fnHandleSaveSuccess(
        sUrl, sContent, elViewport, dictViewer, fnOnSuccess,
    ) {
        if (dictViewer) {
            _fnDiscardDraftFor(dictViewer);
            _fnUpdateIndicator(dictViewer, "saved");
            _fnClearEditingState(dictViewer);
        }
        PipeleyenApp.fnShowToast("File saved", "success");
        fnRevertTestStateForFile(sUrl);
        fnRenderTextWithToolbar(sContent, sUrl, elViewport);
        if (fnOnSuccess) fnOnSuccess();
    }

    function _fnHandleSaveConflict(
        sUrl, sContent, elViewport, dictBody, fnOnSuccess,
    ) {
        var dictDetail = (dictBody && dictBody.detail) || {};
        var sCurrentContent = dictDetail.sCurrentContent || "";
        var sCurrentHash = dictDetail.sCurrentHash || "";
        PipeleyenModals.fnShowChoiceModal(
            "File changed on disk",
            "Another writer modified this file after you started " +
            "editing. Overwriting will discard their changes; " +
            "viewing the diff lets you reconcile by hand.",
            [
                {sLabel: "Overwrite anyway", sStyleClass: "btn-primary",
                 fnCallback: function () {
                     _fnSaveOverwrite(
                         sUrl, sContent, elViewport, fnOnSuccess);
                 }},
                {sLabel: "View disk content",
                 fnCallback: function () {
                     _fnShowDiskContent(sCurrentContent, sCurrentHash);
                 }},
                {sLabel: "Cancel"},
            ]);
    }

    function _fnSaveOverwrite(
        sUrl, sContent, elViewport, fnOnSuccess,
    ) {
        var sSaveUrl = fsBuildSaveUrl(sUrl);
        fetch(sSaveUrl, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({sContent: sContent}),
        }).then(function (response) {
            if (!response.ok) {
                return response.text().then(function (sBody) {
                    throw new Error(_fsExtractErrorDetail(sBody));
                });
            }
            _fnHandleSaveSuccess(
                sUrl, sContent, elViewport,
                _fdictViewerForViewport(elViewport), fnOnSuccess);
        }).catch(function (error) {
            PipeleyenApp.fnShowToast(
                "Overwrite failed: " + error.message, "error");
        });
    }

    function _fnShowDiskContent(sCurrentContent, sCurrentHash) {
        var sBody =
            '<p>Current on-disk content (sha256 ' +
            sCurrentHash.slice(0, 12) + '…):</p>' +
            '<pre style="max-height:300px;overflow:auto;' +
            'background:var(--panel-bg, #f5f5f5);' +
            'padding:8px;font-size:0.85em;">' +
            VaibifyUtilities.fnEscapeHtml(sCurrentContent) +
            '</pre>';
        PipeleyenModals.fnShowInfoModal("File on disk", sBody);
    }

    function fnBindEditorFind(elFind, elTextarea) {
        elFind.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") return;
            var sQuery = elFind.value;
            if (!sQuery) return;
            var iStart = elTextarea.selectionEnd;
            var iFound = elTextarea.value.indexOf(sQuery, iStart);
            if (iFound === -1) {
                iFound = elTextarea.value.indexOf(sQuery);
            }
            if (iFound >= 0) {
                elTextarea.focus();
                elTextarea.setSelectionRange(
                    iFound, iFound + sQuery.length
                );
            }
        });
    }

    function fbIsOutputFile(sUrl) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return false;
        var sBasename = sUrl.split("/").pop().split("?")[0];
        for (var i = 0; i < dictWorkflow.listSteps.length; i++) {
            if (fbPathBelongsToStep(
                sBasename, dictWorkflow.listSteps[i]
            )) {
                return true;
            }
        }
        return false;
    }

    function fnRevertTestStateForFile(sUrl) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var sBasename = sUrl.split("/").pop().split("?")[0];
        for (var i = 0; i < dictWorkflow.listSteps.length; i++) {
            var dictStep = dictWorkflow.listSteps[i];
            if (fbPathBelongsToStep(sBasename, dictStep)) {
                if (dictStep.dictVerification) {
                    dictStep.dictVerification.sUnitTest = "untested";
                    fetch("/api/steps/" + sContainerId + "/" + i, {
                        method: "PUT",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            dictVerification: dictStep.dictVerification
                        }),
                    });
                }
                PipeleyenApp.fnRenderStepList();
                return;
            }
        }
    }

    /* --- Test Failure Outline --- */

    function fnApplyTestFailureOutline(elViewport, sPath) {
        elViewport.classList.remove("viewport-test-failed");
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps) return;
        var sBasename = sPath.split("/").pop();
        for (var i = 0; i < dictWorkflow.listSteps.length; i++) {
            if (fbPathBelongsToStep(sBasename, dictWorkflow.listSteps[i])) {
                var dictVerify = dictWorkflow.listSteps[i].dictVerification;
                if (dictVerify) {
                    var sState = dictVerify.sUnitTest;
                    if (sState === "failed" || sState === "error") {
                        elViewport.classList.add("viewport-test-failed");
                    }
                }
                return;
            }
        }
    }

    function fbPathBelongsToStep(sBasename, dictStep) {
        var listFiles = (dictStep.saPlotFiles || []).concat(
            dictStep.saOutputDataFiles || []
        );
        for (var i = 0; i < listFiles.length; i++) {
            var sStepBase = listFiles[i].split("/").pop();
            if (sStepBase === sBasename) return true;
        }
        return false;
    }

    /* --- Drag and Drop --- */

    function fnBindDropTargets() {
        ["viewportA", "viewportB"].forEach(function (sViewportId) {
            var elViewport = document.getElementById(sViewportId);
            var dictViewer = sViewportId === "viewportA" ?
                dictViewerA : dictViewerB;

            elViewport.addEventListener("dragover", function (event) {
                event.preventDefault();
                elViewport.classList.add("drag-over");
            });
            elViewport.addEventListener("dragleave", function () {
                elViewport.classList.remove("drag-over");
            });
            elViewport.addEventListener("drop", function (event) {
                event.preventDefault();
                elViewport.classList.remove("drag-over");
                var sPath = event.dataTransfer.getData("vaibify/filepath");
                if (!sPath) return;
                var sWorkdir = event.dataTransfer.getData(
                    "vaibify/workdir") || "";
                _fnConfirmIfDirty(
                    dictViewer, "Dropping a new file here",
                    function () {
                        fnNavigateToPath(dictViewer, sPath, sWorkdir);
                    });
            });
        });
    }

    /* --- Init --- */

    document.addEventListener("DOMContentLoaded", function () {
        fnBindDropTargets();

        document.getElementById("btnBackA").addEventListener("click",
            function () {
                _fnConfirmIfDirty(
                    dictViewerA, "Navigating back",
                    function () { fnNavigateBack(dictViewerA); });
            });
        document.getElementById("btnForwardA").addEventListener("click",
            function () {
                _fnConfirmIfDirty(
                    dictViewerA, "Navigating forward",
                    function () { fnNavigateForward(dictViewerA); });
            });
        document.getElementById("btnBackB").addEventListener("click",
            function () {
                _fnConfirmIfDirty(
                    dictViewerB, "Navigating back",
                    function () { fnNavigateBack(dictViewerB); });
            });
        document.getElementById("btnForwardB").addEventListener("click",
            function () {
                _fnConfirmIfDirty(
                    dictViewerB, "Navigating forward",
                    function () { fnNavigateForward(dictViewerB); });
            });

        document.getElementById("btnRefreshA").addEventListener("click",
            function () {
                _fnConfirmIfDirty(
                    dictViewerA, "Refreshing the viewer",
                    function () {
                        var dictEntry = fdictGetCurrentEntry(dictViewerA);
                        if (dictEntry) {
                            fnDisplayInViewport(dictViewerA, dictEntry);
                        }
                    });
            });
        document.getElementById("btnRefreshB").addEventListener("click",
            function () {
                _fnConfirmIfDirty(
                    dictViewerB, "Refreshing the viewer",
                    function () {
                        var dictEntry = fdictGetCurrentEntry(dictViewerB);
                        if (dictEntry) {
                            fnDisplayInViewport(dictViewerB, dictEntry);
                        }
                    });
            });
    });

    function fnDisplayGeneratedTest(sPath, sContent, iStep) {
        _fdictResolveTargetViewer(
            "A", sPath, function (dictViewer) {
                var elViewport = fnGetViewport(dictViewer);
                elViewport.classList.add("viewport-test-generated");
                elViewport.classList.remove("viewport-test-failed");
                fnRenderGeneratedTestEditor(
                    sContent, sPath, elViewport, iStep);
            });
    }

    function fnRenderGeneratedTestEditor(
        sText, sPath, elViewport, iStep
    ) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";

        var elToolbar = document.createElement("div");
        elToolbar.className = "editor-toolbar";
        var sCurrentText = sText;

        var elAccept = document.createElement("button");
        elAccept.className = "btn btn-primary";
        elAccept.textContent = "Accept";
        var elEdit = document.createElement("button");
        elEdit.className = "btn-icon";
        elEdit.title = "Edit";
        elEdit.innerHTML = "&#9998;";
        var elDiscard = document.createElement("button");
        elDiscard.className = "btn";
        elDiscard.textContent = "Discard";

        elToolbar.appendChild(elEdit);
        elToolbar.appendChild(elAccept);
        elToolbar.appendChild(elDiscard);

        var elPre = document.createElement("pre");
        elPre.textContent = sText;
        elViewport.appendChild(elToolbar);
        elViewport.appendChild(elPre);

        elEdit.addEventListener("click", function () {
            _fnEnterTestEditMode(
                sCurrentText, sPath, elViewport, iStep,
                elToolbar, elPre,
                function (sNewText) { sCurrentText = sNewText; }
            );
        });
        elAccept.addEventListener("click", function () {
            fnAcceptAndRunTest(
                sPath, sCurrentText, iStep, elViewport);
        });
        elDiscard.addEventListener("click", function () {
            fnDiscardProposedTest(elViewport);
        });
    }

    function _fnEnterTestEditMode(
        sText, sPath, elViewport, iStep, elToolbar, elPre,
        fnUpdateText
    ) {
        elToolbar.innerHTML = "";
        var elSave = document.createElement("button");
        elSave.className = "btn btn-primary";
        elSave.textContent = "Save";
        var elCancel = document.createElement("button");
        elCancel.className = "btn";
        elCancel.textContent = "Cancel";
        elToolbar.appendChild(elSave);
        elToolbar.appendChild(elCancel);

        var elTextarea = document.createElement("textarea");
        elTextarea.className = "editor-textarea";
        elTextarea.value = sText;
        elPre.replaceWith(elTextarea);

        elSave.addEventListener("click", function () {
            var sNewText = elTextarea.value;
            fnUpdateText(sNewText);
            var elNewPre = document.createElement("pre");
            elNewPre.textContent = sNewText;
            elTextarea.replaceWith(elNewPre);
            fnRenderGeneratedTestEditor(
                sNewText, sPath, elViewport, iStep);
        });
        elCancel.addEventListener("click", function () {
            fnRenderGeneratedTestEditor(
                sText, sPath, elViewport, iStep);
        });
    }

    function fnAcceptAndRunTest(sPath, sContent, iStep, elTestViewport) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var elProgressViewport = elTestViewport ||
            document.getElementById("viewportA");

        elProgressViewport.innerHTML =
            '<div class="test-progress">' +
            '<p>Performing tests...</p></div>';
        elProgressViewport.classList.remove("viewport-test-generated");

        fetch("/api/steps/" + sContainerId + "/" + iStep +
            "/save-and-run-test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                sContent: sContent, sFilePath: sPath,
            }),
        })
        .then(function (response) { return response.json(); })
        .then(function (dictResult) {
            _fnRenderTestAcceptResult(
                dictResult, iStep, elProgressViewport);
        })
        .catch(function (error) {
            elProgressViewport.innerHTML =
                '<p class="test-result-fail">Test execution ' +
                'failed: ' + error.message + '</p>';
        });
    }

    function _fnRenderTestAcceptResult(
        dictResult, iStep, elProgressViewport,
    ) {
        var bPassed = dictResult.bPassed === true;
        var elProgress = elProgressViewport.querySelector(
            ".test-progress");
        if (!elProgress) return;
        elProgress.querySelector("p").textContent += " done.";
        var elResult = document.createElement("p");
        elResult.className = bPassed ?
            "test-result-pass" : "test-result-fail";
        elResult.innerHTML = bPassed ?
            '<img src="/static/favicon.png" ' +
            'class="vaib-verified-badge"> All tests pass!' :
            '<span class="test-fail-glyph">&#9888;</span> ' +
            'Some tests failed.';
        elProgress.appendChild(elResult);
        _fnRecordTestVerification(iStep, bPassed);
        if (dictResult.sOutput) {
            _fnDisplayTestAcceptOutput(
                dictResult.sOutput, elProgressViewport);
        }
    }

    function _fnRecordTestVerification(iStep, bPassed) {
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        if (!dictWorkflow || !dictWorkflow.listSteps[iStep]) return;
        var dictV = dictWorkflow.listSteps[iStep]
            .dictVerification || {};
        dictV.sUnitTest = bPassed ? "passed" : "failed";
        dictWorkflow.listSteps[iStep].dictVerification = dictV;
        if (bPassed) PipeleyenApp.fnClearOutputModified(iStep);
        PipeleyenApp.fnRenderStepList();
    }

    function _fnDisplayTestAcceptOutput(sOutput, elProgressViewport) {
        var sOtherLetter = (elProgressViewport ===
            fnGetViewport(dictViewerA)) ? "B" : "A";
        _fdictResolveTargetViewer(
            sOtherLetter, "test output", function (dictViewer) {
                var elViewport = fnGetViewport(dictViewer);
                elViewport.innerHTML = "";
                var elTestPre = document.createElement("pre");
                elTestPre.textContent = sOutput;
                elViewport.appendChild(elTestPre);
            });
    }

    function fnSaveGeneratedTest(sPath, elViewport, iStep) {
        var elTextarea = elViewport.querySelector("textarea");
        var sContent = elTextarea ?
            elTextarea.value :
            elViewport.querySelector("pre").textContent;
        var sContainerId = PipeleyenApp.fsGetContainerId();
        fetch("/api/file/" + sContainerId + "/" + sPath, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({sContent: sContent}),
        }).then(function (r) {
            if (!r.ok) return r.text().then(function (sBody) {
                var sDetail = "unknown error";
                try {
                    sDetail = JSON.parse(sBody).detail || sDetail;
                } catch (e) { /* non-JSON response */ }
                throw new Error(sDetail);
            });
            elViewport.classList.remove("viewport-test-generated");
            PipeleyenTestManager.fnFinalizeGeneratedTest(iStep);
            PipeleyenApp.fnShowToast("Test saved", "success");
            fnRenderTextWithToolbar(sContent, "", elViewport);
        }).catch(function (error) {
            PipeleyenApp.fnShowToast(
                "Save failed: " + error.message, "error"
            );
        });
    }

    function fnDiscardProposedTest(elViewport) {
        elViewport.classList.remove("viewport-test-generated");
        elViewport.innerHTML =
            '<span class="placeholder">Proposed test discarded</span>';
    }

    function fnCancelGeneratedTestViewer(elViewport, iStep) {
        elViewport.classList.remove("viewport-test-generated");
        elViewport.innerHTML =
            '<span class="placeholder">Test generation cancelled</span>';
        PipeleyenTestManager.fnCancelGeneratedTest(iStep);
    }

    function fnDisplayTestOutput(sOutput, bPassed) {
        _fdictResolveTargetViewer(
            sNextViewer, "test output", function (dictViewer) {
                sNextViewer = dictViewer.sId === "A" ? "B" : "A";
                var elViewport = fnGetViewport(dictViewer);
                elViewport.innerHTML = "";
                var elPre = document.createElement("pre");
                elPre.textContent = sOutput;
                elPre.style.whiteSpace = "pre-wrap";
                elPre.style.padding = "12px";
                elViewport.appendChild(elPre);
            });
    }

    function fnClaimNextViewerForReplacement(sIncomingPath, fnOnReady) {
        _fdictResolveTargetViewer(
            sNextViewer, sIncomingPath, function (dictViewer) {
                sNextViewer = dictViewer.sId === "A" ? "B" : "A";
                fnOnReady(dictViewer.sId);
            });
    }

    function fnShowPlaceholderInNextViewer(sHtml, sIncomingPath) {
        _fdictResolveTargetViewer(
            sNextViewer, sIncomingPath || "placeholder",
            function (dictViewer) {
                sNextViewer = dictViewer.sId === "A" ? "B" : "A";
                fnGetViewport(dictViewer).innerHTML = sHtml;
            });
    }

    return {
        fnLoadStepFigures: fnLoadStepFigures,
        fnDisplayFigureByTemplate: fnDisplayFigureByTemplate,
        fnDisplayFileFromContainer: fnDisplayFileFromContainer,
        fnDisplayInNextViewer: fnDisplayInNextViewer,
        fnDisplayFileInViewer: fnDisplayFileInViewer,
        fsClaimNextViewer: fsClaimNextViewer,
        fnClaimNextViewerForReplacement:
            fnClaimNextViewerForReplacement,
        fnShowPlaceholderInNextViewer: fnShowPlaceholderInNextViewer,
        fnDisplayGeneratedTest: fnDisplayGeneratedTest,
        fnDisplayTestOutput: fnDisplayTestOutput,
        fnCreateZoomToolbar: fnCreateZoomToolbar,
        fnReleaseResources: fnReleaseAllResources,
        fbAnyViewerDirty: fbAnyViewerDirty,
        fbViewerIsDirty: fbViewerIsDirty,
        fbViewerHasOpenEditor: fbViewerHasOpenEditor,
    };
})();
