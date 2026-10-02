"""Every text file and every captured command output names its encoding.

Without one, bytes are decoded in the process locale. That locale is C
after a VTK file is read or written, and on a minimal host, so the first
non-ASCII byte in a file or a command's output (a file name in a git
listing, a unit symbol in a log) raised ``UnicodeDecodeError`` far from
its cause. Binary opens are exempt, as are the library ``open`` calls
that are not file reads (a FITS file, a URL opener).
"""

import ast
import pathlib

import pytest

import vaibify

PATH_PACKAGE = pathlib.Path(vaibify.__file__).resolve().parent
LIST_SUBPROCESS_FUNCTIONS = {
    "run", "Popen", "check_output", "call", "check_call",
}
SET_NOT_TEXT_RECEIVERS = {
    "os", "io", "webbrowser", "tarfile", "zipfile", "gzip", "codecs",
    "Image", "fitsLib", "_OBJECT_OPENER", "urllib", "opener",
}


def _fbHasKeyword(nodeCall, sName):
    return any(keyword.arg == sName for keyword in nodeCall.keywords)


def _fbModeIsBinary(nodeCall):
    sMode = None
    if len(nodeCall.args) >= 2 and isinstance(nodeCall.args[1], ast.Constant):
        sMode = nodeCall.args[1].value
    for keyword in nodeCall.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            sMode = keyword.value.value
    return isinstance(sMode, str) and "b" in sMode


def _fbKeywordIsOn(nodeCall, sName):
    for keyword in nodeCall.keywords:
        if keyword.arg == sName:
            return not (isinstance(keyword.value, ast.Constant)
                        and keyword.value.value in (False, None))
    return False


def _fbOpensTextWithoutEncoding(nodeCall):
    funcCalled = nodeCall.func
    if isinstance(funcCalled, ast.Name) and funcCalled.id == "open":
        return not _fbModeIsBinary(nodeCall) and not _fbHasKeyword(
            nodeCall, "encoding")
    if not isinstance(funcCalled, ast.Attribute):
        return False
    if funcCalled.attr in ("read_text", "write_text"):
        return not _fbHasKeyword(nodeCall, "encoding")
    if funcCalled.attr in ("open", "fdopen") and isinstance(
        funcCalled.value, ast.Name
    ) and funcCalled.value.id not in SET_NOT_TEXT_RECEIVERS - {"os"}:
        if funcCalled.value.id == "os" and funcCalled.attr != "fdopen":
            return False
        return not _fbModeIsBinary(nodeCall) and not _fbHasKeyword(
            nodeCall, "encoding")
    return False


def _fbRunsTextSubprocessWithoutEncoding(nodeCall):
    funcCalled = nodeCall.func
    if not (isinstance(funcCalled, ast.Attribute)
            and funcCalled.attr in LIST_SUBPROCESS_FUNCTIONS
            and isinstance(funcCalled.value, ast.Name)
            and funcCalled.value.id == "subprocess"):
        return False
    bText = _fbKeywordIsOn(nodeCall, "text") or _fbKeywordIsOn(
        nodeCall, "universal_newlines")
    return bText and not _fbHasKeyword(nodeCall, "encoding")


def flistFindUndeclaredEncodings(sSourceText):
    """Return (line, kind) for each text I/O call naming no encoding."""
    listFound = []
    for node in ast.walk(ast.parse(sSourceText)):
        if not isinstance(node, ast.Call):
            continue
        if _fbOpensTextWithoutEncoding(node):
            listFound.append((node.lineno, "text file"))
        elif _fbRunsTextSubprocessWithoutEncoding(node):
            listFound.append((node.lineno, "text subprocess"))
    return listFound


@pytest.mark.falsification
def testNoTextFileOrCommandOutputIsDecodedInTheProcessLocale():
    """Kills: dropping an encoding from a text open or a text command."""
    listOffenders = []
    for pathSource in sorted(PATH_PACKAGE.rglob("*.py")):
        listFound = flistFindUndeclaredEncodings(
            pathSource.read_text(encoding="utf-8"))
        listOffenders.extend(
            f"{pathSource.relative_to(PATH_PACKAGE).as_posix()}:{iLine} "
            f"({sKind})" for iLine, sKind in listFound)
    assert listOffenders == [], listOffenders


def testTheScannerFlagsEachShapeItIsMeantToCatch():
    sSource = (
        "import subprocess\n"
        "open('a')\n"
        "open('a', 'w')\n"
        "p.read_text()\n"
        "p.write_text('x')\n"
        "os.fdopen(3, 'r+')\n"
        "subprocess.run(['x'], text=True)\n"
        "subprocess.Popen(['x'], universal_newlines=True)\n"
    )
    assert [iLine for iLine, _ in flistFindUndeclaredEncodings(sSource)] == (
        [2, 3, 4, 5, 6, 7, 8])


def testTheScannerAcceptsWhatIsAlreadyDeclaredOrBinary():
    sSource = (
        "import subprocess\n"
        "open('a', encoding='utf-8')\n"
        "open('a', 'rb')\n"
        "open('a', mode='wb')\n"
        "p.read_text(encoding='utf-8')\n"
        "os.fdopen(3, 'wb')\n"
        "fitsLib.open('a')\n"
        "subprocess.run(['x'], capture_output=True)\n"
        "subprocess.run(['x'], text=True, encoding='utf-8')\n"
    )
    assert flistFindUndeclaredEncodings(sSource) == []
