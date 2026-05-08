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
            VaibifyUtilities.fnEscapeHtml(dictWf.sPath) + '">' +
            '<span class="name">' +
            VaibifyUtilities.fnEscapeHtml(dictWf.sName) +
            '</span>' +
            '<span class="image">' +
            VaibifyUtilities.fnEscapeHtml(sRepo) + '</span></div>'
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
        VaibifyNewWorkflowWizard.fnLaunch(sContainerId);
    }

    /* --- Workflow Selection --- */

    function _fdictFetchWorkflow(sId, sPath) {
        return VaibifyApi.fdictPostRaw(
            "/api/connect/" + sId +
            "?sWorkflowPath=" + encodeURIComponent(sPath)
        );
    }

    async function fnSelectWorkflow(
        sId, sWorkflowPathArg, sWorkflowName
    ) {
        try {
            var dictResult = await _fdictFetchWorkflow(
                sId, sWorkflowPathArg);
            PipeleyenApp.fnActivateWorkflow(
                sId, dictResult, sWorkflowName);
            fnCheckOriginDrift(sId, false);
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        }
    }

    var _bRefreshing = false;

    async function fnRefreshWorkflow() {
        if (_bRefreshing) return;
        var sId = PipeleyenApp.fsGetContainerId();
        var sPath = PipeleyenApp.fsGetWorkflowPath();
        if (!sId || !sPath) return;
        _bRefreshing = true;
        try {
            var dictResult = await _fdictFetchWorkflow(
                sId, sPath);
            PipeleyenApp.fnRefreshWorkflowData(dictResult);
            await fnCheckOriginDrift(sId, false);
            PipeleyenApp.fnShowToast(
                "Workflow refreshed", "info");
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        } finally {
            _bRefreshing = false;
        }
    }

    async function fnCheckOriginDrift(sId, bForce) {
        if (!sId) return null;
        try {
            var dictStatus = await VaibifyApi.fdictPost(
                "/api/git/" + sId + "/fetch-project-repo",
                { bForce: !!bForce }
            );
            _fnRenderDriftBanner(sId, dictStatus, null);
            return dictStatus;
        } catch (error) {
            _fnHideDriftBanner();
            return null;
        }
    }

    async function fnPullProjectRepo() {
        var sId = PipeleyenApp.fsGetContainerId();
        if (!sId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/git/" + sId + "/pull-project-repo", {}
            );
            if (dictResult && dictResult.sRefusal) {
                _fnRenderDriftBanner(sId, null, dictResult);
                return;
            }
            PipeleyenApp.fnShowToast(
                "Pulled to " + (dictResult.sNewHeadSha || "").slice(0, 7),
                "success"
            );
            _fnHideDriftBanner();
            await fnRefreshWorkflow();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        }
    }

    function _fnRenderDriftBanner(sId, dictStatus, dictRefusal) {
        var elBanner = document.getElementById("driftBanner");
        if (!elBanner) return;
        if (dictRefusal && dictRefusal.sRefusal === "dirty-working-tree") {
            elBanner.classList.add("dirty");
            elBanner.innerHTML = _fsBuildDirtyMarkup(dictRefusal);
            elBanner.hidden = false;
            _fnAttachDriftBannerDismiss(elBanner);
            return;
        }
        var iBehind = (dictStatus && dictStatus.iBehind) || 0;
        if (!iBehind) {
            _fnHideDriftBanner();
            return;
        }
        elBanner.classList.remove("dirty");
        elBanner.innerHTML = _fsBuildBehindMarkup(dictStatus);
        elBanner.hidden = false;
        var elPull = elBanner.querySelector(".drift-banner-pull");
        if (elPull) {
            elPull.addEventListener("click", fnPullProjectRepo);
        }
        _fnAttachDriftBannerDismiss(elBanner);
    }

    function _fnAttachDriftBannerDismiss(elBanner) {
        var elDismiss = elBanner.querySelector(".drift-banner-dismiss");
        if (elDismiss) {
            elDismiss.addEventListener("click", _fnHideDriftBanner);
        }
    }

    function _fsBuildBehindMarkup(dictStatus) {
        var sBranch = (dictStatus.sBranch || "main");
        var iBehind = dictStatus.iBehind || 0;
        var sCommits = iBehind === 1 ? "commit" : "commits";
        var sMessage = "Container is " + iBehind + " " +
            sCommits + " behind origin/" +
            VaibifyUtilities.fnEscapeHtml(sBranch) + ".";
        return '<div class="drift-banner-message">' + sMessage +
            '</div><div class="drift-banner-actions">' +
            '<button type="button" class="drift-banner-pull">Pull</button>' +
            '<button type="button" class="drift-banner-dismiss" ' +
            'aria-label="Dismiss drift banner">×</button>' +
            '</div>';
    }

    function _fsBuildDirtyMarkup(dictRefusal) {
        var listFiles = dictRefusal.listDirtyFiles || [];
        var listItems = listFiles.slice(0, 5).map(function (sPath) {
            return "<li>" + VaibifyUtilities.fnEscapeHtml(sPath) +
                "</li>";
        });
        if (listFiles.length > 5) {
            listItems.push("<li>(+" +
                (listFiles.length - 5) + " more)</li>");
        }
        return '<div class="drift-banner-message">' +
            'Cannot fast-forward: working tree has uncommitted ' +
            'changes. Commit or revert these files, then click ' +
            'Pull again.<ul class="drift-banner-dirty-list">' +
            listItems.join("") + '</ul></div>' +
            '<div class="drift-banner-actions">' +
            '<button type="button" class="drift-banner-dismiss" ' +
            'aria-label="Dismiss drift banner">×</button>' +
            '</div>';
    }

    function _fnHideDriftBanner() {
        var elBanner = document.getElementById("driftBanner");
        if (!elBanner) return;
        elBanner.hidden = true;
        elBanner.innerHTML = "";
        elBanner.classList.remove("dirty");
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
        var sHtml = '<div class="workflow-dropdown-item new-workflow"'
            + ' data-action="newWorkflow">'
            + '<span class="wf-name">+ New Workflow&hellip;</span>'
            + '</div>';
        sHtml += '<div class="workflow-dropdown-item' +
            (bInNoWorkflow ? " current" : "") +
            '" data-action="noWorkflow">' +
            '<span class="wf-name">No Workflow</span></div>';
        sHtml += listWorkflows.map(function (dictWf) {
            var bCurrent = dictWf.sPath === sWorkflowPath;
            return (
                '<div class="workflow-dropdown-item' +
                (bCurrent ? " current" : "") +
                '" data-path="' +
                VaibifyUtilities.fnEscapeHtml(dictWf.sPath) +
                '" data-name="' +
                VaibifyUtilities.fnEscapeHtml(dictWf.sName) +
                '">' +
                '<span class="wf-name">' +
                VaibifyUtilities.fnEscapeHtml(dictWf.sName) +
                '</span>' +
                '<span class="wf-path">' +
                VaibifyUtilities.fnEscapeHtml(dictWf.sPath) +
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
                    if (el.dataset.action === "newWorkflow") {
                        VaibifyNewWorkflowWizard.fnLaunch(
                            PipeleyenApp.fsGetContainerId());
                        return;
                    }
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
        "Features & Authentication",
        "Packages",
        "Summary",
    ];
    var _I_WIZARD_LAST_STEP = 7;
    var _LIST_WIZARD_HELP = [
        '<p>The folder on your host machine where vaibify writes ' +
        '<code>vaibify.yml</code> and stores any project files. This ' +
        'becomes the canonical location of the project &mdash; the ' +
        'registry points here, and you edit configuration here later.</p>' +
        '<p>Use the picker to navigate to an existing folder, or click ' +
        '<strong>+ New folder</strong> to create one. A common location ' +
        'is <code>~/src/</code>.</p>',

        '<p>A starter set of files that determines the initial shape ' +
        'of your project. Two templates ship with vaibify:</p>' +
        '<p><strong>sandbox</strong> &mdash; a blank workspace for ' +
        'ad-hoc exploration. Use this when you have one project ' +
        'repository or just want a clean environment to work in.</p>' +
        '<p><strong>toolkit</strong> &mdash; a workspace for developing ' +
        'several peer code repositories side-by-side. Use this when ' +
        'you are actively editing multiple libraries that depend on ' +
        'each other and want each one to appear in the Repos panel ' +
        'with its own git status and push controls.</p>' +
        '<p>Pick <strong>sandbox</strong> if you are not sure. You can ' +
        'restructure later by editing <code>vaibify.yml</code>.</p>',

        '<p>The name used for the Docker container, image, and registry ' +
        'entry. Must be unique across all your vaibify projects on this ' +
        'host.</p>' +
        '<p>This is how you refer to the container in CLI commands ' +
        '(<code>vaibify connect -p &lt;name&gt;</code>) and how it ' +
        'appears in the GUI container list. Defaults to the directory ' +
        'name; lowercase letters, digits, and hyphens are safest.</p>',

        '<p>The version of Python installed inside the container. ' +
        'Vaibify supports 3.9 through 3.14 and uses ' +
        '<strong>3.12</strong> by default.</p>' +
        '<p><strong>Why vaibify needs to know this:</strong> Python is ' +
        'special among the languages vaibify supports because it is ' +
        'the language vaibify\'s own internal tooling runs in (the ' +
        'pipeline runner, test scaffolding, data introspection, and ' +
        'plot helpers all live in Python). The Dockerfile installs ' +
        '<code>python${PYTHON_VERSION}</code>, ' +
        '<code>python${PYTHON_VERSION}-dev</code>, and the matching ' +
        '<code>venv</code> from apt at build time, then makes that the ' +
        'default <code>python</code> binary in the container. R and ' +
        'Julia (enabled separately under Features) install whatever ' +
        'version their respective package managers ship with &mdash; ' +
        'you do not pick those.</p>' +
        '<p><strong>Which one to choose:</strong> if your code is ' +
        'tested against a specific Python version, pick that. ' +
        'Otherwise leave it at 3.12 &mdash; it is the most widely ' +
        'compatible release for current scientific Python libraries.</p>',

        '<p>A list of git URLs that will be cloned into ' +
        '<code>/workspace/&lt;repo-name&gt;</code> inside the container ' +
        'at startup. Public HTTPS URLs work without any setup; private ' +
        'repos need the GitHub authentication toggle on the next step ' +
        '(also required for pushing to any repo, public or private).</p>' +
        '<p><strong>How repos are installed:</strong> by default, the ' +
        'wizard sets each repo\'s install method to ' +
        '<code>pip_editable</code>, which runs <code>pip install -e .</code> ' +
        'so the repo\'s declared Python dependencies are auto-resolved. ' +
        'This is the right default for Python codebases.</p>' +
        '<p>Other install methods are available by editing ' +
        '<code>vaibify.yml</code> after creation: <code>c_and_pip</code> ' +
        'for Python repos with C extensions that need <code>make</code> ' +
        'first; <code>pip_no_deps</code> if you want to manage ' +
        'dependencies separately; <code>scripts_only</code> for ' +
        'non-Python repos (just clones and adds to <code>PATH</code> ' +
        'and <code>PYTHONPATH</code>); <code>reference</code> to clone ' +
        'without installing at all.</p>' +
        '<p>You can add, remove, or change install methods later by ' +
        'editing <code>vaibify.yml</code> and rebuilding.</p>',

        '<p>Optional components installed inside the container image at ' +
        'build time, plus the GitHub credential toggle.</p>' +
        '<p><strong>Features</strong>: each checkbox bakes one tool into ' +
        'the image. <strong>Claude Code CLI</strong> lets you run Claude ' +
        'inside the container. <strong>JupyterLab</strong>, ' +
        '<strong>LaTeX</strong>, <strong>R</strong>, <strong>Julia</strong>, ' +
        '<strong>PostgreSQL client</strong>, <strong>DVC</strong>, and ' +
        '<strong>NVIDIA GPU</strong> are independent toggles. Enabling a ' +
        'feature adds build time but makes the tool immediately available.</p>' +
        '<p><strong>GitHub authentication</strong>: delegates to your ' +
        'host\'s <code>gh auth</code> to provide a token. Required for ' +
        'pushing to any repository (public or private) and for cloning ' +
        'private repositories. Leave this on unless you are certain you ' +
        'will never need to push from inside the container. If ' +
        '<code>gh auth login</code> is not set up on your host, the ' +
        'container will still work but git push will fail.</p>' +
        '<p>The <strong>macOS sleep prevention</strong> toggle (only ' +
        'shown on macOS) is recommended for long builds. The ' +
        '<strong>network isolation</strong> toggle blocks all outbound ' +
        'traffic from the container &mdash; useful for running untrusted ' +
        'code, but breaks anything that needs the internet.</p>',

        '<p>Packages installed in the container image at build time. ' +
        'Vaibify directly supports three package ecosystems:</p>' +
        '<p><strong>System packages</strong> &mdash; Ubuntu/Debian ' +
        'libraries installed via <code>apt</code>. Use this for things ' +
        'like <code>gfortran</code>, <code>libhdf5-dev</code>, or ' +
        '<code>cmake</code>.</p>' +
        '<p><strong>Python packages</strong> &mdash; installed via the ' +
        'package manager you choose in the Advanced section: ' +
        '<code>pip</code> (default), <code>conda</code>, or ' +
        '<code>mamba</code>. The Advanced section also has a separate ' +
        '<strong>Conda packages</strong> textarea that is only used when ' +
        'the manager is set to conda or mamba.</p>' +
        '<p><strong>Most users leave both textareas empty.</strong> For ' +
        'Python projects, dependencies are pulled in automatically when ' +
        'your listed repos are installed via <code>pip install -e .</code> ' +
        '&mdash; pip reads each repo\'s <code>setup.py</code> or ' +
        '<code>pyproject.toml</code> and installs everything declared ' +
        'there. You only need this textarea for ad-hoc packages your ' +
        'scripts in <code>/workspace</code> import that no installed ' +
        'repo depends on.</p>' +
        '<p><strong>Other languages</strong> (R, Julia, Rust, Node.js, ' +
        'etc.) are not directly configurable here. If you enabled the ' +
        'R or Julia feature on the previous step, the language runtime ' +
        'is installed but you install language-specific packages from ' +
        'inside the running container using that language\'s native ' +
        'tooling: <code>install.packages()</code> or ' +
        '<code>BiocManager::install()</code> for R, ' +
        '<code>Pkg.add()</code> for Julia, <code>cargo add</code> for ' +
        'Rust, <code>npm install</code> for Node, etc.</p>' +
        '<p>If you discover a missing package later, you can open a ' +
        'terminal in the container and install it directly (ephemeral ' +
        '&mdash; gone on rebuild), or edit <code>vaibify.yml</code> and ' +
        'add it to the right list, then click Rebuild (permanent). The ' +
        '<strong>Advanced</strong> section also exposes pip flags, ' +
        'container user, base image, and workspace root &mdash; all are ' +
        'safe to leave at their defaults.</p>',

        '<p>Review your selections before creating the project. Clicking ' +
        '<strong>Create</strong> writes <code>vaibify.yml</code> to your ' +
        'project directory and registers the project with vaibify.</p>' +
        '<p>The container itself is <em>not</em> built yet &mdash; that ' +
        'happens when you click the project tile on the landing page. ' +
        'Nothing here is permanent: you can edit <code>vaibify.yml</code> ' +
        'directly after creation, or remove the project from the registry ' +
        'and start over.</p>',
    ];
    var _LIST_FEATURE_DEFINITIONS = [
        {sKey: "claude", sLabel: "Claude Code CLI",
         sHint: "Install the Claude Code agent inside the container."},
        {sKey: "jupyter", sLabel: "JupyterLab",
         sHint: "Install JupyterLab for notebook-based work."},
        {sKey: "latex", sLabel: "LaTeX (TeX Live)",
         sHint: "Install TeX Live for paper writing."},
        {sKey: "rLanguage", sLabel: "R language",
         sHint: "Install R and the IRkernel for Jupyter."},
        {sKey: "julia", sLabel: "Julia",
         sHint: "Install the Julia language."},
        {sKey: "database", sLabel: "PostgreSQL client",
         sHint: "Install the psql command-line client."},
        {sKey: "dvc", sLabel: "DVC",
         sHint: "Install DVC for data versioning."},
        {sKey: "gpu", sLabel: "NVIDIA GPU",
         sHint: "Requires NVIDIA GPU and nvidia-container-toolkit on host."},
    ];
    var _LIST_DEFAULT_FEATURES = ["latex"];

    function fnOpenCreateWizard() {
        _iWizardStep = 0;
        _dictWizardData = _fdictBuildDefaultWizardData();
        document.getElementById("modalCreateWizard")
            .style.display = "flex";
        _fnRenderWizardStep(_iWizardStep);
    }

    function _fdictBuildDefaultWizardData() {
        var dictData = _fdictBuildBasicWizardDefaults();
        dictData.listFeatures = _LIST_DEFAULT_FEATURES.slice();
        dictData.bUseGithubAuth = true;
        dictData.bNeverSleep = _fbIsMacOs();
        dictData.bNetworkIsolation = false;
        return _fdictExtendWithPackageDefaults(dictData);
    }

    function _fdictBuildBasicWizardDefaults() {
        return {
            sDirectory: "",
            sTemplateName: "",
            sProjectName: "",
            sPythonVersion: "3.12",
            listRepositories: [],
        };
    }

    function _fdictExtendWithPackageDefaults(dictData) {
        dictData.listSystemPackages = [];
        dictData.listPythonPackages = [];
        dictData.listCondaPackages = [];
        dictData.sPackageManager = "pip";
        dictData.sPipInstallFlags = "";
        dictData.sContainerUser = "researcher";
        dictData.sBaseImage = "ubuntu:24.04";
        dictData.sWorkspaceRoot = "/workspace";
        return dictData;
    }

    function _fbIsMacOs() {
        var sPlatform = (navigator.platform || "").toLowerCase();
        var sUserAgent = (navigator.userAgent || "").toLowerCase();
        return sPlatform.indexOf("mac") !== -1 ||
            sUserAgent.indexOf("mac os") !== -1;
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
        var elHelp = document.getElementById("btnWizardHelp");
        if (elHelp) {
            elHelp.addEventListener("click", _fnHandleWizardHelpClick);
        }
    }

    function _fnHandleWizardHelpClick() {
        var sTitle = _LIST_WIZARD_TITLES[_iWizardStep] + " — Help";
        var sBody = _LIST_WIZARD_HELP[_iWizardStep] || "";
        PipeleyenModals.fnShowInfoModal(sTitle, sBody);
        _fnRaiseInfoModalAboveWizard();
    }

    function _fnRaiseInfoModalAboveWizard() {
        var elInfo = document.getElementById("modalInfo");
        if (elInfo) elInfo.style.zIndex = "1200";
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
        if (_iWizardStep >= _I_WIZARD_LAST_STEP) {
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
            _fnRenderStepFeatures,
            _fnRenderStepPackages,
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
            iStep === _I_WIZARD_LAST_STEP ? "Create" : "Next";
    }

    function _fnRenderStepDirectory(elContent) {
        var sCurrent = _dictWizardData.sDirectory ||
            "(none selected)";
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project Directory</label>' +
            '<button type="button" class="btn" ' +
            'id="btnWizardChooseDirectory">' +
            'Choose Directory...</button>' +
            '<div class="wizard-selected-path" ' +
            'id="wizardSelectedPath">' +
            VaibifyUtilities.fnEscapeHtml(sCurrent) +
            '</div></div>';
        document.getElementById("btnWizardChooseDirectory")
            .addEventListener("click",
                _fnHandleChooseDirectoryClick);
    }

    function _fnHandleChooseDirectoryClick() {
        PipeleyenDirectoryBrowser.fnOpenForCreate(
            _fnApplyChosenDirectory);
    }

    function _fnApplyChosenDirectory(sChosenPath) {
        _dictWizardData.sDirectory = sChosenPath;
        var elLabel = document.getElementById("wizardSelectedPath");
        if (elLabel) {
            elLabel.textContent = sChosenPath;
        }
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
                    VaibifyUtilities.fnEscapeHtml(sName) + '"' +
                    sActive + '>' +
                    '<div class="add-choice-title">' +
                    VaibifyUtilities.fnEscapeHtml(sName) +
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

    function _fbIsToolkit() {
        return _dictWizardData.sTemplateName === "toolkit";
    }

    function _fsRepositoriesLabel() {
        return _fbIsToolkit()
            ? "Repositories to clone (one per line)"
            : "Repositories (one per line, optional)";
    }

    function _fsRepositoriesPlaceholder() {
        if (_fbIsToolkit()) {
            return "https://github.com/org/first.git\n" +
                "https://github.com/org/second.git\n" +
                "https://github.com/org/third.git";
        }
        return "https://github.com/org/repo.git";
    }

    function _fsRepositoriesHelperText() {
        if (!_fbIsToolkit()) return "";
        return '<p class="muted-text" ' +
            'style="font-size:12px;margin-top:6px;">' +
            'These repos will be cloned into /workspace ' +
            'and auto-tracked.</p>';
    }

    function _fnRenderStepRepositories(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>' + _fsRepositoriesLabel() + '</label>' +
            '<textarea id="inputWizardRepos" rows="5" ' +
            'placeholder="' + _fsRepositoriesPlaceholder() +
            '"></textarea>' +
            _fsRepositoriesHelperText() +
            '</div>';
        document.getElementById("inputWizardRepos").value =
            _dictWizardData.listRepositories.join("\n");
    }

    function _fnRenderStepFeatures(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Container features</label>' +
            '<div id="wizardFeatureList" class="wizard-feature-list">' +
            _LIST_FEATURE_DEFINITIONS.map(_fsRenderFeatureRow).join("") +
            '</div></div>' +
            _fsRenderAuthSection() +
            _fsRenderRuntimeTogglesSection();
    }

    function _fsRenderFeatureRow(dictFeature) {
        var bChecked =
            _dictWizardData.listFeatures.indexOf(dictFeature.sKey) !== -1;
        return '<label class="wizard-feature-row" title="' +
            VaibifyUtilities.fnEscapeHtml(dictFeature.sHint) + '">' +
            '<input type="checkbox" class="wizard-feature-input" ' +
            'data-feature="' +
            VaibifyUtilities.fnEscapeHtml(dictFeature.sKey) + '"' +
            (bChecked ? " checked" : "") + '>' +
            '<span>' +
            VaibifyUtilities.fnEscapeHtml(dictFeature.sLabel) +
            '</span></label>';
    }

    function _fsRenderAuthSection() {
        var bChecked = _dictWizardData.bUseGithubAuth !== false;
        return '<div class="form-group"><label>' +
            'GitHub authentication</label>' +
            '<label class="wizard-toggle-row" title="Required for ' +
            'pushing to repos and accessing private repos.">' +
            '<input type="checkbox" id="wizardUseGithubAuth"' +
            (bChecked ? " checked" : "") + '>' +
            '<span>GitHub authentication ' +
            '(push to repos, access private repos)' +
            '</span></label></div>';
    }

    function _fsRenderRuntimeTogglesSection() {
        var sNeverSleep = _fbIsMacOs()
            ? _fsRenderNeverSleepRow() : "";
        return '<div class="form-group">' +
            '<label>Runtime options</label>' +
            sNeverSleep + _fsRenderNetworkIsolationRow() + '</div>';
    }

    function _fsRenderNeverSleepRow() {
        var bChecked = _dictWizardData.bNeverSleep === true;
        return '<label class="wizard-toggle-row" title="macOS only: ' +
            'runs caffeinate to prevent sleep during long builds.">' +
            '<input type="checkbox" id="wizardNeverSleep"' +
            (bChecked ? " checked" : "") + '>' +
            '<span>Prevent macOS from sleeping ' +
            'during long builds</span></label>';
    }

    function _fsRenderNetworkIsolationRow() {
        var bChecked = _dictWizardData.bNetworkIsolation === true;
        return '<label class="wizard-toggle-row" title="Blocks all ' +
            'outbound network traffic from the container.">' +
            '<input type="checkbox" id="wizardNetworkIsolation"' +
            (bChecked ? " checked" : "") + '>' +
            '<span>Network isolation ' +
            '(block outbound traffic)</span></label>';
    }

    function _fnRenderStepPackages(elContent) {
        elContent.innerHTML =
            _fsRenderPackageTextarea(
                "wizardSystemPackages", "System packages (apt)",
                "gfortran\nlibhdf5-dev\ncmake",
                _dictWizardData.listSystemPackages) +
            _fsRenderPackageTextarea(
                "wizardPythonPackages", "Python packages (pip)",
                "numpy\nmatplotlib\npandas",
                _dictWizardData.listPythonPackages) +
            _fsRenderPackagesAdvancedSection();
    }

    function _fsRenderPackageTextarea(sId, sLabel, sPlaceholder, listValues) {
        var sValue = (listValues || []).join("\n");
        return '<div class="form-group"><label>' +
            VaibifyUtilities.fnEscapeHtml(sLabel) + '</label>' +
            '<textarea id="' + sId + '" rows="4" placeholder="' +
            VaibifyUtilities.fnEscapeHtml(sPlaceholder) + '">' +
            VaibifyUtilities.fnEscapeHtml(sValue) +
            '</textarea><div class="wizard-helper-text">' +
            'One per line.</div></div>';
    }

    function _fsRenderPackagesAdvancedSection() {
        return '<details class="wizard-advanced">' +
            '<summary>Advanced</summary>' +
            _fsRenderPackageManagerSelect() +
            _fsRenderPackageTextarea(
                "wizardCondaPackages",
                "Conda packages (only when manager is conda/mamba)",
                "scipy\nh5py", _dictWizardData.listCondaPackages) +
            _fsRenderAdvancedTextInputs() + '</details>';
    }

    function _fsRenderPackageManagerSelect() {
        var listOptions = ["pip", "conda", "mamba"];
        var sCurrent = _dictWizardData.sPackageManager || "pip";
        var sOptions = listOptions.map(function (sValue) {
            var sSelected = sValue === sCurrent ? " selected" : "";
            return '<option value="' + sValue + '"' + sSelected +
                '>' + sValue + '</option>';
        }).join("");
        return '<div class="form-group"><label>' +
            'Package manager</label><select ' +
            'id="wizardPackageManager">' + sOptions +
            '</select></div>';
    }

    function _fsRenderAdvancedTextInputs() {
        return _fsRenderTextInput(
            "wizardPipFlags", "Pip install flags",
            "--no-build-isolation",
            _dictWizardData.sPipInstallFlags) +
            _fsRenderTextInput(
                "wizardContainerUser", "Container user",
                "researcher",
                _dictWizardData.sContainerUser) +
            _fsRenderTextInput(
                "wizardBaseImage", "Base image",
                "ubuntu:24.04",
                _dictWizardData.sBaseImage) +
            _fsRenderTextInput(
                "wizardWorkspaceRoot", "Workspace root",
                "/workspace",
                _dictWizardData.sWorkspaceRoot);
    }

    function _fsRenderTextInput(sId, sLabel, sPlaceholder, sValue) {
        return '<div class="form-group"><label>' +
            VaibifyUtilities.fnEscapeHtml(sLabel) + '</label>' +
            '<input type="text" id="' + sId + '" ' +
            'placeholder="' +
            VaibifyUtilities.fnEscapeHtml(sPlaceholder) + '" ' +
            'value="' +
            VaibifyUtilities.fnEscapeHtml(sValue || "") + '">' +
            '</div>';
    }

    // TODO: add a "track project dir" checkbox so /workspace itself
    // can appear in the Repos panel. Deferred: /workspace is not a
    // top-level subdirectory and is not part of flistDiscoverGitDirs
    // (which walks /workspace/<name>/.git at depth 2).
    function _fnRenderStepSummary(elContent) {
        elContent.innerHTML =
            '<div class="wizard-summary-block">' +
            _fsSummaryBasics() + _fsSummaryReposLine() +
            _fsSummaryFeaturesLine() + _fsSummaryAuthLine() +
            _fsSummaryPackagesLines() + _fsSummaryToggleLines() +
            '</div>';
    }

    function _fsSummaryBasics() {
        return _fsSummaryRow("Directory", _dictWizardData.sDirectory) +
            _fsSummaryRow("Template", _dictWizardData.sTemplateName) +
            _fsSummaryRow("Project Name",
                _dictWizardData.sProjectName) +
            _fsSummaryRow("Python", _dictWizardData.sPythonVersion);
    }

    function _fsSummaryRow(sLabel, sValue) {
        return '<p><strong>' +
            VaibifyUtilities.fnEscapeHtml(sLabel) + ':</strong> ' +
            VaibifyUtilities.fnEscapeHtml(sValue || "") + '</p>';
    }

    function _fsSummaryReposLine() {
        var listRepos = _dictWizardData.listRepositories || [];
        var sValue = listRepos.length > 0
            ? listRepos.join(", ") : "None";
        return _fsSummaryRow("Repositories", sValue);
    }

    function _fsSummaryFeaturesLine() {
        var listFeatures = _dictWizardData.listFeatures || [];
        var sValue = listFeatures.length > 0
            ? listFeatures.join(", ") : "None";
        return _fsSummaryRow("Features", sValue);
    }

    function _fsSummaryAuthLine() {
        var sValue = _dictWizardData.bUseGithubAuth
            ? "Yes (gh_token via gh auth)" : "No";
        return _fsSummaryRow("GitHub auth", sValue);
    }

    function _fsSummaryPackagesLines() {
        var sSystem = (_dictWizardData.listSystemPackages || [])
            .join(", ") || "(template defaults)";
        var sPython = (_dictWizardData.listPythonPackages || [])
            .join(", ") || "None";
        return _fsSummaryRow("System packages", sSystem) +
            _fsSummaryRow("Python packages", sPython) +
            _fsSummaryRow("Package manager",
                _dictWizardData.sPackageManager);
    }

    function _fsSummaryToggleLines() {
        var sNeverSleep = _fbIsMacOs()
            ? _fsSummaryRow("Prevent macOS sleep",
                _dictWizardData.bNeverSleep ? "Yes" : "No") : "";
        return sNeverSleep + _fsSummaryRow(
            "Network isolation",
            _dictWizardData.bNetworkIsolation ? "Yes" : "No");
    }

    function _fnSaveCurrentStepData() {
        _fnSaveBasicStepFields();
        _fnSaveFeaturesAndToggles();
        _fnSavePackagesAndAdvanced();
    }

    function _fnSaveBasicStepFields() {
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
            _dictWizardData.listRepositories = _flistSplitLines(
                elRepos.value);
        }
    }

    function _fnSaveFeaturesAndToggles() {
        var listChecks = document.querySelectorAll(
            ".wizard-feature-input");
        if (listChecks.length > 0) {
            _dictWizardData.listFeatures = _flistCheckedFeatures(
                listChecks);
        }
        _fnReadCheckboxInto("wizardUseGithubAuth", "bUseGithubAuth");
        _fnReadCheckboxInto("wizardNeverSleep", "bNeverSleep");
        _fnReadCheckboxInto("wizardNetworkIsolation",
            "bNetworkIsolation");
    }

    function _fnReadCheckboxInto(sId, sField) {
        var elCheck = document.getElementById(sId);
        if (elCheck) _dictWizardData[sField] = elCheck.checked;
    }

    function _flistCheckedFeatures(listChecks) {
        var listResult = [];
        listChecks.forEach(function (elCheck) {
            if (elCheck.checked) {
                listResult.push(elCheck.dataset.feature);
            }
        });
        return listResult;
    }

    function _fnSavePackagesAndAdvanced() {
        _fnReadTextareaIntoList(
            "wizardSystemPackages", "listSystemPackages");
        _fnReadTextareaIntoList(
            "wizardPythonPackages", "listPythonPackages");
        _fnReadTextareaIntoList(
            "wizardCondaPackages", "listCondaPackages");
        _fnReadInputInto("wizardPackageManager", "sPackageManager");
        _fnReadInputInto("wizardPipFlags", "sPipInstallFlags");
        _fnReadInputInto("wizardContainerUser", "sContainerUser");
        _fnReadInputInto("wizardBaseImage", "sBaseImage");
        _fnReadInputInto("wizardWorkspaceRoot", "sWorkspaceRoot");
    }

    function _fnReadTextareaIntoList(sId, sField) {
        var elArea = document.getElementById(sId);
        if (elArea) {
            _dictWizardData[sField] = _flistSplitLines(elArea.value);
        }
    }

    function _fnReadInputInto(sId, sField) {
        var elInput = document.getElementById(sId);
        if (elInput) _dictWizardData[sField] = elInput.value.trim();
    }

    function _flistSplitLines(sText) {
        return sText.split("\n")
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return s.length > 0; });
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
        if (iStep === 4 && _fbIsToolkit() &&
            _dictWizardData.listRepositories.length === 0) {
            PipeleyenApp.fnShowToast(
                "Toolkit containers require at least one " +
                "repository URL.", "warning");
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
            PipeleyenContainerManager.fnLoadContainers();
        } catch (error) {
            PipeleyenApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
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
        fnRefreshWorkflow: fnRefreshWorkflow,
        fnCheckOriginDrift: fnCheckOriginDrift,
        fnPullProjectRepo: fnPullProjectRepo,
        fnToggleWorkflowDropdown: fnToggleWorkflowDropdown,
        fnHideWorkflowDropdown: fnHideWorkflowDropdown,
        fnSaveCurrentWorkflow: fnSaveCurrentWorkflow,
        fnOpenCreateWizard: fnOpenCreateWizard,
        fnBindCreateWizardModal: fnBindCreateWizardModal,
    };
})();
