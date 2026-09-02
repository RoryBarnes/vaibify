"""Determinism audit for the PROOF L3 readiness gate.

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
    "LIST_DETERMINISM_QUESTIONS",
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


S_ACCEPT_BLAS_WAIVER_KEY = "bAcceptBlasVariance"
S_OMP_NUM_THREADS_KEY = "dOmpNumThreads"
S_MKL_CBWR_KEY = "sMklCbwr"

# The three questions, each answered separately since 2026-08-30.
#
# `sAnswerKey` records the CHOICE and `sValueKey` the value a choice
# may need. They are separate because a value cannot express "I
# considered this and the answer is no": `bAcceptBlasVariance: false`
# was written by the old form whenever it was submitted with nothing
# ticked, so it means "unanswered" and "declined" at once.
#
# `sLabel` and `sPlainQuestion` are researcher-facing and deliberately
# carry no key names. A researcher is being asked about their science,
# not about a JSON schema, and the old copy put `bAcceptBlasVariance`
# in front of them as if it were a word.
S_BLAS_ANSWER_KEY = "sBlasVarianceAnswer"
S_OMP_ANSWER_KEY = "sOmpThreadsAnswer"
S_MKL_ANSWER_KEY = "sMklModeAnswer"

S_BLAS_ACCEPTED = "accepted"
S_BLAS_REJECTED = "rejected"
S_OMP_PINNED = "pinned"
S_OMP_UNPINNED = "unpinned"
S_MKL_PINNED = "pinned"
S_MKL_NOT_USED = "not-used"

LIST_DETERMINISM_QUESTIONS = (
    {
        "sKey": "blasVariance",
        "sAnswerKey": S_BLAS_ANSWER_KEY,
        "sValueKey": "",
        "sAnswerNeedingValue": "",
        "tAnswers": (S_BLAS_ACCEPTED, S_BLAS_REJECTED),
        "sLabel": "Last-digit numeric differences",
        "sPlainQuestion": (
            "NumPy and SciPy do their heavy arithmetic in a maths "
            "library underneath, which splits big sums across several "
            "threads and adds the pieces back in whatever order they "
            "finish. Floating-point addition is not exactly "
            "associative, so the final digits of a result can differ "
            "from run to run on the very same machine. Do you accept "
            "those differences between your run and a rerun?"
        ),
    },
    {
        "sKey": "ompThreads",
        "sAnswerKey": S_OMP_ANSWER_KEY,
        "sValueKey": S_OMP_NUM_THREADS_KEY,
        "sAnswerNeedingValue": S_OMP_PINNED,
        "tAnswers": (S_OMP_PINNED, S_OMP_UNPINNED),
        "sLabel": "Thread count",
        "sPlainQuestion": (
            "How many threads the maths library uses decides how "
            "those pieces are split, so fixing the number removes one "
            "source of that reordering. Leaving it free lets a rerun "
            "use whatever the machine offers, which is usually faster "
            "and usually fine. Is the thread count fixed?"
        ),
    },
    {
        "sKey": "mklMode",
        "sAnswerKey": S_MKL_ANSWER_KEY,
        "sValueKey": S_MKL_CBWR_KEY,
        "sAnswerNeedingValue": S_MKL_PINNED,
        "tAnswers": (S_MKL_PINNED, S_MKL_NOT_USED),
        "sLabel": "Intel maths library (MKL)",
        "sPlainQuestion": (
            "There is more than one such maths library. Most "
            "installations use OpenBLAS; some use Intel's Math "
            "Kernel Library, MKL. MKL additionally picks different "
            "internal routines depending on the exact processor it "
            "finds, so with MKL the same input can give different "
            "final digits on a different MACHINE, not just a "
            "different run. It has a setting that turns that off. "
            "Does this project use MKL, and if so is that setting on?"
        ),
    },
)

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

    THE 2026-08-30 RULING, and it changed what a declaration means.
    The gate used to be an OR: any one of a BLAS waiver, a pinned
    thread count or an MKL mode satisfied it. That let a project
    attest at Level 3 having answered one third of the question —
    pinning the thread count says nothing about whether last-digit
    variance is acceptable, and the two are independent sources of
    run-to-run difference. Each is now asked separately.

    ANSWERING is the criterion, never a particular answer — the same
    rule the Personal AI Configuration row runs on. "I do not accept
    numeric variance", "threads are not pinned" and "this project does
    not use MKL" are complete, passing answers. Only silence fails.

    That distinction is why the answers are recorded as their own keys
    rather than inferred from the values. ``bAcceptBlasVariance:
    false`` was the shape the old form wrote when submitted with
    nothing ticked, so it cannot be told apart from never having
    chosen — and reading it as a deliberate "no" would attest a claim
    the researcher never made.
    """
    dictDeterminism = (dictWorkflow or {}).get("dictDeterminism") or {}
    return [
        dictQuestion["sKey"]
        for dictQuestion in LIST_DETERMINISM_QUESTIONS
        if not _fbQuestionIsAnswered(dictDeterminism, dictQuestion)
    ]


def _fbQuestionIsAnswered(dictDeterminism, dictQuestion):
    """Return True iff one question carries a recorded, valid answer.

    An answer that names a pinned value must CARRY that value:
    "threads are pinned" with no count is not an answer, it is half of
    one, and a rerun could not act on it.
    """
    sAnswer = dictDeterminism.get(dictQuestion["sAnswerKey"])
    if sAnswer not in dictQuestion["tAnswers"]:
        return False
    if sAnswer != dictQuestion["sAnswerNeedingValue"]:
        return True
    return _fbValueIsPresent(dictDeterminism.get(dictQuestion["sValueKey"]))


def _fbValueIsPresent(jsonValue):
    """Return True iff a pinned value was actually supplied."""
    if jsonValue is None:
        return False
    if isinstance(jsonValue, str):
        return jsonValue.strip() != ""
    return True


def fbWorkflowDeclaresDeterminism(dictWorkflow):
    """Return True iff every determinism question has been answered."""
    return not flistUnansweredDeterminismQuestions(dictWorkflow)


def _flistDescribeUnansweredQuestions(dictWorkflow):
    """Return one issue per unanswered determinism question.

    Named individually because they are separate questions with
    separate answers; a single "determinism is not declared" line
    cannot tell a researcher which of the three is still open, and the
    row that reports it now carries a marker each.
    """
    dictByKey = {
        dictQuestion["sKey"]: dictQuestion
        for dictQuestion in LIST_DETERMINISM_QUESTIONS
    }
    listIssues = []
    for sKey in flistUnansweredDeterminismQuestions(dictWorkflow):
        dictQuestion = dictByKey[sKey]
        listIssues.append(
            dictQuestion["sLabel"] + " — not answered yet. "
            + dictQuestion["sPlainQuestion"]
        )
    return listIssues


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

    EVIDENCE, never a verdict, and the distinction is the whole point.
    A researcher was asked whether their project uses MKL and had no
    way to find out — the script scan beside the question answers a
    different question entirely (clock seeds, urandom), so running it
    told them nothing and reasonably read as "nothing to see here".

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
            "Maths library: your dependency lock pins Intel MKL, so "
            "the MKL question applies to this project."
        )
    return (
        "Maths library: no Intel MKL package in your dependency lock, "
        "so the MKL question most likely does not apply."
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
            + ", so this project probably does use Intel MKL. The "
            "reproducibility setting is worth turning on."
        )
    return (
        "Your requirements.lock names no Intel MKL package, so this "
        "project most likely uses OpenBLAS and the MKL question does "
        "not apply. Note this reads what the environment DECLARES: a "
        "package installed by hand, or one already in the base image, "
        "would not show up here."
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
