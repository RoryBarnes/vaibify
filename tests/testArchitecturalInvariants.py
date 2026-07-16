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
    "testNoRootUserInDispatcherCalls",
    "testFnWriteFileDefaultsToContainerUserOwnership",
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
    "testNoDirectTruthClaimWrites",
    "testEmptyCommandCategoryIsUnnecessaryAfterLoad",
    "testAtLeastLevel1IffAllFourCriteria",
    "testHashCheckRunsRegardlessOfMtime",
    "testMarkerCoversAllDeclaredOutputs",
    "testTemplateCommandsUseStepTokens",
    "testStepCountCapEnforcedOnAddRoutes",
    "testClaimRejectsForeignLease",
    "testReleaseRejectsNonOwner",
    "testWebSocketGatesUseSharedAuthorizationGuard",
    "testLockPayloadCarriesStartedIso",
    "testSetAllowedContainersRemoved",
    "testKeepAliveDirectoryChmod700",
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


_T_LEAF_MODULE_NAMES = ("pipelineUtils.py", "truthDerivation.py")


def testLeafModuleHasNoIntraPackageImports():
    """Designated leaf modules must not import from the vaibify package.

    ``pipelineUtils.py`` and ``truthDerivation.py`` are deliberate
    leaf modules — they break circular dependency cycles and ensure
    the canonical truth-derivation home stays composable from
    anywhere in the package graph.
    """
    for sLeafName in _T_LEAF_MODULE_NAMES:
        sPath = GUI_DIR / sLeafName
        _, treeAst = ftParseFile(sPath)
        listImports = flistExtractImports(treeAst)
        listViolations = [
            (sName, iLine) for sName, iLine in listImports
            if sName.startswith("vaibify") or sName.startswith(".")
        ]
        assert listViolations == [], (
            f"{sLeafName} must be a leaf module but imports: "
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
    "fdictProbePushOutcome",
    "fdictRemoteHeadsInContainer",
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


def _flistWorkspaceKwargViolations(sPath):
    """Return (name, line) containerGit calls missing sWorkspace=."""
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
    return listViolations


def testGitRoutesAlwaysPassProjectRepoToContainerGit():
    """Every containerGit.* route call passes sWorkspace explicitly.

    The workspace default is ``/workspace`` (a Docker-managed volume
    that is not itself a git work tree). Routes must resolve the
    active workflow's project repo and forward it explicitly — a
    silent fallback to the default would reintroduce the all-grey
    badge bug where every request runs git against a non-repo path.
    ``syncRoutes.py`` is scanned alongside ``gitRoutes.py`` because
    its push-hardening helpers also call containerGit.
    """
    listAllViolations = []
    for sFileName in ("gitRoutes.py", "syncRoutes.py"):
        for sAttr, iLine in _flistWorkspaceKwargViolations(
            ROUTES_DIR / sFileName,
        ):
            listAllViolations.append((sFileName, sAttr, iLine))
    assert listAllViolations == [], (
        "Route modules must pass sWorkspace=<project-repo> to every "
        "containerGit.* call; relying on the default reintroduces the "
        "/workspace-as-repo bug:\n"
        + "\n".join(
            f"  {sFile}: {sAttr}() on line {iLine}"
            for sFile, sAttr, iLine in listAllViolations
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


_SET_DISPATCHER_METHOD_NAMES = frozenset({
    "texecRunInContainerStreamed",
    "texecRunInContainerStreamedWithChunks",
    "ftResultExecuteCommand",
    "fsExecCreate",
})

_SET_ROOT_USER_LITERALS = frozenset({"root", "0"})


def _fbCallNamesDispatcherMethod(nodeCall):
    """Return True when nodeCall is ``something.<dispatcher>(...)``.

    Only attribute-style calls qualify; bare-name calls cannot reach
    the dispatcher because it lives on a DockerConnection instance.
    """
    if not isinstance(nodeCall.func, ast.Attribute):
        return False
    return nodeCall.func.attr in _SET_DISPATCHER_METHOD_NAMES


def _fsExtractRootLiteralFromKwargs(nodeCall):
    """Return the literal ``"root"``/``"0"`` passed via sUser=, else ``""``.

    Catches the realistic regression shape (``call(..., sUser="root")``).
    Variable-indirection (``s = "root"; call(sUser=s)``) and dict-spread
    forms are intentionally out of scope — neither has ever appeared in
    vaibify source and the resulting false negative is far less likely
    than the literal-kwarg case.
    """
    for nodeKeyword in nodeCall.keywords:
        if nodeKeyword.arg != "sUser":
            continue
        nodeValue = nodeKeyword.value
        if not isinstance(nodeValue, ast.Constant):
            continue
        if not isinstance(nodeValue.value, str):
            continue
        if nodeValue.value in _SET_ROOT_USER_LITERALS:
            return nodeValue.value
    return ""


def testNoRootUserInDispatcherCalls():
    """Docker-exec dispatcher calls must not opt into root via sUser=.

    Container exec defaults to the image's unprivileged ``USER``
    directive (pinned in ``docker/Dockerfile``); the dispatcher
    methods on ``DockerConnection`` respect that default when ``sUser``
    is ``None``. Passing ``sUser="root"`` (or ``"0"``) re-elevates a
    single call and creates root-owned files in the workspace volume —
    which then block the in-container agent's unprivileged writes
    (e.g. a researcher's ``git push`` cannot append to a
    ``.git/objects/<prefix>`` touched by the elevated call, since
    ``sudo`` was deliberately removed in commit 426f6b7).

    If a future feature genuinely needs root, fix the entrypoint root
    phase or extend ``fnMigrateWorkspaceOwnership`` — do not punch a
    hole at the runtime-exec layer.

    ``dockerConnection.py`` itself is exempt: its docstrings reference
    ``"root"`` as part of the documented opt-in contract.
    """
    pathVaibify = REPO_ROOT / "vaibify"
    listOffenders = []
    for pathFile in pathVaibify.rglob("*.py"):
        if _fbIsExcludedScanPath(pathFile):
            continue
        if pathFile.name == "dockerConnection.py":
            continue
        try:
            _, treeAst = ftParseFile(pathFile)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for nodeCall in ast.walk(treeAst):
            if not isinstance(nodeCall, ast.Call):
                continue
            if not _fbCallNamesDispatcherMethod(nodeCall):
                continue
            sLiteral = _fsExtractRootLiteralFromKwargs(nodeCall)
            if sLiteral:
                listOffenders.append(
                    (pathFile.relative_to(REPO_ROOT),
                     nodeCall.lineno, sLiteral)
                )
    assert listOffenders == [], (
        "Docker-exec dispatcher calls must not pass sUser=\"root\" or "
        "sUser=\"0\". A root-elevated exec creates root-owned files "
        "in the workspace volume that block subsequent unprivileged "
        "writes (e.g. a researcher's git push). If a feature "
        "genuinely needs root, fix the entrypoint root phase, do not "
        "bypass via runtime exec.\n"
        + "\n".join(
            f"  {pathRel}:{iLine}: sUser={sLit!r}"
            for pathRel, iLine, sLit in listOffenders
        )
    )


def testFnWriteFileDefaultsToContainerUserOwnership():
    """Backend tar writes must default to the unprivileged container user.

    ``_finfoBuildTarEntry`` builds the ``TarInfo`` that
    ``container.put_archive`` materialises inside the container.
    ``tarfile.TarInfo`` natively defaults ``uid``/``gid`` to 0; if that
    default leaks through, every file written by the host backend lands
    root-owned and the in-container agent (no sudo by design — commit
    426f6b7) cannot edit it. Locks the safe default in place so a
    future refactor cannot silently regress to the tarfile default.

    Pair with ``testContainerUserUidIsOneThousand``: that test pins the
    Dockerfile's user UID to 1000; this test pins the dispatcher's
    default to the same value.
    """
    from vaibify.docker.dockerConnection import DockerConnection
    infoTarDefault = DockerConnection._finfoBuildTarEntry(
        "test.json", iSize=0, iMode=None, iUid=None, iGid=None,
    )
    assert infoTarDefault.uid == 1000, (
        f"default tar uid must be the unprivileged container user "
        f"(1000); got {infoTarDefault.uid}. A non-1000 default lands "
        f"backend-written files unreadable/uneditable by the "
        f"in-container agent."
    )
    assert infoTarDefault.gid == 1000, (
        f"default tar gid must be the unprivileged container group "
        f"(1000); got {infoTarDefault.gid}."
    )
    infoTarOverride = DockerConnection._finfoBuildTarEntry(
        "secret.env", iSize=0, iMode=0o600, iUid=0, iGid=0,
    )
    assert infoTarOverride.uid == 0 and infoTarOverride.gid == 0, (
        "explicit iUid=0/iGid=0 must still pass through — the secret "
        "writer relies on the override path."
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
    assert "fbHeartbeatIsStale" in sPipelineStateSource, (
        "pipelineState.fdictReadReconciledState must call "
        "fbHeartbeatIsStale to reconcile a vanished runner; without "
        "this branch the always-on watchdog cannot flip bRunning."
    )
    sPipelineRoutesSource = fsReadSource(
        ROUTES_DIR / "pipelineRoutes.py",
    )
    assert "fdictReadReconciledState" in sPipelineRoutesSource, (
        "pipelineRoutes.fnGetPipelineState must delegate to "
        "pipelineState.fdictReadReconciledState so the /state endpoint "
        "and every other state reader share one reconciliation path."
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
    # Step-level raw-input declaration (Input Data block). Inputs are
    # consumed, not produced, so they never belong in _OUTPUT_KEYS.
    "saInputDataFiles",
    # stepRoutes decorates the response with a resolved view of the
    # step's outputs; this is a runtime projection, not a declaration.
    "saResolvedOutputFiles",
    # Historical key names that survive only inside workflowMigrations
    # (pre-v8 documents used saDataFiles for output data and
    # saOutputFiles as a legacy general-outputs bucket; the v7->v8
    # migrator merges both into saOutputDataFiles). Migration code must
    # keep reading the old names; the manifest never sees them.
    "saDataFiles",
    "saOutputFiles",
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


# Truth-claim axis keys whose literal assignments must route through
# ``truthDerivation``. Future L2/L3 truths extend this set with one line
# so a new axis becomes invariant-protected the moment its key is added.
SET_TRUTH_CLAIM_AXIS_KEYS = frozenset({
    "sUnitTest",
    "sIntegrity",
    "sQualitative",
    "sQuantitative",
})

# String literals that constitute a truth claim. ``"untested"`` and
# ``"unnecessary"`` are state-machine values, not truth claims, and are
# intentionally absent.
SET_TRUTH_CLAIM_LITERALS = frozenset({
    "passed",
    "passed-from-marker",
    "failed",
})

# Files exempt from the invariant. Only the canonical writer itself is
# allowed to assign these literals to a truth-claim axis.
SET_TRUTH_DERIVATION_EXEMPT_FILES = frozenset({
    "truthDerivation.py",
})


def _flistFindTruthClaimViolations(pathFile, sSource):
    """Return ``[(iLineNo, sKey, sLiteral), ...]`` for one source file."""
    treeAst = ast.parse(sSource, filename=str(pathFile))
    listViolations = []
    for node in ast.walk(treeAst):
        if not isinstance(node, ast.Assign):
            continue
        if not _fbAssignsLiteral(node, SET_TRUTH_CLAIM_LITERALS):
            continue
        for sKey in _flistAssignedAxisKeys(node):
            listViolations.append(
                (node.lineno, sKey, _fsExtractLiteralValue(node.value)),
            )
    return listViolations


def _fbAssignsLiteral(nodeAssign, setLiterals):
    """Return True iff the assignment's RHS is one of the watched string literals."""
    sValue = _fsExtractLiteralValue(nodeAssign.value)
    return sValue in setLiterals


def _fsExtractLiteralValue(nodeValue):
    """Return the string literal value of ``nodeValue`` or '' for non-literals."""
    if isinstance(nodeValue, ast.Constant) and isinstance(
        nodeValue.value, str,
    ):
        return nodeValue.value
    return ""


def _flistAssignedAxisKeys(nodeAssign):
    """Return the set of truth-claim axis keys this assignment writes to."""
    listKeys = []
    for nodeTarget in nodeAssign.targets:
        sKey = _fsSubscriptKey(nodeTarget)
        if sKey in SET_TRUTH_CLAIM_AXIS_KEYS:
            listKeys.append(sKey)
    return listKeys


def _fsSubscriptKey(nodeTarget):
    """Return the string key for ``dict["key"]`` or '' for any other shape."""
    if not isinstance(nodeTarget, ast.Subscript):
        return ""
    nodeSlice = nodeTarget.slice
    if isinstance(nodeSlice, ast.Constant) and isinstance(
        nodeSlice.value, str,
    ):
        return nodeSlice.value
    return ""


def testNoDirectTruthClaimWrites():
    """Truth-claim axes are written only by the canonical truth-derivation module.

    The dashboard's ground truth — whether a step's tests passed, its
    integrity check held, its qualitative/quantitative criteria
    satisfied — must always be derived from observation, never
    asserted by a producer. Direct literal assignments of
    ``"passed"``, ``"passed-from-marker"``, or ``"failed"`` to a
    truth-claim axis key bypass the canonical derivation and let
    a producer claim a truth it cannot observe. ``"untested"`` and
    ``"unnecessary"`` are state-machine values and remain allowed at
    their original sites.

    A future L2/L3 PR extends ``SET_TRUTH_CLAIM_AXIS_KEYS`` with the
    new key (e.g. ``"sGithubSync"``) and the invariant immediately
    protects it; no further test scaffolding is required.
    """
    pathGui = GUI_DIR
    listViolations = []
    for pathFile in pathGui.rglob("*.py"):
        if pathFile.name in SET_TRUTH_DERIVATION_EXEMPT_FILES:
            continue
        sSource = fsReadSource(pathFile)
        for iLineNo, sKey, sLiteral in _flistFindTruthClaimViolations(
            pathFile, sSource,
        ):
            listViolations.append(
                (pathFile.name, iLineNo, sKey, sLiteral),
            )
    assert listViolations == [], (
        "Truth-claim axis writes must go through "
        "``truthDerivation`` so the dashboard reflects observation "
        "not assertion:\n"
        + "\n".join(
            f"  {sFile}:{iLine}: {sKey!r} = {sLit!r}"
            for sFile, iLine, sKey, sLit in listViolations
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
            "saOutputDataFiles": ["out.json"],
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
            "saOutputDataFiles": ["data/out.csv", "data/{iteration}.csv"],
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
    """Every literal saOutputDataFiles / saPlotFiles entry hashes into the marker."""
    sStepDir = _fnSeedPlotCoverageFiles(tmp_path)
    _fnWritePlotCoverageWorkflow(tmp_path)
    dictHashes = _fdictComputePlotCoverageHashes(tmp_path, sStepDir)
    assert "step1/data/out.csv" in dictHashes
    assert "step1/Plot/fig.pdf" in dictHashes
    for sPath in dictHashes:
        assert "{" not in sPath, (
            f"templated path {sPath} leaked into marker hashes"
        )


_TEMPLATES_DIR = REPO_ROOT / "templates"

# Extensions that signal a token is a file path argument.
_T_PATH_EXTENSIONS = (
    ".json", ".npy", ".csv", ".txt", ".pdf", ".png", ".npz",
    ".jpg", ".jpeg", ".svg", ".h5", ".hdf5", ".nc",
)


def _fbLooksLikeFilePath(sToken):
    """Return True when a command argument resembles a file path."""
    if not sToken or sToken.startswith("-"):
        return False
    if "/" in sToken:
        return True
    sLower = sToken.lower()
    return any(sLower.endswith(sExt) for sExt in _T_PATH_EXTENSIONS)


def _fbPathIsTokenised(sToken, sStepDirectory):
    """Return True when a path argument is wrapped in a known substitution."""
    if "{Step" in sToken or "{sPlotDirectory" in sToken:
        return True
    if "{sFigureType" in sToken:
        return True
    if sStepDirectory and sToken.split("/", 1)[0] == sStepDirectory:
        return True
    return not ("/" in sToken)


def _flistScanCommandForHardcodedPaths(sCommand, sStepDirectory):
    """Return tokens in sCommand that look like un-tokenised cross-step paths."""
    listOffending = []
    for sToken in sCommand.split():
        if not _fbLooksLikeFilePath(sToken):
            continue
        if _fbPathIsTokenised(sToken, sStepDirectory):
            continue
        listOffending.append(sToken)
    return listOffending


def _flistCollectTemplateWorkflows():
    """Return every Project template file under templates/."""
    return sorted(_TEMPLATES_DIR.rglob("project.json"))


def _flistFindTemplateViolations(pathWorkflow):
    """Return (sStepName, sField, sCommand, sToken) tuples for one template."""
    import json as jsonModule
    listViolations = []
    dictWorkflow = jsonModule.loads(pathWorkflow.read_text())
    for dictStep in dictWorkflow.get("listSteps", []):
        sStepDirectory = dictStep.get("sDirectory", "")
        for sField in ("saDataCommands", "saPlotCommands"):
            for sCommand in dictStep.get(sField, []):
                for sToken in _flistScanCommandForHardcodedPaths(
                    sCommand, sStepDirectory,
                ):
                    listViolations.append(
                        (dictStep.get("sName", ""), sField, sCommand, sToken),
                    )
    return listViolations


def testTemplateCommandsUseStepTokens():
    """Vaibify-shipped templates only reference paths via {StepNN.*} tokens.

    The dashboard's dependency parser only sees `{StepNN.varname}`
    tokens; hardcoded cross-step paths break the AICS Level 1
    contract. Enforce the doctrine on every workflow.json under
    `vaibify/templates/`.
    """
    listAllViolations = []
    for pathWorkflow in _flistCollectTemplateWorkflows():
        for tEntry in _flistFindTemplateViolations(pathWorkflow):
            listAllViolations.append((pathWorkflow, *tEntry))
    assert listAllViolations == [], (
        "Hardcoded cross-step paths found in vaibify templates:\n"
        + "\n".join(
            f"  {pathWorkflow.relative_to(REPO_ROOT)} "
            f"[step={sStepName} field={sField}]: "
            f"command={sCommand!r} offending={sToken!r}"
            for pathWorkflow, sStepName, sField, sCommand, sToken
            in listAllViolations
        )
    )


def testTemplateCommandsUseSymbolicNotPositionalTokens():
    """Shipped templates use the canonical ``{step:<id>.stem}`` form.

    Positional ``{StepNN.stem}`` tokens are deprecated (they renumber
    on any insert/reorder — the reorder-drops-a-step hazard). Templates
    are seeds for new workflows, so they must ship in the canonical
    symbolic form. Any step referenced symbolically must also carry the
    ``sStepId`` its token names.
    """
    import json as jsonModule
    import re as reModule
    listViolations = []
    for pathWorkflow in _flistCollectTemplateWorkflows():
        dictWorkflow = jsonModule.loads(pathWorkflow.read_text())
        setDeclaredIds = {
            dictStep.get("sStepId")
            for dictStep in dictWorkflow.get("listSteps", [])
        }
        for dictStep in dictWorkflow.get("listSteps", []):
            for sField in ("saDataCommands", "saPlotCommands",
                           "saTestCommands", "saDependencies"):
                for sCommand in dictStep.get(sField, []):
                    if reModule.search(r"\{Step\d+\.", sCommand):
                        listViolations.append(
                            (pathWorkflow, "positional-token", sCommand),
                        )
                    for sId in reModule.findall(
                        r"\{step:([a-z0-9][a-z0-9-]*)\.", sCommand,
                    ):
                        if sId not in setDeclaredIds:
                            listViolations.append(
                                (pathWorkflow, "unknown-id:" + sId, sCommand),
                            )
    assert listViolations == [], (
        "Deprecated positional or dangling symbolic tokens in "
        "templates:\n" + "\n".join(
            f"  {p.relative_to(REPO_ROOT)} [{sWhy}]: {sCmd!r}"
            for p, sWhy, sCmd in listViolations
        )
    )


# ---------------------------------------------------------------------------
# Reproducibility IO goes through the repo-file adapter, never a raw
# container path string (the host cannot read container files).
# ---------------------------------------------------------------------------

SET_REPRO_FILES_ENTRY_POINTS = frozenset({
    "fiAICSLevel", "fbAtLeastLevel1", "fbAtLeastLevel2",
    "fbAtLeastLevel3", "fbL3ReadinessOK", "fdictL3ReadinessGaps",
    "fdictLevel2Gaps", "flistLevel1Blockers", "flistLevel2Blockers",
    "flistLevel3Blockers",
    "fnWriteManifest", "flistVerifyManifest",
    "flistDeclaredButMissingFromManifest", "flistParseManifestLines",
    "fiCountManifestEntries",
    "fnGenerateRequirementsLock", "flistVerifyRequirementsLock",
    "fbDockerfilePresent", "flistLintDockerfile",
    "fdictReadEnvironmentJson", "fnWriteEnvironmentJson",
    "fbEnvironmentDigestPinned", "fdictCaptureSystemTools",
    "fdictCaptureHostBinaryHashes", "fdictCaptureSingleBinary",
    "fdictReadAttestation", "fnWriteAttestation",
    "fnInvalidateAttestation", "flistReadAttestationHistory",
    "fsCurrentManifestDigest", "fbL3AttestationCurrent",
    "fdictReadCachedSyncStatus", "fnWriteSyncStatus",
    "fdictVerifyRemoteService", "fdictLoadManifestExpectedHashes",
    "fnGenerateReproducibilityEnvelope",
    "fbManifestExists", "fsetStaleOutputsAgainstManifest",
    "fbDeclarationFileExists", "fnWriteDeclarationTemplate",
    "fdictClassifyFalsificationApplicability",
    "fdictBuildFalsificationStatus",
    "fdictReadFalsificationRecord", "fnWriteFalsificationRecord",
    "fbFalsificationRecordCurrent", "fsCurrentFalsificationDigest",
})

SET_RAW_REPO_PATH_NAMES = frozenset({
    "sProjectRepo", "sProjectRepoPath", "sRepo", "sRepoRoot",
    "sRepoPath",
})

# director.py is the host-side parallel runner (host paths are its
# truth), so its raw host-path arguments into the dual-accept entry
# points are correct as written.
SET_REPRO_IO_EXEMPT_FILES = frozenset({
    "director.py",
})


def _fsCalledFunctionName(nodeCall):
    """Return the simple name a Call invokes, or empty string."""
    if isinstance(nodeCall.func, ast.Name):
        return nodeCall.func.id
    if isinstance(nodeCall.func, ast.Attribute):
        return nodeCall.func.attr
    return ""


def _fbArgIsRawRepoPath(nodeArg):
    """Return True iff an argument is a bare raw-repo-path expression."""
    if isinstance(nodeArg, ast.Name):
        return nodeArg.id in SET_RAW_REPO_PATH_NAMES
    if isinstance(nodeArg, ast.Subscript):
        return (
            isinstance(nodeArg.slice, ast.Constant)
            and nodeArg.slice.value == "sProjectRepoPath"
        )
    bIsGetCall = (
        isinstance(nodeArg, ast.Call)
        and isinstance(nodeArg.func, ast.Attribute)
        and nodeArg.func.attr == "get"
        and nodeArg.args
        and isinstance(nodeArg.args[0], ast.Constant)
        and nodeArg.args[0].value == "sProjectRepoPath"
    )
    return bIsGetCall


def _flistRawRepoPathViolations(sPath):
    """Return (function, line) pairs passing raw paths into repro IO."""
    _, treeAst = ftParseFile(sPath)
    listViolations = []
    for node in ast.walk(treeAst):
        if not isinstance(node, ast.Call):
            continue
        sName = _fsCalledFunctionName(node)
        if sName not in SET_REPRO_FILES_ENTRY_POINTS:
            continue
        for nodeArg in list(node.args) + [kw.value for kw in node.keywords]:
            if _fbArgIsRawRepoPath(nodeArg):
                listViolations.append((sName, node.lineno))
    return listViolations


def testGuiNeverPassesRawRepoPathToReproducibilityIO():
    """GUI callers hand reproducibility IO an adapter, not a path string.

    ``sProjectRepoPath`` is a *container* path. A raw string handed to
    a reproducibility entry point wraps into a host adapter that probes
    the host filesystem at a container path — every conjunct then fails
    conservatively forever (the dirty-banner bug class). Production
    callers must pass ``dictCtx.files(sContainerId)``, the poll
    snapshot, or another ``repoFiles`` adapter.
    """
    listAllViolations = []
    for pathModule in sorted(GUI_DIR.rglob("*.py")):
        if pathModule.name in SET_REPRO_IO_EXEMPT_FILES:
            continue
        for tEntry in _flistRawRepoPathViolations(pathModule):
            listAllViolations.append((pathModule, *tEntry))
    assert listAllViolations == [], (
        "Raw repo-path strings passed to reproducibility IO in "
        "vaibify/gui (pass a repoFiles adapter instead):\n"
        + "\n".join(
            f"  {pathModule.relative_to(REPO_ROOT)}:{iLine} {sName}()"
            for pathModule, sName, iLine in listAllViolations
        )
    )


def _fsExtractFunctionBody(sSource, sFunctionName):
    """Return the slice of sSource starting at sFunctionName up to the next def."""
    sMarker = f"async def {sFunctionName}"
    iStart = sSource.find(sMarker)
    if iStart < 0:
        sMarker = f"def {sFunctionName}"
        iStart = sSource.find(sMarker)
    if iStart < 0:
        return ""
    iBodyStart = iStart + len(sMarker)
    iNextAsync = sSource.find("\n    async def ", iBodyStart)
    iNextSync = sSource.find("\n    def ", iBodyStart)
    listEnds = [iEnd for iEnd in (iNextAsync, iNextSync) if iEnd > 0]
    iEnd = min(listEnds) if listEnds else len(sSource)
    return sSource[iBodyStart:iEnd]


def testStepCountCapEnforcedOnAddRoutes():
    """Both fnCreateStep and fnInsertStep must reference _I_STEP_COUNT_MAX.

    The 500-step hard cap is server-authoritative: the client UX
    check can be bypassed by a direct API call, so the routes that
    add steps must each enforce the cap. Static substring assertion
    against the source of each function body is sufficient.
    """
    sPath = GUI_DIR / "routes" / "stepRoutes.py"
    sSource = Path(sPath).read_text(encoding="utf-8")
    for sFunctionName in ("fnCreateStep", "fnInsertStep"):
        sBody = _fsExtractFunctionBody(sSource, sFunctionName)
        assert sBody, (
            f"{sFunctionName} not found in stepRoutes.py — cannot "
            f"verify the 500-step cap is enforced."
        )
        bDirect = "_I_STEP_COUNT_MAX" in sBody
        bViaHelper = "_fnRaiseIfAtStepCap" in sBody
        assert bDirect or bViaHelper, (
            f"{sFunctionName} in stepRoutes.py does not reference "
            f"_I_STEP_COUNT_MAX or _fnRaiseIfAtStepCap. The 500-step "
            f"hard cap must be enforced server-side in every "
            f"step-adding route."
        )


# ---------------------------------------------------------------------------
# Single-session owner-of-record invariants (Stage 1 access model).
#
# The two old gates -- a name-keyed host flock plus the process-global
# ``setAllowedContainers`` set -- collapse into one authority,
# ``app.state.dictContainerOwners``, keyed by a per-claim, server-minted
# lease. These tests pin the load-bearing behaviour of that model so a
# future refactor cannot silently reintroduce the claim short-circuit,
# the append-only authorization leak, a duplicated WebSocket gate, or a
# recycle-proof payload regression.
# ---------------------------------------------------------------------------

# Source modules that decide container access. None may consult a
# process-global container-id membership set; the lease-keyed owner
# record is the sole authority.
_T_ACCESS_DECISION_MODULES = (
    GUI_DIR / "webSocketAuthorization.py",
    ROUTES_DIR / "pipelineRoutes.py",
    ROUTES_DIR / "terminalRoutes.py",
)

# Every security-critical module that historically read or populated the
# old ``setAllowedContainers`` access set. (The deprecated, never-populated
# ``routeContext.py`` read-accessor has now been removed; the lease-keyed
# ``dictContainerOwners`` map is the sole access authority.)
_T_AUTHORIZATION_SOURCE_MODULES = (
    GUI_DIR / "pipelineServer.py",
    GUI_DIR / "registryRoutes.py",
    GUI_DIR / "webSocketAuthorization.py",
    ROUTES_DIR / "pipelineRoutes.py",
    ROUTES_DIR / "terminalRoutes.py",
    ROUTES_DIR / "workflowRoutes.py",
)


def _frecordSeedOwner(sLeaseId, sStartedIso=""):
    """Return an OwnerRecord whose flock handle is an in-memory payload.

    A ``StringIO`` stands in for the held flock so the ownership helpers
    can read ``sStartedIso`` without opening a real lock file.
    """
    import io
    import json as jsonModule
    from vaibify.gui.containerOwnership import OwnerRecord
    fileHandlePayload = io.StringIO(
        jsonModule.dumps({"sStartedIso": sStartedIso}),
    )
    return OwnerRecord(sLeaseId=sLeaseId, fileHandleLock=fileHandlePayload)


def _fbModuleImportsAuthorizationGuard(pathModule):
    """Return True when pathModule imports from webSocketAuthorization."""
    _, treeAst = ftParseFile(pathModule)
    for sName, _iLine in flistExtractImports(treeAst):
        if sName.endswith("webSocketAuthorization"):
            return True
    return False


def testClaimRejectsForeignLease():
    """A foreign-lease claim is arbitrated to 409, never short-circuited.

    The old registry route returned ``{bClaimed: True}`` unconditionally
    once the container was in the in-process lock dict, so a second
    same-hub tab silently succeeded. ``ftdictClaim`` must instead refuse a
    non-owner with 409 -- without leaking the owner's lease -- while
    keeping a same-lease re-claim idempotent for the reload path.
    """
    from vaibify.gui import containerOwnership
    dictOwners = {
        "Proj": _frecordSeedOwner("LEASE-A", "2026-01-02T03:04:05"),
    }
    iCodeForeign, dictForeign = containerOwnership.ftdictClaim(
        dictOwners, "Proj", "LEASE-B", iPort=8000,
    )
    assert iCodeForeign == 409, (
        "a claim presenting a foreign lease must be refused with 409, "
        "not short-circuited to success"
    )
    assert dictForeign.get("bClaimed") is False
    assert "sLeaseId" not in dictForeign, (
        "the 409 body must never echo the current owner's lease"
    )
    iCodeSame, dictSame = containerOwnership.ftdictClaim(
        dictOwners, "Proj", "LEASE-A", iPort=8000,
    )
    assert iCodeSame == 200 and dictSame["sLeaseId"] == "LEASE-A", (
        "a same-lease re-claim (the reload path) must be idempotent success"
    )
    sSource = fsReadSource(GUI_DIR / "registryRoutes.py")
    assert "ftdictClaim" in sSource and "bClaimed" not in sSource, (
        "the claim route must delegate arbitration to "
        "containerOwnership.ftdictClaim and hold no inline bClaimed "
        "short-circuit"
    )


def testReleaseRejectsNonOwner():
    """Release verifies the lease, closing the append-only authz leak.

    ``fnReleaseOwnership`` must return False and retain the record when
    the caller does not present the owning lease, so a non-owner can
    never drop another session's authorization. The old model left
    ``setAllowedContainers`` populated for the whole process lifetime;
    the lease check is what makes release honest.
    """
    from vaibify.gui import containerOwnership
    dictOwners = {"Proj": _frecordSeedOwner("LEASE-A")}
    bForeign = containerOwnership.fnReleaseOwnership(
        dictOwners, "Proj", "LEASE-B",
    )
    assert bForeign is False and "Proj" in dictOwners, (
        "a non-owner release must be rejected and must not drop the record"
    )
    bMissing = containerOwnership.fnReleaseOwnership(
        dictOwners, "Absent", "LEASE-A",
    )
    assert bMissing is False
    sSource = fsReadSource(GUI_DIR / "registryRoutes.py")
    assert "fnReleaseOwnership" in sSource and "sLeaseId" in sSource, (
        "the release route must verify the lease via "
        "containerOwnership.fnReleaseOwnership"
    )


def testWebSocketGatesUseSharedAuthorizationGuard():
    """Both WebSocket routes consult the one shared authorization guard.

    The three-step gate (loopback origin + shared token + owning lease)
    lives only in ``webSocketAuthorization``. Each WebSocket route module
    must import it rather than inline its own check, and no
    access-decision module may reference a process-global
    ``setAllowedContainers`` membership set.
    """
    for sFileName in ("pipelineRoutes.py", "terminalRoutes.py"):
        pathModule = ROUTES_DIR / sFileName
        assert _fbModuleImportsAuthorizationGuard(pathModule), (
            f"{sFileName} must import the shared guard from "
            f"webSocketAuthorization instead of inlining the gate"
        )
    listViolations = [
        pathModule.name for pathModule in _T_ACCESS_DECISION_MODULES
        if "setAllowedContainers" in fsReadSource(pathModule)
    ]
    assert listViolations == [], (
        f"access-decision modules must not consult a container-id "
        f"membership set; setAllowedContainers found in: {listViolations}"
    )
    sGuardSource = fsReadSource(GUI_DIR / "webSocketAuthorization.py")
    assert "def fbAuthorizeContainerSession" in sGuardSource, (
        "webSocketAuthorization must expose fbAuthorizeContainerSession"
    )


def testLockPayloadCarriesStartedIso():
    """Every host-registry holder payload keeps the recycle-proof field.

    ``sStartedIso`` records the holder's process start clock so a reaper
    can tell a genuinely dead holder from a recycled PID. Dropping it
    degrades every reaper to a bare ``os.kill`` liveness check. The
    container-lock builder is asserted by construction; the session and
    keep-alive registries are asserted by source so the whole family
    keeps the field.
    """
    import datetime
    from vaibify.config import containerLock
    dictPayload = containerLock._fdictBuildHolderPayload("Proj", 8000)
    for sKey in ("iPid", "iPort", "sStartedIso", "sProjectName"):
        assert sKey in dictPayload, (
            f"container-lock holder payload missing {sKey!r}; the "
            f"recycle-proof staleness contract depends on it"
        )
    datetime.datetime.fromisoformat(dictPayload["sStartedIso"])
    for sModuleName in ("sessionRegistry.py", "keepAliveManager.py"):
        sSource = fsReadSource(
            REPO_ROOT / "vaibify" / "config" / sModuleName,
        )
        assert "sStartedIso" in sSource, (
            f"{sModuleName} must write sStartedIso into its holder "
            f"payload for the recycle-proof reaper"
        )


def testSetAllowedContainersRemoved():
    """The process-global allow set is gone from every access-decision site.

    The old model authorized a WebSocket/REST call by container-id
    membership in ``setAllowedContainers`` -- a process-global set that
    was append-only (never cleared on release or disconnect) and keyed on
    the process, not the browser. The lease-keyed ``dictContainerOwners``
    map replaces it as the SOLE authority. No security-critical module
    may name the old set, and the new authority must be consulted in its
    place.
    """
    listViolations = [
        pathModule.name for pathModule in _T_AUTHORIZATION_SOURCE_MODULES
        if "setAllowedContainers" in fsReadSource(pathModule)
    ]
    assert listViolations == [], (
        f"setAllowedContainers must not appear in any access-decision "
        f"module; the lease-keyed dictContainerOwners is the single "
        f"authority. Found in: {listViolations}"
    )
    sServerSource = fsReadSource(GUI_DIR / "pipelineServer.py")
    sRegistrySource = fsReadSource(GUI_DIR / "registryRoutes.py")
    assert "dictContainerOwners" in sServerSource, (
        "pipelineServer must build and consult dictContainerOwners as "
        "the replacement authority"
    )
    assert "dictContainerOwners" in sRegistrySource, (
        "registryRoutes claim/release must operate on dictContainerOwners"
    )


def testWebSocketRoutesResolveIdToNameBeforeGate():
    """Both WS routes resolve the docker id to the canonical name first.

    The owner-of-record map is keyed by container NAME (the claim
    route's canonical key), but the WebSocket routes receive the docker
    ID in their path. Each handler must call ``fsContainerNameForId``
    before handing a name to ``fiContainerSessionRejectionCode`` and to
    the per-container live-connection counter; otherwise the name-keyed
    gate lookup misses and every authorized session closes 4403. This
    pins the resolution boundary so an id-keyed regression cannot pass
    CI silently.
    """
    for sFileName in ("pipelineRoutes.py", "terminalRoutes.py"):
        sSource = fsReadSource(ROUTES_DIR / sFileName)
        iResolve = sSource.find("fsContainerNameForId(")
        iGate = sSource.find("fiContainerSessionRejectionCode(")
        assert iResolve != -1, (
            f"{sFileName} must resolve the docker id to the canonical "
            f"name via fsContainerNameForId before gating"
        )
        assert iGate != -1 and iResolve < iGate, (
            f"{sFileName} must call fsContainerNameForId BEFORE "
            f"fiContainerSessionRejectionCode so the name-keyed gate is "
            f"consulted with the resolved name, not the raw docker id"
        )


def testPerContainerLiveConnectionCounterHasProductionDriver():
    """The per-container live-connection counter is driven from source.

    The increment/decrement pair on ``containerOwnership`` once had zero
    non-test callers, so ``iLiveConnectionCount`` stayed at zero and the
    idle reaper force-released live, owned sessions while the
    researcher's WebSocket was open. The shared serve wrapper in
    ``webSocketAuthorization`` is the single production driver; this
    test fails if it stops driving the counter.
    """
    sSource = fsReadSource(GUI_DIR / "webSocketAuthorization.py")
    assert "fnIncrementLiveConnection" in sSource, (
        "webSocketAuthorization must drive the per-container "
        "increment so the reaper sees an honest live count"
    )
    assert "fnDecrementLiveConnection" in sSource, (
        "webSocketAuthorization must drive the per-container decrement "
        "in a finally so the grace clock starts on the last disconnect"
    )


def testKeepAliveDirectoryChmod700(tmp_path):
    """The keep-alive registry creates its directory at mode 0o700.

    ``containerLock`` and ``sessionRegistry`` already chmod their dirs
    0o700; ``keepAliveManager`` historically did not (the security
    divergence noted in the refactor diagnosis). Routing its directory
    creation through ``pidFileRegistry.fnEnsureDirectory`` closes the gap
    by construction. This asserts the shared creator enforces 0o700 and
    that keepAliveManager delegates to it rather than calling
    ``os.makedirs`` directly.
    """
    import os
    from vaibify.config import pidFileRegistry
    sNestedDir = tmp_path / "caffeinate"
    pidFileRegistry.fnEnsureDirectory(str(sNestedDir))
    iMode = os.stat(str(sNestedDir)).st_mode & 0o777
    assert iMode == 0o700, (
        f"pidFileRegistry.fnEnsureDirectory must create registry dirs "
        f"at 0o700; got {oct(iMode)}"
    )
    sSource = fsReadSource(
        REPO_ROOT / "vaibify" / "config" / "keepAliveManager.py",
    )
    assert "pidFileRegistry.fnEnsureDirectory" in sSource, (
        "config/keepAliveManager must create its directory through "
        "pidFileRegistry.fnEnsureDirectory so it inherits 0o700"
    )
    assert "os.makedirs" not in sSource, (
        "config/keepAliveManager must not call os.makedirs directly; "
        "that would bypass the shared 0o700 creator"
    )


# ---------------------------------------------------------------------
# Module-size ratchet (smell-to-justify; see AGENTS.md "When to
# modularize"). Prevents a NEW god module from appearing and stops the
# existing large modules from growing, without forcing a split of a
# cohesive-but-large file today. The grandfathered numbers are known
# debt: they may go DOWN (split or trim), never up. Raising one is a
# deliberate act that should be justified, not a reflex.
# ---------------------------------------------------------------------

I_MODULE_LINE_CAP = 800

DICT_GRANDFATHERED_MODULE_LINES = {
    # +2 (2026-07-04): the pipeline WS route claims the exclusive
    # pipeline lane and closes refusals after accept (fnCloseWithCode).
    # +18 (2026-07-07): three exec-free envelope status booleans
    # (bAiDeclarationAttested / bRebuildAttestationCurrent /
    # bOverleafBound) for the Project-block requirement rows — a
    # cohesive extension of the poll-assembly responsibility.
    # +7 (2026-07-08): degenerate-envelope guard — a failed poll
    # snapshot ships null instead of an empty envelope so the client
    # never overwrites good state with "no binaries".
    # +3 (2026-07-09): the bArxivConfigured envelope boolean — the
    # arXiv L2 criteria are opt-in, keyed on the recorded connection.
    # main +3 (2026-07-09): dictMaxMtimeByStep threaded into the level
    # projection so inactive steps with outputs read "unassessed".
    # main +38 (2026-07-10): workflow-epoch reconciliation
    # (_fnReconcileWorkflowEpoch) replacing one-shot reload delivery.
    # +8 (2026-07-12): the poll now hashes declared-binary absolute
    # paths (flistWorkflowBinaryPaths threaded through the snapshot
    # fetch and the collected-mtimes union) so L3 binary-drift is
    # detected out-of-repo. Cohesive with the poll assembly it extends.
    # +7 (2026-07-12): the non-gating L1 binary-staleness warning —
    # fdictBinaryStaleByStep (binary mtime vs step-output mtime)
    # threaded through the level-state payload into the warning
    # projection. Cohesive with the poll assembly it extends.
    # −50 (2026-07-13): removed the container-activity sample /
    # toolbar busy indicator (f07685a) — its load-average threshold
    # false-positived over an idle container, misrepresenting state.
    # +4 (2026-07-14): poll payload exposes sWorkflowFingerprint (the
    # compare-and-swap baseline the frontend sends back on edits).
    # +13 (2026-07-14): poll payload surfaces dictRunState (reconciled
    # bRunning + iActiveStep) so the continuously-polled dashboard
    # reflects any dispatched run — including an in-container agent's —
    # without a separate pipeline-state poll. Cohesive with poll assembly.
    # +11 (2026-07-14): dictRunState now also carries the live
    # wall-clock-budget status (over-budget flag + elapsed/budget) so a
    # hung-but-heartbeating step is distinguishable from a legitimately
    # long one. Computed live, non-gating. Cohesive with poll assembly.
    # +11 (2026-07-16): input-data files join the poll — their paths in
    # the stat batch and dictMaxInputMtimeByStep on the wire. Cohesive
    # with the mtime groupings already assembled here.
    "routes/pipelineRoutes.py": 2257,
    # +21 (2026-07-09): removing the arXiv connection also clears its
    # cached verify result (_fsClearArxivSyncCache) so the dashboard
    # cannot render a ghost divergence count — cohesive with the
    # configure route it extends.
    # +53 (2026-07-09): Overleaf push provenance
    # (_fnRecordPushProvenance) — the push manifest + sLastPushCommit
    # write that the figure-freeze/arXiv/verify machinery reads;
    # previously never recorded in production. Cohesive with the
    # push finalize it extends.
    # +65 (2026-07-10): stage-validate-commit for the connect flow
    # (_fsFetchPreviousHostCredential + _fnRollBackFailedCredential) —
    # a failed token validation restores the previously working
    # credential instead of deleting it, and the response says which
    # happened. Cohesive with the setup flow it hardens.
    # +88 (2026-07-10): the same rollback extended to container-side
    # Zenodo tokens via an in-container snapshot slot (the value
    # never crosses the docker-exec boundary).
    # +9 (2026-07-10): the Overleaf push now calls the shared
    # fsRefreshVerifyCacheAfterPush hop ("shared by every push
    # route" — this was the missed call site), so the requirement
    # row updates without a manual re-verify.
    # +108 (2026-07-12): the pull-manuscript agent action — mirrors
    # the manuscript sources into the project repo's .vaibify/
    # manuscript/ so the read-manuscript skill reads the real paper
    # instead of hallucinating it. Cohesive with the Overleaf route
    # family it sits in.
    "routes/syncRoutes.py": 2385,
    # main +59 (2026-07-10): content-fingerprint piggyback in the
    # polling stat batch (_ftStatAndFingerprintViaPathfile) — same
    # exec, one sha256 line — feeding the reload detector.
    # +97 (2026-07-16): the input-data staleness lane — resolution and
    # collection of saInputDataFiles, full-path input invalidation,
    # the inputFile pencil bucket, and dictInputHashes drift folded
    # into the marker-hash pass. Mirrors the output lane this module
    # owns; splitting it out would smear one behavior across modules.
    "fileStatusManager.py": 2099,
    # main +35 (2026-07-10): single serialization authority
    # (_ftSplitAndSerializeWorkflow + fsComputeWorkflowFingerprint)
    # and the loader's _sSourceFingerprint stamp for byte-exact,
    # race-free self-write baselines.
    # +2 (2026-07-14): fnEnsureStepIds on the load and save paths —
    # stable sStepId identity (the primitive behind symbolic
    # cross-step references); the helper itself lives in
    # workflowMigrations.py.
    # +84 (2026-07-14): symbolic cross-step references
    # ({step:<id>.stem}) alongside the deprecated positional form —
    # fdictStepIdToIndex, symbolic resolution in the resolver /
    # registry / dependency scan / validation (with a deprecation
    # warning), all cohesive with the token machinery already here.
    # +64 (2026-07-14): the workflow dry-run (fdictResolveWorkflowCommands
    # + flistResidualStepTokens) — the graph's `make -n`, substituting
    # every command without running; cohesive with the resolver.
    # +30 (2026-07-14): ffResolveStepWallClockBudget (+ coercion helper)
    # — the step > workflow-default > none budget resolution the run
    # loop stamps onto each step start. Cohesive with the step-config
    # resolvers already here.
    # +20 (2026-07-15): Project-directory rename contract —
    # VAIBIFY_PROJECTS_DIR/S_VAIBIFY_PROJECTS_SUFFIX canonical with the
    # legacy .vaibify/workflows suffix as a dual-read fallback, so
    # discovery and repo-path derivation accept a Project file in either
    # directory. Cohesive with the on-disk contract already here.
    # +84 (2026-07-16): the input-data declaration contract —
    # saInputDataFiles/listRemoteData boundary validation
    # (_flistValidateInputDataFilePaths, _fsCheckInputPathBoundary)
    # alongside the sibling boundary checks it mirrors, plus the
    # flistStepRemoteDataPaths accessor every remote-data reader
    # shares. Cohesive with the schema this module owns.
    "workflowManager.py": 2254,
    # +44 (2026-07-04): the one-live-pipeline-action dispatch guard
    # (_fbRefuseWhilePipelineTaskLive + the runRefused event) — run
    # exclusivity enforced at dispatch for every lane, cohesive with
    # the message loop it guards.
    # +1 (2026-07-11): one registration line for falsificationRoutes
    # in _fnRegisterAllRoutes.
    # main +8 (2026-07-09): fnDispatchAction threads the active
    # workflow + cached path into every runner call and logs dispatch.
    # main +17 (2026-07-10): fingerprint-based self-write baselines at
    # connect and save, plus iWorkflowEpoch in the connect response.
    # +4 (2026-07-14): sBaseFingerprint on StepUpdateRequest — the
    # optional compare-and-swap guard for update-step (409 on a stale
    # concurrent edit).
    # +3 (2026-07-14): connect response exposes sWorkflowFingerprint so
    # the frontend has a compare-and-swap baseline to send back.
    # +11 (2026-07-14): the wall-clock-budget fields —
    # fWallClockBudgetSeconds on StepUpdateRequest,
    # fDefaultWallClockBudgetSeconds on WorkflowSettingsRequest, and the
    # settings-subset default — making the opt-in budget settable.
    # +3 (2026-07-15): connect path-validation accepts a Project file
    # under either .vaibify/projects (canonical) or .vaibify/workflows
    # (legacy) via T_VAIBIFY_PROJECT_SUFFIXES.
    # +17 (2026-07-16): input-data declaration fields on the step
    # request models (saInputDataFiles, bNoInputData, listRemoteData),
    # threading into fdictStepFromRequest, the
    # fdictCollectInputPathsByStep re-export shim line, and the
    # InputDataAddRequest model for the add-input-data-file action.
    "pipelineServer.py": 1805,
    # +5 (2026-07-02): push-staged guards the commit on "anything
    # staged?" so an already-committed repo still pushes.
    # +13 (2026-07-10): the host ls-remote validation resets ambient
    # git credential helpers (credential isolation) so it can only
    # exercise the vaibify-managed token, never a keychain entry for
    # the same host.
    # +33 (2026-07-10): fbCopyCredentialInContainer — the in-container
    # keyring snapshot/restore primitive for the connect flow's
    # stage-validate-commit; the secret never crosses the exec
    # boundary.
    "syncDispatcher.py": 1673,
    # +9 (2026-07-14): the run loop resolves each step's wall-clock
    # budget and threads it onto the stepStarted event so the state
    # writer can stamp it beside the step start time. Cohesive with the
    # per-step run orchestration it extends.
    "pipelineRunner.py": 1408,
    "dataLoaders.py": 1222,
    "introspectionScript.py": 1192,
    "testGenerator.py": 1063,
    "registryRoutes.py": 987,
}


def _fiCountFileLines(pathFile):
    """Return the number of lines in a source file."""
    with open(pathFile, "r", encoding="utf-8") as fileHandle:
        return sum(1 for _ in fileHandle)


def testModuleSizeIsBounded():
    """No new god modules; grandfathered large modules must not grow.

    A new module over the cap must be split or added to the allow-list
    with a justification; a grandfathered module that grew past its
    recorded size must be trimmed or its entry consciously updated. This
    is a smell-to-justify ratchet, not a mandate to fragment.
    """
    listOffenders = []
    for pathFile in sorted(GUI_DIR.rglob("*.py")):
        sKey = pathFile.relative_to(GUI_DIR).as_posix()
        iLines = _fiCountFileLines(pathFile)
        iAllowed = DICT_GRANDFATHERED_MODULE_LINES.get(sKey, I_MODULE_LINE_CAP)
        if iLines > iAllowed:
            listOffenders.append((sKey, iLines, iAllowed))
    assert not listOffenders, (
        "Module-size ratchet tripped (see AGENTS.md 'When to "
        "modularize'). Split the module along a real seam, or — if it is "
        "one cohesive responsibility — update its entry in "
        "DICT_GRANDFATHERED_MODULE_LINES:\n"
        + "\n".join(
            f"  {sKey}: {iLines} lines (allowed {iAllowed})"
            for sKey, iLines, iAllowed in listOffenders
        )
    )


# ---------------------------------------------------------------------
# Falsification-test convention (see AGENTS.md "Epistemics"). A
# falsification test is a kill-confirmed test: proven to FAIL when the
# guard it defends is broken, not merely to pass. Dedicated falsification
# files must mark every test with the `falsification` marker (via a
# module-level pytestmark) and record the killed mutation on a "Kills:"
# docstring line, so the kill can be re-confirmed as the code evolves.
# ---------------------------------------------------------------------

_LIST_FALSIFICATION_FILE_NAMES = [
    "testPathValidation.py",
    "testFileStatusManagerStaleness.py",
    "testServerMiddlewareCoverage.py",
    "testConftestManagerCoverage.py",
]


def _flistFalsificationFiles():
    """Return the dedicated falsification test files that exist."""
    pathTests = REPO_ROOT / "tests"
    listGlob = sorted(pathTests.glob("test*MutationCoverage.py"))
    listNamed = [pathTests / sName for sName in _LIST_FALSIFICATION_FILE_NAMES]
    return [p for p in listGlob + listNamed if p.exists()]


def _flistTestFunctions(sSource):
    """Return all test* function/method nodes in a parsed module."""
    import ast
    tree = ast.parse(sSource)
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test")
    ]


def testFalsificationFilesDeclareMarker():
    """Every dedicated falsification file marks all its tests."""
    for pathFile in _flistFalsificationFiles():
        sSource = pathFile.read_text(encoding="utf-8")
        assert "pytestmark" in sSource and "falsification" in sSource, (
            f"{pathFile.name} must declare module-level "
            "`pytestmark = pytest.mark.falsification` so every test in it "
            "is a falsification test"
        )


def testFalsificationTestsRecordTheKilledMutation():
    """Every test in a falsification file names the mutation it kills."""
    import ast
    listOffenders = []
    for pathFile in _flistFalsificationFiles():
        for node in _flistTestFunctions(pathFile.read_text(encoding="utf-8")):
            sDoc = ast.get_docstring(node) or ""
            if "Kills:" not in sDoc:
                listOffenders.append(f"{pathFile.name}::{node.name}")
    assert not listOffenders, (
        "Each falsification test must record the mutation it kills on a "
        "'Kills:' docstring line so the kill can be re-confirmed:\n  "
        + "\n  ".join(listOffenders)
    )


def testFalsificationRegistryIsWellFormed():
    """Every falsification-registry entry names a real, unique mutation site.

    Static guard (fast, runs in the suite). The dynamic kill-confirmation
    (apply the mutation, prove the test fails) lives in
    tools/reconfirmFalsification.py, which mutates source and so is run
    deliberately, not as part of `pytest tests/`.
    """
    from tests.falsificationRegistry import LIST_FALSIFICATIONS
    listOffenders = []
    setSeenNodeIds = set()
    for entry in LIST_FALSIFICATIONS:
        if entry.nodeid in setSeenNodeIds:
            listOffenders.append(f"{entry.nodeid}: duplicate nodeid")
        setSeenNodeIds.add(entry.nodeid)
        pathSource = REPO_ROOT / entry.source
        if not pathSource.exists():
            listOffenders.append(f"{entry.nodeid}: missing source {entry.source}")
            continue
        iCount = pathSource.read_text(encoding="utf-8").count(entry.old)
        if iCount != 1:
            listOffenders.append(
                f"{entry.nodeid}: 'old' occurs {iCount}x (need exactly 1) "
                f"in {entry.source}"
            )
        if entry.old == entry.new:
            listOffenders.append(f"{entry.nodeid}: old == new (no mutation)")
        sTestFile = entry.nodeid.split("::", 1)[0]
        pathTestFile = REPO_ROOT / sTestFile
        if not pathTestFile.exists():
            listOffenders.append(f"{entry.nodeid}: missing test file {sTestFile}")
            continue
        sFunction = entry.nodeid.rsplit("::", 1)[1]
        listFunctions = [
            node.name for node in
            _flistTestFunctions(pathTestFile.read_text(encoding="utf-8"))
        ]
        if sFunction not in listFunctions:
            listOffenders.append(
                f"{entry.nodeid}: test function {sFunction} not found in {sTestFile}"
            )
    assert not listOffenders, (
        "Falsification registry is malformed:\n  " + "\n  ".join(listOffenders)
    )


def _fbModuleDeclaresFalsification(tree):
    """Return True when a module-level ``pytestmark`` selects falsification."""
    import ast
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        listTargets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in listTargets
        ):
            continue
        if node.value is not None and "falsification" in ast.unparse(node.value):
            return True
    return False


def _fbNodeDecoratedFalsification(node):
    """Return True when a test function carries the falsification marker."""
    import ast
    return any(
        "falsification" in ast.unparse(decorator)
        for decorator in node.decorator_list
    )


def _flistFalsificationMarkedTests():
    """Return (relpath, function, docstring) for every falsification test.

    Covers both module-level ``pytestmark = pytest.mark.falsification`` files
    and individually ``@pytest.mark.falsification``-decorated tests in the
    interleaved tier-1 files, so the registry bijection is enforced uniformly.
    """
    import ast
    listMarked = []
    for pathFile in sorted((REPO_ROOT / "tests").glob("test*.py")):
        sSource = pathFile.read_text(encoding="utf-8")
        bModuleMarked = _fbModuleDeclaresFalsification(ast.parse(sSource))
        sRelative = str(pathFile.relative_to(REPO_ROOT))
        for node in _flistTestFunctions(sSource):
            if bModuleMarked or _fbNodeDecoratedFalsification(node):
                listMarked.append(
                    (sRelative, node.name, ast.get_docstring(node) or "")
                )
    return listMarked


def testFalsificationMarkedTestsAreRegistered():
    """Every falsification-marked test is documented and re-confirmable.

    Reverse direction of testFalsificationRegistryIsWellFormed: a test that
    carries the ``falsification`` marker (module-level or per-test) must name
    the mutation it kills on a ``Kills:`` docstring line AND have exactly one
    matching entry in LIST_FALSIFICATIONS, so it cannot silently drift out of
    the re-confirmation harness. This closes the gap for the interleaved
    tier-1 files, whose per-test markers are otherwise unenforced.
    """
    from tests.falsificationRegistry import LIST_FALSIFICATIONS
    listOffenders = []
    for sRelative, sFunction, sDocstring in _flistFalsificationMarkedTests():
        if "Kills:" not in sDocstring:
            listOffenders.append(f"{sRelative}::{sFunction}: missing 'Kills:' docstring")
        listMatches = [
            entry for entry in LIST_FALSIFICATIONS
            if entry.nodeid.split("::", 1)[0] == sRelative
            and entry.nodeid.rsplit("::", 1)[1] == sFunction
        ]
        if len(listMatches) != 1:
            listOffenders.append(
                f"{sRelative}::{sFunction}: {len(listMatches)} registry "
                "entries (need exactly 1)"
            )
    assert not listOffenders, (
        "Falsification-marked tests must each carry a 'Kills:' docstring and "
        "exactly one registry entry:\n  " + "\n  ".join(listOffenders)
    )
