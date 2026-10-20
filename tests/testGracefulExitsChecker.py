"""The graceful-exits skill's checker finds each known pattern, and only it.

Every rule is driven by one script that must trigger it and one that
must not, because a checker that reports everything is as useless as
one that reports nothing. The coverage contract is tested on its own:
a language the checker cannot read is reported NOT CHECKED, never
passed over as clean.
"""

import importlib.util
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from vaibify.resources import fpathContainerImageRoot


PATH_CHECKER = (fpathContainerImageRoot() / "skills" / "graceful-exits"
                / "checkGracefulExits.py")


def _fmoduleLoadChecker():
    specChecker = importlib.util.spec_from_file_location(
        "checkGracefulExits", PATH_CHECKER)
    moduleChecker = importlib.util.module_from_spec(specChecker)
    specChecker.loader.exec_module(moduleChecker)
    return moduleChecker


checkGracefulExits = _fmoduleLoadChecker()


def _fsWriteScript(pathDirectory, sName, sSource):
    pathScript = pathDirectory / sName
    pathScript.write_text(textwrap.dedent(sSource), encoding="utf-8")
    return str(pathScript)


def _ftRunChecker(listPaths, capsys):
    iExit = checkGracefulExits.main(listPaths)
    return iExit, capsys.readouterr().out


def _flistRulesFound(pathDirectory, sSource, capsys, sName="script.py"):
    sPath = _fsWriteScript(pathDirectory, sName, sSource)
    _, sOutput = _ftRunChecker([sPath], capsys)
    return [sLine.split(": ")[1] for sLine in sOutput.splitlines()
            if sLine.startswith(sPath + ":")
            and sLine[len(sPath) + 1].isdigit()]


# ---------------------------------------------------------------- Python


def testABareExceptIsReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        try:
            iValue = int("3")
        except:
            pass
        """, capsys) == ["bare-except"]


def testANamedExceptIsNotReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        try:
            iValue = int("3")
        except ValueError:
            pass
        """, capsys) == []


def testABroadExceptThatSwallowsTheErrorIsReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        def fnLoadAll(listPaths):
            for sPath in listPaths:
                try:
                    fnLoad(sPath)
                except (OSError, Exception):
                    continue

        def fnLoad(sPath):
            try:
                fnParse(sPath)
            except BaseException:
                return None
        """, capsys) == ["silent-broad-except", "silent-broad-except"]


def testABroadExceptThatReportsOrReraisesIsNotReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        import logging, sys
        logger = logging.getLogger(__name__)

        def fnLoad(sPath):
            try:
                fnParse(sPath)
            except Exception as error:
                print("cannot parse " + sPath, file=sys.stderr)
                raise
            try:
                fnParse(sPath)
            except Exception:
                logger.exception("cannot parse %s", sPath)
        """, capsys) == []


@pytest.mark.falsification
def testAnErrorPathExitWithNoMessageIsReported(tmp_path, capsys):
    """Kills: treating an exit inside an except handler as a success path."""
    assert _flistRulesFound(tmp_path, """
        import sys

        def fnLoad(sPath):
            try:
                return open(sPath).read()
            except OSError:
                sys.exit(1)
        """, capsys) == ["exit-without-message"]


def testEveryExitSpellingWithAFailureStatusIsReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        from sys import exit as fnLeave
        import os

        def fnStop(iCode):
            if iCode == 1:
                fnLeave(3)
            if iCode == 2:
                raise SystemExit(2)
            if iCode == 3:
                os._exit(4)
        """, capsys) == ["exit-without-message"] * 3


def testAnExitThatSaysWhatFailedIsNotReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        import logging, sys
        logger = logging.getLogger(__name__)

        def fnLoad(sPath):
            try:
                return open(sPath).read()
            except FileNotFoundError:
                sys.exit("no input file at " + sPath)
            except PermissionError:
                print(f"cannot read {sPath}", file=sys.stderr)
                sys.exit(1)
            except OSError:
                logger.error("cannot open %s", sPath)
                raise SystemExit(2)

        def main():
            return 0

        if __name__ == "__main__":
            sys.exit(main())
        sys.exit(0)
        """, capsys) == []


def testAnUntestedSubprocessIsReportedUnderAnyImportSpelling(
        tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        import subprocess
        import subprocess as sp
        from subprocess import call

        def fnRunAll():
            subprocess.run(["make"])
            sp.run(["make"], check=False)
            call(["make"])
            processMake = subprocess.run(["make"])
            print(processMake.stdout)
        """, capsys) == ["unchecked-subprocess"] * 4


def testASubprocessWhoseResultIsTestedIsNotReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        import subprocess, sys

        def fnRunAll(bCheck, dictOptions):
            subprocess.run(["make"], check=True)
            subprocess.run(["make"], check=bCheck)
            subprocess.run(["make"], **dictOptions)
            processMake = subprocess.run(["make"])
            if processMake.returncode != 0:
                sys.exit("make failed")
            if subprocess.call(["make"]) != 0:
                sys.exit("make failed")
            iCode = subprocess.call(["make"])
            sys.exit(iCode)

        def fprocessRun():
            return subprocess.run(["make"])
        """, capsys) == []


def testAnUnguardedOpenInTheEntryPointIsReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        sText = open("data.csv").read()

        def main():
            try:
                fileTable = open("table.csv")
            finally:
                pass

        if __name__ == "__main__":
            main()
        """, capsys) == ["unguarded-open", "unguarded-open"]


def testAGuardedOrLibraryOpenIsNotReported(tmp_path, capsys):
    assert _flistRulesFound(tmp_path, """
        import sys

        def fsReadLibrary(sPath):
            with open(sPath) as fileIn:
                return fileIn.read()

        def main():
            try:
                with open("table.csv") as fileTable:
                    return fileTable.read()
            except OSError as error:
                sys.exit("cannot read table.csv: " + str(error))

        if __name__ == "__main__":
            main()
        """, capsys) == []


def testTheCheckerPassesItsOwnRules(capsys):
    iExit, sOutput = _ftRunChecker([str(PATH_CHECKER)], capsys)
    assert (iExit, sOutput) == (0, "")


# ----------------------------------------------------------------- Shell


def _fsMakeShellcheckDirectory(pathDirectory, sBody):
    pathBin = pathDirectory / "bin"
    pathBin.mkdir()
    if sBody:
        pathFake = pathBin / "shellcheck"
        pathFake.write_text("#!/bin/sh\n" + sBody, encoding="utf-8")
        pathFake.chmod(pathFake.stat().st_mode | stat.S_IXUSR)
    return str(pathBin)


def testAShellScriptWithoutStrictModeIsReported(
        tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", _fsMakeShellcheckDirectory(tmp_path, ""))
    sPath = _fsWriteScript(tmp_path, "run", "#!/bin/bash\nset -e\nmake\n")
    iExit, sOutput = _ftRunChecker([sPath], capsys)
    assert iExit == 1
    assert sPath + ":1: missing-strict-mode:" in sOutput
    assert "missing: nounset, pipefail" in sOutput


@pytest.mark.parametrize("sStrictMode", [
    "set -euo pipefail\n",
    "set -o errexit -o nounset -o pipefail\n",
    "set -eu  # strict\nset -o pipefail\n",
])
def testEverySpellingOfStrictModeIsAccepted(
        tmp_path, capsys, monkeypatch, sStrictMode):
    monkeypatch.setenv("PATH", _fsMakeShellcheckDirectory(tmp_path, ""))
    sPath = _fsWriteScript(
        tmp_path, "run.sh", "#!/usr/bin/env bash\n" + sStrictMode + "make\n")
    iExit, sOutput = _ftRunChecker([sPath], capsys)
    assert iExit == 0
    assert ": missing-strict-mode:" not in sOutput


def testWithoutShellcheckAScriptIsOnlyPartlyChecked(
        tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", _fsMakeShellcheckDirectory(tmp_path, ""))
    sPath = _fsWriteScript(tmp_path, "run.sh", "set -euo pipefail\nmake\n")
    _, sOutput = _ftRunChecker([sPath], capsys)
    assert sPath + ": PARTLY CHECKED: shellcheck is not installed" in sOutput


def testShellcheckFindingsAreReportedInTheCheckersFormat(
        tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", _fsMakeShellcheckDirectory(
        tmp_path, 'echo "$3:2:6: warning: Double quote to prevent '
        'globbing. [SC2086]"\nexit 1\n'))
    sPath = _fsWriteScript(tmp_path, "run.sh",
                           "set -euo pipefail\nrm $sTarget\n")
    iExit, sOutput = _ftRunChecker([sPath], capsys)
    assert iExit == 1
    assert sOutput == (sPath + ":2: shellcheck-SC2086: Double quote to "
                       "prevent globbing.\n")


def testAShellcheckFailureIsACheckerError(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", _fsMakeShellcheckDirectory(
        tmp_path, "echo broken >&2\nexit 3\n"))
    sPath = _fsWriteScript(tmp_path, "run.sh", "set -euo pipefail\n")
    assert _ftRunChecker([sPath], capsys)[0] == 2


# -------------------------------------------------------------- Coverage


@pytest.mark.falsification
def testAnUncheckedLanguageIsReportedNotChecked(tmp_path, capsys):
    """Kills: dropping the NOT CHECKED line for a language with no checker."""
    sPath = _fsWriteScript(tmp_path, "fit.R", "fit <- lm(y ~ x)\n")
    iExit, sOutput = _ftRunChecker([sPath], capsys)
    assert sOutput == (sPath + ": NOT CHECKED: no R checker here; lintr "
                       "would check it\n")
    assert iExit == 0


def testAnUnrecognizedFileNamedExplicitlyIsReportedNotChecked(
        tmp_path, capsys):
    sPath = _fsWriteScript(tmp_path, "notes.xyz", "anything\n")
    _, sOutput = _ftRunChecker([sPath], capsys)
    assert sPath + ": NOT CHECKED: unrecognized file type" in sOutput


def testADirectoryWalkSkipsDataAndHiddenDirectories(tmp_path, capsys):
    _fsWriteScript(tmp_path, "table.csv", "a,b\n")
    (tmp_path / ".git").mkdir()
    _fsWriteScript(tmp_path / ".git", "hook.py", "try:\n    x\nexcept:\n"
                   "    pass\n")
    sScript = _fsWriteScript(tmp_path, "plot.jl", "println(1)\n")
    iExit, sOutput = _ftRunChecker([str(tmp_path)], capsys)
    assert (iExit, sOutput) == (0, sScript + ": NOT CHECKED: no Julia "
                                "checker here; JET.jl would check it\n")


# ------------------------------------------------------------ Exit codes


def _ftRunCheckerProcess(listArguments):
    processChecker = subprocess.run(
        [sys.executable, str(PATH_CHECKER)] + listArguments,
        capture_output=True, text=True, check=False)
    return processChecker.returncode, processChecker.stderr


def testTheExitStatusSeparatesCleanFindingsAndCheckerErrors(tmp_path):
    sClean = _fsWriteScript(tmp_path, "clean.py", "iValue = 1\n")
    sDirty = _fsWriteScript(tmp_path, "dirty.py",
                            "try:\n    x\nexcept:\n    pass\n")
    sBroken = _fsWriteScript(tmp_path, "broken.py", "def (\n")
    assert _ftRunCheckerProcess([sClean])[0] == 0
    assert _ftRunCheckerProcess([sDirty])[0] == 1
    iExit, sError = _ftRunCheckerProcess([sBroken, sDirty])
    assert iExit == 2
    assert "cannot parse " + sBroken in sError
    iExit, sError = _ftRunCheckerProcess(
        [os.path.join(str(tmp_path), "absent.py")])
    assert iExit == 2 and "no such file or directory" in sError
    assert _ftRunCheckerProcess([])[0] == 2
