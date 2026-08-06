"""Architectural invariants for the vaibify package encoded as pytest tests."""

import ast
import importlib
import re
from pathlib import Path

import pytest


__all__ = [
    "testLeafModuleHasNoIntraPackageImports",
    "testEveryRouteModuleExportsRegisterAll",
    "testAllRouteModulesRegisteredInInit",
    "testAllPackageModulesDefineDunderAll",
    "testWorkflowManagerUsesPosixPath",
    "testDirectorUsesOsPath",
    "testNoScienceSpecificIdentifiersInSource",
    "testNoScienceSpecificIdentifiersInShippedTemplates",
    "testScienceTermScanMatchesSeparatedSpellings",
    "testScienceTermScanKeepsItsLeadingWordBoundary",
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
    "testTemplateStepDirectoriesHonorTheSlugContract",
    "testTemplateCommandsNameScriptsThatExist",
    "testStepCountCapEnforcedOnAddRoutes",
    "testClaimRejectsForeignLease",
    "testReleaseRejectsNonOwner",
    "testWebSocketGatesUseSharedAuthorizationGuard",
    "testLockPayloadCarriesStartedIso",
    "testSetAllowedContainersRemoved",
    "testKeepAliveDirectoryChmod700",
]


REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "vaibify"
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
    # A step-directory name from a user's own project, which reached a
    # source comment through a live-incident postmortem rather than
    # through an example. The seed list catches mission names; nothing
    # was watching for the project vocabulary a debugging session drags
    # in, which is the likelier route now that incidents get written up
    # where they were diagnosed.
    "xuvevolution",
    # The maintainer's own tool names, which shipped in the toolkit
    # template README (caught 2026-07-29). Their one deliberate home,
    # levelGates.TUPLE_COMMON_SCIENTIFIC_BINARIES, is scoped below in
    # SET_ALLOWED_SCIENCE_TERM_SOURCE_FILES.
    "vplanet",
    "vspace",
    "multiplanet",
]

# (sTerm, repo-relative path) pairs the source scan tolerates. Each
# entry scopes ONE term to ONE file with a stated reason; the scan
# stays strict everywhere else. levelGates.py names the tool terms on
# purpose: its command-scan heuristic recognizes common scientific
# binaries to defend against false L3 waivers, and renaming them
# would change behavior.
SET_ALLOWED_SCIENCE_TERM_SOURCE_FILES = {
    ("vplanet", "vaibify/reproducibility/levelGates.py"),
    ("vspace", "vaibify/reproducibility/levelGates.py"),
    ("multiplanet", "vaibify/reproducibility/levelGates.py"),
}

# PENDING (2026-07-27): this scan covers *.py/*.html/*.js/*.css under
# vaibify/ only. Markdown is not scanned and repo-root docs/ is
# outside the scanned tree, so occurrences there are not enforced.
#
# CLEARED (2026-07-27): the documentation occurrences recorded here on
# 2026-07-26 have been genericised into one running example -- the
# projects ParameterSweep and SurveyCatalog, with steps
# PosteriorSamples and PosteriorCorner -- across
# docs/architecture.md, docs/dashboard.md, and
# vaibify/docs/scriptAuthoring.md. The docs/vision.md citation is gone
# too, but by attrition: the "Where vaibify sits" section was rewritten
# upstream and no longer names the paper, so the allow-list carve-out
# that entry called for is not needed.
#
# CLEARED (2026-07-27): the matcher now tolerates separators between a
# term's characters, and the two occurrences that motivated it are
# gone. The index.html placeholder was the interesting one: it WAS
# inside the scanned tree and WAS a scanned glob, yet passed for
# months, because the string is spaced ("GJ 1132 b ...") while the term
# is written "gj1132" -- a term list of bare identifiers could not
# match the spaced form of the same name, so the scan was blind to
# exactly the way a human types it. That was the second structural
# blindness in this one check, after the trailing \b. Both are now
# pinned by tests, not by prose:
# testScienceTermScanMatchesSeparatedSpellings and
# testScienceTermScanKeepsItsLeadingWordBoundary.
#
# Also genericised: the TOI-540 example in vaibify/gui/pipelineUtils.py
# (a real catalogue designation used to illustrate the slug contract's
# hyphen rule, which the rule can state without naming an object).
#
# STILL NOT ENFORCED:
#
#   *.md is not a scanned glob, so
#   vaibify/containerImage/skills/create-pipeline-step/SKILL.md:52
#   (example step name) is invisible to this scan.
#
#   tests/ is an excluded directory, and the fixtures there use real
#   object names freely (testStepSlugContract, testSyncRoutesCoverage,
#   testLatexAnnotation and others).
#
# CLEARED (2026-07-29): shipped templates are now enforced by their
# own lane, testNoScienceSpecificIdentifiersInShippedTemplates, which
# scans every file (markdown included) under vaibify/templates/ and
# exempts only the one shipped example workflow.
#
# To finish the job: add "*.md" to _TUPLE_SCIENCE_SCAN_GLOBS, extend the
# scan root to repo-root docs/ (which will also pick up AGENTS.md), and
# decide whether tests-of-record stay exempt. Only then does the
# AGENTS.md rule ("must not appear in vaibify source, templates,
# tests-of-record, or docs") hold everywhere it claims to.

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
    or those callers form a cycle. ``pathContract`` and
    ``pipelineUtils`` are themselves leaf modules (the latter is
    pinned as one by ``testLeafModuleHasNoIntraPackageImports``), so
    importing them cannot close a cycle; a migrator that normalizes a
    field must use the same reader production uses rather than a
    second copy of the rule. Any OTHER intra-package import here is
    almost always a sign that the migrator should pull state from its
    caller instead of reaching back into the package.
    """
    setAllowedLeaves = {".pathContract", ".pipelineUtils"}
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

# A forbidden term is listed as a run-together identifier ("gj1132"),
# but the same name is written with a separator between its parts
# wherever a human types it ("GJ 1132", "GJ-1132", "gj_1132"). Any run
# of separators may therefore sit between any two characters of a term.
_S_TERM_SEPARATOR_PATTERN = r"[\s\-_]*"


def _flistScanForTerm(pathRoot, sTerm, tupleGlobs=_TUPLE_SCIENCE_SCAN_GLOBS,
                      bSkipExcludedDirectories=True):
    """Return (pathFile, iLineNo, sLine, sMatchedToken) matches for sTerm.

    Scans user-facing source files (Python, HTML, JS, CSS) for the
    given identifier. HTML and JS coverage closes the gap left by the
    original Python-only sweep — placeholder strings, comments, and
    inline labels are the most likely vehicle for a project-specific
    name to leak into a release build.
    """
    # Anchored on a LEADING boundary only, and tolerant of separators
    # between characters. Two separate blindnesses produced that shape:
    #
    #   The trailing \b was the first: "_" and letters are word
    #   characters, so \bgj1132\b never matched GJ1132_XUV, GJ1132XUV,
    #   or KeplerFfdCorner -- i.e. every form the identifier actually
    #   takes in this repository.
    #
    #   Separator intolerance was the second: the term list holds
    #   run-together identifiers while a human types the spaced form,
    #   so a shipped placeholder reading "GJ 1132 b ..." sat inside the
    #   scanned tree, inside a scanned glob, and passed for months --
    #   exactly the leak this docstring says the scan exists to catch.
    #
    # The leading boundary stays: without it "proxima" matches inside
    # "approximation". Absence of a trailing boundary is equally
    # deliberate -- a suffixed form is still the same identifier.
    regexTerm = re.compile(
        r"\b" + _S_TERM_SEPARATOR_PATTERN.join(
            re.escape(sCharacter) for sCharacter in sTerm
        ),
        re.IGNORECASE,
    )
    listHits = []
    for sGlob in tupleGlobs:
        for pathFile in pathRoot.rglob(sGlob):
            if bSkipExcludedDirectories and _fbIsExcludedScanPath(pathFile):
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
            if (sTerm, p.relative_to(REPO_ROOT).as_posix())
            not in SET_ALLOWED_SCIENCE_TERM_SOURCE_FILES
        )
    assert listViolations == [], (
        "Science-specific identifiers found in vaibify source:\n"
        + "\n".join(
            f"  [{sTerm} -> {sToken}] {p}:{iLine}: {sText}"
            for sTerm, p, iLine, sText, sToken in listViolations
        )
    )


# The ONE shipped example workflow may carry science overlap by
# explicit ruling (2026-07-27): it is an allow-path, never a weakened
# pattern. Every other shipped template stays strict.
_PATH_ALLOWED_EXAMPLE_TEMPLATE = (
    REPO_ROOT / "vaibify" / "templates" / "workflow"
)


def testNoScienceSpecificIdentifiersInShippedTemplates():
    """Shipped template text carries no science or project-tool names.

    The source-wide scan excludes ``/templates/`` and never globs
    markdown, which is how the maintainer's own tool names shipped in
    the toolkit template README. This lane scans every file of every
    template, exempting only the one shipped example workflow.
    """
    pathTemplatesRoot = REPO_ROOT / "vaibify" / "templates"
    listViolations = []
    for sTerm in LIST_FORBIDDEN_SCIENCE_TERMS:
        listViolations.extend(
            (sTerm, p, iLine, sText, sToken)
            for p, iLine, sText, sToken in _flistScanForTerm(
                pathTemplatesRoot, sTerm, tupleGlobs=("*",),
                bSkipExcludedDirectories=False,
            )
            if _PATH_ALLOWED_EXAMPLE_TEMPLATE not in p.parents
        )
    assert listViolations == [], (
        "Science-specific identifiers found in shipped templates:\n"
        + "\n".join(
            f"  [{sTerm} -> {sToken}] {p}:{iLine}: {sText}"
            for sTerm, p, iLine, sText, sToken in listViolations
        )
    )


# Spellings of one listed term that the scan must catch, and text it
# must leave alone. Without the separator-tolerant matcher only the
# run-together spelling is found, so the scan reports clean on the
# spaced form a human actually types.
TUPLE_SEPARATED_TERM_SPELLINGS = (
    "GJ1132XUV", "GJ 1132 b flux", "GJ-1132", "gj_1132_run",
)
S_TERM_LOOKALIKE_TEXT = "an approximation of the posterior"


@pytest.mark.falsification
def testScienceTermScanMatchesSeparatedSpellings(tmp_path):
    """The scan finds a term however its parts are separated.

    Kills: reverting _flistScanForTerm to a separator-intolerant
    ``re.escape(sTerm)`` pattern, which reports clean on every spelling
    a human types.
    """
    for iIndex, sSpelling in enumerate(TUPLE_SEPARATED_TERM_SPELLINGS):
        pathSample = tmp_path / f"sample{iIndex}.html"
        pathSample.write_text(
            f'<input placeholder="e.g. {sSpelling}">', encoding="utf-8",
        )
    listHits = _flistScanForTerm(tmp_path, "gj1132")
    assert len(listHits) == len(TUPLE_SEPARATED_TERM_SPELLINGS), (
        "Separated spellings the scan failed to match: "
        + repr([
            sSpelling
            for sSpelling in TUPLE_SEPARATED_TERM_SPELLINGS
            if not any(
                sSpelling.lower().startswith(sToken.lower())
                for _, _, _, sToken in listHits
            )
        ])
    )


def testScienceTermScanKeepsItsLeadingWordBoundary(tmp_path):
    """Separator tolerance does not let a term match inside a word.

    Kills: dropping the leading ``\\b`` anchor, after which "proxima"
    matches inside "approximation" and the scan cries wolf.
    """
    (tmp_path / "sample.py").write_text(
        f'S_NOTE = "{S_TERM_LOOKALIKE_TEXT}"\n', encoding="utf-8",
    )
    assert _flistScanForTerm(tmp_path, "proxima") == []


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
    directive (pinned in ``vaibify/containerImage/Dockerfile``); the dispatcher
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
    sDockerfile = fsReadSource(
        REPO_ROOT / "vaibify" / "containerImage" / "Dockerfile",
    )
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


def testEveryCatalogActionHasCliCommand():
    """Every catalog action must be reachable from the host CLI.

    ``LIST_AGENT_ACTIONS`` is the inventory of what a researcher can do
    from the dashboard, and ``vaibify do`` is generated from it, so a
    researcher can drive the same actions from a script. An entry the
    generator cannot dispatch — an unsupported transport, a path
    placeholder no CLI argument supplies — would otherwise disappear
    from the CLI in silence while the dashboard kept the button. The
    escape hatch is ``SET_ACTIONS_WITHOUT_CLI``, which must name real
    actions and carry a written rationale.

    WHAT THIS DOES AND DOES NOT PROVE. Because ``vaibify do`` is
    *generated* from the catalog, a well-formed entry on a supported
    transport gets a command automatically, and this test passes for it
    by construction. Verified: adding a fake POST action leaves this
    green. So it is a tripwire on the GENERATOR, not evidence that any
    particular action works end to end. What it genuinely catches, both
    confirmed by running:

    * an entry on a transport the generator does not implement (a PATCH
      entry fails here), and
    * an entry whose path carries a placeholder no CLI argument can
      bind (``/api/x/{sTotallyUnknownThing}`` fails here).

    Proof that an action actually reaches the hub and does the right
    thing comes from driving it against a live hub, not from here.
    """
    from vaibify.cli.actionCommands import (
        SET_ACTIONS_WITHOUT_CLI, do, flistArgumentPlaceholders,
    )
    from vaibify.gui import actionCatalog
    listViolations = []
    setCatalogNames = set()
    for dictEntry in actionCatalog.LIST_AGENT_ACTIONS:
        sName = dictEntry["sName"]
        setCatalogNames.add(sName)
        if sName in SET_ACTIONS_WITHOUT_CLI:
            continue
        commandAction = do.commands.get(sName)
        if commandAction is None:
            listViolations.append(
                f"{sName} ({dictEntry['sMethod']} {dictEntry['sPath']}) "
                f"has no 'vaibify do' command and is not in "
                f"SET_ACTIONS_WITHOUT_CLI"
            )
            continue
        setParameterNames = {
            parameter.name for parameter in commandAction.params
        }
        for sPlaceholder in flistArgumentPlaceholders(dictEntry):
            if sPlaceholder.lower() not in setParameterNames:
                listViolations.append(
                    f"{sName}: path placeholder {sPlaceholder} has no "
                    f"CLI argument"
                )
    for sExempt in sorted(SET_ACTIONS_WITHOUT_CLI - setCatalogNames):
        listViolations.append(
            f"SET_ACTIONS_WITHOUT_CLI names {sExempt!r}, which is not a "
            f"catalog action"
        )
    assert listViolations == [], (
        "Catalog actions unreachable from the host CLI:\n  "
        + "\n  ".join(listViolations)
    )


def testGeneratedActionsSendFieldsOnTheTransportTheRouteReads():
    """Every query parameter a catalog route declares is in saQueryFields.

    A generated action used to send all its caller fields as a JSON
    body. FastAPI reads a query parameter from the query string only, so
    a field aimed at one arrived nowhere and the parameter silently took
    its default: ``get-host-log-tail iLines=50`` returned 200 lines
    while the catalog's own description advertised the argument, and
    ``write-file ... sWorkdir=/x`` wrote relative to somewhere else.
    Silently doing something other than what was asked is the worst
    shape of all -- there is nothing to notice.

    The route signature is the authority here, not the catalog: this
    reads each action's endpoint and requires every parameter that is
    neither a path placeholder nor a request/body object to be declared
    in ``saQueryFields``. A route that grows one fails the build.
    """
    import inspect
    from unittest.mock import patch

    from pydantic import BaseModel
    from fastapi.routing import APIRoute

    from vaibify.gui import pipelineServer
    from vaibify.gui.actionCatalog import LIST_AGENT_ACTIONS
    from tests.testAgentLaneEnforcement import MockDockerConnection

    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    dictRoutesByKey = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for sMethod in route.methods:
                dictRoutesByKey[(sMethod, route.path)] = route

    listUndeclared = []
    for dictEntry in LIST_AGENT_ACTIONS:
        if dictEntry["sMethod"] == "WS":
            continue
        route = dictRoutesByKey.get(
            (dictEntry["sMethod"], dictEntry["sPath"]),
        )
        if route is None:
            continue
        setDeclared = set(dictEntry.get("saQueryFields") or ())
        for sName, parameter in inspect.signature(
            route.endpoint,
        ).parameters.items():
            if _fbParameterIsPathPlaceholder(sName, route.path):
                continue
            if _fbParameterIsNotAQueryField(sName, parameter, BaseModel):
                continue
            if sName not in setDeclared:
                listUndeclared.append(
                    f"{dictEntry['sName']} -> {sName}"
                )
    assert listUndeclared == [], (
        f"these catalog actions send a field the route reads from the "
        f"QUERY string as a JSON body, so it is silently ignored; "
        f"declare each in the entry's saQueryFields: {listUndeclared}"
    )


def _fbParameterIsPathPlaceholder(sName, sPath):
    """Return True when the parameter fills a path placeholder."""
    return f"{{{sName}}}" in sPath or f"{{{sName}:" in sPath


def _fbParameterIsNotAQueryField(sName, parameter, typeBaseModel):
    """Return True for parameters FastAPI does not read from the query."""
    from fastapi import Request, Response, WebSocket

    annotation = parameter.annotation
    if annotation in (Request, Response, WebSocket, dict):
        return True
    if sName in ("request", "response", "websocket", "background_tasks"):
        return True
    if isinstance(annotation, type) and issubclass(
        annotation, typeBaseModel,
    ):
        return True
    # Optional[SomeModel] and other typing constructs: a body model
    # wrapped in Optional keeps the model in its arguments.
    return any(
        isinstance(objArgument, type)
        and issubclass(objArgument, typeBaseModel)
        for objArgument in getattr(annotation, "__args__", ())
    )


def testGeneratedActionsNeverShadowTopLevelCommands():
    """Generated action commands must stay nested under ``vaibify do``.

    Several catalog names mean something different from the top-level
    command they resemble — ``vaibify push`` copies host files into the
    container, while the ``push-to-github`` action is the Level-2
    publication flow. Click's ``add_command`` overwrites silently, so a
    generator that registered flat would replace a hand-written command
    with no error at all.
    """
    from vaibify.cli.actionCommands import do
    from vaibify.cli.main import main
    from vaibify.gui import actionCatalog
    setCatalogNames = {
        dictEntry["sName"]
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
    }
    setShadowed = setCatalogNames & set(main.commands)
    assert setShadowed == set(), (
        "Catalog actions registered as top-level commands: "
        + ", ".join(sorted(setShadowed))
    )
    assert main.commands.get("do") is do, (
        "The generated action group must be registered as 'vaibify do'"
    )


def testHostHashRouteIsNeverAgentInvokable():
    """The personal-layer hash route must never look agent-safe.

    The route reads an arbitrary host file and returns its SHA-256 +
    byte count; agent-invokable, that is a hash oracle over host
    files (a compromised in-container agent could confirm guesses
    about credentials or dotfiles byte-for-byte). The route must stay
    in ``SET_INTENTIONALLY_EXCLUDED_PATHS``, must never gain a
    ``LIST_AGENT_ACTIONS`` entry, and its live handler must carry no
    ``@fnAgentAction`` marker.
    """
    from vaibify.gui import actionCatalog
    sHashPath = "/api/workflow/{sContainerId}/personal-layer/hash"
    assert ("POST", sHashPath) in (
        actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS
    )
    listCatalogHits = [
        dictEntry["sName"]
        for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry.get("sPath") == sHashPath
    ]
    assert listCatalogHits == [], (
        "The host-file hash route must never appear in "
        f"LIST_AGENT_ACTIONS (found: {listCatalogHits})"
    )
    app = _fappBuildApplication()
    for sMethod, sPath, fnEndpoint in (
        _flistCollectAppStateMutatingRoutes(app)
    ):
        if sPath == sHashPath:
            assert getattr(
                fnEndpoint, "_sAgentActionName", None,
            ) is None, (
                "The host-file hash handler must not carry an "
                "@fnAgentAction marker"
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
    sDockerfile = fsReadSource(
        REPO_ROOT / "vaibify" / "containerImage" / "Dockerfile",
    )
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


# ---------------------------------------------------------------------
# One derivation of the interactive flag.
#
# ``bInteractive`` decides a step's label, and the label is what the
# researcher speaks and what an agent-issued command resolves. Truthiness
# (``if step.bInteractive``), equality (``=== true``) and the normalizing
# classifier DISAGREE on ``null``, on the string ``"false"`` and on an
# absent key, so a second reader silently answers about a DIFFERENT step
# than the ladder shows. ``pipelineUtils.fbStepIsInteractive`` and its JS
# mirror are the only readers of the raw field; everything else asks them.
# ---------------------------------------------------------------------

# ``pipelineUtils`` holds the classifier itself; ``workflowMigrations``
# is the load-time coercion that normalizes a legacy value exactly once.
SET_RAW_INTERACTIVE_EXEMPT_PYTHON_FILES = {
    "pipelineUtils.py",
    "workflowMigrations.py",
}

# ``scriptUtilities.js`` holds the JS mirror of the classifier.
SET_RAW_INTERACTIVE_EXEMPT_JS_FILES = {
    "scriptUtilities.js",
}

_REGEX_RAW_INTERACTIVE_PYTHON = re.compile(
    r"""\.get\(\s*["']bInteractive["']|\[\s*["']bInteractive["']\s*\]"""
)

_REGEX_RAW_INTERACTIVE_JS = re.compile(r"\.bInteractive\b")


def _flistFindRawInteractiveReads(pathFile, regexRawRead):
    """Return 'name:line: text' for each raw read of the persisted flag."""
    listHits = []
    for iNumber, sLine in enumerate(
        fsReadSource(pathFile).splitlines(), start=1,
    ):
        if regexRawRead.search(sLine):
            listHits.append(f"{pathFile.name}:{iNumber}: {sLine.strip()}")
    return listHits


def testInteractiveFlagHasExactlyOneClassifier():
    """No module reads ``bInteractive`` raw; all ask the classifier.

    Six JavaScript sites classified by truthiness while every other
    reader classified by ``=== true``, and three Python sites read the
    raw field with a ``False`` default. Those rules disagree on values
    the field really takes -- ``null`` is the natural absent value of
    the ``Optional[bool]`` API field, and the in-container agent edits
    ``project.json`` by hand -- so the runner, the CLI, the clean-outputs
    builder and the ladder could each answer about a different step.

    Adding a new raw read is the regression this forbids: call
    ``pipelineUtils.fbStepIsInteractive`` in Python and
    ``VaibifyUtilities.fbStepIsInteractive`` in JavaScript instead.
    """
    listOffenders = []
    for pathFile in sorted((REPO_ROOT / "vaibify").rglob("*.py")):
        if pathFile.name in SET_RAW_INTERACTIVE_EXEMPT_PYTHON_FILES:
            continue
        listOffenders += _flistFindRawInteractiveReads(
            pathFile, _REGEX_RAW_INTERACTIVE_PYTHON,
        )
    for pathFile in sorted(STATIC_DIR.glob("*.js")):
        if pathFile.name in SET_RAW_INTERACTIVE_EXEMPT_JS_FILES:
            continue
        listOffenders += _flistFindRawInteractiveReads(
            pathFile, _REGEX_RAW_INTERACTIVE_JS,
        )
    assert listOffenders == [], (
        "These sites read the persisted bInteractive field directly "
        "instead of asking the single classifier "
        "(pipelineUtils.fbStepIsInteractive / "
        "VaibifyUtilities.fbStepIsInteractive). A raw read classifies "
        "null, \"false\" and a missing key differently from the step "
        "ladder, so it can answer about the wrong step:\n  "
        + "\n  ".join(listOffenders)
    )


def testGeneratedConftestTranscribesTheHostStepLabeller():
    """The container conftest carries a COPY of the host label rule.

    The generated conftest runs inside the container, which has no
    vaibify install to import from, so the rule cannot be called across
    the boundary -- the same constraint that makes
    ``introspectionScript.py`` duplicate ``dataLoaders.py``. Transcribing
    ``pipelineUtils``' own source keeps that copy honest: the container
    and the dashboard must produce IDENTICAL labels for every value the
    flag really takes, or a marker written in the container names a
    different step than the badge the researcher sees.

    Executed, not pattern-matched: the generated source is compiled and
    run, and its labeller is compared against the host's.
    """
    from vaibify.gui import conftestManager, pipelineUtils
    sSource = conftestManager.fsBuildConftestSource("/workspace/repo")
    dictNamespace = {}
    exec(compile(sSource, "<generated-conftest>", "exec"), dictNamespace)
    assert "flistComputeAllStepLabels" in dictNamespace, (
        "the generated conftest must define the transcribed labeller; "
        "without it _fsLabelWithinWorkflow falls back to an inline "
        "derivation, which is what the AGENTS.md label trap forbids"
    )
    listFlagValues = [
        True, False, None, "true", "false", "", 0, 1, "0", "yes",
    ]
    listSteps = [
        {"bInteractive": valueFlag} for valueFlag in listFlagValues
    ] + [{"sName": "no flag at all"}]
    listContainerLabels = dictNamespace["flistComputeAllStepLabels"](
        listSteps,
    )
    listHostLabels = pipelineUtils.flistComputeAllStepLabels(listSteps)
    assert listContainerLabels == listHostLabels, (
        "the container conftest labels steps differently from the "
        f"dashboard: container={listContainerLabels} "
        f"host={listHostLabels}. The transcription in "
        "conftestManager._fsTranscribeStepLabelDerivation has drifted "
        "from pipelineUtils."
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

    Enumerates the 2^5 truth table over the five orthogonal
    criteria (repo present, user approved, timing clean, tests
    passing, input data declared) and asserts the gate fires exactly
    when all five are True. Catches future regressions where someone
    weakens one predicate or adds a sixth without updating the
    composition.
    """
    from vaibify.reproducibility.levelGates import fbAtLeastLevel1
    listCriteria = (
        "bRepo", "bUser", "bTiming", "bTests", "bDeclared",
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
            "bNoInputData": dictFlags["bDeclared"],
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

        def fbaFetchFile(self, sId, sPath, iMaxBytes=None):
            # The pipeline-state read is a typed read, and the typed-read
            # adapter spells "absent" as FileNotFoundError rather than a
            # non-zero exit code.
            raise FileNotFoundError(sPath)

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


_TEMPLATES_DIR = REPO_ROOT / "vaibify" / "templates"

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


def testTemplateStepDirectoriesHonorTheSlugContract():
    """A shipped template must satisfy the rule it teaches.

    The ``workflow`` template declared ``GenerateSamples`` in a
    directory named ``Sampler`` and ``PlotHistogram`` in ``Plot``,
    both of which the slug contract forbids. Nothing checked the
    templates against it, so every new project from that template
    opened with red ⚠ directory-mismatch errors on both steps --
    a first-run experience that says the tool is broken.
    """
    import json as jsonModule

    from vaibify.gui.pipelineUtils import (
        fbStepDirectoryConforms, fsSlugFromStepName,
    )

    listViolations = []
    for pathWorkflow in _flistCollectTemplateWorkflows():
        dictWorkflow = jsonModule.loads(pathWorkflow.read_text())
        for dictStep in dictWorkflow.get("listSteps", []):
            if fbStepDirectoryConforms(dictStep):
                continue
            listViolations.append((
                pathWorkflow.relative_to(REPO_ROOT),
                dictStep.get("sName", ""),
                dictStep.get("sDirectory", ""),
                fsSlugFromStepName(dictStep.get("sName") or ""),
            ))
    assert listViolations == [], (
        "Shipped templates violate the step-name/directory contract:\n"
        + "\n".join(
            f"  {pathWorkflow} step '{sName}' lives in '{sDirectory}' "
            f"but the contract requires '{sSlug}'"
            for pathWorkflow, sName, sDirectory, sSlug in listViolations
        )
    )


def testTemplateCommandsNameScriptsThatExist():
    """Every script a template's commands invoke must ship with it.

    The ``workflow`` template invoked ``dataGenerateSamples.py`` and
    ``plotHistogram.py``, neither of which existed anywhere in the
    repository. A new project therefore could not run, and no test
    noticed because nothing ever executed a template.
    """
    import json as jsonModule

    listMissing = []
    for pathWorkflow in _flistCollectTemplateWorkflows():
        dictWorkflow = jsonModule.loads(pathWorkflow.read_text())
        for dictStep in dictWorkflow.get("listSteps", []):
            pathStepDirectory = (
                pathWorkflow.parent / dictStep.get("sDirectory", "")
            )
            for sField in ("saDataCommands", "saPlotCommands",
                           "saTestCommands"):
                for sCommand in dictStep.get(sField, []):
                    for sScript in _flistScriptsInCommand(sCommand):
                        if (pathStepDirectory / sScript).is_file():
                            continue
                        listMissing.append((
                            pathWorkflow.relative_to(REPO_ROOT),
                            dictStep.get("sName", ""), sScript,
                        ))
    assert listMissing == [], (
        "Template commands invoke scripts the template does not "
        "ship:\n" + "\n".join(
            f"  {pathWorkflow} step '{sName}': {sScript}"
            for pathWorkflow, sName, sScript in listMissing
        )
    )


def _flistScriptsInCommand(sCommand):
    """Return the .py arguments a command invokes, ignoring options."""
    return [
        sToken for sToken in sCommand.split()
        if sToken.endswith(".py") and not sToken.startswith("-")
        and "{" not in sToken
    ]


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

# Empty by design. It held only ``director.py``, the withdrawn
# host-side runner, whose raw host-path arguments were correct because
# host paths were its truth. No module under vaibify/gui/ handles host
# paths any more, so an exemption here would be a hole rather than a
# carve-out -- a future file reusing an exempt name would skip the scan
# silently. Adding an entry needs the same justification the deleted
# one had.
SET_REPRO_IO_EXEMPT_FILES = frozenset()


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


def _fbModuleImportsOwnershipAuthority(pathModule):
    """Return True when pathModule imports the container-ownership authority.

    The connect handler is an HTTP route: its lease rides the
    ``X-Vaibify-Lease`` header, so it consults the shared
    ``containerOwnership`` authority (via ``routeScope`` for the header)
    rather than the WebSocket query-param guard. Either import proves it
    reaches for the shared authority instead of an inline membership check.
    """
    _, treeAst = ftParseFile(pathModule)
    for sName, _iLine in flistExtractImports(treeAst):
        if sName.endswith("containerOwnership") or sName.endswith(
            "routeScope"
        ):
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
    # Either face of the authority satisfies this: fbReleaseExplicit
    # answers "did it commit?", ftReleaseExplicit additionally answers
    # "and why not" so the route can 409 a retained refusal (§10).
    assert "ReleaseExplicit" in sSource and "sLeaseId" in sSource, (
        "the release route must commit through the sessionLifecycle "
        "authority (fb/ftReleaseExplicit), never an inline drop"
    )
    assert "fnReleaseOwnership" not in sSource, (
        "no route may call the containerOwnership release primitives "
        "directly; sessionLifecycle is the single transition authority"
    )
    sLifecycleSource = fsReadSource(GUI_DIR / "sessionLifecycle.py")
    assert "fnReleaseOwnership" in sLifecycleSource, (
        "sessionLifecycle.fbReleaseExplicit must delegate the lease "
        "arbitration to containerOwnership.fnReleaseOwnership"
    )


def testWebSocketGatesUseSharedAuthorizationGuard():
    """Every container-session gate consults the one shared guard.

    The three-step gate (loopback origin + shared token + owning lease)
    lives only in ``webSocketAuthorization``. Each WebSocket route module
    must import it rather than inline its own check, and no
    access-decision module may reference a process-global
    ``setAllowedContainers`` membership set.

    The connect handler is the third gate ``architecture.md`` names: it
    had no ownership check at all until 2026-07-25, so a second tab could
    bypass the claim route's 409 while the documentation claimed otherwise.
    Since the HTTP lease moved to the ``X-Vaibify-Lease`` header (Sweep B),
    connect consults the ``containerOwnership`` authority rather than the
    WebSocket query-param guard — still a shared authority, never an inline
    membership check.
    """
    for sFileName in ("pipelineRoutes.py", "terminalRoutes.py"):
        pathModule = ROUTES_DIR / sFileName
        assert _fbModuleImportsAuthorizationGuard(pathModule), (
            f"{sFileName} must import the shared guard from "
            f"webSocketAuthorization instead of inlining the gate"
        )
    pathWorkflow = ROUTES_DIR / "workflowRoutes.py"
    assert _fbModuleImportsOwnershipAuthority(pathWorkflow), (
        "workflowRoutes must consult the shared containerOwnership "
        "authority for the connect gate instead of inlining the check"
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


def testProductionEntryPointsBindHostCheck():
    """Every CLI launcher passes a real port to the app factories.

    ``iExpectedPort`` of 0 disables the DNS-rebinding Host check; that is
    the in-process test harness's deliberate opt-out, and it must never
    reach production by omission. A launcher that constructs an
    application without naming a port would silently serve every Host
    header, so the argument is required at the call sites that bind a
    socket.
    """
    listOffenders = []
    for pathModule in sorted((REPO_ROOT / "vaibify" / "cli").glob("*.py")):
        _, treeAst = ftParseFile(pathModule)
        for node in ast.walk(treeAst):
            if not isinstance(node, ast.Call):
                continue
            sCallee = getattr(node.func, "id", "") or getattr(
                node.func, "attr", "")
            if sCallee not in (
                "fappCreateApplication", "fappCreateHubApplication",
            ):
                continue
            listKeywords = [kw.arg for kw in node.keywords]
            if "iExpectedPort" not in listKeywords:
                listOffenders.append(
                    f"{pathModule.name}:{node.lineno}: {sCallee} without "
                    f"iExpectedPort"
                )
    assert listOffenders == [], (
        "Production entry points must bind the Host check explicitly:\n  "
        + "\n  ".join(listOffenders)
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
    """The serving WS route resolves the docker id to the name first.

    The owner-of-record map is keyed by container NAME (the claim
    route's canonical key), but the WebSocket routes receive the docker
    ID in their path. A handler that serves a session must call
    ``fsContainerNameForId`` before handing a name to
    ``fiContainerSessionRejectionCode`` and to the per-container
    live-connection counter; otherwise the name-keyed gate lookup misses
    and every authorized session closes 4403. This pins the resolution
    boundary so an id-keyed regression cannot pass CI silently.

    The terminal route is deliberately absent: it serves no session at
    all while terminals are withdrawn, and resolving the id there would
    reintroduce the container-existence oracle the withdrawal removed.
    :func:`testWithdrawnTerminalRouteTouchesNothing` asserts the
    stronger property in its place.
    """
    for sFileName in ("pipelineRoutes.py",):
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


def testPoisonIsWrittenThroughOneFunctionOnly():
    """Nothing assigns ``OwnerRecord.poison`` outside its two authorities.

    Poison and fencing are one act. A record marked poisoned while its
    pipeline socket keeps dispatching frames refuses new mutations and
    permits the in-flight ones, which is the opposite of fail-closed --
    so the poison write and the connection fencing live in a single
    function, and a second assignment anywhere would be a poison that
    fences nothing.

    ``containerOwnership.py`` is the seam: it holds the one writer
    (``flistPoisonAndFenceConnections``) and the one clearer
    (``fnClearPoison``). Everything else must call them.
    """
    listViolations = []
    for pathModule in _flistProductionPythonModules():
        if pathModule.name == "containerOwnership.py":
            continue
        _, treeAst = ftParseFile(pathModule)
        for nodeAssign in ast.walk(treeAst):
            if not isinstance(nodeAssign, ast.Assign):
                continue
            for nodeTarget in nodeAssign.targets:
                if isinstance(nodeTarget, ast.Attribute) and (
                    nodeTarget.attr == "poison"
                ):
                    listViolations.append(
                        f"{pathModule.relative_to(PACKAGE_DIR)}"
                        f":{nodeAssign.lineno}"
                    )
    assert listViolations == [], (
        f"poison must be set through "
        f"containerOwnership.flistPoisonAndFenceConnections and cleared "
        f"through fnClearPoison, never assigned directly. Found: "
        f"{listViolations}"
    )


def testThePipelineSocketIsFencedByPoison():
    """The pipeline lane refuses a poisoned container, at accept and per frame.

    Poison denies MUTATIONS. The pipeline WebSocket is a mutation
    channel, so it must be refused at the gate with its own code -- the
    caller's standing is fine, the container is not -- and revalidated
    per frame, because the socket that must stop acting is precisely the
    one admitted before the poison landed.
    """
    sRouteSource = fsReadSource(ROUTES_DIR / "pipelineRoutes.py")
    assert "fbContainerIsPoisoned(" in sRouteSource, (
        "the pipeline WebSocket must refuse a poisoned container"
    )
    assert "I_REJECT_POISONED" in sRouteSource, (
        "the poison refusal must carry its own close code, so a client "
        "can tell it from an authorization refusal"
    )
    assert "iAcceptedGeneration=" in sRouteSource, (
        "the per-frame backstop must be given the generation admitted "
        "at accept, or a transfer cannot fence a socket mid-frame"
    )
    sGuardSource = fsReadSource(GUI_DIR / "webSocketAuthorization.py")
    iPerFrame = sGuardSource.find("def ffbBuildPerFrameCredentialCheck")
    assert "fbContainerIsPoisoned(" in sGuardSource[iPerFrame:], (
        "the per-frame backstop must re-read the poison state, not "
        "capture it at accept"
    )


def testTheHostReconciliationLaneIsNotFencedByPoison():
    """Poison must not fence off its own cure.

    ``vaibify reconcile`` reaches a live hub over the host control
    socket, and that lane is what CLEARS a poison. A fence that covered
    every lane would leave a poisoned container unrecoverable without
    killing the hub, so the host control channel must carry no poison
    refusal of its own.
    """
    sSource = fsReadSource(GUI_DIR / "hostControlChannel.py")
    assert "fbContainerIsPoisoned" not in sSource, (
        "the host reconciliation lane must not refuse on poison; it is "
        "the lane that clears it"
    )
    assert "fnClearPoison(" in sSource, (
        "the reconciliation handler must clear the poison through the "
        "single clearer"
    )


# ---------------------------------------------------------------------
# The parked interactive terminal.
#
# The terminal is the one lane whose containment could not be proven: a
# descendant that calls setsid leaves the recorded process group, so
# "the terminal stopped" was never provable and no authority-ending path
# could honestly report the container quiet. It is withdrawn for the
# alpha. A no-callers invariant over terminalContainment CANNOT pass --
# the module keeps production callers for cleanup, drain, and shutdown
# -- so the parking is expressed as four narrower controls instead.
# ---------------------------------------------------------------------

_S_TERMINAL_WS_PATH = "/ws/terminal"

# Creating a terminal execution is what is parked. Draining, probing,
# and registry construction are the cleanup half and must keep working:
# a legacy record written before the upgrade still has to be terminated
# and proven, or quarantined.
_SET_TERMINAL_CREATION_SYMBOLS = frozenset({
    "TerminalSession",
    "TerminalExecutionRecord",
    "fsPrepareTerminalOperation",
    "fnPromoteTerminalOperation",
    "fnRegisterTerminalRecord",
    "fsMintGroupMarkerPath",
    "fsBuildGroupReportingCommand",
    "fiDiscoverTerminalProcessGroup",
    "fnRecordTerminalProcessGroup",
})

# terminalSession.py holds the parked creation seam itself; the control
# on it is that nothing in production CONSTRUCTS a TerminalSession, not
# that the seam's body has been emptied (that is wave 7's deletion).
_SET_TERMINAL_SEAM_MODULES = frozenset({
    "terminalSession.py",
    "terminalContainment.py",
})


def _flistCallNames(treeAst):
    """Return every called name in a module, attribute calls included."""
    listNames = []
    for nodeCall in ast.walk(treeAst):
        if not isinstance(nodeCall, ast.Call):
            continue
        nodeFunc = nodeCall.func
        if isinstance(nodeFunc, ast.Name):
            listNames.append(nodeFunc.id)
        elif isinstance(nodeFunc, ast.Attribute):
            listNames.append(nodeFunc.attr)
    return listNames


def _flistProductionPythonModules():
    """Return every shipped Python module under the package root."""
    return [
        pathModule for pathModule in PACKAGE_DIR.rglob("*.py")
        if "__pycache__" not in pathModule.parts
    ]


def testWithdrawnTerminalRouteTouchesNothing():
    """The terminal route refuses first and touches no shared state.

    Ordering is the whole contract. The pre-withdrawal handler resolved
    the docker id, ran the ownership gate (which REFRESHES liveness),
    and entered the connection counters before it could refuse anything
    -- so an unauthenticated dial-in learned whether a named container
    existed, and a refused one had already disturbed the owner's
    liveness stamp. The withdrawn handler must reach none of that: the
    close is the only statement it executes.
    """
    pathRoute = ROUTES_DIR / "terminalRoutes.py"
    sSource = fsReadSource(pathRoute)
    for sForbidden in (
        "fsContainerNameForId",
        "fiContainerSessionRejectionCode",
        "fnServeUnderLiveConnectionCounters",
        "TerminalSession",
        "fnRunTerminalSession",
    ):
        assert f"{sForbidden}(" not in sSource, (
            f"the withdrawn terminal route must not call {sForbidden}; "
            f"the refusal is the first and only statement so it creates "
            f"no ownership, no counters, no records, and reveals nothing "
            f"about whether the container exists"
        )
    assert "I_REJECT_TERMINAL_DISABLED" in sSource, (
        "the terminal route must close with the one fixed withdrawal "
        "code, distinct from every authorization refusal"
    )
    assert "fnCloseWithCode(" in sSource, (
        "the refusal must accept then close (fnCloseWithCode) so the "
        "browser observes the real code instead of an opaque 1006"
    )


def testNoProductionPathConstructsATerminalSession():
    """Nothing in the shipped package constructs a terminal session.

    This is parking control 1. The route no longer builds one; this
    fails the build if any production path -- a new route, a background
    task, a convenience helper -- starts building one again while the
    containment boundary is still unprovable.
    """
    listViolations = []
    for pathModule in _flistProductionPythonModules():
        if pathModule.name in _SET_TERMINAL_SEAM_MODULES:
            continue
        _, treeAst = ftParseFile(pathModule)
        if "TerminalSession" in _flistCallNames(treeAst):
            listViolations.append(str(pathModule.relative_to(PACKAGE_DIR)))
    assert listViolations == [], (
        f"no production path may construct a TerminalSession while the "
        f"terminal is withdrawn; found in: {listViolations}"
    )


def testOnlyTheWithdrawnHandlerServesTheTerminalWebSocket():
    """Parking control 2: exactly one handler answers ``/ws/terminal``."""
    listServing = [
        str(pathModule.relative_to(PACKAGE_DIR))
        for pathModule in _flistProductionPythonModules()
        if _S_TERMINAL_WS_PATH in fsReadSource(pathModule)
    ]
    assert listServing == ["gui/routes/terminalRoutes.py"], (
        f"only the withdrawn refusal handler may answer "
        f"{_S_TERMINAL_WS_PATH}; found in: {listServing}"
    )
    sSource = fsReadSource(ROUTES_DIR / "terminalRoutes.py")
    assert sSource.count("@app.websocket(") == 1, (
        "terminalRoutes must register exactly one WebSocket endpoint -- "
        "the refusal -- so no second path can serve a session"
    )


def testNoProductionPathPreparesATerminalExecutionRecord():
    """Parking control 3: only the parked seam names the creation calls.

    A terminal execution becomes durable through
    ``fsPrepareTerminalOperation`` -> ``fnPromoteTerminalOperation`` ->
    ``fnRegisterTerminalRecord``. Outside the seam module (whose own
    constructor is unreachable, per parking control 1) no shipped module
    may name any of them, so no container can acquire a new
    quarantine-bearing terminal record.
    """
    listViolations = []
    for pathModule in _flistProductionPythonModules():
        if pathModule.name in _SET_TERMINAL_SEAM_MODULES:
            continue
        _, treeAst = ftParseFile(pathModule)
        setCalled = set(_flistCallNames(treeAst))
        setOffending = setCalled & _SET_TERMINAL_CREATION_SYMBOLS
        if setOffending:
            listViolations.append(
                (str(pathModule.relative_to(PACKAGE_DIR)),
                 sorted(setOffending))
            )
    assert listViolations == [], (
        f"terminal-record creation is parked; only the seam module may "
        f"name these calls. Found: {listViolations}"
    )


def testRemainingContainmentCallsAreCleanupOnly():
    """Parking control 4: every live containment caller is cleanup.

    ``terminalContainment`` keeps production callers -- appFactory
    builds the registry and drains it at shutdown, sessionLifecycle and
    serverLifespan drain and query it on release, reap, and shutdown --
    which is why a no-callers invariant cannot pass. What must hold is
    narrower: none of those callers CREATES anything.
    """
    listViolations = []
    for pathModule in _flistProductionPythonModules():
        if pathModule.name in _SET_TERMINAL_SEAM_MODULES:
            continue
        sSource = fsReadSource(pathModule)
        if "terminalContainment" not in sSource:
            continue
        _, treeAst = ftParseFile(pathModule)
        setOffending = (
            set(_flistCallNames(treeAst)) & _SET_TERMINAL_CREATION_SYMBOLS
        )
        if setOffending:
            listViolations.append(
                (str(pathModule.relative_to(PACKAGE_DIR)),
                 sorted(setOffending))
            )
    assert listViolations == [], (
        f"the surviving containment callers must be cleanup and "
        f"reconciliation only; creation calls found in: {listViolations}"
    )


# ---------------------------------------------------------------------
# Documented guarantee => enforcement point.
#
# Nearly every defect the 2026-07 review found was a guarantee stated in
# prose that no code enforced: the catalog's ``bAgentSafe`` flag was
# metadata, the import route's docstring promised unreachability with no
# gate behind it, and architecture.md described an ownership check the
# connect handler did not perform. The suite stayed green throughout,
# because no fixture drove the boundary. The two invariants below make a
# prose promise fail CI when nothing implements it.
# ---------------------------------------------------------------------

# A registrar docstring that says the route touches host FILES is a
# promise about the agent lane; the promise needs a gate. Deliberately
# narrow: "host keyring", "host clock" and "host-log-tail" are not
# arbitrary-file access and must not trip this.
_REGEX_HOST_FILESYSTEM_CLAIM = re.compile(
    r"host file(?:system)?s?\b|home-directory file", re.IGNORECASE,
)

# Either guard is acceptable: one refuses the agent outright, the other
# confines what the agent may reach. Both consult the same lane
# authority the middleware used rather than re-reading raw headers.
_T_AGENT_LANE_GUARD_NAMES = (
    "fnRejectAgentTokenLane",
    "fbRequestRidesAgentLane",
)


def _fbRegistersApplicationRoute(nodeFunction):
    """Return True when a nested function carries an ``@app.<method>``."""
    return any(
        ast.unparse(decorator).startswith("app.")
        for decorator in nodeFunction.decorator_list
    )


def _flistCollectRouteRegistrars(pathModule):
    """Return (node, sSegment, sDocumentation) for each route registrar.

    A registrar is a top-level function that registers at least one
    handler with an ``@app.<method>`` decorator. The documentation is the
    registrar's docstring joined with every nested handler's, because the
    claim about what a route touches is written in either place.
    """
    sSource, treeAst = ftParseFile(pathModule)
    listLines = sSource.splitlines()
    listRegistrars = []
    for node in treeAst.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        listNested = [
            nodeChild for nodeChild in ast.walk(node)
            if isinstance(nodeChild, (ast.FunctionDef, ast.AsyncFunctionDef))
            and nodeChild is not node
        ]
        if not any(_fbRegistersApplicationRoute(n) for n in listNested):
            continue
        sDocumentation = " ".join(
            [ast.get_docstring(node) or ""]
            + [ast.get_docstring(n) or "" for n in listNested]
        )
        listRegistrars.append((
            node,
            "\n".join(listLines[node.lineno - 1:node.end_lineno]),
            # Collapsed to one line: a docstring wraps wherever it fits,
            # so "reads the HOST\nfilesystem" must still match.
            re.sub(r"\s+", " ", sDocumentation),
        ))
    return listRegistrars


def testHostFilesystemRoutesRejectTheAgentLane():
    """A route documented as touching host files must gate the agent lane.

    ``project-context/import`` reads the HOST filesystem and said so in
    its own docstring -- "an agent-invokable host read would let a
    compromised in-container agent pull arbitrary home-directory files
    into a public repository" -- while its only gate was a Docker
    liveness check. The imported bytes land at ``.vaibify/AGENTS.md``,
    which the agent-safe read and push actions then expose.

    Catalog exclusion is metadata, not a gate. This invariant fails when
    a THIRD such route appears without ``fnRejectAgentTokenLane`` (which
    refuses the lane) or ``fbRequestRidesAgentLane`` (which confines it),
    so the promise can never again outrun its enforcement.
    """
    listOffenders = []
    iClaimingRoutes = 0
    for pathModule in sorted(GUI_DIR.rglob("*Routes.py")):
        for node, sSegment, sDocumentation in _flistCollectRouteRegistrars(
            pathModule,
        ):
            matchClaim = _REGEX_HOST_FILESYSTEM_CLAIM.search(sDocumentation)
            if matchClaim is None:
                continue
            iClaimingRoutes += 1
            if any(
                sGuard + "(" in sSegment
                for sGuard in _T_AGENT_LANE_GUARD_NAMES
            ):
                continue
            listOffenders.append(
                f"{pathModule.name}:{node.lineno}: {node.name} documents "
                f"host-filesystem access ({matchClaim.group(0)!r}) but "
                f"calls none of {_T_AGENT_LANE_GUARD_NAMES}"
            )
    assert listOffenders == [], (
        "A route whose documentation promises the agent cannot reach "
        "host files must enforce that promise at the route. Call "
        "fnRejectAgentTokenLane(requestHttp) as the handler's first "
        "statement, or confine the agent with fbRequestRidesAgentLane:"
        "\n  " + "\n  ".join(listOffenders)
    )
    assert iClaimingRoutes >= 2, (
        f"only {iClaimingRoutes} route registrars matched the "
        "host-filesystem trigger; the project-context import and the "
        "personal-layer hash routes must both match, otherwise this "
        "invariant has quietly become a no-op"
    )


_REGEX_EXCLUDED_PATH_TUPLE = re.compile(
    r'^\s*\(\s*"(?P<method>[A-Z]+)"\s*,\s*"(?P<path>[^"]+)"\s*\)'
)


def _flistCollectExcludedPathRationales():
    """Return (sPath, sRationale) for each intentionally-excluded route.

    The rationale is the contiguous comment block written directly above
    the path tuple -- the convention ``AGENTS.md`` requires for an
    exclusion. Read from source rather than from the frozenset because a
    frozenset carries no comments.
    """
    listLines = fsReadSource(
        GUI_DIR / "actionCatalog.py",
    ).splitlines()
    listRationales = []
    listComment = []
    bInsideExclusions = False
    for sLine in listLines:
        if sLine.startswith("SET_INTENTIONALLY_EXCLUDED_PATHS"):
            bInsideExclusions = True
            continue
        if not bInsideExclusions:
            continue
        if sLine.startswith("})"):
            break
        if sLine.strip().startswith("#"):
            listComment.append(sLine.strip().lstrip("# "))
            continue
        matchTuple = _REGEX_EXCLUDED_PATH_TUPLE.match(sLine)
        if matchTuple is not None:
            listRationales.append(
                (matchTuple.group("path"), " ".join(listComment)),
            )
        listComment = []
    return listRationales


def _fsFindRegistrarSegmentForPath(sPath):
    """Return the source of the registrar that registers ``sPath``."""
    for pathModule in sorted(GUI_DIR.rglob("*Routes.py")):
        for _node, sSegment, _sDoc in _flistCollectRouteRegistrars(
            pathModule,
        ):
            if '"' + sPath + '"' in sSegment:
                return sSegment
    return ""


def testCatalogHostFilesystemRationalesHaveAnEnforcementPoint():
    """A catalog exclusion citing host files must be enforced at the route.

    The exclusion set is documentation: ``fbAgentLanePermitsRoute``
    consults it, but the rationale beside each entry is prose an author
    can write without wiring anything. Where that prose says the route
    reaches HOST files, the handler itself must also refuse or confine
    the agent lane -- defence in depth, and the difference between a
    promise and a guarantee. This is the second reading of the same
    claim: the route docstring is checked by
    ``testHostFilesystemRoutesRejectTheAgentLane``; deleting either
    wording alone must not silently drop the requirement.
    """
    listOffenders = []
    iClaimingRoutes = 0
    for sPath, sRationale in _flistCollectExcludedPathRationales():
        if _REGEX_HOST_FILESYSTEM_CLAIM.search(sRationale) is None:
            continue
        iClaimingRoutes += 1
        sSegment = _fsFindRegistrarSegmentForPath(sPath)
        if not sSegment:
            listOffenders.append(
                f"{sPath}: excluded with a host-filesystem rationale "
                f"but no registrar in vaibify/gui registers it"
            )
            continue
        if not any(
            sGuard + "(" in sSegment
            for sGuard in _T_AGENT_LANE_GUARD_NAMES
        ):
            listOffenders.append(
                f"{sPath}: the catalog rationale says it reaches host "
                f"files, but the route calls none of "
                f"{_T_AGENT_LANE_GUARD_NAMES}"
            )
    assert listOffenders == [], (
        "Catalog exclusion is metadata, not a gate. A route excluded "
        "because it touches host files must ALSO reject the agent lane "
        "at the handler:\n  " + "\n  ".join(listOffenders)
    )
    assert iClaimingRoutes >= 2, (
        f"only {iClaimingRoutes} catalog exclusions matched the "
        "host-filesystem trigger; project-context/import and "
        "personal-layer/hash must both match, otherwise this invariant "
        "has quietly become a no-op"
    )


def testConnectHandlerGatesOnTheOwningLease():
    """Connect authorizes through the shared guard, after id->name.

    ``architecture.md`` named the owner-of-record map the sole authority
    that claim, connect and both WebSocket gates consult. Connect
    consulted nothing: it took no lease, so a second tab bypassed the
    claim route's 409 and took the workflow, the project-repo path and
    the container's agent session. ``testWebSocketGates...`` proves the
    module imports the guard; this proves the handler CALLS it, and that
    the docker id is resolved to the name-keyed map's key first.
    """
    sSource, treeAst = ftParseFile(ROUTES_DIR / "workflowRoutes.py")
    dictFunctionByName = {
        node.name: node for node in ast.walk(treeAst)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    nodeConnect = dictFunctionByName.get("fnConnect")
    assert nodeConnect is not None, (
        "workflowRoutes must still register a connect handler named "
        "fnConnect"
    )
    sConnectBody = ast.unparse(nodeConnect)
    assert "_fnRequireOwningLeaseForConnect" in sConnectBody, (
        "fnConnect must call _fnRequireOwningLeaseForConnect; without "
        "it a second browser tab connects to a container another "
        "session owns and the claim route's 409 means nothing"
    )
    assert "requestHttp" in {
        arg.arg for arg in nodeConnect.args.args
    }, (
        "fnConnect must accept the Request so the presented lease is "
        "visible to the gate"
    )
    nodeGate = dictFunctionByName.get("_fnRequireOwningLeaseForConnect")
    assert nodeGate is not None, (
        "workflowRoutes must define _fnRequireOwningLeaseForConnect"
    )
    sGateBody = ast.unparse(nodeGate)
    iResolve = sGateBody.find("fsContainerNameForId(")
    iLease = sGateBody.find("fbBrowserSessionOwnsLease(")
    assert iResolve != -1 and iLease != -1, (
        "the connect gate must resolve the docker id to the container "
        "name and then consult the session-bound "
        "containerOwnership.fbBrowserSessionOwnsLease on the "
        "X-Vaibify-Lease header -- never the value-only "
        "fbSessionOwnsContainer and never an inlined membership check, so "
        "a second browser session replaying a copied lease is refused"
    )
    assert iResolve < iLease, (
        "the connect gate must call fsContainerNameForId BEFORE "
        "fbBrowserSessionOwnsLease; the owner map is name-keyed, so an "
        "id-keyed lookup silently misses and refuses every real session"
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
#
# RULING 2026-08-05, for the carrier migration only. Five route modules
# reached their entries within a few lines of each other, and between
# them held 32 of the 57 routes still to migrate: syncRoutes (15),
# reproducibilityRoutes (8), gitRoutes (6), pipelineRoutes (3). Each
# migration adds a handful of lines to a module it does not otherwise
# change, so the ratchet had begun rising by accretion -- a few lines at
# a time, each individually justified, which is how a size limit stops
# meaning anything.
#
# The researcher's decision was to raise the affected entries ONCE,
# deliberately, rather than split. The reasoning is the one AGENTS.md
# already gives: these modules are cohesive, and splitting a file to
# satisfy a NUMBER is the premature-abstraction failure the guidance
# warns about -- the work here is many small tasks across a few
# concepts, not a new responsibility arriving. A split may still be
# right later; it should be triggered by a real seam, not by this.
#
# What this ruling does NOT license: a NEW module written over the cap,
# a rise for any reason other than adding carrier plumbing to an
# existing route, or letting these entries drift upward again
# afterwards. When the migration stops, these numbers are debt like
# every other entry here and may only fall.
#
# Clarified 2026-08-06, because an agent read the line above as
# ambiguous and was right to ask. "A new module over the cap" means a
# newly WRITTEN module, not an existing module taking its first entry
# here. An existing module that crosses 800 for the first time while
# gaining carrier plumbing takes an entry like any other — testRoutes.py
# did, at 802. The agent that hit it first trimmed to exactly 800 and
# then reverted, because reaching the number required deleting the blank
# line after each docstring summary. That reversal was correct and is
# the point of the whole ruling: deforming source to satisfy a count is
# the outcome this exists to prevent, and a two-line overshoot is not
# evidence of a god module.
# ---------------------------------------------------------------------

I_MODULE_LINE_CAP = 800

DICT_GRANDFATHERED_MODULE_LINES = {
    # NEW at 854 (2026-08-02): containerOwnership.py crossed the cap
    # when the ownership IDENTITY joined it — the recorded
    # (prior-owner, lease, generation, session) tuple an in-flight
    # operation runs under, and the comparison that says whether the
    # live record is still it. It is deliberately here and not in a new
    # module: it is a statement ABOUT an OwnerRecord, read in the same
    # breath as the record's own fields, and a separate module would
    # invite a second, drifting notion of what "the same ownership"
    # means — which is the bug class it exists to close.
    # +43 (2026-08-02): the single poison-and-fence writer and the
    # single clearer. Poison and fencing are one act, and the fence
    # needs the lane on ConnectionRecord, so both live beside the
    # record they act on.
    "containerOwnership.py": 897,
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
    # +87 (2026-07-18): the Replay-axis lane — three exec-free envelope
    # keys (dictAiProvenance / bAiModelsDeclared /
    # bProjectContextFileExists) and the ai_provenance.json stamp
    # maintenance (_fnMaintainAiProvenanceStamp: snapshot-based
    # staleness check, rewrite only on drift) so the stamp stays
    # machine-written. Cohesive with the poll side-effects it extends.
    # +9 (2026-07-18): the create-project one-shot handoff — the
    # agent's request popped from dictProjectCreationRequests into the
    # discovery poll response so the browser opens the wizard once.
    # +19 (2026-07-18): repo-root context-file detection
    # (_fbRootContextCandidateDetected + the two poll booleans) for
    # the project-context adopt affordance (concurrent lane).
    # +52 (2026-07-18): the Prompt Record envelope summary
    # (_fdictEnvelopePromptRecord + sReplayAxisState) riding the poll
    # snapshot (concurrent Replay lane).
    # +195 (2026-07-19): the Supervised-mode watchdog lane —
    # exec-free judgment from this poll's stat batch and snapshot
    # (_flistUnattributedRecentPaths, _fbSnapshotHasRecentEvent,
    # digest ratchet), the rare flag write, and the supervision
    # envelope summary. Cohesive with the poll side-effects it
    # extends; the pure chain/flag logic lives in attributionLog.
    # +3 (2026-07-19): the bPersonalLayerDeclared envelope boolean —
    # exec-free, read from the workflow dict like bAiModelsDeclared.
    # +99 (2026-07-25): dashboard-honesty repairs to the poll's two
    # existing side-effect lanes, not a new concern. The ETag stamp is
    # derived from the whole payload instead of a hand-maintained
    # signal list (dictRunState and the Replay envelope had both
    # fallen outside it); the supervision watchdog moves its judgment
    # inside the try that already guarded its write, judges each
    # change against its own mtime, latches a broken event chain into
    # a permanent flag, and reports container-vs-host clock skew.
    # _flistRecentWatchedChanges and _flistRepoRelativePaths are
    # extractions from functions this lane grew, not additions.
    # +1 (2026-07-26): a top-level import of the single interactive-flag
    # classifier. The clean-outputs builder read ``bInteractive`` raw,
    # so a string or null flag made it skip — or wipe — a different set
    # of steps than the ladder shows.
    # +32 (2026-07-26): _fbReconcileUserVerificationByHash, which runs
    # the pass above after the poll snapshot (the side-effect block
    # has no hashes to consult). Cohesive with the poll assembly.
    # +44 (2026-07-29): host-log-tail agent-lane sanitization —
    # _flistSanitizedIncidents and the lane branch that gives the agent
    # an allowlisted per-container view. Cohesive with the existing
    # host-log endpoint; no new responsibility, no seam to split.
    # +2 (2026-07-31): the pipeline WebSocket threads the session-socket
    # index and browser-session store into the shared serve wrapper
    # (ORPHANED_SESSION slice 1). Two keyword arguments, no new logic.
    # +8 (2026-08-01): the pipeline WebSocket builds the §5 per-frame
    # credential check (ORPHANED_SESSION slice 6) and threads it into
    # the handler. One builder call, no new logic here.
    # +18 (2026-08-02): the pipeline WebSocket refuses a poisoned
    # container at the gate and hands the per-frame backstop the
    # generation admitted at accept, so a transfer fences a live socket.
    # +58 (2026-08-04): _fnDeleteOutputsUnderTheDrain, the mode-(b)
    # carrier call that closes the migration plan's named live exploit —
    # the clean route's `rm` used to run on a bare asyncio.to_thread,
    # holding no lock, so a transfer arriving mid-delete saw an idle
    # container and committed over it. Justified rather than split: the
    # helper is a single-call extraction from fnCleanOutputs that
    # carries on its parent's one purpose, so splitting would create the
    # artificial seam AGENTS.md warns against, and most of the growth is
    # the docstring recording WHY the drain is held for the worker's
    # life rather than the request's. NOTE FOR THE NEXT MIGRATION GROUP:
    # this module has ten workflow saves and several more routes still
    # awaiting a carrier, so it will keep pressing this ratchet. The
    # next bump should be a conversation about splitting the file, not
    # another line here.
    # +42 (2026-08-06): the conversation happened, and the 2026-08-05
    # ruling atop this table is its outcome — raise once for carrier
    # plumbing, do not split to satisfy a number. This bump is
    # acknowledge-step's mode-(b) helper, whose docstring is most of it:
    # `_fdictGetModTimes` LOOKS like a read and WRITES a scratch path
    # file into the container before it stats, so a route that carried
    # only its workflow save would have been refused at the probe, and
    # that is the trap worth recording where the next reader will meet
    # it.
    # +94 (2026-08-06): the Kill route's three carriers. Two of them are
    # the sweep and the stopped-state write; the third is the one worth
    # recording here, because the cheap migration would not have had it.
    # Kill reads through the RECONCILING reader, so a Kill issued over a
    # runner that already died must still persist that runner's real
    # exit code and sFailureCauseHost — and that write needs its own
    # carrier, injected into the reader rather than performed by the
    # route. Dropping the reconciling reader would have been smaller and
    # would have made the dashboard say "killed (130)" over a crash.
    "routes/pipelineRoutes.py": 3020,
    # NEW at 802 (2026-08-06): testRoutes.py crossed the cap on the
    # generate-test migration, under the 2026-08-05 ruling above — an
    # existing route module, carrier plumbing, raised once rather than
    # split. It is +2 over the cap and the two lines are the reason the
    # migration is not the cheap one: a PRE-FLIGHT that rejects an
    # out-of-range step index before any carrier opens, so a typo in
    # the URL answers 404 instead of quarantining an untouched
    # container, and the docstring recording WHY that is the only
    # failure the pre-flight can take (every other one the generator
    # raises happens at or after a write). Reaching 800 exactly was
    # possible only by deleting the blank line after each docstring's
    # summary, which is deforming the source to satisfy a number.
    "routes/testRoutes.py": 802,
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
    # +21 (2026-07-25): the hub-startup sweep of stale host credential
    # files, plus the switch of the push route onto githubMirror's
    # hardened token resolver. Both belong to the credential-bearing
    # sync family this module already owns.
    # +27 (2026-07-26): verify-remote now bumps the sync epoch (the
    # one reconcile action that left the screen un-repainted), and
    # the read-only reverify-schedule endpoint makes "the background
    # loop never ran" visible instead of implied.
    # +28 (2026-07-26): _fsetMountedHostPaths and the sweep hook's
    # docker-mount enumeration. The sweep deleted a secret an
    # existing container had bind-mounted, leaving it unstartable;
    # reachability, not age, decides now. Cohesive with the other
    # credential-lifecycle wiring this module already registers.
    # +11 (2026-07-29): the sweep fails closed on daemon-enumeration
    # failure (None vs empty protected set). Same credential-sweep
    # responsibility; no seam to split.
    # +36 (2026-07-31): ORPHANED_SESSION slice 3b — the Overleaf push
    # runs as a carrier mode-(b) lock-held mutation (design §8): the
    # lane-tuple binding and the shielded-supervisor call wrap the
    # existing blocking dispatcher. Extends the push flow this module
    # already owns; the carrier machinery itself lives in
    # commitCarrier.py.
    # +4 (2026-08-05): has-credential rejects the in-container agent
    # lane. The route reads the researcher's HOST keyring and is a GET,
    # so the catalog's agent-lane gate never sees it. Four lines: the
    # shared guard's import, the Request parameter, the call. NOTE this
    # is the fourth route module to reach its cap; whether to split
    # syncRoutes along the credential/DAG seam is the researcher's
    # decision, not a line this bump settles.
    # +208 (2026-08-05): carrier plumbing for five of this module's
    # routes, under the 2026-08-05 ruling at the head of this record.
    # Each is one ``requestHttp`` parameter, one declaration, and one
    # under-the-drain wrapper naming why its worker carries refusals
    # back instead of raising them; ``add-file``'s chain also gained a
    # synchronous twin, because a mode-(b) worker runs in a thread and
    # cannot await the three ``to_thread`` hops it used to make. The
    # remaining ten routes in this module have NOT been migrated, so
    # this entry will need raising again before it may start falling.
    # +257 (2026-08-06): carrier plumbing for eight more of this
    # module's routes — the two Overleaf mirror routes (declaration
    # and rationale only, both act on the HOST mirror), the diff, the
    # manuscript pull, the credential setup, and the three Zenodo
    # routes. The bulk is not the declarations: it is the synchronous
    # twins a mode-(b) worker needs, because that worker runs in a
    # thread and cannot await the ``to_thread`` hops these chains used
    # to make. Two routes remain awaiting here — the GitHub and
    # Overleaf pushes — after which this entry may start falling.
    "routes/syncRoutes.py": 2977,
    # main +59 (2026-07-10): content-fingerprint piggyback in the
    # polling stat batch (_ftStatAndFingerprintViaPathfile) — same
    # exec, one sha256 line — feeding the reload detector.
    # +97 (2026-07-16): the input-data staleness lane — resolution and
    # collection of saInputDataFiles, full-path input invalidation,
    # the inputFile pencil bucket, and dictInputHashes drift folded
    # into the marker-hash pass. Mirrors the output lane this module
    # owns; splitting it out would smear one behavior across modules.
    # +8 (2026-07-16): inputs join _flistStepOutputsRepoRelative so
    # the fresh-clone manifest short-circuit requires manifest-clean
    # inputs too (landed with manifestWriter input coverage).
    # +89 (2026-07-26): the cross-machine content-hash pass for
    # researcher attestations (fbReconcileUserVerificationByContentHash
    # and its two path helpers). A git checkout resets every mtime, so
    # the mtime comparison alone discarded every attestation on a
    # machine hop; content decides now. Cohesive with the verification
    # state machine this module already owns.
    "fileStatusManager.py": 2196,
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
    # +8 (2026-07-16): control-character rejection in the input-path
    # boundary check (closes a heredoc-split vector for the new fields).
    # +30 (2026-07-18): flistDirectoryContractWarnings — the slug
    # contract's backend warnings channel beside the other workflow
    # validators, so a manual project.json edit is never GUI-only.
    # +45 (2026-07-28): fdictResolveTestCommandGroups plus the group
    # table it iterates — the single answer to "what would running this
    # step's tests execute", which both execution lanes now resolve
    # through. The two lanes had each written their own answer and
    # disagreed: the HTTP route's gate admitted a legacy step its
    # runner then skipped, so nothing ran and a pass was recorded. This
    # is the step-test schema, which is what the sibling
    # flistBuildTestCommands / flistResolveTestCommands already are.
    # +10 (2026-07-28): the same resolver, corrected — extra
    # saTestCommands entries now run *alongside* the structured
    # categories instead of only when none exist, deduplicated against
    # them because generating tests rewrites that list as a flat mirror.
    # The dashboard's "add test command" and save-and-run-test both
    # write there, so the fallback reading dropped hand-added tests from
    # green runs. Still one function answering one question.
    "workflowManager.py": 2347,
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
    # +167 (2026-07-16): the remote-data overwrite gate at the
    # dispatch choke point — step-set resolution per run action, one
    # existence exec, and the runRefused remoteDataOverwrite event.
    # Cohesive with the message loop and busy-refusal it sits beside;
    # every lane (browser, agent CLI) must meet the same gate here.
    # +9 (2026-07-16): the gate step-selection mirrors the runner
    # exactly (bRunEnabled + runFrom start bound), diverged from a
    # range() that over-included disabled steps.
    # +9 (2026-07-17): StepRenameRequest — the rename action's request
    # model beside the other step request schemas; the cascade logic
    # itself lives in the new stepRename.py module.
    # +3 (2026-07-18): sDescription on StepUpdateRequest — the Step
    # Viewer's optional Description block field.
    # +26 (2026-07-18): fsDeriveStepDirectory — the slug contract's
    # enforcement at step creation (directory basename derived from
    # the name), beside fdictStepFromRequest which it serves.
    # +1 (2026-07-18): replayRoutes registration line in
    # _fnRegisterAllRoutes (Replay-axis AI-model declarations).
    # +6 (2026-07-18): RequestProjectCreationRequest beside the other
    # request models, plus the dictProjectCreationRequests context
    # seed — the researcher-only create-project handoff state.
    # +87 (2026-07-19): Supervised-mode attribution at the two seams
    # this module owns — the dispatch recorder (thread-hopped ONLY
    # when supervised so unsupervised dispatch timing is untouched)
    # and the reconnect interval check (manifest-digest compare →
    # unsupervised-gap flag) inside the connect flow.
    # +61 (2026-07-25): three path-safety guards hoisted to sit beside
    # fnValidatePathWithinRoot — the control-character rejection behind
    # the heredoc-injection fix, the write denylist (moved out of
    # fileRoutes so testRoutes can share it without a route-to-route
    # import), and the parsed loopback-origin predicate replacing a
    # prefix compare. All three are the module's existing
    # request-validation responsibility.
    # +33 (2026-07-30): the capability-bootstrap exchange endpoint
    # (/api/bootstrap, A1) and the viewer first-connect session-binding
    # (P0). Both extend this module's existing session-establishment
    # responsibility; no new seam.
    # +6 (2026-07-31): ORPHANED_SESSION slice 1 — the pipeline task's
    # mutable iOwnerGeneration field (retagged in place, read at
    # completion, design §2.3) and the viewer served-record's
    # dictSessionOwner index sync. Both extend task registration and
    # ownership recording this module already owns.
    # +14 (2026-07-31): ORPHANED_SESSION slice 4 — the cardinality
    # refusal on the viewer first-connect creation path (design §9): a
    # session already holding a different container is refused before a
    # second owner record is minted. Extends the ownership recording
    # this module already owns.
    # +81 (2026-07-31): ORPHANED_SESSION slice 3b — pipeline dispatch
    # launches through carrier mode (c) (_ftLaunchDispatchTask) and the
    # WebSocket handler binds the durable dispatch context
    # (_fdictBuildDurableDispatchContext). Extends the dispatch/task
    # registration this module already owns; the carrier machinery
    # lives in commitCarrier.py.
    # +11 (2026-07-31): ORPHANED_SESSION slice 3d — the terminal run
    # loop's close path drains the containment record (terminate the
    # recorded process group and prove it empty) before the socket
    # close, per design §7: a socket closing is not a terminal dying.
    # The machinery lives in terminalContainment.py; this is one call
    # plus its rationale.
    # +50 (2026-08-01): ORPHANED_SESSION slice 5 — the /api/transfer
    # redemption endpoint beside its sibling /api/bootstrap, plus the
    # outcome→status map. The transaction itself lives in
    # sessionLifecycle.py; this is the HTTP skin over it, which is this
    # module's existing session-establishment responsibility.
    # +30 (2026-08-01): ORPHANED_SESSION slice 6 — the §5 per-frame
    # re-auth backstop on both receive loops (pipeline and terminal):
    # a frame in flight when its session is revoked is refused with
    # 4401, never dispatched. The check itself is built in
    # webSocketAuthorization; these are the two refusal sites on the
    # loops this module already owns.
    # +2 (2026-08-01): the session-lifecycle evaluator joins the
    # serverLifespan re-export block (its registration and its loop),
    # like the sweep and the idle watchdog beside it.
    # +52 (2026-08-05): the run-dispatch gate over the carrier's
    # live-work registry. Not a second responsibility: this module
    # already owns two dispatch refusals for the same socket —
    # _fbRefuseWhilePipelineTaskLive and the remote-overwrite gate —
    # and this is the third source of the SAME refusal, emitting the
    # same runRefused event through the same builder. It exists
    # because the first of those sees only pipeline actions dispatched
    # over this socket, so an HTTP route holding the container's
    # mutation lock left a Run Step blocking on the lock instead of
    # being refused. Splitting the three apart would put one refusal's
    # reasons a call hop away from its siblings while they still share
    # the event, the loop and the ordering between them.
    "pipelineServer.py": 2489,
    # NEW at 975 (2026-07-31): the commit-guard carrier (design §8) is
    # one normative unit — three commit modes, the shielded supervisor
    # + registry, the out-of-band cancellation plane, the parent-gated
    # helper spawn, and the ordered-shutdown drain. Splitting it would
    # smear a single security boundary (who may commit a container
    # mutation, and who releases the drain) across call hops; every
    # piece changes for the same reason (the §8 model).
    # +19 (2026-08-02): the lock holder registers WHAT it is doing, so
    # a busy refusal can name the live operation. An asyncio.Lock knows
    # only that it is held; the metadata has to live with the holder,
    # and the holder is the supervisor this module owns.
    # +40 (2026-08-02): the carrier refuses a coroutine worker at
    # RUNTIME -- both the declaration and an awaitable result -- because
    # a source-shape check caught only the spelling that had already
    # burned us.
    # +22 (2026-08-02): the coroutine-worker refusal moved to the public
    # entrance as well, so a programming error cannot journal an
    # operation and quarantine a container before being caught.
    # +4 (2026-08-05): CommitRefusedError reparented off PermissionError
    # onto ControlPlaneRefusalError, which is an import line and a
    # three-line docstring pointing at the base. No responsibility
    # moved in or out -- the module is still the §8 commit boundary --
    # and the rationale lives once, on the base class, rather than
    # being restated here.
    "commitCarrier.py": 1065,
    # NEW at 810 (2026-08-01): ORPHANED_SESSION slice 8 added the fifth
    # allowlisted operation, `mint-bootstrap` (the headless `vaibify do`
    # credential, §6b), to hostControlChannel.py. The module IS the
    # closed operation schema plus the one peer-credential portability
    # shim that guards every operation in it; a handler homed elsewhere
    # would be an operation the allowlist does not visibly enumerate,
    # which is the property this protocol exists to hold.
    # +5 (2026-08-02): the break-glass stop callback now reports whether
    # it PROVED the container stopped or absent, so the handler's local
    # shim documents that contract instead of forwarding blindly.
    # +4 (2026-08-02): the force-abandon routes its poison through the
    # single writer and schedules the connection fencing.
    "hostControlChannel.py": 819,
    # NEW at 823 (2026-08-01): sessionLifecycle.py is the single
    # state-transition authority (design §3) — claim, release,
    # transfer, and now the slice-6 orphan transition commit in one
    # module, each under the same canonical lock order. Splitting the
    # transitions apart would smear the one place that may commit an
    # ownership state change across several files; every function here
    # changes for the same reason (the §1 state machine).
    # +100 (2026-08-01): ORPHANED_SESSION slice 6 — the owner-aware
    # session sweep and the evaluator pass that drives it. It belongs
    # beside the orphan transition it commits through: the sweep's
    # whole point is that an expired OWNING session must go through
    # fnOrphanSession, never a bare revoke, so splitting it out would
    # put the caller and the only correct commit path in different
    # files. The evaluator's SCHEDULING lives in serverLifespan.
    # +60 (2026-08-01): ORPHANED_SESSION slice 7 — the absolute cap and
    # fdictSessionExpiryView, the backend truth the pre-expiry warning
    # renders. The view is the read side of the very predicate beside
    # it (the cap is the deadline it counts down to, and the socket
    # veto it must NOT count down to); homing it anywhere else would
    # let the countdown and the expiry drift apart.
    # +101 (2026-08-01): ORPHANED_SESSION slice 6 — the §10 explicit
    # release authority: the busy arbitration (live run / live guarded
    # mutation / live agent, with force scoped to the agent alone) and
    # the channel close that must precede freeing the flock. It is the
    # same transition table as the rest of this module and shares its
    # lock order, its terminal drain, and its connection-detach helper.
    # +146 (2026-08-01): ORPHANED_SESSION slice 9 — the start axis's two
    # ownership transitions (§10b): ftReserveContainerForStart, whose
    # claim-plus-cardinality read-check-write must be atomic against a
    # concurrent claim on a DIFFERENT container, and
    # ftSettleFailedStartOwnership, which frees the flock only for a
    # settlement proven clean. They live here for the same reason every
    # other transition does — this is the only module that may call the
    # ownership primitives — plus the public cardinality-lock accessor
    # and the start-result entitlement rebinding in the transfer commit.
    # +17 (2026-08-01): the failed-start settlement may free the flock
    # only for a record the start CREATED. A start on a container the
    # caller already owns reserves on the existing record, and releasing
    # that dropped a valid owner's lease and flock when the start
    # refused — one click on an already-running container you own. The
    # guard belongs here, in the module that owns release, rather than
    # in the settlement callback that answers cleanliness.
    # +24 (2026-08-02): the failed-start release gate compares the
    # RECORDED ownership identity against the live record instead of a
    # Boolean, so a start cannot free ownership a transfer replaced
    # while it ran.
    # +22 (2026-08-02): fnScheduleConnectionFencing — closing a fenced
    # socket is an await, and the poison commit is synchronous under the
    # held locks, so the close is scheduled rather than awaited there.
    # +6 (2026-08-02): the transfer docstring records that a live
    # mode-(c) task is ADOPTED rather than refused, and why -- an
    # external review read the old wording as a claim that transfer
    # blocks every live mutation, which it does not and should not.
    # +12 (2026-08-02): the transfer docstring enumerates its three
    # cases (lock-held refuses, durable adopts, unregistered is
    # invisible) after an earlier wording claimed the opposite of the
    # third.
    "sessionLifecycle.py": 1319,
    # NEW at 899 (2026-08-01): ORPHANED_SESSION slice 9 —
    # startReservation.py is one lifecycle (design §10b): arbitrate the
    # start under the flock and the cardinality lock, launch it as a
    # mode-(c) durable task, settle it, cancel it, and deliver its
    # outcome. The one real seam has already been taken — the bounded
    # outcome ledger lives in startResultStore.py, which changes for
    # its own reasons (lifetime, caps, rebinding) — and what remains is
    # a single state machine whose steps share the reservation object
    # and the two locks. Splitting it further would put the ordering
    # that IS the safety argument (process confirmed exited → labelled
    # container conclusively gone → reservation compare-and-deleted →
    # flock freed) across several files.
    # +9 (2026-08-01): the reservation records whether the start
    # established the ownership it runs under, which is the fact the
    # settlement guard above consults.
    # +145 (2026-08-02): the start arbitration hardening — the
    # already-running refusal that precedes the reservation, the
    # re-inspection of the exact incarnation before SUCCEEDED may be
    # committed, the hard ceiling, and the owner-lease recovery for a
    # poll whose result record has expired. All four are steps of the
    # same ordering, and that ordering IS the safety argument, so they
    # belong beside it rather than in a module that would have to
    # re-derive the reservation's state to act.
    # +13 (2026-08-02): the quarantine path poisons through the single
    # writer and fences the container's pipeline socket.
    "startReservation.py": 972,
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
    # +69 (2026-07-25): the GitHub connectivity check grew a host lane.
    # The push runs on the host, so a container-only probe reported
    # "Connected" while every dashboard push was refused; the check now
    # resolves the same credential the push will use and reports both
    # lanes separately. Cohesive with the connectivity family here.
    "syncDispatcher.py": 1742,
    # +9 (2026-07-14): the run loop resolves each step's wall-clock
    # budget and threads it onto the stepStarted event so the state
    # writer can stamp it beside the step start time. Cohesive with the
    # per-step run orchestration it extends.
    # +87 (2026-07-16): the remote-data provenance recorder — after a
    # successful pull step, one exec sha256-hashes the declared
    # remote files, updates listRemoteData records, and emits
    # remoteDataRecorded. Cohesive with the step execution it stamps.
    # +4 (2026-07-18): the orchestrator re-export shim carries the
    # slug-contract helpers (fsSlugFromStepName etc.) from
    # pipelineUtils, as testOrchestratorReExportsAreComplete demands.
    # +1 (2026-07-25): the same shim carries fbStepIsInteractive, the
    # single interactive-flag classifier the runner now uses to pick
    # the interactive lane. No new responsibility.
    "pipelineRunner.py": 1500,
    "dataLoaders.py": 1222,
    "introspectionScript.py": 1192,
    "testGenerator.py": 1063,
    # +20 (2026-07-18): flistQueryHostDirectory gains bIncludeFiles
    # (+ _fdictBuildHostFileEntry) so import pickers can list host
    # files, not just directories (concurrent project-context lane).
    # +72 (2026-07-18): per-container resource limits — the
    # CPU/memory fields on CreateProjectRequest and
    # ContainerSettingsRequest, the settings GET/POST threading, the
    # _fnRequireValidResourceLimits API-boundary guard, and the
    # _fnUpdateYamlScalarField extraction the bool/number field
    # writers now share. Cohesive with the settings surface it
    # extends.
    # +27 (2026-07-25): _fnRequireLimitWithinRange — the API-boundary
    # guard now rejects non-finite and oversized caps, which %g would
    # otherwise render into vaibify.yml as text PyYAML reads back as a
    # string, bricking every later load. Cohesive with the guard it
    # replaces.
    # 1106 -> 1125: _fnRejectUninstallablePackages, refusing a conda
    # package list the image build would silently drop. Project
    # creation validation is this module's existing responsibility,
    # so this is the cohesive-module case, not a new concern.
    # +33 (2026-07-26): independent Codex/Gemini settings request fields,
    # readback, and validation. This is the existing container-settings API;
    # extracting a provider abstraction for three short branches would add
    # indirection without separating a distinct responsibility.
    # +9 (2026-07-31): the operation-journal state joins the registry
    # listing (design §8: QUARANTINED must never render as available)
    # and the claim threads the Docker connection into the journal's
    # automatic tier. Annotating listed containers with host lock and
    # journal state is this module's existing responsibility.
    # +7 (2026-07-31): the poison axis joins the same listing annotation
    # (design §2.1: a force-abandoned owner surfaces as bPoisoned, the
    # live in-process mirror of the durable quarantine record).
    # +32 (2026-08-01): the release route answers a RETAINED refusal
    # with 409 and its reason (design §10) instead of a 200 carrying
    # "bReleased: false", and reads the optional bForce flag off a
    # body the pagehide beacon may not send at all. The arbitration
    # itself is in sessionLifecycle; this is its HTTP skin.
    # +28 (2026-08-01): ORPHANED_SESSION slice 9 — the start route
    # becomes the reservation's HTTP skin (202 + status location, never
    # a lease), beside its cancel sibling and the canonical status
    # poll. The arbitration, launch, settlement, and delivery live in
    # startReservation.py; these three handlers resolve the browser
    # session, load the project config, and map outcomes to codes.
    # +3 (2026-08-02): the release force flag's docstring records that
    # an unreadable body fails CLOSED, replacing a rationale that
    # named a pagehide beacon the frontend deliberately never sends.
    "registryRoutes.py": 1237,
    # Grandfathered at 807 (2026-07-18): the catalog grows by design —
    # one block per new agent action (create-project in this lane;
    # project-context actions in the concurrent lane). It remains one
    # cohesive responsibility: the agent-action registry.
    # +33 (2026-07-18): the three Prompt Record actions and the
    # approve-first-capture exclusion (concurrent Replay lane).
    # +3 (2026-07-19): the supervision/configure exclusion — the
    # supervised party must not toggle its own supervision.
    # +22 (2026-07-19): the declare-personal-layer action (user-only
    # L2 consent moment) and the personal-layer/hash exclusion — the
    # host-file hash oracle must never be agent-invokable.
    # +49 (2026-07-25): fbAgentLanePermitsRoute — the server-side
    # enforcement point for bAgentSafe, which until now existed only as
    # client-side advice in vaibify-do. It belongs beside the data it
    # decides on; the catalog stays one cohesive responsibility.
    # +17 (2026-07-26): the reconcile-remote-state entry plus the
    # push-to-github description that now names it. One catalog
    # entry per researcher-invokable action is this module's whole
    # job; the growth is the job being done.
    # +1 (2026-07-29): the run-all / force-run-all descriptions now
    # state that disabled steps are skipped, correcting agent-facing
    # text alongside the Run All disabled-step fix.
    # +18 (2026-07-29): the ten hub control-plane routes added to
    # SET_INTENTIONALLY_EXCLUDED_PATHS so the agent lane refuses the
    # control plane. Governing every route is the catalog's whole job.
    # +5 (2026-07-30): the /api/bootstrap auth endpoint excluded from the
    # agent lane (A1). Same governance responsibility.
    # +5 (2026-08-01): the /api/transfer redemption endpoint excluded
    # from the agent lane (ORPHANED_SESSION slice 5, 'vaibify open').
    # Same governance responsibility.
    # +1 (2026-08-01): cancelling a start joins the container control
    # plane the in-container agent may never operate (slice 9).
    # +3 (2026-08-02): saQueryFields on the three actions whose routes
    # read a parameter from the query string, so a generated command
    # sends each field on the transport the route actually reads.
    "actionCatalog.py": 964,
    # +105 (2026-07-26): reconcile-remote-state — the one action that
    # repairs the dashboard after a push vaibify did not make (an
    # agent or a terminal 'git push'). It is fetch + verify-cache
    # refresh + sync-status bookkeeping + epoch bump, and every one
    # of those parts already lives here: the fetch cache, the fetch
    # runner, and the remote-heads view are this module's, and a
    # sibling route module may not import them. Same cohesive
    # responsibility — reconciling the dashboard with origin — not a
    # second concern.
    # +199 (2026-08-05): carrier plumbing for all six mutating routes
    # (phase 2, under the 2026-08-05 ruling above). The whole rise is
    # the shape the carrier forces: each handler's `await
    # asyncio.to_thread(...)` chain becomes a SYNCHRONOUS worker
    # function -- mode (b) runs workers in a thread and a coroutine
    # would be refused -- so every route grows a named worker plus the
    # docstring saying which commands share its held drain and why. The
    # module's responsibility is unchanged; only the call shape is.
    # −7 (2026-08-05): the settle-then-raise ordering lifted into
    # routeContext.fobjRunWorkerUnderTheDrain on its fourth caller. Both
    # this module and repoRoutes are now fully migrated, so their
    # entries are ratcheted back down to what they actually measure
    # rather than left holding the migration's headroom.
    "routes/gitRoutes.py": 1037,
    # NEW at 824 (2026-08-05): repoRoutes.py crossed the cap when the
    # two Repos-panel pushes were migrated onto carrier mode (b)
    # (migration plan phase 2). The added lines are one worker, one
    # carrier invocation, one shared post-push tail extracted from the
    # two handlers that had it duplicated, and the function that names
    # a push for the journal and the busy refusal without naming its
    # credential. All of it is the Repos panel acting on a repository
    # it already owns — the same cohesive responsibility, not a second
    # concern arriving. There is no seam to split on: the push helpers
    # thread the panel's own sidecar and remote through, and a
    # sibling route module may not import them.
    # −38 (2026-08-05): the lifted drain wrapper, as above.
    "routes/repoRoutes.py": 786,
    # NEW at 808 (2026-08-05): stepRoutes.py crossed the cap by 8 lines
    # when its last three routes were migrated (phase 2, under the
    # 2026-08-05 ruling above). Two of the three could not stay inline:
    # mode (b) runs its worker in a thread, so the rename cascade and
    # the alignment batch each became a named synchronous worker where
    # the handler used to `await asyncio.to_thread(...)`. The added
    # lines are those two workers, the update-step worker, and the
    # docstrings recording which failures are carried back and which
    # poison -- a judgement read out of stepRename's source that a
    # reader must not have to re-derive. Same cohesive responsibility:
    # step CRUD, in the module that owns it.
    "routes/stepRoutes.py": 808,
    # NEW at 962 (2026-08-05): replayRoutes.py crossed the cap when its
    # five remaining routes were migrated (phase 2, under the
    # 2026-08-05 ruling above). Three of the five are probe-then-write
    # sequences whose probe is the GUARD -- "create the context only if
    # it is absent", "import only if absent or bOverwrite" -- so each
    # became a named worker holding one drain across both halves, plus
    # the docstring recording which refusals are carried and which
    # poison. The context write also needed its own mode-(a) commit:
    # it writes .vaibify/AGENTS.md, not project.json, so it could not
    # reuse fnCommitWorkflowSave's record without handing the journal
    # probe a hash belonging to a different file. Same cohesive
    # responsibility: the Replay axis, in the module that owns it.
    "routes/replayRoutes.py": 962,
    # NEW at 946 (2026-08-03): routeScope.py crossed the cap when the
    # carrier-mode declaration joined it (migration plan phase 1c). 130
    # of the ~145 added lines are ONE data record,
    # SET_ROUTES_AWAITING_CARRIER_MODE, and it is deliberately here
    # rather than in a module of its own for two reasons. It is read by
    # exactly one function, _fbServeOnAmbientAdmission, whose branch a
    # reader must understand together with the record — moving it away
    # costs the hop and buys nothing. And it is TEMPORARY by
    # construction: R6 makes it shrink by one on every phase-2
    # migration, and phase 4 deletes it together with the ambient
    # branch, at which point this entry goes with it. Creating a module
    # in order to delete it is churn, not a seam.
    "routeScope.py": 946,
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


# Vendored third-party bundles carry their authors' contact details;
# only first-party frontend source is governed.
SET_EMAIL_SCAN_EXEMPT_DIRECTORIES = {"vendor"}


def testShippedFrontendCarriesNoPersonalContactDetails():
    """No email address appears in first-party frontend source.

    Placeholder attributes in ``index.html`` shipped the maintainer's
    real name and university email to every user, alongside their
    research area, until 2026-07-27. The science-identifier scan cannot
    catch that class: a personal name is not a mission designation, and
    ``testNoScienceSpecificIdentifiersInSource`` only knows the terms in
    its list.

    An address-shaped string is the tractable half of that problem --
    names cannot be enumerated, but a contact address in shipped UI is
    almost always someone's real one leaking out of an example. Use a
    reserved-example domain in placeholders instead (RFC 2606).

    Kills: replace the placeholder on #inputGitIdentityEmail with a
    real-looking address such as "e.g. someone@university.edu".
    """
    regexEmail = re.compile(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    )
    listOffenders = []
    for sGlob in ("**/*.html", "**/*.js", "**/*.css"):
        for pathFile in STATIC_DIR.rglob(sGlob):
            if SET_EMAIL_SCAN_EXEMPT_DIRECTORIES & set(pathFile.parts):
                continue
            for iLine, sLine in enumerate(
                pathFile.read_text(encoding="utf-8").splitlines(), 1
            ):
                for sMatch in regexEmail.findall(sLine):
                    # example.org / .com / .net are reserved for docs.
                    if sMatch.split("@")[-1].startswith("example."):
                        continue
                    listOffenders.append(
                        f"{pathFile.relative_to(REPO_ROOT)}:{iLine}: {sMatch}"
                    )
    assert listOffenders == [], (
        "Personal contact details in shipped frontend source:\n  "
        + "\n  ".join(listOffenders)
    )
