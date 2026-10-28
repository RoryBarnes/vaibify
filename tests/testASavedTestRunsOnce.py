"""A test saved through save-and-run-test runs once, not once per spelling.

A passing save-and-run-test registered ``python -m pytest '<full path>'
-v`` in ``saTestCommands`` even when a declared category already ran the
same file as ``python3 -m pytest tests/<file>`` from the step directory.
The groups the runner executes de-duplicate by exact string, so the two
spellings both ran: every later test run executed that test twice, once
in its category and once in the legacy group (2026-10-27 audit).
"""

import pytest

from vaibify.gui import workflowManager
from vaibify.gui.testStatusManager import _fnRegisterTestCommand


_S_PROJECT_REPO = "/workspace/project"


def _fdictStepWithAQualitativeCategory():
    return {
        "sDirectory": "Analysis",
        "dictTests": {"dictQualitative": {
            "sFilePath": "Analysis/tests/test_qualitative.py",
            "saCommands": ["python3 -m pytest tests/test_qualitative.py"],
        }},
        "saTestCommands": [],
    }


def _flistCommandsNaming(dictStep, sFileName):
    listCommands = []
    for listGroup in workflowManager.fdictResolveTestCommandGroups(
            dictStep).values():
        listCommands.extend(s for s in listGroup if sFileName in s)
    return listCommands


@pytest.mark.falsification
def testAFileACategoryRunsIsNotRegisteredASecondTime():
    """Kills: dropping the category check from the registration."""
    dictStep = _fdictStepWithAQualitativeCategory()
    _fnRegisterTestCommand(
        dictStep, True,
        _S_PROJECT_REPO + "/Analysis/tests/test_qualitative.py",
        _S_PROJECT_REPO,
    )
    assert dictStep["saTestCommands"] == []
    assert len(_flistCommandsNaming(dictStep, "test_qualitative.py")) == 1


def testAFileNoCategoryRunsIsStillRegistered():
    dictStep = _fdictStepWithAQualitativeCategory()
    _fnRegisterTestCommand(
        dictStep, True,
        _S_PROJECT_REPO + "/Analysis/tests/test_extra.py",
        _S_PROJECT_REPO,
    )
    assert len(_flistCommandsNaming(dictStep, "test_extra.py")) == 1
