/* Pipeleyen — Dual figure viewer with unified shared history */

const PipeleyenFigureViewer = (function () {
    "use strict";

    var fbIsFigureFile = VaibifyUtilities.fbIsFigureFile;
    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

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
        var sUrl = "/api/figure/" + sContainerId + "/" + sPath;
        if (sWorkdir) {
            sUrl += "?sWorkdir=" + encodeURIComponent(sWorkdir);
        }
        var elViewport = fnGetViewport(dictViewer);
        var iDot = sPath.lastIndexOf(".");
        var sExtension = iDot >= 0 ?
            sPath.substring(iDot).toLowerCase() : "";

        if (sExtension === ".pdf") {
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
        elImg.onerror = function () {
            elViewport.innerHTML =
                '<span class="placeholder output-missing-message">' +
                'Output not available. Run the step to generate.' +
                '</span>';
        };
        elViewport.appendChild(elImg);
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
                '<span class="placeholder output-missing-message">' +
                'Output not available. Run the step to generate.' +
                '</span>';
        });
    }

    function fnRenderPdfDocument(sUrl, elViewport) {
        if (typeof pdfjsLib === "undefined") {
            elViewport.innerHTML =
                '<span class="placeholder">PDF.js not loaded</span>';
            return;
        }
        pdfjsLib.GlobalWorkerOptions.workerSrc =
            "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
        pdfjsLib.getDocument(sUrl).promise.then(function (pdfDoc) {
            pdfDoc.getPage(1).then(function (page) {
                var dScale = 2.0;
                var viewport = page.getViewport({ scale: dScale });
                var elCanvas = document.createElement("canvas");
                elCanvas.width = viewport.width;
                elCanvas.height = viewport.height;
                elCanvas.style.width = viewport.width / dScale + "px";
                elCanvas.style.height = viewport.height / dScale + "px";
                elViewport.innerHTML = "";
                elViewport.appendChild(elCanvas);
                page.render({
                    canvasContext: elCanvas.getContext("2d"),
                    viewport: viewport,
                });
            });
        }).catch(function (error) {
            elViewport.innerHTML =
                '<span class="placeholder output-missing-message">' +
                'Output not available. Run the step to generate.' +
                '</span>';
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
        if (sFilePath.includes("?")) {
            sFilePath = sFilePath.split("?")[0];
        }
        fetch("/api/file/" + sContainerId + "/" + sFilePath, {
            method: "PUT",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({sContent: sContent}),
        }).then(function (r) {
            if (!r.ok) throw new Error("Save failed");
            PipeleyenApp.fnShowToast("File saved", "success");
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

    return {
        fnLoadStepFigures: fnLoadStepFigures,
        fnDisplayFigureByTemplate: fnDisplayFigureByTemplate,
        fnDisplayFileFromContainer: fnDisplayFileFromContainer,
        fnDisplayInNextViewer: fnDisplayInNextViewer,
    };
})();
