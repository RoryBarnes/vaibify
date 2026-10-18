"""Determinism audit for the PROOF L3 readiness gate.

Wraps :mod:`vaibify.testing.stochasticDetector` and adds rules that
catch determinism leaks the per-script detector cannot see by itself:
clock-based RNG seeds, explicit opt-outs of deterministic algorithms,
reads of OS entropy pools, and an unanswered workflow-level question
about repeatability.

Two top-level entry points: :func:`flistAuditScriptAntiPatterns`
inspects one script for the script-local anti-patterns;
:func:`fbWorkflowDeclaresDeterminism` validates the workflow-level
``dictDeterminism`` block. The L3 readiness gate composes both.

Since 2026-10-17 the block asks ONE question (design ruling 2): how
much speed vaibify should trade for repeatable files. The answer names
an execution profile, which vaibify writes; the researcher never types
it.
"""

import ast
import re
from pathlib import Path

from vaibify.reproducibility import answeredQuestion
from vaibify.reproducibility.executionProfiles import (
    S_EXECUTION_PROFILE_KEY,
)


__all__ = [
    "LIST_DETERMINISM_QUESTIONS",
    "S_REPEATABILITY_ANSWER_KEY",
    "S_REPEATABILITY_SPEED",
    "S_REPEATABILITY_ACROSS",
    "S_ACCEPT_BLAS_WAIVER_KEY",
    "S_BLAS_ACCEPTED",
    "S_BLAS_ANSWER_KEY",
    "S_BLAS_REJECTED",
    "S_MKL_ANSWER_KEY",
    "S_MKL_NOT_USED",
    "S_MKL_PINNED",
    "S_OMP_ANSWER_KEY",
    "S_OMP_PINNED",
    "S_OMP_UNPINNED",
    "S_OMP_NUM_THREADS_KEY",
    "S_MKL_CBWR_KEY",
    "flistUnansweredDeterminismQuestions",
    "flistAuditScriptAntiPatterns",
    "flistAuditScriptSource",
    "fdictScanWorkflowScripts",
    "fbWorkflowDeclaresDeterminism",
    "flistAuditWorkflow",
]


# The ONE question (2026-10-17). `sAnswerKey` records the CHOICE and
# `sValueKey` the execution profile it names, which the declare route
# writes as the newest table version for the choice -- so a researcher
# never types an ID, and every answer carries its value.
#
# `sLabel`, `sPlainQuestion` and each choice's words are
# researcher-facing and carry no key names; they are the design's own
# wording, shipped to the browser so the form holds no second copy.
S_REPEATABILITY_ANSWER_KEY = "sRepeatabilityAnswer"
S_REPEATABILITY_SPEED = "speed"
S_REPEATABILITY_ACROSS = "across"

LIST_DETERMINISM_QUESTIONS = (
    {
        "sKey": "repeatability",
        "sAnswerKey": S_REPEATABILITY_ANSWER_KEY,
        "sValueKey": S_EXECUTION_PROFILE_KEY,
        "tAnswersNeedingValue": (
            S_REPEATABILITY_SPEED, S_REPEATABILITY_ACROSS),
        "tAnswers": (S_REPEATABILITY_SPEED, S_REPEATABILITY_ACROSS),
        "sLabel": "Speed or repeatable files",
        "sPlainQuestion": (
            "How much speed should vaibify trade for repeatable files?"),
        "listChoices": (
            {"sAnswer": S_REPEATABILITY_SPEED,
             "sLabel": "Prioritize speed",
             "bRecommended": False,
             "sDescription": (
                 "Vaibify leaves your math libraries at full speed. "
                 "Files made on another computer can differ in their "
                 "last digits; Level 3 judges them by your published "
                 "comparisons.")},
            {"sAnswer": S_REPEATABILITY_ACROSS,
             "sLabel": "Repeatable across computers",
             "bRecommended": True,
             "sDescription": (
                 "Vaibify runs NumPy, OpenBLAS and the C math library on "
                 "one thread, using instructions that supported "
                 "computers share. Your computers are then more likely "
                 "to produce identical files. Heavy numerical work can "
                 "run several times slower. This reduces differences "
                 "between computers; it cannot eliminate them.")},
        ),
        "sWhy": (
            "The container fixes the software your steps run, but not "
            "the processor they run on. Math libraries choose their "
            "fastest code for the processor they find, and split work "
            "across however many cores it has. So the last digits of a "
            "result can differ between computers even when every "
            "program is the same."),
        "sConsequence": (
            "Your answer changes how steps run, not what Level 3 "
            "requires. Changing it later means rerunning every step "
            "once, and it retires your current Level 3 attestation."),
    },
)

# Legacy, read only by migrations: the three questions asked from
# 2026-08-30 until the one above replaced them. `_fnMigrateV12ToV13`
# promoted values to these answers, and a test pins its literals to
# these constants. No gate reads them now (design D7).
S_ACCEPT_BLAS_WAIVER_KEY = "bAcceptBlasVariance"
S_OMP_NUM_THREADS_KEY = "dOmpNumThreads"
S_MKL_CBWR_KEY = "sMklCbwr"
S_BLAS_ANSWER_KEY = "sBlasVarianceAnswer"
S_OMP_ANSWER_KEY = "sOmpThreadsAnswer"
S_MKL_ANSWER_KEY = "sMklModeAnswer"
S_BLAS_ACCEPTED = "accepted"
S_BLAS_REJECTED = "rejected"
S_OMP_PINNED = "pinned"
S_OMP_UNPINNED = "unpinned"
S_MKL_PINNED = "pinned"
S_MKL_NOT_USED = "not-used"

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

    Reads from the HOST filesystem. A workflow's scripts live inside
    the container, so the dashboard scan uses
    :func:`flistAuditScriptSource` with text fetched through the repo
    adapter instead; this entry point stays for host-side callers.

    Empty list means the script is clean for the patterns this gate
    knows about. A file that does not exist returns one issue so the
    caller does not silently accept a missing reference.
    """
    pathScript = Path(sScriptPath)
    if not pathScript.is_file():
        return [f"Script not found: '{sScriptPath}'"]
    return flistAuditScriptSource(
        pathScript.read_text(encoding="utf-8", errors="replace"),
        sScriptPath,
    )


def flistAuditScriptSource(sSource, sScriptPath):
    """Return issue strings for anti-patterns in already-read source.

    The detectors were always source-based; only the file read was
    host-bound, which is why the scanner shipped for months with no
    caller anywhere in the GUI -- a container project's scripts are
    not on the host to be opened.

    WHAT AN EMPTY LIST MEANS: no *anti-pattern* was found. It is NOT
    proof the script is deterministic, and it says nothing about
    ordinary seeded RNG, unstable dict/set iteration, wall-clock
    branching, or parallel reduction order. The determinism
    declaration remains the researcher's assertion; this only removes
    the cases a machine can see.
    """
    listIssues = []
    listIssues.extend(_flistFindClockSeeds(sSource, sScriptPath))
    listIssues.extend(_flistFindTorchNondeterministicOptOut(
        sSource, sScriptPath))
    listIssues.extend(_flistFindUrandomReads(sSource, sScriptPath))
    listIssues.extend(_flistFindSecretsModuleUse(sSource, sScriptPath))
    return listIssues


def fdictScanWorkflowScripts(dictWorkflow, filesRepo):
    """Scan every step script for determinism anti-patterns.

    Returns ``{listIssues, listScanned, listUnreadable}``. An
    unreadable script is reported separately rather than counted
    clean: "we could not look" and "we looked and found nothing" are
    different answers, and only the second supports a declaration.
    """
    from .manifestPaths import flistStepScriptRepoPaths
    from .repoFiles import ffilesEnsureRepoFiles
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listIssues, listScanned, listUnreadable = [], [], []
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            continue
        for sPath in flistStepScriptRepoPaths(dictStep):
            _fnScanOneScript(
                filesRepo, sPath, listIssues, listScanned, listUnreadable,
            )
    return {
        "listIssues": listIssues,
        "listScanned": sorted(set(listScanned)),
        "listUnreadable": sorted(set(listUnreadable)),
    }


def _fnScanOneScript(
    filesRepo, sPath, listIssues, listScanned, listUnreadable,
):
    """Scan one script, recording it as scanned or unreadable."""
    try:
        sSource = filesRepo.fsReadText(sPath)
    except (OSError, KeyError, ValueError, UnicodeDecodeError):
        listUnreadable.append(sPath)
        return
    listScanned.append(sPath)
    listIssues.extend(flistAuditScriptSource(sSource, sPath))


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


def flistUnansweredDeterminismQuestions(dictWorkflow):
    """Return the determinism questions this workflow has not answered.

    One question since 2026-10-17 (design ruling 2): the three that
    replaced an OR on 2026-08-30 asked about mechanisms the researcher
    could not act on, and none of them changed what ran (design fact
    8). The one question chooses an execution profile, which does.

    ANSWERING is the criterion, never a particular answer — the same
    rule the Personal AI Configuration row runs on. "Prioritize speed"
    and "Repeatable across computers" are both complete, passing
    answers. Only silence fails. The answer is recorded as its own key,
    beside the profile it names, because a value cannot say that a
    question was considered. The three legacy answers no longer count
    (D7): a project that gave them answers this question once.
    """
    return answeredQuestion.flistSelectUnansweredKeys(
        _fdictDeterminismAnswers(dictWorkflow), LIST_DETERMINISM_QUESTIONS,
    )


def _fdictDeterminismAnswers(dictWorkflow):
    """Return the workflow's ``dictDeterminism`` block, empty if absent."""
    return (dictWorkflow or {}).get("dictDeterminism") or {}


def fbWorkflowDeclaresDeterminism(dictWorkflow):
    """Return True iff the determinism question has been answered."""
    return not flistUnansweredDeterminismQuestions(dictWorkflow)


def _flistDescribeUnansweredQuestions(dictWorkflow):
    """Return one issue per unanswered determinism question.

    Named, in the question's own words, so the issue says what is being
    asked rather than that a key is missing.
    """
    return answeredQuestion.flistDescribeUnansweredQuestions(
        _fdictDeterminismAnswers(dictWorkflow), LIST_DETERMINISM_QUESTIONS,
    )


# Distribution names that mean "this environment has Intel MKL in it".
# Conda pulls `mkl` in as a NumPy dependency; the pip wheels carry it
# under these names. A pip-installed NumPy bundles OpenBLAS and matches
# none of them.
_T_MKL_DISTRIBUTION_NAMES = (
    "mkl", "mkl-service", "intel-openmp", "mkl_fft", "mkl-fft",
    "mkl_random", "mkl-random",
)

S_LOCK_FILENAME = "requirements.lock"


def fdictDetectMathsLibrary(filesRepo):
    """Report whether the dependency lock names an Intel MKL package.

    EVIDENCE, never a verdict. The execution profiles cover NumPy,
    OpenBLAS and glibc, and nothing else: Intel MKL chooses its own
    routines by processor, and no profile pins it (design, "Known
    limits"). So a project whose lock names MKL is told that its
    answer does not reach that library -- a limit stated, not hidden.

    Read from ``requirements.lock`` rather than by importing NumPy in
    the container: this module runs in the reproducibility layer with
    a files adapter and no command authority, and adding one here to
    answer a copy question would be a poor trade. The cost is real and
    stated in ``sNote``: the lock is what the environment DECLARES,
    and a hand-installed package or a base image that already carried
    MKL would not appear in it.
    """
    from .repoFiles import ffilesEnsureRepoFiles
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.fbIsFile(S_LOCK_FILENAME):
        return _fdictMathsLibraryUnknown(
            "There is no requirements.lock to read, so vaibify cannot "
            "tell which maths library this project uses."
        )
    try:
        sLock = filesRepo.fsReadText(S_LOCK_FILENAME)
    except (OSError, ValueError, UnicodeDecodeError):
        return _fdictMathsLibraryUnknown(
            "The requirements.lock could not be read, so vaibify "
            "cannot tell which maths library this project uses."
        )
    listFound = _flistFindMklDistributions(sLock)
    return {
        "bMklFound": bool(listFound),
        "listMklPackages": listFound,
        "sNote": _fsDescribeMathsLibrary(listFound),
        "sHeadline": _fsHeadlineMathsLibrary(listFound),
    }


def _fdictMathsLibraryUnknown(sNote):
    """Return the shape used when the lock cannot answer."""
    return {
        "bMklFound": None, "listMklPackages": [],
        "sNote": sNote, "sHeadline": "Maths library: could not tell.",
    }


def _fsHeadlineMathsLibrary(listFound):
    """One line for a toast, authored beside the long form.

    Two lengths rather than one because they are read in different
    places — a toast that carried the full caveat would be unreadable,
    and a row that carried only the headline would overstate. Both live
    here so the short form cannot drift into claiming more than the
    long one does.
    """
    if listFound:
        return (
            "Maths library: your dependency lock pins Intel MKL, which "
            "no execution profile covers."
        )
    return (
        "Maths library: no Intel MKL package in your dependency lock; "
        "the profiles cover the OpenBLAS a pip-installed NumPy uses."
    )


def _flistFindMklDistributions(sLock):
    """Return the MKL distribution names pinned in a lock file."""
    listFound = []
    for sLine in sLock.splitlines():
        sName = re.split(r"[=<>!;\[ ]", sLine.strip(), 1)[0].lower()
        if sName in _T_MKL_DISTRIBUTION_NAMES:
            listFound.append(sName)
    return sorted(set(listFound))


def _fsDescribeMathsLibrary(listFound):
    """Say what the lock shows, and what it cannot show."""
    if listFound:
        return (
            "Your requirements.lock pins " + ", ".join(listFound)
            + ", so this project probably uses Intel MKL. Neither "
            "answer covers it: the execution profiles set NumPy, "
            "OpenBLAS and the C math library, and MKL chooses its own "
            "routines by processor. Its CNR mode (MKL_CBWR) is the "
            "setting that pins them, and it is yours to set."
        )
    return (
        "Your requirements.lock names no Intel MKL package, so this "
        "project most likely uses OpenBLAS, which the execution "
        "profiles cover. Note this reads what the environment "
        "DECLARES: a package installed by hand, or one already in the "
        "base image, would not show up here."
    )


def flistAuditWorkflow(dictWorkflow):
    """Return workflow-level determinism issues (BLAS declaration + per-step flags).

    Steps whose `bUnseededRandomnessWarning` is True surface as
    explicit issues so the dashboard can surface them in the
    readiness card without re-running the scanner.
    """
    listIssues = list(_flistDescribeUnansweredQuestions(dictWorkflow))
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            continue
        if dictStep.get("bUnseededRandomnessWarning") is True:
            sName = dictStep.get("sName") or dictStep.get("sLabel") or "?"
            listIssues.append(
                f"Step '{sName}' has bUnseededRandomnessWarning=True"
            )
    return listIssues
