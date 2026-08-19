/* Vaibify — File Browser (extracted from scriptApplication.js) */

var VaibifyFiles = (function () {
    "use strict";

    /* The initial value only; the real root arrives with the connect
       handshake, because a host project's files live in the directory
       the researcher registered rather than in a container. */
    var sCurrentPath = "/workspace";
    /* What the list currently shows, so a background refresh can leave
       an unchanged directory untouched -- see fnRefreshCurrentDirectory. */
    var _sRenderedFingerprint = "";
    var _bRefreshInFlight = false;

    async function fnLoadDirectory(sPath) {
        sCurrentPath = sPath || VaibifyApp.fsGetWorkspaceRoot();
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;

        fnRenderBreadcrumb(sCurrentPath);
        _fnUpdateConvertButtonVisibility();

        try {
            var listEntries = await VaibifyApi.fdictGet(
                "/api/files/" + sContainerId + sCurrentPath
            );
            fnRenderFileList(listEntries);
            _sRenderedFingerprint = _fsListingFingerprint(listEntries);
        } catch (error) {
            document.getElementById("listFiles").innerHTML =
                '<p style="padding:14px;color:var(--text-muted)">Error loading directory</p>';
            _sRenderedFingerprint = "";
        }
    }

    async function fnRefreshCurrentDirectory() {
        /* The poll tick: re-fetch the CURRENT directory and re-render
           ONLY when its contents changed, so a file an agent or a
           pipeline step writes appears without the researcher
           navigating, while a tick that changed nothing never disturbs
           scroll position or a hover. Runs only while the Files panel
           is the visible one -- a hidden panel is reloaded from its
           root when its tab is next opened, so polling it would fetch
           for no one. This is the sandbox's file refresh: host mode
           lands in a no-workflow view whose only other pollers watch
           git and the container, never the directory. */
        if (_bRefreshInFlight) return;
        var sContainerId = VaibifyApp.fsGetContainerId();
        var elPanel = document.getElementById("panelFiles");
        if (!sContainerId || !elPanel ||
            !elPanel.classList.contains("active")) {
            return;
        }
        _bRefreshInFlight = true;
        try {
            var listEntries = await VaibifyApi.fdictGet(
                "/api/files/" + sContainerId + sCurrentPath
            );
            var sFingerprint = _fsListingFingerprint(listEntries);
            if (sFingerprint !== _sRenderedFingerprint) {
                fnRenderFileList(listEntries);
                _sRenderedFingerprint = sFingerprint;
            }
        } catch (error) {
            /* A transient poll failure keeps the last good listing --
               navigation (fnLoadDirectory) still surfaces a hard error. */
        } finally {
            _bRefreshInFlight = false;
        }
    }

    function _fsListingFingerprint(listEntries) {
        /* Name plus kind, order-independent: the list renders exactly
           those, so this changes iff the rendered output would. A file
           whose bytes change but whose name does not is invisible here,
           correctly -- the browser shows names, not sizes. */
        return listEntries.map(function (entry) {
            return (entry.bIsDirectory ? "d:" : "f:") + entry.sName;
        }).sort().join("\n");
    }

    function _fnUpdateConvertButtonVisibility() {
        /* The "Convert to Project" button is for a host SANDBOX only.
           A containerized project is already a Project (host-only bar),
           and a host project that has ALREADY been promoted
           (bIsProject) is a Project too -- offering it "Convert to
           Project" again would misname its own status. Both signals are
           the server's answer, read through the manager and VaibifyApp. */
        var elBar = document.getElementById("fileConvertToProjectBar");
        if (!elBar) return;
        var bHostSandbox =
            VaibifyApp.fsGetProjectMode() === "host" &&
            !VaibifyContainerManager.fbGetSelectedContainerIsProject();
        elBar.style.display = bHostSandbox ? "" : "none";
    }

    var _bConvertButtonBound = false;

    function fnBindConvertButton() {
        if (_bConvertButtonBound) return;
        var elButton = document.getElementById("btnConvertToProject");
        if (!elButton) return;
        _bConvertButtonBound = true;
        elButton.addEventListener("click", _fnHandleConvertClick);
    }

    function _fnHandleConvertClick() {
        /* A host project's resource id IS its registry name, so
           fsGetContainerId returns the sHostName the convert route
           keys on; the directory comes from the open project. */
        var sHostName = VaibifyApp.fsGetContainerId();
        var sDirectory =
            VaibifyContainerManager.fsGetSelectedContainerDirectory();
        VaibifyWorkflowManager.fnOpenConvertWizard(sHostName, sDirectory);
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
            // Path components come from container filenames, which may
            // contain HTML metacharacters (a hostile filename in a
            // cloned repo or extracted archive). Escape before it enters
            // innerHTML — the sibling directory browser does the same.
            sHtml += '<span class="crumb" data-path="' +
                VaibifyUtilities.fnEscapeHtml(sPathCopy) + '">' +
                VaibifyUtilities.fnEscapeHtml(sPart) + "</span>";
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
            // entry.sName and entry.sPath are raw container filenames;
            // escape both before they enter innerHTML so a hostile
            // filename cannot inject markup (e.g. a <base> tag that
            // reroutes the dashboard's API calls).
            return (
                '<div class="file-item" data-path="' +
                VaibifyUtilities.fnEscapeHtml(entry.sPath) +
                '" data-is-dir="' + (entry.bIsDirectory ? "true" : "false") +
                '" draggable="true">' +
                '<span class="file-icon ' + sIconClass + '">' +
                sIcon + "</span>" +
                '<span class="file-name">' +
                VaibifyUtilities.fnEscapeHtml(entry.sName) + "</span>" +
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
                VaibifyFigureViewer.fnDisplayInNextViewer(
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
            /* Right-click meant "copy this to the backend's host",
               which in host mode is a self-copy and through a tunnel
               reaches the wrong machine entirely. The gesture now does
               the thing a researcher almost always means: bring the
               file to the computer they are sitting at. The
               execution-host copy stays available from the sync
               panel's menu, where it is named for what it does. */
            VaibifyFilePull.fnDownloadToThisComputer(
                elItem.dataset.path);
        });
    }

    function fnBindDropZone() {
        /* The list stays a drop target -- it always was, and a
           researcher who has learned to drop onto it must not find that
           it stopped working. The labelled zone is bound BECAUSE it is
           labelled: it says "drop files here", and a target that reads
           as one without being one is worse than no label at all. */
        var listTargets = [
            document.getElementById("listFiles"),
            document.getElementById("fileUploadDropZone"),
        ];
        listTargets.forEach(function (elTarget) {
            if (elTarget) fnBindDropEvents(elTarget);
        });
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
        var sContainerId = VaibifyApp.fsGetContainerId();
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
            VaibifyApp.fnShowConfirmModal(
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
    document.addEventListener("DOMContentLoaded", fnBindConvertButton);

    return {
        fnLoadDirectory: fnLoadDirectory,
        fnRefreshCurrentDirectory: fnRefreshCurrentDirectory,
    };
})();
