"""Frontend contract checks for the wizard directory picker.

Verifies that PipeleyenDirectoryBrowser exposes the new "create" mode
API, that scriptWorkflowManager wires it into the wizard's first step,
and that the index.html markup carries the new DOM nodes. Mirrors the
string-presence pattern in testReposPanelFrontendContract.py.
"""

import os
import re

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsExtractReturnBlock(sSource):
    iStart = sSource.rfind("return {")
    assert iStart != -1, "IIFE return block not found"
    iEnd = sSource.find("};", iStart)
    assert iEnd != -1, "IIFE return block not terminated"
    return sSource[iStart:iEnd]


def test_directory_browser_exposes_create_mode():
    sSource = _fsReadStaticFile("scriptDirectoryBrowser.js")
    sReturnBlock = _fsExtractReturnBlock(sSource)
    for sName in ("fnOpenForCreate", "fnPromptCreateFolder",
                  "fnHandleModalClose"):
        assert sName in sReturnBlock, (
            sName + " missing from IIFE return block"
        )


def test_directory_browser_has_mode_state():
    sSource = _fsReadStaticFile("scriptDirectoryBrowser.js")
    assert "_sBrowserMode" in sSource
    assert '"existing"' in sSource
    assert '"create"' in sSource


def test_workflow_manager_uses_directory_picker():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert "PipeleyenDirectoryBrowser.fnOpenForCreate" in sSource
    assert "btnWizardChooseDirectory" in sSource


def test_index_html_has_picker_elements():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="btnDirectoryNewFolder"' in sSource
    assert 'id="directoryBrowserSubtitle"' in sSource


def test_index_html_has_wizard_selected_path():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert 'id="wizardSelectedPath"' in sSource


def test_container_manager_binds_new_folder_button():
    sSource = _fsReadStaticFile("scriptContainerManager.js")
    assert "btnDirectoryNewFolder" in sSource
    assert "fnPromptCreateFolder" in sSource
    assert "fnHandleModalClose" in sSource


def test_wizard_has_features_step():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert '"Features & Authentication"' in sSource
    assert "_fnRenderStepFeatures" in sSource
    assert "wizardFeatureList" in sSource
    for sFeature in ("claude", "jupyter", "gpu", "latex",
                     "rLanguage", "julia", "database", "dvc"):
        assert "\"" + sFeature + "\"" in sSource, (
            "feature " + sFeature + " missing from wizard"
        )


def test_wizard_has_packages_step():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert '"Packages"' in sSource
    assert "_fnRenderStepPackages" in sSource
    assert "wizardSystemPackages" in sSource
    assert "wizardPythonPackages" in sSource
    assert "wizardPackageManager" in sSource


def test_wizard_has_github_auth_toggle():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert "wizardUseGithubAuth" in sSource
    assert "bUseGithubAuth" in sSource


def test_wizard_macos_only_neversleep():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert "_fbIsMacOs" in sSource
    assert "wizardNeverSleep" in sSource
    iIdx = sSource.find("function _fsRenderRuntimeTogglesSection")
    assert iIdx != -1, "render function definition missing"
    sBody = sSource[iIdx:iIdx + 800]
    assert "_fbIsMacOs" in sBody, (
        "neverSleep row must be gated on macOS detection"
    )


def test_wizard_data_includes_all_new_fields():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iIdx = sSource.find("_fdictBuildDefaultWizardData")
    assert iIdx != -1
    sBody = sSource[iIdx:iIdx + 1500]
    for sField in ("listFeatures", "bUseGithubAuth", "bNeverSleep",
                   "bNetworkIsolation", "listSystemPackages",
                   "listPythonPackages", "listCondaPackages",
                   "sPackageManager", "sPipInstallFlags",
                   "sContainerUser", "sBaseImage", "sWorkspaceRoot"):
        assert sField in sBody, (
            sField + " missing from wizard default state"
        )


def test_wizard_help_button_in_html():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="btnWizardHelp"' in sSource
    assert 'class="wizard-title-row"' in sSource
    assert 'class="wizard-help-button"' in sSource


def test_wizard_help_array_has_eight_entries():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iIdx = sSource.find("_LIST_WIZARD_HELP")
    assert iIdx != -1, "_LIST_WIZARD_HELP missing"
    iEndArray = sSource.find("];", iIdx)
    sBlock = sSource[iIdx:iEndArray]
    iCount = sBlock.count("<p>")
    assert iCount >= 8, (
        "_LIST_WIZARD_HELP should contain at least 8 <p> blocks "
        "(one per step), got " + str(iCount)
    )


def test_wizard_help_click_handler_wired():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    assert "_fnHandleWizardHelpClick" in sSource
    assert "btnWizardHelp" in sSource
    assert "PipeleyenModals.fnShowInfoModal" in sSource


def test_info_modal_exported_by_modals_module():
    sSource = _fsReadStaticFile("scriptModals.js")
    assert "fnShowInfoModal" in sSource
    iReturn = sSource.rfind("return {")
    sReturnBlock = sSource[iReturn:sSource.find("};", iReturn)]
    assert "fnShowInfoModal" in sReturnBlock, (
        "fnShowInfoModal must be in the IIFE return block"
    )


def test_info_modal_raised_above_wizard():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iIdx = sSource.find("function _fnRaiseInfoModalAboveWizard")
    assert iIdx != -1, "_fnRaiseInfoModalAboveWizard missing"
    sBody = sSource[iIdx:iIdx + 400]
    assert "modalInfo" in sBody
    assert "1200" in sBody, (
        "info modal z-index must exceed wizard z-index 1000"
    )


def test_add_choice_modal_has_help_button():
    sSource = _fsReadStaticFile("index.html")
    assert 'id="btnAddChoiceHelp"' in sSource
    iIdx = sSource.find('id="modalAddChoice"')
    assert iIdx != -1
    iEnd = sSource.find("</div>\n</div>", iIdx)
    sBlock = sSource[iIdx:iEnd]
    assert 'btnAddChoiceHelp' in sBlock, (
        "help button must live inside modalAddChoice"
    )
    assert 'wizard-title-row' in sBlock, (
        "modalAddChoice must use the wizard-title-row layout"
    )


def test_add_choice_help_handler_wired():
    sSource = _fsReadStaticFile("scriptContainerManager.js")
    assert "_fnShowAddChoiceHelp" in sSource
    assert "_S_ADD_CHOICE_HELP" in sSource
    assert "btnAddChoiceHelp" in sSource
    assert "PipeleyenModals.fnShowInfoModal" in sSource


def test_add_choice_help_text_explains_both_paths():
    sSource = _fsReadStaticFile("scriptContainerManager.js")
    iIdx = sSource.find("_S_ADD_CHOICE_HELP")
    assert iIdx != -1
    sBlock = sSource[iIdx:iIdx + 2000]
    assert "Add Existing" in sBlock
    assert "Create New" in sBlock
    assert "vaibify.yml" in sBlock


def test_python_version_help_explains_why_needed():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iIdx = sSource.find("_LIST_WIZARD_HELP")
    iEnd = sSource.find("];", iIdx)
    sHelp = sSource[iIdx:iEnd]
    assert "Why vaibify needs to know this" in sHelp, (
        "python version blurb must explain the vaibify-internal "
        "reason this choice exists"
    )
    assert "PYTHON_VERSION" in sHelp


def test_wizard_help_text_mentions_critical_concepts():
    sSource = _fsReadStaticFile("scriptWorkflowManager.js")
    iIdx = sSource.find("_LIST_WIZARD_HELP")
    iEnd = sSource.find("];", iIdx)
    sHelp = sSource[iIdx:iEnd]
    for sConcept in ("gh auth", "pip install -e",
                     "Most users leave both textareas empty",
                     "Claude Code", "Rebuild"):
        assert sConcept in sHelp, (
            "help text should mention: " + sConcept
        )


def test_wizard_progress_has_eight_dots():
    sSource = _fsReadStaticFile("index.html")
    iStart = sSource.find('class="wizard-progress"')
    assert iStart != -1
    iEnd = sSource.find("</div>", iStart)
    sBlock = sSource[iStart:iEnd]
    iCount = sBlock.count("wizard-progress-step")
    assert iCount == 8, (
        "wizard-progress must have 8 dots, got " + str(iCount)
    )


def test_input_modal_raised_above_picker():
    """fnPromptCreateFolder must lift the input modal above the
    picker's z-index, otherwise it renders hidden behind the picker.
    The picker is at 1100, so the input modal must exceed 1100.
    """
    sSource = _fsReadStaticFile("scriptDirectoryBrowser.js")
    iPrompt = sSource.find("function fnPromptCreateFolder")
    assert iPrompt != -1, "fnPromptCreateFolder missing"
    iEnd = sSource.find("\n    }", iPrompt)
    sPromptBody = sSource[iPrompt:iEnd]
    assert "_fnRaiseInputModalAbovePicker" in sPromptBody, (
        "fnPromptCreateFolder must call the z-index raiser "
        "or the input modal will hide behind the picker"
    )
    assert 'modalInput' in sSource, "modalInput id must be referenced"
    iRaiser = sSource.find("_fnRaiseInputModalAbovePicker")
    iRaiserDef = sSource.find(
        "function _fnRaiseInputModalAbovePicker", iRaiser)
    assert iRaiserDef != -1
    iRaiserEnd = sSource.find("\n    }", iRaiserDef)
    sBody = sSource[iRaiserDef:iRaiserEnd]
    iZ = int("".join(c for c in sBody.split("zIndex")[1]
                     if c.isdigit())[:4])
    assert iZ > 1100, (
        "Input modal z-index must exceed picker z-index 1100; "
        "got " + str(iZ)
    )


def test_directory_browser_functions_under_twenty_lines():
    sSource = _fsReadStaticFile("scriptDirectoryBrowser.js")
    listLines = sSource.split("\n")
    patFunc = re.compile(r"^\s*(async\s+)?function\s+(\w+)\s*\(")
    iIdx = 0
    listOver = []
    while iIdx < len(listLines):
        match = patFunc.match(listLines[iIdx])
        if match:
            sName = match.group(2)
            iDepth = 0
            iStart = iIdx
            bFoundOpen = False
            while iIdx < len(listLines):
                iDepth += listLines[iIdx].count("{")
                iDepth -= listLines[iIdx].count("}")
                if "{" in listLines[iIdx]:
                    bFoundOpen = True
                if bFoundOpen and iDepth == 0:
                    break
                iIdx += 1
            iLength = iIdx - iStart + 1
            if iLength > 20:
                listOver.append((sName, iLength))
        iIdx += 1
    assert not listOver, (
        "Functions over 20 lines: " + str(listOver)
    )
