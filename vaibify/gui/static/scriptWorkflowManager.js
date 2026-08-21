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
                'No projects found. Create one to get ' +
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
        var iSize = dictWf.iSizeBytes || 0;
        return (
            '<div class="container-card" data-path="' +
            VaibifyUtilities.fnEscapeHtml(dictWf.sPath) +
            '" data-size-bytes="' + iSize + '">' +
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
                    var iSize = parseInt(
                        el.dataset.sizeBytes || "0", 10);
                    fnSelectWorkflow(sId, sPath, sName, iSize);
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

    /* The connect handler is gated on the owner-of-record lease, the
     * same principal the WebSockets present, so a second browser tab
     * cannot bypass the claim route's 409 and take the workflow. The
     * lease rides the X-Vaibify-Lease header the authenticated-fetch
     * wrapper attaches, never a query param. */
    function _fdictFetchWorkflow(sId, sPath) {
        return VaibifyApi.fdictPostRaw(
            "/api/connect/" + sId +
            "?sWorkflowPath=" + encodeURIComponent(sPath)
        );
    }

    /* Workflows over this byte threshold get a loading banner on
     * selection so the researcher knows the silence is real work,
     * not a frozen UI. Empirically a 1 MB project.json takes tens of
     * seconds to round-trip + render, while sub-100 KB workflows
     * arrive fast enough that a banner would just flash distractingly. */
    var _I_LARGE_WORKFLOW_BYTES = 100 * 1024;

    function _fnShowLargeWorkflowLoadingBanner(sWorkflowName, iSizeBytes) {
        var elBanner = document.getElementById("workflowLoadingBanner");
        if (!elBanner) return;
        var elText = document.getElementById("workflowLoadingText");
        if (elText) {
            var sSize = (iSizeBytes / 1024).toFixed(0) + " KB";
            elText.textContent = "Loading " + sWorkflowName +
                " (" + sSize + ") — this can take a moment...";
        }
        elBanner.hidden = false;
    }

    function _fnHideLargeWorkflowLoadingBanner() {
        var elBanner = document.getElementById("workflowLoadingBanner");
        if (elBanner) elBanner.hidden = true;
    }

    /* Incremented on every workflow selection. A load or refresh that
     * started before the current generation is stale: the user switched
     * workflows while it was in flight, so applying its result would
     * overwrite the workflow they actually switched to (the double-click
     * / switch-during-load race). The server-side workflow-identity check
     * that rejects a stale WRITE lands with the A1 enforcement layer; this
     * is the rendering-side guard that keeps a stale READ off the screen. */
    var _iWorkflowGeneration = 0;

    async function fnSelectWorkflow(
        sId, sWorkflowPathArg, sWorkflowName, iSizeBytes
    ) {
        var iSize = iSizeBytes || 0;
        var bShowBanner = iSize >= _I_LARGE_WORKFLOW_BYTES;
        if (bShowBanner) {
            _fnShowLargeWorkflowLoadingBanner(sWorkflowName, iSize);
        }
        _iWorkflowGeneration += 1;
        var iThisGeneration = _iWorkflowGeneration;
        try {
            var dictResult = await _fdictFetchWorkflow(
                sId, sWorkflowPathArg);
            if (iThisGeneration !== _iWorkflowGeneration) return;
            VaibifyApp.fnActivateWorkflow(
                sId, dictResult, sWorkflowName);
            fnCheckOriginDrift(sId, false);
        } catch (error) {
            if (iThisGeneration !== _iWorkflowGeneration) return;
            if (_fbClaimWasLost(error)) {
                if (await _fbReclaimAndRetryOnce(
                    sId, sWorkflowPathArg, sWorkflowName,
                    iThisGeneration
                )) return;
                _fnReturnToProjectList(error);
                return;
            }
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        } finally {
            if (iThisGeneration === _iWorkflowGeneration) {
                _fnHideLargeWorkflowLoadingBanner();
            }
        }
    }

    var _S_REFUSAL_CLAIM_REQUIRED = "claim-required";

    function _fbClaimWasLost(error) {
        /* Keyed on the machine-readable code, never the prose: the
           sibling 409 ("in use in another browser session") has no
           recovery to offer, and a recovery keyed on the word "claim"
           would fire for it too. */
        var dictDetail = (error && error.dictDetail) || {};
        return dictDetail.sRefusal === _S_REFUSAL_CLAIM_REQUIRED;
    }

    async function _fbReclaimAndRetryOnce(
        sId, sWorkflowPathArg, sWorkflowName, iThisGeneration
    ) {
        /* The refusal's named recovery is "select the project again to
           claim it" -- a claim plus a retry, which the dashboard can
           run itself. The reaper collects a claim after thirty
           socket-less seconds and the workflow picker holds no socket,
           so a researcher who paused to read the list met this refusal
           on their very next click (live report, 2026-08-20).
           Arbitration still governs the reclaim: a project another
           vaibify process holds refuses it, and the caller then walks
           back to the project list as before. */
        var sName = VaibifyContainerManager.fsGetSelectedContainerName();
        if (!sName) return false;
        var bClaimed =
            await VaibifyContainerManager.fbClaimContainer(sName);
        if (!bClaimed) return false;
        var dictResult;
        try {
            dictResult = await _fdictFetchWorkflow(
                sId, sWorkflowPathArg);
        } catch (errorRetry) {
            return false;
        }
        if (iThisGeneration !== _iWorkflowGeneration) return true;
        VaibifyApp.fnActivateWorkflow(sId, dictResult, sWorkflowName);
        fnCheckOriginDrift(sId, false);
        return true;
    }

    function _fnReturnToProjectList(error) {
        /* A refusal that names an action must leave the researcher
           somewhere they can perform it: the project TILE is the
           claim control, one screen back. Reached only when the
           automatic reclaim could not recover. */
        var dictDetail = (error && error.dictDetail) || {};
        VaibifyApp.fnShowToast(dictDetail.sMessage, "warning");
        VaibifyApp.fnShowContainerLanding();
        VaibifyContainerManager.fnLoadContainers();
    }

    var _bRefreshing = false;

    async function fnRefreshWorkflow() {
        if (_bRefreshing) return;
        var sId = VaibifyApp.fsGetContainerId();
        var sPath = VaibifyApp.fsGetWorkflowPath();
        if (!sId || !sPath) return;
        _bRefreshing = true;
        var iThisGeneration = _iWorkflowGeneration;
        try {
            var dictResult = await _fdictFetchWorkflow(
                sId, sPath);
            /* A workflow switch during the refresh supersedes it; applying
             * this refresh would write the old workflow's data onto the
             * newly selected one. */
            if (iThisGeneration !== _iWorkflowGeneration) return;
            VaibifyApp.fnRefreshWorkflowData(dictResult);
            await fnCheckOriginDrift(sId, false);
            VaibifyApp.fnShowToast(
                "Project refreshed", "info");
        } catch (error) {
            if (iThisGeneration !== _iWorkflowGeneration) return;
            VaibifyApp.fnShowToast(
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

    async function fdictPullProjectRepo() {
        var sId = VaibifyApp.fsGetContainerId();
        if (!sId) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/git/" + sId + "/pull-project-repo", {}
            );
            if (dictResult && dictResult.sRefusal) {
                _fnRenderDriftBanner(sId, null, dictResult);
                return;
            }
            VaibifyApp.fnShowToast(
                "Pulled to " + (dictResult.sNewHeadSha || "").slice(0, 7),
                "success"
            );
            _fnHideDriftBanner();
            await fnRefreshWorkflow();
        } catch (error) {
            VaibifyApp.fnShowToast(
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
            _fnAttachDirtyBannerActions(elBanner);
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
            elPull.addEventListener("click", fdictPullProjectRepo);
        }
        _fnAttachDriftBannerDismiss(elBanner);
    }

    function _fnAttachDirtyBannerActions(elBanner) {
        var elCommitPull = elBanner.querySelector(
            ".drift-banner-commit-and-pull");
        if (elCommitPull) {
            elCommitPull.addEventListener(
                "click", fnCommitCanonicalAndPull);
        }
        _fnAttachDriftBannerDismiss(elBanner);
    }

    async function fnCommitCanonicalAndPull() {
        var sId = VaibifyApp.fsGetContainerId();
        if (!sId) return;
        try {
            var dictCommit = await VaibifyApi.fdictPost(
                "/api/git/" + sId + "/commit-canonical", {});
            if (!dictCommit || !dictCommit.bSuccess) {
                VaibifyApp.fnShowToast(
                    "Could not commit canonical state files",
                    "error");
                return;
            }
            VaibifyApp.fnShowToast(
                "Committed " + (dictCommit.iFilesCommitted || 0) +
                " state files — pulling…",
                "info");
            await fdictPullProjectRepo();
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        }
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
            'changes. Commit canonical state files (test markers, ' +
            'MANIFEST.sha256, requirements.lock, project.json) and ' +
            'pull, or handle them yourself first.' +
            '<ul class="drift-banner-dirty-list">' +
            listItems.join("") + '</ul></div>' +
            '<div class="drift-banner-actions">' +
            '<button type="button" class="drift-banner-commit-and-pull">' +
            'Commit state &amp; Pull</button>' +
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
        var sContainerId = VaibifyApp.fsGetContainerId();
        if (!sContainerId) return;
        try {
            var listWorkflows = await VaibifyApi.fdictGet(
                "/api/workflows/" + sContainerId);
            _fnRenderWorkflowDropdown(listWorkflows);
            elDropdown.classList.add("active");
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Could not load projects", "error");
        }
    }

    function fnHideWorkflowDropdown() {
        document.getElementById("workflowDropdown")
            .classList.remove("active");
    }

    function _fnRenderWorkflowDropdown(listWorkflows) {
        var elDropdown = document.getElementById(
            "workflowDropdown");
        var sWorkflowPath = VaibifyApp.fsGetWorkflowPath();
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var bInNoWorkflow = !sWorkflowPath && !dictWorkflow;
        var sHtml = '<div class="workflow-dropdown-item new-workflow"'
            + ' data-action="newWorkflow">'
            + '<span class="wf-name">+ New Project&hellip;</span>'
            + '</div>';
        sHtml += '<div class="workflow-dropdown-item' +
            (bInNoWorkflow ? " current" : "") +
            '" data-action="noWorkflow">' +
            '<span class="wf-name">Blank Project</span></div>';
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
        var sWorkflowPath = VaibifyApp.fsGetWorkflowPath();
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        elDropdown.querySelectorAll(".workflow-dropdown-item")
            .forEach(function (el) {
                el.addEventListener("click", function (event) {
                    event.stopPropagation();
                    fnHideWorkflowDropdown();
                    if (el.dataset.action === "newWorkflow") {
                        VaibifyNewWorkflowWizard.fnLaunch(
                            VaibifyApp.fsGetContainerId());
                        return;
                    }
                    if (el.dataset.action === "noWorkflow") {
                        if (!dictWorkflow && !sWorkflowPath) return;
                        VaibifyApp.fnEnterNoWorkflow(
                            VaibifyApp.fsGetContainerId());
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
        VaibifyApp.fnShowConfirmModal(
            "Switch Project",
            "Switch to \"" + sNewName + "\"?\n\n" +
            "Current project state will be saved.",
            async function () {
                await fnSaveCurrentWorkflow();
                fnSelectWorkflow(
                    VaibifyApp.fsGetContainerId(),
                    sNewPath, sNewName);
            }
        );
    }

    async function fnSaveCurrentWorkflow() {
        var sContainerId = VaibifyApp.fsGetContainerId();
        var dictWorkflow = VaibifyApp.fdictGetWorkflow();
        var sWorkflowPath = VaibifyApp.fsGetWorkflowPath();
        if (!sContainerId || !dictWorkflow || !sWorkflowPath) return;
        try {
            await VaibifyApi.fdictPostRaw(
                "/api/connect/" + sContainerId +
                "?sWorkflowPath=" +
                encodeURIComponent(sWorkflowPath)
            );
        } catch (error) {
            VaibifyApp.fnShowToast(
                "Could not save project", "error");
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
        "How to become a Project",
        "Files to Copy",
    ];
    /* The wizard's pages are a CATALOGUE; which of them a project
       needs depends on its mode. A host project has no image to
       build, so a Python version, cloned repositories, image
       features and package lists are all questions about a container
       it will never have -- asking them would collect answers
       nothing reads. It also has no NAME page: a host sandbox IS its
       directory, so its name is the directory's basename, derived when
       the directory is chosen rather than typed. Directory and
       template are the whole decision. */
    var _DICT_WIZARD_PAGE = {
        DIRECTORY: 0, TEMPLATE: 1, NAME: 2, PYTHON: 3,
        REPOSITORIES: 4, FEATURES: 5, PACKAGES: 6, SUMMARY: 7,
        DESTINATION: 8, FILES: 9,
    };
    var _T_CONTAINER_WIZARD_PAGES = [
        _DICT_WIZARD_PAGE.DIRECTORY, _DICT_WIZARD_PAGE.TEMPLATE,
        _DICT_WIZARD_PAGE.NAME, _DICT_WIZARD_PAGE.PYTHON,
        _DICT_WIZARD_PAGE.REPOSITORIES, _DICT_WIZARD_PAGE.FEATURES,
        _DICT_WIZARD_PAGE.PACKAGES, _DICT_WIZARD_PAGE.SUMMARY,
    ];
    var _T_HOST_WIZARD_PAGES = [
        _DICT_WIZARD_PAGE.DIRECTORY, _DICT_WIZARD_PAGE.TEMPLATE,
        _DICT_WIZARD_PAGE.SUMMARY,
    ];
    /* Converting an existing host sandbox reuses the container pages
       but OMITS Directory and Template: the directory already exists
       and is scaffolded, so re-choosing or re-templating it would
       clobber the researcher's files. Name is KEPT and mandatory --
       the host basename may not be Docker-safe -- and pre-filled with
       a sanitized suggestion. The page arrays are indexed by page
       ENUM, so this new LIST needs no reindexing. This container-only
       list is what a host PROJECT (already graduated) sees when it is
       containerized -- there is no destination to choose. */
    /* FILES sits before PACKAGES, not after: the files chosen there
       are what the package page scans for imports, so asking for
       packages first would ask the researcher to answer a question
       vaibify is about to answer for them. A container's workspace is
       a fresh Docker volume, NOT the
       container's workspace is a fresh Docker volume, NOT the
       researcher's directory, so converting is also where they decide
       which of their own files cross over. Only the CONTAINER branches
       ask -- a host Project's files already live where the project
       runs, so the promote branch below has no such page. */
    var _T_CONVERT_WIZARD_PAGES = [
        _DICT_WIZARD_PAGE.NAME, _DICT_WIZARD_PAGE.PYTHON,
        _DICT_WIZARD_PAGE.REPOSITORIES, _DICT_WIZARD_PAGE.FEATURES,
        _DICT_WIZARD_PAGE.FILES, _DICT_WIZARD_PAGE.PACKAGES,
        _DICT_WIZARD_PAGE.SUMMARY,
    ];
    /* A host SANDBOX becoming a Project first chooses its destination.
       Both branches open on the same Destination page (position 0), so
       switching the choice keeps position 0 valid and re-lists only the
       pages after it. The container branch is the convert list above
       with the choice prepended; the host branch collects nothing but
       a host-safe name and a summary -- no image, no build. */
    var _T_CONVERT_CHOICE_PAGES = [_DICT_WIZARD_PAGE.DESTINATION].concat(
        _T_CONVERT_WIZARD_PAGES);
    var _T_PROMOTE_CHOICE_PAGES = [
        _DICT_WIZARD_PAGE.DESTINATION, _DICT_WIZARD_PAGE.NAME,
        _DICT_WIZARD_PAGE.SUMMARY,
    ];
    var _LIST_WIZARD_HELP = [
        '<p>The folder on your host machine where vaibify writes ' +
        '<code>vaibify.yml</code> and stores any project files. This ' +
        'becomes the canonical location of the project &mdash; the ' +
        'registry points here, and you edit configuration here later.</p>' +
        '<p>Use the picker to navigate to an existing folder, or click ' +
        '<strong>+ New folder</strong> to create one. A common location ' +
        'is <code>~/src/</code>.</p>',

        '<p>A starter set of files that determines the initial shape ' +
        'of your project. Three templates ship with vaibify:</p>' +
        '<p><strong>sandbox</strong> &mdash; a blank workspace for ' +
        'ad-hoc exploration. Use this when you have one project ' +
        'repository or just want a clean environment to work in.</p>' +
        '<p><strong>toolkit</strong> &mdash; a workspace for developing ' +
        'several peer code repositories side-by-side. Use this when ' +
        'you are actively editing multiple libraries that depend on ' +
        'each other and want each one to appear in the Repos panel ' +
        'with its own git status and push controls.</p>' +
        '<p><strong>workflow</strong> &mdash; vaibify&rsquo;s flagship ' +
        'reproducibility template: a runnable two-step example ' +
        'pipeline (generate samples, then plot a histogram of them) ' +
        'wired with cross-step tokens. Use this when you want the ' +
        'full pipeline machinery &mdash; steps, dependency tracking, ' +
        'and per-step verification &mdash; and replace the example ' +
        'steps with your own.</p>' +
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

        '<p>A sandbox can graduate to a Project in two independent ways ' +
        '&mdash; they are separate axes, and you can do the second later ' +
        'even after the first.</p>' +
        '<p><strong>Host Project</strong> &mdash; keeps running directly ' +
        'on this machine. It gets a real name and is tracked as a ' +
        'Project, but <em>no container is built</em>: nothing to wait ' +
        'for, and the work stays uncontained (host mode). Choose this ' +
        'when you want to name and keep a workspace without the ' +
        'reproducibility machinery of a container.</p>' +
        '<p><strong>Containerized Project</strong> &mdash; rebuilds the ' +
        'project inside a Docker image for full reproducibility. This ' +
        'collects container settings and runs a build (minutes to ' +
        'hours). Choose this when you want the work isolated and ' +
        'exactly reproducible.</p>',

        '<p>Which of this directory&rsquo;s files and folders to copy ' +
        'into the container.</p>' +
        '<p><strong>Why you have to choose:</strong> a container does ' +
        'not share your folder. Its <code>/workspace</code> is a ' +
        'separate Docker volume, so nothing of yours is in there ' +
        'unless it is copied in or cloned from a git remote. Anything ' +
        'you leave unticked simply stays on your machine.</p>' +
        '<p>The copy is <strong>one way and one time</strong>, made ' +
        'when the container first starts. Editing a file on your ' +
        'machine afterwards does not change the container&rsquo;s ' +
        'copy, and editing it in the container does not touch yours.</p>' +
        '<p>Your originals are never moved or deleted &mdash; this ' +
        'only ever copies.</p>' +
        '<p><code>.git</code> is included automatically when the ' +
        'directory is a repository, so your history comes along and ' +
        'the container&rsquo;s copy is a real git repo, which vaibify ' +
        'workflows require.</p>',
    ];
    var _LIST_FEATURE_DEFINITIONS = [
        {bIsAgent: true, sKey: "claude", sLabel: "Claude Code CLI",
         sHint: "Install the Claude Code agent inside the container."},
        {bIsAgent: true, sKey: "codex", sLabel: "Codex CLI",
         sHint: "Install the OpenAI Codex agent inside the container."},
        {bIsAgent: true, sKey: "gemini", sLabel: "Gemini CLI",
         sHint: "Install the Google Gemini agent inside the container."},
        {bIsAgent: true, sKey: "opencode", sLabel: "OpenCode",
         sHint: "Install the OpenCode agent inside the container."},
        {bIsAgent: true, sKey: "cline", sLabel: "Cline",
         sHint: "Install the Cline agent inside the container."},
        {bIsAgent: true, sKey: "openhands", sLabel: "OpenHands",
         sHint: "Install the OpenHands agent inside the container."},
        {bIsAgent: true, sKey: "pi", sLabel: "Pi",
         sHint: "Install the Pi coding agent inside the container."},
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

    function fnOpenCreateWizard(sProjectMode) {
        _iWizardStep = 0;
        _dictWizardData = _fdictBuildDefaultWizardData();
        _dictWizardData.sMode =
            sProjectMode === "host" ? "host" : "container";
        document.getElementById("modalCreateWizard")
            .style.display = "flex";
        _fnRenderWizardStep(_iWizardStep);
    }

    function _flistWizardPages() {
        if (_dictWizardData.sMode === "host") {
            return _T_HOST_WIZARD_PAGES;
        }
        if (_dictWizardData.sMode === "convert") {
            /* A host PROJECT being containerized skips the destination
               choice: it has already graduated, so the only remaining
               question is the container's. A host SANDBOX chooses first,
               and the two branches share the Destination page at
               position 0. */
            if (!_dictWizardData.bOfferHostPromotion) {
                return _T_CONVERT_WIZARD_PAGES;
            }
            return _dictWizardData.sConvertDestination === "host"
                ? _T_PROMOTE_CHOICE_PAGES
                : _T_CONVERT_CHOICE_PAGES;
        }
        return _T_CONTAINER_WIZARD_PAGES;
    }

    function fnOpenConvertWizard(sHostName, sDirectory, bOfferHostPromotion) {
        /* The two triggers (host tile action + host-only Files-panel
           button) both land here. The directory is the existing,
           already-registered one -- never re-chosen. A host sandbox is
           offered a destination choice (host Project vs container); a
           host Project (already graduated) is not -- it goes straight to
           the container flow. The name is pre-filled once the branch is
           known, because a host-safe name allows spaces a Docker name
           does not. */
        _iWizardStep = 0;
        _dictWizardData = _fdictBuildDefaultWizardData();
        _dictWizardData.sMode = "convert";
        _dictWizardData.sHostName = sHostName;
        _dictWizardData.sDirectory = sDirectory || "";
        _dictWizardData.bOfferHostPromotion = bOfferHostPromotion !== false;
        if (_dictWizardData.bOfferHostPromotion) {
            _dictWizardData.sConvertDestination = "";
            _dictWizardData.sProjectName = "";
        } else {
            _dictWizardData.sConvertDestination = "container";
            _dictWizardData.sProjectName = _fsDockerSafeSuggestion(
                _fsProjectNameFromDirectory());
        }
        document.getElementById("modalCreateWizard")
            .style.display = "flex";
        _fnRenderWizardStep(_iWizardStep);
    }

    function _fbPromotingToHostProject() {
        return _dictWizardData.sMode === "convert" &&
            _dictWizardData.sConvertDestination === "host";
    }

    function _fnChooseConvertDestination(sDestination) {
        /* The choice decides the rest of the wizard AND how the name is
           seeded: a host Project name is host-safe (spaces allowed) so
           the raw basename fits, while a container name is sanitized to a
           Docker identifier. Re-render the current step so the progress
           dots (the two branches differ in length) and the final-button
           label reflect the choice. */
        _dictWizardData.sConvertDestination = sDestination;
        var sBasename = _fsProjectNameFromDirectory();
        _dictWizardData.sProjectName = sDestination === "host"
            ? sBasename
            : _fsDockerSafeSuggestion(sBasename);
        _fnRenderWizardStep(_iWizardStep);
    }

    function _fsDockerSafeSuggestion(sName) {
        /* Sanitize a host basename into a valid Docker identifier so
           the Name page opens on something acceptable: it is
           LOWERCASED (an image repository name may not contain
           capitals), illegal runs collapse to a hyphen, leading
           non-alphanumerics and trailing separators are trimmed, and
           the length is capped at 63. The researcher can still type
           anything; the backend is the authority
           (fbIsDockerSafeName). */
        var sCleaned = (sName || "")
            .toLowerCase()
            .replace(/[^a-z0-9_.-]+/g, "-")
            .replace(/^[^a-zA-Z0-9]+/, "")
            .replace(/[-._]+$/, "");
        if (sCleaned.length > 63) sCleaned = sCleaned.substring(0, 63);
        return sCleaned || "project";
    }

    function _fiWizardPageAt(iPosition) {
        return _flistWizardPages()[iPosition];
    }

    function _fdictBuildDefaultWizardData() {
        var dictData = _fdictBuildBasicWizardDefaults();
        dictData.listFeatures = _LIST_DEFAULT_FEATURES.slice();
        dictData.bUseGithubAuth = true;
        dictData.bNeverSleep = _fbIsMacOs();
        dictData.bNetworkIsolation = false;
        dictData.iCpuLimit = 0;
        dictData.fMemoryLimitGigabytes = 0;
        return _fdictExtendWithPackageDefaults(dictData);
    }

    function _fdictBuildBasicWizardDefaults() {
        return {
            sDirectory: "",
            sTemplateName: "",
            sProjectName: "",
            sWorkflowName: "",
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
        var iPage = _fiWizardPageAt(_iWizardStep);
        var sTitle = _LIST_WIZARD_TITLES[iPage] + " — Help";
        var sBody = _LIST_WIZARD_HELP[iPage] || "";
        VaibifyModals.fnShowInfoModal(sTitle, sBody);
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
        if (!_fbValidateWizardStep(_fiWizardPageAt(_iWizardStep))) {
            return;
        }
        /* An agentless container is legal but almost never intended:
           running an AI agent against a contained workspace is what
           vaibify is FOR, so leaving every agent unticked is far more
           likely a slip than a decision. Asked once, remembered, and
           never a refusal. */
        if (_fbLeavingFeaturesWithNoAgent()) {
            _fnConfirmNoAgentThenAdvance();
            return;
        }
        _fnAdvanceOrSubmit();
    }

    function _fbLeavingFeaturesWithNoAgent() {
        if (_fiWizardPageAt(_iWizardStep) !==
                _DICT_WIZARD_PAGE.FEATURES) {
            return false;
        }
        if (_dictWizardData.bAcknowledgedNoAgent) return false;
        return !_fbAnyAgentSelected();
    }

    function _fbAnyAgentSelected() {
        var listSelected = _dictWizardData.listFeatures || [];
        return _LIST_FEATURE_DEFINITIONS.some(function (dictFeature) {
            return dictFeature.bIsAgent &&
                listSelected.indexOf(dictFeature.sKey) !== -1;
        });
    }

    function _fnConfirmNoAgentThenAdvance() {
        VaibifyApp.fnShowConfirmModal(
            "No coding agent selected",
            "This container will have no AI coding agent installed. " +
            "Running an agent against a contained, reproducible " +
            "workspace is what vaibify is for, so this is usually a " +
            "slip rather than a choice.",
            function () {
                _dictWizardData.bAcknowledgedNoAgent = true;
                _fnAdvanceOrSubmit();
            },
            {
                sConfirmLabel: "Continue without one",
                sCancelLabel: "Go back and choose",
                sDetails:
                    "You can install an agent later by editing " +
                    "vaibify.yml and rebuilding the image, but that " +
                    "is another full build -- picking one now is much " +
                    "quicker.",
            }
        );
    }

    function _fnAdvanceOrSubmit() {
        if (_iWizardStep >= _flistWizardPages().length - 1) {
            if (_dictWizardData.sMode === "convert") {
                if (_fbPromotingToHostProject()) {
                    _fnSubmitPromoteHostProject();
                } else {
                    _fnSubmitConvertProject();
                }
            } else {
                _fnSubmitCreateProject();
            }
            return;
        }
        _iWizardStep++;
        _fnRenderWizardStep(_iWizardStep);
    }

    function _fnRenderWizardStep(iPosition) {
        var iPage = _fiWizardPageAt(iPosition);
        _fnUpdateWizardProgress(iPosition);
        _fnUpdateWizardButtons(iPosition);
        document.getElementById("wizardStepTitle").textContent =
            _LIST_WIZARD_TITLES[iPage];
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
            _fnRenderStepDestination,
            _fnRenderStepFilesToCopy,
        ];
        listRenderers[iPage](elContent);
    }

    function _fnUpdateWizardProgress(iPosition) {
        /* The markup carries one dot per page of the LONGEST wizard,
           so a shorter one hides its surplus rather than showing a
           researcher three steps that will never arrive. */
        var iPageCount = _flistWizardPages().length;
        var listDots = document.querySelectorAll(
            ".wizard-progress-step"
        );
        listDots.forEach(function (el, i) {
            el.style.display = i < iPageCount ? "" : "none";
            el.classList.toggle("active", i <= iPosition);
        });
    }

    function _fnUpdateWizardButtons(iPosition) {
        document.getElementById("btnWizardBack").disabled =
            iPosition === 0;
        document.getElementById("btnWizardNext").textContent =
            iPosition === _flistWizardPages().length - 1
                ? _fsFinalButtonLabel() : "Next";
    }

    function _fsFinalButtonLabel() {
        if (_dictWizardData.sMode !== "convert") return "Create";
        return _fbPromotingToHostProject() ? "Promote" : "Convert";
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
        VaibifyDirectoryBrowser.fnOpenForCreate(
            _fnApplyChosenDirectory);
    }

    function _fnApplyChosenDirectory(sChosenPath) {
        _dictWizardData.sDirectory = sChosenPath;
        /* A host sandbox has no name page, so its name follows the
           directory it was just given -- re-derived on every change so
           the two can never diverge. A container project keeps the name
           the researcher types on its own page. */
        if (_dictWizardData.sMode === "host") {
            _dictWizardData.sProjectName = _fsProjectNameFromDirectory();
        }
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

    var _LIST_DESTINATION_CHOICES = [
        {sKey: "host", sTitle: "Host Project",
         sDescription: "Runs on this machine. Named and tracked as a " +
            "Project, but no container is built. Stays uncontained."},
        {sKey: "container", sTitle: "Containerized Project",
         sDescription: "Full reproducibility. Rebuilds the project " +
            "inside a Docker image (this can take minutes to hours)."},
    ];

    function _fnRenderStepDestination(elContent) {
        elContent.innerHTML = '<div class="add-choice-cards">' +
            _LIST_DESTINATION_CHOICES.map(function (dictChoice) {
                var sActive =
                    dictChoice.sKey === _dictWizardData.sConvertDestination
                        ? " style=\"border-color:" +
                          "var(--color-pale-blue);\""
                        : "";
                return '<div class="add-choice-card" ' +
                    'data-destination="' +
                    VaibifyUtilities.fnEscapeHtml(dictChoice.sKey) + '"' +
                    sActive + '>' +
                    '<div class="add-choice-title">' +
                    VaibifyUtilities.fnEscapeHtml(dictChoice.sTitle) +
                    '</div><div class="add-choice-description">' +
                    VaibifyUtilities.fnEscapeHtml(
                        dictChoice.sDescription) +
                    '</div></div>';
            }).join("") + '</div>';
        _fnBindDestinationCardClicks(elContent);
    }

    function _fnBindDestinationCardClicks(elContent) {
        elContent.querySelectorAll(".add-choice-card").forEach(
            function (el) {
                el.addEventListener("click", function () {
                    _fnChooseConvertDestination(el.dataset.destination);
                });
            }
        );
    }

    function _fnRenderStepProjectName(elContent) {
        var sDefault = _fsProjectNameFromDirectory();
        if (!_dictWizardData.sProjectName) {
            _dictWizardData.sProjectName = sDefault;
        }
        /* A host Project's name is host-safe (spaces allowed) and never
           becomes a Docker object; a container name is a Docker
           identifier. The placeholder and note follow the branch so the
           researcher is not told a host name must look like a container
           one. */
        var bHostProject = _fbPromotingToHostProject();
        if (!bHostProject) {
            _fnRenderContainerNamePage(elContent, sDefault);
            return;
        }
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project Name</label>' +
            '<input type="text" id="inputWizardProjectName" ' +
            'placeholder="My Project">' +
            '<p class="muted-text">Spaces are allowed. This project ' +
            'stays on this machine &mdash; no container is built.</p>' +
            '</div>';
        document.getElementById(
            "inputWizardProjectName").value =
            _dictWizardData.sProjectName;
    }

    function _fnRenderContainerNamePage(elContent, sDefault) {
        /* TWO names, because there are two things being created and
           they obey different rules. The Project is what the
           researcher reads on the Project hub and may be called "AI
           Greenhouse"; the container is a Docker identifier and may
           not contain a space. Collapsing them into one field meant a
           researcher typed a perfectly good Project name and was
           refused for Docker's reasons at the END of the wizard, after
           choosing packages and files (live report, 2026-08-21). */
        if (!_dictWizardData.sWorkflowName) {
            _dictWizardData.sWorkflowName = sDefault;
        }
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Project name</label>' +
            '<input type="text" id="inputWizardWorkflowName" ' +
            'placeholder="My Project">' +
            '<p class="muted-text">What you will see on the Project ' +
            'hub. Spaces are fine.</p></div>' +
            '<div class="form-group">' +
            '<label>Container name</label>' +
            '<input type="text" id="inputWizardProjectName" ' +
            'placeholder="my-project">' +
            '<p class="wizard-name-problem" ' +
            'id="wizardNameProblem"></p>' +
            '<p class="muted-text">Docker&rsquo;s name for the image ' +
            'and container. Lowercase letters, digits, dots, hyphens ' +
            'and ' +
            'underscores only.</p></div>';
        document.getElementById("inputWizardWorkflowName").value =
            _dictWizardData.sWorkflowName;
        document.getElementById("inputWizardProjectName").value =
            _dictWizardData.sProjectName;
        _fnBindNameFields();
    }

    function _fnBindNameFields() {
        var elWorkflow = document.getElementById(
            "inputWizardWorkflowName");
        var elContainer = document.getElementById(
            "inputWizardProjectName");
        /* The container name follows the Project name until the
           researcher edits it themselves, at which point it is theirs
           and stops being overwritten. */
        elWorkflow.addEventListener("input", function () {
            if (elContainer.dataset.bEditedByHand === "true") return;
            elContainer.value = _fsDockerSafeSuggestion(elWorkflow.value);
            _fnShowNameProblem(elContainer.value);
        });
        elContainer.addEventListener("input", function () {
            elContainer.dataset.bEditedByHand = "true";
            _fnShowNameProblem(elContainer.value);
        });
        _fnShowNameProblem(elContainer.value);
    }

    var _RE_DOCKER_SAFE_NAME = /^[a-z0-9][a-z0-9_.-]*$/;

    function _fsContainerNameProblem(sName) {
        /* Mirrors the backend's fbIsDockerSafeName. The backend stays
           the authority -- this only moves the SAME refusal to the
           moment of typing, where it is actionable. */
        if (!sName) return "A container name is required.";
        if (sName.indexOf(" ") !== -1) {
            return "A container name cannot contain spaces.";
        }
        /* Named before the general rule below, because "must be
           lowercase" is a far more useful sentence than a recital of
           the whole character class when the only problem is a
           capital letter. */
        if (sName !== sName.toLowerCase()) {
            return "A container name must be lowercase — Docker image "
                + "names cannot contain capitals.";
        }
        if (!_RE_DOCKER_SAFE_NAME.test(sName)) {
            return "Use only lowercase letters, digits, dots, " +
                "hyphens and underscores, starting with a letter or " +
                "digit.";
        }
        if (sName.length > 63) {
            return "A container name is at most 63 characters.";
        }
        return "";
    }

    function _fnShowNameProblem(sName) {
        var elProblem = document.getElementById("wizardNameProblem");
        if (!elProblem) return;
        elProblem.textContent = _fsContainerNameProblem(sName);
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

    /* Always copied, never offered as a choice: a vaibify workflow must
       live inside a git repository, so a container whose copy of the
       project is not one cannot run a pipeline at all. Listing it as a
       tickbox would offer a choice whose "no" breaks the product. */
    var _S_ALWAYS_COPIED_ENTRY = ".git";

    function _fnRenderStepFilesToCopy(elContent) {
        elContent.innerHTML =
            '<div class="form-group">' +
            '<label>Copy into the container</label>' +
            '<p class="wizard-hint">A container does not share this ' +
            'folder &mdash; tick what should be copied in. Your ' +
            'originals stay where they are.</p>' +
            '<div id="wizardSeedRemoteNotice"></div>' +
            '<div id="wizardSeedList" class="wizard-feature-list">' +
            '<p class="muted-text">Reading the folder&hellip;</p>' +
            '</div></div>';
        _fnLoadSeedCandidates();
    }

    async function _fnLoadSeedCandidates() {
        var elList = document.getElementById("wizardSeedList");
        var sDirectory = _dictWizardData.sDirectory || "";
        if (!sDirectory) {
            elList.innerHTML = '<p class="muted-text">' +
                'This project has no directory on record.</p>';
            return;
        }
        try {
            var dictResult = await VaibifyApi.fdictGet(
                "/api/host-directories?bIncludeFiles=true&sPath=" +
                encodeURIComponent(sDirectory));
            _fnRenderSeedCandidates(dictResult.listEntries || []);
        } catch (error) {
            elList.innerHTML = '<p style="color:var(--color-red-text);">' +
                VaibifyUtilities.fnEscapeHtml(
                    VaibifyUtilities.fsSanitizeErrorForUser(
                        error.message)) + '</p>';
        }
        _fnLoadSeedRemoteNotice();
    }

    function _fnRenderSeedCandidates(listEntries) {
        /* Everything is ticked on arrival: a researcher converting
           their own directory wants their own work, and the page is
           there to let them EXCLUDE something, not to make them
           re-choose what they already have. */
        var listOffered = listEntries.filter(function (dictEntry) {
            return dictEntry.sName !== _S_ALWAYS_COPIED_ENTRY;
        });
        var elList = document.getElementById("wizardSeedList");
        if (listOffered.length === 0) {
            elList.innerHTML = '<p class="muted-text">' +
                'This folder is empty.</p>';
            return;
        }
        elList.innerHTML =
            '<label class="wizard-feature-row wizard-seed-all">' +
            '<input type="checkbox" id="wizardSeedSelectAll" checked>' +
            '<span><strong>Select all</strong></span></label>' +
            listOffered.map(_fsRenderSeedRow).join("");
        _fnBindSeedSelectAll();
    }

    function _fsRenderSeedRow(dictEntry) {
        var bChecked = !_dictWizardData.saSeedPaths ||
            _dictWizardData.saSeedPaths.indexOf(dictEntry.sName) !== -1;
        return '<label class="wizard-feature-row">' +
            '<input type="checkbox" class="wizard-seed-input" ' +
            'data-seed-name="' +
            VaibifyUtilities.fnEscapeHtml(dictEntry.sName) + '"' +
            (bChecked ? " checked" : "") + '>' +
            '<span>' +
            VaibifyUtilities.fnEscapeHtml(dictEntry.sName) +
            (dictEntry.bIsDirectory ? "/" : "") + '</span></label>';
    }

    function _fnBindSeedSelectAll() {
        var elAll = document.getElementById("wizardSeedSelectAll");
        var listRows = document.querySelectorAll(".wizard-seed-input");
        elAll.addEventListener("change", function () {
            listRows.forEach(function (elRow) {
                elRow.checked = elAll.checked;
            });
        });
        listRows.forEach(function (elRow) {
            elRow.addEventListener("change", function () {
                elAll.checked = Array.prototype.every.call(
                    listRows, function (el) { return el.checked; });
            });
        });
    }

    async function _fnLoadSeedRemoteNotice() {
        /* A project with no remote is not an error, so this is a
           notice and never a block -- but it is worth saying HERE,
           because converting is the moment the container's copy
           becomes the only copy that is not on the researcher's own
           disk. */
        var elNotice = document.getElementById("wizardSeedRemoteNotice");
        if (!elNotice) return;
        try {
            var dictRemote = await VaibifyApi.fdictGet(
                "/api/registry/" +
                encodeURIComponent(_dictWizardData.sHostName) +
                "/git-remote");
            if (dictRemote.sRemoteUrl) {
                elNotice.innerHTML = "";
                return;
            }
        } catch (error) {
            elNotice.innerHTML = "";
            return;
        }
        elNotice.innerHTML = _fsRenderNoRemoteNotice();
        _fnBindAddRemote();
    }

    function _fsRenderNoRemoteNotice() {
        return '<div class="wizard-remote-notice">' +
            '<p><strong>This folder has no git remote.</strong> ' +
            'Copying puts your files in the container, but nothing ' +
            'pushes them anywhere &mdash; so they exist only on this ' +
            'machine and in the container.</p>' +
            '<div class="wizard-remote-row">' +
            '<input type="text" id="inputWizardRemoteUrl" ' +
            'placeholder="https://github.com/you/project.git">' +
            '<button type="button" class="btn" ' +
            'id="btnWizardAddRemote">Add remote</button></div>' +
            '<p class="muted-text">Optional &mdash; you can add one ' +
            'later and keep going without it.</p></div>';
    }

    function _fnBindAddRemote() {
        var elButton = document.getElementById("btnWizardAddRemote");
        if (!elButton) return;
        elButton.addEventListener("click", async function () {
            var elInput = document.getElementById(
                "inputWizardRemoteUrl");
            var sUrl = (elInput.value || "").trim();
            if (!sUrl) return;
            elButton.disabled = true;
            try {
                await VaibifyApi.fdictPost(
                    "/api/registry/" +
                    encodeURIComponent(_dictWizardData.sHostName) +
                    "/git-remote", {sRemoteUrl: sUrl});
                VaibifyApp.fnShowToast("Remote added.", "success");
                _fnLoadSeedRemoteNotice();
            } catch (error) {
                VaibifyApp.fnShowToast(
                    VaibifyUtilities.fsSanitizeErrorForUser(
                        error.message), "error");
                elButton.disabled = false;
            }
        });
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
            sNeverSleep + _fsRenderNetworkIsolationRow() + '</div>' +
            _fsRenderResourceLimitsSection();
    }

    function _fsRenderResourceLimitsSection() {
        var sCpuValue = _dictWizardData.iCpuLimit > 0
            ? String(_dictWizardData.iCpuLimit) : "";
        var sMemoryValue = _dictWizardData.fMemoryLimitGigabytes > 0
            ? String(_dictWizardData.fMemoryLimitGigabytes) : "";
        return '<div class="form-group">' +
            '<label>Resource limits (blank = no limit)</label>' +
            '<div class="wizard-resource-limit-row">' +
            '<input type="number" id="wizardCpuLimit" min="1" ' +
            'step="1" placeholder="all cores − 1" value="' +
            sCpuValue + '">' +
            '<span>CPU cores</span></div>' +
            '<div class="wizard-resource-limit-row">' +
            '<input type="number" id="wizardMemoryLimit" ' +
            'min="0.25" step="0.25" placeholder="unlimited" ' +
            'value="' + sMemoryValue + '">' +
            '<span>Memory (GB)</span></div>' +
            '<div class="wizard-helper-text">Applied via docker ' +
            'run each time the container starts. A minimal demo ' +
            'container runs comfortably at 1 CPU and 1 GB.' +
            '</div></div>';
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
        /* A conversion carries the researcher's OWN scripts, so their
           imports are known and asking the researcher to transcribe
           them is clerical work vaibify can do. Two fields, not one:
           what was detected stays separable from what the researcher
           added, so they can see what vaibify concluded and correct
           it rather than having their own list silently rewritten. */
        var sDetected = _dictWizardData.sMode === "convert"
            ? _fsRenderDetectedPackagesField() : "";
        elContent.innerHTML =
            _fsRenderPackageTextarea(
                "wizardSystemPackages", "System packages (apt)",
                "gfortran\nlibhdf5-dev\ncmake",
                _dictWizardData.listSystemPackages) +
            sDetected +
            _fsRenderPackageTextarea(
                "wizardPythonPackages",
                sDetected ? "Additional Python packages (pip)"
                    : "Python packages (pip)",
                "numpy\nmatplotlib\npandas",
                _dictWizardData.listPythonPackages) +
            _fsRenderPackagesAdvancedSection();
        if (sDetected) _fnLoadDetectedPackages();
    }

    function _fsRenderDetectedPackagesField() {
        return '<div class="form-group">' +
            '<label>Detected in your scripts (pip)</label>' +
            '<textarea id="wizardDetectedPackages" rows="4" ' +
            'placeholder="Reading your scripts...">' +
            VaibifyUtilities.fnEscapeHtml(
                (_dictWizardData.listDetectedPackages || []).join("\n")) +
            '</textarea><div class="wizard-helper-text" ' +
            'id="wizardDetectedNote">One per line. Read from the ' +
            'imports in the files you chose &mdash; edit freely, ' +
            'nothing is installed until you convert.</div></div>';
    }

    async function _fnLoadDetectedPackages() {
        var elArea = document.getElementById("wizardDetectedPackages");
        var elNote = document.getElementById("wizardDetectedNote");
        if (!elArea) return;
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/registry/" +
                encodeURIComponent(_dictWizardData.sHostName) +
                "/scan-dependencies",
                {saRelativePaths: _dictWizardData.saSeedPaths || []});
            _fnApplyDetectedPackages(dictResult, elArea, elNote);
        } catch (error) {
            elArea.placeholder = "";
            elNote.textContent =
                "Your scripts could not be read, so nothing was " +
                "detected. Add any packages you need below.";
        }
    }

    function _fnApplyDetectedPackages(dictResult, elArea, elNote) {
        var saDetected = dictResult.saDetectedPackages || [];
        _dictWizardData.listDetectedPackages = saDetected;
        elArea.value = saDetected.join("\n");
        elArea.placeholder = "";
        if (saDetected.length === 0) {
            elNote.textContent =
                "No third-party imports found in the " +
                dictResult.iScannedFileCount +
                " Python file(s) you chose.";
            return;
        }
        /* Names, never versions: a version constraint is a scientific
           decision and guessing one would be inventing provenance. */
        elNote.textContent =
            "Read from the imports in the " +
            dictResult.iScannedFileCount + " Python file(s) you " +
            "chose. Versions are not guessed. Edit freely.";
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
        /* A host summary lists only what was asked. Echoing back an
           image's Python version, repositories and package lists for
           a project that will never build one states settings that
           govern nothing. */
        if (_fbPromotingToHostProject()) {
            /* Promotion to a host Project states only what it changes:
               the directory it keeps, the new name it takes, and that it
               stays on this machine with no build. */
            elContent.innerHTML =
                '<div class="wizard-summary-block">' +
                _fsSummaryRow("Directory", _dictWizardData.sDirectory) +
                _fsSummaryRow(
                    "New Project name", _dictWizardData.sProjectName) +
                _fsSummaryRow(
                    "Mode",
                    "Host — runs directly on this machine; no " +
                    "container is built") +
                '</div>';
            return;
        }
        if (_dictWizardData.sMode === "host") {
            /* No Project Name row: a host sandbox's name IS its
               directory basename, so echoing it back as a separate
               field would state a second identity the researcher never
               chose. */
            elContent.innerHTML =
                '<div class="wizard-summary-block">' +
                _fsSummaryRow("Directory", _dictWizardData.sDirectory) +
                _fsSummaryRow("Template", _dictWizardData.sTemplateName) +
                _fsSummaryRow(
                    "Mode",
                    "Host — commands run directly on this machine, " +
                    "not in a container") +
                '</div>';
            return;
        }
        elContent.innerHTML =
            '<div class="wizard-summary-block">' +
            _fsSummaryHeadBlock() +
            _fsSummaryRow("Python", _dictWizardData.sPythonVersion) +
            _fsSummaryReposLine() + _fsSummarySeedLine() +
            _fsSummaryFeaturesLine() + _fsSummaryAuthLine() +
            _fsSummaryPackagesLines() + _fsSummaryToggleLines() +
            '</div>';
    }

    function _fsSummarySeedLine() {
        /* Only a conversion copies anything: a freshly created project
           scaffolds its directory from a template, so there is no
           pre-existing content to carry across. */
        if (_dictWizardData.sMode !== "convert") return "";
        var saSeedPaths = _dictWizardData.saSeedPaths || [];
        var sValue = saSeedPaths.length > 0
            ? saSeedPaths.join(", ") : "Nothing";
        return _fsSummaryRow("Copied into the container", sValue);
    }

    function _fsSummaryHeadBlock() {
        /* A conversion has no Template and its Directory already
           exists, so its head names the DIRECTORY it converts and the
           NEW container name it will carry -- not a template it never
           chose. */
        if (_dictWizardData.sMode === "convert") {
            /* Both names, because the conversion creates both: the
               Project the researcher will open, and the container it
               runs in. Stating only the container name is what left a
               researcher expecting a Project and finding none. */
            return _fsSummaryRow(
                "Directory", _dictWizardData.sDirectory) +
                _fsSummaryRow(
                    "New Project name",
                    _dictWizardData.sWorkflowName ||
                    _dictWizardData.sProjectName) +
                _fsSummaryRow(
                    "New container name",
                    _dictWizardData.sProjectName);
        }
        return _fsSummaryBasics();
    }

    function _fsSummaryBasics() {
        return _fsSummaryRow("Directory", _dictWizardData.sDirectory) +
            _fsSummaryRow("Template", _dictWizardData.sTemplateName) +
            _fsSummaryRow("Project Name",
                _dictWizardData.sProjectName);
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
        /* The MERGED list, because that is what gets installed. A
           summary showing only what the researcher typed would omit
           everything vaibify detected and understate the image. */
        var sPython = (
            _fdictWizardDataWithMergedPackages().listPythonPackages || []
        ).join(", ") || "None";
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
            _dictWizardData.bNetworkIsolation ? "Yes" : "No") +
            _fsSummaryResourceLimitLines();
    }

    function _fsSummaryResourceLimitLines() {
        var sCpuValue = _dictWizardData.iCpuLimit > 0
            ? _dictWizardData.iCpuLimit + " cores"
            : "All cores minus one";
        var sMemoryValue = _dictWizardData.fMemoryLimitGigabytes > 0
            ? _dictWizardData.fMemoryLimitGigabytes + " GB"
            : "Unlimited";
        return _fsSummaryRow("CPU limit", sCpuValue) +
            _fsSummaryRow("Memory limit", sMemoryValue);
    }

    function _fnSaveCurrentStepData() {
        _fnSaveBasicStepFields();
        _fnSaveFeaturesAndToggles();
        _fnSavePackagesAndAdvanced();
        _fnSaveSeedSelection();
    }

    function _fnSaveSeedSelection() {
        /* Only read when the page is actually on screen: a blank list
           saved from some other page would read as "the researcher
           unticked everything" and silently copy nothing. */
        var listRows = document.querySelectorAll(".wizard-seed-input");
        if (listRows.length === 0) return;
        _dictWizardData.saSeedPaths = Array.prototype.filter.call(
            listRows, function (elRow) { return elRow.checked; }
        ).map(function (elRow) { return elRow.dataset.seedName; });
    }

    function _fnSaveBasicStepFields() {
        var elName = document.getElementById(
            "inputWizardProjectName");
        if (elName) {
            _dictWizardData.sProjectName = elName.value.trim();
        }
        var elWorkflowName = document.getElementById(
            "inputWizardWorkflowName");
        if (elWorkflowName) {
            _dictWizardData.sWorkflowName =
                elWorkflowName.value.trim();
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
        _fnReadPositiveNumberInto(
            "wizardCpuLimit", "iCpuLimit", true);
        _fnReadPositiveNumberInto(
            "wizardMemoryLimit", "fMemoryLimitGigabytes", false);
    }

    function _fnReadCheckboxInto(sId, sField) {
        var elCheck = document.getElementById(sId);
        if (elCheck) _dictWizardData[sField] = elCheck.checked;
    }

    function _fnReadPositiveNumberInto(sId, sField, bInteger) {
        /* Blank, zero, or unparsable all mean "no limit" (0). */
        var elInput = document.getElementById(sId);
        if (!elInput) return;
        var fParsed = bInteger
            ? parseInt(elInput.value, 10)
            : parseFloat(elInput.value);
        _dictWizardData[sField] =
            (isNaN(fParsed) || fParsed <= 0) ? 0 : fParsed;
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
            "wizardDetectedPackages", "listDetectedPackages");
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

    function _fbValidateWizardStep(iPage) {
        if (iPage === _DICT_WIZARD_PAGE.DESTINATION &&
                !_dictWizardData.sConvertDestination) {
            VaibifyApp.fnShowToast(
                "Please choose how this becomes a Project.", "warning");
            return false;
        }
        if (iPage === _DICT_WIZARD_PAGE.DIRECTORY &&
                !_dictWizardData.sDirectory) {
            VaibifyApp.fnShowToast(
                "Directory path is required.", "warning");
            return false;
        }
        if (iPage === _DICT_WIZARD_PAGE.TEMPLATE &&
                !_dictWizardData.sTemplateName) {
            VaibifyApp.fnShowToast(
                "Please select a template.", "warning");
            return false;
        }
        if (iPage === _DICT_WIZARD_PAGE.NAME &&
                !_dictWizardData.sProjectName) {
            VaibifyApp.fnShowToast(
                "Project name is required.", "warning");
            return false;
        }
        /* Refuse an unusable container name HERE rather than letting
           the wizard run to its end and be refused by the server after
           the researcher has chosen packages and files. */
        if (iPage === _DICT_WIZARD_PAGE.NAME &&
                !_fbPromotingToHostProject() &&
                _dictWizardData.sMode !== "host") {
            var sProblem = _fsContainerNameProblem(
                _dictWizardData.sProjectName);
            if (sProblem) {
                VaibifyApp.fnShowToast(sProblem, "warning");
                return false;
            }
        }
        if (iPage === _DICT_WIZARD_PAGE.REPOSITORIES && _fbIsToolkit() &&
            _dictWizardData.listRepositories.length === 0) {
            VaibifyApp.fnShowToast(
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
            /* The wizard registers an ENVIRONMENT on the hub; whether
               it holds a Project depends on the template, and a blank
               sandbox holds none — so the toast must not say
               "Project" (live report, 2026-08-20). */
            VaibifyApp.fnShowToast(
                "Environment added.");
            VaibifyContainerManager.fnLoadContainers();
        } catch (error) {
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        } finally {
            elButton.disabled = false;
            elButton.textContent = "Create";
        }
    }

    function _fnSubmitConvertProject() {
        /* One confirm modal before the irreversible-ish step: it
           re-registers the project under a new name, a build runs next
           (minutes to hours), and the vaibify.yml is rewritten with
           container fields. A project open in THIS tab is released by
           the server as part of the conversion; only another session's
           hold refuses. A failed build does NOT revert to host -- it
           leaves a registered, not-yet-built container, exactly the
           normal post-create state. */
        VaibifyApp.fnShowConfirmModal(
            "Convert to a containerized Project",
            _fsConversionConfirmBody(),
            _fnExecuteConversion,
            {
                sConfirmLabel: "Convert and build",
                sCancelLabel: "Go back",
                sDetails:
                    "If the project is open in this tab it is closed " +
                    "automatically; a project open in another session " +
                    "must be closed there first. If the build fails, " +
                    "the project stays registered as a container that " +
                    "has not been built yet -- it does not revert to a " +
                    "host sandbox -- and you can retry the build from " +
                    "its tile.",
            }
        );
    }

    function _fsConversionConfirmBody() {
        return "Re-register '" + _dictWizardData.sHostName +
            "' as the containerized project '" +
            _dictWizardData.sProjectName + "'. The project's " +
            "vaibify.yml is rewritten with the container settings you " +
            "chose, and a Docker image build starts next (this can " +
            "take minutes to hours).";
    }

    function _fbWizardTargetsTheOpenProject() {
        /* True when the wizard's host project is the one THIS tab
           holds the lease for. The promote/convert routes then release
           this tab's own session server-side as part of the
           conversion, so the submit paths must quiet their channels
           first and leave (or re-enter) afterwards. */
        return Boolean(VaibifyApp.fsGetLeaseForContainer(
            _dictWizardData.sHostName));
    }

    async function _fnExecuteConversion() {
        var sHostName = _dictWizardData.sHostName;
        var sNewName = _dictWizardData.sProjectName;
        var bHeldByThisTab = _fbWizardTargetsTheOpenProject();
        /* Converting from inside the open project: the server releases
           this tab's own session before re-registering, closing its
           sockets with a refusal code the connection monitor would
           surface as an outage. Disconnect deliberately first, the way
           a workflow switch does, so the close reads as intentional. */
        var bSocketWasOpen = bHeldByThisTab && VaibifyWebSocket.fbIsOpen();
        if (bSocketWasOpen) VaibifyWebSocket.fnDisconnect();
        try {
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sHostName) +
                "/convert-to-container",
                _fdictWizardDataWithMergedPackages());
        } catch (error) {
            /* Validators run server-side BEFORE the release, so a
               refusal leaves this tab still owning the project --
               reopen the socket it closed and stay in place. */
            if (bSocketWasOpen) {
                VaibifyPipelineRunner.fnConnectPipelineWebSocket();
            }
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
            return;
        }
        _fnCloseWizard();
        if (bHeldByThisTab) {
            /* The conversion released this tab's session, and the
               project is now an unbuilt container -- there is no host
               view to return to. Drop the dead lease and tear down to
               the picker before the build starts. */
            VaibifyApp.fnForgetLease();
            VaibifyApp.fnDisconnect();
        }
        VaibifyApp.fnShowToast(
            "Converted '" + sHostName + "' to '" + sNewName +
            "'. Building the image now.", "success");
        /* Reuse the tile build path: it opens the build-progress
           modal, polls .../build/progress, and reloads the container
           list (flipping the tile host -> container) in its finally. */
        var bBuiltAndRunning =
            await VaibifyContainerManager.fnBuildContainer(sNewName);
        /* A failed build has already said so, with the builder's own
           output. Attempting the copy anyway would bury that behind a
           second, vaguer message about a container that was never
           created. */
        if (bBuiltAndRunning) {
            await _fnCopySelectedFilesIntoContainer(sNewName);
        }
        VaibifyContainerManager.fnLoadContainers();
    }

    function _fdictWizardDataWithMergedPackages() {
        /* The two fields are separate on screen so the researcher can
           see what vaibify concluded apart from what they asked for,
           but the container installs one list. Detected first, then
           anything they added that is not already there -- de-duped,
           because a researcher who types a package vaibify also found
           should not cause it to be installed twice. */
        var listMerged = (_dictWizardData.listDetectedPackages ||
            []).slice();
        (_dictWizardData.listPythonPackages || []).forEach(
            function (sPackage) {
                if (listMerged.indexOf(sPackage) === -1) {
                    listMerged.push(sPackage);
                }
            });
        return Object.assign({}, _dictWizardData, {
            listPythonPackages: listMerged,
        });
    }

    async function _fnCopySelectedFilesIntoContainer(sNewName) {
        /* After the build AND the start it performs: the workspace is
           a Docker volume that does not exist until the container
           runs, so there is nowhere to copy to before this point. A
           failure is reported and never swallowed -- a container the
           researcher believes holds their files but does not is the
           dashboard lying about state. */
        var saSeedPaths = _dictWizardData.saSeedPaths || [];
        if (saSeedPaths.length === 0) return;
        /* Claim first. Copying into a container is a container
           mutation, so it is refused unless this session holds the
           lease -- and nobody holds one on a container that came into
           existence thirty seconds ago. Claiming is honest here rather
           than a workaround: this tab created the container and is
           about to put the researcher's files in it, which is exactly
           what owning it means. */
        if (!await VaibifyContainerManager.fbClaimContainer(sNewName)) {
            VaibifyApp.fnShowToast(
                "The container was built, but your files were not " +
                "copied in: it is in use in another session.", "error");
            return;
        }
        /* The route is container-SCOPED, so its path segment must be
           the container id the authority resolves against the owner
           map -- the name the wizard has been carrying is not
           interchangeable there. */
        var sContainerId =
            await VaibifyContainerManager.fsResolveContainerId(sNewName);
        if (!sContainerId) {
            /* Name the recovery, not just the symptom: the originals
               are untouched on the host, so the researcher needs to
               know nothing was lost and what to do next. Drag-and-drop
               onto the Files panel is the affordance that actually
               exists (scriptFiles.js); do not promise a re-copy
               button here until there is one. */
            VaibifyApp.fnShowToast(
                "Your files were not copied in: '" + sNewName +
                "' is not running yet. Your originals are untouched " +
                "— start it from its tile, then drag them onto the " +
                "Files panel.", "error");
            return;
        }
        try {
            var dictResult = await VaibifyApi.fdictPost(
                "/api/files/" + encodeURIComponent(sContainerId) +
                "/seed-workspace", {saRelativePaths: saSeedPaths});
            VaibifyApp.fnShowToast(
                "Copied " + dictResult.iCopiedCount +
                " item(s) into " + dictResult.sDestination + ".",
                "success");
        } catch (error) {
            VaibifyApp.fnShowToast(
                "The container was built, but copying your files in " +
                "failed: " + VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
        }
    }

    async function _fnSubmitPromoteHostProject() {
        /* No confirm modal and no build: promotion only names the
           project and marks it graduated, staying in host mode. The
           birth animation is the same milestone the container path
           celebrates -- a sandbox becoming a Project -- so it fires here
           too. */
        var sHostName = _dictWizardData.sHostName;
        var sNewName = _dictWizardData.sProjectName;
        var bHeldByThisTab = _fbWizardTargetsTheOpenProject();
        /* Same channel discipline as the conversion path: the server
           releases this tab's own session before re-registering, so
           quiet the socket deliberately first. */
        var bSocketWasOpen = bHeldByThisTab && VaibifyWebSocket.fbIsOpen();
        if (bSocketWasOpen) VaibifyWebSocket.fnDisconnect();
        var elButton = document.getElementById("btnWizardNext");
        elButton.disabled = true;
        elButton.textContent = "Promoting...";
        try {
            await VaibifyApi.fdictPost(
                "/api/registry/" + encodeURIComponent(sHostName) +
                "/promote-to-host-project", {sProjectName: sNewName});
        } catch (error) {
            /* Validators run server-side BEFORE the release, so a
               refusal leaves this tab still owning the project --
               reopen the socket it closed and stay in place. */
            if (bSocketWasOpen) {
                VaibifyPipelineRunner.fnConnectPipelineWebSocket();
            }
            VaibifyApp.fnShowToast(
                VaibifyUtilities.fsSanitizeErrorForUser(
                    error.message), "error");
            elButton.disabled = false;
            elButton.textContent = "Promote";
            return;
        }
        _fnCloseWizard();
        VaibifyApp.fnShowToast(
            "Promoted '" + sHostName + "' to the host Project '" +
            sNewName + "'.", "success");
        if (bHeldByThisTab) {
            /* Curtain up before the teardown: the researcher stays
               "in the dashboard" while the hand-off passes through
               both hubs beneath it. The finally guarantees a failed
               claim or connect drops the curtain onto the real
               screen instead of a permanent blank. */
            VaibifyApp.fnShowPromotionCurtain(sNewName);
            try {
                await _fnReenterPromotedProject(sNewName);
            } finally {
                VaibifyApp.fnHidePromotionCurtain();
            }
            return;
        }
        VaibifyApp.fnAnimateProjectBirth();
        VaibifyContainerManager.fnLoadContainers();
    }

    async function _fnReenterPromotedProject(sNewName) {
        /* The server released this tab's session as part of the
           promotion, so the old lease is dead. Tear the old view down,
           then carry the researcher straight back inside under the new
           name: claim, connect, and -- when the project has exactly
           one workflow -- open it, so the promotion ends in the step
           viewer rather than at the Environment hub. The host warning
           is deliberately NOT re-shown: the researcher accepted it to
           enter this very session, and promotion changes the name and
           the graduated flag, never the directory the warning is
           about. */
        VaibifyApp.fnForgetLease();
        VaibifyApp.fnDisconnect();
        await VaibifyContainerManager.fnLoadContainers();
        var bClaimed =
            await VaibifyContainerManager.fbClaimContainer(sNewName);
        if (!bClaimed) return;
        await VaibifyContainerManager.fnConnectToContainer(sNewName);
        await _fnOpenSoleWorkflow(sNewName);
        /* Drop the curtain before the birth trace so the researcher
           actually sees it draw around the step viewer. */
        VaibifyApp.fnHidePromotionCurtain();
        VaibifyApp.fnAnimateProjectBirth();
    }

    async function _fnOpenSoleWorkflow(sNewName) {
        /* The picker's cards carry the same data their click handler
           reads. Exactly one workflow means there is no choice to
           make, so the promotion completes in the step viewer; zero or
           several leave the researcher on the picker, where the choice
           is theirs. */
        var listCards = document.querySelectorAll(
            "#listWorkflows .container-card");
        if (listCards.length !== 1) return;
        var elCard = listCards[0];
        await fnSelectWorkflow(
            sNewName, elCard.dataset.path,
            elCard.querySelector(".name").textContent,
            parseInt(elCard.dataset.sizeBytes, 10) || 0);
    }

    return {
        fnRenderWorkflowList: fnRenderWorkflowList,
        fnCreateNewWorkflow: fnCreateNewWorkflow,
        fnSelectWorkflow: fnSelectWorkflow,
        fnRefreshWorkflow: fnRefreshWorkflow,
        fnCheckOriginDrift: fnCheckOriginDrift,
        fdictPullProjectRepo: fdictPullProjectRepo,
        fnToggleWorkflowDropdown: fnToggleWorkflowDropdown,
        fnHideWorkflowDropdown: fnHideWorkflowDropdown,
        fnSaveCurrentWorkflow: fnSaveCurrentWorkflow,
        fnOpenCreateWizard: fnOpenCreateWizard,
        fnOpenConvertWizard: fnOpenConvertWizard,
        fnBindCreateWizardModal: fnBindCreateWizardModal,
    };
})();
