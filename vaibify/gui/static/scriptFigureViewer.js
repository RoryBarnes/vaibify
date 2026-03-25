/* Vaibify — Dual figure viewer with unified shared history */

const PipeleyenFigureViewer = (function () {
    "use strict";

    var fbIsFigureFile = VaibifyUtilities.fbIsFigureFile;
    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;
    var SET_BINARY_EXTENSIONS = new Set([
        ".npy", ".npz", ".pkl", ".pickle", ".h5", ".hdf5",
        ".fits", ".fit", ".fz", ".dat", ".bin", ".so",
        ".o", ".a", ".pyc", ".pyo", ".whl", ".egg",
        ".gz", ".tar", ".zip", ".bz2", ".xz",
    ]);

    function fbIsBinaryFile(sExtension) {
        return SET_BINARY_EXTENSIONS.has(sExtension);
    }
    var S_OUTPUT_MISSING = '<span class="placeholder output-missing-message">' +
        'Output not available. Run the step to generate.</span>';

    /* Unified shared history: list of {sPath, sWorkdir, iViewCount, iLastViewed} */
    var listHistory = [];
    var iHistoryCounter = 0;

    /* Two viewer states: A and B */
    var dictViewerA = {
        sId: "A",
        listNavHistory: [],
        iNavIndex: -1,
    };
    var dictViewerB = {
        sId: "B",
        listNavHistory: [],
        iNavIndex: -1,
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

    function fnTrimHistory() {
        /* History is permanent — no trimming. */
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
            if (elSelect.value) {
                var sWorkdir = fsGetWorkdirForPath(elSelect.value);
                fnNavigateToPath(dictViewer, elSelect.value, sWorkdir);
            }
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
                fnNavigateToPath(
                    dictViewerA, listFigures[0], dictStep.sDirectory
                );
            }
        });
    }

    function fnDisplayInNextViewer(sPath, sWorkdir) {
        var dictViewer = sNextViewer === "A" ? dictViewerA : dictViewerB;
        fnNavigateToPath(dictViewer, sPath, sWorkdir || "");
        sNextViewer = sNextViewer === "A" ? "B" : "A";
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

        if (fbIsBinaryFile(sExtension)) {
            elViewport.innerHTML =
                '<span class="placeholder" style="color:var(--color-red)">' +
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
        elViewport.innerHTML = "";
        var elImg = document.createElement("img");
        elImg.src = sUrl;
        elImg.alt = "Figure";
        var dScale = 1.0;
        elImg.onload = function () {
            fnRenderImageWithZoom(elImg, sUrl, elViewport, dScale);
        };
        elImg.onerror = function () {
            elViewport.innerHTML =
                S_OUTPUT_MISSING;
        };
        elViewport.appendChild(elImg);
    }

    function fnRenderImageWithZoom(
        elImg, sUrl, elViewport, dScale
    ) {
        var iNativeWidth = elImg.naturalWidth;
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        if (dScale === "fit") {
            dScale = (elViewport.clientWidth - 32) / iNativeWidth;
        }
        var elToolbar = fnCreateZoomToolbar(
            dScale, function (dNewScale) {
                fnRenderImageWithZoom(
                    elImg, sUrl, elViewport, dNewScale
                );
            }
        );
        elImg.style.width = Math.round(iNativeWidth * dScale) + "px";
        elImg.style.height = "auto";
        elViewport.appendChild(elToolbar);
        var elContent = document.createElement("div");
        elContent.style.overflow = "auto";
        elContent.style.flex = "1";
        elContent.style.display = "flex";
        elContent.style.justifyContent = "center";
        elContent.style.padding = "16px";
        elContent.appendChild(elImg);
        elViewport.appendChild(elContent);
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

    function fnCreateZoomToolbar(dCurrentScale, fnOnZoom) {
        var elToolbar = document.createElement("div");
        elToolbar.className = "editor-toolbar zoom-toolbar";
        var elZoomOut = document.createElement("button");
        elZoomOut.className = "btn-icon";
        elZoomOut.title = "Zoom out";
        elZoomOut.textContent = "\u2212";
        var elZoomLevel = document.createElement("span");
        elZoomLevel.className = "zoom-level";
        elZoomLevel.textContent = Math.round(dCurrentScale * 100) + "%";
        var elZoomIn = document.createElement("button");
        elZoomIn.className = "btn-icon";
        elZoomIn.title = "Zoom in";
        elZoomIn.textContent = "+";
        var elFit = document.createElement("button");
        elFit.className = "btn-icon";
        elFit.title = "Fit to window";
        elFit.textContent = "\u2922";
        elZoomOut.addEventListener("click", function () {
            fnOnZoom(Math.max(0.25, dCurrentScale - 0.25));
        });
        elZoomIn.addEventListener("click", function () {
            fnOnZoom(Math.min(4.0, dCurrentScale + 0.25));
        });
        elFit.addEventListener("click", function () {
            fnOnZoom("fit");
        });
        elToolbar.appendChild(elZoomOut);
        elToolbar.appendChild(elZoomLevel);
        elToolbar.appendChild(elZoomIn);
        elToolbar.appendChild(elFit);
        return elToolbar;
    }

    function fnRenderPdfDocument(sUrl, elViewport) {
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
            pdfDoc.getPage(1).then(function (page) {
                fnRenderPdfPage(page, elViewport, 1.0);
            });
        }).catch(function () {
            elViewport.innerHTML =
                S_OUTPUT_MISSING;
        });
    }

    function fnRenderPdfPage(page, elViewport, dDisplayScale) {
        var dNativeViewport = page.getViewport({ scale: 1.0 });
        if (dDisplayScale === "fit") {
            var dFitWidth = elViewport.clientWidth - 32;
            dDisplayScale = dFitWidth / dNativeViewport.width;
        }
        var dRenderScale = dDisplayScale * 2;
        var viewport = page.getViewport({ scale: dRenderScale });
        var elCanvas = document.createElement("canvas");
        elCanvas.width = viewport.width;
        elCanvas.height = viewport.height;
        elCanvas.style.width = viewport.width / 2 + "px";
        elCanvas.style.height = viewport.height / 2 + "px";
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        var elToolbar = fnCreateZoomToolbar(
            dDisplayScale, function (dNewScale) {
                fnRenderPdfPage(page, elViewport, dNewScale);
            }
        );
        elViewport.appendChild(elToolbar);
        var elContent = document.createElement("div");
        elContent.style.overflow = "auto";
        elContent.style.flex = "1";
        elContent.style.display = "flex";
        elContent.style.justifyContent = "center";
        elContent.style.padding = "16px";
        elContent.appendChild(elCanvas);
        elViewport.appendChild(elContent);
        page.render({
            canvasContext: elCanvas.getContext("2d"),
            viewport: viewport,
        });
    }

    function fnRenderText(sUrl, elViewport) {
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

    function fnRenderTextWithToolbar(sText, sUrl, elViewport) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
        var elToolbar = document.createElement("div");
        elToolbar.className = "editor-toolbar";
        var elEditBtn = document.createElement("button");
        elEditBtn.className = "btn-icon";
        elEditBtn.title = "Edit";
        elEditBtn.innerHTML = "&#9998;";
        elEditBtn.addEventListener("click", function () {
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
        });
        elToolbar.appendChild(elEditBtn);
        var elPre = document.createElement("pre");
        elPre.textContent = sText;
        elViewport.appendChild(elToolbar);
        elViewport.appendChild(elPre);
    }

    function fnEnterEditMode(sText, sUrl, elViewport) {
        elViewport.innerHTML = "";
        elViewport.style.flexDirection = "column";
        elViewport.style.alignItems = "stretch";
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

        elToolbar.appendChild(elFind);
        elToolbar.appendChild(elSave);
        elToolbar.appendChild(elCancel);

        var elTextarea = document.createElement("textarea");
        elTextarea.className = "editor-textarea";
        elTextarea.value = sText;
        elTextarea.spellcheck = false;

        elViewport.appendChild(elToolbar);
        elViewport.appendChild(elTextarea);
        elTextarea.focus();

        fnBindEditorFind(elFind, elTextarea);
        elSave.addEventListener("click", function () {
            fnSaveEditedFile(sUrl, elTextarea.value, elViewport);
        });
        elCancel.addEventListener("click", function () {
            fnRenderTextWithToolbar(sText, sUrl, elViewport);
        });
    }

    function fnSaveEditedFile(sUrl, sContent, elViewport) {
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
        var sSaveUrl = "/api/file/" + sContainerId + "/" +
            sFilePath;
        if (sWorkdir) {
            sSaveUrl += "?sWorkdir=" +
                encodeURIComponent(sWorkdir);
        }
        fetch(sSaveUrl, {
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
            PipeleyenApp.fnShowToast("File saved", "success");
            fnRevertTestStateForFile(sUrl);
            fnRenderTextWithToolbar(sContent, sUrl, elViewport);
        }).catch(function (error) {
            PipeleyenApp.fnShowToast(
                "Save failed: " + error.message, "error"
            );
        });
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
            dictStep.saDataFiles || []
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
                var sPath = event.dataTransfer.getData("pipeleyen/filepath");
                if (sPath) {
                    var sWorkdir = event.dataTransfer.getData(
                        "pipeleyen/workdir"
                    ) || "";
                    fnNavigateToPath(dictViewer, sPath, sWorkdir);
                }
            });
        });
    }

    /* --- Init --- */

    document.addEventListener("DOMContentLoaded", function () {
        fnBindDropTargets();

        document.getElementById("btnBackA").addEventListener("click",
            function () { fnNavigateBack(dictViewerA); });
        document.getElementById("btnForwardA").addEventListener("click",
            function () { fnNavigateForward(dictViewerA); });
        document.getElementById("btnBackB").addEventListener("click",
            function () { fnNavigateBack(dictViewerB); });
        document.getElementById("btnForwardB").addEventListener("click",
            function () { fnNavigateForward(dictViewerB); });

        document.getElementById("btnRefreshA").addEventListener("click",
            function () {
                var dictEntry = fdictGetCurrentEntry(dictViewerA);
                if (dictEntry) {
                    fnDisplayInViewport(dictViewerA, dictEntry);
                }
            });
        document.getElementById("btnRefreshB").addEventListener("click",
            function () {
                var dictEntry = fdictGetCurrentEntry(dictViewerB);
                if (dictEntry) {
                    fnDisplayInViewport(dictViewerB, dictEntry);
                }
            });
    });

    function fnDisplayGeneratedTest(sPath, sContent, iStep) {
        var elViewport = document.getElementById("viewportA");
        elViewport.classList.add("viewport-test-generated");
        elViewport.classList.remove("viewport-test-failed");
        fnRenderGeneratedTestEditor(
            sContent, sPath, elViewport, iStep
        );
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
            fnAcceptAndRunTest(sPath, sCurrentText, iStep);
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

    function fnAcceptAndRunTest(sPath, sContent, iStep) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var elViewportB = document.getElementById("viewportB");
        var elViewportA = document.getElementById("viewportA");

        elViewportA.innerHTML =
            '<div class="test-progress">' +
            '<p>Performing tests...</p></div>';
        elViewportA.classList.remove("viewport-test-generated");

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
            var bPassed = dictResult.bPassed === true;
            var elProgress = elViewportA.querySelector(
                ".test-progress");
            if (!elProgress) return;
            elProgress.querySelector("p").textContent +=
                " done.";
            var elResult = document.createElement("p");
            elResult.className = bPassed ?
                "test-result-pass" : "test-result-fail";
            elResult.innerHTML = bPassed ?
                '<img src="/static/favicon.png" ' +
                'class="vaib-verified-badge"> All tests pass!' :
                '<span class="test-fail-x">&#10007;</span> ' +
                'Some tests failed.';
            elProgress.appendChild(elResult);
            var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
            if (dictWorkflow && dictWorkflow.listSteps[iStep]) {
                var dictV = dictWorkflow.listSteps[iStep]
                    .dictVerification || {};
                dictV.sUnitTest = bPassed ? "passed" : "failed";
                dictWorkflow.listSteps[iStep].dictVerification =
                    dictV;
                if (bPassed) {
                    PipeleyenApp.fnClearOutputModified(iStep);
                }
                PipeleyenApp.fnRenderStepList();
            }
            if (dictResult.sOutput) {
                elViewportB.innerHTML = "";
                var elTestPre = document.createElement("pre");
                elTestPre.textContent = dictResult.sOutput;
                elViewportB.appendChild(elTestPre);
            }
        })
        .catch(function (error) {
            elViewportA.innerHTML =
                '<p class="test-result-fail">Test execution ' +
                'failed: ' + error.message + '</p>';
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
            PipeleyenApp.fnFinalizeGeneratedTest(iStep);
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
        PipeleyenApp.fnCancelGeneratedTest(iStep);
    }

    function fnDisplayTestOutput(sOutput, bPassed) {
        var dictViewer = sNextViewer === "A" ?
            dictViewerA : dictViewerB;
        sNextViewer = sNextViewer === "A" ? "B" : "A";
        var elViewport = fnGetViewport(dictViewer);
        elViewport.innerHTML = "";
        var elPre = document.createElement("pre");
        elPre.textContent = sOutput;
        elPre.style.whiteSpace = "pre-wrap";
        elPre.style.padding = "12px";
        elViewport.appendChild(elPre);
    }

    return {
        fnLoadStepFigures: fnLoadStepFigures,
        fnDisplayFigureByTemplate: fnDisplayFigureByTemplate,
        fnDisplayFileFromContainer: fnDisplayFileFromContainer,
        fnDisplayInNextViewer: fnDisplayInNextViewer,
        fnDisplayGeneratedTest: fnDisplayGeneratedTest,
        fnDisplayTestOutput: fnDisplayTestOutput,
    };
})();
