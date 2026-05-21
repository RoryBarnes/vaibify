"""Determinism audit for the AICS L3 readiness gate.

Wraps :mod:`vaibify.testing.stochasticDetector` and adds rules that
catch determinism leaks the per-script detector cannot see by itself:
clock-based RNG seeds, explicit opt-outs of deterministic algorithms,
reads of OS entropy pools, and missing workflow-level BLAS / OMP
thread declarations.

Two top-level entry points: :func:`flistAuditScriptAntiPatterns`
inspects one script for the script-local anti-patterns;
:func:`fbWorkflowDeclaresDeterminism` validates the workflow-level
``dictDeterminism`` block. The L3 readiness gate composes both.
"""

import ast
import re
from pathlib import Path


__all__ = [
    "S_ACCEPT_BLAS_WAIVER_KEY",
    "S_OMP_NUM_THREADS_KEY",
    "S_MKL_CBWR_KEY",
    "flistAuditScriptAntiPatterns",
    "fbWorkflowDeclaresDeterminism",
    "flistAuditWorkflow",
]


S_ACCEPT_BLAS_WAIVER_KEY = "bAcceptBlasVariance"
S_OMP_NUM_THREADS_KEY = "dOmpNumThreads"
S_MKL_CBWR_KEY = "sMklCbwr"

_SET_CLOCK_MODULES = frozenset({"time", "datetime"})
_SET_CLOCK_ATTRIBUTES = frozenset({
    "time", "monotonic", "perf_counter", "process_time",
    "time_ns", "now", "utcnow", "today",
})
_REGEX_TORCH_NONDETERMINISTIC = re.compile(
    r"torch\.use_deterministic_algorithms\s*\(\s*False\b"
)
_REGEX_DEV_URANDOM = re.compile(r"['\"]/dev/u?random['\"]")
_REGEX_SECRETS_MODULE = re.compile(
    r"\b(?:import\s+secrets\b|from\s+secrets\s+import\b|secrets\.[A-Za-z_])"
)
_REGEX_OS_URANDOM = re.compile(r"\bos\.urandom\s*\(")


def flistAuditScriptAntiPatterns(sScriptPath):
    """Return issue strings for determinism anti-patterns in one script.

    Empty list means the script is clean for the patterns this gate
    knows about. A file that does not exist returns one issue so the
    caller does not silently accept a missing reference.
    """
    pathScript = Path(sScriptPath)
    if not pathScript.is_file():
        return [f"Script not found: '{sScriptPath}'"]
    sSource = pathScript.read_text(encoding="utf-8", errors="replace")
    listIssues = []
    listIssues.extend(_flistFindClockSeeds(sSource, sScriptPath))
    listIssues.extend(_flistFindTorchNondeterministicOptOut(
        sSource, sScriptPath))
    listIssues.extend(_flistFindUrandomReads(sSource, sScriptPath))
    listIssues.extend(_flistFindSecretsModuleUse(sSource, sScriptPath))
    return listIssues


def _flistFindClockSeeds(sSource, sScriptPath):
    """Detect ``seed(time.time())`` and similar clock-derived seeds."""
    try:
        treeAst = ast.parse(sSource)
    except SyntaxError:
        return []
    listIssues = []
    for node in ast.walk(treeAst):
        if not isinstance(node, ast.Call):
            continue
        if not _fbCallIsSeedFunction(node):
            continue
        for nodeArg in node.args:
            if _fbExprUsesClock(nodeArg):
                listIssues.append(
                    f"{sScriptPath}:{node.lineno}: seed(...) argument "
                    "is derived from a clock; outputs will vary across "
                    "runs"
                )
                break
    return listIssues


def _fbCallIsSeedFunction(nodeCall):
    """Return True iff the call's attribute or name ends with 'seed'."""
    nodeFn = nodeCall.func
    if isinstance(nodeFn, ast.Attribute):
        return nodeFn.attr.lower().endswith("seed")
    if isinstance(nodeFn, ast.Name):
        return nodeFn.id.lower().endswith("seed")
    return False


def _fbExprUsesClock(nodeExpr):
    """Return True iff the expression references a clock source."""
    for nodeChild in ast.walk(nodeExpr):
        if isinstance(nodeChild, ast.Attribute):
            if _fbAttributeIsClock(nodeChild):
                return True
        if isinstance(nodeChild, ast.Call):
            if _fbCallIsOsUrandom(nodeChild):
                return True
    return False


def _fbAttributeIsClock(nodeAttr):
    """Return True iff ``<clock_module>.<clock_attr>`` is referenced."""
    if nodeAttr.attr not in _SET_CLOCK_ATTRIBUTES:
        return False
    nodeValue = nodeAttr.value
    while isinstance(nodeValue, ast.Attribute):
        nodeValue = nodeValue.value
    if isinstance(nodeValue, ast.Name):
        return nodeValue.id in _SET_CLOCK_MODULES
    return False


def _fbCallIsOsUrandom(nodeCall):
    """Return True iff the call is os.urandom(...)."""
    nodeFn = nodeCall.func
    if not isinstance(nodeFn, ast.Attribute):
        return False
    if nodeFn.attr != "urandom":
        return False
    nodeValue = nodeFn.value
    return isinstance(nodeValue, ast.Name) and nodeValue.id == "os"


def _flistFindTorchNondeterministicOptOut(sSource, sScriptPath):
    """Detect explicit torch deterministic-algorithm opt-outs."""
    listIssues = []
    for iLine, sLine in enumerate(sSource.splitlines(), start=1):
        if _REGEX_TORCH_NONDETERMINISTIC.search(sLine):
            listIssues.append(
                f"{sScriptPath}:{iLine}: "
                "torch.use_deterministic_algorithms(False) opts out "
                "of deterministic CUDA kernels"
            )
    return listIssues


def _flistFindUrandomReads(sSource, sScriptPath):
    """Detect reads of /dev/urandom or os.urandom calls."""
    listIssues = []
    for iLine, sLine in enumerate(sSource.splitlines(), start=1):
        if _REGEX_DEV_URANDOM.search(sLine):
            listIssues.append(
                f"{sScriptPath}:{iLine}: reads /dev/urandom which "
                "is process-local entropy and cannot be reproduced"
            )
        if _REGEX_OS_URANDOM.search(sLine):
            listIssues.append(
                f"{sScriptPath}:{iLine}: os.urandom(...) returns "
                "non-reproducible OS entropy"
            )
    return listIssues


def _flistFindSecretsModuleUse(sSource, sScriptPath):
    """Detect ``secrets`` module imports or attribute access."""
    listIssues = []
    for iLine, sLine in enumerate(sSource.splitlines(), start=1):
        if _REGEX_SECRETS_MODULE.search(sLine):
            listIssues.append(
                f"{sScriptPath}:{iLine}: secrets module is a "
                "non-reproducible OS-entropy source"
            )
    return listIssues


def fbWorkflowDeclaresDeterminism(dictWorkflow):
    """Return True iff the workflow declares its BLAS / thread pinning.

    Either an explicit ``dOmpNumThreads`` or ``sMklCbwr`` setting
    counts; a ``bAcceptBlasVariance: true`` waiver also passes the
    gate. The waiver is honest documentation of an accepted limitation,
    not a silent bypass.
    """
    dictDeterminism = (dictWorkflow or {}).get("dictDeterminism") or {}
    if dictDeterminism.get(S_ACCEPT_BLAS_WAIVER_KEY) is True:
        return True
    if dictDeterminism.get(S_OMP_NUM_THREADS_KEY) is not None:
        return True
    if dictDeterminism.get(S_MKL_CBWR_KEY):
        return True
    return False


def flistAuditWorkflow(dictWorkflow):
    """Return workflow-level determinism issues (BLAS declaration + per-step flags).

    Steps whose `bUnseededRandomnessWarning` is True surface as
    explicit issues so the dashboard can surface them in the
    readiness card without re-running the scanner.
    """
    listIssues = []
    if not fbWorkflowDeclaresDeterminism(dictWorkflow):
        listIssues.append(
            "Workflow has no dictDeterminism block; declare "
            f"{S_OMP_NUM_THREADS_KEY}, {S_MKL_CBWR_KEY}, or set "
            f"{S_ACCEPT_BLAS_WAIVER_KEY}=true to waive."
        )
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            continue
        if dictStep.get("bUnseededRandomnessWarning") is True:
            sName = dictStep.get("sName") or dictStep.get("sLabel") or "?"
            listIssues.append(
                f"Step '{sName}' has bUnseededRandomnessWarning=True"
            )
    return listIssues
