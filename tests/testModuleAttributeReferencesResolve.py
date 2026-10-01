"""Every ``module.attribute`` reference to a vaibify module resolves.

A bulk rename changes a definition and its direct imports, but a call
written as ``module.fnOldName(...)`` is invisible to import checks and
to pyflakes, and when the call sits inside a broad ``except`` the
``AttributeError`` is swallowed at runtime. That is how the supervised
mode watchdog stopped writing its permanent flags after
``attributionLog.fnAppendFlag`` became ``fdictAppendFlag``: the suite
stayed green and every tick logged "Supervision watchdog failed".

This scan binds each alias of a vaibify module (``import x.y as z``,
``from x import y`` where ``y`` is a module, relative or absolute,
module-level or function-local) and asserts that every attribute read
through that alias exists on the imported module. ``hasattr`` is used
rather than ``dir`` so a module's lazy ``__getattr__`` re-exports
(pipelineServer's route re-exports) count as defined.

``DICT_KNOWN_UNRESOLVED_REFERENCES`` seeds the references that are
broken today, each with the reason it is not yet fixed. The seed is
EXACT: a new unresolved reference fails, and so does a seeded one that
starts resolving -- delete its entry in the same commit as the fix.
"""

import ast
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


def fdictModuleAliasesInTree(treeModule, sCurrentModule, bIsPackage):
    """Return {alias: dotted vaibify module} for every module import."""
    dictAliases = {}
    for nodeImport in ast.walk(treeModule):
        if isinstance(nodeImport, ast.Import):
            for aliasImported in nodeImport.names:
                if aliasImported.name.startswith("vaibify") and aliasImported.asname:
                    dictAliases[aliasImported.asname] = aliasImported.name
        elif isinstance(nodeImport, ast.ImportFrom):
            sBase = fsResolveImportBase(sCurrentModule, bIsPackage, nodeImport)
            if not sBase.startswith("vaibify"):
                continue
            for aliasImported in nodeImport.names:
                sCandidate = f"{sBase}.{aliasImported.name}"
                if fbNameIsVaibifyModule(sCandidate):
                    dictAliases[aliasImported.asname or aliasImported.name] = sCandidate
    return dictAliases


def flistUnresolvedReferencesInSource(sSource, sRelativePath, sCurrentModule, bIsPackage):
    """Return "path::alias.attribute" for every unresolved module read."""
    treeModule = ast.parse(sSource)
    dictAliases = fdictModuleAliasesInTree(treeModule, sCurrentModule, bIsPackage)
    setUnresolved = set()
    for nodeAttribute in ast.walk(treeModule):
        if not (
            isinstance(nodeAttribute, ast.Attribute)
            and isinstance(nodeAttribute.value, ast.Name)
            and nodeAttribute.value.id in dictAliases
            and isinstance(nodeAttribute.ctx, ast.Load)
        ):
            continue
        try:
            moduleImported = importlib.import_module(dictAliases[nodeAttribute.value.id])
        except ImportError:
            continue
        if not hasattr(moduleImported, nodeAttribute.attr):
            setUnresolved.add(
                f"{sRelativePath}::{nodeAttribute.value.id}.{nodeAttribute.attr}"
            )
    return sorted(setUnresolved)


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
    (
        "from vaibify.gui import attributionLog\n"
        "def fnRun():\n"
        "    attributionLog.fnNameThatDoesNotExist()\n",
        ["probe.py::attributionLog.fnNameThatDoesNotExist"],
    ),
    (
        "def fnRun():\n"
        "    from .. import attributionLog as logAlias\n"
        "    logAlias.fdictAppendFlag\n"
        "    logAlias.fnAlsoMissing\n",
        ["probe.py::logAlias.fnAlsoMissing"],
    ),
    (
        "import vaibify.gui.attributionLog as logAlias\n"
        "logAlias.fdictAppendFlag\n",
        [],
    ),
])
def testTheScannerReportsAMissingAttributeAndOnlyThat(sSource, listExpected):
    """The scan catches a missing attribute through each import form."""
    assert flistUnresolvedReferencesInSource(
        sSource, "probe.py", "vaibify.gui.routes.probe", False,
    ) == listExpected
