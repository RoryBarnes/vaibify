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
# "`name` via vaibify-do" names an action as surely as "vaibify-do name".
_RE_ACTION_VIA_VAIBIFY_DO = re.compile(r"`([a-z][a-z0-9-]+)`\s+via\s+vaibify-do")
# A plural claim: every backticked name in the clause shares one safety.
_RE_PLURAL_SAFETY_CLAUSE = re.compile(
    r"\bare\s+(not agent-safe|user-only|agent-safe)\b", re.IGNORECASE)
_RE_KEBAB_NAME = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)+)`")


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
        listNamed = (
            _RE_VAIBIFY_DO_ACTION.findall(sText)
            + _RE_ACTION_VIA_VAIBIFY_DO.findall(_fsCollapseWhitespace(sText)))
        for sAction in listNamed:
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
        sFlat = _fsCollapseWhitespace(sText)
        listClaims = _RE_SAFETY_CLAIM.findall(sFlat) + _flistPluralClaims(sFlat)
        for sAction, sClaim in listClaims:
            if sAction not in dictSafety:
                listWrong.append(f"{sName}: `{sAction}` (not an action) "
                                 f"called {sClaim}")
                continue
            bClaimedSafe = sClaim.lower() == "agent-safe"
            if bClaimedSafe != dictSafety[sAction]:
                listWrong.append(f"{sName}: `{sAction}` called {sClaim}")
    assert listWrong == [], (
        "container guidance contradicts bAgentSafe: " + ", ".join(listWrong))


def _flistPluralClaims(sFlat):
    """Return ``(name, claim)`` for every name in a clause saying "are <claim>"."""
    listClaims = []
    for sClause in re.split(r"[.;]", sFlat):
        matchPlural = _RE_PLURAL_SAFETY_CLAUSE.search(sClause)
        if matchPlural:
            for sAction in _RE_KEBAB_NAME.findall(sClause[:matchPlural.start()]):
                listClaims.append((sAction, matchPlural.group(1)))
    return listClaims


_S_REPO_ROOT = os.path.dirname(os.path.dirname(_S_CONTAINER_IMAGE_DIR))
_RE_BOLD_LABEL = re.compile(r"\*\*([^*]+)\*\*")
_RE_JS_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _fsReadDashboardLabelCorpus():
    """Return every string the dashboard can show, as one searchable text.

    The frontend source, with HTML entities and JavaScript ``\\uXXXX``
    escapes decoded, plus the determinism question, whose wording the
    server ships to the page rather than the page holding a copy.
    """
    import html
    sStaticDir = os.path.join(_S_REPO_ROOT, "vaibify", "gui", "static")
    listTexts = []
    for sName in sorted(os.listdir(sStaticDir)):
        if sName.endswith((".js", ".html")):
            listTexts.append(_fsReadText(os.path.join(sStaticDir, sName)))
    listTexts.append(_fsReadText(os.path.join(
        _S_REPO_ROOT, "vaibify", "reproducibility", "determinismGate.py")))
    sCorpus = html.unescape("\n".join(listTexts))
    return _RE_JS_UNICODE_ESCAPE.sub(
        lambda match: chr(int(match.group(1), 16)), sCorpus)


@pytest.mark.falsification
def testEveryLabelTheDashboardMapQuotesIsOnTheDashboard():
    """A bold label in the dashboard-map skill is one the page really shows.

    Agents invented labels ("Environment record", a button named after
    an action ID) and sent a researcher looking for controls that did
    not exist (2026-10-27). The map is the one place agents copy labels
    from, so every label it quotes must be found in the dashboard.

    Kills: quoting a label the dashboard does not have, such as the
    invented "Environment record".
    """
    sCorpus = _fsReadDashboardLabelCorpus()
    sMap = _fsReadText(os.path.join(_S_SKILLS_DIR, "dashboard-map", "SKILL.md"))
    listMissing = [
        sLabel for sLabel in (
            _fsCollapseWhitespace(s) for s in _RE_BOLD_LABEL.findall(sMap))
        if sLabel not in sCorpus
    ]
    assert listMissing == [], (
        "the dashboard-map skill quotes labels the dashboard does not "
        "show: " + ", ".join(listMissing))


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
