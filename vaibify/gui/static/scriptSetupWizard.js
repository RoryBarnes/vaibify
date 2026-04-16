/* Vaibify — Setup wizard form handling */

var VaibifySetup = (function () {
    "use strict";

    var listRepositories = [];
    var sSelectedTemplate = null;

    /* --- Initialization --- */

    function fnInitialize() {
        fnLoadTemplates();
        fnBindFormEvents();
        fnLoadExistingConfig();
    }

    /* --- Templates --- */

    async function fnLoadTemplates() {
        var elGrid = document.getElementById("templateGrid");
        try {
            var response = await fetch("/api/setup/templates");
            if (!response.ok) {
                throw new Error("Failed to load templates");
            }
            var listTemplates = await response.json();
            fnRenderTemplates(listTemplates);
        } catch (error) {
            elGrid.innerHTML =
                '<p class="muted-text">No templates available</p>';
        }
    }

    function fnRenderTemplates(listTemplates) {
        var elGrid = document.getElementById("templateGrid");
        if (!listTemplates || listTemplates.length === 0) {
            elGrid.innerHTML =
                '<p class="muted-text">No templates available</p>';
            return;
        }
        elGrid.innerHTML = listTemplates.map(function (dictTemplate) {
            return (
                '<div class="template-card" data-template="' +
                fnEscapeHtml(dictTemplate.sName) + '">' +
                '<div class="template-name">' +
                fnEscapeHtml(dictTemplate.sName) + '</div>' +
                '<div class="template-description">' +
                fnEscapeHtml(dictTemplate.sDescription || "") +
                '</div></div>'
            );
        }).join("");

        elGrid.querySelectorAll(".template-card").forEach(function (el) {
            el.addEventListener("click", function () {
                fnSelectTemplate(el.dataset.template, listTemplates);
            });
        });
    }

    function fnSelectTemplate(sName, listTemplates) {
        sSelectedTemplate = sName;
        document.querySelectorAll(".template-card").forEach(function (el) {
            el.classList.toggle(
                "selected", el.dataset.template === sName
            );
        });
        var dictTemplate = null;
        for (var i = 0; i < listTemplates.length; i++) {
            if (listTemplates[i].sName === sName) {
                dictTemplate = listTemplates[i];
                break;
            }
        }
        if (dictTemplate && dictTemplate.dictConfig) {
            fnPopulateForm(dictTemplate.dictConfig);
        }
    }

    /* --- Form Binding --- */

    function fnSyncFeatureCardCheckedClass(elCheckbox) {
        var elCard = elCheckbox.closest(".feature-card");
        if (elCard) {
            elCard.classList.toggle("checked", elCheckbox.checked);
        }
    }

    function fnToggleClaudeAutoUpdateVisibility() {
        var elClaude = document.getElementById("featureClaude");
        var elRow = document.getElementById("claudeAutoUpdateRow");
        if (elClaude && elRow) {
            elRow.style.display = elClaude.checked ? "" : "none";
        }
    }

    function fnBindFormEvents() {
        document.querySelectorAll(
            '.feature-card input[type="checkbox"]'
        ).forEach(function (elCheckbox) {
            elCheckbox.addEventListener("change", function () {
                fnSyncFeatureCardCheckedClass(elCheckbox);
            });
        });
        var elClaude = document.getElementById("featureClaude");
        if (elClaude) {
            elClaude.addEventListener(
                "change", fnToggleClaudeAutoUpdateVisibility
            );
        }

        document.getElementById("btnAddRepo").addEventListener(
            "click", fnAddRepository
        );
        document.getElementById("repoInput").addEventListener(
            "keydown", function (event) {
                if (event.key === "Enter") {
                    event.preventDefault();
                    fnAddRepository();
                }
            }
        );
        document.getElementById("btnSaveConfig").addEventListener(
            "click", fnSaveConfig
        );
        document.getElementById("btnBuildContainer").addEventListener(
            "click", fnBuildContainer
        );
    }

    /* --- Repository Management --- */

    function fnAddRepository() {
        var elInput = document.getElementById("repoInput");
        var sUrl = elInput.value.trim();
        if (!sUrl) return;

        listRepositories.push(sUrl);
        elInput.value = "";
        fnRenderRepositories();
    }

    function fnRemoveRepository(iIndex) {
        listRepositories.splice(iIndex, 1);
        fnRenderRepositories();
    }

    function fnRenderRepositories() {
        var elList = document.getElementById("repoList");
        if (listRepositories.length === 0) {
            elList.innerHTML =
                '<p class="muted-text">No repositories added yet.</p>';
            return;
        }
        elList.innerHTML = listRepositories.map(function (sUrl, iIndex) {
            return (
                '<div class="repo-item" data-index="' + iIndex + '">' +
                '<span class="repo-url">' +
                fnEscapeHtml(sUrl) + '</span>' +
                '<button type="button" class="repo-remove" ' +
                'title="Remove">&times;</button>' +
                '</div>'
            );
        }).join("");

        elList.querySelectorAll(".repo-remove").forEach(function (el) {
            el.addEventListener("click", function () {
                var iIndex = parseInt(
                    el.closest(".repo-item").dataset.index
                );
                fnRemoveRepository(iIndex);
            });
        });
    }

    /* --- Form Population --- */

    function fnPopulateForm(dictConfig) {
        fnSetInputValue("projectName", dictConfig.sProjectName);
        fnSetInputValue("containerUser", dictConfig.sContainerUser);
        fnSetInputValue("pythonVersion", dictConfig.sPythonVersion);
        fnSetInputValue("baseImage", dictConfig.sBaseImage);
        fnSetInputValue("workspaceRoot", dictConfig.sWorkspaceRoot);
        fnSetInputValue("packageManager", dictConfig.sPackageManager);
        fnSetInputValue(
            "overleafProjectId", dictConfig.sOverleafProjectId
        );
        fnSetInputValue(
            "zenodoDepositionId", dictConfig.sZenodoDepositionId
        );
        document.getElementById("neverSleep").checked = Boolean(
            dictConfig.bNeverSleep
        );

        if (dictConfig.listRepositories) {
            listRepositories = dictConfig.listRepositories.slice();
            fnRenderRepositories();
        }

        if (dictConfig.listFeatures) {
            fnSetFeatureCheckboxes(dictConfig.listFeatures);
        }

        fnSetClaudeAutoUpdate(dictConfig.bClaudeAutoUpdate);

        if (dictConfig.listPipPackages) {
            document.getElementById("pipPackages").value =
                dictConfig.listPipPackages.join("\n");
        }
        if (dictConfig.listAptPackages) {
            document.getElementById("aptPackages").value =
                dictConfig.listAptPackages.join("\n");
        }
    }

    function fnSetInputValue(sElementId, sValue) {
        var el = document.getElementById(sElementId);
        if (el && sValue !== undefined && sValue !== null) {
            el.value = sValue;
        }
    }

    function fnSetClaudeAutoUpdate(bValue) {
        var el = document.getElementById("claudeAutoUpdate");
        if (el) {
            el.checked = bValue !== false;
        }
        fnToggleClaudeAutoUpdateVisibility();
    }

    function fnSetFeatureCheckboxes(listFeatures) {
        var dictFeatureMap = {
            jupyter: "featureJupyter",
            rLanguage: "featureRLanguage",
            julia: "featureJulia",
            database: "featureDatabase",
            dvc: "featureDvc",
            latex: "featureLatex",
            claude: "featureClaude",
            gpu: "featureGpu",
        };
        Object.keys(dictFeatureMap).forEach(function (sKey) {
            var el = document.getElementById(dictFeatureMap[sKey]);
            if (el) {
                el.checked = listFeatures.indexOf(sKey) >= 0;
                fnSyncFeatureCardCheckedClass(el);
            }
        });
    }

    /* --- Form Reading --- */

    function fdictBuildConfigFromForm() {
        var listFeatures = [];
        document.querySelectorAll(
            'input[name="features"]:checked'
        ).forEach(function (el) {
            listFeatures.push(el.value);
        });

        return {
            sProjectName: document.getElementById("projectName").value.trim(),
            sContainerUser: document.getElementById(
                "containerUser"
            ).value.trim(),
            sPythonVersion: document.getElementById(
                "pythonVersion"
            ).value.trim(),
            sBaseImage: document.getElementById("baseImage").value.trim(),
            sWorkspaceRoot: document.getElementById(
                "workspaceRoot"
            ).value.trim(),
            sPackageManager: document.getElementById(
                "packageManager"
            ).value,
            listRepositories: listRepositories.slice(),
            listFeatures: listFeatures,
            listPipPackages: flistParseTextarea("pipPackages"),
            listAptPackages: flistParseTextarea("aptPackages"),
            sOverleafProjectId: document.getElementById(
                "overleafProjectId"
            ).value.trim(),
            sZenodoDepositionId: document.getElementById(
                "zenodoDepositionId"
            ).value.trim(),
            bNeverSleep: document.getElementById(
                "neverSleep"
            ).checked,
            bClaudeAutoUpdate: fbReadClaudeAutoUpdate(),
        };
    }

    function fbReadClaudeAutoUpdate() {
        var el = document.getElementById("claudeAutoUpdate");
        return el ? el.checked : true;
    }

    function flistParseTextarea(sElementId) {
        var sValue = document.getElementById(sElementId).value.trim();
        if (!sValue) return [];
        return sValue.split("\n").filter(function (sLine) {
            return sLine.trim().length > 0;
        }).map(function (sLine) {
            return sLine.trim();
        });
    }

    /* --- Validation --- */

    function fbValidateForm() {
        var sProjectName = document.getElementById(
            "projectName"
        ).value.trim();
        if (!sProjectName) {
            fnShowToast("Project name is required", "error");
            document.getElementById("projectName").focus();
            return false;
        }
        return true;
    }

    /* --- Save --- */

    async function fnSaveConfig() {
        if (!fbValidateForm()) return;

        var dictConfig = fdictBuildConfigFromForm();
        try {
            var response = await fetch("/api/setup/save", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(dictConfig),
            });
            if (!response.ok) {
                var dictError = await response.json();
                throw new Error(
                    dictError.detail || "Save failed"
                );
            }
            fnShowToast("Configuration saved", "success");
        } catch (error) {
            fnShowToast(
                "Save failed: " + error.message, "error"
            );
        }
    }

    /* --- Build --- */

    async function fnBuildContainer() {
        if (!fbValidateForm()) return;

        var dictConfig = fdictBuildConfigFromForm();
        var elButton = document.getElementById("btnBuildContainer");
        elButton.disabled = true;
        elButton.textContent = "Building...";

        try {
            var response = await fetch("/api/setup/build", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(dictConfig),
            });
            if (!response.ok) {
                var dictError = await response.json();
                throw new Error(
                    dictError.detail || "Build failed"
                );
            }
            var dictResult = await response.json();
            fnShowToast(
                dictResult.sMessage || "Build started", "success"
            );
        } catch (error) {
            fnShowToast(
                "Build failed: " + error.message, "error"
            );
        } finally {
            elButton.disabled = false;
            elButton.textContent = "Build Container";
        }
    }

    /* --- Load Existing Config --- */

    async function fnLoadExistingConfig() {
        try {
            var response = await fetch("/api/setup/config");
            if (response.ok) {
                var dictConfig = await response.json();
                if (dictConfig && dictConfig.sProjectName) {
                    fnPopulateForm(dictConfig);
                }
            }
        } catch (error) {
            /* No existing config, that is fine */
        }
    }

    /* --- Toast Notifications --- */

    function fnShowToast(sMessage, sType) {
        var el = document.createElement("div");
        el.className = "toast " + (sType || "");
        el.textContent = sMessage;
        document.getElementById("toastContainer").appendChild(el);
        setTimeout(function () { el.remove(); }, 4000);
    }

    /* --- Utilities --- */

    function fnEscapeHtml(sText) {
        var el = document.createElement("span");
        el.textContent = sText;
        return el.innerHTML;
    }

    return {
        fnInitialize: fnInitialize,
    };
})();

document.addEventListener("DOMContentLoaded",
    VaibifySetup.fnInitialize
);
