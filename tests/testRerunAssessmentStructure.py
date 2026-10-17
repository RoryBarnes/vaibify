"""The assessor's two leaf modules can be concatenated into one runnable file.

``rerunAssessment.fsRenderRerunAssessmentScript`` concatenates
``rerunProcedure`` and a frozen policy module verbatim, so each property
here is one way the rendered file breaks or drifts: an import a reader's
container does not have, a name the second module silently rebinds, a
``from __future__`` that is legal only at the top of a file, a second
entry point, syntax the author's interpreter cannot parse, or a policy
whose bytes moved after it was frozen. The gui-only leaf test
(``testLeafModuleHasNoIntraPackageImports``) does not reach
``vaibify/reproducibility/``, which is why these exist.
"""

import ast
import hashlib
import os
import sys

import pytest

from vaibify.reproducibility import (
    comparisonPolicyV1,
    rerunAssessment,
    rerunProcedure,
)
from vaibify.reproducibility.manifestWriter import flistParseManifestText


T_LEAF_MODULES = (rerunProcedure, comparisonPolicyV1)


def _fsReadSource(moduleSource):
    """Return a module's source text."""
    with open(moduleSource.__file__, encoding="utf-8") as fileSource:
        return fileSource.read()


# Imported INSIDE a function only, so a container without it still runs
# every other sub-command: Pillow inside the image check, NumPy inside
# the probe.
SET_LAZY_IMPORT_EXCEPTIONS = {"PIL", "numpy"}


def _flistImportedRoots(sSource, bTopLevelOnly):
    """Return the top-level package of every import in the source."""
    listRoots = []
    treeModule = ast.parse(sSource)
    listNodes = treeModule.body if bTopLevelOnly else ast.walk(treeModule)
    for nodeImport in listNodes:
        if isinstance(nodeImport, ast.Import):
            listRoots.extend(nodeAlias.name.split(".")[0]
                             for nodeAlias in nodeImport.names)
        elif isinstance(nodeImport, ast.ImportFrom):
            listRoots.append((nodeImport.module or "").split(".")[0])
    return listRoots


def _fsetDefinedTopLevelNames(sSource):
    """Return names a module DEFINES at top level (not imports, not dunders)."""
    setNames = set()
    for nodeStatement in ast.parse(sSource).body:
        if isinstance(nodeStatement, (ast.FunctionDef, ast.ClassDef)):
            setNames.add(nodeStatement.name)
        elif isinstance(nodeStatement, (ast.Assign, ast.AnnAssign)):
            listTargets = (nodeStatement.targets
                           if isinstance(nodeStatement, ast.Assign)
                           else [nodeStatement.target])
            for nodeTarget in listTargets:
                if isinstance(nodeTarget, ast.Name):
                    setNames.add(nodeTarget.id)
    return {sName for sName in setNames if not sName.startswith("__")}


def _fdictWorkflowWithOneStep():
    """Return a generic one-step workflow."""
    return {"listSteps": [{
        "sName": "Step one", "sDirectory": "StepOne", "bRunEnabled": True,
        "saDataCommands": ["python3 make.py"],
        "saOutputDataFiles": ["values.json"], "saPlotFiles": ["figure.png"],
        "saPlotCommands": ["python3 plot.py"],
    }]}


def _fbIsStandardLibrary(sRoot):
    """Return True iff a top-level module name belongs to the standard library.

    ``sys.stdlib_module_names`` arrived in Python 3.10. On 3.9 the
    answer is read from where the module lives: built in, or inside the
    interpreter's own library directory and outside site-packages.
    """
    if hasattr(sys, "stdlib_module_names"):
        return sRoot in sys.stdlib_module_names
    import importlib.util
    import sysconfig
    if sRoot in sys.builtin_module_names:
        return True
    specModule = importlib.util.find_spec(sRoot)
    if specModule is None or not specModule.origin:
        return False
    sOrigin = os.path.realpath(specModule.origin)
    sLibrary = os.path.realpath(sysconfig.get_paths()["stdlib"])
    return (sOrigin.startswith(sLibrary + os.sep)
            and "site-packages" not in sOrigin)


def _fnAssertOnlyStandardLibraryImports(moduleLeaf):
    """Assert a leaf imports the standard library, plus its lazy exceptions."""
    sSource = _fsReadSource(moduleLeaf)
    listForeignAtTop = [
        sRoot for sRoot in _flistImportedRoots(sSource, True)
        if not _fbIsStandardLibrary(sRoot)
    ]
    listForeignAnywhere = [
        sRoot for sRoot in _flistImportedRoots(sSource, False)
        if not _fbIsStandardLibrary(sRoot)
        and sRoot not in SET_LAZY_IMPORT_EXCEPTIONS
    ]
    assert listForeignAtTop == []
    assert listForeignAnywhere == []


@pytest.mark.falsification
def testTheProcedureImportsOnlyTheStandardLibrary():
    """A reader's container has the image's Python, never vaibify.

    Kills: importing anything from ``vaibify`` into the procedure, which
    makes the rendered file fail on its first line in every reader's
    container.
    """
    _fnAssertOnlyStandardLibraryImports(rerunProcedure)


@pytest.mark.falsification
def testThePolicyImportsOnlyTheStandardLibrary():
    """The frozen policy is concatenated into the same file, on the same terms.

    Kills: importing anything from ``vaibify`` into the policy module.
    """
    _fnAssertOnlyStandardLibraryImports(comparisonPolicyV1)


@pytest.mark.falsification
def testTheLeafModulesDefineNoNameInCommon():
    """The second module would silently rebind the first's name.

    ``__all__`` and the other dunders are rebound harmlessly; imports
    of the same standard module bind the same object. Every other
    top-level definition must be unique across the two.

    Kills: the policy defining a constant the procedure also defines.
    """
    setShared = (
        _fsetDefinedTopLevelNames(_fsReadSource(rerunProcedure))
        & _fsetDefinedTopLevelNames(_fsReadSource(comparisonPolicyV1)))
    assert setShared == set()


@pytest.mark.falsification
@pytest.mark.parametrize("moduleLeaf", T_LEAF_MODULES,
                         ids=lambda moduleLeaf: moduleLeaf.__name__)
def testTheLeafModulesHaveNoFutureImportAndNoEntryPoint(moduleLeaf):
    """A future import must open a file; the renderer appends the one entry point.

    Kills: an ``if __name__ == "__main__"`` block in the policy module,
    which would run before the plan is defined.
    """
    treeModule = ast.parse(_fsReadSource(moduleLeaf))
    for nodeStatement in treeModule.body:
        assert not (isinstance(nodeStatement, ast.ImportFrom)
                    and nodeStatement.module == "__future__")
        assert not (isinstance(nodeStatement, ast.If)
                    and "__name__" in ast.unparse(nodeStatement.test))


@pytest.mark.falsification
@pytest.mark.parametrize("moduleLeaf", T_LEAF_MODULES,
                         ids=lambda moduleLeaf: moduleLeaf.__name__)
def testTheLeafModulesParseAsPythonThreeEight(moduleLeaf):
    """The interpreter that runs the file is the one in the author's image.

    Parsed at Python 3.8's grammar, and inspected for the constructs a
    newer interpreter's parser accepts at that version without saying so:
    on Python 3.9, nothing 3.9 added is refused by the 3.8 parse, so the
    parse alone guards nothing there.

    Kills: a generic decorator expression (Python 3.9) in the procedure.
    """
    sSource = _fsReadSource(moduleLeaf)
    treeModule = ast.parse(sSource, feature_version=(3, 8))
    assert _flistConstructsNewerThanPython38(treeModule) == []


def _fbIsDottedName(nodeExpression):
    """Return True iff the expression is ``a`` or ``a.b.c``."""
    while isinstance(nodeExpression, ast.Attribute):
        nodeExpression = nodeExpression.value
    return isinstance(nodeExpression, ast.Name)


def _flistConstructsNewerThanPython38(treeModule):
    """Return a description of every construct Python 3.8 cannot run."""
    listFound = []
    for nodeAny in ast.walk(treeModule):
        sKind = type(nodeAny).__name__
        if sKind in ("Match", "TryStar", "TypeAlias"):
            listFound.append(f"{sKind} at line {nodeAny.lineno}")
        if getattr(nodeAny, "type_params", None):
            listFound.append(f"type parameters at line {nodeAny.lineno}")
        for nodeDecorator in getattr(nodeAny, "decorator_list", []):
            nodeCallee = (nodeDecorator.func if isinstance(
                nodeDecorator, ast.Call) else nodeDecorator)
            if not _fbIsDottedName(nodeCallee):
                listFound.append(
                    f"a generic decorator at line {nodeDecorator.lineno}")
    return listFound


@pytest.mark.falsification
def testThePolicyModuleIsFrozenAtItsPinnedBytes():
    """``comparison-v1`` means the same thing in every vaibify that ships it.

    Kills: any edit to the policy module, such as moving the image
    threshold, without a new policy ID.
    """
    with open(comparisonPolicyV1.__file__, "rb") as fileSource:
        sDigest = hashlib.sha256(fileSource.read()).hexdigest()
    assert sDigest == rerunAssessment.S_COMPARISON_POLICY_V1_SHA256


@pytest.mark.falsification
def testRenderingTwiceGivesIdenticalBytes():
    """Both lanes must hold byte-identical text for the same policy and workflow.

    Kills: rendering anything that varies between calls into the file.
    """
    dictWorkflow = _fdictWorkflowWithOneStep()
    sFirst = rerunAssessment.fsRenderRerunAssessmentScript(
        dictWorkflow, rerunAssessment.S_CURRENT_COMPARISON_POLICY)
    sSecond = rerunAssessment.fsRenderRerunAssessmentScript(
        dictWorkflow, rerunAssessment.S_CURRENT_COMPARISON_POLICY)
    assert sFirst == sSecond
    listLines = sFirst.splitlines()
    assert listLines[1] == "# vaibify-comparison-policy: comparison-v1"
    ast.parse(sFirst, feature_version=(3, 8))


@pytest.mark.falsification
def testTheProcedureParsesTheManifestAsTheWriterDoes():
    """The in-container parser is a deliberate copy; it must give the same answer.

    Kills: the copy unescaping ``\\n`` as a literal ``n``.
    """
    sText = (
        "# SHA-256 manifest of workflow artefacts\n"
        "\n"
        + "a" * 64 + "  plain/path.txt\n"
        "\\" + "b" * 64 + "  odd\\nname\\\\with.txt\n"
        + "c" * 64 + "  spaced  name.txt\n"
    )
    assert (rerunProcedure.flistParseManifestEntries(sText)
            == flistParseManifestText(sText))


@pytest.mark.falsification
def testAnUnknownPolicyIsRefusedByTheRenderer():
    """An ID the registry lacks names vaibify's upgrade remedy, never a crash.

    Kills: returning nothing for an unknown policy instead of refusing.
    """
    with pytest.raises(rerunAssessment.ComparisonPolicyUnknownError) as \
            errorRefused:
        rerunAssessment.fsRenderRerunAssessmentScript(
            _fdictWorkflowWithOneStep(), "comparison-v9")
    assert "upgrade vaibify" in str(errorRefused.value)
    assert "comparison-v9" in str(errorRefused.value)


@pytest.mark.falsification
def testAMalformedPolicyLineIsRefusedNotReadAsAbsent():
    """A garbled record is not "none recorded".

    Kills: dropping the strict pattern's refusal.
    """
    sText = ("# Generated by vaibify; do not edit.\n"
             "# vaibify-comparison-policy: ../../etc/passwd\n")
    with pytest.raises(ValueError):
        rerunAssessment.fsReadRecordedComparisonPolicy(sText)


@pytest.mark.falsification
def testAFileWithoutAPolicyLineRecordsNone():
    """No header means the current policy is used, reported as none recorded.

    Kills: treating a headerless file as a malformed one.
    """
    assert rerunAssessment.fsReadRecordedComparisonPolicy(
        "#!/usr/bin/env python3\nprint('hello')\n") is None
    assert rerunAssessment.fsReadRecordedComparisonPolicy(
        rerunAssessment.fsRenderRerunAssessmentScript(
            _fdictWorkflowWithOneStep(), "comparison-v1")) == "comparison-v1"


def testTheInvocationIdBuilderRefusesMalformedParts():
    """An identity is built only from a real label, section and position."""
    assert rerunProcedure.fsBuildInvocationId(
        "A03", "quantitative", 1) == "A03.quantitative.1"
    for tArguments in (("a03", "data", 1), ("A03", "tests", 1),
                       ("A03", "data", 0), ("A03", "data", True),
                       ("A3", "data", 1)):
        with pytest.raises(ValueError):
            rerunProcedure.fsBuildInvocationId(*tArguments)


def testThePlanNumbersOnlyNonEmptyCommands():
    """Empty commands are skipped, as ``reproduce.sh`` skips them."""
    dictWorkflow = _fdictWorkflowWithOneStep()
    dictWorkflow["listSteps"][0]["saDataCommands"] = [
        "", "python3 a.py", "   ", "python3 b.py"]
    dictPlan = rerunAssessment.fdictBuildAssessmentPlan(dictWorkflow)
    assert dictPlan["listSteps"][0]["listDataInvocations"] == [
        "A01.data.1", "A01.data.2"]
    assert dictPlan["listSteps"][0]["listOutputs"] == [
        {"sPath": "StepOne/values.json", "sSection": "data", "sKind": "data"},
        {"sPath": "StepOne/figure.png", "sSection": "plot",
         "sKind": "raster"},
    ]
