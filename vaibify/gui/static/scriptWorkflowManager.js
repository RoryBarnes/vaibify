/* Vaibify — Workflow loading, creation, and switching */

var VaibifyWorkflowManager = (function () {
    "use strict";

    /* --- Workflow List Rendering --- */

    function fnRenderWorkflowList(listWorkflows, sId) {
        var elList = document.getElementById("listWorkflows");
        var sCardsHtml = "";
        if (listWorkflows.length === 0) {
            sCardsHtml =
                '<p style="color: var(--text-muted);' +
                ' text-align: center;">' +
                'No workflows found. Create one to get ' +
                'started.</p>';
        } else {
            sCardsHtml = listWorkflows.map(function (dictWf) {
                return _fsRenderWorkflowCard(dictWf);
            }).join("");
        }
        elList.innerHTML = sCardsHtml;
        _fnBindWorkflowCards(elList, sId);
    }

    function _fsRenderWorkflowCard(dictWf) {
        var sRepo = dictWf.sRepoName || "";
        return (
            '<div class="container-card" data-path="' +
            PipeleyenApp.fnEscapeHtml(dictWf.sPath) + '">' +
            '<span class="name">' +
            PipeleyenApp.fnEscapeHtml(dictWf.sName) +
            '</span>' +
            '<span class="image">' +
            PipeleyenApp.fnEscapeHtml(sRepo) + '</span></div>'
        );
    }

    function _fnBindWorkflowCards(elList, sId) {
        elList.querySelectorAll(".container-card").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    var sPath = el.dataset.path;
                    var sName = el.querySelector(
                        ".name").textContent;
                    fnSelectWorkflow(sId, sPath, sName);
                });
            }
        );
    }

    /* --- Workflow Creation --- */

    function fnCreateNewWorkflow(sContainerId) {
        if (!sContainerId) return;
        PipeleyenApp.fnShowInputModal(
            "Workflow display name",
            "My Workflow",
            function (sName) {
                var sDefault = sName.toLowerCase()
                    .replace(/[^a-z0-9]+/g, "-");
                PipeleyenApp.fnShowInputModal(
                    "Filename (no spaces, .json added " +
                    "automatically)",
                    sDefault,
                    function (sFileName) {
                        _fnPromptForRepoDirectory(
                            sContainerId, sName, sFileName);
                    }
                );
            }
        );
    }

    async function _fnPromptForRepoDirectory(
        sContainerId, sName, sFileName
    ) {
        var sSuggestion = "";
        try {
            var dictRepos = await VaibifyApi.fdictGet(
                "/api/repos/" + sContainerId
            );
            if (dictRepos.listRepos && dictRepos.listRepos.length) {
                sSuggestion = dictRepos.listRepos[0];
            }
        } catch (error) { /* fall through */ }
        PipeleyenApp.fnShowInputModal(
            "Repository directory name under /workspace/ " +
            "(where the workflow and its Plot/ folder " +
            "will live)",
            sSuggestion,
            function (sRepo) {
                _fnSubmitNewWorkflow(
                    sContainerId, sName, sFileName, sRepo);
            }
        );
    }

    async function _fnSubmitNewWorkflow(
        sContainerId, sName, sFileName, sRepo
    ) {
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/workflows/" + sContainerId + "/create",
                {
                    sWorkflowName: sName,
                    sFileName: sFileName,
                    sRepoDirectory: sRepo,
                }
            );
            PipeleyenApp.fnShowToast(
                "Workflow created", "success");
            fnSelectWorkflow(
                sContainerId, dictResult.sPath,
                dictResult.sName);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                PipeleyenApp.fsSanitizeErrorForUser(
                    error.message), "error");
        }
    }

    /* --- Workflow Selection --- */

    async function fnSelectWorkflow(
        sId, sWorkflowPathArg, sWorkflowName
    ) {
        try {
            var dictResult = await VaibifyApi.fdictPostRaw(
                "/api/connect/" + sId +
                "?sWorkflowPath=" +
                encodeURIComponent(sWorkflowPathArg)
            );
            PipeleyenApp.fnActivateWorkflow(
                sId, dictResult, sWorkflowName);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                PipeleyenApp.fsSanitizeErrorForUser(
                    error.message), "error");
        }
    }

    /* --- Workflow Dropdown (Switcher) --- */

    async function fnToggleWorkflowDropdown() {
        var elDropdown = document.getElementById(
            "workflowDropdown");
        if (elDropdown.classList.contains("active")) {
            elDropdown.classList.remove("active");
            return;
        }
        var sContainerId = PipeleyenApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var listWorkflows = await VaibifyApi.fdictGet(
                "/api/workflows/" + sContainerId);
            _fnRenderWorkflowDropdown(listWorkflows);
            elDropdown.classList.add("active");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Could not load workflows", "error");
        }
    }

    function fnHideWorkflowDropdown() {
        document.getElementById("workflowDropdown")
            .classList.remove("active");
    }

    function _fnRenderWorkflowDropdown(listWorkflows) {
        var elDropdown = document.getElementById(
            "workflowDropdown");
        var sWorkflowPath = PipeleyenApp.fsGetWorkflowPath();
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var bInNoWorkflow = !sWorkflowPath && !dictWorkflow;
        var sHtml = '<div class="workflow-dropdown-item' +
            (bInNoWorkflow ? " current" : "") +
            '" data-action="noWorkflow">' +
            '<span class="wf-name">No Workflow</span></div>';
        sHtml += listWorkflows.map(function (dictWf) {
            var bCurrent = dictWf.sPath === sWorkflowPath;
            return (
                '<div class="workflow-dropdown-item' +
                (bCurrent ? " current" : "") +
                '" data-path="' +
                PipeleyenApp.fnEscapeHtml(dictWf.sPath) +
                '" data-name="' +
                PipeleyenApp.fnEscapeHtml(dictWf.sName) +
                '">' +
                '<span class="wf-name">' +
                PipeleyenApp.fnEscapeHtml(dictWf.sName) +
                '</span>' +
                '<span class="wf-path">' +
                PipeleyenApp.fnEscapeHtml(dictWf.sPath) +
                '</span></div>'
            );
        }).join("");
        elDropdown.innerHTML = sHtml;
        _fnBindWorkflowDropdownItems(elDropdown);
    }

    function _fnBindWorkflowDropdownItems(elDropdown) {
        var sWorkflowPath = PipeleyenApp.fsGetWorkflowPath();
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        elDropdown.querySelectorAll(".workflow-dropdown-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHideWorkflowDropdown();
                    if (el.dataset.action === "noWorkflow") {
                        if (!dictWorkflow && !sWorkflowPath) return;
                        PipeleyenApp.fnEnterNoWorkflow(
                            PipeleyenApp.fsGetContainerId());
                        return;
                    }
                    var sPath = el.dataset.path;
                    var sName = el.dataset.name;
                    if (sPath === sWorkflowPath) return;
                    fnConfirmWorkflowSwitch(sPath, sName);
                });
            });
    }

    function fnConfirmWorkflowSwitch(sNewPath, sNewName) {
        PipeleyenApp.fnShowConfirmModal(
            "Switch Workflow",
            "Switch to \"" + sNewName + "\"?\n\n" +
            "Current workflow state will be saved.",
            async function () {
                await fnSaveCurrentWorkflow();
                fnSelectWorkflow(
                    PipeleyenApp.fsGetContainerId(),
                    sNewPath, sNewName);
            }
        );
    }

    async function fnSaveCurrentWorkflow() {
        var sContainerId = PipeleyenApp.fsGetContainerId();
        var dictWorkflow = PipeleyenApp.fdictGetWorkflow();
        var sWorkflowPath = PipeleyenApp.fsGetWorkflowPath();
        if (!sContainerId || !dictWorkflow || !sWorkflowPath) return;
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/connect/" + sContainerId +
                "?sWorkflowPath=" +
                encodeURIComponent(sWorkflowPath)
            );
        } catch (error) {
            PipeleyenApp.fnShowToast(
                "Could not save workflow", "error");
        }
    }

    /* --- Creation Wizard --- */

    var _iWizardStep = 0;
    var _dictWizardData = {};
    var _LIST_WIZARD_TITLES = [
        "Project Directory",
        "Template",
        "Project Name",
        "Python Version",
        "Repositories",
        "Summary",
    ];

    function fnOpenCreateWizard() {
        _iWizardStep = 0;
        _dictWizardData = {
            sDirectory: "",
            sTemplateName: "",
            sProjectName: "",
            sPythonVersion: "3.12",
            listRepositories: [],
        };
        document.getElementById("modalCreateWizard")
            .style.display = "flex";
        _fnRenderWizardStep(_iWizardStep);
    }

    function fnBindCreateWizardModal() {
        document.getElementById("btnWizardCancel").addEventListener(
            "click", _fnCloseWizard
        );
        document.getElementById("btnWizardBack").addEventListener(
            "click", _fnWizardStepBack
        );
        document.getElementById("btnWizardNext").addEventListener(
            "click", _fnWizardStepNext
        );
    }

    function _fnCloseWizard() {
        document.getElementById("modalCreateWizard")
            .style.display = "none";
    }

    function _fnWizardStepBack() {
        if (_iWizardStep <= 0) return;
        _fnSaveCurrentStepData();
        _iWizardStep--;
        _fnRenderWizardStep(_iWizardStep);
    }

    function _fnWizardStepNext() {
        _fnSaveCurrentStepData();
        if (!_fbValidateWizardStep(_iWizardStep)) return;
        if (_iWizardStep >= 5) {
            _fnSubmitCreateProject();
            return;
        }
        _iWizardStep++;
        _fnRenderWizardStep(_iWizardStep);
    }

    function _fnRenderWizardStep(iStep) {
        _fnUpdateWizardProgress(iStep);
        _fnUpdateWizardButtons(iStep);
        document.getElementById("wizardStepTitle").textContent =
            _LIST_WIZARD_TITLES[iStep];
        var elContent = document.getElementById(
            "wizardStepContent");
        var listRenderers = [
            _fnRenderStepDirectory,
            _fnRenderStepTemplate,
            _fnRenderStepProjectName,
            _fnRenderStepPythonVersion,
            _fnRenderStepRepositories,
            _fnRenderStepSummary,
        ];
        listRenderers[iStep](elContent);
    }

    function _fnUpdateWizardProgress(iStep) {
        var listDots = document.querySelectorAll(
            ".wizard-progress-step"
        );
        listDots.forEach(function (el, i) {
            el.classList.toggle("active", i <= iStep);
        });
    }

    function _fnUpdateWizardButtons(iStep) {
        document.getElementById("btnWizardBack").disabled =
            iStep === 0;
        document.getElementById("btnWizardNext").textContent =
            iStep === 5 ? "Create" : "Next";
    }

    function _fnRenderStepDirectory(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project Directory</label>' +
            '<input type="text" id="inputWizardDirectory" ' +
            'placeholder="/Users/you/projects/my-project">' +
            '</div>';
        var elInput = document.getElementById(
            "inputWizardDirectory");
        elInput.value = _dictWizardData.sDirectory;
    }

    function _fnRenderStepTemplate(elContent) {
        elContent.innerHTML =
            '<p class="muted-text" style="text-align:center;">' +
            'Loading templates...</p>';
        _fnFetchAndRenderTemplates(elContent);
    }

    async function _fnFetchAndRenderTemplates(elContent) {
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/setup/templates");
            _fnBuildTemplateCards(
                elContent, dictResult.listTemplates);
        } catch (error) {
            elContent.innerHTML =
                '<p class="muted-text">' +
                'Could not load templates.</p>';
        }
    }

    function _fnBuildTemplateCards(elContent, listTemplates) {
        if (!listTemplates || listTemplates.length === 0) {
            elContent.innerHTML =
                '<p class="muted-text">' +
                'No templates available.</p>';
            return;
        }
        elContent.innerHTML = '<div class="add-choice-cards">' +
            listTemplates.map(function (sName) {
                var sActive =
                    sName === _dictWizardData.sTemplateName
                        ? " style=\"border-color:" +
                          "var(--color-pale-blue);\""
                        : "";
                return '<div class="add-choice-card" ' +
                    'data-template="' +
                    PipeleyenApp.fnEscapeHtml(sName) + '"' +
                    sActive + '>' +
                    '<div class="add-choice-title">' +
                    PipeleyenApp.fnEscapeHtml(sName) +
                    '</div></div>';
            }).join("") + '</div>';
        _fnBindTemplateCardClicks(elContent);
    }

    function _fnBindTemplateCardClicks(elContent) {
        elContent.querySelectorAll(".add-choice-card").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    _dictWizardData.sTemplateName =
                        el.dataset.template;
                    _fnHighlightSelectedCard(elContent, el);
                });
            }
        );
    }

    function _fnHighlightSelectedCard(elContent, elSelected) {
        elContent.querySelectorAll(".add-choice-card").forEach(
            function (el) {
                el.style.borderColor =
                    el === elSelected
                        ? "var(--color-pale-blue)" : "";
            }
        );
    }

    function _fnRenderStepProjectName(elContent) {
        var sDefault = _fsProjectNameFromDirectory();
        if (!_dictWizardData.sProjectName) {
            _dictWizardData.sProjectName = sDefault;
        }
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project Name</label>' +
            '<input type="text" id="inputWizardProjectName" ' +
            'placeholder="my-project">' +
            '</div>';
        document.getElementById(
            "inputWizardProjectName").value =
            _dictWizardData.sProjectName;
    }

    function _fsProjectNameFromDirectory() {
        var sDir = _dictWizardData.sDirectory || "";
        var sTrimmed = sDir.replace(/\/+$/, "");
        var iLastSlash = sTrimmed.lastIndexOf("/");
        return iLastSlash >= 0
            ? sTrimmed.substring(iLastSlash + 1) : sTrimmed;
    }

    function _fnRenderStepPythonVersion(elContent) {
        var listVersions = [
            "3.9", "3.10", "3.11", "3.12", "3.13", "3.14",
        ];
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Python Version</label>' +
            '<select id="selectWizardPython">' +
            listVersions.map(function (sVersion) {
                var sSelected =
                    sVersion === _dictWizardData.sPythonVersion
                        ? " selected" : "";
                return '<option value="' + sVersion + '"' +
                    sSelected + '>' + sVersion + '</option>';
            }).join("") +
            '</select></div>';
    }

    function _fnRenderStepRepositories(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Repositories (one per line, ' +
            'optional)</label>' +
            '<textarea id="inputWizardRepos" rows="5" ' +
            'placeholder="https://github.com/org/repo.git">' +
            '</textarea></div>';
        var sRepos =
            _dictWizardData.listRepositories.join("\n");
        document.getElementById("inputWizardRepos").value =
            sRepos;
    }

    function _fnRenderStepSummary(elContent) {
        elContent.innerHTML =
            '<div style="font-size:13px;' +
            'color:var(--text-secondary);">' +
            '<p><strong>Directory:</strong> ' +
            PipeleyenApp.fnEscapeHtml(
                _dictWizardData.sDirectory) + '</p>' +
            '<p><strong>Template:</strong> ' +
            PipeleyenApp.fnEscapeHtml(
                _dictWizardData.sTemplateName) + '</p>' +
            '<p><strong>Project Name:</strong> ' +
            PipeleyenApp.fnEscapeHtml(
                _dictWizardData.sProjectName) + '</p>' +
            '<p><strong>Python:</strong> ' +
            PipeleyenApp.fnEscapeHtml(
                _dictWizardData.sPythonVersion) + '</p>' +
            '<p><strong>Repositories:</strong> ' +
            (_dictWizardData.listRepositories.length > 0
                ? PipeleyenApp.fnEscapeHtml(
                    _dictWizardData.listRepositories.join(
                        ", "))
                : '<em>None</em>') +
            '</p></div>';
    }

    function _fnSaveCurrentStepData() {
        var elDir = document.getElementById(
            "inputWizardDirectory");
        if (elDir) {
            _dictWizardData.sDirectory = elDir.value.trim();
        }
        var elName = document.getElementById(
            "inputWizardProjectName");
        if (elName) {
            _dictWizardData.sProjectName = elName.value.trim();
        }
        var elPython = document.getElementById(
            "selectWizardPython");
        if (elPython) {
            _dictWizardData.sPythonVersion = elPython.value;
        }
        var elRepos = document.getElementById(
            "inputWizardRepos");
        if (elRepos) {
            _dictWizardData.listRepositories = elRepos.value
                .split("\n")
                .map(function (s) { return s.trim(); })
                .filter(function (s) { return s.length > 0; });
        }
    }

    function _fbValidateWizardStep(iStep) {
        if (iStep === 0 && !_dictWizardData.sDirectory) {
            PipeleyenApp.fnShowToast(
                "Directory path is required.", "warning");
            return false;
        }
        if (iStep === 1 && !_dictWizardData.sTemplateName) {
            PipeleyenApp.fnShowToast(
                "Please select a template.", "warning");
            return false;
        }
        if (iStep === 2 && !_dictWizardData.sProjectName) {
            PipeleyenApp.fnShowToast(
                "Project name is required.", "warning");
            return false;
        }
        return true;
    }

    async function _fnSubmitCreateProject() {
        var elButton = document.getElementById("btnWizardNext");
        elButton.disabled = true;
        elButton.textContent = "Creating...";
        try {
            await VaibifyApi.fdictPost(
                "/api/projects/create", _dictWizardData);
            _fnCloseWizard();
            PipeleyenApp.fnShowToast(
                "Project created successfully.");
            PipeleyenApp.fnLoadContainers();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                PipeleyenApp.fsSanitizeErrorForUser(
                    error.message), "error");
        } finally {
            elButton.disabled = false;
            elButton.textContent = "Create";
        }
    }

    return {
        fnRenderWorkflowList: fnRenderWorkflowList,
        fnCreateNewWorkflow: fnCreateNewWorkflow,
        fnSelectWorkflow: fnSelectWorkflow,
        fnToggleWorkflowDropdown: fnToggleWorkflowDropdown,
        fnHideWorkflowDropdown: fnHideWorkflowDropdown,
        fnSaveCurrentWorkflow: fnSaveCurrentWorkflow,
        fnOpenCreateWizard: fnOpenCreateWizard,
        fnBindCreateWizardModal: fnBindCreateWizardModal,
    };
})();
