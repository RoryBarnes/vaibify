/* Vaibify — New Workflow creation wizard */

var VaibifyNewWorkflowWizard = (function () {
    "use strict";

    var _LIST_STEP_TITLES = [
        "Name your workflow",
        "Choose where it lives",
        "Confirm and create"
    ];
    var _I_LAST_STEP = 2;
    var _PATTERN_NEW_DIR_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.\-]*$/;

    var _sContainerId = "";
    var _iWizardStep = 0;
    var _dictWizardState = _fdictBuildEmptyState();
    var _dictRepoStatus = null;
    var _bSubmitting = false;

    /* ---------- Public API ---------- */

    function fnLaunch(sContainerId) {
        if (!sContainerId) return;
        _sContainerId = sContainerId;
        _iWizardStep = 0;
        _dictWizardState = _fdictBuildEmptyState();
        _dictRepoStatus = null;
        _bSubmitting = false;
        _fnShowModal();
        _fnRenderStep();
    }

    function fnBindEventHandlers() {
        document.getElementById("btnNewWorkflowWizardCancel")
            .addEventListener("click", _fnHandleCancel);
        document.getElementById("btnNewWorkflowWizardBack")
            .addEventListener("click", _fnHandleBack);
        document.getElementById("btnNewWorkflowWizardNext")
            .addEventListener("click", _fnHandleNext);
    }

    /* ---------- State ---------- */

    function _fdictBuildEmptyState() {
        return {
            sDisplayName: "",
            sLocationKind: "",
            sLocationName: "",
            sNewDirName: ""
        };
    }

    function _fnResetWorkflowState() {
        _sContainerId = "";
        _iWizardStep = 0;
        _dictWizardState = _fdictBuildEmptyState();
        _dictRepoStatus = null;
        _bSubmitting = false;
    }

    /* ---------- Modal lifecycle ---------- */

    function _fnShowModal() {
        document.getElementById("modalNewWorkflowWizard")
            .style.display = "flex";
    }

    function _fnHideModal() {
        document.getElementById("modalNewWorkflowWizard")
            .style.display = "none";
    }

    function _fnHandleCancel() {
        if (_bSubmitting) return;
        _fnHideModal();
        _fnResetWorkflowState();
    }

    /* ---------- Navigation ---------- */

    function _fnHandleBack() {
        if (_iWizardStep <= 0 || _bSubmitting) return;
        _iWizardStep--;
        _fnRenderStep();
    }

    function _fnHandleNext() {
        if (_bSubmitting) return;
        if (!_fbStepIsValid(_iWizardStep)) return;
        if (_iWizardStep < _I_LAST_STEP) {
            _iWizardStep++;
            _fnRenderStep();
            return;
        }
        _fnSubmit();
    }

    function _fbStepIsValid(iStep) {
        if (iStep === 0) return _fbNameStepIsValid();
        if (iStep === 1) return _fbLocationStepIsValid();
        return true;
    }

    function _fbNameStepIsValid() {
        var sName = _dictWizardState.sDisplayName.trim();
        if (!sName || sName.length > 200) return false;
        return _fsBuildSlug(sName).length > 0;
    }

    function _fbLocationStepIsValid() {
        if (_dictWizardState.sLocationKind === "new") {
            return _fbNewDirNameValid(_dictWizardState.sNewDirName);
        }
        return _dictWizardState.sLocationName.length > 0;
    }

    /* ---------- Slug / validation helpers ---------- */

    function _fsBuildSlug(sDisplayName) {
        return (sDisplayName || "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "");
    }

    function _fbNewDirNameValid(sName) {
        if (!sName || sName.length > 100) return false;
        if (sName.indexOf("..") !== -1) return false;
        return _PATTERN_NEW_DIR_NAME.test(sName);
    }

    /* ---------- Render: dispatch ---------- */

    function _fnRenderStep() {
        _fnUpdateProgress();
        _fnUpdateButtons();
        document.getElementById("newWorkflowWizardTitle").textContent =
            _LIST_STEP_TITLES[_iWizardStep];
        var elContent = document.getElementById(
            "newWorkflowWizardContent");
        if (_iWizardStep === 0) {
            _fnRenderNameStep(elContent);
        } else if (_iWizardStep === 1) {
            _fnRenderLocationStep(elContent);
        } else {
            _fnRenderConfirmStep(elContent);
        }
    }

    function _fnUpdateProgress() {
        var listDots = document.querySelectorAll(
            "#newWorkflowWizardProgress .wizard-progress-step"
        );
        listDots.forEach(function (el, i) {
            el.classList.toggle("active", i <= _iWizardStep);
        });
    }

    function _fnUpdateButtons() {
        document.getElementById("btnNewWorkflowWizardBack").disabled =
            _iWizardStep === 0 || _bSubmitting;
        var elNext = document.getElementById(
            "btnNewWorkflowWizardNext");
        if (_bSubmitting) {
            elNext.disabled = true;
            elNext.textContent = "Creating…";
            return;
        }
        elNext.textContent = _iWizardStep === _I_LAST_STEP
            ? "Create Workflow" : "Next";
        elNext.disabled = !_fbStepIsValid(_iWizardStep);
    }

    /* ---------- Render: Step 1 (Name) ---------- */

    function _fnRenderNameStep(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label for="newWorkflowName">' +
            'Workflow display name</label>' +
            '<input type="text" id="newWorkflowName" ' +
            'autocomplete="off" />' +
            '<div class="wizard-helper-text" ' +
            'id="newWorkflowNameHelp"></div></div>';
        var elInput = document.getElementById("newWorkflowName");
        elInput.value = _dictWizardState.sDisplayName;
        elInput.focus();
        elInput.addEventListener("input", _fnOnNameInput);
        _fnUpdateNameStepHelp();
    }

    function _fnOnNameInput(event) {
        _dictWizardState.sDisplayName = event.target.value;
        _fnUpdateNameStepHelp();
        _fnUpdateButtons();
    }

    function _fnUpdateNameStepHelp() {
        var sName = _dictWizardState.sDisplayName.trim();
        var sSlug = _fsBuildSlug(sName);
        var elHelp = document.getElementById("newWorkflowNameHelp");
        if (!elHelp) return;
        if (!sName) {
            elHelp.textContent = "Enter a name to continue.";
            return;
        }
        if (sName.length > 200) {
            elHelp.textContent =
                "Keep names under 200 characters.";
            return;
        }
        if (!sSlug) {
            elHelp.textContent =
                "Use at least one letter or number.";
            return;
        }
        elHelp.textContent =
            "Saved as " + sSlug + ".json inside the project repo.";
    }

    /* ---------- Render: Step 2 (Location) ---------- */

    async function _fnRenderLocationStep(elContent) {
        if (_dictRepoStatus) {
            elContent.innerHTML =
                _fsBuildLocationHtml(_dictRepoStatus);
            _fnBindLocationCards(elContent);
            return;
        }
        elContent.innerHTML =
            '<div class="wizard-helper-text">' +
            'Loading directories…</div>';
        try {
            _dictRepoStatus = await VaibifyApi.fdictGet(
                "/api/repos/" + _sContainerId + "/status");
        } catch (error) {
            elContent.innerHTML = _fsBuildLoadErrorHtml(error);
            return;
        }
        elContent.innerHTML = _fsBuildLocationHtml(_dictRepoStatus);
        _fnBindLocationCards(elContent);
    }

    function _fsBuildLoadErrorHtml(error) {
        var sMessage = VaibifyUtilities.fsSanitizeErrorForUser(
            (error && error.message) || "");
        return '<div class="wizard-helper-text">' +
            'Failed to load directories: ' +
            VaibifyUtilities.fnEscapeHtml(sMessage) + '</div>';
    }

    function _fsBuildLocationHtml(dictStatus) {
        var sHtml = "";
        sHtml += _fsBuildLocationGroup(
            "Already set up as vaibify repos",
            dictStatus.listTracked || [], "tracked", true);
        sHtml += _fsBuildLocationGroup(
            "Existing git directories",
            dictStatus.listUndecided || [], "git", false);
        sHtml += _fsBuildLocationGroup(
            "Other directories (will be initialized as a git repo)",
            dictStatus.listNonRepoDirs || [], "nongit", false);
        sHtml += _fsBuildNewDirSection();
        return sHtml;
    }

    function _fsBuildLocationGroup(
        sTitle, listItems, sKind, bAllowMissing
    ) {
        var sBody = listItems.map(function (dictItem) {
            return _fsBuildLocationCard(
                dictItem, sKind, bAllowMissing);
        }).join("");
        if (!sBody) {
            sBody = '<div class="wizard-helper-text">' +
                '(none found)</div>';
        }
        return '<div class="wizard-location-group">' +
            '<h4>' + VaibifyUtilities.fnEscapeHtml(sTitle) +
            '</h4>' + sBody + '</div>';
    }

    function _fsBuildLocationCard(dictItem, sKind, bAllowMissing) {
        var sName = dictItem.sName;
        var sCaption = "/workspace/" + sName;
        var bDisabled = bAllowMissing && dictItem.bMissing;
        if (bDisabled) sCaption += " (missing on disk)";
        var bSelected = (
            _dictWizardState.sLocationKind === sKind &&
            _dictWizardState.sLocationName === sName);
        var sClasses = "container-card wizard-location-card" +
            (bSelected ? " wizard-location-card-selected" : "") +
            (bDisabled ? " wizard-location-card-disabled" : "");
        var sAttrs = bDisabled
            ? ' data-disabled="true"'
            : ' data-kind="' + sKind +
              '" data-name="' +
              VaibifyUtilities.fnEscapeHtml(sName) + '"';
        return '<div class="' + sClasses + '"' + sAttrs + '>' +
            '<span class="name">' +
            VaibifyUtilities.fnEscapeHtml(sName) + '</span>' +
            '<span class="image">' +
            VaibifyUtilities.fnEscapeHtml(sCaption) +
            '</span></div>';
    }

    function _fsBuildNewDirSection() {
        var bSelected = _dictWizardState.sLocationKind === "new";
        var sSelectedClass = bSelected
            ? " wizard-location-card-selected" : "";
        var sValue = VaibifyUtilities.fnEscapeHtml(
            _dictWizardState.sNewDirName);
        var sInputDisplay = bSelected ? "" : "display: none;";
        return '<div class="wizard-location-group">' +
            '<h4>Create a new directory</h4>' +
            '<div class="container-card wizard-location-card' +
            sSelectedClass + '" data-kind="new">' +
            '<span class="name">' +
            'New directory under /workspace/</span>' +
            '<span class="image">Will be created and initialized ' +
            'as a git repo</span></div>' +
            '<div class="form-group" id="wizardNewDirInputGroup" ' +
            'style="' + sInputDisplay + '">' +
            '<label for="wizardNewDirName">Directory name</label>' +
            '<input type="text" id="wizardNewDirName" value="' +
            sValue + '" placeholder="e.g. my_new_project" />' +
            '<div class="wizard-helper-text" ' +
            'id="wizardNewDirHelp"></div></div></div>';
    }

    function _fnBindLocationCards(elContent) {
        elContent.querySelectorAll(".wizard-location-card").forEach(
            function (el) {
                if (el.dataset.disabled) return;
                el.addEventListener(
                    "click", _fnHandleLocationCardClick);
            }
        );
        var elNewInput = document.getElementById("wizardNewDirName");
        if (elNewInput) {
            elNewInput.addEventListener("input", _fnOnNewDirInput);
        }
        _fnUpdateNewDirHelp();
    }

    function _fnHandleLocationCardClick(event) {
        var el = event.currentTarget;
        var sKind = el.dataset.kind;
        var sName = el.dataset.name || "";
        _dictWizardState.sLocationKind = sKind;
        _dictWizardState.sLocationName = sName;
        if (sKind !== "new") {
            _dictWizardState.sNewDirName = "";
        }
        _fnRenderStep();
        if (sKind === "new") {
            var elInput = document.getElementById("wizardNewDirName");
            if (elInput) elInput.focus();
        }
    }

    function _fnOnNewDirInput(event) {
        _dictWizardState.sNewDirName = event.target.value.trim();
        _fnUpdateNewDirHelp();
        _fnUpdateButtons();
    }

    function _fnUpdateNewDirHelp() {
        var elHelp = document.getElementById("wizardNewDirHelp");
        if (!elHelp) return;
        var sName = _dictWizardState.sNewDirName;
        if (!sName) {
            elHelp.textContent = "Letters, numbers, hyphens, dots, " +
                "or underscores. Max 100 characters.";
            return;
        }
        if (!_fbNewDirNameValid(sName)) {
            elHelp.textContent = "Invalid name. Allowed: letters, " +
                "numbers, hyphens, dots, underscores. Cannot start " +
                "with a dot.";
            return;
        }
        elHelp.textContent = "Will create /workspace/" + sName +
            " and initialize it as a git repo.";
    }

    /* ---------- Render: Step 3 (Confirm) ---------- */

    function _fnRenderConfirmStep(elContent) {
        var sName = _dictWizardState.sDisplayName.trim();
        var sSlug = _fsBuildSlug(sName);
        var sLocation = _fsResolveLocationName();
        var bWillInit = _fbLocationNeedsInit();
        var sLines = "";
        sLines += _fsBuildSummaryRow("Workflow", sName);
        sLines += _fsBuildSummaryRow("File", sSlug + ".json");
        sLines += _fsBuildSummaryRow(
            "Location", "/workspace/" + sLocation);
        var sActions = _fsBuildConfirmActions(bWillInit, sSlug);
        elContent.innerHTML =
            '<div class="wizard-summary">' + sLines + '</div>' +
            sActions;
    }

    function _fsResolveLocationName() {
        if (_dictWizardState.sLocationKind === "new") {
            return _dictWizardState.sNewDirName;
        }
        return _dictWizardState.sLocationName;
    }

    function _fbLocationNeedsInit() {
        return _dictWizardState.sLocationKind === "new" ||
            _dictWizardState.sLocationKind === "nongit";
    }

    function _fsBuildSummaryRow(sLabel, sValue) {
        return '<div class="wizard-summary-row">' +
            '<span class="wizard-summary-label">' +
            VaibifyUtilities.fnEscapeHtml(sLabel) + ':</span> ' +
            '<span class="wizard-summary-value">' +
            VaibifyUtilities.fnEscapeHtml(sValue) +
            '</span></div>';
    }

    function _fsBuildConfirmActions(bWillInit, sSlug) {
        var listSteps = [];
        if (_dictWizardState.sLocationKind === "new") {
            listSteps.push("Create the directory");
        }
        if (bWillInit) {
            listSteps.push("Initialize it as a git repo with " +
                "an empty initial commit");
        }
        listSteps.push(
            "Write .vaibify/workflows/" + sSlug + ".json");
        var sBody = listSteps.map(function (sStep) {
            return '<li>' + VaibifyUtilities.fnEscapeHtml(sStep) +
                '</li>';
        }).join("");
        return '<h4>What this will do</h4><ul>' + sBody + '</ul>';
    }

    /* ---------- Submit ---------- */

    async function _fnSubmit() {
        _bSubmitting = true;
        _fnUpdateButtons();
        try {
            if (_fbLocationNeedsInit()) {
                await _fnRunInitProjectRepo();
            }
            var dictResult = await _fnRunCreateWorkflow();
            _fnHideModal();
            VaibifyWorkflowManager.fnSelectWorkflow(
                _sContainerId, dictResult.sPath, dictResult.sName);
            PipeleyenApp.fnShowToast("Workflow created", "success");
            _fnResetWorkflowState();
        } catch (error) {
            _fnHandleSubmitError(error);
        } finally {
            _bSubmitting = false;
            _fnUpdateButtons();
        }
    }

    function _fnRunInitProjectRepo() {
        var sDir = _fsResolveLocationName();
        var bCreateIfMissing =
            _dictWizardState.sLocationKind === "new";
        return VaibifyApi.fdictPost(
            "/api/repos/" + _sContainerId + "/init",
            {
                sDirectory: sDir,
                bCreateIfMissing: bCreateIfMissing
            });
    }

    function _fnRunCreateWorkflow() {
        var sName = _dictWizardState.sDisplayName.trim();
        var sSlug = _fsBuildSlug(sName);
        return VaibifyApi.fdictPost(
            "/api/workflows/" + _sContainerId + "/create",
            {
                sWorkflowName: sName,
                sFileName: sSlug,
                sRepoDirectory: _fsResolveLocationName()
            });
    }

    function _fnHandleSubmitError(error) {
        var sMessage = VaibifyUtilities.fsSanitizeErrorForUser(
            (error && error.message) || "");
        PipeleyenApp.fnShowToast(sMessage, "error");
        if (_fbErrorIsNameCollision(sMessage)) {
            _iWizardStep = 0;
            _fnRenderStep();
            var elInput = document.getElementById("newWorkflowName");
            if (elInput) {
                elInput.focus();
                elInput.select();
            }
        }
    }

    function _fbErrorIsNameCollision(sMessage) {
        var sLower = (sMessage || "").toLowerCase();
        if (sLower.indexOf("already exists") === -1) return false;
        return sLower.indexOf("workflow") !== -1;
    }

    return {
        fnLaunch: fnLaunch,
        fnBindEventHandlers: fnBindEventHandlers
    };
})();
