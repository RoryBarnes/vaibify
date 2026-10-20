#!/usr/bin/env python3
"""Find KNOWN patterns by which a script can fail silently or unhelpfully.

Usage: checkGracefulExits.py <file-or-directory> [...]

Read-only and deterministic; standard library only. It finds the
patterns below and nothing else, so it cannot show that a script exits
gracefully on every failure.

Python (parsed with ``ast``):
  bare-except           ``except:``, which also swallows Ctrl-C and exits
  silent-broad-except   ``except Exception``/``BaseException`` whose body
                        only passes, continues, breaks or returns None
  exit-without-message  ``sys.exit``/``exit``/``quit``/``os._exit``/
                        ``raise SystemExit`` on an error path (inside an
                        except handler, or with a non-zero literal status)
                        with no message argument and no message printed
                        or logged by the statement just before it
  unchecked-subprocess  ``subprocess.run`` without ``check=True``, or
                        ``subprocess.call``, whose result is never tested
                        (a result returned or passed to another call is
                        the caller's to test and is not reported)
  unguarded-open        ``open()`` in a script's entry point -- module-level
                        code and the module functions it calls directly --
                        outside the body of any ``try`` that has a handler

Shell (``.sh``/``.bash``/``.zsh``/``.ksh`` or a shell shebang):
  missing-strict-mode   errexit, nounset or pipefail never set
  shellcheck-SC<n>      each shellcheck finding, when shellcheck is
                        installed; otherwise the file is reported as
                        PARTLY CHECKED

Every other source language is reported as NOT CHECKED, naming the
linter that would check it. A directory is searched recursively for
source files; data files in it are skipped silently.

Output: one line per finding, ``path:line: rule: message``, then the
coverage lines (``path: NOT CHECKED: ...``, ``path: PARTLY CHECKED:
...``); a summary goes to stderr. Exit status: 0 no finding in what was
checked (read the coverage lines -- NOT CHECKED is not clean), 1
findings, 2 the checker could not do its job (a missing or unreadable
path, a file Python cannot parse, a shellcheck failure).
"""

import ast
import os
import re
import shutil
import subprocess
import sys


SET_SHELL_EXTENSIONS = {".sh", ".bash", ".zsh", ".ksh"}
DICT_UNCHECKED_LANGUAGES = {
    ".r": ("R", "lintr"),
    ".jl": ("Julia", "JET.jl"),
    ".c": ("C", "clang-tidy"),
    ".h": ("C", "clang-tidy"),
    ".cc": ("C++", "clang-tidy"),
    ".cpp": ("C++", "clang-tidy"),
    ".cxx": ("C++", "clang-tidy"),
    ".hpp": ("C++", "clang-tidy"),
    ".f": ("Fortran", "gfortran -Wall -Wextra"),
    ".f90": ("Fortran", "gfortran -Wall -Wextra"),
    ".f95": ("Fortran", "gfortran -Wall -Wextra"),
    ".f03": ("Fortran", "gfortran -Wall -Wextra"),
    ".js": ("JavaScript", "eslint"),
    ".mjs": ("JavaScript", "eslint"),
    ".ts": ("TypeScript", "eslint"),
    ".m": ("MATLAB", "checkcode"),
    ".ipynb": ("Jupyter notebook", "nbqa (runs Python linters on notebooks)"),
    ".rs": ("Rust", "cargo clippy"),
    ".go": ("Go", "go vet"),
    ".pl": ("Perl", "perlcritic"),
    ".rb": ("Ruby", "rubocop"),
    ".java": ("Java", "SpotBugs"),
    ".idl": ("IDL", "no common linter"),
}
SET_SKIPPED_DIRECTORIES = {"__pycache__", "node_modules"}
SET_BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}
SET_EXIT_CALL_NAMES = {"sys.exit", "exit", "quit", "os._exit"}
SET_SUBPROCESS_CALL_NAMES = {"subprocess.run", "subprocess.call"}
SET_RETURN_CODE_ATTRIBUTES = {"returncode", "check_returncode"}
SET_MESSAGE_CALL_NAMES = {
    "print", "sys.stderr.write", "warnings.warn", "click.echo",
    "click.secho", "traceback.print_exc",
}
SET_MESSAGE_METHOD_NAMES = {
    "error", "exception", "critical", "warning", "warn", "fatal",
}
SET_REQUIRED_SHELL_OPTIONS = ("errexit", "nounset", "pipefail")
DICT_SHELL_OPTION_LETTERS = {"e": "errexit", "u": "nounset"}
S_SHELL_SHEBANG_PATTERN = r"^#!.*\b(ba|z|k|da)?sh\b"
S_SHELLCHECK_LINE_PATTERN = r"^(.*?):(\d+):\d+: \w+: (.*) \[(SC\d+)\]$"


class CheckerError(Exception):
    """The checker could not examine a path; nothing in it was checked."""


# ---------------------------------------------------------------- Python


def fdictBuildParentMap(nodeTree):
    """Map every node in the tree to the node that contains it."""
    dictParents = {}
    for nodeParent in ast.walk(nodeTree):
        for nodeChild in ast.iter_child_nodes(nodeParent):
            dictParents[nodeChild] = nodeParent
    return dictParents


def fdictBuildImportAliases(nodeTree):
    """Map each locally bound import name to its canonical dotted name."""
    dictAliases = {}
    for node in ast.walk(nodeTree):
        if isinstance(node, ast.Import):
            for nodeAlias in node.names:
                if nodeAlias.asname:
                    dictAliases[nodeAlias.asname] = nodeAlias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for nodeAlias in node.names:
                sLocalName = nodeAlias.asname or nodeAlias.name
                dictAliases[sLocalName] = node.module + "." + nodeAlias.name
    return dictAliases


def fsCanonicalName(nodeExpression, dictAliases):
    """Return ``a.b.c`` for a Name/Attribute chain, resolving imports."""
    listParts = []
    while isinstance(nodeExpression, ast.Attribute):
        listParts.insert(0, nodeExpression.attr)
        nodeExpression = nodeExpression.value
    if not isinstance(nodeExpression, ast.Name):
        return ""
    listParts.insert(0, dictAliases.get(nodeExpression.id, nodeExpression.id))
    return ".".join(listParts)


def fbHandlerIsBroad(nodeHandler):
    """True when an except clause names Exception or BaseException."""
    listTypes = (nodeHandler.type.elts
                 if isinstance(nodeHandler.type, ast.Tuple)
                 else [nodeHandler.type])
    for nodeType in listTypes:
        sName = nodeType.attr if isinstance(nodeType, ast.Attribute) else (
            nodeType.id if isinstance(nodeType, ast.Name) else "")
        if sName in SET_BROAD_EXCEPTION_NAMES:
            return True
    return False


def fbStatementIsSilent(nodeStatement):
    """True for a statement that discards an error without a trace."""
    if isinstance(nodeStatement, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(nodeStatement, ast.Expr):
        return isinstance(nodeStatement.value, ast.Constant)
    if isinstance(nodeStatement, ast.Return):
        return nodeStatement.value is None or (
            isinstance(nodeStatement.value, ast.Constant)
            and nodeStatement.value.value is None)
    return False


def flistFindBroadHandlers(nodeTree, sPath):
    """Report bare excepts and broad excepts that swallow the error."""
    listFindings = []
    for node in ast.walk(nodeTree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            listFindings.append((
                sPath, node.lineno, "bare-except",
                "bare `except:` also catches Ctrl-C and sys.exit; catch "
                "the specific exceptions this block expects"))
        elif fbHandlerIsBroad(node) and all(
                fbStatementIsSilent(nodeStatement)
                for nodeStatement in node.body):
            listFindings.append((
                sPath, node.lineno, "silent-broad-except",
                "`except Exception` discards the error; name what failed "
                "and re-raise or exit non-zero"))
    return listFindings


def fbArgumentIsMessage(nodeArgument):
    """True when an exit argument is text, which Python prints."""
    if isinstance(nodeArgument, ast.JoinedStr):
        return True
    if isinstance(nodeArgument, ast.Constant):
        return isinstance(nodeArgument.value, str)
    if isinstance(nodeArgument, ast.BinOp):
        return fbArgumentIsMessage(nodeArgument.left)
    if isinstance(nodeArgument, ast.Call) and isinstance(
            nodeArgument.func, ast.Attribute):
        return (nodeArgument.func.attr == "format"
                and fbArgumentIsMessage(nodeArgument.func.value))
    return False


def fbArgumentIsFailureStatus(nodeArgument):
    """True for a literal non-zero integer exit status."""
    return (isinstance(nodeArgument, ast.Constant)
            and isinstance(nodeArgument.value, int)
            and not isinstance(nodeArgument.value, bool)
            and nodeArgument.value != 0)


def fbIsInsideHandler(node, dictParents):
    """True when the node sits in an except handler of its own function."""
    while node in dictParents:
        node = dictParents[node]
        if isinstance(node, ast.ExceptHandler):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            return False
    return False


def fnodeEnclosingStatement(node, dictParents):
    """Return the statement that contains the node."""
    while not isinstance(node, ast.stmt):
        node = dictParents[node]
    return node


def fbIsMessageStatement(nodeStatement, dictAliases):
    """True for a statement that prints, logs or warns."""
    if not (isinstance(nodeStatement, ast.Expr)
            and isinstance(nodeStatement.value, ast.Call)):
        return False
    nodeFunction = nodeStatement.value.func
    if fsCanonicalName(nodeFunction, dictAliases) in SET_MESSAGE_CALL_NAMES:
        return True
    return (isinstance(nodeFunction, ast.Attribute)
            and nodeFunction.attr in SET_MESSAGE_METHOD_NAMES)


def fbPrecededByMessage(nodeStatement, dictParents, dictAliases):
    """True when the statement just before this one says something."""
    for _, listSiblings in ast.iter_fields(dictParents[nodeStatement]):
        if isinstance(listSiblings, list) and nodeStatement in listSiblings:
            iIndex = listSiblings.index(nodeStatement)
            return iIndex > 0 and fbIsMessageStatement(
                listSiblings[iIndex - 1], dictAliases)
    return False


def flistExitSites(nodeTree, dictAliases):
    """Return (node, argument-or-None) for every exit call or SystemExit."""
    listSites = []
    for node in ast.walk(nodeTree):
        if isinstance(node, ast.Call) and fsCanonicalName(
                node.func, dictAliases) in SET_EXIT_CALL_NAMES:
            listSites.append((node, node.args[0] if node.args else None))
        elif isinstance(node, ast.Raise) and node.exc is not None:
            nodeException = node.exc
            nodeCallee = (nodeException.func
                          if isinstance(nodeException, ast.Call)
                          else nodeException)
            if fsCanonicalName(nodeCallee, dictAliases) == "SystemExit":
                listArguments = (nodeException.args
                                 if isinstance(nodeException, ast.Call)
                                 else [])
                listSites.append(
                    (node, listArguments[0] if listArguments else None))
    return listSites


def flistFindExitsWithoutMessage(nodeTree, dictParents, dictAliases, sPath):
    """Report error-path exits that never say what failed."""
    listFindings = []
    for node, nodeArgument in flistExitSites(nodeTree, dictAliases):
        if nodeArgument is not None and fbArgumentIsMessage(nodeArgument):
            continue
        bErrorPath = fbIsInsideHandler(node, dictParents) or (
            nodeArgument is not None
            and fbArgumentIsFailureStatus(nodeArgument))
        if not bErrorPath:
            continue
        nodeStatement = fnodeEnclosingStatement(node, dictParents)
        if fbPrecededByMessage(nodeStatement, dictParents, dictAliases):
            continue
        listFindings.append((
            sPath, node.lineno, "exit-without-message",
            "exits on an error path without saying what failed; print a "
            "message naming the failing resource to stderr first"))
    return listFindings


def fnodeEnclosingScope(node, dictParents):
    """Return the function, lambda or module whose names the node uses."""
    while node in dictParents:
        node = dictParents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            return node
    return node


def fbNameIsTested(sName, nodeScope, dictParents, bAnyUseCounts):
    """True when a bound result is read later as a return code."""
    for node in ast.walk(nodeScope):
        if not (isinstance(node, ast.Name) and node.id == sName
                and isinstance(node.ctx, ast.Load)):
            continue
        if bAnyUseCounts:
            return True
        nodeParent = dictParents.get(node)
        if (isinstance(nodeParent, ast.Attribute)
                and nodeParent.attr in SET_RETURN_CODE_ATTRIBUTES):
            return True
    return False


def fbSubprocessResultIsHandled(nodeCall, sCallName, dictParents):
    """True when the result is tested, returned, or handed to a call."""
    nodeParent = dictParents[nodeCall]
    bIsCall = sCallName == "subprocess.call"
    if isinstance(nodeParent, ast.Attribute):
        return nodeParent.attr in SET_RETURN_CODE_ATTRIBUTES
    if isinstance(nodeParent, (ast.Return, ast.Call, ast.Await)):
        return True
    if isinstance(nodeParent, (ast.Compare, ast.If, ast.While, ast.IfExp,
                               ast.BoolOp, ast.UnaryOp, ast.Assert)):
        return bIsCall
    if isinstance(nodeParent, ast.Expr):
        return False
    listTargets = (nodeParent.targets if isinstance(nodeParent, ast.Assign)
                   else [getattr(nodeParent, "target", None)])
    if len(listTargets) == 1 and isinstance(listTargets[0], ast.Name):
        return fbNameIsTested(
            listTargets[0].id, fnodeEnclosingScope(nodeCall, dictParents),
            dictParents, bIsCall)
    return True


def fsCheckKeyword(nodeCall):
    """Return 'checked', 'unchecked' or 'unknown' from the check= keyword."""
    for nodeKeyword in nodeCall.keywords:
        if nodeKeyword.arg is None:
            return "unknown"
        if nodeKeyword.arg == "check":
            if not isinstance(nodeKeyword.value, ast.Constant):
                return "unknown"
            return "checked" if nodeKeyword.value.value is True else (
                "unchecked")
    return "unchecked"


def flistFindUncheckedSubprocess(nodeTree, dictParents, dictAliases, sPath):
    """Report subprocess calls whose failure nobody would notice."""
    listFindings = []
    for node in ast.walk(nodeTree):
        if not isinstance(node, ast.Call):
            continue
        sCallName = fsCanonicalName(node.func, dictAliases)
        if sCallName not in SET_SUBPROCESS_CALL_NAMES:
            continue
        if sCallName == "subprocess.run" and fsCheckKeyword(node) != (
                "unchecked"):
            continue
        if fbSubprocessResultIsHandled(node, sCallName, dictParents):
            continue
        sRemedy = ("whose return code is never tested"
                   if sCallName == "subprocess.call"
                   else "without check=True or a return-code test")
        listFindings.append((
            sPath, node.lineno, "unchecked-subprocess",
            "`" + sCallName + "` " + sRemedy
            + "; a failing command goes unnoticed"))
    return listFindings


def fiterWalkSkippingDefinitions(listStatements):
    """Yield every node under the statements, not entering def/class."""
    listPending = list(listStatements)
    while listPending:
        node = listPending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        yield node
        listPending.extend(ast.iter_child_nodes(node))


def flistEntryPointBodies(nodeTree):
    """Return module-level code plus the module functions it calls."""
    listModuleCode = [nodeStatement for nodeStatement in nodeTree.body
                      if not isinstance(nodeStatement, (
                          ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef))]
    setCalledNames = {
        node.func.id for node in fiterWalkSkippingDefinitions(listModuleCode)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    listBodies = [listModuleCode]
    for nodeStatement in nodeTree.body:
        if isinstance(nodeStatement, (ast.FunctionDef,
                                      ast.AsyncFunctionDef)) and (
                nodeStatement.name in setCalledNames):
            listBodies.append(nodeStatement.body)
    return listBodies


def fbIsInsideGuardedTry(node, dictParents):
    """True when the node is in the body of a try that has a handler."""
    nodeChild = node
    while nodeChild in dictParents:
        nodeParent = dictParents[nodeChild]
        if isinstance(nodeParent, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.Module)):
            return False
        if (isinstance(nodeParent, ast.Try) or type(nodeParent).__name__
                == "TryStar") and nodeParent.handlers and (
                    nodeChild in nodeParent.body):
            return True
        nodeChild = nodeParent
    return False


def flistFindUnguardedOpens(nodeTree, dictParents, dictAliases, sPath):
    """Report open() in a script's entry point with no handler around it."""
    listFindings = []
    for listBody in flistEntryPointBodies(nodeTree):
        for node in fiterWalkSkippingDefinitions(listBody):
            if not (isinstance(node, ast.Call) and fsCanonicalName(
                    node.func, dictAliases) in ("open", "io.open")):
                continue
            if fbIsInsideGuardedTry(node, dictParents):
                continue
            listFindings.append((
                sPath, node.lineno, "unguarded-open",
                "`open()` in the script's entry point with no handler; a "
                "missing or unreadable file ends in a traceback instead of "
                "a message saying what the script expected"))
    return listFindings


def flistCheckPythonSource(sPath, sSource):
    """Return every finding in one Python file; raise on a parse failure."""
    try:
        nodeTree = ast.parse(sSource, filename=sPath)
    except (SyntaxError, ValueError) as error:
        raise CheckerError(
            "cannot parse " + sPath + ": " + str(error)
            + "; nothing in it was checked") from error
    dictParents = fdictBuildParentMap(nodeTree)
    dictAliases = fdictBuildImportAliases(nodeTree)
    return (flistFindBroadHandlers(nodeTree, sPath)
            + flistFindExitsWithoutMessage(
                nodeTree, dictParents, dictAliases, sPath)
            + flistFindUncheckedSubprocess(
                nodeTree, dictParents, dictAliases, sPath)
            + flistFindUnguardedOpens(
                nodeTree, dictParents, dictAliases, sPath))


# ----------------------------------------------------------------- Shell


def fsetEnabledShellOptions(sSource):
    """Return the errexit/nounset/pipefail options a script turns on."""
    setEnabled = set()
    listLines = sSource.splitlines()
    listOptionLines = [listLines[0][2:]] if listLines and listLines[
        0].startswith("#!") else []
    listOptionLines += [sLine.split("#", 1)[0].strip()[len("set "):]
                        for sLine in listLines
                        if re.match(r"^\s*set\s+-", sLine)]
    for sOptions in listOptionLines:
        listTokens = sOptions.split()
        for iIndex, sToken in enumerate(listTokens):
            if not sToken.startswith("-") or sToken.startswith("--"):
                continue
            for sLetter in sToken[1:]:
                if sLetter in DICT_SHELL_OPTION_LETTERS:
                    setEnabled.add(DICT_SHELL_OPTION_LETTERS[sLetter])
                elif sLetter == "o" and iIndex + 1 < len(listTokens):
                    setEnabled.add(listTokens[iIndex + 1])
    return setEnabled


def flistCheckShellStrictMode(sPath, sSource):
    """Report a shell script that never turns on strict mode."""
    setEnabled = fsetEnabledShellOptions(sSource)
    listMissing = [sOption for sOption in SET_REQUIRED_SHELL_OPTIONS
                   if sOption not in setEnabled]
    if not listMissing:
        return []
    return [(sPath, 1, "missing-strict-mode",
             "no `set -euo pipefail` (missing: " + ", ".join(listMissing)
             + "); a failing command lets the script carry on")]


def flistRunShellcheck(sPath):
    """Return shellcheck's findings for one script, in this tool's format."""
    try:
        processShellcheck = subprocess.run(
            ["shellcheck", "-f", "gcc", sPath],
            capture_output=True, text=True, check=False)
    except OSError as error:
        raise CheckerError(
            "cannot run shellcheck on " + sPath + ": " + str(error)) from error
    if processShellcheck.returncode not in (0, 1):
        raise CheckerError(
            "shellcheck failed on " + sPath + " (exit "
            + str(processShellcheck.returncode) + "): "
            + processShellcheck.stderr.strip())
    listFindings = []
    for sLine in processShellcheck.stdout.splitlines():
        matchLine = re.match(S_SHELLCHECK_LINE_PATTERN, sLine)
        if matchLine:
            listFindings.append((
                sPath, int(matchLine.group(2)),
                "shellcheck-" + matchLine.group(4), matchLine.group(3)))
    return listFindings


# ------------------------------------------------------------- Dispatch


def fsReadSource(sPath):
    """Return a file's text, or raise CheckerError naming it."""
    try:
        with open(sPath, encoding="utf-8", errors="replace") as fileSource:
            return fileSource.read()
    except OSError as error:
        raise CheckerError("cannot read " + sPath + ": " + str(error)) from error


def fsClassifyFile(sPath):
    """Return 'python', 'shell', an unchecked extension, or ''."""
    sExtension = os.path.splitext(sPath)[1].lower()
    if sExtension == ".py":
        return "python"
    if sExtension in SET_SHELL_EXTENSIONS:
        return "shell"
    if sExtension in DICT_UNCHECKED_LANGUAGES:
        return sExtension
    if sExtension:
        return ""
    try:
        with open(sPath, "rb") as fileSource:
            sFirstLine = fileSource.readline(200).decode("utf-8", "replace")
    except OSError:
        return ""
    if re.match(S_SHELL_SHEBANG_PATTERN, sFirstLine):
        return "shell"
    return "python" if re.match(r"^#!.*\bpython", sFirstLine) else ""


def flistExpandPaths(listPaths):
    """Return (path, explicitly-named) for each file under the paths."""
    listFiles = []
    for sPath in listPaths:
        if os.path.isfile(sPath):
            listFiles.append((sPath, True))
        elif os.path.isdir(sPath):
            for sDirectory, listSubdirectories, listNames in os.walk(sPath):
                listSubdirectories[:] = sorted(
                    sName for sName in listSubdirectories
                    if not sName.startswith(".")
                    and sName not in SET_SKIPPED_DIRECTORIES)
                listFiles.extend((os.path.join(sDirectory, sName), False)
                                 for sName in sorted(listNames))
        else:
            raise CheckerError("no such file or directory: " + sPath)
    return listFiles


def ftCheckOneFile(sPath, bExplicit, bHaveShellcheck):
    """Return (findings, coverage lines) for one file."""
    sKind = fsClassifyFile(sPath)
    if sKind == "python":
        return flistCheckPythonSource(sPath, fsReadSource(sPath)), []
    if sKind == "shell":
        listFindings = flistCheckShellStrictMode(sPath, fsReadSource(sPath))
        if bHaveShellcheck:
            return listFindings + flistRunShellcheck(sPath), []
        return listFindings, [
            sPath + ": PARTLY CHECKED: shellcheck is not installed; only "
            "missing-strict-mode was checked"]
    if sKind:
        sLanguage, sLinter = DICT_UNCHECKED_LANGUAGES[sKind]
        return [], [sPath + ": NOT CHECKED: no " + sLanguage
                    + " checker here; " + sLinter + " would check it"]
    if bExplicit:
        return [], [sPath + ": NOT CHECKED: unrecognized file type; no "
                    "checker is known for it"]
    return [], []


def main(listArguments=None):
    listArguments = sys.argv[1:] if listArguments is None else listArguments
    if not listArguments or listArguments[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 0 if listArguments else 2
    listFindings, listCoverage, bCheckerError = [], [], False
    try:
        listFiles = flistExpandPaths(listArguments)
    except CheckerError as error:
        print("checkGracefulExits: " + str(error), file=sys.stderr)
        return 2
    bHaveShellcheck = shutil.which("shellcheck") is not None
    for sPath, bExplicit in listFiles:
        try:
            listFileFindings, listFileCoverage = ftCheckOneFile(
                sPath, bExplicit, bHaveShellcheck)
        except CheckerError as error:
            print("checkGracefulExits: " + str(error), file=sys.stderr)
            bCheckerError = True
            continue
        listFindings.extend(listFileFindings)
        listCoverage.extend(listFileCoverage)
    for sPath, iLine, sRule, sMessage in sorted(listFindings):
        print(sPath + ":" + str(iLine) + ": " + sRule + ": " + sMessage)
    for sLine in listCoverage:
        print(sLine)
    print("checkGracefulExits: " + str(len(listFindings)) + " finding(s); "
          + str(len(listCoverage)) + " file(s) not fully checked",
          file=sys.stderr)
    if bCheckerError:
        return 2
    return 1 if listFindings else 0


if __name__ == "__main__":
    sys.exit(main())
