"""The AI Declaration step's file buttons do what they say.

The step offered "Generate template", "Choose existing file" and
"Choose different file" with no click handler behind any of them, and
a step added through the dashboard pointed at an AI_USAGE.md nobody
had written, so its preview read "Declaration file could not be read"
while Generate never appeared. The buttons now ask the server whether
the file exists, offer Generate only when it is ABSENT, and attach a
chosen or generated file through one route.

This drives the real buttons against the host lane's real files: the
seeded declaration step points at an AI_USAGE.md that does not exist.
Every assertion reads the screen AND the saved project file or the
file on disk, because a toast or a re-render can agree with itself
while nothing was written.
"""

import json
import os
import time

import pytest

from tests.browser.conftest import (
    S_HOST_DECLARATION_STEP_NAME,
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

F_SAVE_WAIT_SECONDS = 15.0
S_DEFAULT_DECLARATION = "AI_USAGE.md"
S_CHOSEN_DIRECTORY = "notes"
S_CHOSEN_NAME = "aiUseStatement.md"
S_CHOSEN_RELATIVE = S_CHOSEN_DIRECTORY + "/" + S_CHOSEN_NAME
S_CHOSEN_TEXT = "# How AI assisted\n\nWritten by hand for this test.\n"
S_DECLARATION_BODY = (
    f'.step-wrapper:has-text("{S_HOST_DECLARATION_STEP_NAME}")'
)


def _fsProjectDirectory(serverHub):
    return os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)


def _fsSavedDeclarationFile(serverHub):
    sWorkflowPath = os.path.join(
        _fsProjectDirectory(serverHub), ".vaibify", "projects",
        S_HOST_WORKFLOW_NAME + ".json",
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = json.load(fileWorkflow)
    for dictStep in dictWorkflow["listSteps"]:
        if dictStep.get("sStepKind") == "ai-declaration":
            return dictStep.get("sDeclarationFile", "")
    raise AssertionError("the seeded workflow lost its declaration step")


def _fnWaitForSavedDeclarationFile(serverHub, sExpected):
    fDeadline = time.monotonic() + F_SAVE_WAIT_SECONDS
    sSaved = _fsSavedDeclarationFile(serverHub)
    while sSaved != sExpected and time.monotonic() < fDeadline:
        time.sleep(0.25)
        sSaved = _fsSavedDeclarationFile(serverHub)
    assert sSaved == sExpected, (
        f"the project file still points the declaration at {sSaved!r}, "
        f"not {sExpected!r}: the screen changed but nothing was saved"
    )


def _fnPickFromTheListing(pageDashboard, serverHub):
    """Navigate the picker into the chosen file by clicking entries."""
    sProject = _fsProjectDirectory(serverHub)
    pageDashboard.click(
        f'#filePickerEntries .file-item[data-path='
        f'"{sProject}/{S_CHOSEN_DIRECTORY}"]',
    )
    pageDashboard.click(
        f'#filePickerEntries .file-item[data-path='
        f'"{sProject}/{S_CHOSEN_RELATIVE}"]',
    )
    assert pageDashboard.input_value("#inputFilePickerPath") == (
        S_CHOSEN_RELATIVE
    )
    pageDashboard.click("#btnFilePickerAdd")


def _fnCloseThePickerWithoutChoosing(pageDashboard, serverHub):
    """Assert the picker opened inside the repository, then cancel."""
    pageDashboard.wait_for_selector("#modalFilePicker", timeout=10000)
    pageDashboard.wait_for_selector(
        f'#filePickerEntries .file-item[data-path='
        f'"{_fsProjectDirectory(serverHub)}/{S_CHOSEN_DIRECTORY}"]',
        timeout=10000,
    )
    pageDashboard.click("#btnFilePickerCancel")


@pytest.mark.falsification
def test_generate_and_choose_attach_a_real_file(pageDashboard, serverHub):
    """Kills: the Generate button losing its click handler."""
    sProject = _fsProjectDirectory(serverHub)
    sDefaultPath = os.path.join(sProject, S_DEFAULT_DECLARATION)
    sChosenPath = os.path.join(sProject, S_CHOSEN_RELATIVE)
    assert not os.path.exists(sDefaultPath), (
        "precondition: the seeded declaration file must be absent"
    )
    os.makedirs(os.path.dirname(sChosenPath), exist_ok=True)
    with open(sChosenPath, "w") as fileChosen:
        fileChosen.write(S_CHOSEN_TEXT)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        pageDashboard.click(
            f'.step-item:has-text("{S_HOST_DECLARATION_STEP_NAME}")')

        # 1. Absent: the server said so, so Generate is offered beside
        #    Choose, and no preview pretends the file can be read.
        elGenerate = pageDashboard.locator(
            S_DECLARATION_BODY + " .btn-ai-declaration-generate")
        elGenerate.wait_for(state="visible", timeout=15000)
        assert S_DEFAULT_DECLARATION in elGenerate.inner_text()
        assert pageDashboard.locator(
            S_DECLARATION_BODY + " .ai-declaration-preview").count() == 0

        # 2. "Choose existing file" opens the repository picker.
        pageDashboard.click(
            S_DECLARATION_BODY +
            ' .btn-ai-declaration-choose:has-text("Choose existing file")')
        _fnCloseThePickerWithoutChoosing(pageDashboard, serverHub)

        # 3. Generate writes the template at the attached path, and the
        #    preview then shows the template's own heading.
        elGenerate.click()
        pageDashboard.wait_for_selector(
            S_DECLARATION_BODY +
            ' .ai-declaration-preview:has-text("AI Usage Declaration")',
            timeout=15000)
        with open(sDefaultPath) as fileGenerated:
            assert fileGenerated.read().startswith(
                "# AI Usage Declaration")
        _fnWaitForSavedDeclarationFile(serverHub, S_DEFAULT_DECLARATION)
        assert pageDashboard.locator(
            S_DECLARATION_BODY + " .btn-ai-declaration-generate",
        ).count() == 0, "Generate is still offered over a present file"

        # 4. "Choose different file" attaches the picked file, and the
        #    preview shows THAT file's text.
        pageDashboard.click(
            S_DECLARATION_BODY +
            ' .btn-ai-declaration-choose:has-text("Choose different file")')
        pageDashboard.wait_for_selector("#modalFilePicker", timeout=10000)
        _fnPickFromTheListing(pageDashboard, serverHub)
        pageDashboard.wait_for_selector(
            S_DECLARATION_BODY +
            ' .ai-declaration-preview:has-text("Written by hand")',
            timeout=15000)
        assert pageDashboard.locator(
            S_DECLARATION_BODY + " .ai-declaration-file code",
        ).inner_text() == S_CHOSEN_RELATIVE
        _fnWaitForSavedDeclarationFile(serverHub, S_CHOSEN_RELATIVE)

        # 5. Put the seeded attachment back, through the same route.
        pageDashboard.click(
            S_DECLARATION_BODY +
            ' .btn-ai-declaration-choose:has-text("Choose different file")')
        pageDashboard.wait_for_selector("#modalFilePicker", timeout=10000)
        pageDashboard.fill("#inputFilePickerPath", S_DEFAULT_DECLARATION)
        pageDashboard.click("#btnFilePickerAdd")
        _fnWaitForSavedDeclarationFile(serverHub, S_DEFAULT_DECLARATION)
        assert pageDashboard.listPageErrors == []
    finally:
        for sPath in (sDefaultPath, sChosenPath):
            if os.path.exists(sPath):
                os.remove(sPath)
