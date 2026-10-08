"""No module assumes an unsuffixed generated test or standards file name.

Generated tests and standards carry the step's name
(``test_quantitative_<stepName>.py``, ``quantitative_standards_<stepName>.json``;
``testGenerator.fs*Path`` is the one derivation) because a Zenodo deposit is
flat and two steps' identically-named files overwrote each other. After that
change several features kept looking for the old fixed names. None failed
loudly: the mutation-testing check answered "not applicable" for every
step-named project, and the standards CLI wrote a second, unsuffixed standards
file beside the real one. Their tests stayed green because every fixture used
the same fixed names the code did.

The class is a string literal naming a fixed generated file, so the guard
reads the literals: every non-docstring string constant under ``vaibify/``. A
path must come from the generator's helpers instead. The seed below is frozen
and may only shrink.
"""

import ast
import pathlib
import re

import pytest

PATH_PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "vaibify"

_REGEX_UNSUFFIXED_NAME = re.compile(
    r"\b(test_(integrity|qualitative|quantitative)\.py"
    r"|(integrity|qualitative|quantitative)_standards\.json)"
)

# file -> number of literals allowed. The keys of the template-hash lookup
# are category STEMS: _fsExpectedHashForTestFilename matches a step-named
# file by the stem plus an underscore, and the exact-name branch is a
# harmless fallback. That lookup is covered by
# testAStepSuffixedGeneratedTestIsStillComparedToItsTemplate.
DICT_SEEDED_LITERALS = {
    "vaibify/gui/routes/pipelineRoutes.py": 3,
}


def _fsetDocstringNodeIds(treeModule):
    setIds = set()
    for nodeScope in ast.walk(treeModule):
        if not isinstance(nodeScope, (
            ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
        )):
            continue
        listBody = nodeScope.body
        if listBody and isinstance(listBody[0], ast.Expr) and isinstance(
            listBody[0].value, ast.Constant
        ):
            setIds.add(id(listBody[0].value))
    return setIds


def _flistUnsuffixedLiteralsIn(pathFile):
    treeModule = ast.parse(pathFile.read_text(encoding="utf-8"))
    setDocstrings = _fsetDocstringNodeIds(treeModule)
    return [
        (nodeAny.lineno, nodeAny.value)
        for nodeAny in ast.walk(treeModule)
        if isinstance(nodeAny, ast.Constant)
        and isinstance(nodeAny.value, str)
        and id(nodeAny) not in setDocstrings
        and _REGEX_UNSUFFIXED_NAME.search(nodeAny.value)
    ]


def _fdictUnsuffixedLiteralsByFile():
    dictFound = {}
    for pathFile in sorted(PATH_PACKAGE.rglob("*.py")):
        listFound = _flistUnsuffixedLiteralsIn(pathFile)
        if listFound:
            dictFound[pathFile.relative_to(PATH_PACKAGE.parent).as_posix()] = (
                listFound)
    return dictFound


@pytest.mark.falsification
def testNoModuleHardCodesAnUnsuffixedGeneratedFileName():
    """Kills: a lookup or command naming ``test_quantitative.py`` outright.

    The mutation puts back the literal the mutation-testing command used
    to carry, which is the defect itself.
    """
    dictFound = _fdictUnsuffixedLiteralsByFile()
    listOffenders = []
    for sFile, listLiterals in dictFound.items():
        iAllowed = DICT_SEEDED_LITERALS.get(sFile, 0)
        if len(listLiterals) > iAllowed:
            listOffenders.append(
                sFile + ": " + ", ".join(
                    f"line {iLine} {sValue[:60]!r}"
                    for iLine, sValue in listLiterals))
    assert not listOffenders, (
        "A fixed generated file name is hard-coded. Derive the path with "
        "testGenerator.fsQuantitativeTestPath / fsQuantitativeStandardsPath "
        "(or the integrity and qualitative siblings):\n  "
        + "\n  ".join(listOffenders))


def testTheSeedIsNotLargerThanTheCode():
    """A seed entry whose literals are gone must be removed, not left idle."""
    dictFound = _fdictUnsuffixedLiteralsByFile()
    for sFile, iAllowed in DICT_SEEDED_LITERALS.items():
        assert len(dictFound.get(sFile, [])) == iAllowed, (
            f"{sFile} holds {len(dictFound.get(sFile, []))} such literals "
            f"but the seed allows {iAllowed}; lower or remove the entry")
