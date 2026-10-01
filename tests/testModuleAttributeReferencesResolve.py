"""Every vaibify module attribute a module reads or imports exists.

A bulk rename changes a definition and its direct imports, but a call
written as ``module.fnOldName(...)`` is invisible to import checks and
to pyflakes, and when the call sits inside a broad ``except`` the
``AttributeError`` is swallowed at runtime. That is how the supervised
mode watchdog stopped writing its permanent flags after
``attributionLog.fnAppendFlag`` became ``fdictAppendFlag``: the suite
stayed green and every tick logged "Supervision watchdog failed".

The scan checks four forms, and only these:

* ``alias.name`` where ``alias`` is bound by ``import x.y as alias`` or
  ``from x import y`` (``y`` a module; relative or absolute; module level
  or function local);
* ``vaibify.a.b.name`` after an unaliased ``import vaibify.a.b``;
* ``from x import name`` where ``x`` is a vaibify module and ``name`` is
  neither one of its attributes nor one of its submodules;
* an alias that a parameter, assignment, loop target, ``with`` target,
  ``except`` name, function or class definition rebinds in a nearer scope
  is NOT the module, and reads through it are skipped.

``hasattr`` is used rather than ``dir`` so a module's lazy
``__getattr__`` re-exports (pipelineServer's route re-exports) count as
defined. An ``ImportError`` while importing a vaibify module FAILS the
test: a module the scan cannot import is a module it cannot check.
Attribute reads on ``self``, on call results and through ``getattr`` are
not checked.

``DICT_KNOWN_UNRESOLVED_REFERENCES`` seeds the references that are
broken today, each with the reason it is not yet fixed. The seed is
EXACT: a new unresolved reference fails, and so does a seeded one that
starts resolving -- delete its entry in the same commit as the fix.
"""

import ast
import functools
import importlib
import pathlib

import pytest


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"

DICT_KNOWN_UNRESOLVED_REFERENCES = {
    "vaibify/gui/routes/syncRoutes.py::syncDispatcher.ftResultDownloadDataset": (
        "Known absent: the dataset-download route is deliberately left "
        "unmigrated until the dispatcher exists (see the comment in "
        "syncRoutes._fnRegisterDatasetDownload)."
    ),
}



@functools.lru_cache(maxsize=None)
def fmoduleImportVaibify(sDottedName):
    """Import a vaibify module; an ImportError propagates and fails the scan."""
    return importlib.import_module(sDottedName)


def fsModuleNameFromPath(pathModule):
    """Return the dotted module name of a file under the repository."""
    listParts = list(pathModule.relative_to(PATH_REPOSITORY).with_suffix("").parts)
    if listParts[-1] == "__init__":
        listParts = listParts[:-1]
    return ".".join(listParts)


def fsResolveImportBase(sCurrentModule, bIsPackage, nodeImport):
    """Return the absolute module an ``ImportFrom`` node imports from."""
    if not nodeImport.level:
        return nodeImport.module or ""
    listParts = sCurrentModule.split(".")
    if not bIsPackage:
        listParts = listParts[:-1]
    if nodeImport.level > 1:
        listParts = listParts[: len(listParts) - (nodeImport.level - 1)]
    if nodeImport.module:
        listParts.append(nodeImport.module)
    return ".".join(listParts)


def fbNameIsVaibifyModule(sDottedName):
    """Return True when a dotted name is a module file or package."""
    pathCandidate = PATH_REPOSITORY.joinpath(*sDottedName.split("."))
    return (
        pathCandidate.with_suffix(".py").is_file()
        or (pathCandidate / "__init__.py").is_file()
    )


def fiterNodesOfThisScope(listStatements):
    """Yield the nodes a scope owns, without entering nested scopes."""
    listPending = list(listStatements)
    while listPending:
        nodeCurrent = listPending.pop()
        yield nodeCurrent
        if not isinstance(nodeCurrent, (
            ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
        )):
            listPending.extend(ast.iter_child_nodes(nodeCurrent))


def flistArgumentNames(nodeArguments):
    """Return every parameter name a function signature binds."""
    listArguments = (
        nodeArguments.posonlyargs + nodeArguments.args
        + nodeArguments.kwonlyargs
    )
    listNames = [nodeArgument.arg for nodeArgument in listArguments]
    for nodeVariadic in (nodeArguments.vararg, nodeArguments.kwarg):
        if nodeVariadic is not None:
            listNames.append(nodeVariadic.arg)
    return listNames


def fdictBindingsOfNode(nodeCurrent, sCurrentModule, bIsPackage):
    """Return {name: dotted vaibify module, or None} one node binds."""
    if isinstance(nodeCurrent, ast.Import):
        return {
            (aliasImported.asname or aliasImported.name.split(".")[0]): (
                (aliasImported.name if aliasImported.asname else "vaibify")
                if aliasImported.name.split(".")[0] == "vaibify" else None
            )
            for aliasImported in nodeCurrent.names
        }
    if isinstance(nodeCurrent, ast.ImportFrom):
        sBase = fsResolveImportBase(sCurrentModule, bIsPackage, nodeCurrent)
        dictBound = {}
        for aliasImported in nodeCurrent.names:
            sCandidate = f"{sBase}.{aliasImported.name}"
            bIsModule = sBase.startswith("vaibify") and fbNameIsVaibifyModule(sCandidate)
            dictBound[aliasImported.asname or aliasImported.name] = (
                sCandidate if bIsModule else None
            )
        return dictBound
    if isinstance(nodeCurrent, ast.Name) and not isinstance(nodeCurrent.ctx, ast.Load):
        return {nodeCurrent.id: None}
    if isinstance(nodeCurrent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {nodeCurrent.name: None}
    if isinstance(nodeCurrent, ast.ExceptHandler) and nodeCurrent.name:
        return {nodeCurrent.name: None}
    return {}


def fdictBindingsOfScope(listStatements, listParameterNames, sCurrentModule, bIsPackage):
    """Return {name: module or None} for a scope; any rebinding shadows."""
    dictBindings = {sName: None for sName in listParameterNames}
    for nodeCurrent in fiterNodesOfThisScope(listStatements):
        for sName, sModule in fdictBindingsOfNode(
            nodeCurrent, sCurrentModule, bIsPackage,
        ).items():
            if sName in dictBindings and dictBindings[sName] is None:
                continue
            dictBindings[sName] = sModule if sName not in dictBindings else (
                sModule if dictBindings[sName] == sModule else None
            )
    return dictBindings


def ftSplitAttributeChain(nodeAttribute):
    """Return ``(base name, [attr, ...])`` for ``base.a.b``, else ``("", [])``."""
    listAttributes = []
    nodeCurrent = nodeAttribute
    while isinstance(nodeCurrent, ast.Attribute):
        listAttributes.append(nodeCurrent.attr)
        nodeCurrent = nodeCurrent.value
    if isinstance(nodeCurrent, ast.Name):
        return nodeCurrent.id, list(reversed(listAttributes))
    return "", []


def fiFirstMissingAttributeIndex(sModuleName, listAttributes):
    """Return the index of the first attribute that names nothing, or -1.

    A step that names a submodule continues into it; the first step
    that is an ordinary attribute ends the walk, because what lies
    beyond it is not a module.
    """
    for iIndex, sAttribute in enumerate(listAttributes):
        sSubmodule = f"{sModuleName}.{sAttribute}"
        if fbNameIsVaibifyModule(sSubmodule):
            sModuleName = sSubmodule
            continue
        if not hasattr(fmoduleImportVaibify(sModuleName), sAttribute):
            return iIndex
        return -1
    return -1


class ScopeAwareReferenceFinder(ast.NodeVisitor):
    """Collect unresolved vaibify module references, honoring shadowing."""

    def __init__(self, sRelativePath, sCurrentModule, bIsPackage):
        self.sRelativePath = sRelativePath
        self.sCurrentModule = sCurrentModule
        self.bIsPackage = bIsPackage
        self.listScopes = []
        self.setUnresolved = set()

    def fnPushScope(self, listStatements, listParameterNames=(), bIsClass=False):
        dictBindings = fdictBindingsOfScope(
            listStatements, listParameterNames,
            self.sCurrentModule, self.bIsPackage,
        )
        self.listScopes.append((dictBindings, bIsClass))

    def fsLookUpModule(self, sName):
        """Return the vaibify module ``sName`` is bound to here, else ''."""
        for iDepth, (dictBindings, bIsClass) in enumerate(reversed(self.listScopes)):
            if bIsClass and iDepth > 0:
                continue
            if sName in dictBindings:
                return dictBindings[sName] or ""
        return ""

    def visit_Module(self, nodeModule):
        self.fnPushScope(nodeModule.body)
        self.generic_visit(nodeModule)
        self.listScopes.pop()

    def visit_FunctionDef(self, nodeFunction):
        for nodeOuter in nodeFunction.decorator_list + nodeFunction.args.defaults:
            self.visit(nodeOuter)
        self.fnPushScope(nodeFunction.body, flistArgumentNames(nodeFunction.args))
        for nodeStatement in nodeFunction.body:
            self.visit(nodeStatement)
        self.listScopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, nodeLambda):
        self.fnPushScope([], flistArgumentNames(nodeLambda.args))
        self.visit(nodeLambda.body)
        self.listScopes.pop()

    def visit_ClassDef(self, nodeClass):
        for nodeOuter in nodeClass.decorator_list + nodeClass.bases:
            self.visit(nodeOuter)
        self.fnPushScope(nodeClass.body, bIsClass=True)
        for nodeStatement in nodeClass.body:
            self.visit(nodeStatement)
        self.listScopes.pop()

    def fnVisitComprehension(self, nodeComprehension):
        listTargets = [
            nodeName for nodeGenerator in nodeComprehension.generators
            for nodeName in ast.walk(nodeGenerator.target)
        ]
        self.listScopes.append(({
            nodeName.id: None for nodeName in listTargets
            if isinstance(nodeName, ast.Name)
        }, False))
        self.generic_visit(nodeComprehension)
        self.listScopes.pop()

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = (
        fnVisitComprehension
    )

    def visit_ImportFrom(self, nodeImport):
        sBase = fsResolveImportBase(self.sCurrentModule, self.bIsPackage, nodeImport)
        if not (sBase.startswith("vaibify") and fbNameIsVaibifyModule(sBase)):
            return
        moduleBase = fmoduleImportVaibify(sBase)
        for aliasImported in nodeImport.names:
            if aliasImported.name == "*":
                continue
            if hasattr(moduleBase, aliasImported.name):
                continue
            if fbNameIsVaibifyModule(f"{sBase}.{aliasImported.name}"):
                continue
            self.setUnresolved.add(
                f"{self.sRelativePath}::from {sBase} import {aliasImported.name}"
            )

    def visit_Attribute(self, nodeAttribute):
        sBaseName, listAttributes = ftSplitAttributeChain(nodeAttribute)
        if not sBaseName:
            self.generic_visit(nodeAttribute)
            return
        sModuleName = self.fsLookUpModule(sBaseName)
        if not sModuleName or not isinstance(nodeAttribute.ctx, ast.Load):
            return
        iMissing = fiFirstMissingAttributeIndex(sModuleName, listAttributes)
        if iMissing >= 0:
            sReference = ".".join([sBaseName] + listAttributes[: iMissing + 1])
            self.setUnresolved.add(f"{self.sRelativePath}::{sReference}")


def flistUnresolvedReferencesInSource(sSource, sRelativePath, sCurrentModule, bIsPackage):
    """Return "path::reference" for every unresolved module read."""
    finderReferences = ScopeAwareReferenceFinder(
        sRelativePath, sCurrentModule, bIsPackage,
    )
    finderReferences.visit(ast.parse(sSource))
    return sorted(finderReferences.setUnresolved)


def flistUnresolvedReferencesInPackage():
    """Scan every module under vaibify/ and return unresolved reads."""
    listUnresolved = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        sRelativePath = str(pathModule.relative_to(PATH_REPOSITORY))
        listUnresolved.extend(flistUnresolvedReferencesInSource(
            pathModule.read_text(encoding="utf-8"),
            sRelativePath,
            fsModuleNameFromPath(pathModule),
            pathModule.name == "__init__.py",
        ))
    return listUnresolved


def testEveryModuleAttributeReferenceResolvesOrIsSeeded():
    """No unresolved reference beyond the exact seed, and no stale seed."""
    setUnresolved = set(flistUnresolvedReferencesInPackage())
    setSeeded = set(DICT_KNOWN_UNRESOLVED_REFERENCES)
    listNew = sorted(setUnresolved - setSeeded)
    listStale = sorted(setSeeded - setUnresolved)
    assert not listNew, (
        "These module.attribute reads name nothing on the imported "
        "module (a rename missed a call site?):\n  " + "\n  ".join(listNew)
    )
    assert not listStale, (
        "These seeded references now resolve; delete them from "
        "DICT_KNOWN_UNRESOLVED_REFERENCES:\n  " + "\n  ".join(listStale)
    )


@pytest.mark.parametrize("sSource, listExpected", [
    pytest.param(
        "from vaibify.gui import attributionLog\n"
        "def fnRun():\n"
        "    attributionLog.fnNameThatDoesNotExist()\n",
        ["probe.py::attributionLog.fnNameThatDoesNotExist"],
        id="missingAttributeThroughFromImport",
    ),
    pytest.param(
        "def fnRun():\n"
        "    from .. import attributionLog as logAlias\n"
        "    logAlias.fdictAppendFlag\n"
        "    logAlias.fnAlsoMissing\n",
        ["probe.py::logAlias.fnAlsoMissing"],
        id="functionLocalRelativeAlias",
    ),
    pytest.param(
        "import vaibify.gui.attributionLog as logAlias\n"
        "logAlias.fdictAppendFlag\n",
        [],
        id="resolvingAttributeIsQuiet",
    ),
    pytest.param(
        "from vaibify.gui import attributionLog\n"
        "def fnRun(attributionLog):\n"
        "    attributionLog.fnNameThatDoesNotExist()\n",
        [],
        id="parameterShadowsTheAlias",
    ),
    pytest.param(
        "from vaibify.gui import attributionLog\n"
        "def fnRun():\n"
        "    attributionLog = object()\n"
        "    attributionLog.fnNameThatDoesNotExist\n",
        [],
        id="localAssignmentShadowsTheAlias",
    ),
    pytest.param(
        "from vaibify.gui import attributionLog\n"
        "def fnRun(listItems):\n"
        "    return [attributionLog.fnMissing for attributionLog in listItems]\n",
        [],
        id="comprehensionTargetShadowsTheAlias",
    ),
    pytest.param(
        "from vaibify.gui import attributionLog\n"
        "def fnRun():\n"
        "    return attributionLog.fnMissing\n"
        "def fnOther(attributionLog):\n"
        "    return attributionLog.fnMissing\n",
        ["probe.py::attributionLog.fnMissing"],
        id="shadowingStaysInsideItsOwnFunction",
    ),
    pytest.param(
        "import vaibify.gui.attributionLog\n"
        "vaibify.gui.attributionLog.fnMissing()\n"
        "vaibify.gui.attributionLog.fdictAppendFlag\n",
        ["probe.py::vaibify.gui.attributionLog.fnMissing"],
        id="unaliasedDottedImport",
    ),
    pytest.param(
        "def fnRun():\n"
        "    from vaibify.gui.attributionLog import fnMissing, fdictAppendFlag\n",
        ["probe.py::from vaibify.gui.attributionLog import fnMissing"],
        id="functionLocalFromImportOfAMissingName",
    ),
    pytest.param(
        "from vaibify.gui import attributionLog, routes\n",
        [],
        id="fromImportOfASubmoduleIsFine",
    ),
])
def testTheScannerReportsAMissingAttributeAndOnlyThat(sSource, listExpected):
    """The scan catches each import form, and skips a shadowed alias."""
    assert flistUnresolvedReferencesInSource(
        sSource, "probe.py", "vaibify.gui.routes.probe", False,
    ) == listExpected


def testAnImportErrorWhileImportingAModuleFailsTheScan(monkeypatch):
    """A module the scan cannot import is a module it cannot check."""
    def fmoduleThatCannotBeImported(sDottedName):
        raise ImportError(f"cannot import {sDottedName}")

    fmoduleImportVaibify.cache_clear()
    monkeypatch.setattr(importlib, "import_module", fmoduleThatCannotBeImported)
    with pytest.raises(ImportError):
        flistUnresolvedReferencesInSource(
            "from vaibify.gui import attributionLog\n"
            "attributionLog.fdictAppendFlag\n",
            "probe.py", "vaibify.gui.routes.probe", False,
        )
    monkeypatch.undo()
    fmoduleImportVaibify.cache_clear()
