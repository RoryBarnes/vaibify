/* Vaibify — Step CRUD modal forms */

const VaibifyStepEditor = (function () {
    "use strict";

    var _I_STEP_COUNT_MAX = 500;
    var _S_HUNDRED_STEP_WARN_HTML = "Your project has reached 100 steps. Projects this large take noticeably longer to verify and reproduce; consider whether some steps can be combined or moved to a sibling project in the same repository.";

    let sMode = "create";  /* "create", "edit", or "insert" */
    let iEditIndex = -1;
    let iInsertPosition = -1;
    // The workflow fingerprint captured when the EDIT modal opened. The
    // save submits this exact value so a concurrent agent edit (which
    // the background poll may have already loaded, advancing the tracked
    // fingerprint) cannot let the modal's stale fields pass the backend
    // compare-and-swap.
    let sEditBaseFingerprint = null;

    function fnOpenCreateModal() {
        sMode = "create";
        iEditIndex = -1;
        iInsertPosition = -1;
        fnClearForm();
        document.getElementById("modalTitle").textContent = "New Step";
        fnShowModal();
    }

    function fnOpenInsertModal(iPosition) {
        sMode = "insert";
        iEditIndex = -1;
        iInsertPosition = iPosition;
        fnClearForm();
        document.getElementById("modalTitle").textContent =
            "Insert Step at Position " + (iPosition + 1);
        fnShowModal();
    }

    function fnOpenEditModal(iIndex) {
        sMode = "edit";
        iEditIndex = iIndex;
        iInsertPosition = -1;
        // Snapshot the base version this form is populated from. If the
        // step changes out from under the modal, the save carries this
        // stale fingerprint and the backend refuses it (409) instead of
        // overwriting the concurrent edit.
        sEditBaseFingerprint = VaibifyApp.fsGetWorkflowFingerprint();

        const dictWorkflow = VaibifyApp.fdictGetWorkflow();
        const dictStep = dictWorkflow.listSteps[iIndex];

        // Renames go through the cascade (right-click → Rename) so
        // the directory, marker, and manifest follow the name; the
        // generic edit path must not offer a divergent rename.
        var elName = document.getElementById("inputStepName");
        elName.value = dictStep.sName || "";
        elName.readOnly = true;
        elName.title = "Rename via right-click → Rename, so the " +
            "directory and records follow the name";
        document.getElementById("inputStepDirectory").value =
            dictStep.sDirectory || "";
        document.getElementById("inputInteractive").checked =
            VaibifyUtilities.fbStepIsInteractive(dictStep);
        document.getElementById("inputPlotOnly").checked =
            dictStep.bPlotOnly !== false;
        document.getElementById("inputSetupCommands").value =
            (dictStep.saDataCommands || []).join("\n");
        document.getElementById("inputOutputDataFiles").value =
            (dictStep.saOutputDataFiles || []).join("\n");
        document.getElementById("inputTestCommands").value =
            (dictStep.saTestCommands || []).join("\n");
        document.getElementById("inputCommands").value =
            (dictStep.saPlotCommands || []).join("\n");
        document.getElementById("inputOutputFiles").value =
            (dictStep.saPlotFiles || []).join("\n");

        document.getElementById("modalTitle").textContent =
            "Edit Step: " + dictStep.sName;
        fnShowModal();
    }

    function fnClearForm() {
        var elName = document.getElementById("inputStepName");
        elName.value = "";
        elName.readOnly = false;
        elName.title = "";
        document.getElementById("inputStepDirectory").value = "";
        document.getElementById("inputInteractive").checked = false;
        document.getElementById("inputPlotOnly").checked = true;
        document.getElementById("inputSetupCommands").value = "";
        document.getElementById("inputOutputDataFiles").value = "";
        document.getElementById("inputTestCommands").value = "";
        document.getElementById("inputCommands").value = "";
        document.getElementById("inputOutputFiles").value = "";
    }

    function fnShowModal() {
        var elOverlay = document.getElementById("modalStepEditor");
        var elModal = elOverlay.querySelector(".modal");
        elModal.style.transform = "";
        elOverlay.classList.remove("modal-displaced");
        elOverlay.classList.add("active");
        document.getElementById("inputStepName").focus();
    }

    function fnHideModal() {
        var elOverlay = document.getElementById("modalStepEditor");
        elOverlay.classList.remove("active", "modal-displaced");
        var elModal = elOverlay.querySelector(".modal");
        elModal.style.transform = "";
    }

    function flistParseTextarea(sElementId) {
        const sValue =
            document.getElementById(sElementId).value.trim();
        if (!sValue) return [];
        return sValue.split("\n").filter(function (sLine) {
            return sLine.trim().length > 0;
        });
    }

    function fdictBuildStepFromForm() {
        return {
            sName: document
                .getElementById("inputStepName")
                .value.trim(),
            sDirectory: document
                .getElementById("inputStepDirectory")
                .value.trim(),
            bInteractive: document.getElementById("inputInteractive")
                .checked,
            bPlotOnly: document.getElementById("inputPlotOnly")
                .checked,
            saDataCommands: flistParseTextarea(
                "inputSetupCommands"
            ),
            saOutputDataFiles: flistParseTextarea("inputOutputDataFiles"),
            saTestCommands: flistParseTextarea("inputTestCommands"),
            saPlotCommands: flistParseTextarea("inputCommands"),
            saPlotFiles: flistParseTextarea("inputOutputFiles"),
        };
    }

    async function fnSave() {
        const dictData = fdictBuildStepFromForm();
        if (!dictData.sName) {
            VaibifyApp.fnShowToast(
                "Step name is required",
                "error"
            );
            return;
        }

        const sContainerId = VaibifyApp.fsGetContainerId();
        const dictWorkflow = VaibifyApp.fdictGetWorkflow();

        try {
            if (sMode === "edit") {
                // Route through the shared save so the edit carries the
                // compare-and-swap fingerprint. The old raw fetch sent
                // none, and the backend treats a missing fingerprint as
                // a legacy unconditional overwrite — so a researcher
                // silently clobbered an in-container agent edit that
                // landed while the modal was open. Submit the fingerprint
                // captured AT OPEN (not the current tracked one, which an
                // out-of-band reload may have advanced to the agent's new
                // version): that is what makes the backend refuse a stale
                // save instead of accepting it under the new fingerprint.
                // On a 409 or any failure fnSaveStepUpdate re-syncs and
                // toasts; a null result means the save did not land, so
                // leave the modal open to retry.
                dictData.sBaseFingerprint = sEditBaseFingerprint;
                const dictResult = await VaibifyApp.fnSaveStepUpdate(
                    iEditIndex, dictData);
                if (!dictResult) {
                    return;
                }
                dictWorkflow.listSteps[iEditIndex] = dictResult;
                VaibifyApp.fnShowToast(
                    "Step updated",
                    "success"
                );
            } else if (sMode === "insert") {
                if ((dictWorkflow.listSteps || []).length >= _I_STEP_COUNT_MAX) {
                    VaibifyModals.fnShowInfoModal(
                        "Step limit reached",
                        "Vaibify projects are capped at 500 steps. Remove or combine steps before adding another.");
                    return;
                }
                const response = await fetch(
                    "/api/steps/" +
                        sContainerId +
                        "/insert/" +
                        iInsertPosition,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify(dictData),
                    }
                );
                if (response.ok) {
                    const result = await response.json();
                    if (result.bShouldWarnHundredSteps) {
                        VaibifyModals.fnShowInfoModal(
                            "Project milestone", _S_HUNDRED_STEP_WARN_HTML);
                    }
                    dictWorkflow.listSteps = result.listSteps;
                    VaibifyApp.fnShowToast(
                        "Step inserted (references renumbered)",
                        "success"
                    );
                } else {
                    throw new Error("Insert failed");
                }
            } else {
                if ((dictWorkflow.listSteps || []).length >= _I_STEP_COUNT_MAX) {
                    VaibifyModals.fnShowInfoModal(
                        "Step limit reached",
                        "Vaibify projects are capped at 500 steps. Remove or combine steps before adding another.");
                    return;
                }
                const response = await fetch(
                    "/api/steps/" +
                        sContainerId +
                        "/create",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify(dictData),
                    }
                );
                if (response.ok) {
                    const result = await response.json();
                    if (result.bShouldWarnHundredSteps) {
                        VaibifyModals.fnShowInfoModal(
                            "Project milestone", _S_HUNDRED_STEP_WARN_HTML);
                    }
                    dictWorkflow.listSteps.push(result.dictStep);
                    VaibifyApp.fnShowToast(
                        "Step created",
                        "success"
                    );
                } else {
                    throw new Error("Create failed");
                }
            }
            VaibifyApp.fnRenderStepList();
            fnHideModal();
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Save failed: " + error.message,
                "error"
            );
        }
    }

    function fnBindModalDrag() {
        var elTitle = document.getElementById("modalTitle");
        var elModal = elTitle.closest(".modal");
        var elOverlay = document.getElementById("modalStepEditor");
        var iOffsetX = 0;
        var iOffsetY = 0;
        var iStartX = 0;
        var iStartY = 0;
        var bDragging = false;

        elTitle.addEventListener("mousedown", function (event) {
            if (event.target.tagName === "INPUT") return;
            bDragging = true;
            iStartX = event.clientX - iOffsetX;
            iStartY = event.clientY - iOffsetY;
            event.preventDefault();
        });

        document.addEventListener("mousemove", function (event) {
            if (!bDragging) return;
            iOffsetX = event.clientX - iStartX;
            iOffsetY = event.clientY - iStartY;
            elModal.style.transform =
                "translate(" + iOffsetX + "px, " + iOffsetY + "px)";
            if (!elOverlay.classList.contains("modal-displaced")) {
                elOverlay.classList.add("modal-displaced");
            }
        });

        document.addEventListener("mouseup", function () {
            bDragging = false;
        });
    }

    /* Bind modal events */
    document.addEventListener("DOMContentLoaded", function () {
        document
            .getElementById("btnNewStep")
            .addEventListener("click", fnOpenCreateModal);
        document
            .getElementById("btnModalCancel")
            .addEventListener("click", fnHideModal);
        document
            .getElementById("btnModalSave")
            .addEventListener("click", fnSave);

        /* Close modal on overlay click */
        document
            .getElementById("modalStepEditor")
            .addEventListener("click", function (event) {
                if (event.target === this) {
                    fnHideModal();
                }
            });

        /* Escape key closes modal */
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                fnHideModal();
            }
        });

        fnBindModalDrag();
    });

    /* --- Rename (context menu) ---
       Two stages: a dry-run POST returns the full change-set
       (directory move, path rewrites, script warnings) which is
       shown in a confirm modal, and only the researcher's confirm
       sends the apply. The directory follows the name — that is
       part of what a vaibify project IS — so the preview is the
       honesty step, not an opt-out. */

    function fnOpenRenameModal(iIndex) {
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var dictStep = dictWorkflow
            && dictWorkflow.listSteps[iIndex];
        if (!dictStep) return;
        VaibifyModals.fnShowInputModal(
            "Rename step '" + (dictStep.sName || "") + "' to:",
            dictStep.sName || "",
            function (sNewName) {
                _fnPreviewRename(iIndex, sNewName);
            },
            "Preview Rename"
        );
    }

    async function _fnPreviewRename(iIndex, sNewName) {
        var sContainerId = VaibifyApp.fsGetContainerId();
        try {
            var dictPlan = await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iIndex +
                "/rename",
                {sNewName: sNewName, bDryRun: true});
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
            return;
        }
        VaibifyModals.fnShowConfirmModal(
            "Rename step",
            _fsDescribeRenamePlan(dictPlan),
            function () { _fnApplyRename(iIndex, sNewName); }
        );
    }

    function _fsDescribeRenamePlan(dictPlan) {
        var listLines = [
            "'" + dictPlan.sOldName + "' becomes '" +
            dictPlan.sNewName + "'.",
        ];
        if (dictPlan.bDirectoryRenamed) {
            listLines.push(
                "Directory '" + dictPlan.sOldDirectory +
                "' moves to '" + dictPlan.sNewDirectory +
                "' (git mv; the staged rename appears in the " +
                "Repos panel until you commit).");
            var iRewrites =
                (dictPlan.listFieldRewrites || []).length +
                (dictPlan.listBinaryRewrites || []).length;
            if (iRewrites > 0) {
                listLines.push(iRewrites + " declared path(s), " +
                    "the verification marker, and the manifest " +
                    "are rewritten to follow it.");
            }
        } else if (dictPlan.sDirectoryNote) {
            listLines.push(dictPlan.sDirectoryNote + ".");
        }
        (dictPlan.listScriptWarnings || []).forEach(
            function (sScript) {
                listLines.push("⚠ Script '" + sScript +
                    "' mentions the old directory name and must " +
                    "be updated by hand.");
            });
        (dictPlan.listCommandWarnings || []).forEach(
            function (sWarning) {
                listLines.push("⚠ " + sWarning + ".");
            });
        return listLines.join("\n\n");
    }

    async function _fnApplyRename(iIndex, sNewName) {
        var sContainerId = VaibifyApp.fsGetContainerId();
        try {
            await VaibifyApi.fdictPost(
                "/api/steps/" + sContainerId + "/" + iIndex +
                "/rename",
                {sNewName: sNewName, bDryRun: false});
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
            return;
        }
        VaibifyApp.fnShowToast(
            "Step renamed to '" + sNewName + "'", "success");
        await VaibifyWorkflowManager.fnRefreshWorkflow();
    }

    return {
        fnOpenCreateModal: fnOpenCreateModal,
        fnOpenEditModal: fnOpenEditModal,
        fnOpenInsertModal: fnOpenInsertModal,
        fnOpenRenameModal: fnOpenRenameModal,
    };
})();
