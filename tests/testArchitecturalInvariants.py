"""Architectural invariants for the vaibify package encoded as pytest tests."""

import ast
import importlib
import re
from pathlib import Path


__all__ = [
    "testLeafModuleHasNoIntraPackageImports",
    "testEveryRouteModuleExportsRegisterAll",
    "testAllRouteModulesRegisteredInInit",
    "testAllPackageModulesDefineDunderAll",
    "testWorkflowManagerUsesPosixPath",
    "testDirectorUsesOsPath",
    "testNoScienceSpecificIdentifiersInSource",
    "testRouteModulesDoNotImportSiblings",
    "testNoRawFetchInFeatureModules",
    "testNoRawOnMessageInFeatureModules",
    "testOrchestratorReExportsAreComplete",
    "testEveryJsFileIsRecognizedAsIIFE",
    "testDockerfileDisablesAptSandboxBeforeFirstUpdate",
    "testGitRoutesAlwaysPassProjectRepoToContainerGit",
    "testNoWorkspaceRootedMarkerHardcodeInSource",
    "testNoUnscopedDockerExecOutsideConnection",
    "testAgentActionRegistered",
    "testAgentActionCatalogShape",
    "testWireFormatPathsAreRepoRelative",
    "testStepPayloadsCarrySLabel",
    "testDepsExpandedShowsStepStatusAndTimingAxes",
    "testPipelineStateCarriesLivenessFields",
    "testContainerUserUidIsOneThousand",
    "testManifestWriterKnowsEverySaPathListInGuiSource",
    "testConftestTemplateHasVersionStamp",
    "testNoFlatTestMarkerWritesInSource",
    "testEmptyCommandCategoryIsUnnecessaryAfterLoad",
    "testAtLeastLevel1IffAllFourCriteria",
    "testHashCheckRunsRegardlessOfMtime",
    "testMarkerCoversAllDeclaredOutputs",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
GUI_DIR = REPO_ROOT / "vaibify" / "gui"
ROUTES_DIR = GUI_DIR / "routes"
STATIC_DIR = GUI_DIR / "static"

# Modules that may legitimately omit __all__ (only dunder-init shims).
SET_DUNDER_ALL_EXCEPTIONS = {"__init__.py"}

# Science-specific identifiers forbidden in vaibify source. Extend freely.
LIST_FORBIDDEN_SCIENCE_TERMS = [
    "gj1132",
    "kepler",
    "trappist",
    "proxima",
]

# Directories excluded from source scans (virtualenvs, build artifacts, caches).
SET_EXCLUDED_SCAN_DIRECTORY_FRAGMENTS = (
    "/tests/",
    "/templates/",
    "/docs/",
    "/.venv/",
    "/venv/",
    "/build/",
    "/dist/",
    "/_build/",
    "/__pycache__/",
    "/.git/",
    "/node_modules/",
    "/.pytest_cache/",
)

# Route modules that import from a sibling route module with explicit intent.
# syncRoutes re-uses _fnStoreCommitHash from scriptRoutes to persist the
# upstream commit hash when a sync completes; this helper lives in scriptRoutes
# because the same behaviour runs for non-sync actions as well. Remove the
# entry once the helper is hoisted to a shared non-route module.
SET_ALLOWED_SIBLING_ROUTE_IMPORTS = {
    ("syncRoutes", "scriptRoutes"),
}

# Orchestrator modules and the child modules whose __all__ they re-export.
# pipelineRunner does not re-export pipelineState (it uses it as a namespace
# module via `from . import pipelineState`, not symbol-by-symbol).
DICT_ORCHESTRATOR_CHILDREN = {
    "pipelineRunner": [
        "pipelineValidator",
        "pipelineLogger",
        "pipelineTestRunner",
        "interactiveSteps",
        "pipelineUtils",
    ],
    "pipelineServer": [
        "fileStatusManager",
        "testStatusManager",
    ],
    "testGenerator": [
        "testParser",
        "dataPreview",
        "conftestManager",
        "llmInvoker",
        "templateManager",
    ],
    "syncDispatcher": [
        "fileIntegrity",
    ],
}

# JS files exempt from the raw-fetch ban.
# scriptApiClient.js implements the VaibifyApi wrapper every other module
# must call through. The remaining entries predate the wrapper and are
# tracked technical debt (see the architecture notes about pre-existing,
# unrefactored modules). Do not add new entries to this set; migrate the
# module onto VaibifyApi instead.
SET_FETCH_EXEMPT_JS_FILES = {
    "scriptApiClient.js",
    "scriptApplication.js",
    "scriptFigureViewer.js",
    "scriptResourceMonitor.js",
    "scriptSetupWizard.js",
    "scriptStepEditor.js",
}

# JS files exempt from the raw-onmessage ban: scriptWebSocket.js implements
# the VaibifyWebSocket dispatcher, and scriptTerminal.js runs xterm.js over a
# dedicated terminal WebSocket that predates the dispatcher.
SET_ONMESSAGE_EXEMPT_JS_FILES = {
    "scriptWebSocket.js",
    "scriptTerminal.js",
}

REGEX_RAW_FETCH = re.compile(r"\bfetch\s*\(")
REGEX_RAW_ONMESSAGE = re.compile(r"\.onmessage\b")
REGEX_IIFE_DECLARATION = re.compile(
    r"^\s*(?:var|const|let)\s+\w+\s*=\s*\(\s*function"
)


def fsReadSource(sPath):
    """Return the full text content of a file at sPath."""
    return Path(sPath).read_text(encoding="utf-8")


def flistExtractImports(treeAst):
    """Return a list of (moduleName, iLineNo) tuples for every import node."""
    listImports = []
    for node in ast.walk(treeAst):
        if isinstance(node, ast.Import):
            for alias in node.names:
                listImports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            sModule = node.module or ""
            iLevel = node.level or 0
            sPrefix = "." * iLevel
            listImports.append((sPrefix + sModule, node.lineno))
    return listImports


def fbHasTopLevelFunction(treeAst, sName):
    """Return True if treeAst defines a top-level function named sName."""
    for node in treeAst.body:
        bMatch = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if bMatch and node.name == sName:
            return True
    return False


def fbHasTopLevelDunderAll(treeAst):
    """Return True if treeAst defines a module-level __all__ assignment."""
    for node in treeAst.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return True
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "__all__":
                return True
    return False


def ftParseFile(sPath):
    """Return (sourceText, astTree) for the file at sPath."""
    sSource = fsReadSource(sPath)
    return sSource, ast.parse(sSource, filename=str(sPath))


def testLeafModuleHasNoIntraPackageImports():
    """pipelineUtils.py must not import from the vaibify package."""
    sPath = GUI_DIR / "pipelineUtils.py"
    _, treeAst = ftParseFile(sPath)
    listImports = flistExtractImports(treeAst)
    listViolations = [
        (sName, iLine) for sName, iLine in listImports
        if sName.startswith("vaibify") or sName.startswith(".")
    ]
    assert listViolations == [], (
        f"pipelineUtils.py must be a leaf module but imports: "
        f"{listViolations}"
    )


def testStateManagerHasNoTopLevelIntraPackageImports():
    """stateManager.py must not import from vaibify.gui at module top.

    The dashboard depends on it being importable from
    workflowManager without a cycle. The bootstrap helper imports
    ``containerGit`` lazily inside the function body so the cycle
    is broken at module load time; the test only checks top-level
    nodes (``tree.body``), letting that exception through.
    """
    import ast
    sPath = GUI_DIR / "stateManager.py"
    sSource = sPath.read_text(encoding="utf-8")
    treeAst = ast.parse(sSource)
    listViolations = []
    for node in treeAst.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("vaibify"):
                    listViolations.append(
                        (alias.name, node.lineno),
                    )
        elif isinstance(node, ast.ImportFrom):
            sModule = node.module or ""
            iLevel = node.level or 0
            sFull = ("." * iLevel) + sModule
            if sFull.startswith("vaibify") or sFull.startswith("."):
                listViolations.append((sFull, node.lineno))
    assert listViolations == [], (
        f"stateManager.py top-level imports must be leaf-pure; "
        f"violations: {listViolations}"
    )


def testWorkflowJsonHasNoStatefulFieldsAfterSave():
    """The split must remove dictVerification/dictRunStats/sLabel from workflow.json.

    Asserts the contract by exercising ftSplitMergedDict on a
    representative merged dict and inspecting the declarative half.
    Catches regressions where a future change writes runtime state
    back into the persisted declarative file. Also exercises
    ``_fdictStripComputedFields`` so derived per-step caches
    (``saSourceCodeDeps``, ``saStepScripts``, ``saTestStandards``)
    cannot leak into ``workflow.json`` either.
    """
    from vaibify.gui import stateManager, workflowManager
    dictMerged = {
        "sPlotDirectory": "Plot",
        "bArchiveTrackingMigrated": True,
        "dictStateLoadNotice": {"sLevel": "warning", "sMessage": "x"},
        "listSteps": [
            {
                "sName": "A", "sDirectory": "A",
                "sLabel": "A01",
                "saPlotCommands": [], "saPlotFiles": [],
                "dictVerification": {"sUser": "passed"},
                "dictRunStats": {"fLastRunSeconds": 1.0},
                "saSourceCodeDeps": ["util.py"],
                "saStepScripts": ["A/data.py"],
                "saTestStandards": ["A/tests/quant.json"],
            },
        ],
    }
    dictStripped = workflowManager._fdictStripComputedFields(dictMerged)
    assert "dictStateLoadNotice" not in dictStripped
    for dictStep in dictStripped["listSteps"]:
        for sField in (
            "saSourceCodeDeps", "saStepScripts", "saTestStandards",
        ):
            assert sField not in dictStep, (
                f"step {dictStep['sName']} retained computed "
                f"field {sField!r} after strip"
            )
    dictDeclarative, _ = stateManager.ftSplitMergedDict(dictStripped)
    assert "bArchiveTrackingMigrated" not in dictDeclarative
    for dictStep in dictDeclarative["listSteps"]:
        for sField in (
            "sLabel", "dictVerification", "dictRunStats",
        ):
            assert sField not in dictStep, (
                f"step {dictStep['sName']} retained stateful "
                f"field {sField!r} after split"
            )


def testWorkflowMigrationsImportsOnlyLeafModules():
    """workflowMigrations.py must only depend on documented leaf modules.

    The migration registry is imported by workflowManager.py and
    director.py, so it must sit at the bottom of the dependency graph
    or those callers form a cycle. ``pathContract`` is the only other
    leaf module the migrators need; new intra-package imports here
    are almost always a sign that the migrator should pull state from
    its caller instead of reaching back into the package.
    """
    setAllowedLeaves = {".pathContract"}
    sPath = GUI_DIR / "workflowMigrations.py"
    _, treeAst = ftParseFile(sPath)
    listImports = flistExtractImports(treeAst)
    listViolations = [
        (sName, iLine) for sName, iLine in listImports
        if (sName.startswith("vaibify") or sName.startswith("."))
        and sName not in setAllowedLeaves
    ]
    assert listViolations == [], (
        f"workflowMigrations.py may only import from leaf modules "
        f"({setAllowedLeaves}); violations: {listViolations}"
    )


def testEveryRouteModuleExportsRegisterAll():
    """Every vaibify/gui/routes/*Routes.py defines fnRegisterAll at top level."""
    listRouteFiles = sorted(ROUTES_DIR.glob("*Routes.py"))
    assert listRouteFiles, "No *Routes.py modules found under routes/"
    listMissing = []
    for pathRoute in listRouteFiles:
        _, treeAst = ftParseFile(pathRoute)
        if not fbHasTopLevelFunction(treeAst, "fnRegisterAll"):
            listMissing.append(pathRoute.name)
    assert listMissing == [], (
        f"Route modules missing fnRegisterAll: {listMissing}"
    )


def _fsetGetImportedRouteNames(treeAst):
    """Extract names imported from the routes package in an __init__ AST."""
    setImported = set()
    for node in ast.walk(treeAst):
        if isinstance(node, ast.ImportFrom):
            bRelative = (node.level or 0) >= 1
            if bRelative and (node.module is None or node.module == ""):
                for alias in node.names:
                    setImported.add(alias.name)
    return setImported


def testAllRouteModulesRegisteredInInit():
    """Every *Routes.py is imported by vaibify/gui/routes/__init__.py."""
    sPath = ROUTES_DIR / "__init__.py"
    _, treeAst = ftParseFile(sPath)
    setImported = _fsetGetImportedRouteNames(treeAst)
    listRouteFiles = sorted(ROUTES_DIR.glob("*Routes.py"))
    listMissing = [
        pathRoute.stem for pathRoute in listRouteFiles
        if pathRoute.stem not in setImported
    ]
    assert listMissing == [], (
        f"Route modules not imported in routes/__init__.py: {listMissing}"
    )


def testAllPackageModulesDefineDunderAll():
    """Direct-child modules of vaibify/gui/ declare __all__ (except exceptions)."""
    listModules = sorted(GUI_DIR.glob("*.py"))
    assert listModules, "No python modules found under vaibify/gui/"
    listViolations = []
    for pathModule in listModules:
        if pathModule.name in SET_DUNDER_ALL_EXCEPTIONS:
            continue
        _, treeAst = ftParseFile(pathModule)
        if not fbHasTopLevelDunderAll(treeAst):
            listViolations.append(pathModule.name)
    assert listViolations == [], (
        f"Modules missing __all__: {listViolations}. "
        f"Add __all__ to each, or extend SET_DUNDER_ALL_EXCEPTIONS "
        f"with justification."
    )


def testWorkflowManagerUsesPosixPath():
    """workflowManager.py imports posixpath for container-path manipulation."""
    sPath = GUI_DIR / "workflowManager.py"
    sSource = fsReadSource(sPath)
    assert "import posixpath" in sSource, (
        "workflowManager.py must import posixpath for container paths"
    )


def testDirectorUsesOsPath():
    """director.py uses os.path (host filesystem), not posixpath."""
    sPath = GUI_DIR / "director.py"
    sSource, treeAst = ftParseFile(sPath)
    listImports = flistExtractImports(treeAst)
    setTopNames = {sName for sName, _ in listImports}
    bImportsPosix = any(
        sName == "posixpath" or sName.startswith("posixpath.")
        for sName in setTopNames
    )
    assert not bImportsPosix, (
        "director.py must not import posixpath; host paths use os.path"
    )
    assert "os.path." in sSource, (
        "director.py must actually reference os.path.* for host paths"
    )


def _fbIsRouteSiblingImport(sModulePath, sOwnStem):
    """Return True when sModulePath resolves to a vaibify.gui.routes sibling."""
    sCandidate = sModulePath
    if sCandidate.startswith("."):
        sCandidate = sCandidate.lstrip(".")
    if not sCandidate:
        return False
    if sCandidate.startswith("vaibify.gui.routes."):
        sTail = sCandidate.split(".", 3)[-1]
    elif sModulePath.startswith(".") and not sModulePath.startswith(".."):
        sTail = sCandidate
    else:
        return False
    sSibling = sTail.split(".", 1)[0]
    return sSibling != "" and sSibling != sOwnStem


def _fsExtractSiblingName(sModulePath):
    """Return the route-module stem referenced by a sibling import path."""
    sStripped = sModulePath.lstrip(".")
    if sStripped.startswith("vaibify.gui.routes."):
        return sStripped.split(".", 3)[-1].split(".", 1)[0]
    return sStripped.split(".", 1)[0]


def testRouteModulesDoNotImportSiblings():
    """Route modules must not import from another vaibify/gui/routes/*Routes.py."""
    listRouteFiles = sorted(ROUTES_DIR.glob("*Routes.py"))
    listViolations = []
    for pathRoute in listRouteFiles:
        _, treeAst = ftParseFile(pathRoute)
        for sName, iLine in flistExtractImports(treeAst):
            if not _fbIsRouteSiblingImport(sName, pathRoute.stem):
                continue
            sSibling = _fsExtractSiblingName(sName)
            if (pathRoute.stem, sSibling) in SET_ALLOWED_SIBLING_ROUTE_IMPORTS:
                continue
            listViolations.append((pathRoute.name, sName, iLine))
    assert listViolations == [], (
        "Route modules must not import from sibling routes/*Routes.py:\n"
        + "\n".join(f"  {n}:{ln}: {m}" for n, m, ln in listViolations)
    )


def _flistJsFeatureFiles(setExemptFilenames):
    """Return JS files under static/ excluding the given exempt filenames."""
    return [
        pathFile for pathFile in sorted(STATIC_DIR.glob("*.js"))
        if pathFile.name not in setExemptFilenames
    ]


def _flistRegexHits(pathFile, regexPattern):
    """Return (iLine, sText) hits of regexPattern in the file at pathFile."""
    listHits = []
    sSource = fsReadSource(pathFile)
    for iLineNo, sLine in enumerate(sSource.splitlines(), start=1):
        if regexPattern.search(sLine):
            listHits.append((iLineNo, sLine.strip()))
    return listHits


def testNoRawFetchInFeatureModules():
    """JS feature modules must call VaibifyApi, not fetch() directly."""
    listFeatureFiles = _flistJsFeatureFiles(SET_FETCH_EXEMPT_JS_FILES)
    listViolations = []
    for pathFile in listFeatureFiles:
        for iLine, sText in _flistRegexHits(pathFile, REGEX_RAW_FETCH):
            listViolations.append((pathFile.name, iLine, sText))
    assert listViolations == [], (
        "JS feature modules must route HTTP through VaibifyApi, not fetch():\n"
        + "\n".join(f"  {n}:{ln}: {t}" for n, ln, t in listViolations)
    )


def testNoRawOnMessageInFeatureModules():
    """JS feature modules must route WS events through VaibifyWebSocket."""
    listFeatureFiles = _flistJsFeatureFiles(SET_ONMESSAGE_EXEMPT_JS_FILES)
    listViolations = []
    for pathFile in listFeatureFiles:
        for iLine, sText in _flistRegexHits(pathFile, REGEX_RAW_ONMESSAGE):
            listViolations.append((pathFile.name, iLine, sText))
    assert listViolations == [], (
        "JS feature modules must subscribe via VaibifyWebSocket, "
        "not attach raw .onmessage handlers:\n"
        + "\n".join(f"  {n}:{ln}: {t}" for n, ln, t in listViolations)
    )


def _flistMissingReExports(sOrchestrator, listChildNames):
    """Return (sChild, sSymbol) pairs the orchestrator fails to re-export."""
    moduleOrchestrator = importlib.import_module(
        "vaibify.gui." + sOrchestrator
    )
    listMissing = []
    for sChild in listChildNames:
        moduleChild = importlib.import_module("vaibify.gui." + sChild)
        for sSymbol in getattr(moduleChild, "__all__", []):
            if not hasattr(moduleOrchestrator, sSymbol):
                listMissing.append((sChild, sSymbol))
    return listMissing


def testOrchestratorReExportsAreComplete():
    """Every symbol in each child's __all__ resolves on its orchestrator."""
    listViolations = []
    for sOrch, listChildren in DICT_ORCHESTRATOR_CHILDREN.items():
        for sChild, sSymbol in _flistMissingReExports(sOrch, listChildren):
            listViolations.append((sOrch, sChild, sSymbol))
    assert listViolations == [], (
        "Orchestrator re-export shims are incomplete:\n"
        + "\n".join(
            f"  {sOrch} does not expose {sChild}.{sSymbol}"
            for sOrch, sChild, sSymbol in listViolations
        )
    )


def testEveryJsFileIsRecognizedAsIIFE():
    """Every vaibify/gui/static/*.js declares an IIFE module at its top."""
    listJsFiles = sorted(STATIC_DIR.glob("*.js"))
    assert listJsFiles, "No JavaScript modules found under static/"
    listViolations = []
    for pathFile in listJsFiles:
        sSource = fsReadSource(pathFile)
        if not any(
            REGEX_IIFE_DECLARATION.match(sLine)
            for sLine in sSource.splitlines()
        ):
            listViolations.append(pathFile.name)
    assert listViolations == [], (
        f"JavaScript modules missing IIFE declaration: {listViolations}"
    )


def _fbIsExcludedScanPath(pathFile):
    """Return True when pathFile lives in an excluded build/vendor directory."""
    sPosix = pathFile.as_posix().lower()
    return any(
        sFragment in sPosix
        for sFragment in SET_EXCLUDED_SCAN_DIRECTORY_FRAGMENTS
    )


_TUPLE_SCIENCE_SCAN_GLOBS = ("*.py", "*.html", "*.js", "*.css")


def _flistScanForTerm(pathRoot, sTerm):
    """Return (pathFile, iLineNo, sLine, sMatchedToken) matches for sTerm.

    Scans user-facing source files (Python, HTML, JS, CSS) for the
    given identifier. HTML and JS coverage closes the gap left by the
    original Python-only sweep — placeholder strings, comments, and
    inline labels are the most likely vehicle for a project-specific
    name to leak into a release build.
    """
    regexTerm = re.compile(r"\b" + re.escape(sTerm) + r"\b", re.IGNORECASE)
    listHits = []
    for sGlob in _TUPLE_SCIENCE_SCAN_GLOBS:
        for pathFile in pathRoot.rglob(sGlob):
            if _fbIsExcludedScanPath(pathFile):
                continue
            try:
                sSource = fsReadSource(pathFile)
            except (OSError, UnicodeDecodeError):
                continue
            for iLineNo, sLine in enumerate(
                sSource.splitlines(), start=1,
            ):
                matchTerm = regexTerm.search(sLine)
                if matchTerm:
                    listHits.append(
                        (pathFile, iLineNo, sLine.strip(),
                         matchTerm.group(0)),
                    )
    return listHits


def testNoScienceSpecificIdentifiersInSource():
    """Vaibify source contains no hard-coded science-mission identifiers."""
    pathRoot = REPO_ROOT / "vaibify"
    listViolations = []
    for sTerm in LIST_FORBIDDEN_SCIENCE_TERMS:
        listViolations.extend(
            (sTerm, p, iLine, sText, sToken)
            for p, iLine, sText, sToken in _flistScanForTerm(pathRoot, sTerm)
        )
    assert listViolations == [], (
        "Science-specific identifiers found in vaibify source:\n"
        + "\n".join(
            f"  [{sTerm} -> {sToken}] {p}:{iLine}: {sText}"
            for sTerm, p, iLine, sText, sToken in listViolations
        )
    )


# containerGit helpers that accept sWorkspace (all except the
# project-repo detector, which consumes sWorkflowPath instead).
SET_CONTAINER_GIT_WORKSPACE_FUNCTIONS = {
    "fdictGitStatusInContainer",
    "fdictComputeBlobShasInContainer",
    "flistListContainerFiles",
    "fsGitHeadShaInContainer",
    "ftResultGitAddInContainer",
    "ftResultGitCommitInContainer",
}


def _fbCallProvidesWorkspaceKwarg(nodeCall):
    """Return True when nodeCall passes sWorkspace as a keyword argument."""
    for keyword in nodeCall.keywords or []:
        if keyword.arg == "sWorkspace":
            return True
    return False


def _fbIsContainerGitCall(nodeCall):
    """Return True when nodeCall is a containerGit.<name>(...) attribute call."""
    if not isinstance(nodeCall.func, ast.Attribute):
        return False
    if not isinstance(nodeCall.func.value, ast.Name):
        return False
    return nodeCall.func.value.id == "containerGit"


def testGitRoutesAlwaysPassProjectRepoToContainerGit():
    """Every containerGit.* call in gitRoutes.py passes sWorkspace explicitly.

    The workspace default is ``/workspace`` (a Docker-managed volume
    that is not itself a git work tree). Routes must resolve the
    active workflow's project repo and forward it explicitly — a
    silent fallback to the default would reintroduce the all-grey
    badge bug where every request runs git against a non-repo path.
    """
    sPath = ROUTES_DIR / "gitRoutes.py"
    _, treeAst = ftParseFile(sPath)
    listViolations = []
    for node in ast.walk(treeAst):
        if not isinstance(node, ast.Call):
            continue
        if not _fbIsContainerGitCall(node):
            continue
        sAttr = node.func.attr
        if sAttr not in SET_CONTAINER_GIT_WORKSPACE_FUNCTIONS:
            continue
        if not _fbCallProvidesWorkspaceKwarg(node):
            listViolations.append((sAttr, node.lineno))
    assert listViolations == [], (
        "gitRoutes.py must pass sWorkspace=<project-repo> to every "
        "containerGit.* call; relying on the default reintroduces the "
        "/workspace-as-repo bug:\n"
        + "\n".join(
            f"  {sAttr}() on line {iLine}" for sAttr, iLine in listViolations
        )
    )


S_MARKER_HARDCODE_FORBIDDEN = "/workspace/.vaibify/test_markers"

SET_MARKER_HARDCODE_EXEMPT_FILES = {
    "stateContract.py",
}


def testNoWorkspaceRootedMarkerHardcodeInSource():
    """No vaibify/gui module may hardcode /workspace/.vaibify/test_markers.

    Test markers live under the active workflow's project repo —
    ``<sProjectRepoPath>/.vaibify/test_markers/`` — resolved from the
    workflow dict at request time. A string literal like
    ``/workspace/.vaibify/test_markers`` in module code reintroduces
    the workspace-rooted layout and causes badges/manifest to look at
    one directory while step-status reads from another. Keep the
    single exempt file list tight; ``stateContract.py`` refers to the
    directory name in a docstring/comment as documentation.
    """
    pathGui = GUI_DIR
    listViolations = []
    for pathFile in pathGui.rglob("*.py"):
        if pathFile.name in SET_MARKER_HARDCODE_EXEMPT_FILES:
            continue
        sSource = fsReadSource(pathFile)
        for iLineNo, sLine in enumerate(sSource.splitlines(), start=1):
            if S_MARKER_HARDCODE_FORBIDDEN in sLine:
                listViolations.append(
                    (pathFile.name, iLineNo, sLine.strip())
                )
    assert listViolations == [], (
        f"Modules must not hardcode {S_MARKER_HARDCODE_FORBIDDEN!r}:\n"
        + "\n".join(
            f"  {sFile}:{iLine}: {sText}"
            for sFile, iLine, sText in listViolations
        )
    )


SET_SUBPROCESS_RUN_ATTRS = {
    "run", "Popen", "call", "check_call", "check_output",
}


def _fbIsSubprocessRunCall(nodeCall):
    """Return True when nodeCall invokes one of subprocess's run-style APIs."""
    if not isinstance(nodeCall.func, ast.Attribute):
        return False
    if nodeCall.func.attr not in SET_SUBPROCESS_RUN_ATTRS:
        return False
    nodeValue = nodeCall.func.value
    if not isinstance(nodeValue, ast.Name):
        return False
    return nodeValue.id == "subprocess"


def _flistArgvFromListNode(nodeList):
    """Return string literals from an ``ast.List``; non-strings become None.

    A None entry marks "some value lives here, but it isn't a string
    literal" so adjacency checks (e.g. ``docker exec``) still work and
    flag presence checks (``-u``) remain conservative.
    """
    listValues = []
    for nodeElement in nodeList.elts:
        if isinstance(nodeElement, ast.Constant) and isinstance(
            nodeElement.value, str,
        ):
            listValues.append(nodeElement.value)
        else:
            listValues.append(None)
    return listValues


def _fnIndexAssignmentsInScope(nodeScope, dictByName):
    """Record every ``name = [literal-list]`` assignment within nodeScope.

    Does not descend into nested function or class definitions so each
    scope owns its own variable bindings (matters when the same name
    like ``listCommand`` is reused across helpers in the same module).
    """
    for nodeChild in ast.iter_child_nodes(nodeScope):
        if isinstance(nodeChild, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
            continue
        if isinstance(nodeChild, ast.Assign) and isinstance(
            nodeChild.value, ast.List,
        ):
            for nodeTarget in nodeChild.targets:
                if isinstance(nodeTarget, ast.Name):
                    dictByName[nodeTarget.id] = _flistArgvFromListNode(
                        nodeChild.value,
                    )
        _fnIndexAssignmentsInScope(nodeChild, dictByName)


def _fdictCollectScopedListAssignments(treeAst):
    """Map ``ast.Call`` -> ``{name: argv}`` resolved at the call's own scope.

    Each call inherits the module-level assignments plus the
    assignments inside its enclosing function/class. Names declared in
    sibling functions are intentionally invisible so a literal in one
    helper cannot poison the resolution of a same-named variable in
    another helper.
    """
    dictModule = {}
    _fnIndexAssignmentsInScope(treeAst, dictModule)
    dictByCall = {}
    for nodeScope in _flistFunctionLikeScopes(treeAst):
        dictScoped = dict(dictModule)
        _fnIndexAssignmentsInScope(nodeScope, dictScoped)
        for nodeCall in ast.walk(nodeScope):
            if isinstance(nodeCall, ast.Call):
                dictByCall[id(nodeCall)] = dictScoped
    return dictByCall, dictModule


def _flistFunctionLikeScopes(treeAst):
    """Return every FunctionDef/AsyncFunctionDef node in treeAst."""
    listScopes = []
    for nodeScope in ast.walk(treeAst):
        if isinstance(nodeScope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            listScopes.append(nodeScope)
    return listScopes


def _flistExtractStaticArgv(nodeCall, dictByCall, dictModule):
    """Return string literals from the call's first positional argv.

    Accepts an inline ``ast.List`` or an ``ast.Name`` that refers to a
    list assigned in the call's enclosing function (or module). Returns
    an empty list when argv is neither shape — the bug we guard against
    requires a statically resolvable command list to be useful.
    """
    if not nodeCall.args:
        return []
    nodeArgv = nodeCall.args[0]
    if isinstance(nodeArgv, ast.List):
        return _flistArgvFromListNode(nodeArgv)
    if isinstance(nodeArgv, ast.Name):
        dictScope = dictByCall.get(id(nodeCall), dictModule)
        return list(dictScope.get(nodeArgv.id, []))
    return []


def _fbArgvInvokesDockerExec(listArgv):
    """Return True when listArgv begins ``docker exec ...`` (as adjacent tokens)."""
    for iIndex in range(len(listArgv) - 1):
        if listArgv[iIndex] == "docker" and listArgv[iIndex + 1] == "exec":
            return True
    return False


def _fbArgvPinsUser(listArgv):
    """Return True when listArgv contains an explicit -u or --user flag."""
    return "-u" in listArgv or "--user" in listArgv


def testNoUnscopedDockerExecOutsideConnection():
    """Direct ``docker exec`` subprocess calls must pin -u explicitly.

    Prevents reintroduction of the root-default exec bug: any
    host-side code that bypasses ``dockerConnection`` and shells out
    to ``docker exec`` must specify the user, because plain
    ``docker exec`` inherits the container's runtime user — which is
    root for vaibify containers (the entrypoint phase requires
    ``docker run --user 0`` before ``gosu``-dropping to the install
    user for PID 1). Routing through ``dockerConnection`` is the
    preferred fix; an explicit ``-u`` flag is the escape hatch when
    the dispatcher is not available (e.g. CLI commands).
    """
    pathVaibify = REPO_ROOT / "vaibify"
    listOffenders = []
    for pathFile in pathVaibify.rglob("*.py"):
        if _fbIsExcludedScanPath(pathFile):
            continue
        try:
            _, treeAst = ftParseFile(pathFile)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        dictByCall, dictModule = _fdictCollectScopedListAssignments(treeAst)
        for node in ast.walk(treeAst):
            if not isinstance(node, ast.Call):
                continue
            if not _fbIsSubprocessRunCall(node):
                continue
            listArgv = _flistExtractStaticArgv(node, dictByCall, dictModule)
            if not _fbArgvInvokesDockerExec(listArgv):
                continue
            if _fbArgvPinsUser(listArgv):
                continue
            listOffenders.append(
                (pathFile.relative_to(REPO_ROOT), node.lineno)
            )
    assert listOffenders == [], (
        "Direct `docker exec` subprocess calls must pass -u explicitly "
        "(route through dockerConnection.ftResultExecuteCommand or add "
        "-u/--user). Without -u, exec lands as the container's runtime "
        "user, which is root when --user 0 was used at docker run.\n"
        + "\n".join(
            f"  {pathRel}:{iLine}"
            for pathRel, iLine in listOffenders
        )
    )


def testDockerfileDisablesAptSandboxBeforeFirstUpdate():
    """Dockerfile must disable the _apt sandbox before any apt-get update.

    The unprivileged _apt user (home: /nonexistent) causes gpgv to fail
    signature verification under certain apt 2.x versions inside
    containers, producing a misleading 'invalid signature' error. The
    workaround is to run apt as root via APT::Sandbox::User "root"; this
    test guards against the line being removed or relocated below the
    first apt-get update, which would silently regress the fix.
    """
    sDockerfile = fsReadSource(REPO_ROOT / "docker" / "Dockerfile")
    matchSandbox = re.search(
        r'APT::Sandbox::User\s+"root"', sDockerfile
    )
    assert matchSandbox, (
        "Dockerfile must set 'APT::Sandbox::User \"root\"' to work around "
        "the _apt/gpgv signature-verification bug in container builds"
    )
    matchFirstUpdate = re.search(r"apt-get\s+update", sDockerfile)
    assert matchFirstUpdate, (
        "Dockerfile missing any apt-get update — unexpected state"
    )
    assert matchSandbox.start() < matchFirstUpdate.start(), (
        "APT::Sandbox::User directive must appear before the first "
        "apt-get update; otherwise the first update runs under the "
        "broken sandbox and fails with an 'invalid signature' error"
    )


# ---------------------------------------------------------------
# Agent-action catalog invariants
# ---------------------------------------------------------------

_SET_STATE_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE"})


def _flistCollectAppStateMutatingRoutes(app):
    """Return [(sMethod, sPath, endpoint_fn)] for state-mutating routes."""
    listResult = []
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        listMutMethods = sorted(
            _SET_STATE_MUTATING_METHODS & set(route.methods or ())
        )
        for sMethod in listMutMethods:
            listResult.append((sMethod, route.path, route.endpoint))
    return listResult


def _fappBuildApplication():
    """Build the workflow-viewer FastAPI app with docker mocked."""
    from unittest.mock import MagicMock, patch
    from vaibify.gui.pipelineServer import fappCreateApplication
    with patch(
        "vaibify.gui.pipelineServer._fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        return fappCreateApplication(iExpectedPort=0)


def testAgentActionRegistered():
    """Every state-mutating route must be in the agent catalog or excluded.

    The in-container ``vaibify-do`` CLI reads
    ``vaibify.gui.actionCatalog.LIST_AGENT_ACTIONS`` to translate
    researcher intent into backend calls. A state-mutating HTTP route
    that is neither decorated with ``@fnAgentAction`` nor declared in
    ``SET_INTENTIONALLY_EXCLUDED_PATHS`` is invisible to the agent —
    and the dashboard silently drifts when the agent improvises.
    """
    from vaibify.gui import actionCatalog
    app = _fappBuildApplication()
    listRoutes = _flistCollectAppStateMutatingRoutes(app)
    dictCatalogByPath = {
        (dictEntry["sMethod"], dictEntry["sPath"]): dictEntry["sName"]
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["sMethod"] != "WS"
    }
    listViolations = []
    for sMethod, sPath, fnEndpoint in listRoutes:
        tKey = (sMethod, sPath)
        if tKey in actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS:
            continue
        sCatalogName = dictCatalogByPath.get(tKey)
        if sCatalogName is None:
            listViolations.append(
                f"{sMethod} {sPath} is not in LIST_AGENT_ACTIONS or "
                f"SET_INTENTIONALLY_EXCLUDED_PATHS"
            )
            continue
        sDecoratorName = getattr(
            fnEndpoint, "_sAgentActionName", None,
        )
        if sDecoratorName != sCatalogName:
            listViolations.append(
                f"{sMethod} {sPath} catalog says sName="
                f"{sCatalogName!r} but handler has "
                f"_sAgentActionName={sDecoratorName!r}"
            )
    assert listViolations == [], (
        "Agent-action registration violations:\n  "
        + "\n  ".join(listViolations)
    )


def testAgentActionCatalogShape():
    """Catalog entries must have the required fields and consistent types."""
    from vaibify.gui import actionCatalog
    setSeenNames = set()
    listViolations = []
    for iIndex, dictEntry in enumerate(
        actionCatalog.LIST_AGENT_ACTIONS
    ):
        for sKey in (
            "sName", "sCategory", "sMethod", "sPath",
            "bAgentSafe", "sDescription",
        ):
            if sKey not in dictEntry:
                listViolations.append(
                    f"entry {iIndex}: missing key {sKey!r}"
                )
        sName = dictEntry.get("sName", "")
        if sName in setSeenNames:
            listViolations.append(
                f"entry {iIndex}: duplicate sName={sName!r}"
            )
        setSeenNames.add(sName)
        sMethod = dictEntry.get("sMethod", "")
        if sMethod not in ("WS", "POST", "PUT", "DELETE", "GET"):
            listViolations.append(
                f"entry {iIndex} ({sName}): bad sMethod={sMethod!r}"
            )
        if not isinstance(dictEntry.get("bAgentSafe"), bool):
            listViolations.append(
                f"entry {iIndex} ({sName}): bAgentSafe must be bool"
            )
    assert listViolations == [], (
        "Catalog shape violations:\n  "
        + "\n  ".join(listViolations)
    )


_SET_APPROVED_LIST_MODIFIED_WRITERS = frozenset({
    # Only these two functions may assign directly to
    # dictVerification['listModifiedFiles']. The first is the
    # invalidator (which normalizes via flistNormalizeModifiedFiles
    # before writing); the second is the one-shot loader migration
    # that rewrites legacy abs paths in place.
    "_fnInvalidateStepFiles",
    "fbMigrateModifiedFilesToRepoRelative",
})


_SET_VERIFICATION_DICT_NAMES = frozenset({
    "dictVerification", "dictVerify", "dictV",
})


def _flistFindListModifiedAssignmentSites(treeAst):
    """Return [(functionName, lineNumber)] for every subscript assignment
    to ``<verificationDict>['listModifiedFiles']`` in the AST, scoped
    to the enclosing function. The receiver must be a bare Name in
    ``_SET_VERIFICATION_DICT_NAMES`` to avoid matching unrelated keys
    like ``dictResult['listModifiedFiles']`` used elsewhere.
    """
    listSites = []
    for node in ast.walk(treeAst):
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        for nodeInner in ast.walk(node):
            if not isinstance(nodeInner, ast.Assign):
                continue
            for nodeTarget in nodeInner.targets:
                if not isinstance(nodeTarget, ast.Subscript):
                    continue
                if not isinstance(nodeTarget.value, ast.Name):
                    continue
                if nodeTarget.value.id not in (
                    _SET_VERIFICATION_DICT_NAMES
                ):
                    continue
                sliceNode = nodeTarget.slice
                sKey = None
                if isinstance(sliceNode, ast.Constant):
                    sKey = sliceNode.value
                if sKey == "listModifiedFiles":
                    listSites.append((node.name, nodeInner.lineno))
    return listSites


def _fbCallsHelperOnReturnedKey(treeAst, sFunctionName, sHelperName):
    """Return True if `sFunctionName` returns a dict whose ``dictModTimes``
    value is the result of a ``sHelperName(...)`` call.
    """
    for node in ast.walk(treeAst):
        bMatch = isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef),
        ) and node.name == sFunctionName
        if not bMatch:
            continue
        for nodeReturn in ast.walk(node):
            if not isinstance(nodeReturn, ast.Return):
                continue
            if not isinstance(nodeReturn.value, ast.Dict):
                continue
            for keyNode, valueNode in zip(
                nodeReturn.value.keys,
                nodeReturn.value.values,
            ):
                if not isinstance(keyNode, ast.Constant):
                    continue
                if keyNode.value != "dictModTimes":
                    continue
                if not isinstance(valueNode, ast.Call):
                    continue
                fnNode = valueNode.func
                if isinstance(fnNode, ast.Name):
                    if fnNode.id == sHelperName:
                        return True
                if isinstance(fnNode, ast.Attribute):
                    if fnNode.attr == sHelperName:
                        return True
    return False


def testWireFormatPathsAreRepoRelative():
    """`_fdictFetchOutputStatus` must convert dictModTimes via the contract.

    The path-contract module owns the abs->repo-relative translation
    at every wire boundary. This test asserts that the routes module
    imports the helper *and* uses it on the dictModTimes key of the
    returned status dict. It also asserts fileStatusManager imports
    the contract so the invalidator can normalize listModifiedFiles.
    """
    sRoutesPath = ROUTES_DIR / "pipelineRoutes.py"
    sFileStatusPath = GUI_DIR / "fileStatusManager.py"
    sRoutesSource, treeRoutes = ftParseFile(sRoutesPath)
    sFileStatusSource = fsReadSource(sFileStatusPath)
    assert "from ..pathContract import" in sRoutesSource, (
        "pipelineRoutes.py must import from pathContract for "
        "wire-format conversion"
    )
    assert "from .pathContract import" in sFileStatusSource, (
        "fileStatusManager.py must import from pathContract for "
        "listModifiedFiles normalization"
    )
    bUsesHelper = _fbCallsHelperOnReturnedKey(
        treeRoutes,
        "_fdictFetchOutputStatus",
        "fdictAbsKeysToRepoRelative",
    )
    assert bUsesHelper, (
        "_fdictFetchOutputStatus must wrap dictModTimes with "
        "fdictAbsKeysToRepoRelative before returning it"
    )
    listViolations = []
    for pathModule in sorted(GUI_DIR.rglob("*.py")):
        _, treeModule = ftParseFile(pathModule)
        for sFunction, iLine in _flistFindListModifiedAssignmentSites(
            treeModule,
        ):
            if sFunction in _SET_APPROVED_LIST_MODIFIED_WRITERS:
                continue
            listViolations.append(
                f"{pathModule.relative_to(REPO_ROOT)}:{iLine} "
                f"in {sFunction} assigns dictVerification["
                f"'listModifiedFiles'] outside the approved helpers "
                f"({sorted(_SET_APPROVED_LIST_MODIFIED_WRITERS)}); "
                f"route all writes through flistNormalizeModifiedFiles."
            )
    assert not listViolations, (
        "listModifiedFiles write-contract violated:\n  "
        + "\n  ".join(listViolations)
    )


_SET_STEP_LABEL_HELPERS = frozenset({
    "fdictStepWithLabel",
    "flistStepsWithLabels",
    "fdictWorkflowWithLabels",
})


def testStepPayloadsCarrySLabel():
    """Step payloads on the wire must route through a label decorator.

    User-facing identity for steps is the label (A09, I01); the index
    is a 0-based internal handle. The pipelineUtils module exposes
    three non-mutating decorators that attach ``sLabel`` to a shallow
    copy of the step dict(s) before serialization. Routes that
    emit step data must reach a decorator somewhere in their return
    path — a bare ``return dictWorkflow["listSteps"]`` or
    ``return dictWorkflow`` from a step-emitting route silently drops
    ``sLabel`` and reintroduces the label-translation bug class.
    """
    sStepRoutesSource = fsReadSource(ROUTES_DIR / "stepRoutes.py")
    assert "flistStepsWithLabels" in sStepRoutesSource, (
        "stepRoutes.py must import and use flistStepsWithLabels "
        "for listSteps responses"
    )
    assert "fdictStepWithLabel" in sStepRoutesSource, (
        "stepRoutes.py must import and use fdictStepWithLabel "
        "for single-step responses"
    )
    assert 'return dictWorkflow["listSteps"]' not in sStepRoutesSource, (
        "stepRoutes.py returns a bare listSteps payload without "
        "sLabel; route through flistStepsWithLabels instead"
    )
    sPipelineServerSource = fsReadSource(
        GUI_DIR / "pipelineServer.py",
    )
    assert "fdictWorkflowWithLabels" in sPipelineServerSource, (
        "pipelineServer.py's fdictHandleConnect must decorate the "
        "workflow payload with fdictWorkflowWithLabels so every "
        "step reaching the client carries sLabel"
    )


def testDepsExpandedShowsStepStatusAndTimingAxes():
    """Per-dep expansion must show Step Status + Timing axes.

    Every dependency shown in the expanded Dependencies row must
    render two sub-axes: Step Status (is the dep itself fully
    passing) and Timing (was the dep's output produced before or
    after *this step's own output*). Timing compares dep output
    mtime to THIS step's output mtime — not the verification time —
    so the researcher can tell whether my output was built from the
    dep's current state or from an earlier version.

    ``ftComputeDepAxisStates`` in ``scriptApplication.js`` owns the
    computation; ``fsRenderDepAxisRow`` in ``scriptStepRenderer.js``
    owns the rendering. The per-dep breakdown replaces the earlier
    floating ``"Dependencies failing"`` / ``"Upstream step outputs
    changed"`` lines inside the verification block — those must not
    reappear.
    """
    sAppSource = fsReadSource(STATIC_DIR / "scriptApplication.js")
    assert "function ftComputeDepAxisStates" in sAppSource, (
        "scriptApplication.js must define ftComputeDepAxisStates "
        "with step-status + timing sub-axes for per-dep breakdown"
    )
    assert "function fbAnyDepTimingStale" in sAppSource, (
        "scriptApplication.js must derive the staleness signal for "
        "the ⚠ warning badge from per-dep Timing (fresh mtime "
        "comparison), not from the sticky bUpstreamModified flag — "
        "the flag lags user attestation and gives false warnings"
    )
    assert "iMyOutputMtime" in sAppSource, (
        "Timing comparison must reference the step's OWN output "
        "mtime, not its verification time — so 'my output was "
        "built before dep was regenerated' is caught"
    )
    sRendererSource = fsReadSource(
        STATIC_DIR / "scriptStepRenderer.js",
    )
    assert "fsRenderDepAxisRow" in sRendererSource, (
        "scriptStepRenderer.js must render a sub-row per axis "
        "(Step Status and Timing) inside each dep-item"
    )
    assert "Step Status" in sRendererSource, (
        "Deps expansion must label the step-passing sub-axis "
        "as 'Step Status'"
    )
    assert "Timing" in sRendererSource, (
        "Deps expansion must label the output-mtime sub-axis "
        "as 'Timing'"
    )
    assert "Dependencies failing" not in sRendererSource, (
        "The floating 'Dependencies failing' line must not reappear "
        "in the verification block — the per-dep expansion now "
        "explains the aggregate"
    )
    assert "Upstream step outputs changed" not in sRendererSource, (
        "The floating 'Upstream step outputs changed' line must not "
        "reappear in the verification block — see per-dep Timing axis"
    )


_TUPLE_LIVENESS_FIELDS = (
    "iRunnerPid",
    "sLastHeartbeat",
    "sFailureReason",
)


def testPipelineStateCarriesLivenessFields():
    """``pipeline_state.json`` must carry the runner-liveness contract.

    The dashboard's "running" badge depends on three fields written by
    every fresh state file: ``iRunnerPid`` (diagnostic stamp of the
    runner process), ``sLastHeartbeat`` (the truth signal that the
    poll endpoint uses to detect a vanished runner), and
    ``sFailureReason`` (populated by the poll-side reconciler when it
    flips ``bRunning`` to False on a stale heartbeat). Dropping any of
    them silently reintroduces the "dashboard says running for 2
    hours after the runner died" failure mode.
    """
    sPipelineStateSource = fsReadSource(GUI_DIR / "pipelineState.py")
    for sField in _TUPLE_LIVENESS_FIELDS:
        assert f'"{sField}"' in sPipelineStateSource, (
            f"pipelineState.fdictBuildInitialState must include "
            f"'{sField}' in the schema; the dashboard's runner-liveness "
            f"contract depends on it."
        )
    # The runner side must stamp its PID and the poll side must
    # reconcile on stale heartbeat — both load-bearing modules must
    # at least reference the schema fields and the stale-detection
    # helper.
    sPipelineRunnerSource = fsReadSource(GUI_DIR / "pipelineRunner.py")
    assert "iRunnerPid" in sPipelineRunnerSource, (
        "pipelineRunner must stamp iRunnerPid into the initial state "
        "(use os.getpid() in fdictBuildInitialState)."
    )
    assert "_fnRunHeartbeatLoop" in sPipelineRunnerSource, (
        "pipelineRunner must spawn a heartbeat loop; without it the "
        "poll endpoint cannot detect a vanished runner."
    )
    sPipelineRoutesSource = fsReadSource(
        ROUTES_DIR / "pipelineRoutes.py",
    )
    assert "fbHeartbeatIsStale" in sPipelineRoutesSource, (
        "pipelineRoutes.fnGetPipelineState must call "
        "pipelineState.fbHeartbeatIsStale to reconcile a vanished "
        "runner before returning state to the frontend."
    )


def testContainerUserUidIsOneThousand():
    """Dockerfile must pin the container user to UID 1000.

    The credential keyring volume is owned by UID 1000. If a future
    Dockerfile edit changed the container user's UID, the volume's
    keyring files would become unreadable across rebuilds and the
    user would silently lose stored Overleaf and Zenodo tokens.
    Defense-in-depth for audit finding F-R-07.
    """
    sDockerfile = fsReadSource(REPO_ROOT / "docker" / "Dockerfile")
    matchUseradd = re.search(
        r"useradd\s+-m\s+-s\s+/bin/bash\s+-u\s+1000\s+\$\{CONTAINER_USER\}",
        sDockerfile,
    )
    assert matchUseradd, (
        "Dockerfile must create the container user with "
        "'useradd -m -s /bin/bash -u 1000 ${CONTAINER_USER}' so "
        "the credentials volume's UID 1000 ownership stays valid "
        "across rebuilds (audit finding F-R-07)."
    )


def testManifestWriterKnowsEverySaPathListInGuiSource():
    """Every ``sa<Word>Files`` literal referenced by gui/repro source code
    must appear in ``manifestWriter._OUTPUT_KEYS``.

    Catches the failure mode the hard-coded sibling test cannot: a
    future contributor extends ``workflow.json`` with a new path-list
    key (e.g. ``saArchiveFiles``), wires it into the workflow loader,
    but forgets to teach the manifest writer about it. Without this
    invariant, third parties run ``sha256sum -c MANIFEST.sha256``,
    every listed entry passes, and they conclude the reproduction is
    bit-perfect — even though the new artefacts were never tracked.
    """
    from vaibify.reproducibility import manifestWriter
    setKnownKeys = set(manifestWriter._OUTPUT_KEYS)
    setReferencedKeys = _fsetCollectSaFilesLiterals()
    setOutputKeys = setReferencedKeys - SET_NON_OUTPUT_SA_FILES_KEYS
    listMissing = sorted(setOutputKeys - setKnownKeys)
    assert listMissing == [], (
        f"manifestWriter._OUTPUT_KEYS is missing path-list keys "
        f"referenced elsewhere in source: {listMissing}. Either add "
        f"them to _OUTPUT_KEYS in vaibify/reproducibility/"
        f"manifestWriter.py, or add them to "
        f"SET_NON_OUTPUT_SA_FILES_KEYS in this test if they are "
        f"inputs / runtime-derived fields."
    )


_REGEX_SA_FILES_LITERAL = re.compile(r'["\'](sa[A-Z][A-Za-z]*Files)["\']')


# sa*Files keys that are NOT workflow-declared outputs and therefore must
# not appear in MANIFEST.sha256. Inputs are consumed not produced; resolved
# fields are runtime-decorated views, not declarations. Each entry is
# annotated with where it lives so a future contributor can audit quickly.
SET_NON_OUTPUT_SA_FILES_KEYS = {
    # Step-level input list; provenanceTracker uses it to draw DAG edges
    # from inputs to the step. Inputs are produced upstream, not by this
    # step, so they belong to the upstream step's outputs.
    "saInputFiles",
    # stepRoutes decorates the response with a resolved view of the
    # step's outputs; this is a runtime projection, not a declaration.
    "saResolvedOutputFiles",
}


def _fsetCollectSaFilesLiterals():
    """Scan every Python module under vaibify/ for ``sa<Word>Files`` literals.

    Walking the whole package (not just ``gui/`` and ``reproducibility/``)
    catches a future contributor who introduces a new path-list key in
    ``vaibify/cli/``, ``vaibify/config/``, ``vaibify/docker/``, or
    ``vaibify/testing/`` without teaching the manifest writer about it.
    Build-artifact and vendored directories are excluded via
    ``SET_EXCLUDED_SCAN_DIRECTORY_FRAGMENTS`` (which already covers
    ``tests/``, ``templates/``, ``docs/``, and the usual caches).
    """
    setLiterals = set()
    pathRoot = REPO_ROOT / "vaibify"
    for pathPy in pathRoot.rglob("*.py"):
        sPosix = pathPy.as_posix()
        if any(s in sPosix for s in
               SET_EXCLUDED_SCAN_DIRECTORY_FRAGMENTS):
            continue
        sSource = fsReadSource(pathPy)
        for matchOne in _REGEX_SA_FILES_LITERAL.finditer(sSource):
            setLiterals.add(matchOne.group(1))
    return setLiterals


def testConftestTemplateHasVersionStamp():
    """Every generated conftest source carries a version sentinel.

    The dashboard's connect-time refresh helper compares the embedded
    ``# vaibify-conftest-version:`` line against
    ``S_CONFTEST_VERSION`` to decide whether to rewrite stale copies
    on a researcher's host. Bumping the constant without updating the
    template builder (or vice versa) silently breaks the refresh
    path; this invariant catches that drift.
    """
    from vaibify.gui import conftestManager
    sExpectedStamp = (
        "# vaibify-conftest-version: "
        + conftestManager.S_CONFTEST_VERSION
    )
    sBuilt = conftestManager.fsBuildConftestSource("/x")
    assert sExpectedStamp in sBuilt, (
        "fsBuildConftestSource('/x') must embed "
        f"{sExpectedStamp!r}; otherwise the refresh helper cannot "
        "detect that an installed copy is current."
    )
    sBareTemplate = conftestManager.fsConftestContent()
    assert sExpectedStamp in sBareTemplate, (
        "fsConftestContent() must embed the version stamp too so "
        "the template shipped to /usr/share/vaibify/ stays in sync."
    )


# The conftest template body lives as a string literal inside
# ``conftestManager.py`` and is exec'd inside containers; treat it as
# exempt by file name. Documentation references that use angle-bracket
# placeholders (e.g. ``<step>.json``) are not matched by the regex
# below, so no other docstring exemption is needed.
SET_FLAT_MARKER_LITERAL_EXEMPT_FILES = {
    "conftestManager.py",
}

_REGEX_FLAT_MARKER_LITERAL = re.compile(
    r"\.vaibify/test_markers/[A-Za-z0-9_.\-]+\.json"
)


def testNoFlatTestMarkerWritesInSource():
    """No module hardcodes the flat ``.vaibify/test_markers/<step>.json`` layout.

    Markers live under ``.vaibify/test_markers/<workflowSlug>/`` so
    two workflows in the same project repo don't clobber each other.
    A literal like ``.vaibify/test_markers/step1.json`` in module
    source reintroduces the flat layout and strands markers when a
    workflow is renamed or split. ``fnMigrateFlatMarkers`` is the
    one place that intentionally walks the flat layout (to move
    legacy files into a slug subdir); it constructs paths
    dynamically, never as a string literal, so it is not caught.
    """
    pathGui = GUI_DIR
    listViolations = []
    for pathFile in pathGui.rglob("*.py"):
        if pathFile.name in SET_FLAT_MARKER_LITERAL_EXEMPT_FILES:
            continue
        sSource = fsReadSource(pathFile)
        for iLineNo, sLine in enumerate(
            sSource.splitlines(), start=1,
        ):
            if _REGEX_FLAT_MARKER_LITERAL.search(sLine):
                listViolations.append(
                    (pathFile.name, iLineNo, sLine.strip())
                )
    assert listViolations == [], (
        "Modules must not write to the flat "
        "`.vaibify/test_markers/<file>.json` layout — use the "
        "per-slug subdir instead:\n"
        + "\n".join(
            f"  {sFile}:{iLine}: {sText}"
            for sFile, iLine, sText in listViolations
        )
    )


def testEmptyCommandCategoryIsUnnecessaryAfterLoad():
    """A category with no saCommands is "unnecessary" after the full load.

    Durable regression guard for the schema bug where plot-only steps
    (or any step whose ``saCommands`` list is empty for a given test
    category) had their verification field initialized to ``untested``
    and stayed there forever, wrongly blocking the all-green gate.

    Drives the *full* load pipeline through
    ``fdictLoadWorkflowFromContainer`` — including
    ``_fnLoadAndMergeState``, the derivation hook, and the
    ``fnAttachStepLabels`` step — to prove the hook is wired into the
    load path, not just callable in isolation. A separate unit test of
    ``fbDeriveUnnecessaryVerification`` lives in ``testWorkflowManager``.
    """
    import json
    from unittest.mock import MagicMock
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
    )
    dictWorkflowOnDisk = {
        "iWorkflowSchemaVersion": 3,
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Plot Only",
            "sDirectory": "plotOnly",
            "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["fig.pdf"],
            "dictTests": {
                "dictIntegrity": {"saCommands": [], "sFilePath": ""},
                "dictQualitative": {
                    "saCommands": [], "sFilePath": "",
                },
                "dictQuantitative": {
                    "saCommands": [], "sFilePath": "",
                    "sStandardsPath": "",
                },
            },
        }],
    }
    dictPersistedState = {
        "iStateSchemaVersion": 1,
        "bArchiveTrackingMigrated": True,
        "dictStepState": {
            "plotOnly": {
                "dictVerification": {
                    "sUnitTest": "untested",
                    "sIntegrity": "untested",
                    "sQualitative": "untested",
                    "sQuantitative": "untested",
                },
            },
        },
    }
    mockDocker = MagicMock()

    def _fFetch(sContainerId, sPath):
        if sPath.endswith(".vaibify/workflows/w.json"):
            return json.dumps(dictWorkflowOnDisk).encode("utf-8")
        if sPath.endswith(".vaibify/state.json"):
            return json.dumps(dictPersistedState).encode("utf-8")
        if sPath.endswith(".vaibify/.gitignore"):
            return b"state.json\n"
        raise FileNotFoundError(sPath)

    mockDocker.fbaFetchFile.side_effect = _fFetch
    mockDocker.fnWriteFile.side_effect = lambda *a, **k: None
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictLoaded = fdictLoadWorkflowFromContainer(
        mockDocker, "cid",
        sWorkflowPath="/workspace/Project/.vaibify/workflows/w.json",
    )
    dictVerify = dictLoaded["listSteps"][0]["dictVerification"]
    for sKey in (
        "sUnitTest", "sIntegrity", "sQualitative", "sQuantitative",
    ):
        assert dictVerify[sKey] == "unnecessary", (
            f"{sKey} stayed {dictVerify[sKey]} — the load pipeline "
            "must wire fbDeriveUnnecessaryVerification so empty-commands "
            "categories surface as 'unnecessary' (green) instead of "
            "'untested' (blocking)."
        )


def testAtLeastLevel1IffAllFourCriteria():
    """``fbAtLeastLevel1`` is True iff every L1 criterion holds.

    Enumerates the 2^4 truth table over the four orthogonal
    criteria (repo present, user approved, timing clean, tests
    passing) and asserts the gate fires exactly when all four are
    True. Catches future regressions where someone weakens one
    predicate or adds a fifth without updating the composition.
    """
    from vaibify.reproducibility.levelGates import fbAtLeastLevel1
    listCriteria = (
        "bRepo", "bUser", "bTiming", "bTests",
    )
    for iMask in range(1 << len(listCriteria)):
        dictFlags = {
            sName: bool(iMask & (1 << iBit))
            for iBit, sName in enumerate(listCriteria)
        }
        dictVerification = {
            "sUser": "passed" if dictFlags["bUser"] else "untested",
        }
        if not dictFlags["bTiming"]:
            dictVerification["bUpstreamModified"] = True
        if not dictFlags["bTests"]:
            dictVerification["sUnitTest"] = "failed"
        dictWorkflow = {"listSteps": [{
            "sName": "A", "sDirectory": "A",
            "dictVerification": dictVerification,
        }]}
        sRepo = "/workspace/repo" if dictFlags["bRepo"] else ""
        bExpected = all(dictFlags.values())
        bActual = fbAtLeastLevel1(dictWorkflow, sRepo)
        assert bActual is bExpected, (
            f"flags={dictFlags} expected={bExpected} actual={bActual}"
        )


def _fnSeedHashStaleStep(tmp_path, sUnitTestState):
    """Set up a single-step workflow with matching mtime + drifted content."""
    import os
    from vaibify.gui import mtimeCache
    sStepDir = tmp_path / "step1"
    sStepDir.mkdir()
    sBaselinePath = tmp_path / "baseline.json"
    sBaselinePath.write_text("baseline-bytes")
    sBaselineSha = mtimeCache.fsBlobShaForFile(
        str(tmp_path), "baseline.json", {},
    )
    sLivePath = sStepDir / "out.json"
    sLivePath.write_text("drifted-bytes")
    fSharedMtime = 1_700_000_000.0
    os.utime(str(sLivePath), (fSharedMtime, fSharedMtime))
    os.utime(str(sBaselinePath), (fSharedMtime, fSharedMtime))
    dictWorkflow = {
        "sPath": "/workspace/repo/.vaibify/workflows/main.json",
        "sProjectRepoPath": str(tmp_path),
        "listSteps": [{
            "sLabel": "A01",
            "sDirectory": "step1",
            "saDataFiles": ["out.json"],
            "dictVerification": {
                "sUnitTest": sUnitTestState,
                "sIntegrity": sUnitTestState,
                "sQualitative": sUnitTestState,
                "sQuantitative": sUnitTestState,
            },
        }],
    }
    dictMarker = {
        "sDirectory": "step1",
        "sLabel": "A01",
        "iExitStatus": 0,
        "dictOutputHashes": {"step1/out.json": sBaselineSha},
    }
    return dictWorkflow, dictMarker, str(sLivePath), fSharedMtime


def testHashCheckRunsRegardlessOfMtime(tmp_path):
    """Hash drift must invalidate even when output mtime matches baseline.

    Constructs a step whose ``out.json`` retains a baseline mtime (the
    failure mode created by ``shutil.copy2``) but whose content diverges
    from the marker's recorded blob SHA. After one poll cycle, all four
    test axes must drop to ``untested``.
    """
    from vaibify.gui.fileStatusManager import _flistDetectAndInvalidate

    class _FakeDocker:
        def ftResultExecuteCommand(self, sId, sCmd):
            return (1, "")

    def _fnSave(sId, dictWf):
        return

    dictWorkflow, dictMarker, sLivePath, fMtime = _fnSeedHashStaleStep(
        tmp_path, "passed-from-marker",
    )
    sMtime = str(int(fMtime))
    dictNewModTimes = {sLivePath: sMtime}
    dictCtx = {
        "docker": _FakeDocker(),
        "save": _fnSave,
        "dictPreviousModTimes": {"cid": {sLivePath: sMtime}},
    }
    _flistDetectAndInvalidate(
        dictCtx, "cid", dictWorkflow, dictNewModTimes,
        dictVars={"sRepoRoot": str(tmp_path)},
        dictMarkersByStep={0: dictMarker},
        dictCache={},
    )
    dictVerify = dictWorkflow["listSteps"][0]["dictVerification"]
    for sKey in (
        "sUnitTest", "sIntegrity", "sQualitative", "sQuantitative",
    ):
        assert dictVerify[sKey] == "untested", (
            f"axis {sKey} should have been invalidated; "
            f"got {dictVerify[sKey]}"
        )


def _fnSeedPlotCoverageFiles(tmp_path):
    """Lay down step1/Plot/fig.pdf and step1/data/out.csv under ``tmp_path``."""
    sStepDir = tmp_path / "step1"
    (sStepDir / "Plot").mkdir(parents=True)
    (sStepDir / "data").mkdir()
    (sStepDir / "Plot" / "fig.pdf").write_text("fig")
    (sStepDir / "data" / "out.csv").write_text("csv")
    return sStepDir


def _fnWritePlotCoverageWorkflow(tmp_path):
    """Write a workflow.json mixing literal + templated outputs under ``tmp_path``."""
    import json as jsonModule
    sWorkflowsDir = tmp_path / ".vaibify" / "workflows"
    sWorkflowsDir.mkdir(parents=True)
    (sWorkflowsDir / "main.json").write_text(jsonModule.dumps({
        "listSteps": [{
            "sDirectory": "step1",
            "saDataFiles": ["data/out.csv", "data/{iteration}.csv"],
            "saPlotFiles": ["Plot/fig.pdf"],
        }],
    }))


def _fdictComputePlotCoverageHashes(tmp_path, sStepDir):
    """Execute the conftest plugin's hasher against ``sStepDir`` and return its dict."""
    from vaibify.gui import conftestManager
    sSource = conftestManager.fsBuildConftestSource(str(tmp_path))
    dictNs = {}
    exec(compile(sSource, "<template>", "exec"), dictNs)
    return dictNs["_fdictComputeOutputHashes"](str(sStepDir))


def testMarkerCoversAllDeclaredOutputs(tmp_path):
    """Every literal saDataFiles / saPlotFiles entry hashes into the marker."""
    sStepDir = _fnSeedPlotCoverageFiles(tmp_path)
    _fnWritePlotCoverageWorkflow(tmp_path)
    dictHashes = _fdictComputePlotCoverageHashes(tmp_path, sStepDir)
    assert "step1/data/out.csv" in dictHashes
    assert "step1/Plot/fig.pdf" in dictHashes
    for sPath in dictHashes:
        assert "{" not in sPath, (
            f"templated path {sPath} leaked into marker hashes"
        )
