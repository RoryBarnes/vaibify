/* Vaibify — Pull-to-host modal (extracted from scriptApplication.js) */

var PipeleyenFilePull = (function () {
    "use strict";

    function fnPromptPullToHost(sContainerPath) {
        var sFilename = sContainerPath.split("/").pop();
        var elExisting = document.getElementById("modalPull");
        if (elExisting) elExisting.remove();
        var elModal = document.createElement("div");
        elModal.id = "modalPull";
        elModal.className = "modal-overlay";
        elModal.style.display = "flex";
        elModal.innerHTML = fsRenderPullModalHtml(sFilename);
        document.body.appendChild(elModal);
        fnBindPullModalEvents(elModal, sContainerPath);
        fnLoadPullDirectory(elModal, null);
    }

    function fsRenderPullModalHtml(sFilename) {
        return '<div class="modal" style="width:480px">' +
            '<h2>Pull to host</h2>' +
            '<p style="margin-bottom:8px;color:var(--text-muted)">' +
            VaibifyUtilities.fnEscapeHtml(sFilename) + '</p>' +
            '<div class="pull-breadcrumb" ' +
            'style="margin-bottom:6px;font-size:12px;' +
            'color:var(--text-secondary)"></div>' +
            '<div class="pull-dir-list" style="max-height:240px;' +
            'overflow-y:auto;border:1px solid var(--border-color);' +
            'border-radius:var(--border-radius);' +
            'margin-bottom:12px"></div>' +
            '<div class="modal-actions">' +
            '<button class="btn" id="btnPullCancel">Cancel</button>' +
            '<button class="btn btn-primary" ' +
            'id="btnPullConfirm">Pull Here</button>' +
            '</div></div>';
    }

    function fnBindPullModalEvents(
        elModal, sContainerPath
    ) {
        var sFilename = sContainerPath.split("/").pop();
        document.getElementById("btnPullCancel")
            .addEventListener("click", function () {
                elModal.remove();
            });
        document.getElementById("btnPullConfirm")
            .addEventListener("click", function () {
                var sDirectory = elModal.dataset.currentPath;
                elModal.remove();
                fnExecutePullToHost(
                    sContainerPath, sDirectory + "/" + sFilename);
            });
    }

    async function fnLoadPullDirectory(elModal, sPath) {
        var sUrl = "/api/host-directories";
        if (sPath) sUrl += "?sPath=" + encodeURIComponent(sPath);
        try {
            var dictResult = await VaibifyApi.fdictGet(sUrl);
            fnRenderPullDirectoryList(elModal, dictResult);
        } catch (error) {
            PipeleyenApp.fnShowToast("Cannot browse host", "error");
        }
    }

    function fnRenderPullDirectoryList(elModal, dictResult) {
        var sCurrentPath = dictResult.sCurrentPath;
        elModal.dataset.currentPath = sCurrentPath;
        fnRenderPullBreadcrumb(elModal, sCurrentPath);
        var elList = elModal.querySelector(".pull-dir-list");
        var sHtml = fsRenderPullParentEntry(sCurrentPath);
        dictResult.listEntries.forEach(function (entry) {
            sHtml += fsRenderPullDirectoryEntry(entry);
        });
        elList.innerHTML = sHtml;
        fnBindPullDirectoryClicks(elModal, elList);
    }

    function fsRenderPullParentEntry(sCurrentPath) {
        var sParent = sCurrentPath.replace(/\/[^/]+$/, "");
        if (!sParent || sParent === sCurrentPath) return "";
        return '<div class="pull-dir-entry" data-path="' +
            VaibifyUtilities.fnEscapeHtml(sParent) + '">' +
            '<span class="file-icon dir">&#128193;</span> ' +
            '..</div>';
    }

    function fsRenderPullDirectoryEntry(entry) {
        return '<div class="pull-dir-entry" data-path="' +
            VaibifyUtilities.fnEscapeHtml(entry.sPath) + '">' +
            '<span class="file-icon dir">&#128193;</span> ' +
            VaibifyUtilities.fnEscapeHtml(entry.sName) + '</div>';
    }

    function fnBindPullDirectoryClicks(elModal, elList) {
        elList.querySelectorAll(".pull-dir-entry")
            .forEach(function (el) {
                el.addEventListener("click", function () {
                    fnLoadPullDirectory(elModal, el.dataset.path);
                });
            });
    }

    function fnRenderPullBreadcrumb(elModal, sPath) {
        var elBreadcrumb = elModal.querySelector(
            ".pull-breadcrumb");
        var listParts = sPath.split("/").filter(Boolean);
        var sHtml = "";
        var sBuiltPath = "";
        listParts.forEach(function (sPart, iIndex) {
            sBuiltPath += "/" + sPart;
            if (iIndex > 0) sHtml += " / ";
            sHtml += '<span class="pull-crumb" data-path="' +
                VaibifyUtilities.fnEscapeHtml(sBuiltPath) + '" style="cursor:' +
                'pointer;color:var(--highlight-color)">' +
                VaibifyUtilities.fnEscapeHtml(sPart) + '</span>';
        });
        elBreadcrumb.innerHTML = sHtml;
        elBreadcrumb.querySelectorAll(".pull-crumb")
            .forEach(function (el) {
                el.addEventListener("click", function () {
                    fnLoadPullDirectory(elModal, el.dataset.path);
                });
            });
    }

    async function fnExecutePullToHost(
        sContainerPath, sHostDestination
    ) {
        PipeleyenApp.fnShowToast(
            "Pulling " + sContainerPath + "...", "success");
        try {
            var sContainerId = PipeleyenApp.fsGetContainerId();
            var dictResult = await VaibifyApi.fdictPost(
                "/api/files/" + sContainerId + "/pull",
                {
                    sContainerPath: sContainerPath,
                    sHostDestination: sHostDestination,
                }
            );
            PipeleyenApp.fnShowToast(
                "Pulled to " + dictResult.sHostPath, "success");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Pull failed: " + error.message, "error");
        }
    }

    return {
        fnPromptPullToHost: fnPromptPullToHost,
    };
})();
