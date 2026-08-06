#!/usr/bin/env python3
"""Enumerate every violation of the Hungarian-notation style contract.

The style guide (AGENTS.md, "Style guide") requires function names to
begin with ``f`` plus letters declaring the return type, and prefixed
variables to hold the cast their prefix claims. Until 2026-08-05 that
contract existed only as prose, and the repository's recorded lesson is
that a guarantee stated only in prose is not enforced: the census that
preceded this tool found ``ft`` and ``ftuple`` coexisting for the same
cast, ``ba`` meaning bytes while the documented grammar implied
array-of-bool, and a double-prefixed function name.

THE DIVISION OF LABOUR MIRRORS THE MUTATION INVENTORY. The machine
proves COMPLETENESS: it walks the AST of every module under
``vaibify/`` and emits exactly one row per (identity, debt class) for
every violation it can detect. It does NOT judge intent -- whether a
nonconforming name is a framework obligation, a CLI verb kept by
ruling, or plain drift. Those judgements live in
``DICT_REVIEWED_DISPOSITIONS`` below, reviewed by a human, recorded
against a qualified identity, and honest about being judgements.

WHAT THE SCAN CANNOT SEE, IT DOES NOT CLAIM. A function returning a
wrongly-typed *call result* with no annotation is invisible here; two
such known cases are recorded in ``LIST_REVIEW_TRACKED_MISNAMINGS``
rather than in the inventory, because an inventory row the scan cannot
regenerate would be ejected by the pruning rule. Unannotated,
unprefixed variables are not governed at all. The action-verb rule is
not enforced. See the style-invariant plan and AGENTS.md for the full
statement of the boundary.

The vocabulary is CLOSED, two-tier, and held in two independently
edited copies (here and in ``tests/testStyleInvariants.py``); growing
either tier takes both edits plus a ruling -- the executable form of
the style guide's "if a cast is not listed above, ask me."

Usage::

    python tools/generateStyleInventory.py            # print JSON
    python tools/generateStyleInventory.py --write    # update the file
    python tools/generateStyleInventory.py --check    # drift only
    python tools/generateStyleInventory.py --census   # prefix census

``tests/testStyleInvariants.py`` runs the same scan in CI (imported
in-process, never a subprocess), so the record cannot quietly fall
behind the code it describes, and the frozen seed plus exact budgets
there make the debt ratchet irreversible in both directions.
"""

import argparse
import ast
import json
import pathlib
import re
import sys
from collections import Counter

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"
PATH_INVENTORY = PATH_REPOSITORY / "tests" / "styleInventory.json"

S_SCHEMA_VERSION = "1"

# --------------------------------------------------------------------------
# The two-tier prefix vocabulary. INDEPENDENT COPY: an identical pair of
# structures lives in tests/testStyleInvariants.py; growing either tier
# takes both edits plus a ruling (plan rulings R-A, R1-R4, R8).
# --------------------------------------------------------------------------

# Tier 1: core casts. Agreement sets name the annotation base names the
# prefix accepts. "n" is special-cased (no returns, no yields, None
# annotation); arrays additionally check their subscript element.
DICT_TIER_ONE_AGREEMENT = {
    "n": set(),
    "b": {"bool"},
    "i": {"int"},
    "f": {"float"},
    "d": {"float"},
    "s": {"str"},
    "t": {"tuple", "Tuple"},
    "list": {"list", "List"},
    "dict": {"dict", "Dict"},
    "json": {"dict", "Dict", "list", "List", "str", "int", "float", "bool"},
    "ba": {"bytes", "bytearray"},
    "iter": {"Iterator", "Generator", "AsyncIterator", "AsyncGenerator",
             "Iterable"},
    "context": {"Iterator", "Generator", "AsyncIterator", "AsyncGenerator"},
    "da": {"list", "List", "ndarray"},
    "ia": {"list", "List", "ndarray"},
    "fa": {"list", "List", "ndarray"},
    "sa": {"list", "List"},
    "ta": {"list", "List"},
}

# The element cast an array prefix declares, checked when the annotation
# subscripts its list (list[float] for da); a bare ``list`` also agrees.
DICT_ARRAY_ELEMENT_CAST = {
    "da": {"float"},
    "ia": {"int"},
    "fa": {"float"},
    "sa": {"str"},
    "ta": {"tuple", "Tuple"},
}

# Tier 2: registered domain prefixes, each mapped to its concrete type
# family (derived by reading every site, 2026-08-05; plan Appendix C1).
DICT_TIER_TWO_REGISTRY = {
    "set": {"set", "Set", "frozenset"},
    "preflight": {"PreflightResult"},
    "config": {"ProjectConfig"},
    "path": {"Path"},
    "files": {"HostRepoFiles", "ContainerRepoFiles", "SnapshotRepoFiles"},
    "record": {"StartResultRecord", "StartTaskRecord", "OwnerRecord",
               "ConnectionRecord", "DurableTaskRecord",
               "TerminalExecutionRecord", "PoisonRecord",
               "BrowserSessionRecord"},
    "lock": {"Lock"},
    "response": {"Response", "JSONResponse", "StreamingResponse",
                 "HTMLResponse", "PlainTextResponse", "FileResponse",
                 "RedirectResponse"},
    "app": {"FastAPI"},
    "datetime": {"datetime"},
    "file": {"IO", "TextIO", "BinaryIO", "TextIOWrapper"},
    "token": {"Token"},
    "connection": {"DockerConnection"},
    "admission": {"MutationAdmission"},
    "module": {"ModuleType"},
    "container": {"Container"},
    "buffer": {"BytesIO"},
    "info": {"TarInfo"},
    "socket": {"SocketIO", "socket"},
    "deque": {"deque"},
    "identity": {"OwnershipIdentity"},
    "command": {"Command"},
    "docker": {"DockerClient"},
    "features": {"FeaturesConfig"},
    "repro": {"ReproducibilityConfig"},
    "overleaf": {"OverleafConfig"},
}

DICT_ALL_AGREEMENT = {**DICT_TIER_ONE_AGREEMENT, **DICT_TIER_TWO_REGISTRY}

# Variable-name governance uses the same vocabulary minus "n" (a
# variable cannot "return nothing") and "context" (a decorated shape,
# not a value cast).
SET_VARIABLE_PREFIXES = (
    set(DICT_ALL_AGREEMENT) - {"n", "context"}
)

# Imprecision on a prefixed name defeats the contract (ruling R4).
SET_FORBIDDEN_ANNOTATION_BASES = {"object", "Any"}

# On a @contextmanager function the source annotation describes the
# undecorated generator, so annotating the context-manager type itself
# is the violation (plan round-4 amendment).
SET_CONTEXT_MANAGER_BASES = {"ContextManager", "AsyncContextManager",
                             "AbstractContextManager",
                             "AbstractAsyncContextManager"}

# Debt classes (shrink-only, budgeted in tests/testStyleInvariants.py).
S_CLASS_NAME = "legacy-name"
S_CLASS_FN_RETURN = "legacy-fn-return"
S_CLASS_YIELD = "legacy-yield"
S_CLASS_LITERAL_RETURN = "legacy-literal-return"
S_CLASS_RETURN_ANNOTATION = "legacy-return-annotation"
S_CLASS_ANNOTATION_MISMATCH = "legacy-annotation-mismatch"
LIST_DEBT_CLASSES = [
    S_CLASS_NAME,
    S_CLASS_FN_RETURN,
    S_CLASS_YIELD,
    S_CLASS_LITERAL_RETURN,
    S_CLASS_RETURN_ANNOTATION,
    S_CLASS_ANNOTATION_MISMATCH,
]

# Standing categories: rows with a stated reason, not debt.
S_CATEGORY_CLI_VERB = "cli-verb"
S_CATEGORY_INTERFACE = "interface-method"
S_CATEGORY_SECURITY_PINNED = "security-pinned"

S_REASON_CLI_VERB = (
    "Click command; the user-facing verb and the function name stay "
    "identical by ruling R6"
)

# Reviewed, human-written dispositions keyed by qualified identity.
# The scanner still finds these sites; this map only converts their
# category and supplies the reason. Adding an entry is a review act.
DICT_REVIEWED_DISPOSITIONS = {
    "vaibify/docker/dockerConnection.py::_BytesGeneratorPipe.read": (
        S_CATEGORY_INTERFACE,
        "file-like protocol method consumed by tarfile",
    ),
    "vaibify/reproducibility/githubMirror.py::_AuthStrippingRedirectHandler.redirect_request": (
        S_CATEGORY_INTERFACE,
        "urllib HTTPRedirectHandler override; the name is the protocol",
    ),
    "vaibify/gui/serverMiddleware.py::SessionTokenMiddleware.dispatch": (
        S_CATEGORY_INTERFACE,
        "BaseHTTPMiddleware override; the name is the protocol",
    ),
    "vaibify/gui/serverMiddleware.py::SecurityHeadersMiddleware.dispatch": (
        S_CATEGORY_INTERFACE,
        "BaseHTTPMiddleware override; the name is the protocol",
    ),
    "vaibify/gui/serverMiddleware.py::ActivityTrackingMiddleware.dispatch": (
        S_CATEGORY_INTERFACE,
        "BaseHTTPMiddleware override; the name is the protocol",
    ),
    "vaibify/cli/main.py::_DefaultContainerIdFilter.filter": (
        S_CATEGORY_INTERFACE,
        "logging.Filter override; the name is the protocol",
    ),
    "vaibify/gui/routeScope.py::ContainerAwareRoute.get_route_handler": (
        S_CATEGORY_INTERFACE,
        "fastapi APIRoute override; the name is the protocol",
    ),
    "vaibify/gui/hostIncidents.py::HostIncidentHandler.emit": (
        S_CATEGORY_INTERFACE,
        "logging.Handler override; the name is the protocol",
    ),
    "vaibify/gui/routeContext.py::RouteContext.get": (
        S_CATEGORY_INTERFACE,
        "dict-compatible accessor kept for mapping-style callers",
    ),
    "vaibify/gui/routeContext.py::RouteContext.setdefault": (
        S_CATEGORY_INTERFACE,
        "dict-compatible accessor kept for mapping-style callers",
    ),
    "vaibify/gui/routeContext.py::RouteContext.pop": (
        S_CATEGORY_INTERFACE,
        "dict-compatible accessor kept for mapping-style callers",
    ),
}

# Review-tracked misnamings the scanner CANNOT see (no annotation, no
# literal return): plan Appendix C3. tests/testStyleInvariants.py
# asserts each identity still exists, so a fix must delete its entry
# here -- the record self-prunes and lives in the repository, not in a
# plan file.
LIST_REVIEW_TRACKED_MISNAMINGS = [
    (
        "vaibify/gui/routes/pipelineRoutes.py::_ffilesFetchPollSnapshot",
        "returns a raw repo-path str on the fallback path while carrying "
        "the files prefix; refactor or rename at burn-down",
    ),
    (
        "vaibify/gui/fileStatusManager.py::_ffilesForWorkflowRepo",
        "returns a raw repo-path str on the fallback path while carrying "
        "the files prefix; refactor or rename at burn-down",
    ),
]

RX_FUNCTION_SHAPE = re.compile(r"^(_{0,2})f([a-z]+)([A-Z0-9].*)$")
RX_VARIABLE_SHAPE = re.compile(r"^(_{0,2})([a-z]+)([A-Z0-9].*)?$")
RX_DUNDER = re.compile(r"^__\w+__$")


class UnparseableAnnotationError(Exception):
    """An annotation the scanner cannot resolve; the check fails closed."""


def fsParseFunctionPrefix(sName):
    """Return the vocabulary prefix of a conforming function name, else None."""
    match = RX_FUNCTION_SHAPE.match(sName)
    if match is None:
        return None
    sPrefix = match.group(2)
    if sPrefix in DICT_ALL_AGREEMENT:
        return sPrefix
    return None


def fsParseVariablePrefix(sName):
    """Return the vocabulary prefix a variable name claims, else None."""
    match = RX_VARIABLE_SHAPE.match(sName)
    if match is None:
        return None
    sPrefix = match.group(2)
    if sPrefix in SET_VARIABLE_PREFIXES:
        return sPrefix
    return None


def fsUnparseDecorator(nodeDecorator):
    """Return the decorator's source text without any trailing call."""
    sText = ast.unparse(nodeDecorator)
    return sText.split("(")[0]


def fbHasPropertyDecorator(nodeFunction):
    """Detect property/setter/cached_property definitions (ruling R5)."""
    for nodeDecorator in nodeFunction.decorator_list:
        sText = fsUnparseDecorator(nodeDecorator)
        if sText == "property" or sText.endswith("cached_property"):
            return True
        if sText.endswith((".setter", ".getter", ".deleter")):
            return True
    return False


def fbHasClickCommandDecorator(nodeFunction):
    """Detect Click command/group registration (standing category cli-verb)."""
    for nodeDecorator in nodeFunction.decorator_list:
        sText = fsUnparseDecorator(nodeDecorator)
        if sText.endswith(".command") or sText.endswith(".group"):
            return True
    return False


def fbHasContextManagerDecorator(nodeFunction):
    """Detect @contextmanager / @asynccontextmanager (ruling R8)."""
    for nodeDecorator in nodeFunction.decorator_list:
        if fsUnparseDecorator(nodeDecorator).endswith("contextmanager"):
            return True
    return False


def flistCollectOwnBodyNodes(nodeFunction):
    """Every AST node of the function's own body, nested defs excluded."""
    listCollected = []
    listPending = list(nodeFunction.body)
    while listPending:
        node = listPending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            continue
        listCollected.append(node)
        listPending.extend(ast.iter_child_nodes(node))
    return listCollected


def fsLiteralTypeName(nodeValue):
    """The builtin type name of a syntactic literal, else None."""
    if isinstance(nodeValue, ast.Constant):
        return type(nodeValue.value).__name__
    if isinstance(nodeValue, ast.JoinedStr):
        return "str"
    if isinstance(nodeValue, ast.List):
        return "list"
    if isinstance(nodeValue, ast.Dict):
        return "dict"
    if isinstance(nodeValue, ast.Tuple):
        return "tuple"
    if isinstance(nodeValue, ast.Set):
        return "set"
    if isinstance(nodeValue, ast.UnaryOp) and isinstance(
            nodeValue.operand, ast.Constant):
        return type(nodeValue.operand.value).__name__
    return None


def flistResolveAnnotationParts(nodeAnnotation):
    """Resolve an annotation into (sBaseName, nodeSubscript) parts.

    Optional and Union unwrap; ``X | None`` unions flatten; quoted
    annotations are parsed and fail closed on a syntax error. None
    parts are dropped (Optional always agrees per ruling R4).
    """
    if isinstance(nodeAnnotation, ast.Constant):
        if nodeAnnotation.value is None:
            return []
        if isinstance(nodeAnnotation.value, str):
            try:
                nodeParsed = ast.parse(nodeAnnotation.value, mode="eval").body
            except SyntaxError as error:
                raise UnparseableAnnotationError(
                    nodeAnnotation.value) from error
            return flistResolveAnnotationParts(nodeParsed)
        raise UnparseableAnnotationError(ast.dump(nodeAnnotation))
    if isinstance(nodeAnnotation, ast.Name):
        if nodeAnnotation.id == "None":
            return []
        return [(nodeAnnotation.id, None)]
    if isinstance(nodeAnnotation, ast.Attribute):
        return [(nodeAnnotation.attr, None)]
    if isinstance(nodeAnnotation, ast.BinOp) and isinstance(
            nodeAnnotation.op, ast.BitOr):
        return (flistResolveAnnotationParts(nodeAnnotation.left)
                + flistResolveAnnotationParts(nodeAnnotation.right))
    if isinstance(nodeAnnotation, ast.Subscript):
        return _flistResolveSubscriptParts(nodeAnnotation)
    raise UnparseableAnnotationError(ast.dump(nodeAnnotation))


def _flistResolveSubscriptParts(nodeSubscript):
    listBase = flistResolveAnnotationParts(nodeSubscript.value)
    if len(listBase) != 1:
        raise UnparseableAnnotationError(ast.dump(nodeSubscript))
    sBase = listBase[0][0]
    if sBase == "Optional":
        return flistResolveAnnotationParts(nodeSubscript.slice)
    if sBase == "Union":
        nodeSlice = nodeSubscript.slice
        if isinstance(nodeSlice, ast.Tuple):
            listParts = []
            for nodeElement in nodeSlice.elts:
                listParts.extend(flistResolveAnnotationParts(nodeElement))
            return listParts
        return flistResolveAnnotationParts(nodeSlice)
    return [(sBase, nodeSubscript.slice)]


def fbAnnotationPartAgrees(sPrefix, tPart):
    """Does one resolved annotation part satisfy the prefix's family?"""
    sBase, nodeSubscript = tPart
    if sBase in SET_FORBIDDEN_ANNOTATION_BASES:
        return False
    setAgreement = DICT_ALL_AGREEMENT[sPrefix]
    if sBase not in setAgreement:
        return False
    if sPrefix in DICT_ARRAY_ELEMENT_CAST and sBase in {"list", "List"}:
        return _fbArrayElementAgrees(sPrefix, nodeSubscript)
    return True


def _fbArrayElementAgrees(sPrefix, nodeSubscript):
    if nodeSubscript is None:
        return True
    try:
        listElementParts = flistResolveAnnotationParts(nodeSubscript)
    except UnparseableAnnotationError:
        return False
    setExpected = DICT_ARRAY_ELEMENT_CAST[sPrefix]
    return all(sBase in setExpected for sBase, _ in listElementParts)


def fbLiteralAgreesWithPrefix(sPrefix, sLiteralType):
    """Does a literal return's type satisfy the prefix? bool beats int."""
    if sLiteralType == "NoneType":
        return True
    if sPrefix in ("f", "d") and sLiteralType == "int":
        return True
    setAgreement = DICT_ALL_AGREEMENT[sPrefix]
    if sLiteralType == "bool":
        return "bool" in setAgreement
    return sLiteralType in setAgreement or sLiteralType.capitalize() in (
        setAgreement)


class StyleViolationScanner(ast.NodeVisitor):
    """Collect (identity, debt class, detail) violations for one module."""

    def __init__(self, sRelativePath):
        self.sRelativePath = sRelativePath
        self.listQualifiedStack = []
        self.listViolations = []
        self.counterPrefixCensus = Counter()

    def fsIdentity(self, sName):
        listParts = self.listQualifiedStack + [sName]
        return f"{self.sRelativePath}::" + ".".join(listParts)

    def fnRecord(self, sIdentity, sDebtClass, sDetail):
        self.listViolations.append((sIdentity, sDebtClass, sDetail))

    def visit_ClassDef(self, node):
        self.listQualifiedStack.append(node.name)
        self.generic_visit(node)
        self.listQualifiedStack.pop()

    def visit_FunctionDef(self, node):
        self._fnVisitFunction(node)

    def visit_AsyncFunctionDef(self, node):
        self._fnVisitFunction(node)

    def _fnVisitFunction(self, node):
        sIdentity = self.fsIdentity(node.name)
        sPrefix = self._fsCheckFunctionName(node, sIdentity)
        if sPrefix is not None:
            self.counterPrefixCensus[sPrefix] += 1
            self._fnCheckFunctionBody(node, sIdentity, sPrefix)
            self._fnCheckReturnAnnotation(node, sIdentity, sPrefix)
        self._fnCheckParameterAnnotations(node)
        self.listQualifiedStack.append(node.name)
        self.generic_visit(node)
        self.listQualifiedStack.pop()

    def _fsCheckFunctionName(self, node, sIdentity):
        if RX_DUNDER.match(node.name) or node.name == "main":
            return None
        if node.name.startswith("test"):
            return None
        if fbHasPropertyDecorator(node):
            return None
        sPrefix = fsParseFunctionPrefix(node.name)
        if sPrefix is not None:
            return sPrefix
        if sIdentity in DICT_REVIEWED_DISPOSITIONS:
            sCategory, sReason = DICT_REVIEWED_DISPOSITIONS[sIdentity]
            self.fnRecord(sIdentity, sCategory, sReason)
        elif fbHasClickCommandDecorator(node):
            self.fnRecord(sIdentity, S_CATEGORY_CLI_VERB, S_REASON_CLI_VERB)
        else:
            self.fnRecord(sIdentity, S_CLASS_NAME,
                          "no valid prefix in the closed vocabulary")
        return None

    def _fnCheckFunctionBody(self, node, sIdentity, sPrefix):
        listOwnNodes = flistCollectOwnBodyNodes(node)
        bYields = any(isinstance(child, (ast.Yield, ast.YieldFrom))
                      for child in listOwnNodes)
        bContextDecorated = fbHasContextManagerDecorator(node)
        self._fnCheckYieldRules(sIdentity, sPrefix, bYields, bContextDecorated)
        listValueReturns = [
            child for child in listOwnNodes
            if isinstance(child, ast.Return) and child.value is not None
            and not (isinstance(child.value, ast.Constant)
                     and child.value.value is None)
        ]
        if sPrefix == "n":
            if listValueReturns:
                self.fnRecord(sIdentity, S_CLASS_FN_RETURN,
                              "fn-prefixed function returns a value")
            return
        self._fnCheckLiteralReturns(sIdentity, sPrefix, listValueReturns)

    def _fnCheckYieldRules(self, sIdentity, sPrefix, bYields,
                           bContextDecorated):
        if bContextDecorated and sPrefix != "context":
            self.fnRecord(sIdentity, S_CLASS_YIELD,
                          "context-manager function not prefixed context")
        elif not bContextDecorated and bYields and sPrefix != "iter":
            self.fnRecord(sIdentity, S_CLASS_YIELD,
                          "generator function not prefixed iter")
        elif not bContextDecorated and sPrefix == "context":
            self.fnRecord(sIdentity, S_CLASS_YIELD,
                          "context prefix without a contextmanager decorator")

    def _fnCheckLiteralReturns(self, sIdentity, sPrefix, listValueReturns):
        for nodeReturn in listValueReturns:
            sLiteralType = fsLiteralTypeName(nodeReturn.value)
            if sLiteralType is None:
                continue
            if not fbLiteralAgreesWithPrefix(sPrefix, sLiteralType):
                self.fnRecord(
                    sIdentity, S_CLASS_LITERAL_RETURN,
                    f"returns a {sLiteralType} literal against prefix "
                    f"{sPrefix}")
                return

    def _fnCheckReturnAnnotation(self, node, sIdentity, sPrefix):
        if node.returns is None:
            return
        try:
            listParts = flistResolveAnnotationParts(node.returns)
        except UnparseableAnnotationError:
            self.fnRecord(sIdentity, S_CLASS_RETURN_ANNOTATION,
                          "unparseable return annotation (fails closed)")
            return
        if sPrefix == "n":
            if listParts:
                self.fnRecord(sIdentity, S_CLASS_RETURN_ANNOTATION,
                              "fn-prefixed function annotated non-None")
            return
        if sPrefix == "context":
            self._fnCheckContextAnnotation(sIdentity, listParts)
            return
        if not all(fbAnnotationPartAgrees(sPrefix, tPart)
                   for tPart in listParts):
            self.fnRecord(sIdentity, S_CLASS_RETURN_ANNOTATION,
                          f"return annotation disagrees with prefix {sPrefix}")

    def _fnCheckContextAnnotation(self, sIdentity, listParts):
        for sBase, _ in listParts:
            if sBase in SET_CONTEXT_MANAGER_BASES:
                self.fnRecord(
                    sIdentity, S_CLASS_RETURN_ANNOTATION,
                    "context-manager annotation belongs to the decorated "
                    "callable, not the source generator")
                return
        if not all(fbAnnotationPartAgrees("context", tPart)
                   for tPart in listParts):
            self.fnRecord(sIdentity, S_CLASS_RETURN_ANNOTATION,
                          "return annotation disagrees with prefix context")

    def _fnCheckParameterAnnotations(self, node):
        listArguments = (node.args.posonlyargs + node.args.args
                         + node.args.kwonlyargs)
        for nodeArgument in listArguments:
            if nodeArgument.annotation is None:
                continue
            sIdentity = (self.fsIdentity(node.name)
                         + f"::{nodeArgument.arg}")
            self._fnCheckVariableAnnotation(
                sIdentity, nodeArgument.arg, nodeArgument.annotation)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            sIdentity = self.fsIdentity(node.target.id)
            self._fnCheckVariableAnnotation(
                sIdentity, node.target.id, node.annotation)
        self.generic_visit(node)

    def _fnCheckVariableAnnotation(self, sIdentity, sName, nodeAnnotation):
        sPrefix = fsParseVariablePrefix(sName)
        if sPrefix is None:
            return
        try:
            listParts = flistResolveAnnotationParts(nodeAnnotation)
        except UnparseableAnnotationError:
            self.fnRecord(sIdentity, S_CLASS_ANNOTATION_MISMATCH,
                          "unparseable annotation (fails closed)")
            return
        if not listParts:
            return
        if not all(fbAnnotationPartAgrees(sPrefix, tPart)
                   for tPart in listParts):
            self.fnRecord(sIdentity, S_CLASS_ANNOTATION_MISMATCH,
                          f"annotation disagrees with prefix {sPrefix}")


def flistScanPackage(pathPackage=PATH_PACKAGE):
    """Scan every module and return sorted, de-duplicated violation rows."""
    listViolations = []
    counterCensus = Counter()
    for pathModule in sorted(pathPackage.rglob("*.py")):
        sRelativePath = str(pathModule.relative_to(PATH_REPOSITORY))
        try:
            treeModule = ast.parse(pathModule.read_text(errors="replace"))
        except SyntaxError:
            continue
        scanner = StyleViolationScanner(sRelativePath)
        scanner.visit(treeModule)
        listViolations.extend(scanner.listViolations)
        counterCensus.update(scanner.counterPrefixCensus)
    setUniquePairs = set()
    listRows = []
    for sIdentity, sDebtClass, sDetail in listViolations:
        if (sIdentity, sDebtClass) in setUniquePairs:
            continue
        setUniquePairs.add((sIdentity, sDebtClass))
        listRows.append((sIdentity, sDebtClass, sDetail))
    listRows.sort(key=lambda tRow: (tRow[0], tRow[1]))
    return listRows, counterCensus


def fdictBuildInventory(listRows):
    """Assemble the deterministic JSON document from scanned rows."""
    counterBudgets = Counter()
    listJsonRows = []
    for sIdentity, sDebtClass, sDetail in listRows:
        if sDebtClass in LIST_DEBT_CLASSES:
            counterBudgets[sDebtClass] += 1
            sReason = ""
        else:
            sReason = sDetail
        listJsonRows.append({
            "sIdentity": sIdentity,
            "sDebtClass": sDebtClass,
            "sReason": sReason,
        })
    dictBudgets = {sClass: counterBudgets.get(sClass, 0)
                   for sClass in LIST_DEBT_CLASSES}
    return {
        "sSchemaVersion": S_SCHEMA_VERSION,
        "listRows": listJsonRows,
        "dictBudgets": dictBudgets,
    }


def fdictGenerateInventory():
    """Scan the package and build the inventory document."""
    listRows, _ = flistScanPackage()
    return fdictBuildInventory(listRows)


def fiCheckInventoryDrift():
    """Compare the committed inventory to a fresh regeneration."""
    dictGenerated = fdictGenerateInventory()
    if not PATH_INVENTORY.exists():
        print("styleInventory.json is missing; run --write", file=sys.stderr)
        return 1
    dictCommitted = json.loads(PATH_INVENTORY.read_text())
    if dictCommitted != dictGenerated:
        print("styleInventory.json does not match a regeneration; "
              "inspect with --write on a scratch copy", file=sys.stderr)
        return 1
    return 0


def fnPrintCensus():
    """Print the observed prefix distribution for vocabulary rulings."""
    _, counterCensus = flistScanPackage()
    for sPrefix, iCount in counterCensus.most_common():
        print(f"{iCount:6d}  {sPrefix}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="update tests/styleInventory.json")
    parser.add_argument("--check", action="store_true",
                        help="exit nonzero on drift from the committed file")
    parser.add_argument("--census", action="store_true",
                        help="print the observed prefix distribution")
    arguments = parser.parse_args()
    if arguments.census:
        fnPrintCensus()
        return 0
    if arguments.check:
        return fiCheckInventoryDrift()
    dictInventory = fdictGenerateInventory()
    sRendered = json.dumps(dictInventory, indent=2, sort_keys=False) + "\n"
    if arguments.write:
        PATH_INVENTORY.write_text(sRendered)
        print(f"wrote {len(dictInventory['listRows'])} rows")
        return 0
    print(sRendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
