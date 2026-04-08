/* Vaibify — File Browser (extracted from scriptApplication.js) */

var PipeleyenFiles = (function () {
    "use strict";

    var sCurrentPath = "/workspace";

    async function fnLoadDirectory(sPath) {
        sCurrentPath = sPath || "/workspace";
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;

        fnRenderBreadcrumb(sCurrentPath);

        try {
            var listEntries = await VaibifyApi.fdictGet(
                "/api/files/" + sContainerId + sCurrentPath
            );
            fnRenderFileList(listEntries);
        } catch (error) {
            document.getElementById("listFiles").innerHTML =
                '<p style="padding:14px;color:var(--text-muted)">Error loading directory</p>';
        }
    }

    function fnRenderBreadcrumb(sPath) {
        var elBreadcrumb = document.getElementById("fileBreadcrumb");
        var listParts = sPath.split("/").filter(Boolean);
        var sHtml = "";
        var sBuiltPath = "";
        listParts.forEach(function (sPart, iIndex) {
            sBuiltPath += "/" + sPart;
            var sPathCopy = sBuiltPath;
            if (iIndex > 0) sHtml += " / ";
            sHtml += '<span class="crumb" data-path="' +
                sPathCopy + '">' + sPart + "</span>";
        });
        elBreadcrumb.innerHTML = sHtml;
        elBreadcrumb.querySelectorAll(".crumb").forEach(function (el) {
            el.addEventListener("click", function () {
                fnLoadDirectory(el.dataset.path);
            });
        });
    }

    function fnRenderFileList(listEntries) {
        var elList = document.getElementById("listFiles");
        if (listEntries.length === 0) {
            elList.innerHTML =
                '<p style="padding:14px;color:var(--text-muted)">Empty directory</p>';
            return;
        }
        listEntries.sort(function (a, b) {
            if (a.bIsDirectory !== b.bIsDirectory) {
                return a.bIsDirectory ? -1 : 1;
            }
            return a.sName.localeCompare(b.sName);
        });

        elList.innerHTML = listEntries.map(function (entry) {
            var sIconClass = entry.bIsDirectory ? "dir" : "";
            var sIcon = entry.bIsDirectory ? "&#128193;" : "&#128196;";
            var sLower = entry.sName.toLowerCase();
            if (sLower.endsWith(".pdf") || sLower.endsWith(".png") ||
                sLower.endsWith(".jpg") || sLower.endsWith(".svg")) {
                sIconClass = "figure";
            }
            return (
                '<div class="file-item" data-path="' + entry.sPath +
                '" data-is-dir="' + entry.bIsDirectory +
                '" draggable="true">' +
                '<span class="file-icon ' + sIconClass + '">' +
                sIcon + "</span>" +
                '<span class="file-name">' + entry.sName + "</span>" +
                "</div>"
            );
        }).join("");

        fnBindFileItemDelegation(elList);
    }

    var _bFileItemDelegationBound = false;

    function fnBindFileItemDelegation(elList) {
        if (_bFileItemDelegationBound) return;
        _bFileItemDelegationBound = true;
        elList.addEventListener("click", function (event) {
            var elItem = event.target.closest(".file-item");
            if (!elItem) return;
            if (elItem.dataset.isDir === "true") {
                fnLoadDirectory(elItem.dataset.path);
            } else {
                PipeleyenFigureViewer.fnDisplayInNextViewer(
                    elItem.dataset.path
                );
            }
        });
        elList.addEventListener("dragstart", function (event) {
            var elItem = event.target.closest(".file-item");
            if (!elItem) return;
            event.dataTransfer.setData(
                "vaibify/filepath", elItem.dataset.path
            );
        });
        elList.addEventListener("contextmenu", function (event) {
            var elItem = event.target.closest(".file-item");
            if (!elItem || elItem.dataset.isDir === "true") return;
            event.preventDefault();
            PipeleyenApp.fnPromptPullToHost(elItem.dataset.path);
        });
    }

    function fnBindDropZone() {
        var elList = document.getElementById("listFiles");
        if (!elList) return;
        fnBindDropEvents(elList);
    }

    function fnBindDropEvents(elTarget) {
        elTarget.addEventListener("dragover", function (event) {
            if (!fbHasHostFiles(event)) return;
            event.preventDefault();
            elTarget.classList.add("drag-over");
        });
        elTarget.addEventListener("dragleave", function () {
            elTarget.classList.remove("drag-over");
        });
        elTarget.addEventListener("drop", function (event) {
            elTarget.classList.remove("drag-over");
            if (!fbHasHostFiles(event)) return;
            event.preventDefault();
            fnUploadDroppedFiles(event.dataTransfer.files);
        });
    }

    function fbHasHostFiles(event) {
        var listTypes = event.dataTransfer.types || [];
        for (var i = 0; i < listTypes.length; i++) {
            if (listTypes[i] === "Files") return true;
        }
        return false;
    }

    async function fnUploadDroppedFiles(fileList) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId || fileList.length === 0) return;
        for (var i = 0; i < fileList.length; i++) {
            await fnUploadOneFile(sContainerId, fileList[i]);
        }
        fnLoadDirectory(sCurrentPath);
    }

    async function fnUploadOneFile(sContainerId, file) {
        var sContentBase64 = await fsEncodeFileBase64(file);
        try {
            await VaibifyApi.fdictPost(
                "/api/files/" + sContainerId + "/upload",
                {
                    sFilename: file.name,
                    sDestination: sCurrentPath,
                    sContentBase64: sContentBase64,
                }
            );
        } catch (error) {
            PipeleyenApp.fnShowConfirmModal(
                "Upload Error",
                "Failed to upload " + file.name,
                function () {}
            );
        }
    }

    function fsEncodeFileBase64(file) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () {
                var sEncoded = reader.result.split(",")[1] || "";
                resolve(sEncoded);
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    document.addEventListener("DOMContentLoaded", fnBindDropZone);

    return {
        fnLoadDirectory: fnLoadDirectory,
    };
})();
