"""The in-container guidance must agree with the actions it teaches.

An in-container agent reads two kinds of guidance: the skills under
``vaibify/containerImage/skills/`` and the instructions ``entrypoint.sh``
generates. Both name ``vaibify-do`` actions and say which ones the agent
may run. Neither is generated from the catalog, so both drifted: one
skill sent agents to ``vaibify-do manifest-check``, an action that has
never existed, and described a determinism declaration that had been
replaced (2026-10-27 audit). These tests bind the text to its sources of
truth, so the next drift fails here instead of in front of a researcher.
"""

import os
import re

import pytest

from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
from vaibify.reproducibility.determinismGate import (
    LIST_DETERMINISM_QUESTIONS,
)


_S_CONTAINER_IMAGE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "vaibify", "containerImage",
    )
)
_S_SKILLS_DIR = os.path.join(_S_CONTAINER_IMAGE_DIR, "skills")
_S_HEREDOC_OPEN = "<< 'CLAUDEMD'"
_S_HEREDOC_CLOSE = "\nCLAUDEMD\n"

# A command, not prose: inside a code span or at the start of a line,
# so "`vaibify-do` is the CLI" names no action called "is".
_RE_VAIBIFY_DO_ACTION = re.compile(
    r"(?:^|`)[ \t]*vaibify-do[ \t]+([a-z][a-z0-9-]+)", re.MULTILINE,
)
_RE_SAFETY_CLAIM = re.compile(
    r"`([a-z][a-z0-9-]+)`[^.;`]{0,40}?"
    r"\b(not agent-safe|user-only|agent-safe)\b",
    re.IGNORECASE,
)


def _fsReadText(sPath):
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def _fsReadGeneratedInstructions():
    """Return the instructions entrypoint.sh writes for container agents."""
    sSource = _fsReadText(
        os.path.join(_S_CONTAINER_IMAGE_DIR, "entrypoint.sh"))
    iStart = sSource.index(_S_HEREDOC_OPEN) + len(_S_HEREDOC_OPEN)
    iEnd = sSource.index(_S_HEREDOC_CLOSE, iStart)
    return sSource[iStart:iEnd]


def _flistContainerGuidance():
    """Return ``[(sName, sText)]`` for every text a container agent reads."""
    listGuidance = [("generated instructions", _fsReadGeneratedInstructions())]
    for sSkillName in sorted(os.listdir(_S_SKILLS_DIR)):
        sPath = os.path.join(_S_SKILLS_DIR, sSkillName, "SKILL.md")
        if os.path.isfile(sPath):
            listGuidance.append((sSkillName, _fsReadText(sPath)))
    return listGuidance


def _fsCollapseWhitespace(sText):
    return " ".join(sText.split())


def _fdictAgentSafetyByAction():
    return {
        dictAction["sName"]: bool(dictAction.get("bAgentSafe"))
        for dictAction in LIST_AGENT_ACTIONS
    }


@pytest.mark.falsification
def testEveryVaibifyDoActionInContainerGuidanceExists():
    """Every ``vaibify-do <action>`` a container agent is taught must exist.

    Kills: naming an action the catalog does not hold -- the
    ``vaibify-do manifest-check`` instruction that exited "unknown
    action" for every agent that followed it.
    """
    setKnown = set(_fdictAgentSafetyByAction())
    listUnknown = []
    for sName, sText in _flistContainerGuidance():
        for sAction in _RE_VAIBIFY_DO_ACTION.findall(sText):
            if sAction not in setKnown:
                listUnknown.append(f"{sName}: vaibify-do {sAction}")
    assert listUnknown == [], (
        "container guidance names actions the catalog does not hold: "
        + ", ".join(sorted(set(listUnknown))))


@pytest.mark.falsification
def testContainerGuidanceAgreesWithAgentSafety():
    """A claim that an action is user-only or agent-safe must be the catalog's.

    Kills: guidance calling a user-only action agent-safe, which teaches
    an agent to attempt what the server refuses (and, worse, to tell the
    researcher a choice that is theirs has been made for them).
    """
    dictSafety = _fdictAgentSafetyByAction()
    listWrong = []
    for sName, sText in _flistContainerGuidance():
        for sAction, sClaim in _RE_SAFETY_CLAIM.findall(
                _fsCollapseWhitespace(sText)):
            if sAction not in dictSafety:
                continue
            bClaimedSafe = sClaim.lower() == "agent-safe"
            if bClaimedSafe != dictSafety[sAction]:
                listWrong.append(f"{sName}: `{sAction}` called {sClaim}")
    assert listWrong == [], (
        "container guidance contradicts bAgentSafe: " + ", ".join(listWrong))


@pytest.mark.falsification
def testProofLadderQuotesTheLiveDeterminismQuestion():
    """The proof-ladder skill quotes each determinism question as the gate asks it.

    Kills: changing the question in determinismGate.py without the skill,
    the drift that left agents describing three retired questions for
    ten days after the one question replaced them.
    """
    sSkill = _fsCollapseWhitespace(_fsReadText(
        os.path.join(_S_SKILLS_DIR, "proof-ladder", "SKILL.md")))
    for dictQuestion in LIST_DETERMINISM_QUESTIONS:
        assert dictQuestion["sPlainQuestion"] in sSkill
        for dictChoice in dictQuestion["listChoices"]:
            assert dictChoice["sLabel"] in sSkill
