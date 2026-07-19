/* Vaibify — project context file (Replay axis) creation + import.

   The context file (.vaibify/AGENTS.md) is the researcher's standing
   instructions to the in-container agent. When it exists, the
   Project block renders it as an ordinary tracked-file row (view /
   edit through the file viewer, saved via the dedicated
   project-context route). This module owns the MISSING state — the
   three creation paths — and the host-file import picker:

     - Start from template   POST .../project-context/template
     - Import from the host  POST .../project-context/import {sHostPath}
     - Adopt repo-root file  POST .../project-context/import
                             {bAdoptRepoRoot, sRootBasename}

   The import endpoints are researcher-only (excluded from the agent
   catalog); the agent's path is the update-project-context action,
   surfaced here as a hint.

   Exposes:
     - VaibifyProjectContext.fsRenderMissingContextRow(dictDetail)
     - VaibifyProjectContext.fnGenerateTemplate()
     - VaibifyProjectContext.fnAdoptRepoRoot()
     - VaibifyProjectContext.fnOpenImportPicker()
     - VaibifyProjectContext.fnCloseImportPicker()
*/

var VaibifyProjectContext = (function () {
    "use strict";

    var fnEscapeHtml = VaibifyUtilities.fnEscapeHtml;

    function _felGet(sId) {
        return document.getElementById(sId);
    }

    function fsRenderMissingContextRow(dictDetail) {
        var sAdoptButton = dictDetail.bRepoRootAgentsFileDetected === true
            ? '<button type="button" class="btn btn-small ' +
              'btn-context-adopt">Adopt repo-root context file' +
              '</button> '
            : "";
        return '<div class="requirement-row-status">No project ' +
            'context file (.vaibify/AGENTS.md) yet — the standing ' +
            'instructions the in-container agent reads.</div>' +
            '<div class="requirement-row-actions">' +
            '<button type="button" class="btn btn-small ' +
            'btn-context-template">Start from template</button> ' +
            '<button type="button" class="btn btn-small ' +
            'btn-context-import-open">Import from this computer…' +
            '</button> ' + sAdoptButton + '</div>' +
            '<div class="requirement-row-howto">You can also ask ' +
            'the in-container agent to draft it: it reads the ' +
            'repository and writes the file through the ' +
            'update-project-context action.</div>';
    }

    async function fnGenerateTemplate() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            await VaibifyApi.fdictPost(
                "/api/workflow/" + encodeURIComponent(sContainerId) +
                    "/project-context/template",
                {},
            );
            PipeleyenApp.fnShowToast(
                "Project context template written — click the file " +
                    "row to review and edit it.",
                "success",
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Template failed: " + _fsDetail(error), "error");
        }
    }

    async function fnAdoptRepoRoot() {
        // The envelope flag says a root context file exists but not
        // which name; try the two adoptable basenames in order.
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        var listBasenames = ["CLAUDE.md", "AGENTS.md"];
        for (var i = 0; i < listBasenames.length; i++) {
            try {
                await _fnPostImport(sContainerId, {
                    bAdoptRepoRoot: true,
                    sRootBasename: listBasenames[i],
                });
                PipeleyenApp.fnShowToast(
                    "Adopted " + listBasenames[i] + " as the project " +
                        "context (the root name is now a symlink).",
                    "success",
                );
                return;
            } catch (error) {
                if (i === listBasenames.length - 1) {
                    PipeleyenApp.fnShowToast(
                        "Adopt failed: " + _fsDetail(error), "error");
                }
            }
        }
    }

    async function _fnPostImport(sContainerId, dictBody) {
        return VaibifyApi.fdictPost(
            "/api/workflow/" + encodeURIComponent(sContainerId) +
                "/project-context/import",
            dictBody,
        );
    }

    /* --- Host-file import picker --- */

    function fnOpenImportPicker() {
        _felGet("modalContextImport").style.display = "flex";
        _fnLoadDirectory("");
    }

    function fnCloseImportPicker() {
        _felGet("modalContextImport").style.display = "none";
    }

    async function _fnLoadDirectory(sPath) {
        var sUrl = "/api/host-directories?bIncludeFiles=true";
        if (sPath) {
            sUrl += "&sPath=" + encodeURIComponent(sPath);
        }
        try {
            var dictResult = await VaibifyApi.fdictGet(sUrl);
            _fnRenderEntries(dictResult);
        } catch (error) {
            _felGet("contextImportEntries").innerHTML =
                '<div class="muted-text">' +
                fnEscapeHtml(_fsDetail(error)) + '</div>';
        }
    }

    function _fnRenderEntries(dictResult) {
        _felGet("contextImportPath").textContent =
            dictResult.sCurrentPath || "";
        var elList = _felGet("contextImportEntries");
        elList.innerHTML = "";
        (dictResult.listEntries || []).forEach(function (dictEntry) {
            elList.appendChild(_felBuildEntry(dictEntry));
        });
    }

    function _felBuildEntry(dictEntry) {
        var elRow = document.createElement("div");
        elRow.className = "context-import-entry";
        elRow.textContent = (dictEntry.bIsDirectory ? "📁 " : "📄 ") +
            dictEntry.sName;
        elRow.addEventListener("click", function () {
            if (dictEntry.bIsDirectory) {
                _fnLoadDirectory(dictEntry.sPath);
            } else {
                _fnImportHostFile(dictEntry.sPath);
            }
        });
        return elRow;
    }

    async function _fnImportHostFile(sHostPath, bOverwrite) {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            await _fnPostImport(sContainerId, {
                sHostPath: sHostPath,
                bOverwrite: bOverwrite === true,
            });
            fnCloseImportPicker();
            PipeleyenApp.fnShowToast(
                "Imported the project context file.", "success");
        } catch (error) {
            if (String(_fsDetail(error)).indexOf("bOverwrite") !== -1) {
                PipeleyenApp.fnShowConfirmModal(
                    "Replace project context",
                    "A project context file already exists. Replace " +
                        "it with the imported file?",
                    function () {
                        _fnImportHostFile(sHostPath, true);
                    }
                );
                return;
            }
            PipeleyenApp.fnShowToast(
                "Import failed: " + _fsDetail(error), "error");
        }
    }

    function _fsDetail(error) {
        if (!error) return "unknown error";
        if (typeof error === "string") return error;
        return error.message || "unknown error";
    }

    return {
        fsRenderMissingContextRow: fsRenderMissingContextRow,
        fnGenerateTemplate: fnGenerateTemplate,
        fnAdoptRepoRoot: fnAdoptRepoRoot,
        fnOpenImportPicker: fnOpenImportPicker,
        fnCloseImportPicker: fnCloseImportPicker,
    };
})();
