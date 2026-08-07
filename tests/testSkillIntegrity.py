"""Referential-integrity tests for agent skill files (.claude/skills/*/SKILL.md).

A recipe skill is dense with references to concrete files, symbols, and
test names. Its dominant failure mode is silent staleness: a refactor
renames a function or moves a module, and the skill keeps confidently
instructing agents to use things that no longer exist. Nothing crashes;
the skill just quietly degrades into misinformation. These tests turn
that drift into a test failure.

Path references are checked by delegating to tools/checkAgentDocsPaths.py
(the same checker CI runs on AGENTS.md), so path logic lives in exactly
one place. Symbol and test-name checks are unique to this file: a
referenced identifier must still occur somewhere in the package source,
and a referenced test must still be defined in tests/. Occurrence, not
definition, is the assertion — the threat is a rename that leaves the
skill pointing at nothing, and a rename removes every occurrence.

The evaluation harnesses that exercise skills behaviorally (trigger
classification and A/B outcome runs) live in tools/evaluateSkillTriggers.py
and tools/evaluateSkillOutcomes.py; see docs/skillTesting.md.
"""

import importlib.util
import re
from pathlib import Path

import pytest


__all__ = [
    "testEverySkillHasValidFrontmatter",
    "testSkillPathReferencesResolve",
    "testThePathCheckerRefusesToPassOnAnEmptyScan",
    "testTheTreeExclusionIsRelativeToTheRepositoryRoot",
    "testSkillSymbolReferencesResolve",
    "testSkillTestNameReferencesResolve",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"
TESTS_ROOT = REPO_ROOT / "tests"

# Directories whose Python source constitutes the symbol corpus: a symbol
# referenced by a skill must occur somewhere in these trees.
LIST_SYMBOL_CORPUS_DIRECTORIES = ["vaibify", "tools"]

# Hungarian-notation function or constant identifiers, the only backticked
# spans treated as symbol references. Anything else in backticks (variable
# examples, module names, shell fragments) is ignored.
REGEX_FUNCTION_SYMBOL = re.compile(r"^_{0,2}f[a-z]*[A-Z][A-Za-z0-9]*$")
REGEX_CONSTANT_SYMBOL = re.compile(r"^_?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

REGEX_FENCED_CODE_BLOCK = re.compile(r"```.*?```", re.S)
REGEX_INLINE_CODE_SPAN = re.compile(r"`([^`\n]+)`")

# Test names are extracted from the full skill text (fenced blocks
# included, because pytest commands live in bash blocks). A match followed
# by ".", "*", or "<" is a filename stem, glob, or placeholder, not a test.
REGEX_TEST_NAME = re.compile(r"\btest[A-Z_]\w+")
SET_NON_TEST_FOLLOWERS = {".", "*", "<"}


def flistFindSkillFiles():
    """Return every SKILL.md under the repository's skill root."""
    if not SKILLS_ROOT.exists():
        return []
    return sorted(SKILLS_ROOT.rglob("SKILL.md"))


def fdictParseFrontmatter(pathSkill):
    """Return the YAML-frontmatter key/value pairs of a SKILL.md."""
    listLines = pathSkill.read_text(encoding="utf-8").splitlines()
    if not listLines or listLines[0].strip() != "---":
        return {}
    dictFrontmatter = {}
    for sLine in listLines[1:]:
        if sLine.strip() == "---":
            break
        if ":" in sLine:
            sKey, sValue = sLine.split(":", 1)
            dictFrontmatter[sKey.strip()] = sValue.strip()
    return dictFrontmatter


def flistExtractSymbolReferences(sContent):
    """Return Hungarian-notation identifiers referenced in prose spans."""
    sProse = REGEX_FENCED_CODE_BLOCK.sub("", sContent)
    listSymbols = []
    for sSpan in REGEX_INLINE_CODE_SPAN.findall(sProse):
        sCandidate = sSpan.split("(")[0].strip()
        if REGEX_FUNCTION_SYMBOL.match(sCandidate) or REGEX_CONSTANT_SYMBOL.match(
            sCandidate
        ):
            listSymbols.append(sCandidate)
    return listSymbols


def flistExtractTestNameReferences(sContent):
    """Return camelCase test names referenced anywhere in the skill text."""
    listNames = []
    for match in REGEX_TEST_NAME.finditer(sContent):
        sFollower = sContent[match.end() : match.end() + 1]
        if sFollower not in SET_NON_TEST_FOLLOWERS:
            listNames.append(match.group(0))
    return listNames


def fsReadCorpus(listDirectories, sGlob):
    """Concatenate the text of every file matching sGlob under the directories."""
    listChunks = []
    for sDirectory in listDirectories:
        for pathFile in sorted((REPO_ROOT / sDirectory).rglob(sGlob)):
            if "__pycache__" in pathFile.parts:
                continue
            listChunks.append(pathFile.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(listChunks)


def fmoduleLoadPathChecker():
    """Load tools/checkAgentDocsPaths.py without requiring tools/ on sys.path."""
    pathTool = REPO_ROOT / "tools" / "checkAgentDocsPaths.py"
    spec = importlib.util.spec_from_file_location("checkAgentDocsPaths", pathTool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def testEverySkillHasValidFrontmatter():
    """Each skill declares a name matching its directory and a trigger-bearing description."""
    listSkills = flistFindSkillFiles()
    assert listSkills, "No SKILL.md files found under .claude/skills/"
    for pathSkill in listSkills:
        dictFrontmatter = fdictParseFrontmatter(pathSkill)
        sDirectoryName = pathSkill.parent.name
        assert dictFrontmatter.get("name") == sDirectoryName, (
            f"{pathSkill}: frontmatter name {dictFrontmatter.get('name')!r} "
            f"must match its directory name {sDirectoryName!r}"
        )
        sDescription = dictFrontmatter.get("description", "")
        assert len(sDescription) >= 40, (
            f"{pathSkill}: description is the only text the model sees when "
            "deciding whether to invoke the skill; it must state what the "
            "skill does and when to use it (>= 40 characters)."
        )
        assert "use when" in sDescription.lower(), (
            f"{pathSkill}: description must state its trigger condition "
            "explicitly, e.g. 'Use when the task is to ...'."
        )


def testSkillPathReferencesResolve():
    """Every path a skill references still exists (delegates to the CI path checker)."""
    modulePathChecker = fmoduleLoadPathChecker()
    listBroken = []
    for pathSkill in flistFindSkillFiles():
        for sReference in modulePathChecker.flistBrokenReferences(pathSkill):
            listBroken.append(f"{pathSkill.relative_to(REPO_ROOT)}: {sReference}")
    assert not listBroken, "Broken path references in skills:\n" + "\n".join(listBroken)


def testThePathCheckerRefusesToPassOnAnEmptyScan():
    """Discovery finding nothing is a failure, never a clean run.

    The tree exclusion that keeps agent worktrees out of the scan was
    first written against ABSOLUTE path parts. Run from inside a
    worktree -- where the repository root itself lives under
    ``worktrees/`` -- it excluded every file, so the checker examined
    zero documents and exited 0. A check that reports clean when it
    means "could not look" is the failure mode the checker exists to
    catch in the documents it reads, so it must not commit it itself.

    Kills: in tools/checkAgentDocsPaths.py, delete the ``if not
    listDocs`` guard in fnMain, so an empty document set falls through
    to fnReportAndExit and reports success.
    """
    modulePathChecker = fmoduleLoadPathChecker()
    listReal = modulePathChecker.flistFindAgentsFiles(
        modulePathChecker.REPO_ROOT,
    )
    assert listReal, (
        "the checker found no agent documents in a repository that "
        "always carries a root AGENTS.md, so discovery itself is broken"
    )
    fnFindOriginal = modulePathChecker.flistFindAgentsFiles
    modulePathChecker.flistFindAgentsFiles = lambda _pathRoot: []
    try:
        iStatus = modulePathChecker.fnMain()
    finally:
        modulePathChecker.flistFindAgentsFiles = fnFindOriginal
    assert iStatus == 1, (
        "the checker examined zero documents and reported success; an "
        "empty scan must fail loudly rather than read as coverage"
    )


def testTheTreeExclusionIsRelativeToTheRepositoryRoot():
    """A root nested under an excluded name must not exclude everything.

    The root must be MOVED under an excluded name for this to assert
    anything. A first version checked the real repository root, which
    contains no excluded component, so it passed under both the
    relative and the absolute spelling and killed no mutant at all --
    a test whose premise was wrong, not a guard.

    Kills: in tools/checkAgentDocsPaths.py, change
    fbPathIsInsideExcludedTree to test ``pathCandidate.parts`` instead
    of the parts relative to REPO_ROOT.
    """
    modulePathChecker = fmoduleLoadPathChecker()
    pathNestedRoot = Path("/tmp/probe/.claude/worktrees/agent-probe")
    modulePathChecker.REPO_ROOT = pathNestedRoot
    assert not modulePathChecker.fbPathIsInsideExcludedTree(
        pathNestedRoot / "AGENTS.md"
    ), (
        "a checkout whose own path contains an excluded component "
        "excluded its own documents, so the scan examines nothing and "
        "reports clean"
    )
    assert not modulePathChecker.fbPathIsInsideExcludedTree(
        pathNestedRoot / "vaibify" / "gui" / "AGENTS.md"
    ), "a nested document under a worktree-hosted root was excluded"
    assert modulePathChecker.fbPathIsInsideExcludedTree(
        pathNestedRoot / ".claude" / "worktrees" / "inner" / "AGENTS.md"
    ), "a document inside a nested agent worktree was not excluded"


def testSkillSymbolReferencesResolve():
    """Every function or constant a skill names still occurs in the source."""
    sCorpus = fsReadCorpus(LIST_SYMBOL_CORPUS_DIRECTORIES, "*.py")
    listMissing = []
    for pathSkill in flistFindSkillFiles():
        sContent = pathSkill.read_text(encoding="utf-8")
        for sSymbol in flistExtractSymbolReferences(sContent):
            if not re.search(rf"\b{re.escape(sSymbol)}\b", sCorpus):
                listMissing.append(f"{pathSkill.relative_to(REPO_ROOT)}: {sSymbol}")
    assert not listMissing, (
        "Skills reference symbols that no longer occur anywhere in "
        f"{LIST_SYMBOL_CORPUS_DIRECTORIES}:\n" + "\n".join(listMissing)
    )


def testSkillTestNameReferencesResolve():
    """Every test a skill tells the agent to run is still defined in tests/."""
    sCorpus = fsReadCorpus(["tests"], "*.py")
    listMissing = []
    for pathSkill in flistFindSkillFiles():
        sContent = pathSkill.read_text(encoding="utf-8")
        for sTestName in flistExtractTestNameReferences(sContent):
            if not re.search(rf"\bdef\s+{re.escape(sTestName)}\s*\(", sCorpus):
                listMissing.append(f"{pathSkill.relative_to(REPO_ROOT)}: {sTestName}")
    assert not listMissing, (
        "Skills reference tests that are no longer defined in tests/:\n"
        + "\n".join(listMissing)
    )


def testSymbolExtractorIgnoresScaffoldsAndPlaceholders():
    """Negative control: the extractor must not flag scaffold code or placeholders.

    If extraction ever starts reading fenced scaffolds (`fnHandleExample`)
    or placeholders (`_fLoad<FormatName>`), every skill would fail on
    illustrative names and the suite would train developers to ignore it.
    """
    sSample = (
        "Use `fnRegisterAll` and `WORKSPACE_ROOT`, never `_fLoad<FormatName>`\n"
        "or `_fn…` helpers.\n"
        "```python\n"
        "def fnHandleScaffoldExample():\n"
        "    pass\n"
        "```\n"
    )
    assert flistExtractSymbolReferences(sSample) == ["fnRegisterAll", "WORKSPACE_ROOT"]
    sTestSample = "Run `tests/testMadeUpThing*.py` then testRealThing now"
    assert flistExtractTestNameReferences(sTestSample) == ["testRealThing"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
